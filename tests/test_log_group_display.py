import atexit
import asyncio
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
                "ADMIN_ID=1",
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

from model import control
from model.config import (
    RE_CMD_ANALYSIS_HEALTH,
    RE_CMD_ANALYSIS_LOG_GROUP,
    RE_CMD_ANALYSIS_SUMMARY,
    RE_CMD_ANALYSIS_UNKNOWN,
    RE_CMD_ANALYSIS_WEBMINI,
    RE_CMD_AUDIT_FLUSH_SUMMARY,
    RE_CMD_AUDIT_PUSH_STATUS,
    RE_CMD_ENABLE_PATTERNS,
    RE_CMD_HELP,
    RE_CMD_STAGING_PREFLIGHT,
    RE_CMD_SINGLE_STATUS_PATTERNS,
)


class LogGroupDisplayTests(unittest.TestCase):
    def test_help_regex_accepts_command_aliases(self):
        self.assertIsNotNone(RE_CMD_HELP.match(".指令"))
        self.assertIsNotNone(RE_CMD_HELP.match(".帮助"))
        self.assertIsNotNone(RE_CMD_HELP.match(".help"))

    def test_analysis_regex_accepts_log_group_aliases(self):
        self.assertIsNotNone(RE_CMD_ANALYSIS_SUMMARY.match(".玩法总览"))
        self.assertIsNotNone(RE_CMD_ANALYSIS_HEALTH.match(".发送健康码"))
        self.assertIsNotNone(RE_CMD_ANALYSIS_LOG_GROUP.match(".日志群分析"))
        self.assertIsNotNone(RE_CMD_ANALYSIS_WEBMINI.match(".miniweb分析"))
        self.assertIsNotNone(RE_CMD_ANALYSIS_UNKNOWN.match(".未知指令"))

    def test_audit_push_regex_accepts_log_group_aliases(self):
        self.assertIsNotNone(RE_CMD_AUDIT_PUSH_STATUS.match(".日志推送状态"))
        self.assertIsNotNone(RE_CMD_AUDIT_PUSH_STATUS.match(".推送状态"))
        self.assertIsNotNone(RE_CMD_AUDIT_FLUSH_SUMMARY.match(".发送日志汇总"))
        self.assertIsNotNone(RE_CMD_AUDIT_FLUSH_SUMMARY.match(".立即日志汇总"))

    def test_staging_preflight_regex_accepts_aliases(self):
        self.assertIsNotNone(RE_CMD_STAGING_PREFLIGHT.match(".上线预检"))
        self.assertIsNotNone(RE_CMD_STAGING_PREFLIGHT.match(".待上线预检"))
        self.assertIsNotNone(RE_CMD_STAGING_PREFLIGHT.match(".预检"))

    def test_status_patterns_include_recent_modules(self):
        module_names = {name for _pattern, name in RE_CMD_SINGLE_STATUS_PATTERNS}
        self.assertIn("第二元神", module_names)
        self.assertIn("太一", module_names)
        self.assertIn("放养", module_names)
        self.assertIn("野外历练", module_names)
        self.assertIn("自动副本", module_names)

    def test_user_facing_status_commands_all_match(self):
        commands = [
            ".灵树状态",
            ".法宝状态",
            ".器灵试炼状态",
            ".观星台状态",
            ".观星状态",
            ".观星监控状态",
            ".天阶状态",
            ".玄骨考校状态",
            ".极阴祖师状态",
            ".侍妾状态",
            ".天机代卜状态",
            ".共历心劫状态",
            ".南陇侯状态",
            ".元婴状态",
            ".深度闭关状态",
            ".第二元神状态",
            ".太一状态",
            ".小世界状态",
            ".点卯状态",
            ".闯塔状态",
            ".自动副本状态",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(
                    any(pattern.match(command) for pattern, _module_name in RE_CMD_SINGLE_STATUS_PATTERNS),
                    command,
                )

    def test_module_toggle_patterns_include_recent_modules(self):
        toggles = {
            (module_name, enabled)
            for _pattern, module_name, enabled in RE_CMD_ENABLE_PATTERNS
        }
        self.assertIn(("放养", True), toggles)
        self.assertIn(("野外历练", True), toggles)
        self.assertIn(("第二元神", True), toggles)
        self.assertIn(("太一", False), toggles)
        self.assertIn(("自动副本", True), toggles)

    def test_log_group_card_escapes_html(self):
        html_text = control._format_log_group_card_html("状态<标题>", "a < b & c")
        self.assertIn("状态&lt;标题&gt;", html_text)
        self.assertIn("a &lt; b &amp; c", html_text)
        self.assertIn("<pre>", html_text)

    def test_log_group_command_accepts_string_admin_sender_id(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id="123456", raw_text=".全局暂停")

        with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                patch.object(control, "toggle_global_enabled", new=AsyncMock(return_value=(True, "ok"))) as toggle_mock, \
                patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
            handled = asyncio.run(control.handle_log_group_command(event))

        self.assertTrue(handled)
        toggle_mock.assert_awaited_once_with(False, source="log_group", actor_id=123456)
        reply_mock.assert_awaited_once()

    def test_log_group_command_rejects_non_admin_sender_id(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id="123457", raw_text=".全局暂停")

        with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                patch.object(control, "toggle_global_enabled", new=AsyncMock()) as toggle_mock, \
                patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
            handled = asyncio.run(control.handle_log_group_command(event))

        self.assertFalse(handled)
        toggle_mock.assert_not_awaited()
        reply_mock.assert_not_awaited()

    def test_help_mentions_safe_status_and_selector(self):
        html_text = control._format_log_group_help_html()
        self.assertIn(".状态", html_text)
        self.assertIn("@昵称", html_text)
        self.assertIn("全局锁", html_text)
        self.assertIn(".放养状态", html_text)
        self.assertIn(".野外历练状态", html_text)
        self.assertIn(".自动副本状态", html_text)
        self.assertIn(".储物袋汇总", html_text)
        self.assertIn(".玩法总览", html_text)
        self.assertIn(".上线预检", html_text)
        self.assertIn(".发送健康码", html_text)
        self.assertIn("日志推送", html_text)
        self.assertIn(".日志推送状态", html_text)
        self.assertIn(".发送日志汇总", html_text)
        self.assertIn("副本群轻量指令", html_text)
        self.assertIn(".查询副本", html_text)
        self.assertIn(".开启副本 @用户名", html_text)
        self.assertIn(".加入副本 @用户名 @用户名", html_text)
        self.assertIn(".解散副本", html_text)
        self.assertIn("主线拉人群兼容指令", html_text)
        self.assertIn(".苍坤洞府 123 @用户名", html_text)
        self.assertIn("只读", html_text)

    def test_staging_preflight_formatter_includes_guards(self):
        payload = {
            "summary": {"scanned_lines": 100},
            "health": {
                "sent_total": 12,
                "duplicate_short_gap": [{"command": ".深度闭关"}],
                "any_short_gap": [],
            },
        }

        with patch.object(control, "get_identity_ids", return_value=[]), \
                patch.object(control, "get_game_send_queue_snapshot", return_value=[]), \
                patch.object(control, "get_low_priority_audit_pending_counts", return_value=(0, 0)), \
                patch.object(control, "_load_analysis_payload", return_value=(payload, None)), \
                patch.object(control, "_format_analysis_mtime", return_value="2026-05-24 14:00:00"):
            text = control._format_staging_preflight_text()

        self.assertIn("待上线预检", text)
        self.assertIn("默认最多补发一次", text)
        self.assertIn("去重位已持久化", text)
        self.assertIn("副本旧群调度只回迁移提示", text)
        self.assertIn("扫描行数: 100", text)

    def test_auto_dungeon_status_text_is_not_unknown(self):
        text = control.get_single_module_status_text("自动副本")

        self.assertIn("自动副本状态", text)
        self.assertIn("副本群轻量指令", text)
        self.assertIn("主线拉人群", text)
        self.assertNotIn("未知模块", text)

    def test_analysis_summary_formatter_includes_core_counts(self):
        payload = {
            "summary": {
                "scanned_lines": 1234,
                "invalid_json": 0,
                "source_files": [{"key": "2026-05-23.log", "count": 10}],
                "dates": [{"key": "2026-05-23", "count": 10}],
                "command_families": [{"key": "deep_retreat", "count": 5}],
                "sent_by_family": [{"key": "tree", "count": 3}],
                "hard_stop_hits": [{"keyword": "已被封禁"}],
                "log_group_id": -1003807888644,
            },
            "commands": [{"command": ".深度闭关"}],
            "health": {"sent_total": 7},
            "miniweb": {
                "available": True,
                "raw_messages": {
                    "count": 9,
                    "min_date": "2026-05-23T00:00:00+00:00",
                    "max_date": "2026-05-23T01:00:00+00:00",
                },
            },
        }

        text = control._format_analysis_summary_text(payload)

        self.assertIn("扫描行数: 1,234", text)
        self.assertIn("自动发送: 7", text)
        self.assertIn("deep_retreat: 5", text)
        self.assertIn("tree: 3", text)
        self.assertIn("只读摘要", text)

    def test_analysis_health_formatter_marks_candidates(self):
        payload = {
            "summary": {"hard_stop_hits": [{"keyword": "封禁"}]},
            "health": {
                "sent_total": 2,
                "duplicate_short_gap": [
                    {
                        "cur_ts": "2026-05-23 10:00:00 UTC+8",
                        "sender_id": 1,
                        "gap_sec": 3,
                        "cur_command": ".观星台",
                    }
                ],
                "any_short_gap": [],
                "missing_direct_replies_total": 1,
                "missing_direct_replies_sample": [
                    {
                        "ts": "2026-05-23 10:00:01 UTC+8",
                        "sender_id": 1,
                        "command": "1",
                        "message_id": 9,
                        "family": "non_command",
                    }
                ],
            },
        }

        text = control._format_analysis_health_text(payload)

        self.assertIn("发送健康码（离线候选）", text)
        self.assertIn("重复样本", text)
        self.assertIn("missing_direct_replies 不等于漏发", text)


if __name__ == "__main__":
    unittest.main()
