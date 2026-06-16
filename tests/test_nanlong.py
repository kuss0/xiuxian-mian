import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import nanlong
from model.real_message_replay import get_real_message_text


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    return get_real_message_text(FIXTURE_PATH, sample_id)


class NanlongTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_pending(self, identity_id=991201, *, now=1_700_000_000.0):
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["nanlong_enabled"] = True
            state_module.state["nanlong_reply_to_msg_id"] = 22027
            state_module.state["next_nanlong_time"] = now + 60
            state_module.state["nanlong_reply_due_at"] = now - 1
        return identity_id

    async def test_scheduler_blocks_dirty_pending_fields_without_sending_or_saving(self):
        now = 1_700_000_000.0
        dirty_cases = (
            ("next_nanlong_time", "冷却数据异常"),
            ("nanlong_reply_due_at", "nan"),
            ("nanlong_reply_to_msg_id", "消息ID异常"),
        )

        for index, (field_name, dirty_value) in enumerate(dirty_cases, start=1):
            with self.subTest(field_name=field_name):
                identity_id = self._prepare_pending(991200 + index, now=now)
                with state_module.use_identity(identity_id):
                    state_module.state[field_name] = dirty_value
                    before = {
                        "nanlong_reply_to_msg_id": state_module.state["nanlong_reply_to_msg_id"],
                        "next_nanlong_time": state_module.state["next_nanlong_time"],
                        "nanlong_reply_due_at": state_module.state["nanlong_reply_due_at"],
                        "nanlong_last_error": state_module.state["nanlong_last_error"],
                    }

                    with (
                        patch.object(nanlong, "send_game_command", new=AsyncMock()) as send_mock,
                        patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit_mock,
                        patch.object(nanlong, "save_state") as save_mock,
                    ):
                        await nanlong.run_nanlong_scheduler(now)

                    send_mock.assert_not_awaited()
                    audit_mock.assert_not_awaited()
                    save_mock.assert_not_called()
                    after = {
                        "nanlong_reply_to_msg_id": state_module.state["nanlong_reply_to_msg_id"],
                        "next_nanlong_time": state_module.state["next_nanlong_time"],
                        "nanlong_reply_due_at": state_module.state["nanlong_reply_due_at"],
                        "nanlong_last_error": state_module.state["nanlong_last_error"],
                    }
                    self.assertEqual(before, after)

    async def test_apply_choice_saves_choice_but_does_not_reply_to_dirty_pending(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_pending(now=now)

        with state_module.use_identity(identity_id):
            state_module.state["next_nanlong_time"] = "冷却数据异常"
            state_module.state["nanlong_reply_due_at"] = "nan"

            with (
                patch.object(nanlong, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(nanlong, "save_state") as save_mock,
            ):
                ok, message = await nanlong.apply_nanlong_choice(nanlong.NANLONG_CHOICE_EXCHANGE_FABAO, now=now)

            self.assertTrue(ok)
            self.assertIn("已保存南陇侯选择", message)
            self.assertIn("待回复状态异常", message)
            send_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            save_mock.assert_called_once()
            self.assertEqual(
                nanlong.NANLONG_CHOICE_EXCHANGE_FABAO,
                state_module.get_nanlong_choice(identity_id),
            )
            self.assertEqual(22027, state_module.state["nanlong_reply_to_msg_id"])
            self.assertEqual("冷却数据异常", state_module.state["next_nanlong_time"])
            self.assertEqual("nan", state_module.state["nanlong_reply_due_at"])

    async def test_scheduler_clears_expired_deadline_without_sending(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_pending(now=now)
        with state_module.use_identity(identity_id):
            state_module.state["next_nanlong_time"] = now - 1
            state_module.state["nanlong_reply_due_at"] = now - 10

            with (
                patch.object(nanlong, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(nanlong, "save_state") as save_mock,
            ):
                await nanlong.run_nanlong_scheduler(now)

            send_mock.assert_not_awaited()
            audit_mock.assert_awaited_once()
            save_mock.assert_called_once()
            self.assertEqual(0, state_module.state["nanlong_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["next_nanlong_time"])
            self.assertEqual(0, state_module.state["nanlong_reply_due_at"])
            self.assertEqual("南陇侯提示已超时", state_module.state["nanlong_last_error"])

    async def test_prompt_delay_retry_and_broadcast_confirmation_flow(self):
        now = 1_700_000_000.0
        identity_id = 991299
        prompt_text = (
            "南陇侯望向 @nanlongtester，示意你做出抉择。\n"
            "你有 3 分钟。\n"
            "回复本消息 .交换法宝\n"
            "回复本消息 .交换功法\n"
            "回复本消息 .拒绝交易"
        )
        success_text = "【天机异闻·南陇侯的交易】@nanlongtester 已完成交易。"

        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(
            identity_id,
            username="nanlongtester",
            enabled=True,
            nanlong_choice=nanlong.NANLONG_CHOICE_EXCHANGE_FABAO,
        )

        with state_module.use_identity(identity_id):
            state_module.state["nanlong_enabled"] = True

            with (
                patch.object(nanlong.random, "randint", return_value=20),
                patch.object(nanlong, "save_state") as save_mock,
                patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                handled = await nanlong.handle_nanlong_prompt(prompt_text, now, SimpleNamespace(id=8801))

            self.assertTrue(handled)
            self.assertEqual(8801, state_module.state["nanlong_reply_to_msg_id"])
            self.assertEqual(now + 180, state_module.state["next_nanlong_time"])
            self.assertEqual(now + 20, state_module.state["nanlong_reply_due_at"])
            save_mock.assert_called_once()
            audit_mock.assert_not_awaited()

            with (
                patch.object(nanlong, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(nanlong, "save_state") as save_mock,
            ):
                await nanlong.run_nanlong_scheduler(now + 19)

            send_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            save_mock.assert_not_called()

            async def fake_send_first(command, **kwargs):
                return SimpleNamespace(id=9901, sent_at=now + 20)

            with (
                patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=fake_send_first)) as send_mock,
                patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(nanlong, "save_state") as save_mock,
            ):
                await nanlong.run_nanlong_scheduler(now + 20)

            send_mock.assert_awaited_once_with(".交换 法宝", track=False, reply_to=8801)
            audit_mock.assert_not_awaited()
            save_mock.assert_called_once()
            self.assertEqual(9901, state_module.state["nanlong_last_msg_id"])
            self.assertEqual(".交换 法宝", state_module.state["nanlong_last_command"])
            self.assertEqual(0, state_module.state["nanlong_retry_count"])
            self.assertEqual(now + 80, state_module.state["nanlong_reply_due_at"])
            self.assertEqual("等待南陇侯交易结果", state_module.state["nanlong_last_error"])

            async def fake_send_retry(command, **kwargs):
                return SimpleNamespace(id=9902, sent_at=now + 80)

            with (
                patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=fake_send_retry)) as send_mock,
                patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(nanlong, "save_state") as save_mock,
            ):
                await nanlong.run_nanlong_scheduler(now + 80)

            send_mock.assert_awaited_once_with(".交换 法宝", track=False, reply_to=8801)
            audit_mock.assert_awaited_once()
            save_mock.assert_called_once()
            self.assertEqual(9902, state_module.state["nanlong_last_msg_id"])
            self.assertEqual(1, state_module.state["nanlong_retry_count"])
            self.assertEqual(now + 140, state_module.state["nanlong_reply_due_at"])

            with (
                patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(nanlong, "save_state") as save_mock,
            ):
                handled = await nanlong.handle_nanlong_result_broadcast(
                    success_text,
                    now + 90,
                    SimpleNamespace(id=8810),
                )

            self.assertTrue(handled)
            audit_mock.assert_awaited_once_with("🤝 南陇侯交易结果已确认")
            save_mock.assert_called_once()
            self.assertEqual(0, state_module.state["nanlong_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["next_nanlong_time"])
            self.assertEqual(0, state_module.state["nanlong_reply_due_at"])
            self.assertEqual(0, state_module.state["nanlong_last_msg_id"])
            self.assertEqual("", state_module.state["nanlong_last_error"])

    async def test_real_nanlong_trade_result_confirms_pending_exchange(self):
        now = 1_781_202_313.0
        identity_id = 991300
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="nan", enabled=True)

        with state_module.use_identity(identity_id):
            state_module.state["nanlong_enabled"] = True
            state_module.state["nanlong_last_msg_id"] = 10226520
            state_module.state["nanlong_reply_to_msg_id"] = 10226510
            state_module.state["next_nanlong_time"] = now + 60
            state_module.state["nanlong_reply_due_at"] = now + 30
            state_module.state["nanlong_last_error"] = "等待南陇侯交易结果"

            with (
                patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(nanlong, "save_state") as save_mock,
            ):
                handled = await nanlong.handle_nanlong_result_broadcast(
                    real_text("nanlong.result.trade"),
                    now,
                    SimpleNamespace(id=10226525),
                )

            self.assertTrue(handled)
            audit_mock.assert_awaited_once_with("🤝 南陇侯交易结果已确认")
            save_mock.assert_called_once()
            self.assertEqual(0, state_module.state["nanlong_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["next_nanlong_time"])
            self.assertEqual(0, state_module.state["nanlong_last_msg_id"])
            self.assertEqual("", state_module.state["nanlong_last_error"])


if __name__ == "__main__":
    unittest.main()
