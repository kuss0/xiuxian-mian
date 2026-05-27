import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

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


if __name__ == "__main__":
    unittest.main()
