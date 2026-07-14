#!/usr/bin/env python3
"""Read-only sent -> first reply -> final edit latency report."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path


TZ_LOCAL = timezone(timedelta(hours=8))
DEFAULT_MODULES = {
    "wild_training": "野外历练",
    "duel": "斗法",
    "mulan": "慕兰烽烟",
}
TS_FORMAT = "%Y-%m-%d %H:%M:%S UTC+8"


def _parse_ts(value):
    try:
        return datetime.strptime(str(value or ""), TS_FORMAT).replace(tzinfo=TZ_LOCAL)
    except (TypeError, ValueError):
        return None


def _iter_day_paths(messages_dir, start, end):
    day = start.date()
    while day <= end.date():
        path = Path(messages_dir) / f"{day.isoformat()}.log"
        if path.exists():
            yield path
        day += timedelta(days=1)


def _read_rows(messages_dir, start, end):
    rows = []
    for path in _iter_day_paths(messages_dir, start, end):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                ts = _parse_ts(row.get("ts"))
                if ts is None or ts < start or ts > end:
                    continue
                row["_ts"] = ts
                rows.append(row)
    return rows


def _percentile(values, ratio):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(len(ordered) * float(ratio)) - 1)
    return round(ordered[index], 3)


def _latency_stats(values):
    return {
        "p50_sec": _percentile(values, 0.50),
        "p95_sec": _percentile(values, 0.95),
        "p99_sec": _percentile(values, 0.99),
        "max_sec": round(max(values), 3) if values else None,
    }


def build_latency_report(messages_dir, *, since_hours=24, now=None, modules=None, missing_sample_limit=20):
    now = now or datetime.now(TZ_LOCAL)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ_LOCAL)
    else:
        now = now.astimezone(TZ_LOCAL)
    start = now - timedelta(hours=max(1.0, float(since_hours or 24)))
    module_map = dict(modules or DEFAULT_MODULES)
    source_to_key = {source: key for key, source in module_map.items()}
    rows = _read_rows(messages_dir, start, now)

    sent = {}
    for row in rows:
        source_module = str(row.get("source_module") or "")
        if row.get("event_type") != "sent" or source_module not in source_to_key:
            continue
        key = (int(row.get("chat_id") or 0), int(row.get("message_id") or 0))
        if key[0] and key[1]:
            sent[key] = row

    replies = {key: [] for key in sent}
    for row in rows:
        if row.get("event_type") not in {"message", "edit"}:
            continue
        reply_to = int(row.get("reply_to_msg_id") or 0)
        key = (int(row.get("chat_id") or 0), reply_to)
        if reply_to > 0 and key in replies:
            replies[key].append(row)

    payload = {
        "policy": "read-only direct reply/edit latency; no send or state mutation",
        "start": start.strftime(TS_FORMAT),
        "end": now.strftime(TS_FORMAT),
        "modules": {},
    }
    for report_key, source_module in module_map.items():
        first_latencies = []
        final_latencies = []
        missing = []
        total = 0
        for root_key, sent_row in sent.items():
            if str(sent_row.get("source_module") or "") != source_module:
                continue
            total += 1
            evidence = sorted(replies.get(root_key) or (), key=lambda item: item["_ts"])
            if not evidence:
                if len(missing) < max(0, int(missing_sample_limit or 0)):
                    missing.append({
                        "ts": sent_row.get("ts") or "",
                        "identity_id": int(sent_row.get("sender_id") or 0),
                        "message_id": int(sent_row.get("message_id") or 0),
                        "command": str(sent_row.get("text") or "")[:120],
                    })
                continue
            first_latencies.append(max(0.0, (evidence[0]["_ts"] - sent_row["_ts"]).total_seconds()))
            final_latencies.append(max(0.0, (evidence[-1]["_ts"] - sent_row["_ts"]).total_seconds()))
        payload["modules"][report_key] = {
            "source_module": source_module,
            "sent": total,
            "replied": len(first_latencies),
            "missing": total - len(first_latencies),
            "first_reply": _latency_stats(first_latencies),
            "final_event": _latency_stats(final_latencies),
            "missing_samples": missing,
        }
    return payload


def _format_stats(stats):
    def value(key):
        raw = stats.get(key)
        return "-" if raw is None else f"{raw:.3f}s"

    return f"P50={value('p50_sec')} P95={value('p95_sec')} P99={value('p99_sec')} max={value('max_sec')}"


def format_report(payload):
    lines = [
        f"module latency report: {payload['start']} -> {payload['end']}",
        f"policy: {payload['policy']}",
    ]
    for key, row in payload.get("modules", {}).items():
        lines.append(
            f"- {key}: sent={row['sent']} replied={row['replied']} missing={row['missing']} "
            f"first[{_format_stats(row['first_reply'])}] final[{_format_stats(row['final_event'])}]"
        )
        for sample in row.get("missing_samples") or ():
            lines.append(
                f"  missing: {sample['ts']} identity={sample['identity_id']} "
                f"msg={sample['message_id']} command={sample['command']}"
            )
    return "\n".join(lines)


def _parse_module_args(values):
    if not values:
        return dict(DEFAULT_MODULES)
    result = {}
    for value in values:
        key, separator, source = str(value or "").partition("=")
        if not separator or not key.strip() or not source.strip():
            raise ValueError(f"invalid module mapping: {value!r}")
        result[key.strip()] = source.strip()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages-dir", default="data/messages")
    parser.add_argument("--since-hours", type=float, default=24.0)
    parser.add_argument("--module", action="append", default=[], help="report_key=source_module")
    parser.add_argument("--missing-sample-limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        modules = _parse_module_args(args.module)
    except ValueError as exc:
        parser.error(str(exc))
    payload = build_latency_report(
        args.messages_dir,
        since_hours=args.since_hours,
        modules=modules,
        missing_sample_limit=args.missing_sample_limit,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else format_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
