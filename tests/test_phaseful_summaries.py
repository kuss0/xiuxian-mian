import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import control, runtime
from model.features import _phaseful, concubine, deep_retreat, tower, yuanying


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class PhasefulSummaryTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def _prepare_identity(self, send_as_id, username):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username=username)

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
                patch.object(deep_retreat.random, "uniform", return_value=180),
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
            self.assertEqual(now + 180, state_module.state["next_deep_retreat_time"])
            self.assertTrue(state_module.state["deep_retreat_probe_pending"])
            audit_mock.assert_awaited_once()
            self.assertTrue(any(
                call.kwargs.get("family") == "deep_retreat"
                and call.kwargs.get("decision") == "not_running_retry_later"
                and "你并未处于深度闭关" in str(call.kwargs.get("matched_text") or "")
                for call in inbox_mock.call_args_list
            ))

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
            self.assertEqual(now + 300, state_module.state["next_deep_retreat_time"])
            self.assertEqual(0, state_module.state["last_deep_retreat_summary_msg_id"])

    async def test_deep_retreat_summary_due_keeps_waiting_passively_after_grace(self):
        send_as_id = 8659059202
        now = 1_700_000_450.0
        self._prepare_identity(send_as_id, "NoTaglessRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "summary_due"
            state_module.state["deep_retreat_summary_sent_at"] = now - deep_retreat.DEEP_RETREAT_SPEC.summary_active_query_grace_sec - 1
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
            self.assertEqual(now + 300, state_module.state["next_deep_retreat_time"])
            self.assertEqual(0, state_module.state["last_deep_retreat_summary_msg_id"])

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

    async def test_deep_retreat_queued_launch_timeout_queries_status_before_relaunch(self):
        send_as_id = 8659059213
        now = 1_700_000_480.0
        self._prepare_identity(send_as_id, "QueuedTimeoutRetreat")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "queued_launch"
            state_module.state["last_deep_retreat_command_time"] = now - 300
            state_module.state["next_deep_retreat_time"] = now - 1

            sent_msg = SimpleNamespace(id=904, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(_phaseful, "save_state"),
            ):
                await deep_retreat.run_deep_retreat_scheduler(now)

            send_mock.assert_awaited_once_with(
                deep_retreat.CMD_DEEP_RETREAT_QUERY,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(904, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertTrue(
                any("先查询状态确认" in str(call.args[0]) for call in audit_mock.await_args_list)
            )

    async def test_deep_retreat_unconfirmed_post_summary_wait_queries_status_before_relaunch(self):
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
                deep_retreat.CMD_DEEP_RETREAT_QUERY,
                track=False,
                priority="chain",
                source_module="深度闭关",
            )
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
            self.assertEqual(906, state_module.state["last_deep_retreat_summary_msg_id"])
            self.assertTrue(
                any("缺少总结/状态确认" in str(call.args[0]) for call in audit_mock.await_args_list)
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
            self.assertEqual(now + 45, state_module.state["next_yuanying_time"])
            self.assertEqual(0, state_module.state["last_yuanying_summary_msg_id"])

    async def test_yuanying_summary_due_sends_explicit_status_query_after_grace(self):
        send_as_id = 8659059203
        now = 1_700_000_460.0
        self._prepare_identity(send_as_id, "NoTaglessSoul")

        with state_module.use_identity(send_as_id):
            state_module.state["yuanying_enabled"] = True
            state_module.state["yuanying_phase"] = "summary_due"
            state_module.state["yuanying_summary_sent_at"] = now - yuanying.YUANYING_SPEC.summary_active_query_grace_sec - 1
            state_module.state["next_yuanying_time"] = now - 1

            sent_msg = SimpleNamespace(id=902, sent_at=now)
            with (
                patch.object(_phaseful, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(_phaseful, "console_log"),
                patch.object(_phaseful, "save_state"),
            ):
                await yuanying.run_yuanying_scheduler(now)

            send_mock.assert_awaited_once_with(
                yuanying.CMD_YUANYING_STATUS,
                track=False,
                priority="chain",
                source_module="元婴",
            )
            self.assertEqual("waiting_summary", state_module.state["yuanying_phase"])
            self.assertEqual(902, state_module.state["last_yuanying_summary_msg_id"])

    def test_yuanying_post_summary_startup_recovery_is_staggered_like_deep_retreat(self):
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
            self.assertEqual(now + 45, state_module.state["next_yuanying_time"])

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
            priority="normal",
            max_retry=0,
            source_module="侍妾",
        )
        with state_module.use_identity(send_as_id):
            self.assertEqual("dream_pending", state_module.state["concubine_phase"])
            self.assertEqual(new_msg_id, state_module.state["concubine_dream_msg_id"])
            self.assertEqual(now + 1 + concubine.CONCUBINE_PHASE_TIMEOUT_SEC, state_module.state["next_concubine_time"])

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
