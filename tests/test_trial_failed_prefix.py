import asyncio
import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model import persistence, ui


CONTEXT = {"wave_key": "wave1", "day_key": "2026-10-06"}
PREFIX = "trial_daily_wave1_last_"


@pytest.fixture
def batch(monkeypatch):
    monkeypatch.setattr(state_module, "_meta_state", deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    monkeypatch.setattr(ui, "_cave_public_batch_state", deepcopy(ui._cave_public_batch_state))
    monkeypatch.setattr(ui, "get_identity_display_name", str)
    monkeypatch.setattr(ui, "get_identity_state", lambda _: {})
    monkeypatch.setattr(ui, "console_log", Mock())
    monkeypatch.setattr(ui.trial_operations, "archive_cross_day_unknown", Mock(return_value={}))
    saves = []

    def save():
        saves.append(json.loads(json.dumps(state_module.get_miniapp_auto_config())))
        return True

    monkeypatch.setattr(ui, "save_state", Mock(side_effect=save))
    action = AsyncMock(return_value=(True, "fixture complete", {}))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", action)
    monkeypatch.setattr(ui, "send_audit_log", notify)
    return SimpleNamespace(saves=saves, action=action, notify=notify)


def run_batch(**kwargs):
    return ui._run_cave_public_entry_batch(
        "fixture-prefix", "", [1001, 1002, 1003], ["trial"], 0,
        trial_daily_context=CONTEXT, **kwargs,
    )


@pytest.mark.parametrize("interruption", ["pause", "rate_limit", "exception"])
def test_failure_before_interruption_survives_reload_and_stays_retryable(batch, interruption):
    interrupted = {
        "pause": (False, "\u5168\u5c40\u6682\u505c\u6765\u6e90\u4e0d\u5141\u8bb8\u6d1e\u5e9c\u516c\u5171\u5165\u53e3 MiniApp HTTP", {}),
        "rate_limit": (False, "fixture shared limit", {"shared_rate_limit": True}),
        "exception": OSError("fixture unavailable"),
    }[interruption]
    batch.action.side_effect = [
        (True, "fixture complete", {}), (False, "fixture failed", {}), interrupted,
    ]
    asyncio.run(run_batch())
    saved = batch.saves[-1]
    assert saved[PREFIX + "status"] == "retry_pending"
    assert saved[PREFIX + "failed"] == 1
    assert saved.get(PREFIX + "failed_steps") == [{"identity_id": 1002, "action": "trial"}]
    state_module.set_miniapp_auto_config(saved)
    resumed = ui._trial_daily_batch_resume_state(CONTEXT)
    assert resumed["failed_steps"] == [(1002, "trial")]
    batch.action.reset_mock()
    batch.action.side_effect = None
    asyncio.run(run_batch(
        resume_cursor=resumed["cursor"], initial_completed=resumed["completed"],
        initial_succeeded=resumed["succeeded"], initial_failed=resumed["failed"],
        initial_failed_steps=resumed["failed_steps"], initial_outcomes=resumed["outcomes"],
        steps_override=resumed["steps"],
    ))
    batch.action.assert_awaited_once_with(1003, "trial", "")
    final = batch.saves[-1]
    assert final[PREFIX + "status"] == "retry_pending"
    assert not final[PREFIX + "run_day"]
    assert final[PREFIX + "steps"] == [{"identity_id": 1002, "action": "trial"}]
    assert final[PREFIX + "failed_steps"] == []
    assert final[PREFIX + "retry_at"] > final[PREFIX + "run_at"]
    resumed = ui._trial_daily_batch_resume_state(CONTEXT)
    batch.action.reset_mock()
    asyncio.run(run_batch(steps_override=resumed["steps"]))
    batch.action.assert_awaited_once_with(1002, "trial", "")
    assert batch.saves[-1][PREFIX + "status"] == "completed"
    assert batch.saves[-1][PREFIX + "failed_steps"] == []
    assert ui._trial_daily_batch_resume_state(CONTEXT) == {}


def test_old_failed_count_without_identity_evidence_cannot_resume(batch):
    ui._persist_trial_daily_batch_state(
        CONTEXT, batch_id="fixture-legacy", status="retry_pending", result="fixture paused",
        cursor=2, completed_count=2, succeeded=1, failed=1,
        steps=[(1001, "trial"), (1002, "trial"), (1003, "trial")], retry_at=1,
    )
    config = ui.normalize_miniapp_auto_config()
    assert ui._trial_daily_wave_recovery_hold(config, "wave1", CONTEXT["day_key"])
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    batch.action.assert_not_awaited()


@pytest.mark.parametrize("failure", ["HTTP 503", "server_error", "connect timeout"])
def test_upstream_failure_at_cursor_is_retained_for_retry(batch, failure):
    batch.action.side_effect = [(True, "fixture complete", {}), (False, failure, {})]
    asyncio.run(run_batch())
    config = batch.saves[-1]
    assert config[PREFIX + "cursor"] == 2
    assert config[PREFIX + "failed_steps"] == [{"identity_id": 1002, "action": "trial"}]
    assert not ui._trial_daily_wave_recovery_hold(config, "wave1", CONTEXT["day_key"])


def test_repeated_entry_failure_keeps_both_failed_identities(batch, monkeypatch):
    monkeypatch.setattr(ui, "_open_cave_public_upstream_circuit", lambda *_args: 9999999999)
    batch.action.return_value = False, "\u5916\u5e9c\u8bd5\u70bc\u5165\u53e3\u4e0d\u53ef\u7528", {}
    asyncio.run(run_batch())
    config = batch.saves[-1]
    assert config[PREFIX + "cursor"] == 2
    assert config[PREFIX + "failed_steps"] == [
        {"identity_id": 1001, "action": "trial"}, {"identity_id": 1002, "action": "trial"},
    ]
    assert config[PREFIX + "retry_at"] == 9999999999
    assert batch.action.await_count == 2


@pytest.mark.parametrize("interruption", ["rate_limit", "exception", "upstream"])
def test_unknown_failure_reason_survives_early_exit(batch, interruption):
    batch.action.side_effect = [
        (False, "fixture pending", {"status": "operation_pending", "error": "finish_outcome_unknown"}),
        {
            "rate_limit": (False, "fixture shared limit", {"shared_rate_limit": True}),
            "exception": OSError("fixture unavailable"),
            "upstream": (False, "HTTP 503", {}),
        }[interruption],
    ]
    asyncio.run(run_batch())
    config = batch.saves[-1]
    assert config[PREFIX + "retry_reason"] == "finish_outcome_unknown"
    assert ui._trial_daily_wave_recovery_hold(config, "wave1", CONTEXT["day_key"])
    assert ui._trial_daily_retry_hold(CONTEXT)["recovery_hold"]
    assert not config[PREFIX + "run_day"]


@pytest.mark.parametrize("failed_steps", [
    [], [{"identity_id": 1003, "action": "trial"}],
    [{"identity_id": 9999, "action": "trial"}], [{"identity_id": 1002, "action": "fate_cards"}],
    [{"identity_id": 1002, "action": "trial"}] * 2,
    [None], "invalid",
])
def test_missing_foreign_future_or_corrupt_failure_evidence_holds(batch, failed_steps):
    config = {
        PREFIX + "status": "retry_pending", PREFIX + "progress_day": CONTEXT["day_key"],
        PREFIX + "cursor": 2, PREFIX + "failed": 1,
        PREFIX + "steps": [{"identity_id": identity, "action": "trial"} for identity in (1001, 1002, 1003)],
        PREFIX + "failed_steps": failed_steps,
    }
    config = ui.normalize_miniapp_auto_config(config)
    assert ui._trial_daily_wave_recovery_hold(config, "wave1", CONTEXT["day_key"])
    assert not ui._trial_daily_wave_recovery_hold(config, "wave2", CONTEXT["day_key"])
    assert not ui._trial_daily_wave_recovery_hold(config, "wave1", "2026-10-07")


def test_legacy_direct_resume_neither_dispatches_nor_claims_completion(batch):
    asyncio.run(run_batch(resume_cursor=2, initial_completed=2, initial_succeeded=1, initial_failed=1))
    batch.action.assert_not_awaited()
    config = batch.saves[-1]
    assert config[PREFIX + "status"] == "retry_pending"
    assert config[PREFIX + "retry_reason"] == "failed_prefix_missing"
    assert config[PREFIX + "failed"] == 1
    assert not config[PREFIX + "run_day"]
    assert not ui._cave_public_batch_state["running"]


def test_failure_prefix_snapshot_does_not_alias_caller_or_resume(batch):
    failures = [(1002, "trial")]
    ui._persist_trial_daily_batch_state(
        CONTEXT, batch_id="fixture", status="retry_pending", result="fixture interrupted",
        cursor=2, completed_count=2, succeeded=1, failed=1, failed_steps=failures,
        steps=[(1001, "trial"), (1002, "trial"), (1003, "trial")], retry_at=1,
    )
    failures.clear()
    ui._trial_daily_batch_resume_state(CONTEXT)["failed_steps"].clear()
    config = ui.normalize_miniapp_auto_config()
    config[PREFIX + "failed_steps"][0]["identity_id"] = 9999
    assert ui._trial_daily_batch_resume_state(CONTEXT)["failed_steps"] == [(1002, "trial")]


def test_ui_admission_passes_failure_prefix_to_worker_and_scheduler(batch, monkeypatch):
    ui._persist_trial_daily_batch_state(
        CONTEXT, batch_id="fixture", status="retry_pending", result="fixture interrupted",
        cursor=2, completed_count=2, succeeded=1, failed=1, failed_steps=[(1002, "trial")],
        steps=[(1001, "trial"), (1002, "trial"), (1003, "trial")], retry_at=1,
    )
    monkeypatch.setattr(ui, "_cave_public_shared_hold", lambda *_args: {})
    monkeypatch.setattr(ui, "_cave_public_background_state", {"running": False})
    monkeypatch.setattr(ui, "_cave_public_ui_run_lock", asyncio.Lock())
    monkeypatch.setattr(ui, "_normalize_cave_public_batch_identity_ids", lambda _: [1001, 1002, 1003])
    monkeypatch.setattr(ui, "_normalize_cave_public_batch_actions", lambda _: ["trial"])
    monkeypatch.setattr(ui, "_cave_public_batch_identity_ids_for_action", lambda _action, ids: ids)
    task_args = []

    def capture(coro):
        task_args.append(dict(coro.cr_frame.f_locals))
        coro.close()

    monkeypatch.setattr(ui, "_fire_and_forget", capture)
    ok, _, extra = asyncio.run(ui.ui_start_cave_public_entry_batch(
        {"public_entry_url": "https://t.me/fixture_bot?startapp=df_fixture"}, trial_daily_context=CONTEXT,
    ))
    assert ok
    assert extra["failed_steps"] == [(1002, "trial")]
    assert task_args[0]["initial_failed_steps"] == [(1002, "trial")]
    assert task_args[0]["initial_failed"] == 1
    batch.action.assert_not_awaited()


def test_failure_prefix_uses_existing_sqlite_meta_codec(batch, tmp_path, monkeypatch):
    db_path = tmp_path / "trial-prefix.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

        def save():
            persistence._save_meta_state(conn, keys=["miniapp_auto_config"])
            conn.commit()

        monkeypatch.setattr(ui, "save_state", save)
        batch.action.side_effect = [
            (True, "fixture complete", {}), (False, "fixture failed", {}),
            (False, "fixture shared limit", {"shared_rate_limit": True}),
        ]
        asyncio.run(run_batch())
    state_module.set_miniapp_auto_config({})
    with sqlite3.connect(db_path) as conn:
        raw = conn.execute("SELECT value FROM meta WHERE key='miniapp_auto_config'").fetchone()[0]
    persistence._META_STATE_CODEC["miniapp_auto_config"][2](raw)
    resumed = ui._trial_daily_batch_resume_state(CONTEXT)
    assert resumed["cursor"] == 2
    assert resumed["failed"] == 1
    assert resumed["failed_steps"] == [(1002, "trial")]


def test_public_config_save_cannot_clear_internal_failure_prefix(batch):
    ui._persist_trial_daily_batch_state(
        CONTEXT, batch_id="fixture", status="retry_pending", result="fixture interrupted",
        cursor=2, completed_count=2, succeeded=1, failed=1, failed_steps=[(1002, "trial")],
        steps=[(1001, "trial"), (1002, "trial"), (1003, "trial")], retry_at=1,
    )
    ok, _ = asyncio.run(ui.ui_set_cave_public_config({
        "delay_sec": 25, PREFIX + "failed_steps": [],
    }))
    assert ok
    assert ui._trial_daily_batch_resume_state(CONTEXT)["failed_steps"] == [(1002, "trial")]
