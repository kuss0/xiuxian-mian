import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import control, runtime, state as state_module
from model.features import checkin, passive_inbox
from model.real_message_replay import get_real_message_text


class CheckinRouteLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.snapshot = copy.deepcopy(state_module._meta_state)
        self.identity_id = 992301
        self.now = datetime(2026, 9, 7, 2, 30, tzinfo=timezone.utc).timestamp()
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(self.identity_id, username="TeachRoute", sect_name="星宫")
        self.identity = state_module.get_identity_state(self.identity_id)
        self.identity.update({
            "checkin_enabled": False,
            "sect_teach_enabled": True,
            "checkin_teach_day": checkin.get_checkin_day_key(self.now),
            "next_sect_teach_time": self.now,
            "sect_teach_reply_to_msg_id": 123,
            "sect_teach_reply_chat_id": -1002,
        })
        self.save_patch = patch.object(checkin, "save_state")
        self.save_patch.start()
        self.addCleanup(self.save_patch.stop)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self.snapshot)

    async def test_checkin_and_teach_replies_keep_their_original_chat(self):
        self.identity["checkin_enabled"] = True
        with state_module.use_identity(self.identity_id):
            await checkin.handle_checkin_reply(
                "点卯成功", self.now,
                SimpleNamespace(id=123, chat_id=-1002, raw_text=checkin.CMD_CHECKIN),
                matched_family="checkin",
            )
            self.assertEqual(-1002, self.identity.get("last_checkin_chat_id"))
            self.assertEqual(-1002, self.identity.get("sect_teach_reply_chat_id"))
            self.identity["checkin_enabled"] = False
            with patch.object(checkin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                id=124, chat_id=-1002, sent_at=self.now + 100,
            ))) as sender:
                await checkin.run_checkin_scheduler(self.now + 100)
            self.assertEqual(-1002, sender.call_args.kwargs.get("target_chat_id"))
            await checkin.handle_sect_teach_reply(
                "传功玉简已记录！", self.now + 101,
                SimpleNamespace(id=124, chat_id=-1002, raw_text=checkin.CMD_SECT_TEACH),
                matched_family="sect_teach",
            )
        self.assertEqual(-1002, self.identity.get("last_sect_teach_chat_id"))
        self.assertEqual(-1002, self.identity.get("sect_teach_reply_chat_id"))
        self.assertEqual(124, self.identity["sect_teach_reply_to_msg_id"])

    async def test_legacy_teach_without_a_chat_does_not_guess_the_primary_group(self):
        self.identity["sect_teach_reply_chat_id"] = 0
        with (
            state_module.use_identity(self.identity_id),
            patch.object(checkin, "send_game_command", new=AsyncMock()) as sender,
        ):
            await checkin.run_checkin_scheduler(self.now)
        sender.assert_not_awaited()
        self.assertEqual(0, self.identity["next_sect_teach_time"])

    async def test_early_success_keeps_the_next_step_after_a_late_send_result(self):
        for result in (SimpleNamespace(id=124, chat_id=-1002), None):
            with self.subTest(result=result):
                self.identity.update({
                    "checkin_teach_count": 0,
                    "sect_teach_completed_message_keys": [],
                    "next_sect_teach_time": self.now,
                    "sect_teach_reply_to_msg_id": 123,
                    "sect_teach_reply_chat_id": -1002,
                })
                expected = {}

                async def send(*_args, **_kwargs):
                    await checkin.handle_sect_teach_reply(
                        "传功玉简已记录！", self.now + 1,
                        SimpleNamespace(id=124, chat_id=-1002, raw_text=checkin.CMD_SECT_TEACH),
                        matched_family="sect_teach",
                    )
                    expected["due"] = self.identity["next_sect_teach_time"]
                    return result

                with (
                    state_module.use_identity(self.identity_id),
                    patch.object(checkin, "send_game_command", new=AsyncMock(side_effect=send)),
                    patch.object(checkin, "send_audit_log", new=AsyncMock()),
                ):
                    await checkin.run_checkin_scheduler(self.now)
                self.assertEqual(1, self.identity["checkin_teach_count"])
                self.assertEqual(expected["due"], self.identity["next_sect_teach_time"])
                self.assertEqual(124, self.identity["sect_teach_reply_to_msg_id"])
                self.assertEqual(-1002, self.identity["sect_teach_reply_chat_id"])

    async def test_disabled_or_removed_identity_cannot_be_revived_by_a_send_receipt(self):
        for operation in ("disable", "remove"):
            with self.subTest(operation=operation):
                self.identity.update({
                    "sect_teach_enabled": True,
                    "next_sect_teach_time": self.now,
                    "sect_teach_reply_to_msg_id": 123,
                    "sect_teach_reply_chat_id": -1002,
                })

                async def send(*_args, **_kwargs):
                    if operation == "disable":
                        control._manual_disable_sect_teach_module_state()
                    else:
                        state_module.remove_identity(self.identity_id)
                    return SimpleNamespace(id=124, chat_id=-1002)

                with (
                    state_module.use_identity(self.identity_id),
                    patch.object(checkin, "send_game_command", new=AsyncMock(side_effect=send)),
                ):
                    await checkin.run_checkin_scheduler(self.now)
                if operation == "disable":
                    self.assertEqual(0, self.identity["last_sect_teach_msg_id"])
                    self.assertEqual(0, self.identity["sect_teach_reply_chat_id"])
                else:
                    self.assertFalse(state_module.has_identity(self.identity_id))

    async def test_success_is_counted_once_per_chat_and_command(self):
        with state_module.use_identity(self.identity_id):
            for chat_id in (-1002, -1002, -1003):
                await checkin.handle_sect_teach_reply(
                    "传功玉简已记录！", self.now,
                    SimpleNamespace(id=124, chat_id=chat_id, raw_text=checkin.CMD_SECT_TEACH),
                    matched_family="sect_teach",
                )
                if chat_id == -1002:
                    self.assertEqual(1, self.identity["checkin_teach_count"])
        self.assertEqual(2, self.identity["checkin_teach_count"])

    async def test_passive_and_direct_success_share_one_count_and_followup(self):
        for passive_first in (False, True):
            with self.subTest(passive_first=passive_first):
                self.identity.update({
                    "checkin_teach_count": 0,
                    "sect_teach_completed_message_keys": [],
                    "sect_teach_reply_to_msg_id": 123,
                    "sect_teach_reply_chat_id": -1002,
                    "next_sect_teach_time": self.now,
                })

                async def direct():
                    await checkin.handle_sect_teach_reply(
                        "传功玉简已记录！", self.now,
                        SimpleNamespace(id=124, chat_id=-1002, raw_text=checkin.CMD_SECT_TEACH),
                        matched_family="sect_teach",
                    )

                async def passive():
                    await passive_inbox._apply_checkin_passive(
                        "传功玉简已记录！", self.now, "sect_teach",
                        {"reply_to_msg_id": 124, "chat_id": -1002},
                    )

                with state_module.use_identity(self.identity_id):
                    if passive_first:
                        await passive()
                        await direct()
                    else:
                        await direct()
                        await passive()
                self.assertEqual(1, self.identity["checkin_teach_count"])
                self.assertEqual(124, self.identity["sect_teach_reply_to_msg_id"])
                self.assertEqual(-1002, self.identity["sect_teach_reply_chat_id"])
                self.assertGreater(self.identity["next_sect_teach_time"], self.now)

    async def _passive_teach(self, text, reply_id=124):
        return await passive_inbox._apply_checkin_passive(
            text, self.now, "sect_teach", {"reply_to_msg_id": reply_id, "chat_id": -1002},
        )

    async def test_real_success_wording_does_not_end_teaching_at_one_of_three(self):
        text = get_real_message_text(Path(__file__).parent / "fixtures" / "real_message_samples.json", "sect_teach.success")
        with state_module.use_identity(self.identity_id):
            self.assertTrue(await self._passive_teach(text))
        self.assertEqual(self.identity["checkin_teach_count"], 1)
        self.assertEqual(self.identity["sect_teach_reply_to_msg_id"], 124)
        self.assertGreater(self.identity["next_sect_teach_time"], self.now)

    async def test_terminal_success_cleans_and_notifies_once_in_either_delivery_order(self):
        baseline = copy.deepcopy(self.identity)
        for passive_first in (False, True):
            with self.subTest(passive_first=passive_first):
                self.identity.clear()
                self.identity.update(copy.deepcopy(baseline))
                self.identity.update(checkin_teach_count=2, sect_teach_completed_message_keys=[[-1002, 122], [-1002, 123]])
                reply = SimpleNamespace(id=124, chat_id=-1002, raw_text=checkin.CMD_SECT_TEACH)
                with (
                    state_module.use_identity(self.identity_id),
                    patch.object(checkin, "cleanup_checkin_chain_messages", new=AsyncMock()) as cleanup,
                    patch.object(checkin, "_notify_sect_teach_completed", new=AsyncMock()) as notify,
                ):
                    if passive_first:
                        await self._passive_teach("传功玉简已记录！")
                    await checkin.handle_sect_teach_reply("传功玉简已记录！", self.now, reply, matched_family="sect_teach")
                    await self._passive_teach("传功玉简已记录！")
                    await checkin.handle_sect_teach_reply("传功玉简已记录！", self.now, reply, matched_family="sect_teach")
                cleanup.assert_awaited_once()
                notify.assert_awaited_once()
                self.assertEqual(self.identity["checkin_teach_count"], 3)
                self.assertEqual(self.identity["next_sect_teach_time"], 0)
                self.assertEqual(self.identity["sect_teach_reply_to_msg_id"], 0)

    async def test_authoritative_teach_count_does_not_rewind_or_count_old_replies_twice(self):
        text = get_real_message_text(Path(__file__).parent / "fixtures" / "real_message_samples.json", "sect_teach.success")
        with state_module.use_identity(self.identity_id):
            await self._passive_teach(text.replace("1/3", "2/3"), 125)
            self.assertEqual(self.identity["checkin_teach_count"], 2)
            before = copy.deepcopy(self.identity)
            await self._passive_teach(text, 124)
            self.assertEqual(self.identity, before)

    async def test_completion_does_not_notify_for_a_replaced_identity_after_cleanup(self):
        self.identity.update(checkin_teach_count=2, sect_teach_completed_message_keys=[[-1002, 122], [-1002, 123]])
        state_module.ensure_identity_registered(self.identity_id + 1)

        async def cleanup():
            state_module.remove_identity(self.identity_id)
            state_module.ensure_identity_registered(self.identity_id)

        with (
            state_module.use_identity(self.identity_id),
            patch.object(checkin, "cleanup_checkin_chain_messages", new=AsyncMock(side_effect=cleanup)),
            patch.object(checkin, "_notify_sect_teach_completed", new=AsyncMock()) as notify,
        ):
            await checkin.handle_sect_teach_reply(
                "传功玉简已记录！", self.now,
                SimpleNamespace(id=124, chat_id=-1002, raw_text=checkin.CMD_SECT_TEACH),
                matched_family="sect_teach",
            )
        notify.assert_not_awaited()
        self.assertEqual(state_module.get_identity_state(self.identity_id)["checkin_teach_count"], 0)

    async def test_passive_terminal_dispatch_does_not_continue_in_another_owner(self):
        other_id = self.identity_id + 1
        other = state_module.ensure_identity_registered(other_id)
        other_before = copy.deepcopy(other)
        for change in ("removed", "replaced", "rebound"):
            with self.subTest(change=change):
                state_module.remove_identity(self.identity_id)
                owner = state_module.ensure_identity_registered(self.identity_id)
                state_module.set_identity_account(self.identity_id, 7010)
                owner.update(
                    sect_teach_enabled=True,
                    checkin_teach_day=checkin.get_checkin_day_key(self.now),
                    checkin_teach_count=2,
                    sect_teach_completed_message_keys=[[-1002, 122], [-1002, 123]],
                )

                async def cleanup():
                    if change == "rebound":
                        state_module.set_identity_account(self.identity_id, 7011)
                    else:
                        state_module.remove_identity(self.identity_id)
                        if change == "replaced":
                            state_module.ensure_identity_registered(self.identity_id)

                with (
                    patch.object(passive_inbox, "_mark_observed_passive_event", return_value=True),
                    patch.object(passive_inbox, "_record_passive_event"),
                    patch.object(passive_inbox, "close_action_guard_by_family") as close_guard,
                    patch.object(checkin, "cleanup_checkin_chain_messages", new=AsyncMock(side_effect=cleanup)),
                    patch.object(checkin, "_notify_sect_teach_completed", new=AsyncMock()) as notify,
                ):
                    handled = await passive_inbox.handle_passive_module_card(
                        "传功玉简已记录！", self.now,
                        {"send_as_id": self.identity_id, "family": "sect_teach", "root_msg_id": 124},
                        SimpleNamespace(id=125, chat_id=-1002),
                        event_type="message",
                    )
                self.assertTrue(handled)
                close_guard.assert_not_called()
                notify.assert_not_awaited()
                self.assertEqual(other, other_before)
                if change == "removed":
                    self.assertFalse(state_module.has_identity(self.identity_id))
                elif change == "replaced":
                    self.assertEqual(state_module.get_identity_state(self.identity_id)["checkin_teach_count"], 0)

    async def test_disabled_teaching_observes_completion_without_active_cleanup(self):
        self.identity.update(sect_teach_enabled=False, next_sect_teach_time=0, sect_teach_reply_to_msg_id=0)
        text = get_real_message_text(Path(__file__).parent / "fixtures" / "real_message_samples.json", "sect_teach.success")
        with (
            state_module.use_identity(self.identity_id),
            patch.object(checkin, "cleanup_checkin_chain_messages", new=AsyncMock()) as cleanup,
            patch.object(checkin, "_notify_sect_teach_completed", new=AsyncMock()) as notify,
            patch.object(checkin, "send_game_command", new=AsyncMock()) as sender,
        ):
            await self._passive_teach(text.replace("1/3", "3/3"))
        self.assertEqual(self.identity["checkin_teach_count"], 3)
        self.assertEqual(self.identity["next_sect_teach_time"], 0)
        cleanup.assert_not_awaited()
        notify.assert_not_awaited()
        sender.assert_not_awaited()

    async def test_passive_small_world_dispatch_preserves_owner_after_await(self):
        other_id = self.identity_id + 1
        other = state_module.ensure_identity_registered(other_id)
        before = copy.deepcopy(other)

        async def apply_result(*_args, **_kwargs):
            state_module.remove_identity(self.identity_id)
            return True

        with (
            patch.object(passive_inbox, "_mark_observed_passive_event", return_value=True),
            patch.object(passive_inbox, "_record_passive_event"),
            patch.object(passive_inbox, "_apply_small_world_passive", new=AsyncMock(side_effect=apply_result)),
            patch.object(passive_inbox, "close_action_guard_by_family") as close_guard,
        ):
            handled = await passive_inbox.handle_passive_module_card(
                "small world observation", self.now,
                {"send_as_id": self.identity_id, "family": "small_world_query", "root_msg_id": 124},
                SimpleNamespace(id=125, chat_id=-1002),
                event_type="message",
            )
        self.assertTrue(handled)
        close_guard.assert_not_called()
        self.assertEqual(other, before)
        self.assertFalse(state_module.has_identity(self.identity_id))

    async def test_already_done_reply_is_terminal_and_idempotent(self):
        self.identity["checkin_teach_count"] = 2
        with (
            state_module.use_identity(self.identity_id),
            patch.object(checkin, "cleanup_checkin_chain_messages", new=AsyncMock()) as cleanup,
            patch.object(checkin, "_notify_sect_teach_completed", new=AsyncMock()) as notify,
        ):
            self.assertTrue(await self._passive_teach("今日已经传功，暂不可再次传功。"))
            self.assertFalse(await self._passive_teach("今日已经传功，暂不可再次传功。"))
        self.assertEqual(self.identity["checkin_teach_count"], 2)
        self.assertEqual(self.identity["next_sect_teach_time"], 0)
        cleanup.assert_awaited_once()
        notify.assert_not_awaited()

    async def test_cleanup_does_not_continue_after_account_rebind(self):
        self.identity["checkin_cleanup_msg_ids"] = [[-1001, 123], [-1002, 123]]
        self.identity["my_msg_ids"] = {(-1001, 123): self.now, (-1002, 123): self.now}
        state_module.set_identity_account(self.identity_id, 7010)
        before = copy.deepcopy(self.identity)

        async def delete(_chat_id, _ids):
            state_module.set_identity_account(self.identity_id, 7011)

        async def run_rpc(coroutine, **_kwargs):
            return await coroutine

        client = SimpleNamespace(delete_messages=AsyncMock(side_effect=delete))
        with (
            state_module.use_identity(self.identity_id),
            patch.object(checkin, "is_auto_delete_sent_messages_enabled", return_value=True),
            patch.object(runtime, "_get_identity_client_with_account", return_value=(7010, client)),
            patch.object(runtime, "_run_account_rpc", side_effect=run_rpc),
        ):
            await checkin.cleanup_checkin_chain_messages()
        client.delete_messages.assert_awaited_once_with(-1001, [123])
        self.assertEqual(self.identity, before)

    async def test_unrecognized_checkin_reply_does_not_complete_or_reschedule(self):
        self.identity["checkin_enabled"] = True
        for text in ("点卯尚未开放，请稍后再试。", "服务维护中，请稍后再试。"):
            for passive in (False, True):
                with self.subTest(text=text, passive=passive), state_module.use_identity(self.identity_id):
                    before = copy.deepcopy(self.identity)
                    if passive:
                        handled = await passive_inbox._apply_checkin_passive(
                            text, self.now, "checkin", {"reply_to_msg_id": 123, "chat_id": -1002},
                        )
                    else:
                        handled = await checkin.handle_checkin_reply(
                            text, self.now,
                            SimpleNamespace(id=123, chat_id=-1002, raw_text=checkin.CMD_CHECKIN),
                            matched_family="checkin",
                        )
                    self.assertFalse(handled)
                    self.assertEqual(before, self.identity)

    async def test_checkin_duplicate_cannot_replace_a_newer_teach_step(self):
        self.identity.update(checkin_enabled=True, next_sect_teach_time=0, sect_teach_reply_to_msg_id=0)
        with state_module.use_identity(self.identity_id):
            reply = SimpleNamespace(id=123, chat_id=-1002, raw_text=checkin.CMD_CHECKIN)
            await checkin.handle_checkin_reply("点卯成功", self.now, reply, matched_family="checkin")
            await checkin.handle_sect_teach_reply(
                "传功玉简已记录！", self.now + 1,
                SimpleNamespace(id=124, chat_id=-1002, raw_text=checkin.CMD_SECT_TEACH),
                matched_family="sect_teach",
            )
            before = copy.deepcopy(self.identity)
            self.assertTrue(await checkin.handle_checkin_reply("点卯成功", self.now + 2, reply, matched_family="checkin"))
            self.assertEqual(before, self.identity)

    async def test_checkin_completion_queues_teaching_once_in_either_delivery_order(self):
        baseline = copy.deepcopy(self.identity)
        for passive_first in (False, True):
            with self.subTest(passive_first=passive_first), state_module.use_identity(self.identity_id):
                self.identity.clear()
                self.identity.update(copy.deepcopy(baseline))
                self.identity.update(checkin_enabled=True, next_sect_teach_time=0, sect_teach_reply_to_msg_id=0)
                reply = SimpleNamespace(id=123, chat_id=-1002, raw_text=checkin.CMD_CHECKIN)

                async def passive(now):
                    return await passive_inbox._apply_checkin_passive(
                        "点卯成功", now, "checkin", {"reply_to_msg_id": 123, "chat_id": -1002},
                    )

                if passive_first:
                    self.assertTrue(await passive(self.now))
                else:
                    await checkin.handle_checkin_reply("点卯成功", self.now, reply, matched_family="checkin")
                due = self.identity["next_sect_teach_time"]
                self.assertGreater(due, self.now)
                if passive_first:
                    await checkin.handle_checkin_reply("点卯成功", self.now + 1, reply, matched_family="checkin")
                else:
                    self.assertFalse(await passive(self.now + 1))
                self.assertEqual(due, self.identity["next_sect_teach_time"])
                self.assertEqual(123, self.identity["sect_teach_reply_to_msg_id"])

    async def test_old_day_completion_cannot_roll_back_todays_teaching(self):
        self.identity.update(checkin_enabled=True, checkin_teach_count=1)
        with state_module.use_identity(self.identity_id):
            before = copy.deepcopy(self.identity)
            await checkin.handle_checkin_reply(
                "点卯成功", self.now - 86400,
                SimpleNamespace(id=120, chat_id=-1002, raw_text=checkin.CMD_CHECKIN),
                matched_family="checkin",
            )
            self.assertEqual(before, self.identity)

    def test_completion_without_route_can_gain_an_exact_anchor_once(self):
        self.identity.update(next_sect_teach_time=0, sect_teach_reply_to_msg_id=0, sect_teach_reply_chat_id=0)
        with state_module.use_identity(self.identity_id):
            self.assertTrue(checkin.apply_checkin_completion(self.now))
            self.assertEqual(0, self.identity["next_sect_teach_time"])
            self.assertTrue(checkin.apply_checkin_completion(self.now + 1, 123, chat_id=-1002))
            self.assertGreater(self.identity["next_sect_teach_time"], self.now)
            before = copy.deepcopy(self.identity)
            self.assertFalse(checkin.apply_checkin_completion(self.now + 2, 123, chat_id=-1002))
            self.assertEqual(before, self.identity)

    async def test_unrecognized_teach_reply_is_not_terminal(self):
        text = "宗门传功暂未开放，请稍后再试。"
        with state_module.use_identity(self.identity_id):
            before = copy.deepcopy(self.identity)
            self.assertFalse(await checkin.handle_sect_teach_reply(
                text, self.now,
                SimpleNamespace(id=124, chat_id=-1002, raw_text=checkin.CMD_SECT_TEACH),
                matched_family="sect_teach",
            ))
            self.assertFalse(await passive_inbox._apply_checkin_passive(
                text, self.now, "sect_teach", {"reply_to_msg_id": 124, "chat_id": -1002},
            ))
            self.assertEqual(before, self.identity)

    async def test_checkin_early_success_wins_over_late_transport_failure(self):
        self.identity.update(checkin_enabled=True, sect_teach_enabled=False, next_checkin_time=self.now)
        expected = {}

        async def send(*_args, **_kwargs):
            await checkin.handle_checkin_reply(
                "点卯成功", self.now + 1,
                SimpleNamespace(id=123, chat_id=-1002, raw_text=checkin.CMD_CHECKIN),
                matched_family="checkin",
            )
            expected["due"] = self.identity["next_checkin_time"]
            return None

        with (
            state_module.use_identity(self.identity_id),
            patch.object(checkin, "send_game_command", new=AsyncMock(side_effect=send)),
            patch.object(checkin, "send_audit_log", new=AsyncMock()),
        ):
            await checkin.run_checkin_scheduler(self.now)
        self.assertEqual(expected["due"], self.identity["next_checkin_time"])

    async def test_checkin_receipt_cannot_enter_a_removed_identity(self):
        self.identity.update(checkin_enabled=True, sect_teach_enabled=False, next_checkin_time=self.now)

        async def send(*_args, **_kwargs):
            state_module.remove_identity(self.identity_id)
            return SimpleNamespace(id=123, chat_id=-1002, sent_at=self.now)

        with (
            state_module.use_identity(self.identity_id),
            patch.object(checkin, "send_game_command", new=AsyncMock(side_effect=send)),
        ):
            await checkin.run_checkin_scheduler(self.now)
        self.assertFalse(state_module.has_identity(self.identity_id))

    def test_control_resume_preserves_the_last_checkin_chat(self):
        for resume in (control._manual_enable_sect_teach_module_state, control._restore_sect_teach_runtime):
            with self.subTest(resume=resume.__name__), state_module.use_identity(self.identity_id):
                self.identity.update({
                    "next_sect_teach_time": 0,
                    "sect_teach_reply_to_msg_id": 0,
                    "sect_teach_reply_chat_id": 0,
                    "last_checkin_msg_id": 123,
                    "last_checkin_chat_id": -1002,
                    "last_checkin_done_day": checkin.get_checkin_day_key(self.now),
                })
                resume(self.now)
                self.assertEqual(123, self.identity["sect_teach_reply_to_msg_id"])
                self.assertEqual(-1002, self.identity["sect_teach_reply_chat_id"])

    def test_daily_reset_clears_routes_and_receipts(self):
        with state_module.use_identity(self.identity_id):
            self.identity.update(last_checkin_chat_id=-1001, last_sect_teach_chat_id=-1002)
            checkin.remember_sect_teach_completion(124, chat_id=-1002)
            checkin.reset_checkin_daily_state(self.now + 86400)
        self.assertEqual([], self.identity["sect_teach_completed_message_keys"])
        for key in ("last_checkin_chat_id", "last_sect_teach_chat_id", "sect_teach_reply_chat_id"):
            self.assertEqual(0, self.identity[key], key)

    async def test_cleanup_never_resolves_an_ambiguous_legacy_id_by_primary_group(self):
        self.identity["checkin_cleanup_msg_ids"] = [123]
        self.identity["my_msg_ids"] = {(-1001, 123): self.now, (-1002, 123): self.now}
        client = SimpleNamespace(delete_messages=AsyncMock())
        with (
            state_module.use_identity(self.identity_id),
            patch.object(checkin, "is_auto_delete_sent_messages_enabled", return_value=True),
            patch.object(runtime, "_get_identity_client_with_account", return_value=(1, client)),
        ):
            await checkin.cleanup_checkin_chain_messages()
        client.delete_messages.assert_not_awaited()
        self.assertEqual(2, len(self.identity["my_msg_ids"]))

    async def test_cleanup_uses_chat_keys_and_keeps_work_added_during_deletion(self):
        self.identity["checkin_cleanup_msg_ids"] = [[-1001, 123], [-1002, 123]]
        self.identity["my_msg_ids"] = {(-1001, 123): self.now, (-1002, 123): self.now}

        async def delete(chat_id, _ids):
            if chat_id == -1001:
                checkin.remember_checkin_cleanup_msg_id(125, chat_id=-1002)
                self.identity["my_msg_ids"][(-1002, 125)] = self.now + 1

        async def run_rpc(coroutine, **_kwargs):
            return await coroutine

        client = SimpleNamespace(delete_messages=AsyncMock(side_effect=delete))
        with (
            state_module.use_identity(self.identity_id),
            patch.object(checkin, "is_auto_delete_sent_messages_enabled", return_value=True),
            patch.object(runtime, "_get_identity_client_with_account", return_value=(1, client)),
            patch.object(runtime, "_run_account_rpc", new=AsyncMock(side_effect=run_rpc)),
        ):
            await checkin.cleanup_checkin_chain_messages()
        self.assertEqual([((-1001, [123]), {}), ((-1002, [123]), {})], client.delete_messages.call_args_list)
        self.assertEqual([[-1002, 125]], self.identity["checkin_cleanup_msg_ids"])
        self.assertEqual({(-1002, 125): self.now + 1}, self.identity["my_msg_ids"])


if __name__ == "__main__":
    unittest.main()
