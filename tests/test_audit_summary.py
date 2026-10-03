import asyncio
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telethon.extensions import html as telegram_html
from telethon.tl.types import MessageEntityMention

from model.audit_messages import text_units
from model.audit_summary import confirmed_summary_kind, format_grouped_summary, routine_bucket_key
from model.audit_summary_store import AuditSummaryStore
from model import runtime
from model.features import cave_treasure_runtime as cave


@pytest.mark.parametrize("kind", ["deep_retreat", "yuanying"])
@pytest.mark.parametrize("response", [
    None, {}, {"ok": "true"}, {"ok": True}, {"ok": False, "extra": {"sync": {"handled": True}}},
    {"ok": True, "extra": {"sync": {"handled": False}, "status_sync": {"handled": True}}},
    {"ok": True, "extra": {"sync": {"handled": True}, "outcome_unknown": True}},
    {"ok": True, "extra": {"sync": {"handled": True}, "action_skipped": True}},
])
def test_only_confirmed_results_can_be_grouped(kind, response):
    assert confirmed_summary_kind(kind, response) == ""


@pytest.mark.parametrize("key", ["sync", "status_sync"])
def test_confirmed_results_and_unknown_kind(key):
    result = {"ok": True, "extra": {key: {"handled": True}}}
    assert confirmed_summary_kind("yuanying", result) == "yuanying"
    assert confirmed_summary_kind("world_boss", result) == ""


def test_semantic_bucket_preserves_long_content_and_stable_identity():
    prefix = "x" * 1000
    assert routine_bucket_key("yuanying", 1, prefix + "a") != routine_bucket_key("yuanying", 1, prefix + "b")
    assert routine_bucket_key("yuanying", 1, "same") != routine_bucket_key("yuanying", 2, "same")


def test_summary_fixed_order_no_false_success_or_reward_accumulation():
    rows = [
        {"summary_kind": "yuanying", "identity_id": 1, "html": "@old 奖励 10", "count": 5, "last_at": 1},
        {"summary_kind": "deep_retreat", "identity_id": 2, "html": "已同步", "count": 1},
        {"summary_kind": "yuanying", "identity_id": 1, "html": "@new 奖励 20", "count": 1, "last_at": 2},
    ]
    before = deepcopy(rows)
    text = format_grouped_summary(rows, now_text="10:00")
    assert rows == before
    assert text.index("闭关：") < text.index("元婴：")
    assert "元婴：1 个身份，6 条记录" in text
    assert "@new" in text and "@old" not in text
    assert "成功" not in text and "奖励 30" not in text
    assert not any(isinstance(e, MessageEntityMention) for e in telegram_html.parse(text)[1])


def test_bounded_fair_details_and_escaped_html():
    rows = [{"summary_kind": "deep_retreat", "identity_id": i, "count": 1, "html": "😀" * 500} for i in range(100)]
    rows += [{"summary_kind": "yuanying", "identity_id": 777, "count": 1, "html": "safe &lt;b&gt;"}]
    output = format_grouped_summary(rows, now_text="12:00", max_details=20)
    plain, entities = telegram_html.parse(output)
    assert "101" not in plain  # Each module is counted separately.
    assert "元婴：1 个身份" in plain
    assert "元婴 /" in plain
    assert "safe <b>" in plain
    assert text_units(plain) < 3500
    assert any(getattr(e, "collapsed", False) for e in entities)


class RuntimeStructuredSummaryTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = AuditSummaryStore(Path(directory.name) / "audit.db", runtime._low_priority_audit_bucket,
                                       runtime._low_priority_audit_order, clock=lambda: 1000)
        self.patchers = [
            patch.object(runtime, "_audit_summary_store", self.store),
            patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", True),
            patch.object(runtime, "LOG_GROUP_DELIVERY_METRICS", False),
            patch.object(runtime, "_schedule_low_priority_audit_flush"),
            patch.object(runtime, "console_log"),
            patch.object(runtime, "get_send_as_label", side_effect=lambda identity: f"actor{identity}"),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)
        runtime._low_priority_audit_bucket.clear()
        runtime._low_priority_audit_order.clear()

    async def asyncTearDown(self):
        runtime._low_priority_audit_bucket.clear()
        runtime._low_priority_audit_order.clear()

    async def flush(self):
        self.store.next_at = 0
        return await runtime.flush_low_priority_audit_summary()

    async def test_24_identities_share_one_digest_without_claiming_batch_complete(self):
        sender = AsyncMock(return_value=True)
        with patch.object(runtime, "_send_log_group_message", sender):
            for identity in range(1, 25):
                await runtime.send_audit_log("元婴状态已确认", priority="normal", send_as_id=identity, summary_kind="yuanying")
            sender.assert_not_awaited()
            await self.flush()
        sender.assert_awaited_once()
        text = sender.call_args.args[0]
        assert "元婴：24 个身份，24 条记录" in text
        assert "全部完成" not in text and "tg://user" not in text
        assert runtime._audit_summary_interval() == 1800

    async def test_legacy_switch_keeps_existing_delivery(self):
        sender = AsyncMock(return_value=True)
        with patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", False), patch.object(runtime, "_send_log_group_message", sender):
            await runtime.send_audit_log("元婴已出窍", priority="normal", summary_kind="yuanying")
        sender.assert_awaited_once()
        assert not runtime._low_priority_audit_bucket

    async def test_high_buttons_and_unmigrated_warnings_are_not_delayed(self):
        sender = AsyncMock(return_value=True)
        with patch.object(runtime, "_send_log_group_message", sender):
            await runtime.send_audit_log("需要人工核实", priority="high", summary_kind="yuanying")
            await runtime.send_audit_log("请处理", priority="normal", summary_kind="yuanying", buttons=[["button"]])
            await runtime.send_audit_log("出窍结果未知", priority="normal")
            await runtime.send_audit_log("warning", priority="medium", summary_kind="unknown")
        assert sender.await_count == 4
        assert not runtime._low_priority_audit_bucket

    async def test_rename_does_not_split_bucket_and_long_differences_do(self):
        with patch.object(runtime, "get_send_as_label", return_value="old"):
            await runtime.send_audit_log("same", send_as_id=1, priority="normal", summary_kind="yuanying")
        with patch.object(runtime, "get_send_as_label", return_value="new"):
            await runtime.send_audit_log("same", send_as_id=1, priority="normal", summary_kind="yuanying")
        assert runtime.get_low_priority_audit_pending_counts() == (2, 1)
        row = next(iter(runtime._low_priority_audit_bucket.values()))
        assert "new" in row["html"]
        for suffix in ("a", "b"):
            await runtime.send_audit_log("x" * 500 + suffix, send_as_id=1, limit=10, summary_kind="yuanying")
        assert runtime.get_low_priority_audit_pending_counts() == (4, 3)

    async def test_unknown_delivery_does_not_requeue_over_newer_observation(self):
        with patch.object(runtime.time, "time", return_value=1000):
            await runtime.send_audit_log("same", send_as_id=1, summary_kind="yuanying")
        async def send_then_fail(*args, **kwargs):
            with patch.object(runtime.time, "time", return_value=2000), patch.object(runtime, "get_send_as_label", return_value="new"):
                await runtime.send_audit_log("same", send_as_id=1, summary_kind="yuanying")
            return False
        with patch.object(runtime, "_send_log_group_message", AsyncMock(side_effect=send_then_fail)):
            assert await self.flush() is False
        assert runtime.get_low_priority_audit_pending_counts() == (1, 1)
        assert len(self.store.held) == 1
        assert self.store.held[0]["count"] == 1
        row = next(iter(runtime._low_priority_audit_bucket.values()))
        assert row["first_at"] == 2000 and row["last_at"] == 2000
        assert "new" in row["html"]

    async def test_formatter_failure_restores_bucket(self):
        await runtime.send_audit_log("same", send_as_id=1, summary_kind="yuanying")
        with patch.object(runtime, "_format_low_priority_audit_summary", side_effect=ValueError("format")):
            assert await self.flush() is False
        assert runtime.get_low_priority_audit_pending_counts() == (1, 1)

    async def test_business_adapters_tag_only_confirmed_responses(self):
        sender = AsyncMock()
        good = {"ok": True, "extra": {"sync": {"handled": True}}}
        bad = {"ok": False, "extra": {"outcome_unknown": True}}
        with patch.object(cave, "send_audit_log", sender):
            await cave._audit_cave_yuanying("ok", 1, good)
            await cave._audit_cave_yuanying("unknown", 1, bad)
            await cave._audit_cave_retreat("ok", 2, good)
            await cave._audit_cave_retreat("unknown", 2, bad)
        assert [call.kwargs["summary_kind"] for call in sender.call_args_list] == ["yuanying", "", "deep_retreat", ""]


class NotificationDeliveryRuntimeTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for p in (patch.object(runtime, "LOG_GROUP_DELIVERY_METRICS", True),
                  patch.object(runtime, "LOG_SEND_MODE", "bot"),
                  patch.object(runtime, "LOG_BOT_TOKEN", "test"),
                  patch.object(runtime, "_LOG_BOT_BACKOFF_UNTIL", 0)):
            p.start()
            self.addCleanup(p.stop)

    async def test_bot_success_has_one_receipt_and_no_fallback(self):
        with patch.object(runtime, "emit_delivery_receipt") as receipt, \
                patch.object(runtime, "_send_log_group_via_bot", return_value=(True, "")), \
                patch.object(runtime, "_get_any_authed_client_with_account") as account:
            assert await runtime._send_log_group_message("body") is True
        account.assert_not_called()
        receipt.assert_called_once()
        assert receipt.call_args.kwargs["outcome"] == "confirmed"

    async def test_unknown_bot_and_confirmed_account_are_separate_attempts(self):
        account_client = MagicMock()
        with patch.object(runtime, "emit_delivery_receipt") as receipt, \
                patch.object(runtime, "_send_log_group_via_bot", side_effect=TimeoutError()), \
                patch.object(runtime, "_get_any_authed_client_with_account", return_value=(1, account_client)), \
                patch.object(runtime, "_run_account_rpc", AsyncMock()):
            assert await runtime._send_log_group_message("body") is True
        assert [(c.kwargs["transport"], c.kwargs["outcome"]) for c in receipt.call_args_list] == [("bot", "unknown"), ("account", "confirmed")]

    async def test_metrics_failure_cannot_trigger_an_extra_send(self):
        with patch.object(runtime, "emit_delivery_receipt", side_effect=RuntimeError()), \
                patch.object(runtime, "_send_log_group_via_bot", return_value=(True, "")), \
                patch.object(runtime, "_get_any_authed_client_with_account") as account:
            assert await runtime._send_log_group_message("body") is True
        account.assert_not_called()

    async def test_cancellation_is_not_swallowed_and_is_unknown(self):
        with patch.object(runtime, "emit_delivery_receipt") as receipt, \
                patch.object(runtime, "_send_chat_via_log_bot", side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                await runtime.send_log_bot_notification(-1, "body")
        assert receipt.call_args.kwargs["outcome"] == "unknown"

    async def test_disabled_metrics_and_unconfirmed_secondary_response(self):
        with patch.object(runtime, "emit_delivery_receipt") as receipt, \
                patch.object(runtime, "_send_chat_via_log_bot", return_value=(False, 'HTTP 403: {"ok":false,"error_code":403}')):
            assert await runtime.send_log_bot_notification(-1, "body") is False
            assert receipt.call_args.kwargs["outcome"] == "unconfirmed"
            receipt.reset_mock()
            with patch.object(runtime, "LOG_GROUP_DELIVERY_METRICS", False):
                assert await runtime.send_log_bot_notification(-1, "body") is False
            receipt.assert_not_called()

    async def test_returned_timeout_is_unknown_without_changing_account_fallback(self):
        account_client = MagicMock()
        with patch.object(runtime, "emit_delivery_receipt") as receipt, \
                patch.object(runtime, "_send_log_group_via_bot", return_value=(False, "timeout: read timed out")), \
                patch.object(runtime, "_get_any_authed_client_with_account", return_value=(1, account_client)), \
                patch.object(runtime, "_run_account_rpc", AsyncMock()) as account:
            assert await runtime._send_log_group_message("body") is True
        account.assert_awaited_once()
        assert [(c.kwargs["transport"], c.kwargs["outcome"]) for c in receipt.call_args_list] == [("bot", "unknown"), ("account", "confirmed")]

    async def test_requests_timeout_reaches_secondary_receipt_as_unknown(self):
        with patch.object(runtime, "emit_delivery_receipt") as receipt, \
                patch.object(runtime.requests, "post", side_effect=runtime.requests.exceptions.ReadTimeout("read timeout")):
            assert await runtime.send_log_bot_notification(-1, "body") is False
        assert receipt.call_args.kwargs["outcome"] == "unknown"

    async def test_classifier_failure_cannot_change_backoff_or_return_value(self):
        error = 'HTTP 429: {"ok":false,"error_code":429,"parameters":{"retry_after":60}}'
        with patch.object(runtime, "bot_delivery_outcome", side_effect=RuntimeError("metrics only")), \
                patch.object(runtime, "_send_chat_via_log_bot", return_value=(False, error)), \
                patch.object(runtime, "_mark_log_bot_backoff", return_value=60) as backoff:
            assert await runtime.send_log_bot_notification(-1, "body") is False
        backoff.assert_called_once_with(error)
