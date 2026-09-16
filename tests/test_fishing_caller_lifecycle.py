import asyncio
import copy
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, state as state_module
from model.features import cave_treasure_runtime as cave
from model.features import fishing_runtime as fishing
from model.features import fishing_miniapp as adapter
from model.features import storage_bag
from model.features.miniapp_common import MiniAppFlowCancelled
from model.timing import get_day_key
from model.webapp_core import MiniAppRequestPolicy


REAL_DAILY_REPORT = fishing._send_fishing_daily_completion_summary


@pytest.fixture
def fishing_env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    identity_id, other_id, now = 991060001, 991060002, 1700000000.0
    state_module.set_identity_account(identity_id, 7106)
    state_module.set_identity_account(other_id, 7107)
    identity = state_module.get_identity_state(identity_id)
    identity.update(fishing_enabled=True, fishing_pond="pond", fishing_bait="bait",
                    next_fishing_time=now - 1, fishing_phase="idle",
                    fishing_daily_day=get_day_key(now), fishing_daily_count=0, fishing_daily_limit=10)
    monkeypatch.setattr(fishing, "_SEND_LOCKS", {})
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    for module, name in ((fishing, "save_state"), (cave, "save_state"), (fishing, "mark_dirty"),
                         (fishing, "_fishing_miniapp_capture_store"), (cave, "_fishing_miniapp_capture_store"),
                         (cave, "_capture_store")):
        monkeypatch.setattr(module, name, Mock(return_value=True if name == "save_state" else None))
    items = Mock()
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", items)
    audit, daily, harvest = AsyncMock(return_value=True), AsyncMock(return_value=False), AsyncMock(return_value=True)
    monkeypatch.setattr(cave, "send_audit_log", audit)
    monkeypatch.setattr(fishing, "send_audit_log", audit)
    monkeypatch.setattr(cave, "_send_fishing_daily_completion_summary", daily)
    monkeypatch.setattr(fishing, "_send_fishing_daily_completion_summary", daily)
    monkeypatch.setattr(fishing, "_send_fishing_miniapp_harvest_summary", harvest)
    monkeypatch.setattr(fishing.time, "time", lambda: now)
    session = {"ok": True, "init_data": "fixture-init", "player_id": -100991060001,
               "result": {"ok": True, "data": {"raw": {"account": {"externalApps": {"groups": [{
                   "apps": [{"key": "fishing", "available": True, "action": "fishing"}],
               }]}}}}}}
    loader = AsyncMock(return_value=session)
    external = AsyncMock(return_value={"ok": True, "data": {
        "url": "/miniapp/xianxia-fishing?startapp=fish_FIXTURE106",
    }})
    result = {"ok": True, "status": "settled", "data": {
        "settled_count": 1, "expGain": 4, "catches": [{"fish": "fixture-fish", "rewards": []}],
    }}
    flow = AsyncMock(return_value=result)
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_external_action_production_flow", external)
    monkeypatch.setattr(cave, "run_fishing_miniapp_production_flow", flow)
    monkeypatch.setattr(fishing, "run_fishing_miniapp_production_flow", flow)
    yield SimpleNamespace(identity_id=identity_id, identity=identity, other_id=other_id, now=now,
                          items=items, session=session, loader=loader, external=external, flow=flow,
                          result=result, audit=audit, daily=daily, harvest=harvest,
                          url="https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE106")
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


@pytest.fixture
def fishing_db(fishing_env, monkeypatch, tmp_path):
    for name, value in {
        "DB_FILE": str(tmp_path / "fishing.db"), "_db_conn": None, "_db_initialized": False,
        "_schema_columns_ensured_key": None, "_schema_columns_ensured_version": None,
        "_persistence_snapshot_db_key": "", "_persisted_meta_snapshot": {}, "_persisted_identity_snapshots": {},
        "_state_dirty": False, "_last_flush_time": 0, "_last_save_failed_at": 0, "_last_save_error": "",
    }.items():
        monkeypatch.setattr(persistence, name, value)
    monkeypatch.setattr(persistence, "_try_write_live_guard_backup", Mock(return_value=False))
    monkeypatch.setattr(fishing, "save_state", persistence.save_state)
    monkeypatch.setattr(cave, "save_state", persistence.save_state)
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", storage_bag.apply_storage_bag_item_deltas)
    try:
        yield fishing_env
    finally:
        if persistence._db_conn is not None:
            persistence._db_conn.close()


def invalidate(h, change):
    if change == "removed":
        state_module.remove_identity(h.identity_id)
    elif change == "replaced":
        state_module.remove_identity(h.identity_id)
        state_module.set_identity_account(h.identity_id, 7106)
    elif change == "rebound":
        state_module.set_identity_account(h.identity_id, 7199)
    elif change == "identity_disabled":
        state_module.set_identity_enabled(h.identity_id, False)
    elif change == "paused":
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")
    elif change == "module_disabled":
        h.identity["fishing_enabled"] = False
    elif change == "schedule":
        h.identity.update(next_fishing_time=h.now + 8000, fishing_phase="replacement",
                          fishing_reply_to_msg_id=999, fishing_last_result="replacement")
    else:
        h.identity["fishing_" + change] = "replacement"


async def run_caller(h, caller):
    if caller == "public":
        return await cave.run_cave_public_fishing(h.identity_id, h.url, now=h.now)
    event = SimpleNamespace(id=106, message=SimpleNamespace(buttons=[[SimpleNamespace(
        text="fishing", button=SimpleNamespace(url="https://t.me/fanrenxiuxian_bot?startapp=fish_FIXTURE106"),
    )]]))
    with state_module.use_identity(h.identity_id):
        return await fishing.handle_fishing_miniapp_entry(event, "\u3010\u7075\u6eaa\u5782\u9493\u3011", h.now)


@pytest.mark.parametrize("boundary", ["loader", "external"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "identity_disabled", "paused", "pond", "bait", "schedule"])
def test_public_await_boundaries_do_not_continue_changed_ownership(fishing_env, boundary, change):
    h = fishing_env
    target = getattr(h, boundary)
    response = target.return_value
    changed_state = {}

    async def change_and_return(*_args, **_kwargs):
        invalidate(h, change)
        changed_state.update(copy.deepcopy(state_module._meta_state))
        return response

    target.side_effect = change_and_return
    result = asyncio.run(run_caller(h, "public"))
    assert not result["ok"]
    assert result["extra"].get("status") == "cancelled"
    h.flow.assert_not_awaited()
    if boundary == "loader":
        h.external.assert_not_awaited()
    h.items.assert_not_called()
    assert state_module._meta_state["identity_states"] == changed_state["identity_states"]


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "identity_disabled", "paused", "pond", "bait", "schedule", "module_disabled"])
def test_message_announcement_cannot_send_after_admission_changes(fishing_env, change):
    h = fishing_env
    changed_state = {}

    async def announce(*_args, **_kwargs):
        invalidate(h, change)
        changed_state.update(copy.deepcopy(state_module._meta_state))
        return True

    h.audit.side_effect = announce
    asyncio.run(run_caller(h, "message"))
    h.flow.assert_not_awaited()
    h.items.assert_not_called()
    assert state_module._meta_state["identity_states"] == changed_state["identity_states"]


@pytest.mark.parametrize("caller", ["public", "message"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
def test_late_result_cannot_write_to_another_owner(fishing_env, caller, change):
    h = fishing_env
    changed_state = {}

    async def late(*_args, **_kwargs):
        invalidate(h, change)
        changed_state.update(copy.deepcopy(state_module._meta_state))
        return h.result

    h.flow.side_effect = late
    asyncio.run(run_caller(h, caller))
    h.items.assert_not_called()
    assert state_module._meta_state["identity_states"] == changed_state["identity_states"]


@pytest.mark.parametrize("caller", ["public", "message"])
@pytest.mark.parametrize("change", ["identity_disabled", "paused", "pond", "bait", "schedule"])
def test_current_owner_keeps_confirmed_facts_without_rewriting_changed_plan(fishing_env, caller, change):
    h = fishing_env
    changed_state = {}

    async def late(*_args, **_kwargs):
        invalidate(h, change)
        changed_state.update(copy.deepcopy(h.identity))
        return h.result

    h.flow.side_effect = late
    asyncio.run(run_caller(h, caller))
    assert h.identity["fishing_daily_count"] == 1
    h.items.assert_called_once_with(h.identity_id, {"fixture-fish": 1}, persist=False)
    for key in ("next_fishing_time", "fishing_phase", "fishing_reply_to_msg_id", "fishing_last_result",
                "fishing_pond", "fishing_bait"):
        assert h.identity.get(key) == changed_state.get(key), key
    h.daily.assert_not_awaited()
    h.harvest.assert_not_awaited()


@pytest.mark.parametrize("caller", ["public", "message"])
@pytest.mark.parametrize("has_result", [False, True])
def test_cancelled_caller_adopts_only_confirmed_result_and_releases_locks(fishing_env, caller, has_result):
    h = fishing_env
    before = {}

    async def cancel(*_args, **_kwargs):
        before.update(copy.deepcopy(h.identity))
        raise MiniAppFlowCancelled(h.result if has_result else None)

    h.flow.side_effect = cancel
    with pytest.raises(MiniAppFlowCancelled):
        asyncio.run(run_caller(h, caller))
    if has_result:
        assert h.identity["fishing_daily_count"] == 1
        h.items.assert_called_once_with(h.identity_id, {"fixture-fish": 1}, persist=False)
    else:
        assert h.identity == before
        h.items.assert_not_called()
    assert h.identity["next_fishing_time"] == before["next_fishing_time"]
    assert not cave._public_entry_lock(h.identity_id).locked()
    assert not fishing._fishing_send_lock(h.identity_id).locked()
    h.daily.assert_not_awaited()
    h.harvest.assert_not_awaited()


def test_public_and_message_share_the_existing_game_lock(fishing_env):
    h = fishing_env

    async def run():
        async with fishing._fishing_send_lock(h.identity_id):
            result = await run_caller(h, "public")
            assert not result["ok"]
            assert result["extra"].get("status") == "busy"

    asyncio.run(run())
    h.loader.assert_not_awaited()
    h.flow.assert_not_awaited()


def test_public_switch_is_independent_of_the_disabled_message_module(fishing_env):
    h = fishing_env
    h.identity["fishing_enabled"] = False
    result = asyncio.run(run_caller(h, "public"))
    assert result["ok"]
    h.flow.assert_awaited_once()
    assert h.identity["fishing_daily_count"] == 1


def test_partial_catches_are_not_lost_when_a_later_round_has_no_rod(fishing_env):
    h = fishing_env
    result = {**h.result, "status": "no_rod", "error": "no_rod"}
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(result, h.now)
    assert h.identity["fishing_daily_count"] == 1
    h.items.assert_called_once_with(h.identity_id, {"fixture-fish": 1}, persist=False)
    assert h.identity["fishing_last_error"] == ""
    assert h.identity["next_fishing_time"] > h.now


@pytest.mark.parametrize("status", ["failed", "shop_failed", "request_budget"])
def test_partial_http_failure_uses_failure_delay_and_retry_after(fishing_env, status):
    h = fishing_env
    result = {**h.result, "status": status, "error": "fixture failure", "events": [{"retry_after_sec": 4000}]}
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(result, h.now)
    assert h.identity["fishing_daily_count"] == 1
    assert h.identity["next_fishing_time"] >= h.now + 4000
    assert h.identity["fishing_last_error"]


@pytest.mark.parametrize("caller", ["public", "message"])
def test_notification_failure_does_not_lose_completed_result(fishing_env, caller):
    h = fishing_env
    if caller == "public":
        h.audit.side_effect = RuntimeError("fixture notification failure")
    else:
        h.harvest.side_effect = RuntimeError("fixture notification failure")
    result = asyncio.run(run_caller(h, caller))
    assert result if caller == "message" else result["ok"]
    assert h.identity["fishing_daily_count"] == 1
    h.items.assert_called_once()
    h.flow.assert_awaited_once()


@pytest.mark.parametrize("caller", ["public", "message"])
@pytest.mark.parametrize("boundary", ["start", "result"])
def test_real_worker_is_joined_before_caller_locks_release(fishing_env, monkeypatch, caller, boundary):
    h = fishing_env
    entered, release = threading.Event(), threading.Event()
    requests = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        requests.append(endpoint)
        if endpoint == boundary:
            entered.set()
            assert release.wait(3)
        if endpoint == "start":
            return {"ok": True, "session": {"phase": "bite"}, "challenge": {
                "challengeId": "fixture106", "minDurationMs": 20, "maxDurationMs": 70000,
            }}
        if endpoint == "finish":
            return {"ok": True}
        if endpoint == "result":
            return {"ok": True, "ready": True, "result": {"fish": "fixture-fish", "expGain": 4}}
        raise AssertionError("cancelled worker dispatched a new round")

    async def flow(identity_id, **kwargs):
        return await adapter.run_fishing_miniapp_production_flow(
            identity_id, **{**kwargs, "init_data": "fixture-init", "transport": transport,
                            "sleeper": lambda _delay: None,
                            "adapter": replace(adapter.build_fishing_miniapp_adapter(),
                                               request_policy=MiniAppRequestPolicy(min_interval_sec=0))},
        )

    monkeypatch.setattr(cave, "run_fishing_miniapp_production_flow", flow)
    monkeypatch.setattr(fishing, "run_fishing_miniapp_production_flow", flow)

    async def run():
        task = asyncio.create_task(run_caller(h, caller))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            held = not task.done() and fishing._fishing_send_lock(h.identity_id).locked()
            if caller == "public":
                held = held and cave._public_entry_lock(h.identity_id).locked()
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled):
                await task
        assert held
        assert h.identity["fishing_daily_count"] == int(boundary == "result")
        assert requests == (["start"] if boundary == "start" else ["start", "finish", "result"])
        assert not fishing._fishing_send_lock(h.identity_id).locked()
        assert not cave._public_entry_lock(h.identity_id).locked()
        assert h.items.call_count == int(boundary == "result")

    asyncio.run(run())


@pytest.mark.parametrize("caller", ["public", "message"])
def test_each_caller_passes_a_live_guard_into_worker(fishing_env, caller):
    h = fishing_env

    async def flow(*_args, **kwargs):
        guard = kwargs.get("operation_check")
        assert callable(guard) and guard() is True
        h.identity["fishing_bait"] = "changed"
        assert guard() is False
        return {"ok": False, "status": "cancelled", "data": {}}

    h.flow.side_effect = flow
    asyncio.run(run_caller(h, caller))
    h.items.assert_not_called()
    assert h.identity["fishing_daily_count"] == 0


def test_public_entry_keeps_background_observation_guard_after_external_await(fishing_env):
    h = fishing_env
    allowed = True
    original = h.external.return_value

    async def external(*_args, **kwargs):
        nonlocal allowed
        assert kwargs["operation_check"]() is True
        allowed = False
        assert kwargs["operation_check"]() is False
        return original

    h.external.side_effect = external
    with cave.observe_cave_public_entry(h.identity_id, h.url, operation_check=lambda: allowed):
        result = asyncio.run(run_caller(h, "public"))
    assert result["extra"]["status"] == "cancelled"
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("caller", ["public", "message"])
def test_post_settlement_notification_cancellation_carries_result(fishing_env, caller):
    h = fishing_env
    if caller == "public":
        h.audit.side_effect = asyncio.CancelledError()
    else:
        h.harvest.side_effect = asyncio.CancelledError()
    with pytest.raises(MiniAppFlowCancelled) as raised:
        asyncio.run(run_caller(h, caller))
    assert raised.value.result
    assert h.identity["fishing_daily_count"] == 1
    h.items.assert_called_once()


def test_pre_worker_notification_cancellation_restores_only_its_unsent_marker(fishing_env):
    h = fishing_env
    before = copy.deepcopy(h.identity)
    h.audit.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_caller(h, "message"))
    h.flow.assert_not_awaited()
    h.items.assert_not_called()
    assert h.identity == before


def test_duplicate_message_entry_cannot_mutate_active_plan(fishing_env):
    h = fishing_env
    before = copy.deepcopy(h.identity)

    async def run():
        async with fishing._fishing_send_lock(h.identity_id):
            assert await run_caller(h, "message")

    asyncio.run(run())
    h.flow.assert_not_awaited()
    assert h.identity == before


@pytest.mark.parametrize("caller", ["public", "message"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "schedule"])
def test_notification_await_does_not_enter_replacement_daily_chain(fishing_env, caller, change):
    h = fishing_env
    target = h.audit if caller == "public" else h.harvest
    changed_state = {}

    async def notify(*_args, **_kwargs):
        invalidate(h, change)
        changed_state.update(copy.deepcopy(state_module._meta_state))
        return True

    target.side_effect = notify
    asyncio.run(run_caller(h, caller))
    h.daily.assert_not_awaited()
    assert state_module._meta_state["identity_states"] == changed_state["identity_states"]


@pytest.mark.parametrize("target", ["origin", "other"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "count", "day", "error"])
def test_daily_report_ack_does_not_mark_replaced_or_changed_facts(fishing_env, monkeypatch, target, change):
    h = fishing_env
    day = get_day_key(h.now)
    entries = []
    for identity_id in (h.identity_id, h.other_id):
        identity = state_module.get_identity_state(identity_id)
        identity.update(fishing_daily_day=day, fishing_daily_count=1, fishing_daily_limit=1,
                        fishing_last_error="before", fishing_daily_summary_day="")
        entries.append({"identity_id": identity_id, "day": day, "count": 1, "limit": 1,
                        "summary_day": "", "reportable": True, "name": str(identity_id)})
    monkeypatch.setattr(fishing, "_enabled_fishing_daily_entries", Mock(return_value=(day, entries, False)))
    target_id = h.identity_id if target == "origin" else h.other_id
    changed_state = {}

    async def notify(*_args, **_kwargs):
        if change == "removed":
            state_module.remove_identity(target_id)
        elif change == "replaced":
            state_module.remove_identity(target_id)
            state_module.set_identity_account(target_id, 7106)
        elif change == "rebound":
            state_module.set_identity_account(target_id, 7999)
        else:
            key, value = {"count": ("fishing_daily_count", 2), "day": ("fishing_daily_day", "later"),
                          "error": ("fishing_last_error", "newer error")}[change]
            state_module.get_identity_state(target_id)[key] = value
        if state_module.has_identity(target_id):
            changed_state.update(copy.deepcopy(state_module.get_identity_state(target_id)))
        return True

    h.audit.side_effect = notify
    with state_module.use_identity(h.identity_id):
        assert asyncio.run(REAL_DAILY_REPORT(h.now))
    if change == "removed":
        assert not state_module.has_identity(target_id)
    else:
        current = state_module.get_identity_state(target_id)
        assert current["fishing_daily_summary_day"] == changed_state["fishing_daily_summary_day"]
        assert current["fishing_last_error"] == changed_state["fishing_last_error"]
    unchanged_id = h.other_id if target == "origin" else h.identity_id
    assert state_module.get_identity_state(unchanged_id)["fishing_daily_summary_day"] == day


def test_daily_report_is_not_dispatched_twice_by_concurrent_identities(fishing_env, monkeypatch):
    h = fishing_env
    day = get_day_key(h.now)
    entries = [{"identity_id": h.identity_id, "day": day, "count": 1, "limit": 1,
                "summary_day": "", "reportable": True, "name": "fixture"}]
    monkeypatch.setattr(fishing, "_enabled_fishing_daily_entries", Mock(return_value=(day, entries, False)))

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def audit(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return True

        h.audit.side_effect = audit
        with state_module.use_identity(h.identity_id):
            first = asyncio.create_task(REAL_DAILY_REPORT(h.now))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            with state_module.use_identity(h.other_id):
                second = asyncio.create_task(REAL_DAILY_REPORT(h.now))
            await asyncio.sleep(0.01)
            count = h.audit.await_count
        finally:
            release.set()
            await first
            if second is not None:
                await second
        assert count == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["replaced", "rebound", "schedule"])
def test_failed_daily_notice_does_not_write_an_error_into_replacement_state(fishing_env, monkeypatch, change):
    h = fishing_env
    day = get_day_key(h.now)
    entries = [{"identity_id": h.identity_id, "day": day, "count": 1, "limit": 1,
                "summary_day": "", "reportable": True, "name": "fixture"}]
    monkeypatch.setattr(fishing, "_enabled_fishing_daily_entries", Mock(return_value=(day, entries, False)))
    changed = {}

    async def notify(*_args, **_kwargs):
        invalidate(h, change)
        changed.update(copy.deepcopy(state_module.get_identity_state(h.identity_id)))
        return False

    h.audit.side_effect = notify
    with state_module.use_identity(h.identity_id):
        assert asyncio.run(REAL_DAILY_REPORT(h.now))
    assert state_module.get_identity_state(h.identity_id) == changed


@pytest.mark.parametrize("caller", ["public", "message"])
def test_cancelled_partial_failure_still_adopts_confirmed_count(fishing_env, caller):
    h = fishing_env
    partial = {**h.result, "ok": False, "status": "failed", "error": "later step failed"}

    async def cancel(*_args, **_kwargs):
        raise MiniAppFlowCancelled(partial)

    h.flow.side_effect = cancel
    with pytest.raises(MiniAppFlowCancelled):
        asyncio.run(run_caller(h, caller))
    assert h.identity["fishing_daily_count"] == 1
    h.items.assert_called_once_with(h.identity_id, {"fixture-fish": 1}, persist=False)


def test_finish_submission_without_a_ready_result_does_not_count_as_settled(fishing_env):
    h = fishing_env
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result({"ok": True, "status": "finish_submitted", "data": {}}, h.now)
    assert h.identity["fishing_daily_count"] == 0
    h.items.assert_not_called()


@pytest.mark.parametrize("caller", ["public", "message"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_confirmed_facts_and_preserved_plan_survive_temporary_sqlite_reload(fishing_db, caller, cancelled):
    h = fishing_db

    async def flow(*_args, **_kwargs):
        invalidate(h, "schedule")
        if cancelled:
            raise MiniAppFlowCancelled(h.result)
        return h.result

    h.flow.side_effect = flow
    if cancelled:
        with pytest.raises(MiniAppFlowCancelled):
            asyncio.run(run_caller(h, caller))
    else:
        asyncio.run(run_caller(h, caller))
    state_module._meta_state["identity_states"] = {}
    state_module.set_storage_bag_records({})
    assert persistence.load_state()
    reloaded = state_module.get_identity_state(h.identity_id)
    assert reloaded["fishing_daily_count"] == 1
    assert reloaded["next_fishing_time"] == h.now + 8000
    assert reloaded["fishing_phase"] == "replacement"
    assert reloaded["fishing_reply_to_msg_id"] == 999
    assert state_module.get_identity_account(h.identity_id) == 7106
    assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fixture-fish"] == 1
    assert state_module.get_identity_state(h.other_id)["fishing_daily_count"] == 0
