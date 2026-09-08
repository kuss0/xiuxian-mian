import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import tianxing


IDENTITY_ID = 990450001
NOW = 1700000000.0
CHAT_ID = -100450001


@pytest.fixture
def auto_env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY_ID, 7451)
    state_module.update_send_as_profile(IDENTITY_ID, username="fixture_auto", sect_name="天星宗")
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(
        tianxing_enabled=True,
        tianxing_auto_config={"timeline_enabled": True, "craft_farm_enabled": False},
        tianxing_observation={"last_observed_at": NOW - 10, "tianji_value": 12},
    )
    monkeypatch.setattr(tianxing, "_TIANXING_AUTO_LOCKS", {})
    monkeypatch.setattr(tianxing, "_TIANXING_TIMELINE_LOCKS", {})
    save = Mock()
    send = AsyncMock(return_value=SimpleNamespace(id=4501, sent_at=NOW, chat_id=CHAT_ID))
    monkeypatch.setattr(tianxing, "save_state", save)
    monkeypatch.setattr(tianxing, "send_game_command", send)
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": "operation_changed", "definitely_unsent": True})
    yield SimpleNamespace(identity=identity, send=send, save=save)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def invalidate(h, change):
    if change == "removed":
        state_module.remove_identity(IDENTITY_ID)
    elif change == "replaced":
        state_module.remove_identity(IDENTITY_ID)
        state_module.set_identity_account(IDENTITY_ID, 7451)
        state_module.get_identity_state(IDENTITY_ID)["tianxing_enabled"] = True
    elif change == "rebound":
        state_module.set_identity_account(IDENTITY_ID, 7452)
    elif change == "identity_disabled":
        state_module.set_identity_enabled(IDENTITY_ID, False)
    elif change == "module_disabled":
        h.identity["tianxing_enabled"] = False
    elif change == "config":
        h.identity["tianxing_auto_config"]["auto_observe_enabled"] = False
    elif change == "global_paused":
        state_module.set_global_enabled(False)
    elif change == "paused":
        h.identity["tianxing_observation"]["automation_paused_until"] = -1
    elif change == "new_pending":
        h.identity["tianxing_observation"].update(
            auto_pending_action="observe", auto_pending_command=".观命",
            auto_pending_sent_at=NOW + 1, auto_pending_due_at=NOW + 100, auto_next_time=NOW + 100,
        )
    elif change == "new_clock":
        h.identity["tianxing_observation"].update(auto_next_time=NOW + 3600, auto_last_plan_at=NOW + 1)
    elif change == "reply":
        observed = h.identity["tianxing_observation"]
        tianxing._clear_tianxing_auto_pending(observed)
        observed.update(last_observed_at=NOW + 1, available_stars=["太阴"], available_stars_day=tianxing.get_day_key(NOW))
    else:
        raise AssertionError(change)


async def run_scheduler(kind="auto", now=NOW):
    scheduler = {
        "auto": tianxing.run_tianxing_scheduler,
        "daily": tianxing.run_tianxing_daily_bootstrap_scheduler,
        "followup": tianxing.run_tianxing_timeline_followup_scheduler,
    }[kind]
    with state_module.use_identity(IDENTITY_ID):
        return await scheduler(now)


async def run_plan():
    with state_module.use_identity(IDENTITY_ID):
        observed = tianxing.normalize_tianxing_observation(state_module.state.get("tianxing_observation"))
        config = tianxing.normalize_tianxing_auto_config(state_module.state.get("tianxing_auto_config"))
        plan = tianxing.build_tianxing_manual_plan("observe", now=NOW)
        return await tianxing._execute_tianxing_auto_plan(plan, observed, config, NOW)


@pytest.mark.parametrize("kind", ["auto", "daily", "followup"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "identity_disabled", "module_disabled", "config", "global_paused", "paused"])
def test_auto_lock_wait_rechecks_owner_and_controls(auto_env, kind, change):
    async def run():
        lock = asyncio.Lock()
        tianxing._TIANXING_AUTO_LOCKS[IDENTITY_ID] = lock
        await lock.acquire()
        task = asyncio.create_task(run_scheduler(kind))
        await asyncio.sleep(0)
        invalidate(auto_env, change)
        expected = copy.deepcopy(state_module._meta_state)
        lock.release()
        await task
        assert state_module._meta_state == expected

    asyncio.run(run())
    auto_env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["auto", "followup"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module_disabled", "config", "new_clock"])
def test_outer_scheduler_does_not_overwrite_after_timeline_await(auto_env, monkeypatch, kind, change):
    expected = None

    async def drain(*_args, **_kwargs):
        nonlocal expected
        invalidate(auto_env, change)
        expected = copy.deepcopy(state_module._meta_state)
        return {"active": True, "phase": "sent_waiting_ack", "next_time": NOW + 60}

    monkeypatch.setattr(tianxing, "_drain_existing_tianxing_timeline", drain)
    asyncio.run(run_scheduler(kind))
    assert state_module._meta_state == expected
    auto_env.send.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["receipt", "unsent", "cancelled"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "new_pending", "reply"])
def test_auto_send_result_keeps_new_owner_pending_and_early_reply(auto_env, outcome, change):
    expected = None

    async def send(*_args, **_kwargs):
        nonlocal expected
        invalidate(auto_env, change)
        expected = copy.deepcopy(state_module._meta_state)
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return None if outcome == "unsent" else SimpleNamespace(id=4501, sent_at=NOW, chat_id=CHAT_ID)

    auto_env.send.side_effect = send
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run_plan())
    else:
        asyncio.run(run_plan())
    assert state_module._meta_state == expected


@pytest.mark.parametrize("change", ["module_disabled", "config", "paused", "new_pending", "reply", "global_paused"])
def test_auto_send_checks_current_operation_in_queue(auto_env, change):
    dispatched = []

    async def send(command, **kwargs):
        invalidate(auto_env, change)
        check = kwargs.get("operation_check")
        if check is None or check() is True:
            dispatched.append(command)
            return SimpleNamespace(id=4501, sent_at=NOW, chat_id=CHAT_ID)
        return None

    auto_env.send.side_effect = send
    asyncio.run(run_plan())
    assert dispatched == []


def test_auto_sending_state_save_failure_prevents_dispatch(auto_env):
    auto_env.save.return_value = False
    asyncio.run(run_plan())
    auto_env.send.assert_not_awaited()


def test_auto_normal_send_retains_exact_message_and_chat(auto_env):
    async def send(_command, **kwargs):
        assert kwargs["operation_check"]() is True
        return SimpleNamespace(id=4501, sent_at=NOW, chat_id=CHAT_ID)

    auto_env.send.side_effect = send
    asyncio.run(run_plan())
    observed = auto_env.identity["tianxing_observation"]
    assert observed["auto_pending_msg_id"] == 4501
    assert observed["auto_pending_chat_id"] == CHAT_ID


@pytest.mark.parametrize("change", ["module_disabled", "config", "global_paused", "paused"])
def test_auto_keeps_receipt_after_post_dispatch_control_change(auto_env, change):
    async def send(*_args, **_kwargs):
        invalidate(auto_env, change)
        return SimpleNamespace(id=4501, sent_at=NOW, chat_id=CHAT_ID)

    auto_env.send.side_effect = send
    asyncio.run(run_plan())
    assert auto_env.identity["tianxing_observation"]["auto_pending_msg_id"] == 4501


def test_drain_stops_if_child_replaces_identity(auto_env, monkeypatch):
    step = {"action": "predict", "arg": "探索", "status": "pending"}
    auto_env.identity["tianxing_timeline_state"] = {
        "phase": "waiting_send", "active_step_index": 0, "active_step": step, "steps": [dict(step)],
    }
    expected = None

    async def timeline(*_args, **_kwargs):
        nonlocal expected
        invalidate(auto_env, "replaced")
        expected = copy.deepcopy(state_module._meta_state)
        return {"phase": "sent_waiting_ack", "changed": True}

    child = AsyncMock(side_effect=timeline)
    monkeypatch.setattr(tianxing, "run_tianxing_timeline_scheduler", child)

    async def run():
        with state_module.use_identity(IDENTITY_ID):
            return await tianxing._drain_existing_tianxing_timeline(NOW, {})

    result = asyncio.run(run())
    assert not result.get("active")
    assert state_module._meta_state == expected
    child.assert_awaited_once()


def test_inactive_timeline_result_does_not_reuse_old_auto_clock(auto_env, monkeypatch):
    expected = None

    async def drain(*_args, **_kwargs):
        nonlocal expected
        invalidate(auto_env, "new_clock")
        expected = copy.deepcopy(state_module._meta_state)
        return {}

    monkeypatch.setattr(tianxing, "_drain_existing_tianxing_timeline", drain)
    asyncio.run(run_scheduler())
    auto_env.send.assert_not_awaited()
    assert state_module._meta_state == expected


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module_disabled", "config", "new_clock"])
@pytest.mark.parametrize("active", [True, False])
def test_outer_scheduler_keeps_current_state_after_craft_await(auto_env, monkeypatch, change, active):
    h = auto_env
    h.identity["tianxing_observation"].update(
        fixed_star="太阴", fixed_star_day=tianxing.get_day_key(NOW), available_stars=["太阴"],
        available_stars_day=tianxing.get_day_key(NOW), available_stars_source="observe",
    )
    expected = None

    async def craft(*_args, **_kwargs):
        nonlocal expected
        invalidate(h, change)
        expected = copy.deepcopy(state_module._meta_state)
        return {"active": active, "stage": "sent_waiting_reply", "next_time": NOW + 60}

    monkeypatch.setattr(tianxing, "_drain_existing_tianxing_timeline", AsyncMock(return_value={}))
    child = AsyncMock(side_effect=craft)
    monkeypatch.setattr(tianxing, "run_tianxing_craft_farm_scheduler", child)
    asyncio.run(run_scheduler())
    child.assert_awaited_once()
    h.send.assert_not_awaited()
    assert state_module._meta_state == expected


@pytest.mark.parametrize("known_receipt", [True, False])
def test_pause_and_resume_do_not_erase_dispatched_auto_pending(auto_env, known_receipt):
    observed = auto_env.identity["tianxing_observation"]
    observed.update(
        auto_pending_action="observe", auto_pending_command=".观命",
        auto_pending_op_id="fixture-pending", auto_pending_account_id=7451,
        auto_pending_seen_replies=["fixture-reply"],
        auto_pending_msg_id=4501 if known_receipt else 0, auto_pending_chat_id=CHAT_ID,
        auto_pending_sent_at=NOW - 5, auto_pending_due_at=NOW + 85,
    )
    expected = {key: value for key, value in observed.items() if key.startswith("auto_pending_")}
    with state_module.use_identity(IDENTITY_ID):
        tianxing.set_tianxing_automation_paused(True, now=NOW)
    asyncio.run(run_scheduler())
    with state_module.use_identity(IDENTITY_ID):
        tianxing.set_tianxing_automation_paused(False, now=NOW + 1)
    restored = auto_env.identity["tianxing_observation"]
    assert {key: value for key, value in restored.items() if key.startswith("auto_pending_")} == expected
    auto_env.send.assert_not_awaited()


def test_receipt_keeps_newer_manual_auto_clock(auto_env):
    async def send(*_args, **_kwargs):
        invalidate(auto_env, "new_clock")
        return SimpleNamespace(id=4501, sent_at=NOW, chat_id=CHAT_ID)

    auto_env.send.side_effect = send
    asyncio.run(run_plan())
    observed = auto_env.identity["tianxing_observation"]
    assert observed["auto_pending_msg_id"] == 4501
    assert observed["auto_next_time"] == NOW + 3600
    assert observed["auto_last_plan_at"] == NOW + 1


@pytest.mark.parametrize("chat_id", [CHAT_ID, 0])
def test_auto_recovery_uses_saved_chat_or_refuses_unknown_route(auto_env, monkeypatch, chat_id):
    observed = auto_env.identity["tianxing_observation"]
    observed.update(
        auto_pending_action="observe", auto_pending_command=".观命", auto_pending_msg_id=4501,
        auto_pending_chat_id=chat_id, auto_pending_sent_at=NOW - 5, auto_pending_due_at=NOW + 85,
    )
    lookup = Mock(return_value=[])
    monkeypatch.setattr(tianxing, "find_message_log_replies", lookup)
    monkeypatch.setattr(tianxing, "get_sent_message_chat_id", lambda *_args, **_kwargs: 0)
    with state_module.use_identity(IDENTITY_ID):
        assert not tianxing._recover_tianxing_pending_reply_from_message_log(observed, NOW)
    if chat_id:
        lookup.assert_called_once()
        assert lookup.call_args.kwargs["chat_id"] == CHAT_ID
    else:
        lookup.assert_not_called()


def test_actual_pause_keeps_late_receipt_and_pause_deadline(auto_env):
    pause_deadline = None

    async def send(*_args, **_kwargs):
        nonlocal pause_deadline
        tianxing.set_tianxing_automation_paused(True, now=NOW + 1)
        pause_deadline = auto_env.identity["tianxing_observation"]["auto_next_time"]
        return SimpleNamespace(id=4501, sent_at=NOW, chat_id=CHAT_ID)

    auto_env.send.side_effect = send
    asyncio.run(run_plan())
    observed = auto_env.identity["tianxing_observation"]
    assert observed["auto_pending_msg_id"] == 4501
    assert observed["auto_pending_chat_id"] == CHAT_ID
    assert observed["automation_paused_until"] == -1
    assert observed["auto_last_action"] == "paused"
    assert observed["auto_next_time"] == pause_deadline


def test_real_reply_before_receipt_is_not_reopened(auto_env):
    from model.real_message_replay import get_real_message_text
    from pathlib import Path

    text = get_real_message_text(Path(__file__).parent / "fixtures" / "real_message_samples.json", "tianxing.observe.basic")
    expected = None

    async def send(command, **kwargs):
        nonlocal expected
        auto_env.identity["pending_tasks"][(CHAT_ID, 4501)] = {
            "cmd": command, "chat_id": CHAT_ID, "op_id": kwargs["op_id"],
            "source_module": kwargs["source_module"], "send_started_at": NOW, "sent_at": NOW + 0.5,
        }
        assert tianxing.apply_tianxing_passive(
            text, now=NOW + 1, family="tianxing_observe",
            reply_context={"send_as_id": IDENTITY_ID, "chat_id": CHAT_ID, "root_msg_id": 4501, "msg_id": 4502},
        )
        expected = copy.deepcopy(auto_env.identity["tianxing_observation"])
        return SimpleNamespace(id=4501, send_started_at=NOW, sent_at=NOW + 0.5, chat_id=CHAT_ID)

    auto_env.send.side_effect = send
    asyncio.run(run_plan())
    assert auto_env.identity["tianxing_observation"] == expected
    assert not expected["auto_pending_action"]
