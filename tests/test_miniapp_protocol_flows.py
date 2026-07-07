import json
import random
import unittest

from model import webapp_core
from model.features import cave_treasure_miniapp, stargazer_miniapp, tree_miniapp, trial_miniapp


class MiniAppProtocolFlowTests(unittest.TestCase):
    def test_stargazer_ready_status_variants_are_collectable(self):
        farm_state = stargazer_miniapp.parse_stargazer_farm_state({
            "data": {
                "domain": {
                    "mode": "stars",
                    "plots": [
                        {"key": "a", "empty": False, "status": "精华已成"},
                        {"key": "b", "empty": False, "statusLabel": "可收集"},
                    ],
                },
            },
        })
        decision = stargazer_miniapp.choose_stargazer_farm_action(farm_state)

        self.assertEqual(2, farm_state["ready_slot_count"])
        self.assertTrue(farm_state["all_ready"])
        self.assertEqual("collect", decision["action"])

    def test_stargazer_invalid_domain_stops_without_action(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {"ok": True, "domain": {"mode": "unknown", "plots": [{"key": "a"}]}}

        result = stargazer_miniapp.run_stargazer_miniapp_lab_flow(
            token="farm_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            star_choice="竹灵",
            transport=transport,
        )
        text = json.dumps(result, ensure_ascii=False)

        self.assertFalse(result["ok"])
        self.assertEqual("failed", result["status"])
        self.assertEqual(["start"], calls)
        self.assertNotIn("farm_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)

    def test_trial_unsolved_challenge_stops_before_finish(self):
        calls = []
        nodes = [{"id": f"n{idx}", "x": 10 + idx * 15, "y": 20 + idx * 10} for idx in range(5)]
        edges = [
            {"from": left["id"], "to": right["id"]}
            for left_index, left in enumerate(nodes)
            for right in nodes[left_index + 1:]
        ]

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "challenge": {
                        "mode": "tianjiPlanarityV1",
                        "challengeId": "k5-unsolved",
                        "nodes": nodes,
                        "edges": edges,
                        "minDurationMs": 20,
                        "maxDurationMs": 90000,
                    },
                }
            raise AssertionError("finish must not be called when solver fails")

        result = trial_miniapp.run_trial_miniapp_lab_flow(
            token="trial_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=random.Random(7),
            sleeper=lambda _delay: None,
        )
        text = json.dumps(result, ensure_ascii=False)

        self.assertFalse(result["ok"])
        self.assertEqual("solve_failed", result["status"])
        self.assertEqual(["start"], calls)
        self.assertEqual("solve", result["events"][-1]["step"])
        self.assertFalse(result["events"][-1]["ok"])
        self.assertNotIn("trial_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)

    def test_cave_treasure_daily_exhausted_start_does_not_hunt(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {
                "ok": True,
                "dwelling": {
                    "hunt": {"used": 3, "limit": 3, "remaining": 0, "actionPoints": 0},
                },
            }

        result = cave_treasure_miniapp.run_cave_treasure_miniapp_lab_flow(
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=random.Random(3),
        )
        text = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("daily_limit", result["status"])
        self.assertEqual(["start"], calls)
        self.assertEqual("daily_games_exhausted", result["events"][-1]["reason"])
        self.assertNotIn("df_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)

    def test_cave_treasure_reveal_app_error_is_sanitized_and_stops(self):
        calls = []
        capture = webapp_core.MiniAppCaptureStore()

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            payload = dict(request["payload"])
            calls.append((endpoint, payload.get("sessionId"), payload.get("index")))
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "huntRun": {
                        "sessionId": "hunt-expired",
                        "status": "active",
                        "size": 2,
                        "ap": 1,
                        "maxAp": 1,
                        "cells": [{"index": 0, "revealed": False}],
                    },
                }
            if endpoint == "hunt_reveal":
                return 200, {
                    "ok": False,
                    "error": "session expired token=df_SECRET999 hash=VERY_SECRET Authorization: Bearer SUPERSECRET",
                }
            return 404, {"ok": False, "error": "unexpected"}

        result = cave_treasure_miniapp.run_cave_treasure_miniapp_lab_flow(
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=random.Random(4),
            capture_sink=capture,
            capture_source="unit token=df_SECRET999 Authorization: Bearer SUPERSECRET",
        )
        text = json.dumps({"result": result, "capture": capture.records}, ensure_ascii=False)

        self.assertFalse(result["ok"])
        self.assertEqual("failed", result["status"])
        self.assertEqual([("start", None, None), ("hunt_reveal", "hunt-expired", 0)], calls)
        self.assertNotIn("df_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)
        self.assertNotIn("SUPERSECRET", text)
        self.assertIn("<redacted>", text)

    def test_tree_start_lab_flow_reads_state_without_gameplay_calls(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {
                "ok": True,
                "tree": {
                    "gameplayMode": "council",
                    "gameplayName": "云梦山灵眼赛",
                    "status": "growing",
                    "statusLabel": "每日双赛",
                    "maturity": 73.5,
                },
                "council": {
                    "daily": {
                        "jump": {"used": 2, "limit": 3, "best": 21},
                        "fly": {"used": 0, "limit": 3, "best": 0},
                    },
                    "season": {"seasonId": "lyz20260706", "status": "active", "dayIndex": 2},
                },
                "ranking": {
                    "myContributionPoints": 128,
                    "branchRank": 4,
                    "claimed": False,
                },
                "actions": {"canMeridian": True, "canHarvest": False},
            }

        result = tree_miniapp.run_tree_miniapp_start_lab_flow(
            token="tree_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            sleeper=lambda _delay: None,
        )
        text = json.dumps(result, ensure_ascii=False)
        state = result["data"]["state"]

        self.assertTrue(result["ok"])
        self.assertEqual("ready", result["status"])
        self.assertEqual(["start"], calls)
        self.assertEqual("council", state["gameplay_mode"])
        self.assertEqual("云梦山灵眼赛", state["gameplay_name"])
        self.assertEqual("lyz20260706", state["season_id"])
        self.assertEqual({"used": 2, "limit": 3, "remaining": 1, "best": 21}, state["jump"])
        self.assertEqual({"used": 0, "limit": 3, "remaining": 3, "best": 0}, state["fly"])
        self.assertTrue(state["can_run_game"])
        self.assertFalse(state["can_claim_reward"])
        self.assertTrue(state["actions"]["canMeridian"])
        self.assertFalse(state["actions"]["canHarvest"])
        self.assertNotIn("tree_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)

    def test_tree_failed_start_capture_is_sanitized(self):
        calls = []
        capture = webapp_core.MiniAppCaptureStore()

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {
                "ok": False,
                "error": "session expired token=tree_SECRET999 hash=VERY_SECRET Authorization: Bearer SUPERSECRET",
            }

        result = tree_miniapp.run_tree_miniapp_start_lab_flow(
            token="tree_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            sleeper=lambda _delay: None,
            capture_sink=capture,
            capture_source="unit token=tree_SECRET999 Authorization: Bearer SUPERSECRET",
        )
        text = json.dumps({"result": result, "capture": capture.records}, ensure_ascii=False)

        self.assertFalse(result["ok"])
        self.assertEqual("failed", result["status"])
        self.assertEqual(["start"], calls)
        self.assertNotIn("tree_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)
        self.assertNotIn("SUPERSECRET", text)
        self.assertIn("<redacted>", text)

    def test_tree_game_proofs_use_tens_targets_and_replay(self):
        fly_proof, fly_summary = tree_miniapp.build_tree_fly_proof(
            {"seed": "shape-test-seed-001", "runToken": "run-token-secret"},
            rng=random.Random(1),
            profile={"target_score": 30},
        )
        fly_replay = tree_miniapp.simulate_tree_fly_run(
            "shape-test-seed-001",
            fly_proof["flaps"],
            max_duration_ms=fly_proof["durationMs"],
        )
        jump_proof, jump_summary = tree_miniapp.build_tree_jump_proof(
            {"seed": "shape-test-seed-001", "runToken": "run-token-secret"},
            rng=random.Random(1),
            profile={"target_score": 30},
        )
        jump_replay = tree_miniapp.simulate_tree_jump_run("shape-test-seed-001", jump_proof["charges"])

        self.assertEqual(30, fly_summary["targetScore"])
        self.assertGreaterEqual(fly_summary["score"], 20)
        self.assertLessEqual(fly_summary["score"], 150)
        self.assertEqual(fly_proof["clientScore"], fly_replay["score"])
        self.assertTrue(all(isinstance(item, int) for item in fly_proof["flaps"]))
        self.assertGreater(fly_proof["durationMs"], 20_000)

        self.assertEqual(30, jump_summary["targetScore"])
        self.assertGreaterEqual(jump_summary["score"], 20)
        self.assertLessEqual(jump_summary["score"], 150)
        self.assertEqual(jump_proof["clientScore"], jump_replay["score"])
        self.assertTrue(all(isinstance(item, float) for item in jump_proof["charges"]))

    def test_tree_score_profile_clamps_to_tens_policy(self):
        self.assertEqual({"target_score_range": (20, 20)}, tree_miniapp.normalize_tree_score_profile("fly", {"target_score": 7}))
        self.assertEqual({"target_score_range": (150, 150)}, tree_miniapp.normalize_tree_score_profile("jump", {"target_score": 999}))
        self.assertEqual({"target_score_range": (24, 45)}, tree_miniapp.normalize_tree_score_profile("fly", {}))

    def test_tree_jump_proof_does_not_overshoot_score_cap(self):
        for index in range(6):
            seed = f"audit-seed-{index:02d}"
            proof, summary = tree_miniapp.build_tree_game_proof(
                "jump",
                {"seed": seed, "runToken": "run-token-secret"},
                rng=random.Random(1080 + index),
                profile={"target_score": 80},
            )
            replay = tree_miniapp.simulate_tree_jump_run(seed, proof["charges"])

            self.assertLessEqual(summary["score"], 80)
            self.assertEqual(proof["clientScore"], replay["score"])
            self.assertLessEqual(replay["score"], 80)

    def test_tree_jump_proof_can_target_126_for_manual_canary(self):
        proof, summary = tree_miniapp.build_tree_game_proof(
            "jump",
            {"seed": "luoyun-canary-seed-126", "runToken": "run-token-secret"},
            rng=random.Random(1260),
            profile={"target_score": 126},
        )
        replay = tree_miniapp.simulate_tree_jump_run("luoyun-canary-seed-126", proof["charges"])

        self.assertEqual(126, summary["targetScore"])
        self.assertGreaterEqual(summary["score"], 126)
        self.assertLessEqual(summary["score"], 150)
        self.assertEqual(proof["clientScore"], replay["score"])
        self.assertEqual(summary["score"], replay["score"])

    def test_tree_fly_proof_caps_expensive_planning_profile(self):
        proof, summary = tree_miniapp.build_tree_game_proof(
            "fly",
            {"seed": "audit-heavy-fly-seed", "runToken": "run-token-secret"},
            rng=random.Random(2200),
            profile={
                "target_score": 80,
                "beam_width": 10000,
                "max_duration_ms": 999999999,
                "max_plan_frames": 999999999,
            },
        )
        replay = tree_miniapp.simulate_tree_fly_run(
            "audit-heavy-fly-seed",
            proof["flaps"],
            max_duration_ms=proof["durationMs"],
        )

        self.assertEqual(tree_miniapp.TREE_MINIAPP_FLY_MAX_BEAM_WIDTH, summary["profile"]["beam_width"])
        self.assertEqual(tree_miniapp.TREE_MINIAPP_FLY_MAX_PLAN_DURATION_MS, summary["profile"]["max_duration_ms"])
        self.assertLessEqual(proof["durationMs"], tree_miniapp.TREE_MINIAPP_FLY_MAX_PLAN_DURATION_MS + 2_000)
        self.assertEqual(proof["clientScore"], replay["score"])
        self.assertGreaterEqual(replay["score"], 20)
        self.assertLessEqual(replay["score"], 150)

    def test_tree_game_lab_flow_prepares_without_submit(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "tree": {"gameplayMode": "council"},
                    "council": {
                        "daily": {
                            "jump": {"used": 0, "limit": 3, "best": 0},
                            "fly": {"used": 0, "limit": 3, "best": 0},
                        },
                        "season": {"seasonId": "lyz20260706", "status": "active"},
                    },
                }
            if endpoint == "run_start":
                self.assertEqual("fly", request["payload"]["mode"])
                return 200, {
                    "ok": True,
                    "run": {
                        "mode": "fly",
                        "runToken": "run_SECRET999",
                        "seed": "shape-test-seed-001",
                        "used": 1,
                        "limit": 3,
                        "runNo": 1,
                        "seasonId": "lyz20260706",
                    },
                }
            raise AssertionError(f"{endpoint} must not be called when submit=False")

        result = tree_miniapp.run_tree_miniapp_game_lab_flow(
            token="tree_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            mode="fly",
            submit=False,
            transport=transport,
            rng=random.Random(1),
            sleeper=lambda _delay: None,
            score_profile={"target_score": 30},
        )
        text = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("prepared", result["status"])
        self.assertEqual(["start", "run_start"], calls)
        self.assertEqual("fly", result["data"]["mode"])
        self.assertGreaterEqual(result["data"]["proof_summary"]["score"], 20)
        self.assertEqual([30, 30], result["data"]["score_profile"]["target_score_range"])
        self.assertNotIn("tree_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)
        self.assertNotIn("run_SECRET999", text)

    def test_tree_game_lab_flow_reports_mode_exhausted_without_run_start(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "tree": {"gameplayMode": "council"},
                    "council": {
                        "daily": {
                            "jump": {"used": 3, "limit": 3, "best": 21},
                            "fly": {"used": 2, "limit": 3, "best": 0},
                        },
                        "season": {"seasonId": "lyz20260706", "status": "active"},
                    },
                }
            raise AssertionError(f"{endpoint} must not be called when selected mode is exhausted")

        result = tree_miniapp.run_tree_miniapp_game_lab_flow(
            token="tree_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            mode="jump",
            submit=True,
            transport=transport,
            rng=random.Random(1),
            sleeper=lambda _delay: None,
            score_profile={"target_score": 126},
        )

        self.assertFalse(result["ok"])
        self.assertEqual("mode_exhausted", result["status"])
        self.assertEqual(["start"], calls)
        self.assertEqual("jump", result["data"]["mode"])
        self.assertEqual(0, result["data"]["state"]["jump"]["remaining"])
        self.assertEqual(1, result["data"]["state"]["fly"]["remaining"])

    def test_tree_game_lab_flow_submit_uses_mock_proof_shape(self):
        calls = []
        submitted_payload = {}

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "tree": {"gameplayMode": "council"},
                    "council": {
                        "daily": {
                            "jump": {"used": 0, "limit": 3, "best": 0},
                            "fly": {"used": 0, "limit": 3, "best": 0},
                        },
                        "season": {"seasonId": "lyz20260706", "status": "active"},
                    },
                }
            if endpoint == "run_start":
                return 200, {
                    "ok": True,
                    "run": {
                        "mode": "jump",
                        "runToken": "run_SECRET999",
                        "seed": "shape-test-seed-001",
                        "used": 1,
                        "limit": 3,
                        "runNo": 1,
                        "seasonId": "lyz20260706",
                    },
                }
            if endpoint == "run_submit":
                submitted_payload.update(request["payload"])
                proof = request["payload"]["proof"]
                return 200, {"ok": True, "score": proof["clientScore"], "seasonState": {"ready": True}}
            raise AssertionError(f"unexpected endpoint {endpoint}")

        result = tree_miniapp.run_tree_miniapp_game_lab_flow(
            token="tree_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            mode="jump",
            submit=True,
            transport=transport,
            rng=random.Random(1),
            sleeper=lambda _delay: None,
            score_profile={"target_score": 30},
        )
        text = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("settled", result["status"])
        self.assertEqual(["start", "run_start", "run_submit"], calls)
        self.assertEqual("jump", submitted_payload["mode"])
        self.assertEqual("run_SECRET999", submitted_payload["runToken"])
        self.assertEqual({"charges", "durationMs", "clientScore"}, set(submitted_payload["proof"]))
        self.assertGreaterEqual(submitted_payload["proof"]["clientScore"], 20)
        self.assertNotIn("tree_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)
        self.assertNotIn("run_SECRET999", text)

    def test_capture_store_direct_raw_dict_append_is_sanitized(self):
        store = webapp_core.MiniAppCaptureStore()

        safe = store.append({
            "source": "manual token=trial_SECRET999 Authorization: Bearer SUPERSECRET",
            "error": "initData=query_id%3Dabc%26hash%3DVERY_SECRET next=df_SECRET777",
            "request": {
                "payload": {
                    "token": "trial_SECRET999",
                    "initData": "query_id=abc&hash=VERY_SECRET&user=42",
                },
            },
        })
        text = json.dumps(safe, ensure_ascii=False)

        self.assertEqual(safe, store.records[0])
        self.assertNotIn("trial_SECRET999", text)
        self.assertNotIn("df_SECRET777", text)
        self.assertNotIn("VERY_SECRET", text)
        self.assertNotIn("SUPERSECRET", text)


if __name__ == "__main__":
    unittest.main()
