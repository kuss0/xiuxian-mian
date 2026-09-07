import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import state as state_module
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
        with state_module.use_identity(self.identity_id), patch.object(nanlong, "find_message_log_replies", return_value=[]) as recovery:
            await nanlong._recover_nanlong_pending_reply_from_log(self.now)
        recovery.assert_not_called()

    async def test_current_unthreaded_result_can_arrive_in_another_group(self):
        self.seed_exchange()
        with state_module.use_identity(self.identity_id):
            handled = await nanlong.handle_nanlong_result_broadcast(self.trade_text, self.now, self.event(900, -1002))
        self.assertTrue(handled)
        self.assertEqual(0, self.identity["nanlong_last_msg_id"])

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
