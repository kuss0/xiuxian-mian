#!/usr/bin/env python3
"""Summarize persistence would-write shadow metrics without runtime access."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = PROJECT_ROOT / "data" / "state" / "persistence_shadow"


def _iter_rows(directory: Path):
    for path in sorted(directory.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row


def build_report(
    directory: Path = DEFAULT_DIR,
    *,
    since_hours: float = 24.0,
    now: float | None = None,
) -> dict[str, Any]:
    current = float(now if now is not None else time.time())
    cutoff = current - max(0.1, float(since_hours)) * 3600
    rows = [row for row in _iter_rows(Path(directory)) if float(row.get("ts_epoch", 0) or 0) >= cutoff]
    baselines = [row for row in rows if row.get("event") == "baseline"]
    intervals = [row for row in rows if row.get("event") == "interval"]
    starts = [float(row.get("ts_epoch", 0) or 0) for row in baselines]
    starts.extend(float(row.get("started_at", 0) or 0) for row in intervals)
    ends = [float(row.get("ended_at", 0) or 0) for row in intervals]
    observation_sec = max(
        0.0,
        max(ends, default=0.0) - min((value for value in starts if value > 0), default=current),
    )
    backup_reasons = Counter()
    meta_keys = Counter()
    for row in intervals:
        backup_reasons.update(row.get("backup_reason_counts") or {})
        meta_keys.update(row.get("meta_key_counts") or {})
    sessions = {
        (int(row.get("pid") or 0), float(row.get("process_started_at", 0) or 0))
        for row in rows
    }
    totals = {
        key: sum(int(row.get(key, 0) or 0) for row in intervals)
        for key in (
            "save_count",
            "no_change_count",
            "full_scope_count",
            "changed_save_count",
            "meta_changed_total",
            "identity_changed_total",
            "identity_deleted_total",
            "telemetry_error_count",
        )
    }
    totals["meta_changed_max"] = max(
        (int(row.get("meta_changed_max", 0) or 0) for row in intervals),
        default=0,
    )
    totals["identity_changed_max"] = max(
        (int(row.get("identity_changed_max", 0) or 0) for row in intervals),
        default=0,
    )
    observation_hours = observation_sec / 3600.0
    return {
        "since_hours": float(since_hours),
        "observation_hours": round(observation_hours, 3),
        "process_sessions": len(sessions),
        "baseline_count": len(baselines),
        "interval_count": len(intervals),
        "totals": totals,
        "backup_reason_counts": dict(sorted(backup_reasons.items())),
        "meta_key_counts": dict(meta_keys.most_common()),
        "p1_review_ready": bool(
            observation_hours >= 12
            and len(sessions) >= 2
            and totals["telemetry_error_count"] == 0
        ),
        "p2_review_ready": bool(
            observation_hours >= 24
            and len(sessions) >= 2
            and totals["telemetry_error_count"] == 0
        ),
        "policy": "read-only shadow metrics; readiness is not deployment approval",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only persistence shadow report.")
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    parser.add_argument("--since-hours", type=float, default=24.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(Path(args.dir), since_hours=args.since_hours)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        totals = report["totals"]
        print(
            f"persistence shadow: {report['observation_hours']:.3f}h "
            f"sessions={report['process_sessions']} saves={totals['save_count']} "
            f"no-change={totals['no_change_count']} "
            f"identity-changed={totals['identity_changed_total']} "
            f"errors={totals['telemetry_error_count']}"
        )
        print(
            f"review gates: P1={'ready' if report['p1_review_ready'] else 'collecting'} "
            f"P2={'ready' if report['p2_review_ready'] else 'collecting'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
