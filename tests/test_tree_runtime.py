import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import state as state_module
from model.features import tree_runtime


def _tree_event(url="https://t.me/fanrenxiuxian_bot/app?startapp=tree_SECRET999"):
    button = SimpleNamespace(button=SimpleNamespace(text="进入灵树", url=url))
    return SimpleNamespace(id=7001, message=SimpleNamespace(buttons=[[button]]))


class TreeRuntimeSummaryTests(unittest.TestCase):
    def test_tree_daily_summary_reports_podium_scores_and_safe_target(self):
        summary = tree_runtime._format_tree_summary({
            "ok": True,
            "status": "settled",
            "data": {
                "phase": "settled",
                "quotas": {},
                "runs": [
                    {
                        "mode": "jump",
                        "score": 13,
                        "ranking_target": {
                            "top_scores": [19, 15, 12],
                            "target_score": 13,
                        },
                    },
                ],
                "rewards": {},
            },
        })

        self.assertIn("跳榜前三=19/15/12，目标13", summary)

    def test_tree_success_summary_does_not_report_scores_as_rewards(self):
        summary = tree_runtime._format_tree_summary({
            "ok": True,
            "status": "settled",
            "data": {
                "mode": "jump",
                "proof_summary": {"score": 30, "targetScore": 30},
                "submit": {"score": 30},
            },
        })

        self.assertIn("MiniApp settled", summary)
        self.assertIn("跳一跳", summary)
        self.assertIn("未解析到新增物资", summary)
        self.assertNotIn("分数", summary)
        self.assertNotIn("目标", summary)
        self.assertNotIn("30", summary)

    def test_tree_mode_exhausted_summary_omits_best_score(self):
        summary = tree_runtime._format_tree_summary({
            "ok": False,
            "status": "mode_exhausted",
            "data": {
                "mode": "fly",
                "state": {
                    "jump": {"used": 3, "limit": 3, "best": 126},
                    "fly": {"used": 1, "limit": 1, "best": 88},
                },
            },
        })

        self.assertIn("飞一飞次数已用完", summary)
        self.assertIn("跳一跳 3/3", summary)
        self.assertIn("飞一飞 1/1", summary)
        self.assertNotIn("best", summary)
        self.assertNotIn("126", summary)
        self.assertNotIn("88", summary)

    def test_tree_zero_score_summary_reports_server_verification(self):
        summary = tree_runtime._format_tree_summary({
            "ok": False,
            "status": "zero_score",
            "error": "fly server score is zero; stop remaining daily attempts",
            "data": {
                "phase": "blocked",
                "quotas": {},
                "runs": [{
                    "mode": "fly",
                    "score": 0,
                    "server_verification": {
                        "ok": False,
                        "hit": True,
                        "score": 0,
                        "durationMs": 1234,
                    },
                }],
                "rewards": {},
            },
        })

        self.assertIn("飞分 0", summary)
        self.assertIn("服务校验 ok=0,hit=1,score=0,durationMs=1234", summary)
        self.assertIn("stop remaining daily attempts", summary)


class TreeRuntimeEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._manual_auth = dict(tree_runtime._MANUAL_AUTH)
        self._coordinator = dict(tree_runtime._COORDINATOR)
        tree_runtime._MANUAL_AUTH.clear()
        tree_runtime._GLOBAL_RUN_LOCK = None
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.ensure_identity_registered(1001)
        state_module.ensure_identity_registered(1002)
        state_module.update_send_as_profile(1001, username="first_identity", label="first", sect_name="落云宗")
        state_module.update_send_as_profile(1002, username="imcanonical_ai", label="反向的钟", sect_name="落云宗")

    def tearDown(self):
        tree_runtime._MANUAL_AUTH.clear()
        tree_runtime._MANUAL_AUTH.update(self._manual_auth)
        tree_runtime._GLOBAL_RUN_LOCK = None
        tree_runtime._COORDINATOR.clear()
        tree_runtime._COORDINATOR.update(self._coordinator)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_tree_entry_can_resolve_manual_auth_from_mention_without_context(self):
        tree_runtime.authorize_tree_miniapp_manual_run(
            1002,
            now=1_700_000_000.0,
            mode="jump",
            score_profile={"target_score": 20},
            submit=True,
        )
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {"mode": "jump", "proof_summary": {"score": 18}, "submit": {"score": 18}},
        }

        with patch.object(
            tree_runtime,
            "run_tree_miniapp_game_production_flow",
            new=AsyncMock(return_value=flow_result),
        ) as flow_mock, patch.object(tree_runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
            handled = await tree_runtime.handle_tree_miniapp_entry(
                _tree_event(),
                "【落云宗 · 灵眼之树】\n@imcanonical_ai 已抵达云梦山灵树台。\n\n点击下方 进入灵树。",
                1_700_000_001.0,
                result_msg_id=7001,
            )

        self.assertTrue(handled)
        flow_mock.assert_awaited_once()
        kwargs = flow_mock.await_args.kwargs
        self.assertEqual("tree_SECRET999", kwargs["token"])
        self.assertEqual("jump", kwargs["mode"])
        self.assertTrue(kwargs["submit"])
        self.assertEqual("tree_runtime:1002:7001", kwargs["capture_source"])
        self.assertNotIn(1002, tree_runtime._MANUAL_AUTH)
        audit_send_as_ids = [call.kwargs.get("send_as_id") for call in audit_mock.await_args_list]
        self.assertEqual([1002, 1002], audit_send_as_ids)

    async def test_tree_entry_requires_explicit_manual_auth_even_when_tracked_family_matches(self):
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {"mode": "jump", "proof_summary": {"score": 16}, "submit": {"score": 16}},
        }

        with state_module.use_identity(1002):
            with patch.object(
                tree_runtime,
                "run_tree_miniapp_game_production_flow",
                new=AsyncMock(return_value=flow_result),
            ) as flow_mock, patch.object(tree_runtime, "send_audit_log", new=AsyncMock()):
                handled = await tree_runtime.handle_tree_miniapp_entry(
                    _tree_event(),
                    "【落云宗 · 灵眼之树】\n@imcanonical_ai 已抵达云梦山灵树台。\n\n点击下方 进入灵树。",
                    1_700_000_001.0,
                    matched_family="tree_miniapp",
                    result_msg_id=7002,
                )

        self.assertFalse(handled)
        flow_mock.assert_not_awaited()

    async def test_daily_authorization_requires_enabled_identity_and_luoyun_sect(self):
        disabled = tree_runtime.prepare_tree_miniapp_daily_run(1001, enabled=False, now=1_700_000_000.0)
        self.assertFalse(disabled["ok"])

        state_module.update_send_as_profile(1001, sect_name="星宫")
        wrong_sect = tree_runtime.prepare_tree_miniapp_daily_run(1001, enabled=True, now=1_700_000_000.0)
        self.assertFalse(wrong_sect["ok"])
        self.assertIn("宗门不匹配", wrong_sect["reason"])

    async def test_channel_health_frozen_identity_can_use_public_tree_entry(self):
        state_module.update_send_as_profile(1002, enabled=False)
        state_module.set_channel_send_as_health({
            "status": "closed",
            "restore_identity_ids": [1002],
            "frozen_identity_ids": [1002],
        })

        eligible, reason = tree_runtime.check_tree_miniapp_eligibility(1002, enabled=True)
        prepared = tree_runtime.prepare_tree_miniapp_daily_run(
            1002,
            enabled=True,
            day_key="2026-07-16",
            now=1_700_000_000.0,
        )

        self.assertTrue(eligible, reason)
        self.assertTrue(prepared["ok"])

    async def test_manually_disabled_identity_cannot_use_public_tree_entry(self):
        state_module.update_send_as_profile(1002, enabled=False)
        state_module.set_channel_send_as_health({
            "status": "closed",
            "restore_identity_ids": [],
            "frozen_identity_ids": [],
        })

        eligible, reason = tree_runtime.check_tree_miniapp_eligibility(1002, enabled=True)

        self.assertFalse(eligible)
        self.assertEqual("身份已停用", reason)

    async def test_daily_entry_binds_reply_chain_and_runs_daily_flow(self):
        prepared = tree_runtime.prepare_tree_miniapp_daily_run(
            1002,
            enabled=True,
            day_key="2026-07-13",
            now=1_700_000_000.0,
        )
        self.assertTrue(tree_runtime.finalize_tree_miniapp_daily_command(prepared["op_id"], 8123, now=1_700_000_001.0))
        flow_result = {
            "ok": True,
            "status": "completed",
            "data": {
                "phase": "completed",
                "quotas": {
                    "jump": {"used": 2, "limit": 2, "remaining": 0},
                    "fly": {"used": 1, "limit": 1, "remaining": 0},
                },
                "runs": [{"mode": "jump", "score": 18}],
                "rewards": {"items": {"灵木": 1}, "gains": {}},
            },
        }

        with patch.object(
            tree_runtime,
            "run_tree_miniapp_daily_production_flow",
            new=AsyncMock(return_value=flow_result),
        ) as flow_mock, patch.object(tree_runtime, "send_audit_log", new=AsyncMock()):
            wrong_chain = await tree_runtime.handle_tree_miniapp_entry(
                _tree_event(),
                "【落云宗 · 灵眼之树】 @imcanonical_ai",
                1_700_000_002.0,
                reply_to=SimpleNamespace(id=9000),
            )
            handled = await tree_runtime.handle_tree_miniapp_entry(
                _tree_event(),
                "【落云宗 · 灵眼之树】 @imcanonical_ai",
                1_700_000_003.0,
                reply_to=SimpleNamespace(id=8123),
                result_msg_id=7001,
            )

        self.assertFalse(wrong_chain)
        self.assertTrue(handled)
        flow_mock.assert_awaited_once()
        self.assertEqual("completed", tree_runtime.get_tree_miniapp_coordinator_snapshot()["phase"])

    async def test_direct_daily_flow_reuses_supplied_init_data(self):
        flow_result = {
            "ok": True,
            "status": "completed",
            "data": {
                "phase": "completed",
                "quotas": {
                    "jump": {"used": 1, "limit": 1, "remaining": 0},
                    "fly": {"used": 1, "limit": 1, "remaining": 0},
                },
                "runs": [],
                "rewards": {},
            },
        }
        with patch.object(
            tree_runtime,
            "run_tree_miniapp_daily_production_flow",
            new=AsyncMock(return_value=flow_result),
        ) as flow_mock, patch.object(tree_runtime, "send_audit_log", new=AsyncMock()):
            result = await tree_runtime.run_tree_miniapp_daily_direct(
                1002,
                token="tree_SECRET999",
                webview_url="https://t.me/fanrenxiuxian_bot?startapp=tree_SECRET999",
                init_data="dwelling_init_data",
                day_key="2026-07-14",
                op_id="tree_daily:2026-07-14:1002",
            )

        self.assertTrue(result["ok"])
        self.assertEqual("dwelling_init_data", flow_mock.await_args.kwargs["init_data"])
        self.assertEqual("completed", tree_runtime.get_tree_miniapp_coordinator_snapshot()["phase"])

    async def test_broadcast_fallback_requires_exact_mention_and_bound_authorization(self):
        prepared = tree_runtime.prepare_tree_miniapp_daily_run(1002, enabled=True, now=1_700_000_000.0)
        with patch.object(
            tree_runtime,
            "run_tree_miniapp_daily_production_flow",
            new=AsyncMock(),
        ) as flow_mock:
            unbound = await tree_runtime.handle_tree_miniapp_entry(
                _tree_event(),
                "【落云宗 · 灵眼之树】 @imcanonical_ai",
                1_700_000_001.0,
                require_identity_match=True,
            )
            tree_runtime.finalize_tree_miniapp_daily_command(prepared["op_id"], 8123, now=1_700_000_002.0)
            missing_mention = await tree_runtime.handle_tree_miniapp_entry(
                _tree_event(),
                "【落云宗 · 灵眼之树】",
                1_700_000_003.0,
                require_identity_match=True,
            )

        self.assertFalse(unbound)
        self.assertFalse(missing_mention)
        flow_mock.assert_not_awaited()

    async def test_global_coordinator_rejects_second_identity_before_command_send(self):
        first = tree_runtime.prepare_tree_miniapp_daily_run(1001, enabled=True, now=1_700_000_000.0)
        self.assertTrue(first["ok"])
        tree_runtime.finalize_tree_miniapp_daily_command(first["op_id"], 8101, now=1_700_000_001.0)
        second = tree_runtime.prepare_tree_miniapp_daily_run(1002, enabled=True, now=1_700_000_000.0)
        self.assertFalse(second["ok"])
        self.assertEqual(1001, second["active_identity_id"])
        self.assertNotIn(1002, tree_runtime._MANUAL_AUTH)

        self.assertTrue(tree_runtime.cancel_tree_miniapp_daily_run(first["op_id"], reason="send failed"))
        retry = tree_runtime.prepare_tree_miniapp_daily_run(1002, enabled=True, now=1_700_000_002.0)
        self.assertTrue(retry["ok"])


if __name__ == "__main__":
    unittest.main()
