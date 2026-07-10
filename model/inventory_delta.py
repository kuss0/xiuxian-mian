import hashlib
import json
import re
import time

from .persistence import save_state
from .state import get_inventory_delta_records, set_inventory_delta_records
from .timing import fmt_abs_ts


INVENTORY_DELTA_STATUS_PENDING = "pending_inventory_confirm"
INVENTORY_DELTA_STATUS_CONFIRMED = "confirmed_inventory_snapshot"
INVENTORY_DELTA_STATUS_SUPERSEDED = "superseded_by_inventory_snapshot"
INVENTORY_DELTA_RECORD_LIMIT = 300
INVENTORY_NON_ITEM_NAMES = {
    "修为",
    "经验",
    "贡献",
    "宗门贡献",
    "塔印",
    "信仰",
    "人口",
    "默契",
    "神识",
    "香火",
}
_SOURCE_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def stable_payload_digest(value, *, length=20):
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(value)
    digest = hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()
    return digest[: max(8, int(length or 20))]


def _safe_source_id(value, *, fallback_payload=None):
    text = str(value or "").strip()
    if text:
        safe = _SOURCE_ID_SAFE_RE.sub("_", text)[:120].strip("._:-")
        if safe:
            return safe
    return f"payload:{stable_payload_digest(fallback_payload or {})}"


def _record_key(identity_id, source, source_id):
    return stable_payload_digest([int(identity_id or 0), str(source or ""), str(source_id or "")], length=32)


def _parse_item_count(value, default=0):
    try:
        return int(float(str(value if value not in {None, ""} else default).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return int(default or 0)


def normalize_inventory_item_name(value):
    name = str(value or "").strip().strip("[]【】")
    return name if name and name not in INVENTORY_NON_ITEM_NAMES else ""


def _add_item(items, name, count):
    name = normalize_inventory_item_name(name)
    count = _parse_item_count(count, 0)
    if not name or count == 0:
        return
    items[name] = _parse_item_count(items.get(name), 0) + count
    if items[name] == 0:
        items.pop(name, None)


def normalize_inventory_items(value):
    items = {}
    if isinstance(value, list):
        for entry in value:
            if not isinstance(entry, dict):
                continue
            _add_item(
                items,
                entry.get("name")
                or entry.get("itemName")
                or entry.get("item_name")
                or entry.get("display_name")
                or entry.get("title")
                or entry.get("label"),
                entry.get("qty")
                or entry.get("quantity")
                or entry.get("amount")
                or entry.get("count")
                or entry.get("num")
                or entry.get("value")
                or 1,
            )
        return items
    if isinstance(value, dict):
        for name, count in value.items():
            if isinstance(count, dict):
                _add_item(
                    items,
                    count.get("name") or count.get("itemName") or count.get("item_name") or name,
                    count.get("qty")
                    or count.get("quantity")
                    or count.get("amount")
                    or count.get("count")
                    or count.get("num")
                    or count.get("value")
                    or 1,
                )
            else:
                _add_item(items, name, count)
    return items


def _compact_source_summary(summary):
    if not isinstance(summary, dict):
        return {}
    result = {}
    for key, value in summary.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (str, int, float, bool)):
            result[str(key)] = value
        elif isinstance(value, list):
            result[str(key)] = [
                item if isinstance(item, (str, int, float, bool)) else str(item)[:80]
                for item in value[:8]
            ]
        elif isinstance(value, dict):
            result[str(key)] = {
                str(child_key): child_value if isinstance(child_value, (str, int, float, bool)) else str(child_value)[:80]
                for child_key, child_value in list(value.items())[:8]
            }
    return result


def _prune_delta_records(records):
    rows = [
        (key, record)
        for key, record in (records or {}).items()
        if isinstance(record, dict) and str(key or "") != "_meta"
    ]
    rows.sort(key=lambda item: float(item[1].get("updated_at") or 0), reverse=True)
    pruned = {key: record for key, record in rows[:INVENTORY_DELTA_RECORD_LIMIT]}
    meta = records.get("_meta") if isinstance((records or {}).get("_meta"), dict) else {}
    pruned["_meta"] = {
        **meta,
        "updated_at": time.time(),
        "updated_at_text": fmt_abs_ts(time.time()),
        "record_limit": INVENTORY_DELTA_RECORD_LIMIT,
    }
    return pruned


def record_inventory_delta(
    identity_id,
    *,
    source,
    source_id="",
    items=None,
    status=INVENTORY_DELTA_STATUS_PENDING,
    now=None,
    source_summary=None,
):
    identity_id = int(identity_id or 0)
    source = str(source or "").strip()
    normalized_items = normalize_inventory_items(items or {})
    if identity_id <= 0 or not source or not normalized_items:
        return {"changed": False, "record": {}, "record_key": ""}
    now = time.time() if now is None else float(now)
    safe_source_id = _safe_source_id(source_id, fallback_payload={"source": source, "items": normalized_items})
    record_key = _record_key(identity_id, source, safe_source_id)
    records = dict(get_inventory_delta_records())
    previous = records.get(record_key) if isinstance(records.get(record_key), dict) else {}
    compact_summary = _compact_source_summary(source_summary)
    record = {
        "identity_id": identity_id,
        "source": source,
        "source_id": safe_source_id,
        "status": str(status or INVENTORY_DELTA_STATUS_PENDING).strip() or INVENTORY_DELTA_STATUS_PENDING,
        "items": dict(sorted(normalized_items.items())),
        "updated_at": float(now),
        "updated_at_text": fmt_abs_ts(now),
        "source_summary": compact_summary,
    }
    if previous:
        comparable_previous = {
            key: previous.get(key)
            for key in ("identity_id", "source", "source_id", "status", "items", "source_summary")
        }
        comparable_record = {
            key: record.get(key)
            for key in ("identity_id", "source", "source_id", "status", "items", "source_summary")
        }
        if comparable_previous == comparable_record:
            return {"changed": False, "record": previous, "record_key": record_key}
    records[record_key] = record
    set_inventory_delta_records(_prune_delta_records(records))
    save_state()
    return {"changed": True, "record": record, "record_key": record_key}


def _storage_record_items(record):
    items = record.get("items") if isinstance(record, dict) else {}
    return normalize_inventory_items(items if isinstance(items, dict) else {})


def _storage_record_updated_at(record):
    try:
        return float((record or {}).get("updated_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def _freshness_label(base_updated_at, pending_updated_at):
    if pending_updated_at > base_updated_at:
        return "delta_newer_than_snapshot"
    if base_updated_at > 0:
        return "storage_snapshot_current"
    return "delta_without_snapshot"


def build_inventory_freshness_snapshot(identity_ids, storage_records, *, delta_records=None, now=None):
    now = time.time() if now is None else float(now)
    storage_records = storage_records if isinstance(storage_records, dict) else {}
    delta_records = delta_records if isinstance(delta_records, dict) else get_inventory_delta_records()
    rows = []
    pending_record_count = 0
    stale_record_count = 0
    for identity_id in [int(item or 0) for item in identity_ids or []]:
        if identity_id <= 0:
            continue
        storage_record = storage_records.get(str(identity_id)) if isinstance(storage_records, dict) else {}
        storage_record = storage_record if isinstance(storage_record, dict) else {}
        base_items = _storage_record_items(storage_record)
        base_updated_at = _storage_record_updated_at(storage_record)
        pending_deltas = {}
        stale_deltas = {}
        latest_by_item = {}
        evidence = []
        for record_key, record in (delta_records or {}).items():
            if not isinstance(record, dict) or str(record_key or "") == "_meta":
                continue
            if int(record.get("identity_id") or 0) != identity_id:
                continue
            delta_items = normalize_inventory_items(record.get("items") if isinstance(record.get("items"), dict) else {})
            if not delta_items:
                continue
            updated_at = _storage_record_updated_at(record)
            status = str(record.get("status") or INVENTORY_DELTA_STATUS_PENDING)
            is_pending = status == INVENTORY_DELTA_STATUS_PENDING and updated_at > base_updated_at
            target = pending_deltas if is_pending else stale_deltas
            if is_pending:
                pending_record_count += 1
            else:
                stale_record_count += 1
            for item_name, delta in delta_items.items():
                target[item_name] = _parse_item_count(target.get(item_name), 0) + int(delta or 0)
                latest = latest_by_item.get(item_name)
                if is_pending and (not latest or updated_at >= float(latest.get("updated_at_raw") or 0)):
                    latest_by_item[item_name] = {
                        "source": record.get("source") or "",
                        "source_id": record.get("source_id") or "",
                        "updated_at_raw": updated_at,
                        "updated_at": fmt_abs_ts(updated_at),
                        "status": status,
                    }
            evidence.append({
                "record_key": str(record_key),
                "source": record.get("source") or "",
                "source_id": record.get("source_id") or "",
                "status": status if is_pending else INVENTORY_DELTA_STATUS_SUPERSEDED,
                "updated_at_raw": updated_at,
                "updated_at": fmt_abs_ts(updated_at),
                "age_sec": max(0, int(now - updated_at)) if updated_at > 0 else 0,
                "items": delta_items,
            })
        item_names = set(base_items) | set(pending_deltas)
        merged_items = {}
        for item_name in sorted(item_names):
            base_quantity = _parse_item_count(base_items.get(item_name), 0)
            pending_delta = _parse_item_count(pending_deltas.get(item_name), 0)
            latest = latest_by_item.get(item_name) or {}
            updated_at_raw = float(latest.get("updated_at_raw") or base_updated_at or 0)
            merged_items[item_name] = {
                "item_name": item_name,
                "quantity": max(0, base_quantity + pending_delta),
                "base_quantity": base_quantity,
                "pending_delta": pending_delta,
                "source": latest.get("source") or storage_record.get("source") or "storage_bag",
                "freshness_source": latest.get("source") or storage_record.get("source") or "storage_bag",
                "freshness": _freshness_label(base_updated_at, updated_at_raw),
                "status": latest.get("status") or "confirmed_snapshot",
                "updated_at_raw": updated_at_raw,
                "updated_at": fmt_abs_ts(updated_at_raw),
                "age_sec": max(0, int(now - updated_at_raw)) if updated_at_raw > 0 else 0,
            }
        evidence.sort(key=lambda item: float(item.get("updated_at_raw") or 0), reverse=True)
        rows.append({
            "identity_id": identity_id,
            "base_updated_at_raw": base_updated_at,
            "base_updated_at": fmt_abs_ts(base_updated_at),
            "pending_deltas": {name: count for name, count in sorted(pending_deltas.items()) if count},
            "stale_deltas": {name: count for name, count in sorted(stale_deltas.items()) if count},
            "merged_items": merged_items,
            "evidence": evidence[:12],
            "pending_record_count": sum(1 for item in evidence if item.get("status") == INVENTORY_DELTA_STATUS_PENDING),
            "stale_record_count": sum(1 for item in evidence if item.get("status") == INVENTORY_DELTA_STATUS_SUPERSEDED),
        })
    return {
        "rows": rows,
        "pending_record_count": pending_record_count,
        "stale_record_count": stale_record_count,
        "record_count": len([1 for key, value in (delta_records or {}).items() if str(key) != "_meta" and isinstance(value, dict)]),
    }


__all__ = [
    "INVENTORY_DELTA_STATUS_CONFIRMED",
    "INVENTORY_DELTA_STATUS_PENDING",
    "INVENTORY_DELTA_STATUS_SUPERSEDED",
    "build_inventory_freshness_snapshot",
    "normalize_inventory_item_name",
    "normalize_inventory_items",
    "record_inventory_delta",
    "stable_payload_digest",
]
