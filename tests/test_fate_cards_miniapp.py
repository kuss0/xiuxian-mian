import json
import unittest

from model.features import cave_treasure_miniapp, fate_cards_miniapp
from model.webapp_core import sanitize_webapp_secret_text, summarize_webapp_url, validate_miniapp_flow_plan


class FateCardsMiniAppTests(unittest.TestCase):
    def test_adapter_and_plan_are_manual_only(self):
        adapter = fate_cards_miniapp.build_fate_cards_miniapp_adapter()
        plan = fate_cards_miniapp.build_fate_cards_miniapp_flow_plan()

        self.assertEqual([], validate_miniapp_flow_plan(plan, adapter))
        self.assertTrue(adapter.manual_only)
        self.assertFalse(adapter.default_enabled)
        self.assertEqual(
            {"start", "draw", "interpret", "choose", "settle"},
            set(adapter.safe_summary()["endpoint_keys"]),
        )
        self.assertEqual(["launch", "start"], [step.key for step in plan.steps])
        self.assertEqual("single_identity_public_entry_probe", plan.read_scope)

    def test_hyphen_token_summary_and_game_hint_are_safe(self):
        summary = summarize_webapp_url(
            "https://t.me/hantianzun21_bot?startapp=fate-SECRET999",
            button_text="进入天机命脉",
            message_text="外府命脉入口",
        )

        self.assertEqual("fate_cards", summary["game_hint"])
        self.assertEqual("fate", summary["start_param"]["kind"])
        self.assertEqual("T999", summary["start_param"]["suffix"])
        self.assertNotIn("fate-SECRET999", json.dumps(summary, ensure_ascii=False))
        self.assertNotIn("SECRET999", sanitize_webapp_secret_text("failed fate-SECRET999"))

    def test_mutating_request_requires_explicit_lab_opt_in(self):
        with self.assertRaisesRegex(ValueError, "显式 Lab 授权"):
            fate_cards_miniapp.build_fate_cards_miniapp_request(
                "draw",
                token="fate_SECRET",
                init_data="query_id=secret",
                payload={"questionKey": "cultivation"},
            )

        request = fate_cards_miniapp.build_fate_cards_miniapp_request(
            "draw",
            token="fate_SECRET",
            init_data="query_id=secret",
            payload={"questionKey": "cultivation"},
            allow_mutation=True,
        )
        self.assertTrue(request["url"].endswith("/draw"))
        self.assertEqual("cultivation", request["payload"]["questionKey"])

    def test_start_parser_uses_frontend_question_default_but_not_choice_guess(self):
        state = fate_cards_miniapp.parse_fate_cards_state({
            "ok": True,
            "challengeDate": "2026-07-28",
            "traceBalance": 12,
            "questions": [
                {"key": "cultivation", "name": "修行", "prompt": "今日修行应进还是守？"},
                {"key": "opportunity", "name": "机缘"},
                {"key": "wealth", "name": "财运"},
                {"key": "relationship", "name": "因缘"},
                {"key": "sect", "name": "宗门"},
                {"key": "calamity", "name": "劫数"},
            ],
            "choices": [
                {"key": "accept", "name": "顺势承命"},
                {"key": "defy", "name": "逆势改命"},
                {"key": "hide", "name": "藏锋避劫"},
            ],
            "hasDrawn": False,
            "record": None,
            "spread": ["cause", "present", "outcome"],
        })

        self.assertEqual("cultivation", state["default_question_key"])
        self.assertEqual("", state["default_choice_key"])
        self.assertEqual(6, state["question_count"])
        self.assertEqual(3, state["choice_count"])
        self.assertEqual("manual_draw", state["decision"]["action"])
        self.assertFalse(state["decision"]["safe_to_auto"])

    def test_drawn_record_blocks_when_server_has_no_default_choice(self):
        state = fate_cards_miniapp.parse_fate_cards_state({
            "ok": True,
            "hasDrawn": True,
            "questions": [{"key": "cultivation"}],
            "choices": [{"key": "accept"}, {"key": "hide"}],
            "record": {
                "questionKey": "cultivation",
                "cards": [{"title": "掌天瓶"}, {"title": "天机阁"}, {"title": "飞升台"}],
            },
        })

        self.assertEqual("blocked", state["decision"]["action"])
        self.assertEqual("default_choice_unknown", state["decision"]["reason"])

    def test_explicit_default_choice_and_settle_state_are_preserved(self):
        state = fate_cards_miniapp.parse_fate_cards_state({
            "ok": True,
            "hasDrawn": True,
            "choices": [{"key": "accept", "isDefault": True}],
            "record": {"cards": [{}, {}, {}]},
        })
        self.assertEqual("accept", state["default_choice_key"])
        self.assertEqual("manual_choose", state["decision"]["action"])

        settled = fate_cards_miniapp.parse_fate_cards_state({
            "ok": True,
            "hasDrawn": True,
            "record": {
                "questionKey": "cultivation",
                "choiceKey": "accept",
                "cards": [{}, {}, {}],
                "quest": {
                    "title": "承命·积修为",
                    "status": "active",
                    "progress": 30,
                    "target": 30,
                    "canSettle": True,
                },
            },
        })
        self.assertEqual("manual_settle", settled["decision"]["action"])

    def test_read_only_probe_never_calls_mutation_endpoints_or_leaks_secrets(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {
                "ok": True,
                "challengeDate": "2026-07-28",
                "traceBalance": 9,
                "questions": [{"key": "cultivation", "name": "修行"}],
                "choices": [{"key": "accept", "name": "顺势承命"}],
                "hasDrawn": False,
            }

        result = fate_cards_miniapp.run_fate_cards_start_probe(
            token="fate_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            sleeper=lambda _delay: None,
        )

        self.assertTrue(result["ok"])
        self.assertEqual("observed", result["status"])
        self.assertEqual(["start"], calls)
        safe = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("fate_SECRET999", safe)
        self.assertNotIn("VERY_SECRET", safe)

    def test_cave_dynamic_action_and_launch_are_recognized(self):
        request = cave_treasure_miniapp.build_cave_external_action_request(
            "fate_cards",
            token="df_SECRET",
            init_data="query_id=secret",
            player_id=1001,
        )
        self.assertEqual("fate_cards", request["payload"]["action"])

        payload = {
            "account": {
                "externalApps": {
                    "groups": [{
                        "apps": [{
                            "key": "fate_cards",
                            "title": "天机命脉",
                            "action": "fate_cards",
                            "available": True,
                        }],
                    }],
                },
            },
            "launch": {
                "url": "https://t.me/hantianzun21_bot?startapp=fate_SAMPLE123",
            },
        }
        app = fate_cards_miniapp.find_fate_cards_external_app(payload)
        launch = fate_cards_miniapp.extract_fate_cards_launch_from_payload(payload)
        self.assertEqual("fate_cards", app["action"])
        self.assertTrue(app["available"])
        self.assertEqual("fate_SAMPLE123", launch["token"])
        self.assertNotIn("fate_SAMPLE123", json.dumps(launch["safe_summary"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
