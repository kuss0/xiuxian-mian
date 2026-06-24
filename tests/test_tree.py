import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import runtime
from model.features import passive_inbox, tree


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class TreeTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_channel_identity_reply_sender_resolves_tree_status_owner(self):
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")

        reply_to = type(
            "ReplyMessage",
            (),
            {
                "id": 9352845,
                "sender_id": -1003800619925,
                "raw_text": ".灵树状态",
            },
        )()

        context = runtime.get_reply_context(reply_to, reply_to_msg_id=9352845)

        self.assertEqual(identity_id, context["send_as_id"])
        self.assertEqual("tree_panel", context["family"])
        self.assertEqual("reply_sender", context["matched_via"])

    async def test_unregistered_reply_sender_does_not_resolve_owner(self):
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")

        reply_to = type(
            "ReplyMessage",
            (),
            {
                "id": 9352846,
                "sender_id": 123456789,
                "raw_text": ".灵树状态",
            },
        )()

        context = runtime.get_reply_context(reply_to, reply_to_msg_id=9352846)

        self.assertIsNone(context["send_as_id"])
        self.assertEqual("tree_panel", context["family"])

    async def test_unowned_normal_panel_does_not_recover_stale_maturing_state(self):
        now = 1000.0
        identity_ids = [3756719391, 3800619925]
        for identity_id in identity_ids:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username=f"user{identity_id}")
            with state_module.use_identity(identity_id):
                state_module.state["tree_enabled"] = True
                state_module.state["is_maturing"] = True
                state_module.state["is_harvested"] = True
                state_module.state["pending_irrigation"] = True
                state_module.state["next_irr_time"] = now + 9999999

        panel = (
            "【落云宗 · 灵眼之树】\n"
            "💧 环境: 干渴 (需 水/冰/雾)\n"
            "🌲 进度:\n"
            "🟩🟩🟩⬜⬜ 75.08%\n"
            "🔄 阶段: 4 / 4\n\n"
            "👤 你的当前状态: 944 点\n"
            "🌰 奉养灵树: 需【一截灵眼之树】 0/1 或木髓 0/3"
        )

        with (
            patch.object(tree, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tree, "save_state"),
        ):
            with state_module.use_identity(identity_ids[0]):
                handled = await tree.handle_tree_panel(panel, now, False)

        self.assertFalse(handled)
        audit_mock.assert_not_awaited()
        for identity_id in identity_ids:
            with state_module.use_identity(identity_id):
                self.assertTrue(state_module.state["is_maturing"])
                self.assertTrue(state_module.state["is_harvested"])
                self.assertTrue(state_module.state["pending_irrigation"])
                self.assertEqual(now + 9999999, state_module.state["next_irr_time"])

    async def test_owned_normal_panel_recovers_stale_maturing_state_for_all_tree_identities(self):
        now = 1000.0
        identity_ids = [3756719391, 3800619925]
        for identity_id in identity_ids:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username=f"user{identity_id}")
            with state_module.use_identity(identity_id):
                state_module.state["tree_enabled"] = True
                state_module.state["is_maturing"] = True
                state_module.state["is_harvested"] = True
                state_module.state["pending_irrigation"] = True
                state_module.state["next_irr_time"] = now + 9999999

        panel = (
            "【落云宗 · 灵眼之树】\n"
            "💧 环境: 干渴 (需 水/冰/雾)\n"
            "🌲 进度:\n"
            "🟩🟩🟩⬜⬜ 75.08%\n"
            "🔄 阶段: 4 / 4\n\n"
            "📊 实时贡献榜:\n"
            "1. user3756719391 (你): 944\n\n"
            "👤 你的当前状态: 944 点\n"
            "🌰 奉养灵树: 需【一截灵眼之树】 0/1 或木髓 0/3"
        )

        with (
            patch.object(tree, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tree, "save_state"),
        ):
            with state_module.use_identity(identity_ids[0]):
                handled = await tree.handle_tree_panel(panel, now, False)

        self.assertTrue(handled)
        audit_mock.assert_awaited_once()
        for identity_id in identity_ids:
            with state_module.use_identity(identity_id):
                self.assertFalse(state_module.state["is_maturing"])
                self.assertFalse(state_module.state["is_harvested"])
                self.assertFalse(state_module.state["pending_irrigation"])
                self.assertGreaterEqual(state_module.state["next_irr_time"], now + 45 * 60)
                self.assertLessEqual(state_module.state["next_irr_time"], now + 75 * 60)

    async def test_mature_panel_final_branch_board_queues_all_enabled_harvest(self):
        now = 1000.0
        identity_ids = [3756719391, 3800619925]
        for identity_id in identity_ids:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username=f"user{identity_id}")
            with state_module.use_identity(identity_id):
                state_module.state["tree_enabled"] = True
                state_module.state["is_maturing"] = True
                state_module.state["is_harvested"] = False
                state_module.state["tree_harvest_inflight_until"] = 0

        panel = (
            "【落云宗 · 灵眼之树】\n"
            "✨ 状态: 成熟采摘期\n"
            "⏳ 剩余: 23小时50分钟20秒\n"
            "🏆 本轮最终分枝榜 (天道快照):\n"
            "🥇 user3800619925 (你): 1039 ⏳(未领)\n"
            "🥈 user3756719391: 957 ⏳(未领)\n\n"
            "👤 你的当前状态: 1039 点\n"
            "🌰 奉养灵树: 凝液需木髓 11/12"
        )
        captured_batches = []

        def capture_fire_and_forget(coro):
            captured_batches.append(list(coro.cr_frame.f_locals["send_as_ids"]))
            coro.close()

        with (
            patch.object(tree, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tree, "_iter_tree_enabled_identity_ids", side_effect=lambda: iter(identity_ids)),
            patch.object(tree, "_fire_and_forget", side_effect=capture_fire_and_forget) as fire_mock,
            patch.object(tree, "save_state"),
        ):
            with state_module.use_identity(identity_ids[1]):
                handled = await tree.handle_tree_panel(panel, now, False)

        self.assertTrue(handled)
        fire_mock.assert_called_once()
        audit_mock.assert_awaited_once()
        self.assertEqual([identity_ids], captured_batches)
        for identity_id in identity_ids:
            with state_module.use_identity(identity_id):
                self.assertTrue(state_module.state["is_maturing"])
                self.assertFalse(state_module.state["is_harvested"])
                self.assertGreater(state_module.state["tree_harvest_inflight_until"], now)

    async def test_unowned_final_branch_board_queues_only_local_unclaimed_tree_identity(self):
        now = 1000.0
        identity_ids = [3756719391, 3800619925]
        for identity_id in identity_ids:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username=f"user{identity_id}")
            with state_module.use_identity(identity_id):
                state_module.state["tree_enabled"] = True
                state_module.state["is_maturing"] = True
                state_module.state["is_harvested"] = False
                state_module.state["tree_harvest_inflight_until"] = 0

        panel = (
            "【落云宗 · 灵眼之树】\n"
            "✨ 状态: 成熟采摘期\n"
            "🏆 本轮最终分枝榜 (天道快照):\n"
            "5. outsider (你): 1106 ⏳(未领)\n"
            "7. user3800619925: 1039 ⏳(未领)\n"
            "8. user3756719391: 957 ✅(已领)\n\n"
            "👤 你的当前状态: 1106 点"
        )
        captured_batches = []

        def capture_fire_and_forget(coro):
            captured_batches.append(list(coro.cr_frame.f_locals["send_as_ids"]))
            coro.close()

        with (
            patch.object(tree, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tree, "_iter_tree_enabled_identity_ids", side_effect=lambda: iter(identity_ids)),
            patch.object(tree, "_fire_and_forget", side_effect=capture_fire_and_forget) as fire_mock,
            patch.object(tree, "save_state"),
        ):
            with state_module.use_identity(identity_ids[0]):
                handled = await tree.handle_tree_panel(panel, now, False)

        self.assertTrue(handled)
        fire_mock.assert_called_once()
        audit_mock.assert_awaited_once()
        self.assertEqual([[identity_ids[1]]], captured_batches)
        with state_module.use_identity(identity_ids[1]):
            self.assertTrue(state_module.state["is_maturing"])
            self.assertGreater(state_module.state["tree_harvest_inflight_until"], now)
        with state_module.use_identity(identity_ids[0]):
            self.assertEqual(0, state_module.state["tree_harvest_inflight_until"])

    async def test_recent_normal_startup_tree_status_request_is_suppressed(self):
        now = 1000.0
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")
        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            state_module.state["is_maturing"] = False
            state_module.state["is_invading"] = False
            state_module.state["pending_irrigation"] = False
            state_module.state["last_tree_status_sent_at"] = now - 60
            state_module.state["tree_bootstrap_check_needed"] = False
            state_module.state["tree_bootstrap_check_due_at"] = 0

            with patch.object(tree, "save_state") as save_mock:
                scheduled = tree.request_tree_bootstrap_check(now)

            self.assertFalse(scheduled)
            self.assertFalse(state_module.state["tree_bootstrap_check_needed"])
            self.assertEqual(0, state_module.state["tree_bootstrap_check_due_at"])
            save_mock.assert_not_called()

    async def test_recent_normal_due_tree_bootstrap_check_does_not_send_again(self):
        now = 1000.0
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")
        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            state_module.state["is_maturing"] = False
            state_module.state["is_invading"] = False
            state_module.state["pending_irrigation"] = False
            state_module.state["last_tree_status_sent_at"] = now - 60
            state_module.state["tree_bootstrap_check_needed"] = True
            state_module.state["tree_bootstrap_check_due_at"] = now - 1

            with (
                patch.object(tree, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(tree, "save_state"),
            ):
                await tree.run_tree_bootstrap_check(now)

            send_mock.assert_not_awaited()
            self.assertFalse(state_module.state["tree_bootstrap_check_needed"])
            self.assertEqual(0, state_module.state["tree_bootstrap_check_due_at"])

    async def test_tree_scheduler_queries_pulse_panel_without_recent_snapshot(self):
        now = 1000.0
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")
        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            state_module.state["is_maturing"] = False
            state_module.state["is_invading"] = False
            state_module.state["next_irr_time"] = now - 1
            state_module.state["tree_pulse_last_panel_at"] = 0

            with (
                patch.object(tree, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9911628, sent_at=now + 1))) as send_mock,
                patch.object(tree, "save_state"),
            ):
                await tree.run_tree_scheduler(now)

            send_mock.assert_awaited_once_with(
                tree.CMD_TREE_PULSE_STATUS,
                track=False,
                max_retry=0,
                source_module="灵树",
            )
            self.assertGreaterEqual(state_module.state["next_irr_time"], now + 1 + tree.TREE_PULSE_STATUS_SPREAD_MIN_SEC)
            self.assertLessEqual(state_module.state["next_irr_time"], now + 1 + tree.TREE_PULSE_STATUS_SPREAD_MAX_SEC)

    async def test_tree_scheduler_never_sends_legacy_irrigation_for_pulse_mode(self):
        now = 1000.0
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")
        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            state_module.state["is_maturing"] = False
            state_module.state["is_invading"] = False
            state_module.state["next_irr_time"] = now - 1
            state_module.state["tree_pulse_last_panel_at"] = 0

            with (
                patch.object(tree, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9911628, sent_at=now + 1))) as send_mock,
                patch.object(tree, "save_state"),
            ):
                await tree.run_tree_scheduler(now)

            self.assertNotEqual(".灵树灌溉", send_mock.await_args.args[0])
            self.assertEqual(tree.CMD_TREE_PULSE_STATUS, send_mock.await_args.args[0])

    async def test_tree_scheduler_sends_one_pulse_action_from_recent_panel(self):
        now = 1000.0
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")
        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            state_module.state["is_maturing"] = False
            state_module.state["is_invading"] = False
            state_module.state["next_irr_time"] = now - 1
            state_module.state["tree_pulse_last_panel_at"] = now - 60
            state_module.state["tree_pulse_progress"] = 50.0
            state_module.state["tree_pulse_main"] = "木"
            state_module.state["tree_pulse_aux"] = "水"
            state_module.state["tree_pulse_reverse"] = "火"
            state_module.state["tree_pulse_neutral"] = "土/金"
            state_module.state["tree_pulse_stability"] = 62
            state_module.state["tree_pulse_turbidity"] = 0
            state_module.state["tree_pulse_daily_used"] = 0
            state_module.state["tree_pulse_daily_limit"] = 6

            with (
                patch.object(tree, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9911629, sent_at=now + 2))) as send_mock,
                patch.object(tree, "save_state"),
            ):
                await tree.run_tree_scheduler(now)

            send_mock.assert_awaited_once_with(
                ".定脉 固脉 土",
                max_retry=0,
                reply_timeout=tree.TREE_PULSE_REPLY_TIMEOUT_SEC,
                source_module="灵树",
            )
            self.assertEqual(now + 2 + 10 * 60, state_module.state["next_irr_time"])

    async def test_tree_pulse_strategy_prefers_turbidity_then_stability_then_rush(self):
        high_turbidity = {
            "progress": 50.0,
            "main": "木",
            "aux": "水",
            "reverse": "火",
            "neutral_elements": ["土", "金"],
            "stability": 90,
            "turbidity": 60,
            "daily_used": 0,
            "daily_limit": 6,
            "rush_used": 0,
            "rush_limit": 2,
        }
        self.assertEqual((".定脉 净浊", "浊息过高"), tree._choose_tree_pulse_command(high_turbidity))

        low_stability = dict(high_turbidity, turbidity=0, stability=84)
        self.assertEqual((".定脉 固脉 土", "脉稳偏低"), tree._choose_tree_pulse_command(low_stability))

        stable = dict(high_turbidity, turbidity=0, stability=90)
        self.assertEqual((".定脉 冲脉 木", "冲脉次数可用"), tree._choose_tree_pulse_command(stable))

        rush_exhausted = dict(stable, rush_used=2, rush_limit=2)
        self.assertEqual((".定脉 注灵 木", "主脉注灵"), tree._choose_tree_pulse_command(rush_exhausted))

    async def test_tree_pulse_full_progress_stops_actions(self):
        now = 1000.0
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")
        panel = (
            "【落云宗 · 灵树玩法】\n"
            "⚙️ 当前玩法: 云梦灵眼定脉\n"
            "🌲 进度:\n"
            "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 100.00%\n"
            "🧭 今日脉象: 主脉【木】 / 辅脉【水】 / 逆脉【火】 / 平脉【土/金】\n"
            "🧷 脉稳: 62/100 (脉象平稳) | ☁️ 浊息/紊乱: 0/165\n"
            "📜 今日定脉令: 0/6 | 冲脉 0/2\n"
            "指令: .定脉 注灵 木 / .定脉 固脉 土 / .定脉 净浊 / .定脉 冲脉 火"
        )
        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            state_module.state["is_maturing"] = False
            state_module.state["next_irr_time"] = now - 1
            with (
                patch.object(tree, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(tree, "save_state"),
            ):
                handled = await tree.handle_tree_panel(panel, now, True)

            self.assertTrue(handled)
            self.assertTrue(state_module.state["is_maturing"])
            self.assertGreater(state_module.state["next_irr_time"], now + 24 * 3600)
            self.assertEqual("灵树已成熟或遭劫难，停止定脉", state_module.state["tree_pulse_last_error"])
            audit_mock.assert_awaited_once()

    async def test_tree_pulse_panel_accepts_log_style_elements(self):
        now = 1000.0
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")
        panel = (
            "【云梦灵眼定脉】\n"
            "灵眼之树进度 72.50%\n"
            "主脉: 木｜辅脉: 水｜逆脉: 火｜平脉: 土/金\n"
            "脉稳（当前 62）｜浊气/紊乱: 3/165\n"
            "今日定脉令: 1/6｜冲脉 0/2"
        )
        parsed = tree.parse_tree_pulse_panel(panel)

        self.assertIsNotNone(parsed)
        self.assertEqual("木", parsed["main"])
        self.assertEqual("水", parsed["aux"])
        self.assertEqual("火", parsed["reverse"])
        self.assertEqual(["土", "金"], parsed["neutral_elements"])
        self.assertEqual(62, parsed["stability"])
        self.assertEqual(3, parsed["turbidity"])

        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            with (
                patch.object(tree, "send_audit_log", new=AsyncMock()),
                patch.object(tree, "save_state"),
            ):
                handled = await tree.handle_tree_panel(panel, now, True)

            self.assertTrue(handled)
            self.assertEqual("木", state_module.state["tree_pulse_main"])
            self.assertEqual("水", state_module.state["tree_pulse_aux"])
            self.assertEqual("火", state_module.state["tree_pulse_reverse"])
            self.assertEqual("土/金", state_module.state["tree_pulse_neutral"])

    async def test_tree_pulse_panel_accepts_current_panel_wording(self):
        now = 1000.0
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")
        panel = (
            "【落云宗 · 灵树玩法】\n"
            "⚙️ 当前玩法: 云梦灵眼定脉\n"
            "🌲 进度:\n"
            "🟩🟩⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 20.63%\n"
            "当前管理员关闭旧版【灵树灌溉】，本轮成长改由【云梦灵眼定脉】推进。\n"
            "🌀 定脉玩法: 云梦灵眼定脉\n"
            "🧭 今日脉象: 主脉【水】 / 辅脉【土】 / 逆脉【金】 / 平脉【木/火】\n"
            "🧷 脉稳: 63/100 (脉象平稳) | ☁️ 浊息/紊乱: 26/16\n"
            "📜 今日定脉令: 6/6 | 冲脉 0/2\n"
            "指令: .定脉 注灵 木 / .定脉 固脉 土 / .定脉 净浊 / .定脉 冲脉 火"
        )
        parsed = tree.parse_tree_pulse_panel(panel)

        self.assertIsNotNone(parsed)
        self.assertEqual(20.63, parsed["progress"])
        self.assertEqual("水", parsed["main"])
        self.assertEqual("土", parsed["aux"])
        self.assertEqual("金", parsed["reverse"])
        self.assertEqual(["木", "火"], parsed["neutral_elements"])
        self.assertEqual(63, parsed["stability"])
        self.assertEqual(100, parsed["stability_max"])
        self.assertEqual(26, parsed["turbidity"])
        self.assertEqual(16, parsed["turbidity_max"])
        self.assertEqual(6, parsed["daily_used"])
        self.assertEqual(6, parsed["daily_limit"])

        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            with (
                patch.object(tree, "send_audit_log", new=AsyncMock()),
                patch.object(tree, "save_state"),
            ):
                handled = await tree.handle_tree_panel(panel, now, True)

            self.assertTrue(handled)
            self.assertEqual("今日定脉令已满", state_module.state["tree_pulse_last_error"])
            status_text = tree.get_tree_status_text()

        self.assertIn("当前玩法：云梦灵眼定脉", status_text)
        self.assertIn("进度：20.63%", status_text)
        self.assertIn("今日定脉：6/6", status_text)
        self.assertIn("脉稳：63/100；浊息/紊乱：26/16", status_text)

    async def test_irrigation_success_reply_confirms_next_time_from_real_receipt(self):
        now = 2000.0
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")
        reply_to = SimpleNamespace(id=9911628, raw_text=".灵树灌溉")
        text = (
            "【🌿 灵树灌溉】\n"
            "当前环境: 生机萎靡 (需 木/森/草)\n"
            "你注入了: 木行 灵气\n"
            "🌳 成熟度: 72.93% -> 73.08%"
        )
        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            state_module.state["next_irr_time"] = now + 60

            with (
                patch.object(tree, "_next_irrigation_delay", return_value=7200),
                patch.object(tree, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(tree, "save_state"),
            ):
                handled = await tree.handle_tree_cd_fix(text, now, reply_to, matched_family="tree_panel")

            self.assertTrue(handled)
            self.assertEqual(now + 7200, state_module.state["next_irr_time"])
            audit_mock.assert_awaited_once()

    async def test_passive_guard_success_does_not_end_invasion(self):
        identity_id = 3756719391
        now = 1000.0
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="boxboxji")

        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            state_module.state["is_invading"] = True
            state_module.state["pending_irrigation"] = True
            state_module.state["next_irr_time"] = now + 3600
            state_module.state["next_guard_time"] = now + 1800

        text = "【守山成功】\n你向护山大阵注入灵力，暂时稳住了阵脚。"
        with patch.object(passive_inbox, "save_state"), patch.object(passive_inbox, "_save_passive_stats"):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={"send_as_id": identity_id, "family": "tree_guard"},
            )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertTrue(state_module.state["is_invading"])
            self.assertTrue(state_module.state["pending_irrigation"])
            self.assertEqual(now + 3600, state_module.state["next_irr_time"])
            self.assertEqual(now + 1800, state_module.state["next_guard_time"])
