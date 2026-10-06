import json
from copy import deepcopy

import pytest

from tools import business_semantic_report as report


CHAT_A, CHAT_B = -1001, -1002


def row(minute, message_id, text, *, chat_id=CHAT_A, sender_id=101, reply_to=0, sent=False):
    return {
        "ts": f"2026-10-06 10:{minute:02d}:00 UTC+8", "chat_id": chat_id,
        "message_id": message_id, "text": text, "sender_id": sender_id if sent else 900,
        "event_type": "sent" if sent else "message", "sender_is_bot": not sent,
        "reply_to_msg_id": reply_to,
    }


def panel(minute, message_id, faith, *, chat_id=CHAT_A, sender_id=101):
    return [
        row(minute, message_id, ".小世界", chat_id=chat_id, sender_id=sender_id, sent=True),
        row(minute, message_id + 1, f"信仰: {faith} / 100\n稳定: 100 / 100", chat_id=chat_id, reply_to=message_id),
    ]


def build(tmp_path, rows):
    path = tmp_path / "2026-10-06.log"
    path.write_text("\n".join(json.dumps(item) for item in rows), encoding="utf-8")
    return report.build_small_world_evidence(tmp_path, day="2026-10-06", days=1)


def test_equal_message_ids_in_two_chats_do_not_replace_root_owner(tmp_path):
    result = build(tmp_path, panel(0, 10, 90) + panel(1, 10, 10, chat_id=CHAT_B, sender_id=202)
                   + panel(3, 20, 85) + panel(4, 20, 10, chat_id=CHAT_B, sender_id=202))
    assert result["script_roots"] == 4
    assert result["identities"] == [101, 202]
    assert [(item["identity_id"], item["faith_delta"]) for item in result["deltas"]] == [(101, -5)]


def test_foreign_chat_reply_cannot_explain_our_faith(tmp_path):
    result = build(tmp_path, panel(0, 10, 80) + [
        row(1, 12, ".显灵", sent=True),
        row(2, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", chat_id=CHAT_B, reply_to=12),
    ] + panel(3, 20, 85))
    assert result["deltas"][0]["status"] == "unexplained"
    assert result["events"] == []


def test_duplicate_bot_delivery_changes_faith_once(tmp_path):
    reply = row(2, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12)
    result = build(tmp_path, panel(0, 10, 80) + [row(1, 12, ".显灵", sent=True), reply, deepcopy(reply)] + panel(3, 20, 85))
    assert result["deltas"][0]["expected_faith"] == 85
    assert result["deltas"][0]["status"] == "explained"
    assert len(result["events"]) == 1


def test_same_bot_event_indexed_by_reply_and_mention_is_only_applied_once(tmp_path):
    rows = panel(0, 10, 80)
    rows[0]["sender_username"] = "old_name"
    result = build(tmp_path, rows + [row(1, 12, ".显灵", sent=True),
        row(2, 13, "@old_name 显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12),
    ] + panel(3, 20, 85))
    assert result["deltas"][0]["expected_faith"] == 85
    assert len(result["events"]) == 1


def test_distinct_same_number_events_in_two_chats_are_both_retained(tmp_path):
    result = build(tmp_path, panel(0, 10, 70) + [
        row(1, 12, ".显灵", sent=True),
        row(2, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12),
        row(3, 12, ".显灵", chat_id=CHAT_B, sent=True),
        row(4, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", chat_id=CHAT_B, reply_to=12),
    ] + panel(5, 20, 80))
    assert result["deltas"][0]["status"] == "explained"
    assert len(result["events"]) == 2
    assert {event["chat_id"] for event in result["events"]} == {CHAT_A, CHAT_B}


@pytest.mark.parametrize("scope", [None, 0, True, "-1001", 1.5])
def test_invalid_chat_scope_is_not_silently_adopted(tmp_path, scope):
    rows = panel(0, 10, 80, chat_id=scope) + panel(1, 20, 85, chat_id=scope)
    result = build(tmp_path, rows)
    assert result["script_panels"] == result["script_roots"] == 0
    assert result["scope"]["unscoped_roots"] == 2


def test_missing_chat_scope_is_visible_not_inferred(tmp_path):
    rows = panel(0, 10, 80)
    for item in rows:
        item.pop("chat_id")
    result = build(tmp_path, rows)
    assert result["script_panels"] == 0
    assert result["scope"]["unscoped_roots"] == 1


def test_conflicting_root_identity_is_excluded(tmp_path):
    rows = panel(0, 10, 80)
    rows.append(row(1, 10, ".小世界", sender_id=202, sent=True))
    result = build(tmp_path, rows)
    assert result["script_panels"] == result["script_roots"] == 0
    assert result["scope"]["conflicting_roots"] == 1


def test_username_suffix_is_not_our_broadcast(tmp_path):
    rows = panel(0, 10, 90)
    rows[0]["sender_username"] = "old_name"
    result = build(tmp_path, rows + [row(1, 12, "@old_name1 信仰 -5 点 库存香火损失 500 点")] + panel(3, 20, 85))
    assert result["deltas"][0]["status"] == "unexplained"
    assert result["events"] == []


def test_exact_old_username_broadcast_is_retained(tmp_path):
    rows = panel(0, 10, 90)
    rows[0]["sender_username"] = "old_name"
    next_panel = panel(3, 20, 85)
    next_panel[0]["sender_username"] = "new_name"
    result = build(tmp_path, rows + [row(1, 12, "@OLD_NAME 信仰 -5 点")] + next_panel)
    assert result["deltas"][0]["status"] == "explained"
    assert result["events"][0]["chat_id"] == CHAT_A


def test_conflicting_versions_of_one_reply_never_prove_a_sum(tmp_path):
    reply = row(2, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12)
    edit = {**reply, "event_type": "edit", "text": "显灵成功 (信仰 +7, 稳定 +0, 人口 +0)"}
    result = build(tmp_path, panel(0, 10, 70) + [row(1, 12, ".显灵", sent=True), reply, edit] + panel(3, 20, 82))
    assert result["deltas"][0]["status"] == "partially_explained"
    assert result["deltas"][0]["expected_faith"] is None
    assert result["deltas"][0]["conflicting_events"] == 1
    assert len(result["events"]) == 2
    assert all(event["conflicting"] for event in result["events"])


def test_identical_edit_or_duplicate_root_is_idempotent(tmp_path):
    rows = panel(0, 10, 80)
    command = row(1, 12, ".显灵", sent=True)
    reply = row(2, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12)
    result = build(tmp_path, rows + [deepcopy(rows[0]), command, deepcopy(command), reply,
        {**reply, "event_type": "edit"},
    ] + panel(3, 20, 85))
    assert result["script_roots"] == 3
    assert result["deltas"][0]["status"] == "explained"
    assert result["deltas"][0]["conflicting_events"] == 0
    assert len(result["events"]) == 1


@pytest.mark.parametrize("field", ["message_id", "sender_id"])
@pytest.mark.parametrize("value", [True, "101", None, 0, -101, 2**63])
def test_invalid_root_ids_do_not_establish_ownership(tmp_path, field, value):
    rows = panel(0, 10, 80)
    rows[0][field] = value
    result = build(tmp_path, rows)
    assert result["script_roots"] == result["script_panels"] == 0


def test_conflicting_command_for_same_root_is_not_last_writer_wins(tmp_path):
    rows = panel(0, 10, 80)
    result = build(tmp_path, rows + [row(1, 10, ".显灵", sent=True), deepcopy(rows[0])])
    assert result["script_panels"] == 0
    assert result["scope"]["conflicting_roots"] == 1


def test_duplicate_loss_broadcast_is_applied_once(tmp_path):
    rows = panel(0, 10, 90)
    rows[0]["sender_username"] = "old_name"
    loss = row(1, 12, "@old_name 信仰 -5 点 库存香火损失 500 点")
    result = build(tmp_path, rows + [loss, deepcopy(loss)] + panel(3, 20, 85))
    assert result["deltas"][0]["status"] == "explained"
    assert result["deltas"][0]["expected_faith"] == 85
    assert len(result["events"]) == 2


def test_missing_reply_scope_is_not_inferred_from_unique_command(tmp_path):
    reply = row(2, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12)
    reply.pop("chat_id")
    result = build(tmp_path, panel(0, 10, 80) + [row(1, 12, ".显灵", sent=True), reply] + panel(3, 20, 85))
    assert result["deltas"][0]["status"] == "unexplained"
    assert result["events"] == []


def test_reassigned_username_broadcast_is_not_claimed_for_either_owner(tmp_path):
    rows = panel(0, 10, 90)
    rows[0]["sender_username"] = "old_name"
    other = row(1, 12, "hello", sent=True, sender_id=202)
    other.update(event_type="message", sender_username="old_name", sender_is_bot=False)
    result = build(tmp_path, rows + [other, row(2, 13, "@old_name 信仰 -5 点")] + panel(3, 20, 85))
    assert result["scope"]["ambiguous_aliases"] == 1
    assert result["deltas"][0]["status"] == "unexplained"
    assert result["events"] == []


@pytest.mark.parametrize("is_bot", [False, "true", 1, None])
def test_non_bot_evidence_cannot_change_faith(tmp_path, is_bot):
    reply = row(2, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12)
    reply["sender_is_bot"] = is_bot
    result = build(tmp_path, panel(0, 10, 80) + [row(1, 12, ".显灵", sent=True), reply] + panel(3, 20, 85))
    assert result["deltas"][0]["status"] == "unexplained"


def test_indexed_and_direct_evidence_have_the_same_deduplication(tmp_path):
    rows = panel(0, 10, 80)
    rows[0]["sender_username"] = "old_name"
    command = row(1, 12, ".显灵", sent=True)
    reply = row(2, 13, "@old_name 显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12)
    rows += [command, reply, deepcopy(reply)] + panel(3, 20, 85)
    indexed = build(tmp_path, rows)["deltas"][0]
    direct = report._event_explanations(
        rows, {(CHAT_A, 12): command}, 101, report._parse_ts(rows[0]["ts"]),
        report._parse_ts(rows[-1]["ts"]), 80, 85,
    )
    assert direct["expected_faith"] == indexed["expected_faith"] == 85
    assert direct["events"] == indexed["events"]


def test_repeated_absolute_faith_cannot_undo_a_later_loss(tmp_path):
    absolute = row(1, 13, "信仰提升至 90", reply_to=12)
    later_duplicate = {**absolute, "ts": "2026-10-06 10:04:00 UTC+8"}
    rows = panel(0, 10, 80)
    rows[0]["sender_username"] = "old_name"
    result = build(tmp_path, rows + [row(1, 12, ".神迹 布道", sent=True), absolute,
        row(3, 15, "@old_name 信仰 -5 点"), later_duplicate,
    ] + panel(5, 20, 85))
    assert result["deltas"][0]["status"] == "explained"
    assert result["deltas"][0]["expected_faith"] == 85
    assert len(result["events"]) == 2


def test_late_duplicate_cannot_explain_another_panel_interval(tmp_path):
    reply = row(2, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12)
    duplicate = {**reply, "ts": "2026-10-06 10:04:00 UTC+8"}
    result = build(tmp_path, panel(0, 10, 80) + [row(1, 12, ".显灵", sent=True), reply]
                   + panel(3, 20, 85) + [duplicate] + panel(5, 30, 90))
    assert [delta["status"] for delta in result["deltas"]] == ["explained", "unexplained"]
    assert len(result["events"]) == 1


def test_conflicting_late_edit_is_not_a_new_round(tmp_path):
    reply = row(2, 13, "显灵成功 (信仰 +5, 稳定 +0, 人口 +0)", reply_to=12)
    edit = row(4, 13, "显灵成功 (信仰 +7, 稳定 +0, 人口 +0)", reply_to=12)
    edit["event_type"] = "edit"
    result = build(tmp_path, panel(0, 10, 80) + [row(1, 12, ".显灵", sent=True), reply]
                   + panel(3, 20, 85) + [edit] + panel(5, 30, 92))
    assert all(delta["expected_faith"] is None for delta in result["deltas"])
    assert all(delta["conflicting_events"] == 1 for delta in result["deltas"])
