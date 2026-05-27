import atexit
import asyncio
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


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

from model import state as state_module
from model import ui


class GuanxingConfigTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_basic_config_preserves_guanxing_settings_when_fields_are_omitted(self):
        state_module.set_guanxing_monitor_targets(["地磁暴动", "星辰异象"])
        state_module.set_guanxing_shift_target("@target_user")

        with patch.object(ui, "save_state"), patch.object(ui, "console_log"):
            ok, message = asyncio.run(
                ui.ui_set_basic_config(
                    "-100123",
                    "8388633812",
                    "0",
                    True,
                    tiandao_judgement_enabled=False,
                    guanxing_monitor_enabled=True,
                    guanxing_shift_target=None,
                    guanxing_monitor_targets=None,
                )
            )

        self.assertTrue(ok, message)
        self.assertIn("观星监控 开启", message)
        self.assertEqual(["地磁暴动", "星辰异象"], state_module.get_guanxing_monitor_targets())
        self.assertEqual("@target_user", state_module.get_guanxing_shift_target())

    def test_ui_bool_parser_handles_form_strings(self):
        for value in ("false", "0", "off", "关闭", ""):
            with self.subTest(value=value):
                self.assertFalse(ui._coerce_ui_bool(value))

        for value in ("true", "1", "on", "开启"):
            with self.subTest(value=value):
                self.assertTrue(ui._coerce_ui_bool(value))

    def test_basic_config_treats_form_false_strings_as_disabled(self):
        state_module.set_guanxing_monitor_targets([])

        with patch.object(ui, "save_state"), patch.object(ui, "console_log"):
            ok, message = asyncio.run(
                ui.ui_set_basic_config(
                    "-100123",
                    "8388633812",
                    "0",
                    "false",
                    tiandao_judgement_enabled="0",
                    guanxing_monitor_enabled="off",
                    guanxing_shift_target=None,
                    guanxing_monitor_targets=None,
                )
            )

        self.assertTrue(ok, message)
        self.assertFalse(state_module.is_auto_delete_sent_messages_enabled())
        self.assertFalse(state_module.get_tiandao_judgement_enabled())
        self.assertFalse(state_module.get_guanxing_monitor_enabled())

    def test_ui_feature_setters_treat_form_false_strings_as_disabled(self):
        send_as_id = 9001001
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, spiritual_root_attrs="金", replica_gold_dps_enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["taiyi_node_search_enabled"] = True

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok_small, message_small = asyncio.run(
                ui.ui_set_small_world_feature_enabled(send_as_id, "preach", "false")
            )
            ok_tianti, message_tianti = asyncio.run(
                ui.ui_set_tianti_feature_enabled(send_as_id, "wenxin", "false")
            )
            ok_taiyi, message_taiyi = asyncio.run(
                ui.ui_set_taiyi_node_search_enabled(send_as_id, "false")
            )
            ok_gold, message_gold = ui.ui_set_replica_gold_dps_enabled(send_as_id, "false")

        self.assertTrue(ok_small, message_small)
        self.assertTrue(ok_tianti, message_tianti)
        self.assertTrue(ok_taiyi, message_taiyi)
        self.assertTrue(ok_gold, message_gold)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["small_world_preach_enabled"])
            self.assertFalse(state_module.state["tianti_wenxin_enabled"])
            self.assertFalse(state_module.state["taiyi_node_search_enabled"])
        self.assertFalse(state_module.get_replica_gold_dps_enabled(send_as_id))

    def test_replica_config_treats_form_false_strings_as_disabled(self):
        payload = {
            "group_ids": "-100777",
            "listener_account_map": {"-100777": "9001"},
            "participant_identity_ids": [],
            "virtual_hall_match_enabled_map": {"-100777": "false"},
        }

        with patch.object(ui, "save_state"):
            ok, message = ui.ui_set_replica_config(payload)

        self.assertTrue(ok, message)
        self.assertEqual([-100777], state_module.get_replica_group_ids())
        self.assertEqual({"-100777": False}, state_module.get_replica_virtual_hall_match_enabled_map())


if __name__ == "__main__":
    unittest.main()
