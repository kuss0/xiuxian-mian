import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import bot_delivery, miniapp_daily_report, safety_watchdog, storage_bag_report
from tools.notification_report import RECEIPT_PREFIX, delivery_report


ENV = {"LOG_BOT_TOKEN": "123:private-token", "LOG_GROUP_ID": "-1001"}
ACK = {"ok": True, "result": {"message_id": 42, "chat": {"id": -1001}}}


class Response(io.BytesIO):
    status = 200


@pytest.fixture(params=["safety_watchdog", "miniapp_daily_report", "storage_bag_report"])
def sender(request, monkeypatch):
    monkeypatch.delenv("LOG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_GROUP_ID", raising=False)
    monkeypatch.setattr(miniapp_daily_report, "_load_env_file", lambda _: ENV)
    monkeypatch.setattr(storage_bag_report, "load_env_file", lambda _: ENV)
    calls = {
        "safety_watchdog": lambda: safety_watchdog.send_log_via_bot(ENV, "private message"),
        "miniapp_daily_report": lambda: miniapp_daily_report.send_log_group("private message", Path("unused")),
        "storage_bag_report": lambda: storage_bag_report.send_log_group_chunks(["private message"], Path("unused")),
    }
    return request.param, calls[request.param]


@pytest.mark.parametrize("outcome", ["confirmed", "unknown", "unconfirmed"])
def test_each_actual_tool_attempt_emits_one_content_free_record(sender, monkeypatch, capsys, outcome):
    import urllib.error

    if outcome == "confirmed":
        opener = Mock(return_value=Response(json.dumps(ACK).encode()))
    elif outcome == "unknown":
        opener = Mock(side_effect=TimeoutError("private-token"))
    else:
        opener = Mock(side_effect=urllib.error.HTTPError(
            "private-token", 429, "private message", {},
            io.BytesIO(b'{"ok":false,"error_code":429}'),
        ))
    monkeypatch.setattr(miniapp_daily_report.urllib.request, "urlopen", opener)
    source, call = sender
    if outcome != "confirmed" and source != "safety_watchdog":
        with pytest.raises(RuntimeError, match=outcome):
            call()
    else:
        call()
    opener.assert_called_once()
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert "private" not in captured.err and "private-token" not in captured.out
    report = delivery_report(lines)
    assert report["attempts"] == 1
    assert report["by_source"][source]["outcomes"] == {outcome: 1}
    assert report["confirmed_visible_metrics_count"] == 0
    assert report["confirmed_visible_utf16_units"]["p95"] is None
    assert report["confirmed_explicit_mention_links"] == 0
    assert report["confirmed_payload_utf16_units"]["p95"] == (15 if outcome == "confirmed" else None)


def test_disabled_cli_and_missing_config_emit_no_delivery_receipt(monkeypatch, capsys):
    assert "skipped" in safety_watchdog.send_log_via_bot({}, "private")
    assert RECEIPT_PREFIX not in capsys.readouterr().err


def test_metrics_failure_cannot_change_confirmed_result_or_retry(monkeypatch):
    opener = Mock(return_value=Response(json.dumps(ACK).encode()))
    monkeypatch.setattr(bot_delivery, "_emit_tool_receipt", Mock(side_effect=OSError("private-token")))
    result = bot_delivery.read_bot_delivery(opener, "-1001", source="safety_watchdog", message="private")
    assert result.outcome == "confirmed"
    opener.assert_called_once()


def test_bag_partial_send_records_only_attempted_chunks(monkeypatch, capsys):
    monkeypatch.delenv("LOG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_GROUP_ID", raising=False)
    monkeypatch.setattr(storage_bag_report, "load_env_file", lambda _: ENV)
    opener = Mock(side_effect=[Response(json.dumps(ACK).encode()), TimeoutError("private")])
    monkeypatch.setattr(storage_bag_report.request, "urlopen", opener)
    with pytest.raises(RuntimeError, match="confirmed=1/3"):
        storage_bag_report.send_log_group_chunks(["one", "two", "three"], Path("unused"))
    report = delivery_report(capsys.readouterr().err.splitlines())
    assert opener.call_count == report["attempts"] == 2
    assert report["outcomes"] == {"confirmed": 1, "unknown": 1}


def tool_receipt(**changes):
    return {
        "schema": 2, "receipt_id": "a" * 32, "at": 1791285000.0,
        "chat_id": -1001, "topic_id": 0, "source": "safety_watchdog", "transport": "bot",
        "outcome": "confirmed", "elapsed_ms": 123, "payload_sha256": "b" * 64,
        "payload_utf16_units": 100, "payload_lines": 2, **changes,
    }


def line(row):
    return RECEIPT_PREFIX + json.dumps(row)


def test_mixed_versions_keep_visible_size_and_mentions_runtime_only():
    from model.audit_delivery import delivery_receipt

    runtime = delivery_receipt("runtime", chat_id=-1001, transport="bot", outcome="confirmed", elapsed_sec=0.25)
    rows = [line(runtime), line(tool_receipt()), line(tool_receipt())]
    report = delivery_report(rows)
    assert report["attempts"] == 2
    assert report["by_source"]["runtime"]["attempts"] == 1
    assert report["by_source"]["safety_watchdog"]["attempts"] == 1
    assert report["confirmed_visible_metrics_count"] == 1
    assert report["confirmed_payload_metrics_count"] == 1
    assert report["confirmed_visible_utf16_units"] == {"p50": 7, "p95": 7, "p99": 7}
    assert report["confirmed_payload_utf16_units"] == {"p50": 100, "p95": 100, "p99": 100}
    assert report["confirmed_latency_ms"]["p95"] == 250
    assert "not business events" in report["unit"]


@pytest.mark.parametrize("change", [
    {"source": "private-token"}, {"source": []}, {"source": "runtime"},
    {"transport": "account"}, {"schema": True}, {"schema": 2.0},
    {"at": True}, {"topic_id": True}, {"topic_id": -1},
    {"payload_utf16_units": -1}, {"payload_lines": "2"},
])
def test_bad_tool_records_are_not_reported(change):
    report = delivery_report([line(tool_receipt(**change))])
    assert report["attempts"] == 0
    assert "private-token" not in json.dumps(report)


def test_topic_separates_identical_payload_counts():
    report = delivery_report([
        line(tool_receipt(topic_id=10)),
        line(tool_receipt(receipt_id="c" * 32, topic_id=20)),
    ])
    assert report["attempts"] == 2
    assert report["repeated_confirmed_payloads"] == 0


@pytest.mark.parametrize("outcome", ["confirmed", "unknown"])
@pytest.mark.parametrize("failure", ["stderr", "clock", "uuid", "start_clock"])
def test_diagnostic_failures_preserve_transport_outcome(monkeypatch, outcome, failure):
    opener = Mock(return_value=Response(json.dumps(ACK).encode())) if outcome == "confirmed" else Mock(side_effect=TimeoutError())
    fail = Mock(side_effect=OSError("private diagnostic"))
    if failure == "stderr":
        monkeypatch.setattr(bot_delivery.sys, "stderr", Mock(write=fail))
    elif failure == "clock":
        monkeypatch.setattr(bot_delivery.time, "time", fail)
    elif failure == "uuid":
        monkeypatch.setattr(bot_delivery.uuid, "uuid4", fail)
    else:
        monkeypatch.setattr(bot_delivery.time, "monotonic", fail)
    result = bot_delivery.read_bot_delivery(opener, "-1001", source="miniapp_daily_report", message="private")
    assert result.outcome == outcome
    opener.assert_called_once()


def test_legacy_helper_call_does_not_emit_metrics(capsys):
    result = bot_delivery.read_bot_delivery(lambda: Response(json.dumps(ACK).encode()), "-1001")
    assert result.outcome == "confirmed"
    assert capsys.readouterr().err == ""


def test_html_metrics_are_raw_payload_not_visible_text(capsys):
    message = '<b>private</b>\n<a href="tg://user?id=123">admin</a>\U0001f600'
    result = bot_delivery.read_bot_delivery(
        lambda: Response(json.dumps(ACK).encode()), "-1001", source="safety_watchdog", message=message,
    )
    assert result.outcome == "confirmed"
    captured = capsys.readouterr()
    assert captured.out == "" and "private" not in captured.err and "tg://" not in captured.err
    report = delivery_report(captured.err.splitlines())
    assert report["confirmed_payload_utf16_units"]["p50"] == len(message.encode("utf-16-le")) // 2
    assert report["confirmed_visible_metrics_count"] == 0


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_receipt_id_is_excluded_not_first_wins(reverse):
    rows = [line(tool_receipt()), line(tool_receipt(outcome="unknown"))]
    if reverse:
        rows.reverse()
    report = delivery_report(rows + rows)
    assert report["attempts"] == 0
    assert report["excluded_conflicting_receipts"] == 1
    assert report["outcomes"] == {}


def test_deeply_nested_unrelated_input_is_ignored():
    report = delivery_report(["{\"MESSAGE\":" + "[" * 2000 + "]" * 2000 + "}",
                              RECEIPT_PREFIX + "[" * 2000 + "]" * 2000])
    assert report["attempts"] == 0 and report["ignored_lines"] == 2


def test_runtime_record_cannot_inject_source_or_topic():
    from model.audit_delivery import delivery_receipt

    row = delivery_receipt("runtime", chat_id=-1001, transport="bot", outcome="confirmed", elapsed_sec=0)
    report = delivery_report([line({**row, "source": "private", "topic_id": []}), line(row)])
    assert report["by_source"] == {"runtime": {"attempts": 1, "outcomes": {"confirmed": 1}}}
    assert report["excluded_conflicting_receipts"] == 0


@pytest.mark.parametrize("key,value", [("receipt_id", int("1" * 32)), ("payload_sha256", int("1" * 64))])
def test_numeric_digests_are_not_valid_records(key, value):
    assert delivery_report([line(tool_receipt(**{key: value}))])["available"] is False


@pytest.mark.parametrize("envelope", [False, True])
def test_duplicate_json_fields_do_not_choose_a_convenient_outcome(envelope):
    raw = line(tool_receipt()).replace('"outcome": "confirmed"', '"outcome":"unknown","outcome":"confirmed"')
    if envelope:
        raw = '{"MESSAGE":"unrelated","MESSAGE":' + json.dumps(line(tool_receipt())) + '}'
    assert delivery_report([raw])["available"] is False
