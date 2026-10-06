import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state, ui


@pytest.fixture
def scheduler(monkeypatch):
    monkeypatch.setattr(state, "_meta_state", deepcopy(state.GLOBAL_STATE_DEFAULTS))
    monkeypatch.setattr(ui, "_cave_public_batch_state", {"running": False})
    monkeypatch.setattr(ui, "_cave_public_background_state", {"running": False})
    monkeypatch.setattr(ui, "_recover_cave_treasure_result_once", lambda _: None)
    monkeypatch.setattr(ui, "maybe_send_cave_public_fate_cards_daily_summary", AsyncMock(return_value=False))
    monkeypatch.setattr(ui, "get_global_enabled", lambda: True)
    monkeypatch.setattr(ui, "_cave_public_shared_hold", lambda _: {})
    monkeypatch.setattr(ui, "get_cave_public_entry_gate", lambda *_, **__: {})
    monkeypatch.setattr(ui, "_run_tree_miniapp_daily_scheduler", AsyncMock(return_value={"started": False}))
    ids = []
    monkeypatch.setattr(ui, "_normalize_cave_public_batch_identity_ids", lambda _: list(ids))
    background = AsyncMock(return_value={"started": False})
    start = AsyncMock(return_value=(True, "fixture", {"batch_id": "fixture-new"}))
    persist, notify = Mock(return_value=True), AsyncMock()
    monkeypatch.setattr(ui, "_run_cave_public_background_scheduler", background)
    monkeypatch.setattr(ui, "ui_start_cave_public_entry_batch", start)
    monkeypatch.setattr(ui, "save_state", persist)
    monkeypatch.setattr(ui, "send_audit_log", notify)
    config = ui.normalize_miniapp_auto_config()
    config.update(trial_daily_enabled=True, trial_daily_scheduler_confirmed=True,
                  cave_public_trial_enabled=True,
                  cave_public_entry_urls=["https://t.me/fanrenxiuxian_bot?startapp=df_fixture"])
    state.set_miniapp_auto_config(config)
    return SimpleNamespace(ids=ids, background=background, start=start, persist=persist, notify=notify)


def wave_time(wave):
    return datetime(2026, 10, 7, 1 if wave == "wave1" else 5, 30, tzinfo=ui.TZ_LOCAL).timestamp()


@pytest.mark.parametrize("wave", ["wave1", "wave2"])
@pytest.mark.parametrize("resuming", [False, True])
def test_empty_selection_never_finishes_or_erases_wave(scheduler, wave, resuming):
    config = ui.normalize_miniapp_auto_config()
    if resuming:
        prefix = f"trial_daily_{wave}_last_"
        config.update({prefix + "status": "retry_pending", prefix + "progress_day": "2026-10-07",
                       prefix + "batch_id": "fixture-prior", prefix + "cursor": 1,
                       prefix + "completed": 1, prefix + "succeeded": 1,
                       prefix + "retry_at": wave_time(wave) - 60,
                       prefix + "retry_reason": "public_entry_busy",
                       prefix + "steps": [{"identity_id": 1001, "action": "trial"}],
                       prefix + "outcomes": {"trial": {"gains": {"trace": 7}}}})
        state.set_miniapp_auto_config(config)
    before = deepcopy(state.get_miniapp_auto_config())
    result = asyncio.run(ui.run_miniapp_daily_scheduler(wave_time(wave)))
    assert result == {"started": False, "reason": "no_enabled_identity", "wave": wave}
    assert state.get_miniapp_auto_config() == before
    scheduler.persist.assert_not_called()
    scheduler.start.assert_not_awaited()
    scheduler.notify.assert_not_awaited()


@pytest.mark.parametrize("wave,expected", [("wave1", [1001, 1002]), ("wave2", [1003, 1004])])
def test_restored_selection_can_start_in_same_window(scheduler, wave, expected):
    now = wave_time(wave)
    assert not asyncio.run(ui.run_miniapp_daily_scheduler(now))["started"]
    scheduler.ids.extend([1001, 1002, 1003, 1004])
    result = asyncio.run(ui.run_miniapp_daily_scheduler(now + 60))
    assert result["started"] and result["wave"] == wave
    scheduler.start.assert_awaited_once()
    assert scheduler.start.await_args.args[0]["send_as_ids"] == expected
    scheduler.persist.assert_not_called()


def test_empty_trial_does_not_starve_other_background_work(scheduler):
    scheduler.background.return_value = {"started": True, "kind": "fixture-background"}
    assert asyncio.run(ui.run_miniapp_daily_scheduler(wave_time("wave1"))) == scheduler.background.return_value
    scheduler.background.assert_awaited_once()
    scheduler.persist.assert_not_called()
    scheduler.start.assert_not_awaited()


@pytest.mark.parametrize("wave", ["wave1", "wave2"])
def test_existing_completed_day_is_not_reopened(scheduler, wave):
    config = ui.normalize_miniapp_auto_config()
    config.update({f"trial_daily_{wave}_last_run_day": "2026-10-07",
                   f"trial_daily_{wave}_last_status": "completed"})
    state.set_miniapp_auto_config(config)
    scheduler.ids.extend([1001, 1002, 1003, 1004])
    assert not asyncio.run(ui.run_miniapp_daily_scheduler(wave_time(wave)))["started"]
    assert state.get_miniapp_auto_config() == config
    scheduler.start.assert_not_awaited()
    scheduler.persist.assert_not_called()


def test_existing_unknown_hold_is_not_replaced_by_empty_selection(scheduler):
    config = ui.normalize_miniapp_auto_config()
    config.update(trial_daily_wave1_last_status="retry_pending",
                  trial_daily_wave1_last_progress_day="2026-10-07",
                  trial_daily_wave1_last_retry_reason="batch_checkpoint_save_outcome_unknown")
    state.set_miniapp_auto_config(config)
    result = asyncio.run(ui.run_miniapp_daily_scheduler(wave_time("wave1")))
    assert result["reason"] == "trial_recovery_hold"
    assert state.get_miniapp_auto_config() == config
    scheduler.start.assert_not_awaited()
    scheduler.persist.assert_not_called()
