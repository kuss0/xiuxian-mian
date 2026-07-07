import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import cave_treasure_runtime


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
        state_module.ensure_identity_registered(1001)
        state_module.update_send_as_profile(1001, username="xuruode4", label="竹灵 2")

    def tearDown(self):
        cave_treasure_runtime._MANUAL_AUTH_UNTIL.clear()
        cave_treasure_runtime._MANUAL_AUTH_UNTIL.update(self._manual_auth)
        cave_treasure_runtime._RUN_LOCKS.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

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
                "state": {"games_used": 3, "games_limit": 3},
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

    async def test_cave_entry_respects_global_pause_before_http(self):
        cave_treasure_runtime.authorize_cave_treasure_miniapp_manual_run(1001, now=1_700_000_000.0)
        with state_module.use_identity(1001):
            with patch.object(cave_treasure_runtime, "get_global_enabled", return_value=False), \
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
        self.assertIn("奖励:古禁印痕x1、灵石x31、玄晶x2", result_text)
        self.assertNotIn("score", result_text)
        self.assertNotIn("session", result_text)
        self.assertNotIn("quality", result_text)

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


if __name__ == "__main__":
    unittest.main()
