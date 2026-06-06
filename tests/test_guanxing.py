import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import guanxing


class GuanxingShiftGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.set_guanxing_round_state({})

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_guanxing_query_uses_reactive_priority(self):
        now = 1_700_000_000.0
        identity_id = 8659059191
        state_module.ensure_identity_registered(identity_id)

        with patch.object(
            guanxing,
            "send_game_command",
            new=AsyncMock(return_value=SimpleNamespace(id=701)),
        ) as send_mock:
            msg = await guanxing._send_guanxing_query(identity_id, "2026-06-03T22", now)

        self.assertEqual(701, msg.id)
        send_mock.assert_awaited_once_with(
            guanxing.CMD_GUANXING,
            track=False,
            send_as_id=identity_id,
            priority="reactive",
        )
        with state_module.use_identity(identity_id):
            self.assertEqual(701, state_module.state["guanxing_last_query_msg_id"])

    async def test_guanxing_shift_uses_reactive_priority(self):
        identity_id = 8659059191
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["guanxing_enabled"] = True
            state_module.state["guanxing_panel_slot_key"] = "2026-06-03T22"
            state_module.state["guanxing_last_panel_msg_id"] = 702

        with (
            patch.object(guanxing, "get_guanxing_shift_target", return_value="@target"),
            patch.object(
                guanxing,
                "send_game_command",
                new=AsyncMock(return_value=SimpleNamespace(id=703)),
            ) as send_mock,
        ):
            sent, error_text = await guanxing._send_guanxing_shift(identity_id, "2026-06-03T22")

        self.assertTrue(sent)
        self.assertEqual("", error_text)
        send_mock.assert_awaited_once_with(
            f"{guanxing.CMD_GUANXING_SHIFT} @target",
            track=False,
            reply_to=702,
            send_as_id=identity_id,
            priority="reactive",
        )
        with state_module.use_identity(identity_id):
            self.assertEqual(703, state_module.state["guanxing_last_shift_msg_id"])

    async def test_send_next_shift_blocks_duplicate_trigger_while_inflight(self):
        now = 1_700_000_000.0
        round_state = {
            "slot_key": "2026-06-03T22",
            "stage": guanxing.ROUND_STAGE_WAITING_EXTERNAL,
            "participant_ids": [8659059191, 3922509228],
            "next_shift_index": 1,
            "shift_inflight_identity_id": 3922509228,
            "shift_inflight_at": now - 10,
        }

        with (
            patch.object(guanxing, "_send_guanxing_shift", new=AsyncMock()) as send_shift,
            patch.object(guanxing, "save_state") as save_mock,
        ):
            sent = await guanxing._send_next_shift(round_state, now, reason_text="外部触发继续")

        self.assertFalse(sent)
        send_shift.assert_not_awaited()
        save_mock.assert_not_called()
        self.assertEqual(1, round_state["next_shift_index"])
        self.assertEqual(3922509228, round_state["shift_inflight_identity_id"])

    async def test_send_next_shift_preclaims_identity_and_clears_inflight_after_success(self):
        now = 1_700_000_000.0
        round_state = {
            "slot_key": "2026-06-03T22",
            "stage": guanxing.ROUND_STAGE_WAITING_EXTERNAL,
            "participant_ids": [8659059191, 3922509228],
            "next_shift_index": 1,
            "shift_inflight_identity_id": 0,
            "shift_inflight_at": 0,
        }
        observed_during_send = {}

        async def fake_send(identity_id, slot_key):
            observed_during_send.update(
                {
                    "identity_id": identity_id,
                    "slot_key": slot_key,
                    "next_shift_index": round_state.get("next_shift_index"),
                    "stage": round_state.get("stage"),
                    "shift_inflight_identity_id": round_state.get("shift_inflight_identity_id"),
                    "shift_inflight_at": round_state.get("shift_inflight_at"),
                }
            )
            return True, ""

        with (
            patch.object(guanxing, "_send_guanxing_shift", side_effect=fake_send) as send_shift,
            patch.object(guanxing, "get_guanxing_shift_target", return_value="@target"),
            patch.object(guanxing, "console_log"),
            patch.object(guanxing, "save_state"),
        ):
            sent = await guanxing._send_next_shift(round_state, now, reason_text="外部触发继续")

        self.assertTrue(sent)
        send_shift.assert_awaited_once_with(3922509228, "2026-06-03T22")
        self.assertEqual(
            {
                "identity_id": 3922509228,
                "slot_key": "2026-06-03T22",
                "next_shift_index": 2,
                "stage": guanxing.ROUND_STAGE_WAITING_FINISH,
                "shift_inflight_identity_id": 3922509228,
                "shift_inflight_at": now,
            },
            observed_during_send,
        )
        self.assertEqual(2, round_state["next_shift_index"])
        self.assertEqual(guanxing.ROUND_STAGE_WAITING_FINISH, round_state["stage"])
        self.assertEqual(0, round_state["shift_inflight_identity_id"])
        self.assertEqual(0, round_state["shift_inflight_at"])

    async def test_external_shift_ignores_negative_sender_id_matching_participant(self):
        now = 1_700_000_000.0
        round_state = {
            "slot_key": "2026-06-03T22",
            "slot_start_at": now - 100,
            "slot_end_at": now + 100,
            "stage": guanxing.ROUND_STAGE_WAITING_EXTERNAL,
            "gate_keyword": "星辰异象",
            "participant_ids": [8659059191, 3922509228],
            "next_shift_index": 1,
            "consumed_external_msg_ids": [],
            "shift_inflight_identity_id": 0,
            "shift_inflight_at": 0,
        }

        with (
            patch.object(guanxing, "sync_guanxing_round_from_monitor", return_value=(round_state, False)),
            patch.object(guanxing, "_send_next_shift", new=AsyncMock()) as send_next,
        ):
            handled = await guanxing.handle_guanxing_external_shift_command(
                ".改换星移 @target",
                now,
                SimpleNamespace(sender_id=-1008659059191, id=9452872),
            )

        self.assertFalse(handled)
        send_next.assert_not_awaited()
        self.assertEqual([], round_state["consumed_external_msg_ids"])

    async def test_build_round_state_applies_shift_delay_without_beating_query_guard(self):
        slot_info = {
            "slot_key": "2026-06-03T22",
            "slot_start_at": 800.0,
            "slot_end_at": 1000.0,
        }

        with patch.object(guanxing, "get_guanxing_shift_delay_sec", return_value=-60):
            round_state = guanxing._build_round_state(slot_info)

        self.assertEqual(940.0, round_state["shift_due_at"])

        with patch.object(guanxing, "get_guanxing_shift_delay_sec", return_value=-600):
            guarded_state = guanxing._build_round_state(slot_info)

        self.assertEqual(820.0, guarded_state["shift_due_at"])
