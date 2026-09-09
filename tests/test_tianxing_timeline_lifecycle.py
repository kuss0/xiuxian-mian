import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import action_guard
from model import state as state_module
from model.features import tianxing


IDENTITY_ID = 990440001
NOW = 1700000000.0
CHAT_ID = -100440001


def timeline_fixture(*, status="pending", plan_id="fixture-timeline", started_at=0):
    step = {
        "id": "fixture-predict-step", "action": "predict", "arg": "探索", "route": "探索",
        "command": ".推命 探索", "status": status, "send_msg_id": 0,
        "send_started_at": started_at, "sent_at": 0,
    }
    return tianxing.normalize_tianxing_timeline_state({
        "plan_id": plan_id, "phase": "sending" if status == "sending" else "waiting_send",
        "route": "探索", "active_step": step, "active_step_index": 0, "steps": [copy.deepcopy(step)],
        "created_at": NOW - 60, "updated_at": started_at or NOW - 1,
    })


@pytest.fixture
def timeline_env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    saved_guards = copy.deepcopy(action_guard._recent_closed_command_guards)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    action_guard._recent_closed_command_guards.clear()
    state_module.set_identity_account(IDENTITY_ID, 7441)
    state_module.update_send_as_profile(IDENTITY_ID, username="fixture_tianxing", sect_name="天星宗")
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(
        tianxing_enabled=True,
        tianxing_auto_config={
            "timeline_enabled": True, "timeline_dry_run_enabled": False, "auto_predict_enabled": True,
            "auto_change_fate_enabled": True, "ack_timeout_sec": 15, "calibration_backoff_sec": 60,
        },
        tianxing_observation={
            "last_observed_at": NOW - 10, "fixed_star": "太阴", "available_stars": ["太阴"],
            "current_prediction": "", "current_prediction_until": 0, "current_change": "",
            "current_change_until": 0, "tianji_value": 12, "calamity_count": 0,
        },
        tianxing_timeline_state=timeline_fixture(),
    )
    monkeypatch.setattr(tianxing, "_TIANXING_TIMELINE_LOCKS", {})
    monkeypatch.setattr(tianxing, "_TIANXING_AUTO_LOCKS", {})
    save = Mock()
    send = AsyncMock(return_value=SimpleNamespace(id=4401, sent_at=NOW, chat_id=CHAT_ID))
    monkeypatch.setattr(tianxing, "save_state", save)
    monkeypatch.setattr(tianxing, "send_game_command", send)
    monkeypatch.setattr(tianxing, "get_last_game_send_block", Mock(return_value={}))
    monkeypatch.setattr(tianxing, "get_sent_message_chat_id", lambda _id, **_kwargs: CHAT_ID)
    monkeypatch.setattr(tianxing, "_tianxing_action_guard_wait", lambda _command, _now: (0, ""))
    yield SimpleNamespace(identity=identity, send=send, save=save)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)
    action_guard._recent_closed_command_guards.clear()
    action_guard._recent_closed_command_guards.update(saved_guards)


def invalidate(h, change):
    if change == "removed":
        state_module.remove_identity(IDENTITY_ID)
    elif change == "replaced":
        state_module.remove_identity(IDENTITY_ID)
        state_module.set_identity_account(IDENTITY_ID, 7441)
        state_module.get_identity_state(IDENTITY_ID)["tianxing_enabled"] = True
    elif change == "rebound":
        state_module.set_identity_account(IDENTITY_ID, 7442)
    elif change == "identity_disabled":
        state_module.set_identity_enabled(IDENTITY_ID, False)
    elif change == "module_disabled":
        h.identity["tianxing_enabled"] = False
    elif change == "config":
        h.identity["tianxing_auto_config"]["auto_predict_enabled"] = False
    elif change == "plan":
        h.identity["tianxing_timeline_state"] = timeline_fixture(status="sending", plan_id="newer-plan", started_at=NOW + 1)
    elif change == "step":
        h.identity["tianxing_timeline_state"]["active_step"]["send_started_at"] = NOW + 1
    elif change == "observation":
        h.identity["tianxing_observation"].update(
            current_prediction="斗法", current_prediction_until=NOW + 3600, current_prediction_set_at=NOW,
        )
    elif change == "paused":
        h.identity["tianxing_observation"]["automation_paused_until"] = -1
    elif change == "global_paused":
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")
    else:
        raise AssertionError(change)


async def run_scheduler(now=NOW, **kwargs):
    with state_module.use_identity(IDENTITY_ID):
        return await tianxing.run_tianxing_timeline_scheduler(now, **kwargs)


def test_normal_timeline_send_records_exact_receipt_and_chat(timeline_env):
    result = asyncio.run(run_scheduler())
    timeline_env.send.assert_awaited_once()
    assert result["phase"] == "sent_waiting_ack"
    step = timeline_env.identity["tianxing_timeline_state"]["active_step"]
    assert step["send_msg_id"] == 4401
    assert step["send_chat_id"] == CHAT_ID


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "identity_disabled", "module_disabled", "config", "global_paused", "paused"])
def test_timeline_lock_wait_does_not_enter_invalidated_work(timeline_env, change):
    h = timeline_env

    async def run():
        lock = asyncio.Lock()
        tianxing._TIANXING_TIMELINE_LOCKS[IDENTITY_ID] = lock
        await lock.acquire()
        task = asyncio.create_task(run_scheduler())
        await asyncio.sleep(0)
        invalidate(h, change)
        expected = copy.deepcopy(state_module._meta_state)
        lock.release()
        await task
        assert state_module._meta_state == expected

    asyncio.run(run())
    h.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "plan", "step"])
def test_late_send_receipt_never_overwrites_a_new_owner_or_step(timeline_env, change):
    h = timeline_env
    expected = None

    async def send(*_args, **_kwargs):
        nonlocal expected
        invalidate(h, change)
        expected = copy.deepcopy(state_module._meta_state)
        return SimpleNamespace(id=4401, sent_at=NOW, chat_id=CHAT_ID)

    h.send.side_effect = send
    asyncio.run(run_scheduler())
    assert state_module._meta_state == expected


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "plan", "step"])
def test_cancelled_send_does_not_restore_an_old_timeline(timeline_env, change):
    h = timeline_env
    expected = None

    async def send(*_args, **_kwargs):
        nonlocal expected
        invalidate(h, change)
        expected = copy.deepcopy(state_module._meta_state)
        raise asyncio.CancelledError

    h.send.side_effect = send
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_scheduler())
    assert state_module._meta_state == expected


@pytest.mark.parametrize("change", ["module_disabled", "config", "paused", "plan", "step", "observation"])
def test_queued_timeline_command_has_a_live_operation_guard(timeline_env, monkeypatch, change):
    h = timeline_env
    dispatched = []

    async def send(command, **kwargs):
        invalidate(h, change)
        check = kwargs.get("operation_check")
        if check is None or check() is True:
            dispatched.append(command)
            return SimpleNamespace(id=4401, sent_at=NOW, chat_id=CHAT_ID)
        return None

    h.send.side_effect = send
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": "operation_changed", "definitely_unsent": True})
    asyncio.run(run_scheduler())
    assert dispatched == []


@pytest.mark.parametrize("cancelled", [False, True])
def test_reply_before_receipt_or_cancellation_stays_confirmed(timeline_env, cancelled):
    h = timeline_env
    expected = None

    async def send(*_args, **_kwargs):
        nonlocal expected
        timeline = h.identity["tianxing_timeline_state"]
        timeline["active_step"].update(status="confirmed", confirmed_at=NOW + 2)
        timeline.update(phase="state_confirmed", updated_at=NOW + 2)
        expected = copy.deepcopy(timeline)
        if cancelled:
            raise asyncio.CancelledError
        return SimpleNamespace(id=4401, sent_at=NOW, chat_id=CHAT_ID)

    h.send.side_effect = send
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run_scheduler())
    else:
        asyncio.run(run_scheduler())
    assert h.identity["tianxing_timeline_state"] == expected


@pytest.mark.parametrize("change", ["module_disabled", "config", "identity_disabled", "global_paused", "paused"])
def test_returned_receipt_survives_post_dispatch_control_changes(timeline_env, change):
    async def send(*_args, **_kwargs):
        invalidate(timeline_env, change)
        return SimpleNamespace(id=4401, sent_at=NOW, chat_id=CHAT_ID)

    timeline_env.send.side_effect = send
    asyncio.run(run_scheduler())
    step = timeline_env.identity["tianxing_timeline_state"]["active_step"]
    assert step["status"] == "sent_waiting_ack"
    assert step["send_msg_id"] == 4401


def test_recovered_sending_without_message_id_never_rearms_prediction(timeline_env):
    h = timeline_env
    h.identity["tianxing_timeline_state"] = timeline_fixture(status="sending", started_at=NOW - 1000)
    result = asyncio.run(run_scheduler())
    h.send.assert_not_awaited()
    assert result["phase"] == "ack_timeout"
    step = h.identity["tianxing_timeline_state"]["active_step"]
    assert step["status"] == "ack_timeout"
    assert step["calibration_due_at"] > NOW
    assert not step.get("queue_retry_at")


def test_send_exception_is_persisted_as_unknown_not_retried(timeline_env):
    h = timeline_env
    h.send.side_effect = TimeoutError("fixture-unknown-send")
    result = asyncio.run(run_scheduler())
    assert result["phase"] == "ack_timeout"
    assert h.identity["tianxing_timeline_state"]["active_step"]["status"] == "ack_timeout"
    asyncio.run(run_scheduler(NOW + 1))
    assert h.send.await_count == 1


def test_unknown_timeline_send_does_not_close_a_newer_guard(timeline_env):
    h = timeline_env
    h.send.return_value = None
    with state_module.use_identity(IDENTITY_ID):
        action_guard.note_sent(".推命 探索", IDENTITY_ID, 4499, sent_at=NOW, chat_id=CHAT_ID)
    guard_before = copy.deepcopy(h.identity["action_guard_sessions"])
    asyncio.run(run_scheduler())
    assert h.identity["action_guard_sessions"] == guard_before


def test_parent_operation_can_cancel_tianxing_before_it_enters(timeline_env):
    result = asyncio.run(run_scheduler(operation_check=lambda: False))
    timeline_env.send.assert_not_awaited()
    assert result["phase"] == "cancelled"


def test_failed_sending_state_save_does_not_dispatch(timeline_env):
    timeline_env.save.return_value = False
    result = asyncio.run(run_scheduler())
    timeline_env.send.assert_not_awaited()
    assert result["changed"]
    assert timeline_env.identity["tianxing_timeline_state"]["active_step"]["status"] == "pending"


def test_unchanged_queued_timeline_command_remains_allowed(timeline_env):
    async def send(_command, **kwargs):
        assert kwargs["operation_check"]() is True
        return SimpleNamespace(id=4401, sent_at=NOW, chat_id=CHAT_ID)

    timeline_env.send.side_effect = send
    result = asyncio.run(run_scheduler())
    assert result["phase"] == "sent_waiting_ack"


@pytest.mark.parametrize("block", [
    {}, {"code": "new_unclassified_fault"},
    {"code": "send_timeout"}, {"code": "send_exception"},
])
def test_only_explicit_unsent_evidence_can_retry(timeline_env, monkeypatch, block):
    timeline_env.send.return_value = None
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: block)
    result = asyncio.run(run_scheduler())
    assert result["phase"] == "ack_timeout"
    assert timeline_env.identity["tianxing_timeline_state"]["active_step"]["status"] == "ack_timeout"


@pytest.mark.parametrize("block", [
    {"code": "send_queue_timeout"}, {"code": "send_prepare_timeout"},
    {"code": "custom_admission", "definitely_unsent": True},
])
def test_known_unsent_retries_once_after_backoff(timeline_env, monkeypatch, block):
    h = timeline_env
    h.send.return_value = None
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: block)
    first = asyncio.run(run_scheduler())
    assert first["phase"] == "waiting_send"
    retry_at = h.identity["tianxing_timeline_state"]["blocked_until"]
    asyncio.run(run_scheduler(retry_at - 1))
    assert h.send.await_count == 1
    h.send.return_value = SimpleNamespace(id=4402, sent_at=retry_at + 1, chat_id=CHAT_ID)
    final = asyncio.run(run_scheduler(retry_at + 1))
    assert final["phase"] == "sent_waiting_ack"
    assert h.send.await_count == 2


@pytest.mark.parametrize("msg_id,chat_id,closed", [
    (4401, CHAT_ID, True), (4402, CHAT_ID, False),
    (4401, CHAT_ID - 1, False), (0, CHAT_ID, False),
])
def test_timeline_guard_cleanup_is_message_and_chat_scoped(timeline_env, msg_id, chat_id, closed):
    with state_module.use_identity(IDENTITY_ID):
        action_guard.note_sent(".推命 探索", IDENTITY_ID, 4401, sent_at=NOW, chat_id=CHAT_ID)
        result = tianxing._close_tianxing_guard_for_timeline_step(
            {"action": "predict", "send_msg_id": msg_id, "send_chat_id": chat_id}, NOW + 1,
        )
    assert result is closed
    assert ("tianxing_predict" in timeline_env.identity["action_guard_sessions"]) is not closed


@pytest.mark.parametrize("msg_id", [None, 0, -1, "invalid"])
def test_invalid_message_id_stays_unknown(timeline_env, msg_id):
    timeline_env.send.return_value = SimpleNamespace(id=msg_id, sent_at=NOW, chat_id=CHAT_ID)
    result = asyncio.run(run_scheduler())
    assert result["phase"] == "ack_timeout"


def test_late_receipt_preserves_unrelated_farm_bookkeeping(timeline_env):
    async def send(_command, **kwargs):
        farm = timeline_env.identity["tianxing_timeline_state"]["craft_farm"]
        farm.update(daily_count=7, last_result="newer-farm-result", updated_at=NOW + 1)
        assert kwargs["operation_check"]() is True
        return SimpleNamespace(id=4401, sent_at=NOW + 2, chat_id=CHAT_ID)

    timeline_env.send.side_effect = send
    result = asyncio.run(run_scheduler())
    timeline = timeline_env.identity["tianxing_timeline_state"]
    assert result["phase"] == "sent_waiting_ack"
    assert timeline["craft_farm"]["daily_count"] == 7
    assert timeline["craft_farm"]["last_result"] == "newer-farm-result"


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module_disabled", "plan"])
def test_scheduler_rechecks_after_actual_calibration_send(timeline_env, change):
    expected = None
    timeline = timeline_env.identity["tianxing_timeline_state"]
    panel = dict(timeline["active_step"], action="panel", arg="", route="", command=tianxing.CMD_TIANXING_PANEL)
    timeline.update(active_step=panel, steps=[copy.deepcopy(panel)])

    async def calibration(*_args, **_kwargs):
        nonlocal expected
        invalidate(timeline_env, change)
        expected = copy.deepcopy(state_module._meta_state)
        return SimpleNamespace(id=4491, sent_at=NOW, chat_id=CHAT_ID)

    timeline_env.send.side_effect = calibration
    asyncio.run(run_scheduler())
    timeline_env.send.assert_awaited_once()
    if change == "module_disabled":
        current = copy.deepcopy(state_module._meta_state)
        actual_timeline = current["identity_states"][IDENTITY_ID].pop("tianxing_timeline_state")
        expected["identity_states"][IDENTITY_ID].pop("tianxing_timeline_state")
        assert current == expected
        assert actual_timeline["active_step"]["send_msg_id"] == 4491
        assert actual_timeline["active_step"]["status"] == "sent_waiting_ack"
    else:
        assert state_module._meta_state == expected


def test_paused_parent_cancels_queued_tianxing_step(timeline_env, monkeypatch):
    allowed = True

    async def send(_command, **kwargs):
        nonlocal allowed
        allowed = False
        assert kwargs["operation_check"]() is False
        return None

    timeline_env.send.side_effect = send
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": "operation_changed", "definitely_unsent": True})
    asyncio.run(run_scheduler(operation_check=lambda: allowed))
    assert timeline_env.identity["tianxing_timeline_state"]["active_step"]["status"] == "pending"


@pytest.mark.parametrize("flag", ["timeline_dry_run_enabled", "auto_predict_enabled"])
def test_existing_plan_honors_current_action_switch(timeline_env, flag):
    timeline_env.identity["tianxing_auto_config"][flag] = flag == "timeline_dry_run_enabled"
    result = asyncio.run(run_scheduler())
    timeline_env.send.assert_not_awaited()
    assert result["changed"]


def test_release_rechecks_expired_prediction(timeline_env):
    h = timeline_env
    h.identity["tianxing_observation"].update(
        current_prediction="探索", current_prediction_until=NOW - 1, current_prediction_set_at=NOW - 60,
    )
    step = {
        "id": "release", "action": "release_downstream", "route": "探索", "arg": "探索",
        "status": "pending", "release_basis": "prediction",
    }
    h.identity["tianxing_timeline_state"].update(active_step=step, steps=[copy.deepcopy(step)])
    result = asyncio.run(run_scheduler())
    h.send.assert_not_awaited()
    assert result["phase"] == "blocked_replan"
    assert not h.identity["tianxing_timeline_state"]["released_routes"]


def test_backoff_begins_after_slow_unknown_send(timeline_env, monkeypatch):
    current_time = NOW
    monkeypatch.setattr(tianxing._TianxingOperation, "current_time", lambda _self: current_time)

    async def send(*_args, **_kwargs):
        nonlocal current_time
        current_time += 180
        return None

    timeline_env.send.side_effect = send
    asyncio.run(run_scheduler())
    assert timeline_env.identity["tianxing_timeline_state"]["blocked_until"] == NOW + 180 + 60


def test_unknown_sending_survives_sqlite_reload_without_resending(timeline_env, monkeypatch, tmp_path):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "tianxing.db"))
    monkeypatch.setattr(tianxing, "save_state", persistence.save_state)
    timeline_env.identity["tianxing_timeline_state"] = timeline_fixture(status="sending", started_at=NOW - 1000)
    assert persistence.save_state() is True
    assert persistence.load_state() is True
    result = asyncio.run(run_scheduler())
    assert result["phase"] == "ack_timeout"
    assert persistence.load_state() is True
    restored = state_module.get_identity_state(IDENTITY_ID)["tianxing_timeline_state"]
    assert restored["active_step"]["status"] == "ack_timeout"
    asyncio.run(run_scheduler(NOW + 1))
    timeline_env.send.assert_not_awaited()


def test_missing_prediction_expiry_cannot_be_inferred_from_set_time():
    assert tianxing._prediction_effective_until("探索", {
        "current_prediction": "探索", "current_prediction_set_at": NOW - 60,
    }, NOW) == 0


@pytest.mark.parametrize("until", [NOW - 1, NOW + 30])
def test_explicit_prediction_expiry_is_not_extended_from_set_time(timeline_env, until):
    observed = {
        "current_prediction": "探索", "current_prediction_until": until,
        "current_prediction_set_at": NOW - 60,
    }
    assert tianxing._prediction_effective_until("探索", observed, NOW) == until
