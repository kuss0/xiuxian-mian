import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, app_runtime, message_log_recovery, persistence, runtime, state as state_module
from model.features import concubine, passive_inbox
from tests.test_concubine_fragment_contract import panel as fragment_panel


ID, ACCOUNT, CHAT, BOT, ROOT = 99082001, 8201, -100820001, 88082001, 82001
NOW = 1_700_000_500.0
KINDS = ("status", "gift_status")
KEYS = {"status": "concubine_status_msg_id", "gift_status": "concubine_gift_status_msg_id"}
PANEL = (
    "\u4f60\u7684\u9053\u5fc3\u4f8d\u59be: \u3010\u51cc\u7389\u7075\u3011 (\u72b6\u6001: \u968f\u884c\u4e2d)\n"
    "\u60c5\u7f18\u503c: 184\n\u5165\u68a6\u5bfb\u56fe\u51b7\u5374: 2\u5c0f\u65f6\n"
    "\u5929\u673a\u4ee3\u535c\u51b7\u5374: 3\u5c0f\u65f6\n\u5171\u5386\u5fc3\u52ab\u51b7\u5374: 4\u5c0f\u65f6"
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    before = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_game_group_id(CHAT)
    state_module.set_game_bot_ids([BOT])
    state_module.set_identity_account(ID, ACCOUNT)
    state_module.update_send_as_profile(ID, username="query_owner", sect_name="\u661f\u5bab")
    identity = state_module.get_identity_state(ID)
    identity.update(
        concubine_enabled=True, concubine_tianji_enabled=True,
        concubine_phase="idle", concubine_availability="available",
        concubine_name="\u51cc\u7389\u7075", concubine_kind="\u9053\u5fc3\u4f8d\u59be",
        concubine_affinity=100, concubine_last_snapshot_at=NOW - 7200,
        concubine_last_greet_day=concubine._local_day_key(NOW),
    )
    clock = [NOW]
    send = AsyncMock(return_value=SimpleNamespace(
        id=ROOT, chat_id=CHAT, sent_at=NOW + .5, send_started_at=NOW,
    ))
    save, audit, gift = Mock(return_value=True), AsyncMock(), AsyncMock(return_value=True)
    monkeypatch.setattr(concubine, "_CONCUBINE_QUERY_INFLIGHT", {}, raising=False)
    monkeypatch.setattr(concubine, "send_game_command", send)
    monkeypatch.setattr(concubine, "save_state", save)
    monkeypatch.setattr(concubine, "mark_dirty", Mock())
    monkeypatch.setattr(concubine, "send_audit_log", audit)
    monkeypatch.setattr(concubine, "console_log", Mock())
    monkeypatch.setattr(concubine, "_record_concubine_ignored_reply", Mock())
    monkeypatch.setattr(concubine, "_send_gift_bag_command", gift)
    monkeypatch.setattr(concubine.time, "time", lambda: clock[0])
    monkeypatch.setattr(concubine.random, "uniform", lambda *_args: 1800)
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args, **_kwargs: {"status": "none"}, raising=False)
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", Mock(return_value=[]), raising=False)
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[]), raising=False)
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "query.db"))
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
        yield SimpleNamespace(identity=identity, clock=clock, send=send, save=save, audit=audit, gift=gift)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(before)


async def send_query(kind):
    with state_module.use_identity(ID):
        return await getattr(concubine, f"_send_{kind}_command")(concubine.time.time())


def receipt(env):
    record = env.identity["concubine_status_query"]
    env.identity["pending_tasks"][(CHAT, ROOT)] = {
        "cmd": record["command"],
        "family": "concubine_fragment" if record["kind"] == "fragment" else "concubine_status",
        "op_id": record["op_id"], "source_module": "concubine_status",
        "account_id": ACCOUNT, "chat_id": CHAT, "message_id": ROOT,
        "sent_at": NOW + .5, "send_started_at": NOW,
    }


async def reply(env, *, text=PANEL, **changes):
    kwargs = dict(
        matched_family="concubine_status", current_msg_id=ROOT + 1,
        current_chat_id=CHAT, observed_at=NOW + 1,
    )
    kwargs.update(changes)
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_status_reply(
            text, env.clock[0] + 2,
            SimpleNamespace(id=ROOT, raw_text=concubine.CMD_CONCUBINE_STATUS, chat_id=CHAT),
            **kwargs,
        )


@pytest.mark.parametrize("kind", KINDS)
def test_query_persists_owner_before_dispatch_and_has_no_transport_retry(env, kind):
    async def sent(_command, **kwargs):
        record = env.identity.get("concubine_status_query", {})
        assert record["status"] == "sending"
        assert record["kind"] == kind
        assert record["account_id"] == ACCOUNT
        assert record["chat_id"] == CHAT
        assert kwargs["send_as_id"] == ID
        assert kwargs["target_chat_id"] == CHAT
        assert kwargs["op_id"] == record["op_id"]
        assert kwargs["max_retry"] == 0
        assert kwargs["operation_check"]()
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(send_query(kind))
    assert env.identity["concubine_status_query"]["status"] == "sent"
    assert env.identity[KEYS[kind]] == ROOT


@pytest.mark.parametrize("kind", KINDS)
def test_early_reply_wins_over_delayed_send_receipt(env, kind):
    async def sent(_command, **_kwargs):
        receipt(env)
        assert await reply(env)
        assert env.identity["concubine_status_query"]["status"] == "complete"
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(send_query(kind))
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity[KEYS[kind]] == 0
    assert env.identity["concubine_affinity"] == 184
    assert env.gift.await_count == (kind == "gift_status")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_late_query_receipt_cannot_write_a_replacement_owner(env, kind, change):
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
    assert not asyncio.run(send_query(kind))
    assert state_module._meta_state == after


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["pause", "disable", "module", "schedule", "partner", "chat"])
def test_queue_rechecks_controls_and_query_business_plan(env, kind, change, monkeypatch):
    async def sent(_command, **kwargs):
        if change == "pause":
            state_module.set_global_enabled(False)
        elif change == "disable":
            state_module.set_identity_enabled(ID, False)
        elif change == "module":
            env.identity["concubine_enabled"] = env.identity["concubine_tianji_enabled"] = False
        elif change == "schedule":
            env.identity["next_concubine_time"] = NOW + 40000
        elif change == "partner":
            env.identity["concubine_name"] = "replacement"
        else:
            state_module.set_game_group_id(CHAT - 1)
        assert not kwargs["operation_check"]()
        monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args, **_kwargs: {
            "status": "unsent", "code": "operation_cancelled", "at": NOW + .1,
        }, raising=False)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(send_query(kind))
    assert env.identity["concubine_status_query"]["status"] == "unsent"
    if change == "schedule":
        assert env.identity["next_concubine_time"] == NOW + 40000
    if change == "partner":
        assert env.identity["concubine_name"] == "replacement"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_uncertain_query_survives_restore_and_has_no_immediate_resend(env, kind, mode):
    env.send.return_value = None
    if mode == "exception":
        env.send.side_effect = RuntimeError("fixture")
    elif mode == "cancel":
        env.send.side_effect = asyncio.CancelledError()
    if mode == "none":
        asyncio.run(send_query(kind))
    else:
        with pytest.raises(RuntimeError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(send_query(kind))
    assert env.identity["concubine_status_query"]["status"] == "unknown"
    before = copy.deepcopy(env.identity["concubine_status_query"])
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 10)
    assert env.identity["concubine_status_query"] == before
    assert env.identity["concubine_phase"] == kind + "_pending"
    assert not asyncio.run(send_query(kind))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_query_persistence_and_original_receipt_close_after_reload(env, kind):
    env.send.return_value = None
    asyncio.run(send_query(kind))
    assert persistence.save_state()
    before = copy.deepcopy(env.identity["concubine_status_query"])
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity["concubine_status_query"] == before
    receipt(env)
    assert asyncio.run(reply(env))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert persistence.save_state()
    assert persistence.load_state()
    assert state_module.get_identity_state(ID)["concubine_status_query"]["status"] == "complete"


@pytest.mark.parametrize("kind", KINDS)
def test_incomplete_query_reply_does_not_close_the_request(env, kind):
    asyncio.run(send_query(kind))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text=PANEL.splitlines()[0]))
    assert env.identity == before
    assert asyncio.run(reply(env))
    assert env.identity["concubine_status_query"]["status"] == "complete"


@pytest.mark.parametrize("kind", KINDS)
def test_duplicate_complete_query_cannot_repeat_followups(env, kind):
    asyncio.run(send_query(kind))
    assert asyncio.run(reply(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before
    assert env.gift.await_count == (kind == "gift_status")


@pytest.mark.parametrize("kind", KINDS)
def test_query_cannot_cross_chats_with_the_same_message_id(env, kind):
    asyncio.run(send_query(kind))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, current_chat_id=CHAT - 1))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
def test_unsaved_query_never_dispatches(env, kind):
    env.save.return_value = False
    assert not asyncio.run(send_query(kind))
    env.send.assert_not_awaited()


def finalize(env, *, log=False):
    record = env.identity["concubine_status_query"]
    return runtime._finalize_game_command_sent(
        concubine.CMD_CONCUBINE_STATUS, msg_id=ROOT, sent_at=NOW + .5, send_started_at=NOW,
        send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0,
        append_sent_log=log, reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC,
        send_intent={"op_id": record["op_id"], "source_module": concubine.CONCUBINE_QUERY_SOURCE},
    )


def logged_send(env, **changes):
    record = env.identity["concubine_status_query"]
    return {
        "event_type": "sent", "chat_id": CHAT, "message_id": ROOT, "sender_id": ID,
        "op_id": record["op_id"], "source_module": concubine.CONCUBINE_QUERY_SOURCE,
        "ts_epoch": NOW + .5, "text": concubine.CMD_CONCUBINE_STATUS, **changes,
    }


def logged_reply(**changes):
    return {
        "event_type": "message", "chat_id": CHAT, "message_id": ROOT + 1,
        "reply_to_msg_id": ROOT, "sender_is_bot": True, "sender_id": BOT,
        "server_event_at": NOW + 1, "text": PANEL, **changes,
    }


def tick(env, at):
    env.clock[0] = at
    with state_module.use_identity(ID):
        return asyncio.run(concubine.run_concubine_scheduler(at))


@pytest.mark.parametrize("kind", KINDS)
def test_early_reply_uses_real_runtime_pending_without_account_column(env, kind):
    async def sent(*_args, **_kwargs):
        message = finalize(env)
        assert "account_id" not in env.identity["pending_tasks"][(CHAT, ROOT)]
        assert await reply(env)
        assert (CHAT, ROOT) not in env.identity["pending_tasks"]
        # The shared layer may re-register a delayed original receipt.
        finalize(env)
        return message

    env.send.side_effect = sent
    assert asyncio.run(send_query(kind))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["native", "passive"])
def test_real_dispatch_paths_finish_once_without_scalar_anchor(env, kind, route):
    env.send.return_value = None
    asyncio.run(send_query(kind))
    finalize(env)
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=NOW + 1)
    context = {"family": "concubine_status", "send_as_id": ID, "chat_id": CHAT,
               "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    parent = SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_STATUS)
    if route == "native":
        handled = asyncio.run(app._handle_routed_reply_event(event, PANEL, NOW + 2, parent, context))
    else:
        handled = asyncio.run(passive_inbox.handle_passive_module_card(
            PANEL, now=NOW + 2, reply_context=context, event=event, event_type="message"))
    assert handled
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 184
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    completed = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == completed
    assert env.gift.await_count == (kind == "gift_status")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["pause", "disable", "module", "schedule", "partner", "phase", "chat"])
def test_delayed_read_closes_itself_without_overwriting_new_business(env, kind, change):
    asyncio.run(send_query(kind))
    receipt(env)
    if change == "pause":
        state_module.set_global_enabled(False)
    elif change == "disable":
        state_module.set_identity_enabled(ID, False)
    elif change == "module":
        env.identity["concubine_tianji_enabled"] = env.identity["concubine_enabled"] = False
    elif change == "schedule":
        env.identity["next_concubine_time"] = NOW + 40000
    elif change == "partner":
        env.identity["concubine_name"] = "replacement"
    elif change == "phase":
        env.identity["concubine_phase"] = "heart_pending"
        env.identity["concubine_heart_msg_id"] = ROOT + 9
    else:
        state_module.set_game_group_id(CHAT - 1)
    before = copy.deepcopy(env.identity)
    assert asyncio.run(reply(env))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    for key in ("concubine_name", "concubine_affinity", "next_concubine_time", "concubine_heart_msg_id"):
        assert env.identity[key] == before[key]
    if change == "phase":
        assert env.identity["concubine_phase"] == "heart_pending"
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [
    ("op_id", "foreign"), ("source_module", "foreign"), ("cmd", ".foreign"),
    ("account_id", ACCOUNT + 1), ("send_started_at", NOW - 100), ("sent_at", NOW + 100),
])
def test_unknown_read_rejects_a_foreign_pending_receipt(env, kind, field, value):
    env.send.return_value = None
    asyncio.run(send_query(kind))
    receipt(env)
    env.identity["pending_tasks"][(CHAT, ROOT)][field] = value
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
def test_ambiguous_operation_roots_are_not_adopted(env, kind):
    env.send.return_value = None
    asyncio.run(send_query(kind))
    receipt(env)
    env.identity["pending_tasks"][(CHAT, ROOT + 2)] = dict(
        env.identity["pending_tasks"][(CHAT, ROOT)], message_id=ROOT + 2)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
def test_same_root_replacement_pending_and_other_chat_survive_cleanup(env, kind):
    asyncio.run(send_query(kind))
    receipt(env)
    env.identity["pending_tasks"][(CHAT, ROOT)]["op_id"] = "newer"
    env.identity["pending_tasks"][(CHAT - 1, ROOT)] = dict(env.identity["pending_tasks"][(CHAT, ROOT)], chat_id=CHAT - 1)
    before = copy.deepcopy(env.identity["pending_tasks"])
    assert asyncio.run(reply(env))
    assert env.identity["pending_tasks"] == before


@pytest.mark.parametrize("kind", KINDS)
def test_owned_logged_read_recovers_before_timeout_and_does_not_repeat(env, monkeypatch, kind):
    env.send.return_value = None
    asyncio.run(send_query(kind))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", Mock(return_value=[logged_send(env)]))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply()]))
    tick(env, NOW + 61)
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["concubine_last_snapshot_at"] == NOW + 1
    assert env.gift.await_count == (kind == "gift_status")
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [
    ("event_type", "sent"), ("sender_id", BOT + 1), ("sender_is_bot", False),
    ("chat_id", CHAT - 1), ("reply_to_msg_id", ROOT + 1), ("message_id", 0),
    ("server_event_at", 0), ("server_event_at", NOW - 10), ("server_event_at", NOW + 300),
])
def test_untrusted_logged_reply_cannot_close_read(env, monkeypatch, kind, field, value):
    asyncio.run(send_query(kind))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply(**{field: value})]))
    tick(env, NOW + 61)
    assert env.identity["concubine_status_query"]["status"] == "sent"
    assert env.identity["concubine_affinity"] == 100
    env.gift.assert_not_awaited()
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_latest_incomplete_edit_does_not_reveal_older_complete_panel(env, monkeypatch, kind):
    asyncio.run(send_query(kind))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[
        logged_reply(), logged_reply(event_type="edit", server_event_at=NOW + 2, text=PANEL.splitlines()[0]),
    ]))
    tick(env, NOW + 61)
    assert env.identity["concubine_status_query"]["status"] == "sent"
    assert env.identity["concubine_affinity"] == 100
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_read_timeout_allows_scheduled_requery_but_does_not_spend_gift_day(env, kind):
    env.send.return_value = None
    asyncio.run(send_query(kind))
    tick(env, NOW + concubine.CONCUBINE_PHASE_TIMEOUT_SEC + 1)
    assert env.identity["concubine_status_query"]["status"] == "expired"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_gift_attempt_day"] == ""
    assert not asyncio.run(send_query(kind))
    env.clock[0] = env.identity["next_concubine_time"] + 1
    env.send.return_value = SimpleNamespace(id=ROOT + 5, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
    assert asyncio.run(send_query(kind))
    assert env.send.await_count == 2
    assert env.identity["concubine_status_query"]["msg_id"] == ROOT + 5


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [
    ("kind", []), ("status", []), ("msg_id", True), ("msg_id", 1.5),
    ("account_id", True), ("chat_id", 0), ("op_id", ""), ("plan_key", "wrong"),
    ("started_at", float("inf")), ("started_at", True), ("started_at", str(NOW)),
    ("dispatch_at", NOW - 10), ("reply_at", "bad"), ("replay_after", -1),
])
def test_malformed_query_is_retained_without_dispatch_or_completion(env, kind, field, value):
    asyncio.run(send_query(kind))
    env.identity["concubine_status_query"][field] = value
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    tick(env, NOW + 4000)
    assert env.identity == before
    env.send.assert_awaited_once()


def test_real_sent_log_without_account_column_recovers_after_reload(env, monkeypatch, tmp_path):
    import json
    from datetime import datetime

    monkeypatch.setattr(runtime, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(message_log_recovery, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", message_log_recovery.find_recent_message_log_commands)
    monkeypatch.setattr(concubine, "find_message_log_replies", message_log_recovery.find_message_log_replies)
    env.send.return_value = None
    asyncio.run(send_query("status"))
    with state_module.use_identity(ID):
        finalize(env, log=True)
    env.identity["pending_tasks"] = {}
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    day = datetime.fromtimestamp(NOW, concubine.TZ_LOCAL).strftime("%Y-%m-%d")
    path = tmp_path / f"{day}.log"
    logged = [json.loads(line) for line in path.read_text().splitlines()]
    assert "account_id" not in logged[-1]
    with path.open("a") as log:
        log.write(json.dumps(logged_reply(ts=datetime.fromtimestamp(NOW + 1, concubine.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"))) + "\n")
    tick(env, NOW + 61)
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 184


def test_gift_completion_is_saved_before_followup_and_failed_save_cannot_spend(env):
    asyncio.run(send_query("gift_status"))
    receipt(env)
    before = copy.deepcopy(env.identity)
    env.save.return_value = False
    assert not asyncio.run(reply(env))
    assert env.identity == before
    env.gift.assert_not_awaited()
    assert env.identity["concubine_gift_attempt_day"] == ""


@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_gift_followup_await_cannot_write_a_replacement_owner(env, change):
    asyncio.run(send_query("gift_status"))
    after = {}

    async def gift(_now):
        assert env.identity["concubine_status_query"]["status"] == "complete"
        env.save.assert_called()
        if change == "delete":
            state_module.remove_identity(ID)
        elif change == "replace":
            state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
        else:
            state_module.set_identity_account(ID, ACCOUNT + 1)
        after.update(copy.deepcopy(state_module._meta_state))

    env.gift.side_effect = gift
    assert asyncio.run(reply(env))
    assert state_module._meta_state == after


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [
    ("chat_id", CHAT - 1), ("id", 0), ("id", float(ROOT)), ("id", True),
    ("sent_at", NOW + 50), ("sent_at", str(NOW)), ("send_started_at", NOW - 50),
    ("send_started_at", str(NOW)),
])
def test_invalid_transport_receipt_is_unknown_not_sent(env, kind, field, value):
    setattr(env.send.return_value, field, value)
    assert not asyncio.run(send_query(kind))
    assert env.identity["concubine_status_query"]["status"] == "unknown"
    assert env.identity[KEYS[kind]] == 0


@pytest.mark.parametrize("kind", KINDS)
def test_unresolved_read_blocks_both_caches_even_when_ui_clears_phase(env, kind):
    asyncio.run(send_query(kind))
    env.identity["concubine_phase"] = "idle"
    env.identity[KEYS[kind]] = 0
    env.identity["concubine_last_panel_msg_id"] = ROOT + 99
    env.identity["concubine_last_snapshot_at"] = NOW
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(send_query("gift_status"))
    with state_module.use_identity(ID):
        assert not concubine.sync_concubine_miniapp_status(PANEL, NOW + 1)["handled"]
    assert not asyncio.run(passive_inbox.handle_passive_module_card(
        PANEL, now=NOW + 2, event=SimpleNamespace(id=ROOT + 101, chat_id=CHAT, sender_id=BOT, server_event_at=NOW + 1),
        reply_context={"send_as_id": ID, "family": "concubine_status", "root_msg_id": ROOT + 100,
                       "reply_to_msg_id": ROOT + 100, "reply_to_sender_id": ID,
                       "reply_to_command": concubine.CMD_CONCUBINE_STATUS, "reply_to_server_at": NOW,
                       "reply_to_command_edited": False}, event_type="message",
    ))
    assert env.identity == before
    env.send.assert_awaited_once()
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_expired_read_on_rebound_account_does_not_hold_new_owner_forever(env, kind):
    asyncio.run(send_query(kind))
    state_module.set_identity_account(ID, ACCOUNT + 1)
    env.identity["next_concubine_time"] = NOW + 5000
    tick(env, NOW + 1000)
    assert env.identity["concubine_status_query"]["status"] == "expired"
    assert env.identity["next_concubine_time"] == NOW + 5000
    assert env.identity["concubine_affinity"] == 100
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_log_recovery_throttles_without_extending_query_lifetime(env, monkeypatch, kind):
    asyncio.run(send_query(kind))
    lookup = Mock(return_value=[])
    monkeypatch.setattr(concubine, "find_message_log_replies", lookup)
    tick(env, NOW + 10)
    calls = lookup.call_count
    before = copy.deepcopy(env.identity)
    tick(env, NOW + 11)
    assert lookup.call_count == calls
    assert env.identity == before
    tick(env, NOW + 1000)
    assert env.identity["concubine_status_query"]["status"] == "expired"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_simultaneous_status_and_gift_queries_cannot_both_dispatch(env, kind):
    async def sent(*_args, **_kwargs):
        for sibling in KINDS:
            assert not await send_query(sibling)
        with state_module.use_identity(ID):
            await concubine.run_concubine_scheduler(NOW + 1000)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(send_query(kind))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_fresh_unsent_block_allows_delayed_retry_but_cached_block_does_not(env, monkeypatch, kind):
    env.send.return_value = None
    block = {"status": "unsent", "code": "send_queue_timeout", "at": NOW}
    blocks = iter(({"status": "none"}, block, block, block))
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args, **_kwargs: next(blocks))
    assert not asyncio.run(send_query(kind))
    assert env.identity["concubine_status_query"]["status"] == "unsent"
    assert not asyncio.run(send_query(kind))
    env.clock[0] = env.identity["next_concubine_time"] + 1
    assert not asyncio.run(send_query(kind))
    assert env.identity["concubine_status_query"]["status"] == "unknown"
    assert env.send.await_count == 2


@pytest.mark.parametrize("kind", KINDS)
def test_fractional_pending_key_cannot_be_truncated_to_expected_root(env, kind):
    env.send.return_value = None
    asyncio.run(send_query(kind))
    receipt(env)
    pending = env.identity["pending_tasks"].pop((CHAT, ROOT))
    env.identity["pending_tasks"][(CHAT, ROOT + .5)] = pending
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
def test_original_reply_from_before_logged_dispatch_cannot_be_used(env, monkeypatch, kind):
    env.send.return_value = None
    asyncio.run(send_query(kind))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", Mock(return_value=[logged_send(env, ts_epoch=NOW + 50)]))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply()]))
    tick(env, NOW + 61)
    assert env.identity["concubine_status_query"]["status"] == "sent"
    assert env.identity["concubine_affinity"] == 100


@pytest.mark.parametrize("kind", KINDS)
def test_conflicting_same_clock_log_revisions_do_not_close_query(env, monkeypatch, kind):
    asyncio.run(send_query(kind))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[
        logged_reply(), logged_reply(event_type="edit", text=PANEL.replace("184", "999")),
    ]))
    tick(env, NOW + 61)
    assert env.identity["concubine_status_query"]["status"] == "sent"
    assert env.identity["concubine_affinity"] == 100


@pytest.mark.parametrize("field,value", [
    ("send_as_id", ID + 1), ("account_id", ACCOUNT + 1), ("chat_id", CHAT - 1),
    ("root_msg_id", ROOT + 2), ("sender_id", BOT + 1), ("reply_to_command_edited", True),
    ("reply_to_command", ".foreign"),
])
def test_explicit_reply_context_cannot_conflict_with_owned_query(env, field, value):
    asyncio.run(send_query("status"))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, reply_context={field: value}))
    assert env.identity == before


def test_native_query_cleanup_cannot_delete_a_replaced_pending_item(env):
    asyncio.run(send_query("status"))
    receipt(env)
    env.identity["pending_tasks"][(CHAT, ROOT)]["op_id"] = "replacement"
    pending = copy.deepcopy(env.identity["pending_tasks"])
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=NOW + 1)
    context = {"family": "concubine_status", "send_as_id": ID, "chat_id": CHAT,
               "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    parent = SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_STATUS)
    assert asyncio.run(app._handle_routed_reply_event(event, PANEL, NOW + 2, parent, context))
    assert env.identity["pending_tasks"] == pending
    assert not asyncio.run(app._handle_routed_reply_event(event, PANEL, NOW + 2, parent, context, replay=True))
    assert env.identity["pending_tasks"] == pending


@pytest.mark.parametrize("kind", KINDS)
def test_late_status_after_expiry_cannot_apply_as_manual_or_passive_panel(env, kind):
    asyncio.run(send_query(kind))
    tick(env, NOW + 1000)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, observed_at=NOW + 999))
    assert not asyncio.run(passive_inbox.handle_passive_module_card(
        PANEL, now=NOW + 1000,
        reply_context={"send_as_id": ID, "family": "concubine_status", "root_msg_id": ROOT, "reply_to_msg_id": ROOT},
        event=SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=NOW + 999),
        event_type="message",
    ))
    assert env.identity == before


@pytest.mark.parametrize("route", ["native", "passive"])
def test_completed_query_does_not_disable_new_explicit_manual_status(env, route):
    asyncio.run(send_query("status"))
    assert asyncio.run(reply(env))
    env.identity["concubine_phase"] = "idle"
    event = SimpleNamespace(id=ROOT + 11, chat_id=CHAT, sender_id=BOT, server_event_at=NOW + 20)
    parent = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_STATUS)
    context = {"family": "concubine_status", "send_as_id": ID, "chat_id": CHAT,
               "root_msg_id": ROOT + 10, "reply_to_msg_id": ROOT + 10,
               "reply_to_command": concubine.CMD_CONCUBINE_STATUS, "reply_to_sender_id": ID,
               "reply_to_server_at": NOW + 19, "reply_to_command_edited": False}
    text = PANEL.replace("184", "250")
    if route == "native":
        handled = asyncio.run(app._handle_routed_reply_event(event, text, NOW + 21, parent, context))
    else:
        handled = asyncio.run(passive_inbox.handle_passive_module_card(
            text, now=NOW + 21, reply_context=context, event=event, event_type="message"))
    assert handled
    assert env.identity["concubine_affinity"] == 250
    assert env.identity["concubine_last_panel_msg_id"] == ROOT + 11
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_no_partner_result_preserves_sibling_read_pending(env, kind):
    asyncio.run(send_query(kind))
    receipt(env)
    env.identity["pending_tasks"][(CHAT, ROOT + 9)] = dict(env.identity["pending_tasks"][(CHAT, ROOT)], op_id="sibling", message_id=ROOT + 9)
    sibling = copy.deepcopy(env.identity["pending_tasks"][(CHAT, ROOT + 9)])
    assert asyncio.run(reply(env, text="\u4f60\u5f53\u524d\u6ca1\u6709\u4f8d\u59be\u3002"))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["pending_tasks"] == {(CHAT, ROOT + 9): sibling}
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_phaseful_summary_closes_only_read_without_certifying_partner(env, kind):
    asyncio.run(send_query(kind))
    receipt(env)
    seen = env.identity["concubine_last_snapshot_at"]
    assert asyncio.run(reply(env, text="\u6df1\u5ea6\u95ed\u5173\u603b\u7ed3"))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_last_snapshot_at"] == seen
    assert env.identity["concubine_affinity"] == 100
    assert env.identity["concubine_gift_attempt_day"] == ""
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    env.gift.assert_not_awaited()


def test_stale_gift_panel_may_finish_read_but_cannot_start_gift(env):
    asyncio.run(send_query("gift_status"))
    env.clock[0] = NOW + 7200
    assert asyncio.run(reply(env))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["concubine_last_snapshot_at"] == NOW + 1
    assert env.identity["concubine_gift_attempt_day"] == ""
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("record", [[], None, {"status": "unknown"}])
def test_corrupt_query_is_visible_and_cannot_fall_through_to_legacy_reset(env, record):
    env.identity["concubine_status_query"] = record
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW)
        assert "\u67e5\u8be2\u5f52\u5c5e\u8bb0\u5f55\u5f02\u5e38" in concubine.get_concubine_status_text()
    tick(env, NOW + 2000)
    assert env.identity == before
    env.send.assert_not_awaited()


def test_older_query_edit_cannot_become_manual_after_a_newer_query_completes(env):
    asyncio.run(send_query("status"))
    assert asyncio.run(reply(env))
    env.clock[0] += 3600
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
    assert asyncio.run(send_query("status"))
    with state_module.use_identity(ID):
        assert asyncio.run(concubine.handle_concubine_status_reply(
            PANEL, env.clock[0] + 1,
            SimpleNamespace(id=ROOT + 10, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_STATUS),
            matched_family="concubine_status", current_msg_id=ROOT + 11, current_chat_id=CHAT,
            observed_at=env.clock[0] + 1,
        ))
        before = copy.deepcopy(env.identity)
        assert not asyncio.run(concubine.handle_concubine_status_reply(
            PANEL.replace("184", "999"), env.clock[0] + 2,
            SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_STATUS),
            matched_family="concubine_status", current_msg_id=ROOT + 1, current_chat_id=CHAT,
            observed_at=env.clock[0] + 2,
        ))
    assert env.identity == before


async def deliver_query(env, route, *, text=PANEL, context=None):
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=NOW + 1)
    parent = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_STATUS)
    if context is None:
        context = {"family": "concubine_status", "send_as_id": ID, "account_id": ACCOUNT,
                   "chat_id": CHAT, "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    if route == "direct":
        return await reply(env, text=text, reply_context=dict(context, sender_id=BOT))
    if route == "native":
        return await app._handle_routed_reply_event(event, text, NOW + 2, parent, context)
    return await passive_inbox.handle_passive_module_card(
        text, now=NOW + 2, reply_context=context, event=event, event_type="message")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["direct", "native", "passive"])
@pytest.mark.parametrize("text", [PANEL, "\u4f60\u5f53\u524d\u6ca1\u6709\u4f8d\u59be\u3002", "\u6df1\u5ea6\u95ed\u5173\u603b\u7ed3"], ids=["panel", "no-partner", "summary"])
@pytest.mark.parametrize("failure", ["false", "exception"])
def test_owned_query_completion_rolls_back_and_replays(env, kind, route, text, failure):
    assert asyncio.run(send_query(kind))
    receipt(env)
    own_pending = env.identity["pending_tasks"][(CHAT, ROOT)]
    siblings = {
        (CHAT, ROOT + 9): dict(own_pending, op_id="sibling", message_id=ROOT + 9),
        (CHAT - 1, ROOT): dict(own_pending, op_id="other-chat", chat_id=CHAT - 1),
    }
    env.identity["pending_tasks"].update(copy.deepcopy(siblings))
    before = copy.deepcopy(env.identity)
    if failure == "false":
        env.save.return_value = False
        assert not asyncio.run(deliver_query(env, route, text=text))
    else:
        env.save.side_effect = OSError("completion storage failure")
        with pytest.raises(OSError, match="completion storage failure"):
            asyncio.run(deliver_query(env, route, text=text))
    assert env.identity == before
    env.gift.assert_not_awaited()
    env.audit.assert_not_awaited()
    assert not passive_inbox._observed_passive_events

    env.save.return_value, env.save.side_effect = True, None
    assert asyncio.run(deliver_query(env, route, text=text))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["pending_tasks"] == siblings
    completed = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver_query(env, route, text=text))
    assert env.identity == completed
    assert env.gift.await_count == (kind == "gift_status" and text == PANEL)
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("receipt_status", ["sent", "unknown"])
@pytest.mark.parametrize("explicit", [False, True])
def test_passive_query_receipt_selects_chat_owner(env, kind, receipt_status, explicit):
    if receipt_status == "unknown":
        env.send.return_value = None
    asyncio.run(send_query(kind))
    receipt(env)
    state_module.set_identity_account(ID + 1, ACCOUNT + 1)
    other = state_module.get_identity_state(ID + 1)
    other.update(copy.deepcopy(env.identity))
    other["concubine_status_query"].update(
        identity_id=ID + 1, account_id=ACCOUNT + 1, chat_id=CHAT - 1, op_id="other-query")
    other["pending_tasks"] = {(CHAT - 1, ROOT): dict(
        other["pending_tasks"][(CHAT, ROOT)], chat_id=CHAT - 1, account_id=ACCOUNT + 1, op_id="other-query")}
    other_before = copy.deepcopy(other)
    context = {"family": "concubine_status", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    if explicit:
        context["send_as_id"] = ID
    assert asyncio.run(deliver_query(env, "passive", context=context))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 184
    assert other == other_before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["direct", "passive"])
@pytest.mark.parametrize("receipt_status", ["sent", "unknown"])
@pytest.mark.parametrize("failure", ["false", "exception"])
def test_failed_query_completion_survives_sqlite_reload(env, monkeypatch, kind, route, receipt_status, failure):
    if receipt_status == "unknown":
        env.send.return_value = None
    asyncio.run(send_query(kind))
    receipt(env)
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    before = copy.deepcopy(env.identity)
    if failure == "false":
        env.save.return_value = False
        assert not asyncio.run(deliver_query(env, route))
    else:
        env.save.side_effect = OSError("completion storage failure")
        with pytest.raises(OSError, match="completion storage failure"):
            asyncio.run(deliver_query(env, route))
    assert env.identity == before
    env.gift.assert_not_awaited()

    # A later periodic save must not publish the failed completion either.
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity["concubine_status_query"] == before["concubine_status_query"]
    assert env.identity["pending_tasks"] == before["pending_tasks"]
    assert env.identity["concubine_affinity"] == 100
    monkeypatch.setattr(concubine, "save_state", persistence.save_state)
    assert asyncio.run(deliver_query(env, route))
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 184
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert not asyncio.run(deliver_query(env, route))
    assert env.gift.await_count == (kind == "gift_status")
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("explicit", [False, True])
def test_unbound_passive_query_can_replay_after_receipt_arrives(env, kind, explicit):
    env.send.return_value = None
    asyncio.run(send_query(kind))
    before = copy.deepcopy(env.identity)
    context = {"family": "concubine_status", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    if explicit:
        context["send_as_id"] = ID
    assert not asyncio.run(deliver_query(env, "passive", context=context))
    assert env.identity == before
    assert not passive_inbox._observed_passive_events
    receipt(env)
    assert asyncio.run(deliver_query(env, "passive", context=context))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    env.send.assert_awaited_once()


def prepare_recovery_query(env, kind):
    if kind == "fragment":
        with state_module.use_identity(ID):
            concubine._set_fragment_progress("xutian", 4, 4)
    assert asyncio.run(send_query(kind))
    receipt(env)


@pytest.mark.parametrize("kind", [*KINDS, "fragment"])
@pytest.mark.parametrize("failure", ["false", "exception"])
@pytest.mark.parametrize("outcome", ["panel", "no_partner", "summary"])
def test_failed_replay_completion_does_not_expire_terminal_evidence(env, monkeypatch, kind, failure, outcome):
    prepare_recovery_query(env, kind)
    text = {
        "panel": fragment_panel(4, 4) if kind == "fragment" else PANEL,
        "no_partner": "\u4f60\u5f53\u524d\u6ca1\u6709\u4f8d\u59be\u3002",
        "summary": "\u6df1\u5ea6\u95ed\u5173\u603b\u7ed3",
    }[outcome]
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply(text=text)]))
    failure_value = False if failure == "false" else OSError("completion storage failure")
    env.save.reset_mock()
    env.save.side_effect = [True, failure_value, True]
    if failure == "false":
        tick(env, NOW + 1001)
    else:
        with pytest.raises(OSError, match="completion storage failure"):
            tick(env, NOW + 1001)
    assert env.save.call_count == 2
    assert env.identity["concubine_status_query"]["status"] == "sent"
    assert (CHAT, ROOT) in env.identity["pending_tasks"]
    assert env.identity["concubine_affinity"] == 100
    env.gift.assert_not_awaited()

    env.save.side_effect = None
    tick(env, NOW + 1062)
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    env.gift.assert_not_awaited()
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", [*KINDS, "fragment"])
@pytest.mark.parametrize("failure", ["false", "exception"])
def test_query_recovery_checkpoint_save_failure_rolls_back(env, monkeypatch, kind, failure):
    prepare_recovery_query(env, kind)
    before = copy.deepcopy(env.identity)
    replies = Mock(return_value=[logged_reply(text=fragment_panel(4, 4) if kind == "fragment" else PANEL)])
    monkeypatch.setattr(concubine, "find_message_log_replies", replies)
    env.save.reset_mock()
    if failure == "false":
        env.save.return_value = False
        tick(env, NOW + 1001)
    else:
        env.save.side_effect = OSError("checkpoint storage failure")
        with pytest.raises(OSError, match="checkpoint storage failure"):
            tick(env, NOW + 1001)
    assert env.identity == before
    replies.assert_not_called()
    env.save.assert_called_once()
    env.send.assert_awaited_once()
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", [*KINDS, "fragment"])
def test_incomplete_logged_reply_still_allows_bounded_read_expiry(env, monkeypatch, kind):
    prepare_recovery_query(env, kind)
    text = (fragment_panel() if kind == "fragment" else PANEL).splitlines()[0]
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply(text=text)]))
    tick(env, NOW + 1001)
    assert env.identity["concubine_status_query"]["status"] == "expired"
    assert env.identity["concubine_affinity"] == 100
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    env.send.assert_awaited_once()
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["phase", "partner"])
@pytest.mark.parametrize("failure", ["false", "exception"])
def test_failed_delayed_query_completion_retains_new_business(env, kind, change, failure):
    assert asyncio.run(send_query(kind))
    receipt(env)
    env.identity["next_concubine_time"] = NOW + 40000
    if change == "phase":
        env.identity.update(concubine_phase="heart_pending", concubine_heart_msg_id=ROOT + 9)
    else:
        env.identity["concubine_name"] = "new partner"
    before = copy.deepcopy(env.identity)
    if failure == "false":
        env.save.return_value = False
        assert not asyncio.run(deliver_query(env, "passive"))
    else:
        env.save.side_effect = OSError("completion storage failure")
        with pytest.raises(OSError, match="completion storage failure"):
            asyncio.run(deliver_query(env, "passive"))
    assert env.identity == before
    env.gift.assert_not_awaited()
    env.save.return_value, env.save.side_effect = True, None
    assert asyncio.run(deliver_query(env, "passive"))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    for key in ("concubine_name", "concubine_affinity", "concubine_heart_msg_id", "next_concubine_time"):
        assert env.identity[key] == before[key]
    if change == "phase":
        assert env.identity["concubine_phase"] == "heart_pending"
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_passive_query_ambiguous_exact_owners_remain_unresolved(env, kind):
    assert asyncio.run(send_query(kind))
    receipt(env)
    state_module.set_identity_account(ID + 1, ACCOUNT + 1)
    other = state_module.get_identity_state(ID + 1)
    other.update(copy.deepcopy(env.identity))
    other["concubine_status_query"].update(identity_id=ID + 1, account_id=ACCOUNT + 1, op_id="other-query")
    before, other_before = copy.deepcopy(env.identity), copy.deepcopy(other)
    assert not asyncio.run(deliver_query(env, "passive", context={
        "family": "concubine_status", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}))
    assert env.identity == before and other == other_before
    assert not passive_inbox._observed_passive_events
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("hint", ["explicit", "sender", "scalar"])
def test_passive_unowned_status_still_routes_beside_an_unresolved_owned_query(env, hint):
    assert asyncio.run(send_query("status"))
    state_module.set_identity_account(ID + 1, ACCOUNT + 1)
    other = state_module.get_identity_state(ID + 1)
    other.update(copy.deepcopy(env.identity))
    other["concubine_status_query"].update(
        identity_id=ID + 1, account_id=ACCOUNT + 1, msg_id=ROOT + 100, op_id="other-query")
    other["concubine_status_msg_id"] = ROOT + 100
    other_before = copy.deepcopy(other)
    env.identity["concubine_status_query"] = {}
    if hint != "scalar":
        # An explicit manual read has no active legacy script query to resume.
        env.identity.update(concubine_phase="idle", concubine_status_msg_id=0)
    context = {"family": "concubine_status", "root_msg_id": ROOT, "reply_to_msg_id": ROOT,
               "reply_to_command": concubine.CMD_CONCUBINE_STATUS,
               "reply_to_server_at": NOW, "reply_to_command_edited": False}
    if hint == "explicit":
        context["send_as_id"] = ID
    if hint != "scalar":
        context["reply_to_sender_id"] = ID
    before = copy.deepcopy(env.identity)
    assert asyncio.run(deliver_query(env, "passive", context=context)) is (hint != "scalar")
    if hint == "scalar":
        assert env.identity == before
    else:
        assert env.identity["concubine_affinity"] == 184
        assert env.identity["concubine_status_query"]["origin"] == "observed"
    assert other == other_before


@pytest.mark.parametrize("kind", KINDS)
def test_query_passive_diagnostic_failure_cannot_undo_saved_completion(env, monkeypatch, kind):
    assert asyncio.run(send_query(kind))
    receipt(env)
    monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock(side_effect=OSError("diagnostic failed")))
    assert asyncio.run(deliver_query(env, "passive"))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert not env.identity["pending_tasks"]
    assert not asyncio.run(deliver_query(env, "passive"))
    assert env.gift.await_count == (kind == "gift_status")
