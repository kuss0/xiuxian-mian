import unittest
import json
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import ui
from model import state as state_module


class MiniAppEntryProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_miniapp_send_whitelists_are_exact(self):
        self.assertEqual({"cave_treasure", "fishing", "stargazer", "tree", "trial"}, set(ui.MINIAPP_ENTRY_PROBE_COMMANDS))
        self.assertEqual({"cave_treasure", "stargazer", "tree", "trial"}, set(ui.MINIAPP_MANUAL_RUN_COMMANDS))
        self.assertNotIn("world_boss", ui.MINIAPP_ENTRY_PROBE_COMMANDS)
        self.assertNotIn("world_boss", ui.MINIAPP_MANUAL_RUN_COMMANDS)
        self.assertNotIn("fishing", ui.MINIAPP_MANUAL_RUN_COMMANDS)

    async def test_tree_score_config_is_ui_adjustable_tens_policy(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "save_state", return_value=True) as save_mock:
            ok, message = await ui.ui_set_tree_miniapp_score_config(1001, {
                "jump_target_score": 7,
                "fly_target_score": 999,
            })

        snapshot = ui.get_miniapp_status_snapshot(send_as_id=1001)
        tree = snapshot["score_controls"]["tree"]

        self.assertTrue(ok)
        self.assertIn("跳一跳 20", message)
        self.assertIn("飞一飞 150", message)
        self.assertEqual([20, 20], tree["jump"]["target_score_range"])
        self.assertEqual([150, 150], tree["fly"]["target_score_range"])
        self.assertEqual(20, tree["jump"]["min_target_score"])
        self.assertEqual(150, tree["fly"]["max_target_score"])
        save_mock.assert_called_once()

    async def test_tree_score_config_is_scoped_by_identity(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001, 1002]), \
                patch.object(ui, "save_state", return_value=True):
            ok_first, _message_first = await ui.ui_set_tree_miniapp_score_config(1001, {
                "jump_target_score": 28,
                "fly_target_score": 36,
            })
            ok_second, _message_second = await ui.ui_set_tree_miniapp_score_config(1002, {
                "jump_target_score": 44,
                "fly_target_score": 52,
            })

        first = ui.get_miniapp_status_snapshot(send_as_id=1001)["score_controls"]["tree"]
        second = ui.get_miniapp_status_snapshot(send_as_id=1002)["score_controls"]["tree"]
        default = ui.get_miniapp_status_snapshot(send_as_id=1003)["score_controls"]["tree"]

        self.assertTrue(ok_first)
        self.assertTrue(ok_second)
        self.assertEqual([28, 28], first["jump"]["target_score_range"])
        self.assertEqual([36, 36], first["fly"]["target_score_range"])
        self.assertEqual([44, 44], second["jump"]["target_score_range"])
        self.assertEqual([52, 52], second["fly"]["target_score_range"])
        self.assertEqual([24, 42], default["jump"]["target_score_range"])
        self.assertEqual([24, 45], default["fly"]["target_score_range"])

    async def test_tree_score_config_rejects_non_numeric_before_save(self):
        with patch.object(ui, "get_identity_ids", return_value=[1002]), \
                patch.object(ui, "save_state", return_value=True) as save_mock:
            ok, message = await ui.ui_set_tree_miniapp_score_config(1002, {
                "jump_target_score": "abc",
                "fly_target_score": 30,
            })

        self.assertFalse(ok)
        self.assertIn("目标分必须是数字", message)
        save_mock.assert_not_called()

    async def test_probe_sends_only_whitelisted_entry_command_without_tracking(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12345))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_entry_probe(1001, "fishing")

        self.assertTrue(ok)
        self.assertIn("入口诊断", message)
        self.assertEqual(".钓鱼", extra["command"])
        self.assertEqual(12345, extra["msg_id"])
        send_mock.assert_awaited_once()
        kwargs = send_mock.await_args.kwargs
        self.assertFalse(kwargs["track"])
        self.assertEqual(0, kwargs["max_retry"])
        self.assertEqual(1001, kwargs["send_as_id"])
        self.assertEqual("MiniApp诊断", kwargs["source_module"])
        self.assertEqual("miniapp_entry_probe", kwargs["chain_id"])

    async def test_probe_rejects_unknown_game_key_before_send(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12345))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "send_game_command", new=send_mock):
            ok, message, extra = await ui.ui_send_miniapp_entry_probe(1001, "world_boss")

        self.assertFalse(ok)
        self.assertIn("仅允许", message)
        self.assertEqual({}, extra)
        send_mock.assert_not_awaited()

    async def test_probe_allows_trial_entry_command_without_tracking(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12346))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_entry_probe(1001, "trial")

        self.assertTrue(ok)
        self.assertIn("入口诊断", message)
        self.assertEqual(".天机试炼", extra["command"])
        self.assertEqual(12346, extra["msg_id"])
        kwargs = send_mock.await_args.kwargs
        self.assertFalse(kwargs["track"])
        self.assertEqual(0, kwargs["max_retry"])
        self.assertEqual("MiniApp诊断", kwargs["source_module"])

    async def test_probe_allows_cave_treasure_entry_command_without_tracking(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12347))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_entry_probe(1001, "cave_treasure")

        self.assertTrue(ok)
        self.assertIn("入口诊断", message)
        self.assertEqual(".洞府", extra["command"])
        self.assertEqual(12347, extra["msg_id"])
        kwargs = send_mock.await_args.kwargs
        self.assertFalse(kwargs["track"])
        self.assertEqual(0, kwargs["max_retry"])
        self.assertEqual("MiniApp诊断", kwargs["source_module"])
        self.assertEqual("miniapp_entry_probe", kwargs["chain_id"])

    async def test_probe_allows_tree_entry_command_without_tracking(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12352))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_entry_probe(1001, "tree")

        self.assertTrue(ok)
        self.assertIn("入口诊断", message)
        self.assertEqual(".灵树", extra["command"])
        self.assertEqual(12352, extra["msg_id"])
        kwargs = send_mock.await_args.kwargs
        self.assertFalse(kwargs["track"])
        self.assertEqual(0, kwargs["max_retry"])
        self.assertEqual("MiniApp诊断", kwargs["source_module"])
        self.assertEqual("miniapp_entry_probe", kwargs["chain_id"])

    async def test_probe_rejects_disabled_identity_before_send(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12345))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=False), \
                patch.object(ui, "send_game_command", new=send_mock):
            ok, message, extra = await ui.ui_send_miniapp_entry_probe(1001, "stargazer")

        self.assertFalse(ok)
        self.assertEqual("身份已停用", message)
        self.assertEqual({}, extra)
        send_mock.assert_not_awaited()

    async def test_manual_run_allows_trial_and_authorizes_before_send(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12348))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "authorize_trial_miniapp_manual_run", return_value=123456.0) as auth_mock, \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_manual_run(1001, "trial")

        self.assertTrue(ok)
        self.assertIn("手动执行", message)
        self.assertEqual(".天机试炼", extra["command"])
        auth_mock.assert_called_once_with(1001)
        kwargs = send_mock.await_args.kwargs
        self.assertFalse(kwargs["track"])
        self.assertEqual(0, kwargs["max_retry"])
        self.assertEqual("MiniApp手动", kwargs["source_module"])
        self.assertEqual("miniapp_manual_run", kwargs["chain_id"])

    async def test_manual_run_allows_stargazer_and_authorizes_before_send(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12350))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "authorize_stargazer_miniapp_manual_run", return_value=123456.0) as auth_mock, \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_manual_run(1001, "stargazer")

        self.assertTrue(ok)
        self.assertIn("手动执行", message)
        self.assertEqual(".观星台", extra["command"])
        auth_mock.assert_called_once_with(1001)
        kwargs = send_mock.await_args.kwargs
        self.assertFalse(kwargs["track"])
        self.assertEqual(0, kwargs["max_retry"])
        self.assertEqual("MiniApp手动", kwargs["source_module"])
        self.assertEqual("miniapp_manual_run", kwargs["chain_id"])

    async def test_manual_run_allows_cave_treasure_and_authorizes_before_send(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12351))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "authorize_cave_treasure_miniapp_manual_run", return_value=123456.0) as auth_mock, \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_manual_run(1001, "cave_treasure")

        self.assertTrue(ok)
        self.assertIn("手动执行", message)
        self.assertEqual(".洞府", extra["command"])
        auth_mock.assert_called_once_with(1001)
        kwargs = send_mock.await_args.kwargs
        self.assertFalse(kwargs["track"])
        self.assertEqual(0, kwargs["max_retry"])
        self.assertEqual("MiniApp手动", kwargs["source_module"])
        self.assertEqual("miniapp_manual_run", kwargs["chain_id"])

    async def test_manual_run_allows_tree_and_authorizes_with_score_config_before_send(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12353))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_score_config", return_value={
                    "jump": {"target_score_range": [126, 126]},
                    "fly": {"target_score_range": [36, 36]},
                }), \
                patch.object(ui, "authorize_tree_miniapp_manual_run", return_value=123456.0) as auth_mock, \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_manual_run(1001, "tree")

        self.assertTrue(ok)
        self.assertIn("手动执行", message)
        self.assertEqual(".灵树", extra["command"])
        auth_mock.assert_called_once_with(
            1001,
            mode="jump",
            score_profile={"target_score_range": [126, 126]},
            submit=True,
        )
        kwargs = send_mock.await_args.kwargs
        self.assertFalse(kwargs["track"])
        self.assertEqual(0, kwargs["max_retry"])
        self.assertEqual("MiniApp手动", kwargs["source_module"])
        self.assertEqual("miniapp_manual_run", kwargs["chain_id"])

    async def test_manual_run_allows_tree_fly_mode_and_uses_fly_score_config(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12354))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_score_config", return_value={
                    "jump": {"target_score_range": [126, 126]},
                    "fly": {"target_score_range": [36, 36]},
                }), \
                patch.object(ui, "authorize_tree_miniapp_manual_run", return_value=123456.0) as auth_mock, \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_manual_run(1001, "tree", payload={"mode": "fly"})

        self.assertTrue(ok)
        self.assertIn("手动执行", message)
        self.assertEqual(".灵树", extra["command"])
        auth_mock.assert_called_once_with(
            1001,
            mode="fly",
            score_profile={"target_score_range": [36, 36]},
            submit=True,
        )
        self.assertEqual("MiniApp手动", send_mock.await_args.kwargs["source_module"])

    async def test_manual_run_rejects_non_manual_game_key_before_send(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12349))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "send_game_command", new=send_mock):
            ok, message, extra = await ui.ui_send_miniapp_manual_run(1001, "fishing")

        self.assertFalse(ok)
        self.assertIn("仅允许", message)
        self.assertEqual({}, extra)
        send_mock.assert_not_awaited()

    async def test_trial_batch_run_uses_enabled_identities_and_background_task(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001, 1002, 1003]), \
                patch.object(ui, "get_identity_enabled", side_effect=lambda identity_id: identity_id != 1002), \
                patch.object(ui, "start_trial_miniapp_batch_run", return_value="batch1") as start_mock, \
                patch.object(ui, "_fire_and_forget", side_effect=lambda coro: coro.close()) as fire_mock, \
                patch.object(ui, "send_audit_log", new=AsyncMock()) as audit_mock:
            ok, message, extra = await ui.ui_start_trial_miniapp_batch_run()

        self.assertTrue(ok)
        self.assertIn("2 个身份", message)
        self.assertEqual({"batch_id": "batch1", "count": 2}, extra)
        start_mock.assert_called_once_with([1001, 1003])
        fire_mock.assert_called_once()
        audit_mock.assert_awaited_once()

    def test_miniapp_status_snapshot_is_safe_and_includes_cave_treasure(self):
        snapshot = ui.get_miniapp_status_snapshot()
        text = json.dumps(snapshot, ensure_ascii=False)
        adapters = {item["game_key"]: item for item in snapshot["adapters"]}
        probe_commands = {item["game_key"]: item["command"] for item in snapshot["entry_probe_commands"]}
        manual_run_commands = {item["game_key"]: item["command"] for item in snapshot["manual_run_commands"]}
        batch_run_commands = {item["game_key"]: item for item in snapshot["batch_run_commands"]}

        self.assertIn("cave_treasure", adapters)
        self.assertFalse(adapters["cave_treasure"]["default_enabled"])
        self.assertTrue(adapters["cave_treasure"]["manual_only"])
        self.assertEqual("miniapp", adapters["cave_treasure"]["ui_group"])
        self.assertEqual("sect", adapters["stargazer"]["ui_group"])
        self.assertEqual("sect", adapters["tree"]["ui_group"])
        self.assertEqual(".洞府", probe_commands["cave_treasure"])
        self.assertEqual(".钓鱼", probe_commands["fishing"])
        self.assertEqual(".灵树", probe_commands["tree"])
        self.assertEqual(".洞府", manual_run_commands["cave_treasure"])
        self.assertEqual(".观星台", manual_run_commands["stargazer"])
        self.assertEqual(".灵树", manual_run_commands["tree"])
        self.assertEqual(".天机试炼", manual_run_commands["trial"])
        self.assertEqual(".天机试炼", batch_run_commands["trial"]["command"])
        self.assertEqual("/api/miniapp-trial-batch-run", batch_run_commands["trial"]["endpoint"])
        self.assertIn("全号批量", batch_run_commands["trial"]["label"])
        self.assertNotIn("fishing", manual_run_commands)
        self.assertIn("cave_treasure", snapshot["flow_plans"])
        self.assertIn("tree", snapshot["flow_plans"])
        self.assertFalse(snapshot["flow_plans"]["cave_treasure"]["default_enabled"])
        self.assertFalse(snapshot["flow_plans"]["tree"]["default_enabled"])
        self.assertTrue(snapshot["flow_plans"]["cave_treasure"]["manual_only"])
        self.assertTrue(snapshot["flow_plans"]["tree"]["manual_only"])
        self.assertFalse(snapshot["policy"]["raw_init_data_persisted"])
        self.assertFalse(snapshot["policy"]["raw_start_token_persisted"])
        self.assertNotIn("tgWebAppData", text)
        self.assertNotIn("initData=", text)
        self.assertNotIn("hash=", text)
        self.assertNotIn("df_SECRET", text)


if __name__ == "__main__":
    unittest.main()
