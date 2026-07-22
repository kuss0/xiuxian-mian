import json
import random
import unittest
from unittest.mock import patch

from model import webapp_core
from model.features import cave_treasure_miniapp, fishing_miniapp, stargazer_miniapp, tree_miniapp, trial_miniapp


class MiniAppProtocolFlowTests(unittest.TestCase):
    def test_cave_external_action_whitelist_accepts_dynamic_stargazer_and_tree(self):
        for action in ("sect_farm", "stargazer", "spirit_tree", "tree"):
            request = cave_treasure_miniapp.build_cave_external_action_request(
                action,
                token="df_SECRET999",
                player_id=-1008659059191,
                init_data="query_id=abc&hash=VERY_SECRET",
            )
            self.assertEqual(action, request["payload"]["action"])
        with self.assertRaises(ValueError):
            cave_treasure_miniapp.build_cave_external_action_request(
                "untrusted_external_action",
                token="df_SECRET999",
                player_id=-1008659059191,
                init_data="query_id=abc",
            )

    def test_cave_journey_wild_experience_uses_strict_mode_and_server_state(self):
        parsed = cave_treasure_miniapp.parse_cave_dwelling_overview({
            "ok": True,
            "account": {
                "journey": {
                    "serverTime": 1784500000000,
                    "wildExperience": {
                        "available": False,
                        "dailyCount": 1,
                        "dailyLimit": 2,
                        "dailyRemaining": 1,
                        "remainingSeconds": 43200,
                        "lastAt": 1784490000000,
                        "readyAt": 1784543200000,
                        "resetAt": 1784563200000,
                        "modes": [
                            {"key": "cautious", "label": "谨慎", "risk": "低", "reward": "偏低"},
                            {"key": "balanced", "label": "均衡", "risk": "中", "reward": "常规"},
                            {"key": "deep", "label": "深入", "risk": "高", "reward": "较高"},
                        ],
                    },
                },
            },
        })
        wild = parsed["journey"]["wild_experience"]
        self.assertFalse(wild["available"])
        self.assertEqual(43200, wild["remaining_seconds"])
        self.assertEqual(1784543200000, wild["ready_at"])
        self.assertEqual({"cautious", "balanced", "deep"}, {item["key"] for item in wild["modes"]})

        request = cave_treasure_miniapp.build_cave_journey_action_request(
            "wild_experience",
            mode="deep",
            token="df_SECRET999",
            player_id=8659059191,
            init_data="query_id=abc&hash=VERY_SECRET",
        )
        self.assertEqual("journey", request["safe_summary"]["endpoint"])
        self.assertEqual({"action", "mode", "playerId"}, set(request["payload"]) - {"token", "initData"})
        self.assertEqual("wild_experience", request["payload"]["action"])
        self.assertEqual("deep", request["payload"]["mode"])
        self.assertEqual(8659059191, request["payload"]["playerId"])
        self.assertNotIn("VERY_SECRET", json.dumps(request["safe_summary"], ensure_ascii=False))

        with self.assertRaises(ValueError):
            cave_treasure_miniapp.build_cave_journey_action_request(
                "wild_experience",
                mode="unsafe",
                token="df_SECRET999",
                init_data="query_id=abc",
            )

    def test_cave_small_world_parses_real_nested_dashboard_and_scoped_request(self):
        parsed = cave_treasure_miniapp.parse_cave_dwelling_overview({
            "ok": True,
            "account": {
                "smallWorld": {
                    "hasWorld": True,
                    "summary": {
                        "population": 900,
                        "populationCap": 1000,
                        "faith": 81,
                        "stability": 93,
                        "incensePoints": 500,
                        "uncollectedIncense": 12.5,
                        "hourlyIncense": 3.5,
                        "shenshiText": "8/8",
                    },
                    "actions": {
                        "canCollect": True,
                        "canManifest": True,
                        "edictRemainingSeconds": 0,
                        "barrierRemainingSeconds": 120,
                        "barrierCost": 80,
                    },
                    "temple": {"level": 3, "name": "山河神庙"},
                    "prayer": {
                        "title": "江河决堤",
                        "description": "洪水将至",
                        "successRate": 88,
                        "expiresInSeconds": 3600,
                        "cost": [{"name": "修为", "owned": 900, "required": 800, "missing": 0}],
                    },
                },
            },
        })

        world = parsed["small_world"]
        self.assertEqual(81, world["faith"])
        self.assertEqual(93, world["stability"])
        self.assertEqual(500, world["incense_stock"])
        self.assertEqual(12.5, world["pending_incense"])
        self.assertEqual("江河决堤", world["prayer_title"])
        self.assertTrue(world["prayer_resources_ready"])
        self.assertTrue(world["can_manifest"])
        self.assertTrue(world["barrier_active"])

        request = cave_treasure_miniapp.build_cave_small_world_action_request(
            "manifest",
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
        )
        self.assertEqual("small_world", request["safe_summary"]["endpoint"])
        self.assertEqual("manifest", request["payload"]["action"])
        self.assertEqual({"action"}, set(request["payload"]) - {"token", "initData"})
        self.assertNotIn("VERY_SECRET", json.dumps(request["safe_summary"], ensure_ascii=False))

        external_request = cave_treasure_miniapp.build_cave_external_action_request(
            "trial",
            token="df_SECRET999",
            player_id=8659059191,
            init_data="query_id=abc&hash=VERY_SECRET",
        )
        self.assertEqual("external", external_request["safe_summary"]["endpoint"])
        self.assertEqual("trial", external_request["payload"]["action"])
        self.assertEqual("8659059191", external_request["payload"]["playerId"])
        self.assertEqual({"action", "playerId"}, set(external_request["payload"]) - {"token", "initData"})

    def test_cave_dwelling_overview_parses_new_dashboard_without_url_tokens(self):
        parsed = cave_treasure_miniapp.parse_cave_dwelling_overview({
            "ok": True,
            "account": {
                "playerId": 8659059191,
                "username": "WalterWA2000",
                "daoName": "清源子",
                "sectName": "天星宗",
                "cultivationLevel": "化神后期大圆满",
                "commandCenter": {
                    "security": {
                        "mode": "whitelist_only",
                        "directRawCommand": False,
                        "maxInputLength": 80,
                        "text": "天机阁只识别白名单指令，并映射到洞府内已有接口或入口；不会把任意文本直通给 bot。",
                    },
                    "entries": [{
                        "key": "formation_self",
                        "title": "阵法 / 隐修 / 御宝加持",
                        "status": "integrated",
                        "targetTab": "command",
                        "buttonText": "到天机阁",
                        "note": "个人阵法、隐修状态和临时御宝加持可直接在天机阁执行。",
                        "commands": [".我的阵法", ".布阵", ".撤阵", ".吐纳养法", ".避世", ".入世"],
                    }, {
                        "key": "journey",
                        "title": "钓鱼 / 天机试炼 / 赛事",
                        "buttonText": "看外府",
                        "commands": [".钓鱼", ".天机试炼", ".诸天杯"],
                    }, {
                        "key": "sect_tasks",
                        "title": "宗门任务 / 外交 / 俸禄",
                        "status": "pending",
                        "buttonText": "待迁移",
                        "note": "点卯已迁入天机阁；任务提交与外交仍待迁移。",
                        "commands": [".宗门任务", ".提交任务", ".宗门外交"],
                    }, {
                        "key": "sect_target_magic",
                        "title": "夺舍 / 咒术 / 双修邀约",
                        "status": "group_only",
                        "buttonText": "群内保留",
                        "note": "暂不从天机阁输入框直通。",
                        "commands": [".夺舍", ".下咒"],
                    }],
                },
                "externalApps": {
                    "groups": [{
                        "key": "sect_farm",
                        "title": "宗门灵圃",
                        "apps": [{
                            "key": "sect_farm_locked",
                            "title": "宗门灵圃",
                            "buttonText": "查看灵圃",
                            "status": "ready",
                            "available": True,
                            "url": "/miniapp/xianxia-sect-farm?startapp=farm_SECRET999",
                        }, {
                            "key": "tianji_trial",
                            "title": "天机试炼",
                            "buttonText": "进入试炼",
                            "status": "ready",
                            "available": True,
                            "url": "https://t.me/fanrenxiuxian_bot?startapp=trial_SECRET999",
                        }],
                    }],
                },
                "smallWorld": {
                    "level": 3,
                    "templeLevel": 3,
                    "population": 190000,
                    "faith": 92,
                    "faithCap": 100,
                    "stability": 100,
                    "stabilityCap": 100,
                    "pendingIncense": 2103.34,
                    "incenseStock": 20785,
                    "prayer": {"title": "江河决堤", "status": "pending"},
                    "barrier": {"active": True},
                    "actions": {"canManifest": True, "canHarvest": False, "canBarrier": True},
                },
            },
            "identity": {"selectedPlayerId": 8659059191},
            "dwelling": {
                "hasDwelling": True,
                "lingqiPool": 503110.3,
                "lingqiPct": 83.85,
                "productionHint": 2975.0,
                "visualCapacity": 600000.0,
                "formation": {"active": True, "level": 5, "mode": "聚灵", "title": "聚灵阵势"},
                "hunt": {"used": 0, "limit": 3, "remaining": 3, "actionPoints": 8},
                "meditation": {
                    "canSettle": True,
                    "projectedGain": 9,
                    "consumableLingqi": 2.0,
                    "reason": "ready",
                    "reasonText": "静室可结算本次灵气沉淀。",
                    "deepSeclusion": {
                        "active": True,
                        "completed": False,
                        "canStart": False,
                        "canForceExit": True,
                        "canSettle": False,
                        "remainingSeconds": 107536,
                        "endMs": 1783721290498,
                        "statusText": "闭关中，剩余 29小时52分。",
                    },
                    "standardCultivation": {
                        "canCultivate": False,
                        "reason": "deep_seclusion_active",
                        "cooldownRemainingSeconds": 0,
                        "deepSeclusionActive": True,
                    },
                },
            },
        })
        text = json.dumps(parsed, ensure_ascii=False)

        self.assertTrue(parsed["ok"])
        self.assertEqual(8659059191, parsed["player_id"])
        self.assertEqual("WalterWA2000", parsed["username"])
        self.assertEqual("天星宗", parsed["sect_name"])
        self.assertTrue(parsed["has_dwelling"])
        self.assertEqual(3, parsed["hunt"]["limit"])
        self.assertEqual(8, parsed["hunt"]["action_points"])
        self.assertTrue(parsed["meditation"]["can_settle"])
        self.assertTrue(parsed["deep_seclusion"]["active"])
        self.assertFalse(parsed["deep_seclusion"]["can_start"])
        self.assertTrue(parsed["deep_seclusion"]["can_force_exit"])
        self.assertFalse(parsed["standard_cultivation"]["can_cultivate"])
        self.assertEqual(92, parsed["small_world"]["faith"])
        self.assertEqual(100, parsed["small_world"]["stability"])
        self.assertEqual("江河决堤", parsed["small_world"]["prayer_title"])
        self.assertTrue(parsed["small_world"]["barrier_active"])
        self.assertTrue(parsed["small_world"]["can_manifest"])
        self.assertEqual("whitelist_only", parsed["command_center"]["security"]["mode"])
        self.assertFalse(parsed["command_center"]["security"]["direct_raw_command"])
        self.assertEqual(4, parsed["command_center"]["entry_count"])
        self.assertEqual(["formation_self"], [item["key"] for item in parsed["command_center"]["tianjige_entries"]])
        self.assertEqual(".布阵", parsed["command_center"]["tianjige_entries"][0]["commands"][1])
        self.assertEqual(["farm", "trial"], [app["start_kind"] for app in parsed["external_apps"]])
        self.assertNotIn("farm_SECRET999", text)
        self.assertNotIn("trial_SECRET999", text)

    def test_cave_deep_seclusion_action_request_is_scoped_to_action_only(self):
        request = cave_treasure_miniapp.build_cave_deep_seclusion_action_request(
            "settle",
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
        )

        self.assertEqual("deep_seclusion", request["safe_summary"]["endpoint"])
        self.assertEqual("df_SECRET999", request["payload"]["token"])
        self.assertEqual("settle", request["payload"]["action"])
        self.assertIn("initData", request["payload"])
        self.assertEqual({"action"}, set(request["payload"]) - {"token", "initData"})
        self.assertNotIn("VERY_SECRET", json.dumps(request["safe_summary"], ensure_ascii=False))
        with self.assertRaises(ValueError):
            cave_treasure_miniapp.build_cave_deep_seclusion_action_request(
                "unknown",
                token="df_SECRET999",
                init_data="query_id=abc&hash=VERY_SECRET",
            )

    def test_cave_tianjige_command_request_is_strictly_whitelisted(self):
        request = cave_treasure_miniapp.build_cave_tianjige_command_request(
            ".元婴出窍",
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
        )

        self.assertEqual("command_center", request["safe_summary"]["endpoint"])
        self.assertEqual(".元婴出窍", request["payload"]["command"])
        self.assertEqual("df_SECRET999", request["payload"]["token"])
        self.assertIn("initData", request["payload"])
        self.assertEqual({"command"}, set(request["payload"]) - {"token", "initData"})
        self.assertNotIn("VERY_SECRET", json.dumps(request["safe_summary"], ensure_ascii=False))
        self.assertNotIn("df_SECRET999", json.dumps(request["safe_summary"], ensure_ascii=False))

        status_request = cave_treasure_miniapp.build_cave_tianjige_command_request(
            ".元婴状态",
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            player_id=8659059191,
        )
        self.assertEqual(".元婴状态", status_request["payload"]["command"])
        self.assertEqual(8659059191, status_request["payload"]["playerId"])

        tianti_request = cave_treasure_miniapp.build_cave_tianjige_command_request(
            ".天阶状态",
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
        )
        self.assertEqual(".天阶状态", tianti_request["payload"]["command"])

        with self.assertRaises(ValueError):
            cave_treasure_miniapp.build_cave_tianjige_command_request(
                ".闭关修炼",
                token="df_SECRET999",
                init_data="query_id=abc&hash=VERY_SECRET",
            )

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

    def test_trial_loop_uses_finish_embedded_next_challenge(self):
        calls = []
        submitted_ids = []

        def challenge(challenge_id):
            return {
                "challengeId": challenge_id,
                "mode": "tianjiMeridianV1",
                "sequence": ["p1"],
                "points": [{"id": "p1", "x": 12, "y": 34}],
                "minDurationMs": 20,
                "maxDurationMs": 1000,
            }

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "challenge": challenge("trial-1"),
                    "trial": {"dailyLimit": 3, "remainingToday": 3},
                }
            if endpoint == "finish":
                challenge_id = request["payload"]["trialProof"]["challengeId"]
                submitted_ids.append(challenge_id)
                if challenge_id == "trial-1":
                    return 200, {
                        "ok": True,
                        "dailyProgress": {"completed": 1, "limit": 3, "remaining": 2},
                        "nextChallenge": challenge("trial-2"),
                        "nextTrial": {"dailyLimit": 3, "remainingToday": 2},
                        "result": {"traceGain": 3},
                    }
                if challenge_id == "trial-2":
                    return 200, {
                        "ok": True,
                        "dailyProgress": {"completed": 2, "limit": 3, "remaining": 1},
                        "nextChallenge": challenge("trial-3"),
                        "nextTrial": {"dailyLimit": 3, "remainingToday": 1},
                        "result": {"traceGain": 4},
                    }
                return 200, {
                    "ok": True,
                    "dailyProgress": {"completed": 3, "limit": 3, "remaining": 0},
                    "result": {"traceGain": 5},
                }
            raise AssertionError(f"unexpected endpoint {endpoint}")

        result = trial_miniapp.run_trial_miniapp_loop_lab_flow(
            token="trial_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=random.Random(172),
            sleeper=lambda _delay: None,
            max_rounds=99,
        )
        text = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual("settled", result["status"])
        self.assertEqual(["start", "finish", "finish", "finish"], calls)
        self.assertEqual(["trial-1", "trial-2", "trial-3"], submitted_ids)
        self.assertEqual(3, result["data"]["settled_count"])
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

    def test_cave_treasure_start_carries_explicit_selected_player_id(self):
        start_payloads = []

        def transport(request):
            start_payloads.append(dict(request["payload"]))
            return 200, {
                "ok": True,
                "dwelling": {"hunt": {"used": 3, "limit": 3, "remaining": 0, "actionPoints": 0}},
            }

        result = cave_treasure_miniapp.run_cave_treasure_miniapp_lab_flow(
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            player_id=-1003820064579,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(-1003820064579, start_payloads[0]["playerId"])

    def test_cave_treasure_huntrun_only_replies_keep_daily_counter_context(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {"ok": True, "dwelling": {"hunt": {"used": 0, "limit": 3, "remaining": 3, "actionPoints": 8}}}
            if endpoint == "hunt":
                return 200, {
                    "ok": True,
                    "dwelling": {"hunt": {"used": 1, "limit": 3, "remaining": 2, "actionPoints": 8}},
                    "huntRun": {
                        "sessionId": "hunt-context",
                        "status": "active",
                        "size": 2,
                        "ap": 1,
                        "maxAp": 8,
                        "cells": [{"index": 0, "revealed": False}],
                    },
                }
            if endpoint == "hunt_reveal":
                return 200, {
                    "ok": True,
                    "huntRun": {
                        "sessionId": "hunt-context",
                        "status": "active",
                        "size": 2,
                        "ap": 0,
                        "maxAp": 8,
                        "foundMain": True,
                    },
                }
            if endpoint == "hunt_settle":
                return 200, {
                    "ok": True,
                    "dwelling": {"hunt": {"used": 1, "limit": 3, "remaining": 2, "actionPoints": 8}},
                    "huntResult": {"loot": [{"name": "灵石", "quantity": 1}]},
                }
            return 404, {"ok": False}

        result = cave_treasure_miniapp.run_cave_treasure_miniapp_lab_flow(
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=random.Random(15),
            max_steps=3,
        )
        games = [event.get("games") for event in result["events"] if event.get("step") == "decide"]

        self.assertEqual(["start", "hunt", "hunt_reveal", "hunt_settle"], calls)
        self.assertNotIn("0/0", games)
        self.assertIn("1/3", games)

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

    def test_cave_treasure_latest_hint_skips_revealed_target(self):
        state = cave_treasure_miniapp.parse_cave_treasure_state({
            "huntRun": {
                "sessionId": "hunt-hint",
                "status": "active",
                "size": 3,
                "ap": 2,
                "maxAp": 8,
                "latestHint": {
                    "markers": [
                        {"index": 4, "kind": "treasure"},
                        {"index": 5, "kind": "resource"},
                    ],
                },
                "cells": [
                    {"index": 4, "revealed": True},
                    {"index": 5, "revealed": False},
                    {"index": 6, "revealed": False},
                ],
            },
        })
        decision = cave_treasure_miniapp.choose_cave_treasure_action(state, rng=random.Random(5))
        request = cave_treasure_miniapp.build_cave_treasure_action_request(
            decision,
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
        )

        self.assertEqual(6, state["hint_target"])
        self.assertEqual("search", decision["action"])
        self.assertEqual("hint_target", decision["reason"])
        self.assertEqual(6, decision["targetIndex"])
        self.assertEqual("hunt_reveal", request["safe_summary"]["endpoint"])
        self.assertEqual(5, request["payload"]["index"])

    def test_cave_treasure_latest_hint_prefers_available_treasure_marker(self):
        state = cave_treasure_miniapp.parse_cave_treasure_state({
            "huntRun": {
                "sessionId": "hunt-priority",
                "status": "active",
                "size": 2,
                "ap": 2,
                "maxAp": 8,
                "latestHint": {
                    "markers": [
                        {"index": 1, "kind": "resource"},
                        {"index": 2, "kind": "treasure"},
                    ],
                },
                "cells": [
                    {"index": 1, "revealed": False},
                    {"index": 2, "revealed": False},
                    {"index": 3, "revealed": False},
                ],
            },
        })
        decision = cave_treasure_miniapp.choose_cave_treasure_action(state, rng=random.Random(6))

        self.assertEqual(3, state["hint_target"])
        self.assertEqual("search", decision["action"])
        self.assertEqual("hint_target", decision["reason"])
        self.assertEqual(3, decision["targetIndex"])

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

    def test_cave_treasure_unknown_mutation_is_not_retried(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "dwelling": {"hunt": {"used": 0, "limit": 3, "remaining": 3, "actionPoints": 8}},
                }
            if endpoint == "hunt":
                raise TimeoutError("unknown after send")
            return 404, {"ok": False, "error": "unexpected"}

        result = cave_treasure_miniapp.run_cave_treasure_miniapp_lab_flow(
            token="df_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            rng=random.Random(9),
        )

        self.assertFalse(result["ok"])
        self.assertEqual("result_unknown", result["status"])
        self.assertEqual("enter", result["data"]["uncertain_action"])
        self.assertTrue(result["data"]["state"]["outcome_unknown"])
        self.assertEqual(["start", "hunt"], calls)

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
        jump_proof, jump_summary = tree_miniapp.build_tree_jump_proof(
            {"seed": "shape-test-seed-001", "runToken": "run-token-secret"},
            rng=random.Random(1),
            profile={"target_score": 30},
        )
        fly_replay = tree_miniapp.simulate_tree_fly_run(
            "shape-test-seed-001",
            fly_proof["flaps"],
            max_duration_ms=fly_proof["durationMs"],
        )
        jump_replay = tree_miniapp.simulate_tree_jump_run("shape-test-seed-001", jump_proof["charges"])

        self.assertGreaterEqual(fly_summary["targetScore"], 16)
        self.assertLessEqual(fly_summary["targetScore"], 20)
        self.assertGreaterEqual(fly_summary["score"], 0)
        self.assertLessEqual(fly_summary["score"], 20)
        self.assertEqual(fly_proof["clientScore"], fly_summary["profile"]["planned_score"])
        self.assertEqual(fly_proof["clientScore"], fly_summary["profile"]["replay_score"])
        self.assertEqual(fly_proof["clientScore"], fly_replay["score"])
        self.assertEqual(0, fly_proof["flaps"][0])
        self.assertTrue(all(isinstance(item, int) for item in fly_proof["flaps"]))
        self.assertGreater(fly_proof["durationMs"], 20_000)

        self.assertGreaterEqual(jump_summary["targetScore"], 27)
        self.assertLessEqual(jump_summary["targetScore"], 33)
        self.assertGreaterEqual(jump_summary["score"], 0)
        self.assertLessEqual(jump_summary["score"], 33)
        self.assertEqual(jump_proof["clientScore"], jump_replay["score"])
        self.assertTrue(all(isinstance(item, float) for item in jump_proof["charges"]))

    def test_tree_score_profile_clamps_to_low_anticheat_policy(self):
        self.assertEqual({"target_score_range": (5, 9)}, tree_miniapp.normalize_tree_score_profile("fly", {"target_score": 7}))
        self.assertEqual({"target_score_range": (90, 96)}, tree_miniapp.normalize_tree_score_profile("jump", {"target_score": 999}))
        self.assertEqual({"target_score_range": (33, 39)}, tree_miniapp.normalize_tree_score_profile("jump", {"target_score_range": [36, 36]}))
        self.assertEqual({"target_score_range": (90, 96)}, tree_miniapp.normalize_tree_score_profile("jump", {"target_score_range": [126, 126]}))
        self.assertEqual({"target_score_range": (8, 12)}, tree_miniapp.normalize_tree_score_profile("fly", {}))

    def test_tree_score_records_migrate_legacy_jump_default(self):
        self.assertEqual(
            {
                "4319360789": {
                    "jump": {"target_score_range": (72, 78)},
                    "fly": {"target_score_range": (8, 12)},
                }
            },
            tree_miniapp.normalize_tree_score_records(
                {
                    "4319360789": {
                        "jump": {"target_score_range": [30, 36]},
                        "fly": {"target_score_range": [8, 12]},
                    }
                }
            ),
        )

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

            self.assertLessEqual(summary["targetScore"], 96)
            self.assertLessEqual(summary["score"], 96)
            self.assertEqual(proof["clientScore"], replay["score"])
            self.assertLessEqual(replay["score"], 96)

    def test_tree_jump_proof_clamps_manual_canary_to_safe_cap(self):
        proof, summary = tree_miniapp.build_tree_game_proof(
            "jump",
            {"seed": "luoyun-canary-seed-126", "runToken": "run-token-secret"},
            rng=random.Random(1260),
            profile={"target_score": 126},
        )
        replay = tree_miniapp.simulate_tree_jump_run("luoyun-canary-seed-126", proof["charges"])

        self.assertLessEqual(summary["targetScore"], 96)
        self.assertGreaterEqual(summary["targetScore"], 90)
        self.assertLessEqual(summary["score"], 96)
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
            self.assertGreaterEqual(summary["targetScore"], 90)
            self.assertLessEqual(summary["targetScore"], 96)

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
        self.assertEqual(tree_miniapp.TREE_MINIAPP_FLY_MAX_BEAM_WIDTH, summary["profile"]["beam_width"])
        self.assertEqual(tree_miniapp.TREE_MINIAPP_FLY_MAX_PLAN_DURATION_MS, summary["profile"]["max_duration_ms"])
        self.assertLessEqual(proof["durationMs"], tree_miniapp.TREE_MINIAPP_FLY_MAX_PLAN_DURATION_MS + 2_000)
        self.assertEqual(proof["clientScore"], summary["profile"]["planned_score"])
        self.assertGreater(proof["clientScore"], 0)
        self.assertLessEqual(proof["clientScore"], 20)
        self.assertEqual(0, proof["flaps"][0])

    def test_tree_fly_proof_uses_server_validation_frame(self):
        proof, summary = tree_miniapp.build_tree_game_proof(
            "fly",
            {"seed": "42a9f208fdcd34c63db6", "runToken": "run-token-secret"},
            rng=random.Random(3600),
            profile={"target_score": 36, "beam_width": 640, "max_duration_ms": 90_000},
        )
        self.assertGreaterEqual(summary["targetScore"], 16)
        self.assertLessEqual(summary["targetScore"], 20)
        self.assertGreaterEqual(proof["clientScore"], summary["targetScore"])
        self.assertEqual(proof["clientScore"], summary["profile"]["planned_score"])
        replay = tree_miniapp.simulate_tree_fly_run(
            "42a9f208fdcd34c63db6",
            proof["flaps"],
            max_duration_ms=proof["durationMs"],
        )
        self.assertEqual(proof["clientScore"], replay["score"])
        self.assertEqual(tree_miniapp.TREE_MINIAPP_FLY_FRAME_MS, summary["profile"]["frame_ms"])

    def test_tree_fly_consumed_proof_matches_server_at_16ms(self):
        seed = "b3bc0cc89467844fbc30"
        flaps = [
            0, 1133, 1666, 2466, 3266, 4549, 4883, 5983, 6333, 7466,
            7883, 8716, 9366, 10149, 10466, 11616, 12416, 13283, 14283,
            15116, 16066, 16399, 16616, 17683, 17899, 19049, 20000,
            20866, 21033, 21366, 22500, 23050,
        ]

        server_replay = tree_miniapp.simulate_tree_fly_run(
            seed,
            flaps,
            max_duration_ms=23067,
            frame_ms=16.0,
        )
        browser_replay = tree_miniapp.simulate_tree_fly_run(
            seed,
            flaps,
            max_duration_ms=23067,
            frame_ms=1000.0 / 60.0,
        )

        self.assertEqual(8, server_replay["score"])
        self.assertEqual(17, browser_replay["score"])

    def test_tree_ranking_target_aims_near_third_place_without_chasing_over_cap(self):
        data = {
            "ranking": {
                "branchTop": [
                    {"jump": 19, "fly": 40},
                    {"jump": 15, "fly": 30},
                    {"jump": 12, "fly": 25},
                    {"jump": 10, "fly": 18, "self": True},
                ]
            }
        }

        jump = tree_miniapp.tree_miniapp_ranking_target(data, "jump", {"target_score": 8})
        fly = tree_miniapp.tree_miniapp_ranking_target(data, "fly", {"target_score": 16})

        self.assertEqual(13, jump["target_score"])
        self.assertEqual(3, jump["reference_rank"])
        self.assertTrue(jump["chase_possible"])
        self.assertEqual([19, 15, 12], jump["top_scores"])
        self.assertEqual(16, fly["target_score"])
        self.assertFalse(fly["chase_possible"])

    def test_tree_ranking_target_does_not_tie_or_chase_first_place(self):
        tied = {
            "ranking": {
                "branchTop": [
                    {"jump": 13},
                    {"jump": 13},
                    {"jump": 12},
                ]
            }
        }
        compressed = {
            "ranking": {
                "branchTop": [
                    {"jump": 13},
                    {"jump": 12},
                    {"jump": 12},
                ]
            }
        }

        tied_target = tree_miniapp.tree_miniapp_ranking_target(tied, "jump", {"target_score": 8})
        compressed_target = tree_miniapp.tree_miniapp_ranking_target(compressed, "jump", {"target_score": 8})

        self.assertEqual(12, tied_target["target_score"])
        self.assertTrue(tied_target["chase_possible"])
        self.assertEqual(12, compressed_target["target_score"])
        self.assertTrue(compressed_target["chase_possible"])

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
        self.assertEqual([16, 20], result["data"]["score_profile"]["target_score_range"])
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
        self.assertLessEqual(submitted_payload["proof"]["clientScore"], 33)
        self.assertNotIn("tree_SECRET999", text)
        self.assertNotIn("VERY_SECRET", text)
        self.assertNotIn("run_SECRET999", text)

    def test_tree_game_lab_flow_rejects_failed_server_verification(self):
        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            if endpoint == "start":
                return 200, {"ok": True, "council": {"daily": {
                    "jump": {"used": 0, "limit": 1},
                    "fly": {"used": 0, "limit": 1},
                }}}
            if endpoint == "run_start":
                return 200, {"ok": True, "run": {
                    "mode": "fly",
                    "runToken": "run_SECRET999",
                    "seed": "server-verification-seed",
                    "used": 1,
                    "limit": 1,
                }}
            if endpoint == "run_submit":
                return 200, {
                    "ok": True,
                    "score": 18,
                    "verified": {"ok": False, "hit": True, "score": 0, "durationMs": 4567},
                }
            raise AssertionError(endpoint)

        with patch.object(
            tree_miniapp,
            "build_tree_game_proof",
            return_value=(
                {"flaps": [0, 200], "durationMs": 1000, "clientScore": 18},
                {"mode": "fly", "targetScore": 18, "score": 18, "durationMs": 1000},
            ),
        ):
            result = tree_miniapp.run_tree_miniapp_game_lab_flow(
                token="tree_SECRET999",
                init_data="query_id=abc&hash=VERY_SECRET",
                mode="fly",
                submit=True,
                transport=transport,
                sleeper=lambda _delay: None,
            )

        self.assertFalse(result["ok"])
        self.assertEqual("zero_score", result["status"])
        self.assertEqual(0, result["data"]["submit"]["score"])
        self.assertEqual(
            {"ok": False, "hit": True, "score": 0, "durationMs": 4567},
            result["data"]["submit"]["server_verification"],
        )

    def test_tree_game_lab_flow_blocks_zero_score_submit(self):
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
                return 200, {
                    "ok": True,
                    "run": {
                        "mode": "jump",
                        "runToken": "run_SECRET999",
                        "seed": "zero-score-seed",
                        "used": 1,
                        "limit": 3,
                        "runNo": 1,
                        "seasonId": "lyz20260706",
                    },
                }
            raise AssertionError(f"{endpoint} must not be called when local score is zero")

        with patch.object(
            tree_miniapp,
            "build_tree_game_proof",
            return_value=(
                {"charges": [0.1], "durationMs": 1200, "clientScore": 0},
                {"mode": "jump", "targetScore": 4, "score": 0, "durationMs": 1200},
            ),
        ):
            result = tree_miniapp.run_tree_miniapp_game_lab_flow(
                token="tree_SECRET999",
                init_data="query_id=abc&hash=VERY_SECRET",
                mode="jump",
                submit=True,
                transport=transport,
                rng=random.Random(1),
                sleeper=lambda _delay: None,
                score_profile={"target_score": 4},
            )

        self.assertFalse(result["ok"])
        self.assertEqual("unsafe_score", result["status"])
        self.assertEqual(["start", "run_start"], calls)
        self.assertEqual(0, result["data"]["proof_summary"]["score"])

    def test_tree_daily_flow_runs_dynamic_jump_then_fly_quota_to_completion(self):
        calls = []
        used = {"jump": 0, "fly": 0}
        limits = {"jump": 2, "fly": 1}

        def state_payload():
            return {
                "ok": True,
                "council": {
                    "daily": {
                        mode: {
                            "used": used[mode],
                            "limit": limits[mode],
                            "remaining": limits[mode] - used[mode],
                        }
                        for mode in ("jump", "fly")
                    },
                },
            }

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            mode = request["payload"].get("mode", "")
            calls.append((endpoint, mode))
            if endpoint == "start":
                return 200, state_payload()
            if endpoint == "run_start":
                return 200, {
                    "ok": True,
                    "run": {
                        "mode": mode,
                        "runToken": f"run_{mode}_{used[mode]}",
                        "seed": f"seed-{mode}-{used[mode]}",
                    },
                }
            if endpoint == "run_submit":
                used[mode] += 1
                return 200, {
                    "ok": True,
                    "score": request["payload"]["proof"]["clientScore"],
                    "rewards": [{"name": "灵木", "qty": 1}],
                }
            raise AssertionError(endpoint)

        with patch.object(
            tree_miniapp,
            "build_tree_game_proof",
            side_effect=lambda mode, _run, **_kwargs: (
                {"durationMs": 1, "clientScore": 18, "charges": [0.5]} if mode == "jump" else {"durationMs": 1, "clientScore": 17, "flaps": [100]},
                {"mode": mode, "score": 18 if mode == "jump" else 17, "targetScore": 18 if mode == "jump" else 17, "durationMs": 1},
            ),
        ):
            result = tree_miniapp.run_tree_miniapp_daily_lab_flow(
                token="tree_SECRET999",
                init_data="query_id=abc&hash=VERY_SECRET",
                transport=transport,
                sleeper=lambda _delay: None,
                rng=random.Random(1),
            )

        self.assertTrue(result["ok"])
        self.assertEqual("completed", result["status"])
        self.assertEqual("completed", result["data"]["phase"])
        self.assertEqual(0, result["data"]["quotas"]["jump"]["remaining"])
        self.assertEqual(0, result["data"]["quotas"]["fly"]["remaining"])
        self.assertEqual(["jump", "jump", "fly"], [item["mode"] for item in result["data"]["runs"]])
        self.assertEqual({"灵木": 3}, result["data"]["rewards"]["items"])
        run_modes = [mode for endpoint, mode in calls if endpoint == "run_start"]
        self.assertEqual(["jump", "jump", "fly"], run_modes)

    def test_tree_daily_flow_uses_submit_quota_without_extra_reconcile(self):
        calls = []

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            mode = request["payload"].get("mode", "")
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "council": {"daily": {
                        "jump": {"used": 0, "limit": 1, "remaining": 1},
                        "fly": {"used": 1, "limit": 1, "remaining": 0},
                    }},
                }
            if endpoint == "run_start":
                return 200, {"ok": True, "run": {"runToken": "run-once", "seed": "seed-once"}}
            if endpoint == "run_submit":
                return 200, {
                    "ok": True,
                    "score": 16,
                    "seasonState": {"daily": {
                        "jump": {"used": 1, "limit": 1, "remaining": 0},
                        "fly": {"used": 1, "limit": 1, "remaining": 0},
                    }},
                }
            raise AssertionError((endpoint, mode))

        with patch.object(
            tree_miniapp,
            "build_tree_game_proof",
            return_value=(
                {"durationMs": 1, "clientScore": 16, "charges": [0.5]},
                {"mode": "jump", "score": 16, "targetScore": 16, "durationMs": 1},
            ),
        ):
            result = tree_miniapp.run_tree_miniapp_daily_lab_flow(
                token="tree_SECRET999",
                init_data="query_id=abc&hash=VERY_SECRET",
                transport=transport,
                sleeper=lambda _delay: None,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(["start", "run_start", "run_submit"], calls)

    def test_tree_daily_flow_stops_unknown_non_idempotent_request_without_retry(self):
        for failing_endpoint in ("run_start", "run_submit"):
            calls = []

            def transport(request):
                endpoint = request["safe_summary"]["endpoint"]
                calls.append(endpoint)
                if endpoint == "start":
                    return 200, {
                        "ok": True,
                        "council": {"daily": {
                            "jump": {"used": 0, "limit": 1, "remaining": 1},
                            "fly": {"used": 0, "limit": 1, "remaining": 1},
                        }},
                    }
                if endpoint == "run_start" and failing_endpoint == "run_start":
                    raise TimeoutError("unknown after send")
                if endpoint == "run_start":
                    return 200, {"ok": True, "run": {"runToken": "run-once", "seed": "seed-once"}}
                if endpoint == "run_submit":
                    raise TimeoutError("unknown after submit")
                raise AssertionError(endpoint)

            with patch.object(
                tree_miniapp,
                "build_tree_game_proof",
                return_value=(
                    {"durationMs": 1, "clientScore": 15, "charges": [0.5]},
                    {"mode": "jump", "score": 15, "targetScore": 15, "durationMs": 1},
                ),
            ):
                result = tree_miniapp.run_tree_miniapp_daily_lab_flow(
                    token="tree_SECRET999",
                    init_data="query_id=abc&hash=VERY_SECRET",
                    transport=transport,
                    sleeper=lambda _delay: None,
                )

            self.assertFalse(result["ok"])
            self.assertEqual("result_unknown", result["status"])
            self.assertEqual("unknown", result["data"]["phase"])
            self.assertEqual(1, calls.count(failing_endpoint))
            self.assertNotIn("reconcile", [event["step"] for event in result["events"]])

    def test_tree_daily_flow_stops_after_first_server_zero_score(self):
        calls = []
        used = {"jump": 1, "fly": 0}

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            mode = request["payload"].get("mode", "")
            calls.append((endpoint, mode))
            if endpoint == "start":
                return 200, {"ok": True, "council": {"daily": {
                    "jump": {"used": 1, "limit": 1, "remaining": 0},
                    "fly": {"used": used["fly"], "limit": 3, "remaining": 3 - used["fly"]},
                }}}
            if endpoint == "run_start":
                return 200, {"ok": True, "run": {"runToken": "run-fly", "seed": "seed-fly"}}
            if endpoint == "run_submit":
                used["fly"] += 1
                return 200, {
                    "ok": True,
                    "score": 0,
                    "verified": {"ok": False, "hit": True, "score": 0, "durationMs": 1234},
                }
            raise AssertionError(endpoint)

        with patch.object(
            tree_miniapp,
            "build_tree_game_proof",
            return_value=(
                {"durationMs": 1, "clientScore": 18, "flaps": [100]},
                {"mode": "fly", "score": 18, "targetScore": 18, "durationMs": 1},
            ),
        ):
            result = tree_miniapp.run_tree_miniapp_daily_lab_flow(
                token="tree_SECRET999",
                init_data="query_id=abc&hash=VERY_SECRET",
                transport=transport,
                sleeper=lambda _delay: None,
            )

        self.assertFalse(result["ok"])
        self.assertEqual("zero_score", result["status"])
        self.assertEqual("blocked", result["data"]["phase"])
        self.assertEqual(1, used["fly"])
        self.assertEqual(1, sum(1 for endpoint, _mode in calls if endpoint == "run_submit"))
        self.assertEqual(0, result["data"]["runs"][0]["score"])
        self.assertEqual(1, result["data"]["quotas"]["fly"]["used"])
        self.assertEqual(
            {"ok": False, "hit": True, "score": 0, "durationMs": 1234},
            result["data"]["runs"][0]["server_verification"],
        )

    def test_tree_daily_flow_stops_after_first_material_fly_verification_mismatch(self):
        calls = []
        used = {"fly": 0}

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            mode = request["payload"].get("mode", "")
            calls.append((endpoint, mode))
            if endpoint == "start":
                return 200, {"ok": True, "council": {"daily": {
                    "jump": {"used": 1, "limit": 1, "remaining": 0},
                    "fly": {"used": used["fly"], "limit": 3, "remaining": 3 - used["fly"]},
                }}}
            if endpoint == "run_start":
                return 200, {"ok": True, "run": {"runToken": "run-fly", "seed": "seed-fly-once"}}
            if endpoint == "run_submit":
                used["fly"] += 1
                return 200, {
                    "ok": True,
                    "score": 2,
                    "verified": {"ok": True, "hit": True, "score": 2, "durationMs": 24000},
                    "seasonState": {"daily": {
                        "jump": {"used": 1, "limit": 1, "remaining": 0},
                        "fly": {"used": 1, "limit": 3, "remaining": 2},
                    }},
                }
            raise AssertionError(endpoint)

        with patch.object(
            tree_miniapp,
            "build_tree_game_proof",
            return_value=(
                {"durationMs": 24000, "clientScore": 18, "flaps": [0, 1000]},
                {"mode": "fly", "score": 18, "targetScore": 18, "durationMs": 24000},
            ),
        ):
            result = tree_miniapp.run_tree_miniapp_daily_lab_flow(
                token="tree_SECRET999",
                init_data="query_id=abc&hash=VERY_SECRET",
                transport=transport,
                sleeper=lambda _delay: None,
            )

        self.assertFalse(result["ok"])
        self.assertEqual("verification_mismatch", result["status"])
        self.assertEqual("blocked", result["data"]["phase"])
        self.assertEqual(1, used["fly"])
        self.assertEqual(1, sum(1 for endpoint, _mode in calls if endpoint == "run_submit"))
        self.assertEqual(2, result["data"]["quotas"]["fly"]["remaining"])
        self.assertEqual(18, result["data"]["runs"][0]["client_score"])
        self.assertEqual(2, result["data"]["runs"][0]["score"])
        self.assertEqual("seed-fly-once", result["data"]["runs"][0]["run_seed"])
        self.assertEqual([0, 1000], result["data"]["runs"][0]["proof"]["flaps"])

    def test_tree_daily_flow_requires_explicit_quota_for_both_modes(self):
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            return 200, {
                "ok": True,
                "council": {"daily": {
                    "jump": {"used": 1, "limit": 1, "remaining": 0},
                }},
            }

        result = tree_miniapp.run_tree_miniapp_daily_lab_flow(
            token="tree_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            sleeper=lambda _delay: None,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("quota_unknown", result["status"])
        self.assertEqual("blocked", result["data"]["phase"])
        self.assertEqual(["start"], calls)

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
