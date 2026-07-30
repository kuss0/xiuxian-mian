"""Pure target-group membership classification for account gating.

This module deliberately does not mutate runtime state.  It provides the
classification and account-to-identity projection needed by a later runtime
gate without making startup or scheduler RPC calls on import.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from telethon import errors, functions, types, utils


ACCOUNT_MEMBERSHIP_MEMBER_PROBE_INTERVAL_SEC = 6 * 60 * 60
ACCOUNT_MEMBERSHIP_RETRY_INTERVAL_SEC = 15 * 60


class TargetGroupMembership(str, Enum):
    MEMBER = "member"
    NOT_MEMBER = "not_member"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


_DEFINITIVE_NOT_MEMBER_ERRORS = (
    errors.UserNotParticipantError,
    errors.ChannelPrivateError,
    errors.ChannelInvalidError,
)
_DEFINITIVE_NOT_MEMBER_NAMES = frozenset({
    "USER_NOT_PARTICIPANT",
    "CHANNEL_PRIVATE",
    "CHANNEL_INVALID",
})


@dataclass(frozen=True)
class TargetGroupMembershipProbe:
    status: TargetGroupMembership
    reason: str = ""
    error_name: str = ""


@dataclass(frozen=True)
class AccountMembershipGate:
    account_id: int
    identity_ids: tuple[int, ...]
    status: TargetGroupMembership
    block_group_commands: bool
    reason: str = ""


def _rpc_error_name(exc: BaseException) -> str:
    message = str(getattr(exc, "message", "") or "").strip().upper()
    if message in _DEFINITIVE_NOT_MEMBER_NAMES:
        return message
    text = str(exc or "").upper()
    for name in _DEFINITIVE_NOT_MEMBER_NAMES:
        if name in text:
            return name
    return exc.__class__.__name__.upper()


def classify_membership_error(exc: BaseException) -> TargetGroupMembershipProbe:
    """Classify only deterministic Telegram negatives as not-member.

    Flood waits, transport errors, Telegram internal failures, entity-cache
    misses, and all unrecognised errors remain unknown and must not disable an
    account or identity.
    """

    error_name = _rpc_error_name(exc)
    if isinstance(exc, _DEFINITIVE_NOT_MEMBER_ERRORS) or error_name in _DEFINITIVE_NOT_MEMBER_NAMES:
        return TargetGroupMembershipProbe(
            TargetGroupMembership.NOT_MEMBER,
            reason=str(exc or error_name),
            error_name=error_name,
        )
    return TargetGroupMembershipProbe(
        TargetGroupMembership.UNKNOWN,
        reason=str(exc or error_name),
        error_name=error_name,
    )


async def probe_target_group_membership(client, target) -> TargetGroupMembershipProbe:
    """Read the current account's membership without changing local state."""

    try:
        peer = await client.get_input_entity(target)
    except Exception as exc:
        return classify_membership_error(exc)

    if isinstance(peer, (types.InputPeerUser, types.InputPeerChat)):
        return TargetGroupMembershipProbe(TargetGroupMembership.NOT_APPLICABLE)

    try:
        channel = utils.get_input_channel(peer)
    except (TypeError, ValueError) as exc:
        return TargetGroupMembershipProbe(
            TargetGroupMembership.UNKNOWN,
            reason=str(exc),
            error_name=exc.__class__.__name__.upper(),
        )

    try:
        await client(functions.channels.GetParticipantRequest(
            channel=channel,
            participant=types.InputPeerSelf(),
        ))
    except Exception as exc:
        return classify_membership_error(exc)
    return TargetGroupMembershipProbe(TargetGroupMembership.MEMBER)


def resolve_account_identity_ids(
    account_id: int,
    identity_account_map: Mapping,
    configured_identity_ids: Sequence[int] = (),
) -> tuple[int, ...]:
    """Project one login account to its personal and channel identities."""

    account_id = int(account_id or 0)
    if account_id <= 0:
        return ()
    configured = set()
    for raw_identity_id in configured_identity_ids:
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError):
            continue
        if identity_id > 0:
            configured.add(identity_id)

    identity_ids = set()
    for raw_identity_id, raw_account_id in (identity_account_map or {}).items():
        try:
            identity_id = int(raw_identity_id or 0)
            mapped_account_id = int(raw_account_id or 0)
        except (TypeError, ValueError):
            continue
        if identity_id > 0 and mapped_account_id == account_id:
            identity_ids.add(identity_id)

    # A personal identity can predate the explicit identity-account map.  Only
    # infer it when the configured send-as id exactly equals the login account.
    if account_id in configured:
        identity_ids.add(account_id)
    return tuple(sorted(identity_ids))


def build_account_membership_gate(
    account_id: int,
    probe: TargetGroupMembershipProbe,
    identity_account_map: Mapping,
    configured_identity_ids: Sequence[int] = (),
) -> AccountMembershipGate:
    """Build a side-effect-free gate decision for one login account."""

    account_id = int(account_id or 0)
    return AccountMembershipGate(
        account_id=account_id,
        identity_ids=resolve_account_identity_ids(
            account_id,
            identity_account_map,
            configured_identity_ids,
        ),
        status=probe.status,
        block_group_commands=probe.status is TargetGroupMembership.NOT_MEMBER,
        reason=str(probe.reason or ""),
    )


def merge_account_membership_probe(
    previous: Mapping | None,
    probe: TargetGroupMembershipProbe,
    *,
    account_id: int,
    identity_ids: Sequence[int],
    game_group_id: int,
    now: float,
    member_probe_interval_sec: float = ACCOUNT_MEMBERSHIP_MEMBER_PROBE_INTERVAL_SEC,
    retry_interval_sec: float = ACCOUNT_MEMBERSHIP_RETRY_INTERVAL_SEC,
) -> dict:
    """Merge one probe without letting a transient result flip a known state."""

    previous = dict(previous or {})
    account_id = int(account_id or 0)
    game_group_id = int(game_group_id or 0)
    same_group = int(previous.get("game_group_id") or 0) == game_group_id
    previous_status = str(previous.get("status") or TargetGroupMembership.UNKNOWN.value)
    previous_definitive = previous_status in {
        TargetGroupMembership.MEMBER.value,
        TargetGroupMembership.NOT_MEMBER.value,
        TargetGroupMembership.NOT_APPLICABLE.value,
    }
    probe_status = probe.status.value
    if probe.status is TargetGroupMembership.UNKNOWN and same_group and previous_definitive:
        effective_status = previous_status
        effective_reason = str(previous.get("reason") or "")
    else:
        effective_status = probe_status
        effective_reason = str(probe.reason or "")

    normalized_identity_ids = []
    for raw_identity_id in identity_ids:
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError):
            continue
        if identity_id > 0:
            normalized_identity_ids.append(identity_id)

    interval = (
        float(member_probe_interval_sec)
        if probe.status in {TargetGroupMembership.MEMBER, TargetGroupMembership.NOT_APPLICABLE}
        else float(retry_interval_sec)
    )
    last_definitive_at = float(previous.get("last_definitive_at") or 0) if same_group else 0.0
    if probe.status is not TargetGroupMembership.UNKNOWN:
        last_definitive_at = float(now)
    return {
        "account_id": account_id,
        "game_group_id": game_group_id,
        "identity_ids": sorted(set(normalized_identity_ids)),
        "status": effective_status,
        "probe_status": probe_status,
        "reason": effective_reason,
        "last_error": str(probe.reason or "") if probe.status is TargetGroupMembership.UNKNOWN else "",
        "error_name": str(probe.error_name or ""),
        "checked_at": float(now),
        "last_definitive_at": last_definitive_at,
        "next_probe_at": float(now) + max(60.0, interval),
    }


__all__ = [
    "AccountMembershipGate",
    "TargetGroupMembership",
    "TargetGroupMembershipProbe",
    "build_account_membership_gate",
    "classify_membership_error",
    "merge_account_membership_probe",
    "probe_target_group_membership",
    "resolve_account_identity_ids",
]
