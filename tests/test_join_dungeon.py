import atexit
import asyncio
import copy
import json
import os
import sys
import tempfile
import time
import unittest
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

from model import state as state_module
from model.features import join_dungeon, workflow_log


class MessageEntityMention:
    def __init__(self, offset, length):
        self.offset = offset
        self.length = length


class MessageEntityMentionName:
    def __init__(self, user_id):
        self.user_id = user_id


def _event(msg_id, sender_id, text, *, reply_to=0, topic=123, entities=None, forum_topic=True):
    return SimpleNamespace(
        id=msg_id,
        sender_id=sender_id,
        chat_id=-100,
        raw_text=text,
        reply_to=SimpleNamespace(reply_to_msg_id=reply_to, reply_to_top_id=topic, forum_topic=forum_topic),
        message=SimpleNamespace(entities=entities or []),
    )


def _read_workflow_events(tmpdir):
    events = []
    for path in Path(tmpdir).glob("**/*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        join_dungeon._inbox.clear()
        join_dungeon._by_msg_id.clear()
        join_dungeon._join_keys.clear()
        join_dungeon._join_throttle.clear()

    def tearDown(self):
        join_dungeon._inbox.clear()
        join_dungeon._by_msg_id.clear()
        join_dungeon._join_keys.clear()
        join_dungeon._join_throttle.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class JoinDungeonTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def _prepare_identity(self, identity_id=90001, username="bbtest"):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username=username, enabled=True)
        state_module.get_identity_state(identity_id)["dungeon_join_enabled"] = True
        return identity_id

    async def test_strict_reply_chain_mentions_join(self):
        identity_id = self._prepare_identity()
        now = 1000.0
        opener = _event(10, 111, ".开启虚天殿")
        announce = _event(
            11,
            7900199668,
            "【虚天殿已开启】\n副本ID: 393\n其他道友可使用 .加入副本 393 加入队伍！",
            reply_to=10,
        )
        at_text = "@bbtest 来"
        at = _event(12, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)

            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(
            ".加入副本 393",
            track=False,
            send_as_id=identity_id,
            priority="urgent_reactive",
        )

    async def test_dungeon_join_workflow_log_tracks_announcement_send_and_success(self):
        identity_id = self._prepare_identity()
        now = 1000.0
        opener = _event(10, 111, ".开启虚天殿")
        announce = _event(
            11,
            7900199668,
            "【虚天殿已开启】\n副本ID: 393\n其他道友可使用 .加入副本 393 加入队伍！",
            reply_to=10,
        )
        at_text = "@bbtest 来"
        at = _event(12, 111, at_text, entities=[MessageEntityMention(0, 7)])
        success_reply = _event(
            100,
            7900199668,
            "@bbtest 已成功加入副本 393\n当前队伍:\n- @bbtest",
            reply_to=99,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir), \
                    patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                    patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99, sent_at=now + 2))) as send_mock, \
                    patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
                join_dungeon.record_game_group_message(opener, now=now)
                join_dungeon.record_game_group_message(announce, now=now + 1)
                join_dungeon.record_game_group_message(at, now=now + 2)

                handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)
                self.assertTrue(handled)
                self.assertTrue(await join_dungeon.handle_dungeon_join_bot_message(success_reply, success_reply.raw_text, now + 3))
                events = _read_workflow_events(tmpdir)

        send_mock.assert_awaited_once_with(
            ".加入副本 393",
            track=False,
            send_as_id=identity_id,
            priority="urgent_reactive",
        )
        self.assertTrue(any(
            event.get("event") == "announcement_seen"
            and event.get("decision") == "passive_announcement_recorded"
            and event.get("msg_id") == 11
            for event in events
        ))
        self.assertTrue(any(
            event.get("event") == "join_reserved"
            and event.get("op_id") == f"{identity_id}:393"
            and event.get("source_message_id") == 11
            for event in events
        ))
        self.assertTrue(any(
            event.get("event") == "join_sent"
            and event.get("command") == ".加入副本 393"
            and event.get("detail", {}).get("retry_count") == 0
            for event in events
        ))
        self.assertTrue(any(
            event.get("event") == "join_success"
            and event.get("decision") == "join_success_observed"
            and event.get("reply_to_msg_id") == 99
            and "@bbtest 已成功加入副本 393" in event.get("text", "")
            for event in events
        ))

    async def test_wrong_opener_does_not_join(self):
        self._prepare_identity()
        now = 2000.0
        opener = _event(20, 111, ".开启虚天殿")
        announce = _event(21, 7900199668, "副本ID: 394", reply_to=20)
        at_text = "@bbtest 来"
        at = _event(22, 222, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)

            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_plain_text_at_without_mention_entity_does_not_join(self):
        self._prepare_identity()
        now = 3000.0
        opener = _event(30, 111, ".开启虚天殿")
        announce = _event(31, 7900199668, "副本ID: 395", reply_to=30)
        at_text = "@bbtest 来"
        at = _event(32, 111, at_text, entities=[])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)

            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_game_group_output_is_only_join_command(self):
        identity_id = self._prepare_identity()
        now = 4000.0
        opener = _event(40, 111, ".开启虚天殿")
        announce = _event(41, 7900199668, "副本ID: 396", reply_to=40)
        at_text = "@bbtest 来"
        at = _event(42, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)

            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertTrue(handled)
        sent_command = send_mock.await_args.kwargs.get("command") if "command" in send_mock.await_args.kwargs else send_mock.await_args.args[0]
        self.assertEqual(f"{join_dungeon.CMD_DUNGEON_JOIN} 396", sent_command)
        self.assertEqual(f"{join_dungeon.CMD_DUNGEON_JOIN} 396", f".加入副本 396")

    async def test_non_bot_sender_with_dungeon_text_is_skipped(self):
        self._prepare_identity()
        now = 5000.0
        opener = _event(50, 111, ".开启虚天殿")
        fake_announce = _event(51, 999999, "【虚天殿已开启】副本ID: 397", reply_to=50)
        at_text = "@bbtest 来"
        at = _event(52, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(fake_announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_announcement_outside_60s_window_is_skipped(self):
        self._prepare_identity()
        opener = _event(60, 111, ".开启虚天殿")
        announce = _event(61, 7900199668, "副本ID: 398", reply_to=60)
        at_text = "@bbtest 来"
        at = _event(62, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(opener, now=6000.0)
            join_dungeon.record_game_group_message(announce, now=6001.0)
            join_dungeon.record_game_group_message(at, now=6062.1)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, 6062.1)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_announcement_exactly_at_window_lower_bound_hits(self):
        identity_id = self._prepare_identity()
        opener = _event(70, 111, ".开启虚天殿")
        announce = _event(71, 7900199668, "副本ID: 399", reply_to=70)
        at_text = "@bbtest 来"
        at = _event(72, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=7000.0)
            join_dungeon.record_game_group_message(announce, now=7001.0)
            join_dungeon.record_game_group_message(at, now=7061.0)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, 7061.0)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(".加入副本 399", track=False, send_as_id=identity_id, priority="urgent_reactive")

    async def test_announcement_reply_target_missing_is_skipped(self):
        self._prepare_identity()
        now = 8000.0
        announce = _event(81, 7900199668, "副本ID: 400", reply_to=999)
        at_text = "@bbtest 来"
        at = _event(82, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_bot_message_without_reply_to_is_skipped(self):
        self._prepare_identity()
        now = 9000.0
        announce = _event(91, 7900199668, "副本ID: 401", reply_to=0)
        at_text = "@bbtest 来"
        at = _event(92, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_dynamic_han_tianzun_bot_flag_counts_as_game_bot(self):
        identity_id = self._prepare_identity()
        now = 9500.0
        opener = _event(95, 111, ".开启副本")
        announce = _event(
            96,
            424242,
            "【虚天殿已开启】\n副本ID: 394\n其他道友可使用 .加入副本 394 加入队伍！",
            reply_to=95,
        )
        setattr(announce, "_xiuxian_sender_is_game_bot", True)
        at_text = "@bbtest 来"
        at = _event(97, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=123))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(
            ".加入副本 394",
            track=False,
            send_as_id=identity_id,
            priority="urgent_reactive",
        )

    async def test_bot_reply_without_dungeon_text_is_skipped(self):
        self._prepare_identity()
        now = 10000.0
        opener = _event(100, 111, ".开启虚天殿")
        announce = _event(101, 7900199668, "@someone 你今天还没签到呢", reply_to=100)
        at_text = "@bbtest 来"
        at = _event(102, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_multiple_announcements_picks_latest_msg_id(self):
        identity_id = self._prepare_identity()
        now = 11000.0
        at_text = "@bbtest 来"
        at = _event(120, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(_event(110, 111, ".开启虚天殿"), now=now)
            join_dungeon.record_game_group_message(_event(111, 7900199668, "副本ID: 402", reply_to=110), now=now + 1)
            join_dungeon.record_game_group_message(_event(112, 111, ".开启虚天殿"), now=now + 2)
            join_dungeon.record_game_group_message(_event(113, 7900199668, "副本ID: 403", reply_to=112), now=now + 3)
            join_dungeon.record_game_group_message(at, now=now + 4)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 4)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(".加入副本 403", track=False, send_as_id=identity_id, priority="urgent_reactive")

    async def test_different_topic_is_skipped(self):
        self._prepare_identity()
        now = 12000.0
        opener = _event(130, 111, ".开启虚天殿", topic=999)
        announce = _event(131, 7900199668, "副本ID: 404", reply_to=130, topic=999)
        at_text = "@bbtest 来"
        at = _event(132, 111, at_text, topic=123, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_non_forum_chat_with_all_topic_zero_emits(self):
        identity_id = self._prepare_identity()
        now = 13000.0
        opener = _event(140, 111, ".开启虚天殿", topic=0, forum_topic=False)
        announce = _event(141, 7900199668, "副本ID: 405", reply_to=140, topic=0, forum_topic=False)
        at_text = "@bbtest 来"
        at = _event(142, 111, at_text, topic=0, forum_topic=False, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(".加入副本 405", track=False, send_as_id=identity_id, priority="urgent_reactive")

    async def test_same_dungeon_duplicate_mentions_dedup(self):
        identity_id = self._prepare_identity()
        now = 14000.0
        opener = _event(150, 111, ".开启虚天殿")
        announce = _event(151, 7900199668, "副本ID: 406", reply_to=150)
        at_text = "@bbtest 来"
        at1 = _event(152, 111, at_text, entities=[MessageEntityMention(0, 7)])
        at2 = _event(153, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at1, now=now + 2)
            handled1 = await join_dungeon.handle_dungeon_join_mention(at1, at_text, now + 2)
            join_dungeon.record_game_group_message(at2, now=now + 3)
            handled2 = await join_dungeon.handle_dungeon_join_mention(at2, at_text, now + 3)

        self.assertTrue(handled1)
        self.assertFalse(handled2)
        send_mock.assert_awaited_once_with(".加入副本 406", track=False, send_as_id=identity_id, priority="urgent_reactive")

    def test_extract_dungeon_id_known_formats(self):
        self.assertEqual("407", join_dungeon._parse_dungeon_id("副本ID: 407"))
        self.assertEqual("408", join_dungeon._parse_dungeon_id("副本ID:408"))
        self.assertEqual("409", join_dungeon._parse_dungeon_id("副本ID：409"))
        self.assertEqual("410", join_dungeon._parse_dungeon_id(".加入副本 410"))
        self.assertEqual("411", join_dungeon._parse_dungeon_id("【坠魔谷·集结】\n房间ID: 411"))
        self.assertEqual("411", join_dungeon._parse_dungeon_id(".加入坠魔谷 411"))
        self.assertEqual("411", join_dungeon._parse_dungeon_id(".加入黄龙山 411"))
        self.assertEqual("", join_dungeon._parse_dungeon_id("hello world 411"))
        self.assertEqual(
            "",
            join_dungeon._parse_dungeon_id(
                "【血色试炼·集结】\n"
                "@fixuuu 正在召集同伴，准备进入【血色禁地】采药试炼！\n"
                "房间ID: 707\n"
                "其他道友可使用 .加入血色试炼 707 加入队伍！(最多 3 人)"
            ),
        )

    def test_infer_dungeon_kind_from_mainline_formats(self):
        self.assertEqual(join_dungeon.DUNGEON_KIND_VIRTUAL_HALL, join_dungeon._infer_dungeon_kind("【虚天殿已开启】\n副本ID: 411"))
        self.assertEqual(join_dungeon.DUNGEON_KIND_ZHUIMO, join_dungeon._infer_dungeon_kind("【坠魔谷·集结】\n房间ID: 411"))
        self.assertEqual(join_dungeon.DUNGEON_KIND_HUANGLONG, join_dungeon._infer_dungeon_kind("【黄龙山大战·集结】\n房间ID: 411"))

    def test_format_dungeon_join_command_uses_replica_kind(self):
        self.assertEqual(".加入副本 411", join_dungeon._format_dungeon_join_command("411", join_dungeon.DUNGEON_KIND_VIRTUAL_HALL))
        self.assertEqual(".加入坠魔谷 411", join_dungeon._format_dungeon_join_command("411", join_dungeon.DUNGEON_KIND_ZHUIMO))
        self.assertEqual(".加入黄龙山 411", join_dungeon._format_dungeon_join_command("411", join_dungeon.DUNGEON_KIND_HUANGLONG))

    def test_dungeon_inbox_snapshot_exposes_mainline_join_command(self):
        now = 16700.0
        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]):
            join_dungeon.record_game_group_message(
                _event(181, 7900199668, "【黄龙山大战·集结】\n房间ID: 514"),
                now=now,
            )

        with patch.object(join_dungeon.time, "time", return_value=now + 2):
            snapshot = join_dungeon.get_dungeon_join_inbox_snapshot()

        self.assertEqual("514", snapshot[-1]["dungeon_id"])
        self.assertEqual(join_dungeon.DUNGEON_KIND_HUANGLONG, snapshot[-1]["dungeon_kind"])
        self.assertEqual("黄龙山", snapshot[-1]["dungeon_name"])
        self.assertEqual(".加入黄龙山 514", snapshot[-1]["join_command"])

    def test_dungeon_ui_snapshot_exposes_commands_and_identity_state(self):
        identity_id = self._prepare_identity()
        state_module.set_dungeon_join_run_state({
            str(identity_id): {
                "pending_room_id": "515",
                "pending_msg_id": 188,
                "pending_until": 16860.0,
                "last_result": "pending",
                "updated_at": 16801.0,
            }
        })

        from model import ui

        with patch.object(ui.time, "time", return_value=16800.0):
            snapshot = ui.get_dungeon_join_snapshot()

        commands = {item["name"]: item["join_command"] for item in snapshot["commands"]}
        self.assertEqual(".加入副本", commands["虚天殿"])
        self.assertEqual(".加入坠魔谷", commands["坠魔谷"])
        self.assertEqual(".加入黄龙山", commands["黄龙山"])
        row = next(item for item in snapshot["rows"] if item["identity_id"] == identity_id)
        self.assertTrue(row["module_enabled"])
        self.assertEqual("等待回复", row["status_text"])
        self.assertEqual("515", row["pending_room_id"])

    def test_utf16_mention_slice_handles_emoji_before_mention(self):
        self.assertEqual("@bbtest", join_dungeon._slice_utf16_units("👌@bbtest 来", 2, 7))

    async def test_forum_topic_falls_back_to_reply_to_msg_id(self):
        identity_id = self._prepare_identity()
        now = 15000.0
        opener = _event(160, 111, ".开启虚天殿", reply_to=123, topic=0, forum_topic=True)
        announce = _event(161, 7900199668, "副本ID: 412", reply_to=160, topic=123, forum_topic=True)
        at_text = "@bbtest 来"
        at = _event(162, 111, at_text, reply_to=123, topic=0, forum_topic=True, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(".加入副本 412", track=False, send_as_id=identity_id, priority="urgent_reactive")

    async def test_configured_topic_id_fallback_without_forum_flag(self):
        identity_id = self._prepare_identity()
        now = 16000.0
        opener = _event(170, 111, ".开启虚天殿", reply_to=123, topic=0, forum_topic=False)
        announce = _event(171, 7900199668, "副本ID: 413", reply_to=170, topic=123, forum_topic=True)
        at_text = "@bbtest 来"
        at = _event(172, 111, at_text, reply_to=123, topic=0, forum_topic=False, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "get_game_topic_id", return_value=123), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(".加入副本 413", track=False, send_as_id=identity_id, priority="urgent_reactive")

    async def test_zhuimo_announcement_uses_zhuimo_join_command(self):
        identity_id = self._prepare_identity()
        now = 16500.0
        opener = _event(175, 111, ".坠魔谷")
        announce = _event(176, 7900199668, "【坠魔谷·集结】\n队长 @leader\n房间ID: 512", reply_to=175)
        at_text = "@bbtest 来"
        at = _event(177, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(".加入坠魔谷 512", track=False, send_as_id=identity_id, priority="urgent_reactive")

    async def test_huanglong_announcement_uses_huanglong_join_command(self):
        identity_id = self._prepare_identity()
        now = 16600.0
        opener = _event(178, 111, ".黄龙山")
        announce = _event(179, 7900199668, "【黄龙山大战·集结】\n队长 @leader\n房间ID: 513", reply_to=178)
        at_text = "@bbtest 来"
        at = _event(180, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=99))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertTrue(handled)
        send_mock.assert_awaited_once_with(".加入黄龙山 513", track=False, send_as_id=identity_id, priority="urgent_reactive")

    async def test_blood_trial_room_id_is_not_treated_as_supported_join_dungeon(self):
        self._prepare_identity()
        now = 16650.0
        opener = _event(185, 111, ".开启血色试炼")
        announce = _event(
            186,
            7900199668,
            "【血色试炼·集结】\n"
            "@fixuuu 正在召集同伴，准备进入【血色禁地】采药试炼！\n"
            "房间ID: 707\n"
            "准入境界：炼气五层 - 筑基后期\n"
            "进入次数：每日 1 次\n"
            "副本内采得的灵草会先暂存于队伍药篓，只有成功撤离时才会统一发到储物袋。\n"
            "其他道友可使用 .加入血色试炼 707 加入队伍！(最多 3 人)\n"
            "队长可随时使用 .进入血色试炼 出发。",
            reply_to=185,
        )
        at_text = "@bbtest 来"
        at = _event(187, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            handled = await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2)

        self.assertFalse(handled)
        self.assertEqual([], join_dungeon.get_dungeon_join_inbox_snapshot())
        send_mock.assert_not_awaited()

    async def test_duplicate_mentions_during_send_queue_are_reserved(self):
        identity_id = self._prepare_identity()
        now = 17000.0
        opener = _event(180, 111, ".开启虚天殿")
        announce = _event(181, 7900199668, "副本ID: 414", reply_to=180)
        at_text = "@bbtest 来"
        at1 = _event(182, 111, at_text, entities=[MessageEntityMention(0, 7)])
        at2 = _event(183, 111, at_text, entities=[MessageEntityMention(0, 7)])
        release_send = asyncio.Event()
        calls = []

        async def slow_send(*args, **kwargs):
            calls.append((args, kwargs))
            await release_send.wait()
            return SimpleNamespace(id=99, sent_at=now + 10)

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=slow_send), \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at1, now=now + 2)
            first = asyncio.create_task(join_dungeon.handle_dungeon_join_mention(at1, at_text, now + 2))
            await asyncio.sleep(0)
            join_dungeon.record_game_group_message(at2, now=now + 3)
            second = await join_dungeon.handle_dungeon_join_mention(at2, at_text, now + 3)
            release_send.set()
            first_result = await first

        self.assertTrue(first_result)
        self.assertFalse(second)
        self.assertEqual(1, len(calls))
        self.assertEqual(".加入副本 414", calls[0][0][0])
        self.assertEqual(identity_id, calls[0][1]["send_as_id"])
        self.assertEqual("urgent_reactive", calls[0][1]["priority"])

    async def test_fast_retry_join_resends_once_while_pending(self):
        identity_id = self._prepare_identity()
        now = time.time()
        join_dungeon._mark_join_sent(identity_id, "414", now, msg_id=99)

        with patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=100, sent_at=now + 3))) as send_mock, \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            first = await join_dungeon._retry_join_once(identity_id, "414", join_dungeon.DUNGEON_KIND_VIRTUAL_HALL, ".加入副本 414", 99, delay_sec=0)
            second = await join_dungeon._retry_join_once(identity_id, "414", join_dungeon.DUNGEON_KIND_VIRTUAL_HALL, ".加入副本 414", 100, delay_sec=0)

        self.assertTrue(first)
        self.assertFalse(second)
        send_mock.assert_awaited_once_with(
            ".加入副本 414",
            track=False,
            send_as_id=identity_id,
            priority="urgent_reactive",
        )
        record = state_module.get_dungeon_join_run_state()[str(identity_id)]
        self.assertEqual(1, record["retry_count"])
        self.assertEqual(100, record["pending_msg_id"])

    async def test_cooldown_reply_blocks_later_join(self):
        identity_id = self._prepare_identity()
        now = 18000.0
        opener = _event(190, 111, ".开启虚天殿")
        announce = _event(191, 7900199668, "副本ID: 415", reply_to=190)
        at_text = "@bbtest 来"
        at = _event(192, 111, at_text, entities=[MessageEntityMention(0, 7)])
        cd_reply = _event(
            194,
            7900199668,
            "无法立即加入新副本，请在 1小时2分钟3秒 后再试",
            reply_to=193,
        )

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=193, sent_at=now + 2))), \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            self.assertTrue(await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2))
            self.assertTrue(await join_dungeon.handle_dungeon_join_bot_message(cd_reply, cd_reply.raw_text, now + 3))

        record = state_module.get_dungeon_join_run_state()[str(identity_id)]
        self.assertFalse(record["participating"])
        self.assertGreater(record["cooldown_until"], now + 3600)

    async def test_dungeon_join_workflow_log_tracks_cooldown_guard_skip(self):
        identity_id = self._prepare_identity()
        now = 18000.0
        opener = _event(190, 111, ".开启虚天殿")
        announce = _event(191, 7900199668, "副本ID: 415", reply_to=190)
        at_text = "@bbtest 来"
        at = _event(192, 111, at_text, entities=[MessageEntityMention(0, 7)])
        cd_reply = _event(
            194,
            7900199668,
            "无法立即加入新副本，请在 1小时2分钟3秒 后再试",
            reply_to=193,
        )
        next_opener = _event(195, 111, ".开启虚天殿")
        next_announce = _event(196, 7900199668, "副本ID: 416", reply_to=195)
        next_at = _event(197, 111, at_text, entities=[MessageEntityMention(0, 7)])

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir), \
                    patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                    patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=193, sent_at=now + 2))), \
                    patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
                join_dungeon.record_game_group_message(opener, now=now)
                join_dungeon.record_game_group_message(announce, now=now + 1)
                join_dungeon.record_game_group_message(at, now=now + 2)
                self.assertTrue(await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2))
                self.assertTrue(await join_dungeon.handle_dungeon_join_bot_message(cd_reply, cd_reply.raw_text, now + 3))
                join_dungeon.record_game_group_message(next_opener, now=now + 10)
                join_dungeon.record_game_group_message(next_announce, now=now + 11)
                join_dungeon.record_game_group_message(next_at, now=now + 12)
                handled = await join_dungeon.handle_dungeon_join_mention(next_at, at_text, now + 12)
                self.assertFalse(handled)
                events = _read_workflow_events(tmpdir)

        self.assertTrue(any(
            event.get("event") == "join_cooldown"
            and event.get("decision") == "join_cooldown_observed"
            and event.get("detail", {}).get("wait_sec") == 3723
            and "无法立即加入新副本" in event.get("text", "")
            for event in events
        ))
        self.assertTrue(any(
            event.get("event") == "join_skipped"
            and event.get("decision") == "join_guard_cooldown"
            and event.get("detail", {}).get("reason") == "cooldown"
            and event.get("op_id") == f"{identity_id}:416"
            for event in events
        ))

        next_opener = _event(195, 111, ".开启虚天殿")
        next_announce = _event(196, 7900199668, "副本ID: 416", reply_to=195)
        next_at = _event(197, 111, at_text, entities=[MessageEntityMention(0, 7)])
        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(next_opener, now=now + 10)
            join_dungeon.record_game_group_message(next_announce, now=now + 11)
            join_dungeon.record_game_group_message(next_at, now=now + 12)
            handled = await join_dungeon.handle_dungeon_join_mention(next_at, at_text, now + 12)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_success_reply_marks_participating_and_blocks_next_join(self):
        identity_id = self._prepare_identity(username="bbtest")
        now = 19000.0
        opener = _event(200, 111, ".开启虚天殿")
        announce = _event(201, 7900199668, "副本ID: 417", reply_to=200)
        at_text = "@bbtest 来"
        at = _event(202, 111, at_text, entities=[MessageEntityMention(0, 7)])
        success_reply = _event(
            204,
            7900199668,
            "@bbtest 已成功加入副本 417\n当前队伍:\n- @bbtest",
            reply_to=203,
        )

        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=203, sent_at=now + 2))), \
                patch.object(join_dungeon, "send_audit_log", new=AsyncMock()):
            join_dungeon.record_game_group_message(opener, now=now)
            join_dungeon.record_game_group_message(announce, now=now + 1)
            join_dungeon.record_game_group_message(at, now=now + 2)
            self.assertTrue(await join_dungeon.handle_dungeon_join_mention(at, at_text, now + 2))
            self.assertTrue(await join_dungeon.handle_dungeon_join_bot_message(success_reply, success_reply.raw_text, now + 3))

        record = state_module.get_dungeon_join_run_state()[str(identity_id)]
        self.assertTrue(record["participating"])
        self.assertEqual("417", record["room_id"])

        next_opener = _event(205, 111, ".开启虚天殿")
        next_announce = _event(206, 7900199668, "副本ID: 418", reply_to=205)
        next_at = _event(207, 111, at_text, entities=[MessageEntityMention(0, 7)])
        with patch.object(join_dungeon, "get_game_bot_ids", return_value=[7900199668]), \
                patch.object(join_dungeon, "send_game_command", new=AsyncMock()) as send_mock:
            join_dungeon.record_game_group_message(next_opener, now=now + 10)
            join_dungeon.record_game_group_message(next_announce, now=now + 11)
            join_dungeon.record_game_group_message(next_at, now=now + 12)
            handled = await join_dungeon.handle_dungeon_join_mention(next_at, at_text, now + 12)

        self.assertFalse(handled)
        send_mock.assert_not_awaited()

    async def test_success_progress_sets_cooldown_without_next_probe(self):
        identity_id = self._prepare_identity(username="bbtest")
        now = 20000.0
        state_module.set_dungeon_join_run_state({
            str(identity_id): {
                "participating": True,
                "room_id": "419",
                "joined_at": now - 600,
                "active_until": now + 3000,
            }
        })
        progress = _event(210, 7900199668, "【鼎前抉择】\n队长 @bbtest 面临抉择。")

        handled = await join_dungeon.handle_dungeon_join_bot_message(progress, progress.raw_text, now)

        self.assertTrue(handled)
        record = state_module.get_dungeon_join_run_state()[str(identity_id)]
        self.assertFalse(record["participating"])
        self.assertGreater(record["cooldown_until"], now + 120 * 60)

    async def test_success_progress_clears_same_room_participants_from_leader_text(self):
        leader_id = self._prepare_identity(identity_id=90001, username="leader")
        member_id = self._prepare_identity(identity_id=90002, username="member")
        other_id = self._prepare_identity(identity_id=90003, username="other")
        now = 20200.0
        state_module.set_dungeon_join_run_state({
            str(leader_id): {
                "participating": True,
                "room_id": "1325",
                "joined_at": now - 600,
                "active_until": now + 3000,
            },
            str(member_id): {
                "participating": True,
                "room_id": "1325",
                "joined_at": now - 590,
                "active_until": now + 3000,
            },
            str(other_id): {
                "participating": True,
                "room_id": "1326",
                "joined_at": now - 300,
                "active_until": now + 3000,
            },
        })
        progress = _event(
            211,
            7900199668,
            "【鼎前抉择】\n队长 @leader，请在 120秒 内抉择：\n- 输入 .争鼎 求稳 / .争鼎 夺鼎",
        )

        handled = await join_dungeon.handle_dungeon_join_bot_message(progress, progress.raw_text, now)

        self.assertTrue(handled)
        records = state_module.get_dungeon_join_run_state()
        self.assertFalse(records[str(leader_id)]["participating"])
        self.assertFalse(records[str(member_id)]["participating"])
        self.assertTrue(records[str(other_id)]["participating"])

    async def test_external_success_progress_does_not_clear_local_active_room(self):
        identity_id = self._prepare_identity(username="bbtest")
        now = 20300.0
        state_module.set_dungeon_join_run_state({
            str(identity_id): {
                "participating": True,
                "room_id": "1325",
                "joined_at": now - 600,
                "active_until": now + 3000,
            }
        })
        progress = _event(
            212,
            7900199668,
            "【鼎前抉择】\n队长 @TrickPlayer，请在 120秒 内抉择：\n- 点击下方按钮，或输入 .争鼎 求稳 / .争鼎 夺鼎",
        )

        handled = await join_dungeon.handle_dungeon_join_bot_message(progress, progress.raw_text, now)

        self.assertFalse(handled)
        record = state_module.get_dungeon_join_run_state()[str(identity_id)]
        self.assertTrue(record["participating"])
        self.assertEqual("1325", record["room_id"])

    async def test_terminal_afterhall_text_clears_single_active_room(self):
        first_id = self._prepare_identity(identity_id=90001, username="first")
        second_id = self._prepare_identity(identity_id=90002, username="second")
        now = 20400.0
        state_module.set_dungeon_join_run_state({
            str(first_id): {
                "participating": True,
                "room_id": "1325",
                "joined_at": now - 600,
                "active_until": now + 3000,
            },
            str(second_id): {
                "participating": True,
                "room_id": "1325",
                "joined_at": now - 590,
                "active_until": now + 3000,
            },
        })
        progress = _event(
            213,
            7900199668,
            "【后殿冲关止步】\n鼎压攀至极限，后殿炉心彻底失控，将众人尽数震退。\n好在第三关结算所得早已锁定，这次失去的只有后殿追加机缘。",
        )

        handled = await join_dungeon.handle_dungeon_join_bot_message(progress, progress.raw_text, now)

        self.assertTrue(handled)
        records = state_module.get_dungeon_join_run_state()
        self.assertFalse(records[str(first_id)]["participating"])
        self.assertFalse(records[str(second_id)]["participating"])

    async def test_progress_mentions_after_team_section_do_not_clear_identity(self):
        identity_id = self._prepare_identity(username="bbtest")
        now = 20500.0
        state_module.set_dungeon_join_run_state({
            str(identity_id): {
                "participating": True,
                "room_id": "419",
                "joined_at": now - 600,
                "active_until": now + 3000,
            }
        })
        progress = _event(
            215,
            7900199668,
            "【鼎前抉择】\n当前队伍:\n- @other\n【卦象词条】\n@bbtest 仅出现在说明里",
        )

        handled = await join_dungeon.handle_dungeon_join_bot_message(progress, progress.raw_text, now)

        self.assertFalse(handled)
        record = state_module.get_dungeon_join_run_state()[str(identity_id)]
        self.assertTrue(record["participating"])
        self.assertEqual("419", record["room_id"])

    async def test_progress_uses_usernames_inside_team_section(self):
        identity_id = self._prepare_identity(username="bbtest")
        now = 20600.0
        state_module.set_dungeon_join_run_state({
            str(identity_id): {
                "participating": True,
                "room_id": "419",
                "joined_at": now - 600,
                "active_until": now + 3000,
            }
        })
        progress = _event(
            216,
            7900199668,
            "【鼎前抉择】\n当前队伍:\n- @leader\n- @bbtest\n【卦象词条】\n@other",
        )

        handled = await join_dungeon.handle_dungeon_join_bot_message(progress, progress.raw_text, now)

        self.assertTrue(handled)
        record = state_module.get_dungeon_join_run_state()[str(identity_id)]
        self.assertFalse(record["participating"])
        self.assertGreater(record["cooldown_until"], now + 120 * 60)

    async def test_new_replica_join_text_marks_success(self):
        identity_id = self._prepare_identity(username="bbtest")
        now = 21000.0
        state_module.set_dungeon_join_run_state({
            str(identity_id): {
                "pending_msg_id": 221,
                "pending_room_id": "420",
                "pending_until": now + 300,
            }
        })
        reply = _event(
            222,
            7900199668,
            "@bbtest 已成功加入坠魔谷 420\n当前队伍 (2/5):\n - @leader\n - @bbtest",
            reply_to=221,
        )

        handled = await join_dungeon.handle_dungeon_join_bot_message(reply, reply.raw_text, now)

        self.assertTrue(handled)
        record = state_module.get_dungeon_join_run_state()[str(identity_id)]
        self.assertTrue(record["participating"])
        self.assertEqual("420", record["room_id"])


if __name__ == "__main__":
    unittest.main()
