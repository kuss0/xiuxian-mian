import asyncio
import unittest
import json
import copy
from datetime import datetime
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
        self.assertEqual({"cave_treasure", "stargazer", "trial"}, set(ui.MINIAPP_MANUAL_RUN_COMMANDS))
        self.assertNotIn("world_boss", ui.MINIAPP_ENTRY_PROBE_COMMANDS)
        self.assertNotIn("world_boss", ui.MINIAPP_MANUAL_RUN_COMMANDS)
        self.assertNotIn("fishing", ui.MINIAPP_MANUAL_RUN_COMMANDS)
        self.assertNotIn("tree", ui.MINIAPP_MANUAL_RUN_COMMANDS)

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
        self.assertIn("跳一跳 4-10", message)
        self.assertIn("飞一飞 14-20", message)
        self.assertEqual([4, 10], tree["jump"]["target_score_range"])
        self.assertEqual([14, 20], tree["fly"]["target_score_range"])
        self.assertEqual(4, tree["jump"]["min_target_score"])
        self.assertEqual(20, tree["fly"]["max_target_score"])
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
        self.assertEqual([14, 20], first["jump"]["target_score_range"])
        self.assertEqual([14, 20], first["fly"]["target_score_range"])
        self.assertEqual([14, 20], second["jump"]["target_score_range"])
        self.assertEqual([14, 20], second["fly"]["target_score_range"])
        self.assertEqual([8, 16], default["jump"]["target_score_range"])
        self.assertEqual([8, 18], default["fly"]["target_score_range"])

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

    async def test_phaseful_passive_trigger_is_fixed_maintenance_only_and_untracked(self):
        identity_id = 1001
        state_module.ensure_identity_registered(identity_id)
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("tianzun_maintenance")
        with state_module.use_identity(identity_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "summary_due"

        send_mock = AsyncMock(return_value=SimpleNamespace(id=12354))
        with patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_phaseful_passive_trigger(identity_id)

        self.assertTrue(ok)
        self.assertIn("普通触发文本", message)
        self.assertEqual("在", extra["text"])
        self.assertEqual(12354, extra["msg_id"])
        self.assertEqual(["元婴:summary_due"], extra["targets"])
        kwargs = send_mock.await_args.kwargs
        self.assertEqual("在", send_mock.await_args.args[0])
        self.assertFalse(kwargs["track"])
        self.assertEqual(0, kwargs["max_retry"])
        self.assertEqual("normal", kwargs["priority"])
        self.assertTrue(kwargs["allow_maintenance_pause"])
        self.assertEqual("被动结算触发", kwargs["source_module"])

    async def test_phaseful_passive_trigger_refuses_when_global_automation_is_running(self):
        identity_id = 1001
        state_module.ensure_identity_registered(identity_id)
        state_module.set_global_enabled(True)
        state_module.set_global_pause_source("")
        with state_module.use_identity(identity_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "summary_due"

        send_mock = AsyncMock(return_value=SimpleNamespace(id=12355))
        with patch.object(ui, "send_game_command", new=send_mock):
            ok, message, extra = await ui.ui_send_phaseful_passive_trigger(identity_id)

        self.assertFalse(ok)
        self.assertIn("天尊维护", message)
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

    async def test_manual_run_rejects_tree_before_send(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12353))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "send_game_command", new=send_mock):
            ok, message, extra = await ui.ui_send_miniapp_manual_run(1001, "tree", {"mode": "fly"})

        self.assertFalse(ok)
        self.assertIn("仅允许", message)
        self.assertEqual({}, extra)
        send_mock.assert_not_awaited()

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

    async def test_cave_public_entry_small_world_uses_http_runner_without_sending_command(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "run_cave_public_small_world_sync", new=AsyncMock(return_value={
                    "ok": True,
                    "message": "洞府小世界同步：信仰 92｜稳定 100｜江河决堤",
                    "extra": {"record_key": "1001:cave_small_world"},
                })) as run_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            ok, message, extra = await ui.ui_run_cave_public_entry(
                1001,
                "small_world",
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            )

        self.assertTrue(ok)
        self.assertIn("洞府小世界同步", message)
        self.assertEqual("1001:cave_small_world", extra["record_key"])
        run_mock.assert_awaited_once()
        send_mock.assert_not_awaited()

    async def test_cave_public_entry_deep_retreat_action_uses_http_runner_without_sending_command(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "run_cave_public_deep_retreat_action", new=AsyncMock(return_value={
                    "ok": True,
                    "message": "洞府闭关 status 完成：已同步｜阶段 running",
                    "extra": {"record_key": "1001:cave_deep_retreat"},
                })) as run_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            ok, message, extra = await ui.ui_run_cave_public_entry(
                1001,
                "deep_status",
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            )

        self.assertTrue(ok)
        self.assertIn("洞府闭关 status", message)
        self.assertEqual("1001:cave_deep_retreat", extra["record_key"])
        run_mock.assert_awaited_once_with(1001, "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999", "status")
        send_mock.assert_not_awaited()

    async def test_cave_public_entry_treasure_uses_http_runner_without_sending_command(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "run_cave_public_treasure", new=AsyncMock(return_value={
                    "ok": True,
                    "message": "洞府寻宝公共入口：MiniApp daily_limit｜游戏 3/3｜奖励:古禁印痕x1",
                    "extra": {"state_record_key": "1001:cave_treasure"},
                })) as run_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            ok, message, extra = await ui.ui_run_cave_public_entry(
                1001,
                "treasure",
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            )

        self.assertTrue(ok)
        self.assertIn("洞府寻宝公共入口", message)
        self.assertEqual("1001:cave_treasure", extra["state_record_key"])
        run_mock.assert_awaited_once_with(1001, "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999")
        send_mock.assert_not_awaited()

    async def test_cave_public_entry_trial_uses_http_runner_without_sending_command(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "run_cave_public_trial", new=AsyncMock(return_value={
                    "ok": True,
                    "message": "洞府天机试炼公共入口：MiniApp daily_limit｜2次｜收益:天机残痕+2",
                    "extra": {"trial_title": "天机试炼"},
                })) as run_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            ok, message, extra = await ui.ui_run_cave_public_entry(
                1001,
                "trial",
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            )

        self.assertTrue(ok)
        self.assertIn("洞府天机试炼公共入口", message)
        self.assertEqual("天机试炼", extra["trial_title"])
        run_mock.assert_awaited_once_with(1001, "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999")
        send_mock.assert_not_awaited()

    async def test_cave_public_entry_yuanying_uses_http_runner_without_sending_command(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "run_cave_public_yuanying", new=AsyncMock(return_value={
                    "ok": True,
                    "message": "洞府天机阁元婴出窍：元婴已出窍。",
                    "extra": {"sync": {"phase": "running"}},
                })) as run_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            ok, message, extra = await ui.ui_run_cave_public_entry(
                1001,
                "yuanying",
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            )

        self.assertTrue(ok)
        self.assertIn("洞府天机阁元婴出窍", message)
        self.assertEqual("running", extra["sync"]["phase"])
        run_mock.assert_awaited_once_with(1001, "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999")
        send_mock.assert_not_awaited()

    async def test_cave_public_config_is_independent_from_legacy_module_switches(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "cave_public_small_world_enabled": False,
            "cave_public_deep_status_enabled": False,
            "cave_public_treasure_enabled": False,
            "cave_public_trial_enabled": False,
        }

        with patch.object(ui, "save_state", return_value=True) as save_mock:
            ok, message = await ui.ui_set_cave_public_config({
                "small_world_enabled": True,
                "deep_status_enabled": False,
                "treasure_enabled": True,
                "trial_enabled": False,
                "delay_sec": 7,
            })

        automation = ui.get_miniapp_status_snapshot()["automation"]
        self.assertTrue(ok)
        self.assertIn("small_world", message)
        self.assertIn("treasure", message)
        self.assertTrue(automation["cave_public_small_world_enabled"])
        self.assertFalse(automation["cave_public_deep_status_enabled"])
        self.assertTrue(automation["cave_public_treasure_enabled"])
        self.assertFalse(automation["cave_public_trial_enabled"])
        self.assertEqual(10, automation["cave_public_delay_sec"])
        self.assertNotIn("small_world_enabled", state_module.get_miniapp_auto_config())
        self.assertNotIn("deep_retreat_enabled", state_module.get_miniapp_auto_config())
        save_mock.assert_called_once()

    async def test_world_boss_miniapp_config_is_default_off_and_clamped(self):
        state_module._meta_state["miniapp_auto_config"] = {}

        initial = ui.get_miniapp_status_snapshot()["automation"]
        self.assertFalse(initial["world_boss_auto_enabled"])
        self.assertEqual(1, initial["world_boss_auto_account_limit"])

        with patch.object(ui, "save_state", return_value=True) as save_mock:
            ok, message = await ui.ui_set_world_boss_miniapp_config({
                "enabled": True,
                "account_limit": 9,
                "account_gap_sec": 0,
            })

        automation = ui.get_miniapp_status_snapshot()["automation"]
        self.assertTrue(ok)
        self.assertIn("最多 4 个登录账户", message)
        self.assertTrue(automation["world_boss_auto_enabled"])
        self.assertEqual(4, automation["world_boss_auto_account_limit"])
        self.assertEqual(1, automation["world_boss_auto_account_gap_sec"])
        save_mock.assert_called_once()

    async def test_cave_public_batch_claims_slot_before_background_task(self):
        batch_snapshot = dict(ui._cave_public_batch_state)
        ui._cave_public_batch_state.clear()
        ui._cave_public_batch_state.update(batch_snapshot)
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_small_world_enabled": True,
            "cave_public_deep_status_enabled": False,
            "cave_public_treasure_enabled": False,
            "cave_public_trial_enabled": False,
        }
        scheduled = []

        def close_scheduled(coro):
            scheduled.append(coro)
            coro.close()

        try:
            with patch.object(ui, "get_identity_ids", return_value=[1001, 1002]), \
                    patch.object(ui, "get_identity_enabled", return_value=True), \
                    patch.object(ui, "_fire_and_forget", side_effect=close_scheduled) as fire_mock:
                ok, _message, extra = await ui.ui_start_cave_public_entry_batch({
                    "public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                })
                second_ok, second_message, _second_extra = await ui.ui_start_cave_public_entry_batch({
                    "public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                })

            self.assertTrue(ok)
            self.assertEqual(["small_world"], extra["actions"])
            self.assertEqual(2, extra["count"])
            self.assertTrue(ui._cave_public_batch_state["running"])
            self.assertEqual("等待启动", ui._cave_public_batch_state["current"])
            self.assertNotIn("df_SECRET", json.dumps(ui._cave_public_batch_state, ensure_ascii=False))
            self.assertFalse(second_ok)
            self.assertIn("正在运行", second_message)
            fire_mock.assert_called_once()
            self.assertEqual(1, len(scheduled))
        finally:
            ui._cave_public_batch_state.clear()
            ui._cave_public_batch_state.update(batch_snapshot)

    def test_cave_public_batch_deduplicates_twenty_four_send_as_identities_to_four_login_accounts(self):
        account_ids = [1001, 1002, 1003, 1004]
        identity_ids = []
        account_by_identity = {}
        for account_id in account_ids:
            identity_ids.append(account_id)
            account_by_identity[account_id] = account_id
            for offset in range(1, 6):
                alias_id = account_id * 100 + offset
                identity_ids.append(alias_id)
                account_by_identity[alias_id] = account_id

        self.assertEqual(24, len(identity_ids))
        with patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "get_identity_account", side_effect=lambda identity_id: account_by_identity[identity_id]):
            selected = ui._cave_public_batch_identity_ids_for_action("small_world", identity_ids)
            trial_selected = ui._cave_public_batch_identity_ids_for_action("trial", identity_ids)
            steps = ui._build_cave_public_batch_steps(identity_ids, ["small_world"])

        self.assertEqual(account_ids, selected)
        self.assertEqual(identity_ids, trial_selected)
        self.assertEqual([(account_id, "small_world") for account_id in account_ids], steps)

    def test_cave_public_batch_skips_aliases_when_login_account_identity_is_disabled(self):
        identity_ids = [1001, 100101, 100102, 1002, 100201, 100202]
        account_by_identity = {
            1001: 1001,
            100101: 1001,
            100102: 1001,
            1002: 1002,
            100201: 1002,
            100202: 1002,
        }

        with patch.object(ui, "get_identity_enabled", side_effect=lambda identity_id: identity_id != 1002), \
                patch.object(ui, "get_identity_account", side_effect=lambda identity_id: account_by_identity[identity_id]):
            selected = ui._cave_public_batch_identity_ids_for_action("small_world", identity_ids)

        self.assertEqual([1001], selected)

    async def test_cave_public_batch_runs_actions_strictly_serially(self):
        batch_snapshot = dict(ui._cave_public_batch_state)
        active = 0
        max_active = 0
        calls = []

        async def run_entry(identity_id, action, _url):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            calls.append((identity_id, action))
            await asyncio.sleep(0)
            active -= 1
            return True, f"{action} 完成", {}

        try:
            with patch.object(ui, "get_identity_enabled", return_value=True), \
                    patch.object(ui, "get_identity_display_name", side_effect=lambda identity_id: f"角色{identity_id}"), \
                    patch.object(ui, "ui_run_cave_public_entry", new=run_entry), \
                    patch.object(ui, "send_audit_log", new=AsyncMock()):
                await ui._run_cave_public_entry_batch(
                    "cave_public_test",
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    [1001, 1002],
                    ["small_world", "trial"],
                    0,
                )

            self.assertEqual(1, max_active)
            self.assertEqual(
                [(1001, "small_world"), (1002, "small_world"), (1001, "trial"), (1002, "trial")],
                calls,
            )
            self.assertFalse(ui._cave_public_batch_state["running"])
            self.assertEqual(4, ui._cave_public_batch_state["completed"])
            self.assertEqual(4, ui._cave_public_batch_state["succeeded"])
            self.assertEqual(0, ui._cave_public_batch_state["failed"])
        finally:
            ui._cave_public_batch_state.clear()
            ui._cave_public_batch_state.update(batch_snapshot)

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

    async def test_miniapp_daily_scheduler_starts_first_wave_inside_window(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            "cave_public_small_world_enabled": False,
            "cave_public_deep_status_enabled": False,
            "cave_public_treasure_enabled": False,
            "cave_public_trial_enabled": True,
            "cave_public_stargazer_enabled": False,
            "cave_public_yuanying_enabled": False,
        }
        now = datetime(2026, 7, 7, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "_normalize_trial_batch_identity_ids", return_value=[1001, 1002, 1003, 1004]) as ids_mock, \
                patch.object(ui, "ui_start_cave_public_entry_batch", new=AsyncMock(return_value=(True, "ok", {"batch_id": "batch-auto"}))) as start_mock, \
                patch.object(ui, "save_state", return_value=True) as save_mock:
            result = await ui.run_miniapp_daily_scheduler(now)
            second = await ui.run_miniapp_daily_scheduler(now + 60)

        self.assertTrue(result["started"])
        self.assertEqual("batch-auto", result["batch_id"])
        self.assertEqual(2, result["count"])
        self.assertEqual("wave1", result["wave"])
        self.assertEqual({"started": False, "reason": "wave1_done_today"}, second)
        ids_mock.assert_called_once_with({})
        self.assertEqual([1001, 1002], start_mock.await_args.args[0]["send_as_ids"])
        self.assertEqual(["trial"], start_mock.await_args.args[0]["actions"])
        save_mock.assert_called_once()
        snapshot = ui.get_miniapp_status_snapshot()["automation"]
        self.assertFalse(snapshot["trial_daily_done_today"])
        self.assertEqual("batch-auto", snapshot["trial_daily_last_batch_id"])
        self.assertEqual("batch-auto", snapshot["trial_daily_wave1_last_batch_id"])

    async def test_miniapp_daily_scheduler_starts_second_wave_inside_window(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            "cave_public_small_world_enabled": False,
            "cave_public_treasure_enabled": False,
            "cave_public_trial_enabled": True,
        }
        now = datetime(2026, 7, 7, 5, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "_normalize_trial_batch_identity_ids", return_value=[1001, 1002, 1003, 1004]) as ids_mock, \
                patch.object(ui, "ui_start_cave_public_entry_batch", new=AsyncMock(return_value=(True, "ok", {"batch_id": "batch-wave2"}))) as start_mock, \
                patch.object(ui, "save_state", return_value=True):
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertTrue(result["started"])
        self.assertEqual("wave2", result["wave"])
        self.assertEqual(2, result["count"])
        ids_mock.assert_called_once_with({})
        self.assertEqual([1003, 1004], start_mock.await_args.args[0]["send_as_ids"])

    async def test_miniapp_daily_scheduler_waits_when_global_disabled(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
        }
        now = datetime(2026, 7, 7, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "get_global_enabled", return_value=False), \
                patch.object(ui, "_normalize_trial_batch_identity_ids") as ids_mock, \
                patch.object(ui, "start_trial_miniapp_batch_run") as start_mock, \
                patch.object(ui, "save_state") as save_mock:
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertEqual({"started": False, "reason": "global_disabled"}, result)
        ids_mock.assert_not_called()
        start_mock.assert_not_called()
        save_mock.assert_not_called()
        snapshot = ui.get_miniapp_status_snapshot()["automation"]
        self.assertFalse(snapshot["trial_daily_done_today"])
        self.assertFalse(snapshot["trial_daily_waves"][0]["done_today"])

    async def test_miniapp_daily_scheduler_allows_public_trial_during_maintenance_pause(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            "cave_public_small_world_enabled": False,
            "cave_public_treasure_enabled": False,
            "cave_public_trial_enabled": True,
        }
        now = datetime(2026, 7, 7, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "get_global_enabled", return_value=False), \
                patch.object(ui, "get_global_pause_source", return_value="tianzun_maintenance"), \
                patch.object(ui, "_normalize_trial_batch_identity_ids", return_value=[1001, 1002]), \
                patch.object(ui, "ui_start_cave_public_entry_batch", new=AsyncMock(return_value=(True, "ok", {"batch_id": "maintenance-batch"}))) as start_mock, \
                patch.object(ui, "save_state", return_value=True):
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertTrue(result["started"])
        self.assertEqual("maintenance-batch", result["batch_id"])
        start_mock.assert_awaited_once()

    async def test_miniapp_daily_scheduler_legacy_done_counts_as_both_waves(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
            "trial_daily_last_run_day": "2026-07-07",
            "trial_daily_last_batch_id": "old-batch",
            "trial_daily_last_run_at": 1,
            "trial_daily_last_result": "旧批次",
        }
        now = datetime(2026, 7, 7, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "_normalize_trial_batch_identity_ids") as ids_mock, \
                patch.object(ui, "start_trial_miniapp_batch_run") as start_mock:
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertEqual({"started": False, "reason": "done_today"}, result)
        ids_mock.assert_not_called()
        start_mock.assert_not_called()
        snapshot = ui.get_miniapp_auto_config_snapshot(datetime(2026, 7, 7, 5, 30, tzinfo=ui.TZ_LOCAL).timestamp())
        self.assertTrue(snapshot["trial_daily_done_today"])
        self.assertTrue(snapshot["trial_daily_waves"][0]["done_today"])
        self.assertTrue(snapshot["trial_daily_waves"][1]["done_today"])

    async def test_miniapp_daily_scheduler_requires_public_entry_url(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
        }
        now = datetime(2026, 7, 7, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "_normalize_trial_batch_identity_ids") as ids_mock, \
                patch.object(ui, "start_trial_miniapp_batch_run") as start_mock:
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertEqual({"started": False, "reason": "public_entry_url_missing"}, result)
        ids_mock.assert_not_called()
        start_mock.assert_not_called()
        snapshot = ui.get_miniapp_status_snapshot()["automation"]
        self.assertTrue(snapshot["trial_daily_enabled"])
        self.assertTrue(snapshot["trial_daily_scheduler_confirmed"])
        self.assertTrue(snapshot["trial_daily_effective_enabled"])

    async def test_miniapp_daily_scheduler_defaults_disabled_on_empty_config(self):
        state_module._meta_state["miniapp_auto_config"] = {}
        now = datetime(2026, 7, 7, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "_normalize_trial_batch_identity_ids") as ids_mock, \
                patch.object(ui, "start_trial_miniapp_batch_run") as start_mock:
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertEqual({"started": False, "reason": "disabled"}, result)
        ids_mock.assert_not_called()
        start_mock.assert_not_called()

    async def test_miniapp_daily_scheduler_does_not_start_outside_window(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
        }
        now = datetime(2026, 7, 7, 0, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "_normalize_trial_batch_identity_ids") as ids_mock, \
                patch.object(ui, "start_trial_miniapp_batch_run") as start_mock:
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertEqual({"started": False, "reason": "outside_window"}, result)
        ids_mock.assert_not_called()
        start_mock.assert_not_called()

    def test_miniapp_status_snapshot_is_safe_and_includes_cave_treasure(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
        }
        snapshot = ui.get_miniapp_status_snapshot()
        text = json.dumps(snapshot, ensure_ascii=False)
        adapters = {item["game_key"]: item for item in snapshot["adapters"]}
        probe_commands = {item["game_key"]: item["command"] for item in snapshot["entry_probe_commands"]}
        manual_run_commands = {item["game_key"]: item["command"] for item in snapshot["manual_run_commands"]}
        self.assertTrue(snapshot["automation"]["cave_public_entry_url_configured"])
        self.assertNotIn("df_SECRET999", text)
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
        self.assertEqual(".天机试炼", manual_run_commands["trial"])
        self.assertEqual(".天机试炼", batch_run_commands["trial"]["command"])
        self.assertEqual("/api/miniapp-trial-batch-run", batch_run_commands["trial"]["endpoint"])
        self.assertIn("全号批量", batch_run_commands["trial"]["label"])
        self.assertNotIn("fishing", manual_run_commands)
        self.assertNotIn("tree", manual_run_commands)
        self.assertIn("cave_treasure", snapshot["flow_plans"])
        self.assertIn("tree", snapshot["flow_plans"])
        self.assertFalse(snapshot["flow_plans"]["cave_treasure"]["default_enabled"])
        self.assertFalse(snapshot["flow_plans"]["tree"]["default_enabled"])
        self.assertTrue(snapshot["flow_plans"]["cave_treasure"]["manual_only"])
        self.assertTrue(snapshot["flow_plans"]["tree"]["manual_only"])
        self.assertEqual([".洞府"], snapshot["flow_plans"]["cave_treasure"]["replaces_commands"])
        self.assertEqual([".灵树"], snapshot["flow_plans"]["tree"]["replaces_commands"])
        self.assertEqual("single_identity_command_replacement", snapshot["flow_plans"]["cave_treasure"]["read_scope"])
        self.assertEqual(["module_snapshot", "daily_counter", "inventory_delta"], snapshot["flow_plans"]["cave_treasure"]["state_outputs"])
        self.assertIn("state_records", snapshot)
        self.assertEqual(0, snapshot["state_records"]["record_count"])
        self.assertFalse(snapshot["policy"]["raw_init_data_persisted"])
        self.assertFalse(snapshot["policy"]["raw_start_token_persisted"])
        self.assertFalse(snapshot["automation"]["trial_daily_enabled"])
        self.assertFalse(snapshot["automation"]["trial_daily_effective_enabled"])
        self.assertTrue(snapshot["automation"]["cave_public_small_world_enabled"])
        self.assertTrue(snapshot["automation"]["cave_public_deep_status_enabled"])
        self.assertTrue(snapshot["automation"]["cave_public_treasure_enabled"])
        self.assertTrue(snapshot["automation"]["cave_public_trial_enabled"])
        self.assertEqual(20, snapshot["automation"]["cave_public_delay_sec"])
        self.assertIn("cave_public_batch", snapshot)
        self.assertEqual("01:00-04:00 / 05:00-08:00", snapshot["automation"]["trial_daily_window_text"])
        self.assertNotIn("tgWebAppData", text)
        self.assertNotIn("initData=", text)
        self.assertNotIn("hash=", text)
        self.assertNotIn("df_SECRET", text)

    def test_cave_public_deep_status_is_due_only_for_enabled_due_identity(self):
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(1001)
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["next_deep_retreat_time"] = now - 1
            self.assertTrue(ui._cave_public_background_action_due("deep_status", 1001, now))
            state_module.state["next_deep_retreat_time"] = now + 3600
            self.assertFalse(ui._cave_public_background_action_due("deep_status", 1001, now))
            state_module.state["deep_retreat_enabled"] = False
            state_module.state["next_deep_retreat_time"] = now - 1
            self.assertFalse(ui._cave_public_background_action_due("deep_status", 1001, now))

    def test_cave_public_background_prioritizes_oldest_phaseful_actions(self):
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(1001)
        state_module.ensure_identity_registered(1002)
        with state_module.use_identity(1001):
            state_module.state["next_yuanying_time"] = now - 60
            state_module.state["next_deep_retreat_time"] = now - 600
        with state_module.use_identity(1002):
            state_module.state["next_yuanying_time"] = now - 300

        candidates = [(1001, "treasure"), (1001, "deep_status"), (1001, "yuanying"), (1002, "yuanying")]
        candidates.sort(key=lambda item: ui._cave_public_background_candidate_sort_key(item[1], item[0], now))

        self.assertEqual((1002, "yuanying"), candidates[0])
        self.assertEqual((1001, "yuanying"), candidates[1])
        self.assertEqual((1001, "deep_status"), candidates[2])
        self.assertEqual((1001, "treasure"), candidates[3])

    def test_cave_public_background_selects_deep_action_from_phase(self):
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(1001)
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_phase"] = "waiting_summary"
        self.assertEqual("deep_settle", ui._cave_public_background_deep_action(1001, now))
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
        self.assertEqual("deep_start", ui._cave_public_background_deep_action(1001, now))
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_phase"] = "launching"
        self.assertEqual("deep_status", ui._cave_public_background_deep_action(1001, now))


if __name__ == "__main__":
    unittest.main()
