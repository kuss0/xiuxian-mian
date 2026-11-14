"""Replay the normal WA chain that raised the 2026-11-14 burst warning."""

import copy
import json
from pathlib import Path

import pytest

from tools import safety_watchdog as watchdog


@pytest.fixture
def sample():
    rows = json.loads((Path(__file__).parent / "fixtures/concubine_heart_burst_20261114.json").read_text())
    for row in rows:
        row["_epoch"] = watchdog.parse_local_ts(row["ts"])
    return rows


def config():
    return watchdog.build_config(watchdog.parse_args(["--project-root", "/tmp/heart-watchdog-test", "--dry-run"]))


def sampled_at():
    return watchdog.parse_local_ts("2026-11-14 18:07:31 UTC+8")


def choices(rows):
    return [row for row in rows if row["event_type"] == "sent" and row["text"] in watchdog.HEART_CHOICE_COMMANDS]


def test_real_eight_sends_are_five_normal_commands_and_three_verified_rounds(sample):
    now, cfg = sampled_at(), config()
    assert watchdog.find_send_breach(sample, now, cfg) == ""
    assert watchdog.count_non_burst_exempt_since(sample, now, 120) == 5
    details = "\n".join(watchdog.send_burst_details(sample, now, cfg, "send burst: 8+ sends in 120s"))
    assert "计数 5｜豁免 3｜身份 1" in details
    assert "msg=1589487" in details  # Launch still counts.
    assert all(f"msg={row['message_id']}" not in details for row in choices(sample))


@pytest.mark.parametrize("number", [1, 2, 3])
def test_in_flight_chain_does_not_need_future_round_or_settlement(sample, number):
    now = choices(sample)[number - 1]["_epoch"]
    sample = [row for row in sample if row["_epoch"] <= now]
    assert watchdog.count_non_burst_exempt_since(sample, now, 120) == 5


@pytest.mark.parametrize("seconds", [120, 300, 900])
def test_burst_window_can_start_after_launch_and_first_round(sample, seconds):
    now = choices(sample)[1]["_epoch"] + seconds
    assert watchdog.count_non_burst_exempt_since(sample, now, seconds) == 0


@pytest.mark.parametrize("fault", [
    "no_receipts", "no_launch", "wrong_chat", "wrong_sender", "wrong_prompt", "wrong_chain",
    "same_round", "skipped_round", "future_receipt", "forwarded", "human", "wrong_bot",
    "bot_changed", "duplicate_op", "duplicate_message", "fourth_choice", "malformed_prompt",
])
def test_unverified_or_repeated_choices_keep_burst_warning(sample, fault):
    answers = choices(sample)
    prompts = [row for row in sample if row["event_type"] in {"message", "edit"}]
    if fault == "no_receipts":
        sample[:] = [row for row in sample if row["event_type"] == "sent"]
    elif fault == "no_launch":
        sample[:] = [row for row in sample if row["text"] != ".共历心劫"]
        sample.append(dict(answers[0], text=".无关命令", family="other", chain_id=""))
    elif fault == "wrong_chat":
        for row in prompts:
            row["chat_id"] -= 1
    elif fault == "wrong_sender":
        for row in answers:
            row["sender_id"] += 1
    elif fault == "wrong_prompt":
        for row in prompts:
            row["reply_to_msg_id"] += 1
    elif fault == "wrong_chain":
        for row in answers:
            row["chain_id"] = "a" * 32
    elif fault == "same_round":
        for row in prompts[1:3]:
            row["text"] = prompts[0]["text"]
    elif fault == "skipped_round":
        prompts[1]["text"] = prompts[2]["text"]
    elif fault == "future_receipt":
        prompts[0]["_epoch"] = sampled_at() + 1
    elif fault in {"forwarded", "human", "wrong_bot"}:
        for row in prompts:
            row.update({"forwarded": True} if fault == "forwarded" else
                       {"sender_is_bot": False} if fault == "human" else
                       {"sender_username": "unrelated_bot"})
    elif fault == "bot_changed":
        prompts[1]["sender_id"] += 1
    elif fault == "duplicate_op":
        answers[1]["op_id"] = answers[0]["op_id"]
    elif fault == "duplicate_message":
        answers[1]["message_id"] = answers[0]["message_id"]
    elif fault == "fourth_choice":
        sample.append(dict(answers[-1], _epoch=sampled_at() - 1, message_id=1589499, op_id="c" * 32))
    elif fault == "malformed_prompt":
        prompts[0]["text"] += "\n【坠魔心劫·第2轮】"
    assert watchdog.find_send_breach(sample, sampled_at(), config()).startswith("send burst:")


def test_fourth_answer_keeps_existing_repeat_fuse_even_below_burst_limit(sample):
    first = choices(sample)[0]
    sample = [row for row in sample if row["_epoch"] <= first["_epoch"]]
    for number in range(1, 4):
        sample.append(dict(first, _epoch=first["_epoch"] + number * 14,
                           message_id=1589491 + number, op_id=f"{number:032x}"))
    cfg = config()
    cfg.total_2m_limit = cfg.total_5m_limit = cfg.total_15m_limit = 99
    assert "same command repeat" in watchdog.find_send_breach(sample, first["_epoch"] + 43, cfg)


def test_round_edit_from_future_cannot_validate_an_earlier_answer(sample):
    prompts = [row for row in sample if row["event_type"] == "edit"]
    prompts[0]["_epoch"] = choices(sample)[1]["_epoch"] + 1
    assert watchdog.find_send_breach(sample, sampled_at(), config()).startswith("send burst:")


def test_duplicate_listener_copies_do_not_create_extra_rounds(sample):
    sample += copy.deepcopy([row for row in sample if row["event_type"] in {"message", "edit"}])
    assert watchdog.find_send_breach(sample, sampled_at(), config()) == ""


def test_receipts_never_exempt_ordinary_commands(sample):
    for number in range(3):
        sample.append(dict(sample[0], _epoch=sampled_at() - 110 + number * 17,
                           text=f".other{number}", family="other", source_module="other",
                           message_id=1600000 + number, op_id=f"{number:032x}"))
    cfg = config()
    cfg.min_any_gap_sec = 0
    assert watchdog.find_send_breach(sample, sampled_at(), cfg) == "send burst: 8+ sends in 120s"
    details = "\n".join(watchdog.send_burst_details(sample, sampled_at(), cfg, "send burst: 8+ sends in 120s"))
    assert "计数 8｜豁免 3" in details
