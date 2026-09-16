import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, message_log_recovery, persistence, runtime, state as state_module, ui
from model.features import passive_inbox, tianti


ID = 990800001
ACCOUNT = 8001
CHAT = -100800001
ROOT = 80001
NOW = 1_700_000_500.0
BOT = 880800001
KINDS = ("wenxin", "gangfeng", "status", "climb")
COMMANDS = {
    "wenxin": tianti.CMD_TIANTI_WENXIN, "gangfeng": tianti.CMD_TIANTI_GANGFENG,
    "status": tianti.CMD_TIANTI_STATUS, "climb": tianti.CMD_TIANTI_CLIMB,
}
SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "real_message_samples.json").read_text())
RESULTS = {
    "wenxin": SAMPLES["tianti.wenxin.success"]["text"],
    "status": SAMPLES["tianti.status.panel"]["text"],
    "climb": SAMPLES["tianti.climb.success"]["text"],
    "gangfeng": "\u3010\u4e5d\u5929\u7f61\u98ce\u3011\n\u3010\u7f61\u98ce\u6dec\u4f53\u3011\u63d0\u5347\u81f3 3/12 \u5c42",
}


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
        tianti_enabled=True, tianti_wenxin_enabled=True, tianti_gangfeng_enabled=True,
        tianti_progress_current=11, tianti_progress_total=12, tianti_cycle_count=2,
        next_tianti_climb_time=NOW - 1, tianti_last_status_seen_at=NOW - 60,
    )
    clock = [NOW]
    selected = ["climb"]
    send = AsyncMock(return_value=SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + 0.5, send_started_at=NOW))
    recover, save, audit = AsyncMock(return_value=False), Mock(return_value=True), AsyncMock()
    real_recover = tianti._recover_due_tianti_replies
    monkeypatch.setattr(tianti, "_TIANTI_RUN_LOCKS", {})
    monkeypatch.setattr(tianti, "_TIANTI_LEGACY_REPLAY_AFTER", {})
    monkeypatch.setattr(tianti.time, "time", lambda: clock[0])
    monkeypatch.setattr(tianti.random, "randint", lambda *_args: 0)
    monkeypatch.setattr(tianti, "_should_trigger_tianti_wenxin", lambda _now: (selected[0] == "wenxin", "fixture-wenxin"))
    monkeypatch.setattr(tianti, "_should_trigger_tianti_gangfeng", lambda _now: (selected[0] == "gangfeng", "fixture-gangfeng"))
    monkeypatch.setattr(tianti, "_tianti_status_sync_due", lambda _now: selected[0] == "status")
    monkeypatch.setattr(tianti, "_recover_due_tianti_replies", recover)
    monkeypatch.setattr(tianti, "send_game_command", send)
    monkeypatch.setattr(tianti, "save_state", save)
    monkeypatch.setattr(tianti, "send_audit_log", audit)
    monkeypatch.setattr(tianti, "console_log", Mock())
    monkeypatch.setattr(tianti, "classify_game_send_block", lambda *_args, **_kwargs: {"status": "none"}, raising=False)
    monkeypatch.setattr(tianti, "find_recent_message_log_commands", Mock(return_value=[]))
    monkeypatch.setattr(tianti, "find_message_log_message", Mock(return_value=None))
    monkeypatch.setattr(tianti, "find_message_log_replies", Mock(return_value=[]))
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "tianti.db"))
    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    try:
        yield SimpleNamespace(identity=identity, selected=selected, send=send, recover=recover, real_recover=real_recover, save=save, audit=audit, clock=clock)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(before)


async def tick(env):
    with state_module.use_identity(ID):
        await tianti.run_tianti_scheduler(env.clock[0])


def change_owner(env, change):
    if change == "delete":
        state_module.remove_identity(ID)
    elif change == "replace":
        state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
    elif change == "rebind":
        state_module.set_identity_account(ID, ACCOUNT + 1)
    elif change == "identity_disabled":
        state_module.set_identity_enabled(ID, False)
    elif change == "module_disabled":
        env.identity["tianti_enabled"] = False
    elif change == "pause":
        state_module.set_global_enabled(False)
    else:
        env.identity["next_tianti_climb_time"] = NOW + 80000


@pytest.mark.parametrize("kind", KINDS)
def test_scheduled_tianti_commands_are_owned_and_never_transport_retried(env, kind):
    env.selected[0] = kind
    asyncio.run(tick(env))
    env.send.assert_awaited_once()
    call = env.send.await_args
    assert call.args[0] == COMMANDS[kind]
    assert call.kwargs["max_retry"] == 0
    assert call.kwargs["send_as_id"] == ID
    assert call.kwargs["target_chat_id"] == CHAT
    assert call.kwargs["op_id"]
    assert callable(call.kwargs["operation_check"])
    record = env.identity["tianti_commands"][kind]
    assert record["account_id"] == ACCOUNT
    assert record["chat_id"] == CHAT
    assert record["msg_id"] == ROOT
    assert record["status"] == "sent"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["delete", "replace", "rebind", "identity_disabled", "module_disabled", "pause", "schedule"])
def test_scheduler_does_not_continue_after_recovery_changes_owner_or_plan(env, kind, change):
    env.selected[0] = kind
    after = {}

    async def recovered(_now):
        change_owner(env, change)
        after.update(copy.deepcopy(state_module._meta_state))
        return False

    env.recover.side_effect = recovered
    asyncio.run(tick(env))
    env.send.assert_not_awaited()
    assert state_module._meta_state == after


def reply_context(kind, **overrides):
    return {
        "send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT, "root_msg_id": ROOT,
        "sender_id": BOT, "msg_id": ROOT + 1, "server_event_at": NOW + 1,
        "family": f"tianti_{kind}", **overrides,
    }


async def deliver(kind, *, now=NOW + 1, text=None, context=None):
    with state_module.use_identity(ID):
        return await tianti.handle_tianti_reply(
            RESULTS[kind] if text is None else text, now,
            SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=COMMANDS[kind]),
            matched_family=f"tianti_{kind}",
            reply_context=reply_context(kind) if context is None else context,
        )


def finalize(env, kind, *, chat=CHAT, root=ROOT):
    record = env.identity["tianti_commands"][kind]
    return runtime._finalize_game_command_sent(
        COMMANDS[kind], msg_id=root, sent_at=NOW + 0.5, send_started_at=NOW,
        send_as_id=ID, game_group_id=chat, topic_id=0, track=True, max_retry=0, append_sent_log=False,
        send_intent={"op_id": record["op_id"], "source_module": tianti.TIANTI_SOURCE_MODULE},
    )


@pytest.mark.parametrize("kind", KINDS)
def test_native_result_completes_owned_command_before_notifying(env, kind):
    env.selected[0] = kind
    asyncio.run(tick(env))
    finalize(env, kind)

    async def audit(*_args, **_kwargs):
        assert env.identity["tianti_commands"][kind]["status"] == "complete"
        assert (CHAT, ROOT) not in env.identity["pending_tasks"]
        assert env.save.called
        raise RuntimeError("notification unavailable")

    env.audit.side_effect = audit
    assert asyncio.run(deliver(kind))
    completed = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(kind, now=NOW + 500))
    assert env.identity == completed
    assert env.identity["tianti_commands"][kind]["reply_at"] == NOW + 1


@pytest.mark.parametrize("kind", KINDS)
def test_real_early_reply_adopts_runtime_receipt_and_survives_return(env, kind):
    env.selected[0] = kind
    completed = {}

    async def sent(*_args, **_kwargs):
        receipt = finalize(env, kind)
        assert await deliver(kind)
        completed.update(copy.deepcopy(env.identity))
        return receipt

    env.send.side_effect = sent
    asyncio.run(tick(env))
    assert env.identity == completed
    assert env.identity["tianti_commands"][kind]["status"] == "complete"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [
    ("chat_id", CHAT - 1), ("root_msg_id", ROOT - 1), ("root_msg_id", 0),
    ("send_as_id", ID + 1), ("account_id", ACCOUNT + 1), ("sender_id", BOT + 1),
    ("msg_id", 0), ("server_event_at", NOW - 5), ("server_event_at", float("nan")),
    ("server_event_at", 0), ("server_event_at", NOW + 100), ("server_event_at", str(NOW + 1)),
    ("reply_to_command", ".unrelated"), ("reply_to_command_edited", True),
])
def test_unowned_or_unordered_reply_never_mutates(env, kind, field, value):
    env.selected[0] = kind
    asyncio.run(tick(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(kind, context=reply_context(kind, **{field: value})))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_audit_await_cannot_write_into_replaced_owner(env, kind, change):
    env.selected[0] = kind
    asyncio.run(tick(env))
    after = {}

    async def audit(*_args, **_kwargs):
        change_owner(env, change)
        after.update(copy.deepcopy(state_module._meta_state))

    env.audit.side_effect = audit
    assert asyncio.run(deliver(kind))
    assert state_module._meta_state == after


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["module_disabled", "identity_disabled", "pause", "schedule", "rank"])
def test_delayed_receipt_keeps_evidence_without_overwriting_changed_plan(env, kind, change):
    env.selected[0] = kind
    expected = {}

    async def sent(*_args, **_kwargs):
        if change == "rank":
            state_module.set_tianti_rank_choice(ID, "\u957f\u8001")
        else:
            change_owner(env, change)
        expected.update({key: value for key, value in env.identity.items() if key != "tianti_commands"})
        return env.send.return_value

    env.send.side_effect = sent
    asyncio.run(tick(env))
    assert {key: value for key, value in env.identity.items() if key != "tianti_commands"} == expected
    assert env.identity["tianti_commands"][kind]["status"] == "sent"
    assert asyncio.run(deliver(kind))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["delete", "replace", "rebind", "module_disabled", "identity_disabled", "pause", "schedule"])
def test_queued_command_rechecks_its_original_owner_and_plan(env, kind, change):
    env.selected[0] = kind

    async def sent(*_args, **kwargs):
        assert kwargs["operation_check"]()
        change_owner(env, change)
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = sent
    asyncio.run(tick(env))


@pytest.mark.parametrize("kind", KINDS)
def test_reply_after_pause_is_saved_without_sending_a_followup(env, kind):
    env.selected[0] = kind
    asyncio.run(tick(env))
    env.identity["tianti_enabled"] = False
    state_module.set_global_enabled(False)
    assert asyncio.run(deliver(kind))
    assert env.identity["tianti_commands"][kind]["status"] == "complete"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_log_recovery_uses_server_time_and_exact_original_chat(env, monkeypatch, kind):
    env.selected[0] = kind
    asyncio.run(tick(env))
    finalize(env, kind)
    finalize(env, kind, chat=CHAT - 1)
    other = copy.deepcopy(env.identity["pending_tasks"][(CHAT - 1, ROOT)])
    state_module.set_game_group_id(CHAT - 1)
    entries = Mock(return_value=[{
        "event_type": "edit", "chat_id": CHAT, "reply_to_msg_id": ROOT, "message_id": ROOT + 1,
        "sender_is_bot": True, "sender_id": BOT, "server_event_at": NOW + 1,
        "ts_epoch": NOW + 86400, "text": RESULTS[kind],
    }])
    monkeypatch.setattr(tianti, "find_message_log_replies", entries)
    with state_module.use_identity(ID):
        assert asyncio.run(env.real_recover(NOW + 86400))
    assert {call.kwargs["chat_id"] for call in entries.call_args_list} == {CHAT}
    assert len(entries.call_args_list) <= 2
    assert env.identity["tianti_commands"][kind]["reply_at"] == NOW + 1
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert env.identity["pending_tasks"][(CHAT - 1, ROOT)] == other


@pytest.mark.parametrize("field,value", [
    ("sender_is_bot", False), ("sender_id", BOT + 1), ("chat_id", CHAT - 1),
    ("reply_to_msg_id", ROOT - 1), ("message_id", 0), ("event_type", "sent"),
    ("server_event_at", 0), ("server_event_at", NOW - 100),
])
def test_log_recovery_does_not_trust_broad_bot_or_scalar_matches(env, monkeypatch, field, value):
    asyncio.run(tick(env))
    entry = {
        "event_type": "message", "chat_id": CHAT, "reply_to_msg_id": ROOT, "message_id": ROOT + 1,
        "sender_is_bot": True, "sender_id": BOT, "server_event_at": NOW + 1,
        "ts_epoch": NOW + 10, "text": RESULTS["climb"], field: value,
    }
    monkeypatch.setattr(tianti, "find_message_log_replies", Mock(return_value=[entry]))
    with state_module.use_identity(ID):
        assert not asyncio.run(env.real_recover(NOW + 100))
    assert env.identity["tianti_commands"]["climb"]["status"] == "sent"
    assert env.identity["tianti_progress_current"] == 11


@pytest.mark.parametrize("kind", KINDS)
def test_manual_result_has_owned_context_and_cannot_replace_uncertain_work(env, kind):
    context = reply_context(
        kind, source="manual_game_command", reply_to_server_at=NOW - 1,
        reply_to_sender_id=ID, reply_to_command=COMMANDS[kind],
    )
    env.identity["tianti_enabled"] = False
    assert asyncio.run(deliver(kind, context=context))
    assert env.identity["tianti_commands"][kind]["op_id"] == f"manual:{CHAT}:{ROOT}"
    completed = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(kind, now=NOW + 3600, context=dict(context, server_event_at=NOW + 3600)))
    assert env.identity == completed
    env.identity["tianti_commands"] = {}
    env.identity["tianti_enabled"] = True
    env.identity["next_tianti_climb_time"] = NOW - 1
    env.selected[0] = "climb"
    env.send.return_value = None
    asyncio.run(tick(env))
    assert env.identity["tianti_commands"]["climb"]["status"] == "unknown"
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(kind, context=context))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("event_kind", ["message", "edit"])
def test_actual_active_and_passive_entrypoints_use_same_native_contract(env, monkeypatch, kind, event_kind):
    env.selected[0] = kind
    asyncio.run(tick(env))
    context = reply_context(kind)
    event = SimpleNamespace(
        id=ROOT + 1, chat_id=CHAT, sender_id=BOT,
        date=datetime.fromtimestamp(NOW + 1, timezone.utc),
        edit_date=datetime.fromtimestamp(NOW + 1, timezone.utc),
    )
    reply = SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=COMMANDS[kind])
    assert asyncio.run(app._handle_routed_reply_event(
        event, RESULTS[kind], NOW + 1000, reply, context, event_kind=event_kind,
    ))
    before = copy.deepcopy(env.identity)
    monkeypatch.setattr(passive_inbox, "_mark_observed_passive_event", lambda *_a, **_kw: True)
    monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock())
    monkeypatch.setattr(passive_inbox, "save_state", Mock())
    assert not asyncio.run(passive_inbox.handle_passive_module_card(
        app.from_telegram_event(event, RESULTS[kind], context, event_kind=event_kind), now=NOW + 1000,
    ))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
def test_passive_first_delivery_can_close_native_operation(env, monkeypatch, kind):
    env.selected[0] = kind
    asyncio.run(tick(env))
    monkeypatch.setattr(passive_inbox, "_mark_observed_passive_event", lambda *_a, **_kw: True)
    monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock())
    monkeypatch.setattr(passive_inbox, "save_state", Mock())
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=NOW + 1)
    assert asyncio.run(passive_inbox.handle_passive_module_card(
        RESULTS[kind], now=NOW + 1000, event=event, reply_context=reply_context(kind),
    ))
    assert env.identity["tianti_commands"][kind]["status"] == "complete"
    assert env.identity["tianti_commands"][kind]["reply_at"] == NOW + 1


@pytest.mark.parametrize("text", [
    "\u3010\u51cc\u9704\u4e91\u9636\u3011\n\u767b\u9636\u51b7\u5374: \u53ef\u7acb\u5373\u767b\u9636",
    RESULTS["status"].replace("10 / 12", "13 / 12"),
    RESULTS["status"].replace("37\u5206\u949f43\u79d2", "\u4e0d\u53ef\u7528"),
])
def test_native_partial_panel_preserves_all_state_until_terminal_edit(env, text):
    env.selected[0] = "status"
    asyncio.run(tick(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver("status", text=text))
    assert env.identity == before
    assert asyncio.run(deliver("status"))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_command_survives_database_reload_and_closes_from_receipt(env, kind):
    env.selected[0] = kind
    env.send.return_value = None
    asyncio.run(tick(env))
    assert persistence.save_state()
    saved = copy.deepcopy(env.identity["tianti_commands"])
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity["tianti_commands"] == saved
    finalize(env, kind)
    assert asyncio.run(deliver(kind))
    assert persistence.save_state()
    assert persistence.load_state()
    assert state_module.get_identity_state(ID)["tianti_commands"][kind]["status"] == "complete"


def test_historical_scalar_anchors_alone_are_not_unresolved_work(env):
    env.identity.update(tianti_last_climb_msg_id=44, tianti_last_gangfeng_msg_id=45, tianti_last_wenxin_msg_id=46)
    asyncio.run(tick(env))
    env.send.assert_awaited_once()


def test_read_only_status_can_expire_but_mutations_cannot(env, monkeypatch):
    env.selected[0] = "status"
    env.send.return_value = None
    asyncio.run(tick(env))
    env.clock[0] += tianti.TIANTI_REPLAY_INTERVAL_SEC + 1
    asyncio.run(tick(env))
    assert env.send.await_count == 2


@pytest.mark.parametrize("field,value", [
    ("rank_choice", []), ("status", {}), ("account_id", 0), ("chat_id", 0),
    ("started_at", float("inf")), ("msg_id", 1.5), ("sent_at", True), ("status", "unsent"),
])
def test_invalid_persisted_records_hold_without_crashing_or_dispatching(env, field, value):
    asyncio.run(tick(env))
    env.identity["tianti_commands"]["climb"][field] = value
    asyncio.run(tick(env))
    assert env.send.await_count == 1
    assert env.identity["tianti_last_error"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_receipt_cannot_write_into_a_replacement_owner(env, kind, change):
    env.selected[0] = kind
    after = {}

    async def sent(*_args, **_kwargs):
        change_owner(env, change)
        after.update(copy.deepcopy(state_module._meta_state))
        return env.send.return_value

    env.send.side_effect = sent
    asyncio.run(tick(env))
    assert state_module._meta_state == after


@pytest.mark.parametrize("kind", KINDS)
def test_concurrent_schedulers_do_not_queue_two_commands_for_one_role(env, kind):
    env.selected[0] = kind

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def sent(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return env.send.return_value

        env.send.side_effect = sent
        first = asyncio.create_task(tick(env))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(tick(env))
            await asyncio.sleep(0.01)
            assert env.send.await_count == 1
            release.set()
            await asyncio.gather(first, second)
        finally:
            release.set()
            await asyncio.gather(first, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("kind", ("wenxin", "gangfeng", "climb"))
@pytest.mark.parametrize("result", ["none", "cancel", "exception"])
def test_unknown_mutation_is_retained_and_not_reissued_after_its_timer(env, kind, result):
    env.selected[0] = kind
    if result == "none":
        env.send.return_value = None
        asyncio.run(tick(env))
    else:
        error = asyncio.CancelledError if result == "cancel" else RuntimeError
        env.send.side_effect = error("fixture")
        with pytest.raises(error):
            asyncio.run(tick(env))
    record = copy.deepcopy(env.identity["tianti_commands"][kind])
    assert record["status"] == "unknown"
    env.clock[0] += 86400
    env.send.side_effect = None
    asyncio.run(tick(env))
    env.send.assert_awaited_once()
    assert env.identity["tianti_commands"][kind] == record


@pytest.mark.parametrize("kind", KINDS)
def test_confirmed_early_reply_wins_over_delayed_send_receipt(env, kind):
    env.selected[0] = kind

    async def sent(*_args, **_kwargs):
        record = env.identity["tianti_commands"][kind]
        record.update(status="complete", msg_id=ROOT, sent_at=NOW + 0.5, reply_at=NOW + 1)
        env.identity[f"next_tianti_{kind}_time"] = NOW + 20000
        return env.send.return_value

    env.send.side_effect = sent
    asyncio.run(tick(env))
    assert env.identity["tianti_commands"][kind]["status"] == "complete"
    assert env.identity[f"next_tianti_{kind}_time"] == NOW + 20000


@pytest.mark.parametrize("kind", ("wenxin", "gangfeng", "climb"))
def test_current_definitely_unsent_outcome_can_retry_but_stale_block_cannot(env, monkeypatch, kind):
    env.selected[0] = kind
    env.send.return_value = None
    block = {"status": "unsent", "code": "send_queue_timeout", "at": NOW}
    # The first send reports a new block. On the next call the cached block is
    # unchanged, so it is no longer evidence about that dispatch.
    blocks = iter(({"status": "none"}, block, block, block))
    monkeypatch.setattr(tianti, "classify_game_send_block", lambda *_args, **_kwargs: next(blocks))
    asyncio.run(tick(env))
    assert env.identity["tianti_commands"][kind]["status"] == "unsent"
    env.clock[0] += tianti.RETRY_MAX_SEC + 1
    asyncio.run(tick(env))
    assert env.send.await_count == 2
    assert env.identity["tianti_commands"][kind]["status"] == "unknown"


@pytest.mark.parametrize("kind", KINDS)
def test_unpersisted_intent_cannot_dispatch(env, kind):
    env.selected[0] = kind
    env.save.return_value = False
    asyncio.run(tick(env))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_complete_cycle_releases_the_next_due_operation(env, kind):
    env.selected[0] = kind
    asyncio.run(tick(env))
    assert asyncio.run(deliver(kind))
    env.clock[0] = max(NOW + 300, env.identity[f"next_tianti_{kind}_time"] + 1)
    env.send.return_value = SimpleNamespace(
        id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0] + 0.5, send_started_at=env.clock[0],
    )
    asyncio.run(tick(env))
    assert env.send.await_count == 2
    assert env.identity["tianti_commands"][kind]["status"] == "sent"
    assert env.identity["tianti_commands"][kind]["msg_id"] == ROOT + 10


@pytest.mark.parametrize("known_account", [True, False])
def test_scoped_legacy_pending_is_migrated_and_reconciled(env, monkeypatch, known_account):
    pending = {"cmd": COMMANDS["climb"], "chat_id": CHAT, "sent_at": NOW - 1}
    if known_account:
        pending["account_id"] = ACCOUNT
    env.identity["pending_tasks"] = {(CHAT, ROOT): pending}
    env.identity["tianti_last_climb_msg_id"] = ROOT
    logged_send = {
        "event_type": "sent", "chat_id": CHAT, "message_id": ROOT, "sender_id": ID,
        "account_id": ACCOUNT, "text": COMMANDS["climb"], "ts_epoch": NOW - 1,
    }
    sent_log = Mock(return_value=logged_send)
    monkeypatch.setattr(tianti, "find_message_log_message", sent_log)
    monkeypatch.setattr(tianti, "find_message_log_replies", Mock(return_value=[{
        "event_type": "message", "chat_id": CHAT, "reply_to_msg_id": ROOT, "message_id": ROOT + 1,
        "sender_id": BOT, "sender_is_bot": True, "server_event_at": NOW + 1, "text": RESULTS["climb"],
    }]))
    with state_module.use_identity(ID):
        assert asyncio.run(env.real_recover(NOW + 100))
    assert env.identity["tianti_commands"]["climb"]["status"] == "complete"
    assert not env.identity["pending_tasks"]
    if known_account:
        sent_log.assert_not_called()
    else:
        assert sent_log.call_args.kwargs["chat_id"] == CHAT
    env.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["account", "chat", "root", "sender", "event_type", "timestamp"])
def test_legacy_pending_needs_matching_account_and_sent_log_evidence(env, monkeypatch, change):
    env.identity["pending_tasks"] = {(CHAT, ROOT): {"cmd": COMMANDS["climb"], "chat_id": CHAT, "sent_at": NOW - 1}}
    entry = {
        "event_type": "sent", "chat_id": CHAT, "message_id": ROOT, "sender_id": ID,
        "account_id": ACCOUNT, "text": COMMANDS["climb"], "ts_epoch": NOW - 1,
    }
    field, value = {
        "account": ("account_id", ACCOUNT + 1), "chat": ("chat_id", CHAT - 1),
        "root": ("message_id", ROOT + 1), "sender": ("sender_id", ID + 1),
        "event_type": ("event_type", "message"), "timestamp": ("ts_epoch", NOW - 10),
    }[change]
    entry[field] = value
    reader = Mock(return_value=entry)
    monkeypatch.setattr(tianti, "find_message_log_message", reader)
    monkeypatch.setattr(tianti, "_recover_due_tianti_replies", env.real_recover)
    asyncio.run(tick(env))
    asyncio.run(tick(env))
    assert reader.call_count == 1
    assert not env.identity["tianti_commands"]
    assert (CHAT, ROOT) in env.identity["pending_tasks"]
    env.send.assert_not_awaited()


def test_manual_climb_can_supersede_only_a_known_older_read(env):
    env.selected[0] = "status"
    asyncio.run(tick(env))
    finalize(env, "status")
    context = reply_context(
        "climb", source="manual_game_command", reply_to_server_at=NOW + 1,
        reply_to_sender_id=ID, reply_to_command=COMMANDS["climb"], server_event_at=NOW + 2,
    )
    assert asyncio.run(deliver("climb", now=NOW + 2, context=context))
    assert env.identity["tianti_commands"]["status"]["status"] == "expired"
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver("status", now=NOW + 100, context=reply_context("status", server_event_at=NOW + 50)))
    assert env.identity == before
    env.send.assert_awaited_once()


def test_wenxin_extra_levels_are_not_added_again_by_later_edits(env):
    env.selected[0] = "wenxin"
    env.identity["tianti_gangfeng_level"] = 10
    asyncio.run(tick(env))
    text = RESULTS["wenxin"] + "\n\u4e5d\u5929\u7f61\u98ce\u987a\u52bf\u5165\u4f53\uff0c\u4f60\u7684\u3010\u7f61\u98ce\u6dec\u4f53\u3011\u989d\u5916\u63d0\u5347\u4e86 3 \u5c42"
    assert asyncio.run(deliver("wenxin", text=text))
    assert env.identity["tianti_gangfeng_level"] == 12
    assert not asyncio.run(deliver("wenxin", now=NOW + 1000, text=text, context=reply_context("wenxin", server_event_at=NOW + 1000)))
    assert env.identity["tianti_gangfeng_level"] == 12


@pytest.mark.parametrize("change", ["rank", "group", "subflag"])
@pytest.mark.parametrize("boundary", ["recover", "queued"])
def test_profile_group_and_subfeature_changes_invalidate_dispatch(env, change, boundary):
    env.selected[0] = "gangfeng"
    def invalidate():
        if change == "rank":
            state_module.set_tianti_rank_choice(ID, "\u957f\u8001")
        elif change == "group":
            state_module.set_game_group_id(CHAT - 1)
        else:
            env.identity["tianti_gangfeng_enabled"] = False

    if boundary == "recover":
        async def recover(_now):
            invalidate()
            return False
        env.recover.side_effect = recover
        asyncio.run(tick(env))
        env.send.assert_not_awaited()
    else:
        async def send(_command, **kwargs):
            invalidate()
            assert not kwargs["operation_check"]()
            return None
        env.send.side_effect = send
        asyncio.run(tick(env))
        assert env.identity["tianti_commands"]["gangfeng"]["status"] == "unknown"


@pytest.mark.parametrize("change", ["delete", "replace", "rebind", "pause"])
def test_ui_read_does_not_dispatch_after_recovery_invalidates_owner(env, change):
    async def recover(_now):
        change_owner(env, change)
        return False
    env.recover.side_effect = recover
    ok, _message = asyncio.run(ui.ui_sync_tianti_status(ID))
    assert not ok
    env.send.assert_not_awaited()


def test_ui_status_read_is_owned_when_automation_is_disabled(env):
    env.identity["tianti_enabled"] = False
    ok, _message = asyncio.run(ui.ui_sync_tianti_status(ID))
    assert ok
    assert env.identity["tianti_commands"]["status"]["status"] == "sent"
    assert asyncio.run(deliver("status"))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_missing_or_mismatched_receipt_metadata_remains_unknown(env, kind):
    env.selected[0] = kind
    env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT - 1, sent_at=NOW)
    asyncio.run(tick(env))
    assert env.identity["tianti_commands"][kind]["status"] == "unknown"
    assert not env.identity[tianti.TIANTI_COMMANDS[kind][1]]


def test_cached_unsent_block_at_same_time_is_not_new_evidence(env, monkeypatch):
    block = {"status": "unsent", "code": "send_queue_timeout", "at": NOW}
    monkeypatch.setattr(tianti, "classify_game_send_block", lambda *_a, **_kw: block)
    env.send.return_value = None
    asyncio.run(tick(env))
    assert env.identity["tianti_commands"]["climb"]["status"] == "unknown"


@pytest.mark.parametrize("kind", ["climb", "gangfeng"])
def test_conflicting_success_and_resource_denial_cannot_close_operation(env, kind):
    env.selected[0] = kind
    asyncio.run(tick(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(kind, text=RESULTS[kind] + "\n\u8d44\u6e90\u4e0d\u8db3"))
    assert env.identity == before


def test_climb_uses_the_rank_captured_when_dispatch_was_admitted(env):
    asyncio.run(tick(env))
    assert env.identity["tianti_commands"]["climb"]["rank_choice"] == "\u666e\u901a"
    state_module.set_tianti_rank_choice(ID, "\u957f\u8001")
    assert asyncio.run(deliver("climb"))
    assert env.identity["next_tianti_climb_time"] == NOW + 1 + tianti.TIANTI_RANK_CD_SECONDS["\u666e\u901a"]


def test_sent_log_can_recover_a_missing_rpc_receipt_without_blind_retry(env, monkeypatch):
    env.send.return_value = None
    asyncio.run(tick(env))
    record = copy.deepcopy(env.identity["tianti_commands"]["climb"])
    monkeypatch.setattr(tianti, "find_recent_message_log_commands", Mock(return_value=[{
        "event_type": "sent", "account_id": ACCOUNT, "chat_id": CHAT, "message_id": ROOT,
        "sender_id": ID, "ts_epoch": NOW + 0.5, "op_id": record["op_id"],
        "source_module": tianti.TIANTI_SOURCE_MODULE, "text": COMMANDS["climb"],
    }]))
    monkeypatch.setattr(tianti, "find_message_log_replies", Mock(return_value=[{
        "event_type": "edit", "chat_id": CHAT, "reply_to_msg_id": ROOT, "message_id": ROOT + 1,
        "sender_is_bot": True, "sender_id": BOT, "server_event_at": NOW + 1, "text": RESULTS["climb"],
    }]))
    with state_module.use_identity(ID):
        assert asyncio.run(env.real_recover(NOW + 86400))
    assert env.identity["tianti_commands"]["climb"]["status"] == "complete"
    env.send.assert_awaited_once()


def test_real_log_recovery_finds_owned_send_even_after_a_later_manual_command(env, monkeypatch, tmp_path):
    env.send.return_value = None
    asyncio.run(tick(env))
    record = env.identity["tianti_commands"]["climb"]
    monkeypatch.setattr(message_log_recovery, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr(tianti, "find_recent_message_log_commands", message_log_recovery.find_recent_message_log_commands)
    monkeypatch.setattr(tianti, "find_message_log_replies", message_log_recovery.find_message_log_replies)
    rows = [
        {
            "event_type": "sent", "account_id": ACCOUNT, "chat_id": CHAT, "message_id": ROOT,
            "sender_id": ID, "op_id": record["op_id"], "source_module": tianti.TIANTI_SOURCE_MODULE,
            "text": COMMANDS["climb"],
        },
        {"event_type": "message", "chat_id": CHAT, "message_id": ROOT + 10, "sender_id": ID, "text": COMMANDS["climb"]},
        {
            "event_type": "edit", "chat_id": CHAT, "message_id": ROOT + 1, "reply_to_msg_id": ROOT,
            "sender_is_bot": True, "sender_id": BOT, "server_event_at": NOW + 1, "text": RESULTS["climb"],
        },
    ]
    for index, row in enumerate(rows):
        row["ts"] = datetime.fromtimestamp(NOW + index, tianti.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")
    log = tmp_path / (datetime.fromtimestamp(NOW, tianti.TZ_LOCAL).strftime("%Y-%m-%d") + ".log")
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with state_module.use_identity(ID):
        assert asyncio.run(env.real_recover(NOW + 10))
    assert env.identity["tianti_commands"]["climb"]["status"] == "complete"
    env.send.assert_awaited_once()


def test_conflicting_sent_roots_for_one_operation_are_not_guessed(env, monkeypatch):
    env.send.return_value = None
    asyncio.run(tick(env))
    record = env.identity["tianti_commands"]["climb"]
    entry = {
        "event_type": "sent", "account_id": ACCOUNT, "chat_id": CHAT, "sender_id": ID,
        "ts_epoch": NOW + 0.5, "op_id": record["op_id"], "text": COMMANDS["climb"],
        "source_module": tianti.TIANTI_SOURCE_MODULE,
    }
    monkeypatch.setattr(tianti, "find_recent_message_log_commands", Mock(return_value=[
        dict(entry, message_id=ROOT), dict(entry, message_id=ROOT + 10),
    ]))
    with state_module.use_identity(ID):
        assert not asyncio.run(env.real_recover(NOW + 100))
    assert env.identity["tianti_commands"]["climb"]["status"] == "unknown"
    assert env.identity["tianti_commands"]["climb"]["msg_id"] == 0


@pytest.mark.parametrize("kind,text", [
    ("climb", RESULTS["climb"]), ("gangfeng", RESULTS["gangfeng"]), ("status", RESULTS["status"]),
    ("climb", "\u4e5d\u5929\u7f61\u98ce\u5c1a\u672a\u518d\u805a\uff0c\u8bf7\u5728 17\u79d2 \u540e\u518d\u8bd5\u3002"),
    ("gangfeng", "\u4e5d\u5929\u7f61\u98ce\u5c1a\u672a\u518d\u805a\uff0c\u8bf7\u5728 17\u79d2 \u540e\u518d\u8bd5\u3002"),
    ("climb", "\u4fee\u4e3a\u4e0d\u8db3"), ("gangfeng", "\u4fee\u4e3a\u4e0d\u8db3"),
])
def test_cd_display_and_schedule_share_server_time_even_on_late_replay(env, kind, text):
    env.selected[0] = kind
    asyncio.run(tick(env))
    assert asyncio.run(deliver(kind, now=NOW + 3600, text=text))
    timer = "gangfeng" if kind == "gangfeng" else "climb"
    due_at = env.identity[f"next_tianti_{timer}_time"]
    display_key = "tianti_gangfeng_status" if kind == "gangfeng" else "tianti_cooldown_text"
    assert env.identity[display_key] == datetime.fromtimestamp(due_at, tianti.TZ_LOCAL).strftime("%H:%M:%S")
