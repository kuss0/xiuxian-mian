import asyncio
import copy
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_runtime as cave
from model.features import stargazer
from model.features import stargazer_miniapp as farm
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppRequestPolicy


def farm_response(kind, *, collected=False):
    status = {"ready": "\u53ef\u6536\u96c6", "idle": "\u7a7a\u95f2", "busy": "\u51dd\u805a\u4e2d"}[kind]
    result = {
        "ok": True,
        "domain": {"mode": "stars", "plots": [{
            "key": "a", "empty": kind == "idle", "status": status,
            "remainingSec": 60 if kind == "busy" else 0,
        }]},
    }
    if collected or kind == "busy":
        result["actionResult"] = {"ok": True, "message": "\u3010star-material\u3011x2" if collected else "pulled"}
    return result


def adapter_with_limit(count=32):
    return replace(farm.build_stargazer_miniapp_adapter(), request_policy=MiniAppRequestPolicy(
        min_interval_sec=0, max_requests_per_run=count,
    ))


@pytest.fixture
def stargazer_env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    identity_id = 990400001
    state_module.set_identity_account(identity_id, 7401)
    identity = state_module.get_identity_state(identity_id)
    identity.update(stargazer_enabled=True, next_stargazer_panel_time=4000, stargazer_last_action="fixture-original")
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(stargazer, "_MINIAPP_RUN_LOCKS", {})
    monkeypatch.setattr(stargazer, "_MINIAPP_MANUAL_AUTH_UNTIL", {})
    monkeypatch.setattr(cave, "_capture_store", Mock(return_value=None))
    monkeypatch.setattr(stargazer, "_stargazer_miniapp_capture_store", Mock(return_value=None))
    monkeypatch.setattr(stargazer, "save_state", Mock())
    monkeypatch.setattr(cave, "save_state", Mock())
    monkeypatch.setattr(stargazer, "send_audit_log", AsyncMock(return_value=True))
    items = Mock()
    monkeypatch.setattr(stargazer, "apply_storage_bag_item_deltas", items)
    session = {
        "ok": True, "init_data": "fixture-init", "player_id": identity_id,
        "result": {"ok": True, "data": {"raw": {"account": {"externalApps": {"groups": [{
            "apps": [{"key": "sect_farm", "title": "\u89c2\u661f\u53f0", "available": True, "action": "sect_farm"}],
        }]}}}}},
    }
    loader = AsyncMock(return_value=session)
    external = AsyncMock(return_value={"ok": True, "data": {
        "url": "https://asc.aiopenai.app/miniapp/xianxia-sect-farm?startapp=farm_FIXTURE40",
    }})
    result = {"ok": True, "status": "wait", "data": {
        "farm_state": farm.parse_stargazer_farm_state(farm_response("busy")),
        "action_counts": {"collect": 1, "pull": 1, "soothe": 0},
        "item_deltas": {"star-material": 2},
    }}
    flow = AsyncMock(return_value=result)
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_external_action_production_flow", external)
    monkeypatch.setattr(cave, "run_stargazer_miniapp_production_flow", flow)
    monkeypatch.setattr(stargazer, "run_stargazer_miniapp_production_flow", flow)
    yield SimpleNamespace(
        identity_id=identity_id, identity=identity, session=session, loader=loader,
        external=external, flow=flow, result=result, items=items,
        url="https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE40", now=1700000000.0,
    )
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def invalidate(h, change):
    if change == "removed":
        state_module.remove_identity(h.identity_id)
    elif change == "replaced":
        state_module.remove_identity(h.identity_id)
        state_module.set_identity_account(h.identity_id, 7401)
    elif change == "rebound":
        state_module.set_identity_account(h.identity_id, 7402)
    elif change == "identity_disabled":
        state_module.set_identity_enabled(h.identity_id, False)
    elif change == "module_disabled":
        h.identity["stargazer_enabled"] = False
    elif change == "choice":
        state_module.set_stargazer_star_choice(h.identity_id, "\u5929\u96f7\u661f")
    else:
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")


def test_flow_keeps_confirmed_collect_when_next_farm_snapshot_is_missing():
    responses = [farm_response("ready"), {"ok": True, "actionResult": {
        "ok": True, "message": "\u3010star-material\u3011x2",
    }}]
    transport = Mock(side_effect=responses)
    result = farm.run_stargazer_miniapp_lab_flow(
        token="farm_FIXTURE40", init_data="fixture-init", transport=transport, adapter=adapter_with_limit(),
    )
    assert not result["ok"]
    assert result["data"].get("action_counts", {}).get("collect") == 1
    assert result["data"].get("item_deltas") == {"star-material": 2}
    assert transport.call_count == 2


def test_flow_uses_one_budget_for_start_and_all_actions():
    transport = Mock(side_effect=[farm_response("ready"), farm_response("idle", collected=True), farm_response("busy")])
    result = farm.run_stargazer_miniapp_lab_flow(
        token="farm_FIXTURE40", init_data="fixture-init", transport=transport, adapter=adapter_with_limit(2),
    )
    assert not result["ok"]
    assert result["error"] == "request_budget_exhausted"
    assert result["data"]["item_deltas"] == {"star-material": 2}
    assert transport.call_count == 2


def test_flow_stops_next_action_after_guard_invalidation_but_keeps_received_reward():
    allowed = True
    calls = []

    def transport(request):
        nonlocal allowed
        action = request["payload"].get("action") or "start"
        calls.append(action)
        if action == "start":
            return farm_response("ready")
        allowed = False
        return farm_response("idle", collected=True)

    result = farm.run_stargazer_miniapp_lab_flow(
        token="farm_FIXTURE40", init_data="fixture-init", transport=transport,
        adapter=adapter_with_limit(), operation_check=lambda: allowed,
    )
    assert not result["ok"]
    assert result["status"] == "cancelled"
    assert result["data"]["item_deltas"] == {"star-material": 2}
    assert calls == ["start", "collect"]


def test_cancelled_production_thread_is_drained_and_stops_after_inflight_collect():
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def transport(request):
        action = request["payload"].get("action") or "start"
        calls.append(action)
        if action == "start":
            return farm_response("ready")
        if action == "collect":
            entered.set()
            assert release.wait(3)
            return farm_response("idle", collected=True)
        return farm_response("busy")

    async def run():
        task = asyncio.create_task(farm.run_stargazer_miniapp_production_flow(
            990400001, token="farm_FIXTURE40", webview_url="", init_data="fixture-init",
            transport=transport, adapter=adapter_with_limit(),
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done(), "caller released ownership while its HTTP worker was running"
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as raised:
                await task
            assert raised.value.result["data"]["item_deltas"] == {"star-material": 2}
            assert calls == ["start", "collect"]
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["loader", "external"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "identity_disabled", "module_disabled", "choice", "paused"])
def test_public_caller_rechecks_owner_and_configuration_between_awaits(stargazer_env, phase, change):
    h = stargazer_env
    mocked = h.loader if phase == "loader" else h.external
    response = mocked.return_value

    async def invalidate_and_return(*_args, **_kwargs):
        invalidate(h, change)
        return response

    mocked.side_effect = invalidate_and_return
    result = asyncio.run(cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now))
    assert not result["ok"]
    assert result["extra"].get("status") == "cancelled"
    h.flow.assert_not_awaited()
    h.items.assert_not_called()
    if phase == "loader":
        h.external.assert_not_awaited()
    if change == "removed":
        assert not state_module.has_identity(h.identity_id)


def test_public_and_manual_callers_share_one_game_operation_lock(stargazer_env):
    h = stargazer_env

    async def run():
        async with stargazer._stargazer_miniapp_run_lock(h.identity_id):
            result = await cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now)
            assert not result["ok"]

    asyncio.run(run())
    h.loader.assert_not_awaited()
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
def test_public_result_is_not_applied_to_replaced_owner(stargazer_env, change):
    h = stargazer_env

    async def action(*_args, **_kwargs):
        invalidate(h, change)
        return h.result

    h.flow.side_effect = action
    result = asyncio.run(cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now))
    assert not result["ok"]
    h.items.assert_not_called()
    if change == "removed":
        assert not state_module.has_identity(h.identity_id)


@pytest.mark.parametrize("change", ["module_disabled", "identity_disabled", "paused", "choice"])
def test_same_owner_confirmed_result_does_not_overwrite_new_schedule(stargazer_env, change):
    h = stargazer_env

    async def action(*_args, **_kwargs):
        invalidate(h, change)
        h.identity["next_stargazer_panel_time"] = h.now + 12345
        h.identity["stargazer_last_action"] = "replacement-schedule"
        return h.result

    h.flow.side_effect = action
    result = asyncio.run(cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now))
    assert result["ok"]
    assert result["extra"]["rewards"] == {"star-material": 2}
    h.items.assert_called_once_with(h.identity_id, {"star-material": 2})
    assert h.identity["next_stargazer_panel_time"] == h.now + 12345
    assert h.identity["stargazer_last_action"] == "replacement-schedule"


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module_disabled", "paused"])
def test_manual_caller_rechecks_after_entry_notification(stargazer_env, change):
    h = stargazer_env
    button = SimpleNamespace(text="\u8fdb\u5165\u7075\u5703", button=SimpleNamespace(
        url="https://t.me/fanrenxiuxian_bot/app?startapp=farm_FIXTURE40",
    ))
    event = SimpleNamespace(id=4001, message=SimpleNamespace(buttons=[[button]]))
    stargazer.authorize_stargazer_miniapp_manual_run(h.identity_id, now=h.now)

    async def notify(*_args, **_kwargs):
        invalidate(h, change)
        return True

    stargazer.send_audit_log.side_effect = notify

    async def run():
        with state_module.use_identity(h.identity_id):
            await stargazer.handle_stargazer_miniapp_entry(event, "\u89c2\u661f\u53f0 MiniApp", h.now)

    asyncio.run(run())
    h.flow.assert_not_awaited()
    h.items.assert_not_called()


def test_notification_failure_cannot_discard_confirmed_public_result(stargazer_env, caplog):
    h = stargazer_env
    stargazer.send_audit_log.side_effect = RuntimeError("SECRET-REPORT-DETAIL")
    result = asyncio.run(cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now))
    assert result["ok"]
    h.items.assert_called_once_with(h.identity_id, {"star-material": 2})
    assert "RuntimeError" in caplog.text
    assert "SECRET-REPORT-DETAIL" not in caplog.text


def test_channel_freeze_and_maintenance_still_allow_public_stargazer(stargazer_env):
    h = stargazer_env
    state_module.set_identity_enabled(h.identity_id, False)
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [h.identity_id]})
    state_module.set_global_enabled(False)
    state_module.set_global_pause_source("tianzun_maintenance")
    result = asyncio.run(cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now))
    assert result["ok"]
    h.flow.assert_awaited_once()


def test_ordinary_public_result_still_updates_authoritative_wait(stargazer_env):
    h = stargazer_env
    result = asyncio.run(cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now))
    assert result["ok"]
    assert h.identity["next_stargazer_panel_time"] > h.now + 60
    assert h.identity["stargazer_last_action"] == "miniapp_waiting_panel"
    h.items.assert_called_once_with(h.identity_id, {"star-material": 2})


def test_duplicate_entry_during_public_flow_does_not_mutate_its_schedule(stargazer_env):
    h = stargazer_env
    original = copy.deepcopy(h.identity)
    button = SimpleNamespace(text="\u8fdb\u5165\u7075\u5703", button=SimpleNamespace(
        url="https://t.me/fanrenxiuxian_bot/app?startapp=farm_FIXTURE40",
    ))
    event = SimpleNamespace(id=4002, message=SimpleNamespace(buttons=[[button]]))

    async def run():
        with state_module.use_identity(h.identity_id):
            async with stargazer._stargazer_miniapp_run_lock(h.identity_id):
                assert await stargazer.handle_stargazer_miniapp_entry(event, "\u89c2\u661f\u53f0 MiniApp", h.now)

    asyncio.run(run())
    assert h.identity == original
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("caller", ["public", "manual"])
def test_cancelled_caller_holds_locks_until_thread_finishes_and_records_reward_once(stargazer_env, monkeypatch, caller):
    h = stargazer_env
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def transport(request):
        action = request["payload"].get("action") or "start"
        calls.append(action)
        if action == "start":
            return farm_response("ready")
        if action == "collect":
            entered.set()
            assert release.wait(3)
            return farm_response("idle", collected=True)
        return farm_response("busy")

    async def real_flow(identity_id, **kwargs):
        return await farm.run_stargazer_miniapp_production_flow(
            identity_id, **{**kwargs, "init_data": "fixture-init"}, transport=transport, adapter=adapter_with_limit(),
        )

    monkeypatch.setattr(cave, "run_stargazer_miniapp_production_flow", real_flow)
    monkeypatch.setattr(stargazer, "run_stargazer_miniapp_production_flow", real_flow)
    button = SimpleNamespace(text="\u8fdb\u5165\u7075\u5703", button=SimpleNamespace(
        url="https://t.me/fanrenxiuxian_bot/app?startapp=farm_FIXTURE40",
    ))
    event = SimpleNamespace(id=4003, message=SimpleNamespace(buttons=[[button]]))

    async def run():
        with state_module.use_identity(h.identity_id):
            if caller == "manual":
                stargazer.authorize_stargazer_miniapp_manual_run(h.identity_id, now=h.now)
                task = asyncio.create_task(stargazer.handle_stargazer_miniapp_entry(
                    event, "\u89c2\u661f\u53f0 MiniApp", h.now,
                ))
            else:
                task = asyncio.create_task(cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            assert stargazer._stargazer_miniapp_run_lock(h.identity_id).locked()
            if caller == "public":
                assert cave._public_entry_lock(h.identity_id).locked()
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as raised:
                await task
            result = raised.value.result
            rewards = result["extra"]["rewards"] if caller == "public" else result["data"]["item_deltas"]
            assert rewards == {"star-material": 2}
            h.items.assert_called_once_with(h.identity_id, {"star-material": 2})
            assert calls == ["start", "collect"]
            assert not stargazer._stargazer_miniapp_run_lock(h.identity_id).locked()
            if caller == "public":
                assert not cave._public_entry_lock(h.identity_id).locked()
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


def test_new_timer_alone_invalidates_inflight_operation_without_losing_reward(stargazer_env):
    h = stargazer_env

    async def action(*_args, **_kwargs):
        h.identity["next_stargazer_panel_time"] = h.now + 9000
        return h.result

    h.flow.side_effect = action
    result = asyncio.run(cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now))
    assert result["ok"]
    assert h.identity["next_stargazer_panel_time"] == h.now + 9000
    h.items.assert_called_once_with(h.identity_id, {"star-material": 2})


async def run_caller(h, caller):
    if caller == "public":
        return await cave.run_cave_public_stargazer(h.identity_id, h.url, now=h.now)
    button = SimpleNamespace(text="farm", button=SimpleNamespace(
        url="https://t.me/fanrenxiuxian_bot/app?startapp=farm_FIXTURE40",
    ))
    event = SimpleNamespace(id=4004, message=SimpleNamespace(buttons=[[button]]))
    stargazer.authorize_stargazer_miniapp_manual_run(h.identity_id, now=h.now)
    with state_module.use_identity(h.identity_id):
        return await stargazer.handle_stargazer_miniapp_entry(event, "\u89c2\u661f\u53f0 MiniApp", h.now)


@pytest.mark.parametrize("phase", ["slot", "entity", "input", "webview"])
def test_webview_resolution_stops_at_each_invalidated_await(monkeypatch, phase):
    allowed = True

    async def step(boundary, value):
        nonlocal allowed
        if phase == boundary:
            allowed = False
        return value

    @asynccontextmanager
    async def slot(**_kwargs):
        await step("slot", None)
        yield

    async def webview(_request):
        return await step("webview", SimpleNamespace(url="https://asc.aiopenai.app/#tgWebAppData=fixture-init"))

    async def entity(_bot):
        return await step("entity", "fixture-bot")

    async def input_entity(_bot):
        return await step("input", "fixture-input")

    client = AsyncMock(side_effect=webview)
    client.get_entity = AsyncMock(side_effect=entity)
    client.get_input_entity = AsyncMock(side_effect=input_entity)
    monkeypatch.setattr(farm, "account_rpc_slot", slot)
    monkeypatch.setattr(farm, "_get_identity_client_with_account", Mock(return_value=(7401, client)))
    transport = Mock()
    result = asyncio.run(farm.run_stargazer_miniapp_production_flow(
        990400001, token="farm_FIXTURE40",
        webview_url="https://t.me/fanrenxiuxian_bot/app?startapp=farm_FIXTURE40",
        transport=transport, adapter=adapter_with_limit(),
        operation_check=lambda: allowed,
    ))
    assert result["status"] == "cancelled", result
    transport.assert_not_called()
    assert client.get_entity.await_count == int(phase != "slot")
    assert client.get_input_entity.await_count == int(phase in {"input", "webview"})
    assert client.await_count == int(phase == "webview")


@pytest.mark.parametrize("caller", ["public", "manual"])
def test_cancelled_without_result_does_not_write_or_fabricate_outcome(stargazer_env, caller):
    h = stargazer_env
    before = {}

    async def cancel(*_args, **_kwargs):
        before.update(copy.deepcopy(h.identity))
        raise MiniAppFlowCancelled()

    h.flow.side_effect = cancel
    with pytest.raises(MiniAppFlowCancelled) as raised:
        asyncio.run(run_caller(h, caller))
    assert not raised.value.result
    assert h.identity == before
    h.items.assert_not_called()
    assert not cave._public_entry_lock(h.identity_id).locked()
    assert not stargazer._stargazer_miniapp_run_lock(h.identity_id).locked()


@pytest.mark.parametrize("caller", ["public", "manual"])
def test_cancelled_notification_keeps_confirmed_reward_once(stargazer_env, caller):
    h = stargazer_env

    async def notify(*_args, **_kwargs):
        if h.items.called:
            raise asyncio.CancelledError
        return True

    stargazer.send_audit_log.side_effect = notify
    with pytest.raises(MiniAppFlowCancelled) as raised:
        asyncio.run(run_caller(h, caller))
    result = raised.value.result
    rewards = result["extra"]["rewards"] if caller == "public" else result["data"]["item_deltas"]
    assert rewards == {"star-material": 2}
    h.items.assert_called_once_with(h.identity_id, {"star-material": 2})
    assert h.identity["stargazer_last_action"] == "miniapp_waiting_panel"
    assert not cave._public_entry_lock(h.identity_id).locked()
    assert not stargazer._stargazer_miniapp_run_lock(h.identity_id).locked()


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
@pytest.mark.parametrize("caller", ["public", "manual"])
def test_notification_cannot_return_result_under_replacement_owner(stargazer_env, cancel, change, caller):
    h = stargazer_env

    async def notify(*_args, **_kwargs):
        if not h.items.called:
            return True
        invalidate(h, change)
        if cancel:
            raise asyncio.CancelledError
        return True

    stargazer.send_audit_log.side_effect = notify
    if cancel:
        with pytest.raises(MiniAppFlowCancelled) as raised:
            asyncio.run(run_caller(h, caller))
        assert not raised.value.result
    else:
        result = asyncio.run(run_caller(h, caller))
        if caller == "public":
            assert not result["ok"]
            assert result["extra"]["status"] == "cancelled"
            assert not result["extra"].get("rewards")
        else:
            assert result is True
    h.items.assert_called_once_with(h.identity_id, {"star-material": 2})
    if change == "removed":
        assert not state_module.has_identity(h.identity_id)


@pytest.mark.parametrize("caller", ["public", "manual"])
def test_partial_success_failure_retains_reward_and_schedules_from_failure(stargazer_env, caller):
    h = stargazer_env
    h.flow.return_value = {**h.result, "ok": False, "status": "failed", "error": "upstream failure"}
    result = asyncio.run(run_caller(h, caller))
    if caller == "public":
        assert not result["ok"]
        assert result["extra"]["rewards"] == {"star-material": 2}
    h.items.assert_called_once_with(h.identity_id, {"star-material": 2})
    assert h.identity["stargazer_last_action"] == "miniapp_error"
    assert h.identity["stargazer_followup_due_at"] > h.now + stargazer.STARGAZER_MINIAPP_FAILURE_BACKOFF_SEC
