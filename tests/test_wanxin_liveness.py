import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from model import state as state_module
from model import persistence
from model.features import wanxin
from test_wanxin_lifecycle import OWNER, HELPER, NOW, CHAT, receipt, schedule, deliver, visit_event, prepare_accept
from test_wanxin_lifecycle import env as env
from yinluo_native_support import native_logs, native_reply


def start_visit(env, *, unknown=False):
    env.monkeypatch.setattr(wanxin, "send_game_command", AsyncMock(return_value=None if unknown else receipt()))
    if unknown:
        env.monkeypatch.setattr(wanxin, "classify_game_send_block", lambda *_args: {"status": "unknown", "code": "send_timeout"})
    asyncio.run(schedule())
    return copy.deepcopy(env.owner["wanxin_observation"]["pending"])


def advance_to_protect(env, now=NOW + 100):
    env.owner["wanxin_observation"]["next_protect_time"] = now
    env.monkeypatch.setattr(wanxin.time, "time", lambda: now)
    sender = AsyncMock(return_value=receipt(200, now))
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule(now))
    return sender


def test_unknown_visit_allows_independent_action_without_losing_original_operation(env):
    original = start_visit(env)
    sender = advance_to_protect(env)
    sender.assert_awaited_once()
    assert sender.await_args.args[0] == ".护持神魂"
    observed = env.owner["wanxin_observation"]
    assert observed["pending"]["action"] == "protect"
    assert observed["pending"]["msg_id"] == 200
    held = observed["unresolved_actions"]["visit"]
    assert held["op_id"] == original["op_id"]
    assert held["msg_id"] == original["msg_id"]
    assert held["status"] == "unknown"
    assert observed["next_visit_time"] == NOW - 1


@pytest.mark.parametrize("unknown_send", [False, True])
def test_late_result_closes_only_its_unresolved_slot(env, unknown_send):
    original = start_visit(env, unknown=unknown_send)
    sender = advance_to_protect(env)
    sender.assert_awaited_once()
    active = copy.deepcopy(env.owner["wanxin_observation"]["pending"])
    env.owner["pending_tasks"][(CHAT, 100)] = {
        "cmd": ".探望南宫婉", "family": "wanxin_visit", "account_id": OWNER,
        "op_id": original["op_id"], "chat_id": CHAT, "sent_at": NOW,
    }
    sibling = {"cmd": ".护持神魂", "chat_id": CHAT, "account_id": OWNER, "sent_at": NOW + 100}
    env.owner["pending_tasks"][(CHAT, 200)] = sibling
    assert asyncio.run(deliver(visit_event(at=NOW + 50), NOW + 101))
    observed = env.owner["wanxin_observation"]
    assert observed["pending"] == active
    assert not observed["unresolved_actions"]
    assert observed["next_visit_time"] > NOW
    assert env.owner["pending_tasks"] == {(CHAT, 200): sibling}


def test_unresolved_commission_blocks_related_chain_but_not_owner_visit(env):
    env.owner["wanxin_observation"]["auto_config"]["publish_enabled"] = True
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    assert sender.await_args.args[0] == ".发布解咒委托 1"
    env.monkeypatch.setattr(wanxin.time, "time", lambda: NOW + 100)
    sender.reset_mock(return_value=False)
    sender.return_value = receipt(200, NOW + 100)
    asyncio.run(schedule(NOW + 100))
    sender.assert_awaited_once()
    assert sender.await_args.args[0] == ".探望南宫婉"
    observed = env.owner["wanxin_observation"]
    assert observed["unresolved_actions"]["publish"]["msg_id"] == 100
    assert observed["commission"]["id"] == 0


def test_disabled_cleanup_replays_a_held_result_without_sending(env):
    start_visit(env)
    env.owner["wanxin_observation"]["auto_config"].update(protect_enabled=False, deduce_enabled=False)
    asyncio.run(schedule(NOW + 100))
    assert "visit" in env.owner["wanxin_observation"]["unresolved_actions"]
    env.owner["wanxin_enabled"] = False
    now = NOW + 1000
    rows = native_logs(visit_event())
    env.monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter((row, NOW + 2) for row in rows))
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(wanxin.run_wanxin_global_cleanup_scheduler(now))
    assert not env.owner["wanxin_enabled"]
    assert not env.owner["wanxin_observation"]["unresolved_actions"]
    assert env.owner["wanxin_observation"]["next_visit_time"] > NOW
    sender.assert_not_awaited()


def test_json_reload_and_day_rollover_cannot_rearm_an_unknown_action(env):
    original = start_visit(env)
    env.owner["wanxin_observation"]["auto_config"].update(protect_enabled=False, deduce_enabled=False)
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    for now in (NOW + 100, NOW + 2000, NOW + 90000, NOW + 180000):
        env.owner["wanxin_observation"] = json.loads(json.dumps(env.owner["wanxin_observation"]))
        env.monkeypatch.setattr(wanxin.time, "time", lambda: now)
        asyncio.run(schedule(now))
        observed = env.owner["wanxin_observation"]
        assert not observed["pending"]
        assert list(observed["unresolved_actions"]) == ["visit"]
        assert observed["unresolved_actions"]["visit"]["op_id"] == original["op_id"]
    sender.assert_not_awaited()
    with state_module.use_identity(OWNER):
        ui = wanxin.get_wanxin_ui_state(now)
        assert ui["unresolved_actions"][0]["action"] == "visit"
        assert "未确认" in wanxin.get_wanxin_status_text()


def test_incomplete_legacy_operation_is_not_given_a_current_account(env):
    env.owner["wanxin_observation"]["pending"] = {
        "action": "visit", "family": "wanxin_visit", "send_as_id": OWNER,
        "msg_id": 100, "chat_id": CHAT, "sent_at": NOW - 100, "reply_due_at": NOW - 1,
    }
    env.owner["wanxin_observation"]["next_protect_time"] = NOW - 1
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    sender.assert_not_awaited()
    observed = env.owner["wanxin_observation"]
    assert observed["pending"]["msg_id"] == 100
    assert not observed["pending"].get("account_id")
    assert not observed.get("unresolved_actions")


def test_recovery_reads_one_batch_for_multiple_held_actions(env):
    start_visit(env)
    advance_to_protect(env)
    env.owner["wanxin_observation"]["auto_config"].update(deduce_enabled=False)
    asyncio.run(schedule(NOW + 200))
    assert set(env.owner["wanxin_observation"]["unresolved_actions"]) == {"visit", "protect"}
    calls = []
    rows = native_logs(
        visit_event(), native_reply(OWNER, ".护持神魂", "护持神魂成功，魂封 -5。", NOW + 101, root=200, command_at=NOW + 100),
    )

    def read(start, end):
        calls.append((start, end))
        return iter((row, row["server_event_at"]) for row in rows)

    env.monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", read)
    with state_module.use_identity(OWNER):
        assert asyncio.run(wanxin._cleanup_wanxin_pending_only(NOW + 1000))
    assert len(calls) == 1
    assert not env.owner["wanxin_observation"]["unresolved_actions"]


def test_helper_rebind_does_not_adopt_an_old_held_result(env):
    env.owner["wanxin_observation"]["next_visit_time"] = NOW + 3600
    env.owner["wanxin_observation"]["commission"].update(id=5, published_at=NOW - 100, owner_username="owner")
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    assert sender.await_args.args[0] == ".接取解咒委托 5"
    asyncio.run(schedule(NOW + 100))
    state_module.set_identity_account(HELPER, OWNER)
    before = copy.deepcopy(env.owner)
    result = native_reply(HELPER, ".接取解咒委托 5", "【咒契协定已成】\n阴罗宗弟子 @helper 已接取 @owner 的解咒委托。", NOW + 1, root=100, command_at=NOW)
    assert not asyncio.run(deliver(result, NOW + 101))
    assert env.owner == before


def test_late_identification_uses_original_pending_actor_after_helper_selection_changes(env):
    prepare_accept(env)
    env.owner["wanxin_observation"]["commission"].update(accepted=True, accepted_at=NOW - 80, helper_username="helper")
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    assert sender.await_args.args[0] == ".辨认咒纹 @owner"
    advance_to_protect(env)
    active = copy.deepcopy(env.owner["wanxin_observation"]["pending"])
    assert "identify" in env.owner["wanxin_observation"]["unresolved_actions"]
    state_module.ensure_identity_registered(HELPER + 1)
    state_module.set_identity_account(HELPER + 1, HELPER + 1)
    state_module.update_send_as_profile(HELPER + 1, username="new_helper", sect_name="阴罗宗", enabled=True)
    env.owner["wanxin_observation"]["assist"]["send_as_id"] = HELPER + 1
    reply = native_reply(HELPER, ".辨认咒纹 @owner", "【阴罗辨咒】\n@helper 替 @owner 锁定咒源。咒源 +20，咒师贡献 +120。",
                         NOW + 50, root=100, command_at=NOW)
    assert asyncio.run(deliver(reply, NOW + 101))
    observed = env.owner["wanxin_observation"]
    assert observed["pending"] == active
    assert not observed["unresolved_actions"]
    assert observed["assist"]["send_as_id"] == HELPER + 1


def test_native_acceptance_survives_helper_rename_without_reaccepting(env):
    prepare_accept(env)
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    reply = native_reply(HELPER, ".接取解咒委托 5", "【咒契协定已成】\n阴罗宗弟子 @helper 已接取 @owner 的解咒委托。",
                         NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(reply))
    state_module.update_send_as_profile(HELPER, username="renamed_helper")
    assert wanxin._commission_accept_evidence_valid(env.owner["wanxin_observation"])
    sender.reset_mock()
    sender.return_value = receipt(200, NOW + 30)
    env.monkeypatch.setattr(wanxin.time, "time", lambda: NOW + 30)
    asyncio.run(schedule(NOW + 30))
    sender.assert_awaited_once()
    assert sender.await_args.args[0] == ".辨认咒纹 @owner"
    assert env.owner["wanxin_observation"]["commission"]["accepted"]


def test_transport_still_in_flight_cannot_be_isolated_or_reentered(env):
    calls = []

    async def send(command, **_kwargs):
        calls.append(command)
        env.owner["wanxin_observation"]["next_protect_time"] = NOW - 1
        await schedule(NOW + 200)
        assert env.owner["wanxin_observation"]["pending"]["status"] == "sending"
        assert not env.owner["wanxin_observation"]["unresolved_actions"]
        return receipt()

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    asyncio.run(schedule())
    assert calls == [".探望南宫婉"]


def test_late_duplicate_only_removes_its_restored_held_slot(env):
    start_visit(env)
    advance_to_protect(env)
    held = copy.deepcopy(env.owner["wanxin_observation"]["unresolved_actions"]["visit"])
    event = visit_event(at=NOW + 50)
    assert asyncio.run(deliver(event, NOW + 101))
    current = copy.deepcopy(env.owner["wanxin_observation"])
    env.owner["wanxin_observation"]["unresolved_actions"]["visit"] = held
    assert asyncio.run(deliver(event, NOW + 102))
    assert env.owner["wanxin_observation"] == current


@pytest.mark.parametrize("action", ["moon_seal", "moon_join"])
def test_unknown_affinity_spending_blocks_both_spending_actions(env, action):
    env.owner["concubine_affinity"] = 250
    observed = env.owner["wanxin_observation"]
    observed["moon_awakened"] = True
    observed["auto_config"].update(
        visit_enabled=False, protect_enabled=False, deduce_enabled=False,
        moon_seal_enabled=True, moon_join_enabled=True,
    )
    other = "moon_join" if action == "moon_seal" else "moon_seal"
    observed[f"next_{other}_time"] = NOW + 10
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    assert env.owner["wanxin_observation"]["pending"]["action"] == action
    asyncio.run(schedule(NOW + 100))
    asyncio.run(schedule(NOW + 100000))
    sender.assert_awaited_once()
    assert action in env.owner["wanxin_observation"]["unresolved_actions"]
    assert env.owner["concubine_affinity"] == 250


@pytest.mark.parametrize("field,value", [
    ("pending", []), ("pending", 0), ("pending", "broken"),
    ("unresolved_actions", []), ("unresolved_actions", {"visit": None}),
    ("unresolved_actions", {"unexpected": {"action": "visit"}}),
])
def test_malformed_operations_preserve_raw_evidence_and_reject_sends_and_replies(env, field, value):
    observed = env.owner["wanxin_observation"]
    observed[field] = copy.deepcopy(value)
    normalized = wanxin.normalize_wanxin_observation(observed)
    assert normalized["unresolved_invalid"]
    original_field = "invalid_pending" if field == "pending" else field
    assert normalized[original_field] == value
    assert wanxin.normalize_wanxin_observation(json.loads(json.dumps(normalized)))[original_field] == value
    before = copy.deepcopy(env.owner)
    assert not asyncio.run(deliver(visit_event()))
    assert env.owner == before
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    sender.assert_not_awaited()
    with state_module.use_identity(OWNER):
        assert wanxin.get_wanxin_ui_state()["unresolved_invalid"]
        assert "未确认" in wanxin.get_wanxin_status_text()


def test_duplicate_active_and_held_ownership_is_not_silently_deduplicated(env):
    original = start_visit(env)
    env.owner["wanxin_observation"]["unresolved_actions"]["visit"] = original
    before = copy.deepcopy(env.owner)
    assert not asyncio.run(deliver(visit_event()))
    assert env.owner == before
    normalized = wanxin.normalize_wanxin_observation(before["wanxin_observation"])
    assert normalized["unresolved_invalid"]
    assert normalized["pending"] == normalized["unresolved_actions"]["visit"]


@pytest.mark.parametrize("slot", ["pending", "held"])
@pytest.mark.parametrize("field", ["status", "action"])
@pytest.mark.parametrize("value", [[], ["sent"], {}, {"broken": True}])
def test_corrupt_pending_fields_hold_without_crashing_or_losing_evidence(env, slot, field, value):
    start_visit(env)
    if slot == "held":
        advance_to_protect(env)
    observed = env.owner["wanxin_observation"]
    pending = observed["pending"] if slot == "pending" else observed["unresolved_actions"]["visit"]
    pending[field] = copy.deepcopy(value)
    raw_pending = copy.deepcopy(pending)
    normalized = wanxin.normalize_wanxin_observation(observed)
    assert normalized["unresolved_invalid"]
    normalized = wanxin.normalize_wanxin_observation(json.loads(json.dumps(normalized)))
    retained = normalized["invalid_pending"] if slot == "pending" else normalized["unresolved_actions"]["visit"]
    assert retained == raw_pending
    before = copy.deepcopy(env.owner)
    assert not asyncio.run(deliver(visit_event()))
    assert env.owner == before
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule(NOW + 1000))
    sender.assert_not_awaited()
    with state_module.use_identity(OWNER):
        assert wanxin.get_wanxin_ui_state()["unresolved_invalid"]


@pytest.mark.parametrize("same_chat", [True, False])
def test_message_root_collision_is_independent_of_operation_uuid(env, same_chat):
    start_visit(env)
    advance_to_protect(env)
    observed = env.owner["wanxin_observation"]
    active = observed["pending"]
    held = observed["unresolved_actions"]["visit"]
    assert active["op_id"] != held["op_id"]
    active["msg_id"] = held["msg_id"]
    active["chat_id"] = held["chat_id"] if same_chat else CHAT - 1
    before = copy.deepcopy(observed)
    normalized = wanxin.normalize_wanxin_observation(observed)
    assert normalized.get("unresolved_invalid", False) is same_chat
    assert normalized["pending"] == before["pending"]
    assert normalized["unresolved_actions"] == before["unresolved_actions"]
    if same_chat:
        assert not asyncio.run(deliver(visit_event()))
        assert env.owner["wanxin_observation"] == before


def test_full_action_slots_never_evict_unknown_work_to_admit_a_new_send(env):
    base = start_visit(env)
    observed = env.owner["wanxin_observation"]
    observed["pending"] = {}
    for index, action in enumerate(wanxin.WANXIN_ACTION_FAMILIES):
        command = wanxin.WANXIN_ACTION_COMMANDS[action]
        if action in {"publish", "accept"}:
            command += " 1"
        elif action in wanxin.WANXIN_ASSIST_ACTIONS:
            command += " @owner"
        op_id = f"{index + 1:032x}"
        actor_id = HELPER if action in set(wanxin.WANXIN_ASSIST_ACTIONS) | {"accept"} else OWNER
        observed["unresolved_actions"][action] = dict(
            base, action=action, family=wanxin.WANXIN_ACTION_FAMILIES[action], command=command,
            send_as_id=actor_id, account_id=actor_id, op_id=op_id, msg_id=100 + index,
            resource_op_id=op_id if action in wanxin.WANXIN_RESOURCE_ACTIONS else "",
        )
    before = copy.deepcopy(observed["unresolved_actions"])
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule(NOW + 90000))
    sender.assert_not_awaited()
    assert env.owner["wanxin_observation"]["unresolved_actions"] == before


@pytest.fixture
def persistent_env(env, monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "wanxin.db"))
    for name, value in {
        "_db_conn": None, "_db_initialized": False,
        "_schema_columns_ensured_key": None, "_schema_columns_ensured_version": None,
        "_persistence_snapshot_db_key": "", "_persisted_meta_snapshot": {}, "_persisted_identity_snapshots": {},
        "_state_dirty": False, "_last_flush_time": 0, "_last_save_failed_at": 0.0, "_last_save_error": "",
    }.items():
        monkeypatch.setattr(persistence, name, value)
    monkeypatch.setattr(persistence, "_write_live_guard_backup", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(wanxin, "save_state", persistence.save_state)
    try:
        yield env
    finally:
        if persistence._db_conn is not None:
            persistence._db_conn.close()


def test_sqlite_reload_retains_held_operation_and_accepts_exact_late_result(persistent_env):
    env = persistent_env
    start_visit(env)
    advance_to_protect(env)
    before = copy.deepcopy(env.owner["wanxin_observation"])
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.owner = state_module.get_identity_state(OWNER)
    assert env.owner["wanxin_observation"] == before
    assert asyncio.run(deliver(visit_event(at=NOW + 50), NOW + 101))
    assert not env.owner["wanxin_observation"]["unresolved_actions"]
    assert env.owner["wanxin_observation"]["pending"] == before["pending"]
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert state_module.get_identity_state(OWNER)["wanxin_observation"]["pending"] == before["pending"]


def test_reply_transaction_rolls_back_pending_affinity_and_evidence(persistent_env):
    env = persistent_env
    env.owner["concubine_affinity"] = 200
    observed = env.owner["wanxin_observation"]
    observed["auto_config"].update(visit_enabled=False, moon_greet_enabled=True)
    observed["moon_awakened"] = True
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    env.owner["pending_tasks"][(CHAT, 100)] = {
        "cmd": ".婉影问安", "chat_id": CHAT, "sent_at": NOW, "account_id": OWNER,
    }
    assert persistence.save_state()
    before = copy.deepcopy(env.owner)
    conn = persistence.get_db_conn()
    conn.execute(f"CREATE TEMP TRIGGER fail_wanxin BEFORE INSERT ON identity_runtime_state "
                 f"WHEN NEW.send_as_id = {OWNER} BEGIN SELECT RAISE(ABORT, 'wanxin failure'); END")
    event = native_reply(OWNER, ".婉影问安", "【婉影问安】\n婉心 +2，情缘 +3。", NOW + 1, root=100, command_at=NOW)
    try:
        assert not asyncio.run(deliver(event))
        assert env.owner == before
    finally:
        conn.execute("DROP TRIGGER fail_wanxin")
    assert asyncio.run(deliver(event))
    assert env.owner["concubine_affinity"] == 203
    assert not env.owner["pending_tasks"]
    assert not env.owner["wanxin_observation"]["pending"]
