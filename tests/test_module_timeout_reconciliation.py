import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import hehuan, tianxing, yinluo


class ModuleTimeoutReconciliationTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        self.identity_id = 990801
        state_module.ensure_identity_registered(self.identity_id)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_tianxing_pending_timeout_holds_downstream_for_calibration(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_auto_config"] = {"calibration_backoff_sec": 120}
            active_step = {
                "id": "predict:explore",
                "action": "predict",
                "arg": "探索",
                "status": "sent_waiting_ack",
                "send_msg_id": 9001,
                "ack_due_at": now - 1,
            }
            state_module.state["tianxing_timeline_state"] = {
                "phase": "sent_waiting_ack",
                "active_step_index": 0,
                "active_step": active_step,
                "steps": [active_step],
            }

            with patch.object(tianxing, "save_state") as save_mock:
                handled = tianxing.reconcile_tianxing_timeout_from_pending(
                    9001,
                    cmd=".推命 探索",
                    now=now,
                )

            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertTrue(handled)
        self.assertEqual("ack_timeout", timeline["phase"])
        self.assertEqual("ack_timeout", timeline["active_step"]["status"])
        self.assertGreater(timeline["active_step"]["calibration_due_at"], now)
        self.assertIn("不放行下游", timeline["last_error"])
        save_mock.assert_called_once()

    def test_hehuan_pending_timeout_assumes_consumed_only_after_start_evidence(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_observation"] = {
                "auto_pending_msg_id": 9002,
                "auto_pending_sent_at": now - 300,
                "auto_pending_deadline_at": now - 1,
                "last_observed_at": now - 260,
                "last_path": hehuan.PATH_TONGCAN,
                "last_action": "双修 温养",
                "last_result": "pending",
                "recent": [
                    {
                        "ts": now - 260,
                        "path": hehuan.PATH_TONGCAN,
                        "action": "双修 温养",
                        "result": "pending",
                        "summary": "契印感应，温养双修结算中",
                    }
                ],
            }

            with patch.object(hehuan, "_recover_hehuan_pending_from_message_log", return_value=False), \
                    patch.object(hehuan, "save_state") as save_mock:
                handled = hehuan.reconcile_hehuan_timeout_from_pending(9002, now=now)

            observed = hehuan.normalize_hehuan_observation(state_module.state["hehuan_observation"])

        self.assertTrue(handled)
        self.assertEqual("assumed_consumed", observed["last_result"])
        self.assertEqual(0, observed["auto_pending_msg_id"])
        self.assertGreater(observed["next_hehuan_time"], now)
        save_mock.assert_called_once()

    def test_yinluo_blood_forest_pending_timeout_sets_consumed_cooldown(self):
        now = 1_780_000_000.0
        sent_at = now - 30
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "next_blood_forest_time": 0,
            }

            with patch.object(yinluo, "save_state") as save_mock:
                handled = yinluo.reconcile_yinluo_timeout_from_pending(
                    9003,
                    cmd=yinluo.CMD_YINLUO_BLOOD_FOREST,
                    sent_at=sent_at,
                    now=now,
                )

            observed = yinluo.normalize_yinluo_observation(state_module.state["yinluo_observation"])
            plan = yinluo.build_yinluo_manual_plan("blood_forest", now=now)

        self.assertTrue(handled)
        self.assertEqual("assumed_consumed", observed["last_result"])
        self.assertGreaterEqual(
            observed["next_blood_forest_time"],
            sent_at + yinluo.YINLUO_BLOOD_FOREST_OBSERVED_CD_SEC + yinluo.YINLUO_TIME_BUFFER_SEC,
        )
        self.assertFalse(plan["allowed"])
        save_mock.assert_called_once()

    def test_yinluo_blood_forest_timeout_retries_when_phaseful_summary_consumed_command(self):
        now = 1_780_000_000.0
        sent_at = now - 30
        summary_entry = {
            "event_type": "edit",
            "message_id": 9004,
            "text": "📜 修士 @timeout_user 深度闭关总结\n【深度闭关总结】",
        }
        state_module.update_send_as_profile(self.identity_id, username="timeout_user")
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "next_blood_forest_time": sent_at + yinluo.YINLUO_BLOOD_FOREST_OBSERVED_CD_SEC,
            }

            with (
                patch.object(yinluo, "iter_message_log_entries_between", return_value=iter([(summary_entry, sent_at + 5)])),
                patch.object(yinluo, "save_state") as save_mock,
            ):
                handled = yinluo.reconcile_yinluo_timeout_from_pending(
                    9003,
                    cmd=yinluo.CMD_YINLUO_BLOOD_FOREST,
                    sent_at=sent_at,
                    now=now,
                )

            observed = yinluo.normalize_yinluo_observation(state_module.state["yinluo_observation"])

        self.assertTrue(handled)
        self.assertEqual("phaseful_consumed", observed["last_result"])
        self.assertEqual(now + yinluo.YINLUO_AUTO_CHAIN_STEP_SEC, observed["next_blood_forest_time"])
        self.assertEqual("", observed["last_error"])
        save_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
