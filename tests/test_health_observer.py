import json
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

    def test_health_payload_flags_foreign_xiuxian_process(self):
        cfg = health_observer.ObserverConfig(
            project_root=Path("/opt/xiuxian-main"),
            services=("xiuxian.service",),
            interval_sec=60,
            journal_window_sec=600,
            max_journal_matches=12,
            max_event_lines=100,
            state_dir=Path(tempfile.mkdtemp()),
            business_window_sec=1800,
        )
        snapshot = {
            "ts": "2026-07-02 01:30:00",
            "status": "error",
            "services": {"xiuxian.service": {"ActiveState": "active", "SubState": "running"}},
            "safety": {"fused": False},
            "journals": [],
            "business": {"message_state": {}, "db_state": {}},
            "foreign_xiuxian_processes": [{"pid": 123, "cmdline": "/opt/xiuxian/xiuxian.py", "legacy": True}],
        }

        payload = health_observer.build_health_payload(snapshot, cfg)

        self.assertTrue(any(item["code"] == "foreign_xiuxian_process" for item in payload["risk_reasons"]))

    def test_warn_line_ignores_no_resend_context(self):
        self.assertFalse(health_observer.is_warn_journal_line("启动校验：查询灵树状态（无补发）。"))
        self.assertFalse(health_observer.is_warn_journal_line("状态确认，不补发。"))
        self.assertFalse(health_observer.is_warn_journal_line("野外历练回复超时，准备补发一次。"))
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "野外历练结果编辑未留存，已按正常周期恢复，原消息ID=11240885"
            )
        )
        self.assertTrue(health_observer.is_warn_journal_line("野外历练补发后仍无回复，进入下一轮。"))

    def test_warn_line_ignores_quiz_business_timeout(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "🦴 <code>@vvlvdfr</code>｜题库内超时未作答｜题库匹配 B.鼎外乾蓝冰焰"
            )
        )

    def test_warn_line_reports_send_queue_timeout(self):
        self.assertTrue(
            health_observer.is_warn_journal_line(
                "[WalterWA2000] ⏳ 指令排队超时未发送：.炼制 玄铁剑 | >45s | acc=8659059191 group=-1001680975844 topic=7310786"
            )
        )

    def test_warn_line_ignores_expected_dungeon_join_miss(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "[2026-06-03 22:48:41] 🧩 自动副本：收到 @，但未找到同话题/同开门人/60s 内的副本公告。"
            )
        )

    def test_warn_line_ignores_worker_shutdown_timeout_during_code_reload(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "Jun 06 14:50:10 pve python[2948600]: worker 优雅退出超时，强制结束。"
            )
        )

    def test_journal_match_ignores_reload_disconnected_traceback_block(self):
        lines = [
            "Jun 30 08:55:07 pve python[3618640]: Traceback (most recent call last):",
            "Jun 30 08:55:07 pve python[3618640]:   File \"/opt/xiuxian-main/model/app.py\", line 788, in _resolve_event_reply",
            "Jun 30 08:55:07 pve python[3618640]: ConnectionError: Cannot send requests while disconnected",
        ]

        self.assertTrue(health_observer.is_hard_journal_line(lines[0]))
        self.assertTrue(health_observer._is_benign_disconnected_traceback_block(lines, 0))
        self.assertTrue(health_observer._is_benign_disconnected_traceback_block(lines, 2))

    def test_wrong_session_security_line_is_warn_not_hard(self):
        line = (
            "Jul 02 09:26:44 pve python[385979]: Security error while unpacking a received message: "
            "Server replied with a wrong session ID (see FAQ for details)"
        )

        self.assertFalse(health_observer.is_hard_journal_line(line))
        self.assertTrue(health_observer.is_warn_journal_line(line))

    def test_persistent_timestamp_outdated_is_not_hard(self):
        line = (
            "Jul 04 23:21:43 pve python[1721578]: Telegram is having internal issues "
            "PersistentTimestampOutdatedError: Persistent timestamp outdated "
            "(caused by GetChannelDifferenceRequest)"
        )

        self.assertFalse(health_observer.is_hard_journal_line(line))

    def test_getting_difference_value_error_is_not_hard(self):
        line = (
            "Jul 04 23:21:56 pve python[1721578]: Getting difference for channel updates 1828482465 "
            "caused ValueError; ending getting difference prematurely until server issues are resolved"
        )

        self.assertFalse(health_observer.is_hard_journal_line(line))

    def test_hard_line_ignores_explore_rift_storm_result(self):
        self.assertFalse(
            health_observer.is_hard_journal_line(
                "Jun 17 00:01:03 pve python[44241]: [growrdick] 🕳 探寻裂缝结果：遭遇风暴 ｜ 修为 -33943"
            )
        )

    def test_warn_line_ignores_expected_phaseful_recovery_audits(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "Jun 16 23:52:50 pve python[44241]: [xuruode3] ↩️ 归位结算吃掉原指令，已补发一次：.入梦寻图"
            )
        )
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "Jun 17 00:02:45 pve python[44241]: [xuruode3] 🧘 launching 超时，已回退。"
            )
        )
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "Jun 17 00:02:45 pve python[44241]: [xuruode3] 🧘 launching 超时，改用状态查询校准。"
            )
        )
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "Jun 17 00:16:54 pve python[44241]: [xueuode5] ⚠️ 共历心劫抉择无回合推进，已停止旧 prompt；按长冷却等待 12:09:31。"
            )
        )
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "Jul 02 02:02:55 pve python[235521]: [WalterWA2000] 🧯 指令 .卜筮问天 超时无响应，交由模块状态机继续。"
            )
        )
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "Jul 02 03:33:42 pve python[266008]: [xuruode6] 🧘 深度闭关 续轮指令超时无确认，改用状态查询校准。"
            )
        )

    def test_journal_since_is_not_before_current_service_start(self):
        start_epoch = health_observer.parse_local_ts("2026-06-06 14:38:55")
        with patch.object(health_observer.time, "time", return_value=health_observer.parse_local_ts("2026-06-06 14:42:18")):
            self.assertEqual(
                "2026-06-06 14:38:55",
                health_observer.journal_since_text(600, service_start_epoch=start_epoch),
            )

    def test_watchdog_journal_filter_uses_reset_marker_for_watchdog_only(self):
        service_start = health_observer.parse_local_ts("2026-07-02 01:30:00")
        reset_at = health_observer.parse_local_ts("2026-07-02 01:35:00")

        self.assertEqual(
            reset_at,
            health_observer.journal_filter_start_epoch(
                "xiuxian-safety-watchdog.service",
                service_start_epoch=service_start,
                watchdog_reset_epoch=reset_at,
            ),
        )
        self.assertEqual(
            service_start,
            health_observer.journal_filter_start_epoch(
                "xiuxian.service",
                service_start_epoch=service_start,
                watchdog_reset_epoch=reset_at,
            ),
        )

    def test_read_safety_reset_epoch_from_marker(self):
        reset_at = health_observer.parse_local_ts("2026-07-02 01:35:00")
        with tempfile.TemporaryDirectory() as tmp_dir:
            marker = Path(tmp_dir) / "safety_watchdog_reset.json"
            marker.write_text(json.dumps({"reset_at_epoch": reset_at}), encoding="utf-8")
            with patch.dict(os.environ, {"XIUXIAN_STATE_DIR": tmp_dir}, clear=True):
                self.assertEqual(reset_at, health_observer.read_safety_reset_epoch(Path("/repo")))

    def test_parse_systemd_start_timestamp(self):
        self.assertEqual(
            health_observer.parse_local_ts("2026-06-06 14:38:55"),
            health_observer.parse_systemd_start_timestamp("Sat 2026-06-06 14:38:55 CST"),
        )
        self.assertEqual(0.0, health_observer.parse_systemd_start_timestamp(""))

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
        self.assertFalse(
            health_observer.is_hard_journal_line(
                "Jun 17 00:21:31 pve python[57808]: answerCallbackQuery failed: HTTP 400: {\"ok\":false,\"error_code\":400,\"description\":\"Bad Request: query is too old and response timeout expired or query ID is invalid\"}"
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

    def test_listener_heartbeat_path_follows_xiuxian_environment(self):
        with patch.dict(os.environ, {"XIUXIAN_STATE_DIR": "/var/state"}, clear=True):
            self.assertEqual(
                Path("/var/state/listener_heartbeat.json"),
                health_observer.listener_heartbeat_path(Path("/repo")),
            )
        with patch.dict(os.environ, {"XIUXIAN_DATA_DIR": "/srv/xiuxian-data"}, clear=True):
            self.assertEqual(
                Path("/srv/xiuxian-data/state/listener_heartbeat.json"),
                health_observer.listener_heartbeat_path(Path("/repo")),
            )

    def test_health_payload_flags_stale_listener_heartbeat(self):
        cfg = health_observer.ObserverConfig(
            project_root=Path("/opt/xiuxian-main"),
            services=("xiuxian.service", "xiuxian-listener.service"),
            interval_sec=60,
            journal_window_sec=600,
            max_journal_matches=12,
            max_event_lines=100,
            state_dir=Path(tempfile.mkdtemp()),
            business_window_sec=1800,
        )
        snapshot = {
            "ts": "2026-07-02 01:30:00",
            "status": "ok",
            "services": {
                "xiuxian.service": {"ActiveState": "active", "SubState": "running"},
                "xiuxian-listener.service": {"ActiveState": "active", "SubState": "running"},
            },
            "listener": {"available": True, "status": "running", "age_sec": 240, "path": "/tmp/listener_heartbeat.json"},
            "safety": {"fused": False},
            "journals": [],
            "business": {"message_state": {}, "db_state": {}},
            "foreign_xiuxian_processes": [],
        }

        payload = health_observer.build_health_payload(snapshot, cfg)

        self.assertTrue(any(item["code"] == "listener_heartbeat_stale" for item in payload["risk_reasons"]))

    def test_business_message_analysis_flags_repeated_active_status_queries(self):
        now = 1_780_500_000.0
        events = [
            {"event_type": "sent", "_epoch": now - 500, "message_id": 101, "sender_id": 1, "text": ".查看闭关"},
            {"event_type": "sent", "_epoch": now - 300, "message_id": 102, "sender_id": 1, "text": ".查看闭关"},
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertEqual(2, result["active_status_counts"][".查看闭关"])
        self.assertEqual(2, result["active_status_identity_counts"]["1:.查看闭关"])
        self.assertEqual(".查看闭关", result["repeated_command_samples"][0]["command"])
        self.assertTrue(any("active status query repeated" in item["message"] for item in result["alerts"]))

    def test_business_message_analysis_ignores_repeated_active_status_before_safety_reset(self):
        now = 1_780_500_000.0
        events = [
            {"event_type": "sent", "_epoch": now - 500, "message_id": 101, "sender_id": 1, "text": ".查看闭关"},
            {"event_type": "sent", "_epoch": now - 300, "message_id": 102, "sender_id": 1, "text": ".查看闭关"},
        ]

        result = health_observer.analyze_message_events(events, now, 1800, reset_after_epoch=now - 100)

        self.assertEqual({}, result["active_status_counts"])
        self.assertFalse(any("active status query repeated" in item["message"] for item in result["alerts"]))

    def test_business_message_analysis_allows_active_status_queries_from_different_identities(self):
        now = 1_780_500_000.0
        events = [
            {"event_type": "sent", "_epoch": now - 500, "message_id": 101, "sender_id": 1, "text": ".查看闭关"},
            {"event_type": "sent", "_epoch": now - 300, "message_id": 102, "sender_id": 2, "text": ".查看闭关"},
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertEqual(2, result["active_status_counts"][".查看闭关"])
        self.assertEqual(1, result["active_status_identity_counts"]["1:.查看闭关"])
        self.assertEqual(1, result["active_status_identity_counts"]["2:.查看闭关"])
        self.assertFalse(any("active status query repeated" in item["message"] for item in result["alerts"]))

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

    def test_business_message_analysis_allows_marked_divination_query_chain(self):
        now = 1_780_500_000.0
        sender_id = 3777092103
        events = []
        for index in range(4):
            events.append({
                "event_type": "sent",
                "_epoch": now - 600 + index * 90,
                "message_id": 300 + index,
                "sender_id": sender_id,
                "text": ".卜筮问天",
                "family": "divination",
                "source_module": "卜筮问天",
                "op_id": f"divination_query:{sender_id}:2026-06-17:{index + 1}:try{index + 1}",
                "chain_id": f"divination:{sender_id}:2026-06-17",
            })

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertFalse(any("guarded command repeated" in item["message"] for item in result["alerts"]))

    def test_business_message_analysis_flags_unmarked_divination_repeats(self):
        now = 1_780_500_000.0
        sender_id = 3777092103
        events = [
            {
                "event_type": "sent",
                "_epoch": now - 600 + index * 90,
                "message_id": 400 + index,
                "sender_id": sender_id,
                "text": ".卜筮问天",
            }
            for index in range(4)
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertTrue(any("guarded command repeated" in item["message"] for item in result["alerts"]))

    def test_business_message_analysis_allows_one_small_world_refresh_round(self):
        now = 1_780_500_000.0
        sender_id = 301299112
        events = [
            {
                "event_type": "sent",
                "_epoch": now - 1200 + index * 120,
                "message_id": 500 + index,
                "sender_id": sender_id,
                "text": ".小世界",
                "family": "small_world_query",
                "source_module": "小世界",
            }
            for index in range(9)
        ]

        result = health_observer.analyze_message_events(events, now, 3600)

        self.assertFalse(any("guarded command repeated" in item["message"] for item in result["alerts"]))

    def test_business_message_analysis_flags_small_world_beyond_one_refresh_round(self):
        now = 1_780_500_000.0
        sender_id = 301299112
        events = [
            {
                "event_type": "sent",
                "_epoch": now - 1800 + index * 120,
                "message_id": 600 + index,
                "sender_id": sender_id,
                "text": ".小世界",
                "family": "small_world_query",
                "source_module": "小世界",
            }
            for index in range(10)
        ]

        result = health_observer.analyze_message_events(events, now, 3600)

        self.assertTrue(any("guarded command repeated" in item["message"] for item in result["alerts"]))

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

    def test_business_db_state_counts_module_pending_without_task_queue(self):
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
                        timeout REAL NOT NULL,
                        retry INTEGER NOT NULL DEFAULT 0,
                        max_retry INTEGER NOT NULL DEFAULT 3,
                        source_module TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE identities(
                        send_as_id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL DEFAULT '',
                        label TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE identity_module_state(
                        send_as_id INTEGER PRIMARY KEY,
                        wild_training_enabled INTEGER NOT NULL DEFAULT 0,
                        deep_retreat_enabled INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE identity_timers(
                        send_as_id INTEGER PRIMARY KEY,
                        next_wild_training_time REAL NOT NULL DEFAULT 0,
                        next_concubine_time REAL NOT NULL DEFAULT 0,
                        next_deep_retreat_time REAL NOT NULL DEFAULT 0,
                        next_yuanying_time REAL NOT NULL DEFAULT 0
                    );
                    CREATE TABLE identity_runtime_state(
                        send_as_id INTEGER PRIMARY KEY,
                        wild_training_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                        wild_training_reply_due_at REAL NOT NULL DEFAULT 0,
                        wild_training_last_result TEXT NOT NULL DEFAULT '',
                        wild_training_last_error TEXT NOT NULL DEFAULT '',
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
                for identity_id in range(1, 131):
                    conn.execute("INSERT INTO identities(send_as_id, username, label) VALUES(?, ?, '')", (identity_id, f"active{identity_id}"))
                    conn.execute("INSERT INTO identity_module_state(send_as_id, deep_retreat_enabled) VALUES(?, 1)", (identity_id,))
                    conn.execute("INSERT INTO identity_timers(send_as_id, next_deep_retreat_time) VALUES(?, ?)", (identity_id, now + 3600))
                    conn.execute(
                        "INSERT INTO identity_runtime_state(send_as_id, deep_retreat_phase) VALUES(?, 'running')",
                        (identity_id,),
                    )
                conn.execute("INSERT INTO identities(send_as_id, username, label) VALUES(1000, 'tester', '测试号')")
                conn.execute("INSERT INTO identity_module_state(send_as_id, wild_training_enabled) VALUES(1000, 1)")
                conn.execute("INSERT INTO identity_timers(send_as_id, next_wild_training_time) VALUES(1000, ?)", (now - 60,))
                conn.execute(
                    """
                    INSERT INTO identity_runtime_state(
                        send_as_id,
                        wild_training_reply_to_msg_id,
                        wild_training_reply_due_at,
                        wild_training_last_result
                    ) VALUES(1000, 99, ?, '已出发：深入')
                    """,
                    (now + 300,),
                )
                conn.commit()

            result = health_observer.read_db_business_state(db_path, now)

        self.assertEqual(0, result["pending_total"])
        self.assertEqual(1, result["module_pending_total"])
        self.assertEqual("wild_training", result["module_pending_samples"][0]["module"])
        self.assertEqual(99, result["module_pending_samples"][0]["pending"][0]["msg_id"])
        self.assertFalse(any(item["identity_id"] == 1000 for item in result["module_summary"]))

    def test_module_summary_ignores_stale_due_without_pending_anchor(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, concubine_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_concubine_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    concubine_phase TEXT NOT NULL DEFAULT 'idle',
                    concubine_heart_due_at REAL NOT NULL DEFAULT 0,
                    concubine_heart_msg_id INTEGER NOT NULL DEFAULT 0,
                    concubine_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, concubine_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_concubine_time) VALUES(42, 0)")
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, concubine_heart_due_at, concubine_heart_msg_id) VALUES(42, ?, 0)",
                (now - 3 * 86400,),
            )

            summary = health_observer.build_module_summary(conn, now)

        concubine = next(item for item in summary if item["module"] == "concubine")
        self.assertEqual("ok", concubine["status"])
        self.assertTrue(concubine["due"][0]["stale_without_pending"])

    def test_module_summary_ignores_disabled_module_stale_last_error(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, fishing_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_fishing_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    fishing_phase TEXT NOT NULL DEFAULT 'idle',
                    fishing_last_result TEXT NOT NULL DEFAULT '',
                    fishing_last_error TEXT NOT NULL DEFAULT '',
                    fishing_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    fishing_status_msg_id INTEGER NOT NULL DEFAULT 0,
                    fishing_reply_due_at REAL NOT NULL DEFAULT 0,
                    fishing_transfer_due_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'fisher')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, fishing_enabled) VALUES(42, 0)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_fishing_time) VALUES(42, 0)")
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, fishing_last_result, fishing_last_error) VALUES(42, '已发送：.收竿', '回复超时：11305511')"
            )

            summary = health_observer.build_module_summary(conn, now)

        self.assertFalse(any(item["module"] == "fishing" for item in summary))

    def test_module_summary_ignores_resolved_concubine_puzzle_send_error(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, concubine_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_concubine_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    concubine_phase TEXT NOT NULL DEFAULT 'idle',
                    concubine_last_error TEXT NOT NULL DEFAULT '',
                    concubine_last_result TEXT NOT NULL DEFAULT '',
                    concubine_fragment_xutian_count INTEGER NOT NULL DEFAULT 0,
                    concubine_fragment_xutian_total INTEGER NOT NULL DEFAULT 4,
                    concubine_fragment_cangkun_count INTEGER NOT NULL DEFAULT 0,
                    concubine_fragment_cangkun_total INTEGER NOT NULL DEFAULT 4
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'resolved')")
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(43, 'ready')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, concubine_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_module_state(send_as_id, concubine_enabled) VALUES(43, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_concubine_time) VALUES(42, ?)", (now + 600,))
            conn.execute("INSERT INTO identity_timers(send_as_id, next_concubine_time) VALUES(43, ?)", (now + 600,))
            conn.execute(
                """
                INSERT INTO identity_runtime_state(
                    send_as_id, concubine_last_error,
                    concubine_fragment_xutian_count, concubine_fragment_cangkun_count
                ) VALUES(42, '发送 .拼图 失败', 3, 3)
                """
            )
            conn.execute(
                """
                INSERT INTO identity_runtime_state(
                    send_as_id, concubine_last_error,
                    concubine_fragment_xutian_count, concubine_fragment_cangkun_count
                ) VALUES(43, '发送 .拼图 失败', 3, 4)
                """
            )

            summary = health_observer.build_module_summary(conn, now)

        resolved = next(item for item in summary if item["identity_id"] == 42)
        ready = next(item for item in summary if item["identity_id"] == 43)
        self.assertEqual("ok", resolved["status"])
        self.assertFalse(any("发送 .拼图 失败" in detail for detail in resolved["details"]))
        self.assertEqual("warn", ready["status"])
        self.assertTrue(any("发送 .拼图 失败" in detail for detail in ready["details"]))

    def test_module_summary_ignores_stale_heart_due_during_unrelated_concubine_phase(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, concubine_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_concubine_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    concubine_phase TEXT NOT NULL DEFAULT 'idle',
                    concubine_heart_due_at REAL NOT NULL DEFAULT 0,
                    concubine_heart_msg_id INTEGER NOT NULL DEFAULT 0,
                    concubine_heart_prompt_msg_id INTEGER NOT NULL DEFAULT 0,
                    concubine_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, concubine_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_concubine_time) VALUES(42, ?)", (now + 600,))
            conn.execute(
                """
                INSERT INTO identity_runtime_state(send_as_id, concubine_phase, concubine_heart_due_at, concubine_heart_msg_id, concubine_heart_prompt_msg_id)
                VALUES(42, 'dream_pending', ?, 0, 0)
                """,
                (now - 600,),
            )

            summary = health_observer.build_module_summary(conn, now)

        concubine = next(item for item in summary if item["module"] == "concubine")
        self.assertEqual("active", concubine["status"])
        self.assertTrue(concubine["due"][0]["stale_without_pending"])

    def test_module_summary_flags_overdue_due_with_pending_anchor(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, concubine_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_concubine_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    concubine_phase TEXT NOT NULL DEFAULT 'idle',
                    concubine_heart_due_at REAL NOT NULL DEFAULT 0,
                    concubine_heart_msg_id INTEGER NOT NULL DEFAULT 0,
                    concubine_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, concubine_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_concubine_time) VALUES(42, 0)")
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, concubine_heart_due_at, concubine_heart_msg_id) VALUES(42, ?, 99)",
                (now - 600,),
            )

            summary = health_observer.build_module_summary(conn, now)

        concubine = next(item for item in summary if item["module"] == "concubine")
        self.assertEqual("error", concubine["status"])
        self.assertEqual(99, concubine["pending"][0]["msg_id"])

    def test_module_summary_flags_overdue_heart_due_with_prompt_anchor(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, concubine_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_concubine_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    concubine_phase TEXT NOT NULL DEFAULT 'idle',
                    concubine_heart_due_at REAL NOT NULL DEFAULT 0,
                    concubine_heart_msg_id INTEGER NOT NULL DEFAULT 0,
                    concubine_heart_prompt_msg_id INTEGER NOT NULL DEFAULT 0,
                    concubine_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, concubine_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_concubine_time) VALUES(42, 0)")
            conn.execute(
                """
                INSERT INTO identity_runtime_state(send_as_id, concubine_phase, concubine_heart_due_at, concubine_heart_msg_id, concubine_heart_prompt_msg_id)
                VALUES(42, 'heart_choice_pending', ?, 0, 99)
                """,
                (now - 600,),
            )

            summary = health_observer.build_module_summary(conn, now)

        concubine = next(item for item in summary if item["module"] == "concubine")
        self.assertEqual("error", concubine["status"])
        self.assertFalse(concubine["due"][0]["stale_without_pending"])

    def test_module_summary_treats_retrying_auto_timeout_as_warn(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, hehuan_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    hehuan_observation TEXT NOT NULL DEFAULT '{}',
                    concubine_partner_kind TEXT NOT NULL DEFAULT '',
                    concubine_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    concubine_reply_due_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, hehuan_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id) VALUES(42)")
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, hehuan_observation) VALUES(42, ?)",
                (json.dumps({
                    "last_action": "双修 温养",
                    "last_result": "success",
                    "last_summary": "温养双修成功",
                    "auto_last_error": "温养回复超时或被吞",
                    "auto_retry_count": 1,
                }, ensure_ascii=False),),
            )

            summary = health_observer.build_module_summary(conn, now)

        hehuan = next(item for item in summary if item["module"] == "hehuan")
        self.assertEqual("warn", hehuan["status"])

    def test_module_summary_treats_scheduled_auto_send_failure_as_warn(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, hehuan_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    hehuan_observation TEXT NOT NULL DEFAULT '{}',
                    concubine_partner_kind TEXT NOT NULL DEFAULT '',
                    concubine_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    concubine_reply_due_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, hehuan_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id) VALUES(42)")
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, hehuan_observation) VALUES(42, ?)",
                (json.dumps({
                    "last_observed_at": now - 60,
                    "last_action": "双修 温养",
                    "last_result": "success",
                    "last_summary": "温养双修成功",
                    "auto_last_error": "10分钟内没有吧唧发言，锚点发送失败或被安全策略拦截",
                    "auto_last_error_at": now - 10,
                    "auto_next_time": now + 300,
                }, ensure_ascii=False),),
            )

            summary = health_observer.build_module_summary(conn, now)

        hehuan = next(item for item in summary if item["module"] == "hehuan")
        self.assertEqual("warn", hehuan["status"])

    def test_module_summary_treats_scheduled_last_error_send_failure_as_warn(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, wild_training_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_wild_training_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    wild_training_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    wild_training_reply_due_at REAL NOT NULL DEFAULT 0,
                    wild_training_last_result TEXT NOT NULL DEFAULT '',
                    wild_training_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, wild_training_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_wild_training_time) VALUES(42, ?)", (now + 300,))
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, wild_training_last_result, wild_training_last_error) VALUES(42, ?, ?)",
                ("天星先炼制消费推命：send_blocked_waiting", ".炼制 玄铁剑 发送失败或被安全策略拦截。"),
            )

            summary = health_observer.build_module_summary(conn, now)

        wild = next(item for item in summary if item["module"] == "wild_training")
        self.assertEqual("warn", wild["status"])

    def test_module_summary_treats_scheduled_concubine_tianji_send_failure_as_warn(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, concubine_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_concubine_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    concubine_phase TEXT NOT NULL DEFAULT 'idle',
                    concubine_tianji_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, concubine_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_concubine_time) VALUES(42, ?)", (now + 300,))
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, concubine_tianji_last_error) VALUES(42, ?)",
                ("发送 .天机代卜 失败",),
            )

            summary = health_observer.build_module_summary(conn, now)

        concubine = next(item for item in summary if item["module"] == "concubine")
        self.assertEqual("warn", concubine["status"])

    def test_module_summary_flags_overdue_next_time_without_pending_anchor(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, wild_training_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_wild_training_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    wild_training_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    wild_training_reply_due_at REAL NOT NULL DEFAULT 0,
                    wild_training_last_result TEXT NOT NULL DEFAULT '',
                    wild_training_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, wild_training_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_wild_training_time) VALUES(42, ?)", (now - 700,))
            conn.execute("INSERT INTO identity_runtime_state(send_as_id) VALUES(42)")

            summary = health_observer.build_module_summary(conn, now)

        wild = next(item for item in summary if item["module"] == "wild_training")
        self.assertEqual("error", wild["status"])
        self.assertTrue(wild["next"][0]["lag_without_anchor"])
        self.assertTrue(any("调度滞后" in detail for detail in wild["details"]))

    def test_module_summary_ignores_disabled_identity(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, wild_training_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_wild_training_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    wild_training_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    wild_training_reply_due_at REAL NOT NULL DEFAULT 0,
                    wild_training_last_result TEXT NOT NULL DEFAULT '',
                    wild_training_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username, enabled) VALUES(42, 'paused', 0)")
            conn.execute("INSERT INTO identity_module_state(send_as_id, wild_training_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_wild_training_time) VALUES(42, ?)", (now - 700,))
            conn.execute("INSERT INTO identity_runtime_state(send_as_id, wild_training_last_result) VALUES(42, '修为+2544')")

            summary = health_observer.build_module_summary(conn, now)

        self.assertFalse(any(item["identity_id"] == 42 for item in summary))

    def test_module_summary_does_not_flag_overdue_next_time_with_pending_anchor(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, wild_training_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_wild_training_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    wild_training_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    wild_training_reply_due_at REAL NOT NULL DEFAULT 0,
                    wild_training_last_result TEXT NOT NULL DEFAULT '',
                    wild_training_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, wild_training_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_wild_training_time) VALUES(42, ?)", (now - 700,))
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, wild_training_reply_to_msg_id, wild_training_reply_due_at) VALUES(42, 99, ?)",
                (now + 60,),
            )

            summary = health_observer.build_module_summary(conn, now)

        wild = next(item for item in summary if item["module"] == "wild_training")
        self.assertEqual("active", wild["status"])
        self.assertFalse(wild["next"][0]["lag_without_anchor"])

    def test_module_summary_ignores_legacy_hehuan_auto_error_after_success(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, hehuan_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    hehuan_observation TEXT NOT NULL DEFAULT '{}',
                    concubine_partner_kind TEXT NOT NULL DEFAULT '',
                    concubine_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    concubine_reply_due_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, hehuan_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id) VALUES(42)")
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, hehuan_observation) VALUES(42, ?)",
                (json.dumps({
                    "last_observed_at": now - 30,
                    "last_action": "双修 温养",
                    "last_result": "success",
                    "last_summary": "温养双修成功",
                    "auto_last_error": "10分钟内没有吧唧发言，锚点发送失败或被安全策略拦截",
                    "auto_next_time": now + 300,
                }, ensure_ascii=False),),
            )

            summary = health_observer.build_module_summary(conn, now)

        hehuan = next(item for item in summary if item["module"] == "hehuan")
        self.assertEqual("ok", hehuan["status"])
        self.assertFalse(any(item.startswith("自动错误:") for item in hehuan["details"]))

    def test_module_summary_treats_tianxing_existing_prediction_cooldown_as_state(self):
        now = 1_780_500_000.0
        observation = json.dumps({
            "last_action": "推命",
            "last_result": "cooldown",
            "last_summary": "推命尚未应验 探索",
            "last_error": "推命尚未应验",
            "fixed_star": "贪狼",
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "tianji_value": 38,
            "auto_last_error": "天星宗自动动作回复超时，暂缓重试；不继续推进下游。",
            "auto_pending_action": "",
        }, ensure_ascii=False)
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, tianxing_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    tianxing_observation TEXT NOT NULL DEFAULT '{}',
                    tianxing_timeline_state TEXT NOT NULL DEFAULT '{}',
                    tianxing_auto_config TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username, label) VALUES(42, 'tutuerduoxiao', '小耳朵图图')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, tianxing_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id) VALUES(42)")
            conn.execute("INSERT INTO identity_runtime_state(send_as_id, tianxing_observation) VALUES(42, ?)", (observation,))

            summary = health_observer.build_module_summary(conn, now)

        tianxing = next(item for item in summary if item["module"] == "tianxing")
        self.assertEqual("ok", tianxing["status"])
        self.assertTrue(any("推命:探索" in item for item in tianxing["details"]))
        self.assertFalse(any(item.startswith("错误:") or item.startswith("自动错误:") for item in tianxing["details"]))

    def test_module_summary_treats_tianxing_daily_limit_and_replan_as_state(self):
        now = 1_780_500_000.0
        observation = json.dumps({
            "last_observed_at": now - 30,
            "last_action": "天机盘",
            "last_result": "panel",
            "fixed_star": "太阴",
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "tianji_value": 30,
            "auto_last_action": "craft_farm",
            "auto_last_error": "炼制攒点今日已达 42 轮。",
            "auto_next_time": now + 6 * 3600,
        }, ensure_ascii=False)
        timeline = json.dumps({
            "phase": "blocked_replan",
            "route": "探索",
            "last_error": "天机盘校准回复超时，回到时间线重算；不连续查盘。",
            "blocked_until": now - 60,
            "active_step": {},
        }, ensure_ascii=False)
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, tianxing_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    tianxing_observation TEXT NOT NULL DEFAULT '{}',
                    tianxing_timeline_state TEXT NOT NULL DEFAULT '{}',
                    tianxing_auto_config TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tutuerduoxiao')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, tianxing_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id) VALUES(42)")
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, tianxing_observation, tianxing_timeline_state) VALUES(42, ?, ?)",
                (observation, timeline),
            )

            summary = health_observer.build_module_summary(conn, now)

        tianxing = next(item for item in summary if item["module"] == "tianxing")
        self.assertEqual("ok", tianxing["status"])

    def test_module_summary_ignores_tianxing_send_failure_when_prediction_is_stable(self):
        now = 1_780_500_000.0
        observation = json.dumps({
            "last_observed_at": now - 30,
            "last_action": "推命",
            "last_result": "cooldown",
            "last_summary": "推命尚未应验 探索",
            "last_error": "",
            "fixed_star": "贪狼",
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "tianji_value": 38,
            "auto_last_error": "天星宗自动命令发送失败或被安全策略拦截",
            "auto_pending_action": "",
            "auto_pending_msg_id": 0,
        }, ensure_ascii=False)
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, tianxing_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    tianxing_observation TEXT NOT NULL DEFAULT '{}',
                    tianxing_timeline_state TEXT NOT NULL DEFAULT '{}',
                    tianxing_auto_config TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username, label) VALUES(42, 'tutuerduoxiao', '小耳朵图图')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, tianxing_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id) VALUES(42)")
            conn.execute("INSERT INTO identity_runtime_state(send_as_id, tianxing_observation) VALUES(42, ?)", (observation,))

            summary = health_observer.build_module_summary(conn, now)

        tianxing = next(item for item in summary if item["module"] == "tianxing")
        self.assertEqual("ok", tianxing["status"])
        self.assertFalse(any(item.startswith("自动错误:") for item in tianxing["details"]))

    def test_module_summary_marks_stale_tianxing_fixed_star(self):
        now = health_observer.parse_local_ts("2026-07-02 02:00:00")
        observation = json.dumps({
            "last_observed_at": now - 30,
            "last_action": "观命",
            "last_result": "success",
            "available_stars": ["贪狼", "天府", "紫微"],
            "available_stars_day": "2026-07-02",
            "fixed_star": "贪狼",
            "fixed_star_day": "2026-07-01",
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "tianji_value": 38,
        }, ensure_ascii=False)
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, tianxing_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    tianxing_observation TEXT NOT NULL DEFAULT '{}',
                    tianxing_timeline_state TEXT NOT NULL DEFAULT '{}',
                    tianxing_auto_config TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username, label) VALUES(42, 'tutuerduoxiao', '小耳朵图图')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, tianxing_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id) VALUES(42)")
            conn.execute("INSERT INTO identity_runtime_state(send_as_id, tianxing_observation) VALUES(42, ?)", (observation,))

            summary = health_observer.build_module_summary(conn, now)

        tianxing = next(item for item in summary if item["module"] == "tianxing")
        self.assertFalse(any(item == "定命:贪狼" for item in tianxing["details"]))
        self.assertTrue(any(item == "旧定命:贪狼" for item in tianxing["details"]))

    def test_health_payload_and_markdown_include_score_risks_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = health_observer.ObserverConfig(
                project_root=Path(tmp_dir),
                services=("xiuxian.service",),
                interval_sec=60,
                journal_window_sec=600,
                max_journal_matches=5,
                max_event_lines=100,
                state_dir=Path(tmp_dir) / "health",
                business_window_sec=900,
            )
            snapshot = {
                "ts": "2026-06-29 12:00:00",
                "status": "ok",
                "services": {"xiuxian.service": {"ActiveState": "active", "SubState": "running"}},
                "safety": {"fused": True, "path": "/tmp/safety_watchdog_fused.json", "reason": "same command repeat"},
                "journals": [],
                "business": {
                    "message_log": "/tmp/messages.log",
                    "message_state": {"window_sec": 900, "sent_count": 3, "last_sent_ts": "2026-06-29 11:59:59"},
                    "db_state": {
                        "available": True,
                        "db_path": "/tmp/state.db",
                        "pending_total": 0,
                        "module_pending_total": 1,
                        "module_pending_samples": [
                            {
                                "identity_id": 42,
                                "username": "tester",
                                "module": "wild_training",
                                "module_label": "野外历练",
                                "pending": [{"label": "回复", "msg_id": 99}],
                            }
                        ],
                        "module_summary": [],
                    },
                    "alerts": [],
                },
            }

            snapshot["health"] = health_observer.build_health_payload(snapshot, cfg)
            snapshot["evidence_refs"] = health_observer.build_evidence_refs(snapshot)
            markdown = health_observer.format_audit_pack_markdown(snapshot)

        self.assertLess(snapshot["health"]["score"], 100)
        self.assertTrue(any(item["code"] == "safety_watchdog_fused" for item in snapshot["health"]["risk_reasons"]))
        self.assertTrue(any(item["kind"] == "safety_watchdog_fused" for item in snapshot["evidence_refs"]))
        self.assertIn("Xiuxian Health Audit Pack", markdown)
        self.assertIn("score:", markdown)
        self.assertIn("pending: tasks=0 module=1", markdown)
        self.assertIn("pending tester 野外历练: 回复 msg=99", markdown)

    def test_merge_status_promotes_business_warnings_from_ok_only(self):
        status, reasons = health_observer.merge_status(
            "ok",
            [health_observer.business_alert("active status query repeated")],
        )

        self.assertEqual("warn", status)
        self.assertIn("business warnings: 1", reasons)


if __name__ == "__main__":
    unittest.main()
