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


class TreeRuntimeEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._manual_auth = dict(tree_runtime._MANUAL_AUTH)
        tree_runtime._MANUAL_AUTH.clear()
        tree_runtime._RUN_LOCKS.clear()
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.ensure_identity_registered(1001)
        state_module.ensure_identity_registered(1002)
        state_module.update_send_as_profile(1001, username="first_identity", label="first")
        state_module.update_send_as_profile(1002, username="imcanonical_ai", label="反向的钟")

    def tearDown(self):
        tree_runtime._MANUAL_AUTH.clear()
        tree_runtime._MANUAL_AUTH.update(self._manual_auth)
        tree_runtime._RUN_LOCKS.clear()
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


if __name__ == "__main__":
    unittest.main()
