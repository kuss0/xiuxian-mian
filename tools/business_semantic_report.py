#!/usr/bin/env python3
"""Read-only semantic evidence for shared-send checkpoint debt.

This report consumes persisted message logs and sanitized MiniApp captures only.
It does not import the runtime scheduler or call Telegram/HTTP APIs.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MESSAGES_DIR = PROJECT_ROOT / "data" / "messages"
CAPTURE_DIR = PROJECT_ROOT / "data" / "state" / "miniapp_capture"
WORLD_BOSS_CAPTURE_DIR = PROJECT_ROOT / "data" / "messages" / "miniapp-captures"
TZ_LOCAL = timezone(timedelta(hours=8))

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")
_PANEL_FAITH = re.compile(r"信仰:\s*(\d+)\s*/\s*(\d+)")
_PANEL_STABILITY = re.compile(r"稳定:\s*(\d+)\s*/\s*(\d+)")
_MANIFEST_DELTA = re.compile(r"\(信仰\s*([+-]?\d+),\s*稳定\s*([+-]?\d+),\s*人口\s*([+-]?\d+)")
_ABSOLUTE_FAITH = re.compile(r"信仰(?:提升至|降至|崩塌\s*)(\d+)")
_FAITH_LOSS = re.compile(r"(?:信仰(?:崩塌|动摇)|信仰)\s*([+-]\d+)\s*点")
_STOCK_LOSS = re.compile(r"库存香火损失\s*([+-]?\d+)\s*点")
_HARVEST = re.compile(r"(?:供奉的|待收)\s*([\d,.]+)\s*点?香火")
_REFINE = re.compile(r"燃烧了\s*([\d,.]+)\s*点香火")
_ACTION_RE = re.compile(
    r"^\.(?P<action>小世界|显灵|神迹\s+(?:布道|赈灾)|收割香火|神识淬炼(?:\s+\S+)?)"
)


def _number(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return 0


def _parse_ts(value: Any) -> float:
    text = str(value or "").strip()
    if text.endswith(" UTC+8"):
        text = text[:-6]
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=TZ_LOCAL).timestamp()
        except ValueError:
            continue
    return 0.0


def _format_ts(value: float) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value, TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S")


def _iter_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _message_paths(messages_dir: Path, day: str, days: int) -> list[Path]:
    end = datetime.strptime(day, "%Y-%m-%d").date()
    return [messages_dir / f"{end - timedelta(days=offset):%Y-%m-%d}.log" for offset in range(max(1, days))]


def _parse_panel(text: str) -> dict[str, int] | None:
    faith = _PANEL_FAITH.search(text or "")
    stability = _PANEL_STABILITY.search(text or "")
    if not faith:
        return None
    return {
        "faith": _number(faith.group(1)),
        "faith_max": _number(faith.group(2)),
        "stability": _number(stability.group(1)) if stability else 0,
        "stability_max": _number(stability.group(2)) if stability else 0,
    }


def _event_explanations(
    rows: list[dict[str, Any]],
    roots: dict[int, dict[str, Any]],
    identity_id: int,
    start: float,
    end: float,
    previous_faith: int,
    current_faith: int,
) -> dict[str, Any]:
    explanations: list[str] = []
    expected_faith = previous_faith
    has_evidence = False
    identity_aliases = {
        str(row.get("sender_username") or "").lstrip("@").lower()
        for row in rows
        if row.get("event_type") in {"sent", "message"}
        and int(row.get("sender_id") or 0) == identity_id
        and row.get("sender_username")
    }
    for row in rows:
        ts = _parse_ts(row.get("ts"))
        if not start < ts <= end:
            continue
        text = str(row.get("text") or "")
        event_type = str(row.get("event_type") or "")
        if event_type == "sent":
            continue

        try:
            reply_to = int(row.get("reply_to_msg_id") or 0)
        except (TypeError, ValueError, OverflowError):
            reply_to = 0
        root = roots.get(reply_to)
        if row.get("sender_is_bot") and root is not None and int(root.get("sender_id") or 0) == identity_id:
            manifest = _MANIFEST_DELTA.search(text)
            if manifest:
                delta = _number(manifest.group(1))
                expected_faith = max(0, min(100, expected_faith + delta))
                has_evidence = True
                explanations.append("同身份显灵回复")
            absolute = _ABSOLUTE_FAITH.search(text)
            if absolute:
                target = _number(absolute.group(1))
                expected_faith = max(0, min(100, target))
                has_evidence = True
                explanations.append("同身份神迹回复")

        # Broadcast losses are not replies to our query, but are still direct
        # business evidence when the bot names the same identity.
        if event_type in {"message", "edit"} and row.get("sender_is_bot"):
            lowered = text.lower()
            if identity_aliases and any(f"@{alias}" in lowered for alias in identity_aliases):
                loss = _FAITH_LOSS.search(text)
                if loss:
                    expected_faith = max(0, min(100, expected_faith + _number(loss.group(1))))
                    has_evidence = True
                    explanations.append("同身份天灾/信仰变更文案")
    return {
        "explanations": list(dict.fromkeys(explanations)),
        "expected_faith": expected_faith if has_evidence else None,
        "matched": bool(has_evidence and expected_faith == current_faith),
    }


def build_small_world_evidence(
    messages_dir: Path = MESSAGES_DIR,
    *,
    day: str | None = None,
    days: int = 3,
    now: float | None = None,
) -> dict[str, Any]:
    if day is None:
        day = datetime.fromtimestamp(now or datetime.now(TZ_LOCAL).timestamp(), TZ_LOCAL).strftime("%Y-%m-%d")
    rows = [row for path in _message_paths(messages_dir, day, days) for row in _iter_json_lines(path)]
    rows.sort(key=lambda row: _parse_ts(row.get("ts")))

    # Only roots persisted as script sends are eligible. Player messages that
    # happen to use the same command are deliberately excluded.
    roots: dict[int, dict[str, Any]] = {}
    for row in rows:
        if row.get("event_type") != "sent":
            continue
        text = str(row.get("text") or "").strip()
        if not _ACTION_RE.match(text):
            continue
        try:
            message_id = int(row.get("message_id") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        roots[message_id] = row

    panels: list[dict[str, Any]] = []
    for row in rows:
        if row.get("event_type") not in {"message", "edit"} or not row.get("sender_is_bot"):
            continue
        panel = _parse_panel(str(row.get("text") or ""))
        if panel is None:
            continue
        try:
            reply_to = int(row.get("reply_to_msg_id") or 0)
        except (TypeError, ValueError, OverflowError):
            reply_to = 0
        root = roots.get(reply_to)
        if root is None:
            continue
        panels.append({
            "identity_id": int(root.get("sender_id") or 0),
            "message_id": int(row.get("message_id") or 0),
            "root_message_id": reply_to,
            "ts": _parse_ts(row.get("ts")),
            "faith": panel["faith"],
            "stability": panel["stability"],
            "text": str(row.get("text") or "")[:160],
        })

    panels.sort(key=lambda item: (item["identity_id"], item["ts"], item["message_id"]))
    deltas: list[dict[str, Any]] = []
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for panel in panels:
        grouped[panel["identity_id"]].append(panel)
    for identity_id, identity_panels in grouped.items():
        for previous, current in zip(identity_panels, identity_panels[1:]):
            faith_delta = current["faith"] - previous["faith"]
            stability_delta = current["stability"] - previous["stability"]
            if faith_delta == 0:
                continue
            evidence = _event_explanations(
                rows,
                roots,
                identity_id,
                previous["ts"],
                current["ts"],
                previous["faith"],
                current["faith"],
            )
            deltas.append({
                "identity_id": identity_id,
                "from": _format_ts(previous["ts"]),
                "to": _format_ts(current["ts"]),
                "faith_before": previous["faith"],
                "faith_after": current["faith"],
                "faith_delta": faith_delta,
                "stability_delta": stability_delta,
                "expected_faith": evidence["expected_faith"],
                "explanations": evidence["explanations"],
                "status": "explained" if evidence["matched"] else "unexplained",
            })

    status_counts = Counter(item["status"] for item in deltas)
    return {
        "day": day,
        "days": max(1, days),
        "script_roots": len(roots),
        "script_panels": len(panels),
        "identities": sorted(grouped),
        "deltas": deltas,
        "summary": {
            "explained": status_counts.get("explained", 0),
            "unexplained": status_counts.get("unexplained", 0),
        },
    }


def _capture_paths(capture_dirs: Iterable[Path], day: str | None, days: int) -> list[Path]:
    capture_dirs = tuple(Path(path) for path in capture_dirs)
    if day:
        end = datetime.strptime(day, "%Y-%m-%d").date()
        paths: list[Path] = []
        for offset in range(max(1, days)):
            date_text = f"{end - timedelta(days=offset):%Y-%m-%d}"
            for capture_dir in capture_dirs:
                paths.extend(sorted(capture_dir.glob(f"*-{date_text}.jsonl")))
        return paths
    return [path for capture_dir in capture_dirs for path in sorted(capture_dir.glob("*.jsonl"))]


def build_miniapp_rate_evidence(
    capture_dir: Path | None = None,
    *,
    day: str | None = None,
    days: int = 3,
    limit: int = 90,
    window_sec: int = 60,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    ignored_non_http_records = 0
    capture_dirs = (Path(capture_dir),) if capture_dir is not None else (CAPTURE_DIR, WORLD_BOSS_CAPTURE_DIR)
    for path in _capture_paths(capture_dirs, day, days):
        for row in _iter_json_lines(path):
            ts = float(row.get("created_at") or 0)
            if ts <= 0:
                continue
            method = str(row.get("method") or "").upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                ignored_non_http_records += 1
                continue
            records.append({
                "ts": ts,
                "method": method,
                "adapter_key": str(row.get("adapter_key") or ""),
                "step_key": str(row.get("step_key") or ""),
                "ok": bool(row.get("ok")),
                "error_type": str(row.get("error_type") or ""),
                "source": str(row.get("source") or ""),
            })
    records.sort(key=lambda row: row["ts"])

    left = 0
    max_count = 0
    max_window: tuple[float, float] | None = None
    saturation_windows: list[dict[str, Any]] = []
    for right, row in enumerate(records):
        while left <= right and row["ts"] - records[left]["ts"] >= window_sec:
            left += 1
        count = right - left + 1
        if count > max_count:
            max_count = count
            max_window = (records[left]["ts"], row["ts"])
        if count >= limit and (not saturation_windows or saturation_windows[-1]["end_ts"] < records[left]["ts"]):
            saturation_windows.append({
                "start_ts": records[left]["ts"],
                "end_ts": row["ts"],
                "count": count,
            })

    error_counts = Counter(row["error_type"] for row in records if row["error_type"])
    return {
        "day": day or "all-captures",
        "days": max(1, days),
        "limit": int(limit),
        "window_sec": int(window_sec),
        "records": len(records),
        "ignored_non_http_records": ignored_non_http_records,
        "max_window_count": max_count,
        "max_window": {
            "start": _format_ts(max_window[0]),
            "end": _format_ts(max_window[1]),
        } if max_window else None,
        "saturation_windows": [
            {**item, "start": _format_ts(item["start_ts"]), "end": _format_ts(item["end_ts"])}
            for item in saturation_windows
        ],
        "error_counts": dict(sorted(error_counts.items())),
        "status": "saturated" if saturation_windows else "below_limit",
    }


def build_report(**kwargs) -> dict[str, Any]:
    return {
        "small_world": build_small_world_evidence(**{key: value for key, value in kwargs.items() if key in {"messages_dir", "day", "days", "now"}}),
        "miniapp_rate": build_miniapp_rate_evidence(**{key: value for key, value in kwargs.items() if key in {"capture_dir", "day", "days", "limit", "window_sec"}}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only semantic evidence report.")
    parser.add_argument("--day", default="", help="End day YYYY-MM-DD; default is today for message logs.")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--limit", type=int, default=90)
    parser.add_argument("--window-sec", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(day=args.day or None, days=args.days, limit=args.limit, window_sec=args.window_sec)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        small = report["small_world"]
        rate = report["miniapp_rate"]
        print(f"small-world: panels={small['script_panels']} deltas={small['summary']}")
        print(f"miniapp rate: records={rate['records']} max={rate['max_window_count']}/{rate['limit']} status={rate['status']} errors={rate['error_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
