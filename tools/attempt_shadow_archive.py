#!/usr/bin/env python3
"""Export a verified, report-only CommandAttempt shadow archive.

This tool never deletes or updates the production database.  A retention age
must be supplied explicitly, and writing an archive requires ``--write``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from model.config import TZ_LOCAL  # noqa: E402


TERMINAL_TRANSPORTS = ("abandoned", "blocked", "sent")
# A transport can be complete while the shadow business projection is still
# open.  Open rows are deliberately retained because shadow mode does not own
# the reducer and must not infer business completion.
ARCHIVEABLE_BUSINESS_STATES = ("terminal_ok", "terminal_fail", "abandoned")
JSON_COLUMNS = {"intent_json", "meta_json", "payload_json"}
SENSITIVE_KEY_RE = re.compile(
    r"(?:token|cookie|session|authorization|password|passwd|secret|init[_-]?data)",
    re.IGNORECASE,
)


def _redact(value, *, depth=0):
    if depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SENSITIVE_KEY_RE.search(str(key)) else _redact(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _record(row):
    result = {}
    for key in row.keys():
        value = row[key]
        if key in JSON_COLUMNS:
            try:
                value = _redact(json.loads(str(value or "{}")))
            except (TypeError, ValueError, json.JSONDecodeError):
                value = "[INVALID_JSON]"
        result[str(key)] = value
    return result


def _connect(db_path):
    path = Path(db_path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _select_attempts(conn, before_ts):
    transport_placeholders = ",".join("?" for _ in TERMINAL_TRANSPORTS)
    business_placeholders = ",".join("?" for _ in ARCHIVEABLE_BUSINESS_STATES)
    return conn.execute(
        f"""
        SELECT * FROM command_attempts
        WHERE transport IN ({transport_placeholders})
          AND business IN ({business_placeholders})
          AND updated_at > 0
          AND updated_at < ?
        ORDER BY created_at, op_id
        """,
        (*TERMINAL_TRANSPORTS, *ARCHIVEABLE_BUSINESS_STATES, float(before_ts)),
    ).fetchall()


def _selection(db_path, before_ts):
    with _connect(db_path) as conn:
        rows = _select_attempts(conn, before_ts)
    return rows


def _timeline_counts(db_path, op_ids):
    if not op_ids:
        return {"transitions": 0, "evidence": 0}
    placeholders = ",".join("?" for _ in op_ids)
    with _connect(db_path) as conn:
        transitions = conn.execute(
            f"SELECT COUNT(*) FROM command_attempt_transitions WHERE op_id IN ({placeholders})",
            tuple(op_ids),
        ).fetchone()[0]
        evidence = conn.execute(
            f"SELECT COUNT(*) FROM command_attempt_evidence WHERE op_id IN ({placeholders})",
            tuple(op_ids),
        ).fetchone()[0]
    return {"transitions": int(transitions), "evidence": int(evidence)}


def _timestamp_bounds(rows, fields):
    values = []
    for row in rows:
        for field in fields:
            try:
                value = float(row[field] or 0)
            except (IndexError, KeyError, TypeError, ValueError, OverflowError):
                continue
            if value > 0:
                values.append(value)
    if not values:
        return {"min_ts": None, "max_ts": None, "min": "", "max": ""}
    return {
        "min_ts": min(values),
        "max_ts": max(values),
        "min": _time_text(min(values)),
        "max": _time_text(max(values)),
    }


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_jsonl(path, rows):
    with Path(path).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path):
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _time_text(value):
    if not value:
        return ""
    return datetime.fromtimestamp(float(value), TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")


def build_report(db_path, *, before_ts=None, now=None):
    now = float(now if now is not None else time.time())
    if before_ts is None:
        return {
            "schema_version": 1,
            "status": "report_only",
            "selection": {
                "eligible": False,
                "reason": "explicit retention age required",
                "before_ts": None,
            },
            "counts": {"attempts": 0, "transitions": 0, "evidence": 0},
            "generated_at": _time_text(now),
        }
    rows = _selection(db_path, before_ts)
    op_ids = [str(row["op_id"]) for row in rows]
    timeline_counts = _timeline_counts(db_path, op_ids)
    return {
        "schema_version": 1,
        "status": "report_only",
        "selection": {
            "eligible": True,
            "transport_states": list(TERMINAL_TRANSPORTS),
            "business_states": list(ARCHIVEABLE_BUSINESS_STATES),
            "before_ts": float(before_ts),
            "before": _time_text(before_ts),
            "op_ids": op_ids,
        },
        "counts": {"attempts": len(rows), **timeline_counts},
        "generated_at": _time_text(now),
    }


def export_archive(db_path, output_dir, *, before_ts, now=None):
    """Export selected timelines and verify the resulting archive.

    The database is opened read-only.  The returned manifest is only written
    after all three JSONL files have been re-read and their hashes checked.
    """

    now = float(now if now is not None else time.time())
    selected_rows = _selection(db_path, before_ts)
    op_ids = [str(row["op_id"]) for row in selected_rows]
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(now, TZ_LOCAL).strftime("%Y%m%d-%H%M%S")
    archive_dir = output_root / f"archive-{stamp}"
    suffix = 1
    while archive_dir.exists():
        archive_dir = output_root / f"archive-{stamp}-{suffix:04d}"
        suffix += 1
    archive_dir.mkdir()

    placeholders = ",".join("?" for _ in op_ids)
    with _connect(db_path) as conn:
        if op_ids:
            transitions = conn.execute(
                f"SELECT * FROM command_attempt_transitions WHERE op_id IN ({placeholders}) ORDER BY op_id, seq",
                op_ids,
            ).fetchall()
            evidence = conn.execute(
                f"SELECT * FROM command_attempt_evidence WHERE op_id IN ({placeholders}) ORDER BY op_id, seq",
                op_ids,
            ).fetchall()
        else:
            transitions = []
            evidence = []

    files = {
        "attempts": archive_dir / "attempts.jsonl",
        "transitions": archive_dir / "transitions.jsonl",
        "evidence": archive_dir / "evidence.jsonl",
    }
    _write_jsonl(files["attempts"], [{"record_type": "attempt", "record": _record(row)} for row in selected_rows])
    _write_jsonl(files["transitions"], [{"record_type": "transition", "record": _record(row)} for row in transitions])
    _write_jsonl(files["evidence"], [{"record_type": "evidence", "record": _record(row)} for row in evidence])

    manifest = {
        "schema_version": 1,
        "archive_kind": "command_attempt_shadow",
        "status": "verified",
        "generated_at": _time_text(now),
        "selection": {
            "transport_states": list(TERMINAL_TRANSPORTS),
            "business_states": list(ARCHIVEABLE_BUSINESS_STATES),
            "before_ts": float(before_ts),
            "before": _time_text(before_ts),
            "op_ids": op_ids,
        },
        "files": {},
        "database_mutation": "none",
        "deletion": "not_supported",
    }
    for name, path in files.items():
        source_rows = {
            "attempts": selected_rows,
            "transitions": transitions,
            "evidence": evidence,
        }[name]
        timestamp_fields = {
            "attempts": ("created_at", "updated_at", "sent_at", "closed_at"),
            "transitions": ("ts",),
            "evidence": ("ts",),
        }[name]
        manifest["files"][name] = {
            "path": path.name,
            "rows": len(_read_jsonl(path)),
            "sha256": _sha256(path),
            **_timestamp_bounds(source_rows, timestamp_fields),
        }
    manifest["counts"] = {
        "attempts": manifest["files"]["attempts"]["rows"],
        "transitions": manifest["files"]["transitions"]["rows"],
        "evidence": manifest["files"]["evidence"]["rows"],
    }

    manifest_path = archive_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verify_archive(archive_dir)
    return manifest_path, manifest


def verify_archive(archive_dir):
    archive_dir = Path(archive_dir)
    manifest = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))
    for name, details in manifest["files"].items():
        path = archive_dir / details["path"]
        rows = _read_jsonl(path)
        if len(rows) != int(details["rows"]):
            raise ValueError(f"archive row count mismatch: {name}")
        if _sha256(path) != details["sha256"]:
            raise ValueError(f"archive digest mismatch: {name}")
    attempt_ids = [
        str(item["record"].get("op_id") or "")
        for item in _read_jsonl(archive_dir / manifest["files"]["attempts"]["path"])
    ]
    if attempt_ids != list(manifest["selection"]["op_ids"]):
        raise ValueError("archive attempt selection mismatch")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(PROJECT_ROOT / "data/state/chaogu_state.db"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data/state/command_attempt_archives"))
    parser.add_argument("--before-epoch", type=float, default=None)
    parser.add_argument("--older-than-sec", type=float, default=None)
    parser.add_argument("--write", action="store_true", help="write a verified archive; still never deletes")
    args = parser.parse_args(argv)

    now = time.time()
    before_ts = args.before_epoch
    if before_ts is None and args.older_than_sec is not None:
        before_ts = now - max(1.0, float(args.older_than_sec))
    if args.write and before_ts is None:
        parser.error("--write requires --before-epoch or --older-than-sec")
    if before_ts is None:
        print(json.dumps(build_report(args.db, now=now), ensure_ascii=False, sort_keys=True))
        return 0
    report = build_report(args.db, before_ts=before_ts, now=now)
    if not args.write:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    manifest_path, manifest = export_archive(args.db, args.output_dir, before_ts=before_ts, now=now)
    manifest["manifest_path"] = str(manifest_path)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
