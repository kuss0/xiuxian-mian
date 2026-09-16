import asyncio
import copy
import inspect
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_miniapp as dwelling
from model.features import cave_treasure_runtime as cave
from model.features import tianxing
from model.features import wild_training as wild
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppRequestPolicy, miniapp_retry_after_sec


IDENTITY_ID = 990430001
NOW = 1700000000.0
ENTRY_URL = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE43"
JOURNEY_FLOW = dwelling.run_cave_journey_action_production_flow
LOAD_SESSION = cave._load_cave_public_identity_session


def journey_payload(*, completed=False, player_id=IDENTITY_ID):
    payload = {
        "ok": True,
        "account": {
            "playerId": player_id,
            "journey": {
                "serverTime": int(NOW * 1000),
                "wildExperience": {
                    "available": not completed,
                    "dailyCount": 1 if completed else 0,
                    "dailyLimit": 2, "dailyRemaining": 1 if completed else 2,
                    "remainingSeconds": 43200 if completed else 0,
                    "readyAt": int((NOW + 43200) * 1000) if completed else 0,
                    "resetAt": int((NOW + 86400) * 1000),
                },
            },
        },
    }
    if completed:
        payload["actionResult"] = {
            "ok": True, "completed": True, "title": "fixture-wild-result",
            "rawMessage": "fixture-result", "cultivationDelta": 1200,
            "loot": [{"name": "fixture-loot", "quantity": 1}],
        }
    return payload


def flow_result(payload=None):
    return {
        "ok": True, "status": "acted", "action_dispatched": True,
        "data": journey_payload(completed=True) if payload is None else payload,
        "events": [{"step": "journey:wild_experience", "ok": True, "status_code": 200, "attempts": 1}],
    }


def adapter_with_limit(count=1):
    return replace(dwelling.build_cave_treasure_miniapp_adapter(), request_policy=MiniAppRequestPolicy(
        min_interval_sec=0, max_requests_per_run=count,
    ))


def run_flow(transport, **kwargs):
    return JOURNEY_FLOW(
        IDENTITY_ID, token="df_FIXTURE43", webview_url=ENTRY_URL, init_data="fixture-init",
        player_id=IDENTITY_ID, action="wild_experience", mode="cautious",
        transport=transport, adapter=adapter_with_limit(), **kwargs,
    )


@pytest.fixture
def wild_env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY_ID, 7431)
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(
        wild_training_enabled=True, wild_training_strategy="谨慎",
        next_wild_training_time=NOW, tianxing_enabled=False,
    )
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(wild, "_WILD_TRAINING_LOCKS", {})
    monkeypatch.setattr(wild, "_WILD_TRAINING_MINIAPP_TASKS", {})
    monkeypatch.setattr(wild, "_WILD_TRAINING_MINIAPP_RUN_LOCK", None)
    monkeypatch.setattr(wild, "_WILD_TRAINING_MINIAPP_LAST_RUN_AT", 0)
    monkeypatch.setattr(wild, "time", SimpleNamespace(time=lambda: NOW, monotonic=time.monotonic))
    monkeypatch.setattr(wild, "_wild_training_public_entry_urls", lambda: [ENTRY_URL])
    monkeypatch.setattr(cave, "_capture_store", Mock(return_value=None))
    monkeypatch.setattr(cave, "save_state", Mock())
    monkeypatch.setattr(wild, "save_state", Mock())
    audit = AsyncMock(return_value=True)
    monkeypatch.setattr(wild, "send_audit_log", audit)
    raw = journey_payload()
    session = {
        "ok": True, "player_id": IDENTITY_ID, "init_data": "fixture-init",
        "result": {"ok": True, "data": {"raw": raw, "overview": dwelling.parse_cave_dwelling_overview(raw)}},
    }
    loader = AsyncMock(return_value=session)
    flow = AsyncMock(return_value=flow_result())
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_journey_action_production_flow", flow)
    yield SimpleNamespace(identity=identity, session=session, loader=loader, flow=flow, audit=audit)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def invalidate(h, change):
    if change == "removed":
        state_module.remove_identity(IDENTITY_ID)
    elif change == "replaced":
        state_module.remove_identity(IDENTITY_ID)
        state_module.set_identity_account(IDENTITY_ID, 7431)
        state_module.get_identity_state(IDENTITY_ID)["wild_training_enabled"] = True
    elif change == "rebound":
        state_module.set_identity_account(IDENTITY_ID, 7432)
    elif change == "identity_disabled":
        state_module.set_identity_enabled(IDENTITY_ID, False)
    elif change == "paused":
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")
    elif change == "schedule":
        h.identity["next_wild_training_time"] = NOW + 60000
    elif change == "strategy":
        h.identity["wild_training_strategy"] = "均衡"
    elif change == "tianxing_config":
        h.identity["tianxing_auto_config"]["timeline_enabled"] = False
    else:
        h.identity[change] = not h.identity.get(change, False)


async def run_worker():
    with state_module.use_identity(IDENTITY_ID):
        return await wild._run_wild_training_miniapp_worker(IDENTITY_ID, [ENTRY_URL], NOW)


@pytest.mark.parametrize("change", [
    "removed", "replaced", "rebound", "identity_disabled", "paused", "schedule",
    "strategy", "wild_training_enabled", "tianxing_enabled", "tianxing_config",
])
def test_public_loader_invalidation_stops_before_action(wild_env, change):
    h = wild_env
    expected = None

    async def load(*_args, **_kwargs):
        nonlocal expected
        invalidate(h, change)
        expected = copy.deepcopy(state_module._meta_state)
        return h.session

    h.loader.side_effect = load
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    h.flow.assert_not_awaited()
    assert not result["ok"]
    assert state_module._meta_state == expected


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "schedule", "wild_training_enabled", "strategy"])
def test_worker_rechecks_after_tianxing_preparation(wild_env, monkeypatch, change):
    expected = None

    async def prepare(*_args, **_kwargs):
        nonlocal expected
        invalidate(wild_env, change)
        expected = copy.deepcopy(state_module._meta_state)
        return True

    monkeypatch.setattr(wild, "_prepare_wild_training_tianxing_route", prepare)
    public = AsyncMock(return_value={"ok": False, "message": "fixture"})
    monkeypatch.setattr(wild, "run_cave_public_wild_training", public)
    asyncio.run(run_worker())
    public.assert_not_awaited()
    assert state_module._meta_state == expected


@pytest.mark.parametrize("change", ["schedule", "wild_training_enabled", "rebound"])
def test_serial_gap_does_not_admit_invalidated_work(wild_env, monkeypatch, change):
    async def delay(_seconds):
        invalidate(wild_env, change)

    monkeypatch.setattr(wild, "_WILD_TRAINING_MINIAPP_LAST_RUN_AT", NOW)
    monkeypatch.setattr(wild.asyncio, "sleep", delay)
    public = AsyncMock(return_value={"ok": False})
    monkeypatch.setattr(wild, "run_cave_public_wild_training", public)
    asyncio.run(run_worker())
    public.assert_not_awaited()


@pytest.mark.parametrize("business", [None, {}, {"ok": "false"}, {"ok": False}, {"ok": True, "completed": False}])
def test_transport_success_is_not_a_completed_journey(wild_env, business):
    raw = journey_payload()
    raw["actionResult"] = business
    wild_env.flow.return_value = flow_result(raw)
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert not result["ok"]
    assert not result["extra"]["completed"]
    assert not state_module.get_inventory_delta_records()


def test_empty_journey_does_not_fabricate_a_zero_counter_panel():
    overview = dwelling.parse_cave_dwelling_overview({"ok": True, "account": {"playerId": IDENTITY_ID}})
    assert not overview["journey"].get("wild_experience")


def test_nested_result_retains_confirmed_partial_action_without_old_panel(wild_env):
    raw = {
        "ok": True, "account": {"playerId": IDENTITY_ID},
        "actionResult": {"ok": True, "completed": True, "cultivationDelta": 1200},
    }
    wild_env.flow.return_value = flow_result({"ok": True, "data": raw})
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert result["ok"]
    assert result["extra"]["completed"]
    assert not result["extra"]["wild"]
    assert result["extra"]["next_time"] >= NOW + 1800


def test_foreign_player_reply_cannot_publish_its_cooldown_or_inventory(wild_env):
    wild_env.flow.return_value = flow_result(journey_payload(completed=True, player_id=1234))
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert not result["ok"]
    assert not result["extra"].get("wild")
    assert result["extra"].get("next_time", 0) == 0
    assert not state_module.get_miniapp_state_records()
    assert not state_module.get_inventory_delta_records()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
def test_action_result_cannot_mutate_a_replacement_owner(wild_env, change):
    expected = None

    async def action(*_args, **_kwargs):
        nonlocal expected
        invalidate(wild_env, change)
        expected = copy.deepcopy(state_module._meta_state)
        return flow_result()

    wild_env.flow.side_effect = action
    asyncio.run(run_worker())
    assert state_module._meta_state == expected


@pytest.mark.parametrize("change", ["schedule", "wild_training_enabled"])
def test_confirmed_action_keeps_fact_without_overwriting_new_deadline(wild_env, change):
    async def action(*_args, **_kwargs):
        invalidate(wild_env, change)
        return flow_result()

    wild_env.flow.side_effect = action
    asyncio.run(run_worker())
    assert wild_env.identity["wild_training_last_completed_at"] == NOW
    expected = NOW + (60000 if change == "schedule" else 43200)
    assert wild_env.identity["next_wild_training_time"] == pytest.approx(expected, abs=0.1)
    assert any(row["items"].get("fixture-loot") == 1 for row in state_module.get_inventory_delta_records().values())


def test_notification_failure_does_not_turn_completion_into_gameplay_failure(wild_env):
    wild_env.audit.side_effect = RuntimeError("fixture-notification-unavailable")
    asyncio.run(run_worker())
    assert wild_env.identity["wild_training_retry_count"] == 0
    assert wild_env.identity["wild_training_last_completed_at"] == NOW
    assert wild_env.identity["next_wild_training_time"] == pytest.approx(NOW + 43200, abs=0.1)
    assert not wild_env.identity["wild_training_last_error"]


def test_worker_exception_does_not_overwrite_an_independent_new_schedule(wild_env, monkeypatch):
    async def public(*_args, **_kwargs):
        wild_env.identity["next_wild_training_time"] = NOW + 60000
        raise RuntimeError("fixture-late-error")

    monkeypatch.setattr(wild, "run_cave_public_wild_training", public)
    asyncio.run(run_worker())
    assert wild_env.identity["next_wild_training_time"] == NOW + 60000
    assert wild_env.identity["wild_training_retry_count"] == 0


def test_done_callback_does_not_remove_replacement_task(wild_env, monkeypatch):
    async def worker(*_args, **_kwargs):
        return None

    monkeypatch.setattr(wild, "_run_wild_training_miniapp_worker", worker)
    monkeypatch.setattr(wild, "track_background_task", lambda task: task)

    async def run():
        assert wild._launch_wild_training_miniapp_worker(IDENTITY_ID, [ENTRY_URL], NOW)
        old = wild._WILD_TRAINING_MINIAPP_TASKS[IDENTITY_ID]
        replacement = asyncio.get_running_loop().create_future()
        wild._WILD_TRAINING_MINIAPP_TASKS[IDENTITY_ID] = replacement
        try:
            await old
            await asyncio.sleep(0)
            assert wild._WILD_TRAINING_MINIAPP_TASKS.get(IDENTITY_ID) is replacement
        finally:
            replacement.cancel()

    asyncio.run(run())


def test_task_creation_failure_closes_worker_coroutine(wild_env, monkeypatch):
    async def worker(*_args, **_kwargs):
        return None

    coro = worker()
    monkeypatch.setattr(wild, "_run_wild_training_miniapp_worker", Mock(return_value=coro))
    monkeypatch.setattr(wild.asyncio, "create_task", Mock(side_effect=RuntimeError("fixture-no-task")))
    try:
        with pytest.raises(RuntimeError, match="fixture-no-task"):
            wild._launch_wild_training_miniapp_worker(IDENTITY_ID, [ENTRY_URL], NOW)
        assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED
    finally:
        coro.close()


def test_journey_flow_preserves_http_status_and_retry_after():
    response = SimpleNamespace(status_code=429, headers={"Retry-After": "50000"}, json=lambda: {"ok": False, "error": "rate_limit"})
    transport = Mock(return_value=response)
    result = asyncio.run(run_flow(transport))
    assert not result["ok"]
    assert miniapp_retry_after_sec(result) == 50000
    assert result["events"][-1]["status_code"] == 429
    assert transport.call_count == 1


def test_cancelled_worker_drains_http_and_persists_confirmed_result(wild_env, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def transport(request):
        calls.append(request["payload"]["action"])
        entered.set()
        assert release.wait(3)
        return journey_payload(completed=True)

    async def action(*_args, **kwargs):
        kwargs["transport"] = transport
        kwargs["adapter"] = adapter_with_limit()
        return await JOURNEY_FLOW(*_args, **kwargs)

    monkeypatch.setattr(cave, "run_cave_journey_action_production_flow", action)

    async def run():
        task = asyncio.create_task(run_worker())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done(), "HTTP thread escaped the wild-training worker"
            assert cave.is_cave_public_entry_busy(IDENTITY_ID)
            assert wild._wild_training_miniapp_worker_busy()
            release.set()
            with pytest.raises(MiniAppFlowCancelled):
                await task
            assert wild_env.identity["wild_training_last_completed_at"] == NOW
            assert wild_env.identity["next_wild_training_time"] == pytest.approx(NOW + 43200, abs=0.1)
            assert calls == ["wild_experience"]
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


def enable_tianxing(h, monkeypatch):
    h.identity.update(
        tianxing_enabled=True, wild_training_strategy="深入",
        tianxing_auto_config={"timeline_enabled": True},
        tianxing_observation={
            "current_prediction": "探索", "current_prediction_until": NOW + 3600,
            "current_prediction_set_at": NOW - 10, "last_observed_at": NOW - 10,
            "current_change": "探索", "current_change_until": NOW + 3600,
            "current_change_set_at": NOW - 10, "tianji_value": 9,
        },
    )
    monkeypatch.setattr(tianxing, "is_module_available", lambda _name: True)


@pytest.mark.parametrize("change", ["prediction_expired", "change_expired", "prediction_consumed", "paused"])
def test_tianxing_is_rechecked_after_public_entry_loading(wild_env, monkeypatch, change):
    h = wild_env
    enable_tianxing(h, monkeypatch)

    async def load(*_args, **_kwargs):
        observed = h.identity["tianxing_observation"]
        if change == "prediction_expired":
            observed["current_prediction_until"] = NOW - 1
        elif change == "change_expired":
            observed["current_change_until"] = NOW - 1
        elif change == "prediction_consumed":
            observed["prediction_consumed_at"] = NOW
            observed["prediction_consumed_route"] = "探索"
        else:
            observed["automation_paused_until"] = -1
        return h.session

    h.loader.side_effect = load
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "深入", now=NOW))
    h.flow.assert_not_awaited()
    assert not result["ok"]
    assert not result["extra"].get("acted")


@pytest.mark.parametrize("retreat_until", [0, NOW - 600, NOW + 10, NOW + 86400])
def test_deep_retreat_never_blocks_or_consumes_tianxing_route(wild_env, monkeypatch, retreat_until):
    h = wild_env
    enable_tianxing(h, monkeypatch)
    h.identity["next_deep_retreat_time"] = retreat_until
    h.identity["deep_retreat_phase"] = "waiting" if retreat_until else "idle"
    before = copy.deepcopy(h.identity["tianxing_observation"])
    with state_module.use_identity(IDENTITY_ID):
        assert wild.wild_training_http_route_is_ready("深入", NOW)
    assert h.identity["tianxing_observation"] == before


def test_tianji_shortage_allows_cautious_with_fresh_prediction_only(wild_env, monkeypatch):
    h = wild_env
    enable_tianxing(h, monkeypatch)
    h.identity["tianxing_observation"].update(current_change="", current_change_until=0, tianji_value=2)
    h.identity["tianxing_timeline_state"] = {"phase": "need_tianji_for_change"}
    with state_module.use_identity(IDENTITY_ID):
        assert wild._effective_wild_training_strategy(NOW) == "谨慎"
        assert wild.wild_training_http_route_is_ready("谨慎", NOW)
        assert not wild.wild_training_http_route_is_ready("深入", NOW)
        h.identity["tianxing_observation"]["prediction_consumed_at"] = NOW
        h.identity["tianxing_observation"]["prediction_consumed_route"] = "探索"
        assert not wild.wild_training_http_route_is_ready("谨慎", NOW)


def test_late_result_does_not_consume_a_new_tianxing_prediction(wild_env, monkeypatch):
    h = wild_env
    enable_tianxing(h, monkeypatch)
    expected = None

    async def action(*_args, **_kwargs):
        nonlocal expected
        h.identity["tianxing_observation"].update(
            current_prediction="斗法", current_prediction_until=NOW + 6000,
            current_prediction_set_at=NOW + 1,
        )
        expected = copy.deepcopy(h.identity["tianxing_observation"])
        result = flow_result()
        result["data"]["actionResult"]["rawMessage"] = "【野外历练 · 改命脱险】\n【推命命中】探索\n【改命回天】"
        return result

    h.flow.side_effect = action
    consume = Mock(return_value=True)
    unknown = Mock()
    monkeypatch.setattr(wild, "apply_tianxing_passive", consume)
    monkeypatch.setattr(wild, "mark_tianxing_route_result_unknown", unknown)
    asyncio.run(run_worker())
    consume.assert_not_called()
    unknown.assert_not_called()
    assert h.identity["tianxing_observation"] == expected
    assert h.identity["wild_training_last_completed_at"] == NOW


@pytest.mark.parametrize("stage", ["calibration", "timeline", "craft"])
@pytest.mark.parametrize("change", ["removed", "replaced", "schedule", "wild_training_enabled"])
def test_preparation_awaits_cannot_rewrite_invalidated_wild_state(wild_env, monkeypatch, stage, change):
    h = wild_env
    enable_tianxing(h, monkeypatch)
    expected = None

    async def invalidate_and_reply(*_args, **_kwargs):
        nonlocal expected
        invalidate(h, change)
        expected = copy.deepcopy(state_module._meta_state)
        return {"active": True, "phase": "waiting", "stage": "waiting"}

    preflight = {"route_allowed": False, "stage": "prediction_conflict" if stage == "craft" else "timeline_waiting", "timeline_required": True}
    monkeypatch.setattr(wild, "build_tianxing_route_preflight_plan", Mock(return_value=preflight))
    monkeypatch.setattr(wild, "build_tianxing_consume_window", Mock(return_value=[{"route": "探索"}]))
    monkeypatch.setattr(wild, "send_game_command", invalidate_and_reply)
    monkeypatch.setattr(wild, "run_tianxing_timeline_scheduler", invalidate_and_reply)
    monkeypatch.setattr(wild, "run_tianxing_consume_craft_prediction", invalidate_and_reply)

    async def run():
        with state_module.use_identity(IDENTITY_ID):
            if stage == "calibration":
                return await wild._send_tianxing_panel_calibration(NOW, "fixture-calibration")
            return await wild._prepare_wild_training_tianxing_route(NOW, due_at=NOW)

    assert not asyncio.run(run())
    assert state_module._meta_state == expected


def test_cancelled_loader_result_is_not_a_wild_cooldown(wild_env):
    wild_env.loader.side_effect = MiniAppFlowCancelled({"ok": True, "data": {"raw": journey_payload()}})
    with pytest.raises(MiniAppFlowCancelled):
        asyncio.run(run_worker())
    wild_env.flow.assert_not_awaited()
    assert wild_env.identity["wild_training_last_result"] != "MiniApp 冷却状态已同步"
    assert wild_env.identity["wild_training_last_completed_at"] == 0
    assert not state_module.get_miniapp_state_records()


def test_returned_cooldown_uses_completion_time_not_loader_start(wild_env, monkeypatch):
    clock = [20.0]
    monkeypatch.setattr(cave, "time", SimpleNamespace(time=lambda: NOW, monotonic=lambda: clock[0]))

    async def action(*_args, **_kwargs):
        clock[0] += 90
        return flow_result()

    wild_env.flow.side_effect = action
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert result["extra"]["next_time"] == NOW + 90 + 43200


def test_worker_honors_server_retry_after_on_rejected_action(wild_env, monkeypatch):
    enable_tianxing(wild_env, monkeypatch)
    wild_env.flow.return_value = {
        "ok": False, "status": "failed", "error": "external_action_rate_limited", "action_dispatched": True,
        "data": {"ok": False, "error": "external_action_rate_limited"},
        "events": [{"step": "journey:wild_experience", "status_code": 429, "attempts": 1, "retry_after_sec": 50000}],
    }
    unknown = Mock()
    monkeypatch.setattr(wild, "mark_tianxing_route_result_unknown", unknown)
    asyncio.run(run_worker())
    assert wild_env.identity["next_wild_training_time"] == NOW + 50000
    assert wild_env.identity["wild_training_retry_count"] == 1
    unknown.assert_not_called()


@pytest.mark.parametrize("result", [
    {"ok": False, "message": "timeout", "extra": {"phase": "session_failed"}},
    {"ok": False, "message": "入口状态缺失", "extra": {"phase": "state_missing"}},
    {"ok": False, "message": "dwelling_token_expired", "extra": {"phase": "session_failed", "acted": True}},
    {"ok": False, "message": "dwelling_token_expired", "extra": {"phase": "session_failed", "retry_after_sec": 50000}},
])
def test_entry_fallback_never_replays_unknown_or_rate_limited_requests(result):
    assert not wild._wild_training_entry_failure_can_fallback(result)


def test_explicit_expired_entry_is_eligible_for_bounded_fallback():
    assert wild._wild_training_entry_failure_can_fallback({
        "ok": False, "message": "dwelling_token_expired", "extra": {"phase": "session_failed", "acted": False},
    })


def test_invalidated_journey_flow_does_not_send_http():
    transport = Mock(return_value=journey_payload(completed=True))
    result = asyncio.run(run_flow(transport, operation_check=lambda: False))
    assert result["status"] == "cancelled"
    assert not result.get("action_dispatched")
    transport.assert_not_called()


def test_incomplete_counters_never_authorize_an_action(wild_env):
    raw = wild_env.session["result"]["data"]["raw"]
    raw["account"]["journey"]["wildExperience"].pop("dailyLimit")
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert not result["ok"]
    wild_env.flow.assert_not_awaited()


def test_newer_public_record_is_not_replaced_by_earlier_action(wild_env):
    record = {"state": {"phase": "newer-operation"}, "updated_at": NOW + 60}

    async def action(*_args, **_kwargs):
        state_module.set_miniapp_state_records({f"{IDENTITY_ID}:wild_training": record})
        return flow_result()

    wild_env.flow.side_effect = action
    asyncio.run(run_worker())
    assert state_module.get_miniapp_state_records()[f"{IDENTITY_ID}:wild_training"] == record
    assert wild_env.identity["next_wild_training_time"] == NOW


def test_scheduler_lock_wait_keeps_the_original_identity_owner(wild_env, monkeypatch):
    scheduler = AsyncMock()
    monkeypatch.setattr(wild, "_run_wild_training_miniapp_scheduler_unlocked", scheduler)

    async def run():
        lock = asyncio.Lock()
        wild._WILD_TRAINING_LOCKS[IDENTITY_ID] = lock
        await lock.acquire()
        with state_module.use_identity(IDENTITY_ID):
            task = asyncio.create_task(wild.run_wild_training_scheduler(NOW))
        await asyncio.sleep(0)
        invalidate(wild_env, "replaced")
        lock.release()
        await task

    asyncio.run(run())
    scheduler.assert_not_awaited()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "schedule", "wild_training_enabled"])
def test_queued_worker_keeps_enqueue_time_ownership(wild_env, change):
    async def run():
        with state_module.use_identity(IDENTITY_ID):
            assert wild._launch_wild_training_miniapp_worker(IDENTITY_ID, [ENTRY_URL], NOW)
        task = wild._WILD_TRAINING_MINIAPP_TASKS[IDENTITY_ID]
        invalidate(wild_env, change)
        expected = copy.deepcopy(state_module._meta_state)
        await task
        assert state_module._meta_state == expected

    asyncio.run(run())
    wild_env.loader.assert_not_awaited()


def test_refreshed_entry_configuration_cancels_the_old_worker_before_action(wild_env, monkeypatch):
    async def load(*_args, **_kwargs):
        monkeypatch.setattr(wild, "_wild_training_public_entry_urls", lambda: [ENTRY_URL + "NEW"])
        return wild_env.session

    wild_env.loader.side_effect = load
    asyncio.run(run_worker())
    wild_env.flow.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("loot", 7), ("loot", "unparsed-loot"), ("modes", 7)])
def test_malformed_optional_fields_do_not_erase_confirmed_completion(wild_env, field, value):
    raw = journey_payload(completed=True)
    if field == "modes":
        raw["account"]["journey"]["wildExperience"][field] = value
    else:
        raw["actionResult"][field] = value
    wild_env.flow.return_value = flow_result(raw)
    asyncio.run(run_worker())
    assert wild_env.identity["wild_training_last_completed_at"] == NOW
    assert wild_env.identity["wild_training_retry_count"] == 0


@pytest.mark.parametrize("bad_id", [0, None, "not-a-player"])
def test_explicit_unparseable_player_id_does_not_confirm_an_action(wild_env, bad_id):
    wild_env.flow.return_value = flow_result(journey_payload(completed=True, player_id=bad_id))
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert not result["ok"]
    assert not state_module.get_inventory_delta_records()


def test_two_confirmed_actions_with_identical_rewards_remain_distinct(wild_env):
    asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW + 86400))
    rows = [row for row in state_module.get_inventory_delta_records().values() if row.get("source") == "wild_training_miniapp"]
    assert len(rows) == 2


@pytest.mark.parametrize("value", [float("inf"), float("nan"), "bad"])
def test_nonfinite_scheduler_timers_do_not_permanently_stall(wild_env, monkeypatch, value):
    wild_env.identity["next_wild_training_time"] = value
    monkeypatch.setattr(wild.random, "uniform", lambda _a, _b: 600)

    async def run():
        with state_module.use_identity(IDENTITY_ID):
            await wild.run_wild_training_scheduler(NOW)

    asyncio.run(run())
    assert wild_env.identity["next_wild_training_time"] == NOW + 600
    wild_env.flow.assert_not_awaited()


@pytest.mark.parametrize("player_id", [None, True, False, "", 0, "bad", -1001, float(IDENTITY_ID), IDENTITY_ID + 1, {}])
def test_journey_selection_is_validated_before_auth_or_http(monkeypatch, player_id):
    auth = AsyncMock(return_value="fixture-init")
    monkeypatch.setattr(dwelling, "request_cave_treasure_miniapp_init_data", auth)
    transport = Mock(return_value=journey_payload(completed=True))
    result = asyncio.run(JOURNEY_FLOW(
        IDENTITY_ID, token="df_FIXTURE43", webview_url=ENTRY_URL, player_id=player_id,
        action="wild_experience", mode="cautious", transport=transport,
    ))
    assert not result["ok"]
    assert result["error"].startswith("cave_action_player_")
    auth.assert_not_awaited()
    transport.assert_not_called()


@pytest.mark.parametrize("action,mode", [("wild_experience", "cautious"), ("set_encounter_mode", "off")])
def test_journey_builders_require_selected_player(action, mode):
    with pytest.raises(TypeError):
        dwelling.build_cave_journey_action_request(action, mode=mode, token="df_FIXTURE43")
    for invalid in (None, True, 0, -1, float(IDENTITY_ID)):
        with pytest.raises(ValueError):
            dwelling.build_cave_journey_action_request(action, mode=mode, token="df_FIXTURE43", player_id=invalid)
    selected = -1_000_000_000_000 - IDENTITY_ID
    request = dwelling.build_cave_journey_action_request(action, mode=mode, token="df_FIXTURE43", player_id=selected)
    assert request["payload"]["playerId"] == selected


def unowned_journey_payload(variant, *, completed=True):
    raw = journey_payload(completed=completed)
    if variant in {"missing", "selector_only"}:
        raw["account"].pop("playerId")
    elif variant == "contradiction":
        raw["identity"] = {"selectedPlayerId": IDENTITY_ID + 1}
    else:
        raw["account"]["playerId"] = {"other": 7431, "bool": True, "float": float(IDENTITY_ID), "null": None}[variant]
    if variant == "selector_only":
        raw["identity"] = {"selectedPlayerId": IDENTITY_ID}
    return raw


@pytest.mark.parametrize("variant", ["missing", "selector_only", "contradiction", "other", "bool", "float", "null"])
def test_journey_adapter_does_not_export_an_unowned_success(variant):
    transport = Mock(return_value={"ok": True, "data": unowned_journey_payload(variant)})
    result = asyncio.run(run_flow(transport))
    assert not result["ok"]
    assert result["status"] == "identity_unverified"
    assert result["error"].startswith("cave_action_player_")
    assert result["action_dispatched"]
    assert not result["data"]
    transport.assert_called_once()


@pytest.mark.parametrize("variant", ["missing", "selector_only", "contradiction", "other", "bool", "float", "null"])
def test_public_journey_rejects_unowned_snapshot_before_any_dispatch_or_cooldown(wild_env, variant):
    data = wild_env.session["result"]["data"]
    data["raw"] = unowned_journey_payload(variant, completed=False)
    before = copy.deepcopy(state_module._meta_state)
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert not result["ok"]
    assert not result["extra"].get("acted")
    assert "cave_action_player_" in result["message"]
    wild_env.flow.assert_not_awaited()
    assert state_module._meta_state == before


@pytest.mark.parametrize("variant", ["missing", "selector_only", "contradiction", "bool", "float"])
def test_public_journey_reducer_independently_rejects_unowned_receipts(wild_env, variant):
    wild_env.flow.return_value = flow_result(unowned_journey_payload(variant))
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert not result["ok"]
    assert result["extra"]["phase"] == "action_unknown"
    assert result["extra"]["acted"]
    assert result["extra"]["outcome_unknown"]
    assert not result["extra"].get("action_result")
    assert not result["extra"].get("wild")
    assert not result["extra"].get("next_time")
    assert not state_module.get_inventory_delta_records()
    assert not state_module.get_miniapp_state_records()


@pytest.mark.parametrize("completed", [0, "false", None])
def test_journey_completion_flag_must_be_boolean_when_present(wild_env, completed):
    raw = journey_payload(completed=True)
    raw["actionResult"]["completed"] = completed
    wild_env.flow.return_value = flow_result(raw)
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert not result["ok"]
    assert result["extra"]["outcome_unknown"]
    assert not state_module.get_inventory_delta_records()


def test_unowned_journey_result_does_not_consume_tianxing_or_create_rewards(wild_env, monkeypatch):
    enable_tianxing(wild_env, monkeypatch)
    raw = unowned_journey_payload("selector_only")
    raw["actionResult"]["rawMessage"] = "\u3010\u63a8\u547d\u547d\u4e2d\u3011\u63a2\u7d22\n\u3010\u6539\u547d\u56de\u5929\u3011"
    wild_env.flow.return_value = flow_result(raw)
    consume = Mock(return_value=True)
    unknown = Mock()
    monkeypatch.setattr(wild, "apply_tianxing_passive", consume)
    monkeypatch.setattr(wild, "mark_tianxing_route_result_unknown", unknown)
    asyncio.run(run_worker())
    consume.assert_not_called()
    unknown.assert_called_once()
    assert wild_env.identity["wild_training_last_completed_at"] == 0
    assert not state_module.get_inventory_delta_records()


def test_journey_uses_verified_raw_panel_instead_of_a_detached_cached_overview(wild_env):
    wild_env.session["result"]["data"]["raw"] = journey_payload(completed=True)
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert result["ok"]
    assert result["extra"]["phase"] == "cooldown"
    assert result["extra"]["next_time"] == pytest.approx(NOW + 43200, abs=0.1)
    wild_env.flow.assert_not_awaited()


@pytest.mark.parametrize("strategy,mode", [("谨慎", "cautious"), ("均衡", "balanced"), ("深入", "deep")])
@pytest.mark.parametrize("signed_reply", [True, False])
def test_real_journey_selection_only_changes_the_channel(wild_env, monkeypatch, strategy, mode, signed_reply):
    selected = -1_000_000_000_000 - IDENTITY_ID
    state_module.set_identity_account(7431, 7431)
    primary_before = copy.deepcopy(state_module.get_identity_state(7431))
    wild_env.identity["wild_training_strategy"] = strategy
    state_module.set_identity_enabled(IDENTITY_ID, False)
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [IDENTITY_ID]})
    calls = []

    def transport(request):
        payload = request["payload"]
        player = payload.get("playerId", 7431)
        action = payload.get("action", "start")
        calls.append((action, player, payload.get("mode")))
        reply_player = player if signed_reply else dwelling._normalize_cave_inventory_player_id(player)
        raw = journey_payload(completed=action == "wild_experience", player_id=reply_player)
        raw["identity"] = {"selectedPlayerId": player, "choices": [{"playerId": 7431}, {"playerId": selected}]}
        return raw

    monkeypatch.setattr(cave, "_load_cave_public_identity_session", LOAD_SESSION)
    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", AsyncMock(return_value="fixture-init"))
    monkeypatch.setattr(cave, "run_cave_journey_action_production_flow", JOURNEY_FLOW)
    monkeypatch.setattr(dwelling, "_flow_transport", lambda *_args, **_kwargs: transport)
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, strategy, now=NOW))
    assert result["ok"], result
    assert calls == [("start", 7431, None), ("start", selected, None), ("wild_experience", selected, mode)]
    assert state_module.get_identity_state(7431) == primary_before
    assert result["extra"]["completed"]
    assert result["extra"]["next_time"] == pytest.approx(NOW + 43200, abs=0.1)
    rows = [row for key, row in state_module.get_inventory_delta_records().items() if key != "_meta"]
    assert len(rows) == 1
    assert rows[0]["identity_id"] == IDENTITY_ID
    assert rows[0]["items"] == {"fixture-loot": 1}


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429, 500, 503])
def test_unowned_http_failure_keeps_transport_classification_without_business_data(wild_env, monkeypatch, status):
    raw = unowned_journey_payload("other")
    raw.update(ok=False, error="fixture-rejected-or-unavailable")
    headers = {"Retry-After": "50000"} if status == 429 or status >= 500 else {}
    response = SimpleNamespace(status_code=status, headers=headers, json=lambda: raw)
    transport = Mock(return_value=response)

    async def action(*args, **kwargs):
        return await JOURNEY_FLOW(*args, **kwargs, transport=transport, adapter=adapter_with_limit())

    monkeypatch.setattr(cave, "run_cave_journey_action_production_flow", action)
    result = asyncio.run(cave.run_cave_public_wild_training(IDENTITY_ID, ENTRY_URL, "谨慎", now=NOW))
    assert not result["ok"]
    assert result["extra"]["phase"] == ("action_unknown" if status >= 500 else "blocked")
    assert result["extra"]["outcome_unknown"] is (status >= 500)
    assert not result["extra"].get("action_result")
    assert not result["extra"].get("wild")
    assert not state_module.get_inventory_delta_records()
    assert not state_module.get_miniapp_state_records()
    assert miniapp_retry_after_sec(result) == (50000 if headers else 0)
    transport.assert_called_once()
