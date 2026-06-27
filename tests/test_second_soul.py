import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import ui
from model.config import CMD_SECOND_SOUL_CHOICE_STABLE, CMD_SECOND_SOUL_DEMON_STATUS, CMD_SECOND_SOUL_PURGE, CMD_SECOND_SOUL_STATUS
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
    async def test_status_panel_writes_level_for_ui_even_when_module_disabled(self):
        send_as_id = 8659059188
        now = 500.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = False

            handled = await second_soul.handle_second_soul_status_reply(
                "【你的第二元神：金之元神】\n状态: 窍中温养\n等级: 34 级\n五子同心魔: 5/5 | 同心 100 | 魔染 40",
                now,
                reply_to=SimpleNamespace(raw_text=CMD_SECOND_SOUL_STATUS),
                matched_family="second_soul_status",
            )

        self.assertTrue(handled)
        record = state_module.get_tianjige_dao_path_records()[str(send_as_id)]
        self.assertEqual("34级", record["second_soul_level"])
        identity_snapshot = ui.get_identity_ui_snapshot(send_as_id)
        self.assertEqual("34级", identity_snapshot["second_soul_level_text"])

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

    async def test_return_broadcast_high_moran_sends_single_purge_and_dedupes(self):
        send_as_id = 8659059195
        now = 5000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="MoranSoul")
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "cultivating"

        text = (
            "【第二元神归位】\n"
            "道友 @MoranSoul 的第二元神已结束修炼，回归窍中温养。\n"
            "主魂获得了 43207 点修为，第二元神获得了 2971 点经验。\n"
            "五子流转：同心 100→100，魔染 83→91。"
        )

        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=61, sent_at=now + 1))) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
        ):
            handled = await second_soul.handle_second_soul_return_broadcast(text, now)
            handled_duplicate = await second_soul.handle_second_soul_return_broadcast(text, now + 2)

        self.assertTrue(handled)
        self.assertTrue(handled_duplicate)
        send_mock.assert_awaited_once_with(
            CMD_SECOND_SOUL_PURGE,
            track=False,
            send_as_id=send_as_id,
            priority="chain",
        )
        with state_module.use_identity(send_as_id):
            self.assertEqual("purge_pending", state_module.state["second_soul_phase"])
            self.assertEqual(91, state_module.state["second_soul_moran_value"])
            self.assertEqual(1, state_module.state["second_soul_purge_attempts"])
            self.assertEqual(61, state_module.state["second_soul_purge_msg_id"])

    async def test_purge_no_reply_queries_demon_status_once(self):
        send_as_id = 8659059196
        now = 6000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "purge_pending"
            state_module.state["second_soul_moran_value"] = 91
            state_module.state["second_soul_purge_attempts"] = 1
            state_module.state["second_soul_purge_msg_id"] = 71
            state_module.state["second_soul_purge_due_at"] = now - 1

        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=72, sent_at=now + 1))) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
            state_module.use_identity(send_as_id),
        ):
            await second_soul.run_second_soul_scheduler(now)

        send_mock.assert_awaited_once_with(
            CMD_SECOND_SOUL_DEMON_STATUS,
            track=False,
            send_as_id=send_as_id,
            priority="chain",
        )
        with state_module.use_identity(send_as_id):
            self.assertEqual("purge_status_pending", state_module.state["second_soul_phase"])
            self.assertEqual(72, state_module.state["second_soul_purge_status_msg_id"])
            self.assertEqual(1, state_module.state["second_soul_purge_attempts"])

    async def test_demon_status_high_moran_sends_second_purge_only_once(self):
        send_as_id = 8659059197
        now = 7000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "purge_status_pending"
            state_module.state["second_soul_purge_attempts"] = 1
            state_module.state["second_soul_purge_status_msg_id"] = 81

        reply_to = SimpleNamespace(id=81, raw_text=CMD_SECOND_SOUL_DEMON_STATUS)
        text = "【你的第二元神：金之元神】\n状态: 窍中温养\n等级: 34 级\n五子同心魔: 5/5 | 调度 修炼 | 同心 100 | 魔染 91"
        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=82, sent_at=now + 1))) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
            state_module.use_identity(send_as_id),
        ):
            handled = await second_soul.handle_second_soul_demon_status_reply(
                text,
                now,
                reply_to,
                matched_family="second_soul_demon_status",
            )

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(
            CMD_SECOND_SOUL_PURGE,
            track=False,
            send_as_id=send_as_id,
            priority="chain",
        )
        with state_module.use_identity(send_as_id):
            self.assertEqual("purge_pending", state_module.state["second_soul_phase"])
            self.assertEqual(2, state_module.state["second_soul_purge_attempts"])
            self.assertEqual(82, state_module.state["second_soul_purge_msg_id"])

    async def test_demon_status_low_moran_resumes_train_queue_without_purge(self):
        send_as_id = 8659059198
        now = 8000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_tianjige_dao_path_records({str(send_as_id): {"second_soul_level": "33级"}})
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "purge_status_pending"
            state_module.state["second_soul_purge_attempts"] = 1
            state_module.state["second_soul_purge_status_msg_id"] = 91

        reply_to = SimpleNamespace(id=91, raw_text=CMD_SECOND_SOUL_DEMON_STATUS)
        text = "【你的第二元神：金之元神】\n状态: 窍中温养\n五子同心魔: 5/5 | 同心 100 | 魔染 40"
        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
            state_module.use_identity(send_as_id),
        ):
            handled = await second_soul.handle_second_soul_demon_status_reply(
                text,
                now,
                reply_to,
                matched_family="second_soul_demon_status",
            )

        self.assertTrue(handled)
        send_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("ready_to_train", state_module.state["second_soul_phase"])
            self.assertEqual(40, state_module.state["second_soul_moran_value"])
            self.assertEqual(0, state_module.state["second_soul_purge_attempts"])
        record = state_module.get_tianjige_dao_path_records()[str(send_as_id)]
        self.assertEqual("33级", record["second_soul_level"])

    async def test_purge_reply_low_moran_clears_purge(self):
        send_as_id = 8659059199
        now = 9000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "purge_pending"
            state_module.state["second_soul_purge_attempts"] = 1
            state_module.state["second_soul_purge_msg_id"] = 101

        reply_to = SimpleNamespace(id=101, raw_text=CMD_SECOND_SOUL_PURGE)
        text = "【元神镇魔】\n你耗去 5000 点修为，强行镇压识海中翻腾的五魔。\n魔染度: 91 → 39\n同心度: 100 → 100"
        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
            state_module.use_identity(send_as_id),
        ):
            handled = await second_soul.handle_second_soul_purge_reply(
                text,
                now,
                reply_to,
                matched_family="second_soul_purge",
            )

        self.assertTrue(handled)
        send_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("ready_to_train", state_module.state["second_soul_phase"])
            self.assertEqual(39, state_module.state["second_soul_moran_value"])
            self.assertEqual(0, state_module.state["second_soul_purge_msg_id"])


if __name__ == "__main__":
    unittest.main()
