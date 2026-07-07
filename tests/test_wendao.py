import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import storage_bag, wendao


class WendaoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_identity(self, identity_id=8659059191, *, sect_name="元婴宗", xiuwei_current=5000):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(
            identity_id,
            username="walterwa2000",
            sect_name=sect_name,
            xiuwei_current=xiuwei_current,
        )
        return identity_id

    def test_parse_wendao_result_summary_extracts_xiuwei_and_items(self):
        summary, item_deltas = wendao.parse_wendao_result_summary(
            "【问道得宝】\n"
            "你向宗门长老问道，心有所悟，修为增加了 1,234 点。\n"
            "【太乙银精】 x 2\n"
            "法则碎片·风 x 1"
        )

        self.assertIn("修为 +1234", summary)
        self.assertIn("太乙银精x2", summary)
        self.assertEqual({"太乙银精": 2, "法则碎片·风": 1}, item_deltas)

    async def test_scheduler_sends_wendao_with_reply_tracking_metadata(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            state_module.state["next_wendao_time"] = now - 1
            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(wendao, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(wendao, "save_state"),
            ):
                await wendao.run_wendao_scheduler(now)

            send_mock.assert_awaited_once_with(".问道", track=False, max_retry=0, source_module="问道")
            self.assertEqual(22027, state_module.state["wendao_reply_to_msg_id"])
            self.assertEqual(now + wendao.WENDAO_REPLY_TIMEOUT_SEC, state_module.state["wendao_reply_due_at"])
            self.assertEqual("已发送", state_module.state["wendao_last_result"])

    async def test_scheduler_send_timeout_uses_unknown_backoff(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            state_module.state["next_wendao_time"] = now - 1
            with (
                patch.object(wendao, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                patch.object(wendao, "classify_game_send_block", return_value={"status": "unknown", "code": "send_timeout"}),
                patch.object(wendao, "send_audit_log", new=AsyncMock()),
                patch.object(wendao, "save_state"),
            ):
                await wendao.run_wendao_scheduler(now)

            send_mock.assert_awaited_once_with(".问道", track=False, max_retry=0, source_module="问道")
            self.assertEqual(now + wendao.WENDAO_SEND_UNKNOWN_BACKOFF_SEC, state_module.state["next_wendao_time"])
            self.assertIn("状态未知", state_module.state["wendao_last_error"])
            self.assertEqual(0, state_module.state["wendao_reply_to_msg_id"])

    async def test_scheduler_blocks_unparseable_next_time_without_retry_spam(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            state_module.state["next_wendao_time"] = "冷却数据异常"
            with (
                patch.object(wendao, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wendao, "save_state") as save_mock,
            ):
                await wendao.run_wendao_scheduler(now)

            send_mock.assert_not_awaited()
            save_mock.assert_not_called()
            self.assertEqual("冷却数据异常", state_module.state["next_wendao_time"])
            self.assertEqual(0, state_module.state.get("wendao_reply_to_msg_id", 0))
            self.assertEqual("", state_module.state.get("wendao_last_error", ""))

    async def test_scheduler_blocks_unknown_sect_without_sending(self):
        identity_id = self._prepare_identity(sect_name="")
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            state_module.state["next_wendao_time"] = now - 1
            with (
                patch.object(wendao, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wendao, "save_state"),
            ):
                await wendao.run_wendao_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertTrue(state_module.state["wendao_enabled"])
            self.assertIn("宗门未知", state_module.state["wendao_last_error"])
            self.assertGreater(state_module.state["next_wendao_time"], now)

    async def test_scheduler_blocks_wrong_sect_and_disables_module(self):
        identity_id = self._prepare_identity(sect_name="星宫")
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            state_module.state["next_wendao_time"] = now - 1
            with (
                patch.object(wendao, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wendao, "send_audit_log", new=AsyncMock()),
                patch.object(wendao, "save_state"),
            ):
                await wendao.run_wendao_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertFalse(state_module.state["wendao_enabled"])
            self.assertIn("宗门不符", state_module.state["wendao_last_error"])

    async def test_result_reply_updates_storage_bag_and_schedules_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({})
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            state_module.state["wendao_reply_to_msg_id"] = 22027
            state_module.state["wendao_reply_due_at"] = now + 30
            with (
                patch.object(wendao.random, "uniform", return_value=0),
                patch.object(wendao, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(wendao, "send_audit_log", new=AsyncMock()),
            ):
                handled = await wendao.handle_wendao_reply(
                    "【问道得宝】\n"
                    "修为增加了 1000 点\n"
                    "【太乙银精】 x 2",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".问道"),
                    matched_family="wendao",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["wendao_reply_to_msg_id"])
            self.assertEqual(now + wendao.WENDAO_CD, state_module.state["next_wendao_time"])
            self.assertEqual("修为 +1000 ｜ 奖励：太乙银精x2", state_module.state["wendao_last_result"])
            records = state_module.get_storage_bag_records()
            self.assertEqual(2, records[str(identity_id)]["items"]["太乙银精"])

    async def test_cd_reply_uses_real_wait_text(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            with (
                patch.object(wendao, "save_state"),
                patch.object(wendao, "send_audit_log", new=AsyncMock()),
            ):
                handled = await wendao.handle_wendao_reply(
                    "天机不可频繁窥探，请在 1小时2分钟3秒 后再来。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".问道"),
                    matched_family="wendao",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(now + 3723 + wendao.CD_BUFFER_SEC, state_module.state["next_wendao_time"])
            self.assertEqual("冷却中", state_module.state["wendao_last_result"])

    async def test_recovered_pending_reply_extends_wait_from_recovery_time(self):
        identity_id = self._prepare_identity()
        original_ts = 1_700_000_000.0
        recovery_ts = original_ts + wendao.WENDAO_REPLY_TIMEOUT_SEC + 5
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            state_module.state["wendao_reply_to_msg_id"] = 22027
            state_module.state["wendao_reply_due_at"] = original_ts + wendao.WENDAO_REPLY_TIMEOUT_SEC
            with (
                patch.object(wendao.time, "time", return_value=recovery_ts),
                patch.object(wendao, "save_state"),
            ):
                handled = await wendao.handle_wendao_reply(
                    "你消耗了 1000 点修为，虔诚地向宗门长老问道，希望能获得一丝天机...",
                    original_ts,
                    reply_to=SimpleNamespace(id=22027, raw_text=".问道"),
                    matched_family="wendao",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(22027, state_module.state["wendao_reply_to_msg_id"])
            self.assertEqual(22028, state_module.state["wendao_pending_result_msg_id"])
            self.assertEqual(recovery_ts + wendao.WENDAO_REPLY_TIMEOUT_SEC, state_module.state["wendao_reply_due_at"])
            self.assertEqual(state_module.state["wendao_reply_due_at"], state_module.state["next_wendao_time"])
            self.assertEqual("问道中", state_module.state["wendao_last_result"])

    async def test_recovery_does_not_replay_same_pending_ack_after_wait_expires(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            state_module.state["wendao_reply_to_msg_id"] = 22027
            state_module.state["wendao_pending_result_msg_id"] = 22028
            state_module.state["wendao_reply_due_at"] = now - 1
            only_ack_entry = {
                "message_id": 22028,
                "ts_epoch": now - wendao.WENDAO_REPLY_TIMEOUT_SEC,
                "text": "你消耗了 1000 点修为，虔诚地向宗门长老问道，希望能获得一丝天机...",
            }
            with (
                patch.object(wendao, "find_message_log_message", return_value=only_ack_entry),
                patch.object(wendao, "find_message_log_replies", return_value=[only_ack_entry]),
                patch.object(wendao, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(wendao, "save_state"),
            ):
                await wendao.run_wendao_scheduler(now)

            self.assertEqual(0, state_module.state["wendao_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["wendao_pending_result_msg_id"])
            self.assertEqual("问道回复超时", state_module.state["wendao_last_error"])
            self.assertEqual(now + wendao.RETRY_MAX_SEC, state_module.state["next_wendao_time"])
            audit_mock.assert_awaited_once()
            self.assertIn("问道回复超时", audit_mock.await_args.args[0])

    async def test_inventory_fenglei_wings_does_not_shorten_cd_without_equipped_signal(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({str(identity_id): {"items": {"风雷翅": 1}}})
        with state_module.use_identity(identity_id):
            state_module.state["wendao_enabled"] = True
            with (
                patch.object(wendao.random, "uniform", return_value=0),
                patch.object(wendao, "save_state"),
                patch.object(wendao, "send_audit_log", new=AsyncMock()),
            ):
                handled = await wendao.handle_wendao_reply(
                    "【问道得宝】\n修为增加了 1 点",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".问道"),
                    matched_family="wendao",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(now + wendao.WENDAO_CD, state_module.state["next_wendao_time"])


if __name__ == "__main__":
    unittest.main()
