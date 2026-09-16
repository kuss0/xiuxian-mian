import asyncio
import copy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from model import action_guard, cultivation_accounting as cultivation
from model import persistence, runtime as transport, state as state_module
from model import yinluo_accounting as accounting
from model import yinluo_resource_book as books
from model.features import yinluo
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, CHAT, IDENTITY, bind, env, finalize, observe, panel, prepare,
    receipts as receipts_fixture, runtime,
)
from test_yinluo_completion_lifecycle import (
    CONSUME, READ, reload_state, restored_completion,
)
from yinluo_native_support import native_logs


receipts = receipts_fixture
BARRIERS = ["legacy_pending", "capacity", "receipt_conflict", "gap_capacity", "gap_command_conflict"]


def retain_barrier(owner, reason):
    value = owner[accounting.STATE_KEY]
    if reason.startswith("gap_"):
        value["book"]["gap"] = {"reason": reason.removeprefix("gap_"), "at": 111.0}
    else:
        value["hold"] = reason
    assert accounting.read_accounting(IDENTITY)[1] == ""
    assert persistence.save_state()


def pending_read_with_consumption(owner, reason):
    consuming = prepare(CONSUME)
    finalize(consuming, root=200)
    bind(consuming, root=200, at=110.5)
    retain_barrier(owner, reason)
    record = prepare(READ, now=112)
    finalize(record, at=112.5, detached=True)
    bind(record, at=112.5)
    return record, consuming


def assert_barrier_and_consumption(owner, before, consuming):
    assert owner[accounting.STATE_KEY]["hold"] == before[accounting.STATE_KEY]["hold"]
    assert owner[accounting.STATE_KEY]["book"]["gap"] == before[accounting.STATE_KEY]["book"]["gap"]
    assert accounting.current_operation(IDENTITY, consuming["op_id"]) == next(
        item for item in before[accounting.STATE_KEY]["operations"] if item["op_id"] == consuming["op_id"]
    )
    defaults = {"chain_id": "", "delete_policy": ""}
    assert {**defaults, **owner["pending_tasks"][CHAT, 200]} == {**defaults, **before["pending_tasks"][CHAT, 200]}
    assert owner["action_guard_sessions"]["yinluo_convert"] == before["action_guard_sessions"]["yinluo_convert"]
    assert owner[cultivation.STATE_KEY] == before[cultivation.STATE_KEY]
    assert accounting.admission_reason(IDENTITY, CONSUME)
    assert accounting.resource_balance(IDENTITY, "sha")["value"] is None


@pytest.mark.parametrize("barrier", BARRIERS)
def test_owned_panel_completes_only_its_read_under_retained_barriers(receipts, barrier):
    record, consuming = pending_read_with_consumption(receipts, barrier)
    before = copy.deepcopy(receipts)
    received = panel(sha=1800, msg_id=101, start=112, end=120)
    assert observe(received)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert accounting.reply_complete(received, now=800)
    assert (CHAT, 100) not in receipts["pending_tasks"]
    assert "yinluo_banner" not in receipts["action_guard_sessions"]
    assert_barrier_and_consumption(receipts, before, consuming)
    assert not accounting.admission_reason(IDENTITY, READ)
    restored = reload_state()
    assert (CHAT, 100) not in restored["pending_tasks"]
    assert_barrier_and_consumption(restored, before, consuming)


@pytest.mark.parametrize("barrier", BARRIERS)
@pytest.mark.parametrize("cold", [False, True])
def test_completed_read_cleanup_survives_a_later_resource_barrier(receipts, monkeypatch, barrier, cold):
    owner, record, _received = restored_completion(READ, cold=cold)
    action_guard.note_sent(READ, IDENTITY, 100, sent_at=110.5, chat_id=CHAT)
    retain_barrier(owner, barrier)
    before = copy.deepcopy(owner)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    assert yinluo.recover_yinluo_resources(IDENTITY, 800)
    assert not owner["pending_tasks"]
    assert "yinluo_banner" not in owner["action_guard_sessions"]
    assert owner[accounting.STATE_KEY] == before[accounting.STATE_KEY]
    assert owner["yinluo_observation"] == before["yinluo_observation"]
    assert accounting.current_operation(IDENTITY, record["op_id"], chat_id=CHAT, msg_id=100)["phase"] == "complete"
    assert not yinluo.recover_yinluo_resources(IDENTITY, 800)
    # Guard helpers use the existing dirty-state save, not the pending transaction.
    assert not reload_state()["pending_tasks"]
    yinluo.recover_yinluo_resources(IDENTITY, 800)
    assert "yinluo_banner" not in state_module.get_identity_state(IDENTITY)["action_guard_sessions"]


@pytest.mark.parametrize("barrier", BARRIERS)
def test_scheduler_expires_only_owned_reads_even_when_held_and_not_business_due(receipts, monkeypatch, barrier):
    record, consuming = pending_read_with_consumption(receipts, barrier)
    receipts["yinluo_observation"]["auto_next_time"] = 2000
    before = copy.deepcopy(receipts)
    sender = AsyncMock(side_effect=AssertionError("A retained barrier cannot trigger new automatic queries"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(800))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "read_expired"
    assert (CHAT, 100) not in receipts["pending_tasks"]
    assert receipts["yinluo_observation"]["auto_last_action"] == "resource_hold"
    assert_barrier_and_consumption(receipts, before, consuming)
    sender.assert_not_awaited()
    assert (CHAT, 100) not in reload_state()["pending_tasks"]


@pytest.mark.parametrize("barrier", BARRIERS)
def test_barrier_never_becomes_a_repeating_automatic_calibration_loop(receipts, monkeypatch, barrier):
    retain_barrier(receipts, barrier)
    sender = AsyncMock(side_effect=AssertionError("A fresh panel cannot heal an ownership or capacity hold"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    before = copy.deepcopy(receipts[accounting.STATE_KEY])
    with state_module.use_identity(IDENTITY):
        for now in (800, 1600, 5000):
            asyncio.run(yinluo.run_yinluo_scheduler(now))
    sender.assert_not_awaited()
    assert receipts[accounting.STATE_KEY] == before
    assert receipts["yinluo_observation"]["auto_last_action"] == "resource_hold"


@pytest.mark.parametrize("barrier", BARRIERS)
def test_retained_barrier_does_not_broaden_consuming_completion_cleanup(receipts, barrier):
    owner, _record, _received = restored_completion(CONSUME)
    action_guard.note_sent(CONSUME, IDENTITY, 100, sent_at=110.5, chat_id=CHAT)
    retain_barrier(owner, barrier)
    before = copy.deepcopy(owner)
    assert not yinluo.recover_yinluo_resources(IDENTITY, 800, entries=[])
    assert owner == before


@pytest.mark.parametrize("barrier", ["legacy_pending", "gap_capacity"])
@pytest.mark.parametrize("failure", ["sqlite", "false", "exception"])
def test_held_read_fact_and_cleanup_rollback_together(receipts, monkeypatch, barrier, failure):
    record, consuming = pending_read_with_consumption(receipts, barrier)
    owner = reload_state()
    before = copy.deepcopy(owner)
    received = panel(sha=1800, msg_id=101, start=112, end=120)
    connection = persistence.get_db_conn()
    with monkeypatch.context() as patch:
        if failure == "sqlite":
            connection.execute("CREATE TEMP TRIGGER fail_held_read BEFORE INSERT ON identity_runtime_state "
                               "BEGIN SELECT RAISE(ABORT, 'held read failure'); END")
        else:
            def fail_save():
                assert (CHAT, 100) not in owner["pending_tasks"]
                assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
                if failure == "exception":
                    raise RuntimeError("held read failure")
                return False

            patch.setattr(persistence, "save_state", fail_save)
        try:
            if failure == "exception":
                with pytest.raises(RuntimeError, match="held read failure"):
                    observe(received)
            else:
                assert not observe(received)
        finally:
            if failure == "sqlite":
                connection.execute("DROP TRIGGER fail_held_read")
        assert owner == before
    restored = reload_state()
    assert restored[accounting.STATE_KEY] == before[accounting.STATE_KEY]
    assert restored["pending_tasks"] == before["pending_tasks"]
    assert observe(received)
    assert (CHAT, 100) not in reload_state()["pending_tasks"]
    assert_barrier_and_consumption(restored, before, consuming)


@pytest.mark.parametrize("barrier", ["legacy_pending", "gap_command_conflict"])
@pytest.mark.parametrize("field,value", [
    ("op_id", "f" * 32), ("cmd", CONSUME), ("sent_at", 111),
    ("source_module", "foreign"), ("account_id", ACCOUNT + 1), ("send_as_id", IDENTITY + 1),
])
def test_held_read_cleanup_still_requires_exact_pending_ownership(receipts, barrier, field, value):
    owner, _record, _received = restored_completion(READ)
    retain_barrier(owner, barrier)
    owner["pending_tasks"][CHAT, 100][field] = value
    before = copy.deepcopy(owner)
    assert not accounting.clear_completed_pending(IDENTITY)
    assert owner == before


@pytest.mark.parametrize("barrier", ["receipt_conflict", "gap_capacity"])
@pytest.mark.parametrize("damage", ["digest", "index", "owner"])
def test_held_read_cleanup_rejects_corrupt_cold_evidence(receipts, barrier, damage):
    owner, _record, _received = restored_completion(READ, cold=True)
    retain_barrier(owner, barrier)
    connection = persistence.get_db_conn()
    if damage == "digest":
        connection.execute("UPDATE yinluo_archive_commands SET digest=? WHERE command_msg_id=100", ("f" * 64,))
    elif damage == "index":
        connection.execute("DELETE FROM yinluo_archive_messages WHERE command_msg_id=100")
    else:
        connection.execute("UPDATE yinluo_archive_commands SET account_id=? WHERE command_msg_id=100", (ACCOUNT + 1,))
    connection.commit()
    before = copy.deepcopy(owner)
    assert not yinluo.recover_yinluo_resources(IDENTITY, 800, entries=[])
    assert owner == before


@pytest.mark.parametrize("barrier", BARRIERS)
def test_active_read_caller_keeps_scheduler_exclusion_under_a_hold(receipts, monkeypatch, barrier):
    record, _consuming = pending_read_with_consumption(receipts, barrier)
    monkeypatch.setattr(yinluo, "_YINLUO_INFLIGHT", {(IDENTITY, record["op_id"]): (receipts, ACCOUNT)})
    before = copy.deepcopy(receipts)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(800))
    assert receipts == before


@pytest.mark.parametrize("barrier", ["", *BARRIERS])
def test_next_explicit_query_reaches_real_shared_admission_after_local_cleanup(receipts, monkeypatch, barrier):
    owner, _record, _received = restored_completion(READ)
    action_guard.note_sent(READ, IDENTITY, 100, sent_at=110.5, chat_id=CHAT)
    retain_barrier(owner, barrier)
    monkeypatch.setattr(yinluo.time, "time", lambda: 300.0)
    monkeypatch.setattr(transport, "_global_recovery_hold_until_for_priority", lambda *_args: 0)
    monkeypatch.setattr(transport, "_dungeon_quiet_blocks_send", AsyncMock(return_value=False))
    monkeypatch.setattr(transport, "is_account_offline", lambda *_args: False)
    monkeypatch.setattr(transport, "_account_target_group_blocks_send", AsyncMock(return_value=False))
    monkeypatch.setattr(transport, "_account_flood_wait_until", lambda *_args: 0)
    monkeypatch.setattr(transport, "is_identity_weak", lambda *_args: False)
    monkeypatch.setattr(transport, "_refresh_bot_health_timeout_before_send", lambda: None)
    monkeypatch.setattr(transport, "_bot_health_blocks_send", lambda *_args: False)
    monkeypatch.setattr(transport, "_run_game_command_pre_send_guards", AsyncMock(return_value=(True, "", "")))
    monkeypatch.setattr(transport, "send_audit_log", AsyncMock())
    calls = []

    async def send(command, **options):
        assert options["operation_check"]()
        allowed = await transport._game_send_allowed(
            command, send_as_id=IDENTITY, account_id=ACCOUNT, send_priority=transport.SEND_PRIORITY_NORMAL,
            send_intent={}, target_chat_id=CHAT, owner_check=options["operation_check"],
        )
        assert allowed, transport.get_last_game_send_block(IDENTITY, command)
        calls.append(command)
        record = accounting.current_operation(IDENTITY, options["op_id"])
        return finalize(record, root=300, at=300.5)

    monkeypatch.setattr(yinluo, "send_game_command", send)
    ok, reason, _plan = asyncio.run(yinluo.execute_yinluo_manual_action("banner", send_as_id=IDENTITY, now=300))
    assert ok, reason
    assert calls == [READ]
    assert (CHAT, 100) not in owner["pending_tasks"]
    assert accounting.admission_reason(IDENTITY, CONSUME)


@pytest.mark.parametrize("barrier", ["capacity", "gap_command_conflict"])
def test_hold_recheck_time_does_not_slide_on_every_scheduler_tick(receipts, monkeypatch, barrier):
    retain_barrier(receipts, barrier)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(800))
        before = copy.deepcopy(receipts)
        asyncio.run(yinluo.run_yinluo_scheduler(810))
    assert receipts == before


def test_real_capacity_overflow_cannot_turn_an_unstored_read_receipt_into_success(receipts, monkeypatch):
    record = prepare(READ)
    finalize(record, detached=True)
    bind(record, at=110.5)
    monkeypatch.setattr(books, "MAX_RECEIPTS", len(receipts[accounting.STATE_KEY]["book"]["receipts"]))
    received = panel(sha=1800, msg_id=101, start=110, end=120)
    assert observe(received)
    assert receipts[accounting.STATE_KEY]["book"]["gap"]["reason"] == "capacity"
    assert not accounting.reply_complete(received, now=800)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "sent"
    assert (CHAT, 100) in receipts["pending_tasks"]
    assert accounting.expire_unanswered_reads(IDENTITY, now=800, timeout=600)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "read_expired"
    assert (CHAT, 100) not in reload_state()["pending_tasks"]


def test_business_capacity_hold_created_during_read_completion_keeps_atomic_cleanup(receipts):
    points = copy.deepcopy(next(iter(receipts[accounting.STATE_KEY]["business"].values())))
    receipts[accounting.STATE_KEY]["business"] = {
        str(index): copy.deepcopy(points) for index in range(accounting.MAX_BUSINESS_POINTS)
    }
    record = prepare(READ)
    finalize(record, detached=True)
    bind(record, at=110.5)
    received = panel(msg_id=101, start=110, end=120)
    update = accounting.stage_event(received, now=800)
    observed = yinluo._project_yinluo_observation(update, received.text)
    assert update.value["hold"] == "capacity"
    assert accounting.commit_update(update, observations={IDENTITY: {"yinluo_observation": observed}})
    assert not reload_state()["pending_tasks"]
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert accounting.admission_reason(IDENTITY, CONSUME) == "capacity"


@pytest.mark.parametrize("barrier", ["legacy_pending", "gap_command_conflict"])
def test_withdrawn_read_cannot_be_cleaned_as_complete_under_a_barrier(receipts, barrier):
    owner, record, received = restored_completion(READ)
    retain_barrier(owner, barrier)
    withdrawn = replace(received, event_type="edit", text="", reply_context=None, root_msg_id=0, server_event_at=130)
    assert observe(withdrawn)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "unknown"
    before = copy.deepcopy(owner)
    assert not accounting.clear_completed_pending(IDENTITY)
    assert not accounting.reply_complete(received, now=800)
    assert owner == before


@pytest.mark.parametrize("barrier", ["capacity", "gap_command_conflict"])
def test_completed_query_cleanup_preserves_real_remote_cooldown(receipts, monkeypatch, barrier):
    owner, _record, _received = restored_completion(READ)
    action_guard.note_sent(READ, IDENTITY, 100, sent_at=110.5, chat_id=CHAT)
    assert action_guard.note_remote_block(READ, IDENTITY, block_until=900, kind="cooldown", now=111)
    retain_barrier(owner, barrier)
    monkeypatch.setattr(yinluo.time, "time", lambda: 300.0)
    assert yinluo.recover_yinluo_resources(IDENTITY, 300, entries=[])
    guard = owner["action_guard_sessions"]["yinluo_banner"]
    assert guard["remote_block_until"] == 900
    assert guard["remote_block_kind"] == "cooldown"
    assert guard["last_msg_id"] == 0
    assert not action_guard.before_send(READ, IDENTITY, now=300)[0]


@pytest.mark.parametrize("barrier", BARRIERS)
def test_held_scheduler_uses_due_local_log_evidence_before_expiring_a_read(receipts, monkeypatch, barrier):
    record, consuming = pending_read_with_consumption(receipts, barrier)
    receipts["yinluo_observation"]["auto_next_time"] = 0
    before = copy.deepcopy(receipts)
    received = panel(msg_id=101, start=112, end=120)
    reads = []

    def read_logs(now, **_options):
        reads.append(now)
        return native_logs(received)

    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", read_logs)
    sender = AsyncMock(side_effect=AssertionError("Local recovery must not dispatch a command"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(300))
        asyncio.run(yinluo.run_yinluo_scheduler(310))
    assert reads == [300]
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert (CHAT, 100) not in receipts["pending_tasks"]
    assert_barrier_and_consumption(receipts, before, consuming)
    sender.assert_not_awaited()
