import time
import unittest

from tools import safety_watchdog


def _event(epoch, sender_id, text):
    return {
        "event_type": "sent",
        "_epoch": float(epoch),
        "sender_id": int(sender_id),
        "text": text,
    }


class SafetyWatchdogTests(unittest.TestCase):
    def _config(self):
        return safety_watchdog.WatchdogConfig(
            project_root=safety_watchdog.Path("/tmp/xiuxian-test"),
            service_name="xiuxian",
            interval_sec=15,
            action="stop",
            dry_run=True,
            max_lines=1000,
            min_any_gap_sec=0,
            total_2m_limit=99,
            total_5m_limit=99,
            total_15m_limit=99,
            same_command_gap_sec=60,
            guarded_repeat_gap_sec=90,
            guarded_max_attempts_45m=99,
            guarded_fourth_min_span_sec=14 * 60,
            refresh_repeat_gap_sec=4 * 60,
            refresh_max_attempts_90m=10,
            journal_check_interval_sec=60,
        )

    def test_small_world_tool_chain_does_not_fuse_repeat_query(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 120, sender_id, ".小世界"),
            _event(now - 90, sender_id, ".收割香火"),
            _event(now - 64, sender_id, ".小世界"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_small_world_direct_repeat_still_fuses(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 120, sender_id, ".小世界"),
            _event(now - 64, sender_id, ".小世界"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())
        self.assertIn("same command repeat", breach)


if __name__ == "__main__":
    unittest.main()
