import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, app_runtime, message_log_recovery, persistence, runtime, state as state_module
from model.features import _phaseful, concubine, concubine_fragment_actions as actions, passive_inbox
from tests.test_concubine_fragment_contract import NAME, panel


ID, ACCOUNT, CHAT, BOT, ROOT = 99088001, 8801, -100880001, 88088001, 88001
NOW = 1_700_000_500.0
FIELD = "concubine_fragment_actions"
KINDS = ("dream", "puzzle")
COMMANDS = {"dream": concubine.CMD_CONCUBINE_DREAM, "puzzle": concubine.CMD_CONCUBINE_PUZZLE}
DREAM = (
    "\u3010\u5165\u68a6\u5bfb\u56fe\u3011\n"
    "\u672c\u6b21\u68a6\u5146\u9501\u5b9a\uff1a\u3010\u865a\u5929\u6b8b\u56fe\u3011 \u7ebf\u8def\u3002\n"
    f"\u4f60\u4e0e\u4f8d\u59be\u3010{NAME}\u3011\u5171\u5165\u8ff7\u68a6\uff0c\u89c5\u5f97\u3010\u865a\u5929\u6b8b\u56fe\u3011\u788e\u7247\u3002\n"
    "\u672c\u6b21\u6389\u843d\u7387\uff1a28%\uff08\u5f53\u524d \u865a\u5929\u6b8b\u56fe 3/4\uff09\u3002"
)
PUZZLE = "\u3010\u865a\u5929\u6b8b\u56fe\u00b7\u62fc\u5408\u6210\u529f\u3011\n\u865a\u5929\u6b8b\u56fe\u8206\u56fe\u5df2\u6210\uff0c\u4fee\u4e3a +120\u3002"


@pytest.fixture
def env(monkeypatch, tmp_path):
    before = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_game_group_id(CHAT)
    state_module.set_game_bot_ids([BOT])
    state_module.set_identity_account(ID, ACCOUNT)
    identity = state_module.get_identity_state(ID)
    identity.update(
        concubine_enabled=True, concubine_phase="idle", concubine_availability="available",
        concubine_name=NAME, concubine_kind="\u9053\u5fc3\u4f8d\u59be", concubine_affinity=1000,
        concubine_last_snapshot_at=NOW - 10, concubine_dream_due_at=NOW - 1,
        concubine_last_greet_day=concubine._local_day_key(NOW), next_concubine_time=NOW,
    )
    with state_module.use_identity(ID):
        concubine._set_fragment_progress("xutian", 2, 4)
        concubine._set_fragment_progress("cangkun", 1, 4)
    clock = [NOW]
    send = AsyncMock(return_value=SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + .5, send_started_at=NOW))
    save, audit = Mock(return_value=True), AsyncMock()
    monkeypatch.setattr(concubine, "send_game_command", send)
    monkeypatch.setattr(concubine, "save_state", save)
    monkeypatch.setattr(concubine, "mark_dirty", Mock())
    monkeypatch.setattr(concubine, "send_audit_log", audit)
    monkeypatch.setattr(concubine, "console_log", Mock())
    monkeypatch.setattr(concubine, "_record_concubine_event", Mock())
    monkeypatch.setattr(concubine, "_record_concubine_ignored_reply", Mock())
    monkeypatch.setattr(concubine, "_CONCUBINE_QUERY_INFLIGHT", {})
    monkeypatch.setattr(actions, "_INFLIGHT", {})
    monkeypatch.setattr(concubine.time, "time", lambda: clock[0])
    monkeypatch.setattr(concubine.random, "uniform", lambda *_args: 120)
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args, **_kwargs: {"status": "none"})
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", Mock(return_value=[]))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[]))
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "fragment-actions.db"))
    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(passive_inbox, "save_state", save)
    monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock())
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    try:
        yield SimpleNamespace(identity=identity, clock=clock, send=send, save=save, audit=audit)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(before)


def prepare(env, kind):
    if kind == "puzzle":
        with state_module.use_identity(ID):
            concubine._set_fragment_progress("xutian", 4, 4)
            concubine._set_fragment_progress("cangkun", 4, 4)
            env.clock[0] = NOW - 2
            env.send.return_value = SimpleNamespace(id=ROOT - 10, chat_id=CHAT, sent_at=NOW - 2, send_started_at=NOW - 2)
            assert asyncio.run(concubine._send_fragment_command(NOW - 2))
            assert asyncio.run(concubine.handle_concubine_fragment_reply(
                panel(4, 4), NOW - 1,
                SimpleNamespace(id=ROOT - 10, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_FRAGMENT),
                current_msg_id=ROOT - 9, current_chat_id=CHAT, observed_at=NOW - 1,
                reply_context={"sender_id": BOT},
            ))
            assert concubine._is_current_fragment_confirmed(NOW)
            env.clock[0] = NOW
            env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + .5, send_started_at=NOW)
            env.identity["next_concubine_time"] = NOW
    env.send.reset_mock()
    env.save.reset_mock()
    env.audit.reset_mock()


async def start(env, kind):
    with state_module.use_identity(ID):
        return await getattr(concubine, f"_send_{kind}_command")(env.clock[0])


def receipt(env, kind):
    record = env.identity[FIELD][kind]
    env.identity["pending_tasks"][(CHAT, ROOT)] = {
        "cmd": COMMANDS[kind], "family": f"concubine_{kind}",
        "op_id": record["op_id"], "source_module": "concubine_fragments",
        "account_id": ACCOUNT, "chat_id": CHAT, "message_id": ROOT,
        "sent_at": NOW + .5, "send_started_at": NOW,
    }


async def reply(env, kind, *, text=None, route="direct", context=None, root=ROOT, at=NOW + 1, chat=CHAT):
    text = (DREAM if kind == "dream" else PUZZLE) if text is None else text
    parent = SimpleNamespace(id=root, chat_id=chat, sender_id=ID, raw_text=COMMANDS[kind])
    event = SimpleNamespace(id=root + 1, chat_id=chat, sender_id=BOT, server_event_at=at)
    if context is None:
        context = {"send_as_id": ID, "account_id": ACCOUNT, "chat_id": chat, "family": f"concubine_{kind}",
                   "root_msg_id": root, "reply_to_msg_id": root, "sender_id": BOT}
    if route == "native":
        return await app._handle_routed_reply_event(event, text, max(env.clock[0], at) + 1, parent, context)
    if route == "passive":
        return await passive_inbox.handle_passive_module_card(
            text, now=max(env.clock[0], at) + 1, reply_context=context, event=event, event_type="message")
    with state_module.use_identity(ID):
        return await getattr(concubine, f"handle_concubine_{kind}_reply")(
            text, max(env.clock[0], at) + 1, parent, matched_family=f"concubine_{kind}",
            current_msg_id=root + 1, current_chat_id=chat, observed_at=at, reply_context=context)


@pytest.mark.parametrize("kind", KINDS)
def test_fragment_action_persists_intent_and_tracks_without_retries(env, kind):
    prepare(env, kind)

    async def sent(command, **kwargs):
        record = env.identity[FIELD][kind]
        assert record["status"] == "sending"
        assert record["identity_id"] == ID and record["account_id"] == ACCOUNT and record["chat_id"] == CHAT
        assert command == record["command"] == COMMANDS[kind]
        assert kwargs["track"] is True and kwargs["max_retry"] == 0
        assert kwargs["source_module"] == "concubine_fragments"
        assert kwargs["op_id"] == record["op_id"] and kwargs["operation_check"]()
        assert kwargs["send_as_id"] == ID and kwargs["target_chat_id"] == CHAT
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "sent"
    assert env.identity[f"concubine_{kind}_msg_id"] == ROOT


@pytest.mark.parametrize("kind", KINDS)
def test_fragment_action_early_reply_cannot_be_rewound_by_send_return(env, kind):
    prepare(env, kind)

    async def sent(*_args, **_kwargs):
        receipt(env, kind)
        assert await reply(env, kind)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "complete"
    assert env.identity[f"concubine_{kind}_msg_id"] == 0
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_unknown_fragment_mutation_survives_restart_and_never_blindly_resends(env, kind, mode):
    prepare(env, kind)
    env.send.return_value = None
    if mode != "none":
        env.send.side_effect = RuntimeError("fixture") if mode == "exception" else asyncio.CancelledError()
        with pytest.raises(RuntimeError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(start(env, kind))
    else:
        assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unknown"
    before = copy.deepcopy(env.identity[FIELD])
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1000)
        asyncio.run(concubine.run_concubine_scheduler(NOW + 86400))
    assert env.identity[FIELD][kind]["op_id"] == before[kind]["op_id"]
    assert env.identity[FIELD][kind]["status"] in {"unknown", "sent"}
    assert env.identity["concubine_phase"] == kind + "_pending"
    assert not asyncio.run(start(env, kind))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_fragment_action_late_return_cannot_write_a_replacement_owner(env, kind, change):
    prepare(env, kind)
    after = {}

    async def sent(*_args, **_kwargs):
        if change == "delete":
            state_module.remove_identity(ID)
        elif change == "replace":
            state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
        else:
            state_module.set_identity_account(ID, ACCOUNT + 1)
        after.update(copy.deepcopy(state_module._meta_state))
        return env.send.return_value

    env.send.side_effect = sent
    assert not asyncio.run(start(env, kind))
    assert state_module._meta_state == after


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_unsaved_fragment_action_never_dispatches(env, kind, mode):
    prepare(env, kind)
    if mode == "false":
        env.save.return_value = False
        assert not asyncio.run(start(env, kind))
    else:
        env.save.side_effect = OSError("fixture")
        with pytest.raises(OSError):
            asyncio.run(start(env, kind))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_fragment_action_unknown_reply_does_not_clear_operation(env, kind, route):
    prepare(env, kind)
    asyncio.run(start(env, kind))
    receipt(env, kind)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind, text="unrecognized result", route=route))
    assert env.identity == before


def test_puzzle_success_never_fabricates_dream_cooldown(env):
    prepare(env, "puzzle")
    due_at = env.identity["concubine_dream_due_at"]
    assert asyncio.run(start(env, "puzzle"))
    assert asyncio.run(reply(env, "puzzle"))
    assert env.identity["concubine_dream_due_at"] == due_at


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_fragment_completion_is_owned_and_replay_safe(env, kind, route):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    receipt(env, kind)
    sibling = dict(env.identity["pending_tasks"][(CHAT, ROOT)], op_id="f" * 32)
    env.identity["pending_tasks"][(CHAT - 1, ROOT)] = dict(sibling, chat_id=CHAT - 1)
    assert asyncio.run(reply(env, kind, route=route))
    assert env.identity[FIELD][kind]["result"]["applied"] is True
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert env.identity["pending_tasks"][(CHAT - 1, ROOT)]["op_id"] == "f" * 32
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind, route=route))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [
    ("sender_id", BOT + 1), ("send_as_id", ID + 1), ("account_id", ACCOUNT + 1),
    ("chat_id", CHAT - 1), ("root_msg_id", ROOT - 1), ("reply_to_msg_id", ROOT - 1),
    ("op_id", "f" * 32), ("source_module", "other"), ("family", "concubine_status"),
    ("reply_to_command_edited", True), ("reply_to_command", ".other"),
])
def test_fragment_completion_rejects_foreign_route_evidence(env, kind, field, value):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    receipt(env, kind)
    context = {"sender_id": BOT, field: value}
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind, context=context))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("at", [0, None, True, "1700000501", float("nan"), NOW - 2, NOW + 20])
def test_fragment_completion_requires_trusted_server_clock(env, kind, at):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(actions.handle_reply(
            kind, DREAM if kind == "dream" else PUZZLE, NOW + 2,
            SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=COMMANDS[kind]),
            current_msg_id=ROOT + 1, current_chat_id=CHAT, observed_at=at, reply_context={"sender_id": BOT},
        ))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["direct", "native", "passive"])
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_failed_fragment_completion_save_rolls_back_every_projection_and_replays(env, kind, route, mode):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    receipt(env, kind)
    before = copy.deepcopy(env.identity)
    if mode == "false":
        env.save.return_value = False
        assert not asyncio.run(reply(env, kind, route=route))
    else:
        env.save.side_effect = OSError("fixture")
        with pytest.raises(OSError):
            asyncio.run(reply(env, kind, route=route))
    assert env.identity == before
    env.save.side_effect = None
    env.save.return_value = True
    assert asyncio.run(reply(env, kind, route=route))
    assert env.identity[FIELD][kind]["status"] == "complete"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["partner", "snapshot", "progress", "disabled", "timer"])
def test_late_fragment_completion_cannot_rewind_new_business_plan(env, kind, change):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    if change == "partner":
        env.identity["concubine_name"] = "another"
    elif change == "snapshot":
        env.identity["concubine_last_snapshot_at"] = NOW + 10
    elif change == "progress":
        env.identity["concubine_fragment_xutian_count"] = 1
    elif change == "disabled":
        env.identity["concubine_enabled"] = False
    else:
        env.identity["next_concubine_time"] = NOW + 10000
    before = copy.deepcopy(env.identity)
    assert asyncio.run(reply(env, kind))
    assert env.identity["next_concubine_time"] == before["next_concubine_time"]
    for key in ("concubine_name", "concubine_last_snapshot_at", "concubine_enabled"):
        assert env.identity[key] == before[key]
    if change in {"partner", "snapshot", "progress"}:
        assert env.identity[FIELD][kind]["result"]["applied"] is False
        assert env.identity["concubine_fragment_xutian_count"] == before["concubine_fragment_xutian_count"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [
    ("identity_id", ID + 1), ("account_id", True), ("chat_id", 0), ("partner", []),
    ("op_id", "invalid"), ("plan_key", {}), ("started_at", float("nan")),
    ("snapshot_at", NOW + 10), ("dream_due_at", False), ("msg_id", True),
    ("status", []), ("fragments", {"xutian": [True, 4], "cangkun": [0, 4]}),
    ("fragments", {"xutian": [0, 5], "cangkun": [0, 4]}), ("result", {}),
])
def test_corrupt_fragment_record_holds_without_repairing_it_from_guesses(env, kind, field, value):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    env.identity[FIELD][kind][field] = value
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert actions.records() is None
        assert actions.block_reason() == "invalid"
        assert asyncio.run(actions.recover(NOW + 86400))
        concubine.restore_concubine_runtime(NOW + 86400)
    assert not asyncio.run(start(env, kind))
    assert not asyncio.run(reply(env, kind))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("state", ["sent", "unknown", "complete"])
def test_fragment_action_and_receipt_survive_sqlite_reload(env, kind, state):
    prepare(env, kind)
    if state == "unknown":
        env.send.return_value = None
    asyncio.run(start(env, kind))
    receipt(env, kind)
    if state == "complete":
        assert asyncio.run(reply(env, kind))
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity[FIELD][kind]["status"] == state
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 60)
    if state != "complete":
        assert asyncio.run(reply(env, kind))
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind))
    assert env.identity == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("foreign_chat", [False, True])
def test_passive_fragment_owner_selection_needs_unique_chat_and_operation(env, kind, foreign_chat):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    other = ID + 1
    state_module.set_identity_account(other, ACCOUNT + 1)
    second = state_module.get_identity_state(other)
    second.update(copy.deepcopy(env.identity))
    second[FIELD][kind].update(identity_id=other, account_id=ACCOUNT + 1, chat_id=CHAT - 1 if foreign_chat else CHAT)
    second_before = copy.deepcopy(second)
    handled = asyncio.run(reply(env, kind, route="passive", context={
        "family": "concubine_" + kind, "reply_to_msg_id": ROOT, "root_msg_id": ROOT,
    }))
    assert handled is foreign_chat
    assert second == second_before
    assert env.identity[FIELD][kind]["status"] == ("complete" if foreign_chat else "sent")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("fresh", [False, True])
def test_only_fresh_unsent_evidence_authorizes_local_retry(env, kind, fresh, monkeypatch):
    prepare(env, kind)
    block = {"status": "unsent", "code": "send_queue_timeout", "at": NOW - 1}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def unsent(*_args, **_kwargs):
        if fresh:
            block["at"] = NOW
        return None

    env.send.side_effect = unsent
    assert not asyncio.run(start(env, kind))
    assert env.identity["concubine_dream_due_at"] == NOW - 1
    assert env.identity[FIELD][kind]["status"] == ("unsent" if fresh else "unknown")
    assert not asyncio.run(start(env, kind))
    if fresh:
        env.clock[0] += 121
        env.send.side_effect = None
        env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW + 121, send_started_at=NOW + 121)
        assert asyncio.run(start(env, kind))
        assert env.send.await_count == 2
    else:
        env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["global", "identity", "module", "partner", "nanlong", "query"])
def test_queued_fragment_guard_revalidates_owner_controls_and_other_work(env, kind, change, monkeypatch):
    prepare(env, kind)

    async def send(_command, **kwargs):
        assert kwargs["operation_check"]()
        if change == "global":
            state_module.set_global_enabled(False)
        elif change == "identity":
            state_module.set_identity_enabled(ID, False)
        elif change == "module":
            env.identity["concubine_enabled"] = False
        elif change == "partner":
            env.identity["concubine_name"] = "another"
        elif change == "nanlong":
            monkeypatch.setattr(concubine, "_has_active_nanlong_pending", lambda *_args: True)
        else:
            env.identity["concubine_status_query"] = {"bad": True}
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = send
    assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unknown"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_fragment_log_completion_retries_failed_save_without_expiring_mutation(env, kind, mode, monkeypatch):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    receipt(env, kind)
    entry = {"event_type": "message", "sender_is_bot": True, "sender_id": BOT, "chat_id": CHAT,
             "message_id": ROOT + 1, "reply_to_msg_id": ROOT, "server_event_at": NOW + 1,
             "text": DREAM if kind == "dream" else PUZZLE}
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[entry]))
    before = copy.deepcopy(env.identity)
    env.save.side_effect = [True, False if mode == "false" else OSError("fixture")]
    with state_module.use_identity(ID):
        if mode == "exception":
            with pytest.raises(OSError):
                asyncio.run(actions.recover(NOW + 1000))
        else:
            assert asyncio.run(actions.recover(NOW + 1000))
        assert env.identity[FIELD][kind]["status"] == "sent"
        assert env.identity["concubine_dream_due_at"] == before["concubine_dream_due_at"]
        assert env.identity["pending_tasks"] == before["pending_tasks"]
        env.save.side_effect = None
        assert asyncio.run(actions.recover(NOW + 1000 + concubine.CONCUBINE_QUERY_REPLAY_SEC + 1))
    assert env.identity[FIELD][kind]["status"] == "complete"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("text", [
    "\u68a6\u56fe\u611f\u5e94\u5c1a\u672a\u91cd\u542f\uff0c\u8bf7\u5728 7\u5c0f\u65f63\u5206\u949f22\u79d2 \u540e\u518d\u8bd5\u3002",
    "\u4fee\u4e3a\u4e0d\u8db3\uff0c\u5171\u68a6\u5bfb\u56fe\u9700\u8981\u4fee\u4e3a\u3002",
    "\u4f60\u5c1a\u65e0\u4f8d\u59be\uff0c\u65e0\u6cd5\u5171\u68a6\u5bfb\u56fe\u3002",
])
def test_dream_negative_result_uses_server_cd_or_local_retry_not_guessed_cd(env, text):
    assert asyncio.run(start(env, "dream"))
    assert asyncio.run(reply(env, "dream", text=text))
    outcome = env.identity[FIELD]["dream"]["result"]["outcome"]
    if outcome == "cooldown":
        assert env.identity["concubine_dream_due_at"] == NOW + 1 + 7 * 3600 + 3 * 60 + 22 + concubine.CD_BUFFER_SEC
    else:
        assert env.identity["concubine_dream_due_at"] == NOW - 1
        if outcome == "shortage":
            assert env.identity[FIELD]["dream"]["retry_at"] > NOW + 2
            assert not asyncio.run(start(env, "dream"))
        else:
            assert env.identity["concubine_availability"] == "unknown"


@pytest.mark.parametrize("kind", KINDS)
def test_legacy_fragment_pending_is_not_reset_or_replayed(env, kind):
    env.identity["concubine_phase"] = kind + "_pending"
    env.identity[f"concubine_{kind}_msg_id"] = ROOT
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1000)
        asyncio.run(concubine.run_concubine_scheduler(NOW + 86400))
        assert actions.block_reason() == "legacy_pending"
    assert env.identity == before
    env.send.assert_not_awaited()


def test_unknown_partner_is_queried_without_spending_a_dream(env):
    env.identity.update(concubine_name="", concubine_availability="unknown", concubine_last_snapshot_at=0)
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(NOW))
    assert env.send.await_args.args == (concubine.CMD_CONCUBINE_STATUS,)
    assert not env.identity[FIELD]


def finalize(env, kind, *, log=False):
    return runtime._finalize_game_command_sent(
        COMMANDS[kind], msg_id=ROOT, sent_at=NOW + .5, send_started_at=NOW,
        send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0,
        append_sent_log=log, reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC,
        send_intent={"op_id": env.identity[FIELD][kind]["op_id"], "source_module": actions.SOURCE},
    )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["native", "passive"])
def test_native_fragment_receipt_and_early_reply_do_not_rearm_pending(env, kind, route):
    prepare(env, kind)

    async def sent(*_args, **_kwargs):
        message = finalize(env, kind)
        assert "account_id" not in env.identity["pending_tasks"][(CHAT, ROOT)]
        assert await reply(env, kind, route=route)
        finalize(env, kind)
        return message

    env.send.side_effect = sent
    assert asyncio.run(start(env, kind))
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert env.identity[FIELD][kind]["status"] == "complete"


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_fragment_native_log_recovers_after_reload_without_resending(env, kind, monkeypatch, tmp_path):
    prepare(env, kind)
    monkeypatch.setattr(runtime, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(message_log_recovery, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", message_log_recovery.find_recent_message_log_commands)
    monkeypatch.setattr(concubine, "find_message_log_replies", message_log_recovery.find_message_log_replies)
    env.send.return_value = None
    assert not asyncio.run(start(env, kind))
    with state_module.use_identity(ID):
        finalize(env, kind, log=True)
    env.identity["pending_tasks"] = {}
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    path = tmp_path / (datetime.fromtimestamp(NOW, concubine.TZ_LOCAL).strftime("%Y-%m-%d") + ".log")
    entry = {
        "ts": datetime.fromtimestamp(NOW + 1, concubine.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "event_type": "message", "sender_id": BOT, "sender_is_bot": True, "chat_id": CHAT,
        "message_id": ROOT + 1, "reply_to_msg_id": ROOT, "server_event_at": NOW + 1,
        "text": DREAM if kind == "dream" else PUZZLE,
    }
    with path.open("a") as log:
        log.write(json.dumps(entry) + "\n")
    with state_module.use_identity(ID):
        assert asyncio.run(actions.recover(NOW + 61))
    assert env.identity[FIELD][kind]["status"] == "complete"
    assert env.identity[FIELD][kind]["result"]["applied"] is True
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [
    ("op_id", "other"), ("source_module", "other"), ("account_id", ACCOUNT + 1),
    ("cmd", ".other"), ("chat_id", CHAT - 1), ("message_id", ROOT + 1),
    ("send_started_at", NOW - 10), ("sent_at", NOW + 20),
])
def test_unknown_fragment_cannot_adopt_foreign_receipt(env, kind, field, value):
    prepare(env, kind)
    env.send.return_value = None
    assert not asyncio.run(start(env, kind))
    receipt(env, kind)
    env.identity["pending_tasks"][(CHAT, ROOT)][field] = value
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("text", [
    "\u3010\u6df1\u5ea6\u95ed\u5173\u603b\u7ed3\u3011\n\u4fee\u4e3a +100",
    "\u3010\u5165\u68a6\u5bfb\u56fe\u3011",
    DREAM.replace("3/4", "3/5"), DREAM.replace("3/4", "-1/4"), DREAM.replace("3/4", "3.0/4"),
    DREAM + "\n\u5f53\u524d\u8fdb\u5ea6\uff1a2/4\u3002",
    DREAM + "\n\u6b8b\u56fe\u5c1a\u672a\u9f50\u5168",
    "\u68a6\u56fe\u611f\u5e94\u5c1a\u672a\u91cd\u542f\u3002",
    "\u68a6\u56fe\u611f\u5e94\u5c1a\u672a\u91cd\u542f\uff0c\u8bf7\u5728 -1\u5c0f\u65f6 \u540e\u518d\u8bd5\u3002",
    PUZZLE + "\n\u6b8b\u56fe\u5c1a\u672a\u9f50\u5168",
])
def test_partial_or_conflicting_fragment_result_never_completes_mutation(env, kind, text):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind, text=text))
    assert env.identity == before


def test_puzzle_incomplete_revokes_confirmation_but_never_invents_missing_pieces(env):
    prepare(env, "puzzle")
    assert asyncio.run(start(env, "puzzle"))
    assert asyncio.run(reply(env, "puzzle", text="\u6b8b\u56fe\u5c1a\u672a\u9f50\u5168\uff0c\u7f3a\u5931\u6b8b\u7eb9\uff1a\u5317\u9619\u6b8b\u7eb9\u3002"))
    assert env.identity["concubine_fragment_confirm_key"] == ""
    assert env.identity["concubine_fragment_xutian_count"] == 4
    assert env.identity["concubine_fragment_cangkun_count"] == 4
    assert not asyncio.run(start(env, "puzzle"))


def test_puzzle_queue_cannot_spend_expired_confirmation(env):
    prepare(env, "puzzle")

    async def sent(_command, **kwargs):
        assert kwargs["operation_check"]()
        env.clock[0] += concubine.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC + 1
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env, "puzzle"))
    assert env.identity[FIELD]["puzzle"]["status"] == "unknown"


def test_full_dream_fragment_puzzle_chain_preserves_real_dream_cd(env):
    assert asyncio.run(start(env, "dream"))
    assert asyncio.run(reply(env, "dream", text=DREAM.replace("3/4", "4/4")))
    due_at = env.identity["concubine_dream_due_at"]
    assert env.identity["concubine_fragment_confirm_key"] == ""
    assert not asyncio.run(start(env, "puzzle"))
    env.clock[0] = env.identity["next_concubine_time"] + 1
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
        assert env.send.await_args.args == (concubine.CMD_CONCUBINE_FRAGMENT,)
        assert asyncio.run(concubine.handle_concubine_fragment_reply(
            panel(4, 1), env.clock[0] + 1, SimpleNamespace(id=ROOT + 10, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_FRAGMENT),
            current_msg_id=ROOT + 11, current_chat_id=CHAT, observed_at=env.clock[0] + 1,
            reply_context={"sender_id": BOT},
        ))
        env.clock[0] = env.identity["next_concubine_time"] + 1
        env.send.return_value = SimpleNamespace(id=ROOT + 20, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    assert env.send.await_args.args == (concubine.CMD_CONCUBINE_PUZZLE,)
    assert asyncio.run(reply(env, "puzzle", root=ROOT + 20, at=env.clock[0] + 1))
    assert env.identity["concubine_fragment_xutian_count"] == 0
    assert env.identity["concubine_fragment_cangkun_count"] == 1
    assert env.identity["concubine_dream_due_at"] == due_at
    assert env.identity["next_concubine_time"] >= due_at
    assert env.send.await_count == 3


@pytest.mark.parametrize("kind", KINDS)
def test_voyage_rejection_without_wait_cannot_borrow_cached_return_time(env, kind):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    env.identity["concubine_voyage_return_at"] = NOW + 3600
    before = copy.deepcopy(env.identity)
    assert asyncio.run(reply(env, kind, text="\u4f8d\u59be\u4ecd\u5728\u8fdc\u822a\u9014\u4e2d\uff0c\u6682\u65e0\u6cd5\u4e0e\u4f60\u540c\u68a6\u5bfb\u56fe\u3002"))
    result = env.identity[FIELD][kind]["result"]
    assert result["outcome"] == "voyage_lock" and result["wait_until"] == 0
    assert env.identity["concubine_voyage_return_at"] == before["concubine_voyage_return_at"]
    assert env.identity["concubine_dream_due_at"] == before["concubine_dream_due_at"]
    assert env.identity["next_concubine_time"] == before["next_concubine_time"]


@pytest.mark.parametrize("kind", KINDS)
def test_explicit_voyage_wait_survives_later_voyage_state_change(env, kind):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    text = "\u4f8d\u59be\u4ecd\u5728\u8fdc\u822a\u9014\u4e2d\uff0c\u9884\u8ba1\u5f52\u822a\u8fd8\u9700 1\u5c0f\u65f6\u3002"
    assert asyncio.run(reply(env, kind, text=text))
    assert env.identity[FIELD][kind]["result"]["wait_until"] == NOW + 1 + 3600 + concubine.CD_BUFFER_SEC
    due_at = env.identity["concubine_dream_due_at"]
    env.identity.update(concubine_voyage_return_at=0, concubine_voyage_status="idle")
    with state_module.use_identity(ID):
        assert actions.records()[kind]["status"] == "complete"
    assert env.identity["concubine_dream_due_at"] == due_at


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("save_mode", ["success", "false", "exception"])
def test_late_native_registration_after_completion_cleans_only_its_own_pending(env, kind, save_mode):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    assert asyncio.run(reply(env, kind))
    with state_module.use_identity(ID):
        finalize(env, kind)
    env.identity["pending_tasks"][(CHAT, ROOT + 10)] = dict(
        env.identity["pending_tasks"][(CHAT, ROOT)], message_id=ROOT + 10, op_id="f" * 32)
    before = copy.deepcopy(env.identity)
    if save_mode == "false":
        env.save.return_value = False
    elif save_mode == "exception":
        env.save.side_effect = OSError("fixture")
    with state_module.use_identity(ID):
        if save_mode == "exception":
            with pytest.raises(OSError):
                asyncio.run(actions.recover(NOW + 60))
        else:
            assert asyncio.run(actions.recover(NOW + 60))
    if save_mode != "success":
        assert env.identity == before
        env.save.side_effect = None
        env.save.return_value = True
        with state_module.use_identity(ID):
            assert asyncio.run(actions.recover(NOW + 120))
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert env.identity["pending_tasks"][(CHAT, ROOT + 10)]["op_id"] == "f" * 32
    assert env.identity[FIELD] == before[FIELD]
    assert env.identity["next_concubine_time"] == before["next_concubine_time"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_recovery_checkpoint_cannot_dirty_an_unsaved_receipt_or_retry_clock(env, kind, mode):
    prepare(env, kind)
    env.send.return_value = None
    assert not asyncio.run(start(env, kind))
    receipt(env, kind)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        if mode == "false":
            env.save.return_value = False
            assert asyncio.run(actions.recover(NOW + 10))
        else:
            env.save.side_effect = OSError("fixture")
            with pytest.raises(OSError):
                asyncio.run(actions.recover(NOW + 10))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
def test_heavenly_ban_routes_to_safety_without_fabricating_fragment_completion(env, kind, monkeypatch):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    before = copy.deepcopy(env.identity)
    safety = concubine.heavenly_ban_mod
    monkeypatch.setattr(safety, "save_state", env.save)
    monkeypatch.setattr(safety, "send_audit_log", AsyncMock())
    monkeypatch.setattr(safety, "mark_account_offline", Mock())
    text = "\u3010\u5929\u9053\u5c01\u7981\u3011\u5df2\u88ab\u6253\u4e0a\u5c01\u7981\u70d9\u5370\u3002"
    assert not asyncio.run(reply(env, kind, text=text, route="native"))
    assert asyncio.run(reply(env, kind, text=text, route="passive"))
    assert not state_module.get_identity_enabled(ID)
    assert env.identity[FIELD] == before[FIELD]
    assert env.identity["concubine_dream_due_at"] == before["concubine_dream_due_at"]
    assert env.identity["concubine_phase"] == before["concubine_phase"]
    assert not asyncio.run(start(env, kind))


@pytest.mark.parametrize("legacy", [False, True])
def test_shared_summary_replay_cannot_bypass_dream_ownership(env, legacy, monkeypatch):
    if legacy:
        env.identity.update(concubine_phase="dream_pending", concubine_dream_msg_id=ROOT)
    else:
        assert asyncio.run(start(env, "dream"))
        receipt(env, "dream")
    monkeypatch.setattr(_phaseful.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(_phaseful, "send_game_command", env.send)
    monkeypatch.setattr(_phaseful, "send_audit_log", env.audit)
    monkeypatch.setattr(_phaseful, "save_state", env.save)
    before = copy.deepcopy(env.identity)
    env.send.reset_mock()
    asyncio.run(_phaseful._replay_summary_consumed_command(ID, {
        "cmd": COMMANDS["dream"], "chat_id": CHAT, "msg_id": ROOT,
        "sent_at": NOW, "track": not legacy, "max_retry": 0,
    }))
    env.send.assert_not_awaited()
    assert env.identity == before
