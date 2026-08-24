#!/usr/bin/env python3
"""Read-only semantic evidence for shared-send checkpoint debt.

This report consumes persisted message logs and sanitized MiniApp captures only.
It does not import the runtime scheduler or call Telegram/HTTP APIs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("XIUXIAN_DATA_DIR") or PROJECT_ROOT / "data")
STATE_DIR = Path(os.environ.get("XIUXIAN_STATE_DIR") or DATA_DIR / "state")
MESSAGES_DIR = Path(os.environ.get("XIUXIAN_MESSAGES_DIR") or DATA_DIR / "messages")
CAPTURE_DIR = STATE_DIR / "miniapp_capture"
WORLD_BOSS_CAPTURE_DIR = MESSAGES_DIR / "miniapp-captures"
TZ_LOCAL = timezone(timedelta(hours=8))

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")
_PANEL_FAITH = re.compile(r"信仰:\s*(\d+)\s*/\s*(\d+)")
_PANEL_STABILITY = re.compile(r"稳定:\s*(\d+)\s*/\s*(\d+)")
_MANIFEST_DELTA = re.compile(r"\(信仰\s*([+-]?\d+),\s*稳定\s*([+-]?\d+),\s*人口\s*([+-]?\d+)")
_ABSOLUTE_FAITH = re.compile(r"信仰(?:提升至|降至|崩塌)\s*(\d+)")
_FAITH_LOSS = re.compile(r"(?:信仰(?:崩塌|动摇)|信仰)\s*([+-]\d+)\s*点")
_STOCK_LOSS = re.compile(r"库存香火损失\s*([+-]?\d+)\s*点")
_HARVEST = re.compile(r"(?:供奉的|待收)\s*([\d,.]+)\s*点?香火")
_REFINE = re.compile(r"燃烧了\s*([\d,.]+)\s*点香火")
_ACTION_RE = re.compile(
    r"^\.(?P<action>小世界|显灵|神迹\s+(?:布道|赈灾)|收割香火|神识淬炼(?:\s+\S+)?)"
)

# These are expected business terminal states declared by the corresponding
# MiniApp flows. Keep this exact and module-scoped so an unknown application
# error is never hidden by a broad substring match.
_MINIAPP_EXPECTED_TERMINAL_ERRORS = {
    "cave_treasure": frozenset({
        "daily_games_exhausted",
        "daily_limit",
        "hunt_daily_limit",
    }),
    "fishing": frozenset({
        "daily_limit",
        "fishing_daily_limit",
        "fishing_daily_limit_reached",
        "fishing_no_remaining",
        "no_remaining",
        "remaining_empty",
        "次数已尽",
        "次数用完",
    }),
    "tree": frozenset({
        "daily_limit",
        "limit_reached",
        "no_remaining",
        "reward_claimed",
        "season_closed",
        "剩余 0",
        "次数已尽",
    }),
    "trial": frozenset({
        "daily_limit",
        "limit_reached",
        "trial_daily_limit",
        "no_remaining",
        "today_exhausted",
        "剩余 0",
        "次数已尽",
    }),
    "world_boss": frozenset({
        "boss_event_closed",
    }),
}

# These are expected business diagnostics for a finished World Boss window.
# They should remain visible without being counted as generic application
# failures. Identity and token errors intentionally remain in error_counts.
_MINIAPP_EXPECTED_DIAGNOSTICS = {
    "world_boss": frozenset({
        "boss_hit_outside_window",
        "boss_client_clock_mismatch",
    }),
}


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
    *,
    indexed_rows: list[dict[str, Any]] | None = None,
    identity_aliases: set[str] | None = None,
) -> dict[str, Any]:
    explanations: list[str] = []
    events: list[dict[str, Any]] = []
    event_keys: set[tuple[Any, ...]] = set()
    expected_faith = previous_faith
    has_faith_evidence = False
    if identity_aliases is None:
        identity_aliases = {
            str(row.get("sender_username") or "").lstrip("@").lower()
            for row in rows
            if row.get("event_type") in {"sent", "message"}
            and int(row.get("sender_id") or 0) == identity_id
            and row.get("sender_username")
        }
    candidate_rows = indexed_rows if indexed_rows is not None else rows

    def add_event(row: dict[str, Any], kind: str, **values: Any) -> None:
        event = {
            "ts": _format_ts(_parse_ts(row.get("ts"))),
            "message_id": _number(row.get("message_id")),
            "kind": kind,
            **values,
        }
        key = (event["message_id"], kind, tuple(sorted(values.items())))
        if key in event_keys:
            return
        event_keys.add(key)
        events.append(event)

    for row in candidate_rows:
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
                has_faith_evidence = True
                explanations.append("同身份显灵回复")
                add_event(
                    row,
                    "manifest_delta",
                    faith_delta=delta,
                    stability_delta=_number(manifest.group(2)),
                    population_delta=_number(manifest.group(3)),
                )
            absolute = _ABSOLUTE_FAITH.search(text)
            if absolute:
                target = _number(absolute.group(1))
                expected_faith = max(0, min(100, target))
                has_faith_evidence = True
                explanations.append("同身份神迹回复")
                add_event(row, "god_absolute_faith", faith=target)

        # Broadcast losses are not replies to our query, but are still direct
        # business evidence when the bot names the same identity.
        if event_type in {"message", "edit"} and row.get("sender_is_bot"):
            lowered = text.lower()
            if identity_aliases and any(f"@{alias}" in lowered for alias in identity_aliases):
                loss = _FAITH_LOSS.search(text)
                if loss:
                    delta = _number(loss.group(1))
                    expected_faith = max(0, min(100, expected_faith + delta))
                    has_faith_evidence = True
                    explanations.append("同身份天灾/信仰变更文案")
                    add_event(row, "faith_loss", faith_delta=delta)
                stock_loss = _STOCK_LOSS.search(text)
                if stock_loss:
                    amount = abs(_number(stock_loss.group(1)))
                    explanations.append("同身份库存香火失窃文案")
                    add_event(row, "stock_loss", incense_delta=-amount)
    return {
        "explanations": list(dict.fromkeys(explanations)),
        "events": events,
        "expected_faith": expected_faith if has_faith_evidence else None,
        "has_faith_evidence": has_faith_evidence,
        "matched": bool(has_faith_evidence and expected_faith == current_faith),
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

    identity_aliases_by_id: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("event_type") not in {"sent", "message"} or not row.get("sender_username"):
            continue
        try:
            sender_id = int(row.get("sender_id") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if sender_id:
            identity_aliases_by_id[sender_id].add(
                str(row.get("sender_username") or "").lstrip("@").lower()
            )

    # Build the same reply/broadcast candidate set once. The previous path
    # rescanned every message for every panel delta, which made a multi-day
    # report scale quadratically with the log volume.
    indexed_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("event_type") == "sent" or not row.get("sender_is_bot"):
            continue
        try:
            reply_to = int(row.get("reply_to_msg_id") or 0)
        except (TypeError, ValueError, OverflowError):
            reply_to = 0
        root = roots.get(reply_to)
        if root is not None:
            try:
                root_identity_id = int(root.get("sender_id") or 0)
            except (TypeError, ValueError, OverflowError):
                root_identity_id = 0
            if root_identity_id:
                indexed_events[root_identity_id].append(row)

        if row.get("event_type") not in {"message", "edit"}:
            continue
        lowered = str(row.get("text") or "").lower()
        if not lowered:
            continue
        for candidate_identity_id, aliases in identity_aliases_by_id.items():
            if any(f"@{alias}" in lowered for alias in aliases):
                indexed_events[candidate_identity_id].append(row)

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
                indexed_rows=indexed_events.get(identity_id, []),
                identity_aliases=identity_aliases_by_id.get(identity_id, set()),
            )
            status = "explained" if evidence["matched"] else (
                "partially_explained" if evidence["has_faith_evidence"] else "unexplained"
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
                "events": evidence["events"],
                "status": status,
            })

    status_counts = Counter(item["status"] for item in deltas)
    events: list[dict[str, Any]] = []
    event_keys: set[tuple[Any, ...]] = set()
    for item in deltas:
        for event in item.get("events") or []:
            key = (item["identity_id"], event.get("message_id"), event.get("kind"))
            if key in event_keys:
                continue
            event_keys.add(key)
            events.append({"identity_id": item["identity_id"], **event})
    events.sort(key=lambda item: (item.get("ts") or "", item.get("message_id") or 0))
    return {
        "day": day,
        "days": max(1, days),
        "script_roots": len(roots),
        "script_panels": len(panels),
        "identities": sorted(grouped),
        "deltas": deltas,
        "events": events,
        "summary": {
            "explained": status_counts.get("explained", 0),
            "partially_explained": status_counts.get("partially_explained", 0),
            "unexplained": status_counts.get("unexplained", 0),
        },
    }


def _capture_paths(
    capture_dirs: Iterable[Path],
    day: str | None,
    days: int,
    *,
    now: float | None = None,
) -> list[Path]:
    capture_dirs = tuple(Path(path) for path in capture_dirs)
    end = (
        datetime.strptime(day, "%Y-%m-%d").date()
        if day
        else datetime.fromtimestamp(float(now if now is not None else time.time()), TZ_LOCAL).date()
    )
    paths: list[Path] = []
    for offset in range(max(1, days)):
        date_text = f"{end - timedelta(days=offset):%Y-%m-%d}"
        for capture_dir in capture_dirs:
            paths.extend(sorted(capture_dir.glob(f"*-{date_text}.jsonl")))
    return paths


def _expected_miniapp_terminal_error(record: dict[str, Any]) -> str:
    adapter_key = str(record.get("adapter_key") or "").strip().lower()
    error = str(record.get("error") or "").strip().lower()
    if error and error in _MINIAPP_EXPECTED_TERMINAL_ERRORS.get(adapter_key, ()):
        return error
    return ""


def _expected_miniapp_diagnostic(record: dict[str, Any]) -> str:
    adapter_key = str(record.get("adapter_key") or "").strip().lower()
    error = str(record.get("error") or "").strip().lower()
    if error and error in _MINIAPP_EXPECTED_DIAGNOSTICS.get(adapter_key, ()):
        return error
    return ""


def build_miniapp_rate_evidence(
    capture_dir: Path | None = None,
    *,
    day: str | None = None,
    days: int = 3,
    limit: int = 90,
    window_sec: int = 60,
    now: float | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    ignored_non_http_records = 0
    capture_dirs = (Path(capture_dir),) if capture_dir is not None else (CAPTURE_DIR, WORLD_BOSS_CAPTURE_DIR)
    effective_now = float(now if now is not None else time.time())
    effective_day = day or datetime.fromtimestamp(effective_now, TZ_LOCAL).strftime("%Y-%m-%d")
    for path in _capture_paths(capture_dirs, day, days, now=effective_now):
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
                "error": str(row.get("error") or ""),
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

    terminal_counts: Counter[str] = Counter()
    diagnostic_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    for row in records:
        terminal_error = _expected_miniapp_terminal_error(row)
        if terminal_error:
            terminal_counts[terminal_error] += 1
        elif (diagnostic := _expected_miniapp_diagnostic(row)):
            diagnostic_counts[diagnostic] += 1
        elif row["error_type"]:
            error_counts[row["error_type"]] += 1
    return {
        "day": effective_day,
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
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "diagnostic_counts": dict(sorted(diagnostic_counts.items())),
        "error_counts": dict(sorted(error_counts.items())),
        "status": "saturated" if saturation_windows else "below_limit",
    }


def build_report(**kwargs) -> dict[str, Any]:
    return {
        "small_world": build_small_world_evidence(**{key: value for key, value in kwargs.items() if key in {"messages_dir", "day", "days", "now"}}),
        "miniapp_rate": build_miniapp_rate_evidence(**{key: value for key, value in kwargs.items() if key in {"capture_dir", "day", "days", "limit", "window_sec", "now"}}),
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
        print(
            f"miniapp rate: records={rate['records']} "
            f"max={rate['max_window_count']}/{rate['limit']} status={rate['status']} "
            f"terminals={rate['terminal_counts']} "
            f"diagnostics={rate['diagnostic_counts']} errors={rate['error_counts']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
