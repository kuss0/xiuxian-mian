import json
import os
import re
import time
from datetime import datetime

from ..config import (
    LOG_RETENTION_CLEANUP_INTERVAL_SEC,
    STATE_DIR,
    TZ_LOCAL,
    WORKFLOW_LOG_MAX_MB,
    WORKFLOW_LOG_RETENTION_DAYS,
)
from ..log_retention import cleanup_log_files


_DEFAULT_WORKFLOW_LOG_DIR = os.path.join(STATE_DIR, "workflow_logs")
WORKFLOW_LOG_DIR = _DEFAULT_WORKFLOW_LOG_DIR
WORKFLOW_TEXT_LIMIT = 1000
WORKFLOW_DETAIL_LIMIT = 500
WORKFLOW_RETENTION_DAYS = WORKFLOW_LOG_RETENTION_DAYS
WORKFLOW_MAX_BYTES = max(0, int(WORKFLOW_LOG_MAX_MB or 0)) * 1024 * 1024
WORKFLOW_CLEANUP_INTERVAL_SEC = LOG_RETENTION_CLEANUP_INTERVAL_SEC

_last_cleanup_at = 0.0
_SAFE_WORKFLOW_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


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


def _safe_workflow_name(value):
    name = _SAFE_WORKFLOW_RE.sub("_", str(value or "").strip())
    return name.strip("._-") or "unknown"


def _truncate_text(value, limit=WORKFLOW_TEXT_LIMIT):
    if isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(value)
    else:
        text = str(value or "")
    text = text.replace("\r", "\n").strip()
    if not text:
        return ""
    safe_limit = max(1, int(limit or WORKFLOW_TEXT_LIMIT))
    if len(text) <= safe_limit:
        return text
    return text[:safe_limit]


def _compact_detail(value, depth=0):
    if value is None:
        return None
    if depth > 2:
        return _truncate_text(value, WORKFLOW_DETAIL_LIMIT)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = _truncate_text(key, 80)
            if not key_text:
                continue
            compact_item = _compact_detail(item, depth + 1)
            if compact_item not in (None, "", [], {}):
                result[key_text] = compact_item
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in list(value)[:20]:
            compact_item = _compact_detail(item, depth + 1)
            if compact_item not in (None, "", [], {}):
                result.append(compact_item)
        return result
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if value else None
    return _truncate_text(value, WORKFLOW_DETAIL_LIMIT)


def _day_key(ts=None):
    return datetime.fromtimestamp(_safe_float(ts) or time.time(), TZ_LOCAL).strftime("%Y-%m-%d")


def _resolve_workflow_log_dir():
    configured_dir = str(WORKFLOW_LOG_DIR or "").strip()
    if configured_dir and configured_dir != _DEFAULT_WORKFLOW_LOG_DIR:
        return configured_dir
    env_state_dir = str(os.environ.get("XIUXIAN_STATE_DIR") or "").strip()
    if env_state_dir:
        return os.path.join(os.path.abspath(env_state_dir), "workflow_logs")
    return configured_dir or _DEFAULT_WORKFLOW_LOG_DIR


def get_workflow_log_path(workflow, ts=None):
    return os.path.join(_resolve_workflow_log_dir(), _safe_workflow_name(workflow), f"{_day_key(ts)}.jsonl")


def _cleanup_old_workflow_logs(now=None):
    global _last_cleanup_at
    now = _safe_float(now) or time.time()
    if _last_cleanup_at and now - _last_cleanup_at < WORKFLOW_CLEANUP_INTERVAL_SEC:
        return
    _last_cleanup_at = now
    base_dir = _resolve_workflow_log_dir()
    cleanup_log_files(
        base_dir,
        suffixes=(".jsonl",),
        retention_days=WORKFLOW_RETENTION_DAYS,
        max_bytes=WORKFLOW_MAX_BYTES,
        recursive=True,
        now=now,
    )


def _compact_payload(payload):
    result = {}
    int_keys = {"identity_id", "chat_id", "msg_id", "message_id", "reply_to_msg_id", "source_message_id"}
    for key, value in payload.items():
        if value is None:
            continue
        if key in int_keys:
            int_value = _safe_int(value)
            if int_value:
                result[key] = int_value
            continue
        if key == "ts":
            float_value = _safe_float(value)
            if float_value:
                result[key] = float_value
            continue
        if key == "detail":
            detail_value = _compact_detail(value)
            if detail_value not in (None, "", [], {}):
                result[key] = detail_value
            continue
        text_limit = WORKFLOW_TEXT_LIMIT if key in {"text", "summary"} else WORKFLOW_DETAIL_LIMIT
        text_value = _truncate_text(value, text_limit)
        if text_value:
            result[key] = text_value
    return result


def append_workflow_event(
    workflow,
    *,
    op_id="",
    step="",
    event="",
    status="",
    identity_id=0,
    chat_id=0,
    msg_id=0,
    reply_to_msg_id=0,
    source_message_id=0,
    family="",
    command="",
    text="",
    decision="",
    detail=None,
    route_source="",
    state_before="",
    state_after="",
    now=None,
):
    workflow_name = _safe_workflow_name(workflow)
    ts = _safe_float(now) or time.time()
    payload = _compact_payload(
        {
            "ts": ts,
            "workflow": workflow_name,
            "op_id": op_id,
            "step": step,
            "event": event,
            "status": status,
            "identity_id": identity_id,
            "chat_id": chat_id,
            "msg_id": msg_id,
            "message_id": msg_id,
            "reply_to_msg_id": reply_to_msg_id,
            "source_message_id": source_message_id,
            "family": family,
            "command": command,
            "text": text,
            "decision": decision,
            "detail": detail,
            "route_source": route_source,
            "state_before": state_before,
            "state_after": state_after,
        }
    )
    if not payload:
        return False
    try:
        path = get_workflow_log_path(workflow_name, ts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _cleanup_old_workflow_logs(ts)
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        return False
    return True


def iter_workflow_events(workflow, path=None, limit=100):
    source_path = path or get_workflow_log_path(workflow)
    safe_limit = max(1, min(int(limit or 100), 1000))
    try:
        with open(source_path, "r", encoding="utf-8") as fp:
            lines = fp.readlines()[-safe_limit:]
    except OSError:
        return []
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
    "append_workflow_event",
    "get_workflow_log_path",
    "iter_workflow_events",
]
