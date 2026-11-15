"""Replay the actual burst warning without trusting command labels alone."""
import copy
import json
from pathlib import Path

import pytest

from tools import safety_watchdog as watchdog


@pytest.fixture
def observed():
    fixture = json.loads(Path(__file__).with_name("fixtures").joinpath("xuangu_watchdog_20261115.json").read_text())
    assert fixture["source"] == "production data/messages/2026-11-15.log"
    events = fixture["events"]
    for item in events:
        item["_epoch"] = watchdog.parse_local_ts(item["ts"])
    return events


def test_real_120_second_warning_needs_receipts_before_exempting(observed, monkeypatch):
    now = watchdog.parse_local_ts("2026-11-15 12:02:05 UTC+8")
    events = [item for item in observed if item["_epoch"] <= now]
    cfg = watchdog.build_config(watchdog.parse_args(["--dry-run"]))
    with monkeypatch.context() as patcher:
        patcher.setattr(watchdog, "get_receipt_verified_xuangu_ids", lambda *_: set())
        assert watchdog.count_non_burst_exempt_since(events, now, 120) == 8
        assert watchdog.find_send_breach(events, now, cfg) == "send burst: 8+ sends in 120s"
    assert watchdog.count_non_burst_exempt_since(events, now, 120) == 1  # Last role reply has not arrived yet.
    assert watchdog.find_send_breach(events, now, cfg) == ""


def test_real_rejected_late_action_is_never_exempt(observed):
    now = watchdog.parse_local_ts("2026-11-15 12:06:00 UTC+8")
    verified = watchdog.get_receipt_verified_xuangu_ids(observed, now)
    sends = [item for item in observed if item["event_type"] == "sent"]
    rejected = next(item for item in sends if item["message_id"] == 1595568)
    assert id(rejected) not in verified
    # wisemole's Telegram echo has no username, so its role-to-game-name
    # association also remains unproven rather than receiving an exemption.
    assert {item["message_id"] for item in sends if id(item) not in verified} == {1595543, 1595568}


@pytest.mark.parametrize("change", ["no_receipts", "wrong_bot", "forwarded", "wrong_chat", "wrong_topic", "future", "failure", "wrong_chain"])
def test_unproven_xuangu_sends_remain_counted(observed, change):
    now = watchdog.parse_local_ts("2026-11-15 12:06:00 UTC+8")
    if change == "no_receipts":
        observed = [item for item in observed if not item.get("sender_is_bot")]
    for item in observed:
        if item.get("sender_is_bot"):
            if change == "wrong_bot": item["sender_username"] = "unrelated_bot"
            if change == "forwarded": item["forwarded"] = True
            if change == "wrong_chat": item["chat_id"] = -42
            if change == "wrong_topic": item["topic_id"] = 42
            if change == "future": item["_epoch"] = now + 1
            if change == "failure": item["text"] = "当前轮次已结束，请先 .玄骨状态。"
        if change == "wrong_chain" and item["event_type"] == "sent": item["chain_id"] = "other-flow"
    assert not watchdog.get_receipt_verified_xuangu_ids(observed, now)


def test_duplicate_action_invalidates_flow_exemptions_and_still_warns(observed):
    now = watchdog.parse_local_ts("2026-11-15 12:02:05 UTC+8")
    events = [item for item in observed if item["_epoch"] <= now]
    original = next(item for item in events if item["event_type"] == "sent" and item["text"].startswith(".玄骨分工"))
    duplicate = copy.deepcopy(original)
    duplicate.update(message_id=99999999, _epoch=now)
    events.append(duplicate)
    assert not watchdog.get_receipt_verified_xuangu_ids(events, now)
    cfg = watchdog.build_config(watchdog.parse_args(["--dry-run"]))
    assert watchdog.find_send_breach(events, now, cfg).startswith("send burst:")
