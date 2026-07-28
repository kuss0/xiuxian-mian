import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import runtime
from model import state as state_module
from model.features import passive_inbox
from model.features import tower
from model.timing import get_day_key


class TowerSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_TEST"],
        }
        tower._TOWER_TASKS.clear()
        tower._TOWER_RUN_LOCK = None
        tower._TOWER_LAST_RUN_AT = 0
        tower._TOWER_UPSTREAM_CIRCUIT_UNTIL = 0
        tower._TOWER_PREFERRED_ENTRY_INDEX = 0

    def tearDown(self):
        tower._TOWER_TASKS.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(self.snapshot)

    def _prepare_identity(self, identity_id):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="TowerUser")

    async def test_due_scheduler_queues_miniapp_worker_without_game_command(self):
        identity_id = 8659059301
        now = 1_700_000_000.0
        self._prepare_identity(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_day"] = ""
            state_module.state["next_tower_time"] = now - 1
            with patch.object(tower, "_is_tower_window_time", return_value=True), \
                    patch.object(tower, "_launch_tower_worker", return_value=True) as launch_mock, \
                    patch.object(tower, "save_state"):
                await tower.run_tower_scheduler(now)

            launch_mock.assert_called_once()
            self.assertEqual(identity_id, launch_mock.call_args.args[0])
            self.assertEqual(0, state_module.state["last_tower_msg_id"])
            self.assertGreater(state_module.state["next_tower_time"], now)

    async def test_worker_marks_success_done_and_never_sends_text_command(self):
        identity_id = 8659059302
        now = 1_700_000_100.0
        self._prepare_identity(identity_id)
        result = {
            "ok": True,
            "message": "洞府琉璃问心塔：通过 8 层｜止步 9 层｜修为 +1260｜塔印 +42",
            "extra": {
                "replay": {"cleared_count": 8, "end_floor": 8, "failed_floor": 9},
                "gains": {"修为": 1260, "塔印": 42},
                "rewards": {},
            },
        }
        with patch.object(tower, "run_cave_public_tower", new=AsyncMock(return_value=result)) as run_mock, \
                patch.object(tower.time, "time", return_value=now), \
                patch.object(tower, "send_audit_log", new=AsyncMock()), \
                patch.object(tower, "save_state"), \
                patch.object(tower, "console_log"):
            await tower._run_tower_worker(
                identity_id,
                ["https://t.me/fanrenxiuxian_bot?startapp=df_TEST"],
                scheduled_at=now,
            )

        run_mock.assert_awaited_once()
        with state_module.use_identity(identity_id):
            self.assertEqual(get_day_key(now), state_module.state["last_tower_day"])
            self.assertEqual(0, state_module.state["tower_reply_due_at"])
            self.assertEqual(0, state_module.state["last_tower_msg_id"])

    async def test_worker_failure_backs_off_without_replaying_text_command(self):
        identity_id = 8659059303
        now = 1_700_000_200.0
        self._prepare_identity(identity_id)
        result = {"ok": False, "message": "洞府琉璃问心塔动态入口获取失败：HTTP 502", "extra": {}}
        with patch.object(tower, "run_cave_public_tower", new=AsyncMock(return_value=result)), \
                patch.object(tower.time, "time", return_value=now), \
                patch.object(tower, "send_audit_log", new=AsyncMock()), \
                patch.object(tower, "save_state"), \
                patch.object(tower, "console_log"):
            await tower._run_tower_worker(
                identity_id,
                ["https://t.me/fanrenxiuxian_bot?startapp=df_TEST"],
                scheduled_at=now,
            )

        with state_module.use_identity(identity_id):
            self.assertEqual("", state_module.state["last_tower_day"])
            self.assertGreater(state_module.state["next_tower_time"], now)
            self.assertEqual(0, state_module.state["last_tower_msg_id"])

    async def test_generic_retry_retires_legacy_tower_pending_without_send(self):
        identity_id = 8659059304
        now = 1_700_000_300.0
        self._prepare_identity(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["pending_tasks"] = {
                5001: {"cmd": ".闯塔", "sent_at": now - 500, "timeout": 30, "retry": 0, "max_retry": 1},
            }
        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
                patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock:
            await runtime.run_retry_scheduler(now, send_as_id=identity_id)

        send_mock.assert_not_awaited()
        with state_module.use_identity(identity_id):
            self.assertNotIn(5001, state_module.state["pending_tasks"])

    async def test_passive_legacy_tower_card_marks_day_complete_without_send(self):
        identity_id = 8659059305
        now = 1_700_000_400.0
        self._prepare_identity(identity_id)
        with state_module.use_identity(identity_id), \
                patch.object(tower, "save_state"):
            changed = passive_inbox._apply_tower_passive(
                "【琉璃问心塔】\n本次共闯过 21 层。",
                now,
                "tower",
            )

            self.assertTrue(changed)
            self.assertEqual(get_day_key(now), state_module.state["last_tower_day"])
            self.assertGreater(state_module.state["next_tower_time"], now)
