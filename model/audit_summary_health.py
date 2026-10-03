"""Read-only, bounded summary diagnostics; no runtime imports or replay authority."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .audit_summary_store import MAX_BYTES, validate_summary_checkpoint


HELD_GRACE_SEC = 300
PENDING_GRACE_SEC = 900


def read_summary_health(path, now, *, required=False):
    path = Path(path).absolute()
    result = {"path": str(path), "status": "not_created", "available": False,
              "policy": "read-only; absent is not proof of disabled or healthy", "alerts": []}
    try:
        # Do not create a missing database or follow an operator path symlink.
        if path.is_symlink():
            raise ValueError("summary path is a symlink")
        if not path.exists():
            if required:
                result["alerts"] = [{"code": "notification_summary_missing", "severity": "error",
                                     "message": "notification summary required but checkpoint absent; delivery state unknown"}]
                result["status"] = "error"
            return result
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", timeout=0.25)) as conn:
            conn.execute("PRAGMA query_only=ON")
            deadline = time.monotonic() + 1
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            conn.execute("BEGIN")
            row = conn.execute("SELECT length(CAST(payload AS BLOB)) FROM summary_checkpoint WHERE id=1").fetchone()
            if row is None:
                result["status"] = "uninitialized"
                if required:
                    raise ValueError("required summary not initialized")
                return result
            if type(row[0]) is not int or row[0] > MAX_BYTES:
                raise ValueError("summary checkpoint exceeds limit")
            raw = conn.execute("SELECT payload FROM summary_checkpoint WHERE id=1").fetchone()[0]
            rows, held, next_at, retired = validate_summary_checkpoint(json.loads(raw))
        times = [row["last_at"] for row in rows] + [item["at"] for item in held]
        if any(at > now + 60 for at in times):
            raise ValueError("summary clock is in the future")
        unresolved = [item for item in held if now - item["at"] >= HELD_GRACE_SEC]
        result.update(available=True, status="ok", pending_records=sum(row["count"] for row in rows),
                      held_batches=len(held), unresolved_batches=len(unresolved), retired_records=retired,
                      next_at=next_at, oldest_held_age_sec=max((max(0, now - item["at"]) for item in held), default=0))
        if unresolved:
            result["alerts"].append({"code": "notification_summary_unresolved", "severity": "warn",
                                     "message": f"notification summary: {len(unresolved)} batches await delivery review; no automatic replay"})
        if rows and now >= next_at + PENDING_GRACE_SEC:
            result["alerts"].append({"code": "notification_summary_overdue", "severity": "warn",
                                     "message": "notification summary: pending records past saved delivery window"})
        if retired:
            result["alerts"].append({"code": "notification_summary_retired", "severity": "warn",
                                     "message": f"notification summary: {retired} records retired with unconfirmed delivery; review local logs"})
        if result["alerts"]:
            result["status"] = "warn"
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, OverflowError, RecursionError):
        # Never expose raw payload, SQL errors or message content to monitoring.
        result.update(status="error", available=False, alerts=[{
            "code": "notification_summary_unreadable", "severity": "error",
            "message": "notification summary checkpoint unreadable or invalid; delivery state unknown",
        }])
    return result
