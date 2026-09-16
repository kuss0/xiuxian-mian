import asyncio
import copy
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model import ui
from model.features import cave_treasure_runtime as cave
from model.features import stargazer, tianti
from model.features.miniapp_common import MiniAppFlowCancelled


BACKGROUND_ACTIONS = (
    "yuanying", "deep_status", "small_world", "small_world_harvest", "fishing",
    "stargazer", "fate_cards", "treasure", "tianti_status",
)


@pytest.fixture
def background(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    identity_id = 990410001
    state_module.set_identity_account(identity_id, identity_id)
    identity = state_module.get_identity_state(identity_id)
    identity.update(stargazer_enabled=True, next_stargazer_panel_time=0)
    config = {
        "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE41"],
        "cave_public_stargazer_enabled": True,
        "cave_public_delay_sec": 20,
    }
    state_module.set_miniapp_auto_config(config)
    monkeypatch.setattr(ui, "_cave_public_background_state", {
        "running": False, "next_run_at": 0, "cursor": 0, "last_action": "", "last_result": "",
        "circuit_open_until": 0, "circuit_reason": "",
    })
    monkeypatch.setattr(ui, "_cave_public_background_retry_at", {})
    monkeypatch.setattr(ui, "_cave_public_background_operation", None)
    monkeypatch.setattr(ui, "_cave_public_background_daily_done", set())
    monkeypatch.setattr(ui, "_cave_public_batch_state", {"running": False})
    monkeypatch.setattr(ui, "_cave_public_ui_run_lock", asyncio.Lock())
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", state_module.get_miniapp_auto_config)
    monkeypatch.setattr(ui, "console_log", Mock())
    monkeypatch.setattr(ui, "save_state", Mock())
    now = [1_700_000_000.0]
    monkeypatch.setattr(ui.time, "time", lambda: now[0])
    queued = []
    monkeypatch.setattr(ui, "_fire_and_forget", queued.append)
    real_run = ui.ui_run_cave_public_entry
    run = AsyncMock(return_value=(True, "fixture completed", {}))
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", run)
    h = SimpleNamespace(identity_id=identity_id, identity=identity, config=config, now=now, queued=queued, run=run, real_run=real_run)
    try:
        yield h
    finally:
        for coroutine in queued:
            coroutine.close()
        state_module._meta_state.clear()
        state_module._meta_state.update(saved)


def change_background(h, change):
    if change == "removed":
        state_module.remove_identity(h.identity_id)
    elif change == "replaced":
        state_module.remove_identity(h.identity_id)
        state_module.set_identity_account(h.identity_id, h.identity_id)
        state_module.get_identity_state(h.identity_id)["stargazer_enabled"] = True
    elif change == "rebound":
        state_module.set_identity_account(h.identity_id, h.identity_id + 1)
    elif change == "identity_disabled":
        state_module.set_identity_enabled(h.identity_id, False)
    elif change == "module_disabled":
        h.identity["stargazer_enabled"] = False
    elif change == "choice":
        state_module.set_stargazer_star_choice(h.identity_id, "\u5929\u96f7\u661f")
    elif change == "rescheduled":
        h.identity["next_stargazer_panel_time"] = h.now[0] + 600
    elif change == "auto_disabled":
        state_module.set_miniapp_auto_config({**h.config, "cave_public_stargazer_enabled": False})
    elif change == "entry_changed":
        state_module.set_miniapp_auto_config({
            **h.config, "cave_public_entry_urls": ["https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE42"],
        })
    elif change == "day_changed":
        h.now[0] += 86400
    else:
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")


async def queue_background(h):
    result = await ui._run_cave_public_background_scheduler(h.now[0], state_module.get_miniapp_auto_config())
    assert result["started"], result
    assert len(h.queued) == 1
    return h.queued[0]


@pytest.mark.parametrize("change", [
    "removed", "replaced", "rebound", "identity_disabled", "module_disabled", "choice",
    "rescheduled", "auto_disabled", "entry_changed", "day_changed", "paused",
])
def test_queued_background_does_not_start_after_admission_changes(background, change):
    h = background

    async def run():
        worker = await queue_background(h)
        change_background(h, change)
        await worker

    asyncio.run(run())
    h.run.assert_not_awaited()
    assert not ui._cave_public_background_retry_at
    assert not ui._cave_public_background_daily_done
    assert not ui._cave_public_background_state["running"]
    if change == "removed":
        assert not state_module.has_identity(h.identity_id)


def test_cancelled_background_without_outcome_releases_slot_without_failure_cooldown(background):
    h = background
    h.run.side_effect = asyncio.CancelledError

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await (await queue_background(h))

    asyncio.run(run())
    assert not ui._cave_public_background_retry_at
    assert not ui._cave_public_background_daily_done
    assert not ui._cave_public_background_state["running"]


def test_unknown_treasure_result_cannot_mark_the_day_complete(background):
    h = background
    config = {**h.config, "cave_public_stargazer_enabled": False, "cave_public_treasure_enabled": True}
    state_module.set_miniapp_auto_config(config)
    h.run.return_value = False, "fixture unknown", {"outcome_unknown": True, "daily_exhausted": True}

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    h.run.assert_awaited_once()
    assert not ui._cave_public_background_daily_done
    assert not ui._cave_public_background_state["running"]


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "rescheduled", "auto_disabled"])
def test_late_background_failure_does_not_replace_new_owner_or_schedule(background, change):
    h = background

    async def request(*_args, **_kwargs):
        change_background(h, change)
        return False, "fixture failed", {}

    h.run.side_effect = request

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    assert not ui._cave_public_background_retry_at
    assert not ui._cave_public_background_state["running"]
    if change == "removed":
        assert not state_module.has_identity(h.identity_id)


def test_background_failure_cannot_shorten_a_newer_retry_deadline(background):
    h = background
    key = ("stargazer", h.identity_id)

    async def request(*_args, **_kwargs):
        ui._cave_public_background_retry_at[key] = h.now[0] + 7200
        return False, "fixture failure", {"retry_after_sec": 60}

    h.run.side_effect = request

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    assert ui._cave_public_background_retry_at[key] == h.now[0] + 7200


def test_old_background_finally_does_not_release_a_replaced_running_slot(background):
    h = background
    replacement = {
        "running": True, "next_run_at": h.now[0] + 600, "last_action": "another-role:treasure",
        "last_result": "replacement running", "circuit_open_until": 0, "circuit_reason": "",
    }

    async def request(*_args, **_kwargs):
        ui._cave_public_background_state.clear()
        ui._cave_public_background_state.update(replacement)
        return True, "old completed", {}

    h.run.side_effect = request

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    assert ui._cave_public_background_state == replacement


def test_queue_creation_failure_closes_coroutine_and_releases_slot(background, monkeypatch):
    h = background
    captured = []

    def fail(coroutine):
        captured.append(coroutine)
        raise RuntimeError("fixture task creation failure")

    monkeypatch.setattr(ui, "_fire_and_forget", fail)
    try:
        with pytest.raises(RuntimeError, match="fixture task creation"):
            asyncio.run(ui._run_cave_public_background_scheduler(h.now[0], h.config))
        assert not ui._cave_public_background_state["running"]
        assert inspect.getcoroutinestate(captured[0]) == inspect.CORO_CLOSED
    finally:
        for coroutine in captured:
            coroutine.close()


@pytest.mark.parametrize("changed_owner", [False, True])
def test_daily_terminal_marker_belongs_to_original_owner_and_game_day(background, changed_owner):
    h = background
    config = {**h.config, "cave_public_stargazer_enabled": False, "cave_public_treasure_enabled": True}
    state_module.set_miniapp_auto_config(config)
    started_day = ui.get_day_key(h.now[0])

    async def request(*_args, **_kwargs):
        h.now[0] += 86400
        if changed_owner:
            change_background(h, "replaced")
        return True, "daily limit", {"daily_exhausted": True}

    h.run.side_effect = request

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    expected = set() if changed_owner else {("treasure", started_day, h.identity_id)}
    assert ui._cave_public_background_daily_done == expected


def test_ordinary_background_completion_retains_spacing_and_retry_behavior(background):
    h = background

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    h.run.assert_awaited_once()
    assert not ui._cave_public_background_state["running"]
    assert ui._cave_public_background_state["last_result"] == "fixture completed"
    assert ui._cave_public_background_state["next_run_at"] == h.now[0] + 20
    assert ui._cave_public_background_retry_at[("stargazer", h.identity_id)] == h.now[0] + 60


def test_frozen_channel_during_maintenance_keeps_public_background_available(background):
    h = background
    state_module.set_identity_enabled(h.identity_id, False)
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [h.identity_id]})
    state_module.set_global_enabled(False)
    state_module.set_global_pause_source("tianzun_maintenance")

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    h.run.assert_awaited_once()


def test_task_cancelled_before_coroutine_start_releases_only_its_slot(background, monkeypatch):
    h = background
    tasks = []

    def schedule(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        task.cancel()
        return task

    monkeypatch.setattr(ui, "_fire_and_forget", schedule)

    async def run():
        result = await ui._run_cave_public_background_scheduler(h.now[0], h.config)
        assert result["started"]
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
        assert tasks[0].cancelled()

    asyncio.run(run())
    h.run.assert_not_awaited()
    assert not ui._cave_public_background_state["running"]
    assert ui._cave_public_background_operation is None
    assert not ui._cave_public_background_retry_at


def test_same_identity_new_operation_cannot_be_released_by_old_completion(background):
    h = background
    replacement = {}

    async def request(*_args, **_kwargs):
        next_operation = ui._CavePublicBackgroundOperation.capture(h.identity_id, "stargazer", h.config, h.now[0])
        ui._cave_public_background_operation = next_operation
        ui._cave_public_background_state["last_result"] = "new operation running"
        replacement.update(copy.deepcopy(ui._cave_public_background_state))
        return True, "old operation completed", {}

    h.run.side_effect = request

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    assert ui._cave_public_background_state == replacement
    assert not ui._cave_public_background_retry_at
    assert ui._cave_public_background_operation.owns_slot()


@pytest.mark.parametrize("changed_owner", [False, True])
def test_cancelled_confirmed_result_preserves_only_current_owner_terminal_marker(background, changed_owner):
    h = background
    config = {**h.config, "cave_public_stargazer_enabled": False, "cave_public_treasure_enabled": True}
    state_module.set_miniapp_auto_config(config)

    async def request(*_args, **_kwargs):
        if changed_owner:
            change_background(h, "replaced")
        raise MiniAppFlowCancelled({"ok": True, "message": "daily limit", "extra": {"daily_exhausted": True}})

    h.run.side_effect = request

    async def run():
        with pytest.raises(MiniAppFlowCancelled):
            await (await queue_background(h))

    asyncio.run(run())
    expected = set() if changed_owner else {("treasure", ui.get_day_key(h.now[0]), h.identity_id)}
    assert ui._cave_public_background_daily_done == expected
    assert not ui._cave_public_background_retry_at
    assert not ui._cave_public_background_state["running"]


@pytest.mark.parametrize("phase", ["init", "start"])
def test_real_ui_loader_rechecks_background_switch_after_await(background, monkeypatch, phase):
    h = background
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", h.real_run)
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(stargazer, "_MINIAPP_RUN_LOCKS", {})
    monkeypatch.setattr(cave, "_capture_store", Mock(return_value=None))

    async def init(*_args, **_kwargs):
        if phase == "init":
            change_background(h, "auto_disabled")
        return "fixture-init"

    async def start(*_args, **_kwargs):
        change_background(h, "auto_disabled")
        return {"ok": True, "data": {"overview": {"player_id": h.identity_id}, "raw": {}}}

    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", AsyncMock(side_effect=init))
    start_mock = AsyncMock(side_effect=start)
    details = AsyncMock()
    flow = AsyncMock()
    monkeypatch.setattr(cave, "run_cave_dwelling_start_production_flow", start_mock)
    monkeypatch.setattr(cave, "run_cave_dwelling_snapshot_production_flow", details)
    monkeypatch.setattr(cave, "run_stargazer_miniapp_production_flow", flow)

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    assert start_mock.await_count == int(phase == "start")
    details.assert_not_awaited()
    flow.assert_not_awaited()
    assert not ui._cave_public_background_retry_at
    assert not ui._cave_public_background_state["running"]


def test_explicit_ui_request_is_independent_of_background_auto_switch(background, monkeypatch):
    h = background
    change_background(h, "auto_disabled")
    runner = AsyncMock(return_value={"ok": True, "message": "manual complete", "extra": {}})
    monkeypatch.setattr(ui, "run_cave_public_stargazer", runner)
    result = asyncio.run(h.real_run(h.identity_id, "stargazer", h.config["cave_public_entry_urls"][0]))
    assert result[0]
    runner.assert_awaited_once()


def test_exception_diagnostic_does_not_expose_request_secret(background):
    h = background
    h.run.side_effect = RuntimeError("SECRET-INIT-DATA")

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    assert "RuntimeError" in ui._cave_public_background_state["last_result"]
    assert "SECRET-INIT-DATA" not in ui._cave_public_background_state["last_result"]
    assert "SECRET-INIT-DATA" not in str(ui.console_log.call_args_list)
    assert ui._cave_public_background_retry_at[("stargazer", h.identity_id)] == h.now[0] + 1800


@pytest.mark.parametrize("action", BACKGROUND_ACTIONS)
@pytest.mark.parametrize("disable", [False, True])
def test_every_background_action_honors_its_own_auto_switch(background, action, disable):
    h = background
    config = {
        **h.config,
        **{flag: False for flag in ui._CAVE_PUBLIC_BACKGROUND_ACTION_FLAGS.values()},
        ui._CAVE_PUBLIC_BACKGROUND_ACTION_FLAGS[action]: True,
        "cave_public_fishing_identity_ids": [h.identity_id],
        "cave_public_tianti_status_identity_ids": [h.identity_id],
    }
    for key in ui._CAVE_PUBLIC_BACKGROUND_MODULE_KEYS.get(action, ()):
        h.identity[key] = True
    state_module.set_miniapp_auto_config(config)

    async def run():
        worker = await queue_background(h)
        if disable:
            state_module.set_miniapp_auto_config({**config, ui._CAVE_PUBLIC_BACKGROUND_ACTION_FLAGS[action]: False})
        await worker

    asyncio.run(run())
    assert h.run.await_count == int(not disable)
    assert not ui._cave_public_background_state["running"]
    if disable:
        assert not ui._cave_public_background_retry_at


@pytest.mark.parametrize("action,selection", [
    ("fishing", "cave_public_fishing_identity_ids"),
    ("tianti_status", "cave_public_tianti_status_identity_ids"),
])
def test_deselected_identity_does_not_start_queued_action(background, action, selection):
    h = background
    config = {**h.config, "cave_public_stargazer_enabled": False,
              ui._CAVE_PUBLIC_BACKGROUND_ACTION_FLAGS[action]: True, selection: [h.identity_id]}
    state_module.set_miniapp_auto_config(config)

    async def run():
        worker = await queue_background(h)
        state_module.set_miniapp_auto_config({**config, selection: []})
        await worker

    asyncio.run(run())
    h.run.assert_not_awaited()
    assert not ui._cave_public_background_retry_at


def test_delayed_deep_settlement_cannot_run_after_manual_new_retreat(background):
    h = background
    h.identity.update(deep_retreat_enabled=True, deep_retreat_phase="summary_due", next_deep_retreat_time=0)
    config = {**h.config, "cave_public_stargazer_enabled": False, "cave_public_deep_status_enabled": True}
    state_module.set_miniapp_auto_config(config)

    async def run():
        worker = await queue_background(h)
        assert ui._cave_public_background_operation.action == "deep_settle"
        h.identity.update(deep_retreat_phase="running", next_deep_retreat_time=h.now[0] + 86400)
        await worker

    asyncio.run(run())
    h.run.assert_not_awaited()
    assert not ui._cave_public_background_retry_at


def test_unrelated_config_save_does_not_cancel_admitted_operation(background):
    h = background

    async def run():
        worker = await queue_background(h)
        state_module.set_miniapp_auto_config({**h.config, "trial_daily_enabled": True})
        await worker

    asyncio.run(run())
    h.run.assert_awaited_once()


def test_shared_server_limit_is_kept_after_owner_invalidation(background):
    h = background

    async def request(*_args, **_kwargs):
        change_background(h, "removed")
        return False, "server rate limit", {"shared_rate_limit": True, "shared_retry_after_sec": 900}

    h.run.side_effect = request

    async def run():
        await (await queue_background(h))

    asyncio.run(run())
    assert not ui._cave_public_background_retry_at
    assert ui._cave_public_shared_retry_at() == h.now[0] + 900
    assert ui._cave_public_background_state["next_run_at"] == h.now[0] + 900
    assert not ui._cave_public_background_state["running"]
    assert not state_module.has_identity(h.identity_id)


@pytest.mark.parametrize("lock_kind", ["ui", "identity", "stargazer"])
def test_local_busy_after_queue_is_not_a_thirty_minute_failure(background, monkeypatch, lock_kind):
    h = background
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(stargazer, "_MINIAPP_RUN_LOCKS", {})
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", h.real_run)
    loader = AsyncMock()
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    lock = {
        "ui": ui._cave_public_ui_run_lock,
        "identity": cave._public_entry_lock(h.identity_id),
        "stargazer": stargazer._stargazer_miniapp_run_lock(h.identity_id),
    }[lock_kind]

    async def run():
        worker = await queue_background(h)
        async with lock:
            await worker

    asyncio.run(run())
    loader.assert_not_awaited()
    assert not ui._cave_public_background_retry_at
    assert not ui._cave_public_background_state["running"]
    assert "\u5931\u8d25" not in str(ui.console_log.call_args_list)


def test_pending_tianti_read_reaches_runtime_as_local_wait(background, monkeypatch):
    h = background
    config = {
        **h.config, "cave_public_stargazer_enabled": False, "cave_public_tianti_status_enabled": True,
        "cave_public_tianti_status_identity_ids": [h.identity_id],
    }
    state_module.set_miniapp_auto_config(config)
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", h.real_run)
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(tianti, "_TIANTI_RUN_LOCKS", {})
    loader = AsyncMock()
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)

    async def run():
        worker = await queue_background(h)
        h.identity["pending_tasks"] = {(-10011, 72): {"cmd": tianti.CMD_TIANTI_WENXIN, "time": h.now[0]}}
        await worker

    asyncio.run(run())
    loader.assert_not_awaited()
    assert state_module.get_miniapp_auto_config() == config
    assert not ui._cave_public_background_retry_at
    assert not ui._cave_public_background_state["running"]
    assert ui._cave_public_background_state["next_run_at"] == h.now[0] + config["cave_public_delay_sec"]
    assert "\u7b49\u5f85" in str(ui.console_log.call_args_list)
    assert "\u5931\u8d25" not in str(ui.console_log.call_args_list)


def test_new_shared_hold_while_queued_keeps_no_local_failure_retry(background):
    h = background

    async def run():
        worker = await queue_background(h)
        ui._remember_cave_public_shared_limit({"retry_after_sec": 900}, h.now[0])
        await worker

    asyncio.run(run())
    h.run.assert_not_awaited()
    assert not ui._cave_public_background_retry_at
    assert ui._cave_public_background_state["next_run_at"] == h.now[0] + 900
