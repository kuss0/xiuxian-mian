import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import cave_treasure_miniapp, cave_treasure_runtime, deep_retreat, tianti, yuanying


def _cave_event(url="https://t.me/fanrenxiuxian_bot/app?startapp=df_SECRET999"):
    button = SimpleNamespace(button=SimpleNamespace(text="进入洞府", url=url))
    return SimpleNamespace(id=6001, message=SimpleNamespace(buttons=[[button]]))


class CaveTreasureRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._manual_auth = dict(cave_treasure_runtime._MANUAL_AUTH_UNTIL)
        cave_treasure_runtime._MANUAL_AUTH_UNTIL.clear()
        cave_treasure_runtime._RUN_LOCKS.clear()
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.set_storage_bag_records({})
        state_module.set_inventory_delta_records({})
        state_module.set_miniapp_state_records({})
        state_module.ensure_identity_registered(1001)
        state_module.update_send_as_profile(1001, username="xuruode4", label="竹灵 2")

    def tearDown(self):
        cave_treasure_runtime._MANUAL_AUTH_UNTIL.clear()
        cave_treasure_runtime._MANUAL_AUTH_UNTIL.update(self._manual_auth)
        cave_treasure_runtime._RUN_LOCKS.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_public_wild_training_executes_once_and_returns_server_cooldown(self):
        now = 1_700_000_000.0
        with state_module.use_identity(1001) as identity_state:
            identity_state["wild_training_enabled"] = True
        session = {
            "ok": True,
            "init_data": "query_id=abc&hash=SECRET",
            "player_id": 1001,
            "result": {
                "data": {
                    "overview": {
                        "journey": {
                            "wild_experience": {
                                "available": True,
                                "daily_count": 0,
                                "daily_limit": 2,
                                "daily_remaining": 2,
                                "remaining_seconds": 0,
                                "ready_at": 0,
                                "reset_at": 1_700_086_400_000,
                                "modes": [],
                            },
                        },
                    },
                    "raw": {},
                },
            },
        }
        action_result = {
            "ok": True,
            "account": {
                "playerId": 1001,
                "journey": {
                    "serverTime": 1_700_000_005_000,
                    "wildExperience": {
                        "available": False,
                        "dailyCount": 1,
                        "dailyLimit": 2,
                        "dailyRemaining": 1,
                        "remainingSeconds": 43200,
                        "readyAt": 1_700_043_205_000,
                        "resetAt": 1_700_086_400_000,
                        "modes": [],
                    },
                },
            },
            "actionResult": {
                "ok": True,
                "completed": True,
                "title": "妖兽遭遇",
                "message": "历练完成",
                "cultivationDelta": 2860,
                "loot": [{"name": "养魂木", "quantity": 1}],
            },
        }
        flow_result = {"ok": True, "status": "acted", "data": action_result}

        with patch.object(cave_treasure_runtime, "is_cave_public_identity_available", return_value=True), \
                patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)), \
                patch.object(cave_treasure_runtime, "run_cave_journey_action_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock:
            result = await cave_treasure_runtime.run_cave_public_wild_training(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                "深入",
                now=now,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["extra"]["acted"])
        self.assertTrue(result["extra"]["completed"])
        self.assertEqual(now + 43200, result["extra"]["next_time"])
        flow_mock.assert_awaited_once()
        self.assertEqual("wild_experience", flow_mock.await_args.kwargs["action"])
        self.assertEqual("deep", flow_mock.await_args.kwargs["mode"])
        self.assertEqual(1001, flow_mock.await_args.kwargs["player_id"])
        miniapp_record = state_module.get_miniapp_state_records()["1001:wild_training"]
        self.assertEqual([".野外历练"], miniapp_record["replaces_commands"])
        inventory = state_module.get_inventory_delta_records()
        self.assertTrue(any((row.get("items") or {}).get("养魂木") == 1 for row in inventory.values()))

    def test_wild_training_requeues_when_server_reports_no_cooldown(self):
        now = 1_700_000_000.0
        next_time = cave_treasure_runtime._wild_training_post_action_next_time(
            {
                "available": True,
                "daily_count": 1,
                "daily_limit": 2,
                "daily_remaining": 1,
                "remaining_seconds": 0,
                "ready_at": 0,
            },
            {"dailyCount": 1, "dailyLimit": 2},
            now=now,
        )
        self.assertEqual(now + cave_treasure_runtime.WILD_TRAINING_NO_COOLDOWN_FOLLOWUP_SEC, next_time)

    def test_wild_training_waits_for_reset_after_daily_runs_are_exhausted(self):
        now = 1_700_000_000.0
        reset_at = now + 8 * 3600
        next_time = cave_treasure_runtime._wild_training_post_action_next_time(
            {
                "available": False,
                "daily_count": 2,
                "daily_limit": 2,
                "daily_remaining": 0,
                "remaining_seconds": 0,
                "ready_at": 0,
                "reset_at": reset_at * 1000,
            },
            {"dailyCount": 2, "dailyLimit": 2},
            now=now,
        )
        self.assertEqual(reset_at, next_time)

    async def test_public_wild_training_rejects_action_response_for_wrong_identity(self):
        now = 1_700_000_000.0
        with state_module.use_identity(1001) as identity_state:
            identity_state["wild_training_enabled"] = True
        session = {
            "ok": True,
            "init_data": "query_id=abc",
            "player_id": 1001,
            "result": {"data": {"overview": {"journey": {"wild_experience": {
                "available": True, "daily_count": 0, "daily_limit": 2,
                "daily_remaining": 2, "remaining_seconds": 0,
            }}}}},
        }
        flow_result = {"ok": True, "data": {
            "ok": True,
            "account": {"playerId": 9999, "journey": {"wildExperience": {
                "available": True, "dailyCount": 1, "dailyLimit": 2,
                "dailyRemaining": 1, "remainingSeconds": 0,
            }}},
            "actionResult": {"ok": True, "completed": True, "title": "妖兽遭遇"},
        }}
        with patch.object(cave_treasure_runtime, "is_cave_public_identity_available", return_value=True), \
                patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)), \
                patch.object(cave_treasure_runtime, "run_cave_journey_action_production_flow", new=AsyncMock(return_value=flow_result)):
            result = await cave_treasure_runtime.run_cave_public_wild_training(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                "谨慎",
                now=now,
            )
        self.assertFalse(result["ok"])
        self.assertIn("身份校验失败", result["message"])

    async def test_cave_entry_ignored_without_manual_authorization(self):
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock()) as flow_mock:
                handled = await cave_treasure_runtime.handle_cave_treasure_miniapp_entry(
                    _cave_event(),
                    "【洞府】点击进入洞府",
                    1_700_000_000.0,
                )

        self.assertFalse(handled)
        flow_mock.assert_not_awaited()

    async def test_cave_entry_runs_once_after_manual_authorization(self):
        cave_treasure_runtime.authorize_cave_treasure_miniapp_manual_run(1001, now=1_700_000_000.0)
        flow_result = {
            "ok": True,
            "status": "daily_limit",
            "data": {
                "state": {"session_id": "hunt-session-secret", "games_used": 3, "games_limit": 3},
                "rewards": [{"name": "古禁印痕", "qty": 1}],
            },
        }
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await cave_treasure_runtime.handle_cave_treasure_miniapp_entry(
                    _cave_event(),
                    "【洞府】点击进入洞府",
                    1_700_000_001.0,
                    result_msg_id=6001,
                )

        self.assertTrue(handled)
        flow_mock.assert_awaited_once()
        kwargs = flow_mock.await_args.kwargs
        self.assertEqual("df_SECRET999", kwargs["token"])
        self.assertEqual(cave_treasure_runtime.CAVE_TREASURE_MANUAL_MAX_STEPS, kwargs["max_steps"])
        self.assertIn("capture_sink", kwargs)
        self.assertEqual("cave_treasure_runtime:1001:6001", kwargs["capture_source"])
        self.assertNotIn(1001, cave_treasure_runtime._MANUAL_AUTH_UNTIL)
        audit_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list)
        self.assertIn("洞府寻宝 MiniApp 接管入口", audit_text)
        self.assertIn("洞府寻宝结果", audit_text)
        miniapp_records = state_module.get_miniapp_state_records()
        self.assertIn("1001:cave_treasure", miniapp_records)
        miniapp_record = miniapp_records["1001:cave_treasure"]
        self.assertEqual("cave_treasure", miniapp_record["game_key"])
        self.assertEqual(["module_snapshot", "daily_counter", "inventory_delta"], miniapp_record["outputs"])
        self.assertEqual([".洞府"], miniapp_record["replaces_commands"])
        self.assertEqual(3, miniapp_record["state"]["games_used"])
        self.assertTrue(miniapp_record["state"]["has_session_id"])
        self.assertNotIn("hunt-session-secret", json.dumps(miniapp_record, ensure_ascii=False))

    async def test_cave_entry_respects_global_pause_before_http(self):
        cave_treasure_runtime.authorize_cave_treasure_miniapp_manual_run(1001, now=1_700_000_000.0)
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "get_global_enabled", return_value=False), \
                    patch.object(cave_treasure_runtime, "get_global_pause_source", return_value="safety_watchdog"), \
                    patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock()) as flow_mock, \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await cave_treasure_runtime.handle_cave_treasure_miniapp_entry(
                    _cave_event(),
                    "【洞府】点击进入洞府",
                    1_700_000_001.0,
                    result_msg_id=6001,
                )

        self.assertTrue(handled)
        flow_mock.assert_not_awaited()
        self.assertNotIn(1001, cave_treasure_runtime._MANUAL_AUTH_UNTIL)
        audit_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list)
        self.assertIn("全局暂停", audit_text)

    async def test_cave_entry_allows_http_during_tianzun_maintenance_pause(self):
        cave_treasure_runtime.authorize_cave_treasure_miniapp_manual_run(1001, now=1_700_000_000.0)
        flow_result = {"ok": True, "status": "daily_limit", "data": {"state": {"games_used": 3, "games_limit": 3}}}
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "get_global_enabled", return_value=False), \
                    patch.object(cave_treasure_runtime, "get_global_pause_source", return_value="tianzun_maintenance"), \
                    patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await cave_treasure_runtime.handle_cave_treasure_miniapp_entry(
                    _cave_event(),
                    "【洞府】点击进入洞府",
                    1_700_000_001.0,
                    result_msg_id=6001,
                )

        self.assertTrue(handled)
        flow_mock.assert_awaited_once()
        audit_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list)
        self.assertIn("天尊维护暂停中，仅执行 MiniApp HTTP", audit_text)

    async def test_cave_result_reports_game_materials_not_technical_fields(self):
        cave_treasure_runtime.authorize_cave_treasure_miniapp_manual_run(1001, now=1_700_000_000.0)
        flow_result = {
            "ok": True,
            "status": "daily_limit",
            "data": {
                "state": {"games_used": 3, "games_limit": 3},
                "results": [
                    {
                        "cultivationGain": 10,
                        "contribution": 48,
                        "loot": [{"name": "灵石", "quantity": 31}],
                        "rewards": [{"name": "玄晶", "qty": 2}],
                        "text": "获得灵石 +20，获得【古禁印痕】x1",
                        "logs": ["获得灵石 x31。", "获得凝血草 x5。"],
                        "score": 99,
                        "sessionId": "secret-session",
                        "qualityBonus": 3,
                    },
                ],
            },
        }
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock(return_value=flow_result)), \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await cave_treasure_runtime.handle_cave_treasure_miniapp_entry(
                    _cave_event(),
                    "【洞府】点击进入洞府",
                    1_700_000_001.0,
                    result_msg_id=6001,
                )

        self.assertTrue(handled)
        result_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list if "洞府寻宝结果" in str(call.args[0]))
        self.assertIn("游戏 3/3", result_text)
        self.assertIn("收益:修为+10、灵石+20、贡献+48", result_text)
        self.assertIn("奖励:凝血草x5、古禁印痕x1、灵石x31、玄晶x2", result_text)
        self.assertNotIn("score", result_text)
        self.assertNotIn("session", result_text)
        self.assertNotIn("quality", result_text)

    async def test_cave_result_records_pending_inventory_delta_without_mutating_storage_snapshot(self):
        cave_treasure_runtime.authorize_cave_treasure_miniapp_manual_run(1001, now=1_700_000_000.0)
        state_module.set_storage_bag_records({
            "1001": {
                "owner": "source",
                "items": {"灵石": 100},
                "sections": {"API": {"灵石": 100}},
                "updated_at": 1_699_999_900.0,
            }
        })
        flow_result = {
            "ok": True,
            "status": "daily_limit",
            "data": {
                "settled_count": 1,
                "results": [
                    {
                        "sessionId": "hunt-session-secret",
                        "cultivationGain": 10,
                        "contribution": 48,
                        "loot": [{"name": "灵石", "quantity": 31}],
                        "text": "获得灵石 +20，获得【古禁印痕】x1",
                        "logs": ["获得凝血草 x5。"],
                    },
                ],
            },
        }

        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock(return_value=flow_result)), \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
                handled = await cave_treasure_runtime.handle_cave_treasure_miniapp_entry(
                    _cave_event(),
                    "【洞府】点击进入洞府",
                    1_700_000_001.0,
                    result_msg_id=6001,
                )

        self.assertTrue(handled)
        storage_record = state_module.get_storage_bag_records()["1001"]
        self.assertEqual({"灵石": 100}, storage_record["items"])
        delta_records = [
            record for key, record in state_module.get_inventory_delta_records().items()
            if key != "_meta" and isinstance(record, dict)
        ]
        self.assertEqual(1, len(delta_records))
        self.assertEqual("cave_treasure_miniapp", delta_records[0]["source"])
        self.assertEqual("pending_inventory_confirm", delta_records[0]["status"])
        self.assertEqual({"凝血草": 5, "古禁印痕": 1, "灵石": 51}, delta_records[0]["items"])
        self.assertNotIn("hunt-session-secret", delta_records[0]["source_id"])

    async def test_cave_inventory_counts_multiple_log_gains_when_loot_empty(self):
        flow_result = {
            "ok": True,
            "status": "daily_limit",
            "data": {
                "settled_count": 1,
                "results": [
                    {
                        "grade": "失败",
                        "loot": [],
                        "logs": ["获得灵石 x25。", "获得灵石 x33。"],
                    },
                ],
            },
        }

        self.assertEqual({"灵石": 58}, cave_treasure_runtime._cave_treasure_inventory_items(flow_result))

    async def test_public_entry_treasure_runs_directly_from_df_url(self):
        flow_result = {
            "ok": True,
            "status": "daily_limit",
            "data": {
                "settled_count": 3,
                "state": {"games_used": 3, "games_limit": 3},
                "results": [{"logs": ["获得古禁印痕 x1。"]}],
            },
        }
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                    "ok": True,
                    "init_data": "dwelling_init_data",
                    "player_id": 1001,
                    "result": {"ok": True},
                })), \
                patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
            result = await cave_treasure_runtime.run_cave_public_treasure(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
            )

        self.assertTrue(result["ok"])
        self.assertIn("洞府寻宝公共入口", result["message"])
        flow_mock.assert_awaited_once()
        self.assertEqual("df_SECRET999", flow_mock.await_args.kwargs["token"])
        self.assertEqual("dwelling_init_data", flow_mock.await_args.kwargs["init_data"])
        self.assertEqual(1001, flow_mock.await_args.kwargs["player_id"])
        self.assertIn("古禁印痕", "\n".join(str(call.args[0]) for call in audit_mock.await_args_list))
        self.assertEqual("normal", audit_mock.await_args.kwargs["priority"])
        self.assertEqual(3, result["extra"]["games_used"])
        self.assertEqual(3, result["extra"]["games_limit"])
        self.assertEqual(3, result["extra"]["settled_count"])
        self.assertEqual({"古禁印痕": 1}, result["extra"]["rewards"])
        self.assertTrue(result["extra"]["daily_exhausted"])

    async def test_public_treasure_unknown_result_freezes_same_day_retry(self):
        now = 1_700_000_000.0
        unknown_result = {
            "ok": False,
            "status": "result_unknown",
            "error": "timeout",
            "data": {
                "state": {
                    "games_used": 1,
                    "games_limit": 3,
                    "outcome_unknown": True,
                    "outcome_unknown_action": "search",
                },
            },
        }
        cave_treasure_runtime._record_cave_treasure_miniapp_state(1001, unknown_result, now=now)

        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock()) as session_mock, \
                patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock()) as flow_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_treasure(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now + 60,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["extra"]["daily_exhausted"])
        self.assertEqual("outcome_unknown_hold", result["extra"]["skipped"])
        session_mock.assert_not_awaited()
        flow_mock.assert_not_awaited()

    async def test_public_treasure_and_small_world_fail_closed_when_identity_selection_fails(self):
        public_url = "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"
        for runner_name, flow_name in (
            ("run_cave_public_treasure", "run_cave_treasure_miniapp_production_flow"),
            ("run_cave_public_small_world_sync", "run_cave_small_world_production_flow"),
        ):
            with self.subTest(runner=runner_name), state_module.use_identity(1001), \
                    patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                    patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                        "ok": False,
                        "error": "洞府身份校验失败：期望 1001，实际 -1002001",
                    })), \
                    patch.object(cave_treasure_runtime, flow_name, new=AsyncMock()) as flow_mock, \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
                result = await getattr(cave_treasure_runtime, runner_name)(1001, public_url, now=1_700_000_000.0)

            self.assertFalse(result["ok"])
            self.assertIn("身份", result["message"])
            flow_mock.assert_not_awaited()

    async def test_public_entry_trial_reads_external_app_and_runs_trial(self):
        cave_start = {
            "ok": True,
            "status": "ok",
            "data": {
                "overview": {"player_id": 1001},
                "raw": {
                    "account": {
                        "playerId": 1001,
                        "externalApps": {
                            "groups": [{
                                "key": "daily",
                                "apps": [{
                                    "key": "trial",
                                    "title": "天机试炼",
                                    "url": "https://t.me/fanrenxiuxian_bot?startapp=trial_SECRET999",
                                }],
                            }],
                        },
                    },
                },
            },
        }
        trial_result = {
            "ok": True,
            "status": "daily_limit",
            "settled_count": 2,
            "data": {"traceGain": 2},
        }
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="dwelling_init_data")), \
                patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock(return_value=cave_start)) as start_mock, \
                patch.object(cave_treasure_runtime, "run_trial_miniapp_production_flow", new=AsyncMock(return_value=trial_result)) as trial_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
            result = await cave_treasure_runtime.run_cave_public_trial(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("天机试炼", result["extra"]["trial_title"])
        self.assertEqual(2, result["extra"]["settled_count"])
        self.assertEqual({"天机残痕": 2}, result["extra"]["gains"])
        start_mock.assert_awaited_once()
        self.assertNotIn("player_id", start_mock.await_args.kwargs)
        trial_mock.assert_awaited_once()
        self.assertEqual("trial_SECRET999", trial_mock.await_args.kwargs["token"])
        self.assertEqual("dwelling_init_data", trial_mock.await_args.kwargs["init_data"])
        self.assertEqual(1001, trial_mock.await_args.kwargs["player_id"])
        self.assertIn("天机残痕+2", "\n".join(str(call.args[0]) for call in audit_mock.await_args_list))

    async def test_public_entry_trial_resolves_dynamic_external_action_with_player_id(self):
        cave_start = {
            "ok": True,
            "status": "ok",
            "data": {
                "overview": {"player_id": 1001},
                "raw": {
                    "account": {
                        "externalApps": {
                            "groups": [{
                                "apps": [{
                                    "key": "tianji_trial",
                                    "title": "天机试炼",
                                    "available": True,
                                    "action": "trial",
                                    "url": "",
                                }],
                            }],
                        },
                    },
                },
            },
        }
        external_result = {
            "ok": True,
            "status": "ok",
            "data": {"url": "/miniapp/xianxia-trial?startapp=trial_SECRET999"},
        }
        trial_result = {"ok": True, "status": "daily_limit", "settled_count": 0, "data": {}}
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="dwelling_init_data")), \
                patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock(return_value=cave_start)), \
                patch.object(cave_treasure_runtime, "run_cave_external_action_production_flow", new=AsyncMock(return_value=external_result)) as external_mock, \
                patch.object(cave_treasure_runtime, "run_trial_miniapp_production_flow", new=AsyncMock(return_value=trial_result)) as trial_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_trial(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
            )

        self.assertTrue(result["ok"])
        external_mock.assert_awaited_once()
        self.assertEqual("trial", external_mock.await_args.kwargs["action"])
        self.assertEqual(1001, external_mock.await_args.kwargs["player_id"])
        self.assertEqual("dwelling_init_data", external_mock.await_args.kwargs["init_data"])
        trial_mock.assert_awaited_once()
        self.assertEqual("trial_SECRET999", trial_mock.await_args.kwargs["token"])
        self.assertEqual(1001, trial_mock.await_args.kwargs["player_id"])

    async def test_public_entry_trial_stops_when_selected_player_mismatches(self):
        cave_start = {
            "ok": True,
            "status": "ok",
            "data": {"overview": {"player_id": 9999}, "raw": {}},
        }
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="dwelling_init_data")), \
                patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock(return_value=cave_start)) as start_mock, \
                patch.object(cave_treasure_runtime, "run_trial_miniapp_production_flow", new=AsyncMock()) as trial_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_trial(
                2001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
            )

        self.assertFalse(result["ok"])
        self.assertIn("不包含目标身份", result["message"])
        self.assertNotIn("player_id", start_mock.await_args.kwargs)
        trial_mock.assert_not_awaited()

    async def test_public_entry_trial_switches_channel_identity_from_dwelling_choices(self):
        identity_id = 3820064579
        raw_player_id = -1003820064579
        initial_start = {
            "ok": True,
            "status": "ok",
            "data": {
                "overview": {"player_id": 301299112},
                "raw": {
                    "account": {"playerId": 301299112},
                    "identity": {
                        "choices": [
                            {"playerId": 301299112},
                            {"playerId": raw_player_id},
                        ],
                    },
                },
            },
        }
        selected_start = {
            "ok": True,
            "status": "ok",
            "data": {
                "overview": {"player_id": raw_player_id},
                "raw": {
                    "account": {
                        "playerId": raw_player_id,
                        "externalApps": {
                            "groups": [{
                                "apps": [{
                                    "key": "tianji_trial",
                                    "title": "天机试炼",
                                    "available": True,
                                    "url": "https://t.me/fanrenxiuxian_bot?startapp=trial_SECRET999",
                                }],
                            }],
                        },
                    },
                },
            },
        }
        trial_result = {"ok": True, "status": "daily_limit", "settled_count": 0, "data": {}}
        state_module.ensure_identity_registered(identity_id)
        state_module.set_identity_account(identity_id, 301299112)
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="dwelling_init_data")), \
                patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock(side_effect=[initial_start, selected_start])) as start_mock, \
                patch.object(cave_treasure_runtime, "run_trial_miniapp_production_flow", new=AsyncMock(return_value=trial_result)) as trial_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_trial(
                identity_id,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(2, start_mock.await_count)
        self.assertNotIn("player_id", start_mock.await_args_list[0].kwargs)
        self.assertEqual(raw_player_id, start_mock.await_args_list[1].kwargs["player_id"])
        self.assertEqual(raw_player_id, trial_mock.await_args.kwargs["player_id"])

    async def test_public_entry_session_hydrates_deferred_details_before_external_lookup(self):
        start_result = {
            "ok": True,
            "status": "ok",
            "data": {
                "overview": {"player_id": 1001},
                "raw": {
                    "ok": True,
                    "account": {
                        "playerId": 1001,
                        "deferredPending": True,
                        "profile": {"cultivation": {"current": 123}},
                        "externalApps": {"groups": []},
                    },
                    "dwelling": {"meditation": {"deepSeclusion": {"active": True}}},
                    "snapshot": {"level": "overview", "deferredPending": True},
                },
            },
        }
        details_result = {
            "ok": True,
            "status": "ok",
            "data": {
                "ok": True,
                "account": {
                    "playerId": 1001,
                    "deferredPending": False,
                    "externalApps": {
                        "groups": [{"apps": [{"key": "fishing", "available": True, "action": "fishing"}]}],
                    },
                },
                "snapshot": {"level": "deferred", "deferredPending": False},
            },
        }
        with patch.object(
            cave_treasure_runtime,
            "request_cave_treasure_miniapp_init_data",
            new=AsyncMock(return_value="dwelling_init_data"),
        ), patch.object(
            cave_treasure_runtime,
            "run_cave_dwelling_start_production_flow",
            new=AsyncMock(return_value=start_result),
        ) as start_mock, patch.object(
            cave_treasure_runtime,
            "run_cave_dwelling_snapshot_production_flow",
            new=AsyncMock(return_value=details_result),
        ) as details_mock:
            session = await cave_treasure_runtime._load_cave_public_identity_session(
                1001,
                "df_SECRET999",
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
                capture_source="test",
                include_details=True,
            )

        self.assertTrue(session["ok"])
        start_mock.assert_awaited_once()
        details_mock.assert_awaited_once()
        self.assertEqual(1001, details_mock.await_args.kwargs["player_id"])
        raw = session["result"]["data"]["raw"]
        self.assertEqual(123, raw["account"]["profile"]["cultivation"]["current"])
        self.assertTrue(raw["dwelling"]["meditation"]["deepSeclusion"]["active"])
        self.assertEqual("fishing", raw["account"]["externalApps"]["groups"][0]["apps"][0]["key"])

    async def test_public_entry_fishing_uses_selected_channel_and_dwelling_init_data(self):
        identity_id = 3820064579
        raw_player_id = -1003820064579
        state_module.ensure_identity_registered(identity_id)
        state_module.set_identity_account(identity_id, 301299112)
        with state_module.use_identity(identity_id):
            state_module.state["fishing_daily_day"] = ""
            state_module.state["fishing_daily_count"] = 0
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_pond"] = "青溪浅滩"
            state_module.state["fishing_bait"] = "灵米饵"

        session = {
            "ok": True,
            "init_data": "dwelling_init_data",
            "player_id": raw_player_id,
            "result": {
                "ok": True,
                "data": {
                    "raw": {
                        "account": {
                            "externalApps": {
                                "groups": [{
                                    "apps": [{
                                        "key": "fishing",
                                        "title": "灵溪垂钓",
                                        "available": True,
                                        "action": "fishing",
                                    }],
                                }],
                            },
                        },
                    },
                },
            },
        }
        external_result = {
            "ok": True,
            "data": {
                "url": "/miniapp/xianxia-fishing?startapp=fish_CHANNEL999",
                "title": "灵溪垂钓",
            },
        }
        fishing_result = {
            "ok": False,
            "status": "daily_limit",
            "error": "fishing_daily_limit_reached",
            "data": {},
        }
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)) as session_mock, \
                patch.object(cave_treasure_runtime, "run_cave_external_action_production_flow", new=AsyncMock(return_value=external_result)) as external_mock, \
                patch.object(cave_treasure_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=fishing_result)) as fishing_mock, \
                patch.object(cave_treasure_runtime, "_apply_fishing_miniapp_result", return_value="5/5竿") as apply_mock, \
                patch.object(cave_treasure_runtime, "_send_fishing_daily_completion_summary", new=AsyncMock(return_value=True)) as summary_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_fishing(
                identity_id,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(session_mock.await_args.kwargs["include_details"])
        self.assertEqual(raw_player_id, external_mock.await_args.kwargs["player_id"])
        self.assertEqual("fishing", external_mock.await_args.kwargs["action"])
        self.assertEqual("dwelling_init_data", fishing_mock.await_args.kwargs["init_data"])
        self.assertEqual("青溪浅滩", fishing_mock.await_args.kwargs["pond_choice"])
        self.assertEqual("灵米饵", fishing_mock.await_args.kwargs["bait_choice"])
        self.assertTrue(result["extra"]["daily_exhausted"])
        apply_mock.assert_called_once()
        summary_mock.assert_awaited_once()

    async def test_public_entry_fishing_skips_identity_without_rod_until_next_day(self):
        identity_id = 3504367852
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["next_fishing_time"] = 0
            state_module.state["fishing_last_result"] = ""
            state_module.state["fishing_last_error"] = "old"

        session = {
            "ok": True,
            "init_data": "dwelling_init_data",
            "player_id": -1003504367852,
            "result": {
                "ok": True,
                "data": {
                    "raw": {
                        "account": {
                            "externalApps": {
                                "groups": [{
                                    "apps": [{
                                        "key": "fishing",
                                        "title": "灵溪垂钓",
                                        "available": False,
                                        "action": "fishing",
                                    }],
                                }],
                            },
                        },
                    },
                },
            },
        }
        now = 1_700_000_000.0
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)), \
                patch.object(cave_treasure_runtime, "run_cave_external_action_production_flow", new=AsyncMock()) as external_mock, \
                patch.object(cave_treasure_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock()) as fishing_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()), \
                patch.object(cave_treasure_runtime, "save_state"):
            result = await cave_treasure_runtime.run_cave_public_fishing(
                identity_id,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("rod_missing", result["extra"]["skipped"])
        external_mock.assert_not_awaited()
        fishing_mock.assert_not_awaited()
        with state_module.use_identity(identity_id):
            self.assertEqual(
                cave_treasure_runtime.fishing_behavior.next_fishing_reset_timestamp(
                    now,
                    cave_treasure_runtime._fishing_reset_jitter_sec(identity_id),
                ),
                state_module.state["next_fishing_time"],
            )
            self.assertEqual("未持有鱼竿，今日跳过", state_module.state["fishing_last_result"])
            self.assertEqual("", state_module.state["fishing_last_error"])

    async def test_public_entry_fishing_skips_identity_when_entry_is_missing(self):
        session = {
            "ok": True,
            "init_data": "dwelling_init_data",
            "player_id": 1001,
            "result": {"ok": True, "data": {"raw": {"account": {"externalApps": {"groups": []}}}}},
        }
        now = 1_700_000_000.0
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)), \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_fishing(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("entry_missing", result["extra"]["skipped"])
        with state_module.use_identity(1001):
            self.assertGreater(state_module.state["next_fishing_time"], now)
            self.assertEqual("未开放灵溪垂钓，今日跳过", state_module.state["fishing_last_result"])
            self.assertEqual("", state_module.state["fishing_last_error"])

    def test_public_entry_trial_finds_trial_url_without_leaking_token(self):
        launch = cave_treasure_runtime._find_trial_launch_in_cave_payload({
            "externalApps": {
                "groups": [{
                    "apps": [{
                        "title": "天机试炼",
                        "url": "https://t.me/fanrenxiuxian_bot?startapp=trial_SECRET999",
                    }],
                }],
            },
        })

        self.assertEqual("trial_SECRET999", launch["token"])
        self.assertEqual("天机试炼", launch["title"])
        self.assertNotIn("trial_SECRET999", json.dumps(launch["safe_summary"], ensure_ascii=False))

        relative = cave_treasure_runtime._find_trial_launch_in_cave_payload({
            "key": "tianji_trial",
            "title": "天机试炼",
            "url": "/miniapp/xianxia-trial?startapp=trial_SECRET888",
        })
        self.assertEqual("trial_SECRET888", relative["token"])
        self.assertTrue(relative["webview_url"].startswith("https://asc.aiopenai.app/"))

        external = cave_treasure_runtime._find_trial_external_app_in_cave_payload({
            "account": {
                "commandCenter": {
                    "entries": [{"key": "trial_help", "title": "天机试炼", "note": "同名说明项"}],
                },
                "externalApps": {
                    "groups": [{
                        "apps": [{
                            "key": "tianji_trial",
                            "title": "天机试炼",
                            "available": True,
                            "url": "#",
                        }],
                    }],
                },
            },
        })
        self.assertEqual("tianji_trial", external["action"])

    async def test_public_stargazer_selects_alias_player_and_reuses_dwelling_session(self):
        cave_start = {
            "ok": True,
            "status": "ok",
            "data": {
                "overview": {"player_id": 2001},
                "raw": {
                    "account": {
                        "starPalace": {
                            "observatory": {
                                "title": "星宫观星台",
                                "url": "https://t.me/fanrenxiuxian_bot?startapp=farm_SECRET999",
                            },
                        },
                        "externalApps": {"groups": []},
                    },
                },
            },
        }
        flow_result = {
            "ok": True,
            "status": "wait",
            "data": {
                "farm_state": {"total_slots": 2},
                "action_counts": {"collect": 1},
                "item_deltas": {"星辰精华": 2},
            },
        }
        state_module.ensure_identity_registered(2001)
        state_module.set_identity_account(2001, 1001)
        with state_module.use_identity(2001):
            state_module.state["stargazer_enabled"] = True
            with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                    patch.object(cave_treasure_runtime, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="dwelling_init_data")), \
                    patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock(return_value=cave_start)) as start_mock, \
                    patch.object(cave_treasure_runtime, "run_stargazer_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                    patch.object(cave_treasure_runtime.stargazer, "_finish_stargazer_miniapp_result", new=AsyncMock(return_value=True)) as finish_mock:
                result = await cave_treasure_runtime.run_cave_public_stargazer(
                    2001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    now=1_700_000_000.0,
                )

        self.assertTrue(result["ok"])
        self.assertNotIn("player_id", start_mock.await_args.kwargs)
        self.assertEqual(2001, flow_mock.await_args.kwargs["player_id"])
        self.assertEqual("dwelling_init_data", flow_mock.await_args.kwargs["init_data"])
        self.assertEqual({"collect": 1}, result["extra"]["action_counts"])
        self.assertEqual({"星辰精华": 2}, result["extra"]["rewards"])
        finish_mock.assert_awaited_once()

    async def test_public_stargazer_resolves_dynamic_external_action(self):
        session = {
            "ok": True,
            "init_data": "dwelling_init_data",
            "player_id": -1002001,
            "result": {
                "ok": True,
                "data": {
                    "raw": {
                        "account": {
                            "externalApps": {
                                "groups": [{
                                    "apps": [{
                                        "key": "sect_farm",
                                        "title": "宗门观星台",
                                        "available": True,
                                        "action": "sect_farm",
                                    }],
                                }],
                            },
                        },
                    },
                },
            },
        }
        external_result = {
            "ok": True,
            "data": {"url": "/miniapp/xianxia-sect-farm?startapp=farm_SECRET999"},
        }
        flow_result = {"ok": True, "status": "wait", "data": {}}
        state_module.ensure_identity_registered(2001)
        with state_module.use_identity(2001):
            state_module.state["stargazer_enabled"] = True
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)), \
                patch.object(cave_treasure_runtime, "run_cave_external_action_production_flow", new=AsyncMock(return_value=external_result)) as external_mock, \
                patch.object(cave_treasure_runtime, "run_stargazer_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                patch.object(cave_treasure_runtime.stargazer, "_finish_stargazer_miniapp_result", new=AsyncMock(return_value=True)):
            result = await cave_treasure_runtime.run_cave_public_stargazer(
                2001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("sect_farm", external_mock.await_args.kwargs["action"])
        self.assertEqual(-1002001, external_mock.await_args.kwargs["player_id"])
        self.assertEqual("dwelling_init_data", external_mock.await_args.kwargs["init_data"])
        self.assertEqual("farm_SECRET999", flow_mock.await_args.kwargs["token"])
        self.assertEqual(-1002001, flow_mock.await_args.kwargs["player_id"])

    async def test_public_stargazer_skips_identity_when_entry_is_missing(self):
        with state_module.use_identity(1001):
            state_module.state["stargazer_enabled"] = True
        session = {
            "ok": True,
            "init_data": "dwelling_init_data",
            "player_id": 1001,
            "result": {"ok": True, "data": {"raw": {"account": {"externalApps": {"groups": []}}}}},
        }
        now = 1_700_000_000.0
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)), \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_stargazer(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("entry_missing", result["extra"]["skipped"])
        with state_module.use_identity(1001):
            self.assertEqual(now + 6 * 3600, state_module.state["next_stargazer_panel_time"])
            self.assertEqual("public_entry_unavailable", state_module.state["stargazer_last_action"])

    async def test_public_tree_uses_spirit_tree_external_app_and_reuses_dwelling_session(self):
        cave_start = {
            "ok": True,
            "status": "ok",
            "data": {
                "overview": {"player_id": 1001},
                "raw": {
                    "account": {
                        "externalApps": {
                            "groups": [{
                                "key": "sect_farm",
                                "apps": [{
                                    "key": "spirit_tree",
                                    "title": "落云宗灵眼之树",
                                    "available": True,
                                    "url": "https://t.me/fanrenxiuxian_bot?startapp=tree_SECRET999",
                                }],
                            }],
                        },
                    },
                },
            },
        }
        flow_result = {
            "ok": True,
            "status": "completed",
            "data": {"phase": "completed", "quotas": {}, "runs": [], "rewards": {}},
        }
        state_module.update_send_as_profile(1001, username="imcanonical_ai", label="反向的钟", sect_name="落云宗")
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="dwelling_init_data")), \
                patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock(return_value=cave_start)), \
                patch.object(cave_treasure_runtime.tree_runtime, "run_tree_miniapp_daily_direct", new=AsyncMock(return_value=flow_result)) as flow_mock:
            result = await cave_treasure_runtime.run_cave_public_tree(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
                day_key="2026-07-14",
                op_id="tree_daily:2026-07-14:1001",
                score_profiles={"jump": {}, "fly": {}},
            )

        self.assertTrue(result["ok"])
        self.assertEqual("tree_SECRET999", flow_mock.await_args.kwargs["token"])
        self.assertEqual("dwelling_init_data", flow_mock.await_args.kwargs["init_data"])
        self.assertEqual("2026-07-14", flow_mock.await_args.kwargs["day_key"])
        self.assertEqual("tree_daily:2026-07-14:1001", flow_mock.await_args.kwargs["op_id"])

    async def test_public_tree_resolves_dynamic_external_action(self):
        session = {
            "ok": True,
            "init_data": "dwelling_init_data",
            "player_id": -1001001,
            "result": {
                "ok": True,
                "data": {
                    "raw": {
                        "account": {
                            "externalApps": {
                                "groups": [{
                                    "apps": [{
                                        "key": "spirit_tree",
                                        "title": "落云宗灵眼之树",
                                        "available": True,
                                        "action": "spirit_tree",
                                    }],
                                }],
                            },
                        },
                    },
                },
            },
        }
        external_result = {
            "ok": True,
            "data": {"url": "/miniapp/xianxia-spirit-tree?startapp=tree_SECRET999"},
        }
        flow_result = {"ok": True, "status": "completed", "data": {}}
        state_module.update_send_as_profile(1001, username="imcanonical_ai", label="反向的钟", sect_name="落云宗")
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)), \
                patch.object(cave_treasure_runtime, "run_cave_external_action_production_flow", new=AsyncMock(return_value=external_result)) as external_mock, \
                patch.object(cave_treasure_runtime.tree_runtime, "run_tree_miniapp_daily_direct", new=AsyncMock(return_value=flow_result)) as flow_mock:
            result = await cave_treasure_runtime.run_cave_public_tree(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("spirit_tree", external_mock.await_args.kwargs["action"])
        self.assertEqual(-1001001, external_mock.await_args.kwargs["player_id"])
        self.assertEqual("dwelling_init_data", external_mock.await_args.kwargs["init_data"])
        self.assertEqual("tree_SECRET999", flow_mock.await_args.kwargs["token"])

    async def test_public_yuanying_runs_tianjige_command_and_replays_success(self):
        now = 1_700_000_000.0
        result_data = {
            "actionResult": {
                "ok": True,
                "rawMessage": "你心念一动，丹田中的元婴化作一道流光飞出，消失在天际。\n它将在外云游 8 小时，为你寻觅天地奇珍。",
            },
        }
        status_result = {
            "ok": True,
            "status": "ok",
            "data": {"actionResult": {"ok": True, "rawMessage": "【元婴状态】\n状态: 窍中温养，可继续出窍。"}},
        }
        flow_result = {"ok": True, "status": "ok", "data": result_data}
        cave_start = {"ok": True, "status": "ok", "data": {"overview": {"player_id": 1001}, "raw": {}}}
        with state_module.use_identity(1001):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "idle"
            state_module.state["next_yuanying_time"] = now - 1
            with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                    patch.object(cave_treasure_runtime, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="dwelling_init_data")), \
                    patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock(return_value=cave_start)), \
                    patch.object(cave_treasure_runtime, "run_cave_tianjige_command_production_flow", new=AsyncMock(side_effect=[status_result, flow_result])) as flow_mock, \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()) as audit_mock, \
                    patch.object(yuanying, "send_audit_log", new=AsyncMock()), \
                    patch.object(yuanying, "save_state"), \
                    patch.object(yuanying, "_note_yuanying_remote_block"):
                result = await cave_treasure_runtime.run_cave_public_yuanying(
                    1001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    now=now,
                )

            self.assertTrue(result["ok"])
            self.assertEqual([".元婴状态", ".元婴出窍"], [call.kwargs["command"] for call in flow_mock.await_args_list])
            self.assertTrue(all(call.kwargs["player_id"] == 1001 for call in flow_mock.await_args_list))
            self.assertEqual("cave_public_tianjige_yuanying:1001", flow_mock.await_args_list[-1].kwargs["capture_source"])
            self.assertEqual("running", state_module.state["yuanying_phase"])
            self.assertGreater(state_module.state["next_yuanying_time"], now)
            audit_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list)
            self.assertIn("洞府天机阁元婴出窍", audit_text)
            self.assertEqual("normal", audit_mock.await_args.kwargs["priority"])

    async def test_public_yuanying_respects_phaseful_gate_before_http(self):
        now = 1_700_000_000.0
        with state_module.use_identity(1001):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "running"
            state_module.state["next_yuanying_time"] = now + 3600
            with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                    patch.object(cave_treasure_runtime, "run_cave_tianjige_command_production_flow", new=AsyncMock()) as flow_mock:
                result = await cave_treasure_runtime.run_cave_public_yuanying(
                    1001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    now=now,
                )

        self.assertFalse(result["ok"])
        self.assertIn("尚未到出窍窗口", result["message"])
        flow_mock.assert_not_awaited()

    async def test_public_yuanying_accepts_send_as_alias_with_player_id(self):
        state_module.ensure_identity_registered(2001)
        state_module.update_send_as_profile(2001, username="alias_role", label="别名角色")
        state_module.set_identity_account(2001, 1001)
        now = 1_700_000_000.0
        cave_start = {"ok": True, "status": "ok", "data": {"overview": {"player_id": 2001}, "raw": {}}}
        status_result = {
            "ok": True,
            "status": "ok",
            "data": {"actionResult": {"ok": True, "rawMessage": "【元婴状态】\n归来倒计时 1小时。"}},
        }
        with state_module.use_identity(2001):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "running"
            state_module.state["next_yuanying_time"] = now - 1
            with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                    patch.object(cave_treasure_runtime, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="dwelling_init_data")), \
                    patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock(return_value=cave_start)) as start_mock, \
                    patch.object(cave_treasure_runtime, "run_cave_tianjige_command_production_flow", new=AsyncMock(return_value=status_result)) as flow_mock, \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()), \
                    patch.object(yuanying, "send_audit_log", new=AsyncMock()), \
                    patch.object(yuanying, "_note_yuanying_remote_block"):
                result = await cave_treasure_runtime.run_cave_public_yuanying(
                    2001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    now=now,
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["extra"]["launched"])
        self.assertNotIn("player_id", start_mock.await_args.kwargs)
        self.assertEqual(2001, flow_mock.await_args.kwargs["player_id"])

    async def test_tianjige_yuanying_sync_never_triggers_legacy_warm_retry(self):
        with state_module.use_identity(1001):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "idle"
            state_module.state["next_yuanying_time"] = 123.0
            with patch.object(yuanying, "handle_yuanying_status_reply", new=AsyncMock()) as status_mock:
                sync = await cave_treasure_runtime.sync_cave_tianjige_yuanying_result(
                    1001,
                    {"actionResult": {"ok": False, "rawMessage": "窍中温养，暂不可再次出窍。"}},
                    now=1_700_000_000.0,
                )

        self.assertFalse(sync["handled"])
        self.assertEqual("action_rejected", sync["reason"])
        status_mock.assert_not_awaited()
        with state_module.use_identity(1001):
            self.assertEqual("idle", state_module.state["yuanying_phase"])
            self.assertEqual(123.0, state_module.state["next_yuanying_time"])

    async def test_tianjige_yuanying_empty_message_is_ignored(self):
        sync = await cave_treasure_runtime.sync_cave_tianjige_yuanying_result(
            1001,
            {"actionResult": {"ok": True}},
            now=1_700_000_000.0,
        )

        self.assertFalse(sync["handled"])
        self.assertEqual("missing_identity_or_message", sync["reason"])

    async def test_tianjige_yuanying_negative_warm_status_never_launches(self):
        now = 1_700_000_000.0
        with state_module.use_identity(1001):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "running"
            state_module.state["next_yuanying_time"] = now - 1
            sync = await cave_treasure_runtime.sync_cave_tianjige_yuanying_result(
                1001,
                {"actionResult": {"ok": True, "rawMessage": "【元婴状态】\n状态: 窍中温养，但暂不可再次出窍。"}},
                now=now,
                command=yuanying.CMD_YUANYING_STATUS,
            )

        self.assertFalse(sync["handled"])
        self.assertFalse(sync["ready"])
        with state_module.use_identity(1001):
            self.assertEqual("running", state_module.state["yuanying_phase"])
            self.assertEqual(now - 1, state_module.state["next_yuanying_time"])

    async def test_tianjige_yuanying_requires_literal_true_action_result(self):
        now = 1_700_000_000.0
        for action_result in (
            {"rawMessage": "【元婴状态】\n状态: 窍中温养"},
            {"ok": None, "rawMessage": "【元婴状态】\n状态: 窍中温养"},
            {"ok": "true", "rawMessage": "【元婴状态】\n状态: 窍中温养"},
        ):
            with self.subTest(action_result=action_result), state_module.use_identity(1001):
                state_module.state["yuanying_enabled"] = True
                state_module.state["yuanying_phase"] = "running"
                state_module.state["next_yuanying_time"] = now - 1
                sync = await cave_treasure_runtime.sync_cave_tianjige_yuanying_result(
                    1001,
                    {"actionResult": action_result},
                    now=now,
                    command=yuanying.CMD_YUANYING_STATUS,
                )

                self.assertFalse(sync["handled"])
                self.assertFalse(sync["ready"])
                self.assertEqual("action_rejected", sync["reason"])
                self.assertEqual("running", state_module.state["yuanying_phase"])
                self.assertEqual(now - 1, state_module.state["next_yuanying_time"])

    async def test_public_yuanying_active_retreat_status_defers_without_launch(self):
        now = 1_700_000_000.0
        cave_start = {"ok": True, "status": "ok", "data": {"overview": {"player_id": 1001}, "raw": {}}}
        status_result = {
            "ok": True,
            "status": "ok",
            "data": {
                "actionResult": {
                    "ok": True,
                    "rawMessage": "**你的本命元婴**\n**状态**: 元婴闭关\n**已积累修为**: 约 7123 点 (发言时自动结算)",
                },
            },
        }
        with state_module.use_identity(1001):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "summary_due"
            state_module.state["next_yuanying_time"] = now - 1
            state_module.state["yuanying_probe_pending"] = True
            with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                    patch.object(cave_treasure_runtime, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="dwelling_init_data")), \
                    patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock(return_value=cave_start)), \
                    patch.object(cave_treasure_runtime, "run_cave_tianjige_command_production_flow", new=AsyncMock(return_value=status_result)) as flow_mock, \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()), \
                    patch.object(cave_treasure_runtime, "save_state"), \
                    patch.object(yuanying, "save_state"):
                result = await cave_treasure_runtime.run_cave_public_yuanying(
                    1001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    now=now,
                )

            self.assertTrue(result["ok"])
            self.assertFalse(result["extra"]["launched"])
            self.assertEqual([".元婴状态"], [call.kwargs["command"] for call in flow_mock.await_args_list])
            self.assertEqual("active_yuanying_retreat", result["extra"]["status_sync"]["reason"])
            self.assertEqual("running", state_module.state["yuanying_phase"])
            self.assertFalse(state_module.state["yuanying_probe_pending"])
            self.assertEqual(
                now + cave_treasure_runtime.CAVE_YUANYING_STATUS_RECHECK_SEC,
                state_module.state["next_yuanying_time"],
            )

    async def test_tianjige_command_flow_disables_http_retries(self):
        http_result = SimpleNamespace(ok=True, data={"actionResult": {"ok": True, "message": "已处理"}})
        with patch.object(cave_treasure_miniapp, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="query_id=abc&hash=SECRET")), \
                patch.object(cave_treasure_miniapp, "execute_miniapp_http_request", new=Mock(return_value=http_result)) as execute_mock:
            result = await cave_treasure_miniapp.run_cave_tianjige_command_production_flow(
                1001,
                token="df_SECRET999",
                webview_url="https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                command=".元婴出窍",
            )

        self.assertTrue(result["ok"])
        self.assertEqual((), execute_mock.call_args.kwargs["backoff_sec"])
        self.assertEqual("command_center:.元婴出窍", execute_mock.call_args.kwargs["step_key"])

    async def test_public_tianti_status_replays_read_only_panel_without_climb(self):
        now = 1_700_000_500.0
        raw_message = (
            "【凌霄云阶】\n"
            "当前进度：3 / 12 阶\n"
            "已完成周天：1 轮\n"
            "罡风淬体：2 / 12 层\n"
            "登阶冷却：可用\n"
            "问心状态：今日尚未问心"
        )
        with state_module.use_identity(1001) as identity_state:
            identity_state["tianti_enabled"] = True

        session = {
            "ok": True,
            "init_data": "query_id=abc&hash=SECRET",
            "player_id": 1001,
        }
        result = {"ok": True, "data": {"actionResult": {"ok": True, "rawMessage": raw_message}}}
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)), \
                patch.object(cave_treasure_runtime, "run_cave_tianjige_command_production_flow", new=AsyncMock(return_value=result)) as flow_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            response = await cave_treasure_runtime.run_cave_public_tianti_status(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(response["ok"])
        self.assertIn("只读，不触发登阶", response["message"])
        self.assertEqual(".天阶状态", flow_mock.await_args.kwargs["command"])
        self.assertEqual(3, state_module.state["tianti_progress_current"])
        self.assertEqual(12, state_module.state["tianti_progress_total"])
        self.assertEqual(1, state_module.state["tianti_cycle_count"])
        self.assertEqual(0, state_module.state["tianti_last_climb_msg_id"])

    async def test_public_tianjige_read_only_command_does_not_update_game_state(self):
        now = 1_700_000_500.0
        session = {"ok": True, "init_data": "query_id=abc&hash=SECRET", "player_id": 1001}
        result = {"ok": True, "data": {"actionResult": {"rawMessage": "阴罗幡：魂魄 3，精华可收取。"}}}
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)), \
                patch.object(cave_treasure_runtime, "run_cave_tianjige_command_production_flow", new=AsyncMock(return_value=result)) as flow_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            response = await cave_treasure_runtime.run_cave_public_tianjige_read_only(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                ".我的阴罗幡",
                now=now,
            )

        self.assertTrue(response["ok"])
        self.assertIn("阴罗幡", response["message"])
        self.assertEqual(".我的阴罗幡", flow_mock.await_args.kwargs["command"])
        self.assertEqual(1001, flow_mock.await_args.kwargs["player_id"])

    async def test_deep_seclusion_action_flow_disables_http_retries(self):
        http_result = SimpleNamespace(ok=True, data={"actionResult": {"ok": True, "message": "已结算"}})
        with patch.object(cave_treasure_miniapp, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="query_id=abc&hash=SECRET")), \
                patch.object(cave_treasure_miniapp, "execute_miniapp_http_request", new=Mock(return_value=http_result)) as execute_mock:
            result = await cave_treasure_miniapp.run_cave_deep_seclusion_action_production_flow(
                1001,
                token="df_SECRET999",
                webview_url="https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                action="settle",
            )

        self.assertTrue(result["ok"])
        self.assertEqual((), execute_mock.call_args.kwargs["backoff_sec"])
        self.assertEqual("deep_seclusion:settle", execute_mock.call_args.kwargs["step_key"])

    async def test_deep_seclusion_settle_replays_summary_through_deep_retreat_state(self):
        now = 1_700_000_500.0
        text = (
            "📜 修士 @xuruode4 深度闭关总结\n"
            "【深度闭关总结】\n"
            "本次结算时长: 8.0 小时 (基础上限8小时)\n"
            "神魂吐纳次数: 32 周天\n"
            "本次深度闭关，你的修为最终变化了 26682 点！"
        )
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "idle"
            state_module.state["next_deep_retreat_time"] = now - 60

        with (
            patch("model.features._phaseful.save_state"),
            patch.object(deep_retreat, "save_state"),
            patch.object(deep_retreat, "console_log"),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
        ):
            result = await cave_treasure_runtime.sync_cave_deep_seclusion_action_result(
                1001,
                "settle",
                {"ok": True, "actionResult": {"rawMessage": text}},
                now=now,
            )

        self.assertTrue(result["handled"])
        self.assertEqual("summary", result["message_kind"])
        with state_module.use_identity(1001):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])

    async def test_deep_seclusion_settle_before_completion_restores_running_timer(self):
        now = 1_700_000_550.0
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "observing_summary"
            state_module.state["next_deep_retreat_time"] = now - 60

        with patch.object(deep_retreat, "save_state"):
            result = await cave_treasure_runtime.sync_cave_deep_seclusion_action_result(
                1001,
                "settle",
                {
                    "ok": True,
                    "actionResult": {
                        "ok": True,
                        "completed": False,
                        "remainingSeconds": 3738,
                        "message": "深度闭关尚未完成。",
                    },
                },
                now=now,
            )

        self.assertTrue(result["handled"])
        self.assertEqual("still_running", result["reason"])
        with state_module.use_identity(1001):
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 3738 + deep_retreat.CD_BUFFER_SEC, state_module.state["next_deep_retreat_time"])

    async def test_deep_seclusion_ambiguous_settle_defers_to_status(self):
        now = 1_700_000_575.0
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["next_deep_retreat_time"] = now - 60

        with patch.object(cave_treasure_runtime, "save_state"):
            result = await cave_treasure_runtime.sync_cave_deep_seclusion_action_result(
                1001,
                "settle",
                {"ok": True, "actionResult": {"ok": True, "message": "操作完成"}},
                now=now,
            )

        self.assertFalse(result["handled"])
        self.assertEqual("ambiguous_settle_recheck_status", result["reason"])
        with state_module.use_identity(1001):
            self.assertEqual("launching", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + cave_treasure_runtime.CAVE_DEEP_STATUS_RECHECK_SEC, state_module.state["next_deep_retreat_time"])

    async def test_deep_seclusion_message_prefers_action_result_raw_message(self):
        message = cave_treasure_runtime.extract_cave_deep_seclusion_action_message({
            "ok": True,
            "message": "操作完成",
            "actionResult": {"rawMessage": "【深度闭关总结】\n本次结算时长: 8.0 小时\n神魂吐纳次数: 32 周天"},
        })

        self.assertIn("深度闭关总结", message)
        self.assertNotEqual("操作完成", message)

    async def test_deep_seclusion_start_replays_success_through_deep_retreat_state(self):
        now = 1_700_000_600.0
        text = "你已进入深度闭关状态，神魂将自行吐纳 **8** 小时。\n期间你将无法进行大部分操作。下次发言时将自动结算本次闭关的收获。"
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "idle"
            state_module.state["next_deep_retreat_time"] = now - 60

        with (
            patch.object(deep_retreat, "save_state"),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
            patch("model.features.passive_inbox.record_passive_inbox_event"),
        ):
            result = await cave_treasure_runtime.sync_cave_deep_seclusion_action_result(
                1001,
                "start",
                {"ok": True, "actionResult": {"message": text}},
                now=now,
            )

        self.assertTrue(result["handled"])
        self.assertEqual("start", result["message_kind"])
        with state_module.use_identity(1001):
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 8 * 60 * 60 + deep_retreat.CD_BUFFER_SEC, state_module.state["next_deep_retreat_time"])

    async def test_deep_seclusion_status_payload_replays_running_state(self):
        now = 1_700_000_700.0
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "launching"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now + 8 * 60 * 60

        with (
            patch.object(deep_retreat, "save_state"),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
            patch("model.features.passive_inbox.record_passive_inbox_event"),
        ):
            result = await cave_treasure_runtime.sync_cave_deep_seclusion_action_result(
                1001,
                "status",
                {
                    "ok": True,
                    "data": {
                        "deep_seclusion": {
                            "active": True,
                            "remaining_seconds": 2128,
                            "status_text": "闭关中，剩余 0小时35分。",
                        },
                    },
                },
                now=now,
            )

        self.assertTrue(result["handled"])
        self.assertEqual("status", result["message_kind"])
        with state_module.use_identity(1001):
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
            self.assertFalse(state_module.state["deep_retreat_probe_pending"])
            self.assertEqual(now + 35 * 60 + deep_retreat.CD_BUFFER_SEC, state_module.state["next_deep_retreat_time"])

    async def test_expired_cave_authorization_does_not_run(self):
        cave_treasure_runtime.authorize_cave_treasure_miniapp_manual_run(1001, now=1_700_000_000.0, ttl_sec=60)
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock()) as flow_mock:
                handled = await cave_treasure_runtime.handle_cave_treasure_miniapp_entry(
                    _cave_event(),
                    "【洞府】点击进入洞府",
                    1_700_000_120.0,
                )

        self.assertFalse(handled)
        flow_mock.assert_not_awaited()
        self.assertNotIn(1001, cave_treasure_runtime._MANUAL_AUTH_UNTIL)

    async def test_unrouted_cave_entry_requires_authorized_username_match(self):
        cave_treasure_runtime.authorize_cave_treasure_miniapp_manual_run(1001, now=1_700_000_000.0)
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock()) as flow_mock:
                handled = await cave_treasure_runtime.handle_cave_treasure_miniapp_entry(
                    _cave_event(),
                    "【洞府】\n道友 @other 的洞府入口已开启。",
                    1_700_000_001.0,
                    require_identity_match=True,
                )

        self.assertFalse(handled)
        flow_mock.assert_not_awaited()
        self.assertIn(1001, cave_treasure_runtime._MANUAL_AUTH_UNTIL)

        flow_result = {"ok": True, "status": "settled", "data": {"rewards": [{"name": "玄晶", "qty": 1}]}}
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "run_cave_treasure_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
                handled = await cave_treasure_runtime.handle_cave_treasure_miniapp_entry(
                    _cave_event(),
                    "【洞府】\n道友 @xuruode4 的洞府入口已开启。",
                    1_700_000_002.0,
                    result_msg_id=6001,
                    require_identity_match=True,
                )

        self.assertTrue(handled)
        flow_mock.assert_awaited_once()

    async def test_cave_public_small_world_blocks_non_maintenance_global_pause(self):
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "get_global_enabled", return_value=False), \
                    patch.object(cave_treasure_runtime, "get_global_pause_source", return_value="safety_watchdog"), \
                    patch.object(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", new=AsyncMock()) as flow_mock:
                result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                    1001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    now=1_700_000_001.0,
                )

        self.assertFalse(result["ok"])
        self.assertIn("全局暂停来源", result["message"])
        flow_mock.assert_not_awaited()

    def test_cave_public_small_world_plans_manifest_then_sermon(self):
        with state_module.use_identity(1001):
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            manifest = cave_treasure_runtime._plan_cave_public_small_world_action({
                "small_world": {
                    "available": True,
                    "has_world": True,
                    "has_prayer": True,
                    "prayer_title": "江河决堤",
                    "prayer_resources_ready": True,
                    "can_manifest": True,
                },
            })
            sermon = cave_treasure_runtime._plan_cave_public_small_world_action({
                "small_world": {
                    "available": True,
                    "has_world": True,
                    "has_prayer": False,
                    "faith": 81,
                    "faith_cap": 100,
                    "edict_remaining_seconds": 0,
                },
            })

        self.assertEqual("manifest", manifest["action"])
        self.assertEqual("miracle_sermon", sermon["action"])

    def test_cave_public_small_world_due_harvest_preempts_optional_sermon(self):
        now = 1_700_000_001.0
        with state_module.use_identity(1001):
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_next_public_harvest_at"] = now - 1
            plan = cave_treasure_runtime._plan_cave_public_small_world_action({
                "small_world": {
                    "available": True,
                    "has_world": True,
                    "has_prayer": False,
                    "can_harvest": True,
                    "faith": 80,
                    "faith_cap": 100,
                    "edict_remaining_seconds": 0,
                },
            }, now=now)

        self.assertEqual("collect", plan["action"])
        self.assertTrue(plan["harvest_due"])

    def test_cave_public_small_world_blocked_prayer_does_not_starve_due_harvest(self):
        now = 1_700_000_001.0
        with state_module.use_identity(1001):
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_next_public_harvest_at"] = now - 1
            plan = cave_treasure_runtime._plan_cave_public_small_world_action({
                "small_world": {
                    "available": True,
                    "has_world": True,
                    "has_prayer": True,
                    "prayer_title": "江河决堤",
                    "prayer_resources_ready": False,
                    "can_manifest": False,
                    "can_harvest": True,
                },
            }, now=now)

        self.assertEqual("collect", plan["action"])
        self.assertIn("祈愿暂不可处理", plan["reason"])

    def test_cave_public_small_world_does_not_auto_relief_for_population_deficit(self):
        with state_module.use_identity(1001):
            state_module.state["small_world_preach_enabled"] = True
            plan = cave_treasure_runtime._plan_cave_public_small_world_action({
                "small_world": {
                    "available": True,
                    "has_world": True,
                    "has_prayer": False,
                    "faith": 100,
                    "faith_cap": 100,
                    "population": 84000,
                    "population_cap": 100000,
                    "stability": 70,
                    "stability_cap": 100,
                    "edict_remaining_seconds": 0,
                },
            })

        self.assertNotIn("action", plan)

    def test_cave_public_small_world_high_stock_plan_suppresses_optional_actions(self):
        with state_module.use_identity(1001):
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_high_stock_silence_enabled"] = True
            state_module.state["small_world_barrier_min_stock"] = 130000
            plan = cave_treasure_runtime._plan_cave_public_small_world_action({
                "small_world": {
                    "available": True,
                    "has_world": True,
                    "has_prayer": False,
                    "faith": 80,
                    "faith_cap": 100,
                    "stability": 100,
                    "stability_cap": 100,
                    "population": 1000,
                    "population_cap": 1000,
                    "incense_stock": 150000,
                    "can_harvest": True,
                    "edict_remaining_seconds": 0,
                },
            })

        self.assertTrue(plan["silent"])
        self.assertTrue(plan["suppress_refresh"])
        self.assertNotIn("action", plan)
        self.assertIn("高香火静默", plan["reason"])

    def test_cave_public_small_world_high_stock_plan_is_disabled_by_default(self):
        with state_module.use_identity(1001):
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_high_stock_silence_enabled"] = False
            plan = cave_treasure_runtime._plan_cave_public_small_world_action({
                "small_world": {
                    "available": True,
                    "has_world": True,
                    "has_prayer": False,
                    "faith": 80,
                    "faith_cap": 100,
                    "incense_stock": 150000,
                    "edict_remaining_seconds": 0,
                },
            })

        self.assertEqual("miracle_sermon", plan["action"])
        self.assertNotIn("silent", plan)

    def test_cave_public_small_world_skips_sermon_for_minor_faith_drift(self):
        with state_module.use_identity(1001):
            state_module.state["small_world_preach_enabled"] = True
            plan = cave_treasure_runtime._plan_cave_public_small_world_action({
                "small_world": {
                    "available": True,
                    "has_world": True,
                    "has_prayer": False,
                    "faith": 93,
                    "faith_cap": 100,
                    "edict_remaining_seconds": 0,
                },
            })

        self.assertNotIn("action", plan)
        self.assertIn("当前无已启用", plan["reason"])

    async def test_cave_public_small_world_executes_action_and_updates_legacy_snapshot(self):
        flow_result = {
            "ok": True,
            "status": "acted",
            "data": {
                "action": "manifest",
                "plan": {"action": "manifest", "reason": "处理祈愿 江河决堤"},
                "action_result": {"rawMessage": "显灵成功，信仰 +3。"},
                "overview": {
                    "small_world": {
                        "available": True,
                        "has_world": True,
                        "faith": 84,
                        "faith_cap": 100,
                        "stability": 95,
                        "stability_cap": 100,
                        "population": 900,
                        "population_cap": 1000,
                        "incense_stock": 500,
                        "pending_incense": 0,
                        "has_prayer": False,
                    },
                },
            },
        }
        with state_module.use_identity(1001):
            state_module.state["small_world_manifest_enabled"] = True
            with patch.object(cave_treasure_runtime, "get_global_enabled", return_value=False), \
                    patch.object(cave_treasure_runtime, "get_global_pause_source", return_value="tianzun_maintenance"), \
                    patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                        "ok": True,
                        "init_data": "dwelling_init_data",
                        "player_id": 1001,
                        "result": {"ok": True, "data": {"raw": {"account": {"smallWorld": {"hasWorld": True}}}}},
                    })), \
                    patch.object(cave_treasure_runtime, "run_cave_small_world_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                    patch.object(cave_treasure_runtime, "save_state"), \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
                result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                    1001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    now=1_700_000_001.0,
                )

        self.assertTrue(result["ok"])
        self.assertEqual("manifest", result["extra"]["action"])
        self.assertIn("已显灵", result["message"])
        self.assertEqual(84, state_module.get_identity_state(1001)["small_world_faith_value"])
        flow_mock.assert_awaited_once()
        self.assertEqual("dwelling_init_data", flow_mock.await_args.kwargs["init_data"])
        self.assertEqual(1001, flow_mock.await_args.kwargs["player_id"])
        self.assertTrue(flow_mock.await_args.kwargs["initial_snapshot"]["account"]["smallWorld"]["hasWorld"])
        self.assertEqual("normal", audit_mock.await_args.kwargs["priority"])

    async def test_small_world_flow_reuses_split_details_snapshot_without_second_start(self):
        snapshot = {
            "account": {
                "smallWorld": {
                    "hasWorld": True,
                    "summary": {"faith": 94, "population": 250000, "stability": 100},
                    "actions": {"canCollect": False, "canManifest": False},
                },
            },
        }
        planner = Mock(return_value={})
        with patch.object(cave_treasure_miniapp, "execute_miniapp_http_request") as execute_mock:
            result = await cave_treasure_miniapp.run_cave_small_world_production_flow(
                1001,
                token="df_SECRET999",
                webview_url="https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                init_data="query_id=abc&hash=SECRET",
                player_id=1001,
                initial_snapshot=snapshot,
                action_planner=planner,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("noop", result["status"])
        self.assertEqual(94, result["data"]["overview"]["small_world"]["faith"])
        execute_mock.assert_not_called()
        planner.assert_called_once()

    async def test_small_world_flow_merges_partial_action_reply_with_details_snapshot(self):
        snapshot = {
            "account": {
                "smallWorld": {
                    "hasWorld": True,
                    "summary": {"faith": 94, "population": 250000, "stability": 100},
                    "actions": {"canCollect": False, "canManifest": True},
                },
            },
        }
        action_reply = SimpleNamespace(
            ok=True,
            error="",
            data={
                "snapshot": {"level": "action", "partial": True, "domains": ["smallWorld"]},
                "actionResult": {"ok": True, "rawMessage": "显灵成功"},
            },
        )
        with patch.object(cave_treasure_miniapp, "execute_miniapp_http_request", return_value=action_reply) as execute_mock:
            result = await cave_treasure_miniapp.run_cave_small_world_production_flow(
                1001,
                token="df_SECRET999",
                webview_url="https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                init_data="query_id=abc&hash=SECRET",
                player_id=1001,
                initial_snapshot=snapshot,
                action_planner=lambda _overview: {"action": "manifest"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual("acted", result["status"])
        self.assertEqual(94, result["data"]["overview"]["small_world"]["faith"])
        self.assertEqual("显灵成功", result["data"]["action_result"]["rawMessage"])
        execute_mock.assert_called_once()

    async def test_cave_public_small_world_collect_sets_independent_eight_hour_clock(self):
        now = 1_700_000_001.0
        flow_result = {
            "ok": True,
            "status": "acted",
            "data": {
                "action": "collect",
                "plan": {"action": "collect", "harvest_due": True, "reason": "MiniApp 8 小时收割到期"},
                "action_result": {"rawMessage": "收割成功，获得 800 香火。"},
                "overview": {
                    "small_world": {
                        "available": True,
                        "has_world": True,
                        "faith": 100,
                        "stability": 100,
                        "incense_stock": 1800,
                        "pending_incense": 0,
                        "has_prayer": False,
                    },
                },
            },
        }
        with state_module.use_identity(1001):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_next_public_harvest_at"] = now - 1
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                    "ok": True,
                    "init_data": "dwelling_init_data",
                    "player_id": 1001,
                    "result": {"ok": True},
                })), \
                patch.object(cave_treasure_runtime, "run_cave_small_world_production_flow", new=AsyncMock(return_value=flow_result)), \
                patch.object(cave_treasure_runtime, "save_state"), \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        with state_module.use_identity(1001):
            self.assertEqual(now, state_module.state["small_world_last_public_harvest_at"])
            self.assertEqual(
                now + cave_treasure_runtime.CAVE_SMALL_WORLD_HARVEST_INTERVAL_SEC,
                state_module.state["small_world_next_public_harvest_at"],
            )

    async def test_cave_public_small_world_empty_due_harvest_advances_eight_hours(self):
        now = 1_700_000_001.0
        flow_result = {
            "ok": True,
            "status": "noop",
            "data": {
                "plan": {
                    "harvest_due": True,
                    "harvest_checked": True,
                    "reason": "8 小时收割已检查，当前无可收香火",
                },
                "overview": {
                    "small_world": {
                        "available": True,
                        "has_world": True,
                        "faith": 100,
                        "stability": 100,
                        "incense_stock": 1000,
                        "pending_incense": 0,
                        "has_prayer": False,
                    },
                },
            },
        }
        with state_module.use_identity(1001):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_next_public_harvest_at"] = now - 1
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                    "ok": True,
                    "init_data": "dwelling_init_data",
                    "player_id": 1001,
                    "result": {"ok": True},
                })), \
                patch.object(cave_treasure_runtime, "run_cave_small_world_production_flow", new=AsyncMock(return_value=flow_result)), \
                patch.object(cave_treasure_runtime, "save_state"), \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        with state_module.use_identity(1001):
            self.assertEqual(0, state_module.state["small_world_last_public_harvest_at"])
            self.assertEqual(
                now + cave_treasure_runtime.CAVE_SMALL_WORLD_HARVEST_INTERVAL_SEC,
                state_module.state["small_world_next_public_harvest_at"],
            )

    async def test_cave_public_small_world_uses_shared_three_hour_god_cooldown(self):
        now = 1_700_000_001.0
        flow_result = {
            "ok": True,
            "status": "acted",
            "data": {
                "action": "miracle_sermon",
                "plan": {"action": "miracle_sermon", "reason": "信仰 60/100，执行布道"},
                "action_result": {"rawMessage": "布道成功。"},
                "overview": {
                    "small_world": {
                        "available": True,
                        "has_world": True,
                        "faith": 100,
                        "faith_cap": 100,
                        "stability": 100,
                        "stability_cap": 100,
                        "population": 900,
                        "population_cap": 1000,
                        "has_prayer": False,
                    },
                },
            },
        }
        with state_module.use_identity(1001):
            state_module.state["small_world_preach_enabled"] = True
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                    "ok": True,
                    "init_data": "dwelling_init_data",
                    "player_id": 1001,
                    "result": {"ok": True},
                })), \
                patch.object(cave_treasure_runtime, "run_cave_small_world_production_flow", new=AsyncMock(return_value=flow_result)), \
                patch.object(cave_treasure_runtime, "save_state"), \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        with state_module.use_identity(1001):
            self.assertEqual(
                now + cave_treasure_runtime.CAVE_SMALL_WORLD_GOD_COOLDOWN_SEC,
                state_module.state["small_world_god_cooldown_until"],
            )
            self.assertEqual(now + cave_treasure_runtime.CAVE_SMALL_WORLD_CYCLE_SEC, state_module.state["next_small_world_time"])

    async def test_cave_public_small_world_skips_http_before_identity_due_time(self):
        now = 1_700_000_001.0
        with state_module.use_identity(1001):
            state_module.state["next_small_world_time"] = now + 3600
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "run_cave_small_world_production_flow", new=AsyncMock()) as flow_mock:
            result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["extra"]["skipped"])
        flow_mock.assert_not_awaited()

    async def test_cave_public_small_world_fifth_refresh_returns_to_six_hour_cycle(self):
        now = 1_700_000_001.0
        flow_result = {
            "ok": True,
            "status": "noop",
            "data": {
                "plan": {"reason": "当前无已启用且可执行的小世界动作"},
                "overview": {
                    "small_world": {
                        "available": True,
                        "has_world": True,
                        "has_prayer": False,
                        "faith": 95,
                        "stability": 100,
                    },
                },
            },
        }
        with state_module.use_identity(1001):
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_refresh_count"] = 4
            state_module.state["next_small_world_time"] = now - 1
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                    "ok": True,
                    "init_data": "dwelling_init_data",
                    "player_id": 1001,
                    "result": {"ok": True},
                })), \
                patch.object(cave_treasure_runtime, "run_cave_small_world_production_flow", new=AsyncMock(return_value=flow_result)), \
                patch.object(cave_treasure_runtime, "save_state"), \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        with state_module.use_identity(1001):
            self.assertEqual(0, state_module.state["small_world_refresh_count"])
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(now + 6 * 3600, state_module.state["next_small_world_time"])

    async def test_cave_public_small_world_high_stock_silence_does_not_schedule_refresh(self):
        now = 1_700_000_001.0
        flow_result = {
            "ok": True,
            "status": "noop",
            "data": {
                "plan": {
                    "silent": True,
                    "suppress_refresh": True,
                    "reason": "高香火静默：库存 150000 已达阈值 130000，跳过刷新/维护",
                },
                "overview": {
                    "small_world": {
                        "available": True,
                        "has_world": True,
                        "has_prayer": False,
                        "faith": 100,
                        "faith_cap": 100,
                        "stability": 100,
                        "stability_cap": 100,
                        "population": 1000,
                        "population_cap": 1000,
                        "incense_stock": 150000,
                    },
                },
            },
        }
        with state_module.use_identity(1001):
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_high_stock_silence_enabled"] = True
            state_module.state["small_world_refresh_count"] = 1
            state_module.state["next_small_world_time"] = now - 1
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                    "ok": True,
                    "init_data": "dwelling_init_data",
                    "player_id": 1001,
                    "result": {"ok": True},
                })), \
                patch.object(cave_treasure_runtime, "run_cave_small_world_production_flow", new=AsyncMock(return_value=flow_result)), \
                patch.object(cave_treasure_runtime, "save_state"), \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        with state_module.use_identity(1001):
            self.assertEqual(0, state_module.state["small_world_refresh_count"])
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(now + cave_treasure_runtime.CAVE_SMALL_WORLD_CYCLE_SEC, state_module.state["next_small_world_time"])
            self.assertIn("高香火静默", state_module.state["small_world_last_error"])

    async def test_cave_public_small_world_enforces_persisted_minimum_request_interval(self):
        now = 1_700_000_001.0
        with state_module.use_identity(1001):
            state_module.state["next_small_world_time"] = now - 1
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_next_public_harvest_at"] = now - 1
            state_module.state["small_world_last_public_request_at"] = now - 60
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock()) as session_mock, \
                patch.object(cave_treasure_runtime, "save_state"):
            result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["extra"]["skipped"])
        self.assertEqual(
            now - 60 + cave_treasure_runtime.CAVE_SMALL_WORLD_MIN_REQUEST_SEC,
            result["extra"]["next_time"],
        )
        with state_module.use_identity(1001):
            self.assertEqual(
                now - 60 + cave_treasure_runtime.CAVE_SMALL_WORLD_MIN_REQUEST_SEC,
                state_module.state["small_world_next_public_harvest_at"],
            )
        session_mock.assert_not_awaited()

    async def test_cave_public_harvest_unavailable_panel_uses_short_retry(self):
        now = 1_700_000_001.0
        flow_result = {
            "ok": True,
            "status": "noop",
            "data": {
                "plan": {"reason": "小世界尚不可用"},
                "overview": {
                    "small_world": {
                        "available": False,
                        "has_world": False,
                    },
                },
            },
        }
        with state_module.use_identity(1001):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_next_public_harvest_at"] = now - 1
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                    "ok": True,
                    "init_data": "dwelling_init_data",
                    "player_id": 1001,
                    "result": {"ok": True},
                })), \
                patch.object(cave_treasure_runtime, "run_cave_small_world_production_flow", new=AsyncMock(return_value=flow_result)), \
                patch.object(cave_treasure_runtime, "save_state"), \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
                harvest_only=True,
            )

        self.assertTrue(result["ok"])
        with state_module.use_identity(1001):
            self.assertEqual(
                now + cave_treasure_runtime.CAVE_SMALL_WORLD_HARVEST_RETRY_SEC,
                state_module.state["small_world_next_public_harvest_at"],
            )

    async def test_cave_public_small_world_marks_request_before_session_failure(self):
        now = 1_700_000_001.0
        with state_module.use_identity(1001):
            state_module.state["next_small_world_time"] = now - 1
            state_module.state["small_world_last_public_request_at"] = 0
        with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                    "ok": False,
                    "error": "request timeout",
                })), \
                patch.object(cave_treasure_runtime, "save_state") as save_mock, \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_small_world_sync(
                1001,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=now,
            )

        self.assertFalse(result["ok"])
        with state_module.use_identity(1001):
            self.assertEqual(now, state_module.state["small_world_last_public_request_at"])
        save_mock.assert_called()

    async def test_cave_public_deep_retreat_allows_maintenance_pause_and_records(self):
        flow_result = {"ok": True, "status": "status", "data": {"data": {"deep_seclusion": {"active": True, "remaining_seconds": 88}}}}
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_enabled"] = True
            with patch.object(cave_treasure_runtime, "get_global_enabled", return_value=False), \
                    patch.object(cave_treasure_runtime, "get_global_pause_source", return_value="tianzun_maintenance"), \
                    patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                        "ok": True,
                        "init_data": "dwelling_init_data",
                        "player_id": 1001,
                        "result": {"ok": True},
                    })), \
                    patch.object(cave_treasure_runtime, "run_cave_deep_seclusion_action_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
                result = await cave_treasure_runtime.run_cave_public_deep_retreat_action(
                    1001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    "status",
                    now=1_700_000_001.0,
                )

        self.assertTrue(result["ok"])
        self.assertIn("洞府闭关 status 完成", result["message"])
        flow_mock.assert_awaited_once()
        self.assertEqual("dwelling_init_data", flow_mock.await_args.kwargs["init_data"])
        self.assertIn("1001:cave_deep_retreat", state_module.get_miniapp_state_records())

    async def test_cave_public_deep_retreat_allows_send_as_player_switch(self):
        state_module.set_identity_account(1001, 2001)
        flow_result = {"ok": True, "status": "start", "data": {"actionResult": {"ok": True, "rawMessage": "深度闭关成功"}}}
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_enabled"] = True
            with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                    patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                        "ok": True,
                        "init_data": "dwelling_init_data",
                        "player_id": 1001,
                        "result": {"ok": True},
                    })) as session_mock, \
                    patch.object(cave_treasure_runtime, "run_cave_deep_seclusion_action_production_flow", new=AsyncMock(return_value=flow_result)), \
                    patch.object(cave_treasure_runtime, "sync_cave_deep_seclusion_action_result", new=AsyncMock(return_value={
                        "handled": True,
                        "message_kind": "start",
                        "phase": "running",
                    })), \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
                result = await cave_treasure_runtime.run_cave_public_deep_retreat_action(
                    1001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    "start",
                    now=1_700_000_001.0,
                )

        self.assertTrue(result["ok"])
        session_mock.assert_awaited_once()

    def test_cave_public_deep_status_replaces_send_as_identity_scheduler(self):
        state_module.set_miniapp_auto_config({
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            "cave_public_deep_status_enabled": True,
            "cave_public_yuanying_enabled": True,
        })
        state_module.set_identity_account(1001, 2001)

        with state_module.use_identity(1001):
            self.assertTrue(state_module.is_cave_public_auto_enabled("deep_retreat"))
            self.assertTrue(state_module.is_cave_public_auto_enabled("yuanying"))

        state_module.set_identity_account(1001, 1001)
        with state_module.use_identity(1001):
            self.assertTrue(state_module.is_cave_public_auto_enabled("deep_retreat"))

    async def test_cave_public_deep_status_unrecognized_reply_defers_thirty_minutes(self):
        now = 1_700_000_001.0
        flow_result = {"ok": True, "status": "status", "data": {"actionResult": {"ok": True, "rawMessage": "状态读取完成"}}}
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["next_deep_retreat_time"] = now - 1
            with patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                    patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                        "ok": True,
                        "init_data": "dwelling_init_data",
                        "player_id": 1001,
                        "result": {"ok": True},
                    })), \
                    patch.object(cave_treasure_runtime, "run_cave_deep_seclusion_action_production_flow", new=AsyncMock(return_value=flow_result)), \
                    patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
                result = await cave_treasure_runtime.run_cave_public_deep_retreat_action(
                    1001,
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    "status",
                    now=now,
                )

        self.assertTrue(result["ok"])
        self.assertIn("30 分钟后保守复查", result["message"])
        with state_module.use_identity(1001):
            self.assertEqual(
                now + cave_treasure_runtime.CAVE_DEEP_STATUS_RECHECK_SEC,
                state_module.state["next_deep_retreat_time"],
            )


if __name__ == "__main__":
    unittest.main()
