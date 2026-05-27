import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import runtime
from model.features import passive_inbox, tree


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class TreeTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_channel_identity_reply_sender_resolves_tree_status_owner(self):
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")

        reply_to = type(
            "ReplyMessage",
            (),
            {
                "id": 9352845,
                "sender_id": -1003800619925,
                "raw_text": ".灵树状态",
            },
        )()

        context = runtime.get_reply_context(reply_to, reply_to_msg_id=9352845)

        self.assertEqual(identity_id, context["send_as_id"])
        self.assertEqual("tree_panel", context["family"])
        self.assertEqual("reply_sender", context["matched_via"])

    async def test_unregistered_reply_sender_does_not_resolve_owner(self):
        identity_id = 3800619925
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="growrdick")

        reply_to = type(
            "ReplyMessage",
            (),
            {
                "id": 9352846,
                "sender_id": 123456789,
                "raw_text": ".灵树状态",
            },
        )()

        context = runtime.get_reply_context(reply_to, reply_to_msg_id=9352846)

        self.assertIsNone(context["send_as_id"])
        self.assertEqual("tree_panel", context["family"])

    async def test_normal_panel_recovers_stale_maturing_state_for_all_tree_identities(self):
        now = 1000.0
        identity_ids = [3756719391, 3800619925]
        for identity_id in identity_ids:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username=f"user{identity_id}")
            with state_module.use_identity(identity_id):
                state_module.state["tree_enabled"] = True
                state_module.state["is_maturing"] = True
                state_module.state["is_harvested"] = True
                state_module.state["pending_irrigation"] = True
                state_module.state["next_irr_time"] = now + 9999999

        panel = (
            "【落云宗 · 灵眼之树】\n"
            "💧 环境: 干渴 (需 水/冰/雾)\n"
            "🌲 进度:\n"
            "🟩🟩🟩⬜⬜ 75.08%\n"
            "🔄 阶段: 4 / 4\n\n"
            "👤 你的当前状态: 944 点\n"
            "🌰 奉养灵树: 需【一截灵眼之树】 0/1 或木髓 0/3"
        )

        with (
            patch.object(tree, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tree, "save_state"),
        ):
            handled = await tree.handle_tree_panel(panel, now, False)

        self.assertFalse(handled)
        audit_mock.assert_awaited_once()
        for identity_id in identity_ids:
            with state_module.use_identity(identity_id):
                self.assertFalse(state_module.state["is_maturing"])
                self.assertFalse(state_module.state["is_harvested"])
                self.assertFalse(state_module.state["pending_irrigation"])
                self.assertGreaterEqual(state_module.state["next_irr_time"], now + 45 * 60)
                self.assertLessEqual(state_module.state["next_irr_time"], now + 75 * 60)

    async def test_passive_guard_success_does_not_end_invasion(self):
        identity_id = 3756719391
        now = 1000.0
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="boxboxji")

        with state_module.use_identity(identity_id):
            state_module.state["tree_enabled"] = True
            state_module.state["is_invading"] = True
            state_module.state["pending_irrigation"] = True
            state_module.state["next_irr_time"] = now + 3600
            state_module.state["next_guard_time"] = now + 1800

        text = "【守山成功】\n你向护山大阵注入灵力，暂时稳住了阵脚。"
        with patch.object(passive_inbox, "save_state"), patch.object(passive_inbox, "_save_passive_stats"):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={"send_as_id": identity_id, "family": "tree_guard"},
            )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertTrue(state_module.state["is_invading"])
            self.assertTrue(state_module.state["pending_irrigation"])
            self.assertEqual(now + 3600, state_module.state["next_irr_time"])
            self.assertEqual(now + 1800, state_module.state["next_guard_time"])
