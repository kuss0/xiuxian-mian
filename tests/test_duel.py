import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import duel


class DuelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_identity(self, identity_id=8659059191, *, realm="元婴后期", xiuwei_current=700000):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(
            identity_id,
            username="walterwa2000",
            realm=realm,
            xiuwei_current=xiuwei_current,
        )
        return identity_id

    def test_target_normalization_and_command(self):
        self.assertEqual("@cupaopao", duel.normalize_duel_target("cupaopao"))
        self.assertEqual("@cupaopao", duel.normalize_duel_target("@cupaopao extra"))
        self.assertEqual("8398842598", duel.normalize_duel_target("8398842598"))
        self.assertEqual(".斗法 @cupaopao", duel.build_duel_command("@cupaopao"))

    async def test_scheduler_blocks_realm_and_xiuwei_gate_without_sending(self):
        identity_id = self._prepare_identity(realm="元婴中期", xiuwei_current=900000)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertIn("境界需为元婴后期", state_module.state["duel_last_error"])

        identity_id = self._prepare_identity(8659059192, realm="元婴后期", xiuwei_current=600000)
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertIn("修为需 >600000", state_module.state["duel_last_error"])

    async def test_scheduler_sends_duel_command_when_gate_passes(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(duel, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_awaited_once_with(".斗法 @cupaopao", track=False, max_retry=0, source_module="斗法")
            self.assertEqual(22027, state_module.state["duel_reply_to_msg_id"])
            self.assertEqual(now + duel.DUEL_REPLY_TIMEOUT_SEC, state_module.state["duel_reply_due_at"])
            self.assertEqual("已发送", state_module.state["duel_last_result"])

    async def test_scheduler_prepares_tianxing_before_future_duel_window(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        due_at = now + 120
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = due_at
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "",
                "current_prediction": "",
                "tianji_value": 9,
            }
            state_module.state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "strategy_dry_run_enabled": False,
                "duel_route_enabled": True,
                "min_tianji_for_change": 6,
                "route_prepare_lead_sec": 300,
            }

            with (
                patch.object(duel, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True})) as timeline_mock,
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            timeline_mock.assert_awaited_once()
            window = timeline_mock.await_args.kwargs["windows"][0]
            self.assertEqual("斗法", window["route"])
            self.assertEqual("consume", window["kind"])
            send_mock.assert_not_awaited()
            self.assertEqual(due_at, state_module.state["next_duel_time"])
            self.assertEqual("天星时间线：sent_waiting_ack", state_module.state["duel_last_result"])

    async def test_scheduler_requests_tianxing_timeline_before_due_duel(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "",
                "current_prediction": "",
                "tianji_value": 9,
            }
            state_module.state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "strategy_dry_run_enabled": False,
                "duel_route_enabled": True,
                "min_tianji_for_change": 6,
            }

            with (
                patch.object(duel, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True})) as timeline_mock,
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel.random, "uniform", return_value=duel.DUEL_RECOVERY_MIN_SEC),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            timeline_mock.assert_awaited_once()
            self.assertEqual("斗法", timeline_mock.await_args.kwargs["windows"][0]["route"])
            send_mock.assert_not_awaited()
            self.assertEqual(now + duel.DUEL_RECOVERY_MIN_SEC, state_module.state["next_duel_time"])
            self.assertEqual("天星时间线：sent_waiting_ack", state_module.state["duel_last_result"])

    async def test_scheduler_sends_duel_when_tianxing_timeline_released(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "斗法",
                "current_change_until": now + 3600,
                "current_prediction": "斗法",
                "current_prediction_until": now + 1800,
                "tianji_value": 9,
            }
            state_module.state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "strategy_dry_run_enabled": False,
                "duel_route_enabled": True,
            }
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "斗法": {"released_at": now - 5, "plan_id": "test", "reason": "confirmed"},
                },
            }

            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(duel, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(duel, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_awaited_once_with(".斗法 @cupaopao", track=False, max_retry=0, source_module="斗法")
            self.assertEqual(22027, state_module.state["duel_reply_to_msg_id"])

    async def test_scheduler_does_not_insert_tianxing_duel_route_by_default(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "",
                "current_prediction": "",
                "tianji_value": 9,
            }
            state_module.state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "strategy_dry_run_enabled": False,
            }

            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(duel, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(duel, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_awaited_once_with(".斗法 @cupaopao", track=False, max_retry=0, source_module="斗法")
            self.assertEqual(22027, state_module.state["duel_reply_to_msg_id"])

    async def test_scheduler_blocks_duel_when_other_tianxing_prediction_active(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_prediction": "闭关",
                "current_prediction_until": now + 1800,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 9,
            }

            with (
                patch.object(duel, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_not_awaited()
            self.assertEqual(now + 1800 + duel.CD_BUFFER_SEC, state_module.state["next_duel_time"])
            self.assertIn("避免逆命", state_module.state["duel_last_error"])

    async def test_scheduler_blocks_without_positive_total_count(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 0
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("斗法次数未配置", state_module.state["duel_last_error"])

    async def test_progress_replies_extend_wait_without_private_followup(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    "⚔️ 法宝齐出！ ⚔️\n@walterwa2000 与 @cupaopao 战至酣处，天机阁正在推演战局...",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @cupaopao"),
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["duel_magic_due_at"])
            self.assertEqual(now + duel.DUEL_REPLY_TIMEOUT_SEC, state_module.state["duel_reply_due_at"])

            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now + 5)

            send_mock.assert_not_awaited()

    async def test_end_broadcast_counts_completion_and_disables_at_total(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 1
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            state_module.state["duel_started_at"] = now - 5
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_broadcast(
                    "【天道战报·文字版】\n@walterwa2000 与 @cupaopao 斗法结束。\n胜者：@cupaopao\n败者：@walterwa2000\n败者进入【虚弱状态】10分钟。",
                    now,
                    event=SimpleNamespace(id=22030),
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.state["duel_enabled"])
            self.assertEqual(1, state_module.state["duel_completed_count"])
            self.assertEqual("斗法结束，胜者 @cupaopao", state_module.state["duel_last_result"])

    async def test_broadcast_without_current_pending_is_ignored(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_broadcast(
                    "【天道战报·文字版】\n@walterwa2000 与 @cupaopao 斗法结束。\n胜者：@cupaopao\n败者：@walterwa2000",
                    now,
                    event=SimpleNamespace(id=22030),
                )

            self.assertFalse(handled)
            audit_mock.assert_not_awaited()
            self.assertEqual(0, state_module.state["duel_completed_count"])

    async def test_broadcast_outside_pending_window_is_ignored(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now - duel.DUEL_RESULT_GRACE_SEC - 1
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_broadcast(
                    "【天道战报·文字版】\n@walterwa2000 与 @cupaopao 斗法结束。\n胜者：@cupaopao\n败者：@walterwa2000",
                    now,
                    event=SimpleNamespace(id=22030),
                )

            self.assertFalse(handled)
            audit_mock.assert_not_awaited()
            self.assertEqual(0, state_module.state["duel_completed_count"])

    async def test_winning_report_uses_normal_cooldown_even_when_loser_is_weak(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    "【天道战报·文字版】\n@walterwa2000 与 @cupaopao 斗法结束。\n胜者：@walterwa2000\n败者：@cupaopao\n败者进入【虚弱状态】10分钟。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @cupaopao"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertEqual(now + duel.DUEL_NORMAL_COOLDOWN_SEC + duel.CD_BUFFER_SEC, state_module.state["next_duel_time"])
            self.assertEqual("", state_module.state["duel_last_error"])

    async def test_winner_not_self_without_loser_line_uses_long_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    "【天道战报·文字版】\n@walterwa2000 与 @cupaopao 斗法结束。\n胜者：@cupaopao",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @cupaopao"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertEqual(now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC + duel.CD_BUFFER_SEC, state_module.state["next_duel_time"])
            self.assertEqual("斗法结束，胜者 @cupaopao", state_module.state["duel_last_error"])

    async def test_failure_reply_uses_long_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    "元神尚未平复，无法再次斗法。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @cupaopao"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertEqual(now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC + duel.CD_BUFFER_SEC, state_module.state["next_duel_time"])
            self.assertEqual("元神尚未平复，无法再次斗法。", state_module.state["duel_last_error"])


if __name__ == "__main__":
    unittest.main()
