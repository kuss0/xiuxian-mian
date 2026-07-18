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

    def test_parse_exact_red_packet_command(self):
        self.assertEqual(
            {"amount": 50.0, "count": 10},
            red_packet_monitor.parse_red_packet_command(".发红包 50 10"),
        )
        self.assertIsNone(red_packet_monitor.parse_red_packet_command("发红包 50 10"))
        self.assertIsNone(red_packet_monitor.parse_red_packet_command(".发红包 五十 10"))

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
