import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, app_runtime, message_log_recovery, persistence, runtime, state as state_module
from model.features import concubine, passive_inbox
from tests.test_concubine_fragment_contract import NAME, panel


ID, ACCOUNT, CHAT, BOT, ROOT = 99086001, 8601, -100860001, 88086001, 86001
NOW = 1_700_000_500.0
FIELD = "concubine_status_query"


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
        concubine_name=NAME, concubine_kind="\u9053\u5fc3\u4f8d\u59be", concubine_affinity=500,
        concubine_dream_due_at=NOW + 3600, concubine_last_snapshot_at=NOW - 60,
    )
    with state_module.use_identity(ID):
        concubine._set_fragment_progress("xutian", 4, 4)
        concubine._set_fragment_progress("cangkun", 4, 4)
    clock = [NOW]
    send = AsyncMock(return_value=SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + .5, send_started_at=NOW))
    save, audit = Mock(return_value=True), AsyncMock()
    monkeypatch.setattr(concubine, "_CONCUBINE_QUERY_INFLIGHT", {})
    monkeypatch.setattr(concubine, "send_game_command", send)
    monkeypatch.setattr(concubine, "save_state", save)
    monkeypatch.setattr(concubine, "mark_dirty", Mock())
    monkeypatch.setattr(concubine, "console_log", Mock())
    monkeypatch.setattr(concubine, "send_audit_log", audit)
    monkeypatch.setattr(concubine.time, "time", lambda: clock[0])
    monkeypatch.setattr(concubine.random, "uniform", lambda *_args: 120)
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args, **_kwargs: {"status": "none"})
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", Mock(return_value=[]))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[]))
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "fragment.db"))
    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(passive_inbox, "save_state", save)
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock())
    try:
        yield SimpleNamespace(identity=identity, clock=clock, send=send, save=save, audit=audit)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(before)


async def start(env):
    with state_module.use_identity(ID):
        return await concubine._send_fragment_command(env.clock[0])


def receipt(env, *, root=ROOT, chat=CHAT):
    record = env.identity[FIELD]
    env.identity["pending_tasks"][(chat, root)] = {
        "cmd": concubine.CMD_CONCUBINE_FRAGMENT, "family": "concubine_fragment",
        "op_id": record["op_id"], "source_module": concubine.CONCUBINE_QUERY_SOURCE,
        "account_id": ACCOUNT, "chat_id": chat, "message_id": root,
        "sent_at": NOW + .5, "send_started_at": NOW,
    }


async def reply(env, text=None, *, root=ROOT, now=None, **changes):
    kwargs = dict(
        matched_family="concubine_fragment", current_msg_id=ROOT + 1,
        current_chat_id=CHAT, observed_at=NOW + 1, reply_context={"sender_id": BOT},
    )
    kwargs.update(changes)
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_fragment_reply(
            panel() if text is None else text, env.clock[0] + 2 if now is None else now,
            SimpleNamespace(id=root, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_FRAGMENT), **kwargs,
        )


def test_fragment_query_persists_owner_and_tracks_receipt_before_dispatch(env):
    async def sent(command, **kwargs):
        record = env.identity[FIELD]
        assert record["kind"] == "fragment"
        assert record["status"] == "sending"
        assert record["partner"] == NAME
        assert record["identity_id"] == ID and record["account_id"] == ACCOUNT and record["chat_id"] == CHAT
        assert command == record["command"] == concubine.CMD_CONCUBINE_FRAGMENT
        assert kwargs["track"] is True and kwargs["max_retry"] == 0
        assert kwargs["reply_timeout"] == concubine.CONCUBINE_PHASE_TIMEOUT_SEC
        assert kwargs["target_chat_id"] == CHAT and kwargs["send_as_id"] == ID
        assert kwargs["op_id"] == record["op_id"] and kwargs["operation_check"]()
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "sent"


@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_fragment_late_receipt_cannot_write_a_different_owner(env, change):
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
    assert not asyncio.run(start(env))
    assert state_module._meta_state == after


@pytest.mark.parametrize("change", ["pause", "identity", "module", "partner", "chat", "phase", "progress", "schedule"])
def test_fragment_queued_read_revalidates_owner_controls_and_business(env, change, monkeypatch):
    async def sent(*_args, **kwargs):
        if change == "pause":
            state_module.set_global_enabled(False)
        elif change == "identity":
            state_module.set_identity_enabled(ID, False)
        elif change == "module":
            env.identity["concubine_enabled"] = False
            env.identity["concubine_tianji_enabled"] = True
        elif change == "partner":
            env.identity["concubine_name"] = "replacement"
        elif change == "chat":
            state_module.set_game_group_id(CHAT - 1)
        elif change == "phase":
            env.identity["concubine_phase"] = "puzzle_pending"
        elif change == "progress":
            env.identity["concubine_fragment_cangkun_count"] = 0
        else:
            env.identity["next_concubine_time"] = NOW + 40000
        assert not kwargs["operation_check"]()
        monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args, **_kwargs: {
            "status": "unsent", "code": "operation_cancelled", "at": NOW + .1,
        })
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unsent"
    if change == "schedule":
        assert env.identity["next_concubine_time"] == NOW + 40000


@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_uncertain_fragment_query_is_retained_through_restart(env, mode):
    env.send.return_value = None
    if mode != "none":
        env.send.side_effect = RuntimeError("fixture") if mode == "exception" else asyncio.CancelledError()
        with pytest.raises(RuntimeError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(start(env))
    else:
        assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unknown"
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 60)
        assert env.identity == before
        asyncio.run(concubine.run_concubine_scheduler(NOW + 60))
    assert env.identity[FIELD]["status"] == "unknown"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_fragment_intent_save_failure_does_not_send(env, mode):
    if mode == "false":
        env.save.return_value = False
        assert not asyncio.run(start(env))
    else:
        env.save.side_effect = OSError("fixture")
        with pytest.raises(OSError):
            asyncio.run(start(env))
    env.send.assert_not_awaited()


def test_fragment_early_reply_wins_over_transport_completion(env):
    async def sent(*_args, **_kwargs):
        receipt(env)
        assert await reply(env)
        assert env.identity[FIELD]["status"] == "complete"
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    assert env.identity["concubine_phase"] == "puzzle_ready"
    assert env.identity["concubine_fragment_confirmed_at"] == NOW + 1
    assert not env.identity["pending_tasks"]
    assert env.identity["concubine_fragment_msg_id"] == 0


@pytest.mark.parametrize("anchor", [0, ROOT])
def test_legacy_fragment_pending_is_not_reset_or_given_a_new_owner(env, anchor):
    env.identity.update(concubine_phase="fragment_pending", concubine_fragment_msg_id=anchor)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert "\u7f3a\u5c11\u5f52\u5c5e\u8bb0\u5f55" in concubine.get_concubine_status_text()
        concubine.restore_concubine_runtime(NOW + 1000)
        asyncio.run(concubine.run_concubine_scheduler(NOW + 1000))
    assert env.identity == before
    env.send.assert_not_awaited()


async def deliver(env, route, *, context=None, text=None, root=ROOT, chat=CHAT, at=NOW + 1, event_type="message"):
    event = SimpleNamespace(id=root + 1, chat_id=chat, sender_id=BOT, server_event_at=at)
    metadata = dict(family="concubine_fragment", send_as_id=ID, chat_id=chat,
                    root_msg_id=root, reply_to_msg_id=root, reply_to_command=concubine.CMD_CONCUBINE_FRAGMENT)
    if context is not None:
        metadata = context
    text = panel() if text is None else text
    if route == "native":
        return await app._handle_routed_reply_event(
            event, text, max(env.clock[0], at) + 2,
            SimpleNamespace(id=root, chat_id=chat, raw_text=concubine.CMD_CONCUBINE_FRAGMENT),
            metadata, event_kind=event_type,
        )
    return await passive_inbox.handle_passive_module_card(
        text, now=max(env.clock[0], at) + 2, reply_context=metadata, event=event, event_type=event_type,
    )


def test_fragment_inflight_query_excludes_all_other_concubine_reads(env):
    async def sent(*_args, **_kwargs):
        assert not await start(env)
        with state_module.use_identity(ID):
            assert not await concubine._send_status_command(NOW)
            assert not await concubine._send_gift_status_command(NOW)
            await concubine.run_concubine_scheduler(NOW + 1000)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ["native", "passive"])
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_fragment_completion_save_failure_keeps_pending_and_can_replay(env, route, mode):
    assert asyncio.run(start(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    if mode == "false":
        env.save.return_value = False
        assert not asyncio.run(deliver(env, route))
    else:
        env.save.side_effect = OSError("fixture")
        with pytest.raises(OSError):
            asyncio.run(deliver(env, route))
    assert env.identity == before
    env.audit.assert_not_awaited()
    env.save.side_effect, env.save.return_value = None, True
    assert asyncio.run(deliver(env, route))
    assert env.identity[FIELD]["status"] == "complete"
    assert not env.identity["pending_tasks"]
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ["native", "passive"])
@pytest.mark.parametrize("replacement", [False, True])
def test_fragment_completion_cleanup_is_exact_and_duplicate_safe(env, route, replacement):
    assert asyncio.run(start(env))
    receipt(env)
    pending = env.identity["pending_tasks"]
    pending[(CHAT - 1, ROOT)] = dict(pending[(CHAT, ROOT)], chat_id=CHAT - 1)
    pending[(CHAT, ROOT + 8)] = dict(pending[(CHAT, ROOT)], message_id=ROOT + 8, op_id="sibling")
    if replacement:
        pending[(CHAT, ROOT)]["op_id"] = "replacement"
    expected = {key: copy.deepcopy(value) for key, value in pending.items() if replacement or key != (CHAT, ROOT)}
    assert asyncio.run(deliver(env, route))
    assert env.identity["pending_tasks"] == expected
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env, route))
    assert env.identity == before


@pytest.mark.parametrize("change", ["pause", "module", "identity", "schedule", "partner", "snapshot", "progress", "phase"])
def test_late_fragment_panel_cannot_override_changed_business_plan(env, change):
    assert asyncio.run(start(env))
    if change == "pause":
        state_module.set_global_enabled(False)
    elif change == "module":
        env.identity["concubine_enabled"] = False
    elif change == "identity":
        state_module.set_identity_enabled(ID, False)
    elif change == "schedule":
        env.identity["next_concubine_time"] = NOW + 50000
    elif change == "partner":
        env.identity["concubine_name"] = "replacement"
    elif change == "snapshot":
        env.identity["concubine_last_snapshot_at"] = NOW + 10
    elif change == "progress":
        env.identity["concubine_fragment_xutian_count"] = 1
    else:
        env.identity["concubine_phase"] = "puzzle_pending"
        env.identity["concubine_puzzle_msg_id"] = ROOT + 9
    retained = {key: copy.deepcopy(value) for key, value in env.identity.items()
                if key.startswith("concubine_") or key == "next_concubine_time"}
    assert asyncio.run(reply(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert not env.identity["concubine_fragment_confirm_key"]
    for key, value in retained.items():
        if key not in {FIELD, "concubine_phase", "concubine_fragment_msg_id"}:
            assert env.identity[key] == value
    if change == "phase":
        assert env.identity["concubine_phase"] == "puzzle_pending"


@pytest.mark.parametrize("kwargs", [
    {"root": ROOT + 2}, {"root": 0}, {"root": str(ROOT)},
    {"current_chat_id": CHAT - 1}, {"current_chat_id": 0},
    {"current_msg_id": ROOT}, {"current_msg_id": str(ROOT + 1)},
    {"observed_at": NOW - 2}, {"observed_at": NOW + 3}, {"observed_at": True},
    {"observed_at": 0}, {"observed_at": float("nan")}, {"observed_at": float("inf")},
    {"reply_context": {}}, {"reply_context": {"sender_id": BOT + 1}},
    {"reply_context": {"sender_id": BOT, "account_id": ACCOUNT + 1}},
    {"reply_context": {"sender_id": BOT, "send_as_id": ID + 1}},
    {"reply_context": {"sender_id": BOT, "root_msg_id": ROOT + 1}},
    {"reply_context": {"sender_id": BOT, "reply_to_msg_id": ROOT + 1}},
    {"reply_context": {"sender_id": BOT, "chat_id": CHAT - 1}},
    {"reply_context": {"sender_id": BOT, "reply_to_command_edited": True}},
    {"reply_context": {"sender_id": BOT, "reply_to_command": concubine.CMD_CONCUBINE_PUZZLE}},
])
def test_fragment_reply_requires_owned_official_server_timed_evidence(env, kwargs):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, **kwargs))
    assert env.identity == before


@pytest.mark.parametrize("field,value", [
    ("id", 0), ("id", True), ("chat_id", CHAT - 1), ("chat_id", 0),
    ("sent_at", 0), ("sent_at", "1700000500"), ("sent_at", NOW - 1),
    ("send_started_at", 0), ("send_started_at", NOW - 2), ("send_started_at", NOW + 1),
])
def test_malformed_fragment_transport_receipts_remain_unknown(env, field, value):
    setattr(env.send.return_value, field, value)
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity[FIELD]["msg_id"] == 0
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ["native", "passive"])
def test_fragment_result_can_arrive_after_disable_without_new_sends(env, route):
    assert asyncio.run(start(env))
    env.identity["concubine_enabled"] = False
    state_module.set_global_enabled(False)
    assert asyncio.run(deliver(env, route))
    assert env.identity[FIELD]["status"] == "complete"
    assert not env.identity["concubine_fragment_confirm_key"]
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(NOW + 1000))
    env.send.assert_awaited_once()


def test_fragment_native_reply_cannot_be_consumed_as_status(env):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine.handle_concubine_status_reply(
            "\u4f60\u5f53\u524d\u6ca1\u6709\u4f8d\u59be\u3002", NOW + 2,
            SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_STATUS),
            matched_family="concubine_status", current_msg_id=ROOT + 1,
            current_chat_id=CHAT, observed_at=NOW + 1, reply_context={"sender_id": BOT},
        ))
    assert env.identity == before


def test_fragment_dispatcher_rejects_missing_sender_without_raising(env):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(app._handle_routed_reply_event(
        SimpleNamespace(id=ROOT + 1, chat_id=CHAT, server_event_at=NOW + 1), panel(), NOW + 2,
        SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_FRAGMENT),
        {"family": "concubine_fragment", "send_as_id": ID, "root_msg_id": ROOT, "reply_to_msg_id": ROOT},
    ))
    assert env.identity == before


@pytest.mark.parametrize("explicit", [False, True])
def test_passive_fragment_uses_owned_chat_when_roots_collide(env, explicit):
    assert asyncio.run(start(env))
    state_module.set_identity_account(ID + 1, ACCOUNT + 1)
    other = state_module.get_identity_state(ID + 1)
    other.update(copy.deepcopy(env.identity))
    other[FIELD].update(identity_id=ID + 1, account_id=ACCOUNT + 1, chat_id=CHAT - 1)
    other_before = copy.deepcopy(other)
    context = {"family": "concubine_fragment", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    if explicit:
        context["send_as_id"] = ID
    assert asyncio.run(deliver(env, "passive", context=context))
    assert other == other_before
    assert env.identity[FIELD]["status"] == "complete"


@pytest.mark.parametrize("status", ["sent", "unknown"])
def test_fragment_query_and_exact_receipt_survive_sqlite_reload(env, status):
    if status == "unknown":
        env.send.return_value = None
    asyncio.run(start(env))
    receipt(env)
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity[FIELD]["status"] == status
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 60)
    assert asyncio.run(reply(env))
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_expiring_fragment_read_rolls_back_if_completion_cannot_save(env, mode):
    assert asyncio.run(start(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        if mode == "false":
            env.save.return_value = False
            assert not concubine._expire_status_query(concubine._status_query_record(), concubine._status_query_owner(), NOW + 1000)
        else:
            env.save.side_effect = OSError("fixture")
            with pytest.raises(OSError):
                concubine._expire_status_query(concubine._status_query_record(), concubine._status_query_owner(), NOW + 1000)
    assert env.identity == before


def test_fragment_read_expiry_keeps_quantities_and_retries_only_after_read_backoff(env):
    assert asyncio.run(start(env))
    receipt(env)
    due = env.identity["concubine_dream_due_at"]
    env.clock[0] = NOW + 1000
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
        assert concubine._get_fragment_progress("xutian") == (4, 4)
        assert concubine._get_fragment_progress("cangkun") == (4, 4)
    assert env.identity[FIELD]["status"] == "expired"
    assert env.identity["concubine_dream_due_at"] == due
    assert not env.identity["pending_tasks"]
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()
    env.clock[0] = env.identity["next_concubine_time"]
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["msg_id"] == ROOT + 10


def test_stale_fragment_panel_finishes_read_without_admitting_puzzle(env):
    assert asyncio.run(start(env))
    env.clock[0] = NOW + 7200
    assert asyncio.run(reply(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert not env.identity["concubine_fragment_confirm_key"]
    assert env.identity["concubine_phase"] == "idle"
    env.audit.assert_not_awaited()


@pytest.mark.parametrize("age", [0, concubine.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC, concubine.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC + 1, -1])
def test_fragment_confirmation_has_a_bounded_server_clock(env, age):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    env.clock[0] = NOW + 1 + age
    with state_module.use_identity(ID):
        assert concubine._is_current_fragment_confirmed() is (0 <= age <= concubine.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC)


def test_expired_confirmation_requests_fragments_instead_of_spending(env):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    env.clock[0] = NOW + 1 + concubine.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC + 1
    env.send.reset_mock()
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine._send_puzzle_command(env.clock[0]))
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    env.send.assert_awaited_once()
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_FRAGMENT


@pytest.mark.parametrize("change", ["account", "partner", "missing_query", "wrong_query", "missing_root", "clock", "quantities"])
def test_fragment_confirmation_remains_bound_to_its_completed_read(env, change):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    if change == "account":
        state_module.set_identity_account(ID, ACCOUNT + 1)
    elif change == "partner":
        env.identity["concubine_name"] = "replacement"
    elif change == "missing_query":
        env.identity[FIELD] = {}
    elif change == "wrong_query":
        env.identity[FIELD]["kind"] = "status"
    elif change == "missing_root":
        env.identity[FIELD]["msg_id"] = 0
    elif change == "clock":
        env.identity["concubine_fragment_confirmed_at"] = NOW + 2
    else:
        env.identity["concubine_fragment_xutian_count"] = env.identity["concubine_fragment_count"] = 4
        env.identity["concubine_fragment_confirm_key"] = "xutian:4/4|cangkun:4/4"
    with state_module.use_identity(ID):
        assert not concubine._is_current_fragment_confirmed(NOW + 3)
        assert not asyncio.run(concubine._send_puzzle_command(NOW + 3))
    env.send.assert_awaited_once()


def test_legacy_confirmation_flags_alone_cannot_authorize_puzzle(env):
    with state_module.use_identity(ID):
        concubine._mark_fragment_confirmation(NOW)
        assert not concubine._is_current_fragment_confirmed(NOW)
        assert not asyncio.run(concubine._send_puzzle_command(NOW))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("name", [NAME, "\u5357\u5bab\u5a49"])
def test_fragment_no_partner_result_requires_status_read_and_keeps_quantities(env, name):
    env.identity["concubine_name"] = name
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, "\u4f60\u5f53\u524d\u6ca1\u6709\u4f8d\u59be\u3002"))
    assert env.identity[FIELD]["outcome"] == "no_partner"
    assert env.identity["concubine_availability"] == "unknown"
    assert env.identity["concubine_name"] == name
    assert env.identity["concubine_fragment_xutian_count"] == 4
    assert env.identity["concubine_fragment_cangkun_count"] == 4
    env.clock[0] = env.identity["next_concubine_time"]
    env.send.reset_mock()
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    env.send.assert_awaited_once()
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS


@pytest.mark.parametrize("text", [
    panel() + "\n\u4f60\u5f53\u524d\u6ca1\u6709\u4f8d\u59be\u3002",
    panel() + "\n\u6df1\u5ea6\u95ed\u5173\u603b\u7ed3",
    "\u6df1\u5ea6\u95ed\u5173\u603b\u7ed3\n\u4f60\u5f53\u524d\u6ca1\u6709\u4f8d\u59be\u3002",
])
def test_conflicting_fragment_results_cannot_complete_read(env, text):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text))
    assert env.identity == before


@pytest.mark.parametrize("fault", ["error", "delete", "cancel"])
def test_fragment_notification_cannot_undo_a_committed_result(env, fault):
    assert asyncio.run(start(env))

    async def notify(*_args, **kwargs):
        assert kwargs["send_as_id"] == ID
        assert env.identity[FIELD]["status"] == "complete"
        if fault == "delete":
            state_module.remove_identity(ID)
        elif fault == "error":
            raise OSError("fixture")
        else:
            raise asyncio.CancelledError()

    env.audit.side_effect = notify
    if fault == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(reply(env))
    else:
        assert asyncio.run(reply(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_fragment_confirm_key"] == "cangkun:4/4"


def finalize(env, *, log=False):
    return runtime._finalize_game_command_sent(
        concubine.CMD_CONCUBINE_FRAGMENT, msg_id=ROOT, sent_at=NOW + .5, send_started_at=NOW,
        send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0,
        append_sent_log=log, reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC,
        send_intent={"op_id": env.identity[FIELD]["op_id"], "source_module": concubine.CONCUBINE_QUERY_SOURCE},
    )


def logged_reply(**changes):
    return dict(
        event_type="message", chat_id=CHAT, message_id=ROOT + 1,
        reply_to_msg_id=ROOT, sender_is_bot=True, sender_id=BOT,
        server_event_at=NOW + 1, text=panel(),
    ) | changes


def tick(env, at):
    env.clock[0] = at
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(at))


@pytest.mark.parametrize("route", ["native", "passive"])
def test_fragment_early_reply_accepts_real_receipt_without_account_column(env, route):
    async def sent(*_args, **_kwargs):
        message = finalize(env)
        assert "account_id" not in env.identity["pending_tasks"][(CHAT, ROOT)]
        assert await deliver(env, route)
        finalize(env)
        return message

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


def test_fragment_native_sent_log_recovers_unknown_read_after_sqlite_reload(env, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(message_log_recovery, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", message_log_recovery.find_recent_message_log_commands)
    monkeypatch.setattr(concubine, "find_message_log_replies", message_log_recovery.find_message_log_replies)
    env.send.return_value = None
    assert not asyncio.run(start(env))
    with state_module.use_identity(ID):
        finalize(env, log=True)
    env.identity["pending_tasks"] = {}
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    path = tmp_path / (datetime.fromtimestamp(NOW, concubine.TZ_LOCAL).strftime("%Y-%m-%d") + ".log")
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert "account_id" not in entries[-1]
    with path.open("a") as log:
        log.write(json.dumps(logged_reply(ts=datetime.fromtimestamp(NOW + 1, concubine.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"))) + "\n")
    tick(env, NOW + 61)
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_fragment_confirm_key"] == "cangkun:4/4"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("field,value", [
    ("op_id", "foreign"), ("source_module", "foreign"), ("account_id", ACCOUNT + 1),
    ("cmd", concubine.CMD_CONCUBINE_PUZZLE), ("chat_id", CHAT - 1),
    ("send_started_at", NOW - 10), ("sent_at", NOW + 20), ("message_id", ROOT + 1),
])
def test_fragment_unknown_cannot_adopt_a_foreign_or_inconsistent_receipt(env, field, value):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    receipt(env)
    env.identity["pending_tasks"][(CHAT, ROOT)][field] = value
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before


def test_fragment_unknown_receipt_ambiguity_never_selects_a_root(env):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    receipt(env)
    receipt(env, root=ROOT + 5)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before


@pytest.mark.parametrize("field,value", [
    ("sender_is_bot", False), ("sender_id", BOT + 1), ("chat_id", CHAT - 1),
    ("reply_to_msg_id", ROOT + 1), ("message_id", ROOT), ("event_type", "sent"),
    ("server_event_at", None), ("server_event_at", NOW - 10), ("server_event_at", NOW + 5000),
])
def test_fragment_log_recovery_rejects_unowned_untrusted_or_untimed_replies(env, monkeypatch, field, value):
    assert asyncio.run(start(env))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply(**{field: value})]))
    tick(env, NOW + 61)
    assert env.identity[FIELD]["status"] == "sent"
    assert not env.identity["concubine_fragment_confirm_key"]
    env.send.assert_awaited_once()


@pytest.mark.parametrize("conflict", [False, True])
def test_fragment_newer_or_conflicting_log_revision_cannot_reveal_old_completion(env, monkeypatch, conflict):
    assert asyncio.run(start(env))
    newest = logged_reply(text="pending", event_type="edit", server_event_at=NOW + (1 if conflict else 2))
    lookup = Mock(return_value=[logged_reply(), newest])
    monkeypatch.setattr(concubine, "find_message_log_replies", lookup)
    tick(env, NOW + 61)
    assert env.identity[FIELD]["status"] == "sent"
    assert not env.identity["concubine_fragment_confirm_key"]
    lookup.return_value += [logged_reply(event_type="edit", server_event_at=NOW + 3)]
    tick(env, NOW + 122)
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity[FIELD]["reply_at"] == NOW + 3


@pytest.mark.parametrize("field,value", [
    ("kind", "dream"), ("account_id", 0), ("identity_id", ID + 1), ("partner", ""),
    ("partner", []), ("started_at", False), ("status", []), ("msg_id", "86001"),
    ("command", concubine.CMD_CONCUBINE_STATUS), ("plan_key", "foreign"),
])
def test_malformed_fragment_query_cannot_send_complete_or_reset(env, field, value):
    assert asyncio.run(start(env))
    env.identity[FIELD][field] = value
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1000)
    tick(env, NOW + 1000)
    assert env.identity == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("text,outcome", [
    ("\u6df1\u5ea6\u95ed\u5173\u603b\u7ed3", "summary"),
    ("\u4f8d\u59be\u6b63\u5728\u8fdc\u822a\u9014\u4e2d\uff0c\u8fd8\u9700 30\u5206\u949f\u540e\u5f52\u6765\u3002", "voyage_lock"),
])
def test_fragment_negative_read_result_never_certifies_counts_or_dream_cd(env, text, outcome):
    assert asyncio.run(start(env))
    due = env.identity["concubine_dream_due_at"]
    assert asyncio.run(reply(env, text))
    assert env.identity[FIELD]["outcome"] == outcome
    assert not env.identity["concubine_fragment_confirm_key"]
    assert env.identity["concubine_fragment_xutian_count"] == 4
    assert env.identity["concubine_fragment_cangkun_count"] == 4
    assert env.identity["concubine_dream_due_at"] == due
    if outcome == "voyage_lock":
        assert env.identity["concubine_voyage_return_at"] == NOW + 1 + 1800 + concubine.CD_BUFFER_SEC
