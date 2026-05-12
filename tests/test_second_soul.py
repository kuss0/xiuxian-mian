import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.config import CMD_SECOND_SOUL_CHOICE_STABLE
from model.features import second_soul


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class SecondSoulTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_heart_demon_warning_auto_chooses_stable_once(self):
        send_as_id = 8659059191
        now = 1000.0
        event_msg_id = 8798378
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="WalterWA2000")

        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True

        text = (
            "【天道警示·心魔试炼】\n"
            "道友 @WalterWA2000 的第二元神在修炼中遭遇心魔，道心动摇！\n"
            "你必须立即为其做出抉择：\n\n"
            "1. 回复本消息 .抉择 强行突破 (高风险，高回报)\n"
            "2. 回复本消息 .抉择 稳固道心 (低风险，低回报)"
        )

        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=1))) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
        ):
            handled = await second_soul.handle_second_soul_heart_demon_warning_broadcast(text, now, event_msg_id)
            handled_duplicate = await second_soul.handle_second_soul_heart_demon_warning_broadcast(text, now + 1, event_msg_id)

        self.assertTrue(handled)
        self.assertTrue(handled_duplicate)
        send_mock.assert_awaited_once_with(
            CMD_SECOND_SOUL_CHOICE_STABLE,
            track=False,
            reply_to=event_msg_id,
            send_as_id=send_as_id,
            priority="reactive",
        )
        with state_module.use_identity(send_as_id):
            self.assertEqual("heart_demon_pending", state_module.state["second_soul_phase"])
            self.assertEqual(event_msg_id, state_module.state["second_soul_heart_demon_msg_id"])

    async def test_stable_choice_result_enters_train_queue(self):
        send_as_id = 8659059192
        now = 2000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "heart_demon_pending"
            state_module.state["second_soul_heart_demon_msg_id"] = 123

        with (
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
        ):
            handled = await second_soul.handle_second_soul_choice_result_broadcast(
                "【稳扎稳打·成功】\n你稳固道心，成功渡过心魔试炼。",
                now,
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("ready_to_train", state_module.state["second_soul_phase"])
            self.assertEqual(0, state_module.state["second_soul_heart_demon_msg_id"])
            self.assertEqual(now, state_module.state["next_second_soul_time"])

    async def test_heart_demon_warning_respects_disabled_auto_choice(self):
        send_as_id = 8659059193
        now = 3000.0
        event_msg_id = 8798379
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="NoAutoSoul")
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_auto_choice_enabled"] = False

        text = "【天道警示·心魔试炼】\n道友 @NoAutoSoul 的第二元神在修炼中遭遇心魔，道心动摇！"

        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
        ):
            handled = await second_soul.handle_second_soul_heart_demon_warning_broadcast(text, now, event_msg_id)

        self.assertTrue(handled)
        send_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("heart_demon_pending", state_module.state["second_soul_phase"])

    async def test_heart_demon_warning_can_choose_break_strategy(self):
        send_as_id = 8659059194
        now = 4000.0
        event_msg_id = 8798380
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="BreakSoul")
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_choice_strategy"] = "break"

        text = "【天道警示·心魔试炼】\n道友 @BreakSoul 的第二元神在修炼中遭遇心魔，道心动摇！"

        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=1))) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
        ):
            handled = await second_soul.handle_second_soul_heart_demon_warning_broadcast(text, now, event_msg_id)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(
            ".抉择 强行突破",
            track=False,
            reply_to=event_msg_id,
            send_as_id=send_as_id,
            priority="reactive",
        )


if __name__ == "__main__":
    unittest.main()
