"""Routine group noise is batched without hiding alerts or replaying unknown sends."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from model import runtime
from model.audit_summary_store import AuditSummaryStore


class QuietNotificationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.now = [1000.0]
        self.store = AuditSummaryStore(Path(directory.name) / "summary.db", {}, [], clock=lambda: self.now[0])
        self.sender = AsyncMock(return_value=True)
        for item in (
            patch.object(runtime, "LOG_GROUP_QUIET_ROUTINE", True),
            patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", True),
            patch.object(runtime, "LOG_GROUP_LOW_PRIORITY_SUMMARY_INTERVAL_SEC", 3600),
            patch.object(runtime, "LOG_GROUP_LOW_PRIORITY_SUMMARY_MAX_DETAILS", 5),
            patch.object(runtime, "_audit_summary_store", self.store),
            patch.object(runtime, "_low_priority_audit_bucket", self.store.bucket),
            patch.object(runtime, "_low_priority_audit_order", self.store.order),
            patch.object(runtime, "_schedule_low_priority_audit_flush"),
            patch.object(runtime, "console_log"),
            patch.object(runtime, "_send_log_group_message", self.sender),
            patch.object(runtime, "ADMIN_IDS", frozenset({1})),
        ):
            item.start()
            self.addCleanup(item.stop)

    async def test_normal_success_and_wait_are_saved_then_sent_once_per_hour(self):
        for body in ("洞府寻宝已完成｜奖励：凝血草x13", "深度闭关进行中，完成后继续结算", "批次进度：已完成 2/6"):
            assert await runtime.send_audit_log(body, scope="global", priority="normal")
        self.sender.assert_not_awaited()
        assert sum(row["count"] for row in self.store.bucket.values()) == 3
        self.now[0] += 1800
        await runtime.flush_low_priority_audit_summary()
        self.sender.assert_not_awaited()
        self.now[0] += 1800
        await runtime.flush_low_priority_audit_summary()
        self.sender.assert_awaited_once()
        message = self.sender.await_args.args[0]
        assert "凝血草x13" in message and "<blockquote expandable>" in message
        assert "tg://user" not in message and not self.store.bucket

    async def test_failures_and_unknown_results_stay_immediate_even_if_marked_low(self):
        for body in ("请求失败：HTTP 502", "结果未知，未确认结算", "安全锁拦截", "Connection timeout", "野外负伤而归"):
            with self.subTest(body=body):
                self.sender.reset_mock()
                await runtime.send_audit_log(body, scope="global", priority="low")
                self.sender.assert_awaited_once()
                assert not self.store.bucket and body in self.sender.await_args.args[0]

    async def test_manual_action_and_explicit_high_keep_admin_attention(self):
        for body, priority in (("请手动重新登录", "normal"), ("关键资源告警", "high")):
            self.sender.reset_mock()
            await runtime.send_audit_log(body, scope="global", priority=priority)
            self.sender.assert_awaited_once()
            assert "tg://user?id=1" in self.sender.await_args.args[0] and not self.store.bucket

    async def test_buttons_never_wait_for_the_summary(self):
        buttons = [[{"text": "查看", "data": "fixture"}]]
        await runtime.send_audit_log("交互结果", scope="global", priority="low", buttons=buttons)
        self.sender.assert_awaited_once()
        assert self.sender.await_args.kwargs["buttons"] == buttons and not self.store.bucket

    async def test_routine_numbers_are_not_misread_as_http_errors(self):
        await runtime.send_audit_log("获得灵石 +500｜剩余 429", scope="global", priority="normal")
        self.sender.assert_not_awaited()
        assert len(self.store.bucket) == 1

    async def test_quiet_switch_off_preserves_existing_normal_delivery(self):
        with patch.object(runtime, "LOG_GROUP_QUIET_ROUTINE", False):
            await runtime.send_audit_log("普通结果", scope="global", priority="normal")
        self.sender.assert_awaited_once()
        assert not self.store.bucket

    async def test_missing_durable_summary_does_not_enable_new_grouping(self):
        with patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", False):
            await runtime.send_audit_log("普通结果", scope="global", priority="normal")
        self.sender.assert_awaited_once()

    async def test_unknown_summary_delivery_is_not_replayed(self):
        await runtime.send_audit_log("普通结果", scope="global", priority="normal")
        self.now[0] += 3600
        self.sender.return_value = False
        await runtime.flush_low_priority_audit_summary()
        assert len(self.store.held) == 1
        self.now[0] += 7200
        await runtime.flush_low_priority_audit_summary()
        self.sender.assert_awaited_once()
        assert self.sender.await_args.kwargs["allow_unknown_fallback"] is False
