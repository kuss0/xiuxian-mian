#!/usr/bin/env python3
"""Read-only fishing evidence snapshot, not an execution or retry decision."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.features import fishing_dwelling_journal as cast_journal
from model.features import fishing_dwelling_supply as supply_journal


TZ = ZoneInfo("Asia/Shanghai")
SKIPS = {
    "洞府原生钓鱼：未持有鱼竿，今日跳过": "no_rod",
    "洞府原生钓鱼：无可用侍妾，今日跳过": "no_companion",
    "洞府原生钓鱼：今日次数已用尽，等待次日（fishing_daily_limit_reached）": "quota_exhausted",
    "未开放灵溪垂钓，今日跳过": "legacy_unavailable_skip",
}
RUNTIME_FIELDS = (
    "fishing_daily_day", "fishing_daily_count", "fishing_daily_limit",
    "fishing_daily_catch_summary_json", "fishing_last_result",
    "fishing_native_operation", "fishing_native_supply",
    "fishing_operation", "fishing_result_pending",
    "concubine_voyage_status", "concubine_voyage_return_at",
)


def _object(value):
    if value in (None, ""):
        return {}
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("expected object")
    return parsed


def _count(value):
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ValueError("invalid count")
    return value


def _clock(value):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid clock")
    datetime.fromtimestamp(value, TZ)
    return value


def _items(value):
    if not isinstance(value, dict) or len(value) > 1000:
        raise ValueError("invalid items")
    for name, count in value.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("invalid item name")
        _count(count)
    return {name: count for name, count in value.items() if count}


def _flag(value):
    if value is None:
        return False
    if type(value) not in (bool, int) or value not in (0, 1):
        raise ValueError("invalid flag")
    return bool(value)


def _entry_configured(config):
    urls = config.get("cave_public_entry_urls") or []
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, list):
        raise ValueError("invalid entry selection")
    return any(isinstance(url, str) and url.strip() for url in [*urls, config.get("cave_public_entry_url")])


def read_snapshot(db_path):
    """A single WAL-aware read transaction; never create or migrate a DB."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        meta = {row["key"]: _object(row["value"]) for row in conn.execute(
            "SELECT key,value FROM meta WHERE key IN ('miniapp_auto_config','identity_account_map')")}
        fields = ",".join("r." + name for name in RUNTIME_FIELDS)
        rows = [dict(row) for row in conn.execute(f"""
            SELECT i.send_as_id,i.username,i.label,i.enabled,
                   m.fishing_enabled,t.next_fishing_time,{fields}
            FROM identities i
            LEFT JOIN identity_module_state m USING(send_as_id)
            LEFT JOIN identity_timers t USING(send_as_id)
            LEFT JOIN identity_runtime_state r USING(send_as_id)
            ORDER BY i.send_as_id
        """)]
        return rows, meta


def _receipt(value, validator, identity_id, account_id):
    try:
        record = _object(value)
        if not record:
            return "none"
        validator(record)
        _clock(record["created_at"])
        if record["identity_id"] != identity_id or record["account_id"] != account_id:
            return "owner_mismatch"
        return record["phase"]
    except (ValueError, TypeError, OverflowError, RecursionError, KeyError, OSError):
        # Never include raw receipts or exception text in the report.
        return "invalid"


def summarize_identity(row, *, config, accounts, now):
    day = datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")
    identity_id = row["send_as_id"]
    configured_ids = config.get("cave_public_fishing_identity_ids") or []
    if not isinstance(configured_ids, list):
        raise ValueError("invalid fishing selection")
    public_selected = identity_id in {
        int(value) for value in configured_ids
        if type(value) in (int, str) and str(value).isdigit()
    }
    public_on = public_selected and _flag(config.get("cave_public_fishing_enabled")) and _entry_configured(config)
    standalone_on = _flag(row.get("fishing_enabled"))
    selected = standalone_on or public_on
    result = {
        "identity_id": identity_id,
        "label": row.get("username") or row.get("label") or str(identity_id),
        "selected": selected, "public_selected": public_selected,
        "standalone_enabled": standalone_on,
        "group_identity_enabled": row.get("enabled") == 1,
        "evidence": "none", "warnings": [], "summary": None, "quota": None,
    }
    warnings = result["warnings"]
    try:
        return_at = _clock(row.get("concubine_voyage_return_at", 0) or 0)
        result["voyage_return_time"] = (datetime.fromtimestamp(return_at, TZ).isoformat()
                                        if row.get("concubine_voyage_status") == "sailing" and return_at else None)
    except (ValueError, TypeError, OverflowError, OSError):
        result["voyage_return_time"] = None
        warnings.append("voyage_return_time:invalid")
    for name, validator in (("fishing_native_operation", cast_journal.validate),
                            ("fishing_native_supply", supply_journal.validate)):
        phase = _receipt(row.get(name), validator, identity_id, accounts.get(str(identity_id)))
        result[name] = phase
        if phase not in {"none", "accounted"}:
            warnings.append(name + ":" + phase)
    for name in ("fishing_operation", "fishing_result_pending"):
        try:
            if _object(row.get(name)):
                warnings.append(name + ":unresolved")
        except (ValueError, TypeError, RecursionError):
            warnings.append(name + ":invalid")

    try:
        due = _clock(row.get("next_fishing_time"))
        result["next_time"] = datetime.fromtimestamp(due, TZ).isoformat() if due else None
        result["schedule"] = "disabled" if not selected else "scheduled" if due > now else "due"
    except (ValueError, TypeError, OverflowError, OSError):
        result.update(next_time=None, schedule="invalid")
        warnings.append("next_fishing_time:invalid")

    if row.get("fishing_daily_day") == day:
        try:
            used, limit = _count(row.get("fishing_daily_count")), _count(row.get("fishing_daily_limit"))
            if used > limit:
                raise ValueError("invalid quota")
            result["quota"] = {"used": used, "limit": limit}
        except (ValueError, TypeError):
            warnings.append("daily_quota:invalid")
        result["evidence"] = SKIPS.get(row.get("fishing_last_result"), "none")
    try:
        summary = _object(row.get("fishing_daily_catch_summary_json"))
        if summary.get("day") == day:
            result["summary"] = {"rods": _count(summary.get("rods")),
                                 "fish": _items(summary.get("fish")),
                                 "rewards": _items(summary.get("rewards"))}
            if result["summary"]["rods"]:
                result["evidence"] = "recorded_settlements"
            if result["quota"] and result["summary"]["rods"] > result["quota"]["used"]:
                warnings.append("summary_rods_exceed_quota_used")
    except (ValueError, TypeError, RecursionError):
        warnings.append("daily_summary:invalid")
    # A settled receipt is evidence for one rod only, not a full-day completion.
    if result["fishing_native_operation"] == "accounted":
        record = _object(row.get("fishing_native_operation"))
        result["latest_native_cast_day"] = datetime.fromtimestamp(record["created_at"], TZ).strftime("%Y-%m-%d")
    return result


def build_report(db_path, *, now=None):
    now = _clock(time.time() if now is None else now)
    rows, meta = read_snapshot(db_path)
    identities = [summarize_identity(row, config=meta.get("miniapp_auto_config", {}),
                                    accounts=meta.get("identity_account_map", {}), now=now) for row in rows]
    return {
        "generated_at": datetime.fromtimestamp(now, TZ).isoformat(),
        "read_only": True, "selected": sum(row["selected"] for row in identities),
        "evidence_counts": dict(Counter(row["evidence"] for row in identities if row["selected"])),
        "warning_identities": sum(bool(row["warnings"]) for row in identities),
        "identities": identities,
        "scope": "Saved local evidence only; selection and future timers do not prove execution. No game requests.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("XIUXIAN_DB_FILE") or ROOT / "data/state/chaogu_state.db"))
    parser.add_argument("--now", type=float, help="Snapshot interpretation time as a Unix timestamp")
    args = parser.parse_args()
    try:
        report = build_report(args.db, now=args.now)
    except (sqlite3.Error, ValueError, TypeError, OverflowError, OSError, RecursionError):
        print(json.dumps({"read_only": True, "error": "snapshot_unavailable_or_invalid"}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
