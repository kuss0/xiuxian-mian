import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import control
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

    async def test_manual_reenable_starts_a_new_completed_batch(self):
        identity_id = self._prepare_identity()
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = False
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_completed_count"] = 10
            state_module.state["next_duel_time"] = 0

        with patch.object(control, "save_state"):
            ok, _message = await control.set_module_enabled("斗法", True, send_as_id=identity_id)

        self.assertTrue(ok)
        with state_module.use_identity(identity_id):
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertGreater(state_module.state["next_duel_time"], 0)

    def test_manual_disable_cancels_tianxing_duel_timeline(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "current_prediction": "斗法",
                "current_prediction_until": now + 3600,
                "current_prediction_set_at": now - 10,
            }
            state_module.state["tianxing_auto_config"] = {"duel_route_enabled": True}
            state_module.state["tianxing_timeline_state"] = {
                "phase": "downstream_released",
                "route": "斗法",
                "active_step_index": 1,
                "active_step": {"route": "斗法", "status": "released"},
                "released_routes": {"斗法": {"released_at": now - 5}},
            }

            with patch.object(control, "_clear_pending_tasks_by_commands"):
                control._manual_disable_duel_module_state()

            self.assertFalse(state_module.state["duel_enabled"])
            observed = state_module.state["tianxing_observation"]
            self.assertEqual("", observed["current_prediction"])
            self.assertEqual("斗法", observed["prediction_cancelled_route"])
            self.assertFalse(state_module.state["tianxing_auto_config"]["duel_route_enabled"])
            timeline = state_module.state["tianxing_timeline_state"]
            self.assertEqual("blocked_replan", timeline["phase"])
            self.assertEqual({}, timeline["active_step"])
            self.assertNotIn("斗法", timeline["released_routes"])

    def test_target_normalization_and_command(self):
        self.assertEqual("@cupaopao", duel.normalize_duel_target("cupaopao"))
        self.assertEqual("@cupaopao", duel.normalize_duel_target("@cupaopao extra"))
        self.assertEqual("8398842598", duel.normalize_duel_target("8398842598"))
        self.assertEqual(["@cupaopao", "@hughpig", "8398842598"], duel.normalize_duel_targets("cupaopao,@hughpig 8398842598"))
        self.assertEqual(["@cupaopao"], duel.normalize_duel_targets("@cupaopao, cupaopao"))
        self.assertEqual(".斗法 @cupaopao", duel.build_duel_command("@cupaopao"))

    def test_controlled_loadout_requires_exact_current_equipment(self):
        self.assertTrue(duel._loadout_reply_matches("你已祭出【玄铁剑】。\n当前祭出：【玄铁剑】\n神识御宝：1/26", ("玄铁剑",)))
        self.assertFalse(duel._loadout_reply_matches("当前祭出：【玄铁剑】、【金光砖】", ("玄铁剑",)))

    def test_controlled_loadout_accepts_already_unequipped_reply(self):
        self.assertTrue(duel._loadout_unequip_reply("你当前并未祭出任何法宝。"))

    async def test_wa_controlled_loadout_confirms_only_xuantie_before_duel(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_last_result"] = "斗法配装:prepare"

            sent = [SimpleNamespace(id=1001, sent_at=now), SimpleNamespace(id=1002, sent_at=now + 20)]
            send_mock = AsyncMock(side_effect=sent)
            with (
                patch.object(duel, "send_game_command", new=send_mock),
                patch.object(duel, "find_message_log_replies", return_value=[]),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_awaited_once_with(".卸下法宝", track=False, max_retry=0, source_module="斗法配装")
            self.assertEqual("斗法配装:prepare_unequip_wait", state_module.state["duel_last_result"])

            with (
                patch.object(duel, "find_message_log_replies", return_value=[{"text": "你已收回当前祭出的所有法宝。"}]),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now + 10)
            self.assertEqual("斗法配装:prepare_equip", state_module.state["duel_last_result"])

            with (
                patch.object(duel, "send_game_command", new=send_mock),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now + 20)
            self.assertEqual("斗法配装:prepare_equip_wait", state_module.state["duel_last_result"])

            with (
                patch.object(duel, "find_message_log_replies", return_value=[{
                    "text": "你已祭出【玄铁剑】。\n当前祭出：【玄铁剑】\n神识御宝：1/26",
                }]),
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now + 30)

            self.assertTrue(state_module.state["duel_unequip_prepared"])
            self.assertEqual("斗法配装:battle_ready", state_module.state["duel_last_result"])

    async def test_wa_batch_completion_enters_restore_before_stopping(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "【天道战报·文字版】\n胜者：@walterwa2000\n败者：@ccahen"
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_completed_count"] = 4
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:battle_ready"
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    text,
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @ccahen"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.state["duel_enabled"])
            self.assertEqual(5, state_module.state["duel_completed_count"])
            self.assertEqual("斗法配装:restore_needed", state_module.state["duel_last_result"])
            self.assertGreater(state_module.state["next_duel_time"], now)
            self.assertIn("恢复原法宝配装", audit_mock.await_args.args[0])

    def test_duel_result_delay_uses_real_weakness_and_batch_stagger(self):
        text = (
            "【天道战报·文字版】\n"
            "胜者：@ccahen | 净得修为 +6.0万\n"
            "败者：@Lpprceqei | 损失修为 -6.0万\n"
            "💔 【神魂重创】 败者进入【虚弱状态】10分钟，期间极易陨落！"
        )
        with patch.object(duel, "_duel_batch_stagger_sec", return_value=180):
            delay = duel._duel_next_delay_from_result(text, True)

        self.assertEqual(10 * 60 + duel.CD_BUFFER_SEC + 180, delay)

    def test_duel_win_result_uses_only_batch_stagger(self):
        text = "【天道战报·文字版】\n胜者：@Lpprceqei\n败者：@ccahen"
        with patch.object(duel, "_duel_batch_stagger_sec", return_value=240):
            delay = duel._duel_next_delay_from_result(text, False)

        self.assertEqual(duel.DUEL_SAME_TARGET_COOLDOWN_SEC + duel.CD_BUFFER_SEC + 240, delay)

    def test_duel_loss_updates_local_xiuwei_estimate(self):
        identity_id = self._prepare_identity(xiuwei_current=700000)
        text = "【天道战报·文字版】\n胜者：@ccahen\n败者：@walterwa2000 | 损失修为 -6.0万"
        with state_module.use_identity(identity_id):
            loss = duel._apply_duel_xiuwei_loss(text)
            profile = state_module.get_send_as_profile(identity_id)

        self.assertEqual(60000, loss)
        self.assertEqual(640000, profile["xiuwei_current"])

    async def test_scheduler_blocks_realm_and_xiuwei_gate_without_sending(self):
        identity_id = self._prepare_identity(realm="元婴中期", xiuwei_current=900000)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 2
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertIn("境界至少需为元婴后期", state_module.state["duel_last_error"])

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
            self.assertIn("斗法前需至少 660000 修为", state_module.state["duel_last_error"])

    def test_profile_gate_allows_realms_above_minimum(self):
        for offset, realm in enumerate(("元婴后期", "化神初期", "化神后期大圆满")):
            identity_id = self._prepare_identity(8659059200 + offset, realm=realm, xiuwei_current=900000)
            with state_module.use_identity(identity_id):
                self.assertEqual("", duel._profile_gate_reason())

    def test_profile_gate_blocks_unknown_realm(self):
        identity_id = self._prepare_identity(8659059210, realm="未知境界", xiuwei_current=900000)
        with state_module.use_identity(identity_id):
            self.assertIn("当前=未知境界", duel._profile_gate_reason())

    async def test_scheduler_reconciles_consumed_prediction_before_xiuwei_gate(self):
        identity_id = self._prepare_identity(xiuwei_current=604056)
        now = 1_780_000_000.0
        report_at = now - 3600
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_last_msg_id"] = 29410
            state_module.state["next_duel_time"] = now + 300
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "current_prediction": "斗法",
                "current_prediction_set_at": now - 7200,
                "current_prediction_until": now + 7200,
            }
            state_module.state["tianxing_timeline_state"] = {
                "phase": "downstream_released",
                "active_step_index": 0,
                "active_step": {"action": "release_downstream", "route": "斗法", "status": "released"},
                "released_routes": {"斗法": {"released_at": now - 7000, "basis": "prediction"}},
            }
            report = {
                "message_id": 29410,
                "ts_epoch": report_at,
                "text": "【天道战报·文字版】\n攻方：@walterwa2000\n🏁 终局结算",
            }
            with (
                patch.object(duel, "find_message_log_message", return_value=report),
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
                patch.object(duel, "console_log"),
            ):
                await duel.run_duel_scheduler(now)

            observed = duel.normalize_tianxing_observation(state_module.state["tianxing_observation"])
            timeline = duel.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        send_mock.assert_not_awaited()
        self.assertEqual("", observed["current_prediction"])
        self.assertEqual("斗法", observed["prediction_consumed_route"])
        self.assertNotIn("斗法", timeline["released_routes"])
        self.assertEqual("blocked_replan", timeline["phase"])

    async def test_scheduler_reconciles_final_report_after_batch_disabled(self):
        identity_id = self._prepare_identity()
        now = 1_780_000_100.0
        report_at = now - 30
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = False
            state_module.state["duel_completed_count"] = 10
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_last_msg_id"] = 29411
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "current_prediction": "斗法",
                "current_prediction_set_at": now - 120,
                "current_prediction_until": now + 7200,
                "prediction_consumed_route": "",
                "prediction_consumed_at": 0,
            }
            state_module.state["tianxing_timeline_state"] = {
                "phase": "downstream_released",
                "active_step_index": 0,
                "active_step": {"action": "release_downstream", "route": "斗法", "status": "released"},
                "released_routes": {"斗法": {"released_at": now - 90, "basis": "prediction"}},
            }
            report = {
                "message_id": 29411,
                "ts_epoch": report_at,
                "text": "【天道战报·文字版】\n攻方：@walterwa2000\n【推命命中】司命演算吻合\n🏁 终局结算",
            }
            with (
                patch.object(duel, "find_message_log_message", return_value=report),
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
                patch.object(duel, "console_log"),
            ):
                await duel.run_duel_scheduler(now)

            observed = duel.normalize_tianxing_observation(state_module.state["tianxing_observation"])
            timeline = duel.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        send_mock.assert_not_awaited()
        self.assertEqual("", observed["current_prediction"])
        self.assertEqual("斗法", observed["prediction_consumed_route"])
        self.assertEqual(report_at, observed["prediction_consumed_at"])
        self.assertNotIn("斗法", timeline["released_routes"])
        self.assertEqual("blocked_replan", timeline["phase"])

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

    async def test_scheduler_definitely_unsent_duel_honors_runtime_backoff(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        blocked_until = now + 1800
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "_prepare_duel_tianxing_route", new=AsyncMock(return_value=True)),
                patch.object(duel, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(duel, "classify_game_send_block", return_value={
                    "status": "unsent",
                    "code": "send_as_peer_invalid",
                    "blocked_until": blocked_until,
                }),
                patch.object(duel.random, "uniform", return_value=10),
                patch.object(duel, "console_log") as console_mock,
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            self.assertEqual("斗法未发送: send_as_peer_invalid", state_module.state["duel_last_error"])
            self.assertEqual(blocked_until + 10, state_module.state["next_duel_time"])
            self.assertIn("斗法未发送", str(console_mock.call_args.args[0]))
            audit_mock.assert_not_awaited()

    async def test_same_target_cooldown_is_shared_across_identities(self):
        first_id = self._prepare_identity(8659059191)
        second_id = self._prepare_identity(3823558636)
        now = 1_700_000_000.0

        with state_module.use_identity(first_id):
            duel._set_target_cooldown("@cupaopao", now + 600, confirmed=True, command_msg_id=22027)

        with state_module.use_identity(second_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "_duel_batch_stagger_sec", return_value=180),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(now + 780, state_module.state["next_duel_time"])
            self.assertIn("仍在斗法冷却", state_module.state["duel_last_error"])

    async def test_successful_send_reserves_target_before_reply(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        fake_msg = SimpleNamespace(id=22027, sent_at=now)
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "send_game_command", new=AsyncMock(return_value=fake_msg)),
                patch.object(duel, "_prepare_duel_tianxing_route", new=AsyncMock(return_value=True)),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

        record = state_module.get_duel_target_cooldowns()["@cupaopao"]
        self.assertFalse(record["confirmed"])
        self.assertEqual(22027, record["command_msg_id"])
        self.assertEqual(now + duel.DUEL_TARGET_RESERVATION_SEC, record["until"])

    async def test_unconfirmed_reservation_blocks_other_identity_for_same_target(self):
        first_id = self._prepare_identity(8659059191)
        second_id = self._prepare_identity(3823558636)
        now = 1_700_000_000.0

        with state_module.use_identity(first_id):
            duel._set_target_cooldown(
                "@cupaopao",
                now + duel.DUEL_TARGET_RESERVATION_SEC,
                confirmed=False,
                command_msg_id=22027,
            )

        with state_module.use_identity(second_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now + 3 * 60
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "_duel_batch_stagger_sec", return_value=180),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now + 3 * 60)

        send_mock.assert_not_awaited()
        with state_module.use_identity(second_id):
            self.assertIn("仍在斗法冷却", state_module.state["duel_last_error"])

    async def test_own_cooldown_reply_releases_target_reservation(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            duel._set_target_cooldown(
                "@cupaopao",
                now + duel.DUEL_TARGET_RESERVATION_SEC,
                confirmed=False,
                command_msg_id=22027,
            )
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
        self.assertNotIn("@cupaopao", state_module.get_duel_target_cooldowns())

    async def test_target_named_cooldown_reply_confirms_shared_target_lock(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            duel._set_target_cooldown(
                "@ccahen",
                now + duel.DUEL_TARGET_RESERVATION_SEC,
                confirmed=False,
                command_msg_id=22027,
            )
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    "道友 @ccahen 元神尚未平复，5分钟内无法再次斗法。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @ccahen"),
                    result_msg_id=22029,
                )

        self.assertTrue(handled)
        record = state_module.get_duel_target_cooldowns()["@ccahen"]
        self.assertTrue(record["confirmed"])
        self.assertEqual(
            now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC + duel.DUEL_TARGET_CONTENTION_BUFFER_SEC,
            record["until"],
        )
        with state_module.use_identity(identity_id):
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertEqual("", state_module.state["duel_last_error"])

    async def test_scheduler_rotates_batch_targets_by_completed_count(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@alpha @beta"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_completed_count"] = 1
            state_module.state["next_duel_time"] = now - 1
            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(duel, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_awaited_once_with(".斗法 @beta", track=False, max_retry=0, source_module="斗法")
            self.assertEqual(22027, state_module.state["duel_reply_to_msg_id"])

    async def test_scheduler_blocks_self_target(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@walterwa2000 @beta"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_completed_count"] = 0
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertIn("斗法目标不能是自己", state_module.state["duel_last_error"])

    async def test_scheduler_prepares_tianxing_before_future_duel_window(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        due_at = now + duel.DUEL_TIANXING_PREPARE_LEAD_SEC
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
            self.assertEqual(due_at - duel.DUEL_TIANXING_PREPARE_LEAD_SEC, window["start_at"])
            self.assertEqual(
                duel.DUEL_TIANXING_PREPARE_LEAD_SEC,
                timeline_mock.await_args.kwargs["config"]["route_prepare_lead_sec"],
            )
            send_mock.assert_not_awaited()
            self.assertEqual(due_at, state_module.state["next_duel_time"])
            self.assertEqual("天星时间线：sent_waiting_ack", state_module.state["duel_last_result"])

    def test_reconcile_consumed_duel_prediction_uses_last_report_without_double_counting(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        report_at = now - 30
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["duel_last_msg_id"] = 7788
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "current_prediction": "斗法",
                "current_prediction_until": now + 3600,
                "current_prediction_set_at": now - 120,
                "prediction_consumed_route": "",
                "prediction_consumed_at": 0,
                "tianji_value": 19,
            }
            state_module.state["tianxing_timeline_state"] = {
                "phase": "downstream_released",
                "active_step_index": 0,
                "active_step": {"action": "release_downstream", "route": "斗法", "status": "released"},
                "released_routes": {"斗法": {"released_at": now - 90, "basis": "prediction"}},
            }
            report = {
                "message_id": 7788,
                "ts_epoch": report_at,
                "text": "【天道战报·文字版】\n攻方：@dueler\n🏁 终局结算",
            }

            with (
                patch.object(duel, "find_message_log_message", return_value=report),
                patch.object(duel, "console_log") as console_mock,
                patch.object(duel, "save_state") as save_mock,
            ):
                changed = duel._reconcile_consumed_duel_prediction_from_last_report(now)

            observed = duel.normalize_tianxing_observation(state_module.state["tianxing_observation"])
            timeline = duel.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertTrue(changed)
        self.assertEqual("", observed["current_prediction"])
        self.assertEqual("斗法", observed["prediction_consumed_route"])
        self.assertEqual(report_at, observed["prediction_consumed_at"])
        self.assertEqual(19, observed["tianji_value"])
        self.assertNotIn("斗法", timeline["released_routes"])
        self.assertEqual("blocked_replan", timeline["phase"])
        save_mock.assert_called_once()
        console_mock.assert_called_once()

    def test_reconcile_consumed_duel_prediction_accepts_escape_result_with_prediction_banner(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_100.0
        report_at = now - 20
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["duel_last_msg_id"] = 7789
            state_module.state["tianxing_observation"] = {
                "current_prediction": "斗法",
                "current_prediction_until": now + 3600,
                "current_prediction_set_at": now - 120,
                "prediction_consumed_route": "",
                "prediction_consumed_at": 0,
            }
            report = {
                "message_id": 7789,
                "ts_epoch": report_at,
                "text": (
                    "面对境界压制，@xuruode6 凭借神通侥幸逃脱！(成功率: 26%)\n"
                    "✨ 【司命盘】 @xuruode6 【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30"
                ),
            }
            with (
                patch.object(duel, "find_message_log_message", return_value=report),
                patch.object(duel, "console_log"),
                patch.object(duel, "save_state"),
            ):
                changed = duel._reconcile_consumed_duel_prediction_from_last_report(now)

            observed = duel.normalize_tianxing_observation(state_module.state["tianxing_observation"])

        self.assertTrue(changed)
        self.assertEqual("", observed["current_prediction"])
        self.assertEqual("斗法", observed["prediction_consumed_route"])
        self.assertEqual(report_at, observed["prediction_consumed_at"])

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
                patch.object(duel.random, "uniform", return_value=120),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_not_awaited()
            self.assertEqual(now + 120, state_module.state["next_duel_time"])
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

    async def test_reply_timeout_uses_random_long_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 2
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now - 1
            with (
                patch.object(duel, "find_message_log_replies", return_value=[]),
                patch.object(duel.random, "uniform", return_value=40 * 60),
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(0, state_module.state["duel_reply_to_msg_id"])
            self.assertEqual("斗法回复超时", state_module.state["duel_last_error"])
            self.assertEqual(now + 40 * 60 + duel.CD_BUFFER_SEC, state_module.state["next_duel_time"])

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

    async def test_external_duel_report_refreshes_shared_target_lock_without_counting_attempt(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            with (
                patch.object(duel, "console_log") as console_mock,
                patch.object(duel, "save_state") as save_mock,
            ):
                handled = await duel.handle_duel_target_observation(
                    "【天道战报·文字版】\n@external_player 与 @cupaopao 斗法结束。\n胜者：@cupaopao\n败者：@external_player",
                    now,
                    event=SimpleNamespace(id=22031),
                )

            target_lock = state_module.get_duel_target_cooldowns()["@cupaopao"]
            self.assertTrue(handled)
            self.assertTrue(target_lock["confirmed"])
            self.assertEqual(now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC, target_lock["until"])
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertEqual(0, state_module.state["duel_reply_to_msg_id"])
            save_mock.assert_called_once()
            console_mock.assert_called_once()

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

    async def test_winning_report_uses_same_target_cooldown_when_opponent_is_weak(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 2
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
            self.assertGreaterEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC + duel.CD_BUFFER_SEC + duel.DUEL_BATCH_STAGGER_MIN_SEC,
            )
            self.assertLessEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC + duel.CD_BUFFER_SEC + duel.DUEL_BATCH_STAGGER_MAX_SEC,
            )
            self.assertEqual("", state_module.state["duel_last_error"])

    async def test_batch_result_uses_same_target_cooldown_plus_stagger(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao @hughpig"
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "_duel_batch_stagger_sec", return_value=5 * 60),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    "【天道战报·文字版】\n@walterwa2000 与 @cupaopao 斗法结束。\n胜者：@walterwa2000\n败者：@cupaopao",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @cupaopao"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertEqual(now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC + duel.CD_BUFFER_SEC + 5 * 60, state_module.state["next_duel_time"])

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
            self.assertGreaterEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MIN_SEC + duel.CD_BUFFER_SEC,
            )
            self.assertLessEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MAX_SEC + duel.CD_BUFFER_SEC,
            )
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
            self.assertGreaterEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MIN_SEC + duel.CD_BUFFER_SEC,
            )
            self.assertLessEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MAX_SEC + duel.CD_BUFFER_SEC,
            )
            self.assertEqual("元神尚未平复，无法再次斗法。", state_module.state["duel_last_error"])

    async def test_escape_reply_counts_attempt_and_disables_at_total(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "面对境界压制，@walterwa2000 凭借神通侥幸逃脱！(成功率: 19%)"
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 1
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    text,
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @cupaopao"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.state["duel_enabled"])
            self.assertEqual(1, state_module.state["duel_completed_count"])
            self.assertEqual(0, state_module.state["duel_reply_to_msg_id"])
            self.assertEqual(text, state_module.state["duel_last_result"])
            target_lock = state_module.get_duel_target_cooldowns()["@cupaopao"]
            self.assertTrue(target_lock["confirmed"])
            self.assertEqual(now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC, target_lock["until"])
            self.assertEqual(text, state_module.state["duel_last_error"])
            audit_mock.assert_awaited_once_with("✅ 斗法完成：1/1", scope="identity", limit=180)

    async def test_lock_failure_reply_counts_attempt_and_uses_long_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "锁定目标时遭遇天机反噬，失败了: Could not find the input entity for PeerUser(user_id=8155156921)"
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "8155156921"
            state_module.state["duel_total_count"] = 2
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    text,
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 8155156921"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual(1, state_module.state["duel_completed_count"])
            self.assertEqual(0, state_module.state["duel_reply_to_msg_id"])
            self.assertEqual("目标锁定失败：天机反噬", state_module.state["duel_last_result"])
            self.assertEqual("目标锁定失败：天机反噬", state_module.state["duel_last_error"])
            self.assertGreaterEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MIN_SEC + duel.CD_BUFFER_SEC,
            )
            self.assertLessEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MAX_SEC + duel.CD_BUFFER_SEC,
            )

    async def test_per_target_limit_reply_counts_attempt_and_uses_long_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "天道不公，但亦有其则！你今日对 @cupaopao 出手次数过多，已被法则限制！"
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_completed_count"] = 1
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    text,
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @cupaopao"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertEqual(2, state_module.state["duel_completed_count"])
            self.assertEqual(text, state_module.state["duel_last_result"])
            self.assertEqual(text, state_module.state["duel_last_error"])
            self.assertGreaterEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MIN_SEC + duel.CD_BUFFER_SEC,
            )
            self.assertLessEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MAX_SEC + duel.CD_BUFFER_SEC,
            )

    async def test_message_log_recovery_accepts_terminal_non_report_reply(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "对方尚未踏入仙途，此番出手恐有失身份。"
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 1
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(
                    duel,
                    "find_message_log_replies",
                    return_value=[{"text": text, "ts_epoch": now - 1, "message_id": 22029}],
                ) as recovery_mock,
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel._recover_duel_pending_from_message_log(now, 22027)

            self.assertTrue(handled)
            recovery_mock.assert_called_once()
            self.assertFalse(state_module.state["duel_enabled"])
            self.assertEqual(1, state_module.state["duel_completed_count"])
            self.assertEqual(text, state_module.state["duel_last_result"])
            self.assertEqual(text, state_module.state["duel_last_error"])

    def test_terminal_non_report_texts_are_recognized_for_log_recovery(self):
        samples = [
            "面对境界压制，@fanrenxiuxian_06 凭借神通侥幸逃脱！(成功率: 19%)",
            "锁定目标时遭遇天机反噬，失败了: Could not find the input entity for PeerUser(user_id=8155156921)",
            "天道不公，但亦有其则！你今日对 @real 出手次数过多，已被法则限制！",
            "对方尚未踏入仙途，此番出手恐有失身份。",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(duel.is_duel_reply_text(sample))


if __name__ == "__main__":
    unittest.main()
