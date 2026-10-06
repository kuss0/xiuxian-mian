import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model import ui


DAY = "2026-10-06"
CONTEXT = {"wave_key": "wave1", "day_key": DAY}
PREFIX = "trial_daily_wave1_last_"
OUTCOMES = {"trial": {
    "attempted": 2, "succeeded": 2, "failed": 0, "settled_count": 6,
    "gains": {"trace": 86}, "rewards": {"material": 2}, "action_counts": {},
}}


@pytest.fixture
def batch(monkeypatch):
    monkeypatch.setattr(state_module, "_meta_state", deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    monkeypatch.setattr(ui, "_cave_public_batch_state", deepcopy(ui._cave_public_batch_state))
    monkeypatch.setattr(ui, "get_identity_display_name", lambda identity: str(identity))
    monkeypatch.setattr(ui, "console_log", Mock())
    monkeypatch.setattr(ui.trial_operations, "archive_cross_day_unknown", Mock(return_value={}))
    saves = []
    def save():
        saves.append(json.loads(json.dumps(state_module.get_miniapp_auto_config())))
        return True

    monkeypatch.setattr(ui, "save_state", Mock(side_effect=save))
    action = AsyncMock(return_value=(True, "fixture complete", {
        "settled_count": 3, "gains": {"trace": 43}, "rewards": {"material": 1},
    }))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", action)
    monkeypatch.setattr(ui, "send_audit_log", notify)
    return SimpleNamespace(saves=saves, action=action, notify=notify)


def persist(*, context=None, **kwargs):
    values = {
        "batch_id": "fixture-batch", "status": "completed", "result": "fixture complete",
        "completed": True, "cursor": 2, "completed_count": 2, "succeeded": 2,
        "failed": 0, "steps": [(1001, "trial"), (1002, "trial")],
        "outcomes": deepcopy(OUTCOMES),
    }
    values.update(kwargs)
    return ui._persist_trial_daily_batch_state(context or CONTEXT, **values)


def run_batch(**kwargs):
    return ui._run_cave_public_entry_batch(
        "fixture-batch", "", [1001, 1002], ["trial"], 0,
        trial_daily_context=kwargs.pop("context", CONTEXT), **kwargs,
    )


def test_completed_outcomes_survive_serialization_without_reopening_work(batch):
    assert persist()
    saved = batch.saves[-1]
    assert saved[PREFIX + "outcomes"] == OUTCOMES
    assert saved[PREFIX + "status"] == "completed"
    assert saved[PREFIX + "run_day"] == DAY
    assert saved[PREFIX + "steps"] == []
    assert saved[PREFIX + "cursor"] == saved[PREFIX + "completed"] == 0
    state_module.set_miniapp_auto_config(saved)
    assert ui._trial_daily_batch_resume_state(CONTEXT) == {}
    assert ui._trial_daily_retry_hold(CONTEXT) == {}
    batch.action.assert_not_awaited()
    batch.notify.assert_not_awaited()


@pytest.mark.parametrize("delivery", [True, False, None, "error", "cancel"])
def test_completion_is_saved_before_delivery_and_kept_for_every_outcome(batch, delivery):
    async def notify(*_args, **_kwargs):
        assert batch.saves[-1][PREFIX + "outcomes"] == OUTCOMES
        assert batch.saves[-1][PREFIX + "status"] == "completed"
        if delivery == "error":
            raise OSError("fixture delivery failed")
        if delivery == "cancel":
            raise asyncio.CancelledError
        return delivery

    batch.notify.side_effect = notify
    if delivery in {"error", "cancel"}:
        with pytest.raises(OSError if delivery == "error" else asyncio.CancelledError):
            asyncio.run(run_batch())
    else:
        asyncio.run(run_batch())
    batch.notify.assert_awaited_once()
    assert batch.action.await_count == 2
    assert not ui._cave_public_batch_state["running"]
    assert ui.normalize_miniapp_auto_config()[PREFIX + "outcomes"] == OUTCOMES
    assert ui._trial_daily_batch_resume_state(CONTEXT) == {}
    assert len(batch.saves) == 1


def test_outcome_snapshots_do_not_alias_input_normalization_or_resume(batch):
    source = deepcopy(OUTCOMES)
    persist(status="retry_pending", completed=False, cursor=1, outcomes=source)
    source["trial"]["gains"]["trace"] = 999
    assert ui.normalize_miniapp_auto_config()[PREFIX + "outcomes"] == OUTCOMES
    normalized = ui.normalize_miniapp_auto_config()
    normalized[PREFIX + "outcomes"]["trial"]["rewards"]["material"] = 999
    assert ui.normalize_miniapp_auto_config()[PREFIX + "outcomes"] == OUTCOMES
    resumed = ui._trial_daily_batch_resume_state(CONTEXT)
    resumed["outcomes"]["trial"]["settled_count"] = 999
    assert ui.normalize_miniapp_auto_config()[PREFIX + "outcomes"] == OUTCOMES


def test_resuming_batch_does_not_mutate_callers_outcome_snapshot(batch):
    initial = deepcopy(OUTCOMES)
    asyncio.run(run_batch(
        resume_cursor=1, initial_completed=1, initial_succeeded=1, initial_outcomes=initial,
    ))
    assert initial == OUTCOMES
    assert ui.normalize_miniapp_auto_config()[PREFIX + "outcomes"]["trial"]["gains"] == {"trace": 129}
    batch.action.assert_awaited_once()


def test_new_wave_and_new_day_do_not_inherit_completed_rewards(batch):
    persist()
    wave2 = {"wave_key": "wave2", "day_key": DAY}
    assert ui._trial_daily_batch_resume_state(wave2) == {}
    asyncio.run(run_batch(context=wave2))
    config = ui.normalize_miniapp_auto_config()
    assert config[PREFIX + "outcomes"] == OUTCOMES
    assert config["trial_daily_wave2_last_outcomes"] == OUTCOMES
    next_day = {**CONTEXT, "day_key": "2026-10-07"}
    assert ui._trial_daily_batch_resume_state(next_day) == {}
    asyncio.run(run_batch(context=next_day))
    config = ui.normalize_miniapp_auto_config()
    assert config[PREFIX + "outcomes"] == OUTCOMES
    assert config[PREFIX + "run_day"] == "2026-10-07"
    assert config["trial_daily_wave2_last_run_day"] == DAY


def test_fresh_running_wave_resets_previous_completed_snapshot(batch):
    persist()
    persist(
        context={**CONTEXT, "day_key": "2026-10-07"}, status="running", completed=False,
        cursor=0, completed_count=0, succeeded=0, outcomes={},
    )
    config = ui.normalize_miniapp_auto_config()
    assert config[PREFIX + "outcomes"] == {}
    assert config[PREFIX + "progress_day"] == "2026-10-07"
    assert config[PREFIX + "status"] == "running"
    assert config[PREFIX + "steps"] == [
        {"identity_id": 1001, "action": "trial"}, {"identity_id": 1002, "action": "trial"},
    ]


def test_no_work_completion_cannot_reuse_old_rewards(batch, monkeypatch):
    persist()
    monkeypatch.setattr(ui, "_build_cave_public_batch_steps", lambda *_args: [])
    asyncio.run(run_batch())
    assert ui.normalize_miniapp_auto_config()[PREFIX + "outcomes"] == {}
    batch.action.assert_not_awaited()
    batch.notify.assert_awaited_once()


def test_failed_steps_still_hold_without_claiming_completion(batch):
    batch.action.return_value = False, "fixture failed", {}
    asyncio.run(run_batch())
    config = ui.normalize_miniapp_auto_config()
    assert config[PREFIX + "status"] == "retry_pending"
    assert not config[PREFIX + "run_day"]
    assert config[PREFIX + "steps"] == [
        {"identity_id": 1001, "action": "trial"}, {"identity_id": 1002, "action": "trial"},
    ]
    assert config[PREFIX + "retry_at"] > config[PREFIX + "run_at"]
    assert config[PREFIX + "outcomes"] == {}
