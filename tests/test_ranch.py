import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import ranch


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class RanchRetirementTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def _register_identity(self, identity_id=3711993781, username="xuruode3"):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username=username, enabled=True)
        return identity_id

    def test_initial_schedule_fails_closed_and_preserves_inflight_return(self):
        identity_id = self._register_identity()
        with state_module.use_identity(identity_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["next_ranch_time"] = 1234
            state_module.state["ranch_reply_to_msg_id"] = 55
            state_module.state["ranch_reply_due_at"] = 1300
            state_module.state["ranch_retry_count"] = 1
            state_module.state["ranch_return_pending"] = True
            state_module.state["ranch_return_wait_since"] = 900

            with patch.object(ranch, "mark_dirty") as mark_dirty_mock:
                next_time = ranch.schedule_ranch_initial_check(1000)

            self.assertEqual(0.0, next_time)
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertEqual(0, state_module.state["next_ranch_time"])
            self.assertEqual(0, state_module.state["ranch_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["ranch_reply_due_at"])
            self.assertEqual(0, state_module.state["ranch_retry_count"])
            self.assertTrue(state_module.state["ranch_return_pending"])
            self.assertEqual(900, state_module.state["ranch_return_wait_since"])
            self.assertEqual(ranch.RANCH_ARCHIVE_REASON, state_module.state["ranch_last_error"])
            mark_dirty_mock.assert_called_once()

    async def test_scheduler_is_a_fail_closed_tombstone(self):
        identity_id = self._register_identity()
        with state_module.use_identity(identity_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["next_ranch_time"] = 100
            state_module.state["ranch_reply_to_msg_id"] = 77
            state_module.state["ranch_return_pending"] = True

            with patch.object(ranch, "save_state") as save_mock:
                result = await ranch.run_ranch_scheduler(200)

            self.assertFalse(result)
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertEqual(0, state_module.state["next_ranch_time"])
            self.assertEqual(0, state_module.state["ranch_reply_to_msg_id"])
            self.assertTrue(state_module.state["ranch_return_pending"])
            save_mock.assert_called_once()

        self.assertFalse(hasattr(ranch, "send_game_command"))

    async def test_late_success_reply_is_parsed_while_module_is_archived(self):
        identity_id = self._register_identity(username="WalterWA2000")
        now = 1000.0
        with state_module.use_identity(identity_id):
            state_module.state["ranch_enabled"] = False
            with (
                patch.object(ranch, "mark_dirty"),
                patch.object(ranch, "save_state") as save_mock,
                patch.object(ranch, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                handled = await ranch.handle_ranch_reply(
                    "【万兽奔腾】\n你打开万兽谷传送阵，灵兽四散放养。",
                    now,
                    SimpleNamespace(id=123, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertTrue(state_module.state["ranch_return_pending"])
            self.assertEqual(now, state_module.state["ranch_return_wait_since"])
            self.assertEqual("历史放养成功，等待灵兽归来", state_module.state["ranch_last_result"])
            self.assertEqual(123, state_module.state["ranch_last_msg_id"])
            self.assertEqual(0, state_module.state["next_ranch_time"])
            save_mock.assert_called_once()
            audit_mock.assert_awaited_once()

    async def test_return_broadcast_matches_pending_identity_even_when_disabled(self):
        identity_id = self._register_identity(username="WalterWA2000")
        with state_module.use_identity(identity_id):
            state_module.state["ranch_enabled"] = False
            state_module.state["ranch_return_pending"] = True
            state_module.state["ranch_return_wait_since"] = 900
            state_module.state["next_ranch_time"] = 0

        with (
            patch.object(ranch, "save_state") as save_mock,
            patch.object(ranch, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            handled = await ranch.handle_ranch_return_broadcast(
                "【灵兽归来】\n道友 @WalterWA2000 你放养的灵兽已自行归来。",
                1000,
                SimpleNamespace(id=456),
            )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertFalse(state_module.state["ranch_return_pending"])
            self.assertEqual(456, state_module.state["ranch_return_seen_msg_id"])
            self.assertEqual("灵兽归来已确认", state_module.state["ranch_last_result"])
        save_mock.assert_called_once()
        audit_mock.assert_awaited_once()

    async def test_no_idle_reply_closes_active_fields_without_rearming(self):
        identity_id = self._register_identity()
        with state_module.use_identity(identity_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["next_ranch_time"] = 1500
            with (
                patch.object(ranch, "mark_dirty"),
                patch.object(ranch, "save_state"),
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
            ):
                handled = await ranch.handle_ranch_reply(
                    ranch.RANCH_NO_IDLE_PET_TEXT,
                    1000,
                    SimpleNamespace(id=222, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertEqual(0, state_module.state["next_ranch_time"])
            self.assertFalse(state_module.state["ranch_return_pending"])
            self.assertEqual("无休息中灵兽", state_module.state["ranch_last_result"])

    async def test_wrong_sect_variant_is_still_parsed_passively(self):
        identity_id = self._register_identity(username="growrdick")
        with state_module.use_identity(identity_id):
            state_module.state["ranch_enabled"] = False
            with (
                patch.object(ranch, "mark_dirty"),
                patch.object(ranch, "save_state"),
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
            ):
                handled = await ranch.handle_ranch_reply(
                    "你并非万灵宗弟子，无法通晓御兽之术。",
                    1000,
                    SimpleNamespace(id=333, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertEqual("非万灵宗弟子", state_module.state["ranch_last_result"])
            self.assertIn("并非万灵宗弟子", state_module.state["ranch_last_error"])

    def test_status_is_an_explicit_archive_tombstone(self):
        identity_id = self._register_identity()
        with state_module.use_identity(identity_id):
            text = ranch.get_ranch_status_text()

        self.assertIn("旧版群命令自动化：已归档", text)
        self.assertIn("MiniApp（Gate C 未开放）", text)
        self.assertNotIn("下次执行", text)


if __name__ == "__main__":
    unittest.main()
