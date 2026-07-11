#!/usr/bin/env python3
"""Offline CommandAttempt binding replay for message-log JSONL files."""

import argparse
import json
from collections import Counter
from datetime import datetime

from model.command_attempt import classify_evidence_binding


def _event_at(payload):
    raw = str((payload or {}).get("ts") or "").replace(" UTC+8", "")
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").timestamp()
    except (TypeError, ValueError):
        return float((payload or {}).get("ts_epoch") or 0)


def replay_entries(entries):
    rows = []
    counts = Counter()
    for payload in entries:
        if not isinstance(payload, dict):
            continue
        result = classify_evidence_binding(
            event_kind=str(payload.get("event_type") or "message"),
            msg_id=int(payload.get("message_id") or payload.get("msg_id") or 0),
            reply_to_msg_id=int(payload.get("reply_to_msg_id") or 0),
            identity_id=int(payload.get("send_as_id") or payload.get("identity_id") or 0),
            family=str(payload.get("family") or ""),
            op_id=str(payload.get("op_id") or ""),
            chain_id=str(payload.get("chain_id") or ""),
            event_at=_event_at(payload),
        )
        counts[result.status.value] += 1
        rows.append(
            {
                "message_id": int(payload.get("message_id") or payload.get("msg_id") or 0),
                "status": result.status.value,
                "matched_op_id": result.matched_op_id,
                "candidate_op_ids": list(result.candidate_op_ids),
                "reason": result.reason,
                "anchor": result.anchor,
            }
        )
    return {"counts": dict(sorted(counts.items())), "rows": rows}


def replay_file(path):
    entries = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return replay_entries(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="message-log JSONL file")
    parser.add_argument("--details", action="store_true", help="include per-message decisions")
    args = parser.parse_args()
    report = replay_file(args.path)
    if not args.details:
        report.pop("rows", None)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
