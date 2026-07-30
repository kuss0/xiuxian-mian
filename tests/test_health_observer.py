import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tools import health_observer


class HealthObserverTests(unittest.TestCase):
    def test_miniapp_capture_health_reports_recent_proof_failure_as_error(self):
        now = 1_780_500_000.0
        result = health_observer.analyze_miniapp_capture_health(
            [
                {
                    "created_at": now - 60,
                    "adapter_key": "fishing",
                    "source": "cave_public_fishing:301299112",
                    "step_key": "finish",
                    "status_code": 400,
                    "ok": False,
                    "error_type": "app",
                    "error": "fishing_proof_invalid",
                }
            ],
            now,
        )

        self.assertEqual(1, result["critical_count"])
        self.assertEqual("error", result["alerts"][0]["severity"])
        self.assertEqual("fishing", result["alerts"][0]["sample"][0]["adapter_key"])

    def test_miniapp_capture_health_ignores_expected_terminal_and_old_failure(self):
        now = 1_780_500_000.0
        result = health_observer.analyze_miniapp_capture_health(
            [
                {
                    "created_at": now - 60,
                    "adapter_key": "fishing",
                    "ok": False,
                    "error_type": "app",
                    "error": "fishing_daily_limit_reached",
                },
                {
                    "created_at": now - health_observer.MINIAPP_CAPTURE_HEALTH_WINDOW_SEC - 1,
                    "adapter_key": "fishing",
                    "ok": False,
                    "error_type": "app",
                    "error": "fishing_proof_invalid",
                },
            ],
            now,
        )

        self.assertEqual(1, result["expected_terminal_count"])
        self.assertEqual(0, result["critical_count"])
        self.assertEqual([], result["alerts"])

    def test_miniapp_capture_health_warns_for_recent_transient_failure(self):
        now = 1_780_500_000.0
        result = health_observer.analyze_miniapp_capture_health(
            [
                {
                    "created_at": now - 30,
                    "adapter_key": "cave_treasure",
                    "source": "cave_public_treasure:301299112",
                    "step_key": "dwelling_start",
                    "status_code": 502,
                    "ok": False,
                    "error_type": "transient",
                    "error": "HTTP 502 returned non JSON",
                }
            ],
            now,
        )

        self.assertEqual(1, result["warning_count"])
        self.assertEqual("warn", result["alerts"][0]["severity"])

    def test_miniapp_capture_health_ignores_recovered_transient_failure(self):
        now = 1_780_500_000.0
        source = "cave_public_treasure:301299112"
        result = health_observer.analyze_miniapp_capture_health(
            [
                {
                    "created_at": now - 30,
                    "adapter_key": "cave_treasure",
                    "source": source,
                    "step_key": "dwelling_start",
                    "status_code": 502,
                    "ok": False,
                    "error_type": "transient",
                    "error": "HTTP 502 returned non JSON",
                },
                {
                    "created_at": now - 20,
                    "adapter_key": "cave_treasure",
                    "source": source,
                    "step_key": "dwelling_start",
                    "status_code": 200,
                    "ok": True,
                },
            ],
            now,
        )

        self.assertEqual(1, result["recovered_transient_count"])
        self.assertEqual(0, result["warning_count"])
        self.assertEqual([], result["alerts"])

    def test_read_recent_miniapp_capture_events_uses_sanitized_http_rows_only(self):
        now = 1_780_500_000.0
        day_key = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as tmp_dir:
            capture_dir = Path(tmp_dir) / "data" / "state" / "miniapp_capture"
            capture_dir.mkdir(parents=True)
            path = capture_dir / f"fishing-{day_key}.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "created_at": now - 30,
                                "adapter_key": "fishing",
                                "step_key": "finish",
                                "url_path": "/api/miniapp/xianxia-fishing/finish",
                                "ok": False,
                                "error": "fishing_proof_invalid",
                            }
                        ),
                        json.dumps(
                            {
                                "created_at": now - 20,
                                "adapter_key": "fishing",
                                "step_key": "business:fishing",
                                "ok": True,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rows = health_observer.read_recent_miniapp_capture_events(Path(tmp_dir), now)

        self.assertEqual(1, len(rows))
        self.assertEqual("finish", rows[0]["step_key"])

    def test_world_boss_health_reports_recent_minimum_duration_failure_as_error(self):
        now = 1_780_500_000.0
        result = health_observer.analyze_world_boss_miniapp_health(
            {
                "event_key": "2026-07-30:test",
                "miniapp_auto_status": "partial",
                "miniapp_auto_finished_at": now - 60,
                "miniapp_auto_results": [
                    {
                        "identity_id": 301299112,
                        "phase": "battle",
                        "ok": False,
                        "status": "failed",
                        "error": "boss_duration_too_short",
                    }
                ],
            },
            now,
        )

        self.assertTrue(result["recent"])
        self.assertEqual(1, result["duration_failure_count"])
        self.assertEqual("error", result["alerts"][0]["severity"])
        self.assertEqual(301299112, result["alerts"][0]["sample"][0]["identity_id"])

    def test_world_boss_health_warns_for_recent_partial_or_failed_identity(self):
        now = 1_780_500_000.0
        result = health_observer.analyze_world_boss_miniapp_health(
            {
                "event_key": "2026-07-30:test",
                "miniapp_auto_status": "partial",
                "miniapp_auto_finished_at": now - 60,
                "miniapp_auto_results": [
                    {
                        "identity_id": 8659059191,
                        "phase": "battle",
                        "ok": False,
                        "status": "event_closed_partial",
                        "error": "world boss closed after partial contribution",
                    }
                ],
            },
            now,
        )

        self.assertTrue(result["recent"])
        self.assertEqual(1, result["partial_or_failed_count"])
        self.assertEqual("warn", result["alerts"][0]["severity"])
        self.assertIn("partial/failed identities", result["alerts"][0]["message"])

    def test_world_boss_health_ignores_historical_failure(self):
        now = 1_780_500_000.0
        result = health_observer.analyze_world_boss_miniapp_health(
            {
                "event_key": "2026-07-29:test",
                "miniapp_auto_status": "partial",
                "miniapp_auto_finished_at": now - health_observer.WORLD_BOSS_MINIAPP_HEALTH_WINDOW_SEC - 1,
                "miniapp_auto_results": [
                    {
                        "identity_id": 301299112,
                        "phase": "battle",
                        "ok": False,
                        "status": "failed",
                        "error": "boss_duration_too_short",
                    }
                ],
            },
            now,
        )

        self.assertFalse(result["recent"])
        self.assertEqual(1, result["duration_failure_count"])
        self.assertEqual([], result["alerts"])

    def test_world_boss_health_warns_for_recent_terminal_status_without_results(self):
        now = 1_780_500_000.0
        result = health_observer.analyze_world_boss_miniapp_health(
            {
                "event_key": "2026-07-30:test",
                "miniapp_auto_status": "runtime_error",
                "miniapp_auto_finished_at": now - 60,
                "miniapp_auto_results": [],
            },
            now,
        )

        self.assertTrue(result["recent"])
        self.assertEqual("warn", result["alerts"][0]["severity"])
        self.assertIn("runtime_error", result["alerts"][0]["message"])

    def test_warn_journal_ignores_benign_locked_wording(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "💡 定向红包已锁定给 @someone，份数自动调整为 1 份。"
            )
        )
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "本次梦兆锁定：【虚天残图】线路。"
            )
        )
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "🧧 红包候选观察｜type=message｜text=本次讨红包已过期。"
            )
        )

    def test_journal_ignores_alert_words_inside_passive_red_packet_observation(self):
        line = "🧧 红包候选观察｜type=message｜text=ERROR FUSED 风暴 超时"

        self.assertFalse(health_observer.is_hard_journal_line(line))
        self.assertFalse(health_observer.is_warn_journal_line(line))

    def test_warn_journal_keeps_explicit_lock_and_block_signals(self):
        self.assertTrue(health_observer.is_warn_journal_line("全局安全锁已触发"))
        self.assertTrue(health_observer.is_warn_journal_line("状态机锁死，等待人工处理"))
        self.assertTrue(health_observer.is_warn_journal_line("天星预检阻断斗法"))

    def test_read_journal_matches_limits_journalctl_input(self):
        with patch.object(health_observer, "run_command", return_value=(0, "recent line\n", "")) as run:
            result = health_observer.read_journal_matches(
                "xiuxian.service",
                600,
                12,
                max_lines=321,
            )

        command = run.call_args.args[0]
        self.assertIn("--lines=321", command)
        self.assertEqual(1, result["total_lines"])

    def test_append_event_does_not_rewrite_under_size_budget(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            path.write_text('{"old":true}\n', encoding="utf-8")
            with patch.object(Path, "read_text", side_effect=AssertionError("full history must not be read")):
                health_observer.append_event(path, {"new": True}, max_lines=100)

            self.assertEqual(
                '{"old":true}\n{"new":true}\n',
                path.read_text(encoding="utf-8"),
            )

    def test_append_event_compacts_by_streaming_a_bounded_tail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            path.write_bytes(b"".join(f'{{"n":{index}}}\n'.encode() for index in range(20)))
            with patch.object(health_observer, "EVENT_HISTORY_MAX_BYTES", 80):
                health_observer.append_event(path, {"n": 20}, max_lines=100)

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual('{"n":20}', lines[-1])
            self.assertLessEqual(path.stat().st_size, 80 + 16)
            self.assertGreaterEqual(len(lines), 2)

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

    def test_classify_snapshot_treats_listener_inactive_as_warning(self):
        status, reasons = health_observer.classify_snapshot(
            {
                "xiuxian.service": {"ActiveState": "active", "SubState": "running"},
                "xiuxian-listener.service": {"ActiveState": "inactive", "SubState": "dead"},
            },
            [{"hard_count": 0, "warn_count": 0}],
        )

        self.assertEqual("warn", status)
        self.assertIn("xiuxian-listener.service inactive: inactive/dead", reasons)

    def test_classify_snapshot_treats_disabled_watchdog_as_warning(self):
        status, reasons = health_observer.classify_snapshot(
            {
                "xiuxian.service": {"ActiveState": "active", "SubState": "running"},
                "xiuxian-safety-watchdog.service": {"ActiveState": "inactive", "SubState": "dead"},
            },
            [{"hard_count": 0, "warn_count": 0}],
        )

        self.assertEqual("warn", status)
        self.assertIn("xiuxian-safety-watchdog.service inactive: inactive/dead", reasons)

    def test_optional_inactive_listener_journal_is_skipped(self):
        self.assertTrue(
            health_observer.should_skip_optional_inactive_journal(
                "xiuxian-listener.service",
                {"xiuxian-listener.service": {"ActiveState": "inactive", "SubState": "dead"}},
            )
        )
        self.assertFalse(
            health_observer.should_skip_optional_inactive_journal(
                "xiuxian-listener.service",
                {"xiuxian-listener.service": {"ActiveState": "active", "SubState": "running"}},
            )
        )
        self.assertFalse(
            health_observer.should_skip_optional_inactive_journal(
                "xiuxian.service",
                {"xiuxian.service": {"ActiveState": "inactive", "SubState": "dead"}},
            )
        )

    def test_optional_inactive_watchdog_journal_is_skipped(self):
        self.assertTrue(
            health_observer.should_skip_optional_inactive_journal(
                "xiuxian-safety-watchdog.service",
                {"xiuxian-safety-watchdog.service": {"ActiveState": "inactive", "SubState": "dead"}},
            )
        )

    def test_should_print_snapshot_throttles_repeated_warning_signature(self):
        snapshot = {
            "status": "warn",
            "reasons": ["journal warn matches: 16"],
            "health": {
                "risk_reasons": [
                    {"code": "journal_warn", "message": "journal warn matches: 16", "severity": "warn"}
                ]
            },
        }
        signature = health_observer.snapshot_log_signature(snapshot)

        self.assertFalse(
            health_observer.should_print_snapshot(
                snapshot,
                last_signature=signature,
                last_print_at=1000.0,
                now=1000.0 + health_observer.WARN_PRINT_INTERVAL_SEC - 1,
            )
        )
        self.assertTrue(
            health_observer.should_print_snapshot(
                snapshot,
                last_signature=signature,
                last_print_at=1000.0,
                now=1000.0 + health_observer.WARN_PRINT_INTERVAL_SEC,
            )
        )

    def test_should_print_snapshot_reports_changed_signature_immediately(self):
        old_snapshot = {"status": "warn", "reasons": ["journal warn matches: 16"], "health": {"risk_reasons": []}}
        new_snapshot = {"status": "warn", "reasons": ["business warnings: 1"], "health": {"risk_reasons": []}}

        self.assertTrue(
            health_observer.should_print_snapshot(
                new_snapshot,
                last_signature=health_observer.snapshot_log_signature(old_snapshot),
                last_print_at=1000.0,
                now=1001.0,
            )
        )

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
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "口径：仅统计真实天道战报；目标CD、拒绝、超时和未发送不计。"
            )
        )
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

    def test_warn_line_ignores_safety_lock_short_backoff(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "[jfdffdddd] 🧯 安全锁拦截：.显灵｜小世界显灵 小世界显灵未发送，短退避重试，剩余约 1295s"
            )
        )

    def test_warn_line_ignores_expected_dungeon_join_miss(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "[2026-06-03 22:48:41] 🧩 自动副本：收到 @，但未找到同话题/同开门人/60s 内的副本公告。"
            )
        )

    def test_warn_line_ignores_expected_dungeon_quiet_deferral(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "[sanshaoyedejian1] 🤫 虚天殿静场令生效中，暂缓普通指令：.剥离咒源 @jfdffdddd｜恢复 2026-07-29 10:03:48 UTC+8"
            )
        )
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "[WalterWA2000] 📝 点卯未发送：dungeon_quiet，延后至 2026-07-29 10:17:50 UTC+8"
            )
        )
        self.assertTrue(
            health_observer.is_warn_journal_line(
                "[WalterWA2000] ⏳ 指令排队超时未发送：.宗门点卯 | >100s"
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

    def test_interdc_call_error_is_transient_warning_not_hard(self):
        line = (
            "Jul 25 01:45:13 pve python[2654567]: Telegram is having internal issues "
            "InterdcCallErrorError: An error occurred while communicating with DC 4 "
            "(caused by SendMessageRequest)"
        )

        self.assertFalse(health_observer.is_hard_journal_line(line))
        self.assertTrue(health_observer.is_warn_journal_line(line))

    def test_startup_get_dialogs_rpc_call_fail_is_not_health_alert(self):
        line = (
            "Jul 30 08:00:08 pve python[1126265]: WARNING:xiuxian.telethon.account_301299112.client.users:"
            "Telegram is having internal issues RpcCallFailError: Telegram is having internal issues, "
            "please try again later. (caused by GetDialogsRequest)"
        )

        self.assertFalse(health_observer.is_hard_journal_line(line))
        self.assertFalse(health_observer.is_warn_journal_line(line))

    def test_send_rpc_call_fail_error_is_transient_warning_not_hard(self):
        line = (
            "Jul 30 08:00:08 pve python[1126265]: Telegram is having internal issues "
            "RpcCallFailError: Telegram is having internal issues, please try again later. "
            "(caused by SendMessageRequest)"
        )

        self.assertFalse(health_observer.is_hard_journal_line(line))
        self.assertTrue(health_observer.is_warn_journal_line(line))

    def test_getting_difference_value_error_is_not_hard(self):
        line = (
            "Jul 04 23:21:56 pve python[1721578]: Getting difference for channel updates 1828482465 "
            "caused ValueError; ending getting difference prematurely until server issues are resolved"
        )

        self.assertFalse(health_observer.is_hard_journal_line(line))

    def test_log_bot_callback_connection_reset_is_not_hard(self):
        line = (
            "Jul 10 03:29:01 pve python[150823]: log bot callback poll failed: "
            "('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))"
        )

        self.assertFalse(health_observer.is_hard_journal_line(line))

    def test_log_bot_callback_transient_bot_api_errors_are_not_hard(self):
        samples = [
            'Jul 14 09:27:21 pve python[2122718]: log bot callback poll failed: HTTP 429: {"ok":false,"error_code":429,"description":"Too Many Requests: retry after 5","parameters":{"retry_after":5}}',
            'Jul 14 09:27:23 pve python[2122718]: log bot callback poll failed: HTTP 502: {"ok":false,"error_code":502,"description":"Bad Gateway"}',
            "Jul 14 09:27:59 pve python[2122718]: log bot callback poll failed: timeout: HTTPSConnectionPool(host='api.telegram.org', port=443): Read timed out. (read timeout=35)",
        ]

        for line in samples:
            with self.subTest(line=line):
                self.assertFalse(health_observer.is_hard_journal_line(line))

    def test_listener_sidecar_unauthed_sessions_are_not_hard_journal_errors(self):
        line = (
            "Jul 08 21:00:52 pve python[3686429]: listener sidecar degraded: no connected accounts "
            "failed=[{'account_id': 301299112, 'error': 'listener session 未独立授权: "
            "/opt/xiuxian-main/data/session/listener_account_301299112.session；已禁止复制主 session，请先单独登录 listener_account_301299112。'}]"
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
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "Jul 10 04:02:15 pve python[187263]: [2026-07-10 04:02:15] 🚀 自动化系统启动：全局暂停中，仅加载状态与 UI，跳过启动恢复和普通调度。"
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

    def test_health_payload_treats_listener_inactive_as_optional_warning(self):
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
            "status": "warn",
            "services": {
                "xiuxian.service": {"ActiveState": "active", "SubState": "running"},
                "xiuxian-listener.service": {"ActiveState": "inactive", "SubState": "dead"},
            },
            "listener": {"available": True, "status": "stopped", "age_sec": 999, "path": "/tmp/listener_heartbeat.json"},
            "safety": {"fused": False},
            "journals": [],
            "business": {"message_state": {}, "db_state": {}},
            "foreign_xiuxian_processes": [],
        }

        payload = health_observer.build_health_payload(snapshot, cfg)

        codes = {item["code"] for item in payload["risk_reasons"]}
        self.assertIn("optional_service_inactive", codes)
        self.assertNotIn("service_not_running", codes)
        self.assertNotIn("listener_heartbeat_stale", codes)

    def test_health_payload_does_not_warn_for_explicitly_disabled_optional_listener(self):
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
            "ts": "2026-07-29 03:45:00",
            "status": "ok",
            "services": {
                "xiuxian.service": {"ActiveState": "active", "SubState": "running", "UnitFileState": "enabled"},
                "xiuxian-listener.service": {
                    "ActiveState": "inactive",
                    "SubState": "dead",
                    "UnitFileState": "disabled",
                },
            },
            "listener": {
                "available": True,
                "status": "stopped",
                "age_sec": 999999,
                "path": "/tmp/listener_heartbeat.json",
            },
            "safety": {"fused": False},
            "journals": [],
            "business": {"message_state": {}, "db_state": {}, "alerts": []},
            "foreign_xiuxian_processes": [],
        }

        payload = health_observer.build_health_payload(snapshot, cfg)
        status, reasons = health_observer.classify_snapshot(snapshot["services"], [])

        codes = {item["code"] for item in payload["risk_reasons"]}
        self.assertNotIn("optional_service_inactive", codes)
        self.assertEqual("ok", status)
        self.assertEqual([], reasons)

    def test_health_payload_ignores_stopped_listener_heartbeat_when_sidecar_is_not_observed(self):
        cfg = health_observer.ObserverConfig(
            project_root=Path("/opt/xiuxian-main"),
            services=("xiuxian.service", "xiuxian-safety-watchdog.service"),
            interval_sec=60,
            journal_window_sec=600,
            max_journal_matches=12,
            max_event_lines=100,
            state_dir=Path(tempfile.mkdtemp()),
            business_window_sec=1800,
        )
        snapshot = {
            "ts": "2026-07-29 03:20:00",
            "status": "ok",
            "services": {
                "xiuxian.service": {"ActiveState": "active", "SubState": "running"},
                "xiuxian-safety-watchdog.service": {"ActiveState": "active", "SubState": "running"},
            },
            "listener": {
                "available": True,
                "status": "stopped",
                "age_sec": 999999,
                "path": "/tmp/listener_heartbeat.json",
            },
            "safety": {"fused": False},
            "journals": [],
            "business": {"message_state": {}, "db_state": {}, "alerts": []},
            "foreign_xiuxian_processes": [],
        }

        payload = health_observer.build_health_payload(snapshot, cfg)

        codes = {item["code"] for item in payload["risk_reasons"]}
        self.assertNotIn("listener_heartbeat_stale", codes)
        self.assertNotIn("listener_heartbeat_missing", codes)

    def test_health_payload_uses_fishing_business_density_thresholds(self):
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
            "ts": "2026-07-12 00:15:00",
            "epoch": 1_780_500_000.0,
            "status": "ok",
            "services": {"xiuxian.service": {"ActiveState": "active", "SubState": "running"}},
            "listener": {"available": False},
            "safety": {"fused": False},
            "journals": [],
            "business": {
                "message_state": {
                    "window_sec": 1800,
                    "sent_count": 25,
                    "module_counts": {"灵溪垂钓:入口": 8, "灵溪垂钓:后处理": 22},
                },
                "db_state": {},
            },
            "foreign_xiuxian_processes": [],
        }

        payload = health_observer.build_health_payload(snapshot, cfg)

        self.assertFalse(any(item["code"] == "module_send_density" for item in payload["risk_reasons"]))

    def test_health_payload_downgrades_sidecar_without_accounts_when_main_replies_are_fresh(self):
        now = 1_780_500_000.0
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
            "epoch": now,
            "status": "ok",
            "services": {
                "xiuxian.service": {"ActiveState": "active", "SubState": "running"},
                "xiuxian-listener.service": {"ActiveState": "active", "SubState": "running"},
            },
            "listener": {
                "available": True,
                "status": "degraded_no_connected_accounts",
                "age_sec": 5,
                "path": "/tmp/listener_heartbeat.json",
            },
            "safety": {"fused": False},
            "journals": [],
            "business": {
                "message_state": {
                    "last_bot_reply_at": now - 30,
                    "last_bot_reply_ts": "2026-07-02 01:29:30",
                },
                "db_state": {},
                "alerts": [],
            },
            "foreign_xiuxian_processes": [],
        }

        payload = health_observer.build_health_payload(snapshot, cfg)

        codes = {item["code"] for item in payload["risk_reasons"]}
        self.assertIn("listener_sidecar_unbound", codes)
        self.assertNotIn("listener_status_not_running", codes)
        self.assertEqual(100, payload["score"])
        self.assertEqual("ok", payload["level"])

    def test_health_payload_keeps_sidecar_warning_when_main_replies_are_stale(self):
        now = 1_780_500_000.0
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
            "epoch": now,
            "status": "ok",
            "services": {
                "xiuxian.service": {"ActiveState": "active", "SubState": "running"},
                "xiuxian-listener.service": {"ActiveState": "active", "SubState": "running"},
            },
            "listener": {
                "available": True,
                "status": "degraded_no_connected_accounts",
                "age_sec": 5,
                "path": "/tmp/listener_heartbeat.json",
            },
            "safety": {"fused": False},
            "journals": [],
            "business": {
                "message_state": {
                    "last_bot_reply_at": now - 600,
                    "last_bot_reply_ts": "2026-07-02 01:20:00",
                },
                "db_state": {},
                "alerts": [],
            },
            "foreign_xiuxian_processes": [],
        }

        payload = health_observer.build_health_payload(snapshot, cfg)

        codes = {item["code"] for item in payload["risk_reasons"]}
        self.assertIn("listener_status_not_running", codes)
        self.assertNotIn("listener_sidecar_unbound", codes)

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

    def test_business_message_analysis_allows_two_spaced_active_status_queries(self):
        now = 1_780_500_000.0
        events = [
            {"event_type": "sent", "_epoch": now - 900, "message_id": 101, "sender_id": 1, "text": ".查看闭关"},
            {"event_type": "sent", "_epoch": now - 100, "message_id": 102, "sender_id": 1, "text": ".查看闭关"},
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertEqual(2, result["active_status_identity_counts"]["1:.查看闭关"])
        self.assertFalse(any("active status query repeated" in item["message"] for item in result["alerts"]))

    def test_business_message_analysis_flags_three_spaced_active_status_queries(self):
        now = 1_780_500_000.0
        events = [
            {"event_type": "sent", "_epoch": now - 1500, "message_id": 101, "sender_id": 1, "text": ".查看闭关"},
            {"event_type": "sent", "_epoch": now - 800, "message_id": 102, "sender_id": 1, "text": ".查看闭关"},
            {"event_type": "sent", "_epoch": now - 100, "message_id": 103, "sender_id": 1, "text": ".查看闭关"},
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

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

    def test_business_message_analysis_flags_repeated_daily_once_family(self):
        now = 1_780_500_000.0
        events = [
            {
                "event_type": "sent",
                "_epoch": now - 900,
                "message_id": 1801,
                "sender_id": 8659059191,
                "text": ".婉影问安",
                "family": "wanxin_moon_greet",
            },
            {
                "event_type": "sent",
                "_epoch": now - 200,
                "message_id": 1802,
                "sender_id": 8659059191,
                "text": ".婉影问安",
                "family": "wanxin_moon_greet",
            },
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertTrue(any("daily-once action repeated" in item["message"] for item in result["alerts"]))
        self.assertEqual("daily_once_repeat", result["repeated_command_samples"][0]["kind"])

    def test_business_message_analysis_allows_daily_once_actions_from_different_identities(self):
        now = 1_780_500_000.0
        events = [
            {"event_type": "sent", "_epoch": now - 500, "message_id": 1811, "sender_id": 1, "text": ".婉影问安", "family": "wanxin_moon_greet"},
            {"event_type": "sent", "_epoch": now - 300, "message_id": 1812, "sender_id": 2, "text": ".婉影问安", "family": "wanxin_moon_greet"},
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertFalse(any("daily-once action repeated" in item["message"] for item in result["alerts"]))

    def test_business_message_analysis_excludes_unanchored_broadcasts_from_bot_replies(self):
        now = 1_780_500_000.0
        events = [
            {
                "event_type": "message",
                "_epoch": now - 30,
                "message_id": 301,
                "sender_is_bot": True,
                "reply_to_msg_id": 7310786,
                "text": "━━━━━━━━━━━━━━━\n【世界通告｜真仙试锋开启】\n点击下方按钮进入真仙战场。",
            },
            {
                "event_type": "edit",
                "_epoch": now - 20,
                "message_id": 302,
                "sender_is_bot": True,
                "reply_to_msg_id": 7310786,
                "text": "📜 修士 @foo 深度闭关总结\n【深度闭关总结】\n本次结算时长: 8.0 小时",
            },
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertEqual(0, result["bot_reply_count"])
        self.assertEqual(0.0, result["last_bot_reply_at"])
        self.assertEqual([], result["last_bot_reply_sample"])

    def test_business_message_analysis_counts_bot_reply_to_script_sent(self):
        now = 1_780_500_000.0
        events = [
            {"event_type": "sent", "_epoch": now - 60, "message_id": 401, "sender_id": 10, "text": ".野外历练 谨慎"},
            {
                "event_type": "message",
                "_epoch": now - 50,
                "message_id": 402,
                "sender_is_bot": True,
                "reply_to_msg_id": 401,
                "text": "【野外历练】\n@alpha 选择【谨慎】策略，正向荒野深处行去。",
            },
        ]

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertEqual(1, result["bot_reply_count"])
        self.assertEqual(now - 50, result["last_bot_reply_at"])

    def test_business_message_analysis_splits_fishing_entry_and_post_processing_density(self):
        now = 1_780_500_000.0
        events = []
        for index in range(8):
            events.append({
                "event_type": "sent",
                "_epoch": now - 900 + index,
                "message_id": 500 + index,
                "sender_id": 1000 + index,
                "text": ".钓鱼 青溪浅滩 灵米饵",
                "family": "fishing",
                "source_module": "灵溪垂钓",
            })
        for index in range(17):
            events.append({
                "event_type": "sent",
                "_epoch": now - 600 + index,
                "message_id": 600 + index,
                "sender_id": 1000 + index % 8,
                "text": ".鱼篓" if index % 3 == 0 else ".开鱼 青鳞小鲫 2",
                "family": "fishing",
                "source_module": "灵溪垂钓",
            })

        result = health_observer.analyze_message_events(events, now, 1800)

        self.assertEqual(8, result["module_counts"]["灵溪垂钓:入口"])
        self.assertEqual(17, result["module_counts"]["灵溪垂钓:后处理"])
        self.assertNotIn("灵溪垂钓", result["module_counts"])

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
        self.assertEqual(1, result["module_counts"]["卜筮问天"])

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

    def test_business_db_state_reports_closed_channel_send_as_cohort(self):
        now = 1_780_500_000.0
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "state.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE pending_tasks(
                        msg_id INTEGER PRIMARY KEY, send_as_id INTEGER NOT NULL, cmd TEXT NOT NULL,
                        sent_at REAL NOT NULL, retry INTEGER NOT NULL, timeout REAL NOT NULL,
                        reply_to_msg_id INTEGER NOT NULL DEFAULT 0, max_retry INTEGER NOT NULL DEFAULT 3,
                        priority TEXT NOT NULL DEFAULT '', source_module TEXT NOT NULL DEFAULT '',
                        op_id TEXT NOT NULL DEFAULT '', chain_id TEXT NOT NULL DEFAULT '', delete_policy TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE identities(
                        send_as_id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL DEFAULT '',
                        enabled INTEGER NOT NULL DEFAULT 1
                    );
                    CREATE TABLE identity_timers(
                        send_as_id INTEGER PRIMARY KEY,
                        next_concubine_time REAL NOT NULL DEFAULT 0,
                        next_deep_retreat_time REAL NOT NULL DEFAULT 0,
                        next_yuanying_time REAL NOT NULL DEFAULT 0,
                        next_wild_training_time REAL NOT NULL DEFAULT 0
                    );
                    CREATE TABLE identity_module_state(
                        send_as_id INTEGER PRIMARY KEY,
                        wild_training_enabled INTEGER NOT NULL DEFAULT 0,
                        tianxing_enabled INTEGER NOT NULL DEFAULT 0
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
                    "INSERT INTO meta(key, value) VALUES('channel_send_as_health', ?)",
                    (json.dumps({
                        "status": "closed",
                        "frozen_identity_ids": [101, 102],
                        "restore_identity_ids": [101, 102],
                        "last_error": "SendAsPeerInvalidError",
                        "last_probe_at": now - 60,
                        "next_probe_at": now + 240,
                    }),),
                )
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('account_target_memberships', ?)",
                    (json.dumps({
                        "7001": {
                            "account_id": 7001,
                            "status": "not_member",
                            "probe_status": "unknown",
                            "identity_ids": [101, 102],
                            "reason": "USER_NOT_PARTICIPANT",
                            "checked_at": now - 60,
                            "next_probe_at": now + 840,
                        }
                    }),),
                )
                conn.execute("INSERT INTO identities(send_as_id, username, enabled) VALUES(101, 'frozen', 0)")
                conn.execute(
                    "INSERT INTO identity_timers(send_as_id, next_wild_training_time) VALUES(101, ?)",
                    (now - 3600,),
                )
                conn.execute(
                    "INSERT INTO identity_module_state(send_as_id, wild_training_enabled, tianxing_enabled) VALUES(101, 1, 0)"
                )
                conn.commit()

            result = health_observer.read_db_business_state(db_path, now)

        self.assertEqual("closed", result["channel_send_as_health"]["status"])
        alerts = [item for item in result["alerts"] if "channel send-as cohort frozen" in item["message"]]
        self.assertEqual(1, len(alerts))
        self.assertEqual(2, alerts[0]["count"])
        self.assertEqual("info", alerts[0]["severity"])
        self.assertEqual("SendAsPeerInvalidError", alerts[0]["sample"]["last_error"])
        membership_alerts = [item for item in result["alerts"] if "outside target group" in item["message"]]
        self.assertEqual(1, len(membership_alerts))
        self.assertEqual("info", membership_alerts[0]["severity"])
        self.assertEqual(7001, membership_alerts[0]["sample"][0]["account_id"])
        wild_alerts = [item for item in result["alerts"] if "public wild-training lag" in item["message"]]
        self.assertEqual(1, len(wild_alerts))
        self.assertEqual(1, wild_alerts[0]["count"])
        self.assertEqual(101, wild_alerts[0]["sample"][0]["identity_id"])

    def test_business_db_state_ignores_stuck_phases_for_disabled_identity(self):
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
                    CREATE TABLE identities(
                        send_as_id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL DEFAULT '',
                        enabled INTEGER NOT NULL DEFAULT 1
                    );
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
                conn.execute("INSERT INTO identities(send_as_id, username, enabled) VALUES(42, 'disabled', 0)")
                conn.execute(
                    "INSERT INTO identity_timers(send_as_id, next_concubine_time, next_deep_retreat_time) VALUES(42, ?, ?)",
                    (now - 700, now - 700),
                )
                conn.execute(
                    "INSERT INTO identity_runtime_state(send_as_id, concubine_phase, deep_retreat_phase) VALUES(42, 'dream_pending', 'summary_due')"
                )

            result = health_observer.read_db_business_state(db_path, now)

        self.assertTrue(result["available"])
        self.assertEqual([], result["stuck_phases"])
        self.assertFalse(any("stuck runtime phases" in item["message"] for item in result["alerts"]))

    def test_business_db_state_suppresses_scheduler_lag_during_recovery_ramp(self):
        now = 1_780_500_000.0
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "state.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE pending_tasks(
                        msg_id INTEGER PRIMARY KEY, send_as_id INTEGER NOT NULL, cmd TEXT NOT NULL,
                        sent_at REAL NOT NULL, retry INTEGER NOT NULL, timeout REAL NOT NULL,
                        reply_to_msg_id INTEGER NOT NULL DEFAULT 0, max_retry INTEGER NOT NULL DEFAULT 3,
                        priority TEXT NOT NULL DEFAULT '', source_module TEXT NOT NULL DEFAULT '',
                        op_id TEXT NOT NULL DEFAULT '', chain_id TEXT NOT NULL DEFAULT '', delete_policy TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '');
                    CREATE TABLE identity_timers(
                        send_as_id INTEGER PRIMARY KEY, next_concubine_time REAL NOT NULL DEFAULT 0,
                        next_deep_retreat_time REAL NOT NULL DEFAULT 0, next_yuanying_time REAL NOT NULL DEFAULT 0
                    );
                    CREATE TABLE identity_runtime_state(
                        send_as_id INTEGER PRIMARY KEY, concubine_phase TEXT NOT NULL DEFAULT 'idle',
                        deep_retreat_phase TEXT NOT NULL DEFAULT 'idle', deep_retreat_summary_sent_at REAL NOT NULL DEFAULT 0,
                        yuanying_phase TEXT NOT NULL DEFAULT 'idle', yuanying_summary_sent_at REAL NOT NULL DEFAULT 0,
                        tower_reply_due_at REAL NOT NULL DEFAULT 0, last_tower_msg_id INTEGER NOT NULL DEFAULT 0
                    );
                    """
                )
                conn.execute("INSERT INTO meta(key, value) VALUES('global_recovery_throttle_until', ?)", (str(now + 900),))
                conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
                conn.execute(
                    "INSERT INTO identity_timers(send_as_id, next_deep_retreat_time) VALUES(42, ?)",
                    (now - 700,),
                )
                conn.execute(
                    "INSERT INTO identity_runtime_state(send_as_id, deep_retreat_phase) VALUES(42, 'post_summary_wait')"
                )

            result = health_observer.read_db_business_state(db_path, now)

        self.assertTrue(result["available"])
        self.assertEqual([], result["stuck_phases"])
        self.assertEqual(now + 900, result["recovery_throttle_until"])
        self.assertFalse(any("stuck runtime phases" in item["message"] for item in result["alerts"]))

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

    def test_module_summary_treats_small_world_manifest_loss_as_game_result(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, small_world_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_small_world_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    small_world_phase TEXT NOT NULL DEFAULT 'idle',
                    small_world_query_msg_id INTEGER NOT NULL DEFAULT 0,
                    small_world_preach_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    small_world_manifest_msg_id INTEGER NOT NULL DEFAULT 0,
                    small_world_harvest_msg_id INTEGER NOT NULL DEFAULT 0,
                    small_world_barrier_msg_id INTEGER NOT NULL DEFAULT 0,
                    small_world_preach_due_at REAL NOT NULL DEFAULT 0,
                    small_world_barrier_due_at REAL NOT NULL DEFAULT 0,
                    small_world_god_cooldown_until REAL NOT NULL DEFAULT 0,
                    small_world_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'smallworld')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, small_world_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_small_world_time) VALUES(42, ?)", (now + 3600,))
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, small_world_last_error) VALUES(42, '显灵失败，停止本轮')"
            )

            summary = health_observer.build_module_summary(conn, now)

        item = next(entry for entry in summary if entry["module"] == "small_world")
        self.assertEqual("ok", item["status"])
        self.assertTrue(any("显灵失败，停止本轮" in detail for detail in item["details"]))

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

    def test_module_summary_treats_scheduled_hehuan_anchor_failure_as_warn(self):
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
                    "last_observed_at": now - 3600,
                    "last_action": "双修 温养",
                    "last_result": "success",
                    "last_summary": "温养双修成功",
                    "auto_last_action": "warm",
                    "auto_last_error": "同参对象 @jfdffdddd 发言锚点发送失败，暂不裸发温养双修。",
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

    def test_module_summary_suppresses_overdue_next_time_when_global_paused(self):
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

            summary = health_observer.build_module_summary(conn, now, global_paused=True)

        wild = next(item for item in summary if item["module"] == "wild_training")
        self.assertEqual("ok", wild["status"])
        self.assertFalse(wild["next"][0]["lag_without_anchor"])
        self.assertFalse(any("调度滞后" in detail for detail in wild["details"]))

    def test_module_summary_suppresses_overdue_due_field_during_recovery(self):
        now = 1_780_500_000.0
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
                CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, duel_enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE identity_timers(send_as_id INTEGER PRIMARY KEY, next_duel_time REAL NOT NULL DEFAULT 0);
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    duel_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    duel_open_msg_id INTEGER NOT NULL DEFAULT 0,
                    duel_reply_due_at REAL NOT NULL DEFAULT 0,
                    duel_magic_due_at REAL NOT NULL DEFAULT 0,
                    duel_last_result TEXT NOT NULL DEFAULT '',
                    duel_last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute("INSERT INTO identities(send_as_id, username) VALUES(42, 'tester')")
            conn.execute("INSERT INTO identity_module_state(send_as_id, duel_enabled) VALUES(42, 1)")
            conn.execute("INSERT INTO identity_timers(send_as_id, next_duel_time) VALUES(42, ?)", (now - 300,))
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, duel_reply_to_msg_id, duel_open_msg_id, duel_reply_due_at) VALUES(42, 100, 101, ?)",
                (now - 300,),
            )

            summary = health_observer.build_module_summary(conn, now, global_paused=True)

        duel_item = next(item for item in summary if item["module"] == "duel")
        self.assertNotEqual("error", duel_item["status"])
        self.assertEqual(300, duel_item["due"][0]["overdue_sec"])

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

    def test_module_summary_ignores_consumed_tianxing_deadline_after_replan(self):
        now = 1_780_500_000.0
        timeline = json.dumps({
            "phase": "blocked_replan",
            "route": "探索",
            "last_error": "探索 放行已被下游动作消费，需重算时间线。",
            "deadline_at": now - 3600,
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
            conn.execute("INSERT INTO identity_runtime_state(send_as_id, tianxing_timeline_state) VALUES(42, ?)", (timeline,))

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

    def test_merge_status_keeps_expected_business_info_out_of_warning(self):
        status, reasons = health_observer.merge_status(
            "ok",
            [health_observer.business_alert("channel cohort intentionally frozen", severity="info")],
        )

        self.assertEqual("ok", status)
        self.assertEqual([], reasons)


if __name__ == "__main__":
    unittest.main()
