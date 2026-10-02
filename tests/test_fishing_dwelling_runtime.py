import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from model.features import cave_treasure_runtime as cave
from model.features import fishing_runtime as fishing
from model.features import fishing_dwelling_runtime as native, concubine
from model.features.miniapp_common import MiniAppFlowCancelled
from model import ui, state as state_module
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_dwelling_journal import Store, start
from model.features import fishing_dwelling_supply as supply
from test_fishing_dwelling_supply import shop_context, SETTINGS


fishing_env = lifecycle.fishing_env
fishing_db = lifecycle.fishing_db


def configure(h):
    h.identity["fishing_pond"] = "青溪浅滩"
    h.identity["fishing_bait"] = "凡饵"
    raw = h.session["result"]["data"]["raw"]
    raw["account"]["commandCenter"] = {"entries": [{"key": "fishing", "status": "integrated"}]}
    raw["characterModel"] = {"selectedId": "ngw"}


def enable_voyage_handoff(h):
    h.identity.update(concubine_voyage_settled_at=h.now, concubine_name="南宫婉",
                      concubine_kind="道心侍妾", concubine_availability="available",
                      concubine_affinity=320, concubine_voyage_enabled=True,
                      concubine_voyage_status="idle")
    state_module.set_miniapp_auto_config({
        "cave_public_entry_url": h.url, "cave_public_fishing_enabled": True,
        "cave_public_fishing_identity_ids": [h.identity_id]})


@pytest.mark.parametrize("public_only", [False, True])
def test_voyage_handoff_is_bounded_and_never_changes_fishing_plan(fishing_env, public_only):
    h = fishing_env
    enable_voyage_handoff(h)
    h.identity["fishing_enabled"] = not public_only
    before = deepcopy(h.identity)
    assert native.voyage_launch_wait_reason(h.identity_id, h.now)
    assert native.voyage_launch_wait_reason(h.identity_id, h.now + native.VOYAGE_HANDOFF_SEC - 1)
    assert not native.voyage_launch_wait_reason(h.identity_id, h.now + native.VOYAGE_HANDOFF_SEC)
    assert not native.voyage_launch_wait_reason(h.identity_id, h.now - 1)
    assert h.identity == before


@pytest.mark.parametrize("done", ["quota", "no_rod", "exhausted", "disabled", "future_timer", "no_entry", "old_pending"])
def test_voyage_does_not_wait_when_fishing_cannot_run(fishing_env, done):
    h = fishing_env
    enable_voyage_handoff(h)
    if done == "quota":
        h.identity["fishing_daily_count"] = h.identity["fishing_daily_limit"]
    elif done == "no_rod":
        h.identity["fishing_last_result"] = "未持有鱼竿，今日跳过"
    elif done == "exhausted":
        h.identity["fishing_last_result"] = "fishing_daily_limit_reached"
    elif done == "disabled":
        h.identity["fishing_enabled"] = False
        state_module.set_miniapp_auto_config({"cave_public_entry_url": h.url})
    elif done == "no_entry":
        state_module.set_miniapp_auto_config({})
    elif done == "old_pending":
        h.identity[native.STATE_KEY] = pending_receipt(h, native.STATE_KEY)
        h.identity[native.STATE_KEY]["created_at"] = h.now - 3600
    else:
        h.identity["next_fishing_time"] = h.now + native.VOYAGE_HANDOFF_SEC
    assert not native.voyage_launch_wait_reason(h.identity_id, h.now)


def test_voyage_handoff_covers_both_launch_routes_but_not_return(fishing_env, monkeypatch):
    h = fishing_env
    enable_voyage_handoff(h)
    miniapp, command = AsyncMock(), AsyncMock()
    monkeypatch.setattr(concubine, "_send_voyage_miniapp_command", miniapp)
    monkeypatch.setattr(concubine.voyage_actions, "send", command)

    async def scenario():
        with state_module.use_identity(h.identity_id):
            assert not concubine._is_voyage_eligible(h.now)
            assert not await concubine._send_voyage_command(h.now)
            miniapp.assert_not_awaited()
            command.assert_not_awaited()
            assert concubine._is_voyage_eligible(h.now + native.VOYAGE_HANDOFF_SEC)
            h.identity.update(concubine_voyage_status="returned", concubine_voyage_return_at=h.now - 1)
            assert concubine._is_voyage_return_due(h.now)
            await concubine._send_voyage_return_command(h.now)
            assert miniapp.await_args.args[0] == "return"

    asyncio.run(scenario())


def test_live_fishing_lock_and_recent_receipt_block_voyage_after_handoff_expires(fishing_env):
    h = fishing_env
    enable_voyage_handoff(h)
    h.identity["concubine_voyage_settled_at"] = h.now - 3600

    async def scenario():
        async with fishing._fishing_send_lock(h.identity_id):
            assert native.voyage_launch_wait_reason(h.identity_id, h.now)
        assert not native.voyage_launch_wait_reason(h.identity_id, h.now)
        h.identity[native.STATE_KEY] = pending_receipt(h, native.STATE_KEY)
        h.identity[native.STATE_KEY]["created_at"] = h.now
        assert native.voyage_launch_wait_reason(h.identity_id, h.now + 299)
        assert not native.voyage_launch_wait_reason(h.identity_id, h.now + 300)

    asyncio.run(scenario())


def test_voyage_return_clock_survives_real_sqlite_reload_without_extension(fishing_db):
    h = fishing_db
    enable_voyage_handoff(h)
    assert native.persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert native.persistence.load_state()
    assert state_module.get_identity_state(h.identity_id)["concubine_voyage_settled_at"] == h.now
    assert not native.voyage_launch_wait_reason(h.identity_id, h.now + native.VOYAGE_HANDOFF_SEC)


def test_explicit_canary_keeps_both_existing_locks_without_rescheduling(fishing_env, monkeypatch):
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


def test_integrated_public_action_prefers_native_without_external_open(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    worker = AsyncMock(return_value={"ok": True, "committed": True, "status": "settled", "data": {"catches": {"fish": 1}}})
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert result["ok"]
    worker.assert_awaited_once()
    assert worker.call_args.kwargs["update_schedule"] is True
    h.flow.assert_not_awaited()
    h.external.assert_not_awaited()


@pytest.mark.parametrize("selected", [True, False])
def test_public_selection_does_not_require_enabling_standalone_module(fishing_env, monkeypatch, selected):
    h = fishing_env
    configure(h)
    h.identity["fishing_enabled"] = False
    monkeypatch.setattr(fishing, "is_cave_public_auto_enabled", lambda action, identity: selected)
    worker = AsyncMock(return_value={"ok": True, "committed": True, "status": "settled"})
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert result["ok"] is selected
    assert worker.await_count == int(selected)
    assert not h.identity["fishing_enabled"]
    h.external.assert_not_awaited()
    h.flow.assert_not_awaited()


def test_public_selection_removed_midflight_cancels_native_operation(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    timer = h.identity["next_fishing_time"]
    selected = [True]
    monkeypatch.setattr(fishing, "is_cave_public_auto_enabled", lambda action, identity: selected[0])
    async def worker(*args, **kwargs):
        assert kwargs["operation_check"]()
        selected[0] = False
        assert not kwargs["operation_check"]()
        return {"ok": False, "status": "cancelled"}
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert not result["ok"] and h.identity["fishing_enabled"]
    assert h.identity["next_fishing_time"] == timer


@pytest.mark.parametrize("reason", ["fishing_rod_missing", "fishing_daily_limit_reached", "fishing_companion_missing"])
def test_confirmed_terminal_skip_waits_until_next_day(fishing_env, monkeypatch, reason):
    h = fishing_env
    configure(h)
    h.identity.update(fishing_daily_day=fishing.get_day_key(h.now - 86400), fishing_daily_count=10)
    other_before = deepcopy(state_module.get_identity_state(h.other_id))
    worker = AsyncMock(return_value={"ok": False, "status": "blocked", "error": reason, "outcome_unknown": False})
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert result["ok"] and result["extra"]["terminal_skip"]
    assert result["extra"]["status"] == "skipped"
    assert {"fishing_rod_missing": "未持有鱼竿", "fishing_daily_limit_reached": "次数已用尽",
            "fishing_companion_missing": "无可用侍妾"}[reason] in h.identity["fishing_last_result"]
    assert h.identity["next_fishing_time"] > h.now + 1800
    assert h.identity["fishing_enabled"] and not h.identity["fishing_last_error"]
    assert state_module.get_identity_state(h.other_id) == other_before
    assert h.identity["fishing_daily_day"] == fishing.get_day_key(h.now)
    assert h.identity["fishing_daily_count"] == 0
    h.daily.assert_awaited_once()
    _, entries, _ = fishing._enabled_fishing_daily_entries(h.now)
    assert len(entries) == 1 and not entries[0]["reportable"]
    assert entries[0]["terminal_skip"] or entries[0]["daily_exhausted"]
    _, entries, _ = fishing._enabled_fishing_daily_entries(h.now + 86400)
    assert not entries[0]["terminal_skip"] and not entries[0]["daily_exhausted"]


@pytest.mark.parametrize("raises", [False, True])
def test_no_rod_failed_local_save_does_not_mark_day_done(fishing_env, monkeypatch, raises):
    h = fishing_env
    configure(h)
    before = h.identity["next_fishing_time"]
    worker = AsyncMock(return_value={"ok": False, "status": "blocked", "error": "fishing_rod_missing", "outcome_unknown": False})
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    def save():
        if raises:
            raise OSError("disk failure")
        return False
    monkeypatch.setattr(native.persistence, "save_state", save)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert not result["ok"] and not result["extra"]["terminal_skip"]
    assert h.identity["next_fishing_time"] == before


@pytest.mark.parametrize("change", ["disabled", "cancelled", "unknown"])
def test_no_rod_cannot_close_day_after_authority_lost(fishing_env, monkeypatch, change):
    h = fishing_env
    configure(h)
    timer = h.identity["next_fishing_time"]

    async def worker(*args, **kwargs):
        result = {"ok": False, "status": "blocked", "error": "fishing_rod_missing"}
        if change == "disabled":
            h.identity["fishing_enabled"] = False
        elif change == "unknown":
            result["outcome_unknown"] = True
        else:
            raise MiniAppFlowCancelled(result)
        return result

    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    if change == "cancelled":
        with pytest.raises(MiniAppFlowCancelled) as exc:
            asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
        result = exc.value.result
    else:
        result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert not result["ok"] and not result["extra"]["terminal_skip"]
    assert h.identity["next_fishing_time"] == (h.now + 1800 if change == "unknown" else timer)


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


def test_manual_recovery_alias_is_not_in_scheduled_action_list(fishing_env):
    assert ui._cave_public_entry_runner(fishing_env.identity_id, "fishing_native_recover") is not None
    assert "fishing_native_recover" not in ui._cave_public_actions_from_config()


def test_recovery_without_pending_record_never_loads_game_or_uses_old_route(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    worker = AsyncMock()
    session = AsyncMock()
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", session)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_recovery_only=True))
    assert result["extra"]["status"] == "recovery_not_needed"
    session.assert_not_awaited()
    worker.assert_not_awaited()
    h.external.assert_not_awaited()
    h.flow.assert_not_awaited()


def test_recovery_keeps_timer_and_passes_read_only_mode(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    h.identity[native.STATE_KEY] = deepcopy(pending_receipt(h, native.STATE_KEY))
    timer = h.identity["next_fishing_time"]
    worker = AsyncMock(return_value={"ok": False, "status": "recovery_wait"})
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_recovery_only=True))
    assert result["extra"]["status"] == "recovery_wait"
    assert worker.await_args.kwargs["recovery_only"] is True
    assert worker.await_args.kwargs["update_schedule"] is False
    assert h.identity["next_fishing_time"] == timer
    h.daily.assert_not_awaited()


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


def test_accounted_canary_clears_old_error_without_advancing_timer(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    h.identity.update(fishing_last_error="operation_pending", fishing_last_result="old pending result")
    timer = h.identity["next_fishing_time"]
    monkeypatch.setattr(native.persistence, "save_state", lambda: True)
    monkeypatch.setattr(native, "run_native_fishing_production_flow", AsyncMock(return_value={
        "ok": True, "committed": True, "status": "settled", "data": {"catches": {"fish": 1}},
    }))
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_canary=True))
    assert result["ok"]
    assert h.identity["fishing_last_error"] == ""
    assert h.identity["fishing_last_result"] == result["message"]
    assert h.identity["next_fishing_time"] == timer
