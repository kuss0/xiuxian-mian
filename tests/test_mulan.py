import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import action_guard
from model import state as state_module
from model.features import mulan


class MulanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_identity(self, identity_id=8659059191):
        state_module.ensure_identity_registered(identity_id)
        state_module._meta_state["identity_states"][int(identity_id)] = state_module.new_identity_state()
        state_module.set_identity_account(identity_id, identity_id)
        return identity_id

    def test_parse_collect_ids_defaults_to_123(self):
        self.assertEqual([1, 2, 3], mulan.parse_mulan_report_ids("【慕兰军报】今日有三份军报。"))

    def test_parse_collect_ids_from_numbered_lines(self):
        self.assertEqual(
            [2, 7, 9],
            mulan.parse_mulan_report_ids("2. 西线军报\n7号 南线军报\n编号9：北线军报"),
        )

    def test_judgement_classifier_is_conservative(self):
        self.assertEqual("reliable", mulan.classify_mulan_judgement("研判较高，情报可靠，可公开。"))
        self.assertEqual("reliable", mulan.classify_mulan_judgement("稳定 较高：前线线索可信。"))
        self.assertEqual("suspicious", mulan.classify_mulan_judgement("研判可疑，不可靠。"))
        self.assertEqual("suspicious", mulan.classify_mulan_judgement("稳定 可疑：疑点较多。"))
        self.assertEqual("unknown", mulan.classify_mulan_judgement("长老沉吟不语。"))

    def test_fixed_report_examples_have_direct_verdicts_and_routes(self):
        now = 1_700_000_000.0
        reliable_examples = {
            "今夜圣灯换焰，主灯会短暂离开护灯法士三十息": "破灯",
            "边境粮道将过西岭，阵师缺人护送一批阵旗": "护阵",
            "法士营北帐换防，附灵蛇胆与妖丹暂存在同一灵袋": "奇袭",
            "有小股法士借草沟绕行，似在寻找黄龙山外阵缺口": "斥候",
        }
        for report_text, action in reliable_examples.items():
            with self.subTest(report_text=report_text):
                intel = mulan._known_mulan_intel(report_text, now)
                self.assertEqual("reliable", intel.get("verdict"))
                self.assertEqual(action, intel.get("support_action"))

        suspicious_examples = (
            "黄龙阵旗已全部撤回，护阵路线今日无事",
            "圣灯已熄，只需正面冲阵便可夺灯",
            "慕兰主力已退三百里，草原前线今日几乎无兵",
            "南营无人防守，所有法士都在主帐议事",
        )
        for report_text in suspicious_examples:
            with self.subTest(report_text=report_text):
                intel = mulan._known_mulan_intel(report_text, now)
                self.assertEqual("suspicious", intel.get("verdict"))
                self.assertEqual("", intel.get("support_action"))

    def test_action_guard_resolves_mulan_commands(self):
        self.assertEqual("mulan_collect", action_guard.resolve_action_key(".搜集军报"))
        self.assertEqual("mulan_collect", action_guard.resolve_action_key(".慕兰谍影"))
        self.assertEqual("mulan_collect", action_guard.resolve_action_key(".边境军功"))
        self.assertEqual("mulan_judge", action_guard.resolve_action_key(".辨报 2"))
        self.assertEqual("mulan_publish", action_guard.resolve_action_key(".公开军报 2"))
        self.assertEqual("mulan_support", action_guard.resolve_action_key(".支援慕兰 奇袭"))

    async def test_scheduler_starts_with_collect_command(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["next_mulan_time"] = now - 1
            fake_msg = SimpleNamespace(id=1001, sent_at=now)
            with (
                patch.object(mulan, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(mulan, "save_state"),
            ):
                await mulan.run_mulan_scheduler(now)

            send_mock.assert_awaited_once_with(
                ".搜集军报",
                track=False,
                max_retry=0,
                source_module="慕兰烽烟",
                queue_timeout=mulan.MULAN_SEND_QUEUE_TIMEOUT_SEC,
            )
            self.assertEqual("collect_pending", state_module.state["mulan_phase"])
            self.assertEqual(1001, state_module.state["mulan_reply_to_msg_id"])
            self.assertEqual(now + mulan.MULAN_REPLY_TIMEOUT_SEC, state_module.state["mulan_reply_due_at"])

    def test_initial_recovery_uses_wide_stagger_to_avoid_midnight_queue_burst(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            with (
                patch.object(mulan.random, "uniform", return_value=mulan.MULAN_RECOVERY_MAX_SEC) as uniform_mock,
                patch.object(mulan, "mark_dirty"),
            ):
                due_at = mulan.schedule_mulan_initial_check(now, persist=False)

            uniform_mock.assert_called_once_with(mulan.MULAN_RECOVERY_MIN_SEC, mulan.MULAN_RECOVERY_MAX_SEC)
            self.assertEqual(now + mulan.MULAN_RECOVERY_MAX_SEC, due_at)
            self.assertEqual(now + mulan.MULAN_RECOVERY_MAX_SEC, state_module.state["next_mulan_time"])

    async def test_scheduler_disables_when_identity_has_no_account_mapping(self):
        identity_id = 3711993781
        state_module.ensure_identity_registered(identity_id)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["next_mulan_time"] = now - 1
            with (
                patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                await mulan.run_mulan_scheduler(now)

            send_mock.assert_not_awaited()
            audit_mock.assert_awaited_once()
            self.assertFalse(state_module.state["mulan_enabled"])
            self.assertEqual("idle", state_module.state["mulan_phase"])
            self.assertEqual(0, state_module.state["next_mulan_time"])
            self.assertIn("未绑定账号", state_module.state["mulan_last_error"])

    async def test_suspicious_judgement_stops_same_identity_and_uses_text_support(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        sent_commands = []

        async def fake_send(command, **kwargs):
            sent_commands.append((command, kwargs))
            return SimpleNamespace(id=2000 + len(sent_commands), sent_at=now + len(sent_commands))

        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_reply_to_msg_id"] = 1001
            state_module.state["mulan_phase"] = "collect_pending"
            with (
                patch.object(mulan, "send_game_command", new=fake_send),
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                handled = await mulan.handle_mulan_reply(
                    "【军报】\n1. 东线\n2. 西线\n3. 北线",
                    now,
                    reply_to=SimpleNamespace(id=1001, raw_text=".搜集军报"),
                    matched_family="mulan_collect",
                    result_msg_id=1002,
                )
                self.assertTrue(handled)

                await mulan.run_mulan_scheduler(now + 1)
                self.assertEqual(".辨报 1", sent_commands[-1][0])
                self.assertEqual(1, state_module.state["mulan_current_id"])

                handled = await mulan.handle_mulan_reply(
                    "1号军报研判可疑，疑点较多。",
                    now + 2,
                    reply_to=SimpleNamespace(id=2001, raw_text=".辨报 1"),
                    matched_family="mulan_judge",
                    result_msg_id=2002,
                )
                self.assertTrue(handled)
                self.assertEqual("ready_to_support", state_module.state["mulan_phase"])
                self.assertEqual("", state_module.state["mulan_last_error"])
                self.assertEqual("护阵", state_module.state["mulan_support_action"])

                await mulan.run_mulan_scheduler(now + 3)
                self.assertEqual(".支援慕兰 护阵", sent_commands[-1][0])

            self.assertEqual([".辨报 1", ".支援慕兰 护阵"], [command for command, _ in sent_commands])
            self.assertNotIn(".辨报 2", [command for command, _ in sent_commands])

    async def test_late_judge_reply_does_not_clobber_current_support_pending(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = mulan.MULAN_PHASE_SUPPORT_PENDING
            state_module.state["mulan_reply_to_msg_id"] = 3001
            state_module.state["mulan_reply_due_at"] = now + 120
            state_module.state["mulan_current_id"] = 0
            state_module.state["mulan_pending_ids"] = "2,3"
            state_module.state["mulan_support_action"] = "护阵"

            with patch.object(mulan, "save_state"):
                handled = await mulan.handle_mulan_reply(
                    "1号军报研判较高，情报可靠，可公开。",
                    now,
                    reply_to=SimpleNamespace(id=2001, raw_text=".辨报 1"),
                    matched_family="mulan_judge",
                    result_msg_id=2002,
                )

            self.assertTrue(handled)
            self.assertEqual(mulan.MULAN_PHASE_SUPPORT_PENDING, state_module.state["mulan_phase"])
            self.assertEqual(3001, state_module.state["mulan_reply_to_msg_id"])
            self.assertEqual("2,3", state_module.state["mulan_pending_ids"])
            self.assertEqual("护阵", state_module.state["mulan_support_action"])
            self.assertIn("不匹配", state_module.state["mulan_last_error"])

    async def test_shared_reliable_report_publishes_without_judging(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        sent_commands = []

        async def fake_send(command, **kwargs):
            sent_commands.append(command)
            return SimpleNamespace(id=3000 + len(sent_commands), sent_at=now + len(sent_commands))

        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "ready_to_judge"
            state_module.state["mulan_pending_ids"] = "1,2"
            state_module.state["mulan_report_texts"] = {"1": "有小股法士借草沟绕行，似在寻找黄龙山外阵缺口。", "2": "南营无人防守。"}
            mulan._record_mulan_intel(
                "有小股法士借草沟绕行，似在寻找黄龙山外阵缺口。",
                "reliable",
                now,
                report_id=1,
                support_action="斥候",
            )
            with (
                patch.object(mulan, "send_game_command", new=fake_send),
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                await mulan.run_mulan_scheduler(now + 1)
                self.assertEqual("ready_to_publish", state_module.state["mulan_phase"])
                self.assertEqual([], sent_commands)
                await mulan.run_mulan_scheduler(now + 2)

            self.assertEqual([".公开军报 1"], sent_commands)
            self.assertEqual("publish_pending", state_module.state["mulan_phase"])

    async def test_fixed_reliable_report_publishes_without_judging(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        sent_commands = []

        async def fake_send(command, **kwargs):
            sent_commands.append(command)
            return SimpleNamespace(id=3000 + len(sent_commands), sent_at=now + len(sent_commands))

        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "ready_to_judge"
            state_module.state["mulan_pending_ids"] = "1,2"
            state_module.state["mulan_report_texts"] = {
                "1": "圣灯已熄，只需正面冲阵便可夺灯。",
                "2": "法士营北帐换防，附灵蛇胆与妖丹暂存在同一灵袋。",
            }
            with (
                patch.object(mulan, "send_game_command", new=fake_send),
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                await mulan.run_mulan_scheduler(now + 1)
                self.assertEqual("ready_to_publish", state_module.state["mulan_phase"])
                self.assertEqual(2, state_module.state["mulan_public_id"])
                self.assertEqual([], sent_commands)
                await mulan.run_mulan_scheduler(now + 2)

            self.assertEqual([".公开军报 2"], sent_commands)
            self.assertEqual("publish_pending", state_module.state["mulan_phase"])

    async def test_shared_suspicious_reports_skip_to_text_support_without_judging(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        sent_commands = []

        async def fake_send(command, **kwargs):
            sent_commands.append(command)
            return SimpleNamespace(id=3000 + len(sent_commands), sent_at=now + len(sent_commands))

        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "ready_to_judge"
            state_module.state["mulan_pending_ids"] = "1,2"
            state_module.state["mulan_report_texts"] = {"1": "南营无人防守。", "2": "圣灯已熄，只需正面冲阵便可夺灯。"}
            mulan._record_mulan_intel("南营无人防守。", "suspicious", now, report_id=1)
            mulan._record_mulan_intel("圣灯已熄，只需正面冲阵便可夺灯。", "suspicious", now, report_id=2)
            with (
                patch.object(mulan, "send_game_command", new=fake_send),
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                await mulan.run_mulan_scheduler(now + 1)
                self.assertEqual("ready_to_support", state_module.state["mulan_phase"])
                self.assertEqual("护阵", state_module.state["mulan_support_action"])
                self.assertEqual([], sent_commands)
                await mulan.run_mulan_scheduler(now + 2)

            self.assertEqual([".支援慕兰 护阵"], sent_commands)

    async def test_fixed_suspicious_reports_do_not_drive_risky_fallback(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        sent_commands = []

        async def fake_send(command, **kwargs):
            sent_commands.append(command)
            return SimpleNamespace(id=3000 + len(sent_commands), sent_at=now + len(sent_commands))

        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "ready_to_judge"
            state_module.state["mulan_pending_ids"] = "1,2"
            state_module.state["mulan_report_texts"] = {
                "1": "圣灯已熄，只需正面冲阵便可夺灯。",
                "2": "南营无人防守，所有法士都在主帐议事。",
            }
            with (
                patch.object(mulan, "send_game_command", new=fake_send),
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                await mulan.run_mulan_scheduler(now + 1)
                self.assertEqual("ready_to_support", state_module.state["mulan_phase"])
                self.assertEqual("护阵", state_module.state["mulan_support_action"])
                await mulan.run_mulan_scheduler(now + 2)

            self.assertEqual([".支援慕兰 护阵"], sent_commands)

    async def test_limited_judgement_is_not_runtime_error(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "judge_pending"
            state_module.state["mulan_current_id"] = 2
            state_module.state["mulan_pending_ids"] = "1,2,3"
            state_module.state["mulan_report_texts"] = {"2": "未知军报。", "3": "法士营北帐换防。"}
            with (
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                handled = await mulan.handle_mulan_reply(
                    "【辨报受限】\n今日神识只够细辨一条军报。你已辨过其他军报，剩余消息只能凭文本线索自行判断。",
                    now,
                    reply_to=SimpleNamespace(id=3001, raw_text=".辨报 2"),
                    matched_family="mulan_judge",
                    result_msg_id=3002,
                )

            self.assertTrue(handled)
            self.assertEqual("ready_to_support", state_module.state["mulan_phase"])
            self.assertEqual("奇袭", state_module.state["mulan_support_action"])
            self.assertEqual("", state_module.state["mulan_last_error"])

    async def test_support_timeout_finishes_cycle_without_panel_retry(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "support_pending"
            state_module.state["mulan_reply_to_msg_id"] = 888
            state_module.state["mulan_reply_due_at"] = now - 1
            with (
                patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(mulan.random, "uniform", return_value=0),
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                await mulan.run_mulan_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("cooldown", state_module.state["mulan_phase"])
            self.assertEqual(0, state_module.state["mulan_reply_to_msg_id"])
            self.assertIn("支援结果超时", state_module.state["mulan_last_result"])
            self.assertEqual("", state_module.state["mulan_last_error"])
            self.assertGreater(state_module.state["next_mulan_time"], now)

    async def test_ready_to_panel_from_legacy_support_timeout_finishes_cycle(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "ready_to_panel"
            state_module.state["mulan_last_command"] = ".支援慕兰 破灯"
            state_module.state["mulan_last_result"] = "support_pending 无回复，准备面板校准"
            state_module.state["mulan_support_action"] = "破灯"
            state_module.state["next_mulan_time"] = now - 1
            with (
                patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(mulan.random, "uniform", return_value=0),
                patch.object(mulan, "save_state"),
            ):
                await mulan.run_mulan_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("cooldown", state_module.state["mulan_phase"])
            self.assertIn("支援校准旧状态已收束", state_module.state["mulan_last_result"])
            self.assertEqual("", state_module.state["mulan_last_error"])

    async def test_shadow_report_panel_is_parsed_as_reports_not_support_panel(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "collect_pending"
            with (
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                handled = await mulan.handle_mulan_reply(
                    "【慕兰谍影】\n日期：2026-07-01\n状态：待公开\n\n军报匣\n1. 南营无人防守，所有法士都在主帐议事。（未辨）\n2. 有小股法士借草沟绕行，似在寻找黄龙山外阵缺口。（未辨）",
                    now,
                    reply_to=SimpleNamespace(id=3001, raw_text=".慕兰谍影"),
                    matched_family="mulan_panel",
                    result_msg_id=3002,
                )

            self.assertTrue(handled)
            self.assertEqual("ready_to_judge", state_module.state["mulan_phase"])
            self.assertEqual("1,2", state_module.state["mulan_pending_ids"])
            self.assertEqual("南营无人防守，所有法士都在主帐议事", state_module.state["mulan_report_texts"]["1"])

    async def test_true_publish_flows_into_support(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        sent_commands = []

        async def fake_send(command, **kwargs):
            sent_commands.append(command)
            return SimpleNamespace(id=4000 + len(sent_commands), sent_at=now + len(sent_commands))

        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "publish_pending"
            state_module.state["mulan_public_id"] = 2
            state_module.state["mulan_public_text"] = "法士营北帐换防，附灵蛇胆与妖丹暂存在同一灵袋。"
            with (
                patch.object(mulan, "send_game_command", new=fake_send),
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                handled = await mulan.handle_mulan_reply(
                    "【慕兰谍影·真报】\n前线采信了你的军报：法士营北帐换防，附灵蛇胆与妖丹暂存在同一灵袋。\n\n获得边境军功 +2。\n今日支援【夜袭法士营】时，将获得情报助力。",
                    now,
                    reply_to=SimpleNamespace(id=4001, raw_text=".公开军报 2"),
                    matched_family="mulan_publish",
                    result_msg_id=4002,
                )
                self.assertTrue(handled)
                self.assertEqual("ready_to_support", state_module.state["mulan_phase"])
                self.assertEqual("奇袭", state_module.state["mulan_support_action"])

                await mulan.run_mulan_scheduler(now + 1)
                self.assertEqual(".支援慕兰 奇袭", sent_commands[-1])
                support_msg_id = state_module.state["mulan_reply_to_msg_id"]

                handled = await mulan.handle_mulan_reply(
                    "【慕兰烽烟】\n@x 领了【夜袭法士营】之令，正赶往天南边境...",
                    now + 2,
                    reply_to=SimpleNamespace(id=support_msg_id, raw_text=".支援慕兰 奇袭"),
                    matched_family="mulan_support",
                    result_msg_id=4004,
                )
                self.assertTrue(handled)
                self.assertEqual("support_pending", state_module.state["mulan_phase"])

                handled = await mulan.handle_mulan_reply(
                    "【慕兰烽烟 · 夜袭法士营】小胜\n获得修为 +415\n边境军功 +3，累计 5\n连续支援 1 天",
                    now + 3,
                    reply_to=SimpleNamespace(id=support_msg_id, raw_text=".支援慕兰 奇袭"),
                    matched_family="mulan_support",
                    result_msg_id=4004,
                )
                self.assertTrue(handled)

            self.assertEqual("cooldown", state_module.state["mulan_phase"])
            self.assertEqual("", state_module.state["mulan_last_error"])

    async def test_timeout_clears_pending_and_schedules_retry(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "collect_pending"
            state_module.state["mulan_reply_to_msg_id"] = 999
            state_module.state["mulan_reply_due_at"] = now - 1
            with (
                patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                await mulan.run_mulan_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(0, state_module.state["mulan_reply_to_msg_id"])
            self.assertEqual("", state_module.state["mulan_last_error"])
            self.assertEqual("idle", state_module.state["mulan_phase"])
            self.assertIn("搜集无回复", state_module.state["mulan_last_result"])
            self.assertGreater(state_module.state["next_mulan_time"], now)

    async def test_timeout_recovers_collect_reply_from_message_log(self):
        identity_id = self._prepare_identity()
        now = datetime(2026, 7, 4, 6, 45, tzinfo=mulan.TZ_LOCAL).timestamp()
        report_text = "\n".join([
            "【慕兰谍影】",
            "今日军报匣：",
            "1. 今夜圣灯换焰，主灯会短暂离开护灯法士三十息",
            "2. 黄龙阵旗已全部撤回，护阵路线今日无事",
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-07-04.log"
            log_path.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "ts": "2026-07-04 06:44:40 UTC+8",
                            "event_type": "message",
                            "message_id": 1001,
                            "sender_id": identity_id,
                            "reply_to_msg_id": 0,
                            "text": ".搜集军报",
                        },
                        {
                            "ts": "2026-07-04 06:44:42 UTC+8",
                            "event_type": "message",
                            "message_id": 1002,
                            "sender_id": 8609885831,
                            "reply_to_msg_id": 1001,
                            "text": report_text,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            with state_module.use_identity(identity_id):
                state_module.state["mulan_enabled"] = True
                state_module.state["mulan_phase"] = "collect_pending"
                state_module.state["mulan_reply_to_msg_id"] = 1001
                state_module.state["mulan_reply_due_at"] = now - 1
                state_module.state["mulan_last_command"] = ".搜集军报"
                with (
                    patch("model.message_log_recovery.MESSAGES_DIR", tmpdir),
                    patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                    patch.object(mulan, "save_state"),
                    patch.object(mulan, "send_audit_log", new=AsyncMock()),
                ):
                    await mulan.run_mulan_scheduler(now)

                send_mock.assert_not_awaited()
                self.assertEqual("ready_to_judge", state_module.state["mulan_phase"])
                self.assertEqual(0, state_module.state["mulan_reply_to_msg_id"])
                self.assertEqual("1,2", state_module.state["mulan_pending_ids"])

    async def test_stale_collect_pending_without_anchor_recovers_without_send(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "collect_pending"
            state_module.state["mulan_reply_to_msg_id"] = 0
            state_module.state["mulan_reply_due_at"] = 0
            state_module.state["mulan_last_error"] = "collect_pending 回复超时"
            state_module.state["next_mulan_time"] = now + 600
            with (
                patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                await mulan.run_mulan_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["mulan_phase"])
            self.assertEqual("", state_module.state["mulan_last_error"])
            self.assertEqual("搜集无回复，等待重试", state_module.state["mulan_last_result"])
            self.assertEqual(now + 600, state_module.state["next_mulan_time"])

    async def test_phaseful_risk_defers_send_without_pending(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["next_mulan_time"] = now - 1
            with (
                patch.object(mulan, "get_phaseful_summary_risk_reason", return_value="深度闭关临近归位结算"),
                patch.object(mulan.random, "uniform", return_value=90),
                patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(mulan, "save_state"),
            ):
                await mulan.run_mulan_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(0, state_module.state["mulan_reply_to_msg_id"])
            self.assertEqual(now + 90, state_module.state["next_mulan_time"])
            self.assertEqual("", state_module.state["mulan_last_error"])
            self.assertIn("延后发送", state_module.state["mulan_last_result"])

    async def test_ready_support_respects_deferred_next_time(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "ready_to_support"
            state_module.state["mulan_support_action"] = "护阵"
            state_module.state["next_mulan_time"] = now + 300
            with (
                patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(mulan, "get_phaseful_summary_risk_reason", return_value="深度闭关临近归位结算"),
                patch.object(mulan, "save_state"),
            ):
                await mulan.run_mulan_scheduler(now + 1)

            send_mock.assert_not_awaited()
            self.assertEqual("ready_to_support", state_module.state["mulan_phase"])
            self.assertEqual(now + 300, state_module.state["next_mulan_time"])

    async def test_global_send_block_uses_identity_without_name_error(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["next_mulan_time"] = now - 1
            with (
                patch.object(mulan, "get_phaseful_summary_risk_reason", return_value=""),
                patch.object(mulan, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(mulan, "was_last_game_send_blocked_by_global", return_value=True) as blocked_mock,
                patch.object(mulan.random, "uniform", return_value=600),
                patch.object(mulan, "save_state"),
            ):
                await mulan.run_mulan_scheduler(now)

            blocked_mock.assert_called_once_with(identity_id, ".搜集军报")
            self.assertEqual("全局暂停，等待恢复错峰", state_module.state["mulan_last_result"])
            self.assertEqual("", state_module.state["mulan_last_error"])
            self.assertEqual(now + 600, state_module.state["next_mulan_time"])

    async def test_send_queue_timeout_uses_staggered_retry(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["next_mulan_time"] = now - 1
            with (
                patch.object(mulan, "get_phaseful_summary_risk_reason", return_value=""),
                patch.object(mulan, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(mulan, "was_last_game_send_blocked_by_global", return_value=False),
                patch.object(mulan, "get_last_game_send_block", return_value={"code": "send_queue_timeout"}),
                patch.object(mulan.random, "uniform", return_value=180),
                patch.object(mulan, "save_state"),
            ):
                await mulan.run_mulan_scheduler(now)

            self.assertEqual("发送队列拥挤，慕兰错峰重试", state_module.state["mulan_last_result"])
            self.assertEqual("", state_module.state["mulan_last_error"])
            self.assertEqual(now + 180, state_module.state["next_mulan_time"])

    async def test_publish_uses_critical_send_queue_timeout(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        fake_msg = SimpleNamespace(id=1101, sent_at=now)
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = mulan.MULAN_PHASE_READY_TO_PUBLISH
            state_module.state["mulan_public_id"] = 2
            state_module.state["next_mulan_time"] = now - 1
            with (
                patch.object(mulan, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(mulan, "save_state"),
            ):
                await mulan.run_mulan_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(".公开军报 2", send_mock.await_args.args[0])
            self.assertEqual(mulan.MULAN_CRITICAL_SEND_QUEUE_TIMEOUT_SEC, send_mock.await_args.kwargs["queue_timeout"])

    async def test_cd_reply_uses_real_wait_text(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            with (
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                handled = await mulan.handle_mulan_reply(
                    "军报尚未整理，请在 1小时2分钟3秒 后再试。",
                    now,
                    reply_to=SimpleNamespace(id=1001, raw_text=".搜集军报"),
                    matched_family="mulan_collect",
                    result_msg_id=1002,
                )

            self.assertTrue(handled)
            self.assertEqual(now + 3723 + mulan.CD_BUFFER_SEC, state_module.state["next_mulan_time"])
            self.assertEqual("冷却中", state_module.state["mulan_last_result"])

    async def test_daily_done_reply_moves_to_support_without_judging(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "collect_pending"
            state_module.state["mulan_reply_to_msg_id"] = 1001
            state_module.state["mulan_reply_due_at"] = now + 60
            action_guard.note_sent(".搜集军报", identity_id, 1001, sent_at=now - 10)
            self.assertIn("mulan_collect", state_module.state["action_guard_sessions"])

            with (
                patch.object(mulan.random, "uniform", return_value=60),
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                handled = await mulan.handle_mulan_reply(
                    "今日军报已经提交，不可重复公开。",
                    now,
                    reply_to=SimpleNamespace(id=1001, raw_text=".搜集军报"),
                    matched_family="mulan_collect",
                    result_msg_id=1002,
                )

            self.assertTrue(handled)
            self.assertEqual("ready_to_support", state_module.state["mulan_phase"])
            self.assertIn("军报已处理", state_module.state["mulan_last_result"])
            self.assertEqual("护阵", state_module.state["mulan_support_action"])
            self.assertEqual(0, state_module.state["mulan_current_id"])
            self.assertNotIn("mulan_collect", state_module.state["action_guard_sessions"])

            with (
                patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(mulan, "save_state"),
            ):
                await mulan.run_mulan_scheduler(now + 1)

            send_mock.assert_awaited_once()
            self.assertEqual(".支援慕兰 护阵", send_mock.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
