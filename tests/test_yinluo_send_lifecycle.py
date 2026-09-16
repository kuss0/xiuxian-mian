import asyncio
import copy
from unittest.mock import AsyncMock, Mock

import pytest

from model import cultivation_accounting as cultivation
from model import persistence, runtime as transport, state as state_module
from model import yinluo_accounting as accounting
from model.features import wanxin, yinluo
from test_cultivation_accounting import snapshot
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, CHAT, CONVERT, IDENTITY, TARGET, _target, bind, env, event,
    finalize, observe, panel, prepare, receipts as receipts_fixture, runtime, value,
)


receipts = receipts_fixture
COMMAND = ".\u5316\u529f\u4e3a\u715e 10000"


def reload_state():
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    return state_module.get_identity_state(IDENTITY)


@pytest.mark.parametrize("caller", ["manual", "wanxin"])
def test_real_predispatch_cancellation_releases_only_unsent_reservation(receipts, monkeypatch, caller):
    target = _target(receipts) if caller == "wanxin" else None
    monkeypatch.setattr(yinluo, "send_game_command", transport.send_game_command)
    monkeypatch.setattr(wanxin, "send_game_command", transport.send_game_command)
    rpc = Mock(side_effect=AssertionError("A cancelled admission must not reach the RPC"))
    monkeypatch.setattr(transport, "_start_game_send_rpc", rpc)

    async def run():
        entered = asyncio.Event()

        async def admission(_command, **options):
            assert options["owner_check"]()
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(transport, "_game_send_allowed", admission)
        if target is None:
            pending = yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=110)
        else:
            pending = wanxin._send_assist_action(target["wanxin_observation"], "strip", 110)
        with state_module.use_identity(IDENTITY if target is None else TARGET):
            task = asyncio.create_task(pending)
        try:
            await asyncio.wait_for(entered.wait(), 1)
            record = copy.deepcopy(receipts[accounting.STATE_KEY]["operations"][0])
            assert record["phase"] == "prepared"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return record

    original = asyncio.run(run())
    rpc.assert_not_called()
    block = transport.classify_game_send_block(IDENTITY, original["command"])
    assert block["status"] == "unsent" and block["code"] == "send_cancelled_unsent"
    current = accounting.current_operation(IDENTITY, original["op_id"])
    assert current["phase"] == "unsent"
    assert current["msg_id"] == 0
    assert not receipts["pending_tasks"]
    assert cultivation.cultivation_balance(IDENTITY) == {"status": "ready", "value": 500000}
    assert value() == {"status": "ready", "value": 2000}
    if target is not None:
        assert not target["wanxin_observation"]["pending"]
        assert not target["wanxin_observation"]["unresolved_actions"]
        assert target["wanxin_observation"]["commission"]["id"] == 10
    restored = reload_state()
    assert accounting.current_operation(IDENTITY, original["op_id"]) == current
    assert not restored["pending_tasks"]
    assert accounting.admission_reason(IDENTITY, original["command"]) == ""


@pytest.mark.parametrize("return_message", [False, True])
def test_completed_native_reply_accepts_its_delayed_return_receipt(receipts, monkeypatch, return_message):
    completed = []

    async def send(command, **options):
        assert options["operation_check"]()
        record = receipts[accounting.STATE_KEY]["operations"][0]
        message = finalize(record)
        assert observe(event(command, CONVERT, end=111))
        assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
        completed.append(copy.deepcopy(receipts))
        await asyncio.sleep(0)
        return message if return_message else None

    monkeypatch.setattr(yinluo, "send_game_command", send)
    ok, message, _plan = asyncio.run(yinluo.execute_yinluo_manual_action(
        "convert", "10000", send_as_id=IDENTITY, now=110,
    ))
    assert ok, message
    assert receipts == completed[0]
    assert not receipts["pending_tasks"]
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
    assert reload_state()[accounting.STATE_KEY] == completed[0][accounting.STATE_KEY]


def test_completed_transport_receipt_is_idempotent_after_reload(receipts):
    record = prepare(COMMAND)
    bind(record)
    assert observe(event(COMMAND, CONVERT))
    restored = reload_state()
    before = copy.deepcopy(restored)
    assert accounting.record_transport(
        IDENTITY, record["op_id"], phase="sent", msg_id=100, chat_id=CHAT, sent_at=110,
    )
    assert restored == before
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


@pytest.mark.parametrize("exit_kind", ["none", "cancel", "error"])
@pytest.mark.parametrize("block_kind", ["fresh", "inherited", "old", "future", "foreign_owner", "foreign_command"])
def test_unreceived_send_needs_new_matching_unsent_evidence(receipts, monkeypatch, exit_kind, block_kind):
    if block_kind == "inherited":
        transport._record_game_send_block(IDENTITY, COMMAND, "global_disabled", "paused", definitely_unsent=True)

    async def send(command, **_options):
        monkeypatch.setattr(yinluo.time, "time", lambda: 110.5)
        if block_kind != "inherited":
            block = transport._record_game_send_block(IDENTITY, command, "global_disabled", "paused", definitely_unsent=True)
            if block_kind == "old":
                block["at"] = 109
            elif block_kind == "future":
                block["at"] = 300
            elif block_kind == "foreign_owner":
                block["send_as_id"] = IDENTITY + 1
            elif block_kind == "foreign_command":
                block["command"] = ".unrelated"
        if exit_kind == "cancel":
            raise asyncio.CancelledError
        if exit_kind == "error":
            raise RuntimeError("local failure")
        return None

    monkeypatch.setattr(yinluo, "send_game_command", send)
    operation = yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=110)
    if exit_kind == "none":
        assert not asyncio.run(operation)[0]
    else:
        with pytest.raises(asyncio.CancelledError if exit_kind == "cancel" else RuntimeError):
            asyncio.run(operation)
    expected = "unsent" if block_kind == "fresh" else "unknown"
    assert receipts[accounting.STATE_KEY]["operations"][0]["phase"] == expected
    restored = reload_state()
    assert restored[accounting.STATE_KEY]["operations"][0]["phase"] == expected
    assert cultivation.cultivation_balance(IDENTITY)["value"] == (500000 if expected == "unsent" else 490000)


@pytest.mark.parametrize("field,value", [
    ("msg_id", 101), ("msg_id", True), ("msg_id", "100"),
    ("chat_id", CHAT - 1), ("chat_id", str(CHAT)),
    ("sent_at", 111), ("sent_at", "110"), ("sent_at", True),
    ("phase", "unknown"), ("phase", "unsent"),
])
def test_completed_operation_rejects_changed_receipt_without_reopening(receipts, field, value):
    record = prepare(COMMAND)
    bind(record)
    assert observe(event(COMMAND, CONVERT))
    before = copy.deepcopy(receipts)
    params = dict(phase="sent", msg_id=100, chat_id=CHAT, sent_at=110)
    params[field] = value
    assert not accounting.record_transport(IDENTITY, record["op_id"], **params)
    assert receipts == before
    assert reload_state()[accounting.STATE_KEY] == before[accounting.STATE_KEY]


def test_completed_operation_can_retire_before_its_caller_returns(receipts, monkeypatch):
    monkeypatch.setattr(accounting, "MAX_OPERATIONS", 2)
    completed = []

    async def send(command, **_options):
        record = receipts[accounting.STATE_KEY]["operations"][0]
        message = finalize(record)
        assert observe(event(command, CONVERT, end=111))
        assert snapshot(490000, at=130, msg_id=300)
        assert observe(panel(sha=4000, msg_id=500, start=132, end=133))
        assert accounting.current_operation(IDENTITY, record["op_id"]) is None
        completed.append(copy.deepcopy(receipts))
        return message

    monkeypatch.setattr(yinluo, "send_game_command", send)
    ok, message, _plan = asyncio.run(yinluo.execute_yinluo_manual_action(
        "convert", "10000", send_as_id=IDENTITY, now=110,
    ))
    assert ok, message
    assert receipts == completed[0]
    assert not receipts["pending_tasks"]
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


def test_failed_release_save_keeps_original_reservation(receipts, monkeypatch):
    conn = persistence.get_db_conn()

    async def send(command, **_options):
        transport._record_game_send_block(IDENTITY, command, "global_disabled", "paused", definitely_unsent=True)
        conn.execute(f"CREATE TEMP TRIGGER fail_unsent_release BEFORE INSERT ON identity_runtime_state "
                     f"WHEN NEW.send_as_id = {IDENTITY} BEGIN SELECT RAISE(ABORT, 'release failure'); END")
        raise asyncio.CancelledError

    monkeypatch.setattr(yinluo, "send_game_command", send)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=110))
    finally:
        conn.execute("DROP TRIGGER IF EXISTS fail_unsent_release")
    assert receipts[accounting.STATE_KEY]["operations"][0]["phase"] == "prepared"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
    restored = reload_state()
    assert restored[accounting.STATE_KEY]["operations"][0]["phase"] == "prepared"
    spy = AsyncMock()
    monkeypatch.setattr(yinluo, "send_game_command", spy)
    asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=120))
    spy.assert_not_awaited()


def test_duplicate_sent_receipt_needs_no_new_write(receipts, monkeypatch):
    record = prepare(COMMAND)
    bind(record)
    restored = reload_state()
    before = copy.deepcopy(restored)
    save = Mock(side_effect=AssertionError("A persisted duplicate has no new state to commit"))
    monkeypatch.setattr(persistence, "save_state", save)
    assert accounting.record_transport(
        IDENTITY, record["op_id"], phase="sent", msg_id=100, chat_id=CHAT, sent_at=110,
    )
    save.assert_not_called()
    assert restored == before


@pytest.mark.parametrize("phase", ["sent", "unknown"])
def test_bound_receipt_cannot_change_its_original_send_time(receipts, phase):
    record = prepare(COMMAND)
    bind(record)
    if phase == "unknown":
        assert accounting.record_transport(IDENTITY, record["op_id"], phase="unknown")
    before = copy.deepcopy(receipts)
    assert not accounting.record_transport(
        IDENTITY, record["op_id"], phase="sent", msg_id=100, chat_id=CHAT, sent_at=110.5,
    )
    assert receipts == before
    assert reload_state()[accounting.STATE_KEY] == before[accounting.STATE_KEY]


def test_retained_transport_without_result_is_not_business_completion(receipts, monkeypatch):
    async def send(_command, **_options):
        finalize(receipts[accounting.STATE_KEY]["operations"][0], detached=True)
        return None

    monkeypatch.setattr(yinluo, "send_game_command", send)
    ok, message, _plan = asyncio.run(yinluo.execute_yinluo_manual_action(
        "convert", "10000", send_as_id=IDENTITY, now=110,
    ))
    assert ok, message
    record = receipts[accounting.STATE_KEY]["operations"][0]
    assert record["phase"] == "sent"
    assert not accounting.command_complete(receipts[accounting.STATE_KEY], CHAT, 100)
    assert (CHAT, 100) in receipts["pending_tasks"]
    assert receipts["yinluo_observation"]["auto_last_error"] == ""
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 500000
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
    spy = AsyncMock()
    monkeypatch.setattr(yinluo, "send_game_command", spy)
    asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=120))
    spy.assert_not_awaited()
    restored = reload_state()
    assert restored[accounting.STATE_KEY]["operations"][0] == record
    assert observe(event(COMMAND, CONVERT))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


@pytest.mark.parametrize("change", ["disable", "replace", "rebind", "delete"])
def test_cancel_unwind_never_reenables_or_writes_through_changed_owner(receipts, monkeypatch, change):
    replacement = []

    async def send(command, **_options):
        if change == "disable":
            receipts["yinluo_enabled"] = False
        elif change == "replace":
            state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(receipts)
        elif change == "rebind":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        else:
            state_module._meta_state["identity_ids"].remove(IDENTITY)
            state_module._meta_state["identity_states"].pop(IDENTITY)
        replacement.append(copy.deepcopy(state_module._meta_state))
        transport._record_game_send_block(IDENTITY, command, "operation_changed", "changed", definitely_unsent=True)
        raise asyncio.CancelledError

    monkeypatch.setattr(yinluo, "send_game_command", send)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=110))
    if change == "disable":
        assert not receipts["yinluo_enabled"]
        assert receipts[accounting.STATE_KEY]["operations"][0]["phase"] == "unsent"
        assert not reload_state()["yinluo_enabled"]
    else:
        assert state_module._meta_state == replacement[0]


@pytest.mark.parametrize("damage", ["operation", "root", "account", "digest", "index"])
def test_late_caller_cannot_adopt_foreign_or_corrupt_archived_completion(receipts, monkeypatch, damage):
    monkeypatch.setattr(accounting, "MAX_OPERATIONS", 2)
    record = prepare(COMMAND)
    bind(record)
    assert observe(event(COMMAND, CONVERT))
    assert snapshot(490000, at=130, msg_id=300)
    assert observe(panel(sha=4000, msg_id=500, start=132, end=133))
    assert accounting.current_operation(IDENTITY, record["op_id"]) is None
    operation_id, root = record["op_id"], 100
    if damage == "operation":
        operation_id = "f" * 32
    elif damage == "root":
        root = 101
    elif damage == "account":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    elif damage == "digest":
        persistence.get_db_conn().execute("UPDATE yinluo_archive_commands SET digest='wrong' WHERE command_msg_id=100")
    else:
        persistence.get_db_conn().execute("DELETE FROM yinluo_archive_messages WHERE command_msg_id=100")
    before = copy.deepcopy(receipts)
    assert not accounting.record_transport(IDENTITY, operation_id, phase="sent", msg_id=root, chat_id=CHAT, sent_at=110)
    assert accounting.current_operation(IDENTITY, operation_id, chat_id=CHAT, msg_id=root) is None
    assert receipts == before
