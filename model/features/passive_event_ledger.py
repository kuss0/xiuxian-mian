import json
import os
import time
from datetime import datetime

from ..config import (
    LOG_RETENTION_CLEANUP_INTERVAL_SEC,
    PASSIVE_EVENT_LEDGER_MAX_MB,
    PASSIVE_EVENT_LEDGER_RETENTION_DAYS,
    STATE_DIR,
    TZ_LOCAL,
)
from ..log_retention import cleanup_log_files


_DEFAULT_PASSIVE_EVENT_LEDGER_DIR = os.path.join(STATE_DIR, "passive_event_ledger")
PASSIVE_EVENT_LEDGER_DIR = _DEFAULT_PASSIVE_EVENT_LEDGER_DIR
PASSIVE_EVENT_TEXT_LIMIT = 800
PASSIVE_EVENT_DETAIL_LIMIT = 240
PASSIVE_EVENT_RETENTION_DAYS = PASSIVE_EVENT_LEDGER_RETENTION_DAYS
PASSIVE_EVENT_MAX_BYTES = max(0, int(PASSIVE_EVENT_LEDGER_MAX_MB or 0)) * 1024 * 1024
PASSIVE_EVENT_CLEANUP_INTERVAL_SEC = LOG_RETENTION_CLEANUP_INTERVAL_SEC

_last_cleanup_at = 0.0


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _truncate_text(value, limit=PASSIVE_EVENT_TEXT_LIMIT):
    text = str(value or "").replace("\r", "\n").strip()
    if not text:
        return ""
    safe_limit = max(1, int(limit or PASSIVE_EVENT_TEXT_LIMIT))
    if len(text) <= safe_limit:
        return text
    return text[:safe_limit]


def _day_key(ts=None):
    return datetime.fromtimestamp(_safe_float(ts) or time.time(), TZ_LOCAL).strftime("%Y-%m-%d")


def _resolve_ledger_dir():
    configured_dir = str(PASSIVE_EVENT_LEDGER_DIR or "").strip()
    if configured_dir and configured_dir != _DEFAULT_PASSIVE_EVENT_LEDGER_DIR:
        return configured_dir
    env_state_dir = str(os.environ.get("XIUXIAN_STATE_DIR") or "").strip()
    if env_state_dir:
        return os.path.join(os.path.abspath(env_state_dir), "passive_event_ledger")
    return configured_dir or _DEFAULT_PASSIVE_EVENT_LEDGER_DIR


def get_passive_event_ledger_path(ts=None):
    return os.path.join(_resolve_ledger_dir(), f"{_day_key(ts)}.jsonl")


def _recent_ledger_paths(limit_files=30):
    ledger_dir = _resolve_ledger_dir()
    try:
        names = sorted(name for name in os.listdir(ledger_dir) if name.endswith(".jsonl"))
    except OSError:
        return []
    safe_limit = max(1, int(limit_files or 30))
    return [os.path.join(ledger_dir, name) for name in names[-safe_limit:]]


def _cleanup_old_ledgers(now=None):
    global _last_cleanup_at
    now = _safe_float(now) or time.time()
    if _last_cleanup_at and now - _last_cleanup_at < PASSIVE_EVENT_CLEANUP_INTERVAL_SEC:
        return
    _last_cleanup_at = now
    ledger_dir = _resolve_ledger_dir()
    cleanup_log_files(
        ledger_dir,
        suffixes=(".jsonl",),
        retention_days=PASSIVE_EVENT_RETENTION_DAYS,
        max_bytes=PASSIVE_EVENT_MAX_BYTES,
        recursive=False,
        now=now,
    )


def _compact_payload(payload):
    result = {}
    for key, value in payload.items():
        if value is None:
            continue
        if key in {"chat_id", "msg_id", "message_id", "reply_to_msg_id", "reply_to_sender_id", "root_msg_id", "source_message_id", "identity_id"}:
            int_value = _safe_int(value)
            if int_value:
                result[key] = int_value
            continue
        if key == "ts":
            float_value = _safe_float(value)
            if float_value:
                result[key] = float_value
            continue
        text_limit = PASSIVE_EVENT_TEXT_LIMIT if key in {"matched_text", "summary"} else PASSIVE_EVENT_DETAIL_LIMIT
        text_value = _truncate_text(value, text_limit)
        if text_value:
            result[key] = text_value
    return result


def append_passive_event(
    *,
    kind,
    module="",
    identity_id=0,
    reason="",
    summary="",
    family="",
    chat_id=0,
    msg_id=0,
    reply_to_msg_id=0,
    reply_to_sender_id=0,
    root_msg_id=0,
    event_type="",
    route_source="",
    matched_text="",
    matched_text_hash="",
    decision="",
    state_before="",
    state_after="",
    command="",
    source_message_id=0,
    now=None,
):
    ts = _safe_float(now) or time.time()
    payload = _compact_payload(
        {
            "ts": ts,
            "kind": kind,
            "module": module,
            "identity_id": identity_id,
            "reason": reason,
            "summary": summary,
            "family": family,
            "chat_id": chat_id,
            "msg_id": msg_id,
            "message_id": msg_id,
            "reply_to_msg_id": reply_to_msg_id,
            "reply_to_sender_id": reply_to_sender_id,
            "root_msg_id": root_msg_id,
            "event_type": event_type,
            "route_source": route_source,
            "matched_text": matched_text,
            "matched_text_hash": matched_text_hash,
            "decision": decision,
            "state_before": state_before,
            "state_after": state_after,
            "command": command,
            "source_message_id": source_message_id,
        }
    )
    if not payload:
        return False
    try:
        ledger_dir = _resolve_ledger_dir()
        os.makedirs(ledger_dir, exist_ok=True)
        _cleanup_old_ledgers(ts)
        path = get_passive_event_ledger_path(ts)
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        return False
    return True


def iter_passive_events(path=None, limit=100):
    safe_limit = max(1, min(int(limit or 100), 1000))
    source_paths = [path] if path else _recent_ledger_paths()
    if not source_paths:
        source_paths = [get_passive_event_ledger_path()]
    lines = []
    for source_path in source_paths:
        try:
            with open(source_path, "r", encoding="utf-8") as fp:
                lines.extend(fp.readlines())
                if len(lines) > safe_limit:
                    lines = lines[-safe_limit:]
        except OSError:
            continue
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


__all__ = [
    "append_passive_event",
    "get_passive_event_ledger_path",
    "iter_passive_events",
]
