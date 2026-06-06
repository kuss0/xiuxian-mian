import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import health_observer


class HealthObserverTests(unittest.TestCase):
    def test_parse_systemctl_show_groups_multiple_services(self):
        output = (
            "Id=xiuxian.service\n"
            "ActiveState=active\n"
            "SubState=running\n"
            "MainPID=123\n"
            "\n"
            "Id=xiuxian-safety-watchdog.service\n"
            "ActiveState=active\n"
            "SubState=running\n"
            "MainPID=456\n"
        )

        parsed = health_observer.parse_systemctl_show(output)

        self.assertEqual("running", parsed["xiuxian.service"]["SubState"])
        self.assertEqual("456", parsed["xiuxian-safety-watchdog.service"]["MainPID"])

    def test_classify_snapshot_separates_warning_and_error(self):
        services = {
            "xiuxian.service": {"ActiveState": "active", "SubState": "running"},
            "xiuxian-safety-watchdog.service": {"ActiveState": "active", "SubState": "running"},
        }

        status, reasons = health_observer.classify_snapshot(
            services,
            [{"hard_count": 0, "warn_count": 2}],
        )

        self.assertEqual("warn", status)
        self.assertIn("journal warn matches: 2", reasons)

        status, reasons = health_observer.classify_snapshot(
            {"xiuxian.service": {"ActiveState": "inactive", "SubState": "dead"}},
            [{"hard_count": 0, "warn_count": 0}],
        )

        self.assertEqual("error", status)
        self.assertIn("xiuxian.service not running: inactive/dead", reasons)

    def test_warn_line_ignores_no_resend_context(self):
        self.assertFalse(health_observer.is_warn_journal_line("启动校验：查询灵树状态（无补发）。"))
        self.assertFalse(health_observer.is_warn_journal_line("状态确认，不补发。"))
        self.assertTrue(health_observer.is_warn_journal_line("野外历练回复超时，准备补发一次。"))

    def test_warn_line_ignores_quiz_business_timeout(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "🦴 <code>@vvlvdfr</code>｜题库内超时未作答｜题库匹配 B.鼎外乾蓝冰焰"
            )
        )

    def test_warn_line_ignores_expected_dungeon_join_miss(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "[2026-06-03 22:48:41] 🧩 自动副本：收到 @，但未找到同话题/同开门人/60s 内的副本公告。"
            )
        )

    def test_warn_line_ignores_external_dispatch_skip_but_flags_send_failure(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "Jun 04 07:17:03 pve python[1990220]: 🧩 主线拉人未发送：虚天殿 1245｜@missing"
            )
        )
        self.assertTrue(
            health_observer.is_warn_journal_line(
                "Jun 04 08:01:03 pve python[1990220]: 🧩 主线拉人发送失败：虚天殿 1245｜@first(send_failed)"
            )
        )
        self.assertTrue(
            health_observer.is_warn_journal_line(
                "Jun 04 08:01:03 pve python[1990220]: 🧩 主线拉人未发送：虚天殿 1245｜@first(send_failed)"
            )
        )

    def test_hard_line_ignores_existing_fuse_marker_notice(self):
        self.assertFalse(
            health_observer.is_hard_journal_line(
                "Jun 03 18:48:25 pve python[1240250]: already fused: /opt/xiuxian-main/data/state/safety_watchdog_fused.json"
            )
        )
        self.assertTrue(
            health_observer.is_hard_journal_line(
                "Jun 03 18:48:25 pve python[1240250]: [SAFETY WATCHDOG FUSED]"
            )
        )

    def test_business_paths_follow_xiuxian_environment(self):
        now = 1_780_502_400.0
        project_root = Path("/repo")

        with patch.dict(os.environ, {"XIUXIAN_DATA_DIR": "/srv/xiuxian-data"}, clear=True):
            self.assertEqual(
                Path("/srv/xiuxian-data/messages/2026-06-04.log"),
                health_observer.current_message_log(project_root, now=now),
            )
            self.assertEqual(
                Path("/srv/xiuxian-data/state/chaogu_state.db"),
                health_observer.state_db_path(project_root),
            )

        with patch.dict(os.environ, {"XIUXIAN_MESSAGES_DIR": "/var/msg", "XIUXIAN_STATE_DIR": "/var/state"}, clear=True):
            self.assertEqual(
                Path("/var/msg/2026-06-04.log"),
                health_observer.current_message_log(project_root, now=now),
            )
            self.assertEqual(
                Path("/var/state/chaogu_state.db"),
                health_observer.state_db_path(project_root),
            )

        with patch.dict(os.environ, {"XIUXIAN_DB_FILE": "/tmp/custom.db"}, clear=True):
            self.assertEqual(Path("/tmp/custom.db"), health_observer.state_db_path(project_root))

    def test_business_message_analysis_flags_repeated_active_status_queries(self):
        now = 1_780_500_000.0
        events = [
            {"event_type": "sent", "_epoch": now - 500, "message_id": 101, "sender_id": 1, "text": ".查看闭关"},
            {"event_type": "sent", "_epoch": now - 300, "message_id": 102, "sender_id": 1, "text": ".查看闭关"},
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertEqual(2, result["active_status_counts"][".查看闭关"])
        self.assertTrue(any("active status query repeated" in item["message"] for item in result["alerts"]))

    def test_business_message_analysis_flags_cooldown_replies_to_script_sends(self):
        now = 1_780_500_000.0
        events = []
        for index, text in enumerate((".入梦寻图", ".天机代卜", ".深度闭关"), start=1):
            msg_id = 200 + index
            events.append({"event_type": "sent", "_epoch": now - 600 + index, "message_id": msg_id, "sender_id": 10, "text": text})
            events.append({
                "event_type": "message",
                "_epoch": now - 590 + index,
                "message_id": msg_id + 100,
                "reply_to_msg_id": msg_id,
                "text": "尚未重启，请在 3分钟 后再试。",
            })

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertEqual(3, result["cooldown_reply_count"])
        self.assertTrue(any("cooldown replies" in item["message"] for item in result["alerts"]))

    def test_business_db_state_flags_overdue_pending_and_stuck_phase(self):
        now = 1_780_500_000.0
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "state.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE pending_tasks(
                        msg_id INTEGER PRIMARY KEY,
                        send_as_id INTEGER NOT NULL,
                        cmd TEXT NOT NULL,
                        sent_at REAL NOT NULL,
                        retry INTEGER NOT NULL,
                        timeout REAL NOT NULL,
                        reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                        max_retry INTEGER NOT NULL DEFAULT 3,
                        priority TEXT NOT NULL DEFAULT '',
                        source_module TEXT NOT NULL DEFAULT '',
                        op_id TEXT NOT NULL DEFAULT '',
                        chain_id TEXT NOT NULL DEFAULT '',
                        delete_policy TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '');
                    CREATE TABLE identity_timers(
                        send_as_id INTEGER PRIMARY KEY,
                        next_concubine_time REAL NOT NULL DEFAULT 0,
                        next_deep_retreat_time REAL NOT NULL DEFAULT 0,
                        next_yuanying_time REAL NOT NULL DEFAULT 0
                    );
                    CREATE TABLE identity_runtime_state(
                        send_as_id INTEGER PRIMARY KEY,
                        concubine_phase TEXT NOT NULL DEFAULT 'idle',
                        deep_retreat_phase TEXT NOT NULL DEFAULT 'idle',
                        deep_retreat_summary_sent_at REAL NOT NULL DEFAULT 0,
                        yuanying_phase TEXT NOT NULL DEFAULT 'idle',
                        yuanying_summary_sent_at REAL NOT NULL DEFAULT 0,
                        tower_reply_due_at REAL NOT NULL DEFAULT 0,
                        last_tower_msg_id INTEGER NOT NULL DEFAULT 0
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO pending_tasks(msg_id, send_as_id, cmd, sent_at, timeout, retry, max_retry, source_module) VALUES(1, 42, '.入梦寻图', ?, 60, 0, 1, '侍妾')",
                    (now - 600,),
                )
                conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
                conn.execute(
                    "INSERT INTO identity_timers(send_as_id, next_concubine_time, next_deep_retreat_time, next_yuanying_time) VALUES(42, ?, ?, 0)",
                    (now - 700, now - 700),
                )
                conn.execute(
                    "INSERT INTO identity_runtime_state(send_as_id, concubine_phase, deep_retreat_phase) VALUES(42, 'dream_pending', 'summary_due')"
                )
                conn.commit()

            result = health_observer.read_db_business_state(db_path, now)

        self.assertTrue(result["available"])
        self.assertEqual(1, result["pending_total"])
        self.assertEqual(1, len(result["overdue_pending"]))
        self.assertEqual(2, len(result["stuck_phases"]))
        self.assertTrue(any("overdue pending" in item["message"] for item in result["alerts"]))
        self.assertTrue(any("stuck runtime phases" in item["message"] for item in result["alerts"]))

    def test_merge_status_promotes_business_warnings_from_ok_only(self):
        status, reasons = health_observer.merge_status(
            "ok",
            [health_observer.business_alert("active status query repeated")],
        )

        self.assertEqual("warn", status)
        self.assertIn("business warnings: 1", reasons)


if __name__ == "__main__":
    unittest.main()
