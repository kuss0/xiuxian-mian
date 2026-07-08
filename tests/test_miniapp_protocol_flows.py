import json
import random
import unittest

from model import webapp_core
from model.features import cave_treasure_miniapp, fishing_miniapp, stargazer_miniapp, tree_miniapp, trial_miniapp


class MiniAppProtocolFlowTests(unittest.TestCase):
    def test_mutating_miniapp_steps_do_not_retry_transient_failures(self):
        trial_calls = []

        def trial_transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            trial_calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "challenge": {
                        "challengeId": "trial-no-retry",
                        "sequence": ["p1"],
                        "points": [{"id": "p1", "x": 20, "y": 20}],
                        "minDurationMs": 20,
                        "maxDurationMs": 1000,
                    },
                }
            raise RuntimeError("finish transient after server side may have applied")

        trial_miniapp.run_trial_miniapp_lab_flow(
            token="trial_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=trial_transport,
            sleeper=lambda _delay: None,
            rng=random.Random(11),
        )
        self.assertEqual(["start", "finish"], trial_calls)

        fishing_calls = []

        def fishing_transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            fishing_calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "session": {"phase": "bite", "serverNow": 0, "biteAt": 0},
                    "challenge": {"challengeId": "fish-no-retry", "minDurationMs": 20, "maxDurationMs": 1000},
                }
            raise RuntimeError("finish transient after server side may have applied")

        fishing_miniapp.run_fishing_miniapp_lab_flow(
            token="fish_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=fishing_transport,
            sleeper=lambda _delay: None,
            rng=random.Random(12),
        )
        self.assertEqual(["start", "finish"], fishing_calls)

        cave_calls = []

        def cave_transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            cave_calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "huntRun": {
                        "sessionId": "hunt-no-retry",
                        "status": "active",
                        "size": 2,
                        "ap": 1,
                        "maxAp": 1,
                        "cells": [{"index": 0, "revealed": False}],
                    },
                }
            raise RuntimeError("action transient after server side may have applied")

        cave_treasure_miniapp.run_cave_treasure_miniapp_lab_flow(
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=cave_transport,
            sleeper=lambda _delay: None,
            rng=random.Random(13),
        )
        self.assertEqual(["start", "hunt_reveal"], cave_calls)

        stargazer_calls = []

        def stargazer_transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            stargazer_calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "domain": {
                        "mode": "stars",
                        "plots": [{"key": "slot1", "empty": False, "status": "可收集"}],
                    },
                }
            raise RuntimeError("action transient after server side may have applied")

        stargazer_miniapp.run_stargazer_miniapp_lab_flow(
            token="farm_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            star_choice="",
            transport=stargazer_transport,
            sleeper=lambda _delay: None,
        )
        self.assertEqual(["start", "action"], stargazer_calls)

        tree_calls = []

        def tree_transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            tree_calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "council": {
                        "daily": {
                            "jump": {"used": 0, "limit": 3, "best": 0},
                            "fly": {"used": 0, "limit": 3, "best": 0},
                        },
                        "season": {"seasonId": "lyz20260708", "status": "active"},
                    },
                }
            raise RuntimeError("run/start transient after server side may have applied")

        tree_miniapp.run_tree_miniapp_game_lab_flow(
            token="tree_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            mode="jump",
            submit=True,
            transport=tree_transport,
            sleeper=lambda _delay: None,
            rng=random.Random(14),
        )
        self.assertEqual(["start", "run_start"], tree_calls)

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

    def test_trial_nested_challenge_accepts_wxjerry_field_aliases(self):
        calls = []
        submitted_proof = {}

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "data": {
                        "trial": {
                            "challenge": {
                                "id": "alias-c1",
                                "type": "tianjiMeridianV1",
                                "answer": ["p1", "p2"],
                                "points": {
                                    "p1": {"key": "p1", "x": 12, "y": 34},
                                    "p2": {"name": "p2", "x": 56, "y": 78},
                                },
                                "minDurationMs": 20,
                                "maxDurationMs": 1000,
                            },
                        },
                    },
                }
            if endpoint == "finish":
                submitted_proof.update(request["payload"]["trialProof"])
                return 200, {"ok": True, "result": {"ready": True, "reward": 1}}
            raise AssertionError(f"unexpected endpoint {endpoint}")

        result = trial_miniapp.run_trial_miniapp_lab_flow(
            token="trial_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=random.Random(17),
            sleeper=lambda _delay: None,
        )
        text = json.dumps({"result": result, "proof": submitted_proof}, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("settled", result["status"])
        self.assertEqual(["start", "finish"], calls)
        self.assertEqual("alias-c1", submitted_proof["challengeId"])
        self.assertEqual("tianjiMeridianV1", submitted_proof["mode"])
        self.assertEqual(["p1", "p2"], submitted_proof["sequence"])
        self.assertEqual([12, 56], [tap["x"] for tap in submitted_proof["taps"]])
        self.assertNotIn("trial_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)

    def test_trial_meridian_accepts_dict_points_with_key_only_ids(self):
        proof = trial_miniapp.build_trial_proof(
            {
                "id": "meridian-key-only-1",
                "type": "tianjiMeridianV1",
                "answer": ["p1"],
                "points": {
                    "p1": {"x": 12, "y": 34},
                },
                "minDurationMs": 20,
                "maxDurationMs": 1000,
            },
            rng=random.Random(171),
        )

        self.assertEqual("meridian-key-only-1", proof["challengeId"])
        self.assertEqual([{"id": "p1", "x": 12, "y": 34}], proof["taps"])

    def test_trial_specialized_solvers_accept_wxjerry_id_type_aliases(self):
        calls = []
        submitted = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "challenge": {
                        "id": "lights-alias-1",
                        "type": "tianjiLightsOutV1",
                        "gridSize": 4,
                        "targetState": 1,
                        "cells": [1] * 16,
                        "minDurationMs": 20,
                        "maxDurationMs": 1000,
                    },
                }
            if endpoint == "finish":
                submitted.append(dict(request["payload"]["trialProof"]))
                return 200, {"ok": True}
            raise AssertionError(f"unexpected endpoint {endpoint}")

        result = trial_miniapp.run_trial_miniapp_lab_flow(
            token="trial_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=random.Random(18),
            sleeper=lambda _delay: None,
        )
        text = json.dumps({"result": result, "proof": submitted}, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual(["start", "finish"], calls)
        self.assertEqual("lights-alias-1", submitted[0]["challengeId"])
        self.assertEqual("tianjiLightsOutV1", submitted[0]["mode"])
        self.assertNotIn("trial_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)

    def test_trial_planarity_accepts_dict_nodes_and_key_ids(self):
        submitted_proof = {}

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "data": {
                        "challenge": {
                            "id": "planarity-alias-1",
                            "type": "tianjiPlanarityV1",
                            "nodes": {
                                "a": {"key": "a", "x": 20, "y": 20},
                                "b": {"key": "b", "x": 80, "y": 20},
                                "c": {"key": "c", "x": 80, "y": 80},
                                "d": {"key": "d", "x": 20, "y": 80},
                            },
                            "edges": {
                                "ab": {"source": "a", "target": "b"},
                                "bc": {"source": "b", "target": "c"},
                                "cd": {"source": "c", "target": "d"},
                                "da": {"source": "d", "target": "a"},
                            },
                            "minDurationMs": 20,
                            "maxDurationMs": 1000,
                        },
                    },
                }
            if endpoint == "finish":
                submitted_proof.update(request["payload"]["trialProof"])
                return 200, {"ok": True}
            raise AssertionError(f"unexpected endpoint {endpoint}")

        result = trial_miniapp.run_trial_miniapp_lab_flow(
            token="trial_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=random.Random(19),
            sleeper=lambda _delay: None,
        )
        text = json.dumps({"result": result, "proof": submitted_proof}, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("planarity-alias-1", submitted_proof["challengeId"])
        self.assertEqual("tianjiPlanarityV1", submitted_proof["mode"])
        self.assertEqual({"a", "b", "c", "d"}, set(submitted_proof["positions"]))
        self.assertNotIn("trial_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)

    def test_trial_planarity_accepts_dict_nodes_with_key_only_ids(self):
        proof = trial_miniapp.build_trial_proof(
            {
                "id": "planarity-key-only-1",
                "type": "tianjiPlanarityV1",
                "nodes": {
                    "a": {"x": 20, "y": 20},
                    "b": {"x": 80, "y": 20},
                    "c": {"x": 80, "y": 80},
                    "d": {"x": 20, "y": 80},
                },
                "edges": {
                    "ab": {"source": "a", "target": "b"},
                    "bc": {"source": "b", "target": "c"},
                    "cd": {"source": "c", "target": "d"},
                    "da": {"source": "d", "target": "a"},
                },
                "minDurationMs": 20,
                "maxDurationMs": 1000,
            },
            rng=random.Random(191),
        )

        self.assertEqual("planarity-key-only-1", proof["challengeId"])
        self.assertEqual({"a", "b", "c", "d"}, set(proof["positions"]))

    def test_trial_planarity_empty_nodes_fails(self):
        with self.assertRaisesRegex(ValueError, "no valid nodes"):
            trial_miniapp.build_trial_proof(
                {
                    "id": "planarity-empty-1",
                    "type": "tianjiPlanarityV1",
                    "nodes": {},
                    "edges": {},
                    "minDurationMs": 20,
                    "maxDurationMs": 1000,
                },
                rng=random.Random(192),
            )

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

    def test_cave_treasure_action_button_text_does_not_mean_treasure_found(self):
        state = cave_treasure_miniapp.parse_cave_treasure_state({
            "huntRun": {
                "sessionId": "hunt-active",
                "status": "active",
                "size": 2,
                "ap": 2,
                "maxAp": 8,
                "text": "可用操作：继续寻宝，或见好就收；若有收获可再来一次。",
                "cells": [
                    {"index": 0, "revealed": False},
                    {"index": 1, "revealed": False},
                ],
            },
        })
        decision = cave_treasure_miniapp.choose_cave_treasure_action(state, rng=random.Random(5))

        self.assertFalse(state["treasure_found"])
        self.assertEqual("search", decision["action"])

        found_state = cave_treasure_miniapp.parse_cave_treasure_state({
            "huntRun": {
                "sessionId": "hunt-found",
                "status": "active",
                "size": 2,
                "ap": 2,
                "maxAp": 8,
                "text": "你发现宝光大盛，主宝已现，可见好就收。",
            },
        })
        found_decision = cave_treasure_miniapp.choose_cave_treasure_action(found_state, rng=random.Random(5))

        self.assertTrue(found_state["treasure_found"])
        self.assertEqual("settle", found_decision["action"])

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

    def test_tree_game_proofs_use_low_anticheat_targets_and_replay(self):
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

        self.assertGreaterEqual(fly_summary["targetScore"], 14)
        self.assertLessEqual(fly_summary["targetScore"], 20)
        self.assertGreaterEqual(fly_summary["score"], 0)
        self.assertLessEqual(fly_summary["score"], 20)
        self.assertEqual(fly_proof["clientScore"], fly_replay["score"])
        self.assertTrue(all(isinstance(item, int) for item in fly_proof["flaps"]))
        self.assertGreater(fly_proof["durationMs"], 20_000)

        self.assertGreaterEqual(jump_summary["targetScore"], 14)
        self.assertLessEqual(jump_summary["targetScore"], 20)
        self.assertGreaterEqual(jump_summary["score"], 0)
        self.assertLessEqual(jump_summary["score"], 20)
        self.assertEqual(jump_proof["clientScore"], jump_replay["score"])
        self.assertTrue(all(isinstance(item, float) for item in jump_proof["charges"]))

    def test_tree_score_profile_clamps_to_low_anticheat_policy(self):
        self.assertEqual({"target_score_range": (4, 10)}, tree_miniapp.normalize_tree_score_profile("fly", {"target_score": 7}))
        self.assertEqual({"target_score_range": (14, 20)}, tree_miniapp.normalize_tree_score_profile("jump", {"target_score": 999}))
        self.assertEqual({"target_score_range": (14, 20)}, tree_miniapp.normalize_tree_score_profile("jump", {"target_score_range": [36, 36]}))
        self.assertEqual({"target_score_range": (14, 20)}, tree_miniapp.normalize_tree_score_profile("jump", {"target_score_range": [126, 126]}))
        self.assertEqual({"target_score_range": (8, 18)}, tree_miniapp.normalize_tree_score_profile("fly", {}))

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

            self.assertLessEqual(summary["targetScore"], 20)
            self.assertLessEqual(summary["score"], 20)
            self.assertEqual(proof["clientScore"], replay["score"])
            self.assertLessEqual(replay["score"], 20)

    def test_tree_jump_proof_clamps_manual_canary_to_safe_cap(self):
        proof, summary = tree_miniapp.build_tree_game_proof(
            "jump",
            {"seed": "luoyun-canary-seed-126", "runToken": "run-token-secret"},
            rng=random.Random(1260),
            profile={"target_score": 126},
        )
        replay = tree_miniapp.simulate_tree_jump_run("luoyun-canary-seed-126", proof["charges"])

        self.assertLessEqual(summary["targetScore"], 20)
        self.assertGreaterEqual(summary["targetScore"], 14)
        self.assertLessEqual(summary["score"], 20)
        self.assertEqual(proof["clientScore"], replay["score"])
        self.assertEqual(summary["score"], replay["score"])

    def test_tree_fixed_score_profile_is_randomized_at_execution(self):
        targets = set()
        for index in range(12):
            _proof, summary = tree_miniapp.build_tree_game_proof(
                "jump",
                {"seed": f"luoyun-anticheat-seed-{index}", "runToken": "run-token-secret"},
                rng=random.Random(2600 + index),
                profile={"target_score": 126},
            )
            targets.add(summary["targetScore"])
            self.assertGreaterEqual(summary["targetScore"], 14)
            self.assertLessEqual(summary["targetScore"], 20)

        self.assertGreater(len(targets), 1)

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
        self.assertGreaterEqual(replay["score"], 0)
        self.assertLessEqual(replay["score"], 20)

    def test_tree_fly_proof_uses_server_validation_frame(self):
        proof, summary = tree_miniapp.build_tree_game_proof(
            "fly",
            {"seed": "42a9f208fdcd34c63db6", "runToken": "run-token-secret"},
            rng=random.Random(3600),
            profile={"target_score": 36, "beam_width": 640, "max_duration_ms": 90_000},
        )
        replay = tree_miniapp.simulate_tree_fly_run(
            "42a9f208fdcd34c63db6",
            proof["flaps"],
            max_duration_ms=proof["durationMs"],
        )

        self.assertGreaterEqual(summary["targetScore"], 14)
        self.assertLessEqual(summary["targetScore"], 20)
        self.assertGreaterEqual(replay["score"], summary["targetScore"])
        self.assertEqual(proof["clientScore"], replay["score"])
        self.assertEqual(tree_miniapp.TREE_MINIAPP_FLY_FRAME_MS, summary["profile"]["frame_ms"])

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
        self.assertGreaterEqual(result["data"]["proof_summary"]["score"], 0)
        self.assertLessEqual(result["data"]["proof_summary"]["score"], 20)
        self.assertEqual([14, 20], result["data"]["score_profile"]["target_score_range"])
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
            score_profile={"target_score": 80},
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
        self.assertGreaterEqual(submitted_payload["proof"]["clientScore"], 0)
        self.assertLessEqual(submitted_payload["proof"]["clientScore"], 20)
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
