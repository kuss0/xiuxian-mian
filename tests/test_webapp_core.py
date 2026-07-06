import hashlib
import hmac
import json
import tempfile
import unittest
from types import SimpleNamespace
from urllib.parse import quote, urlencode

from model import webapp_core
from model.features import cave_treasure_miniapp, fishing_miniapp, miniapp_registry, stargazer_miniapp, trial_miniapp


class WebAppCoreTests(unittest.TestCase):
    def test_summarize_webapp_url_redacts_start_param_and_init_data(self):
        summary = webapp_core.summarize_webapp_url(
            "https://example.com/app?startapp=stk_SECRET9999#tgWebAppData=query_id%3Dabc%26hash%3Dhidden",
            button_text="打开验证",
            message_text="天道审判 Mini App",
        )

        self.assertEqual("example.com", summary["host"])
        self.assertEqual("tiandao_judgement", summary["game_hint"])
        self.assertTrue(summary["has_start_param"])
        self.assertTrue(summary["has_sensitive_init_data"])
        self.assertEqual("stk", summary["start_param"]["kind"])
        self.assertEqual("9999", summary["start_param"]["suffix"])
        self.assertEqual("startapp", summary["start_param"]["key"])
        self.assertIn("tgWebAppData", summary["sensitive_keys"])
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("stk_SECRET9999", serialized)
        self.assertNotIn("query_id%3Dabc", serialized)
        self.assertNotIn("hidden", serialized)

    def test_summarize_webapp_url_detects_world_boss_without_token(self):
        summary = webapp_core.summarize_webapp_url(
            "https://boss.example/app",
            button_text="进入世界BOSS",
            message_text="真仙试锋已开启",
        )

        self.assertEqual("world_boss", summary["game_hint"])
        self.assertFalse(summary["has_start_param"])
        self.assertFalse(summary["has_sensitive_init_data"])

    def test_launch_request_rejects_untrusted_host_and_bot(self):
        adapter = webapp_core.MiniAppAdapter(
            game_key="fishing",
            label="灵溪垂钓",
            bot_username="fanrenxiuxian_bot",
            start_param_pattern=r"fish_[A-Z0-9]+",
        )

        bad_host = webapp_core.build_miniapp_launch_request(
            adapter,
            "https://evil.example/app?startapp=fish_ABC1",
        )
        bad_bot = webapp_core.build_miniapp_launch_request(
            adapter,
            "https://t.me/evil_bot/app?startapp=fish_ABC1",
        )
        good = webapp_core.build_miniapp_launch_request(
            adapter,
            "https://t.me/fanrenxiuxian_bot/app?startapp=fish_ABC1",
        )

        self.assertFalse(bad_host.allowed)
        self.assertFalse(bad_bot.allowed)
        self.assertTrue(good.allowed)
        args = webapp_core.build_request_webview_args(adapter, good)
        self.assertEqual("fanrenxiuxian_bot", args["bot_username"])
        self.assertEqual("fish_ABC1", args["start_param"])
        self.assertNotIn("fish_ABC1", json.dumps(good.safe_summary(), ensure_ascii=False))

    def test_launch_request_accepts_scheme_less_tme_link(self):
        adapter = webapp_core.MiniAppAdapter(
            game_key="fishing",
            label="灵溪垂钓",
            bot_username="fanrenxiuxian_bot",
            start_param_pattern=r"fish_[A-Z0-9]+",
        )

        request = webapp_core.build_miniapp_launch_request(
            adapter,
            "t.me/fanrenxiuxian_bot/app?startapp=fish_ABC1",
        )

        self.assertTrue(request.allowed)
        self.assertEqual("t.me", request.host)
        self.assertEqual("fanrenxiuxian_bot", request.bot_username)
        self.assertEqual("fish_ABC1", webapp_core.build_request_webview_args(adapter, request)["start_param"])

    def test_init_data_store_keeps_raw_value_only_in_memory(self):
        now = [1000.0]
        store = webapp_core.MiniAppInitDataStore(ttl_sec=60, clock=lambda: now[0])

        session_id = store.put(
            adapter_key="fishing",
            identity_id=123,
            bot_username="fanrenxiuxian_bot",
            host="asc.aiopenai.app",
            start_param="fish_SECRET",
            init_data="query_id=abc&hash=VERY_SECRET&user=42",
            source="test",
        )

        snapshot_text = json.dumps(store.safe_snapshot(), ensure_ascii=False)
        self.assertEqual("query_id=abc&hash=VERY_SECRET&user=42", store.get_init_data(session_id))
        self.assertNotIn("VERY_SECRET", snapshot_text)
        self.assertNotIn("query_id=abc", snapshot_text)
        self.assertNotIn("fish_SECRET", snapshot_text)
        now[0] = 1061.0
        self.assertEqual("", store.get_init_data(session_id))

    def test_extract_and_validate_miniapp_init_data_from_webview_url(self):
        bot_token = "123456:TEST_TOKEN"
        fields = {
            "query_id": "AAEAAAE",
            "user": '{"id":42,"first_name":"Lab"}',
            "auth_date": "1700000000",
        }
        raw_without_hash = urlencode(fields)
        data_check_string = "auth_date=1700000000\nquery_id=AAEAAAE\nuser={\"id\":42,\"first_name\":\"Lab\"}"
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        init_data = f"{raw_without_hash}&hash={expected_hash}"
        webview_url = (
            "https://asc.aiopenai.app/app#tgWebAppData="
            f"{quote(init_data, safe='')}&tgWebAppVersion=7.0"
        )

        extracted = webapp_core.extract_miniapp_init_data_from_url(webview_url)
        result = webapp_core.validate_miniapp_init_data(extracted, bot_token, now=1700000300)
        summary_text = json.dumps(result.safe_summary(), ensure_ascii=False)

        self.assertEqual(init_data, extracted)
        self.assertEqual(expected_hash, webapp_core.sign_miniapp_init_data(init_data, bot_token))
        self.assertTrue(result.ok)
        self.assertEqual(300, int(result.age_sec))
        self.assertEqual(fields["user"], result.fields["user"])
        self.assertIn("auth_date", result.safe_summary()["field_keys"])
        self.assertNotIn(expected_hash, summary_text)
        self.assertNotIn("TEST_TOKEN", summary_text)
        self.assertNotIn(fields["user"], summary_text)

    def test_validate_miniapp_init_data_rejects_tamper_expiry_and_duplicates(self):
        bot_token = "123456:TEST_TOKEN"
        raw_without_hash = "auth_date=1700000000&query_id=AAEAAAE&user=lab"
        valid = f"{raw_without_hash}&hash={webapp_core.sign_miniapp_init_data(raw_without_hash, bot_token)}"
        tampered = valid.replace("query_id=AAEAAAE", "query_id=BAD")

        tampered_result = webapp_core.validate_miniapp_init_data(tampered, bot_token, now=1700000001)
        expired = webapp_core.validate_miniapp_init_data(valid, bot_token, now=1700000901)
        duplicate = webapp_core.validate_miniapp_init_data(
            "auth_date=1700000000&query_id=AAEAAAE&query_id=BB&hash=deadbeef",
            bot_token,
            now=1700000001,
        )

        self.assertFalse(tampered_result.ok)
        self.assertEqual("signature", tampered_result.error_type)
        self.assertFalse(expired.ok)
        self.assertEqual("expired", expired.error_type)
        self.assertFalse(duplicate.ok)
        self.assertEqual("invalid_format", duplicate.error_type)

    def test_extract_miniapp_init_data_from_url_returns_empty_without_fragment_data(self):
        self.assertEqual("", webapp_core.extract_miniapp_init_data_from_url("https://example.com/app"))

    def test_fishing_miniapp_adapter_builds_safe_http_request(self):
        adapter = fishing_miniapp.build_fishing_miniapp_adapter()
        request = fishing_miniapp.build_fishing_miniapp_request(
            "start",
            token="fish_TOKEN1234",
            init_data="query_id=abc&hash=VERY_SECRET&user=42",
            payload={"phase": "waiting"},
            adapter=adapter,
        )

        self.assertEqual("https://asc.aiopenai.app/api/miniapp/xianxia-fishing/start", request["url"])
        self.assertEqual("POST", request["method"])
        self.assertEqual("fish_TOKEN1234", request["payload"]["token"])
        self.assertEqual("waiting", request["payload"]["phase"])
        self.assertIn("initData", request["payload"])
        summary_text = json.dumps(request["safe_summary"], ensure_ascii=False)
        self.assertNotIn("VERY_SECRET", summary_text)
        self.assertNotIn("query_id=abc", summary_text)
        self.assertIn("initData", request["safe_summary"]["secret_keys"])

    def test_miniapp_http_request_safe_summary_does_not_log_header_values(self):
        adapter = fishing_miniapp.build_fishing_miniapp_adapter()
        request = webapp_core.build_miniapp_http_request(
            adapter,
            "result",
            {"token": "fish_TOKEN1234"},
            init_data="query_id=abc&hash=VERY_SECRET",
            headers={"Authorization": "Bearer SECRET", "User-Agent": "lab"},
            timeout_sec=8,
        )

        summary_text = json.dumps(request["safe_summary"], ensure_ascii=False)
        self.assertEqual(["Authorization", "User-Agent"], request["safe_summary"]["header_keys"])
        self.assertEqual(8, request["safe_summary"]["timeout_sec"])
        self.assertNotIn("Bearer SECRET", summary_text)
        self.assertNotIn("VERY_SECRET", summary_text)

    def test_known_miniapp_registry_is_manual_only_by_default(self):
        registry = miniapp_registry.build_known_miniapp_registry()

        self.assertEqual(("cave_treasure", "fishing", "stargazer", "trial", "world_boss"), registry.keys())
        self.assertFalse(registry.require("fishing").default_enabled)
        self.assertFalse(registry.require("trial").default_enabled)
        self.assertFalse(registry.require("cave_treasure").default_enabled)
        self.assertTrue(registry.require("world_boss").manual_only)
        inferred = registry.infer(button_text="进入观星台", message_text="星台已迁入小程序")
        self.assertEqual("stargazer", inferred.game_key)
        inferred_trial = registry.infer(button_text="进入试炼", message_text="【天机试炼台】灵脉点穴")
        self.assertEqual("trial", inferred_trial.game_key)
        inferred_cave = registry.infer(button_text="进入洞府", message_text="前往外府石室寻宝")
        self.assertEqual("cave_treasure", inferred_cave.game_key)
        snapshot_text = json.dumps(registry.safe_snapshot(), ensure_ascii=False)
        self.assertIn("灵溪垂钓", snapshot_text)
        self.assertIn("天机试炼", snapshot_text)
        self.assertIn("洞府寻宝", snapshot_text)
        self.assertNotIn("tgWebAppData", snapshot_text)

    def test_miniapp_registry_duplicate_requires_replace(self):
        registry = webapp_core.MiniAppAdapterRegistry()
        adapter = fishing_miniapp.build_fishing_miniapp_adapter()

        registry.register(adapter)
        with self.assertRaises(ValueError):
            registry.register(adapter)
        registry.register(adapter, replace=True)
        self.assertIs(adapter, registry.require("fishing"))

    def test_fishing_miniapp_flow_plan_documents_lab_sequence(self):
        plan = fishing_miniapp.build_fishing_miniapp_flow_plan()
        summary = plan.safe_summary()

        self.assertTrue(summary["manual_only"])
        self.assertFalse(summary["default_enabled"])
        self.assertEqual(7, summary["step_count"])
        self.assertEqual(
            ["launch", "start_waiting", "wait_bite", "start_bite", "finish", "result", "next"],
            [step["key"] for step in summary["steps"]],
        )
        self.assertEqual("ready", summary["steps"][5]["poll_until_key"])
        summary_text = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("query_id", summary_text)
        self.assertNotIn("hash=", summary_text)

    def test_fishing_proof_is_natural_and_bounded(self):
        proof = fishing_miniapp.build_fishing_proof(
            {
                "mode": "xianxiaFishingV1",
                "challengeId": "c1",
                "minDurationMs": 4200,
                "maxDurationMs": 70000,
            },
            rng=__import__("random").Random(7),
        )

        self.assertEqual("xianxiaFishingV1", proof["mode"])
        self.assertEqual("c1", proof["challengeId"])
        self.assertGreaterEqual(proof["durationMs"], 4500)
        self.assertEqual(100, proof["progress"])
        self.assertGreaterEqual(proof["score"], 85)
        self.assertLessEqual(proof["score"], 98)
        self.assertGreaterEqual(proof["samples"], 180)

    def test_sanitize_webapp_secret_text_redacts_start_tokens(self):
        text = "failed initData=query_id%3Dabc&hash=secret token=fish_SECRET999 startapp=farm_SECRET888 next=df_SECRET777"
        sanitized = webapp_core.sanitize_webapp_secret_text(text)

        self.assertIn("initData=<redacted>", sanitized)
        self.assertIn("token=<redacted>", sanitized)
        self.assertIn("startapp=<redacted>", sanitized)
        self.assertNotIn("fish_SECRET999", sanitized)
        self.assertNotIn("farm_SECRET888", sanitized)
        self.assertNotIn("df_SECRET777", sanitized)
        self.assertNotIn("secret", sanitized.lower())

    def test_safe_miniapp_event_detail_redacts_values_but_keeps_shape(self):
        detail = webapp_core.safe_miniapp_event_detail({
            "token": "fish_SECRET999",
            "initData": "query_id=abc&hash=VERY_SECRET",
            "url": "https://t.me/fanrenxiuxian_bot?startapp=fish_SECRET999",
            "phase": "waiting",
        })
        serialized = json.dumps(detail, ensure_ascii=False)

        self.assertEqual("waiting", detail["phase"])
        self.assertEqual("t.me", detail["url"]["host"])
        self.assertTrue(detail["token"]["present"])
        self.assertNotIn("fish_SECRET999", serialized)
        self.assertNotIn("VERY_SECRET", serialized)

    def test_miniapp_api_url_rejects_unknown_host_and_path(self):
        adapter = fishing_miniapp.build_fishing_miniapp_adapter(api_base_url="https://evil.example")
        with self.assertRaisesRegex(ValueError, "host not allowed"):
            webapp_core.build_miniapp_api_url(adapter, "start")

        adapter = fishing_miniapp.build_fishing_miniapp_adapter()
        with self.assertRaisesRegex(ValueError, "path not allowed"):
            webapp_core.build_miniapp_api_url(adapter, "/api/miniapp/other/start")

    def test_miniapp_api_url_strips_base_path_before_endpoint(self):
        adapter = fishing_miniapp.build_fishing_miniapp_adapter(
            api_base_url="https://asc.aiopenai.app/old/path?ignored=1",
        )

        self.assertEqual(
            "https://asc.aiopenai.app/api/miniapp/xianxia-fishing/start",
            webapp_core.build_miniapp_api_url(adapter, "start"),
        )

    def test_execute_miniapp_http_request_retries_transient_but_not_app_error(self):
        requests = []
        sleeps = []

        def transient_then_ok(request):
            requests.append(request)
            if len(requests) == 1:
                return 503, {"ok": False, "error": "gateway"}
            return 200, {"ok": True, "result": {"score": 94}}

        request = fishing_miniapp.build_fishing_miniapp_request("start", token="fish_T", init_data="init")
        result = webapp_core.execute_miniapp_http_request(
            request,
            transient_then_ok,
            backoff_sec=(0.1, 0.2),
            sleeper=sleeps.append,
        )

        self.assertTrue(result.ok)
        self.assertEqual(2, result.attempts)
        self.assertEqual([0.1], sleeps)

        requests.clear()

        def app_error(request):
            requests.append(request)
            return 200, {"ok": False, "error": "fishing_token_used"}

        result = webapp_core.execute_miniapp_http_request(request, app_error, backoff_sec=(0.1, 0.2))
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual("app", result.error_type)
        self.assertEqual(1, len(requests))

    def test_miniapp_capture_record_redacts_secrets_and_keeps_shape(self):
        request = fishing_miniapp.build_fishing_miniapp_request(
            "result",
            token="fish_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET&user=42",
        )

        record = webapp_core.build_miniapp_capture_record(
            request,
            (
                200,
                {
                    "ok": True,
                    "result": {
                        "message": "完成 token=fish_NEXT888 hash=VERY_SECRET",
                        "fish": {"name": "银须灵鲢", "grade": "灵鱼"},
                    },
                    "nextToken": "fish_NEXT888",
                },
            ),
            step_key="result",
            source="unit token=fish_SECRET999",
        )
        safe = record.safe_record()
        serialized = json.dumps(safe, ensure_ascii=False)

        self.assertTrue(safe["ok"])
        self.assertEqual("fishing", safe["adapter_key"])
        self.assertEqual("result", safe["step_key"])
        self.assertEqual("asc.aiopenai.app", safe["url_host"])
        self.assertIn("payload_shape", safe["request"])
        self.assertEqual("object", safe["response"]["body_shape"]["type"])
        self.assertIn("银须灵鲢", serialized)
        self.assertNotIn("fish_SECRET999", serialized)
        self.assertNotIn("fish_NEXT888", serialized)
        self.assertNotIn("VERY_SECRET", serialized)
        self.assertNotIn("query_id=abc", serialized)

    def test_execute_miniapp_http_request_can_emit_capture_records(self):
        request = fishing_miniapp.build_fishing_miniapp_request(
            "start",
            token="fish_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
        )
        records = []

        result = webapp_core.execute_miniapp_http_request(
            request,
            lambda _request: (200, {"ok": True, "session": {"phase": "waiting", "biteAt": 200, "serverNow": 100}}),
            backoff_sec=(),
            capture_sink=records,
            capture_source="lab-capture",
            step_key="start_waiting",
        )
        serialized = json.dumps(records, ensure_ascii=False)

        self.assertTrue(result.ok)
        self.assertEqual(1, len(records))
        self.assertEqual("start_waiting", records[0]["step_key"])
        self.assertEqual("lab-capture", records[0]["source"])
        self.assertNotIn("fish_SECRET999", serialized)
        self.assertNotIn("VERY_SECRET", serialized)

    def test_miniapp_capture_store_writes_jsonl_without_raw_credentials(self):
        request = fishing_miniapp.build_fishing_miniapp_request(
            "next",
            token="fish_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
        )
        record = webapp_core.build_miniapp_capture_record(
            request,
            (200, {"ok": True, "token": "fish_NEXT888"}),
            step_key="next",
            source="unit",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/miniapp-capture.jsonl"
            store = webapp_core.MiniAppCaptureStore(path)
            stored = store.append(record)
            text = open(path, encoding="utf-8").read()

        self.assertEqual(stored, store.records[0])
        self.assertEqual(1, len(store.records))
        self.assertIn('"step_key": "next"', text)
        self.assertNotIn("fish_SECRET999", text)
        self.assertNotIn("fish_NEXT888", text)
        self.assertNotIn("VERY_SECRET", text)

    def test_flow_plan_validation_and_prepare_mode_are_safe(self):
        adapter = fishing_miniapp.build_fishing_miniapp_adapter()
        plan = fishing_miniapp.build_fishing_miniapp_flow_plan()

        self.assertEqual([], webapp_core.validate_miniapp_flow_plan(plan, adapter))
        result = webapp_core.run_miniapp_flow_plan(
            plan,
            adapter,
            {"token": "fish_SECRET999", "biteAt": 200, "serverNow": 0, "fishingProof": {"x": 1}},
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=None,
        )
        summary_text = json.dumps(result.safe_summary(), ensure_ascii=False)

        self.assertTrue(result.ok)
        self.assertIn("event_count", result.safe_summary())
        self.assertNotIn("fish_SECRET999", summary_text)
        self.assertNotIn("VERY_SECRET", summary_text)

    def test_flow_runner_stops_on_not_ready_poll_key(self):
        adapter = fishing_miniapp.build_fishing_miniapp_adapter()
        plan = webapp_core.MiniAppFlowPlan(
            adapter_key="fishing",
            label="灵溪垂钓",
            steps=(
                webapp_core.MiniAppFlowStep(
                    key="result",
                    endpoint="result",
                    required_payload_keys=("token", "initData"),
                    poll_until_key="ready",
                ),
            ),
        )

        result = webapp_core.run_miniapp_flow_plan(
            plan,
            adapter,
            {"token": "fish_T"},
            init_data="init",
            transport=lambda request: (200, {"ok": True, "ready": False}),
            backoff_sec=(),
        )

        self.assertFalse(result.ok)
        self.assertIn("ready not ready", result.error)

    def test_stargazer_miniapp_launch_and_summary_are_lab_only(self):
        url = "https://t.me/fanrenxiuxian_bot?startapp=farm_SECRET999"
        button = SimpleNamespace(
            text="进入灵圃",
            button=SimpleNamespace(url=url),
        )
        event = SimpleNamespace(message=SimpleNamespace(buttons=[[button]]))
        summary = stargazer_miniapp.summarize_stargazer_entry(
            url,
            button_text="进入灵圃",
            message_text="【星宫 · 观星台】",
        )
        extracted = stargazer_miniapp.extract_stargazer_miniapp_launch(
            event,
            message_text="【星宫 · 观星台】\n@lab 的引星盘已接入宗门灵圃。\n\n点击下方 进入灵圃，牵引星辰与收取星辰精华。",
        )
        request, args = stargazer_miniapp.build_stargazer_launch_args(url)
        plan = stargazer_miniapp.build_stargazer_miniapp_flow_plan()
        summary_text = json.dumps({"summary": summary, "safe": extracted["safe_summary"]}, ensure_ascii=False)

        self.assertEqual("stargazer", summary["game_hint"])
        self.assertEqual("farm", summary["start_param"]["kind"])
        self.assertEqual("farm_SECRET999", extracted["token"])
        self.assertTrue(request.allowed)
        self.assertEqual("farm_SECRET999", args["start_param"])
        self.assertTrue(plan.manual_only)
        self.assertFalse(plan.default_enabled)
        self.assertNotIn("farm_SECRET999", summary_text)

    def test_stargazer_farm_protocol_parse_and_action_requests_are_safe(self):
        adapter = stargazer_miniapp.build_stargazer_miniapp_adapter()
        start_request = stargazer_miniapp.build_stargazer_miniapp_request(
            "start",
            token="farm_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            adapter=adapter,
        )
        farm_state = stargazer_miniapp.parse_stargazer_farm_state({
            "ok": True,
            "domain": {
                "mode": "stars",
                "plots": [
                    {"key": "a", "empty": False, "status": "凝聚中", "remainingText": "1分钟后"},
                    {"key": "b", "empty": False, "status": "元磁紊乱"},
                    {"key": "c", "empty": True, "status": "空闲"},
                ],
            },
        })
        decision = stargazer_miniapp.choose_stargazer_farm_action(farm_state, star_choice="竹灵")
        action_request = stargazer_miniapp.build_stargazer_farm_action_request(
            decision,
            token="farm_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            adapter=adapter,
        )
        serialized = json.dumps(
            {
                "start": start_request["safe_summary"],
                "state": farm_state,
                "decision": decision,
                "action": action_request["safe_summary"],
                "payload": webapp_core.safe_miniapp_event_detail(action_request["payload"]),
            },
            ensure_ascii=False,
        )

        self.assertEqual("https://asc.aiopenai.app/api/miniapp/xianxia-sect-farm/start", start_request["url"])
        self.assertEqual("https://asc.aiopenai.app/api/miniapp/xianxia-sect-farm/action", action_request["url"])
        self.assertEqual(3, farm_state["total_slots"])
        self.assertEqual(1, farm_state["dim_slot_count"])
        self.assertEqual("soothe", decision["action"])
        self.assertEqual("b", decision["plotKey"])
        self.assertEqual("soothe", action_request["payload"]["action"])
        self.assertNotIn("farm_SECRET999", serialized)
        self.assertNotIn("VERY_SECRET", serialized)

    def test_stargazer_farm_action_decision_order(self):
        ready = stargazer_miniapp.parse_stargazer_farm_state({
            "domain": {"mode": "stars", "plots": [
                {"key": "1", "empty": False, "status": "可收集"},
                {"key": "2", "empty": False, "status": "可收集"},
            ]}
        })
        empty = stargazer_miniapp.parse_stargazer_farm_state({
            "domain": {"mode": "stars", "plots": [
                {"key": "1", "empty": False, "status": "凝聚中", "remainingSec": 30},
                {"key": "2", "empty": True, "status": "空闲"},
            ]}
        })
        busy = stargazer_miniapp.parse_stargazer_farm_state({
            "domain": {"mode": "stars", "plots": [
                {"key": "1", "empty": False, "status": "凝聚中", "remainingSec": 30},
                {"key": "2", "empty": False, "status": "凝聚中", "remainingSec": 80},
            ]}
        })

        self.assertEqual("collect", stargazer_miniapp.choose_stargazer_farm_action(ready)["action"])
        pull = stargazer_miniapp.choose_stargazer_farm_action(empty, star_choice="竹灵")
        self.assertEqual({"action": "pull", "plotKey": "2", "reason": "empty_plot", "starName": "竹灵"}, pull)
        wait = stargazer_miniapp.choose_stargazer_farm_action(busy)
        self.assertEqual("wait", wait["action"])
        self.assertEqual(80, wait["wait_sec"])

    def test_stargazer_miniapp_lab_flow_runs_actions_and_captures_safely(self):
        calls = []

        def transport(request):
            payload = dict(request.get("payload") or {})
            calls.append(payload)
            if request["url"].endswith("/start"):
                return {
                    "ok": True,
                    "domain": {"mode": "stars", "plots": [
                        {"key": "a", "empty": False, "status": "元磁紊乱"},
                        {"key": "b", "empty": True, "status": "空闲"},
                    ]},
                }
            if payload.get("action") == "soothe":
                return {
                    "ok": True,
                    "domain": {"mode": "stars", "plots": [
                        {"key": "a", "empty": False, "status": "可收集"},
                        {"key": "b", "empty": False, "status": "可收集"},
                    ]},
                }
            if payload.get("action") == "collect":
                return {
                    "ok": True,
                    "message": "收集完成：获得【星辰精华】x2",
                    "domain": {"mode": "stars", "plots": [
                        {"key": "a", "empty": True, "status": "空闲"},
                        {"key": "b", "empty": True, "status": "空闲"},
                    ]},
                }
            if payload.get("action") == "pull":
                return {
                    "ok": True,
                    "domain": {"mode": "stars", "plots": [
                        {"key": "a", "empty": False, "status": "凝聚中", "remainingSec": 60},
                        {"key": "b", "empty": False, "status": "凝聚中", "remainingSec": 90},
                    ]},
                }
            return {"ok": False, "error": "unexpected"}

        capture = webapp_core.MiniAppCaptureStore()
        result = stargazer_miniapp.run_stargazer_miniapp_lab_flow(
            token="farm_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            star_choice="竹灵",
            transport=transport,
            capture_sink=capture,
            capture_source="unit-test",
        )
        serialized = json.dumps({"result": result, "capture": capture.records}, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("wait", result["status"])
        self.assertEqual({"soothe": 1, "collect": 1, "pull": 1}, result["data"]["action_counts"])
        self.assertEqual({"星辰精华": 2}, result["data"]["item_deltas"])
        self.assertEqual(["soothe", "collect", "pull"], [call.get("action") for call in calls[1:]])
        self.assertEqual("竹灵", calls[-1].get("starName"))
        self.assertNotIn("farm_SECRET999", serialized)
        self.assertNotIn("VERY_SECRET", serialized)

    def test_trial_miniapp_request_launch_and_flow_plan_are_lab_only(self):
        url = "https://t.me/fanrenxiuxian_bot?startapp=trial_SECRET999"
        button = SimpleNamespace(
            text="进入天机试炼",
            button=SimpleNamespace(url=url),
        )
        event = SimpleNamespace(message=SimpleNamespace(buttons=[[button]]))
        adapter = trial_miniapp.build_trial_miniapp_adapter()
        request = trial_miniapp.build_trial_miniapp_request(
            "start",
            token="trial_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            adapter=adapter,
        )
        summary = trial_miniapp.summarize_trial_entry(
            url,
            button_text="进入天机试炼",
            message_text="【天机试炼台】灵脉点穴",
        )
        extracted = trial_miniapp.extract_trial_miniapp_launch(event, message_text="【天机试炼台】灵脉点穴")
        launch, args = trial_miniapp.build_trial_launch_args(url)
        plan = trial_miniapp.build_trial_miniapp_flow_plan()
        serialized = json.dumps({"request": request["safe_summary"], "summary": summary, "safe": extracted["safe_summary"]}, ensure_ascii=False)

        self.assertEqual("https://asc.aiopenai.app/api/miniapp/xianxia-trial/start", request["url"])
        self.assertEqual("trial_SECRET999", request["payload"]["token"])
        self.assertIn("initData", request["payload"])
        self.assertEqual("trial", summary["game_hint"])
        self.assertEqual("trial_SECRET999", extracted["token"])
        self.assertTrue(launch.allowed)
        self.assertEqual("trial_SECRET999", args["start_param"])
        self.assertTrue(plan.manual_only)
        self.assertFalse(plan.default_enabled)
        self.assertEqual(["launch", "start", "solve", "finish", "next"], [step.key for step in plan.steps])
        self.assertNotIn("trial_SECRET999", serialized)
        self.assertNotIn("VERY_SECRET", serialized)

    def test_trial_proof_uses_sequence_and_avoids_traps(self):
        proof = trial_miniapp.build_trial_proof(
            {
                "mode": "tianjiMeridianV1",
                "challengeId": "trial-c1",
                "sequence": ["p1", "p2", "p3"],
                "trapIds": ["p2"],
                "points": [
                    {"id": "p1", "x": 12, "y": 34},
                    {"id": "p2", "x": 56, "y": 78},
                    {"id": "p3", "x": 90, "y": 10},
                ],
                "minDurationMs": 3200,
                "maxDurationMs": 90000,
            },
            rng=__import__("random").Random(3),
        )

        self.assertEqual("tianjiMeridianV1", proof["mode"])
        self.assertEqual("trial-c1", proof["challengeId"])
        self.assertEqual(["p1", "p2", "p3"], proof["sequence"])
        self.assertEqual(["p1", "p3"], [tap["id"] for tap in proof["taps"]])
        self.assertEqual(1, proof["trapHits"])
        self.assertGreaterEqual(proof["durationMs"], 5000)
        self.assertLessEqual(proof["durationMs"], 90000)

    def test_trial_lab_flow_solves_and_finishes_without_secret_leak(self):
        calls = []
        sleeps = []
        capture = webapp_core.MiniAppCaptureStore()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append((endpoint, dict(request["payload"])))
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "trial": {"title": "灵脉点穴"},
                    "challenge": {
                        "mode": "tianjiMeridianV1",
                        "challengeId": "trial-c1",
                        "sequence": [1, 2],
                        "trapIds": [9],
                        "points": [{"id": "1", "x": 10, "y": 20}, {"id": "2", "x": 30, "y": 40}],
                        "minDurationMs": 20,
                        "maxDurationMs": 90000,
                    },
                }
            if endpoint == "finish":
                self.assertIn("trialProof", request["payload"])
                self.assertEqual("trial_SECRET999", request["payload"]["token"])
                return 200, {"ok": True, "result": {"expGain": 20, "traceGain": 1}}
            return 404, {"ok": False, "error": "unexpected"}

        result = trial_miniapp.run_trial_miniapp_lab_flow(
            token="trial_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=__import__("random").Random(5),
            sleeper=sleeps.append,
            capture_sink=capture,
            capture_source="unit-test",
        )
        summary_text = json.dumps(result, ensure_ascii=False)
        capture_text = json.dumps(capture.records, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("settled", result["status"])
        self.assertEqual({"expGain": 20, "traceGain": 1}, result["data"])
        self.assertEqual(["start", "finish"], [call[0] for call in calls])
        self.assertEqual(["start", "finish"], [item["step_key"] for item in capture.records])
        self.assertEqual(1, len(sleeps))
        self.assertNotIn("VERY_SECRET", summary_text)
        self.assertNotIn("trial_SECRET999", summary_text)
        self.assertNotIn("VERY_SECRET", capture_text)
        self.assertNotIn("trial_SECRET999", capture_text)

    def test_trial_lab_flow_classifies_daily_limit(self):
        result = trial_miniapp.run_trial_miniapp_lab_flow(
            token="trial_SECRET999",
            init_data="init",
            transport=lambda _request: (200, {"ok": False, "error": "daily_limit"}),
        )

        self.assertFalse(result["ok"])
        self.assertEqual("daily_limit", result["status"])

    def test_trial_lab_loop_uses_next_until_daily_limit(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            token = request["payload"]["token"]
            calls.append((endpoint, token))
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "trial": {"title": "灵脉点穴"},
                    "challenge": {
                        "mode": "tianjiMeridianV1",
                        "challengeId": f"{token}-c1",
                        "sequence": [1],
                        "trapIds": [],
                        "points": [{"id": "1", "x": 10, "y": 20}],
                        "minDurationMs": 20,
                        "maxDurationMs": 90000,
                    },
                }
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"traceGain": 1}}
            if endpoint == "next" and token == "trial_SECRET999":
                return 200, {"ok": True, "token": "trial_NEXT888"}
            if endpoint == "next":
                return 200, {"ok": False, "error": "daily_limit"}
            return 404, {"ok": False, "error": "unexpected"}

        result = trial_miniapp.run_trial_miniapp_loop_lab_flow(
            token="trial_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=__import__("random").Random(5),
            sleeper=lambda _delay: None,
            max_rounds=3,
        )
        summary_text = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("settled", result["status"])
        self.assertEqual(2, result["data"]["settled_count"])
        self.assertEqual(
            ["start", "finish", "next", "start", "finish", "next"],
            [endpoint for endpoint, _token in calls],
        )
        self.assertNotIn("trial_SECRET999", summary_text)
        self.assertNotIn("trial_NEXT888", summary_text)
        self.assertNotIn("VERY_SECRET", summary_text)

    def test_cave_treasure_entry_state_and_flow_are_lab_only(self):
        url = "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"
        button = SimpleNamespace(
            text="进入洞府",
            button=SimpleNamespace(url=url),
        )
        event = SimpleNamespace(message=SimpleNamespace(buttons=[[button]]))
        adapter = cave_treasure_miniapp.build_cave_treasure_miniapp_adapter()
        request = cave_treasure_miniapp.build_cave_treasure_miniapp_request(
            "start",
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            adapter=adapter,
        )
        summary = cave_treasure_miniapp.summarize_cave_treasure_entry(
            url,
            button_text="进入洞府",
            message_text="【洞府】点击下方 进入洞府，或前往外府石室寻宝。",
        )
        extracted = cave_treasure_miniapp.extract_cave_treasure_miniapp_launch(
            event,
            message_text="【洞府】点击下方 进入洞府，或前往外府石室寻宝。",
        )
        launch, args = cave_treasure_miniapp.build_cave_treasure_launch_args(url)
        plan = cave_treasure_miniapp.build_cave_treasure_miniapp_flow_plan()
        serialized = json.dumps({"request": request["safe_summary"], "summary": summary, "safe": extracted["safe_summary"]}, ensure_ascii=False)

        self.assertEqual("https://asc.aiopenai.app/api/miniapp/xianxia-dongfu/start", request["url"])
        self.assertEqual("df_SECRET999", request["payload"]["token"])
        self.assertEqual("cave_treasure", summary["game_hint"])
        self.assertEqual("df_SECRET999", extracted["token"])
        self.assertTrue(launch.allowed)
        self.assertEqual("df_SECRET999", args["start_param"])
        self.assertTrue(plan.manual_only)
        self.assertFalse(plan.default_enabled)
        self.assertEqual(["launch", "start", "decide_action", "action"], [step.key for step in plan.steps])
        self.assertNotIn("df_SECRET999", serialized)
        self.assertNotIn("VERY_SECRET", serialized)

    def test_cave_treasure_state_keeps_sense_remaining_and_games_used_separate(self):
        parsed = cave_treasure_miniapp.parse_cave_treasure_state({
            "ok": True,
            "data": {
                "tab": "寻宝",
                "inRound": True,
                "sense": "神识 8/8",
                "games": "游戏 0/3",
                "hint": "石室里第3个小人脚下有微光。",
                "targetCount": 7,
            },
        })
        decision = cave_treasure_miniapp.choose_cave_treasure_action(parsed, rng=__import__("random").Random(2))

        self.assertEqual(8, parsed["action_remaining"])
        self.assertEqual(8, parsed["action_limit"])
        self.assertEqual(0, parsed["games_used"])
        self.assertEqual(3, parsed["games_limit"])
        self.assertEqual("search", decision["action"])
        self.assertEqual(3, decision["targetIndex"])
        self.assertEqual("hint_target", decision["reason"])

        exhausted = dict(parsed, games_used=3, games_limit=3)
        self.assertEqual("done", cave_treasure_miniapp.choose_cave_treasure_action(exhausted)["action"])
        found = dict(parsed, treasure_found=True, can_bonus_retry=True)
        self.assertEqual("bonus_retry", cave_treasure_miniapp.choose_cave_treasure_action(found)["action"])
        no_hit_spent = dict(parsed, action_remaining=0, treasure_found=False, settled=False)
        self.assertEqual("settle", cave_treasure_miniapp.choose_cave_treasure_action(no_hit_spent)["action"])

    def test_cave_treasure_uses_reported_limits_instead_of_sample_counts(self):
        parsed = cave_treasure_miniapp.parse_cave_treasure_state({
            "ok": True,
            "data": {
                "tab": "寻宝",
                "inRound": True,
                "text": "寻宝中\n神识：9/11\n游戏：1/4\n石室内没有明显提示。",
            },
        })
        decision = cave_treasure_miniapp.choose_cave_treasure_action(
            parsed,
            rng=__import__("random").Random(7),
        )

        self.assertEqual(9, parsed["action_remaining"])
        self.assertEqual(11, parsed["action_limit"])
        self.assertEqual(1, parsed["games_used"])
        self.assertEqual(4, parsed["games_limit"])
        self.assertEqual(11, parsed["target_count"])
        self.assertEqual("search", decision["action"])
        self.assertGreaterEqual(decision["targetIndex"], 1)
        self.assertLessEqual(decision["targetIndex"], 11)
        self.assertEqual("random_target", decision["reason"])
        self.assertEqual("settle", cave_treasure_miniapp.choose_cave_treasure_action(
            dict(parsed, action_remaining=0, games_used=1, games_limit=4, settled=False)
        )["action"])
        self.assertEqual("done", cave_treasure_miniapp.choose_cave_treasure_action(
            dict(parsed, action_remaining=0, games_used=4, games_limit=4, settled=True)
        )["action"])

    def test_cave_treasure_lab_flow_uses_page_state_until_daily_done_without_secret_leak(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            payload = dict(request["payload"])
            calls.append((endpoint, payload.get("action"), payload.get("targetIndex")))
            if endpoint == "start":
                return 200, {"ok": True, "data": {"tab": "洞府", "games": "游戏 0/3", "sense": "神识 8/8"}}
            if endpoint == "action" and payload.get("action") == "switch_treasure":
                return 200, {"ok": True, "data": {"tab": "寻宝", "games": "游戏 0/3", "sense": "神识 8/8"}}
            if endpoint == "action" and payload.get("action") == "enter":
                return 200, {
                    "ok": True,
                    "data": {
                        "tab": "寻宝",
                        "inRound": True,
                        "games": "游戏 0/3",
                        "sense": "神识 1/8",
                        "hint": "第1个小人处灵气浮动。",
                        "targetCount": 7,
                    },
                }
            if endpoint == "action" and payload.get("action") == "search":
                self.assertEqual(1, payload.get("targetIndex"))
                return 200, {
                    "ok": True,
                    "data": {
                        "tab": "寻宝",
                        "inRound": True,
                        "games": "游戏 0/3",
                        "sense": "神识 0/8",
                        "found": True,
                        "text": "寻得洞府宝光，可见好就收。",
                    },
                }
            if endpoint == "action" and payload.get("action") == "settle":
                return 200, {
                    "ok": True,
                    "data": {
                        "tab": "寻宝",
                        "games": "游戏 3/3",
                        "sense": "神识 0/8",
                        "text": "见好就收，今日寻宝已结算。",
                    },
                }
            return 404, {"ok": False, "error": "unexpected"}

        result = cave_treasure_miniapp.run_cave_treasure_miniapp_lab_flow(
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=__import__("random").Random(4),
        )
        summary_text = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("daily_limit", result["status"])
        self.assertEqual(
            [("start", None, None), ("action", "switch_treasure", None), ("action", "enter", None), ("action", "search", 1), ("action", "settle", None)],
            calls,
        )
        self.assertNotIn("df_SECRET999", summary_text)
        self.assertNotIn("VERY_SECRET", summary_text)

    def test_cave_treasure_lab_flow_bonus_retry_then_settles(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            payload = dict(request["payload"])
            calls.append((endpoint, payload.get("action"), payload.get("targetIndex")))
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "data": {
                        "tab": "寻宝",
                        "inRound": True,
                        "games": "游戏 1/4",
                        "sense": "神识 1/9",
                        "hint": "第2个小人处宝光闪动。",
                        "targetCount": 9,
                    },
                }
            if endpoint == "action" and payload.get("action") == "search":
                self.assertEqual(2, payload.get("targetIndex"))
                return 200, {
                    "ok": True,
                    "data": {
                        "tab": "寻宝",
                        "inRound": True,
                        "games": "游戏 1/4",
                        "sense": "神识 0/9",
                        "found": True,
                        "text": "寻得宝物，可再来一次，也可见好就收。",
                    },
                }
            if endpoint == "action" and payload.get("action") == "bonus_retry":
                return 200, {
                    "ok": True,
                    "data": {
                        "tab": "寻宝",
                        "inRound": True,
                        "games": "游戏 1/4",
                        "sense": "神识 0/9",
                        "found": True,
                        "text": "宝光已稳，请见好就收。",
                    },
                }
            if endpoint == "action" and payload.get("action") == "settle":
                return 200, {
                    "ok": True,
                    "data": {
                        "tab": "寻宝",
                        "games": "游戏 4/4",
                        "sense": "神识 0/9",
                        "settled": True,
                        "text": "见好就收，今日寻宝已结算。",
                    },
                }
            return 404, {"ok": False, "error": "unexpected"}

        result = cave_treasure_miniapp.run_cave_treasure_miniapp_lab_flow(
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=__import__("random").Random(9),
        )
        summary_text = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("daily_limit", result["status"])
        self.assertEqual(
            [("start", None, None), ("action", "search", 2), ("action", "bonus_retry", None), ("action", "settle", None)],
            calls,
        )
        self.assertNotIn("df_SECRET999", summary_text)
        self.assertNotIn("VERY_SECRET", summary_text)

    def test_known_flow_plans_include_stargazer_without_production_enable(self):
        plans = miniapp_registry.build_known_miniapp_flow_plans()

        self.assertEqual({"cave_treasure", "fishing", "stargazer", "trial"}, set(plans))
        self.assertTrue(plans["stargazer"].manual_only)
        self.assertFalse(plans["stargazer"].default_enabled)
        self.assertTrue(plans["trial"].manual_only)
        self.assertFalse(plans["trial"].default_enabled)
        self.assertTrue(plans["cave_treasure"].manual_only)
        self.assertFalse(plans["cave_treasure"].default_enabled)

    def test_fishing_proof_is_formula_consistent_over_samples(self):
        rng = __import__("random").Random(11)
        for _ in range(100):
            proof = fishing_miniapp.build_fishing_proof(
                {"challengeId": "c1", "minDurationMs": 4200, "maxDurationMs": 70000},
                rng=rng,
                score_low=92,
                score_high=97,
            )
            score = fishing_miniapp._score_from_proof(
                proof["progress"],
                proof["stability"],
                proof["dangerMs"],
                proof["slackMs"],
            )
            self.assertEqual(score, proof["score"], proof)
            self.assertGreaterEqual(proof["score"], 92)
            self.assertLessEqual(proof["score"], 97)

    def test_fishing_proof_caps_hostile_duration(self):
        proof = fishing_miniapp.build_fishing_proof(
            {"challengeId": "c1", "minDurationMs": 86_400_000, "maxDurationMs": 0},
            rng=__import__("random").Random(3),
        )

        self.assertLessEqual(proof["durationMs"], fishing_miniapp.FISHING_MINIAPP_PROOF_DURATION_CAP_MS)
        self.assertEqual(
            fishing_miniapp._score_from_proof(
                proof["progress"],
                proof["stability"],
                proof["dangerMs"],
                proof["slackMs"],
            ),
            proof["score"],
        )

    def test_fishing_lab_flow_waits_finishes_and_polls_ready(self):
        calls = []
        sleeps = []
        capture = webapp_core.MiniAppCaptureStore()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append((endpoint, dict(request["payload"])))
            if endpoint == "start" and len([item for item in calls if item[0] == "start"]) == 1:
                return 200, {"ok": True, "session": {"phase": "waiting", "biteAt": 200, "serverNow": 0}}
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "session": {"phase": "bite", "biteAt": 200, "serverNow": 210},
                    "challenge": {"challengeId": "c1", "minDurationMs": 20, "maxDurationMs": 70000},
                }
            if endpoint == "finish":
                self.assertIn("fishingProof", request["payload"])
                self.assertEqual("fish_SECRET999", request["payload"]["token"])
                return 200, {"ok": True, "result": {"score": 94, "grade": "甲等"}}
            if endpoint == "result":
                return 200, {"ok": True, "ready": True, "result": {"score": 94, "grade": "甲等"}}
            return 404, {"ok": False, "error": "unexpected"}

        result = fishing_miniapp.run_fishing_miniapp_lab_flow(
            token="fish_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=__import__("random").Random(5),
            sleeper=sleeps.append,
            capture_sink=capture,
            capture_source="unit-test",
        )
        summary_text = json.dumps(result, ensure_ascii=False)
        capture_text = json.dumps(capture.records, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("settled", result["status"])
        self.assertEqual({"score": 94, "grade": "甲等"}, result["data"])
        self.assertEqual([0.2], sleeps)
        self.assertEqual(["start", "start", "finish", "result"], [call[0] for call in calls])
        self.assertEqual(["start_waiting", "start_bite", "finish", "result"], [item["step_key"] for item in capture.records])
        self.assertNotIn("VERY_SECRET", summary_text)
        self.assertNotIn("VERY_SECRET", capture_text)
        self.assertNotIn("fish_SECRET999", capture_text)
        self.assertIn("payload_shape", capture_text)

    def test_fishing_lab_flow_far_bite_is_not_ready_without_finish(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {"ok": True, "session": {"phase": "waiting", "biteAt": 60000, "serverNow": 0}}

        result = fishing_miniapp.run_fishing_miniapp_lab_flow(
            token="fish_T",
            init_data="init",
            transport=transport,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("not_ready", result["status"])
        self.assertEqual(["start"], calls)

    def test_fishing_lab_flow_app_error_is_classified_without_retry(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {"ok": False, "error": "fishing_token_channel_unbound"}

        result = fishing_miniapp.run_fishing_miniapp_lab_flow(
            token="fish_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
        )
        summary_text = json.dumps(result, ensure_ascii=False)

        self.assertFalse(result["ok"])
        self.assertEqual("unbindable", result["status"])
        self.assertEqual(["start"], calls)
        self.assertNotIn("fish_SECRET999", summary_text)
        self.assertNotIn("VERY_SECRET", summary_text)

    def test_fishing_loop_flow_uses_next_token_without_new_chat_command(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append((endpoint, request["payload"].get("token")))
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "session": {"phase": "bite", "serverNow": 0},
                    "challenge": {"challengeId": f"c{len(calls)}", "minDurationMs": 20, "maxDurationMs": 70000},
                }
            if endpoint == "finish":
                token = request["payload"].get("token")
                fish = "银须灵鲢" if token == "fish_FIRST" else "赤尾火鲤"
                return 200, {
                    "ok": True,
                    "result": {
                        "score": 94,
                        "details": {
                            "fish": {"name": fish, "grade": "灵鱼", "weight": 2.88},
                            "rewards": [{"name": "幸运符", "qty": 1}],
                        },
                    },
                }
            if endpoint == "result":
                token = request["payload"].get("token")
                fish = "银须灵鲢" if token == "fish_FIRST" else "赤尾火鲤"
                return 200, {
                    "ok": True,
                    "ready": True,
                    "result": {
                        "score": 94,
                        "details": {
                            "fish": {"name": fish, "grade": "灵鱼", "weight": 2.88},
                            "rewards": [{"name": "幸运符", "qty": 1}],
                        },
                    },
                }
            if endpoint == "next":
                return 200, {"ok": True, "token": "fish_NEXT"}
            return 404, {"ok": False, "error": "unexpected"}

        result = fishing_miniapp.run_fishing_miniapp_loop_lab_flow(
            token="fish_FIRST",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            max_rounds=2,
            rng=__import__("random").Random(5),
            sleeper=lambda _sec: None,
            rest_range_sec=(0, 0),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(2, result["data"]["settled_count"])
        self.assertEqual(["银须灵鲢", "赤尾火鲤"], [item["fish"] for item in result["data"]["catches"]])
        self.assertEqual("幸运符", result["data"]["catches"][0]["rewards"][0]["name"])
        self.assertIn(("next", "fish_FIRST"), calls)
        self.assertIn(("start", "fish_NEXT"), calls)
        self.assertEqual(["start", "finish", "result", "next", "start", "finish", "result"], [call[0] for call in calls])

    def test_fishing_loop_flow_stops_when_next_button_has_no_token(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "session": {"phase": "bite", "serverNow": 0},
                    "challenge": {"challengeId": "c1", "minDurationMs": 20, "maxDurationMs": 70000},
                }
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"score": 94}}
            if endpoint == "result":
                return 200, {"ok": True, "ready": True, "result": {"score": 94}}
            if endpoint == "next":
                return 200, {"ok": True, "ready": False}
            return 404, {"ok": False, "error": "unexpected"}

        result = fishing_miniapp.run_fishing_miniapp_loop_lab_flow(
            token="fish_FIRST",
            init_data="init",
            transport=transport,
            max_rounds=2,
            rng=__import__("random").Random(5),
            sleeper=lambda _sec: None,
            rest_range_sec=(0, 0),
        )

        self.assertTrue(result["ok"])
        self.assertEqual("next_unavailable", result["status"])
        self.assertEqual(1, result["data"]["settled_count"])
        self.assertEqual(["start", "finish", "result", "next"], calls)


if __name__ == "__main__":
    unittest.main()
