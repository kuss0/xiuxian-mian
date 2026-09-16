import atexit
import asyncio
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
from model import message_log_recovery, runtime
from model import state as state_module
from model.features import concubine, passive_inbox, workflow_log
from tests.test_concubine_fragment_contract import panel as fragment_panel


def _read_workflow_events(tmpdir):
    events = []
    for path in Path(tmpdir).glob("**/*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


class ConcubineAffinityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._passive_stats_snapshot = copy.deepcopy(passive_inbox._passive_stats)
        self._observed_passive_snapshot = dict(passive_inbox._observed_passive_events)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.set_game_group_id(-1001680975844)
        state_module.set_global_enabled(True)
        clock = patch.object(concubine.time, "time", return_value=1_700_000_000.0)
        clock.start()
        self.addCleanup(clock.stop)
        passive_inbox._passive_stats = {
            "total": 0,
            "changed": 0,
            "skipped": 0,
            "modules": {},
            "skip_reasons": {},
            "recent": [],
        }
        passive_inbox._observed_passive_events = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        passive_inbox._passive_stats = self._passive_stats_snapshot
        passive_inbox._observed_passive_events = self._observed_passive_snapshot

    def _prepare_identity(self, *, affinity=1000, dream_due_at=1_700_000_600.0, tianji_due_at=1_699_999_000.0, sect_name="星宫", kind="道心侍妾"):
        send_as_id = 991101
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, 1101)
        state_module.update_send_as_profile(send_as_id, username="xinggong", sect_name=sect_name)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = True
            identity_state["concubine_tianji_enabled"] = True
            identity_state["concubine_heart_enabled"] = False
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_availability"] = "available"
            identity_state["concubine_name"] = "凌玉灵"
            identity_state["concubine_kind"] = kind
            identity_state["concubine_affinity"] = affinity
            identity_state["concubine_dream_due_at"] = dream_due_at
            identity_state["concubine_tianji_due_at"] = tianji_due_at
            identity_state["next_concubine_time"] = 0
        return send_as_id

    def _status_receipt(self, msg_id, now):
        return SimpleNamespace(id=msg_id, sent_at=now, send_started_at=now, chat_id=state_module.get_game_group_id())

    def _external_event(self, msg_id, now):
        state_module.set_game_bot_ids([88096001])
        return SimpleNamespace(id=msg_id, chat_id=state_module.get_game_group_id(),
                               sender_id=88096001, server_event_at=now)

    def _legacy_status_pending(self, root, now):
        return {(state_module.get_game_group_id(), root): {
            "cmd": config.CMD_CONCUBINE_STATUS, "family": "concubine_status",
            "chat_id": state_module.get_game_group_id(), "message_id": root,
            "account_id": 1101, "sent_at": now - 1,
        }}

    async def _manual_status(self, text, now, root, *, msg_id=None, passive=False):
        event = self._external_event(msg_id or root + 1, now)
        context = {"send_as_id": 991101, "account_id": 1101, "chat_id": event.chat_id,
                   "family": "concubine_status", "root_msg_id": root, "reply_to_msg_id": root,
                   "reply_to_command": config.CMD_CONCUBINE_STATUS, "reply_to_sender_id": 991101,
                   "reply_to_server_at": now - 1, "reply_to_command_edited": False, "sender_id": event.sender_id}
        if passive:
            return await passive_inbox.handle_passive_module_card(
                text, now=now, reply_context=context, event=event, event_type="message",
            )
        return await concubine.handle_concubine_status_reply(
            text, now, SimpleNamespace(id=root, chat_id=event.chat_id, sender_id=991101,
                                       raw_text=config.CMD_CONCUBINE_STATUS),
            matched_family="concubine_status", current_msg_id=event.id, current_chat_id=event.chat_id,
            observed_at=now, reply_context=context,
        )

    def _assert_status_query_sent(self, send):
        send.assert_awaited_once_with(
            config.CMD_CONCUBINE_STATUS, track=True, max_retry=0,
            reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC, send_as_id=991101,
            target_chat_id=-1001680975844, source_module=concubine.CONCUBINE_QUERY_SOURCE,
            op_id=ANY, operation_check=ANY,
        )
        self.assertEqual(state_module.state["concubine_status_query"]["op_id"], send.await_args.kwargs["op_id"])

    def _assert_gift_action_sent(self, send, command):
        send.assert_awaited_once_with(
            command, track=True, max_retry=0, reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC,
            send_as_id=991101, target_chat_id=-1001680975844, source_module="concubine_gift",
            op_id=ANY, operation_check=ANY,
        )

    def _assert_greet_action_sent(self, send):
        send.assert_awaited_once_with(
            config.CMD_CONCUBINE_DAILY_GREET, track=True, max_retry=0,
            reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC, send_as_id=991101,
            target_chat_id=-1001680975844, source_module="concubine_greet",
            op_id=ANY, operation_check=ANY,
        )

    def _assert_fragment_action_sent(self, send, kind):
        send.assert_awaited_once_with(
            config.CMD_CONCUBINE_DREAM if kind == "dream" else config.CMD_CONCUBINE_PUZZLE,
            track=True, max_retry=0, reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC,
            send_as_id=991101, target_chat_id=-1001680975844, source_module="concubine_fragments",
            op_id=ANY, operation_check=ANY, **({"priority": "chain"} if kind == "puzzle" else {}),
        )

    def _assert_voyage_action_sent(self, send, command):
        send.assert_awaited_once_with(
            command, track=True, max_retry=0, reply_timeout=concubine.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC,
            send_as_id=991101, target_chat_id=-1001680975844, source_module="concubine_voyage",
            op_id=ANY, operation_check=ANY, priority="chain",
        )

    def _assert_tianji_action_sent(self, send):
        send.assert_awaited_once_with(
            config.CMD_CONCUBINE_TIANJI, track=True, max_retry=0,
            reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC, send_as_id=991101,
            target_chat_id=-1001680975844, source_module="concubine_tianji",
            op_id=ANY, operation_check=ANY,
        )

    async def _start_owned_tianji_action(self, identity_id, root, now, *, partner=None):
        state_module.set_game_bot_ids([88090001])
        with state_module.use_identity(identity_id) as identity_state:
            identity_state.update(concubine_phase="idle", concubine_tianji_msg_id=0,
                                  concubine_last_snapshot_at=now - 1, concubine_tianji_due_at=now - 1)
            if partner:
                identity_state["concubine_name"] = partner
            with patch.object(concubine, "save_state"), patch.object(concubine.time, "time", return_value=now), patch.object(
                concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(root, now)),
            ):
                self.assertTrue(await concubine._send_tianji_command(now))

    def _voyage_reply_metadata(self, msg_id, now):
        return dict(current_msg_id=msg_id, current_chat_id=-1001680975844,
                    observed_at=now, reply_context={"sender_id": 88090001})

    async def _start_owned_voyage_action(self, identity_id, kind, root, now, *, partner=None):
        state_module.set_game_bot_ids([88090001])
        with state_module.use_identity(identity_id) as identity_state:
            identity_state.update(concubine_phase="idle", concubine_voyage_msg_id=0,
                                  concubine_voyage_retry_count=0, concubine_last_snapshot_at=now - 1)
            if partner:
                identity_state["concubine_name"] = partner
            if kind == "voyage_return":
                identity_state.update(concubine_voyage_status="returned", concubine_voyage_return_at=now - 1)
                identity_state["concubine_voyage_route"] = identity_state.get("concubine_voyage_route") or config.CONCUBINE_VOYAGE_DEFAULT_ROUTE
            else:
                identity_state.update(concubine_voyage_status="idle", concubine_voyage_return_at=0)
            with patch.object(concubine, "save_state"), patch.object(concubine.time, "time", return_value=now), patch.object(
                concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(root, now)),
            ):
                self.assertTrue(await getattr(concubine, f"_send_{kind}_command")(now))

    async def _start_owned_fragment_action(self, identity_id, kind, root, now):
        state_module.set_game_bot_ids([88088001])
        with state_module.use_identity(identity_id) as identity_state:
            identity_state.update(concubine_phase="idle", concubine_dream_msg_id=0, concubine_puzzle_msg_id=0,
                                  concubine_last_snapshot_at=now - 1)
            if kind == "dream":
                identity_state["concubine_dream_due_at"] = now - 1
            with patch.object(concubine, "save_state"), patch.object(concubine, "send_audit_log", new=AsyncMock()):
                if kind == "puzzle":
                    xutian = identity_state["concubine_fragment_xutian_count"]
                    cangkun = identity_state["concubine_fragment_cangkun_count"]
                    with patch.object(concubine.time, "time", return_value=now), patch.object(
                        concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(root - 10, now)),
                    ):
                        self.assertTrue(await concubine._send_fragment_command(now))
                        self.assertTrue(await concubine.handle_concubine_fragment_reply(
                            fragment_panel(xutian, cangkun), now, self._status_receipt(root - 10, now),
                            current_msg_id=root - 9, current_chat_id=state_module.get_game_group_id(),
                            observed_at=now, reply_context={"sender_id": 88088001},
                        ))
                with patch.object(concubine.time, "time", return_value=now), patch.object(
                    concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(root, now)),
                ):
                    self.assertTrue(await getattr(concubine, f"_send_{kind}_command")(now))

    async def _start_owned_greet(self, identity_id, now):
        state_module.set_game_bot_ids([88083001])
        with state_module.use_identity(identity_id) as identity_state:
            identity_state.update(concubine_phase="idle", concubine_greet_msg_id=0,
                                  concubine_last_snapshot_at=now - 1)
            with patch.object(concubine, "save_state"), patch.object(
                concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(456, now)),
            ):
                self.assertTrue(await concubine._send_greet_command(now))

    async def _start_gift_bag(self, identity_id, now):
        state_module.set_game_bot_ids([88083001])
        with state_module.use_identity(identity_id) as identity_state:
            identity_state.update(
                concubine_phase="idle", concubine_gift_bag_msg_id=0, concubine_gift_msg_id=0,
                concubine_last_greet_day=concubine._local_day_key(now),
                concubine_last_panel_msg_id=500, concubine_last_snapshot_at=now - 1,
            )
            with patch.object(concubine, "save_state"), patch.object(
                concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(601, now)),
            ):
                self.assertTrue(await concubine._send_gift_bag_command(now))

    def _gift_reply_metadata(self, msg_id, now):
        return dict(current_msg_id=msg_id, current_chat_id=-1001680975844,
                    observed_at=now, reply_context={"sender_id": 88083001})

    async def _start_owned_gift(self, identity_id, now):
        await self._start_gift_bag(identity_id, now)
        with state_module.use_identity(identity_id), patch.object(concubine, "save_state"), patch.object(
            concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(701, now)),
        ):
            self.assertTrue(await concubine.handle_concubine_storage_bag_reply(
                "@xinggong 的储物袋\n材料:\n- 灵石 x 1,000\n", now,
                SimpleNamespace(raw_text=".储物袋", id=601, chat_id=-1001680975844),
                matched_family="storage_bag", **self._gift_reply_metadata(602, now),
            ))

    def _inbox_summaries(self, inbox_mock):
        return [str(call.kwargs.get("summary") or "") for call in inbox_mock.call_args_list]

    def _log_ts(self, ts):
        return datetime.fromtimestamp(float(ts), config.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")

    def _write_message_log(self, log_dir, entries, now):
        day = datetime.fromtimestamp(float(now), config.TZ_LOCAL).date().isoformat()
        log_path = Path(log_dir) / f"{day}.log"
        with log_path.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return log_path

    def test_message_log_topic_guard_matches_real_log_shapes(self):
        with patch.object(concubine, "get_game_topic_id", return_value=7310786):
            self.assertTrue(concubine._payload_matches_game_topic({"reply_to_msg_id": 9796379}))
            self.assertTrue(concubine._payload_matches_game_topic({"topic_id": 7310786, "reply_to_msg_id": 9796379}))
            self.assertTrue(concubine._payload_matches_game_topic({"topic_id": 0, "reply_to_msg_id": 7310786}))
            self.assertFalse(concubine._payload_matches_game_topic({"topic_id": 458347, "reply_to_msg_id": 9797504}))
            self.assertFalse(concubine._payload_matches_game_topic({"topic_id": 0, "reply_to_msg_id": 458347}))

    async def test_selfless_realm_records_observation_without_inventing_affinity_or_cd(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        text = (
            "@xinggong 【无我之境】\n"
            "在你心神即将被心魔吞噬的危急时刻，侍妾 凌玉灵 挺身而出，"
            "耗尽与你的所有情缘为你挡下此劫...\n"
            "你成功渡过此劫，修为未损。"
        )

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_affinity_event(text, now, self._external_event(1, now))
            self.assertTrue(handled)
            self.assertEqual(1000, state_module.state["concubine_affinity"])
            self.assertEqual("", state_module.state["concubine_tianji_last_error"])
            self.assertEqual(1_699_999_000.0, state_module.state["concubine_tianji_due_at"])
            self.assertEqual(0, state_module.state["next_concubine_time"])
            self.assertEqual("pending", state_module.state["concubine_external_observation"]["status"])

    async def test_moon_contract_defers_partner_projection_until_an_absolute_panel(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=0)
        state_module.get_identity_state(send_as_id)["concubine_auto_reacquire"] = True
        text = (
            "🌙 【月殿因果 · 南宫婉入世】\n\n"
            "道友 @xinggong 已以 LDC 契约请得 【南宫婉】 相随。\n"
            "原侍妾：【无】 已被替换。\n"
            "南宫婉不会被南陇侯夺走，也不会被洞府访客拐走。\n"
            "初始情缘：120，可用 .我的侍妾 查看。"
        )

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()):
            handled = await concubine.handle_concubine_affinity_event(
                text,
                now,
                self._external_event(99, now),
                require_identity_hint=True,
            )

        self.assertTrue(handled)
        self.assertEqual("凌玉灵", state_module.state["concubine_name"])
        self.assertEqual("道心侍妾", state_module.state["concubine_kind"])
        self.assertEqual(0, state_module.state["concubine_affinity"])
        self.assertEqual("available", state_module.state["concubine_availability"])
        self.assertTrue(state_module.state["concubine_auto_reacquire"])
        self.assertEqual("pending", state_module.state["concubine_external_observation"]["status"])
        self.assertEqual("南宫婉", state_module.state["concubine_external_observation"]["event"]["facts"]["partner"])

    async def test_nangong_wan_ignores_nanlong_loss_broadcast(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=180, kind="红尘道侣")
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_name"] = "南宫婉·月影"

        text = "@xinggong 的侍妾【南宫婉·月影】被南陇侯掳走。"
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as save_mock, \
             patch.object(concubine, "send_audit_log", new=AsyncMock()):
            handled = await concubine.handle_concubine_loss_broadcast(text, now, self._external_event(100, now))

        self.assertFalse(handled)
        save_mock.assert_not_called()
        self.assertEqual("南宫婉·月影", state_module.state["concubine_name"])
        self.assertEqual("available", state_module.state["concubine_availability"])
        self.assertEqual(180, state_module.state["concubine_affinity"])

    async def test_tianji_low_affinity_reply_requests_read_without_inventing_zero(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_TIANJI, id=123)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "tianji_pending"
            identity_state["concubine_tianji_msg_id"] = 123

        await self._start_owned_tianji_action(send_as_id, 123, now - 1)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_tianji_reply(
                "你与侍妾情缘未至，至少需 300 情缘方可代卜天机。",
                now,
                reply_to,
                matched_family="concubine_tianji",
                **self._voyage_reply_metadata(125, now),
            )
            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(1000, state_module.state["concubine_affinity"])
            self.assertEqual("unknown", state_module.state["concubine_availability"])
            self.assertEqual(now - 2, state_module.state["concubine_tianji_due_at"])
            self.assertEqual(now, state_module.state["next_concubine_time"])

    async def test_tianji_short_cooldown_reply_uses_real_wait_wording(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(dream_due_at=now + 3600, tianji_due_at=now - 1)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_TIANJI, id=124)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "tianji_pending"
            identity_state["concubine_tianji_msg_id"] = 124
            identity_state["concubine_tianji_last_error"] = "pending"
            identity_state["concubine_tianji_chain"] = "心劫前兆"
            identity_state["concubine_tianji_chain_due_at"] = now - 60

        await self._start_owned_tianji_action(send_as_id, 124, now - 1)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_tianji_reply(
                "天机链路尚未重铸，请在 24 秒后再试。",
                now,
                reply_to,
                matched_family="concubine_tianji",
                **self._voyage_reply_metadata(126, now),
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_tianji_msg_id"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual(now + 24 + config.CD_BUFFER_SEC, state_module.state["concubine_tianji_due_at"])
        self.assertEqual("", state_module.state["concubine_tianji_chain"])
        self.assertEqual(0, state_module.state["concubine_tianji_chain_due_at"])
        self.assertEqual(state_module.state["concubine_tianji_due_at"], state_module.state["next_concubine_time"])

    async def test_affinity_gain_preserves_tianji_block_until_absolute_panel(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（270/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_affinity_event(
                "@xinggong 侍妾【凌玉灵】向你微微颔首，你们的情缘增加了 30 点。",
                now,
                self._external_event(2, now),
            )
            self.assertTrue(handled)
            self.assertEqual(270, state_module.state["concubine_affinity"])
            self.assertEqual("情缘恢复中（270/300），暂缓天机代卜", state_module.state["concubine_tianji_last_error"])
            self.assertEqual(0, state_module.state["next_concubine_time"])
            self.assertEqual("pending", state_module.state["concubine_external_observation"]["status"])

    async def test_affinity_fallback_requires_identity_hint_before_name_match(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（270/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as mock_save:
            handled = await concubine.handle_concubine_affinity_event(
                "侍妾【凌玉灵】向你微微颔首，你们的情缘增加了 30 点。",
                now,
                self._external_event(3, now),
                require_identity_hint=True,
            )

        self.assertFalse(handled)
        mock_save.assert_not_called()
        self.assertEqual(270, state_module.state["concubine_affinity"])
        self.assertEqual("情缘恢复中（270/300），暂缓天机代卜", state_module.state["concubine_tianji_last_error"])

    async def test_affinity_fallback_accepts_explicit_identity_hint(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（270/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_affinity_event(
                "@xinggong 侍妾【凌玉灵】向你微微颔首，你们的情缘增加了 30 点。",
                now,
                self._external_event(4, now),
                require_identity_hint=True,
            )

        self.assertTrue(handled)
        self.assertEqual(270, state_module.state["concubine_affinity"])
        self.assertEqual("情缘恢复中（270/300），暂缓天机代卜", state_module.state["concubine_tianji_last_error"])
        self.assertEqual("pending", state_module.state["concubine_external_observation"]["status"])

    async def test_scheduler_sends_daily_greet_only_when_affinity_below_threshold(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        sent_msg = self._status_receipt(456, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_greet_action_sent(mock_send)
        self.assertEqual("greet_pending", state_module.state["concubine_phase"])
        self.assertEqual(456, state_module.state["concubine_greet_msg_id"])

    async def test_scheduler_does_not_greet_when_affinity_reaches_threshold(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=300, dream_due_at=now + 3600, tianji_due_at=now + 600)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=30):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])

    async def test_scheduler_respects_future_next_time_even_if_active_due_is_stale(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["next_concubine_time"] = now + 300
            identity_state["concubine_last_snapshot_at"] = now - 3600

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()

    async def test_scheduler_uses_cached_snapshot_for_dream_when_snapshot_is_stale(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_snapshot_at"] = now - 24 * 3600

        sent_msg = self._status_receipt(987, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_fragment_action_sent(mock_send, "dream")
        self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        self.assertEqual(987, state_module.state["concubine_dream_msg_id"])
        self.assertEqual(0, state_module.state["concubine_status_msg_id"])

    async def test_scheduler_uses_cached_snapshot_for_tianji_when_snapshot_is_stale(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_panel_msg_id"] = 123
            identity_state["concubine_last_snapshot_at"] = now - 24 * 3600

        sent_msg = self._status_receipt(989, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_tianji_action_sent(mock_send)
        self.assertEqual("tianji_pending", state_module.state["concubine_phase"])
        self.assertEqual(989, state_module.state["concubine_tianji_msg_id"])
        self.assertEqual(0, state_module.state["concubine_status_msg_id"])

    async def test_scheduler_allows_tianji_when_snapshot_is_fresh(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_panel_msg_id"] = 123
            identity_state["concubine_last_snapshot_at"] = now

        sent_msg = self._status_receipt(990, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_tianji_action_sent(mock_send)
        self.assertEqual("tianji_pending", state_module.state["concubine_phase"])
        self.assertEqual(990, state_module.state["concubine_tianji_msg_id"])

    async def test_scheduler_recovers_owned_tianji_success_from_official_log(self):
        now = 1_700_000_000.0
        sent_at = now - 3 * 3600
        reply_at = sent_at + 2
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_due_at"] = now - 1
            identity_state["next_concubine_time"] = now - 1

        await self._start_owned_tianji_action(send_as_id, 1001, sent_at)
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(sent_at),
                        "event_type": "sent",
                        "message_id": 1001,
                        "sender_id": send_as_id,
                        "text": config.CMD_CONCUBINE_TIANJI,
                    },
                    {
                        "ts": self._log_ts(reply_at),
                        "event_type": "message",
                        "message_id": 1002,
                        "reply_to_msg_id": 1001,
                        "chat_id": -1001680975844,
                        "sender_id": 88090001,
                        "sender_is_bot": True,
                        "server_event_at": reply_at,
                        "text": "【天机代卜链】\n侍妾【凌玉灵】焚香推演，为你接引一缕天机。\n得卦【残图引路】：下一次 .入梦寻图 的残图片段掉率大幅提升。\n本次消耗：180修为。",
                    },
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(message_log_recovery, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
                 patch.object(concubine.random, "uniform", return_value=30):
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        expected_due = reply_at + config.CONCUBINE_TIANJI_CD_SEC + config.CD_BUFFER_SEC
        self.assertEqual(expected_due, state_module.state["concubine_tianji_due_at"])
        self.assertEqual("残图引路", state_module.state["concubine_tianji_chain"])
        self.assertEqual(expected_due, state_module.state["concubine_tianji_chain_due_at"])
        self.assertEqual(expected_due + 30, state_module.state["next_concubine_time"])

    async def test_scheduler_recovers_owned_tianji_cooldown_from_official_log(self):
        now = 1_700_000_000.0
        sent_at = now - 60
        reply_at = sent_at + 2
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_due_at"] = now - 1
            identity_state["next_concubine_time"] = now - 1

        await self._start_owned_tianji_action(send_as_id, 1003, sent_at)
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(sent_at),
                        "event_type": "sent",
                        "message_id": 1003,
                        "sender_id": send_as_id,
                        "text": config.CMD_CONCUBINE_TIANJI,
                    },
                    {
                        "ts": self._log_ts(reply_at),
                        "event_type": "message",
                        "message_id": 1004,
                        "reply_to_msg_id": 1003,
                        "chat_id": -1001680975844,
                        "sender_id": 88090001,
                        "sender_is_bot": True,
                        "server_event_at": reply_at,
                        "text": "天机链路尚未重铸，请在 9小时5分钟31秒 后再试。",
                    },
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(message_log_recovery, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
                 patch.object(concubine.random, "uniform", return_value=30):
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        expected_due = reply_at + 9 * 3600 + 5 * 60 + 31 + config.CD_BUFFER_SEC
        self.assertEqual(expected_due, state_module.state["concubine_tianji_due_at"])
        self.assertEqual(expected_due + 30, state_module.state["next_concubine_time"])


    async def test_scheduler_refreshes_status_when_heart_panel_is_stale_and_no_logged_panel_exists(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_heart_due_at"] = now - 1
            identity_state["concubine_last_panel_msg_id"] = 123
            identity_state["concubine_last_snapshot_at"] = now - concubine.CONCUBINE_HEART_PANEL_MAX_AGE_SEC - 1

        sent_msg = self._status_receipt(989, now)
        with tempfile.TemporaryDirectory() as tmpdir:
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
                await concubine.run_concubine_scheduler(now)

        self._assert_status_query_sent(mock_send)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(989, state_module.state["concubine_status_msg_id"])

    async def test_scheduler_refreshes_status_when_heart_panel_is_missing(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_heart_due_at"] = now - 1
            identity_state["concubine_last_panel_msg_id"] = 0
            identity_state["concubine_last_snapshot_at"] = now - 60

        sent_msg = self._status_receipt(989, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_status_query_sent(mock_send)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(989, state_module.state["concubine_status_msg_id"])

    async def test_scheduler_reuses_recent_status_panel_for_active_calibration(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_panel_msg_id"] = 9388001
            identity_state["concubine_last_snapshot_at"] = now - 60
            identity_state["concubine_tianji_last_error"] = "tianji_pending 等待回复超时，准备状态校准"

        sent_msg = self._status_receipt(990, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_tianji_action_sent(mock_send)
        self.assertEqual("tianji_pending", state_module.state["concubine_phase"])
        self.assertEqual(990, state_module.state["concubine_tianji_msg_id"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])

    async def test_tianji_send_unknown_keeps_original_operation_without_cooldown(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.time, "time", return_value=now), \
             patch.object(concubine.random, "uniform", return_value=300), \
             patch.object(concubine, "classify_game_send_block", return_value={"status": "none"}), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)) as mock_send:
            sent = await concubine._send_tianji_command(now)

        self.assertFalse(sent)
        self._assert_tianji_action_sent(mock_send)
        self.assertEqual("tianji_pending", state_module.state["concubine_phase"])
        self.assertEqual("unknown", state_module.state["concubine_tianji_action"]["status"])
        self.assertIn("发送状态未知", state_module.state["concubine_tianji_last_error"])
        self.assertEqual(now - 1, state_module.state["concubine_tianji_due_at"])

    async def test_status_command_reuses_recent_panel_instead_of_resending(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_panel_msg_id"] = 9388001
            identity_state["concubine_last_snapshot_at"] = now - 60
            identity_state["concubine_tianji_last_error"] = "天机代卜等待回复超时，准备状态校准"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=45), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            sent = await concubine._send_status_command(now)

        self.assertFalse(sent)
        mock_send.assert_not_awaited()
        self.assertEqual(0, state_module.state["concubine_status_msg_id"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual(now + 45, state_module.state["next_concubine_time"])

    async def test_scheduler_allows_replayable_dream_during_summary_due(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now + 3600)
        sent_msg = self._status_receipt(991, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_snapshot_at"] = now - 24 * 3600
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "summary_due"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_fragment_action_sent(mock_send, "dream")
        self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        self.assertEqual(991, state_module.state["concubine_dream_msg_id"])

    async def test_scheduler_defers_dream_command_during_waiting_summary_window(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "waiting_summary"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=90):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(now + 90, state_module.state["next_concubine_time"])
        self.assertIn("入梦寻图等待闭关/元婴结算", state_module.state["concubine_last_error"])

    async def test_dream_send_blocked_by_global_is_deferred_without_error(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_snapshot_at"] = now

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as save_mock, \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)) as mock_send, \
             patch.object(concubine, "classify_game_send_block", side_effect=[{"status": "none"}, {"status": "unsent", "code": "global_disabled", "at": now}]), \
             patch.object(concubine.time, "time", return_value=now), \
             patch.object(concubine.random, "uniform", return_value=600):
            await concubine.run_concubine_scheduler(now)

        self._assert_fragment_action_sent(mock_send, "dream")
        save_mock.assert_called()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual("", state_module.state["concubine_last_error"])
        self.assertEqual(now - 1, state_module.state["concubine_dream_due_at"])
        self.assertEqual(now + 600, state_module.state["concubine_fragment_actions"]["dream"]["retry_at"])
        self.assertEqual(now + 600, state_module.state["next_concubine_time"])

    async def test_scheduler_calibrates_status_after_dream_pending_timeout_before_retry(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now + 3600)
        sent_msg = self._status_receipt(992, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_error"] = "dream_pending 等待回复超时，已转状态校准"
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_status_query_sent(mock_send)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(992, state_module.state["concubine_status_msg_id"])

    async def test_scheduler_prioritizes_tianji_before_dream_when_both_are_due(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now - 1)
        sent_msg = self._status_receipt(993, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_tianji_action_sent(mock_send)
        self.assertEqual("tianji_pending", state_module.state["concubine_phase"])
        self.assertEqual(993, state_module.state["concubine_tianji_msg_id"])
        self.assertEqual(0, state_module.state["concubine_dream_msg_id"])

    async def test_tianji_sent_without_reply_holds_operation_across_later_ticks(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        sent_msg = self._status_receipt(994, now)

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_chain"] = "心劫前兆"
            identity_state["concubine_tianji_chain_due_at"] = now - 60

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_tianji_action_sent(mock_send)
        self.assertEqual(now - 1, state_module.state["concubine_tianji_due_at"])
        operation_id = state_module.state["concubine_tianji_action"]["op_id"]

        timeout_now = now + config.CONCUBINE_PHASE_TIMEOUT_SEC + 1
        with tempfile.TemporaryDirectory() as tmpdir, \
             state_module.use_identity(send_as_id), \
             patch.object(message_log_recovery, "MESSAGES_DIR", tmpdir), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine.random, "uniform", return_value=60), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as timeout_send:
            await concubine.run_concubine_scheduler(timeout_now)
            await concubine.run_concubine_scheduler(timeout_now + 86400)

        timeout_send.assert_not_awaited()
        self.assertEqual("tianji_pending", state_module.state["concubine_phase"])
        self.assertEqual(now - 1, state_module.state["concubine_tianji_due_at"])
        self.assertEqual("sent", state_module.state["concubine_tianji_action"]["status"])
        self.assertEqual(operation_id, state_module.state["concubine_tianji_action"]["op_id"])
        self.assertEqual(994, state_module.state["concubine_tianji_msg_id"])

    async def test_scheduler_calibrates_status_after_tianji_pending_timeout_before_retry(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        sent_msg = self._status_receipt(994, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "tianji_pending 等待回复超时，已转状态校准"
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_status_query_sent(mock_send)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(994, state_module.state["concubine_status_msg_id"])

    async def test_status_reply_clears_tianji_timeout_calibration_error_before_retry(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        panel_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n"
            "情缘值: 1000\n"
            "当前誓约: 无\n"
            "入梦寻图冷却: 60分钟\n"
            "共历心劫冷却: 可施展\n"
            "天机代卜冷却: 可施展\n"
            "命令: .每日问安、.天机代卜"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "status_pending"
            identity_state["concubine_status_msg_id"] = 994
            identity_state["pending_tasks"] = self._legacy_status_pending(994, now)
            identity_state["concubine_tianji_last_error"] = "tianji_pending 等待回复超时，已转状态校准"
            identity_state["next_concubine_time"] = now

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(995, now))) as mock_send:
            handled = await self._manual_status(panel_text, now, 994, msg_id=996)
            self.assertTrue(handled)
            self.assertEqual("", state_module.state["concubine_tianji_last_error"])
            self.assertEqual("idle", state_module.state["concubine_phase"])

            await concubine.run_concubine_scheduler(now)

        self._assert_tianji_action_sent(mock_send)
        self.assertEqual("tianji_pending", state_module.state["concubine_phase"])
        self.assertEqual(995, state_module.state["concubine_tianji_msg_id"])
        self.assertEqual(0, state_module.state["concubine_status_msg_id"])

    async def test_status_reply_treats_phaseful_summary_as_consumed_and_rechecks(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "status_pending"
            identity_state["concubine_status_msg_id"] = 533650
            identity_state["pending_tasks"] = self._legacy_status_pending(533650, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "console_log"), \
             patch.object(concubine.random, "uniform", return_value=60):
            handled = await self._manual_status(
                "【元婴闭关结算】\n你的元婴在过去 12 小时内为你增加了 15600 点修为！",
                now, 533650,
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_status_msg_id"])
        self.assertEqual(now + 60, state_module.state["next_concubine_time"])
        self.assertEqual(now - 1, state_module.state["concubine_dream_due_at"])
        self.assertEqual(now - 1, state_module.state["concubine_tianji_due_at"])
        self.assertIn("触发闭关/元婴结算", state_module.state["concubine_last_error"])

    async def test_gift_status_summary_preserves_unproven_legacy_daily_attempt(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        today = concubine._local_day_key(now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "gift_status_pending"
            identity_state["concubine_gift_status_msg_id"] = 533650
            identity_state["pending_tasks"] = self._legacy_status_pending(533650, now)
            identity_state["concubine_last_greet_day"] = today
            identity_state["concubine_gift_attempt_day"] = today
            identity_state["concubine_last_gift_day"] = ""

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "console_log"), \
             patch.object(concubine.random, "uniform", return_value=60):
            handled = await self._manual_status(
                "【深度闭关总结】\n本次深度闭关，你的修为最终变化了 5060 点！",
                now, 533650,
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_gift_status_msg_id"])
        self.assertEqual(today, state_module.state["concubine_gift_attempt_day"])
        self.assertEqual("", state_module.state["concubine_last_gift_day"])
        self.assertFalse(concubine._is_gift_recovery_due(now + 60))
        self.assertIn("触发闭关/元婴结算", state_module.state["concubine_last_error"])

    async def test_scheduler_defers_tianji_command_during_phaseful_summary_window(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_snapshot_at"] = now
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "waiting_summary"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=90):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(now + 90, state_module.state["next_concubine_time"])
        self.assertIn("天机代卜等待闭关/元婴结算", state_module.state["concubine_tianji_last_error"])

    async def test_scheduler_clears_stale_phaseful_summary_wait_errors_after_window_closes(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_error"] = "入梦寻图等待闭关/元婴结算，稍后处理"
            identity_state["concubine_tianji_last_error"] = "天机代卜等待闭关/元婴结算，稍后处理"
            identity_state["concubine_greet_last_error"] = "每日问安等待闭关/元婴结算，稍后处理"
            identity_state["concubine_gift_last_error"] = "赠予侍妾等待闭关/元婴结算，稍后处理"
            identity_state["concubine_voyage_last_error"] = "侍妾远航等待闭关/元婴结算，稍后处理"
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "idle"
            identity_state["yuanying_enabled"] = True
            identity_state["yuanying_phase"] = "idle"
            identity_state["next_concubine_time"] = now + 3600

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as save_mock, \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        save_mock.assert_called()
        self.assertEqual("", state_module.state["concubine_last_error"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual("", state_module.state["concubine_greet_last_error"])
        self.assertEqual("", state_module.state["concubine_gift_last_error"])
        self.assertEqual("", state_module.state["concubine_voyage_last_error"])

    async def test_dream_reply_keeps_due_tianji_on_short_chain(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 321

        await self._start_owned_fragment_action(send_as_id, "dream", 321, now - 1)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_dream_reply(
                "【入梦寻图】\n本次梦兆锁定：【虚天残图】 线路。\n你与侍妾【凌玉灵】共梦乱星海，获得 【虚天残图】 残纹 北阙残纹（新残纹）。\n当前进度：1/4。",
                now,
                SimpleNamespace(raw_text=config.CMD_CONCUBINE_DREAM, id=321),
                matched_family="concubine_dream", current_msg_id=322,
                current_chat_id=state_module.get_game_group_id(), observed_at=now, reply_context={"sender_id": 88088001},
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(now + 30, state_module.state["next_concubine_time"])
        self.assertLessEqual(state_module.state["concubine_tianji_due_at"], now)

    async def test_daily_greet_reply_marks_day_and_clears_block_at_threshold(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DAILY_GREET, id=456)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（270/300），暂缓天机代卜"
        await self._start_owned_greet(send_as_id, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_greet_reply(
                "侍妾【凌玉灵】向你微微颔首，你们的情缘增加了 30 点。",
                now,
                reply_to,
                matched_family="concubine_greet",
                **self._gift_reply_metadata(457, now),
            )

        self.assertTrue(handled)
        self.assertEqual(300, state_module.state["concubine_affinity"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual("", state_module.state["concubine_greet_last_error"])
        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_last_greet_day"])
        self.assertEqual(0, state_module.state["concubine_greet_msg_id"])
        self.assertEqual(0, state_module.state["concubine_greet_retry_count"])
        self.assertEqual("idle", state_module.state["concubine_phase"])

    async def test_daily_greet_repeat_reply_prevents_same_day_resend(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=120, dream_due_at=now + 3600, tianji_due_at=now - 1)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DAILY_GREET, id=456)
        await self._start_owned_greet(send_as_id, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_greet_reply(
                "今日已经问安过了，请勿过多打扰。你的心意她已收到。",
                now,
                reply_to,
                matched_family="concubine_greet",
                **self._gift_reply_metadata(457, now),
            )
        self.assertTrue(handled)
        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_last_greet_day"])
        self.assertEqual(0, state_module.state["concubine_greet_retry_count"])

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(789, now + 60))) as mock_send:
            await concubine.run_concubine_scheduler(now + 60)
        self._assert_status_query_sent(mock_send)
        self.assertEqual("gift_status_pending", state_module.state["concubine_phase"])

    async def test_scheduler_after_daily_greet_requests_status_for_gift_recovery(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = concubine._local_day_key(now)

        sent_msg = self._status_receipt(501, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_status_query_sent(mock_send)
        self.assertEqual("gift_status_pending", state_module.state["concubine_phase"])
        self.assertEqual(501, state_module.state["concubine_gift_status_msg_id"])
        self.assertEqual("", state_module.state["concubine_gift_attempt_day"])

    async def test_scheduler_uses_fresh_cached_panel_for_gift_recovery(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        today = concubine._local_day_key(now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = today
            identity_state["concubine_last_panel_msg_id"] = 9387319
            identity_state["concubine_last_snapshot_at"] = now - 60

        sent_msg = self._status_receipt(601, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_gift_action_sent(mock_send, concubine.CMD_STORAGE_BAG)
        self.assertEqual("gift_bag_pending", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_gift_status_msg_id"])
        self.assertEqual(601, state_module.state["concubine_gift_bag_msg_id"])
        self.assertEqual("", state_module.state["concubine_gift_attempt_day"])

    async def test_scheduler_refreshes_status_for_gift_recovery_when_cached_panel_is_stale(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = concubine._local_day_key(now)
            identity_state["concubine_last_panel_msg_id"] = 9387319
            identity_state["concubine_last_snapshot_at"] = now - concubine.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC - 1

        sent_msg = self._status_receipt(501, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_status_query_sent(mock_send)
        self.assertEqual("gift_status_pending", state_module.state["concubine_phase"])
        self.assertEqual(501, state_module.state["concubine_gift_status_msg_id"])

    async def test_gift_status_unknown_retains_query_without_marking_day(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        today = concubine._local_day_key(now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = today

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=90), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_status_query_sent(mock_send)
        self.assertEqual("gift_status_pending", state_module.state["concubine_phase"])
        self.assertEqual("", state_module.state["concubine_last_gift_day"])
        self.assertEqual("", state_module.state["concubine_gift_attempt_day"])
        self.assertEqual(now + concubine.CONCUBINE_PHASE_TIMEOUT_SEC, state_module.state["next_concubine_time"])
        self.assertEqual("unknown", state_module.state["concubine_status_query"]["status"])

    async def test_gift_bag_unknown_keeps_ownership_without_marking_day(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        today = concubine._local_day_key(now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = today
            identity_state["concubine_last_panel_msg_id"] = 9387319
            identity_state["concubine_last_snapshot_at"] = now - 60

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=120), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_gift_action_sent(mock_send, concubine.CMD_STORAGE_BAG)
        self.assertEqual("gift_bag_pending", state_module.state["concubine_phase"])
        self.assertEqual("", state_module.state["concubine_last_gift_day"])
        self.assertEqual(now + concubine.CONCUBINE_PHASE_TIMEOUT_SEC, state_module.state["next_concubine_time"])
        self.assertEqual("unknown", state_module.state["concubine_gift_actions"]["gift_bag"]["status"])
        self.assertEqual("", state_module.state["concubine_gift_attempt_day"])

    async def test_gift_query_blocks_duplicate_recovery_chain_start(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        today = concubine._local_day_key(now)
        sent_msg = self._status_receipt(501, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = today

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_status_query_sent(mock_send)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_gift_status_msg_id"] = 0
            identity_state["next_concubine_time"] = now

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now + 1)

        mock_send.assert_not_awaited()
        self.assertEqual("", state_module.state["concubine_gift_attempt_day"])
        self.assertEqual("sent", state_module.state["concubine_status_query"]["status"])

    async def test_concurrent_scheduler_starts_only_one_gift_recovery_chain(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(
            affinity=240,
            dream_due_at=now + 3600,
            tianji_due_at=now + 3600,
        )
        today = concubine._local_day_key(now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = today

        sent_msg = self._status_receipt(501, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await asyncio.gather(
                concubine.run_concubine_scheduler(now),
                concubine.run_concubine_scheduler(now),
            )

        self._assert_status_query_sent(mock_send)
        self.assertEqual("gift_status_pending", state_module.state["concubine_phase"])
        self.assertEqual(501, state_module.state["concubine_gift_status_msg_id"])
        self.assertEqual("", state_module.state["concubine_gift_attempt_day"])

    async def test_restart_preserves_legacy_gift_pending_without_resending(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(
            affinity=240,
            dream_due_at=now + 3600,
            tianji_due_at=now + 3600,
        )
        today = concubine._local_day_key(now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = today
            identity_state["concubine_gift_attempt_day"] = today
            identity_state["concubine_phase"] = "gift_pending"
            identity_state["concubine_gift_msg_id"] = 701
            identity_state["concubine_gift_amount"] = 60
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            concubine.restore_concubine_runtime(now)
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("gift_pending", state_module.state["concubine_phase"])
        self.assertEqual(701, state_module.state["concubine_gift_msg_id"])
        self.assertEqual(60, state_module.state["concubine_gift_amount"])
        self.assertEqual(today, state_module.state["concubine_gift_attempt_day"])

    def test_concubine_persisted_message_ids_route_to_reply_families(self):
        state = state_module.new_identity_state()
        state["concubine_gift_status_msg_id"] = 501
        state["concubine_gift_bag_msg_id"] = 601
        state["concubine_gift_msg_id"] = 701
        state["concubine_tianji_msg_id"] = 801
        state["concubine_voyage_msg_id"] = 901

        self.assertEqual("concubine_status", runtime._get_special_tracked_message_family(state, 501))
        self.assertEqual("storage_bag", runtime._get_special_tracked_message_family(state, 601))
        self.assertEqual("concubine_gift", runtime._get_special_tracked_message_family(state, 701))
        self.assertEqual("concubine_tianji", runtime._get_special_tracked_message_family(state, 801))
        self.assertEqual("concubine_voyage", runtime._get_special_tracked_message_family(state, 901))

    async def test_gift_status_and_bag_reply_sends_exact_stone_amount(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        status_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n"
            "情缘值: 240\n"
            "当前誓约: 无\n"
            "入梦寻图冷却: 60分钟\n"
            "共历心劫冷却: 可施展\n"
            "天机代卜冷却: 可施展\n"
            "命令: .每日问安、.天机代卜"
        )
        bag_text = (
            "@xinggong 的储物袋\n"
            "法宝/丹药/杂物:\n"
            "- 灵石 x 1,000\n"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = concubine._local_day_key(now)
        state_module.set_game_bot_ids([88083001])
        with state_module.use_identity(send_as_id), patch.object(concubine, "save_state"), patch.object(
            concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(501, now)),
        ):
            self.assertTrue(await concubine._send_gift_status_command(now))

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(601, now))) as mock_send:
            handled = await concubine.handle_concubine_status_reply(
                status_text,
                now,
                SimpleNamespace(raw_text=config.CMD_CONCUBINE_STATUS, id=501),
                matched_family="concubine_status",
                **self._gift_reply_metadata(502, now),
            )
        self.assertTrue(handled)
        self._assert_gift_action_sent(mock_send, ".储物袋")
        self.assertEqual("gift_bag_pending", state_module.state["concubine_phase"])
        self.assertEqual(601, state_module.state["concubine_gift_bag_msg_id"])

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(701, now))) as mock_send:
            handled = await concubine.handle_concubine_storage_bag_reply(
                bag_text,
                now,
                SimpleNamespace(raw_text=".储物袋", id=601),
                matched_family="storage_bag",
                **self._gift_reply_metadata(602, now),
            )
        self.assertTrue(handled)
        self._assert_gift_action_sent(mock_send, f"{config.CMD_CONCUBINE_GIFT_STONE} 灵石*60")
        self.assertEqual("gift_pending", state_module.state["concubine_phase"])
        self.assertEqual(60, state_module.state["concubine_gift_amount"])

    async def test_unowned_bag_reply_cannot_resume_a_cleared_gift_phase(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=60, dream_due_at=now + 3600, tianji_due_at=now - 1)
        bag_text = (
            "@xinggong 的储物袋\n"
            "材料:\n"
            "- 灵石 x 5,222\n"
        )
        today = concubine._local_day_key(now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_last_greet_day"] = today
            identity_state["concubine_gift_attempt_day"] = today
            identity_state["concubine_gift_status_msg_id"] = 0
            identity_state["concubine_gift_bag_msg_id"] = 0

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=701, sent_at=now))) as mock_send:
            handled = await concubine.handle_concubine_storage_bag_reply(
                bag_text,
                now,
                SimpleNamespace(raw_text=".储物袋", id=601),
                matched_family="storage_bag",
            )

        self.assertFalse(handled)
        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_gift_amount"])

    async def test_gift_success_updates_affinity_and_unblocks_tianji(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        await self._start_owned_gift(send_as_id, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "gift_pending"
            identity_state["concubine_gift_msg_id"] = 701
            identity_state["concubine_gift_amount"] = 60
            identity_state["concubine_last_greet_day"] = concubine._local_day_key(now)
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（240/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "apply_storage_bag_item_deltas", return_value=True), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_gift_reply(
                "你将【灵石】x60 赠予了侍妾【凌玉灵】，你们的情缘增加了 60 点！",
                now,
                SimpleNamespace(raw_text=f"{config.CMD_CONCUBINE_GIFT_STONE} 灵石*60", id=701),
                matched_family="concubine_gift",
                **self._gift_reply_metadata(702, now),
            )

        self.assertTrue(handled)
        self.assertEqual(300, state_module.state["concubine_affinity"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_last_gift_day"])
        self.assertEqual(0, state_module.state["concubine_gift_msg_id"])
        self.assertEqual(0, state_module.state["concubine_gift_amount"])
        self.assertEqual("idle", state_module.state["concubine_phase"])

    async def test_gift_insufficient_stones_suppresses_attempt_without_claiming_success(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        await self._start_gift_bag(send_as_id, now)
        bag_text = (
            "@xinggong 的储物袋\n"
            "材料:\n"
            "- 灵石 x 10\n"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "gift_bag_pending"
            identity_state["concubine_gift_bag_msg_id"] = 601
            identity_state["concubine_last_greet_day"] = concubine._local_day_key(now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            handled = await concubine.handle_concubine_storage_bag_reply(
                bag_text,
                now,
                SimpleNamespace(raw_text=".储物袋", id=601),
                matched_family="storage_bag",
                **self._gift_reply_metadata(602, now),
            )
        self.assertTrue(handled)
        mock_send.assert_not_awaited()
        self.assertEqual("", state_module.state["concubine_last_gift_day"])
        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_gift_attempt_day"])
        self.assertIn("灵石不足", state_module.state["concubine_gift_last_error"])

    async def test_gift_command_without_owned_inventory_is_not_sent(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "gift_pending"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=150), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)) as mock_send:
            sent = await concubine._send_gift_command(now, 60)

        self.assertFalse(sent)
        mock_send.assert_not_awaited()
        self.assertEqual("gift_pending", state_module.state["concubine_phase"])
        self.assertEqual("", state_module.state["concubine_last_gift_day"])
        self.assertEqual(0, state_module.state["next_concubine_time"])

    async def test_daily_greet_summary_keeps_unknown_operation_without_retry_or_day_marker(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=0, dream_due_at=now + 3600, tianji_due_at=now - 1)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DAILY_GREET, id=456)
        await self._start_owned_greet(send_as_id, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=90):
            handled = await concubine.handle_concubine_greet_reply(
                "✨ 天道感应：检测到 @xinggong 功成圆满，神魂正在归位...",
                now,
                reply_to,
                matched_family="deep_retreat",
                **self._gift_reply_metadata(457, now),
            )

        self.assertFalse(handled)
        self.assertEqual("", state_module.state["concubine_last_greet_day"])
        self.assertEqual(0, state_module.state["concubine_greet_retry_count"])
        self.assertEqual("greet_pending", state_module.state["concubine_phase"])
        self.assertEqual(456, state_module.state["concubine_greet_msg_id"])
        self.assertEqual(now + concubine.CONCUBINE_PHASE_TIMEOUT_SEC, state_module.state["next_concubine_time"])
        self.assertEqual("sent", state_module.state["concubine_greet_action"]["status"])

    async def test_legacy_daily_greet_timeout_preserves_pending_without_fabricating_completion(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=0, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "greet_pending"
            identity_state["concubine_greet_msg_id"] = 456
            identity_state["concubine_greet_retry_count"] = 1
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine.random, "uniform", return_value=0):
            await concubine.run_concubine_scheduler(now)

        self.assertEqual("", state_module.state["concubine_last_greet_day"])
        self.assertEqual(1, state_module.state["concubine_greet_retry_count"])
        self.assertEqual("greet_pending", state_module.state["concubine_phase"])
        self.assertEqual(456, state_module.state["concubine_greet_msg_id"])
        self.assertEqual(now - 1, state_module.state["next_concubine_time"])

    async def test_scheduler_defers_daily_greet_during_deep_retreat_summary_wait(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=120, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "summary_due"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=90):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual(now + 90, state_module.state["next_concubine_time"])
        self.assertIn("等待闭关/元婴结算", state_module.state["concubine_greet_last_error"])

    async def test_non_star_palace_identity_does_not_daily_greet(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=120, dream_due_at=now + 3600, tianji_due_at=now - 1, sect_name="太一门")

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=0):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertIn("情缘不足", state_module.state["concubine_tianji_last_error"])

    async def test_scheduler_clears_stale_affinity_error_when_threshold_is_met(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1010, dream_due_at=now + 3600, tianji_due_at=now + 600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "情缘不足（0/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            await concubine.run_concubine_scheduler(now)

        self.assertEqual(1010, state_module.state["concubine_affinity"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual(now + 630, state_module.state["next_concubine_time"])


    async def test_status_snapshot_does_not_clear_active_heart_prompt(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        panel_text = (
            "你的红尘道侣: 【若兰】 (状态: 随行中)\n\n"
            "她安静地陪伴着你，虽不通星宫秘法，却也可为你牵引第二期机缘。\n\n"
            "【第二期机缘】\n"
            "- 入梦寻图冷却: 430分钟\n"
            "- 共历心劫冷却: 可施展\n"
            "- 天机代卜冷却: 199分钟\n"
            "- 梦图拼片: 虚天 3/4 | 苍坤 1/4\n"
            "命令: .入梦寻图、.残图、.拼图、.共历心劫、.天机代卜"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_pending"
            identity_state["concubine_heart_msg_id"] = 9296119
            identity_state["concubine_heart_prompt_msg_id"] = 9296120
            identity_state["concubine_heart_round"] = 1
            identity_state["concubine_heart_due_at"] = now + 3600
            identity_state["next_concubine_time"] = now + 20

        with state_module.use_identity(send_as_id):
            before = copy.deepcopy(state_module.get_identity_state(send_as_id))
            parsed = concubine._parse_status_panel(panel_text, now)
            self.assertTrue(parsed)
            self.assertFalse(concubine._apply_status_snapshot(parsed, now + 5))
            self.assertEqual(before, state_module.get_identity_state(send_as_id))
            self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
            self.assertEqual(9296119, state_module.state["concubine_heart_msg_id"])
            self.assertEqual(9296120, state_module.state["concubine_heart_prompt_msg_id"])
            self.assertEqual(1, state_module.state["concubine_heart_round"])
            self.assertEqual(now + 20, state_module.state["next_concubine_time"])
            self.assertEqual(now + 3600, state_module.state["concubine_heart_due_at"])

    def test_status_panel_minute_cooldown_keeps_rounding_margin(self):
        now = 1_700_000_000.0

        self.assertEqual(
            now + 199 * 60 + 65,
            concubine._parse_wait_due_at("199分钟", now, coarse_minute_buffer=True),
        )
        self.assertEqual(
            now + 199 * 60 + 30 + 5,
            concubine._parse_wait_due_at("199分钟30秒", now, coarse_minute_buffer=True),
        )

        panel_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n"
            "- 入梦寻图冷却: 199分钟\n"
            "- 共历心劫冷却: 199分钟\n"
            "- 天机代卜冷却: 199分钟"
        )
        parsed = concubine._parse_status_panel(panel_text, now)
        self.assertEqual(now + 199 * 60 + 65, parsed["dream_due_at"])
        self.assertEqual(now + 199 * 60 + 65, parsed["heart_due_at"])
        self.assertEqual(now + 199 * 60 + 65, parsed["tianji_due_at"])

    async def test_status_snapshot_does_not_regress_future_tianji_cooldown(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        panel_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n\n"
            "情缘值: 1000\n"
            "【掩月心契】\n"
            "- 当前誓约: 守秘\n"
            "【第二期机缘】\n"
            "- 天机代卜链: 无\n"
            "- 入梦寻图冷却: 可施展\n"
            "- 共历心劫冷却: 可施展\n"
            "- 天机代卜冷却: 可施展\n"
            "- 梦图拼片: 虚天 0/4 | 苍坤 0/4\n"
            "命令: .入梦寻图、.残图、.拼图、.共历心劫、.天机代卜"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_due_at"] = now + 11 * 3600
            identity_state["concubine_tianji_chain"] = "残图引路"
            identity_state["concubine_tianji_chain_due_at"] = now + 11 * 3600

        with state_module.use_identity(send_as_id):
            parsed = concubine._parse_status_panel(panel_text, now + 60)
            self.assertTrue(parsed)
            self.assertTrue(concubine._apply_status_snapshot(parsed, now + 60))
            self.assertEqual(now + 11 * 3600, state_module.state["concubine_tianji_due_at"])
            self.assertEqual(0, state_module.state["concubine_tianji_chain_due_at"])

    async def test_cangkun_dream_broadcast_does_not_overwrite_xutian_progress(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DREAM, id=501)

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 501
            concubine._set_fragment_progress(concubine.DREAM_KIND_XUTIAN, 2, 4)

        await self._start_owned_fragment_action(send_as_id, "dream", 501, now - 1)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()):
            handled = await concubine.handle_concubine_dream_reply(
                "【全群异闻·苍坤残图】\n道友共梦归来，残图进度已至 4/4。",
                now,
                reply_to,
                matched_family="concubine_dream", current_msg_id=502,
                current_chat_id=state_module.get_game_group_id(), observed_at=now, reply_context={"sender_id": 88088001},
            )

        self.assertTrue(handled)
        self.assertEqual((2, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_XUTIAN))
        self.assertEqual((4, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_CANGKUN))
        self.assertEqual(2, state_module.state["concubine_fragment_count"])

    async def test_pending_dream_without_reply_id_does_not_clear_current_pending(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 501
            identity_state["next_concubine_time"] = now + 60

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as save_mock, \
             patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock:
            handled = await concubine.handle_concubine_dream_reply(
                "这是另一条不带回复关系的入梦相关消息。",
                now,
                reply_to=None,
                matched_family="concubine_dream",
            )

        self.assertFalse(handled)
        save_mock.assert_not_called()
        self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        self.assertEqual(501, state_module.state["concubine_dream_msg_id"])
        self.assertEqual(now + 60, state_module.state["next_concubine_time"])
        inbox_mock.assert_not_called()

    async def test_legacy_status_timeout_cannot_adopt_source_less_message_log(self):
        now = 1_700_000_900.0
        send_as_id = self._prepare_identity()
        panel_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n\n"
            "情缘值: 1000\n"
            "已解锁神通:\n - 【天机卜算】: 可在你观星冷却时代卜一次。\n\n"
            "【第二期机缘】\n"
            "- 天机代卜链: 无\n"
            "- 入梦寻图冷却: 120分钟\n"
            "- 共历心劫冷却: 30分钟\n"
            "- 天机代卜冷却: 60分钟\n"
            "- 梦图拼片: 虚天 1/4 | 苍坤 2/4\n"
            "命令: .入梦寻图、.残图、.拼图、.共历心劫、.天机代卜"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "status_pending"
            identity_state["concubine_status_msg_id"] = 501
            identity_state["next_concubine_time"] = now - 1
            identity_state["concubine_last_error"] = "pending"

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(now - 10),
                        "event_type": "message",
                        "message_id": 602,
                        "reply_to_msg_id": 501,
                        "text": panel_text,
                    }
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()), \
                 patch.object(concubine.random, "uniform", return_value=30), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_called()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_status_msg_id"])
        self.assertEqual(0, state_module.state["concubine_last_panel_msg_id"])
        self.assertEqual({}, state_module.state["concubine_status_query"])
        self.assertEqual("凌玉灵", state_module.state["concubine_name"])
        self.assertEqual("侍妾状态查询等待回复超时", state_module.state["concubine_last_error"])

    async def test_legacy_dream_timeout_cannot_adopt_unowned_message_log(self):
        now = 1_700_000_900.0
        send_as_id = self._prepare_identity()
        reply_text = (
            "【入梦寻图】\n"
            "本次梦兆锁定：【虚天残图】 线路。\n"
            "你与侍妾【凌玉灵】共入迷梦，觅得【虚天残图】碎片。\n"
            "本次掉落率：28%（当前 虚天残图 2/4）。"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 701
            identity_state["next_concubine_time"] = now - 1
            identity_state["concubine_last_error"] = "pending"

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(now - 10),
                        "event_type": "message",
                        "message_id": 702,
                        "reply_to_msg_id": 701,
                        "text": reply_text,
                    }
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()), \
                 patch.object(concubine.random, "uniform", return_value=0), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_called()
        self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        self.assertEqual(701, state_module.state["concubine_dream_msg_id"])
        self.assertEqual(1_700_000_600.0, state_module.state["concubine_dream_due_at"])
        self.assertEqual("pending", state_module.state["concubine_last_error"])
        self.assertEqual({}, state_module.state["concubine_fragment_actions"])

    async def test_dream_timeout_ignores_message_log_reply_from_other_topic(self):
        now = 1_700_000_900.0
        send_as_id = self._prepare_identity()
        reply_text = (
            "【入梦寻图】\n"
            "本次梦兆锁定：【虚天残图】 线路。\n"
            "你与侍妾【凌玉灵】共入迷梦，觅得【虚天残图】碎片。\n"
            "本次掉落率：28%（当前 虚天残图 2/4）。"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 701
            identity_state["next_concubine_time"] = now - 1
            identity_state["concubine_last_error"] = "pending"

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(now - 10),
                        "event_type": "message",
                        "message_id": 702,
                        "topic_id": 458347,
                        "reply_to_msg_id": 701,
                        "text": reply_text,
                    }
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "get_game_topic_id", return_value=7310786), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()), \
                 patch.object(concubine.random, "uniform", return_value=0), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_called()
        self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        self.assertEqual(701, state_module.state["concubine_dream_msg_id"])
        self.assertEqual("pending", state_module.state["concubine_last_error"])





    async def test_cangkun_puzzle_success_clears_only_cangkun_progress(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(dream_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_PUZZLE, id=777)

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "puzzle_pending"
            identity_state["concubine_puzzle_msg_id"] = 777
            concubine._set_fragment_progress(concubine.DREAM_KIND_XUTIAN, 3, 4)
            concubine._set_fragment_progress(concubine.DREAM_KIND_CANGKUN, 4, 4)

        await self._start_owned_fragment_action(send_as_id, "puzzle", 777, now - 1)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()):
            handled = await concubine.handle_concubine_puzzle_reply(
                "【苍坤残图·拼合成功】\n苍坤洞府舆图已成，修为 +120。",
                now,
                reply_to,
                matched_family="concubine_puzzle", current_msg_id=778,
                current_chat_id=state_module.get_game_group_id(), observed_at=now, reply_context={"sender_id": 88088001},
            )

        self.assertTrue(handled)
        self.assertEqual((3, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_XUTIAN))
        self.assertEqual((0, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_CANGKUN))
        self.assertEqual(3, state_module.state["concubine_fragment_count"])

    async def test_fragment_confirmation_promotes_puzzle_and_blocks_repeat_fragment_send(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_FRAGMENT, id=701)
        state_module.set_game_bot_ids([88001])

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["next_concubine_time"] = now
            concubine._set_fragment_progress(concubine.DREAM_KIND_XUTIAN, 3, 4)
            concubine._set_fragment_progress(concubine.DREAM_KIND_CANGKUN, 4, 4)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=self._status_receipt(701, now))), \
             patch.object(concubine.random, "uniform", return_value=0):
            self.assertTrue(await concubine._send_fragment_command(now))
            handled = await concubine.handle_concubine_fragment_reply(
                "侍妾【凌玉灵】（随行中）的残图卷轴如下：\n\n"
                "【虚天残图卷】\n"
                "拼片进度：3/4\n"
                "已收集：北阙残纹、南渊残纹、西极残纹\n"
                "缺失残纹：东离残纹\n"
                "重复藏本：北阙残纹x4、南渊残纹x3\n\n"
                "【苍坤残图卷】\n"
                "拼片进度：4/4\n"
                "已收集：慕兰残纹、禁门残纹、玉匣残纹、太妙残纹\n"
                "缺失残纹：无\n"
                "重复藏本：禁门残纹x2",
                now,
                reply_to,
                matched_family="concubine_fragment",
                current_msg_id=702, current_chat_id=state_module.get_game_group_id(), observed_at=now,
                reply_context={"sender_id": 88001},
            )

        self.assertTrue(handled)
        self.assertEqual("puzzle_ready", state_module.state["concubine_phase"])
        self.assertEqual("cangkun:4/4", state_module.state["concubine_fragment_confirm_key"])
        self.assertEqual(now, state_module.state["concubine_fragment_confirmed_at"])
        self.assertEqual(now, state_module.state["next_concubine_time"])

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_puzzle_msg_id"] = 0
            identity_state["next_concubine_time"] = now

        sent_msg = self._status_receipt(888, now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_fragment_action_sent(mock_send, "puzzle")
        self.assertEqual("puzzle_pending", state_module.state["concubine_phase"])
        self.assertEqual(888, state_module.state["concubine_puzzle_msg_id"])
        self.assertEqual("cangkun:4/4", state_module.state["concubine_fragment_confirm_key"])

    async def test_stale_fragment_send_cannot_overwrite_puzzle_success(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "idle"
            identity_state["next_concubine_time"] = now
            identity_state["concubine_last_error"] = ""
            concubine._set_fragment_progress(concubine.DREAM_KIND_CANGKUN, 4, 4)

        async def finish_puzzle_before_queue_returns(*_args, **_kwargs):
            concubine._clear_fragment_progress(concubine.DREAM_KIND_CANGKUN)
            state_module.state["concubine_phase"] = "idle"
            state_module.state["next_concubine_time"] = now + 3600
            state_module.state["concubine_last_error"] = ""
            return None

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as save_mock, \
             patch.object(concubine, "_send_concubine_game_command", new=AsyncMock(side_effect=finish_puzzle_before_queue_returns)):
            sent = await concubine._send_fragment_command(now)

        self.assertFalse(sent)
        self.assertEqual("", state_module.state["concubine_last_error"])
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(now + 3600, state_module.state["next_concubine_time"])
        save_mock.assert_called()









    async def test_passive_inbox_recovers_dream_reply_from_pending_chain_without_send_as_id(self):
        now = 1_780_414_158.0
        send_as_id = self._prepare_identity()
        text = (
            "【入梦寻图】\n"
            "本次梦兆锁定：【苍坤残图】 线路。\n"
            "你与侍妾【洛神】共入迷梦，终只见荒沙蔽月，未觅得【苍坤残图】碎片。\n"
            "另一条残图线路仍可能在后续 .入梦寻图 中显化。\n"
            "本次掉落率：22%（进度衰减 -12%，当前 苍坤残图 2/4）。"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = True
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 9746562
            identity_state["concubine_fragment_cangkun_count"] = 1
            identity_state["concubine_fragment_cangkun_total"] = 4

        state_module.get_identity_state(send_as_id)["concubine_name"] = "洛神"
        await self._start_owned_fragment_action(send_as_id, "dream", 9746562, now - 1)

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_dream",
                    "reply_to_msg_id": 9746562,
                    "root_msg_id": 9746562,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9746564, sender_id=88088001, server_event_at=now),
                event_type="message",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_dream_msg_id"])
            self.assertEqual((2, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_CANGKUN))
            self.assertGreater(state_module.state["concubine_dream_due_at"], now)

    async def test_passive_inbox_recovers_dream_cooldown_from_pending_chain_without_send_as_id(self):
        now = 1_780_471_316.0
        send_as_id = self._prepare_identity()
        text = "梦图感应尚未重启，请在 7小时3分钟22秒 后再试。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = True
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 9779196
            identity_state["concubine_last_error"] = "pending"

        await self._start_owned_fragment_action(send_as_id, "dream", 9779196, now - 1)

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_dream",
                    "reply_to_msg_id": 9779196,
                    "root_msg_id": 9779196,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9779198, sender_id=88088001, server_event_at=now),
                event_type="message",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_dream_msg_id"])
            self.assertEqual("", state_module.state["concubine_last_error"])
            self.assertEqual(now + 25402 + config.CD_BUFFER_SEC, state_module.state["concubine_dream_due_at"])

    async def test_passive_inbox_recovers_tianji_reply_from_pending_chain_without_send_as_id(self):
        now = 1_780_417_211.0
        send_as_id = self._prepare_identity()
        text = (
            "【天机代卜链】\n"
            "侍妾【红莲】焚香推演，为你接引一缕天机。\n"
            "得卦【心劫前兆】：下一次 .共历心劫 成本降低且首轮评分提高。\n"
            "本次消耗：180修为。"
        )
        await self._start_owned_tianji_action(send_as_id, 9747210, now - 1, partner="红莲")

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_tianji",
                    "reply_to_msg_id": 9747210,
                    "root_msg_id": 9747210,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9747211, sender_id=88090001, server_event_at=now),
                event_type="message",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_tianji_msg_id"])
            self.assertEqual("心劫前兆", state_module.state["concubine_tianji_chain"])
            self.assertGreater(state_module.state["concubine_tianji_due_at"], now)

    async def test_passive_inbox_recovers_tianji_cooldown_from_pending_chain_without_send_as_id(self):
        now = 1_780_471_072.0
        send_as_id = self._prepare_identity()
        text = "天机链路尚未重铸，请在 11小时9分钟29秒 后再试。"
        await self._start_owned_tianji_action(send_as_id, 9778978, now - 1)

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_tianji",
                    "reply_to_msg_id": 9778978,
                    "root_msg_id": 9778978,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9778979, sender_id=88090001, server_event_at=now),
                event_type="message",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_tianji_msg_id"])
            self.assertEqual("", state_module.state["concubine_tianji_last_error"])
            self.assertEqual(now + 40169 + config.CD_BUFFER_SEC, state_module.state["concubine_tianji_due_at"])


    async def test_passive_inbox_does_not_recover_ambiguous_dream_pending_context(self):
        now = 1_780_414_158.0
        first_id = self._prepare_identity()
        second_id = 991102
        state_module.ensure_identity_registered(second_id)
        state_module.update_send_as_profile(second_id, username="second")
        text = (
            "【入梦寻图】\n"
            "本次梦兆锁定：【苍坤残图】 线路。\n"
            "你与侍妾【洛神】共梦慕兰荒原，获得 【苍坤残图】 残纹 慕兰残纹（新残纹）。\n"
            "当前进度：3/4。"
        )
        for identity_id in (first_id, second_id):
            with state_module.use_identity(identity_id) as identity_state:
                identity_state["concubine_enabled"] = True
                identity_state["concubine_phase"] = "dream_pending"
                identity_state["concubine_dream_msg_id"] = 9746562

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_dream",
                    "reply_to_msg_id": 9746562,
                    "root_msg_id": 9746562,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9746564),
                event_type="message",
            )

        self.assertFalse(handled)
        with state_module.use_identity(first_id):
            self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        with state_module.use_identity(second_id):
            self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(0, snapshot["skipped"])
        self.assertEqual({}, passive_inbox._observed_passive_events)

    def test_heart_choice_delay_is_fast_enough_for_edited_prompt(self):
        with patch.object(concubine.random, "uniform", return_value=7.5) as mock_uniform:
            self.assertEqual(7.5, concubine._heart_next_choice_delay())
        mock_uniform.assert_called_once_with(6, 9)


    def test_no_partner_hint_does_not_count_as_realm_block(self):
        text = "你尚无红颜知己。唯有筑基之后，方可于.红尘寻缘中觅得佳人。"
        self.assertTrue(concubine._is_no_partner_text(text))
        self.assertFalse(concubine._is_partner_not_eligible_text(text))
        self.assertTrue(concubine._is_partner_not_eligible_text("你尚未筑基，根基不稳，当以修炼为重。"))

    async def test_no_partner_scheduler_wakes_at_reacquire_blocked_until(self):
        now = 1_700_000_000.0
        blocked_until = now + 600
        send_as_id = self._prepare_identity(dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_auto_reacquire"] = True
            identity_state["concubine_phase"] = "no_partner"
            identity_state["concubine_availability"] = "no_partner"
            identity_state["concubine_name"] = ""
            identity_state["concubine_reacquire_blocked_until"] = blocked_until
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual(blocked_until, state_module.state["next_concubine_time"])

    async def test_no_partner_dream_reply_preserves_reacquire_blocked_until(self):
        now = 1_700_000_000.0
        blocked_until = now + 600
        send_as_id = self._prepare_identity(dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DREAM, id=912)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_auto_reacquire"] = True
            identity_state["concubine_phase"] = "no_partner"
            identity_state["concubine_availability"] = "no_partner"
            identity_state["concubine_name"] = ""
            identity_state["concubine_reacquire_blocked_until"] = blocked_until
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), patch.object(concubine, "save_state"):
            handled = await concubine.handle_concubine_dream_reply(
                "你尚无侍妾，无法共梦寻图。",
                now,
                reply_to,
                matched_family="concubine_dream",
            )

        self.assertFalse(handled)
        self.assertEqual("no_partner", state_module.state["concubine_phase"])
        self.assertEqual("no_partner", state_module.state["concubine_availability"])
        self.assertEqual(now - 1, state_module.state["next_concubine_time"])
        self.assertEqual(blocked_until, state_module.state["concubine_reacquire_blocked_until"])

















    async def test_voyage_panel_blocks_due_concubine_actions(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now - 1, tianji_due_at=now - 1)
        panel_text = (
            "你的道心侍妾: 【柳玉】 (状态: 随行中)\n"
            "情缘值: 320\n"
            "当前誓约: 无\n"
            "入梦寻图冷却: 可施展\n"
            "共历心劫冷却: 可施展\n"
            "天机代卜冷却: 可施展\n"
            "远航状态: 均衡航线进行中，剩余约 56 分钟。\n"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            parsed = concubine._parse_status_panel(panel_text, now)
            self.assertIsNotNone(parsed)
            self.assertTrue(concubine._apply_status_snapshot(parsed, now))
            await concubine.run_concubine_scheduler(now)

        expected_return_at = now + 56 * 60 + config.CD_BUFFER_SEC
        mock_send.assert_not_awaited()
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual("均衡", state_module.state["concubine_voyage_route"])
        self.assertEqual(expected_return_at, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(expected_return_at, state_module.state["next_concubine_time"])

    async def test_tianji_reply_voyage_lock_sets_sailing_and_clears_pending(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
        await self._start_owned_tianji_action(send_as_id, 812, now - 1)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_tianji_reply(
                "侍妾正在远航途中，暂无法焚香代卜。",
                now,
                SimpleNamespace(raw_text=config.CMD_CONCUBINE_TIANJI, id=812),
                matched_family="concubine_tianji",
                **self._voyage_reply_metadata(813, now),
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_tianji_msg_id"])
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual(0, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(now + concubine.CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC, state_module.state["next_concubine_time"])
        self.assertIn("天机代卜被远航锁拦截", state_module.state["concubine_tianji_last_error"])

    async def test_scheduler_returns_voyage_before_other_concubine_actions(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now - 1, tianji_due_at=now - 1)
        sent_msg = self._status_receipt(913, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = False
            identity_state["concubine_voyage_status"] = "returned"
            identity_state["concubine_voyage_route"] = "冒险"
            identity_state["concubine_voyage_return_at"] = now
            identity_state["next_concubine_time"] = 0

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_voyage_action_sent(mock_send, config.CMD_CONCUBINE_VOYAGE_RETURN)
        self.assertEqual("voyage_return_pending", state_module.state["concubine_phase"])
        self.assertEqual(913, state_module.state["concubine_voyage_msg_id"])

    async def test_scheduler_does_not_start_voyage_when_module_disabled(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_heart_enabled"] = False
            identity_state["concubine_voyage_enabled"] = False
            identity_state["concubine_voyage_route"] = "均衡"
            identity_state["next_concubine_time"] = now + 3600

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual("均衡", state_module.state["concubine_voyage_route"])
        self.assertEqual("", state_module.state["concubine_voyage_last_error"])

    async def test_scheduler_starts_voyage_when_module_enabled_after_other_actions_clear(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        sent_msg = self._status_receipt(916, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_heart_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_voyage_route"] = "均衡"
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_voyage_action_sent(mock_send, f"{config.CMD_CONCUBINE_VOYAGE} {config.CONCUBINE_VOYAGE_DEFAULT_ROUTE}")
        self.assertEqual("voyage_pending", state_module.state["concubine_phase"])
        self.assertEqual(916, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(config.CONCUBINE_VOYAGE_DEFAULT_ROUTE, state_module.state["concubine_voyage_actions"]["voyage"]["route"])
        self.assertEqual(now + config.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC, state_module.state["next_concubine_time"])

    async def test_moon_partner_starts_moon_trace_voyage_at_160_affinity(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=160, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        sent_msg = self._status_receipt(9916, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_name"] = "南宫婉·月影"
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_voyage_action_sent(mock_send, f"{config.CMD_CONCUBINE_VOYAGE} {config.CONCUBINE_VOYAGE_MOON_ROUTE}")
        self.assertEqual(config.CONCUBINE_VOYAGE_MOON_ROUTE, state_module.state["concubine_voyage_actions"]["voyage"]["route"])

    async def test_moon_partner_below_160_does_not_fallback_to_default_voyage(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=159, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_name"] = "南宫婉·月影"
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("月殿寻痕情缘不足（159/160），暂不远航", state_module.state["concubine_voyage_last_error"])

    async def test_moon_voyage_unknown_send_keeps_recorded_route(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state.update(concubine_enabled=False, concubine_tianji_enabled=False,
                                  concubine_voyage_enabled=True, concubine_name="南宫婉·月影")
        with state_module.use_identity(send_as_id), patch.object(concubine, "save_state"), patch.object(
            concubine, "classify_game_send_block", return_value={"status": "none"},
        ), patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)) as mock_send:
            self.assertFalse(await concubine._send_voyage_command(now))
            self.assertFalse(await concubine._send_voyage_command(now + 86400))
        self._assert_voyage_action_sent(mock_send, f"{config.CMD_CONCUBINE_VOYAGE} {config.CONCUBINE_VOYAGE_MOON_ROUTE}")
        record = state_module.state["concubine_voyage_actions"]["voyage"]
        self.assertEqual("unknown", record["status"])
        self.assertEqual(config.CONCUBINE_VOYAGE_MOON_ROUTE, record["route"])

    async def test_moon_voyage_affinity_rejection_requests_status_without_inventing_affinity(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=200, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_name"] = "南宫婉·月影"
            identity_state["concubine_phase"] = "voyage_pending"
            identity_state["concubine_voyage_msg_id"] = 9923
            identity_state["concubine_voyage_route"] = config.CONCUBINE_VOYAGE_MOON_ROUTE

        await self._start_owned_voyage_action(send_as_id, "voyage", 9923, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_voyage_reply(
                "侍妾心神未定，此航线至少需要 160 情缘值。",
                now,
                SimpleNamespace(raw_text=f"{config.CMD_CONCUBINE_VOYAGE} {config.CONCUBINE_VOYAGE_MOON_ROUTE}", id=9923),
                matched_family="concubine_voyage", **self._voyage_reply_metadata(9924, now),
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(200, state_module.state["concubine_affinity"])
        self.assertEqual("unknown", state_module.state["concubine_availability"])
        self.assertIn("至少需要 160 情缘值", state_module.state["concubine_voyage_last_error"])

    async def test_scheduler_starts_voyage_during_summary_due_when_only_voyage_due(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        sent_msg = self._status_receipt(917, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_heart_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "summary_due"
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=90), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_voyage_action_sent(mock_send, f"{config.CMD_CONCUBINE_VOYAGE} {config.CONCUBINE_VOYAGE_DEFAULT_ROUTE}")
        self.assertEqual("voyage_pending", state_module.state["concubine_phase"])
        self.assertEqual(917, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(now + config.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC, state_module.state["next_concubine_time"])

    async def test_summary_due_voyage_waits_for_due_tianji(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now - 1)
        sent_msg = SimpleNamespace(id=917, sent_at=now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "summary_due"
            identity_state["concubine_tianji_last_error"] = "天机代卜等待闭关/元婴结算，稍后处理"
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=90):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(now + 90, state_module.state["next_concubine_time"])
        self.assertIn("天机代卜等待闭关/元婴结算", state_module.state["concubine_tianji_last_error"])

    async def test_scheduler_status_calibrates_for_enabled_voyage_without_cached_partner(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        sent_msg = self._status_receipt(918, now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_heart_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_availability"] = "unknown"
            identity_state["concubine_name"] = ""
            identity_state["next_concubine_time"] = 0

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=90), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_status_query_sent(mock_send)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(918, state_module.state["concubine_status_msg_id"])
        self.assertEqual(0, state_module.state["concubine_dream_msg_id"])
        self.assertEqual(now + config.CONCUBINE_PHASE_TIMEOUT_SEC, state_module.state["next_concubine_time"])
        self.assertEqual("", state_module.state["concubine_voyage_last_error"])

    async def test_scheduler_keeps_voyage_affinity_gate_after_unarchiving(self):
        now = 1_700_000_000.0
        low_affinity_id = self._prepare_identity(affinity=119, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(low_affinity_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["next_concubine_time"] = 0

        with state_module.use_identity(low_affinity_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=60), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertIn("情缘不足", state_module.state["concubine_voyage_last_error"])

        other_sect_id = self._prepare_identity(affinity=120, dream_due_at=now + 3600, tianji_due_at=now + 3600, sect_name="落云宗")
        sent_msg = self._status_receipt(919, now)
        with state_module.use_identity(other_sect_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["next_concubine_time"] = 0

        with state_module.use_identity(other_sect_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=60), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        self._assert_voyage_action_sent(mock_send, f"{config.CMD_CONCUBINE_VOYAGE} {config.CONCUBINE_VOYAGE_DEFAULT_ROUTE}")
        self.assertEqual("voyage_pending", state_module.state["concubine_phase"])
        self.assertEqual(919, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual("", state_module.state["concubine_voyage_last_error"])

    async def test_voyage_return_reply_sets_idle_and_schedules_next_chain(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_VOYAGE_RETURN, id=915)
        text = (
            "【乱星海远航·归】\n"
            "侍妾【柳玉】已自 冒险 航线归来，向你呈上收获：\n"
            "灵石 x 100"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_msg_id"] = 915

        await self._start_owned_voyage_action(send_as_id, "voyage_return", 915, now, partner="柳玉")

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()) as audit_mock, \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_voyage_reply(
                text,
                now,
                reply_to,
                matched_family="concubine_voyage", **self._voyage_reply_metadata(916, now),
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual("idle", state_module.state["concubine_voyage_status"])
        self.assertEqual("冒险", state_module.state["concubine_voyage_route"])
        self.assertIn("灵石 x 100", state_module.state["concubine_voyage_last_result"])
        self.assertEqual(now + 30, state_module.state["next_concubine_time"])
        audit_mock.assert_awaited_once()
        self.assertIn("远航归来", audit_mock.await_args.args[0])
        self.assertIn("灵石x100", audit_mock.await_args.args[0])
        self.assertEqual("medium", audit_mock.await_args.kwargs["priority"])

    async def test_voyage_return_definitely_unsent_keeps_lock_and_clears_false_error(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_voyage_status"] = "sailing"
            identity_state["concubine_voyage_route"] = "冒险"
            identity_state["concubine_voyage_return_at"] = now - 1
            identity_state["concubine_voyage_retry_count"] = 0

        send_block = {
            "code": "send_as_peer_invalid",
            "reason": "You can't send messages as the specified peer",
        }
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=600), \
             patch.object(concubine, "classify_game_send_block", side_effect=[{"status": "none"}, dict(send_block, status="unsent", at=now)]), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)):
            sent = await concubine._send_voyage_return_command(now)

        self.assertFalse(sent)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual(now - 1, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(0, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual("", state_module.state["concubine_voyage_last_error"])
        self.assertEqual(now + 600, state_module.state["next_concubine_time"])
        self.assertEqual("unsent", state_module.state["concubine_voyage_actions"]["voyage_return"]["status"])

    async def test_legacy_voyage_pending_cannot_gain_retry_permission_from_old_block(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state.update(concubine_voyage_enabled=True, concubine_phase="voyage_return_pending",
                                  concubine_voyage_status="returned", concubine_voyage_return_at=now - 1,
                                  concubine_voyage_retry_count=1, concubine_voyage_msg_id=918)
            before = copy.deepcopy(identity_state)
        with state_module.use_identity(send_as_id), patch.object(concubine, "save_state"), patch.object(
            concubine, "get_last_game_send_block", return_value={"code": "send_queue_timeout", "at": now},
        ), patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            self.assertFalse(await concubine._send_voyage_return_command(now))
        mock_send.assert_not_awaited()
        self.assertEqual(before, state_module.get_identity_state(send_as_id))

    async def test_voyage_return_affinity_loss_triggers_star_palace_recovery(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_VOYAGE_RETURN, id=916)
        text = (
            "【乱星海远航·归】\n"
            "侍妾【白瑶怡】已自 冒险 航线归来，向你呈上收获：\n"
            "- 修为 +405\n"
            "- 灵石 +97\n"
            "- 养魂木 x4\n"
            "- 侍妾额外为你蓄灵 7 点\n"
            "路遇风暴，侍妾道心受惊，情缘减少 32 点。"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_msg_id"] = 916

        await self._start_owned_voyage_action(send_as_id, "voyage_return", 916, now, partner="白瑶怡")

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()) as audit_mock, \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_voyage_reply(
                text,
                now,
                reply_to,
                matched_family="concubine_voyage", **self._voyage_reply_metadata(917, now),
            )

        self.assertTrue(handled)
        self.assertEqual(288, state_module.state["concubine_affinity"])
        self.assertIn("远航损耗情缘", state_module.state["concubine_tianji_last_error"])
        self.assertEqual(now + 30, state_module.state["next_concubine_time"])
        audit_mock.assert_awaited_once()
        self.assertIn("养魂木x4", audit_mock.await_args.args[0])
        self.assertIn("情缘-32", audit_mock.await_args.args[0])

        sent_msg = self._status_receipt(456, now + 30)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now + 30)

        self._assert_greet_action_sent(mock_send)
        self.assertEqual("greet_pending", state_module.state["concubine_phase"])
        self.assertEqual(456, state_module.state["concubine_greet_msg_id"])

    async def test_voyage_return_reply_with_remaining_cd_updates_return_at(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_VOYAGE_RETURN, id=917)
        text = "侍妾尚未归航，预计归航还需 56 分钟。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_status"] = "sailing"
            identity_state["concubine_voyage_msg_id"] = 917
            identity_state["concubine_voyage_retry_count"] = 1

        await self._start_owned_voyage_action(send_as_id, "voyage_return", 917, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_voyage_reply(
                text,
                now,
                reply_to,
                matched_family="concubine_voyage", **self._voyage_reply_metadata(918, now),
            )

        expected_return_at = now + 56 * 60 + config.CD_BUFFER_SEC
        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(0, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual(expected_return_at, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(expected_return_at, state_module.state["next_concubine_time"])

    async def test_legacy_voyage_return_timeout_preserves_original_deadline(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_status"] = "returned"
            identity_state["concubine_voyage_msg_id"] = 918
            identity_state["concubine_voyage_return_at"] = now - 1
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "_recover_concubine_pending_from_message_log", new=AsyncMock(return_value=False)), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("voyage_return_pending", state_module.state["concubine_phase"])
        self.assertEqual(918, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(0, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual(now - 1, state_module.state["next_concubine_time"])

    async def test_voyage_return_timeout_recovers_reply_from_message_log_without_retry(self):
        now = 1_700_000_000.0
        reply_ts = now - 2
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_text = (
            "【乱星海远航·归】\n"
            "侍妾【辛如音】已自 冒险 航线归来，向你呈上收获：\n"
            "- 修为 +589\n"
            "- 灵石 +154\n"
            "- 养魂木 x2\n"
            "路遇风暴，侍妾道心受惊，情缘减少 21 点。"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_status"] = "returned"
            identity_state["concubine_voyage_msg_id"] = 918
            identity_state["concubine_voyage_return_at"] = now - 1
            identity_state["next_concubine_time"] = now - 1

        await self._start_owned_voyage_action(send_as_id, "voyage_return", 918, now - 10, partner="辛如音")

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(reply_ts),
                        "event_type": "message",
                        "chat_id": state_module.get_game_group_id(),
                        "sender_id": 88090001, "sender_is_bot": True,
                        "server_event_at": reply_ts,
                        "message_id": 919,
                        "reply_to_msg_id": 918,
                        "text": reply_text,
                    }
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(message_log_recovery, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine.random, "uniform", return_value=30), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual("idle", state_module.state["concubine_voyage_status"])
        self.assertIn("养魂木 x2", state_module.state["concubine_voyage_last_result"])

    async def test_voyage_pending_lock_wait_sets_long_return_at(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=f"{config.CMD_CONCUBINE_VOYAGE} 冒险", id=919)
        text = "侍妾仍在远航中，请在 11小时48分钟5秒 后再试。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_pending"
            identity_state["concubine_voyage_status"] = ""
            identity_state["concubine_voyage_route"] = "冒险"
            identity_state["concubine_voyage_msg_id"] = 919
            identity_state["concubine_voyage_retry_count"] = 1

        await self._start_owned_voyage_action(send_as_id, "voyage", 919, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_voyage_reply(
                text,
                now,
                reply_to,
                matched_family="concubine_voyage", **self._voyage_reply_metadata(920, now),
            )

        expected_return_at = now + 11 * 3600 + 48 * 60 + 5 + config.CD_BUFFER_SEC
        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(0, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual("冒险", state_module.state["concubine_voyage_route"])
        self.assertEqual(expected_return_at, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(expected_return_at, state_module.state["next_concubine_time"])

    async def test_daily_greet_voyage_lock_wait_sets_long_return_at(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DAILY_GREET, id=456)
        text = "侍妾仍在远航中，请在 11小时48分钟5秒 后再试。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_voyage_status"] = ""
            identity_state["concubine_voyage_route"] = "冒险"
        await self._start_owned_greet(send_as_id, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_greet_reply(
                text,
                now,
                reply_to,
                matched_family="concubine_greet",
                **self._gift_reply_metadata(457, now),
            )

        expected_return_at = now + 11 * 3600 + 48 * 60 + 5 + config.CD_BUFFER_SEC
        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_greet_msg_id"])
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual(expected_return_at, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(expected_return_at, state_module.state["next_concubine_time"])

    async def test_unowned_reacquire_log_cannot_rewrite_cooldown(self):
        now = 1_700_000_000.0
        reply_ts = now - 5
        send_as_id = self._prepare_identity(affinity=0, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        text = "@xinggong 神念消耗过剧，请在 7小时3分钟22秒 后再试。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "reacquire_pending"
            identity_state["concubine_availability"] = "no_partner"
            identity_state["concubine_auto_reacquire"] = True
            identity_state["concubine_reacquire_msg_id"] = 801
            before = copy.deepcopy(identity_state)

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [{
                    "event_type": "message",
                    "ts": self._log_ts(reply_ts),
                    "message_id": 901,
                    "reply_to_msg_id": 801,
                    "text": text,
                }],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine.time, "time", return_value=reply_ts), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()) as audit_mock:
                handled = await concubine._recover_concubine_pending_from_message_log(now, "reacquire_pending")

        self.assertFalse(handled)
        self.assertEqual(before, state_module.get_identity_state(send_as_id))
        audit_mock.assert_not_awaited()


    async def test_voyage_return_no_task_requires_status_even_for_current_return_reply(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        text = "侍妾当前并无可结算的远航任务。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_voyage_status"] = "sailing"
            identity_state["concubine_voyage_route"] = "冒险"
            identity_state["concubine_voyage_return_at"] = 0

        with state_module.use_identity(send_as_id), \
             patch.object(concubine.random, "uniform", return_value=0):
            parsed = concubine._parse_voyage_status_text(text, now)
            self.assertTrue(concubine._apply_voyage_snapshot(parsed, now))

        self.assertEqual("needs_status", state_module.state["concubine_voyage_status"])
        self.assertEqual(2, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual(now + concubine.CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC, state_module.state["next_concubine_time"])

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_msg_id"] = 918
            identity_state["concubine_voyage_retry_count"] = 1

        await self._start_owned_voyage_action(send_as_id, "voyage_return", 918, now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_voyage_reply(
                text,
                now + 10,
                SimpleNamespace(raw_text=config.CMD_CONCUBINE_VOYAGE_RETURN, id=918),
                matched_family="concubine_voyage", **self._voyage_reply_metadata(919, now + 10),
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual("needs_status", state_module.state["concubine_voyage_status"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(0, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual(now + 10 + concubine.CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC, state_module.state["next_concubine_time"])

    async def test_voyage_pending_timeout_recovers_evidence_without_retry(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
        await self._start_owned_voyage_action(send_as_id, "voyage_return", 918, now)
        with state_module.use_identity(send_as_id), patch.object(concubine, "save_state"), patch.object(
            concubine, "find_message_log_replies", return_value=[],
        ), patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            for later in (now + 30, now + 3600, now + 86400):
                await concubine.run_concubine_scheduler(later)
                concubine.restore_concubine_runtime(later)
                self.assertFalse(await concubine._send_voyage_return_command(later))
        mock_send.assert_not_awaited()
        self.assertEqual("voyage_return_pending", state_module.state["concubine_phase"])
        record = state_module.state["concubine_voyage_actions"]["voyage_return"]
        self.assertEqual("sent", record["status"])
        self.assertEqual(918, record["msg_id"])
        self.assertEqual(0, state_module.state["concubine_voyage_retry_count"])

    async def test_legacy_voyage_start_timeout_stays_held_after_unarchive(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state.update(concubine_voyage_enabled=True, concubine_phase="voyage_pending",
                                  concubine_voyage_status="", concubine_voyage_route="冒险",
                                  concubine_voyage_msg_id=921, next_concubine_time=now - 100)
            before = copy.deepcopy(identity_state)
        with state_module.use_identity(send_as_id), patch.object(concubine, "save_state"), patch.object(
            concubine, "send_audit_log", new=AsyncMock(),
        ) as audit_mock, patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)
            concubine.restore_concubine_runtime(now + 1000)
        mock_send.assert_not_awaited()
        audit_mock.assert_not_awaited()
        self.assertEqual(before, state_module.get_identity_state(send_as_id))

    def test_voyage_status_no_task_clears_stale_sailing_lock(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        text = "侍妾【柳玉】当前并未执行远航任务。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_voyage_status"] = "sailing"
            identity_state["concubine_voyage_route"] = "冒险"
            identity_state["concubine_voyage_return_at"] = now - 3600
            identity_state["concubine_voyage_retry_count"] = 2

        with state_module.use_identity(send_as_id):
            parsed = concubine._parse_voyage_status_text(text, now)
            self.assertTrue(concubine._apply_voyage_snapshot(parsed, now))

        self.assertEqual("idle", state_module.state["concubine_voyage_status"])
        self.assertEqual(0, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(0, state_module.state["concubine_voyage_retry_count"])

    async def test_moon_voyage_real_panel_and_return_are_parsed_and_audited(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=166, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        sailing_text = "远航状态: 月殿寻痕航线进行中，剩余约 319 分钟。"
        returned_text = "远航状态: 月殿寻痕航线已归航，待结算（.远航归来）。"
        result_text = (
            "【乱星海远航·归】\n"
            "侍妾【南宫婉·月影】已自 月殿寻痕 航线归来，向你呈上收获：\n"
            "- 修为 +423\n- 灵石 +89\n- 素女禁纹 x1\n"
            "此行顺遂，侍妾对你更添信重，情缘增加 6 点。"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state.update(concubine_voyage_enabled=True, concubine_voyage_route=config.CONCUBINE_VOYAGE_MOON_ROUTE)
            sailing = concubine._parse_voyage_status_text(sailing_text, now)
            returned = concubine._parse_voyage_status_text(returned_text, now)
        await self._start_owned_voyage_action(send_as_id, "voyage_return", 9901, now, partner="南宫婉·月影")
        with state_module.use_identity(send_as_id), patch.object(concubine, "save_state"), patch.object(
            concubine, "send_audit_log", new=AsyncMock(),
        ) as audit:
            self.assertTrue(await concubine.handle_concubine_voyage_reply(
                result_text, now, SimpleNamespace(id=9901, raw_text=config.CMD_CONCUBINE_VOYAGE_RETURN),
                matched_family="concubine_voyage", **self._voyage_reply_metadata(9902, now),
            ))
        self.assertEqual("sailing", sailing["status"])
        self.assertEqual(config.CONCUBINE_VOYAGE_MOON_ROUTE, sailing["route"])
        self.assertEqual("returned", returned["status"])
        self.assertEqual(config.CONCUBINE_VOYAGE_MOON_ROUTE, returned["route"])
        self.assertEqual("idle", state_module.state["concubine_voyage_status"])
        self.assertEqual(172, state_module.state["concubine_affinity"])
        audit.assert_awaited_once()
        self.assertIn("月殿寻痕", audit.await_args.args[0])
        self.assertIn("素女禁纹x1", audit.await_args.args[0])

    def test_restore_voyage_runtime_snapshot_noops_for_same_payload(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_msg_id"] = 918
            identity_state["concubine_voyage_status"] = "returned"
            identity_state["concubine_voyage_route"] = "冒险"
            identity_state["concubine_voyage_return_at"] = now - 1
            identity_state["concubine_voyage_last_result"] = "灵石 x 100"
            identity_state["concubine_voyage_last_error"] = ""
            identity_state["concubine_voyage_retry_count"] = 1

        with state_module.use_identity(send_as_id):
            snapshot = concubine._voyage_runtime_snapshot()
            before = copy.deepcopy(dict(state_module.state.items()))
            self.assertFalse(concubine._restore_voyage_runtime_snapshot(snapshot))
            after = copy.deepcopy(dict(state_module.state.items()))

        self.assertEqual(before, after)

    def test_restore_voyage_runtime_snapshot_keeps_plain_payload_shape(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        payload = {
            "phase": "voyage_pending",
            "concubine_voyage_msg_id": 920,
            "concubine_voyage_status": "sailing",
            "concubine_voyage_route": "冒险",
            "concubine_voyage_return_at": now + 3600,
            "concubine_voyage_last_result": "",
            "concubine_voyage_last_error": "等待归航",
            "concubine_voyage_retry_count": 2,
        }
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "idle"

        with state_module.use_identity(send_as_id):
            self.assertTrue(concubine._restore_voyage_runtime_snapshot(payload))
            restored = concubine._voyage_runtime_snapshot()

        self.assertEqual(payload, restored)

    async def test_status_reply_ignored_during_voyage_pending(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        panel_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n"
            "情缘值: 320\n"
            "入梦寻图冷却: 可施展\n共历心劫冷却: 可施展\n天机代卜冷却: 可施展\n"
            "远航状态: 冒险航线进行中，剩余约 56 分钟。\n"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_pending"
            identity_state["concubine_voyage_msg_id"] = 920

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as save_mock:
            handled = await self._manual_status(panel_text, now, 1)

        self.assertFalse(handled)
        save_mock.assert_not_called()
        self.assertEqual("voyage_pending", state_module.state["concubine_phase"])
        self.assertEqual(920, state_module.state["concubine_voyage_msg_id"])

    async def test_unowned_dream_voyage_text_cannot_update_voyage_snapshot(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True

        before = copy.deepcopy(state_module.get_identity_state(send_as_id))
        with state_module.use_identity(send_as_id), \
             patch.object(concubine.random, "uniform", return_value=0):
            changed = await passive_inbox.handle_passive_module_card(
                "侍妾仍在远航途中，暂无法与你同梦寻图。",
                now=now, reply_context={"send_as_id": send_as_id, "family": "concubine_dream"},
                event=self._external_event(932, now), event_type="message",
            )

        self.assertFalse(changed)
        self.assertEqual(before, state_module.get_identity_state(send_as_id))

    async def test_passive_concubine_status_panel_refreshes_cached_panel_msg_id(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        panel_msg_id = 9387319
        panel_text = (
            "你的红尘道侣: 【凌玉灵】 (状态: 随行中)\n\n"
            "她安静地陪伴着你，虽不通星宫秘法，却也可为你牵引第二期机缘。\n\n"
            "【第二期机缘】\n"
            "- 入梦寻图冷却: 可施展\n"
            "- 共历心劫冷却: 可施展\n"
            "- 天机代卜冷却: 可施展\n"
            "命令: .入梦寻图、.残图、.拼图、.共历心劫、.坠魔心劫、.天机代卜"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_last_panel_msg_id"] = 123

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(concubine, "save_state"):
            handled = await self._manual_status(panel_text, now, panel_msg_id - 1, passive=True)

        self.assertTrue(handled)
        self.assertEqual(panel_msg_id, state_module.state["concubine_last_panel_msg_id"])
        self.assertEqual(-1001680975844, state_module.state["concubine_last_panel_chat_id"])
        self.assertEqual("凌玉灵", state_module.state["concubine_name"])

    async def test_external_same_name_status_panel_without_identity_context_is_ignored(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=30, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        panel_text = (
            "你的道心侍妾: 【墨彩环】 (状态: 随行中)\n\n"
            "情缘值: 3744\n"
            "【第二期机缘】\n"
            "- 梦图拼片: 虚天 2/4 | 苍坤 1/4\n"
            "命令: .入梦寻图、.残图、.拼图 虚天/苍坤"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_name"] = "墨彩环"
            identity_state["concubine_affinity"] = 30
            identity_state["concubine_fragment_count"] = 0
            identity_state["concubine_fragment_total"] = 4
            identity_state["concubine_last_panel_msg_id"] = 11197905

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state") as save_mock:
            handled = await passive_inbox.handle_passive_module_card(
                panel_text,
                now=now,
                reply_context={"family": "concubine_status", "reply_to_sender_id": 123456789},
                event=SimpleNamespace(id=11197518, chat_id=-1001680975844),
                event_type="message",
            )

        self.assertFalse(handled)
        save_mock.assert_not_called()
        with state_module.use_identity(send_as_id):
            self.assertEqual(30, state_module.state["concubine_affinity"])
            self.assertEqual(0, state_module.state["concubine_fragment_count"])
            self.assertEqual(11197905, state_module.state["concubine_last_panel_msg_id"])

    async def test_status_handler_ignores_idle_panel_without_command_anchor(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=30, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        panel_text = (
            "你的道心侍妾: 【墨彩环】 (状态: 随行中)\n\n"
            "情缘值: 3744\n"
            "【第二期机缘】\n"
            "- 梦图拼片: 虚天 2/4 | 苍坤 1/4"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_name"] = "墨彩环"
            identity_state["concubine_affinity"] = 30
            identity_state["concubine_phase"] = "idle"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as save_mock:
            handled = await concubine.handle_concubine_status_reply(
                panel_text,
                now,
                SimpleNamespace(raw_text="", id=11197517),
                matched_family="concubine_status",
                current_msg_id=11197518,
            )

        self.assertFalse(handled)
        save_mock.assert_not_called()
        self.assertEqual(30, state_module.state["concubine_affinity"])




    async def test_nanlong_protected_trade_broadcast_does_not_mark_no_partner(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        text = (
            "【天机异闻·南陇侯的交易】\n"
            "道友 @xinggong 经过深思熟虑，选择将侍妾【凌玉灵】与南陇侯交换！\n"
            "作为回报，南陇侯赐予了其一件至宝：【元磁山核·甲】！"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["nanlong_enabled"] = True
            identity_state["nanlong_reply_to_msg_id"] = 22027
            identity_state["next_nanlong_time"] = now + 60
            identity_state["nanlong_protect_phase"] = "recall_pending"
            identity_state["nanlong_last_msg_id"] = 9903

            with (
                patch.object(concubine, "save_state") as save_mock,
                patch.object(concubine, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                handled = await concubine.handle_concubine_loss_broadcast(text, now, self._external_event(8810, now))

            self.assertTrue(handled)
            save_mock.assert_called_once()
            audit_mock.assert_awaited_once()
            self.assertEqual("available", identity_state["concubine_availability"])
            self.assertEqual("idle", identity_state["concubine_phase"])
            self.assertEqual("凌玉灵", identity_state["concubine_name"])
            self.assertEqual("pending", identity_state["concubine_external_observation"]["status"])

    async def test_nanlong_unprotected_trade_broadcast_requires_calibration(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        text = (
            "【天机异闻·南陇侯的交易】\n"
            "道友 @xinggong 经过深思熟虑，选择将侍妾【凌玉灵】与南陇侯交换！"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            with (
                patch.object(concubine, "save_state") as save_mock,
                patch.object(concubine, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                handled = await concubine.handle_concubine_loss_broadcast(text, now, self._external_event(8810, now))

            self.assertTrue(handled)
            save_mock.assert_called_once()
            audit_mock.assert_awaited_once()
            self.assertEqual("available", identity_state["concubine_availability"])
            self.assertEqual("idle", identity_state["concubine_phase"])
            self.assertEqual("pending", identity_state["concubine_external_observation"]["status"])


if __name__ == "__main__":
    unittest.main()
