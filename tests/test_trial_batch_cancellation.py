import asyncio
import json
import sqlite3
import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model import persistence, ui
from model.features.miniapp_common import MiniAppFlowCancelled, run_miniapp_blocking_flow


CONTEXT = {"wave_key": "wave1", "day_key": "2026-10-06"}
PREFIX = "trial_daily_wave1_last_"
GAIN = {"settled_count": 1, "gains": {"trace": 7}, "rewards": {"material": 1}}


@pytest.fixture
def batch(monkeypatch):
    monkeypatch.setattr(state_module, "_meta_state", deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    monkeypatch.setattr(ui, "_cave_public_batch_state", deepcopy(ui._cave_public_batch_state))
    monkeypatch.setattr(ui, "get_identity_display_name", str)
    monkeypatch.setattr(ui, "get_identity_state", lambda _: {})
    monkeypatch.setattr(ui, "console_log", Mock())
    monkeypatch.setattr(ui.trial_operations, "archive_cross_day_unknown", Mock(return_value={}))
    saved = []

    def save():
        saved.append(json.loads(json.dumps(state_module.get_miniapp_auto_config())))
        return True

    persist = Mock(side_effect=save)
    action = AsyncMock(return_value=(True, "complete", deepcopy(GAIN)))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(ui, "save_state", persist)
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", action)
    monkeypatch.setattr(ui, "send_audit_log", notify)
    return SimpleNamespace(saved=saved, persist=persist, action=action, notify=notify)


def run_batch(**kwargs):
    return ui._run_cave_public_entry_batch(
        "fixture-cancel", "", [1001, 1002, 1003], ["trial"], 0,
        trial_daily_context=CONTEXT, **kwargs,
    )


def assert_prefix(config, *, cursor=1, succeeded=1, failed=0):
    assert config[PREFIX + "cursor"] == cursor
    assert config[PREFIX + "completed"] == cursor
    assert config[PREFIX + "succeeded"] == succeeded
    assert config[PREFIX + "failed"] == failed
    assert config[PREFIX + "status"] == "retry_pending"
    assert not config[PREFIX + "run_day"]
    assert config[PREFIX + "outcomes"]["trial"]["gains"] == {"trace": 7}


@pytest.mark.parametrize("cancel", [
    asyncio.CancelledError(),
    MiniAppFlowCancelled({"ok": True, "extra": GAIN}),
    MiniAppFlowCancelled({"ok": False, "extra": {"outcome_unknown": True}}),
])
def test_cancelled_step_preserves_prefix_and_holds_without_adopting_unowned_result(batch, cancel):
    batch.action.side_effect = [(True, "complete", deepcopy(GAIN)), cancel]
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(run_batch())
    assert caught.value is cancel
    assert not ui._cave_public_batch_state["running"]
    assert ui._cave_public_batch_state["current"] == ""
    assert ui._cave_public_batch_state["finished_at"] > 0
    config = batch.saved[-1]
    assert_prefix(config)
    assert config[PREFIX + "retry_reason"] == "batch_cancelled_outcome_unknown"
    state_module.set_miniapp_auto_config(config)
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    assert batch.action.await_count == 2
    batch.notify.assert_not_awaited()


def test_cancel_between_steps_resumes_only_remaining_identities(batch, monkeypatch):
    cancel = asyncio.CancelledError()
    monkeypatch.setattr(ui.asyncio, "sleep", AsyncMock(side_effect=cancel))
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(run_batch())
    assert caught.value is cancel
    assert not ui._cave_public_batch_state["running"]
    config = batch.saved[-1]
    assert_prefix(config)
    assert not ui._trial_daily_wave_recovery_hold(config, "wave1", CONTEXT["day_key"])
    state_module.set_miniapp_auto_config(config)
    resumed = ui._trial_daily_batch_resume_state(CONTEXT)
    monkeypatch.setattr(ui.asyncio, "sleep", AsyncMock())
    batch.action.reset_mock()
    asyncio.run(run_batch(
        resume_cursor=resumed["cursor"], initial_completed=resumed["completed"],
        initial_succeeded=resumed["succeeded"], initial_failed=resumed["failed"],
        initial_failed_steps=resumed["failed_steps"], initial_outcomes=resumed["outcomes"],
        steps_override=resumed["steps"],
    ))
    assert [call.args[0] for call in batch.action.await_args_list] == [1002, 1003]
    assert batch.saved[-1][PREFIX + "outcomes"]["trial"]["gains"] == {"trace": 21}
    assert batch.saved[-1][PREFIX + "status"] == "completed"


@pytest.mark.parametrize("unknown", [False, True])
def test_cancelled_failure_notification_keeps_failure_prefix_and_prior_unknown(batch, unknown):
    cancel = asyncio.CancelledError()
    batch.action.side_effect = [
        (True, "complete", deepcopy(GAIN)),
        (False, "pending" if unknown else "failed", {
            "status": "operation_pending", "error": "finish_outcome_unknown",
        } if unknown else {}),
    ]
    batch.notify.side_effect = cancel
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(run_batch())
    assert caught.value is cancel
    config = batch.saved[-1]
    assert_prefix(config, cursor=2, failed=1)
    assert config[PREFIX + "failed_steps"] == [{"identity_id": 1002, "action": "trial"}]
    assert ui._trial_daily_wave_recovery_hold(config, "wave1", CONTEXT["day_key"]) is unknown
    assert not ui._cave_public_batch_state["running"]
    assert batch.action.await_count == 2
    assert batch.notify.await_count == 1


def test_cancelled_completion_notification_does_not_reopen_finished_batch(batch):
    cancel = asyncio.CancelledError()
    batch.notify.side_effect = cancel
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(run_batch())
    assert caught.value is cancel
    assert batch.saved[-1][PREFIX + "status"] == "completed"
    assert ui._trial_daily_batch_resume_state(CONTEXT) == {}
    assert not ui._cave_public_batch_state["running"]
    assert batch.action.await_count == 3


@pytest.mark.parametrize("storage", [False, None, 1, "ok", OSError("private storage error")])
def test_checkpoint_failure_keeps_original_cancel_and_conservative_memory_hold(batch, monkeypatch, storage):
    cancel = asyncio.CancelledError()
    monkeypatch.setattr(ui.asyncio, "sleep", AsyncMock(side_effect=cancel))
    batch.persist.side_effect = storage if isinstance(storage, Exception) else None
    batch.persist.return_value = storage
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(run_batch())
    assert caught.value is cancel
    assert not ui._cave_public_batch_state["running"]
    config = ui.normalize_miniapp_auto_config()
    assert_prefix(config)
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    assert "private storage error" not in ui._cave_public_batch_state["last_result"]
    batch.persist.assert_called_once()
    batch.notify.assert_not_awaited()


@pytest.mark.parametrize("replacement_kind", ["id", "time", "object"])
def test_stale_cancel_cannot_clear_replacement_batch(batch, monkeypatch, replacement_kind):
    replacement = {}

    async def action(*args):
        if replacement_kind == "id":
            ui._set_cave_public_batch_state(batch_id="replacement")
        elif replacement_kind == "time":
            ui._set_cave_public_batch_state(started_at=9999999999)
        else:
            monkeypatch.setattr(ui, "_cave_public_batch_state", deepcopy(ui._cave_public_batch_state))
        ui._set_cave_public_batch_state(running=True, current="new work")
        replacement.update(deepcopy(ui._cave_public_batch_state))
        raise asyncio.CancelledError()

    batch.action.side_effect = action
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_batch())
    assert ui._cave_public_batch_state == replacement
    batch.persist.assert_not_called()
    batch.notify.assert_not_awaited()


def test_cancel_waits_for_existing_http_thread_drain_before_releasing_batch(batch):
    entered, release = threading.Event(), threading.Event()

    def flow(operation):
        entered.set()
        assert release.wait(3)
        return {"ok": True, "extra": GAIN}

    async def action(*args):
        return await run_miniapp_blocking_flow(flow)

    batch.action.side_effect = action

    async def scenario():
        task = asyncio.create_task(run_batch())
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            assert ui._cave_public_batch_state["running"]
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled):
                await task

    asyncio.run(scenario())
    assert not ui._cave_public_batch_state["running"]
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    batch.action.assert_awaited_once()
    batch.notify.assert_not_awaited()


def test_cancel_prefix_and_hold_survive_sqlite_reload(batch, tmp_path, monkeypatch):
    db_path = tmp_path / "cancel.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

        def save():
            persistence._save_meta_state(conn, keys=["miniapp_auto_config"])
            conn.commit()
            return True

        monkeypatch.setattr(ui, "save_state", save)
        batch.action.side_effect = [(True, "complete", deepcopy(GAIN)), asyncio.CancelledError()]
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run_batch())
    state_module.set_miniapp_auto_config({})
    with sqlite3.connect(db_path) as conn:
        raw = conn.execute("SELECT value FROM meta WHERE key='miniapp_auto_config'").fetchone()[0]
    persistence._META_STATE_CODEC["miniapp_auto_config"][2](raw)
    assert_prefix(ui.normalize_miniapp_auto_config())
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]


def test_cancelled_batch_hold_rejects_scheduler_admission(batch, monkeypatch):
    batch.action.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_batch())
    monkeypatch.setattr(ui, "_cave_public_shared_hold", lambda *_: {})
    monkeypatch.setattr(ui, "_cave_public_background_state", {"running": False})
    monkeypatch.setattr(ui, "_cave_public_ui_run_lock", asyncio.Lock())
    launch = Mock()
    monkeypatch.setattr(ui, "_fire_and_forget", launch)
    ok, _message, extra = asyncio.run(ui.ui_start_cave_public_entry_batch({}, trial_daily_context=CONTEXT))
    assert not ok
    assert extra["recovery_hold"]
    launch.assert_not_called()


def test_persistence_and_config_diagnostic_failure_cannot_mask_cancellation(batch, monkeypatch):
    cancel = asyncio.CancelledError()

    async def action(*args):
        monkeypatch.setattr(ui, "normalize_miniapp_auto_config", Mock(side_effect=ValueError("invalid config")))
        raise cancel

    batch.action.side_effect = action
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(run_batch())
    assert caught.value is cancel
    assert not ui._cave_public_batch_state["running"]
    assert "invalid config" not in ui._cave_public_batch_state["last_result"]
    batch.notify.assert_not_awaited()


def allow_admission(monkeypatch):
    monkeypatch.setattr(ui, "_cave_public_shared_hold", lambda *_: {})
    monkeypatch.setattr(ui, "_cave_public_background_state", {"running": False})
    monkeypatch.setattr(ui, "_cave_public_ui_run_lock", asyncio.Lock())
    monkeypatch.setattr(ui, "_normalize_cave_public_batch_identity_ids", lambda _: [1001, 1002, 1003])
    monkeypatch.setattr(ui, "_normalize_cave_public_batch_actions", lambda _: ["trial"])
    monkeypatch.setattr(ui, "_cave_public_batch_identity_ids_for_action", lambda _action, ids: ids)


@pytest.mark.parametrize("replace_claim", [False, True])
def test_cancel_before_worker_starts_releases_only_original_claim(batch, monkeypatch, replace_claim):
    allow_admission(monkeypatch)
    tasks = []

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        task.cancel()
        return task

    monkeypatch.setattr(ui, "_fire_and_forget", spawn)

    async def scenario():
        ok, _message, _extra = await ui.ui_start_cave_public_entry_batch({
            "public_entry_url": "https://t.me/fixture_bot?startapp=df_fixture",
        }, trial_daily_context=CONTEXT)
        assert ok
        if replace_claim:
            ui._set_cave_public_batch_state(batch_id="new", current="new work")
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert ui._cave_public_batch_state["running"] is replace_claim
    if replace_claim:
        assert ui._cave_public_batch_state["batch_id"] == "new"
        assert ui._cave_public_batch_state["current"] == "new work"
    else:
        assert ui._cave_public_batch_state["current"] == ""
    batch.action.assert_not_awaited()
    batch.persist.assert_not_called()
    batch.notify.assert_not_awaited()


def test_task_creation_failure_closes_coroutine_and_claim(batch, monkeypatch):
    allow_admission(monkeypatch)
    coroutines = []

    def fail(coro):
        coroutines.append(coro)
        raise RuntimeError("task creation failed")

    monkeypatch.setattr(ui, "_fire_and_forget", fail)
    ok, _, _ = asyncio.run(ui.ui_start_cave_public_entry_batch({
        "public_entry_url": "https://t.me/fixture_bot?startapp=df_fixture",
    }, trial_daily_context=CONTEXT))
    assert not ok
    assert not ui._cave_public_batch_state["running"]
    assert coroutines[0].cr_frame is None
    batch.action.assert_not_awaited()
    batch.notify.assert_not_awaited()


@pytest.mark.parametrize("upstream", [False, True])
def test_cancelled_exit_notification_does_not_rewrite_saved_pause_or_backoff(batch, upstream):
    cancel = asyncio.CancelledError()
    batch.action.side_effect = [
        (True, "complete", deepcopy(GAIN)),
        (False, "HTTP 503" if upstream else "\u5168\u5c40\u6682\u505c", {}),
    ]
    batch.notify.side_effect = [True, cancel] if upstream else cancel
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(run_batch())
    assert caught.value is cancel
    assert not ui._cave_public_batch_state["running"]
    assert len(batch.saved) == 1
    assert state_module.get_miniapp_auto_config() == batch.saved[0]
    assert batch.saved[0][PREFIX + "retry_at"] > 0 if upstream else batch.saved[0][PREFIX + "retry_at"] == 0
    assert batch.action.await_count == 2
