import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from model import control, identity_refresh, cultivation_accounting
from model import state as state_module
from model.features import duel
from test_identity_refresh_lifecycle import env as env, deliver, IDENTITY, ACCOUNT, CHAT, NOW, CMD_IDENTITY_INFO


@pytest.fixture
def duel_env(env, monkeypatch):
    env.identity.update(duel_enabled=True, duel_target="@ccahen", duel_total_count=10,
                        duel_completed_count=0, next_duel_time=NOW, duel_reply_to_msg_id=0)
    state_module.update_send_as_profile(IDENTITY, realm="元婴后期")
    monkeypatch.setattr(duel, "_controlled_loadout_config", lambda _now: None)
    monkeypatch.setattr(duel, "_target_gate_reason", lambda _target: "")
    monkeypatch.setattr(duel, "_release_all_managed_pair_batches", Mock())
    monkeypatch.setattr(duel, "_reconcile_consumed_duel_prediction_from_last_report", Mock())
    monkeypatch.setattr(duel, "reconcile_duel_from_message_log", Mock())
    monkeypatch.setattr(duel, "save_state", Mock(return_value=True))
    monkeypatch.setattr(duel, "send_game_command", AsyncMock())
    return env


def test_due_missing_baseline_requests_owned_primary_read_without_duel(duel_env):
    assert cultivation_accounting.cultivation_balance(IDENTITY)["status"] == "no_baseline"
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert [cmd for cmd, _ in duel_env.calls] == [CMD_IDENTITY_INFO]
    duel.send_game_command.assert_not_awaited()
    assert duel_env.identity["next_duel_time"] > NOW
    assert cultivation_accounting.cultivation_balance(IDENTITY)["status"] == "no_baseline"
    assert asyncio.run(deliver())
    assert cultivation_accounting.cultivation_balance(IDENTITY)["status"] == "ready"
    assert duel_env.identity["duel_completed_count"] == 0


def test_pending_read_is_not_repeated_on_next_duel_check(duel_env):
    asyncio.run(duel.run_duel_scheduler(NOW))
    original = identity_refresh.request_for(IDENTITY)
    asyncio.run(duel.run_duel_scheduler(NOW + 2000))
    assert [cmd for cmd, _ in duel_env.calls] == [CMD_IDENTITY_INFO]
    assert identity_refresh.request_for(IDENTITY) is original


@pytest.mark.parametrize("condition", ["disabled", "not_due", "corrupt_refresh", "recent_read", "future_read"])
def test_no_baseline_does_not_override_read_admission(duel_env, condition):
    if condition == "disabled":
        duel_env.identity["duel_enabled"] = False
    elif condition == "not_due":
        duel_env.identity["next_duel_time"] = NOW + 100
    elif condition == "corrupt_refresh":
        duel_env.identity["identity_info_refresh"] = {"version": "broken"}
    else:
        duel_env.identity["identity_info_last_requested_at"] = NOW + (100 if condition == "future_read" else -2000)
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert duel_env.calls == []
    duel.send_game_command.assert_not_awaited()


@pytest.mark.parametrize("status", ["corrupt", "unverified_profile", "account_changed", "ready"])
def test_only_absent_baseline_requests_calibration(duel_env, monkeypatch, status):
    monkeypatch.setattr(duel, "cultivation_balance", lambda _id: {"status": status, "value": 0})
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert duel_env.calls == []
    duel.send_game_command.assert_not_awaited()


def test_primary_only_refresh_preserves_explicit_ui_default(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY, include_auxiliary=False))[0]
    assert [cmd for cmd, _ in env.calls] == [CMD_IDENTITY_INFO]


@pytest.mark.parametrize("value", [-1, True, "yesterday", float("nan"), float("inf")])
def test_invalid_last_request_clock_is_not_reset_to_zero(duel_env, value):
    duel_env.identity["identity_info_last_requested_at"] = value
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert duel_env.calls == []


@pytest.mark.parametrize("age,expected", [(86399, 0), (86400, 1), (86401, 1)])
def test_calibration_interval_is_rolling_24_hours(duel_env, age, expected):
    duel_env.identity["identity_info_last_requested_at"] = NOW - age
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert len(duel_env.calls) == expected


def test_old_unknown_refresh_is_not_replaced(duel_env):
    previous = identity_refresh.begin(IDENTITY, NOW - 90000)
    record = identity_refresh.reserve(previous, "primary", NOW - 90000)
    record["status"] = "unknown"
    previous["status"] = "failed"
    assert identity_refresh.request_for(IDENTITY) is previous
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert duel_env.calls == []
    assert identity_refresh.request_for(IDENTITY) is previous


def test_ineligible_realm_does_not_start_balance_read(duel_env):
    state_module.update_send_as_profile(IDENTITY, realm="筑基后期")
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert duel_env.calls == []


@pytest.mark.parametrize("condition", ["complete", "unconfigured", "mind_exhausted", "outside_window"])
def test_no_balance_query_when_no_duel_work_can_run(duel_env, monkeypatch, condition):
    if condition == "complete":
        duel_env.identity["duel_completed_count"] = 10
    elif condition == "unconfigured":
        duel_env.identity["duel_total_count"] = 0
    elif condition == "mind_exhausted":
        duel_env.identity.update(duel_log_reconcile_day=duel._duel_day_key(NOW), duel_observed_mind_remaining=0)
    else:
        monkeypatch.setattr(duel, "is_within_duel_exec_window", lambda _now: False)
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert duel_env.calls == []
    duel.send_game_command.assert_not_awaited()


@pytest.mark.parametrize("value", [None, False, 0, "", []])
def test_empty_malformed_refresh_is_not_treated_as_absent(duel_env, value):
    duel_env.identity["identity_info_refresh"] = value
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert duel_env.calls == []
    assert duel_env.identity["identity_info_refresh"] is value


@pytest.mark.parametrize("condition", ["wrong_chat", "wrong_sender", "rebound_account"])
def test_automatic_calibration_requires_owned_reply(duel_env, condition):
    asyncio.run(duel.run_duel_scheduler(NOW))
    if condition == "rebound_account":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    assert not asyncio.run(deliver(
        chat=CHAT - 1 if condition == "wrong_chat" else CHAT,
        sender=IDENTITY + 1 if condition == "wrong_sender" else IDENTITY,
    ))
    assert cultivation_accounting.cultivation_balance(IDENTITY)["status"] != "ready"
    duel.send_game_command.assert_not_awaited()


def test_calibration_does_not_prepare_equipment_or_consume_tianxing(duel_env, monkeypatch):
    prepare = AsyncMock()
    equipment = AsyncMock()
    monkeypatch.setattr(duel, "_prepare_duel_tianxing_route", prepare)
    monkeypatch.setattr(duel, "_run_controlled_loadout_prepare", equipment)
    config = {"duel_route_enabled": True}
    duel_env.identity["tianxing_auto_config"] = config.copy()
    asyncio.run(duel.run_duel_scheduler(NOW))
    assert [cmd for cmd, _ in duel_env.calls] == [CMD_IDENTITY_INFO]
    assert duel_env.identity["tianxing_auto_config"] == config
    prepare.assert_not_awaited()
    equipment.assert_not_awaited()
    duel.send_game_command.assert_not_awaited()
