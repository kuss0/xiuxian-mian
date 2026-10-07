import json
from copy import deepcopy

import pytest

from tools import business_semantic_report as report


DAY = "2026-10-07"
BOT = 8735907987
CHAT = -1002083016447
PANEL = "\u4fe1\u4ef0: {} / 100\n\u7a33\u5b9a: 95 / 100"


def samples():
    common = {"chat_id": CHAT, "event_type": "message"}
    return [
        {**common, "ts": DAY + " 14:00:00 UTC+8", "event_type": "sent", "message_id": 10,
         "sender_id": 101, "sender_username": "fixture_owner", "text": ".\u5c0f\u4e16\u754c"},
        {**common, "ts": DAY + " 14:00:02 UTC+8", "message_id": 11, "reply_to_msg_id": 10,
         "sender_id": BOT, "sender_is_bot": True, "sender_username": "hantianzun32_bot", "text": PANEL.format(100)},
        {**common, "ts": DAY + " 15:00:00 UTC+8", "event_type": "sent", "message_id": 20,
         "sender_id": 101, "text": ".\u5c0f\u4e16\u754c"},
        {**common, "ts": DAY + " 15:00:02 UTC+8", "message_id": 21, "reply_to_msg_id": 20,
         "sender_id": BOT, "text": PANEL.format(98)},
    ]


def run(tmp_path, rows):
    path = tmp_path / (DAY + ".log")
    text = "\n".join(json.dumps(row) for row in rows) + "\n"
    path.write_text(text, encoding="utf-8")
    before = deepcopy(rows)
    result = report.build_small_world_evidence(tmp_path, day=DAY, days=1)
    assert rows == before
    assert path.read_text(encoding="utf-8") == text
    return result


@pytest.mark.parametrize("cross_group", [False, True])
def test_prior_anchored_official_bot_metadata_recovers_later_missing_flag(tmp_path, cross_group):
    rows = samples()
    if cross_group:
        rows[2]["chat_id"] = rows[3]["chat_id"] = -1001680975844
    value = run(tmp_path, rows)
    assert value["script_panels"] == 2
    assert value["summary"]["unexplained"] == 1
    assert value["deltas"][0]["faith_delta"] == -2
    assert value["scope"]["inferred_bot_rows"] == 1


@pytest.mark.parametrize("change", [
    {"sender_is_bot": False}, {"sender_is_bot": None}, {"sender_is_bot": 1},
    {"sender_id": BOT + 1}, {"sender_id": True}, {"sender_id": 0},
    {"sender_username": "other_bot"}, {"forwarded": True}, {"event_type": "sent"},
])
def test_missing_metadata_inference_cannot_override_contrary_evidence(tmp_path, change):
    rows = samples()
    rows[-1].update(change)
    assert run(tmp_path, rows)["script_panels"] == 1


@pytest.mark.parametrize("change", [
    {"sender_is_bot": False}, {"sender_is_bot": None}, {"sender_is_bot": 1},
    {"sender_id": True}, {"sender_id": BOT + 1}, {"sender_username": "fixture_bot"},
    {"sender_username": "hantianzun32_bot_fake"}, {"sender_username": None},
    {"reply_to_msg_id": 999}, {"chat_id": CHAT - 1}, {"forwarded": True},
    {"ts": DAY + " 13:59:59 UTC+8"}, {"ts": DAY + " 16:00:00 UTC+8"},
])
def test_unproven_seed_never_authorizes_missing_metadata(tmp_path, change):
    rows = samples()
    rows[1].update(change)
    result = run(tmp_path, rows)
    assert not any(delta["to"] == DAY + " 15:00:02" for delta in result["deltas"])
    assert result["scope"]["inferred_bot_rows"] == 0


def test_future_explicit_human_conflict_disables_sender_inference(tmp_path):
    rows = samples()
    rows.append({"ts": DAY + " 16:00:00 UTC+8", "event_type": "message", "chat_id": CHAT,
                 "message_id": 30, "sender_id": BOT, "sender_is_bot": False, "text": "fixture conflict"})
    assert run(tmp_path, rows)["script_panels"] == 1


def test_inferred_broadcast_explains_only_its_own_identity(tmp_path):
    rows = samples()
    rows.insert(2, {"ts": DAY + " 14:30:00 UTC+8", "event_type": "message", "chat_id": CHAT,
                    "message_id": 15, "sender_id": BOT, "text": "@fixture_owner \u4fe1\u4ef0 -2 \u70b9"})
    result = run(tmp_path, rows)
    assert result["script_panels"] == 2
    assert result["summary"]["explained"] == 1
    assert result["events"][0]["identity_id"] == 101


def test_known_sender_does_not_bypass_exact_script_root(tmp_path):
    rows = samples()
    rows[-1]["chat_id"] -= 1
    assert run(tmp_path, rows)["script_panels"] == 1


@pytest.mark.parametrize("index", [1, 3])
def test_conflicting_sender_for_same_message_cannot_seed_or_receive_inference(tmp_path, index):
    rows = samples()
    rows.append({**rows[index], "sender_id": 202, "sender_is_bot": False})
    assert run(tmp_path, rows)["script_panels"] == 1


@pytest.mark.parametrize("name", ["other_bot", "hantianzun99_bot", None])
def test_later_unanchored_username_change_requires_new_proof(tmp_path, name):
    rows = samples()
    rows.insert(2, {"ts": DAY + " 14:30:00 UTC+8", "event_type": "message", "chat_id": CHAT,
                    "message_id": 15, "sender_id": BOT, "sender_is_bot": True,
                    "sender_username": name, "text": "fixture changed metadata"})
    assert run(tmp_path, rows)["script_panels"] == 1


def test_new_anchored_official_metadata_can_reestablish_proof(tmp_path):
    rows = samples()
    rows.insert(2, {"ts": DAY + " 14:30:00 UTC+8", "event_type": "message", "chat_id": CHAT,
                    "message_id": 15, "sender_id": BOT, "sender_is_bot": True,
                    "sender_username": "hantianzun99_bot", "text": "fixture changed metadata"})
    rows.insert(3, {**rows[0], "ts": DAY + " 14:40:00 UTC+8", "message_id": 16})
    rows.insert(4, {**rows[1], "ts": DAY + " 14:40:01 UTC+8", "message_id": 17,
                    "reply_to_msg_id": 16, "sender_username": "hantianzun99_bot"})
    assert run(tmp_path, rows)["script_panels"] == 3
