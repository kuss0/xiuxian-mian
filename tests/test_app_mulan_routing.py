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
from model import state as state_module


class AppMulanRoutingTests(unittest.IsolatedAsyncioTestCase):
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

    def _prepare_identity(self, identity_id=301299112):
        state_module.ensure_identity_registered(identity_id)
        state_module._meta_state["identity_states"][int(identity_id)] = state_module.new_identity_state()
        state_module.set_identity_account(identity_id, identity_id)
        return identity_id

    async def test_routed_mulan_support_final_edit_replays_after_start_notice_consumed(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        event = SimpleNamespace(id=11294406, sender_id=8757550896, chat_id=-1001680975844)
        reply_to = SimpleNamespace(id=11294405, raw_text=".支援慕兰 护阵")
        reply_context = {
            "send_as_id": identity_id,
            "family": "mulan_support",
            "reply_to_msg_id": 11294405,
            "root_msg_id": 11294405,
        }
        final_text = (
            "【慕兰烽烟 · 固守边境法阵】小胜\n"
            "你接过阵师抛来的黄旗，将一截摇晃的阵脉重新钉回山势。\n\n"
            "获得修为 +382\n"
            "获得灵石 +285\n"
            "边境军功 +4，累计 6\n"
            "连续支援 1 天"
        )

        with state_module.use_identity(identity_id):
            state_module.state["mulan_enabled"] = True
            state_module.state["mulan_phase"] = "support_pending"
            state_module.state["mulan_reply_to_msg_id"] = 11294405
            state_module.state["mulan_reply_due_at"] = now + 180
            state_module.state["mulan_support_action"] = "护阵"

        app._mark_runtime_message_consumed(event, "mulan_support")
        with (
            patch("model.features.mulan.save_state", return_value=True),
            patch("model.features.mulan.send_audit_log", new=AsyncMock()),
            patch.object(app, "schedule_cleanup", new=AsyncMock()),
        ):
            handled = await app._handle_routed_reply_event(
                event,
                final_text,
                now,
                reply_to,
                reply_context,
                event_kind="edit",
            )

        self.assertTrue(handled)
        identity_state = state_module.get_identity_state(identity_id)
        self.assertEqual("cooldown", identity_state["mulan_phase"])
        self.assertEqual("支援完成：护阵", identity_state["mulan_last_result"])
        self.assertEqual(0, identity_state["mulan_reply_to_msg_id"])
        self.assertEqual("", identity_state["mulan_last_error"])


if __name__ == "__main__":
    unittest.main()
