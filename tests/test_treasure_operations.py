import asyncio
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, persistence, state as state_module, ui
from model.features import cave_treasure_miniapp as worker
from model.features import cave_treasure_runtime as runtime
from model.features import treasure_operations as operations
from model.features import treasure_results as results
from model.features.miniapp_common import MiniAppFlowCancelled, MiniAppIdentityOwner
from test_treasure_lifecycle import IDENTITY, INIT, TOKEN, URL, adapter, call, change_owner, panel, run, scripted_transport
from test_treasure_lifecycle import h as h


NATIVE_WRITER = operations.CheckpointWriter


@pytest.fixture
def native(h, monkeypatch):
    monkeypatch.setattr(operations, "CheckpointWriter", NATIVE_WRITER)
    h.ops_save, h.result_save = Mock(return_value=True), Mock(return_value=True)
    monkeypatch.setattr(operations, "save_state", h.ops_save)
    monkeypatch.setattr(results, "save_state", h.result_save)
    monkeypatch.setattr(operations, "time", SimpleNamespace(time=lambda: h.now))
    h.calls = []
    h.transport = scripted_transport(h.calls, limit=1)

    async def flow(identity_id, **kwargs):
        return await worker.run_cave_treasure_miniapp_production_flow(
            identity_id, **dict(kwargs, transport=h.transport, adapter=adapter(), sleeper=lambda _delay: None),
        )

    h.flow.side_effect = flow
    monkeypatch.setattr(ui, "_cave_public_background_retry_at", {})
    monkeypatch.setattr(ui, "_cave_public_background_daily_done", set())
    h.send = AsyncMock()
    monkeypatch.setattr(ui, "send_game_command", h.send)
    return h


@pytest.fixture
def treasure_db(native, monkeypatch, tmp_path):
    h = native
    for name, value in {
        "DB_FILE": str(tmp_path / "treasure.db"), "_db_conn": None, "_db_initialized": False,
        "_schema_columns_ensured_key": None, "_schema_columns_ensured_version": None,
        "_persistence_snapshot_db_key": "", "_persisted_meta_snapshot": {}, "_persisted_identity_snapshots": {},
        "_state_dirty": False, "_last_flush_time": 0, "_last_save_failed_at": 0, "_last_save_error": "",
    }.items():
        monkeypatch.setattr(persistence, name, value)
    monkeypatch.setattr(persistence, "_try_write_live_guard_backup", Mock(return_value=False))
    monkeypatch.setattr(operations, "save_state", persistence.save_state)
    monkeypatch.setattr(results, "save_state", persistence.save_state)
    try:
        yield h
    finally:
        if persistence._db_conn is not None:
            persistence._db_conn.close()


async def uncommitted_worker(h, *, checkpoint=None):
    projection = results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY))
    writer = operations.CheckpointWriter(projection, player_id=IDENTITY)
    result = await worker.run_cave_treasure_miniapp_production_flow(
        IDENTITY, token=TOKEN, webview_url=URL, init_data=INIT, player_id=IDENTITY,
        transport=h.transport, adapter=adapter(), sleeper=lambda _delay: None,
        checkpoint=(lambda frame: checkpoint(writer, frame)) if checkpoint else writer,
        operation_check=writer.is_current,
    )
    return writer, result


def test_native_call_commits_linked_operation_and_result_once(native):
    h = native
    response = asyncio.run(call(h, "public"))
    assert response["ok"], response
    assert h.calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
    record = h.owner[operations.STATE_KEY]
    assert operations.valid_record(record)
    assert record["checkpoint"]["phase"] == "complete"
    assert operations.result_matches(MiniAppIdentityOwner.capture(IDENTITY), h.owner[results.STATE_KEY])
    assert not operations.hold_reason(IDENTITY)
    h.result_save.assert_called_once()
    inventory = state_module.get_inventory_delta_records()
    assert inventory[operations.inventory_key(record)]["items"] == {"fixture_item": 1}
    assert runtime.recover_cave_treasure_result(IDENTITY) is None
    assert h.result_save.call_count == 1


def test_every_native_mutation_has_durable_owned_intent(treasure_db, monkeypatch):
    h = treasure_db
    main_thread = threading.get_ident()
    original_save = persistence.save_state
    original_transport = h.transport

    def save():
        assert threading.get_ident() == main_thread
        return original_save()

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        assert threading.get_ident() != main_thread
        if endpoint != "start":
            with sqlite3.connect(persistence.DB_FILE) as conn:
                encoded = conn.execute("SELECT treasure_operation FROM identity_runtime_state WHERE send_as_id=?",
                                       (IDENTITY,)).fetchone()[0]
            record = json.loads(encoded)
            assert operations.valid_record(record)
            assert record["checkpoint"]["phase"] == "intent"
            assert record["checkpoint"]["pending"]["action"] == {
                "hunt": "enter", "hunt_reveal": "search", "hunt_settle": "settle",
            }[endpoint]
        return original_transport(request)

    h.transport = transport
    monkeypatch.setattr(operations, "save_state", save)
    response = asyncio.run(call(h, "public"))
    assert response["ok"], response
    record, committed = deepcopy(h.owner[operations.STATE_KEY]), deepcopy(h.owner[results.STATE_KEY])
    encoded = json.dumps(record)
    assert TOKEN not in encoded and INIT not in encoded and "fixture125-1" not in encoded
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert state_module.get_identity_state(IDENTITY)[operations.STATE_KEY] == record
    assert state_module.get_identity_state(IDENTITY)[results.STATE_KEY] == committed
    assert runtime.recover_cave_treasure_result(IDENTITY) is None


@pytest.mark.parametrize("caller", ["public", "command"])
def test_later_unknown_request_keeps_earlier_material_and_blocks_reentry(native, caller):
    h = native
    base = scripted_transport(h.calls, limit=2)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "hunt" and "hunt_settle" in h.calls:
            h.calls.append("hunt")
            raise OSError("fixture later round unknown")
        return base(request)

    h.transport = transport
    asyncio.run(call(h, caller))
    record = h.owner[operations.STATE_KEY]
    assert operations.valid_record(record), record
    assert record["checkpoint"]["pending"]["action"] == "enter"
    assert len(record["checkpoint"]["receipts"]) == 1
    assert state_module.get_inventory_delta_records()[operations.inventory_key(record)]["items"] == {"fixture_item": 1}
    before_calls = list(h.calls)
    response = asyncio.run(call(h, "public"))
    assert not response["ok"] and response["extra"]["persistence_only"]
    assert h.calls == before_calls
    h.loader.assert_awaited_once()


def test_interrupted_settlement_recovery_is_local_atomic_and_idempotent(treasure_db):
    h = treasure_db
    writer, result = asyncio.run(uncommitted_worker(h))
    assert result["status"] == "daily_limit", result
    assert writer.sequence and h.owner[operations.STATE_KEY]["checkpoint"]["phase"] == "settled"
    before = deepcopy(h.owner[operations.STATE_KEY])
    assert not state_module.get_inventory_delta_records()
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"].get("persistence_saved"), response
    assert not response["extra"]["daily_exhausted"]
    assert h.owner[operations.STATE_KEY] == before
    assert runtime.recover_cave_treasure_result(IDENTITY) is None
    assert len(set(state_module.get_inventory_delta_records()) - {"_meta"}) == 1
    assert persistence.load_state()
    assert runtime.recover_cave_treasure_result(IDENTITY) is None
    assert state_module.get_inventory_delta_records()[operations.inventory_key(before)]["items"] == {"fixture_item": 1}
    h.audit.assert_not_awaited()
    h.loader.assert_not_awaited()
    h.send.assert_not_awaited()


@pytest.mark.parametrize("ack", [False, None, 1, "true", "exception"])
def test_failed_initial_checkpoint_does_not_dispatch_a_mutation(native, ack):
    h = native
    if ack == "exception":
        h.ops_save.side_effect = OSError("fixture storage full")
    else:
        h.ops_save.return_value = ack
    response = asyncio.run(call(h, "public"))
    assert not response["ok"]
    assert h.calls == ["start"]
    assert h.owner[operations.STATE_KEY] == {}


def test_failed_post_dispatch_checkpoint_retains_facts_and_recovers_without_game(native):
    h = native
    h.ops_save.side_effect = [True, True, False, False]
    response = asyncio.run(call(h, "public"))
    assert response["extra"]["persistence_only"], response
    record = h.owner[operations.STATE_KEY]
    assert record["pending_save"] and record["checkpoint"]["state"]["in_round"]
    assert h.calls == ["start", "hunt"]
    h.ops_save.side_effect = None
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"]["persistence_saved"], response
    assert operations.hold_reason(IDENTITY) == "original_round_required"
    assert h.calls == ["start", "hunt"]


def test_cancelled_settlement_drains_and_commits_before_releasing_account(native):
    h = native
    entered, release = threading.Event(), threading.Event()
    base = h.transport

    def transport(request):
        if request["safe_summary"]["endpoint"] == "hunt_settle":
            entered.set()
            assert release.wait(4)
        return base(request)

    h.transport = transport

    async def scenario():
        task = asyncio.create_task(call(h, "public"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done() and runtime.is_cave_treasure_busy(IDENTITY)
            task.cancel()
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as failure:
                await task
        assert failure.value.result["extra"]["settled_count"] == 1
        assert not runtime.is_cave_treasure_busy(IDENTITY)

    asyncio.run(scenario())
    assert len(h.owner[operations.STATE_KEY]["checkpoint"]["receipts"]) == 1
    assert h.owner[results.STATE_KEY]["phase"] == "complete"


@pytest.mark.parametrize("malformed", ["{broken", "[]", "null", "false", "1", '{"bad":NaN}'])
def test_bad_persisted_operation_never_decodes_to_idle(malformed):
    value = persistence._deserialize_db_value(operations.STATE_KEY, malformed)
    assert value != {} and not operations.valid_record(value)


@pytest.mark.parametrize("value", [None, False, [], {"x": float("nan")}, {"x": "x" * (256 * 1024)}])
def test_bad_operation_serializes_to_an_explicit_hold(value):
    assert json.loads(persistence._serialize_db_value(operations.STATE_KEY, value)) == {"invalid": True}


@pytest.mark.parametrize("caller", ["public", "command", "ui", "ui_command", "scheduler", "background"])
@pytest.mark.parametrize("control", ["unchanged", "pause", "disable", "config"])
def test_interrupted_operation_recovers_across_all_controls_without_entry(native, caller, control):
    h = native
    asyncio.run(uncommitted_worker(h))
    if control in {"pause", "disable"}:
        change_owner(h, control)
    elif control == "config":
        state_module.set_miniapp_auto_config({"cave_public_treasure_enabled": False, "cave_public_entry_url": ""})
    before, calls = deepcopy(h.owner), list(h.calls)

    async def recover():
        if caller in {"public", "command"}:
            return await call(h, caller)
        if caller == "ui":
            return await ui.ui_run_cave_public_entry(IDENTITY, "treasure", "")
        if caller == "ui_command":
            return await ui.ui_send_miniapp_manual_run(IDENTITY, "cave_treasure")
        if caller == "scheduler":
            return await ui.run_miniapp_daily_scheduler(h.now)
        return await ui._run_cave_public_background_scheduler(h.now, {})

    asyncio.run(recover())
    assert h.owner[results.STATE_KEY]["phase"] == "complete"
    assert {key: value for key, value in h.owner.items() if key != results.STATE_KEY} == {
        key: value for key, value in before.items() if key != results.STATE_KEY}
    assert h.calls == calls
    for mock in (h.loader, h.flow, h.audit, h.capture_entry, h.send):
        mock.assert_not_awaited()
    h.result_save.assert_called_once()


@pytest.mark.parametrize("kind", ["invalid", "unknown", "rebound", "complete_rebound"])
def test_account_shared_hold_cannot_be_evaded_by_a_sibling_or_rebind(native, kind):
    h = native
    if kind == "invalid":
        h.owner[operations.STATE_KEY] = {"invalid": True}
    else:
        if kind != "complete_rebound":
            def unknown(request):
                if request["safe_summary"]["endpoint"] == "start":
                    return panel()
                raise OSError("fixture unresolved enter")
            h.transport = unknown
        asyncio.run(call(h, "public"))
        if "rebound" in kind:
            state_module.set_identity_account(IDENTITY, IDENTITY + 100)
    sibling = IDENTITY + 1
    state_module.set_identity_account(sibling, IDENTITY)
    state_module.update_send_as_profile(sibling, username="treasure_sibling", enabled=False)
    assert operations.hold_reason(sibling) == "account_operation_pending"
    assert runtime.authorize_cave_treasure_miniapp_manual_run(sibling) == 0
    h.loader.reset_mock()
    response = asyncio.run(runtime.run_cave_public_treasure(sibling, URL, now=h.now + 86400))
    assert not response["ok"] and response["extra"]["persistence_only"]
    h.loader.assert_not_awaited()


@pytest.mark.parametrize("change", ["delete", "replace", "rebind", "pause", "disable"])
def test_owner_or_admission_change_during_intent_save_stops_transport(native, change):
    h = native

    def save():
        if h.owner[operations.STATE_KEY]["checkpoint"]["phase"] == "intent":
            change_owner(h, change)
        return True

    h.ops_save.side_effect = save
    response = asyncio.run(call(h, "public"))
    assert not response["ok"]
    assert h.calls == ["start"]
    assert not state_module.get_inventory_delta_records()
    if change in {"pause", "disable"}:
        checkpoint = h.owner[operations.STATE_KEY]["checkpoint"]
        assert checkpoint["pending"] == {}
        assert checkpoint["resolution"]["kind"] == "not_sent"
        assert not checkpoint["action_dispatched"]


@pytest.mark.parametrize("caller", ["public", "command"])
def test_uncheckpointed_returned_mutations_are_not_trusted(native, caller):
    h = native
    h.flow.side_effect = None
    h.flow.return_value = h.result
    asyncio.run(call(h, caller))
    assert h.owner[results.STATE_KEY].get("invalid")
    assert not state_module.get_inventory_delta_records()
    assert not runtime.authorize_cave_treasure_miniapp_manual_run(IDENTITY)


def test_pending_linked_result_respects_original_checkpoint(native):
    h = native
    h.result_save.return_value = False
    asyncio.run(call(h, "public"))
    assert h.owner[results.STATE_KEY]["phase"] == "pending"
    h.owner[operations.STATE_KEY]["checkpoint"]["error"] = "changed checkpoint"
    assert operations.valid_record(h.owner[operations.STATE_KEY])
    h.result_save.return_value = True
    h.result_save.reset_mock()
    before = deepcopy(state_module._meta_state)
    assert not results.recovery_due(IDENTITY)
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert not response["ok"] and response["extra"]["persistence_only"]
    assert state_module._meta_state == before
    h.result_save.assert_not_called()


@pytest.mark.parametrize("change", ["delete_journal", "change_materials", "change_unknown", "change_account", "change_state", "change_clock"])
def test_linked_completion_does_not_authorize_corrupt_or_unpaired_evidence(native, change):
    h = native
    asyncio.run(call(h, "public"))
    if change == "delete_journal":
        h.owner[operations.STATE_KEY] = {}
    elif change == "change_materials":
        record = h.owner[results.STATE_KEY]
        record["response"]["extra"]["rewards"]["fixture_item"] = 2
        record["inventory"]["record"]["items"]["fixture_item"] = 2
        assert results.valid_record(record)
    elif change == "change_unknown":
        h.owner[operations.STATE_KEY]["checkpoint"]["pending"] = {
            "action": "enter", "session_key": "", "target_index": 0,
        }
        h.owner[operations.STATE_KEY]["checkpoint"]["ok"] = False
    elif change == "change_state":
        h.owner[results.STATE_KEY]["miniapp"]["record"]["state"]["action_remaining"] = 123
    elif change == "change_clock":
        h.owner[results.STATE_KEY]["created_at"] += 86400
    else:
        h.owner[results.STATE_KEY]["account_id"] += 1
    before_calls = list(h.calls)
    assert runtime.authorize_cave_treasure_miniapp_manual_run(IDENTITY) == 0
    response = asyncio.run(call(h, "public"))
    assert not response["ok"]
    assert h.calls == before_calls


def test_local_retry_is_throttled_and_preserves_original_observation_day(native):
    h = native
    asyncio.run(uncommitted_worker(h))
    observed = h.owner[operations.STATE_KEY]["updated_at"]
    h.result_save.return_value = False
    assert ui._recover_cave_treasure_result_once(h.now)["persistence_only"]
    assert ui._recover_cave_treasure_result_once(h.now + 59) is None
    assert ui._recover_cave_treasure_result_once(h.now + 60)["persistence_only"]
    assert h.result_save.call_count == 2
    h.result_save.return_value = True
    h.now += 86400
    recovered = runtime.recover_cave_treasure_result(IDENTITY)
    assert recovered["extra"]["persistence_saved"]
    assert h.owner[results.STATE_KEY]["created_at"] == observed
    assert ui._cave_public_background_action_due("treasure", IDENTITY, h.now)
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()


def frames_for_run():
    frames = []
    run(scripted_transport([], limit=1), checkpoint=lambda frame: frames.append(deepcopy(frame)) or True)
    return frames


@pytest.mark.parametrize("change,prefix", [
    ("skip_sequence", 1), ("search_before_enter", 1), ("clear_intent", 2),
    ("wrong_search_session", 4), ("wrong_receipt_session", 6), ("remove_receipt", 7),
    ("change_receipt", 7), ("regress_dispatch", 4), ("reenter_active_round", 3),
])
def test_checkpoint_transition_rejects_reordered_or_foreign_evidence(native, change, prefix):
    h = native
    frames = frames_for_run()
    if change == "search_before_enter":
        forged = deepcopy(frames[3])
        forged["state"] = deepcopy(frames[0]["state"])
    elif change == "clear_intent":
        forged = deepcopy(frames[0])
        forged["action_dispatched"] = True
    elif change == "wrong_search_session":
        forged = deepcopy(frames[4])
        forged["state"]["session_key"] = "f" * 64
    elif change == "wrong_receipt_session":
        forged = deepcopy(frames[6])
        forged["receipts"][0]["session_key"] = "f" * 64
    elif change in {"remove_receipt", "change_receipt"}:
        forged = deepcopy(frames[6])
        forged["phase"] = "complete"
        if change == "remove_receipt":
            forged["receipts"] = []
        else:
            forged["receipts"][0]["rewards"]["fixture_item"] = 99
    elif change == "regress_dispatch":
        forged = deepcopy(frames[4])
        forged["action_dispatched"] = False
    elif change == "reenter_active_round":
        forged = deepcopy(frames[1])
        forged["state"] = deepcopy(frames[2]["state"])
        forged["action_dispatched"] = True
    else:
        forged = deepcopy(frames[1])
    forged["sequence"] = prefix + (2 if change == "skip_sequence" else 1)

    async def scenario():
        projection = results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY))
        writer = operations.CheckpointWriter(projection, player_id=IDENTITY)
        for frame in frames[:prefix]:
            assert writer(frame)
        before = deepcopy(h.owner)
        h.ops_save.reset_mock()
        assert not writer(forged)
        assert h.owner == before
        h.ops_save.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["phase", "pending.action", "pending.session_key", "state.in_round", "receipts"])
@pytest.mark.parametrize("value", [False, None, [], {}, 1])
def test_checkpoint_fields_accept_only_their_declared_types(field, value):
    frame = frames_for_run()[1]
    scope = frame
    keys = field.split(".")
    for key in keys[:-1]:
        scope = scope[key]
    scope[keys[-1]] = value
    valid = field == "state.in_round" and value is False or field == "receipts" and isinstance(value, list)
    assert operations.valid_checkpoint(frame) is valid


def test_exact_duplicate_checkpoint_is_idempotent_and_not_a_new_write(native):
    h = native
    frame = frames_for_run()[0]

    async def scenario():
        writer = operations.CheckpointWriter(results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY)),
                                             player_id=IDENTITY)
        assert writer(frame)
        assert writer(deepcopy(frame))
        h.ops_save.assert_called_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase,action", [("response", ""), ("intent", "enter"), ("settled", ""), ("complete", "")])
def test_cancellation_inside_checkpoint_save_is_propagated_without_more_gameplay(native, phase, action):
    h = native
    interrupted = []

    def save():
        frame = h.owner[operations.STATE_KEY]["checkpoint"]
        if not interrupted and frame["phase"] == phase and frame["pending"].get("action", "") == action:
            interrupted.append(deepcopy(frame))
            raise asyncio.CancelledError
        return True

    h.ops_save.side_effect = save
    with pytest.raises(MiniAppFlowCancelled):
        asyncio.run(call(h, "public"))
    assert interrupted
    if phase in {"response", "intent"}:
        assert h.calls == ["start"]
    else:
        assert h.calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
        assert len(h.owner[operations.STATE_KEY]["checkpoint"]["receipts"]) == 1
    assert not runtime.is_cave_treasure_busy(IDENTITY)


def test_sqlite_result_abort_retains_journal_and_commits_after_reload(treasure_db):
    h = treasure_db
    asyncio.run(uncommitted_worker(h))
    conn = persistence.get_db_conn()
    conn.execute("""CREATE TRIGGER reject_result BEFORE UPDATE OF treasure_result ON identity_runtime_state
                    WHEN NEW.treasure_result != OLD.treasure_result
                    BEGIN SELECT RAISE(ABORT, 'fixture result abort'); END""")
    conn.commit()
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"]["status"] == "persistence_pending"
    pending = deepcopy(h.owner[results.STATE_KEY])
    with sqlite3.connect(persistence.DB_FILE) as reader:
        assert json.loads(reader.execute("SELECT treasure_result FROM identity_runtime_state WHERE send_as_id=?",
                                         (IDENTITY,)).fetchone()[0]) == {}
        for key in ("inventory_delta_records", "miniapp_state_records"):
            assert json.loads(reader.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]) == {}
    conn.execute("DROP TRIGGER reject_result")
    conn.commit()
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    h.owner = state_module.get_identity_state(IDENTITY)
    assert h.owner[results.STATE_KEY] == pending
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"]["persistence_saved"]
    assert runtime.recover_cave_treasure_result(IDENTITY) is None
    assert state_module.get_inventory_delta_records()[operations.inventory_key(h.owner[operations.STATE_KEY])]["items"] == {"fixture_item": 1}
    h.loader.assert_not_awaited()
    h.flow.assert_not_awaited()


def test_sqlite_receipt_failure_keeps_pending_fact_for_normal_flush_and_reload(treasure_db):
    h = treasure_db
    assert persistence.save_state()
    conn = persistence.get_db_conn()
    conn.execute("""CREATE TRIGGER reject_receipt BEFORE UPDATE OF treasure_operation ON identity_runtime_state
                    WHEN json_array_length(NEW.treasure_operation, '$.checkpoint.receipts') > 0
                    BEGIN SELECT RAISE(ABORT, 'fixture receipt abort'); END""")
    conn.commit()
    response = asyncio.run(call(h, "public"))
    assert response["extra"]["persistence_only"]
    record = deepcopy(h.owner[operations.STATE_KEY])
    assert record["pending_save"] and len(record["checkpoint"]["receipts"]) == 1
    assert h.owner[results.STATE_KEY] == {}
    with sqlite3.connect(persistence.DB_FILE) as reader:
        original = json.loads(reader.execute("SELECT treasure_operation FROM identity_runtime_state WHERE send_as_id=?",
                                             (IDENTITY,)).fetchone()[0])
        assert original["checkpoint"]["phase"] == "intent"
        assert original["checkpoint"]["pending"]["action"] == "settle"
    conn.execute("DROP TRIGGER reject_receipt")
    conn.commit()
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    h.owner = state_module.get_identity_state(IDENTITY)
    assert h.owner[operations.STATE_KEY] == record
    before_calls = list(h.calls)
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"]["persistence_saved"]
    assert not h.owner[operations.STATE_KEY]["pending_save"]
    assert h.calls == before_calls
    assert runtime.recover_cave_treasure_result(IDENTITY) is None


def test_timed_out_queued_checkpoint_cannot_commit_later(native, monkeypatch):
    h = native
    callbacks = []
    monkeypatch.setattr(operations, "CHECKPOINT_TIMEOUT_SEC", 0.01)

    async def scenario():
        writer = operations.CheckpointWriter(results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY)),
                                             player_id=IDENTITY)
        writer.loop = SimpleNamespace(call_soon_threadsafe=callbacks.append)
        assert not await asyncio.to_thread(writer, frames_for_run()[0])
        assert len(callbacks) == 1
        callbacks[0]()

    asyncio.run(scenario())
    h.ops_save.assert_not_called()
    assert h.owner[operations.STATE_KEY] == {}


def test_checkpoint_timeout_after_save_started_drains_exact_acknowledgement(native, monkeypatch):
    h = native
    started, expired, draining = threading.Event(), threading.Event(), threading.Event()

    class TimeoutOnce(Future):
        def result(self, timeout=None):
            if timeout is not None and not expired.is_set():
                assert started.wait(2)
                expired.set()
                raise TimeoutError()
            if timeout is None:
                draining.set()
                timeout = 2
            return super().result(timeout=timeout)

    def save():
        if not started.is_set():
            started.set()
            assert expired.wait(2) and draining.wait(2)
        return True

    monkeypatch.setattr(operations, "Future", TimeoutOnce)
    h.ops_save.side_effect = save
    response = asyncio.run(call(h, "public"))
    assert response["ok"], response
    assert draining.is_set()
    assert len(h.owner[operations.STATE_KEY]["checkpoint"]["receipts"]) == 1


@pytest.mark.parametrize("code", [408, 425, 429, 500, 503])
def test_server_wait_survives_checkpoint_result_and_local_recovery(native, code):
    h = native

    def transport(request):
        if request["safe_summary"]["endpoint"] == "start":
            return panel()
        return code, {"ok": False, "error": "fixture busy"}, {"Retry-After": "91"}

    h.transport = transport
    asyncio.run(uncommitted_worker(h))
    record = h.owner[operations.STATE_KEY]
    assert record["checkpoint"]["retry_after_sec"] == 91
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"]["persistence_saved"] and response["extra"]["retry_after_sec"] == 91
    assert operations.hold_reason(IDENTITY) == "outcome_unknown_hold"


def test_same_session_material_errors_keep_confirmed_native_subset(native):
    h = native
    base = h.transport

    def transport(request):
        data = base(request)
        if request["safe_summary"]["endpoint"] == "hunt_settle":
            data["huntResult"]["loot"].append({"name": "invalid_item", "quantity": "many"})
            data["huntResult"]["log"] = ["\u4fee\u4e3a+25", "\u5929\u673a\u6b8b\u75d5+1"]
            data["huntResult"]["contribution"] = 10
        return data

    h.transport = transport
    response = asyncio.run(call(h, "public"))
    assert response["ok"], response
    assert response["extra"]["rewards"] == {"fixture_item": 1}
    assert response["extra"]["gains"] == {"\u4fee\u4e3a": 25, "\u5929\u673a\u6b8b\u75d5": 1, "\u8d21\u732e": 10}
    assert h.owner[operations.STATE_KEY]["checkpoint"]["receipts"][0]["material_error"]
    assert not operations.hold_reason(IDENTITY)


@pytest.mark.parametrize("pause_source", ["manual", "persistence_guard", "bot_health_monitor", "tianzun_maintenance"])
def test_paused_main_loop_recovers_journal_without_resuming_gameplay(native, monkeypatch, pause_source):
    h = native
    asyncio.run(uncommitted_worker(h))
    state_module.set_global_enabled(False)
    state_module.set_global_pause_source(pause_source)
    for name in ("gc_my_msg_ids", "gc_ui_login_tokens", "gc_ui_sessions", "_cancel_identity_schedulers", "check_bot_health_timeout"):
        monkeypatch.setattr(app, name, Mock())
    monkeypatch.setattr(app, "flush_if_dirty", Mock(return_value=True))
    monkeypatch.setattr(app, "has_persistence_write_failure", lambda: False)
    monkeypatch.setattr(app, "should_pause_for_bot_health", lambda: False)
    monkeypatch.setattr(app, "run_account_target_membership_probe_scheduler", AsyncMock())
    dispatch = AsyncMock(side_effect=AssertionError("paused gameplay must not dispatch"))
    monkeypatch.setattr(app, "_run_global_schedulers", dispatch)

    async def sleep(stop_event, _delay):
        stop_event.set()
    monkeypatch.setattr(app, "_sleep_or_stop", sleep)

    async def scenario():
        await app.main_loop(asyncio.Event())
    asyncio.run(scenario())
    assert h.owner[results.STATE_KEY]["phase"] == "complete"
    assert not state_module.get_global_enabled()
    assert state_module.get_global_pause_source() == pause_source
    for mock in (dispatch, h.flow, h.loader, h.send, h.audit):
        mock.assert_not_awaited()
    h.result_save.assert_called_once()


def test_signed_selected_player_is_preserved_in_journal_and_every_request(native):
    h = native
    selected = -1_000_000_000_000 - IDENTITY
    h.session["player_id"] = selected
    base, players = h.transport, []

    def transport(request):
        players.append(request["payload"]["playerId"])
        return base(request)

    h.transport = transport
    response = asyncio.run(call(h, "public"))
    assert response["ok"], response
    assert set(players) == {selected} and len(players) == 4
    assert h.owner[operations.STATE_KEY]["player_id"] == selected
    assert operations.valid_record(h.owner[operations.STATE_KEY])


def test_next_day_can_start_new_owned_operation_only_after_known_completion(native):
    h = native
    first = asyncio.run(call(h, "public"))
    assert first["ok"]
    operation_id = h.owner[operations.STATE_KEY]["operation_id"]
    h.now += 86400
    base = scripted_transport(h.calls, limit=1)

    def transport(request):
        data = base(request)
        for key in ("huntRun", "huntResult"):
            if key in data:
                data[key]["sessionId"] = "fixture-next-day"
        return data

    h.transport = transport
    second = asyncio.run(call(h, "public"))
    assert second["ok"], second
    assert operation_id != h.owner[operations.STATE_KEY]["operation_id"]
    assert len(set(state_module.get_inventory_delta_records()) - {"_meta"}) == 2
    assert not operations.hold_reason(IDENTITY)


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("failure", ["http", "rejected", "wrong_player", "checkpoint"])
def test_new_read_failure_preserves_the_previous_completed_operation(native, caller, failure):
    h = native
    assert asyncio.run(call(h, "public"))["ok"]
    previous_operation = deepcopy(h.owner[operations.STATE_KEY])
    previous_result = deepcopy(h.owner[results.STATE_KEY])
    inventory = deepcopy(state_module.get_inventory_delta_records())
    snapshot = deepcopy(state_module.get_miniapp_state_records())
    h.now += 86400
    h.calls.clear()
    h.result_save.reset_mock()

    def transport(request):
        h.calls.append(request["safe_summary"]["endpoint"])
        if failure == "http":
            return 503, "fixture read unavailable"
        if failure == "rejected":
            return 403, {"ok": False, "error": "fixture entry expired"}
        data = panel()
        if failure == "wrong_player":
            data["account"]["playerId"] = IDENTITY + 1
        return data

    h.transport = transport
    if failure == "checkpoint":
        h.ops_save.return_value = False
    response = asyncio.run(call(h, caller))
    if caller == "public":
        assert not response["ok"]
        assert not response["extra"].get("state_record_key")
        assert not response["extra"].get("inventory_record_key")
    else:
        assert response is True
    assert h.calls == ["start"]
    assert h.owner[operations.STATE_KEY] == previous_operation
    assert h.owner[results.STATE_KEY] == previous_result
    assert state_module.get_inventory_delta_records() == inventory
    assert state_module.get_miniapp_state_records() == snapshot
    assert not operations.hold_reason(IDENTITY)
    assert runtime.recover_cave_treasure_result(IDENTITY) is None
    h.result_save.assert_not_called()


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("previous", [False, True])
def test_initial_daily_limit_commits_a_linked_read_only_operation(native, caller, previous):
    h = native
    if previous:
        assert asyncio.run(call(h, "public"))["ok"]
        h.now += 86400
    inventory = deepcopy(state_module.get_inventory_delta_records())
    previous_id = h.owner[operations.STATE_KEY].get("operation_id")
    h.calls.clear()

    def transport(request):
        h.calls.append(request["safe_summary"]["endpoint"])
        return 409, {"ok": False, "error": "hunt_daily_limit", "account": {"playerId": IDENTITY}}

    h.transport = transport
    response = asyncio.run(call(h, caller))
    if caller == "public":
        assert response["ok"] and response["extra"]["daily_exhausted"], response
    else:
        assert response is True
    assert h.calls == ["start"]
    record = h.owner[operations.STATE_KEY]
    assert operations.valid_record(record)
    assert record["operation_id"] != previous_id
    assert not record["checkpoint"]["action_dispatched"]
    assert record["checkpoint"]["phase"] == "complete"
    assert operations.result_matches(MiniAppIdentityOwner.capture(IDENTITY), h.owner[results.STATE_KEY])
    assert state_module.get_inventory_delta_records() == inventory
    assert not operations.hold_reason(IDENTITY)
    assert runtime.recover_cave_treasure_result(IDENTITY) is None


@pytest.mark.parametrize("failure", ["http", "checkpoint"])
def test_successor_read_failure_survives_sqlite_reload_and_allows_next_round(treasure_db, failure):
    h = treasure_db
    assert asyncio.run(call(h, "public"))["ok"]
    previous_operation = deepcopy(h.owner[operations.STATE_KEY])
    previous_result = deepcopy(h.owner[results.STATE_KEY])
    h.now += 86400
    h.calls.clear()
    if failure == "checkpoint":
        conn = persistence.get_db_conn()
        conn.execute("""CREATE TRIGGER reject_successor BEFORE UPDATE OF treasure_operation ON identity_runtime_state
                        WHEN json_extract(NEW.treasure_operation, '$.operation_id') !=
                             json_extract(OLD.treasure_operation, '$.operation_id')
                        BEGIN SELECT RAISE(ABORT, 'fixture successor abort'); END""")
        conn.commit()

    def unavailable(request):
        h.calls.append(request["safe_summary"]["endpoint"])
        return (503, "fixture read unavailable") if failure == "http" else panel()

    h.transport = unavailable
    assert not asyncio.run(call(h, "public"))["ok"]
    assert h.calls == ["start"]
    assert h.owner[operations.STATE_KEY] == previous_operation
    assert h.owner[results.STATE_KEY] == previous_result
    if failure == "checkpoint":
        conn.execute("DROP TRIGGER reject_successor")
        conn.commit()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    h.owner = state_module.get_identity_state(IDENTITY)
    assert h.owner[operations.STATE_KEY] == previous_operation
    assert h.owner[results.STATE_KEY] == previous_result
    assert runtime.recover_cave_treasure_result(IDENTITY) is None
    assert not operations.hold_reason(IDENTITY)
    base = scripted_transport(h.calls, limit=1)

    def next_round(request):
        data = base(request)
        for key in ("huntRun", "huntResult"):
            if key in data:
                data[key]["sessionId"] = "fixture-successor-round"
        return data

    h.calls.clear()
    h.transport = next_round
    assert asyncio.run(call(h, "public"))["ok"]
    assert h.calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
    assert len(set(state_module.get_inventory_delta_records()) - {"_meta"}) == 2


@pytest.mark.parametrize("unknown", [False, True])
def test_unjournaled_facts_cannot_replace_a_linked_completion(native, unknown):
    h = native
    assert asyncio.run(call(h, "public"))["ok"]
    record = deepcopy(h.owner[operations.STATE_KEY])
    inventory = deepcopy(state_module.get_inventory_delta_records())
    snapshot = deepcopy(state_module.get_miniapp_state_records())
    projection = results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY))
    result = operations.projected_result(record)
    if unknown:
        result.update(ok=False, status="result_unknown", outcome_unknown=True)
        result["data"]["state"].update(outcome_unknown=True, outcome_unknown_action="enter")
    h.result_save.reset_mock()
    response = runtime._commit_cave_treasure_result(projection, result, now=h.now + 1)
    assert not response["ok"] and response["extra"]["persistence_only"]
    assert h.owner[results.STATE_KEY].get("invalid")
    assert h.owner[operations.STATE_KEY] == record
    assert state_module.get_inventory_delta_records() == inventory
    assert state_module.get_miniapp_state_records() == snapshot
    assert operations.hold_reason(IDENTITY)
    h.result_save.assert_not_called()
