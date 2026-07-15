"""Strict, shadow-only inbound evidence binding for CommandAttempt."""

from __future__ import annotations

import logging

from . import store
from .config import get_attempt_feature_flags
from .service import append_evidence
from .types import AttemptNotFound, BindResult, BindStatus, EvidenceKind


_LOG = logging.getLogger(__name__)


def _consistent(attempt, identity_id):
    return int(identity_id or 0) <= 0 or attempt.send_as_id == int(identity_id)


def _result(candidates, *, reason, anchor, identity_id=0, anchored=False):
    candidates = [item for item in candidates if _consistent(item, identity_id)]
    op_ids = tuple(sorted({item.op_id for item in candidates}))
    if anchored and len(op_ids) == 1:
        return BindResult(BindStatus.MATCHED, op_ids[0], op_ids, reason, anchor)
    if op_ids:
        return BindResult(BindStatus.AMBIGUOUS, "", op_ids, reason, anchor)
    return BindResult(BindStatus.UNMATCHED, "", (), reason, anchor)


def classify_evidence_binding(
    *,
    event_kind,
    msg_id=0,
    reply_to_msg_id=0,
    identity_id=0,
    family="",
    op_id="",
    chain_id="",
    event_at=0,
    candidate_window_sec=900,
):
    """Classify binding without mutating the ledger."""
    event_kind = str(event_kind or "message").strip().lower()
    msg_id = int(msg_id or 0)
    reply_to_msg_id = int(reply_to_msg_id or 0)

    if reply_to_msg_id > 0:
        return _result(
            store.list_attempts_by_root_msg_id(reply_to_msg_id),
            reason="exact_reply_to_root",
            anchor="reply_to_msg_id",
            identity_id=identity_id,
            anchored=True,
        )

    if event_kind == "edit" and msg_id > 0:
        result = _result(
            store.list_attempts_by_result_msg_id(msg_id),
            reason="exact_edit_result",
            anchor="result_msg_id",
            identity_id=identity_id,
            anchored=True,
        )
        if result.status is not BindStatus.UNMATCHED:
            return result

    op_id = str(op_id or "").strip()
    if op_id:
        try:
            candidates = [store.get_attempt(op_id)]
        except AttemptNotFound:
            candidates = []
        return _result(
            candidates,
            reason="explicit_op_id",
            anchor="op_id",
            identity_id=identity_id,
            anchored=True,
        )

    chain_id = str(chain_id or "").strip()
    if chain_id:
        return _result(
            store.list_attempts_by_chain_id(chain_id),
            reason="explicit_chain_id",
            anchor="chain_id",
            identity_id=identity_id,
            anchored=True,
        )

    if int(identity_id or 0) <= 0 or float(event_at or 0) <= 0:
        return BindResult(
            BindStatus.UNMATCHED,
            reason="no_strong_anchor",
            anchor="passive_observation",
        )

    candidates = store.list_bind_candidates(
        send_as_id=identity_id,
        family=family,
        event_at=event_at,
        window_sec=candidate_window_sec,
    )
    return _result(
        candidates,
        reason="candidate_only",
        anchor="identity_family_time",
        identity_id=identity_id,
        anchored=False,
    )


def bind_shadow_evidence(
    *,
    event_kind,
    msg_id=0,
    reply_to_msg_id=0,
    identity_id=0,
    family="",
    text="",
    op_id="",
    chain_id="",
    event_at=0,
    edit_seq=0,
    source="live",
    payload=None,
):
    """Bind and persist evidence only when a strong anchor selects one attempt."""
    if not get_attempt_feature_flags().shadow_bind:
        return BindResult(BindStatus.UNMATCHED, reason="shadow_bind_disabled", anchor="flag")
    try:
        result = classify_evidence_binding(
            event_kind=event_kind,
            msg_id=msg_id,
            reply_to_msg_id=reply_to_msg_id,
            identity_id=identity_id,
            family=family,
            op_id=op_id,
            chain_id=chain_id,
            event_at=event_at,
        )
        if result.status is not BindStatus.MATCHED:
            return result
        kind = EvidenceKind.REPLY_EDIT if str(event_kind).lower() == "edit" else EvidenceKind.REPLY_NEW
        append_evidence(
            result.matched_op_id,
            kind=kind,
            msg_id=msg_id,
            edit_seq=edit_seq,
            family=family,
            text=text,
            source=source,
            payload={
                **dict(payload or {}),
                "bind_reason": result.reason,
                "bind_anchor": result.anchor,
            },
            result_msg_id=msg_id,
            now=event_at or None,
        )
        return result
    except Exception as exc:
        _LOG.warning("CommandAttempt shadow bind failed: %s", exc)
        return BindResult(BindStatus.UNMATCHED, reason="shadow_bind_error", anchor="error")


__all__ = ["bind_shadow_evidence", "classify_evidence_binding"]
