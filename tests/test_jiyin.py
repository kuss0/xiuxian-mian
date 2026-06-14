import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import delayed_actions
from model.features import jiyin


class JiyinTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        delayed_actions.reset_delayed_actions_for_tests()

    def tearDown(self):
        delayed_actions.reset_delayed_actions_for_tests()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_identity(self, identity_id=8659059191):
        state_module.ensure_identity_registered(identity_id)
        return identity_id

    async def test_scheduler_blocks_dirty_next_time_without_clearing_or_saving(self):
        now = 1_700_000_000.0

        for dirty_next in ("极阴时间异常", "nan", "inf", "-inf"):
            with self.subTest(dirty_next=dirty_next):
                identity_id = self._prepare_identity()
                with state_module.use_identity(identity_id):
                    state_module.state["jiyin_enabled"] = True
                    state_module.state["jiyin_reply_to_msg_id"] = 22027
                    state_module.state["next_jiyin_time"] = dirty_next
                    state_module.state["jiyin_last_error"] = ""

                    with (
                        patch.object(jiyin, "send_audit_log", new=AsyncMock()) as audit_mock,
                        patch.object(jiyin, "save_state") as save_mock,
                    ):
                        await jiyin.run_jiyin_scheduler(now)

                    audit_mock.assert_not_awaited()
                    save_mock.assert_not_called()
                    self.assertEqual(22027, state_module.state["jiyin_reply_to_msg_id"])
                    self.assertEqual(dirty_next, state_module.state["next_jiyin_time"])
                    self.assertEqual("", state_module.state["jiyin_last_error"])

    async def test_apply_choice_blocks_dirty_next_time_without_sending_to_old_reply(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0

        with state_module.use_identity(identity_id):
            state_module.state["jiyin_enabled"] = True
            state_module.state["jiyin_reply_to_msg_id"] = 22027
            state_module.state["next_jiyin_time"] = "nan"

            with (
                patch.object(jiyin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(jiyin, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(jiyin, "save_state") as save_mock,
            ):
                ok, message = await jiyin.apply_jiyin_choice(jiyin.JIYIN_CHOICE_HIDE_AURA, now=now)

            self.assertTrue(ok)
            self.assertIn("已保存极阴祖师选择", message)
            send_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            save_mock.assert_called_once()
            self.assertEqual(22027, state_module.state["jiyin_reply_to_msg_id"])
            self.assertEqual("nan", state_module.state["next_jiyin_time"])

    def test_status_text_tolerates_dirty_pending_state(self):
        identity_id = self._prepare_identity()

        with state_module.use_identity(identity_id):
            state_module.state["jiyin_reply_to_msg_id"] = "消息异常"
            state_module.state["next_jiyin_time"] = "inf"

            text = jiyin.get_jiyin_status_text()

        self.assertIn("🌑 极阴祖师", text)
        self.assertIn("- 待回复消息ID：无", text)
        self.assertIn("- 截止时间：未设置", text)

    async def test_prompt_schedules_delayed_reply_and_finalizes_after_queue_send(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        prompt = (
            "@jiyintester 做出抉择。\n"
            "你必须在 3 分钟内回复。\n"
            "回复本消息 .献上魂魄\n"
            "回复本消息 .收敛气息"
        )
        state_module.update_send_as_profile(
            identity_id,
            username="jiyintester",
            enabled=True,
            jiyin_choice=jiyin.JIYIN_CHOICE_HIDE_AURA,
        )

        with state_module.use_identity(identity_id):
            state_module.state["jiyin_enabled"] = True

            with (
                patch.object(jiyin.random, "randint", return_value=20),
                patch.object(jiyin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(jiyin, "save_state") as save_mock,
            ):
                handled = await jiyin.handle_jiyin_prompt(prompt, now, SimpleNamespace(id=33001))

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            save_mock.assert_called_once()
            self.assertEqual(33001, state_module.state["jiyin_reply_to_msg_id"])
            self.assertEqual(now + 180, state_module.state["next_jiyin_time"])
            queued = delayed_actions.list_delayed_actions()
            self.assertEqual(1, len(queued))
            self.assertEqual(".收敛气息", queued[0]["command"])
            self.assertEqual(now + 20, queued[0]["due_at"])
            self.assertEqual(identity_id, queued[0]["send_as_id"])
            self.assertFalse(queued[0]["track"])
            self.assertEqual(33001, queued[0]["reply_to_msg_id"])
            self.assertEqual("jiyin", queued[0]["source_module"])
            self.assertEqual("jiyin_prompt_reply", queued[0]["op_id"])

            calls = []

            async def fake_send(command, **kwargs):
                calls.append((command, kwargs))
                return SimpleNamespace(id=44001)

            results = await delayed_actions.drain_due_actions(now + 20, fake_send)

            self.assertEqual(
                [
                    (
                        ".收敛气息",
                        {
                            "send_as_id": identity_id,
                            "track": False,
                            "reply_to": 33001,
                            "priority": "reactive",
                            "source_module": "jiyin",
                            "op_id": "jiyin_prompt_reply",
                            "chain_id": f"jiyin:{identity_id}:33001",
                        },
                    )
                ],
                calls,
            )
            self.assertEqual("sent", results[0]["status"])

            with (
                patch.object(jiyin, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(jiyin, "save_state") as save_mock,
            ):
                handled = await jiyin.handle_jiyin_delayed_action_result(results[0])

            self.assertTrue(handled)
            audit_mock.assert_awaited_once()
            save_mock.assert_called_once()
            self.assertEqual(0, state_module.state["jiyin_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["next_jiyin_time"])
            self.assertEqual("", state_module.state["jiyin_last_error"])
            self.assertEqual([], delayed_actions.list_delayed_actions())

    async def test_prompt_delayed_reply_failure_keeps_pending_for_manual_recovery(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        prompt = (
            "@jiyinfailed 做出抉择。\n"
            "回复本消息 .献上魂魄\n"
            "回复本消息 .收敛气息"
        )
        state_module.update_send_as_profile(
            identity_id,
            username="jiyinfailed",
            enabled=True,
            jiyin_choice=jiyin.JIYIN_CHOICE_HIDE_AURA,
        )

        with state_module.use_identity(identity_id):
            state_module.state["jiyin_enabled"] = True
            with patch.object(jiyin.random, "randint", return_value=20):
                handled = await jiyin.handle_jiyin_prompt(prompt, now, SimpleNamespace(id=33002))
            self.assertTrue(handled)

            async def fake_send(command, **kwargs):
                return None

            results = await delayed_actions.drain_due_actions(now + 20, fake_send)
            self.assertEqual("failed", results[0]["status"])

            with (
                patch.object(jiyin, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(jiyin, "save_state") as save_mock,
            ):
                handled = await jiyin.handle_jiyin_delayed_action_result(results[0])

            self.assertTrue(handled)
            audit_mock.assert_awaited_once()
            save_mock.assert_called_once()
            self.assertEqual(33002, state_module.state["jiyin_reply_to_msg_id"])
            self.assertGreater(state_module.state["next_jiyin_time"], now)
            self.assertEqual("极阴祖师延迟回复发送失败", state_module.state["jiyin_last_error"])


if __name__ == "__main__":
    unittest.main()
