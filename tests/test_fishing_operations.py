import asyncio
import copy
import hashlib
import json
import sqlite3
import threading
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, state as state_module
from model.features import fishing_operations as operations
from model.features import fishing_runtime as fishing
from model.features import fishing_miniapp as worker
from model.features import cave_treasure_runtime as cave
from model.features import storage_bag
from model import ui
from test_fishing_checkpoints import run
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_worker_lifecycle import TOKEN, INIT, adapter_with_limit, response


fishing_env = lifecycle.fishing_env
fishing_db = lifecycle.fishing_db


def frames():
    records = []
    run(lambda record: records.append(record) or True)
    return records


async def make_writer(h, **kwargs):
    return operations.CheckpointWriter(fishing.FishingMiniAppOperation.capture(h.identity_id), **kwargs)


def test_worker_checkpoint_is_on_main_loop_and_precedes_sqlite_transport(fishing_db, monkeypatch):
    h = fishing_db
    main_thread = threading.get_ident()
    calls, writes = [], []
    save = persistence.save_state

    def checked_save():
        assert threading.get_ident() == main_thread
        writes.append(copy.deepcopy(h.identity[operations.STATE_KEY]))
        return save()

    monkeypatch.setattr(fishing, "save_state", checked_save)

    def transport(request):
        assert threading.get_ident() != main_thread
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        with sqlite3.connect(persistence.DB_FILE) as conn:
            encoded = conn.execute("SELECT fishing_operation FROM identity_runtime_state WHERE send_as_id = ?",
                                   (h.identity_id,)).fetchone()[0]
        record = json.loads(encoded)
        assert operations.valid_record(record)
        if endpoint in {"start", "finish", "next"}:
            checkpoint = record["checkpoint"]
            assert checkpoint["phase"] == "intent"
            assert checkpoint["unresolved_action"] == endpoint
        if endpoint == "next":
            assert len(record["checkpoint"]["round_receipts"]) == 1
            return {"ok": True, "token": "fish_SECOND112"}
        return response(endpoint)

    async def scenario():
        writer = await make_writer(h)
        result = await asyncio.to_thread(run, writer, transport=transport)
        assert writer.finish(result)
        return result

    result = asyncio.run(scenario())
    assert result["data"]["settled_count"] == 2
    assert len(writes) == 13
    assert calls == ["start", "finish", "result", "next", "start", "finish", "result"]
    encoded = json.dumps(h.identity[operations.STATE_KEY])
    assert TOKEN not in encoded and INIT not in encoded and "fishingProof" not in encoded
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    retained = state_module.get_identity_state(h.identity_id)[operations.STATE_KEY]
    assert operations.valid_record(retained)
    assert len(retained["checkpoint"]["round_receipts"]) == 2


@pytest.mark.parametrize("failed", [False, None, OSError("fixture disk full")])
def test_checkpoint_save_failure_never_dispatches(fishing_env, monkeypatch, failed):
    h = fishing_env
    save = Mock(side_effect=failed) if isinstance(failed, Exception) else Mock(return_value=failed)
    monkeypatch.setattr(fishing, "save_state", save)
    calls = []

    async def scenario():
        writer = await make_writer(h)
        return await asyncio.to_thread(run, writer, transport=lambda request: calls.append(request))

    result = asyncio.run(scenario())
    assert result["checkpoint_error"]
    assert calls == []
    assert h.identity[operations.STATE_KEY] == {}


def test_checkpoint_sequence_replay_and_stale_writer_do_not_overwrite(fishing_env, monkeypatch):
    h = fishing_env
    save = Mock(return_value=True)
    monkeypatch.setattr(fishing, "save_state", save)
    records = frames()

    async def scenario():
        writer = await make_writer(h)
        competing = await make_writer(h)
        assert writer(records[0])
        before = copy.deepcopy(h.identity)
        assert writer(records[0])
        assert not writer(records[2])
        assert not competing(records[0])
        assert h.identity == before
        assert save.call_count == 1
        assert writer(records[1])
        assert not writer(records[0])

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "identity_disabled", "paused", "pond", "bait"])
def test_changed_owner_or_admission_cannot_checkpoint_next_intent(fishing_env, monkeypatch, change):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))

    async def scenario():
        writer = await make_writer(h, operation_check=lambda: h.identity["fishing_enabled"]
                                   and state_module.get_global_enabled()
                                   and fishing.FishingMiniAppOperation.capture(h.identity_id).is_current())
        # The real caller also binds the original immutable plan.
        captured = fishing.FishingMiniAppOperation.capture(h.identity_id)
        writer.operation_check = lambda: captured.is_current() and state_module.get_global_enabled()
        lifecycle.invalidate(h, change)
        before = copy.deepcopy(state_module._meta_state)
        assert not writer(frames()[0])
        assert state_module._meta_state == before

    asyncio.run(scenario())


@pytest.mark.parametrize("malformed", ["{broken", "[]", "null", "false", "1"])
def test_corrupt_operation_json_remains_a_hold(malformed):
    decoded = persistence._deserialize_db_value(operations.STATE_KEY, malformed)
    assert decoded and not operations.valid_record(decoded)


def test_unknown_next_cannot_reuse_previous_ready_token(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))

    async def scenario():
        writer = await make_writer(h)
        for record in frames()[:6]:
            assert writer(record)

    asyncio.run(scenario())
    record = h.identity[operations.STATE_KEY]
    assert operations.reason(record) == "unknown_next_round"
    assert not operations.matching_recovery_launch(record, {"token": TOKEN})
    assert record["checkpoint"]["unresolved_round_key"] == hashlib.sha256(TOKEN.encode()).hexdigest()


def test_known_round_recovery_merges_prefix_without_resetting_durable_revision(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    records = frames()

    async def scenario():
        writer = await make_writer(h)
        for record in records[:7]:
            assert writer(record)
        saved = copy.deepcopy(h.identity[operations.STATE_KEY])
        recovery = await make_writer(h, recovery=True)
        token = "fish_CHECKPOINT112_1"
        assert operations.matching_recovery_launch(saved, {"token": token})
        result = await asyncio.to_thread(
            worker.run_fishing_miniapp_recovery_lab_flow,
            token=token, init_data=INIT, expected_round_key=saved["checkpoint"]["unresolved_round_key"],
            pending_action="next", settled_round_keys=saved["checkpoint"]["settled_round_keys"],
            transport=lambda request: response(request["safe_summary"]["endpoint"]),
            adapter=adapter_with_limit(), checkpoint=recovery, sleeper=lambda _delay: None,
        )
        assert recovery.finish(result)
        restored = h.identity[operations.STATE_KEY]
        assert restored["operation_id"] == saved["operation_id"]
        assert restored["revision"] == saved["revision"] + 2
        assert restored["checkpoint"]["round_receipts"][0] == saved["checkpoint"]["round_receipts"][0]
        assert len(restored["checkpoint"]["round_receipts"]) == 2
        assert not restored["checkpoint"]["outcome_unknown"]

    asyncio.run(scenario())


def test_retry_after_from_result_read_is_persisted(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))

    def transport(request):
        if request["safe_summary"]["endpoint"] == "result":
            return {"status": 429, "headers": {"Retry-After": "2700"}, "body": {"ok": False}}
        return response(request["safe_summary"]["endpoint"])

    async def scenario():
        writer = await make_writer(h)
        result = await asyncio.to_thread(run, writer, transport=transport, rounds=1)
        assert writer.finish(result)

    asyncio.run(scenario())
    assert h.identity[operations.STATE_KEY]["retry_at"] >= h.now + 2700
    assert h.identity[operations.STATE_KEY]["checkpoint"]["outcome_unknown"]


def stage(h, count):
    async def scenario():
        writer = await make_writer(h)
        for record in frames()[:count]:
            assert writer(record)
    asyncio.run(scenario())


@pytest.mark.parametrize("caller", ["public", "message", "scheduler"])
@pytest.mark.parametrize("count,settled,unknown", [(1, 0, True), (5, 1, False), (6, 1, True), (7, 1, True), (12, 2, False)])
def test_receipt_recovery_precedes_entries_and_is_once_only(fishing_env, monkeypatch, caller, count, settled, unknown):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", storage_bag.apply_storage_bag_item_deltas)
    stage(h, count)
    previous = copy.deepcopy(h.identity[operations.STATE_KEY])

    async def scenario():
        if caller == "scheduler":
            with state_module.use_identity(h.identity_id):
                await fishing.run_fishing_scheduler(h.now)
        else:
            await lifecycle.run_caller(h, caller)

    asyncio.run(scenario())
    assert h.identity["fishing_daily_count"] == settled
    retained = h.identity[operations.STATE_KEY]
    if unknown:
        assert retained["checkpoint"]["outcome_unknown"]
        assert retained["accounted_round_keys"] == previous["checkpoint"]["settled_round_keys"]
        assert retained["operation_id"] == previous["operation_id"]
    else:
        assert retained == {}
    inventory = state_module.get_storage_bag_records()
    if settled:
        assert inventory[str(h.identity_id)]["items"]["fixture-fish"] == settled
    fishing.recover_fishing_result_pending(h.identity_id)
    assert h.identity["fishing_daily_count"] == settled
    assert inventory == state_module.get_storage_bag_records()
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()
    h.external.assert_not_awaited()


def test_v2_accounting_failure_restores_operation_and_recovers_after_sqlite_reload(fishing_db, monkeypatch):
    h = fishing_db
    stage(h, 6)
    original = copy.deepcopy(h.identity[operations.STATE_KEY])
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=False))
    result = fishing.recover_fishing_result_pending(h.identity_id)
    assert not result["ok"]
    assert h.identity["fishing_daily_count"] == 0
    assert h.identity[operations.STATE_KEY] == original
    assert h.identity["fishing_result_pending"]["version"] == 2
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    state_module.set_storage_bag_records({})
    assert persistence.load_state()
    h.identity = state_module.get_identity_state(h.identity_id)
    monkeypatch.setattr(fishing, "save_state", persistence.save_state)
    assert not fishing.recover_fishing_result_pending(h.identity_id)["ok"]
    assert h.identity["fishing_result_pending"] == {}
    assert h.identity["fishing_daily_count"] == 1
    assert h.identity[operations.STATE_KEY]["accounted_round_keys"] == original["checkpoint"]["settled_round_keys"]
    state_module._meta_state["identity_states"] = {}
    state_module.set_storage_bag_records({})
    assert persistence.load_state()
    h.identity = state_module.get_identity_state(h.identity_id)
    assert not fishing.recover_fishing_result_pending(h.identity_id)["ok"]
    assert h.identity["fishing_daily_count"] == 1
    assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fixture-fish"] == 1


@pytest.mark.parametrize("change", ["facts", "inventory", "account", "record"])
def test_changed_operation_basis_is_retained_without_adoption_or_entry(fishing_env, monkeypatch, change):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    stage(h, 5)
    if change == "facts":
        h.identity["fishing_daily_count"] = 4
    elif change == "inventory":
        state_module.set_storage_bag_records({str(h.identity_id): {"items": {"fixture-fish": 3}}})
    elif change == "account":
        state_module.set_identity_account(h.identity_id, 8888)
    else:
        h.identity[operations.STATE_KEY]["checkpoint"]["round_receipts"][0]["data"]["settled_count"] = 8
    before = copy.deepcopy(h.identity)
    result = asyncio.run(lifecycle.run_caller(h, "public"))
    assert not result["ok"]
    assert h.identity == before
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()


@pytest.mark.parametrize("control", ["reset", "startup", "retire", "status", "daily", "ui"])
def test_controls_and_reads_preserve_unresolved_operation_and_day_basis(fishing_env, monkeypatch, control):
    h = fishing_env
    h.identity.update(fishing_pond=state_module.IDENTITY_STATE_TEMPLATE["fishing_pond"],
                      fishing_bait=state_module.IDENTITY_STATE_TEMPLATE["fishing_bait"])
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    stage(h, 6)
    before = copy.deepcopy(h.identity)
    monkeypatch.setattr(fishing.time, "time", lambda: h.now + 86400)
    with state_module.use_identity(h.identity_id):
        if control == "reset":
            fishing.clear_fishing_state(persist=True)
        elif control == "startup":
            fishing.schedule_fishing_initial_check(h.now + 86400, persist=True)
        elif control == "retire":
            assert not fishing._retire_legacy_fishing_state(h.now + 86400)
        elif control == "status":
            assert "\u56de\u6267\u7f3a\u5931" in fishing.get_fishing_status_text()
        elif control == "daily":
            asyncio.run(lifecycle.REAL_DAILY_REPORT(h.now + 86400))
        else:
            snapshot = ui._get_fishing_miniapp_runtime_snapshot(h.identity_id, h.identity)
            assert snapshot["recovery_pending"]
            assert snapshot["recovery_reason"] == "unknown_next_round"
    assert h.identity == before


def test_scheduler_does_not_project_while_worker_holds_game_lock(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    stage(h, 5)
    before = copy.deepcopy(h.identity)

    async def scenario():
        async with fishing._fishing_send_lock(h.identity_id):
            with state_module.use_identity(h.identity_id):
                await fishing.run_fishing_scheduler(h.now)

    asyncio.run(scenario())
    assert h.identity == before


@pytest.mark.parametrize("caller", ["public", "message"])
def test_actual_caller_checkpoints_partial_chain_and_does_not_repeat_unknown_next(fishing_env, monkeypatch, caller):
    h = fishing_env
    calls = []
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", storage_bag.apply_storage_bag_item_deltas)

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "next":
            raise TimeoutError("fixture next response lost")
        return response(endpoint)

    async def flow(identity_id, **kwargs):
        return await worker.run_fishing_miniapp_production_flow(identity_id, **{
            **kwargs, "max_rounds": 2, "init_data": INIT, "transport": transport,
            "pond_choice": "", "bait_choice": "", "sleeper": lambda _delay: None,
            "adapter": adapter_with_limit(),
        })

    monkeypatch.setattr(cave, "run_fishing_miniapp_production_flow", flow)
    monkeypatch.setattr(fishing, "run_fishing_miniapp_production_flow", flow)
    first = asyncio.run(lifecycle.run_caller(h, caller))
    if caller == "public":
        assert first["extra"]["status"] == "operation_pending"
    assert h.identity["fishing_daily_count"] == 1
    assert operations.reason(h.identity[operations.STATE_KEY]) == "unknown_next_round"
    assert len(h.identity[operations.STATE_KEY]["accounted_round_keys"]) == 1
    h.loader.reset_mock()
    h.external.reset_mock()
    asyncio.run(lifecycle.run_caller(h, caller))
    assert calls == ["start", "finish", "result", "next"]
    assert h.identity["fishing_daily_count"] == 1
    h.daily.assert_not_awaited()
    h.harvest.assert_not_awaited()
    h.loader.assert_not_awaited()
    h.external.assert_not_awaited()


def test_matching_message_entry_only_reads_original_result(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", storage_bag.apply_storage_bag_item_deltas)
    stage(h, 4)
    launch = {"token": TOKEN, "webview_url": f"https://t.me/fanrenxiuxian_bot?startapp={TOKEN}"}
    monkeypatch.setattr(fishing, "extract_fishing_miniapp_launch", lambda *_args, **_kwargs: launch)
    authorization = AsyncMock(return_value=INIT)
    monkeypatch.setattr(worker, "request_fishing_miniapp_init_data", authorization)
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return response(endpoint)

    monkeypatch.setattr(worker, "build_pooled_miniapp_transport", lambda **_kwargs: transport)
    adapter = adapter_with_limit()
    monkeypatch.setattr(worker, "build_fishing_miniapp_adapter", lambda: adapter)
    asyncio.run(lifecycle.run_caller(h, "message"))
    assert calls == ["result"]
    assert h.identity["fishing_daily_count"] == 1
    assert not h.identity[operations.STATE_KEY]["checkpoint"]["outcome_unknown"]
    assert h.identity[operations.STATE_KEY]["retry_at"] == h.now + operations.RECOVERY_GAP_SEC
    authorization.assert_awaited_once()
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()


def test_projection_error_receipt_never_becomes_accounted_or_unlocks_new_game(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    record_frames = frames()
    record_frames[-1]["round_receipts"][1] = {
        "round_key": record_frames[-1]["round_receipts"][1]["round_key"],
        "data": {"settled_count": 1}, "projection_error": True,
    }

    async def scenario():
        writer = await make_writer(h)
        for record in record_frames:
            assert writer(record)

    asyncio.run(scenario())
    result = fishing.recover_fishing_result_pending(h.identity_id)
    assert not result["ok"]
    assert h.identity["fishing_daily_count"] == 1
    assert operations.reason(h.identity[operations.STATE_KEY]) == "receipt_projection_pending"
    assert len(h.identity[operations.STATE_KEY]["accounted_round_keys"]) == 1
    assert not operations.local_recovery_due(h.identity[operations.STATE_KEY], h.now + 86400)


def test_retry_after_after_settlement_is_checkpointed_before_worker_returns():
    records = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint == "shop":
            return {"status": 429, "headers": {"Retry-After": "2700"}, "body": {"ok": False}}
        return response(endpoint)

    result = run(lambda record: records.append(record) or True, transport=transport, pond_choice="pond")
    assert result["data"]["settled_count"] == 1
    assert records[-1]["retry_after_sec"] == 2700
    assert not records[-1]["outcome_unknown"]


@pytest.mark.parametrize("change", ["replaced", "rebound", "operation"])
def test_recovery_completion_cannot_account_a_replacement_operation(fishing_env, monkeypatch, change):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    stage(h, 4)
    replacement = {}

    async def complete_late(*_args, **_kwargs):
        saved = copy.deepcopy(h.identity)
        if change != "operation":
            lifecycle.invalidate(h, change)
        h.identity = state_module.get_identity_state(h.identity_id)
        h.identity.update(saved)
        h.identity[operations.STATE_KEY] = {}
        writer = await make_writer(h)
        for frame in frames()[:5]:
            assert writer(frame)
        replacement.update(copy.deepcopy(h.identity))
        return {"ok": False, "status": "failed"}

    monkeypatch.setattr(worker, "run_fishing_miniapp_recovery_production_flow", complete_late)

    async def scenario():
        return await operations.recover_entry(h.identity_id, {"token": TOKEN}, lambda: True)

    result = asyncio.run(scenario())
    assert not result["ok"]
    assert h.identity == replacement
    assert h.identity["fishing_daily_count"] == 0


@pytest.mark.parametrize("variant", ["first_response", "wrong_receipt"])
def test_writer_requires_intent_and_exact_pending_receipt_binding(fishing_env, monkeypatch, variant):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))

    async def scenario():
        writer = await make_writer(h)
        records = frames()
        if variant == "first_response":
            frame = records[1]
            frame["sequence"] = 1
        else:
            for frame in records[:4]:
                assert writer(frame)
            frame = records[4]
            frame["round_receipts"][0]["round_key"] = "b" * 64
            frame["settled_round_keys"] = ["b" * 64]
        before = copy.deepcopy(h.identity)
        assert not writer(frame)
        assert h.identity == before

    asyncio.run(scenario())


@pytest.mark.parametrize("variant", ["huge", "deep"])
def test_operation_persistence_bounds_corrupt_input(variant):
    if variant == "huge":
        record = {"payload": "x" * operations.MAX_BYTES}
        encoded = json.dumps(record)
    else:
        record = {}
        node = record
        for _ in range(2000):
            node["payload"] = {}
            node = node["payload"]
        encoded = '{"payload":' * 2000 + '{}' + '}' * 2000
    assert persistence._deserialize_db_value(operations.STATE_KEY, encoded) == {"invalid": True}
    serialized = persistence._serialize_db_value(operations.STATE_KEY, record)
    assert json.loads(serialized) == {"invalid": True}


def test_real_sqlite_abort_rolls_back_receipt_and_inventory_together(fishing_db):
    h = fishing_db
    stage(h, 6)
    original = copy.deepcopy(h.identity[operations.STATE_KEY])
    before_inventory = copy.deepcopy(state_module.get_storage_bag_records())
    conn = persistence.get_db_conn()
    conn.execute("""CREATE TEMP TRIGGER fail_fishing_v2 BEFORE UPDATE ON identity_runtime_state
                    WHEN NEW.fishing_daily_count > OLD.fishing_daily_count
                    BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END""")
    assert not fishing.recover_fishing_result_pending(h.identity_id)["ok"]
    row = conn.execute("SELECT fishing_daily_count, fishing_operation FROM identity_runtime_state WHERE send_as_id = ?",
                       (h.identity_id,)).fetchone()
    assert row["fishing_daily_count"] == 0
    assert json.loads(row["fishing_operation"]) == original
    assert h.identity[operations.STATE_KEY] == original
    assert state_module.get_storage_bag_records() == before_inventory
    saved_inventory = json.loads(conn.execute("SELECT value FROM meta WHERE key = 'storage_bag_records'").fetchone()[0])
    assert saved_inventory == before_inventory
    conn.execute("DROP TRIGGER fail_fishing_v2")
    conn.commit()
    assert not fishing.recover_fishing_result_pending(h.identity_id)["ok"]
    assert h.identity["fishing_daily_count"] == 1
    assert len(h.identity[operations.STATE_KEY]["accounted_round_keys"]) == 1


@pytest.mark.parametrize("failed_final", [False, True])
def test_finished_writer_cannot_authorize_delayed_callback(fishing_env, monkeypatch, failed_final):
    h = fishing_env
    save = Mock(return_value=True)
    monkeypatch.setattr(fishing, "save_state", save)

    async def scenario():
        writer = await make_writer(h)
        result = await asyncio.to_thread(run, writer, rounds=1)
        save.return_value = not failed_final
        assert writer.finish(result) is (not failed_final)
        before = copy.deepcopy(h.identity)
        delayed = frames()[5]
        delayed["sequence"] = writer.sequence + 1
        save.return_value = True
        assert not writer(delayed)
        assert not writer(writer.last_checkpoint)
        assert h.identity == before

    asyncio.run(scenario())


@pytest.mark.parametrize("corrupt", [None, False, []])
def test_writer_cannot_replace_a_falsey_corrupt_operation(fishing_env, corrupt):
    h = fishing_env
    h.identity[operations.STATE_KEY] = corrupt
    with pytest.raises(fishing.FishingMiniAppCommitError):
        asyncio.run(make_writer(h))


def test_timed_out_queued_checkpoint_never_writes_later(fishing_env, monkeypatch):
    from types import SimpleNamespace
    h = fishing_env
    save = Mock(return_value=True)
    monkeypatch.setattr(fishing, "save_state", save)
    monkeypatch.setattr(operations, "CHECKPOINT_TIMEOUT_SEC", 0.01)
    callbacks = []

    async def scenario():
        writer = await make_writer(h)
        writer.loop = SimpleNamespace(call_soon_threadsafe=callbacks.append)
        assert not await asyncio.to_thread(writer, frames()[0])
        assert len(callbacks) == 1
        callbacks[0]()

    asyncio.run(scenario())
    save.assert_not_called()
    assert h.identity[operations.STATE_KEY] == {}


def test_public_scheduler_stops_polling_when_only_unknown_next_remains(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", lambda: {"cave_public_fishing_identity_ids": [h.identity_id]})
    stage(h, 6)
    assert ui._cave_public_background_action_due("fishing", h.identity_id, h.now)
    assert not fishing.recover_fishing_result_pending(h.identity_id)["ok"]
    assert not ui._cave_public_background_action_due("fishing", h.identity_id, h.now + 86400)


@pytest.mark.parametrize("disabled", [False, True])
def test_restart_keeps_confirmed_valuable_reminder_unless_controls_changed(fishing_env, monkeypatch, disabled):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    records = frames()[:6]
    for record in records:
        for receipt in record["round_receipts"]:
            receipt["data"]["catches"][0]["rewards"] = [{"name": "\u6cd5\u5219\u788e\u7247", "qty": 1}]
            receipt["data"]["catches"][0]["companion"] = True

    async def scenario():
        writer = await make_writer(h)
        for record in records:
            assert writer(record)

    asyncio.run(scenario())
    if disabled:
        h.identity["fishing_enabled"] = False
    assert not fishing.recover_fishing_result_pending(h.identity_id)["ok"]
    assert h.identity["fishing_daily_count"] == 1
    assert len(h.identity["fishing_valuable_drop_reminders"]) == (0 if disabled else 1)
    before = copy.deepcopy(h.identity)
    fishing.recover_fishing_result_pending(h.identity_id)
    assert h.identity == before


def test_codec_and_writer_use_the_same_encoded_byte_limit():
    value = {"items": [1] * 500, "padding": ""}
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    value["padding"] = "x" * (operations.MAX_BYTES - size)
    serialized = persistence._serialize_db_value(operations.STATE_KEY, value)
    assert len(serialized.encode()) == operations.MAX_BYTES
    assert json.loads(serialized) == value


def test_writer_rejects_receipt_shapes_the_codec_cannot_retain(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    stage(h, 5)
    record = copy.deepcopy(h.identity[operations.STATE_KEY])
    record["checkpoint"]["round_receipts"][0]["data"]["rewards"] = [{"name": "x", "qty": 1}] * 4000
    assert len(json.dumps(record).encode()) < operations.MAX_BYTES
    assert json.loads(persistence._serialize_db_value(operations.STATE_KEY, record)) == {"invalid": True}
    assert not operations.valid_record(record)
