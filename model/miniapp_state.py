import hashlib
import json
import re
import time

from .persistence import save_state
from .state import get_identity_ids, get_miniapp_state_records, get_send_as_profile, set_miniapp_state_records
from .timing import fmt_abs_ts
from .webapp_core import sanitize_webapp_secret_text


MINIAPP_STATE_RECORD_LIMIT = 300
_SOURCE_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_GAME_KEY_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_SENSITIVE_KEY_PARTS = (
    "token",
    "initdata",
    "tgwebappdata",
    "hash",
    "auth",
    "cookie",
    "secret",
    "session",
    "webview",
    "url",
)
_SAFE_SESSION_SUMMARY_KEYS = {"hassessionid", "sessioniddigest"}


def _stable_digest(value, *, length=20):
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(value)
    digest = hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()
    return digest[: max(8, int(length or 20))]


def _normalize_game_key(value):
    text = _GAME_KEY_RE.sub("_", str(value or "").strip().lower()).strip("._:-")
    return text[:64]


def _normalize_text(value, *, limit=120):
    return sanitize_webapp_secret_text(str(value or "").strip(), limit=limit)


def _normalize_string_list(values, *, limit=64):
    result = []
    seen = set()
    for value in values or ():
        text = _normalize_text(value, limit=limit)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _source_id_is_sensitive(text):
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(text or "")).lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _safe_source_id(value, *, fallback_payload=None):
    text = str(value or "").strip()
    if text and not _source_id_is_sensitive(text):
        safe = _SOURCE_ID_SAFE_RE.sub("_", text)[:120].strip("._:-")
        if safe:
            return safe
    return f"payload:{_stable_digest(fallback_payload or text or {})}"


def _is_sensitive_key(key):
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(key or "")).lower()
    if normalized in _SAFE_SESSION_SUMMARY_KEYS:
        return False
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _safe_state_value(value, *, depth=0):
    if depth > 5:
        return _normalize_text(value, limit=120)
    if isinstance(value, dict):
        result = {}
        for raw_key, child in sorted(value.items(), key=lambda item: str(item[0])):
            key = str(raw_key or "").strip()
            if not key or _is_sensitive_key(key):
                continue
            safe_key = _normalize_text(key, limit=80)
            if not safe_key:
                continue
            safe_child = _safe_state_value(child, depth=depth + 1)
            if safe_child not in (None, "", [], {}):
                result[safe_key] = safe_child
        return result
    if isinstance(value, list):
        result = []
        for item in value[:80]:
            safe_item = _safe_state_value(item, depth=depth + 1)
            if safe_item not in (None, "", [], {}):
                result.append(safe_item)
        return result
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return _normalize_text(value, limit=240)


def sanitize_miniapp_state(state):
    if not isinstance(state, dict):
        return {}
    safe = _safe_state_value(state)
    if not isinstance(safe, dict):
        safe = {}
    session_value = (
        state.get("session_id")
        or state.get("sessionId")
        or state.get("session")
        or state.get("huntSessionId")
    )
    if session_value not in (None, ""):
        safe["has_session_id"] = True
        safe["session_id_digest"] = _stable_digest(str(session_value), length=16)
    return safe


def _record_key(identity_id, game_key):
    return f"{int(identity_id or 0)}:{_normalize_game_key(game_key)}"


def _prune_state_records(records):
    rows = [
        (key, record)
        for key, record in (records or {}).items()
        if isinstance(record, dict) and str(key or "") != "_meta"
    ]
    rows.sort(key=lambda item: float(item[1].get("updated_at") or 0), reverse=True)
    pruned = {key: record for key, record in rows[:MINIAPP_STATE_RECORD_LIMIT]}
    now = time.time()
    meta = records.get("_meta") if isinstance((records or {}).get("_meta"), dict) else {}
    pruned["_meta"] = {
        **meta,
        "updated_at": now,
        "updated_at_text": fmt_abs_ts(now),
        "record_limit": MINIAPP_STATE_RECORD_LIMIT,
    }
    return pruned


def record_miniapp_state(
    identity_id,
    game_key,
    state,
    *,
    source="",
    source_id="",
    now=None,
    outputs=None,
    replaces_commands=None,
    persist=True,
):
    try:
        identity_id = int(identity_id or 0)
    except (TypeError, ValueError, OverflowError):
        identity_id = 0
    game_key = _normalize_game_key(game_key)
    safe_state = sanitize_miniapp_state(state)
    if identity_id <= 0 or not game_key or not safe_state:
        return {"changed": False, "record": {}, "record_key": ""}
    now = time.time() if now is None else float(now)
    source = _normalize_text(source or f"{game_key}_miniapp", limit=80)
    record = {
        "identity_id": identity_id,
        "game_key": game_key,
        "source": source,
        "source_id": _safe_source_id(
            source_id,
            fallback_payload={"identity_id": identity_id, "game_key": game_key, "state": safe_state},
        ),
        "state": safe_state,
        "outputs": _normalize_string_list(outputs or ()),
        "replaces_commands": _normalize_string_list(replaces_commands or ()),
        "updated_at": float(now),
        "updated_at_text": fmt_abs_ts(now),
    }
    key = _record_key(identity_id, game_key)
    records = dict(get_miniapp_state_records())
    previous = records.get(key) if isinstance(records.get(key), dict) else {}
    comparable_keys = ("identity_id", "game_key", "source", "source_id", "state", "outputs", "replaces_commands")
    if previous and {k: previous.get(k) for k in comparable_keys} == {k: record.get(k) for k in comparable_keys}:
        return {"changed": False, "record": previous, "record_key": key}
    records[key] = record
    set_miniapp_state_records(_prune_state_records(records))
    if persist:
        save_state()
    return {"changed": True, "record": record, "record_key": key}


def _identity_filter(identity_ids=None, send_as_id=None):
    if send_as_id not in (None, "", 0, "0"):
        try:
            return {int(send_as_id)}
        except (TypeError, ValueError, OverflowError):
            return set()
    if identity_ids is None:
        return None
    result = set()
    for item in identity_ids or ():
        try:
            value = int(item or 0)
        except (TypeError, ValueError, OverflowError):
            value = 0
        if value > 0:
            result.add(value)
    return result


def get_miniapp_state_snapshot(identity_ids=None, game_key=None, *, send_as_id=None, now=None):
    now = time.time() if now is None else float(now)
    game_key = _normalize_game_key(game_key) if game_key else ""
    identity_filter = _identity_filter(identity_ids=identity_ids, send_as_id=send_as_id)
    records = get_miniapp_state_records()
    rows = []
    for key, record in (records or {}).items():
        if str(key or "") == "_meta" or not isinstance(record, dict):
            continue
        try:
            identity_id = int(record.get("identity_id") or 0)
        except (TypeError, ValueError, OverflowError):
            identity_id = 0
        if identity_id <= 0:
            continue
        if identity_filter is not None and identity_id not in identity_filter:
            continue
        record_game_key = _normalize_game_key(record.get("game_key"))
        if game_key and record_game_key != game_key:
            continue
        profile = get_send_as_profile(identity_id)
        updated_at = float(record.get("updated_at") or 0)
        rows.append({
            "record_key": str(key),
            "identity_id": identity_id,
            "label": profile.get("label") or profile.get("username") or profile.get("daohao") or str(identity_id),
            "game_key": record_game_key,
            "source": _normalize_text(record.get("source"), limit=80),
            "source_id": _safe_source_id(record.get("source_id"), fallback_payload=record),
            "state": sanitize_miniapp_state(record.get("state") if isinstance(record.get("state"), dict) else {}),
            "outputs": _normalize_string_list(record.get("outputs") or ()),
            "replaces_commands": _normalize_string_list(record.get("replaces_commands") or ()),
            "updated_at": updated_at,
            "updated_at_text": fmt_abs_ts(updated_at),
            "age_sec": max(0, int(now - updated_at)) if updated_at > 0 else 0,
        })
    rows.sort(key=lambda item: (item["game_key"], item["identity_id"]))
    by_identity = {}
    for row in rows:
        by_identity.setdefault(str(row["identity_id"]), {})[row["game_key"]] = row
    meta = records.get("_meta") if isinstance((records or {}).get("_meta"), dict) else {}
    return {
        "record_count": len(rows),
        "rows": rows,
        "by_identity": by_identity,
        "known_identity_ids": list(get_identity_ids()),
        "meta": {
            "record_limit": int(meta.get("record_limit") or MINIAPP_STATE_RECORD_LIMIT),
            "updated_at": float(meta.get("updated_at") or 0),
            "updated_at_text": str(meta.get("updated_at_text") or ""),
        },
    }


def replay_miniapp_capture_records(records, parser, *, game_key="", endpoint=""):
    game_key = _normalize_game_key(game_key) if game_key else ""
    endpoint_filter = {str(item or "").strip() for item in endpoint} if isinstance(endpoint, (list, tuple, set)) else set()
    if isinstance(endpoint, str) and endpoint.strip():
        endpoint_filter.add(endpoint.strip())
    states = []
    errors = []
    endpoints = []
    for record in records or ():
        if not isinstance(record, dict):
            continue
        record_game_key = _normalize_game_key(record.get("adapter_key") or record.get("game_key"))
        if game_key and record_game_key != game_key:
            continue
        record_endpoint = str(record.get("endpoint") or "").strip()
        if endpoint_filter and record_endpoint not in endpoint_filter:
            continue
        response = record.get("response") if isinstance(record.get("response"), dict) else {}
        body = response.get("body") if isinstance(response, dict) else {}
        if not isinstance(body, dict):
            continue
        endpoints.append(record_endpoint)
        try:
            parsed = parser(body)
        except Exception as exc:
            errors.append({
                "endpoint": record_endpoint,
                "error": sanitize_webapp_secret_text(exc),
            })
            continue
        safe_state = sanitize_miniapp_state(parsed)
        if safe_state:
            states.append({
                "endpoint": record_endpoint,
                "step_key": str(record.get("step_key") or ""),
                "ok": bool(record.get("ok")),
                "state": safe_state,
            })
    return {
        "record_count": len(endpoints),
        "state_count": len(states),
        "endpoints": endpoints,
        "states": states,
        "latest_state": states[-1]["state"] if states else {},
        "errors": errors,
    }


__all__ = [
    "MINIAPP_STATE_RECORD_LIMIT",
    "get_miniapp_state_snapshot",
    "record_miniapp_state",
    "replay_miniapp_capture_records",
    "sanitize_miniapp_state",
]
