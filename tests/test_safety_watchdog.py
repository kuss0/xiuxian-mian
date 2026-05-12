import time
import unittest
import tempfile

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

    def test_reset_marker_filters_old_sent_events(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 120, sender_id, ".加入副本 394"),
            _event(now - 90, sender_id, ".加入副本 394"),
            _event(now + 10, sender_id, ".小世界"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = safety_watchdog.Path(tmpdir)
            marker_dir = root / "data" / "state"
            marker_dir.mkdir(parents=True)
            (marker_dir / "safety_watchdog_reset.json").write_text(
                safety_watchdog.json.dumps({"reset_at_epoch": now}),
                encoding="utf-8",
            )
            reset_after = safety_watchdog.get_reset_after_epoch(root)
            filtered = [
                item for item in events
                if float(item.get("_epoch", 0) or 0) >= reset_after
            ]

        self.assertEqual(1, len(filtered))
        self.assertEqual("", safety_watchdog.find_send_breach(filtered, now, self._config()))

    def test_dungeon_join_repeat_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 30, sender_id, ".加入副本 394"),
            _event(now - 28, sender_id, ".加入副本 394"),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 0

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_sect_teach_three_step_chain_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 120, sender_id, ".宗门传功"),
            _event(now - 70, sender_id, ".宗门传功"),
            _event(now - 25, sender_id, ".宗门传功"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_sect_teach_fourth_attempt_still_fuses(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 180, sender_id, ".宗门传功"),
            _event(now - 130, sender_id, ".宗门传功"),
            _event(now - 80, sender_id, ".宗门传功"),
            _event(now - 30, sender_id, ".宗门传功"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())
        self.assertIn("sect teach over attempts", breach)


if __name__ == "__main__":
    unittest.main()
