import json
import random
import threading
import unittest

from model.features import world_boss_miniapp
from model.webapp_core import MiniAppCaptureStore


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


class WorldBossMiniAppTests(unittest.TestCase):
    def test_build_websocket_url_uses_same_origin_ticket_fallback(self):
        urls = world_boss_miniapp.build_world_boss_websocket_urls({"ticket": "ws-secret"})

        self.assertEqual(1, len(urls))
        self.assertEqual(
            "wss://asc.aiopenai.app/ws/miniapp/xianxia-world-boss/state?ticket=ws-secret",
            urls[0],
        )

    def test_build_websocket_url_accepts_nested_same_origin_path(self):
        urls = world_boss_miniapp.build_world_boss_websocket_urls({
            "websocket": {
                "ticket": "ws-secret",
                "wsPath": "/ws/miniapp/xianxia-world-boss/state?format=compact&ticket=stale",
            },
        })

        self.assertEqual(
            "wss://asc.aiopenai.app/ws/miniapp/xianxia-world-boss/state?format=compact&ticket=ws-secret",
            urls[0],
        )
        self.assertNotIn("stale", urls[0])

    def test_build_websocket_url_rejects_foreign_or_wrong_service_urls(self):
        with self.assertRaisesRegex(ValueError, "ticket invalid"):
            world_boss_miniapp.build_world_boss_websocket_urls({
                "ticket": "ws-secret",
                "wsUrl": "wss://example.com/ws/miniapp/xianxia-world-boss/state",
            })
        with self.assertRaisesRegex(ValueError, "ticket invalid"):
            world_boss_miniapp.build_world_boss_websocket_urls({
                "ticket": "ws-secret",
                "wsUrl": "wss://asc.aiopenai.app/ws/miniapp/unrelated/state",
            })

    def test_decode_websocket_ping_and_nested_state(self):
        self.assertEqual(
            {"type": "ping"},
            world_boss_miniapp.decode_world_boss_websocket_message('{"type":"heartbeat"}'),
        )
        decoded = world_boss_miniapp.decode_world_boss_websocket_message(json.dumps({
            "type": "snapshot",
            "data": {
                "state": {
                    "serverTimeMs": 123456,
                    "boss": {"eventStatus": "active", "roomStatus": "battle", "phase": 2},
                },
            },
        }))

        self.assertEqual("snapshot", decoded["type"])
        self.assertEqual("battle", decoded["boss"]["roomStatus"])
        self.assertEqual(123456, decoded["server_time_ms"])

    def test_decode_websocket_ignores_invalid_or_unrelated_payload(self):
        self.assertEqual({}, world_boss_miniapp.decode_world_boss_websocket_message("not-json"))
        self.assertEqual(
            {"type": "notice"},
            world_boss_miniapp.decode_world_boss_websocket_message('{"type":"notice","data":{"x":1}}'),
        )

    def test_expires_in_caps_local_timeline_before_late_windows(self):
        duration = world_boss_miniapp._world_boss_challenge_duration_ms({
            "durationMs": 28000,
            "maxDurationMs": 90000,
            "expiresIn": 75,
            "windows": [{"id": "late", "centerMs": 87000, "hitMs": 620}],
        })
        self.assertEqual(75000, duration)

    def test_expiry_trims_late_windows_and_finishes_before_server_close(self):
        calls = []
        hit_windows = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle", "actionLimit": 1, "actionsRemaining": 1},
                    "challenge": {
                        "mode": "qyz_focus_burst_v2",
                        "challengeId": "challenge-expiry-trim",
                        "durationMs": 6000,
                        "maxDurationMs": 8000,
                        "expiresIn": 7,
                        "windows": [
                            {"id": "w1", "centerMs": 1200, "hitMs": 620, "perfectMs": 210},
                            {"id": "w2", "centerMs": 2800, "hitMs": 620, "perfectMs": 210},
                            {"id": "w3", "centerMs": 5000, "hitMs": 620, "perfectMs": 210},
                        ],
                    },
                }
            if endpoint == "begin":
                return 200, {"ok": True, "startsInMs": 0}
            if endpoint == "hit":
                hit_windows.append(request["payload"]["windowId"])
                return 200, {"ok": True, "hit": {"attemptConsumed": False, "perfect": True, "damageYi": 100}}
            if endpoint == "finish":
                return 200, {
                    "ok": True,
                    "result": {"score": 200, "hits": len(hit_windows), "realtime_damage_yi": 200},
                }
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_EXPIRY_TRIM",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(["w1", "w2"], hit_windows)
        self.assertEqual(["start", "begin", "hit", "hit", "finish"], calls)
        plan_event = next(event for event in result["events"] if event["step"] == "plan")
        self.assertEqual(1, plan_event["expiry_trimmed_window_count"])

    def test_positive_damage_overrides_false_attempt_consumed_flag(self):
        self.assertTrue(world_boss_miniapp._world_boss_hit_was_consumed({
            "attempt_consumed": False,
            "damage_yi": 42,
            "perfect": False,
        }))
        self.assertFalse(world_boss_miniapp._world_boss_hit_was_consumed({
            "attempt_consumed": False,
            "damage_yi": 0,
            "perfect": False,
        }))

    def test_nangongque_protocol_adapter_is_manual_single_attempt_and_redacted(self):
        adapter = world_boss_miniapp.build_nangongque_miniapp_adapter()
        self.assertTrue(adapter.manual_only)
        self.assertFalse(adapter.default_enabled)
        self.assertEqual(1, adapter.request_policy.max_attempts_per_request)
        self.assertEqual("nangongque", world_boss_miniapp.world_boss_kind_from_token("nqb_SECRET"))

        start = world_boss_miniapp.build_nangongque_miniapp_request(
            "start",
            token="nqb_SECRET_TOKEN",
            init_data="query_id=secret&hash=VERY_SECRET",
            player_id=-1008659059191,
        )
        state = world_boss_miniapp.build_nangongque_miniapp_request(
            "state",
            session_token="SESSION_SECRET",
            init_data="query_id=secret&hash=VERY_SECRET",
            room_id="room-1",
            player_id=-1008659059191,
        )
        action = world_boss_miniapp.build_nangongque_miniapp_request(
            "input",
            session_token="SESSION_SECRET",
            init_data="query_id=secret&hash=VERY_SECRET",
            room_id="room-1",
            player_id=-1008659059191,
            input_payload={
                "seq": 1,
                "time": 1784690000000,
                "moveX": 0.25,
                "moveY": -0.5,
                "action": "attack",
                "compact": True,
            },
        )
        claim = world_boss_miniapp.build_nangongque_miniapp_request(
            "claim",
            session_token="SESSION_SECRET",
            init_data="query_id=secret&hash=VERY_SECRET",
        )

        self.assertEqual({"token", "initData", "playerId"}, set(start["payload"]))
        self.assertEqual({"sessionToken", "initData", "roomId", "playerId"}, set(state["payload"]))
        self.assertEqual(
            {"sessionToken", "initData", "roomId", "playerId", "input"},
            set(action["payload"]),
        )
        self.assertEqual({"sessionToken", "initData"}, set(claim["payload"]))
        self.assertEqual(1, action["transport_attempts"])
        self.assertEqual("world_boss", action["global_priority"])

        serialized = json.dumps(action["safe_summary"], ensure_ascii=False)
        self.assertNotIn("SESSION_SECRET", serialized)
        self.assertNotIn("VERY_SECRET", serialized)

    def test_nangongque_error_classification_separates_terminal_and_wait_states(self):
        self.assertTrue(world_boss_miniapp.is_terminal_nangongque_miniapp_error("nangongque_room_missing"))
        self.assertFalse(world_boss_miniapp.is_terminal_nangongque_miniapp_error("settlement_not_ready"))
        self.assertEqual(
            "settlement_not_ready",
            world_boss_miniapp.classify_nangongque_miniapp_error("settlement_not_ready"),
        )
        self.assertEqual("rate_limited", world_boss_miniapp.classify_nangongque_miniapp_error("HTTP 429"))

    def test_nangongque_executor_does_not_retry_an_uncertain_transport(self):
        calls = []
        request = world_boss_miniapp.build_nangongque_miniapp_request(
            "input",
            session_token="SESSION_SECRET",
            init_data="query_id=secret&hash=VERY_SECRET",
            room_id="room-1",
            player_id=7,
            input_payload={"seq": 1, "action": "attack"},
        )

        def transport(_request):
            calls.append(1)
            raise RuntimeError("connection reset")

        result = world_boss_miniapp.execute_nangongque_miniapp_request(
            request,
            transport,
            sleeper=lambda _delay: None,
        )
        self.assertFalse(result.ok)
        self.assertEqual(1, len(calls))
        self.assertEqual("transient", result.error_type)

    def test_adapter_and_request_payloads_are_scoped_and_captures_are_redacted(self):
        adapter = world_boss_miniapp.build_world_boss_miniapp_adapter()
        self.assertTrue(adapter.manual_only)
        self.assertFalse(adapter.default_enabled)

        start = world_boss_miniapp.build_world_boss_miniapp_request(
            "start",
            token="qyz_SECRET_TOKEN",
            init_data="query_id=secret&hash=VERY_SECRET",
            player_id=8659059191,
        )
        state = world_boss_miniapp.build_world_boss_miniapp_request(
            "state", token="qyz_SECRET_TOKEN", init_data="query_id=secret&hash=VERY_SECRET",
        )
        begin = world_boss_miniapp.build_world_boss_miniapp_request(
            "begin",
            token="qyz_SECRET_TOKEN",
            init_data="query_id=secret&hash=VERY_SECRET",
            challenge_id="challenge-1",
        )
        hit = world_boss_miniapp.build_world_boss_miniapp_request(
            "hit",
            token="qyz_SECRET_TOKEN",
            init_data="query_id=secret&hash=VERY_SECRET",
            challenge_id="challenge-1",
            window_id="window-1",
            elapsed_ms=2500,
            hold_ms=900,
        )
        finish = world_boss_miniapp.build_world_boss_miniapp_request(
            "finish",
            token="qyz_SECRET_TOKEN",
            init_data="query_id=secret&hash=VERY_SECRET",
            boss_proof={"mode": "qyz_focus_burst_v2"},
        )

        self.assertEqual({"token", "initData", "playerId"}, set(start["payload"]))
        self.assertEqual({"token", "initData"}, set(state["payload"]))
        self.assertEqual({"token", "initData", "challengeId"}, set(begin["payload"]))
        self.assertEqual(
            {"token", "initData", "challengeId", "windowId", "elapsedMs", "holdMs"},
            set(hit["payload"]),
        )
        self.assertEqual({"token", "initData", "bossProof"}, set(finish["payload"]))
        self.assertEqual("world_boss", start["global_priority"])

        captures = MiniAppCaptureStore()
        world_boss_miniapp.run_world_boss_miniapp_lab_flow(
            token="qyz_SECRET_TOKEN",
            init_data="query_id=secret&hash=VERY_SECRET",
            player_id=8659059191,
            transport=lambda _request: (409, {"ok": False, "error": "event_closed"}),
            sleeper=lambda _delay: None,
            capture_sink=captures,
            capture_source="world-boss-test:qyz_SECRET_TOKEN",
        )
        serialized = json.dumps(captures.records, ensure_ascii=False)
        self.assertNotIn("qyz_SECRET_TOKEN", serialized)
        self.assertNotIn("VERY_SECRET", serialized)
        self.assertNotIn("query_id=secret", serialized)

    def test_new_single_battle_protocol_calls_begin_before_hits(self):
        calls = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle", "actionLimit": 1, "actionsRemaining": 1},
                    "challenge": {
                        "mode": "qyz_focus_burst_v2",
                        "challengeId": "challenge-begin",
                        "durationMs": 4200,
                        "maxDurationMs": 6000,
                        "windows": [
                            {"id": "w1", "centerMs": 1200, "hitMs": 620, "perfectMs": 210},
                            {"id": "w2", "centerMs": 2800, "hitMs": 620, "perfectMs": 210},
                        ],
                    },
                }
            if endpoint == "begin":
                self.assertEqual("challenge-begin", request["payload"]["challengeId"])
                return 200, {"ok": True, "startsInMs": 500}
            if endpoint == "hit":
                return 200, {
                    "ok": True,
                    "hit": {"attemptConsumed": True, "perfect": True, "damageYi": 100},
                    "boss": {"actionLimit": 1, "actionsUsed": 1, "actionsRemaining": 0},
                }
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"score": 90, "hits": 2, "perfects": 2}}
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_BEGIN",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
            rng=random.Random(9),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(["start", "begin", "hit", "hit", "finish"], calls)
        self.assertTrue(any(event["step"] == "begin_sync" for event in result["events"]))

    def test_window_skip_count_omits_tail_windows_without_changing_server_limit(self):
        calls = []
        hit_windows = []
        finish_proofs = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle", "actionLimit": 1, "actionsRemaining": 1},
                    "player": {"maxHp": 1000},
                    "challenge": {
                        "mode": "qyz_focus_burst_v2",
                        "challengeId": "challenge-window-skip",
                        "durationMs": 7600,
                        "minDurationMs": 7200,
                        "windows": [
                            {"id": "w1", "centerMs": 1200, "hitMs": 620, "perfectMs": 210},
                            {"id": "w2", "centerMs": 2800, "hitMs": 620, "perfectMs": 210},
                            {"id": "w3", "centerMs": 4400, "hitMs": 620, "perfectMs": 210},
                            {"id": "w4", "centerMs": 6000, "hitMs": 620, "perfectMs": 210},
                        ],
                    },
                }
            if endpoint == "begin":
                return 200, {"ok": True, "startsInMs": 0}
            if endpoint == "hit":
                hit_windows.append(request["payload"]["windowId"])
                return 200, {
                    "ok": True,
                    "hit": {"attemptConsumed": True, "perfect": True, "damageYi": 100},
                    "boss": {"actionLimit": 1, "actionsUsed": 0, "actionsRemaining": 1},
                }
            if endpoint == "finish":
                finish_proofs.append(request["payload"]["bossProof"])
                return 200, {
                    "ok": True,
                    "result": {
                        "score": 80,
                        "hits": len(hit_windows),
                        "perfects": len(hit_windows),
                        "realtime_hit_count": len(hit_windows),
                        "realtime_damage_yi": len(hit_windows) * 100,
                    },
                }
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_WINDOW_SKIP",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
            rng=random.Random(9),
            window_skip_count=2,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(["start", "begin", "hit", "hit", "finish"], calls)
        self.assertEqual(["w1", "w2"], hit_windows)
        self.assertEqual(4, result["data"]["result"]["planned_window_count"])
        self.assertEqual(2, result["data"]["result"]["target_window_count"])
        self.assertEqual(2, result["data"]["result"]["window_skip_count"])
        self.assertFalse(result["data"]["result"]["full_window_run"])
        self.assertEqual(8200, finish_proofs[0]["durationMs"])

    def test_explicit_proof_duration_cannot_undercut_server_minimum(self):
        proof = world_boss_miniapp.build_world_boss_proof(
            {
                "mode": "qyz_focus_burst_v2",
                "challengeId": "challenge-min-duration",
                "minDurationMs": 75000,
            },
            [{"elapsedMs": 64000, "holdMs": 1200}],
            [],
            duration_ms=67500,
        )

        self.assertEqual(76000, proof["durationMs"])

    def test_dead_proof_may_settle_before_normal_minimum(self):
        proof = world_boss_miniapp.build_world_boss_proof(
            {
                "mode": "qyz_focus_burst_v2",
                "challengeId": "challenge-dead-early",
                "minDurationMs": 75000,
            },
            [{"elapsedMs": 12000, "holdMs": 1200}],
            [],
            duration_ms=14500,
            dead=True,
        )

        self.assertEqual(14500, proof["durationMs"])

    def test_begin_uses_server_started_at_under_high_rtt(self):
        calls = []
        clock = FakeClock()
        wall_epoch = 1_784_212_000.0

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle", "actionLimit": 1, "actionsRemaining": 1},
                    "challenge": {
                        "mode": "qyz_focus_burst_v2",
                        "challengeId": "challenge-server-anchor",
                        "durationMs": 2200,
                        "maxDurationMs": 4000,
                        "windows": [
                            {"id": "w1", "centerMs": 1200, "hitMs": 620, "perfectMs": 210},
                        ],
                    },
                }
            if endpoint == "begin":
                begin_started_at = clock.clock()
                clock.sleep(0.9)
                return 200, {
                    "ok": True,
                    "startsInMs": 1000,
                    "serverStartedAtMs": int((wall_epoch + begin_started_at + 1.2) * 1000),
                }
            if endpoint == "hit":
                return 200, {
                    "ok": True,
                    "hit": {"attemptConsumed": True, "perfect": True, "damageYi": 100},
                    "boss": {"actionLimit": 1, "actionsUsed": 1, "actionsRemaining": 0},
                }
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"score": 100, "hits": 1, "perfects": 1}}
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_SERVER_ANCHOR",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
            wall_clock=lambda: wall_epoch + clock.clock(),
            rng=random.Random(9),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(["start", "begin", "hit", "finish"], calls)
        sync = next(event for event in result["events"] if event["step"] == "begin_sync")
        self.assertEqual("server_started_at", sync["sync_source"])
        self.assertAlmostEqual(900, sync["round_trip_ms"], delta=1)
        self.assertAlmostEqual(300, sync["wait_ms"], delta=1)

    def test_outside_window_is_a_miss_but_still_finishes_once(self):
        calls = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle", "actionLimit": 1, "actionsRemaining": 1},
                    "challenge": {
                        "mode": "qyz_focus_burst_v2",
                        "challengeId": "challenge-outside-window",
                        "durationMs": 4200,
                        "maxDurationMs": 6000,
                        "playerHp": 10000,
                        "windows": [
                            {"id": "w1", "centerMs": 1200, "hitMs": 620, "perfectMs": 210},
                            {"id": "w2", "centerMs": 2800, "hitMs": 620, "perfectMs": 210},
                        ],
                    },
                }
            if endpoint == "begin":
                return 200, {"ok": True, "startsInMs": 500}
            if endpoint == "hit" and request["payload"]["windowId"] == "w1":
                return 200, {"ok": True, "hit": {"attemptConsumed": True, "perfect": True, "damageYi": 100}}
            if endpoint == "hit":
                return 409, {"ok": False, "error": "boss_hit_outside_window"}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"score": 72, "hits": 1, "perfects": 1, "damageYi": 100}}
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_OUTSIDE",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
            rng=random.Random(9),
        )

        self.assertTrue(result["ok"])
        self.assertEqual("settled", result["status"])
        self.assertEqual(["start", "begin", "hit", "hit", "finish"], calls)
        self.assertTrue(any(event["step"] == "server_rejected_window" for event in result["events"]))
        summary = result["data"]["result"]
        self.assertEqual(2, summary["planned_window_count"])
        self.assertEqual(1, summary["rejected_window_count"])
        self.assertFalse(summary["full_window_run"])

    def test_strict_error_classification(self):
        for error_type in world_boss_miniapp.WORLD_BOSS_ERROR_TYPES:
            self.assertEqual(error_type, world_boss_miniapp.classify_world_boss_miniapp_error(error_type))
            self.assertEqual(error_type, world_boss_miniapp.classify_world_boss_miniapp_error(f"server: {error_type}"))
        self.assertEqual("failed", world_boss_miniapp.classify_world_boss_miniapp_error("unknown_error"))

        missing = world_boss_miniapp.run_world_boss_miniapp_lab_flow(
            token="",
            init_data="query_id=x&hash=y",
            transport=lambda _request: self.fail("transport must not be called"),
        )
        self.assertEqual("boss_token_missing", missing["status"])

    def test_event_closed_stops_after_start(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 409, {"ok": False, "error": "boss_event_closed"}

        result = world_boss_miniapp.run_world_boss_miniapp_lab_flow(
            token="qyz_CLOSED",
            init_data="query_id=x&hash=y",
            player_id=1,
            transport=transport,
            sleeper=lambda _delay: None,
        )
        self.assertFalse(result["ok"])
        self.assertEqual("boss_event_closed", result["status"])
        self.assertEqual(["start"], calls)

    def test_event_closed_after_real_hits_reconciles_state_without_finish(self):
        calls = []
        clock = FakeClock()
        stop_event = threading.Event()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle", "actionLimit": 1, "actionsRemaining": 1},
                    "challenge": {
                        "mode": "qyz_focus_burst_v2",
                        "challengeId": "challenge-closed-after-hit",
                        "durationMs": 4200,
                        "maxDurationMs": 6000,
                        "windows": [
                            {"id": "w1", "centerMs": 1200, "hitMs": 620, "perfectMs": 210},
                            {"id": "w2", "centerMs": 2800, "hitMs": 620, "perfectMs": 210},
                        ],
                    },
                }
            if endpoint == "begin":
                return 200, {"ok": True, "startsInMs": 0}
            if endpoint == "hit" and request["payload"]["windowId"] == "w1":
                return 200, {
                    "ok": True,
                    "hit": {"attemptConsumed": True, "perfect": True, "damageYi": 100},
                    "boss": {"actionLimit": 1, "actionsUsed": 0, "actionsRemaining": 1},
                }
            if endpoint == "hit":
                return 409, {"ok": False, "error": "boss_event_closed"}
            if endpoint == "state":
                return 200, {"ok": True, "boss": {"eventStatus": "closed"}}
            if endpoint == "finish":
                self.fail("closed event must not submit finish")
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_CLOSED_AFTER_HIT",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
            stop_event=stop_event,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("event_closed_partial", result["status"])
        self.assertEqual(["start", "begin", "hit", "hit", "state"], calls)
        self.assertEqual(1, result["data"]["result"]["accepted_hit_count"])
        self.assertEqual(100, result["data"]["result"]["accepted_damage_yi"])
        self.assertTrue(stop_event.is_set())

    def test_identity_selection_is_returned_without_combat(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {
                "ok": True,
                "needsIdentitySelection": True,
                "identityChoices": [
                    {"playerId": 11, "username": "alpha", "daoName": "甲", "secret": "drop-me"},
                    {"playerId": 12, "username": "beta", "available": True},
                ],
            }

        result = world_boss_miniapp.run_world_boss_miniapp_lab_flow(
            token="qyz_SELECT",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=lambda _delay: None,
        )
        self.assertEqual("needs_identity_selection", result["status"])
        self.assertEqual(["start"], calls)
        self.assertEqual([11, 12], [item["playerId"] for item in result["data"]["identities"]])
        self.assertNotIn("secret", result["data"]["identities"][0])

    def test_verification_request_is_not_bypassed(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {"ok": True, "needsVerification": True, "verifyUrl": "/xianxia-verify?id=secret"}

        result = world_boss_miniapp.run_world_boss_miniapp_lab_flow(
            token="qyz_VERIFY",
            init_data="query_id=x&hash=y",
            player_id=1,
            transport=transport,
            sleeper=lambda _delay: None,
        )
        self.assertEqual("verification_required", result["status"])
        self.assertEqual(["start"], calls)

    def test_action_plan_uses_sorted_window_centers_and_safe_high_damage_holds(self):
        plan = world_boss_miniapp.build_world_boss_action_plan(
            {
                "challengeId": "challenge-plan",
                "windows": [
                    {"windowId": "late", "startMs": 4000, "endMs": 5000},
                    {"windowId": "early", "centerMs": 1500},
                    {"windowId": "middle", "openMs": 2500, "closeMs": 3500},
                ],
            },
            rng=random.Random(7),
        )
        self.assertEqual(["early", "middle", "late"], [item["windowId"] for item in plan])
        self.assertTrue(all(item["centerMs"] - item["elapsedMs"] == 140 for item in plan))
        self.assertTrue(all(1200 <= item["holdMs"] <= 1235 for item in plan))
        self.assertTrue(all(item["hitMs"] == 560 for item in plan))
        self.assertTrue(all(item["chargeStartMs"] == item["elapsedMs"] - item["holdMs"] for item in plan))
        self.assertTrue(all(item["stance"] == "强攻" for item in plan))

    def test_action_plan_tracks_ring_hit_width_without_overcharging(self):
        plan = world_boss_miniapp.build_world_boss_action_plan(
            {
                "challengeId": "challenge-ring",
                "windows": [
                    {"id": "normal", "centerMs": 2000, "hitMs": 700, "perfectMs": 180},
                    {"id": "wide", "centerMs": 5000, "hitMs": 1800, "perfectMs": 180},
                ],
            },
            rng=random.Random(13),
        )

        self.assertTrue(1200 <= plan[0]["holdMs"] <= 1235)
        self.assertTrue(1200 <= plan[1]["holdMs"] <= 1235)
        self.assertTrue(all(520 <= item["holdMs"] <= 1250 for item in plan))

    def test_start_refresh_waits_real_windows_then_hits_and_finishes_once(self):
        calls = []
        payloads = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            payloads.append(dict(request["payload"]))
            if endpoint == "start" and calls.count("start") == 1:
                return 200, {
                    "ok": True,
                    "joined": True,
                    "sessionToken": "qyz_SESSION",
                    "boss": {"roomStatus": "joining", "joinRemainingSeconds": 20},
                }
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "elapsedMs": 500,
                    "challenge": {
                        "mode": "qyz_focus_burst_v2",
                        "challengeId": "challenge-live",
                        "playerHp": 1000,
                        "windows": [
                            {"windowId": "w1", "centerMs": 1500},
                            {"windowId": "w2", "startMs": 2500, "endMs": 3500},
                        ],
                    },
                }
            if endpoint == "begin":
                return 200, {"ok": True, "startsInMs": 500}
            if endpoint == "hit" and request["payload"]["windowId"] == "w1":
                return 200, {
                    "ok": True,
                    "hit": {"damageYi": 120},
                }
            if endpoint == "hit":
                return 200, {
                    "ok": True,
                    "hit": {"damageYi": 280},
                }
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"rank": 3, "reward": "玄晶"}}
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_miniapp_lab_flow(
            token="qyz_FLOW",
            init_data="query_id=x&hash=y",
            player_id=99,
            transport=transport,
            rng=random.Random(9),
            sleeper=clock.sleep,
            clock=clock.clock,
        )
        self.assertTrue(result["ok"])
        self.assertEqual("settled", result["status"])
        self.assertEqual(["start", "start", "begin", "hit", "hit", "finish"], calls)
        self.assertEqual("qyz_SESSION", payloads[1]["token"])
        self.assertTrue(all(delay > 0 for delay in clock.sleeps))
        self.assertEqual("challenge-live", payloads[2]["challengeId"])
        self.assertEqual("w1", payloads[3]["windowId"])
        self.assertAlmostEqual(1360, payloads[3]["elapsedMs"], delta=1)
        self.assertGreaterEqual(payloads[3]["holdMs"], 520)
        self.assertEqual("w2", payloads[4]["windowId"])
        proof = payloads[-1]["bossProof"]
        self.assertEqual("qyz_focus_burst_v2", proof["mode"])
        self.assertEqual("强攻", proof["stance"])
        self.assertAlmostEqual(1360, proof["actions"][0]["t"], delta=1)
        self.assertAlmostEqual(2860, proof["actions"][1]["t"], delta=2)
        self.assertEqual(1000, proof["playerHp"])
        self.assertFalse(proof["dead"])
        self.assertIs(proof["realtimeDamageApplied"], True)
        self.assertEqual(0, proof["clientStats"]["damage"])
        self.assertEqual(0, proof["clientStats"]["grazes"])
        self.assertEqual(2, proof["clientStats"]["dodges"])
        self.assertEqual(2, proof["clientStats"]["bestCombo"])
        self.assertGreaterEqual(proof["durationMs"], 3000 + 560 + 2200)

    def test_slow_hit_responses_only_make_a_bounded_release_adjustment(self):
        calls = []
        hit_elapsed_ms = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle", "actionLimit": 1, "actionsRemaining": 1},
                    "challenge": {
                        "challengeId": "challenge-slow-hit",
                        "windows": [
                            {"id": "w1", "centerMs": 1500, "hitMs": 620, "perfectMs": 210},
                            {"id": "w2", "centerMs": 7200, "hitMs": 620, "perfectMs": 210},
                            {"id": "w3", "centerMs": 12900, "hitMs": 620, "perfectMs": 210},
                        ],
                    },
                }
            if endpoint == "hit":
                hit_elapsed_ms.append(request["payload"]["elapsedMs"])
                clock.sleep(1.3)
                return 200, {"ok": True, "hit": {"perfect": True, "damageYi": 100}}
            if endpoint == "finish":
                return 200, {
                    "ok": True,
                    "result": {"score": 100, "hits": 3, "perfects": 3, "realtime_hit_count": 3},
                }
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_SLOW_HIT",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        for actual, expected in zip(hit_elapsed_ms, (1300, 7000, 12700)):
            self.assertAlmostEqual(expected, actual, delta=1)
        self.assertEqual(3, result["data"]["result"]["completed_window_count"])
        self.assertTrue(result["data"]["result"]["full_window_run"])

    def test_live_window_lead_stays_perfect_and_absorbs_observed_latency_spike(self):
        plan = world_boss_miniapp.build_world_boss_action_plan(
            {
                "challengeId": "challenge-latency-guard",
                "windows": [
                    {"id": "w11", "centerMs": 58939, "hitMs": 620, "perfectMs": 210},
                ],
            },
            rng=random.Random(27),
        )

        action = plan[0]
        release_lead_ms = action["centerMs"] - action["elapsedMs"]
        self.assertEqual(200, release_lead_ms)
        self.assertLessEqual(release_lead_ms, action["perfectMs"])
        # The latest production miss included roughly 760ms of arrival delay.
        # Releasing at the safe early edge keeps that request inside hitMs.
        arrival_delta_ms = action["elapsedMs"] + 760 - action["centerMs"]
        self.assertLessEqual(abs(arrival_delta_ms), action["hitMs"])

    def test_joined_battle_polls_until_room_is_locked(self):
        calls = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start" and calls.count("start") == 1:
                return 200, {"ok": True, "boss": {"roomStatus": "waiting", "participantCount": 4}}
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "elapsedMs": 0,
                    "challenge": {
                        "challengeId": "challenge-wait",
                        "windows": [{"id": "w1", "centerMs": 1500, "hitMs": 560, "perfectMs": 180}],
                    },
                }
            if endpoint == "hit":
                return 200, {"ok": True, "playerHp": 100, "clientStats": {"hits": 1}}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(
            True, "joined", player_id="77", identity_id=7700, account_id=7,
        )
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_WAIT",
            init_data="query_id=x&hash=y",
            transport=transport,
            rng=random.Random(5),
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(["start", "start", "hit", "finish"], calls)
        self.assertEqual(1.2, clock.sleeps[0])

    def test_realtime_transition_wakes_start_refresh_without_replacing_http(self):
        calls = []
        waits = []
        sleeps_when_waiting = []
        clock = FakeClock()
        realtime_boss = {"roomStatus": "waiting"}

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start" and calls.count("start") == 1:
                return 200, {"ok": True, "boss": {"roomStatus": "waiting"}}
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle"},
                    "challenge": {
                        "challengeId": "challenge-realtime-wake",
                        "windows": [{"id": "w1", "centerMs": 1500}],
                    },
                }
            if endpoint == "hit":
                return 200, {"ok": True, "hit": {"damageYi": 1}}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        def realtime_waiter(timeout_sec):
            waits.append(timeout_sec)
            sleeps_when_waiting.append(list(clock.sleeps))
            realtime_boss["roomStatus"] = "battle"
            return True

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_SESSION",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
            realtime_waiter=realtime_waiter,
            realtime_state_provider=lambda: dict(realtime_boss),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(["start", "start", "hit", "finish"], calls)
        self.assertEqual([1.2], waits)
        self.assertEqual([[]], sleeps_when_waiting)

    def test_start_refresh_uses_page_join_intervals(self):
        calls = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start" and calls.count("start") == 1:
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "joining", "joinRemainingSeconds": 18},
                }
            if endpoint == "start" and calls.count("start") == 2:
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "joining", "joinRemainingSeconds": 3},
                }
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle"},
                    "player": {"maxHp": 100},
                    "challenge": {
                        "challengeId": "challenge-refresh",
                        "windows": [{"id": "w1", "centerMs": 1500}],
                    },
                }
            if endpoint == "hit":
                return 200, {"ok": True, "hit": {"damageYi": 1}}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77")
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_SESSION",
            entry_token="qyz_ENTRY",
            init_data="query_id=x&hash=y",
            transport=transport,
            rng=random.Random(3),
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(6.0, clock.sleeps[0])
        self.assertEqual(1.2, clock.sleeps[1])
        self.assertEqual(1.25, clock.sleeps[2])

    def test_join_receipt_waits_near_lock_before_first_refresh(self):
        calls = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle"},
                    "player": {"maxHp": 100},
                    "challenge": {
                        "challengeId": "challenge-delayed-refresh",
                        "windows": [{"id": "w1", "centerMs": 1500}],
                    },
                }
            if endpoint == "hit":
                return 200, {"ok": True, "hit": {"damageYi": 1}}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(
            True,
            "joined",
            player_id="77",
            join_remaining_sec=20,
        )
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_DELAY",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(17.0, clock.sleeps[0])
        self.assertEqual(["start", "hit", "finish"], calls)

    def test_start_refresh_429_uses_bounded_progressive_backoff(self):
        calls = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start" and calls.count("start") <= 2:
                return 429, "rate limited"
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle"},
                    "player": {"maxHp": 100},
                    "challenge": {
                        "challengeId": "challenge-after-429",
                        "windows": [{"id": "w1", "centerMs": 1500}],
                    },
                }
            if endpoint == "hit":
                return 200, {"ok": True, "hit": {"damageYi": 1}}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77")
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_RATE",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        self.assertEqual([12.0, 24.0], clock.sleeps[:2])
        self.assertEqual(3, calls.count("start"))

    def test_join_retry_after_returns_rate_limited_without_state_calibration(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 429, {"ok": False, "error": "rate limited"}, {"Retry-After": "120"}

        receipt = world_boss_miniapp.join_world_boss_miniapp_lab(
            token="qyz_RATE_JOIN",
            init_data="query_id=x&hash=y",
            player_id=77,
            identity_id=7700,
            transport=transport,
        )

        self.assertFalse(receipt.joined)
        self.assertEqual("rate_limited", receipt.status)
        self.assertEqual(120, receipt.retry_after_sec)
        self.assertEqual(["start"], calls)

    def test_long_start_retry_after_does_not_poll_state_or_sleep(self):
        calls = []
        clock = FakeClock()

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 429, {"ok": False, "error": "rate limited"}, {"Retry-After": "120"}

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_RATE_BATTLE",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("rate_limited", result["status"])
        self.assertEqual(120, result["retry_after_sec"])
        self.assertEqual(["start"], calls)
        self.assertEqual([], clock.sleeps)

    def test_short_retry_after_does_not_sleep_past_battle_deadline(self):
        calls = []
        clock = FakeClock()

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 429, {"ok": False, "error": "rate limited"}, {"Retry-After": "10"}

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_RATE_DEADLINE",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
            battle_wait_timeout_sec=5,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("rate_limited", result["status"])
        self.assertEqual(10, result["retry_after_sec"])
        self.assertEqual(["start"], calls)
        self.assertEqual([], clock.sleeps)

    def test_session_token_error_resets_to_entry_token_before_battle(self):
        tokens = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            tokens.append((endpoint, request["payload"]["token"]))
            if endpoint == "start" and len(tokens) == 1:
                return 409, {"ok": False, "error": "boss_token_used"}
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "sessionToken": "qyz_RECONNECTED",
                    "boss": {"roomStatus": "battle"},
                    "player": {"maxHp": 100},
                    "challenge": {
                        "challengeId": "challenge-reconnect",
                        "windows": [{"id": "w1", "centerMs": 1500}],
                    },
                }
            if endpoint == "hit":
                return 200, {"ok": True, "hit": {"damageYi": 1}}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77")
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_SESSION",
            entry_token="qyz_ENTRY",
            init_data="query_id=x&hash=y",
            transport=transport,
            rng=random.Random(4),
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(("start", "qyz_SESSION"), tokens[0])
        self.assertEqual(("start", "qyz_ENTRY"), tokens[1])
        self.assertEqual(("hit", "qyz_RECONNECTED"), tokens[2])
        self.assertEqual(("finish", "qyz_RECONNECTED"), tokens[3])
        self.assertTrue(any(event["step"] == "reset_entry_token" for event in result["events"]))

    def test_unknown_start_is_not_retried_and_state_calibrates_join(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                raise TimeoutError("start response lost")
            return 200, {
                "ok": True,
                "joined": True,
                "player": {"playerId": 88, "daoName": "校准角色"},
                "room": {"status": "waiting"},
            }

        receipt = world_boss_miniapp.join_world_boss_miniapp_lab(
            token="qyz_UNKNOWN",
            init_data="query_id=x&hash=y",
            player_id=88,
            identity_id=8800,
            account_id=8,
            transport=transport,
            sleeper=lambda _delay: None,
        )

        self.assertTrue(receipt.joined)
        self.assertTrue(receipt.calibrated)
        self.assertEqual("joined_calibrated", receipt.status)
        self.assertEqual(["start", "state"], calls)

    def test_four_account_barrier_joins_all_before_first_battle_state(self):
        calls = []
        clock = FakeClock()
        starts_by_token = {}

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            token = request["payload"]["token"]
            calls.append((endpoint, token))
            if endpoint == "start":
                starts_by_token[token] = starts_by_token.get(token, 0) + 1
                if starts_by_token[token] == 1:
                    return 200, {"ok": True, "joined": True, "playerId": request["payload"]["playerId"]}
                return 200, {
                    "ok": True,
                    "elapsedMs": 0,
                    "challenge": {
                        "challengeId": f"challenge-{token}",
                        "windows": [{"windowId": "w1", "centerMs": 1500}],
                    },
                }
            if endpoint == "hit":
                return 200, {"ok": True, "playerHp": 100, "clientStats": {"hits": 1}}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        entries = [
            {
                "token": f"qyz_ACCOUNT_{index}",
                "init_data": f"query_id={index}&hash=secret",
                "player_id": 100 + index,
                "identity_id": 200 + index,
                "account_id": index,
            }
            for index in range(1, 5)
        ]
        result = world_boss_miniapp.run_world_boss_miniapp_batch_lab_flow(
            entries,
            transport=transport,
            rng=random.Random(21),
            sleeper=clock.sleep,
            clock=clock.clock,
            opened_at=clock.clock(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(4, result["barrier"]["joined_count"])
        self.assertEqual(["start"] * 4, [endpoint for endpoint, _token in calls[:4]])
        self.assertEqual(["start", "hit", "finish"] * 4, [endpoint for endpoint, _token in calls[4:]])

    def test_server_elapsed_does_not_shift_page_local_timeline(self):
        calls = []
        hit_windows = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "elapsedMs": 2500,
                    "challenge": {
                        "challengeId": "challenge-filter",
                        "windows": [
                            {"windowId": "expired", "centerMs": 1000},
                            {"windowId": "future", "centerMs": 4000},
                        ],
                    },
                }
            if endpoint == "hit":
                hit_windows.append(request["payload"]["windowId"])
                return 200, {"ok": True, "playerHp": 90, "clientStats": {"hits": 1}}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(
            True, "joined", player_id="77", identity_id=7700, account_id=7,
        )
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_FILTER",
            init_data="query_id=x&hash=y",
            transport=transport,
            rng=random.Random(22),
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(["start", "hit", "hit", "finish"], calls)
        self.assertEqual(["expired", "future"], hit_windows)
        plan_event = next(event for event in result["events"] if event["step"] == "plan")
        self.assertEqual(0, plan_event["expired_window_count"])
        self.assertEqual(0, plan_event["current_elapsed_ms"])
        self.assertEqual(2500, plan_event["server_elapsed_ms_ignored"])

    def test_action_limit_does_not_retry_hit_or_submit_finish(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start" and calls.count("start") == 1:
                return 200, {"ok": True, "joined": True}
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "challenge": {"challengeId": "challenge-limit", "windows": [{"windowId": "w1", "centerMs": 1500}]},
                }
            return 409, {"ok": False, "error": "boss_action_limit"}

        result = world_boss_miniapp.run_world_boss_miniapp_lab_flow(
            token="qyz_LIMIT",
            init_data="query_id=x&hash=y",
            player_id=1,
            transport=transport,
            sleeper=lambda _delay: None,
            clock=lambda: 0,
        )
        self.assertEqual("boss_action_limit", result["status"])
        self.assertEqual(["start", "start", "hit"], calls)

    def test_action_limit_after_real_hit_still_submits_finish(self):
        calls = []
        clock = FakeClock()
        hit_count = 0

        def transport(request):
            nonlocal hit_count
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle"},
                    "challenge": {
                        "challengeId": "challenge-limit-after-hit",
                        "windows": [
                            {"id": "w1", "centerMs": 1500},
                            {"id": "w2", "centerMs": 3000},
                        ],
                    },
                }
            if endpoint == "hit":
                hit_count += 1
                if hit_count == 1:
                    return 200, {
                        "ok": True,
                        "hit": {"attemptConsumed": True, "perfect": True, "damageYi": 100},
                    }
                return 409, {"ok": False, "error": "boss_action_limit"}
            if endpoint == "finish":
                return 200, {
                    "ok": True,
                    "result": {"settled": True, "score": 100, "hits": 1, "perfects": 1},
                }
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77")
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_LIMIT_AFTER_HIT",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        self.assertEqual("settled", result["status"])
        self.assertEqual(["start", "hit", "hit", "finish"], calls)
        self.assertTrue(any(event["step"] == "action_limit_after_hits" for event in result["events"]))

    def test_missed_window_applies_page_counter_damage_and_resets_combo(self):
        calls = []
        clock = FakeClock()
        hit_count = 0

        def transport(request):
            nonlocal hit_count
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle"},
                    "player": {"maxHp": 100},
                    "challenge": {
                        "challengeId": "challenge-miss",
                        "phase": 1,
                        "windows": [
                            {"id": "w1", "centerMs": 1500},
                            {"id": "w2", "centerMs": 3000},
                            {"id": "w3", "centerMs": 5000},
                        ],
                    },
                }
            if endpoint == "hit":
                hit_count += 1
                if hit_count == 1:
                    clock.sleep(2.5)
                return 200, {"ok": True, "hit": {"damageYi": 1}}
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77")
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_MISS",
            init_data="query_id=x&hash=y",
            transport=transport,
            rng=random.Random(8),
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertTrue(result["ok"])
        proof = result["proof"]
        self.assertEqual(84, proof["playerHp"])
        self.assertFalse(proof["dead"])
        self.assertEqual(2, proof["clientStats"]["hits"])
        self.assertEqual(1, proof["clientStats"]["combo"])
        self.assertEqual(1, proof["clientStats"]["bestCombo"])
        self.assertEqual(1, sum(event["step"] == "miss_window" for event in result["events"]))

    def test_expired_windows_can_kill_player_and_finish_without_hits(self):
        calls = []
        clock = FakeClock()
        sleep_count = 0

        def lagging_sleep(delay):
            nonlocal sleep_count
            sleep_count += 1
            clock.sleep(delay + (2.0 if sleep_count == 2 else 0.0))

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle"},
                    "player": {"maxHp": 30},
                    "challenge": {
                        "challengeId": "challenge-dead",
                        "phase": 1,
                        "windows": [
                            {"id": "w1", "centerMs": 500},
                            {"id": "w2", "centerMs": 1500},
                        ],
                    },
                }
            if endpoint == "finish":
                return 200, {"ok": True, "result": {"settled": True}}
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77")
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_DEAD",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=lagging_sleep,
            clock=clock.clock,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("settled_zero_contribution", result["status"])
        self.assertEqual(["start", "finish"], calls)
        self.assertEqual([], result["proof"]["actions"])
        self.assertEqual(0, result["proof"]["playerHp"])
        self.assertTrue(result["proof"]["dead"])
        self.assertTrue(any(event["step"] == "player_dead" for event in result["events"]))

    def test_server_action_limit_stops_extra_hit_requests_and_reports_real_summary(self):
        calls = []
        clock = FakeClock()
        hit_count = 0
        captures = MiniAppCaptureStore()

        def transport(request):
            nonlocal hit_count
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle", "actionLimit": 2, "actionsRemaining": 2},
                    "challenge": {
                        "challengeId": "challenge-action-limit",
                        "windows": [
                            {"id": "w1", "centerMs": 1500},
                            {"id": "w2", "centerMs": 3000},
                            {"id": "w3", "centerMs": 4500},
                        ],
                    },
                }
            if endpoint == "hit":
                hit_count += 1
                return 200, {
                    "ok": True,
                    "hit": {
                        "attemptConsumed": True,
                        "perfect": hit_count == 1,
                        "damageYi": 100 * hit_count,
                    },
                    "boss": {
                        "actionLimit": 2,
                        "actionsUsed": hit_count,
                        "actionsRemaining": 2 - hit_count,
                    },
                }
            if endpoint == "finish":
                return 200, {
                    "ok": True,
                    "result": {
                        "score": 900,
                        "hits": 2,
                        "perfects": 1,
                        "realtime_hit_count": 2,
                        "realtime_damage_yi": 300,
                    },
                }
            self.fail(endpoint)

        receipt = world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77")
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt,
            token="qyz_LIMIT",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
            capture_sink=captures,
            capture_source="world_boss:battle:77",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(["start", "hit", "hit", "finish"], calls)
        self.assertEqual(2, result["data"]["result"]["accepted_hit_count"])
        self.assertEqual(300, result["data"]["result"]["accepted_damage_yi"])
        self.assertEqual(3, result["data"]["result"]["planned_window_count"])
        self.assertFalse(result["data"]["result"]["full_window_run"])
        serialized = json.dumps(captures.records, ensure_ascii=False)
        self.assertIn('"attempt_consumed": true', serialized)
        self.assertIn('"realtime_damage_yi": 300.0', serialized)

    def test_finish_zero_overrides_earlier_consumed_hit_evidence(self):
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle"},
                    "challenge": {
                        "challengeId": "challenge-zero-finish",
                        "windows": [{"id": "w1", "centerMs": 1500}],
                    },
                }
            if endpoint == "hit":
                return 200, {
                    "ok": True,
                    "hit": {"attemptConsumed": True, "perfect": True, "damageYi": 100},
                }
            if endpoint == "finish":
                return 200, {
                    "ok": True,
                    "result": {
                        "score": 0,
                        "hits": 0,
                        "realtime_hit_count": 0,
                        "realtime_damage_yi": 0,
                    },
                }
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_ZERO",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("settled_zero_contribution", result["status"])
        self.assertEqual(1, result["data"]["result"]["accepted_hit_count"])

    def test_finish_hit_count_overrides_successful_http_attempt_count(self):
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle"},
                    "challenge": {
                        "challengeId": "challenge-authoritative-hits",
                        "windows": [
                            {"id": "w1", "centerMs": 1500},
                            {"id": "w2", "centerMs": 7200},
                        ],
                    },
                }
            if endpoint == "hit":
                return 200, {"ok": True, "hit": {"perfect": True, "damageYi": 100}}
            if endpoint == "finish":
                return 200, {
                    "ok": True,
                    "result": {"score": 100, "hits": 1, "perfects": 1, "realtime_hit_count": 1},
                }
            self.fail(endpoint)

        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77"),
            token="qyz_AUTHORITATIVE_HITS",
            init_data="query_id=x&hash=y",
            transport=transport,
            sleeper=clock.sleep,
            clock=clock.clock,
        )

        summary = result["data"]["result"]
        self.assertEqual(2, summary["attempted_hit_count"])
        self.assertEqual(1, summary["completed_window_count"])
        self.assertFalse(summary["full_window_run"])


if __name__ == "__main__":
    unittest.main()
