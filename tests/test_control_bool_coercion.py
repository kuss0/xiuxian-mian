import asyncio
import copy
import unittest
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

        with patch.object(control, "save_state"), patch.object(control, "send_audit_log", new=AsyncMock()):
            ok, message = asyncio.run(control.toggle_global_enabled("off", source="test"))

        self.assertTrue(ok, message)
        self.assertFalse(state_module.get_global_enabled())
        self.assertIn("全局暂停", message)

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
