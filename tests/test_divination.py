import asyncio
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import divination
from model import module_manifest
from model.timing import get_day_key


TREASURE_TEXT = (
    "【神物现世】\n"
    "卦象显示，【昆吾通行令】与此番天机相合。\n"
    "你是否愿意消耗 灵石x10 来换取它？\n"
    "请在 5分钟 内回复本消息 .换取"
)

REAL_TREASURE_TEXT = (
    "【神物现世】！天机罗盘疯狂转动，最终指向一处被迷雾笼罩的上古神山！"
    "卦象显示，【昆吾通行令】的机缘已降临于你！\n\n"
    "天道示警：获取此等逆天之物，需献上祭品以获天道认可。\n"
    "你是否愿意消耗 【三级妖丹】x4、【养魂木】x1 来换取它？\n\n"
    "请在 5分钟 内回复本消息 .换取 来确认，超时则机缘消散。"
)

REAL_TREASURE_TEXT_X6 = (
    "【神物现世】！天机罗盘疯狂转动，最终指向一处被迷雾笼罩的上古神山！"
    "卦象显示，【昆吾通行令】的机缘已降临于你！\n\n"
    "天道示警：获取此等逆天之物，需献上祭品以获天道认可。\n"
    "你是否愿意消耗 【三级妖丹】x6、【养魂木】x1 来换取它？\n\n"
    "请在 5分钟 内回复本消息 .换取 来确认，超时则机缘消散。"
)

OTHER_TREASURE_TEXT = (
    "【神物现世】\n"
    "卦象显示，【太虚丹】与此番天机相合。\n"
    "你是否愿意消耗 灵石x10 来换取它？\n"
    "请在 5分钟 内回复本消息 .换取"
)


class DivinationTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._save_state_patcher = patch("model.features.divination.save_state", return_value=True)
        self.save_state_mock = self._save_state_patcher.start()
        async def fake_api_refresh(*, identity_ids=None, write_empty=False, fetch_func=None):
            if identity_ids is None:
                refreshed_ids = [int(identity_id or 0) for identity_id in state_module.get_identity_ids()]
            else:
                refreshed_ids = [int(identity_id or 0) for identity_id in identity_ids]
            refreshed_ids = sorted({identity_id for identity_id in refreshed_ids if identity_id > 0})
            return {
                "ok": bool(refreshed_ids),
                "message": "mocked",
                "updated_count": len(refreshed_ids),
                "skipped_count": 0,
                "updated_identity_ids": refreshed_ids,
            }
        self._api_refresh_patcher = patch(
            "model.features.divination.refresh_storage_bag_records_from_api",
            new=AsyncMock(side_effect=fake_api_refresh),
        )
        self.api_refresh_mock = self._api_refresh_patcher.start()

    def tearDown(self):
        self._api_refresh_patcher.stop()
        self._save_state_patcher.stop()
        state_module._meta_state.clear()
        state_module._meta_state.update(self._meta_state_snapshot)

    def _register_identity(self, send_as_id, username, *, enabled=True, divination_enabled=False):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username=f"@{username}", enabled=enabled)
        state_module.get_identity_state(send_as_id)["divination_enabled"] = bool(divination_enabled)
        return send_as_id

    def _write_divination_message_log(self, tmpdir, now, payloads):
        day = divination.datetime.fromtimestamp(float(now), divination.TZ_LOCAL).strftime("%Y-%m-%d")
        path = Path(tmpdir) / f"{day}.log"
        with path.open("w", encoding="utf-8") as handle:
            for payload in payloads:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path

    def test_default_disabled_and_daily_limit_is_six(self):
        identity_id = self._register_identity(991201, "target")

        self.assertFalse(state_module.get_identity_state(identity_id)["divination_enabled"])
        self.assertEqual(6, state_module.get_divination_daily_limit(identity_id))

    def test_ui_config_updates_daily_limit_snapshot(self):
        from model import ui

        identity_id = self._register_identity(991201, "target")

        async def run_test():
            with patch("model.ui.send_audit_log", new=AsyncMock()):
                ok, message = await ui.ui_set_divination_config(identity_id, daily_limit=9)
                snapshot = ui.get_identity_ui_snapshot(identity_id)
                return ok, message, snapshot

        ok, message, snapshot = asyncio.run(run_test())
        self.assertTrue(ok)
        self.assertIn("9/日", message)
        self.assertEqual(9, state_module.get_divination_daily_limit(identity_id))
        self.assertEqual(9, snapshot["divination_daily_limit"])

    def test_ui_toggle_exposes_divination_switch_in_snapshot(self):
        from model import ui

        identity_id = self._register_identity(991201, "target")

        async def run_test():
            with patch("model.control.save_state"), patch("model.control.console_log"):
                ok, message = await ui.ui_set_module_enabled(identity_id, "卜筮问天", True)
            snapshot = ui.get_identity_ui_snapshot(identity_id)
            module_card = next(item for item in snapshot["modules"] if item["name"] == "卜筮问天")
            return ok, message, module_card

        ok, message, module_card = asyncio.run(run_test())
        self.assertTrue(ok)
        self.assertIn("已开启卜筮问天", message)
        self.assertTrue(module_card["enabled"])

    def test_ui_config_clamps_daily_limit(self):
        from model import ui

        identity_id = self._register_identity(991201, "target")

        async def run_test():
            with patch("model.ui.send_audit_log", new=AsyncMock()):
                ok_high, _message_high = await ui.ui_set_divination_config(identity_id, daily_limit=99)
                high_limit = state_module.get_divination_daily_limit(identity_id)
                ok_low, _message_low = await ui.ui_set_divination_config(identity_id, daily_limit=0)
                low_limit = state_module.get_divination_daily_limit(identity_id)
                snapshot = ui.get_identity_ui_snapshot(identity_id)
                return ok_high, high_limit, ok_low, low_limit, snapshot

        ok_high, high_limit, ok_low, low_limit, snapshot = asyncio.run(run_test())
        self.assertTrue(ok_high)
        self.assertEqual(20, high_limit)
        self.assertTrue(ok_low)
        self.assertEqual(1, low_limit)
        self.assertEqual(1, snapshot["divination_daily_limit"])

    def test_ui_module_detail_uses_selected_identity(self):
        from model import ui

        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_divination_daily_limit(identity_id, 8)

        snapshot = ui.get_identity_ui_snapshot(identity_id)
        divination_card = next(item for item in snapshot["modules"] if item["name"] == "卜筮问天")

        self.assertIn("次数: 8/日", divination_card["detail"])
        self.assertNotIn("未选择身份", divination_card["detail"])

    def test_scheduler_sends_divination_query_when_enabled_and_due(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {"day_key": get_day_key(now), "count": 0, "next_query_at": now - 1}
        })

        async def run_test():
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9001))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(now)
                return send_mock.await_args

        send_args = asyncio.run(run_test())
        self.assertEqual(".卜筮问天", send_args.args[0])
        self.assertEqual(identity_id, send_args.kwargs["send_as_id"])
        self.assertEqual(6, state_module.get_divination_daily_limit(identity_id))
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(9001, record["pending_query_msg_id"])
        self.assertEqual(0, record["next_query_at"])
        self.assertEqual("waiting_intermediate", record["phase"])
        self.assertEqual(1, record["sent_attempts"])
        self.assertIn(":1:try1", send_args.kwargs["op_id"])

    def test_scheduler_persists_initial_start_record_before_first_query(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 36_000.0
        state_module.set_divination_run_state({})

        async def run_test():
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.random.uniform", return_value=5), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock:
                await divination.run_divination_scheduler(now)
                send_mock.assert_not_awaited()

        asyncio.run(run_test())

        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(get_day_key(now), record["day_key"])
        self.assertEqual(0, record["count"])
        self.assertEqual("idle", record["phase"])
        self.assertEqual(now + 5, record["next_query_at"])

    def test_scheduler_sends_after_persisted_initial_start_record(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 36_000.0
        state_module.set_divination_run_state({})

        async def run_test():
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.random.uniform", return_value=5), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as first_send:
                await divination.run_divination_scheduler(now)
                first_send.assert_not_awaited()

            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9002))) as second_send, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(now + 6)
                return second_send.await_args

        send_args = asyncio.run(run_test())

        self.assertEqual(".卜筮问天", send_args.args[0])
        self.assertEqual(identity_id, send_args.kwargs["send_as_id"])
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(9002, record["pending_query_msg_id"])
        self.assertEqual(0, record["next_query_at"])
        self.assertEqual("waiting_intermediate", record["phase"])

    def test_scheduler_prereads_today_count_from_message_log_before_sending(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1_800_000_000.0
        state_module.set_divination_run_state({
            str(identity_id): {"day_key": get_day_key(now), "count": 0, "next_query_at": now - 1}
        })

        async def run_test(tmpdir):
            self._write_divination_message_log(tmpdir, now, [
                {"event_type": "message", "message_id": 7001, "sender_id": identity_id, "text": ".卜筮问天"},
                {"event_type": "message", "message_id": 7002, "sender_id": 8888, "reply_to_msg_id": 7001, "text": "天机罗盘开始转动……今日第 4 次"},
            ])
            with patch("model.features.divination.MESSAGES_DIR", tmpdir), \
                    patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9002))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(now)
                return send_mock.await_args

        with tempfile.TemporaryDirectory() as tmpdir:
            send_args = asyncio.run(run_test(tmpdir))

        self.assertEqual(".卜筮问天", send_args.args[0])
        self.assertIn(":5:try1", send_args.kwargs["op_id"])
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(4, record["count"])
        self.assertEqual(9002, record["pending_query_msg_id"])
        self.assertEqual("waiting_intermediate", record["phase"])

    def test_scheduler_forces_preread_again_when_initial_start_becomes_due(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1_800_000_000.0
        state_module.set_divination_run_state({})

        async def run_test(tmpdir):
            with patch("model.features.divination.MESSAGES_DIR", tmpdir), \
                    patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.random.uniform", return_value=5), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as first_send:
                await divination.run_divination_scheduler(now)
                first_send.assert_not_awaited()

            self._write_divination_message_log(tmpdir, now + 6, [
                {"event_type": "message", "message_id": 7301, "sender_id": identity_id, "text": ".卜筮问天"},
                {"event_type": "message", "message_id": 7302, "sender_id": 8888, "reply_to_msg_id": 7301, "text": "天机罗盘开始转动……今日第 4 次"},
            ])
            with patch("model.features.divination.MESSAGES_DIR", tmpdir), \
                    patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9004))) as second_send, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(now + 6)
                return second_send.await_args

        with tempfile.TemporaryDirectory() as tmpdir:
            send_args = asyncio.run(run_test(tmpdir))

        self.assertEqual(".卜筮问天", send_args.args[0])
        self.assertIn(":5:try1", send_args.kwargs["op_id"])
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(4, record["count"])
        self.assertEqual(9004, record["pending_query_msg_id"])

    def test_scheduler_preread_stops_when_message_log_count_reaches_limit(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1_800_000_000.0
        state_module.set_divination_run_state({
            str(identity_id): {"day_key": get_day_key(now), "count": 0, "next_query_at": now - 1}
        })

        async def run_test(tmpdir):
            self._write_divination_message_log(tmpdir, now, [
                {"event_type": "sent", "message_id": 7101, "sender_id": identity_id, "text": ".卜筮问天"},
                {"event_type": "message", "message_id": 7102, "sender_id": 8888, "reply_to_msg_id": 7101, "text": "天机罗盘开始转动……今日第 6 次"},
            ])
            with patch("model.features.divination.MESSAGES_DIR", tmpdir), \
                    patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.random.uniform", return_value=5), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock:
                await divination.run_divination_scheduler(now)
                send_mock.assert_not_awaited()

        with tempfile.TemporaryDirectory() as tmpdir:
            asyncio.run(run_test(tmpdir))

        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(6, record["count"])
        self.assertEqual("done_today", record["phase"])
        self.assertGreater(record["next_query_at"], now + 3600)

    def test_scheduler_preread_ignores_other_identity_message_log_count(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        other_id = self._register_identity(991202, "other", divination_enabled=True)
        now = 1_800_000_000.0
        state_module.set_divination_run_state({
            str(identity_id): {"day_key": get_day_key(now), "count": 0, "next_query_at": now - 1}
        })

        async def run_test(tmpdir):
            self._write_divination_message_log(tmpdir, now, [
                {"event_type": "sent", "message_id": 7201, "sender_id": other_id, "text": ".卜筮问天"},
                {"event_type": "message", "message_id": 7202, "sender_id": 8888, "reply_to_msg_id": 7201, "text": "天机罗盘开始转动……今日第 6 次"},
            ])
            with patch("model.features.divination.MESSAGES_DIR", tmpdir), \
                    patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9003))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(now)
                return send_mock.await_args

        with tempfile.TemporaryDirectory() as tmpdir:
            send_args = asyncio.run(run_test(tmpdir))

        self.assertEqual(".卜筮问天", send_args.args[0])
        self.assertIn(":1:try1", send_args.kwargs["op_id"])
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(0, record["count"])
        self.assertEqual(9003, record["pending_query_msg_id"])

    def test_scheduler_does_not_send_query_when_disabled(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=False)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {"day_key": get_day_key(now), "count": 0, "next_query_at": now - 1}
        })

        async def run_test():
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(now)
                send_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_scheduler_does_not_repeat_while_query_reply_pending(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(now),
                "count": 1,
                "next_query_at": 0,
                "pending_query_msg_id": 9001,
                "pending_until": now + 120,
            }
        })

        async def run_test():
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock:
                await divination.run_divination_scheduler(now)
                send_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_scheduler_marks_observed_limit_record_done_without_sending(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(now),
                "phase": "idle",
                "count": 6,
                "next_query_at": now + 3600,
            }
        })

        async def run_test():
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock:
                await divination.run_divination_scheduler(now)
                send_mock.assert_not_awaited()

        asyncio.run(run_test())
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(6, record["count"])
        self.assertEqual("done_today", record["phase"])

    def test_scheduler_does_not_send_after_exchange_success_today(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(now),
                "phase": "idle",
                "count": 2,
                "next_query_at": now - 1,
                "exchange_success_day": get_day_key(now),
                "exchange_success_target": "昆吾通行令",
                "exchange_success_at": now - 30,
            }
        })

        async def run_test():
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock:
                await divination.run_divination_scheduler(now)
                send_mock.assert_not_awaited()

        asyncio.run(run_test())
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(2, record["count"])
        self.assertEqual("done_today", record["phase"])
        self.assertGreater(record["next_query_at"], now + 3600)

    def test_swallowed_query_timeout_does_not_increment_count_and_retries_same_target(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(now),
                "phase": "waiting_intermediate",
                "count": 5,
                "sent_attempts": 1,
                "next_query_at": 0,
                "pending_query_msg_id": 9001,
                "pending_until": now - 1,
                "pending_count_recorded": False,
            }
        })

        async def run_test():
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as timeout_send:
                await divination.run_divination_scheduler(now)
                timeout_send.assert_not_awaited()

            after_timeout = state_module.get_divination_run_state()[str(identity_id)]
            self.assertEqual(5, after_timeout["count"])
            self.assertEqual("idle", after_timeout["phase"])
            self.assertEqual(now + 60, after_timeout["next_query_at"])
            self.assertIn("中间态超时", after_timeout["last_error"])

            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9002))) as retry_send, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(now + 61)
                return retry_send.await_args

        retry_args = asyncio.run(run_test())
        self.assertEqual(".卜筮问天", retry_args.args[0])
        self.assertIn(":6:try2", retry_args.kwargs["op_id"])
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(5, record["count"])
        self.assertEqual(2, record["sent_attempts"])
        self.assertEqual("waiting_intermediate", record["phase"])

    def test_intermediate_reply_records_daily_count_and_waits_final_edit(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        event = SimpleNamespace(id=7002, chat_id=-100123)
        text = "你消耗了 20 点修为，开始转动天机罗盘... (今日第 2 次)"

        async def run_test():
            return await divination.handle_divination_reply(
                text,
                1000.0,
                event=event,
                matched_family="divination",
                reply_context={"send_as_id": identity_id, "reply_to_msg_id": 7001},
            )

        self.assertTrue(asyncio.run(run_test()))
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(2, record["count"])
        self.assertEqual(7002, record["pending_reply_msg_id"])
        self.assertGreater(record["pending_until"], 1000.0)
        self.assertTrue(record["pending_count_recorded"])
        self.assertEqual("waiting_final", record["phase"])

    def test_intermediate_reply_closes_query_action_guard(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.get_identity_state(identity_id)["action_guard_sessions"] = {
            "divination": {
                "action_key": "divination",
                "attempt": 1,
                "last_sent_at": 990.0,
                "first_sent_at": 990.0,
                "next_allowed_at": 0,
                "last_msg_id": 7001,
                "last_command": ".卜筮问天",
            }
        }

        async def run_test():
            return await divination.handle_divination_reply(
                "你消耗了 20 点修为，开始转动天机罗盘... (今日第 3 次)",
                1000.0,
                event=SimpleNamespace(id=7002, chat_id=-100123),
                matched_family="divination",
                reply_context={"send_as_id": identity_id, "reply_to_msg_id": 7001},
            )

        self.assertTrue(asyncio.run(run_test()))
        self.assertNotIn("divination", state_module.get_identity_state(identity_id)["action_guard_sessions"])

    def test_observed_daily_limit_schedules_next_day_after_final_edit(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(now),
                "count": 5,
                "next_query_at": 0,
                "pending_query_msg_id": 7001,
                "pending_reply_msg_id": 0,
                "pending_until": now + 120,
                "pending_count_recorded": False,
            }
        })

        async def run_test():
            intermediate_handled = await divination.handle_divination_reply(
                "你消耗了 20 点修为，开始转动天机罗盘... (今日第 6 次)",
                now,
                event=SimpleNamespace(id=7002, chat_id=-100123),
                matched_family="divination",
                reply_context={"send_as_id": identity_id, "reply_to_msg_id": 7001},
            )
            with patch("model.features.divination.random.uniform", return_value=5), \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                final_handled = await divination.handle_divination_reply(
                    "【卦象：吉】\n卦象显示“道心通明”，你对天地法则的理解更深一层。",
                    now + 3,
                    event=SimpleNamespace(id=7002, chat_id=-100123),
                    reply_to=None,
                    matched_family=None,
                    reply_context={},
                )
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock:
                await divination.run_divination_scheduler(now + 61)
                send_mock.assert_not_awaited()
            return intermediate_handled, final_handled

        intermediate_handled, final_handled = asyncio.run(run_test())
        self.assertTrue(intermediate_handled)
        self.assertTrue(final_handled)
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(6, record["count"])
        self.assertEqual(0, record["pending_query_msg_id"])
        self.assertEqual("done_today", record["phase"])
        self.assertGreater(record["next_query_at"], now + 3600)

    def test_plain_final_without_observed_count_does_not_increment_count(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(now),
                "phase": "waiting_intermediate",
                "count": 1,
                "next_query_at": 0,
                "pending_query_msg_id": 7001,
                "pending_reply_msg_id": 7002,
                "pending_until": now + 120,
                "pending_count_recorded": False,
            }
        })

        async def run_test():
            with patch("model.features.divination._recover_daily_count_from_message_log", return_value=0), \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                return await divination.handle_divination_reply(
                    "【卦象：吉】\n卦象显示“道心通明”，你对天地法则的理解更深一层。",
                    now + 3,
                    event=SimpleNamespace(id=7002, chat_id=-100123),
                    reply_to=None,
                    matched_family=None,
                    reply_context={},
                )

        self.assertTrue(asyncio.run(run_test()))
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(1, record["count"])
        self.assertEqual(0, record["pending_query_msg_id"])
        self.assertEqual("idle", record["phase"])
        self.assertEqual(now + 3 + 60, record["next_query_at"])
        self.assertIn("未计入今日次数", record["last_error"])

    def test_plain_final_recovers_observed_count_from_message_log_cache(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(now),
                "phase": "waiting_intermediate",
                "count": 5,
                "next_query_at": 0,
                "pending_query_msg_id": 7001,
                "pending_reply_msg_id": 7002,
                "pending_until": now + 120,
                "pending_count_recorded": False,
            }
        })

        async def run_test():
            with patch("model.features.divination._recover_daily_count_from_message_log", return_value=6), \
                    patch("model.features.divination.random.uniform", return_value=5), \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                return await divination.handle_divination_reply(
                    "【卦象：吉】\n卦象显示“道心通明”，你对天地法则的理解更深一层。",
                    now + 3,
                    event=SimpleNamespace(id=7002, chat_id=-100123),
                    reply_to=None,
                    matched_family=None,
                    reply_context={},
                )

        self.assertTrue(asyncio.run(run_test()))
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(6, record["count"])
        self.assertEqual(0, record["pending_query_msg_id"])
        self.assertEqual("done_today", record["phase"])
        self.assertEqual("", record["last_error"])

    def test_plain_final_edit_after_intermediate_schedules_next_round(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(now),
                "count": 1,
                "next_query_at": 0,
                "pending_query_msg_id": 7001,
                "pending_reply_msg_id": 7002,
                "pending_until": now + 120,
                "pending_count_recorded": True,
            }
        })

        async def run_test():
            with patch("model.features.divination.send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await divination.handle_divination_reply(
                    "【卦象：吉】\n卦象显示“金玉满堂”，你脚下灵光一闪，竟捡到了 2 块灵石！",
                    now + 3,
                    event=SimpleNamespace(id=7002, chat_id=-100123),
                    reply_to=SimpleNamespace(id=7001, raw_text=".卜筮问天"),
                    matched_family=None,
                    reply_context={},
                )
                return handled, audit_mock.await_args

        handled, audit_args = asyncio.run(run_test())
        self.assertTrue(handled)
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(1, record["count"])
        self.assertEqual(0, record["pending_query_msg_id"])
        self.assertEqual(0, record["pending_reply_msg_id"])
        self.assertEqual(now + 3 + 60, record["next_query_at"])
        self.assertEqual("", record["last_error"])
        self.assertIn("卜筮问天结果", audit_args.args[0])
        self.assertIn("金玉满堂", audit_args.args[0])
        self.assertIn("已确认 1/6", audit_args.args[0])
        self.assertEqual("medium", audit_args.kwargs["priority"])

    def test_plain_final_edit_resolves_identity_from_pending_reply_msg_id(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(now),
                "count": 1,
                "next_query_at": 0,
                "pending_query_msg_id": 7001,
                "pending_reply_msg_id": 7002,
                "pending_until": now + 120,
                "pending_count_recorded": True,
            }
        })

        async def run_test():
            with patch("model.features.divination.send_audit_log", new=AsyncMock()):
                return await divination.handle_divination_reply(
                    "【卦象：吉】\n卦象显示“道心通明”，你对天地法则的理解更深一层，修为增加了 149 点！",
                    now + 3,
                    event=SimpleNamespace(id=7002, chat_id=-100123),
                    reply_to=None,
                    matched_family=None,
                    reply_context={},
                )

        self.assertTrue(asyncio.run(run_test()))
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(0, record["pending_query_msg_id"])
        self.assertEqual(now + 3 + 60, record["next_query_at"])

    def test_xiuwei_shortage_stops_remaining_queries_today(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        now = 1000.0

        async def run_test():
            with patch("model.features.divination.send_audit_log", new=AsyncMock()):
                return await divination.handle_divination_reply(
                    "修为不足！神游太虚需消耗 8000 点修为。",
                    now,
                    event=SimpleNamespace(id=7002, chat_id=-100123),
                    matched_family="divination",
                    reply_context={"send_as_id": identity_id, "reply_to_msg_id": 7001},
                )

        self.assertTrue(asyncio.run(run_test()))
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(get_day_key(now), record["blocked_day"])
        self.assertEqual("修为不足", record["block_reason"])

        async def run_scheduler():
            with patch("model.features.divination.get_identity_ids", return_value=[identity_id]), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock:
                await divination.run_divination_scheduler(now + 60)
                send_mock.assert_not_awaited()

        asyncio.run(run_scheduler())

    def test_disabled_module_ignores_treasure_without_exchange_or_transfer(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=False)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"灵石": 10}, "sections": {}}})
        event = SimpleNamespace(id=7001, chat_id=-100123)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock()) as transfer_mock:
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": identity_id},
                )
                send_mock.assert_not_awaited()
                transfer_mock.assert_not_awaited()
                return handled

        self.assertTrue(asyncio.run(run_test()))
        self.assertEqual({}, state_module.get_divination_pending_exchanges())

    def test_ownerless_treasure_text_does_not_exchange_or_transfer(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"灵石": 10}, "sections": {}}})
        event = SimpleNamespace(id=7001, chat_id=-100123)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock()) as transfer_mock:
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={},
                )
                send_mock.assert_not_awaited()
                transfer_mock.assert_not_awaited()
                return handled

        self.assertTrue(asyncio.run(run_test()))
        self.assertEqual({}, state_module.get_divination_pending_exchanges())

    def test_enabled_module_ignores_non_kunwu_treasure_without_exchange_or_transfer(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"灵石": 10}, "sections": {}}})
        event = SimpleNamespace(id=7001, chat_id=-100123)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock()) as transfer_mock:
                handled = await divination.handle_divination_reply(
                    OTHER_TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": identity_id},
                )
                send_mock.assert_not_awaited()
                transfer_mock.assert_not_awaited()
                return handled

        self.assertTrue(asyncio.run(run_test()))
        self.assertEqual({}, state_module.get_divination_pending_exchanges())

    def test_enabled_module_with_enough_storage_sends_exchange(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"灵石": 10}, "sections": {}}})
        event = SimpleNamespace(id=7001, chat_id=-100123)
        self.save_state_mock.reset_mock()

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=8001))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": identity_id},
                )
                return handled, send_mock.await_args

        handled, send_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".换取", send_args.args[0])
        self.assertEqual(identity_id, send_args.kwargs["send_as_id"])
        self.assertEqual(7001, send_args.kwargs["reply_to"])
        self.api_refresh_mock.assert_awaited()
        self.assertIn(identity_id, self.api_refresh_mock.await_args.kwargs["identity_ids"])
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("exchange_sent", pending["status"])
        self.assertGreaterEqual(self.save_state_mock.call_count, 1)

    def test_api_refresh_failure_blocks_auto_exchange_and_transfer(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"灵石": 10}, "sections": {}}})
        event = SimpleNamespace(id=7001, chat_id=-100123)
        self.api_refresh_mock.side_effect = RuntimeError("api down")

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock()) as transfer_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": identity_id},
                )
                send_mock.assert_not_awaited()
                transfer_mock.assert_not_awaited()
                return handled

        self.assertTrue(asyncio.run(run_test()))
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("manual_required", pending["status"])
        self.assertIn("天机阁API读取失败", pending["last_error"])

    def test_enabled_module_uses_sections_cache_when_items_missing(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {}, "sections": {"API": {"灵石": 10}}}})
        event = SimpleNamespace(id=7001, chat_id=-100123)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=8001))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": identity_id},
                )
                return handled, send_mock.await_args

        handled, send_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".换取", send_args.args[0])
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("exchange_sent", pending["status"])

    def test_real_treasure_text_with_material_costs_sends_exchange(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"三级妖丹": 4, "养魂木": 1}, "sections": {}}})
        event = SimpleNamespace(id=9955440, chat_id=-1001680975844)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=8001))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    REAL_TREASURE_TEXT,
                    1000.0,
                    event=event,
                    reply_context={"send_as_id": identity_id, "reply_to_msg_id": 9955438},
                )
                return handled, send_mock.await_args

        handled, send_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".换取", send_args.args[0])
        self.assertEqual(9955440, send_args.kwargs["reply_to"])
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("exchange_sent", pending["status"])
        self.assertEqual({"三级妖丹": 4, "养魂木": 1}, pending["costs"])

    def test_real_treasure_text_with_x6_material_costs_sends_exchange(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"三级妖丹": 6, "养魂木": 1}, "sections": {}}})
        event = SimpleNamespace(id=9955440, chat_id=-1001680975844)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=8001))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    REAL_TREASURE_TEXT_X6,
                    1000.0,
                    event=event,
                    reply_context={"send_as_id": identity_id, "reply_to_msg_id": 9955438},
                )
                return handled, send_mock.await_args

        handled, send_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".换取", send_args.args[0])
        self.assertEqual(9955440, send_args.kwargs["reply_to"])
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("exchange_sent", pending["status"])
        self.assertEqual({"三级妖丹": 6, "养魂木": 1}, pending["costs"])

    def test_edited_treasure_result_can_exchange_after_nonterminal_text(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"三级妖丹": 6, "养魂木": 1}, "sections": {}}})
        event = SimpleNamespace(id=9955440, chat_id=-1001680975844)
        reply_context = {"send_as_id": identity_id, "reply_to_msg_id": 9955438}

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=8001))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                initial_handled = await divination.handle_divination_reply(
                    "天机罗盘缓缓转动，卦象仍在凝聚，请稍候……",
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context=reply_context,
                )
                final_handled = await divination.handle_divination_reply(
                    REAL_TREASURE_TEXT_X6,
                    1003.0,
                    event=event,
                    matched_family="divination",
                    reply_context=reply_context,
                )
                return initial_handled, final_handled, send_mock.await_args

        initial_handled, final_handled, send_args = asyncio.run(run_test())
        self.assertTrue(initial_handled)
        self.assertTrue(final_handled)
        self.assertEqual(".换取", send_args.args[0])
        self.assertEqual({"三级妖丹": 6, "养魂木": 1}, next(iter(state_module.get_divination_pending_exchanges().values()))["costs"])

    def test_enabled_module_with_missing_storage_starts_transfer(self):
        target_id = self._register_identity(991201, "target", divination_enabled=True)
        source_id = self._register_identity(991202, "source", divination_enabled=False)
        state_module.set_storage_bag_records({
            str(target_id): {"items": {"杂草": 1}, "sections": {}},
            str(source_id): {"items": {"灵石": 10}, "sections": {}},
        })
        event = SimpleNamespace(id=7001, chat_id=-100123)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock(return_value=(True, "", {"batch": {"batch_id": "batch-1"}}))) as transfer_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": target_id},
                )
                send_mock.assert_not_awaited()
                return handled, transfer_mock.await_args, audit_mock.await_args

        handled, transfer_args, audit_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(([{"source_identity_id": source_id, "items": [{"item_name": "灵石", "quantity": 10, "method": "basic"}]}],), transfer_args.args)
        self.assertEqual(target_id, transfer_args.kwargs["target_identity_id"])
        self.assertEqual("杂草", transfer_args.kwargs["listing_item"])
        self.assertTrue(transfer_args.kwargs["stop_on_error"])
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("transfer_running", pending["status"])
        self.assertEqual("batch-1", pending["batch_id"])
        self.assertEqual({"灵石": 10}, pending["missing_costs"])
        self.assertIn("缺 灵石x10", audit_args.args[0])

    def test_missing_storage_skips_protected_transfer_source(self):
        target_id = self._register_identity(991201, "target", divination_enabled=True)
        protected_id = self._register_identity(991202, "wa2000", divination_enabled=False)
        state_module.set_storage_bag_records({
            str(target_id): {"items": {"杂草": 1}, "sections": {}},
            str(protected_id): {"items": {"灵石": 10}, "sections": {}},
        })
        event = SimpleNamespace(id=7001, chat_id=-100123)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock()) as transfer_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": target_id},
                )
                send_mock.assert_not_awaited()
                transfer_mock.assert_not_awaited()
                return handled

        self.assertTrue(asyncio.run(run_test()))
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("manual_required", pending["status"])
        self.assertIn("灵石x10", pending["last_error"])

    def test_missing_storage_respects_blocked_cost_rule(self):
        target_id = self._register_identity(991201, "target", divination_enabled=True)
        source_id = self._register_identity(991202, "source", divination_enabled=False)
        state_module.set_storage_bag_item_rules({"灵石": {"method": "blocked", "tags": ["货币"]}})
        state_module.set_storage_bag_records({
            str(target_id): {"items": {"杂草": 1}, "sections": {}},
            str(source_id): {"items": {"灵石": 10}, "sections": {}},
        })
        event = SimpleNamespace(id=7001, chat_id=-100123)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock()) as transfer_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": target_id},
                )
                send_mock.assert_not_awaited()
                transfer_mock.assert_not_awaited()
                return handled

        self.assertTrue(asyncio.run(run_test()))
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("manual_required", pending["status"])
        self.assertIn("灵石x10", pending["last_error"])

    def test_missing_storage_does_not_use_blocked_item_as_listing_marker(self):
        target_id = self._register_identity(991201, "target", divination_enabled=True)
        source_id = self._register_identity(991202, "source", divination_enabled=False)
        state_module.set_storage_bag_records({
            str(target_id): {"items": {"昆吾通行令": 1}, "sections": {}},
            str(source_id): {"items": {"灵石": 10}, "sections": {}},
        })
        event = SimpleNamespace(id=7001, chat_id=-100123)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock()) as transfer_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": target_id},
                )
                send_mock.assert_not_awaited()
                transfer_mock.assert_not_awaited()
                return handled

        self.assertTrue(asyncio.run(run_test()))
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("manual_required", pending["status"])
        self.assertEqual("目标身份缺少可上架标记物", pending["last_error"])

    def test_missing_storage_uses_rule_transfer_method(self):
        target_id = self._register_identity(991201, "target", divination_enabled=True)
        source_id = self._register_identity(991202, "source", divination_enabled=False)
        state_module.set_storage_bag_item_rules({"灵石": {"method": "gift", "tags": ["货币"]}})
        state_module.set_storage_bag_records({
            str(target_id): {"items": {"杂草": 1}, "sections": {}},
            str(source_id): {"items": {"灵石": 10}, "sections": {}},
        })
        event = SimpleNamespace(id=7001, chat_id=-100123)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock(return_value=(True, "", {"batch": {"batch_id": "batch-1"}}))) as transfer_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": target_id},
                )
                send_mock.assert_not_awaited()
                return handled, transfer_mock.await_args

        handled, transfer_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual("gift", transfer_args.args[0][0]["items"][0]["method"])

    def test_missing_resources_without_sources_requires_manual_handling(self):
        target_id = self._register_identity(991201, "target", divination_enabled=True)
        source_id = self._register_identity(991202, "source", divination_enabled=False)
        state_module.set_storage_bag_records({
            str(target_id): {"items": {"杂草": 1, "三级妖丹": 2}, "sections": {}},
            str(source_id): {"items": {"养魂木": 1}, "sections": {}},
        })
        event = SimpleNamespace(id=9955440, chat_id=-1001680975844)

        async def run_test():
            with patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.start_storage_bag_transfer_batch", new=AsyncMock()) as transfer_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_reply(
                    REAL_TREASURE_TEXT,
                    1000.0,
                    event=event,
                    matched_family="divination",
                    reply_context={"send_as_id": target_id},
                )
                send_mock.assert_not_awaited()
                transfer_mock.assert_not_awaited()
                return handled

        self.assertTrue(asyncio.run(run_test()))
        pending = next(iter(state_module.get_divination_pending_exchanges().values()))
        self.assertEqual("manual_required", pending["status"])
        self.assertIn("三级妖丹x2", pending["last_error"])

    def test_scheduler_after_transfer_sends_exchange_only(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"灵石": 10}, "sections": {}}})
        state_module.set_divination_pending_exchanges({
            "key-1": {
                "key": "key-1",
                "status": "transfer_running",
                "target_identity_id": identity_id,
                "target_item": "昆吾通行令",
                "costs": {"灵石": 10},
                "source_msg_id": 7001,
                "expires_at": 1300.0,
                "batch_id": "batch-1",
            }
        })

        async def run_test():
            with patch("model.features.divination.get_storage_bag_transfer_snapshot", return_value={"batch": {"batch_id": "batch-1", "status": "done", "running": False}}), \
                    patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=8001))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(1100.0)
                return [call.args[0] for call in send_mock.await_args_list]

        sent_commands = asyncio.run(run_test())
        self.assertEqual([".换取"], sent_commands)
        self.assertNotIn(".卜筮问天", sent_commands)

    def test_scheduler_expires_transfer_without_exchange(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"灵石": 10}, "sections": {}}})
        state_module.set_divination_pending_exchanges({
            "key-1": {
                "key": "key-1",
                "status": "transfer_running",
                "target_identity_id": identity_id,
                "target_item": "昆吾通行令",
                "costs": {"灵石": 10},
                "source_msg_id": 7001,
                "expires_at": 1099.0,
                "batch_id": "batch-1",
            }
        })

        async def run_test():
            with patch("model.features.divination.get_storage_bag_transfer_snapshot", return_value={"batch": {"batch_id": "batch-1", "status": "done", "running": False}}), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(1100.0)
                send_mock.assert_not_awaited()

        asyncio.run(run_test())
        self.assertEqual({}, state_module.get_divination_pending_exchanges())

    def test_scheduler_recovers_transfer_after_restart_when_storage_is_enough(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"三级妖丹": 4, "养魂木": 1}, "sections": {}}})
        state_module.set_divination_pending_exchanges({
            "key-1": {
                "key": "key-1",
                "status": "transfer_running",
                "target_identity_id": identity_id,
                "target_item": "昆吾通行令",
                "costs": {"三级妖丹": 4, "养魂木": 1},
                "source_msg_id": 9955440,
                "expires_at": 1300.0,
                "batch_id": "batch-before-restart",
            }
        })

        async def run_test():
            with patch("model.features.divination.get_storage_bag_transfer_snapshot", return_value={"batch": {}}), \
                    patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=8001))) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(1100.0)
                return send_mock.await_args

        send_args = asyncio.run(run_test())
        self.assertEqual(".换取", send_args.args[0])
        self.assertEqual(9955440, send_args.kwargs["reply_to"])
        pending = state_module.get_divination_pending_exchanges()["key-1"]
        self.assertEqual("exchange_sent", pending["status"])

    def test_scheduler_does_not_recover_exchange_when_module_is_disabled(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=False)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"三级妖丹": 4, "养魂木": 1}, "sections": {}}})
        state_module.set_divination_pending_exchanges({
            "key-1": {
                "key": "key-1",
                "status": "transfer_running",
                "target_identity_id": identity_id,
                "target_item": "昆吾通行令",
                "costs": {"三级妖丹": 4, "养魂木": 1},
                "source_msg_id": 9955440,
                "expires_at": 1300.0,
                "batch_id": "batch-before-restart",
            }
        })

        async def run_test():
            with patch("model.features.divination.get_storage_bag_transfer_snapshot", return_value={"batch": {}}), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(1100.0)
                send_mock.assert_not_awaited()

        asyncio.run(run_test())
        pending = state_module.get_divination_pending_exchanges()["key-1"]
        self.assertEqual("manual_required", pending["status"])
        self.assertEqual("卜筮问天模块已关闭", pending["last_error"])

    def test_scheduler_does_not_exchange_unsupported_pending_target(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"灵石": 10}, "sections": {}}})
        state_module.set_divination_pending_exchanges({
            "key-1": {
                "key": "key-1",
                "status": "transfer_running",
                "target_identity_id": identity_id,
                "target_item": "太虚丹",
                "costs": {"灵石": 10},
                "source_msg_id": 7001,
                "expires_at": 1300.0,
                "batch_id": "batch-1",
            }
        })

        async def run_test():
            with patch("model.features.divination.get_storage_bag_transfer_snapshot", return_value={"batch": {"batch_id": "batch-1", "status": "done", "running": False}}), \
                    patch("model.features.divination.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.features.divination.send_audit_log", new=AsyncMock()):
                await divination.run_divination_scheduler(1100.0)
                send_mock.assert_not_awaited()

        asyncio.run(run_test())
        pending = state_module.get_divination_pending_exchanges()["key-1"]
        self.assertEqual("manual_required", pending["status"])
        self.assertEqual("仅自动处理昆吾通行令", pending["last_error"])

    def test_exchange_success_updates_storage_cache_and_clears_pending(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"三级妖丹": 4, "养魂木": 1}, "sections": {}}})
        state_module.set_divination_run_state({
            str(identity_id): {
                "day_key": get_day_key(1100.0),
                "phase": "idle",
                "count": 2,
                "next_query_at": 1101.0,
            }
        })
        state_module.set_divination_pending_exchanges({
            "key-1": {
                "key": "key-1",
                "status": "exchange_sent",
                "target_identity_id": identity_id,
                "target_item": "昆吾通行令",
                "costs": {"三级妖丹": 4, "养魂木": 1},
                "source_msg_id": 9955440,
                "exchange_msg_id": 9966046,
                "expires_at": 1300.0,
            }
        })
        success_text = "换取成功！\n你已成功献上祭品，获得了天道认可，【昆吾通行令】已放入你的储物袋！"

        async def run_test():
            with patch("model.features.divination.send_audit_log", new=AsyncMock()):
                handled = await divination.handle_divination_exchange_reply(
                    success_text,
                    1100.0,
                    reply_context={"send_as_id": identity_id, "reply_to_msg_id": 9966046},
                )
                return handled

        self.assertTrue(asyncio.run(run_test()))
        self.assertEqual({}, state_module.get_divination_pending_exchanges())
        items = state_module.get_storage_bag_records()[str(identity_id)]["items"]
        self.assertEqual(0, items.get("三级妖丹", 0))
        self.assertEqual(0, items.get("养魂木", 0))
        self.assertEqual(1, items["昆吾通行令"])
        record = state_module.get_divination_run_state()[str(identity_id)]
        self.assertEqual(2, record["count"])
        self.assertEqual("done_today", record["phase"])
        self.assertEqual(get_day_key(1100.0), record["exchange_success_day"])
        self.assertEqual("昆吾通行令", record["exchange_success_target"])
        self.assertGreater(record["next_query_at"], 1100.0 + 3600)

    def test_exchange_reply_uses_pending_match_when_family_is_stale(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_storage_bag_records({str(identity_id): {"items": {"三级妖丹": 4, "养魂木": 1}, "sections": {}}})
        state_module.set_divination_pending_exchanges({
            "key-1": {
                "key": "key-1",
                "status": "exchange_sent",
                "target_identity_id": identity_id,
                "target_item": "昆吾通行令",
                "costs": {"三级妖丹": 4, "养魂木": 1},
                "source_msg_id": 9955440,
                "exchange_msg_id": 9966046,
                "expires_at": 1300.0,
            }
        })

        async def run_test():
            with patch("model.features.divination.send_audit_log", new=AsyncMock()):
                return await divination.handle_divination_exchange_reply(
                    "换取成功！\n你已成功献上祭品，获得了天道认可，【昆吾通行令】已放入你的储物袋！",
                    1100.0,
                    matched_family="divination",
                    reply_context={"send_as_id": identity_id, "reply_to_msg_id": 9966046},
                )

        self.assertTrue(asyncio.run(run_test()))
        self.assertEqual({}, state_module.get_divination_pending_exchanges())

    def test_status_shows_pending_exchange_error_detail(self):
        identity_id = self._register_identity(991201, "target", divination_enabled=True)
        state_module.set_divination_pending_exchanges({
            "key-1": {
                "key": "key-1",
                "status": "manual_required",
                "target_identity_id": identity_id,
                "target_item": "昆吾通行令",
                "costs": {"灵石": 10},
                "last_error": "可用库存不足：灵石x4",
                "created_at": 1000.0,
            }
        })

        text = divination.get_divination_status_text(identity_id)

        self.assertIn("待处理: 1", text)
        self.assertIn("昆吾通行令: 需手动处理", text)
        self.assertIn("需 灵石x10", text)
        self.assertIn("可用库存不足：灵石x4", text)

    def test_divination_ui_script_loads_after_main_app_and_posts_config_endpoint(self):
        html = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "model/web/static/js/divination_ui.js").read_text(encoding="utf-8")

        app_index = html.index("<script src='/static/js/app.js'></script>")
        divination_index = html.index("<script src='/static/js/divination_ui.js'></script>")

        self.assertLess(app_index, divination_index)
        self.assertIn("renderModules", script)
        self.assertIn("/api/divination-config", script)
        self.assertIn("data-divination-daily-limit", script)
        self.assertIn("existingControl", script)
        self.assertIn("existingInput.value = String(limit)", script)

    def test_divination_manifest_is_automatic_query_module(self):
        manifest = module_manifest.MODULE_MANIFESTS["卜筮问天"]

        self.assertEqual(module_manifest.SEND_POLICY_OBSERVE_THEN_SEND, manifest.send_policy)
        self.assertEqual(module_manifest.ACTIVE_QUERY_FALLBACK_ONLY, manifest.active_query_policy)
        self.assertEqual(("divination",), manifest.replay_modules)
        self.assertEqual(("divination", "divination_exchange"), manifest.reply_families)

    def test_divination_pending_commands_map_to_module_name(self):
        from model import control

        self.assertEqual("卜筮问天", control._get_pending_task_module_name(".卜筮问天"))
        self.assertEqual("卜筮问天", control._get_pending_task_module_name(".换取"))


if __name__ == "__main__":
    unittest.main()
