import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from model import persistence, state as state_module
from model.features import concubine, tianjige_transport
from model.features.cave_treasure_miniapp import normalize_cave_tianjige_command
from tests.test_concubine_divination_lifecycle import (
    env, base_env, ID, ACCOUNT, NOW, NAME, FIELD, COMMAND, SUCCESS, COOLDOWN, CHAIN, start, reply,
)


def status_text(*, wait="3小时", chain="无", name=NAME):
    return (f"你的道心侍妾: 【{name}】 (状态: 随行中)\n情缘值: 320\n"
            f"入梦寻图冷却: 2小时\n天机代卜冷却: {wait}\n"
            f"共历心劫冷却: 4小时\n天机代卜链: {chain}")


@pytest.fixture
def http(env, monkeypatch):
    monkeypatch.setattr(tianjige_transport, "available", lambda _: True)
    monkeypatch.setattr(tianjige_transport, "pavilion_available", lambda _: True)

    async def execute(identity_id, command, *, op_id, operation_check):
        assert operation_check()
        value = env.identity[FIELD]
        assert value["transport"] == "miniapp"
        if command == COMMAND:
            assert value["status"] == "sending" and value["op_id"] == op_id
            message = SUCCESS
        else:
            assert command == concubine.CMD_CONCUBINE_STATUS
            assert value["reconciliation"]["op_id"] == op_id
            assert value["replay_after"] > env.clock[0]
            message = status_text()
        return {"terminal": True, "action_dispatched": True, "message": message,
                "receipt": {"transport": "miniapp", "send_as_id": ID, "account_id": ACCOUNT,
                            "player_id": ID, "op_id": op_id, "command": command,
                            "started_at": env.clock[0], "received_at": env.clock[0] + 1}}
    mock = AsyncMock(side_effect=execute)
    monkeypatch.setattr(tianjige_transport, "execute", mock)
    return mock


def make_unknown(env, http):
    execute = http.side_effect
    http.side_effect = None
    http.return_value = {"action_dispatched": True}
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unknown"
    http.side_effect = execute
    env.clock[0] = NOW + 1800


def recover(env):
    with state_module.use_identity(ID):
        return asyncio.run(concubine.divination_actions.recover(env.clock[0]))


@pytest.mark.parametrize("outcome,text", [
    ("success", SUCCESS), ("cooldown", COOLDOWN),
    ("resource_shortage", "修为不足，代卜天机需消耗 180 修为。"),
    ("affinity_shortage", "情缘未至，无法为你卜算天机。"),
    ("voyage_lock", "侍妾正在远航途中，暂无法焚香代卜。"),
])
def test_http_result_reuses_business_reducer(env, http, outcome, text):
    execute = http.side_effect
    async def response(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["message"] = text
        return result
    http.side_effect = response
    assert normalize_cave_tianjige_command(COMMAND) == COMMAND
    assert asyncio.run(start(env))
    value = env.identity[FIELD]
    assert value["status"] == "complete" and value["result"]["outcome"] == outcome
    assert value["msg_id"] == value["reply_msg_id"] == 0
    with state_module.use_identity(ID):
        assert concubine.divination_actions.record() is not None
        assert not concubine.divination_actions.block_reason()
    if outcome in {"success", "cooldown"}:
        assert env.identity["concubine_tianji_due_at"] > NOW
    if outcome == "success":
        assert env.identity["concubine_tianji_chain"] == CHAIN
    if outcome == "resource_shortage":
        assert value["retry_at"] > NOW
    env.send.assert_not_awaited()


def test_unsent_releases_phase_but_does_not_fall_back(env, http):
    http.side_effect = None
    http.return_value = {"action_dispatched": False}
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unsent"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["next_concubine_time"] > NOW
    env.send.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("account_id", 123), ("player_id", True), ("op_id", "other"),
                                        ("started_at", NOW - 1), ("command", ".天机盘")])
def test_mismatched_http_receipt_is_unknown(env, http, field, value):
    execute = http.side_effect
    async def response(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["receipt"][field] = value
        return result
    http.side_effect = response
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity["concubine_tianji_due_at"] == NOW - 1
    env.send.assert_not_awaited()


@pytest.mark.parametrize("chain", ["无", f"{CHAIN}（剩余 2小时）"])
def test_unknown_reconciles_fresh_cooldown_without_claiming_spend(env, http, chain):
    make_unknown(env, http)
    execute = http.side_effect
    async def response(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["message"] = status_text(chain=chain)
        return result
    http.side_effect = response
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(env.clock[0])
        asyncio.run(concubine._run_concubine_scheduler(env.clock[0]))
    value = env.identity[FIELD]
    assert value["status"] == "reconciled"
    assert "result" not in value and "http_receipt" not in value
    assert env.identity["concubine_tianji_due_at"] > env.clock[0]
    assert env.identity["concubine_tianji_chain"] == ("" if chain == "无" else CHAIN)
    assert env.identity["concubine_phase"] == "idle"
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    with state_module.use_identity(ID):
        assert concubine.divination_actions.record()["status"] == "reconciled"
    assert not recover(env)
    assert http.await_count == 2
    env.send.assert_not_awaited()


@pytest.mark.parametrize("text", [status_text(wait="可用"), status_text(name="其他侍妾"),
                                status_text().replace("\n天机代卜链: 无", ""), SUCCESS,
                                status_text().replace("\n共历心劫冷却: 4小时", "")])
def test_inconclusive_panel_remains_unknown_with_backoff(env, http, text):
    make_unknown(env, http)
    execute = http.side_effect
    async def response(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["message"] = text
        return result
    http.side_effect = response
    assert recover(env)
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity[FIELD]["replay_after"] > env.clock[0]
    assert recover(env)
    assert http.await_count == 2
    assert not asyncio.run(reply(env))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["plan", "owner", "partner", "disabled"])
@pytest.mark.parametrize("recovery", [False, True])
def test_stale_result_cannot_mutate_replacement_plan(env, http, monkeypatch, change, recovery):
    if recovery:
        make_unknown(env, http)
    execute = http.side_effect
    async def response(*args, **kwargs):
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
    http.side_effect = response
    if recovery:
        assert recover(env)
    else:
        assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] in {"sending", "unknown"}
    assert env.identity["concubine_tianji_due_at"] == NOW - 1
    env.send.assert_not_awaited()


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("fail_save", [1, 2])
def test_save_failure_preserves_pending_or_no_send(env, http, monkeypatch, recovery, fail_save):
    if recovery:
        make_unknown(env, http)
    before = copy.deepcopy(env.identity)
    calls = []
    def save():
        calls.append(1)
        return len(calls) != fail_save
    monkeypatch.setattr(concubine, "save_state", save)
    if recovery:
        assert recover(env)
    else:
        assert not asyncio.run(start(env))
    assert env.identity["concubine_tianji_due_at"] == before["concubine_tianji_due_at"]
    if fail_save == 1:
        assert env.identity == before
    else:
        assert env.identity[FIELD]["status"] == ("unknown" if recovery else "sending")
    assert http.await_count == fail_save - 1 + int(recovery)
    env.send.assert_not_awaited()


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_interruption_keeps_unknown_and_no_replay(env, http, recovery, cancel):
    if recovery:
        make_unknown(env, http)
    http.side_effect = asyncio.CancelledError() if cancel else RuntimeError("fixture")
    if cancel or not recovery:
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            recover(env) if recovery else asyncio.run(start(env))
    else:
        assert recover(env)
    assert env.identity[FIELD]["status"] == "unknown"
    assert ID not in concubine.divination_actions._INFLIGHT
    env.send.assert_not_awaited()


def test_initial_recovery_delay_and_inflight_guard(env, http):
    make_unknown(env, http)
    env.clock[0] -= 1
    assert recover(env)
    http.assert_awaited_once()
    env.clock[0] += 1
    execute = http.side_effect
    async def response(*args, **kwargs):
        result = await execute(*args, **kwargs)
        assert await concubine.divination_actions.recover(env.clock[0] + 10000)
        return result
    http.side_effect = response
    assert recover(env)
    assert http.await_count == 2


@pytest.mark.parametrize("mutation", ["native", "receipt_owner", "command", "text", "effect", "same_op", "old_read"])
def test_corrupted_reconciliation_fails_closed(env, http, mutation):
    make_unknown(env, http)
    assert recover(env)
    value = env.identity[FIELD]
    probe = value["reconciliation"]
    if mutation == "native":
        value.pop("transport")
    elif mutation == "receipt_owner":
        probe["receipt"]["account_id"] += 1
    elif mutation == "command":
        probe["receipt"]["command"] = COMMAND
    elif mutation == "text":
        probe["text"] = "unrelated"
    elif mutation == "effect":
        probe["effect"]["chain"] = "other"
    elif mutation == "same_op":
        probe["op_id"] = probe["receipt"]["op_id"] = value["op_id"]
    else:
        probe["started_at"] = value["started_at"]
    assert persistence.save_state() and persistence.load_state()
    with state_module.use_identity(ID):
        assert concubine.divination_actions.record() is None
        assert concubine.divination_actions.block_reason() == "invalid"


@pytest.mark.parametrize("completed", [False, True])
def test_http_record_survives_reload_without_group_adoption(env, http, completed):
    if completed:
        assert asyncio.run(start(env))
    else:
        make_unknown(env, http)
    before = copy.deepcopy(env.identity[FIELD])
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    with state_module.use_identity(ID):
        assert concubine.divination_actions.record() == before
        concubine.restore_concubine_runtime(NOW + 60)
        asyncio.run(concubine.divination_actions.recover(NOW + 120))
    assert env.identity[FIELD] == before
    http.assert_awaited_once()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("op_id", "f" * 32), ("account_id", 123),
                                        ("started_at", NOW), ("command", COMMAND)])
def test_reconcile_requires_fresh_owned_read_receipt(env, http, field, value):
    make_unknown(env, http)
    execute = http.side_effect
    async def response(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["receipt"][field] = value
        return result
    http.side_effect = response
    assert recover(env)
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity["concubine_tianji_due_at"] == NOW - 1


def test_reconciled_cooldown_remains_send_floor(env, http):
    make_unknown(env, http)
    assert recover(env)
    due = env.identity[FIELD]["reconciliation"]["effect"]["due_at"]
    env.identity["concubine_tianji_due_at"] = 0
    with state_module.use_identity(ID):
        assert concubine.divination_actions.next_at() == due
    assert not asyncio.run(start(env))
    assert http.await_count == 2


@pytest.mark.parametrize("recovery", [False, True])
def test_http_reducer_failure_rolls_back_projection(env, http, monkeypatch, recovery):
    if recovery:
        make_unknown(env, http)
    def broken(*args):
        env.identity["concubine_tianji_due_at"] = NOW + 10000
        raise RuntimeError("fixture")
    monkeypatch.setattr(concubine, "_schedule_after_tianji", broken)
    if recovery:
        assert recover(env)
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(start(env))
    assert env.identity["concubine_tianji_due_at"] == NOW - 1
    assert env.identity["concubine_phase"] == "tianji_pending"
    assert env.identity[FIELD]["status"] == ("unknown" if recovery else "sending")
    assert ID not in concubine.divination_actions._INFLIGHT
