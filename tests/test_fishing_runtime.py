import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import runtime as runtime_module
from model.features import fishing_runtime


FISHING_START_TEXT = """【灵溪垂钓】
钓者：@WalterWA2000
鱼塘：青溪浅滩
天象：小雨
鱼讯：静候鱼讯
进度：□□□□□□□□□□ 0%

你挂上 【灵米饵】，抛竿入水，敛息坐定。
预计 47秒 内会有鱼讯。
可用：.钓鱼状态 / .收竿"""

FISHING_BITE_TEXT = """【灵溪垂钓】
钓者：@WalterWA2000
鱼塘：青溪浅滩
天象：小雨
鱼讯：鱼在试口
进度：■■■■■■■□□□ 67%

鱼讯已至，请在 33秒 内 .提竿。

可用：.试探咬饵 / .提竿 / .收竿
提竿剩余：33秒"""

FISHING_CATCH_TEXT = """【提竿成功】
@WalterWA2000 在 青溪浅滩 猛然提竿，灵线绷成一道银弧。
水下灵光一翻，竟是一尾 【银须灵鲢】！

品阶：灵鱼
重量：1.54斤
钓术：Lv.0 凡竿 (+4)


鱼获已入鱼篓，可用 .开鱼 银须灵鲢 查看鱼腹机缘。"""

OTHER_ANGLER_CATCH_TEXT = FISHING_CATCH_TEXT.replace("@WalterWA2000", "@xianxia_01")

OPEN_FISH_TEXT = """【剖鱼取机缘】
你剖开 【银须灵鲢】x1，鱼腹中灵光微闪。

获得：灵石x28、灵鱼肉x1、灵鱼鳞x1、清灵草x1、修为+39"""


class FishingRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.set_storage_bag_records({})

    def tearDown(self):
        for task in list(fishing_runtime._FOLLOWUP_TASKS.values()):
            if task and not task.done():
                task.cancel()
        fishing_runtime._FOLLOWUP_TASKS.clear()
        for task in list(fishing_runtime._RECOVERY_TASKS.values()):
            if task and not task.done():
                task.cancel()
        fishing_runtime._RECOVERY_TASKS.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_identity(self, identity_id=8659059191):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="walterwa2000")
        return identity_id

    async def test_scheduler_sends_first_planned_fishing_command(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "凡饵"
            state_module.state["fishing_auto_chum_enabled"] = False
            state_module.state["fishing_auto_buy_bait_enabled"] = False
            state_module.state["next_fishing_time"] = now - 1
            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".钓鱼 青溪浅滩 凡饵", track=False, max_retry=0, source_module="灵溪垂钓")
            self.assertEqual(22027, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual(now + fishing_runtime.FISHING_FAST_REPLY_TIMEOUT_SEC, state_module.state["fishing_reply_due_at"])
            self.assertEqual("fishing", state_module.state["fishing_phase"])

    async def test_status_command_uses_short_reply_timeout(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "waiting"
            state_module.state["next_fishing_time"] = now - 1
            state_module.state["fishing_pending_action"] = ".钓鱼状态"
            fake_msg = SimpleNamespace(id=22028, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".钓鱼状态", track=False, priority="urgent_reactive", max_retry=0, source_module="灵溪垂钓")
            self.assertEqual(now + fishing_runtime.FISHING_STATUS_REPLY_TIMEOUT_SEC, state_module.state["fishing_reply_due_at"])
            self.assertEqual("checking", state_module.state["fishing_phase"])

    async def test_initial_check_uses_short_human_delay(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            with patch.object(fishing_runtime.random, "uniform", return_value=30):
                due_at = fishing_runtime.schedule_fishing_initial_check(now, persist=False)

            self.assertEqual(now + 30, due_at)
            self.assertEqual(now + 30, state_module.state["next_fishing_time"])

    async def test_initial_check_preserves_pending_open_reply_after_restart(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "opening"
            state_module.state["fishing_reply_to_msg_id"] = 22042
            state_module.state["fishing_reply_due_at"] = now + 60
            state_module.state["fishing_pending_open_fish"] = '{"青鳞小鲫": 2}'

            due_at = fishing_runtime.schedule_fishing_initial_check(now, persist=False)

            self.assertEqual(now + 60, due_at)
            self.assertEqual("opening", state_module.state["fishing_phase"])
            self.assertEqual(22042, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual('{"青鳞小鲫": 2}', state_module.state["fishing_pending_open_fish"])
            with patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock:
                await fishing_runtime.run_fishing_scheduler(now + 10)

            send_mock.assert_not_awaited()

    async def test_daily_limit_queries_basket_before_opening(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 1
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 1
            state_module.state["next_fishing_time"] = now - 1
            basket_msg = SimpleNamespace(id=22044, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=basket_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".鱼篓", track=False, max_retry=0, source_module="灵溪垂钓")
            self.assertEqual("basket", state_module.state["fishing_phase"])
            self.assertEqual(22044, state_module.state["fishing_reply_to_msg_id"])
            self.assertGreater(state_module.state["next_fishing_time"], now)

    async def test_pending_lift_is_not_blocked_by_daily_limit(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 1
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 1
            state_module.state["fishing_pending_action"] = ".提竿"
            state_module.state["next_fishing_time"] = now - 1
            fake_msg = SimpleNamespace(id=22028, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".提竿", track=False, priority="event_burst", max_retry=0, source_module="灵溪垂钓")
            self.assertEqual(now + fishing_runtime.FISHING_ACTION_REPLY_TIMEOUT_SEC, state_module.state["fishing_reply_due_at"])

    async def test_pending_lift_send_inflight_blocks_status_reentry(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        sent_commands = []

        async def fake_send(command, **_kwargs):
            sent_commands.append(command)
            if command == ".提竿":
                await fishing_runtime.run_fishing_scheduler(now + 0.1)
            return SimpleNamespace(id=22028, sent_at=now)

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "waiting"
            state_module.state["fishing_pending_action"] = ".提竿"
            state_module.state["next_fishing_time"] = now - 1
            state_module.state["fishing_reply_to_msg_id"] = 0
            state_module.state["fishing_reply_due_at"] = 0
            with (
                patch.object(fishing_runtime, "send_game_command", new=fake_send),
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            self.assertEqual([".提竿"], sent_commands)
            self.assertEqual("lifting", state_module.state["fishing_phase"])
            self.assertEqual("", state_module.state["fishing_pending_action"])
            self.assertEqual(22028, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual(now + fishing_runtime.FISHING_ACTION_REPLY_TIMEOUT_SEC, state_module.state["fishing_reply_due_at"])

    async def test_new_fishing_command_waits_when_two_other_rods_active(self):
        identity_id = self._prepare_identity()
        active_a = self._prepare_identity(10001)
        active_b = self._prepare_identity(10002)
        now = 1_700_000_000.0
        with state_module.use_identity(active_a):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "waiting"
            state_module.state["fishing_pending_action"] = ".钓鱼状态"
        with state_module.use_identity(active_b):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "lifting"
            state_module.state["fishing_reply_to_msg_id"] = 22020
            state_module.state["fishing_reply_due_at"] = now + 10
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "凡饵"
            state_module.state["fishing_auto_chum_enabled"] = False
            state_module.state["fishing_auto_buy_bait_enabled"] = False
            state_module.state["next_fishing_time"] = now - 1
            with (
                patch.object(fishing_runtime.random, "uniform", return_value=4),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(now + 4, state_module.state["next_fishing_time"])
            self.assertIn("钓鱼排队中", state_module.state["fishing_last_error"])

    async def test_pending_lift_ignores_new_rod_capacity_limit(self):
        identity_id = self._prepare_identity()
        active_a = self._prepare_identity(10001)
        active_b = self._prepare_identity(10002)
        now = 1_700_000_000.0
        for active_id in (active_a, active_b):
            with state_module.use_identity(active_id):
                state_module.state["fishing_enabled"] = True
                state_module.state["fishing_phase"] = "waiting"
                state_module.state["fishing_pending_action"] = ".钓鱼状态"
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "waiting"
            state_module.state["fishing_pending_action"] = ".提竿"
            state_module.state["next_fishing_time"] = now - 1
            fake_msg = SimpleNamespace(id=22028, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".提竿", track=False, priority="event_burst", max_retry=0, source_module="灵溪垂钓")

    async def test_followup_keeps_pending_until_command_is_sent(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0

        async def fake_send(command, sent_at):
            self.assertEqual(".钓鱼状态", command)
            self.assertEqual(".钓鱼状态", state_module.state["fishing_pending_action"])
            return True

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_pending_action"] = ".钓鱼状态"
            state_module.state["next_fishing_time"] = now
            with patch.object(fishing_runtime, "_send_fishing_command", new=fake_send):
                await fishing_runtime._run_fishing_followup(identity_id, ".钓鱼状态", now)

    async def test_scheduler_recovers_stale_rod_by_status_not_new_fishing(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "waiting"
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now - 1
            state_module.state["next_fishing_time"] = now - 1
            fake_msg = SimpleNamespace(id=22035, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".钓鱼状态", track=False, priority="urgent_reactive", max_retry=0, source_module="灵溪垂钓")
            self.assertNotEqual(".钓鱼 青溪浅滩 凡饵", send_mock.await_args.args[0])

    async def test_scheduler_retries_swallowed_status_inside_fishing_window(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "checking"
            state_module.state["fishing_status_msg_id"] = 22020
            state_module.state["fishing_reply_to_msg_id"] = 22021
            state_module.state["fishing_reply_due_at"] = now - 1
            state_module.state["next_fishing_time"] = now - 1
            fake_msg = SimpleNamespace(id=22022, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".提竿", track=False, priority="event_burst", max_retry=0, source_module="灵溪垂钓")
            self.assertEqual(22022, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual(now + fishing_runtime.FISHING_ACTION_REPLY_TIMEOUT_SEC, state_module.state["fishing_reply_due_at"])

    async def test_recovery_task_advances_swallowed_status_without_global_scheduler(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "checking"
            state_module.state["fishing_reply_to_msg_id"] = 22021
            state_module.state["fishing_reply_due_at"] = now - 1
            state_module.state["next_fishing_time"] = now - 1
            fake_msg = SimpleNamespace(id=22022, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "time") as time_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
            ):
                time_mock.time.return_value = now
                await fishing_runtime._run_fishing_recovery(identity_id, 22021, now - 1)

            send_mock.assert_awaited_once_with(".提竿", track=False, priority="event_burst", max_retry=0, source_module="灵溪垂钓")

    async def test_recovery_task_ignores_stale_anchor(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "checking"
            state_module.state["fishing_reply_to_msg_id"] = 22022
            state_module.state["fishing_reply_due_at"] = now - 1
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "time") as time_mock,
            ):
                time_mock.time.return_value = now
                await fishing_runtime._run_fishing_recovery(identity_id, 22021, now - 1)

            send_mock.assert_not_awaited()

    async def test_scheduler_preserves_open_queue_on_timeout_without_status_loop(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "opening"
            state_module.state["fishing_reply_to_msg_id"] = 22042
            state_module.state["fishing_reply_due_at"] = now - 1
            state_module.state["fishing_pending_open_fish"] = "银须灵鲢"
            state_module.state["next_fishing_time"] = now - 1
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual("银须灵鲢", state_module.state["fishing_pending_open_fish"])
            self.assertEqual(now + 3600, state_module.state["next_fishing_time"])

    async def test_start_status_counts_confirmed_rod_once_and_schedules_status(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "fishing"
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "_schedule_fishing_followup", return_value=True) as followup_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    FISHING_START_TEXT,
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".钓鱼 青溪浅滩 灵米饵"),
                    matched_family="fishing",
                    result_msg_id=22030,
                )

            self.assertTrue(handled)
            self.assertEqual(1, state_module.state["fishing_daily_count"])
            self.assertEqual(fishing_runtime.get_day_key(now), state_module.state["fishing_daily_day"])
            self.assertEqual("waiting", state_module.state["fishing_phase"])
            self.assertEqual(".钓鱼状态", state_module.state["fishing_pending_action"])
            self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
            self.assertGreater(state_module.state["next_fishing_time"], now)
            followup_mock.assert_called_once()
            self.assertEqual(".钓鱼状态", followup_mock.call_args.args[1])

    async def test_bite_status_respects_auto_probe_toggle(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "_schedule_fishing_followup", return_value=True) as followup_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    FISHING_BITE_TEXT,
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".钓鱼状态"),
                    matched_family="fishing",
                    result_msg_id=22031,
                )

            self.assertTrue(handled)
            followup_mock.assert_called_once()
            self.assertEqual(".提竿", followup_mock.call_args.args[1])
            self.assertEqual(".提竿", state_module.state["fishing_pending_action"])
            self.assertEqual("waiting", state_module.state["fishing_phase"])

            state_module.state["fishing_reply_to_msg_id"] = 22032
            state_module.state["fishing_auto_probe_enabled"] = True
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "_schedule_fishing_followup", return_value=True) as probe_followup_mock,
            ):
                await fishing_runtime.handle_fishing_reply(
                    FISHING_BITE_TEXT,
                    now,
                    reply_to=SimpleNamespace(id=22032, raw_text=".钓鱼状态"),
                    matched_family="fishing",
                    result_msg_id=22033,
                )

            probe_followup_mock.assert_called_once()
            self.assertEqual(".试探咬饵", probe_followup_mock.call_args.args[1])
            self.assertEqual(".试探咬饵", state_module.state["fishing_pending_action"])
            self.assertEqual("waiting", state_module.state["fishing_phase"])

    async def test_catch_queues_fish_before_next_rod_without_opening(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    FISHING_CATCH_TEXT,
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".提竿"),
                    matched_family="fishing",
                    result_msg_id=22032,
                )

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual('{"银须灵鲢": 1}', state_module.state["fishing_pending_open_fish"])
            self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["fishing_reply_due_at"])
            self.assertGreater(state_module.state["next_fishing_time"], now)
            self.assertIn("钓获：银须灵鲢", state_module.state["fishing_last_result"])

    async def test_other_angler_catch_does_not_open_fish_for_current_identity(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            state_module.state["fishing_phase"] = "waiting"
            with (
                patch.object(fishing_runtime, "save_state") as save_mock,
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    OTHER_ANGLER_CATCH_TEXT,
                    now,
                    reply_to=SimpleNamespace(id=777, raw_text=".提竿"),
                    matched_family="fishing",
                    result_msg_id=22032,
                )

            self.assertFalse(handled)
            send_mock.assert_not_awaited()
            save_mock.assert_not_called()
            self.assertEqual("waiting", state_module.state["fishing_phase"])
            self.assertEqual(22027, state_module.state["fishing_reply_to_msg_id"])

    async def test_duplicate_lift_is_suppressed_while_lift_reply_is_pending(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "lifting"
            state_module.state["fishing_reply_to_msg_id"] = 22040
            state_module.state["fishing_reply_due_at"] = now + 60
            with patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock:
                sent = await fishing_runtime._send_fishing_command(".提竿", now)

            self.assertFalse(sent)
            send_mock.assert_not_awaited()
            self.assertEqual(22040, state_module.state["fishing_reply_to_msg_id"])

    async def test_late_open_fish_reply_preserves_new_active_rod(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "waiting"
            state_module.state["fishing_reply_to_msg_id"] = 22050
            state_module.state["fishing_reply_due_at"] = now + 60
            state_module.state["fishing_pending_action"] = ".钓鱼状态"
            state_module.state["next_fishing_time"] = now + 30
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    OPEN_FISH_TEXT,
                    now,
                    reply_to=SimpleNamespace(id=22042, raw_text=".开鱼 银须灵鲢"),
                    matched_family="fishing",
                    result_msg_id=22052,
                )

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            self.assertEqual("waiting", state_module.state["fishing_phase"])
            self.assertEqual(22050, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual(".钓鱼状态", state_module.state["fishing_pending_action"])
            self.assertEqual(now + 30, state_module.state["next_fishing_time"])
            self.assertIn("开鱼：银须灵鲢", state_module.state["fishing_last_result"])

    async def test_open_only_reply_calibrates_pending_count(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "opening"
            state_module.state["fishing_reply_to_msg_id"] = 22042
            state_module.state["fishing_reply_due_at"] = now + 60
            state_module.state["fishing_pending_open_fish"] = '{"赤尾火鲤": 7}'
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "_schedule_fishing_followup", return_value=True) as followup_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    "你的鱼篓中只有【赤尾火鲤】x6。",
                    now,
                    reply_to=SimpleNamespace(id=22042, raw_text=".开鱼 赤尾火鲤 7"),
                    matched_family="fishing",
                    result_msg_id=22043,
                )

            self.assertTrue(handled)
            self.assertEqual('{"赤尾火鲤": 6}', state_module.state["fishing_pending_open_fish"])
            self.assertEqual(".开鱼 赤尾火鲤 6", state_module.state["fishing_pending_action"])
            self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
            followup_mock.assert_called_once()

    async def test_in_progress_reply_checks_status_instead_of_starting_new_rod(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "_schedule_fishing_followup", return_value=True) as followup_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    "你已有一竿尚未收起。可用 .钓鱼状态 查看，或 .收竿 放弃。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".钓鱼 青溪浅滩 灵米饵"),
                    matched_family="fishing",
                    result_msg_id=22034,
                )

            self.assertTrue(handled)
            followup_mock.assert_called_once()
            self.assertEqual(".钓鱼状态", followup_mock.call_args.args[1])

    async def test_no_active_fishing_reply_clears_recovery_chain(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "checking"
            state_module.state["fishing_reply_to_msg_id"] = 22043
            state_module.state["fishing_reply_due_at"] = now + 60
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    "你当前没有正在进行的垂钓。",
                    now,
                    reply_to=SimpleNamespace(id=22043, raw_text=".钓鱼状态"),
                    matched_family="fishing",
                    result_msg_id=22044,
                )

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual("", state_module.state["fishing_pending_action"])
            self.assertIn("当前没有正在进行的垂钓", state_module.state["fishing_last_result"])

    async def test_daily_limit_reply_schedules_basket_without_immediate_send(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    "你今日已垂钓 20/20 竿，神识已乏，明日再来。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".钓鱼 青溪浅滩 凡饵"),
                    matched_family="fishing",
                    result_msg_id=22033,
                )

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            self.assertEqual(20, state_module.state["fishing_daily_count"])
            self.assertIn("今日钓鱼次数已达上限：20/20", state_module.state["fishing_last_error"])
            self.assertEqual(".鱼篓", state_module.state["fishing_pending_action"])
            self.assertGreater(state_module.state["next_fishing_time"], now)
            self.assertLess(state_module.state["next_fishing_time"], now + 30)

    async def test_routed_terminal_reply_accepts_stale_anchor(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_last_msg_id"] = 999
            state_module.state["fishing_reply_to_msg_id"] = 888
            state_module.state["fishing_reply_due_at"] = now + 60
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    FISHING_CATCH_TEXT,
                    now,
                    reply_to=SimpleNamespace(id=777, raw_text=".提竿"),
                    matched_family="fishing",
                    result_msg_id=22032,
                )

            self.assertTrue(handled)
            send_mock.assert_not_awaited()

    async def test_swallowed_fishing_reply_without_reply_to_is_accepted_when_pending(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "fishing"
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "_schedule_fishing_followup", return_value=True),
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    FISHING_START_TEXT,
                    now,
                    reply_to=None,
                    matched_family=None,
                    result_msg_id=22030,
                )

            self.assertTrue(handled)
            self.assertEqual("waiting", state_module.state["fishing_phase"])
            self.assertEqual(22030, state_module.state["fishing_status_msg_id"])

    async def test_swallowed_fishing_reply_without_pending_is_ignored(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_reply_to_msg_id"] = 0
            state_module.state["fishing_reply_due_at"] = 0
            with patch.object(fishing_runtime, "save_state") as save_mock:
                handled = await fishing_runtime.handle_fishing_reply(
                    FISHING_START_TEXT,
                    now,
                    reply_to=None,
                    matched_family=None,
                    result_msg_id=22030,
                )

            self.assertFalse(handled)
            save_mock.assert_not_called()

    async def test_resource_shortage_fails_closed_without_forced_buy(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_auto_buy_bait_enabled"] = True
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    "购买失败，当前灵石不足。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".买鱼饵 灵米饵 8"),
                    matched_family="fishing",
                    result_msg_id=22031,
                )

            self.assertTrue(handled)
            self.assertEqual("", state_module.state["fishing_forced_buy_bait"])
            self.assertEqual(0, state_module.state["fishing_forced_buy_count"])
            self.assertIn("灵石不足", state_module.state["fishing_last_error"])
            self.assertGreaterEqual(state_module.state["next_fishing_time"], now + 6 * 3600)
            audit_mock.assert_awaited()

    async def test_known_chum_shortage_uses_configured_buy_batch(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_auto_buy_bait_enabled"] = True
            state_module.state["fishing_auto_buy_bait_count"] = 8
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            with patch.object(fishing_runtime, "save_state"):
                handled = await fishing_runtime.handle_fishing_reply(
                    "打窝失败，资源不足：item_fishing_bait_spirit_ricex3。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".打窝 灵草窝"),
                    matched_family="fishing",
                    result_msg_id=22031,
                )

            self.assertTrue(handled)
            self.assertEqual("灵米饵", state_module.state["fishing_forced_buy_bait"])
            self.assertEqual(8, state_module.state["fishing_forced_buy_count"])

    async def test_routed_manual_buy_updates_storage_when_module_disabled(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            state_module.set_storage_bag_records({
                str(identity_id): {"items": {"灵石": 1000}, "sections": {"材料": {"灵石": 1000}}},
            })
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    "【渔具铺】\n你购得 【灵米饵】x2。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".买鱼饵 灵米饵 2"),
                    matched_family="fishing",
                    result_msg_id=22031,
                )

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            items = state_module.get_storage_bag_records()[str(identity_id)]["items"]
            self.assertEqual(2, items["灵米饵"])
            self.assertEqual(930, items["灵石"])

    async def test_routed_basket_calibrates_storage_when_module_disabled(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            state_module.set_storage_bag_records({
                str(identity_id): {
                    "items": {"凡饵": 5, "灵米饵": 1, "旧物": 7},
                    "sections": {"材料": {"凡饵": 5, "灵米饵": 1, "旧物": 7}},
                },
            })
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    "【鱼篓】\n"
                    "青竹钓竿：已持有\n"
                    "钓术：Lv.1 垂纶（111熟练度）\n"
                    "今日竿数：10/20\n"
                    "当前窝料：无\n\n"
                    "鱼饵\n"
                    "- 灵米饵 x3\n\n"
                    "鱼获\n"
                    "- 银须灵鲢 x1\n\n"
                    "可用 .开鱼 <鱼名> [数量] 查看鱼腹机缘。",
                    now,
                    reply_to=SimpleNamespace(id=22028, raw_text=".鱼篓"),
                    matched_family="fishing",
                    result_msg_id=22032,
                )

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            items = state_module.get_storage_bag_records()[str(identity_id)]["items"]
            self.assertNotIn("凡饵", items)
            self.assertEqual(3, items["灵米饵"])
            self.assertEqual(1, items["银须灵鲢"])
            self.assertEqual(7, items["旧物"])
            self.assertEqual(10, state_module.state["fishing_daily_count"])
            self.assertEqual(20, state_module.state["fishing_daily_limit"])
            self.assertEqual("", state_module.state["fishing_active_chum_name"])
            self.assertEqual(0, state_module.state["fishing_chum_rods_remaining"])

    async def test_routed_basket_calibration_does_not_break_active_rod(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "waiting"
            state_module.state["fishing_reply_to_msg_id"] = 22050
            state_module.state["fishing_reply_due_at"] = now + 30
            state_module.state["fishing_pending_action"] = ".钓鱼状态"
            state_module.state["next_fishing_time"] = now + 20
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "_schedule_fishing_followup", return_value=True) as followup_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    "【鱼篓】\n"
                    "青竹钓竿：已持有\n"
                    "钓术：Lv.1 垂纶（111熟练度）\n"
                    "今日竿数：10/20\n"
                    "当前窝料：无\n\n"
                    "鱼饵\n"
                    "- 灵米饵 x3\n\n"
                    "鱼获\n"
                    "- 银须灵鲢 x1\n\n"
                    "可用 .开鱼 <鱼名> [数量] 查看鱼腹机缘。",
                    now,
                    reply_to=SimpleNamespace(id=22028, raw_text=".鱼篓"),
                    matched_family="fishing",
                    result_msg_id=22032,
                )

            self.assertTrue(handled)
            self.assertEqual("waiting", state_module.state["fishing_phase"])
            self.assertEqual(22050, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual(now + 30, state_module.state["fishing_reply_due_at"])
            self.assertEqual(".钓鱼状态", state_module.state["fishing_pending_action"])
            self.assertEqual(now + 20, state_module.state["next_fishing_time"])
            followup_mock.assert_called_once()

    def test_runtime_send_gap_whitelist_is_fishing_short_window_only(self):
        self.assertTrue(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_URGENT_REACTIVE,
                ".钓鱼状态",
                intent={"source_module": "灵溪垂钓"},
            )
        )
        self.assertTrue(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_EVENT_BURST,
                ".提竿",
                intent={"source_module": "灵溪垂钓"},
            )
        )
        self.assertTrue(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_EVENT_BURST,
                ".开鱼 银须灵鲢",
                intent={"source_module": "灵溪垂钓"},
            )
        )
        self.assertFalse(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_EVENT_BURST,
                ".钓鱼 青溪浅滩 灵米饵",
                intent={"source_module": "灵溪垂钓"},
            )
        )
        self.assertFalse(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_NORMAL,
                ".提竿",
                intent={"source_module": "灵溪垂钓"},
            )
        )
        self.assertFalse(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_EVENT_BURST,
                ".提竿",
                intent={"source_module": "自动副本"},
            )
        )

    def test_runtime_module_gap_enforces_fishing_minimum(self):
        runtime_module._MODULE_LAST_SEND_AT.clear()
        runtime_module._MODULE_LAST_SEND_AT["灵溪垂钓"] = 100.0

        self.assertEqual(
            102.0,
            runtime_module._module_send_gap_ready_at({"source_module": "灵溪垂钓"}, now_mono=100.5),
        )
        self.assertEqual(
            0.0,
            runtime_module._module_send_gap_ready_at({"source_module": "自动副本"}, now_mono=100.5),
        )


if __name__ == "__main__":
    unittest.main()
