import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from model import config
from model import control
from model import state as state_module
from model import ui


class ControlBoolCoercionTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_direct_identity_toggle_treats_form_false_string_as_disabled(self):
        send_as_id = 990301
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)

        with patch.object(control, "save_state"), patch.object(control, "send_audit_log", new=AsyncMock()):
            ok, message = asyncio.run(control.set_identity_enabled(send_as_id, "false", source="test"))

        self.assertTrue(ok, message)
        self.assertFalse(state_module.get_identity_enabled(send_as_id))
        self.assertIn("暂停身份", message)

    def test_direct_global_toggle_treats_form_false_string_as_disabled(self):
        state_module.set_global_enabled(True)
        for identity_id in (990311, 990312):
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, enabled=True)

        with patch.object(control, "save_state"), patch.object(control, "send_audit_log", new=AsyncMock()) as audit_mock:
            ok, message = asyncio.run(control.toggle_global_enabled("off", source="test"))

        self.assertTrue(ok, message)
        self.assertFalse(state_module.get_global_enabled())
        self.assertIn("全局暂停", message)
        self.assertEqual("high", audit_mock.await_args.kwargs["priority"])

    def test_global_resume_resets_safety_watchdog_marker(self):
        state_module.set_global_enabled(False)
        state_module.ensure_identity_registered(990313)
        state_module.update_send_as_profile(990313, enabled=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            fused_marker = state_dir / "safety_watchdog_fused.json"
            reset_marker = state_dir / "safety_watchdog_reset.json"
            fused_marker.write_text(json.dumps({"reason": "old"}), encoding="utf-8")

            with patch.object(control, "STATE_DIR", str(state_dir)), \
                 patch.object(control, "save_state"), \
                 patch.object(control, "send_audit_log", new=AsyncMock()):
                ok, message = asyncio.run(control.toggle_global_enabled(True, source="test"))

            self.assertTrue(ok, message)
            self.assertTrue(state_module.get_global_enabled())
            self.assertFalse(fused_marker.exists())
            payload = json.loads(reset_marker.read_text(encoding="utf-8"))
            self.assertGreater(payload["reset_at_epoch"], 0)

    def test_direct_identity_module_toggle_treats_form_false_string_as_disabled(self):
        send_as_id = 990321
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["checkin_enabled"] = True

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("点卯", "false", send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["checkin_enabled"])

    def test_concubine_voyage_available_for_non_xinggong_identity(self):
        send_as_id = 990322
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="落云宗", realm="结丹后期")

        modules = state_module.get_available_module_names(send_as_id)

        self.assertIn("侍妾远航", modules)
        self.assertIn("共历心劫", modules)
        self.assertNotIn("灵树", modules)

    def test_wendao_only_available_for_yuanying_sect_identity(self):
        cases = (
            ("unknown_sect", "", False),
            ("wrong_sect", "落云宗", False),
            ("yuanying_sect", "元婴宗", True),
        )

        for offset, (label, sect_name, expected_available) in enumerate(cases):
            with self.subTest(label=label):
                send_as_id = 990330 + offset
                state_module.ensure_identity_registered(send_as_id)
                state_module.update_send_as_profile(send_as_id, sect_name=sect_name, realm="元婴初期")

                modules = state_module.get_available_module_names(send_as_id)

                self.assertEqual(expected_available, "问道" in modules)

    def test_wendao_toggle_rejected_for_non_yuanying_sect(self):
        send_as_id = 990332
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="落云宗", realm="元婴初期")

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("问道", True, send_as_id=send_as_id))

        self.assertFalse(ok)
        self.assertIn("未提供问道模块", message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["wendao_enabled"])

    def test_archived_tree_module_cannot_be_enabled(self):
        send_as_id = 990323
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="落云宗", realm="结丹后期")

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("灵树", True, send_as_id=send_as_id))

        self.assertFalse(ok)
        self.assertIn("灵树模块已归档", message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["tree_enabled"])

    def test_ui_module_toggle_message_matches_coerced_false_string(self):
        send_as_id = 990331
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["checkin_enabled"] = True

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(ui.ui_set_module_enabled(send_as_id, "点卯", "false"))

        self.assertTrue(ok, message)
        self.assertIn("已关闭点卯", message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["checkin_enabled"])

    def test_direct_global_module_toggle_treats_form_false_string_as_disabled(self):
        state_module.set_guanxing_monitor_enabled(True)

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("观星监控", "0"))

        self.assertTrue(ok, message)
        self.assertFalse(state_module.get_guanxing_monitor_enabled())

    def test_disabling_checkin_does_not_clear_sect_teach_runtime(self):
        send_as_id = 990341
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["checkin_enabled"] = True
            state_module.state["sect_teach_enabled"] = True
            state_module.state["next_checkin_time"] = now + 100
            state_module.state["next_sect_teach_time"] = now + 200
            state_module.state["sect_teach_reply_to_msg_id"] = 101
            state_module.state["last_sect_teach_msg_id"] = 102
            state_module.state["pending_tasks"] = {
                201: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
                202: {"cmd": config.CMD_SECT_TEACH, "sent_at": now, "retry": 0},
            }

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("点卯", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["checkin_enabled"])
            self.assertTrue(state_module.state["sect_teach_enabled"])
            self.assertEqual(0, state_module.state["next_checkin_time"])
            self.assertEqual(now + 200, state_module.state["next_sect_teach_time"])
            self.assertEqual(101, state_module.state["sect_teach_reply_to_msg_id"])
            self.assertEqual(102, state_module.state["last_sect_teach_msg_id"])
            self.assertEqual({202: {"cmd": config.CMD_SECT_TEACH, "sent_at": now, "retry": 0}}, state_module.state["pending_tasks"])

    def test_disabling_sect_teach_does_not_clear_checkin_runtime(self):
        send_as_id = 990342
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["checkin_enabled"] = True
            state_module.state["sect_teach_enabled"] = True
            state_module.state["next_checkin_time"] = now + 100
            state_module.state["next_sect_teach_time"] = now + 200
            state_module.state["sect_teach_reply_to_msg_id"] = 101
            state_module.state["last_sect_teach_msg_id"] = 102
            state_module.state["pending_tasks"] = {
                201: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
                202: {"cmd": config.CMD_SECT_TEACH, "sent_at": now, "retry": 0},
            }

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("宗门传功", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertTrue(state_module.state["checkin_enabled"])
            self.assertFalse(state_module.state["sect_teach_enabled"])
            self.assertEqual(now + 100, state_module.state["next_checkin_time"])
            self.assertEqual(0, state_module.state["next_sect_teach_time"])
            self.assertEqual(0, state_module.state["sect_teach_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["last_sect_teach_msg_id"])
            self.assertEqual({201: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0}}, state_module.state["pending_tasks"])

    def test_disabling_tianxing_clears_observation_and_pending(self):
        send_as_id = 990344
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
        with state_module.use_identity(send_as_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {"last_observed_at": now, "last_error": "旧天星错误"}
            state_module.state["pending_tasks"] = {
                301: {"cmd": config.CMD_TIANXING_PANEL, "sent_at": now, "retry": 0},
                302: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
            }

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("天星宗", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["tianxing_enabled"])
            self.assertEqual({}, state_module.state["tianxing_observation"])
            self.assertEqual({302: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0}}, state_module.state["pending_tasks"])

    def test_disabling_yinluo_clears_observation_and_pending(self):
        send_as_id = 990345
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="阴罗宗")
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {"last_observed_at": now, "last_error": "旧阴罗错误"}
            state_module.state["pending_tasks"] = {
                401: {"cmd": config.CMD_YINLUO_BANNER, "sent_at": now, "retry": 0},
                402: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
            }

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("阴罗宗", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["yinluo_enabled"])
            self.assertEqual({}, state_module.state["yinluo_observation"])
            self.assertEqual({402: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0}}, state_module.state["pending_tasks"])

    def test_startup_initialization_clears_disabled_passive_observations(self):
        send_as_id = 990346
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["tianxing_enabled"] = False
            state_module.state["tianxing_observation"] = {"last_error": "旧天星错误"}
            state_module.state["yinluo_enabled"] = False
            state_module.state["yinluo_observation"] = {"last_error": "旧阴罗错误"}

        with patch.object(control, "mark_dirty") as mark_dirty_mock:
            control.initialize_identity_runtime(send_as_id, now=1_700_000_000.0)

        with state_module.use_identity(send_as_id):
            self.assertEqual({}, state_module.state["tianxing_observation"])
            self.assertEqual({}, state_module.state["yinluo_observation"])
        mark_dirty_mock.assert_called()

    def test_toggling_concubine_preserves_partner_snapshot(self):
        send_as_id = 990343
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_availability"] = "available"
            state_module.state["concubine_name"] = "银月"
            state_module.state["concubine_kind"] = "道心侍妾"
            state_module.state["concubine_affinity"] = 330
            state_module.state["concubine_dream_due_at"] = now + 3600
            state_module.state["concubine_fragment_xutian_count"] = 1
            state_module.state["concubine_fragment_cangkun_count"] = 3
            state_module.state["concubine_last_snapshot_at"] = now - 600
            state_module.state["concubine_status_msg_id"] = 123
            state_module.state["concubine_phase"] = "status_pending"

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("侍妾", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["concubine_enabled"])
            self.assertEqual("available", state_module.state["concubine_availability"])
            self.assertEqual("银月", state_module.state["concubine_name"])
            self.assertEqual("道心侍妾", state_module.state["concubine_kind"])
            self.assertEqual(330, state_module.state["concubine_affinity"])
            self.assertEqual(now + 3600, state_module.state["concubine_dream_due_at"])
            self.assertEqual(1, state_module.state["concubine_fragment_xutian_count"])
            self.assertEqual(3, state_module.state["concubine_fragment_cangkun_count"])
            self.assertEqual(now - 600, state_module.state["concubine_last_snapshot_at"])
            self.assertEqual(0, state_module.state["concubine_status_msg_id"])
            self.assertEqual("idle", state_module.state["concubine_phase"])

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("侍妾", True, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertTrue(state_module.state["concubine_enabled"])
            self.assertEqual("available", state_module.state["concubine_availability"])
            self.assertEqual("银月", state_module.state["concubine_name"])
            self.assertEqual("道心侍妾", state_module.state["concubine_kind"])
            self.assertEqual(330, state_module.state["concubine_affinity"])
            self.assertEqual(now - 600, state_module.state["concubine_last_snapshot_at"])


if __name__ == "__main__":
    unittest.main()
