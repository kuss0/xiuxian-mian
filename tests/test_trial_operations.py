import asyncio
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, state as state_module, ui
from model.features import trial_operations as operations
from model.features import trial_runtime as runtime
from model.features import trial_miniapp as worker
from model.features import cave_treasure_runtime as cave
from model.features.miniapp_common import MiniAppIdentityOwner
import test_trial_lifecycle as lifecycle
from test_trial_checkpoints import TOKEN, INIT, PLAYER, challenge, run


h = lifecycle.h
IDENTITY = lifecycle.IDENTITY


@pytest.fixture
def trial_db(h, monkeypatch, tmp_path):
    for name, value in {
        "DB_FILE": str(tmp_path / "trial.db"), "_db_conn": None, "_db_initialized": False,
        "_schema_columns_ensured_key": None, "_schema_columns_ensured_version": None,
        "_persistence_snapshot_db_key": "", "_persisted_meta_snapshot": {}, "_persisted_identity_snapshots": {},
        "_state_dirty": False, "_last_flush_time": 0, "_last_save_failed_at": 0, "_last_save_error": "",
    }.items():
        monkeypatch.setattr(persistence, name, value)
    monkeypatch.setattr(persistence, "_try_write_live_guard_backup", Mock(return_value=False))
    monkeypatch.setattr(operations, "save_state", persistence.save_state)
    monkeypatch.setattr(operations, "time", SimpleNamespace(time=lambda: h.now))
    try:
        yield h
    finally:
        if persistence._db_conn is not None:
            persistence._db_conn.close()


async def owned_run(h, *, transport=None, checkpoint=None, rounds=1, operation_check=None):
    writer = operations.CheckpointWriter(MiniAppIdentityOwner.capture(IDENTITY),
                                         player_id=PLAYER, operation_check=operation_check)
    callback = (lambda frame: checkpoint(writer, frame)) if checkpoint else writer
    result = await asyncio.to_thread(
        run, transport=transport, checkpoint=callback, rounds=rounds,
        operation_check=lambda: writer.is_current() and (operation_check is None or operation_check() is True),
    )
    return writer, result


def test_checkpoint_is_on_main_loop_and_in_sqlite_before_transport(trial_db, monkeypatch):
    h = trial_db
    main_thread = threading.get_ident()
    calls = []
    save = persistence.save_state

    def save_on_loop():
        assert threading.get_ident() == main_thread
        return save()

    monkeypatch.setattr(operations, "save_state", save_on_loop)

    def transport(request):
        assert threading.get_ident() != main_thread
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        with sqlite3.connect(persistence.DB_FILE) as conn:
            encoded = conn.execute("SELECT trial_operation FROM identity_runtime_state WHERE send_as_id=?", (IDENTITY,)).fetchone()[0]
        record = json.loads(encoded)
        assert operations.valid_record(record)
        assert record["checkpoint"]["phase"] == "intent"
        assert record["checkpoint"]["pending"]["action"] == endpoint
        if endpoint == "finish":
            return {"ok": True, "result": {"traceGain": 3}}
        return {"ok": True, "challenge": challenge()}

    async def scenario():
        writer, result = await owned_run(h, transport=transport)
        assert writer.finish(result)
        return result

    result = asyncio.run(scenario())
    assert result["status"] == "settled"
    assert calls == ["start", "finish"]
    record = deepcopy(h.owner[operations.STATE_KEY])
    assert operations.valid_record(record) and not operations.pending(h.owner)
    assert len(record["checkpoint"]["round_receipts"]) == 1
    encoded = json.dumps(record)
    assert TOKEN not in encoded and INIT not in encoded and "trialProof" not in encoded
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert state_module.get_identity_state(IDENTITY)[operations.STATE_KEY] == record


@pytest.mark.parametrize("failure", [False, None, "true", "exception"])
def test_failed_intent_save_never_reaches_transport(h, monkeypatch, failure):
    save = Mock(side_effect=OSError("fixture disk full")) if failure == "exception" else Mock(return_value=failure)
    monkeypatch.setattr(operations, "save_state", save)
    transport = Mock()

    async def scenario():
        writer, result = await owned_run(h, transport=transport)
        assert writer.finish(result)
        return result

    result = asyncio.run(scenario())
    transport.assert_not_called()
    assert result["checkpoint_error"]
    assert h.owner[operations.STATE_KEY] == {}


@pytest.mark.parametrize("kind", ["invalid", "wrong_owner", "unknown"])
def test_held_operation_stops_public_entry_and_ui_command_before_network(h, monkeypatch, kind):
    if kind == "invalid":
        h.owner[operations.STATE_KEY] = {"invalid": True}
    else:
        async def scenario():
            def transport(_request):
                raise OSError("fixture connection reset")
            writer, result = await owned_run(h, transport=transport)
            assert writer.finish(result)
        asyncio.run(scenario())
        if kind == "wrong_owner":
            h.owner[operations.STATE_KEY]["account_id"] += 1
    before = deepcopy(h.owner)
    result = asyncio.run(cave.run_cave_public_trial(IDENTITY, h.public_url))
    assert result["extra"]["status"] == "operation_pending"
    h.loader.assert_not_awaited()
    h.external.assert_not_awaited()
    h.flow.assert_not_awaited()
    assert runtime.authorize_trial_miniapp_manual_run(IDENTITY) == 0
    send = AsyncMock()
    monkeypatch.setattr(ui, "send_game_command", send)
    ok, _message, extra = asyncio.run(ui.ui_send_miniapp_manual_run(IDENTITY, "trial"))
    assert not ok and extra["persistence_only"]
    send.assert_not_awaited()
    assert h.owner == before


def test_interrupted_settlement_recovers_locally_once(trial_db):
    h = trial_db

    async def scenario():
        writer = operations.CheckpointWriter(MiniAppIdentityOwner.capture(IDENTITY), player_id=PLAYER)
        records = []
        run(checkpoint=lambda frame: records.append(deepcopy(frame)) or True)
        for record in records:
            if record["phase"] == "complete":
                break
            assert writer(record)

    asyncio.run(scenario())
    assert operations.pending(h.owner)
    result = operations.recover_local(IDENTITY)
    assert result["persistence_only"] and result["status"] == "partial"
    assert result["data"]["settled_count"] == 1
    assert result["data"]["results"][0]["traceGain"] == 3
    assert operations.recover_local(IDENTITY) is None
    assert not operations.pending(h.owner)
    assert persistence.load_state()
    assert operations.recover_local(IDENTITY) is None


@pytest.mark.parametrize("malformed", ["{broken", "[]", "null", "false", "1", '{"bad":NaN}'])
def test_malformed_journal_does_not_decode_to_idle(malformed):
    decoded = persistence._deserialize_db_value(operations.STATE_KEY, malformed)
    assert decoded != {}
    assert not operations.valid_record(decoded)


@pytest.mark.parametrize("value", [None, False, [], {"bad": float("nan")}, {"bad": "x" * (256 * 1024)}])
def test_bad_or_oversized_journal_serializes_as_a_hold(value):
    encoded = persistence._serialize_db_value(operations.STATE_KEY, value)
    assert encoded == '{"invalid":true}' or json.loads(encoded) == {"invalid": True}


@pytest.mark.parametrize("caller", ["public", "command"])
def test_native_caller_persists_unknown_finish_and_prevents_reentry(h, monkeypatch, caller):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            raise OSError("fixture connection reset")
        return {"ok": True, "challenge": challenge()}

    async def flow(identity_id, **kwargs):
        return await worker.run_trial_miniapp_production_flow(
            identity_id, **dict(kwargs, init_data=INIT, transport=transport, sleeper=lambda _delay: None),
        )

    monkeypatch.setattr(cave, "run_trial_miniapp_production_flow", flow)
    monkeypatch.setattr(runtime, "run_trial_miniapp_production_flow", flow)
    asyncio.run(lifecycle.call_trial(h, caller))
    assert calls == ["start", "finish"]
    assert operations.pending(h.owner)
    assert h.owner[operations.STATE_KEY]["checkpoint"]["pending"]["action"] == "finish"
    assert h.owner[operations.STATE_KEY]["checkpoint"]["outcome_unknown"]
    assert runtime.authorize_trial_miniapp_manual_run(IDENTITY) == 0
    asyncio.run(cave.run_cave_public_trial(IDENTITY, h.public_url))
    assert calls == ["start", "finish"]


def captured_run(**kwargs):
    frames = []
    result = run(checkpoint=lambda frame: frames.append(deepcopy(frame)) or True, **kwargs)
    return frames, result


@pytest.mark.parametrize("change,prefix", [
    ("clear_unknown", 1), ("foreign_challenge", 1), ("unrelated_entry", 1),
    ("finish_before_challenge", 1), ("next_before_settlement", 3),
    ("switched_challenge", 2), ("repeat_start", 2), ("dispatch_regression", 3),
])
def test_checkpoint_cannot_skip_or_replace_pending_work(h, change, prefix):
    frames, _result = captured_run()
    if change == "clear_unknown":
        forged = deepcopy(frames[-1])
        forged["round_receipts"] = []
    elif change in {"foreign_challenge", "unrelated_entry"}:
        forged = deepcopy(frames[1])
        if change == "foreign_challenge":
            forged["pending"]["entry_key"] = "f" * 64
        else:
            forged["pending"].update(action="entry", challenge_key="")
    elif change in {"finish_before_challenge", "switched_challenge"}:
        forged = deepcopy(frames[2])
        if change == "switched_challenge":
            forged["pending"]["challenge_key"] = "f" * 64
    elif change == "next_before_settlement":
        forged = deepcopy(frames[2])
        forged["pending"].update(action="next", challenge_key="")
    elif change == "repeat_start":
        forged = deepcopy(frames[0])
        forged["action_dispatched"] = True
    else:
        forged = deepcopy(frames[3])
        forged["action_dispatched"] = False
    forged["sequence"] = prefix + 1

    async def scenario():
        writer = operations.CheckpointWriter(MiniAppIdentityOwner.capture(IDENTITY), player_id=PLAYER)
        for frame in frames[:prefix]:
            assert writer(frame)
        before = deepcopy(h.owner[operations.STATE_KEY])
        assert not writer(forged)
        assert h.owner[operations.STATE_KEY] == before

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["phase", "pending_action"])
@pytest.mark.parametrize("value", [[], {}, None, 1, True])
def test_bad_checkpoint_types_are_rejected_without_raising(field, value):
    frames, _result = captured_run()
    frame = frames[0]
    if field == "pending_action":
        frame["pending"]["action"] = value
    else:
        frame[field] = value
    assert not operations.valid_checkpoint(frame)


@pytest.mark.parametrize("field", ["identity_id", "account_id"])
@pytest.mark.parametrize("entry", ["authorization", "writer"])
def test_completed_foreign_record_cannot_authorize_another_operation(h, field, entry):
    async def scenario():
        writer, result = await owned_run(h)
        assert writer.finish(result)
        h.owner[operations.STATE_KEY][field] += 1
        before = deepcopy(h.owner)
        if entry == "authorization":
            assert runtime.authorize_trial_miniapp_manual_run(IDENTITY) == 0
        else:
            with pytest.raises(ValueError):
                operations.CheckpointWriter(MiniAppIdentityOwner.capture(IDENTITY), player_id=PLAYER)
        assert h.owner == before

    asyncio.run(scenario())


def test_completed_pending_save_is_a_local_only_public_call(h):
    async def scenario():
        writer, result = await owned_run(h)
        assert writer.finish(result)
        h.owner[operations.STATE_KEY]["pending_save"] = True
        return await cave.run_cave_public_trial(IDENTITY, h.public_url)

    result = asyncio.run(scenario())
    assert result["extra"].get("persistence_only") is True
    h.loader.assert_not_awaited()
    h.external.assert_not_awaited()
    h.flow.assert_not_awaited()
    assert not operations.pending(h.owner)
    assert operations.recover_local(IDENTITY) is None


@pytest.mark.parametrize("saved", [False, True])
def test_save_callback_cannot_overwrite_a_replacement_record(h, monkeypatch, saved):
    replacement = {"invalid": True, "replacement": "fixture"}

    def changed_save():
        h.owner[operations.STATE_KEY] = deepcopy(replacement)
        return saved

    monkeypatch.setattr(operations, "save_state", changed_save)
    transport = Mock()

    async def scenario():
        writer, result = await owned_run(h, transport=transport)
        assert not writer.finish(result)

    asyncio.run(scenario())
    transport.assert_not_called()
    assert h.owner[operations.STATE_KEY] == replacement


@pytest.mark.parametrize("prefix", [0, 4])
def test_manual_admission_cannot_recover_or_overlap_an_active_worker(h, monkeypatch, prefix):
    send = AsyncMock()
    monkeypatch.setattr(ui, "send_game_command", send)

    async def scenario():
        writer = operations.CheckpointWriter(MiniAppIdentityOwner.capture(IDENTITY), player_id=PLAYER)
        frames, _result = captured_run()
        for frame in frames[:prefix]:
            assert writer(frame)
        async with runtime._run_lock(IDENTITY):
            before = deepcopy(h.owner)
            ok, _message, extra = await ui.ui_send_miniapp_manual_run(IDENTITY, "trial")
            assert not ok and extra["status"] == "busy"
            assert runtime.authorize_trial_miniapp_manual_run(IDENTITY) == 0
            assert h.owner == before

    asyncio.run(scenario())
    send.assert_not_awaited()


def test_timed_out_queued_checkpoint_cannot_write_later(h, monkeypatch):
    callbacks = []
    save = Mock(return_value=True)
    monkeypatch.setattr(operations, "save_state", save)
    monkeypatch.setattr(operations, "CHECKPOINT_TIMEOUT_SEC", 0.01)

    async def scenario():
        writer = operations.CheckpointWriter(MiniAppIdentityOwner.capture(IDENTITY), player_id=PLAYER)
        writer.loop = SimpleNamespace(call_soon_threadsafe=callbacks.append)
        frames, _result = captured_run()
        assert not await asyncio.to_thread(writer, frames[0])
        assert len(callbacks) == 1
        callbacks[0]()

    asyncio.run(scenario())
    save.assert_not_called()
    assert h.owner[operations.STATE_KEY] == {}


def test_timed_out_running_checkpoint_drains_its_acknowledgement(h, monkeypatch):
    started, timed_out, draining = threading.Event(), threading.Event(), threading.Event()

    class TimeoutOnce(Future):
        def result(self, timeout=None):
            if timeout is not None and not timed_out.is_set():
                assert started.wait(2)
                timed_out.set()
                raise TimeoutError()
            if timeout is None:
                draining.set()
                timeout = 2
            return super().result(timeout=timeout)

    def save():
        if not started.is_set():
            started.set()
            assert timed_out.wait(2)
            assert draining.wait(2)
        return True

    monkeypatch.setattr(operations, "Future", TimeoutOnce)
    monkeypatch.setattr(operations, "save_state", save)

    async def scenario():
        writer, result = await owned_run(h)
        assert writer.finish(result)
        assert not result["checkpoint_error"]
        assert result["status"] == "settled" and result["data"]["settled_count"] == 1

    asyncio.run(scenario())
    assert draining.is_set() and not operations.pending(h.owner)


@pytest.mark.parametrize("phase,action,calls,count,pending", [
    ("intent", "start", [], 0, ""),
    ("response", "challenge", ["start"], 0, "challenge"),
    ("intent", "finish", ["start"], 0, "challenge"),
    ("settled", "", ["start", "finish"], 1, ""),
    ("intent", "next", ["start", "finish"], 1, ""),
    ("response", "entry", ["start", "finish", "next"], 1, "entry"),
    ("complete", "", ["start", "finish", "next", "start", "finish"], 2, ""),
])
@pytest.mark.parametrize("raises", [False, True])
def test_failed_checkpoint_preserves_facts_and_stops_later_requests(h, monkeypatch, phase, action, calls, count, pending, raises):
    observed, failed = [], []

    def save():
        frame = h.owner[operations.STATE_KEY]["checkpoint"]
        if not failed and frame["phase"] == phase and frame["pending"].get("action", "") == action:
            failed.append(deepcopy(frame))
            if raises:
                raise OSError("fixture disk failure")
            return False
        return True

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        observed.append(endpoint)
        if endpoint == "finish":
            return {"ok": True, "result": {"traceGain": 3}}
        if endpoint == "next":
            return {"ok": True, "token": TOKEN + "_next"}
        return {"ok": True, "challenge": challenge(observed.count("start"))}

    monkeypatch.setattr(operations, "save_state", save)

    async def scenario():
        writer, result = await owned_run(h, transport=transport, rounds=2)
        assert writer.finish(result)
        return result

    result = asyncio.run(scenario())
    assert len(failed) == 1 and observed == calls
    assert result["checkpoint_error"]
    assert result["data"]["settled_count"] == len(result["round_receipts"]) == count
    assert result["pending"].get("action", "") == pending
    record = h.owner[operations.STATE_KEY]
    if record:
        assert operations.valid_record(record)
        assert record["checkpoint"]["round_receipts"] == result["round_receipts"]
        assert record["checkpoint"]["pending"] == result["pending"]


def test_sqlite_receipt_abort_retains_memory_facts_until_local_save(trial_db):
    h = trial_db
    assert persistence.save_state()
    conn = persistence.get_db_conn()
    conn.execute("""CREATE TEMP TRIGGER fail_trial_receipt BEFORE UPDATE ON identity_runtime_state
                    WHEN json_array_length(json_extract(NEW.trial_operation, '$.checkpoint.round_receipts')) > 0
                    BEGIN SELECT RAISE(ABORT, 'fixture trial receipt failure'); END""")

    async def scenario():
        writer, result = await owned_run(h)
        return operations.finish_result(writer, result)

    result = asyncio.run(scenario())
    assert result["persistence_pending"] and result["data"]["settled_count"] == 1
    memory = deepcopy(h.owner[operations.STATE_KEY])
    assert memory["pending_save"] and memory["checkpoint"]["round_receipts"]
    stored = json.loads(conn.execute("SELECT trial_operation FROM identity_runtime_state WHERE send_as_id=?",
                                    (IDENTITY,)).fetchone()[0])
    assert stored["checkpoint"]["pending"]["action"] == "finish"
    assert not stored["checkpoint"]["round_receipts"]
    conn.execute("DROP TRIGGER fail_trial_receipt")
    conn.commit()
    recovered = operations.recover_local(IDENTITY)
    assert recovered["persistence_only"] and recovered["status"] == "recovered"
    assert not operations.pending(h.owner)
    assert persistence.load_state()
    restored = state_module.get_identity_state(IDENTITY)[operations.STATE_KEY]
    assert restored == {**memory, "pending_save": False}
    assert operations.recover_local(IDENTITY) is None


@pytest.mark.parametrize("failed_final", [False, True])
def test_finished_writer_rejects_late_checkpoint_or_finish(h, monkeypatch, failed_final):
    save = Mock(return_value=True)
    monkeypatch.setattr(operations, "save_state", save)

    async def scenario():
        writer, result = await owned_run(h)
        save.return_value = not failed_final
        assert writer.finish(result) is (not failed_final)
        before = deepcopy(h.owner)
        save.return_value = True
        assert not writer(writer.last_checkpoint)
        assert not writer.finish(result)
        assert h.owner == before
        assert h.owner[operations.STATE_KEY]["pending_save"] is failed_final

    asyncio.run(scenario())


@pytest.mark.parametrize("corrupt", [None, False, [], 0, ""])
def test_writer_cannot_replace_a_falsey_corrupt_journal(h, corrupt):
    h.owner[operations.STATE_KEY] = corrupt

    async def scenario():
        with pytest.raises(ValueError):
            operations.CheckpointWriter(MiniAppIdentityOwner.capture(IDENTITY), player_id=PLAYER)

    asyncio.run(scenario())
    assert h.owner[operations.STATE_KEY] == corrupt


@pytest.mark.parametrize("path", ["embedded", "next_challenge", "next_token", "next_both", "nested_token"])
def test_owned_multiround_paths_preserve_entry_keys_and_ordered_receipts(h, path):
    calls, finished = [], []
    rotated = path in {"next_token", "next_both", "nested_token"}

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            index = len(finished)
            assert request["payload"]["token"] == (f"{TOKEN}_{index}" if rotated and index else TOKEN)
            finished.append(request["payload"]["trialProof"]["challengeId"])
            return {"ok": True, "result": {"traceGain": 3}, **(
                {"nextChallenge": challenge(index + 2)} if path == "embedded" else {}
            )}
        if endpoint == "next":
            metadata = {"token": f"{TOKEN}_{len(finished)}"} if rotated else {}
            if path != "next_token":
                metadata["challenge"] = challenge(len(finished) + 1)
            return {"ok": True, **({"data": metadata} if path == "nested_token" else metadata)}
        return {"ok": True, "challenge": challenge(len(finished) + 1)}

    async def scenario():
        writer, result = await owned_run(h, transport=transport, rounds=3)
        assert writer.finish(result)
        return result

    result = asyncio.run(scenario())
    assert not result["checkpoint_error"] and result["status"] == "settled"
    assert len(set(finished)) == len(result["round_receipts"]) == 3
    assert calls.count("start") == (3 if path == "next_token" else 1)
    assert calls.count("next") == (0 if path == "embedded" else 2)
    checkpoint = h.owner[operations.STATE_KEY]["checkpoint"]
    assert checkpoint["round_receipts"] == result["round_receipts"]
    assert not operations.pending(h.owner)


@pytest.mark.parametrize("endpoint,expected_pending", [("start", ""), ("finish", "challenge"), ("next", "")])
def test_definitive_rejection_returns_to_exact_previous_stage(h, endpoint, expected_pending):
    calls = []

    def transport(request):
        current = request["safe_summary"]["endpoint"]
        calls.append(current)
        if current == endpoint:
            return {"ok": False, "error": "fixture request rejected"}
        return {"ok": True, "result": {"traceGain": 3}} if current == "finish" else {"ok": True, "challenge": challenge()}

    async def scenario():
        writer, result = await owned_run(h, transport=transport, rounds=2)
        assert writer.finish(result)
        return result

    result = asyncio.run(scenario())
    assert calls[-1] == endpoint and not result["outcome_unknown"]
    assert result["pending"].get("action", "") == expected_pending
    assert result["request_resolution"]["kind"] == "rejected"
    assert not result["checkpoint_error"]


@pytest.mark.parametrize("endpoint", ["start", "finish", "next"])
@pytest.mark.parametrize("code", [408, 425, 429, 500, 503])
def test_ambiguous_http_failure_retains_request_and_server_wait_without_retry(h, endpoint, code):
    calls = []

    def transport(request):
        current = request["safe_summary"]["endpoint"]
        calls.append(current)
        if current == endpoint:
            return code, {"ok": False, "error": "fixture busy"}, {"Retry-After": "91"}
        return {"ok": True, "result": {"traceGain": 3}} if current == "finish" else {"ok": True, "challenge": challenge()}

    async def scenario():
        writer, result = await owned_run(h, transport=transport, rounds=2)
        assert writer.finish(result)
        return result

    result = asyncio.run(scenario())
    expected = ["start", "finish", "next"]
    assert calls == expected[:expected.index(endpoint) + 1]
    assert result["outcome_unknown"] and result["pending"]["action"] == endpoint
    assert result["request_resolution"] == {} and result["retry_after_sec"] == 91
    record = h.owner[operations.STATE_KEY]
    assert record["checkpoint"]["retry_after_sec"] == 91
    assert record["checkpoint"]["pending"]["action"] == endpoint
    assert operations.recover_local(IDENTITY)["status"] == "operation_pending"
    assert runtime.authorize_trial_miniapp_manual_run(IDENTITY) == 0


@pytest.mark.parametrize("endpoint", ["start", "finish"])
def test_control_change_after_saved_intent_records_proven_unsent(h, monkeypatch, endpoint):
    allowed, calls, changed = True, [], []

    def save():
        nonlocal allowed
        frame = h.owner[operations.STATE_KEY]["checkpoint"]
        if not changed and frame["phase"] == "intent" and frame["pending"]["action"] == endpoint:
            allowed = False
            changed.append(deepcopy(frame))
        return True

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return {"ok": True, "challenge": challenge()}

    monkeypatch.setattr(operations, "save_state", save)

    async def scenario():
        writer, result = await owned_run(h, transport=transport, operation_check=lambda: allowed)
        assert writer.finish(result)
        return result

    result = asyncio.run(scenario())
    assert calls == ([] if endpoint == "start" else ["start"])
    assert not result["outcome_unknown"] and not result["checkpoint_error"]
    assert result["request_resolution"]["kind"] == "not_sent"
    assert result["pending"].get("action", "") == ("" if endpoint == "start" else "challenge")


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
def test_owner_change_during_save_cannot_dispatch_or_rollback_new_owner(h, monkeypatch, change):
    transport = Mock()
    snapshots = []

    def save():
        lifecycle.invalidate_owner(h, change)
        snapshots.append(deepcopy(state_module._meta_state["identity_states"]))
        return True

    monkeypatch.setattr(operations, "save_state", save)

    async def scenario():
        writer, result = await owned_run(h, transport=transport)
        assert not writer.finish(result)

    asyncio.run(scenario())
    transport.assert_not_called()
    assert len(snapshots) == 1 and state_module._meta_state["identity_states"] == snapshots[0]


def test_duplicate_out_of_order_and_mutated_checkpoints_leave_revision_unchanged(h, monkeypatch):
    save = Mock(return_value=True)
    monkeypatch.setattr(operations, "save_state", save)

    async def scenario():
        writer = operations.CheckpointWriter(MiniAppIdentityOwner.capture(IDENTITY), player_id=PLAYER)
        frames, _result = captured_run()
        assert writer(frames[0])
        assert writer(frames[0])
        assert save.call_count == 1
        before = deepcopy(h.owner[operations.STATE_KEY])
        assert not writer(frames[2])
        assert h.owner[operations.STATE_KEY] == before
        for frame in frames[1:4]:
            assert writer(frame)
        before = deepcopy(h.owner[operations.STATE_KEY])
        forged = deepcopy(frames[4])
        forged["round_receipts"][0]["data"]["traceGain"] += 1
        assert not writer(forged)
        assert h.owner[operations.STATE_KEY] == before
        assert writer(frames[4])
        frames[4]["round_receipts"].clear()
        assert len(h.owner[operations.STATE_KEY]["checkpoint"]["round_receipts"]) == 1

    asyncio.run(scenario())
