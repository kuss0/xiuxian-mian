import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import _phaseful, deep_retreat
from tests import test_phaseful_early_result as base


env = base.env


@pytest.mark.parametrize("spec", base.SPECS, ids=("deep", "soul"))
@pytest.mark.parametrize("reserved", [False, True])
@pytest.mark.parametrize("change", ["running", "settled", "disabled", "timer", "probe", "anchor", "removed", "replaced"])
def test_query_cleanup_does_not_dispatch_over_newer_state(env, monkeypatch, spec, reserved, change):
    base.prepare_calibration(env.identity, spec, "probe" if reserved else "launch")
    expected = {}

    async def cleanup(_spec):
        if change == "running":
            _phaseful.mark_success(spec, base.NOW + 2, next_time=base.NOW + 12345)
        elif change == "settled":
            _phaseful.begin_post_summary_wait(spec, base.NOW + 2, confirmed=True)
        elif change == "disabled":
            env.identity[spec.enabled_key] = False
        elif change == "timer":
            env.identity[spec.next_time_key] = base.NOW + 777
        elif change == "probe":
            env.identity[spec.probe_pending_key] = not env.identity[spec.probe_pending_key]
        elif change == "anchor":
            env.identity[spec.last_summary_msg_id_key] = 9010
        elif change == "removed":
            state_module.remove_identity(base.ID)
        else:
            state_module._meta_state["identity_states"][base.ID] = copy.deepcopy(env.identity)
        expected.update(copy.deepcopy(state_module._meta_state))

    send = AsyncMock(return_value=SimpleNamespace(id=9001, sent_at=base.NOW + 3))
    monkeypatch.setattr(_phaseful, "delete_summary_trigger_msg", AsyncMock(side_effect=cleanup))
    monkeypatch.setattr(_phaseful, "send_game_command", send)
    assert not asyncio.run(_phaseful._send_active_summary_query(spec, base.NOW, probe_reserved=reserved))
    send.assert_not_awaited()
    env.recover.assert_not_called()
    _phaseful.send_audit_log.assert_not_awaited()
    assert state_module._meta_state == expected


@pytest.mark.parametrize("spec", base.SPECS, ids=("deep", "soul"))
@pytest.mark.parametrize("reserved", [False, True])
def test_query_cleanup_bookkeeping_and_profile_changes_allow_one_query(env, monkeypatch, spec, reserved):
    base.prepare_calibration(env.identity, spec, "probe" if reserved else "launch")
    env.identity["my_msg_ids"][9000] = {"cmd": ".fixture"}

    async def cleanup(_spec):
        env.identity["my_msg_ids"].pop(9000)
        env.identity["identity_name"] = "renamed"

    sent_at = base.NOW + 70
    send = AsyncMock(return_value=SimpleNamespace(id=9001, sent_at=sent_at))
    monkeypatch.setattr(_phaseful, "delete_summary_trigger_msg", AsyncMock(side_effect=cleanup))
    monkeypatch.setattr(_phaseful, "send_game_command", send)
    assert asyncio.run(_phaseful._send_active_summary_query(spec, base.NOW, probe_reserved=reserved))
    send.assert_awaited_once()
    assert env.identity[spec.phase_key] == "waiting_summary"
    assert env.identity[spec.summary_sent_at_key] == sent_at
    assert env.identity[spec.last_summary_msg_id_key] == 9001
    assert env.identity["identity_name"] == "renamed"
    assert 9000 not in env.identity["my_msg_ids"]


@pytest.mark.parametrize("reserved", [False, True])
def test_real_deep_success_during_query_cleanup_wins(env, monkeypatch, reserved):
    spec = deep_retreat.DEEP_RETREAT_SPEC
    base.prepare_calibration(env.identity, spec, "probe" if reserved else "launch")
    monkeypatch.setattr(deep_retreat, "save_state", Mock())
    monkeypatch.setattr(deep_retreat, "_record_deep_retreat_event", Mock())
    monkeypatch.setattr(deep_retreat, "_note_deep_retreat_remote_block", Mock())
    monkeypatch.setattr(deep_retreat, "send_audit_log", AsyncMock())
    expected = {}

    async def cleanup(_spec):
        assert await deep_retreat.handle_deep_retreat_success_reply(
            "\u4f60\u5df2\u8fdb\u5165\u6df1\u5ea6\u95ed\u5173\u72b6\u6001\uff0c"
            "\u795e\u9b42\u5c06\u81ea\u884c\u5410\u7eb3\uff0c8\u5c0f\u65f6\u540e\u7ed3\u675f\u3002",
            base.NOW + 2, SimpleNamespace(id=9000, raw_text=deep_retreat.CMD_DEEP_RETREAT),
            matched_family="deep_retreat",
        )
        expected.update(copy.deepcopy(env.identity))

    send = AsyncMock(return_value=SimpleNamespace(id=9001, sent_at=base.NOW + 3))
    monkeypatch.setattr(_phaseful, "delete_summary_trigger_msg", AsyncMock(side_effect=cleanup))
    monkeypatch.setattr(_phaseful, "send_game_command", send)
    assert not asyncio.run(_phaseful._send_active_summary_query(spec, base.NOW, probe_reserved=reserved))
    send.assert_not_awaited()
    assert env.identity == expected
