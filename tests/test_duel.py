import copy
import sys
import unittest
from datetime import datetime, timedelta
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
        with state_module.use_identity(identity_id):
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:battle_ready"
        return identity_id

    async def test_manual_reenable_starts_a_new_completed_batch(self):
        identity_id = self._prepare_identity()
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = False
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_completed_count"] = 10
            state_module.state["next_duel_time"] = 0
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_last_result"] = ""

        with patch.object(control, "save_state"):
            ok, _message = await control.set_module_enabled("斗法", True, send_as_id=identity_id)

        self.assertTrue(ok)
        with state_module.use_identity(identity_id):
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertGreater(state_module.state["next_duel_time"], 0)
            self.assertEqual("斗法配装:prepare", state_module.state["duel_last_result"])

    async def test_manual_reenable_seeds_baiji_unequip_prepare(self):
        identity_id = self._prepare_identity(301299112)
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = False
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_completed_count"] = 0
            state_module.state["next_duel_time"] = 0
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_last_result"] = ""

        with patch.object(control, "save_state"):
            ok, _message = await control.set_module_enabled("斗法", True, send_as_id=identity_id)

        self.assertTrue(ok)
        with state_module.use_identity(identity_id):
            self.assertEqual("斗法配装:prepare", state_module.state["duel_last_result"])
            self.assertFalse(state_module.state["duel_unequip_prepared"])

    def test_duel_config_seeds_default_loadout_for_every_identity(self):
        now = 1_700_000_000.0
        for identity_id in (301299112, 99001999):
            self._prepare_identity(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["duel_enabled"] = True
                state_module.state["duel_last_result"] = ""
                state_module.state["duel_unequip_prepared"] = False
                with patch.object(duel, "save_state"):
                    duel.apply_duel_config(
                        target="@ccahen",
                        total_count=5,
                        reset_progress=True,
                        now=now,
                        persist=True,
                    )
                self.assertEqual("斗法配装:prepare", state_module.state["duel_last_result"])

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

    def test_parse_int_accepts_sqlite_real_message_ids(self):
        self.assertEqual(245402, duel._parse_int(245402.0))
        self.assertEqual(245402, duel._parse_int("245402.0"))

    def test_controlled_loadout_requires_exact_current_equipment(self):
        self.assertTrue(duel._loadout_reply_matches("你已祭出【玄铁剑】。\n当前祭出：【玄铁剑】\n神识御宝：1/26", ("玄铁剑",)))
        self.assertFalse(duel._loadout_reply_matches("当前祭出：【玄铁剑】、【金光砖】", ("玄铁剑",)))

    def test_controlled_loadout_accepts_already_unequipped_reply(self):
        self.assertTrue(duel._loadout_unequip_reply("你当前并未祭出任何法宝。"))

    def test_controlled_loadout_exact_anchor_recovers_reply_across_restart_gap(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        reply = {
            "message_id": 245535,
            "reply_to_msg_id": 245532,
            "text": "你已祭出【元合五极山】。",
        }
        with state_module.use_identity(identity_id):
            state_module.state["duel_magic_sent_at"] = 245532
            with patch.object(duel, "find_message_log_replies", return_value=[reply]) as recover_mock:
                found = duel._find_loadout_reply(now, lambda text: "元合五极山" in text)

        self.assertEqual(reply, found)
        self.assertEqual(
            duel.DUEL_LOADOUT_RECOVERY_LOOKBACK_SEC,
            recover_mock.call_args.kwargs["lookback_sec"],
        )

    async def test_baiji_controlled_loadout_confirms_unequipped_without_equipping(self):
        identity_id = self._prepare_identity(301299112)
        state_module.update_send_as_profile(identity_id, username="jfdffdddd1")
        now = 1_700_000_000.0
        config = duel.DUEL_DEFAULT_LOADOUT
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_last_result"] = ""

            sent_msg = SimpleNamespace(id=1000, sent_at=now)
            with (
                patch.object(duel, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(duel, "save_state"),
            ):
                prepared = await duel._run_controlled_loadout_prepare(now, config)

            self.assertFalse(prepared)
            send_mock.assert_awaited_once_with(".卸下法宝", track=False, max_retry=0, source_module="斗法配装")
            self.assertEqual("斗法配装:prepare_unequip_wait", state_module.state["duel_last_result"])

            with (
                patch.object(duel, "_find_loadout_reply", return_value={"text": "你当前并未祭出任何法宝。"}),
                patch.object(duel, "send_game_command", new=AsyncMock()) as second_send_mock,
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
            ):
                prepared = await duel._run_controlled_loadout_prepare(now + 10, config)

            self.assertFalse(prepared)
            second_send_mock.assert_not_awaited()
            audit_mock.assert_awaited_once()
            self.assertTrue(state_module.state["duel_unequip_prepared"])
            self.assertEqual("斗法配装:battle_ready", state_module.state["duel_last_result"])

            prepared = await duel._run_controlled_loadout_prepare(now + 20, config)
            self.assertTrue(prepared)

    def test_baiji_batch_completion_keeps_unequipped_without_restore_commands(self):
        identity_id = self._prepare_identity(301299112)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_completed_count"] = 10
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:battle_ready"

            completion = duel._complete_duel_batch(now)

            self.assertFalse(completion["restoring"])
            self.assertTrue(completion["daily"])
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertTrue(state_module.state["duel_unequip_prepared"])
            self.assertEqual("斗法配装:battle_ready", state_module.state["duel_last_result"])
            self.assertGreater(state_module.state["next_duel_time"], now)

    async def test_wa_controlled_loadout_confirms_unequipped_before_duel(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now - 1
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_last_result"] = "斗法配装:prepare"

            send_mock = AsyncMock(return_value=SimpleNamespace(id=1001, sent_at=now))
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
            self.assertTrue(state_module.state["duel_unequip_prepared"])
            self.assertEqual("斗法配装:battle_ready", state_module.state["duel_last_result"])

    async def test_wa_restored_loadout_can_start_a_new_batch(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_completed_count"] = 0
            state_module.state["next_duel_time"] = now - 1
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_last_result"] = "斗法配装:restored"
            state_module.state["tianxing_auto_config"] = {"duel_route_enabled": False}

            send_mock = AsyncMock(return_value=SimpleNamespace(id=1001, sent_at=now))
            with (
                patch.object(duel, "send_game_command", new=send_mock),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_awaited_once_with(".卸下法宝", track=False, max_retry=0, source_module="斗法配装")
            self.assertEqual("斗法配装:prepare_unequip_wait", state_module.state["duel_last_result"])

    async def test_loadout_prepare_does_not_run_before_duel_lead_window(self):
        identity_id = self._prepare_identity(99002001)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 5
            state_module.state["next_duel_time"] = now + 3600
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_last_result"] = "斗法配装:prepare"
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(now + 3600, state_module.state["next_duel_time"])

    async def test_managed_defender_is_unequipped_before_duel_send(self):
        baiji_id = self._prepare_identity(301299112)
        wa_id = self._prepare_identity(8659059191)
        state_module.update_send_as_profile(baiji_id, username="jfdffdddd1", username_aliases=["jfdffdddd"])
        state_module.update_send_as_profile(wa_id, username="WalterWA20000", username_aliases=["WalterWA2000"])
        now = 1_700_000_000.0
        state_module.set_duel_target_cooldowns({})
        with state_module.use_identity(wa_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@jfdffdddd1"
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_completed_count"] = 0
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_last_result"] = "斗法配装:restored"
            state_module.state["next_duel_time"] = now + 3600
        with state_module.use_identity(baiji_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@WalterWA20000"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:battle_ready"
            state_module.state["next_duel_time"] = now - 1
            send_mock = AsyncMock(
                side_effect=(
                    SimpleNamespace(id=1000, sent_at=now),
                    SimpleNamespace(id=1001, sent_at=now + 20),
                )
            )
            with (
                patch.object(duel, "send_game_command", new=send_mock),
                patch.object(duel, "_prepare_duel_tianxing_route", new=AsyncMock(return_value=True)),
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)
                self.assertEqual(".卸下法宝", send_mock.await_args_list[0].args[0])
                self.assertEqual(1, send_mock.await_count)

                with patch.object(duel, "_find_loadout_reply", return_value={"text": "你已收回当前祭出的所有法宝。"}):
                    await duel.run_duel_scheduler(now + 10)
                self.assertEqual(1, send_mock.await_count)

                await duel.run_duel_scheduler(now + 20)
                self.assertEqual(".斗法 @WalterWA20000", send_mock.await_args_list[1].args[0])

        with state_module.use_identity(wa_id):
            self.assertTrue(state_module.state["duel_unequip_prepared"])
            self.assertEqual("斗法配装:battle_ready", state_module.state["duel_last_result"])
        pair = state_module.get_duel_target_cooldowns()["@walterwa20000"]
        self.assertEqual(baiji_id, pair["pair_batch_owner_identity_id"])
        self.assertEqual(wa_id, pair["pair_batch_defender_identity_id"])

    async def test_managed_defender_cannot_start_reverse_duel_during_pair_batch(self):
        baiji_id = self._prepare_identity(301299112)
        wa_id = self._prepare_identity(8659059191)
        state_module.update_send_as_profile(baiji_id, username="jfdffdddd1", username_aliases=["jfdffdddd"])
        state_module.update_send_as_profile(wa_id, username="WalterWA20000", username_aliases=["WalterWA2000"])
        now = 1_700_000_000.0
        state_module.set_duel_target_cooldowns({})
        with state_module.use_identity(baiji_id):
            target_id, reason = duel._claim_managed_target_pair_batch("@WalterWA20000", now)
        self.assertEqual(wa_id, target_id)
        self.assertEqual("", reason)
        with state_module.use_identity(wa_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@jfdffdddd1"
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:battle_ready"
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "_prepare_duel_tianxing_route", new=AsyncMock(return_value=True)) as tianxing_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now + 1)

            send_mock.assert_not_awaited()
            tianxing_mock.assert_not_awaited()
            self.assertIn("受控互斗批次", state_module.state["duel_last_error"])

    def test_pair_batch_completion_queues_managed_defender_restore(self):
        baiji_id = self._prepare_identity(301299112)
        wa_id = self._prepare_identity(8659059191)
        state_module.update_send_as_profile(baiji_id, username="jfdffdddd1", username_aliases=["jfdffdddd"])
        state_module.update_send_as_profile(wa_id, username="WalterWA20000", username_aliases=["WalterWA2000"])
        now = 1_700_000_000.0
        state_module.set_duel_target_cooldowns({})
        with state_module.use_identity(wa_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@jfdffdddd1"
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_completed_count"] = 0
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:battle_ready"
            state_module.state["next_duel_time"] = now + 3600
        with state_module.use_identity(baiji_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@WalterWA20000"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_completed_count"] = 5
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:battle_ready"
            target_id, reason = duel._claim_managed_target_pair_batch("@WalterWA20000", now)
            self.assertEqual((wa_id, ""), (target_id, reason))
            completion = duel._complete_duel_batch(now + 1)

        self.assertFalse(completion["restoring"])
        with state_module.use_identity(wa_id):
            self.assertEqual("斗法配装:restore_needed", state_module.state["duel_last_result"])
            self.assertEqual(now + 1 + duel.DUEL_LOADOUT_STEP_DELAY_SEC, state_module.state["next_duel_time"])
        pair = state_module.get_duel_target_cooldowns().get("@walterwa20000") or {}
        self.assertNotIn("pair_batch_owner_identity_id", pair)

    async def test_wa_batch_completion_enters_restore_without_disabling(self):
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
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual(5, state_module.state["duel_completed_count"])
            self.assertEqual("斗法配装:restore_needed", state_module.state["duel_last_result"])
            self.assertGreater(state_module.state["next_duel_time"], now)
            self.assertIn("恢复原法宝配装", audit_mock.await_args.args[0])

    async def test_no_available_target_restores_wa_even_when_next_time_is_tomorrow(self):
        identity_id = self._prepare_identity(8659059191)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@target"
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_completed_count"] = 1
            state_module.state["duel_daily_limit_day"] = duel._duel_day_key(now)
            state_module.state["duel_daily_limited_targets"] = ["@target"]
            state_module.state["next_duel_time"] = now + 12 * 3600
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:battle_ready"
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            self.assertEqual("斗法配装:restore_needed", state_module.state["duel_last_result"])
            self.assertGreater(state_module.state["next_duel_time"], now)
            self.assertIn("开始恢复原法宝配装", audit_mock.await_args.args[0])

    def test_complete_restored_wa_does_not_queue_restore_again(self):
        identity_id = self._prepare_identity(8659059191)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_completed_count"] = 0
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_last_result"] = "斗法配装:restored"

            completion = duel._complete_duel_batch(now)

            self.assertFalse(completion["restoring"])
            self.assertEqual("斗法配装:restored", state_module.state["duel_last_result"])

    async def test_wa_restore_schedules_next_daily_batch_when_enabled(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_completed_count"] = 5
            state_module.state["duel_observed_completed_count"] = 5
            state_module.state["duel_observed_baseline_count"] = 0
            state_module.state["duel_log_reconcile_day"] = duel._duel_day_key(now)
            state_module.state["duel_log_reconcile_at"] = now
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:restore_equip:5"
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            self.assertTrue(state_module.state["duel_enabled"])
            self.assertFalse(state_module.state["duel_unequip_prepared"])
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertEqual(5, state_module.state["duel_observed_baseline_count"])
            self.assertEqual("斗法配装:restored", state_module.state["duel_last_result"])
            self.assertEqual(
                datetime.fromtimestamp(now, duel.TZ_LOCAL).date() + timedelta(days=1),
                datetime.fromtimestamp(state_module.state["next_duel_time"], duel.TZ_LOCAL).date(),
            )
            self.assertIn("次日批次", audit_mock.await_args.args[0])

    async def test_wa_restore_skips_redundant_unequip_when_empty_is_confirmed(self):
        identity_id = self._prepare_identity(8659059191)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:restore_needed"
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                handled = await duel._run_controlled_loadout_restore(
                    now,
                    duel.DUEL_CONTROLLED_LOADOUTS[identity_id],
                )

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            self.assertEqual("斗法配装:restore_equip:0", state_module.state["duel_last_result"])

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

    def test_duel_progress_label_avoids_impossible_fraction_when_manual_facts_exceed_config(self):
        self.assertEqual("3/3", duel._duel_progress_label(3, 3))
        self.assertEqual("4 场（配置 3）", duel._duel_progress_label(4, 3))
        self.assertEqual("4 场", duel._duel_progress_label(4, 0))

    def test_duel_win_result_uses_only_batch_stagger(self):
        text = "【天道战报·文字版】\n胜者：@Lpprceqei\n败者：@ccahen"
        with patch.object(duel, "_duel_batch_stagger_sec", return_value=240):
            delay = duel._duel_next_delay_from_result(text, False)

        self.assertEqual(duel.DUEL_SAME_TARGET_COOLDOWN_SEC + duel.CD_BUFFER_SEC + 240, delay)

    def test_duel_loss_without_explicit_weakness_uses_target_cooldown(self):
        text = "【天道战报·文字版】\n胜者：@ccahen\n败者：@Lpprceqei"
        with patch.object(duel, "_duel_batch_stagger_sec", return_value=240):
            delay = duel._duel_next_delay_from_result(text, True)

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
            self.assertIn("元婴须达到元婴后期", state_module.state["duel_last_error"])

        identity_id = self._prepare_identity(8659059192, realm="元婴后期", xiuwei_current=150000)
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
            self.assertIn("需至少 260000", state_module.state["duel_last_error"])
            self.assertIn("保留 200000 + 风险 60000", state_module.state["duel_last_error"])

    def test_profile_gate_allows_jiedan_late_and_yuanying_late_plus(self):
        for offset, realm in enumerate(("结丹后期", "元婴后期", "化神初期", "化神后期大圆满")):
            identity_id = self._prepare_identity(8659059200 + offset, realm=realm, xiuwei_current=260000)
            with state_module.use_identity(identity_id):
                self.assertEqual("", duel._profile_gate_reason())

    def test_profile_gate_blocks_yuanying_early_and_mid(self):
        for offset, realm in enumerate(("元婴初期", "元婴中期")):
            identity_id = self._prepare_identity(8659059220 + offset, realm=realm, xiuwei_current=900000)
            with state_module.use_identity(identity_id):
                reason = duel._profile_gate_reason()
                self.assertIn("元婴须达到元婴后期", reason)
                self.assertIn(realm, reason)

    def test_profile_gate_blocks_below_jiedan_late(self):
        identity_id = self._prepare_identity(8659059230, realm="结丹中期", xiuwei_current=900000)
        with state_module.use_identity(identity_id):
            self.assertIn("境界至少需为结丹后期", duel._profile_gate_reason())

    def test_profile_gate_blocks_unknown_realm(self):
        identity_id = self._prepare_identity(8659059210, realm="未知境界", xiuwei_current=900000)
        with state_module.use_identity(identity_id):
            self.assertIn("当前=未知境界", duel._profile_gate_reason())

    def test_reserve_xiuwei_default_and_ui_override(self):
        identity_id = self._prepare_identity(8659059240, realm="元婴后期", xiuwei_current=260000)
        with state_module.use_identity(identity_id):
            state_module.state["duel_reserve_xiuwei"] = 0
            self.assertEqual(200000, duel.get_duel_reserve_xiuwei())
            self.assertEqual("", duel._profile_gate_reason())

            with patch.object(duel, "save_state"):
                duel.apply_duel_config(reserve_xiuwei=400000, persist=True)
            self.assertEqual(400000, state_module.state["duel_reserve_xiuwei"])
            self.assertEqual(400000, duel.get_duel_reserve_xiuwei())
            self.assertIn("需至少 460000", duel._profile_gate_reason())

            with patch.object(duel, "save_state"):
                duel.apply_duel_config(reserve_xiuwei="", persist=True)
            self.assertEqual(0, state_module.state["duel_reserve_xiuwei"])
            self.assertEqual(200000, duel.get_duel_reserve_xiuwei())
            self.assertEqual("", duel._profile_gate_reason())

    def test_managed_target_gate_checks_target_reserve_and_loss_risk(self):
        attacker_id = self._prepare_identity(99002001, xiuwei_current=900000)
        target_id = self._prepare_identity(99002002, realm="化神初期", xiuwei_current=250000)
        state_module.update_send_as_profile(attacker_id, username="high_attacker")
        state_module.update_send_as_profile(target_id, username="low_target")

        with state_module.use_identity(attacker_id):
            reason = duel._target_gate_reason("@low_target")

        self.assertIn("守方 @low_target 剩余修为不足", reason)
        self.assertIn("保留 200000 + 风险 60000", reason)

    def test_tiny_managed_loss_marks_resource_depleted_until_recovered(self):
        attacker_id = self._prepare_identity(99002003, xiuwei_current=900000)
        target_id = self._prepare_identity(99002004, realm="化神初期", xiuwei_current=700000)
        state_module.update_send_as_profile(attacker_id, username="high_attacker")
        state_module.update_send_as_profile(target_id, username="low_target")
        now = 1_700_000_000.0
        text = (
            "【天道战报·文字版】\n"
            "胜者：@high_attacker\n"
            "败者：@low_target | 损失修为 -120"
        )

        with state_module.use_identity(attacker_id):
            changed, current_loss = duel._record_managed_duel_loss(text, now, result_msg_id=4001)
            reason = duel._target_gate_reason("@low_target")

        self.assertTrue(changed)
        self.assertEqual(0, current_loss)
        self.assertIn("可转移修为已接近耗尽", reason)
        target_profile = state_module.get_send_as_profile(target_id)
        self.assertEqual(699880, target_profile["xiuwei_current"])
        record = state_module.get_duel_target_cooldowns()["@low_target"]
        self.assertEqual(now, record["resource_depleted_at"])
        self.assertEqual(699880, record["resource_depleted_xiuwei"])
        self.assertEqual(duel.DUEL_RESOURCE_RECOVERY_XIUWEI, record["resource_recovery_xiuwei"])

        state_module.update_send_as_profile(target_id, xiuwei_current=899880)
        with state_module.use_identity(attacker_id):
            self.assertEqual("", duel._target_gate_reason("@low_target"))

    def test_useful_managed_loss_becomes_next_loss_risk(self):
        attacker_id = self._prepare_identity(99002005, xiuwei_current=900000)
        target_id = self._prepare_identity(99002006, realm="化神初期", xiuwei_current=700000)
        state_module.update_send_as_profile(attacker_id, username="high_attacker")
        state_module.update_send_as_profile(target_id, username="low_target")
        now = 1_700_000_000.0
        text = (
            "【天道战报·文字版】\n"
            "胜者：@high_attacker\n"
            "败者：@low_target | 损失修为 -24.0万"
        )

        with state_module.use_identity(attacker_id):
            changed, current_loss = duel._record_managed_duel_loss(text, now, result_msg_id=4002)
            self.assertTrue(changed)
            self.assertEqual(0, current_loss)

        record = state_module.get_duel_target_cooldowns()["@low_target"]
        self.assertEqual(240000, record["recent_loss_xiuwei"])
        state_module.update_send_as_profile(target_id, xiuwei_current=430000)
        with state_module.use_identity(attacker_id):
            reason = duel._target_gate_reason("@low_target")
        self.assertIn("需至少 440000", reason)

    def test_managed_loss_replay_is_idempotent_by_result_message_id(self):
        attacker_id = self._prepare_identity(99002007, xiuwei_current=900000)
        target_id = self._prepare_identity(99002008, realm="化神初期", xiuwei_current=700000)
        state_module.update_send_as_profile(attacker_id, username="high_attacker")
        state_module.update_send_as_profile(target_id, username="low_target")
        text = (
            "【天道战报·文字版】\n"
            "胜者：@high_attacker\n"
            "败者：@low_target | 损失修为 -6.0万"
        )

        with state_module.use_identity(attacker_id):
            first = duel._record_managed_duel_loss(text, 1_700_000_000.0, result_msg_id=4010)
            second = duel._record_managed_duel_loss(text, 1_700_000_010.0, result_msg_id=4010)

        self.assertEqual((True, 0), first)
        self.assertEqual((False, 0), second)
        self.assertEqual(640000, state_module.get_send_as_profile(target_id)["xiuwei_current"])

    def test_window_normalize_label_and_bounds(self):
        self.assertEqual(0, duel.normalize_duel_window_minute(-1, 0))
        self.assertEqual(23 * 60 + 59, duel.normalize_duel_window_minute(9999, 0))
        self.assertEqual(8 * 60 + 30, duel.normalize_duel_window_minute(8 * 60 + 30, 0))
        self.assertEqual("08:30-22:00", duel.get_duel_window_label(start_minute=8 * 60 + 30, end_minute=22 * 60))

        # 固定本地日：2024-01-15 12:00 Asia/Shanghai ≈ 1705291200 附近，用 bounds 反推。
        noon_local = datetime(2024, 1, 15, 12, 0, 0, tzinfo=duel.TZ_LOCAL)
        now = noon_local.timestamp()
        start_ts, end_ts = duel.get_duel_window_bounds(now, start_minute=9 * 60, end_minute=18 * 60)
        self.assertTrue(duel.is_within_duel_exec_window(now, start_minute=9 * 60, end_minute=18 * 60))
        self.assertFalse(duel.is_within_duel_exec_window(start_ts - 1, start_minute=9 * 60, end_minute=18 * 60))
        self.assertFalse(duel.is_within_duel_exec_window(end_ts + 1, start_minute=9 * 60, end_minute=18 * 60))
        # 窗口已过 → 次日开窗
        after = end_ts + 60
        open_at = duel.next_duel_exec_window_open(after, start_minute=9 * 60, end_minute=18 * 60)
        self.assertAlmostEqual(start_ts + 24 * 3600, open_at, places=0)

    def test_estimate_duel_capacity_ok_and_overflow(self):
        # 全日窗 + 10 场：本号间隔约 13 分，应足够。
        ok = duel.estimate_duel_capacity(total_count=10, start_minute=0, end_minute=23 * 60 + 59)
        self.assertTrue(ok["ok"])
        self.assertGreaterEqual(ok["self_max"], 10)
        self.assertEqual("", ok["reason"])

        # 60 分钟窗：self_max ≈ 3600/780+1 = 5，10 场应不足。
        tight = duel.estimate_duel_capacity(total_count=10, start_minute=12 * 60, end_minute=13 * 60)
        self.assertFalse(tight["ok"])
        self.assertIn("身份次数", tight["reason"])
        self.assertLess(tight["self_max"], 10)

        # 同目标合计超共享 CD 容量。
        target = duel.estimate_duel_capacity(
            total_count=2,
            start_minute=0,
            end_minute=30,  # 30 分钟
            target_hits=10,
        )
        self.assertFalse(target["ok"])
        self.assertIn("同目标合计", target["reason"])

        # 零宽窗口：最多 1 场瞬时。
        zero = duel.estimate_duel_capacity(total_count=1, start_minute=10 * 60, end_minute=10 * 60)
        self.assertTrue(zero["ok"])
        self.assertEqual(1, zero["self_max"])
        zero_fail = duel.estimate_duel_capacity(total_count=2, start_minute=10 * 60, end_minute=10 * 60)
        self.assertFalse(zero_fail["ok"])

    def test_apply_duel_config_window_minutes(self):
        identity_id = self._prepare_identity(8659059250, realm="元婴后期", xiuwei_current=300000)
        with state_module.use_identity(identity_id):
            state_module.state["duel_window_start_minute"] = 0
            state_module.state["duel_window_end_minute"] = 1439
            with patch.object(duel, "save_state"):
                config = duel.apply_duel_config(
                    total_count=10,
                    window_start_minute=9 * 60,
                    window_end_minute=21 * 60 + 30,
                    persist=True,
                )
            self.assertEqual(9 * 60, state_module.state["duel_window_start_minute"])
            self.assertEqual(21 * 60 + 30, state_module.state["duel_window_end_minute"])
            self.assertEqual("09:00-21:30", config["window_label"])
            self.assertEqual(9 * 60, config["window_start_minute"])
            self.assertIn("capacity", config)
            # end < start 被钳到 start
            with patch.object(duel, "save_state"):
                duel.apply_duel_config(window_start_minute=20 * 60, window_end_minute=8 * 60, persist=True)
            self.assertEqual(20 * 60, state_module.state["duel_window_start_minute"])
            self.assertEqual(20 * 60, state_module.state["duel_window_end_minute"])

    def test_plan_duel_presets_yuanying_jiedan_and_excluded(self):
        plan = duel.plan_duel_presets(
            [
                {
                    "send_as_id": 1001,
                    "realm": "元婴后期",
                    "username": "yuanying_a",
                    "label": "元婴A",
                },
                {
                    "send_as_id": 1002,
                    "realm": "元婴后期",
                    "username": "yuanying_b",
                    "label": "元婴B",
                },
                {
                    "send_as_id": 2001,
                    "realm": "结丹后期",
                    "username": "jiedan_1",
                    "label": "结丹1",
                },
                {
                    "send_as_id": 2002,
                    "realm": "结丹后期",
                    "username": "jiedan_2",
                    "label": "结丹2",
                },
                {
                    "send_as_id": 2003,
                    "realm": "结丹后期",
                    "username": "jiedan_3",
                    "label": "结丹3",
                },
                {
                    "send_as_id": 301299112,
                    "realm": "元婴后期",
                    "username": "jfdffdddd",
                    "label": "吧唧",
                },
                {
                    "send_as_id": 8659059191,
                    "realm": "元婴后期",
                    "username": "walterwa2000",
                    "label": "WA",
                },
                {
                    "send_as_id": 3001,
                    "realm": "元婴中期",
                    "username": "mid_only",
                    "label": "中期",
                },
            ]
        )
        by_id = {row["send_as_id"]: row for row in plan["rows"]}
        self.assertEqual(["@yuanying_a", "@yuanying_b"], plan["yuanying_targets"])
        self.assertEqual(3, plan["jiedan_count"])

        self.assertTrue(by_id[1001]["duel_enabled"])
        self.assertEqual("@ccahen", by_id[1001]["duel_target"])
        self.assertEqual(10, by_id[1001]["duel_total_count"])
        self.assertEqual("yuanying", by_id[1001]["band"])

        self.assertTrue(by_id[2001]["duel_enabled"])
        self.assertEqual("@yuanying_a", by_id[2001]["duel_target"])
        self.assertEqual("@yuanying_b", by_id[2002]["duel_target"])
        self.assertEqual("@yuanying_a", by_id[2003]["duel_target"])
        self.assertEqual(10, by_id[2001]["duel_total_count"])

        self.assertFalse(by_id[301299112]["duel_enabled"])
        self.assertEqual("excluded", by_id[301299112]["role"])
        self.assertFalse(by_id[8659059191]["duel_enabled"])
        self.assertEqual("excluded", by_id[8659059191]["role"])

        self.assertFalse(by_id[3001]["duel_enabled"])
        self.assertEqual("none", by_id[3001]["role"])

        # 分组可视化 + 同目标负载 + 容量字段（吸收上游 group 思路）
        groups = plan["groups"]
        self.assertEqual(2, len(groups["yuanying_sources"]))
        self.assertEqual(3, len(groups["jiedan_sources"]))
        self.assertIn(301299112, groups["excluded"])
        self.assertIn(8659059191, groups["excluded"])
        self.assertIn(3001, groups["disabled"])
        # 元婴均打 @ccahen×10×2=20；结丹打元婴 a/b 各 20/10
        self.assertEqual(20, groups["target_hits"]["@ccahen"])
        self.assertEqual(20, groups["target_hits"]["@yuanying_a"])
        self.assertEqual(10, groups["target_hits"]["@yuanying_b"])
        self.assertIn("capacity", by_id[1001])
        self.assertTrue(by_id[1001]["capacity"].get("ok") or by_id[1001]["capacity"].get("reason"))
        self.assertTrue(by_id[3001]["capacity"].get("skipped"))

    def test_apply_duel_preset_row_writes_config(self):
        identity_id = self._prepare_identity(99001001, realm="元婴后期", xiuwei_current=300000)
        state_module.update_send_as_profile(identity_id, username="lab_yuanying")
        row = {
            "send_as_id": identity_id,
            "band": "yuanying",
            "role": "yuanying",
            "duel_enabled": True,
            "duel_target": "@ccahen",
            "duel_total_count": 10,
            "reason": "元婴后预设打 @ccahen ×10",
        }
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = False
            state_module.state["duel_target"] = ""
            state_module.state["duel_total_count"] = 0
            # 稳妥：预设不得改写天星斗法线开关
            state_module.state["tianxing_auto_config"] = {"duel_route_enabled": True}
            with patch.object(duel, "save_state"):
                result = duel.apply_duel_preset_row(row, now=1_700_000_000.0, persist=True, force=True)
            self.assertTrue(result["applied"])
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual("@ccahen", state_module.state["duel_target"])
            self.assertEqual(10, state_module.state["duel_total_count"])
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertTrue(state_module.state["tianxing_auto_config"]["duel_route_enabled"])

    def test_apply_duel_preset_disable_does_not_cancel_tianxing_route(self):
        """排除预设关斗法 ≠ 关模块：不得 cancel 天星斗法线。"""
        identity_id = self._prepare_identity(99001002, realm="元婴后期", xiuwei_current=300000)
        now = 1_700_000_000.0
        row = {
            "send_as_id": identity_id,
            "band": "yuanying",
            "role": "excluded",
            "duel_enabled": False,
            "duel_target": "",
            "duel_total_count": 0,
            "reason": "吧唧/WA 预设关闭",
        }
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 10
            state_module.state["tianxing_auto_config"] = {"duel_route_enabled": True}
            state_module.state["tianxing_observation"] = {
                "current_prediction": "斗法",
                "current_prediction_until": now + 3600,
                "current_prediction_set_at": now - 10,
            }
            with patch.object(duel, "save_state"):
                result = duel.apply_duel_preset_row(row, now=now, persist=True, force=True)
            self.assertTrue(result["applied"])
            self.assertFalse(state_module.state["duel_enabled"])
            self.assertTrue(state_module.state["tianxing_auto_config"]["duel_route_enabled"])
            self.assertEqual("斗法", state_module.state["tianxing_observation"]["current_prediction"])

    async def test_scheduler_outside_window_defers_and_may_prepare_tianxing(self):
        """窗外不发送；改期到开窗后仍可按 lead 提前备天星（稳妥对齐原版 future-due）。"""
        identity_id = self._prepare_identity(8659059260, realm="元婴后期", xiuwei_current=300000)
        # 本地 03:00，窗 09:00-18:00
        now_local = datetime(2024, 1, 15, 3, 0, 0, tzinfo=duel.TZ_LOCAL)
        now = now_local.timestamp()
        open_at = duel.get_duel_window_bounds(now, start_minute=9 * 60, end_minute=18 * 60)[0]
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_completed_count"] = 0
            state_module.state["next_duel_time"] = now - 1
            state_module.state["duel_window_start_minute"] = 9 * 60
            state_module.state["duel_window_end_minute"] = 18 * 60
            with (
                patch.object(duel, "_prepare_duel_tianxing_route", new=AsyncMock(return_value=True)) as prep_mock,
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertAlmostEqual(open_at, state_module.state["next_duel_time"], places=0)
            self.assertIn("不在斗法执行窗口", state_module.state["duel_last_error"])
            # 03:00 距 09:00 远大于 60s lead，consume window 为空 → 不调用 prepare
            prep_mock.assert_not_awaited()

        # 进入 lead：开窗前 30s 应触发 prepare(due=open_at)，仍不发送
        near = open_at - 30
        with state_module.use_identity(identity_id):
            state_module.state["next_duel_time"] = near - 1
            state_module.state["duel_last_error"] = ""
            with (
                patch.object(duel, "_prepare_duel_tianxing_route", new=AsyncMock(return_value=True)) as prep_mock,
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(near)

            send_mock.assert_not_awaited()
            prep_mock.assert_awaited()
            self.assertAlmostEqual(open_at, prep_mock.await_args.kwargs.get("due_at") or prep_mock.await_args.args[1], places=0)

    async def test_manual_enable_applies_preset_when_empty(self):
        yy_id = 9911001
        jd_id = 9911002
        for send_as_id, realm, username in (
            (yy_id, "元婴后期", "lab_yy"),
            (jd_id, "结丹后期", "lab_jd"),
        ):
            state_module.ensure_identity_registered(send_as_id)
            state_module.update_send_as_profile(
                send_as_id,
                username=username,
                realm=realm,
                xiuwei_current=300000,
            )
            with state_module.use_identity(send_as_id):
                state_module.state["duel_enabled"] = False
                state_module.state["duel_target"] = ""
                state_module.state["duel_total_count"] = 0
                state_module.state["duel_completed_count"] = 0
                state_module.state["next_duel_time"] = 0

        with patch.object(control, "save_state"):
            ok_yy, _ = await control.set_module_enabled("斗法", True, send_as_id=yy_id)
            ok_jd, _ = await control.set_module_enabled("斗法", True, send_as_id=jd_id)
        self.assertTrue(ok_yy)
        self.assertTrue(ok_jd)

        with state_module.use_identity(yy_id):
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual("@ccahen", state_module.state["duel_target"])
            self.assertEqual(10, state_module.state["duel_total_count"])
        with state_module.use_identity(jd_id):
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual("@lab_yy", state_module.state["duel_target"])
            self.assertEqual(10, state_module.state["duel_total_count"])

    async def test_scheduler_reconciles_consumed_prediction_before_xiuwei_gate(self):
        # 修为仍高于 20 万门槛，本用例只验证天星预判消费，不触发修为 gate。
        identity_id = self._prepare_identity(xiuwei_current=250000)
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
            self.assertIn("正在被其他身份斗法", state_module.state["duel_last_error"])

    async def test_reciprocal_duel_is_blocked_while_other_side_is_attacking(self):
        baiji_id = self._prepare_identity(301299112)
        wa_id = self._prepare_identity(8659059191)
        state_module.update_send_as_profile(baiji_id, username="jfdffdddd1", username_aliases=["jfdffdddd"])
        state_module.update_send_as_profile(wa_id, username="WalterWA20000", username_aliases=["WalterWA2000"])
        now = 1_700_000_000.0
        state_module.set_duel_target_cooldowns({})
        with state_module.use_identity(baiji_id):
            duel._set_target_cooldown(
                "@WalterWA20000",
                now + duel.DUEL_TARGET_RESERVATION_SEC,
                confirmed=False,
                command_msg_id=22027,
            )
        with state_module.use_identity(wa_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@jfdffdddd1"
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_unequip_prepared"] = True
            state_module.state["duel_last_result"] = "斗法配装:battle_ready"
            state_module.state["next_duel_time"] = now - 1
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertIn("当前身份正被", state_module.state["duel_last_error"])

    async def test_completed_duel_blocks_reverse_pair_until_target_cooldown_expires(self):
        baiji_id = self._prepare_identity(301299112)
        wa_id = self._prepare_identity(8659059191)
        state_module.update_send_as_profile(baiji_id, username="jfdffdddd1", username_aliases=["jfdffdddd"])
        state_module.update_send_as_profile(wa_id, username="WalterWA20000", username_aliases=["WalterWA2000"])
        now = 1_700_000_000.0
        state_module.set_duel_target_cooldowns({})
        with state_module.use_identity(wa_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@jfdffdddd1"
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    "【天道战报·文字版】\n"
                    "攻方：@WalterWA2000 · 惊慕\n"
                    "守方：@jfdffdddd · 空尘子\n"
                    "🏁 终局结算\n胜者：@WalterWA2000\n败者：@jfdffdddd",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @jfdffdddd1"),
                    result_msg_id=22030,
                )
            self.assertTrue(handled)

        with state_module.use_identity(baiji_id):
            block = duel._active_duel_participant_block("@WalterWA20000", now + 1)
            self.assertIn("互斗冷却中", block)

    def test_target_cooldown_without_report_does_not_create_reverse_pair_lock(self):
        baiji_id = self._prepare_identity(301299112)
        wa_id = self._prepare_identity(8659059191)
        state_module.update_send_as_profile(baiji_id, username="jfdffdddd1", username_aliases=["jfdffdddd"])
        state_module.update_send_as_profile(wa_id, username="WalterWA20000", username_aliases=["WalterWA2000"])
        now = 1_700_000_000.0
        state_module.set_duel_target_cooldowns({})
        with state_module.use_identity(wa_id):
            duel._set_target_cooldown(
                "@jfdffdddd1",
                now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC,
                confirmed=True,
                command_msg_id=22027,
                reciprocal=False,
            )
        with state_module.use_identity(baiji_id):
            self.assertFalse(duel._active_duel_participant_block("@WalterWA20000", now + 1))

    def test_completed_pair_cooldown_does_not_block_unrelated_attacker(self):
        baiji_id = self._prepare_identity(301299112)
        wa_id = self._prepare_identity(8659059191)
        third_id = 3823558636
        state_module.ensure_identity_registered(third_id)
        state_module._meta_state["identity_states"][third_id] = state_module.new_identity_state()
        state_module.set_identity_account(third_id, third_id)
        state_module.update_send_as_profile(baiji_id, username="jfdffdddd1", username_aliases=["jfdffdddd"])
        state_module.update_send_as_profile(wa_id, username="WalterWA20000", username_aliases=["WalterWA2000"])
        state_module.update_send_as_profile(third_id, username="third_dueler")
        now = 1_700_000_000.0
        state_module.set_duel_target_cooldowns({})
        with state_module.use_identity(wa_id):
            duel._set_target_cooldown(
                "@jfdffdddd1",
                now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC,
                confirmed=True,
                command_msg_id=22027,
                reciprocal=True,
            )
        with state_module.use_identity(third_id):
            self.assertFalse(duel._active_duel_participant_block("@WalterWA20000", now + 1))

    def test_plain_target_update_does_not_transfer_existing_pair_lock_to_new_owner(self):
        baiji_id = self._prepare_identity(301299112)
        wa_id = self._prepare_identity(8659059191)
        third_id = 3823558636
        state_module.ensure_identity_registered(third_id)
        state_module._meta_state["identity_states"][third_id] = state_module.new_identity_state()
        state_module.set_identity_account(third_id, third_id)
        state_module.update_send_as_profile(baiji_id, username="jfdffdddd1", username_aliases=["jfdffdddd"])
        state_module.update_send_as_profile(wa_id, username="WalterWA20000", username_aliases=["WalterWA2000"])
        state_module.update_send_as_profile(third_id, username="third_dueler")
        now = 1_700_000_000.0
        state_module.set_duel_target_cooldowns({})
        with state_module.use_identity(wa_id):
            duel._set_target_cooldown(
                "@jfdffdddd1",
                now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC,
                confirmed=True,
                reciprocal=True,
            )
        with state_module.use_identity(third_id):
            duel._set_target_cooldown(
                "@jfdffdddd1",
                now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC,
                confirmed=True,
                reciprocal=False,
            )
        with state_module.use_identity(baiji_id):
            self.assertIn(
                "互斗冷却中",
                duel._active_duel_participant_block("@WalterWA20000", now + 1),
            )
            self.assertFalse(duel._active_duel_participant_block("@third_dueler", now + 1))

    def test_manual_duel_log_evidence_rebuilds_progress_and_loadout(self):
        baiji_id = self._prepare_identity(301299112)
        wa_id = self._prepare_identity(8659059191)
        state_module.update_send_as_profile(baiji_id, username="jfdffdddd1", username_aliases=["jfdffdddd"])
        state_module.update_send_as_profile(wa_id, username="WalterWA20000", username_aliases=["WalterWA2000"])
        now = 1_700_000_000.0
        entries = [
            {"event_type": "message", "message_id": 100, "sender_id": wa_id, "text": ".卸下法宝", "ts_epoch": now - 600},
            {"event_type": "message", "message_id": 101, "reply_to_msg_id": 100, "text": "你已收回当前祭出的所有法宝，祭炼与本命联系仍然保留。", "ts_epoch": now - 599},
        ]
        for index in range(4):
            command_id = 200 + index * 10
            entries.append({
                "event_type": "message",
                "message_id": command_id,
                "sender_id": wa_id,
                "text": ".斗法 @jfdffdddd1",
                "ts_epoch": now - 500 + index * 100,
            })
            if index == 3:
                entries.append({
                    "event_type": "sent",
                    "message_id": command_id,
                    "sender_id": wa_id,
                    "text": ".斗法 @jfdffdddd1",
                    "ts_epoch": now - 500 + index * 100,
                })
            entries.append({
                "event_type": "message",
                "message_id": command_id + 1,
                "reply_to_msg_id": command_id,
                "text": (
                    "【天道战报·文字版】\n"
                    "攻方：@WalterWA2000 · 惊慕\n"
                    "守方：@jfdffdddd · 空尘子\n"
                    "胜者：@WalterWA2000\n"
                    f"🧠 今日神念：{9-index}/10 | 对此人剩余胜场: {4-index}"
                ),
                "ts_epoch": now - 495 + index * 100,
            })

        with state_module.use_identity(wa_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@jfdffdddd1"
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_completed_count"] = 1
            state_module.state["duel_last_msg_id"] = 0
            state_module.state["duel_unequip_prepared"] = False
            with patch.object(duel, "_duel_day_log_entries", return_value=entries):
                changed = duel.reconcile_duel_from_message_log(now, force=True)

            self.assertTrue(changed)
            self.assertEqual(4, state_module.state["duel_completed_count"])
            self.assertEqual(4, state_module.state["duel_observed_completed_count"])
            self.assertEqual(3, state_module.state["duel_observed_manual_count"])
            self.assertEqual(6, state_module.state["duel_observed_mind_remaining"])
            self.assertTrue(state_module.state["duel_unequip_prepared"])
            self.assertEqual("斗法配装:battle_ready", state_module.state["duel_last_result"])

    def test_log_reconcile_caps_stale_long_delay_after_complete_report(self):
        identity_id = self._prepare_identity(301299112)
        state_module.update_send_as_profile(identity_id, username="jfdffdddd1")
        now = 1_700_000_000.0
        report_at = now - 20
        entries = [
            {
                "event_type": "sent",
                "message_id": 250,
                "sender_id": identity_id,
                "text": ".斗法 @target",
                "ts_epoch": report_at - 10,
            },
            {
                "event_type": "message",
                "message_id": 251,
                "reply_to_msg_id": 250,
                "text": (
                    "【天道战报·文字版】\n"
                    "攻方：@jfdffdddd1\n"
                    "守方：@target\n"
                    "胜者：@target"
                ),
                "ts_epoch": report_at,
            },
        ]
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@target"
            state_module.state["duel_total_count"] = 5
            state_module.state["duel_completed_count"] = 0
            state_module.state["next_duel_time"] = now + duel.DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MAX_SEC
            with patch.object(duel, "_duel_day_log_entries", return_value=entries):
                changed = duel.reconcile_duel_from_message_log(now, force=True)

            self.assertTrue(changed)
            self.assertEqual(
                report_at
                + duel.DUEL_SAME_TARGET_COOLDOWN_SEC
                + duel.CD_BUFFER_SEC
                + duel.DUEL_BATCH_STAGGER_MAX_SEC,
                state_module.state["next_duel_time"],
            )

    def test_log_reconcile_does_not_override_restore_phase_with_equip_reply(self):
        identity_id = self._prepare_identity(8659059191)
        now = 1_700_000_000.0
        entries = [
            {"event_type": "sent", "message_id": 300, "sender_id": identity_id, "text": ".装备 青竹蜂云剑（神雷版）", "ts_epoch": now - 2},
            {
                "event_type": "message",
                "message_id": 301,
                "reply_to_msg_id": 300,
                "text": "你已祭出【青竹蜂云剑（神雷版）】。\n当前祭出：【青竹蜂云剑（神雷版）】\n神识御宝：4/26",
                "ts_epoch": now - 1,
            },
        ]
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@jfdffdddd1"
            state_module.state["duel_last_result"] = "斗法配装:restore_equip_wait:0"
            state_module.state["duel_unequip_prepared"] = False
            with patch.object(duel, "_duel_day_log_entries", return_value=entries):
                duel.reconcile_duel_from_message_log(now, force=True)

            self.assertEqual("斗法配装:restore_equip_wait:0", state_module.state["duel_last_result"])
            self.assertFalse(state_module.state["duel_unequip_prepared"])

    def test_log_reconcile_does_not_override_pending_unequip_with_stale_equip_reply(self):
        identity_id = self._prepare_identity(301299112)
        now = 1_700_000_000.0
        entries = [
            {
                "event_type": "sent",
                "message_id": 320,
                "sender_id": identity_id,
                "text": ".装备 玄天斩灵剑",
                "ts_epoch": now - 30,
            },
            {
                "event_type": "message",
                "message_id": 321,
                "reply_to_msg_id": 320,
                "text": "你已祭出【玄天斩灵剑】。\n当前祭出：【玄天斩灵剑】",
                "ts_epoch": now - 29,
            },
        ]
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@target"
            state_module.state["next_duel_time"] = now + 3600
            state_module.state["duel_last_result"] = "斗法配装:prepare_unequip_wait"
            state_module.state["duel_unequip_prepared"] = False
            state_module.state["duel_magic_sent_at"] = 322
            state_module.state["duel_magic_due_at"] = now + 120
            with patch.object(duel, "_duel_day_log_entries", return_value=entries):
                duel.reconcile_duel_from_message_log(now, force=True)

            self.assertEqual("斗法配装:prepare_unequip_wait", state_module.state["duel_last_result"])
            self.assertFalse(state_module.state["duel_unequip_prepared"])
            self.assertEqual(322, state_module.state["duel_magic_sent_at"])

    def test_loadout_reply_invalidates_duel_log_cache(self):
        identity_id = self._prepare_identity(301299112)
        now = 1_700_000_000.0
        reply = {"message_id": 331, "text": "你当前并未祭出任何法宝。", "ts_epoch": now}
        with state_module.use_identity(identity_id):
            state_module.state["duel_magic_sent_at"] = 330
            duel._DUEL_DAY_LOG_CACHE.update(day="2023-11-14", refreshed_at=now, entries=[{"message_id": 1}])
            with patch.object(duel, "find_message_log_replies", return_value=[reply]):
                found = duel._find_loadout_reply(now, duel._loadout_unequip_reply)

        self.assertEqual(reply, found)
        self.assertEqual("", duel._DUEL_DAY_LOG_CACHE["day"])
        self.assertEqual(0.0, duel._DUEL_DAY_LOG_CACHE["refreshed_at"])
        self.assertEqual([], duel._DUEL_DAY_LOG_CACHE["entries"])

    def test_completed_batch_consumes_observed_progress_baseline(self):
        identity_id = self._prepare_identity(8659059191)
        now = 1_700_000_000.0
        entries = []
        for index in range(4):
            command_id = 400 + index * 10
            entries.extend([
                {
                    "event_type": "sent",
                    "message_id": command_id,
                    "sender_id": identity_id,
                    "text": ".斗法 @target",
                    "ts_epoch": now - 500 + index * 100,
                },
                {
                    "event_type": "message",
                    "message_id": command_id + 1,
                    "reply_to_msg_id": command_id,
                    "text": (
                        "【天道战报·文字版】\n"
                        "攻方：@walterwa2000 · 惊慕\n"
                        "守方：@target · 守方\n"
                        "胜者：@walterwa2000\n"
                        f"🧠 今日神念：{9-index}/10"
                    ),
                    "ts_epoch": now - 495 + index * 100,
                },
            ])
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@target"
            state_module.state["duel_total_count"] = 3
            state_module.state["duel_completed_count"] = 4
            state_module.state["duel_observed_completed_count"] = 4
            state_module.state["duel_observed_baseline_count"] = 0
            state_module.state["duel_log_reconcile_day"] = duel._duel_day_key(now)
            state_module.state["duel_last_msg_id"] = 9999

            completion = duel._complete_duel_batch(now)
            self.assertTrue(completion["restoring"])
            self.assertEqual(4, state_module.state["duel_observed_baseline_count"])

            state_module.state["duel_completed_count"] = 0
            state_module.state["duel_last_result"] = "斗法配装:restored"
            with patch.object(duel, "_duel_day_log_entries", return_value=entries):
                duel.reconcile_duel_from_message_log(now + 1, force=True)

            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertEqual(4, state_module.state["duel_observed_completed_count"])

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

    async def test_phaseful_settlement_on_duel_root_stays_intermediate_until_final_report(self):
        identity_id = self._prepare_identity(3765328695)
        state_module.update_send_as_profile(identity_id, username="Lpprceqei")
        now = 1_700_000_000.0
        root_msg_id = 226300
        reply_to = SimpleNamespace(id=root_msg_id, raw_text=".斗法 @ccahen")
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@ccahen"
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_completed_count"] = 0
            state_module.state["duel_reply_to_msg_id"] = root_msg_id
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            state_module.state["duel_started_at"] = now - 5
            state_module.state["duel_last_result"] = "已发送"

            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
                patch.object(duel, "_duel_batch_stagger_sec", return_value=5 * 60),
            ):
                settlement_handled = await duel.handle_duel_reply(
                    "【元婴闭关结算】\n你的元婴闭关已经结束。",
                    now,
                    reply_to=reply_to,
                    result_msg_id=226301,
                )

                self.assertFalse(settlement_handled)
                self.assertEqual(root_msg_id, state_module.state["duel_reply_to_msg_id"])
                self.assertEqual(0, state_module.state["duel_completed_count"])
                self.assertEqual("已发送", state_module.state["duel_last_result"])

                waiting_handled = await duel.handle_duel_reply(
                    "正在锁定对手天机，请稍候...",
                    now + 1,
                    reply_to=reply_to,
                    result_msg_id=226302,
                )
                self.assertTrue(waiting_handled)
                self.assertEqual(root_msg_id, state_module.state["duel_reply_to_msg_id"])
                self.assertEqual(226302, state_module.state["duel_open_msg_id"])

                final_handled = await duel.handle_duel_reply(
                    "【天道战报·文字版】\n"
                    "攻方：@Lpprceqei · 元婴后期\n"
                    "守方：@ccahen · 化神后期\n"
                    "胜者：@ccahen | 余血 100/100万\n"
                    "败者：@Lpprceqei | 余血 0/100万 | 损失修为 -6.0万",
                    now + 61,
                    reply_to=reply_to,
                    result_msg_id=226309,
                )

            self.assertTrue(final_handled)
            self.assertEqual(0, state_module.state["duel_reply_to_msg_id"])
            self.assertEqual(1, state_module.state["duel_completed_count"])
            self.assertEqual(226309, state_module.state["duel_last_msg_id"])
            self.assertIn("斗法结束，胜者 @ccahen", state_module.state["duel_last_result"])
            self.assertEqual(640000, state_module.get_send_as_profile(identity_id)["xiuwei_current"])
            audit_mock.assert_awaited_once()

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

    async def test_end_broadcast_counts_completion_and_rolls_to_next_day(self):
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
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual(1, state_module.state["duel_completed_count"])
            self.assertEqual("斗法配装:restore_needed", state_module.state["duel_last_result"])
            self.assertEqual(now + duel.DUEL_LOADOUT_STEP_DELAY_SEC, state_module.state["next_duel_time"])

    async def test_non_wa_batch_completion_rolls_to_next_local_day(self):
        identity_id = self._prepare_identity(3765328695)
        now = 1_700_000_000.0
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
                    "面对境界压制，@walterwa2000 凭借神通侥幸逃脱！(成功率: 19%)",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @cupaopao"),
                    result_msg_id=22029,
                )

            next_duel_time = state_module.state["next_duel_time"]
            self.assertTrue(handled)
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertEqual("@cupaopao", state_module.state["duel_target"])
            self.assertEqual(
                datetime.fromtimestamp(now, duel.TZ_LOCAL).date() + timedelta(days=1),
                datetime.fromtimestamp(next_duel_time, duel.TZ_LOCAL).date(),
            )
            audit_mock.assert_awaited_once()
            self.assertIn("今日斗法完成：1/1", audit_mock.await_args.args[0])

    async def test_scheduler_rolls_stale_non_wa_completed_batch_to_next_day(self):
        identity_id = self._prepare_identity(3765328695)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_completed_count"] = 10
            state_module.state["next_duel_time"] = 0
            with (
                patch.object(duel, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(duel, "save_state"),
            ):
                await duel.run_duel_scheduler(now)

            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertEqual(
                datetime.fromtimestamp(now, duel.TZ_LOCAL).date() + timedelta(days=1),
                datetime.fromtimestamp(state_module.state["next_duel_time"], duel.TZ_LOCAL).date(),
            )
            self.assertIn("今日任务完成：10/10", state_module.state["duel_last_result"])
            send_mock.assert_not_awaited()

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

    async def test_winner_not_self_without_loser_line_uses_target_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@cupaopao"
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()),
                patch.object(duel, "_duel_batch_stagger_sec", return_value=5 * 60),
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    "【天道战报·文字版】\n@walterwa2000 与 @cupaopao 斗法结束。\n胜者：@cupaopao",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @cupaopao"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertEqual(
                state_module.state["next_duel_time"],
                now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC + duel.CD_BUFFER_SEC + 5 * 60,
            )
            self.assertEqual("", state_module.state["duel_last_error"])

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

    async def test_escape_reply_counts_attempt_and_rolls_to_next_day(self):
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
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual(1, state_module.state["duel_completed_count"])
            self.assertEqual(0, state_module.state["duel_reply_to_msg_id"])
            self.assertEqual("斗法配装:restore_needed", state_module.state["duel_last_result"])
            target_lock = state_module.get_duel_target_cooldowns()["@cupaopao"]
            self.assertTrue(target_lock["confirmed"])
            self.assertEqual(now + duel.DUEL_SAME_TARGET_COOLDOWN_SEC, target_lock["until"])
            self.assertEqual(text, state_module.state["duel_last_error"])
            self.assertIn("开始恢复原法宝配装", audit_mock.await_args.args[0])

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

    async def test_per_target_limit_reply_ends_single_target_day_without_counting_attempt(self):
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
            self.assertEqual(1, state_module.state["duel_completed_count"])
            self.assertEqual("斗法配装:restore_needed", state_module.state["duel_last_result"])
            self.assertEqual("", state_module.state["duel_last_error"])
            self.assertEqual(["@cupaopao"], state_module.state["duel_daily_limited_targets"])
            self.assertEqual(now + duel.DUEL_LOADOUT_STEP_DELAY_SEC, state_module.state["next_duel_time"])

    async def test_per_target_limit_reply_switches_to_another_configured_target(self):
        identity_id = self._prepare_identity(99002000)
        now = 1_700_000_000.0
        text = "天道不公，但亦有其则！你今日对 @first 出手次数过多，已被法则限制！"
        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@first @second"
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_completed_count"] = 0
            state_module.state["duel_reply_to_msg_id"] = 22027
            state_module.state["duel_reply_due_at"] = now + duel.DUEL_REPLY_TIMEOUT_SEC
            with (
                patch.object(duel, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(duel, "save_state"),
            ):
                handled = await duel.handle_duel_reply(
                    text,
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".斗法 @first"),
                    result_msg_id=22029,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["duel_completed_count"])
            self.assertEqual("@second", duel._target_token(now))
            self.assertGreaterEqual(state_module.state["next_duel_time"], now + duel.DUEL_RECOVERY_MIN_SEC)
            self.assertLessEqual(state_module.state["next_duel_time"], now + duel.DUEL_RECOVERY_MAX_SEC)
            self.assertIn("切换至 @second", audit_mock.await_args.args[0])

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
            self.assertTrue(state_module.state["duel_enabled"])
            self.assertEqual(1, state_module.state["duel_completed_count"])
            self.assertEqual("斗法配装:restore_needed", state_module.state["duel_last_result"])
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
