import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from model import persistence, state as state_module
from model.features import concubine, concubine_fragment_actions as actions, tianjige_transport
from tests.test_concubine_fragment_actions import (
    env, ID, ACCOUNT, NOW, NAME, FIELD, DREAM, PUZZLE, start,
)


def snapshot(*, fragments=None, dream_at=NOW - 86400, xutian_at=NOW - 86400, cangkun_at=NOW - 86400):
    return {
        "partner": NAME,
        "fragments": fragments or {"xutian": [2, 4], "cangkun": [1, 4]},
        "puzzle_completed": {"xutian": 2, "cangkun": 3},
        "last_dream_at": dream_at,
        "last_puzzle_at": {"xutian": xutian_at, "cangkun": cangkun_at},
    }


def command_receipt(env, op_id, command):
    return {
        "transport": "miniapp", "send_as_id": ID, "account_id": ACCOUNT, "player_id": ID,
        "op_id": op_id, "command": command, "started_at": env.clock[0],
        "received_at": env.clock[0] + 1,
    }


def pavilion_receipt(env, op_id):
    return {
        "transport": "miniapp", "send_as_id": ID, "account_id": ACCOUNT, "player_id": ID,
        "op_id": op_id, "section": "pavilion", "started_at": env.clock[0],
        "received_at": env.clock[0] + 1,
    }


@pytest.fixture
def http(env, monkeypatch):
    current_snapshot = [snapshot()]
    monkeypatch.setattr(tianjige_transport, "available", lambda _: True)
    monkeypatch.setattr(tianjige_transport, "pavilion_available", lambda _: True)

    async def execute(identity_id, command, *, op_id, operation_check):
        assert identity_id == ID and operation_check()
        record = env.identity[FIELD]["dream" if command == concubine.CMD_CONCUBINE_DREAM else "puzzle"]
        assert record["status"] == "sending" and record["transport"] == "miniapp" and record["op_id"] == op_id
        return {
            "terminal": True, "action_dispatched": True,
            "message": DREAM if command == concubine.CMD_CONCUBINE_DREAM else PUZZLE,
            "receipt": command_receipt(env, op_id, command),
        }

    async def read(identity_id, *, partner, op_id, operation_check, projection="companion"):
        assert identity_id == ID and partner == NAME and projection == "fragments" and operation_check()
        return {
            "ok": True, "fragments": copy.deepcopy(current_snapshot[0]),
            "receipt": pavilion_receipt(env, op_id),
        }

    command = AsyncMock(side_effect=execute)
    pavilion = AsyncMock(side_effect=read)
    monkeypatch.setattr(tianjige_transport, "execute", command)
    monkeypatch.setattr(tianjige_transport, "read_pavilion", pavilion)
    return command, pavilion, current_snapshot


def prepare_puzzle(env, http):
    _command, pavilion, current_snapshot = http
    with state_module.use_identity(ID):
        concubine._set_fragment_progress("xutian", 4, 4)
        concubine._set_fragment_progress("cangkun", 4, 4)
    env.clock[0] = NOW - 2
    current_snapshot[0] = snapshot(fragments={"xutian": [4, 4], "cangkun": [4, 4]})
    with state_module.use_identity(ID):
        assert asyncio.run(concubine._send_fragment_command(env.clock[0]))
        assert concubine._is_current_fragment_confirmed(env.clock[0])
    record = env.identity["concubine_status_query"]
    assert record["transport"] == "miniapp" and record["msg_id"] == record["reply_msg_id"] == 0
    env.clock[0] = NOW
    env.identity["next_concubine_time"] = NOW
    pavilion.reset_mock()


def recover(env):
    with state_module.use_identity(ID):
        return asyncio.run(actions.recover(env.clock[0]))


def test_fragment_confirmation_is_structured_http_read_without_group_anchor(env, http):
    _command, pavilion, current_snapshot = http
    with state_module.use_identity(ID):
        concubine._set_fragment_progress("xutian", 4, 4)
        concubine._set_fragment_progress("cangkun", 4, 4)
    current_snapshot[0] = snapshot(fragments={"xutian": [4, 4], "cangkun": [4, 4]})
    with state_module.use_identity(ID):
        assert asyncio.run(concubine._send_fragment_command(NOW))
        assert concubine._is_current_fragment_confirmed(NOW)
    record = env.identity["concubine_status_query"]
    assert record["status"] == "complete" and record["outcome"] == "panel"
    assert record["transport"] == "miniapp" and record["msg_id"] == record["reply_msg_id"] == 0
    assert record["confirmation_key"] == "xutian:4/4|cangkun:4/4"
    pavilion.assert_awaited_once()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
def test_fragment_http_mutation_reuses_native_reducer(env, http, kind):
    command, _pavilion, _snapshot = http
    if kind == "puzzle":
        prepare_puzzle(env, http)
    assert asyncio.run(start(env, kind))
    record = env.identity[FIELD][kind]
    assert record["status"] == "complete" and record["transport"] == "miniapp"
    assert record["msg_id"] == record["reply_msg_id"] == 0
    assert record["result"]["outcome"] == kind
    assert env.identity["concubine_phase"] == "idle"
    command.assert_awaited_once()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
def test_unknown_fragment_mutation_reconciles_only_from_new_server_event(env, http, kind):
    command, pavilion, current_snapshot = http
    if kind == "puzzle":
        prepare_puzzle(env, http)
    command.side_effect = None
    command.return_value = {"action_dispatched": True}
    assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unknown"
    env.clock[0] = NOW + actions.HTTP_RECONCILE_INTERVAL_SEC
    if kind == "dream":
        current_snapshot[0] = snapshot(
            fragments={"xutian": [3, 4], "cangkun": [1, 4]}, dream_at=NOW + 1,
        )
    else:
        current_snapshot[0] = snapshot(
            fragments={"xutian": [0, 4], "cangkun": [4, 4]}, xutian_at=NOW + 1,
        )
    assert recover(env)
    record = env.identity[FIELD][kind]
    assert record["status"] == "reconciled" and record["reconciliation"]["consumed"] == [kind if kind == "dream" else "xutian"]
    assert env.identity["concubine_phase"] == "idle"
    assert command.await_count == 1 and pavilion.await_count == 1
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
def test_inconclusive_fragment_snapshot_keeps_unknown_and_throttles_reads(env, http, kind):
    command, pavilion, _snapshot = http
    if kind == "puzzle":
        prepare_puzzle(env, http)
    command.side_effect = None
    command.return_value = {"action_dispatched": True}
    assert not asyncio.run(start(env, kind))
    env.clock[0] = NOW + actions.HTTP_RECONCILE_INTERVAL_SEC
    assert recover(env)
    before = copy.deepcopy(env.identity[FIELD][kind])
    assert before["status"] == "unknown" and before["replay_after"] > env.clock[0]
    assert recover(env) and env.identity[FIELD][kind] == before
    pavilion.assert_awaited_once()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
def test_future_fragment_event_cannot_reconcile_unknown_mutation(env, http, kind):
    command, _pavilion, current_snapshot = http
    if kind == "puzzle":
        prepare_puzzle(env, http)
    command.side_effect = None
    command.return_value = {"action_dispatched": True}
    assert not asyncio.run(start(env, kind))
    env.clock[0] = NOW + actions.HTTP_RECONCILE_INTERVAL_SEC
    future = env.clock[0] + actions.HTTP_EVENT_CLOCK_SKEW_SEC + 1
    current_snapshot[0] = snapshot(dream_at=future) if kind == "dream" else snapshot(
        fragments={"xutian": [0, 4], "cangkun": [4, 4]}, xutian_at=future,
    )
    assert recover(env)
    assert env.identity[FIELD][kind]["status"] == "unknown"


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
def test_fragment_http_records_survive_reload_without_telegram_adoption(env, http, kind):
    if kind == "puzzle":
        prepare_puzzle(env, http)
    assert asyncio.run(start(env, kind))
    expected = copy.deepcopy(env.identity[FIELD][kind])
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    with state_module.use_identity(ID):
        assert actions.records()[kind] == expected
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
@pytest.mark.parametrize("field,value", [
    ("op_id", "foreign"), ("account_id", ACCOUNT + 1), ("player_id", True),
    ("command", ".我的侍妾"), ("started_at", NOW - 1),
])
def test_forged_fragment_command_receipt_never_applies(env, http, kind, field, value):
    command, _pavilion, _snapshot = http
    if kind == "puzzle":
        prepare_puzzle(env, http)
    execute = command.side_effect

    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["receipt"][field] = value
        return result

    command.side_effect = changed
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unknown"
    assert env.identity["concubine_dream_due_at"] == before["concubine_dream_due_at"]
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
def test_forged_fragment_reconciliation_receipt_remains_unknown(env, http, kind):
    command, pavilion, current_snapshot = http
    if kind == "puzzle":
        prepare_puzzle(env, http)
    command.side_effect = None
    command.return_value = {"action_dispatched": True}
    assert not asyncio.run(start(env, kind))
    env.clock[0] = NOW + actions.HTTP_RECONCILE_INTERVAL_SEC
    current_snapshot[0] = snapshot(
        fragments={"xutian": [3, 4], "cangkun": [1, 4]}, dream_at=NOW + 1,
    ) if kind == "dream" else snapshot(
        fragments={"xutian": [0, 4], "cangkun": [4, 4]}, xutian_at=NOW + 1,
    )
    read = pavilion.side_effect

    async def changed(*args, **kwargs):
        result = await read(*args, **kwargs)
        result["receipt"]["account_id"] += 1
        return result

    pavilion.side_effect = changed
    assert recover(env)
    assert env.identity[FIELD][kind]["status"] == "unknown"
    assert env.identity["concubine_phase"] == kind + "_pending"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
@pytest.mark.parametrize("cancel", [False, True])
def test_fragment_http_interruption_preserves_unknown_without_group_fallback(env, http, kind, cancel):
    command, _pavilion, _snapshot = http
    if kind == "puzzle":
        prepare_puzzle(env, http)
    command.side_effect = asyncio.CancelledError() if cancel else RuntimeError("fixture")
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unknown"
    assert ID not in actions._INFLIGHT
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
@pytest.mark.parametrize("fail_save", [1, 2])
def test_fragment_http_save_failure_never_releases_or_duplicates(env, http, kind, fail_save):
    command, _pavilion, _snapshot = http
    if kind == "puzzle":
        prepare_puzzle(env, http)
    command.reset_mock()
    calls = []
    before = copy.deepcopy(env.identity)

    def save():
        calls.append(1)
        return len(calls) != fail_save

    concubine.save_state = save
    assert not asyncio.run(start(env, kind))
    assert command.await_count == fail_save - 1
    if fail_save == 1:
        assert env.identity == before
    else:
        assert env.identity[FIELD][kind]["status"] == "sending"
        assert env.identity["concubine_phase"] == kind + "_pending"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["dream", "puzzle"])
def test_corrupted_reconciled_fragment_record_fails_closed(env, http, kind):
    command, _pavilion, current_snapshot = http
    if kind == "puzzle":
        prepare_puzzle(env, http)
    command.side_effect = None
    command.return_value = {"action_dispatched": True}
    assert not asyncio.run(start(env, kind))
    env.clock[0] = NOW + actions.HTTP_RECONCILE_INTERVAL_SEC
    current_snapshot[0] = snapshot(
        fragments={"xutian": [3, 4], "cangkun": [1, 4]}, dream_at=NOW + 1,
    ) if kind == "dream" else snapshot(
        fragments={"xutian": [0, 4], "cangkun": [4, 4]}, xutian_at=NOW + 1,
    )
    assert recover(env)
    env.identity[FIELD][kind]["reconciliation"]["receipt"]["section"] = "inventory"
    assert persistence.save_state() and persistence.load_state()
    with state_module.use_identity(ID):
        assert actions.records() is None
        assert actions.block_reason() == "invalid"


def test_fragment_http_read_save_failure_never_reaches_pavilion(env, http):
    _command, pavilion, current_snapshot = http
    with state_module.use_identity(ID):
        concubine._set_fragment_progress("xutian", 4, 4)
    current_snapshot[0] = snapshot(fragments={"xutian": [4, 4], "cangkun": [1, 4]})
    before = copy.deepcopy(env.identity)
    env.save.return_value = False
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine._send_fragment_command(NOW))
    assert env.identity == before
    pavilion.assert_not_awaited()
    env.send.assert_not_awaited()


def test_malformed_http_fragment_query_fails_closed_without_raising(env, http):
    env.identity["concubine_status_query"] = {"kind": "fragment", "transport": "miniapp"}
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert concubine._status_query_record() is None
        concubine.restore_concubine_runtime(NOW + 1000)
    assert env.identity == before
    http[1].assert_not_awaited()
    env.send.assert_not_awaited()
