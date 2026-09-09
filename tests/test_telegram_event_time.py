import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from model import app_message_log, message_log_recovery
from model.message_box import build_message_fact_from_event
from model.verified_event import from_telegram_event, telegram_event_timestamp


NOW = 1780000000.0


@pytest.mark.parametrize("kind", ["message", "edit"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("zone", [None, timezone.utc, timezone(timedelta(hours=8))])
def test_event_time_comes_from_server_dates(kind, nested, zone):
    dates = {
        "date": datetime.fromtimestamp(NOW, timezone.utc),
        "edit_date": datetime.fromtimestamp(NOW + 30, timezone.utc),
    }
    dates = {key: value.astimezone(zone) if zone else value.replace(tzinfo=None) for key, value in dates.items()}
    event = SimpleNamespace(message=SimpleNamespace(**dates)) if nested else SimpleNamespace(**dates)
    assert telegram_event_timestamp(event, kind) == NOW + (30 if kind == "edit" else 0)


@pytest.mark.parametrize("value", [None, True, "1780000000", -1, float("inf"), float("nan"), 10**1000])
def test_invalid_replay_timestamp_is_not_replaced_by_the_message_date(value):
    event = SimpleNamespace(server_event_at=value, date=datetime.fromtimestamp(NOW, timezone.utc))
    assert telegram_event_timestamp(event) == 0


def test_missing_edit_time_cannot_borrow_the_original_message_time():
    event = SimpleNamespace(date=datetime.fromtimestamp(NOW, timezone.utc), edit_date=None)
    assert telegram_event_timestamp(event, "edit") == 0
    assert telegram_event_timestamp(SimpleNamespace(), "message") == 0


@pytest.mark.parametrize("kind", ["message", "edit"])
def test_verified_event_and_message_box_preserve_server_time(kind):
    event = SimpleNamespace(
        id=5511, chat_id=-100550001, sender_id=7551,
        date=datetime.fromtimestamp(NOW, timezone.utc),
        edit_date=datetime.fromtimestamp(NOW + 30, timezone.utc),
    )
    direct = from_telegram_event(event, "result", {}, event_kind=kind)
    fact = build_message_fact_from_event(event, "result", {}, event_type=kind)
    assert fact.to_verified_game_event() == direct
    assert direct.server_event_at == NOW + (30 if kind == "edit" else 0)


@pytest.mark.parametrize("kind", ["message", "edit"])
def test_log_keeps_receipt_time_for_search_and_server_time_for_effects(monkeypatch, tmp_path, kind):
    class ReceiptClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(NOW + 60, tz)

    monkeypatch.setattr(app_message_log, "datetime", ReceiptClock)
    event = SimpleNamespace(
        id=5511, chat_id=-100550001, sender_id=7551, raw_text="result",
        date=datetime.fromtimestamp(NOW, timezone.utc),
        edit_date=datetime.fromtimestamp(NOW + 30, timezone.utc),
        reply_to=SimpleNamespace(reply_to_msg_id=5510),
    )
    received_at, payload = app_message_log._build_message_log_payload(event, event_type=kind)
    assert message_log_recovery.parse_message_log_ts(payload["ts"]) == NOW + 60
    assert payload["server_event_at"] == NOW + (30 if kind == "edit" else 0)
    log = tmp_path / f"{received_at.date().isoformat()}.log"
    log.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    rows = message_log_recovery.find_message_log_replies(
        5510, NOW + 60, lookback_sec=5, lookahead_sec=0, chat_id=event.chat_id, messages_dir=tmp_path,
    )
    assert len(rows) == 1
    assert rows[0]["ts_epoch"] == NOW + 60
    assert rows[0]["server_event_at"] == payload["server_event_at"]
