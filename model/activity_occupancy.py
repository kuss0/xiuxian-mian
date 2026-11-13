"""Read-only activity ownership for local player and channel identities."""
import time

from .config import REPLICA_ACTIVE_TTL_SEC
from .state import get_expedition_run_state, get_hunt_run_state, get_replica_run_state
from .features.hunt import ACTIVE_PHASES


def get_activity_block_reason(identity_id, *, include_hunt=True, include_xuangu=True, include_changfeng=True, include_replica=True, now=None):
    now = time.time() if now is None else float(now)
    identity_id = int(identity_id)
    if include_hunt:
        for run in get_hunt_run_state().values():
            if (isinstance(run, dict) and run.get("phase") in ACTIVE_PHASES | {"interrupted", "held"}
                    and (run.get("requires_reconciliation") or (not run.get("stop_requested") and float(run.get("expires_at") or 0) > now))
                    and any(int(member.get("identity_id") or 0) == identity_id for member in run.get("members", []))):
                return f"猎妖船进行中（{run.get('name') or '队伍'}）"
    rosters = get_expedition_run_state().get("xuangu_auto", {}).get("rosters", {})
    for roster in rosters.values() if include_xuangu else ():
        if (isinstance(roster, dict) and roster.get("phase") in {"waiting_open", "joining", "running", "finishing", "interrupted", "held", "disbanded"}
                and (roster.get("requires_reconciliation") or (not roster.get("disbanded") and float(roster.get("expires_at") or 0) > now))
                and any(int(member.get("identity_id") or 0) == identity_id for member in roster.get("my_members", []))):
            return "玄骨遗府进行中"
    if include_changfeng:
        trial = get_expedition_run_state().get("changfeng", {}).get(str(identity_id), {})
        if trial.get("phase") not in {None, "idle", "done"}:
            return "长风试炼执行中或结果待核对"
    if not include_replica:
        return ""
    records = get_replica_run_state()
    records = records.get("by_identity", records)
    record = records.get(str(identity_id), {}) if isinstance(records, dict) else {}
    states = dict(record.get("replica_states") or {})
    states["virtual_hall"] = {**record, **(states.get("virtual_hall") or {})}
    for item in states.values():
        if not isinstance(item, dict) or not item.get("participating"):
            continue
        expiry, joined = float(item.get("active_until") or 0), float(item.get("joined_at") or 0)
        if joined > 0:
            expiry = min(expiry, joined + REPLICA_ACTIVE_TTL_SEC) if expiry else joined + REPLICA_ACTIVE_TTL_SEC
        if expiry > now:
            return "副本进行中"
    return ""
