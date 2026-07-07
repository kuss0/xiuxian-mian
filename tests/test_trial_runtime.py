import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import trial_runtime


def _trial_event(url="https://t.me/fanrenxiuxian_bot/app?startapp=trial_SECRET999"):
    button = SimpleNamespace(button=SimpleNamespace(text="进入天机试炼", url=url))
    return SimpleNamespace(id=5001, message=SimpleNamespace(buttons=[[button]]))


class TrialRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._manual_auth = dict(trial_runtime._MANUAL_AUTH_UNTIL)
        self._batch_runs = copy.deepcopy(trial_runtime._BATCH_RUNS)
        self._batch_by_identity = dict(trial_runtime._BATCH_BY_IDENTITY)
        trial_runtime._MANUAL_AUTH_UNTIL.clear()
        trial_runtime._BATCH_RUNS.clear()
        trial_runtime._BATCH_BY_IDENTITY.clear()
        trial_runtime._RUN_LOCKS.clear()
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.ensure_identity_registered(1001)
        state_module.update_send_as_profile(1001, username="xuruode4", label="竹灵 2")
        state_module.ensure_identity_registered(1002)
        state_module.update_send_as_profile(1002, username="xuruode5", label="竹灵 3")

    def tearDown(self):
        trial_runtime._MANUAL_AUTH_UNTIL.clear()
        trial_runtime._MANUAL_AUTH_UNTIL.update(self._manual_auth)
        trial_runtime._BATCH_RUNS.clear()
        trial_runtime._BATCH_RUNS.update(copy.deepcopy(self._batch_runs))
        trial_runtime._BATCH_BY_IDENTITY.clear()
        trial_runtime._BATCH_BY_IDENTITY.update(self._batch_by_identity)
        trial_runtime._RUN_LOCKS.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_trial_entry_ignored_without_manual_authorization(self):
        with state_module.use_identity(1001):
            with patch.object(trial_runtime, "run_trial_miniapp_production_flow", new=AsyncMock()) as flow_mock:
                handled = await trial_runtime.handle_trial_miniapp_entry(
                    _trial_event(),
                    "【天机试炼台】灵脉点穴",
                    1_700_000_000.0,
                )

        self.assertFalse(handled)
        flow_mock.assert_not_awaited()

    async def test_trial_entry_runs_once_after_manual_authorization(self):
        trial_runtime.authorize_trial_miniapp_manual_run(1001, now=1_700_000_000.0)
        flow_result = {"ok": True, "status": "settled", "data": {"settled_count": 2}}
        with state_module.use_identity(1001):
            with patch.object(trial_runtime, "run_trial_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                    patch.object(trial_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await trial_runtime.handle_trial_miniapp_entry(
                    _trial_event(),
                    "【天机试炼台】灵脉点穴",
                    1_700_000_001.0,
                    result_msg_id=5001,
                )

        self.assertTrue(handled)
        flow_mock.assert_awaited_once()
        kwargs = flow_mock.await_args.kwargs
        self.assertEqual("trial_SECRET999", kwargs["token"])
        self.assertEqual(99, kwargs["max_rounds"])
        self.assertIn("capture_sink", kwargs)
        self.assertEqual("trial_runtime:1001:5001", kwargs["capture_source"])
        self.assertNotIn(1001, trial_runtime._MANUAL_AUTH_UNTIL)
        audit_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list)
        self.assertIn("天机试炼 MiniApp 接管入口", audit_text)
        self.assertIn("天机试炼结果", audit_text)

    async def test_trial_batch_collects_results_and_sends_one_summary(self):
        with patch.object(trial_runtime.asyncio, "create_task", side_effect=lambda coro: coro.close()):
            batch_id = trial_runtime.start_trial_miniapp_batch_run([1001, 1002], now=1_700_000_000.0)
        self.assertTrue(batch_id)
        trial_runtime.note_trial_batch_send_result(batch_id, 1001, ok=True, msg_id=11)
        trial_runtime.note_trial_batch_send_result(batch_id, 1002, ok=True, msg_id=12)
        trial_runtime._record_trial_batch_result(batch_id, 1001, {
            "ok": True,
            "status": "settled",
            "data": {"reward_trace": 9, "rewards": [{"name": "灵脉砂", "qty": 2}]},
        })
        trial_runtime._record_trial_batch_result(batch_id, 1002, {
            "ok": True,
            "status": "settled",
            "data": {"traceGain": 11, "bonusLoot": [{"name": "玄晶", "qty": 1}]},
        })

        with patch.object(trial_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
            finalized = await trial_runtime.finalize_trial_batch_run(batch_id)

        self.assertTrue(finalized)
        audit_mock.assert_awaited_once()
        text = str(audit_mock.await_args.args[0])
        self.assertIn("天机试炼批量结果｜2/2 成功", text)
        self.assertIn("收益：天机残痕+20", text)
        self.assertIn("奖励：灵脉砂x2、玄晶x1", text)
        self.assertIn("成功：", text)
        self.assertNotIn(batch_id, trial_runtime._BATCH_RUNS)

    async def test_trial_entry_respects_global_pause_before_http(self):
        trial_runtime.authorize_trial_miniapp_manual_run(1001, now=1_700_000_000.0)
        with state_module.use_identity(1001):
            with patch.object(trial_runtime, "get_global_enabled", return_value=False), \
                    patch.object(trial_runtime, "run_trial_miniapp_production_flow", new=AsyncMock()) as flow_mock, \
                    patch.object(trial_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await trial_runtime.handle_trial_miniapp_entry(
                    _trial_event(),
                    "【天机试炼台】灵脉点穴",
                    1_700_000_001.0,
                    result_msg_id=5001,
                )

        self.assertTrue(handled)
        flow_mock.assert_not_awaited()
        self.assertNotIn(1001, trial_runtime._MANUAL_AUTH_UNTIL)
        audit_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list)
        self.assertIn("全局暂停", audit_text)

    async def test_trial_result_reports_game_materials_not_technical_fields(self):
        trial_runtime.authorize_trial_miniapp_manual_run(1001, now=1_700_000_000.0)
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {
                "settled_count": 2,
                "results": [
                    {
                        "expGain": 10,
                        "traceGain": 1,
                        "rewards": [{"name": "灵脉砂", "qty": 2}],
                        "score": 99,
                        "sessionId": 123,
                    },
                    {
                        "expGain": 5,
                        "reward_trace": 11,
                        "bonusLoot": [{"name": "玄晶", "qty": 1}],
                    },
                ],
            },
        }
        with state_module.use_identity(1001):
            with patch.object(trial_runtime, "run_trial_miniapp_production_flow", new=AsyncMock(return_value=flow_result)), \
                    patch.object(trial_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await trial_runtime.handle_trial_miniapp_entry(
                    _trial_event(),
                    "【天机试炼台】灵脉点穴",
                    1_700_000_001.0,
                    result_msg_id=5001,
                )

        self.assertTrue(handled)
        result_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list if "天机试炼结果" in str(call.args[0]))
        self.assertIn("收益:天机残痕+12、经验+15", result_text)
        self.assertIn("奖励:灵脉砂x2、玄晶x1", result_text)
        self.assertNotIn("score", result_text)
        self.assertNotIn("session", result_text)

    async def test_expired_trial_authorization_does_not_run(self):
        trial_runtime.authorize_trial_miniapp_manual_run(1001, now=1_700_000_000.0, ttl_sec=60)
        with state_module.use_identity(1001):
            with patch.object(trial_runtime, "run_trial_miniapp_production_flow", new=AsyncMock()) as flow_mock:
                handled = await trial_runtime.handle_trial_miniapp_entry(
                    _trial_event(),
                    "【天机试炼台】灵脉点穴",
                    1_700_000_120.0,
                )

        self.assertFalse(handled)
        flow_mock.assert_not_awaited()
        self.assertNotIn(1001, trial_runtime._MANUAL_AUTH_UNTIL)

    async def test_unrouted_trial_entry_requires_authorized_username_match(self):
        trial_runtime.authorize_trial_miniapp_manual_run(1001, now=1_700_000_000.0)
        with state_module.use_identity(1001):
            with patch.object(trial_runtime, "run_trial_miniapp_production_flow", new=AsyncMock()) as flow_mock:
                handled = await trial_runtime.handle_trial_miniapp_entry(
                    _trial_event(),
                    "【天机试炼台】\n道友 @other，本次 初阶·魔网解线 入口已开启。",
                    1_700_000_001.0,
                    require_identity_match=True,
                )

        self.assertFalse(handled)
        flow_mock.assert_not_awaited()
        self.assertIn(1001, trial_runtime._MANUAL_AUTH_UNTIL)

        flow_result = {"ok": True, "status": "settled", "data": {"settled_count": 1}}
        with state_module.use_identity(1001):
            with patch.object(trial_runtime, "run_trial_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock, \
                    patch.object(trial_runtime, "send_audit_log", new=AsyncMock()):
                handled = await trial_runtime.handle_trial_miniapp_entry(
                    _trial_event(),
                    "【天机试炼台】\n道友 @xuruode4，本次 初阶·魔网解线 入口已开启。",
                    1_700_000_002.0,
                    result_msg_id=5001,
                    require_identity_match=True,
                )

        self.assertTrue(handled)
        flow_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
