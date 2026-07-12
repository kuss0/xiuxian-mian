"""Best-effort runtime bridge for the shadow-only CommandAttempt ledger."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .config import get_attempt_feature_flags
from .service import create_attempt, mark_transport
from .types import TransportState


_LOG = logging.getLogger(__name__)
_CURRENT_SCOPE = ContextVar("command_attempt_shadow_scope", default=None)


@dataclass
class ShadowAttemptScope:
    op_id: str
    queued: bool = False
    sending: bool = False
    terminal: bool = False


def _diagnose(action, exc):
    _LOG.warning("CommandAttempt shadow %s failed: %s", action, exc)


@contextmanager
def shadow_attempt_scope(
    *,
    command,
    send_as_id,
    source_module="",
    family="",
    priority="",
    chain_id="",
    intent=None,
    legacy_op_id="",
    reply_to_msg_id=0,
):
    """Open one task-local shadow attempt without affecting send behavior."""
    if not get_attempt_feature_flags().shadow_write or not send_as_id:
        yield None
        return

    try:
        record = create_attempt(
            command=command,
            send_as_id=send_as_id,
            source_module=source_module or "runtime",
            family=family,
            priority=priority,
            chain_id=chain_id,
            intent=intent,
            reply_to_msg_id=reply_to_msg_id,
            meta={"legacy_op_id": legacy_op_id} if legacy_op_id else {},
        )
    except Exception as exc:
        _diagnose("create", exc)
        yield None
        return

    scope = ShadowAttemptScope(op_id=record.op_id)
    token = _CURRENT_SCOPE.set(scope)
    try:
        yield scope
    finally:
        if not scope.terminal:
            note_abandoned("scope_exit_without_terminal")
        _CURRENT_SCOPE.reset(token)


def _scope():
    scope = _CURRENT_SCOPE.get()
    if scope is None or scope.terminal:
        return None
    return scope


def note_queued():
    scope = _scope()
    if scope is None or scope.queued:
        return
    try:
        mark_transport(
            scope.op_id,
            TransportState.QUEUED,
            transition_key="runtime:queued",
        )
        scope.queued = True
    except Exception as exc:
        _diagnose("queue", exc)


def note_sending():
    scope = _scope()
    if scope is None or scope.sending:
        return
    try:
        if not scope.queued:
            note_queued()
        # Keep this boundary task-local for Gate 0-3. The approved transport
        # state model has no persisted `sending` state yet.
        scope.sending = True
    except Exception as exc:
        _diagnose("sending", exc)


def note_abandoned(reason="scope_exit_without_terminal"):
    scope = _scope()
    if scope is None:
        return
    reason = str(reason or "scope_exit_without_terminal")
    target = TransportState.SEND_UNKNOWN if scope.sending else TransportState.BLOCKED
    definitely_unsent = not scope.sending
    try:
        mark_transport(
            scope.op_id,
            target,
            transition_key=f"runtime:abandoned:{reason}",
            code=reason,
            summary=reason,
            block_code=reason,
            block_reason=reason,
            definitely_unsent=definitely_unsent,
            last_error="" if definitely_unsent else reason,
        )
        scope.terminal = True
    except Exception as exc:
        _diagnose("abandon", exc)


def note_blocked(code, reason, *, definitely_unsent=False):
    scope = _scope()
    if scope is None:
        return
    code = str(code or "blocked")
    definitely_unsent = bool(definitely_unsent)
    target = TransportState.BLOCKED if definitely_unsent else TransportState.SEND_UNKNOWN
    if target is TransportState.SEND_UNKNOWN and not scope.queued:
        # No send-slot entry means the RPC was never started. Keep the ledger
        # truthful even when the legacy block classifier is conservative.
        target = TransportState.BLOCKED
        definitely_unsent = True
    try:
        mark_transport(
            scope.op_id,
            target,
            transition_key=f"runtime:block:{code}",
            code=code,
            summary=str(reason or ""),
            block_code=code,
            block_reason=str(reason or ""),
            definitely_unsent=definitely_unsent,
            last_error="" if definitely_unsent else str(reason or ""),
        )
        scope.terminal = True
    except Exception as exc:
        _diagnose("block", exc)


def note_sent(msg_id, *, sent_at=None):
    scope = _scope()
    if scope is None:
        return
    try:
        if not scope.queued:
            note_queued()
        mark_transport(
            scope.op_id,
            TransportState.SENT,
            transition_key=f"runtime:sent:{int(msg_id or 0)}",
            root_msg_id=int(msg_id or 0),
            sent_at=sent_at,
        )
        scope.terminal = True
    except Exception as exc:
        _diagnose("sent", exc)


def current_shadow_op_id():
    scope = _CURRENT_SCOPE.get()
    return str(scope.op_id) if scope is not None else ""


__all__ = [
    "ShadowAttemptScope",
    "note_abandoned",
    "current_shadow_op_id",
    "note_blocked",
    "note_queued",
    "note_sending",
    "note_sent",
    "shadow_attempt_scope",
]
