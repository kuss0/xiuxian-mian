import time
import unittest
import tempfile
import sqlite3
from unittest.mock import patch

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

    def test_dungeon_dissolve_open_gap_is_not_global_lock_breach(self):
        now = time.time()
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(now - 10, 3765328695, ".解散副本"),
            _event(now - 2, 3711993781, ".开启虚天殿"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_dungeon_fast_chain_is_not_send_burst(self):
        now = time.time()
        events = [
            _event(now - 55, 3765328695, ".开启虚天殿"),
            _event(now - 40, 3800619925, ".加入副本 969"),
            _event(now - 39, 8659059191, ".加入副本 969"),
            _event(now - 38, 3947749189, ".加入副本 969"),
            _event(now - 37, 3943773722, ".加入副本 969"),
            _event(now - 20, 3765328695, ".选择道路 冰"),
            _event(now - 12, 3765328695, ".阵策 势"),
            _event(now - 4, 3765328695, ".后殿阵策 卦"),
        ]
        cfg = self._config()
        cfg.total_2m_limit = 5
        cfg.min_any_gap_sec = 12

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_dungeon_open_repeat_still_fuses(self):
        now = time.time()
        events = [
            _event(now - 40, 3922509228, ".开启虚天殿"),
            _event(now - 9, 3922509228, ".开启虚天殿"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_non_dungeon_commands_still_send_burst(self):
        now = time.time()
        events = [
            _event(now - 50, 1001, ".我的侍妾"),
            _event(now - 45, 1002, ".小世界"),
            _event(now - 40, 1003, ".灵树状态"),
            _event(now - 35, 1004, ".储物袋"),
            _event(now - 30, 1005, ".状态"),
        ]
        cfg = self._config()
        cfg.total_2m_limit = 5

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("send burst", breach)

    def test_non_virtual_dungeon_join_repeat_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 30, sender_id, ".加入坠魔谷 394"),
            _event(now - 28, sender_id, ".加入坠魔谷 394"),
            _event(now - 26, sender_id, ".加入黄龙山 395"),
            _event(now - 24, sender_id, ".加入黄龙山 395"),
            _event(now - 22, sender_id, ".加入苍坤洞府 396"),
            _event(now - 20, sender_id, ".加入苍坤洞府 396"),
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

    def test_concubine_recovery_status_recheck_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 3922509228
        events = [
            _event(now - 90, sender_id, ".我的侍妾"),
            _event(now - 64, sender_id, ".每日问安"),
            _event(now - 31, sender_id, ".我的侍妾"),
            _event(now - 9, sender_id, ".储物袋"),
            _event(now - 1, sender_id, ".赠予侍妾 灵石*270"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_direct_concubine_status_repeat_still_fuses(self):
        now = time.time()
        sender_id = 3922509228
        events = [
            _event(now - 90, sender_id, ".我的侍妾"),
            _event(now - 31, sender_id, ".我的侍妾"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())
        self.assertIn("same command repeat", breach)

    def test_stale_fuse_marker_re_fuses_when_global_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = safety_watchdog.Path(tmpdir)
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("INSERT INTO meta(key, value) VALUES('global_enabled', '1')")
                conn.commit()
            marker = state_dir / "safety_watchdog_fused.json"
            marker.write_text(
                safety_watchdog.json.dumps({"reason": "old", "actions": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            cfg = self._config()
            cfg.project_root = root
            cfg.action = "soft"
            cfg.dry_run = False

            with patch.object(safety_watchdog, "send_log_via_bot", return_value="sent") as send_mock:
                safety_watchdog.perform_fuse(cfg, {}, "new breach")

            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                value = conn.execute("SELECT value FROM meta WHERE key = 'global_enabled'").fetchone()[0]
            payload = safety_watchdog.json.loads(marker.read_text(encoding="utf-8"))

        self.assertEqual("0", value)
        self.assertEqual("new breach", payload["reason"])
        send_mock.assert_not_called()

    def test_existing_fuse_marker_keeps_disabled_global_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = safety_watchdog.Path(tmpdir)
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("INSERT INTO meta(key, value) VALUES('global_enabled', '0')")
                conn.commit()
            marker = state_dir / "safety_watchdog_fused.json"
            marker.write_text(
                safety_watchdog.json.dumps({"reason": "old", "actions": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            cfg = self._config()
            cfg.project_root = root
            cfg.action = "soft"
            cfg.dry_run = False

            safety_watchdog.perform_fuse(cfg, {}, "new breach")

            payload = safety_watchdog.json.loads(marker.read_text(encoding="utf-8"))

        self.assertEqual("old", payload["reason"])

    def test_existing_same_reason_marker_does_not_re_fuse_even_when_global_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = safety_watchdog.Path(tmpdir)
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("INSERT INTO meta(key, value) VALUES('global_enabled', '1')")
                conn.commit()
            marker = state_dir / "safety_watchdog_fused.json"
            marker.write_text(
                safety_watchdog.json.dumps({"reason": "same breach", "actions": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            cfg = self._config()
            cfg.project_root = root
            cfg.action = "soft"
            cfg.dry_run = False

            safety_watchdog.perform_fuse(cfg, {}, "same breach")

            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                value = conn.execute("SELECT value FROM meta WHERE key = 'global_enabled'").fetchone()[0]
            payload = safety_watchdog.json.loads(marker.read_text(encoding="utf-8"))

        self.assertEqual("1", value)
        self.assertEqual("same breach", payload["reason"])


if __name__ == "__main__":
    unittest.main()
