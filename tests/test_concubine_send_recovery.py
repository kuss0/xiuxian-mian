import copy
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from model import state as state_module
from model.config import MESSAGES_DIR, TZ_LOCAL
from model.features import concubine


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
        state_module.update_send_as_profile(send_as_id, username="recover")
        return send_as_id

    def _write_message_log(self, now, payload):
        log_file = Path(MESSAGES_DIR) / f"{datetime.fromtimestamp(now, TZ_LOCAL).strftime('%Y-%m-%d')}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def test_dream_empty_send_recovers_logged_sent_message(self):
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

            self.assertTrue(sent)
            self.assertEqual("dream_pending", state_module.state["concubine_phase"])
            self.assertEqual(11428704, state_module.state["concubine_dream_msg_id"])
            self.assertGreater(state_module.state["next_concubine_time"], now)
            self.assertNotEqual("发送 .入梦寻图 失败", state_module.state.get("concubine_last_error"))

    async def test_dream_empty_send_without_logged_sent_uses_short_retry(self):
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
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(now + 90, state_module.state["concubine_dream_due_at"])
            self.assertEqual(now + 90, state_module.state["next_concubine_time"])
            self.assertEqual("发送 .入梦寻图 失败", state_module.state["concubine_last_error"])

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
                patch.object(concubine, "get_last_game_send_block", return_value={"code": "send_queue_timeout"}),
                patch.object(concubine.time, "time", return_value=now),
                patch.object(concubine.random, "uniform", return_value=120),
            ):
                sent = await concubine._send_dream_command(now)

            self.assertFalse(sent)
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(now + 120, state_module.state["concubine_dream_due_at"])
            self.assertEqual(now + 120, state_module.state["next_concubine_time"])
            self.assertEqual("", state_module.state["concubine_last_error"])
            self.assertIn("发送队列拥堵", state_module.state["concubine_last_result"])


if __name__ == "__main__":
    unittest.main()
