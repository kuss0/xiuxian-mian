import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence
from model import state as state_module
from model.features import explore_rift


NOW = 1791414465.0
IDENTITY = 990580001


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, 7551)
    state_module.update_send_as_profile(IDENTITY, realm="\u5143\u5a74\u521d\u671f", xiuwei_current=1000)
    identity = state_module.get_identity_state(IDENTITY)
    identity.update(explore_rift_enabled=True, tianxing_enabled=True)
    send = AsyncMock(return_value=SimpleNamespace(id=5701, chat_id=-100570001, sent_at=NOW))
    ready = Mock(return_value=True)
    prepare = AsyncMock(return_value=True)
    real_prepare = explore_rift._prepare_explore_rift_tianxing_route
    monkeypatch.setattr(explore_rift, "send_game_command", send)
    monkeypatch.setattr(explore_rift, "save_state", Mock(return_value=True))
    monkeypatch.setattr(explore_rift, "console_log", Mock())
    monkeypatch.setattr(explore_rift, "send_audit_log", AsyncMock())
    monkeypatch.setattr(explore_rift, "_tianxing_explore_change_ready", ready)
    monkeypatch.setattr(explore_rift, "_prepare_explore_rift_tianxing_route", prepare)
    monkeypatch.setattr(explore_rift, "build_tianxing_consume_window", Mock(return_value=[{}]))
    monkeypatch.setattr(explore_rift.time, "time", lambda: NOW)
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "rift.db"))
    with state_module.use_identity(IDENTITY):
        yield SimpleNamespace(identity=identity, send=send, ready=ready, prepare=prepare, real_prepare=real_prepare)
    explore_rift._EXPLORE_RIFT_LOCKS.clear()
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


@pytest.mark.parametrize("label", [
    "\u5929\u661f\u65f6\u95f4\u7ebf\uff1asent_waiting_ack",
    "\u5929\u661f\u65f6\u95f4\u7ebf\uff1aneed_tianji_for_change",
    "\u5929\u661f\u5148\u70bc\u5236\u6d88\u8d39\u63a8\u547d\uff1awaiting_reply",
])
@pytest.mark.parametrize("reload", [False, True])
def test_ready_route_never_pulls_future_business_deadline(env, label, reload):
    due = NOW + 579
    env.identity.update(next_explore_rift_time=due, explore_rift_last_result=label)
    if reload:
        assert persistence.save_state()
        assert persistence.load_state()
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    env.send.assert_not_awaited()
    assert state_module.state["next_explore_rift_time"] == due


@pytest.mark.parametrize("offset", [-1, 30, 600])
@pytest.mark.parametrize("shortage", [False, True])
def test_preparation_retry_does_not_rewrite_business_deadline(env, offset, shortage):
    due = NOW + offset
    env.identity["next_explore_rift_time"] = due
    if shortage:
        explore_rift._schedule_explore_rift_tianji_wait(NOW, due)
    else:
        explore_rift._schedule_explore_rift_tianxing_prepare_retry(NOW, due)
    assert env.identity["next_explore_rift_time"] == due
    assert env.identity["explore_rift_tianxing_prepare_retry_at"] > NOW


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("reload", [False, True])
def test_due_rift_retries_preparation_only_when_due_or_ready(env, ready, reload):
    env.identity.update(next_explore_rift_time=NOW - 1,
                        explore_rift_tianxing_prepare_retry_at=NOW + 3600,
                        deep_retreat_phase="running", next_deep_retreat_time=NOW + 86400)
    env.ready.return_value = ready
    if reload:
        assert persistence.save_state()
        assert persistence.load_state()
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    if ready:
        env.prepare.assert_awaited_once()
        env.send.assert_awaited_once()
    else:
        env.prepare.assert_not_awaited()
        env.send.assert_not_awaited()
        assert state_module.state["next_explore_rift_time"] == NOW - 1


def test_preparation_retry_expiry_wakes_due_rift(env):
    env.identity.update(next_explore_rift_time=NOW - 1,
                        explore_rift_tianxing_prepare_retry_at=NOW)
    env.ready.return_value = False
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    env.prepare.assert_awaited_once()
    env.send.assert_awaited_once()


@pytest.mark.parametrize("offset", [-1, 579])
@pytest.mark.parametrize("phase", ["sent_waiting_ack", "need_tianji_for_change"])
def test_real_preparation_then_ready_respects_original_deadline(env, monkeypatch, offset, phase):
    due = NOW + offset
    env.identity["next_explore_rift_time"] = due
    env.ready.return_value = False
    monkeypatch.setattr(explore_rift, "_prepare_explore_rift_tianxing_route", env.real_prepare)
    preflight = Mock(return_value={"route_allowed": False, "timeline_required": True})
    timeline = AsyncMock(return_value={"phase": phase, "changed": True})
    monkeypatch.setattr(explore_rift, "build_tianxing_route_preflight_plan", preflight)
    monkeypatch.setattr(explore_rift, "run_tianxing_timeline_scheduler", timeline)
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    assert env.identity["next_explore_rift_time"] == due
    assert env.identity["explore_rift_tianxing_prepare_retry_at"] > NOW
    env.send.assert_not_awaited()
    assert persistence.save_state()
    assert persistence.load_state()
    env.ready.return_value = True
    preflight.return_value = {"route_allowed": True}
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW + 1))
    if offset > 0:
        env.send.assert_not_awaited()
        assert state_module.state["next_explore_rift_time"] == due
        asyncio.run(explore_rift.run_explore_rift_scheduler(due))
    env.send.assert_awaited_once()
    timeline.assert_awaited_once()


def test_official_cooldown_survives_preparation_label_and_reload(env):
    env.identity["next_explore_rift_time"] = NOW - 1
    text = "\u7a7a\u95f4\u88c2\u7f1d\u5c1a\u672a\u7a33\u5b9a\u3002\u8bf7\u5728 3\u5206\u949f41\u79d2 \u540e\u518d\u884c\u63a2\u5bfb\u3002"
    reply = SimpleNamespace(id=5701, chat_id=-100570001, raw_text=explore_rift.CMD_EXPLORE_RIFT)
    context = {"send_as_id": IDENTITY, "chat_id": reply.chat_id, "root_msg_id": reply.id,
               "reply_to_msg_id": reply.id, "msg_id": 5702, "server_event_at": NOW,
               "processed_at": NOW, "event_type": "message"}
    assert asyncio.run(explore_rift.handle_explore_rift_reply(
        text, NOW, reply_to=reply, matched_family="explore_rift", result_msg_id=5702,
        reply_context=context,
    ))
    due = NOW + 221 + explore_rift.CD_BUFFER_SEC
    assert env.identity["next_explore_rift_time"] == due
    env.identity["explore_rift_last_result"] = "\u5929\u661f\u65f6\u95f4\u7ebf\uff1asent_waiting_ack"
    assert persistence.save_state()
    assert persistence.load_state()
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW + 1))
    env.send.assert_not_awaited()
    assert state_module.state["next_explore_rift_time"] == due


@pytest.mark.parametrize("ready,pending", [(False, False), (True, False), (False, True)])
def test_fast_scan_waiting_preparation_does_not_starve_next_identity(env, monkeypatch, ready, pending):
    from model import app

    other_id = IDENTITY + 1
    state_module.set_identity_account(other_id, 7551)
    state_module.get_identity_state(other_id).update(explore_rift_enabled=True, next_explore_rift_time=NOW - 1)
    env.identity.update(next_explore_rift_time=NOW - 2,
                        explore_rift_tianxing_prepare_retry_at=NOW + 3600)
    if pending:
        env.identity.update(explore_rift_reply_to_msg_id=5701, explore_rift_reply_due_at=NOW - 1)
    env.ready.return_value = ready
    monkeypatch.setattr(app, "get_identity_ids", lambda: [IDENTITY, other_id])
    monkeypatch.setattr(app, "get_identity_enabled", lambda _: True)
    monkeypatch.setattr(app, "_is_identity_account_offline", lambda _: False)
    monkeypatch.setattr(app, "is_identity_weak", lambda *_: False)
    selected = []

    async def record(_now):
        selected.append(state_module.get_current_identity_id())

    monkeypatch.setattr(app, "run_explore_rift_scheduler", record)
    asyncio.run(app._run_due_explore_rift_schedulers(NOW, limit=1))
    assert selected == [IDENTITY if ready or pending else other_id]
