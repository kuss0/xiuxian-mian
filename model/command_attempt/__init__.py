"""Shadow-only CommandAttempt ledger APIs.

Gate 1 does not integrate these APIs with runtime sending or inbound routing.
"""

from .config import AttemptFeatureFlags, get_attempt_feature_flags
from .identity import IdentityContextRequired, require_identity_id
from .service import (
    append_evidence,
    create_attempt,
    get_attempt,
    list_due_attempts,
    list_evidence,
    list_open_attempts,
    list_transitions,
    mark_business,
    mark_transport,
    prune_terminal_attempts,
)
from .types import (
    AttemptConflict,
    AttemptEvidence,
    AttemptNotFound,
    AttemptRecord,
    AttemptTransition,
    BusinessState,
    EvidenceKind,
    RecoveryPolicy,
    TransportState,
)

__all__ = [
    "AttemptConflict",
    "AttemptEvidence",
    "AttemptFeatureFlags",
    "AttemptNotFound",
    "AttemptRecord",
    "AttemptTransition",
    "BusinessState",
    "EvidenceKind",
    "IdentityContextRequired",
    "RecoveryPolicy",
    "TransportState",
    "append_evidence",
    "create_attempt",
    "get_attempt",
    "get_attempt_feature_flags",
    "list_due_attempts",
    "list_evidence",
    "list_open_attempts",
    "list_transitions",
    "mark_business",
    "mark_transport",
    "prune_terminal_attempts",
    "require_identity_id",
]
