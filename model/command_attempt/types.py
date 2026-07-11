from dataclasses import dataclass
from enum import Enum


class _TextEnum(str, Enum):
    def __str__(self):
        return self.value


class TransportState(_TextEnum):
    CREATED = "created"
    QUEUED = "queued"
    BLOCKED = "blocked"
    SENT_NO_ID = "sent_no_id"
    SENT = "sent"
    SEND_UNKNOWN = "send_unknown"
    TIMED_OUT = "timed_out"
    ABANDONED = "abandoned"


class BusinessState(_TextEnum):
    OPEN = "open"
    PROGRESSED = "progressed"
    MANUAL_REQUIRED = "manual_required"
    TERMINAL_OK = "terminal_ok"
    TERMINAL_FAIL = "terminal_fail"
    ABANDONED = "abandoned"


class RecoveryPolicy(_TextEnum):
    WAIT_LATE_EDIT = "wait_late_edit"
    RECOVER_FROM_LOG = "recover_from_log"
    ALLOW_RESEND = "allow_resend"
    MANUAL_REQUIRED = "manual_required"
    NO_RESEND = "no_resend"


class EvidenceKind(_TextEnum):
    SEND = "send"
    REPLY_NEW = "reply_new"
    REPLY_EDIT = "reply_edit"
    BUTTON = "button"
    PANEL = "panel"
    LOG_RECOVER = "log_recover"
    BROADCAST_BIND = "broadcast_bind"


class BindStatus(_TextEnum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"


TRANSPORT_TRANSITIONS = {
    TransportState.CREATED: {
        TransportState.QUEUED,
        TransportState.BLOCKED,
        TransportState.ABANDONED,
    },
    TransportState.QUEUED: {
        TransportState.BLOCKED,
        TransportState.SENT_NO_ID,
        TransportState.SENT,
        TransportState.SEND_UNKNOWN,
        TransportState.ABANDONED,
    },
    TransportState.SENT_NO_ID: {
        TransportState.SENT,
        TransportState.SEND_UNKNOWN,
        TransportState.TIMED_OUT,
        TransportState.ABANDONED,
    },
    TransportState.SEND_UNKNOWN: {
        TransportState.SENT,
        TransportState.TIMED_OUT,
        TransportState.ABANDONED,
    },
    TransportState.TIMED_OUT: {
        TransportState.SENT,
        TransportState.ABANDONED,
    },
    TransportState.BLOCKED: set(),
    TransportState.SENT: set(),
    TransportState.ABANDONED: set(),
}


BUSINESS_TRANSITIONS = {
    BusinessState.OPEN: {
        BusinessState.PROGRESSED,
        BusinessState.MANUAL_REQUIRED,
        BusinessState.TERMINAL_OK,
        BusinessState.TERMINAL_FAIL,
        BusinessState.ABANDONED,
    },
    BusinessState.PROGRESSED: {
        BusinessState.MANUAL_REQUIRED,
        BusinessState.TERMINAL_OK,
        BusinessState.TERMINAL_FAIL,
        BusinessState.ABANDONED,
    },
    BusinessState.MANUAL_REQUIRED: {
        BusinessState.PROGRESSED,
        BusinessState.TERMINAL_OK,
        BusinessState.TERMINAL_FAIL,
        BusinessState.ABANDONED,
    },
    BusinessState.TERMINAL_OK: set(),
    BusinessState.TERMINAL_FAIL: set(),
    BusinessState.ABANDONED: set(),
}


class AttemptError(RuntimeError):
    pass


class AttemptNotFound(AttemptError):
    pass


class AttemptConflict(AttemptError):
    pass


@dataclass(frozen=True)
class AttemptRecord:
    op_id: str
    chain_id: str
    send_as_id: int
    account_id: int
    source_module: str
    command: str
    command_family: str
    priority: str
    intent: dict
    transport: TransportState
    business: BusinessState
    recovery_policy: RecoveryPolicy
    block_code: str
    block_reason: str
    definitely_unsent: bool
    root_msg_id: int
    reply_to_msg_id: int
    result_msg_id: int
    resend_count: int
    max_resend: int
    transport_due_at: float
    business_due_at: float
    business_code: str
    business_summary: str
    last_error: str
    last_transition_key: str
    meta: dict
    version: int
    created_at: float
    updated_at: float
    sent_at: float
    closed_at: float


@dataclass(frozen=True)
class AttemptTransition:
    id: int
    op_id: str
    seq: int
    axis: str
    from_state: str
    to_state: str
    code: str
    summary: str
    transition_key: str
    ts: float


@dataclass(frozen=True)
class AttemptEvidence:
    id: int
    op_id: str
    seq: int
    kind: EvidenceKind
    msg_id: int
    edit_seq: int
    family: str
    text_digest: str
    source: str
    idempotency_key: str
    ts: float
    payload: dict


@dataclass(frozen=True)
class BindResult:
    status: BindStatus
    matched_op_id: str = ""
    candidate_op_ids: tuple[str, ...] = ()
    reason: str = ""
    anchor: str = ""


__all__ = [
    "AttemptConflict",
    "AttemptError",
    "AttemptEvidence",
    "AttemptNotFound",
    "AttemptRecord",
    "AttemptTransition",
    "BindResult",
    "BindStatus",
    "BUSINESS_TRANSITIONS",
    "BusinessState",
    "EvidenceKind",
    "RecoveryPolicy",
    "TRANSPORT_TRANSITIONS",
    "TransportState",
]
