import asyncio
import copy
import threading
from dataclasses import replace
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_miniapp as dwelling
from model.features import cave_treasure_runtime as cave
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppRequestAborted, MiniAppRequestPolicy, miniapp_retry_after_sec


IDENTITY_ID = 990420001
ENTRY_URL = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE42"
NOW = 1700000000.0
LOAD_SESSION = cave._load_cave_public_identity_session


def world_payload(*, prayer=False, collected=False):
    return {
        "ok": True,
        "account": {
            "playerId": IDENTITY_ID,
            "smallWorld": {
                "hasWorld": True,
                "summary": {
                    "population": 900, "populationCap": 1000,
                    "faith": 90, "stability": 95, "incensePoints": 1800 if collected else 1000,
                    "uncollectedIncense": 0 if collected else 800, "hourlyIncense": 100,
                },
                "prayer": {"title": "fixture-prayer", "cost": []} if prayer else None,
                "actions": {
                    "canCollect": not collected, "canManifest": prayer,
                    "prayerRemainingSeconds": 0 if prayer else 21600,
                },
            },
        },
    }


def flow_result(action="collect", *, ok=True):
    raw = world_payload(collected=ok)
    return {
        "ok": ok, "status": "acted" if ok else "action_failed",
        "error": "" if ok else "fixture-business-failure",
        "events": [],
        "data": {
            "overview": dwelling.parse_cave_dwelling_overview(raw),
            "before_overview": dwelling.parse_cave_dwelling_overview(world_payload()),
            "action": action, "action_confirmed": ok, "action_dispatched": True, "snapshot_current": True,
            "action_result": {"ok": ok, "message": "fixture-action-result"},
            "plan": {"action": action, "harvest_due": action == "collect"}, "raw": raw,
        },
    }


def adapter_with_limit(count=8):
    return replace(dwelling.build_cave_treasure_miniapp_adapter(), request_policy=MiniAppRequestPolicy(
        min_interval_sec=0, max_requests_per_run=count,
    ))


def run_flow(transport, **kwargs):
    return dwelling.run_cave_small_world_production_flow(
        IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL, init_data="fixture-init",
        player_id=IDENTITY_ID, action_planner=lambda _overview: {"action": "collect"},
        transport=transport, adapter=adapter_with_limit(), **kwargs,
    )


@pytest.fixture
def world_env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY_ID, 7401)
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(
        small_world_enabled=True, small_world_harvest_enabled=True,
        next_small_world_time=NOW - 1, small_world_next_public_harvest_at=NOW - 1,
        small_world_faith_value=70, small_world_incense_stock=900,
        small_world_pending_incense=700, small_world_last_panel_at=NOW - 3600,
        small_world_panel_snapshot={"faith": 70, "stock": 900, "updated_at": NOW - 3600},
    )
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(cave, "_capture_store", Mock(return_value=None))
    monkeypatch.setattr(cave, "save_state", Mock())
    audit = AsyncMock(return_value=True)
    monkeypatch.setattr(cave, "send_audit_log", audit)
    session = {
        "ok": True, "init_data": "fixture-init", "player_id": IDENTITY_ID,
        "result": {"ok": True, "data": {"raw": world_payload()}},
    }
    loader = AsyncMock(return_value=session)
    flow = AsyncMock(return_value=flow_result())
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_small_world_production_flow", flow)
    yield SimpleNamespace(identity=identity, loader=loader, flow=flow, audit=audit, session=session)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def invalidate(h, change):
    if change == "removed":
        state_module.remove_identity(IDENTITY_ID)
    elif change == "replaced":
        state_module.remove_identity(IDENTITY_ID)
        state_module.set_identity_account(IDENTITY_ID, 7401)
    elif change == "rebound":
        state_module.set_identity_account(IDENTITY_ID, 7402)
    elif change == "identity_disabled":
        state_module.set_identity_enabled(IDENTITY_ID, False)
    elif change == "paused":
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")
    elif change == "schedule":
        h.identity["next_small_world_time"] = NOW + 50000
    elif change == "panel":
        h.identity["small_world_panel_snapshot"]["faith"] = 99
    elif change == "phase":
        h.identity["small_world_phase"] = "manifest_sent"
    else:
        h.identity[change] = not h.identity.get(change, False)


@pytest.mark.parametrize("business", [
    {"ok": False, "error": "resource_shortage"},
    {"ok": True, "completed": False},
    {"ok": True, "completed": 0},
    {"ok": True, "completed": "false"},
    {"ok": True, "completed": None},
    {"ok": "false"},
    {"message": "unconfirmed"},
    None,
])
def test_http_success_does_not_confirm_rejected_or_missing_business_result(business):
    raw = world_payload()
    raw["actionResult"] = business
    transport = Mock(return_value=raw)
    result = asyncio.run(run_flow(transport, initial_snapshot=world_payload()))
    assert not result["ok"]
    assert not result["data"].get("action_confirmed")
    assert transport.call_count == 1


def test_one_budget_covers_start_and_action():
    transport = Mock(side_effect=[world_payload(), {"ok": True, "actionResult": {"ok": True}}])
    result = asyncio.run(dwelling.run_cave_small_world_production_flow(
        IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL, init_data="fixture-init",
        player_id=IDENTITY_ID, action_planner=lambda _overview: {"action": "collect"},
        transport=transport, adapter=adapter_with_limit(1),
    ))
    assert not result["ok"]
    assert result["error"] == "request_budget_exhausted"
    assert transport.call_count == 1


def test_flow_retains_server_retry_after_and_http_failure_evidence():
    response = SimpleNamespace(
        status_code=429, headers={"Retry-After": "50000"},
        json=lambda: {"ok": False, "error": "external_action_rate_limited"},
    )
    transport = Mock(return_value=response)
    result = asyncio.run(run_flow(transport, initial_snapshot=world_payload()))
    assert not result["ok"]
    assert miniapp_retry_after_sec(result) == 50000
    assert result["events"][-1]["status_code"] == 429
    assert transport.call_count == 1


def test_partial_success_keeps_receipt_but_does_not_relabel_old_panel_as_fresh():
    transport = Mock(return_value={
        "ok": True, "account": {"playerId": IDENTITY_ID},
        "actionResult": {"ok": True, "message": "confirmed"},
    })
    result = asyncio.run(run_flow(transport, initial_snapshot=world_payload(prayer=True)))
    assert result["ok"]
    assert result["data"].get("action_confirmed") is True
    assert result["data"].get("snapshot_current") is False
    assert result["data"]["before_overview"]["small_world"]["has_prayer"]
    assert transport.call_count == 1


def test_missing_world_panel_is_not_fabricated_as_zero_resources():
    assert dwelling.parse_cave_dwelling_overview({"ok": True})["small_world"] == {}


@pytest.mark.parametrize("step", ["start", "collect"])
def test_cancelled_flow_drains_the_thread_before_releasing_its_caller(step):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def transport(request):
        action = request["payload"].get("action") or "start"
        calls.append(action)
        if action == step:
            entered.set()
            assert release.wait(3)
        response = world_payload(collected=action == "collect")
        if action == "collect":
            response["actionResult"] = {"ok": True}
        return response

    async def run():
        task = asyncio.create_task(run_flow(transport))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done(), "HTTP worker outlived its operation lock"
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as raised:
                await task
            assert calls == (["start"] if step == "start" else ["start", "collect"])
            if step == "collect":
                assert raised.value.result["data"]["action_confirmed"]
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


@pytest.mark.parametrize("change", [
    "removed", "replaced", "rebound", "identity_disabled", "paused", "schedule", "panel", "phase",
    "small_world_enabled", "small_world_harvest_enabled", "small_world_manifest_enabled",
    "small_world_preach_enabled", "small_world_refine_enabled", "small_world_refresh_enabled",
    "small_world_high_stock_silence_enabled",
])
def test_caller_revalidates_after_loading_before_planning_or_spending(world_env, change):
    h = world_env

    async def loader(*_args, **_kwargs):
        invalidate(h, change)
        return h.session

    h.loader.side_effect = loader
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert not result["ok"]
    assert result["extra"].get("status") == "cancelled"
    h.flow.assert_not_awaited()
    h.audit.assert_not_awaited()
    if change == "removed":
        assert not state_module.has_identity(IDENTITY_ID)


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
def test_received_result_never_updates_a_replacement_owner(world_env, change):
    h = world_env

    async def action(*_args, **_kwargs):
        invalidate(h, change)
        return h.flow.return_value

    h.flow.side_effect = action
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert not result["ok"]
    assert result["extra"].get("status") == "cancelled"
    assert state_module.get_miniapp_state_records() == {}
    h.audit.assert_not_awaited()


@pytest.mark.parametrize("ok", [False, True])
def test_old_completion_does_not_overwrite_new_clocks_panel_or_error(world_env, ok):
    h = world_env

    async def action(*_args, **_kwargs):
        h.identity.update(
            next_small_world_time=NOW + 70000, small_world_next_public_harvest_at=NOW + 60000,
            small_world_last_public_harvest_at=NOW + 5, small_world_last_panel_at=NOW + 5,
            small_world_panel_snapshot={"faith": 99, "stock": 50000, "updated_at": NOW + 5},
            small_world_faith_value=99, small_world_incense_stock=50000,
            small_world_phase="manifest_sent", small_world_last_error="newer-state",
        )
        return flow_result(ok=ok)

    h.flow.side_effect = action
    asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert h.identity["next_small_world_time"] == NOW + 70000
    assert h.identity["small_world_next_public_harvest_at"] == NOW + 60000
    assert h.identity["small_world_last_public_harvest_at"] == NOW + 5
    assert h.identity["small_world_panel_snapshot"]["faith"] == 99
    assert h.identity["small_world_faith_value"] == 99
    assert h.identity["small_world_incense_stock"] == 50000
    assert h.identity["small_world_last_error"] == "newer-state"
    assert h.identity["small_world_phase"] == "manifest_sent"


def test_local_busy_does_not_rewrite_the_running_operations_schedule(world_env):
    h = world_env
    h.identity["small_world_last_public_request_at"] = NOW
    before = copy.deepcopy(h.identity)

    async def run():
        async with cave._public_entry_lock(IDENTITY_ID):
            result = await cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW)
            assert result["extra"].get("status") == "busy"

    asyncio.run(run())
    assert h.identity == before
    h.loader.assert_not_awaited()


def test_partial_manifest_success_does_not_use_stale_prayer_for_immediate_retry(world_env):
    h = world_env
    result = flow_result("manifest")
    result["data"]["snapshot_current"] = False
    result["data"]["overview"] = dwelling.parse_cave_dwelling_overview(world_payload(prayer=True))
    result["data"]["action_result"]["rawMessage"] = "\u663e\u7075\u6210\u529f\uff01\u4e0b\u4e00\u6b21\u51e1\u4eba\u7948\u613f\u611f\u5e94\u9700\u7b49\u5f85 360 \u5206\u949f\u3002"
    h.flow.return_value = result
    h.identity["small_world_harvest_enabled"] = False
    before = copy.deepcopy(h.identity["small_world_panel_snapshot"])
    response = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert response["ok"]
    assert h.identity["next_small_world_time"] >= NOW + 21600
    assert h.identity["small_world_panel_snapshot"] == {**before, "updated_at": 0}


def test_runtime_preserves_retry_after_and_does_not_mark_rejected_harvest_completed(world_env):
    h = world_env
    h.flow.return_value = flow_result(ok=False)
    h.flow.return_value["events"] = [{"ok": False, "status_code": 429, "retry_after_sec": 50000}]
    response = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert not response["ok"]
    assert response["extra"].get("retry_after_sec") == 50000
    assert h.identity["small_world_last_public_harvest_at"] == 0
    assert h.identity["small_world_next_public_harvest_at"] >= NOW + 50000
    assert h.identity["next_small_world_time"] >= NOW + 50000


def test_notification_error_does_not_discard_confirmed_harvest(world_env):
    h = world_env
    h.audit.side_effect = RuntimeError("fixture-notification-failure")
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert result["ok"]
    assert h.identity["small_world_last_public_harvest_at"] == NOW
    assert h.identity["small_world_next_public_harvest_at"] == NOW + 8 * 3600


def test_cancelled_action_retains_confirmed_harvest_and_carries_result(world_env):
    h = world_env
    h.flow.side_effect = MiniAppFlowCancelled(flow_result())
    with pytest.raises(MiniAppFlowCancelled) as raised:
        asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert h.identity["small_world_last_public_harvest_at"] == NOW
    assert h.identity["small_world_next_public_harvest_at"] == NOW + 8 * 3600
    assert raised.value.result["ok"]
    h.audit.assert_not_awaited()


@pytest.mark.parametrize("stage", ["start", "action"])
def test_flow_rejects_explicit_other_player_without_importing_their_resources(stage):
    raw = world_payload()
    raw["account"]["playerId"] = IDENTITY_ID + 1
    raw["actionResult"] = {"ok": True}
    transport = Mock(return_value=raw)
    result = asyncio.run(run_flow(transport, initial_snapshot=world_payload() if stage == "action" else None))
    assert not result["ok"]
    assert result["error"] == "cave_action_player_mismatch"
    assert result["data"]["action_confirmed"] is False
    assert result["data"]["snapshot_current"] is False
    assert transport.call_count == 1


def test_flow_preserves_confirmed_action_when_post_action_panel_is_malformed():
    raw = world_payload(collected=True)
    raw["account"]["smallWorld"]["barrier"] = "malformed-barrier"
    raw["actionResult"] = {"ok": True, "message": "confirmed-collect"}
    transport = Mock(return_value=raw)
    result = asyncio.run(run_flow(transport, initial_snapshot=world_payload()))
    assert result["data"]["action_confirmed"]
    assert not result["data"]["snapshot_current"]
    assert result["data"]["action_result"]["message"] == "confirmed-collect"
    assert transport.call_count == 1


@pytest.mark.parametrize("step", ["slot", "entity", "input", "webview"])
def test_init_data_guard_checks_each_telegram_await(monkeypatch, step):
    allowed = True
    calls = []

    def returned(stage, value):
        nonlocal allowed
        calls.append(stage)
        if step == stage:
            allowed = False
        return value

    class Client:
        async def get_entity(self, _bot):
            return returned("entity", "fixture-bot")

        async def get_input_entity(self, _bot):
            return returned("input", "fixture-input")

        async def __call__(self, _request):
            return returned("webview", SimpleNamespace(url="https://asc.aiopenai.app/?tgWebAppData=fixture"))

    @asynccontextmanager
    async def slot(**_kwargs):
        returned("slot", None)
        yield

    monkeypatch.setattr(dwelling, "_get_identity_client_with_account", lambda _identity: (7401, Client()))
    monkeypatch.setattr(dwelling, "account_rpc_slot", slot)
    monkeypatch.setattr(dwelling, "_recent_game_bot_usernames", lambda **_kwargs: [])
    with pytest.raises(MiniAppRequestAborted):
        asyncio.run(dwelling.request_cave_treasure_miniapp_init_data(
            IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL, operation_check=lambda: allowed,
        ))
    assert calls == ["slot", "entity", "input", "webview"][:["slot", "entity", "input", "webview"].index(step) + 1]


def test_flow_stops_when_planner_invalidates_admission():
    allowed = True

    def planner(_overview):
        nonlocal allowed
        allowed = False
        return {"action": "collect"}

    transport = Mock()
    result = asyncio.run(dwelling.run_cave_small_world_production_flow(
        IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL, init_data="fixture-init",
        player_id=IDENTITY_ID, initial_snapshot=world_payload(), action_planner=planner, transport=transport,
        operation_check=lambda: allowed,
    ))
    assert result["status"] == "cancelled"
    transport.assert_not_called()


@pytest.mark.parametrize("change", ["small_world_enabled", "small_world_harvest_enabled", "identity_disabled", "paused"])
def test_toggle_off_during_http_keeps_receipt_but_not_old_scheduling(world_env, change):
    h = world_env

    async def action(*_args, **_kwargs):
        invalidate(h, change)
        return flow_result()

    h.flow.side_effect = action
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert result["ok"]
    assert result["extra"]["operation_cancelled"]
    assert h.identity["next_small_world_time"] == NOW - 1
    assert h.identity["small_world_last_public_harvest_at"] == NOW
    assert h.identity["small_world_next_public_harvest_at"] == NOW + 28800


@pytest.mark.parametrize("kind", ["partial", "unknown"])
def test_post_mutation_without_panel_invalidates_only_unchanged_cached_decisions(world_env, kind):
    h = world_env
    h.identity["small_world_manifest_enabled"] = True
    h.identity["small_world_panel_snapshot"].update(has_prayer=True, has_wait=False)
    before = copy.deepcopy(h.identity["small_world_panel_snapshot"])
    h.flow.return_value = flow_result("manifest", ok=kind == "partial")
    h.flow.return_value["data"]["snapshot_current"] = False
    asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert h.identity["small_world_panel_snapshot"] == {**before, "updated_at": 0}
    assert h.identity["small_world_last_panel_at"] == 0
    assert h.identity["small_world_faith_value"] == 70
    assert h.identity["small_world_incense_stock"] == 900


def test_authoritative_countdown_takes_precedence_over_old_prayer_presence(world_env):
    h = world_env
    h.flow.return_value = flow_result("manifest")
    world = h.flow.return_value["data"]["overview"]["small_world"]
    world.update(has_prayer=True, prayer_remaining_seconds=21600)
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert result["ok"]
    assert h.identity["next_small_world_time"] == NOW + 21600 + cave.CD_BUFFER_SEC


def test_explicit_manual_run_still_works_with_automatic_master_switch_off(world_env):
    h = world_env
    h.identity["small_world_enabled"] = False
    state_module.set_miniapp_auto_config({"cave_public_small_world_enabled": False})
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert result["ok"]
    h.flow.assert_awaited_once()


def test_maintenance_and_channel_freeze_do_not_block_available_miniapp(world_env):
    state_module.set_global_enabled(False)
    state_module.set_global_pause_source("tianzun_maintenance")
    state_module.set_identity_enabled(IDENTITY_ID, False)
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [IDENTITY_ID]})
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert result["ok"]


def test_existing_command_pending_skips_miniapp_without_changing_its_deadline(world_env):
    h = world_env
    h.identity["small_world_phase"] = "manifest_sent"
    before = copy.deepcopy(h.identity)
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert result["extra"]["status"] == "busy"
    assert h.identity == before
    h.loader.assert_not_awaited()


@pytest.mark.parametrize("stage", ["start", "selected", "details"])
def test_session_loading_keeps_http_retry_after_for_the_runtime(world_env, monkeypatch, stage):
    h = world_env
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", LOAD_SESSION)
    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", AsyncMock(return_value="fixture-init"))
    responses = []
    if stage == "selected":
        responses.append({"ok": True, "account": {"playerId": 7401}, "identity": {"choices": [
            {"playerId": 7401}, {"playerId": IDENTITY_ID},
        ]}})
    if stage == "details":
        responses.append({"ok": True, "account": {"playerId": IDENTITY_ID}, "snapshot": {"level": "overview"}})
    responses.append(SimpleNamespace(
        status_code=429, headers={"Retry-After": "50000"}, json=lambda: {"ok": False, "error": "rate_limited"},
    ))
    transport = Mock(side_effect=responses)
    monkeypatch.setattr(dwelling, "_flow_transport", lambda *_args, **_kwargs: transport)
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert not result["ok"]
    assert result["extra"].get("retry_after_sec") == 50000
    assert transport.call_count == (1 if stage == "start" else 2)
    h.flow.assert_not_awaited()


def test_cooldowns_use_completion_time_not_a_slow_loader_start_time(world_env, monkeypatch):
    h = world_env
    clock = [0.0]
    monkeypatch.setattr(cave, "time", SimpleNamespace(time=lambda: NOW, monotonic=lambda: clock[0]))

    async def action(*_args, **_kwargs):
        clock[0] = 125.0
        return flow_result()

    h.flow.side_effect = action
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert result["ok"]
    assert h.identity["small_world_last_public_harvest_at"] == NOW + 125
    assert h.identity["small_world_next_public_harvest_at"] == NOW + 125 + 28800


def test_newer_miniapp_record_is_not_overwritten_by_an_old_confirmed_result(world_env):
    h = world_env
    newer = {"state": {"faith": 99, "incense_stock": 50000}, "updated_at": NOW + 10}

    async def action(*_args, **_kwargs):
        state_module.set_miniapp_state_records({f"{IDENTITY_ID}:cave_small_world": newer})
        return flow_result()

    h.flow.side_effect = action
    asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert state_module.get_miniapp_state_records()[f"{IDENTITY_ID}:cave_small_world"] == newer
    assert h.identity["small_world_faith_value"] == 70


def test_cancelled_notification_returns_confirmation_without_extra_game_requests(world_env):
    h = world_env
    h.audit.side_effect = asyncio.CancelledError()
    with pytest.raises(MiniAppFlowCancelled) as raised:
        asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert raised.value.result["ok"]
    assert h.identity["small_world_last_public_harvest_at"] == NOW
    h.flow.assert_awaited_once()


def test_notification_time_owner_replacement_does_not_return_old_roles_result(world_env):
    h = world_env

    async def audit(*_args, **_kwargs):
        invalidate(h, "replaced")

    h.audit.side_effect = audit
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert not result["ok"]
    assert result["extra"]["status"] == "cancelled"
    assert state_module.get_identity_state(IDENTITY_ID)["small_world_last_public_harvest_at"] == 0


def test_real_thread_cancellation_keeps_public_lock_until_confirmed_receipt(world_env, monkeypatch):
    h = world_env
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def transport(request):
        calls.append(request["payload"]["action"])
        entered.set()
        assert release.wait(3)
        raw = world_payload(collected=True)
        raw["actionResult"] = {"ok": True, "message": "confirmed-collect"}
        return raw

    async def actual_flow(identity_id, **kwargs):
        return await dwelling.run_cave_small_world_production_flow(
            identity_id, **kwargs, transport=transport, adapter=adapter_with_limit(),
        )

    monkeypatch.setattr(cave, "run_cave_small_world_production_flow", actual_flow)

    async def run():
        task = asyncio.create_task(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            assert cave._public_entry_lock(IDENTITY_ID).locked()
            concurrent = await cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW)
            assert concurrent["extra"]["status"] == "busy"
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as raised:
                await task
            assert raised.value.result["ok"]
            assert not cave._public_entry_lock(IDENTITY_ID).locked()
            assert h.identity["small_world_last_public_harvest_at"] == NOW
            assert h.identity["small_world_next_public_harvest_at"] == NOW + 28800
            assert calls == ["collect"]
            h.loader.assert_awaited_once()
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


@pytest.mark.parametrize("summary", [{"faith": 95}, {"faith": None}, {"faith": float("nan")}])
def test_partial_resource_domain_is_not_a_new_zero_balance_snapshot(summary):
    raw = {
        "ok": True, "account": {"playerId": IDENTITY_ID, "smallWorld": {"hasWorld": True, "summary": summary}},
        "actionResult": {"ok": True},
    }
    result = asyncio.run(run_flow(Mock(return_value=raw), initial_snapshot=world_payload()))
    assert result["data"]["action_confirmed"]
    assert not result["data"]["snapshot_current"]


def test_zero_resource_values_are_not_replaced_with_legacy_fallbacks():
    raw = world_payload(collected=True)
    world = raw["account"]["smallWorld"]
    world.update(population=999, incenseStock=888, pendingIncense=777)
    world["summary"].update(population=0, incensePoints=0, uncollectedIncense=0)
    parsed = dwelling.parse_cave_dwelling_overview(raw)["small_world"]
    assert parsed["population"] == 0
    assert parsed["incense_stock"] == 0
    assert parsed["pending_incense"] == 0


def test_unchanged_large_stock_never_enables_refining(world_env, monkeypatch):
    h = world_env
    h.identity.update(small_world_harvest_enabled=False, small_world_refine_enabled=False)
    h.session["result"]["data"]["raw"]["account"]["smallWorld"]["summary"]["incensePoints"] = 150000
    transport = Mock()

    async def actual_flow(identity_id, **kwargs):
        return await dwelling.run_cave_small_world_production_flow(identity_id, **kwargs, transport=transport)

    monkeypatch.setattr(cave, "run_cave_small_world_production_flow", actual_flow)
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert result["ok"]
    assert not result["extra"]["action"]
    assert h.identity["small_world_refine_enabled"] is False
    assert h.identity["small_world_incense_stock"] == 150000
    transport.assert_not_called()
    assert cave._calc_refine_amount(1180) == 30


def test_returned_foreign_resource_snapshot_cannot_become_local_truth(world_env, monkeypatch):
    h = world_env
    raw = world_payload(collected=True)
    raw["account"]["playerId"] = IDENTITY_ID + 1
    raw["account"]["smallWorld"]["summary"]["incensePoints"] = 50000
    raw["actionResult"] = {"ok": True}

    async def actual_flow(identity_id, **kwargs):
        return await dwelling.run_cave_small_world_production_flow(identity_id, **kwargs, transport=lambda _request: raw)

    monkeypatch.setattr(cave, "run_cave_small_world_production_flow", actual_flow)
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert not result["ok"]
    assert h.identity["small_world_incense_stock"] == 900
    assert h.identity["small_world_last_public_harvest_at"] == 0


def test_noop_partial_panel_does_not_drive_repeated_prayer_refreshes(world_env, monkeypatch):
    h = world_env
    h.identity.update(small_world_manifest_enabled=True, small_world_refresh_enabled=True, small_world_harvest_enabled=False)
    h.session["result"]["data"]["raw"]["account"]["smallWorld"] = {"hasWorld": True, "summary": {"faith": 95}}
    transport = Mock()

    async def actual_flow(identity_id, **kwargs):
        return await dwelling.run_cave_small_world_production_flow(identity_id, **kwargs, transport=transport)

    monkeypatch.setattr(cave, "run_cave_small_world_production_flow", actual_flow)
    asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert h.identity["small_world_refresh_count"] == 0
    assert h.identity["next_small_world_time"] == NOW + 21600
    assert h.identity["small_world_faith_value"] == 70
    transport.assert_not_called()


def test_incomplete_start_snapshot_does_not_enter_the_spending_planner():
    raw = world_payload()
    raw["account"]["smallWorld"]["summary"].pop("incensePoints")
    planner = Mock(return_value={"action": "collect"})
    transport = Mock(return_value={"ok": True, "actionResult": {"ok": True}})
    result = asyncio.run(dwelling.run_cave_small_world_production_flow(
        IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL, init_data="fixture-init",
        player_id=IDENTITY_ID, initial_snapshot=raw, action_planner=planner, transport=transport,
    ))
    assert result["status"] == "noop"
    assert not result["data"]["action_dispatched"]
    planner.assert_not_called()
    transport.assert_not_called()


def test_nested_start_envelope_does_not_hide_the_new_action_snapshot():
    before = {"ok": True, "data": world_payload()}
    after = world_payload(collected=True)
    after["actionResult"] = {"ok": True}
    result = asyncio.run(run_flow(Mock(return_value=after), initial_snapshot=before))
    assert result["ok"]
    assert result["data"]["snapshot_current"]
    assert result["data"]["overview"]["small_world"]["incense_stock"] == 1800
    assert result["data"]["overview"]["small_world"]["pending_incense"] == 0


@pytest.mark.parametrize("flow", ["run_cave_dwelling_start_production_flow", "run_cave_dwelling_snapshot_production_flow"])
def test_guarded_loader_flows_report_cancelled_before_init_data_rpc(monkeypatch, flow):
    client = Mock()
    monkeypatch.setattr(dwelling, "_get_identity_client_with_account", client)
    result = asyncio.run(getattr(dwelling, flow)(
        IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL, operation_check=lambda: False,
    ))
    assert not result["ok"]
    assert result["status"] == "cancelled"
    client.assert_not_called()


@pytest.mark.parametrize("player_id", [IDENTITY_ID, -1_000_000_000_000 - IDENTITY_ID, str(IDENTITY_ID)])
def test_small_world_builder_preserves_explicit_player_and_action_metadata(player_id):
    request = dwelling.build_cave_small_world_action_request(
        "refine_shenshi", token="df_FIXTURE42", init_data="fixture-init",
        player_id=player_id, payload={"amount": 30},
    )
    assert request["payload"] == {
        "action": "refine_shenshi", "playerId": int(player_id), "amount": 30,
        "token": "df_FIXTURE42", "initData": "fixture-init",
    }


def test_small_world_builder_requires_selected_player():
    with pytest.raises(TypeError):
        dwelling.build_cave_small_world_action_request("collect", token="df_FIXTURE42")


@pytest.mark.parametrize("player_id", [None, True, False, 0, "", -1001, "bad", 1001.0, {}, []])
def test_small_world_builder_rejects_invalid_players(player_id):
    with pytest.raises(ValueError):
        dwelling.build_cave_small_world_action_request("collect", token="df_FIXTURE42", player_id=player_id)


@pytest.mark.parametrize("player_id", [None, True, False, 0, "", -1001, "bad", 1001.0, IDENTITY_ID + 1, {}, []])
def test_small_world_flow_rejects_invalid_selection_before_auth_or_transport(monkeypatch, player_id):
    auth = AsyncMock(return_value="fixture-init")
    monkeypatch.setattr(dwelling, "request_cave_treasure_miniapp_init_data", auth)
    transport = Mock(return_value=world_payload())
    planner = Mock(return_value={"action": "collect"})
    result = asyncio.run(dwelling.run_cave_small_world_production_flow(
        IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL,
        player_id=player_id, action_planner=planner, transport=transport, adapter=adapter_with_limit(),
    ))
    assert not result["ok"]
    assert result["error"].startswith("cave_action_player_")
    auth.assert_not_awaited()
    planner.assert_not_called()
    transport.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("action", "barrier"), ("playerId", IDENTITY_ID + 1),
    ("token", "df_OTHER"), ("initData", "other-init"),
])
def test_small_world_planner_cannot_override_action_identity_or_auth(field, value):
    transport = Mock(return_value=world_payload())
    result = asyncio.run(dwelling.run_cave_small_world_production_flow(
        IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL, init_data="fixture-init",
        player_id=IDENTITY_ID, initial_snapshot=world_payload(), transport=transport,
        action_planner=lambda _overview: {"action": "collect", "payload": {field: value}},
    ))
    assert not result["ok"]
    assert result["error"] == "small_world_payload_reserved"
    assert not result["data"]["action_dispatched"]
    transport.assert_not_called()


@pytest.mark.parametrize("stage", ["provided", "start", "action"])
@pytest.mark.parametrize("variant", ["missing", "selector_only", "other", "bool", "float", "container", "contradiction"])
def test_small_world_requires_account_identity_before_planning_or_accepting_receipt(stage, variant):
    response = world_payload(collected=stage == "action")
    account = response["account"]
    if variant in {"missing", "selector_only"}:
        account.pop("playerId")
    elif variant == "contradiction":
        response["identity"] = {"selectedPlayerId": IDENTITY_ID + 1}
    else:
        account["playerId"] = {"other": IDENTITY_ID + 1, "bool": True, "float": float(IDENTITY_ID), "container": []}[variant]
    if variant == "selector_only":
        response["identity"] = {"selectedPlayerId": IDENTITY_ID}
    response["actionResult"] = {"ok": True, "rawMessage": "unowned-receipt"}
    response = {"ok": True, "data": response}
    before = world_payload()
    snapshot = response if stage == "provided" else before if stage == "action" else None
    transport = Mock(return_value=response)
    planner = Mock(return_value={"action": "collect"})
    result = asyncio.run(dwelling.run_cave_small_world_production_flow(
        IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL, init_data="fixture-init",
        player_id=IDENTITY_ID, initial_snapshot=snapshot, transport=transport,
        action_planner=planner, adapter=adapter_with_limit(),
    ))
    assert not result["ok"]
    assert result["status"] == "identity_unverified"
    assert result["error"].startswith("cave_action_player_")
    assert not result["data"]["action_confirmed"]
    assert not result["data"]["snapshot_current"]
    assert not result["data"].get("action_result")
    assert transport.call_count == (0 if stage == "provided" else 1)
    if stage == "action":
        assert result["data"]["action_dispatched"]
        assert result["data"]["raw"] == before
        planner.assert_called_once()
    else:
        assert not result["data"].get("raw")
        planner.assert_not_called()


@pytest.mark.parametrize("action", sorted(dwelling.CAVE_SMALL_WORLD_ACTIONS))
@pytest.mark.parametrize("reply_signed", [True, False])
def test_small_world_channel_action_transport_never_falls_back_to_primary(action, reply_signed):
    player_id = -1_000_000_000_000 - IDENTITY_ID
    calls = []

    def transport(request):
        calls.append(request)
        selected = request["payload"].get("playerId", 7401)
        response = world_payload()
        response["account"]["playerId"] = selected if reply_signed else dwelling._normalize_cave_inventory_player_id(selected)
        if request["payload"].get("action"):
            response["actionResult"] = {"ok": True}
        return response

    result = asyncio.run(dwelling.run_cave_small_world_production_flow(
        IDENTITY_ID, token="df_FIXTURE42", webview_url=ENTRY_URL, init_data="fixture-init",
        player_id=player_id, transport=transport, adapter=adapter_with_limit(),
        action_planner=lambda _overview: {"action": action, "payload": {"amount": 30} if action == "refine_shenshi" else {}},
    ))
    assert result["ok"], result
    assert result["data"]["action_confirmed"]
    assert [call["payload"]["playerId"] for call in calls] == [player_id, player_id]
    assert calls[-1]["payload"]["action"] == action


def test_real_small_world_entry_selection_and_harvest_update_only_the_channel(world_env, monkeypatch):
    h = world_env
    player_id = -1_000_000_000_000 - IDENTITY_ID
    state_module.set_identity_account(7401, 7401)
    primary_before = copy.deepcopy(state_module.get_identity_state(7401))
    state_module.set_identity_enabled(IDENTITY_ID, False)
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [IDENTITY_ID]})
    calls = []

    def transport(request):
        payload = request["payload"]
        selected = payload.get("playerId", 7401)
        calls.append((payload.get("action", "start"), selected))
        raw = world_payload(collected=payload.get("action") == "collect")
        raw["account"]["playerId"] = selected
        raw["identity"] = {"selectedPlayerId": selected, "choices": [{"playerId": 7401}, {"playerId": player_id}]}
        if payload.get("action"):
            raw["actionResult"] = {"ok": True, "completed": True}
        return raw

    monkeypatch.setattr(cave, "_load_cave_public_identity_session", LOAD_SESSION)
    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", AsyncMock(return_value="fixture-init"))
    monkeypatch.setattr(cave, "run_cave_small_world_production_flow", dwelling.run_cave_small_world_production_flow)
    monkeypatch.setattr(dwelling, "_flow_transport", lambda *_args, **_kwargs: transport)
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert result["ok"], result
    assert calls == [("start", 7401), ("start", player_id), ("collect", player_id)]
    assert state_module.get_identity_state(7401) == primary_before
    assert h.identity["small_world_last_public_harvest_at"] == NOW
    assert h.identity["small_world_next_public_harvest_at"] == NOW + 28800
    assert h.identity["small_world_incense_stock"] == 1800
    assert h.identity["small_world_refine_enabled"] is False


def test_unowned_small_world_failure_text_cannot_extend_channel_cooldown(world_env, monkeypatch):
    h = world_env
    h.identity.update(small_world_harvest_enabled=False, small_world_manifest_enabled=True)
    h.session["result"]["data"]["raw"] = world_payload(prayer=True)
    raw = world_payload(prayer=True)
    raw["account"]["playerId"] = 7401
    raw["actionResult"] = {"ok": False, "rawMessage": "\u8d44\u6e90\u4e0d\u8db3\uff0c\u8bf7\u7b49\u5f85 999 \u5c0f\u65f6\u3002"}

    async def actual_flow(identity_id, **kwargs):
        return await dwelling.run_cave_small_world_production_flow(identity_id, **kwargs, transport=lambda _request: raw)

    monkeypatch.setattr(cave, "run_cave_small_world_production_flow", actual_flow)
    result = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW))
    assert not result["ok"]
    assert "cave_action_player_mismatch" in h.identity["small_world_last_error"]
    assert h.identity["next_small_world_time"] == NOW + 21600
    assert h.identity["small_world_incense_stock"] == 900
