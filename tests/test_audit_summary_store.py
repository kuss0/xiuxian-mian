import asyncio
import json
import sqlite3
import threading
from functools import wraps
from unittest.mock import AsyncMock, patch

import pytest

from model import runtime
from model.audit_summary import format_grouped_summary, routine_bucket_key
from model.audit_summary_store import AuditSummaryStore, COMPACTED_KEY, HELD_TTL, MAX_HELD, MAX_ROWS, PENDING_TTL


def run_async(test):
    @wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


@pytest.fixture
def setup_store(tmp_path):
    now = [1000.0]
    reports = []
    def make():
        return AuditSummaryStore(tmp_path / "summary.db", {}, [], clock=lambda: now[0], report=reports.append)
    return make, now, reports


async def enqueue(store, text="observation", identity=1):
    def add():
        key = routine_bucket_key("yuanying", identity, text)
        row = store.bucket.get(key)
        if row is None:
            row = dict(bucket_key=key, identity_id=identity, summary_kind="yuanying", count=0,
                       first_at=store.clock(), first_ts="10:00", last_ts="10:00", seq=len(store.order),
                       html=text, plain=text)
            store.bucket[key] = row
            store.order.append(key)
        row["count"] += 1
        row["last_at"] = store.clock()
    return await store.enqueue(add, 1800)


def render(rows):
    return format_grouped_summary(rows, now_text="10:00")


def records(store):
    return sum(row["count"] for row in store.bucket.values())


@run_async
async def test_oversized_row_is_counted_without_poisoning_the_checkpoint(setup_store):
    make, _, _ = setup_store
    store = make()
    assert await enqueue(store, text="a" * 50000)
    assert await enqueue(store, text="ordinary")
    assert records(store) == 2
    assert store.bucket[COMPACTED_KEY]["count"] == 1
    restarted = make()
    assert await restarted.resume(1800)
    assert restarted.bucket == store.bucket


@run_async
async def test_restart_loads_tuple_keys_and_does_not_send_immediately(setup_store):
    make, now, _ = setup_store
    first = make()
    assert await enqueue(first)
    now[0] += 3600
    restarted = make()
    assert await restarted.resume(1800)
    assert restarted.bucket == first.bucket
    sender = AsyncMock(return_value=True)
    assert not await restarted.flush(render, sender, 1800)
    sender.assert_not_awaited()
    now[0] += 1800
    assert await restarted.flush(render, sender, 1800)
    sender.assert_awaited_once()
    final = make()
    assert await final.resume(1800)
    assert not final.bucket and not final.held


@run_async
@pytest.mark.parametrize("outcome", [False, TimeoutError(), asyncio.CancelledError()])
async def test_ambiguous_delivery_survives_restart_without_replay(setup_store, outcome):
    make, now, _ = setup_store
    first = make()
    await enqueue(first)
    now[0] += 1800
    sender = AsyncMock(return_value=outcome if outcome is False else None,
                       side_effect=outcome if isinstance(outcome, BaseException) else None)
    if isinstance(outcome, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await first.flush(render, sender, 1800)
    else:
        assert not await first.flush(render, sender, 1800)
    assert not first.bucket and len(first.held) == 1
    restarted = make()
    await restarted.resume(1800)
    assert restarted.held == first.held
    now[0] += 1800
    assert await restarted.flush(render, sender, 1800)
    sender.assert_awaited_once()


@run_async
async def test_concurrent_enqueue_and_flush_have_one_transport_and_separate_batches(setup_store):
    make, now, _ = setup_store
    store = make()
    await enqueue(store)
    now[0] += 1800
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow_send(message):
        entered.set()
        await release.wait()
        return True
    sender = AsyncMock(side_effect=slow_send)
    task = asyncio.create_task(store.flush(render, sender, 1800))
    await entered.wait()
    try:
        assert await enqueue(store)
        assert not await store.flush(render, sender, 1800)
        assert records(store) == 1 and len(store.held) == 1
    finally:
        release.set()
        await task
    assert records(store) == 1 and not store.held
    now[0] += 1800
    assert await store.flush(render, sender, 1800)
    assert sender.await_count == 2


@run_async
async def test_formatter_failure_is_retryable_without_transport(setup_store):
    make, now, _ = setup_store
    store = make()
    await enqueue(store)
    now[0] += 1800
    def broken(rows):
        raise ValueError("bad formatting")
    sender = AsyncMock(return_value=True)
    assert not await store.flush(broken, sender, 1800)
    assert records(store) == 1 and not store.held
    assert not await store.flush(render, sender, 1800)
    sender.assert_not_awaited()
    now[0] += 1800
    assert await store.flush(render, sender, 1800)


@run_async
async def test_failed_pre_send_checkpoint_holds_delivery_and_restores_memory(setup_store):
    make, now, reports = setup_store
    store = make()
    await enqueue(store)
    now[0] += 1800
    sender = AsyncMock(return_value=True)
    with patch.object(store, "_disk", side_effect=OSError("disk")):
        assert not await store.flush(render, sender, 1800)
    assert records(store) == 1 and not store.held
    sender.assert_not_awaited()
    assert len(reports) == 1
    now[0] += 1800
    assert await store.flush(render, sender, 1800)


@run_async
async def test_failed_ack_checkpoint_never_replays_on_restart(setup_store):
    make, now, _ = setup_store
    store = make()
    await enqueue(store)
    now[0] += 1800
    async def send(message):
        store._disk = lambda *_: (_ for _ in ()).throw(OSError("ack failed"))
        return True
    assert await store.flush(render, send, 1800)
    assert store.last_error == "OSError"
    restarted = make()
    assert await restarted.resume(1800)
    assert not restarted.bucket and len(restarted.held) == 1


@run_async
async def test_capacity_and_ttl_compact_counts_instead_of_replaying_stale_details(setup_store):
    make, now, _ = setup_store
    store = make()
    for identity in range(MAX_ROWS + 7):
        assert await enqueue(store, identity=identity)
    assert len(store.bucket) == MAX_ROWS + 1
    assert store.bucket[COMPACTED_KEY]["count"] == 7
    now[0] += PENDING_TTL
    assert await store.resume(1800)
    assert list(store.bucket) == [COMPACTED_KEY]
    assert records(store) == MAX_ROWS + 7
    assert "压缩" in render(list(store.bucket.values()))
    restarted = make()
    await restarted.resume(1800)
    assert restarted.bucket == store.bucket


@run_async
async def test_held_retention_is_bounded_and_visible(setup_store):
    make, now, _ = setup_store
    store = make()
    await store.resume(1800)
    store.held = [dict(id=str(i), at=now[0], count=2, hash="a" * 64, message="result unknown")
                  for i in range(MAX_HELD + 3)]
    await store.resume(1800)
    assert len(store.held) == MAX_HELD and store.retired_records == 6
    now[0] += HELD_TTL
    await store.resume(1800)
    assert not store.held and store.retired_records == 2 * (MAX_HELD + 3)


@run_async
@pytest.mark.parametrize("payload", ["not-json", '{"version":99}', '{"version":1,"rows":{}}'])
async def test_corrupt_store_is_not_overwritten_and_only_holds_summary(setup_store, payload):
    make, now, reports = setup_store
    store = make()
    store._disk(payload)
    assert not await enqueue(store)
    now[0] += 1800
    sender = AsyncMock()
    assert not await store.flush(render, sender, 1800)
    sender.assert_not_awaited()
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT payload FROM summary_checkpoint").fetchone()[0] == payload
    assert records(store) == 1 and reports


@run_async
async def test_cancelled_disk_job_is_joined_before_unlocking(setup_store):
    make, _, _ = setup_store
    store = make()
    await store.resume(1800)
    started, release = threading.Event(), threading.Event()
    disk = store._disk
    def slow_disk(payload=None):
        started.set()
        assert release.wait(5)
        return disk(payload)
    with patch.object(store, "_disk", side_effect=slow_disk):
        task = asyncio.create_task(enqueue(store))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert store.lock.locked() and not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    restarted = make()
    await restarted.resume(1800)
    assert records(restarted) == 1


@run_async
async def test_cancel_before_transport_keeps_retryable_rows(setup_store):
    make, now, _ = setup_store
    store = make()
    await enqueue(store)
    now[0] += 1800
    sender = AsyncMock()
    with patch.object(store, "_io", side_effect=asyncio.CancelledError()):
        with pytest.raises(asyncio.CancelledError):
            await store.flush(render, sender, 1800)
    assert records(store) == 1 and not store.held
    sender.assert_not_awaited()


@run_async
async def test_runtime_startup_schedules_restored_bucket_without_new_event(setup_store):
    make, _, _ = setup_store
    old = make()
    await enqueue(old)
    restarted = make()
    with patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", True), \
            patch.object(runtime, "_audit_summary_store", restarted), \
            patch.object(runtime, "_low_priority_audit_bucket", restarted.bucket), \
            patch.object(runtime, "_low_priority_audit_flush_task", None), \
            patch.object(runtime, "_background_tasks", set()):
        await runtime.resume_audit_summary()
        task = runtime._low_priority_audit_flush_task
        assert task is not None and not task.done()
        assert await runtime.shutdown_background_tasks()
        assert task.cancelled()
        assert records(restarted) == 1


@run_async
async def test_disabled_startup_does_not_open_summary_database(setup_store):
    make, _, _ = setup_store
    store = make()
    with patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", False), \
            patch.object(runtime, "_get_audit_summary_store") as getter:
        await runtime.resume_audit_summary()
        getter.assert_not_called()
    assert not store.path.exists()


@run_async
async def test_faults_do_not_block_high_priority_or_recurse(setup_store):
    make, _, _ = setup_store
    store = make()
    with patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", True), \
            patch.object(runtime, "_audit_summary_store", store), \
            patch.object(runtime, "_queue_low_priority_audit") as queue, \
            patch.object(runtime, "console_log"), \
            patch.object(runtime, "_send_log_group_message", AsyncMock(return_value=True)) as sender, \
            patch.object(store, "_disk", side_effect=OSError("full")):
        assert not await runtime.send_audit_log("routine", priority="low")
        assert await runtime.send_audit_log("urgent", priority="high")
    queue.assert_called_once()
    sender.assert_awaited_once()


@run_async
async def test_invalid_checkpoint_types_do_not_partially_replace_memory(setup_store):
    make, _, _ = setup_store
    first = make()
    await enqueue(first)
    data = first._disk()
    data["rows"][0]["count"] = True
    first._disk(json.dumps(data))
    restarted = make()
    assert not await restarted.resume(1800)
    assert not restarted.bucket
