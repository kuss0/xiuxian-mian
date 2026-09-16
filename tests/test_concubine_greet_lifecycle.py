import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, app_runtime, persistence, runtime, state as state_module
from model.features import concubine, concubine_affinity_actions, passive_inbox


ID, ACCOUNT, CHAT, BOT, ROOT = 99084001, 8401, -100840001, 88084001, 84001
NOW = 1_700_000_500.0
FIELD = "concubine_greet_action"
NAME = "\u51cc\u7389\u7075"
SUCCESS = "\u4f8d\u59be\u3010\u51cc\u7389\u7075\u3011\u5411\u4f60\u5fae\u5fae\u9894\u9996\uff0c\u4f60\u4eec\u7684\u60c5\u7f18\u589e\u52a0\u4e86 30 \u70b9\u3002"
ALREADY = "\u4eca\u65e5\u5df2\u7ecf\u95ee\u5b89\u8fc7\u4e86\uff0c\u8bf7\u52ff\u8fc7\u591a\u6253\u6270\u3002\u4f60\u7684\u5fc3\u610f\u5979\u5df2\u6536\u5230\u3002"
SUMMARY = "\u5929\u9053\u611f\u5e94\uff1a\u68c0\u6d4b\u5230 @greet_owner \u529f\u6210\u5706\u6ee1\uff0c\u795e\u9b42\u6b63\u5728\u5f52\u4f4d..."


@pytest.fixture
def env(monkeypatch, tmp_path):
    before = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_game_group_id(CHAT)
    state_module.set_game_bot_ids([BOT])
    state_module.set_identity_account(ID, ACCOUNT)
    state_module.update_send_as_profile(ID, username="greet_owner", sect_name="\u661f\u5bab")
    identity = state_module.get_identity_state(ID)
    identity.update(
        concubine_enabled=True, concubine_tianji_enabled=True,
        concubine_phase="idle", concubine_availability="available",
        concubine_name=NAME, concubine_kind="\u9053\u5fc3\u4f8d\u59be", concubine_affinity=270,
        concubine_last_snapshot_at=NOW - 1, concubine_last_panel_msg_id=ROOT - 1,
    )
    clock, block = [NOW], {"status": "none"}
    send = AsyncMock(return_value=SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + .2, send_started_at=NOW))
    save, audit = Mock(return_value=True), AsyncMock()
    monkeypatch.setattr(concubine, "send_game_command", send)
    monkeypatch.setattr(concubine_affinity_actions, "_INFLIGHT", {})
    monkeypatch.setattr(concubine, "save_state", save)
    monkeypatch.setattr(concubine, "mark_dirty", Mock())
    monkeypatch.setattr(concubine, "send_audit_log", audit)
    monkeypatch.setattr(concubine, "console_log", Mock())
    monkeypatch.setattr(concubine.time, "time", lambda: clock[0])
    monkeypatch.setattr(concubine.random, "uniform", lambda *_args: 120)
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args, **_kwargs: dict(block))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", Mock(return_value=[]))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[]))
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "greet.db"))
    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(passive_inbox, "save_state", save)
    monkeypatch.setattr(passive_inbox, "_save_passive_stats", Mock())
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    monkeypatch.setattr(passive_inbox, "_passive_stats", {
        "total": 0, "changed": 0, "skipped": 0, "modules": {}, "skip_reasons": {}, "recent": [],
    })
    try:
        yield SimpleNamespace(identity=identity, clock=clock, send=send, save=save, audit=audit, block=block)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(before)


async def send_greet(env):
    with state_module.use_identity(ID):
        return await concubine._send_greet_command(env.clock[0])


def receipt(env, root=ROOT):
    record = env.identity[FIELD]
    env.identity["pending_tasks"][(CHAT, root)] = {
        "cmd": record["command"], "family": "concubine_greet", "op_id": record["op_id"],
        "source_module": "concubine_greet", "account_id": ACCOUNT, "chat_id": CHAT,
        "message_id": root, "sent_at": env.clock[0] + .2, "send_started_at": env.clock[0],
    }


async def reply(env, text=SUCCESS, *, root=ROOT, **changes):
    kwargs = dict(
        matched_family="concubine_greet", current_msg_id=ROOT + 1, current_chat_id=CHAT,
        observed_at=env.clock[0] + 1, reply_context={"sender_id": BOT},
    )
    kwargs.update(changes)
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_greet_reply(
            text, env.clock[0] + 2,
            SimpleNamespace(id=root, raw_text=concubine.CMD_CONCUBINE_DAILY_GREET, chat_id=CHAT), **kwargs,
        )


def tick(env, at):
    env.clock[0] = at
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(at))


def test_greet_saves_owned_intent_before_sending(env):
    async def sent(command, **kwargs):
        record = env.identity.get(FIELD, {})
        assert record["status"] == "sending"
        assert record["command"] == command == concubine.CMD_CONCUBINE_DAILY_GREET
        assert record["identity_id"] == ID and record["account_id"] == ACCOUNT
        assert kwargs["send_as_id"] == ID and kwargs["target_chat_id"] == CHAT
        assert kwargs["source_module"] == "concubine_greet" and kwargs["op_id"] == record["op_id"]
        assert kwargs["track"] is True and kwargs["max_retry"] == 0
        assert kwargs["reply_timeout"] == concubine.CONCUBINE_PHASE_TIMEOUT_SEC
        assert kwargs["operation_check"]()
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == "sent"


@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_late_greet_receipt_does_not_write_through_replaced_owner(env, change):
    after = {}

    async def sent(_command, **_kwargs):
        if change == "delete":
            state_module.remove_identity(ID)
        elif change == "replace":
            state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
        else:
            state_module.set_identity_account(ID, ACCOUNT + 1)
        after.update(copy.deepcopy(state_module._meta_state))
        return env.send.return_value

    env.send.side_effect = sent
    assert not asyncio.run(send_greet(env))
    assert state_module._meta_state == after


@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_unknown_greet_survives_restart_and_never_blindly_retries(env, mode):
    env.send.return_value = None
    if mode == "none":
        assert not asyncio.run(send_greet(env))
    else:
        error = RuntimeError if mode == "exception" else asyncio.CancelledError
        env.send.side_effect = error()
        with pytest.raises(error):
            asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == "unknown"
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1)
    assert env.identity == before
    for at in (NOW + 901, NOW + 86400, NOW + 7 * 86400):
        tick(env, at)
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity["concubine_last_greet_day"] == ""
    env.send.assert_awaited_once()


@pytest.mark.parametrize("change", ["global", "identity", "module", "partner", "affinity", "schedule", "chat", "day", "summary"])
def test_queued_greet_checks_owner_controls_and_plan(env, change):
    async def sent(_command, **kwargs):
        if change == "global":
            state_module.set_global_enabled(False)
        elif change == "identity":
            state_module.set_identity_enabled(ID, False)
        elif change == "module":
            env.identity["concubine_tianji_enabled"] = False
        elif change == "partner":
            env.identity["concubine_name"] = "replacement"
        elif change == "affinity":
            env.identity["concubine_affinity"] = 300
        elif change == "schedule":
            env.identity["next_concubine_time"] = NOW + 40000
        elif change == "chat":
            state_module.set_game_group_id(CHAT - 1)
        elif change == "day":
            env.clock[0] += 86400
        else:
            env.identity.update(deep_retreat_enabled=True, deep_retreat_phase="observing_summary")
        assert not kwargs["operation_check"]()
        env.block.update(status="unsent", code="operation_cancelled", at=env.clock[0])
        return None

    env.send.side_effect = sent
    assert not asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == "unsent"
    assert env.identity["concubine_last_greet_day"] == ""
    if change == "schedule":
        assert env.identity["next_concubine_time"] == NOW + 40000


@pytest.mark.parametrize("outcome", [False, "exception"])
def test_failed_intent_save_never_sends_greet(env, outcome):
    if outcome == "exception":
        env.save.side_effect = OSError("fixture")
        with pytest.raises(OSError):
            asyncio.run(send_greet(env))
    else:
        env.save.return_value = False
        assert not asyncio.run(send_greet(env))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("text,gain", [(SUCCESS, 30), (ALREADY, 0)])
def test_greet_result_is_idempotent_and_survives_daily_marker_reset(env, text, gain):
    assert asyncio.run(send_greet(env))
    receipt(env)
    assert asyncio.run(reply(env, text))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 270 + gain
    assert env.identity["concubine_last_greet_day"] == concubine._local_day_key(NOW)
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text))
    assert env.identity == before
    env.identity.update(concubine_phase="idle", concubine_affinity=270, concubine_last_greet_day="")
    assert not asyncio.run(send_greet(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("text", [SUMMARY, "working", SUCCESS.replace(NAME, "different_partner")])
def test_nonterminal_greet_text_keeps_the_owned_operation(env, text):
    assert asyncio.run(send_greet(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text))
    assert env.identity == before
    tick(env, NOW + 2000)
    assert env.identity[FIELD]["status"] == "sent"
    assert env.identity["concubine_last_greet_day"] == ""
    env.send.assert_awaited_once()


def test_legacy_greet_pending_is_held_not_erased_or_fabricated_complete(env):
    env.identity.update(concubine_phase="greet_pending", concubine_greet_msg_id=ROOT,
                        concubine_greet_retry_count=1, next_concubine_time=NOW - 1)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW)
        asyncio.run(concubine.run_concubine_scheduler(NOW + 2000))
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("late", ["receipt", "exception", "cancel"])
def test_early_completion_wins_over_a_late_transport_result(env, late):
    async def sent(_command, **_kwargs):
        receipt(env)
        assert await reply(env)
        assert env.identity[FIELD]["status"] == "complete"
        if late == "exception":
            raise RuntimeError("fixture")
        if late == "cancel":
            raise asyncio.CancelledError()
        return env.send.return_value

    env.send.side_effect = sent
    if late == "receipt":
        assert asyncio.run(send_greet(env))
    else:
        with pytest.raises(RuntimeError if late == "exception" else asyncio.CancelledError):
            asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_greet_msg_id"] == 0
    assert env.identity["concubine_affinity"] == 300
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("change", ["module", "global", "identity", "schedule", "partner", "affinity", "snapshot"])
def test_confirmed_greet_respects_changed_controls_and_observations(env, change):
    assert asyncio.run(send_greet(env))
    receipt(env)
    if change == "module":
        env.identity["concubine_tianji_enabled"] = False
    elif change == "global":
        state_module.set_global_enabled(False)
    elif change == "identity":
        state_module.set_identity_enabled(ID, False)
    elif change == "schedule":
        env.identity["next_concubine_time"] = NOW + 40000
    elif change == "partner":
        env.identity["concubine_name"] = "replacement"
    elif change == "affinity":
        env.identity["concubine_affinity"] = 300
    else:
        env.identity["concubine_last_snapshot_at"] = NOW + .5
    next_at = env.identity["next_concubine_time"]
    assert asyncio.run(reply(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_last_greet_day"] == concubine._local_day_key(NOW)
    assert env.identity["next_concubine_time"] == next_at
    assert env.identity[FIELD]["result"]["applied"] is (change not in {"partner", "affinity", "snapshot"})
    assert env.identity["concubine_affinity"] == (270 if change in {"partner", "snapshot"} else 300)
    env.send.assert_awaited_once()


@pytest.mark.parametrize("field,value", [
    ("send_as_id", ID + 1), ("account_id", ACCOUNT + 1), ("chat_id", CHAT - 1),
    ("root_msg_id", ROOT + 20), ("reply_to_msg_id", ROOT + 20), ("sender_id", BOT + 1),
    ("reply_to_command_edited", True), ("reply_to_command", ".unrelated"),
])
def test_greet_rejects_conflicting_reply_context(env, field, value):
    assert asyncio.run(send_greet(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, reply_context={"sender_id": BOT, field: value}))
    assert env.identity == before


@pytest.mark.parametrize("changes", [
    {"root": ROOT + 20}, {"current_chat_id": CHAT - 1}, {"current_msg_id": ROOT},
    {"current_msg_id": ROOT + .5}, {"observed_at": NOW - 2}, {"observed_at": NOW + 3},
    {"observed_at": 0}, {"observed_at": float("nan")}, {"reply_context": {}},
])
def test_greet_requires_exact_root_official_sender_and_server_clock(env, changes):
    assert asyncio.run(send_greet(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, **changes))
    assert env.identity == before


@pytest.mark.parametrize("outcome", [False, "exception"])
def test_completion_save_failure_restores_pending_before_replay(env, outcome):
    assert asyncio.run(send_greet(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    if outcome == "exception":
        env.save.side_effect = OSError("fixture")
        with pytest.raises(OSError):
            asyncio.run(reply(env))
    else:
        env.save.return_value = False
        assert not asyncio.run(reply(env))
    assert env.identity == before
    env.save.side_effect = None
    env.save.return_value = True
    assert asyncio.run(reply(env))
    assert env.identity["concubine_affinity"] == 300
    assert not asyncio.run(reply(env))
    env.send.assert_awaited_once()


async def routed_reply(env, route, *, text=SUCCESS, **changes):
    context = {"send_as_id": ID, "family": "concubine_greet", "root_msg_id": ROOT,
               "reply_to_msg_id": ROOT, "chat_id": CHAT, "reply_to_command": concubine.CMD_CONCUBINE_DAILY_GREET}
    context.update(changes)
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=env.clock[0] + 1)
    if route == "native":
        parent = SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_DAILY_GREET)
        return await app._handle_routed_reply_event(event, text, env.clock[0] + 2, parent, context)
    return await passive_inbox.handle_passive_module_card(
        text, now=env.clock[0] + 2, reply_context=context, event=event, event_type="message")


@pytest.mark.parametrize("route", ["native", "passive"])
def test_real_greet_reply_paths_use_the_owned_reducer(env, route):
    assert asyncio.run(send_greet(env))
    receipt(env)
    assert asyncio.run(routed_reply(env, route))
    assert env.identity[FIELD]["status"] == "complete"
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(routed_reply(env, route))
    assert env.identity == before


@pytest.mark.parametrize("route", ["native", "passive"])
def test_reply_dedupe_cannot_hide_a_failed_local_completion_save(env, route):
    assert asyncio.run(send_greet(env))
    receipt(env)
    env.save.return_value = False
    assert not asyncio.run(routed_reply(env, route))
    assert env.identity[FIELD]["status"] == "sent"
    env.save.return_value = True
    assert asyncio.run(routed_reply(env, route))
    assert env.identity[FIELD]["status"] == "complete"


@pytest.mark.parametrize("route", ["native", "passive"])
def test_unowned_legacy_reply_cannot_invent_greet_completion(env, route):
    env.identity.update(concubine_phase="greet_pending", concubine_greet_msg_id=ROOT)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(routed_reply(env, route))
    assert env.identity == before


@pytest.mark.parametrize("stale", [True, False])
def test_only_fresh_unsent_evidence_can_release_a_greet_for_retry(env, stale):
    if stale:
        env.block.update(status="unsent", code="bot_health", at=NOW - 2)

    async def sent(_command, **_kwargs):
        if not stale:
            env.block.update(status="unsent", code="bot_health", at=NOW)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == ("unknown" if stale else "unsent")
    assert not asyncio.run(send_greet(env))
    env.send.assert_awaited_once()
    if not stale:
        env.clock[0] = NOW + 121
        env.send.side_effect = None
        env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW + 121.2, send_started_at=NOW + 121)
        assert asyncio.run(send_greet(env))
        assert env.identity[FIELD]["status"] == "sent"
        assert env.send.await_count == 2


@pytest.mark.parametrize("field,value", [
    ("id", 0), ("id", True), ("id", ROOT + .5), ("chat_id", CHAT - 1),
    ("sent_at", NOW - 5), ("sent_at", NOW + 5), ("sent_at", float("inf")),
    ("send_started_at", 0), ("send_started_at", NOW + .5),
])
def test_invalid_send_receipt_keeps_greet_unknown(env, field, value):
    setattr(env.send.return_value, field, value)
    assert not asyncio.run(send_greet(env))
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity[FIELD]["msg_id"] == 0
    tick(env, NOW + 901)
    env.send.assert_awaited_once()


def test_cross_day_delivery_closes_original_day_not_delivery_day(env):
    assert asyncio.run(send_greet(env))
    env.clock[0] += 86400
    assert asyncio.run(reply(env, ALREADY))
    assert env.identity["concubine_last_greet_day"] == concubine._local_day_key(NOW)
    assert env.identity[FIELD]["day"] != concubine._local_day_key(env.clock[0])
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0] + .2, send_started_at=env.clock[0])
    assert asyncio.run(send_greet(env))
    assert env.identity[FIELD]["day"] == concubine._local_day_key(env.clock[0])


def logged_reply(**changes):
    entry = dict(event_type="message", text=SUCCESS, sender_is_bot=True, sender_id=BOT,
                 chat_id=CHAT, reply_to_msg_id=ROOT, message_id=ROOT + 1,
                 server_event_at=NOW + 3, ts_epoch=NOW + 3)
    entry.update(changes)
    return entry


def test_log_recovery_replays_the_greet_result(env, monkeypatch):
    assert asyncio.run(send_greet(env))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply()]))
    tick(env, NOW + 61)
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 300
    env.send.assert_awaited_once()


@pytest.mark.parametrize("change", ["player", "bot", "chat", "root", "clock", "newer_incomplete", "same_clock_conflict"])
def test_log_recovery_rejects_untrusted_or_obsolete_greet_text(env, monkeypatch, change):
    assert asyncio.run(send_greet(env))
    entries = [logged_reply()]
    if change == "player":
        entries[0]["sender_is_bot"] = False
    elif change == "bot":
        entries[0]["sender_id"] = BOT + 1
    elif change == "chat":
        entries[0]["chat_id"] = CHAT - 1
    elif change == "root":
        entries[0]["reply_to_msg_id"] = ROOT + 100
    elif change == "clock":
        entries[0]["server_event_at"] = NOW - 2
    elif change == "newer_incomplete":
        entries.append(logged_reply(text="working", event_type="edit", server_event_at=NOW + 4))
    else:
        entries.append(logged_reply(text="conflict", event_type="edit"))
    lookup = Mock(return_value=entries)
    monkeypatch.setattr(concubine, "find_message_log_replies", lookup)
    tick(env, NOW + 61)
    calls = lookup.call_count
    tick(env, NOW + 62)
    assert lookup.call_count == calls
    assert env.identity[FIELD]["status"] == "sent"
    assert env.identity["concubine_affinity"] == 270
    env.send.assert_awaited_once()


def test_no_partner_result_requires_calibration_before_another_greet(env):
    assert asyncio.run(send_greet(env))
    assert asyncio.run(reply(env, "\u4f60\u5c1a\u65e0\u4f8d\u59be"))
    assert env.identity[FIELD]["result"]["outcome"] == "no_partner"
    assert env.identity["concubine_last_greet_day"] == ""
    tick(env, NOW + 2000)
    assert env.send.await_count == 2
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS


def test_greet_without_snapshot_does_not_guess_affinity_or_unlock_spending(env):
    env.identity["concubine_last_snapshot_at"] = 0
    assert asyncio.run(send_greet(env))
    assert asyncio.run(reply(env))
    assert env.identity["concubine_affinity"] == 270
    assert env.identity[FIELD]["result"]["applied"] is False
    tick(env, NOW + 2000)
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS


def test_voyage_lock_uses_reply_clock_not_delayed_delivery(env):
    assert asyncio.run(send_greet(env))
    env.clock[0] += 3600
    text = "\u4f8d\u59be\u4ecd\u5728\u8fdc\u822a\u4e2d\uff0c\u8bf7\u5728 11\u5c0f\u65f648\u5206\u949f5\u79d2 \u540e\u518d\u8bd5\u3002"
    assert asyncio.run(reply(env, text, observed_at=NOW + 1))
    expected = NOW + 1 + 11 * 3600 + 48 * 60 + 5 + concubine.CD_BUFFER_SEC
    assert env.identity[FIELD]["result"]["outcome"] == "voyage_lock"
    assert env.identity["concubine_voyage_return_at"] == expected
    assert env.identity["concubine_last_greet_day"] == ""
    tick(env, NOW + 7200)
    env.send.assert_awaited_once()


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_greet_recovery_checkpoint_failure_keeps_owned_record(env, monkeypatch, mode):
    assert asyncio.run(send_greet(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    scan = Mock(return_value=[])
    monkeypatch.setattr(concubine, "find_message_log_replies", scan)
    env.save.reset_mock()
    if mode == "exception":
        env.save.side_effect = OSError("checkpoint")
        with pytest.raises(OSError):
            tick(env, NOW + 1001)
    else:
        env.save.return_value = False
        tick(env, NOW + 1001)
    assert env.identity == before
    scan.assert_not_called()
    env.save.assert_called_once()


@pytest.mark.parametrize("mode", ["success", "false", "exception"])
def test_late_greet_pending_cleanup_preserves_sibling_and_failed_save(env, mode):
    assert asyncio.run(send_greet(env))
    assert asyncio.run(reply(env))
    receipt(env)
    env.identity["pending_tasks"][(CHAT - 1, ROOT)] = dict(
        env.identity["pending_tasks"][(CHAT, ROOT)], chat_id=CHAT - 1)
    before = copy.deepcopy(env.identity)
    if mode == "false":
        env.save.return_value = False
    elif mode == "exception":
        env.save.side_effect = OSError("cleanup")
    with state_module.use_identity(ID):
        if mode == "exception":
            with pytest.raises(OSError):
                asyncio.run(concubine_affinity_actions.recover(NOW + 30))
        else:
            assert asyncio.run(concubine_affinity_actions.recover(NOW + 30))
    if mode == "success":
        assert (CHAT, ROOT) not in env.identity["pending_tasks"]
        assert (CHAT - 1, ROOT) in env.identity["pending_tasks"]
    else:
        assert env.identity == before


@pytest.mark.parametrize("value", [None, [], {"extra": {}}, {"status": "sent"}])
def test_invalid_greet_container_is_held_without_dispatch_or_reset(env, value):
    env.identity[FIELD] = value
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert concubine_affinity_actions.block_reason() == "invalid"
        concubine.restore_concubine_runtime(NOW)
        asyncio.run(concubine.run_concubine_scheduler(NOW))
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("field,value", [
    ("status", []), ("affinity", True), ("affinity", 270.5), ("started_at", float("nan")),
    ("msg_id", float(ROOT)), ("account_id", "8401"), ("kind", []), ("plan_key", "broken"),
    ("affinity_seen_at", -1), ("affinity_seen_at", "0"), ("day", "yesterday"),
])
def test_invalid_greet_record_cannot_be_retried_or_completed(env, field, value):
    assert asyncio.run(send_greet(env))
    env.identity[FIELD][field] = value
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    with state_module.use_identity(ID):
        assert concubine_affinity_actions.block_reason() == "invalid"
        concubine.restore_concubine_runtime(NOW + 1000)
        asyncio.run(concubine.run_concubine_scheduler(NOW + 1000))
    assert env.identity == before
    env.send.assert_awaited_once()


def test_resetting_scalar_phase_does_not_unlock_unknown_greet(env):
    env.send.return_value = None
    assert not asyncio.run(send_greet(env))
    env.identity.update(concubine_phase="idle", concubine_greet_msg_id=0, next_concubine_time=0)
    with state_module.use_identity(ID):
        assert concubine.concubine_miniapp_status_block_reason(NOW + 10) == "affinity_action_pending"
        assert not asyncio.run(concubine._send_status_command(NOW + 10))
    tick(env, NOW + 1000)
    env.send.assert_awaited_once()


def test_real_receipt_can_recover_unknown_greet_without_an_account_column(env):
    env.send.return_value = None
    assert not asyncio.run(send_greet(env))
    record = env.identity[FIELD]
    with state_module.use_identity(ID):
        runtime._finalize_game_command_sent(
            record["command"], msg_id=ROOT, sent_at=NOW + .2, send_started_at=NOW,
            send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0, append_sent_log=False,
            reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC,
            send_intent={"op_id": record["op_id"], "source_module": "concubine_greet"},
        )
    pending = env.identity["pending_tasks"][(CHAT, ROOT)]
    assert "account_id" not in pending
    assert asyncio.run(reply(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("bad", ["operation", "account", "chat", "fractional", "multiple"])
def test_unknown_greet_rejects_ambiguous_or_contradictory_receipts(env, bad):
    env.send.return_value = None
    assert not asyncio.run(send_greet(env))
    receipt(env)
    pending = env.identity["pending_tasks"]
    if bad == "operation":
        pending[CHAT, ROOT]["op_id"] = "replacement"
    elif bad == "account":
        pending[CHAT, ROOT]["account_id"] = ACCOUNT + 1
    elif bad == "chat":
        item = pending.pop((CHAT, ROOT))
        item["chat_id"] = CHAT - 1
        pending[CHAT - 1, ROOT] = item
    elif bad == "fractional":
        pending[CHAT, ROOT + .5] = pending.pop((CHAT, ROOT))
    else:
        pending[CHAT, ROOT + 20] = dict(pending[CHAT, ROOT], message_id=ROOT + 20)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before


def test_greet_cleanup_keeps_a_replacement_at_the_same_root(env):
    assert asyncio.run(send_greet(env))
    receipt(env)
    env.identity["pending_tasks"][CHAT, ROOT]["op_id"] = "replacement"
    before = copy.deepcopy(env.identity["pending_tasks"])
    assert asyncio.run(reply(env))
    assert env.identity["pending_tasks"] == before


def test_greet_operation_is_not_reentered_while_awaiting_transport(env):
    async def sent(_command, **_kwargs):
        assert not await send_greet(env)
        await concubine.run_concubine_scheduler(NOW + 1000)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(send_greet(env))
    env.send.assert_awaited_once()


def test_greet_queue_check_uses_the_captured_identity_context(env):
    state_module.set_identity_account(ID + 1, ACCOUNT)
    state_module.get_identity_state(ID + 1).update(deep_retreat_enabled=True, deep_retreat_phase="observing_summary")

    async def sent(_command, **kwargs):
        with state_module.use_identity(ID + 1):
            assert kwargs["operation_check"]()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(send_greet(env))


@pytest.mark.parametrize("complete", [False, True])
def test_greet_record_survives_sqlite_reload(env, complete):
    assert FIELD in state_module.IDENTITY_JSON_COLUMNS
    assert FIELD in state_module.IDENTITY_RUNTIME_COLUMNS
    assert asyncio.run(send_greet(env))
    receipt(env)
    if complete:
        assert asyncio.run(reply(env))
    expected = copy.deepcopy(env.identity[FIELD])
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity[FIELD] == expected
    if complete:
        assert not asyncio.run(reply(env))
    else:
        assert asyncio.run(reply(env))
    assert env.identity["concubine_affinity"] == 300
    env.send.assert_awaited_once()


@pytest.mark.parametrize("text", [
    SUMMARY + "\n" + SUCCESS, SUCCESS + "\n" + ALREADY,
    SUCCESS + "\n" + SUCCESS, SUCCESS.replace("30", "0"), SUCCESS.replace("30", str(2 ** 63)),
])
def test_ambiguous_or_impossible_greet_result_cannot_complete(env, text):
    assert asyncio.run(send_greet(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text))
    assert env.identity == before


@pytest.mark.parametrize("value", [True, 270.5, "270", "bad", -1, float("nan")])
def test_invalid_affinity_never_enters_greet_transport(env, value):
    env.identity["concubine_affinity"] = value
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(send_greet(env))
    assert env.identity == before
    env.send.assert_not_awaited()
