import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, cultivation_accounting as cultivation, persistence
from model import state as state_module
from model.features import duel
from model.verified_event import VerifiedGameEvent
from test_cultivation_accounting import ACCOUNT, IDENTITY, env as env, snapshot, value
from test_resource_accounting import CHAT


WINNER = IDENTITY + 1
BOT = 880650001


@pytest.fixture(autouse=True)
def setup(env, monkeypatch):
    state_module.set_game_group_route_config({"primary_group_id": CHAT, "backup_group_ids": [CHAT - 1], "enabled": True})
    state_module.set_game_bot_ids([BOT])
    state_module.update_send_as_profile(IDENTITY, username="defender", realm="\u5143\u5a74\u540e\u671f")
    state_module.set_identity_account(WINNER, ACCOUNT + 1)
    state_module.update_send_as_profile(WINNER, username="attacker", realm="\u5143\u5a74\u540e\u671f")
    assert snapshot()
    assert snapshot(identity=WINNER)
    monkeypatch.setattr(duel, "save_state", Mock(return_value=True))


def battle(*, at=120, msg_id=1000, chat=CHAT, root=None, start=110, amount=60000, edited=False):
    root = msg_id - 1 if root is None else root
    text = (
        "\u3010\u5929\u9053\u6218\u62a5\u00b7\u6587\u5b57\u7248\u3011\n"
        "\u653b\u65b9\uff1a@attacker\n\u5b88\u65b9\uff1a@defender\n"
        f"\u80dc\u8005\uff1a@attacker | \u51c0\u5f97\u4fee\u4e3a +{amount}\n"
        f"\u8d25\u8005\uff1a@defender | \u635f\u5931\u4fee\u4e3a -{amount}"
    )
    return VerifiedGameEvent(
        event_type="edit" if edited else "message", chat_id=chat, msg_id=msg_id, sender_id=BOT, text=text,
        reply_context={
            "send_as_id": WINNER, "family": "duel", "chat_id": chat,
            "reply_to_msg_id": root, "root_msg_id": root, "reply_to_command": ".\u6597\u6cd5 @defender",
            "reply_to_server_at": start, "reply_to_sender_id": WINNER,
        },
        identity_id=WINNER, family="duel", root_msg_id=root, route_source="native",
        reply_to_sender_id=WINNER, server_event_at=float(at),
    )


def test_cross_chat_losses_and_wins_are_counted_without_scalar_id_ordering(env):
    assert duel.observe_duel_cultivation(battle(), now=150)
    assert duel.observe_duel_cultivation(battle(at=140, start=130, msg_id=100, chat=CHAT - 1), now=150)
    assert value() == 380000
    assert state_module.get_send_as_profile(WINNER)["xiuwei_current"] == 620000
    assert not duel.observe_duel_cultivation(battle(), now=160)
    assert value() == 380000


def test_manual_battle_and_disabled_modules_still_reconcile_without_open_pending(env):
    state_module.set_global_enabled(False)
    env["duel_enabled"] = False
    assert duel.observe_duel_cultivation(battle(), now=150)
    assert value() == 440000
    assert not env["pending_tasks"]
    assert not env["duel_enabled"]


def test_native_edit_changes_one_operation_and_late_original_does_not_revert_it(env):
    assert duel.observe_duel_cultivation(battle(), now=150)
    assert duel.observe_duel_cultivation(battle(at=130, amount=50000, edited=True), now=150)
    assert value() == 450000
    assert not duel.observe_duel_cultivation(battle(), now=160)
    assert not duel.observe_duel_cultivation(battle(at=130, amount=50000, edited=True), now=160)
    assert value() == 450000


def test_zero_amount_edit_does_not_leave_old_winner_credit_spendable(env):
    assert duel.observe_duel_cultivation(battle(), now=150)
    assert duel.observe_duel_cultivation(battle(at=130, amount=0, edited=True), now=150)
    assert value() == 500000
    assert cultivation.cultivation_balance(WINNER)["value"] == 500000


def test_unparsed_new_revision_blocks_old_credit_until_resolved(env):
    assert duel.observe_duel_cultivation(battle(), now=150)
    unknown = battle(at=130, edited=True)
    unknown = replace(unknown, text=unknown.text.replace("+60000", "+unknown"))
    assert duel.observe_duel_cultivation(unknown, now=150)
    assert cultivation.cultivation_balance(WINNER)["status"] == "conflicting_revision"
    assert not duel.observe_duel_cultivation(battle(), now=150)
    assert cultivation.cultivation_balance(WINNER)["value"] is None
    assert duel.observe_duel_cultivation(battle(at=140, amount=50000, edited=True), now=150)
    assert cultivation.cultivation_balance(WINNER)["value"] == 550000


@pytest.mark.parametrize("change", ["not_report", "winner_missing", "loser_missing", "winner_changed"])
def test_edit_with_missing_or_changed_participant_cannot_leave_old_credit_spendable(env, change):
    assert duel.observe_duel_cultivation(battle(), now=150)
    edited = battle(at=130, edited=True)
    if change == "not_report":
        text = "result withdrawn"
    elif change == "winner_missing":
        text = "\n".join(line for line in edited.text.splitlines() if not line.startswith("\u80dc\u8005"))
    elif change == "loser_missing":
        text = "\n".join(line for line in edited.text.splitlines() if not line.startswith("\u8d25\u8005"))
    else:
        text = edited.text.replace("@attacker", "@outsider")
    assert duel.observe_duel_cultivation(replace(edited, text=text), now=150)
    assert cultivation.cultivation_balance(WINNER)["value"] is None
    duel.observe_duel_cultivation(battle(), now=150)
    assert cultivation.cultivation_balance(WINNER)["value"] is None
    assert snapshot(500000, 140, 2000, identity=WINNER)
    assert cultivation.cultivation_balance(WINNER)["value"] == 500000


def test_later_command_evidence_enriches_the_same_result_instead_of_creating_a_second_charge(env):
    unanchored = replace(battle(), reply_context={}, root_msg_id=0, identity_id=0)
    assert duel.observe_duel_cultivation(unanchored, now=150)
    assert duel.observe_duel_cultivation(battle(), now=150)
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 440000
    assert cultivation.cultivation_balance(WINNER)["value"] == 560000
    assert len(env[cultivation.STATE_KEY]["ledger"]["entries"]) == 1
    assert not duel.observe_duel_cultivation(unanchored, now=150)


def test_edit_without_repeated_command_context_revises_the_owned_result(env):
    assert duel.observe_duel_cultivation(battle(), now=150)
    edited = replace(battle(at=130, amount=50000, edited=True), reply_context={}, root_msg_id=0, identity_id=0)
    assert duel.observe_duel_cultivation(edited, now=150)
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 450000
    assert cultivation.cultivation_balance(WINNER)["value"] == 550000
    assert len(env[cultivation.STATE_KEY]["ledger"]["entries"]) == 1


def test_old_unparsed_result_cannot_conflict_a_later_valid_revision(env):
    assert duel.observe_duel_cultivation(battle(at=140, amount=50000, edited=True), now=150)
    unknown = replace(battle(), text=battle().text.replace("+60000", "+unknown"))
    assert not duel.observe_duel_cultivation(unknown, now=150)
    assert cultivation.cultivation_balance(WINNER)["value"] == 550000


@pytest.mark.parametrize("amount", ["6,000", "6e4", "60000unknown", "6\u4e07\u4e07"])
def test_unrecognized_amount_token_does_not_accept_its_numeric_prefix(env, amount):
    event = battle()
    event = replace(event, text=event.text.replace("60000", amount))
    assert duel.observe_duel_cultivation(event, now=150)
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    assert cultivation.cultivation_balance(WINNER)["value"] is None


def test_partial_first_seen_report_does_not_leave_known_participants_spendable(env):
    event = battle()
    text = "\n".join(line for line in event.text.splitlines() if not line.startswith("\u80dc\u8005"))
    assert duel.observe_duel_cultivation(replace(event, text=text), now=150)
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    assert cultivation.cultivation_balance(WINNER)["value"] is None


def test_winner_and_loser_aliasing_the_same_identity_is_not_a_positive_reward(env):
    state_module.update_send_as_profile(IDENTITY, username_aliases=["defender_alias"])
    event = battle()
    text = event.text.replace("\u80dc\u8005\uff1a@attacker", "\u80dc\u8005\uff1a@defender_alias")
    assert duel.observe_duel_cultivation(replace(event, text=text), now=150)
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    assert cultivation.cultivation_balance(WINNER)["value"] is None


@pytest.mark.parametrize("text, expected", [("-2.4\u4e07", 24000), ("-2\u4ebf", 200000000), ("-1\u5146", 1000000000000)])
def test_resource_units_are_not_silently_truncated(text, expected):
    match = duel.RE_DUEL_XIUWEI_LOSS.search("\u635f\u5931\u4fee\u4e3a " + text)
    assert duel._duel_resource_amount(match) == expected


def test_integer_resource_amount_is_not_rounded_through_binary_float(env):
    match = duel.RE_DUEL_XIUWEI_GAIN.search("\u51c0\u5f97\u4fee\u4e3a +9007199254740993")
    assert duel._duel_resource_amount(match) == 9007199254740993


def test_result_withdrawal_and_source_ownership_survive_actual_sqlite_reload(env):
    assert duel.observe_duel_cultivation(battle(), now=150)
    assert duel.observe_duel_cultivation(replace(battle(at=130, edited=True), text=""), now=150)
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert cultivation.cultivation_balance(WINNER)["value"] is None
    assert not duel.observe_duel_cultivation(battle(), now=150)
    assert cultivation.cultivation_balance(WINNER)["value"] is None
    assert duel.observe_duel_cultivation(battle(at=140, amount=0, edited=True), now=150)
    assert cultivation.cultivation_balance(WINNER)["value"] == 500000


def test_recent_profile_is_not_double_debited_by_old_official_report(env):
    assert snapshot(200000, 150, 2000)
    assert duel.observe_duel_cultivation(battle(), now=170)
    assert value() == 200000
    assert snapshot(900000, 90, 5) == {}
    assert value() == 200000


@pytest.mark.parametrize("changes", [
    {"sender_id": 99}, {"chat_id": CHAT - 2}, {"msg_id": 0}, {"msg_id": True},
    {"server_event_at": 0}, {"server_event_at": float("nan")}, {"server_event_at": 999},
])
def test_untrusted_or_malformed_result_cannot_write_resources(env, changes):
    before = copy.deepcopy(env[cultivation.STATE_KEY])
    assert not duel.observe_duel_cultivation(replace(battle(), **changes), now=150)
    assert value() == 500000
    assert env[cultivation.STATE_KEY] == before


def test_unanchored_official_result_is_retained_but_does_not_authorize_new_spending(env):
    event = replace(battle(), reply_context={}, root_msg_id=0, identity_id=0)
    assert duel.observe_duel_cultivation(event, now=150)
    assert value() == 500000
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "overlapping_observation"
    with state_module.use_identity(IDENTITY):
        assert "overlapping_observation" in duel._profile_gate_reason()
    assert snapshot(440000, 130, 2000)
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 440000


def test_edited_parent_command_is_not_misread_as_original_command_evidence(env):
    event = battle()
    event = replace(event, reply_context={**event.reply_context, "reply_to_command_edited": True})
    assert duel.observe_duel_cultivation(event, now=150)
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "overlapping_observation"


def test_native_reply_context_marks_edited_parent_command(env, monkeypatch):
    command = SimpleNamespace(
        raw_text=".\u6597\u6cd5 @defender", id=999, chat_id=CHAT, sender_id=WINNER,
        date=datetime.fromtimestamp(110, timezone.utc), edit_date=datetime.fromtimestamp(115, timezone.utc),
    )
    event = SimpleNamespace(chat_id=CHAT, get_reply_message=AsyncMock(return_value=command))
    monkeypatch.setattr(app, "get_reply_context", lambda *_args, **_kwargs: {})
    _reply, context = asyncio.run(app._resolve_event_reply(event))
    assert context["reply_to_command_edited"] is True


def test_username_aliases_require_unique_identity_before_debit(env):
    state_module.update_send_as_profile(IDENTITY, username="defender_new", username_aliases=["defender"])
    assert duel.observe_duel_cultivation(battle(), now=150)
    assert value() == 440000
    state_module.set_identity_account(IDENTITY + 2, ACCOUNT + 2)
    state_module.update_send_as_profile(IDENTITY + 2, username="defender")
    previous_entry = copy.deepcopy(env[cultivation.STATE_KEY]["ledger"]["entries"][0])
    duel.observe_duel_cultivation(battle(at=140, start=130, msg_id=2000), now=150)
    assert value() == 440000
    assert env[cultivation.STATE_KEY]["ledger"]["entries"][0] == previous_entry
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None


def test_unknown_balance_does_not_block_recovery_of_already_sent_duel(env, monkeypatch):
    assert snapshot(400000, 130, evidence={"source": "api", "requested_at": 130.1})
    env.update(duel_enabled=True, duel_target="@attacker", duel_total_count=3,
               duel_reply_to_msg_id=100, duel_reply_due_at=120, duel_started_at=110)
    monkeypatch.setattr(duel, "reconcile_duel_from_message_log", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(duel, "_controlled_loadout_config", lambda *_args: None)
    recover = AsyncMock(return_value=True)
    monkeypatch.setattr(duel, "_recover_duel_pending_from_message_log", recover)
    monkeypatch.setattr(duel, "send_audit_log", AsyncMock())
    send = AsyncMock(side_effect=AssertionError("no new duel"))
    monkeypatch.setattr(duel, "send_game_command", send)
    with state_module.use_identity(IDENTITY):
        asyncio.run(duel.run_duel_scheduler(150))
    recover.assert_awaited_once_with(150, 100)
    send.assert_not_awaited()


def test_manual_loss_during_preparation_rechecks_both_balances_before_send(env, monkeypatch):
    env.update(duel_enabled=True, duel_target="@attacker", duel_total_count=3, next_duel_time=0,
               duel_unequip_prepared=True, duel_last_result="\u6597\u6cd5\u914d\u88c5:battle_ready")
    monkeypatch.setattr(duel, "reconcile_duel_from_message_log", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(duel, "_controlled_loadout_config", lambda *_args: None)
    monkeypatch.setattr(duel, "_prepare_managed_target_loadout", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(duel, "is_within_duel_exec_window", lambda *_args: True)
    monkeypatch.setattr(duel.time, "time", lambda: 150.0)

    async def prepare(*_args, **_kwargs):
        assert snapshot(10000, 140, identity=WINNER)
        return True

    monkeypatch.setattr(duel, "_prepare_duel_tianxing_route", prepare)
    send = AsyncMock(side_effect=AssertionError("balance dropped during await"))
    monkeypatch.setattr(duel, "send_game_command", send)
    with state_module.use_identity(IDENTITY):
        asyncio.run(duel.run_duel_scheduler(150))
    send.assert_not_awaited()


def log_entries(event):
    context = event.reply_context
    return [
        {"event_type": "message", "chat_id": event.chat_id, "message_id": event.root_msg_id,
         "sender_id": WINNER, "text": context["reply_to_command"], "server_event_at": context["reply_to_server_at"]},
        {"event_type": event.event_type, "chat_id": event.chat_id, "message_id": event.msg_id,
         "reply_to_msg_id": event.root_msg_id, "sender_id": BOT, "text": event.text,
         "server_event_at": event.server_event_at, "ts_epoch": 999999},
    ]


def test_shared_log_replay_keeps_chat_ownership_and_native_revision_times(env):
    first = log_entries(battle())
    second = log_entries(battle(at=140, start=130, msg_id=100, chat=CHAT - 1))
    assert duel.reconcile_duel_cultivation_entries([*second, *first], now=150)
    assert value() == 380000
    assert not duel.reconcile_duel_cultivation_entries([*first, *second], now=160)
    assert not duel.observe_duel_cultivation(battle(), now=160)
    assert value() == 380000


@pytest.mark.parametrize("text", ["result withdrawn", ""])
def test_daily_log_selection_keeps_nonreport_edits_for_accounting(env, monkeypatch, text):
    original = log_entries(battle())
    edit = log_entries(replace(battle(at=130, edited=True), text=text))[-1]
    rows = [*original, edit]
    monkeypatch.setattr(duel, "_DUEL_DAY_LOG_CACHE", {})
    monkeypatch.setattr(duel, "iter_message_log_entries_between", lambda *_args: ((row, 140) for row in rows))
    selected = duel._duel_day_log_entries(150)
    assert duel.reconcile_duel_cultivation_entries(selected, now=150)
    assert cultivation.cultivation_balance(WINNER)["value"] is None


def test_replay_follows_only_official_same_chat_reply_chain_to_original_command(env):
    event = battle(msg_id=1002, root=999)
    rows = log_entries(event)
    rows[-1]["reply_to_msg_id"] = 1001
    rows.insert(1, {
        "event_type": "message", "chat_id": CHAT, "message_id": 1001,
        "reply_to_msg_id": 999, "sender_id": BOT, "text": "battle started", "server_event_at": 115,
    })
    assert duel.reconcile_duel_cultivation_entries(rows, now=150)
    assert cultivation.cultivation_balance(WINNER)["value"] == 560000
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 440000


@pytest.mark.parametrize("fault", ["player", "chat", "future", "no_time", "edited", "cycle", "conflict", "sent_only"])
def test_replay_does_not_invent_original_command_evidence_from_bad_chain_nodes(env, fault):
    rows = log_entries(battle(msg_id=1002, root=999))
    rows[-1]["reply_to_msg_id"] = 1001
    parent = {
        "event_type": "message", "chat_id": CHAT, "message_id": 1001,
        "reply_to_msg_id": 999, "sender_id": BOT, "text": "battle started", "server_event_at": 115,
    }
    if fault == "player":
        parent["sender_id"] = WINNER
    elif fault == "chat":
        parent["chat_id"] = CHAT - 1
    elif fault == "future":
        parent["server_event_at"] = 121
    elif fault == "no_time":
        parent["server_event_at"] = 0
    elif fault == "edited":
        parent["event_type"] = "edit"
    elif fault == "cycle":
        parent["reply_to_msg_id"] = 1002
    elif fault == "conflict":
        rows.insert(1, {**parent, "sender_id": WINNER})
    elif fault == "sent_only":
        rows[0]["event_type"] = "sent"
    rows.insert(1, parent)
    assert duel.reconcile_duel_cultivation_entries(rows, now=150)
    assert cultivation.cultivation_balance(WINNER)["status"] == "overlapping_observation"
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None


def test_daily_log_selection_retains_only_metadata_for_intermediate_reply_nodes(env, monkeypatch):
    rows = log_entries(battle(msg_id=1002, root=999))
    rows[-1]["reply_to_msg_id"] = 1001
    rows.insert(1, {
        "event_type": "message", "chat_id": CHAT, "message_id": 1001,
        "reply_to_msg_id": 999, "sender_id": BOT, "text": "battle started", "server_event_at": 115,
    })
    monkeypatch.setattr(duel, "_DUEL_DAY_LOG_CACHE", {})
    monkeypatch.setattr(duel, "iter_message_log_entries_between", lambda *_args: ((row, 140) for row in rows))
    selected = duel._duel_day_log_entries(150)
    assert selected[1]["resource_context_only"]
    assert selected[1]["text"] == ""
    assert duel.reconcile_duel_cultivation_entries(selected, now=150)
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 440000


def test_replay_rejects_missing_server_time_instead_of_using_receipt_time(env):
    entries = log_entries(battle())
    entries[-1]["server_event_at"] = 0
    before = copy.deepcopy(env[cultivation.STATE_KEY])
    assert not duel.reconcile_duel_cultivation_entries(entries, now=1000000)
    assert env[cultivation.STATE_KEY] == before


def test_queued_resource_check_stays_bound_to_current_target_balance(env, monkeypatch):
    env.update(duel_enabled=True, duel_target="@attacker", duel_total_count=3, next_duel_time=0,
               duel_unequip_prepared=True, duel_last_result="\u6597\u6cd5\u914d\u88c5:battle_ready")
    monkeypatch.setattr(duel, "reconcile_duel_from_message_log", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(duel, "_controlled_loadout_config", lambda *_args: None)
    monkeypatch.setattr(duel, "_prepare_managed_target_loadout", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(duel, "_prepare_duel_tianxing_route", AsyncMock(return_value=True))
    monkeypatch.setattr(duel, "is_within_duel_exec_window", lambda *_args: True)
    monkeypatch.setattr(duel.time, "time", lambda: 150.0)
    monkeypatch.setattr(duel, "classify_game_send_block", lambda *_args: {"status": "unsent", "code": "operation_invalid"})

    async def send(_command, **kwargs):
        check = kwargs["operation_check"]
        assert check()
        assert snapshot(10000, 140, identity=WINNER)
        assert not check()
        return None

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(duel, "send_game_command", send_mock)
    with state_module.use_identity(IDENTITY):
        asyncio.run(duel.run_duel_scheduler(150))
    send_mock.assert_awaited_once()
    assert not env["duel_reply_to_msg_id"]


@pytest.mark.parametrize("change", ["replace", "account", "rename"])
def test_queued_resource_check_rejects_changed_target_ownership(env, monkeypatch, change):
    env.update(duel_enabled=True, duel_target="@attacker", duel_total_count=3, next_duel_time=0,
               duel_unequip_prepared=True, duel_last_result="\u6597\u6cd5\u914d\u88c5:battle_ready")
    monkeypatch.setattr(duel, "reconcile_duel_from_message_log", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(duel, "_controlled_loadout_config", lambda *_args: None)
    monkeypatch.setattr(duel, "_prepare_managed_target_loadout", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(duel, "_prepare_duel_tianxing_route", AsyncMock(return_value=True))
    monkeypatch.setattr(duel, "is_within_duel_exec_window", lambda *_args: True)
    monkeypatch.setattr(duel.time, "time", lambda: 150.0)
    monkeypatch.setattr(duel, "classify_game_send_block", lambda *_args: {"status": "unsent", "code": "operation_invalid"})

    async def send(_command, **kwargs):
        check = kwargs["operation_check"]
        assert check()
        if change == "replace":
            state_module._meta_state["identity_states"][WINNER] = copy.deepcopy(state_module.get_identity_state(WINNER))
        elif change == "account":
            state_module.set_identity_account(WINNER, ACCOUNT + 10)
            state_module.get_identity_state(WINNER)[cultivation.STATE_KEY] = {}
            assert snapshot(at=140, identity=WINNER)
        else:
            state_module.update_send_as_profile(WINNER, username="renamed", username_aliases=[])
        assert cultivation.cultivation_balance(WINNER)["status"] == "ready"
        assert not check()
        return None

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(duel, "send_game_command", send_mock)
    with state_module.use_identity(IDENTITY):
        asyncio.run(duel.run_duel_scheduler(150))
    send_mock.assert_awaited_once()
    assert not env["duel_reply_to_msg_id"]


def test_ambiguous_managed_target_does_not_skip_defender_resource_gate(env):
    other = WINNER + 1
    state_module.set_identity_account(other, ACCOUNT + 2)
    state_module.update_send_as_profile(other, username="attacker")
    assert snapshot(identity=other)
    with state_module.use_identity(IDENTITY):
        assert duel._target_gate_reason("@attacker")


@pytest.mark.parametrize("edited", [False, True])
def test_native_app_records_resource_fact_before_later_handler_cancellation(env, monkeypatch, edited):
    event = battle(edited=edited)
    raw = SimpleNamespace(
        id=event.msg_id, sender_id=BOT, chat_id=CHAT, raw_text=event.text,
        date=datetime.fromtimestamp(110, timezone.utc), edit_date=datetime.fromtimestamp(120, timezone.utc),
    )
    if not edited:
        raw.date = raw.edit_date
    for name in (
        "_append_replica_group_message_log", "_append_replica_dispatch_group_message_log",
        "_append_game_group_message_log", "_bind_command_attempt_shadow", "_record_message_box_shadow",
        "record_game_group_message", "observe_dungeon_quiet_text", "_refresh_identity_username_from_event",
    ):
        monkeypatch.setattr(app, name, Mock(return_value=False))
    for name in ("observe_red_packet_candidate", "capture_cave_public_entry_event", "handle_log_group_command"):
        monkeypatch.setattr(app, name, AsyncMock(return_value=False))
    monkeypatch.setattr(app, "_is_game_group_listener_event", lambda *_args: True)
    monkeypatch.setattr(app, "_is_game_bot_event", AsyncMock(return_value=True))
    monkeypatch.setattr(app, "_claim_runtime_event", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(app, "_resolve_event_reply", AsyncMock(return_value=(None, event.reply_context)))
    monkeypatch.setattr(app, "_note_game_bot_activity", AsyncMock(side_effect=asyncio.CancelledError))
    handler = app.on_message_edited if edited else app.on_message
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(handler(raw))
    assert value() == 440000
    assert len(env[cultivation.STATE_KEY]["ledger"]["entries"]) == 1
    duel.save_state.assert_called_once()
