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
        state_module.set_guanxing_shift_delay_sec(25)

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
        self.assertEqual(25, state_module.get_guanxing_shift_delay_sec())

    def test_basic_config_accepts_guanxing_negative_shift_delay(self):
        with patch.object(ui, "save_state"), patch.object(ui, "console_log"):
            ok, message = asyncio.run(
                ui.ui_set_basic_config(
                    "-100123",
                    "8388633812",
                    "0",
                    True,
                    tiandao_judgement_enabled=False,
                    guanxing_monitor_enabled=False,
                    guanxing_shift_target="@target_user",
                    guanxing_shift_delay_sec="-60",
                    guanxing_monitor_targets=[],
                )
            )

        self.assertTrue(ok, message)
        self.assertIn("观星首发偏移 -60秒", message)
        self.assertEqual(-60, state_module.get_guanxing_shift_delay_sec())

    def test_basic_config_rejects_guanxing_shift_delay_too_early(self):
        with patch.object(ui, "save_state"), patch.object(ui, "console_log"):
            ok, message = asyncio.run(
                ui.ui_set_basic_config(
                    "-100123",
                    "8388633812",
                    "0",
                    True,
                    tiandao_judgement_enabled=False,
                    guanxing_monitor_enabled=False,
                    guanxing_shift_target="@target_user",
                    guanxing_shift_delay_sec="-181",
                    guanxing_monitor_targets=[],
                )
            )

        self.assertFalse(ok)
        self.assertIn("不能小于 -180 秒", message)

    def test_basic_config_rejects_guanxing_shift_delay_non_numeric(self):
        with patch.object(ui, "save_state"), patch.object(ui, "console_log"):
            ok, message = asyncio.run(
                ui.ui_set_basic_config(
                    "-100123",
                    "8388633812",
                    "0",
                    True,
                    tiandao_judgement_enabled=False,
                    guanxing_monitor_enabled=False,
                    guanxing_shift_target="@target_user",
                    guanxing_shift_delay_sec="abc",
                    guanxing_monitor_targets=[],
                )
            )

        self.assertFalse(ok)
        self.assertIn("必须是数字", message)

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
            "dispatch_group_ids": "-100888",
            "dispatch_listener_account_map": {"-100888": "9002"},
            "participant_identity_ids": [],
            "dispatch_participant_identity_ids": [],
            "virtual_hall_match_enabled_map": {"-100777": "false"},
        }

        with patch.object(ui, "save_state"):
            ok, message = ui.ui_set_replica_config(payload)

        self.assertTrue(ok, message)
        self.assertEqual([-100777], state_module.get_replica_group_ids())
        self.assertEqual([-100888], state_module.get_replica_dispatch_group_ids())
        self.assertEqual({"-100888": 9002}, state_module.get_replica_dispatch_listener_account_map())
        self.assertEqual({"-100777": False}, state_module.get_replica_virtual_hall_match_enabled_map())

    def test_replica_config_preserves_dispatch_fields_when_omitted(self):
        state_module.set_replica_group_ids([-100777])
        state_module.set_replica_dispatch_group_ids([-100888])
        state_module.set_replica_dispatch_listener_account_map({"-100888": 9002})
        state_module.ensure_identity_registered(9002001)
        state_module.set_replica_dispatch_participant_identity_ids([9002001])
        payload = {
            "group_ids": "-100777",
            "listener_account_map": {"-100777": "9001"},
            "participant_identity_ids": [],
            "virtual_hall_match_enabled_map": {"-100777": "false"},
        }

        with patch.object(ui, "save_state"):
            ok, message = ui.ui_set_replica_config(payload)

        self.assertTrue(ok, message)
        self.assertEqual([-100888], state_module.get_replica_dispatch_group_ids())
        self.assertEqual({"-100888": 9002}, state_module.get_replica_dispatch_listener_account_map())
        self.assertEqual([9002001], state_module.get_replica_dispatch_participant_identity_ids())

    def test_replica_config_saves_dispatch_participants_separately(self):
        for identity_id in (9003001, 9003002, 9003003):
            state_module.ensure_identity_registered(identity_id)
        payload = {
            "group_ids": "-100777",
            "listener_account_map": {"-100777": "9001"},
            "participant_identity_ids": ["9003001", "9003002", "9003003"],
            "dispatch_participant_identity_ids": ["9003001", "9003002"],
            "virtual_hall_match_enabled_map": {},
        }

        with patch.object(ui, "save_state"):
            ok, message = ui.ui_set_replica_config(payload)

        self.assertTrue(ok, message)
        self.assertEqual([9003001, 9003002, 9003003], state_module.get_replica_participant_identity_ids())
        self.assertEqual([9003001, 9003002], state_module.get_replica_dispatch_participant_identity_ids())
        snapshot = ui.get_replica_config_snapshot()
        self.assertEqual([9003001, 9003002], snapshot["dispatch_participant_identity_ids"])

    def test_replica_config_snapshot_uses_typed_open_commands(self):
        leader_id = 9003101
        low_id = 9003102
        state_module.ensure_identity_registered(leader_id)
        state_module.ensure_identity_registered(low_id)
        state_module.update_send_as_profile(leader_id, username="leader", realm="结丹初期", enabled=True)
        state_module.update_send_as_profile(low_id, username="low", realm="筑基后期", enabled=True)
        state_module.set_replica_participant_identity_ids([leader_id, low_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "苍坤残图": 2}, "sections": {}},
            str(low_id): {"items": {"苍坤残图": 1}, "sections": {}},
        })

        snapshot = ui.get_replica_config_snapshot()
        identities = {int(item["identity_id"]): item for item in snapshot["identity_options"]}

        leader = identities[leader_id]
        self.assertTrue(leader["can_open"])
        self.assertEqual(["virtual_hall", "cangkun"], leader["openable_kinds"])
        self.assertEqual("", leader["preferred_open_kind"])
        self.assertEqual("需指定类型", leader["preferred_open_label"])
        self.assertEqual(
            [".开启副本 @leader 虚", ".开启副本 @leader 苍"],
            [item["command"] for item in leader["open_commands"]],
        )
        self.assertNotIn(".开启副本 @leader", [item["command"] for item in leader["open_commands"]])

        low = identities[low_id]
        self.assertFalse(low["can_open"])
        self.assertEqual([], low["open_commands"])
        self.assertEqual("", low["preferred_open_label"])

    def test_replica_config_saves_query_aggregator_separately_and_preserves_blank_secret(self):
        state_module.set_replica_query_aggregator_config({
            "base_url": "https://old.example/api",
            "client_id": "old-client",
            "secret": "old-secret",
        })
        payload = {
            "group_ids": "-100777",
            "listener_account_map": {"-100777": "9001"},
            "participant_identity_ids": [],
            "virtual_hall_match_enabled_map": {},
            "query_aggregator_config": {
                "base_url": "https://new.example/api/",
                "client_id": "new-client",
                "secret": "",
            },
        }

        with patch.object(ui, "save_state"):
            ok, message = ui.ui_set_replica_config(payload)

        self.assertTrue(ok, message)
        self.assertEqual(
            {
                "base_url": "https://new.example/api",
                "client_id": "new-client",
                "secret": "old-secret",
            },
            state_module.get_replica_query_aggregator_config(),
        )
        snapshot = ui.get_replica_config_snapshot()["query_aggregator_config"]
        self.assertTrue(snapshot["configured"])
        self.assertTrue(snapshot["secret_configured"])
        self.assertNotIn("secret", snapshot)

    def test_replica_config_ignores_dispatch_group_overlaps(self):
        state_module.set_game_group_id(-100999)
        payload = {
            "group_ids": "-100777",
            "listener_account_map": {"-100777": "9001"},
            "dispatch_group_ids": "-100999\n-100777\n-100888",
            "dispatch_listener_account_map": {"-100999": "9003", "-100777": "9001", "-100888": "9002"},
            "participant_identity_ids": [],
            "virtual_hall_match_enabled_map": {},
        }

        with patch.object(ui, "save_state"):
            ok, message = ui.ui_set_replica_config(payload)

        self.assertTrue(ok, message)
        self.assertIn("已忽略", message)
        self.assertEqual([-100888], state_module.get_replica_dispatch_group_ids())
        self.assertEqual({"-100888": 9002}, state_module.get_replica_dispatch_listener_account_map())


if __name__ == "__main__":
    unittest.main()
