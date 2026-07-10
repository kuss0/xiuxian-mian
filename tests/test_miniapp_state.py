import copy
import json
import unittest
from unittest.mock import patch

from model import miniapp_state, persistence
from model import state as state_module
from model.features import cave_treasure_miniapp


class MiniAppStateTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        state_module.ensure_identity_registered(1001)
        state_module.update_send_as_profile(1001, username="xuruode4", label="竹灵 2")
        state_module.set_miniapp_state_records({})
        state_module.set_inventory_delta_records({})

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_sanitize_miniapp_state_drops_raw_session_and_tokens(self):
        state = miniapp_state.sanitize_miniapp_state({
            "session_id": "hunt-session-secret",
            "games_used": 2,
            "games_limit": 3,
            "nested": {
                "token": "df_SECRET999",
                "hint_text": "第 3 格有宝",
            },
        })
        text = json.dumps(state, ensure_ascii=False)

        self.assertEqual(2, state["games_used"])
        self.assertTrue(state["has_session_id"])
        self.assertIn("session_id_digest", state)
        self.assertNotIn("hunt-session-secret", text)
        self.assertNotIn("df_SECRET999", text)
        self.assertNotIn("token", text)

    def test_record_miniapp_state_stores_one_safe_latest_record(self):
        with patch.object(miniapp_state, "save_state", return_value=True) as save_mock:
            first = miniapp_state.record_miniapp_state(
                1001,
                "cave_treasure",
                {"session_id": "hunt-session-secret", "games_used": 1, "games_limit": 3},
                source="cave_treasure_miniapp",
                source_id="msg:6001:abc",
                outputs=("module_snapshot", "daily_counter", "inventory_delta"),
                replaces_commands=(".洞府",),
                now=1_700_000_000.0,
            )
            duplicate = miniapp_state.record_miniapp_state(
                1001,
                "cave_treasure",
                {"session_id": "hunt-session-secret", "games_used": 1, "games_limit": 3},
                source="cave_treasure_miniapp",
                source_id="msg:6001:abc",
                outputs=("module_snapshot", "daily_counter", "inventory_delta"),
                replaces_commands=(".洞府",),
                now=1_700_000_010.0,
            )

        snapshot = miniapp_state.get_miniapp_state_snapshot(send_as_id=1001, now=1_700_000_020.0)
        row = snapshot["rows"][0]
        text = json.dumps(snapshot, ensure_ascii=False)

        self.assertTrue(first["changed"])
        self.assertFalse(duplicate["changed"])
        self.assertEqual(1, snapshot["record_count"])
        self.assertEqual("1001:cave_treasure", first["record_key"])
        self.assertEqual("cave_treasure", row["game_key"])
        self.assertEqual(["module_snapshot", "daily_counter", "inventory_delta"], row["outputs"])
        self.assertEqual([".洞府"], row["replaces_commands"])
        self.assertTrue(row["state"]["has_session_id"])
        self.assertIn("session_id_digest", row["state"])
        self.assertEqual(20, row["age_sec"])
        self.assertNotIn("hunt-session-secret", text)
        save_mock.assert_called_once()

    def test_replay_cave_capture_records_returns_safe_latest_state(self):
        records = [
            {
                "adapter_key": "cave_treasure",
                "step_key": "start",
                "endpoint": "start",
                "ok": True,
                "response": {
                    "body": {
                        "ok": True,
                        "dwelling": {"hunt": {"used": 2, "limit": 3, "remaining": 0, "actionPoints": 1}},
                    },
                },
            },
            {
                "adapter_key": "cave_treasure",
                "step_key": "action:settle",
                "endpoint": "hunt_settle",
                "ok": True,
                "response": {
                    "body": {
                        "ok": True,
                        "dwelling": {"hunt": {"used": 3, "limit": 3, "remaining": 0, "actionPoints": 8}},
                        "huntRun": {"sessionId": "hunt-session-secret", "status": "settled", "ap": 0, "maxAp": 8},
                        "huntResult": {"grade": "甲等", "score": 80, "loot": [{"name": "灵石", "quantity": 12}]},
                    },
                },
            },
        ]

        replay = miniapp_state.replay_miniapp_capture_records(
            records,
            cave_treasure_miniapp.parse_cave_treasure_state,
            game_key="cave_treasure",
        )
        text = json.dumps(replay, ensure_ascii=False)

        self.assertEqual(2, replay["record_count"])
        self.assertEqual(2, replay["state_count"])
        self.assertEqual(["start", "hunt_settle"], replay["endpoints"])
        self.assertEqual(3, replay["latest_state"]["games_used"])
        self.assertEqual(3, replay["latest_state"]["games_limit"])
        self.assertTrue(replay["latest_state"]["settled"])
        self.assertTrue(replay["latest_state"]["has_session_id"])
        self.assertNotIn("hunt-session-secret", text)

    def test_meta_codec_persists_inventory_delta_and_miniapp_state_records(self):
        state_module.set_inventory_delta_records({"delta": {"identity_id": 1001, "items": {"灵石": 1}}})
        state_module.set_miniapp_state_records({"1001:cave_treasure": {"identity_id": 1001, "game_key": "cave_treasure"}})

        delta_encoded = persistence._META_STATE_CODEC["inventory_delta_records"][1](state_module.get_inventory_delta_records())
        miniapp_encoded = persistence._META_STATE_CODEC["miniapp_state_records"][1](state_module.get_miniapp_state_records())
        state_module.set_inventory_delta_records({})
        state_module.set_miniapp_state_records({})
        persistence._META_STATE_CODEC["inventory_delta_records"][2](delta_encoded)
        persistence._META_STATE_CODEC["miniapp_state_records"][2](miniapp_encoded)

        self.assertEqual({"灵石": 1}, state_module.get_inventory_delta_records()["delta"]["items"])
        self.assertEqual(1001, state_module.get_miniapp_state_records()["1001:cave_treasure"]["identity_id"])


if __name__ == "__main__":
    unittest.main()
