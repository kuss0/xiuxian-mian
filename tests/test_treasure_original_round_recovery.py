import asyncio
from copy import deepcopy
import multiprocessing
import os
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, state as state_module, ui
from model.features import cave_treasure_miniapp as worker
from model.features import cave_treasure_runtime as runtime
from model.features import treasure_operations as operations
from model.features import treasure_results as results
from model.features.miniapp_common import MiniAppIdentityOwner
from test_treasure_lifecycle import IDENTITY, INIT, URL, adapter, call, panel, receipt, round_state, scripted_transport
from test_treasure_lifecycle import h as h
from test_treasure_operations import native as native
from test_treasure_operations import treasure_db as treasure_db
from test_treasure_restart_process import _process


NATIVE_LOADER = runtime._load_cave_public_identity_session


def interrupt(h, kind, *, limit=1):
    base = scripted_transport(h.calls, limit=limit)

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint == {"search": "hunt_reveal", "settle": "hunt_settle", "enter": "hunt"}.get(kind):
            if limit == 1 or h.calls.count("hunt_settle"):
                h.calls.append(endpoint)
                raise OSError("fixture130 interrupted mutation")
        return base(request)

    h.transport = transport

    async def flow(identity_id, **kwargs):
        return await worker.run_cave_treasure_miniapp_production_flow(
            identity_id, **dict(kwargs, transport=h.transport, adapter=adapter(), sleeper=lambda _delay: None,
                               max_steps=1 if kind == "known" else 32),
        )

    h.flow.side_effect = flow
    asyncio.run(call(h, "public"))
    assert operations.hold_reason(IDENTITY) in {"original_round_required", "outcome_unknown_hold"}
    return deepcopy(h.owner[operations.STATE_KEY]), deepcopy(h.owner[results.STATE_KEY])


def resume_transport(h, kind, *, index=1, limit=1):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            if kind == "settle":
                return {**panel(index, limit), "huntResult": receipt(index)}
            return {**panel(index, limit), "huntRun": round_state(index, revealed=kind == "search")}
        if endpoint == "hunt_reveal":
            assert kind == "known"
            return {"ok": True, "huntRun": round_state(index, revealed=True)}
        assert endpoint == "hunt_settle"
        return {**panel(index, limit), "huntResult": receipt(index)}

    h.transport = transport

    async def flow(identity_id, **kwargs):
        return await worker.run_cave_treasure_miniapp_production_flow(
            identity_id, **dict(kwargs, transport=h.transport, adapter=adapter(), sleeper=lambda _delay: None),
        )

    h.flow.side_effect = flow
    return calls


@pytest.mark.parametrize("kind", ["known", "search", "settle"])
@pytest.mark.parametrize("caller", ["public", "command"])
def test_authorized_call_resumes_only_the_original_round(native, kind, caller):
    before, _result = interrupt(native, kind)
    calls = resume_transport(native, kind)
    asyncio.run(call(native, caller))
    assert calls == (["start", "hunt_reveal", "hunt_settle"] if kind == "known"
                     else ["start", "hunt_settle"] if kind == "search" else ["start"])
    current = native.owner[operations.STATE_KEY]
    assert current["operation_id"] != before["operation_id"]
    assert current["version"] == 2 and operations.valid_record(current)
    assert operations.result_matches(MiniAppIdentityOwner.capture(IDENTITY), native.owner[results.STATE_KEY])
    assert not operations.hold_reason(IDENTITY)
    assert native.owner[results.STATE_KEY]["response"]["extra"]["settled_count"] == 1


@pytest.mark.parametrize("retire_inventory", [False, True])
def test_previous_receipt_is_not_projected_again_during_recovery(native, retire_inventory):
    before, _result = interrupt(native, "search", limit=2)
    old_key = operations.inventory_key(before)
    inventory = state_module.get_inventory_delta_records()
    assert inventory[old_key]["items"] == {"fixture_item": 1}
    if retire_inventory:
        state_module.set_inventory_delta_records({key: value for key, value in inventory.items() if key != old_key})
    calls = resume_transport(native, "search", index=2, limit=2)
    response = asyncio.run(call(native, "public"))
    assert calls == ["start", "hunt_settle"]
    assert response["extra"]["rewards"] == {"fixture_item": 1}
    current = native.owner[operations.STATE_KEY]
    assert len(current["checkpoint"]["receipts"]) == 2
    inventory = state_module.get_inventory_delta_records()
    assert inventory[operations.inventory_key(current)]["items"] == {"fixture_item": 1}
    if retire_inventory:
        assert old_key not in inventory
    else:
        assert inventory[old_key]["items"] == {"fixture_item": 1}
    assert runtime.recover_cave_treasure_result(IDENTITY) is None


@pytest.mark.parametrize("kind", ["foreign", "unchanged", "missing", "read_failed"])
def test_inconclusive_read_preserves_the_old_journal_and_result(native, kind):
    before, old_result = interrupt(native, "search")
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        assert endpoint == "start"
        if kind == "read_failed":
            raise OSError("fixture130 read failed")
        if kind == "missing":
            return panel(1, 1)
        return {**panel(1, 1), "huntRun": round_state(2 if kind == "foreign" else 1)}

    native.transport = transport
    asyncio.run(call(native, "public"))
    assert calls == ["start"]
    assert native.owner[operations.STATE_KEY] == before
    assert native.owner[results.STATE_KEY] == old_result
    assert not state_module.get_inventory_delta_records()


def test_unknown_enter_without_a_returned_session_stays_held(native):
    before, old_result = interrupt(native, "enter")
    calls = resume_transport(native, "known")
    asyncio.run(call(native, "public"))
    assert calls == []
    assert native.owner[operations.STATE_KEY] == before
    assert native.owner[results.STATE_KEY] == old_result


def test_verified_daily_reset_retires_unknown_search_before_starting_a_new_round(native):
    before, _old_result = interrupt(native, "search", limit=1)
    native.now += 86400
    calls = []
    native.transport = scripted_transport(calls, limit=1)

    async def flow(identity_id, **kwargs):
        return await worker.run_cave_treasure_miniapp_production_flow(
            identity_id, **dict(kwargs, transport=native.transport, adapter=adapter(), sleeper=lambda _delay: None),
        )

    native.flow.side_effect = flow
    reconciled = asyncio.run(call(native, "public"))
    assert not reconciled["ok"] and reconciled["extra"]["status"] == "daily_reset_reconciled"
    assert calls == ["start"]
    current = native.owner[operations.STATE_KEY]
    assert current["version"] == 2 and current["resume"]["operation_id"] == before["operation_id"]
    assert not operations.hold_reason(IDENTITY)

    response = asyncio.run(call(native, "public"))
    assert response["ok"] and response["extra"]["daily_exhausted"], response
    assert calls == ["start", "start", "hunt", "hunt_reveal", "hunt_settle"]


@pytest.mark.parametrize("kind", ["search", "settle"])
def test_daily_counter_must_move_back_and_only_search_can_be_retired(native, kind):
    before, old_result = interrupt(native, kind, limit=1)
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return panel(1, 1)

    native.transport = transport

    async def flow(identity_id, **kwargs):
        return await worker.run_cave_treasure_miniapp_production_flow(
            identity_id, **dict(kwargs, transport=native.transport, adapter=adapter(), sleeper=lambda _delay: None),
        )

    native.flow.side_effect = flow
    asyncio.run(call(native, "public"))
    assert calls == ["start"]
    assert native.owner[operations.STATE_KEY] == before
    assert native.owner[results.STATE_KEY] == old_result


@pytest.mark.parametrize("stage", ["checkpoint", "result"])
def test_recovery_save_failure_preserves_facts_and_retries_only_local_work(native, stage):
    before, old_result = interrupt(native, "search", limit=2)
    old_inventory = deepcopy(state_module.get_inventory_delta_records())
    calls = resume_transport(native, "search", index=2, limit=2)
    target = native.ops_save if stage == "checkpoint" else native.result_save
    target.return_value = False
    response = asyncio.run(call(native, "public"))
    assert not response["ok"]
    assert state_module.get_inventory_delta_records() == old_inventory
    if stage == "checkpoint":
        assert calls == ["start"]
        assert native.owner[operations.STATE_KEY] == before
        assert native.owner[results.STATE_KEY] == old_result
    else:
        assert calls == ["start", "hunt_settle"]
        assert native.owner[results.STATE_KEY]["phase"] == "pending"
    target.return_value = True
    if stage == "checkpoint":
        asyncio.run(call(native, "public"))
        assert calls == ["start", "start", "hunt_settle"]
    else:
        runtime.recover_cave_treasure_result(IDENTITY)
        assert calls == ["start", "hunt_settle"]
    assert not operations.hold_reason(IDENTITY)
    assert sum(row["items"]["fixture_item"] for key, row in state_module.get_inventory_delta_records().items()
               if key != "_meta") == 2


@pytest.mark.parametrize("stage", ["after_read", "after_settle", "after_commit"])
def test_continuation_and_receipt_prefix_survive_sqlite_reload(treasure_db, monkeypatch, stage):
    h = treasure_db
    before, old_result = interrupt(h, "search", limit=2)
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    h.owner = state_module.get_identity_state(IDENTITY)
    assert h.owner[operations.STATE_KEY] == before
    calls = resume_transport(h, "search", index=2, limit=2)
    original_save = persistence.save_state
    stop = False

    def save():
        nonlocal stop
        record = h.owner[operations.STATE_KEY]
        result = h.owner[results.STATE_KEY]
        if stage != "after_commit" and isinstance(record, dict) and record.get("version") == 2:
            if stage == "after_read" and record["checkpoint"]["phase"] == "intent":
                stop = True
            if (stage == "after_settle" and isinstance(result, dict) and result.get("phase") == "complete"
                    and result.get("operation_id") == record["operation_id"]):
                stop = True
        if stop:
            return False
        return original_save()

    monkeypatch.setattr(operations, "save_state", save)
    monkeypatch.setattr(results, "save_state", save)
    asyncio.run(call(h, "public"))
    game_calls = list(calls)
    monkeypatch.setattr(operations, "save_state", original_save)
    monkeypatch.setattr(results, "save_state", original_save)
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    h.owner = state_module.get_identity_state(IDENTITY)
    assert operations.valid_record(h.owner[operations.STATE_KEY])
    assert h.owner[operations.STATE_KEY]["resume"]["receipt_digest"] == operations.digest(before["checkpoint"]["receipts"])
    runtime.recover_cave_treasure_result(IDENTITY)
    assert calls == game_calls
    assert operations.result_matches(MiniAppIdentityOwner.capture(IDENTITY), h.owner[results.STATE_KEY])
    if stage == "after_read":
        asyncio.run(call(h, "public"))
        assert calls == ["start", "start", "hunt_settle"]
    else:
        assert calls == ["start", "hunt_settle"]
    assert not operations.hold_reason(IDENTITY)
    assert sum(row["items"]["fixture_item"] for key, row in state_module.get_inventory_delta_records().items()
               if key != "_meta") == 2
    assert h.owner[results.STATE_KEY] != old_result


@pytest.mark.parametrize("field,value", [
    ("receipts", -1), ("receipts", True), ("receipts", 3),
    ("receipt_digest", "a" * 64), ("checkpoint", "bad"), ("operation_id", "bad"),
])
def test_invalid_continuation_provenance_stays_held(native, field, value):
    interrupt(native, "search", limit=2)
    resume_transport(native, "search", index=2, limit=2)
    asyncio.run(call(native, "public"))
    record = native.owner[operations.STATE_KEY]
    record["resume"][field] = value
    assert not operations.valid_record(record)
    assert not operations.resume_allowed(IDENTITY)
    assert operations.hold_reason(IDENTITY)


@pytest.mark.parametrize("change", ["owner", "account", "disabled", "pause"])
def test_recovery_read_cannot_dispatch_after_owner_or_permission_changes(native, monkeypatch, change):
    before, _ = interrupt(native, "known")
    calls = resume_transport(native, "known")
    original = native.transport

    def transport(request):
        data = original(request)
        if change == "owner":
            state_module._meta_state["identity_states"][IDENTITY] = deepcopy(native.owner)
        elif change == "account":
            state_module.set_identity_account(IDENTITY, IDENTITY + 1)
        elif change == "disabled":
            state_module.set_identity_enabled(IDENTITY, False)
        else:
            monkeypatch.setattr(runtime, "_public_entry_allowed", lambda: False)
        return data

    native.transport = transport
    asyncio.run(call(native, "public"))
    assert calls == ["start"]
    assert not state_module.get_inventory_delta_records()
    assert state_module.get_identity_state(IDENTITY)[operations.STATE_KEY]["checkpoint"]["receipts"] == before["checkpoint"]["receipts"]


@pytest.mark.parametrize("kind", ["operation", "result", "legacy_snapshot"])
def test_sibling_hold_cannot_be_bypassed_for_recovery(native, kind):
    before, _ = interrupt(native, "search")
    sibling = IDENTITY + 1
    state_module.set_identity_account(sibling, IDENTITY)
    other = state_module.get_identity_state(sibling)
    if kind == "legacy_snapshot":
        records = state_module.get_miniapp_state_records()
        records[f"{sibling}:cave_treasure"] = {"state": {"outcome_unknown": True}}
        state_module.set_miniapp_state_records(records)
    else:
        other[operations.STATE_KEY if kind == "operation" else results.STATE_KEY] = {"invalid": True}
    calls = resume_transport(native, "search")
    asyncio.run(call(native, "public"))
    assert calls == []
    assert native.owner[operations.STATE_KEY] == before


def test_ui_and_background_allow_original_round_recovery(native, monkeypatch):
    interrupt(native, "search")
    calls = resume_transport(native, "search")
    assert ui._cave_public_background_action_due("treasure", IDENTITY, native.now)
    monkeypatch.setattr(ui, "_cave_public_shared_hold", lambda now: {})
    asyncio.run(ui.ui_run_cave_public_entry(IDENTITY, "treasure", URL))
    assert calls == ["start", "hunt_settle"]
    assert not ui._cave_public_background_action_due("treasure", IDENTITY, native.now)


@pytest.mark.parametrize("kind", ["known", "search", "settle"])
def test_original_round_manually_settled_is_adopted_from_its_exact_receipt(native, kind):
    interrupt(native, kind)
    calls = resume_transport(native, "settle")
    response = asyncio.run(call(native, "public"))
    assert calls == ["start"]
    assert response["ok"] and response["extra"]["rewards"] == {"fixture_item": 1}
    assert not operations.hold_reason(IDENTITY)


@pytest.mark.parametrize("envelope", [False, True])
@pytest.mark.parametrize("kind", ["search", "settle"])
def test_conflicting_snapshot_session_echo_cannot_resolve_an_original_round(native, envelope, kind):
    before, old_result = interrupt(native, kind)
    calls = resume_transport(native, kind)
    original = native.transport

    def transport(request):
        value = original(request)
        return ({"data": value, "sessionId": "foreign130"} if envelope
                else {**value, "sessionId": "foreign130"})

    native.transport = transport
    asyncio.run(call(native, "public"))
    assert calls == ["start"]
    assert native.owner[operations.STATE_KEY] == before
    assert native.owner[results.STATE_KEY] == old_result


@pytest.mark.parametrize("boundary,count,reason", [
    ("snapshot", 1, "original_round_required"),
    ("hunt_settle_dispatched", 1, "outcome_unknown_hold"),
    ("settled", 2, ""), ("finished", 2, ""), ("commit_before_db", 2, ""), ("commit_after_db", 2, ""),
])
def test_forced_stop_during_continuation_never_replays_old_inventory(tmp_path, boundary, count, reason):
    context = multiprocessing.get_context("spawn")
    outputs = []
    for mode in ("first", "reload"):
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_process, args=(str(tmp_path), mode, "resume_" + boundary, send))
        process.start()
        send.close()
        try:
            assert receive.poll(20)
            observed = receive.recv()
            assert "error" not in observed, observed
            outputs.append(observed)
            if mode == "first":
                os.kill(process.pid, signal.SIGKILL)
            process.join(10)
            assert process.exitcode == (-signal.SIGKILL if mode == "first" else 0), observed
        finally:
            if process.is_alive():
                process.kill()
                process.join(10)
            receive.close()
    interrupted, restored = outputs
    assert restored["original"] == interrupted["record"] == restored["record"]
    assert restored["calls"] == [] and restored["reason"] == reason
    record = restored["record"]
    assert record["version"] == 2 and record["resume"]["receipts"] == 1
    assert len(record["checkpoint"]["receipts"]) == count
    assert sum(row["items"]["fixture_item"] for row in restored["inventory"].values()) == count
    assert len(restored["inventory"]) == count
    assert "private-session128" not in str(restored) and "df_FIXTURE128_SECRET" not in str(restored)


@pytest.mark.parametrize("status", [408, 429, 503])
def test_recovery_read_preserves_server_retry_after_without_repeating(native, status):
    before, old_result = interrupt(native, "search")
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return SimpleNamespace(status_code=status, headers={"Retry-After": "2400"},
                               json=lambda: {"ok": False, "error": "fixture_wait"})

    native.transport = transport
    response = asyncio.run(call(native, "public"))
    assert calls == ["start"]
    assert response["extra"]["retry_after_sec"] == 2400
    assert native.owner[operations.STATE_KEY] == before
    assert native.owner[results.STATE_KEY] == old_result


@pytest.mark.parametrize("resolved,retry_after,real_loader", [
    (False, 0, False), (True, 0, False), (False, 60, False), (False, 2400, False),
    (False, 60, True), (False, 2400, True),
])
def test_background_recovery_runs_through_real_ui_and_preserves_retry_spacing(
    native, monkeypatch, resolved, retry_after, real_loader,
):
    before, old_result = interrupt(native, "search")
    calls = resume_transport(native, "search")
    original = native.transport

    def transport(request):
        value = original(request)
        if not resolved:
            assert request["safe_summary"]["endpoint"] == "start"
            if retry_after:
                return SimpleNamespace(status_code=503, headers={"Retry-After": str(retry_after)},
                                       json=lambda: {"ok": False, "error": "fixture_wait"})
            return {**panel(1, 1), "huntRun": round_state()}
        return value

    native.transport = transport
    if real_loader:
        monkeypatch.setattr(runtime, "_load_cave_public_identity_session", NATIVE_LOADER)
        monkeypatch.setattr(runtime, "request_cave_treasure_miniapp_init_data", AsyncMock(return_value=INIT))

        async def start(identity_id, **kwargs):
            return await worker.run_cave_dwelling_start_production_flow(
                identity_id, **dict(kwargs, transport=native.transport, adapter=adapter(), sleeper=lambda _delay: None),
            )

        monkeypatch.setattr(runtime, "run_cave_dwelling_start_production_flow", start)
    config = {**{flag: False for flag in ui._CAVE_PUBLIC_BACKGROUND_ACTION_FLAGS.values()},
              "cave_public_treasure_enabled": True, "cave_public_entry_urls": [URL], "cave_public_delay_sec": 20}
    state_module.set_miniapp_auto_config(config)
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", state_module.get_miniapp_auto_config)
    monkeypatch.setattr(ui.time, "time", lambda: native.now)
    monkeypatch.setattr(ui, "_cave_public_background_state", {
        "running": False, "next_run_at": 0, "cursor": 0, "circuit_open_until": 0, "circuit_reason": "",
    })
    monkeypatch.setattr(ui, "_cave_public_background_operation", None)
    monkeypatch.setattr(ui, "_cave_public_batch_state", {"running": False})
    monkeypatch.setattr(ui, "_cave_public_ui_run_lock", asyncio.Lock())
    monkeypatch.setattr(ui, "console_log", Mock())
    monkeypatch.setattr(ui, "save_state", Mock())
    monkeypatch.setattr(ui, "send_audit_log", AsyncMock())
    queued = []
    monkeypatch.setattr(ui, "_fire_and_forget", queued.append)

    async def scenario():
        first = await ui._run_cave_public_background_scheduler(native.now, config)
        assert first["started"], first
        assert len(queued) == 1
        await queued.pop()
        deadline = ui._cave_public_background_retry_at[("treasure", IDENTITY)]
        assert deadline >= native.now + (60 if resolved else max(1800, retry_after))
        again = await ui._run_cave_public_background_scheduler(native.now + 30, config)
        assert not again["started"]
        assert not queued

    try:
        asyncio.run(scenario())
    finally:
        for coroutine in queued:
            coroutine.close()
    assert calls == (["start", "hunt_settle"] if resolved else ["start"])
    if not resolved:
        assert native.owner[operations.STATE_KEY] == before
        assert native.owner[results.STATE_KEY] == old_result


def test_real_identity_loader_preserves_the_original_accounting_link(native, monkeypatch):
    interrupt(native, "search")
    calls = resume_transport(native, "search")
    monkeypatch.setattr(runtime, "_load_cave_public_identity_session", NATIVE_LOADER)
    monkeypatch.setattr(runtime, "request_cave_treasure_miniapp_init_data", AsyncMock(return_value=INIT))

    async def start(identity_id, **kwargs):
        return await worker.run_cave_dwelling_start_production_flow(
            identity_id, **dict(kwargs, transport=native.transport, adapter=adapter(), sleeper=lambda _delay: None),
        )

    monkeypatch.setattr(runtime, "run_cave_dwelling_start_production_flow", start)
    response = asyncio.run(call(native, "public"))
    assert response["ok"], response
    assert calls == ["start", "start", "hunt_settle"]
    assert not operations.hold_reason(IDENTITY)


def test_second_interruption_keeps_the_same_previously_accounted_receipt_prefix(native):
    initial, _ = interrupt(native, "search", limit=2)
    calls = resume_transport(native, "search", index=2, limit=2)
    original = native.transport

    def transport(request):
        value = original(request)
        if request["safe_summary"]["endpoint"] == "hunt_settle":
            raise OSError("fixture130 interrupted again")
        return value

    native.transport = transport
    asyncio.run(call(native, "public"))
    middle = deepcopy(native.owner[operations.STATE_KEY])
    assert middle["checkpoint"]["pending"]["action"] == "settle"
    assert calls == ["start", "hunt_settle"]
    assert operations.resume_allowed(IDENTITY)
    calls = resume_transport(native, "settle", index=2, limit=2)
    response = asyncio.run(call(native, "public"))
    assert calls == ["start"]
    assert response["ok"] and response["extra"]["rewards"] == {"fixture_item": 1}
    final = native.owner[operations.STATE_KEY]
    assert final["resume"]["operation_id"] == middle["operation_id"]
    assert final["resume"]["receipt_digest"] == operations.digest(initial["checkpoint"]["receipts"])
    inventory = state_module.get_inventory_delta_records()
    assert inventory[operations.inventory_key(initial)]["items"] == {"fixture_item": 1}
    assert inventory[operations.inventory_key(final)]["items"] == {"fixture_item": 1}
    assert operations.inventory_key(middle) not in inventory


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("action", ["search", "settle"])
@pytest.mark.parametrize("quota", ["valid", "missing", "invalid"])
def test_checkpoint_intent_obeys_worker_quota_admission(native, resume, action, quota):
    previous = interrupt(native, "known")[0] if resume else None
    payload = {**panel(1, 1), "huntRun": round_state(revealed=action == "settle")}
    if quota == "missing":
        payload.pop("dwelling")
    elif quota == "invalid":
        payload["dwelling"]["hunt"]["remaining"] = "invalid"
    observed = worker.parse_cave_treasure_state(payload)
    expected = quota != "invalid"
    assert worker.choose_cave_treasure_action(observed)["action"] == (action if expected else "blocked")
    value = {
        "version": 1, "phase": "response", "sequence": 1,
        "action_dispatched": previous["checkpoint"]["action_dispatched"] if previous else False,
        "pending": {}, "receipts": [], "resolution": {}, "retry_after_sec": 0,
        "ok": False, "status": "running", "error": "", "state": operations.state_evidence(observed),
    }
    calls = list(native.calls)

    async def scenario():
        projection = results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY), resume=resume)
        writer = operations.CheckpointWriter(projection, player_id=IDENTITY, resume=resume)
        assert writer(value)
        before = deepcopy(native.owner[operations.STATE_KEY])
        saves = native.ops_save.call_count
        intent = deepcopy(value)
        intent.update(phase="intent", sequence=2, pending={
            "action": action, "session_key": value["state"]["session_key"],
            "target_index": 1 if action == "search" else 0,
        })
        assert writer(intent) is expected
        if not expected:
            assert native.owner[operations.STATE_KEY] == before
            assert native.ops_save.call_count == saves

    asyncio.run(scenario())
    assert native.calls == calls


@pytest.mark.parametrize("kind", ["known", "search"])
def test_recovery_waits_for_valid_quota_without_losing_the_original_round(native, kind):
    interrupt(native, kind)
    calls = resume_transport(native, kind)
    original = native.transport

    def malformed_quota(request):
        value = original(request)
        assert request["safe_summary"]["endpoint"] == "start"
        value["dwelling"]["hunt"]["remaining"] = "invalid"
        return value

    native.transport = malformed_quota
    response = asyncio.run(call(native, "public"))
    assert calls == ["start"]
    assert response["extra"]["status"] == "blocked"
    assert operations.hold_reason(IDENTITY) == "original_round_required"
    assert operations.resume_allowed(IDENTITY)
    assert not state_module.get_inventory_delta_records()
    native.transport = original
    response = asyncio.run(call(native, "public"))
    assert response["ok"]
    assert calls == (["start", "start", "hunt_reveal", "hunt_settle"] if kind == "known"
                     else ["start", "start", "hunt_settle"])
    assert not operations.hold_reason(IDENTITY)
    assert response["extra"]["rewards"] == {"fixture_item": 1}
