import copy
from dataclasses import replace
import sqlite3
from unittest.mock import AsyncMock

import pytest

from model import cultivation_accounting as cultivation
from model import persistence, state as state_module
from model import yinluo_accounting as accounting
from model import yinluo_archive as archive
from model.features import wanxin, yinluo
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, CHAT, IDENTITY, bind, env, event, observe, panel, value,
    runtime as runtime_fixture,
)
from test_yinluo_retention import ASSIST, reload_state, retire


runtime = runtime_fixture
MODULE = "\u5a49\u5fc3\u5c01\u9b42"
STRIP = ".\u5265\u79bb\u5492\u6e90"
BANNER = ".\u501f\u5e61\u9547\u9b42"
BANNER_REPLY = (
    "\u3010\u501f\u5e61\u9547\u9b42\u3011\n@provider_one \u501f\u9634\u7f57\u5e61\u538b\u4f4f\u5c01\u9b42\u5492\u53cd\u6251\uff0c"
    "\u5e61\u9762\u715e\u6c14\u88ab\u524a\u53bb 80 \u70b9\u3002\n"
    "@target_one \u9b42\u5c01 -13\uff0c\u6708\u9b44 +1\uff1b\u5492\u5e08\u8d21\u732e +100\u3002"
)


def beneficiary_cycle(index, *, action="strip", complete=True, target_index=None):
    target_index = index if target_index is None else target_index
    target_id, account_id = IDENTITY + target_index + 1, ACCOUNT + target_index + 2
    start, root = 200 + index * 100, 100 + index * 20
    username = f"target_{target_index}"
    state_module.set_identity_account(target_id, account_id)
    state_module.update_send_as_profile(target_id, username=username, enabled=True)
    target = state_module.get_identity_state(target_id)
    target["wanxin_enabled"] = True
    target["wanxin_observation"] = wanxin.normalize_wanxin_observation({
        "assist": {"send_as_id": IDENTITY},
        "commission": {"id": index + 10, "accepted": True, "published_at": start - 20,
                       "accepted_at": start - 10, "helper_username": "provider_one"},
    })
    beneficiary = {"identity_id": target_id, "account_id": account_id, "commission_id": index + 10,
                   "published_at": start - 20, "accepted_at": start - 10}
    command = f"{STRIP if action == 'strip' else BANNER} @{username}"
    record, reason = accounting.prepare_operation(IDENTITY, command, CHAT, start,
                                                  source_module=MODULE, beneficiary=beneficiary)
    assert not reason
    bind(record, root=root, at=start)
    text = ASSIST if action == "strip" else BANNER_REPLY
    received = event(command, text.replace("target_one", username), root=root, msg_id=root + 1,
                     start=start, end=start + 2)
    if complete:
        assert observe(received)
    return target_id, received, record


@pytest.mark.parametrize("remove_old", [False, True])
def test_many_beneficiaries_do_not_permanently_exhaust_business_points(runtime, monkeypatch, remove_old):
    monkeypatch.setattr(accounting, "MAX_BUSINESS_POINTS", 8)
    baseline = copy.deepcopy(cultivation.read_cultivation_ledger(IDENTITY))
    for index in range(12):
        target_id, received, _record = beneficiary_cycle(index)
        book = runtime[accounting.STATE_KEY]
        assert not book["hold"], f"Business capacity blocked completed assistance at beneficiary {index}"
        assert state_module.get_identity_state(target_id)["wanxin_observation"]["commission"]["id"] == 0
        assert len(book["business"]) <= accounting.MAX_BUSINESS_POINTS
        root = received.root_msg_id
        assert observe(panel(msg_id=root + 5, start=received.server_event_at + 2,
                             end=received.server_event_at + 3))
        if remove_old:
            assert state_module.remove_identity(target_id)
        assert value() == {"status": "ready", "value": 2000}
    assert cultivation.read_cultivation_ledger(IDENTITY) == baseline
    assert archive.business_stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)["points"] == 8
    assert persistence.save_state()
    assert reload_state()[accounting.STATE_KEY] == runtime[accounting.STATE_KEY]


def filled_history(monkeypatch, *, count=5, action="banner"):
    monkeypatch.setattr(accounting, "MAX_BUSINESS_POINTS", 8)
    first = None
    for index in range(count):
        target_id, received, record = beneficiary_cycle(index, action=action)
        if index == 0 and action == "banner":
            received = replace(received, event_type="edit", server_event_at=212)
            assert observe(received)
        if first is None:
            first = target_id, received, record
        assert observe(panel(msg_id=received.root_msg_id + 5, start=received.server_event_at + 2,
                             end=received.server_event_at + 3))
    assert persistence.save_state()
    return first


def staged_assist(received):
    update = accounting.stage_event(received, now=received.server_event_at + 1)
    assert update is not None
    observations = {IDENTITY: {"yinluo_observation": yinluo._project_yinluo_observation(update, received.text)}}
    observations.update(wanxin.project_assist_resource_reply(update, received.text))
    assert update.business_changes
    return update, observations


@pytest.mark.parametrize("cold_command", [False, True])
def test_cold_business_boundary_rejects_duplicate_and_out_of_order_edits(runtime, monkeypatch, cold_command):
    target_id, received, _record = filled_history(monkeypatch)
    if cold_command:
        retire()
    runtime = reload_state()
    key = f"assist:{target_id}:banner"
    conn = persistence.get_db_conn()
    stored = archive.read_business_point(conn, IDENTITY, ACCOUNT, key)
    assert stored and stored[0]["points"]["end"]["at"] == 212
    assert key not in runtime[accounting.STATE_KEY]["business"]
    before = copy.deepcopy(state_module.get_identity_state(target_id))
    assert not observe(received)
    assert state_module.get_identity_state(target_id) == before
    older = replace(received, server_event_at=207)
    assert observe(older)
    assert state_module.get_identity_state(target_id) == before
    assert archive.read_business_point(conn, IDENTITY, ACCOUNT, key) == stored
    assert not observe(older)
    assert value() == {"status": "ready", "value": 2000}


@pytest.mark.parametrize("cold_command", [False, True])
def test_newer_owned_revision_restores_only_its_cold_business_key(runtime, monkeypatch, cold_command):
    target_id, received, record = filled_history(monkeypatch)
    if cold_command:
        retire()
    key = f"assist:{target_id}:banner"
    before = copy.deepcopy(runtime[accounting.STATE_KEY]["business"])
    edited = replace(received, server_event_at=650, text=received.text.replace("+100", "+120"))
    assert observe(edited)
    points = runtime[accounting.STATE_KEY]["business"][key]
    assert points["end"]["at"] == 650 and points["start"]["at"] == record["started_at"]
    assert points["start"]["evidence"]["msg_id"] == received.root_msg_id
    assert archive.read_business_point(persistence.get_db_conn(), IDENTITY, ACCOUNT, key) is None
    assert len(runtime[accounting.STATE_KEY]["business"]) == len(before)
    target = state_module.get_identity_state(target_id)
    assert target["wanxin_observation"]["assist"]["last_contrib_gain"] == 120
    assert target["wanxin_observation"]["commission"]["id"] == 10
    assert not observe(edited)
    assert reload_state()[accounting.STATE_KEY] == runtime[accounting.STATE_KEY]


@pytest.mark.parametrize("cold_command", [False, True])
def test_new_commission_reuses_cold_key_without_reviving_old_business(runtime, monkeypatch, cold_command):
    target_id, previous_reply, _record = filled_history(monkeypatch, count=6)
    if cold_command:
        retire()
    key = f"assist:{target_id}:banner"
    assert archive.read_business_point(persistence.get_db_conn(), IDENTITY, ACCOUNT, key)
    same_target, received, _record = beneficiary_cycle(6, action="banner", target_index=0)
    assert same_target == target_id
    assert archive.read_business_point(persistence.get_db_conn(), IDENTITY, ACCOUNT, key) is None
    boundary = copy.deepcopy(runtime[accounting.STATE_KEY]["business"][key])
    assert boundary["start"]["evidence"]["msg_id"] == received.root_msg_id
    assert boundary["end"]["at"] == received.server_event_at
    target = state_module.get_identity_state(target_id)
    before = copy.deepcopy(target)
    assert before["wanxin_observation"]["assist"]["bannered_commission_id"] == 16
    assert observe(replace(previous_reply, server_event_at=850, text=previous_reply.text.replace("+100", "+120")))
    assert target == before
    assert runtime[accounting.STATE_KEY]["business"][key] == boundary
    assert reload_state()[accounting.STATE_KEY]["business"][key] == boundary


def test_contextless_edit_does_not_destroy_cold_business_boundary(runtime, monkeypatch):
    target_id, received, _record = filled_history(monkeypatch)
    retire()
    key = f"assist:{target_id}:banner"
    conn = persistence.get_db_conn()
    stored = archive.read_business_point(conn, IDENTITY, ACCOUNT, key)
    before = copy.deepcopy(state_module.get_identity_state(target_id))
    edited = replace(received, server_event_at=650, reply_context=None, root_msg_id=0, identity_id=target_id)
    assert observe(edited)
    assert state_module.get_identity_state(target_id) == before
    assert archive.read_business_point(conn, IDENTITY, ACCOUNT, key) == stored
    assert not observe(edited)
    assert observe(replace(received, server_event_at=660))
    assert runtime[accounting.STATE_KEY]["business"][key]["end"]["at"] == 660


def test_business_lookup_reads_native_cold_proof_without_restoring_it(runtime, monkeypatch):
    target_id, _received, _record = filled_history(monkeypatch)
    retire()
    before = copy.deepcopy(state_module._meta_state)
    conn = persistence.get_db_conn()
    stats = archive.stats(conn, IDENTITY, ACCOUNT)
    business_stats = archive.business_stats(conn, IDENTITY, ACCOUNT)
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        for _ in range(3):
            stored = accounting._stored_business_point(runtime[accounting.STATE_KEY], f"assist:{target_id}:banner")
            assert stored[0]["points"]["end"]["at"] == 212
    finally:
        conn.set_trace_callback(None)
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE", "BEGIN")) for sql in statements)
    assert state_module._meta_state == before
    assert archive.stats(conn, IDENTITY, ACCOUNT) == stats
    assert archive.business_stats(conn, IDENTITY, ACCOUNT) == business_stats


def test_missing_cold_point_cannot_turn_an_old_reply_into_new_business(runtime, monkeypatch):
    target_id, received, _record = filled_history(monkeypatch)
    key = f"assist:{target_id}:banner"
    conn = persistence.get_db_conn()
    assert archive.read_business_point(conn, IDENTITY, ACCOUNT, key)
    conn.execute("DELETE FROM yinluo_archive_business WHERE business_key=?", (key,))
    conn.commit()
    before = copy.deepcopy(state_module.get_identity_state(target_id))
    observe(replace(received, server_event_at=207))
    assert state_module.get_identity_state(target_id) == before
    assert key not in runtime[accounting.STATE_KEY]["business"]
    assert accounting.capacity_status(IDENTITY)["status"] == "archive_conflict"


@pytest.mark.parametrize("step", ["retire", "restore"])
@pytest.mark.parametrize("failure", ["sqlite_index", "sqlite_hot", "false", "exception"])
def test_business_index_and_resource_projection_roll_back_together(runtime, monkeypatch, step, failure):
    if step == "retire":
        filled_history(monkeypatch, count=4)
        _target_id, received, _record = beneficiary_cycle(4, complete=False)
        assert persistence.save_state()
    else:
        _target_id, received, _record = filled_history(monkeypatch)
        received = replace(received, server_event_at=650, text=received.text.replace("+100", "+120"))
    reload_state()
    update, observations = staged_assist(received)
    before = copy.deepcopy(state_module._meta_state)
    conn = persistence.get_db_conn()
    stats = archive.business_stats(conn, IDENTITY, ACCOUNT)
    with monkeypatch.context() as patch:
        if failure.startswith("sqlite_"):
            table = "yinluo_archive_business" if failure == "sqlite_index" else "identity_runtime_state"
            conn.execute(f"CREATE TEMP TRIGGER fail_business BEFORE INSERT ON {table} "
                         "BEGIN SELECT RAISE(ABORT, 'business index failure'); END")
        else:
            def fail_save(**kwargs):
                assert kwargs["yinluo_business_changes"]
                if failure == "exception":
                    raise RuntimeError("business index failure")
                return False

            patch.setattr(persistence, "save_state", fail_save)
        try:
            if failure == "exception":
                with pytest.raises(RuntimeError, match="business index failure"):
                    accounting.commit_update(update, observations=observations)
            else:
                assert not accounting.commit_update(update, observations=observations)
        finally:
            if failure.startswith("sqlite_"):
                conn.execute("DROP TRIGGER fail_business")
    assert not conn.in_transaction
    assert state_module._meta_state == before
    assert archive.business_stats(conn, IDENTITY, ACCOUNT) == stats
    owner = reload_state()
    assert owner[accounting.STATE_KEY] == before["identity_states"][IDENTITY][accounting.STATE_KEY]
    assert owner[cultivation.STATE_KEY] == before["identity_states"][IDENTITY][cultivation.STATE_KEY]
    assert observe(received)
    assert not owner[accounting.STATE_KEY]["hold"]


def test_default_business_limit_survives_a_real_roster_turnover(runtime, record_testsuite_property):
    assert accounting.MAX_BUSINESS_POINTS == 120
    count = accounting.MAX_BUSINESS_POINTS + 2
    for index in range(count):
        target_id, received, _record = beneficiary_cycle(index)
        assert not runtime[accounting.STATE_KEY]["hold"], index
        assert observe(panel(msg_id=received.root_msg_id + 5, start=received.server_event_at + 2,
                             end=received.server_event_at + 3))
        assert state_module.remove_identity(target_id)
    assert len(runtime[accounting.STATE_KEY]["business"]) == 120
    assert value() == {"status": "ready", "value": 2000}
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 500000
    conn = persistence.get_db_conn()
    stats = archive.business_stats(conn, IDENTITY, ACCOUNT)
    assert stats["points"] == 6
    assert runtime[accounting.STATE_KEY]["business_archive"] == archive.business_manifest(conn, IDENTITY, ACCOUNT)
    record_testsuite_property("business_retention_beneficiaries", count)
    record_testsuite_property("business_hot_points", len(runtime[accounting.STATE_KEY]["business"]))
    record_testsuite_property("business_cold_points", stats["points"])
    record_testsuite_property("business_cold_payload_bytes", stats["payload_bytes"])
    assert persistence.save_state()
    assert reload_state()[accounting.STATE_KEY] == runtime[accounting.STATE_KEY]


@pytest.mark.parametrize("damage", ["payload", "digest", "native_operation", "native_index", "wrong_key", "replacement_row"])
def test_cold_business_corruption_never_reopens_a_clock(runtime, monkeypatch, damage):
    target_id, received, _record = filled_history(monkeypatch)
    retire()
    key = f"assist:{target_id}:banner"
    conn = persistence.get_db_conn()
    if damage in {"payload", "digest"}:
        conn.execute(f"UPDATE yinluo_archive_business SET {damage}=? WHERE business_key=?", ("{}", key))
    elif damage == "native_operation":
        conn.execute("DELETE FROM yinluo_archive_commands WHERE command_msg_id=100")
    elif damage == "native_index":
        conn.execute("DELETE FROM yinluo_archive_messages WHERE command_msg_id=100")
    elif damage == "wrong_key":
        conn.execute("UPDATE yinluo_archive_business SET business_key=? WHERE business_key=?",
                     (f"assist:{target_id + 100}:banner", key))
    else:
        payload, _digest = archive.read_business_point(conn, IDENTITY, ACCOUNT, key)
        payload["points"]["end"]["at"] = 202
        encoded = archive._encode(payload)
        conn.execute("UPDATE yinluo_archive_business SET payload=?, digest=? WHERE business_key=?",
                     (encoded, archive._digest(encoded), key))
    conn.commit()
    before = copy.deepcopy(state_module.get_identity_state(target_id))
    observe(replace(received, server_event_at=650))
    assert state_module.get_identity_state(target_id) == before
    assert key not in runtime[accounting.STATE_KEY]["business"]


@pytest.mark.parametrize("change", ["provider", "account", "hot_type", "cold_revision", "proof"])
def test_staged_business_retirement_rejects_changed_state(runtime, monkeypatch, change):
    filled_history(monkeypatch, count=4)
    _target, received, _record = beneficiary_cycle(4, complete=False)
    update, observations = staged_assist(received)
    if change == "provider":
        state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(runtime)
    elif change == "account":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 99)
    elif change == "hot_type":
        key = update.business_changes[0].business_key
        runtime[accounting.STATE_KEY]["business"][key]["end"]["at"] = float(
            runtime[accounting.STATE_KEY]["business"][key]["end"]["at"])
    elif change == "cold_revision":
        archive.apply_changes(persistence.get_db_conn(), (), business_changes=update.business_changes)
        persistence.get_db_conn().commit()
    else:
        runtime[accounting.STATE_KEY]["operations"][0]["beneficiary"]["identity_id"] += 100
    before = copy.deepcopy(state_module._meta_state)
    assert not accounting.commit_update(update, observations=observations)
    assert state_module._meta_state == before


@pytest.mark.parametrize("unproved", ["manual", "other_beneficiary"])
def test_business_capacity_cannot_evict_unbound_or_foreign_points(runtime, monkeypatch, unproved):
    target_id, _received, _record = beneficiary_cycle(0)
    assert observe(panel(msg_id=105, start=204, end=205))
    monkeypatch.setattr(accounting, "MAX_BUSINESS_POINTS", 5)
    key = f"assist:{target_id}:strip"
    before = copy.deepcopy(runtime[accounting.STATE_KEY]["business"][key])
    operation = runtime[accounting.STATE_KEY]["operations"][0]
    if unproved == "manual":
        operation["beneficiary"] = {}
    else:
        operation["beneficiary"]["identity_id"] += 100
    beneficiary_cycle(1)
    assert runtime[accounting.STATE_KEY]["hold"] == "capacity"
    assert runtime[accounting.STATE_KEY]["business"][key] == before
    assert archive.business_stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)["points"] == 0


@pytest.mark.parametrize("damage", ["bool", "float", "identity", "account", "key", "start_edit", "end_type", "reverse"])
def test_business_payload_types_and_native_interval_are_strict(runtime, monkeypatch, damage):
    target_id, _received, _record = filled_history(monkeypatch)
    key = f"assist:{target_id}:banner"
    payload, _digest = archive.read_business_point(persistence.get_db_conn(), IDENTITY, ACCOUNT, key)
    if damage in {"bool", "float"}:
        payload["version"] = True if damage == "bool" else 1.0
    elif damage == "identity":
        payload["identity_id"] += 1
    elif damage == "account":
        payload["account_id"] += 1
    elif damage == "key":
        payload["business_key"] = "action:banner"
    elif damage == "start_edit":
        payload["points"]["start"]["evidence"]["edited"] = True
    elif damage == "end_type":
        payload["points"]["end"]["at"] = "212"
    else:
        payload["points"]["start"], payload["points"]["end"] = payload["points"]["end"], payload["points"]["start"]
    with pytest.raises(archive.ArchiveConflict):
        accounting._validate_business_payload(payload, IDENTITY, ACCOUNT, key)


@pytest.mark.parametrize("has_cold", [False, True])
def test_legacy_business_schema_only_upgrades_an_empty_archive(runtime, monkeypatch, has_cold):
    if has_cold:
        filled_history(monkeypatch)
    runtime[accounting.STATE_KEY].pop("business_archive")
    before = copy.deepcopy(runtime)
    current, reason = accounting.read_accounting(IDENTITY)
    assert not reason
    assert current["business_archive"] == archive.empty_business_manifest()
    update = accounting._update(IDENTITY, current, cultivation.read_cultivation_ledger(IDENTITY))
    assert accounting.commit_update(update) is not has_cold
    if has_cold:
        assert runtime == before
        assert accounting.capacity_status(IDENTITY)["status"] == "archive_conflict"
    else:
        assert reload_state()[accounting.STATE_KEY]["business_archive"] == archive.empty_business_manifest()


def test_point_removal_cannot_commit_without_its_cold_write(runtime, monkeypatch):
    filled_history(monkeypatch, count=4)
    _target, received, _record = beneficiary_cycle(4, complete=False)
    update, observations = staged_assist(received)
    update.business_changes = ()
    before = copy.deepcopy(state_module._meta_state)
    assert not accounting.commit_update(update, observations=observations)
    assert state_module._meta_state == before


def test_point_restoration_cannot_commit_without_its_cold_removal(runtime, monkeypatch):
    _target, received, _record = filled_history(monkeypatch)
    received = replace(received, server_event_at=650, text=received.text.replace("+100", "+120"))
    update, observations = staged_assist(received)
    update.business_changes = tuple(change for change in update.business_changes if change.payload is not None)
    before = copy.deepcopy(state_module._meta_state)
    stats = archive.business_stats(persistence.get_db_conn(), IDENTITY, ACCOUNT)
    assert not accounting.commit_update(update, observations=observations)
    assert state_module._meta_state == before
    assert archive.business_stats(persistence.get_db_conn(), IDENTITY, ACCOUNT) == stats


@pytest.mark.parametrize("action", ["banner", "strip"])
@pytest.mark.parametrize("prior_count", [1, 2])
def test_full_projection_never_retires_its_own_new_point_and_loses_terminal_fact(runtime, monkeypatch, action, prior_count):
    monkeypatch.setattr(accounting, "MAX_BUSINESS_POINTS", 4 + prior_count)
    old_targets = []
    for index in range(prior_count):
        old_target, previous, _record = beneficiary_cycle(index, action=action)
        old_targets.append(old_target)
        assert observe(panel(msg_id=previous.root_msg_id + 5, start=previous.server_event_at + 2,
                             end=previous.server_event_at + 3))
    target_id, received, record = beneficiary_cycle(prior_count, action=action, complete=False)
    shortage = (
        "\u4f60\u7684\u9634\u7f57\u5e61\u715e\u6c14\u4e0d\u8db3\uff0c"
        f"{BANNER[1:] if action == 'banner' else STRIP[1:]}"
        f"\u81f3\u5c11\u9700\u8981 {80 if action == 'banner' else 120} \u70b9\u715e\u6c14\u3002"
    )
    received = replace(received, text=shortage)
    assert observe(received)
    current = runtime[accounting.STATE_KEY]
    assert current["hold"] == ("capacity" if prior_count == 1 else "")
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert accounting.command_outcome(current["book"], CHAT, received.root_msg_id) == "denied"
    assert f"assist:{target_id}:{action}" in current["business"]
    assert ("shortage_sha" in current["business"]) is (prior_count == 2)
    for old_target in old_targets:
        assert archive.read_business_point(persistence.get_db_conn(), IDENTITY, ACCOUNT, f"assist:{old_target}:{action}")
    assert archive.read_business_point(persistence.get_db_conn(), IDENTITY, ACCOUNT, f"assist:{target_id}:{action}") is None
    assert state_module.get_identity_state(target_id)["wanxin_observation"]["commission"]["id"] == prior_count + 10
    assert reload_state()[accounting.STATE_KEY] == current


@pytest.mark.parametrize("step", ["retire", "restore"])
@pytest.mark.parametrize("control", ["provider_module", "provider_identity", "beneficiary_module", "beneficiary_identity", "global"])
def test_disabled_automation_keeps_confirmed_retention_facts(runtime, monkeypatch, step, control):
    if step == "retire":
        filled_history(monkeypatch, count=4)
        target_id, received, _record = beneficiary_cycle(4, complete=False)
    else:
        target_id, received, _record = filled_history(monkeypatch)
        received = replace(received, server_event_at=650, text=received.text.replace("+100", "+120"))
    target = state_module.get_identity_state(target_id)
    if control == "provider_module":
        runtime["yinluo_enabled"] = False
    elif control == "provider_identity":
        state_module.set_identity_enabled(IDENTITY, False)
    elif control == "beneficiary_module":
        target["wanxin_enabled"] = False
    elif control == "beneficiary_identity":
        state_module.set_identity_enabled(target_id, False)
    else:
        state_module._meta_state["global_enabled"] = False
    flags = (runtime["yinluo_enabled"], target["wanxin_enabled"], state_module._meta_state["global_enabled"])
    profiles = {identity: copy.deepcopy(state_module.get_send_as_profile(identity)) for identity in (IDENTITY, target_id)}
    sender = AsyncMock(side_effect=AssertionError("Retaining a confirmed fact must not send a command"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    monkeypatch.setattr(wanxin, "send_game_command", sender)
    assert observe(received)
    assert not observe(received)
    sender.assert_not_called()
    assert (runtime["yinluo_enabled"], target["wanxin_enabled"], state_module._meta_state["global_enabled"]) == flags
    assert {identity: state_module.get_send_as_profile(identity) for identity in profiles} == profiles
    assert not runtime[accounting.STATE_KEY]["hold"]
    if step == "retire":
        assert target["wanxin_observation"]["commission"]["id"] == 0
        assert value()["value"] == 1880
    else:
        assert target["wanxin_observation"]["assist"]["last_contrib_gain"] == 120
        assert value()["value"] == 2000
    assert reload_state()[accounting.STATE_KEY] == runtime[accounting.STATE_KEY]


@pytest.mark.parametrize("manifest", [
    None, [], {}, {"count": False, "digest": "a" * 64}, {"count": 1.0, "digest": "a" * 64},
    {"count": -1, "digest": "a" * 64}, {"count": 2 ** 63, "digest": "a" * 64},
    {"count": 1, "digest": "A" * 64}, {"count": 1, "digest": "a" * 63},
    {"count": 1, "digest": None}, {"count": 0, "digest": "a" * 64},
    {"count": 1, "digest": "a" * 64, "extra": True},
])
def test_malformed_hot_manifest_never_becomes_an_empty_archive(runtime, manifest):
    runtime[accounting.STATE_KEY]["business_archive"] = manifest
    before = copy.deepcopy(runtime)
    current, reason = accounting.read_accounting(IDENTITY)
    assert current is None and reason == "corrupt_yinluo_accounting"
    assert not observe(panel(msg_id=50, start=130, end=140))
    assert runtime == before


@pytest.mark.parametrize("step", ["retire", "restore"])
@pytest.mark.parametrize("change", ["touched", "unrelated"])
def test_cold_write_race_during_save_rolls_back_only_this_update(runtime, monkeypatch, step, change):
    if step == "retire":
        filled_history(monkeypatch, count=5)
        _target, received, _record = beneficiary_cycle(5, complete=False)
        assert persistence.save_state()
    else:
        _target, received, _record = filled_history(monkeypatch, count=6)
        received = replace(received, server_event_at=850, text=received.text.replace("+100", "+120"))
    update, observations = staged_assist(received)
    before = copy.deepcopy(state_module._meta_state)
    save = persistence.save_state
    concurrent_rows = []
    conn = persistence.get_db_conn()

    def save_after_other_writer(**kwargs):
        other = sqlite3.connect(persistence.DB_FILE, timeout=0)
        try:
            if change == "touched":
                touched = next(item for item in update.business_changes if (item.payload is None) == (step == "restore"))
                archive.apply_changes(other, (), business_changes=(touched,))
            else:
                touched = {item.business_key for item in update.business_changes}
                key = next(row[0] for row in other.execute("SELECT business_key FROM yinluo_archive_business") if row[0] not in touched)
                other.execute("DELETE FROM yinluo_archive_business WHERE business_key=?", (key,))
            other.commit()
            concurrent_rows.extend(other.execute("SELECT * FROM yinluo_archive_business ORDER BY business_key").fetchall())
        finally:
            other.close()
        return save(**kwargs)

    monkeypatch.setattr(persistence, "save_state", save_after_other_writer)
    assert not accounting.commit_update(update, observations=observations)
    assert not conn.in_transaction
    assert state_module._meta_state == before
    assert [tuple(row) for row in conn.execute("SELECT * FROM yinluo_archive_business ORDER BY business_key")] == concurrent_rows
    assert reload_state()[accounting.STATE_KEY] == before["identity_states"][IDENTITY][accounting.STATE_KEY]


def test_business_cas_acquires_writer_lock_before_revision_check(runtime, monkeypatch):
    filled_history(monkeypatch, count=4)
    _target, received, _record = beneficiary_cycle(4, complete=False)
    update, _observations = staged_assist(received)
    conn = persistence.get_db_conn()
    before = archive.business_stats(conn, IDENTITY, ACCOUNT)
    other = sqlite3.connect(persistence.DB_FILE, timeout=0)
    try:
        archive.apply_changes(conn, (), business_changes=update.business_changes)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("DELETE FROM yinluo_archive_business")
    finally:
        conn.rollback()
        other.close()
    assert archive.business_stats(conn, IDENTITY, ACCOUNT) == before


@pytest.mark.parametrize("proof", ["command", "index"])
def test_native_proof_removed_during_save_cannot_retire_business_point(runtime, monkeypatch, proof):
    filled_history(monkeypatch, count=4)
    retire()
    _target, received, _record = beneficiary_cycle(4, complete=False)
    assert persistence.save_state()
    update, observations = staged_assist(received)
    source = update.business_changes[0].payload["points"]["start"]["evidence"]
    before = copy.deepcopy(state_module._meta_state)
    save = persistence.save_state

    def save_after_proof_deleted(**kwargs):
        other = sqlite3.connect(persistence.DB_FILE, timeout=0)
        try:
            table = "yinluo_archive_commands" if proof == "command" else "yinluo_archive_messages"
            changed = other.execute(f"DELETE FROM {table} WHERE chat_id=? AND command_msg_id=?",
                                    (source["chat_id"], source["msg_id"]))
            assert changed.rowcount == 1
            other.commit()
        finally:
            other.close()
        return save(**kwargs)

    monkeypatch.setattr(persistence, "save_state", save_after_proof_deleted)
    assert not accounting.commit_update(update, observations=observations)
    assert state_module._meta_state == before
    conn = persistence.get_db_conn()
    assert not conn.in_transaction
    assert archive.business_stats(conn, IDENTITY, ACCOUNT)["points"] == 0
    assert reload_state()[accounting.STATE_KEY] == before["identity_states"][IDENTITY][accounting.STATE_KEY]
