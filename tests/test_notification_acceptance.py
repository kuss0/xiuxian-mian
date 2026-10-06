"""Acceptance exercises the real log command and summary path, not Telegram."""

import asyncio
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from model import control, runtime, state as state_module
from model.audit_summary_store import AuditSummaryStore
from model.features import stargazer


class NotificationAcceptanceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.now = [1000.0]
        self.store = AuditSummaryStore(Path(directory.name) / "summary.db", {}, [], clock=lambda: self.now[0])
        self.sender = AsyncMock(return_value=True)
        self.reply = AsyncMock()
        for p in (
            patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", True),
            patch.object(runtime, "_audit_summary_store", self.store),
            patch.object(runtime, "_low_priority_audit_bucket", self.store.bucket),
            patch.object(runtime, "_low_priority_audit_order", self.store.order),
            patch.object(runtime, "_schedule_low_priority_audit_flush"),
            patch.object(runtime, "console_log"),
            patch.object(runtime, "_send_log_group_message", self.sender),
            patch.object(control, "ADMIN_IDS", frozenset({1})),
            patch.object(control, "_reply_log_group_card", self.reply),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def add(self):
        await runtime.send_audit_log("元婴状态已确认", scope="global", priority="normal", summary_kind="yuanying")

    async def finish_stargazer(self, *, failed=False):
        saved = copy.deepcopy(state_module._meta_state)
        identity_id = 990710001
        try:
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id), \
                    patch.object(stargazer, "send_audit_log", runtime.send_audit_log), \
                    patch.object(stargazer, "apply_storage_bag_item_deltas") as items, \
                    patch.object(stargazer, "save_state"), \
                    patch.object(stargazer.random, "uniform", return_value=0):
                result = {
                    "ok": not failed, "status": "failed" if failed else "wait",
                    "error": "HTTP 429" if failed else "",
                    "data": {"farm_state": {"max_wait": 60}, "action_counts": {"collect": 1},
                             "item_deltas": {"星辰精华": 2}},
                }
                assert await stargazer._finish_stargazer_miniapp_result(result, self.now[0])
                items.assert_called_once_with(identity_id, {"星辰精华": 2})
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(saved)

    async def test_stargazer_success_queues_then_joins_existing_summary(self):
        await self.finish_stargazer()
        self.sender.assert_not_awaited()
        assert self.store.path.exists()
        assert len(self.store.bucket) == 1
        assert "星辰精华x2" in next(iter(self.store.bucket.values()))["plain"]
        self.now[0] += 1800
        await runtime.flush_low_priority_audit_summary()
        self.sender.assert_awaited_once()
        assert "星辰精华x2" in self.sender.await_args.args[0]
        assert "<blockquote expandable>" in self.sender.await_args.args[0]
        assert not self.store.bucket and not self.store.held

    async def test_stargazer_failure_still_notifies_without_waiting_for_summary(self):
        await self.finish_stargazer(failed=True)
        self.sender.assert_awaited_once()
        assert "HTTP 429" in self.sender.await_args.args[0]
        assert not self.store.bucket

    async def manual_summary(self):
        event = SimpleNamespace(chat_id=control.LOG_GROUP_ID, sender_id=1, raw_text=".发送日志汇总")
        assert await control.handle_log_group_command(event)
        return self.reply.await_args.args[2]

    async def test_window_wait_is_not_reported_as_send_failure(self):
        await self.add()
        body = await self.manual_summary()
        self.sender.assert_not_awaited()
        assert "发送失败" not in body
        assert "等待" in body

    async def test_unknown_delivery_does_not_promise_automatic_retry(self):
        await self.add()
        self.now[0] += 1800
        self.sender.return_value = False
        body = await self.manual_summary()
        self.sender.assert_awaited_once()
        assert len(self.store.held) == 1
        assert "稍后会自动重试" not in body
        assert "待核查" in body

    async def test_held_only_state_is_not_presented_as_nothing_pending(self):
        await self.add()
        self.now[0] += 1800
        self.sender.return_value = False
        await runtime.flush_low_priority_audit_summary()
        body = await self.manual_summary()
        self.sender.assert_awaited_once()
        assert "待核查" in body

    async def test_successful_summary_command_still_confirms_delivery(self):
        await self.add()
        self.now[0] += 1800
        body = await self.manual_summary()
        self.sender.assert_awaited_once()
        assert "已发送" in body
        assert not self.store.bucket and not self.store.held

    async def test_high_priority_does_not_wait_on_summary_storage_lock(self):
        await self.store.lock.acquire()
        try:
            assert await asyncio.wait_for(runtime.send_audit_log("需人工处理", priority="high"), timeout=0.2)
        finally:
            self.store.lock.release()
        self.sender.assert_awaited_once()
        assert not self.store.path.exists()

    async def test_inflight_query_does_not_start_another_send(self):
        await self.add()
        self.now[0] += 1800
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(message, **kwargs):
            entered.set()
            await release.wait()
            return True
        self.sender.side_effect = send
        task = asyncio.create_task(runtime.flush_low_priority_audit_summary())
        await entered.wait()
        try:
            body = await self.manual_summary()
            assert "正在发送" in body
            assert "没有待" not in body
            self.sender.assert_awaited_once()
        finally:
            release.set()
            await task

    async def test_legacy_manual_failure_still_keeps_its_original_retry_policy(self):
        with patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", False):
            await runtime.send_audit_log("routine", priority="low")
            self.sender.return_value = False
            body = await self.manual_summary()
        assert "稍后会自动重试" in body
        assert len(self.store.bucket) == 1 and not self.store.held
        assert not self.store.path.exists()

    async def test_storage_failure_is_visible_without_claiming_delivery(self):
        with patch.object(self.store, "_disk", side_effect=OSError("full")):
            await self.add()
            self.now[0] += 1800
            body = await self.manual_summary()
        assert "存储或投递出现异常" in body
        assert "已发送" not in body
        self.sender.assert_not_awaited()
