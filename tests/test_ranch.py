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
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import ranch


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class RanchTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def _write_message_log(self, log_dir, entries, now):
        day = datetime.fromtimestamp(float(now), ranch.TZ_LOCAL).date().isoformat()
        log_path = Path(log_dir) / f"{day}.log"
        with log_path.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return log_path

    def _log_ts(self, ts):
        return datetime.fromtimestamp(float(ts), ranch.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")

    async def test_success_waits_for_return_broadcast_before_next_send(self):
        send_as_id = 8659059191
        now = 1000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="WalterWA2000")

        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            with (
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                handled = await ranch.handle_ranch_reply(
                    "【万兽奔腾】\n你打开万兽谷传送阵，灵兽四散放养。",
                    now,
                    SimpleNamespace(id=123, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertTrue(state_module.state["ranch_return_pending"])

            state_module.state["next_ranch_time"] = now
            with (
                patch.object(ranch, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                await ranch.run_ranch_scheduler(now + 1)
            send_mock.assert_not_awaited()

        with (
            patch.object(ranch, "send_audit_log", new=AsyncMock()),
            patch.object(ranch, "save_state"),
        ):
            handled_return = await ranch.handle_ranch_return_broadcast(
                "【灵兽归来】\n道友 @WalterWA2000 你放养的灵兽已自行归来。",
                now + 2,
                SimpleNamespace(id=456),
            )

        self.assertTrue(handled_return)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["ranch_return_pending"])
            self.assertEqual(456, state_module.state["ranch_return_seen_msg_id"])

    async def test_stale_return_wait_reprobes_instead_of_waiting_forever(self):
        send_as_id = 3711993781
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode3")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["ranch_return_pending"] = True
            state_module.state["ranch_return_wait_since"] = now - ranch.RANCH_RETURN_MAX_WAIT_SEC - 1
            state_module.state["next_ranch_time"] = now - 3600

            with (
                patch.object(ranch.random, "uniform", return_value=ranch.RANCH_RETURN_STALE_REPROBE_MIN_SEC),
                patch.object(ranch, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                await ranch.run_ranch_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertFalse(state_module.state["ranch_return_pending"])
            self.assertEqual(now + ranch.RANCH_RETURN_STALE_REPROBE_MIN_SEC, state_module.state["next_ranch_time"])
            self.assertIn("归来广播等待超时", state_module.state["ranch_last_error"])

    async def test_no_idle_after_reply_timeout_waits_for_possible_silent_success(self):
        send_as_id = 3711993781
        now = 1_700_000_000.0
        first_sent_at = now - ranch.RANCH_REPLY_TIMEOUT_SEC
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode3")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["ranch_reply_to_msg_id"] = 111
            state_module.state["ranch_reply_due_at"] = now

            with (
                patch.object(ranch.random, "uniform", return_value=ranch.RANCH_RETRY_MIN_SEC),
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                await ranch.run_ranch_scheduler(now)

            self.assertEqual(1, state_module.state["ranch_retry_count"])
            self.assertEqual(first_sent_at, state_module.state["ranch_return_wait_since"])
            self.assertEqual(now + ranch.RANCH_RETRY_MIN_SEC, state_module.state["next_ranch_time"])

            with (
                patch.object(ranch.random, "uniform", return_value=ranch.RANCH_CYCLE_MIN_SEC),
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                handled = await ranch.handle_ranch_reply(
                    "你当前没有处于【休息中】的灵兽可供放养。",
                    now + ranch.RANCH_RETRY_MIN_SEC + 1,
                    SimpleNamespace(id=222, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertTrue(state_module.state["ranch_return_pending"])
            self.assertEqual(first_sent_at, state_module.state["ranch_return_wait_since"])
            self.assertEqual(first_sent_at + ranch.RANCH_CYCLE_MIN_SEC, state_module.state["next_ranch_time"])
            self.assertEqual(0, state_module.state["ranch_retry_count"])
            self.assertIn("可能已生效", state_module.state["ranch_last_result"])

    async def test_scheduler_recovers_no_idle_retry_after_restart_from_message_log(self):
        send_as_id = 3711993781
        now = 1_780_208_953.0
        first_sent_at = now - 1800
        retry_sent_at = first_sent_at + ranch.RANCH_REPLY_TIMEOUT_SEC + ranch.RANCH_RETRY_MIN_SEC
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode3")

        entries = [
            {
                "ts": self._log_ts(first_sent_at),
                "event_type": "sent",
                "message_id": 9628390,
                "sender_id": send_as_id,
                "text": ".一键放养",
                "family": "ranch",
                "source_module": "放养",
            },
            {
                "ts": self._log_ts(retry_sent_at),
                "event_type": "sent",
                "message_id": 9628843,
                "sender_id": send_as_id,
                "text": ".一键放养",
                "family": "ranch",
                "source_module": "放养",
            },
            {
                "ts": self._log_ts(retry_sent_at + 2),
                "event_type": "message",
                "message_id": 9628844,
                "sender_id": 8757550896,
                "reply_to_msg_id": 9628843,
                "text": "你当前没有处于【休息中】的灵兽可供放养。",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(tmpdir, entries, now)
            with state_module.use_identity(send_as_id):
                state_module.state["ranch_enabled"] = True
                state_module.state["ranch_last_result"] = "无休息中灵兽"
                state_module.state["next_ranch_time"] = now + 3600

                with (
                    patch.object(ranch, "MESSAGES_DIR", tmpdir),
                    patch.object(ranch.random, "uniform", return_value=ranch.RANCH_CYCLE_MIN_SEC),
                    patch.object(ranch, "send_game_command", new=AsyncMock()) as send_mock,
                    patch.object(ranch, "send_audit_log", new=AsyncMock()) as audit_mock,
                    patch.object(ranch, "save_state"),
                ):
                    await ranch.run_ranch_scheduler(now)

                send_mock.assert_not_awaited()
                audit_mock.assert_awaited_once()
                self.assertTrue(state_module.state["ranch_return_pending"])
                self.assertEqual(first_sent_at, state_module.state["ranch_return_wait_since"])
                self.assertEqual(first_sent_at + ranch.RANCH_CYCLE_MIN_SEC, state_module.state["next_ranch_time"])
                self.assertIn("历史补偿", state_module.state["ranch_last_result"])

    async def test_no_idle_after_stale_reprobe_keeps_waiting_for_return(self):
        send_as_id = 3711993781
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode3")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["ranch_return_pending"] = False
            state_module.state["ranch_last_error"] = "灵兽归来广播等待超时，等待起点=昨日"

            with (
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                handled = await ranch.handle_ranch_reply(
                    "你当前没有处于【休息中】的灵兽可供放养。",
                    now,
                    SimpleNamespace(id=123, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertTrue(state_module.state["ranch_return_pending"])
            self.assertEqual(now, state_module.state["ranch_return_wait_since"])
            self.assertEqual("无休息中灵兽，继续等待归来", state_module.state["ranch_last_result"])
            self.assertEqual("", state_module.state["ranch_last_error"])

    async def test_scheduler_blocks_dirty_next_ranch_time_without_mutation(self):
        send_as_id = 3711993782
        now = 1_700_000_000.0
        dirty_next_time = "冷却数据异常"
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode4")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["next_ranch_time"] = dirty_next_time
            state_module.state["ranch_last_result"] = "无休息中灵兽"

            with (
                patch.object(
                    ranch,
                    "_recover_possible_silent_ranch_success_from_log",
                    return_value=True,
                ) as recover_mock,
                patch.object(ranch, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(ranch, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(ranch, "save_state") as save_mock,
            ):
                await ranch.run_ranch_scheduler(now)

            recover_mock.assert_not_called()
            send_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            save_mock.assert_not_called()
            self.assertEqual(dirty_next_time, state_module.state["next_ranch_time"])
            self.assertEqual("无休息中灵兽", state_module.state["ranch_last_result"])

    async def test_scheduler_blocks_dirty_pending_reply_due_without_clearing(self):
        send_as_id = 3711993783
        now = 1_700_000_000.0
        dirty_due_at = "待回复时间异常"
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode5")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["next_ranch_time"] = now - 1
            state_module.state["ranch_reply_to_msg_id"] = 333
            state_module.state["ranch_reply_due_at"] = dirty_due_at
            state_module.state["ranch_retry_count"] = 0
            state_module.state["ranch_return_wait_since"] = 123

            with (
                patch.object(ranch, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(ranch, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(ranch, "save_state") as save_mock,
            ):
                await ranch.run_ranch_scheduler(now)

            send_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            save_mock.assert_not_called()
            self.assertEqual(333, state_module.state["ranch_reply_to_msg_id"])
            self.assertEqual(dirty_due_at, state_module.state["ranch_reply_due_at"])
            self.assertEqual(0, state_module.state["ranch_retry_count"])
            self.assertEqual(123, state_module.state["ranch_return_wait_since"])

    async def test_retry_due_during_dungeon_quiet_defers_without_consuming_retry(self):
        send_as_id = 3711993781
        now = 1_700_000_000.0
        quiet_until = now + 120
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode3")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["next_ranch_time"] = now - 1
            state_module.state["ranch_retry_count"] = 1
            state_module.state["ranch_reply_to_msg_id"] = 0
            state_module.state["ranch_reply_due_at"] = 0
            state_module.state["ranch_return_pending"] = False
            state_module.state["dungeon_quiet_until"] = quiet_until
            state_module.state["dungeon_quiet_reason"] = "虚天殿静场令"

            with (
                patch.object(ranch.random, "uniform", return_value=20),
                patch.object(ranch, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                await ranch.run_ranch_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(1, state_module.state["ranch_retry_count"])
            self.assertEqual(quiet_until + 20, state_module.state["next_ranch_time"])
            self.assertIn("补发撞到虚天殿静场令", state_module.state["ranch_last_error"])
            self.assertNotIn("进入下一轮", state_module.state["ranch_last_error"])

    async def test_send_blocked_by_dungeon_quiet_after_queue_defers_without_retry(self):
        send_as_id = 3711993781
        now = 1_700_000_000.0
        quiet_until = now + 120
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode3")

        async def fake_send(*_args, **_kwargs):
            state_module.state["dungeon_quiet_until"] = quiet_until
            state_module.state["dungeon_quiet_reason"] = "坠魔谷静场令"
            return None

        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["next_ranch_time"] = now - 1
            state_module.state["ranch_retry_count"] = 0
            state_module.state["ranch_reply_to_msg_id"] = 0
            state_module.state["ranch_reply_due_at"] = 0
            state_module.state["ranch_return_pending"] = False

            with (
                patch.object(ranch.random, "uniform", return_value=20),
                patch.object(ranch, "send_game_command", new=fake_send),
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                await ranch.run_ranch_scheduler(now)

            self.assertEqual(0, state_module.state["ranch_retry_count"])
            self.assertEqual(quiet_until + 20, state_module.state["next_ranch_time"])
            self.assertIn("发送撞到坠魔谷静场令", state_module.state["ranch_last_error"])
            self.assertNotIn("准备补发", state_module.state["ranch_last_error"])

    async def test_wrong_sect_variants_disable_ranch(self):
        send_as_id = 3800619925
        now = 1000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="growrdick")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True

            with (
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                handled = await ranch.handle_ranch_reply(
                    "你并非万灵宗弟子，无法通晓御兽之术。",
                    now,
                    SimpleNamespace(id=123, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertIn("并非万灵宗弟子", state_module.state["ranch_last_error"])
