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
from model import action_guard
from model import config
from model import runtime
from model import state as state_module
from model.features import checkin


class EarlyReplyReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._reply_tracker_snapshot = copy.deepcopy(runtime._reply_chain_tracker)
        self._event_claims_snapshot = dict(app_runtime._runtime_event_claims)
        self._consumed_snapshot = dict(app_runtime._runtime_message_consumed)
        self._guard_snapshot = dict(action_guard._recent_closed_command_guards)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        runtime._reply_chain_tracker.clear()
        app_runtime._runtime_event_claims.clear()
        app_runtime._runtime_message_consumed.clear()
        app._early_routed_replies.clear()

    def tearDown(self):
        app._early_routed_replies.clear()
        runtime._reply_chain_tracker.clear()
        runtime._reply_chain_tracker.update(self._reply_tracker_snapshot)
        app_runtime._runtime_event_claims.clear()
        app_runtime._runtime_event_claims.update(self._event_claims_snapshot)
        app_runtime._runtime_message_consumed.clear()
        app_runtime._runtime_message_consumed.update(self._consumed_snapshot)
        action_guard._recent_closed_command_guards.clear()
        action_guard._recent_closed_command_guards.update(self._guard_snapshot)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def test_reply_before_send_bookkeeping_is_replayed_after_registration(self):
        identity_id = 7538826434
        command_msg_id = 154926
        reply_msg_id = 154927
        event_at = 1_784_084_108.0
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="Lpprceqei", sect_name="星宫")

        with state_module.use_identity(identity_id) as identity_state:
            identity_state["checkin_enabled"] = True
            identity_state["sect_teach_enabled"] = False
            identity_state["checkin_teach_day"] = checkin.get_checkin_day_key(event_at)
            identity_state["last_checkin_done_day"] = ""
            identity_state["next_checkin_time"] = event_at - 1

        reply_to = SimpleNamespace(id=command_msg_id, raw_text=config.CMD_CHECKIN, sender_id=identity_id)
        event = SimpleNamespace(
            id=reply_msg_id,
            chat_id=-1001680975844,
            sender_id=8861328042,
            raw_text="点卯成功！你获得了 105 点宗门贡献。",
            reply_to=SimpleNamespace(reply_to_msg_id=command_msg_id, reply_to_top_id=7310786),
            message=SimpleNamespace(buttons=None),
        )
        early_context = {
            "send_as_id": identity_id,
            "family": None,
            "reply_to_msg_id": command_msg_id,
            "root_msg_id": command_msg_id,
            "matched_via": "reply_sender",
            "source": "",
        }

        with (
            state_module.use_identity(identity_id),
            patch.object(checkin, "save_state"),
            patch.object(app, "schedule_cleanup", new=AsyncMock()),
        ):
            first_handled = await app._handle_routed_reply_event(
                event,
                event.raw_text,
                event_at,
                reply_to,
                early_context,
            )

        self.assertTrue(first_handled)
        self.assertIn((event.chat_id, identity_id, command_msg_id), app._early_routed_replies)

        with state_module.use_identity(identity_id) as identity_state:
            identity_state["pending_tasks"][command_msg_id] = {
                "cmd": config.CMD_CHECKIN,
                "chat_id": event.chat_id,
                "sent_at": event_at + 3,
                "retry": 0,
                "timeout": 900,
            }
            identity_state["my_msg_ids"][command_msg_id] = event_at + 3
        runtime.track_reply_chain_message(command_msg_id, identity_id, "checkin", root_msg_id=command_msg_id)

        with (
            state_module.use_identity(identity_id),
            patch.object(app, "_EARLY_ROUTED_REPLY_REPLAY_DELAY_SEC", 0),
            patch.object(checkin, "save_state"),
            patch.object(app, "schedule_cleanup", new=AsyncMock()),
            patch.object(app, "_bind_command_attempt_shadow") as bind_mock,
        ):
            replayed = await app._replay_early_replies_after_sent(
                identity_id,
                config.CMD_CHECKIN,
                event_at + 3,
                command_msg_id,
            )

        self.assertTrue(replayed)
        bind_mock.assert_called_once()
        with state_module.use_identity(identity_id) as identity_state:
            self.assertNotIn(command_msg_id, identity_state["pending_tasks"])
            self.assertEqual(checkin.get_checkin_day_key(event_at), identity_state["last_checkin_done_day"])

    async def test_timeout_log_recovery_updates_business_state_not_only_pending(self):
        identity_id = 7538826434
        sent_at = 1_784_084_100.0
        reply_at = sent_at + 2
        now = sent_at + 120
        chat_id = -1001680975844
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="audit_identity", sect_name="星宫")
        with state_module.use_identity(identity_id) as identity_state:
            identity_state.update(
                checkin_enabled=True,
                sect_teach_enabled=False,
                last_checkin_done_day="",
                next_checkin_time=sent_at - 1,
            )
            identity_state["my_msg_ids"][154926] = sent_at
            identity_state["pending_tasks"][154926] = {
                "cmd": config.CMD_CHECKIN,
                "chat_id": chat_id,
                "sent_at": sent_at,
                "retry": 0,
                "timeout": 10,
                "max_retry": 0,
            }
        reply = {
            "event_type": "message",
            "message_id": 154927,
            "reply_to_msg_id": 154926,
            "chat_id": chat_id,
            "sender_id": 8861328042,
            "sender_is_bot": True,
            "ts_epoch": reply_at,
            "text": "点卯成功！你获得了 105 点宗门贡献。",
        }
        with (
            patch.object(runtime, "find_message_log_replies", return_value=[reply]),
            patch.object(runtime, "get_game_bot_ids", return_value=[8861328042]),
            patch.object(runtime, "should_pause_for_bot_health", return_value=False),
            patch.object(runtime, "send_game_command", new=AsyncMock()) as send,
            patch.object(app, "schedule_cleanup", new=AsyncMock()),
            patch.object(checkin, "save_state"),
        ):
            await runtime.run_retry_scheduler(now, send_as_id=identity_id)

        send.assert_not_awaited()
        identity_state = state_module.get_identity_state(identity_id)
        self.assertEqual(checkin.get_checkin_day_key(reply_at), identity_state["last_checkin_done_day"])
        self.assertGreater(identity_state["next_checkin_time"], now)
        self.assertNotIn(154926, identity_state["pending_tasks"])

    def _pending_log_fixture(self, command=config.CMD_CHECKIN):
        identity_id = 7538826434
        sent_at = 1_784_084_100.0
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="audit_identity", sect_name="星宫")
        item = {
            "cmd": command,
            "chat_id": -1001680975844,
            "sent_at": sent_at,
            "retry": 0,
            "timeout": 10,
            "max_retry": 0,
        }
        with state_module.use_identity(identity_id) as identity_state:
            identity_state.update(checkin_enabled=True, sect_teach_enabled=False)
            identity_state["pending_tasks"][154926] = item
            identity_state["my_msg_ids"][154926] = sent_at
            if command == config.CMD_IDENTITY_INFO:
                identity_state["last_identity_info_msg_id"] = 154926
        reply = {
            "event_type": "message",
            "message_id": 154927,
            "reply_to_msg_id": 154926,
            "chat_id": item["chat_id"],
            "sender_id": 8861328042,
            "sender_is_bot": True,
            "ts_epoch": sent_at + 2,
            "text": "点卯成功！你获得了 105 点宗门贡献。",
        }
        self.enterContext(patch.object(runtime, "get_game_bot_ids", return_value=[8861328042]))
        self.enterContext(patch.object(app, "schedule_cleanup", new=AsyncMock()))
        self.enterContext(patch.object(checkin, "save_state"))
        return identity_id, item, reply, sent_at + 120

    async def test_pending_replay_does_not_clear_other_command_in_same_family(self):
        identity_id, item, reply, now = self._pending_log_fixture()
        other = {**item, "chat_id": -1002083016447, "sent_at": now}
        identity_state = state_module.get_identity_state(identity_id)
        identity_state["pending_tasks"][154928] = other
        with patch.object(runtime, "find_message_log_replies", return_value=[reply]):
            await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now)
        self.assertNotIn(154926, identity_state["pending_tasks"])
        self.assertEqual(other, identity_state["pending_tasks"][154928])

    async def test_live_reply_does_not_clear_newer_same_family_pending_or_guard(self):
        identity_id, item, reply, now = self._pending_log_fixture()
        identity_state = state_module.get_identity_state(identity_id)
        identity_state["pending_tasks"][154928] = {**item, "sent_at": now}
        event, reply_to = app._logged_reply_event(reply, item["cmd"], identity_id)
        context = {"send_as_id": identity_id, "family": "checkin", "reply_to_msg_id": 154926,
                   "root_msg_id": 154926, "chat_id": reply["chat_id"]}
        with patch.object(app, "close_action_guard_by_family") as close:
            handled = await app._handle_routed_reply_event(event, event.raw_text, now, reply_to, context)
        self.assertTrue(handled)
        self.assertNotIn(154926, identity_state["pending_tasks"])
        self.assertIn(154928, identity_state["pending_tasks"])
        self.assertEqual(154926, close.call_args.kwargs["expected_msg_id"])

    async def test_live_unmatched_or_failed_handler_preserves_pending(self):
        identity_id, item, reply, now = self._pending_log_fixture()
        event, reply_to = app._logged_reply_event(reply, item["cmd"], identity_id)
        context = {"send_as_id": identity_id, "family": "checkin", "reply_to_msg_id": 154926,
                   "root_msg_id": 154926, "chat_id": reply["chat_id"]}
        with patch.object(app, "handle_checkin_reply", new=AsyncMock(return_value=False)):
            handled = await app._handle_routed_reply_event(event, "unrecognized result", now, reply_to, context)
        self.assertFalse(handled)
        self.assertIn(154926, state_module.get_identity_state(identity_id)["pending_tasks"])
        app_runtime._runtime_event_claims.clear()
        with patch.object(app, "handle_checkin_reply", side_effect=RuntimeError("injected failure")):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                await app._handle_routed_reply_event(event, event.raw_text, now, reply_to, context)
        self.assertIn(154926, state_module.get_identity_state(identity_id)["pending_tasks"])

    async def test_early_reply_cache_does_not_replay_another_chat_or_identity(self):
        identity_id, item, reply, now = self._pending_log_fixture()
        other_identity = identity_id + 1
        state_module.ensure_identity_registered(other_identity)
        other_chat = -1002083016447
        for current_identity, chat_id in ((identity_id, item["chat_id"]), (other_identity, other_chat)):
            entry = {**reply, "chat_id": chat_id}
            event, reply_to = app._logged_reply_event(entry, item["cmd"], current_identity)
            app._remember_early_routed_reply(event, event.raw_text, now, reply_to, {
                "send_as_id": current_identity, "reply_to_msg_id": 154926, "matched_via": "reply_sender",
            }, event_kind="message")
        with (
            patch.object(app, "_EARLY_ROUTED_REPLY_REPLAY_DELAY_SEC", 0),
            patch.object(app, "_handle_routed_reply_event", new=AsyncMock(return_value=True)) as handler,
            patch.object(app, "_bind_command_attempt_shadow"),
        ):
            self.assertTrue(await app._replay_early_replies_after_sent(
                identity_id, item["cmd"], now, 154926, game_group_id=item["chat_id"],
            ))
            self.assertEqual(1, handler.await_count)
            self.assertEqual(item["chat_id"], handler.await_args.args[0].chat_id)
            handler.reset_mock()
            self.assertTrue(await app._replay_early_replies_after_sent(
                other_identity, item["cmd"], now, 154926, game_group_id=other_chat,
            ))
            self.assertEqual(1, handler.await_count)
            self.assertEqual(other_chat, handler.await_args.args[0].chat_id)

    async def test_identity_info_final_edit_is_dispatched_after_partial_card(self):
        identity_id, item, reply, now = self._pending_log_fixture(config.CMD_IDENTITY_INFO)
        event, reply_to = app._logged_reply_event(reply, item["cmd"], identity_id)
        context = {"send_as_id": identity_id, "family": "identity_info", "reply_to_msg_id": 154926,
                   "root_msg_id": 154926, "chat_id": reply["chat_id"]}
        with patch.object(app, "handle_identity_info_reply", new=AsyncMock(return_value=True)) as handler:
            self.assertTrue(await app._handle_routed_reply_event(event, "partial card", now, reply_to, context))
            self.assertTrue(await app._handle_routed_reply_event(event, "final card", now + 1, reply_to, context, event_kind="edit"))
        self.assertEqual(["partial card", "final card"], [call.args[0] for call in handler.await_args_list])

    async def test_pending_replay_rejects_wrong_chat_anchor_player_and_untrusted_bot(self):
        identity_id, item, reply, now = self._pending_log_fixture()
        invalid_replies = [
            {**reply, "chat_id": -1002083016447},
            {**reply, "reply_to_msg_id": 154925},
            {**reply, "sender_id": 1234, "sender_is_bot": False},
            {**reply, "sender_id": 1234, "sender_is_bot": True},
            {**reply, "event_type": "sent"},
        ]
        with (
            patch.object(runtime, "find_message_log_replies", return_value=invalid_replies),
            patch.object(runtime, "_GAME_REPLY_REPLAYER", new=AsyncMock()) as replayer,
        ):
            recovered = await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now)
        self.assertIsNone(recovered)
        replayer.assert_not_awaited()
        self.assertIn(154926, state_module.get_identity_state(identity_id)["pending_tasks"])

    async def test_unmatched_reply_keeps_pending_and_does_not_resend_after_log_expires(self):
        identity_id, item, reply, now = self._pending_log_fixture()
        reply["text"] = "无法识别的新格式回包"
        with (
            patch.object(runtime, "find_message_log_replies", side_effect=[[reply], []]),
            patch.object(runtime, "should_pause_for_bot_health", return_value=False),
            patch.object(runtime, "get_bot_last_seen_at", return_value=now + 10_000),
            patch.object(runtime, "send_game_command", new=AsyncMock()) as send,
            patch.object(runtime, "send_audit_log", new=AsyncMock()),
            patch.object(app, "handle_checkin_reply", new=AsyncMock(return_value=False)),
        ):
            await runtime.run_retry_scheduler(now, send_as_id=identity_id)
            await runtime.run_retry_scheduler(now + 600, send_as_id=identity_id)
        send.assert_not_awaited()
        pending = state_module.get_identity_state(identity_id)["pending_tasks"][154926]
        self.assertEqual("reply_handler_not_matched", pending["reply_recovery_error"])

    async def test_handler_failure_can_replay_on_next_recovery_without_losing_sibling(self):
        identity_id, item, reply, now = self._pending_log_fixture()
        identity_state = state_module.get_identity_state(identity_id)
        identity_state["pending_tasks"][154928] = {**item, "sent_at": now}
        original_handler = app.handle_checkin_reply

        async def handle_after_failure(*args, **kwargs):
            if not getattr(handle_after_failure, "failed", False):
                handle_after_failure.failed = True
                raise RuntimeError("injected handler failure")
            return await original_handler(*args, **kwargs)

        with (
            patch.object(runtime, "find_message_log_replies", return_value=[reply]),
            patch.object(app, "handle_checkin_reply", side_effect=handle_after_failure),
        ):
            first = await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now)
            self.assertFalse(first["recovery_handled"])
            self.assertIn(154926, identity_state["pending_tasks"])
            self.assertIn(154928, identity_state["pending_tasks"])
            second = await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now + 61)
        self.assertTrue(second["recovery_handled"])
        self.assertNotIn(154926, identity_state["pending_tasks"])
        self.assertIn(154928, identity_state["pending_tasks"])

    async def test_missing_handler_preserves_pending_without_claiming_success(self):
        identity_id, item, reply, now = self._pending_log_fixture()
        with (
            patch.object(runtime, "find_message_log_replies", return_value=[reply]),
            patch.object(runtime, "_GAME_REPLY_REPLAYER", None),
        ):
            result = await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now)
        self.assertFalse(result["recovery_handled"])
        self.assertEqual("reply_handler_unavailable", item["reply_recovery_error"])

    async def test_waiting_reply_keeps_pending_and_action_guard(self):
        identity_id, item, reply, now = self._pending_log_fixture(config.CMD_IDENTITY_INFO)
        reply["text"] = "正在推演天机，请稍候。"
        with (
            patch.object(runtime, "find_message_log_replies", return_value=[reply]),
            patch.object(app, "handle_identity_info_reply", new=AsyncMock(return_value=True)),
            patch.object(app, "close_action_guard_by_family") as app_close,
            patch.object(runtime, "action_guard_close_by_family") as runtime_close,
        ):
            result = await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now)
        self.assertTrue(result["recovery_handled"])
        self.assertIn(154926, state_module.get_identity_state(identity_id)["pending_tasks"])
        app_close.assert_not_called()
        runtime_close.assert_not_called()

    async def test_duplicate_replay_cleans_late_pending_without_reapplying_business(self):
        identity_id, item, reply, now = self._pending_log_fixture()
        with (
            patch.object(runtime, "find_message_log_replies", return_value=[reply]),
            patch.object(app, "handle_checkin_reply", wraps=app.handle_checkin_reply) as handler,
        ):
            await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now)
            state_module.get_identity_state(identity_id)["pending_tasks"][154926] = dict(item)
            result = await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now + 61)
        self.assertTrue(result["recovery_handled"])
        self.assertEqual(1, handler.await_count)
        self.assertNotIn(154926, state_module.get_identity_state(identity_id)["pending_tasks"])

    async def test_out_of_order_edit_replays_ack_then_result_once(self):
        identity_id, item, reply, now = self._pending_log_fixture(".斗法 @audit_target")
        reply["text"] = "test ack"
        edit = {**reply, "event_type": "edit", "text": "test result", "ts_epoch": reply["ts_epoch"] + 2}
        observed = []

        async def handle_duel(text, *args, **kwargs):
            observed.append(text)
            return True

        with (
            patch.object(runtime, "find_message_log_replies", return_value=[edit, reply, edit]),
            patch.object(app, "handle_duel_reply", side_effect=handle_duel),
        ):
            await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now)
        self.assertEqual(["test ack", "test result"], observed)

    async def test_ack_receipt_survives_dedupe_expiry_and_allows_final_edit(self):
        identity_id, item, reply, now = self._pending_log_fixture(config.CMD_IDENTITY_INFO)
        reply["text"] = "正在推演天机，请稍候。"
        edit = {**reply, "event_type": "edit", "text": "天命玉牒：推演完成", "ts_epoch": now + 500}
        with (
            patch.object(runtime, "find_message_log_replies", side_effect=[[reply], [reply], [reply, edit]]),
            patch.object(app, "handle_identity_info_reply", new=AsyncMock(return_value=True)) as handler,
        ):
            await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now)
            app_runtime._runtime_event_claims.clear()
            app_runtime._runtime_message_consumed.clear()
            await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now + 300)
            await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now + 600)
        self.assertEqual([reply["text"], edit["text"]], [call.args[0] for call in handler.await_args_list])
        self.assertNotIn(154926, state_module.get_identity_state(identity_id)["pending_tasks"])

    async def test_old_reply_does_not_close_newer_action_guard(self):
        identity_id, item, reply, now = self._pending_log_fixture(config.CMD_WENDAO)
        action_guard.note_sent(item["cmd"], identity_id, 154928, now)
        identity_state = state_module.get_identity_state(identity_id)
        sessions_before = copy.deepcopy(identity_state["action_guard_sessions"])
        self.assertTrue(sessions_before)
        with (
            patch.object(runtime, "find_message_log_replies", return_value=[reply]),
            patch.object(app, "handle_wendao_reply", new=AsyncMock(return_value=True)),
        ):
            await runtime._recover_pending_reply_from_message_log(identity_id, 154926, item, now)
        self.assertEqual(sessions_before, identity_state["action_guard_sessions"])


if __name__ == "__main__":
    unittest.main()
