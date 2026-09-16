import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import persistence, state as state_module
from model import yinluo_accounting as accounting
from model.features import hehuan, tianxing, yinluo
from yinluo_native_support import CHAT, native_logs, native_reply, seed_resources


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

    def _prepare_yinluo(self, command, sent_at, root):
        self.assertTrue(seed_resources(self.identity_id, sent_at))
        state_module.get_identity_state(self.identity_id)["yinluo_enabled"] = True
        record, reason = accounting.prepare_operation(
            self.identity_id, command, CHAT, sent_at, source_module="阴罗宗",
        )
        self.assertEqual("", reason)
        self.assertTrue(accounting.record_transport(
            self.identity_id, record["op_id"], phase="sent", msg_id=root, chat_id=CHAT, sent_at=sent_at,
        ))
        return record

    def test_yinluo_blood_forest_timeout_does_not_invent_consumption(self):
        now = 1_780_000_000.0
        sent_at = now - 30
        command = yinluo.CMD_YINLUO_BLOOD_FOREST
        with (state_module.use_identity(self.identity_id),
              patch.object(persistence, "save_state", return_value=True) as save_mock,
              patch.object(yinluo, "read_yinluo_log_batch", return_value=[])):
            record = self._prepare_yinluo(command, sent_at, 9003)
            save_mock.reset_mock()
            handled = yinluo.reconcile_yinluo_timeout_from_pending(
                9003, cmd=command, sent_at=sent_at, now=now, chat_id=CHAT,
            )
            observed = yinluo.normalize_yinluo_observation(state_module.state["yinluo_observation"])
            self.assertEqual("sent", accounting.current_operation(self.identity_id, record["op_id"])["phase"])
            self.assertEqual("yinluo_command_in_flight", accounting.admission_reason(self.identity_id, command))
        self.assertFalse(handled)
        self.assertNotEqual("assumed_consumed", observed["last_result"])
        self.assertEqual(0, observed["next_blood_forest_time"])
        save_mock.assert_not_called()

    def test_yinluo_refine_timeout_rejects_unthreaded_observation_only(self):
        now = 1_780_000_000.0
        sent_at = now - 30
        command = ".囚禁魂魄 3 凶兽戾魄"
        with (state_module.use_identity(self.identity_id),
              patch.object(persistence, "save_state", return_value=True) as save_mock,
              patch.object(yinluo, "read_yinluo_log_batch", return_value=[])):
            record = self._prepare_yinluo(command, sent_at, 9005)
            state_module.state["yinluo_observation"].update({
                "last_observed_at": sent_at + 1,
                "last_action": "囚禁魂魄",
                "last_result": "success",
                "last_refine_slot": 3,
                "last_resource": "凶兽戾魄",
                "sha_current": 300,
                "soul_stocks": {"凶兽戾魄": 0},
                "refining_slot_numbers": [3],
            })
            before = copy.deepcopy(state_module.state["yinluo_observation"])
            save_mock.reset_mock()
            handled = yinluo.reconcile_yinluo_timeout_from_pending(
                9005, cmd=command, sent_at=sent_at, now=now, chat_id=CHAT,
            )
            after = state_module.state["yinluo_observation"]
            self.assertEqual("sent", accounting.current_operation(self.identity_id, record["op_id"])["phase"])
        self.assertFalse(handled)
        self.assertEqual(before, after)
        save_mock.assert_not_called()

    def test_yinluo_refine_timeout_recovers_exact_native_final_edit(self):
        now, sent_at = 1_780_000_000.0, 1_779_999_970.0
        command = ".囚禁魂魄 3 凶兽戾魄"
        rows = native_logs(native_reply(
            self.identity_id, command, "一缕【凶兽戾魄】被强行打入3号炼化槽，炼化已开始。", sent_at + 2,
            root=9005, command_at=sent_at, edited=True,
        ))
        with (state_module.use_identity(self.identity_id),
              patch.object(persistence, "save_state", return_value=True),
              patch.object(yinluo, "read_yinluo_log_batch", return_value=rows)):
            record = self._prepare_yinluo(command, sent_at, 9005)
            self.assertTrue(yinluo.reconcile_yinluo_timeout_from_pending(
                9005, cmd=command, sent_at=sent_at, now=now, chat_id=CHAT,
            ))
            self.assertEqual("complete", accounting.current_operation(self.identity_id, record["op_id"])["phase"])
            self.assertEqual([3], state_module.state["yinluo_observation"]["refining_slot_numbers"])

    def test_yinluo_refine_timeout_does_not_accept_stale_reply(self):
        now = 1_780_000_000.0
        sent_at = now - 30
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": sent_at - 1,
                "last_action": "囚禁魂魄",
                "last_result": "success",
            }
            handled = yinluo.reconcile_yinluo_timeout_from_pending(
                9006,
                cmd=".囚禁魂魄 3 凶兽戾魄",
                sent_at=sent_at,
                now=now,
            )

        self.assertFalse(handled)

    def test_yinluo_blood_forest_timeout_does_not_retry_on_unrelated_summary(self):
        now = 1_780_000_000.0
        sent_at = now - 30
        command = yinluo.CMD_YINLUO_BLOOD_FOREST
        rows = native_logs(native_reply(
            self.identity_id, command, "修士 @timeout_user 深度闭关总结\n【深度闭关总结】", sent_at + 5,
            root=9003, command_at=sent_at, edited=True,
        ))
        state_module.update_send_as_profile(self.identity_id, username="timeout_user")
        with (state_module.use_identity(self.identity_id),
              patch.object(persistence, "save_state", return_value=True),
              patch.object(yinluo, "read_yinluo_log_batch", return_value=rows)):
            record = self._prepare_yinluo(command, sent_at, 9003)
            handled = yinluo.reconcile_yinluo_timeout_from_pending(
                9003, cmd=command, sent_at=sent_at, now=now, chat_id=CHAT,
            )
            observed = yinluo.normalize_yinluo_observation(state_module.state["yinluo_observation"])
            self.assertEqual("sent", accounting.current_operation(self.identity_id, record["op_id"])["phase"])
            self.assertEqual("yinluo_command_in_flight", accounting.admission_reason(self.identity_id, command))
        self.assertFalse(handled)
        self.assertNotEqual("phaseful_consumed", observed["last_result"])
        self.assertEqual(0, observed["next_blood_forest_time"])


if __name__ == "__main__":
    unittest.main()
