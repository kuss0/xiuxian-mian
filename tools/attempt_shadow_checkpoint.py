#!/usr/bin/env python3
"""Write a read-only CommandAttempt shadow checkpoint report."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from model.config import DB_FILE, MESSAGES_DIR, TZ_LOCAL  # noqa: E402
from model.message_log_recovery import iter_message_log_entries_between  # noqa: E402


STRONG_BIND_REASONS = {
    "exact_reply_to_root",
    "exact_edit_result",
    "explicit_op_id",
    "explicit_chain_id",
}
RECENT_ANOMALY_WINDOW_SEC = 24 * 3600


def _json_dict(value):
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _counter_rows(rows, *keys):
    counts = Counter(tuple(str(row[key] or "") for key in keys) for row in rows)
    return [
        {**{key: values[index] for index, key in enumerate(keys)}, "count": count}
        for values, count in sorted(counts.items())
    ]


def _sent_log_ids(start_at, end_at, messages_dir):
    sent_ids = set()
    files_seen = set()
    for entry, _entry_at in iter_message_log_entries_between(start_at, end_at, messages_dir=messages_dir):
        if str((entry or {}).get("event_type") or "") != "sent":
            continue
        msg_id = int((entry or {}).get("message_id") or 0)
        if msg_id > 0:
            sent_ids.add(msg_id)
        raw_ts = str((entry or {}).get("ts") or "")[:10]
        if raw_ts:
            files_seen.add(raw_ts)
    return sent_ids, sorted(files_seen)


def build_checkpoint(*, db_path=DB_FILE, messages_dir=MESSAGES_DIR, now=None):
    now = float(now if now is not None else time.time())
    db_path = Path(db_path)
    messages_dir = Path(messages_dir)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        attempts = conn.execute("SELECT * FROM command_attempts ORDER BY created_at").fetchall()
        evidence = conn.execute("SELECT * FROM command_attempt_evidence ORDER BY id").fetchall()
        transitions = conn.execute("SELECT * FROM command_attempt_transitions ORDER BY id").fetchall()
    finally:
        conn.close()

    attempt_count = len(attempts)
    created_at = [float(row["created_at"] or 0) for row in attempts if float(row["created_at"] or 0) > 0]
    observation_start = min(created_at) if created_at else now
    observation_hours = max((now - observation_start) / 3600, 1 / 60)
    sent_attempts = [row for row in attempts if str(row["transport"] or "") == "sent"]
    root_ids = {int(row["root_msg_id"] or 0) for row in sent_attempts if int(row["root_msg_id"] or 0) > 0}
    sent_log_ids, log_days = _sent_log_ids(max(0, observation_start - 60), now + 60, messages_dir)
    missing_root_ids = sorted(root_ids - sent_log_ids)

    bind_reasons = Counter()
    bind_anchors = Counter()
    guessed_evidence = 0
    for row in evidence:
        payload = _json_dict(row["payload_json"])
        reason = str(payload.get("bind_reason") or "missing")
        anchor = str(payload.get("bind_anchor") or "missing")
        bind_reasons[reason] += 1
        bind_anchors[anchor] += 1
        if reason not in STRONG_BIND_REASONS:
            guessed_evidence += 1

    stale_transport_rows = [
        row
        for row in attempts
        if str(row["transport"] or "") in {"created", "queued", "sending"}
        and now - float(row["updated_at"] or 0) > 300
    ]
    stale_transport = len(stale_transport_rows)
    last_error_rows = [row for row in attempts if str(row["last_error"] or "").strip()]
    last_errors = len(last_error_rows)
    recent_cutoff = now - RECENT_ANOMALY_WINDOW_SEC
    recent_stale_transport = sum(
        1
        for row in stale_transport_rows
        if float(row["updated_at"] or 0) >= recent_cutoff
    )
    recent_last_errors = sum(
        1
        for row in last_error_rows
        if float(row["updated_at"] or 0) >= recent_cutoff
    )
    blocked = sum(1 for row in attempts if str(row["transport"] or "") == "blocked")
    send_unknown = sum(1 for row in attempts if str(row["transport"] or "") == "send_unknown")
    approx_payload_bytes = sum(
        len(str(row["intent_json"] or ""))
        + len(str(row["meta_json"] or ""))
        + len(str(row["command"] or ""))
        for row in attempts
    ) + sum(len(str(row["payload_json"] or "")) for row in evidence)
    rows_per_hour = attempt_count / observation_hours
    projected_72h_attempts = int(round(rows_per_hour * 72))
    avg_bytes_per_attempt = (approx_payload_bytes / attempt_count) if attempt_count else 0
    projected_72h_payload_bytes = int(round(projected_72h_attempts * avg_bytes_per_attempt))

    reasons = []
    if missing_root_ids:
        reasons.append(f"sent-log parity missing {len(missing_root_ids)} roots")
    if guessed_evidence:
        reasons.append(f"non-strong evidence bindings {guessed_evidence}")
    if recent_stale_transport:
        reasons.append(f"recent stale transport rows {recent_stale_transport}")
    if recent_last_errors:
        reasons.append(f"recent attempt errors {recent_last_errors}")

    return {
        "ts": datetime.fromtimestamp(now, TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "epoch": now,
        "status": "ok" if not reasons else "warn",
        "reasons": reasons,
        "policy": "read-only shadow report; no send, retry, cooldown, recovery, reducer, scheduler, or business control",
        "observation": {
            "started_at": datetime.fromtimestamp(observation_start, TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
            "hours": round(observation_hours, 3),
            "message_log_days": log_days,
        },
        "attempts": {
            "count": attempt_count,
            "transport_business": _counter_rows(attempts, "transport", "business"),
            "blocked": blocked,
            "send_unknown": send_unknown,
            "stale_transport_over_300s": stale_transport,
            "last_error_count": last_errors,
            "recent_window_sec": RECENT_ANOMALY_WINDOW_SEC,
            "recent_stale_transport_over_300s": recent_stale_transport,
            "recent_last_error_count": recent_last_errors,
        },
        "sent_log_parity": {
            "sent_attempts": len(sent_attempts),
            "rooted_attempts": len(root_ids),
            "roots_in_persisted_sent_logs": len(root_ids & sent_log_ids),
            "missing_root_count": len(missing_root_ids),
            "missing_root_ids": missing_root_ids[:100],
        },
        "binding": {
            "evidence_count": len(evidence),
            "bind_reason": dict(sorted(bind_reasons.items())),
            "bind_anchor": dict(sorted(bind_anchors.items())),
            "non_strong_written_bindings": guessed_evidence,
        },
        "capacity": {
            "transition_rows": len(transitions),
            "evidence_rows": len(evidence),
            "db_file_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "approx_attempt_and_evidence_payload_bytes": approx_payload_bytes,
            "attempt_rows_per_hour": round(rows_per_hour, 3),
            "projected_attempts_at_72h": projected_72h_attempts,
            "projected_payload_bytes_at_72h": projected_72h_payload_bytes,
            "retention_action": "none; shadow archive requires separate approval",
        },
    }


def write_checkpoint(payload, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(float(payload["epoch"]), TZ_LOCAL).strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"checkpoint-{stamp}.json"
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8")
    (output_dir / "latest.json").write_text(rendered, encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_FILE))
    parser.add_argument("--messages-dir", default=str(MESSAGES_DIR))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data" / "state" / "command_attempt_checkpoints"))
    parser.add_argument("--stdout-only", action="store_true")
    args = parser.parse_args(argv)
    payload = build_checkpoint(db_path=args.db, messages_dir=args.messages_dir)
    if not args.stdout_only:
        payload["report_path"] = str(write_checkpoint(payload, args.output_dir))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
