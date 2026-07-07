"""MiniApp capture summary helpers.

The capture JSONL files are already sanitized by ``model.webapp_core``.  This
module keeps the second layer compact: enough protocol evidence for UI and AI
handoff, without dumping large response bodies into the page.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .webapp_core import sanitize_webapp_secret_text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURE_DIR = PROJECT_ROOT / "data" / "state" / "miniapp_capture"
_GAME_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,48}$")


def normalize_miniapp_game_key(value):
    game_key = str(value or "").strip().lower()
    return game_key if _GAME_KEY_RE.match(game_key) else ""


def _today_text():
    return datetime.now().strftime("%Y-%m-%d")


def _safe_day_text(value):
    text = str(value or "").strip()
    return text if re.match(r"^\d{4}-\d{2}-\d{2}$", text) else ""


def _capture_path(game_key, day="", *, capture_dir=None):
    normalized = normalize_miniapp_game_key(game_key)
    if not normalized:
        return None
    base = Path(capture_dir or DEFAULT_CAPTURE_DIR)
    safe_day = _safe_day_text(day) or _today_text()
    return base / f"{normalized}-{safe_day}.jsonl"


def find_latest_capture_day(game_key, *, capture_dir=None):
    normalized = normalize_miniapp_game_key(game_key)
    if not normalized:
        return ""
    base = Path(capture_dir or DEFAULT_CAPTURE_DIR)
    matches = sorted(base.glob(f"{normalized}-*.jsonl"))
    if not matches:
        return ""
    stem = matches[-1].stem
    prefix = f"{normalized}-"
    return stem[len(prefix):] if stem.startswith(prefix) else ""


def load_miniapp_capture_records(game_key, *, day="", limit=200, capture_dir=None):
    normalized = normalize_miniapp_game_key(game_key)
    safe_day = _safe_day_text(day) or find_latest_capture_day(normalized, capture_dir=capture_dir) or _today_text()
    path = _capture_path(normalized, safe_day, capture_dir=capture_dir)
    if path is None or not path.exists():
        return {
            "game_key": normalized,
            "day": safe_day,
            "path": str(path or ""),
            "records": [],
            "missing": True,
        }
    try:
        max_records = max(1, min(1000, int(limit or 200)))
    except (TypeError, ValueError):
        max_records = 200
    rows = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return {
        "game_key": normalized,
        "day": safe_day,
        "path": str(path),
        "records": rows[-max_records:],
        "missing": False,
        "total_lines": len(rows),
        "scanned_lines": min(len(rows), max_records),
    }


def _shape_keys(shape):
    if not isinstance(shape, dict):
        return []
    keys = shape.get("keys")
    if isinstance(keys, list):
        return sorted(str(key) for key in keys)
    return []


def _short_shape(shape, *, max_children=8):
    if not isinstance(shape, dict):
        return {}
    result = {
        "type": str(shape.get("type") or ""),
        "keys": _shape_keys(shape)[:max_children],
    }
    if "length" in shape:
        result["length"] = shape.get("length")
    children = shape.get("children")
    if isinstance(children, dict):
        result["children"] = {
            str(key): {
                "type": str(value.get("type") or ""),
                "keys": _shape_keys(value)[:max_children],
                **({"length": value.get("length")} if "length" in value else {}),
            }
            for key, value in list(sorted(children.items()))[:max_children]
            if isinstance(value, dict)
        }
    items = shape.get("items")
    if isinstance(items, list) and items:
        result["items"] = [_short_shape(items[0], max_children=max_children)]
    return {key: value for key, value in result.items() if value not in ("", [], {}, None)}


def _latest_text(ts):
    try:
        value = float(ts or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _summary_text(value, *, limit=220):
    return sanitize_webapp_secret_text(value, limit=limit)


def summarize_miniapp_capture_records(game_key, records, *, day="", path="", total_lines=None, scanned_lines=None):
    normalized = normalize_miniapp_game_key(game_key)
    groups = {}
    for row in records or []:
        if not isinstance(row, dict):
            continue
        method = str(row.get("method") or "POST").upper()
        url_path = str(row.get("url_path") or "")
        step_key = str(row.get("step_key") or row.get("endpoint") or url_path or "")
        group_key = (method, url_path, step_key)
        item = groups.setdefault(group_key, {
            "method": method,
            "url_path": url_path,
            "step_key": step_key,
            "endpoint": str(row.get("endpoint") or ""),
            "count": 0,
            "ok_count": 0,
            "error_count": 0,
            "status_codes": Counter(),
            "elapsed_ms_total": 0,
            "request_payload_keys": set(),
            "request_secret_keys": set(),
            "response_keys": set(),
            "latest": None,
            "latest_ok": None,
            "latest_error": None,
        })
        item["count"] += 1
        if row.get("ok"):
            item["ok_count"] += 1
        else:
            item["error_count"] += 1
        status_code = int(row.get("status_code") or 0)
        if status_code:
            item["status_codes"][str(status_code)] += 1
        try:
            item["elapsed_ms_total"] += int(row.get("elapsed_ms") or 0)
        except (TypeError, ValueError):
            pass
        request = row.get("request") if isinstance(row.get("request"), dict) else {}
        summary = request.get("summary") if isinstance(request.get("summary"), dict) else {}
        payload_shape = request.get("payload_shape") if isinstance(request.get("payload_shape"), dict) else {}
        item["request_payload_keys"].update(str(key) for key in (summary.get("payload_keys") or _shape_keys(payload_shape)))
        item["request_secret_keys"].update(str(key) for key in (summary.get("secret_keys") or []))
        response = row.get("response") if isinstance(row.get("response"), dict) else {}
        item["response_keys"].update(str(key) for key in (response.get("data_keys") or _shape_keys(response.get("body_shape"))))
        latest = item["latest"]
        if latest is None or float(row.get("created_at") or 0) >= float(latest.get("created_at") or 0):
            item["latest"] = row
        if row.get("ok"):
            latest_ok = item["latest_ok"]
            if latest_ok is None or float(row.get("created_at") or 0) >= float(latest_ok.get("created_at") or 0):
                item["latest_ok"] = row
        else:
            latest_error = item["latest_error"]
            if latest_error is None or float(row.get("created_at") or 0) >= float(latest_error.get("created_at") or 0):
                item["latest_error"] = row

    endpoint_items = []
    for item in groups.values():
        latest = item.get("latest") or {}
        latest_ok = item.get("latest_ok") or {}
        latest_error = item.get("latest_error") or {}
        request = latest.get("request") if isinstance(latest.get("request"), dict) else {}
        response = latest.get("response") if isinstance(latest.get("response"), dict) else {}
        count = int(item["count"] or 0)
        endpoint_items.append({
            "method": item["method"],
            "url_path": item["url_path"],
            "step_key": item["step_key"],
            "endpoint": item["endpoint"],
            "count": count,
            "ok_count": int(item["ok_count"] or 0),
            "error_count": int(item["error_count"] or 0),
            "status_codes": dict(sorted(item["status_codes"].items())),
            "avg_elapsed_ms": int(item["elapsed_ms_total"] / count) if count else 0,
            "request_payload_keys": sorted(item["request_payload_keys"]),
            "request_secret_keys": sorted(item["request_secret_keys"]),
            "response_keys": sorted(item["response_keys"]),
            "request_shape": _short_shape(request.get("payload_shape")),
            "response_shape": _short_shape(response.get("body_shape")),
            "latest_at": float(latest.get("created_at") or 0),
            "latest_at_text": _latest_text(latest.get("created_at")),
            "latest_source": _summary_text(latest.get("source"), limit=120),
            "latest_ok": bool(latest.get("ok")),
            "latest_error": _summary_text(latest.get("error"), limit=220),
            "latest_success_at": float(latest_ok.get("created_at") or 0),
            "latest_success_at_text": _latest_text(latest_ok.get("created_at")),
            "latest_error_at": float(latest_error.get("created_at") or 0),
            "latest_error_at_text": _latest_text(latest_error.get("created_at")),
        })
    endpoint_items.sort(key=lambda item: (item["url_path"], item["step_key"]))

    recent = []
    for row in sorted((row for row in (records or []) if isinstance(row, dict)), key=lambda r: float(r.get("created_at") or 0))[-8:]:
        recent.append({
            "created_at": float(row.get("created_at") or 0),
            "created_at_text": _latest_text(row.get("created_at")),
            "step_key": str(row.get("step_key") or ""),
            "method": str(row.get("method") or ""),
            "url_path": str(row.get("url_path") or ""),
            "ok": bool(row.get("ok")),
            "status_code": int(row.get("status_code") or 0),
            "elapsed_ms": int(row.get("elapsed_ms") or 0),
            "error": _summary_text(row.get("error"), limit=220),
        })

    ok_total = sum(1 for row in records or [] if isinstance(row, dict) and row.get("ok"))
    return {
        "game_key": normalized,
        "day": _safe_day_text(day) or "",
        "path": str(path or ""),
        "total_records": int(total_lines if total_lines is not None else len(records or [])),
        "scanned_records": int(scanned_lines if scanned_lines is not None else len(records or [])),
        "ok_records": ok_total,
        "error_records": max(0, len(records or []) - ok_total),
        "endpoint_count": len(endpoint_items),
        "endpoints": endpoint_items,
        "recent": recent,
        "redaction": {
            "raw_init_data": False,
            "raw_start_token": False,
            "raw_headers": False,
        },
        "ai_handoff": {
            "rule": "按 endpoint/request_payload_keys 构造请求；按 response_shape/response_keys 修解析；缺样本先抓包，禁止猜 raw token/initData。",
            "capture_fields": ["method", "url_path", "request_shape", "response_shape", "status_codes", "latest_error"],
        },
    }


def get_miniapp_capture_summary(game_key, *, day="", limit=200, capture_dir=None):
    loaded = load_miniapp_capture_records(game_key, day=day, limit=limit, capture_dir=capture_dir)
    return summarize_miniapp_capture_records(
        loaded.get("game_key") or game_key,
        loaded.get("records") or [],
        day=loaded.get("day") or day,
        path=loaded.get("path") or "",
        total_lines=loaded.get("total_lines", 0 if loaded.get("missing") else None),
        scanned_lines=loaded.get("scanned_lines", 0 if loaded.get("missing") else None),
    )


def format_miniapp_capture_summary(summary):
    lines = [
        f"MiniApp capture: {summary.get('game_key') or '-'} {summary.get('day') or '-'}",
        f"records: {summary.get('scanned_records', 0)}/{summary.get('total_records', 0)} | endpoints: {summary.get('endpoint_count', 0)} | ok/error: {summary.get('ok_records', 0)}/{summary.get('error_records', 0)}",
    ]
    for item in summary.get("endpoints") or []:
        lines.append(
            f"- {item.get('method')} {item.get('url_path')} [{item.get('step_key')}] "
            f"count={item.get('count')} ok={item.get('ok_count')} err={item.get('error_count')} "
            f"payload={','.join(item.get('request_payload_keys') or []) or '-'} "
            f"response={','.join(item.get('response_keys') or []) or '-'}"
        )
        if item.get("latest_success_at_text") or item.get("latest_error_at_text"):
            lines.append(
                f"  latest_ok_at: {item.get('latest_success_at_text') or '-'} | "
                f"latest_err_at: {item.get('latest_error_at_text') or '-'}"
            )
        if item.get("latest_error"):
            lines.append(f"  latest_error: {item.get('latest_error')}")
    if not summary.get("endpoints"):
        lines.append("- no capture samples")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_CAPTURE_DIR",
    "find_latest_capture_day",
    "format_miniapp_capture_summary",
    "get_miniapp_capture_summary",
    "load_miniapp_capture_records",
    "normalize_miniapp_game_key",
    "summarize_miniapp_capture_records",
]
