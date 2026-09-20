import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from model import persistence, state as state_module
from model.features import concubine, tianjige_transport
from tests.test_concubine_voyage_actions import (
    env, base_env, ID, ACCOUNT, NOW, FIELD, KINDS, STARTED, RETURNED, NAME, ROUTE, prepare, start,
)


@pytest.fixture
def http(env, monkeypatch):
    monkeypatch.setattr(tianjige_transport, "available", lambda identity_id: True)
    monkeypatch.setattr(tianjige_transport, "pavilion_available", lambda identity_id: True)
    async def execute(identity_id, command, *, op_id, operation_check):
        assert operation_check()
        record = next(r for r in env.identity[FIELD].values() if r["op_id"] == op_id)
        assert record["status"] == "sending" and record["transport"] == "miniapp"
        return {"ok": True, "terminal": True, "action_dispatched": True,
                "message": STARTED if record["kind"] == "voyage" else RETURNED,
                "receipt": {"transport": "miniapp", "send_as_id": ID, "account_id": ACCOUNT,
                            "player_id": ID, "op_id": op_id, "command": command,
                            "started_at": env.clock[0], "received_at": env.clock[0] + 1}}
    mock = AsyncMock(side_effect=execute)
    monkeypatch.setattr(tianjige_transport, "execute", mock)
    return mock


@pytest.mark.parametrize("kind", KINDS)
def test_http_voyage_complete_with_no_fabricated_message(env, http, kind):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    record = env.identity[FIELD][kind]
    assert record["status"] == "complete" and record["msg_id"] == record["reply_msg_id"] == 0
    with state_module.use_identity(ID):
        assert concubine.voyage_actions.records() is not None
        assert not concubine.voyage_actions.block_reason()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_http_unknown_is_never_replayed_via_group(env, http, kind):
    prepare(env, kind)
    http.side_effect = None
    http.return_value = {"ok": False, "action_dispatched": True, "outcome_unknown": True}
    assert not asyncio.run(start(env, kind))
    record = copy.deepcopy(env.identity[FIELD][kind])
    assert record["status"] == "unknown"
    env.clock[0] = NOW + 10000
    with state_module.use_identity(ID):
        assert asyncio.run(concubine.voyage_actions.recover(env.clock[0]))
    after = env.identity[FIELD][kind]
    assert after["status"] == "unknown" and after["op_id"] == record["op_id"]
    assert after["replay_after"] > env.clock[0]
    assert http.await_count == 2
    assert http.await_args.args[1] == concubine.CMD_CONCUBINE_VOYAGE_STATUS
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_http_voyage_unsent_is_retryable_without_group_send(env, http, kind):
    prepare(env, kind)
    http.side_effect = None
    http.return_value = {"ok": False, "action_dispatched": False}
    assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unsent"
    assert env.identity["next_concubine_time"] > NOW
    env.send.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("account_id", 123), ("player_id", True), ("op_id", "foreign")])
def test_http_voyage_rejects_invalid_receipt(env, http, field, value):
    execute = http.side_effect
    async def changed(*args, **kwargs):
        response = await execute(*args, **kwargs)
        response["receipt"][field] = value
        return response
    http.side_effect = changed
    assert not asyncio.run(start(env, "voyage"))
    assert env.identity[FIELD]["voyage"]["status"] == "unknown"
    assert env.identity["concubine_voyage_status"] == "idle"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("unknown", [True, False])
def test_http_voyage_survives_sqlite_reload(env, http, kind, unknown):
    prepare(env, kind)
    if unknown:
        http.side_effect = None
        http.return_value = {"ok": False, "action_dispatched": True}
    asyncio.run(start(env, kind))
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity[FIELD][kind]["status"] == ("unknown" if unknown else "complete")
    with state_module.use_identity(ID):
        assert concubine.voyage_actions.records() is not None
        concubine.restore_concubine_runtime(NOW + 60)
        asyncio.run(concubine.voyage_actions.recover(NOW + 120))
    http.assert_awaited_once()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_save_failure_preserves_original_pending_instead_of_resending(env, http, kind, monkeypatch):
    prepare(env, kind)
    saves = []
    def save():
        saves.append(1)
        return len(saves) == 1
    monkeypatch.setattr(concubine, "save_state", save)
    assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "sending"
    http.assert_awaited_once()
    env.send.assert_not_awaited()


def unknown(env, http, kind):
    prepare(env, kind)
    http.side_effect = None
    http.return_value = {"ok": False, "action_dispatched": True}
    assert not asyncio.run(start(env, kind))
    env.clock[0] = NOW + 10000


def panel(http, env, kind, text=None):
    async def execute(identity_id, command, *, op_id, operation_check):
        assert command == concubine.CMD_CONCUBINE_VOYAGE_STATUS
        assert operation_check()
        record = env.identity[FIELD][kind]
        assert record["reconciliation"]["op_id"] == op_id
        assert record["replay_after"] > env.clock[0]
        return {"terminal": True, "action_dispatched": True,
                "message": text or (f"远航状态: {ROUTE}航线进行中，剩余约 319 分钟。" if kind == "voyage"
                                    else f"侍妾【{NAME}】当前并未执行远航任务。"),
                "receipt": {"transport": "miniapp", "send_as_id": ID, "account_id": ACCOUNT,
                            "player_id": ID, "op_id": op_id, "command": command,
                            "started_at": env.clock[0], "received_at": env.clock[0] + 1}}
    http.side_effect = execute
    return execute


def recover(env):
    with state_module.use_identity(ID):
        return asyncio.run(concubine.voyage_actions.recover(env.clock[0]))


@pytest.mark.parametrize("kind", KINDS)
def test_http_unknown_reconciles_from_owned_panel_without_rewards(env, http, kind):
    unknown(env, http, kind)
    original_affinity = env.identity["concubine_affinity"]
    panel(http, env, kind)
    assert recover(env)
    record = env.identity[FIELD][kind]
    assert record["status"] == "reconciled" and record["msg_id"] == 0
    assert "result" not in record and "http_receipt" not in record
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_voyage_status"] == ("sailing" if kind == "voyage" else "idle")
    assert env.identity["concubine_affinity"] == original_affinity
    if kind == "voyage_return":
        assert env.identity["concubine_last_snapshot_at"] == 0
        assert env.identity["concubine_availability"] == "unknown"
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    with state_module.use_identity(ID):
        assert concubine.voyage_actions.records()[kind]["status"] == "reconciled"
        assert not concubine.voyage_actions.block_reason()
    assert not recover(env)
    assert http.await_count == 2
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind,text", [
    ("voyage", f"侍妾【{NAME}】当前并未执行远航任务。"),
    ("voyage", "远航状态: 其他航线进行中，剩余约 319 分钟。"),
    ("voyage_return", f"远航状态: {ROUTE}航线已归航，待结算（.远航归来）。"),
    ("voyage_return", "侍妾当前并无可结算的远航任务"),
    ("voyage_return", "侍妾【其他侍妾】当前并未执行远航任务。"),
    ("voyage_return", RETURNED),
])
def test_inconclusive_panel_retains_unknown_and_rate_limits(env, http, kind, text):
    unknown(env, http, kind)
    panel(http, env, kind, text)
    assert recover(env)
    record = copy.deepcopy(env.identity[FIELD][kind])
    assert record["status"] == "unknown"
    env.clock[0] += 1
    assert recover(env)
    assert env.identity[FIELD][kind] == record
    assert http.await_count == 2
    env.send.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("op_id", "f" * 32), ("account_id", 123),
                                        ("started_at", NOW), ("command", ".远航归来")])
def test_reconcile_rejects_foreign_or_stale_receipts(env, http, field, value):
    unknown(env, http, "voyage")
    execute = panel(http, env, "voyage")
    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        result["receipt"][field] = value
        return result
    http.side_effect = changed
    assert recover(env)
    assert env.identity[FIELD]["voyage"]["status"] == "unknown"


@pytest.mark.parametrize("change", ["plan", "partner", "owner"])
def test_reconcile_does_not_apply_across_await_changes(env, http, monkeypatch, change):
    unknown(env, http, "voyage")
    execute = panel(http, env, "voyage")
    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        if change == "plan":
            env.identity["concubine_voyage_enabled"] = False
        elif change == "partner":
            env.identity["concubine_name"] = "replacement"
        else:
            monkeypatch.setattr(concubine, "get_identity_account", lambda _: ACCOUNT + 1)
        return result
    http.side_effect = changed
    assert recover(env)
    assert env.identity[FIELD]["voyage"]["status"] == "unknown"
    assert env.identity["concubine_voyage_status"] == "idle"


@pytest.mark.parametrize("fail_save", [1, 2])
def test_reconcile_save_failure_does_not_release_pending(env, http, monkeypatch, fail_save):
    unknown(env, http, "voyage")
    panel(http, env, "voyage")
    calls = []
    def save():
        calls.append(1)
        return len(calls) != fail_save
    monkeypatch.setattr(concubine, "save_state", save)
    assert recover(env)
    assert env.identity[FIELD]["voyage"]["status"] == "unknown"
    assert env.identity["concubine_voyage_status"] == "idle"
    assert env.identity["concubine_phase"] == "voyage_pending"
    assert http.await_count == fail_save


def test_reconcile_inflight_read_is_not_duplicated(env, http):
    unknown(env, http, "voyage")
    execute = panel(http, env, "voyage")
    async def changed(*args, **kwargs):
        result = await execute(*args, **kwargs)
        assert await concubine.voyage_actions.recover(env.clock[0] + 10000)
        return result
    http.side_effect = changed
    assert recover(env)
    assert http.await_count == 2


def test_reconcile_waits_thirty_minutes_not_native_reply_timeout(env, http):
    unknown(env, http, "voyage")
    panel(http, env, "voyage")
    env.clock[0] = NOW + concubine.voyage_actions.HTTP_RECONCILE_INTERVAL_SEC - 1
    assert recover(env)
    http.assert_awaited_once()
    env.clock[0] += 1
    assert recover(env)
    assert http.await_count == 2


@pytest.mark.parametrize("kind", KINDS)
def test_scheduler_restores_and_reconciles_without_mutation(env, http, kind):
    unknown(env, http, kind)
    panel(http, env, kind)
    original = copy.deepcopy(env.identity[FIELD][kind])
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(env.clock[0])
        assert env.identity[FIELD][kind] == original
        asyncio.run(concubine._run_concubine_scheduler(env.clock[0]))
    assert env.identity[FIELD][kind]["status"] == "reconciled"
    assert http.await_count == 2
    env.send.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["native", "receipt_owner", "command", "text", "voyage", "same_op", "old_read"])
def test_malformed_reconciliation_fails_closed_after_reload(env, http, mutation):
    unknown(env, http, "voyage")
    panel(http, env, "voyage")
    assert recover(env)
    record = env.identity[FIELD]["voyage"]
    probe = record["reconciliation"]
    if mutation == "native":
        record.pop("transport")
    elif mutation == "receipt_owner":
        probe["receipt"]["account_id"] += 1
    elif mutation == "command":
        probe["receipt"]["command"] = record["command"]
    elif mutation == "text":
        probe["text"] = "unrelated"
    elif mutation == "voyage":
        probe["voyage"]["route"] = "other"
    elif mutation == "same_op":
        probe["op_id"] = probe["receipt"]["op_id"] = record["op_id"]
    else:
        probe["started_at"] = record["started_at"]
    assert persistence.save_state()
    assert persistence.load_state()
    with state_module.use_identity(ID):
        assert concubine.voyage_actions.records() is None
        assert concubine.voyage_actions.block_reason() == "invalid"


@pytest.mark.parametrize("cancel", [True, False])
def test_reconcile_interruption_preserves_backoff(env, http, cancel):
    unknown(env, http, "voyage")
    http.side_effect = asyncio.CancelledError() if cancel else RuntimeError("fixture")
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            recover(env)
    else:
        assert recover(env)
    assert ID not in concubine.voyage_actions._INFLIGHT
    assert env.identity[FIELD]["voyage"]["status"] == "unknown"
    http.side_effect = None
    assert recover(env)
    assert http.await_count == 2


def test_reconciled_return_requires_fresh_explicit_affinity(env, http):
    from tests.test_concubine_query_lifecycle import PANEL

    unknown(env, http, "voyage_return")
    panel(http, env, "voyage_return")
    assert recover(env)
    at = env.clock[0]
    text = PANEL.replace("凌玉灵", NAME).replace("道心侍妾", "红尘道侣")
    without_affinity = text.replace("\n情缘值: 184", "")
    with state_module.use_identity(ID):
        parsed = concubine._parse_status_panel(without_affinity, at + 1)
        assert "affinity" not in parsed
        assert not concubine._apply_status_snapshot(parsed, at + 1)
        stale = concubine._parse_status_panel(text, at - 1)
        assert not concubine._apply_status_snapshot(stale, at + 1)
        assert env.identity["concubine_affinity"] == 320
        assert env.identity["concubine_availability"] == "unknown"
        parsed = concubine._parse_status_panel(text, at + 1)
        assert concubine._apply_status_snapshot(parsed, at + 1)
        assert env.identity["concubine_affinity"] == 184
        assert env.identity["concubine_availability"] == "available"
        later = concubine._parse_status_panel(without_affinity, at + 2)
        assert concubine._apply_status_snapshot(later, at + 2)
        assert env.identity["concubine_affinity"] == 184


def test_reconcile_reducer_exception_rolls_back(env, http, monkeypatch):
    unknown(env, http, "voyage")
    panel(http, env, "voyage")
    def broken(*args):
        env.identity["concubine_voyage_status"] = "sailing"
        raise RuntimeError("fixture")
    monkeypatch.setattr(concubine, "_apply_voyage_snapshot", broken)
    assert recover(env)
    assert env.identity[FIELD]["voyage"]["status"] == "unknown"
    assert env.identity["concubine_phase"] == "voyage_pending"
    assert env.identity["concubine_voyage_status"] == "idle"
