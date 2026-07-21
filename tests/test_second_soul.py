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
from model.config import (
    CMD_SECOND_SOUL_CHOICE_STABLE,
    CMD_SECOND_SOUL_DEMON_STATUS,
    CMD_SECOND_SOUL_PURGE,
    CMD_SECOND_SOUL_STATUS,
    CMD_SECOND_SOUL_TRAIN,
)
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

    async def test_return_broadcast_purges_at_default_threshold_boundary(self):
        send_as_id = 8659059295
        now = 5050.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="ThresholdSoul")
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "cultivating"
            self.assertEqual(60, second_soul.get_second_soul_purge_threshold())

        text = (
            "【第二元神归位】\n"
            "道友 @ThresholdSoul 的第二元神已结束修炼，回归窍中温养。\n"
            "五子流转：同心 100→100，魔染 58→60。"
        )
        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=62, sent_at=now + 1))) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
        ):
            handled = await second_soul.handle_second_soul_return_broadcast(text, now)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(
            CMD_SECOND_SOUL_PURGE,
            track=False,
            send_as_id=send_as_id,
            priority="chain",
        )

    async def test_return_broadcast_below_threshold_or_unknown_does_not_purge(self):
        for offset, suffix in enumerate(("五子流转：同心 100→100，魔染 58→59。", "本轮修炼平稳结束。")):
            send_as_id = 8659059296 + offset
            username = f"SafeThresholdSoul{offset}"
            now = 5100.0 + offset
            state_module.ensure_identity_registered(send_as_id)
            state_module.update_send_as_profile(send_as_id, username=username)
            with state_module.use_identity(send_as_id):
                state_module.state["second_soul_enabled"] = True
                state_module.state["second_soul_phase"] = "cultivating"

            text = (
                "【第二元神归位】\n"
                f"道友 @{username} 的第二元神已结束修炼，回归窍中温养。\n"
                f"{suffix}"
            )
            with (
                patch.object(second_soul, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(second_soul, "send_audit_log", new=AsyncMock()),
                patch.object(second_soul, "save_state"),
            ):
                handled = await second_soul.handle_second_soul_return_broadcast(text, now)

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            with state_module.use_identity(send_as_id):
                self.assertEqual("ready_to_train", state_module.state["second_soul_phase"])

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

    async def test_demon_status_does_not_update_second_soul_level(self):
        send_as_id = 8659059200
        now = 8100.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_tianjige_dao_path_records({str(send_as_id): {"second_soul_level": "33级"}})
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = False

            handled = await second_soul.handle_second_soul_demon_status_reply(
                "【五子同心魔】\n等级: 34 级\n五子同心魔: 5/5 | 同心 100 | 魔染 40",
                now,
                reply_to=SimpleNamespace(id=91, raw_text=CMD_SECOND_SOUL_DEMON_STATUS),
                matched_family="second_soul_demon_status",
            )

        self.assertFalse(handled)
        record = state_module.get_tianjige_dao_path_records()[str(send_as_id)]
        self.assertEqual("33级", record["second_soul_level"])

    async def test_busy_training_without_remaining_is_short_recheck_not_end_time(self):
        send_as_id = 8659059201
        now = 8200.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "train_pending"
            state_module.state["second_soul_train_msg_id"] = 101

        with (
            patch.object(second_soul.random, "uniform", return_value=1800),
            patch.object(second_soul, "save_state"),
            state_module.use_identity(send_as_id),
        ):
            handled = await second_soul.handle_second_soul_train_reply(
                "你的第二元神正在(修炼中)，无法分心修炼。",
                now,
                reply_to=SimpleNamespace(id=101, raw_text=".元神修炼"),
                matched_family="second_soul_train",
            )
            status_text = second_soul.get_second_soul_status_text()

        self.assertTrue(handled)
        self.assertEqual("cultivating", state_module.state["second_soul_phase"])
        self.assertEqual(now + 1800, state_module.state["next_second_soul_time"])
        self.assertIn("短复查", state_module.state["second_soul_last_error"])
        self.assertIn("下次复查", status_text)
        self.assertNotIn("修炼结束", status_text)

    async def test_heart_demon_missing_deadline_is_repaired(self):
        send_as_id = 8659059202
        now = 8300.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "heart_demon_pending"
            state_module.state["second_soul_heart_demon_deadline"] = 0

        with (
            patch.object(second_soul, "save_state"),
            patch.object(second_soul, "send_game_command", new=AsyncMock()) as send_mock,
            state_module.use_identity(send_as_id),
        ):
            await second_soul.run_second_soul_scheduler(now)

        send_mock.assert_not_awaited()
        self.assertEqual("heart_demon_pending", state_module.state["second_soul_phase"])
        self.assertEqual(now + second_soul.SECOND_SOUL_HEART_DEMON_DEADLINE_SEC, state_module.state["second_soul_heart_demon_deadline"])

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

    async def test_status_send_timeout_stays_pending_for_late_reply(self):
        send_as_id = 8659059203
        now = 9100.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "idle"
            state_module.state["next_second_soul_time"] = now - 1

        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
            patch.object(second_soul, "classify_game_send_block", return_value={"status": "unknown", "code": "send_timeout"}),
            patch.object(second_soul.random, "uniform", return_value=1800),
            patch.object(second_soul.time, "time", return_value=now + 2),
            patch.object(second_soul, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(second_soul, "save_state"),
            state_module.use_identity(send_as_id),
        ):
            await second_soul.run_second_soul_scheduler(now)

        send_mock.assert_awaited_once_with(CMD_SECOND_SOUL_STATUS, track=False, priority="chain")
        with state_module.use_identity(send_as_id):
            self.assertEqual("status_pending", state_module.state["second_soul_phase"])
            self.assertEqual(0, state_module.state["second_soul_status_msg_id"])
            self.assertEqual(now + 2 + 1800, state_module.state["next_second_soul_time"])
            self.assertIn("状态未知", state_module.state["second_soul_last_error"])
        self.assertIn("状态未知", audit_mock.await_args.args[0])

    async def test_train_send_timeout_stays_pending_for_late_reply(self):
        send_as_id = 8659059204
        now = 9200.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "ready_to_train"
            state_module.state["next_second_soul_time"] = now - 1

        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
            patch.object(second_soul, "classify_game_send_block", return_value={"status": "unknown", "code": "send_timeout"}),
            patch.object(second_soul.random, "uniform", return_value=2100),
            patch.object(second_soul.time, "time", return_value=now + 3),
            patch.object(second_soul, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(second_soul, "save_state"),
            state_module.use_identity(send_as_id),
        ):
            await second_soul.run_second_soul_scheduler(now)

        send_mock.assert_awaited_once_with(CMD_SECOND_SOUL_TRAIN, track=False, priority="chain")
        with state_module.use_identity(send_as_id):
            self.assertEqual("train_pending", state_module.state["second_soul_phase"])
            self.assertEqual(0, state_module.state["second_soul_train_msg_id"])
            self.assertEqual(now + 3 + 2100, state_module.state["next_second_soul_time"])
            self.assertIn("状态未知", state_module.state["second_soul_last_error"])
        self.assertIn("状态未知", audit_mock.await_args.args[0])

    async def test_train_send_queue_timeout_returns_to_ready(self):
        send_as_id = 8659059205
        now = 9300.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "ready_to_train"
            state_module.state["next_second_soul_time"] = now - 1

        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=None)),
            patch.object(second_soul, "classify_game_send_block", return_value={"status": "unsent", "code": "send_queue_timeout"}),
            patch.object(second_soul.time, "time", return_value=now + 4),
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
            state_module.use_identity(send_as_id),
        ):
            await second_soul.run_second_soul_scheduler(now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("ready_to_train", state_module.state["second_soul_phase"])
            self.assertEqual(now + 4 + 600, state_module.state["next_second_soul_time"])
            self.assertEqual(0, state_module.state["second_soul_train_msg_id"])
            self.assertEqual("", state_module.state["second_soul_last_error"])

    async def test_train_global_recovery_hold_is_not_a_business_error(self):
        send_as_id = 8659059210
        now = 9350.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "ready_to_train"
            state_module.state["next_second_soul_time"] = now - 1
            state_module.state["second_soul_last_error"] = "old error"

        with (
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=None)),
            patch.object(second_soul, "classify_game_send_block", return_value={"status": "unsent", "code": "global_recovery_cooldown"}),
            patch.object(second_soul.time, "time", return_value=now + 2),
            patch.object(second_soul, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(second_soul, "save_state"),
            state_module.use_identity(send_as_id),
        ):
            await second_soul.run_second_soul_scheduler(now)

        with state_module.use_identity(send_as_id):
            self.assertEqual("ready_to_train", state_module.state["second_soul_phase"])
            self.assertEqual(now + 2 + 600, state_module.state["next_second_soul_time"])
            self.assertEqual("", state_module.state["second_soul_last_error"])
        self.assertIn("global_recovery_cooldown", audit_mock.await_args.args[0])

    async def test_train_timeout_recovers_unknown_send_and_logged_reply(self):
        send_as_id = 8659059206
        now = 9400.0
        state_module.ensure_identity_registered(send_as_id)
        recovered_command = {
            "message_id": 301,
            "text": CMD_SECOND_SOUL_TRAIN,
            "ts_epoch": now - 40,
        }
        recovered_reply = {
            "message_id": 302,
            "reply_to_msg_id": 301,
            "event_type": "message",
            "chat_id": state_module.get_game_group_id(),
            "sender_is_bot": True,
            "text": "你的第二元神已开始闭关修炼，本次修炼将持续24小时。",
            "ts_epoch": now - 39,
        }
        with state_module.use_identity(send_as_id):
            state_module.state["second_soul_enabled"] = True
            state_module.state["second_soul_phase"] = "train_pending"
            state_module.state["next_second_soul_time"] = now - 1
            state_module.state["second_soul_train_msg_id"] = 0

        with (
            patch.object(second_soul, "recover_sent_command_from_message_log", return_value=recovered_command),
            patch.object(second_soul, "find_message_log_replies", return_value=[recovered_reply]),
            patch.object(second_soul, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
            patch.object(second_soul, "console_log"),
            state_module.use_identity(send_as_id),
        ):
            await second_soul.run_second_soul_scheduler(now)

        send_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("cultivating", state_module.state["second_soul_phase"])
            self.assertGreater(state_module.state["next_second_soul_time"], now + 23 * 3600)
            self.assertEqual(0, state_module.state["second_soul_train_msg_id"])


if __name__ == "__main__":
    unittest.main()
