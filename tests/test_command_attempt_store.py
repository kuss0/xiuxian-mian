import copy
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from model import persistence
from model import state as state_module
from model.command_attempt import (
    AttemptConflict,
    BusinessState,
    EvidenceKind,
    IdentityContextRequired,
    RecoveryPolicy,
    TransportState,
    append_evidence,
    create_attempt,
    get_attempt,
    get_attempt_feature_flags,
    list_due_attempts,
    list_evidence,
    list_open_attempts,
    list_transitions,
    mark_business,
    mark_transport,
    prune_terminal_attempts,
)
from tools import attempt_report


@pytest.fixture
def attempt_identity():
    snapshot = copy.deepcopy(state_module._meta_state)
    identity_id = 9001001
    state_module.ensure_identity_registered(identity_id)
    state_module.set_identity_account(identity_id, identity_id)
    yield identity_id
    state_module._meta_state.clear()
    state_module._meta_state.update(snapshot)


def _create(identity_id, suffix, *, now=1_700_000_000.0, **kwargs):
    return create_attempt(
        command=".测试命令",
        send_as_id=identity_id,
        source_module="test_attempt",
        family="test_family",
        op_id=f"test_attempt:{suffix}",
        now=now,
        **kwargs,
    )


def test_feature_flags_default_to_shadow_off():
    names = (
        "XIUXIAN_ATTEMPT_SHADOW_WRITE",
        "XIUXIAN_ATTEMPT_SHADOW_BIND",
        "XIUXIAN_ATTEMPT_RECOVER_REPORT_ONLY",
        "XIUXIAN_ATTEMPT_CONTROL_MODULES",
        "XIUXIAN_ATTEMPT_CONTROL_IDENTITIES",
    )
    env = {key: value for key, value in os.environ.items() if key not in names}
    with patch.dict(os.environ, env, clear=True):
        flags = get_attempt_feature_flags()

    assert flags.shadow_write is False
    assert flags.shadow_bind is False
    assert flags.recover_report_only is False
    assert flags.production_control_enabled is False


def test_create_requires_explicit_or_active_identity(attempt_identity):
    with pytest.raises(IdentityContextRequired):
        create_attempt(command=".测试", source_module="test", op_id="test:no-context")

    with state_module.use_identity(attempt_identity):
        record = create_attempt(
            command=".测试",
            source_module="test",
            op_id="test:active-context",
            now=1_700_000_001.0,
        )

    assert record.send_as_id == attempt_identity


def test_create_persists_initial_projection_and_transitions(attempt_identity):
    record = _create(
        attempt_identity,
        "create",
        chain_id="chain:test",
        intent={"mode": "shadow", "token": "secret-value"},
        meta={"initData": "secret-init", "safe": "visible"},
    )

    assert record.transport is TransportState.CREATED
    assert record.business is BusinessState.OPEN
    assert record.recovery_policy is RecoveryPolicy.WAIT_LATE_EDIT
    assert record.intent["token"] == "[REDACTED]"
    assert record.meta["initData"] == "[REDACTED]"
    assert record.meta["safe"] == "visible"
    assert [item.axis for item in list_transitions(record.op_id)] == ["transport", "business"]


def test_duplicate_op_id_rejects_different_immutable_intent(attempt_identity):
    _create(attempt_identity, "duplicate-op")

    with pytest.raises(AttemptConflict):
        create_attempt(
            command=".另一条命令",
            send_as_id=attempt_identity,
            source_module="test_attempt",
            family="test_family",
            op_id="test_attempt:duplicate-op",
            now=1_700_000_001.0,
        )


def test_transport_and_business_state_graph(attempt_identity):
    record = _create(attempt_identity, "state-graph")
    queued = mark_transport(
        record.op_id,
        TransportState.QUEUED,
        transition_key="transport:queued",
        expected_version=record.version,
    )
    sent = mark_transport(
        record.op_id,
        TransportState.SENT,
        transition_key="transport:sent",
        expected_version=queued.version,
        root_msg_id=12345,
        sent_at=1_700_000_010.0,
    )
    progressed = mark_business(
        record.op_id,
        BusinessState.PROGRESSED,
        transition_key="business:progressed",
        expected_version=sent.version,
        code="reply_new",
    )
    closed = mark_business(
        record.op_id,
        BusinessState.TERMINAL_OK,
        transition_key="business:ok",
        expected_version=progressed.version,
        summary="completed",
        now=1_700_000_020.0,
    )

    assert closed.root_msg_id == 12345
    assert closed.transport is TransportState.SENT
    assert closed.business is BusinessState.TERMINAL_OK
    assert closed.closed_at == 1_700_000_020.0

    with pytest.raises(AttemptConflict):
        mark_business(
            record.op_id,
            BusinessState.PROGRESSED,
            transition_key="business:illegal-reopen",
        )
    with pytest.raises(AttemptConflict):
        mark_transport(
            record.op_id,
            TransportState.SEND_UNKNOWN,
            transition_key="transport:illegal-regress",
        )


def test_transition_key_is_idempotent(attempt_identity):
    record = _create(attempt_identity, "transition-idempotent")
    first = mark_transport(
        record.op_id,
        TransportState.QUEUED,
        transition_key="transport:queued-once",
    )
    second = mark_transport(
        record.op_id,
        TransportState.QUEUED,
        transition_key="transport:queued-once",
    )

    assert second.version == first.version
    assert len(list_transitions(record.op_id)) == 3


def test_compare_and_swap_rejects_stale_version(attempt_identity):
    record = _create(attempt_identity, "cas")
    mark_transport(
        record.op_id,
        TransportState.QUEUED,
        transition_key="transport:queued",
        expected_version=record.version,
    )

    with pytest.raises(AttemptConflict):
        mark_transport(
            record.op_id,
            TransportState.BLOCKED,
            transition_key="transport:blocked-stale",
            expected_version=record.version,
            definitely_unsent=True,
        )


def test_concurrent_updates_allow_only_one_version_winner(attempt_identity):
    record = _create(attempt_identity, "concurrent")

    def update(target, key):
        return mark_transport(
            record.op_id,
            target,
            transition_key=key,
            expected_version=record.version,
            definitely_unsent=target is TransportState.BLOCKED,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(update, TransportState.QUEUED, "transport:queued"),
            pool.submit(update, TransportState.BLOCKED, "transport:blocked"),
        ]
    results = []
    failures = []
    for future in futures:
        try:
            results.append(future.result())
        except AttemptConflict as exc:
            failures.append(exc)

    assert len(results) == 1
    assert len(failures) == 1


def test_evidence_is_idempotent_and_redacted(attempt_identity):
    record = _create(attempt_identity, "evidence")
    first = append_evidence(
        record.op_id,
        kind=EvidenceKind.REPLY_NEW,
        msg_id=22028,
        family="test_family",
        text="真实回复",
        source="live",
        payload={"nested": {"session_cookie": "secret", "value": 7}},
        idempotency_key="reply:22028:new:0",
        now=1_700_000_100.0,
    )
    second = append_evidence(
        record.op_id,
        kind=EvidenceKind.REPLY_NEW,
        msg_id=22028,
        family="test_family",
        text="真实回复",
        source="message_log",
        payload={"different": True},
        idempotency_key="reply:22028:new:0",
        now=1_700_000_200.0,
    )

    assert second.id == first.id
    evidence = list_evidence(record.op_id)
    assert len(evidence) == 1
    assert evidence[0].payload["nested"]["session_cookie"] == "[REDACTED]"
    assert evidence[0].text_digest
    projected = get_attempt(record.op_id)
    assert projected.result_msg_id == 22028
    assert projected.version == record.version + 1


def test_due_open_and_retention_queries(attempt_identity):
    due = _create(
        attempt_identity,
        "due",
        now=1_700_000_000.0,
        transport_due_at=1_700_000_010.0,
    )
    terminal = _create(attempt_identity, "terminal", now=1_600_000_000.0)
    terminal = mark_business(
        terminal.op_id,
        BusinessState.TERMINAL_FAIL,
        transition_key="business:terminal",
        now=1_600_000_100.0,
    )

    assert due.op_id in {item.op_id for item in list_due_attempts(1_700_000_020.0)}
    assert terminal.op_id not in {item.op_id for item in list_open_attempts(send_as_id=attempt_identity)}
    assert prune_terminal_attempts(1_650_000_000.0, limit=10) >= 1
    assert get_attempt(due.op_id).op_id == due.op_id


def test_restart_reopens_attempt_from_sqlite(attempt_identity):
    record = _create(attempt_identity, "restart")
    queued = mark_transport(
        record.op_id,
        TransportState.QUEUED,
        transition_key="transport:queued",
    )

    if persistence._db_conn is not None:
        persistence._db_conn.close()
    persistence._db_conn = None
    persistence._db_initialized = False

    restored = get_attempt(record.op_id)

    assert restored.transport is TransportState.QUEUED
    assert restored.version == queued.version


def test_read_only_report_contains_timeline(attempt_identity):
    record = _create(attempt_identity, "report")
    mark_transport(
        record.op_id,
        TransportState.QUEUED,
        transition_key="transport:queued",
    )
    append_evidence(
        record.op_id,
        kind=EvidenceKind.REPLY_NEW,
        msg_id=33001,
        text="回复文案",
        idempotency_key="reply:33001",
    )

    payload = attempt_report.attempt_payload(record.op_id)

    assert payload["attempt"]["op_id"] == record.op_id
    assert len(payload["transitions"]) == 3
    assert payload["evidence"][0]["msg_id"] == 33001
