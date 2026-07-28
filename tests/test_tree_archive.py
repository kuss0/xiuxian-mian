import copy
import inspect
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import tree


class TreeArchiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_status_points_to_miniapp_without_command_state(self):
        status = tree.get_tree_status_text()
        self.assertIn("旧版群命令自动化：已归档", status)
        self.assertIn("当前执行入口：MiniApp", status)

    def test_archive_surface_contains_no_game_send_path(self):
        source = inspect.getsource(tree)
        self.assertNotIn("send_game_command", source)
        self.assertNotIn("CMD_TREE_", source)

    def test_passive_pulse_panel_compatibility_updates_observed_state(self):
        identity_id = 990442
        state_module.ensure_identity_registered(identity_id)
        panel = (
            "【落云宗 · 灵树玩法】\n"
            "进度：72.50%\n"
            "主脉【木】 / 辅脉【水】 / 逆脉【火】 / 平脉【金、土】\n"
            "脉稳：62/100\n"
            "浊息/紊乱：3/165\n"
            "今日定脉令：1/6\n"
            "冲脉 2/3\n"
            "命令：.定脉 固脉 木、.定脉 净浊 水"
        )

        parsed = tree.parse_tree_pulse_panel(panel)
        self.assertIsNotNone(parsed)
        self.assertEqual(72.5, parsed["progress"])
        self.assertEqual(["金", "土"], parsed["neutral_elements"])
        self.assertEqual([".定脉 固脉 木", ".定脉 净浊 水"], parsed["available_commands"])

        with state_module.use_identity(identity_id):
            self.assertTrue(tree._apply_tree_pulse_panel(parsed, 1700000000))
            self.assertTrue(state_module.state["tree_pulse_mode_seen"])
            self.assertEqual(72.5, state_module.state["tree_pulse_progress"])
            self.assertEqual(1, state_module.state["tree_pulse_daily_used"])
            self.assertEqual(6, state_module.state["tree_pulse_daily_limit"])

    def test_bootstrap_request_only_clears_legacy_flags(self):
        identity_id = 990441
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tree_bootstrap_check_needed"] = True
            state_module.state["tree_bootstrap_check_due_at"] = 1234

            self.assertFalse(tree.request_tree_bootstrap_check(1000, min_sec=1, max_sec=2))
            self.assertFalse(state_module.state["tree_bootstrap_check_needed"])
            self.assertEqual(0, state_module.state["tree_bootstrap_check_due_at"])

    async def test_legacy_handlers_and_schedulers_are_noops(self):
        self.assertFalse(await tree.handle_tree_invasion_start("灵树遭袭", 1000))
        self.assertFalse(await tree.handle_tree_invasion_end("入侵结束", 1000, True))
        self.assertFalse(await tree.handle_tree_rebirth_reset("灵树轮回", 1000))
        self.assertFalse(await tree.handle_tree_cd_fix("冷却", 1000, None, "tree_panel"))
        self.assertFalse(await tree.handle_tree_exception_prompt("异常", 1000))
        self.assertFalse(await tree.handle_tree_panel("旧面板", 1000, True))
        self.assertFalse(await tree.handle_tree_harvest_reply("旧采摘", 1000, None, "tree_harvest", 12))
        self.assertFalse(await tree.run_tree_bootstrap_check(1000))
        self.assertFalse(await tree.run_tree_scheduler(1000))


if __name__ == "__main__":
    unittest.main()
