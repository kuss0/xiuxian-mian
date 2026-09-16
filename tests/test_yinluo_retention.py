import copy
from dataclasses import replace
import sqlite3

import pytest

from model import cultivation_accounting as cultivation
from model import persistence, state as state_module
from model import yinluo_accounting as accounting
from model import yinluo_archive as archive
from model import yinluo_resource_book as books
from model.features import wanxin
from test_cultivation_accounting import delta, snapshot
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, CHAT, IDENTITY, TARGET, _start_owned_wanxin_strip, _target, bind, env, event, observe, panel, value,
    runtime as runtime_fixture,
)


runtime = runtime_fixture
ASSIST = "【剥离咒源成功】\n@provider_one 替 @target_one 剥下一段阴罗残咒。幡面煞气被削去 120 点。"


def assist_cycle(runtime, index):
    start, root = 200 + index * 100, 100 + index * 20
    target = _target(runtime)
    commission = target["wanxin_observation"]["commission"]
    commission.update(id=index + 10, published_at=start - 20, accepted_at=start - 10)
    beneficiary = {
        "identity_id": TARGET, "account_id": ACCOUNT + 2, "commission_id": index + 10,
        "published_at": start - 20, "accepted_at": start - 10,
    }
    record, reason = accounting.prepare_operation(
        IDENTITY, ".剥离咒源 @target_one", CHAT, start, source_module="婉心封魂", beneficiary=beneficiary,
    )
    assert reason == "", (index, reason)
    bind(record, root=root, at=start)
    received = event(record["command"], ASSIST, root=root, msg_id=root + 1, start=start, end=start + 2)
    assert observe(received)
    assert target["wanxin_observation"]["commission"]["id"] == 0
    assert observe(panel(sha=2000, msg_id=root + 5, start=start + 4, end=start + 5))
    return received, record


def reload_state():
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    return state_module.get_identity_state(IDENTITY)


def retirement_update():
    state, reason = accounting.read_accounting(IDENTITY)
    assert not reason
    ledger = cultivation.read_cultivation_ledger(IDENTITY)
    changes = []
    accounting._compact_value(state, ledger, changes, force=True)
    return accounting._update(IDENTITY, state, ledger, archive_changes=changes)


def retire():
    update = retirement_update()
    assert update.archive_changes
    assert accounting.commit_update(update)
    return update


def archived_assist(runtime):
    received, record = assist_cycle(runtime, 0)
    retire()
    assert archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, received.root_msg_id)
    return received, record


def test_completed_assistance_does_not_exhaust_operation_slots(runtime):
    for index in range(accounting.MAX_OPERATIONS + 5):
        assist_cycle(runtime, index)
    before = copy.deepcopy(runtime[accounting.STATE_KEY])
    assert not before["hold"]
    assert not before["book"]["gap"]
    assert len(before["operations"]) < accounting.MAX_OPERATIONS
    assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)["commands"] > 0
    assert reload_state()[accounting.STATE_KEY] == before
    assert value() == {"status": "ready", "value": 2000}


def test_native_panels_do_not_exhaust_receipt_slots(runtime):
    for index in range(accounting.MAX_RECEIPTS + 5):
        assert observe(panel(msg_id=100 + index * 3, start=200 + index * 10, end=201 + index * 10))
        assert not runtime[accounting.STATE_KEY]["book"]["gap"], index
    assert len(runtime[accounting.STATE_KEY]["book"]["receipts"]) < accounting.MAX_RECEIPTS
    assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)["commands"] > 0
    assert value() == {"status": "ready", "value": 2000}


def test_multiple_capacity_cycles_keep_hot_state_bounded(runtime, monkeypatch, record_testsuite_property):
    monkeypatch.setattr(accounting, "MAX_OPERATIONS", 6)
    monkeypatch.setattr(accounting, "MAX_RECEIPTS", 12)
    monkeypatch.setattr(books, "MAX_RECEIPTS", 12)
    for index in range(80):
        assist_cycle(runtime, index)
        current, reason = accounting.read_accounting(IDENTITY)
        assert not reason and not current["hold"] and not current["book"]["gap"]
        assert len(current["operations"]) <= 6 and len(current["book"]["receipts"]) <= 12
    stats = archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)
    assert stats["commands"] > 64 and stats["payload_bytes"] > 0
    record_testsuite_property("yinluo_retention_cycles", 80)
    record_testsuite_property("yinluo_cold_commands", stats["commands"])
    record_testsuite_property("yinluo_cold_payload_bytes", stats["payload_bytes"])
    record_testsuite_property("yinluo_hot_operations", len(current["operations"]))
    record_testsuite_property("yinluo_hot_receipts", len(current["book"]["receipts"]))
    before = copy.deepcopy(runtime[accounting.STATE_KEY])
    assert reload_state()[accounting.STATE_KEY] == before
    assert value()["value"] == 2000


def test_archived_duplicate_preserves_completion_without_loading_hot_history(runtime):
    received, _record = archived_assist(runtime)
    runtime = reload_state()
    before = copy.deepcopy(runtime)
    stats = archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)
    assert accounting.reply_complete(received, now=250)
    assert accounting.command_complete(runtime[accounting.STATE_KEY], CHAT, received.root_msg_id, command=received.reply_context["reply_to_command"])
    assert not observe(received)
    assert runtime == before
    assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT) == stats


@pytest.mark.parametrize("new_commission", [False, True])
def test_archived_duplicate_closes_only_its_restored_wanxin_slot(runtime, monkeypatch, new_commission):
    target = _start_owned_wanxin_strip(runtime, monkeypatch)
    original = copy.deepcopy(target["wanxin_observation"]["pending"])
    received = event(".剥离咒源 @target_one", ASSIST)
    assert observe(received)
    assert observe(panel(sha=1880, msg_id=200, start=124, end=125))
    retire()
    if new_commission:
        target["wanxin_observation"]["commission"].update(id=11, published_at=130, accepted_at=140, accepted=True)
    expected = copy.deepcopy(target["wanxin_observation"])
    original["status"] = "unknown"
    target["wanxin_observation"]["unresolved_actions"]["strip"] = original
    assert persistence.save_state()
    runtime = reload_state()
    target = state_module.get_identity_state(TARGET)
    provider = copy.deepcopy(runtime)
    stats = archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)
    assert observe(received)
    assert target["wanxin_observation"] == expected
    assert runtime == provider
    assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT) == stats
    assert value()["value"] == 1880
    with state_module.use_identity(TARGET):
        assert not wanxin.get_wanxin_ui_state()["unresolved_actions"]


@pytest.mark.parametrize("contextless", [False, True])
def test_late_archive_revision_recovers_original_owner_and_stays_uncertain(runtime, contextless):
    received, record = archived_assist(runtime)
    edited = replace(received, event_type="edit", text=ASSIST.replace("120", "130"), server_event_at=250)
    if contextless:
        edited = replace(edited, reply_context=None, root_msg_id=0, identity_id=TARGET)
    assert observe(edited)
    assert value()["value"] is None
    operation = accounting.current_operation(IDENTITY, record["op_id"])
    assert operation["beneficiary"] == record["beneficiary"]
    assert operation["phase"] == ("unknown" if contextless else "complete")
    assert archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, received.root_msg_id) is None
    assert not observe(edited)
    assert observe(panel(msg_id=500, start=260, end=270, sha=1990))
    assert value()["value"] == (1870 if contextless else 1990)
    if contextless:
        assert accounting.admission_reason(IDENTITY, ".化功为煞 10000") == "yinluo_command_in_flight"


@pytest.mark.parametrize("change", ["account", "commission", "helper", "name"])
def test_archived_assist_never_consumes_replacement_commission(runtime, change):
    received, _record = archived_assist(runtime)
    target = _target(runtime)
    if change == "account":
        state_module.set_identity_account(TARGET, ACCOUNT + 3)
    elif change == "commission":
        target["wanxin_observation"]["commission"]["id"] = 99
    elif change == "helper":
        target["wanxin_observation"]["assist"]["send_as_id"] = IDENTITY + 8
    else:
        target["wanxin_observation"]["commission"]["id"] = 99
        state_module.update_send_as_profile(TARGET, username="renamed_target")
    before = copy.deepcopy(target)
    assert observe(replace(received, event_type="edit", text=ASSIST.replace("120", "130"), server_event_at=250))
    assert target == before
    assert value()["value"] is None


def test_archived_manual_assistance_cannot_acquire_new_beneficiary(runtime):
    target = _target(runtime)
    received = event(".剥离咒源 @target_one", ASSIST, start=200, end=202)
    assert observe(received)
    assert observe(panel(msg_id=200, start=204, end=205))
    retire()
    stored, _digest = archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)
    assert stored["operation"] is None
    target = _target(runtime)
    before = copy.deepcopy(target)
    assert observe(replace(received, event_type="edit", server_event_at=250, text=ASSIST.replace("120", "130")))
    assert target == before
    reload_state()
    target = state_module.get_identity_state(TARGET)
    assert observe(replace(received, event_type="edit", server_event_at=260, text=ASSIST.replace("120", "140")))
    assert target == before


def test_archive_restoration_does_not_follow_provider_rebind(runtime):
    received, _record = archived_assist(runtime)
    before = copy.deepcopy(runtime)
    state_module.set_identity_account(IDENTITY, ACCOUNT + 10)
    assert not observe(replace(received, event_type="edit", server_event_at=250, text=""))
    assert runtime[accounting.STATE_KEY] == before[accounting.STATE_KEY]
    assert archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, received.root_msg_id)


@pytest.mark.parametrize("part", ["payload", "digest"])
def test_corrupt_archive_holds_without_replacing_hot_state(runtime, part):
    received, _record = archived_assist(runtime)
    conn = persistence.get_db_conn()
    conn.execute(f"UPDATE yinluo_archive_commands SET {part}=? WHERE command_msg_id=?", ("{}", received.root_msg_id))
    conn.commit()
    before = copy.deepcopy(runtime[accounting.STATE_KEY])
    assert observe(received)
    assert runtime[accounting.STATE_KEY] == {**before, "hold": "receipt_conflict"}
    assert value()["value"] is None
    assert not accounting.reply_complete(received, now=250)
    assert accounting.prepare_operation(IDENTITY, ".化功为煞 10000", CHAT, 300, source_module="阴罗宗")[0] is None


@pytest.mark.parametrize("operation", ["restore", "retire"])
def test_archive_and_hot_state_roll_back_together_on_sql_error(runtime, operation):
    if operation == "restore":
        received, _record = archived_assist(runtime)
        update = accounting.stage_event(replace(received, event_type="edit", server_event_at=250,
                                               text=ASSIST.replace("120", "130")), now=250)
    else:
        assist_cycle(runtime, 0)
        update = retirement_update()
    assert update.archive_changes
    conn = persistence.get_db_conn()
    before, stats = copy.deepcopy(runtime), archive.stats(conn, IDENTITY, ACCOUNT)
    conn.execute("CREATE TEMP TRIGGER fail_hot BEFORE INSERT ON identity_runtime_state "
                 "BEGIN SELECT RAISE(ABORT, 'test failure after archive write'); END")
    try:
        assert not accounting.commit_update(update)
    finally:
        conn.execute("DROP TRIGGER fail_hot")
    assert not conn.in_transaction
    assert runtime == before
    assert archive.stats(conn, IDENTITY, ACCOUNT) == stats
    assert reload_state()[accounting.STATE_KEY] == before[accounting.STATE_KEY]


def test_stale_archive_cas_cannot_delete_newer_evidence(runtime):
    received, _record = archived_assist(runtime)
    update = accounting.stage_event(replace(received, event_type="edit", server_event_at=250, text=""), now=250)
    conn = persistence.get_db_conn()
    payload, digest = archive.read_command(conn, IDENTITY, ACCOUNT, CHAT, received.root_msg_id)
    payload["operation"]["op_id"] = "f" * 32
    archive.apply_changes(conn, [archive.ArchiveChange(IDENTITY, ACCOUNT, CHAT, received.root_msg_id, digest, payload)])
    conn.commit()
    before = copy.deepcopy(runtime)
    assert not accounting.commit_update(update)
    assert not conn.in_transaction
    assert runtime == before
    assert archive.read_command(conn, IDENTITY, ACCOUNT, CHAT, received.root_msg_id)[0] == payload


def test_archive_cas_holds_write_lock_before_reading_revision(runtime):
    received, _record = archived_assist(runtime)
    conn = persistence.get_db_conn()
    payload, digest = archive.read_command(conn, IDENTITY, ACCOUNT, CHAT, received.root_msg_id)
    other = sqlite3.connect(persistence.DB_FILE, timeout=0)
    try:
        archive.apply_changes(conn, [archive.ArchiveChange(IDENTITY, ACCOUNT, CHAT, received.root_msg_id, digest, payload)])
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("DELETE FROM yinluo_archive_commands WHERE command_msg_id=?", (received.root_msg_id,))
        conn.rollback()
    finally:
        other.close()
    assert archive.read_command(conn, IDENTITY, ACCOUNT, CHAT, received.root_msg_id)[1] == digest


def test_compaction_cannot_hide_a_conflicting_hot_result_owner(runtime, monkeypatch):
    monkeypatch.setattr(accounting, "MAX_RECEIPTS", 1)
    before = copy.deepcopy(runtime[accounting.STATE_KEY])
    received = replace(panel(), root_msg_id=18, reply_context={**panel().reply_context, "root_msg_id": 18, "reply_to_msg_id": 18})
    assert observe(received)
    assert runtime[accounting.STATE_KEY] == {**before, "hold": "receipt_conflict"}
    assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)["commands"] == 0


def test_contextless_archived_panel_edit_withdraws_only_its_native_baseline(runtime):
    received = panel()
    retire()
    assert observe(replace(received, event_type="edit", reply_context=None, root_msg_id=0,
                           text="", server_event_at=120))
    assert runtime[accounting.STATE_KEY]["book"]["ledgers"]["sha"]["baseline"]["conflicted"]
    assert value()["value"] is None
    assert observe(panel(msg_id=50, start=129, end=130))
    assert value()["value"] == 2000


@pytest.mark.parametrize("damage", ["missing_index", "orphan_index", "orphan_native", "contract", "size"])
def test_archive_integrity_failures_do_not_restore_unvalidated_facts(runtime, monkeypatch, damage):
    received, _record = archived_assist(runtime)
    conn = persistence.get_db_conn()
    if damage == "missing_index":
        conn.execute("DELETE FROM yinluo_archive_messages WHERE msg_id=?", (received.msg_id,))
    elif damage in {"orphan_index", "orphan_native"}:
        conn.execute("DELETE FROM yinluo_archive_commands WHERE command_msg_id=?", (received.root_msg_id,))
        if damage == "orphan_index":
            received = replace(received, event_type="edit", reply_context=None, root_msg_id=0, server_event_at=250)
    elif damage == "contract":
        payload, _digest = archive.read_command(conn, IDENTITY, ACCOUNT, CHAT, received.root_msg_id)
        payload["operation"]["phase"] = "unknown"
        encoded = archive._encode(payload)
        conn.execute("UPDATE yinluo_archive_commands SET payload=?, digest=? WHERE command_msg_id=?",
                     (encoded, archive._digest(encoded), received.root_msg_id))
    else:
        monkeypatch.setattr(archive, "MAX_COMMAND_BYTES", 10)
    conn.commit()
    before = copy.deepcopy(runtime[accounting.STATE_KEY])
    assert observe(received)
    assert runtime[accounting.STATE_KEY] == {**before, "hold": "receipt_conflict"}


def test_archive_batch_cannot_modify_another_account(runtime):
    update = retirement_update()
    update.archive_changes = tuple(replace(change, account_id=ACCOUNT + 1) for change in update.archive_changes)
    before = copy.deepcopy(runtime)
    assert not accounting.commit_update(update)
    assert runtime == before
    assert archive.stats(persistence.get_db_conn(), IDENTITY, ACCOUNT + 1)["commands"] == 0


def test_restoration_markers_are_bounded_by_real_hot_commands(runtime):
    received, _record = archived_assist(runtime)
    assert observe(replace(received, event_type="edit", server_event_at=250, text=ASSIST.replace("120", "130")))
    update = retirement_update()
    update.value["restored_roots"].append([CHAT, 99999])
    assert not accounting.commit_update(update)
    assert observe(panel(msg_id=500, start=260, end=270))
    retire()
    assert runtime[accounting.STATE_KEY]["restored_roots"] == []


def test_retirement_preserves_foreign_cultivation_entries_and_business_clocks(runtime):
    assist_cycle(runtime, 0)
    assert delta(key="duel:other", amount=300, start=210, end=215)
    old_ledger = copy.deepcopy(cultivation.read_cultivation_ledger(IDENTITY))
    old_business = copy.deepcopy(runtime[accounting.STATE_KEY]["business"])
    old_observed = copy.deepcopy(runtime["yinluo_observation"])
    retire()
    assert cultivation.read_cultivation_ledger(IDENTITY) == old_ledger
    assert runtime[accounting.STATE_KEY]["business"] == old_business
    assert runtime["yinluo_observation"] == old_observed
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 500300


def test_native_coverage_cannot_retire_unknown_operation(runtime):
    record, reason = accounting.prepare_operation(IDENTITY, ".化功为煞 10000", CHAT, 200, source_module="阴罗宗")
    assert not reason
    bind(record, at=200)
    assert accounting.record_transport(IDENTITY, record["op_id"], phase="unknown")
    assert snapshot(490000, 250, 300)
    assert observe(panel(msg_id=350, start=259, end=260, sha=4000))
    retire()
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "unknown"
    assert not archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)


def test_early_success_cannot_retire_its_still_unbound_operation(runtime):
    record, reason = accounting.prepare_operation(IDENTITY, ".剥离咒源 @target_one", CHAT, 200, source_module="阴罗宗")
    assert not reason
    assert observe(event(record["command"], ASSIST, start=200, end=202))
    assert observe(panel(msg_id=200, start=204, end=205))
    retire()
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "prepared"
    assert not archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)
    bind(record, at=200)
    retire()
    assert archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)


def test_unknown_soul_name_cannot_be_discarded_after_other_balances(runtime):
    assert observe(event(".召唤魔影", "召唤成功，镇压成功！所得魂魄未明。", start=200, end=202))
    assert snapshot(500000, 250, 300)
    assert observe(panel(msg_id=350, start=259, end=260))
    retire()
    assert not archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)
    assert any(item["command"] == ".召唤魔影" for item in runtime[accounting.STATE_KEY]["book"]["receipts"])


def test_calibration_at_receipt_capacity_retires_only_covered_terminal_work(runtime, monkeypatch):
    received = event(".剥离咒源 @target_one", ASSIST, start=200, end=202)
    assert observe(received)
    monkeypatch.setattr(accounting, "MAX_RECEIPTS", 2)
    monkeypatch.setattr(books, "MAX_RECEIPTS", 2)
    assert observe(panel(msg_id=300, start=249, end=250))
    assert not runtime[accounting.STATE_KEY]["book"]["gap"]
    assert value()["value"] == 2000
    assert archive.read_command(persistence.get_db_conn(), IDENTITY, ACCOUNT, CHAT, 100)


def test_read_slot_is_available_when_uncovered_completions_fill_mutation_slots(runtime, monkeypatch):
    monkeypatch.setattr(accounting, "MAX_OPERATIONS", 2)
    received, _record = assist_cycle(runtime, 0)
    # A later edit is terminal but its financial interval lacks new coverage.
    assert observe(replace(received, event_type="edit", server_event_at=250, text=ASSIST.replace("120", "130")))
    read, reason = accounting.prepare_operation(IDENTITY, ".我的阴罗幡", CHAT, 300, source_module="阴罗宗")
    assert not reason and read
    bind(read, root=400, at=300)
    assert observe(panel(msg_id=401, start=300, end=310))
    assert value()["value"] == 2000


def test_invalid_archive_batch_is_rejected_before_any_sql_write(runtime):
    update = retirement_update()
    change = update.archive_changes[0]
    conn = persistence.get_db_conn()
    trace = []
    conn.set_trace_callback(trace.append)
    try:
        with pytest.raises(archive.ArchiveConflict):
            archive.apply_changes(conn, [change, replace(change, chat_id=0)])
        with pytest.raises(archive.ArchiveConflict):
            archive.apply_changes(conn, [replace(change, payload={"version": True, "receipts": [], "operation": None})])
        with pytest.raises(archive.ArchiveConflict):
            archive.apply_changes(conn, [change, change])
    finally:
        conn.set_trace_callback(None)
    assert not any(sql.startswith(("INSERT", "DELETE", "BEGIN")) for sql in trace)


def test_conflicting_result_ownership_does_not_credit_a_new_command(runtime):
    received, _record = archived_assist(runtime)
    before = copy.deepcopy(runtime[accounting.STATE_KEY])
    counterfeit = event(".剥离咒源 @target_one", ASSIST, root=received.root_msg_id - 1,
                        msg_id=received.msg_id, start=received.reply_context["reply_to_server_at"], end=250)
    assert observe(counterfeit)
    assert runtime[accounting.STATE_KEY] == {**before, "hold": "receipt_conflict"}


@pytest.mark.parametrize("foreign", ["message", "sender", "chat"])
def test_unrelated_contextless_edit_does_not_read_hot_books_with_archives(runtime, monkeypatch, foreign):
    received, _record = archived_assist(runtime)
    edited = replace(received, event_type="edit", reply_context=None, root_msg_id=0, server_event_at=250)
    edited = replace(edited, **{
        "message": {"msg_id": 9999}, "sender": {"sender_id": received.sender_id + 1},
        "chat": {"chat_id": CHAT - 1},
    }[foreign])
    monkeypatch.setattr(accounting, "read_accounting", lambda *_args: pytest.fail("unrelated identity read"))
    assert not observe(edited)
