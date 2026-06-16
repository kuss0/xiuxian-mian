import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import explore_rift, storage_bag
from model.real_message_replay import get_real_message_text


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    return get_real_message_text(FIXTURE_PATH, sample_id)


class ExploreRiftTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_identity(self, identity_id=8659059191, *, realm="元婴初期", xiuwei_current=1000):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(
            identity_id,
            username="walterwa2000",
            realm=realm,
            xiuwei_current=xiuwei_current,
            xiuwei_max=500000,
        )
        return identity_id

    def test_parse_explore_rift_result_summary_counts_reward_tokens(self):
        summary, item_deltas = explore_rift.parse_explore_rift_result_summary(
            "【探寻成功】\n"
            "命盘【贪狼】照命，主偏财夺势。\n"
            "你的元婴满载而归，为你带来了：【法则碎片·木】, 【法则碎片·雷】, 【法则碎片·土】！"
        )

        self.assertEqual(
            "奖励：法则碎片·木x1、法则碎片·雷x1、法则碎片·土x1",
            summary,
        )
        self.assertEqual({"法则碎片·木": 1, "法则碎片·雷": 1, "法则碎片·土": 1}, item_deltas)

    def test_parse_real_beast_victory_summary_counts_reward_lines(self):
        summary, item_deltas = explore_rift.parse_explore_rift_result_summary(
            real_text("explore_rift.beast_victory.space_core")
        )

        self.assertEqual(
            "奖励：法则碎片·空间x1、四级妖丹x5、空间之核x1",
            summary,
        )
        self.assertEqual({"法则碎片·空间": 1, "四级妖丹": 5, "空间之核": 1}, item_deltas)

    def test_real_terminal_result_titles_are_identified_as_explore_rift_replies(self):
        for sample_id in (
            "explore_rift.failure.storm",
            "explore_rift.failure.beast_defeat",
            "explore_rift.beast_victory.space_core",
        ):
            with self.subTest(sample_id=sample_id):
                self.assertTrue(explore_rift.is_explore_rift_reply_text(real_text(sample_id)))

    async def test_scheduler_sends_explore_rift_with_reply_tracking_metadata(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_awaited_once_with(".探寻裂缝", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(22027, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_REPLY_TIMEOUT_SEC, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual("已发送", state_module.state["explore_rift_last_result"])

    async def test_pending_reply_clears_initial_timeout_and_waits_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10425942
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["next_explore_rift_time"] = now + 30
            with patch.object(explore_rift, "save_state"):
                handled = await explore_rift.handle_explore_rift_reply(
                    "你运转全身法力，撕开一道漆黑的空间裂缝，将元婴送入其中探寻机缘...",
                    now,
                    reply_to=SimpleNamespace(id=10425942, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10425944,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(10425944, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual("探寻中", state_module.state["explore_rift_last_result"])
            self.assertGreaterEqual(state_module.state["next_explore_rift_time"], now + explore_rift.EXPLORE_RIFT_CD)

            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now + explore_rift.EXPLORE_RIFT_REPLY_TIMEOUT_SEC + 1)

            send_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            self.assertNotIn("超时", state_module.state["explore_rift_last_error"])

    async def test_scheduler_blocks_auto_high_xiuwei_without_sending(self):
        identity_id = self._prepare_identity(xiuwei_current=500000)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertTrue(state_module.state["explore_rift_enabled"])
            self.assertIn("auto模式", state_module.state["explore_rift_last_error"])
            self.assertGreater(state_module.state["next_explore_rift_time"], now)

    async def test_scheduler_blocks_missing_current_xiuwei_without_sending(self):
        identity_id = self._prepare_identity(xiuwei_current=0)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertTrue(state_module.state["explore_rift_enabled"])
            self.assertIn("修为未知", state_module.state["explore_rift_last_error"])
            self.assertGreater(state_module.state["next_explore_rift_time"], now)

    async def test_scheduler_blocks_below_yuanying_and_disables_module(self):
        identity_id = self._prepare_identity(realm="结丹后期", xiuwei_current=1000)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertFalse(state_module.state["explore_rift_enabled"])
            self.assertIn("境界不符", state_module.state["explore_rift_last_error"])

    async def test_result_reply_updates_storage_bag_and_schedules_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({})
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 22027
            state_module.state["explore_rift_reply_due_at"] = now + 30
            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    "【探寻成功】\n"
                    "你的元婴满载而归，为你带来了：【法则碎片·火】, 【法则碎片·金】, 【法则碎片·水】！",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertEqual(
                "奖励：法则碎片·火x1、法则碎片·金x1、法则碎片·水x1",
                state_module.state["explore_rift_last_result"],
            )
            records = state_module.get_storage_bag_records()
            self.assertEqual(1, records[str(identity_id)]["items"]["法则碎片·火"])
            self.assertEqual(1, records[str(identity_id)]["items"]["法则碎片·金"])
            self.assertEqual(1, records[str(identity_id)]["items"]["法则碎片·水"])

    async def test_real_storm_failure_clears_pending_and_schedules_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10425942
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["explore_rift_pending_result_msg_id"] = 10425944

            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    real_text("explore_rift.failure.storm"),
                    now,
                    reply_to=SimpleNamespace(id=10425942, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10425944,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertIn("遭遇风暴", state_module.state["explore_rift_last_result"])

    async def test_real_beast_defeat_clears_pending_and_schedules_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10426277
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["explore_rift_pending_result_msg_id"] = 10426278

            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    real_text("explore_rift.failure.beast_defeat"),
                    now,
                    reply_to=SimpleNamespace(id=10426277, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10426278,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertIn("不敌败退", state_module.state["explore_rift_last_result"])

    async def test_real_beast_victory_updates_storage_bag_and_schedules_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({})
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10410001
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["explore_rift_pending_result_msg_id"] = 10410003

            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    real_text("explore_rift.beast_victory.space_core"),
                    now,
                    reply_to=SimpleNamespace(id=10410001, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10410003,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertEqual(
                "奖励：法则碎片·空间x1、四级妖丹x5、空间之核x1",
                state_module.state["explore_rift_last_result"],
            )
            records = state_module.get_storage_bag_records()
            self.assertEqual(1, records[str(identity_id)]["items"]["法则碎片·空间"])
            self.assertEqual(5, records[str(identity_id)]["items"]["四级妖丹"])
            self.assertEqual(1, records[str(identity_id)]["items"]["空间之核"])

    async def test_cd_reply_uses_real_wait_text(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            with (
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    "空间裂缝尚未稳定，其中的空间风暴仍在肆虐。请在 1小时20分钟31秒 后再行探寻。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(now + 4831 + explore_rift.CD_BUFFER_SEC, state_module.state["next_explore_rift_time"])
            self.assertEqual("冷却中", state_module.state["explore_rift_last_result"])

    async def test_inventory_fenglei_wings_does_not_shorten_cd_without_equipped_signal(self):
        identity_id = self._prepare_identity(realm="化神初期")
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({str(identity_id): {"items": {"风雷翅": 1}}})
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    "【探寻成功】\n你的元婴满载而归，为你带来了：【法则碎片·金】！",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])


if __name__ == "__main__":
    unittest.main()
