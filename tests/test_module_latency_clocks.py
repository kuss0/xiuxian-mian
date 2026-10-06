import json
from datetime import datetime, timedelta

import pytest

from tools import module_latency_report as report


START = datetime(2026, 10, 6, 4, 27, 14, tzinfo=report.TZ_LOCAL)


def row(offset, kind, message_id, sender_id, **kwargs):
    result = {
        "ts": (START + timedelta(seconds=offset)).strftime(report.TS_FORMAT),
        "event_type": kind, "chat_id": -1001, "message_id": message_id,
        "sender_id": sender_id, "text": ".fixture" if message_id == 10 else "fixture reply",
    }
    result.update(kwargs)
    return result


def evidence():
    return [
        row(0, "sent", 10, 2001, source_module="fixture"),
        row(0, "message", 10, 2001, server_event_at=START.timestamp() - 1),
        row(851, "message", 11, 9001, sender_is_bot=True, reply_to_msg_id=10,
            server_event_at=START.timestamp() + 2),
    ]


def build(tmp_path, rows):
    (tmp_path / "2026-10-06.log").write_text(
        "\n".join(json.dumps(item) for item in rows) + "\n", encoding="utf-8",
    )
    return report.build_latency_report(
        tmp_path, since_hours=24, now=START + timedelta(hours=1), modules={"fixture": "fixture"},
    )


def test_delayed_history_observation_is_not_server_latency(tmp_path):
    payload = build(tmp_path, evidence())
    module = payload["modules"]["fixture"]
    assert module["first_reply"]["p95_sec"] == 851
    assert module["server_clock_samples"] == 1
    assert module["server_first_reply"]["p95_sec"] == 3
    assert module["server_final_event"]["p95_sec"] == 3
    assert module["first_observation_lag"]["p95_sec"] == 849
    sample = module["slow_samples"][0]
    assert sample["chat_id"] == -1001
    assert sample["server_first_reply_sec"] == 3
    assert sample["first_observation_lag_sec"] == 849
    formatted = report.format_report(payload)
    assert "server_first" in formatted and "observation_lag" in formatted


@pytest.mark.parametrize("change", ["missing", "chat", "sender", "text", "forward", "edit"])
def test_echo_requires_exact_sent_message_ownership(tmp_path, change):
    rows = evidence()
    if change == "missing":
        rows.pop(1)
    else:
        key, value = {
            "chat": ("chat_id", -1002), "sender": ("sender_id", 9999),
            "text": ("text", ".different"), "forward": ("forwarded", True),
            "edit": ("event_type", "edit"),
        }[change]
        rows[1][key] = value
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["replied"] == 1
    assert module["first_reply"]["p95_sec"] == 851
    assert module["server_clock_samples"] == 0
    assert module["server_first_reply"]["p95_sec"] is None


@pytest.mark.parametrize("value", [None, True, "1791232033", 0, -1, float("nan"), float("inf"), 1e30, 10**400])
@pytest.mark.parametrize("index", [1, 2])
def test_invalid_server_timestamps_do_not_change_observed_latency(tmp_path, index, value):
    rows = evidence()
    rows[index]["server_event_at"] = value
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["server_clock_samples"] == 0
    assert module["first_reply"]["p95_sec"] == 851


def test_conflicting_echo_timestamps_do_not_pick_a_convenient_clock(tmp_path):
    rows = evidence()
    rows.append({**rows[1], "server_event_at": START.timestamp() - 2})
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["server_clock_samples"] == 0


def test_identical_echo_duplicates_are_not_extra_latency_samples(tmp_path):
    rows = evidence()
    rows.append(dict(rows[1]))
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["sent"] == 1
    assert module["server_clock_samples"] == 1


def test_out_of_order_observation_keeps_both_clock_bases(tmp_path):
    rows = evidence()
    rows[2].update(ts=(START + timedelta(seconds=100)).strftime(report.TS_FORMAT))
    rows.append(row(7, "edit", 11, 9001, sender_is_bot=True, reply_to_msg_id=10,
                    server_event_at=START.timestamp() + 6))
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["first_reply"]["p95_sec"] == 7
    assert module["final_event"]["p95_sec"] == 100
    assert module["server_first_reply"]["p95_sec"] == 3
    assert module["server_final_event"]["p95_sec"] == 7
    assert module["first_observation_lag"]["p95_sec"] == 98


def test_partial_server_evidence_does_not_claim_final_server_time(tmp_path):
    rows = evidence()
    rows.append(row(852, "edit", 11, 9001, sender_is_bot=True, reply_to_msg_id=10))
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["server_clock_samples"] == 0
    assert module["server_final_event"]["p95_sec"] is None
    assert module["final_event"]["p95_sec"] == 852


def test_ancestor_edit_does_not_become_a_direct_child_reply(tmp_path):
    rows = evidence()
    rows.append(row(10, "sent", 12, 2001, source_module="fixture", reply_to_msg_id=11))
    rows.append(row(13, "edit", 11, 9001, sender_is_bot=True, reply_to_msg_id=10,
                    server_event_at=START.timestamp() + 13))
    payload = build(tmp_path, rows)
    module = payload["modules"]["fixture"]
    assert module["sent"] == 2 and module["replied"] == 1 and module["missing"] == 1
    assert module["missing_samples"][0]["chat_id"] == -1001
    assert "not a business failure count" in payload["policy"]


def test_reply_cannot_precede_server_command(tmp_path):
    rows = evidence()
    rows[2]["server_event_at"] = START.timestamp() - 2
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["server_clock_samples"] == 0
    assert module["first_reply"]["p95_sec"] == 851


def test_server_timestamp_after_local_observation_is_ambiguous(tmp_path):
    rows = evidence()
    rows[1]["server_event_at"] = START.timestamp() + 1
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["server_clock_samples"] == 0


def test_missing_clock_for_one_command_does_not_invent_or_erase_other_samples(tmp_path):
    rows = evidence()
    rows.extend([
        row(10, "sent", 20, 2002, source_module="fixture"),
        row(14, "message", 21, 9001, sender_is_bot=True, reply_to_msg_id=20),
    ])
    payload = build(tmp_path, rows)
    module = payload["modules"]["fixture"]
    assert module["sent"] == module["replied"] == 2
    assert module["server_clock_samples"] == 1
    assert module["server_first_reply"]["p95_sec"] == 3
    assert module["first_reply"]["p95_sec"] == 851
    json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize("sender", [None, 0, True])
def test_empty_or_invalid_matching_senders_are_not_ownership(tmp_path, sender):
    rows = evidence()
    rows[0]["sender_id"] = rows[1]["sender_id"] = sender
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["first_reply"]["p95_sec"] == 851
    assert module["server_clock_samples"] == 0


def test_channel_identity_can_own_server_clock_evidence(tmp_path):
    rows = evidence()
    rows[0]["sender_id"] = rows[1]["sender_id"] = -1004321
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["server_clock_samples"] == 1
    assert module["server_first_reply"]["p95_sec"] == 3


def test_unrelated_non_message_metadata_is_not_parsed_as_an_echo(tmp_path):
    rows = evidence()
    rows.append(row(2, "diagnostic", "not-a-message-id", None, chat_id="not-a-chat"))
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["sent"] == module["server_clock_samples"] == 1


@pytest.mark.parametrize("command", [None, "", []])
def test_missing_or_invalid_command_text_cannot_establish_echo(tmp_path, command):
    rows = evidence()
    rows[0]["text"] = rows[1]["text"] = command
    module = build(tmp_path, rows)["modules"]["fixture"]
    assert module["replied"] == 1
    assert module["server_clock_samples"] == 0
