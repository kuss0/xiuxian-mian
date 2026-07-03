import copy
import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import action_guard, control, runtime, ui
from model.features import _phaseful, concubine, deep_retreat, tianxing, tower, yuanying


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._summary_consumed_commands_snapshot = copy.deepcopy(_phaseful._SUMMARY_CONSUMED_COMMANDS)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        _phaseful._SUMMARY_CONSUMED_COMMANDS.clear()

    def tearDown(self):
        _phaseful._SUMMARY_CONSUMED_COMMANDS.clear()
        _phaseful._SUMMARY_CONSUMED_COMMANDS.update(copy.deepcopy(self._summary_consumed_commands_snapshot))
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class PhasefulSummaryTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def _prepare_identity(self, send_as_id, username):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username=username)

    def test_post_summary_wait_exceeds_same_command_guard_window(self):
        self.assertGreater(
            deep_retreat.DEEP_RETREAT_SPEC.post_summary_wait_sec,
            action_guard.POST_CLOSE_REPEAT_GUARD_SEC,
        )
        self.assertGreater(
            yuanying.YUANYING_SPEC.post_summary_wait_sec,
            action_guard.POST_CLOSE_REPEAT_GUARD_SEC,
        )

    def _active_tianxing_farm_config(self, now):
        local_time = time.localtime(now)
        return {
            "timeline_enabled": True,
            "timeline_dry_run_enabled": False,
            "auto_predict_enabled": True,
            "auto_change_fate_enabled": False,
            "farm_window_enabled": True,
            "farm_window_start": f"{local_time.tm_hour:02d}:{local_time.tm_min:02d}",
            "farm_window_duration_min": 5,
        }

    def test_passive_summary_trigger_reply_context_uses_abs_tracked_id(self):
        send_as_id = 8659059210
        self._prepare_identity(send_as_id, "PassiveTrackedRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["last_deep_retreat_summary_msg_id"] = -901

        context = runtime.get_reply_context(reply_to_msg_id=901, send_as_id=send_as_id)

        self.assertEqual(send_as_id, context["send_as_id"])
        self.assertEqual("deep_retreat", context["family"])

    async def test_deep_retreat_direct_summary_finalizes_wait(self):
        send_as_id = 8659059191
        now = 1_700_000_000.0
        self._prepare_identity(send_as_id, "Shadow_Plus")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 10
            state_module.state["last_deep_retreat_summary_msg_id"] = 123
        action_guard.note_sent(deep_retreat.CMD_DEEP_RETREAT, send_as_id, 456, sent_at=now - 300)
        self.assertTrue(action_guard.note_remote_block(
            "deep_retreat",
            send_as_id=send_as_id,
            block_until=now + 7200,
            reason="游戏提示深度闭关执行中",
            kind="running",
            now=now - 299,
            command=deep_retreat.CMD_DEEP_RETREAT,
        ))

        text = (
            "📜 修士 @Shadow_Plus 深度闭关总结\n"
            "【深度闭关总结】\n"
            "本次结算时长: 8.0 小时 (基础上限8小时)\n"
            "神魂吐纳次数: 32 周天\n"
            "本次深度闭关，你的修为最终变化了 26682 点！"
        )

        with (
            patch.object(deep_retreat, "save_state"),
            patch.object(deep_retreat, "console_log"),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(text, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(0, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertNotIn("deep_retreat", state_module.state["action_guard_sessions"])
        allowed, reason = action_guard.before_send(deep_retreat.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now + 31)
        self.assertTrue(allowed, reason)

    async def test_deep_retreat_broadcast_ignores_non_summary_text(self):
        send_as_id = 8659059211
        now = 1_700_000_010.0
        self._prepare_identity(send_as_id, "jihejish")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 10

        text = (
            "【野外历练 · 灵机暗藏】\n"
            "@jihejish 在山涧残阵旁避开妖兽踪迹，采得一份机缘。\n"
            "获得修为 +2551，获得 【养魂木】x1。"
        )

        with (
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(text, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
        audit_mock.assert_not_awaited()
        inbox_mock.assert_not_called()

    async def test_deep_retreat_running_near_due_completion_notice_finalizes(self):
        send_as_id = 8659059204
        now = 1_700_000_020.0
        self._prepare_identity(send_as_id, "Shadow_Plus")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "running"
            state_module.state["next_deep_retreat_time"] = now + 120

        text = "✨ 天道感应：检测到 @Shadow_Plus 功成圆满，神魂正在归位..."

        with (
            patch.object(deep_retreat, "console_log"),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(text, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])

    async def test_deep_retreat_running_explicit_completion_notice_finalizes_even_if_local_timer_late(self):
        send_as_id = 8659059205
        now = 1_700_000_030.0
        self._prepare_identity(send_as_id, "Shadow_Plus")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "running"
            state_module.state["next_deep_retreat_time"] = now + 3600

        text = "✨ 天道感应：检测到 @Shadow_Plus 功成圆满，神魂正在归位..."

        with (
            patch.object(deep_retreat, "console_log"),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(text, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])

    async def test_deep_retreat_full_summary_after_completion_notice_is_archived(self):
        send_as_id = 8659059222
        now = 1_700_000_035.0
        self._prepare_identity(send_as_id, "myios17")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["next_deep_retreat_time"] = now + 30

        text = (
            "📜 修士 @myios17 深度闭关总结\n"
            "【深度闭关总结】\n"
            "本次结算时长: 8.0 小时 (基础上限8小时)\n"
            "神魂吐纳次数: 32 周天\n\n"
            "- 修行有成: 20 次\n"
            "- 心神不宁: 11 次\n"
            "- 走火入魔: 1 次\n"
            "本次深度闭关，你的修为最终变化了 6687 点！"
        )

        with (
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(text, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
        audit_mock.assert_not_awaited()
        self.assertTrue(any(
            call.kwargs.get("reason") == "no_change"
            and call.kwargs.get("decision") == "summary_already_finalized"
            and call.kwargs.get("identity_id") == send_as_id
            for call in inbox_mock.call_args_list
        ))

    async def test_deep_retreat_tagless_far_future_running_summary_is_ignored(self):
        send_as_id = 8659059208
        now = 1_700_000_040.0
        self._prepare_identity(send_as_id, "Shadow_Plus")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "running"
            state_module.state["next_deep_retreat_time"] = now + 3600

        text = "【深度闭关总结】\n本次结算时长: 5.3 小时\n神魂吐纳次数: 21 周天"

        with (
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(text, now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
        audit_mock.assert_not_awaited()
        self.assertTrue(any(
            call.kwargs.get("reason") == "deep_retreat_summary_no_match"
            and call.kwargs.get("decision") == "summary_no_match_skip"
            and call.kwargs.get("include_recent") is False
            for call in inbox_mock.call_args_list
        ))

    async def test_deep_retreat_tagless_force_exit_summary_uses_reply_context_identity(self):
        send_as_id = 8659059192
        now = 1_700_000_100.0
        self._prepare_identity(send_as_id, "NoAtRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 10

        text = (
            "【深度闭关总结】\n"
            "本次结算时长: 3.1 小时 (基础上限8小时)\n"
            "神魂吐纳次数: 12 周天\n"
            "【强行出关惩罚】: 因你强行中断修行，所得感悟流失大半。"
        )

        with (
            patch.object(deep_retreat, "save_state"),
            patch.object(deep_retreat, "console_log"),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(
                text,
                now,
                reply_context={"send_as_id": send_as_id, "family": "deep_retreat", "reply_to_msg_id": 9545414},
            )

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])

    async def test_deep_retreat_force_exit_summary_updates_tianxing_retreat_farm_cooldown(self):
        send_as_id = 8659059244
        now = 1_700_000_101.0
        self._prepare_identity(send_as_id, "TianxingForceExitRetreat")

        with state_module.use_identity(send_as_id):
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 10
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 30,
                    "last_action": "force_exit",
                    "last_command": ".强行出关",
                    "target_tianji": 42,
                }
            }

        text = (
            "【深度闭关总结】\n"
            "本次结算时长: 85.7 小时 (化身护法，上限100小时)\n"
            "神魂吐纳次数: 342 周天\n\n"
            "【强行出关惩罚】: 因你强行中断修行，所得感悟流失大半。\n"
            "你的神魂因中断修行而震荡不休，需调息40分钟方可进行下一次【闭关修炼】。"
        )

        with (
            patch.object(deep_retreat, "save_state"),
            patch.object(deep_retreat, "console_log"),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(
                text,
                now,
                reply_context={"send_as_id": send_as_id, "family": "deep_retreat", "reply_to_msg_id": 9545415},
            )

        with state_module.use_identity(send_as_id):
            farm = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual("cooldown", farm["phase"])
            self.assertGreaterEqual(farm["next_time"], now + 40 * 60)

    async def test_deep_retreat_tagless_force_exit_summary_skips_mismatched_reply_context(self):
        waiting_id = 8659059220
        other_id = 8659059221
        now = 1_700_000_110.0
        self._prepare_identity(waiting_id, "WaitingRetreat")
        self._prepare_identity(other_id, "OtherRetreat")

        with state_module.use_identity(waiting_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 10
        with state_module.use_identity(other_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "idle"

        text = (
            "【深度闭关总结】\n"
            "本次结算时长: 5.5 小时 (基础上限8小时)\n"
            "神魂吐纳次数: 21 周天\n"
            "【强行出关惩罚】: 因你强行中断修行，所得感悟流失大半。"
        )

        with (
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(
                text,
                now,
                reply_context={"send_as_id": other_id, "family": "deep_retreat", "reply_to_msg_id": 9545414},
            )

        with state_module.use_identity(waiting_id):
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
        with state_module.use_identity(other_id):
            self.assertEqual("idle", state_module.state["deep_retreat_phase"])
        audit_mock.assert_not_awaited()
        self.assertTrue(any(
            call.kwargs.get("reason") == "deep_retreat_summary_no_match"
            and call.kwargs.get("decision") == "summary_no_match_skip"
            for call in inbox_mock.call_args_list
        ))

    async def test_deep_retreat_tagless_summary_skips_multiple_candidates(self):
        first_id = 8659059193
        second_id = 8659059194
        now = 1_700_000_200.0
        self._prepare_identity(first_id, "FirstRetreat")
        self._prepare_identity(second_id, "SecondRetreat")

        for send_as_id in (first_id, second_id):
            with state_module.use_identity(send_as_id):
                state_module.state["deep_retreat_enabled"] = True
                state_module.state["deep_retreat_phase"] = "waiting_summary"
                state_module.state["deep_retreat_summary_sent_at"] = now - 10

        text = "【深度闭关总结】\n本次结算时长: 3.1 小时\n神魂吐纳次数: 12 周天"

        with (
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(text, now)

        with state_module.use_identity(first_id):
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
        with state_module.use_identity(second_id):
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
        audit_mock.assert_not_awaited()
        self.assertTrue(any(
            call.kwargs.get("reason") == "deep_retreat_summary_no_match"
            and call.kwargs.get("decision") == "summary_no_match_skip"
            and "深度闭关总结" in str(call.kwargs.get("matched_text") or "")
            for call in inbox_mock.call_args_list
        ))

    async def test_deep_retreat_tagless_near_due_running_summary_skips_multiple_candidates(self):
        first_id = 8659059206
        second_id = 8659059207
        now = 1_700_000_220.0
        self._prepare_identity(first_id, "FirstRetreat")
        self._prepare_identity(second_id, "SecondRetreat")

        for send_as_id in (first_id, second_id):
            with state_module.use_identity(send_as_id):
                state_module.state["deep_retreat_enabled"] = True
                state_module.state["deep_retreat_phase"] = "running"
                state_module.state["next_deep_retreat_time"] = now + 120

        text = "【深度闭关总结】\n本次结算时长: 5.3 小时\n神魂吐纳次数: 21 周天"

        with (
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(text, now)

        with state_module.use_identity(first_id):
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
        with state_module.use_identity(second_id):
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
        audit_mock.assert_not_awaited()
        self.assertTrue(any(
            call.kwargs.get("reason") == "deep_retreat_summary_no_match"
            and call.kwargs.get("decision") == "summary_no_match_skip"
            for call in inbox_mock.call_args_list
        ))

    async def test_deep_retreat_short_cooldown_reply_updates_next_time(self):
        send_as_id = 8659059200
        now = 1_700_000_250.0
        self._prepare_identity(send_as_id, "ShortRetreatCd")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "launching"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["last_deep_retreat_command_time"] = now - 5
            state_module.state["next_deep_retreat_time"] = now + deep_retreat.DEEP_RETREAT_CD

            with (
                patch.object(deep_retreat, "save_state"),
                patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
            ):
                handled = await deep_retreat.handle_deep_retreat_status_reply(
                    "灵气尚未平复，无法立即再次闭关。请在 5秒 后再试。",
                    now,
                    reply_to=SimpleNamespace(raw_text=deep_retreat.CMD_DEEP_RETREAT),
                    matched_family="deep_retreat",
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["deep_retreat_phase"])
            self.assertFalse(state_module.state["deep_retreat_probe_pending"])
            self.assertEqual(
                now + 5 + deep_retreat.CD_BUFFER_SEC,
                state_module.state["next_deep_retreat_time"],
            )
            allowed, reason = action_guard.before_send(deep_retreat.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now + 1)
            self.assertFalse(allowed)
            self.assertIn("短冷却", reason)
            audit_mock.assert_awaited_once()
            self.assertTrue(any(
                call.kwargs.get("family") == "deep_retreat"
                and call.kwargs.get("decision") == "short_cd_rescheduled"
                and "灵气尚未平复" in str(call.kwargs.get("matched_text") or "")
                for call in inbox_mock.call_args_list
            ))

    async def test_deep_retreat_passive_trigger_status_reply_confirms_not_running(self):
        send_as_id = 8659059209
        now = 1_700_000_270.0
        self._prepare_identity(send_as_id, "PassiveStatusRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 130
            state_module.state["last_deep_retreat_summary_msg_id"] = -901

            with (
                patch.object(deep_retreat.random, "uniform", return_value=12),
                patch.object(deep_retreat, "delete_deep_retreat_summary_trigger_msg", new=AsyncMock()),
                patch.object(deep_retreat, "save_state"),
                patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
            ):
                handled = await deep_retreat.handle_deep_retreat_status_reply(
                    "你并未处于深度闭关之中。",
                    now,
                    reply_to=SimpleNamespace(raw_text="1"),
                    matched_family=None,
                )

            self.assertTrue(handled)
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 12, state_module.state["next_deep_retreat_time"])
            self.assertTrue(state_module.state["deep_retreat_probe_pending"])
            self.assertEqual(now, state_module.state["last_deep_retreat_command_time"])
            audit_mock.assert_awaited_once()
            self.assertTrue(any(
                call.kwargs.get("family") == "deep_retreat"
                and call.kwargs.get("decision") == "not_running_relaunch_soon"
                and "你并未处于深度闭关" in str(call.kwargs.get("matched_text") or "")
                for call in inbox_mock.call_args_list
            ))

    async def test_deep_retreat_start_reply_sets_exact_eight_hour_cd(self):
        send_as_id = 8659059218
        now = 1_700_000_280.0
        self._prepare_identity(send_as_id, "DeepRetreatStartsAfterStatus")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "launching"
            state_module.state["last_deep_retreat_command_time"] = now - 5

            with (
                patch.object(deep_retreat, "save_state"),
                patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
                patch("model.features.passive_inbox.record_passive_inbox_event"),
            ):
                handled = await deep_retreat.handle_deep_retreat_success_reply(
                    "你已进入深度闭关状态，神魂将自行吐纳 8 小时。\n期间你将无法进行大部分操作。下次发言时将自动结算本次闭关的收获。",
                    now,
                    reply_to=SimpleNamespace(raw_text=deep_retreat.CMD_DEEP_RETREAT),
                    matched_family="deep_retreat",
                )

            self.assertTrue(handled)
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 8 * 60 * 60 + deep_retreat.CD_BUFFER_SEC, state_module.state["next_deep_retreat_time"])
            allowed, reason = action_guard.before_send(deep_retreat.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now + 10)
            self.assertFalse(allowed)
            self.assertIn("执行中", reason)

    async def test_deep_retreat_start_reply_uses_observed_long_duration(self):
        send_as_id = 8659059234
        now = 1_700_000_285.0
        self._prepare_identity(send_as_id, "DeepRetreatLongDuration")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "launching"
            state_module.state["last_deep_retreat_command_time"] = now - 5

            with (
                patch.object(deep_retreat, "save_state"),
                patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
                patch("model.features.passive_inbox.record_passive_inbox_event"),
            ):
                handled = await deep_retreat.handle_deep_retreat_success_reply(
                    "你已进入深度闭关状态，神魂将自行吐纳 100 小时。\n期间你将无法进行大部分操作。下次发言时将自动结算本次闭关的收获。",
                    now,
                    reply_to=SimpleNamespace(raw_text=deep_retreat.CMD_DEEP_RETREAT),
                    matched_family="deep_retreat",
                )

            self.assertTrue(handled)
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 100 * 60 * 60 + deep_retreat.CD_BUFFER_SEC, state_module.state["next_deep_retreat_time"])

    async def test_yuanying_direct_summary_finalizes_wait(self):
        send_as_id = 8659059195
        now = 1_700_000_300.0
        self._prepare_identity(send_as_id, "Shadow_Plus")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "waiting_summary"
            state_module.state["yuanying_summary_sent_at"] = now - 10
            state_module.state["last_yuanying_summary_msg_id"] = 456

        text = (
            "📜 修士 @Shadow_Plus 元神归窍总结\n"
            "你的元婴在虚空中神游八小时，带回了以下收获：\n"
            "元婴成长:\n - 获得了 800 点经验。"
        )

        with (
            patch.object(yuanying, "save_state"),
            patch.object(yuanying, "console_log"),
            patch.object(yuanying, "send_audit_log", new=AsyncMock()),
        ):
            await yuanying.handle_yuanying_summary_broadcast(
                text,
                now,
                reply_context={"send_as_id": send_as_id, "family": "yuanying", "reply_to_msg_id": 9544658},
            )

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["yuanying_phase"])
            self.assertEqual(0, state_module.state["last_yuanying_summary_msg_id"])

    async def test_yuanying_closure_summary_variant_finalizes_wait(self):
        send_as_id = 8659059199
        now = 1_700_000_350.0
        self._prepare_identity(send_as_id, "Shadow_Plus")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "waiting_summary"
            state_module.state["yuanying_summary_sent_at"] = now - 10
            state_module.state["last_yuanying_summary_msg_id"] = 457

        text = (
            "【元婴闭关结算】\n"
            "你的元婴在过去 8 小时内为你增加了 11200 点修为！\n"
            "本次元婴闭关已结束。"
        )

        with (
            patch.object(yuanying, "save_state"),
            patch.object(yuanying, "console_log"),
            patch.object(yuanying, "send_audit_log", new=AsyncMock()),
        ):
            await yuanying.handle_yuanying_summary_broadcast(
                text,
                now,
                reply_context={"send_as_id": send_as_id, "family": "yuanying", "reply_to_msg_id": 9544658},
            )

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["yuanying_phase"])
            self.assertEqual(0, state_module.state["last_yuanying_summary_msg_id"])

    async def test_yuanying_running_reply_accepts_retreat_task_variant(self):
        send_as_id = 8659059201
        now = 1_700_000_360.0
        self._prepare_identity(send_as_id, "YuanyingBusy")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "launching"
            state_module.state["yuanying_probe_pending"] = False
            state_module.state["next_yuanying_time"] = now + 8 * 60 * 60

            with (
                patch.object(yuanying, "mark_dirty"),
                patch.object(yuanying, "schedule_yuanying_status_probe", new=AsyncMock()) as probe_mock,
            ):
                handled = await yuanying.handle_yuanying_running_reply(
                    "你的元婴正在执行“元婴闭关”任务，请先使用 .元婴归窍 将其召回。",
                    now,
                    reply_to=SimpleNamespace(raw_text=yuanying.CMD_YUANYING),
                    matched_family="yuanying",
                )

            self.assertTrue(handled)
            self.assertEqual("running", state_module.state["yuanying_phase"])
            self.assertTrue(state_module.state["yuanying_probe_pending"])
            probe_mock.assert_awaited_once()

    async def test_yuanying_running_reply_with_stale_timer_reschedules_cd_before_probe(self):
        send_as_id = 8659059202
        now = 1_700_000_362.0
        self._prepare_identity(send_as_id, "YuanyingStaleTimer")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "waiting_summary"
            state_module.state["yuanying_probe_pending"] = False
            state_module.state["yuanying_summary_sent_at"] = now - 120
            state_module.state["last_yuanying_summary_msg_id"] = 901
            state_module.state["next_yuanying_time"] = now - 1

            with (
                patch.object(_phaseful.random, "uniform", return_value=60),
                patch.object(yuanying, "save_state"),
                patch.object(yuanying, "mark_dirty"),
                patch.object(yuanying, "schedule_yuanying_status_probe", new=AsyncMock()) as probe_mock,
            ):
                handled = await yuanying.handle_yuanying_running_reply(
                    "你的元婴正在执行“元神出窍”任务，无法分身。请先使用 .元婴归窍 将其召回。",
                    now,
                    reply_to=SimpleNamespace(raw_text=yuanying.CMD_YUANYING),
                    matched_family="yuanying",
                )

            self.assertTrue(handled)
            self.assertEqual("running", state_module.state["yuanying_phase"])
            self.assertEqual(0, state_module.state["yuanying_summary_sent_at"])
            self.assertEqual(0, state_module.state["last_yuanying_summary_msg_id"])
            self.assertEqual(
                now + yuanying.YUANYING_CD + yuanying.CD_BUFFER_SEC + 60,
                state_module.state["next_yuanying_time"],
            )
            self.assertTrue(state_module.state["yuanying_probe_pending"])
            probe_mock.assert_awaited_once()

    async def test_yuanying_late_summary_does_not_clobber_new_running_cycle(self):
        send_as_id = 8659059204
        now = 1_700_000_363.0
        next_time = now + 8 * 60 * 60
        self._prepare_identity(send_as_id, "WalterWA2000")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "running"
            state_module.state["next_yuanying_time"] = next_time
            state_module.state["last_yuanying_command_time"] = now - 2

        text = (
            "📜 修士 @WalterWA2000 元神归窍总结\n"
            "你的元婴在虚空中神游八小时，带回了以下收获：\n"
            "元婴成长:\n - 获得了 800 点经验。"
        )

        with (
            patch.object(yuanying, "save_state"),
            patch.object(yuanying, "console_log"),
            patch.object(yuanying, "send_audit_log", new=AsyncMock()),
        ):
            await yuanying.handle_yuanying_summary_broadcast(
                text,
                now,
                reply_context={"send_as_id": send_as_id, "family": "yuanying", "reply_to_msg_id": 10964022},
            )

        with state_module.use_identity(send_as_id):
            self.assertEqual("running", state_module.state["yuanying_phase"])
            self.assertEqual(next_time, state_module.state["next_yuanying_time"])

    async def test_yuanying_summary_finalize_race_does_not_clobber_running_cycle(self):
        send_as_id = 8659059205
        now = 1_700_000_364.0
        next_time = now + 8 * 60 * 60
        self._prepare_identity(send_as_id, "WalterWA2000")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "waiting_summary"
            state_module.state["yuanying_summary_sent_at"] = now - 5
            state_module.state["last_yuanying_summary_msg_id"] = 10964021

        async def mark_new_cycle_started(_spec):
            with state_module.use_identity(send_as_id):
                state_module.state["yuanying_phase"] = "running"
                state_module.state["next_yuanying_time"] = next_time

        text = (
            "📜 修士 @WalterWA2000 元神归窍总结\n"
            "你的元婴在虚空中神游八小时，带回了以下收获：\n"
            "元婴成长:\n - 获得了 800 点经验。"
        )

        with (
            patch.object(_phaseful, "delete_summary_trigger_msg", new=AsyncMock(side_effect=mark_new_cycle_started)),
            patch.object(yuanying, "save_state"),
            patch.object(yuanying, "console_log"),
            patch.object(yuanying, "send_audit_log", new=AsyncMock()),
        ):
            await yuanying.handle_yuanying_summary_broadcast(
                text,
                now,
                reply_context={"send_as_id": send_as_id, "family": "yuanying", "reply_to_msg_id": 10964022},
            )

        with state_module.use_identity(send_as_id):
            self.assertEqual("running", state_module.state["yuanying_phase"])
            self.assertEqual(next_time, state_module.state["next_yuanying_time"])

    async def test_yuanying_success_reply_uses_observed_duration(self):
        send_as_id = 8659059235
        now = 1_700_000_365.0
        self._prepare_identity(send_as_id, "YuanyingObservedCd")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "launching"
            state_module.state["last_yuanying_command_time"] = now - 5

            with (
                patch.object(yuanying, "save_state"),
                patch.object(yuanying, "send_audit_log", new=AsyncMock()),
            ):
                handled = await yuanying.handle_yuanying_success_reply(
                    "你心念一动，丹田中的元婴化作一道流光飞出，消失在天际。\n它将在外云游 8 小时，为你寻觅天地奇珍。下一次发言时若已归来，将自动结算收获。",
                    now,
                    reply_to=SimpleNamespace(raw_text=yuanying.CMD_YUANYING),
                    matched_family="yuanying",
                )

            self.assertTrue(handled)
            self.assertEqual("running", state_module.state["yuanying_phase"])
            self.assertEqual(now + 8 * 60 * 60 + yuanying.CD_BUFFER_SEC, state_module.state["next_yuanying_time"])

            allowed, reason = action_guard.before_send(yuanying.CMD_YUANYING, send_as_id=send_as_id, now=now + 10)
            self.assertFalse(allowed)
            self.assertIn("执行中", reason)

    async def test_yuanying_sect_success_reply_accepts_retreat_command(self):
        send_as_id = 8659059237
        now = 1_700_000_366.0
        self._prepare_identity(send_as_id, "YuanyingSectCd")

        with state_module.use_identity(send_as_id):
            state_module.update_send_as_profile(send_as_id, sect_name="元婴宗")
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "launching"
            state_module.state["last_yuanying_command_time"] = now - 5

            with (
                patch.object(yuanying, "save_state"),
                patch.object(yuanying, "send_audit_log", new=AsyncMock()),
            ):
                handled = await yuanying.handle_yuanying_success_reply(
                    "你心念一动，丹田中的元婴化作一道流光飞出，消失在洞府深处。\n它将在外云游 8 小时。",
                    now,
                    reply_to=SimpleNamespace(raw_text=yuanying.CMD_YUANYING_SECT_RETREAT),
                    matched_family=None,
                )

            self.assertTrue(handled)
            self.assertEqual("running", state_module.state["yuanying_phase"])
            self.assertEqual(yuanying.CMD_YUANYING_SECT_RETREAT, yuanying.get_yuanying_launch_command())

    async def test_yuanying_status_reply_writes_level_for_ui_even_when_module_disabled(self):
        send_as_id = 8659059236
        now = 1_700_000_370.0
        self._prepare_identity(send_as_id, "YuanyingLevelRead")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = False

            handled = await yuanying.handle_yuanying_status_reply(
                "【元婴状态】\n元婴等级: 13 级\n状态: 窍中温养",
                now,
                reply_to=SimpleNamespace(raw_text=yuanying.CMD_YUANYING_STATUS),
                matched_family="yuanying",
            )

        self.assertTrue(handled)
        record = state_module.get_tianjige_dao_path_records()[str(send_as_id)]
        self.assertEqual("13级", record["yuanying_level"])
        identity_snapshot = ui.get_identity_ui_snapshot(send_as_id)
        self.assertEqual("13级", identity_snapshot["yuanying_level_text"])

    async def test_yuanying_sect_qiaozhong_relaunches_retreat_command(self):
        send_as_id = 8659059238
        now = 1_700_000_371.0
        self._prepare_identity(send_as_id, "YuanyingSectWarm")

        with state_module.use_identity(send_as_id):
            state_module.update_send_as_profile(send_as_id, sect_name="元婴宗")
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "launching"
            sent_msg = SimpleNamespace(id=905, sent_at=now)

            with (
                patch.object(yuanying, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(yuanying, "save_state"),
            ):
                handled = await yuanying.handle_yuanying_status_reply(
                    "【元婴状态】\n状态: 窍中温养，可继续闭关。",
                    now,
                    reply_to=SimpleNamespace(raw_text=yuanying.CMD_YUANYING_STATUS),
                    matched_family="yuanying",
                )

            self.assertTrue(handled)
            send_mock.assert_awaited_once_with(yuanying.CMD_YUANYING_SECT_RETREAT, track=False, priority="chain")
            self.assertEqual("launching", state_module.state["yuanying_phase"])


    async def test_summary_timeout_falls_back_to_normal_cd_without_relaunch(self):
        send_as_id = 8659059196
        now = 1_700_000_400.0
        self._prepare_identity(send_as_id, "TimeoutRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - deep_retreat.SUMMARY_TIMEOUT_SEC - 1
            state_module.state["last_deep_retreat_summary_msg_id"] = 789

            with (
                patch.object(_phaseful.random, "uniform", return_value=60),
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()),
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["deep_retreat_phase"])
            self.assertEqual(0, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertGreaterEqual(
                state_module.state["next_deep_retreat_time"],
                now + deep_retreat.DEEP_RETREAT_CD + deep_retreat.CD_BUFFER_SEC,
            )

    async def test_summary_launch_timeout_queries_status_as_abnormal_calibration(self):
        send_as_id = 8659059233
        now = 1_700_000_410.0
        self._prepare_identity(send_as_id, "LaunchTimeoutRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - deep_retreat.SUMMARY_TIMEOUT_SEC - 1
            state_module.state["last_deep_retreat_summary_msg_id"] = 908
            state_module.state["deep_retreat_probe_pending"] = True

            sent_msg = SimpleNamespace(id=909, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "save_state"),
                patch.object(_phaseful, "delete_summary_trigger_msg", new=AsyncMock()),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT_QUERY,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(now, state_module.state["deep_retreat_summary_sent_at"])
            self.assertEqual(909, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertFalse(state_module.state["deep_retreat_probe_pending"])
            self.assertTrue(
                any("续轮指令超时无确认" in str(call.args[0]) for call in audit_mock.await_args_list)
            )

    async def test_summary_launch_timeout_calibration_is_single_flight(self):
        send_as_id = 8659059234
        now = 1_700_000_411.0
        self._prepare_identity(send_as_id, "LaunchTimeoutSingleFlight")

        async def slow_query(*_args, **_kwargs):
            await asyncio.sleep(0.01)
            return SimpleNamespace(id=910, sent_at=now)

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - deep_retreat.SUMMARY_TIMEOUT_SEC - 1
            state_module.state["last_deep_retreat_summary_msg_id"] = 908
            state_module.state["deep_retreat_probe_pending"] = True

            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(side_effect=slow_query)) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "save_state"),
                patch.object(_phaseful, "delete_summary_trigger_msg", new=AsyncMock()),
            ):
                await asyncio.gather(
                    deep_retreat.run_deep_retreat_scheduler(now),
                    deep_retreat.run_deep_retreat_scheduler(now),
                )

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT_QUERY,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual(1, sum("续轮指令超时无确认" in str(call.args[0]) for call in audit_mock.await_args_list))
            self.assertFalse(state_module.state["deep_retreat_probe_pending"])

    async def test_deep_retreat_launching_timeout_queries_status(self):
        send_as_id = 8659059235
        now = 1_700_000_412.0
        self._prepare_identity(send_as_id, "LaunchTimeoutStatus")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "launching"
            state_module.state["last_deep_retreat_command_time"] = now - deep_retreat.LAUNCHING_TIMEOUT_SEC - 1
            state_module.state["next_deep_retreat_time"] = now + deep_retreat.DEEP_RETREAT_CD

            sent_msg = SimpleNamespace(id=911, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "save_state"),
                patch.object(_phaseful, "delete_summary_trigger_msg", new=AsyncMock()),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT_QUERY,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(now, state_module.state["deep_retreat_summary_sent_at"])
            self.assertEqual(911, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertFalse(state_module.state["deep_retreat_probe_pending"])
            self.assertTrue(any("launching 超时" in str(call.args[0]) for call in audit_mock.await_args_list))

    async def test_deep_retreat_summary_due_waits_for_passive_trigger_before_grace_expires(self):
        send_as_id = 8659059202
        now = 1_700_000_450.0
        self._prepare_identity(send_as_id, "NoTaglessRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - deep_retreat.DEEP_RETREAT_SPEC.summary_active_query_grace_sec + 60
            state_module.state["next_deep_retreat_time"] = now - 1

            with (
                patch.object(_phaseful.random, "uniform", return_value=300),
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("summary_due", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 60, state_module.state["next_deep_retreat_time"])
            self.assertEqual(0, state_module.state["last_deep_retreat_summary_msg_id"])

    async def test_deep_retreat_orphan_summary_due_queries_status_immediately(self):
        send_as_id = 8659059240
        now = 1_700_000_451.0
        self._prepare_identity(send_as_id, "NewRetreatIdentity")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - 60
            state_module.state["last_deep_retreat_command_time"] = 0
            state_module.state["last_deep_retreat_summary_msg_id"] = 0
            state_module.state["next_deep_retreat_time"] = now + 1200

            sent_msg = SimpleNamespace(id=903, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "save_state"),
                patch.object(_phaseful, "delete_summary_trigger_msg", new=AsyncMock()),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT_QUERY,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(now, state_module.state["deep_retreat_summary_sent_at"])
            self.assertEqual(903, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertTrue(any("无发起记录" in str(call.args[0]) for call in audit_mock.await_args_list))

    async def test_deep_retreat_idle_due_does_not_orphan_query_status(self):
        send_as_id = 8659059242
        now = 1_700_000_451.0
        self._prepare_identity(send_as_id, "IdleDueRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "idle"
            state_module.state["deep_retreat_summary_sent_at"] = 0
            state_module.state["last_deep_retreat_command_time"] = 0
            state_module.state["last_deep_retreat_summary_msg_id"] = 0
            state_module.state["next_deep_retreat_time"] = now - 1

            with (
                patch.object(_phaseful.random, "uniform", return_value=300),
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("summary_due", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 300, state_module.state["next_deep_retreat_time"])
            self.assertEqual(0, state_module.state["last_deep_retreat_summary_msg_id"])

    async def test_deep_retreat_summary_due_launches_business_command_after_grace(self):
        send_as_id = 8659059202
        now = 1_700_000_450.0
        self._prepare_identity(send_as_id, "NoTaglessRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - deep_retreat.DEEP_RETREAT_SPEC.summary_active_query_grace_sec - 1
            state_module.state["next_deep_retreat_time"] = now - 1

            sent_msg = SimpleNamespace(id=902, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(now, state_module.state["deep_retreat_summary_sent_at"])
            self.assertEqual(902, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertTrue(state_module.state["deep_retreat_probe_pending"])

    async def test_deep_retreat_summary_due_ignores_tianxing_phaseful_defer_deadlock(self):
        send_as_id = 8659059252
        now = 1_700_000_450.0
        self._prepare_identity(send_as_id, "RetreatTianxingDeadlock")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - deep_retreat.DEEP_RETREAT_SPEC.summary_active_query_grace_sec - 1
            state_module.state["next_deep_retreat_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_timeline_state"] = {
                "phase": "phaseful_deferred",
                "blocked_until": now + 600,
                "active_step": {"action": "predict", "status": "pending"},
            }

            sent_msg = SimpleNamespace(id=9252, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(now, state_module.state["deep_retreat_summary_sent_at"])
            self.assertEqual(9252, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertTrue(state_module.state["deep_retreat_probe_pending"])

    async def test_deep_retreat_summary_due_failed_launch_stays_in_summary_due(self):
        send_as_id = 8659059202
        now = 1_700_000_450.0
        started_at = now - deep_retreat.DEEP_RETREAT_SPEC.summary_active_query_grace_sec - 1
        self._prepare_identity(send_as_id, "NoTaglessRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = started_at
            state_module.state["next_deep_retreat_time"] = now - 1

            with (
                patch.object(_phaseful.time, "time", return_value=now),
                patch.object(_phaseful.random, "uniform", return_value=300),
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            audit_mock.assert_awaited_once()
            self.assertEqual("summary_due", state_module.state["deep_retreat_phase"])
            self.assertEqual(started_at, state_module.state["deep_retreat_summary_sent_at"])
            self.assertEqual(now + 300, state_module.state["next_deep_retreat_time"])
            self.assertFalse(state_module.state["deep_retreat_probe_pending"])

    async def test_deep_retreat_post_summary_failed_launch_stays_in_post_wait(self):
        send_as_id = 8659059202
        now = 1_700_000_450.0
        self._prepare_identity(send_as_id, "NoTaglessRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["next_deep_retreat_time"] = now - 1

            with (
                patch.object(_phaseful.time, "time", return_value=now),
                patch.object(_phaseful.random, "uniform", return_value=120),
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            audit_mock.assert_awaited_once()
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 120, state_module.state["next_deep_retreat_time"])
            self.assertTrue(state_module.state["deep_retreat_probe_pending"])

    async def test_deep_retreat_summary_due_wait_log_is_once_per_wait(self):
        send_as_id = 8659059231
        now = 1_700_000_450.0
        self._prepare_identity(send_as_id, "QuietRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - deep_retreat.DEEP_RETREAT_SPEC.summary_active_query_grace_sec + 60
            state_module.state["next_deep_retreat_time"] = now - 1

            with (
                patch.object(_phaseful.random, "uniform", return_value=300),
                patch.object(_phaseful, "send_game_command", new=AsyncMock()),
                patch.object(_phaseful, "console_log") as console_mock,
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)
                state_module.state["next_deep_retreat_time"] = now - 1
                await deep_retreat.run_deep_retreat_scheduler(now + 1)

            console_mock.assert_called_once()
            self.assertTrue(state_module.state["deep_retreat_waiting_logged"])

    def test_deep_retreat_summary_due_ignores_unrelated_dot_command(self):
        send_as_id = 8659059226
        now = 1_700_000_455.0
        next_time = now - 1
        self._prepare_identity(send_as_id, "DivinationDoesNotTriggerRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - 60
            state_module.state["next_deep_retreat_time"] = next_time

            with patch.object(_phaseful, "save_state") as save_mock:
                _phaseful.observe_phaseful_identity_message(
                    send_as_id,
                    ".卜筮问天",
                    now=now,
                    msg_id=9338520,
                    track=True,
                    reply_to=0,
                    source_module="卜筮问天",
                )

            save_mock.assert_not_called()
            self.assertEqual("summary_due", state_module.state["deep_retreat_phase"])
            self.assertEqual(next_time, state_module.state["next_deep_retreat_time"])
            self.assertNotIn(send_as_id, _phaseful._SUMMARY_CONSUMED_COMMANDS)

    def test_summary_risk_reason_flags_due_window_without_global_block(self):
        send_as_id = 8659059225
        now = 1_700_000_454.0
        self._prepare_identity(send_as_id, "RetreatRiskWindow")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "running"
            state_module.state["next_deep_retreat_time"] = now + 30

            reason = _phaseful.get_phaseful_summary_risk_reason(now, lead_sec=60)
            self.assertIn("深度闭关", reason)
            self.assertFalse(_phaseful.has_phaseful_summary_block(now))

            state_module.state["next_deep_retreat_time"] = now + 3600
            self.assertEqual("", _phaseful.get_phaseful_summary_risk_reason(now, lead_sec=60))

            state_module.state["deep_retreat_phase"] = "summary_due"
            self.assertIn("待结算", _phaseful.get_phaseful_summary_risk_reason(now, lead_sec=60))

    def test_deep_retreat_summary_due_accepts_explicit_status_trigger(self):
        send_as_id = 8659059227
        now = 1_700_000_456.0
        self._prepare_identity(send_as_id, "ExplicitRetreatQuery")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - 60
            state_module.state["next_deep_retreat_time"] = now - 1

            with patch.object(_phaseful, "save_state") as save_mock:
                _phaseful.observe_phaseful_identity_message(
                    send_as_id,
                    deep_retreat.CMD_DEEP_RETREAT_QUERY,
                    now=now,
                    msg_id=9338521,
                    track=False,
                    reply_to=0,
                    source_module="深度闭关",
                )

            save_mock.assert_called_once()
            self.assertEqual("observing_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(now, state_module.state["deep_retreat_summary_sent_at"])
            self.assertEqual(now + deep_retreat.DEEP_RETREAT_SPEC.summary_observe_sec, state_module.state["next_deep_retreat_time"])
            self.assertNotIn(send_as_id, _phaseful._SUMMARY_CONSUMED_COMMANDS)

    def test_deep_retreat_summary_due_keeps_replayable_command_trigger(self):
        send_as_id = 8659059228
        now = 1_700_000_457.0
        msg_id = 9338522
        self._prepare_identity(send_as_id, "DreamRetreatReplay")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - 60
            state_module.state["next_deep_retreat_time"] = now - 1

            with patch.object(_phaseful, "save_state"):
                _phaseful.observe_phaseful_identity_message(
                    send_as_id,
                    concubine.CMD_CONCUBINE_DREAM,
                    now=now,
                    msg_id=msg_id,
                    track=False,
                    reply_to=0,
                    priority="normal",
                    source_module="侍妾",
                )

            self.assertEqual("observing_summary", state_module.state["deep_retreat_phase"])
            payload = _phaseful._SUMMARY_CONSUMED_COMMANDS.get(send_as_id)
            self.assertIsNotNone(payload)
            self.assertEqual(concubine.CMD_CONCUBINE_DREAM, payload["cmd"])
            self.assertEqual(msg_id, payload["msg_id"])
            self.assertEqual(["deep_retreat_phase"], payload["specs"])

    def test_deep_retreat_summary_due_ignores_wild_training_replay(self):
        send_as_id = 8659059232
        now = 1_700_000_457.0
        msg_id = 9338526
        command = ".野外历练 谨慎"
        self._prepare_identity(send_as_id, "WildRetreatReplay")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - 60
            state_module.state["next_deep_retreat_time"] = now - 1

            with patch.object(_phaseful, "save_state"):
                _phaseful.observe_phaseful_identity_message(
                    send_as_id,
                    command,
                    now=now,
                    msg_id=msg_id,
                    track=False,
                    reply_to=0,
                    priority="normal",
                    source_module="野外历练",
                )

            self.assertEqual("summary_due", state_module.state["deep_retreat_phase"])
            self.assertNotIn(send_as_id, _phaseful._SUMMARY_CONSUMED_COMMANDS)

    def test_deep_retreat_summary_due_ignores_replayable_reply_echo(self):
        send_as_id = 8659059230
        now = 1_700_000_458.0
        next_time = now - 1
        self._prepare_identity(send_as_id, "ManualDreamEcho")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - 60
            state_module.state["next_deep_retreat_time"] = next_time

            with patch.object(_phaseful, "save_state") as save_mock:
                _phaseful.observe_phaseful_identity_message(
                    send_as_id,
                    concubine.CMD_CONCUBINE_DREAM,
                    now=now,
                    msg_id=9338524,
                    track=True,
                    reply_to=7310786,
                    source_module="侍妾",
                )

            save_mock.assert_not_called()
            self.assertEqual("summary_due", state_module.state["deep_retreat_phase"])
            self.assertEqual(next_time, state_module.state["next_deep_retreat_time"])
            self.assertNotIn(send_as_id, _phaseful._SUMMARY_CONSUMED_COMMANDS)

    async def test_deep_retreat_passive_timeout_queries_status_before_relaunch(self):
        send_as_id = 8659059212
        now = 1_700_000_470.0
        self._prepare_identity(send_as_id, "PassiveTimeoutRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - deep_retreat.DEEP_RETREAT_SPEC.summary_passive_timeout_sec - 1
            state_module.state["last_deep_retreat_summary_msg_id"] = -901

            sent_msg = SimpleNamespace(id=903, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "save_state"),
                patch.object(_phaseful, "delete_summary_trigger_msg", new=AsyncMock()),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT_QUERY,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(903, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertTrue(
                any("改用状态查询确认" in str(call.args[0]) for call in audit_mock.await_args_list)
            )

    async def test_deep_retreat_queued_launch_timeout_delays_relaunch_without_status_query(self):
        send_as_id = 8659059213
        now = 1_700_000_480.0
        self._prepare_identity(send_as_id, "QueuedTimeoutRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "queued_launch"
            state_module.state["last_deep_retreat_command_time"] = now - 300
            state_module.state["next_deep_retreat_time"] = now - 1

            with (
                patch.object(_phaseful.random, "uniform", return_value=180),
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "save_state"),
                patch.object(_phaseful, "delete_summary_trigger_msg", new=AsyncMock()),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertTrue(state_module.state["deep_retreat_probe_pending"])
            self.assertEqual(now + 180, state_module.state["next_deep_retreat_time"])
            self.assertEqual(0, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertTrue(
                any("不再状态查询" in str(call.args[0]) for call in audit_mock.await_args_list)
            )

    async def test_deep_retreat_unconfirmed_post_summary_wait_relaunches_business_command(self):
        send_as_id = 8659059215
        now = 1_700_000_485.0
        self._prepare_identity(send_as_id, "UnconfirmedPostRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["deep_retreat_probe_pending"] = False
            state_module.state["next_deep_retreat_time"] = now - 1

            sent_msg = SimpleNamespace(id=906, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "save_state"),
                patch.object(_phaseful, "delete_summary_trigger_msg", new=AsyncMock()),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("launching", state_module.state["deep_retreat_phase"])
            self.assertEqual(now, state_module.state["last_deep_retreat_command_time"])
            audit_mock.assert_not_awaited()

    async def test_deep_retreat_farm_window_requests_tianxing_timeline_before_launch(self):
        send_as_id = 8659059241
        now = 1_700_000_485.0
        self._prepare_identity(send_as_id, "TianxingFarmRetreat")
        state_module.update_send_as_profile(send_as_id, username="TianxingFarmRetreat", sect_name="天星宗")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            state_module.state["tianxing_auto_config"] = dict(
                self._active_tianxing_farm_config(now),
                deep_retreat_consume_enabled=True,
            )

            with (
                patch.object(deep_retreat, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True})) as timeline_mock,
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(deep_retreat.random, "uniform", return_value=120),
                patch.object(deep_retreat, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            timeline_mock.assert_awaited_once()
            self.assertEqual("闭关", timeline_mock.await_args.kwargs["windows"][0]["route"])
            send_mock.assert_not_awaited()
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 120, state_module.state["next_deep_retreat_time"])

    async def test_deep_retreat_running_prepares_tianxing_before_estimated_summary_due(self):
        send_as_id = 8659059244
        now = 1_700_000_485.0
        due_at = now + 240
        self._prepare_identity(send_as_id, "TianxingRunningRetreat")
        state_module.update_send_as_profile(send_as_id, username="TianxingRunningRetreat", sect_name="天星宗")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "running"
            state_module.state["deep_retreat_probe_pending"] = False
            state_module.state["next_deep_retreat_time"] = due_at
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            state_module.state["tianxing_auto_config"] = dict(
                self._active_tianxing_farm_config(now),
                deep_retreat_consume_enabled=True,
                route_prepare_lead_sec=300,
            )

            with (
                patch.object(deep_retreat, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True})) as timeline_mock,
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(deep_retreat, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            timeline_mock.assert_awaited_once()
            window = timeline_mock.await_args.kwargs["windows"][0]
            self.assertEqual("闭关", window["route"])
            self.assertEqual("consume", window["kind"])
            self.assertEqual(due_at, state_module.state["next_deep_retreat_time"])
            send_mock.assert_not_awaited()
            self.assertEqual("running", state_module.state["deep_retreat_phase"])

    async def test_deep_retreat_tianxing_release_allows_launch(self):
        send_as_id = 8659059242
        now = 1_700_000_486.0
        self._prepare_identity(send_as_id, "TianxingReleasedRetreat")
        state_module.update_send_as_profile(send_as_id, username="TianxingReleasedRetreat", sect_name="天星宗")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_prediction": "闭关",
                "current_prediction_until": now + 3600,
            }
            state_module.state["tianxing_auto_config"] = self._active_tianxing_farm_config(now)
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "闭关": {"released_at": now - 5, "plan_id": "test", "reason": "confirmed"},
                },
            }

            sent_msg = SimpleNamespace(id=907, sent_at=now)
            with (
                patch.object(deep_retreat, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("launching", state_module.state["deep_retreat_phase"])

    async def test_deep_retreat_waits_for_tianxing_retreat_farm_chain(self):
        send_as_id = 8659059245
        now = 1_700_000_486.0
        self._prepare_identity(send_as_id, "TianxingRetreatFarmChain")
        state_module.update_send_as_profile(send_as_id, username="TianxingRetreatFarmChain", sect_name="天星宗")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 30,
                    "next_time": now + 90,
                    "cooldown_until": now + 600,
                    "target_tianji": 42,
                    "last_command": ".服用 合气丹",
                }
            }
            state_module.state["tianxing_auto_config"] = self._active_tianxing_farm_config(now)

            with (
                patch.object(deep_retreat, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(deep_retreat, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_not_awaited()
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 600 + deep_retreat.CD_BUFFER_SEC, state_module.state["next_deep_retreat_time"])

    async def test_deep_retreat_waits_for_tianxing_ready_retreat_prediction(self):
        send_as_id = 8659059246
        now = 1_700_000_486.0
        self._prepare_identity(send_as_id, "TianxingReadyRetreat")
        state_module.update_send_as_profile(send_as_id, username="TianxingReadyRetreat", sect_name="天星宗")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 30,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_prediction": "闭关",
                "current_prediction_until": now + 3600,
                "tianji_value": 3,
            }
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "ready",
                    "started_at": now - 30,
                    "next_time": now,
                    "cooldown_until": now,
                    "target_tianji": 42,
                    "last_command": ".服用 合气丹",
                }
            }
            state_module.state["tianxing_auto_config"] = self._active_tianxing_farm_config(now)

            with (
                patch.object(deep_retreat, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(deep_retreat, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_not_awaited()
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(
                now + deep_retreat.DEEP_RETREAT_TIANXING_RETRY_MIN_SEC + deep_retreat.CD_BUFFER_SEC,
                state_module.state["next_deep_retreat_time"],
            )

    async def test_deep_retreat_waits_for_active_tianxing_explore_timeline(self):
        send_as_id = 8659059247
        now = 1_700_000_486.0
        self._prepare_identity(send_as_id, "TianxingExploreTimeline")
        state_module.update_send_as_profile(send_as_id, username="TianxingExploreTimeline", sect_name="天星宗")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "tianxing-timeline-explore",
                "phase": "sending",
                "route": "探索",
                "active_step_index": 0,
                "active_step": {
                    "action": "predict",
                    "arg": "探索",
                    "route": "探索",
                    "command": ".推命 探索",
                    "status": "sending",
                    "send_started_at": now - 20,
                    "ack_due_at": now + 70,
                },
                "steps": [{
                    "action": "predict",
                    "arg": "探索",
                    "route": "探索",
                    "command": ".推命 探索",
                    "status": "sending",
                    "send_started_at": now - 20,
                    "ack_due_at": now + 70,
                }],
            }
            state_module.state["tianxing_auto_config"] = self._active_tianxing_farm_config(now)

            with (
                patch.object(deep_retreat, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(deep_retreat, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_not_awaited()
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 70 + deep_retreat.CD_BUFFER_SEC, state_module.state["next_deep_retreat_time"])

    async def test_deep_retreat_blocks_when_other_tianxing_prediction_active(self):
        send_as_id = 8659059243
        now = 1_700_000_487.0
        self._prepare_identity(send_as_id, "TianxingConflictRetreat")
        state_module.update_send_as_profile(send_as_id, username="TianxingConflictRetreat", sect_name="天星宗")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_prediction": "探索",
                "current_prediction_until": now + 1800,
                "current_change": "",
                "current_change_until": 0,
            }
            state_module.state["tianxing_auto_config"] = {
                "timeline_enabled": False,
                "farm_window_enabled": False,
                "deep_retreat_consume_enabled": True,
            }

            with (
                patch.object(deep_retreat, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(deep_retreat, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_not_awaited()
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
            self.assertEqual(now + 1800 + deep_retreat.CD_BUFFER_SEC, state_module.state["next_deep_retreat_time"])

    async def test_yuanying_queued_launch_timeout_delays_relaunch_without_status_query(self):
        send_as_id = 8659059217
        now = 1_700_000_487.0
        self._prepare_identity(send_as_id, "QueuedTimeoutSoul")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "queued_launch"
            state_module.state["last_yuanying_command_time"] = now - 300
            state_module.state["next_yuanying_time"] = now - 1

            with (
                patch.object(_phaseful.random, "uniform", return_value=75),
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "save_state"),
                patch.object(_phaseful, "delete_summary_trigger_msg", new=AsyncMock()),
            ):
                await yuanying.run_yuanying_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("post_summary_wait", state_module.state["yuanying_phase"])
            self.assertTrue(state_module.state["yuanying_probe_pending"])
            self.assertEqual(now + 75, state_module.state["next_yuanying_time"])
            self.assertEqual(0, state_module.state["last_yuanying_summary_msg_id"])
            self.assertTrue(
                any("不再状态查询" in str(call.args[0]) for call in audit_mock.await_args_list)
            )

    async def test_deep_retreat_confirmed_post_summary_wait_can_relaunch(self):
        send_as_id = 8659059216
        now = 1_700_000_486.0
        self._prepare_identity(send_as_id, "ConfirmedPostRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now - 1

            sent_msg = SimpleNamespace(id=907, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("launching", state_module.state["deep_retreat_phase"])
            self.assertEqual(now, state_module.state["last_deep_retreat_command_time"])

    async def test_deep_retreat_post_summary_wait_clears_stale_remote_block_before_relaunch(self):
        send_as_id = 8659059218
        now = 1_700_000_486.0
        self._prepare_identity(send_as_id, "StaleRemoteBlockRetreat")

        async def guarded_send(command, **kwargs):
            allowed, _reason = action_guard.before_send(command, send_as_id=send_as_id, now=now)
            if not allowed:
                return None
            return SimpleNamespace(id=908, sent_at=now)

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "post_summary_wait"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now - 1
        action_guard.note_sent(deep_retreat.CMD_DEEP_RETREAT, send_as_id, 456, sent_at=now - 300)
        action_guard.note_remote_block(
            "deep_retreat",
            send_as_id=send_as_id,
            block_until=now + 7200,
            reason="游戏提示深度闭关执行中",
            kind="running",
            now=now - 299,
            command=deep_retreat.CMD_DEEP_RETREAT,
        )

        with state_module.use_identity(send_as_id):
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(side_effect=guarded_send)) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual("launching", state_module.state["deep_retreat_phase"])
            session = state_module.state["action_guard_sessions"].get("deep_retreat") or {}
            self.assertEqual(0, float(session.get("remote_block_until", 0) or 0))
            self.assertEqual("", session.get("remote_block_reason", ""))

    async def test_deep_retreat_already_running_reply_keeps_estimate_without_status_probe(self):
        send_as_id = 8659059214
        now = 1_700_000_490.0
        next_time = now + deep_retreat.DEEP_RETREAT_CD
        self._prepare_identity(send_as_id, "AlreadyRunningRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "launching"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = next_time

            with (
                patch.object(deep_retreat, "schedule_deep_retreat_status_probe", new=AsyncMock()) as probe_mock,
                patch("model.features.passive_inbox.record_passive_inbox_event"),
            ):
                handled = await deep_retreat.handle_deep_retreat_running_reply(
                    "你已在深度闭关之中。",
                    now,
                    reply_to=SimpleNamespace(raw_text=deep_retreat.CMD_DEEP_RETREAT, id=905),
                    matched_family="deep_retreat",
                )

            self.assertTrue(handled)
            probe_mock.assert_not_awaited()
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
            self.assertFalse(state_module.state["deep_retreat_probe_pending"])
            self.assertEqual(next_time, state_module.state["next_deep_retreat_time"])
            allowed, reason = action_guard.before_send(deep_retreat.CMD_DEEP_RETREAT, send_as_id=send_as_id, now=now + 10)
            self.assertFalse(allowed)
            self.assertIn("执行中", reason)

    async def test_deep_retreat_already_running_reply_resets_near_due_estimate(self):
        send_as_id = 8659059214
        now = 1_700_000_490.0
        self._prepare_identity(send_as_id, "AlreadyRunningRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "launching"
            state_module.state["deep_retreat_probe_pending"] = True
            state_module.state["next_deep_retreat_time"] = now + 120

            with (
                patch.object(_phaseful.random, "uniform", return_value=60),
                patch.object(deep_retreat, "save_state"),
                patch("model.features.passive_inbox.record_passive_inbox_event"),
            ):
                handled = await deep_retreat.handle_deep_retreat_running_reply(
                    "你已在深度闭关之中。",
                    now,
                    reply_to=SimpleNamespace(raw_text=deep_retreat.CMD_DEEP_RETREAT, id=905),
                    matched_family="deep_retreat",
                )

            self.assertTrue(handled)
            self.assertEqual("running", state_module.state["deep_retreat_phase"])
            self.assertFalse(state_module.state["deep_retreat_probe_pending"])
            self.assertEqual(
                now + deep_retreat.DEEP_RETREAT_CD + deep_retreat.CD_BUFFER_SEC + 60,
                state_module.state["next_deep_retreat_time"],
            )

    async def test_yuanying_summary_due_waits_for_passive_trigger_before_grace_expires(self):
        send_as_id = 8659059203
        now = 1_700_000_460.0
        self._prepare_identity(send_as_id, "NoTaglessSoul")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "summary_due"
            state_module.state["yuanying_summary_sent_at"] = now - yuanying.YUANYING_SPEC.summary_active_query_grace_sec + 30
            state_module.state["next_yuanying_time"] = now - 1

            with (
                patch.object(_phaseful.random, "uniform", return_value=45),
                patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await yuanying.run_yuanying_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("summary_due", state_module.state["yuanying_phase"])
            self.assertEqual(now + 30, state_module.state["next_yuanying_time"])
            self.assertEqual(0, state_module.state["last_yuanying_summary_msg_id"])

    async def test_yuanying_summary_due_launches_business_command_after_grace(self):
        send_as_id = 8659059203
        now = 1_700_000_460.0
        self._prepare_identity(send_as_id, "NoTaglessSoul")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "summary_due"
            state_module.state["yuanying_summary_sent_at"] = now - yuanying.YUANYING_SPEC.summary_active_query_grace_sec - 1
            state_module.state["next_yuanying_time"] = now - 1

            sent_msg = SimpleNamespace(id=904, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await yuanying.run_yuanying_scheduler(now)

            send_mock.assert_awaited_once_with(
                yuanying.CMD_YUANYING,
                track=False,
                priority="chain",
                source_module="元婴",
            )
            self.assertEqual("waiting_summary", state_module.state["yuanying_phase"])
            self.assertEqual(now, state_module.state["yuanying_summary_sent_at"])
            self.assertEqual(904, state_module.state["last_yuanying_summary_msg_id"])
            self.assertTrue(state_module.state["yuanying_probe_pending"])

    async def test_yuanying_sect_summary_due_launches_retreat_command_after_grace(self):
        send_as_id = 8659059204
        now = 1_700_000_460.0
        self._prepare_identity(send_as_id, "YuanyingSectLaunch")

        with state_module.use_identity(send_as_id):
            state_module.update_send_as_profile(send_as_id, sect_name="元婴宗")
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "summary_due"
            state_module.state["yuanying_summary_sent_at"] = now - yuanying.YUANYING_SPEC.summary_active_query_grace_sec - 1
            state_module.state["next_yuanying_time"] = now - 1

            sent_msg = SimpleNamespace(id=905, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await yuanying.run_yuanying_scheduler(now)

            send_mock.assert_awaited_once_with(
                yuanying.CMD_YUANYING_SECT_RETREAT,
                track=False,
                priority="chain",
                source_module="元婴",
            )
            self.assertEqual("waiting_summary", state_module.state["yuanying_phase"])
            self.assertEqual(905, state_module.state["last_yuanying_summary_msg_id"])

    def test_yuanying_summary_due_ignores_unrelated_dot_command(self):
        send_as_id = 8659059229
        now = 1_700_000_461.0
        next_time = now - 1
        self._prepare_identity(send_as_id, "DivinationDoesNotTriggerSoul")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "summary_due"
            state_module.state["yuanying_summary_sent_at"] = now - 60
            state_module.state["next_yuanying_time"] = next_time

            with patch.object(_phaseful, "save_state") as save_mock:
                _phaseful.observe_phaseful_identity_message(
                    send_as_id,
                    ".卜筮问天",
                    now=now,
                    msg_id=9338523,
                    track=True,
                    reply_to=0,
                    source_module="卜筮问天",
                )

            save_mock.assert_not_called()
            self.assertEqual("summary_due", state_module.state["yuanying_phase"])
            self.assertEqual(next_time, state_module.state["next_yuanying_time"])

    def test_yuanying_post_summary_startup_recovery_stays_immediate(self):
        send_as_id = 8659059197
        now = 1_700_000_500.0
        self._prepare_identity(send_as_id, "RecoverSoul")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "post_summary_wait"
            state_module.state["next_yuanying_time"] = now - 1

            with patch.object(control.random, "uniform", return_value=45):
                control.initialize_identity_runtime(send_as_id, now)

            self.assertEqual("post_summary_wait", state_module.state["yuanying_phase"])
            self.assertEqual(now + 1, state_module.state["next_yuanying_time"])

    def test_yuanying_idle_startup_recovery_uses_short_phaseful_spread(self):
        send_as_id = 8659059198
        now = 1_700_000_600.0
        self._prepare_identity(send_as_id, "RecoverIdleSoul")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "idle"
            state_module.state["next_yuanying_time"] = now - 1

            with patch.object(control.random, "uniform", return_value=120):
                control.initialize_identity_runtime(send_as_id, now)

            self.assertEqual("idle", state_module.state["yuanying_phase"])
            self.assertEqual(now + 120, state_module.state["next_yuanying_time"])

    async def test_summary_replay_skips_tree_water_after_tree_cd_advanced(self):
        send_as_id = 8659059199
        now = 1_700_000_700.0
        self._prepare_identity(send_as_id, "TreeReplay")

        with state_module.use_identity(send_as_id):
            state_module.state["next_irr_time"] = now + 3600

        payload = {
            "cmd": ".灵树灌溉",
            "msg_id": 9338483,
            "sent_at": now - 30,
            "track": False,
            "reply_to": 0,
            "priority": None,
            "max_retry": 0,
        }
        with (
            patch.object(_phaseful.time, "time", return_value=now),
            patch.object(_phaseful.random, "uniform", return_value=0),
            patch.object(_phaseful.asyncio, "sleep", new=AsyncMock()),
            patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(_phaseful, "send_audit_log", new=AsyncMock()),
            patch.object(_phaseful, "save_state"),
        ):
            await _phaseful._replay_summary_consumed_command(send_as_id, payload)

        send_mock.assert_not_awaited()

    async def test_summary_replay_concubine_dream_rebuilds_pending_state(self):
        send_as_id = 8659059225
        now = 1_700_001_000.0
        old_msg_id = 9338504
        new_msg_id = 9338505
        self._prepare_identity(send_as_id, "DreamReplay")

        with state_module.use_identity(send_as_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "dream_pending"
            state_module.state["concubine_dream_msg_id"] = old_msg_id
            state_module.state["next_concubine_time"] = now - 1

        payload = {
            "cmd": concubine.CMD_CONCUBINE_DREAM,
            "msg_id": old_msg_id,
            "sent_at": now - 30,
            "track": False,
            "reply_to": 0,
            "priority": "normal",
            "max_retry": 0,
            "send_intent": {"source_module": "侍妾"},
        }
        sent_msg = SimpleNamespace(id=new_msg_id, sent_at=now + 1)
        with (
            patch.object(_phaseful.time, "time", return_value=now),
            patch.object(_phaseful.random, "uniform", return_value=0),
            patch.object(_phaseful.asyncio, "sleep", new=AsyncMock()),
            patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
            patch.object(_phaseful, "send_audit_log", new=AsyncMock()),
            patch.object(_phaseful, "save_state"),
        ):
            await _phaseful._replay_summary_consumed_command(send_as_id, payload)

        send_mock.assert_awaited_once_with(
            concubine.CMD_CONCUBINE_DREAM,
            track=False,
            send_as_id=send_as_id,
            priority="retry",
            max_retry=0,
            source_module="侍妾",
            op_id=f"phaseful_replay:{send_as_id}:{old_msg_id}:{concubine.CMD_CONCUBINE_DREAM}",
            chain_id=f"phaseful_replay:{send_as_id}:{old_msg_id}",
        )
        with state_module.use_identity(send_as_id):
            self.assertEqual("dream_pending", state_module.state["concubine_phase"])
            self.assertEqual(new_msg_id, state_module.state["concubine_dream_msg_id"])
            self.assertEqual(now + 1 + concubine.CONCUBINE_PHASE_TIMEOUT_SEC, state_module.state["next_concubine_time"])

    async def test_summary_replay_concubine_voyage_return_rebuilds_pending_state(self):
        send_as_id = 8659059226
        now = 1_700_001_100.0
        old_msg_id = 9338514
        new_msg_id = 9338515
        self._prepare_identity(send_as_id, "VoyageReplay")

        with state_module.use_identity(send_as_id):
            state_module.state["concubine_voyage_enabled"] = True
            state_module.state["concubine_phase"] = "voyage_return_pending"
            state_module.state["concubine_voyage_status"] = "returned"
            state_module.state["concubine_voyage_msg_id"] = old_msg_id
            state_module.state["concubine_voyage_retry_count"] = 0
            state_module.state["next_concubine_time"] = now - 1

        payload = {
            "cmd": concubine.CMD_CONCUBINE_VOYAGE_RETURN,
            "msg_id": old_msg_id,
            "sent_at": now - 30,
            "track": False,
            "reply_to": 0,
            "priority": "chain",
            "max_retry": 0,
            "send_intent": {"source_module": "侍妾远航"},
        }
        sent_msg = SimpleNamespace(id=new_msg_id, sent_at=now + 1)
        with (
            patch.object(_phaseful.time, "time", return_value=now),
            patch.object(_phaseful.random, "uniform", return_value=0),
            patch.object(_phaseful.asyncio, "sleep", new=AsyncMock()),
            patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
            patch.object(_phaseful, "send_audit_log", new=AsyncMock()),
            patch.object(_phaseful, "save_state"),
        ):
            await _phaseful._replay_summary_consumed_command(send_as_id, payload)

        send_mock.assert_awaited_once_with(
            concubine.CMD_CONCUBINE_VOYAGE_RETURN,
            track=False,
            send_as_id=send_as_id,
            priority="retry",
            max_retry=0,
            source_module="侍妾远航",
            op_id=f"phaseful_replay:{send_as_id}:{old_msg_id}:{concubine.CMD_CONCUBINE_VOYAGE_RETURN}",
            chain_id=f"phaseful_replay:{send_as_id}:{old_msg_id}",
        )
        with state_module.use_identity(send_as_id):
            self.assertEqual("voyage_return_pending", state_module.state["concubine_phase"])
            self.assertEqual(new_msg_id, state_module.state["concubine_voyage_msg_id"])
            self.assertEqual(1, state_module.state["concubine_voyage_retry_count"])
            self.assertEqual(now + 1 + concubine.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC, state_module.state["next_concubine_time"])

    def test_summary_replay_rejects_archived_voyage_command_with_route_suffix(self):
        self.assertFalse(_phaseful._is_summary_replayable_command(f"{concubine.CMD_CONCUBINE_VOYAGE} 冒险"))

    def test_summary_replay_rejects_wild_training_with_strategy_suffix(self):
        self.assertFalse(_phaseful._is_summary_replayable_command(".野外历练 谨慎"))

    async def test_summary_replay_ignores_stale_wild_training_payload(self):
        send_as_id = 8659059221
        now = 1_700_001_200.0
        old_msg_id = 9338525
        command = ".野外历练 谨慎"
        self._prepare_identity(send_as_id, "WildReplay")

        payload = {
            "cmd": command,
            "msg_id": old_msg_id,
            "sent_at": now - 30,
            "track": False,
            "reply_to": 0,
            "priority": "normal",
            "max_retry": 0,
            "send_intent": {"source_module": "野外历练"},
        }
        with (
            patch.object(_phaseful.time, "time", return_value=now),
            patch.object(_phaseful.random, "uniform", return_value=0),
            patch.object(_phaseful.asyncio, "sleep", new=AsyncMock()),
            patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(_phaseful, "send_audit_log", new=AsyncMock()),
            patch.object(_phaseful, "save_state"),
        ):
            await _phaseful._replay_summary_consumed_command(send_as_id, payload)

        send_mock.assert_not_awaited()

    async def test_summary_replay_tower_sets_explicit_wait_state(self):
        send_as_id = 8659059223
        now = 1_700_000_800.0
        old_msg_id = 9338484
        new_msg_id = 9338485
        self._prepare_identity(send_as_id, "TowerReplay")

        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_msg_id"] = old_msg_id
            state_module.state["tower_reply_due_at"] = now - 1
            state_module.state["tower_retry_count"] = 0
            state_module.state["pending_tasks"] = {
                old_msg_id: {
                    "cmd": ".闯塔",
                    "sent_at": now - 30,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                    "max_retry": 0,
                }
            }

        payload = {
            "cmd": ".闯塔",
            "msg_id": old_msg_id,
            "sent_at": now - 30,
            "track": False,
            "reply_to": 0,
            "priority": "normal",
            "max_retry": 0,
            "send_intent": {"source_module": "闯塔"},
        }
        sent_msg = SimpleNamespace(id=new_msg_id, sent_at=now + 1)
        with (
            patch.object(_phaseful.time, "time", return_value=now),
            patch.object(_phaseful.random, "uniform", return_value=0),
            patch.object(_phaseful.asyncio, "sleep", new=AsyncMock()),
            patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
            patch.object(_phaseful, "send_audit_log", new=AsyncMock()),
            patch.object(_phaseful, "save_state"),
        ):
            await _phaseful._replay_summary_consumed_command(send_as_id, payload)

        send_mock.assert_awaited_once_with(
            ".闯塔",
            track=False,
            send_as_id=send_as_id,
            priority="retry",
            max_retry=0,
            source_module="闯塔",
            op_id=f"phaseful_replay:{send_as_id}:{old_msg_id}:.闯塔",
            chain_id=f"phaseful_replay:{send_as_id}:{old_msg_id}",
        )
        with state_module.use_identity(send_as_id):
            self.assertEqual(new_msg_id, state_module.state["last_tower_msg_id"])
            self.assertEqual(1, state_module.state["tower_retry_count"])
            self.assertEqual(now + 1 + tower.TOWER_REPLY_TIMEOUT_SEC, state_module.state["tower_reply_due_at"])
            self.assertEqual(state_module.state["tower_reply_due_at"], state_module.state["next_tower_time"])
            self.assertNotIn(old_msg_id, state_module.state["pending_tasks"])

    async def test_summary_replay_tower_skips_done_today_or_other_pending(self):
        send_as_id = 8659059224
        now = 1_700_000_900.0
        old_msg_id = 9338494
        other_msg_id = 9338495
        self._prepare_identity(send_as_id, "TowerReplaySkip")

        payload = {
            "cmd": ".闯塔",
            "msg_id": old_msg_id,
            "sent_at": now - 30,
            "track": False,
            "reply_to": 0,
            "priority": None,
            "max_retry": 0,
        }
        with state_module.use_identity(send_as_id):
            state_module.state["tower_enabled"] = True
            state_module.state["last_tower_day"] = _phaseful.get_day_key(now)
            state_module.state["last_tower_msg_id"] = old_msg_id

        with (
            patch.object(_phaseful.time, "time", return_value=now),
            patch.object(_phaseful.random, "uniform", return_value=0),
            patch.object(_phaseful.asyncio, "sleep", new=AsyncMock()),
            patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(_phaseful, "send_audit_log", new=AsyncMock()),
            patch.object(_phaseful, "save_state"),
        ):
            await _phaseful._replay_summary_consumed_command(send_as_id, payload)

        send_mock.assert_not_awaited()

        with state_module.use_identity(send_as_id):
            state_module.state["last_tower_day"] = ""
            state_module.state["tower_retry_count"] = 0
            state_module.state["pending_tasks"] = {
                old_msg_id: {"cmd": ".闯塔", "sent_at": now - 30, "retry": 0, "timeout": 10},
                other_msg_id: {"cmd": ".闯塔", "sent_at": now - 10, "retry": 0, "timeout": 10},
            }

        with (
            patch.object(_phaseful.time, "time", return_value=now),
            patch.object(_phaseful.random, "uniform", return_value=0),
            patch.object(_phaseful.asyncio, "sleep", new=AsyncMock()),
            patch.object(_phaseful, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(_phaseful, "send_audit_log", new=AsyncMock()),
            patch.object(_phaseful, "save_state"),
        ):
            await _phaseful._replay_summary_consumed_command(send_as_id, payload)

        send_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertNotIn(old_msg_id, state_module.state["pending_tasks"])
            self.assertIn(other_msg_id, state_module.state["pending_tasks"])


if __name__ == "__main__":
    unittest.main()
