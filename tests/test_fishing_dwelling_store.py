import asyncio
from copy import deepcopy
import json
import sqlite3
import threading
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, state as state_module
from model.features import fishing_dwelling_store as native
from model.features.fishing_dwelling_protocol import ProtocolError, ServerClock, fight_steps
from model.features.fishing_dwelling_journal import validate, MAX_BYTES
from model.features.fishing_dwelling_miniapp import run_native_fishing_production_flow
from model.features.miniapp_common import MiniAppFlowCancelled
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_dwelling_journal import context, response, fighting
from test_fishing_dwelling_supply import shop_context, SETTINGS
from model.features import fishing_dwelling_supply as supply


fishing_env = lifecycle.fishing_env
fishing_db = lifecycle.fishing_db


def start(ledger):
    return ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait")


def persisted(identity_id):
    with sqlite3.connect(persistence.DB_FILE) as conn:
        return json.loads(conn.execute("SELECT fishing_native_operation FROM identity_runtime_state WHERE send_as_id=?",
                                       (identity_id,)).fetchone()[0])


@pytest.mark.parametrize("phase", ["pending", "confirmed", "accounted"])
def test_supply_real_sqlite_restart_retains_phase_and_prevents_duplicate(fishing_db, phase):
    h = fishing_db

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.supply_journal()
        _, expected, body = ledger.prepare(context=shop_context(), site_id="west-shore", model_id="ngw",
                                           basis=store.basis()["inventory"], **SETTINGS)
        with sqlite3.connect(persistence.DB_FILE) as conn:
            saved = json.loads(conn.execute("SELECT fishing_native_supply FROM identity_runtime_state WHERE send_as_id=?",
                                            (h.identity_id,)).fetchone()[0])
        assert saved["operation_id"] == body["operationId"] and saved["phase"] == "pending"
        if phase != "pending":
            ledger.accept(expected, shop_context(rice=20, stone=4300))
        if phase == "accounted":
            assert store.project_supply()
            assert store.project_supply() is False

    asyncio.run(scenario())
    original = deepcopy(h.identity[supply.STATE_KEY])
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert state_module.get_identity_state(h.identity_id)[supply.STATE_KEY] == original
    supply.validate(original)
    assert original["phase"] == phase


@pytest.mark.parametrize("failure", ["timeout", "foreign", "disabled", "save", "context_read"])
def test_native_supply_flow_cannot_cast_or_duplicate_purchase(fishing_db, monkeypatch, failure):
    h = fishing_db
    calls, allowed = [], [True]

    def transport(request):
        action = request["safe_summary"]["endpoint"]
        calls.append(action)
        if action == "context":
            return shop_context()
        assert action == "buy-bait"
        assert h.identity[supply.STATE_KEY]["phase"] == "pending"
        assert h.identity[native.STATE_KEY] == {}
        if failure == "timeout":
            raise TimeoutError("response lost")
        result = shop_context(rice=20, stone=4300)
        if failure == "foreign":
            result["playerId"] = 3
        elif failure == "context_read":
            result = {"ok": True}
        elif failure == "disabled":
            allowed[0] = False
        elif failure == "save":
            monkeypatch.setattr(persistence, "save_state", lambda: False)
        return result

    async def run():
        return await run_native_fishing_production_flow(
            h.identity_id, player_id=-100991060001, token="df_FIXTURE", init_data="fixture", site_id="west-shore",
            model_id="ngw", bait_id="rice", transport=transport, operation_check=lambda: allowed[0],
            supply_settings=SETTINGS,
        )
    result = asyncio.run(run())
    assert calls == ["context", "buy-bait"] and not result.get("committed")
    if failure == "disabled":
        assert result["supply_committed"] and h.identity[supply.STATE_KEY]["phase"] == "accounted"
        return
    assert h.identity[supply.STATE_KEY]["phase"] == "pending"
    original = deepcopy(h.identity[supply.STATE_KEY])
    calls.clear()
    result = asyncio.run(run())
    assert not calls and not result.get("committed") and not result.get("supply_committed")
    assert h.identity[supply.STATE_KEY] == original


def test_supply_projection_save_failure_preserves_confirmed_receipt(fishing_db, monkeypatch):
    h = fishing_db

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.supply_journal()
        _, expected, _ = ledger.prepare(context=shop_context(), site_id="west-shore", model_id="ngw",
                                        basis=store.basis()["inventory"], **SETTINGS)
        ledger.accept(expected, shop_context(rice=20, stone=4300))
        from model.features import fishing_runtime as fishing
        inventory = deepcopy(fishing.get_storage_bag_records())
        monkeypatch.setattr(persistence, "save_state", lambda: False)
        with pytest.raises(ProtocolError, match="projection_save_failed"):
            store.project_supply()
        assert fishing.get_storage_bag_records() == inventory
        assert h.identity[supply.STATE_KEY]["phase"] == "confirmed"

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
def test_supply_old_owner_cannot_confirm_or_project(fishing_db, change):
    h = fishing_db

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.supply_journal()
        _, expected, _ = ledger.prepare(context=shop_context(), site_id="west-shore", model_id="ngw", **SETTINGS)
        lifecycle.invalidate(h, change)
        original = deepcopy(state_module._meta_state)
        with pytest.raises(ProtocolError):
            ledger.accept(expected, shop_context(rice=20, stone=4300))
        with pytest.raises(ProtocolError):
            store.project_supply()
        assert state_module._meta_state == original

    asyncio.run(scenario())


def test_previous_accounted_rod_does_not_mark_a_later_failed_request_successful(fishing_db):
    h = fishing_db

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.journal()
        query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait", projection_basis=store.basis())
        ledger.accept(query, response(settled=True))
        store.project(1700000000)
        assert h.identity[native.STATE_KEY]["phase"] == "accounted"
        def transport(request):
            raise TimeoutError("next context unavailable")
        result = await run_native_fishing_production_flow(
            h.identity_id, player_id=-100991060001, token="df_FIXTURE", init_data="fixture", site_id="west-shore",
            model_id="ngw", bait_id="bait", transport=transport, operation_check=lambda: True,
        )
        assert not result.get("committed") and not result["ok"]
    asyncio.run(scenario())


def test_supply_cancel_drains_inflight_purchase_and_projects_exactly_once(fishing_db):
    h = fishing_db
    entered, release = threading.Event(), threading.Event()
    calls = []

    def transport(request):
        action = request["safe_summary"]["endpoint"]
        calls.append(action)
        if action == "context":
            return shop_context()
        assert action == "buy-bait"
        entered.set()
        assert release.wait(5)
        return shop_context(rice=20, stone=4300)

    async def scenario():
        before_timer = h.identity["next_fishing_time"]
        task = asyncio.create_task(run_native_fishing_production_flow(
            h.identity_id, player_id=-100991060001, token="df_FIXTURE", init_data="fixture", site_id="west-shore",
            model_id="ngw", bait_id="rice", transport=transport, operation_check=lambda: True,
            supply_settings=SETTINGS,
        ))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
        release.set()
        with pytest.raises(MiniAppFlowCancelled) as caught:
            await task
        assert caught.value.result["supply_committed"]
        assert h.identity["next_fishing_time"] == before_timer
        assert h.identity[supply.STATE_KEY]["phase"] == "accounted"
        assert h.identity[native.STATE_KEY] == {}
        assert calls == ["context", "buy-bait"]
    asyncio.run(scenario())


@pytest.mark.parametrize("key", [native.STATE_KEY, supply.STATE_KEY])
def test_supply_and_cast_mutations_are_mutually_exclusive(fishing_db, key):
    h = fishing_db

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        cast, supplies = store.journal(), store.supply_journal()
        def buy():
            return supplies.prepare(context=shop_context(), site_id="west-shore", model_id="ngw", **SETTINGS)
        if key == native.STATE_KEY:
            start(cast)
            with pytest.raises(ProtocolError, match="other_operation_unresolved"):
                buy()
        else:
            buy()
            with pytest.raises(ProtocolError, match="other_operation_unresolved"):
                start(cast)
    asyncio.run(scenario())


def test_store_persists_mutation_before_worker_receives_permission(fishing_db, monkeypatch):
    h = fishing_db
    main_thread = threading.get_ident()
    save = persistence.save_state
    writes = []

    def checked_save():
        assert threading.get_ident() == main_thread
        writes.append(deepcopy(h.identity[native.STATE_KEY]))
        return save()

    monkeypatch.setattr(persistence, "save_state", checked_save)

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)

        def worker():
            ledger = store.journal()
            query, _ = start(ledger)
            assert persisted(h.identity_id)["pending_action"] == "cast"
            ledger.accept(query, response())
            query, _ = ledger.prepare("hook", clock=ServerClock.capture(15000, 0, 0), monotonic_now=0)
            assert persisted(h.identity_id)["pending_action"] == "hook"
            ledger.accept(query, fighting())
            for final, proof, details in fight_steps(fighting()["session"]["fight"]):
                action = "fight" if final else "checkpoint"
                query, _ = ledger.prepare(action, proof=proof, details=details)
                assert persisted(h.identity_id)["pending_action"] == action
                ledger.accept(query, response(settled=True) if final else {"ok": True})
        await asyncio.to_thread(worker)

    asyncio.run(scenario())
    assert h.identity["fishing_operation"] == {} and h.identity["fishing_daily_count"] == 0
    assert persisted(h.identity_id)["phase"] == "settled"
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    record = state_module.get_identity_state(h.identity_id)[native.STATE_KEY]
    validate(record)
    assert record["phase"] == "settled" and record["catches"] == {"fish": 2}
    assert {r["pending_action"] for r in writes} >= {"cast", "hook", "checkpoint", "fight"}


@pytest.mark.parametrize("action", ["cast", "hook", "checkpoint", "fight"])
def test_restart_with_unknown_mutation_only_queries_original_session(fishing_db, action):
    h = fishing_db

    async def before_restart():
        ledger = native.NativeFishingStore(h.identity_id, -100991060001).journal()
        query, _ = start(ledger)
        if action == "cast":
            return
        ledger.accept(query, response())
        query, _ = ledger.prepare("hook", clock=ServerClock.capture(15000, 0, 0), monotonic_now=0)
        if action == "hook":
            return
        ledger.accept(query, fighting())
        steps = list(fight_steps(fighting()["session"]["fight"]))
        _, proof, details = steps[-1] if action == "fight" else steps[0]
        ledger.prepare(action, proof=proof, details=details)

    asyncio.run(before_restart())
    original = persisted(h.identity_id)
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()

    async def after_restart():
        ledger = native.NativeFishingStore(h.identity_id, -100991060001).journal()
        query, payload = ledger.recovery()
        assert query.action == "state"
        assert payload.get("castOperationId", payload.get("sessionId")) == (
            original["cast_id"] if action == "cast" else original["session_id"])
        assert ledger.record["pending_action"] == action
        with pytest.raises(ProtocolError):
            start(ledger)

    asyncio.run(after_restart())


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
def test_old_store_cannot_write_after_identity_owner_changes(fishing_db, change):
    h = fishing_db

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.journal()
        query, _ = start(ledger)
        lifecycle.invalidate(h, change)
        before = deepcopy(state_module._meta_state)
        with pytest.raises(ProtocolError):
            ledger.accept(query, response(settled=True))
        assert state_module._meta_state == before

    asyncio.run(scenario())


@pytest.mark.parametrize("result", [False, None, OSError("fixture full")])
def test_store_save_failure_does_not_release_mutation_or_reuse_store(fishing_db, monkeypatch, result):
    h = fishing_db
    saver = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    monkeypatch.setattr(persistence, "save_state", saver)

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.journal()
        with pytest.raises((ProtocolError, OSError)):
            start(ledger)
        assert h.identity[native.STATE_KEY] == {}
        with pytest.raises(ProtocolError):
            store.journal()

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["fishing_operation", "fishing_result_pending"])
def test_unresolved_legacy_records_are_not_reinterpreted_or_overwritten(fishing_db, key):
    h = fishing_db
    h.identity[key] = {"invalid": True}

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        with pytest.raises(ProtocolError, match="legacy_fishing_unresolved"):
            store.journal()

    asyncio.run(scenario())
    assert h.identity[key] == {"invalid": True} and h.identity[native.STATE_KEY] == {}


@pytest.mark.parametrize("value", ["{broken", "[]", "null", "false", "1", '"' + 'x' * (MAX_BYTES + 1) + '"'])
def test_native_corrupt_data_remains_a_hold(value):
    parsed = persistence._deserialize_db_value(native.STATE_KEY, value)
    assert parsed != {}
    with pytest.raises(ProtocolError):
        validate(parsed)


def test_native_limit_and_schema_default_are_consistent(fishing_db):
    assert state_module.FISHING_NATIVE_OPERATION_MAX_BYTES == MAX_BYTES
    assert persistence.save_state()
    assert persisted(fishing_db.identity_id) == {}
    assert persistence._serialize_db_value(native.STATE_KEY, {"oversize": "x" * MAX_BYTES}) == '{"invalid":true}'


def settled_store(h, *, caught=True, bonus=()):
    store = native.NativeFishingStore(h.identity_id, -100991060001)
    ledger = store.journal()
    query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait",
                            now=h.now, projection_basis=store.basis())
    data = response(settled=True)
    data["session"]["result"].update(caught=caught, bonusLoot=list(bonus))
    if not caught:
        data["session"]["result"]["fish"] = None
    data["context"] = context()["context"]
    data["context"].update(serverNow=h.now * 1000, quota={"used": 1, "remaining": 4, "limit": 5})
    ledger.accept(query, data)
    return store


def test_native_gains_and_accounted_marker_commit_once_together(fishing_db):
    h = fishing_db

    async def scenario():
        store = settled_store(h)
        assert store.project(h.now)
        assert not store.project(h.now)

    asyncio.run(scenario())
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    identity = state_module.get_identity_state(h.identity_id)
    assert identity[native.STATE_KEY]["phase"] == "accounted"
    assert identity["fishing_daily_count"] == 1 and identity["fishing_daily_limit"] == 5
    summary = json.loads(identity["fishing_daily_catch_summary_json"])
    assert summary["rods"] == 1 and summary["fish"] == {"fish": 2}
    assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fish"] == 2


@pytest.mark.parametrize("existing_due", [0, 1700000200])
def test_native_catch_merges_transfer_queue_once_and_survives_restart(fishing_db, existing_due):
    h = fishing_db
    h.identity.update(fishing_transfer_target_id=h.other_id,
                      fishing_caught_fish_json='{"fish": 3, "older-fish": 1}',
                      fishing_transfer_due_at=existing_due)

    async def scenario():
        store = settled_store(h)
        assert store.project(h.now)
        assert not store.project(h.now)

    asyncio.run(scenario())
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    identity = state_module.get_identity_state(h.identity_id)
    assert identity[native.STATE_KEY]["phase"] == "accounted"
    assert json.loads(identity["fishing_caught_fish_json"]) == {"fish": 5, "older-fish": 1}
    assert identity["fishing_transfer_target_id"] == h.other_id
    assert identity["fishing_transfer_due_at"] == (
        existing_due or h.now + lifecycle.fishing.fishing_behavior.FISHING_TRANSFER_QUEUE_DELAY_SEC)
    assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fish"] == 2


@pytest.mark.parametrize("change", ["disabled", "canary", "target", "queue", "timer", "off"])
def test_native_transfer_preserves_concurrent_plan_and_timer_neutral_canary(fishing_db, change):
    h = fishing_db
    h.identity.update(fishing_transfer_target_id=0 if change == "off" else h.other_id,
                      fishing_caught_fish_json='{"older-fish": 1}', fishing_transfer_due_at=h.now + 300)

    async def scenario():
        store = settled_store(h)
        if change == "disabled":
            h.identity["fishing_enabled"] = False
        elif change == "target":
            h.identity["fishing_transfer_target_id"] = 771122
        elif change == "queue":
            h.identity["fishing_caught_fish_json"] = '{"manually-edited": 4}'
        elif change == "timer":
            h.identity["fishing_transfer_due_at"] = h.now + 500
        before = {key: h.identity[key] for key in (
            "fishing_caught_fish_json", "fishing_transfer_due_at", "fishing_transfer_target_id")}
        assert store.project(h.now, update_schedule=change != "canary")
        assert {key: h.identity[key] for key in before} == before
        assert h.identity[native.STATE_KEY]["phase"] == "accounted"
        assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fish"] == 2

    asyncio.run(scenario())


def test_native_transfer_rolls_back_with_inventory_on_failed_save(fishing_db, monkeypatch):
    h = fishing_db
    h.identity.update(fishing_transfer_target_id=h.other_id,
                      fishing_caught_fish_json='{"older-fish": 1}', fishing_transfer_due_at=0)

    async def scenario():
        store = settled_store(h)
        before = deepcopy(h.identity)
        inventory = deepcopy(state_module.get_storage_bag_records())
        monkeypatch.setattr(persistence, "save_state", lambda: False)
        with pytest.raises(ProtocolError, match="projection_save_failed"):
            store.project(h.now)
        assert h.identity == before
        assert state_module.get_storage_bag_records() == inventory
        assert persisted(h.identity_id)["phase"] == "settled"

    asyncio.run(scenario())


@pytest.mark.parametrize("caught", [True, False])
@pytest.mark.parametrize("next_day", [True, False])
def test_native_transfer_only_queues_fish_not_bonus_loot_including_late_recovery(fishing_db, caught, next_day):
    h = fishing_db
    h.identity.update(fishing_transfer_target_id=h.other_id,
                      fishing_caught_fish_json='{"older-fish": 1}', fishing_transfer_due_at=0)

    async def scenario():
        store = settled_store(h, caught=caught, bonus=[{"name": "gem", "qty": 3}])
        assert store.project(h.now + (86400 if next_day else 0))
        assert json.loads(h.identity["fishing_caught_fish_json"]) == (
            {"older-fish": 1, "fish": 2} if caught else {"older-fish": 1})
        assert (h.identity["fishing_transfer_due_at"] > 0) is caught
        assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"] == (
            {"fish": 2, "gem": 3, "bait": 5} if caught else {"gem": 3, "bait": 5})

    asyncio.run(scenario())


@pytest.mark.parametrize("key", [native.STATE_KEY, supply.STATE_KEY])
def test_existing_transfer_queue_waits_for_unresolved_native_receipt(fishing_env, monkeypatch, key):
    h = fishing_env
    h.identity.update(fishing_transfer_target_id=h.other_id,
                      fishing_caught_fish_json='{"older-fish": 1}', fishing_transfer_due_at=0)
    h.identity[key] = {"invalid": True}
    gift = AsyncMock()
    monkeypatch.setattr(lifecycle.fishing, "start_storage_bag_gift_batch", gift)
    before = deepcopy(h.identity)
    with state_module.use_identity(h.identity_id):
        assert asyncio.run(lifecycle.fishing._run_pending_fishing_transfer(h.now)) is False
    gift.assert_not_awaited()
    assert h.identity == before


def test_native_accounted_transfer_hands_off_once_through_existing_queue(fishing_db, monkeypatch):
    h = fishing_db
    h.identity.update(fishing_transfer_target_id=h.other_id,
                      fishing_caught_fish_json='{"older-fish": 1}', fishing_transfer_due_at=0)
    gift = AsyncMock(return_value=(True, "queued", {}))
    monkeypatch.setattr(lifecycle.fishing, "start_storage_bag_gift_batch", gift)

    async def scenario():
        store = settled_store(h)
        assert store.project(h.now)
        with state_module.use_identity(h.identity_id):
            due = h.identity["fishing_transfer_due_at"]
            assert await lifecycle.fishing._run_pending_fishing_transfer(due - 1) is False
            assert await lifecycle.fishing._run_pending_fishing_transfer(due) is True
            assert await lifecycle.fishing._run_pending_fishing_transfer(due + 1) is False
        assert h.identity["fishing_caught_fish_json"] == ""
        assert h.identity["fishing_transfer_due_at"] == 0
        assert h.identity[native.STATE_KEY]["phase"] == "accounted"

    asyncio.run(scenario())
    gift.assert_awaited_once_with([{
        "source_identity_id": h.identity_id, "target_identity_id": h.other_id,
        "items": [{"item_name": "fish", "quantity": 2, "method": "gift"},
                  {"item_name": "older-fish", "quantity": 1, "method": "gift"}],
    }], target_identity_id=h.other_id, stop_on_error=True)


def test_v3_fish_bonus_and_consumption_commit_together_without_double_bonus_bait(fishing_db):
    h = fishing_db

    async def scenario():
        state_module.set_storage_bag_records({str(h.identity_id): {"identity_id": h.identity_id,
            "items": {"bait": 5}, "sections": {}, "empty": False}})
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.journal()
        query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait",
                                projection_basis=store.basis())
        data = response(settled=True)
        data["context"]["serverNow"] = h.now * 1000
        data["session"]["result"]["bonusLoot"] = [{"name": "gem", "qty": 3}, {"name": "bait", "qty": 2}]
        data["context"]["baits"][0]["count"] = 6
        data["context"]["shop"] = {"activeChum": {"name": "chum", "remaining": 3},
                                    "chums": [{"name": "chum", "usedToday": 1}]}
        ledger.accept(query, data)
        assert store.project(h.now)
        assert store.project(h.now) is False
        items = state_module.get_storage_bag_records()[str(h.identity_id)]["items"]
        assert items == {"fish": 2, "gem": 3, "bait": 6}
        summary = json.loads(h.identity["fishing_daily_catch_summary_json"])
        assert summary["rods"] == 1 and summary["fish"] == {"fish": 2}
        assert summary["rewards"] == {"gem": 3, "bait": 2}
        assert h.identity["fishing_active_chum_name"] == "chum" and h.identity["fishing_chum_rods_remaining"] == 3
        assert json.loads(h.identity["fishing_chum_counts"]) == {"chum": 1}

    asyncio.run(scenario())
    assert persisted(h.identity_id)["version"] == 3 and persisted(h.identity_id)["phase"] == "accounted"


def test_v2_recovery_does_not_invent_resources_or_rewrite_old_version(fishing_db):
    h = fishing_db

    async def scenario():
        store = settled_store(h)
        record = h.identity[native.STATE_KEY]
        record.pop("settlement_resources")
        record["version"] = 2
        validate(record)
        assert persistence.save_state()
        assert store.project(h.now)
        assert h.identity[native.STATE_KEY]["version"] == 2
        assert "settlement_resources" not in h.identity[native.STATE_KEY]
        assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"] == {"fish": 2}

    asyncio.run(scenario())


def test_projection_failure_rolls_back_inventory_and_keeps_receipt(fishing_db, monkeypatch):
    h = fishing_db

    async def scenario():
        store = settled_store(h)
        before = deepcopy(h.identity)
        items = deepcopy(state_module.get_storage_bag_records())
        monkeypatch.setattr(persistence, "save_state", lambda: False)
        with pytest.raises(ProtocolError, match="projection_save_failed"):
            store.project(h.now)
        assert h.identity == before
        assert state_module.get_storage_bag_records() == items
        assert persisted(h.identity_id)["phase"] == "settled"

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["facts", "inventory"])
def test_manual_changes_block_duplicate_projection(fishing_db, change):
    h = fishing_db

    async def scenario():
        store = settled_store(h)
        if change == "facts":
            h.identity["fishing_daily_count"] = 2
        else:
            state_module.set_storage_bag_records({str(h.identity_id): {"items": {"fish": 2}, "updated_at": h.now}})
        before = deepcopy(state_module._meta_state)
        with pytest.raises(ProtocolError, match="projection_basis_changed"):
            store.project(h.now)
        assert state_module._meta_state == before

    asyncio.run(scenario())


def test_ui_disabled_after_result_keeps_gains_but_does_not_reschedule(fishing_db):
    h = fishing_db

    async def scenario():
        store = settled_store(h)
        h.identity.update(fishing_enabled=False, next_fishing_time=987654)
        assert store.project(h.now)
        assert h.identity["next_fishing_time"] == 987654 and not h.identity["fishing_enabled"]
        assert h.identity["fishing_daily_count"] == 1

    asyncio.run(scenario())


def test_previous_day_receipt_does_not_reset_current_day_counters(fishing_db):
    h = fishing_db

    async def scenario():
        store = settled_store(h)
        assert store.project(h.now + 86400)
        assert h.identity["fishing_daily_count"] == 0
        assert h.identity["fishing_daily_catch_summary_json"] == ""
        assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fish"] == 2

    asyncio.run(scenario())


def test_new_cast_requires_accounting_and_captures_new_basis(fishing_db):
    h = fishing_db

    async def scenario():
        store = settled_store(h)
        first_id = h.identity[native.STATE_KEY]["cast_id"]
        with pytest.raises(ProtocolError):
            start(store.journal())
        assert store.project(h.now)
        ledger = store.journal()
        query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait",
                                projection_basis=store.basis())
        assert query.cast_id != first_id
        assert ledger.record["projection_basis"] == store.basis()

    asyncio.run(scenario())


def test_cancelled_worker_drains_and_keeps_settlement_without_rescheduling(fishing_db):
    h = fishing_db
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def transport(request):
        action = request["safe_summary"]["endpoint"]
        calls.append(action)
        if action == "context":
            return context()
        assert action == "cast"
        entered.set()
        assert release.wait(5)
        done.set()
        return response(settled=True)

    async def scenario():
        task = asyncio.create_task(run_native_fishing_production_flow(
            h.identity_id, player_id=-100991060001, token="df_NATIVE_FIXTURE", init_data="fixture",
            site_id="west-shore", model_id="ngw", bait_id="bait", transport=transport,
            operation_check=lambda: True,
        ))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        original_timer = h.identity["next_fishing_time"]
        release.set()
        with pytest.raises(MiniAppFlowCancelled) as caught:
            await task
        assert done.is_set()
        assert caught.value.result["committed"]
        assert h.identity[native.STATE_KEY]["phase"] == "accounted"
        assert h.identity["next_fishing_time"] == original_timer
        assert calls == ["context", "cast"]

    try:
        asyncio.run(scenario())
    finally:
        release.set()


@pytest.mark.parametrize("key", ["identity_id", "account_id", "player_id"])
def test_project_rejects_another_owners_receipt(fishing_db, key):
    h = fishing_db

    async def scenario():
        store = settled_store(h)
        h.identity[native.STATE_KEY][key] += 1
        with pytest.raises(ProtocolError, match="owner_mismatch"):
            store.project(h.now)
        assert h.identity["fishing_daily_count"] == 0

    asyncio.run(scenario())
