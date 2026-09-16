import asyncio
import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model import cultivation_accounting as cultivation
from model import persistence, runtime as transport, state as state_module
from model import yinluo_accounting as accounting
from model import yinluo_archive as archive
from model import yinluo_resource_book as books
from model.config import CMD_YINLUO_DAILY_SACRIFICE
from model.features import yinluo
from test_cultivation_accounting import snapshot
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, BOT, CHAT, CONVERT, IDENTITY, bind, env, event, finalize, observe, panel,
    receipts as receipts_fixture, runtime,
)
from test_yinluo_completion_lifecycle import READ, reload_state


receipts = receipts_fixture
MODULE = "\u9634\u7f57\u5b97"
SACRIFICE_REPLY = "\u4f60\u5f15\u52a8\u4e5d\u5e7d\u715e\u6c14\u704c\u5165\u5e61\u4e2d\uff0c\u715e\u6c14\u6c60\u589e\u52a0\u4e86 500 \u70b9\u3002"


def fill_uncovered_completions(owner, monkeypatch, *, bound="operations", limit=4, action="sacrifice"):
    if bound == "operations":
        monkeypatch.setattr(accounting, "MAX_OPERATIONS", limit)
    else:
        monkeypatch.setattr(accounting, "MAX_RECEIPTS", limit)
        monkeypatch.setattr(books, "MAX_RECEIPTS", limit)
    if limit > 16:
        expanded = panel(msg_id=50, start=108, end=109)
        assert observe(replace(expanded, text=expanded.text.replace("10000", "1000000")))
    config = owner["yinluo_observation"]["auto_config"]
    for key in config:
        if type(config[key]) is bool:
            config[key] = key == ("daily_sacrifice" if action == "sacrifice" else "convert")
    config.update(convert_amount=10000, convert_sha_threshold=100000)
    command = CMD_YINLUO_DAILY_SACRIFICE if action == "sacrifice" else ".\u5316\u529f\u4e3a\u715e 10000"
    text = SACRIFICE_REPLY if action == "sacrifice" else CONVERT
    for index in range(limit - 1):
        start, root = 200.0 + index * 86400, 100 + index * 20
        record, reason = accounting.prepare_operation(
            IDENTITY, command, CHAT, start, source_module=MODULE,
        )
        assert not reason
        finalize(record, root=root, at=start + 0.5)
        assert observe(event(command, text, root=root, msg_id=root + 1, start=start, end=start + 1))
    due_key = "next_daily_sacrifice_time" if action == "sacrifice" else "next_convert_time"
    now = owner["yinluo_observation"][due_key] + 1
    assert 0 < now - owner["yinluo_observation"]["last_observed_at"] < 86400 - 30
    monkeypatch.setattr(yinluo.time, "time", lambda: now)
    expected = 2000 + (500 if action == "sacrifice" else 2000) * (limit - 1)
    assert accounting.resource_balance(IDENTITY, "sha") == {"status": "ready", "value": expected}
    assert accounting.prepare_operation(
        IDENTITY, command, CHAT, now, source_module=MODULE,
    ) == (None, "capacity")
    assert persistence.save_state()
    return now


@pytest.mark.parametrize("bound", ["operations", "receipts"])
@pytest.mark.parametrize("restart", [False, True])
def test_scheduler_uses_reserved_read_to_resume_after_soft_capacity(receipts, monkeypatch, bound, restart):
    now = fill_uncovered_completions(receipts, monkeypatch, bound=bound)
    owner = reload_state() if restart else receipts
    baseline = copy.deepcopy(cultivation.read_cultivation_ledger(IDENTITY))
    sends = []

    async def send(command, **kwargs):
        assert command == (READ if not sends else CMD_YINLUO_DAILY_SACRIFICE)
        assert kwargs["operation_check"]()
        sends.append(command)
        record = accounting.current_operation(IDENTITY, kwargs["op_id"])
        start, root = (now, 500) if command == READ else (now + 10, 600)
        finalize(record, root=root, at=start + 0.5)
        assert observe(panel(sha=3500, msg_id=501, start=start, end=start + 1) if command == READ else
                       event(command, SACRIFICE_REPLY, root=root, msg_id=root + 1, start=start, end=start + 1))
        return SimpleNamespace(id=root, chat_id=CHAT, sent_at=start + 0.5)

    monkeypatch.setattr(yinluo, "send_game_command", send)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now))
    assert sends == [READ]
    assert not owner["pending_tasks"]
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert accounting.resource_balance(IDENTITY, "sha") == {"status": "ready", "value": 3500}
    assert cultivation.read_cultivation_ledger(IDENTITY) == baseline
    for root in (100, 120, 140):
        assert archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, root)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now + 10))
    assert sends == [READ, CMD_YINLUO_DAILY_SACRIFICE]
    assert not owner["pending_tasks"]
    assert accounting.resource_balance(IDENTITY, "sha")["value"] == 4000
    assert owner["yinluo_observation"]["next_daily_sacrifice_time"] > now + 11
    record = owner[accounting.STATE_KEY]["operations"][-1]
    assert record["command"] == CMD_YINLUO_DAILY_SACRIFICE and record["phase"] == "complete"
    assert reload_state()[accounting.STATE_KEY]["operations"][-1] == record


@pytest.mark.parametrize("bound", ["operations", "receipts"])
def test_capacity_calibration_without_reply_waits_for_the_owned_read(receipts, monkeypatch, bound):
    now = fill_uncovered_completions(receipts, monkeypatch, bound=bound)
    sender = AsyncMock(return_value=SimpleNamespace(id=500, chat_id=CHAT, sent_at=now + 0.5))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now))
        asyncio.run(yinluo.run_yinluo_scheduler(now + 1))
        asyncio.run(yinluo.run_yinluo_scheduler(now + 30))
    sender.assert_awaited_once()
    assert sender.await_args.args[0] == READ
    current = accounting.current_operation(IDENTITY, sender.await_args.kwargs["op_id"])
    assert current["phase"] == "sent"
    assert accounting.resource_balance(IDENTITY, "sha")["value"] == 3500


def test_independent_native_coverage_can_already_reopen_dispatch_capacity(receipts, monkeypatch):
    now = fill_uncovered_completions(receipts, monkeypatch)
    assert observe(panel(sha=3500, msg_id=501, start=now, end=now + 1))
    record, reason = accounting.prepare_operation(
        IDENTITY, CMD_YINLUO_DAILY_SACRIFICE, CHAT, now + 10, source_module=MODULE,
    )
    assert not reason and record


def test_capacity_inspection_does_not_write_or_load_a_new_balance(receipts, monkeypatch):
    fill_uncovered_completions(receipts, monkeypatch)
    before = copy.deepcopy(state_module._meta_state)
    conn = persistence.get_db_conn()
    stats = archive.stats(conn, IDENTITY, ACCOUNT)
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        for _ in range(3):
            status = accounting.capacity_status(IDENTITY)
            assert status == {"status": "banner_required", "operations": 3, "receipts": 3, "resources": ["sha"]}
            with state_module.use_identity(IDENTITY):
                assert yinluo.get_yinluo_ui_state()["capacity"] == status
    finally:
        conn.set_trace_callback(None)
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE", "BEGIN")) for sql in statements)
    assert state_module._meta_state == before
    assert archive.stats(conn, IDENTITY, ACCOUNT) == stats


def test_default_operation_limit_recovers_without_raising_the_limit(receipts, monkeypatch):
    assert accounting.MAX_OPERATIONS == 64
    now = fill_uncovered_completions(receipts, monkeypatch, limit=64)
    assert len(receipts[accounting.STATE_KEY]["operations"]) == 63
    assert accounting.capacity_status(IDENTITY)["status"] == "banner_required"
    received = panel(sha=33500, msg_id=2001, start=now, end=now + 1)
    received = replace(received, text=received.text.replace("10000", "1000000"))
    assert observe(received)
    record, reason = accounting.prepare_operation(
        IDENTITY, CMD_YINLUO_DAILY_SACRIFICE, CHAT, now + 10, source_module=MODULE,
    )
    assert not reason and record
    assert len(receipts[accounting.STATE_KEY]["operations"]) < 64
    assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)["commands"] >= 63
    assert accounting.resource_balance(IDENTITY, "sha")["value"] == 33500


def test_missing_cultivation_is_not_treated_as_a_banner_only_dependency(receipts, monkeypatch):
    now = fill_uncovered_completions(receipts, monkeypatch, action="convert")
    status = accounting.capacity_status(IDENTITY)
    assert status["status"] == "native_coverage_required"
    assert status["resources"] == ["cultivation", "sha"]
    sender = AsyncMock(side_effect=AssertionError("A banner cannot supply cultivation coverage"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now))
        asyncio.run(yinluo.run_yinluo_scheduler(now + 1))
    sender.assert_not_awaited()
    assert receipts["yinluo_observation"]["auto_last_action"] == "resource_capacity"
    assert snapshot(470000, at=now + 2, msg_id=400)
    assert accounting.capacity_status(IDENTITY)["status"] == "banner_required"
    assert observe(panel(sha=8000, msg_id=501, start=now + 3, end=now + 4))
    assert accounting.capacity_status(IDENTITY)["status"] == "ready"
    assert accounting.resource_balance(IDENTITY, "sha")["value"] == 8000
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 470000


def test_newer_incomplete_banner_does_not_trigger_identical_query_loop(receipts, monkeypatch):
    now = fill_uncovered_completions(receipts, monkeypatch)
    incomplete = event(READ, "\u3010\u9053\u53cb\u7684\u9634\u7f57\u5e61\u3011\n\u70bc\u5316\u69fd:\n1\u53f7\u69fd: [\u7a7a\u95f2]",
                       root=500, msg_id=501, start=now, end=now + 1)
    assert observe(incomplete)
    assert accounting.capacity_status(IDENTITY)["status"] == "native_coverage_required"
    sender = AsyncMock(side_effect=AssertionError("Unchanged incomplete coverage must not cause repeated queries"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now + 200))
        asyncio.run(yinluo.run_yinluo_scheduler(now + 4000))
    sender.assert_not_awaited()
    assert observe(event(CMD_YINLUO_DAILY_SACRIFICE, SACRIFICE_REPLY.replace("500", "600"), root=140,
                         msg_id=141, start=200 + 2 * 86400, end=now + 4001, edited=True))
    assert accounting.capacity_status(IDENTITY)["status"] == "banner_required"


def test_known_unsent_capacity_query_honors_backoff_while_daily_work_is_due(receipts, monkeypatch):
    now = fill_uncovered_completions(receipts, monkeypatch)
    calls = []

    async def unsent(command, **_kwargs):
        calls.append(command)
        transport._record_game_send_block(IDENTITY, command, "global_disabled", "paused", definitely_unsent=True)
        return None

    monkeypatch.setattr(yinluo, "send_game_command", unsent)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now))
        asyncio.run(yinluo.run_yinluo_scheduler(now + 1))
        asyncio.run(yinluo.run_yinluo_scheduler(now + 30))
    assert calls == [READ]
    assert receipts["yinluo_observation"]["auto_next_time"] >= now + yinluo.YINLUO_AUTO_SEND_FAIL_BACKOFF_SEC
    assert receipts[accounting.STATE_KEY]["operations"][-1]["phase"] == "unsent"


def test_repeated_unknown_reads_do_not_destroy_future_capacity_recovery(receipts, monkeypatch):
    now = fill_uncovered_completions(receipts, monkeypatch, bound="receipts")
    clock = {"now": now}
    monkeypatch.setattr(yinluo.time, "time", lambda: clock["now"])
    calls = []

    async def send(command, **kwargs):
        assert command == READ and kwargs["operation_check"]()
        root = 500 + 20 * len(calls)
        at = clock["now"]
        calls.append(root)
        record = accounting.current_operation(IDENTITY, kwargs["op_id"])
        finalize(record, root=root, at=at + 0.5)
        reply = (event(READ, "unrecognized read result", root=root, msg_id=root + 1, start=at, end=at + 1)
                 if len(calls) <= 4 else panel(sha=3500, msg_id=root + 1, start=at, end=at + 1))
        assert observe(reply)
        return SimpleNamespace(id=root, chat_id=CHAT, sent_at=at + 0.5)

    monkeypatch.setattr(yinluo, "send_game_command", send)
    for index in range(5):
        clock["now"] = now + index * (yinluo.YINLUO_AUTO_CALIBRATE_RETRY_SEC + 2)
        with state_module.use_identity(IDENTITY):
            asyncio.run(yinluo.run_yinluo_scheduler(clock["now"]))
        assert len(calls) == index + 1
        assert not receipts[accounting.STATE_KEY]["book"]["gap"]
        if index < 4:
            assert len(receipts[accounting.STATE_KEY]["book"]["receipts"]) <= 4
    assert accounting.capacity_status(IDENTITY)["status"] == "ready"
    assert accounting.resource_balance(IDENTITY, "sha")["value"] == 3500
    assert accounting.prepare_operation(
        IDENTITY, CMD_YINLUO_DAILY_SACRIFICE, CHAT, clock["now"] + 10, source_module=MODULE,
    )[0]


def owned_unknown_read(*, prior_panel=False):
    record, reason = accounting.prepare_operation(IDENTITY, READ, CHAT, 110, source_module=MODULE)
    assert not reason
    finalize(record, at=110.5)
    bind(record, at=110.5)
    received = event(READ, "unrecognized read result", msg_id=101, end=120, edited=prior_panel)
    if prior_panel:
        assert observe(panel(sha=1700, msg_id=101, start=110, end=111))
    assert observe(received)
    assert persistence.save_state()
    return record, received


@pytest.mark.parametrize("prior_panel", [False, True])
@pytest.mark.parametrize("contextless", [False, True])
def test_expired_read_archives_evidence_and_preserves_late_edit_ownership(receipts, prior_panel, contextless):
    record, _received = owned_unknown_read(prior_panel=prior_panel)
    before = copy.deepcopy(receipts[accounting.STATE_KEY])
    ledger = copy.deepcopy(cultivation.read_cultivation_ledger(IDENTITY))
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    stored = archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)
    assert stored is not None, "Expired reads must retain their native result owner, not delete the evidence"
    payload, _digest = stored
    assert payload["version"] == 2
    assert payload["operation"] == {**record, "msg_id": 100, "sent_at": 110.5, "phase": "read_expired"}
    assert accounting.current_operation(IDENTITY, record["op_id"], chat_id=CHAT, msg_id=100) == payload["operation"]
    assert payload["receipts"] == [item for item in before["book"]["receipts"]
                                   if item["start"]["evidence"]["msg_id"] == 100]
    assert archive.result_owners(persistence.get_db_conn(), CHAT, 101) == [(IDENTITY, ACCOUNT, 100, BOT)]
    assert not accounting.current_operation(IDENTITY, record["op_id"])
    owner = reload_state()
    assert not owner["pending_tasks"]
    assert owner[accounting.STATE_KEY]["book"]["ledgers"] == before["book"]["ledgers"]
    assert owner[accounting.STATE_KEY]["business"] == before["business"]
    edited = replace(panel(sha=1900, msg_id=101, start=110, end=720), event_type="edit")
    if contextless:
        edited = replace(edited, reply_context=None, root_msg_id=0, identity_id=IDENTITY + 1)
    assert observe(edited)
    assert archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100) is None
    current = accounting.current_operation(IDENTITY, record["op_id"])
    assert current["phase"] == ("read_expired" if contextless else "complete")
    assert current["command"] == READ and current["sent_at"] == 110.5
    assert cultivation.read_cultivation_ledger(IDENTITY) == ledger
    if contextless:
        assert not accounting.command_complete(owner[accounting.STATE_KEY], CHAT, 100)
        assert owner[accounting.STATE_KEY]["business"] == before["business"]
        assert accounting.resource_balance(IDENTITY, "sha")["value"] == (None if prior_panel else 2000)
    else:
        assert accounting.resource_balance(IDENTITY, "sha") == {"status": "ready", "value": 1900}
    assert not observe(edited)
    assert reload_state()[accounting.STATE_KEY] == owner[accounting.STATE_KEY]


def archive_unknown_read():
    record, received = owned_unknown_read()
    assert accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)
    assert archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)
    return record, received


def legacy_expired_read(owner):
    record, _received = owned_unknown_read()
    owner[accounting.STATE_KEY]["operations"][0]["phase"] = "read_expired"
    owner["pending_tasks"].clear()
    assert persistence.save_state()
    return record


def test_capacity_inspection_simulates_legacy_read_retirement_without_writes(receipts, monkeypatch):
    record = legacy_expired_read(receipts)
    monkeypatch.setattr(accounting, "MAX_OPERATIONS", 2)
    before = copy.deepcopy(state_module._meta_state)
    conn = persistence.get_db_conn()
    stats = archive.stats(conn, IDENTITY, ACCOUNT)
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        assert accounting.capacity_status(IDENTITY)["status"] == "ready"
    finally:
        conn.set_trace_callback(None)
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE", "BEGIN")) for sql in statements)
    assert state_module._meta_state == before
    assert archive.stats(conn, IDENTITY, ACCOUNT) == stats
    mutation, reason = accounting.prepare_operation(
        IDENTITY, CMD_YINLUO_DAILY_SACRIFICE, CHAT, 720, source_module=MODULE,
    )
    assert not reason
    assert mutation["op_id"] != record["op_id"]
    owner = reload_state()
    assert len(owner[accounting.STATE_KEY]["operations"]) == 1
    assert archive.read_command(conn, IDENTITY, ACCOUNT, CHAT, 100)[0]["version"] == 2
    assert accounting.resource_balance(IDENTITY, "sha")["value"] == 2000


def test_expired_cold_read_accepts_only_its_exact_duplicate_transport(receipts, monkeypatch):
    record, _received = archive_unknown_read()
    before = copy.deepcopy(state_module._meta_state)
    conn = persistence.get_db_conn()
    stats = archive.stats(conn, IDENTITY, ACCOUNT)

    def no_save(**_kwargs):
        raise AssertionError("Duplicate or mismatching transport must not restore or reopen a cold read")

    monkeypatch.setattr(persistence, "save_state", no_save)
    expected = {"phase": "sent", "chat_id": CHAT, "msg_id": 100, "sent_at": 110.5}
    assert accounting.record_transport(IDENTITY, record["op_id"], **expected)
    for field, value in (("phase", "unknown"), ("phase", "unsent"), ("chat_id", CHAT - 1),
                         ("msg_id", 101), ("sent_at", 110.6), ("sent_at", "110.5")):
        assert not accounting.record_transport(IDENTITY, record["op_id"], **{**expected, field: value})
    assert state_module._meta_state == before
    assert archive.stats(conn, IDENTITY, ACCOUNT) == stats


def test_repeated_contextless_edits_keep_one_cold_owner_without_synthesizing_balances(receipts):
    record, received = archive_unknown_read()
    before = copy.deepcopy(receipts[accounting.STATE_KEY])
    for index in range(6):
        edited = replace(received, event_type="edit", text=panel(sha=9000).text,
                         server_event_at=720 + index, reply_context=None, root_msg_id=0, identity_id=IDENTITY + 1)
        assert observe(edited)
        assert accounting.expire_unanswered_reads(IDENTITY, now=800 + index, timeout=600)
        payload, _digest = archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)
        assert payload["operation"]["op_id"] == record["op_id"]
        assert len(payload["receipts"]) == index + 2
        assert payload["receipts"][-1]["end"]["at"] == 720 + index
        assert receipts[accounting.STATE_KEY]["book"]["ledgers"] == before["book"]["ledgers"]
        assert receipts[accounting.STATE_KEY]["business"] == before["business"]
        assert not receipts[accounting.STATE_KEY]["restored_roots"]
        assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)["commands"] == 1
        assert not accounting.reply_complete(edited, now=800 + index)
    assert reload_state()[accounting.STATE_KEY] == receipts[accounting.STATE_KEY]


@pytest.mark.parametrize("step", ["expiry", "prepare", "restore"])
@pytest.mark.parametrize("failure", ["sqlite_archive", "sqlite_hot", "false", "exception"])
def test_expired_read_archive_and_hot_state_fail_atomically(receipts, monkeypatch, step, failure):
    if step == "restore":
        _record, received = archive_unknown_read()
        receipts = reload_state()
        update = accounting.stage_event(replace(received, event_type="edit", server_event_at=720, text=panel(sha=1800).text), now=721)
        assert update and update.archive_changes

        def execute():
            return accounting.commit_update(update)
    elif step == "prepare":
        legacy_expired_read(receipts)
        receipts = reload_state()

        def execute():
            record, reason = accounting.prepare_operation(IDENTITY, READ, CHAT, 720, source_module=MODULE)
            assert record is None and reason == "persistence_failed"
            return False
    else:
        owned_unknown_read()
        receipts = reload_state()

        def execute():
            return accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600)

    conn = persistence.get_db_conn()
    before, stats = copy.deepcopy(receipts), archive.stats(conn, IDENTITY, ACCOUNT)
    with monkeypatch.context() as patch:
        if failure.startswith("sqlite_"):
            table = "yinluo_archive_commands" if failure == "sqlite_archive" else "identity_runtime_state"
            operation = "DELETE" if step == "restore" and failure == "sqlite_archive" else "INSERT"
            conn.execute(f"CREATE TEMP TRIGGER fail_expired_read BEFORE {operation} ON {table} "
                         "BEGIN SELECT RAISE(ABORT, 'expired read failure'); END")
        else:
            def fail_save(**kwargs):
                assert kwargs["yinluo_archive_changes"]
                if failure == "exception":
                    raise RuntimeError("expired read failure")
                return False

            patch.setattr(persistence, "save_state", fail_save)
        try:
            if failure == "exception":
                with pytest.raises(RuntimeError, match="expired read failure"):
                    execute()
            else:
                assert not execute()
        finally:
            if failure.startswith("sqlite_"):
                conn.execute("DROP TRIGGER fail_expired_read")
    assert not conn.in_transaction
    assert receipts == before
    assert archive.stats(conn, IDENTITY, ACCOUNT) == stats
    restored = reload_state()
    assert restored[accounting.STATE_KEY] == before[accounting.STATE_KEY]
    assert restored[cultivation.STATE_KEY] == before[cultivation.STATE_KEY]
    assert restored["pending_tasks"] == before["pending_tasks"]


@pytest.mark.parametrize("damage", [
    "version_bool", "version_float", "version_unknown", "operation_none", "operation_list", "operation_empty",
    "phase_sent", "phase_unknown", "phase_complete", "operation_root", "operation_interval", "mutation",
    "terminal", "pending", "receipt_command", "receipt_sender", "scopes", "effects",
])
def test_expired_read_payload_is_not_a_general_unresolved_mutation_archive(receipts, damage):
    archive_unknown_read()
    payload, _digest = archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)
    if damage.startswith("version_"):
        payload["version"] = {"version_bool": True, "version_float": 2.0, "version_unknown": 3}[damage]
    elif damage in {"operation_none", "operation_list", "operation_empty"}:
        payload["operation"] = {"operation_none": None, "operation_list": [], "operation_empty": {}}[damage]
    elif damage.startswith("phase_"):
        payload["operation"]["phase"] = damage.removeprefix("phase_")
    elif damage == "operation_root":
        payload["operation"]["msg_id"] = 99
    elif damage == "operation_interval":
        payload["operation"].update(started_at=90, sent_at=91)
    elif damage == "mutation":
        payload["operation"]["command"] = CMD_YINLUO_DAILY_SACRIFICE
        payload["receipts"][0]["command"] = CMD_YINLUO_DAILY_SACRIFICE
    elif damage in {"terminal", "pending"}:
        payload["receipts"][0]["phase"] = "panel" if damage == "terminal" else "pending"
    elif damage == "receipt_command":
        payload["receipts"][0]["command"] = CMD_YINLUO_DAILY_SACRIFICE
    elif damage == "receipt_sender":
        payload["receipts"][0]["sender_id"] = True
    elif damage == "scopes":
        payload["receipts"][0]["scopes"] = ["outcome"]
    else:
        payload["receipts"][0]["effects"] = [{"component": "sacrifice_sha", "resource": "sha", "amount": 500}]
    with pytest.raises(archive.ArchiveConflict):
        accounting._validate_archive_payload(payload, IDENTITY, ACCOUNT, CHAT, 100)


@pytest.mark.parametrize("control", ["module", "identity", "global", "all_actions"])
def test_capacity_calibration_never_enables_disabled_controls(receipts, monkeypatch, control):
    now = fill_uncovered_completions(receipts, monkeypatch)
    if control == "module":
        receipts["yinluo_enabled"] = False
    elif control == "identity":
        state_module.set_identity_enabled(IDENTITY, False)
    elif control == "global":
        state_module._meta_state["global_enabled"] = False
    else:
        config = receipts["yinluo_observation"]["auto_config"]
        for key in config:
            if type(config[key]) is bool:
                config[key] = False
    before = copy.deepcopy(receipts[accounting.STATE_KEY])
    flags = copy.deepcopy(receipts["yinluo_observation"]["auto_config"])
    sender = AsyncMock(side_effect=AssertionError("A capacity query must respect disabled controls"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now))
    sender.assert_not_awaited()
    assert receipts[accounting.STATE_KEY] == before
    assert receipts["yinluo_observation"]["auto_config"] == flags


def test_manual_mutation_does_not_silently_send_a_capacity_query(receipts, monkeypatch):
    now = fill_uncovered_completions(receipts, monkeypatch)
    sender = AsyncMock(side_effect=AssertionError("A manual mutation cannot become another command"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    ok, message, plan = asyncio.run(yinluo.execute_yinluo_manual_action("daily_sacrifice", send_as_id=IDENTITY, now=now))
    assert not ok and message and plan["command"] == CMD_YINLUO_DAILY_SACRIFICE
    sender.assert_not_awaited()


def test_active_read_cannot_be_archived_from_under_its_caller(receipts):
    record, _received = owned_unknown_read()
    before = copy.deepcopy(receipts)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=711, timeout=600, active_ops={record["op_id"]})
    assert receipts == before
    assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)["commands"] == 0


@pytest.mark.parametrize("control", ["module", "identity", "global", "owner", "account", "config", "sect"])
def test_capacity_query_revalidates_queued_owner_and_controls(receipts, monkeypatch, control):
    now = fill_uncovered_completions(receipts, monkeypatch)
    baseline = copy.deepcopy(receipts[accounting.STATE_KEY]["book"])
    calls = []

    async def send(command, **kwargs):
        assert command == READ and kwargs["operation_check"]()
        calls.append(command)
        if control == "module":
            receipts["yinluo_enabled"] = False
        elif control == "identity":
            state_module.set_identity_enabled(IDENTITY, False)
        elif control == "global":
            state_module._meta_state["global_enabled"] = False
        elif control == "owner":
            state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(receipts)
        elif control == "account":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        elif control == "sect":
            state_module.update_send_as_profile(IDENTITY, sect_name="\u5929\u661f\u5b97")
        else:
            receipts["yinluo_observation"]["auto_config"]["daily_sacrifice"] = False
        assert not kwargs["operation_check"]()
        transport._record_game_send_block(IDENTITY, command, "pre_send_guard", "changed", definitely_unsent=True)
        return None

    monkeypatch.setattr(yinluo, "send_game_command", send)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now))
    assert calls == [READ]
    owner = state_module.get_identity_state(IDENTITY)
    assert owner[accounting.STATE_KEY]["book"] == baseline
    assert not owner["pending_tasks"]
    if control not in {"account", "owner"}:
        assert owner[accounting.STATE_KEY]["operations"][-1]["phase"] == "unsent"


@pytest.mark.parametrize("hold", ["capacity", "receipt_conflict", "legacy_pending", "receipt_gap"])
def test_capacity_query_does_not_repair_existing_holds_or_missing_receipts(receipts, monkeypatch, hold):
    now = fill_uncovered_completions(receipts, monkeypatch)
    resource = receipts[accounting.STATE_KEY]
    if hold == "receipt_gap":
        resource["book"]["gap"] = {"reason": "capacity", "at": now}
    else:
        resource["hold"] = hold
    barrier, gap = resource["hold"], copy.deepcopy(resource["book"]["gap"])
    sender = AsyncMock(side_effect=AssertionError("Native calibration cannot repair missing evidence"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now))
    sender.assert_not_awaited()
    assert observe(panel(sha=3500, msg_id=501, start=now + 2, end=now + 3))
    assert receipts[accounting.STATE_KEY]["hold"] == barrier
    assert receipts[accounting.STATE_KEY]["book"]["gap"] == gap
    assert accounting.capacity_status(IDENTITY)["status"] != "ready"


@pytest.mark.parametrize("damage", ["payload", "digest", "contract", "missing_index", "orphan_index"])
def test_corrupt_expired_read_archive_cannot_be_restored_as_valid_evidence(receipts, damage):
    _record, received = archive_unknown_read()
    conn = persistence.get_db_conn()
    if damage in {"payload", "digest"}:
        conn.execute(f"UPDATE yinluo_archive_commands SET {damage}=? WHERE command_msg_id=100", ("{}",))
    elif damage == "contract":
        payload, _digest = archive.read_command(conn, IDENTITY, ACCOUNT, CHAT, 100)
        payload["operation"]["phase"] = "unknown"
        encoded = archive._encode(payload)
        conn.execute("UPDATE yinluo_archive_commands SET payload=?, digest=? WHERE command_msg_id=100",
                     (encoded, archive._digest(encoded)))
    elif damage == "missing_index":
        conn.execute("DELETE FROM yinluo_archive_messages WHERE command_msg_id=100")
    else:
        conn.execute("DELETE FROM yinluo_archive_commands WHERE command_msg_id=100")
    conn.commit()
    before = copy.deepcopy(receipts[accounting.STATE_KEY])
    assert observe(replace(received, event_type="edit", server_event_at=720, text=panel(sha=1800).text))
    assert receipts[accounting.STATE_KEY] == {**before, "hold": "receipt_conflict"}
    assert accounting.resource_balance(IDENTITY, "sha")["value"] is None


@pytest.mark.parametrize("change", ["owner", "account", "pending", "book", "cultivation"])
def test_staged_expired_read_cannot_commit_through_a_replacement(receipts, change):
    record, _received = owned_unknown_read()
    value, reason = accounting.read_accounting(IDENTITY)
    assert not reason
    value["operations"][0]["phase"] = "read_expired"
    changes = []
    accounting._retire_expired_reads(value, changes, [record["op_id"]])
    assert changes
    update = accounting._update(IDENTITY, value, cultivation.read_cultivation_ledger(IDENTITY),
                                archive_changes=changes, expired_reads=[record["op_id"]])
    if change == "owner":
        state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(receipts)
    elif change == "account":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    elif change == "pending":
        receipts["pending_tasks"][CHAT, 100]["op_id"] = "f" * 32
    elif change == "book":
        receipts[accounting.STATE_KEY]["hold"] = "receipt_conflict"
    else:
        assert snapshot(value=600000, at=710, msg_id=500)
    before = copy.deepcopy(state_module._meta_state)
    assert not accounting.commit_update(update)
    assert state_module._meta_state == before
    assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)["commands"] == 0


@pytest.mark.parametrize("contextless", [False, True])
def test_expired_cold_read_does_not_follow_an_account_rebinding(receipts, contextless):
    _record, received = archive_unknown_read()
    state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    edited = replace(received, event_type="edit", server_event_at=720, text=panel(sha=1800).text)
    if contextless:
        edited = replace(edited, reply_context=None, root_msg_id=0)
    before = copy.deepcopy(receipts)
    assert not observe(edited)
    assert receipts == before
    assert archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)


def test_full_receipt_slot_does_not_start_a_query_that_cannot_retain_its_reply(receipts, monkeypatch):
    now = fill_uncovered_completions(receipts, monkeypatch, bound="receipts")
    assert observe(event(CMD_YINLUO_DAILY_SACRIFICE, SACRIFICE_REPLY.replace("500", "600"), root=140,
                         msg_id=141, start=200 + 2 * 86400, end=now + 1, edited=True))
    assert len(receipts[accounting.STATE_KEY]["book"]["receipts"]) == accounting.MAX_RECEIPTS
    now = receipts["yinluo_observation"]["next_daily_sacrifice_time"] + 1
    monkeypatch.setattr(yinluo.time, "time", lambda: now)
    assert accounting.capacity_status(IDENTITY)["status"] == "capacity_read_slot"
    assert accounting.prepare_operation(IDENTITY, READ, CHAT, now, source_module=MODULE) == (None, "capacity")
    sender = AsyncMock(side_effect=AssertionError("No receipt slot is available for a failed calibration"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(now))
    sender.assert_not_awaited()
    assert not receipts[accounting.STATE_KEY]["book"]["gap"]
    assert observe(panel(sha=3600, msg_id=501, start=now + 2, end=now + 3))
    assert accounting.capacity_status(IDENTITY)["status"] == "ready"
    assert accounting.prepare_operation(
        IDENTITY, CMD_YINLUO_DAILY_SACRIFICE, CHAT, now + 10, source_module=MODULE,
    )[0]


@pytest.mark.parametrize("contextless", [False, True])
def test_withdrawn_banner_is_not_used_to_suppress_useful_capacity_calibration(receipts, monkeypatch, contextless):
    now = fill_uncovered_completions(receipts, monkeypatch)
    incomplete = event(READ, "\u3010\u9053\u53cb\u7684\u9634\u7f57\u5e61\u3011\n\u70bc\u5316\u69fd:\n1\u53f7\u69fd: [\u7a7a\u95f2]",
                       root=500, msg_id=501, start=now, end=now + 1)
    assert observe(incomplete)
    assert accounting.capacity_status(IDENTITY)["status"] == "native_coverage_required"
    before = copy.deepcopy(receipts[accounting.STATE_KEY]["business"]["action:banner"])
    withdrawn = replace(incomplete, event_type="edit", text="withdrawn", server_event_at=now + 2)
    if contextless:
        withdrawn = replace(withdrawn, reply_context=None, root_msg_id=0)
    assert observe(withdrawn)
    assert receipts[accounting.STATE_KEY]["business"]["action:banner"] == before
    assert accounting.capacity_status(IDENTITY)["status"] == "banner_required"
