import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import app
from model import app_runtime
from model import state as state_module


class AppDuelRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        app_runtime._runtime_event_claims.clear()
        app_runtime._runtime_message_consumed.clear()

    def tearDown(self):
        app_runtime._runtime_event_claims.clear()
        app_runtime._runtime_message_consumed.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_routed_duel_terminal_edit_replays_after_waiting_message_consumed(self):
        identity_id = 3852827410
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        state_module.set_identity_account(identity_id, identity_id)
        event = SimpleNamespace(id=359377, sender_id=8735907987, chat_id=-1002083016447)
        reply_to = SimpleNamespace(id=359376, raw_text=".斗法 @Lpprceqei")
        reply_context = {
            "send_as_id": identity_id,
            "family": "duel",
            "reply_to_msg_id": 359376,
            "root_msg_id": 359376,
        }

        with state_module.use_identity(identity_id):
            state_module.state["duel_enabled"] = True
            state_module.state["duel_target"] = "@Lpprceqei"
            state_module.state["duel_total_count"] = 10
            state_module.state["duel_reply_to_msg_id"] = 359376
            state_module.state["duel_reply_due_at"] = now + 150
            state_module.state["duel_open_msg_id"] = 359377
            state_module.state["duel_started_at"] = now - 5

        app._mark_runtime_message_consumed(event, "duel")
        with (
            patch("model.features.duel.save_state", return_value=True),
            patch("model.features.duel.send_audit_log", new=AsyncMock()),
            patch.object(app, "schedule_cleanup", new=AsyncMock()),
        ):
            handled = await app._handle_routed_reply_event(
                event,
                "天道有则！你与 @Lpprceqei 在24小时内已交锋过多，暂不可再次斗法！",
                now,
                reply_to,
                reply_context,
                event_kind="edit",
            )

        self.assertTrue(handled)
        identity_state = state_module.get_identity_state(identity_id)
        self.assertEqual(0, identity_state["duel_reply_to_msg_id"])
        self.assertIn("@lpprceqei", identity_state["duel_daily_limited_targets"])


if __name__ == "__main__":
    unittest.main()
