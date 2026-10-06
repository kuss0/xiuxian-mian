import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from tools import safety_watchdog as watchdog


ENV = {"LOG_BOT_TOKEN": "123:secret-token", "LOG_GROUP_ID": "-1001"}
ACK = {"ok": True, "result": {"message_id": 42, "chat": {"id": -1001}, "text": "private notification"}}


class Response(io.BytesIO):
    status = 200


def send(monkeypatch, payload, *, status=200):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    response = Response(raw)
    response.status = status
    opener = Mock(return_value=response)
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", opener)
    result = watchdog.send_log_via_bot(ENV, "private notification")
    opener.assert_called_once()
    assert opener.call_args.kwargs["timeout"] == 8
    assert response.closed
    assert "private notification" not in result
    assert "secret-token" not in result
    return result


def test_success_requires_a_matching_message_receipt(monkeypatch):
    assert send(monkeypatch, ACK) == "log bot ok: message_id=42"


@pytest.mark.parametrize("payload", [
    {"ok": True}, {"ok": 1, "result": ACK["result"]},
    {"ok": False, "error_code": 400, "description": "private notification"},
    {"ok": True, "result": {"message_id": True, "chat": {"id": -1001}}},
    {"ok": True, "result": {"message_id": 0, "chat": {"id": -1001}}},
    {"ok": True, "result": {"message_id": 2**63, "chat": {"id": -1001}}},
    {"ok": True, "result": {"message_id": 42, "chat": {"id": -1002}}},
    {"ok": True, "result": {"message_id": 42, "chat": {"id": "-1001"}}},
    {"ok": True, "result": {"message_id": 42}},
    {"ok": True, "result": []}, {"ok": False}, [], None,
    b'<html>private notification</html>', b'{"ok":', b'\xff',
    b'{"ok":false,"ok":true,"result":{"message_id":42,"chat":{"id":-1001}}}',
    b'{"ok":true,"result":{"message_id":41,"message_id":42,"chat":{"id":-1001}}}',
    b'[' * 2000 + b']' * 2000,
], ids=lambda payload: type(payload).__name__)
def test_ambiguous_responses_are_unknown_not_success(monkeypatch, payload):
    assert send(monkeypatch, payload).startswith("log bot unknown:")


def test_large_success_receipt_is_parsed_instead_of_truncated(monkeypatch):
    payload = {"padding": "x" * 1024, **ACK}
    assert send(monkeypatch, payload) == "log bot ok: message_id=42"


def test_response_read_is_bounded(monkeypatch):
    response = Mock()
    response.status = 200
    response.read.return_value = b"x" * (65536 + 1)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock(return_value=response)
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", opener)
    assert watchdog.send_log_via_bot(ENV, "private notification").startswith("log bot unknown:")
    response.read.assert_called_once_with(65536 + 1)
    opener.assert_called_once()


@pytest.mark.parametrize("code", [400, 403, 429])
def test_explicit_http_rejection_is_unconfirmed_without_retry(monkeypatch, code):
    body = io.BytesIO(json.dumps({"ok": False, "error_code": code, "description": "private notification"}).encode())
    error = urllib.error.HTTPError("https://api.telegram.org/bot123:secret-token/sendMessage", code, "private error", {}, body)
    opener = Mock(side_effect=error)
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", opener)
    assert watchdog.send_log_via_bot(ENV, "private notification") == f"log bot unconfirmed: HTTP {code}"
    opener.assert_called_once()
    assert body.closed


@pytest.mark.parametrize("code,payload", [
    (502, {"ok": False, "error_code": 502}),
    (403, {"ok": False, "error_code": 400}),
    (403, {"ok": True, "result": ACK["result"]}),
    (403, {"ok": False, "error_code": "403"}),
    (403, None),
])
def test_http_error_without_proven_rejection_remains_unknown(monkeypatch, code, payload):
    body = io.BytesIO(json.dumps(payload).encode())
    error = urllib.error.HTTPError("https://api.telegram.org/bot123:secret-token/sendMessage", code, "private error", {}, body)
    opener = Mock(side_effect=error)
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", opener)
    result = watchdog.send_log_via_bot(ENV, "private notification")
    assert result.startswith("log bot unknown:")
    assert "secret-token" not in result and "private" not in result
    opener.assert_called_once()
    assert body.closed


@pytest.mark.parametrize("error", [
    TimeoutError("private https://api.telegram.org/bot123:secret-token/sendMessage"),
    urllib.error.URLError("private secret-token"),
    ConnectionError("private secret-token"),
])
def test_transport_error_never_logs_raw_exception_or_retries(monkeypatch, error):
    opener = Mock(side_effect=error)
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", opener)
    result = watchdog.send_log_via_bot(ENV, "private notification")
    assert result.startswith("log bot unknown:")
    assert "private" not in result and "secret-token" not in result
    opener.assert_called_once()


@pytest.mark.parametrize("env", [{}, {"LOG_GROUP_ID": "-1001"}, {"LOG_BOT_TOKEN": "123:secret-token"}])
def test_missing_configuration_still_skips_without_send(monkeypatch, env):
    opener = Mock()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", opener)
    assert watchdog.send_log_via_bot(env, "private notification").startswith("log bot skipped:")
    opener.assert_not_called()


@pytest.mark.parametrize("http_error", [False, True])
def test_response_read_failure_closes_stream_and_preserves_unknown(monkeypatch, http_error):
    class BrokenResponse(Response):
        def read(self, _limit):
            raise TimeoutError("private secret-token")

    response = BrokenResponse()
    if http_error:
        error = urllib.error.HTTPError("https://api.telegram.org/bot123:secret-token/sendMessage", 403, "private", {}, response)
        opener = Mock(side_effect=error)
    else:
        opener = Mock(return_value=response)
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", opener)
    assert watchdog.send_log_via_bot(ENV, "private notification") == "log bot unknown: TimeoutError"
    opener.assert_called_once()
    assert response.closed
