import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch


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

VALUABLE_OPEN_FISH_TEXT = """【剖鱼取机缘】
你剖开 【银须灵鲢】x1，鱼腹中灵光一震，竟牵出伴生机缘。

获得：灵石x28、【大衍诀】x1、修为+39"""


class FishingRuntimeTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.set_storage_bag_records({})

    def tearDown(self):
        fishing_runtime._SEND_LOCKS.clear()
        fishing_runtime._RECENT_COMMANDS.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_identity(self, identity_id=8659059191):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="walterwa2000")
        return identity_id

    def _local_ts(self, year, month, day, hour, minute=0, second=0):
        return datetime(year, month, day, hour, minute, second, tzinfo=fishing_runtime.TZ_LOCAL).timestamp()

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

    def test_private_rod_followup_and_recovery_tasks_are_removed(self):
        for name in (
            "_FOLLOWUP_TASKS",
            "_RECOVERY_TASKS",
            "_schedule_fishing_followup",
            "_schedule_fishing_recovery",
            "_run_fishing_followup",
            "_run_fishing_recovery",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(fishing_runtime, name))

    async def test_deprecated_rod_actions_are_discarded_without_sending(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        for command in (".钓鱼状态", ".试探咬饵", ".提竿", ".收竿"):
            with self.subTest(command=command), state_module.use_identity(identity_id):
                state_module.state["fishing_enabled"] = True
                state_module.state["fishing_phase"] = "waiting"
                state_module.state["fishing_pending_action"] = command
                state_module.state["fishing_reply_to_msg_id"] = 22027
                state_module.state["fishing_reply_due_at"] = now + 30
                state_module.state["next_fishing_time"] = now - 1
                with (
                    patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                    patch.object(fishing_runtime, "save_state"),
                    patch.object(fishing_runtime.random, "uniform", return_value=0),
                ):
                    sent = await fishing_runtime._send_fishing_command(command, now)

                self.assertFalse(sent)
                send_mock.assert_not_awaited()
                self.assertEqual("idle", state_module.state["fishing_phase"])
                self.assertEqual("", state_module.state["fishing_pending_action"])
                self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
                self.assertEqual(now + fishing_runtime.FISHING_MINIAPP_FAILURE_BACKOFF_SEC, state_module.state["next_fishing_time"])

    async def test_selected_public_miniapp_identity_never_falls_back_to_text_scheduler(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_TEST"],
            "cave_public_fishing_enabled": False,
            "cave_public_fishing_identity_ids": [identity_id],
        }
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "waiting"
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now - 1
            state_module.state["fishing_pending_action"] = ".钓鱼状态"
            state_module.state["next_fishing_time"] = now - 1
            with patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock:
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual("", state_module.state["fishing_pending_action"])
            self.assertEqual(now + fishing_runtime.FISHING_MINIAPP_FAILURE_BACKOFF_SEC, state_module.state["next_fishing_time"])
            self.assertIn("禁止回退", state_module.state["fishing_last_error"])


    async def test_scheduler_defers_new_fishing_when_miniapp_capacity_full(self):
        identity_id = self._prepare_identity()
        other_id = self._prepare_identity(10001)
        another_id = self._prepare_identity(10002)
        now = 1_700_000_000.0

        for active_id in (other_id, another_id):
            with state_module.use_identity(active_id):
                state_module.state["fishing_enabled"] = True
                state_module.state["fishing_phase"] = "miniapp"

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "凡饵"
            state_module.state["fishing_auto_chum_enabled"] = False
            state_module.state["fishing_auto_buy_bait_enabled"] = False
            state_module.state["next_fishing_time"] = now - 1
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(now + 4, state_module.state["next_fishing_time"])
            self.assertIn("钓鱼排队中", state_module.state["fishing_last_error"])


    async def test_timeout_recovers_logged_reply_before_status_fallback(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 4, 6, 40, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-07-04.log"
            entries = [
                {
                    "ts": "2026-07-04 06:39:40 UTC+8",
                    "event_type": "message",
                    "message_id": 22027,
                    "chat_id": state_module.get_game_group_id(),
                    "sender_id": identity_id,
                    "reply_to_msg_id": 0,
                    "text": ".提竿",
                },
                {
                    "ts": "2026-07-04 06:39:42 UTC+8",
                    "event_type": "message",
                    "message_id": 22028,
                    "chat_id": state_module.get_game_group_id(),
                    "sender_id": 8609885831,
                    "reply_to_msg_id": 22027,
                    "text": FISHING_CATCH_TEXT,
                },
            ]
            log_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in entries) + "\n", encoding="utf-8")

            with state_module.use_identity(identity_id):
                state_module.state["fishing_enabled"] = True
                state_module.state["fishing_phase"] = "lifting"
                state_module.state["fishing_pending_action"] = ".提竿"
                state_module.state["fishing_reply_to_msg_id"] = 22027
                state_module.state["fishing_reply_due_at"] = now - 1
                state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
                with (
                    patch("model.message_log_recovery.MESSAGES_DIR", tmpdir),
                    patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                    patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                    patch.object(fishing_runtime, "save_state"),
                ):
                    await fishing_runtime.run_fishing_scheduler(now)

                send_mock.assert_not_awaited()
                self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
                self.assertEqual(22028, state_module.state["fishing_last_msg_id"])
                self.assertEqual("", state_module.state["fishing_last_error"])

    def _miniapp_event(self, url="https://t.me/fanrenxiuxian_bot/app?startapp=fish_TEST1234"):
        return SimpleNamespace(
            id=33001,
            message=SimpleNamespace(
                buttons=[
                    [
                        SimpleNamespace(
                            text="进入钓鱼",
                            button=SimpleNamespace(url=url),
                        )
                    ]
                ]
            ),
        )

    async def test_miniapp_entry_is_ignored_when_fishing_disabled(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 50, 0)

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock()) as flow_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

        self.assertFalse(handled)
        flow_mock.assert_not_awaited()

    async def test_unclaimed_miniapp_entry_does_not_fall_back_to_text_followups(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 50, 30)

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "fishing"
            state_module.state["fishing_pending_action"] = ".钓鱼状态"
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 5
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.hold_unclaimed_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】\n钓者：@WalterWA2000\n点击下方 进入灵溪垂钓，依水面变化稳住灵线，即可提竿。",
                    now,
                    result_msg_id=33001,
                )
                await fishing_runtime.run_fishing_scheduler(now + 60)

        self.assertTrue(handled)
        send_mock.assert_not_awaited()
        audit_mock.assert_awaited_once()
        self.assertEqual("idle", state_module.state["fishing_phase"])
        self.assertEqual("", state_module.state["fishing_pending_action"])
        self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
        self.assertIn("本竿不回退", state_module.state["fishing_last_error"])
        self.assertEqual(now + fishing_runtime.FISHING_MINIAPP_FAILURE_BACKOFF_SEC, state_module.state["next_fishing_time"])

    async def test_cave_entry_does_not_hold_or_mutate_fishing(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 11, 0, 23, 25)
        cave_event = self._miniapp_event(
            "https://t.me/hantianzun19_bot?startapp=df_SECRET999"
        )
        cave_event.message.buttons[0][0].text = "进入洞府"

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "idle"
            state_module.state["fishing_last_result"] = "existing"
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(fishing_runtime, "save_state") as save_mock,
            ):
                held = await fishing_runtime.hold_unclaimed_fishing_miniapp_entry(
                    cave_event,
                    "【洞府】点击下方进入洞府，查看洞天布置。",
                    now,
                    result_msg_id=14484,
                )

        self.assertFalse(held)
        audit_mock.assert_not_awaited()
        save_mock.assert_not_called()
        self.assertEqual("existing", state_module.state["fishing_last_result"])

    async def test_unclaimed_miniapp_entry_prompt_without_button_still_blocks_text_followups(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 50, 35)

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "fishing"
            state_module.state["fishing_pending_action"] = ".钓鱼状态"
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 5
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.hold_unclaimed_fishing_miniapp_entry(
                    SimpleNamespace(id=33003, message=SimpleNamespace(buttons=[])),
                    "【灵溪垂钓】\n钓者：@WalterWA2000\n\n点击下方 进入灵溪垂钓，依水面变化稳住灵线，即可提竿。",
                    now,
                    result_msg_id=33003,
                )
                await fishing_runtime.run_fishing_scheduler(now + 60)

        self.assertTrue(handled)
        send_mock.assert_not_awaited()
        self.assertEqual("idle", state_module.state["fishing_phase"])
        self.assertEqual("", state_module.state["fishing_pending_action"])
        self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])


    async def test_miniapp_entry_respects_global_pause_before_http(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 51, 0)

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            with (
                patch.object(fishing_runtime, "get_global_enabled", return_value=False),
                patch.object(fishing_runtime, "get_global_pause_source", return_value="safety_watchdog"),
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock()) as flow_mock,
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=600),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            flow_mock.assert_not_awaited()
            self.assertIn("全局暂停", state_module.state["fishing_last_result"])
            self.assertEqual(now + 600, state_module.state["next_fishing_time"])
            audit_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list)
            self.assertIn("全局暂停", audit_text)

    async def test_miniapp_entry_allows_http_during_tianzun_maintenance_pause(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 10, 7, 51, 0)
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {"fish": "青鳞小鲫", "settled_count": 1},
            "events": [{"step": "round", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            with (
                patch.object(fishing_runtime, "get_global_enabled", return_value=False),
                patch.object(fishing_runtime, "get_global_pause_source", return_value="tianzun_maintenance"),
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock,
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            flow_mock.assert_awaited_once()
            audit_text = "\n".join(str(call.args[0]) for call in audit_mock.await_args_list)
            self.assertIn("天尊维护暂停中，仅执行 MiniApp HTTP", audit_text)

    async def test_miniapp_entry_runs_flow_and_updates_daily_counter(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 52, 0)
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {"fish": "银须灵鲢", "reward": "灵石x28"},
            "events": [{"step": "result", "ok": True}],
            "proof": {"score": 94},
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 2
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock,
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            flow_mock.assert_awaited_once_with(
                identity_id,
                token="fish_TEST1234",
                webview_url="https://t.me/fanrenxiuxian_bot/app?startapp=fish_TEST1234",
                max_rounds=fishing_runtime.FISHING_MINIAPP_CHAIN_PROTECT_ROUNDS,
                pond_choice="青溪浅滩",
                bait_choice="凡饵",
                capture_sink=ANY,
                capture_source="fishing_runtime:8659059191:33001",
            )
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual(33001, state_module.state["fishing_last_msg_id"])
            self.assertEqual(3, state_module.state["fishing_daily_count"])
            self.assertIn("MiniApp settled", state_module.state["fishing_last_result"])
            self.assertEqual("", state_module.state["fishing_last_error"])
            self.assertEqual(now + 4, state_module.state["next_fishing_time"])

    async def test_miniapp_entry_counts_chained_rounds_without_second_chat_send(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 53, 0)
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {"settled_count": 4, "rounds": [{}, {}, {}, {}]},
            "events": [{"step": "round", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 1
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock,
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            flow_mock.assert_awaited_once_with(
                identity_id,
                token="fish_TEST1234",
                webview_url="https://t.me/fanrenxiuxian_bot/app?startapp=fish_TEST1234",
                max_rounds=fishing_runtime.FISHING_MINIAPP_CHAIN_PROTECT_ROUNDS,
                pond_choice="青溪浅滩",
                bait_choice="凡饵",
                capture_sink=ANY,
                capture_source="fishing_runtime:8659059191:33001",
            )
            send_mock.assert_not_awaited()
            self.assertEqual(5, state_module.state["fishing_daily_count"])
            self.assertIn("4竿", state_module.state["fishing_last_result"])

    async def test_miniapp_entry_uses_remaining_rods_without_fixed_five_cap(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 53, 30)
        flow_result = {
            "ok": True,
            "status": "next_unavailable",
            "data": {"settled_count": 1, "next_status": "missing_token"},
            "events": [{"step": "next", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 20
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 2
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock,
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            flow_mock.assert_awaited_once_with(
                identity_id,
                token="fish_TEST1234",
                webview_url="https://t.me/fanrenxiuxian_bot/app?startapp=fish_TEST1234",
                max_rounds=fishing_runtime.FISHING_MINIAPP_CHAIN_PROTECT_ROUNDS,
                pond_choice="青溪浅滩",
                bait_choice="凡饵",
                capture_sink=ANY,
                capture_source="fishing_runtime:8659059191:33001",
            )

    async def test_miniapp_entry_does_not_cap_chain_by_stale_local_daily_limit(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 53, 35)
        flow_result = {
            "ok": True,
            "status": "daily_limit",
            "data": {"settled_count": 30, "next_status": "daily_limit"},
            "events": [{"step": "next", "ok": False}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 20
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)) as flow_mock,
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

        self.assertTrue(handled)
        self.assertEqual(fishing_runtime.FISHING_MINIAPP_CHAIN_PROTECT_ROUNDS, flow_mock.await_args.kwargs["max_rounds"])
        send_mock.assert_not_awaited()

    async def test_miniapp_result_calibrates_daily_progress_from_api_payload(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 53, 40)
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {
                "settled_count": 1,
                "daily": {"used": 6, "limit": 30, "remaining": 24},
                "catches": [{"fish": "银须灵鲢"}],
            },
            "events": [{"step": "result", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 20
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 4
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            self.assertEqual(30, state_module.state["fishing_daily_limit"])
            self.assertEqual(6, state_module.state["fishing_daily_count"])

    async def test_miniapp_next_unavailable_backs_off_instead_of_chat_resend(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 54, 0)
        flow_result = {
            "ok": True,
            "status": "next_unavailable",
            "data": {"settled_count": 1, "next_status": "missing_token"},
            "events": [{"step": "next", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 1
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )
                await fishing_runtime.run_fishing_scheduler(now + 10)

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            self.assertEqual(2, state_module.state["fishing_daily_count"])
            self.assertIn("next_unavailable", state_module.state["fishing_last_error"])
            self.assertEqual(now + fishing_runtime.FISHING_MINIAPP_FAILURE_BACKOFF_SEC, state_module.state["next_fishing_time"])

    async def test_miniapp_next_missing_bait_keeps_settled_count_and_backs_off(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 54, 10)
        flow_result = {
            "ok": True,
            "status": "next_failed",
            "error": "fishing_bait_missing",
            "data": {
                "settled_count": 1,
                "next_status": "failed",
                "next_error": "fishing_bait_missing",
                "next_bait_name": "凡饵",
                "catches": [{"fish": "银须灵鲢", "weight": "1.3斤"}],
            },
            "events": [{"step": "next", "ok": False}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 1
            state_module.state["fishing_auto_buy_bait_enabled"] = False
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            self.assertEqual(2, state_module.state["fishing_daily_count"])
            self.assertEqual("凡饵", state_module.state["fishing_forced_buy_bait"])
            self.assertIn("缺少鱼饵：凡饵", state_module.state["fishing_last_error"])
            self.assertEqual(now + fishing_runtime.fishing_behavior.FISHING_BLOCKED_RETRY_SEC, state_module.state["next_fishing_time"])
            self.assertIn("银须灵鲢", state_module.state["fishing_daily_catch_summary_json"])

    async def test_miniapp_not_ready_does_not_increment_daily_counter(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 54, 30)
        flow_result = {
            "ok": False,
            "status": "not_ready",
            "error": "result_not_ready",
            "data": {"phase": "finish_submitted", "ready": False},
            "events": [{"step": "result_wait", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 2
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            self.assertEqual(2, state_module.state["fishing_daily_count"])
            self.assertIn("MiniApp not_ready", state_module.state["fishing_last_error"])
            self.assertEqual(now + fishing_runtime.FISHING_MINIAPP_FAILURE_BACKOFF_SEC, state_module.state["next_fishing_time"])

    async def test_miniapp_no_rod_skips_identity_without_error_or_daily_consumption(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 55, 0)
        flow_result = {
            "ok": False,
            "status": "no_rod",
            "error": "你需要先在商城购买鱼竿",
            "data": {"terminal_skip": True, "rod_required": True},
            "events": [{"step": "start_waiting", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 2
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33002,
                )

            self.assertTrue(handled)
            self.assertEqual(2, state_module.state["fishing_daily_count"])
            self.assertEqual("未持有鱼竿，今日跳过", state_module.state["fishing_last_result"])
            self.assertEqual("", state_module.state["fishing_last_error"])
            self.assertNotIn("MiniApp 异常", "\n".join(str(call.args[0]) for call in audit_mock.await_args_list if call.args))

    async def test_miniapp_partial_not_ready_counts_only_settled_rounds(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 54, 40)
        flow_result = {
            "ok": True,
            "status": "not_ready",
            "data": {"settled_count": 2, "last_status": "not_ready"},
            "events": [{"step": "round", "ok": False}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 1
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            self.assertEqual(3, state_module.state["fishing_daily_count"])
            self.assertIn("MiniApp not_ready", state_module.state["fishing_last_error"])
            self.assertEqual(now + fishing_runtime.FISHING_MINIAPP_FAILURE_BACKOFF_SEC, state_module.state["next_fishing_time"])

    async def test_miniapp_daily_limit_after_chained_rounds_calibrates_to_full(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 55, 0)
        flow_result = {
            "ok": True,
            "status": "daily_limit",
            "data": {"settled_count": 4, "next_status": "daily_limit"},
            "events": [{"step": "next", "ok": False}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            self.assertEqual(5, state_module.state["fishing_daily_count"])
            self.assertEqual(5, state_module.state["fishing_daily_limit"])
            self.assertGreater(state_module.state["next_fishing_time"], now)
            self.assertLess(state_module.state["next_fishing_time"], now + 60)
            self.assertEqual("", state_module.state["fishing_basket_calibrated_day"])

    async def test_miniapp_result_records_catch_summary_and_transfer_items(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 56, 0)
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {
                "settled_count": 1,
                "catches": [{
                    "fish": "银须灵鲢",
                    "grade": "灵鱼",
                    "weight": "2.88斤",
                    "rewards": [{"name": "幸运符", "qty": 1}],
                    "companion": True,
                }],
            },
            "events": [{"step": "round", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            state_module.state["fishing_transfer_target_id"] = 301299112
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "apply_storage_bag_item_deltas") as delta_mock,
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            self.assertIn("渔获:银须灵鲢", state_module.state["fishing_last_result"])
            self.assertIn("幸运符x1", state_module.state["fishing_last_result"])
            self.assertEqual('{"银须灵鲢": 1}', state_module.state["fishing_caught_fish_json"])
            delta_mock.assert_called_once_with(identity_id, {"银须灵鲢": 1})
            audit_texts = [call.args[0] for call in audit_mock.await_args_list]
            self.assertTrue(any("MiniApp 接管入口" in text for text in audit_texts))
            harvest_texts = [text for text in audit_texts if "MiniApp 收获" in text]
            self.assertEqual(1, len(harvest_texts))
            self.assertIn("银须灵鲢", harvest_texts[0])
            self.assertIn("幸运符x1", harvest_texts[0])
            self.assertFalse(any("灵溪垂钓日结" in text for text in audit_texts))

    async def test_miniapp_reward_only_summary_reports_materials_not_fields(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 56, 30)
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {
                "settled_count": 1,
                "bonusLoot": [{"name": "幸运符", "qty": 1}],
                "score": 96,
                "session_id": 12253,
            },
            "events": [{"step": "round", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "apply_storage_bag_item_deltas"),
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            self.assertIn("奖励:幸运符x1", state_module.state["fishing_last_result"])
            self.assertNotIn("结果字段", state_module.state["fishing_last_result"])
            self.assertNotIn("score", state_module.state["fishing_last_result"])
            self.assertIn("幸运符", state_module.state["fishing_daily_catch_summary_json"])
            audit_texts = [call.args[0] for call in audit_mock.await_args_list]
            harvest_texts = [text for text in audit_texts if "MiniApp 收获" in text]
            self.assertEqual(1, len(harvest_texts))
            self.assertIn("幸运符x1", harvest_texts[0])
            self.assertNotIn("score", harvest_texts[0])
            self.assertNotIn("session", harvest_texts[0])

    async def test_miniapp_technical_only_result_does_not_report_harvest_fields(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 56, 45)
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {
                "settled_count": 1,
                "score": 96,
                "session_id": 12253,
                "quality_bonus": 0.27,
            },
            "events": [{"step": "round", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "apply_storage_bag_item_deltas"),
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

            self.assertTrue(handled)
            self.assertIn("未解析到新增物资", state_module.state["fishing_last_result"])
            self.assertNotIn("结果字段", state_module.state["fishing_last_result"])
            audit_texts = [call.args[0] for call in audit_mock.await_args_list]
            self.assertFalse(any("MiniApp 收获" in text for text in audit_texts))

    async def test_miniapp_daily_completion_sends_one_catch_summary(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 57, 0)
        day_key = fishing_runtime.get_day_key(now)
        flow_result = {
            "ok": True,
            "status": "settled",
            "data": {
                "settled_count": 1,
                "catches": [{
                    "fish": "银须灵鲢",
                    "grade": "灵鱼",
                    "weight": "2.88斤",
                    "rewards": [{"name": "灵石", "qty": 28}],
                }],
            },
            "events": [{"step": "round", "ok": True}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = day_key
            state_module.state["fishing_daily_count"] = 4
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "apply_storage_bag_item_deltas"),
                patch.object(fishing_runtime.random, "uniform", return_value=4),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )
                await fishing_runtime._send_fishing_daily_completion_summary(now + 1)

            self.assertTrue(handled)
            self.assertEqual(5, state_module.state["fishing_daily_count"])
            self.assertEqual(day_key, state_module.state["fishing_daily_summary_day"])
            self.assertIn("银须灵鲢", state_module.state["fishing_daily_catch_summary_json"])
            audit_texts = [call.args[0] for call in audit_mock.await_args_list]
            daily_texts = [text for text in audit_texts if "灵溪垂钓日结" in text]
            self.assertEqual(1, len(daily_texts))
            self.assertIn("5/5竿", daily_texts[0])
            self.assertIn("银须灵鲢x1", daily_texts[0])
            self.assertIn("灵石x28", daily_texts[0])

    async def test_daily_completion_waits_for_all_enabled_fishing_identities(self):
        identity_id = self._prepare_identity()
        other_id = self._prepare_identity(10001)
        now = self._local_ts(2026, 7, 6, 8, 0, 0)
        day_key = fishing_runtime.get_day_key(now)

        for target_id, count in ((identity_id, 5), (other_id, 4)):
            with state_module.use_identity(target_id):
                state_module.state["fishing_enabled"] = True
                state_module.state["fishing_daily_limit"] = 5
                state_module.state["fishing_daily_day"] = day_key
                state_module.state["fishing_daily_count"] = count
                state_module.state["fishing_daily_catch_summary_json"] = json.dumps({
                    "day": day_key,
                    "rods": count,
                    "fish": {"银须灵鲢": count},
                    "rewards": {},
                }, ensure_ascii=False)

        with state_module.use_identity(identity_id):
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                sent = await fishing_runtime._send_fishing_daily_completion_summary(now)

        self.assertFalse(sent)
        audit_mock.assert_not_awaited()
        self.assertEqual("", state_module.get_identity_state(identity_id)["fishing_daily_summary_day"])
        self.assertEqual("", state_module.get_identity_state(other_id)["fishing_daily_summary_day"])

    async def test_daily_completion_sends_one_all_identity_summary(self):
        identity_id = self._prepare_identity()
        other_id = self._prepare_identity(10001)
        now = self._local_ts(2026, 7, 6, 8, 5, 0)
        day_key = fishing_runtime.get_day_key(now)

        for target_id, fish_name in ((identity_id, "银须灵鲢"), (other_id, "赤尾火鲤")):
            with state_module.use_identity(target_id):
                state_module.state["fishing_enabled"] = True
                state_module.state["fishing_daily_limit"] = 5
                state_module.state["fishing_daily_day"] = day_key
                state_module.state["fishing_daily_count"] = 5
                state_module.state["fishing_daily_catch_summary_json"] = json.dumps({
                    "day": day_key,
                    "rods": 5,
                    "fish": {fish_name: 5},
                    "rewards": {"幸运符": 1},
                }, ensure_ascii=False)

        with state_module.use_identity(identity_id):
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                sent = await fishing_runtime._send_fishing_daily_completion_summary(now)

        self.assertTrue(sent)
        audit_mock.assert_awaited_once()
        text = audit_mock.await_args.args[0]
        self.assertIn("灵溪垂钓日结｜全体", text)
        self.assertIn("10/10竿", text)
        self.assertIn("银须灵鲢x5", text)
        self.assertIn("赤尾火鲤x5", text)
        self.assertEqual(day_key, state_module.get_identity_state(identity_id)["fishing_daily_summary_day"])
        self.assertEqual(day_key, state_module.get_identity_state(other_id)["fishing_daily_summary_day"])

    async def test_daily_completion_includes_public_fishing_identity_with_legacy_disabled(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 8, 10, 0)
        day_key = fishing_runtime.get_day_key(now)
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_TEST",
            "cave_public_fishing_enabled": True,
            "cave_public_fishing_identity_ids": [identity_id],
        }
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            state_module.state["fishing_daily_limit"] = 20
            state_module.state["fishing_daily_day"] = day_key
            state_module.state["fishing_daily_count"] = 20
            state_module.state["fishing_daily_catch_summary_json"] = json.dumps({
                "day": day_key,
                "rods": 20,
                "fish": {"银须灵鲢": 20},
                "rewards": {},
            }, ensure_ascii=False)
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                sent = await fishing_runtime._send_fishing_daily_completion_summary(now)

        self.assertTrue(sent)
        self.assertIn("20/20竿", audit_mock.await_args.args[0])

    async def test_daily_completion_includes_channel_frozen_public_identity(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 15, 8, 10, 0)
        day_key = fishing_runtime.get_day_key(now)
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_TEST",
            "cave_public_fishing_enabled": True,
            "cave_public_fishing_identity_ids": [identity_id],
        }
        state_module._meta_state["channel_send_as_health"] = {
            "status": "closed",
            "restore_identity_ids": [identity_id],
        }
        state_module.update_send_as_profile(identity_id, enabled=False)
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = day_key
            state_module.state["fishing_daily_count"] = 5
            state_module.state["fishing_daily_catch_summary_json"] = json.dumps({
                "day": day_key,
                "rods": 5,
                "fish": {"赤尾火鲤": 5},
                "rewards": {},
            }, ensure_ascii=False)
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                sent = await fishing_runtime._send_fishing_daily_completion_summary(now)

        self.assertTrue(sent)
        self.assertIn("角色1", audit_mock.await_args.args[0])
        self.assertIn("赤尾火鲤x5", audit_mock.await_args.args[0])

    async def test_daily_completion_excludes_manually_disabled_public_identity(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 15, 8, 10, 0)
        day_key = fishing_runtime.get_day_key(now)
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_TEST",
            "cave_public_fishing_enabled": True,
            "cave_public_fishing_identity_ids": [identity_id],
        }
        state_module._meta_state["channel_send_as_health"] = {
            "status": "closed",
            "restore_identity_ids": [],
        }
        state_module.update_send_as_profile(identity_id, enabled=False)
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = day_key
            state_module.state["fishing_daily_count"] = 5
            state_module.state["fishing_daily_catch_summary_json"] = json.dumps({
                "day": day_key,
                "rods": 5,
                "fish": {"赤尾火鲤": 5},
                "rewards": {},
            }, ensure_ascii=False)
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                sent = await fishing_runtime._send_fishing_daily_completion_summary(now)

        self.assertFalse(sent)
        audit_mock.assert_not_awaited()

    async def test_daily_completion_ignores_public_identities_without_rods(self):
        identity_id = self._prepare_identity()
        skipped_id = 301299112
        state_module.ensure_identity_registered(skipped_id)
        now = self._local_ts(2026, 7, 15, 8, 10, 0)
        day_key = fishing_runtime.get_day_key(now)
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_TEST",
            "cave_public_fishing_enabled": True,
            "cave_public_fishing_identity_ids": [identity_id, skipped_id],
        }
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = day_key
            state_module.state["fishing_daily_count"] = 5
            state_module.state["fishing_daily_catch_summary_json"] = json.dumps({
                "day": day_key,
                "rods": 5,
                "fish": {"银须灵鲢": 5},
                "rewards": {},
            }, ensure_ascii=False)
        with state_module.use_identity(skipped_id):
            state_module.state["fishing_enabled"] = False
            state_module.state["fishing_daily_limit"] = 20
            state_module.state["fishing_daily_day"] = day_key
            state_module.state["fishing_daily_count"] = 0
            state_module.state["fishing_last_result"] = "未持有鱼竿，今日跳过"
            state_module.state["fishing_daily_catch_summary_json"] = ""
        with state_module.use_identity(identity_id):
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                sent = await fishing_runtime._send_fishing_daily_completion_summary(now)

        self.assertTrue(sent)
        text = audit_mock.await_args.args[0]
        self.assertIn("5/5竿｜角色1", text)
        self.assertIn("银须灵鲢x5", text)
        self.assertNotIn(state_module.get_identity_display_name(skipped_id), text)
        self.assertEqual(day_key, state_module.get_identity_state(skipped_id)["fishing_daily_summary_day"])

    async def test_daily_limit_terminal_with_stale_zero_count_does_not_block_summary(self):
        identity_id = self._prepare_identity()
        exhausted_id = 301299112
        state_module.ensure_identity_registered(exhausted_id)
        now = self._local_ts(2026, 7, 15, 8, 10, 0)
        day_key = fishing_runtime.get_day_key(now)
        state_module._meta_state["miniapp_auto_config"] = {
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_TEST",
            "cave_public_fishing_enabled": True,
            "cave_public_fishing_identity_ids": [identity_id, exhausted_id],
        }
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = day_key
            state_module.state["fishing_daily_count"] = 5
            state_module.state["fishing_daily_catch_summary_json"] = json.dumps({
                "day": day_key,
                "rods": 5,
                "fish": {"青鳞小鲫": 5},
                "rewards": {},
            }, ensure_ascii=False)
        with state_module.use_identity(exhausted_id):
            state_module.state["fishing_enabled"] = False
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = day_key
            state_module.state["fishing_daily_count"] = 0
            state_module.state["fishing_last_result"] = "MiniApp daily_limit｜fishing_daily_limit_reached"
            state_module.state["fishing_daily_catch_summary_json"] = json.dumps({
                "day": "2026-07-14",
                "rods": 5,
                "fish": {"旧鱼": 5},
                "rewards": {},
            }, ensure_ascii=False)
        with state_module.use_identity(identity_id):
            with (
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                sent = await fishing_runtime._send_fishing_daily_completion_summary(now)

        self.assertTrue(sent)
        text = audit_mock.await_args.args[0]
        self.assertIn("5/5竿｜角色1", text)
        self.assertNotIn("旧鱼", text)
        self.assertEqual(day_key, state_module.get_identity_state(exhausted_id)["fishing_daily_summary_day"])

    def test_daily_limit_without_progress_infers_real_settled_limit(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 8, 15, 0)
        catches = [{"fish": f"灵鱼{i}", "rewards": []} for i in range(20)]
        with state_module.use_identity(identity_id):
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "apply_storage_bag_item_deltas"),
            ):
                fishing_runtime._apply_fishing_miniapp_result({
                    "ok": True,
                    "status": "daily_limit",
                    "settled_count": 20,
                    "data": {"catches": catches},
                }, now=now)

            self.assertEqual(20, state_module.state["fishing_daily_limit"])
            self.assertEqual(20, state_module.state["fishing_daily_count"])

    def test_daily_limit_error_response_closes_daily_counter(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 15, 7, 30, 0)
        with state_module.use_identity(identity_id):
            state_module.state["fishing_daily_limit"] = 5
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "apply_storage_bag_item_deltas"),
            ):
                fishing_runtime._apply_fishing_miniapp_result({
                    "ok": False,
                    "status": "daily_limit",
                    "error": "fishing_daily_limit_reached",
                    "data": {},
                }, now=now)

            self.assertEqual(5, state_module.state["fishing_daily_count"])
            self.assertEqual("", state_module.state["fishing_last_error"])
            self.assertGreater(state_module.state["next_fishing_time"], now)

    async def test_miniapp_failure_backs_off_without_old_followup_chain(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 8, 50, 0)
        flow_result = {
            "ok": False,
            "status": "failed",
            "error": "webview failed",
            "events": [{"step": "launch", "ok": False}],
        }

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_pending_action"] = ".钓鱼状态"
            state_module.state["fishing_phase"] = "checking"
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 3
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock(return_value=flow_result)),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@WalterWA2000，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )
                await fishing_runtime.run_fishing_scheduler(now + 60)

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual("", state_module.state["fishing_pending_action"])
            self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
            self.assertIn("MiniApp failed", state_module.state["fishing_last_error"])
            self.assertEqual(now + fishing_runtime.FISHING_MINIAPP_FAILURE_BACKOFF_SEC, state_module.state["next_fishing_time"])

    async def test_miniapp_mode_blocks_deprecated_lift_followup(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_last_result"] = "MiniApp failed｜webview failed"
            state_module.state["fishing_pending_action"] = ".提竿"
            state_module.state["next_fishing_time"] = now - 1
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime.random, "uniform", return_value=0),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual("", state_module.state["fishing_pending_action"])
            self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
            self.assertEqual("", state_module.state["fishing_last_error"])
            self.assertIn("MiniApp failed", state_module.state["fishing_last_result"])
            self.assertEqual(now + fishing_runtime.FISHING_MINIAPP_FAILURE_BACKOFF_SEC, state_module.state["next_fishing_time"])

    async def test_miniapp_entry_rejects_other_angler(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 7, 6, 7, 55, 0)

        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            with (
                patch.object(fishing_runtime, "run_fishing_miniapp_production_flow", new=AsyncMock()) as flow_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                handled = await fishing_runtime.handle_fishing_miniapp_entry(
                    self._miniapp_event(),
                    "【灵溪垂钓】钓者：@xianxia_01，请点击按钮进入小程序",
                    now,
                    result_msg_id=33001,
                )

        self.assertFalse(handled)
        flow_mock.assert_not_awaited()

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

    async def test_daily_limit_queries_basket_before_opening_when_auto_open_enabled(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_auto_open_fish_enabled"] = True
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

            send_mock.assert_awaited_once_with(".鱼篓", track=False, priority="event_burst", max_retry=0, source_module="灵溪垂钓")
            self.assertEqual("basket", state_module.state["fishing_phase"])
            self.assertEqual(22044, state_module.state["fishing_reply_to_msg_id"])
            self.assertGreater(state_module.state["next_fishing_time"], now)

    async def test_pending_transfer_due_enqueues_storage_bag_gift_batch(self):
        identity_id = self._prepare_identity()
        target_id = self._prepare_identity(10002)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_transfer_target_id"] = target_id
            state_module.state["fishing_transfer_due_at"] = now - 1
            state_module.state["fishing_caught_fish_json"] = '{"银须灵鲢": 2}'
            state_module.state["next_fishing_time"] = now - 1
            with (
                patch.object(fishing_runtime, "start_storage_bag_gift_batch", new=AsyncMock(return_value=(True, "已加入", {}))) as gift_mock,
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock()),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            gift_mock.assert_awaited_once()
            task = gift_mock.await_args.args[0][0]
            self.assertEqual(identity_id, task["source_identity_id"])
            self.assertEqual(target_id, task["target_identity_id"])
            self.assertEqual([{"item_name": "银须灵鲢", "quantity": 2, "method": "gift"}], task["items"])
            self.assertEqual(target_id, gift_mock.await_args.kwargs["target_identity_id"])
            self.assertEqual("", state_module.state["fishing_caught_fish_json"])
            self.assertEqual(0, state_module.state["fishing_transfer_due_at"])
            send_mock.assert_not_awaited()

    async def test_pending_transfer_waits_when_not_due_or_rod_active(self):
        identity_id = self._prepare_identity()
        target_id = self._prepare_identity(10002)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_transfer_target_id"] = target_id
            state_module.state["fishing_transfer_due_at"] = now + 60
            state_module.state["fishing_caught_fish_json"] = '{"银须灵鲢": 2}'
            state_module.state["next_fishing_time"] = now + 3600
            with (
                patch.object(fishing_runtime, "start_storage_bag_gift_batch", new=AsyncMock()) as gift_mock,
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            gift_mock.assert_not_awaited()
            send_mock.assert_not_awaited()

            state_module.state["fishing_transfer_due_at"] = now - 1
            state_module.state["fishing_phase"] = "waiting"
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 30
            with (
                patch.object(fishing_runtime, "start_storage_bag_gift_batch", new=AsyncMock()) as gift_mock,
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            gift_mock.assert_not_awaited()
            send_mock.assert_not_awaited()




    async def test_prep_window_buys_bait_instead_of_starting_old_day_rod(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 6, 26, 23, 40)
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "灵米饵"
            state_module.state["fishing_auto_chum_enabled"] = False
            state_module.state["fishing_auto_buy_bait_enabled"] = True
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            state_module.state["next_fishing_time"] = now - 1
            fake_msg = SimpleNamespace(id=22060, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".买鱼饵 灵米饵 20", track=False, max_retry=0, source_module="灵溪垂钓")
            self.assertEqual("buying", state_module.state["fishing_phase"])
            self.assertNotEqual(".钓鱼 青溪浅滩 灵米饵", send_mock.await_args.args[0])

    async def test_prep_window_holds_until_midnight_when_bait_is_ready(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 6, 26, 23, 45)
        midnight = self._local_ts(2026, 6, 27, 0, 0)
        expected_start = midnight + fishing_runtime._fishing_reset_jitter_sec(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "灵米饵"
            state_module.state["fishing_auto_chum_enabled"] = False
            state_module.state["fishing_auto_buy_bait_enabled"] = True
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 0
            state_module.state["next_fishing_time"] = now - 1
            state_module.set_storage_bag_records({
                str(identity_id): {"items": {"灵米饵": 3}, "sections": {"材料": {"灵米饵": 3}}},
            })
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(expected_start, state_module.state["next_fishing_time"])
            self.assertEqual(".钓鱼 青溪浅滩 灵米饵", state_module.state["fishing_pending_action"])
            self.assertIn("日切待命", state_module.state["fishing_last_result"])

    async def test_prep_window_does_not_pre_chum_when_bait_is_ready(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 6, 26, 23, 45)
        expected_start = self._local_ts(2026, 6, 27, 0, 0)
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "灵米饵"
            state_module.state["fishing_auto_chum_enabled"] = True
            state_module.state["fishing_chum_names"] = '["米糠小窝"]'
            state_module.state["fishing_auto_buy_bait_enabled"] = True
            state_module.state["fishing_daily_day"] = fishing_runtime.get_day_key(now)
            state_module.state["fishing_daily_count"] = 20
            state_module.state["fishing_daily_limit"] = 20
            state_module.state["next_fishing_time"] = now - 1
            state_module.set_storage_bag_records({
                str(identity_id): {"items": {"灵米饵": 3, "凡饵": 2, "灵石": 1000}, "sections": {"材料": {"灵米饵": 3, "凡饵": 2}}},
            })
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertGreaterEqual(state_module.state["next_fishing_time"], expected_start)
            self.assertLessEqual(state_module.state["next_fishing_time"], expected_start + 12)
            self.assertEqual(".钓鱼 青溪浅滩 灵米饵", state_module.state["fishing_pending_action"])
            self.assertIn("日切待命", state_module.state["fishing_last_result"])

    async def test_midnight_resets_daily_counter_and_starts_next_day_rod(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 6, 27, 0, 0, 2)
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "凡饵"
            state_module.state["fishing_auto_chum_enabled"] = False
            state_module.state["fishing_auto_buy_bait_enabled"] = False
            state_module.state["fishing_daily_day"] = "2026-06-26"
            state_module.state["fishing_daily_count"] = 20
            state_module.state["fishing_daily_limit"] = 20
            state_module.state["next_fishing_time"] = now - 1
            state_module.set_storage_bag_records({
                str(identity_id): {"items": {"凡饵": 5}, "sections": {"材料": {"凡饵": 5}}},
            })
            fake_msg = SimpleNamespace(id=22061, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".钓鱼 青溪浅滩 凡饵", track=False, max_retry=0, source_module="灵溪垂钓")
            self.assertEqual(fishing_runtime.get_day_key(now), state_module.state["fishing_daily_day"])
            self.assertEqual(0, state_module.state["fishing_daily_count"])
            self.assertEqual("fishing", state_module.state["fishing_phase"])

    async def test_midnight_rush_skips_chum_for_first_rod(self):
        identity_id = self._prepare_identity()
        now = self._local_ts(2026, 6, 27, 0, 0, 2)
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "灵米饵"
            state_module.state["fishing_auto_chum_enabled"] = True
            state_module.state["fishing_chum_names"] = '["米糠小窝"]'
            state_module.state["fishing_auto_buy_bait_enabled"] = True
            state_module.state["fishing_daily_day"] = "2026-06-26"
            state_module.state["fishing_daily_count"] = 20
            state_module.state["fishing_daily_limit"] = 20
            state_module.state["next_fishing_time"] = now - 1
            state_module.set_storage_bag_records({
                str(identity_id): {"items": {"灵米饵": 3, "凡饵": 2, "灵石": 1000}, "sections": {"材料": {"灵米饵": 3, "凡饵": 2}}},
            })
            fake_msg = SimpleNamespace(id=22063, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_awaited_once_with(".钓鱼 青溪浅滩 灵米饵", track=False, max_retry=0, source_module="灵溪垂钓")
            self.assertEqual("fishing", state_module.state["fishing_phase"])
            self.assertNotEqual(".打窝 米糠小窝", send_mock.await_args.args[0])


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

    async def test_chum_start_waits_when_two_other_flows_are_active(self):
        identity_id = self._prepare_identity()
        active_a = self._prepare_identity(10001)
        active_b = self._prepare_identity(10002)
        now = 1_700_000_000.0
        with state_module.use_identity(active_a):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "buying"
        with state_module.use_identity(active_b):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "chumming"
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "凡饵"
            state_module.state["fishing_auto_chum_enabled"] = True
            state_module.state["fishing_chum_names"] = '["米糠小窝"]'
            state_module.state["fishing_auto_buy_bait_enabled"] = False
            state_module.state["next_fishing_time"] = now - 1
            state_module.set_storage_bag_records({
                str(identity_id): {"items": {"凡饵": 5, "灵石": 100}, "sections": {"材料": {"凡饵": 5, "灵石": 100}}},
            })
            with (
                patch.object(fishing_runtime.random, "uniform", return_value=4),
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                await fishing_runtime.run_fishing_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(now + 4, state_module.state["next_fishing_time"])
            self.assertIn("正在垂钓或准备", state_module.state["fishing_last_error"])







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



    async def test_catch_records_result_before_next_rod_without_opening(self):
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
            self.assertEqual("", state_module.state["fishing_pending_open_fish"])
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



    async def test_duplicate_fishing_start_is_suppressed_inside_watchdog_window(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        command = ".钓鱼 青溪浅滩 灵米饵"
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "灵米饵"
            state_module.state["next_fishing_time"] = now - 1
            first_msg = SimpleNamespace(id=22050, sent_at=now)
            with (
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock(return_value=first_msg)) as first_send_mock,
                patch.object(fishing_runtime, "save_state"),
            ):
                first_sent = await fishing_runtime._send_fishing_command(command, now)

            self.assertTrue(first_sent)
            first_send_mock.assert_awaited_once()
            state_module.state["fishing_phase"] = "idle"
            state_module.state["fishing_reply_to_msg_id"] = 0
            state_module.state["fishing_reply_due_at"] = 0
            state_module.state["next_fishing_time"] = now + 49
            with patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as duplicate_send_mock:
                duplicate_sent = await fishing_runtime._send_fishing_command(command, now + 50)

            self.assertFalse(duplicate_sent)
            duplicate_send_mock.assert_not_awaited()
            self.assertIn("短窗重复指令已抑制", state_module.state["fishing_last_error"])



    async def test_common_open_fish_reply_does_not_queue_valuable_reminder(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "opening"
            state_module.state["fishing_reply_to_msg_id"] = 22042
            state_module.state["fishing_reply_due_at"] = now + 60
            state_module.state["fishing_pending_open_fish"] = '{"银须灵鲢": 1}'
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
            self.assertEqual([], state_module.state["fishing_valuable_drop_reminders"])

    async def test_valuable_open_fish_reply_queues_valuable_reminder(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "opening"
            state_module.state["fishing_reply_to_msg_id"] = 22042
            state_module.state["fishing_reply_due_at"] = now + 60
            state_module.state["fishing_pending_open_fish"] = '{"银须灵鲢": 1}'
            with (
                patch.object(fishing_runtime, "save_state") as save_mock,
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                handled = await fishing_runtime.handle_fishing_reply(
                    VALUABLE_OPEN_FISH_TEXT,
                    now,
                    reply_to=SimpleNamespace(id=22042, raw_text=".开鱼 银须灵鲢"),
                    matched_family="fishing",
                    result_msg_id=22052,
                )

            self.assertTrue(handled)
            send_mock.assert_not_awaited()
            reminders = state_module.state["fishing_valuable_drop_reminders"]
            self.assertEqual(1, len(reminders))
            self.assertIn("大衍诀", reminders[0]["item"])
            self.assertEqual("银须灵鲢", reminders[0]["fish"])
            self.assertEqual(22052, reminders[0]["result_msg_id"])
            self.assertGreaterEqual(save_mock.call_count, 2)

    async def test_open_only_reply_calibrates_pending_count(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_phase"] = "opening"
            state_module.state["fishing_reply_to_msg_id"] = 22042
            state_module.state["fishing_reply_due_at"] = now + 60
            state_module.state["fishing_pending_open_fish"] = '{"赤尾火鲤": 7}'
            with patch.object(fishing_runtime, "save_state"):
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

    async def test_scheduler_sends_fishing_valuable_reminders_three_times(self):
        identity_id = self._prepare_identity()
        now = 1_780_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            state_module.state["fishing_valuable_drop_reminders"] = [{
                "event_id": "fishing-valuable:22052:银须灵鲢:大衍诀",
                "source": "灵溪垂钓伴生机缘",
                "item": "大衍诀",
                "fish": "银须灵鲢",
                "event_at": now,
                "next_index": 0,
                "next_reminder_at": now,
                "done": False,
                "result_msg_id": 22052,
            }]
            with (
                patch.object(fishing_runtime, "save_state") as save_mock,
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
            ):
                await fishing_runtime.run_fishing_scheduler(now)
                await fishing_runtime.run_fishing_scheduler(now + 60)
                await fishing_runtime.run_fishing_scheduler(now + 3 * 3600)
                await fishing_runtime.run_fishing_scheduler(now + 6 * 3600)
            reminders = state_module.state["fishing_valuable_drop_reminders"]

        self.assertEqual(3, audit_mock.await_count)
        self.assertIn("大衍诀", audit_mock.await_args_list[0].args[0])
        self.assertIn("第1/3次，即时", audit_mock.await_args_list[0].args[0])
        self.assertIn("第2/3次，+3h", audit_mock.await_args_list[1].args[0])
        self.assertIn("第3/3次，+6h", audit_mock.await_args_list[2].args[0])
        self.assertEqual("high", audit_mock.await_args_list[0].kwargs["priority"])
        self.assertTrue(reminders[0]["done"])
        self.assertEqual(3, reminders[0]["next_index"])
        self.assertGreaterEqual(save_mock.call_count, 3)

    async def test_scheduler_retries_fishing_valuable_reminder_when_audit_send_fails(self):
        identity_id = self._prepare_identity()
        now = 1_780_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = False
            state_module.state["fishing_valuable_drop_reminders"] = [{
                "event_id": "fishing-valuable:22052:银须灵鲢:大衍诀",
                "source": "灵溪垂钓伴生机缘",
                "item": "大衍诀",
                "fish": "银须灵鲢",
                "event_at": now,
                "next_index": 0,
                "next_reminder_at": now,
                "done": False,
                "result_msg_id": 22052,
            }]
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=False)) as audit_mock,
            ):
                await fishing_runtime.run_fishing_scheduler(now)
            reminders = state_module.state["fishing_valuable_drop_reminders"]

        self.assertEqual(1, audit_mock.await_count)
        self.assertEqual(0, reminders[0]["next_index"])
        self.assertEqual(now + 5 * 60, reminders[0]["next_reminder_at"])
        self.assertFalse(reminders[0]["done"])

    async def test_scheduler_does_not_send_fishing_command_in_same_tick_as_valuable_reminder(self):
        identity_id = self._prepare_identity()
        now = 1_780_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_bait"] = "凡饵"
            state_module.state["fishing_auto_chum_enabled"] = False
            state_module.state["fishing_auto_buy_bait_enabled"] = False
            state_module.state["next_fishing_time"] = now - 1
            state_module.state["fishing_valuable_drop_reminders"] = [{
                "event_id": "fishing-valuable:22052:银须灵鲢:大衍诀",
                "source": "灵溪垂钓伴生机缘",
                "item": "大衍诀",
                "fish": "银须灵鲢",
                "event_at": now,
                "next_index": 0,
                "next_reminder_at": now,
                "done": False,
                "result_msg_id": 22052,
            }]
            with (
                patch.object(fishing_runtime, "save_state"),
                patch.object(fishing_runtime, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(fishing_runtime, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                await fishing_runtime.run_fishing_scheduler(now)

        audit_mock.assert_awaited_once()
        send_mock.assert_not_awaited()

    async def test_in_progress_reply_checks_status_instead_of_starting_new_rod(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_reply_to_msg_id"] = 22027
            state_module.state["fishing_reply_due_at"] = now + 60
            with patch.object(fishing_runtime, "save_state"):
                handled = await fishing_runtime.handle_fishing_reply(
                    "你已有一竿尚未收起。可用 .钓鱼状态 查看，或 .收竿 放弃。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".钓鱼 青溪浅滩 灵米饵"),
                    matched_family="fishing",
                    result_msg_id=22034,
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual("", state_module.state["fishing_pending_action"])
            self.assertEqual(0, state_module.state["fishing_reply_to_msg_id"])
            self.assertIn("一竿尚未收起", state_module.state["fishing_last_result"])

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

    async def test_daily_limit_reply_stops_when_auto_open_disabled(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_auto_open_fish_enabled"] = False
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
            self.assertEqual("", state_module.state["fishing_pending_action"])
            self.assertGreater(state_module.state["next_fishing_time"], now + 3600)

    async def test_daily_limit_reply_schedules_basket_when_auto_open_enabled(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["fishing_enabled"] = True
            state_module.state["fishing_auto_open_fish_enabled"] = True
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
            with patch.object(fishing_runtime, "save_state"):
                handled = await fishing_runtime.handle_fishing_reply(
                    FISHING_START_TEXT,
                    now,
                    reply_to=None,
                    matched_family=None,
                    result_msg_id=22030,
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["fishing_phase"])
            self.assertEqual("", state_module.state["fishing_pending_action"])
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

    async def test_known_chum_shortage_accepts_display_bait_name(self):
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
                    "打窝失败，资源不足：凡饵x2。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".打窝 米糠小窝"),
                    matched_family="fishing",
                    result_msg_id=22031,
                )

            self.assertTrue(handled)
            self.assertEqual("凡饵", state_module.state["fishing_forced_buy_bait"])
            self.assertEqual(8, state_module.state["fishing_forced_buy_count"])
            self.assertEqual("", state_module.state["fishing_last_error"])
            self.assertLess(state_module.state["next_fishing_time"], now + 60)

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


    def test_runtime_send_gap_whitelist_is_limited_to_fast_modules(self):
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
        self.assertTrue(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_EVENT_BURST,
                ".鱼篓",
                intent={"source_module": "灵溪垂钓"},
            )
        )
        self.assertTrue(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_EVENT_BURST,
                ".上架 灵石*1 换 妖丹*3",
                intent={"source_module": "储物袋"},
            )
        )
        self.assertTrue(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_EVENT_BURST,
                ".赠送 木髓*1",
                intent={"source_module": "储物袋"},
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
        self.assertFalse(
            runtime_module._send_gap_whitelist_allows(
                runtime_module.SEND_PRIORITY_EVENT_BURST,
                ".上架 灵石*1 换 妖丹*3",
                intent={"source_module": "自动副本"},
            )
        )

    def test_runtime_module_gap_enforces_fast_module_minimum(self):
        runtime_module._MODULE_LAST_SEND_AT.clear()
        runtime_module._MODULE_LAST_SEND_AT["灵溪垂钓"] = 100.0
        runtime_module._MODULE_LAST_SEND_AT["储物袋"] = 100.0

        self.assertEqual(
            102.0,
            runtime_module._module_send_gap_ready_at({"source_module": "灵溪垂钓"}, now_mono=100.5),
        )
        self.assertEqual(
            105.0,
            runtime_module._module_send_gap_ready_at({"source_module": "储物袋"}, now_mono=100.5),
        )
        self.assertEqual(
            0.0,
            runtime_module._module_send_gap_ready_at({"source_module": "自动副本"}, now_mono=100.5),
        )


if __name__ == "__main__":
    unittest.main()
