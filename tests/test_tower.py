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
            self.assertEqual(now, state_module.state["last_tower_command_sent_at"])
            self.assertEqual(now + tower.TOWER_REPLY_TIMEOUT_SEC, state_module.state["tower_reply_due_at"])
            self.assertEqual(0, state_module.state["tower_retry_count"])
            self.assertEqual(state_module.state["tower_reply_due_at"], state_module.state["next_tower_time"])

    async def test_recent_send_attempt_without_msg_id_waits_instead_of_duplicate_send(self):
        send_as_id = 8659059304
        now = 1_700_000_060.0
        first_sent_at = now - 6
        self._prepare_identity(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_day"] = ""
            state_module.state["last_tower_msg_id"] = 0
            state_module.state["last_tower_command_sent_at"] = first_sent_at
            state_module.state["tower_reply_due_at"] = 0
            state_module.state["tower_retry_count"] = 0
            state_module.state["next_tower_time"] = now - 1

            with (
                patch.object(tower, "_is_tower_window_time", return_value=True),
                patch.object(tower, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(tower, "mark_dirty"),
                patch.object(tower, "save_state"),
            ):
                await tower.run_tower_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(first_sent_at + tower.TOWER_DUPLICATE_SEND_GUARD_SEC, state_module.state["tower_reply_due_at"])
            self.assertEqual(state_module.state["tower_reply_due_at"], state_module.state["next_tower_time"])

    async def test_send_failure_clears_pre_send_guard_and_uses_long_retry(self):
        send_as_id = 8659059305
        now = 1_700_000_070.0
        failed_at = now + 1
        self._prepare_identity(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_day"] = ""
            state_module.state["next_tower_time"] = now - 1

            with (
                patch.object(tower, "_is_tower_window_time", return_value=True),
                patch.object(tower.time, "time", return_value=failed_at),
                patch.object(tower, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                patch.object(tower, "classify_game_send_block", return_value={"status": "none", "code": ""}),
                patch.object(tower, "save_state"),
                patch.object(tower, "send_audit_log", new=AsyncMock()),
            ):
                await tower.run_tower_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(0, state_module.state["last_tower_command_sent_at"])
            self.assertEqual(0, state_module.state["tower_reply_due_at"])
            self.assertEqual(failed_at + tower.RETRY_MAX_SEC, state_module.state["next_tower_time"])

    async def test_runtime_unsent_block_is_deferred_without_failure_log(self):
        send_as_id = 8659059306
        now = 1_700_000_075.0
        failed_at = now + 1
        self._prepare_identity(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_day"] = ""
            state_module.state["next_tower_time"] = now - 1

            with (
                patch.object(tower, "_is_tower_window_time", return_value=True),
                patch.object(tower.time, "time", return_value=failed_at),
                patch.object(tower, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(tower, "classify_game_send_block", return_value={
                    "status": "unsent",
                    "code": "global_recovery_cooldown",
                }),
                patch.object(tower, "save_state"),
                patch.object(tower, "console_log") as console_mock,
                patch.object(tower, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                await tower.run_tower_scheduler(now)

            self.assertEqual(0, state_module.state["last_tower_command_sent_at"])
            self.assertEqual(0, state_module.state["tower_reply_due_at"])
            self.assertEqual(failed_at + tower.RETRY_MAX_SEC, state_module.state["next_tower_time"])
            self.assertIn("未发送", str(console_mock.call_args.args[0]))
            audit_mock.assert_not_awaited()

    async def test_send_timeout_keeps_unknown_attempt_waiting_for_reply(self):
        send_as_id = 8659059307
        now = 1_700_000_076.0
        failed_at = now + 1
        self._prepare_identity(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_day"] = ""
            state_module.state["next_tower_time"] = now - 1

            with (
                patch.object(tower, "_is_tower_window_time", return_value=True),
                patch.object(tower.time, "time", return_value=failed_at),
                patch.object(tower, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(tower, "classify_game_send_block", return_value={
                    "status": "unknown",
                    "code": "send_timeout",
                }),
                patch.object(tower, "save_state"),
                patch.object(tower, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                await tower.run_tower_scheduler(now)

            self.assertEqual(now, state_module.state["last_tower_command_sent_at"])
            self.assertEqual(failed_at + tower.TOWER_REPLY_TIMEOUT_SEC, state_module.state["tower_reply_due_at"])
            self.assertEqual(state_module.state["tower_reply_due_at"], state_module.state["next_tower_time"])
            self.assertIn("状态未知", str(audit_mock.await_args.args[0]))

    async def test_dirty_next_tower_time_fail_closed_without_sending(self):
        now = 1_700_000_080.0
        dirty_values = ("not-a-timestamp", "nan", "inf")

        for offset, dirty_value in enumerate(dirty_values):
            with self.subTest(dirty_value=dirty_value):
                send_as_id = 8659059310 + offset
                self._prepare_identity(send_as_id)

                with state_module.use_identity(send_as_id):
                    state_module.state["tower_enabled"] = True
                    state_module.state["last_tower_day"] = ""
                    state_module.state["last_tower_msg_id"] = 0
                    state_module.state["tower_reply_due_at"] = 0
                    state_module.state["tower_retry_count"] = 0
                    state_module.state["next_tower_time"] = dirty_value

                    with (
                        patch.object(tower, "send_game_command", new=AsyncMock()) as send_mock,
                        patch.object(tower, "mark_dirty") as dirty_mock,
                        patch.object(tower, "save_state") as save_mock,
                    ):
                        await tower.run_tower_scheduler(now + offset)

                    send_mock.assert_not_awaited()
                    dirty_mock.assert_not_called()
                    save_mock.assert_not_called()
                    self.assertEqual(dirty_value, state_module.state["next_tower_time"])

    async def test_dirty_tower_reply_due_at_fail_closed_keeps_waiting(self):
        send_as_id = 8659059313
        now = 1_700_000_090.0
        dirty_due_at = "nan"
        self._prepare_identity(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_day"] = ""
            state_module.state["last_tower_msg_id"] = 5001
            state_module.state["tower_reply_due_at"] = dirty_due_at
            state_module.state["tower_retry_count"] = 0
            state_module.state["next_tower_time"] = now - 1

            with (
                patch.object(tower.random, "uniform", return_value=3),
                patch.object(tower, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(tower, "mark_dirty") as dirty_mock,
                patch.object(tower, "save_state") as save_mock,
                patch.object(tower, "send_audit_log", new=AsyncMock()),
            ):
                await tower.run_tower_scheduler(now)

            send_mock.assert_not_awaited()
            dirty_mock.assert_not_called()
            save_mock.assert_not_called()
            self.assertEqual(5001, state_module.state["last_tower_msg_id"])
            self.assertEqual(dirty_due_at, state_module.state["tower_reply_due_at"])
            self.assertEqual(0, state_module.state["tower_retry_count"])
            self.assertEqual(now - 1, state_module.state["next_tower_time"])

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
