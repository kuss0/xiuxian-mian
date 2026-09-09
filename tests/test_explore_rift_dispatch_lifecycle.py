import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, runtime
from model import state as state_module
from model.features import explore_rift, tianxing


NOW = 1780000000.0
IDENTITY = 990580001
ACCOUNT = 7551
CHAT = -100580001
BOT = 880580001
ROOT = 5801
RESULT = 5802
ROUTE = "\u63a2\u7d22"
SOURCE = "\u63a2\u5bfb\u88c2\u7f1d"
FINAL = "\u3010\u906d\u9047\u98ce\u66b4\u3011\n\u4fee\u4e3a\u5012\u9000\u4e86 300 \u70b9\uff01"
START = "\u4f60\u8fd0\u8f6c\u5168\u8eab\u6cd5\u529b\uff0c\u6495\u5f00\u4e00\u9053\u6f06\u9ed1\u7684\u7a7a\u95f4\u88c2\u7f1d"
CD = "\u7a7a\u95f4\u88c2\u7f1d\u5c1a\u672a\u7a33\u5b9a\uff0c\u8bf7\u7b49\u5f85 1\u5c0f\u65f6\u3002"


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_bot_ids([BOT])
    state_module.set_game_group_route_config({"primary_group_id": CHAT})
    state_module.update_send_as_profile(IDENTITY, realm="\u5143\u5a74\u521d\u671f", xiuwei_current=1000)
    identity = state_module.get_identity_state(IDENTITY)
    identity.update(explore_rift_enabled=True, tianxing_enabled=False, next_explore_rift_time=NOW - 1)
    send = AsyncMock(return_value=SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW, send_started_at=NOW))
    audit = AsyncMock()
    prepare = AsyncMock(return_value=True)
    monkeypatch.setattr(explore_rift, "send_game_command", send)
    monkeypatch.setattr(explore_rift, "send_audit_log", audit)
    monkeypatch.setattr(explore_rift, "save_state", Mock(return_value=True))
    monkeypatch.setattr(explore_rift, "console_log", Mock())
    monkeypatch.setattr(explore_rift, "classify_game_send_block", Mock(return_value={"status": "unknown", "code": "send_timeout"}))
    monkeypatch.setattr(explore_rift, "_prepare_explore_rift_tianxing_route", prepare)
    monkeypatch.setattr(explore_rift.random, "uniform", lambda *_args: 0)
    monkeypatch.setattr(explore_rift.time, "time", lambda: NOW)
    monkeypatch.setattr(explore_rift, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runtime, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "dispatch.db"))
    with state_module.use_identity(IDENTITY):
        yield SimpleNamespace(identity=identity, send=send, audit=audit, prepare=prepare, path=tmp_path)
    explore_rift._EXPLORE_RIFT_LOCKS.clear()
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def operation():
    return state_module.state.get("tianxing_observation", {}).get("explore_rift_unknown_snapshot", {})


def register_receipt(op_id="", *, msg_id=ROOT):
    return runtime._finalize_game_command_sent(
        explore_rift.CMD_EXPLORE_RIFT, msg_id=msg_id, sent_at=NOW, send_started_at=NOW,
        send_as_id=IDENTITY, game_group_id=CHAT, topic_id=0, track=False,
        send_intent={"source_module": SOURCE, "op_id": op_id},
    )


def deliver(text=FINAL, **context):
    return explore_rift.handle_explore_rift_reply(
        text, NOW + 2, matched_family="explore_rift", result_msg_id=RESULT,
        reply_context={
            "send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT,
            "reply_to_msg_id": ROOT, "msg_id": RESULT, "family": "explore_rift",
            "server_event_at": NOW + 1, "processed_at": NOW + 2, "event_type": "edit", **context,
        },
    )


def test_dispatch_persists_before_send_and_cancellation_survives_reload(env, monkeypatch):
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)

    async def cancelled(*args, **kwargs):
        assert operation()["command_op_id"] == kwargs["op_id"]
        assert operation()["command_status"] == "sending"
        assert kwargs["operation_check"]()
        raise asyncio.CancelledError()

    env.send.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    assert persistence.load_state()
    assert operation()["command_status"] in {"sending", "unknown"}
    env.send.side_effect = None
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW + 86400))
    assert env.send.await_count == 1
    assert operation()


def test_unsaved_operation_never_dispatches(env, monkeypatch):
    monkeypatch.setattr(explore_rift, "save_state", Mock(return_value=False))
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("result", [FINAL, START, CD])
@pytest.mark.parametrize("receipt", ["sent", "unknown"])
def test_early_result_is_not_overwritten_by_transport_return(env, result, receipt):
    expected = {}

    async def early(*args, **kwargs):
        msg = register_receipt(kwargs.get("op_id", ""))
        assert await deliver(result)
        expected.update(copy.deepcopy(env.identity))
        return msg if receipt == "sent" else None

    env.send.side_effect = early
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    for key in (
        "explore_rift_reply_to_msg_id", "explore_rift_pending_result_msg_id", "explore_rift_reply_due_at",
        "next_explore_rift_time", "explore_rift_last_result", "explore_rift_last_result_key",
    ):
        assert env.identity[key] == expected[key], key
    if result != START:
        assert not operation()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "new_operation"])
def test_late_dispatch_receipt_cannot_update_another_owner(env, change):
    expected = {}

    async def changed(*args, **kwargs):
        if change in {"removed", "replaced"}:
            state_module.remove_identity(IDENTITY)
            target = IDENTITY if change == "replaced" else IDENTITY + 1
            state_module.set_identity_account(target, ACCOUNT)
            current = state_module.get_identity_state(target)
            current.update(copy.deepcopy(env.identity))
        else:
            target, current = IDENTITY, env.identity
            if change == "rebound":
                state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
            else:
                operation()["op_id"] = "replacement"
        expected.update(target=target, state=copy.deepcopy(current))
        return SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW)

    env.send.side_effect = changed
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    assert state_module.get_identity_state(expected["target"]) == expected["state"]


@pytest.mark.parametrize("change", ["module", "global", "account", "clock", "manual", "tianxing", "resource", "realm"])
def test_queued_dispatch_rechecks_business_admission(env, monkeypatch, change):
    async def queued(*args, **kwargs):
        assert kwargs["operation_check"]()
        if change == "module":
            env.identity["explore_rift_enabled"] = False
        elif change == "global":
            state_module.set_global_enabled(False)
        elif change == "account":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        elif change == "clock":
            env.identity["next_explore_rift_time"] = NOW + 36000
        elif change == "manual":
            env.identity["explore_rift_manual_required"] = True
        elif change == "tianxing":
            env.identity["tianxing_enabled"] = True
        elif change == "resource":
            state_module.update_send_as_profile(IDENTITY, xiuwei_current=600000)
        else:
            state_module.update_send_as_profile(IDENTITY, realm="\u7ed3\u4e39\u521d\u671f")
        assert not kwargs["operation_check"]()
        return None

    monkeypatch.setattr(explore_rift, "classify_game_send_block", lambda *_: {"status": "unsent", "code": "operation_invalidated"})
    env.send.side_effect = queued
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    assert env.send.await_count == 1
    if change == "manual":
        assert env.identity["explore_rift_manual_required"]


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module", "global"])
def test_preparation_cannot_release_invalidated_identity(env, change):
    async def prepared(*args, **kwargs):
        if change in {"removed", "replaced"}:
            state_module.remove_identity(IDENTITY)
            target = IDENTITY if change == "replaced" else IDENTITY + 1
            state_module.set_identity_account(target, ACCOUNT)
            state_module.get_identity_state(target).update(copy.deepcopy(env.identity))
        elif change == "rebound":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        elif change == "module":
            env.identity["explore_rift_enabled"] = False
        else:
            state_module.set_global_enabled(False)
        return True

    env.prepare.side_effect = prepared
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("receipt", [
    SimpleNamespace(id=True, chat_id=CHAT, sent_at=NOW),
    SimpleNamespace(id="5801", chat_id=CHAT, sent_at=NOW),
    SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=0),
    SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW - 120),
    SimpleNamespace(id=ROOT, chat_id=0, sent_at=NOW),
    SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + 36000),
    SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=float("nan")),
])
def test_malformed_receipt_stays_unknown_and_never_retries(env, receipt):
    env.send.return_value = receipt
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW + 86400))
    assert env.send.await_count == 1
    assert operation() and not env.identity["explore_rift_reply_to_msg_id"]


def test_unrecognized_send_block_is_not_permission_to_retry(env, monkeypatch):
    env.send.return_value = None
    monkeypatch.setattr(explore_rift, "classify_game_send_block", lambda *_: {"code": "unexpected_result"})
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW + 86400))
    assert env.send.await_count == 1
    assert operation()


def test_unknown_dispatch_does_not_adopt_another_manual_command(env):
    env.send.return_value = None
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    register_receipt("different-operation")
    assert not asyncio.run(deliver())
    assert operation()


def test_cancelled_dispatch_can_reconcile_detached_receipt_after_reload(env, monkeypatch):
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
    captured = {}

    async def cancelled(*args, **kwargs):
        captured["op_id"] = kwargs["op_id"]
        raise asyncio.CancelledError()

    env.send.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    assert persistence.load_state()
    register_receipt(captured["op_id"])
    day = datetime.fromtimestamp(NOW, explore_rift.TZ_LOCAL).date().isoformat()
    path = env.path / f"{day}.log"
    row = {
        "ts": datetime.fromtimestamp(NOW + 1, explore_rift.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "server_event_at": NOW + 1, "event_type": "edit", "text": FINAL,
        "message_id": RESULT, "chat_id": CHAT, "sender_id": BOT, "reply_to_msg_id": ROOT,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW + 601))
    assert not operation()
    assert state_module.state["next_explore_rift_time"] == NOW + 1 + explore_rift.EXPLORE_RIFT_CD
    assert env.send.await_count == 1


@pytest.mark.parametrize("lease", [False, True])
def test_tianxing_holds_pending_rift_but_admits_its_original_dispatch(env, lease):
    env.identity["tianxing_enabled"] = True
    state_module.update_send_as_profile(IDENTITY, sect_name="\u5929\u661f\u5b97")
    explore_rift._mark_explore_rift_send_unknown(NOW)
    operation().update(command_op_id="rift-send", command_status="sending", command_started_at=NOW)
    if lease:
        env.identity["tianxing_timeline_state"] = {"released_routes": {ROUTE: {"released_at": NOW, "reason": SOURCE}}}
    for op_id, source, expected in (("rift-send", SOURCE, True), ("other", SOURCE, False), ("rift-send", "other", False)):
        result = tianxing.tianxing_route_pre_send_guard(
            explore_rift.CMD_EXPLORE_RIFT, send_as_id=IDENTITY,
            intent={"op_id": op_id, "source_module": source}, now=NOW,
        )
        assert result["allowed"] is expected
    operation()["command_status"] = "unknown"
    assert not tianxing.tianxing_route_pre_send_guard(
        explore_rift.CMD_EXPLORE_RIFT, send_as_id=IDENTITY,
        intent={"op_id": "rift-send", "source_module": SOURCE}, now=NOW,
    )["allowed"]
    assert tianxing.tianxing_route_pre_send_guard(
        ".\u6df1\u5ea6\u95ed\u5173", send_as_id=IDENTITY, now=NOW,
    )["allowed"]


def test_tianxing_preflight_does_not_forget_pending_rift_when_lease_expires(env):
    env.identity["tianxing_enabled"] = True
    state_module.update_send_as_profile(IDENTITY, sect_name="\u5929\u661f\u5b97")
    explore_rift._mark_explore_rift_send_unknown(NOW)
    observed = env.identity["tianxing_observation"]
    observed.update(
        current_prediction=ROUTE, current_prediction_set_at=NOW - 30, current_prediction_until=NOW + 3600,
        current_change=ROUTE, current_change_set_at=NOW - 30, current_change_until=NOW + 7200,
    )
    plan = tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)
    assert not plan["route_allowed"]
    assert plan["stage"] == "rift_pending"


@pytest.mark.parametrize("xiuwei", [1000, 500000])
def test_own_dispatch_revalidates_real_tianxing_protection(env, xiuwei):
    env.identity["tianxing_enabled"] = True
    state_module.update_send_as_profile(IDENTITY, sect_name="\u5929\u661f\u5b97", xiuwei_current=xiuwei)
    env.identity["tianxing_observation"] = {
        "current_prediction": ROUTE, "current_prediction_set_at": NOW - 30, "current_prediction_until": NOW + 3600,
        "current_change": ROUTE, "current_change_set_at": NOW - 30, "current_change_until": NOW + 7200,
    }

    async def checked(*args, **kwargs):
        assert kwargs["operation_check"]()
        assert tianxing.tianxing_route_pre_send_guard(
            explore_rift.CMD_EXPLORE_RIFT, send_as_id=IDENTITY,
            intent={"op_id": kwargs["op_id"], "source_module": SOURCE}, now=NOW,
        )["allowed"]
        env.identity["tianxing_observation"]["current_change_until"] = NOW - 1
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = checked
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("pause", ["module", "global"])
def test_post_dispatch_pause_retains_receipt_and_accepts_final(env, pause):
    async def paused(*args, **kwargs):
        msg = register_receipt(kwargs["op_id"])
        if pause == "module":
            env.identity["explore_rift_enabled"] = False
        else:
            state_module.set_global_enabled(False)
        return msg

    env.send.side_effect = paused
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    assert env.identity["explore_rift_reply_to_msg_id"] == ROOT
    assert operation()["command_status"] == "sent"
    assert asyncio.run(deliver())
    assert not operation()
    assert env.identity["next_explore_rift_time"] == NOW + 1 + explore_rift.EXPLORE_RIFT_CD
    if pause == "module":
        assert not env.identity["explore_rift_enabled"]
    else:
        assert not state_module.get_global_enabled()


@pytest.mark.parametrize("change", ["rebound", "replaced"])
def test_lock_wait_keeps_original_identity_owner(env, change):
    async def scenario():
        lock = explore_rift._explore_rift_lock()
        await lock.acquire()
        task = asyncio.create_task(explore_rift.run_explore_rift_scheduler(NOW))
        await asyncio.sleep(0)
        if change == "rebound":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        else:
            state_module.remove_identity(IDENTITY)
            state_module.set_identity_account(IDENTITY, ACCOUNT)
            state_module.get_identity_state(IDENTITY).update(copy.deepcopy(env.identity))
        lock.release()
        await task

    asyncio.run(scenario())
    env.send.assert_not_awaited()
