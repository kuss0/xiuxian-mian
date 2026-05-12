import atexit
import asyncio
import copy
import os
import sys
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
                "ADMIN_ID=0",
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
from model.features import join_dungeon


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
        self.assertEqual("", join_dungeon._parse_dungeon_id("hello world 411"))

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


if __name__ == "__main__":
    unittest.main()
