"""Report-only search-node API fallback gate.

Python's live Taiyi path is text-first and phaseful. This module only evaluates
an already available local/API snapshot as backup evidence; it does not fetch
API data and must not be wired to an independent `.搜寻节点` sender without an
explicit conflict policy for the existing Taiyi chain.
"""

from dataclasses import dataclass

from ..config import CMD_NODE_SEARCH
from ..state import REALM_SORT_INDEX, infer_realm_from_xiuwei_max
from ..timing import cd_decision


MIN_SEARCH_NODE_REALM = "化神初期"
MIN_SEARCH_NODE_SHENSHI = 100
SEARCH_NODE_CD_SEC = 12 * 3600


@dataclass(frozen=True)
class SearchNodeApiFallbackDecision:
    should_send: bool
    command: str = ""
    reason: str = ""
    realm: str = ""
    shenshi_points: int = 0
    cd_state: str = ""
    cd_reason: str = ""
    last_at: float | None = None


def _parse_int(value, default=0):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return default
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _field(snapshot, *keys):
    payload = _as_dict(snapshot)
    for key in keys:
        if key in payload:
            return payload.get(key)
    primary = _as_dict(payload.get("identity_info_primary_payload"))
    for key in keys:
        if key in primary:
            return primary.get(key)
    return None


def _snapshot_realm(snapshot):
    realm = str(_field(snapshot, "cultivation_level", "realm", "level") or "").strip()
    if realm:
        return realm
    return infer_realm_from_xiuwei_max(_field(snapshot, "xiuwei_max", "max_cultivation_points"))


def _realm_at_least(realm, min_realm):
    realm_index = REALM_SORT_INDEX.get(str(realm or "").strip())
    min_index = REALM_SORT_INDEX.get(str(min_realm or "").strip())
    if realm_index is None or min_index is None:
        return False
    return realm_index >= min_index


def _snapshot_shenshi(snapshot):
    return _parse_int(_field(snapshot, "shenshi_points", "spiritual_sense_points", "spiritual_sense"))


def decide_search_node_api_fallback(snapshot, *, now):
    """Evaluate Rust search_node gates against an already available snapshot."""
    realm = _snapshot_realm(snapshot)
    if not _realm_at_least(realm, MIN_SEARCH_NODE_REALM):
        return SearchNodeApiFallbackDecision(False, reason="realm_blocked", realm=realm)

    shenshi_points = _snapshot_shenshi(snapshot)
    if shenshi_points < MIN_SEARCH_NODE_SHENSHI:
        return SearchNodeApiFallbackDecision(
            False,
            reason="shenshi_blocked",
            realm=realm,
            shenshi_points=shenshi_points,
        )

    cd = cd_decision(_field(snapshot, "last_node_search_time"), now, SEARCH_NODE_CD_SEC)
    if cd.blocks:
        return SearchNodeApiFallbackDecision(
            False,
            reason="cd_blocked",
            realm=realm,
            shenshi_points=shenshi_points,
            cd_state=cd.state,
            cd_reason=cd.reason,
            last_at=cd.last_at,
        )

    return SearchNodeApiFallbackDecision(
        True,
        command=CMD_NODE_SEARCH,
        reason="ready",
        realm=realm,
        shenshi_points=shenshi_points,
        cd_state=cd.state,
        cd_reason=cd.reason,
        last_at=cd.last_at,
    )


__all__ = [
    "MIN_SEARCH_NODE_REALM",
    "MIN_SEARCH_NODE_SHENSHI",
    "SEARCH_NODE_CD_SEC",
    "SearchNodeApiFallbackDecision",
    "decide_search_node_api_fallback",
]
