import asyncio
from copy import deepcopy
import json
import sqlite3
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, inventory_delta, miniapp_state, persistence, state as state_module, ui
from model.features import cave_treasure_runtime as runtime
from model.features import treasure_results as results
from model.features.miniapp_common import MiniAppFlowCancelled, MiniAppIdentityOwner
from test_treasure_lifecycle import IDENTITY, INIT, TOKEN, URL, call, change_owner, receipt
from test_treasure_lifecycle import h as h


@pytest.fixture
def commits(h, monkeypatch):
    for name, original in h.originals.items():
        if name != "_record_cave_treasure_business_capture":
            monkeypatch.setattr(runtime, name, original)
    save = Mock(return_value=True)
    for module in (runtime, inventory_delta, miniapp_state, runtime.treasure_results):
        monkeypatch.setattr(module, "save_state", save)
    h.save = save
    monkeypatch.setattr(ui, "_cave_public_background_retry_at", {})
    monkeypatch.setattr(ui, "_cave_public_background_daily_done", set())
    h.send = AsyncMock()
    monkeypatch.setattr(ui, "send_game_command", h.send)
    return h


@pytest.mark.parametrize("failure", [False, None, "true", "exception"])
def test_failed_projection_save_is_not_daily_completion(commits, failure):
    h = commits
    before_inventory = deepcopy(state_module.get_inventory_delta_records())
    before_state = deepcopy(state_module.get_miniapp_state_records())
    if failure == "exception":
        h.save.side_effect = OSError("fixture disk full")
    else:
        h.save.return_value = failure
    response = asyncio.run(call(h, "public"))
    assert not response["ok"]
    assert response["extra"]["status"] == "persistence_pending"
    assert not response["extra"].get("daily_exhausted")
    assert state_module.get_inventory_delta_records() == before_inventory
    assert state_module.get_miniapp_state_records() == before_state
    assert h.owner.get("treasure_result")


def test_inventory_state_and_local_completion_share_one_commit(commits):
    h = commits
    writes = []

    def save():
        writes.append((deepcopy(h.owner), deepcopy(state_module.get_inventory_delta_records()),
                       deepcopy(state_module.get_miniapp_state_records())))
        return True

    h.save.side_effect = save
    result = asyncio.run(call(h, "public"))
    assert result["ok"]
    assert len(writes) == 1
    owner, inventory, records = writes[0]
    assert owner["treasure_result"]["phase"] == "complete"
    assert any(row.get("identity_id") == IDENTITY for row in inventory.values())
    assert f"{IDENTITY}:cave_treasure" in records


def test_next_call_recovers_local_result_without_repeating_game(commits):
    h = commits
    h.save.return_value = False
    asyncio.run(call(h, "public"))
    h.save.return_value = True
    result = asyncio.run(call(h, "public"))
    assert h.flow.await_count == 1
    assert h.loader.await_count == 1
    assert result["extra"]["persistence_only"]
    assert h.owner["treasure_result"]["phase"] == "complete"


def stage_pending(h):
    h.save.return_value = False
    response = asyncio.run(call(h, "public"))
    assert response["extra"]["status"] == "persistence_pending"
    assert results.valid_record(h.owner[results.STATE_KEY])
    h.save.return_value = True
    h.save.reset_mock()
    for mock in (h.flow, h.loader, h.audit, h.capture_entry):
        mock.reset_mock()
    return deepcopy(h.owner[results.STATE_KEY])


async def recover(h, caller):
    if caller in {"public", "command"}:
        return await call(h, caller)
    if caller == "ui":
        return await ui.ui_run_cave_public_entry(IDENTITY, "treasure", "")
    if caller == "ui_command":
        return await ui.ui_send_miniapp_manual_run(IDENTITY, "cave_treasure")
    if caller == "scheduler":
        return await ui.run_miniapp_daily_scheduler(h.now)
    if caller == "background":
        return await ui._run_cave_public_background_scheduler(h.now, {})
    raise AssertionError(caller)


@pytest.mark.parametrize("caller", ["public", "command", "ui", "ui_command", "scheduler", "background"])
@pytest.mark.parametrize("control", ["unchanged", "pause", "disable", "config"])
def test_all_callers_recover_locally_even_with_gameplay_disabled(commits, caller, control):
    h = commits
    pending = stage_pending(h)
    if control in {"pause", "disable"}:
        change_owner(h, control)
    elif control == "config":
        state_module.set_miniapp_auto_config({"cave_public_treasure_enabled": False, "cave_public_entry_url": ""})
    before = deepcopy(h.owner)
    storage = deepcopy(state_module.get_storage_bag_records())
    asyncio.run(recover(h, caller))
    assert h.owner[results.STATE_KEY]["phase"] == "complete"
    assert h.owner[results.STATE_KEY]["operation_id"] == pending["operation_id"]
    assert {k: v for k, v in h.owner.items() if k != results.STATE_KEY} == {
        k: v for k, v in before.items() if k != results.STATE_KEY}
    assert state_module.get_storage_bag_records() == storage
    h.save.assert_called_once()
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()
    h.capture_entry.assert_not_awaited()
    h.audit.assert_not_awaited()
    h.send.assert_not_awaited()


def test_repeated_local_recovery_is_idempotent(commits):
    h = commits
    stage_pending(h)
    recovered = runtime.recover_cave_treasure_result(IDENTITY)
    assert recovered["extra"]["persistence_saved"]
    assert not recovered["extra"]["daily_exhausted"]
    before = deepcopy(state_module._meta_state)
    for _ in range(3):
        assert runtime.recover_cave_treasure_result(IDENTITY) is None
    assert state_module._meta_state == before
    h.save.assert_called_once()


@pytest.mark.parametrize("caller", ["public", "command", "ui", "ui_command"])
@pytest.mark.parametrize("failure", [False, None, "true", 1, "exception"])
def test_failed_local_recovery_never_repeats_game_or_claims_completion(commits, caller, failure):
    h = commits
    pending = stage_pending(h)
    if failure == "exception":
        h.save.side_effect = OSError("fixture disk full")
    else:
        h.save.return_value = failure
    for _ in range(2):
        asyncio.run(recover(h, caller))
    assert h.owner[results.STATE_KEY] == pending
    assert not state_module.get_inventory_delta_records()
    assert not state_module.get_miniapp_state_records()
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()
    h.audit.assert_not_awaited()
    h.capture_entry.assert_not_awaited()
    h.send.assert_not_awaited()


def test_scheduler_throttles_only_local_retries(commits):
    h = commits
    stage_pending(h)
    h.save.return_value = False
    assert ui._recover_cave_treasure_result_once(h.now)["persistence_only"]
    assert ui._recover_cave_treasure_result_once(h.now + 59) is None
    assert ui._recover_cave_treasure_result_once(h.now + 60)["persistence_only"]
    assert h.save.call_count == 2
    assert not ui._cave_public_background_action_due("treasure", IDENTITY, h.now + 60)
    assert ui._cave_public_background_daily_done == set()


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("saved", [False, True])
def test_cancelled_worker_retains_confirmed_materials_before_release(commits, caller, saved):
    h = commits
    h.flow.side_effect = MiniAppFlowCancelled(h.result)
    h.save.return_value = saved
    with pytest.raises(MiniAppFlowCancelled) as error:
        asyncio.run(call(h, caller))
    record = h.owner[results.STATE_KEY]
    assert results.valid_record(record)
    assert record["phase"] == ("complete" if saved else "pending")
    assert record["response"]["extra"]["rewards"] == {"fixture_item": 1}
    assert not runtime.is_cave_treasure_busy(IDENTITY)
    if not saved:
        assert error.value.result["extra"]["status"] == "persistence_pending"
        h.save.return_value = True
        assert runtime.recover_cave_treasure_result(IDENTITY)["extra"]["persistence_saved"]
    assert h.flow.await_count == 1


def test_cancellation_during_save_rolls_back_and_is_propagated(commits):
    h = commits
    h.save.side_effect = asyncio.CancelledError
    with pytest.raises(MiniAppFlowCancelled) as error:
        asyncio.run(call(h, "public"))
    assert error.value.result["extra"]["persistence_only"]
    assert h.owner[results.STATE_KEY]["phase"] == "pending"
    assert not state_module.get_inventory_delta_records()
    assert not state_module.get_miniapp_state_records()
    assert not runtime.is_cave_treasure_busy(IDENTITY)


@pytest.mark.parametrize("result_kind", ["unknown", "empty_loot", "no_material", "rate_limit"])
def test_nonstandard_results_keep_known_facts_and_recovery_meaning(commits, result_kind):
    h = commits
    if result_kind == "unknown":
        h.result.update(ok=False, status="result_unknown", outcome_unknown=True)
    elif result_kind == "empty_loot":
        h.result["data"]["results"] = [{**receipt(), "loot": []}]
    elif result_kind == "no_material":
        h.result["data"]["results"] = []
    else:
        h.result.update(ok=False, status="blocked", error="external_action_rate_limited",
                        shared_rate_limit=True, retry_after_sec=300)
    pending = stage_pending(h)
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"]["persistence_saved"]
    assert response["extra"]["settled_count"] == (0 if result_kind == "no_material" else 1)
    assert not response["extra"]["daily_exhausted"]
    assert h.owner[results.STATE_KEY]["response"] == pending["response"]
    if result_kind == "unknown":
        state_module.set_miniapp_state_records({})
        held = asyncio.run(call(h, "public"))
        assert not held["ok"]
        assert held["extra"]["reason"] == "outcome_unknown_hold"
        h.flow.assert_not_awaited()
    if result_kind == "rate_limit":
        assert response["extra"]["shared_rate_limit"]
        assert response["extra"]["retry_after_sec"] == 300


@pytest.mark.parametrize("kind", ["inventory", "miniapp"])
@pytest.mark.parametrize("when", ["during_game", "during_recovery"])
def test_changed_projection_base_is_retained_without_overwrite(commits, kind, when):
    h = commits
    getter, setter = ((state_module.get_inventory_delta_records, state_module.set_inventory_delta_records)
                      if kind == "inventory" else (state_module.get_miniapp_state_records, state_module.set_miniapp_state_records))
    prepared = h.originals["_record_cave_treasure_inventory_delta" if kind == "inventory"
                           else "_record_cave_treasure_miniapp_state"](IDENTITY, h.result, now=h.now, prepare=True)
    key = prepared["record_key"]
    newer = {"newer": True, "state": {"outcome_unknown": False}, "updated_at": h.now + 1}
    if when == "during_game":
        async def game(*_args, **_kwargs):
            setter({key: deepcopy(newer)})
            return h.result
        h.flow.side_effect = game
        response = asyncio.run(call(h, "public"))
    else:
        stage_pending(h)
        setter({key: deepcopy(newer)})
        response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"]["reason"] == "result_basis_changed"
    assert getter() == {key: newer}
    assert results.valid_record(h.owner[results.STATE_KEY])
    assert h.owner[results.STATE_KEY]["phase"] == "pending"
    assert not results.recovery_due(IDENTITY)
    h.save.assert_not_called()


@pytest.mark.parametrize("kind", ["inventory", "miniapp"])
@pytest.mark.parametrize("replacement", [None, False, 0, {}, []])
def test_missing_and_falsey_projection_bases_are_not_equivalent(commits, kind, replacement):
    h = commits
    record = stage_pending(h)
    setter = state_module.set_inventory_delta_records if kind == "inventory" else state_module.set_miniapp_state_records
    setter({record[kind]["key"]: replacement})
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"]["reason"] == "result_basis_changed"
    assert h.owner[results.STATE_KEY] == record
    h.save.assert_not_called()


@pytest.mark.parametrize("failure", [False, "exception"])
@pytest.mark.parametrize("replace", ["unrelated", "same_key", "owner", "slot"])
def test_rollback_preserves_concurrent_changes_and_replacement_ownership(commits, failure, replace):
    h = commits
    replacement = {"replacement": True}
    changed = {}

    def save():
        h.owner["next_deep_retreat_time"] = h.now + 3600
        marker = h.owner[results.STATE_KEY]
        for kind, getter, setter in (("inventory", state_module.get_inventory_delta_records, state_module.set_inventory_delta_records),
                                     ("miniapp", state_module.get_miniapp_state_records, state_module.set_miniapp_state_records)):
            current = deepcopy(getter())
            current["unrelated"] = deepcopy(replacement)
            current["_meta"] = deepcopy(replacement)
            if replace == "same_key":
                current[marker[kind]["key"]] = deepcopy(replacement)
            setter(current)
            changed[kind] = current
        if replace == "owner":
            state_module._meta_state["identity_states"][IDENTITY] = deepcopy(h.owner)
        elif replace == "slot":
            h.owner[results.STATE_KEY] = deepcopy(replacement)
        if failure == "exception":
            raise OSError("fixture disk full")
        return failure

    h.save.side_effect = save
    response = asyncio.run(call(h, "public"))
    assert not response["ok"]
    current_owner = state_module.get_identity_state(IDENTITY)
    assert current_owner["next_deep_retreat_time"] == h.now + 3600
    if replace == "slot":
        assert current_owner[results.STATE_KEY] == replacement
    else:
        assert current_owner[results.STATE_KEY]["phase"] == "pending"
    for kind, getter in (("inventory", state_module.get_inventory_delta_records), ("miniapp", state_module.get_miniapp_state_records)):
        assert getter()["unrelated"] == replacement
        assert getter()["_meta"] == replacement
        if replace == "same_key":
            assert getter() == changed[kind]
        else:
            assert getter() == {"unrelated": replacement, "_meta": replacement}


def test_failed_save_restores_rows_evicted_by_projection_pruning(commits):
    h = commits
    before = {str(index): {"updated_at": h.now - index - 1, "identity_id": IDENTITY + 1}
              for index in range(inventory_delta.INVENTORY_DELTA_RECORD_LIMIT)}
    state_module.set_inventory_delta_records(deepcopy(before))
    h.save.return_value = False
    asyncio.run(call(h, "public"))
    assert state_module.get_inventory_delta_records() == before


@pytest.mark.parametrize("when", ["before_recovery", "after_save"])
def test_account_rebinding_does_not_adopt_old_account_result(commits, when):
    h = commits
    if when == "before_recovery":
        stage_pending(h)
        state_module.set_identity_account(IDENTITY, IDENTITY + 1)
        before = deepcopy(state_module._meta_state)
        response = runtime.recover_cave_treasure_result(IDENTITY)
        assert state_module._meta_state == before
        h.save.assert_not_called()
    else:
        def save():
            state_module.set_identity_account(IDENTITY, IDENTITY + 1)
            return True
        h.save.side_effect = save
        response = asyncio.run(call(h, "public"))
        assert h.owner[results.STATE_KEY]["phase"] == "complete"
    assert not response["ok"]
    assert response["extra"]["reason"] == "owner_changed"
    assert results.hold_reason(IDENTITY) == "owner_changed"


@pytest.mark.parametrize("value", [None, False, 0, "", [], {"invalid": True}])
def test_invalid_record_is_a_hold_for_owner_and_sibling(commits, monkeypatch, value):
    h = commits
    sibling = IDENTITY + 1
    state_module.set_identity_account(sibling, IDENTITY)
    state_module.set_identity_enabled(IDENTITY, False)
    state_module.set_identity_enabled(sibling, True)
    h.owner[results.STATE_KEY] = value
    monkeypatch.setattr(runtime, "_channel_identity_treasure_allowed", lambda _id: True)
    for identity in (IDENTITY, sibling):
        response = asyncio.run(runtime.run_cave_public_treasure(identity, URL, now=h.now))
        assert response["extra"]["status"] == "persistence_pending"
        assert not ui._cave_public_background_action_due("treasure", identity, h.now)
        assert runtime.authorize_cave_treasure_miniapp_manual_run(identity, now=h.now) == 0
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()
    h.save.assert_not_called()


def test_pending_result_holds_original_account_after_rebinding(commits):
    h = commits
    stage_pending(h)
    sibling = IDENTITY + 1
    state_module.set_identity_account(sibling, IDENTITY)
    state_module.set_identity_account(IDENTITY, IDENTITY + 2)
    assert results.hold_reason(sibling) == "account_result_pending"
    assert results.hold_reason(IDENTITY + 2) == "owner_changed"
    state_module.set_identity_account(IDENTITY + 2, IDENTITY + 2)
    assert results.hold_reason(IDENTITY + 2) == "account_result_pending"


@pytest.mark.parametrize("lock_kind", ["public", "account"])
def test_recovery_cannot_run_while_owned_worker_is_active(commits, lock_kind):
    h = commits
    pending = stage_pending(h)

    async def scenario():
        lock = runtime._public_entry_lock(IDENTITY) if lock_kind == "public" else runtime._run_lock(IDENTITY)
        async with lock:
            assert runtime.recover_cave_treasure_result(IDENTITY)["extra"]["status"] == "busy"
            assert (await ui.ui_run_cave_public_entry(IDENTITY, "treasure", URL))[2]["status"] == "busy"
            assert (await ui.ui_send_miniapp_manual_run(IDENTITY, "cave_treasure"))[2]["status"] == "busy"
            assert ui._recover_cave_treasure_result_once(h.now) is None
    asyncio.run(scenario())
    assert h.owner[results.STATE_KEY] == pending
    h.save.assert_not_called()
    h.send.assert_not_awaited()


def test_queued_manual_entry_rechecks_result_hold(commits):
    h = commits

    async def send(*_args, **kwargs):
        check = kwargs["operation_check"]
        assert check()
        h.owner[results.STATE_KEY] = {"invalid": True}
        assert not check()
        return None
    h.send.side_effect = send
    response = asyncio.run(ui.ui_send_miniapp_manual_run(IDENTITY, "cave_treasure"))
    assert not response[0]
    assert not runtime._has_manual_auth(IDENTITY, h.now)


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("failure", ["raise", "cancel"])
def test_notification_failure_never_replays_a_committed_projection(commits, caller, failure):
    h = commits

    async def audit(message, **_kwargs):
        if "fixture_item" in message:
            raise asyncio.CancelledError if failure == "cancel" else RuntimeError("fixture audit failed")
    h.audit.side_effect = audit
    if failure == "cancel":
        with pytest.raises(MiniAppFlowCancelled) as error:
            asyncio.run(call(h, caller))
        assert error.value.result["extra"]["rewards"] == {"fixture_item": 1}
    else:
        asyncio.run(call(h, caller))
    assert h.owner[results.STATE_KEY]["phase"] == "complete"
    assert runtime.recover_cave_treasure_result(IDENTITY) is None
    h.save.assert_called_once()


def test_business_capture_error_does_not_lose_the_result(commits, monkeypatch):
    h = commits
    monkeypatch.setattr(runtime, "_record_cave_treasure_business_capture", Mock(side_effect=OSError("fixture disk full")))
    assert asyncio.run(call(h, "public"))["ok"]
    assert h.owner[results.STATE_KEY]["phase"] == "complete"


@pytest.mark.parametrize("helper", ["_record_cave_treasure_inventory_delta", "_record_cave_treasure_miniapp_state"])
def test_projection_build_failure_blocks_replay(commits, monkeypatch, helper):
    h = commits
    monkeypatch.setattr(runtime, helper, Mock(side_effect=ValueError("fixture invalid data")))
    assert not asyncio.run(call(h, "public"))["ok"]
    assert h.owner[results.STATE_KEY]["invalid"]
    assert not asyncio.run(call(h, "public"))["ok"]
    assert h.flow.await_count == 1
    h.save.assert_not_called()


def test_only_secret_free_bounded_business_data_is_retained(commits):
    h = commits
    h.result["error"] = f"token={TOKEN} {URL} {INIT}"
    h.result["ok"] = False
    h.result["data"]["state"].update(token=TOKEN, initData=INIT, url=URL, sessionId="fixture-session-secret")
    h.result["data"]["results"][0]["sessionId"] = "fixture-session-secret"
    h.result["data"]["diagnostic"] = {"cookie": INIT}
    record = stage_pending(h)
    encoded = json.dumps(record, ensure_ascii=False)
    for value in (TOKEN, INIT, URL, "fixture-session-secret"):
        assert value not in encoded
    assert len(encoded.encode()) <= state_module.TREASURE_RESULT_MAX_BYTES
    assert results.valid_record(record)


@pytest.mark.parametrize("encoded", ["null", "false", "0", '""', "[]", "{broken", '{"v":NaN}',
                                     '{"v":"' + "x" * (256 * 1024) + '"}'])
def test_corrupt_or_oversized_sqlite_value_cannot_release_a_hold(commits, encoded):
    decoded = persistence._deserialize_db_value(results.STATE_KEY, encoded)
    assert decoded != {} and not results.valid_record(decoded)
    commits.owner[results.STATE_KEY] = decoded
    assert results.hold_reason(IDENTITY)


@pytest.mark.parametrize("value", [None, False, 0, "", [], {"value": float("nan")},
                                   {"value": "x" * (256 * 1024)}])
def test_serialization_keeps_invalid_records_as_explicit_holds(value):
    encoded = persistence._serialize_db_value(results.STATE_KEY, value)
    assert json.loads(encoded) == {"invalid": True}


@pytest.mark.parametrize("path,value", [
    ("version", True), ("identity_id", IDENTITY + 1), ("account_id", IDENTITY + 1),
    ("created_at", float("nan")), ("phase", []), ("phase", False), ("operation_id", "new"),
    ("inventory.before", "invalid"), ("inventory.key", "foreign"),
    ("inventory.record.items", {"fixture_item": 2}),
    ("inventory.record.items", {"fixture_item": True}),
    ("inventory.record.source_summary", {"settled_count": True, "result_msg_id": 0}),
    ("miniapp.record.state.owner_account_id", IDENTITY + 1),
    ("miniapp.record.state.outcome_unknown", True),
    ("response.ok", "true"), ("response.extra.daily_exhausted", True),
    ("response.extra.settled_count", 2), ("response.extra.games_used", True),
    ("response.extra.rewards", {"fixture_item": 2}), ("response.extra.state_record_key", "foreign"),
])
def test_corrupt_result_fields_never_project_or_send(commits, path, value):
    h = commits
    record = stage_pending(h)
    parts = path.split(".")
    target = record
    for key in parts[:-1]:
        target = target[key]
    target[parts[-1]] = value
    assert not results.valid_record(record)
    h.owner[results.STATE_KEY] = deepcopy(record)
    response = asyncio.run(call(h, "public"))
    assert response["extra"]["reason"] == "invalid_projection"
    assert not state_module.get_inventory_delta_records()
    assert not state_module.get_miniapp_state_records()
    h.save.assert_not_called()
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()


@pytest.fixture
def treasure_db(commits, monkeypatch, tmp_path):
    h = commits
    for name, value in {
        "DB_FILE": str(tmp_path / "treasure.db"), "_db_conn": None, "_db_initialized": False,
        "_schema_columns_ensured_key": None, "_schema_columns_ensured_version": None,
        "_persistence_snapshot_db_key": "", "_persisted_meta_snapshot": {}, "_persisted_identity_snapshots": {},
        "_state_dirty": False, "_last_flush_time": 0, "_last_save_failed_at": 0, "_last_save_error": "",
    }.items():
        monkeypatch.setattr(persistence, name, value)
    monkeypatch.setattr(persistence, "_try_write_live_guard_backup", Mock(return_value=False))
    monkeypatch.setattr(results, "save_state", persistence.save_state)
    try:
        yield h
    finally:
        if persistence._db_conn is not None:
            persistence._db_conn.close()


def test_sqlite_abort_has_no_partial_projection_and_reload_recovers_once(treasure_db):
    h = treasure_db
    assert persistence.save_state() is True
    conn = persistence.get_db_conn()
    conn.execute("""CREATE TRIGGER reject_treasure BEFORE UPDATE ON identity_runtime_state
                    BEGIN SELECT RAISE(ABORT, 'fixture treasure save abort'); END""")
    conn.execute("""CREATE TRIGGER reject_treasure_insert BEFORE INSERT ON identity_runtime_state
                    BEGIN SELECT RAISE(ABORT, 'fixture treasure save abort'); END""")
    conn.commit()
    response = asyncio.run(call(h, "public"))
    assert response["extra"]["status"] == "persistence_pending"
    pending = deepcopy(h.owner[results.STATE_KEY])
    assert results.valid_record(pending)
    with sqlite3.connect(persistence.DB_FILE) as reader:
        assert json.loads(reader.execute("SELECT treasure_result FROM identity_runtime_state WHERE send_as_id=?",
                                         (IDENTITY,)).fetchone()[0]) == {}
        for key in ("inventory_delta_records", "miniapp_state_records"):
            assert json.loads(reader.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]) == {}
    conn.execute("DROP TRIGGER reject_treasure")
    conn.execute("DROP TRIGGER reject_treasure_insert")
    conn.commit()
    # The normal dirty-state flush can persist the retained pending result.
    assert persistence.save_state() is True
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    h.owner = state_module.get_identity_state(IDENTITY)
    assert h.owner[results.STATE_KEY] == pending
    assert not state_module.get_inventory_delta_records()
    assert not state_module.get_miniapp_state_records()
    response = runtime.recover_cave_treasure_result(IDENTITY)
    assert response["extra"]["persistence_saved"]
    with sqlite3.connect(persistence.DB_FILE) as reader:
        assert json.loads(reader.execute("SELECT treasure_result FROM identity_runtime_state WHERE send_as_id=?",
                                         (IDENTITY,)).fetchone()[0])["phase"] == "complete"
        inventory = json.loads(reader.execute("SELECT value FROM meta WHERE key='inventory_delta_records'").fetchone()[0])
        records = json.loads(reader.execute("SELECT value FROM meta WHERE key='miniapp_state_records'").fetchone()[0])
    assert inventory[pending["inventory"]["key"]]["items"] == {"fixture_item": 1}
    assert records[pending["miniapp"]["key"]] == pending["miniapp"]["record"]
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert runtime.recover_cave_treasure_result(IDENTITY) is None
    assert h.flow.await_count == 1


def test_preparation_does_not_change_global_records_or_save(commits):
    h = commits
    before = deepcopy(state_module._meta_state)
    projection = results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY))
    assert projection.is_current()
    for helper in ("_record_cave_treasure_inventory_delta", "_record_cave_treasure_miniapp_state"):
        prepared = h.originals[helper](IDENTITY, h.result, now=h.now, prepare=True)
        assert prepared["changed"] and prepared["record"]
    assert state_module._meta_state == before
    h.save.assert_not_called()


def test_committed_daily_receipt_survives_unchanged_summary_timestamp(commits):
    h = commits
    h.result["data"]["state"].update(quota_verified=True, quota_error="", games_remaining=0)
    prior = h.originals["_record_cave_treasure_miniapp_state"](IDENTITY, h.result, now=h.now - 86400)
    h.save.reset_mock()
    response = asyncio.run(call(h, "public"))
    assert response["extra"]["daily_exhausted"]
    assert state_module.get_miniapp_state_records()[prior["record_key"]]["updated_at"] == h.now - 86400
    assert not ui._cave_public_background_action_due("treasure", IDENTITY, h.now)
    assert ui._cave_public_background_action_due("treasure", IDENTITY, h.now + 86400)


def test_previous_day_local_recovery_does_not_consume_todays_quota(commits):
    h = commits
    h.result["data"]["state"].update(quota_verified=True, quota_error="", games_remaining=0)
    stage_pending(h)
    h.now += 86400
    response = asyncio.run(call(h, "public"))
    assert response["extra"]["persistence_saved"]
    assert not response["extra"]["daily_exhausted"]
    assert ui._cave_public_background_action_due("treasure", IDENTITY, h.now)
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("pause_source", ["manual", "persistence_guard", "bot_health_monitor", "tianzun_maintenance"])
def test_paused_main_loop_recovers_without_resuming_gameplay(commits, monkeypatch, pause_source):
    h = commits
    stage_pending(h)
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
    dispatch.assert_not_awaited()
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()
    h.send.assert_not_awaited()
    h.save.assert_called_once()
