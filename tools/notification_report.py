#!/usr/bin/env python3
"""Read-only call-site inventory and transport receipt report. No runtime import."""

import argparse
import ast
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

try:
    from .bot_delivery import TOOL_SOURCES, unique_json_object
except ImportError:
    from bot_delivery import TOOL_SOURCES, unique_json_object


RECEIPT_PREFIX = "TG_NOTIFICATION_DELIVERY "


def inventory(project_root):
    calls, other_senders, errors = [], [], []
    for folder in ("model", "tools"):
        for path in sorted((Path(project_root) / folder).rglob("*.py")):
            relative = str(path.relative_to(project_root))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeError) as exc:
                errors.append({"file": relative, "error": type(exc).__name__})
                continue
            for node in ast.walk(tree):
                if (folder == "model" and isinstance(node, ast.Call)
                        and (getattr(node.func, "id", "") or getattr(node.func, "attr", "")) == "send_audit_log"):
                    priority = next((k.value for k in node.keywords if k.arg == "priority"), None)
                    calls.append({
                        "file": relative, "line": node.lineno,
                        "priority": "auto (implicit)" if priority is None else ast.unparse(priority),
                        "has_summary_kind": any(k.arg == "summary_kind" for k in node.keywords),
                    })
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and "sendMessage" in node.value:
                    other_senders.append({"file": relative, "line": node.lineno})
    return {"calls": len(calls), "priorities": dict(Counter(c["priority"] for c in calls)),
            "by_file": dict(Counter(c["file"] for c in calls).most_common()), "call_sites": calls,
            "direct_bot_api_candidates": other_senders, "parse_errors": errors}


def _receipt(line):
    line = line.strip()
    if line.startswith("{"):
        try:
            envelope = json.loads(line, object_pairs_hook=unique_json_object)
        except (ValueError, RecursionError):
            return None
        line = envelope.get("MESSAGE", "") if isinstance(envelope, dict) else ""
    if not isinstance(line, str) or not line.startswith(RECEIPT_PREFIX):
        return None
    try:
        value = json.loads(line[len(RECEIPT_PREFIX):], object_pairs_hook=unique_json_object)
        if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] not in (1, 2):
            return None
        if value.get("transport") not in {"bot", "account"} or value.get("outcome") not in {"confirmed", "unconfirmed", "unknown"}:
            return None
        if not isinstance(value.get("receipt_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["receipt_id"]):
            return None
        if not isinstance(value.get("payload_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["payload_sha256"]):
            return None
        if not isinstance(value.get("chat_id"), int) or isinstance(value["chat_id"], bool):
            return None
        if value["schema"] == 1:
            value["source"] = "runtime"
            fields = ("utf16_units", "lines", "explicit_mention_links", "elapsed_ms")
        else:
            if value.get("source") not in TOOL_SOURCES or value["transport"] != "bot":
                return None
            fields = ("payload_utf16_units", "payload_lines", "elapsed_ms", "topic_id")
        for field in fields:
            if type(value.get(field)) is not int or value[field] < 0:
                return None
        if type(value.get("at")) not in (int, float) or not math.isfinite(value["at"]):
            return None
        datetime.fromtimestamp(value["at"], ZoneInfo("Asia/Shanghai"))
        common = ("schema", "receipt_id", "at", "chat_id", "source", "transport", "outcome", "payload_sha256")
        return {key: value[key] for key in (*common, *fields)}
    except (ValueError, TypeError, OSError, OverflowError, RecursionError):
        return None


def _percentiles(values):
    values = sorted(values)
    return {f"p{p}": values[max(0, math.ceil(len(values) * p / 100) - 1)] if values else None
            for p in (50, 95, 99)}


def delivery_report(lines):
    receipts = {}
    conflicts = set()
    ignored = 0
    for line in lines:
        row = _receipt(line)
        if row is None:
            ignored += 1
        else:
            if row["receipt_id"] in receipts and receipts[row["receipt_id"]] != row:
                conflicts.add(row["receipt_id"])
            receipts.setdefault(row["receipt_id"], row)
    rows = [row for key, row in receipts.items() if key not in conflicts]
    confirmed = [row for row in rows if row["outcome"] == "confirmed"]
    visible = [row for row in confirmed if row["schema"] == 1]
    payload = [row for row in confirmed if row["schema"] == 2]
    hourly = Counter()
    for row in confirmed:
        hour = datetime.fromtimestamp(row["at"], ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:00 UTC+8")
        hourly[(str(row["chat_id"]), hour)] += 1
    payloads = Counter((row["chat_id"], row.get("topic_id"), row["payload_sha256"]) for row in confirmed)
    sources = sorted({row["source"] for row in rows})
    return {
        "available": bool(rows),
        "coverage": "supplied runtime and instrumented tool receipts only; historical uninstrumented sends and other senders excluded",
        "unit": "transport attempts, not business events; unknown outcomes may have reached Telegram",
        "attempts": len(rows), "outcomes": dict(Counter(row["outcome"] for row in rows)),
        "excluded_conflicting_receipts": len(conflicts),
        "by_source": {source: {
            "attempts": sum(row["source"] == source for row in rows),
            "outcomes": dict(Counter(row["outcome"] for row in rows if row["source"] == source)),
        } for source in sources},
        "ignored_lines": ignored,
        "first_at": min((row["at"] for row in rows), default=None),
        "last_at": max((row["at"] for row in rows), default=None),
        "confirmed_by_hour": [{"chat_id": chat, "hour": hour, "count": count}
                              for (chat, hour), count in sorted(hourly.items())],
        "repeated_confirmed_payloads": sum(count - 1 for count in payloads.values()),
        "size_and_mention_coverage": "visible size/mention links: runtime schema 1 only; payload size: tool schema 2, including HTML markup",
        "confirmed_visible_metrics_count": len(visible),
        "confirmed_payload_metrics_count": len(payload),
        "confirmed_explicit_mention_links": sum(row["explicit_mention_links"] for row in visible),
        "confirmed_visible_utf16_units": _percentiles([row["utf16_units"] for row in visible]),
        "confirmed_payload_utf16_units": _percentiles([row["payload_utf16_units"] for row in payload]),
        "confirmed_latency_ms": _percentiles([row["elapsed_ms"] for row in confirmed]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--journal", help="Saved journal JSONL or plain receipts; '-' reads stdin")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"inventory": inventory(args.project_root), "delivery": delivery_report(())}
    if args.journal == "-":
        report["delivery"] = delivery_report(sys.stdin)
    elif args.journal:
        with Path(args.journal).open(encoding="utf-8") as source:
            report["delivery"] = delivery_report(source)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
