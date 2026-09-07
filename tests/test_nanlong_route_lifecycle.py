import copy
import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import message_log_recovery, state as state_module
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
            for entry, timestamp in entries:
                local_time = datetime.fromtimestamp(timestamp, message_log_recovery.TZ_LOCAL)
                payload = {"ts": local_time.strftime("%Y-%m-%d %H:%M:%S UTC+8"), **entry}
                path = Path(directory) / f"{local_time.date().isoformat()}.log"
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            with patch.object(message_log_recovery, "MESSAGES_DIR", directory):
                yield

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
            await nanlong.handle_nanlong_prompt(self.prompt_text, self.now + 1, self.event(456, -1002, at=self.now + 1))
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
