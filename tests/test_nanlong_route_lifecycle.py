import asyncio
import copy
import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import message_log_recovery, runtime, state as state_module
from model.features import nanlong


class NanlongRouteLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.snapshot = copy.deepcopy(state_module._meta_state)
        self.identity_id = 995201
        self.now = 1_700_000_000.0
        self.identity = state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(
            self.identity_id, username="NanlongRoute", enabled=True,
            nanlong_choice=nanlong.NANLONG_CHOICE_EXCHANGE_FABAO,
        )
        self.identity.update(nanlong_enabled=True, concubine_name="南宫婉")
        self.prompt_text = (
            "南陇侯望向 @NanlongRoute，示意你做出抉择。\n你有 3 分钟。\n"
            "回复本消息 .交换法宝\n回复本消息 .交换功法\n回复本消息 .拒绝交易"
        )
        self.trade_text = "【天机异闻·南陇侯的交易】@NanlongRoute 已完成交易。"
        for target, kwargs in (
            ("save_state", {}), ("mark_dirty", {}),
            ("send_audit_log", {"new": AsyncMock()}),
        ):
            patcher = patch.object(nanlong, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in (("_reply_chain_tracker", {}), ("_bot_waiting_since", 0.0)):
            patcher = patch.object(runtime, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self.snapshot)

    def event(self, msg_id=123, chat_id=-1001, *, at=None, reply_id=0):
        return SimpleNamespace(
            id=msg_id, chat_id=chat_id,
            date=datetime.fromtimestamp(self.now if at is None else at, timezone.utc),
            reply_to=SimpleNamespace(reply_to_msg_id=reply_id),
        )

    async def seed_prompt(self, msg_id=123, chat_id=-1001, *, now=None):
        now = self.now if now is None else now
        with state_module.use_identity(self.identity_id):
            await nanlong.handle_nanlong_prompt(self.prompt_text, now, self.event(msg_id, chat_id, at=now))
        self.identity["nanlong_reply_due_at"] = now

    def seed_exchange(self, *, protected=False):
        self.identity.update(
            nanlong_reply_to_msg_id=123, nanlong_reply_chat_id=-1001,
            next_nanlong_time=self.now + 120, nanlong_reply_due_at=self.now + 30,
            nanlong_prompt_at=self.now - 10, nanlong_last_sent_at=self.now - 1,
            nanlong_last_msg_id=124, nanlong_last_chat_id=-1001,
            nanlong_last_command=nanlong.CMD_NANLONG_EXCHANGE_FABAO,
            nanlong_protect_phase="exchange_pending" if protected else "",
        )
        if protected:
            self.identity["concubine_name"] = "墨彩环"

    @contextmanager
    def logged_results(self, entries, *, groups=(-1001, -1002)):
        state_module.set_game_bot_ids([7001])
        state_module.set_game_group_route_config({
            "enabled": True, "primary_group_id": groups[0],
            "backup_group_ids": list(groups[1:]),
        })
        with tempfile.TemporaryDirectory(prefix="nanlong-recovery-") as directory:
            def append_entry(entry, timestamp):
                local_time = datetime.fromtimestamp(timestamp, message_log_recovery.TZ_LOCAL)
                payload = {"ts": local_time.strftime("%Y-%m-%d %H:%M:%S UTC+8"), **entry}
                path = Path(directory) / f"{local_time.date().isoformat()}.log"
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

            for entry, timestamp in entries:
                append_entry(entry, timestamp)
            with patch.object(message_log_recovery, "MESSAGES_DIR", directory):
                yield append_entry

    def result_entry(self, *, chat_id=-1002, reply_id=0, text=None, **overrides):
        return {
            "event_type": "message", "message_id": 900, "chat_id": chat_id,
            "sender_id": 7001, "reply_to_msg_id": reply_id,
            "text": self.trade_text if text is None else text, **overrides,
        }

    async def test_prompt_and_scheduled_reply_keep_the_original_chat(self):
        await self.seed_prompt(chat_id=-1002)
        with state_module.use_identity(self.identity_id), patch.object(
            nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                id=124, chat_id=-1002, sent_at=self.now + 1,
            )),
        ) as sender:
            await nanlong.run_nanlong_scheduler(self.now + 1)
        self.assertEqual(-1002, sender.call_args.kwargs.get("target_chat_id"))
        self.assertEqual(-1002, self.identity.get("nanlong_reply_chat_id"))
        self.assertEqual(-1002, self.identity.get("nanlong_last_chat_id"))

    async def test_unknown_prompt_chat_cannot_send_to_primary_group(self):
        await self.seed_prompt(chat_id=0)
        with state_module.use_identity(self.identity_id), patch.object(
            nanlong, "send_game_command", new=AsyncMock(),
        ) as sender:
            await nanlong.run_nanlong_scheduler(self.now + 1)
        sender.assert_not_awaited()

    async def test_scheduler_reentry_during_send_does_not_repeat_the_rpc(self):
        baseline = copy.deepcopy(self.identity)
        for step in ("exchange", "place", "reject"):
            with self.subTest(step=step):
                self.identity.clear()
                self.identity.update(copy.deepcopy(baseline))
                await self.seed_prompt()
                if step == "place":
                    self.identity["concubine_name"] = "墨彩环"
                with state_module.use_identity(self.identity_id):
                    state_module.set_nanlong_choice(self.identity_id, "reject" if step == "reject" else "exchange_fabao")
                    calls = 0

                    async def send(*_args, **_kwargs):
                        nonlocal calls
                        calls += 1
                        if calls == 1:
                            await nanlong.run_nanlong_scheduler(self.now + 1)
                        return SimpleNamespace(id=124 + calls, chat_id=-1001, sent_at=self.now + 2)

                    with (
                        patch.object(nanlong, "_get_nanlong_cave_status", return_value=nanlong.NANLONG_CAVE_STATUS_AVAILABLE),
                        patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)),
                    ):
                        await nanlong.run_nanlong_scheduler(self.now)
                self.assertEqual(1, calls)

    async def test_duplicate_place_confirmation_cannot_send_exchange_twice(self):
        self.seed_exchange(protected=True)
        self.identity.update(nanlong_protect_phase="place_pending", nanlong_last_command=nanlong.CMD_CONCUBINE_PLACE)
        reply = SimpleNamespace(id=124, chat_id=-1001, raw_text=nanlong.CMD_CONCUBINE_PLACE)
        calls = 0

        async def send(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                await nanlong.handle_nanlong_reply(
                    "你已将道侣【墨彩环】安置在洞府的藏娇阁中。", self.now + 1, reply, matched_family="nanlong",
                )
            return SimpleNamespace(id=124 + calls, chat_id=-1001, sent_at=self.now + 2)

        with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)):
            await nanlong.handle_nanlong_reply(
                "你已将道侣【墨彩环】安置在洞府的藏娇阁中。", self.now, reply, matched_family="nanlong",
            )
        self.assertEqual(1, calls)

    async def test_unknown_send_does_not_automatically_retry(self):
        await self.seed_prompt()
        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now + 1),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=None)) as sender,
            patch.object(nanlong, "classify_game_send_block", create=True, return_value={"status": "unknown", "code": "send_timeout"}),
        ):
            await nanlong.run_nanlong_scheduler(self.now)
            await nanlong.run_nanlong_scheduler(self.now + 90)
        sender.assert_awaited_once()
        self.assertEqual(0, self.identity["nanlong_reply_due_at"])
        self.assertIn("未知", self.identity["nanlong_last_error"])

    async def test_failed_pre_send_save_cannot_cross_transport(self):
        await self.seed_prompt()
        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "save_state", return_value=False),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            await nanlong.run_nanlong_scheduler(self.now)
        sender.assert_not_awaited()

    async def test_send_checkpoint_precedes_transport_and_receipt_is_saved_afterward(self):
        await self.seed_prompt()
        snapshots = []

        def save():
            snapshots.append({key: self.identity[key] for key in ("nanlong_last_msg_id", "nanlong_reply_due_at", "nanlong_last_command")})
            return True

        async def send(*_args, **_kwargs):
            self.assertEqual([{
                "nanlong_last_msg_id": 0, "nanlong_reply_due_at": 0,
                "nanlong_last_command": nanlong.CMD_NANLONG_EXCHANGE_FABAO,
            }], snapshots)
            return SimpleNamespace(id=124, chat_id=-1001, sent_at=self.now + 1)

        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "save_state", side_effect=save),
            patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)),
        ):
            await nanlong.run_nanlong_scheduler(self.now)
        self.assertEqual(2, len(snapshots))
        self.assertEqual(124, snapshots[-1]["nanlong_last_msg_id"])
        self.assertGreater(snapshots[-1]["nanlong_reply_due_at"], self.now)

    async def test_definitely_unsent_command_can_retry_without_consuming_the_prompt(self):
        await self.seed_prompt()
        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now + 1),
            patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=[None, SimpleNamespace(id=124, chat_id=-1001, sent_at=self.now + 60)])) as sender,
            patch.object(nanlong, "classify_game_send_block", return_value={"status": "unsent", "code": "send_queue_timeout"}),
        ):
            await nanlong.run_nanlong_scheduler(self.now)
            self.assertGreater(self.identity["nanlong_reply_due_at"], self.now)
            await nanlong.run_nanlong_scheduler(self.now + 60)
        self.assertEqual(2, sender.await_count)
        self.assertEqual(124, self.identity["nanlong_last_msg_id"])

    async def test_unsent_place_does_not_downgrade_to_unprotected_exchange(self):
        await self.seed_prompt()
        self.identity["concubine_name"] = "墨彩环"
        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now + 1),
            patch.object(nanlong, "_get_nanlong_cave_status", return_value=nanlong.NANLONG_CAVE_STATUS_AVAILABLE),
            patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=[None, SimpleNamespace(id=124, chat_id=-1001, sent_at=self.now + 60)])) as sender,
            patch.object(nanlong, "classify_game_send_block", return_value={"status": "unsent"}),
        ):
            await nanlong.run_nanlong_scheduler(self.now)
            await nanlong.run_nanlong_scheduler(self.now + 60)
        self.assertEqual([nanlong.CMD_CONCUBINE_PLACE] * 2, [call.args[0] for call in sender.await_args_list])

    async def test_unsent_exchange_keeps_the_confirmed_place_protection(self):
        self.seed_exchange(protected=True)
        self.identity.update(nanlong_protect_phase="place_pending", nanlong_last_command=nanlong.CMD_CONCUBINE_PLACE)
        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now + 1),
            patch.object(nanlong, "_get_nanlong_cave_status", return_value=nanlong.NANLONG_CAVE_STATUS_AVAILABLE),
            patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=[None, SimpleNamespace(id=125, chat_id=-1001, sent_at=self.now + 60)])) as sender,
            patch.object(nanlong, "classify_game_send_block", return_value={"status": "unsent"}),
        ):
            await nanlong.handle_nanlong_reply(
                "你已将道侣【墨彩环】安置在洞府的藏娇阁中。", self.now,
                SimpleNamespace(id=124, chat_id=-1001, raw_text=nanlong.CMD_CONCUBINE_PLACE), matched_family="nanlong",
            )
            await nanlong.run_nanlong_scheduler(self.now + 60)
        self.assertEqual([nanlong.CMD_NANLONG_EXCHANGE_FABAO] * 2, [call.args[0] for call in sender.await_args_list])
        self.assertEqual("exchange_pending", self.identity["nanlong_protect_phase"])

    async def test_unsent_confirmation_retry_retains_the_previous_receipt(self):
        self.seed_exchange()
        self.identity["nanlong_reply_due_at"] = self.now
        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=None)),
            patch.object(nanlong, "classify_game_send_block", return_value={"status": "unsent"}),
        ):
            await nanlong.run_nanlong_scheduler(self.now)
        self.assertEqual(124, self.identity["nanlong_last_msg_id"])
        self.assertEqual(self.now - 1, self.identity["nanlong_last_sent_at"])

    async def test_changing_choice_does_not_rearm_an_unresolved_send(self):
        await self.seed_prompt()
        self.identity.update(nanlong_reply_due_at=0, nanlong_last_command=nanlong.CMD_NANLONG_EXCHANGE_FABAO)
        with state_module.use_identity(self.identity_id):
            await nanlong.apply_nanlong_choice("exchange_gongfa", self.now + 1)
        self.assertEqual(0, self.identity["nanlong_reply_due_at"])

    async def test_choice_change_after_dispatch_cannot_discard_the_real_receipt(self):
        await self.seed_prompt()

        async def send(*_args, **_kwargs):
            await nanlong.apply_nanlong_choice("exchange_gongfa", self.now + 1)
            return SimpleNamespace(id=124, chat_id=-1001, sent_at=self.now + 2)

        with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)):
            await nanlong.run_nanlong_scheduler(self.now)
        self.assertEqual(124, self.identity["nanlong_last_msg_id"])
        self.assertEqual(nanlong.CMD_NANLONG_EXCHANGE_FABAO, self.identity["nanlong_last_command"])

    async def test_early_unthreaded_trade_result_is_replayed_as_receipt_arrives(self):
        await self.seed_prompt()

        async def send(command, **kwargs):
            handled = await nanlong.handle_nanlong_result_broadcast(
                self.trade_text, self.now + 1, self.event(900, -1002, at=self.now + 1),
            )
            self.assertFalse(handled)
            return self.finalize_receipt(command, kwargs, detached=False)

        with (
            self.logged_results([(self.result_entry(), self.now + 1)]),
            state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now),
            patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)) as sender,
        ):
            await nanlong.run_nanlong_scheduler(self.now)
        sender.assert_awaited_once()
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])
        self.assertEqual(0, self.identity["nanlong_reply_to_msg_id"])
        self.assertEqual("", self.identity["nanlong_last_error"])

    async def test_rebind_while_sending_cannot_install_receipt_on_new_account(self):
        await self.seed_prompt()
        state_module.set_identity_account(self.identity_id, 7101)

        async def send(*_args, **_kwargs):
            state_module.set_identity_account(self.identity_id, 7102)
            return SimpleNamespace(id=124, chat_id=-1001, sent_at=self.now + 1)

        with state_module.use_identity(self.identity_id), patch.object(
            nanlong, "send_game_command", new=AsyncMock(side_effect=send),
        ):
            await nanlong.run_nanlong_scheduler(self.now)
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])

    def finalize_receipt(self, command, kwargs, *, msg_id=124, sent_at=None, send_started_at=None, detached=True):
        receipt = {
            "message": None, "detached": detached, "send_as_id": self.identity_id,
            "command": command,
            "finalize_kwargs": {
                "send_as_id": self.identity_id, "reply_to": kwargs.get("reply_to"), "track": False,
                "game_group_id": kwargs["target_chat_id"], "topic_id": 0,
                "send_started_at": self.now if send_started_at is None else send_started_at,
                "send_intent": {key: kwargs.get(key, "") for key in ("source_module", "chain_id", "op_id")},
            },
        }
        with (
            patch.object(runtime, "_append_sent_message_log"),
            patch.object(runtime, "_notify_game_command_sent_observers"),
            patch.object(runtime, "note_game_command_sent"),
        ):
            return runtime._finalize_game_send_receipt(
                receipt, msg_id=msg_id, sent_at=self.now + 10 if sent_at is None else sent_at,
            )

    async def seed_detached_exchange(self, *, step="exchange", cancelled=False):
        await self.seed_prompt()
        state_module.set_identity_account(self.identity_id, 7101)
        if step in {"place", "recall"}:
            self.identity["concubine_name"] = "墨彩环"
        if step == "recall":
            self.seed_exchange(protected=True)
        elif step == "reject":
            state_module.set_nanlong_choice(self.identity_id, "reject")
        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now),
            patch.object(nanlong, "_get_nanlong_cave_status", return_value=nanlong.NANLONG_CAVE_STATUS_AVAILABLE),
            patch.object(nanlong, "send_game_command", new=AsyncMock(
                return_value=None, side_effect=asyncio.CancelledError if cancelled else None,
            )) as sender,
            patch.object(nanlong, "classify_game_send_block", return_value={"status": "unknown"}),
        ):
            action = (
                nanlong._send_nanlong_recall_after_trade(self.now) if step == "recall"
                else nanlong.run_nanlong_scheduler(self.now)
            )
            if cancelled:
                with self.assertRaises(asyncio.CancelledError):
                    await action
            else:
                await action
        self.finalize_receipt(sender.call_args.args[0], sender.call_args.kwargs)
        return self.identity["pending_tasks"][(-1001, 124)]

    async def test_unthreaded_result_before_actual_dispatch_cannot_close_queued_exchange(self):
        for detached in (False, True):
            with self.subTest(detached=detached):
                with state_module.use_identity(self.identity_id):
                    nanlong.clear_nanlong_state()
                self.identity["nanlong_last_prompt_key"] = ""
                await self.seed_prompt()

                async def send(command, **kwargs):
                    msg = self.finalize_receipt(
                        command, kwargs, sent_at=self.now + 20,
                        send_started_at=self.now + 10, detached=detached,
                    )
                    return None if detached else msg

                with (
                    self.logged_results([(self.result_entry(), self.now + 5)]),
                    state_module.use_identity(self.identity_id),
                    patch.object(nanlong.time, "time", return_value=self.now),
                    patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)) as sender,
                    patch.object(nanlong, "classify_game_send_block", return_value={"status": "unknown"}),
                ):
                    await nanlong.run_nanlong_scheduler(self.now)
                    await nanlong.run_nanlong_scheduler(self.now + 21)
                sender.assert_awaited_once()
                self.assertEqual(124, self.identity["nanlong_last_msg_id"])
                self.assertEqual(123, self.identity["nanlong_reply_to_msg_id"])
                self.assertEqual(self.now + 10, self.identity["nanlong_last_sent_at"])

    async def test_complete_protected_chain_replays_each_early_reply_once(self):
        await self.seed_prompt()
        self.identity["concubine_name"] = "墨彩环"
        clock = self.now
        commands = []
        results = []
        place_text = "你已将道侣【墨彩环】安置在洞府的藏娇阁中。"
        recall_text = "你已将道侣【墨彩环】从藏娇阁中召回。"

        async def send(command, **kwargs):
            nonlocal clock
            commands.append(command)
            msg_id = 123 + len(commands)
            dispatched_at = clock
            clock += 1
            text = {nanlong.CMD_CONCUBINE_PLACE: place_text, nanlong.CMD_CONCUBINE_RECALL: recall_text}.get(command, self.trade_text)
            direct = command != nanlong.CMD_NANLONG_EXCHANGE_FABAO
            entry = self.result_entry(
                message_id=900 + msg_id, chat_id=-1001 if direct else -1002,
                reply_id=msg_id if direct else 0, text=text,
            )
            append_entry(entry, clock)
            results.append((entry, clock))
            if direct:
                handled = await nanlong.handle_nanlong_reply(
                    text, clock, SimpleNamespace(id=msg_id, chat_id=-1001), matched_family="nanlong",
                )
            else:
                handled = await nanlong.handle_nanlong_result_broadcast(text, clock, self.event(900 + msg_id, -1002, at=clock))
            self.assertFalse(handled)
            clock += 5
            return self.finalize_receipt(
                command, kwargs, msg_id=msg_id, sent_at=clock, send_started_at=dispatched_at, detached=False,
            )

        with (
            self.logged_results([]) as append_entry,
            state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", side_effect=lambda: clock),
            patch.object(nanlong, "_get_nanlong_cave_status", return_value=nanlong.NANLONG_CAVE_STATUS_AVAILABLE),
            patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)) as sender,
        ):
            await nanlong.run_nanlong_scheduler(clock)
            for entry, timestamp in results:
                append_entry(dict(entry, event_type="edit"), timestamp + 1)
            await nanlong.run_nanlong_scheduler(clock + 100)
        self.assertEqual([nanlong.CMD_CONCUBINE_PLACE, nanlong.CMD_NANLONG_EXCHANGE_FABAO, nanlong.CMD_CONCUBINE_RECALL], commands)
        self.assertEqual(3, sender.await_count)
        self.assertEqual(0, self.identity["nanlong_reply_to_msg_id"])
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])
        self.assertEqual("", self.identity["nanlong_protect_phase"])
        self.assertEqual("", self.identity["nanlong_last_error"])

    async def test_detached_place_and_recall_recover_direct_results(self):
        baseline = copy.deepcopy(self.identity)
        for step in ("place", "recall"):
            for cancelled in (False, True):
                with self.subTest(step=step, cancelled=cancelled):
                    self.identity.clear()
                    self.identity.update(copy.deepcopy(baseline))
                    await self.seed_detached_exchange(step=step, cancelled=cancelled)
                    text = "你已将道侣【墨彩环】安置在洞府的藏娇阁中。" if step == "place" else "你已将道侣【墨彩环】从藏娇阁中召回。"
                    entries = [(self.result_entry(chat_id=-1001, reply_id=124, text=text), self.now + 1)]
                    with (
                        self.logged_results(entries),
                        state_module.use_identity(self.identity_id),
                        patch.object(nanlong.time, "time", return_value=self.now + 11),
                        patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                            id=125, chat_id=-1001, sent_at=self.now + 12,
                        ))) as sender,
                    ):
                        await nanlong.run_nanlong_scheduler(self.now + 11)
                        await nanlong.run_nanlong_scheduler(self.now + 13)
                    if step == "place":
                        sender.assert_awaited_once()
                        self.assertEqual(nanlong.CMD_NANLONG_EXCHANGE_FABAO, sender.call_args.args[0])
                        self.assertEqual("exchange_pending", self.identity["nanlong_protect_phase"])
                    else:
                        sender.assert_not_awaited()
                        self.assertEqual("", self.identity["nanlong_protect_phase"])
                    self.assertNotIn((-1001, 124), self.identity["pending_tasks"])

    async def test_cancelled_exchange_is_reconciled_by_direct_reply_without_resend(self):
        await self.seed_detached_exchange(cancelled=True)
        with state_module.use_identity(self.identity_id), patch.object(
            nanlong, "send_game_command", new=AsyncMock(),
        ) as sender:
            self.assertTrue(await nanlong.handle_nanlong_reply(
                self.trade_text, self.now + 11, SimpleNamespace(id=124, chat_id=-1001), matched_family="nanlong",
            ))
            self.assertFalse(await nanlong.handle_nanlong_reply(
                self.trade_text, self.now + 12, SimpleNamespace(id=124, chat_id=-1001), matched_family="nanlong",
            ))
        sender.assert_not_awaited()
        self.assertNotIn((-1001, 124), self.identity["pending_tasks"])
        self.assertEqual(0, self.identity["nanlong_reply_to_msg_id"])

    async def test_disabled_or_cleared_operation_cannot_adopt_a_detached_receipt(self):
        baseline = copy.deepcopy(self.identity)
        for change in ("module_disabled", "identity_disabled", "cleared"):
            with self.subTest(change=change):
                self.identity.clear()
                self.identity.update(copy.deepcopy(baseline))
                await self.seed_detached_exchange()
                with state_module.use_identity(self.identity_id):
                    if change == "module_disabled":
                        self.identity["nanlong_enabled"] = False
                    elif change == "identity_disabled":
                        state_module.update_send_as_profile(self.identity_id, enabled=False)
                    else:
                        nanlong.clear_nanlong_state(persist=True)
                    with patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender:
                        await nanlong.run_nanlong_scheduler(self.now + 11)
                sender.assert_not_awaited()
                self.assertEqual(0, self.identity["nanlong_last_msg_id"])
                self.assertIn((-1001, 124), self.identity["pending_tasks"])
                state_module.update_send_as_profile(self.identity_id, enabled=True)

    async def test_detached_reject_receipt_closes_once_without_retry(self):
        await self.seed_detached_exchange(step="reject")
        with state_module.use_identity(self.identity_id), patch.object(
            nanlong, "send_game_command", new=AsyncMock(),
        ) as sender:
            await nanlong.run_nanlong_scheduler(self.now + 11)
            await nanlong.run_nanlong_scheduler(self.now + 100)
        sender.assert_not_awaited()
        self.assertEqual(0, self.identity["nanlong_reply_to_msg_id"])
        self.assertNotIn((-1001, 124), self.identity["pending_tasks"])

    async def test_detached_receipt_recovers_earlier_cross_group_result_without_send(self):
        await self.seed_detached_exchange()
        self.identity["pending_tasks"][(-1002, 124)] = {"cmd": ".unrelated", "chat_id": -1002}
        with (
            self.logged_results([(self.result_entry(), self.now + 1)]),
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            await nanlong.run_nanlong_scheduler(self.now + 11)
        sender.assert_not_awaited()
        self.assertNotIn((-1001, 124), self.identity["pending_tasks"])
        self.assertIn((-1002, 124), self.identity["pending_tasks"])
        self.assertEqual(0, self.identity["nanlong_reply_to_msg_id"])

    async def test_detached_receipt_without_result_is_never_confirmation_retried(self):
        baseline = copy.deepcopy(self.identity)
        for step in ("exchange", "place", "recall"):
            with self.subTest(step=step):
                self.identity.clear()
                self.identity.update(copy.deepcopy(baseline))
                await self.seed_detached_exchange(step=step)
                with (
                    self.logged_results([]),
                    state_module.use_identity(self.identity_id),
                    patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
                    patch.object(nanlong, "send_audit_log", new=AsyncMock()) as audit,
                ):
                    await nanlong.run_nanlong_scheduler(self.now + 11)
                    self.assertEqual(124, self.identity["nanlong_last_msg_id"])
                    await nanlong.apply_nanlong_choice("exchange_gongfa", self.now + 71)
                    for offset in (71, 140, 210):
                        await nanlong.run_nanlong_scheduler(self.now + offset)
                sender.assert_not_awaited()
                audit.assert_awaited_once()
                self.assertEqual(124, self.identity["nanlong_last_msg_id"])
                self.assertIn((-1001, 124), self.identity["pending_tasks"])
                self.assertIn("待核对", self.identity["nanlong_last_error"])

    async def test_detached_receipt_must_match_original_owner_prompt_step_and_attempt(self):
        baseline = copy.deepcopy(self.identity)
        cases = (
            {"source_module": "concubine"}, {"chain_id": "old-prompt"},
            {"op_id": "old-attempt"}, {"reply_to_msg_id": 456},
            {"cmd": nanlong.CMD_NANLONG_EXCHANGE_GONGFA},
            {"sent_at": self.now - 10}, {"sent_at": self.now + 1000},
            {"send_started_at": self.now - 10}, {"send_started_at": self.now + 1000},
            {"send_started_at": "nan"},
            {"send_caller_detached": "true"}, {"chat_id": -1002},
            {"account_id": 7102}, {"duplicate": True},
        )
        for change in cases:
            with self.subTest(change=change):
                self.identity.clear()
                self.identity.update(copy.deepcopy(baseline))
                item = await self.seed_detached_exchange()
                if "account_id" in change:
                    state_module.set_identity_account(self.identity_id, change["account_id"])
                elif "duplicate" in change:
                    self.identity["pending_tasks"][(-1001, 125)] = dict(item)
                else:
                    item.update(change)
                with state_module.use_identity(self.identity_id), patch.object(
                    nanlong, "send_game_command", new=AsyncMock(),
                ) as sender:
                    await nanlong.run_nanlong_scheduler(self.now + 11)
                sender.assert_not_awaited()
                self.assertEqual(0, self.identity["nanlong_last_msg_id"])

    async def test_unknown_reply_is_not_treated_as_a_terminal_result(self):
        for phase, command in (("", nanlong.CMD_NANLONG_EXCHANGE_FABAO), ("place_pending", nanlong.CMD_CONCUBINE_PLACE), ("recall_pending", nanlong.CMD_CONCUBINE_RECALL)):
            with self.subTest(phase=phase), state_module.use_identity(self.identity_id):
                self.seed_exchange(protected=True)
                self.identity.update(nanlong_protect_phase=phase, nanlong_last_command=command)
                self.assertFalse(await nanlong.handle_nanlong_reply(
                    "暂时无法处理，请稍后再试。", self.now,
                    SimpleNamespace(id=124, chat_id=-1001, raw_text=command), matched_family="nanlong",
                ))

    async def test_duplicate_prompt_does_not_reset_inflight_exchange(self):
        await self.seed_prompt()
        self.seed_exchange(protected=True)
        before = copy.deepcopy(self.identity)
        with state_module.use_identity(self.identity_id):
            await nanlong.handle_nanlong_prompt(self.prompt_text, self.now + 20, self.event())
        self.assertEqual(before, self.identity)

    async def test_completed_prompt_is_not_requeued_by_a_late_edit(self):
        await self.seed_prompt()
        self.seed_exchange()
        with state_module.use_identity(self.identity_id):
            await nanlong.handle_nanlong_result_broadcast(self.trade_text, self.now + 1, self.event(125, -1002, at=self.now + 1))
            await nanlong.handle_nanlong_prompt(self.prompt_text, self.now + 2, self.event())
        self.assertEqual(0, self.identity["nanlong_reply_to_msg_id"])

    async def test_prompt_replacement_survives_awaited_override_notification(self):
        await self.seed_prompt()

        async def audit(*_args, **_kwargs):
            nanlong._set_nanlong_pending(789, self.now + 300, self.now + 20)

        with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_audit_log", new=AsyncMock(side_effect=audit)):
            await nanlong.handle_nanlong_prompt(self.prompt_text, self.now + 10, self.event(456, at=self.now + 10))
        self.assertEqual(789, self.identity["nanlong_reply_to_msg_id"])

    async def test_late_send_receipt_cannot_replace_a_new_prompt(self):
        await self.seed_prompt()

        async def send(*_args, **_kwargs):
            nanlong._set_nanlong_pending(456, self.now + 180, self.now + 1, chat_id=-1002)
            return SimpleNamespace(id=124, chat_id=-1001, sent_at=self.now)

        with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)):
            await nanlong.run_nanlong_scheduler(self.now)
        self.assertEqual(456, self.identity["nanlong_reply_to_msg_id"])
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])

    async def test_removed_identity_is_not_reentered_after_send(self):
        await self.seed_prompt()
        other = state_module.ensure_identity_registered(self.identity_id + 1)
        before = copy.deepcopy(other)

        async def send(*_args, **_kwargs):
            state_module.remove_identity(self.identity_id)
            return SimpleNamespace(id=124, chat_id=-1001, sent_at=self.now)

        with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)):
            await nanlong.run_nanlong_scheduler(self.now)
        self.assertFalse(state_module.has_identity(self.identity_id))
        self.assertEqual(before, other)

    async def test_disable_during_pre_send_audit_prevents_exchange(self):
        await self.seed_prompt()
        self.identity["concubine_name"] = "墨彩环"

        async def audit(*_args, **_kwargs):
            self.identity["nanlong_enabled"] = False

        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "_get_nanlong_cave_status", return_value=nanlong.NANLONG_CAVE_STATUS_EMPTY),
            patch.object(nanlong, "send_audit_log", new=AsyncMock(side_effect=audit)),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            await nanlong.run_nanlong_scheduler(self.now)
        sender.assert_not_awaited()

    async def test_same_id_reply_in_another_chat_cannot_trigger_exchange(self):
        self.seed_exchange(protected=True)
        self.identity.update(
            nanlong_protect_phase="place_pending", nanlong_last_command=nanlong.CMD_CONCUBINE_PLACE,
            nanlong_place_msg_id=124,
        )
        with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender:
            handled = await nanlong.handle_nanlong_reply(
                "你已将道侣【墨彩环】安置在洞府的藏娇阁中。", self.now,
                SimpleNamespace(id=124, chat_id=-1002, raw_text=nanlong.CMD_CONCUBINE_PLACE), matched_family="nanlong",
            )
        self.assertFalse(handled)
        sender.assert_not_awaited()

    async def test_recovery_without_original_chat_does_not_guess_primary_group(self):
        self.seed_exchange()
        self.identity["nanlong_last_chat_id"] = 0
        with state_module.use_identity(self.identity_id), patch.object(nanlong, "iter_message_log_entries_between", return_value=[]) as recovery:
            await nanlong._recover_nanlong_pending_reply_from_log(self.now)
        recovery.assert_not_called()

    async def test_current_unthreaded_result_can_arrive_in_another_group(self):
        self.seed_exchange()
        with state_module.use_identity(self.identity_id):
            handled = await nanlong.handle_nanlong_result_broadcast(self.trade_text, self.now, self.event(900, -1002))
        self.assertTrue(handled)
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])

    async def test_log_recovery_completes_cross_group_result_without_resending(self):
        self.seed_exchange()
        self.identity["nanlong_reply_due_at"] = self.now
        with (
            self.logged_results([(self.result_entry(), self.now)]),
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            await nanlong.run_nanlong_scheduler(self.now + 1)
        sender.assert_not_awaited()
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])
        self.assertEqual("", self.identity["nanlong_last_error"])

    async def test_logged_trade_result_can_be_recovered_after_prompt_deadline(self):
        self.seed_exchange()
        with (
            self.logged_results([(self.result_entry(), self.now)]),
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            await nanlong.run_nanlong_scheduler(self.now + 121)
        sender.assert_not_awaited()
        self.assertEqual("", self.identity["nanlong_last_error"])
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])

    async def test_recovery_ignores_player_copy_of_a_place_success(self):
        self.seed_exchange(protected=True)
        self.identity.update(nanlong_protect_phase="place_pending", nanlong_last_command=nanlong.CMD_CONCUBINE_PLACE)
        entry = self.result_entry(
            chat_id=-1001, reply_id=124, sender_id=123456, sender_is_bot=False,
            text="你已将道侣【墨彩环】安置在洞府的藏娇阁中。",
        )
        with (
            self.logged_results([(entry, self.now)]),
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            handled = await nanlong._recover_nanlong_pending_reply_from_log(self.now + 1)
        self.assertFalse(handled)
        sender.assert_not_awaited()
        self.assertEqual(124, self.identity["nanlong_last_msg_id"])

    async def test_log_recovery_accepts_a_strict_new_official_bot_shard(self):
        self.seed_exchange()
        entry = self.result_entry(sender_id=7002, sender_is_bot=True, sender_username="hantianzun999_bot")
        with self.logged_results([(entry, self.now)]), state_module.use_identity(self.identity_id):
            self.assertTrue(await nanlong._recover_nanlong_pending_reply_from_log(self.now + 1))
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])

    async def test_recovery_rejects_untrusted_or_conflicting_result_logs(self):
        variants = (
            {"sender_id": 123456, "sender_is_bot": False},
            {"sender_id": 7002, "sender_is_bot": True, "sender_username": "unrelated_bot"},
            {"chat_id": -1003},
            {"chat_id": -1001, "reply_to_msg_id": 555},
            {"event_type": "sent"},
            {"message_id": 0},
            {"text": "【天机异闻·南陇侯的交易】@SomeoneElse 已完成交易。"},
        )
        for overrides in variants:
            with self.subTest(overrides=overrides):
                self.seed_exchange()
                with (
                    self.logged_results([(self.result_entry(**overrides), self.now)]),
                    state_module.use_identity(self.identity_id),
                ):
                    self.assertFalse(await nanlong._recover_nanlong_pending_reply_from_log(self.now + 1))
                self.assertEqual(124, self.identity["nanlong_last_msg_id"])

    async def test_log_recovery_of_protected_trade_sends_only_one_recall(self):
        self.seed_exchange(protected=True)
        self.identity["nanlong_reply_due_at"] = self.now
        entries = [(self.result_entry(chat_id=chat_id), self.now) for chat_id in (-1001, -1002)]
        with (
            self.logged_results(entries),
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                id=125, chat_id=-1001, sent_at=self.now + 1,
            ))) as sender,
        ):
            await nanlong.run_nanlong_scheduler(self.now + 1)
        sender.assert_awaited_once()
        self.assertEqual(nanlong.CMD_CONCUBINE_RECALL, sender.await_args.args[0])
        self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])
        self.assertEqual(125, self.identity["nanlong_last_msg_id"])

    async def test_real_old_group_prompts_accept_new_group_unthreaded_results(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "nanlong_cross_chat_20260906.json").read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            with self.subTest(username=case["username"]):
                prompt, result = case["prompt"], case["result"]
                prompt_at = datetime.fromisoformat(prompt["date"]).timestamp()
                result_at = datetime.fromisoformat(result["date"]).timestamp()
                state_module.update_send_as_profile(self.identity_id, username=case["username"])
                self.identity["concubine_name"] = case["partner"]
                with (
                    state_module.use_identity(self.identity_id),
                    patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                        id=prompt["id"] + 1, chat_id=prompt["chat_id"], sent_at=prompt_at + 20,
                    ))) as sender,
                    patch.object(nanlong, "_get_nanlong_cave_status", return_value=nanlong.NANLONG_CAVE_STATUS_EMPTY),
                ):
                    self.assertTrue(await nanlong.handle_nanlong_prompt(
                        prompt["text"], prompt_at,
                        self.event(prompt["id"], prompt["chat_id"], at=prompt_at, reply_id=prompt["reply_to_msg_id"]),
                    ))
                    self.identity["nanlong_reply_due_at"] = prompt_at + 20
                    await nanlong.run_nanlong_scheduler(prompt_at + 20)
                    self.assertTrue(await nanlong.handle_nanlong_result_broadcast(
                        result["text"], result_at,
                        self.event(result["id"], result["chat_id"], at=result_at),
                    ))
                sender.assert_awaited_once()
                self.assertEqual(prompt["chat_id"], sender.call_args.kwargs["target_chat_id"])
                self.assertEqual(0, self.identity["nanlong_last_msg_id"])

    async def test_real_cross_group_result_logs_finish_the_existing_trade(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "nanlong_cross_chat_20260906.json").read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            with self.subTest(username=case["username"]):
                prompt, result = case["prompt"], case["result"]
                prompt_at = datetime.fromisoformat(prompt["date"]).timestamp()
                result_at = datetime.fromisoformat(result["date"]).timestamp()
                state_module.update_send_as_profile(self.identity_id, username=case["username"])
                self.identity.update(
                    nanlong_reply_to_msg_id=prompt["id"], nanlong_reply_chat_id=prompt["chat_id"],
                    next_nanlong_time=prompt_at + 600, nanlong_reply_due_at=prompt_at + 80,
                    nanlong_last_msg_id=prompt["id"] + 1, nanlong_last_chat_id=prompt["chat_id"],
                    nanlong_last_sent_at=prompt_at + 20, nanlong_last_command=nanlong.CMD_NANLONG_EXCHANGE_FABAO,
                    nanlong_protect_phase="", nanlong_retry_count=0,
                )
                entry = self.result_entry(chat_id=result["chat_id"], text=result["text"], message_id=result["id"])
                with (
                    self.logged_results([(entry, result_at)], groups=(prompt["chat_id"], result["chat_id"])),
                    state_module.use_identity(self.identity_id),
                    patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
                ):
                    await nanlong.run_nanlong_scheduler(max(result_at + 1, prompt_at + 81))
                sender.assert_not_awaited()
                self.assertEqual(0, self.identity["nanlong_last_msg_id"])
                self.assertEqual("", self.identity["nanlong_last_error"])

    async def test_trade_completion_clears_only_its_exact_detached_receipt(self):
        for from_log in (False, True):
            with self.subTest(from_log=from_log):
                self.seed_exchange()
                self.identity["nanlong_reply_due_at"] = self.now
                pending = {
                    (chat_id, msg_id): {
                        "cmd": nanlong.CMD_NANLONG_EXCHANGE_FABAO,
                        "sent_at": self.now - 1, "chat_id": chat_id,
                        "send_caller_detached": True, "max_retry": 0,
                    }
                    for chat_id, msg_id in ((-1001, 124), (-1002, 124), (-1001, 130))
                }
                self.identity["pending_tasks"] = pending
                with (
                    self.logged_results([(self.result_entry(), self.now)]),
                    state_module.use_identity(self.identity_id),
                    patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
                ):
                    if from_log:
                        await nanlong.run_nanlong_scheduler(self.now + 1)
                    else:
                        await nanlong.handle_nanlong_result_broadcast(self.trade_text, self.now, self.event(900, -1002))
                sender.assert_not_awaited()
                self.assertNotIn((-1001, 124), pending)
                self.assertIn((-1002, 124), pending)
                self.assertIn((-1001, 130), pending)

    async def test_old_or_conflicting_broadcast_cannot_complete_current_trade(self):
        for event in (self.event(900, -1002, at=self.now - 60), self.event(900, -1001, reply_id=555)):
            with self.subTest(event=event):
                self.seed_exchange()
                with state_module.use_identity(self.identity_id):
                    handled = await nanlong.handle_nanlong_result_broadcast(self.trade_text, self.now, event)
                self.assertFalse(handled)
                self.assertEqual(124, self.identity["nanlong_last_msg_id"])

    async def test_trade_broadcast_cannot_complete_place_or_recall_phase(self):
        for phase, command in (("place_pending", nanlong.CMD_CONCUBINE_PLACE), ("recall_pending", nanlong.CMD_CONCUBINE_RECALL)):
            with self.subTest(phase=phase):
                self.seed_exchange(protected=True)
                self.identity.update(nanlong_protect_phase=phase, nanlong_last_command=command)
                with state_module.use_identity(self.identity_id):
                    handled = await nanlong.handle_nanlong_result_broadcast(self.trade_text, self.now, self.event(900, -1002))
                self.assertFalse(handled)
                self.assertEqual(phase, self.identity["nanlong_protect_phase"])

    async def test_new_prompt_survives_result_notification(self):
        self.seed_exchange()

        async def audit(*_args, **_kwargs):
            nanlong._set_nanlong_pending(456, self.now + 300, self.now + 1)

        with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_audit_log", new=AsyncMock(side_effect=audit)):
            await nanlong.handle_nanlong_result_broadcast(self.trade_text, self.now, self.event(900, -1002))
        self.assertEqual(456, self.identity["nanlong_reply_to_msg_id"])

    async def test_account_rebind_during_result_notification_stops_followup_audit(self):
        self.seed_exchange()
        state_module.set_identity_account(self.identity_id, 7101)
        text = "【天机异闻·南陇侯的交易】@NanlongRoute 完成交易，南陇侯赐予【测试丹方】。"

        async def audit(*_args, **_kwargs):
            state_module.set_identity_account(self.identity_id, 7102)

        with state_module.use_identity(self.identity_id), patch.object(
            nanlong, "send_audit_log", new=AsyncMock(side_effect=audit),
        ) as audit_mock:
            self.assertTrue(await nanlong.handle_nanlong_result_broadcast(text, self.now, self.event(900, -1002)))
        audit_mock.assert_awaited_once()
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])
        self.assertEqual(7102, state_module.get_identity_account(self.identity_id))

    async def test_duplicate_trade_result_during_reward_audit_cannot_send_recall_twice(self):
        self.seed_exchange(protected=True)
        text = "【天机异闻·南陇侯的交易】@NanlongRoute 完成交易，南陇侯赐予【测试丹方】。"
        duplicated = False

        async def audit(*_args, **_kwargs):
            nonlocal duplicated
            if not duplicated:
                duplicated = True
                await nanlong.handle_nanlong_result_broadcast(text, self.now, self.event(900, -1002))

        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "send_audit_log", new=AsyncMock(side_effect=audit)),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                id=125, chat_id=-1001, sent_at=self.now,
            ))) as sender,
        ):
            await nanlong.handle_nanlong_result_broadcast(text, self.now, self.event(900, -1002))
        sender.assert_awaited_once()
        self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])

    def test_ambiguous_mentions_do_not_fall_back_to_username_prefix(self):
        self.assertIsNone(nanlong._find_nanlong_identity_id("南陇侯望向 @NanlongRouteExtra 和 @SomeoneElse"))

    def seed_place_confirmation(self):
        self.seed_exchange(protected=True)
        self.identity.update(
            nanlong_protect_phase="place_pending", nanlong_place_msg_id=124,
            nanlong_last_command=nanlong.CMD_CONCUBINE_PLACE,
            nanlong_last_prompt_key="-1001:123",
        )
        return (
            "你已将道侣【墨彩环】安置在洞府的藏娇阁中。",
            SimpleNamespace(id=124, chat_id=-1001, raw_text=nanlong.CMD_CONCUBINE_PLACE),
        )

    async def test_expired_prompt_after_confirmed_placement_recalls_once(self):
        text, reply = self.seed_place_confirmation()
        self.identity["next_nanlong_time"] = self.now - 1
        with (
            self.logged_results([]), state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                id=125, chat_id=-1001, sent_at=self.now,
            ))) as sender,
        ):
            self.assertTrue(await nanlong.handle_nanlong_reply(text, self.now, reply, matched_family="nanlong"))
            self.assertFalse(await nanlong.handle_nanlong_reply(text, self.now, reply, matched_family="nanlong"))
            self.assertEqual([nanlong.CMD_CONCUBINE_RECALL], [call.args[0] for call in sender.await_args_list])
            self.assertEqual(-1001, sender.await_args.kwargs["target_chat_id"])
            self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])
            self.assertTrue(await nanlong.handle_nanlong_reply(
                "你已将道侣【墨彩环】从藏娇阁中召回。", self.now + 1,
                SimpleNamespace(id=125, chat_id=-1001), matched_family="nanlong",
            ))
            await nanlong.run_nanlong_scheduler(self.now + 100)
        sender.assert_awaited_once()
        self.assertEqual("", self.identity["nanlong_protect_phase"])

    async def test_delayed_placement_log_uses_recovery_clock_for_followup(self):
        text, _reply = self.seed_place_confirmation()
        self.identity["next_nanlong_time"] = self.now + 5
        observed_at = self.now + 20
        entry = self.result_entry(chat_id=-1001, reply_id=124, text=text)

        async def send(command, **kwargs):
            self.assertTrue(kwargs["operation_check"](), command)
            return SimpleNamespace(id=125, chat_id=-1001, sent_at=observed_at)

        with (
            self.logged_results([(entry, self.now + 2)]),
            state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=observed_at),
            patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)) as sender,
        ):
            self.assertTrue(await nanlong._recover_nanlong_pending_reply_from_log(observed_at))
        self.assertEqual([nanlong.CMD_CONCUBINE_RECALL], [call.args[0] for call in sender.await_args_list])
        self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])

    async def test_reject_after_placement_finishes_recall_without_waiting_for_trade_reply(self):
        text, reply = self.seed_place_confirmation()
        state_module.set_nanlong_choice(self.identity_id, "reject")
        with (
            self.logged_results([]), state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now),
            patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=[
                SimpleNamespace(id=125, chat_id=-1001, sent_at=self.now),
                SimpleNamespace(id=126, chat_id=-1001, sent_at=self.now + 1),
            ])) as sender,
        ):
            self.assertTrue(await nanlong.handle_nanlong_reply(text, self.now, reply, matched_family="nanlong"))
            self.assertEqual(
                [nanlong.CMD_NANLONG_REJECT, nanlong.CMD_CONCUBINE_RECALL],
                [call.args[0] for call in sender.await_args_list],
            )
            self.assertEqual(123, sender.await_args_list[0].kwargs["reply_to"])
            self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])
            self.assertTrue(await nanlong.handle_nanlong_reply(
                "你已将道侣【墨彩环】从藏娇阁中召回。", self.now + 2,
                SimpleNamespace(id=126, chat_id=-1001), matched_family="nanlong",
            ))
            await nanlong.run_nanlong_scheduler(self.now + 100)
        self.assertEqual(2, sender.await_count)
        self.assertEqual("", self.identity["nanlong_protect_phase"])

    async def test_expired_placement_cleanup_keeps_unknown_recall_without_resend(self):
        text, reply = self.seed_place_confirmation()
        self.identity["next_nanlong_time"] = self.now - 1
        with (
            self.logged_results([]), state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=None)) as sender,
            patch.object(nanlong, "classify_game_send_block", return_value={"status": "unknown", "code": "send_timeout"}),
        ):
            self.assertTrue(await nanlong.handle_nanlong_reply(text, self.now, reply, matched_family="nanlong"))
            await nanlong.run_nanlong_scheduler(self.now + 86400)
        sender.assert_awaited_once()
        self.assertEqual(nanlong.CMD_CONCUBINE_RECALL, self.identity["nanlong_last_command"])
        self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])
        self.assertEqual(0, self.identity["nanlong_reply_due_at"])

    async def test_unresolved_protected_rejection_is_not_erased_at_prompt_expiry(self):
        text, reply = self.seed_place_confirmation()
        state_module.set_nanlong_choice(self.identity_id, "reject")
        with (
            self.logged_results([]), state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=None)) as sender,
            patch.object(nanlong, "classify_game_send_block", return_value={"status": "unknown", "code": "send_timeout"}),
        ):
            await nanlong.handle_nanlong_reply(text, self.now, reply, matched_family="nanlong")
            before = copy.deepcopy(self.identity)
            await nanlong.run_nanlong_scheduler(self.now + 86400)
        sender.assert_awaited_once()
        self.assertEqual(nanlong.CMD_NANLONG_REJECT, self.identity["nanlong_last_command"])
        self.assertEqual(before, self.identity)

    async def test_saved_protected_rejection_resumes_only_recall(self):
        self.seed_place_confirmation()
        state_module.set_nanlong_choice(self.identity_id, "reject")
        with state_module.use_identity(self.identity_id):
            nanlong._set_nanlong_waiting_for_exchange(
                SimpleNamespace(id=125, chat_id=-1001, sent_at=self.now),
                nanlong.CMD_NANLONG_REJECT, protected=True,
            )
            self.assertEqual("exchange_pending", self.identity["nanlong_protect_phase"])
            with (
                self.logged_results([]),
                patch.object(nanlong.time, "time", return_value=self.now + 100),
                patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                    id=126, chat_id=-1001, sent_at=self.now + 100,
                ))) as sender,
            ):
                await nanlong.run_nanlong_scheduler(self.now + 100)
                await nanlong.run_nanlong_scheduler(self.now + 101)
        self.assertEqual([nanlong.CMD_CONCUBINE_RECALL], [call.args[0] for call in sender.await_args_list])
        self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])

    async def test_detached_protected_rejection_preserves_cleanup(self):
        text, reply = self.seed_place_confirmation()
        state_module.set_nanlong_choice(self.identity_id, "reject")
        with (
            self.logged_results([]), state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=None)) as reject_sender,
            patch.object(nanlong, "classify_game_send_block", return_value={"status": "unknown"}),
        ):
            await nanlong.handle_nanlong_reply(text, self.now, reply, matched_family="nanlong")
            self.finalize_receipt(
                reject_sender.await_args.args[0], reject_sender.await_args.kwargs,
                msg_id=125, sent_at=self.now + 10,
            )
            with patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                id=126, chat_id=-1001, sent_at=self.now + 20,
            ))) as recall_sender:
                await nanlong.run_nanlong_scheduler(self.now + 20)
                await nanlong.run_nanlong_scheduler(self.now + 21)
        reject_sender.assert_awaited_once()
        self.assertEqual([nanlong.CMD_CONCUBINE_RECALL], [call.args[0] for call in recall_sender.await_args_list])
        self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])

    async def test_recovery_time_cannot_make_an_old_trade_result_current(self):
        self.seed_exchange(protected=True)
        before = copy.deepcopy(self.identity)
        with (
            self.logged_results([(self.result_entry(), self.now - 20)]),
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            self.assertFalse(await nanlong._recover_nanlong_pending_reply_from_log(self.now + 20))
        sender.assert_not_awaited()
        self.assertEqual(before, self.identity)

    async def test_deferred_recall_keeps_cleanup_backoff_and_unspent_retry(self):
        baseline = copy.deepcopy(self.identity)
        for origin in ("expired", "rejected", "trade"):
            for failure in ("unsent", "save_false", "save_error"):
                with self.subTest(origin=origin, failure=failure):
                    self.identity.clear()
                    self.identity.update(copy.deepcopy(baseline))
                    state_module.set_nanlong_choice(self.identity_id, "exchange_fabao")
                    text, reply = self.seed_place_confirmation()
                    if origin == "expired":
                        self.identity["next_nanlong_time"] = self.now - 1
                    elif origin == "rejected":
                        state_module.set_nanlong_choice(self.identity_id, "reject")
                    else:
                        self.seed_exchange(protected=True)
                        text = self.trade_text
                        reply.raw_text = nanlong.CMD_NANLONG_EXCHANGE_FABAO
                    failed, clock, commands = False, self.now, []

                    def save():
                        nonlocal failed
                        if (failure.startswith("save") and not failed
                                and self.identity["nanlong_last_command"] == nanlong.CMD_CONCUBINE_RECALL
                                and not self.identity["nanlong_last_msg_id"]):
                            failed = True
                            if failure == "save_error":
                                raise OSError("fixture unavailable storage")
                            return False
                        return True

                    async def send(command, **kwargs):
                        nonlocal failed
                        self.assertTrue(kwargs["operation_check"]())
                        commands.append(command)
                        if command == nanlong.CMD_CONCUBINE_RECALL and failure == "unsent" and not failed:
                            failed = True
                            return None
                        return SimpleNamespace(id=125 + len(commands), chat_id=-1001, sent_at=clock)

                    with (
                        self.logged_results([]), state_module.use_identity(self.identity_id),
                        patch.object(nanlong.time, "time", side_effect=lambda: clock),
                        patch.object(nanlong.random, "randint", return_value=30),
                        patch.object(nanlong, "save_state", side_effect=save),
                        patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)),
                        patch.object(nanlong, "classify_game_send_block", return_value={"status": "unsent", "code": "send_queue_timeout"}),
                    ):
                        self.assertTrue(await nanlong.handle_nanlong_reply(text, clock, reply, matched_family="nanlong"))
                        self.assertTrue(failed)
                        self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])
                        due_at = self.identity["nanlong_reply_due_at"]
                        self.assertGreater(due_at, clock)
                        sent_before = list(commands)
                        await nanlong.run_nanlong_scheduler(clock + 1)
                        self.assertEqual(sent_before, commands)
                        self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])
                        clock = due_at
                        await nanlong.run_nanlong_scheduler(clock)
                        self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])
                        self.assertEqual(nanlong.CMD_CONCUBINE_RECALL, self.identity["nanlong_last_command"])
                        self.assertEqual(0, self.identity["nanlong_retry_count"])
                        expected = [nanlong.CMD_NANLONG_REJECT] if origin == "rejected" else []
                        expected += [nanlong.CMD_CONCUBINE_RECALL] * (2 if failure == "unsent" else 1)
                        self.assertEqual(expected, commands)

    async def test_returned_receipt_survives_post_dispatch_disable(self):
        from model import control as controls

        baseline = copy.deepcopy(self.identity)
        for step in ("exchange", "place", "recall", "reject"):
            for control in ("module", "identity"):
                with self.subTest(step=step, control=control):
                    self.identity.clear()
                    self.identity.update(copy.deepcopy(baseline))
                    state_module.update_send_as_profile(self.identity_id, enabled=True)
                    state_module.set_nanlong_choice(self.identity_id, "exchange_fabao")
                    await self.seed_prompt()
                    if step == "place":
                        self.identity["concubine_name"] = "墨彩环"
                    elif step == "recall":
                        self.seed_exchange(protected=True)
                    elif step == "reject":
                        state_module.set_nanlong_choice(self.identity_id, "reject")

                    async def send(command, **kwargs):
                        if control == "module":
                            with patch.object(controls, "save_state"):
                                ok, message = await controls.set_module_enabled("南陇侯", False, send_as_id=self.identity_id)
                            self.assertTrue(ok, message)
                        else:
                            state_module.update_send_as_profile(self.identity_id, enabled=False)
                        return self.finalize_receipt(command, kwargs, detached=False)

                    with (
                        state_module.use_identity(self.identity_id),
                        patch.object(nanlong.time, "time", return_value=self.now),
                        patch.object(nanlong, "_get_nanlong_cave_status", return_value=nanlong.NANLONG_CAVE_STATUS_AVAILABLE),
                        patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)) as sender,
                    ):
                        if step == "recall":
                            await nanlong._send_nanlong_recall_after_trade(self.now)
                        else:
                            await nanlong.run_nanlong_scheduler(self.now)
                        await nanlong.run_nanlong_scheduler(self.now + 120)
                    sender.assert_awaited_once()
                    if step == "reject":
                        self.assertEqual(0, self.identity["nanlong_reply_to_msg_id"])
                    else:
                        self.assertEqual(124, self.identity["nanlong_last_msg_id"])
                        self.assertEqual(-1001, self.identity["nanlong_last_chat_id"])
                        self.assertEqual(self.now, self.identity["nanlong_last_sent_at"])

    async def test_disabled_success_records_continuation_without_sending(self):
        baseline = copy.deepcopy(self.identity)
        steps = (
            ("place_pending", nanlong.CMD_CONCUBINE_PLACE, "你已将道侣【墨彩环】安置在洞府的藏娇阁中。", "exchange_pending"),
            ("exchange_pending", nanlong.CMD_NANLONG_EXCHANGE_FABAO, self.trade_text, "recall_pending"),
            ("recall_pending", nanlong.CMD_CONCUBINE_RECALL, "你已将道侣【墨彩环】从藏娇阁中召回。", ""),
        )
        for phase, command, text, next_phase in steps:
            for control in ("module", "identity"):
                with self.subTest(phase=phase, control=control):
                    self.identity.clear()
                    self.identity.update(copy.deepcopy(baseline))
                    state_module.update_send_as_profile(self.identity_id, enabled=True)
                    self.seed_exchange(protected=True)
                    self.identity.update(nanlong_protect_phase=phase, nanlong_last_command=command)
                    if control == "module":
                        self.identity["nanlong_enabled"] = False
                    else:
                        state_module.update_send_as_profile(self.identity_id, enabled=False)
                    reply = SimpleNamespace(id=124, chat_id=-1001, raw_text=command)
                    with state_module.use_identity(self.identity_id), patch.object(
                        nanlong, "send_game_command", new=AsyncMock(),
                    ) as sender:
                        self.assertTrue(await nanlong.handle_nanlong_reply(text, self.now, reply, matched_family="nanlong"))
                        self.assertFalse(await nanlong.handle_nanlong_reply(text, self.now + 1, reply, matched_family="nanlong"))
                        await nanlong.run_nanlong_scheduler(self.now + 100)
                    sender.assert_not_awaited()
                    self.assertEqual(next_phase, self.identity["nanlong_protect_phase"])
                    self.assertEqual(0, self.identity["nanlong_last_msg_id"])
                    if next_phase:
                        self.assertGreater(self.identity["nanlong_reply_due_at"], 0)
                        self.assertEqual("", self.identity["nanlong_last_command"])

    async def test_disabled_identity_broadcast_records_cleanup_then_resumes_once(self):
        from model import app

        self.seed_exchange(protected=True)
        state_module.update_send_as_profile(self.identity_id, enabled=False)
        with (
            patch.object(app, "_claim_runtime_event", return_value=True),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                id=125, chat_id=-1001, sent_at=self.now + 2,
            ))) as sender,
        ):
            await app._dispatch_nanlong_result_broadcast_fallbacks(self.event(900, -1002), self.trade_text, self.now)
            sender.assert_not_awaited()
            self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])
            self.assertEqual(0, self.identity["nanlong_last_msg_id"])
            state_module.update_send_as_profile(self.identity_id, enabled=True)
            with state_module.use_identity(self.identity_id):
                await nanlong.run_nanlong_scheduler(self.now + 1)
                await nanlong.run_nanlong_scheduler(self.now + 2)
        self.assertEqual([nanlong.CMD_CONCUBINE_RECALL], [call.args[0] for call in sender.await_args_list])

    async def test_missing_results_preserve_receipts_without_retries_or_expiry_deletion(self):
        baseline = copy.deepcopy(self.identity)
        steps = (
            ("place_pending", nanlong.CMD_CONCUBINE_PLACE),
            ("exchange_pending", nanlong.CMD_NANLONG_EXCHANGE_FABAO),
            ("recall_pending", nanlong.CMD_CONCUBINE_RECALL),
            ("", nanlong.CMD_NANLONG_EXCHANGE_FABAO),
        )
        for phase, command in steps:
            with self.subTest(phase=phase):
                self.identity.clear()
                self.identity.update(copy.deepcopy(baseline))
                self.seed_exchange(protected=bool(phase))
                self.identity.update(
                    nanlong_protect_phase=phase, nanlong_last_command=command,
                    nanlong_reply_due_at=self.now - 1,
                )
                with (
                    self.logged_results([]), state_module.use_identity(self.identity_id),
                    patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
                ):
                    for clock in (self.now, self.now + 61, self.now + 86400):
                        await nanlong.run_nanlong_scheduler(clock)
                sender.assert_not_awaited()
                self.assertEqual(124, self.identity["nanlong_last_msg_id"])
                self.assertEqual(command, self.identity["nanlong_last_command"])
                self.assertEqual(phase, self.identity["nanlong_protect_phase"])
                self.assertIn("待核对", self.identity["nanlong_last_error"])

    async def test_new_prompt_cannot_replace_an_unresolved_send_or_result(self):
        for has_receipt in (False, True):
            with self.subTest(has_receipt=has_receipt):
                self.seed_exchange(protected=True)
                self.identity["nanlong_last_prompt_key"] = "-1001:123"
                if not has_receipt:
                    self.identity.update(nanlong_last_msg_id=0, nanlong_reply_due_at=0)
                before = {key: self.identity.get(key) for key in nanlong._NANLONG_OPERATION_KEYS}
                with state_module.use_identity(self.identity_id):
                    await nanlong.handle_nanlong_prompt(self.prompt_text, self.now + 1, self.event(456, -1002, at=self.now + 1))
                self.assertEqual(before, {key: self.identity.get(key) for key in nanlong._NANLONG_OPERATION_KEYS})

    async def test_original_round_result_is_recovered_after_a_long_disabled_interval(self):
        self.seed_exchange()
        original_result_at = self.now + 1
        resumed_at = self.now + 86400
        with (
            self.logged_results([(self.result_entry(), original_result_at)]),
            state_module.use_identity(self.identity_id),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            await nanlong.run_nanlong_scheduler(resumed_at)
        sender.assert_not_awaited()
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])
        self.assertEqual("", self.identity["nanlong_last_error"])

    async def test_direct_reply_rejects_a_contradictory_command_or_older_source_clock(self):
        for reply_command, reply_at in ((nanlong.CMD_CONCUBINE_RECALL, self.now), (nanlong.CMD_NANLONG_EXCHANGE_FABAO, self.now - 30)):
            with self.subTest(command=reply_command, source_at=reply_at):
                self.seed_exchange()
                before = copy.deepcopy(self.identity)
                with state_module.use_identity(self.identity_id):
                    self.assertFalse(await nanlong.handle_nanlong_reply(
                        self.trade_text, reply_at,
                        SimpleNamespace(id=124, chat_id=-1001, raw_text=reply_command), matched_family="nanlong",
                    ))
                self.assertEqual(before, self.identity)

    async def test_native_routed_reply_uses_server_time_while_module_disabled(self):
        from model import app

        self.seed_exchange()
        self.identity["nanlong_enabled"] = False
        reply = SimpleNamespace(id=124, chat_id=-1001, sender_id=self.identity_id, raw_text=nanlong.CMD_NANLONG_EXCHANGE_FABAO)
        context = {"send_as_id": self.identity_id, "family": "nanlong", "root_msg_id": 124, "reply_to_msg_id": 124,
                   "chat_id": -1001, "matched_via": "my_msg_ids"}
        self.identity["my_msg_ids"][(-1001, 124)] = self.now - 1
        event = self.event(900, -1001, at=self.now - 10, reply_id=124)
        event.sender_id = 7001
        with (
            patch.object(app, "_claim_runtime_event", return_value=True),
            patch.object(app, "_has_runtime_message_consumed", return_value=False),
            patch.object(app, "_mark_runtime_message_consumed"),
            patch.object(app, "record_unhandled_routed_reply"),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            self.assertFalse(await app._handle_routed_reply_event(event, self.trade_text, self.now, reply, context))
            self.assertEqual(124, self.identity["nanlong_last_msg_id"])
            event.date = datetime.fromtimestamp(self.now, timezone.utc)
            self.assertTrue(await app._handle_routed_reply_event(event, self.trade_text, self.now, reply, context))
        sender.assert_not_awaited()
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])

    async def test_log_replay_cannot_replace_server_time_with_later_log_time(self):
        for direct in (False, True):
            for source_at in (self.now - 30, 0, None, "invalid"):
                with self.subTest(direct=direct, source_at=source_at):
                    self.seed_exchange()
                    entry = self.result_entry(
                        chat_id=-1001 if direct else -1002, reply_id=124 if direct else 0,
                        server_event_at=source_at,
                    )
                    before = copy.deepcopy(self.identity)
                    with self.logged_results([(entry, self.now + 1)]), state_module.use_identity(self.identity_id):
                        self.assertFalse(await nanlong._recover_nanlong_pending_reply_from_log(self.now + 2))
                    self.assertEqual(before, self.identity)

    async def test_unthreaded_result_requires_a_valid_original_receipt_route(self):
        changes = (
            {"nanlong_last_msg_id": "invalid"}, {"nanlong_last_msg_id": -1},
            {"nanlong_last_msg_id": True}, {"nanlong_last_msg_id": 124.5},
            {"nanlong_last_chat_id": 0}, {"nanlong_last_sent_at": True},
        )
        for change in changes:
            with self.subTest(change=change):
                self.seed_exchange()
                self.identity.update(change)
                before = copy.deepcopy(self.identity)
                with state_module.use_identity(self.identity_id), patch.object(nanlong, "get_sent_message_chat_id", return_value=0):
                    self.assertFalse(await nanlong.handle_nanlong_result_broadcast(self.trade_text, self.now, self.event(900, -1002)))
                self.assertEqual(before, self.identity)

    async def test_ui_keeps_expired_or_disabled_unresolved_work_visible(self):
        from model import ui

        self.seed_exchange(protected=True)
        self.identity.update(nanlong_enabled=False, next_nanlong_time=self.now - 1)
        self.assertTrue(ui.get_identity_ui_snapshot(self.identity_id)["nanlong_pending"])
        self.identity.update(nanlong_reply_to_msg_id=0, nanlong_last_msg_id=0, nanlong_last_command="", nanlong_protect_phase="recall_pending")
        self.assertTrue(ui.get_identity_ui_snapshot(self.identity_id)["nanlong_pending"])
        with state_module.use_identity(self.identity_id):
            nanlong.clear_nanlong_state()
        self.assertFalse(ui.get_identity_ui_snapshot(self.identity_id)["nanlong_pending"])

    async def test_orphan_child_anchor_cannot_send_or_expire_as_an_unused_prompt(self):
        baseline = copy.deepcopy(self.identity)
        for child in ("nanlong_place_msg_id", "nanlong_recall_msg_id"):
            for expired in (False, True):
                with self.subTest(child=child, expired=expired):
                    self.identity.clear()
                    self.identity.update(copy.deepcopy(baseline))
                    await self.seed_prompt()
                    self.identity[child] = 124
                    if expired:
                        self.identity["next_nanlong_time"] = self.now - 1
                    with (
                        state_module.use_identity(self.identity_id),
                        patch.object(nanlong, "iter_message_log_entries_between", return_value=[]),
                        patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
                    ):
                        await nanlong.run_nanlong_scheduler(self.now)
                        await nanlong.run_nanlong_scheduler(self.now + 86400)
                    sender.assert_not_awaited()
                    self.assertEqual(124, self.identity[child])
                    self.assertIn("待核对", self.identity["nanlong_last_error"])

    def seed_native_orphan_receipt(self, command, *, track=False):
        state_module.set_identity_account(self.identity_id, 7101)
        with (
            state_module.use_identity(self.identity_id),
            patch.object(nanlong.time, "time", return_value=self.now),
            patch.object(runtime, "MESSAGES_DIR", message_log_recovery.MESSAGES_DIR),
            patch.object(runtime, "cleanup_message_logs"),
            patch.object(runtime, "_notify_game_command_sent_observers"),
        ):
            nanlong._set_nanlong_pending(123, self.now + 180, self.now - 1, chat_id=-1001)
            self.identity["nanlong_last_chat_id"] = -1001
            self.assertTrue(nanlong._prepare_nanlong_send(command, self.now))
            receipt = runtime._finalize_game_command_sent(
                command, msg_id=124, sent_at=self.now, send_started_at=self.now,
                send_as_id=self.identity_id, track=track, max_retry=0,
                game_group_id=-1001, topic_id=0, send_intent=nanlong._nanlong_send_intent(),
            )
            if command == nanlong.CMD_CONCUBINE_PLACE:
                nanlong._set_nanlong_waiting_for_place(receipt)
            else:
                nanlong._set_nanlong_waiting_for_recall(receipt)
        self.identity.update(nanlong_last_msg_id=0, nanlong_last_command="", nanlong_protect_phase="")

    async def test_owned_orphan_receipt_rejoins_its_original_result_without_resending(self):
        steps = (
            (nanlong.CMD_CONCUBINE_PLACE, "你已将道侣【墨彩环】安置在洞府的藏娇阁中。", nanlong.CMD_NANLONG_EXCHANGE_FABAO),
            (nanlong.CMD_CONCUBINE_RECALL, "你已将道侣【墨彩环】从藏娇阁中召回。", None),
        )
        for command, result, successor in steps:
            with self.subTest(command=command), self.logged_results([]) as append_entry:
                self.seed_native_orphan_receipt(command)
                append_entry(self.result_entry(chat_id=-1001, reply_id=124, text=result), self.now + 1)
                with (
                    state_module.use_identity(self.identity_id),
                    patch.object(nanlong.time, "time", return_value=self.now + 61),
                    patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                        id=125, chat_id=-1001, sent_at=self.now + 61,
                    ))) as sender,
                ):
                    await nanlong.run_nanlong_scheduler(self.now + 61)
                    await nanlong.run_nanlong_scheduler(self.now + 62)
                self.assertEqual([successor] if successor else [], [call.args[0] for call in sender.await_args_list])
                if successor:
                    self.assertEqual("exchange_pending", self.identity["nanlong_protect_phase"])
                    self.assertEqual(125, self.identity["nanlong_last_msg_id"])
                else:
                    self.assertEqual(0, self.identity["nanlong_recall_msg_id"])
                    self.assertEqual("", self.identity["nanlong_last_error"])

    async def test_ui_retains_malformed_nanlong_state_without_breaking_snapshot(self):
        from model import ui

        for field in ("nanlong_reply_to_msg_id", "next_nanlong_time", "nanlong_reply_due_at"):
            for value in ("invalid", "nan", True, [123]):
                with self.subTest(field=field, value=value):
                    self.seed_exchange()
                    self.identity[field] = value
                    before = {key: copy.deepcopy(value) for key, value in self.identity.items() if "nanlong" in key}
                    snapshot = ui.get_identity_ui_snapshot(self.identity_id)
                    self.assertTrue(snapshot["nanlong_pending"])
                    self.assertEqual(before, {key: value for key, value in self.identity.items() if "nanlong" in key})
                    self.assertIn("异常", snapshot["nanlong_last_error"])

    async def test_orphan_receipt_can_use_native_pending_evidence_without_logs(self):
        with self.logged_results([]):
            self.seed_native_orphan_receipt(nanlong.CMD_CONCUBINE_RECALL, track=True)
            with (
                state_module.use_identity(self.identity_id),
                patch.object(nanlong, "iter_message_log_entries_between", return_value=[]),
                patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
            ):
                await nanlong.run_nanlong_scheduler(self.now + 61)
                self.assertEqual(124, self.identity["nanlong_last_msg_id"])
                self.assertEqual("recall_pending", self.identity["nanlong_protect_phase"])
                self.assertTrue(await nanlong.handle_nanlong_reply(
                    "你已将道侣【墨彩环】从藏娇阁中召回。", self.now + 62,
                    SimpleNamespace(id=124, chat_id=-1001, raw_text=nanlong.CMD_CONCUBINE_RECALL), matched_family="nanlong",
                ))
            sender.assert_not_awaited()
            self.assertNotIn((-1001, 124), self.identity["pending_tasks"])

    async def test_conflicting_orphan_receipts_cannot_override_matching_native_evidence(self):
        changes = (
            {"chat_id": -1002}, {"sender_id": self.identity_id + 1}, {"account_id": 7102},
            {"source_module": "侍妾"}, {"family": "concubine"}, {"chain_id": "different"},
            {"op_id": "different"}, {"reply_to_msg_id": 123}, {"text": nanlong.CMD_CONCUBINE_PLACE},
            {"send_started_at": self.now + 10}, {"send_started_at": "invalid"},
            {"message_id": 125},
        )
        for changeset in changes:
            with self.subTest(changes=changeset), self.logged_results([]) as append_entry:
                self.seed_native_orphan_receipt(nanlong.CMD_CONCUBINE_RECALL)
                entry = next(entry for entry, _ in message_log_recovery.iter_message_log_entries_between(self.now - 1, self.now + 1)
                             if entry.get("event_type") == "sent")
                append_entry({**entry, **changeset}, self.now)
                with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender:
                    await nanlong.run_nanlong_scheduler(self.now + 61)
                sender.assert_not_awaited()
                self.assertEqual(0, self.identity["nanlong_last_msg_id"])
                self.assertEqual(124, self.identity["nanlong_recall_msg_id"])
                self.assertIn("待核对", self.identity["nanlong_last_error"])

    async def test_orphan_receipt_rejects_unowned_or_contradictory_state(self):
        changes = (
            {"nanlong_last_chat_id": -1002}, {"nanlong_reply_chat_id": -1002},
            {"nanlong_last_prompt_key": "-1001:999"}, {"nanlong_last_prompt_key": ""},
            {"nanlong_last_sent_at": self.now - 20}, {"nanlong_last_sent_at": self.now + 20},
            {"nanlong_recall_msg_id": "invalid"}, {"nanlong_recall_msg_id": True},
            {"nanlong_recall_msg_id": -124}, {"nanlong_last_msg_id": "invalid"},
            {"nanlong_protect_phase": "invalid"}, {"account": 7102},
        )
        for changeset in changes:
            with self.subTest(changes=changeset), self.logged_results([]):
                self.seed_native_orphan_receipt(nanlong.CMD_CONCUBINE_RECALL)
                if "account" in changeset:
                    state_module.set_identity_account(self.identity_id, changeset["account"])
                else:
                    self.identity.update(changeset)
                before = copy.deepcopy(self.identity)
                with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender:
                    await nanlong.run_nanlong_scheduler(self.now + 61)
                sender.assert_not_awaited()
                for key in ("nanlong_recall_msg_id", "nanlong_last_msg_id", "nanlong_last_command", "nanlong_last_chat_id"):
                    self.assertEqual(before[key], self.identity[key])

    async def test_partial_or_contradictory_child_phase_cannot_repeat_a_known_mutation(self):
        for phase in ("place_pending", "exchange_pending", "recall_pending"):
            with self.subTest(phase=phase), self.logged_results([]):
                self.seed_native_orphan_receipt(nanlong.CMD_CONCUBINE_RECALL)
                self.identity["nanlong_protect_phase"] = phase
                with (
                    state_module.use_identity(self.identity_id),
                    patch.object(nanlong, "iter_message_log_entries_between", return_value=[]),
                    patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
                ):
                    await nanlong.run_nanlong_scheduler(self.now + 61)
                sender.assert_not_awaited()
                self.assertEqual(124, self.identity["nanlong_recall_msg_id"])
                self.assertEqual(0, self.identity["nanlong_last_msg_id"])

    async def test_matching_partial_phase_recovers_from_duplicate_native_pending_and_log_evidence(self):
        for command, phase in ((nanlong.CMD_CONCUBINE_PLACE, "place_pending"), (nanlong.CMD_CONCUBINE_RECALL, "recall_pending")):
            with self.subTest(command=command), self.logged_results([]):
                self.seed_native_orphan_receipt(command, track=True)
                self.identity["nanlong_protect_phase"] = phase
                with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender:
                    await nanlong.run_nanlong_scheduler(self.now + 61)
                    await nanlong.run_nanlong_scheduler(self.now + 62)
                sender.assert_not_awaited()
                self.assertEqual(124, self.identity["nanlong_last_msg_id"])
                self.assertEqual(command, self.identity["nanlong_last_command"])
                self.assertEqual(phase, self.identity["nanlong_protect_phase"])

    async def test_orphan_recall_with_a_stale_previous_command_cannot_recall_again(self):
        with self.logged_results([]):
            self.seed_native_orphan_receipt(nanlong.CMD_CONCUBINE_RECALL)
            self.identity.update(nanlong_last_command=nanlong.CMD_NANLONG_EXCHANGE_FABAO, nanlong_protect_phase="recall_pending")
            with state_module.use_identity(self.identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender:
                await nanlong.run_nanlong_scheduler(self.now + 61)
            sender.assert_not_awaited()
            self.assertEqual(0, self.identity["nanlong_last_msg_id"])
            self.assertEqual(124, self.identity["nanlong_recall_msg_id"])

    async def test_failed_orphan_recovery_save_preserves_evidence_and_does_not_send(self):
        for failure in (False, OSError("local disk unavailable")):
            with self.subTest(failure=failure), self.logged_results([]):
                self.seed_native_orphan_receipt(nanlong.CMD_CONCUBINE_PLACE)
                with (
                    state_module.use_identity(self.identity_id),
                    patch.object(nanlong, "save_state", return_value=False, side_effect=failure if isinstance(failure, Exception) else None),
                    patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
                ):
                    if isinstance(failure, Exception):
                        with self.assertRaises(OSError):
                            await nanlong.run_nanlong_scheduler(self.now + 61)
                    else:
                        await nanlong.run_nanlong_scheduler(self.now + 61)
                sender.assert_not_awaited()
                self.assertEqual(124, self.identity["nanlong_place_msg_id"])
                self.assertEqual(0, self.identity["nanlong_last_msg_id"])
                self.assertEqual("", self.identity["nanlong_last_command"])

    async def test_two_owned_child_receipts_resume_the_latest_cleanup_from_bounded_old_windows(self):
        recalled_at = self.now + 1800
        with self.logged_results([]) as append_entry:
            self.seed_native_orphan_receipt(nanlong.CMD_CONCUBINE_PLACE)
            with (
                state_module.use_identity(self.identity_id),
                patch.object(nanlong.time, "time", return_value=recalled_at),
                patch.object(runtime, "MESSAGES_DIR", message_log_recovery.MESSAGES_DIR),
                patch.object(runtime, "cleanup_message_logs"),
                patch.object(runtime, "_notify_game_command_sent_observers"),
            ):
                nanlong._clear_nanlong_prompt_anchor()
                self.assertTrue(nanlong._prepare_nanlong_send(nanlong.CMD_CONCUBINE_RECALL, recalled_at))
                receipt = runtime._finalize_game_command_sent(
                    nanlong.CMD_CONCUBINE_RECALL, msg_id=126, sent_at=recalled_at, send_started_at=recalled_at,
                    send_as_id=self.identity_id, track=False, game_group_id=-1001, topic_id=0,
                    send_intent=nanlong._nanlong_send_intent(),
                )
                nanlong._set_nanlong_waiting_for_recall(receipt)
            self.identity.update(nanlong_last_msg_id=0, nanlong_last_command="", nanlong_protect_phase="")
            append_entry(self.result_entry(chat_id=-1001, reply_id=126, text="你已将道侣【墨彩环】从藏娇阁中召回。"), recalled_at + 1)
            with (
                state_module.use_identity(self.identity_id),
                patch.object(nanlong, "iter_message_log_entries_between", wraps=message_log_recovery.iter_message_log_entries_between) as reader,
                patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
            ):
                await nanlong.run_nanlong_scheduler(self.now + 86400)
            sender.assert_not_awaited()
            self.assertEqual(0, self.identity["nanlong_place_msg_id"])
            self.assertEqual(0, self.identity["nanlong_recall_msg_id"])
            self.assertEqual("", self.identity["nanlong_last_error"])
            self.assertTrue(all(call.args[1] - call.args[0] < 2 * nanlong.NANLONG_LOG_REPLAY_LOOKBACK_SEC for call in reader.call_args_list))
