import asyncio
import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import action_guard, cultivation_accounting as cultivation
from model import persistence, state as state_module
from model import yinluo_accounting as accounting
from model import yinluo_archive as archive
from model.config import CMD_YINLUO_COLLECT, CMD_YINLUO_REFINE, CMD_YINLUO_SOOTHE
from model.features import yinluo
from test_cultivation_accounting import snapshot
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, CHAT, CONVERT, IDENTITY, env, event, observe, panel,
    receipts as receipts_fixture, runtime,
)
from test_yinluo_completion_lifecycle import CONSUME, READ, reload_state
from test_yinluo_retention import retire
from yinluo_native_support import native_logs


receipts = receipts_fixture
SOUL = "\u5996\u517d\u7cbe\u9b44"
MODULE = "\u9634\u7f57\u5b97"
COMMANDS = {
    "collect": f"{CMD_YINLUO_COLLECT} 1",
    "refine": f"{CMD_YINLUO_REFINE} 1 {SOUL}",
    "soothe": f"{CMD_YINLUO_SOOTHE} 2",
    "convert": CONSUME,
}
REPLIES = {
    "collect": "\u6536\u53d6\u6210\u529f\uff01\n\u4f60\u4ece 1 \u4e2a\u70bc\u5316\u69fd\u4e2d\u83b7\u5f97\u4e86: \u3010\u56db\u7ea7\u5996\u4e39\u3011x1\uff01",
    "refine": f"\u9b42\u9b44\u888b\u4e2d\u6ca1\u6709\u3010{SOUL}\u3011",
    "soothe": "\u5b89\u629a\u6210\u529f\uff01\n\u4f60\u6d88\u8017\u4e86 50 \u70b9\u4fee\u4e3a\uff0c\u6210\u529f\u5b89\u629a\u4e86 1 \u4e2a\u70bc\u5316\u69fd\u3002",
    "convert": CONVERT,
}


def install_legacy(owner, action="refine", *, existing_book=True, root=100, sent_at=110.5):
    if not existing_book:
        owner[accounting.STATE_KEY] = {}
    if action != "convert":
        payload = {"sent_at": sent_at}
        if action == "collect":
            payload["slots"] = [1]
        else:
            payload["slot"] = 2 if action == "soothe" else 1
        if action == "refine":
            payload.update(target=SOUL, cost=400, pre_sha_current=9000, pre_soul_stocks={SOUL: 99})
        owner["yinluo_observation"][f"auto_{action}_pending"] = payload
    owner["pending_tasks"][CHAT, root] = {
        "cmd": COMMANDS[action], "time": sent_at, "sent_at": sent_at,
        "send_started_at": 110.0, "chat_id": CHAT, "source_module": MODULE,
        "op_id": f"yinluo-auto-{action}-110", "max_retry": 0,
    }
    action_guard.note_sent(COMMANDS[action], IDENTITY, root, sent_at=sent_at, chat_id=CHAT)
    return event(COMMANDS[action], REPLIES[action], root=root, msg_id=root + 1)


@pytest.mark.parametrize("existing_book", [False, True])
@pytest.mark.parametrize("action", list(COMMANDS))
def test_owned_terminal_reply_retires_legacy_pending_and_hold_atomically(receipts, existing_book, action):
    received = install_legacy(receipts, action, existing_book=existing_book)
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert persistence.save_state()
    assert observe(received)
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert (CHAT, 100) not in receipts["pending_tasks"]
    if action != "convert":
        assert receipts["yinluo_observation"][f"auto_{action}_pending"] == {}
    expected = 490000 if action == "convert" else 499950 if action == "soothe" else 500000
    assert cultivation.cultivation_balance(IDENTITY)["value"] == expected
    restored = reload_state()
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert (CHAT, 100) not in restored["pending_tasks"]
    yinluo.recover_yinluo_resources(IDENTITY, 800, entries=[])
    before = copy.deepcopy(restored)
    assert not observe(received)
    assert restored == before


@pytest.mark.parametrize("cold", [False, True])
def test_retained_terminal_evidence_resolves_legacy_without_log_or_http(receipts, monkeypatch, cold):
    received = event(COMMANDS["refine"], REPLIES["refine"], msg_id=101)
    assert observe(received)
    if cold:
        assert snapshot(at=130, msg_id=300)
        assert observe(panel(msg_id=501, start=140, end=141))
        retire()
    install_legacy(receipts)
    assert persistence.save_state()
    restored = reload_state()
    before = copy.deepcopy(restored[accounting.STATE_KEY]["book"])
    sender = AsyncMock(side_effect=AssertionError("Legacy reconciliation is local only"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    assert yinluo.recover_yinluo_resources(IDENTITY, 800, entries=[])
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert not restored["pending_tasks"]
    assert restored["yinluo_observation"]["auto_refine_pending"] == {}
    assert restored[accounting.STATE_KEY]["book"] == before
    assert not yinluo.recover_yinluo_resources(IDENTITY, 900, entries=[])
    sender.assert_not_awaited()
    assert not reload_state()["pending_tasks"]


def test_legacy_recovery_uses_original_local_log_window(receipts, monkeypatch):
    received = install_legacy(receipts)
    read = Mock(return_value=native_logs(received))
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", read)
    assert yinluo.recover_yinluo_resources(IDENTITY, 10000)
    assert read.call_args.kwargs["since"] == 110.0
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert not receipts["pending_tasks"]


def test_scheduler_reconciles_due_legacy_work_before_replanning(receipts, monkeypatch):
    received = install_legacy(receipts)
    receipts["yinluo_observation"]["auto_next_time"] = 0
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: native_logs(received))
    sender = AsyncMock(return_value=None)
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(1000))
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert not receipts["yinluo_observation"]["auto_refine_pending"]
    assert (CHAT, 100) not in receipts["pending_tasks"]
    assert all(call.args[0] == READ for call in sender.await_args_list)


def test_manual_planner_sees_locally_resolved_legacy_resources(receipts, monkeypatch):
    assert observe(event(COMMANDS["refine"], REPLIES["refine"], msg_id=101))
    install_legacy(receipts)
    monkeypatch.setattr(yinluo.time, "time", lambda: 800.0)
    sender = AsyncMock(return_value=SimpleNamespace(id=200, chat_id=CHAT, sent_at=800.5))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    ok, reason, _plan = asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=800))
    assert ok, reason
    sender.assert_awaited_once()
    assert sender.await_args.args[0] == CONSUME
    assert (CHAT, 100) not in receipts["pending_tasks"]


@pytest.mark.parametrize("damage", [
    "unknown_result", "missing_root", "wrong_chat", "wrong_clock", "wrong_slot", "wrong_target",
    "marker", "bad_payload", "unowned_summary", "second_unknown", "capacity", "no_source",
])
def test_unproved_legacy_work_cannot_be_released(receipts, damage):
    received = install_legacy(receipts)
    raw = receipts["yinluo_observation"]
    pending = receipts["pending_tasks"][CHAT, 100]
    if damage == "unknown_result":
        received = event(COMMANDS["refine"], "unknown result", msg_id=101)
    elif damage == "missing_root":
        receipts["pending_tasks"].clear()
    elif damage == "wrong_chat":
        pending["chat_id"] = CHAT - 1
    elif damage == "wrong_clock":
        raw["auto_refine_pending"]["sent_at"] = 111.5
    elif damage == "wrong_slot":
        raw["auto_refine_pending"]["slot"] = 2
    elif damage == "wrong_target":
        raw["auto_refine_pending"]["target"] = "other"
    elif damage == "marker":
        raw["legacy_pending_invalid"] = True
    elif damage == "bad_payload":
        raw["auto_refine_pending"] = []
    elif damage == "unowned_summary":
        raw.update(last_result="pending", last_summary="unknown old work")
    elif damage == "second_unknown":
        raw["auto_soothe_pending"] = {"slot": 2, "sent_at": 111.5}
    elif damage == "capacity":
        receipts[accounting.STATE_KEY]["hold"] = "capacity"
    elif damage == "no_source":
        pending["source_module"] = "foreign"
    before = copy.deepcopy(raw["auto_refine_pending"])
    observe(received)
    yinluo.recover_yinluo_resources(IDENTITY, 800, entries=[])
    assert accounting.admission_reason(IDENTITY, CONSUME)
    assert receipts["yinluo_observation"]["auto_refine_pending"] == before


@pytest.mark.parametrize("failure", ["sqlite", "false", "exception"])
def test_native_migration_rolls_back_evidence_pending_and_hold_together(receipts, monkeypatch, failure):
    received = install_legacy(receipts)
    assert persistence.save_state()
    before = copy.deepcopy(receipts)
    connection = persistence.get_db_conn()
    with monkeypatch.context() as patch:
        if failure == "sqlite":
            connection.execute("CREATE TEMP TRIGGER fail_legacy BEFORE INSERT ON identity_runtime_state "
                               "BEGIN SELECT RAISE(ABORT, 'legacy migration failure'); END")
        else:
            def fail_save():
                assert not accounting.read_accounting(IDENTITY)[0]["hold"]
                assert not receipts["yinluo_observation"]["auto_refine_pending"]
                assert (CHAT, 100) not in receipts["pending_tasks"]
                if failure == "exception":
                    raise RuntimeError("legacy migration failure")
                return False
            patch.setattr(persistence, "save_state", fail_save)
        try:
            if failure == "exception":
                with pytest.raises(RuntimeError, match="legacy migration failure"):
                    observe(received)
            else:
                assert not observe(received)
        finally:
            if failure == "sqlite":
                connection.execute("DROP TRIGGER fail_legacy")
    assert receipts == before
    restored = reload_state()
    assert restored["yinluo_observation"] == before["yinluo_observation"]
    assert (CHAT, 100) in restored["pending_tasks"]
    assert observe(received)
    assert not reload_state()["yinluo_observation"]["auto_refine_pending"]


@pytest.mark.parametrize("existing_book", [False, True])
@pytest.mark.parametrize("command", [None, "", [], {}, ".unknown"])
def test_malformed_legacy_command_still_holds_shared_cultivation(receipts, existing_book, command):
    install_legacy(receipts, "convert", existing_book=existing_book)
    receipts["pending_tasks"][CHAT, 100]["cmd"] = command
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert accounting.reserved_cultivation(IDENTITY) == {"status": "legacy_pending", "value": None}
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None


@pytest.mark.parametrize("action", ["collect", "refine", "soothe"])
@pytest.mark.parametrize("damage", [
    "boolean_slot", "float_slot", "foreign_slots", "duplicate_slots", "empty_slots",
    "string_slots", "null_slots", "float_slots", "boolean_slots",
])
def test_conflicting_or_mistyped_slot_aliases_cannot_release_legacy(receipts, action, damage):
    received = install_legacy(receipts, action)
    payload = receipts["yinluo_observation"][f"auto_{action}_pending"]
    slot = 2 if action == "soothe" else 1
    if damage == "boolean_slot":
        payload["slot"] = True
    elif damage == "float_slot":
        payload["slot"] = float(slot)
    else:
        payload["slots"] = {
            "foreign_slots": [slot + 1], "duplicate_slots": [slot, slot], "empty_slots": [],
            "string_slots": str(slot), "null_slots": None, "float_slots": [float(slot)],
            "boolean_slots": [True],
        }[damage]
    before = copy.deepcopy(payload)
    observe(received)
    yinluo.recover_yinluo_resources(IDENTITY, 800, entries=[])
    assert accounting.read_accounting(IDENTITY)[0]["hold"] == "legacy_pending"
    assert receipts["yinluo_observation"][f"auto_{action}_pending"] == before
    assert (CHAT, 100) in receipts["pending_tasks"]


@pytest.mark.parametrize("action", ["collect", "refine", "soothe"])
def test_matching_integer_slot_aliases_allow_exact_completion(receipts, action):
    received = install_legacy(receipts, action)
    payload = receipts["yinluo_observation"][f"auto_{action}_pending"]
    slot = 2 if action == "soothe" else 1
    payload.update(slot=slot, slots=[slot])
    assert observe(received)
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert not receipts["yinluo_observation"][f"auto_{action}_pending"]


@pytest.mark.parametrize("edited", [False, True])
@pytest.mark.parametrize("restart", [False, True])
def test_native_pending_summary_is_resolved_by_its_terminal_reply(receipts, edited, restart):
    terminal = install_legacy(receipts, "convert")
    pending = replace(terminal, text="\u4f60\u5f00\u59cb\u8fd0\u8f6c\u9b54\u529f\uff0c\u51dd\u805a\u715e\u6c14\u3002", server_event_at=111)
    assert observe(pending)
    assert receipts["yinluo_observation"]["last_result"] == "pending"
    assert accounting.read_accounting(IDENTITY)[0]["hold"] == "legacy_pending"
    terminal = replace(terminal, event_type="edit" if edited else "message", msg_id=101 if edited else 102)
    owner = reload_state() if restart else receipts
    assert observe(terminal)
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert owner["yinluo_observation"]["last_result"] == "success"
    assert not owner["pending_tasks"]
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
    observe(pending)
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert owner["yinluo_observation"]["last_result"] == "success"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


def test_legacy_collection_needs_a_terminal_receipt_for_every_slot(receipts):
    first = install_legacy(receipts, "collect")
    receipts["yinluo_observation"]["auto_collect_pending"]["slots"] = [1, 2]
    second = copy.deepcopy(receipts["pending_tasks"][CHAT, 100])
    second["cmd"] = f"{CMD_YINLUO_COLLECT} 2"
    receipts["pending_tasks"][CHAT, 200] = second
    assert observe(first)
    assert accounting.read_accounting(IDENTITY)[0]["hold"] == "legacy_pending"
    assert len(receipts["pending_tasks"]) == 2
    assert observe(event(second["cmd"], REPLIES["collect"], root=200, msg_id=201))
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert not receipts["pending_tasks"]
    assert receipts["yinluo_observation"]["auto_collect_pending"] == {}


def test_legacy_refine_denial_does_not_restore_optimistic_snapshots(receipts):
    received = install_legacy(receipts)
    assert observe(event("\u002e\u6bcf\u65e5\u732e\u796d", "\u4f60\u5f15\u52a8\u4e5d\u5e7d\u715e\u6c14\u704c\u5165\u5e61\u4e2d\uff0c\u715e\u6c14\u6c60\u589e\u52a0\u4e86 500 \u70b9\u3002",
                         root=200, msg_id=201, start=112, end=115))
    assert observe(received)
    assert accounting.resource_balance(IDENTITY, "sha") == {"status": "ready", "value": 2500}
    assert accounting.resource_balance(IDENTITY, f"soul:{SOUL}") == {"status": "ready", "value": 5}
    assert receipts["yinluo_observation"]["sha_current"] == 2500
    assert receipts["yinluo_observation"]["soul_stocks"][SOUL] == 5


@pytest.mark.parametrize("container,field,value", [
    ("slot", "chat_id", str(CHAT)), ("slot", "msg_id", True),
    ("slot", "account_id", ACCOUNT + 1), ("slot", "identity_id", IDENTITY + 1),
    ("slot", "send_as_id", str(IDENTITY)), ("slot", "op_id", "yinluo-auto-refine-109"),
    ("slot", "cmd", COMMANDS["collect"]), ("slot", "command", COMMANDS["collect"]),
    ("slot", "send_started_at", "110.0"),
    ("transport", "account_id", ACCOUNT + 1), ("transport", "identity_id", IDENTITY + 1),
    ("transport", "send_as_id", str(IDENTITY)), ("transport", "msg_id", 101),
    ("transport", "msg_id", "100"), ("transport", "command", COMMANDS["collect"]),
])
def test_explicit_legacy_metadata_conflicts_remain_held(receipts, container, field, value):
    received = install_legacy(receipts)
    record = (receipts["yinluo_observation"]["auto_refine_pending"] if container == "slot"
              else receipts["pending_tasks"][CHAT, 100])
    record[field] = value
    before = copy.deepcopy(receipts["yinluo_observation"]["auto_refine_pending"])
    observe(received)
    yinluo.recover_yinluo_resources(IDENTITY, 800, entries=[])
    assert accounting.admission_reason(IDENTITY, CONSUME)
    assert receipts["yinluo_observation"]["auto_refine_pending"] == before
    assert (CHAT, 100) in receipts["pending_tasks"]


@pytest.mark.parametrize("explicit_chat", [False, True])
def test_two_chat_completions_require_unambiguous_slot_ownership(receipts, explicit_chat):
    state_module.set_game_group_route_config({
        "enabled": True, "primary_group_id": CHAT, "backup_group_ids": [CHAT - 1],
    })
    received = install_legacy(receipts)
    other = copy.deepcopy(receipts["pending_tasks"][CHAT, 100])
    other["chat_id"] = CHAT - 1
    receipts["pending_tasks"][CHAT - 1, 100] = other
    if explicit_chat:
        receipts["yinluo_observation"]["auto_refine_pending"]["chat_id"] = CHAT
    assert observe(received)
    assert accounting.read_accounting(IDENTITY)[0]["hold"] == "legacy_pending"
    assert observe(event(COMMANDS["refine"], REPLIES["refine"], msg_id=101, chat=CHAT - 1))
    assert bool(accounting.read_accounting(IDENTITY)[0]["hold"]) is not explicit_chat
    assert bool(receipts["pending_tasks"]) is not explicit_chat


def stage_retained_legacy(owner):
    assert observe(event(COMMANDS["refine"], REPLIES["refine"], msg_id=101))
    install_legacy(owner)
    value, reason = accounting.read_accounting(IDENTITY)
    assert not reason
    update = accounting._update(IDENTITY, value, cultivation.read_cultivation_ledger(IDENTITY))
    projected = accounting.stage_legacy_completion(update)
    assert projected is not None
    return update, projected


@pytest.mark.parametrize("change", [
    "observation", "config", "boolean_slot", "float_slot", "typed_config", "pending", "pending_key",
    "replace", "rebind", "delete", "income", "wanxin",
])
def test_staged_migration_cannot_overwrite_replacement_or_new_work(receipts, change):
    update, projected = stage_retained_legacy(receipts)
    raw = receipts["yinluo_observation"]
    if change == "observation":
        raw["auto_next_time"] = 500
    elif change == "config":
        raw["auto_config"]["convert_amount"] = 30000
    elif change == "typed_config":
        raw["auto_config"]["collect"] = int(raw["auto_config"]["collect"])
    elif change == "boolean_slot":
        raw["auto_refine_pending"]["slot"] = True
    elif change == "float_slot":
        raw["auto_refine_pending"]["slot"] = 1.0
    elif change == "pending":
        receipts["pending_tasks"][CHAT, 100]["sent_at"] = 111.5
    elif change == "pending_key":
        receipts["pending_tasks"][CHAT, 100.0] = receipts["pending_tasks"].pop((CHAT, 100))
    elif change == "replace":
        state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(receipts)
    elif change == "rebind":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    elif change == "delete":
        state_module.remove_identity(IDENTITY)
    elif change == "income":
        assert snapshot(510000, at=130, msg_id=300)
    elif change == "wanxin":
        state_module.set_identity_account(IDENTITY + 1, ACCOUNT + 1)
        state_module.get_identity_state(IDENTITY + 1)["wanxin_observation"]["pending"] = {
            "send_as_id": IDENTITY, "action": "strip",
        }
    before = copy.deepcopy(state_module._meta_state)
    assert not accounting.commit_update(update, observations={IDENTITY: {"yinluo_observation": projected}})
    assert state_module._meta_state == before


@pytest.mark.parametrize("part", ["payload", "digest", "missing"])
def test_cold_legacy_recovery_keeps_hold_if_archive_disappears_or_is_corrupt(receipts, part):
    assert observe(event(COMMANDS["refine"], REPLIES["refine"], msg_id=101))
    assert snapshot(at=130, msg_id=300)
    assert observe(panel(msg_id=501, start=140, end=141))
    retire()
    install_legacy(receipts)
    value, _reason = accounting.read_accounting(IDENTITY)
    update = accounting._update(IDENTITY, value, cultivation.read_cultivation_ledger(IDENTITY))
    projected = accounting.stage_legacy_completion(update)
    assert projected is not None
    conn = persistence.get_db_conn()
    assert archive.read_command(conn, IDENTITY, ACCOUNT, CHAT, 100)
    if part == "missing":
        conn.execute("DELETE FROM yinluo_archive_commands WHERE command_msg_id=100")
    else:
        conn.execute(f"UPDATE yinluo_archive_commands SET {part}=? WHERE command_msg_id=100", ("{}",))
    conn.commit()
    before = copy.deepcopy(receipts)
    assert not accounting.commit_update(update, observations={IDENTITY: {"yinluo_observation": projected}})
    assert not accounting.reconcile_legacy_pending(IDENTITY)
    assert receipts == before
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"


@pytest.mark.parametrize("failure", ["sqlite", "false", "exception"])
def test_local_legacy_recovery_save_failure_preserves_pending_for_retry(receipts, monkeypatch, failure):
    stage_retained_legacy(receipts)
    assert persistence.save_state()
    before = copy.deepcopy(receipts)
    connection = persistence.get_db_conn()
    with monkeypatch.context() as patch:
        if failure == "sqlite":
            connection.execute("CREATE TEMP TRIGGER fail_local_legacy BEFORE INSERT ON identity_runtime_state "
                               "BEGIN SELECT RAISE(ABORT, 'legacy recovery failure'); END")
        else:
            def fail_save():
                assert not receipts["yinluo_observation"]["auto_refine_pending"]
                assert (CHAT, 100) not in receipts["pending_tasks"]
                if failure == "exception":
                    raise RuntimeError("legacy recovery failure")
                return False
            patch.setattr(persistence, "save_state", fail_save)
        try:
            if failure == "exception":
                with pytest.raises(RuntimeError, match="legacy recovery failure"):
                    accounting.reconcile_legacy_pending(IDENTITY)
            else:
                assert not accounting.reconcile_legacy_pending(IDENTITY)
        finally:
            if failure == "sqlite":
                connection.execute("DROP TRIGGER fail_local_legacy")
    assert receipts == before
    restored = reload_state()
    assert (CHAT, 100) in restored["pending_tasks"]
    assert restored["yinluo_observation"] == before["yinluo_observation"]
    assert accounting.reconcile_legacy_pending(IDENTITY)
    assert not restored["pending_tasks"]
    assert not reload_state()["yinluo_observation"]["auto_refine_pending"]


@pytest.mark.parametrize("prefix", ["yinluo", "yinluo-auto"])
@pytest.mark.parametrize("command,tag,text", [
    (".\u6bcf\u65e5\u732e\u796d", "daily_sacrifice", "\u4f60\u5f15\u52a8\u4e5d\u5e7d\u715e\u6c14\u704c\u5165\u5e61\u4e2d\uff0c\u715e\u6c14\u6c60\u589e\u52a0\u4e86 500 \u70b9\u3002"),
    (".\u8840\u6d17\u5c71\u6797", "blood_forest", "\u6b64\u5730\u751f\u7075\u5c1a\u672a\u6062\u590d\uff0c\u715e\u6c14\u7a00\u8584\u3002"),
    (".\u53ec\u5524\u9b54\u5f71", "demon_summon", "\u9b54\u57df\u88c2\u9699\u5c1a\u672a\u5e73\u590d\u3002"),
])
def test_historical_manual_and_scheduler_action_tags_are_supported(receipts, command, tag, text, prefix):
    receipts["pending_tasks"][CHAT, 100] = {
        "cmd": command, "time": 110.5, "sent_at": 110.5, "send_started_at": 110.0,
        "chat_id": CHAT, "source_module": MODULE, "op_id": f"{prefix}-{tag}-110", "max_retry": 0,
    }
    action_guard.note_sent(command, IDENTITY, 100, sent_at=110.5, chat_id=CHAT)
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert observe(event(command, text, msg_id=101))
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert not receipts["pending_tasks"]


@pytest.mark.parametrize("op_id", [
    "f" * 32, "yinluo-auto-convert-110", "yinluo-auto-refine-111",
    "yinluo-manual-refine-110", "yinluo-auto-refine-0", "",
])
def test_only_the_original_legacy_action_and_planning_time_can_migrate(receipts, op_id):
    received = install_legacy(receipts)
    receipts["pending_tasks"][CHAT, 100]["op_id"] = op_id
    assert observe(received)
    assert not accounting.reconcile_legacy_pending(IDENTITY)
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert (CHAT, 100) in receipts["pending_tasks"]


@pytest.mark.parametrize("damage", ["duplicate", "float_key", "float_chat", "unscoped_collect", "gap", "receipt_conflict"])
def test_ambiguous_or_stronger_legacy_barriers_survive_terminal_evidence(receipts, damage):
    received = install_legacy(receipts, "collect")
    if damage == "duplicate":
        receipts["pending_tasks"][100] = copy.deepcopy(receipts["pending_tasks"][CHAT, 100])
    elif damage == "float_key":
        receipts["pending_tasks"][CHAT, 100.0] = receipts["pending_tasks"].pop((CHAT, 100))
    elif damage == "float_chat":
        receipts["pending_tasks"][float(CHAT), 100] = receipts["pending_tasks"].pop((CHAT, 100))
    elif damage == "unscoped_collect":
        receipts["pending_tasks"][CHAT, 100]["cmd"] = CMD_YINLUO_COLLECT
        received = event(CMD_YINLUO_COLLECT, REPLIES["collect"], msg_id=101)
    elif damage == "gap":
        receipts[accounting.STATE_KEY]["book"]["gap"] = {"reason": "capacity", "at": 100.0}
    else:
        receipts[accounting.STATE_KEY]["hold"] = "receipt_conflict"
    before = copy.deepcopy(receipts["yinluo_observation"]["auto_collect_pending"])
    observe(received)
    assert not accounting.reconcile_legacy_pending(IDENTITY)
    assert accounting.admission_reason(IDENTITY, CONSUME)
    assert receipts["pending_tasks"]
    assert receipts["yinluo_observation"]["auto_collect_pending"] == before


def test_legacy_migration_preserves_all_module_and_action_switches(receipts):
    received = install_legacy(receipts)
    receipts["yinluo_enabled"] = False
    config = receipts["yinluo_observation"]["auto_config"]
    for key in config:
        if type(config[key]) is bool:
            config[key] = False
    before = copy.deepcopy(config)
    state_module._meta_state["global_enabled"] = False
    assert observe(received)
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert not receipts["yinluo_enabled"]
    assert not state_module._meta_state["global_enabled"]
    assert receipts["yinluo_observation"]["auto_config"] == before
