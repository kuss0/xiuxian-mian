import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import jiyin


class JiyinTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
