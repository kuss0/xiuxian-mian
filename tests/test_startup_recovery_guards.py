import atexit
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CREATED_ENV = False

if not ENV_PATH.exists():
    ENV_PATH.write_text(
        "\n".join(
            [
                "API_ID=12345",
                "API_HASH=00000000000000000000000000000000",
                "TG_PROXY_TYPE=",
                "TG_PROXY_HOST=127.0.0.1:7890",
                "LOG_GROUP_ID=0",
                "LOG_SEND_MODE=account",
                "ADMIN_ID=1",
                "CHAOGU_UI_HOST=127.0.0.1",
                "CHAOGU_UI_PORT=3030",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    CREATED_ENV = True

if CREATED_ENV:
    atexit.register(lambda: ENV_PATH.exists() and ENV_PATH.unlink())

sys.path.insert(0, str(PROJECT_ROOT))

from model import action_guard, config, control
from model import state as state_module
from model.features import concubine, explore_rift, small_world, wild_training


class StartupRecoveryGuardTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _prepare_identity(self, send_as_id=991301):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="recover")
        return send_as_id

    def _disable_modules(self):
        for key in state_module.IDENTITY_MODULE_COLUMNS:
            if key.endswith("_enabled") or key in {"is_maturing", "is_invading", "is_harvested", "pending_irrigation", "tree_bootstrap_check_needed"}:
                state_module.state[key] = False

    def test_startup_spread_uses_short_wild_training_recovery_probe(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=300):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual(now + 300, state_module.state["next_wild_training_time"])

    def test_startup_spread_keeps_released_tianxing_wild_training_immediate(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["tianxing_enabled"] = True
            state_module.state["next_wild_training_time"] = now - 1
            state_module.state["wild_training_reply_to_msg_id"] = 0
            state_module.state["tianxing_observation"] = {
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_change": "探索",
                "current_change_until": now + 24 * 3600,
            }
            state_module.state["tianxing_timeline_state"] = {
                "phase": "downstream_released",
                "route": "探索",
                "released_routes": {
                    "探索": {
                        "released_at": now - 60,
                        "basis": "change_fate",
                    }
                },
            }

        with patch.object(control.random, "uniform", return_value=300):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual(now + 1, state_module.state["next_wild_training_time"])

    def test_startup_spread_keeps_expired_tianxing_craft_prediction_wild_training_immediate(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["tianxing_enabled"] = True
            state_module.state["next_wild_training_time"] = now - 1
            state_module.state["wild_training_reply_to_msg_id"] = 0
            state_module.state["wild_training_retry_count"] = 0
            state_module.state["tianxing_observation"] = {
                "current_prediction": "炼制",
                "current_prediction_until": now + 3600,
                "current_change": "",
                "current_change_until": 0,
            }
            state_module.state["tianxing_timeline_state"] = {
                "phase": "blocked_replan",
                "route": "炼制",
                "craft_farm": {
                    "phase": "sent_waiting_reply",
                    "next_time": now - 60,
                    "last_action": "consume_craft_prediction",
                },
            }

        with patch.object(control.random, "uniform", return_value=wild_training.WILD_TRAINING_RETRY_MIN_SEC):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual(now + 1, state_module.state["next_wild_training_time"])

    def test_startup_spread_recovers_wild_training_from_recent_real_result(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        result_at = now - 30 * 60
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_last_result"] = "修为+12000"
            state_module.state["wild_training_last_result_at"] = result_at
            state_module.state["next_wild_training_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=300):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual(result_at + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])

    def test_startup_spread_recovers_wild_training_from_completed_anchor_after_status_overwrite(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        result_at = now - 30 * 60
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_last_result"] = "天星时间线：sent_waiting_ack"
            state_module.state["wild_training_last_result_at"] = 0
            state_module.state["wild_training_last_completed_at"] = result_at
            state_module.state["next_wild_training_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=300):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual(result_at + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])

    def test_startup_spread_keeps_due_wild_training_retry_short(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = now - 1
            state_module.state["wild_training_reply_to_msg_id"] = 0
            state_module.state["wild_training_retry_count"] = 1

        with patch.object(control.random, "uniform", return_value=wild_training.WILD_TRAINING_RETRY_MIN_SEC):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual(now + wild_training.WILD_TRAINING_RETRY_MIN_SEC, state_module.state["next_wild_training_time"])
            self.assertEqual(1, state_module.state["wild_training_retry_count"])

    def test_startup_spread_preserves_phaseful_queued_launch_deadline(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "queued_launch"
            state_module.state["last_deep_retreat_command_time"] = now - 300
            state_module.state["next_deep_retreat_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=900):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual("queued_launch", state_module.state["deep_retreat_phase"])
            self.assertEqual(now - 180, state_module.state["next_deep_retreat_time"])

    def test_startup_spread_clamps_polluted_phaseful_queued_launch_deadline(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "queued_launch"
            state_module.state["last_deep_retreat_command_time"] = now - 300
            state_module.state["next_deep_retreat_time"] = now + 1800

        with patch.object(control.random, "uniform", return_value=900):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("queued_launch", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 1, state_module.state["next_deep_retreat_time"])

    def test_startup_spread_preserves_phaseful_post_summary_deadline(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["next_deep_retreat_time"] = now + 30

        with patch.object(control.random, "uniform", return_value=900):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 30, state_module.state["next_deep_retreat_time"])

    def test_startup_spread_clamps_polluted_phaseful_post_summary_deadline(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["next_deep_retreat_time"] = now + 1800

        with patch.object(control.random, "uniform", return_value=900):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 1, state_module.state["next_deep_retreat_time"])

    def test_startup_spread_does_not_stretch_fishing_timer(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["fishing_enabled"] = True
            state_module.state["next_fishing_time"] = now - 1

        changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(0, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual(now - 1, state_module.state["next_fishing_time"])

    def test_startup_spread_covers_near_future_recovery_timers(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["concubine_tianji_enabled"] = True
            state_module.state["next_concubine_time"] = now + 90

        with patch.object(control.random, "uniform", return_value=600):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual(now + 600, state_module.state["next_concubine_time"])

    def test_initialize_wild_training_unknown_timer_uses_short_recovery_probe(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = 0
            state_module.state["wild_training_reply_to_msg_id"] = 123
            state_module.state["wild_training_reply_due_at"] = now - 1
            state_module.state["wild_training_retry_count"] = 1

        with patch.object(control.random, "uniform", return_value=300):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["wild_training_reply_due_at"])
            self.assertEqual(0, state_module.state["wild_training_retry_count"])
            self.assertEqual(now + 300, state_module.state["next_wild_training_time"])

    def test_initialize_explore_rift_unknown_timer_schedules_recovery(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = 0
            state_module.state["explore_rift_reply_to_msg_id"] = 10425942
            state_module.state["explore_rift_reply_due_at"] = now - 1
            state_module.state["explore_rift_pending_result_msg_id"] = 10425944
            state_module.state["explore_rift_last_error"] = "保留上一条异常"

        with patch.object(explore_rift.random, "uniform", return_value=explore_rift.EXPLORE_RIFT_RECOVERY_MIN_SEC):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual(
                now + explore_rift.EXPLORE_RIFT_RECOVERY_MIN_SEC,
                state_module.state["next_explore_rift_time"],
            )
            self.assertEqual("保留上一条异常", state_module.state["explore_rift_last_error"])

    def test_startup_recovery_clears_stale_action_guard_sessions(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "idle"
            state_module.state["next_deep_retreat_time"] = now + 3600
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["next_concubine_time"] = now + 3600
            state_module.state["action_guard_sessions"] = {
                "tree_water": {
                    "action_key": "tree_water",
                    "attempt": 1,
                    "last_sent_at": now - 120,
                    "last_msg_id": 11,
                    "last_command": ".灵树灌溉",
                },
                "deep_retreat": {
                    "action_key": "deep_retreat",
                    "attempt": 1,
                    "last_sent_at": now - 120,
                    "last_msg_id": 12,
                    "last_command": ".深度闭关",
                },
                "concubine_dream": {
                    "action_key": "concubine_dream",
                    "attempt": 1,
                    "last_sent_at": now - 120,
                    "last_msg_id": 13,
                    "last_command": ".入梦寻图",
                },
            }

        control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual({}, state_module.state["action_guard_sessions"])

    def test_startup_recovery_keeps_active_remote_action_guard_block(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "idle"
            state_module.state["next_deep_retreat_time"] = now + 3600
            state_module.state["action_guard_sessions"] = {
                "deep_retreat": {
                    "action_key": "deep_retreat",
                    "attempt": 1,
                    "last_sent_at": now - 120,
                    "last_msg_id": 12,
                    "last_command": ".深度闭关",
                    "remote_block_until": now + 1800,
                    "remote_block_reason": "游戏提示深度闭关执行中",
                    "remote_block_kind": "running",
                },
            }

        control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            session = state_module.state["action_guard_sessions"].get("deep_retreat") or {}
            self.assertEqual(now + 1800, session.get("remote_block_until"))

        allowed, reason = action_guard.before_send(config.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now + 60)
        self.assertFalse(allowed)
        self.assertIn("执行中", reason)

    def test_action_guard_allows_after_remote_block_expires(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "idle"
            state_module.state["next_deep_retreat_time"] = now - 1
            state_module.state["action_guard_sessions"] = {
                "deep_retreat": {
                    "action_key": "deep_retreat",
                    "attempt": 1,
                    "last_sent_at": now - 120,
                    "last_msg_id": 12,
                    "last_command": ".深度闭关",
                    "remote_block_until": now - 1,
                    "remote_block_reason": "游戏提示深度闭关执行中",
                    "remote_block_kind": "running",
                },
            }

        allowed, reason = action_guard.before_send(config.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now)

        self.assertTrue(allowed, reason)
        with state_module.use_identity(send_as_id):
            session = state_module.state["action_guard_sessions"].get("deep_retreat") or {}
            self.assertEqual(0, int(session.get("attempt", 0) or 0))
            self.assertEqual(0, float(session.get("remote_block_until", 0) or 0))

    def test_startup_recovery_clears_orphan_small_world_query_guard(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_phase"] = "query_pending"
            state_module.state["small_world_query_msg_id"] = 10976313
            state_module.state["next_small_world_time"] = now + 180
            state_module.state["pending_tasks"] = {}
            state_module.state["action_guard_sessions"] = {
                "small_world_query": {
                    "action_key": "small_world_query",
                    "attempt": 1,
                    "last_sent_at": now - 600,
                    "last_msg_id": 10976313,
                    "last_command": config.CMD_SMALL_WORLD_QUERY,
                }
            }

        with patch.object(small_world.random, "uniform", return_value=600):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_query_msg_id"])
            self.assertNotIn("small_world_query", state_module.state["action_guard_sessions"])
            self.assertEqual(now + 600, state_module.state["next_small_world_time"])
            self.assertIn("遗留等待已恢复清理", state_module.state["small_world_last_error"])

    def test_action_guard_keeps_live_reply_wait_but_allows_after_module_timeout(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        command = wild_training.get_wild_training_command("谨慎")
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_reply_to_msg_id"] = 123
            state_module.state["wild_training_reply_due_at"] = now + 300
            state_module.state["action_guard_sessions"] = {
                "wild_training": {
                    "action_key": "wild_training",
                    "attempt": 1,
                    "last_sent_at": now - 180,
                    "first_sent_at": now - 180,
                    "next_allowed_at": now - 1,
                    "last_msg_id": 123,
                    "last_command": command,
                }
            }

        allowed, reason = action_guard.before_send(command, send_as_id=send_as_id, now=now)

        self.assertFalse(allowed)
        self.assertIn("等待游戏回复", reason)
        with state_module.use_identity(send_as_id):
            self.assertIn("wild_training", state_module.state["action_guard_sessions"])
            state_module.state["wild_training_reply_due_at"] = now - 1

        allowed, reason = action_guard.before_send(command, send_as_id=send_as_id, now=now)

        self.assertTrue(allowed, reason)
        with state_module.use_identity(send_as_id):
            session = state_module.state["action_guard_sessions"].get("wild_training") or {}
            self.assertEqual(0, int(session.get("attempt", 0) or 0))

    def test_action_guard_reconciles_hehuan_pending_deadline_for_retry(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 600,
                "auto_pending_msg_id": 456,
                "auto_pending_sent_at": now - 60,
                "auto_pending_deadline_at": now + 120,
            }
            state_module.state["action_guard_sessions"] = {
                "hehuan_dual": {
                    "action_key": "hehuan_dual",
                    "attempt": 1,
                    "last_sent_at": now - 60,
                    "first_sent_at": now - 60,
                    "next_allowed_at": now - 1,
                    "last_msg_id": 456,
                    "last_command": config.CMD_HEHUAN_DUAL,
                }
            }

        allowed, reason = action_guard.before_send(config.CMD_HEHUAN_DUAL, send_as_id=send_as_id, now=now)

        self.assertFalse(allowed)
        self.assertIn("等待游戏回复", reason)
        with state_module.use_identity(send_as_id):
            self.assertIn("hehuan_dual", state_module.state["action_guard_sessions"])
            state_module.state["hehuan_observation"]["auto_pending_deadline_at"] = now - 1

        allowed, reason = action_guard.before_send(config.CMD_HEHUAN_DUAL, send_as_id=send_as_id, now=now)

        self.assertTrue(allowed, reason)
        with state_module.use_identity(send_as_id):
            session = state_module.state["action_guard_sessions"].get("hehuan_dual") or {}
            self.assertEqual(0, int(session.get("attempt", 0) or 0))

    def test_action_guard_allows_phaseful_queued_launch_but_blocks_running_phases(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_phase"] = "queued_launch"

        allowed, reason = action_guard.before_send(config.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now)

        self.assertTrue(allowed, reason)

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_phase"] = "launching"

        allowed, reason = action_guard.before_send(config.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now)

        self.assertFalse(allowed)
        self.assertIn("等待游戏回复", reason)

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_phase"] = "waiting_summary"

        allowed, reason = action_guard.before_send(config.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now)

        self.assertFalse(allowed)
        self.assertIn("等待游戏回复", reason)

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_phase"] = "observing_summary"

        allowed, reason = action_guard.before_send(config.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now)

        self.assertFalse(allowed)
        self.assertIn("等待游戏回复", reason)

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_phase"] = "idle"
            state_module.state["yuanying_phase"] = "queued_launch"

        allowed, reason = action_guard.before_send(config.CMD_YUANYING, send_as_id=send_as_id, now=now)

        self.assertTrue(allowed, reason)

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_phase"] = "running"

        allowed, reason = action_guard.before_send(config.CMD_YUANYING, send_as_id=send_as_id, now=now)

        self.assertFalse(allowed)
        self.assertIn("等待游戏回复", reason)

        allowed, reason = action_guard.before_send(config.CMD_YUANYING_SECT_RETREAT, send_as_id=send_as_id, now=now)

        self.assertFalse(allowed)
        self.assertIn("等待游戏回复", reason)

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_phase"] = "waiting_summary"

        allowed, reason = action_guard.before_send(config.CMD_YUANYING, send_as_id=send_as_id, now=now)

        self.assertFalse(allowed)
        self.assertIn("等待游戏回复", reason)

    def test_tianti_recovery_queries_status_before_stale_gangfeng(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["tianti_enabled"] = True
            state_module.state["tianti_gangfeng_enabled"] = True
            state_module.state["next_tianti_status_time"] = 0
            state_module.state["next_tianti_gangfeng_time"] = 0

        with patch.object(control.random, "uniform", return_value=120):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual(now + 1, state_module.state["next_tianti_status_time"])
            self.assertEqual(now + control.RECOVERY_SPREAD_MAX_SEC + 120, state_module.state["next_tianti_gangfeng_time"])

    def test_tianti_recovery_keeps_fresh_status_snapshot_quiet(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["tianti_enabled"] = True
            state_module.state["tianti_gangfeng_enabled"] = True
            state_module.state["next_tianti_status_time"] = 0
            state_module.state["next_tianti_gangfeng_time"] = 0
            state_module.state["next_tianti_climb_time"] = now + 3600
            state_module.state["tianti_last_status_seen_at"] = now - 60
            state_module.state["tianti_progress_current"] = 10
            state_module.state["tianti_cooldown_text"] = "19:30:31"

        with patch.object(control.random, "uniform", return_value=120):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual(0, state_module.state["next_tianti_status_time"])
            self.assertEqual(now + control.RECOVERY_SPREAD_MAX_SEC + 120, state_module.state["next_tianti_gangfeng_time"])

    def test_tianti_recovery_keeps_local_gangfeng_timer_quiet(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["tianti_enabled"] = True
            state_module.state["tianti_gangfeng_enabled"] = True
            state_module.state["next_tianti_status_time"] = 0
            state_module.state["next_tianti_gangfeng_time"] = now - 1
            state_module.state["next_tianti_climb_time"] = now + 480
            state_module.state["tianti_last_status_seen_at"] = now - control.TIANTI_RECOVERY_STATUS_FRESH_SEC - 1
            state_module.state["tianti_progress_current"] = 10
            state_module.state["tianti_cycle_count"] = 38
            state_module.state["tianti_gangfeng_level"] = 11
            state_module.state["tianti_cooldown_text"] = "8分钟"
            state_module.state["tianti_gangfeng_status"] = "可用"

        with patch.object(control.random, "uniform", return_value=120):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual(0, state_module.state["next_tianti_status_time"])
            self.assertEqual(now - 1, state_module.state["next_tianti_gangfeng_time"])

    def test_tianti_recovery_keeps_skip_reason_for_empty_daily_marker(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["tianti_enabled"] = True
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_gangfeng_enabled"] = True
            state_module.state["tianti_last_wenxin_day"] = ""
            state_module.state["tianti_wenxin_last_trigger_key"] = ""
            state_module.state["tianti_last_skip_reason"] = "wait_final_stage"
            state_module.state["tianti_theoretical_max_stage"] = 12
            state_module.state["tianti_wenxin_trigger_stage"] = 11
            state_module.state["next_tianti_climb_time"] = now + 3600
            state_module.state["next_tianti_gangfeng_time"] = now + 7200
            state_module.state["tianti_progress_current"] = 8
            state_module.state["tianti_cycle_count"] = 39
            state_module.state["tianti_gangfeng_level"] = 12
            state_module.state["tianti_cooldown_text"] = "1小时"

        control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("wait_final_stage", state_module.state["tianti_last_skip_reason"])
            self.assertEqual(12, state_module.state["tianti_theoretical_max_stage"])
            self.assertEqual(11, state_module.state["tianti_wenxin_trigger_stage"])

    def test_tianti_recovery_clears_stale_daily_marker(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["tianti_enabled"] = True
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_last_wenxin_day"] = "2023-11-13"
            state_module.state["tianti_wenxin_last_trigger_key"] = "2023-11-13|11|12|bucket=1|final_stage"
            state_module.state["tianti_gangfeng_last_trigger_key"] = "2023-11-13|stage=11|bucket=1"
            state_module.state["tianti_last_skip_reason"] = "trigger_key_hit"
            state_module.state["tianti_theoretical_max_stage"] = 12
            state_module.state["tianti_wenxin_trigger_stage"] = 11
            state_module.state["next_tianti_wenxin_time"] = now + 3600

        control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("", state_module.state["tianti_last_wenxin_day"])
            self.assertEqual("", state_module.state["tianti_wenxin_last_trigger_key"])
            self.assertEqual("", state_module.state["tianti_gangfeng_last_trigger_key"])
            self.assertEqual("", state_module.state["tianti_last_skip_reason"])
            self.assertEqual(0, state_module.state["tianti_theoretical_max_stage"])
            self.assertEqual(0, state_module.state["tianti_wenxin_trigger_stage"])
            self.assertEqual(0, state_module.state["next_tianti_wenxin_time"])

    def test_tree_recovery_does_not_query_status_for_normal_due_timer(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["tree_enabled"] = True
            state_module.state["next_irr_time"] = now + 60
            state_module.state["last_tree_status_sent_at"] = now - 8 * 3600

        with patch.object(control, "request_tree_bootstrap_check") as request_mock:
            control.initialize_identity_runtime(send_as_id, now)

        request_mock.assert_not_called()
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["tree_bootstrap_check_needed"])
            self.assertEqual(0, state_module.state["tree_bootstrap_check_due_at"])
            self.assertEqual(now + 60, state_module.state["next_irr_time"])

    def test_tree_recovery_clears_stale_normal_bootstrap_probe(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["tree_enabled"] = True
            state_module.state["next_irr_time"] = now + 3600
            state_module.state["tree_bootstrap_check_needed"] = True
            state_module.state["tree_bootstrap_check_due_at"] = now - 1

        control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["tree_bootstrap_check_needed"])
            self.assertEqual(0, state_module.state["tree_bootstrap_check_due_at"])
            self.assertEqual(now + 3600, state_module.state["next_irr_time"])

    def test_tree_recovery_releases_stale_harvested_maturing_state(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["tree_enabled"] = True
            state_module.state["is_maturing"] = True
            state_module.state["is_harvested"] = True
            state_module.state["tree_maturing_logged"] = True
            state_module.state["tree_harvest_followup_due_at"] = now + 3600
            state_module.state["tree_harvest_inflight_until"] = now + 1800
            state_module.state["last_tree_status_sent_at"] = now - control.TREE_HARVESTED_MATURING_STALE_SEC - 1
            state_module.state["next_irr_time"] = now + 115 * 24 * 3600

        control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["is_maturing"])
            self.assertFalse(state_module.state["is_harvested"])
            self.assertFalse(state_module.state["tree_maturing_logged"])
            self.assertEqual(0, state_module.state["tree_harvest_followup_due_at"])
            self.assertEqual(0, state_module.state["tree_harvest_inflight_until"])
            self.assertEqual(now, state_module.state["next_irr_time"])

    def test_tree_recovery_keeps_invasion_status_probe(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["tree_enabled"] = True
            state_module.state["is_invading"] = True
            state_module.state["next_irr_time"] = now + 3600

        with patch.object(control, "request_tree_bootstrap_check", return_value=True) as request_mock:
            control.initialize_identity_runtime(send_as_id, now)

        request_mock.assert_called_once_with(now)

    def test_phaseful_idle_zero_timer_is_not_restored_as_immediate_send(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "idle"
            state_module.state["next_deep_retreat_time"] = 0
            state_module.state["last_deep_retreat_command_time"] = now - 3600

        with patch.object(control.random, "uniform", return_value=120):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 120, state_module.state["next_deep_retreat_time"])
            self.assertEqual(now - 3600, state_module.state["last_deep_retreat_command_time"])

    def test_phaseful_queued_launch_recovery_stays_immediate_when_deadline_expired(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "queued_launch"
            state_module.state["last_deep_retreat_command_time"] = now - 300
            state_module.state["next_deep_retreat_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=900):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("queued_launch", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 1, state_module.state["next_deep_retreat_time"])

    def test_deep_retreat_orphan_summary_due_recovery_wakes_immediately(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - 60
            state_module.state["last_deep_retreat_command_time"] = 0
            state_module.state["last_deep_retreat_summary_msg_id"] = 0
            state_module.state["next_deep_retreat_time"] = now + 120

        with patch.object(control.random, "uniform", return_value=900):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("summary_due", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 1, state_module.state["next_deep_retreat_time"])

    def test_phaseful_invalid_recovery_resets_to_short_idle_spread(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "unknown_phase"
            state_module.state["yuanying_probe_pending"] = True
            state_module.state["yuanying_summary_sent_at"] = now - 300
            state_module.state["last_yuanying_summary_msg_id"] = 456
            state_module.state["last_yuanying_command_time"] = now - 3600
            state_module.state["next_yuanying_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=90):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["yuanying_phase"])
            self.assertFalse(state_module.state["yuanying_probe_pending"])
            self.assertEqual(0, state_module.state["yuanying_summary_sent_at"])
            self.assertEqual(0, state_module.state["last_yuanying_summary_msg_id"])
            self.assertEqual(now + 90, state_module.state["next_yuanying_time"])

    def test_phaseful_waiting_recovery_uses_phaseful_spread(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 60
            state_module.state["last_deep_retreat_summary_msg_id"] = 123
            state_module.state["next_deep_retreat_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=900) as uniform_mock:
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 900, state_module.state["next_deep_retreat_time"])
        uniform_mock.assert_any_call(
            control.RECOVERY_PHASEFUL_IDLE_MIN_SEC,
            control.RECOVERY_PHASEFUL_IDLE_MAX_SEC,
        )

    def test_taiyi_yindao_presend_restart_schedules_short_retry(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_phase"] = "yindao_pending"
            state_module.state["taiyi_phase_entered_at"] = now - 120
            state_module.state["taiyi_yindao_msg_id"] = 0
            state_module.state["taiyi_node_search_msg_id"] = 0
            state_module.state["taiyi_node_define_msg_id"] = 0
            state_module.state["next_taiyi_cycle_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=90):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["taiyi_phase"])
            self.assertEqual(0, state_module.state["taiyi_phase_entered_at"])
            self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
            self.assertEqual(0, state_module.state["taiyi_node_search_msg_id"])
            self.assertEqual(0, state_module.state["taiyi_node_define_msg_id"])
            self.assertEqual(now + 90, state_module.state["next_taiyi_cycle_time"])
            self.assertIn("发送边界不确定", state_module.state["taiyi_last_error"])

    def test_taiyi_yindao_restart_with_msg_id_still_schedules_short_retry(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_phase"] = "yindao_pending"
            state_module.state["taiyi_phase_entered_at"] = now - 120
            state_module.state["taiyi_yindao_msg_id"] = 12345
            state_module.state["taiyi_node_search_msg_id"] = 0
            state_module.state["taiyi_node_define_msg_id"] = 0
            state_module.state["next_taiyi_cycle_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=90):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["taiyi_phase"])
            self.assertEqual(0, state_module.state["taiyi_phase_entered_at"])
            self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
            self.assertEqual(now + 90, state_module.state["next_taiyi_cycle_time"])
            self.assertIn("发送边界不确定", state_module.state["taiyi_last_error"])

    def test_taiyi_yindao_restart_keeps_inflight_retry_pending(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_phase"] = "yindao_pending"
            state_module.state["taiyi_phase_entered_at"] = now - 120
            state_module.state["taiyi_yindao_msg_id"] = 12345
            state_module.state["taiyi_yindao_resend_count"] = 1
            state_module.state["taiyi_node_search_msg_id"] = 0
            state_module.state["taiyi_node_define_msg_id"] = 0
            state_module.state["next_taiyi_cycle_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=90):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("yindao_pending", state_module.state["taiyi_phase"])
            self.assertEqual(1, state_module.state["taiyi_yindao_resend_count"])
            self.assertEqual(12345, state_module.state["taiyi_yindao_msg_id"])
            self.assertEqual(now - 120, state_module.state["taiyi_phase_entered_at"])

    def test_taiyi_yindao_lost_reply_calibrates_from_real_log_text(self):
        send_as_id = 8659059191
        command_ts = 1_700_000_320.0
        reply_ts = command_ts + 1
        now = command_ts + 60
        self._prepare_identity(send_as_id)
        day = "2023-11-15"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / f"{day}.log"
            log_path.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "ts": "2023-11-15 06:18:40 UTC+8",
                            "event_type": "message",
                            "message_id": 9446793,
                            "chat_id": -1001680975844,
                            "sender_id": send_as_id,
                            "reply_to_msg_id": 7310786,
                            "text": ".引道 水",
                        },
                        {
                            "ts": "2023-11-15 06:18:41 UTC+8",
                            "event_type": "message",
                            "message_id": 9446794,
                            "chat_id": -1001680975844,
                            "sender_id": 8349385938,
                            "reply_to_msg_id": 9446793,
                            "text": "你引动【水之道】，获得了 100点神识！\n并领悟了临时增益【润水之息】：\n普通闭关修炼时，获得的修为增加45%。",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with state_module.use_identity(send_as_id):
                self._disable_modules()
                state_module.state["taiyi_enabled"] = True
                state_module.state["taiyi_phase"] = "idle"
                state_module.state["taiyi_phase_entered_at"] = command_ts - 120
                state_module.state["taiyi_yindao_msg_id"] = 0
                state_module.state["next_taiyi_cycle_time"] = command_ts + control.TAIYI_CYCLE_CD_SEC + control.CD_BUFFER_SEC
                state_module.state["taiyi_failure_history"] = [command_ts]
                state_module.state["taiyi_last_error"] = "引道 reply 未回，按正常12h周期兜底"

            with patch.object(control, "MESSAGES_DIR", tmpdir), patch.object(control, "console_log"):
                control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["taiyi_phase"])
            self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
            self.assertEqual(reply_ts + control.TAIYI_CYCLE_CD_SEC + control.CD_BUFFER_SEC, state_module.state["next_taiyi_cycle_time"])
            self.assertEqual(reply_ts, state_module.state["taiyi_phase_entered_at"])
            self.assertEqual([], state_module.state["taiyi_failure_history"])
            self.assertEqual("", state_module.state["taiyi_last_error"])

    def test_concubine_heart_only_identity_restores_runtime(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["concubine_heart_enabled"] = True
            state_module.state["concubine_phase"] = "heart_pending"
            state_module.state["concubine_availability"] = "available"
            state_module.state["concubine_name"] = "若兰"
            state_module.state["concubine_heart_msg_id"] = 701
            state_module.state["concubine_heart_prompt_msg_id"] = 702
            state_module.state["concubine_heart_round"] = 1
            state_module.state["concubine_heart_choice_prompt_msg_id"] = 702
            state_module.state["concubine_heart_choice_round"] = 1
            state_module.state["concubine_heart_choice_sent_at"] = now - 30
            state_module.state["next_concubine_time"] = 0

        with patch.object(concubine.random, "uniform", return_value=90):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_heart_msg_id"])
            self.assertEqual(0, state_module.state["concubine_heart_prompt_msg_id"])
            self.assertEqual(0, state_module.state["concubine_heart_round"])
            self.assertEqual(0, state_module.state["concubine_heart_choice_prompt_msg_id"])
            self.assertEqual(0, state_module.state["concubine_heart_choice_round"])
            self.assertEqual(0, state_module.state["concubine_heart_choice_sent_at"])
            expected_due = now - 30 + config.CONCUBINE_HEART_CD_SEC + config.CD_BUFFER_SEC
            self.assertEqual(expected_due, state_module.state["concubine_heart_due_at"])
            self.assertEqual(expected_due + 90, state_module.state["next_concubine_time"])
            self.assertNotEqual("发送 .共历心劫 失败", state_module.state["concubine_heart_last_error"])


if __name__ == "__main__":
    unittest.main()
