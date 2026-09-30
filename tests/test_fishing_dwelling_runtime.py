import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from model.features import cave_treasure_runtime as cave
from model.features import fishing_runtime as fishing
from model.features import fishing_dwelling_runtime as native
from model.features.miniapp_common import MiniAppFlowCancelled
from model import ui, state as state_module
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_dwelling_journal import Store, start
from model.features import fishing_dwelling_supply as supply
from test_fishing_dwelling_supply import shop_context, SETTINGS


fishing_env = lifecycle.fishing_env


def configure(h):
    h.identity["fishing_pond"] = "青溪浅滩"
    h.identity["fishing_bait"] = "凡饵"
    raw = h.session["result"]["data"]["raw"]
    raw["account"]["commandCenter"] = {"entries": [{"key": "fishing", "status": "integrated"}]}
    raw["characterModel"] = {"selectedId": "ngw"}


def test_native_requires_explicit_canary_and_keeps_both_existing_locks(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)

    async def flow(identity_id, **kwargs):
        assert cave._public_entry_lock(identity_id).locked()
        assert fishing._fishing_send_lock(identity_id).locked()
        assert kwargs["player_id"] == h.session["player_id"]
        assert kwargs["site_id"] == "west-shore" and kwargs["model_id"] == "ngw"
        assert kwargs["bait_choice"] == "凡饵"
        assert kwargs["update_schedule"] is False
        assert kwargs["operation_check"]()
        return {"ok": True, "committed": True, "status": "settled", "data": {"catches": {"fish": 2}}}

    worker = AsyncMock(side_effect=flow)
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_canary=True))
    assert result["ok"] and result["extra"]["native"]
    h.flow.assert_not_awaited()
    h.external.assert_not_awaited()
    assert worker.await_count == 1
    assert not cave._public_entry_lock(h.identity_id).locked()


def test_normal_public_action_does_not_enable_native_before_acceptance(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    worker = AsyncMock()
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    worker.assert_not_awaited()
    assert h.flow.await_count == 1


@pytest.mark.parametrize("blocked", ["model", "directory", "disabled", "pond"])
def test_canary_missing_prerequisites_never_falls_back_to_legacy(fishing_env, monkeypatch, blocked):
    h = fishing_env
    configure(h)
    raw = h.session["result"]["data"]["raw"]
    if blocked == "model":
        raw.pop("characterModel")
    elif blocked == "directory":
        raw["account"].pop("commandCenter")
    elif blocked == "disabled":
        h.identity["fishing_enabled"] = False
    else:
        h.identity["fishing_pond"] = "unknown"
    worker = AsyncMock()
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_canary=True))
    assert not result["ok"]
    worker.assert_not_awaited()
    h.flow.assert_not_awaited()
    h.external.assert_not_awaited()


def test_native_unknown_never_calls_legacy_worker_or_marks_daily_done(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    store = Store()
    start(store.open(owner=(h.identity_id, 7106, h.session["player_id"])))
    h.identity[native.STATE_KEY] = deepcopy(store.record)
    worker = AsyncMock(return_value={"ok": False, "status": "operation_pending", "outcome_unknown": True})
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert not result["ok"] and result["extra"]["outcome_unknown"]
    assert worker.await_count == 1
    h.flow.assert_not_awaited()
    h.external.assert_not_awaited()
    h.daily.assert_not_awaited()
    assert h.identity[native.STATE_KEY] == store.record


def test_corrupt_native_record_keeps_legacy_recovery_blocked(fishing_env):
    h = fishing_env
    h.identity[native.STATE_KEY] = {"phase": "accounted"}
    assert native.pending(h.identity)
    result = fishing.recover_fishing_result_pending(h.identity_id)
    assert result["extra"]["status"] == "native_operation_pending"


def test_canary_cancelled_result_does_not_emit_a_second_summary(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    worker = AsyncMock(side_effect=MiniAppFlowCancelled({"ok": True, "committed": True,
                                                       "data": {"catches": {"fish": 1}}}))
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    with pytest.raises(MiniAppFlowCancelled) as caught:
        asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_canary=True))
    assert caught.value.result["extra"]["committed"]
    h.audit.assert_not_awaited()
    h.daily.assert_not_awaited()


def test_manual_canary_alias_is_not_in_scheduled_action_list(fishing_env):
    assert ui._cave_public_entry_runner(fishing_env.identity_id, "fishing_native_canary") is not None
    assert "fishing_native_canary" not in ui._cave_public_actions_from_config()


def test_pending_supply_also_blocks_legacy_routes(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    h.identity[supply.STATE_KEY] = {"invalid": True}
    assert native.pending(h.identity)
    assert fishing.recover_fishing_result_pending(h.identity_id)["extra"]["status"] == "native_operation_pending"
    worker = AsyncMock()
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert not result["ok"]
    h.flow.assert_not_awaited()
    h.external.assert_not_awaited()


def test_supply_keeps_original_config_and_is_not_a_completed_rod(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    h.identity.update(fishing_auto_buy_bait_enabled=True, fishing_auto_buy_bait_count=11,
                      fishing_auto_chum_enabled=True, fishing_chum_names='["米糠小窝"]')
    before_timer = h.identity["next_fishing_time"]

    async def flow(identity_id, **kwargs):
        assert kwargs["supply_settings"] == {"bait_choice": "凡饵", "auto_buy": True, "buy_count": 11, "chum_names": ("米糠小窝",)}
        assert kwargs["operation_check"]()
        h.identity["fishing_auto_buy_bait_count"] = 8
        assert not kwargs["operation_check"]()
        return {"status": "supplied", "supply_committed": True, "data": {"supply_action": "buy-bait"}}

    monkeypatch.setattr(native, "run_native_fishing_production_flow", flow)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_canary=True))
    assert result["ok"] and result["extra"]["supply_committed"]
    assert not result["extra"]["committed"]
    assert h.identity["next_fishing_time"] == before_timer
    h.daily.assert_not_awaited()


def pending_receipt(h, key):
    store = Store()
    owner = (h.identity_id, 7106, h.session["player_id"])
    if key == native.STATE_KEY:
        start(store.open(owner=owner))
    else:
        journal = supply.SupplyJournal(owner=owner, read_current=lambda: store.record,
                                       compare_and_save=store.save, is_owner_current=lambda: True)
        journal.prepare(context=shop_context(), site_id="west-shore", model_id="ngw", **SETTINGS)
    return store.record


@pytest.mark.parametrize("corrupt", [False, True])
@pytest.mark.parametrize("key", [native.STATE_KEY, supply.STATE_KEY])
@pytest.mark.parametrize("action", ["clear", "initial", "status"])
def test_unresolved_native_records_survive_legacy_cleanup_and_day_rollover(fishing_env, key, action, corrupt):
    h = fishing_env
    configure(h)
    h.identity[key] = {"phase": "pending"} if corrupt else pending_receipt(h, key)
    h.identity.update(fishing_daily_day="2023-11-13", fishing_daily_count=5,
                      fishing_caught_fish_json='{"fish":2}', fishing_transfer_due_at=h.now + 20)
    before = deepcopy(h.identity)
    with state_module.use_identity(h.identity_id):
        if action == "clear":
            fishing.clear_fishing_state(persist=True)
        elif action == "initial":
            fishing.schedule_fishing_initial_check(h.now, persist=True)
        else:
            assert "回执待核对" in fishing.get_fishing_status_text()
    assert h.identity == before


@pytest.mark.parametrize("wait", [0, 120])
@pytest.mark.parametrize("key", [native.STATE_KEY, supply.STATE_KEY])
def test_native_recovery_precedes_daily_done_but_respects_timer(fishing_env, monkeypatch, key, wait):
    h = fishing_env
    h.identity[key] = pending_receipt(h, key)
    h.identity.update(fishing_daily_count=10, fishing_last_result="daily_limit", next_fishing_time=h.now + wait)
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", lambda: {"cave_public_fishing_identity_ids": [h.identity_id]})
    monkeypatch.setattr(ui, "_cave_public_background_daily_done", {("fishing", fishing.get_day_key(h.now), h.identity_id)})
    before = deepcopy(h.identity)
    assert ui._cave_public_background_action_due("fishing", h.identity_id, h.now) is (wait == 0)
    assert h.identity == before


def test_unselected_native_receipt_does_not_enable_background_action(fishing_env, monkeypatch):
    h = fishing_env
    h.identity[native.STATE_KEY] = {"phase": "pending"}
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", lambda: {"cave_public_fishing_identity_ids": []})
    assert not ui._cave_public_background_action_due("fishing", h.identity_id, h.now)


@pytest.mark.parametrize("key", [native.STATE_KEY, supply.STATE_KEY])
@pytest.mark.parametrize("next_day", [False, True])
def test_other_identity_daily_report_preserves_native_receipt_basis(fishing_env, key, next_day):
    h = fishing_env
    h.identity[key] = pending_receipt(h, key)
    h.identity.update(fishing_daily_count=10, fishing_last_result="daily_limit",
                      fishing_daily_catch_summary_json='{"day":"2023-11-15","rods":10,"fish":{"fish":10},"rewards":{}}')
    before = deepcopy(h.identity)
    now = h.now + 86400 if next_day else h.now
    with state_module.use_identity(h.other_id):
        day, entries, changed = fishing._enabled_fishing_daily_entries(now)
        entry = next(item for item in entries if item["identity_id"] == h.identity_id)
        assert entry["active_followup"]
        assert not asyncio.run(lifecycle.REAL_DAILY_REPORT(now))
    assert h.identity == before
    h.audit.assert_not_awaited()
