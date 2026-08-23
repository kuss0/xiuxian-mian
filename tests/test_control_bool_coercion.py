import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from model import config
from model import control
from model import state as state_module
from model import ui


class ControlBoolCoercionTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_direct_identity_toggle_treats_form_false_string_as_disabled(self):
        send_as_id = 990301
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)

        with patch.object(control, "save_state"), patch.object(control, "send_audit_log", new=AsyncMock()):
            ok, message = asyncio.run(control.set_identity_enabled(send_as_id, "false", source="test"))

        self.assertTrue(ok, message)
        self.assertFalse(state_module.get_identity_enabled(send_as_id))
        self.assertIn("暂停身份", message)

    def test_second_soul_purge_threshold_ui_validates_and_persists(self):
        send_as_id = 990302
        state_module.ensure_identity_registered(send_as_id)

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message = asyncio.run(
                ui.ui_set_second_soul_choice_config(send_as_id, purge_threshold="60")
            )
            invalid_ok, invalid_message = asyncio.run(
                ui.ui_set_second_soul_choice_config(send_as_id, purge_threshold="0")
            )

        self.assertTrue(ok, message)
        self.assertIn("自动镇魔阈值=60", message)
        self.assertFalse(invalid_ok)
        self.assertIn("1-100", invalid_message)
        with state_module.use_identity(send_as_id):
            self.assertEqual(60, state_module.state["second_soul_purge_threshold"])

    def test_direct_global_toggle_treats_form_false_string_as_disabled(self):
        state_module.set_global_enabled(True)
        for identity_id in (990311, 990312):
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, enabled=True)

        with patch.object(control, "save_state"), patch.object(control, "send_audit_log", new=AsyncMock()) as audit_mock:
            ok, message = asyncio.run(control.toggle_global_enabled("off", source="test"))

        self.assertTrue(ok, message)
        self.assertFalse(state_module.get_global_enabled())
        self.assertIn("全局暂停", message)
        self.assertEqual("high", audit_mock.await_args.kwargs["priority"])

    def test_manual_resume_is_rejected_while_bot_health_pause_is_active(self):
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("bot_health_monitor")
        with (
            patch.object(control, "should_pause_for_bot_health", return_value=True),
            patch.object(control, "save_state") as save_mock,
            patch.object(control, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            ok, message = asyncio.run(control.toggle_global_enabled(True, source="ui"))

        self.assertFalse(ok)
        self.assertIn("健康暂停中", message)
        self.assertFalse(state_module.get_global_enabled())
        save_mock.assert_not_called()
        audit_mock.assert_not_awaited()

    def test_global_resume_resets_safety_watchdog_marker(self):
        state_module.set_global_enabled(False)
        state_module.ensure_identity_registered(990313)
        state_module.update_send_as_profile(990313, enabled=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            fused_marker = state_dir / "safety_watchdog_fused.json"
            reset_marker = state_dir / "safety_watchdog_reset.json"
            fused_marker.write_text(json.dumps({"reason": "old"}), encoding="utf-8")

            with patch.object(control, "STATE_DIR", str(state_dir)), \
                 patch.object(control, "save_state"), \
                 patch.object(control, "send_audit_log", new=AsyncMock()):
                ok, message = asyncio.run(control.toggle_global_enabled(True, source="test"))

            self.assertTrue(ok, message)
            self.assertTrue(state_module.get_global_enabled())
            self.assertFalse(fused_marker.exists())
            payload = json.loads(reset_marker.read_text(encoding="utf-8"))
            self.assertGreater(payload["reset_at_epoch"], 0)

    def test_bot_health_global_resume_sets_recovery_hold_and_throttle(self):
        now = 1_700_000_000.0
        state_module.set_global_enabled(False)
        state_module.set_global_recovery_hold_until(0)
        state_module.set_global_recovery_throttle_until(0)
        state_module.ensure_identity_registered(990316)
        state_module.update_send_as_profile(990316, enabled=True)

        with (
            patch.object(control.time, "time", return_value=now),
            patch.object(control, "save_state"),
            patch.object(control, "send_audit_log", new=AsyncMock()),
            patch.object(control, "clear_transient_send_failures_for_global_recovery") as clear_mock,
            patch.object(control, "spread_overdue_runtime_timers") as spread_mock,
        ):
            ok, message = asyncio.run(control.toggle_global_enabled(True, source="bot_health_recovery"))

        self.assertTrue(ok, message)
        self.assertEqual(now + control.BOT_HEALTH_RECOVERY_HOLD_SEC, state_module.get_global_recovery_hold_until())
        self.assertEqual(now + control.BOT_HEALTH_RECOVERY_THROTTLE_SEC, state_module.get_global_recovery_throttle_until())
        clear_mock.assert_called_once_with(now)
        spread_mock.assert_called_once_with(now, reason="全局恢复", window_sec=control.RECOVERY_SPREAD_MAX_SEC)

    def test_recovery_throttle_covers_spread_window(self):
        self.assertGreaterEqual(
            control.BOT_HEALTH_RECOVERY_THROTTLE_SEC,
            control.RECOVERY_SPREAD_MAX_SEC + control.RECOVERY_THROTTLE_BUFFER_SEC,
        )

    def test_startup_recovery_extends_active_throttle_for_new_spread(self):
        now = 1_700_000_000.0
        state_module.set_global_recovery_throttle_until(now + 60)

        with (
            patch.object(control.time, "time", return_value=now),
            patch.object(control, "console_log") as log_mock,
            patch.object(control, "mark_dirty") as dirty_mock,
        ):
            changed = control.extend_global_recovery_throttle_for_spread(now, reason="启动恢复")

        self.assertTrue(changed)
        self.assertEqual(
            now + control.RECOVERY_SPREAD_MAX_SEC + control.RECOVERY_THROTTLE_BUFFER_SEC,
            state_module.get_global_recovery_throttle_until(),
        )
        dirty_mock.assert_called_once()
        log_mock.assert_called_once()

    def test_startup_recovery_does_not_create_throttle_when_inactive(self):
        now = 1_700_000_000.0
        state_module.set_global_recovery_throttle_until(0)

        with patch.object(control, "mark_dirty") as dirty_mock:
            changed = control.extend_global_recovery_throttle_for_spread(now, reason="启动恢复")

        self.assertFalse(changed)
        self.assertEqual(0, state_module.get_global_recovery_throttle_until())
        dirty_mock.assert_not_called()

    def test_channel_recovery_can_create_throttle_when_inactive(self):
        now = 1_700_000_000.0
        state_module.set_global_recovery_throttle_until(0)

        with (
            patch.object(control, "console_log") as log_mock,
            patch.object(control, "mark_dirty") as dirty_mock,
        ):
            changed = control.extend_global_recovery_throttle_for_spread(
                now,
                reason="频道身份恢复",
                activate_if_missing=True,
            )

        self.assertTrue(changed)
        self.assertEqual(
            now + control.RECOVERY_SPREAD_MAX_SEC + control.RECOVERY_THROTTLE_BUFFER_SEC,
            state_module.get_global_recovery_throttle_until(),
        )
        dirty_mock.assert_called_once()
        log_mock.assert_called_once()

    def test_manual_global_resume_clears_recovery_hold_and_throttle(self):
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("ui")
        state_module.set_global_recovery_hold_until(1_700_000_180.0)
        state_module.set_global_recovery_throttle_until(1_700_000_900.0)
        state_module.ensure_identity_registered(990317)
        state_module.update_send_as_profile(990317, enabled=True)

        with (
            patch.object(control, "save_state"),
            patch.object(control, "send_audit_log", new=AsyncMock()),
            patch.object(control, "clear_transient_send_failures_for_global_recovery"),
            patch.object(control, "spread_overdue_runtime_timers"),
        ):
            ok, message = asyncio.run(control.toggle_global_enabled(True, source="ui"))

        self.assertTrue(ok, message)
        self.assertEqual(0, state_module.get_global_recovery_hold_until())
        self.assertEqual(0, state_module.get_global_recovery_throttle_until())

    def test_manual_resume_after_safety_watchdog_uses_recovery_ramp(self):
        now = 1_700_000_000.0
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("safety_watchdog")
        state_module.set_global_recovery_hold_until(0)
        state_module.set_global_recovery_throttle_until(0)
        state_module.ensure_identity_registered(990318)
        state_module.update_send_as_profile(990318, enabled=True)

        with (
            patch.object(control.time, "time", return_value=now),
            patch.object(control, "save_state"),
            patch.object(control, "send_audit_log", new=AsyncMock()),
            patch.object(control, "clear_transient_send_failures_for_global_recovery"),
            patch.object(control, "spread_overdue_runtime_timers") as spread_mock,
        ):
            ok, message = asyncio.run(control.toggle_global_enabled(True, source="ui"))

        self.assertTrue(ok, message)
        self.assertEqual(now + control.BOT_HEALTH_RECOVERY_HOLD_SEC, state_module.get_global_recovery_hold_until())
        self.assertEqual(now + control.BOT_HEALTH_RECOVERY_THROTTLE_SEC, state_module.get_global_recovery_throttle_until())
        spread_mock.assert_called_once_with(now, reason="全局恢复", window_sec=control.RECOVERY_SPREAD_MAX_SEC)

    def test_manual_resume_with_safety_marker_uses_recovery_ramp_even_if_source_missing(self):
        now = 1_700_000_000.0
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("")
        state_module.set_global_recovery_hold_until(0)
        state_module.set_global_recovery_throttle_until(0)
        state_module.ensure_identity_registered(990319)
        state_module.update_send_as_profile(990319, enabled=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            (state_dir / "safety_watchdog_fused.json").write_text(
                json.dumps({"reason": "send burst"}),
                encoding="utf-8",
            )

            with (
                patch.object(control.time, "time", return_value=now),
                patch.object(control, "STATE_DIR", str(state_dir)),
                patch.object(control, "save_state"),
                patch.object(control, "send_audit_log", new=AsyncMock()),
                patch.object(control, "clear_transient_send_failures_for_global_recovery"),
                patch.object(control, "spread_overdue_runtime_timers") as spread_mock,
            ):
                ok, message = asyncio.run(control.toggle_global_enabled(True, source="log_group"))

        self.assertTrue(ok, message)
        self.assertEqual(now + control.BOT_HEALTH_RECOVERY_HOLD_SEC, state_module.get_global_recovery_hold_until())
        self.assertEqual(now + control.BOT_HEALTH_RECOVERY_THROTTLE_SEC, state_module.get_global_recovery_throttle_until())
        spread_mock.assert_called_once_with(now, reason="全局恢复", window_sec=control.RECOVERY_SPREAD_MAX_SEC)

    def test_global_resume_spreads_near_future_recovery_timers(self):
        now = 1_700_000_000.0
        send_as_id = 990314
        state_module.set_global_enabled(False)
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["concubine_tianji_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["next_concubine_time"] = 0

        with (
            patch.object(control.time, "time", return_value=now),
            patch.object(control.random, "uniform", return_value=600),
            patch.object(control, "save_state"),
            patch.object(control, "send_audit_log", new=AsyncMock()),
        ):
            ok, message = asyncio.run(control.toggle_global_enabled(True, source="test"))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertEqual(now + 600, state_module.state["next_concubine_time"])

    def test_global_resume_clears_transient_send_failures_before_spread(self):
        now = 1_700_000_000.0
        send_as_id = 990315
        state_module.set_global_enabled(False)
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "basket"
            state_module.state["fishing_reply_to_msg_id"] = 123
            state_module.state["fishing_reply_due_at"] = now - 10
            state_module.state["fishing_last_error"] = "发送失败：.鱼篓"
            state_module.state["next_fishing_time"] = now - 1

        with (
            patch.object(control.time, "time", return_value=now),
            patch.object(control.random, "uniform", return_value=600),
            patch.object(control, "save_state"),
            patch.object(control, "console_log"),
            patch.object(control, "send_audit_log", new=AsyncMock()),
        ):
            ok, message = asyncio.run(control.toggle_global_enabled(True, source="test"))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertEqual("", state_module.state["fishing_last_error"])
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual(now + 600, state_module.state["next_fishing_time"])

    def test_global_resume_clears_stale_pending_without_resend(self):
        now = 1_700_000_000.0
        send_as_id = 990320
        state_module.set_global_enabled(False)
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["pending_tasks"] = {
                771: {
                    "cmd": ".抚摸法宝 青竹蜂云剑（金雷竹·庚金相）",
                    "sent_at": now - 120,
                    "timeout": 30,
                    "retry": 0,
                    "max_retry": 1,
                    "source_module": "法宝",
                    "priority": "normal",
                }
            }

        with (
            patch.object(control.time, "time", return_value=now),
            patch.object(control, "save_state"),
            patch.object(control, "console_log"),
            patch.object(control, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(control, "send_audit_log", new=AsyncMock()),
        ):
            ok, message = asyncio.run(control.toggle_global_enabled(True, source="ui"))

        self.assertTrue(ok, message)
        send_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual({}, state_module.state["pending_tasks"])

    def test_global_recovery_clears_stale_small_world_query_runtime(self):
        now = 1_700_000_000.0
        send_as_id = 990323
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["small_world_phase"] = "query_pending"
            state_module.state["small_world_query_msg_id"] = 773
            state_module.state["next_small_world_time"] = 0
            state_module.state["pending_tasks"] = {
                773: {
                    "cmd": config.CMD_SMALL_WORLD_QUERY,
                    "sent_at": now - 120,
                    "timeout": 30,
                    "retry": 0,
                    "max_retry": 0,
                    "source_module": "小世界",
                    "priority": "chain",
                }
            }

        with (
            patch.object(control.random, "uniform", return_value=600),
            patch.object(control, "mark_dirty") as dirty_mock,
            patch.object(control, "console_log"),
        ):
            changed = control.clear_transient_send_failures_for_global_recovery(now)

        self.assertEqual(1, changed)
        dirty_mock.assert_called()
        with state_module.use_identity(send_as_id):
            self.assertEqual({}, state_module.state["pending_tasks"])
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_query_msg_id"])
            self.assertEqual(now + 600, state_module.state["next_small_world_time"])
            self.assertIn("全局恢复清理旧小世界面板", state_module.state["small_world_last_error"])

    def test_global_recovery_clears_stale_tianti_status_runtime(self):
        now = 1_700_000_000.0
        send_as_id = 990324
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["tianti_status_reply_to_msg_id"] = 774
            state_module.state["tianti_last_status_msg_id"] = 760
            state_module.state["next_tianti_status_time"] = 0
            state_module.state["pending_tasks"] = {
                774: {
                    "cmd": config.CMD_TIANTI_STATUS,
                    "sent_at": now - 120,
                    "timeout": 30,
                    "retry": 0,
                    "max_retry": 1,
                    "source_module": "登天阶",
                    "priority": "normal",
                }
            }

        with (
            patch.object(control.random, "uniform", return_value=600),
            patch.object(control, "mark_dirty") as dirty_mock,
            patch.object(control, "console_log"),
        ):
            changed = control.clear_transient_send_failures_for_global_recovery(now)

        self.assertEqual(1, changed)
        dirty_mock.assert_called()
        with state_module.use_identity(send_as_id):
            self.assertEqual({}, state_module.state["pending_tasks"])
            self.assertEqual(0, state_module.state["tianti_status_reply_to_msg_id"])
            self.assertEqual(760, state_module.state["tianti_last_status_msg_id"])
            self.assertEqual(now + 600, state_module.state["next_tianti_status_time"])
            self.assertIn("全局恢复清理旧天阶状态", state_module.state["tianti_last_error"])

    def test_global_recovery_reconciles_stale_tianxing_pending_before_drop(self):
        now = 1_700_000_000.0
        send_as_id = 990325
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["pending_tasks"] = {
                775: {
                    "cmd": config.CMD_TIANXING_PREDICT,
                    "sent_at": now - 120,
                    "timeout": 30,
                    "retry": 0,
                    "max_retry": 0,
                    "source_module": "天星宗",
                    "priority": "chain",
                }
            }

        with (
            patch.object(control, "mark_dirty"),
            patch.object(control, "console_log"),
            patch("model.features.tianxing.reconcile_tianxing_timeout_from_pending", return_value=True) as reconcile_mock,
        ):
            changed = control.clear_transient_send_failures_for_global_recovery(now)

        self.assertEqual(1, changed)
        reconcile_mock.assert_called_once_with(775, cmd=config.CMD_TIANXING_PREDICT, now=now)
        with state_module.use_identity(send_as_id):
            self.assertEqual({}, state_module.state["pending_tasks"])

    def test_global_recovery_reconciles_stale_hehuan_pending_before_drop(self):
        now = 1_700_000_000.0
        send_as_id = 990326
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["pending_tasks"] = {
                776: {
                    "cmd": f"{config.CMD_HEHUAN_DUAL} 温养",
                    "sent_at": now - 120,
                    "timeout": 30,
                    "retry": 0,
                    "max_retry": 0,
                    "source_module": "合欢宗",
                    "priority": "normal",
                }
            }

        with (
            patch.object(control, "mark_dirty"),
            patch.object(control, "console_log"),
            patch("model.features.hehuan.reconcile_hehuan_timeout_from_pending", return_value=True) as reconcile_mock,
        ):
            changed = control.clear_transient_send_failures_for_global_recovery(now)

        self.assertEqual(1, changed)
        reconcile_mock.assert_called_once_with(776, now=now)
        with state_module.use_identity(send_as_id):
            self.assertEqual({}, state_module.state["pending_tasks"])

    def test_global_recovery_keeps_fresh_pending(self):
        now = 1_700_000_000.0
        send_as_id = 990322
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["pending_tasks"] = {
                772: {
                    "cmd": ".天机盘",
                    "sent_at": now - 20,
                    "timeout": 60,
                    "retry": 0,
                    "max_retry": 1,
                    "source_module": "天星宗",
                    "priority": "reactive",
                }
            }

        with (
            patch.object(control, "mark_dirty") as dirty_mock,
            patch.object(control, "console_log"),
        ):
            changed = control.clear_transient_send_failures_for_global_recovery(now)

        self.assertEqual(0, changed)
        dirty_mock.assert_not_called()
        with state_module.use_identity(send_as_id):
            self.assertIn(772, state_module.state["pending_tasks"])

    def test_direct_identity_module_toggle_treats_form_false_string_as_disabled(self):
        send_as_id = 990321
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["checkin_enabled"] = True

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("点卯", "false", send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["checkin_enabled"])

    def test_concubine_voyage_available_for_non_xinggong_identity(self):
        send_as_id = 990322
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="落云宗", realm="结丹后期")

        modules = state_module.get_available_module_names(send_as_id)

        self.assertIn("侍妾远航", modules)
        self.assertIn("共历心劫", modules)
        self.assertNotIn("灵树", modules)

    def test_wendao_only_available_for_yuanying_sect_identity(self):
        cases = (
            ("unknown_sect", "", False),
            ("wrong_sect", "落云宗", False),
            ("yuanying_sect", "元婴宗", True),
        )

        for offset, (label, sect_name, expected_available) in enumerate(cases):
            with self.subTest(label=label):
                send_as_id = 990330 + offset
                state_module.ensure_identity_registered(send_as_id)
                state_module.update_send_as_profile(send_as_id, sect_name=sect_name, realm="元婴初期")

                modules = state_module.get_available_module_names(send_as_id)

                self.assertEqual(expected_available, "问道" in modules)

    def test_no_sect_and_sanxiu_hide_all_sect_dependent_modules(self):
        blocked_modules = {
            "点卯",
            "宗门传功",
            "闯塔",
            "灵树",
            "观星台",
            "观星",
            "周天星斗",
            "登天阶",
            "太一",
            "放养",
            "合欢宗",
            "天星宗",
            "阴罗宗",
            "问道",
        }
        for offset, sect_name in enumerate(("", "散修")):
            with self.subTest(sect_name=sect_name or "empty"):
                send_as_id = 990360 + offset
                state_module.ensure_identity_registered(send_as_id)
                state_module.update_send_as_profile(send_as_id, sect_name=sect_name, realm="元婴初期")

                modules = set(state_module.get_available_module_names(send_as_id))

                self.assertFalse(blocked_modules & modules)
                self.assertIn("野外历练", modules)
                self.assertIn("深度闭关", modules)

    def test_wendao_toggle_rejected_for_non_yuanying_sect(self):
        send_as_id = 990332
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="落云宗", realm="元婴初期")

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("问道", True, send_as_id=send_as_id))

        self.assertFalse(ok)
        self.assertIn("未提供问道模块", message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["wendao_enabled"])

    def test_luoyun_legacy_tree_module_is_archived(self):
        send_as_id = 990323
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="落云宗", realm="结丹后期")

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("灵树", True, send_as_id=send_as_id))

        self.assertFalse(ok)
        self.assertIn("灵树模块已归档", message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["tree_enabled"])

    def test_archived_tree_module_rejected_before_sect_check(self):
        send_as_id = 990333
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="星宫", realm="结丹后期")

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("灵树", True, send_as_id=send_as_id))

        self.assertFalse(ok)
        self.assertIn("灵树模块已归档", message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["tree_enabled"])

    def test_wanling_legacy_ranch_module_is_archived(self):
        send_as_id = 990334
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="万灵宗", realm="结丹后期")

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("放养", True, send_as_id=send_as_id))

        self.assertFalse(ok)
        self.assertIn("放养模块已归档", message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["ranch_enabled"])

    def test_sanxiu_rejects_checkin_and_sect_teach_enable(self):
        send_as_id = 990361
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="散修", realm="元婴初期")

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            checkin_ok, checkin_message = asyncio.run(control.set_module_enabled("点卯", True, send_as_id=send_as_id))
            teach_ok, teach_message = asyncio.run(control.set_module_enabled("宗门传功", True, send_as_id=send_as_id))

        self.assertFalse(checkin_ok)
        self.assertIn("当前身份无宗门", checkin_message)
        self.assertFalse(teach_ok)
        self.assertIn("当前身份无宗门", teach_message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["checkin_enabled"])
            self.assertFalse(state_module.state["sect_teach_enabled"])

    def test_ui_module_toggle_message_matches_coerced_false_string(self):
        send_as_id = 990331
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["checkin_enabled"] = True

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(ui.ui_set_module_enabled(send_as_id, "点卯", "false"))

        self.assertTrue(ok, message)
        self.assertIn("已关闭点卯", message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["checkin_enabled"])

    def test_direct_global_module_toggle_treats_form_false_string_as_disabled(self):
        state_module.set_guanxing_monitor_enabled(True)

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("观星监控", "0"))

        self.assertTrue(ok, message)
        self.assertFalse(state_module.get_guanxing_monitor_enabled())

    def test_disabling_checkin_does_not_clear_sect_teach_runtime(self):
        send_as_id = 990341
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["checkin_enabled"] = True
            state_module.state["sect_teach_enabled"] = True
            state_module.state["next_checkin_time"] = now + 100
            state_module.state["next_sect_teach_time"] = now + 200
            state_module.state["sect_teach_reply_to_msg_id"] = 101
            state_module.state["last_sect_teach_msg_id"] = 102
            state_module.state["pending_tasks"] = {
                201: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
                202: {"cmd": config.CMD_SECT_TEACH, "sent_at": now, "retry": 0},
            }

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("点卯", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["checkin_enabled"])
            self.assertTrue(state_module.state["sect_teach_enabled"])
            self.assertEqual(0, state_module.state["next_checkin_time"])
            self.assertEqual(now + 200, state_module.state["next_sect_teach_time"])
            self.assertEqual(101, state_module.state["sect_teach_reply_to_msg_id"])
            self.assertEqual(102, state_module.state["last_sect_teach_msg_id"])
            self.assertEqual({202: {"cmd": config.CMD_SECT_TEACH, "sent_at": now, "retry": 0}}, state_module.state["pending_tasks"])

    def test_disabling_sect_teach_does_not_clear_checkin_runtime(self):
        send_as_id = 990342
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["checkin_enabled"] = True
            state_module.state["sect_teach_enabled"] = True
            state_module.state["next_checkin_time"] = now + 100
            state_module.state["next_sect_teach_time"] = now + 200
            state_module.state["sect_teach_reply_to_msg_id"] = 101
            state_module.state["last_sect_teach_msg_id"] = 102
            state_module.state["pending_tasks"] = {
                201: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
                202: {"cmd": config.CMD_SECT_TEACH, "sent_at": now, "retry": 0},
            }

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("宗门传功", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertTrue(state_module.state["checkin_enabled"])
            self.assertFalse(state_module.state["sect_teach_enabled"])
            self.assertEqual(now + 100, state_module.state["next_checkin_time"])
            self.assertEqual(0, state_module.state["next_sect_teach_time"])
            self.assertEqual(0, state_module.state["sect_teach_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["last_sect_teach_msg_id"])
            self.assertEqual({201: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0}}, state_module.state["pending_tasks"])

    def test_disabling_tianxing_clears_observation_and_pending(self):
        send_as_id = 990344
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
        with state_module.use_identity(send_as_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {"last_observed_at": now, "last_error": "旧天星错误"}
            state_module.state["tianxing_timeline_state"] = {"phase": "sent_waiting_ack"}
            state_module.state["pending_tasks"] = {
                301: {"cmd": config.CMD_TIANXING_PANEL, "sent_at": now, "retry": 0},
                302: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
            }
            state_module.state["action_guard_sessions"] = {
                "tianxing_panel": {
                    "action_key": "tianxing_panel",
                    "attempt": 1,
                    "last_sent_at": now,
                    "last_msg_id": 301,
                    "last_command": config.CMD_TIANXING_PANEL,
                },
                "tianxing_set_star": {
                    "action_key": "tianxing_set_star",
                    "attempt": 1,
                    "last_sent_at": now,
                    "last_msg_id": 303,
                    "last_command": f"{config.CMD_TIANXING_SET_STAR} 贪狼",
                },
                "tianxing_retreat_farm": {
                    "action_key": "tianxing_retreat_farm",
                    "attempt": 1,
                    "last_sent_at": now,
                    "last_msg_id": 304,
                    "last_command": config.CMD_NORMAL_RETREAT,
                },
                "deep_retreat": {
                    "action_key": "deep_retreat",
                    "attempt": 1,
                    "last_sent_at": now,
                    "last_msg_id": 305,
                    "last_command": config.CMD_DEEP_RETREAT,
                },
            }

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("天星宗", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["tianxing_enabled"])
            self.assertEqual({}, state_module.state["tianxing_observation"])
            self.assertEqual({}, state_module.state["tianxing_timeline_state"])
            self.assertEqual({302: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0}}, state_module.state["pending_tasks"])
            sessions = state_module.state["action_guard_sessions"]
            self.assertNotIn("tianxing_panel", sessions)
            self.assertNotIn("tianxing_set_star", sessions)
            self.assertNotIn("tianxing_retreat_farm", sessions)
            self.assertIn("deep_retreat", sessions)

    def test_enabling_tianxing_resets_stale_action_guard_sessions(self):
        send_as_id = 990349
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
        with state_module.use_identity(send_as_id):
            state_module.state["tianxing_enabled"] = False
            state_module.state["action_guard_sessions"] = {
                "tianxing_panel": {
                    "action_key": "tianxing_panel",
                    "attempt": 1,
                    "last_sent_at": now,
                    "last_msg_id": 301,
                    "last_command": config.CMD_TIANXING_PANEL,
                },
                "tianxing_set_star": {
                    "action_key": "tianxing_set_star",
                    "attempt": 1,
                    "last_sent_at": now,
                    "last_msg_id": 302,
                    "last_command": f"{config.CMD_TIANXING_SET_STAR} 贪狼",
                },
                "deep_retreat": {
                    "action_key": "deep_retreat",
                    "attempt": 1,
                    "last_sent_at": now,
                    "last_msg_id": 303,
                    "last_command": config.CMD_DEEP_RETREAT,
                },
            }

        with patch.object(control.time, "time", return_value=now), \
             patch.object(control, "save_state"), \
             patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("天星宗", True, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertTrue(state_module.state["tianxing_enabled"])
            sessions = state_module.state["action_guard_sessions"]
            self.assertNotIn("tianxing_panel", sessions)
            self.assertNotIn("tianxing_set_star", sessions)
            self.assertIn("deep_retreat", sessions)

    def test_disabling_yinluo_clears_observation_and_pending(self):
        send_as_id = 990345
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="阴罗宗")
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {"last_observed_at": now, "last_error": "旧阴罗错误"}
            state_module.state["pending_tasks"] = {
                401: {"cmd": config.CMD_YINLUO_BANNER, "sent_at": now, "retry": 0},
                402: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
            }

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("阴罗宗", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["yinluo_enabled"])
            self.assertEqual({}, state_module.state["yinluo_observation"])
            self.assertEqual({402: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0}}, state_module.state["pending_tasks"])

    def test_startup_initialization_clears_disabled_passive_observations(self):
        send_as_id = 990346
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True)
        with state_module.use_identity(send_as_id):
            state_module.state["tianxing_enabled"] = False
            state_module.state["tianxing_observation"] = {"last_error": "旧天星错误"}
            state_module.state["yinluo_enabled"] = False
            state_module.state["yinluo_observation"] = {"last_error": "旧阴罗错误"}

        with patch.object(control, "mark_dirty") as mark_dirty_mock:
            control.initialize_identity_runtime(send_as_id, now=1_700_000_000.0)

        with state_module.use_identity(send_as_id):
            self.assertEqual({}, state_module.state["tianxing_observation"])
            self.assertEqual({}, state_module.state["yinluo_observation"])
        mark_dirty_mock.assert_called()

    def test_startup_retires_legacy_ranch_without_losing_pending_return(self):
        send_as_id = 990348
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, enabled=True, sect_name="万灵宗")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["next_ranch_time"] = now + 300
            state_module.state["ranch_reply_to_msg_id"] = 7788
            state_module.state["ranch_reply_due_at"] = now + 60
            state_module.state["ranch_return_pending"] = True
            state_module.state["ranch_return_wait_since"] = now - 120

        with patch.object(control, "mark_dirty"):
            control.initialize_identity_runtime(send_as_id, now=now)

        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertEqual(0, state_module.state["next_ranch_time"])
            self.assertEqual(0, state_module.state["ranch_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["ranch_reply_due_at"])
            self.assertTrue(state_module.state["ranch_return_pending"])
            self.assertEqual(now - 120, state_module.state["ranch_return_wait_since"])

    def test_startup_availability_clears_stale_sanxiu_sect_modules(self):
        send_as_id = 990347
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, sect_name="散修", realm="元婴初期")
        with state_module.use_identity(send_as_id):
            state_module.state["checkin_enabled"] = True
            state_module.state["sect_teach_enabled"] = True
            state_module.state["tower_enabled"] = True
            state_module.state["hehuan_enabled"] = True
            state_module.state["tianxing_enabled"] = True
            state_module.state["yinluo_enabled"] = True
            state_module.state["ranch_enabled"] = True
            state_module.state["next_checkin_time"] = now
            state_module.state["next_sect_teach_time"] = now
            state_module.state["next_tower_time"] = now
            state_module.state["hehuan_observation"] = {"last_error": "旧合欢"}
            state_module.state["tianxing_observation"] = {"last_error": "旧天星"}
            state_module.state["yinluo_observation"] = {"last_error": "旧阴罗"}
            state_module.state["pending_tasks"] = {
                501: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
                502: {"cmd": config.CMD_SECT_TEACH, "sent_at": now, "retry": 0},
                503: {"cmd": config.CMD_TOWER, "sent_at": now, "retry": 0},
            }

        with patch.object(control, "save_state") as save_mock:
            changed = control.enforce_identity_module_availability(send_as_id)

        self.assertTrue(changed)
        save_mock.assert_called_once()
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["checkin_enabled"])
            self.assertFalse(state_module.state["sect_teach_enabled"])
            self.assertFalse(state_module.state["tower_enabled"])
            self.assertFalse(state_module.state["hehuan_enabled"])
            self.assertFalse(state_module.state["tianxing_enabled"])
            self.assertFalse(state_module.state["yinluo_enabled"])
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertEqual({}, state_module.state["hehuan_observation"])
            self.assertEqual({}, state_module.state["tianxing_observation"])
            self.assertEqual({}, state_module.state["yinluo_observation"])
            self.assertEqual({}, state_module.state["pending_tasks"])

    def test_toggling_concubine_preserves_partner_snapshot(self):
        send_as_id = 990343
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_availability"] = "available"
            state_module.state["concubine_name"] = "银月"
            state_module.state["concubine_kind"] = "道心侍妾"
            state_module.state["concubine_affinity"] = 330
            state_module.state["concubine_dream_due_at"] = now + 3600
            state_module.state["concubine_fragment_xutian_count"] = 1
            state_module.state["concubine_fragment_cangkun_count"] = 3
            state_module.state["concubine_last_snapshot_at"] = now - 600
            state_module.state["concubine_status_msg_id"] = 123
            state_module.state["concubine_phase"] = "status_pending"

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("侍妾", False, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["concubine_enabled"])
            self.assertEqual("available", state_module.state["concubine_availability"])
            self.assertEqual("银月", state_module.state["concubine_name"])
            self.assertEqual("道心侍妾", state_module.state["concubine_kind"])
            self.assertEqual(330, state_module.state["concubine_affinity"])
            self.assertEqual(now + 3600, state_module.state["concubine_dream_due_at"])
            self.assertEqual(1, state_module.state["concubine_fragment_xutian_count"])
            self.assertEqual(3, state_module.state["concubine_fragment_cangkun_count"])
            self.assertEqual(now - 600, state_module.state["concubine_last_snapshot_at"])
            self.assertEqual(0, state_module.state["concubine_status_msg_id"])
            self.assertEqual("idle", state_module.state["concubine_phase"])

        with patch.object(control, "save_state"), patch.object(control, "console_log"):
            ok, message = asyncio.run(control.set_module_enabled("侍妾", True, send_as_id=send_as_id))

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            self.assertTrue(state_module.state["concubine_enabled"])
            self.assertEqual("available", state_module.state["concubine_availability"])
            self.assertEqual("银月", state_module.state["concubine_name"])
            self.assertEqual("道心侍妾", state_module.state["concubine_kind"])
            self.assertEqual(330, state_module.state["concubine_affinity"])
            self.assertEqual(now - 600, state_module.state["concubine_last_snapshot_at"])


if __name__ == "__main__":
    unittest.main()
