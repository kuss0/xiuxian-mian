import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from model import state as state_module
from model.command_attempt import (
    BindStatus,
    EvidenceKind,
    TransportState,
    bind_shadow_evidence,
    classify_evidence_binding,
    create_attempt,
    list_evidence,
    mark_transport,
)
from tools import attempt_bind_replay


@pytest.fixture
def bind_identities():
    snapshot = copy.deepcopy(state_module._meta_state)
    first_id, second_id = 9003001, 9003002
    for identity_id in (first_id, second_id):
        state_module.ensure_identity_registered(identity_id)
        state_module.set_identity_account(identity_id, identity_id)
    yield first_id, second_id
    state_module._meta_state.clear()
    state_module._meta_state.update(snapshot)


def _sent(identity_id, suffix, *, root_msg_id, family="bind_family", chain_id="", sent_at=1_700_000_000.0):
    record = create_attempt(
        command=f".绑定测试 {suffix}",
        send_as_id=identity_id,
        source_module="bind_test",
        family=family,
        chain_id=chain_id,
        op_id=f"bind_test:{suffix}",
        now=sent_at - 1,
    )
    queued = mark_transport(
        record.op_id,
        TransportState.QUEUED,
        transition_key=f"bind:{suffix}:queued",
    )
    return mark_transport(
        record.op_id,
        TransportState.SENT,
        transition_key=f"bind:{suffix}:sent",
        expected_version=queued.version,
        root_msg_id=root_msg_id,
        sent_at=sent_at,
    )


def test_exact_reply_then_exact_edit_bind_and_append_idempotently(bind_identities):
    identity_id, _ = bind_identities
    attempt = _sent(identity_id, "reply-edit", root_msg_id=31001)

    with patch.dict("os.environ", {"XIUXIAN_ATTEMPT_SHADOW_BIND": "1"}):
        reply_result = bind_shadow_evidence(
            event_kind="message",
            msg_id=41001,
            reply_to_msg_id=attempt.root_msg_id,
            identity_id=identity_id,
            family="bind_family",
            text="开始处理",
            event_at=1_700_000_010.0,
        )
        duplicate_result = bind_shadow_evidence(
            event_kind="message",
            msg_id=41001,
            reply_to_msg_id=attempt.root_msg_id,
            identity_id=identity_id,
            family="bind_family",
            text="开始处理",
            event_at=1_700_000_011.0,
        )
        edit_result = bind_shadow_evidence(
            event_kind="edit",
            msg_id=41001,
            identity_id=identity_id,
            family="bind_family",
            text="处理完成",
            event_at=1_700_000_020.0,
        )

    assert reply_result.status is BindStatus.MATCHED
    assert duplicate_result.status is BindStatus.MATCHED
    assert edit_result.status is BindStatus.MATCHED
    evidence = list_evidence(attempt.op_id)
    assert [item.kind for item in evidence] == [EvidenceKind.REPLY_NEW, EvidenceKind.REPLY_EDIT]
    assert all(item.msg_id == 41001 for item in evidence)


def test_exact_anchor_with_identity_contradiction_fails_closed(bind_identities):
    first_id, second_id = bind_identities
    _sent(first_id, "identity-mismatch", root_msg_id=31002)

    result = classify_evidence_binding(
        event_kind="message",
        msg_id=41002,
        reply_to_msg_id=31002,
        identity_id=second_id,
    )

    assert result.status is BindStatus.UNMATCHED
    assert result.matched_op_id == ""


def test_duplicate_exact_root_is_ambiguous(bind_identities):
    first_id, _ = bind_identities
    _sent(first_id, "duplicate-root-a", root_msg_id=31003)
    _sent(first_id, "duplicate-root-b", root_msg_id=31003)

    result = classify_evidence_binding(
        event_kind="message",
        msg_id=41003,
        reply_to_msg_id=31003,
        identity_id=first_id,
    )

    assert result.status is BindStatus.AMBIGUOUS
    assert len(result.candidate_op_ids) == 2


def test_explicit_op_and_chain_follow_strict_cardinality(bind_identities):
    first_id, _ = bind_identities
    one = _sent(first_id, "explicit-op", root_msg_id=31004, chain_id="chain:one")
    _sent(first_id, "chain-a", root_msg_id=31005, chain_id="chain:many")
    _sent(first_id, "chain-b", root_msg_id=31006, chain_id="chain:many")

    by_op = classify_evidence_binding(event_kind="message", op_id=one.op_id, identity_id=first_id)
    by_chain = classify_evidence_binding(event_kind="message", chain_id="chain:many", identity_id=first_id)

    assert by_op.status is BindStatus.MATCHED
    assert by_op.matched_op_id == one.op_id
    assert by_chain.status is BindStatus.AMBIGUOUS


def test_identity_family_time_never_promotes_candidate_to_match(bind_identities):
    first_id, _ = bind_identities
    attempt = _sent(first_id, "heuristic-only", root_msg_id=31007, family="heuristic_family")

    result = classify_evidence_binding(
        event_kind="message",
        msg_id=41007,
        identity_id=first_id,
        family="heuristic_family",
        event_at=attempt.sent_at + 10,
    )

    assert result.status is BindStatus.AMBIGUOUS
    assert result.matched_op_id == ""
    assert attempt.op_id in result.candidate_op_ids
    assert result.anchor == "identity_family_time"


def test_unanchored_broadcast_remains_unmatched(bind_identities):
    result = classify_evidence_binding(
        event_kind="message",
        msg_id=41008,
    )
    assert result.status is BindStatus.UNMATCHED


def test_shadow_bind_flag_off_does_not_append(bind_identities):
    first_id, _ = bind_identities
    attempt = _sent(first_id, "flag-off", root_msg_id=31009)
    with patch.dict("os.environ", {"XIUXIAN_ATTEMPT_SHADOW_BIND": "0"}):
        result = bind_shadow_evidence(
            event_kind="message",
            msg_id=41009,
            reply_to_msg_id=attempt.root_msg_id,
            identity_id=first_id,
            text="不会写入",
        )
    assert result.status is BindStatus.UNMATCHED
    assert list_evidence(attempt.op_id) == []


def test_offline_replay_reports_decisions_without_persisting(bind_identities, tmp_path):
    first_id, _ = bind_identities
    attempt = _sent(first_id, "offline-replay", root_msg_id=31010)
    path = Path(tmp_path) / "messages.jsonl"
    path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "event_type": "message",
                        "message_id": 41010,
                        "reply_to_msg_id": attempt.root_msg_id,
                        "send_as_id": first_id,
                        "ts_epoch": attempt.sent_at + 5,
                    }
                ),
                json.dumps({"event_type": "message", "message_id": 41011, "text": "广播"}),
            )
        ),
        encoding="utf-8",
    )

    report = attempt_bind_replay.replay_file(path)

    assert report["counts"] == {"matched": 1, "unmatched": 1}
    assert report["rows"][0]["matched_op_id"] == attempt.op_id
    assert list_evidence(attempt.op_id) == []
