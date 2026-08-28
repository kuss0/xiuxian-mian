import asyncio
import unittest
import json
import copy
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import ui
from model import state as state_module
from model.features import cave_treasure_runtime


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
        self.assertIn("跳一跳 4-10", message)
        self.assertIn("飞一飞 16-20", message)
        self.assertEqual([4, 10], tree["jump"]["target_score_range"])
        self.assertEqual([16, 20], tree["fly"]["target_score_range"])
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
        self.assertEqual([25, 31], first["jump"]["target_score_range"])
        self.assertEqual([16, 20], first["fly"]["target_score_range"])
        self.assertEqual([41, 47], second["jump"]["target_score_range"])
        self.assertEqual([16, 20], second["fly"]["target_score_range"])
        self.assertEqual([72, 78], default["jump"]["target_score_range"])
        self.assertEqual([8, 12], default["fly"]["target_score_range"])

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

    async def test_tree_daily_auto_config_is_scoped_and_requires_luoyun(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001, 1002]), \
                patch.object(ui, "check_tree_miniapp_eligibility", side_effect=lambda identity_id, enabled=None: (identity_id == 1001, "宗门不匹配")), \
                patch.object(ui, "save_state", return_value=True) as save_mock:
            ok, message = await ui.ui_set_tree_miniapp_auto_config(1001, {"enabled": True})
            rejected, rejected_message = await ui.ui_set_tree_miniapp_auto_config(1002, {"enabled": True})

        self.assertTrue(ok)
        self.assertIn("已开启", message)
        self.assertFalse(rejected)
        self.assertIn("宗门不匹配", rejected_message)
        self.assertEqual([1001], ui.normalize_miniapp_auto_config()["tree_daily_enabled_identity_ids"])
        save_mock.assert_called_once()

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

    async def test_probe_allows_cave_treasure_command_entry(self):
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
        send_mock.assert_awaited_once()

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

    async def test_manual_run_allows_cave_treasure_command_entry(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12351))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "authorize_cave_treasure_miniapp_manual_run", return_value=123456.0) as auth_mock, \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_manual_run(1001, "cave_treasure")

        self.assertTrue(ok)
        self.assertIn("等待入口", message)
        self.assertEqual(".洞府", extra["command"])
        self.assertEqual(12351, extra["msg_id"])
        auth_mock.assert_called_once_with(1001)
        send_mock.assert_awaited_once()

    async def test_manual_run_allows_tree_and_authorizes_before_send(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=12353))
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_score_config", return_value={"fly": {"target_score_range": (8, 18)}}), \
                patch.object(ui, "authorize_tree_miniapp_manual_run", return_value=123456.0) as auth_mock, \
                patch.object(ui, "send_game_command", new=send_mock), \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message, extra = await ui.ui_send_miniapp_manual_run(1001, "tree", {"mode": "fly"})

        self.assertTrue(ok)
        self.assertIn("等待入口", message)
        self.assertEqual("tree", extra["game_key"])
        self.assertEqual(".灵树", extra["command"])
        auth_mock.assert_called_once_with(
            1001,
            mode="fly",
            score_profile={"target_score_range": (8, 18)},
            submit=True,
        )
        send_mock.assert_awaited_once()

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

    async def test_cave_public_entry_promotes_nested_retry_after_to_extra(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "run_cave_public_trial", new=AsyncMock(return_value={
                    "ok": False,
                    "message": "洞府天机试炼限流",
                    "extra": {
                        "result": {
                            "events": [{"step": "start", "retry_after_sec": 3600}],
                        },
                    },
                })) as run_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            ok, message, extra = await ui.ui_run_cave_public_entry(
                1001,
                "trial",
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            )

        self.assertFalse(ok)
        self.assertIn("限流", message)
        self.assertEqual(3600, extra["retry_after_sec"])
        run_mock.assert_awaited_once()
        send_mock.assert_not_awaited()

    async def test_cave_public_entry_fate_cards_uses_persisted_explicit_choice_without_sending_command(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_fate_cards_enabled": False,
            "cave_public_fate_cards_choice_key": "hide",
        }
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "run_cave_public_fate_cards", new=AsyncMock(return_value={
                    "ok": True,
                    "message": "天机命脉已承命，等待服务端完成条件",
                    "extra": {"retry_after_sec": 1800},
                })) as run_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            ok, message, extra = await ui.ui_run_cave_public_entry(
                1001,
                "fate_cards",
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            )

        self.assertTrue(ok)
        self.assertIn("天机命脉", message)
        self.assertEqual(1800, extra["retry_after_sec"])
        run_mock.assert_awaited_once_with(
            1001,
            "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            choice_key="hide",
        )
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
            "cave_public_fate_cards_enabled": False,
            "cave_public_fate_cards_choice_key": "accept",
            "cave_public_fishing_enabled": False,
            "cave_public_fishing_identity_ids": [],
            "cave_public_tianti_status_enabled": False,
            "cave_public_tianti_status_identity_ids": [],
        }

        with patch.object(ui, "save_state", return_value=True) as save_mock:
            ok, message = await ui.ui_set_cave_public_config({
                "small_world_enabled": True,
                "deep_status_enabled": False,
                "treasure_enabled": True,
                "trial_enabled": False,
                "fate_cards_enabled": True,
                "fate_cards_choice_key": "hide",
                "fishing_enabled": True,
                "fishing_identity_ids": [3820064579, "3765328695", "bad"],
                "tianti_status_enabled": True,
                "tianti_status_identity_ids": [1002, "1001", "bad"],
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
        self.assertTrue(automation["cave_public_fate_cards_enabled"])
        self.assertEqual("hide", automation["cave_public_fate_cards_choice_key"])
        self.assertTrue(automation["cave_public_fishing_enabled"])
        self.assertEqual([3765328695, 3820064579], automation["cave_public_fishing_identity_ids"])
        self.assertTrue(automation["cave_public_tianti_status_enabled"])
        self.assertEqual([1001, 1002], automation["cave_public_tianti_status_identity_ids"])
        self.assertEqual(10, automation["cave_public_delay_sec"])
        self.assertNotIn("small_world_enabled", state_module.get_miniapp_auto_config())
        self.assertNotIn("deep_retreat_enabled", state_module.get_miniapp_auto_config())
        save_mock.assert_called_once()

    def test_tianti_public_candidate_includes_lingxiao_with_climb_disabled(self):
        identity_id = 1001
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, sect_name="凌霄宫")
        with state_module.use_identity(identity_id):
            state_module.state["tianti_enabled"] = False
        with patch.object(ui, "get_identity_ids", return_value=[identity_id]), \
                patch.object(ui, "is_cave_public_identity_available", return_value=True):
            candidates = ui.get_miniapp_status_snapshot()["automation"]["cave_public_tianti_status_candidates"]

        self.assertEqual(1, len(candidates))
        self.assertEqual(identity_id, candidates[0]["identity_id"])
        self.assertFalse(candidates[0]["climb_enabled"])

    async def test_cave_public_config_rejects_unsupported_fate_choice_before_save(self):
        with patch.object(ui, "save_state", return_value=True) as save_mock:
            ok, message = await ui.ui_set_cave_public_config({
                "fate_cards_enabled": True,
                "fate_cards_choice_key": "defy",
            })

        self.assertFalse(ok)
        self.assertIn("accept/hide", message)
        save_mock.assert_not_called()

    async def test_cave_public_config_accepts_multiple_public_entry_urls_safely(self):
        with patch.object(ui, "save_state", return_value=True):
            ok, message = await ui.ui_set_cave_public_config({
                "public_entry_urls": (
                    "https://t.me/hantianzun21_bot?startapp=df_SECRET111\n"
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222\n"
                    "https://t.me/hantianzun21_bot?startapp=df_SECRET111"
                ),
            })

        config = ui.normalize_miniapp_auto_config()
        snapshot = ui.get_miniapp_status_snapshot()
        text = json.dumps(snapshot, ensure_ascii=False)
        self.assertTrue(ok)
        self.assertIn("已保存", message)
        self.assertEqual(2, len(config["cave_public_entry_urls"]))
        self.assertEqual(config["cave_public_entry_urls"][0], config["cave_public_entry_url"])
        self.assertTrue(snapshot["automation"]["cave_public_entry_url_configured"])
        self.assertEqual(2, snapshot["automation"]["cave_public_entry_url_count"])
        self.assertNotIn("df_SECRET111", text)
        self.assertNotIn("df_SECRET222", text)

    async def test_cave_public_entry_run_falls_back_only_for_entry_health_failure(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "run_cave_public_trial", new=AsyncMock(side_effect=[
                    {"ok": False, "message": "洞府天机试炼入口读取失败：会话初始化失败", "extra": {}},
                    {"ok": True, "message": "洞府天机试炼公共入口：完成", "extra": {}},
                ])) as run_mock:
            ok, message, extra = await ui.ui_run_cave_public_entry(
                1001,
                "trial",
                "https://t.me/hantianzun21_bot?startapp=df_SECRET111\n"
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222",
            )

        self.assertTrue(ok)
        self.assertIn("完成", message)
        self.assertEqual(1, extra["entry_index"])
        self.assertEqual(2, len(extra["entry_attempts"]))
        self.assertEqual(2, run_mock.await_count)

    async def test_cave_public_entry_token_expiry_falls_back_to_a_fresh_candidate(self):
        background_snapshot = dict(ui._cave_public_background_state)
        try:
            ui._close_cave_public_upstream_circuit()
            with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                    patch.object(ui, "get_identity_enabled", return_value=True), \
                    patch.object(ui, "run_cave_public_trial", new=AsyncMock(side_effect=[
                        {"ok": False, "message": "洞府天机试炼身份读取失败：dwelling_token_expired", "extra": {}},
                        {"ok": True, "message": "洞府天机试炼公共入口：完成", "extra": {}},
                    ])) as run_mock:
                ok, message, extra = await ui.ui_run_cave_public_entry(
                    1001,
                    "trial",
                    "https://t.me/hantianzun21_bot?startapp=df_SECRET111\n"
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222",
                )

            self.assertTrue(ok)
            self.assertIn("完成", message)
            self.assertEqual(1, extra["entry_index"])
            self.assertEqual(2, run_mock.await_count)
            self.assertEqual(0, ui._cave_public_background_state["circuit_open_until"])
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

    async def test_cave_public_entry_all_token_expiry_opens_entry_circuit(self):
        background_snapshot = dict(ui._cave_public_background_state)
        try:
            ui._close_cave_public_upstream_circuit()
            with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                    patch.object(ui, "get_identity_enabled", return_value=True), \
                    patch.object(ui, "run_cave_public_trial", new=AsyncMock(return_value={
                        "ok": False,
                        "message": "洞府天机试炼身份读取失败：dwelling_token_expired",
                        "extra": {},
                    })) as run_mock:
                ok, message, extra = await ui.ui_run_cave_public_entry(
                    1001,
                    "trial",
                    "https://t.me/hantianzun21_bot?startapp=df_SECRET111\n"
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222",
                )

            self.assertFalse(ok)
            self.assertIn("入口授权已过期", message)
            self.assertIn("更新最新洞府公共入口 URL", message)
            self.assertEqual(1, extra["entry_index"])
            self.assertEqual(2, len(extra["entry_attempts"]))
            self.assertEqual(2, run_mock.await_count)
            self.assertGreater(
                ui._cave_public_background_state["circuit_open_until"],
                time.time() + ui.CAVE_PUBLIC_UPSTREAM_CIRCUIT_SEC,
            )
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

    async def test_cave_public_entry_token_expiry_blocks_same_config_until_url_changes(self):
        old_urls = [
            "https://t.me/hantianzun21_bot?startapp=df_SECRET111",
            "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222",
        ]
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_url": old_urls[0],
            "cave_public_entry_urls": old_urls,
        }
        background_snapshot = dict(ui._cave_public_background_state)
        try:
            ui._close_cave_public_upstream_circuit()
            run_mock = AsyncMock(side_effect=[
                {
                    "ok": False,
                    "message": "洞府天机试炼身份读取失败：dwelling_token_expired",
                    "extra": {},
                },
                {
                    "ok": True,
                    "message": "洞府小世界请求仍在最小间隔内，已跳过请求",
                    "extra": {"skipped": True},
                },
            ])
            with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                    patch.object(ui, "get_identity_enabled", return_value=True), \
                    patch.object(ui, "run_cave_public_trial", new=run_mock), \
                    patch.object(cave_treasure_runtime, "save_state", return_value=True) as save_mock:
                ok, message, _extra = await ui.ui_run_cave_public_entry(1001, "trial", "")
                blocked_ok, blocked_message, blocked_extra = await ui.ui_run_cave_public_entry(
                    1001,
                    "trial",
                    "",
                )

            self.assertFalse(ok)
            self.assertIn("已暂停重复请求", message)
            self.assertFalse(blocked_ok)
            self.assertIn("已暂停重复请求", blocked_message)
            self.assertTrue(blocked_extra["entry_token_blocked"])
            self.assertEqual(2, run_mock.await_count)
            self.assertTrue(ui.normalize_miniapp_auto_config()["cave_public_entry_token_blocked_signature"])
            save_mock.assert_called_once()

            new_url = "https://t.me/hantianzun99_bot?startapp=df_FRESH333"
            with patch.object(ui, "save_state", return_value=True):
                changed, _message = await ui.ui_set_cave_public_config({
                    "public_entry_urls": [new_url],
                })
            self.assertTrue(changed)
            config = ui.normalize_miniapp_auto_config()
            self.assertEqual([new_url], config["cave_public_entry_urls"])
            self.assertEqual("", config["cave_public_entry_token_blocked_signature"])
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

    async def test_miniapp_scheduler_skips_persistently_expired_entry_urls(self):
        urls = ["https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222"]
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_url": urls[0],
            "cave_public_entry_urls": urls,
            "cave_public_entry_token_blocked_signature": ui._cave_public_entry_urls_signature(urls),
            "cave_public_entry_token_blocked_at": time.time(),
            "cave_public_entry_token_blocked_reason": "dwelling_token_expired",
        }
        with patch.object(ui, "get_global_enabled", return_value=True), \
                patch.object(ui, "_run_tree_miniapp_daily_scheduler", new=AsyncMock()) as tree_mock, \
                patch.object(ui, "_run_cave_public_background_scheduler", new=AsyncMock()) as cave_mock:
            result = await ui.run_miniapp_daily_scheduler(time.time())

        self.assertFalse(result["started"])
        self.assertEqual("entry_token_blocked", result["reason"])
        tree_mock.assert_not_awaited()
        cave_mock.assert_not_awaited()

    async def test_miniapp_scheduler_canary_uses_only_first_identity_and_url(self):
        now = 1_700_000_000.0
        urls = [
            "https://t.me/hantianzun21_bot?startapp=df_SECRET111",
            "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222",
        ]
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_url": urls[0],
            "cave_public_entry_urls": urls,
            "cave_public_entry_token_blocked_signature": ui._cave_public_entry_urls_signature(urls),
            "cave_public_entry_token_blocked_at": now - 7200,
            "cave_public_entry_token_retry_at": now - 1,
            "cave_public_entry_token_blocked_reason": "dwelling_token_expired",
        }
        state_module.ensure_identity_registered(1001)
        state_module.ensure_identity_registered(1002)
        with patch.object(ui, "get_global_enabled", return_value=True), \
                patch.object(ui, "get_identity_ids", return_value=[1001, 1002]), \
                patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                patch.object(cave_treasure_runtime, "save_state"), \
                patch.object(ui, "probe_cave_public_entry", new=AsyncMock(return_value={
                    "ok": False,
                    "message": "dwelling_token_expired",
                })) as probe_mock, \
                patch.object(ui, "_run_tree_miniapp_daily_scheduler", new=AsyncMock()) as tree_mock:
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertTrue(result["started"])
        self.assertEqual("entry_canary", result["kind"])
        probe_mock.assert_awaited_once_with(1001, urls[0], now=now)
        tree_mock.assert_not_awaited()

    async def test_cave_public_entry_run_opens_circuit_without_retrying_upstream_502(self):
        background_snapshot = dict(ui._cave_public_background_state)
        try:
            ui._close_cave_public_upstream_circuit()
            with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                    patch.object(ui, "get_identity_enabled", return_value=True), \
                    patch.object(ui, "run_cave_public_trial", new=AsyncMock(return_value={
                        "ok": False,
                        "message": "洞府天机试炼身份读取失败：HTTP 502 returned non JSON",
                        "extra": {},
                    })) as run_mock:
                ok, message, extra = await ui.ui_run_cave_public_entry(
                    1001,
                    "trial",
                    "https://t.me/hantianzun21_bot?startapp=df_SECRET111\n"
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222",
                )

            self.assertFalse(ok)
            self.assertIn("HTTP 502", message)
            self.assertEqual(0, extra["entry_index"])
            self.assertEqual(1, len(extra["entry_attempts"]))
            run_mock.assert_awaited_once()
            self.assertGreater(ui._cave_public_background_state["circuit_open_until"], time.time())
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

    async def test_cave_public_entry_stops_candidate_fallback_on_shared_rate_limit(self):
        run_mock = AsyncMock(return_value={
            "ok": False,
            "message": "洞府天机命脉动态入口获取失败：external_action_rate_limited",
            "extra": {
                "retry_after_sec": 45,
                "shared_rate_limit": True,
                "shared_retry_after_sec": 45,
            },
        })
        public_urls = (
            "https://t.me/hantianzun21_bot?startapp=df_SECRET111\n"
            "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222"
        )
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                patch.object(ui, "run_cave_public_fate_cards", new=run_mock):
            ok, message, extra = await ui.ui_run_cave_public_entry(1001, "fate_cards", public_urls)

        self.assertFalse(ok)
        self.assertIn("external_action_rate_limited", message)
        self.assertEqual(0, extra["entry_index"])
        self.assertEqual(1, len(extra["entry_attempts"]))
        self.assertTrue(extra["shared_rate_limit"])
        run_mock.assert_awaited_once()

    async def test_cave_public_entry_run_does_not_fallback_for_business_completion(self):
        with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "run_cave_public_trial", new=AsyncMock(return_value={
                    "ok": False,
                    "message": "洞府天机试炼公共入口：今日次数已尽",
                    "extra": {},
                })) as run_mock:
            ok, message, extra = await ui.ui_run_cave_public_entry(
                1001,
                "trial",
                "https://t.me/hantianzun21_bot?startapp=df_SECRET111\n"
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET222",
            )

        self.assertFalse(ok)
        self.assertIn("次数已尽", message)
        self.assertEqual(0, extra["entry_index"])
        self.assertEqual(1, len(extra["entry_attempts"]))
        run_mock.assert_awaited_once()

    def test_cave_public_fishing_batch_uses_only_configured_enabled_channels(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_fishing_enabled": True,
            "cave_public_fishing_identity_ids": [3765328695, 3820064579],
        }
        with patch.object(ui, "is_cave_public_identity_available", side_effect=lambda identity_id: identity_id != 3820064579):
            selected = ui._cave_public_batch_identity_ids_for_action(
                "fishing",
                [301299112, 3765328695, 3820064579, 3852827410],
            )

        self.assertEqual([3765328695], selected)

    def test_cave_public_batch_includes_channel_health_frozen_identity(self):
        frozen_id = 3504367852
        manual_disabled_id = 3581351795
        for identity_id in (frozen_id, manual_disabled_id):
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_enabled(identity_id, False)
        state_module.set_channel_send_as_health({
            "status": "closed",
            "frozen_identity_ids": [frozen_id, manual_disabled_id],
            "restore_identity_ids": [frozen_id],
        })

        selected = ui._normalize_cave_public_batch_identity_ids({
            "send_as_ids": [frozen_id, manual_disabled_id],
        })

        self.assertEqual([frozen_id], selected)

    def test_cave_public_fishing_background_respects_failure_backoff(self):
        identity_id = 3765328695
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_fishing_enabled": True,
            "cave_public_fishing_identity_ids": [identity_id],
        }
        with state_module.use_identity(identity_id):
            state_module.state["fishing_daily_day"] = ui.get_day_key(now)
            state_module.state["fishing_daily_count"] = 1
            state_module.state["fishing_daily_limit"] = 20
            state_module.state["next_fishing_time"] = now + 1800

        self.assertFalse(ui._cave_public_background_action_due("fishing", identity_id, now))
        self.assertTrue(ui._cave_public_background_action_due("fishing", identity_id, now + 1801))

    def test_cave_public_fishing_background_stops_on_observed_daily_limit(self):
        identity_id = 3765328695
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_fishing_enabled": True,
            "cave_public_fishing_identity_ids": [identity_id],
        }
        with state_module.use_identity(identity_id):
            state_module.state["fishing_daily_day"] = ui.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_last_result"] = "MiniApp daily_limit｜fishing_daily_limit_reached"
            state_module.state["next_fishing_time"] = 0

        self.assertFalse(ui._cave_public_background_action_due("fishing", identity_id, now))

    async def test_cave_public_background_scheduler_queues_without_waiting_for_http(self):
        identity_id = 1001
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["next_stargazer_panel_time"] = 0
        background_snapshot = dict(ui._cave_public_background_state)
        retry_snapshot = dict(ui._cave_public_background_retry_at)
        scheduled = []

        def capture(coro):
            scheduled.append(coro)
            coro.close()

        try:
            ui._cave_public_background_state.update({"running": False, "next_run_at": 0})
            ui._cave_public_background_retry_at.clear()
            run_mock = AsyncMock(return_value=(True, "完成", {}))
            with patch.object(ui, "_fire_and_forget", side_effect=capture), \
                    patch.object(ui, "ui_run_cave_public_entry", new=run_mock):
                result = await ui._run_cave_public_background_scheduler(1_700_000_000.0, {
                    "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"],
                    "cave_public_stargazer_enabled": True,
                    "cave_public_delay_sec": 20,
                })

            self.assertTrue(result["started"])
            self.assertTrue(result["queued"])
            self.assertTrue(ui._cave_public_background_state["running"])
            run_mock.assert_not_awaited()
            self.assertEqual(1, len(scheduled))
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)
            ui._cave_public_background_retry_at.clear()
            ui._cave_public_background_retry_at.update(retry_snapshot)

    async def test_cave_public_background_scheduler_can_queue_selected_tianti_status(self):
        identity_id = 1001
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianti_enabled"] = False
            state_module.state["tianti_progress_current"] = 0
            state_module.state["tianti_cycle_count"] = 0
            state_module.state["tianti_gangfeng_level"] = 0
            state_module.state["tianti_cooldown_text"] = "未记录"
            state_module.state["tianti_wenxin_status"] = "未记录"
            state_module.state["tianti_last_status_seen_at"] = 0
            state_module.state["next_tianti_status_time"] = 0
        config = {
            "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"],
            "cave_public_tianti_status_enabled": True,
            "cave_public_tianti_status_identity_ids": [identity_id],
            "cave_public_delay_sec": 20,
        }
        state_module._meta_state["miniapp_auto_config"] = dict(config)
        background_snapshot = dict(ui._cave_public_background_state)
        retry_snapshot = dict(ui._cave_public_background_retry_at)
        scheduled = []

        def capture(coro):
            scheduled.append(coro)
            coro.close()

        try:
            ui._cave_public_background_state.update({"running": False, "next_run_at": 0, "circuit_open_until": 0})
            ui._cave_public_background_retry_at.clear()
            with patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                    patch.object(ui, "_fire_and_forget", side_effect=capture):
                result = await ui._run_cave_public_background_scheduler(1_700_000_000.0, config)

            self.assertTrue(result["started"])
            self.assertEqual("tianti_status", result["action"])
            self.assertEqual(1, len(scheduled))
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)
            ui._cave_public_background_retry_at.clear()
            ui._cave_public_background_retry_at.update(retry_snapshot)

    async def test_cave_public_tianti_due_is_scoped_to_candidate_identity(self):
        first_id = 1001
        second_id = 1002
        state_module.ensure_identity_registered(first_id)
        state_module.ensure_identity_registered(second_id)
        now = 1_700_000_000.0
        with state_module.use_identity(first_id):
            state_module.state["tianti_enabled"] = True
            state_module.state["next_tianti_status_time"] = now - 1
            state_module.state["tianti_progress_current"] = 1
            state_module.state["tianti_last_status_seen_at"] = now
        with state_module.use_identity(second_id):
            state_module.state["tianti_enabled"] = True
            state_module.state["next_tianti_status_time"] = now + 3600
            state_module.state["tianti_progress_current"] = 1
            state_module.state["tianti_last_status_seen_at"] = now

        config = {
            "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"],
            "cave_public_tianti_status_enabled": True,
            "cave_public_tianti_status_identity_ids": [first_id, second_id],
        }
        state_module._meta_state["miniapp_auto_config"] = dict(config)
        self.assertTrue(ui._cave_public_background_action_due("tianti_status", first_id, now))
        self.assertFalse(ui._cave_public_background_action_due("tianti_status", second_id, now))

    async def test_cave_public_background_worker_releases_slot_after_http(self):
        background_snapshot = dict(ui._cave_public_background_state)
        retry_snapshot = dict(ui._cave_public_background_retry_at)
        try:
            ui._cave_public_background_state.update({"running": True, "next_run_at": 0})
            ui._cave_public_background_retry_at.clear()
            with patch.object(ui, "ui_run_cave_public_entry", new=AsyncMock(return_value=(True, "完成", {}))), \
                    patch.object(ui, "console_log"):
                await ui._execute_cave_public_background_action(1001, "stargazer", 20)

            self.assertFalse(ui._cave_public_background_state["running"])
            self.assertEqual("1001:stargazer", ui._cave_public_background_state["last_action"])
            self.assertEqual("完成", ui._cave_public_background_state["last_result"])
            self.assertIn(("stargazer", 1001), ui._cave_public_background_retry_at)
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)
            ui._cave_public_background_retry_at.clear()
            ui._cave_public_background_retry_at.update(retry_snapshot)

    async def test_cave_public_background_pauses_shared_entry_after_rate_limit(self):
        background_snapshot = dict(ui._cave_public_background_state)
        retry_snapshot = dict(ui._cave_public_background_retry_at)
        try:
            ui._cave_public_background_state.update({
                "running": True,
                "next_run_at": 0,
                "circuit_open_until": 0,
            })
            ui._cave_public_background_retry_at.clear()
            with patch.object(ui, "ui_run_cave_public_entry", new=AsyncMock(return_value=(
                False,
                "洞府天机命脉动态入口获取失败：external_action_rate_limited",
                {
                    "retry_after_sec": 45,
                    "shared_rate_limit": True,
                    "shared_retry_after_sec": 45,
                },
            ))), patch.object(ui, "console_log"):
                started_at = time.time()
                await ui._execute_cave_public_background_action(1001, "fate_cards", 20)

            self.assertFalse(ui._cave_public_background_state["running"])
            self.assertGreaterEqual(ui._cave_public_background_state["next_run_at"], started_at + 299)
            self.assertIn("共享入口限流", ui._cave_public_background_state["last_result"])
            self.assertIn(("fate_cards", 1001), ui._cave_public_background_retry_at)
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)
            ui._cave_public_background_retry_at.clear()
            ui._cave_public_background_retry_at.update(retry_snapshot)

    async def test_cave_public_background_scheduler_holds_all_actions_while_circuit_open(self):
        background_snapshot = dict(ui._cave_public_background_state)
        try:
            now = 1_700_000_000.0
            ui._cave_public_background_state.update({
                "running": False,
                "next_run_at": 0,
                "circuit_open_until": now + 600,
                "circuit_reason": "HTTP 502 returned non JSON",
            })
            with patch.object(ui, "_fire_and_forget") as fire_mock:
                result = await ui._run_cave_public_background_scheduler(now, {
                    "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"],
                    "cave_public_stargazer_enabled": True,
                })

            self.assertFalse(result["started"])
            self.assertEqual("upstream_circuit_open", result["reason"])
            self.assertEqual(now + 600, result["retry_at"])
            fire_mock.assert_not_called()
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

    async def test_cave_public_success_closes_upstream_circuit(self):
        background_snapshot = dict(ui._cave_public_background_state)
        try:
            ui._cave_public_background_state.update({
                "circuit_open_until": time.time() + 600,
                "circuit_reason": "HTTP 502 returned non JSON",
                "next_run_at": time.time() + 600,
            })
            with patch.object(ui, "get_identity_ids", return_value=[1001]), \
                    patch.object(ui, "get_identity_enabled", return_value=True), \
                    patch.object(ui, "run_cave_public_trial", new=AsyncMock(return_value={
                        "ok": True,
                        "message": "洞府天机试炼公共入口：完成",
                        "extra": {},
                    })):
                ok, _message, _extra = await ui.ui_run_cave_public_entry(
                    1001,
                    "trial",
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                )

            self.assertTrue(ok)
            self.assertEqual(0, ui._cave_public_background_state["circuit_open_until"])
            self.assertEqual("", ui._cave_public_background_state["circuit_reason"])
            self.assertLessEqual(ui._cave_public_background_state["next_run_at"], time.time() + 60)
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

    async def test_miniapp_daily_scheduler_holds_tree_and_batches_while_circuit_open(self):
        background_snapshot = dict(ui._cave_public_background_state)
        try:
            now = 1_700_000_000.0
            ui._cave_public_background_state.update({
                "running": False,
                "circuit_open_until": now + 600,
                "circuit_reason": "HTTP 502 returned non JSON",
            })
            tree_mock = AsyncMock(return_value={"started": True})
            with patch.object(ui, "normalize_miniapp_auto_config", return_value={}), \
                    patch.object(ui, "get_miniapp_auto_config_snapshot", return_value={}), \
                    patch.object(ui, "get_global_enabled", return_value=True), \
                    patch.object(ui, "_run_tree_miniapp_daily_scheduler", new=tree_mock):
                result = await ui.run_miniapp_daily_scheduler(now)

            self.assertFalse(result["started"])
            self.assertEqual("upstream_circuit_open", result["reason"])
            tree_mock.assert_not_awaited()
        finally:
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

    async def test_cave_public_background_marks_treasure_daily_exhausted_from_success(self):
        identity_id = 1001
        state_module.ensure_identity_registered(identity_id)
        daily_done_snapshot = set(ui._cave_public_background_daily_done)
        retry_snapshot = dict(ui._cave_public_background_retry_at)
        background_snapshot = dict(ui._cave_public_background_state)
        try:
            ui._cave_public_background_daily_done.clear()
            ui._cave_public_background_retry_at.clear()
            ui._cave_public_background_state.update({"running": True, "next_run_at": 0})
            with patch.object(
                ui,
                "ui_run_cave_public_entry",
                new=AsyncMock(return_value=(
                    True,
                    "洞府寻宝公共入口：MiniApp daily_limit｜游戏 3/3｜奖励:灵石x1",
                    {"daily_exhausted": True},
                )),
            ):
                with patch.object(ui, "console_log"):
                    await ui._execute_cave_public_background_action(identity_id, "treasure", 20)

            self.assertFalse(ui._cave_public_background_action_due("treasure", identity_id, time.time()))
        finally:
            ui._cave_public_background_daily_done.clear()
            ui._cave_public_background_daily_done.update(daily_done_snapshot)
            ui._cave_public_background_retry_at.clear()
            ui._cave_public_background_retry_at.update(retry_snapshot)
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

    async def test_cave_public_background_marks_fishing_terminal_skip_as_daily_done(self):
        identity_id = 1001
        daily_done_snapshot = set(ui._cave_public_background_daily_done)
        retry_snapshot = dict(ui._cave_public_background_retry_at)
        background_snapshot = dict(ui._cave_public_background_state)
        try:
            ui._cave_public_background_daily_done.clear()
            ui._cave_public_background_retry_at.clear()
            ui._cave_public_background_state.update({"running": True, "next_run_at": 0})
            with patch.object(
                ui,
                "ui_run_cave_public_entry",
                new=AsyncMock(return_value=(
                    True,
                    "无可用鱼饵，今日跳过灵溪垂钓",
                    {"skipped": "bait_missing", "terminal_skip": True},
                )),
            ):
                with patch.object(ui, "console_log"):
                    await ui._execute_cave_public_background_action(identity_id, "fishing", 20)

            self.assertFalse(ui._cave_public_background_action_due("fishing", identity_id, time.time()))
        finally:
            ui._cave_public_background_daily_done.clear()
            ui._cave_public_background_daily_done.update(daily_done_snapshot)
            ui._cave_public_background_retry_at.clear()
            ui._cave_public_background_retry_at.update(retry_snapshot)
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

    async def test_world_boss_miniapp_config_is_default_off_and_clamped(self):
        state_module._meta_state["miniapp_auto_config"] = {}

        initial = ui.get_miniapp_status_snapshot()["automation"]
        self.assertFalse(initial["world_boss_auto_enabled"])
        self.assertEqual(1, initial["world_boss_auto_account_limit"])
        self.assertEqual(0, initial["world_boss_auto_finish_reserve_windows"])

        with patch.object(ui, "save_state", return_value=True) as save_mock:
            ok, message = await ui.ui_set_world_boss_miniapp_config({
                "enabled": True,
                "account_limit": 9,
                "account_gap_sec": 0,
                "excluded_identity_ids": "8659059191, 301299112",
                "window_skip_by_identity": {"301299112": 2, "8659059191": 0},
            })

        automation = ui.get_miniapp_status_snapshot()["automation"]
        self.assertTrue(ok)
        self.assertIn("最多 4 个登录账户", message)
        self.assertTrue(automation["world_boss_auto_enabled"])
        self.assertEqual(4, automation["world_boss_auto_account_limit"])
        self.assertEqual(1, automation["world_boss_auto_account_gap_sec"])
        self.assertEqual([301299112, 8659059191], automation["world_boss_auto_excluded_identity_ids"])
        self.assertEqual({"301299112": 2}, automation["world_boss_auto_window_skip_by_identity"])
        self.assertEqual(0, automation["world_boss_auto_finish_reserve_windows"])
        self.assertIn("少出手身份 1 个", message)
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
                    patch.object(ui, "is_cave_public_identity_available", return_value=True), \
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
        with patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                patch.object(ui, "get_identity_account", side_effect=lambda identity_id: account_by_identity[identity_id]):
            selected = ui._cave_public_batch_identity_ids_for_action("small_world", identity_ids)
            trial_selected = ui._cave_public_batch_identity_ids_for_action("trial", identity_ids)
            steps = ui._build_cave_public_batch_steps(identity_ids, ["small_world"])

        self.assertEqual(account_ids, selected)
        self.assertEqual(identity_ids, trial_selected)
        self.assertEqual([(account_id, "small_world") for account_id in account_ids], steps)

    def test_cave_public_harvest_batch_keeps_each_selectable_player_identity(self):
        identity_ids = [1001, 100101, 100102, 1002, 100201, 100202]
        with patch.object(ui, "is_cave_public_identity_available", return_value=True):
            selected = ui._cave_public_batch_identity_ids_for_action("small_world_harvest", identity_ids)
            steps = ui._build_cave_public_batch_steps(identity_ids, ["small_world_harvest"])

        self.assertEqual(identity_ids, selected)
        self.assertEqual([(identity_id, "small_world_harvest") for identity_id in identity_ids], steps)

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

        with patch.object(ui, "is_cave_public_identity_available", side_effect=lambda identity_id: identity_id != 1002), \
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
            extra = {
                "settled_count": 3 if action == "trial" else 0,
                "gains": {"天机残痕": 43} if action == "trial" else {},
            }
            return True, f"{action} 完成", extra

        try:
            with patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                    patch.object(ui, "get_identity_display_name", side_effect=lambda identity_id: f"角色{identity_id}"), \
                    patch.object(ui, "ui_run_cave_public_entry", new=run_entry), \
                    patch.object(ui, "send_audit_log", new=AsyncMock()) as audit_mock:
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
            self.assertTrue(any(
                "天机试炼：2/2 成功｜结算 6次｜收益:天机残痕+86" in str(call.args[0])
                for call in audit_mock.await_args_list
            ))
        finally:
            ui._cave_public_batch_state.clear()
            ui._cave_public_batch_state.update(batch_snapshot)

    async def test_trial_daily_batch_marks_wave_done_only_after_full_execution(self):
        batch_snapshot = dict(ui._cave_public_batch_state)

        async def run_entry(_identity_id, _action, _url):
            return True, "试炼完成", {"settled_count": 3}

        try:
            with patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                    patch.object(ui, "get_identity_display_name", side_effect=lambda identity_id: f"角色{identity_id}"), \
                    patch.object(ui, "ui_run_cave_public_entry", new=run_entry), \
                    patch.object(ui, "send_audit_log", new=AsyncMock()), \
                    patch.object(ui, "save_state", return_value=True) as save_mock:
                await ui._run_cave_public_entry_batch(
                    "trial-wave2-complete",
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    [1001, 1002],
                    ["trial"],
                    0,
                    trial_daily_context={
                        "wave_key": "wave2",
                        "wave_label": "第二批",
                        "day_key": "2026-07-07",
                    },
                )

            config = ui.normalize_miniapp_auto_config()
            self.assertEqual("2026-07-07", config["trial_daily_wave2_last_run_day"])
            self.assertEqual("completed", config["trial_daily_wave2_last_status"])
            self.assertIn("完整执行 2/2", config["trial_daily_wave2_last_result"])
            save_mock.assert_called_once()
        finally:
            ui._cave_public_batch_state.clear()
            ui._cave_public_batch_state.update(batch_snapshot)

    async def test_trial_daily_batch_with_completed_failures_retries_only_failed_steps(self):
        batch_snapshot = dict(ui._cave_public_batch_state)
        calls = []
        should_fail = {1002, 1003}

        async def run_entry(identity_id, _action, _url):
            calls.append(identity_id)
            if identity_id in should_fail:
                return False, "入口暂不可用", {}
            return True, "试炼完成", {"settled_count": 3}

        context = {
            "wave_key": "wave2",
            "wave_label": "第二批",
            "day_key": "2026-07-07",
        }
        try:
            with patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                    patch.object(ui, "get_identity_display_name", side_effect=lambda identity_id: f"角色{identity_id}"), \
                    patch.object(ui, "ui_run_cave_public_entry", new=run_entry), \
                    patch.object(ui, "send_audit_log", new=AsyncMock()), \
                    patch.object(ui, "save_state", return_value=True):
                await ui._run_cave_public_entry_batch(
                    "trial-wave2-failures",
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    [1001, 1002, 1003],
                    ["trial"],
                    0,
                    trial_daily_context=context,
                )
                config = ui.normalize_miniapp_auto_config()
                self.assertEqual("retry_pending", config["trial_daily_wave2_last_status"])
                self.assertEqual("", config["trial_daily_wave2_last_run_day"])
                self.assertEqual(
                    [
                        {"identity_id": 1002, "action": "trial"},
                        {"identity_id": 1003, "action": "trial"},
                    ],
                    config["trial_daily_wave2_last_steps"],
                )
                should_fail.clear()
                resume = ui._trial_daily_batch_resume_state(context)
                await ui._run_cave_public_entry_batch(
                    "trial-wave2-failures",
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    [1002, 1003],
                    ["trial"],
                    0,
                    trial_daily_context=context,
                    resume_cursor=resume["cursor"],
                    initial_completed=resume["completed"],
                    initial_succeeded=resume["succeeded"],
                    initial_failed=resume["failed"],
                    initial_outcomes=resume["outcomes"],
                    steps_override=resume["steps"],
                )

            self.assertEqual([1001, 1002, 1003, 1002, 1003], calls)
            config = ui.normalize_miniapp_auto_config()
            self.assertEqual("completed", config["trial_daily_wave2_last_status"])
            self.assertEqual("2026-07-07", config["trial_daily_wave2_last_run_day"])
        finally:
            ui._cave_public_batch_state.clear()
            ui._cave_public_batch_state.update(batch_snapshot)

    async def test_trial_daily_batch_upstream_abort_remains_retryable(self):
        batch_snapshot = dict(ui._cave_public_batch_state)

        async def run_entry(_identity_id, _action, _url):
            return False, "动态入口获取失败：Read timed out. (read timeout=5)", {}

        try:
            with patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                    patch.object(ui, "get_identity_display_name", side_effect=lambda identity_id: f"角色{identity_id}"), \
                    patch.object(ui, "ui_run_cave_public_entry", new=run_entry), \
                    patch.object(ui, "send_audit_log", new=AsyncMock()), \
                    patch.object(ui, "save_state", return_value=True) as save_mock:
                await ui._run_cave_public_entry_batch(
                    "trial-wave2-abort",
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    [1001, 1002],
                    ["trial"],
                    0,
                    trial_daily_context={
                        "wave_key": "wave2",
                        "wave_label": "第二批",
                        "day_key": "2026-07-07",
                    },
                )

            config = ui.normalize_miniapp_auto_config()
            self.assertEqual("", config["trial_daily_wave2_last_run_day"])
            self.assertEqual("retry_pending", config["trial_daily_wave2_last_status"])
            self.assertIn("完成 1/2", config["trial_daily_wave2_last_result"])
            self.assertEqual(1, ui._cave_public_batch_state["completed"])
            save_mock.assert_called_once()
        finally:
            ui._cave_public_batch_state.clear()
            ui._cave_public_batch_state.update(batch_snapshot)

    async def test_trial_daily_batch_pause_resumes_from_unexecuted_step(self):
        batch_snapshot = dict(ui._cave_public_batch_state)
        context = {
            "wave_key": "wave1",
            "wave_label": "第一批",
            "day_key": "2026-07-07",
        }
        calls = []
        pause_once = True

        async def run_entry(identity_id, action, _url):
            nonlocal pause_once
            calls.append((identity_id, action))
            if identity_id == 1002 and pause_once:
                pause_once = False
                return False, "全局暂停来源不允许洞府公共入口 MiniApp HTTP", {}
            return True, "试炼完成", {"settled_count": 1}

        try:
            with patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                    patch.object(ui, "get_identity_display_name", side_effect=lambda identity_id: f"角色{identity_id}"), \
                    patch.object(ui, "ui_run_cave_public_entry", new=run_entry), \
                    patch.object(ui, "send_audit_log", new=AsyncMock()), \
                    patch.object(ui, "save_state", return_value=True):
                await ui._run_cave_public_entry_batch(
                    "trial-wave1-pause",
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    [1001, 1002, 1003],
                    ["trial"],
                    0,
                    trial_daily_context=context,
                )

                config = ui.normalize_miniapp_auto_config()
                self.assertEqual("retry_pending", config["trial_daily_wave1_last_status"])
                self.assertEqual(1, config["trial_daily_wave1_last_cursor"])
                self.assertEqual(1, config["trial_daily_wave1_last_completed"])
                self.assertEqual(1, config["trial_daily_wave1_last_succeeded"])
                self.assertEqual(0, config["trial_daily_wave1_last_failed"])
                self.assertEqual(
                    [{"identity_id": 1001, "action": "trial"},
                     {"identity_id": 1002, "action": "trial"},
                     {"identity_id": 1003, "action": "trial"}],
                    config["trial_daily_wave1_last_steps"],
                )

                resume = ui._trial_daily_batch_resume_state(context)
                self.assertEqual(1, resume["cursor"])
                await ui._run_cave_public_entry_batch(
                    "trial-wave1-pause",
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    [1001, 1002, 1003],
                    ["trial"],
                    0,
                    trial_daily_context=context,
                    resume_cursor=resume["cursor"],
                    initial_completed=resume["completed"],
                    initial_succeeded=resume["succeeded"],
                    initial_failed=resume["failed"],
                    initial_outcomes=resume["outcomes"],
                    steps_override=resume["steps"],
                )

            self.assertEqual([(1001, "trial"), (1002, "trial"), (1002, "trial"), (1003, "trial")], calls)
            config = ui.normalize_miniapp_auto_config()
            self.assertEqual("completed", config["trial_daily_wave1_last_status"])
            self.assertEqual("2026-07-07", config["trial_daily_wave1_last_run_day"])
            self.assertIn("完整执行 3/3", config["trial_daily_wave1_last_result"])
        finally:
            ui._cave_public_batch_state.clear()
            ui._cave_public_batch_state.update(batch_snapshot)

    async def test_cave_public_trial_batch_stops_after_two_matching_entry_failures(self):
        batch_snapshot = dict(ui._cave_public_batch_state)
        background_snapshot = dict(ui._cave_public_background_state)
        calls = []

        async def run_entry(identity_id, action, _url):
            calls.append((identity_id, action))
            return False, "洞府天机试炼入口读取完成，但外府试炼入口不可用", {}

        try:
            ui._close_cave_public_upstream_circuit()
            with patch.object(ui, "get_identity_display_name", side_effect=lambda identity_id: f"角色{identity_id}"), \
                    patch.object(ui, "ui_run_cave_public_entry", new=run_entry), \
                    patch.object(ui, "send_audit_log", new=AsyncMock()) as audit_mock:
                await ui._run_cave_public_entry_batch(
                    "cave_public_trial_fail_fast",
                    "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                    [1001, 1002, 1003],
                    ["trial"],
                    0,
                )

            self.assertEqual([(1001, "trial"), (1002, "trial")], calls)
            self.assertFalse(ui._cave_public_batch_state["running"])
            self.assertEqual(2, ui._cave_public_batch_state["completed"])
            self.assertEqual(2, ui._cave_public_batch_state["failed"])
            self.assertGreater(ui._cave_public_background_state["circuit_open_until"], time.time())
            self.assertTrue(any("连续 2 个身份" in str(call.args[0]) for call in audit_mock.await_args_list))
        finally:
            ui._cave_public_batch_state.clear()
            ui._cave_public_batch_state.update(batch_snapshot)
            ui._cave_public_background_state.clear()
            ui._cave_public_background_state.update(background_snapshot)

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
            "cave_public_small_world_harvest_enabled": False,
            "cave_public_deep_status_enabled": False,
            "cave_public_treasure_enabled": False,
            "cave_public_trial_enabled": True,
            "cave_public_stargazer_enabled": False,
            "cave_public_yuanying_enabled": False,
        }
        now = datetime(2026, 7, 7, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "_normalize_cave_public_batch_identity_ids", return_value=[1001, 1002, 1003, 1004]) as ids_mock, \
                patch.object(ui, "ui_start_cave_public_entry_batch", new=AsyncMock(return_value=(True, "ok", {"batch_id": "batch-auto"}))) as start_mock, \
                patch.object(ui, "save_state", return_value=True) as save_mock:
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertTrue(result["started"])
        self.assertEqual("batch-auto", result["batch_id"])
        self.assertEqual(2, result["count"])
        self.assertEqual("wave1", result["wave"])
        ids_mock.assert_called_once_with({})
        self.assertEqual([1001, 1002], start_mock.await_args.args[0]["send_as_ids"])
        self.assertEqual(["trial"], start_mock.await_args.args[0]["actions"])
        self.assertEqual("wave1", start_mock.await_args.kwargs["trial_daily_context"]["wave_key"])
        save_mock.assert_called_once()
        snapshot = ui.get_miniapp_status_snapshot()["automation"]
        self.assertFalse(snapshot["trial_daily_done_today"])
        self.assertEqual("batch-auto", snapshot["trial_daily_last_batch_id"])
        self.assertEqual("batch-auto", snapshot["trial_daily_wave1_last_batch_id"])
        self.assertEqual("running", snapshot["trial_daily_wave1_last_status"])
        self.assertEqual("", snapshot["trial_daily_wave1_last_run_day"])

    async def test_miniapp_daily_scheduler_does_not_duplicate_running_batch(self):
        batch_snapshot = dict(ui._cave_public_batch_state)
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            "cave_public_small_world_enabled": False,
            "cave_public_small_world_harvest_enabled": False,
            "cave_public_deep_status_enabled": False,
            "cave_public_treasure_enabled": False,
            "cave_public_trial_enabled": True,
            "trial_daily_wave1_last_status": "running",
        }
        now = datetime(2026, 7, 7, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        try:
            ui._cave_public_batch_state["running"] = True
            with patch.object(ui, "ui_start_cave_public_entry_batch", new=AsyncMock()) as start_mock, \
                    patch.object(ui, "_run_cave_public_background_scheduler", new=AsyncMock(return_value={"started": False})):
                result = await ui.run_miniapp_daily_scheduler(now)

            self.assertEqual({"started": False, "reason": "cave_public_busy"}, result)
            start_mock.assert_not_awaited()
        finally:
            ui._cave_public_batch_state.clear()
            ui._cave_public_batch_state.update(batch_snapshot)

    async def test_miniapp_daily_scheduler_retries_unfinished_wave(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            "cave_public_small_world_enabled": False,
            "cave_public_small_world_harvest_enabled": False,
            "cave_public_deep_status_enabled": False,
            "cave_public_treasure_enabled": False,
            "cave_public_trial_enabled": True,
            "trial_daily_wave2_last_batch_id": "failed-batch",
            "trial_daily_wave2_last_status": "retry_pending",
            "trial_daily_wave2_last_result": "上游异常，等待重试",
        }
        now = datetime(2026, 7, 7, 5, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "_normalize_cave_public_batch_identity_ids", return_value=[1001, 1002, 1003, 1004]), \
                patch.object(ui, "ui_start_cave_public_entry_batch", new=AsyncMock(return_value=(True, "ok", {"batch_id": "retry-batch"}))) as start_mock, \
                patch.object(ui, "save_state", return_value=True):
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertTrue(result["started"])
        self.assertEqual("retry-batch", result["batch_id"])
        self.assertEqual([1003, 1004], start_mock.await_args.args[0]["send_as_ids"])
        config = ui.normalize_miniapp_auto_config()
        self.assertEqual("", config["trial_daily_wave2_last_run_day"])
        self.assertEqual("running", config["trial_daily_wave2_last_status"])

    async def test_miniapp_daily_scheduler_resumes_paused_wave_after_window(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
            "cave_public_small_world_enabled": False,
            "cave_public_small_world_harvest_enabled": False,
            "cave_public_deep_status_enabled": False,
            "cave_public_treasure_enabled": False,
            "cave_public_trial_enabled": True,
            "trial_daily_wave1_last_batch_id": "paused-batch",
            "trial_daily_wave1_last_status": "retry_pending",
            "trial_daily_wave1_last_progress_day": "2026-07-07",
            "trial_daily_wave1_last_cursor": 1,
            "trial_daily_wave1_last_completed": 1,
            "trial_daily_wave1_last_succeeded": 1,
            "trial_daily_wave1_last_steps": [
                {"identity_id": 1001, "action": "trial"},
                {"identity_id": 1002, "action": "trial"},
            ],
        }
        now = datetime(2026, 7, 7, 9, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "_normalize_cave_public_batch_identity_ids", return_value=[1001, 1002, 1003, 1004]), \
                patch.object(ui, "ui_start_cave_public_entry_batch", new=AsyncMock(return_value=(True, "ok", {
                    "batch_id": "paused-batch",
                    "resumed": True,
                    "resume_cursor": 1,
                    "completed": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "batch_steps": [
                        {"identity_id": 1001, "action": "trial"},
                        {"identity_id": 1002, "action": "trial"},
                    ],
                }))) as start_mock, \
                patch.object(ui, "save_state", return_value=True):
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertTrue(result["started"])
        self.assertEqual("wave1", result["wave"])
        self.assertEqual("paused-batch", result["batch_id"])
        self.assertEqual([1001, 1002], start_mock.await_args.args[0]["send_as_ids"])
        self.assertEqual("wave1", start_mock.await_args.kwargs["trial_daily_context"]["wave_key"])

    async def test_miniapp_daily_scheduler_starts_public_tree_once_and_persists_running(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "tree_daily_enabled_identity_ids": [1001],
            "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"],
        }
        now = datetime(2026, 7, 7, 2, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        scheduled = []

        def capture_task(coro):
            scheduled.append(coro)
            coro.close()

        with patch.object(ui, "get_global_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_coordinator_snapshot", return_value={"phase": "idle"}), \
                patch.object(ui, "check_tree_miniapp_eligibility", return_value=(True, "")), \
                patch.object(ui, "_tree_daily_state_for_identity", return_value={}), \
                patch.object(ui, "get_tree_miniapp_score_config", return_value={"jump": {}, "fly": {}}), \
                patch.object(ui, "_fire_and_forget", side_effect=capture_task) as fire_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock, \
                patch.object(ui, "record_miniapp_state") as record_mock, \
                patch.object(ui, "send_audit_log", new=AsyncMock()):
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertTrue(result["started"])
        self.assertEqual(1001, result["identity_id"])
        self.assertEqual("cave_public", result["source"])
        fire_mock.assert_called_once()
        self.assertEqual(1, len(scheduled))
        send_mock.assert_not_awaited()
        self.assertEqual("running", record_mock.call_args.args[2]["phase"])
        self.assertEqual(0, record_mock.call_args.args[2]["command_msg_id"])

    async def test_miniapp_daily_scheduler_does_not_repeat_tree_attempt_same_day(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "tree_daily_enabled_identity_ids": [1001],
        }
        now = datetime(2026, 7, 7, 2, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "get_global_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_coordinator_snapshot", return_value={"phase": "idle"}), \
                patch.object(ui, "check_tree_miniapp_eligibility", return_value=(True, "")), \
                patch.object(ui, "_tree_daily_state_for_identity", return_value={"kind": "daily", "day_key": "2026-07-07", "phase": "unknown"}), \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertEqual({"started": False, "reason": "disabled"}, result)
        send_mock.assert_not_awaited()

    async def test_tree_daily_scheduler_waits_for_retry_after_before_retrying(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "tree_daily_enabled_identity_ids": [1001],
            "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"],
        }
        now = datetime(2026, 7, 7, 2, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "get_global_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_coordinator_snapshot", return_value={"phase": "idle"}), \
                patch.object(ui, "check_tree_miniapp_eligibility", return_value=(True, "")), \
                patch.object(ui, "_tree_daily_state_for_identity", return_value={
                    "kind": "daily",
                    "day_key": "2026-07-07",
                    "phase": "retry_pending",
                    "retry_after_sec": 3600,
                    "retry_at": now + 3600,
                }), \
                patch.object(ui, "_fire_and_forget") as fire_mock:
            result = await ui._run_tree_miniapp_daily_scheduler(
                now,
                ui.normalize_miniapp_auto_config(),
            )

        self.assertEqual({"started": False, "reason": "tree_done_or_ineligible"}, result)
        fire_mock.assert_not_called()

    async def test_tree_daily_scheduler_retries_after_retry_after_deadline(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "tree_daily_enabled_identity_ids": [1001],
            "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"],
        }
        now = datetime(2026, 7, 7, 2, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        scheduled = []

        def capture_task(coro):
            scheduled.append(coro)
            coro.close()

        with patch.object(ui, "get_global_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_coordinator_snapshot", return_value={"phase": "idle"}), \
                patch.object(ui, "check_tree_miniapp_eligibility", return_value=(True, "")), \
                patch.object(ui, "_tree_daily_state_for_identity", return_value={
                    "kind": "daily",
                    "day_key": "2026-07-07",
                    "phase": "retry_pending",
                    "retry_after_sec": 3600,
                    "retry_at": now - 1,
                }), \
                patch.object(ui, "get_tree_miniapp_score_config", return_value={"jump": {}, "fly": {}}), \
                patch.object(ui, "_fire_and_forget", side_effect=capture_task) as fire_mock, \
                patch.object(ui, "record_miniapp_state") as record_mock:
            result = await ui._run_tree_miniapp_daily_scheduler(
                now,
                ui.normalize_miniapp_auto_config(),
            )

        self.assertTrue(result["started"])
        fire_mock.assert_called_once()
        self.assertEqual("running", record_mock.call_args.args[2]["phase"])
        self.assertEqual(1, len(scheduled))

    async def test_tree_daily_scheduler_migrates_legacy_entry_unknown_to_public_entry(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "tree_daily_enabled_identity_ids": [1001],
            "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999"],
        }
        now = datetime(2026, 7, 7, 2, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        scheduled = []

        def capture_task(coro):
            scheduled.append(coro)
            coro.close()

        with patch.object(ui, "get_global_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_coordinator_snapshot", return_value={"phase": "idle"}), \
                patch.object(ui, "check_tree_miniapp_eligibility", return_value=(True, "")), \
                patch.object(ui, "_tree_daily_state_for_identity", return_value={
                    "kind": "daily",
                    "day_key": "2026-07-07",
                    "phase": "unknown",
                    "error": "入口命令无回包",
                }), \
                patch.object(ui, "get_tree_miniapp_score_config", return_value={"jump": {}, "fly": {}}), \
                patch.object(ui, "_fire_and_forget", side_effect=capture_task) as fire_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock, \
                patch.object(ui, "record_miniapp_state") as record_mock:
            result = await ui._run_tree_miniapp_daily_scheduler(
                now,
                ui.normalize_miniapp_auto_config(),
            )

        self.assertTrue(result["started"])
        self.assertEqual("cave_public", result["source"])
        fire_mock.assert_called_once()
        self.assertEqual(1, len(scheduled))
        send_mock.assert_not_awaited()
        self.assertEqual("running", record_mock.call_args.args[2]["phase"])

    async def test_tree_daily_scheduler_closes_stale_entry_without_resend(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "tree_daily_enabled_identity_ids": [1001],
        }
        now = datetime(2026, 7, 7, 2, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        coordinator = {
            "phase": "entry_pending",
            "identity_id": 1001,
            "day_key": "2026-07-07",
            "op_id": "tree_daily:2026-07-07:1001",
            "command_msg_id": 77001,
            "started_at": now - ui.TREE_MINIAPP_ENTRY_PENDING_TIMEOUT_SEC - 1,
        }
        with patch.object(ui, "get_global_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_coordinator_snapshot", return_value=coordinator), \
                patch.object(ui, "cancel_tree_miniapp_daily_run", return_value=True) as cancel_mock, \
                patch.object(ui, "record_miniapp_state") as record_mock, \
                patch.object(ui, "send_audit_log", new=AsyncMock()) as audit_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            result = await ui._run_tree_miniapp_daily_scheduler(
                now,
                ui.normalize_miniapp_auto_config(),
            )

        self.assertEqual({"started": False, "reason": "tree_entry_timeout", "identity_id": 1001}, result)
        cancel_mock.assert_called_once_with(
            "tree_daily:2026-07-07:1001",
            reason="入口命令无回包",
            now=now,
        )
        self.assertEqual("unknown", record_mock.call_args.args[2]["phase"])
        audit_mock.assert_awaited_once()
        send_mock.assert_not_awaited()

    async def test_tree_daily_scheduler_closes_persisted_stale_entry_after_restart(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "tree_daily_enabled_identity_ids": [1001],
        }
        now = datetime(2026, 7, 7, 2, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        persisted = {
            "kind": "daily",
            "day_key": "2026-07-07",
            "phase": "entry_pending",
            "command_msg_id": 77001,
            "_record_updated_at": now - ui.TREE_MINIAPP_ENTRY_PENDING_TIMEOUT_SEC - 1,
            "_record_source_id": "tree_daily:2026-07-07:1001",
        }
        with patch.object(ui, "get_global_enabled", return_value=True), \
                patch.object(ui, "get_tree_miniapp_coordinator_snapshot", return_value={"phase": "idle"}), \
                patch.object(ui, "check_tree_miniapp_eligibility", return_value=(True, "")), \
                patch.object(ui, "_tree_daily_state_for_identity", return_value=persisted), \
                patch.object(ui, "cancel_tree_miniapp_daily_run", return_value=False) as cancel_mock, \
                patch.object(ui, "record_miniapp_state") as record_mock, \
                patch.object(ui, "send_audit_log", new=AsyncMock()) as audit_mock, \
                patch.object(ui, "send_game_command", new=AsyncMock()) as send_mock:
            result = await ui._run_tree_miniapp_daily_scheduler(
                now,
                ui.normalize_miniapp_auto_config(),
            )

        self.assertEqual({"started": False, "reason": "tree_entry_timeout", "identity_id": 1001}, result)
        cancel_mock.assert_called_once()
        self.assertEqual("unknown", record_mock.call_args.args[2]["phase"])
        audit_mock.assert_awaited_once()
        send_mock.assert_not_awaited()

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
        with patch.object(ui, "_normalize_cave_public_batch_identity_ids", return_value=[1001, 1002, 1003, 1004]) as ids_mock, \
                patch.object(ui, "ui_start_cave_public_entry_batch", new=AsyncMock(return_value=(True, "ok", {"batch_id": "batch-wave2"}))) as start_mock, \
                patch.object(ui, "save_state", return_value=True):
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertTrue(result["started"])
        self.assertEqual("wave2", result["wave"])
        self.assertEqual(2, result["count"])
        ids_mock.assert_called_once_with({})
        self.assertEqual([1003, 1004], start_mock.await_args.args[0]["send_as_ids"])
        self.assertEqual("wave2", start_mock.await_args.kwargs["trial_daily_context"]["wave_key"])

    async def test_miniapp_daily_scheduler_waits_when_global_disabled(self):
        state_module._meta_state["miniapp_auto_config"] = {
            "trial_daily_enabled": True,
            "trial_daily_scheduler_confirmed": True,
        }
        now = datetime(2026, 7, 7, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
        with patch.object(ui, "get_global_enabled", return_value=False), \
                patch.object(ui, "_normalize_cave_public_batch_identity_ids") as ids_mock, \
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
                patch.object(ui, "_normalize_cave_public_batch_identity_ids", return_value=[1001, 1002]), \
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
        self.assertEqual("miniapp", adapters["tree"]["ui_group"])
        self.assertEqual(".钓鱼", probe_commands["fishing"])
        self.assertEqual(".灵树", probe_commands["tree"])
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
        self.assertEqual(".洞府", probe_commands["cave_treasure"])
        self.assertEqual(".洞府", manual_run_commands["cave_treasure"])
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

    def test_cave_public_small_world_uses_independent_harvest_due_time(self):
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(1001)
        with state_module.use_identity(1001):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["next_small_world_time"] = now + 6 * 3600
            state_module.state["small_world_next_public_harvest_at"] = now - 1
            self.assertTrue(ui._cave_public_background_action_due("small_world", 1001, now))

            state_module.state["small_world_next_public_harvest_at"] = now + 8 * 3600
            self.assertFalse(ui._cave_public_background_action_due("small_world", 1001, now))

            state_module.state["small_world_harvest_enabled"] = False
            state_module.state["small_world_next_public_harvest_at"] = now - 1
            self.assertFalse(ui._cave_public_background_action_due("small_world", 1001, now))

    def test_cave_public_due_harvest_precedes_deep_retreat_backlog(self):
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(1001)
        with state_module.use_identity(1001):
            state_module.state["small_world_next_public_harvest_at"] = now - 1
            state_module.state["next_deep_retreat_time"] = now - 3600

        harvest_key = ui._cave_public_background_candidate_sort_key("small_world_harvest", 1001, now)
        deep_key = ui._cave_public_background_candidate_sort_key("deep_status", 1001, now)
        self.assertLess(harvest_key, deep_key)

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

    def test_cave_public_post_summary_deep_start_precedes_older_deep_backlog(self):
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(1001)
        state_module.ensure_identity_registered(1002)
        with state_module.use_identity(1001):
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["next_deep_retreat_time"] = now - 3600
        with state_module.use_identity(1002):
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["next_deep_retreat_time"] = now - 1

        candidates = [(1001, "deep_settle"), (1002, "deep_start")]
        candidates.sort(key=lambda item: ui._cave_public_background_candidate_sort_key(item[1], item[0], now))

        self.assertEqual((1002, "deep_start"), candidates[0])

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

    async def test_fate_cards_daily_summary_waits_for_every_settled_identity(self):
        now = datetime(2026, 7, 29, 12, 0, tzinfo=ui.TZ_LOCAL).timestamp()
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_fate_cards_enabled": True,
        }
        state_module._meta_state["miniapp_state_records"] = {
            "1001:fate_cards": {
                "state": {"challenge_date": "2026-07-29", "status": "settled", "gains": {"修为": 36}},
            },
            "1002:fate_cards": {
                "state": {"challenge_date": "2026-07-29", "status": "waiting_quest", "gains": {"修为": 18}},
            },
        }
        audit_mock = AsyncMock()
        with patch.object(ui, "_normalize_cave_public_batch_identity_ids", return_value=[1001, 1002]), \
                patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                patch.object(ui, "send_audit_log", new=audit_mock), \
                patch.object(ui, "save_state"):
            self.assertFalse(await ui.maybe_send_cave_public_fate_cards_daily_summary(now))

        audit_mock.assert_not_awaited()

    async def test_fate_cards_daily_summary_is_short_and_idempotent(self):
        now = datetime(2026, 7, 29, 12, 0, tzinfo=ui.TZ_LOCAL).timestamp()
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_fate_cards_enabled": True,
        }
        state_module._meta_state["miniapp_state_records"] = {
            "1001:fate_cards": {
                "state": {"challenge_date": "2026-07-29", "status": "settled", "gains": {"修为": 36, "天机残痕": 2}},
            },
            "1002:fate_cards": {
                "state": {"challenge_date": "2026-07-29", "status": "settled", "gains": {"修为": 42, "天机残痕": 1}},
            },
        }
        audit_mock = AsyncMock(return_value=True)
        with patch.object(ui, "_normalize_cave_public_batch_identity_ids", return_value=[1001, 1002]), \
                patch.object(ui, "is_cave_public_identity_available", return_value=True), \
                patch.object(ui, "send_audit_log", new=audit_mock), \
                patch.object(ui, "save_state"):
            self.assertTrue(await ui.maybe_send_cave_public_fate_cards_daily_summary(now))
            self.assertFalse(await ui.maybe_send_cave_public_fate_cards_daily_summary(now))

        audit_mock.assert_awaited_once()
        message = audit_mock.await_args.args[0]
        self.assertIn("天机命脉日报｜2/2完成", message)
        self.assertIn("修为+78", message)
        self.assertIn("天机残痕+3", message)
        self.assertLessEqual(len(message), 420)
        config = ui.normalize_miniapp_auto_config()
        self.assertEqual("2026-07-29", config["cave_public_fate_cards_last_report_day"])
        self.assertTrue(config["cave_public_fate_cards_last_report_signature"])

    async def test_miniapp_scheduler_flushes_ready_fate_cards_summary_before_actions(self):
        now = datetime(2026, 7, 29, 12, 0, tzinfo=ui.TZ_LOCAL).timestamp()
        summary_mock = AsyncMock(return_value=True)
        tree_mock = AsyncMock()
        with patch.object(ui, "maybe_send_cave_public_fate_cards_daily_summary", new=summary_mock), \
                patch.object(ui, "_run_tree_miniapp_daily_scheduler", new=tree_mock):
            result = await ui.run_miniapp_daily_scheduler(now)

        self.assertEqual({"started": True, "kind": "fate_cards_daily_summary"}, result)
        summary_mock.assert_awaited_once_with(now)
        tree_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
