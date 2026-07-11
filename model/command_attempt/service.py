import hashlib
import re
import time
import uuid

from ..state import get_identity_account
from . import store
from .identity import require_identity_id
from .types import BusinessState, EvidenceKind, RecoveryPolicy, TransportState


def _module_slug(source_module):
    slug = re.sub(r"[^a-z0-9_]+", "_", str(source_module or "attempt").strip().lower()).strip("_")
    return slug or "attempt"


def new_op_id(source_module="attempt"):
    return f"{_module_slug(source_module)}:{uuid.uuid4()}"


def create_attempt(
    *,
    command,
    send_as_id=None,
    source_module="",
    family="",
    priority="",
    chain_id="",
    op_id=None,
    intent=None,
    recovery_policy=RecoveryPolicy.WAIT_LATE_EDIT,
    max_resend=0,
    transport_due_at=0,
    business_due_at=0,
    reply_to_msg_id=0,
    meta=None,
    now=None,
):
    identity_id = require_identity_id(send_as_id)
    now = float(now or time.time())
    op_id = str(op_id or new_op_id(source_module)).strip()
    if not op_id:
        raise ValueError("op_id is required")
    return store.create_attempt(
        {
            "op_id": op_id,
            "chain_id": str(chain_id or ""),
            "send_as_id": identity_id,
            "account_id": int(get_identity_account(identity_id) or 0),
            "source_module": str(source_module or ""),
            "command": str(command or ""),
            "command_family": str(family or ""),
            "priority": str(priority or ""),
            "intent": dict(intent or {}),
            "recovery_policy": RecoveryPolicy(recovery_policy).value,
            "max_resend": max(0, int(max_resend or 0)),
            "transport_due_at": float(transport_due_at or 0),
            "business_due_at": float(business_due_at or 0),
            "reply_to_msg_id": int(reply_to_msg_id or 0),
            "meta": dict(meta or {}),
            "created_at": now,
        }
    )


def get_attempt(op_id):
    return store.get_attempt(op_id)


def mark_transport(
    op_id,
    transport,
    *,
    transition_key,
    expected_version=None,
    code="",
    summary="",
    now=None,
    **updates,
):
    return store.transition_transport(
        op_id,
        TransportState(transport),
        transition_key=transition_key,
        expected_version=expected_version,
        code=code,
        summary=summary,
        now=now,
        updates=updates,
    )


def mark_business(
    op_id,
    business,
    *,
    transition_key,
    expected_version=None,
    code="",
    summary="",
    business_due_at=None,
    now=None,
):
    return store.transition_business(
        op_id,
        BusinessState(business),
        transition_key=transition_key,
        expected_version=expected_version,
        code=code,
        summary=summary,
        business_due_at=business_due_at,
        now=now,
    )


def append_evidence(
    op_id,
    *,
    kind,
    msg_id=0,
    edit_seq=0,
    family="",
    text="",
    source="live",
    payload=None,
    result_msg_id=0,
    idempotency_key="",
    expected_version=None,
    now=None,
):
    text_digest = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest() if text else ""
    if not idempotency_key:
        idempotency_key = store.default_evidence_idempotency_key(
            kind=EvidenceKind(kind).value,
            msg_id=msg_id,
            edit_seq=edit_seq,
            source=source,
            text_digest=text_digest,
            payload=payload,
        )
    return store.append_evidence(
        op_id,
        kind=EvidenceKind(kind),
        idempotency_key=idempotency_key,
        msg_id=msg_id,
        edit_seq=edit_seq,
        family=family,
        text_digest=text_digest,
        source=source,
        payload=payload,
        result_msg_id=result_msg_id,
        expected_version=expected_version,
        now=now,
    )


def list_open_attempts(*, send_as_id=None, limit=100):
    return store.list_open_attempts(send_as_id=send_as_id, limit=limit)


def list_due_attempts(now, *, limit=100):
    return store.list_due_attempts(now, limit=limit)


def list_evidence(op_id):
    return store.list_evidence(op_id)


def list_transitions(op_id):
    return store.list_transitions(op_id)


def prune_terminal_attempts(before_ts, *, limit=1000):
    return store.prune_terminal_attempts(before_ts, limit=limit)


__all__ = [
    "append_evidence",
    "create_attempt",
    "get_attempt",
    "list_due_attempts",
    "list_evidence",
    "list_open_attempts",
    "list_transitions",
    "mark_business",
    "mark_transport",
    "new_op_id",
    "prune_terminal_attempts",
]
