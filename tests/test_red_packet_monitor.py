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
        self.assertIsNone(red_packet_monitor.parse_red_packet_created("红包已抢完"))

    async def test_target_group_candidate_is_observed_once_across_clients(self):
        event = SimpleNamespace(
            raw_text=".发红包 88 20",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458347,
            sender_id=123,
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

    async def test_only_accepted_high_value_packet_schedules_finite_alerts(self):
        command = SimpleNamespace(
            raw_text=".发红包 50 2",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458350,
            sender_id=123,
        )
        created = SimpleNamespace(
            raw_text="🧧 【LDC 红包】｜@user 50.00 LDC / 2 份 请直接点击下方按钮抢红包",
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
        )
        blocked = SimpleNamespace(
            raw_text="⛔ 普通用户单个红包金额过高，已拦截。当前上限：500.00 LDC",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458353,
            sender_id=456,
        )
        low_command = SimpleNamespace(
            raw_text=".发红包 10 5",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458354,
            sender_id=123,
        )
        low_created = SimpleNamespace(
            raw_text="🧧 【LDC 红包】｜@user 10.00 LDC / 5 份",
            chat=SimpleNamespace(username="ja_netfilter_group"),
            chat_id=-100123,
            id=458355,
            sender_id=456,
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
