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

from model import control
from model import state as state_module
from model.features import concubine, wild_training


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

    def test_startup_spread_uses_normal_wild_training_cd_fallback(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = now - 1

        with patch.object(control.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC):
            changed = control.spread_overdue_runtime_timers(now, reason="test")

        self.assertEqual(1, changed)
        with state_module.use_identity(send_as_id):
            self.assertEqual(now + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])

    def test_initialize_wild_training_unknown_timer_uses_normal_cycle(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id):
            self._disable_modules()
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = 0
            state_module.state["wild_training_reply_to_msg_id"] = 123
            state_module.state["wild_training_reply_due_at"] = now - 1
            state_module.state["wild_training_retry_count"] = 1

        with patch.object(control.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC):
            control.initialize_identity_runtime(send_as_id, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["wild_training_reply_due_at"])
            self.assertEqual(0, state_module.state["wild_training_retry_count"])
            self.assertEqual(now + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])

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
            self.assertEqual(now + 90, state_module.state["next_concubine_time"])


if __name__ == "__main__":
    unittest.main()
