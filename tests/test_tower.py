import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import tower
from model.timing import get_day_key


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class TowerSchedulerTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def _prepare_identity(self, send_as_id, username="TowerUser"):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username=username)

    async def test_first_send_waits_for_reply_without_marking_done(self):
        send_as_id = 8659059301
        now = 1_700_000_000.0
        self._prepare_identity(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_day"] = ""
            state_module.state["next_tower_time"] = now - 1

            sent_msg = SimpleNamespace(id=5001, sent_at=now)
            with (
                patch.object(tower, "_is_tower_window_time", return_value=True),
                patch.object(tower, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(tower, "save_state"),
                patch.object(tower, "send_audit_log", new=AsyncMock()),
            ):
                await tower.run_tower_scheduler(now)

            send_mock.assert_awaited_once_with(
                tower.CMD_TOWER,
                track=False,
                max_retry=0,
                priority=None,
                source_module="闯塔",
            )
            self.assertEqual("", state_module.state["last_tower_day"])
            self.assertEqual(5001, state_module.state["last_tower_msg_id"])
            self.assertEqual(now + tower.TOWER_REPLY_TIMEOUT_SEC, state_module.state["tower_reply_due_at"])
            self.assertEqual(0, state_module.state["tower_retry_count"])
            self.assertEqual(state_module.state["tower_reply_due_at"], state_module.state["next_tower_time"])

    async def test_timeout_schedules_one_short_retry_then_next_day(self):
        send_as_id = 8659059302
        now = 1_700_000_100.0
        self._prepare_identity(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_msg_id"] = 5001
            state_module.state["tower_reply_due_at"] = now - 1
            state_module.state["tower_retry_count"] = 0
            state_module.state["next_tower_time"] = now - 1

            with (
                patch.object(tower.random, "uniform", return_value=3),
                patch.object(tower, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(tower, "mark_dirty"),
                patch.object(tower, "save_state"),
                patch.object(tower, "send_audit_log", new=AsyncMock()),
            ):
                await tower.run_tower_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(0, state_module.state["last_tower_msg_id"])
            self.assertEqual(0, state_module.state["tower_reply_due_at"])
            self.assertEqual(1, state_module.state["tower_retry_count"])
            self.assertEqual(now + 3, state_module.state["next_tower_time"])

            retry_at = now + 3
            retry_msg = SimpleNamespace(id=5002, sent_at=retry_at)
            with (
                patch.object(tower, "send_game_command", new=AsyncMock(return_value=retry_msg)) as send_mock,
                patch.object(tower, "save_state"),
                patch.object(tower, "send_audit_log", new=AsyncMock()),
            ):
                await tower.run_tower_scheduler(retry_at)

            send_mock.assert_awaited_once_with(
                tower.CMD_TOWER,
                track=False,
                max_retry=0,
                priority="retry",
                source_module="闯塔",
            )
            self.assertEqual(5002, state_module.state["last_tower_msg_id"])
            self.assertEqual(1, state_module.state["tower_retry_count"])
            self.assertEqual(retry_at + tower.TOWER_REPLY_TIMEOUT_SEC, state_module.state["tower_reply_due_at"])

            def fake_next_day(ts):
                next_ts = ts + 24 * 60 * 60
                state_module.state["next_tower_time"] = next_ts
                return next_ts

            with (
                patch.object(tower, "_schedule_tower_next_day", side_effect=fake_next_day),
                patch.object(tower, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(tower, "mark_dirty"),
                patch.object(tower, "save_state"),
            ):
                await tower.run_tower_scheduler(retry_at + tower.TOWER_REPLY_TIMEOUT_SEC + 1)

            send_mock.assert_not_awaited()
            self.assertEqual(0, state_module.state["last_tower_msg_id"])
            self.assertEqual(0, state_module.state["tower_reply_due_at"])
            self.assertEqual(0, state_module.state["tower_retry_count"])
            self.assertGreater(state_module.state["next_tower_time"], retry_at + tower.TOWER_REPLY_TIMEOUT_SEC)

    async def test_real_tower_start_and_final_report_are_idempotent(self):
        send_as_id = 8659059303
        now = 1_700_000_200.0
        self._prepare_identity(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_msg_id"] = 5001
            state_module.state["tower_reply_due_at"] = now + 60
            state_module.state["next_tower_time"] = now + 60

            def fake_next_day(ts):
                next_ts = ts + 24 * 60 * 60
                state_module.state["next_tower_time"] = next_ts
                return next_ts

            reply_to = SimpleNamespace(id=5001, raw_text=".闯塔")
            with (
                patch.object(tower, "_schedule_tower_next_day", side_effect=fake_next_day),
                patch.object(tower, "clear_pending_tasks_by_commands"),
                patch.object(tower, "save_state"),
                patch.object(tower, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                handled = await tower.handle_tower_reply(
                    "【琉璃问心塔】\n今日塔相：镜花水月",
                    now,
                    reply_to,
                    matched_family="tower",
                )
                first_next = state_module.state["next_tower_time"]
                handled_edit = await tower.handle_tower_reply(
                    "【试炼古塔 - 战报】\n\n闯塔历程:\n总收获:",
                    now + 5,
                    reply_to,
                    matched_family="tower",
                )

            self.assertTrue(handled)
            self.assertTrue(handled_edit)
            self.assertEqual(get_day_key(now), state_module.state["last_tower_day"])
            self.assertEqual(0, state_module.state["last_tower_msg_id"])
            self.assertEqual(0, state_module.state["tower_reply_due_at"])
            self.assertEqual(0, state_module.state["tower_retry_count"])
            self.assertEqual(first_next, state_module.state["next_tower_time"])
            audit_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
