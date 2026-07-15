import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import app
from model import app_runtime
from model import config
from model import runtime
from model import state as state_module
from model.features import checkin


class EarlyReplyReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._reply_tracker_snapshot = copy.deepcopy(runtime._reply_chain_tracker)
        self._event_claims_snapshot = dict(app_runtime._runtime_event_claims)
        self._consumed_snapshot = dict(app_runtime._runtime_message_consumed)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        runtime._reply_chain_tracker.clear()
        app_runtime._runtime_event_claims.clear()
        app_runtime._runtime_message_consumed.clear()
        app._early_routed_replies.clear()

    def tearDown(self):
        app._early_routed_replies.clear()
        runtime._reply_chain_tracker.clear()
        runtime._reply_chain_tracker.update(self._reply_tracker_snapshot)
        app_runtime._runtime_event_claims.clear()
        app_runtime._runtime_event_claims.update(self._event_claims_snapshot)
        app_runtime._runtime_message_consumed.clear()
        app_runtime._runtime_message_consumed.update(self._consumed_snapshot)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def test_reply_before_send_bookkeeping_is_replayed_after_registration(self):
        identity_id = 7538826434
        command_msg_id = 154926
        reply_msg_id = 154927
        event_at = 1_784_084_108.0
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="Lpprceqei", sect_name="星宫")

        with state_module.use_identity(identity_id) as identity_state:
            identity_state["checkin_enabled"] = True
            identity_state["sect_teach_enabled"] = False
            identity_state["checkin_teach_day"] = checkin.get_checkin_day_key(event_at)
            identity_state["last_checkin_done_day"] = ""
            identity_state["next_checkin_time"] = event_at - 1

        reply_to = SimpleNamespace(id=command_msg_id, raw_text=config.CMD_CHECKIN, sender_id=identity_id)
        event = SimpleNamespace(
            id=reply_msg_id,
            chat_id=-1001680975844,
            sender_id=8861328042,
            raw_text="点卯成功！你获得了 105 点宗门贡献。",
            reply_to=SimpleNamespace(reply_to_msg_id=command_msg_id, reply_to_top_id=7310786),
            message=SimpleNamespace(buttons=None),
        )
        early_context = {
            "send_as_id": identity_id,
            "family": None,
            "reply_to_msg_id": command_msg_id,
            "root_msg_id": command_msg_id,
            "matched_via": "reply_sender",
            "source": "",
        }

        with (
            state_module.use_identity(identity_id),
            patch.object(checkin, "save_state"),
            patch.object(app, "schedule_cleanup", new=AsyncMock()),
        ):
            first_handled = await app._handle_routed_reply_event(
                event,
                event.raw_text,
                event_at,
                reply_to,
                early_context,
            )

        self.assertTrue(first_handled)
        self.assertIn(command_msg_id, app._early_routed_replies)

        with state_module.use_identity(identity_id) as identity_state:
            identity_state["pending_tasks"][command_msg_id] = {
                "cmd": config.CMD_CHECKIN,
                "sent_at": event_at + 3,
                "retry": 0,
                "timeout": 900,
            }
            identity_state["my_msg_ids"][command_msg_id] = event_at + 3
        runtime.track_reply_chain_message(command_msg_id, identity_id, "checkin", root_msg_id=command_msg_id)

        with (
            state_module.use_identity(identity_id),
            patch.object(app, "_EARLY_ROUTED_REPLY_REPLAY_DELAY_SEC", 0),
            patch.object(checkin, "save_state"),
            patch.object(app, "schedule_cleanup", new=AsyncMock()),
            patch.object(app, "_bind_command_attempt_shadow") as bind_mock,
        ):
            replayed = await app._replay_early_replies_after_sent(
                identity_id,
                config.CMD_CHECKIN,
                event_at + 3,
                command_msg_id,
            )

        self.assertTrue(replayed)
        bind_mock.assert_called_once()
        with state_module.use_identity(identity_id) as identity_state:
            self.assertNotIn(command_msg_id, identity_state["pending_tasks"])
            self.assertEqual(checkin.get_checkin_day_key(event_at), identity_state["last_checkin_done_day"])


if __name__ == "__main__":
    unittest.main()
