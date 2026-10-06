import asyncio
import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, state, ui


CONTEXT = {"wave_key": "wave1", "day_key": "2026-10-06"}
PREFIX = "trial_daily_wave1_last_"
PAYLOAD = {"public_entry_url": "https://t.me/fixture_bot?startapp=df_fixture"}


@pytest.fixture
def admission(monkeypatch):
    monkeypatch.setattr(state, "_meta_state", deepcopy(state.GLOBAL_STATE_DEFAULTS))
    monkeypatch.setattr(ui, "_cave_public_batch_state", {"running": False})
    monkeypatch.setattr(ui, "_cave_public_shared_hold", lambda *_: {})
    monkeypatch.setattr(ui, "_cave_public_background_state", {"running": False})
    monkeypatch.setattr(ui, "_cave_public_ui_run_lock", asyncio.Lock())
    monkeypatch.setattr(ui, "get_identity_state", lambda _: {})
    monkeypatch.setattr(ui, "get_identity_display_name", str)
    monkeypatch.setattr(ui.trial_operations, "archive_cross_day_unknown", Mock(return_value={}))
    monkeypatch.setattr(ui, "_normalize_cave_public_batch_identity_ids", lambda _: [1001, 1002])
    monkeypatch.setattr(ui, "_normalize_cave_public_batch_actions", lambda _: ["trial"])
    monkeypatch.setattr(ui, "_cave_public_batch_identity_ids_for_action", lambda _, ids: ids)
    monkeypatch.setattr(ui, "console_log", Mock())
    action, notify = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", action)
    monkeypatch.setattr(ui, "send_audit_log", notify)
    saved, spawned = [], []

    def save():
        saved.append(deepcopy(state.get_miniapp_auto_config()))
        return True

    def spawn(coro):
        spawned.append(deepcopy(state.get_miniapp_auto_config()))
        coro.close()

    persist, launch = Mock(side_effect=save), Mock(side_effect=spawn)
    monkeypatch.setattr(ui, "save_state", persist)
    monkeypatch.setattr(ui, "_fire_and_forget", launch)
    return SimpleNamespace(persist=persist, launch=launch, saved=saved, spawned=spawned,
                           action=action, notify=notify)


@pytest.mark.parametrize("storage", [False, None, 1, "ok", OSError("fixture-private-detail")])
def test_unconfirmed_start_save_cannot_launch_or_consume_prior_progress(admission, storage):
    before = ui.normalize_miniapp_auto_config()
    before.update({
        PREFIX + "status": "retry_pending", PREFIX + "batch_id": "resume-fixture",
        PREFIX + "progress_day": CONTEXT["day_key"], PREFIX + "cursor": 1,
        PREFIX + "completed": 1, PREFIX + "succeeded": 1,
        PREFIX + "steps": [{"identity_id": 1001, "action": "trial"},
                           {"identity_id": 1002, "action": "trial"}],
        PREFIX + "outcomes": {"trial": {"gains": {"trace": 7}}},
    })
    state.set_miniapp_auto_config(deepcopy(before))
    admission.persist.side_effect = storage if isinstance(storage, Exception) else None
    admission.persist.return_value = storage
    ok, message, extra = asyncio.run(ui.ui_start_cave_public_entry_batch(PAYLOAD, trial_daily_context=CONTEXT))
    assert not ok
    assert extra["reason"] == "trial_start_save_failed"
    assert "fixture-private-detail" not in message
    assert not ui._cave_public_batch_state["running"]
    assert ui.normalize_miniapp_auto_config() == before
    admission.launch.assert_not_called()
    admission.action.assert_not_awaited()
    admission.notify.assert_not_awaited()
    admission.persist.assert_called_once()


def test_start_snapshot_is_acknowledged_before_worker_creation(admission):
    ok, _, extra = asyncio.run(ui.ui_start_cave_public_entry_batch(PAYLOAD, trial_daily_context=CONTEXT))
    assert ok
    assert admission.spawned[0] == admission.saved[0]
    snapshot = admission.spawned[0]
    assert snapshot[PREFIX + "status"] == "running"
    assert snapshot[PREFIX + "batch_id"] == extra["batch_id"]
    assert snapshot[PREFIX + "steps"] == extra["batch_steps"]
    assert not snapshot[PREFIX + "run_day"]
    admission.persist.assert_called_once()
    admission.action.assert_not_awaited()


def test_standalone_batch_does_not_write_daily_checkpoint(admission):
    ok, _, _ = asyncio.run(ui.ui_start_cave_public_entry_batch(PAYLOAD))
    assert ok
    admission.persist.assert_not_called()


def test_creation_failure_retains_retryable_start_prefix(admission):
    def fail(coro):
        assert state.get_miniapp_auto_config()[PREFIX + "status"] == "running"
        raise RuntimeError("fixture spawn unavailable")

    admission.launch.side_effect = fail
    ok, _, _ = asyncio.run(ui.ui_start_cave_public_entry_batch(PAYLOAD, trial_daily_context=CONTEXT))
    assert not ok
    assert admission.saved[-1][PREFIX + "status"] == "retry_pending"
    assert admission.saved[-1][PREFIX + "cursor"] == 0
    assert not ui._cave_public_batch_state["running"]
    admission.action.assert_not_awaited()


def test_scheduler_does_not_overwrite_start_owners_snapshot(admission, monkeypatch):
    from datetime import datetime

    now = datetime(2026, 10, 6, 1, 30, tzinfo=ui.TZ_LOCAL).timestamp()
    config = ui.normalize_miniapp_auto_config()
    config.update({"trial_daily_enabled": True, "trial_daily_scheduler_confirmed": True,
                   "cave_public_entry_urls": [PAYLOAD["public_entry_url"]],
                   "cave_public_trial_enabled": True})
    state.set_miniapp_auto_config(config)
    monkeypatch.setattr(ui, "get_global_enabled", lambda: True)
    monkeypatch.setattr(ui, "get_cave_public_entry_gate", lambda *_, **__: {})
    monkeypatch.setattr(ui, "_run_tree_miniapp_daily_scheduler", AsyncMock(return_value={"started": False}))

    async def start(*args, **kwargs):
        ui._persist_trial_daily_batch_state(CONTEXT, batch_id="already-completed", status="completed",
                                            result="fixture complete", completed=True, require_save_ack=True)
        return True, "ok", {"batch_id": "already-completed"}

    monkeypatch.setattr(ui, "ui_start_cave_public_entry_batch", start)
    assert asyncio.run(ui.run_miniapp_daily_scheduler(now))["started"]
    assert state.get_miniapp_auto_config()[PREFIX + "status"] == "completed"
    admission.persist.assert_called_once()


def test_failed_start_keeps_unrelated_configuration_changes(admission):
    def fail():
        config = state.get_miniapp_auto_config()
        config["cave_public_fishing_enabled"] = True
        return False

    admission.persist.side_effect = fail
    ok, _, _ = asyncio.run(ui.ui_start_cave_public_entry_batch(PAYLOAD, trial_daily_context=CONTEXT))
    assert not ok
    assert state.get_miniapp_auto_config()["cave_public_fishing_enabled"]
    admission.launch.assert_not_called()


def test_failed_storage_and_config_read_cannot_leave_claim_running(admission, monkeypatch):
    def fail():
        monkeypatch.setattr(ui, "normalize_miniapp_auto_config", Mock(side_effect=ValueError("fixture-private-config")))
        return False

    admission.persist.side_effect = fail
    ok, message, _ = asyncio.run(ui.ui_start_cave_public_entry_batch(PAYLOAD, trial_daily_context=CONTEXT))
    assert not ok
    assert "fixture-private-config" not in message
    assert not ui._cave_public_batch_state["running"]
    admission.launch.assert_not_called()


@pytest.mark.parametrize("saved", [False, True])
def test_replaced_claim_is_not_launched_or_cleared(admission, monkeypatch, saved):
    replacement = {"running": True, "batch_id": "replacement", "current": "new work"}

    def save():
        monkeypatch.setattr(ui, "_cave_public_batch_state", replacement)
        return saved

    admission.persist.side_effect = save
    ok, _, extra = asyncio.run(ui.ui_start_cave_public_entry_batch(PAYLOAD, trial_daily_context=CONTEXT))
    assert not ok and extra["reason"] == "trial_start_superseded"
    assert ui._cave_public_batch_state == replacement
    admission.launch.assert_not_called()


def test_resumed_admission_preserves_prefix_until_actual_worker_finishes(admission):
    config = ui.normalize_miniapp_auto_config()
    outcomes = {}
    ui._record_cave_public_batch_outcome(outcomes, "trial", True, {"settled_count": 1, "gains": {"trace": 7}})
    config.update({
        PREFIX + "status": "retry_pending", PREFIX + "batch_id": "resume-fixture",
        PREFIX + "progress_day": CONTEXT["day_key"], PREFIX + "cursor": 1,
        PREFIX + "completed": 1, PREFIX + "succeeded": 1,
        PREFIX + "steps": [{"identity_id": 1001, "action": "trial"},
                           {"identity_id": 1002, "action": "trial"}],
        PREFIX + "outcomes": outcomes,
    })
    state.set_miniapp_auto_config(config)
    tasks = []

    def spawn(coro):
        tasks.append(asyncio.create_task(coro))
        return tasks[-1]

    admission.launch.side_effect = spawn
    admission.action.return_value = (True, "fixture done", {"settled_count": 1, "gains": {"trace": 3}})

    async def run():
        ok, _, _ = await ui.ui_start_cave_public_entry_batch(PAYLOAD, trial_daily_context=CONTEXT)
        assert ok
        assert admission.saved[0][PREFIX + "cursor"] == 1
        assert admission.saved[0][PREFIX + "outcomes"]["trial"]["gains"] == {"trace": 7}
        await tasks[0]

    asyncio.run(run())
    admission.action.assert_awaited_once()
    assert admission.action.await_args.args[0] == 1002
    assert admission.saved[-1][PREFIX + "status"] == "completed"
    assert admission.saved[-1][PREFIX + "outcomes"]["trial"]["gains"] == {"trace": 10}


@pytest.mark.parametrize("commit_start", [False, True])
def test_start_checkpoint_uses_real_sqlite_commit_before_spawn(admission, tmp_path, commit_start):
    db_path = tmp_path / "admission.db"
    before = ui.normalize_miniapp_auto_config()
    before.update({
        PREFIX + "status": "retry_pending", PREFIX + "batch_id": "sqlite-resume",
        PREFIX + "progress_day": CONTEXT["day_key"], PREFIX + "cursor": 1,
        PREFIX + "completed": 1, PREFIX + "succeeded": 1,
        PREFIX + "steps": [{"identity_id": 1001, "action": "trial"},
                           {"identity_id": 1002, "action": "trial"}],
    })
    state.set_miniapp_auto_config(before)
    observed = []

    def read():
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as reader:
            return json.loads(reader.execute("SELECT value FROM meta WHERE key='miniapp_auto_config'").fetchone()[0])

    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        persistence._save_meta_state(conn, keys=["miniapp_auto_config"])
        conn.commit()

        def save():
            persistence._save_meta_state(conn, keys=["miniapp_auto_config"])
            if commit_start:
                conn.commit()
            else:
                conn.rollback()
            return commit_start

        def spawn(coro):
            observed.append(read())
            coro.close()

        admission.persist.side_effect = save
        admission.launch.side_effect = spawn
        ok, _, _ = asyncio.run(ui.ui_start_cave_public_entry_batch(PAYLOAD, trial_daily_context=CONTEXT))
        assert ok is commit_start
        stored = read()
        assert stored[PREFIX + "status"] == ("running" if commit_start else "retry_pending")
        assert stored[PREFIX + "cursor"] == 1
        assert stored[PREFIX + "batch_id"] == "sqlite-resume"
        if commit_start:
            assert observed == [stored]
        else:
            admission.launch.assert_not_called()
            assert ui.normalize_miniapp_auto_config() == before
        admission.action.assert_not_awaited()


def test_unstarted_cancel_prefix_survives_sqlite_reload(admission, tmp_path):
    db_path = tmp_path / "cancel-start.db"
    config = ui.normalize_miniapp_auto_config()
    config.update({
        PREFIX + "status": "retry_pending", PREFIX + "batch_id": "sqlite-cancel",
        PREFIX + "progress_day": CONTEXT["day_key"], PREFIX + "cursor": 1,
        PREFIX + "completed": 1, PREFIX + "succeeded": 1,
        PREFIX + "steps": [{"identity_id": 1001, "action": "trial"},
                           {"identity_id": 1002, "action": "trial"}],
    })
    state.set_miniapp_auto_config(config)
    tasks = []

    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

        def save():
            persistence._save_meta_state(conn, keys=["miniapp_auto_config"])
            conn.commit()
            return True

        def spawn(coro):
            task = asyncio.create_task(coro)
            task.cancel()
            tasks.append(task)
            return task

        admission.persist.side_effect = save
        admission.launch.side_effect = spawn

        async def run():
            ok, _, _ = await ui.ui_start_cave_public_entry_batch(PAYLOAD, trial_daily_context=CONTEXT)
            assert ok
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            await asyncio.sleep(0)

        asyncio.run(run())

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as reader:
        stored = json.loads(reader.execute("SELECT value FROM meta WHERE key='miniapp_auto_config'").fetchone()[0])
    state.set_miniapp_auto_config(stored)
    resumed = ui._trial_daily_batch_resume_state(CONTEXT)
    assert resumed["cursor"] == resumed["succeeded"] == 1
    assert resumed["steps"] == [(1001, "trial"), (1002, "trial")]
    assert not ui._cave_public_batch_state["running"]
    admission.action.assert_not_awaited()
    admission.notify.assert_not_awaited()
