import asyncio
import copy
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, runtime as transport, state as state_module
from model import yinluo_accounting as accounting
from model.features import yinluo
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, CHAT, IDENTITY, bind, env, finalize, observe, panel, prepare,
    receipts as receipts_fixture, runtime,
)


receipts = receipts_fixture
READ = ".\u6211\u7684\u9634\u7f57\u5e61"


def reload_state():
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    return state_module.get_identity_state(IDENTITY)


def detached_read():
    record = prepare(READ)
    finalize(record, detached=True)
    assert accounting.adopt_pending_receipt(IDENTITY, record["op_id"])
    return record


@pytest.mark.parametrize("already_expired", [False, True])
def test_expired_detached_read_clears_only_its_owned_pending_after_reload(receipts, already_expired):
    record = detached_read()
    other_chat = CHAT - 1
    receipts["pending_tasks"][other_chat, 100] = dict(receipts["pending_tasks"][CHAT, 100], chat_id=other_chat)
    if already_expired:
        receipts[accounting.STATE_KEY]["operations"][0]["phase"] = "read_expired"
    assert persistence.save_state()
    restored = reload_state()
    unrelated = copy.deepcopy(restored["pending_tasks"][other_chat, 100])
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    assert (CHAT, 100) not in restored["pending_tasks"]
    assert restored["pending_tasks"][other_chat, 100] == unrelated
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "read_expired"
    assert reload_state()["pending_tasks"] == restored["pending_tasks"]


def test_read_timeout_starts_at_receipt_not_queue_admission(receipts):
    record = prepare(READ)
    bind(record, at=300)
    before = copy.deepcopy(receipts)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=899, timeout=600)
    assert receipts == before
    assert accounting.expire_unanswered_reads(IDENTITY, now=900, timeout=600)


def test_expired_unbound_read_can_still_bind_its_original_late_transport(receipts):
    record = prepare(READ)
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    finalize(record, detached=True, at=712)
    assert accounting.adopt_pending_receipt(IDENTITY, record["op_id"])
    current = accounting.current_operation(IDENTITY, record["op_id"])
    assert current["phase"] == "sent" and current["msg_id"] == 100
    assert not accounting.expire_unanswered_reads(IDENTITY, now=1311, timeout=600)
    assert (CHAT, 100) in receipts["pending_tasks"]
    assert accounting.expire_unanswered_reads(IDENTITY, now=1312, timeout=600)
    assert (CHAT, 100) not in receipts["pending_tasks"]


@pytest.mark.parametrize("action,arg", [("banner", ""), ("convert", "10000")])
def test_scheduler_does_not_expire_or_invalidate_an_active_owned_caller(receipts, monkeypatch, action, arg):
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    calls = []

    async def send(_command, **options):
        calls.append(options["op_id"])
        assert len(calls) == 1
        assert options["operation_check"]()
        before = copy.deepcopy(receipts)
        with state_module.use_identity(IDENTITY):
            await yinluo.run_yinluo_scheduler(800)
        assert receipts == before
        assert options["operation_check"]()
        return finalize(receipts[accounting.STATE_KEY]["operations"][0])

    monkeypatch.setattr(yinluo, "send_game_command", send)
    ok, message, _plan = asyncio.run(yinluo.execute_yinluo_manual_action(action, arg, send_as_id=IDENTITY, now=110))
    assert ok, message
    assert len(calls) == 1


def test_expired_read_does_not_block_the_next_read_at_shared_admission(receipts, monkeypatch):
    detached_read()
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    monkeypatch.setattr(transport, "_global_recovery_hold_until_for_priority", lambda *_args: 0)
    monkeypatch.setattr(transport, "_dungeon_quiet_blocks_send", AsyncMock(return_value=False))
    monkeypatch.setattr(transport, "is_account_offline", lambda *_args: False)
    monkeypatch.setattr(transport, "_account_target_group_blocks_send", AsyncMock(return_value=False))
    monkeypatch.setattr(transport, "_account_flood_wait_until", lambda *_args: 0)
    monkeypatch.setattr(transport, "is_identity_weak", lambda *_args: False)
    monkeypatch.setattr(transport, "_refresh_bot_health_timeout_before_send", lambda: None)
    monkeypatch.setattr(transport, "_bot_health_blocks_send", lambda *_args: False)
    monkeypatch.setattr(transport, "_run_game_command_pre_send_guards", AsyncMock(return_value=(True, "", "")))
    monkeypatch.setattr(transport, "action_guard_before_send", lambda *_args, **_kwargs: (True, ""))
    assert asyncio.run(transport._game_send_allowed(
        READ, send_as_id=IDENTITY, account_id=ACCOUNT, send_priority=transport.SEND_PRIORITY_NORMAL,
        send_intent={}, target_chat_id=CHAT, owner_check=lambda: True,
    ))


@pytest.mark.parametrize("failure", ["sqlite", "false", "exception"])
def test_read_expiry_and_pending_cleanup_rollback_together(receipts, monkeypatch, failure):
    record = detached_read()
    assert persistence.save_state()
    owner = reload_state()
    before = copy.deepcopy(owner)
    connection = persistence.get_db_conn()
    with monkeypatch.context() as patch:
        if failure == "sqlite":
            connection.execute("CREATE TEMP TRIGGER fail_expiry BEFORE INSERT ON identity_runtime_state "
                               "BEGIN SELECT RAISE(ABORT, 'expiry failure'); END")
        else:
            def fail_save():
                assert (CHAT, 100) not in owner["pending_tasks"]
                assert owner[accounting.STATE_KEY]["operations"][0]["phase"] == "read_expired"
                if failure == "exception":
                    raise RuntimeError("expiry failure")
                return False

            patch.setattr(persistence, "save_state", fail_save)
        try:
            if failure == "exception":
                with pytest.raises(RuntimeError, match="expiry failure"):
                    accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
            else:
                assert not accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
        finally:
            if failure == "sqlite":
                connection.execute("DROP TRIGGER fail_expiry")
        assert owner == before
    restored = reload_state()
    assert restored[accounting.STATE_KEY] == before[accounting.STATE_KEY]
    assert restored["pending_tasks"] == before["pending_tasks"]
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "read_expired"
    assert not reload_state()["pending_tasks"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_prepare_cannot_prune_an_expired_read_with_unfinished_cleanup(receipts, monkeypatch, corrupt):
    record = detached_read()
    receipts[accounting.STATE_KEY]["operations"][0]["phase"] = "read_expired"
    if corrupt:
        receipts["pending_tasks"][CHAT, 100]["source_module"] = "foreign"
    assert persistence.save_state()
    before = copy.deepcopy(receipts)
    with monkeypatch.context() as patch:
        patch.setattr(persistence, "save_state", lambda: False)
        assert not accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    new, reason = accounting.prepare_operation(IDENTITY, READ, CHAT, 712, source_module=record["source_module"])
    assert new is None and reason == "yinluo_read_cleanup_pending"
    assert receipts == before
    assert reload_state()[accounting.STATE_KEY] == before[accounting.STATE_KEY]


@pytest.mark.parametrize("field,replacement", [
    ("op_id", "f" * 32), ("source_module", "foreign"), ("cmd", ".foreign"),
    ("chat_id", CHAT - 1), ("sent_at", 111), ("sent_at", "110.5"),
    ("account_id", ACCOUNT + 1), ("account_id", str(ACCOUNT)),
    ("send_as_id", IDENTITY + 1), ("identity_id", IDENTITY + 1),
])
def test_read_expiry_preserves_conflicting_pending_metadata(receipts, field, replacement):
    detached_read()
    receipts["pending_tasks"][CHAT, 100][field] = replacement
    before = copy.deepcopy(receipts)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    assert receipts == before


@pytest.mark.parametrize("duplicate_key", [100, (CHAT, 101), "100"])
def test_read_expiry_preserves_ambiguous_pending_receipts(receipts, duplicate_key):
    detached_read()
    receipts["pending_tasks"][duplicate_key] = copy.deepcopy(receipts["pending_tasks"][CHAT, 100])
    before = copy.deepcopy(receipts)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    assert receipts == before


def test_read_expiry_accepts_one_exact_legacy_integer_pending(receipts):
    detached_read()
    receipts["pending_tasks"][100] = receipts["pending_tasks"].pop((CHAT, 100))
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    assert not receipts["pending_tasks"]
    assert not reload_state()["pending_tasks"]


def test_expired_bound_receipt_is_idempotent_without_reopening_or_saving(receipts, monkeypatch):
    record = detached_read()
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    before = copy.deepcopy(receipts)
    save = Mock(side_effect=AssertionError("An expired duplicate cannot reopen or save"))
    monkeypatch.setattr(persistence, "save_state", save)
    assert accounting.record_transport(IDENTITY, record["op_id"], phase="sent", chat_id=CHAT, msg_id=100, sent_at=110.5)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=1000, timeout=600)
    assert receipts == before
    save.assert_not_called()


def test_expiration_does_not_adopt_or_change_an_active_callers_receipt(receipts):
    record = prepare(READ)
    finalize(record, detached=True)
    before = copy.deepcopy(receipts)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600, active_ops={record["op_id"]})
    assert receipts == before


def rpc_receipt(record):
    return {
        "send_as_id": IDENTITY, "account_id": ACCOUNT, "command": record["command"],
        "message": None, "detached": True, "started": True,
        "finalize_kwargs": {
            "send_as_id": IDENTITY, "game_group_id": CHAT, "send_started_at": record["started_at"],
            "send_intent": {"op_id": record["op_id"], "source_module": record["source_module"]},
        },
    }


@pytest.mark.parametrize("done", [False, True])
def test_registered_detached_rpc_blocks_expiration_until_callback_finishes(receipts, done):
    record = prepare(READ)

    async def scenario():
        future = asyncio.get_running_loop().create_future()
        if done:
            future.set_result(None)
        transport._GAME_SEND_TASKS[future] = rpc_receipt(record)
        try:
            before = copy.deepcopy(receipts)
            active = yinluo._active_yinluo_operations(IDENTITY)
            assert active == {record["op_id"]}
            assert not accounting.expire_unanswered_reads(IDENTITY, now=800, timeout=600, active_ops=active)
            with state_module.use_identity(IDENTITY):
                await yinluo.run_yinluo_scheduler(800)
            assert receipts == before
        finally:
            transport._GAME_SEND_TASKS.pop(future)
            if not future.done():
                future.cancel()
        assert accounting.expire_unanswered_reads(IDENTITY, now=800, timeout=600)

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["account", "identity", "command", "chat", "op_id", "module", "intent", "kwargs"])
def test_unrelated_or_malformed_rpc_does_not_claim_an_active_operation(receipts, mismatch):
    record = prepare(READ)
    receipt = rpc_receipt(record)
    if mismatch == "account":
        receipt["account_id"] = ACCOUNT + 1
    elif mismatch == "identity":
        receipt["finalize_kwargs"]["send_as_id"] = IDENTITY + 1
    elif mismatch == "command":
        receipt["command"] = ".foreign"
    elif mismatch == "chat":
        receipt["finalize_kwargs"]["game_group_id"] = CHAT - 1
    elif mismatch == "op_id":
        receipt["finalize_kwargs"]["send_intent"]["op_id"] = "f" * 32
    elif mismatch == "module":
        receipt["finalize_kwargs"]["send_intent"]["source_module"] = "foreign"
    elif mismatch == "intent":
        receipt["finalize_kwargs"]["send_intent"] = None
    else:
        receipt["finalize_kwargs"] = None
    transport._GAME_SEND_TASKS[object()] = receipt
    assert not yinluo._active_yinluo_operations(IDENTITY)


def test_late_native_read_adopts_expired_unbound_transport_before_cleanup(receipts):
    record = prepare(READ)
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    finalize(record, detached=True, at=712)
    received = panel(sha=3000, msg_id=101, start=110, end=715)
    assert observe(received)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert not receipts["pending_tasks"]
    before = copy.deepcopy(receipts[accounting.STATE_KEY])
    assert not observe(received)
    assert reload_state()[accounting.STATE_KEY] == before
    assert not observe(received)
    assert not state_module.get_identity_state(IDENTITY)["pending_tasks"]


@pytest.mark.parametrize("phase", ["prepared", "sent", "unknown"])
def test_read_expiry_never_releases_a_consuming_operation(receipts, phase):
    consuming = prepare(".\u5316\u529f\u4e3a\u715e 10000")
    finalize(consuming, detached=True)
    if phase == "sent":
        bind(consuming, at=110.5)
    elif phase == "unknown":
        assert accounting.record_transport(IDENTITY, consuming["op_id"], phase="unknown")
    before = copy.deepcopy(receipts["pending_tasks"])
    reservation = accounting.cultivation.cultivation_balance(IDENTITY)
    record = prepare(READ, now=111)
    finalize(record, detached=True, root=200, at=111.5)
    assert accounting.adopt_pending_receipt(IDENTITY, record["op_id"])
    assert accounting.expire_unanswered_reads(IDENTITY, now=1000, timeout=600)
    assert receipts["pending_tasks"] == before
    assert accounting.current_operation(IDENTITY, consuming["op_id"])["phase"] == phase
    assert accounting.cultivation.cultivation_balance(IDENTITY) == reservation
    assert reservation["value"] == 490000


def test_cancelled_read_releases_only_active_call_tracking(receipts, monkeypatch):
    async def cancelled(_command, **options):
        assert yinluo._active_yinluo_operations(IDENTITY) == {options["op_id"]}
        raise asyncio.CancelledError

    monkeypatch.setattr(yinluo, "send_game_command", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(yinluo.execute_yinluo_manual_action("banner", send_as_id=IDENTITY, now=110))
    record = receipts[accounting.STATE_KEY]["operations"][0]
    assert record["phase"] == "unknown"
    assert not yinluo._active_yinluo_operations(IDENTITY)
    assert not yinluo._YINLUO_INFLIGHT
    assert accounting.expire_unanswered_reads(IDENTITY, now=710, timeout=600)


@pytest.mark.parametrize("change", ["replace", "rebind", "delete", "accounting", "pending"])
def test_staged_expiry_cannot_commit_over_changed_owner_or_evidence(receipts, monkeypatch, change):
    record = detached_read()
    value, _reason = accounting.read_accounting(IDENTITY)
    value["operations"][0]["phase"] = "read_expired"
    update = accounting._update(
        IDENTITY, value, accounting.cultivation.read_cultivation_ledger(IDENTITY), expired_reads=[record["op_id"]],
    )
    if change == "replace":
        state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(receipts)
    elif change == "rebind":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    elif change == "delete":
        state_module.remove_identity(IDENTITY)
    elif change == "accounting":
        receipts[accounting.STATE_KEY]["hold"] = "receipt_conflict"
    else:
        receipts["pending_tasks"][CHAT, 100]["op_id"] = "f" * 32
    before = copy.deepcopy(state_module._meta_state)
    save = Mock(side_effect=AssertionError("Changed ownership cannot commit expiry"))
    monkeypatch.setattr(persistence, "save_state", save)
    assert not accounting.commit_update(update)
    assert state_module._meta_state == before
    save.assert_not_called()


@pytest.mark.parametrize("change", ["replace", "rebind", "delete"])
def test_local_active_tracking_does_not_attach_to_a_replacement(receipts, monkeypatch, change):
    record = prepare(READ)
    monkeypatch.setattr(yinluo, "_YINLUO_INFLIGHT", {(IDENTITY, record["op_id"]): (receipts, ACCOUNT)})
    assert yinluo._active_yinluo_operations(IDENTITY) == {record["op_id"]}
    if change == "replace":
        state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(receipts)
    elif change == "rebind":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    else:
        state_module.remove_identity(IDENTITY)
    before = copy.deepcopy(state_module._meta_state)
    assert not yinluo._active_yinluo_operations(IDENTITY)
    assert state_module._meta_state == before


@pytest.mark.parametrize("field", ["send_as_id", "identity_id"])
def test_expired_read_does_not_adopt_contradictory_identity_metadata(receipts, field):
    record = prepare(READ)
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    finalize(record, detached=True, at=712)
    receipts["pending_tasks"][CHAT, 100][field] = IDENTITY + 1
    before = copy.deepcopy(receipts)
    assert not accounting.adopt_pending_receipt(IDENTITY, record["op_id"])
    assert not accounting.expire_unanswered_reads(IDENTITY, now=1400, timeout=600)
    assert receipts == before


@pytest.mark.parametrize("malformed", [None, [], {"bad": None}])
def test_malformed_pending_cannot_trigger_partial_read_cleanup(receipts, malformed):
    prepare(READ)
    receipts["pending_tasks"] = malformed
    before = copy.deepcopy(receipts)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    assert receipts == before


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), True, "600", 59])
def test_invalid_expiry_policy_does_not_even_adopt_a_receipt(receipts, timeout):
    record = prepare(READ)
    finalize(record, detached=True)
    before = copy.deepcopy(receipts)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=timeout)
    assert receipts == before


def test_late_read_reply_cannot_complete_a_newer_query(receipts):
    detached_read()
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    new = prepare(READ, now=720)
    finalize(new, root=200, at=720.5)
    bind(new, root=200, at=720.5)
    pending = copy.deepcopy(receipts["pending_tasks"])
    current = accounting.current_operation(IDENTITY, new["op_id"])
    received = panel(msg_id=101, start=110, end=730)
    assert observe(received)
    assert accounting.current_operation(IDENTITY, new["op_id"]) == current
    assert receipts["pending_tasks"] == pending
    assert not observe(received)
    assert accounting.current_operation(IDENTITY, new["op_id"]) == current
