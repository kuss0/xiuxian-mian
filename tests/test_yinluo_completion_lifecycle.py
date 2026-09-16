import asyncio
import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import action_guard, cultivation_accounting as cultivation
from model import persistence, state as state_module
from model import yinluo_accounting as accounting
from model.features import yinluo
from test_cultivation_accounting import snapshot
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, CHAT, CONVERT, IDENTITY, bind, env, event, finalize, observe, panel,
    prepare, receipts as receipts_fixture, runtime,
)
from test_yinluo_retention import retire, retirement_update


receipts = receipts_fixture
READ = ".\u6211\u7684\u9634\u7f57\u5e61"
CONSUME = ".\u5316\u529f\u4e3a\u715e 10000"


def reload_state():
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    return state_module.get_identity_state(IDENTITY)


def completed(command, *, detached=True):
    record = prepare(command)
    finalize(record, detached=detached)
    bind(record, at=110.5)
    pending = copy.deepcopy(state_module.get_identity_state(IDENTITY)["pending_tasks"][CHAT, 100])
    received = panel(msg_id=101, start=110, end=120) if command == READ else event(command, CONVERT)
    assert observe(received)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    return record, pending, received


def restored_completion(command, *, cold=False):
    record, pending, received = completed(command)
    if cold:
        assert snapshot(490000 if command == CONSUME else 500000, at=130, msg_id=300)
        assert observe(panel(sha=4000 if command == CONSUME else 2000, msg_id=500, start=132, end=133))
        retire()
        assert accounting.current_operation(IDENTITY, record["op_id"]) is None
    state_module.get_identity_state(IDENTITY)["pending_tasks"][CHAT, 100] = pending
    assert persistence.save_state()
    owner = reload_state()
    assert (CHAT, 100) in owner["pending_tasks"]
    assert accounting.command_complete(owner[accounting.STATE_KEY], CHAT, 100, command=command)
    return owner, record, received


@pytest.mark.parametrize("command", [READ, CONSUME])
@pytest.mark.parametrize("detached", [False, True])
def test_native_completion_persists_pending_cleanup_before_return(receipts, command, detached):
    record, _pending, _received = completed(command, detached=detached)
    assert not receipts["pending_tasks"]
    restored = reload_state()
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert not restored["pending_tasks"]


@pytest.mark.parametrize("command", [READ, CONSUME])
def test_native_fact_and_pending_cleanup_share_the_same_save(receipts, monkeypatch, command):
    record = prepare(command)
    finalize(record, detached=True)
    bind(record, at=110.5)
    save = persistence.save_state
    calls = []

    def check_save(**kwargs):
        calls.append(kwargs)
        assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
        assert (CHAT, 100) not in receipts["pending_tasks"]
        return save(**kwargs)

    monkeypatch.setattr(persistence, "save_state", check_save)
    received = panel(msg_id=101, start=110, end=120) if command == READ else event(command, CONVERT)
    assert observe(received)
    assert len(calls) == 1


@pytest.mark.parametrize("command", [READ, CONSUME])
@pytest.mark.parametrize("cold", [False, True])
def test_completed_pending_recovers_without_a_log_or_game_request(receipts, monkeypatch, command, cold):
    owner, record, _received = restored_completion(command, cold=cold)
    before = copy.deepcopy(owner)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    assert yinluo.recover_yinluo_resources(IDENTITY, 800)
    assert not owner["pending_tasks"]
    assert owner[accounting.STATE_KEY] == before[accounting.STATE_KEY]
    assert owner["yinluo_observation"] == before["yinluo_observation"]
    assert owner[cultivation.STATE_KEY] == before[cultivation.STATE_KEY]
    assert not reload_state()["pending_tasks"]
    assert not yinluo.recover_yinluo_resources(IDENTITY, 900)
    assert accounting.current_operation(IDENTITY, record["op_id"], chat_id=CHAT, msg_id=100)["phase"] == "complete"


def test_completed_read_pending_is_retired_before_a_new_manual_send(receipts, monkeypatch):
    owner, _record, _received = restored_completion(READ)
    monkeypatch.setattr(yinluo.time, "time", lambda: 720.0)
    calls = []

    async def send(command, **options):
        calls.append(command)
        assert options["operation_check"]()
        assert (CHAT, 100) not in owner["pending_tasks"]
        return SimpleNamespace(id=200, chat_id=CHAT, sent_at=720.5)

    monkeypatch.setattr(yinluo, "send_game_command", send)
    ok, reason, _plan = asyncio.run(yinluo.execute_yinluo_manual_action("banner", send_as_id=IDENTITY, now=720))
    assert ok, reason
    assert calls == [READ]


@pytest.mark.parametrize("cold", [False, True])
def test_known_completed_spending_timeout_clears_residual_pending(receipts, monkeypatch, cold):
    owner, _record, _received = restored_completion(CONSUME, cold=cold)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    with state_module.use_identity(IDENTITY):
        assert yinluo.reconcile_yinluo_timeout_from_pending(100, CONSUME, 110.5, now=800, chat_id=CHAT)
    assert not owner["pending_tasks"]
    assert not reload_state()["pending_tasks"]


@pytest.mark.parametrize("field,replacement", [
    ("op_id", "f" * 32), ("op_id", ""), ("source_module", "foreign"),
    ("cmd", ".foreign"), ("chat_id", CHAT - 1), ("sent_at", 110), ("sent_at", "110.5"),
    ("account_id", ACCOUNT + 1), ("send_as_id", IDENTITY + 1), ("identity_id", IDENTITY + 1),
])
def test_completion_cleanup_preserves_conflicting_pending_evidence(receipts, field, replacement):
    owner, _record, received = restored_completion(CONSUME)
    owner["pending_tasks"][CHAT, 100][field] = replacement
    before = copy.deepcopy(owner)
    assert not accounting.clear_completed_pending(IDENTITY)
    assert not observe(received)
    assert owner == before


@pytest.mark.parametrize("duplicate_key", [100, (CHAT, 101), "100"])
def test_completion_cleanup_rejects_ambiguous_operation_receipts(receipts, duplicate_key):
    owner, _record, _received = restored_completion(CONSUME)
    owner["pending_tasks"][duplicate_key] = copy.deepcopy(owner["pending_tasks"][CHAT, 100])
    before = copy.deepcopy(owner)
    assert not accounting.clear_completed_pending(IDENTITY)
    assert owner == before


@pytest.mark.parametrize("legacy", [False, True])
def test_completion_cleanup_preserves_other_chat_and_unknown_operation(receipts, legacy):
    owner, _record, _received = restored_completion(CONSUME)
    if legacy:
        owner["pending_tasks"][100] = owner["pending_tasks"].pop((CHAT, 100))
    source = owner["pending_tasks"][100 if legacy else (CHAT, 100)]
    other_chat = CHAT - 1
    foreign = dict(source, chat_id=other_chat)
    owner["pending_tasks"][other_chat, 100] = foreign
    newer = prepare(CONSUME, now=130)
    finalize(newer, root=200, at=130.5)
    bind(newer, root=200, at=130.5)
    # Preparation itself may retire a proved old row in the same transaction.
    owner["pending_tasks"][100 if legacy else (CHAT, 100)] = source
    pending_new = copy.deepcopy(owner["pending_tasks"][CHAT, 200])
    assert accounting.clear_completed_pending(IDENTITY)
    assert owner["pending_tasks"] == {(other_chat, 100): foreign, (CHAT, 200): pending_new}
    assert accounting.current_operation(IDENTITY, newer["op_id"])["phase"] == "sent"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 480000
    restored = reload_state()["pending_tasks"]
    assert restored.keys() == owner["pending_tasks"].keys()
    assert restored[other_chat, 100] == foreign
    assert restored[CHAT, 200] == {"chain_id": "", "delete_policy": "", **pending_new}


@pytest.mark.parametrize("exceptional", [False, True])
def test_fact_pending_cleanup_failure_rolls_back_memory_and_sqlite(receipts, monkeypatch, exceptional):
    record = prepare(CONSUME)
    finalize(record, detached=True)
    bind(record, at=110.5)
    owner = reload_state()
    before = copy.deepcopy(owner)
    received = event(CONSUME, CONVERT)
    connection = persistence.get_db_conn()
    with monkeypatch.context() as patch:
        if exceptional:
            def fail_save():
                assert (CHAT, 100) not in owner["pending_tasks"]
                assert owner[accounting.STATE_KEY]["operations"][0]["phase"] == "complete"
                raise RuntimeError("completion failure")

            patch.setattr(persistence, "save_state", fail_save)
            with pytest.raises(RuntimeError, match="completion failure"):
                observe(received)
        else:
            connection.execute("CREATE TEMP TRIGGER fail_completion BEFORE INSERT ON identity_runtime_state "
                               "BEGIN SELECT RAISE(ABORT, 'completion failure'); END")
            try:
                assert not observe(received)
            finally:
                connection.execute("DROP TRIGGER fail_completion")
        assert owner == before
    restored = reload_state()
    assert restored[accounting.STATE_KEY] == before[accounting.STATE_KEY]
    assert restored["pending_tasks"] == before["pending_tasks"]
    assert observe(received)
    assert not reload_state()["pending_tasks"]
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


def test_withdrawn_outcome_cannot_retire_a_restored_pending(receipts):
    owner, record, received = restored_completion(CONSUME)
    withdrawn = replace(received, event_type="edit", text="", reply_context=None, root_msg_id=0, server_event_at=130)
    assert observe(withdrawn)
    before = copy.deepcopy(owner)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "unknown"
    assert not accounting.clear_completed_pending(IDENTITY)
    assert owner == before
    assert (CHAT, 100) in reload_state()["pending_tasks"]


@pytest.mark.parametrize("damage", ["digest", "index", "owner"])
def test_cold_completion_cleanup_requires_valid_archive_evidence(receipts, damage):
    owner, _record, _received = restored_completion(CONSUME, cold=True)
    connection = persistence.get_db_conn()
    if damage == "digest":
        connection.execute("UPDATE yinluo_archive_commands SET digest=? WHERE command_msg_id=100", ("f" * 64,))
    elif damage == "index":
        connection.execute("DELETE FROM yinluo_archive_messages WHERE command_msg_id=100")
    else:
        connection.execute("UPDATE yinluo_archive_commands SET account_id=? WHERE command_msg_id=100", (ACCOUNT + 1,))
    connection.commit()
    before = copy.deepcopy(owner)
    assert not accounting.clear_completed_pending(IDENTITY)
    assert owner == before
    assert (CHAT, 100) in reload_state()["pending_tasks"]


@pytest.mark.parametrize("fail", [False, True])
def test_retirement_and_pending_cleanup_are_one_transaction(receipts, fail):
    record, pending, _received = completed(CONSUME)
    assert snapshot(490000, at=130, msg_id=300)
    assert observe(panel(sha=4000, msg_id=500, start=132, end=133))
    receipts["pending_tasks"][CHAT, 100] = pending
    assert persistence.save_state()
    owner = reload_state()
    before = copy.deepcopy(owner)
    update = retirement_update()
    assert any(change.command_msg_id == 100 for change in update.archive_changes)
    connection = persistence.get_db_conn()
    if fail:
        connection.execute("CREATE TEMP TRIGGER fail_retirement BEFORE INSERT ON identity_runtime_state "
                           "BEGIN SELECT RAISE(ABORT, 'retirement failure'); END")
    try:
        assert accounting.commit_update(update) is not fail
    finally:
        if fail:
            connection.execute("DROP TRIGGER fail_retirement")
    if fail:
        assert owner == before
        assert reload_state()["pending_tasks"] == before["pending_tasks"]
        assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
        assert connection.execute("SELECT COUNT(*) FROM yinluo_archive_commands WHERE command_msg_id=100").fetchone()[0] == 0
    else:
        assert not owner["pending_tasks"]
        assert not reload_state()["pending_tasks"]
        assert accounting.current_operation(IDENTITY, record["op_id"]) is None
        assert accounting.current_operation(IDENTITY, record["op_id"], chat_id=CHAT, msg_id=100)["phase"] == "complete"


def test_already_clean_completion_needs_no_additional_save(receipts, monkeypatch):
    _record, _pending, received = completed(CONSUME)
    before = copy.deepcopy(receipts)
    save = Mock(side_effect=AssertionError("No new fact or cleanup to commit"))
    monkeypatch.setattr(persistence, "save_state", save)
    assert not accounting.clear_completed_pending(IDENTITY)
    assert not observe(received)
    assert receipts == before
    save.assert_not_called()


def test_scheduler_reconciles_completed_reads_even_before_next_action_is_due(receipts, monkeypatch):
    owner, _record, _received = restored_completion(READ)
    owner["yinluo_observation"]["auto_next_time"] = 2000
    for action in yinluo.YINLUO_AUTO_ACTION_KEYS:
        owner["yinluo_observation"]["auto_config"][action] = False
    before = copy.deepcopy(owner["yinluo_observation"])
    sender = AsyncMock(side_effect=AssertionError("Not time for another business action"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(800))
    assert not owner["pending_tasks"]
    assert owner["yinluo_observation"] == before
    sender.assert_not_awaited()


@pytest.mark.parametrize("cold", [False, True])
def test_native_completion_reconciles_a_guard_without_a_remaining_pending_row(receipts, monkeypatch, cold):
    owner, _record, _received = restored_completion(CONSUME, cold=cold)
    owner["pending_tasks"] = {}
    action_guard.note_sent(CONSUME, IDENTITY, 100, sent_at=110.5, chat_id=CHAT)
    assert persistence.save_state()
    owner = reload_state()
    assert "yinluo_convert" in owner["action_guard_sessions"]
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    assert yinluo.recover_yinluo_resources(IDENTITY, 800)
    assert "yinluo_convert" not in owner["action_guard_sessions"]
    assert not owner["pending_tasks"]
    assert not yinluo.recover_yinluo_resources(IDENTITY, 800)


@pytest.mark.parametrize("field,replacement", [
    ("last_account_id", ACCOUNT + 1), ("last_chat_id", CHAT - 1),
    ("last_msg_id", "100"), ("last_command", CONSUME.replace("10000", "20000")),
    ("last_sent_at", 110), ("last_sent_at", None), ("last_sent_at", float("nan")), ("last_sent_at", "110.5"),
])
def test_guard_reconciliation_requires_exact_completed_command_ownership(receipts, monkeypatch, field, replacement):
    owner, _record, _received = restored_completion(CONSUME)
    owner["pending_tasks"] = {}
    action_guard.note_sent(CONSUME, IDENTITY, 100, sent_at=110.5, chat_id=CHAT)
    owner["action_guard_sessions"]["yinluo_convert"][field] = replacement
    before = copy.deepcopy(owner)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    assert not yinluo.recover_yinluo_resources(IDENTITY, 800)
    assert owner == before


def test_completed_guard_keeps_a_real_remote_cooldown(receipts, monkeypatch):
    owner, _record, _received = restored_completion(CONSUME, cold=True)
    owner["pending_tasks"] = {}
    action_guard.note_sent(CONSUME, IDENTITY, 100, sent_at=110.5, chat_id=CHAT)
    assert action_guard.note_remote_block(CONSUME, IDENTITY, block_until=900, kind="cooldown", now=111)
    monkeypatch.setattr(yinluo.time, "time", lambda: 800.0)
    assert yinluo.recover_yinluo_resources(IDENTITY, 800, entries=[])
    guard = owner["action_guard_sessions"]["yinluo_convert"]
    assert guard["remote_block_until"] == 900
    assert guard["remote_block_kind"] == "cooldown"
    assert guard["last_msg_id"] == 0
    assert not action_guard.before_send(CONSUME, IDENTITY, now=800)[0]


@pytest.mark.parametrize("op_id", ["manual", "", "f" * 32])
def test_manual_completion_requires_native_interval_not_unowned_script_uuid(receipts, op_id):
    finalize({"command": CONSUME, "source_module": "\u9634\u7f57\u5b97", "op_id": op_id, "started_at": 110}, detached=True)
    received = event(CONSUME, CONVERT)
    assert observe(received)
    assert not receipts[accounting.STATE_KEY]["operations"]
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
    restored = reload_state()
    assert bool(restored["pending_tasks"]) == (op_id == "f" * 32)


def test_consumption_ack_without_terminal_outcome_keeps_pending_and_guard(receipts):
    command = ".\u53ec\u5524\u9b54\u5f71"
    record = prepare(command)
    finalize(record, detached=True)
    bind(record, at=110.5)
    assert observe(event(command, "\u4f60\u6d88\u8017\u4e86 5000 \u70b9\u4fee\u4e3a\uff0c\u53ec\u5524\u9b54\u57df\u7684\u6295\u5f71\u3002"))
    before = copy.deepcopy(receipts)
    assert not yinluo.recover_yinluo_resources(IDENTITY, 800, entries=[])
    assert receipts == before
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "sent"
    assert (CHAT, 100) in reload_state()["pending_tasks"]


@pytest.mark.parametrize("exceptional", [False, True])
def test_local_completion_recovery_failure_retains_durable_pending(receipts, monkeypatch, exceptional):
    owner, _record, _received = restored_completion(CONSUME, cold=True)
    before = copy.deepcopy(owner)
    with monkeypatch.context() as patch:
        def fail_save():
            assert not owner["pending_tasks"]
            if exceptional:
                raise RuntimeError("cleanup failure")
            return False

        patch.setattr(persistence, "save_state", fail_save)
        if exceptional:
            with pytest.raises(RuntimeError, match="cleanup failure"):
                accounting.clear_completed_pending(IDENTITY)
        else:
            assert not accounting.clear_completed_pending(IDENTITY)
        assert owner == before
    assert reload_state()["pending_tasks"] == before["pending_tasks"]
    assert accounting.clear_completed_pending(IDENTITY)
    assert not reload_state()["pending_tasks"]
