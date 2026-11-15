import asyncio
import copy
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model.audit_summary_recovery import delivery_proof
from model.audit_summary_store import AuditSummaryStore


@pytest.fixture
def sample():
    message = "<b>运行摘要</b>\n<blockquote expandable><code>记录&amp;正文</code></blockquote>"
    batch = dict(id="batch", hash=hashlib.sha256(message.encode()).hexdigest(), at=1000., count=3, message=message)
    reply = SimpleNamespace(id=42, chat_id=-100, sender_id=99, message="运行摘要\n记录&正文",
                            date=datetime.fromtimestamp(1005, timezone.utc), fwd_from=None)
    return batch, reply


@pytest.mark.parametrize("mutation", [None, "sender", "chat", "forward", "late", "text", "duplicate", "hash"])
def test_receipt_requires_unique_own_sender_destination_full_text_and_time(sample, mutation):
    batch, message = sample
    rows = [message]
    if mutation == "sender":
        message.sender_id = 1
    elif mutation == "chat":
        message.chat_id = -101
    elif mutation == "forward":
        message.fwd_from = object()
    elif mutation == "late":
        message.date = datetime.fromtimestamp(2000, timezone.utc)
    elif mutation == "text":
        message.message += " changed"
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(message))
    elif mutation == "hash":
        batch["hash"] = "wrong"
    assert bool(delivery_proof(batch, rows, -100, {99})) is (mutation is None)


@pytest.mark.parametrize("failure", [None, "absent", "save", "concurrent"])
def test_receipt_reconciliation_survives_restart_without_sends_and_preserves_unknown(sample, tmp_path, monkeypatch, failure):
    async def run():
        batch, message = sample
        store = AuditSummaryStore(tmp_path / "summary.db", {}, [], clock=lambda: 1200)
        await store.resume(1800)
        store.held = [batch]
        store.retired_records = 178
        await store._save()
        async def find(item):
            assert not store.lock.locked()  # Reading Telegram must not block enqueue/flush.
            if failure == "concurrent":
                store.held = []
            return None if failure == "absent" else delivery_proof(item, [message], -100, {99})
        if failure == "save":
            monkeypatch.setattr(store, "_save", AsyncMock(return_value=False))
        count = await store.reconcile(find)
        assert count == int(failure is None)
        assert store.retired_records == 178
        if failure in {"save", "absent"}:
            assert store.held == [batch] and not store.receipts
        elif failure is None:
            assert not store.held and store.receipts[0]["message_id"] == 42
            restarted = AuditSummaryStore(store.path, {}, [], clock=lambda: 1201)
            await restarted.resume(1800)
            assert restarted.receipts == store.receipts and not restarted.held
            assert await restarted.reconcile(find) == 0
    asyncio.run(run())


def test_runtime_uses_existing_client_for_one_bounded_read_only_query(sample, tmp_path, monkeypatch):
    from model import runtime
    async def run():
        batch, message = sample
        store = AuditSummaryStore(tmp_path / "summary.db", {}, [], clock=lambda: 1200)
        await store.resume(1800)
        store.held = [batch]
        await store._save()
        tc = SimpleNamespace(get_messages=AsyncMock(return_value=[message]), send_message=AsyncMock())
        monkeypatch.setattr(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", True)
        monkeypatch.setattr(runtime, "LOG_GROUP_ID", -100)
        monkeypatch.setattr(runtime, "LOG_BOT_TOKEN", "99:fixture")
        monkeypatch.setattr(runtime, "get_all_clients", lambda: {1: tc})
        monkeypatch.setattr(runtime, "_get_any_authed_client_with_account", lambda: (1, tc))
        monkeypatch.setattr(runtime, "_get_audit_summary_store", lambda: store)
        ok, text = await runtime.reconcile_audit_summary()
        assert ok and "确认 1 批" in text and not store.held
        tc.get_messages.assert_awaited_once()
        assert tc.get_messages.await_args.args == (-100,)
        assert tc.get_messages.await_args.kwargs["limit"] == 40
        tc.send_message.assert_not_called()
    asyncio.run(run())
