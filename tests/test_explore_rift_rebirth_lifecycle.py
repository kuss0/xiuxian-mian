import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, runtime
from model import state as state_module
from model.features import explore_rift


NOW = 1780000000.0
IDENTITY = 990600001
ACCOUNT = 7551
CHAT = -100600001
ROOT = 6001
SELECT = 6003
OPTIONS = (
    "\u4f60\u9762\u524d\u51fa\u73b0\u4e86\u4e09\u5177\u53ef\u4f9b\u593a\u820d\u7684\u8089\u8eab\uff1a\n"
    "1. \u3010\u593a\u820d \u6d4b\u8bd5\u4e00\u3011\n"
    " - \u7075\u6839: \u4f2a\u7075\u6839(\u91d1\u706b\u6c34\u571f)\n"
    " - \u547d\u9014: \u7a33\u59a5\u4e4b\u8eab\n"
)
SUCCESS = explore_rift.REBIRTH_SUCCESS_PREFIX + "\u4f60\u7684\u795e\u9b42\u4e0e\u65b0\u8089\u8eab\u5b8c\u7f8e\u878d\u5408\u3002"
INTACT = explore_rift.REBIRTH_BODY_INTACT_PREFIX
SEARCHING = explore_rift.REBIRTH_SEARCHING_PREFIX
WEAK = explore_rift.REBIRTH_WEAK_PREFIX + "\uff0c1\u5c0f\u65f6"


def operation():
    return state_module.state.get("explore_rift_rebirth_operation", {})


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_group_route_config({"primary_group_id": CHAT})
    state_module.set_game_bot_ids([880600001])
    identity = state_module.get_identity_state(IDENTITY)
    identity.update(explore_rift_enabled=True, explore_rift_rebirth_required=True)

    async def receipt(command, **kwargs):
        return SimpleNamespace(
            id=ROOT if command == explore_rift.CMD_REBIRTH_REQUEST else SELECT,
            chat_id=CHAT, sent_at=operation().get("started_at", NOW),
        )

    send = AsyncMock(side_effect=receipt)
    audit = AsyncMock()
    monkeypatch.setattr(explore_rift, "send_game_command", send)
    monkeypatch.setattr(explore_rift, "send_audit_log", audit)
    monkeypatch.setattr(explore_rift, "console_log", Mock())
    monkeypatch.setattr(explore_rift, "save_state", Mock(return_value=True))
    monkeypatch.setattr(explore_rift, "classify_game_send_block", lambda *_: {"status": "unknown", "code": "send_timeout"})
    monkeypatch.setattr(explore_rift.time, "time", lambda: NOW)
    monkeypatch.setattr(explore_rift, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runtime, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "rebirth.db"))
    with state_module.use_identity(IDENTITY):
        yield SimpleNamespace(identity=identity, send=send, audit=audit)
    explore_rift._EXPLORE_RIFT_LOCKS.clear()
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def register(command, op_id, *, msg_id=ROOT, at=NOW, chat=CHAT, track=False):
    return runtime._finalize_game_command_sent(
        command, msg_id=msg_id, sent_at=at, send_started_at=at,
        send_as_id=IDENTITY, game_group_id=chat, topic_id=0, track=track, max_retry=0,
        send_intent={"source_module": "\u63a2\u5bfb\u88c2\u7f1d", "op_id": op_id},
    )


def deliver(text=OPTIONS, *, root=ROOT, at=NOW + 1, command=None, context=None):
    command = command or (explore_rift.CMD_REBIRTH_REQUEST if root == ROOT else f"{explore_rift.CMD_REBIRTH_SELECT_PREFIX} 1")
    return explore_rift.handle_explore_rift_reply(
        text, at + 1, reply_to=SimpleNamespace(id=root, chat_id=CHAT, raw_text=command),
        matched_family="explore_rift", result_msg_id=root + 1,
        reply_context={
            "send_as_id": IDENTITY, "account_id": ACCOUNT, "chat_id": CHAT,
            "root_msg_id": root, "reply_to_msg_id": root, "msg_id": root + 1,
            "server_event_at": at, "processed_at": at + 1, "event_type": "edit",
            **(context or {}),
        },
    )


def run(now=NOW):
    return asyncio.run(explore_rift.run_explore_rift_scheduler(now))


@pytest.mark.parametrize("step", ["request", "select"])
def test_intent_is_saved_before_transport_and_cancellation_survives_reload(env, monkeypatch, step):
    if step == "select":
        run()
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)

    async def cancel(command, **kwargs):
        assert operation()["op_id"] == kwargs["op_id"]
        assert operation()["status"] == "sending"
        assert kwargs["operation_check"]()
        assert persistence.load_state()
        assert operation()["op_id"] == kwargs["op_id"]
        raise asyncio.CancelledError()

    env.send.side_effect = cancel
    with pytest.raises(asyncio.CancelledError):
        if step == "request":
            run()
        else:
            asyncio.run(deliver())
    assert persistence.load_state()
    assert operation()["status"] in {"sending", "unknown"}
    calls = env.send.await_count
    run(NOW + 86400)
    assert env.send.await_count == calls


def test_unknown_request_is_not_retried_or_blind_selected(env):
    env.send.side_effect = None
    env.send.return_value = None
    run()
    run(NOW + 1)
    run(NOW + 86400)
    assert env.send.await_count == 1
    assert env.identity["explore_rift_rebirth_required"]


def test_definitely_unsent_request_obeys_backoff(env, monkeypatch):
    monkeypatch.setattr(explore_rift, "classify_game_send_block", lambda *_: {"status": "unsent", "code": "send_queue_timeout"})
    env.send.side_effect = None
    env.send.return_value = None
    run()
    run(NOW + 1)
    assert env.send.await_count == 1
    run(NOW + explore_rift.RETRY_MAX_SEC + 1)
    assert env.send.await_count == 2


def test_unsaved_operation_never_dispatches(env, monkeypatch):
    monkeypatch.setattr(explore_rift, "save_state", Mock(return_value=False))
    run()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["module", "global", "identity", "manual", "weak", "restored"])
def test_request_admission_respects_current_controls(env, change):
    if change == "module":
        env.identity["explore_rift_enabled"] = False
    elif change == "global":
        state_module.set_global_enabled(False)
    elif change == "identity":
        state_module.set_identity_enabled(IDENTITY, False)
    elif change == "manual":
        env.identity["explore_rift_manual_required"] = True
    elif change == "weak":
        env.identity["explore_rift_nascent_escape_weak_until"] = NOW + 3600
    else:
        env.identity["explore_rift_rebirth_required"] = False
        env.identity["explore_rift_enabled"] = False
    run()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["module", "global", "identity", "manual", "weak", "account", "restored"])
def test_queued_request_cannot_execute_invalidated_work(env, monkeypatch, change):
    monkeypatch.setattr(explore_rift, "classify_game_send_block", lambda *_: {"status": "unsent", "code": "operation_invalidated"})

    async def queued(command, **kwargs):
        assert kwargs["operation_check"]()
        if change == "module":
            env.identity["explore_rift_enabled"] = False
        elif change == "global":
            state_module.set_global_enabled(False)
        elif change == "identity":
            state_module.set_identity_enabled(IDENTITY, False)
        elif change == "manual":
            env.identity["explore_rift_manual_required"] = True
        elif change == "weak":
            env.identity["explore_rift_nascent_escape_weak_until"] = NOW + 3600
        elif change == "account":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        else:
            env.identity["explore_rift_rebirth_required"] = False
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = queued
    run()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "new_operation"])
def test_transport_return_cannot_overwrite_replaced_owner_or_work(env, change):
    expected = {}

    async def changed(command, **kwargs):
        if change in {"removed", "replaced"}:
            state_module.remove_identity(IDENTITY)
            target = IDENTITY if change == "replaced" else IDENTITY + 1
            state_module.set_identity_account(target, ACCOUNT)
        else:
            target = IDENTITY
            if change == "rebound":
                state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
            else:
                operation()["op_id"] = "new-operation"
        expected.update(target=target, state=copy.deepcopy(state_module.get_identity_state(target)))
        return SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW)

    env.send.side_effect = changed
    run()
    assert state_module.get_identity_state(expected["target"]) == expected["state"]


def test_early_restoration_is_not_overwritten_by_request_receipt(env):
    async def early(command, **kwargs):
        receipt = register(command, kwargs.get("op_id", ""))
        assert await deliver(INTACT)
        return receipt

    env.send.side_effect = early
    run()
    assert not env.identity["explore_rift_rebirth_required"]
    assert env.identity["explore_rift_rebirth_phase"] == "restored"
    assert not env.identity["explore_rift_rebirth_request_msg_id"]


@pytest.mark.parametrize("receipt", [None, SimpleNamespace(id=SELECT, chat_id=CHAT, sent_at=NOW + 1)])
def test_early_selection_success_is_not_overwritten_by_receipt_or_unknown(env, receipt):
    run()

    async def early(command, **kwargs):
        register(command, kwargs.get("op_id", ""), msg_id=SELECT, at=NOW + 1)
        assert await deliver(SUCCESS, root=SELECT, at=NOW + 2)
        return receipt

    env.send.side_effect = early
    assert asyncio.run(deliver())
    assert not env.identity["explore_rift_rebirth_required"]
    assert env.identity["explore_rift_rebirth_phase"] == "restored"
    assert not env.identity["explore_rift_rebirth_select_msg_id"]


def test_duplicate_options_never_repeat_selection_even_after_timeout(env):
    run()
    assert asyncio.run(deliver())
    assert asyncio.run(deliver(at=NOW + 2))
    assert env.send.await_count == 2
    run(NOW + 86400)
    assert env.send.await_count == 2
    assert env.identity["explore_rift_rebirth_select_msg_id"] == SELECT


@pytest.mark.parametrize("bad", [
    {"root_msg_id": ROOT + 10, "reply_to_msg_id": ROOT + 10, "msg_id": ROOT + 11},
    {"server_event_at": NOW - 100}, {"account_id": ACCOUNT + 1}, {"chat_id": CHAT - 1},
])
def test_old_or_unrelated_rebirth_reply_cannot_select_or_restore(env, bad):
    run()
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(context=bad))
    assert env.identity == before
    assert env.send.await_count == 1


def test_rebirth_wording_on_a_rift_command_is_not_a_rebirth_request(env):
    run()
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(INTACT, command=explore_rift.CMD_EXPLORE_RIFT))
    assert env.identity == before


def test_paused_options_are_recorded_but_not_selected_until_resumed(env):
    run()
    env.identity["explore_rift_enabled"] = False
    assert asyncio.run(deliver())
    assert env.send.await_count == 1
    env.identity["explore_rift_enabled"] = True
    run(NOW + 2)
    assert env.send.await_count == 2


def test_confirmed_request_uses_configured_blind_choice_once(env):
    env.identity["explore_rift_rebirth_blind_index"] = 2
    run()
    run(NOW + explore_rift.EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC + 1)
    assert env.send.await_count == 2
    assert env.send.await_args.args == (f"{explore_rift.CMD_REBIRTH_SELECT_PREFIX} 2",)
    run(NOW + 86400)
    assert env.send.await_count == 2


def rift_result(text, *, root=ROOT - 10, at=NOW - 60):
    return deliver(text, root=root, at=at, command=explore_rift.CMD_EXPLORE_RIFT)


@pytest.mark.parametrize("escaped", [False, True])
def test_new_death_after_restoration_starts_a_distinct_rebirth_cycle(env, escaped):
    assert asyncio.run(rift_result(explore_rift.EXPLORE_RIFT_FATAL_TITLE))
    run()
    run(NOW + 1)
    assert asyncio.run(deliver(INTACT, at=NOW + 2))
    previous_id = operation()["op_id"]
    assert not env.identity["explore_rift_rebirth_required"]
    text = explore_rift.EXPLORE_RIFT_ESCAPE_WEAK_TITLE + "\n1\u5c0f\u65f6" if escaped else explore_rift.EXPLORE_RIFT_FATAL_TITLE
    assert asyncio.run(rift_result(text, root=ROOT + 10, at=NOW + 100))
    due = env.identity["explore_rift_nascent_escape_weak_until"] if escaped else env.identity["explore_rift_fatal_confirm_due_at"]
    run(due + 1)
    run(due + 2)
    assert env.send.await_count == 2
    assert operation()["op_id"] != previous_id
    assert operation()["kind"] == "request"
    assert operation()["status"] == "sent"


@pytest.mark.parametrize("different_root", [False, True])
def test_late_death_evidence_cannot_reopen_an_already_restored_body(env, different_root):
    assert asyncio.run(rift_result(explore_rift.EXPLORE_RIFT_FATAL_TITLE))
    run()
    run(NOW + 1)
    assert asyncio.run(deliver(INTACT, at=NOW + 2))
    expected_operation = copy.deepcopy(operation())
    assert asyncio.run(rift_result(
        explore_rift.EXPLORE_RIFT_ESCAPE_WEAK_TITLE + "\n1\u5c0f\u65f6",
        root=ROOT - 5 if different_root else ROOT - 10,
        at=NOW - 5 if different_root else NOW + 20,
    ))
    assert not env.identity["explore_rift_rebirth_required"]
    assert env.identity["explore_rift_rebirth_phase"] == "restored"
    assert operation() == expected_operation


def test_replayed_searching_ack_does_not_starve_the_request_deadline(env, monkeypatch):
    run()
    assert asyncio.run(deliver(SEARCHING))
    monkeypatch.setattr(explore_rift, "_find_owned_rift_log_reply", lambda *_args, **_kwargs: {
        "text": SEARCHING, "ts": NOW + 1, "server_event_at": NOW + 1,
        "msg_id": ROOT + 1, "root_msg_id": ROOT, "chat_id": CHAT, "event_type": "edit",
    })
    run(NOW + explore_rift.EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC + 1)
    assert env.send.await_count == 2
    assert operation()["kind"] == "select"


@pytest.mark.parametrize("bad", [
    {"account_id": float(ACCOUNT)}, {"identity_id": float(IDENTITY)},
    {"reply": {"at": []}}, {"reply": "corrupt"},
    {"sent_at": True}, {"msg_id": 0}, {"chat_id": 0},
])
def test_malformed_request_evidence_is_not_repaired_by_guessing(env, bad):
    run()
    operation().update(bad)
    expected = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver())
    run(NOW + 86400)
    assert env.identity == expected
    assert env.send.await_count == 1


@pytest.mark.parametrize("bad", [
    {"index": True}, {"request": {"msg_id": ROOT, "chat_id": CHAT}},
    {"request": "corrupt"}, {"request": None},
])
def test_malformed_selection_parent_cannot_complete_rebirth(env, bad):
    run()
    assert asyncio.run(deliver())
    operation().update(bad)
    expected = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(SUCCESS, root=SELECT, at=NOW + 2))
    assert env.identity == expected


def test_unknown_no_id_rebirth_holds_tianxing_and_survives_ui_initialization(env):
    env.send.side_effect = None
    env.send.return_value = None
    run()
    env.identity["explore_rift_rebirth_required"] = False
    assert explore_rift.has_unresolved_explore_rift()
    expected = copy.deepcopy(operation())
    explore_rift.schedule_explore_rift_initial_check(NOW + 10)
    assert operation() == expected
    assert explore_rift.has_unresolved_explore_rift()


def test_failed_result_save_is_retried_before_duplicate_is_acknowledged(env, monkeypatch):
    run()
    save = Mock(return_value=False)
    monkeypatch.setattr(explore_rift, "save_state", save)
    assert not asyncio.run(deliver(INTACT))
    save.reset_mock()
    save.return_value = True
    assert asyncio.run(deliver(INTACT))
    save.assert_called()
    assert not env.identity["explore_rift_rebirth_required"]


def test_unexpected_result_save_exception_rolls_back_rebirth_transition(env, monkeypatch):
    run()
    expected = copy.deepcopy(env.identity)
    monkeypatch.setattr(explore_rift, "save_state", Mock(side_effect=RuntimeError("save interrupted")))
    with pytest.raises(RuntimeError, match="save interrupted"):
        asyncio.run(deliver(INTACT))
    assert env.identity == expected


def test_late_receipt_uses_actual_send_deadline_before_blind_selection(env):
    env.send.side_effect = None
    env.send.return_value = None
    run()
    register(explore_rift.CMD_REBIRTH_REQUEST, operation()["op_id"], at=NOW + 20)
    run(NOW + explore_rift.EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC + 1)
    assert env.send.await_count == 1
    assert env.identity["explore_rift_rebirth_request_msg_id"] == ROOT
    assert operation()["status"] == "sent"
    run(NOW + 20 + explore_rift.EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC + 1)
    assert env.send.await_count == 2


@pytest.mark.parametrize("step", ["request", "select"])
@pytest.mark.parametrize("fields", [
    {"id": True}, {"id": float(ROOT)}, {"id": 0},
    {"chat_id": 0}, {"chat_id": str(CHAT)}, {"sent_at": True},
    {"sent_at": float("nan")}, {"sent_at": NOW - 20}, {"sent_at": NOW + 86400},
])
def test_invalid_transport_receipt_is_unknown_and_never_retried(env, step, fields):
    if step == "select":
        run()
    env.send.side_effect = None
    env.send.return_value = SimpleNamespace(**{
        "id": ROOT if step == "request" else SELECT,
        "chat_id": CHAT, "sent_at": NOW + (step == "select"), **fields,
    })
    if step == "select":
        assert asyncio.run(deliver())
    else:
        run()
    assert operation()["status"] == "unknown"
    calls = env.send.await_count
    run(NOW + 86400)
    assert env.send.await_count == calls
    assert operation()["op_id"]


@pytest.mark.parametrize("step", ["request", "select"])
def test_transport_exception_retains_operation_for_late_receipt(env, monkeypatch, step):
    if step == "select":
        run()
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
    env.send.side_effect = ConnectionError("lost transport receipt")
    with pytest.raises(ConnectionError):
        if step == "request":
            run()
        else:
            asyncio.run(deliver())
    assert persistence.load_state()
    assert operation()["status"] == "unknown"
    root = ROOT if step == "request" else SELECT
    register(operation()["command"], operation()["op_id"], msg_id=root, at=NOW + 1)
    assert asyncio.run(deliver(INTACT if step == "request" else SUCCESS, root=root, at=NOW + 2))
    assert persistence.load_state()
    assert operation()["status"] == "complete"
    assert not state_module.state["explore_rift_rebirth_required"]


@pytest.mark.parametrize("pause", ["global", "identity", "module"])
def test_paused_selection_can_finish_without_reenabling_any_control(env, pause):
    run()
    assert asyncio.run(deliver())
    if pause == "global":
        state_module.set_global_enabled(False)
    elif pause == "identity":
        state_module.set_identity_enabled(IDENTITY, False)
    else:
        env.identity["explore_rift_enabled"] = False
    assert asyncio.run(deliver(SUCCESS, root=SELECT, at=NOW + 2))
    assert not env.identity["explore_rift_rebirth_required"]
    assert env.send.await_count == 2
    if pause == "global":
        assert not state_module.get_global_enabled()
    elif pause == "identity":
        assert not state_module.get_identity_enabled(IDENTITY)
    else:
        assert not env.identity["explore_rift_enabled"]


def test_selection_queued_with_old_config_is_replanned_only_when_definitely_unsent(env, monkeypatch):
    run()
    options = OPTIONS + (
        "2. \u3010\u593a\u820d \u6d4b\u8bd5\u4e8c\u3011\n"
        " - \u7075\u6839: \u5f02\u7075\u6839(\u96f7)\n"
        " - \u547d\u9014: \u627f\u8109\u4e4b\u8eab\n"
    )

    async def reconfigure(command, **kwargs):
        assert kwargs["operation_check"]()
        env.identity.update(explore_rift_rebirth_choice_mode="root_first", explore_rift_rebirth_preferred_root_type="\u5f02\u7075\u6839")
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = reconfigure
    monkeypatch.setattr(explore_rift, "classify_game_send_block", lambda *_: {"status": "unsent", "code": "operation_invalidated"})
    assert asyncio.run(deliver(options))
    assert operation()["status"] == "unsent"
    run(NOW + 2)
    assert env.send.await_count == 2

    async def resumed(command, **kwargs):
        assert kwargs["operation_check"]()
        return SimpleNamespace(id=SELECT, chat_id=CHAT, sent_at=operation()["started_at"])

    env.send.side_effect = resumed
    run(NOW + explore_rift.RETRY_MAX_SEC + 10)
    assert env.send.await_count == 3
    assert env.send.await_args.args == (f"{explore_rift.CMD_REBIRTH_SELECT_PREFIX} 2",)
    assert operation()["status"] == "sent"


def test_selection_unknown_cannot_adopt_same_operation_in_another_chat(env):
    run()
    env.send.side_effect = None
    env.send.return_value = None
    assert asyncio.run(deliver())
    state_module.set_game_group_route_config({"enabled": True, "primary_group_id": CHAT, "backup_group_ids": [CHAT - 1]})
    register(operation()["command"], operation()["op_id"], msg_id=SELECT, chat=CHAT - 1, at=NOW + 2)
    run(NOW + 86400)
    assert operation()["status"] == "unknown"
    assert not operation()["msg_id"]
    assert operation()["chat_id"] == CHAT
    assert env.send.await_count == 2


@pytest.mark.parametrize("failure", [asyncio.CancelledError, RuntimeError])
def test_restoration_is_durable_before_notification_failure(env, monkeypatch, failure):
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
    run()
    env.audit.side_effect = failure()
    with pytest.raises(failure):
        asyncio.run(deliver(INTACT))
    assert persistence.load_state()
    assert operation()["status"] == "complete"
    assert not state_module.state["explore_rift_rebirth_required"]
    env.audit.reset_mock()
    assert asyncio.run(deliver(INTACT))
    env.audit.assert_not_awaited()


def test_rebirth_column_migrates_from_old_sqlite_schema(env, monkeypatch):
    assert persistence.save_state()
    conn = persistence.get_db_conn()
    conn.execute("ALTER TABLE identity_runtime_state DROP COLUMN explore_rift_rebirth_operation")
    conn.commit()
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
    run()
    assert persistence.load_state()
    assert operation()["status"] == "sent"


def test_real_searching_log_replay_reaches_timeout_instead_of_repeating_forever(env, monkeypatch):
    from model import app_message_log

    monkeypatch.setattr(app_message_log, "cleanup_message_logs", Mock())
    run()
    register(operation()["command"], operation()["op_id"])
    at = NOW + 1
    event = SimpleNamespace(
        id=ROOT + 1, chat_id=CHAT, sender_id=880600001, raw_text=SEARCHING,
        date=datetime.fromtimestamp(at, timezone.utc), reply_to=SimpleNamespace(reply_to_msg_id=ROOT),
    )
    _, entry = app_message_log._build_message_log_payload(event)
    entry["ts"] = datetime.fromtimestamp(at, explore_rift.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")
    path = Path(explore_rift.MESSAGES_DIR) / (datetime.fromtimestamp(at, explore_rift.TZ_LOCAL).strftime("%Y-%m-%d") + ".log")
    assert app_message_log._write_message_log(str(path), entry)
    assert asyncio.run(deliver(SEARCHING))
    run(NOW + explore_rift.EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC + 1)
    assert env.send.await_count == 2
    assert operation()["kind"] == "select"


@pytest.mark.parametrize("path", ["routed", "log"])
@pytest.mark.parametrize("kind", ["message", "edit"])
def test_native_selection_result_closes_once_after_pause_and_reload(env, monkeypatch, path, kind):
    from model import app, app_message_log, app_runtime

    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
    run()
    assert asyncio.run(deliver())
    register(operation()["command"], operation()["op_id"], msg_id=SELECT, at=NOW + 1, track=True)
    state_module.state["explore_rift_enabled"] = False
    assert persistence.save_state()
    assert persistence.load_state()
    at = NOW + 2
    event = SimpleNamespace(
        id=SELECT + 1, chat_id=CHAT, sender_id=880600001, raw_text=SUCCESS,
        date=datetime.fromtimestamp(at, timezone.utc),
        edit_date=datetime.fromtimestamp(at, timezone.utc) if kind == "edit" else None,
        reply_to=SimpleNamespace(reply_to_msg_id=SELECT),
    )
    reply = SimpleNamespace(id=SELECT, chat_id=CHAT, raw_text=operation()["command"])
    context = {"send_as_id": IDENTITY, "root_msg_id": SELECT, "reply_to_msg_id": SELECT, "family": "explore_rift"}
    if path == "log":
        _, entry = app_message_log._build_message_log_payload(event, event_type=kind)
        entry["ts_epoch"] = at
        task = state_module.state["pending_tasks"][(CHAT, SELECT)]

    def replay():
        if path == "routed":
            return asyncio.run(app._handle_routed_reply_event(event, SUCCESS, at + 1, reply, context, event_kind=kind))
        return asyncio.run(app._replay_pending_log_replies(IDENTITY, SELECT, task, [entry], at + 1))

    assert replay()
    assert not state_module.state["pending_tasks"]
    assert persistence.load_state()
    assert operation()["status"] == "complete"
    assert not state_module.state["explore_rift_rebirth_required"]
    assert not state_module.state["explore_rift_enabled"]
    completed = copy.deepcopy(state_module.get_identity_state(IDENTITY))
    completed.pop("pending_tasks")
    calls = env.audit.await_count
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    replay()
    replayed = copy.deepcopy(state_module.get_identity_state(IDENTITY))
    assert not replayed.pop("pending_tasks")
    assert replayed == completed
    assert persistence.save_state()
    assert persistence.load_state()
    assert not state_module.state["pending_tasks"]
    assert env.audit.await_count == calls
    assert env.send.await_count == 2


def test_completed_rebirth_reply_can_cleanup_a_reloaded_pending_while_module_is_off(env):
    run()
    assert asyncio.run(deliver(INTACT))
    env.identity["explore_rift_enabled"] = False
    expected = copy.deepcopy(env.identity)
    assert asyncio.run(deliver(INTACT))
    assert env.identity == expected


@pytest.mark.parametrize("selection_status", ["sent", "unknown", "queued"])
def test_server_auto_choice_on_parent_request_completes_the_whole_chain(env, selection_status):
    run()
    auto_choice = explore_rift.REBIRTH_AUTO_SELECT_PREFIX + "\u5df2\u5b8c\u6210\u593a\u820d\u91cd\u751f"

    async def early(command, **kwargs):
        assert kwargs["operation_check"]()
        assert await deliver(auto_choice, at=NOW + 2)
        assert not kwargs["operation_check"]()
        return None

    if selection_status == "queued":
        env.send.side_effect = early
    elif selection_status == "unknown":
        env.send.side_effect = None
        env.send.return_value = None
    assert asyncio.run(deliver())
    if selection_status != "queued":
        assert asyncio.run(deliver(auto_choice, at=NOW + 2))
    assert operation()["status"] == "complete"
    assert explore_rift._rebirth_operation() is not None
    assert not env.identity["explore_rift_rebirth_required"]
    assert persistence.save_state()
    assert persistence.load_state()
    assert operation()["status"] == "complete"
    calls = env.send.await_count
    assert asyncio.run(deliver(auto_choice, at=NOW + 2))
    assert env.send.await_count == calls
