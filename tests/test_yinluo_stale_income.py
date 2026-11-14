import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model import persistence, state as state_module
from model import yinluo_accounting as accounting
from model.features import yinluo
from model.yinluo_recovery_policy import blocking_operations, income_covered_by_native_panel
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, CHAT, CONVERT, IDENTITY, bind, env, event, observe, panel, prepare,
    runtime as runtime_fixture,
)


runtime = runtime_fixture
NOW = 172800.0
DAILY = ".每日献祭"
READ = ".我的阴罗幡"


def unanswered(runtime, command=DAILY):
    operation = prepare(command)
    bind(operation, at=110.5)
    return operation


def fresh_panel():
    return panel(sha=2500, msg_id=301, start=NOW - 2, end=NOW - 1)


def test_next_day_native_balance_unblocks_without_inventing_old_income(runtime):
    old = unanswered(runtime)
    assert accounting.admission_reason(IDENTITY, DAILY) == "yinluo_command_in_flight"
    assert observe(fresh_panel())
    assert accounting.current_operation(IDENTITY, old["op_id"])["phase"] == "sent"
    assert not accounting.command_complete(runtime[accounting.STATE_KEY], CHAT, 100, command=DAILY)
    assert not blocking_operations(runtime[accounting.STATE_KEY])
    assert not accounting.admission_reason(IDENTITY, DAILY)
    assert accounting.resource_balance(IDENTITY, "sha") == {"status": "ready", "value": 2500}
    assert not runtime["yinluo_observation"]["last_daily_sacrifice_day"]
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    restored = state_module.get_identity_state(IDENTITY)
    assert accounting.current_operation(IDENTITY, old["op_id"])["phase"] == "sent"
    assert not blocking_operations(restored[accounting.STATE_KEY])


@pytest.mark.parametrize("fault", [
    "old_panel", "same_day", "wrong_chat", "lower_id", "conflicted", "missing_sha",
    "hold", "gap", "unknown", "unbound", "wrong_module", "ambiguous_reply",
])
def test_missing_or_ambiguous_coverage_keeps_original_block(runtime, fault):
    old = unanswered(runtime)
    assert observe(fresh_panel())
    value = copy.deepcopy(runtime[accounting.STATE_KEY])
    operation = next(item for item in value["operations"] if item["op_id"] == old["op_id"])
    baseline = value["book"]["ledgers"]["sha"]["baseline"]
    if fault == "old_panel":
        baseline["point"]["at"] = 105
    elif fault == "same_day":
        baseline["point"]["at"] = 120
    elif fault == "wrong_chat":
        baseline["point"]["evidence"]["chat_id"] -= 1
    elif fault == "lower_id":
        baseline["point"]["evidence"]["msg_id"] = 99
    elif fault == "conflicted":
        baseline["conflicted"] = True
    elif fault == "missing_sha":
        value["book"]["ledgers"].pop("sha")
    elif fault == "hold":
        value["hold"] = "receipt_conflict"
    elif fault == "gap":
        value["book"]["gap"] = {"reason": "conflict", "at": NOW}
    elif fault == "unknown":
        operation["phase"] = "unknown"
    elif fault == "unbound":
        operation.update(msg_id=0, sent_at=0)
    elif fault == "wrong_module":
        operation["source_module"] = "foreign"
    elif fault == "ambiguous_reply":
        value["book"]["receipts"].append({"start": {"evidence": {"chat_id": CHAT, "msg_id": 100}}})
    assert not income_covered_by_native_panel(value, operation)
    assert operation in blocking_operations(value)


@pytest.mark.parametrize("command", [".化功为煞 10000", ".安抚幡灵 2", ".囚禁魂魄 1 妖兽精魄", ".收取精华", ".召唤魔影"])
def test_later_panel_never_releases_other_unknown_actions(runtime, command):
    old = unanswered(runtime, command)
    assert observe(fresh_panel())
    assert accounting.current_operation(IDENTITY, old["op_id"])["phase"] == "sent"
    assert accounting.admission_reason(IDENTITY, DAILY) == "yinluo_command_in_flight"


def test_late_old_reward_is_not_added_twice_after_balance_coverage(runtime):
    old = unanswered(runtime)
    assert observe(fresh_panel())
    received = event(DAILY, "你引动九幽煞气灌入幡中，煞气池增加了 500 点。", start=110, end=120)
    assert observe(received, now=NOW)
    assert accounting.current_operation(IDENTITY, old["op_id"])["phase"] == "complete"
    assert accounting.resource_balance(IDENTITY, "sha") == {"status": "ready", "value": 2500}
    before = copy.deepcopy(runtime)
    assert not observe(received, now=NOW + 1)
    assert runtime == before


def test_new_day_reward_is_still_counted_and_new_unknown_blocks(runtime):
    unanswered(runtime)
    assert observe(fresh_panel())
    new = prepare(DAILY, now=NOW + 1)
    bind(new, root=400, at=NOW + 1.5)
    assert accounting.admission_reason(IDENTITY, DAILY) == "yinluo_command_in_flight"
    received = event(DAILY, "你引动九幽煞气灌入幡中，煞气池增加了 500 点。",
                     root=400, msg_id=401, start=NOW + 1, end=NOW + 2)
    assert observe(received)
    assert accounting.resource_balance(IDENTITY, "sha") == {"status": "ready", "value": 3000}


def test_scheduler_asks_for_one_balance_then_resumes_daily_from_real_panel(runtime, monkeypatch):
    old = unanswered(runtime)
    monkeypatch.setattr(yinluo.time, "time", lambda: NOW)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    runtime["yinluo_observation"]["auto_config"] = {key: key == "daily_sacrifice" for key in yinluo.YINLUO_AUTO_ACTION_KEYS}
    sends = []

    async def send(command, **options):
        assert options["operation_check"]()
        sends.append(command)
        return SimpleNamespace(id=300, chat_id=CHAT, sent_at=NOW + .5)

    monkeypatch.setattr(yinluo, "send_game_command", send)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(NOW))
        asyncio.run(yinluo.run_yinluo_scheduler(NOW + 1))
        asyncio.run(yinluo.run_yinluo_scheduler(NOW + 2))
    assert sends == [READ]
    assert accounting.current_operation(IDENTITY, old["op_id"])["phase"] == "sent"
    assert observe(panel(sha=2500, msg_id=301, start=NOW, end=NOW + 1))
    runtime["yinluo_observation"]["auto_next_time"] = 0
    planned = AsyncMock()
    monkeypatch.setattr(yinluo, "_execute_yinluo_plan", planned)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(NOW + 5))
    assert planned.await_count == 1
    assert planned.await_args.args[0]["command"] == DAILY


def test_failed_balance_does_not_repeat_after_reload_on_same_day(runtime, monkeypatch):
    unanswered(runtime)
    runtime["yinluo_observation"]["income_recovery_probe_day"] = yinluo.get_day_key(NOW)
    read = prepare(READ, now=NOW - 1000)
    bind(read, root=300, at=NOW - 999.5)
    assert accounting.expire_unanswered_reads(IDENTITY, now=NOW, timeout=600)
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    monkeypatch.setattr(yinluo.time, "time", lambda: NOW)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    sender = AsyncMock()
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(NOW))
    sender.assert_not_awaited()


def test_expired_automatic_probe_stays_once_per_day_after_cold_retirement(runtime, monkeypatch):
    unanswered(runtime)
    monkeypatch.setattr(yinluo.time, "time", lambda: NOW)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])

    async def send(command, **options):
        assert command == READ and options["operation_check"]()
        return SimpleNamespace(id=300, chat_id=CHAT, sent_at=NOW + .5)

    monkeypatch.setattr(yinluo, "send_game_command", send)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(NOW))
    assert accounting.expire_unanswered_reads(IDENTITY, now=NOW + 1000, timeout=600)
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    sender = AsyncMock()
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        for at in (NOW + 1001, NOW + 2000, NOW + 5000):
            asyncio.run(yinluo.run_yinluo_scheduler(at))
    sender.assert_not_awaited()


def test_probe_day_save_failure_cannot_send(runtime, monkeypatch):
    unanswered(runtime)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(yinluo, "save_state", lambda: False)
    sender = AsyncMock()
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(NOW))
    sender.assert_not_awaited()


def test_scheduler_never_automatically_calibrates_spending_silence(runtime, monkeypatch):
    unanswered(runtime, ".化功为煞 10000")
    monkeypatch.setattr(yinluo.time, "time", lambda: NOW)
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: [])
    sender = AsyncMock()
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(NOW))
    sender.assert_not_awaited()
