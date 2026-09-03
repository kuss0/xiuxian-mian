import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model.features import red_packet_monitor


class RedPacketMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._seen_snapshot = copy.copy(red_packet_monitor._SEEN_CANDIDATES)
        red_packet_monitor._SEEN_CANDIDATES.clear()

    def tearDown(self):
        red_packet_monitor._SEEN_CANDIDATES.clear()
        red_packet_monitor._SEEN_CANDIDATES.update(self._seen_snapshot)
        red_packet_monitor._PENDING_COMMANDS.clear()
        red_packet_monitor._PENDING_CREATED.clear()
        red_packet_monitor._ALERTED_PACKETS.clear()

    def test_parse_exact_red_packet_command(self):
        self.assertEqual(
            {"amount": 50.0, "count": 10},
            red_packet_monitor.parse_red_packet_command(".发红包 50 10"),
        )
        self.assertIsNone(red_packet_monitor.parse_red_packet_command("发红包 50 10"))
        self.assertIsNone(red_packet_monitor.parse_red_packet_command(".发红包 五十 10"))

    def test_parse_created_red_packet_card(self):
        self.assertEqual(
            {"amount": 10.0, "count": 5},
            red_packet_monitor.parse_red_packet_created(
                "🧧 【LDC 红包】｜@user 10.00 LDC / 5 份 请直接点击下方按钮抢红包"
            ),
        )
        self.assertEqual(
            {"amount": 100.0, "count": 8},
            red_packet_monitor.parse_red_packet_created(
                "🧧 【LDC 红包】｜@incomingsnow 100.00 LDC / 8 份 请直接点击下方按钮抢红包 需已绑定论坛｜30 分钟"
            ),
        )
        self.assertEqual(
            {"amount": 100.0, "count": 8},
            red_packet_monitor.parse_red_packet_created(
                "🧧【LDC\u00a0红包】 @incomingsnow １００.００ LDC／８ 个"
            ),
        )
        self.assertIsNone(red_packet_monitor.parse_red_packet_created("红包已抢完"))

    def test_expired_card_is_not_a_new_packet(self):
        """2026-09-03 02:20 实录：过期播报曾被当成新红包挂进待配对表。"""
        self.assertIsNone(
            red_packet_monitor.parse_red_packet_created(
                "⌛ 【LDC 红包已过期】\n"
                "发包者：@yyyyy0123210\n"
                "总额：2000.00 LDC\n"
                "已抢：1651.19 LDC / 9 人\n"
                "未抢：348.81 LDC / 1 份"
            )
        )

    def test_other_ldc_cards_are_not_new_packets(self):
        for text in (
            "🧧 【LDC 红包榜 · 本月】 发包榜 1. @a｜2000.00 LDC｜1 包",
            "🧧 【LDC 讨红包到账】 @a 给 @b 打发了 2.00 LDC / 1 份",
            "🧧 【LDC 定向红包已到账】 100.00 LDC / 5 份",
        ):
            with self.subTest(text=text[:24]):
                self.assertIsNone(red_packet_monitor.parse_red_packet_created(text))

    def test_parse_multiline_created_card(self):
        self.assertEqual(
            {"amount": 500.0, "count": 10},
            red_packet_monitor.parse_red_packet_created(
                "🧧 【LDC 红包】｜@yyyyy0123210\n"
                "500.00 LDC / 10 份\n"
                "请直接点击下方按钮抢红包"
            ),
        )

    async def test_target_group_candidate_is_observed_once_across_clients(self):
        event = SimpleNamespace(
            raw_text=".发红包 88 20",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458347,
            sender_id=123,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        with patch.object(red_packet_monitor, "console_log") as log_mock:
            self.assertTrue(await red_packet_monitor.observe_red_packet_candidate(event))
            self.assertTrue(await red_packet_monitor.observe_red_packet_candidate(event))

        log_mock.assert_called_once()
        self.assertIn("type=message", log_mock.call_args.args[0])
        self.assertIn("amount=88 count=20", log_mock.call_args.args[0])

    async def test_edited_candidate_is_observed_after_new_message(self):
        event = SimpleNamespace(
            raw_text="红包已开启",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458348,
            sender_id=456,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        with patch.object(red_packet_monitor, "console_log") as log_mock:
            self.assertTrue(await red_packet_monitor.observe_red_packet_candidate(event))
            event.raw_text = "红包已领取 1/10"
            self.assertTrue(
                await red_packet_monitor.observe_red_packet_candidate(
                    event,
                    event_type="edit",
                )
            )

        self.assertEqual(2, log_mock.call_count)
        self.assertIn("type=edit", log_mock.call_args.args[0])

    async def test_low_value_created_card_is_logged_as_parsed_without_alerting(self):
        event = SimpleNamespace(
            raw_text="🧧 【LDC 红包】｜@user 10.00 LDC / 5 份",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458349,
            sender_id=456,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        with patch.object(red_packet_monitor, "console_log") as log_mock, patch.object(
            red_packet_monitor, "send_audit_log", new=AsyncMock()
        ) as audit_mock:
            self.assertTrue(await red_packet_monitor.observe_red_packet_candidate(event))

        self.assertIn("created=below_threshold:10/5", log_mock.call_args.args[0])
        audit_mock.assert_not_awaited()

    async def test_only_accepted_high_value_packet_schedules_finite_alerts(self):
        command = SimpleNamespace(
            raw_text=".发红包 50 2",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458350,
            sender_id=123,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        created = SimpleNamespace(
            raw_text=(
                "🧧 【LDC 红包】｜@user\n"
                "50.00 LDC / 2 份\n"
                "请直接点击下方按钮抢红包"
            ),
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458351,
            sender_id=456,
            message=SimpleNamespace(
                reply_to=SimpleNamespace(
                    reply_to_top_id=458347,
                    reply_to_msg_id=458350,
                )
            ),
        )
        with patch.object(red_packet_monitor, "console_log"), patch.object(
            red_packet_monitor, "send_audit_log", new=AsyncMock()
        ) as audit_mock, patch.object(
            red_packet_monitor,
            "send_log_bot_notification",
            new=AsyncMock(return_value=True),
        ) as channel_mock, patch.object(
            red_packet_monitor, "_RED_PACKET_ALERT_INTERVAL_SEC", 0
        ), patch.object(red_packet_monitor, "_RED_PACKET_ALERT_COUNT", 3):
            await red_packet_monitor.observe_red_packet_candidate(command)
            await red_packet_monitor.observe_red_packet_candidate(created)
            await red_packet_monitor.drain_red_packet_alert_tasks()

        self.assertEqual(3, audit_mock.await_count)
        self.assertEqual(3, channel_mock.await_count)
        self.assertTrue(
            all(
                call.args[0] == red_packet_monitor.RED_PACKET_NOTIFICATION_CHAT_ID
                for call in channel_mock.await_args_list
            )
        )
        self.assertTrue(all(call.kwargs["priority"] == "high" for call in audit_mock.await_args_list))
        self.assertTrue(
            all(
                "https://t.me/ja_netfilter_group/458347/458351" in call.args[0]
                for call in audit_mock.await_args_list
            )
        )

    async def test_blocked_or_low_value_packet_does_not_alert(self):
        command = SimpleNamespace(
            raw_text=".发红包 5000 10",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458352,
            sender_id=123,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        blocked = SimpleNamespace(
            raw_text="⛔ 普通用户单个红包金额过高，已拦截。当前上限：500.00 LDC",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458353,
            sender_id=456,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        low_command = SimpleNamespace(
            raw_text=".发红包 10 5",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458354,
            sender_id=123,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        low_created = SimpleNamespace(
            raw_text="🧧 【LDC 红包】｜@user 10.00 LDC / 5 份",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458355,
            sender_id=456,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        with patch.object(red_packet_monitor, "console_log"), patch.object(
            red_packet_monitor, "send_audit_log", new=AsyncMock()
        ) as audit_mock, patch.object(
            red_packet_monitor,
            "send_log_bot_notification",
            new=AsyncMock(return_value=True),
        ) as channel_mock:
            await red_packet_monitor.observe_red_packet_candidate(command)
            await red_packet_monitor.observe_red_packet_candidate(blocked)
            await red_packet_monitor.observe_red_packet_candidate(low_command)
            await red_packet_monitor.observe_red_packet_candidate(low_created)
            await red_packet_monitor.drain_red_packet_alert_tasks()

        audit_mock.assert_not_awaited()
        channel_mock.assert_not_awaited()

    async def test_other_group_is_ignored(self):
        event = SimpleNamespace(
            raw_text=".发红包 88 20",
            chat=SimpleNamespace(username="other_group"),
            chat_id=-100456,
            id=1,
            sender_id=123,
            get_chat=AsyncMock(),
        )
        with patch.object(red_packet_monitor, "console_log") as log_mock:
            self.assertFalse(await red_packet_monitor.observe_red_packet_candidate(event))
        log_mock.assert_not_called()

    async def test_same_group_other_topic_is_ignored(self):
        """真正的"别的话题"：reply_to_top_id 指向另一个话题。

        这个用例原本用的是 `reply_to_top_id=0, reply_to_msg_id=458347`，
        并断言应当忽略 —— 但那恰恰是"直接发进话题 458347"的形态，
        生产里 362 条相关消息有 354 条长这样。旧断言把缺陷钉成了正确行为，
        导致测试全绿而红包提醒从未触发。见下面的 direct-post 用例。
        """
        event = SimpleNamespace(
            raw_text=".讨红包",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-1001680975844,
            id=11720276,
            sender_id=8789843163,
            message=SimpleNamespace(
                reply_to=SimpleNamespace(
                    reply_to_top_id=999001,
                    reply_to_msg_id=11720000,
                )
            ),
        )
        with patch.object(red_packet_monitor, "console_log") as log_mock:
            self.assertFalse(await red_packet_monitor.observe_red_packet_candidate(event))
        log_mock.assert_not_called()

    async def test_message_posted_directly_into_topic_is_observed(self):
        """直接发进话题：reply_to_top_id 为空，话题 ID 落在 reply_to_msg_id。"""
        event = SimpleNamespace(
            raw_text=".讨红包",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-1001680975844,
            id=11720276,
            sender_id=8789843163,
            message=SimpleNamespace(
                reply_to=SimpleNamespace(
                    reply_to_top_id=0,
                    reply_to_msg_id=458347,
                )
            ),
        )
        with patch.object(red_packet_monitor, "console_log") as log_mock:
            self.assertTrue(await red_packet_monitor.observe_red_packet_candidate(event))
        log_mock.assert_called_once()

    async def test_production_shape_single_arg_command_alerts(self):
        """2026-09-02 11:05 生产实录：`.发红包 50` + 播报卡片，两条都直接发进话题。

        修复前这两条会在 topic 过滤处被丢掉，命令也因缺少份数而解析失败，
        于是一次告警都没有。份数由 BOT 定为 10 份，命令里并未指定。
        """
        def _in_topic(raw_text, msg_id, sender_id):
            return SimpleNamespace(
                raw_text=raw_text,
                chat=SimpleNamespace(username="ja_netfilter_group"),
                chat_id=-1001680975844,
                id=msg_id,
                sender_id=sender_id,
                message=SimpleNamespace(
                    reply_to=SimpleNamespace(reply_to_top_id=0, reply_to_msg_id=458347)
                ),
            )

        command = _in_topic(".发红包 50", 12100900, 301299112)
        created = _in_topic(
            "🧧 【LDC 红包】｜@jfdffdddd\n"
            "50.00 LDC / 10 份\n"
            "请直接点击下方按钮抢红包\n"
            "需已绑定论坛｜30 分钟",
            12100901,
            8388633812,
        )

        with patch.object(red_packet_monitor, "console_log"), patch.object(
            red_packet_monitor, "send_audit_log", new=AsyncMock()
        ) as audit_mock, patch.object(
            red_packet_monitor,
            "send_log_bot_notification",
            new=AsyncMock(return_value=True),
        ) as channel_mock, patch.object(
            red_packet_monitor, "_RED_PACKET_ALERT_INTERVAL_SEC", 0
        ):
            await red_packet_monitor.observe_red_packet_candidate(command)
            await red_packet_monitor.observe_red_packet_candidate(created)
            await red_packet_monitor.drain_red_packet_alert_tasks()

        self.assertEqual(3, audit_mock.await_count)
        self.assertEqual(3, channel_mock.await_count)
        # 份数取自播报卡片，不是命令
        self.assertIn("数量=10 份", audit_mock.await_args_list[0].args[0])
        self.assertIn("金额=50 LDC", audit_mock.await_args_list[0].args[0])

    def test_parse_command_without_count(self):
        self.assertEqual(
            {"amount": 50.0, "count": None},
            red_packet_monitor.parse_red_packet_command(".发红包 50"),
        )
        self.assertEqual(
            {"amount": 77.77, "count": 7},
            red_packet_monitor.parse_red_packet_command(".发红包 77.77 7"),
        )
        self.assertIsNone(red_packet_monitor.parse_red_packet_command(".发红包"))

    async def test_created_packet_before_command_is_matched_afterward(self):
        created = SimpleNamespace(
            raw_text="🧧 【LDC 红包】｜@user 88.00 LDC / 20 份 请直接点击下方按钮抢红包",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458361,
            sender_id=456,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        command = SimpleNamespace(
            raw_text=".发红包 88 20",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458360,
            sender_id=123,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        with patch.object(red_packet_monitor, "console_log"), patch.object(
            red_packet_monitor, "send_audit_log", new=AsyncMock()
        ) as audit_mock, patch.object(
            red_packet_monitor,
            "send_log_bot_notification",
            new=AsyncMock(return_value=True),
        ) as channel_mock, patch.object(
            red_packet_monitor, "_RED_PACKET_ALERT_INTERVAL_SEC", 0
        ):
            await red_packet_monitor.observe_red_packet_candidate(created)
            await red_packet_monitor.observe_red_packet_candidate(command)
            await red_packet_monitor.drain_red_packet_alert_tasks()

        self.assertEqual(3, audit_mock.await_count)
        self.assertEqual(3, channel_mock.await_count)
        self.assertTrue(
            all(
                "https://t.me/ja_netfilter_group/458347/458361" in call.args[0]
                for call in audit_mock.await_args_list
            )
        )

    async def test_real_world_card_text_matches_command_and_alerts(self):
        command = SimpleNamespace(
            raw_text=".发红包 100 8",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-1001680975844,
            id=11691305,
            sender_id=7876882550,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        created = SimpleNamespace(
            raw_text="🧧 【LDC 红包】｜@incomingsnow 100.00 LDC / 8 份 请直接点击下方按钮抢红包 需已绑定论坛｜30 分钟",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-1001680975844,
            id=11691310,
            sender_id=8388633812,
            message=SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=458347)),
        )
        with patch.object(red_packet_monitor, "console_log"), patch.object(
            red_packet_monitor, "send_audit_log", new=AsyncMock()
        ) as audit_mock, patch.object(
            red_packet_monitor,
            "send_log_bot_notification",
            new=AsyncMock(return_value=True),
        ) as channel_mock, patch.object(
            red_packet_monitor, "_RED_PACKET_ALERT_INTERVAL_SEC", 0
        ):
            await red_packet_monitor.observe_red_packet_candidate(command)
            await red_packet_monitor.observe_red_packet_candidate(created)
            await red_packet_monitor.drain_red_packet_alert_tasks()

        self.assertEqual(3, audit_mock.await_count)
        self.assertEqual(3, channel_mock.await_count)
        self.assertTrue(all("100 LDC" in call.args[0] for call in audit_mock.await_args_list))
        self.assertTrue(all("/458347/11691310" in call.args[0] for call in audit_mock.await_args_list))
