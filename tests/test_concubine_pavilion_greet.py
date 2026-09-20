import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from model import persistence, state as state_module
from model.features import concubine, concubine_affinity_actions as actions, tianjige_transport
from model.features.cave_treasure_miniapp import normalize_cave_tianjige_command
from tests.test_concubine_greet_lifecycle import (
    env, ID, ACCOUNT, NOW, NAME, FIELD, SUCCESS, ALREADY, send_greet, reply,
)


@pytest.fixture
def http(env, monkeypatch):
    monkeypatch.setattr(tianjige_transport, "available", lambda _: True)
    monkeypatch.setattr(tianjige_transport, "pavilion_available", lambda _: True)

    async def execute(identity_id, command, *, op_id, operation_check):
        assert operation_check()
        record = env.identity[FIELD]
        assert record["status"] == "sending" and record["transport"] == "miniapp"
        assert record["op_id"] == op_id and command == concubine.CMD_CONCUBINE_DAILY_GREET
        return {"terminal": True, "action_dispatched": True, "message": SUCCESS,
                "receipt": command_receipt(env, op_id)}

    async def read(identity_id, *, partner, op_id, operation_check):
        assert operation_check() and partner == NAME
        record = env.identity[FIELD]
        assert record["reconciliation"]["op_id"] == op_id
        return {"ok": True, "companion": {"partner": NAME, "greeted_today": True, "affinity": 300},
                "receipt": pavilion_receipt(env, op_id)}

    command = AsyncMock(side_effect=execute)
    pavilion = AsyncMock(side_effect=read)
    monkeypatch.setattr(tianjige_transport, "execute", command)
    monkeypatch.setattr(tianjige_transport, "read_pavilion", pavilion)
    return command, pavilion


def command_receipt(env, op_id):
    return {"transport": "miniapp", "send_as_id": ID, "account_id": ACCOUNT,
            "player_id": ID, "op_id": op_id, "command": concubine.CMD_CONCUBINE_DAILY_GREET,
            "started_at": env.clock[0], "received_at": env.clock[0] + 1}


def pavilion_receipt(env, op_id):
    return {"transport": "miniapp", "send_as_id": ID, "account_id": ACCOUNT,
            "player_id": ID, "op_id": op_id, "section": "pavilion",
            "started_at": env.clock[0], "received_at": env.clock[0] + 1}


def make_unknown(env, http):
    command, _pavilion = http
    command.side_effect = None
    command.return_value = {"action_dispatched": True}
    assert not asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == "unknown"
    env.clock[0] = NOW + actions.HTTP_RECONCILE_INTERVAL_SEC


def recover(env):
    with state_module.use_identity(ID):
        return asyncio.run(actions.recover(env.clock[0]))


def test_gate_revocation_before_dispatch_cancels_without_group_send(env, http, monkeypatch):
    command, _pavilion = http
    enabled = [True]
    monkeypatch.setattr(tianjige_transport, "pavilion_available", lambda _: enabled[0])

    async def revoke(_identity_id, _command, *, op_id, operation_check):
        enabled[0] = False
        assert not operation_check()
        return {"action_dispatched": False}

    command.side_effect = revoke
    assert not asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == "unsent"
    env.send.assert_not_awaited()


def test_disabling_new_pavilion_sends_does_not_block_unknown_reconciliation(env, http, monkeypatch):
    make_unknown(env, http)
    monkeypatch.setattr(tianjige_transport, "pavilion_available", lambda _: False)
    assert recover(env)
    assert env.identity[FIELD]["status"] == "reconciled"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("text,outcome", [(SUCCESS, "success"), (ALREADY, "daily_limit")])
def test_http_greet_reuses_owned_reducer_without_group_ids(env, http, text, outcome):
    command, _pavilion = http
    execute = command.side_effect
    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["message"] = text
        return result
    command.side_effect = changed
    assert normalize_cave_tianjige_command(concubine.CMD_CONCUBINE_DAILY_GREET)
    assert asyncio.run(send_greet(env))
    record = env.identity[FIELD]
    assert record["status"] == "complete" and record["result"]["outcome"] == outcome
    assert record["msg_id"] == record["reply_msg_id"] == 0
    assert env.identity["concubine_last_greet_day"] == concubine._local_day_key(NOW)
    assert env.identity["concubine_affinity"] == (300 if outcome == "success" else 270)
    env.send.assert_not_awaited()


def test_http_unsent_is_retryable_without_group_fallback(env, http):
    command, _pavilion = http
    command.side_effect = None
    command.return_value = {"action_dispatched": False}
    assert not asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == "unsent"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["next_concubine_time"] > NOW
    env.send.assert_not_awaited()


@pytest.mark.parametrize("field,value", [
    ("op_id", "foreign"), ("account_id", ACCOUNT + 1), ("player_id", True),
    ("command", ".我的侍妾"), ("started_at", NOW - 1),
])
def test_invalid_command_receipt_keeps_unknown(env, http, field, value):
    command, _pavilion = http
    execute = command.side_effect
    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["receipt"][field] = value
        return result
    command.side_effect = changed
    assert not asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity["concubine_last_greet_day"] == ""
    env.send.assert_not_awaited()


def test_unknown_greet_reconciles_from_authoritative_pavilion(env, http):
    command, pavilion = http
    make_unknown(env, http)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(env.clock[0])
        asyncio.run(concubine._run_concubine_scheduler(env.clock[0]))
    record = env.identity[FIELD]
    assert record["status"] == "reconciled" and "result" not in record
    assert record["reconciliation"]["companion"]["affinity"] == 300
    assert env.identity["concubine_affinity"] == 300
    assert env.identity["concubine_last_greet_day"] == record["day"]
    assert env.identity["concubine_phase"] == "idle"
    assert command.await_count == pavilion.await_count == 1
    env.send.assert_not_awaited()


def test_same_day_not_greeted_is_inconclusive_and_throttled(env, http):
    _command, pavilion = http
    make_unknown(env, http)
    execute = pavilion.side_effect
    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["companion"]["greeted_today"] = False
        result["companion"]["affinity"] = 270
        return result
    pavilion.side_effect = changed
    assert recover(env)
    before = copy.deepcopy(env.identity[FIELD])
    assert before["status"] == "unknown" and before["replay_after"] > env.clock[0]
    assert recover(env)
    assert env.identity[FIELD] == before and pavilion.await_count == 1
    assert not asyncio.run(reply(env))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("greeted", [False, True])
def test_next_day_snapshot_closes_old_unknown_without_duplicate(env, http, greeted):
    _command, pavilion = http
    make_unknown(env, http)
    env.clock[0] = NOW + 86400
    execute = pavilion.side_effect
    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["companion"].update(greeted_today=greeted, affinity=333)
        result["receipt"]["started_at"] = env.clock[0]
        result["receipt"]["received_at"] = env.clock[0] + 1
        return result
    pavilion.side_effect = changed
    assert recover(env)
    record = env.identity[FIELD]
    assert record["status"] == "reconciled"
    assert env.identity["concubine_affinity"] == 333
    assert env.identity["concubine_last_greet_day"] == (
        concubine._local_day_key(env.clock[0]) if greeted else "")
    assert pavilion.await_count == 1
    env.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["plan", "owner", "partner", "disabled"])
@pytest.mark.parametrize("recovery", [False, True])
def test_owner_or_plan_change_cannot_apply_http_result(env, http, monkeypatch, change, recovery):
    command, pavilion = http
    if recovery:
        make_unknown(env, http)
        target = pavilion
    else:
        target = command
    execute = target.side_effect
    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        if change == "plan":
            env.identity["concubine_tianji_enabled"] = False
        elif change == "owner":
            monkeypatch.setattr(concubine, "get_identity_account", lambda _: ACCOUNT + 1)
        elif change == "partner":
            env.identity["concubine_name"] = "replacement"
        else:
            monkeypatch.setattr(concubine, "get_identity_enabled", lambda _: False)
        return result
    target.side_effect = changed
    assert recover(env) if recovery else not asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] in {"sending", "unknown"}
    assert env.identity["concubine_last_greet_day"] == ""
    env.send.assert_not_awaited()


@pytest.mark.parametrize("field,value", [
    ("op_id", "f" * 32), ("account_id", ACCOUNT + 1), ("player_id", True),
    ("section", "inventory"), ("started_at", NOW),
])
def test_reconcile_rejects_forged_pavilion_receipt(env, http, field, value):
    _command, pavilion = http
    make_unknown(env, http)
    execute = pavilion.side_effect
    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["receipt"][field] = value
        return result
    pavilion.side_effect = changed
    assert recover(env)
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity["concubine_last_greet_day"] == ""


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_http_interruption_preserves_unknown_without_group_send(env, http, recovery, cancel):
    command, pavilion = http
    if recovery:
        make_unknown(env, http)
        target = pavilion
    else:
        target = command
    target.side_effect = asyncio.CancelledError() if cancel else RuntimeError("fixture")
    if cancel or not recovery:
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            recover(env) if recovery else asyncio.run(send_greet(env))
    else:
        assert recover(env)
    assert env.identity[FIELD]["status"] == "unknown"
    assert (ID, "greet") not in actions._INFLIGHT
    env.send.assert_not_awaited()


@pytest.mark.parametrize("complete", [False, True])
def test_http_greet_survives_sqlite_reload(env, http, complete):
    if complete:
        assert asyncio.run(send_greet(env))
    else:
        make_unknown(env, http)
    expected = copy.deepcopy(env.identity[FIELD])
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    with state_module.use_identity(ID):
        assert actions.records()["greet"] == expected
        concubine.restore_concubine_runtime(NOW + 60)
        asyncio.run(actions.recover(NOW + 120))
    assert env.identity[FIELD] == expected
    env.send.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["native", "receipt", "companion", "same_op", "old_read", "day"])
def test_corrupted_reconciled_greet_fails_closed(env, http, mutation):
    make_unknown(env, http)
    assert recover(env)
    record = env.identity[FIELD]
    probe = record["reconciliation"]
    if mutation == "native":
        record.pop("transport")
    elif mutation == "receipt":
        probe["receipt"]["account_id"] += 1
    elif mutation == "companion":
        probe["companion"]["partner"] = "other"
    elif mutation == "same_op":
        probe["op_id"] = probe["receipt"]["op_id"] = record["op_id"]
    elif mutation == "old_read":
        probe["started_at"] = record["started_at"]
    else:
        probe["observed_day"] = "2000-01-01"
    assert persistence.save_state() and persistence.load_state()
    with state_module.use_identity(ID):
        assert actions.records() is None
        assert actions.block_reason() == "invalid"


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("fail_save", [1, 2])
def test_save_failure_never_releases_or_duplicates_greet(env, http, monkeypatch, recovery, fail_save):
    command, pavilion = http
    if recovery:
        make_unknown(env, http)
        calls = []
    else:
        calls = []
    before = copy.deepcopy(env.identity)
    def save():
        calls.append(1)
        return len(calls) != fail_save
    monkeypatch.setattr(concubine, "save_state", save)
    if recovery:
        assert recover(env)
        if fail_save == 1:
            pavilion.assert_not_awaited()
        else:
            pavilion.assert_awaited_once()
    else:
        assert not asyncio.run(send_greet(env))
        if fail_save == 1:
            command.assert_not_awaited()
        else:
            command.assert_awaited_once()
    assert env.identity["concubine_last_greet_day"] == before["concubine_last_greet_day"]
    assert env.identity["concubine_affinity"] == before["concubine_affinity"]
    assert env.identity[FIELD]["status"] in {"sending", "unknown", "unsent"}
    env.send.assert_not_awaited()


@pytest.mark.parametrize("recovery", [False, True])
def test_reducer_exception_rolls_back_greet_projection(env, http, monkeypatch, recovery):
    if recovery:
        make_unknown(env, http)
        def broken(*args):
            env.identity["concubine_affinity"] = 999
            raise RuntimeError("fixture")
        monkeypatch.setattr(concubine, "_schedule_after_tianji", broken)
        assert recover(env)
    else:
        def broken(*args):
            env.identity["concubine_affinity"] = 999
            raise RuntimeError("fixture")
        monkeypatch.setattr(actions, "_apply_result", broken)
        with pytest.raises(RuntimeError):
            asyncio.run(send_greet(env))
    assert env.identity["concubine_affinity"] == 270
    assert env.identity["concubine_last_greet_day"] == ""
    assert env.identity[FIELD]["status"] in {"sending", "unknown"}
    assert (ID, "greet") not in actions._INFLIGHT
    env.send.assert_not_awaited()
