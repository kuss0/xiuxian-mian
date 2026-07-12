import json
import random
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

    def test_action_plan_uses_sorted_window_centers_and_conservative_holds(self):
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
        self.assertTrue(all(abs(item["elapsedMs"] - item["centerMs"]) <= 24 for item in plan))
        self.assertTrue(all(548 <= item["holdMs"] <= 572 for item in plan))
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

        self.assertTrue(688 <= plan[0]["holdMs"] <= 712)
        self.assertLessEqual(plan[1]["holdMs"], 1250)
        self.assertGreaterEqual(plan[1]["holdMs"], 1238)
        self.assertTrue(all(item["holdMs"] <= 1250 for item in plan))

    def test_state_fallback_waits_real_windows_then_hits_and_finishes_once(self):
        calls = []
        payloads = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            payloads.append(dict(request["payload"]))
            if endpoint == "start":
                return 200, {"ok": True, "joined": True, "sessionToken": "qyz_SESSION"}
            if endpoint == "state":
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
            if endpoint == "hit" and request["payload"]["windowId"] == "w1":
                return 200, {
                    "ok": True,
                    "playerHp": 930,
                    "realtimeDamageApplied": 120,
                    "clientStats": {"damage": 120, "hits": 1, "perfects": 1, "combo": 1, "bestCombo": 1},
                }
            if endpoint == "hit":
                return 200, {
                    "ok": True,
                    "playerHp": 850,
                    "dead": False,
                    "realtimeDamageApplied": 280,
                    "clientStats": {"dodges": 1, "grazes": 1, "damage": 280, "hits": 2, "perfects": 2, "combo": 2, "bestCombo": 2},
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
        self.assertEqual(["start", "state", "hit", "hit", "finish"], calls)
        self.assertEqual("qyz_SESSION", payloads[1]["token"])
        self.assertTrue(all(delay > 0 for delay in clock.sleeps))
        self.assertEqual("w1", payloads[2]["windowId"])
        self.assertLessEqual(abs(payloads[2]["elapsedMs"] - 1500), 24)
        self.assertGreaterEqual(payloads[2]["holdMs"], 520)
        self.assertEqual("w2", payloads[3]["windowId"])
        proof = payloads[-1]["bossProof"]
        self.assertEqual("qyz_focus_burst_v2", proof["mode"])
        self.assertEqual("强攻", proof["stance"])
        self.assertTrue(all(abs(item["t"] - center) <= 24 for item, center in zip(proof["actions"], (1500, 3000))))
        self.assertEqual(850, proof["playerHp"])
        self.assertFalse(proof["dead"])
        self.assertIs(proof["realtimeDamageApplied"], True)
        self.assertEqual(280, proof["clientStats"]["damage"])
        self.assertEqual(2, proof["clientStats"]["bestCombo"])

    def test_joined_battle_polls_until_room_is_locked(self):
        calls = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "state" and calls.count("state") == 1:
                return 200, {"ok": True, "boss": {"roomStatus": "waiting", "participantCount": 4}}
            if endpoint == "state":
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
        self.assertEqual(["state", "state", "hit", "finish"], calls)
        self.assertGreaterEqual(clock.sleeps[0], 1.5)

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

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            token = request["payload"]["token"]
            calls.append((endpoint, token))
            if endpoint == "start":
                return 200, {"ok": True, "joined": True, "playerId": request["payload"]["playerId"]}
            if endpoint == "state":
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
        self.assertEqual(["state", "hit", "finish"] * 4, [endpoint for endpoint, _token in calls[4:]])

    def test_battle_refresh_filters_expired_windows(self):
        calls = []
        hit_windows = []
        clock = FakeClock()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "state":
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
        self.assertEqual(["state", "hit", "finish"], calls)
        self.assertEqual(["future"], hit_windows)
        plan_event = next(event for event in result["events"] if event["step"] == "plan")
        self.assertEqual(1, plan_event["expired_window_count"])

    def test_action_limit_does_not_retry_hit_or_submit_finish(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {"ok": True, "joined": True}
            if endpoint == "state":
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
        self.assertEqual(["start", "state", "hit"], calls)


if __name__ == "__main__":
    unittest.main()
