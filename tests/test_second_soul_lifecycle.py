import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, control, persistence, runtime
from model import state as state_module
from model.features import passive_inbox, second_soul


NOW = 1780000000.0
IDENTITY = 990610001
ACCOUNT = 7601
CHAT = -100610001
ROOT = 6101
KINDS = ("status", "train", "purge", "demon_status")
SPECS = {
    "status": (second_soul.CMD_SECOND_SOUL_STATUS, "status_pending", "second_soul_status_msg_id"),
    "train": (second_soul.CMD_SECOND_SOUL_TRAIN, "train_pending", "second_soul_train_msg_id"),
    "purge": (second_soul.CMD_SECOND_SOUL_PURGE, "purge_pending", "second_soul_purge_msg_id"),
    "demon_status": (second_soul.CMD_SECOND_SOUL_DEMON_STATUS, "purge_status_pending", "second_soul_purge_status_msg_id"),
}
RESULTS = {
    "status": "\u3010\u4f60\u7684\u7b2c\u4e8c\u5143\u795e\uff1a\u91d1\u4e4b\u5143\u795e\u3011\n\u72b6\u6001: \u7a8d\u4e2d\u6e29\u517b\n\u7b49\u7ea7: 34 \u7ea7",
    "train": "\u4f60\u7684\u7b2c\u4e8c\u5143\u795e\u5df2\u5f00\u59cb\u95ed\u5173\u4fee\u70bc\uff0c\u672c\u6b21\u4fee\u70bc\u5c06\u6301\u7eed24\u5c0f\u65f6\u3002",
    "purge": "\u3010\u5143\u795e\u9547\u9b54\u3011\n\u4f60\u8017\u53bb 5000 \u70b9\u4fee\u4e3a\uff0c\u5f3a\u884c\u9547\u538b\u8bc6\u6d77\u4e2d\u7ffb\u817e\u7684\u4e94\u9b54\u3002\n\u9b54\u67d3\u5ea6: 91 \u2192 39",
    "demon_status": "\u3010\u4e94\u5b50\u540c\u5fc3\u9b54\u3011\n\u9b54\u67d3: 39",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_group_route_config({"primary_group_id": CHAT})
    state_module.set_game_bot_ids([880610001])
    identity = state_module.get_identity_state(IDENTITY)
    identity.update(second_soul_enabled=True, second_soul_moran_value=91)
    send = AsyncMock(return_value=SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW))
    audit = AsyncMock()
    monkeypatch.setattr(second_soul, "send_game_command", send)
    monkeypatch.setattr(second_soul, "send_audit_log", audit)
    monkeypatch.setattr(second_soul, "console_log", Mock())
    monkeypatch.setattr(second_soul, "save_state", Mock(return_value=True))
    monkeypatch.setattr(second_soul, "classify_game_send_block", lambda *_: {"status": "unknown", "code": "send_timeout"})
    monkeypatch.setattr(second_soul.time, "time", lambda: NOW)
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "second_soul.db"))
    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    with state_module.use_identity(IDENTITY):
        yield SimpleNamespace(identity=identity, send=send, audit=audit)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def deliver(kind, *, now=NOW + 1, root=ROOT, chat=CHAT, text=None, context=None):
    command = SPECS[kind][0]
    return getattr(second_soul, f"handle_second_soul_{kind}_reply")(
        RESULTS[kind] if text is None else text,
        now,
        reply_to=SimpleNamespace(
            id=root, chat_id=chat, raw_text=command, sender_id=IDENTITY,
            date=datetime.fromtimestamp(NOW, timezone.utc),
        ),
        matched_family=f"second_soul_{kind}",
        **({"reply_context": context} if context is not None else {}),
    )


async def dispatch(kind):
    if kind == "purge":
        return await second_soul._send_second_soul_purge(IDENTITY, NOW)
    if kind == "demon_status":
        return await second_soul._send_second_soul_demon_status(IDENTITY, NOW)
    state_module.state["second_soul_phase"] = "ready_to_train" if kind == "train" else "idle"
    state_module.state["next_second_soul_time"] = 0
    return await second_soul.run_second_soul_scheduler(NOW)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("bad_anchor", ["chat", "root", "missing"])
def test_unowned_reply_does_not_mutate_or_claim_pending(env, kind, bad_anchor):
    asyncio.run(dispatch(kind))
    before = copy.deepcopy(env.identity)
    handled = asyncio.run(deliver(
        kind, chat=CHAT - 1 if bad_anchor == "chat" else CHAT,
        root=0 if bad_anchor == "missing" else ROOT + 8 if bad_anchor == "root" else ROOT,
    ))
    assert not handled
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
def test_early_reply_is_not_reverted_by_later_receipt(env, kind):
    captured = {}

    async def send(command, **kwargs):
        receipt = runtime._finalize_game_command_sent(
            command, msg_id=ROOT, sent_at=NOW, send_started_at=NOW,
            send_as_id=IDENTITY, game_group_id=CHAT, topic_id=0, track=True,
            max_retry=0, append_sent_log=False,
            send_intent={"op_id": kwargs.get("op_id", ""), "source_module": kwargs.get("source_module", "")},
        )
        assert await deliver(kind)
        captured.update(phase=env.identity["second_soul_phase"], next=env.identity["next_second_soul_time"])
        return receipt

    env.send.side_effect = send
    asyncio.run(dispatch(kind))
    assert env.identity["second_soul_phase"] == captured["phase"]
    assert env.identity["next_second_soul_time"] == captured["next"]
    assert env.identity[SPECS[kind][2]] == 0


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("invalidation", ["delete", "replace", "rebind"])
def test_receipt_cannot_write_through_changed_owner(env, kind, invalidation):
    captured = {}

    async def send(_command, **_kwargs):
        if invalidation == "delete":
            state_module.remove_identity(IDENTITY)
        elif invalidation == "replace":
            state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(env.identity)
        else:
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        if state_module.has_identity(IDENTITY):
            captured.update(copy.deepcopy(state_module.get_identity_state(IDENTITY)))
        return SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW)

    env.send.side_effect = send
    asyncio.run(dispatch(kind))
    if invalidation == "delete":
        assert not state_module.has_identity(IDENTITY)
    else:
        assert state_module.get_identity_state(IDENTITY) == captured


def test_duplicate_train_result_does_not_extend_training(env):
    asyncio.run(dispatch("train"))
    assert asyncio.run(deliver("train"))
    completed = copy.deepcopy(env.identity)
    asyncio.run(deliver("train", now=NOW + 3600))
    assert env.identity == completed


@pytest.mark.parametrize("kind", ["purge", "demon_status"])
def test_unknown_or_refused_text_does_not_release_training(env, kind):
    asyncio.run(dispatch(kind))
    before = copy.deepcopy(env.identity)
    text = "\u5143\u795e\u9547\u9b54\u6682\u4e0d\u53ef\u7528\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002"
    assert not asyncio.run(deliver(kind, text=text))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
def test_failed_intent_save_does_not_send(env, monkeypatch, kind):
    monkeypatch.setattr(second_soul, "save_state", Mock(return_value=False))
    asyncio.run(dispatch(kind))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_cancelled_send_survives_sqlite_reload_without_immediate_retry(env, monkeypatch, kind):
    monkeypatch.setattr(second_soul, "save_state", persistence.save_state)

    async def cancel(*_args, **_kwargs):
        raise asyncio.CancelledError()

    env.send.side_effect = cancel
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dispatch(kind))
    record = copy.deepcopy(env.identity["second_soul_commands"][kind])
    assert record["status"] == "unknown"
    assert persistence.load_state()
    restored = state_module.get_identity_state(IDENTITY)
    assert restored["second_soul_commands"][kind] == record
    control._restore_second_soul_runtime(NOW + 30)
    assert restored["second_soul_phase"] == SPECS[kind][1]
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 30))
    assert env.send.await_count == 1


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("invalidate", ["module", "identity", "global", "phase"])
def test_queued_command_checks_business_admission(env, kind, invalidate):
    async def blocked(_command, **kwargs):
        assert kwargs["operation_check"]()
        if invalidate == "module":
            env.identity["second_soul_enabled"] = False
        elif invalidate == "identity":
            state_module.set_identity_enabled(IDENTITY, False)
        elif invalidate == "global":
            state_module.set_global_enabled(False)
        else:
            env.identity["second_soul_phase"] = "injured"
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = blocked
    asyncio.run(dispatch(kind))
    assert env.send.await_count == 1


@pytest.mark.parametrize("kind", KINDS)
def test_received_result_after_module_off_is_retained_without_sending(env, kind):
    asyncio.run(dispatch(kind))
    control._disable_second_soul_module_state()
    assert asyncio.run(deliver(kind))
    assert env.identity["second_soul_commands"][kind]["status"] == "complete"
    assert not env.identity["second_soul_enabled"]
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 100))
    assert env.send.await_count == 1


def test_native_result_clock_can_precede_delayed_rpc_receipt(env, monkeypatch):
    async def delayed(*_args, **_kwargs):
        monkeypatch.setattr(second_soul.time, "time", lambda: NOW + 60)
        return SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + 50, send_started_at=NOW)

    env.send.side_effect = delayed
    asyncio.run(dispatch("train"))
    assert asyncio.run(deliver("train", now=NOW + 60, context={
        "send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT,
        "server_event_at": NOW + 1,
    }))
    assert env.identity["next_second_soul_time"] == NOW + 1 + second_soul.SECOND_SOUL_TRAIN_CD_SEC + second_soul.CD_BUFFER_SEC


@pytest.mark.parametrize("field,value", [
    ("send_as_id", IDENTITY + 1), ("account_id", ACCOUNT + 1),
    ("chat_id", CHAT - 1), ("root_msg_id", ROOT + 1),
    ("server_event_at", NOW - 10), ("server_event_at", float("nan")),
])
def test_context_mismatch_is_rejected_before_panel_level_write(env, field, value):
    asyncio.run(dispatch("status"))
    before = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(deliver("status", context={
        "send_as_id": IDENTITY, "account_id": ACCOUNT, "chat_id": CHAT,
        "root_msg_id": ROOT, "server_event_at": NOW + 1, field: value,
    }))
    assert state_module._meta_state == before


def test_unknown_purge_does_not_repeat_on_high_moran_query(env):
    env.send.return_value = None
    asyncio.run(dispatch("purge"))
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW + 200)
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 200))
    assert env.send.await_count == 2
    assert asyncio.run(deliver("demon_status", now=NOW + 201, root=ROOT + 10, text=RESULTS["demon_status"].replace("39", "91")))
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 4000))
    assert env.send.await_count == 2
    assert env.identity["second_soul_commands"]["purge"]["status"] == "unknown"


def test_status_panel_can_calibrate_uncertain_train_before_new_cycle(env):
    env.send.return_value = None
    asyncio.run(dispatch("train"))
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW + 7200)
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 7200))
    assert asyncio.run(deliver("status", now=NOW + 7201, root=ROOT + 10))
    assert env.identity["second_soul_commands"]["train"]["status"] == "expired"
    env.send.return_value = SimpleNamespace(id=ROOT + 20, chat_id=CHAT, sent_at=NOW + 7202)
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 7202))
    assert env.send.await_count == 3


def test_replay_uses_original_chat_and_leaves_other_pending_alone(env, monkeypatch):
    asyncio.run(dispatch("train"))
    record = env.identity["second_soul_commands"]["train"]
    runtime._finalize_game_command_sent(
        SPECS["train"][0], msg_id=ROOT, sent_at=NOW, send_started_at=NOW,
        send_as_id=IDENTITY, game_group_id=CHAT, track=True, max_retry=0, append_sent_log=False,
        send_intent={"op_id": record["op_id"], "source_module": "\u7b2c\u4e8c\u5143\u795e"},
    )
    runtime._finalize_game_command_sent(
        SPECS["train"][0], msg_id=ROOT, sent_at=NOW, send_started_at=NOW,
        send_as_id=IDENTITY, game_group_id=CHAT - 1, track=True, max_retry=0, append_sent_log=False,
    )
    other = copy.deepcopy(env.identity["pending_tasks"][(CHAT - 1, ROOT)])
    state_module.set_game_group_route_config({"primary_group_id": CHAT - 1})
    replies = Mock(return_value=[{
        "chat_id": CHAT, "message_id": ROOT + 1, "reply_to_msg_id": ROOT,
        "sender_id": 880610001, "sender_is_bot": True, "event_type": "edit",
        "server_event_at": NOW + 1, "ts_epoch": NOW + 3600, "text": RESULTS["train"],
    }])
    monkeypatch.setattr(second_soul, "find_message_log_replies", replies)
    assert asyncio.run(second_soul._recover_second_soul_pending_from_message_log(NOW + 3600, "train_pending"))
    assert replies.call_args.kwargs["chat_id"] == CHAT
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert env.identity["pending_tasks"][(CHAT - 1, ROOT)] == other


def test_manual_ui_panel_still_updates_level_when_automation_disabled(env):
    env.identity["second_soul_enabled"] = False
    assert asyncio.run(deliver("status", context={
        "send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT,
        "source": "manual_game_command", "server_event_at": NOW + 1,
    }))
    assert state_module.get_tianjige_dao_path_records()[str(IDENTITY)]["second_soul_level"] == "34\u7ea7"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_dispatcher_forwards_server_clock_for_native_message_and_edit(env, monkeypatch, kind):
    asyncio.run(dispatch(kind))
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app, "_claim_runtime_event", lambda *_a, **_kw: True)
    monkeypatch.setattr(app, "_has_runtime_message_consumed", lambda *_a: False)
    monkeypatch.setattr(app, "_mark_runtime_message_consumed", Mock())
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, date=datetime.fromtimestamp(NOW + 1, timezone.utc), edit_date=datetime.fromtimestamp(NOW + 1, timezone.utc))
    reply = SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=SPECS[kind][0], sender_id=IDENTITY)
    context = {"send_as_id": IDENTITY, "family": f"second_soul_{kind}", "chat_id": CHAT, "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    for event_kind in ("message", "edit"):
        asyncio.run(app._handle_routed_reply_event(event, RESULTS[kind], NOW + 3600, reply, context, event_kind=event_kind))
    assert env.identity["second_soul_commands"][kind]["status"] == "complete"
    assert env.identity["second_soul_commands"][kind]["reply_at"] == NOW + 1


RETURN = (
    "\u3010\u7b2c\u4e8c\u5143\u795e\u5f52\u4f4d\u3011\n"
    "\u9053\u53cb @SoulOwner \u7684\u7b2c\u4e8c\u5143\u795e\u5df2\u7ed3\u675f\u4fee\u70bc\uff0c\u56de\u5f52\u7a8d\u4e2d\u6e29\u517b\u3002\n"
    "\u9b54\u67d3: 91"
)
WARNING = "\u3010\u5929\u9053\u8b66\u793a\u00b7\u5fc3\u9b54\u8bd5\u70bc\u3011\n@SoulOwner \u7684\u7b2c\u4e8c\u5143\u795e\u906d\u9047\u5fc3\u9b54\u3002"


def test_late_high_moran_return_does_not_erase_new_training(env):
    state_module.update_send_as_profile(IDENTITY, username="SoulOwner")
    env.identity.update(second_soul_phase="cultivating", second_soul_last_train_started_at=NOW - 1, next_second_soul_time=NOW + 86400)
    before = copy.deepcopy(env.identity)
    asyncio.run(second_soul.handle_second_soul_return_broadcast(RETURN, NOW))
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("invalidation", ["delete", "replace", "rebind", "disable"])
@pytest.mark.parametrize("broadcast", ["return", "warning"])
def test_broadcast_followup_cannot_cross_owner_change_in_audit(env, invalidation, broadcast):
    state_module.update_send_as_profile(IDENTITY, username="SoulOwner")

    async def audit(*_args, **_kwargs):
        if invalidation == "delete":
            state_module.remove_identity(IDENTITY)
        elif invalidation == "replace":
            state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(env.identity)
        elif invalidation == "rebind":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        else:
            env.identity["second_soul_enabled"] = False

    env.audit.side_effect = audit
    if broadcast == "return":
        asyncio.run(second_soul.handle_second_soul_return_broadcast(RETURN, NOW))
    else:
        asyncio.run(second_soul.handle_second_soul_heart_demon_warning_broadcast(WARNING, NOW, ROOT, event_chat_id=CHAT))
    env.send.assert_not_awaited()


def test_high_moran_broadcast_queues_purge_before_awaiting_audit(env):
    state_module.update_send_as_profile(IDENTITY, username="SoulOwner")

    async def audit(*_args, **_kwargs):
        assert env.identity["second_soul_phase"] in {"purge_ready", "purge_pending"}

    env.audit.side_effect = audit
    asyncio.run(second_soul.handle_second_soul_return_broadcast(RETURN, NOW))
    assert env.send.call_args.args[0] == second_soul.CMD_SECOND_SOUL_PURGE


def test_queued_heart_choice_has_its_own_admission_check(env):
    state_module.update_send_as_profile(IDENTITY, username="SoulOwner")

    async def invalidated(_command, **kwargs):
        assert kwargs["operation_check"]()
        env.identity["second_soul_auto_choice_enabled"] = False
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = invalidated
    asyncio.run(second_soul.handle_second_soul_heart_demon_warning_broadcast(WARNING, NOW, ROOT, event_chat_id=CHAT))


@pytest.mark.parametrize("record", [None, [], {"status": []}, {"unknown": {}}])
def test_malformed_command_state_cannot_send_or_claim_reply(env, record):
    env.identity["second_soul_commands"] = record
    asyncio.run(second_soul.run_second_soul_scheduler(NOW))
    assert not asyncio.run(deliver("status"))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_disable_preserves_exact_pending_receipt_but_disables_retries(env, kind):
    asyncio.run(dispatch(kind))
    record = env.identity["second_soul_commands"][kind]
    runtime._finalize_game_command_sent(
        SPECS[kind][0], msg_id=ROOT, sent_at=NOW, send_started_at=NOW,
        send_as_id=IDENTITY, game_group_id=CHAT, track=True, max_retry=2, append_sent_log=False,
        send_intent={"op_id": record["op_id"], "source_module": "\u7b2c\u4e8c\u5143\u795e"},
    )
    control._disable_second_soul_module_state()
    pending = env.identity["pending_tasks"][(CHAT, ROOT)]
    assert pending["max_retry"] == 0
    assert pending["op_id"] == record["op_id"]
    assert env.identity["second_soul_phase"] == SPECS[kind][1]
    assert asyncio.run(deliver(kind))
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert control.PENDING_TASK_COMMAND_TO_MODULE.get(SPECS[kind][0]) == "\u7b2c\u4e8c\u5143\u795e"


def test_status_timeout_does_not_delete_other_same_family_receipt(env):
    asyncio.run(dispatch("status"))
    runtime._finalize_game_command_sent(
        SPECS["status"][0], msg_id=ROOT + 1, sent_at=NOW + 1, send_started_at=NOW + 1,
        send_as_id=IDENTITY, game_group_id=CHAT - 1, track=True, max_retry=0, append_sent_log=False,
    )
    other = copy.deepcopy(env.identity["pending_tasks"][(CHAT - 1, ROOT + 1)])
    env.send.return_value = SimpleNamespace(id=ROOT + 2, chat_id=CHAT, sent_at=NOW + 4000)
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 4000))
    assert env.identity["pending_tasks"][(CHAT - 1, ROOT + 1)] == other


def test_old_native_return_uses_server_clock_not_delivery_clock(env):
    state_module.update_send_as_profile(IDENTITY, username="SoulOwner")
    env.identity.update(second_soul_phase="cultivating", second_soul_last_train_started_at=NOW - 3600, next_second_soul_time=NOW + 23 * 3600)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(second_soul.handle_second_soul_return_broadcast(
        RETURN, NOW,
        event=SimpleNamespace(id=ROOT, chat_id=CHAT, date=datetime.fromtimestamp(NOW - 86400, timezone.utc)),
    ))
    assert env.identity == before
    env.send.assert_not_awaited()


def test_new_purge_is_not_lost_when_old_result_is_replayed(env, monkeypatch):
    asyncio.run(dispatch("purge"))
    old = copy.deepcopy(env.identity["second_soul_commands"]["purge"])
    runtime._finalize_game_command_sent(
        SPECS["purge"][0], msg_id=ROOT, sent_at=NOW, send_started_at=NOW,
        send_as_id=IDENTITY, game_group_id=CHAT, track=True, max_retry=0, append_sent_log=False,
        send_intent={"op_id": old["op_id"], "source_module": "\u7b2c\u4e8c\u5143\u795e"},
    )
    monkeypatch.setattr(second_soul, "find_message_log_replies", Mock(return_value=[{
        "chat_id": CHAT, "message_id": ROOT + 1, "reply_to_msg_id": ROOT,
        "sender_id": 880610001, "sender_is_bot": True, "event_type": "edit",
        "server_event_at": NOW + 1, "ts_epoch": NOW + 10, "text": RESULTS["purge"].replace("39", "61"),
    }]))
    env.send.return_value = SimpleNamespace(id=ROOT + 2, chat_id=CHAT, sent_at=NOW + 1)
    assert asyncio.run(second_soul._recover_second_soul_pending_from_message_log(NOW + 10, "purge_pending"))
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    current = copy.deepcopy(env.identity)
    assert current["second_soul_commands"]["purge"]["msg_id"] == ROOT + 2
    assert not asyncio.run(deliver("purge", now=NOW + 20))
    assert env.identity == current


def test_actual_ui_refresh_second_soul_read_updates_level_with_module_disabled(env, monkeypatch):
    env.identity["second_soul_enabled"] = False
    monkeypatch.setattr(control, "save_state", Mock())
    monkeypatch.setattr(control, "send_game_command", AsyncMock(side_effect=[
        SimpleNamespace(id=ROOT - 2, chat_id=CHAT, sent_at=NOW),
        SimpleNamespace(id=ROOT - 1, chat_id=CHAT, sent_at=NOW),
        SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW),
    ]))
    ok, _message = asyncio.run(control.refresh_identity_info(IDENTITY, source="ui"))
    assert ok
    assert asyncio.run(deliver("status", context={
        "send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT,
        "source": "", "server_event_at": NOW + 1,
    }))
    assert state_module.get_tianjige_dao_path_records()[str(IDENTITY)]["second_soul_level"] == "34\u7ea7"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["train", "purge", "demon_status"])
def test_native_manual_command_reconciles_once_without_an_automatic_receipt(env, kind):
    context = {
        "send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT,
        "source": "manual_game_command", "server_event_at": NOW + 1,
    }
    assert asyncio.run(deliver(kind, context=context))
    before = copy.deepcopy(env.identity)
    assert asyncio.run(deliver(kind, now=NOW + 100, context=context))
    assert env.identity == before
    env.send.assert_not_awaited()


def test_ui_panel_does_not_clear_an_unresolved_purge(env):
    asyncio.run(dispatch("purge"))
    assert asyncio.run(second_soul.remember_second_soul_status_read(
        SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW + 1),
        send_as_id=IDENTITY, identity_state=env.identity, account_id=ACCOUNT, requested_at=NOW + 1,
    ))
    assert asyncio.run(deliver("status", now=NOW + 2, root=ROOT + 10))
    assert env.identity["second_soul_phase"] == "purge_pending"
    assert env.identity["second_soul_commands"]["purge"]["status"] == "sent"
    assert env.identity["second_soul_moran_value"] == 91
    assert state_module.get_tianjige_dao_path_records()[str(IDENTITY)]["second_soul_level"] == "34\u7ea7"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("corruption", ["none", "list", "wrong_chat"])
def test_dispatch_revalidates_command_record_after_an_await(env, kind, corruption):
    saved = {}

    async def send(_command, **kwargs):
        assert kwargs["operation_check"]()
        if corruption == "none":
            env.identity["second_soul_commands"] = None
        elif corruption == "list":
            env.identity["second_soul_commands"][kind] = []
        else:
            env.identity["second_soul_commands"][kind]["chat_id"] = CHAT - 1
        saved.update(copy.deepcopy(env.identity))
        assert not kwargs["operation_check"]()
        return SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW)

    env.send.side_effect = send
    asyncio.run(dispatch(kind))
    assert env.identity == saved


@pytest.mark.parametrize("boundary", [1, 2, 3])
@pytest.mark.parametrize("invalidation", ["delete", "replace", "rebind"])
def test_ui_refresh_keeps_one_owner_for_all_level_reads(env, monkeypatch, boundary, invalidation):
    captured = {}
    calls = []

    async def send(command, **_kwargs):
        calls.append(command)
        if len(calls) == boundary:
            if invalidation == "delete":
                state_module.remove_identity(IDENTITY)
            elif invalidation == "replace":
                state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(env.identity)
            else:
                state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
            if state_module.has_identity(IDENTITY):
                captured.update(copy.deepcopy(state_module.get_identity_state(IDENTITY)))
        return SimpleNamespace(id=ROOT + len(calls), chat_id=CHAT, sent_at=NOW)

    monkeypatch.setattr(control, "send_game_command", AsyncMock(side_effect=send))
    monkeypatch.setattr(control, "save_state", Mock())
    ok, _message = asyncio.run(control.refresh_identity_info(IDENTITY))
    assert not ok
    assert len(calls) == boundary
    if invalidation == "delete":
        assert not state_module.has_identity(IDENTITY)
    else:
        assert state_module.get_identity_state(IDENTITY) == captured


def test_ui_refresh_queue_checks_original_owner(env, monkeypatch):
    async def send(_command, **kwargs):
        assert kwargs["operation_check"]()
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        assert not kwargs["operation_check"]()
        return None

    sender = AsyncMock(side_effect=send)
    monkeypatch.setattr(control, "send_game_command", sender)
    monkeypatch.setattr(control, "save_state", Mock())
    ok, _message = asyncio.run(control.refresh_identity_info(IDENTITY))
    assert not ok
    sender.assert_awaited_once()


@pytest.mark.parametrize("phase,key", [
    ("train_pending", "second_soul_train_msg_id"),
    ("purge_pending", "second_soul_purge_msg_id"),
    ("purge_status_pending", "second_soul_purge_status_msg_id"),
])
def test_legacy_unowned_mutation_is_not_reset_or_automatically_retried(env, phase, key):
    env.identity.update(second_soul_phase=phase, second_soul_purge_attempts=1)
    env.identity[key] = ROOT
    control._restore_second_soul_runtime(NOW)
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 7200))
    assert env.identity["second_soul_phase"] == phase
    assert env.identity[key] == ROOT
    assert env.identity["second_soul_last_error"]
    env.send.assert_not_awaited()


def test_legacy_warning_does_not_invent_an_account_owner(env):
    state_module.update_send_as_profile(IDENTITY, username="SoulOwner")
    env.identity.update(
        second_soul_phase="heart_demon_pending", second_soul_heart_demon_msg_id=ROOT,
        second_soul_heart_demon_deadline=NOW + 100,
    )
    asyncio.run(second_soul.handle_second_soul_heart_demon_warning_broadcast(WARNING, NOW, ROOT, event_chat_id=CHAT))
    assert env.identity["second_soul_heart_demon_account_id"] == 0
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 7200))
    assert env.identity["second_soul_heart_demon_msg_id"] == ROOT
    env.send.assert_not_awaited()


def test_old_native_warning_cannot_replace_current_training(env, monkeypatch):
    state_module.update_send_as_profile(IDENTITY, username="SoulOwner")
    env.identity.update(
        second_soul_phase="cultivating", second_soul_last_train_started_at=NOW - 3600,
        next_second_soul_time=NOW + 23 * 3600,
    )
    before = copy.deepcopy(env.identity)
    monkeypatch.setattr(app, "_claim_runtime_event", lambda *_a, **_kw: True)
    asyncio.run(app._dispatch_second_soul_broadcast_fallbacks(
        SimpleNamespace(id=ROOT, chat_id=CHAT, date=datetime.fromtimestamp(NOW - 86400, timezone.utc)),
        WARNING, NOW,
    ))
    assert env.identity == before
    env.send.assert_not_awaited()


def test_warning_deadline_uses_original_creation_not_late_edit(env, monkeypatch):
    state_module.update_send_as_profile(IDENTITY, username="SoulOwner")
    monkeypatch.setattr(app, "_claim_runtime_event", lambda *_a, **_kw: True)
    asyncio.run(app._dispatch_second_soul_broadcast_fallbacks(
        SimpleNamespace(
            id=ROOT, chat_id=CHAT,
            date=datetime.fromtimestamp(NOW - 3600, timezone.utc),
            edit_date=datetime.fromtimestamp(NOW, timezone.utc),
        ), WARNING, NOW,
    ))
    env.send.assert_not_awaited()
    assert env.identity["second_soul_heart_demon_deadline"] <= NOW


def test_native_heart_failure_uses_server_settlement_time(env):
    env.identity.update(
        second_soul_phase="heart_demon_pending", second_soul_heart_demon_msg_id=ROOT,
        second_soul_heart_demon_chat_id=CHAT, second_soul_heart_demon_account_id=ACCOUNT,
    )
    settled_at = NOW - 3600
    assert asyncio.run(second_soul.handle_second_soul_choice_result_broadcast(
        "\u3010\u7834\u800c\u540e\u7acb\u00b7\u5931\u8d25\u3011", NOW,
        event=SimpleNamespace(id=ROOT, chat_id=CHAT, edit_date=datetime.fromtimestamp(settled_at, timezone.utc)),
    ))
    assert env.identity["next_second_soul_time"] == settled_at + second_soul.SECOND_SOUL_TRAIN_CD_SEC + second_soul.CD_BUFFER_SEC


@pytest.mark.parametrize("kind,phase", [("purge", "purge_ready"), ("demon_status", "purge_pending")])
def test_definitely_unsent_purge_step_keeps_its_next_action(env, monkeypatch, kind, phase):
    if kind == "demon_status":
        asyncio.run(dispatch("purge"))
    env.send.return_value = None
    monkeypatch.setattr(second_soul, "classify_game_send_block", lambda *_: {"status": "unsent", "code": "bot_health"})
    asyncio.run(dispatch(kind))
    assert env.identity["second_soul_phase"] == phase
    assert env.identity["second_soul_purge_due_at"] == NOW + 600
    if kind == "purge":
        assert env.identity["second_soul_purge_attempts"] == 0
    sent_count = env.send.await_count
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 1))
    assert env.send.await_count == sent_count


@pytest.mark.parametrize("kind", ["purge", "demon_status"])
@pytest.mark.parametrize("moran", [39, 91])
def test_manual_moran_result_does_not_interrupt_training(env, kind, moran):
    asyncio.run(dispatch("train"))
    assert asyncio.run(deliver("train"))
    next_time = env.identity["next_second_soul_time"]
    context = {
        "send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT + 1,
        "source": "manual_game_command", "server_event_at": NOW + 2,
    }
    assert asyncio.run(deliver(kind, root=ROOT + 1, now=NOW + 2, context=context, text=RESULTS[kind].replace("39", str(moran))))
    assert env.identity["second_soul_phase"] == "cultivating"
    assert env.identity["next_second_soul_time"] == next_time
    assert env.identity["second_soul_moran_value"] == moran
    assert env.send.await_count == 1


def test_stale_manual_train_edit_does_not_start_a_new_cooldown(env):
    asyncio.run(dispatch("train"))
    assert asyncio.run(deliver("train"))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(second_soul.handle_second_soul_train_reply(
        RESULTS["train"], NOW + 300,
        reply_to=SimpleNamespace(
            id=ROOT - 1, chat_id=CHAT, raw_text=SPECS["train"][0], sender_id=IDENTITY,
            date=datetime.fromtimestamp(NOW - 86400, timezone.utc),
        ), matched_family="second_soul_train", reply_context={
            "send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT - 1,
            "source": "manual_game_command", "server_event_at": NOW + 300,
        },
    ))
    assert env.identity == before


def test_manual_training_can_coexist_with_an_unfinished_ui_read(env):
    assert asyncio.run(second_soul.remember_second_soul_status_read(
        SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW),
        send_as_id=IDENTITY, identity_state=env.identity, account_id=ACCOUNT, requested_at=NOW,
    ))
    assert asyncio.run(deliver("train", context={
        "send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT,
        "source": "manual_game_command", "server_event_at": NOW + 1,
    }))
    assert env.identity["second_soul_phase"] == "cultivating"
    assert env.identity["second_soul_commands"]["status_read"]["status"] == "sent"
    env.send.assert_not_awaited()


def test_new_manual_train_in_another_chat_can_reuse_a_message_id(env):
    asyncio.run(dispatch("train"))
    assert asyncio.run(deliver("train"))
    state_module.set_game_group_route_config({"primary_group_id": CHAT - 1})
    assert asyncio.run(second_soul.handle_second_soul_train_reply(
        RESULTS["train"], NOW + 86401,
        reply_to=SimpleNamespace(
            id=ROOT, chat_id=CHAT - 1, raw_text=SPECS["train"][0], sender_id=IDENTITY,
            date=datetime.fromtimestamp(NOW + 86400, timezone.utc),
        ), matched_family="second_soul_train", reply_context={
            "send_as_id": IDENTITY, "chat_id": CHAT - 1, "root_msg_id": ROOT,
            "source": "manual_game_command", "server_event_at": NOW + 86401,
        },
    ))
    assert env.identity["second_soul_commands"]["train"]["chat_id"] == CHAT - 1


@pytest.mark.parametrize("bad_status", [[], {}])
def test_malformed_status_value_is_reported_without_crashing_scheduler(env, bad_status):
    asyncio.run(dispatch("train"))
    env.identity["second_soul_commands"]["train"]["status"] = bad_status
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 7200))
    assert env.identity["second_soul_last_error"]
    assert env.send.await_count == 1


def test_status_query_not_unlocked_reply_enters_long_backoff(env):
    asyncio.run(dispatch("status"))
    assert asyncio.run(deliver("status", text="\u4f60\u5c1a\u672a\u51dd\u7ec3\u7b2c\u4e8c\u5143\u795e\u3002"))
    assert env.identity["second_soul_phase"] == "not_unlocked"
    assert env.identity["second_soul_commands"]["status"]["status"] == "complete"
    assert env.identity["next_second_soul_time"] == NOW + 1 + second_soul.SECOND_SOUL_NOT_UNLOCKED_RETRY_SEC
    asyncio.run(second_soul.run_second_soul_scheduler(NOW + 86400))
    assert env.send.await_count == 1


def test_choice_admission_uses_warning_owner_not_callers_context(env):
    other_id = IDENTITY + 10
    state_module.set_identity_account(other_id, ACCOUNT)
    state_module.update_send_as_profile(other_id, username="SoulOther")
    state_module.get_identity_state(other_id)["second_soul_enabled"] = True
    env.identity["second_soul_commands"] = None
    asyncio.run(second_soul.handle_second_soul_heart_demon_warning_broadcast(
        WARNING.replace("SoulOwner", "SoulOther"), NOW, ROOT, event_chat_id=CHAT,
    ))
    env.send.assert_awaited_once()
    assert env.send.call_args.kwargs["send_as_id"] == other_id
    assert env.identity["second_soul_commands"] is None


def test_old_status_reply_cannot_roll_back_newer_return_broadcast(env):
    state_module.update_send_as_profile(IDENTITY, username="SoulOwner")
    asyncio.run(dispatch("status"))
    assert asyncio.run(second_soul.handle_second_soul_return_broadcast(RETURN.replace("91", "39"), NOW + 10))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(
        "status", now=NOW + 20,
        text=RESULTS["status"].replace("\u7a8d\u4e2d\u6e29\u517b", "\u53d7\u4f24"),
        context={"send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT, "server_event_at": NOW + 1},
    ))
    assert env.identity == before


def passive_result(monkeypatch, text, family, *, now=NOW + 100, root=ROOT, chat=CHAT, handled=False, verified=False):
    monkeypatch.setattr(passive_inbox, "save_state", Mock())
    monkeypatch.setattr(passive_inbox, "_mark_observed_passive_event", lambda *_a, **_kw: True)
    monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock())
    context = {
        "send_as_id": IDENTITY, "family": family, "chat_id": chat,
        "root_msg_id": root, "reply_to_msg_id": root, "routed_reply_handled": handled,
    }
    event = SimpleNamespace(id=ROOT + 1, chat_id=chat, date=datetime.fromtimestamp(NOW + 1, timezone.utc))
    if verified:
        return passive_inbox.handle_passive_module_card(app.from_telegram_event(event, text, context), now)
    return passive_inbox.handle_passive_module_card(text, now, reply_context=context, event=event, event_type="message")


@pytest.mark.parametrize("handled", [False, True])
def test_passive_duplicate_does_not_reapply_completed_training(env, monkeypatch, handled):
    asyncio.run(dispatch("train"))
    assert asyncio.run(deliver("train"))
    before = copy.deepcopy(env.identity)
    asyncio.run(passive_result(monkeypatch, RESULTS["train"], "second_soul_train", handled=handled))
    assert env.identity == before


@pytest.mark.parametrize("wrong", ["chat", "root", "missing"])
def test_passive_reply_cannot_bypass_owned_purge_with_an_unowned_panel(env, monkeypatch, wrong):
    asyncio.run(dispatch("purge"))
    before = copy.deepcopy(env.identity)
    asyncio.run(passive_result(
        monkeypatch, RESULTS["status"].replace("\u7a8d\u4e2d\u6e29\u517b", "\u4fee\u70bc\u4e2d"), "second_soul_status",
        root=0 if wrong == "missing" else ROOT + 9 if wrong == "root" else ROOT,
        chat=CHAT - 1 if wrong == "chat" else CHAT,
    ))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("verified", [False, True])
def test_passive_owned_reply_uses_same_completion_contract(env, monkeypatch, kind, verified):
    asyncio.run(dispatch(kind))
    assert asyncio.run(passive_result(monkeypatch, RESULTS[kind], f"second_soul_{kind}", verified=verified))
    assert env.identity["second_soul_commands"][kind]["status"] == "complete"
    assert env.identity["second_soul_commands"][kind]["reply_at"] == NOW + 1


def test_passive_completion_after_disable_does_not_lose_result_or_send(env, monkeypatch):
    asyncio.run(dispatch("train"))
    control._disable_second_soul_module_state()
    assert asyncio.run(passive_result(monkeypatch, RESULTS["train"], "second_soul_train", verified=True))
    assert env.identity["second_soul_commands"]["train"]["status"] == "complete"
    assert env.identity["second_soul_phase"] == "cultivating"
    assert not env.identity["second_soul_enabled"]
    assert env.send.await_count == 1


def test_passive_completion_audit_cannot_continue_into_deleted_identity(env, monkeypatch):
    asyncio.run(dispatch("train"))

    async def audit(*_args, **_kwargs):
        state_module.remove_identity(IDENTITY)

    env.audit.side_effect = audit
    assert asyncio.run(passive_result(monkeypatch, RESULTS["train"], "second_soul_train", verified=True))
    assert not state_module.has_identity(IDENTITY)
