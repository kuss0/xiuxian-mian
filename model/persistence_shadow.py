"""Shadow-only persistence scope metrics.

This module never writes the state database and never changes save, backup,
restore, or scheduling behavior.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


DEFAULT_FLUSH_INTERVAL_SEC = 60.0
DEFAULT_BACKUP_INTERVAL_SEC = 30 * 60.0
PROCESS_STARTED_AT = time.time()


def _new_interval(now: float = 0.0) -> dict[str, Any]:
    return {
        "started_at": float(now or time.time()),
        "save_count": 0,
        "no_change_count": 0,
        "full_scope_count": 0,
        "changed_save_count": 0,
        "meta_changed_total": 0,
        "meta_changed_max": 0,
        "identity_changed_total": 0,
        "identity_changed_max": 0,
        "identity_deleted_total": 0,
        "telemetry_error_count": 0,
        "meta_key_counts": Counter(),
        "backup_reason_counts": Counter(),
    }


_STATE: dict[str, Any] = {
    "db_key": "",
    "meta_snapshot": {},
    "identity_snapshots": {},
    "candidate_last_backup_at": 0.0,
    "last_flush_at": 0.0,
    "interval": _new_interval(),
}


def _enabled() -> bool:
    raw = os.environ.get("XIUXIAN_PERSISTENCE_SHADOW_ENABLED")
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    return _enabled()


def _shadow_dir() -> Path:
    configured = str(os.environ.get("XIUXIAN_PERSISTENCE_SHADOW_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "data" / "state" / "persistence_shadow"


def _float_env(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.environ.get(name) or default)
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, value)


def build_profile_snapshot(profile: dict[str, Any] | None) -> tuple[Any, ...]:
    profile = profile if isinstance(profile, dict) else {}
    return (
        profile.get("username", "") or "",
        json.dumps(profile.get("username_aliases") or [], ensure_ascii=False),
        profile.get("label", "") or "",
        profile.get("daohao", "") or "",
        profile.get("realm", "") or "",
        profile.get("spiritual_root_type", "") or "",
        profile.get("spiritual_root_attrs", "") or "",
        profile.get("replica_professions", "") or "",
        1 if profile.get("replica_gold_dps_enabled", False) else 0,
        profile.get("pet_name", "") or "",
        profile.get("pet_warm_name", "") or "",
        profile.get("pet_trial_name", "") or "",
        profile.get("sect_name", "") or "",
        float(profile.get("sect_updated_at", 0) or 0),
        profile.get("jiyin_choice", "") or "",
        profile.get("nanlong_choice", "reject") or "reject",
        profile.get("stargazer_star_choice", "赤血星") or "赤血星",
        profile.get("tianti_rank_choice", "普通") or "普通",
        int(profile.get("stargazer_total_slots", 0) or 0),
        int(profile.get("checkin_window_start_hour_utc", 2) or 2),
        int(profile.get("checkin_window_end_hour_utc", 3) or 3),
        int(profile.get("tower_window_start_hour_utc", 1) or 1),
        int(profile.get("tower_window_end_hour_utc", 2) or 2),
        1 if profile.get("enabled", True) else 0,
        int(profile.get("xiuwei_current", 0) or 0),
        int(profile.get("xiuwei_max", 0) or 0),
        profile.get("battle_power_text", "") or "",
        int(profile.get("battle_power_value", 0) or 0),
    )


def build_identity_snapshot(
    *,
    profile: dict[str, Any] | None,
    identity_state: dict[str, Any] | None,
    module_columns: tuple[str, ...],
    timer_columns: tuple[str, ...],
    runtime_columns: tuple[str, ...],
    serialize_value: Callable[[str, Any], Any],
    pending_command: Callable[[dict[str, Any]], str],
    retry_limit: int,
) -> tuple[Any, ...]:
    identity_state = identity_state if isinstance(identity_state, dict) else {}
    pending_rows = []
    for msg_id, item in (identity_state.get("pending_tasks") or {}).items():
        item = item if isinstance(item, dict) else {}
        max_retry = item.get("max_retry", retry_limit)
        pending_rows.append(
            (
                int(msg_id),
                pending_command(item),
                float(item.get("sent_at", 0) or 0),
                int(item.get("retry", 0) or 0),
                float(item.get("timeout", 0) or 0),
                int(item.get("reply_to_msg_id", 0) or 0),
                int(max_retry if max_retry is not None else retry_limit),
                str(item.get("priority", "") or ""),
                str(item.get("source_module", "") or ""),
                str(item.get("op_id", "") or ""),
                str(item.get("chain_id", "") or ""),
                str(item.get("delete_policy", "") or ""),
            )
        )
    message_rows = tuple(
        sorted(
            (int(msg_id), float(sent_at or 0), "command")
            for msg_id, sent_at in (identity_state.get("my_msg_ids") or {}).items()
        )
    )
    return (
        build_profile_snapshot(profile),
        tuple(serialize_value(column, identity_state.get(column)) for column in module_columns),
        tuple(serialize_value(column, identity_state.get(column)) for column in timer_columns),
        tuple(serialize_value(column, identity_state.get(column)) for column in runtime_columns),
        tuple(sorted(pending_rows)),
        message_rows,
    )


def _load_previous_candidate_backup_at() -> float:
    try:
        payload = json.loads((_shadow_dir() / "latest.json").read_text(encoding="utf-8"))
        return float(payload.get("candidate_last_backup_at", 0) or 0)
    except Exception:
        return 0.0


def _write_event(payload: dict[str, Any]) -> None:
    directory = _shadow_dir()
    directory.mkdir(parents=True, exist_ok=True)
    now = float(payload.get("ts_epoch") or time.time())
    path = directory / f"{datetime.fromtimestamp(now):%Y-%m-%d}.jsonl"
    safe_payload = dict(payload)
    safe_payload["schema"] = 1
    safe_payload["pid"] = os.getpid()
    safe_payload["process_started_at"] = PROCESS_STARTED_AT
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe_payload, ensure_ascii=False, sort_keys=True) + "\n")
    latest_tmp = directory / "latest.json.tmp"
    latest_tmp.write_text(json.dumps(safe_payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(latest_tmp, directory / "latest.json")


def initialize(
    *,
    db_key: str,
    meta_snapshot: dict[str, Any],
    identity_snapshots: dict[int, Any],
    initial_backup_at: float = 0.0,
    now: float | None = None,
) -> None:
    if not _enabled():
        return
    current = float(now if now is not None else time.time())
    previous_candidate = _load_previous_candidate_backup_at()
    _STATE["db_key"] = str(db_key or "")
    _STATE["meta_snapshot"] = dict(meta_snapshot or {})
    _STATE["identity_snapshots"] = dict(identity_snapshots or {})
    _STATE["candidate_last_backup_at"] = previous_candidate or float(initial_backup_at or current)
    _STATE["last_flush_at"] = current
    _STATE["interval"] = _new_interval(current)
    _write_event(
        {
            "event": "baseline",
            "ts_epoch": current,
            "identity_count": len(identity_snapshots or {}),
            "meta_key_count": len(meta_snapshot or {}),
            "candidate_last_backup_at": _STATE["candidate_last_backup_at"],
        }
    )


def capture(
    *,
    db_key: str,
    meta_snapshot: dict[str, Any],
    identity_snapshots: dict[int, Any],
) -> dict[str, Any] | None:
    if not _enabled():
        return None
    db_key = str(db_key or "")
    meta_snapshot = dict(meta_snapshot or {})
    identity_snapshots = {int(key): value for key, value in (identity_snapshots or {}).items()}
    snapshot_ready = bool(_STATE["db_key"] == db_key)
    previous_meta = _STATE["meta_snapshot"] if snapshot_ready else {}
    previous_identities = _STATE["identity_snapshots"] if snapshot_ready else {}
    changed_meta_keys = tuple(
        key for key, value in meta_snapshot.items()
        if not snapshot_ready or previous_meta.get(key) != value
    )
    changed_identity_ids = tuple(
        identity_id for identity_id in sorted(identity_snapshots)
        if not snapshot_ready or previous_identities.get(identity_id) != identity_snapshots[identity_id]
    )
    deleted_identity_ids = tuple(sorted(set(previous_identities) - set(identity_snapshots))) if snapshot_ready else ()
    return {
        "db_key": db_key,
        "meta_snapshot": meta_snapshot,
        "identity_snapshots": identity_snapshots,
        "snapshot_ready": snapshot_ready,
        "changed_meta_keys": changed_meta_keys,
        "changed_identity_ids": changed_identity_ids,
        "deleted_identity_ids": deleted_identity_ids,
    }


def _candidate_backup_reason(sample: dict[str, Any], now: float) -> str:
    changed = bool(
        sample.get("changed_meta_keys")
        or sample.get("changed_identity_ids")
        or sample.get("deleted_identity_ids")
    )
    if not changed:
        return ""
    if sample.get("deleted_identity_ids") or not sample.get("snapshot_ready"):
        return "roster_changed"
    previous_ids = set(_STATE["identity_snapshots"])
    current_ids = set(sample.get("identity_snapshots") or {})
    if previous_ids != current_ids:
        return "roster_changed"
    if {"accounts", "identity_account_map"}.intersection(sample.get("changed_meta_keys") or ()):
        return "account_structure_changed"
    interval = _float_env("XIUXIAN_LIVE_GUARD_BACKUP_INTERVAL_SEC", DEFAULT_BACKUP_INTERVAL_SEC, 60.0)
    if now - float(_STATE.get("candidate_last_backup_at") or 0) >= interval:
        return "periodic"
    return ""


def commit(sample: dict[str, Any] | None, *, now: float | None = None) -> None:
    if not sample or not _enabled():
        return
    current = float(now if now is not None else time.time())
    interval = _STATE["interval"]
    changed_meta_keys = tuple(sample.get("changed_meta_keys") or ())
    changed_identity_ids = tuple(sample.get("changed_identity_ids") or ())
    deleted_identity_ids = tuple(sample.get("deleted_identity_ids") or ())
    changed = bool(changed_meta_keys or changed_identity_ids or deleted_identity_ids)
    backup_reason = _candidate_backup_reason(sample, current)

    interval["save_count"] += 1
    interval["no_change_count"] += int(not changed)
    interval["full_scope_count"] += int(not sample.get("snapshot_ready"))
    interval["changed_save_count"] += int(changed)
    interval["meta_changed_total"] += len(changed_meta_keys)
    interval["meta_changed_max"] = max(interval["meta_changed_max"], len(changed_meta_keys))
    interval["identity_changed_total"] += len(changed_identity_ids)
    interval["identity_changed_max"] = max(interval["identity_changed_max"], len(changed_identity_ids))
    interval["identity_deleted_total"] += len(deleted_identity_ids)
    interval["meta_key_counts"].update(changed_meta_keys)
    if backup_reason:
        interval["backup_reason_counts"][backup_reason] += 1
        _STATE["candidate_last_backup_at"] = current

    _STATE["db_key"] = str(sample.get("db_key") or "")
    _STATE["meta_snapshot"] = dict(sample.get("meta_snapshot") or {})
    _STATE["identity_snapshots"] = dict(sample.get("identity_snapshots") or {})
    _maybe_flush(current)


def note_error(*, now: float | None = None) -> None:
    if not _enabled():
        return
    _STATE["interval"]["telemetry_error_count"] += 1
    _maybe_flush(float(now if now is not None else time.time()))


def _maybe_flush(now: float, *, force: bool = False) -> None:
    flush_interval = _float_env("XIUXIAN_PERSISTENCE_SHADOW_FLUSH_INTERVAL_SEC", DEFAULT_FLUSH_INTERVAL_SEC, 1.0)
    if not force and now - float(_STATE.get("last_flush_at") or 0) < flush_interval:
        return
    interval = _STATE["interval"]
    if not interval["save_count"] and not interval["telemetry_error_count"] and not force:
        return
    _write_event(
        {
            "event": "interval",
            "ts_epoch": now,
            "started_at": interval["started_at"],
            "ended_at": now,
            "save_count": interval["save_count"],
            "no_change_count": interval["no_change_count"],
            "full_scope_count": interval["full_scope_count"],
            "changed_save_count": interval["changed_save_count"],
            "meta_changed_total": interval["meta_changed_total"],
            "meta_changed_max": interval["meta_changed_max"],
            "identity_changed_total": interval["identity_changed_total"],
            "identity_changed_max": interval["identity_changed_max"],
            "identity_deleted_total": interval["identity_deleted_total"],
            "telemetry_error_count": interval["telemetry_error_count"],
            "meta_key_counts": dict(interval["meta_key_counts"]),
            "backup_reason_counts": dict(interval["backup_reason_counts"]),
            "candidate_last_backup_at": _STATE["candidate_last_backup_at"],
            "identity_count": len(_STATE["identity_snapshots"]),
            "meta_key_count": len(_STATE["meta_snapshot"]),
        }
    )
    _STATE["last_flush_at"] = now
    _STATE["interval"] = _new_interval(now)


def force_flush(*, now: float | None = None) -> None:
    if _enabled():
        _maybe_flush(float(now if now is not None else time.time()), force=True)


def safe_initialize(**kwargs) -> None:
    try:
        initialize(**kwargs)
    except Exception:
        try:
            note_error(now=kwargs.get("now"))
        except Exception:
            pass


def safe_capture(**kwargs) -> dict[str, Any] | None:
    try:
        return capture(**kwargs)
    except Exception:
        try:
            note_error()
        except Exception:
            pass
        return None


def safe_commit(sample, **kwargs) -> None:
    try:
        commit(sample, **kwargs)
    except Exception:
        try:
            note_error(now=kwargs.get("now"))
        except Exception:
            pass


def safe_note_error(**kwargs) -> None:
    try:
        note_error(**kwargs)
    except Exception:
        pass


def reset_for_tests() -> None:
    _STATE["db_key"] = ""
    _STATE["meta_snapshot"] = {}
    _STATE["identity_snapshots"] = {}
    _STATE["candidate_last_backup_at"] = 0.0
    _STATE["last_flush_at"] = 0.0
    _STATE["interval"] = _new_interval()
