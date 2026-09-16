import copy
import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import state as state_module
from model.config import MESSAGES_DIR, TZ_LOCAL
from model.features import concubine
from tests.test_concubine_fragment_contract import NAME, panel


class ConcubineSendRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _prepare_identity(self, send_as_id=3823558636):
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, 8604)
        state_module.set_game_group_id(-100860004)
        state_module.set_game_bot_ids([88004])
        state_module.set_global_enabled(True)
        state_module.get_identity_state(send_as_id).update(concubine_name=NAME, concubine_availability="available")
        state_module.update_send_as_profile(send_as_id, username="recover")
        return send_as_id

    def _write_message_log(self, now, payload):
        log_file = Path(MESSAGES_DIR) / f"{datetime.fromtimestamp(now, TZ_LOCAL).strftime('%Y-%m-%d')}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def test_dream_empty_send_does_not_adopt_unowned_logged_sent_message(self):
        now = 1_783_121_500.0
        send_as_id = self._prepare_identity()
        event_ts = now - 8
        self._write_message_log(
            event_ts,
            {
                "ts": datetime.fromtimestamp(event_ts, TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
                "event_type": "sent",
                "message_id": 11428704,
                "sender_id": send_as_id,
                "topic_id": 0,
                "text": ".入梦寻图",
                "family": "concubine_dream",
            },
        )

        with state_module.use_identity(send_as_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["concubine_dream_due_at"] = now - 1
            with (
                patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(concubine, "was_last_game_send_blocked_by_global", return_value=False),
                patch.object(concubine.time, "time", return_value=now),
            ):
                sent = await concubine._send_dream_command(now)

            self.assertFalse(sent)
            self.assertEqual("dream_pending", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_dream_msg_id"])
            self.assertEqual("unknown", state_module.state["concubine_fragment_actions"]["dream"]["status"])
            self.assertGreater(state_module.state["next_concubine_time"], now)
            self.assertNotEqual("发送 .入梦寻图 失败", state_module.state.get("concubine_last_error"))

    async def test_dream_empty_send_without_receipt_holds_original_operation(self):
        now = 1_783_122_500.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["concubine_dream_due_at"] = now - 1
            with (
                patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(concubine, "was_last_game_send_blocked_by_global", return_value=False),
                patch.object(concubine.time, "time", return_value=now),
                patch.object(concubine.random, "uniform", return_value=90),
            ):
                sent = await concubine._send_dream_command(now)

            self.assertFalse(sent)
            self.assertEqual("dream_pending", state_module.state["concubine_phase"])
            self.assertEqual(now - 1, state_module.state["concubine_dream_due_at"])
            self.assertEqual("unknown", state_module.state["concubine_fragment_actions"]["dream"]["status"])
            self.assertIn("状态未知", state_module.state["concubine_last_error"])

    async def test_dream_send_queue_timeout_is_deferred_not_failed(self):
        now = 1_783_122_700.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["concubine_dream_due_at"] = now - 1
            with (
                patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(concubine, "was_last_game_send_blocked_by_global", return_value=False),
                patch.object(concubine, "classify_game_send_block", side_effect=[
                    {"status": "none"}, {"status": "unsent", "code": "send_queue_timeout", "at": now},
                ]),
                patch.object(concubine.time, "time", return_value=now),
                patch.object(concubine.random, "uniform", return_value=120),
            ):
                sent = await concubine._send_dream_command(now)

            self.assertFalse(sent)
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(now - 1, state_module.state["concubine_dream_due_at"])
            self.assertEqual(now + 120, state_module.state["concubine_fragment_actions"]["dream"]["retry_at"])
            self.assertEqual(now + 120, state_module.state["next_concubine_time"])
            self.assertEqual("", state_module.state["concubine_last_error"])
            self.assertEqual("unsent", state_module.state["concubine_fragment_actions"]["dream"]["status"])

    async def test_dream_action_guard_block_is_deferred_not_failed(self):
        now = 1_783_122_800.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["concubine_dream_due_at"] = now - 1
            with (
                patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(concubine, "was_last_game_send_blocked_by_global", return_value=False),
                patch.object(
                    concubine,
                    "classify_game_send_block",
                    side_effect=[{"status": "none"}, {"status": "unsent", "code": "action_guard", "at": now}],
                ),
                patch.object(concubine.time, "time", return_value=now),
                patch.object(concubine.random, "uniform", return_value=120),
            ):
                sent = await concubine._send_dream_command(now)

            self.assertFalse(sent)
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(now - 1, state_module.state["concubine_dream_due_at"])
            self.assertEqual(now + 120, state_module.state["concubine_fragment_actions"]["dream"]["retry_at"])
            self.assertEqual(now + 120, state_module.state["next_concubine_time"])
            self.assertEqual("", state_module.state["concubine_last_error"])
            self.assertEqual("unsent", state_module.state["concubine_fragment_actions"]["dream"]["status"])

    async def test_puzzle_send_queue_timeout_is_deferred_not_health_error(self):
        now = 1_783_122_900.0
        send_as_id = self._prepare_identity()
        state_module.set_game_group_id(-100860004)
        state_module.set_identity_account(send_as_id, 8604)
        state_module.set_game_bot_ids([88004])
        state_module.set_global_enabled(True)

        with state_module.use_identity(send_as_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["concubine_name"] = NAME
            state_module.state["concubine_availability"] = "available"
            concubine._set_fragment_progress(concubine.DREAM_KIND_CANGKUN, 4, 4)
            with (
                patch.object(concubine.time, "time", return_value=now),
                patch.object(concubine, "save_state", return_value=True),
                patch.object(concubine, "send_audit_log", new=AsyncMock()),
                patch.object(concubine, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                    id=700, chat_id=-100860004, sent_at=now, send_started_at=now))),
            ):
                self.assertTrue(await concubine._send_fragment_command(now))
                self.assertTrue(await concubine.handle_concubine_fragment_reply(
                    panel(), now, SimpleNamespace(id=700, chat_id=-100860004, raw_text=concubine.CMD_CONCUBINE_FRAGMENT),
                    matched_family="concubine_fragment", current_msg_id=701, current_chat_id=-100860004,
                    observed_at=now, reply_context={"sender_id": 88004},
                ))
            with (
                patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(concubine, "classify_game_send_block", side_effect=[
                    {"status": "none"}, {"status": "unsent", "code": "send_queue_timeout", "at": now},
                ]),
                patch.object(concubine.time, "time", return_value=now),
                patch.object(concubine.random, "uniform", return_value=120),
            ):
                sent = await concubine._send_puzzle_command(now)

            self.assertFalse(sent)
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(now + 120, state_module.state["next_concubine_time"])
            self.assertEqual("", state_module.state["concubine_last_error"])
            self.assertEqual("unsent", state_module.state["concubine_fragment_actions"]["puzzle"]["status"])
            self.assertEqual((4, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_CANGKUN))

    async def test_resolved_puzzle_send_failure_is_cleared_from_health_state(self):
        now = 1_783_123_100.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["concubine_availability"] = "available"
            state_module.state["concubine_name"] = "银月"
            state_module.state["concubine_last_error"] = "发送 .拼图 失败"
            state_module.state["next_concubine_time"] = now + 600
            concubine._set_fragment_progress(concubine.DREAM_KIND_XUTIAN, 3, 4)
            concubine._set_fragment_progress(concubine.DREAM_KIND_CANGKUN, 3, 4)
            with patch.object(concubine, "save_state"):
                await concubine.run_concubine_scheduler(now)

            self.assertEqual("", state_module.state["concubine_last_error"])
            self.assertEqual("拼图发送失败已退回残图重查", state_module.state["concubine_last_result"])
            self.assertEqual("idle", state_module.state["concubine_phase"])


if __name__ == "__main__":
    unittest.main()
