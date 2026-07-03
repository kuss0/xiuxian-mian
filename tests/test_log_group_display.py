import atexit
import asyncio
import copy
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
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
from model import state as state_module
from model.features import tianxing
from model.config import (
    RE_CMD_ANALYSIS_HEALTH,
    RE_CMD_ANALYSIS_LOG_GROUP,
    RE_CMD_ANALYSIS_SUMMARY,
    RE_CMD_ANALYSIS_UNKNOWN,
    RE_CMD_ANALYSIS_WEBMINI,
    RE_CMD_RUNTIME_HEALTH,
    RE_CMD_RUNTIME_HEALTH_DETAIL,
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
        self.assertIsNotNone(RE_CMD_ANALYSIS_HEALTH.match(".分析健康"))
        self.assertIsNotNone(RE_CMD_ANALYSIS_HEALTH.match(".发送健康码"))
        self.assertIsNotNone(RE_CMD_RUNTIME_HEALTH.match(".运行健康"))
        self.assertIsNotNone(RE_CMD_RUNTIME_HEALTH.match(".健康摘要"))
        self.assertIsNotNone(RE_CMD_RUNTIME_HEALTH_DETAIL.match(".健康详情"))
        self.assertIsNotNone(RE_CMD_RUNTIME_HEALTH_DETAIL.match(".接手审计"))
        self.assertIsNotNone(RE_CMD_ANALYSIS_LOG_GROUP.match(".日志群分析"))
        self.assertIsNotNone(RE_CMD_ANALYSIS_WEBMINI.match(".miniweb分析"))
        self.assertIsNotNone(RE_CMD_ANALYSIS_UNKNOWN.match(".未知指令"))

    def test_audit_push_regex_accepts_log_group_aliases(self):
        self.assertIsNotNone(RE_CMD_AUDIT_PUSH_STATUS.match(".审计推送状态"))
        self.assertIsNotNone(RE_CMD_AUDIT_PUSH_STATUS.match(".日志推送状态"))
        self.assertIsNotNone(RE_CMD_AUDIT_PUSH_STATUS.match(".推送状态"))
        self.assertIsNotNone(RE_CMD_AUDIT_FLUSH_SUMMARY.match(".发送日志汇总"))
        self.assertIsNotNone(RE_CMD_AUDIT_FLUSH_SUMMARY.match(".立即日志汇总"))

    def test_staging_preflight_regex_accepts_aliases(self):
        self.assertIsNotNone(RE_CMD_STAGING_PREFLIGHT.match(".预发布检查"))
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
        self.assertIn("天星宗", module_names)
        self.assertIn("阴罗宗", module_names)
        self.assertIn("探寻裂缝", module_names)
        self.assertIn("宗门传功", module_names)

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
            ".合欢宗状态",
            ".天星宗状态",
            ".阴罗宗状态",
            ".南陇侯状态",
            ".元婴状态",
            ".探寻裂缝状态",
            ".深度闭关状态",
            ".第二元神状态",
            ".太一状态",
            ".小世界状态",
            ".卜筮问天状态",
            ".点卯状态",
            ".宗门传功状态",
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
        self.assertIn(("天星宗", True), toggles)
        self.assertIn(("阴罗宗", True), toggles)
        self.assertIn(("卜筮问天", True), toggles)
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

    def test_readme_maintenance_aliases_are_handled(self):
        cases = [
            (".审计推送状态", "日志推送状态", "ok"),
            (".预发布检查", "待上线预检", "preflight"),
            (".分析健康", "发送健康码", "health"),
        ]
        for command, expected_title, formatter_result in cases:
            with self.subTest(command=command):
                event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=command)
                with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                        patch.object(control, "get_audit_push_status_text", return_value=formatter_result), \
                        patch.object(control, "_format_staging_preflight_text", return_value=formatter_result), \
                        patch.object(control, "_format_analysis_report_text", return_value=formatter_result), \
                        patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
                    handled = asyncio.run(control.handle_log_group_command(event))

                self.assertTrue(handled)
                reply_mock.assert_awaited_once()
                self.assertEqual(expected_title, reply_mock.await_args.args[1])

    def test_help_mentions_safe_status_and_selector(self):
        html_text = control._format_log_group_help_html()
        self.assertIn(".状态", html_text)
        self.assertIn("@昵称", html_text)
        self.assertIn("全局锁", html_text)
        self.assertIn(".消息盒子shadow", html_text)
        self.assertIn(".消息契约", html_text)
        self.assertIn(".放养状态", html_text)
        self.assertIn(".野外历练状态", html_text)
        self.assertIn(".自动副本状态", html_text)
        self.assertIn(".储物袋汇总", html_text)
        self.assertIn(".还有多少 &lt;物品名&gt;", html_text)
        self.assertIn(".更新储物袋", html_text)
        self.assertIn(".副本cd", html_text)
        self.assertIn(".玩法总览", html_text)
        self.assertIn(".上线预检", html_text)
        self.assertIn(".运行健康", html_text)
        self.assertIn(".深入汇总", html_text)
        self.assertIn(".发送健康码", html_text)
        self.assertIn("日志推送", html_text)
        self.assertIn(".日志推送状态", html_text)
        self.assertIn(".发送日志汇总", html_text)
        self.assertIn("三宗门手动发送", html_text)
        self.assertIn(".合欢温养 @身份", html_text)
        self.assertIn(".天星查盘 @身份", html_text)
        self.assertIn(".阴罗献祭 @身份", html_text)
        self.assertIn(".阴罗血洗 @身份", html_text)
        self.assertIn(".阴罗化煞 &lt;数量&gt; @身份", html_text)
        self.assertIn("虚天后续兜底", html_text)
        self.assertIn(".选择道路 火 @身份", html_text)
        self.assertIn(".后殿阵策 卦 @身份", html_text)
        self.assertIn("副本群轻量指令", html_text)
        self.assertIn(".查询副本", html_text)
        self.assertIn(".查询昆 / .查询虚 / .查询苍", html_text)
        self.assertIn(".副本帮助", html_text)
        self.assertIn(".开启副本 @用户名", html_text)
        self.assertIn(".加入副本 @用户名 @用户名", html_text)
        self.assertIn(".解散副本", html_text)
        self.assertIn("主线拉人群兼容指令", html_text)
        self.assertIn("已停用", html_text)
        self.assertIn(".苍坤洞府 123 @用户名", html_text)
        self.assertIn("只读", html_text)

    def test_wild_deep_summary_reads_sent_logs_and_missing_results(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
            state_module.ensure_identity_registered(8659059191)
            state_module.update_send_as_profile(8659059191, username="WalterWA2000", label="wa2000")
            with tempfile.TemporaryDirectory() as tmpdir:
                log_path = Path(tmpdir) / "2026-07-01.log"
                rows = [
                    {
                        "ts": "2026-07-01 08:00:00 UTC+8",
                        "event_type": "sent",
                        "message_id": 100,
                        "sender_id": 8659059191,
                        "reply_to_msg_id": 0,
                        "text": ".野外历练 深入",
                    },
                    {
                        "ts": "2026-07-01 08:00:02 UTC+8",
                        "event_type": "message",
                        "message_id": 101,
                        "sender_id": 8609885831,
                        "reply_to_msg_id": 100,
                        "text": "【野外历练】\n@WalterWA2000 选择【深入】策略，正向荒野深处行去...",
                    },
                    {
                        "ts": "2026-07-01 08:00:06 UTC+8",
                        "event_type": "edit",
                        "message_id": 101,
                        "sender_id": 8609885831,
                        "reply_to_msg_id": 100,
                        "text": "【野外历练 · 妖兽遭遇】\n【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30\n获得修为 +45000，获得 【养魂木】x2。",
                    },
                    {
                        "ts": "2026-07-01 10:00:00 UTC+8",
                        "event_type": "sent",
                        "message_id": 200,
                        "sender_id": 8659059191,
                        "reply_to_msg_id": 0,
                        "text": ".野外历练 深入",
                    },
                    {
                        "ts": "2026-07-01 10:00:02 UTC+8",
                        "event_type": "message",
                        "message_id": 201,
                        "sender_id": 8609885831,
                        "reply_to_msg_id": 200,
                        "text": "【野外历练】\n@WalterWA2000 选择【深入】策略，正向荒野深处行去...",
                    },
                    {
                        "ts": "2026-07-01 10:00:03 UTC+8",
                        "event_type": "message",
                        "message_id": 202,
                        "sender_id": 999,
                        "reply_to_msg_id": 0,
                        "text": ".野外历练 深入",
                    },
                ]
                log_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
                with patch.object(control, "MESSAGES_DIR", tmpdir):
                    text = control._format_wild_deep_summary_text(
                        explicit_identity_id=8659059191,
                        now=datetime(2026, 7, 1, 12, 0, tzinfo=control.TZ_LOCAL).timestamp(),
                    )

            self.assertIn("总览: 有效轮次 2", text)
            self.assertIn("已结算 1", text)
            self.assertIn("成功: 1", text)
            self.assertIn("失败: 0", text)
            self.assertIn("未捕获: 1", text)
            self.assertIn("原始发送 2", text)
            self.assertIn("修为+45,000", text)
            self.assertIn("天机+1", text)
            self.assertIn("贡献+30", text)
            self.assertIn("养魂木x2", text)
            self.assertIn("时间图", text)
            self.assertIn("08:00✓", text)
            self.assertIn("10:00?", text)
            self.assertNotIn("最近:", text)
            self.assertIn("msg=200", text)
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(meta_snapshot))

    def test_wild_deep_summary_merges_retry_anchor(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
            state_module.ensure_identity_registered(8659059191)
            state_module.update_send_as_profile(8659059191, username="WalterWA2000", label="wa2000")
            with tempfile.TemporaryDirectory() as tmpdir:
                log_path = Path(tmpdir) / "2026-07-01.log"
                rows = [
                    {
                        "ts": "2026-07-01 08:00:00 UTC+8",
                        "event_type": "sent",
                        "message_id": 100,
                        "sender_id": 8659059191,
                        "reply_to_msg_id": 0,
                        "text": ".野外历练 深入",
                    },
                    {
                        "ts": "2026-07-01 08:00:02 UTC+8",
                        "event_type": "message",
                        "message_id": 101,
                        "sender_id": 8609885831,
                        "reply_to_msg_id": 100,
                        "text": "【野外历练】\n@WalterWA2000 选择【深入】策略，正向荒野深处行去...",
                    },
                    {
                        "ts": "2026-07-01 08:00:06 UTC+8",
                        "event_type": "edit",
                        "message_id": 101,
                        "sender_id": 8609885831,
                        "reply_to_msg_id": 100,
                        "text": "【野外历练 · 妖兽遭遇】\n【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30\n获得修为 +45000。",
                    },
                    {
                        "ts": "2026-07-01 10:00:00 UTC+8",
                        "event_type": "sent",
                        "message_id": 200,
                        "sender_id": 8659059191,
                        "reply_to_msg_id": 0,
                        "text": ".野外历练 深入",
                    },
                    {
                        "ts": "2026-07-01 10:00:02 UTC+8",
                        "event_type": "message",
                        "message_id": 201,
                        "sender_id": 8609885831,
                        "reply_to_msg_id": 200,
                        "text": "【野外历练】\n@WalterWA2000 选择【深入】策略，正向荒野深处行去...",
                    },
                    {
                        "ts": "2026-07-01 10:08:00 UTC+8",
                        "event_type": "sent",
                        "message_id": 210,
                        "sender_id": 8659059191,
                        "reply_to_msg_id": 0,
                        "text": ".野外历练 深入",
                    },
                    {
                        "ts": "2026-07-01 10:08:02 UTC+8",
                        "event_type": "message",
                        "message_id": 211,
                        "sender_id": 8609885831,
                        "reply_to_msg_id": 210,
                        "text": "【野外历练】\n@WalterWA2000 选择【深入】策略，正向荒野深处行去...",
                    },
                    {
                        "ts": "2026-07-01 10:08:05 UTC+8",
                        "event_type": "edit",
                        "message_id": 211,
                        "sender_id": 8609885831,
                        "reply_to_msg_id": 210,
                        "text": "【野外历练 · 妖兽遭遇】\n【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30\n【改命回天】劫线被改命截断。\n获得修为 +0。",
                    },
                ]
                log_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
                with patch.object(control, "MESSAGES_DIR", tmpdir):
                    text = control._format_wild_deep_summary_text(
                        explicit_identity_id=8659059191,
                        now=datetime(2026, 7, 1, 12, 0, tzinfo=control.TZ_LOCAL).timestamp(),
                    )

            self.assertIn("总览: 有效轮次 2", text)
            self.assertIn("已结算 2", text)
            self.assertIn("脱险: 1", text)
            self.assertIn("补发合并 1", text)
            self.assertIn("原始发送 3", text)
            self.assertNotIn("未捕获:", text)
            self.assertIn("10:08~补1", text)
            self.assertIn("10:00->10:08 ~改命脱险｜补发1", text)
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(meta_snapshot))

    def test_storage_bag_simple_find_uses_desensitized_totals(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
            state_module.ensure_identity_registered(3101)
            state_module.ensure_identity_registered(3102)
            state_module.update_send_as_profile(3101, username="boxboxji", label="盒子", daohao="守一子")
            state_module.update_send_as_profile(3102, username="WalterWA2000", label="wa2000", daohao="清源子")
            state_module.set_storage_bag_records({
                "3101": {"items": {"木髓": 5, "木髓精华": 2, "灵石": 100}},
                "3102": {"items": {"木髓": 999}},
            })

            text = control._format_storage_bag_simple_find_text("木髓")

            self.assertIn("📦 物资统计: 木髓", text)
            self.assertIn("📊 总计: 1,006", text)
            self.assertIn("👥 角色: 配置 2 个，扫描 2/2 个，命中 2 个", text)
            self.assertIn("🎯 匹配: 精确+模糊", text)
            self.assertIn("📌 匹配物品 (2)", text)
            self.assertIn("📋 持有明细 (2)", text)
            self.assertIn("- 木髓: 1,004", text)
            self.assertIn("- 木髓精华: 2", text)
            self.assertIn("盒子[守一子]", text)
            self.assertIn("999", text)
            self.assertNotIn("3101", text)
            self.assertIn("wa2000[清源子]", text)
            self.assertNotIn("boxboxji", text)
            self.assertNotIn("WalterWA2000", text)
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_log_group_storage_bag_simple_find_is_read_only(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".还有多少 木髓")

        with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
            handled = asyncio.run(control.handle_log_group_command(event))

        self.assertTrue(handled)
        reply_mock.assert_awaited_once()
        send_mock.assert_not_awaited()

    def test_log_group_storage_bag_api_refresh_is_manual_api_only(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".更新储物袋")
        result = {
            "ok": True,
            "message": "已刷新 2 个身份的储物袋（内容变化 2 个）",
            "updated_count": 2,
            "changed_count": 2,
            "skipped_count": 1,
            "updated_identity_ids": [3101, 3102],
        }

        with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                patch.object(control, "refresh_storage_bag_records_from_api", new=AsyncMock(return_value=result)) as refresh_mock, \
                patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
            handled = asyncio.run(control.handle_log_group_command(event))

        self.assertTrue(handled)
        refresh_mock.assert_awaited_once_with(identity_ids=None)
        reply_mock.assert_awaited_once()
        self.assertEqual("储物袋 API 更新", reply_mock.await_args.args[1])
        self.assertIn("结果: 成功", reply_mock.await_args.args[2])
        self.assertIn("刷新: 2 个身份", reply_mock.await_args.args[2])
        self.assertIn("内容变化: 2 个身份", reply_mock.await_args.args[2])
        send_mock.assert_not_awaited()

    def test_log_group_storage_bag_api_refresh_can_target_one_identity(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
            state_module.ensure_identity_registered(3101)
            state_module.update_send_as_profile(3101, username="boxboxji", label="boxboxji")
            event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".更新储物袋 @boxboxji")
            result = {
                "ok": True,
                "message": "已刷新 1 个身份的储物袋（内容变化 1 个）",
                "updated_count": 1,
                "changed_count": 1,
                "skipped_count": 0,
                "updated_identity_ids": [3101],
            }

            with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                    patch.object(control, "refresh_storage_bag_records_from_api", new=AsyncMock(return_value=result)) as refresh_mock, \
                    patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                    patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
                handled = asyncio.run(control.handle_log_group_command(event))

            self.assertTrue(handled)
            refresh_mock.assert_awaited_once_with(identity_ids=[3101])
            self.assertIn("范围: boxboxji", reply_mock.await_args.args[2])
            send_mock.assert_not_awaited()
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_log_group_dungeon_query_alias_replies_status(self):
        for raw_text in (".查询副本", ".查询 虚天殿", ".查询虚", ".查询昆", ".查询苍"):
            with self.subTest(raw_text=raw_text):
                event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=raw_text)
                panel = {"text": "房间：无\n昆吾可开：1\n操作：点按钮", "buttons": [[{"text": "开昆 @leader", "callback_data": "rp:1"}]]}

                with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                        patch.object(control, "build_log_group_replica_panel", return_value=panel) as panel_mock, \
                        patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                        patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
                    handled = asyncio.run(control.handle_log_group_command(event))

                self.assertTrue(handled)
                panel_mock.assert_called_once_with(raw_text, fallback_chat_id=control.LOG_GROUP_ID)
                reply_mock.assert_awaited_once()
                args = reply_mock.await_args.args
                self.assertEqual("副本面板", args[1])
                self.assertIn("操作：点按钮", args[2])
                self.assertNotIn("自动副本状态\n- ", args[2])
                self.assertEqual(panel["buttons"], reply_mock.await_args.kwargs["buttons"])
                send_mock.assert_not_awaited()

    def test_log_group_dungeon_help_is_short_and_read_only(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".副本帮助")

        with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
            handled = asyncio.run(control.handle_log_group_command(event))

        self.assertTrue(handled)
        reply_mock.assert_awaited_once()
        self.assertEqual("副本帮助", reply_mock.await_args.args[1])
        self.assertIn(".查询昆 / .查询虚 / .查询苍", reply_mock.await_args.args[2])
        self.assertIn(".副本cd", reply_mock.await_args.args[2])
        send_mock.assert_not_awaited()

    def test_log_group_dungeon_cd_overview_is_read_only(self):
        for raw_text in (".副本cd", ".副本CD", ".副本冷却", ".查询副本cd"):
            with self.subTest(raw_text=raw_text):
                event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=raw_text)

                with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                        patch.object(control, "format_log_group_replica_cd_overview", return_value="副本 CD 概览\n可开：虚1") as cd_mock, \
                        patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                        patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
                    handled = asyncio.run(control.handle_log_group_command(event))

                self.assertTrue(handled)
                cd_mock.assert_called_once_with()
                reply_mock.assert_awaited_once()
                self.assertEqual("副本 CD 概览", reply_mock.await_args.args[1])
                self.assertIn("可开：虚1", reply_mock.await_args.args[2])
                send_mock.assert_not_awaited()

    def test_log_group_message_contract_status_is_read_only(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".消息契约 concubine_voyage")

        with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                patch.object(control, "get_message_contract_status_text", return_value="契约缺口：1") as status_mock, \
                patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
            handled = asyncio.run(control.handle_log_group_command(event))

        self.assertTrue(handled)
        status_mock.assert_called_once_with(module="", family="concubine_voyage", reason="")
        reply_mock.assert_awaited_once()
        self.assertEqual("消息契约", reply_mock.await_args.args[1])
        send_mock.assert_not_awaited()

    def test_log_group_message_contract_status_can_filter_reason(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".消息契约 reply_context_no_identity")

        with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                patch.object(control, "get_message_contract_status_text", return_value="契约缺口：1") as status_mock, \
                patch.object(control, "_reply_log_group_card", new=AsyncMock()):
            handled = asyncio.run(control.handle_log_group_command(event))

        self.assertTrue(handled)
        status_mock.assert_called_once_with(module="", family="", reason="reply_context_no_identity")

    def test_log_group_message_box_shadow_reports_read_only_alignment(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".消息盒子shadow 20")
        provider_snapshot = control._message_box_shadow_payload_provider

        def fake_provider(**kwargs):
            self.assertTrue(kwargs["include_edits"])
            self.assertEqual(20, kwargs["limit"])
            return {
                "schema": "xiuxian.message_box.shadow.v1",
                "facts": [
                    {
                        "event_type": "message",
                        "chat_id": -1001680975844,
                        "msg_id": 10140776,
                        "sender_id": 8325841058,
                        "raw_text": "裂缝深处法则乱流渐息",
                        "identity_id": 3504367852,
                        "family": "explore_rift",
                        "reply_to_msg_id": 10140774,
                    }
                ],
            }

        try:
            control.register_message_box_shadow_payload_provider(fake_provider)
            with tempfile.TemporaryDirectory() as tmpdir:
                shadow_path = Path(tmpdir) / "latest.json"
                with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                        patch.object(control, "MESSAGE_BOX_SHADOW_LATEST_FILE", shadow_path), \
                        patch.object(control.passive_event_ledger, "iter_passive_events", return_value=[]), \
                        patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                        patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
                    handled = asyncio.run(control.handle_log_group_command(event))
        finally:
            control.register_message_box_shadow_payload_provider(provider_snapshot)

        self.assertTrue(handled)
        reply_mock.assert_awaited_once()
        self.assertEqual("消息盒子 shadow", reply_mock.await_args.args[1])
        body = reply_mock.await_args.args[2]
        self.assertIn("只读", body)
        self.assertIn("缺失 ledger 证据：1", body)
        self.assertIn("explore_rift identity=3504367852 msg=10140776", body)
        send_mock.assert_not_awaited()

    def test_log_group_message_box_shadow_reports_missing_provider(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".消息盒子shadow")
        provider_snapshot = control._message_box_shadow_payload_provider

        try:
            control.register_message_box_shadow_payload_provider(None)
            with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                    patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
                handled = asyncio.run(control.handle_log_group_command(event))
        finally:
            control.register_message_box_shadow_payload_provider(provider_snapshot)

        self.assertTrue(handled)
        self.assertIn("未注册 shadow provider", reply_mock.await_args.args[2])

    def test_log_group_message_box_shadow_provider_failure_is_reported(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".消息盒子shadow")
        provider_snapshot = control._message_box_shadow_payload_provider

        def broken_provider(**_kwargs):
            raise RuntimeError("shadow unavailable")

        try:
            control.register_message_box_shadow_payload_provider(broken_provider)
            with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                    patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                    patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
                handled = asyncio.run(control.handle_log_group_command(event))
        finally:
            control.register_message_box_shadow_payload_provider(provider_snapshot)

        self.assertTrue(handled)
        self.assertIn("导出失败：shadow unavailable", reply_mock.await_args.args[2])
        send_mock.assert_not_awaited()

    def test_three_sect_manual_command_without_identity_only_replies_usage(self):
        event = SimpleNamespace()

        with patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                patch.object(control, "execute_tianxing_manual_action", new=AsyncMock()) as execute_mock:
            handled = asyncio.run(control._handle_three_sect_manual_command(event, ".天星查盘", None))

        self.assertTrue(handled)
        execute_mock.assert_not_awaited()
        reply_mock.assert_awaited_once()
        args = reply_mock.await_args.args
        self.assertIn("必须指定单个身份", args[2])

    def test_three_sect_manual_command_with_identity_dispatches_once(self):
        event = SimpleNamespace()

        with patch.object(control, "get_identity_enabled", return_value=True), \
                patch.object(control, "is_module_available", return_value=True), \
                patch.object(control, "get_identity_display_name", return_value="tx[2101]"), \
                patch.object(control, "execute_tianxing_manual_action", new=AsyncMock(return_value=(True, "ok", {"command": ".天机盘"}))) as execute_mock, \
                patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
            handled = asyncio.run(control._handle_three_sect_manual_command(event, ".天星查盘", 2101))

        self.assertTrue(handled)
        execute_mock.assert_awaited_once_with("查盘", "", send_as_id=2101)
        reply_mock.assert_awaited_once()

    def test_yinluo_daily_sacrifice_manual_aliases_dispatch(self):
        event = SimpleNamespace()

        for raw_text, expected_action in (
            (".阴罗献祭", "献祭"),
            (".阴罗每日献祭", "每日献祭"),
        ):
            with self.subTest(raw_text=raw_text), \
                    patch.object(control, "get_identity_enabled", return_value=True), \
                    patch.object(control, "is_module_available", return_value=True), \
                    patch.object(control, "get_identity_display_name", return_value="yl[3101]"), \
                    patch.object(control, "execute_yinluo_manual_action", new=AsyncMock(return_value=(True, "ok", {"command": ".每日献祭"}))) as execute_mock, \
                    patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
                handled = asyncio.run(control._handle_three_sect_manual_command(event, raw_text, 3101))

            self.assertTrue(handled)
            execute_mock.assert_awaited_once_with(expected_action, "", send_as_id=3101)
            reply_mock.assert_awaited_once()

    def test_ui_allows_yinluo_daily_sacrifice_action(self):
        from model import ui

        with patch.object(ui, "get_identity_ids", return_value=[3101]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "get_available_module_names", return_value=["阴罗宗"]), \
                patch.object(ui, "get_identity_display_name", return_value="yl[3101]"), \
                patch.object(ui, "execute_yinluo_manual_action", new=AsyncMock(return_value=(True, "sent", {}))) as execute_mock:
            ok, message = asyncio.run(ui.ui_execute_yinluo_action(3101, "daily_sacrifice"))

        self.assertTrue(ok)
        self.assertIn("sent", message)
        execute_mock.assert_awaited_once_with("daily_sacrifice", "", send_as_id=3101)

    def test_log_group_three_sect_command_uses_identity_selector(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(3101)
            state_module.update_send_as_profile(3101, username="yinluo_manual", label="yinluo_manual", sect_name="阴罗宗")

            event = SimpleNamespace(
                chat_id=control.LOG_GROUP_ID,
                sender_id=123456,
                raw_text=".阴罗化煞 1000 @yinluo_manual",
            )
            with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                    patch.object(control, "is_module_available", return_value=True), \
                    patch.object(control, "execute_yinluo_manual_action", new=AsyncMock(return_value=(True, "ok", {"command": ".化功为煞 1000"}))) as execute_mock, \
                    patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
                handled = asyncio.run(control.handle_log_group_command(event))

            self.assertTrue(handled)
            execute_mock.assert_awaited_once_with("化煞", "1000", send_as_id=3101)
            reply_mock.assert_awaited_once()
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_tianxing_pause_command_uses_unique_prefix_identity_selector(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
            state_module.ensure_identity_registered(8659059191)
            state_module.update_send_as_profile(
                8659059191,
                username="WalterWA2000",
                label="wa2000",
                sect_name="天星宗",
                enabled=True,
            )
            with state_module.use_identity(8659059191):
                state_module.state["tianxing_enabled"] = True

            event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".天星暂停 @wa")

            with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                    patch.object(tianxing, "save_state"), \
                    patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
                handled = asyncio.run(control.handle_log_group_command(event))

            with state_module.use_identity(8659059191):
                observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])

            self.assertTrue(handled)
            self.assertLess(observed["automation_paused_until"], 0)
            self.assertEqual("paused", observed["auto_last_action"])
            self.assertIn("已暂停", reply_mock.await_args.args[2])

            event.raw_text = ".天星恢复 @wa"
            with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                    patch.object(tianxing, "save_state"), \
                    patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
                handled = asyncio.run(control.handle_log_group_command(event))

            with state_module.use_identity(8659059191):
                observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])

            self.assertTrue(handled)
            self.assertEqual(0, observed["automation_paused_until"])
            self.assertEqual("resumed", observed["auto_last_action"])
            self.assertIn("已恢复", reply_mock.await_args.args[2])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_tianxing_pause_command_rejects_ambiguous_prefix_selector(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
            for identity_id, label in ((1001, "wa2000"), (1002, "wafang")):
                state_module.ensure_identity_registered(identity_id)
                state_module.update_send_as_profile(
                    identity_id,
                    username=label,
                    label=label,
                    sect_name="天星宗",
                    enabled=True,
                )
                with state_module.use_identity(identity_id):
                    state_module.state["tianxing_enabled"] = True

            event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".天星暂停 @wa")

            with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                    patch.object(tianxing, "save_state") as save_mock, \
                    patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock:
                handled = asyncio.run(control.handle_log_group_command(event))

            self.assertTrue(handled)
            save_mock.assert_not_called()
            self.assertIn("匹配多个身份", reply_mock.await_args.args[2])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_xutian_followup_manual_without_identity_only_replies_usage(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".选择道路 火", id=9101)

        with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                patch.object(control, "send_game_command", new=AsyncMock()) as send_mock:
            handled = asyncio.run(control.handle_log_group_command(event))

        self.assertTrue(handled)
        send_mock.assert_not_awaited()
        reply_mock.assert_awaited_once()
        self.assertIn("必须指定单个身份", reply_mock.await_args.args[2])

    def test_xutian_followup_manual_uses_identity_selector(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
            state_module.ensure_identity_registered(3101)
            state_module.update_send_as_profile(3101, username="leader", label="leader", enabled=True)
            event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=123456, raw_text=".后殿阵策 卦 @leader", id=9102)

            with patch.object(control, "ADMIN_IDS", frozenset({123456})), \
                    patch.object(control, "_reply_log_group_card", new=AsyncMock()) as reply_mock, \
                    patch.object(control, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=778))) as send_mock:
                handled = asyncio.run(control.handle_log_group_command(event))

            self.assertTrue(handled)
            send_mock.assert_awaited_once()
            send_args = send_mock.await_args
            self.assertEqual(".后殿阵策 卦", send_args.args[0])
            self.assertFalse(send_args.kwargs["track"])
            self.assertEqual(3101, send_args.kwargs["send_as_id"])
            self.assertEqual("urgent_reactive", send_args.kwargs["priority"])
            self.assertEqual("自动副本", send_args.kwargs["source_module"])
            self.assertEqual("xutian_followup", send_args.kwargs["chain_id"])
            self.assertEqual("keep", send_args.kwargs["delete_policy"])
            reply_mock.assert_awaited_once()
            self.assertIn("结果：已发送", reply_mock.await_args.args[2])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

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

    def test_runtime_health_formatter_is_read_only_and_surfaces_live_state(self):
        @contextmanager
        def fake_use_identity(_identity_id):
            yield

        fake_state = {
            "pending_tasks": {
                9: {"cmd": ".小世界", "sent_at": 1000, "retry": 1, "max_retry": 1},
            },
            "wild_training_enabled": True,
            "wild_training_last_error": "回复超时，准备补发一次",
            "small_world_enabled": True,
            "small_world_phase": "calibration_wait",
            "taiyi_enabled": False,
            "taiyi_last_error": "旧太一错误不应展示",
        }
        inbox = {
            "total": 3,
            "changed": 2,
            "skipped": 1,
            "attention_total": 1,
            "attention_by_class": {"handler_gap": 1},
            "attention_by_reason": {"unhandled_routed_reply": 1},
            "modules": {"small_world": 2},
            "skip_reasons": {"no_identity": 1},
            "recent": [
                {
                    "module": "small_world",
                    "identity_id": 8659,
                    "source_message_id": 99,
                    "route_source": "message:passive_match",
                    "summary": "小世界面板",
                }
            ],
        }

        with patch.object(control, "time") as time_mock, \
                patch.object(control, "get_identity_ids", return_value=[8659]), \
                patch.object(control, "get_identity_enabled", return_value=True), \
                patch.object(control, "get_identity_display_name", return_value="wa2000"), \
                patch.object(control, "use_identity", fake_use_identity), \
                patch.object(control, "state", fake_state), \
                patch.object(control, "get_global_enabled", return_value=True), \
                patch.object(control, "get_game_send_queue_snapshot", return_value=[{"identity_name": "wa2000", "cmd": ".引道 水", "priority": "chain", "status": "waiting", "ready_in_sec": 2}]), \
                patch.object(control, "get_low_priority_audit_pending_counts", return_value=(4, 2)), \
                patch.object(control, "get_passive_inbox_snapshot", return_value=inbox), \
                patch.object(control, "_load_health_observer_snapshot", return_value=None):
            time_mock.time.return_value = 1060
            text = control._format_runtime_health_text()

        self.assertIn("运行健康摘要", text)
        self.assertIn("只读", text)
        self.assertIn("不触发游戏命令", text)
        self.assertIn("游戏 pending: 1", text)
        self.assertIn(".小世界 60s retry=1/1", text)
        self.assertIn("小世界=calibration_wait", text)
        self.assertIn("野外历练", text)
        self.assertNotIn("旧太一错误不应展示", text)
        self.assertIn("消息盒子: total=3 changed=2 skipped=1 attention=1", text)
        self.assertIn("待关注分类: handler_gap:1", text)
        self.assertIn("待关注原因: unhandled_routed_reply:1", text)
        self.assertIn("msg=99", text)

    def test_runtime_health_detail_surfaces_observer_pack(self):
        snapshot = {
            "ts": "2026-06-29 12:00:00",
            "status": "warn",
            "health": {
                "score": 82,
                "level": "warn",
                "risk_reasons": [{"severity": "warn", "message": "module send density: 世界Boss x22/15m"}],
            },
            "business": {
                "message_state": {
                    "sent_count": 22,
                    "window_sec": 900,
                    "repeated_command_samples": [{"identity_id": 301, "command": ".世界boss 战况", "count": 5}],
                },
                "db_state": {
                    "pending_total": 0,
                    "module_summary": [
                        {
                            "identity_id": 301,
                            "username": "baji",
                            "module": "world_boss",
                            "module_label": "世界Boss",
                            "status": "warn",
                            "details": ["动作:战况", "错误:安全锁拦截"],
                        },
                        {
                            "identity_id": 302,
                            "username": "wa2000",
                            "module": "fishing",
                            "module_label": "钓鱼",
                            "status": "active",
                            "details": ["回复:msg=9"],
                        },
                    ],
                },
            },
            "evidence_refs": [
                {"kind": "message_log", "path": "/tmp/messages.log", "sent_count": 22, "last_sent_ts": "2026-06-29 11:59:59"},
                {"kind": "repeat_sample", "identity_id": 301, "command": ".世界boss 战况", "count": 5},
            ],
        }

        with patch.object(control, "_load_health_observer_snapshot", return_value=snapshot):
            text = control._format_runtime_health_detail_text()

        self.assertIn("运行健康详情", text)
        self.assertIn("score=82", text)
        self.assertIn("世界Boss", text)
        self.assertIn(".世界boss 战况 x5", text)
        self.assertIn("/tmp/messages.log", text)

    def test_auto_dungeon_status_text_is_not_unknown(self):
        text = control.get_single_module_status_text("自动副本")

        self.assertIn("自动副本状态", text)
        self.assertIn("副本群轻量指令", text)
        self.assertIn("主线拉人群", text)
        self.assertIn("已停用", text)
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
