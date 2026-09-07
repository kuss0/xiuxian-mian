import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import state as state_module
from model.features import tiandao_judgement as judgement


class TiandaoJudgementRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.snapshot = copy.deepcopy(state_module._meta_state)
        self.terminals = dict(judgement._tiandao_miniapp_terminal_events)
        judgement._tiandao_miniapp_terminal_events.clear()
        self.now = 1_700_000_000.0
        self.identity_id = 995101
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(self.identity_id, username="RouteUser")
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {}
        for name, replacement in (("save_state", None), ("send_audit_log", AsyncMock())):
            patcher = patch.object(judgement, name, **({"new": replacement} if replacement is not None else {}))
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self.snapshot)
        judgement._tiandao_miniapp_terminal_events.clear()
        judgement._tiandao_miniapp_terminal_events.update(self.terminals)

    def add_item(self, msg_id=123, chat_id=-1001, *, miniapp=False):
        item = {
            "target": "RouteUser", "identity_id": self.identity_id,
            "msg_id": msg_id, "chat_id": chat_id,
            "question": "1 + 1", "answer": "2", "token": "rpt_TEST",
            "due_at": self.now - 1, "deadline_at": self.now + 120,
            "created_at": self.now - 10, "retry_count": 0,
        }
        if miniapp:
            item.update(kind="miniapp_drag", miniapp_kind="rpt")
        pending = judgement._get_pending_map()
        pending[f"{chat_id}:{msg_id}"] = item
        judgement._set_pending_map(pending)
        return item

    async def test_reply_send_uses_the_original_prompt_chat(self):
        self.add_item(chat_id=-1002)
        with patch.object(judgement, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=124))) as sender:
            await judgement.run_tiandao_judgement_scheduler(self.now)
        self.assertEqual(-1002, sender.call_args.kwargs.get("target_chat_id"))

    async def test_reply_without_chat_cannot_fall_back_to_the_primary_group(self):
        self.add_item(chat_id=0)
        with patch.object(judgement, "send_game_command", new=AsyncMock()) as sender:
            await judgement.run_tiandao_judgement_scheduler(self.now)
        sender.assert_not_awaited()

    async def test_new_work_queued_during_send_is_retained(self):
        self.add_item()

        async def send(*_args, **_kwargs):
            self.add_item(msg_id=456)
            return SimpleNamespace(id=124)

        with patch.object(judgement, "send_game_command", new=AsyncMock(side_effect=send)):
            await judgement.run_tiandao_judgement_scheduler(self.now)
        self.assertEqual({"-1001:456"}, set(judgement._get_pending_map()))

    async def test_cleared_work_is_not_recreated_after_send_or_miniapp_await(self):
        for miniapp in (False, True):
            with self.subTest(miniapp=miniapp):
                self.add_item(miniapp=miniapp)

                async def clear(*_args, **_kwargs):
                    judgement._set_pending_map({})
                    return {"ok": False, "error": "temporary"} if miniapp else None

                tool = "run_tiandao_miniapp_drag_verification" if miniapp else "send_game_command"
                with patch.object(judgement, tool, new=AsyncMock(side_effect=clear)):
                    await judgement.run_tiandao_judgement_scheduler(self.now)
                self.assertEqual({}, judgement._get_pending_map())

    async def test_cancelled_later_item_is_not_dispatched_from_the_old_snapshot(self):
        self.add_item()
        self.add_item(msg_id=456)

        async def clear(*_args, **_kwargs):
            judgement._set_pending_map({})
            return SimpleNamespace(id=124)

        with patch.object(judgement, "send_game_command", new=AsyncMock(side_effect=clear)) as sender:
            await judgement.run_tiandao_judgement_scheduler(self.now)
        sender.assert_awaited_once()

    async def test_toggle_off_during_first_send_prevents_the_next_item(self):
        self.add_item()
        self.add_item(msg_id=456)

        async def disable(*_args, **_kwargs):
            state_module.state["tiandao_judgement_enabled"] = False
            return SimpleNamespace(id=124)

        with patch.object(judgement, "send_game_command", new=AsyncMock(side_effect=disable)) as sender:
            await judgement.run_tiandao_judgement_scheduler(self.now)
        sender.assert_awaited_once()

    def test_same_id_success_in_another_chat_does_not_clear_both_prompts(self):
        self.add_item(miniapp=True)
        self.add_item(chat_id=-1002, miniapp=True)
        judgement._clear_tiandao_pending_for_success(
            "Mini App 验证完成！已通过本轮交易验证", self.event(-1002, 124, reply_id=123), self.now,
        )
        self.assertEqual({"-1001:123"}, set(judgement._get_pending_map()))

    def test_terminal_edit_prefers_exact_anchor_over_ambiguous_target_name(self):
        self.add_item(miniapp=True)
        self.add_item(msg_id=456, miniapp=True)
        judgement._clear_tiandao_pending_for_success(
            "天道裁决 真相大白 对象 【RouteUser】 已完成本轮自证", self.event(-1001, 123), self.now,
        )
        self.assertEqual({"-1001:456"}, set(judgement._get_pending_map()))

    def test_explicit_wrong_reply_cannot_fall_back_to_target_name(self):
        self.add_item(miniapp=True)
        judgement._clear_tiandao_pending_for_success(
            "天道裁决 真相大白 对象 【RouteUser】 已完成本轮自证", self.event(-1001, 124, reply_id=999), self.now,
        )
        self.assertEqual({"-1001:123"}, set(judgement._get_pending_map()))

    def test_cross_group_broadcast_can_complete_one_unambiguous_current_challenge(self):
        self.add_item(miniapp=True)
        self.assertTrue(judgement._clear_tiandao_pending_for_success(
            "天道裁决 真相大白 对象 【RouteUser】 已完成本轮自证", self.event(-1002, 200), self.now,
        ))
        self.assertEqual({}, judgement._get_pending_map())

    def test_name_only_broadcast_cannot_complete_two_concurrent_challenges(self):
        self.add_item(miniapp=True)
        self.add_item(msg_id=456, miniapp=True)
        judgement._clear_tiandao_pending_for_success(
            "天道裁决 真相大白 对象 【RouteUser】 已完成本轮自证", self.event(-1002, 200), self.now,
        )
        self.assertEqual(2, len(judgement._get_pending_map()))

    def test_delayed_broadcast_from_before_the_challenge_cannot_complete_it(self):
        self.add_item(miniapp=True)
        event = self.event(-1002, 200)
        event.date = datetime.fromtimestamp(self.now - 600, timezone.utc)
        judgement._clear_tiandao_pending_for_success(
            "天道裁决 真相大白 对象 【RouteUser】 已完成本轮自证", event, self.now,
        )
        self.assertEqual(1, len(judgement._get_pending_map()))

    async def test_reply_context_does_not_claim_a_message_in_a_different_chat(self):
        with state_module.use_identity(self.identity_id):
            state_module.state["my_msg_ids"] = {(-1001, 123): self.now}
        event = self.event(-1002, 200, reply_id=123)
        event.get_reply_message = AsyncMock(return_value=None)
        with patch.object(judgement, "_find_message_log_entry_by_msg_id", return_value=None):
            context = await judgement._find_reply_identity_context(event)
        self.assertIsNone(context["identity_id"])

    async def test_unknown_chat_does_not_claim_an_arbitrary_log_entry(self):
        event = self.event(0, 200, reply_id=123)
        event.get_reply_message = AsyncMock(return_value=None)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "2026-09-07.log").write_text(json.dumps({
                "message_id": 123, "chat_id": -1001, "sender_id": self.identity_id,
            }) + "\n", encoding="utf-8")
            with patch.object(judgement, "MESSAGES_DIR", directory):
                context = await judgement._find_reply_identity_context(event)
        self.assertIsNone(context["identity_id"])

    def test_exact_sender_identity_precedes_another_channels_owner_account(self):
        account_id = self.identity_id + 1
        state_module.set_identity_account(self.identity_id, account_id)
        state_module.ensure_identity_registered(account_id)
        self.assertEqual(account_id, judgement._find_identity_id_by_sender_id(account_id))

    def test_shared_account_without_exact_sender_cannot_choose_a_channel(self):
        account_id = self.identity_id + 10
        other_identity = self.identity_id + 1
        state_module.ensure_identity_registered(other_identity)
        state_module.set_identity_account(self.identity_id, account_id)
        state_module.set_identity_account(other_identity, account_id)
        self.assertIsNone(judgement._find_identity_id_by_sender_id(account_id))

    async def test_prompt_cannot_requeue_a_deleted_identity(self):
        for miniapp in (False, True):
            with self.subTest(miniapp=miniapp):
                state_module.ensure_identity_registered(self.identity_id)
                judgement._set_pending_map({})
                text = "【天道审判 · 挂机嫌疑】\n对象 【RouteUser】\n"
                if miniapp:
                    text += "Mini App 拖动验证\nhttps://t.me/fanrenxiuxian_bot?startapp=rpt_TEST"
                else:
                    text += "请计算图中结果，自证清白。\n文本题面：炼制玄铁剑消耗灵石 加 十 等于？\n阵眼口令：TEST\n回复指令：.自证"

                async def resolve(*_args, **_kwargs):
                    state_module.remove_identity(self.identity_id)
                    return {"identity_id": self.identity_id} if miniapp else self.identity_id

                resolver = "_resolve_tiandao_identity_context" if miniapp else "_resolve_tiandao_identity_id"
                with patch.object(judgement, resolver, new=AsyncMock(side_effect=resolve)):
                    await judgement.handle_tiandao_judgement_prompt(text, self.now, self.event(-1001, 123))
                self.assertEqual({}, judgement._get_pending_map())

    async def test_terminal_miniapp_prompt_is_not_requeued_after_identity_lookup(self):
        async def resolve(*_args, **_kwargs):
            judgement._mark_miniapp_terminal_event("-1001:123:rpt_TEST", self.now)
            return {"identity_id": self.identity_id}

        with patch.object(judgement, "_resolve_tiandao_identity_context", new=AsyncMock(side_effect=resolve)):
            await judgement.handle_tiandao_judgement_prompt(
                "【天道审判 · 挂机嫌疑】\n对象 【RouteUser】\nMini App 拖动验证\n"
                "https://t.me/fanrenxiuxian_bot?startapp=rpt_TEST",
                self.now, self.event(-1001, 123),
            )
        self.assertEqual({}, judgement._get_pending_map())

    async def test_prompt_cannot_requeue_after_toggle_off_during_identity_lookup(self):
        for miniapp in (False, True):
            with self.subTest(miniapp=miniapp):
                state_module.state["tiandao_judgement_enabled"] = True
                judgement._set_pending_map({})
                text = "【天道审判 · 挂机嫌疑】\n对象 【RouteUser】\n"
                if miniapp:
                    text += "Mini App 拖动验证\nhttps://t.me/fanrenxiuxian_bot?startapp=rpt_TEST"
                else:
                    text += "请计算图中结果，自证清白。\n文本题面：炼制玄铁剑消耗灵石 加 十 等于？\n阵眼口令：TEST\n回复指令：.自证"
                    self.assertIsNotNone(judgement.parse_tiandao_judgement_prompt(text))

                async def resolve(*_args, **_kwargs):
                    state_module.state["tiandao_judgement_enabled"] = False
                    return {"identity_id": self.identity_id} if miniapp else self.identity_id

                resolver = "_resolve_tiandao_identity_context" if miniapp else "_resolve_tiandao_identity_id"
                with patch.object(judgement, resolver, new=AsyncMock(side_effect=resolve)):
                    await judgement.handle_tiandao_judgement_prompt(text, self.now, self.event(-1001, 123))
                self.assertEqual({}, judgement._get_pending_map())

    async def test_button_actions_stop_when_disabled_during_fetch_or_between_clicks(self):
        for boundary in ("fetch", "click"):
            with self.subTest(boundary=boundary):
                state_module.state["tiandao_judgement_enabled"] = True

                async def click(*_args):
                    if boundary == "click":
                        state_module.state["tiandao_judgement_enabled"] = False

                message = SimpleNamespace(
                    id=123, chat_id=-1001,
                    buttons=[[SimpleNamespace(text="A"), SimpleNamespace(text="B")]],
                    click=AsyncMock(side_effect=click),
                )

                async def fetch(*_args, **_kwargs):
                    if boundary == "fetch":
                        state_module.state["tiandao_judgement_enabled"] = False
                    return message

                client = SimpleNamespace(get_messages=AsyncMock(side_effect=fetch))
                with (
                    patch.object(judgement, "_get_identity_client_for_rpc", return_value=(0, client)),
                    patch.object(judgement.asyncio, "sleep", new=AsyncMock()),
                ):
                    await judgement._click_tiandao_judgement_buttons(
                        SimpleNamespace(message=message, chat_id=-1001), self.identity_id, ["A", "B"],
                    )
                self.assertEqual(0 if boundary == "fetch" else 1, message.click.await_count)

    async def test_button_coordinates_are_taken_from_the_refreshed_message(self):
        old_message = SimpleNamespace(
            id=123, chat_id=-1001, buttons=[[SimpleNamespace(text="A"), SimpleNamespace(text="B")]],
        )
        current_message = SimpleNamespace(
            id=123, chat_id=-1001, buttons=[[SimpleNamespace(text="B"), SimpleNamespace(text="A")]],
            click=AsyncMock(),
        )
        client = SimpleNamespace(get_messages=AsyncMock(return_value=current_message))
        with (
            patch.object(judgement, "_get_identity_client_for_rpc", return_value=(0, client)),
            patch.object(judgement.asyncio, "sleep", new=AsyncMock()),
        ):
            await judgement._click_tiandao_judgement_buttons(
                SimpleNamespace(message=old_message, chat_id=-1001), self.identity_id, ["B"],
            )
        current_message.click.assert_awaited_once_with(0, 0)

    async def test_button_fetch_must_return_the_same_message_and_chat(self):
        for msg_id, chat_id in ((456, -1001), (123, -1002)):
            with self.subTest(msg_id=msg_id, chat_id=chat_id):
                message = SimpleNamespace(
                    id=123, chat_id=-1001, buttons=[[SimpleNamespace(text="A")]],
                )
                fetched = SimpleNamespace(
                    id=msg_id, chat_id=chat_id, buttons=message.buttons, click=AsyncMock(),
                )
                client = SimpleNamespace(get_messages=AsyncMock(return_value=fetched))
                with (
                    patch.object(judgement, "_get_identity_client_for_rpc", return_value=(0, client)),
                    patch.object(judgement.asyncio, "sleep", new=AsyncMock()),
                ):
                    ok, _error = await judgement._click_tiandao_judgement_buttons(
                        SimpleNamespace(message=message, chat_id=-1001), self.identity_id, ["A"],
                    )
                self.assertFalse(ok)
                fetched.click.assert_not_awaited()

    def event(self, chat_id, msg_id, *, reply_id=0):
        return SimpleNamespace(
            id=msg_id, chat_id=chat_id,
            reply_to=SimpleNamespace(reply_to_msg_id=reply_id),
            date=datetime.fromtimestamp(self.now, timezone.utc),
        )
