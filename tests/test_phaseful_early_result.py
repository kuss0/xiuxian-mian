import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import _phaseful, deep_retreat, yuanying


NOW = 1_790_000_000.0
ID = 1001
SPECS = (deep_retreat.DEEP_RETREAT_SPEC, yuanying.YUANYING_SPEC)


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.ensure_identity_registered(ID)
    monkeypatch.setattr(_phaseful, "_PHASEFUL_LAUNCH_LOCKS", {})
    monkeypatch.setattr(_phaseful, "save_state", Mock())
    monkeypatch.setattr(_phaseful, "console_log", Mock())
    monkeypatch.setattr(_phaseful, "send_audit_log", AsyncMock())
    monkeypatch.setattr(_phaseful, "delete_summary_trigger_msg", AsyncMock())
    recover = Mock(return_value=None)
    monkeypatch.setattr(_phaseful, "_recover_phaseful_sent_from_message_log", recover)
    monkeypatch.setattr(_phaseful, "classify_game_send_block", Mock(return_value={"status": "unknown"}))
    try:
        with state_module.use_identity(ID):
            yield SimpleNamespace(identity=state_module.get_identity_state(ID), recover=recover)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(saved)


@pytest.mark.parametrize("spec", SPECS, ids=("deep", "soul"))
@pytest.mark.parametrize("original_phase", ("post_summary_wait", "summary_due"))
@pytest.mark.parametrize("effect", ("running", "settled", "disabled", "rescheduled"))
@pytest.mark.parametrize("returned_message", (True, False), ids=("receipt", "unknown"))
def test_send_return_never_overwrites_newer_phaseful_state(
    env, monkeypatch, spec, original_phase, effect, returned_message,
):
    identity = env.identity
    identity.update({spec.enabled_key: True, spec.phase_key: original_phase,
                     spec.next_time_key: NOW - 1, spec.last_command_key: NOW - 600})
    expected = {}

    async def send(*_args, **_kwargs):
        assert identity[spec.phase_key] == "queued_launch"
        if effect == "running":
            _phaseful.mark_success(spec, NOW + 2, next_time=NOW + 12345)
        elif effect == "settled":
            _phaseful.begin_post_summary_wait(spec, NOW + 2, confirmed=True)
        elif effect == "disabled":
            identity[spec.enabled_key] = False
        else:
            identity[spec.next_time_key] = NOW + 777
        expected.update(copy.deepcopy(identity))
        return SimpleNamespace(id=9001, sent_at=NOW + 3) if returned_message else None

    mocked_send = AsyncMock(side_effect=send)
    monkeypatch.setattr(_phaseful, "send_game_command", mocked_send)
    asyncio.run(_phaseful._send_summary_launch(spec, ".fixture-launch", "fixture", now=NOW))

    assert identity == expected
    mocked_send.assert_awaited_once()
    # Only the pre-dispatch lookup for summary_due is allowed, never recovery
    # or retry after a newer result or operator change has taken ownership.
    assert env.recover.call_count == int(original_phase == "summary_due")
    _phaseful.send_audit_log.assert_not_awaited()


@pytest.mark.parametrize("spec", SPECS, ids=("deep", "soul"))
@pytest.mark.parametrize("original_phase", ("post_summary_wait", "summary_due"))
def test_unchanged_launch_uses_receipt_time_not_queue_time(env, monkeypatch, spec, original_phase):
    identity = env.identity
    identity.update({spec.enabled_key: True, spec.phase_key: original_phase,
                     spec.next_time_key: NOW - 1, spec.last_command_key: NOW - 600})
    sent_at = NOW + 75
    monkeypatch.setattr(_phaseful, "send_game_command", AsyncMock(
        return_value=SimpleNamespace(id=9001, sent_at=sent_at)))
    assert asyncio.run(_phaseful._send_summary_launch(spec, ".fixture-launch", "fixture", now=NOW))
    if original_phase == "post_summary_wait":
        assert identity[spec.phase_key] == "launching"
        assert identity[spec.last_command_key] == sent_at
    else:
        assert identity[spec.phase_key] == "waiting_summary"
        assert identity[spec.summary_sent_at_key] == sent_at
        assert identity[spec.last_summary_msg_id_key] == 9001


@pytest.mark.parametrize("spec", SPECS, ids=("deep", "soul"))
@pytest.mark.parametrize("change", ("removed", "replaced"))
def test_launch_return_cannot_write_a_removed_or_replaced_identity(env, monkeypatch, spec, change):
    env.identity.update({spec.enabled_key: True, spec.phase_key: "post_summary_wait"})
    expected = {}

    async def send(*_args, **_kwargs):
        if change == "removed":
            state_module.remove_identity(ID)
        else:
            state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
        expected.update(copy.deepcopy(state_module._meta_state))
        return SimpleNamespace(id=9001, sent_at=NOW + 3)

    monkeypatch.setattr(_phaseful, "send_game_command", AsyncMock(side_effect=send))
    asyncio.run(_phaseful._send_summary_launch(spec, ".fixture-launch", "fixture", now=NOW))
    assert state_module._meta_state == expected


@pytest.mark.parametrize("spec", SPECS, ids=("deep", "soul"))
@pytest.mark.parametrize("change", ("running", "disabled", "rescheduled"))
def test_cleanup_must_not_dispatch_after_newer_result(env, monkeypatch, spec, change):
    env.identity.update({spec.enabled_key: True, spec.phase_key: "post_summary_wait"})
    expected = {}

    async def cleanup(_spec):
        if change == "running":
            _phaseful.mark_success(spec, NOW + 2, next_time=NOW + 12345)
        elif change == "disabled":
            env.identity[spec.enabled_key] = False
        else:
            env.identity[spec.next_time_key] = NOW + 777
        expected.update(copy.deepcopy(env.identity))

    send = AsyncMock(return_value=SimpleNamespace(id=9001, sent_at=NOW + 3))
    monkeypatch.setattr(_phaseful, "delete_summary_trigger_msg", AsyncMock(side_effect=cleanup))
    monkeypatch.setattr(_phaseful, "send_game_command", send)
    assert not asyncio.run(_phaseful._send_summary_launch(spec, ".fixture-launch", "fixture", now=NOW))
    send.assert_not_awaited()
    assert env.identity == expected


def test_actual_deep_success_reducer_wins_over_late_transport_return(env, monkeypatch):
    spec = deep_retreat.DEEP_RETREAT_SPEC
    env.identity.update({spec.enabled_key: True, spec.phase_key: "post_summary_wait"})
    monkeypatch.setattr(deep_retreat, "save_state", Mock())
    monkeypatch.setattr(deep_retreat, "_record_deep_retreat_event", Mock())
    monkeypatch.setattr(deep_retreat, "_note_deep_retreat_remote_block", Mock())
    monkeypatch.setattr(deep_retreat, "send_audit_log", AsyncMock())
    expected = {}

    async def send(command, **_kwargs):
        handled = await deep_retreat.handle_deep_retreat_success_reply(
            "\u4f60\u5df2\u8fdb\u5165\u6df1\u5ea6\u95ed\u5173\u72b6\u6001\uff0c"
            "\u795e\u9b42\u5c06\u81ea\u884c\u5410\u7eb3\uff0c8\u5c0f\u65f6\u540e\u7ed3\u675f\u3002",
            NOW + 2, SimpleNamespace(id=9001, raw_text=command), matched_family="deep_retreat",
        )
        assert handled
        assert env.identity[spec.phase_key] == "running"
        expected.update(copy.deepcopy(env.identity))
        return SimpleNamespace(id=9001, sent_at=NOW + 3)

    monkeypatch.setattr(_phaseful, "send_game_command", AsyncMock(side_effect=send))
    asyncio.run(_phaseful._send_summary_launch(spec, deep_retreat.CMD_DEEP_RETREAT, "fixture", now=NOW))
    assert env.identity == expected
