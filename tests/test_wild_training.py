import atexit
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CREATED_ENV = False

if not ENV_PATH.exists():
    ENV_PATH.write_text(
        "\n".join(
            [
                "API_ID=12345",
                "API_HASH=00000000000000000000000000000000",
                "TG_PROXY_TYPE=",
                "TG_PROXY_HOST=127.0.0.1:7890",
                "LOG_GROUP_ID=0",
                "LOG_SEND_MODE=account",
                "ADMIN_ID=1",
                "CHAOGU_UI_HOST=127.0.0.1",
                "CHAOGU_UI_PORT=3030",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    CREATED_ENV = True

if CREATED_ENV:
    atexit.register(lambda: ENV_PATH.exists() and ENV_PATH.unlink())

sys.path.insert(0, str(PROJECT_ROOT))

from model import config
from model import runtime
from model import state as state_module
from model.features import passive_inbox, wild_training


class WildTrainingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _prepare_identity(self):
        send_as_id = 991201
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="wild")
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_enabled"] = True
            identity_state["next_wild_training_time"] = 0
            identity_state["wild_training_reply_to_msg_id"] = 101
            identity_state["wild_training_reply_due_at"] = 1_700_000_600.0
            identity_state["wild_training_retry_count"] = 0
            identity_state["wild_training_last_msg_id"] = 101
            identity_state["wild_training_last_result"] = "已发送：谨慎"
            identity_state["wild_training_last_error"] = ""
        return send_as_id

    def _log_ts(self, ts):
        return datetime.fromtimestamp(float(ts), config.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")

    def _write_message_log(self, log_dir, entries, now):
        day = datetime.fromtimestamp(float(now), config.TZ_LOCAL).date().isoformat()
        log_path = Path(log_dir) / f"{day}.log"
        with log_path.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return log_path

    async def test_start_notice_keeps_pending_for_final_edit(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "【野外历练】\n@myios7 选择【谨慎】策略，正向荒野深处行去..."
        reply_to = SimpleNamespace(raw_text=f"{config.CMD_WILD_TRAINING} 谨慎", id=101)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training, "console_log"):
            handled = await wild_training.handle_wild_training_reply(
                text,
                now,
                reply_to,
                matched_family="wild_training",
                current_msg_id=201,
            )

        self.assertTrue(handled)
        self.assertEqual(201, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(1_700_000_600.0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertEqual(201, state_module.state["wild_training_last_msg_id"])
        self.assertEqual("已出发：谨慎", state_module.state["wild_training_last_result"])

    async def test_final_edit_clears_pending_and_records_rewards(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_010.0
        text = (
            "【野外历练 · 灵机暗藏】\n"
            "@myios7 在山涧残阵旁避开妖兽踪迹，采得一份机缘。\n"
            "获得修为 +392，获得 【清灵草】x1。"
        )
        reply_to = SimpleNamespace(raw_text=f"{config.CMD_WILD_TRAINING} 谨慎", id=101)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC), \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()):
            handled = await wild_training.handle_wild_training_reply(
                text,
                now,
                reply_to,
                matched_family="wild_training",
                current_msg_id=201,
            )

        self.assertTrue(handled)
        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual(201, state_module.state["wild_training_last_msg_id"])
        self.assertIn("修为+392", state_module.state["wild_training_last_result"])
        self.assertIn("清灵草x1", state_module.state["wild_training_last_result"])
        self.assertEqual(now, state_module.state["wild_training_last_result_at"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertEqual(now + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])

    async def test_tianxing_final_edit_reports_high_priority_before_normal_audit(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_010.0
        text = (
            "【野外历练 · 灵机暗藏】\n"
            "【推命命中】你顺应命盘，天机值 +1。\n"
            "命盘【贪狼】照命，主偏财夺势。\n"
            "@myios7 采得一份机缘，获得修为 +392，获得 【清灵草】x1。"
        )
        reply_to = SimpleNamespace(raw_text=f"{config.CMD_WILD_TRAINING} 深入", id=101)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC), \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()) as audit_mock:
            handled = await wild_training.handle_wild_training_reply(
                text,
                now,
                reply_to,
                matched_family="wild_training",
                current_msg_id=201,
            )

        self.assertTrue(handled)
        self.assertEqual(2, audit_mock.await_count)
        first_args, first_kwargs = audit_mock.await_args_list[0]
        second_args, second_kwargs = audit_mock.await_args_list[1]
        self.assertIn("🌌 天星探索结果｜野外历练", first_args[0])
        self.assertEqual("high", first_kwargs["priority"])
        self.assertEqual("identity", first_kwargs["scope"])
        self.assertIn("🏞️ 野外历练结果", second_args[0])
        self.assertNotIn("priority", second_kwargs)

    async def test_tianxing_change_fate_escape_result_records_reward_and_no_loss(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_010.0
        text = (
            "【野外历练 · 改命脱险】\n"
            "命盘【贪狼】照命，主偏财夺势。\n"
            "【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30\n"
            "【改命待发】此道改命尚可维持 22小时54分钟\n"
            "【天星偏转】 趋吉偏转，材料显化上扬\n"
            "@WalterWA2000 遭遇 荒古鳞兽，本已要负伤折返，司命盘却替你撬开了一线退路。\n"
            "【改命回天】你强行拨正命轨，硬从凶数中抢回一线生机。\n"
            "你虽未能尽取机缘，却仍带回了 【灵石】x571，且 本次未损修为。"
        )
        reply_to = SimpleNamespace(raw_text=f"{config.CMD_WILD_TRAINING} 深入", id=101)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC), \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()) as audit_mock:
            handled = await wild_training.handle_wild_training_reply(
                text,
                now,
                reply_to,
                matched_family="wild_training",
                current_msg_id=201,
            )

        self.assertTrue(handled)
        self.assertIn("天机+1", state_module.state["wild_training_last_result"])
        self.assertIn("贡献+30", state_module.state["wild_training_last_result"])
        self.assertIn("灵石x571", state_module.state["wild_training_last_result"])
        self.assertIn("未损修为", state_module.state["wild_training_last_result"])
        first_args, first_kwargs = audit_mock.await_args_list[0]
        self.assertIn("🌌 天星探索结果｜野外历练", first_args[0])
        self.assertIn("灵石x571", first_args[0])
        self.assertEqual("high", first_kwargs["priority"])

    async def test_recent_completed_result_with_stale_due_timer_is_rescheduled_not_sent(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_100.0
        result_at = now - 30
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["wild_training_last_msg_id"] = 201
            identity_state["wild_training_last_result"] = "修为-1264"
            identity_state["wild_training_last_result_at"] = result_at
            identity_state["next_wild_training_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC), \
             patch.object(wild_training, "console_log") as console_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock:
            await wild_training.run_wild_training_scheduler(now)

        send_mock.assert_not_awaited()
        console_mock.assert_called_once()
        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertEqual(result_at + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])
        self.assertIn("计时器异常", state_module.state["wild_training_last_error"])

    async def test_started_timeout_schedules_next_without_retry(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_reply_due_at"] = now - 1
            identity_state["wild_training_last_result"] = "已出发：谨慎"

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC), \
             patch.object(wild_training, "console_log") as console_mock, \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()) as audit_mock:
            await wild_training.run_wild_training_scheduler(now)

        audit_mock.assert_not_awaited()
        console_mock.assert_called_once()
        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertEqual(now + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])
        self.assertIn("结果编辑未留存", state_module.state["wild_training_last_result"])
        self.assertIn("已按正常周期恢复", state_module.state["wild_training_last_result"])
        self.assertEqual("", state_module.state["wild_training_last_error"])

    async def test_unanswered_command_still_retries_once(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_reply_due_at"] = now - 1
            identity_state["wild_training_last_result"] = "已发送：谨慎"

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_RETRY_MIN_SEC), \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()):
            await wild_training.run_wild_training_scheduler(now)

        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(1, state_module.state["wild_training_retry_count"])
        self.assertEqual(now + wild_training.WILD_TRAINING_RETRY_MIN_SEC, state_module.state["next_wild_training_time"])
        self.assertIn("准备补发一次", state_module.state["wild_training_last_error"])

    async def test_retry_due_during_dungeon_quiet_defers_without_consuming_retry(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        quiet_until = now + 120
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 1
            identity_state["next_wild_training_time"] = now - 1
        state_module.state["dungeon_quiet_until"] = quiet_until
        state_module.state["dungeon_quiet_reason"] = "虚天殿静场令"

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training.random, "uniform", return_value=20), \
             patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        send_mock.assert_not_awaited()
        self.assertEqual(1, state_module.state["wild_training_retry_count"])
        self.assertEqual(quiet_until + 20, state_module.state["next_wild_training_time"])
        self.assertIn("补发撞到虚天殿静场令", state_module.state["wild_training_last_error"])
        self.assertNotIn("进入下一轮", state_module.state["wild_training_last_error"])

    async def test_send_blocked_by_dungeon_quiet_after_queue_defers_without_retry(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        quiet_until = now + 120
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = now - 1

        async def fake_send(*_args, **_kwargs):
            state_module.state["dungeon_quiet_until"] = quiet_until
            state_module.state["dungeon_quiet_reason"] = "坠魔谷静场令"
            return None

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training.random, "uniform", return_value=20), \
             patch.object(wild_training, "send_game_command", new=fake_send), \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertEqual(quiet_until + 20, state_module.state["next_wild_training_time"])
        self.assertIn("发送撞到坠魔谷静场令", state_module.state["wild_training_last_error"])
        self.assertNotIn("准备补发", state_module.state["wild_training_last_error"])

    async def test_malformed_next_time_blocks_without_save_or_retry(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = "冷却中"

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state") as save_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()) as audit_mock:
            await wild_training.run_wild_training_scheduler(now)

        send_mock.assert_not_awaited()
        audit_mock.assert_not_awaited()
        save_mock.assert_not_called()
        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertEqual("冷却中", state_module.state["next_wild_training_time"])

    async def test_numeric_next_time_still_blocks_until_due_then_sends(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = str(now + 30)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state") as save_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock:
            await wild_training.run_wild_training_scheduler(now)

        send_mock.assert_not_awaited()
        save_mock.assert_not_called()

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["next_wild_training_time"] = str(now)

        sent_msg = SimpleNamespace(id=202, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state") as save_mock, \
             patch.object(wild_training, "console_log"), \
             patch.object(wild_training, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock:
            await wild_training.run_wild_training_scheduler(now)

        send_mock.assert_awaited_once()
        save_mock.assert_called_once()
        self.assertEqual(202, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(now + wild_training.WILD_TRAINING_REPLY_TIMEOUT_SEC, state_module.state["wild_training_reply_due_at"])

    async def test_scheduler_does_not_directly_insert_tianxing_set_star_without_timeline(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = now - 1
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴", "贪狼"],
                "fixed_star": "",
            }
            identity_state["tianxing_auto_config"] = {
                "auto_set_star_enabled": True,
                "strategy_dry_run_enabled": False,
                "star_priority": ["太阴", "贪狼"],
            }
        sent_msg = SimpleNamespace(id=202, sent_at=now)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock, \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_RETRY_MIN_SEC), \
             patch.object(wild_training, "console_log"), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_not_awaited()
        send_mock.assert_awaited_once_with(f"{config.CMD_WILD_TRAINING} 谨慎", track=False)
        self.assertEqual(202, state_module.state["wild_training_reply_to_msg_id"])

    async def test_scheduler_requests_tianxing_timeline_before_wild_training_when_enabled(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = now - 1
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_change": "",
                "current_prediction": "",
                "tianji_value": 9,
            }
            identity_state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "strategy_dry_run_enabled": False,
                "min_tianji_for_change": 6,
            }

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True})) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_RETRY_MIN_SEC), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_awaited_once()
        self.assertEqual("探索", timeline_mock.await_args.kwargs["windows"][0]["route"])
        self.assertTrue(timeline_mock.await_args.kwargs["windows"][0]["require_change_fate"])
        send_mock.assert_not_awaited()
        self.assertEqual("天星时间线：sent_waiting_ack", state_module.state["wild_training_last_result"])

    async def test_scheduler_falls_back_to_cautious_when_tianxing_has_no_change_fate_to_apply(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_strategy"] = "深入"
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = now - 1
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_change": "",
                "current_change_until": 0,
                "current_prediction": "",
                "current_prediction_until": 0,
                "tianji_value": 2,
            }
            identity_state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "strategy_dry_run_enabled": False,
                "min_tianji_for_change": 6,
            }
        sent_msg = SimpleNamespace(id=202, sent_at=now)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "idle", "changed": True})) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock, \
             patch.object(wild_training, "console_log"), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_awaited_once()
        self.assertTrue(timeline_mock.await_args.kwargs["windows"][0]["require_change_fate"])
        send_mock.assert_awaited_once_with(f"{config.CMD_WILD_TRAINING} 谨慎", track=False)
        self.assertEqual("已发送：谨慎", state_module.state["wild_training_last_result"])
        self.assertEqual(202, state_module.state["wild_training_reply_to_msg_id"])

    async def test_scheduler_falls_back_to_cautious_when_tianxing_change_fate_conflicts(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_strategy"] = "深入"
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = now - 1
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "闭关",
                "current_change_until": now + 3600,
                "current_prediction": "",
                "current_prediction_until": 0,
                "tianji_value": 42,
            }
            identity_state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "strategy_dry_run_enabled": False,
                "min_tianji_for_change": 6,
            }
        sent_msg = SimpleNamespace(id=203, sent_at=now)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "change_fate_conflict", "changed": False})) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock, \
             patch.object(wild_training, "console_log"), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_awaited_once()
        self.assertTrue(timeline_mock.await_args.kwargs["windows"][0]["require_change_fate"])
        send_mock.assert_awaited_once_with(f"{config.CMD_WILD_TRAINING} 谨慎", track=False)
        self.assertEqual("已发送：谨慎", state_module.state["wild_training_last_result"])
        self.assertEqual(203, state_module.state["wild_training_reply_to_msg_id"])

    async def test_scheduler_prepares_tianxing_timeline_inside_future_wild_training_lead_window(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        due_at = now + 240
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = due_at
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_change": "",
                "current_prediction": "",
                "tianji_value": 9,
            }
            identity_state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "strategy_dry_run_enabled": False,
                "min_tianji_for_change": 6,
                "route_prepare_lead_sec": 300,
            }

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True})) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_awaited_once()
        self.assertEqual("探索", timeline_mock.await_args.kwargs["windows"][0]["route"])
        self.assertTrue(timeline_mock.await_args.kwargs["windows"][0]["require_change_fate"])
        self.assertEqual(now, timeline_mock.await_args.kwargs["windows"][0]["start_at"])
        self.assertGreaterEqual(timeline_mock.await_args.kwargs["windows"][0]["end_at"], due_at)
        send_mock.assert_not_awaited()
        self.assertEqual(due_at, state_module.state["next_wild_training_time"])
        self.assertEqual("天星时间线：sent_waiting_ack", state_module.state["wild_training_last_result"])

    async def test_scheduler_does_not_insert_tianxing_predict_before_wild_training_without_timeline(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = now - 1
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "",
                "current_prediction": "",
                "tianji_value": 2,
            }
            identity_state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "strategy_dry_run_enabled": False,
                "min_tianji_for_change": 6,
            }
        sent_msg = SimpleNamespace(id=202, sent_at=now)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock, \
             patch.object(wild_training, "console_log"), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_not_awaited()
        send_mock.assert_awaited_once_with(f"{config.CMD_WILD_TRAINING} 谨慎", track=False)
        self.assertEqual(202, state_module.state["wild_training_reply_to_msg_id"])

    async def test_scheduler_sends_wild_training_directly_when_tianxing_timeline_released(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = now - 1
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "探索",
                "current_change_until": now + 3600,
                "current_prediction": "探索",
                "current_prediction_until": now + 1800,
                "tianji_value": 9,
            }
            identity_state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "strategy_dry_run_enabled": False,
            }
            identity_state["tianxing_timeline_state"] = {
                "released_routes": {
                    "探索": {"released_at": now - 5, "plan_id": "test", "reason": "confirmed"},
                },
            }

        sent_msg = SimpleNamespace(id=202, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock, \
             patch.object(wild_training, "console_log"), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_not_awaited()
        send_mock.assert_awaited_once_with(f"{config.CMD_WILD_TRAINING} 深入", track=False)
        self.assertEqual(202, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(now + wild_training.WILD_TRAINING_REPLY_TIMEOUT_SEC, state_module.state["wild_training_reply_due_at"])

    async def test_scheduler_uses_cautious_without_tianxing_change_fate(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_strategy"] = "深入"
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = now - 1
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "",
                "current_change_until": 0,
                "current_prediction": "探索",
                "current_prediction_until": now + 1800,
                "tianji_value": 9,
            }
            identity_state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "strategy_dry_run_enabled": False,
            }
            identity_state["tianxing_timeline_state"] = {
                "released_routes": {
                    "探索": {"released_at": now - 5, "plan_id": "test", "reason": "prediction confirmed", "basis": "prediction"},
                },
            }

        sent_msg = SimpleNamespace(id=202, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock, \
             patch.object(wild_training, "console_log"), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_not_awaited()
        send_mock.assert_awaited_once_with(f"{config.CMD_WILD_TRAINING} 谨慎", track=False)
        self.assertEqual("已发送：谨慎", state_module.state["wild_training_last_result"])

    async def test_scheduler_blocks_wild_training_when_other_prediction_active(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = now - 1
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_prediction": "闭关",
                "current_prediction_until": now + 1800,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 9,
            }

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_RETRY_MIN_SEC), \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_not_awaited()
        send_mock.assert_not_awaited()
        self.assertEqual(now + wild_training.WILD_TRAINING_RETRY_MIN_SEC, state_module.state["next_wild_training_time"])
        self.assertIn("避免逆命", state_module.state["wild_training_last_error"])

    async def test_scheduler_keeps_future_wild_training_due_when_other_prediction_active(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        due_at = now + 240
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = due_at
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_prediction": "炼制",
                "current_prediction_until": now + 8 * 3600,
                "current_change": "探索",
                "current_change_until": now + 3600,
                "tianji_value": 9,
            }
            identity_state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "strategy_dry_run_enabled": False,
                "route_prepare_lead_sec": 300,
            }

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_not_awaited()
        send_mock.assert_not_awaited()
        self.assertEqual(
            min(due_at, now + wild_training.WILD_TRAINING_RETRY_MAX_SEC),
            state_module.state["next_wild_training_time"],
        )
        self.assertIn("避免逆命", state_module.state["wild_training_last_error"])

    async def test_scheduler_keeps_near_future_wild_training_due_when_other_prediction_active(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        due_at = now + 120
        with state_module.use_identity(send_as_id) as identity_state:
            state_module.update_send_as_profile(send_as_id, sect_name="天星宗")
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["next_wild_training_time"] = due_at
            identity_state["tianxing_enabled"] = True
            identity_state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_prediction": "炼制",
                "current_prediction_until": now + 8 * 3600,
                "current_change": "探索",
                "current_change_until": now + 3600,
                "tianji_value": 9,
            }
            identity_state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "strategy_dry_run_enabled": False,
                "route_prepare_lead_sec": 300,
            }

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock, \
             patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)

        timeline_mock.assert_not_awaited()
        send_mock.assert_not_awaited()
        self.assertEqual(due_at, state_module.state["next_wild_training_time"])
        self.assertIn("避免逆命", state_module.state["wild_training_last_error"])

    async def test_started_timeout_recovers_final_edit_from_message_log(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        result_ts = now - 5
        entries = [
            {
                "ts": self._log_ts(result_ts),
                "event_type": "edit",
                "message_id": 201,
                "reply_to_msg_id": 101,
                "text": "【野外历练 · 灵机暗藏】\n@wild 获得修为 +392，获得 【清灵草】x1。",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(tmpdir, entries, now)
            with state_module.use_identity(send_as_id) as identity_state:
                identity_state["wild_training_reply_to_msg_id"] = 201
                identity_state["wild_training_reply_due_at"] = now - 1
                identity_state["wild_training_last_result"] = "已出发：谨慎"

            with state_module.use_identity(send_as_id), \
                 patch.object(wild_training, "MESSAGES_DIR", tmpdir), \
                 patch.object(wild_training, "save_state"), \
                 patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC), \
                 patch.object(wild_training, "send_audit_log", new=AsyncMock()) as audit_mock:
                await wild_training.run_wild_training_scheduler(now)

        audit_mock.assert_awaited_once()
        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertIn("修为+392", state_module.state["wild_training_last_result"])
        self.assertIn("清灵草x1", state_module.state["wild_training_last_result"])
        self.assertEqual(result_ts + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])
        self.assertEqual("", state_module.state["wild_training_last_error"])

    async def test_command_timeout_recovers_start_and_result_from_message_log(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        start_ts = now - 120
        result_ts = start_ts + 4
        entries = [
            {
                "ts": self._log_ts(start_ts),
                "event_type": "message",
                "message_id": 201,
                "reply_to_msg_id": 101,
                "text": "【野外历练】\n@wild 选择【谨慎】策略，正向荒野深处行去...",
            },
            {
                "ts": self._log_ts(result_ts),
                "event_type": "edit",
                "message_id": 201,
                "reply_to_msg_id": 101,
                "text": "【野外历练 · 灵机暗藏】\n@wild 获得修为 +392，获得 【清灵草】x1。",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(tmpdir, entries, now)
            with state_module.use_identity(send_as_id) as identity_state:
                identity_state["wild_training_reply_to_msg_id"] = 101
                identity_state["wild_training_reply_due_at"] = now - 1
                identity_state["wild_training_last_result"] = "已发送：谨慎"

            with state_module.use_identity(send_as_id), \
                 patch.object(wild_training, "MESSAGES_DIR", tmpdir), \
                 patch.object(wild_training, "save_state"), \
                 patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC), \
                 patch.object(wild_training, "send_audit_log", new=AsyncMock()) as audit_mock:
                await wild_training.run_wild_training_scheduler(now)

        audit_mock.assert_awaited_once()
        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertIn("修为+392", state_module.state["wild_training_last_result"])
        self.assertIn("清灵草x1", state_module.state["wild_training_last_result"])
        self.assertEqual(result_ts + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])
        self.assertEqual("", state_module.state["wild_training_last_error"])

    async def test_passive_start_notice_does_not_clear_pending(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "【野外历练】\n@myios7 选择【谨慎】策略，正向荒野深处行去..."

        with state_module.use_identity(send_as_id):
            handled = passive_inbox._apply_wild_training_passive(text, now, "wild_training")

        self.assertTrue(handled)
        self.assertEqual(101, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(1_700_000_600.0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual("已出发：谨慎", state_module.state["wild_training_last_result"])

    async def test_passive_result_without_identity_context_does_not_mutate_current_identity(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_010.0
        text = (
            "【野外历练 · 灵机暗藏】\n"
            "@myios7 在山涧残阵旁避开妖兽踪迹，采得一份机缘。\n"
            "获得修为 +392，获得 【清灵草】x1。"
        )

        with state_module.use_identity(send_as_id), \
             patch.object(passive_inbox, "_save_passive_stats"):
            handled = await passive_inbox.handle_passive_module_card(text, now=now, reply_context={})

        self.assertFalse(handled)
        self.assertEqual(101, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(1_700_000_600.0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual("已发送：谨慎", state_module.state["wild_training_last_result"])

    async def test_passive_result_with_unique_text_tag_routes_without_reply_context(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_010.0
        text = (
            "【野外历练 · 灵机暗藏】\n"
            "@wild 在山涧残阵旁避开妖兽踪迹，采得一份机缘。\n"
            "获得修为 +392，获得 【清灵草】x1。"
        )
        event = SimpleNamespace(chat_id=-1001680975844, id=202)
        before_snapshot = passive_inbox.get_passive_inbox_snapshot()
        before_changed = before_snapshot.get("changed", 0)
        before_wild = before_snapshot.get("modules", {}).get("wild_training", 0)

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={},
                event=event,
                event_type="edit",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["wild_training_reply_due_at"])
            self.assertIn("修为+392", state_module.state["wild_training_last_result"])
            self.assertIn("清灵草x1", state_module.state["wild_training_last_result"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(before_changed + 1, snapshot["changed"])
        self.assertEqual(before_wild + 1, snapshot["modules"]["wild_training"])
        self.assertEqual("edit:passive_tag", snapshot["recent"][-1]["route_source"])

    async def test_passive_short_tag_does_not_match_longer_mention(self):
        send_as_id = self._prepare_identity()
        state_module.update_send_as_profile(send_as_id, username="q")
        now = 1_700_000_010.0
        text = (
            "【野外历练 · 灵机暗藏】\n"
            "@qaq_noaobot 在山涧残阵旁避开妖兽踪迹，采得一份机缘。\n"
            "获得修为 +392，获得 【清灵草】x1。"
        )
        event = SimpleNamespace(chat_id=-1001680975844, id=203)
        before_count = passive_inbox.get_passive_inbox_snapshot().get("skip_reasons", {}).get("external_identity_no_match", 0)

        with state_module.use_identity(send_as_id), patch.object(passive_inbox, "_save_passive_stats"):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={},
                event=event,
                event_type="edit",
            )

        self.assertFalse(handled)
        self.assertEqual(101, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(1_700_000_600.0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual("已发送：谨慎", state_module.state["wild_training_last_result"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(before_count + 1, snapshot["skip_reasons"]["external_identity_no_match"])

    async def test_pending_message_id_routes_after_restart(self):
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            family = runtime._get_special_tracked_message_family(state_module.state, 101)

        self.assertEqual("wild_training", family)

    async def test_start_notice_message_id_routes_after_restart(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "【野外历练】\n@myios7 选择【谨慎】策略，正向荒野深处行去..."
        reply_to = SimpleNamespace(raw_text=f"{config.CMD_WILD_TRAINING} 谨慎", id=101)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training, "console_log"):
            handled = await wild_training.handle_wild_training_reply(
                text,
                now,
                reply_to,
                matched_family="wild_training",
                current_msg_id=201,
            )
            family = runtime._get_special_tracked_message_family(state_module.state, 201)

        self.assertTrue(handled)
        self.assertEqual("wild_training", family)


if __name__ == "__main__":
    unittest.main()
