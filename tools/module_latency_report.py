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


def _bot_sender_ids(rows):
    sender_ids = set()
    for row in rows:
        username = str(row.get("sender_username") or "").strip().lower()
        if row.get("sender_is_bot") is True or username.endswith("_bot"):
            try:
                sender_id = int(row.get("sender_id") or 0)
            except (TypeError, ValueError):
                sender_id = 0
            if sender_id > 0:
                sender_ids.add(sender_id)
    return sender_ids


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


def _server_event_time(row):
    value = row.get("server_event_at")
    if type(value) not in (int, float):
        return None
    if not 0 < value <= row["_ts"].timestamp():
        return None
    return float(value)


def _server_clock_sample(root_times, evidence):
    missing = {
        "server_first_reply_sec": None,
        "server_final_event_sec": None,
        "first_observation_lag_sec": None,
    }
    if len(root_times) != 1 or None in root_times:
        return missing
    sent_at = next(iter(root_times))
    timed = [(_server_event_time(row), row["_ts"].timestamp()) for row in evidence]
    # Incomplete clocks cannot establish the final server event.
    if not timed or any(server is None or server < sent_at for server, _ in timed):
        return missing
    timed.sort()
    return {
        "server_first_reply_sec": round(timed[0][0] - sent_at, 3),
        "server_final_event_sec": round(timed[-1][0] - sent_at, 3),
        "first_observation_lag_sec": round(timed[0][1] - timed[0][0], 3),
    }


def build_latency_report(
    messages_dir,
    *,
    since_hours=24,
    now=None,
    modules=None,
    missing_sample_limit=20,
    slow_sample_limit=10,
):
    now = now or datetime.now(TZ_LOCAL)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ_LOCAL)
    else:
        now = now.astimezone(TZ_LOCAL)
    start = now - timedelta(hours=max(1.0, float(since_hours or 24)))
    module_map = dict(modules or DEFAULT_MODULES)
    source_to_key = {source: key for key, source in module_map.items()}
    rows = _read_rows(messages_dir, start, now)
    bot_sender_ids = _bot_sender_ids(rows)

    sent = {}
    for row in rows:
        source_module = str(row.get("source_module") or "")
        if row.get("event_type") != "sent" or source_module not in source_to_key:
            continue
        key = (int(row.get("chat_id") or 0), int(row.get("message_id") or 0))
        if key[0] and key[1]:
            sent[key] = row

    server_roots = {key: set() for key in sent}
    for row in rows:
        if row.get("event_type") != "message" or row.get("forwarded"):
            continue
        key = (row.get("chat_id"), row.get("message_id"))
        if any(type(value) is not int or value == 0 for value in (*key, row.get("sender_id"))):
            continue
        root = sent.get(key)
        if (root is not None and type(root.get("sender_id")) is int
                and row.get("sender_id") == root.get("sender_id")
                and isinstance(root.get("text"), str) and root["text"]
                and row.get("text") == root.get("text")):
            server_roots[key].add(_server_event_time(row))

    replies = {key: [] for key in sent}
    for row in rows:
        if row.get("event_type") not in {"message", "edit"}:
            continue
        if int(row.get("sender_id") or 0) not in bot_sender_ids:
            continue
        reply_to = int(row.get("reply_to_msg_id") or 0)
        key = (int(row.get("chat_id") or 0), reply_to)
        if reply_to > 0 and key in replies:
            replies[key].append(row)

    payload = {
        "policy": (
            "read-only direct game-bot reply/edit latency; no send or state mutation; "
            "first/final use local log observation time; server clock requires a matching command echo; "
            "missing is not a business failure count (ancestor edits are not direct child replies)"
        ),
        "start": start.strftime(TS_FORMAT),
        "end": now.strftime(TS_FORMAT),
        "modules": {},
    }
    for report_key, source_module in module_map.items():
        first_latencies = []
        final_latencies = []
        server_first_latencies = []
        server_final_latencies = []
        observation_lags = []
        slow_samples = []
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
                        "chat_id": root_key[0],
                        "message_id": int(sent_row.get("message_id") or 0),
                        "command": str(sent_row.get("text") or "")[:120],
                    })
                continue
            first_latency = max(0.0, (evidence[0]["_ts"] - sent_row["_ts"]).total_seconds())
            final_latency = max(0.0, (evidence[-1]["_ts"] - sent_row["_ts"]).total_seconds())
            first_latencies.append(first_latency)
            final_latencies.append(final_latency)
            server_sample = _server_clock_sample(server_roots[root_key], evidence)
            if server_sample["server_first_reply_sec"] is not None:
                server_first_latencies.append(server_sample["server_first_reply_sec"])
                server_final_latencies.append(server_sample["server_final_event_sec"])
                observation_lags.append(server_sample["first_observation_lag_sec"])
            slow_samples.append({
                "ts": sent_row.get("ts") or "",
                "identity_id": int(sent_row.get("sender_id") or 0),
                "chat_id": root_key[0],
                "message_id": int(sent_row.get("message_id") or 0),
                "command": str(sent_row.get("text") or "")[:120],
                "first_reply_sec": round(first_latency, 3),
                "final_event_sec": round(final_latency, 3),
                "final_event_type": str(evidence[-1].get("event_type") or ""),
                "final_message_id": int(evidence[-1].get("message_id") or 0),
                **server_sample,
            })
        slow_samples.sort(
            key=lambda item: (item["final_event_sec"], item["first_reply_sec"]),
            reverse=True,
        )
        payload["modules"][report_key] = {
            "source_module": source_module,
            "sent": total,
            "replied": len(first_latencies),
            "missing": total - len(first_latencies),
            "first_reply": _latency_stats(first_latencies),
            "final_event": _latency_stats(final_latencies),
            "server_clock_samples": len(server_first_latencies),
            "server_first_reply": _latency_stats(server_first_latencies),
            "server_final_event": _latency_stats(server_final_latencies),
            "first_observation_lag": _latency_stats(observation_lags),
            "slow_samples": slow_samples[:max(0, int(slow_sample_limit or 0))],
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
        if "server_clock_samples" in row:
            lines.append(
                f"  server_samples={row['server_clock_samples']} "
                f"server_first[{_format_stats(row['server_first_reply'])}] "
                f"server_final[{_format_stats(row['server_final_event'])}] "
                f"observation_lag[{_format_stats(row['first_observation_lag'])}]"
            )
        for sample in row.get("missing_samples") or ():
            lines.append(
                f"  missing: {sample['ts']} identity={sample['identity_id']} "
                f"chat={sample.get('chat_id', 0)} "
                f"msg={sample['message_id']} command={sample['command']}"
            )
        for sample in row.get("slow_samples") or ():
            lines.append(
                f"  slow: {sample['ts']} identity={sample['identity_id']} msg={sample['message_id']} "
                f"chat={sample.get('chat_id', 0)} "
                f"first={sample['first_reply_sec']:.3f}s final={sample['final_event_sec']:.3f}s "
                f"final_event={sample['final_event_type']}:{sample['final_message_id']} command={sample['command']}"
            )
            if sample.get("server_first_reply_sec") is not None:
                lines.append(
                    f"    server_first={sample['server_first_reply_sec']:.3f}s "
                    f"server_final={sample['server_final_event_sec']:.3f}s "
                    f"observation_lag={sample['first_observation_lag_sec']:.3f}s"
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
    parser.add_argument("--slow-sample-limit", type=int, default=10)
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
        slow_sample_limit=args.slow_sample_limit,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else format_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
