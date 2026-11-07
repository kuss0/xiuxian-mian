import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model import state as state_module
from model.features import _phaseful
from tests import test_phaseful_early_result as base


env = base.env
DELETE_TRIGGER = _phaseful.delete_summary_trigger_msg


def trigger(spec, kind):
    if kind == "query":
        return _phaseful._send_active_summary_query(spec, base.NOW)
    if kind == "passive":
        return _phaseful._send_passive_summary_trigger(spec, "fixture", now=base.NOW)
    return _phaseful._send_summary_trigger(spec, "fixture")


@pytest.mark.parametrize("spec", base.SPECS, ids=("deep", "soul"))
@pytest.mark.parametrize("kind", ("active", "passive", "query"))
@pytest.mark.parametrize("change", ("settled", "running", "disabled", "timer", "removed", "replaced"))
@pytest.mark.parametrize("receipt", (True, False), ids=("receipt", "unknown"))
def test_summary_trigger_transport_never_overwrites_newer_state(env, monkeypatch, spec, kind, change, receipt):
    env.identity.update({spec.enabled_key: True, spec.phase_key: "summary_due",
                         spec.next_time_key: base.NOW - 1, spec.summary_sent_at_key: base.NOW - 600})
    expected = {}

    async def send(*_args, **_kwargs):
        if change == "settled":
            _phaseful.begin_post_summary_wait(spec, base.NOW + 2, confirmed=True)
        elif change == "running":
            _phaseful.mark_success(spec, base.NOW + 2, next_time=base.NOW + 12345)
        elif change == "disabled":
            env.identity[spec.enabled_key] = False
        elif change == "timer":
            env.identity[spec.next_time_key] = base.NOW + 777
        elif change == "removed":
            state_module.remove_identity(base.ID)
        else:
            state_module._meta_state["identity_states"][base.ID] = copy.deepcopy(env.identity)
        expected.update(copy.deepcopy(state_module._meta_state))
        return SimpleNamespace(id=9001, sent_at=base.NOW + 3) if receipt else None

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(_phaseful, "send_game_command", send_mock)
    result = asyncio.run(trigger(spec, kind))

    assert state_module._meta_state == expected
    assert result is receipt
    send_mock.assert_awaited_once()
    env.recover.assert_not_called()
    _phaseful.send_audit_log.assert_not_awaited()
    if receipt and change not in ("removed", "replaced"):
        _phaseful.delete_summary_trigger_msg.assert_any_await(spec, msg_id=9001)
    else:
        assert not any(call.kwargs.get("msg_id") for call in _phaseful.delete_summary_trigger_msg.await_args_list)


@pytest.mark.parametrize("spec", base.SPECS, ids=("deep", "soul"))
@pytest.mark.parametrize("kind", ("active", "passive", "query"))
def test_unchanged_summary_trigger_keeps_receipt_timestamp(env, monkeypatch, spec, kind):
    env.identity.update({spec.enabled_key: True, spec.phase_key: "summary_due", spec.next_time_key: base.NOW - 1})
    sent_at = base.NOW + 75
    send_mock = AsyncMock(return_value=SimpleNamespace(id=9001, sent_at=sent_at))
    monkeypatch.setattr(_phaseful, "send_game_command", send_mock)
    assert asyncio.run(trigger(spec, kind))
    assert env.identity[spec.phase_key] == "waiting_summary"
    assert env.identity[spec.summary_sent_at_key] == sent_at
    assert env.identity[spec.last_summary_msg_id_key] == 9001
    assert not any(call.kwargs.get("msg_id") for call in _phaseful.delete_summary_trigger_msg.await_args_list)


def test_explicit_trigger_cleanup_preserves_newer_anchor(env, monkeypatch):
    spec = base.SPECS[0]
    env.identity[spec.last_summary_msg_id_key] = 9002
    env.identity["my_msg_ids"][9001] = {"cmd": ".old-trigger"}
    env.identity["my_msg_ids"][9002] = {"cmd": ".new-trigger"}
    monkeypatch.setattr(_phaseful, "delete_summary_trigger_msg", DELETE_TRIGGER)
    monkeypatch.setattr(_phaseful, "is_auto_delete_sent_messages_enabled", lambda: False)
    asyncio.run(_phaseful.delete_summary_trigger_msg(spec, msg_id=9001))
    assert 9001 not in env.identity["my_msg_ids"]
    assert 9002 in env.identity["my_msg_ids"]
    assert env.identity[spec.last_summary_msg_id_key] == 9002
