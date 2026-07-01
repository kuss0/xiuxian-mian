import copy
import sys
import unittest
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
        self.assertEqual("suspicious", mulan.classify_mulan_judgement("研判可疑，不可靠。"))
        self.assertEqual("unknown", mulan.classify_mulan_judgement("长老沉吟不语。"))

    def test_action_guard_resolves_mulan_commands(self):
        self.assertEqual("mulan_collect", action_guard.resolve_action_key(".搜集军报"))
        self.assertEqual("mulan_collect", action_guard.resolve_action_key(".慕兰谍影"))
        self.assertEqual("mulan_judge", action_guard.resolve_action_key(".辨报 2"))
        self.assertEqual("mulan_publish", action_guard.resolve_action_key(".公开军报 2"))

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

            send_mock.assert_awaited_once_with(".搜集军报", track=False, max_retry=0, source_module="慕兰")
            self.assertEqual("collect_pending", state_module.state["mulan_phase"])
            self.assertEqual(1001, state_module.state["mulan_reply_to_msg_id"])
            self.assertEqual(now + mulan.MULAN_REPLY_TIMEOUT_SEC, state_module.state["mulan_reply_due_at"])

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

    async def test_suspicious_then_reliable_publishes_and_stops_judging(self):
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

                await mulan.run_mulan_scheduler(now + 3)
                self.assertEqual(".辨报 2", sent_commands[-1][0])

                handled = await mulan.handle_mulan_reply(
                    "2号军报研判较高，情报可靠，可以公开。",
                    now + 4,
                    reply_to=SimpleNamespace(id=2002, raw_text=".辨报 2"),
                    matched_family="mulan_judge",
                    result_msg_id=2003,
                )
                self.assertTrue(handled)
                self.assertEqual("ready_to_publish", state_module.state["mulan_phase"])
                self.assertEqual(2, state_module.state["mulan_public_id"])

                await mulan.run_mulan_scheduler(now + 5)

            self.assertEqual([".辨报 1", ".辨报 2", ".公开军报 2"], [command for command, _ in sent_commands])
            self.assertNotIn(".辨报 3", [command for command, _ in sent_commands])

    async def test_all_suspicious_finishes_without_publish(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        sent_commands = []

        async def fake_send(command, **kwargs):
            sent_commands.append(command)
            return SimpleNamespace(id=3000 + len(sent_commands), sent_at=now + len(sent_commands))

        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "ready_to_judge"
            state_module.state["mulan_pending_ids"] = "1,2,3"
            with (
                patch.object(mulan, "send_game_command", new=fake_send),
                patch.object(mulan, "save_state"),
                patch.object(mulan, "send_audit_log", new=AsyncMock()),
            ):
                for report_id in (1, 2, 3):
                    await mulan.run_mulan_scheduler(now + report_id)
                    self.assertEqual(f".辨报 {report_id}", sent_commands[-1])
                    handled = await mulan.handle_mulan_reply(
                        f"{report_id}号军报研判可疑。",
                        now + report_id + 0.5,
                        reply_to=SimpleNamespace(id=3000 + report_id, raw_text=f".辨报 {report_id}"),
                        matched_family="mulan_judge",
                        result_msg_id=3100 + report_id,
                    )
                    self.assertTrue(handled)

            self.assertEqual([".辨报 1", ".辨报 2", ".辨报 3"], sent_commands)
            self.assertEqual("cooldown", state_module.state["mulan_phase"])
            self.assertIn("未公开", state_module.state["mulan_last_result"])

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
            self.assertIn("回复超时", state_module.state["mulan_last_error"])
            self.assertGreater(state_module.state["next_mulan_time"], now)

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

    async def test_daily_done_reply_finishes_without_judging(self):
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
            self.assertEqual("cooldown", state_module.state["mulan_phase"])
            self.assertEqual("今日已完成", state_module.state["mulan_last_result"])
            self.assertEqual("", state_module.state["mulan_pending_ids"])
            self.assertEqual(0, state_module.state["mulan_current_id"])
            self.assertNotIn("mulan_collect", state_module.state["action_guard_sessions"])

            with (
                patch.object(mulan, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(mulan, "save_state"),
            ):
                await mulan.run_mulan_scheduler(now + 1)

            send_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
