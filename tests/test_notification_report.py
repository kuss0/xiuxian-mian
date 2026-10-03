import json

import pytest

from model.audit_delivery import RECEIPT_PREFIX, bot_delivery_outcome, delivery_receipt, emit_delivery_receipt
from tools.notification_report import delivery_report, inventory


def receipt(**changes):
    value = delivery_receipt("test", chat_id=-123, transport="bot", outcome="confirmed", elapsed_sec=0.25)
    return {**value, "at": 1790977200, **changes}


def line(value):
    return RECEIPT_PREFIX + json.dumps(value)


@pytest.mark.parametrize("error", [
    "timeout: read timed out", "proxy error: unreachable", "Connection reset by peer",
    'HTTP 502: {"ok":false,"error_code":502}', 'HTTP 403: <html>proxy</html>',
    'HTTP 500: {"ok":false,"error_code":400}', 'HTTP 200: {"ok":false,"error_code":400}',
    '{"ok":false,"error_code":true}', '{"ok":false,"error_code":"429"}',
    '{"ok":true}', '{"ok":false}', "null", "[]", "denied", "", "[" * 2000 + "]" * 2000,
])
def test_unproven_bot_failures_are_unknown(error):
    assert bot_delivery_outcome(False, error) == "unknown"


@pytest.mark.parametrize("error", [
    "missing bot token", "invalid chat id", '{"ok":false,"error_code":400}',
    'HTTP 403: {"ok":false,"error_code":403}',
    'HTTP 429: {"ok":false,"error_code":429,"parameters":{"retry_after":60}}',
])
def test_explicit_bot_rejections_are_unconfirmed(error):
    assert bot_delivery_outcome(False, error) == "unconfirmed"


def test_only_literal_success_is_confirmed():
    assert bot_delivery_outcome(True, "") == "confirmed"
    assert bot_delivery_outcome("true", "") == "unknown"


def test_receipt_contains_only_metadata_and_counts_visible_unicode():
    text = '<b>秘密 cookie token initData</b>\n<a href="tg://user?id=1">管理员</a>😀'
    result = delivery_receipt(text, chat_id=-1, transport="bot", outcome="confirmed", elapsed_sec=0.3, parse_mode="HTML")
    encoded = json.dumps(result)
    for secret in ("cookie", "token", "initData", "秘密", "tg://"):
        assert secret not in encoded
    assert result["explicit_mention_links"] == 1
    assert result["utf16_units"] == len("秘密 cookie token initData\n管理员😀".encode("utf-16-le")) // 2
    assert result["lines"] == 2
    assert result["elapsed_ms"] == 300


def test_metrics_do_not_throw_on_broken_output(monkeypatch):
    def fail(*args, **kwargs):
        raise BrokenPipeError()
    monkeypatch.setattr("builtins.print", fail)
    emit_delivery_receipt("secret", chat_id=-1, transport="bot", outcome="confirmed", elapsed_sec=0)


def test_missing_metrics_are_unknown_not_zero_success():
    result = delivery_report(['[2026-10-03] ordinary runtime log', '{bad}', '[]'])
    assert result["available"] is False
    assert result["confirmed_visible_utf16_units"]["p95"] is None
    assert result["ignored_lines"] == 3


def test_report_counts_attempts_and_distinguishes_unknown_fallback():
    a = receipt(outcome="unknown")
    b = receipt(transport="account")
    c = receipt(chat_id=-456)
    report = delivery_report([line(a), json.dumps({"MESSAGE": line(b)}), line(b), line(c)])
    assert report["attempts"] == 3
    assert report["outcomes"] == {"unknown": 1, "confirmed": 2}
    assert report["repeated_confirmed_payloads"] == 0
    assert len(report["confirmed_by_hour"]) == 2
    assert report["confirmed_latency_ms"]["p99"] == 250


def test_exact_payload_repeats_are_not_business_event_counts():
    report = delivery_report([line(receipt()), line(receipt()), line(receipt(outcome="unconfirmed"))])
    assert report["repeated_confirmed_payloads"] == 1
    assert report["outcomes"] == {"confirmed": 2, "unconfirmed": 1}
    assert "not business events" in report["unit"]


@pytest.mark.parametrize("change", [
    {"outcome": "sent"}, {"outcome": []}, {"at": float("nan")}, {"at": 1e200},
    {"elapsed_ms": -1}, {"utf16_units": "10"}, {"receipt_id": "bad"},
    {"payload_sha256": "secret"}, {"chat_id": True}, {"transport": "http"},
])
def test_malformed_receipts_do_not_poison_report(change):
    assert delivery_report([line(receipt(**change))])["available"] is False


def test_inventory_is_static_and_records_compatibility_gaps(tmp_path):
    folder = tmp_path / "model"
    folder.mkdir()
    (folder / "sample.py").write_text('''
raise RuntimeError("must never import target code")
async def notify():
    await send_audit_log("one")
    await send_audit_log("two", priority="high")
    await send_audit_log("three", priority=selected, summary_kind="yuanying")
''')
    result = inventory(tmp_path)
    assert result["calls"] == 3
    assert result["priorities"] == {"auto (implicit)": 1, "'high'": 1, "selected": 1}
    assert sum(row["has_summary_kind"] for row in result["call_sites"]) == 1
    assert not result["parse_errors"]
