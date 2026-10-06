import asyncio
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import events
from telethon.errors import RPCError
from telethon.tl.types import Message, MessageReplyHeader, PeerChannel, PeerUser

from model import app


def reply_event(exc, **overrides):
    fields = dict(
        chat_id=-1002083016447,
        id=1285001,
        sender_id=8816935632,
        reply_to=SimpleNamespace(reply_to_msg_id=1285000, reply_to_top_id=123),
        get_reply_message=AsyncMock(side_effect=exc),
        client=object(),
        raw_text="secret body https://example.invalid/?token=secret",
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def isolated(monkeypatch):
    mocks = SimpleNamespace(
        context=Mock(return_value={}),
        offline=Mock(),
        notify=AsyncMock(),
        send=AsyncMock(),
        save=Mock(),
        listener=Mock(return_value=1001),
    )
    monkeypatch.setattr(app, "get_reply_context", mocks.context)
    monkeypatch.setattr(app, "mark_account_offline", mocks.offline)
    monkeypatch.setattr(app, "is_account_session_error", lambda exc: False)
    monkeypatch.setattr(app, "_get_event_listener_account_id", mocks.listener)
    monkeypatch.setattr(app, "send_audit_log", mocks.notify)
    monkeypatch.setattr(app, "send_game_command", mocks.send)
    monkeypatch.setattr(app, "save_state", mocks.save)
    yield mocks
    mocks.notify.assert_not_awaited()
    mocks.send.assert_not_awaited()
    mocks.save.assert_not_called()


@pytest.mark.parametrize("chat_id", [-1002083016447, -1001680975844])
def test_rpc_failure_retains_exception_and_logs_only_header_context(isolated, chat_id):
    exc = RPCError(request=None, message="UNHANDLED_EXCEPTION", code=-504)
    original_args = exc.args
    event = reply_event(exc, chat_id=chat_id)
    with pytest.raises(RPCError) as caught:
        asyncio.run(app._resolve_event_reply(event))
    assert caught.value is exc
    assert exc.code == -504
    assert exc.args == original_args
    assert len(exc.__notes__) == 1
    note = exc.__notes__[0]
    assert note.startswith("reply_read_context ")
    for field in (
        f"chat_id={chat_id}", "message_id=1285001", "reply_to_msg_id=1285000",
        "topic_id=123", "sender_id=8816935632", "listener_account_id=1001",
        "event_kind=unknown",
    ):
        assert field in note
    assert "secret" not in note
    assert "https://" not in note
    # Existing outer handlers print format_exc; no second logging path is needed.
    rendered = "".join(traceback.format_exception(caught.value))
    assert note in rendered
    assert "_resolve_event_reply" in rendered
    event.get_reply_message.assert_awaited_once()
    isolated.context.assert_not_called()
    isolated.offline.assert_not_called()


@pytest.mark.parametrize("event_cls, kind", [
    (events.NewMessage.Event, "message"),
    (events.MessageEdited.Event, "edit"),
])
def test_real_event_type_classification(isolated, event_cls, kind):
    exc = RuntimeError("read failed")
    event = event_cls(Message(
        id=1285001, peer_id=PeerChannel(2083016447), from_id=PeerUser(8816935632),
        reply_to=MessageReplyHeader(reply_to_msg_id=1285000, reply_to_top_id=123),
        message="secret body",
    ))
    event.get_reply_message = AsyncMock(side_effect=exc)
    assert isinstance(event, event_cls)
    with pytest.raises(RuntimeError):
        asyncio.run(app._resolve_event_reply(event))
    assert f"event_kind={kind}" in exc.__notes__[0]
    assert "chat_id=-1002083016447" in exc.__notes__[0]
    assert "reply_to_msg_id=1285000" in exc.__notes__[0]


@pytest.mark.parametrize("bad_id", [None, "secret\nERROR", True, 1.5, [], {}, 2**100])
def test_malformed_ids_are_unknown_not_interpolated(isolated, bad_id):
    exc = RuntimeError("read failed")
    event = reply_event(
        exc, chat_id=bad_id, id=bad_id, sender_id=bad_id,
        reply_to=SimpleNamespace(reply_to_msg_id=bad_id, reply_to_top_id=bad_id),
    )
    isolated.listener.return_value = bad_id
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(app._resolve_event_reply(event))
    assert caught.value is exc
    assert exc.__notes__ == [
        "reply_read_context chat_id=0 message_id=0 reply_to_msg_id=0 "
        "topic_id=0 sender_id=0 listener_account_id=0 event_kind=unknown"
    ]


def test_missing_headers_do_not_borrow_current_chat_or_infer_owner(isolated):
    exc = RuntimeError("read failed")
    event = SimpleNamespace(get_reply_message=AsyncMock(side_effect=exc))
    isolated.listener.return_value = 0
    with pytest.raises(RuntimeError):
        asyncio.run(app._resolve_event_reply(event))
    assert exc.__notes__ == [
        "reply_read_context chat_id=0 message_id=0 reply_to_msg_id=0 "
        "topic_id=0 sender_id=0 listener_account_id=0 event_kind=unknown"
    ]


def test_diagnostic_failure_cannot_replace_original_failure(isolated):
    exc = RuntimeError("original failure")
    event = reply_event(exc)
    isolated.listener.side_effect = ValueError("broken diagnostic registry")
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(app._resolve_event_reply(event))
    assert caught.value is exc
    event.get_reply_message.assert_awaited_once()
    isolated.context.assert_not_called()


def test_cancellation_has_no_note_or_fallback(isolated):
    exc = asyncio.CancelledError()
    event = reply_event(exc)
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(app._resolve_event_reply(event))
    assert caught.value is exc
    assert not getattr(exc, "__notes__", [])
    isolated.listener.assert_not_called()
    isolated.context.assert_not_called()


def test_disconnect_branch_is_unchanged(isolated):
    exc = ConnectionError("Cannot send requests while disconnected")
    event = reply_event(exc)
    reply, context = asyncio.run(app._resolve_event_reply(event))
    assert reply.id == 1285000
    assert reply.raw_text == ""
    assert "reply_to_sender_id" not in context
    assert not getattr(exc, "__notes__", [])
    isolated.offline.assert_not_called()
    isolated.listener.assert_not_called()


def test_session_branch_marks_only_own_client_offline(isolated, monkeypatch):
    exc = RuntimeError("session invalid")
    event = reply_event(exc)
    monkeypatch.setattr(app, "is_account_session_error", lambda error: error is exc)
    monkeypatch.setattr(app, "get_all_clients", lambda: {1001: object(), 1002: event.client})
    reply, _context = asyncio.run(app._resolve_event_reply(event))
    assert reply.id == 1285000
    isolated.offline.assert_called_once_with(1002, str(exc))
    isolated.listener.assert_not_called()
    assert not getattr(exc, "__notes__", [])


def test_success_keeps_context_and_has_no_diagnostics(isolated):
    root = SimpleNamespace(id=1285000, sender_id=1001, raw_text=".status")
    event = reply_event(None)
    event.get_reply_message = AsyncMock(return_value=root)
    reply, context = asyncio.run(app._resolve_event_reply(event))
    assert reply is root
    assert context["reply_to_sender_id"] == 1001
    assert context["reply_to_command"] == ".status"
    isolated.listener.assert_not_called()
    isolated.offline.assert_not_called()


def test_existing_notes_preserved_and_identical_context_not_repeated(isolated):
    exc = RuntimeError("read failed")
    exc.add_note("earlier diagnostic")
    event = reply_event(exc)
    for _ in range(2):
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(app._resolve_event_reply(event))
        assert caught.value is exc
    assert exc.__notes__[0] == "earlier diagnostic"
    assert len(exc.__notes__) == 2


def test_note_storage_failure_preserves_exception(isolated):
    exc = RuntimeError("read failed")
    exc.__notes__ = None
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(app._resolve_event_reply(reply_event(exc)))
    assert caught.value is exc
    isolated.context.assert_not_called()


@pytest.mark.parametrize("kind", ["message", "edit"])
def test_outer_handler_prints_context_without_routing_failed_read(
    isolated, monkeypatch, capsys, kind,
):
    exc = RPCError(request=None, message="UNHANDLED_EXCEPTION", code=-504)
    event = reply_event(exc)
    for name in (
        "observe_red_packet_candidate", "capture_cave_public_entry_event",
    ):
        monkeypatch.setattr(app, name, AsyncMock())
    for name in (
        "_append_replica_group_message_log", "_append_replica_dispatch_group_message_log",
        "_claim_runtime_event", "_resolve_identity_sender_id",
    ):
        monkeypatch.setattr(app, name, Mock(return_value=False))
    for name in ("_append_game_group_message_log", "record_game_group_message", "observe_dungeon_quiet_text"):
        monkeypatch.setattr(app, name, Mock())
    monkeypatch.setattr(app, "_is_game_group_listener_event", lambda ev: True)
    monkeypatch.setattr(app, "_configured_game_group_ids", lambda: {event.chat_id})
    monkeypatch.setattr(app, "_is_game_bot_event", AsyncMock(return_value=True))
    bind = Mock()
    routed = AsyncMock()
    monkeypatch.setattr(app, "_bind_command_attempt_shadow", bind)
    monkeypatch.setattr(app, "_handle_routed_reply_event", routed)
    handler = app.on_message if kind == "message" else app.on_message_edited
    asyncio.run(handler(event))
    output = capsys.readouterr().out
    assert "RPCError" in output
    assert "reply_read_context chat_id=-1002083016447 message_id=1285001" in output
    assert "secret body" not in output
    event.get_reply_message.assert_awaited_once()
    bind.assert_not_called()
    routed.assert_not_awaited()
    isolated.context.assert_not_called()
