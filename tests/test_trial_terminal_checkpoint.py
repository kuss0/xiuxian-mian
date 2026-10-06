import asyncio
import ast
import json
import sqlite3
import subprocess
from copy import deepcopy
from unittest.mock import Mock

import pytest

from model import persistence, state, ui
from test_trial_batch_cancellation import CONTEXT, GAIN, PREFIX, run_batch
from test_trial_batch_cancellation import batch as batch


@pytest.mark.parametrize("storage", [False, None, 1, "ok", OSError("private-storage-detail")])
@pytest.mark.parametrize("ending", ["completed", "shared_pause", "failed_steps"])
def test_unconfirmed_terminal_save_preserves_prefix_and_holds(batch, storage, ending):
    if ending == "shared_pause":
        batch.action.side_effect = [(True, "done", deepcopy(GAIN)),
                                    (False, "shared limit", {"shared_rate_limit": True})]
    elif ending == "failed_steps":
        batch.action.side_effect = [(True, "done", deepcopy(GAIN)),
                                    (False, "fixture failure", {}),
                                    (True, "done", deepcopy(GAIN))]
    batch.persist.side_effect = storage if isinstance(storage, Exception) else None
    batch.persist.return_value = storage
    asyncio.run(run_batch())
    config = ui.normalize_miniapp_auto_config()
    assert config[PREFIX + "status"] == "retry_pending"
    assert not config[PREFIX + "run_day"]
    assert config[PREFIX + "retry_reason"] == "batch_checkpoint_save_outcome_unknown"
    assert config[PREFIX + "outcomes"]["trial"]["gains"]["trace"] == (
        7 if ending == "shared_pause" else 14 if ending == "failed_steps" else 21
    )
    assert config[PREFIX + "cursor"] == (1 if ending == "shared_pause" else 3)
    assert len(config[PREFIX + "steps"]) == 3
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    assert not ui._cave_public_batch_state["running"]
    assert "private-storage-detail" not in ui._cave_public_batch_state["last_result"]
    batch.persist.assert_called_once()
    assert all("private-storage-detail" not in call.args[0]
               for call in batch.notify.await_args_list)


def test_confirmed_completion_still_retains_rewards_and_completes(batch):
    asyncio.run(run_batch())
    config = batch.saved[-1]
    assert config[PREFIX + "status"] == "completed"
    assert config[PREFIX + "run_day"] == CONTEXT["day_key"]
    assert config[PREFIX + "outcomes"]["trial"]["gains"] == {"trace": 21}
    assert not ui._trial_daily_retry_hold(CONTEXT)
    batch.persist.assert_called_once()


def test_unconfirmed_checkpoint_blocks_admission_without_more_game_calls(batch, monkeypatch):
    batch.persist.side_effect = None
    batch.persist.return_value = False
    asyncio.run(run_batch())
    monkeypatch.setattr(ui, "_cave_public_shared_hold", lambda *_: {})
    monkeypatch.setattr(ui, "_cave_public_background_state", {"running": False})
    monkeypatch.setattr(ui, "_cave_public_ui_run_lock", asyncio.Lock())
    spawn = Mock()
    monkeypatch.setattr(ui, "_fire_and_forget", spawn)
    batch.action.reset_mock()
    ok, _, extra = asyncio.run(ui.ui_start_cave_public_entry_batch({}, trial_daily_context=CONTEXT))
    assert not ok and extra["recovery_hold"]
    spawn.assert_not_called()
    batch.action.assert_not_awaited()


def test_cancelled_checkpoint_notice_does_not_overwrite_hold(batch):
    cancel = asyncio.CancelledError()
    batch.persist.side_effect = None
    batch.persist.return_value = False
    batch.notify.side_effect = cancel
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(run_batch())
    assert caught.value is cancel
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    batch.persist.assert_called_once()


def test_standalone_batch_does_not_need_daily_checkpoint(batch):
    batch.persist.side_effect = OSError("must not save a daily checkpoint")
    asyncio.run(ui._run_cave_public_entry_batch("standalone", "", [1001], ["trial"], 0))
    batch.persist.assert_not_called()
    batch.action.assert_awaited_once()


def test_unrelated_config_change_survives_checkpoint_failure(batch):
    def fail():
        state.get_miniapp_auto_config()["cave_public_fishing_enabled"] = True
        return False

    batch.persist.side_effect = fail
    asyncio.run(run_batch())
    assert state.get_miniapp_auto_config()["cave_public_fishing_enabled"]
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]


def test_configuration_read_failure_still_retains_memory_prefix(batch, monkeypatch):
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", Mock(side_effect=ValueError("private-config")))
    asyncio.run(run_batch())
    held = ui._cave_public_batch_state["checkpoint_hold"]
    assert held["snapshot"][PREFIX + "outcomes"]["trial"]["gains"] == {"trace": 21}
    assert held["snapshot"][PREFIX + "cursor"] == 3
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    batch.persist.assert_not_called()
    assert "private-config" not in batch.notify.await_args.args[0]


@pytest.mark.parametrize("point", ["action", "save", "notification"])
def test_replacement_batch_is_not_changed_by_late_work(batch, monkeypatch, point):
    replacement = {"running": True, "batch_id": "replacement", "started_at": 123, "current": "new work"}

    def replace():
        monkeypatch.setattr(ui, "_cave_public_batch_state", replacement)

    if point == "action":
        async def action(*args):
            replace()
            return True, "done", deepcopy(GAIN)
        batch.action.side_effect = action
    elif point == "save":
        batch.persist.side_effect = lambda: replace() or False
    else:
        batch.action.return_value = (False, "fixture failure", {})
        async def notify(*args, **kwargs):
            replace()
            return True
        batch.notify.side_effect = notify
    before = deepcopy(replacement)
    asyncio.run(run_batch())
    assert ui._cave_public_batch_state == before
    if point != "save":
        batch.persist.assert_not_called()


def test_hold_is_compatible_with_previous_reader_and_survives_later_save(batch, tmp_path):
    batch.persist.side_effect = None
    batch.persist.return_value = False
    asyncio.run(run_batch())
    candidate = deepcopy(state.get_miniapp_auto_config())
    path = tmp_path / "checkpoint.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        persistence._save_meta_state(conn, keys=["miniapp_auto_config"])
        conn.commit()
    with sqlite3.connect(path) as conn:
        restored = json.loads(conn.execute("SELECT value FROM meta WHERE key='miniapp_auto_config'").fetchone()[0])
    assert restored == candidate
    source = subprocess.check_output(["git", "show", "ac92f563:model/ui.py"], text=True)
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == "_trial_daily_wave_recovery_hold")
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "previous-reader", "exec"), namespace)
    assert namespace["_trial_daily_wave_recovery_hold"](restored, "wave1", CONTEXT["day_key"])
    assert ui._trial_daily_wave_recovery_hold(restored, "wave1", CONTEXT["day_key"])


def test_failed_sqlite_commit_does_not_masquerade_as_durable_completion(batch, tmp_path):
    path = tmp_path / "rollback.db"
    baseline = ui.normalize_miniapp_auto_config()
    baseline.update({PREFIX + "status": "running", PREFIX + "batch_id": "fixture-cancel",
                     PREFIX + "progress_day": CONTEXT["day_key"]})
    state.set_miniapp_auto_config(baseline)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        persistence._save_meta_state(conn, keys=["miniapp_auto_config"])
        conn.commit()

        def rollback():
            persistence._save_meta_state(conn, keys=["miniapp_auto_config"])
            conn.rollback()
            return False

        batch.persist.side_effect = rollback
        asyncio.run(run_batch())
    with sqlite3.connect(path) as conn:
        stored = json.loads(conn.execute("SELECT value FROM meta WHERE key='miniapp_auto_config'").fetchone()[0])
    assert stored[PREFIX + "status"] == "running"
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    assert ui.normalize_miniapp_auto_config()[PREFIX + "outcomes"]["trial"]["gains"] == {"trace": 21}
    batch.persist.assert_called_once()


def test_checkpoint_hold_is_scoped_to_its_day_and_wave(batch):
    batch.persist.side_effect = None
    batch.persist.return_value = False
    asyncio.run(run_batch())
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    assert not ui._trial_daily_retry_hold({"wave_key": "wave2", "day_key": CONTEXT["day_key"]})
    assert not ui._trial_daily_retry_hold({"wave_key": "wave1", "day_key": "2026-10-07"})
