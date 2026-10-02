import asyncio
from copy import deepcopy
import json
import sqlite3
import threading
from unittest.mock import Mock

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
from model.timing import get_day_key


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


def settled_store(h):
    store = native.NativeFishingStore(h.identity_id, -100991060001)
    ledger = store.journal()
    query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait",
                            now=h.now, projection_basis=store.basis())
    data = response(settled=True)
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


@pytest.mark.parametrize("change_during_read", [False, True])
def test_empty_rod_recovery_uses_fresh_baits_without_overwriting_other_loot(fishing_db, change_during_read):
    h = fishing_db
    calls = []

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.journal()
        query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait",
                                now=h.now, projection_basis=store.basis())
        ledger.accept(query, fighting())
        state_module.set_storage_bag_records({str(h.identity_id): {"items": {"stone": 237, "bait": 8}}})
        timer = h.identity["next_fishing_time"]

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            assert calls == ["state"]
            if change_during_read:
                # Simulate a concurrent normal-module inventory update.
                store._on_loop(lambda: state_module.set_storage_bag_records(
                    {str(h.identity_id): {"items": {"stone": 238, "bait": 8}}}))
            data = response(settled=True)
            data["session"]["result"] = {"ready": True, "caught": False, "bonusLoot": []}
            data["context"]["serverNow"] = h.now * 1000
            data["context"]["baits"][0]["count"] = 7
            return data

        result = await run_native_fishing_production_flow(
            h.identity_id, player_id=-100991060001, token="df_FIXTURE", init_data="fixture", site_id="west-shore",
            model_id="ngw", bait_id="bait", transport=transport, operation_check=lambda: True,
            recovery_only=True, update_schedule=False,
        )
        items = state_module.get_storage_bag_records()[str(h.identity_id)]["items"]
        assert h.identity["next_fishing_time"] == timer
        if change_during_read:
            assert result["error"] == "native_projection_basis_changed"
            assert h.identity[native.STATE_KEY]["phase"] == "settled"
            assert items == {"stone": 238, "bait": 8}
        else:
            assert result["committed"] and not result["outcome_unknown"]
            assert result["ok"] and result["status"] == "settled" and not result["error"]
            assert h.identity[native.STATE_KEY]["phase"] == "accounted"
            assert items == {"stone": 237, "bait": 7}
            assert h.identity["fishing_daily_count"] == 1

    asyncio.run(scenario())


def test_recovery_uses_same_journal_for_pending_receipt_and_reported_rewards(fishing_db):
    h = fishing_db

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.journal()
        query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait",
                                now=h.now, projection_basis=store.basis())
        ledger.accept(query, fighting())
        _, proof, details = next(fight_steps(ledger.record["remote"]["fight"]))
        ledger.prepare("checkpoint", proof=proof, details=details)
        data = response(settled=True)
        data["session"]["result"] = {"ready": True, "caught": False,
                                     "bonusLoot": [{"name": "waterweed", "qty": 1}]}
        data["context"]["serverNow"] = h.now * 1000
        result = await run_native_fishing_production_flow(
            h.identity_id, player_id=-100991060001, token="df_FIXTURE", init_data="fixture", site_id="west-shore",
            model_id="ngw", bait_id="bait", transport=lambda request: data, operation_check=lambda: True,
            recovery_only=True, update_schedule=False,
        )
        assert result["committed"] and result["ok"] and result["status"] == "settled"
        assert not result["outcome_unknown"] and not result["error"]
        assert result["data"]["rewards"] == {"waterweed": 1}
        assert h.identity[native.STATE_KEY]["phase"] == "accounted"
        assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["waterweed"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("gains", ["fish", "bonus", "none"])
def test_retained_settlement_refresh_never_rebases_gains(fishing_db, gains):
    h = fishing_db

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.journal()
        query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait",
                                now=h.now, projection_basis=store.basis())
        data = response(settled=True)
        data["context"]["serverNow"] = h.now * 1000
        if gains != "fish":
            data["session"]["result"] = {"ready": True, "caught": False,
                                         "bonusLoot": [{"name": "gem", "qty": 1}] if gains == "bonus" else []}
        ledger.accept(query, data)
        state_module.set_storage_bag_records({str(h.identity_id): {"items": {"stone": 9, "bait": 20}}})
        before = deepcopy(h.identity[native.STATE_KEY])
        calls = []

        def transport(request):
            calls.append(request["safe_summary"]["endpoint"])
            fresh = deepcopy(data)
            fresh["context"]["baits"][0]["count"] = 6
            return fresh

        result = await run_native_fishing_production_flow(
            h.identity_id, player_id=-100991060001, token="df_FIXTURE", init_data="fixture", site_id="west-shore",
            model_id="ngw", bait_id="bait", transport=transport, operation_check=lambda: True,
            recovery_only=True, update_schedule=False,
        )
        assert calls == ["state"]
        if gains == "none":
            assert result["committed"]
            assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"] == {"stone": 9, "bait": 6}
        else:
            assert result["error"] == "native_projection_basis_changed"
            assert h.identity[native.STATE_KEY] == before
            assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"] == {"stone": 9, "bait": 20}

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["facts", "wrong_session", "missing_context"])
def test_empty_recovery_does_not_relax_scope_or_fact_checks(fishing_db, change):
    h = fishing_db

    async def scenario():
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.journal()
        query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait",
                                now=h.now, projection_basis=store.basis())
        data = response(settled=True)
        data["session"]["result"] = {"ready": True, "caught": False, "bonusLoot": []}
        data["context"]["serverNow"] = h.now * 1000
        ledger.accept(query, data)
        state_module.set_storage_bag_records({str(h.identity_id): {"items": {"stone": 99}}})
        if change == "facts":
            h.identity["fishing_daily_count"] = 3
        elif change == "wrong_session":
            data["session"]["sessionId"] = "foreign"
        else:
            data.pop("context")
        result = await run_native_fishing_production_flow(
            h.identity_id, player_id=-100991060001, token="df_FIXTURE", init_data="fixture", site_id="west-shore",
            model_id="ngw", bait_id="bait", transport=lambda request: data, operation_check=lambda: True,
            recovery_only=True, update_schedule=False,
        )
        assert not result.get("committed")
        assert h.identity[native.STATE_KEY]["phase"] == "settled"
        assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"] == {"stone": 99}

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


@pytest.mark.parametrize("selected", [False, True])
def test_public_only_selection_controls_projection_schedule(fishing_db, monkeypatch, selected):
    h = fishing_db
    from model.features import fishing_runtime as fishing
    monkeypatch.setattr(fishing, "is_cave_public_auto_enabled", lambda action, identity: selected)

    async def scenario():
        h.identity["fishing_enabled"] = False
        store = settled_store(h)
        timer = h.identity["next_fishing_time"]
        assert store.project(h.now)
        assert h.identity["next_fishing_time"] == (h.now + 30 if selected else timer)
        assert not h.identity["fishing_enabled"]
        assert h.identity["fishing_daily_count"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("has_today_summary", [False, True])
def test_previous_day_recovery_with_current_quota_does_not_add_today_rod(fishing_db, has_today_summary):
    h = fishing_db

    async def scenario():
        day = get_day_key(h.now)
        if has_today_summary:
            h.identity["fishing_daily_catch_summary_json"] = json.dumps(
                {"day": day, "rods": 2, "fish": {"today-fish": 2}, "rewards": {}})
        summary = h.identity["fishing_daily_catch_summary_json"]
        store = native.NativeFishingStore(h.identity_id, -100991060001)
        ledger = store.journal()
        query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait",
                                now=h.now - 86400, projection_basis=store.basis())
        data = response(settled=True)
        data["context"].update(serverNow=h.now * 1000, quota={"used": 2, "remaining": 3, "limit": 5})
        data["session"]["result"]["bonusLoot"] = [{"name": "old-reward", "qty": 1}]
        ledger.accept(query, data)
        assert store.project(h.now)
        assert not store.project(h.now)
        assert h.identity["fishing_daily_count"] == 2
        assert h.identity["fishing_daily_catch_summary_json"] == summary
        assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"] == {
            "fish": 2, "old-reward": 1, "bait": 5}

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
