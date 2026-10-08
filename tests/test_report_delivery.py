import io
import json
import traceback
import urllib.error
import urllib.parse
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import miniapp_daily_report as daily
from tools import storage_bag_report as bag


ENV = {"LOG_BOT_TOKEN": "123:secret-token", "LOG_GROUP_ID": "-1001"}
ACK = {"ok": True, "result": {"message_id": 42, "chat": {"id": -1001}}}
REPORT_TEXT = "private report"


class Response(io.BytesIO):
    status = 200


@pytest.fixture(params=["daily", "bag"])
def sender(request, monkeypatch):
    monkeypatch.delenv("LOG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_GROUP_ID", raising=False)
    monkeypatch.setattr(daily, "_load_env_file", lambda _path: ENV)
    monkeypatch.setattr(bag, "load_env_file", lambda _path: ENV)
    if request.param == "daily":
        return lambda: daily.send_log_group(REPORT_TEXT, Path("unused.env"))
    return lambda: bag.send_log_group_chunks([REPORT_TEXT, "later chunk"], Path("unused.env"))


@pytest.mark.parametrize("payload", [
    {}, {"ok": True}, {"ok": 1, "result": ACK["result"]},
    {"ok": False, "error_code": 400},
    {"ok": True, "result": {"message_id": True, "chat": {"id": -1001}}},
    {"ok": True, "result": {"message_id": 0, "chat": {"id": -1001}}},
    {"ok": True, "result": {"message_id": 42, "chat": {"id": -1002}}},
    {"ok": True, "result": {"message_id": 42, "chat": {"id": "-1001"}}},
    {"ok": True, "result": []}, [], None,
    b"<html>private report</html>", b'{"ok":', b"\xff",
    b'{"ok":false,"ok":true,"result":{"message_id":42,"chat":{"id":-1001}}}',
    b"x" * (65536 + 1),
], ids=lambda payload: type(payload).__name__)
def test_report_stops_on_unknown_receipt(sender, monkeypatch, payload):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    response = Response(raw)
    opener = Mock(return_value=response)
    monkeypatch.setattr(daily.urllib.request, "urlopen", opener)
    with pytest.raises(RuntimeError, match="unknown") as exc:
        sender()
    opener.assert_called_once()
    assert opener.call_args.kwargs["timeout"] == 20
    assert response.closed
    assert "private report" not in str(exc.value)


@pytest.mark.parametrize("code,outcome", [(400, "unconfirmed"), (403, "unconfirmed"),
                                           (429, "unconfirmed"), (502, "unknown")])
def test_report_http_error_is_sanitized_and_never_retried(sender, monkeypatch, code, outcome):
    body = io.BytesIO(json.dumps({"ok": False, "error_code": code, "description": "private report"}).encode())
    error = urllib.error.HTTPError("https://api.telegram.org/bot123:secret-token/sendMessage", code, "private report", {}, body)
    opener = Mock(side_effect=error)
    monkeypatch.setattr(daily.urllib.request, "urlopen", opener)
    with pytest.raises(RuntimeError, match=outcome) as exc:
        sender()
    rendered = "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
    assert "secret-token" not in rendered and "private report" not in rendered
    assert body.closed
    opener.assert_called_once()


@pytest.mark.parametrize("error", [TimeoutError("secret-token"), urllib.error.URLError("secret-token")])
def test_report_transport_failure_is_unknown(sender, monkeypatch, error):
    opener = Mock(side_effect=error)
    monkeypatch.setattr(daily.urllib.request, "urlopen", opener)
    with pytest.raises(RuntimeError, match="unknown") as exc:
        sender()
    assert "secret-token" not in "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
    opener.assert_called_once()


def test_daily_returns_only_verified_receipt(monkeypatch):
    monkeypatch.delenv("LOG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_GROUP_ID", raising=False)
    monkeypatch.setattr(daily, "_load_env_file", lambda _path: ENV)
    response = Response(json.dumps(ACK).encode())
    opener = Mock(return_value=response)
    monkeypatch.setattr(daily.urllib.request, "urlopen", opener)
    receipt = daily.send_log_group("private report", Path("unused.env"))
    assert receipt.outcome == "confirmed" and receipt.message_id == 42
    opener.assert_called_once()
    assert response.closed


def test_bag_stops_after_confirmed_prefix_and_unknown_chunk(monkeypatch):
    monkeypatch.delenv("LOG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_GROUP_ID", raising=False)
    monkeypatch.setattr(bag, "load_env_file", lambda _path: ENV)
    responses = [Response(json.dumps(ACK).encode()), Response(b"{}")]
    opener = Mock(side_effect=responses)
    monkeypatch.setattr(bag.request, "urlopen", opener)
    with pytest.raises(RuntimeError, match=r"chunk=2/3.*confirmed=1/3.*unknown"):
        bag.send_log_group_chunks(["one", "two", "three"], Path("unused.env"))
    assert opener.call_count == 2
    assert all(response.closed for response in responses)


@pytest.mark.parametrize("actual_topic", [None, 44, "43", True])
def test_bag_requires_requested_topic_in_ack(monkeypatch, actual_topic):
    monkeypatch.delenv("LOG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_GROUP_ID", raising=False)
    monkeypatch.setattr(bag, "load_env_file", lambda _path: ENV)
    ack = {"ok": True, "result": {**ACK["result"], "message_thread_id": actual_topic}}
    response = Response(json.dumps(ack).encode())
    opener = Mock(return_value=response)
    monkeypatch.setattr(bag.request, "urlopen", opener)
    with pytest.raises(RuntimeError, match="unknown"):
        bag.send_log_group_chunks(["one", "two"], Path("unused.env"), topic_id=43)
    opener.assert_called_once()
    assert response.closed


def test_bag_all_confirmed_chunks_preserve_payload_and_topic(monkeypatch, capsys):
    monkeypatch.delenv("LOG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_GROUP_ID", raising=False)
    monkeypatch.setattr(bag, "load_env_file", lambda _path: ENV)
    responses = [Response(json.dumps({"ok": True, "result": {
        "message_id": number, "chat": {"id": -1001}, "message_thread_id": 43,
    }}).encode()) for number in (42, 44)]
    opener = Mock(side_effect=responses)
    monkeypatch.setattr(bag.request, "urlopen", opener)
    assert bag.send_log_group_chunks(["one", "two"], Path("unused.env"), topic_id=43) is None
    assert opener.call_count == 2
    for call, text in zip(opener.call_args_list, ["one", "two"]):
        req = call.args[0]
        assert req.get_method() == "POST"
        assert urllib.parse.parse_qs(req.data.decode()) == {
            "chat_id": ["-1001"], "text": [text], "disable_web_page_preview": ["true"],
            "message_thread_id": ["43"],
        }
        assert call.kwargs == {"timeout": 20}
    assert all(response.closed for response in responses)
    output = capsys.readouterr().out
    assert "chunk=1/2: log bot ok: message_id=42" in output
    assert "chunk=2/2: log bot ok: message_id=44" in output
    assert "one" not in output and "two" not in output


@pytest.mark.parametrize("configured", [{}, {"LOG_BOT_TOKEN": "token"}, {"LOG_GROUP_ID": "-1001"}])
def test_missing_configuration_stops_before_network(sender, monkeypatch, configured):
    monkeypatch.setattr(daily, "_load_env_file", lambda _path: configured)
    monkeypatch.setattr(bag, "load_env_file", lambda _path: configured)
    opener = Mock()
    monkeypatch.setattr(daily.urllib.request, "urlopen", opener)
    with pytest.raises(RuntimeError):
        sender()
    opener.assert_not_called()


def test_report_read_is_bounded(sender, monkeypatch):
    response = Mock()
    response.status = 200
    response.read.return_value = b"x" * (65536 + 1)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock(return_value=response)
    monkeypatch.setattr(daily.urllib.request, "urlopen", opener)
    with pytest.raises(RuntimeError, match="oversized"):
        sender()
    response.read.assert_called_once_with(65536 + 1)
    response.__exit__.assert_called_once()
    opener.assert_called_once()


@pytest.mark.parametrize("http_error", [False, True])
def test_report_closes_stream_on_read_failure(sender, monkeypatch, http_error):
    class BrokenResponse(Response):
        def read(self, _limit):
            raise TimeoutError("secret-token")

    response = BrokenResponse()
    if http_error:
        error = urllib.error.HTTPError("secret-token", 403, "private", {}, response)
        opener = Mock(side_effect=error)
    else:
        opener = Mock(return_value=response)
    monkeypatch.setattr(daily.urllib.request, "urlopen", opener)
    with pytest.raises(RuntimeError, match="unknown: TimeoutError") as exc:
        sender()
    assert "secret-token" not in "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
    assert response.closed
    opener.assert_called_once()


def test_daily_main_is_offline_without_explicit_flag(monkeypatch, capsys):
    monkeypatch.setattr(daily, "parse_args", lambda: Namespace(
        day="2026-10-06", capture_dir="unused", fishing_db=None, send_log_group=False, env_file="unused",
    ))
    monkeypatch.setattr(daily, "build_report", lambda *_args, **_kwargs: REPORT_TEXT)
    sender = Mock()
    monkeypatch.setattr(daily, "send_log_group", sender)
    daily.main()
    sender.assert_not_called()
    assert capsys.readouterr().out.strip() == REPORT_TEXT


def test_daily_main_never_prints_success_after_unknown(monkeypatch, capsys):
    monkeypatch.setattr(daily, "parse_args", lambda: Namespace(
        day="2026-10-06", capture_dir="unused", fishing_db=None, send_log_group=True, env_file="unused",
    ))
    monkeypatch.setattr(daily, "build_report", lambda *_args, **_kwargs: REPORT_TEXT)
    sender = Mock(side_effect=RuntimeError("unknown; no automatic retry"))
    monkeypatch.setattr(daily, "send_log_group", sender)
    with pytest.raises(RuntimeError, match="unknown"):
        daily.main()
    sender.assert_called_once()
    output = capsys.readouterr().out
    assert "sent log group" not in output and "log bot ok" not in output
