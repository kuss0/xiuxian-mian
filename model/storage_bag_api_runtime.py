import time

from .storage_bag_api_payload import (
    DEFAULT_STORAGE_BAG_ITEM_NAME_MAP,
    parse_json_maybe as _parse_json_maybe,
    flatten_api_row as _flatten_api_row,
    storage_bag_api_candidate_from_value,
    storage_bag_api_normalize_item_name,
    storage_bag_api_item_count,
    storage_bag_api_add_item,
    storage_bag_api_resolve_item_name,
    storage_bag_api_extract_items,
    storage_bag_api_extract_owner_fields,
)
from .persistence import save_state
from .state import (
    get_identity_display_name,
    get_identity_ids,
    get_identity_ui_display_name,
    get_send_as_profile,
    get_storage_bag_api_config,
    get_storage_bag_records,
    set_storage_bag_api_config,
    set_storage_bag_records,
)
from .storage_bag_api_client import REFRESH_PATH, StorageBagApiError, build_cultivator_path, fetch_storage_bag_result
from .timing import fmt_abs_ts


def storage_bag_api_identity_lookup():
    lookup = {}
    for identity_id in get_identity_ids():
        identity_id = int(identity_id)
        profile = get_send_as_profile(identity_id)
        candidates = [
            str(identity_id),
            profile.get("username"),
            profile.get("label"),
            profile.get("daohao"),
            get_identity_ui_display_name(identity_id),
        ]
        candidates.extend(profile.get("username_aliases") or [])
        for candidate in candidates:
            key = str(candidate or "").strip().lstrip("@").casefold()
            if key:
                lookup[key] = identity_id
    return lookup


def storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup):
    try:
        identity_id = int(identity_id or 0)
    except (TypeError, ValueError):
        identity_id = 0
    local_ids = {int(item or 0) for item in get_identity_ids()}
    if identity_id in local_ids:
        return identity_id
    owner_key = str(owner_text or "").strip().lstrip("@").casefold()
    if owner_key:
        matched_id = int((lookup or {}).get(owner_key) or 0)
        if matched_id:
            return matched_id
    return identity_id


def storage_bag_api_cultivator_candidates(identity_id):
    profile = get_send_as_profile(identity_id)
    raw_candidates = [
        (profile.get("username"), False),
        *((alias, False) for alias in (profile.get("username_aliases") or [])),
        (profile.get("label"), True),
        (profile.get("daohao"), True),
        (get_identity_display_name(identity_id), True),
        (get_identity_ui_display_name(identity_id), True),
    ]
    candidates = []
    seen = set()
    for raw_value, normalize_suffix in raw_candidates:
        candidate = storage_bag_api_candidate_from_value(raw_value, normalize_suffix=normalize_suffix)
        if not candidate:
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def storage_bag_api_apply_payload(payload, *, fallback_identity_id=0, fallback_owner_text="", write_empty=False):
    payload = payload if isinstance(payload, dict) else {}
    if isinstance(payload.get("data"), dict):
        payload = payload.get("data") or {}
    lookup = storage_bag_api_identity_lookup()
    item_name_map = get_storage_bag_api_config().get("item_name_map") or {}
    records = dict(get_storage_bag_records())
    updated = 0
    changed = 0
    skipped = 0
    updated_identity_ids = set()
    now = time.time()

    def update_record(identity_id, owner_text, items, *, source="storage_bag_api", seen_inventory=True):
        nonlocal updated, changed, skipped
        identity_id = storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
        if identity_id == 0 and int(fallback_identity_id or 0):
            identity_id = int(fallback_identity_id or 0)
        if not owner_text:
            owner_text = fallback_owner_text
        local_ids = {int(item or 0) for item in get_identity_ids()}
        if identity_id == 0 or str(identity_id) not in records and identity_id not in local_ids:
            skipped += 1
            return
        if not items and not (write_empty and seen_inventory):
            skipped += 1
            return
        profile = get_send_as_profile(identity_id)
        previous = records.get(str(identity_id))
        previous_items = previous.get("items") if isinstance(previous, dict) else {}
        if dict(previous_items or {}) != dict(items):
            changed += 1
        records[str(identity_id)] = {
            "owner": owner_text or profile.get("username") or profile.get("label") or profile.get("daohao") or str(identity_id),
            "owner_username": profile.get("username") or "",
            "label": profile.get("label") or profile.get("username") or profile.get("daohao") or str(identity_id),
            "sections": {"API": dict(items)},
            "items": dict(items),
            "empty": not bool(items),
            "updated_at": float(now),
            "updated_at_text": fmt_abs_ts(now),
            "source": source,
        }
        updated += 1
        updated_identity_ids.add(int(identity_id))

    if isinstance(payload.get("current"), list):
        grouped = {}
        for row in payload.get("current") or []:
            if not isinstance(row, dict):
                continue
            identity_id, owner_text = storage_bag_api_extract_owner_fields(row)
            identity_id = storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
            key = identity_id or owner_text or "unknown"
            grouped.setdefault(key, {"identity_id": identity_id, "owner_text": owner_text, "items": {}})
            storage_bag_api_add_item(grouped[key]["items"], row.get("name") or row.get("item_name"), row.get("amount") or row.get("quantity") or row.get("count"))
        for row in grouped.values():
            update_record(row["identity_id"], row["owner_text"], row["items"], source="storage_bag_api_current")

    for snapshot in payload.get("snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        identity_id, owner_text = storage_bag_api_extract_owner_fields(snapshot)
        identity_id = storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
        items, seen_inventory = storage_bag_api_extract_items(
            snapshot.get("items") or snapshot.get("inventory") or snapshot.get("storage") or snapshot.get("bag") or snapshot,
            item_name_map,
        )
        update_record(identity_id, owner_text, items, source="storage_bag_api_snapshot", seen_inventory=seen_inventory)

    for character in payload.get("characters") or []:
        if not isinstance(character, dict):
            continue
        identity_id, owner_text = storage_bag_api_extract_owner_fields(character)
        identity_id = storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
        inventory = None
        for inventory_key in ("inventory", "storage_bag", "bag"):
            if inventory_key in character:
                inventory = character.get(inventory_key) or {}
                break
        if inventory is None:
            skipped += 1
            continue
        items, seen_inventory = storage_bag_api_extract_items(inventory, item_name_map)
        update_record(identity_id, owner_text, items, source="storage_bag_api_character", seen_inventory=seen_inventory)

    if payload.get("inventory") is not None or payload.get("storage_bag") is not None or payload.get("bag") is not None:
        identity_id, owner_text = storage_bag_api_extract_owner_fields(payload)
        identity_id = storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
        inventory = payload.get("inventory") or payload.get("storage_bag") or payload.get("bag") or {}
        items, seen_inventory = storage_bag_api_extract_items(inventory, item_name_map)
        update_record(identity_id, owner_text, items, source="storage_bag_api_cultivator", seen_inventory=seen_inventory)

    for key in ("storage_bag_records", "records"):
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        for owner_key, record in value.items():
            if not isinstance(record, dict):
                continue
            identity_id, owner_text = storage_bag_api_extract_owner_fields(record)
            identity_id = storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
            if identity_id == 0:
                identity_id = lookup.get(str(owner_key or "").strip().lstrip("@").casefold(), 0)
            items, seen_inventory = storage_bag_api_extract_items(record.get("items") or record.get("inventory") or record, item_name_map)
            update_record(identity_id, owner_text or str(owner_key or ""), items, source="storage_bag_api_records", seen_inventory=seen_inventory)

    if updated > 0:
        set_storage_bag_records(records)
        save_state()
    return {
        "updated_count": updated,
        "changed_count": changed,
        "skipped_count": skipped,
        "updated_identity_ids": sorted(updated_identity_ids),
        "records": records,
    }


def storage_bag_api_store_session(cookie="", api_token=""):
    current = get_storage_bag_api_config()
    set_storage_bag_api_config({
        **current,
        "cookie": str(cookie or current.get("cookie") or "").strip(),
        "api_token": str(api_token or current.get("api_token") or "").strip(),
    })
    save_state()
    return get_storage_bag_api_config()


def storage_bag_api_store_failure(exc, now):
    current = get_storage_bag_api_config()
    keepalive_enabled = bool(current.get("keepalive_enabled"))
    if isinstance(exc, StorageBagApiError) and exc.status_code == 401:
        keepalive_enabled = False
    set_storage_bag_api_config({
        **current,
        "cookie": getattr(exc, "cookie", "") or current.get("cookie") or "",
        "api_token": getattr(exc, "api_token", "") or current.get("api_token") or "",
        "keepalive_enabled": keepalive_enabled,
        "last_keepalive_at": float(now),
        "last_keepalive_ok": False,
        "last_keepalive_error": str(exc),
        "next_keepalive_at": float(now) + 10 * 60,
    })
    save_state()


def _format_storage_bag_api_refresh_message(total_updated, total_changed):
    total_updated = int(total_updated or 0)
    total_changed = int(total_changed or 0)
    if total_updated <= 0:
        return "API 已返回，但未匹配到可刷新身份"
    if total_changed > 0:
        return f"已刷新 {total_updated} 个身份的储物袋（内容变化 {total_changed} 个）"
    return f"已刷新 {total_updated} 个身份的储物袋（内容未变化）"


async def refresh_storage_bag_records_from_api(*, identity_ids=None, write_empty=False, fetch_func=None):
    config = get_storage_bag_api_config()
    if not config.get("cookie"):
        raise StorageBagApiError("请先配置天机阁 session Cookie")
    fetch = fetch_func or fetch_storage_bag_result
    local_ids = [int(identity_id or 0) for identity_id in get_identity_ids()]
    if identity_ids is None:
        target_ids = [identity_id for identity_id in local_ids if identity_id > 0]
    else:
        wanted = {int(identity_id or 0) for identity_id in identity_ids}
        target_ids = [identity_id for identity_id in local_ids if identity_id in wanted and identity_id > 0]

    active_config = dict(config)
    updated_identity_ids = set()
    total_updated = 0
    total_changed = 0
    total_skipped = 0

    me_result = await fetch(active_config, REFRESH_PATH)
    active_config = storage_bag_api_store_session(me_result.cookie, me_result.api_token)
    me_payload = me_result.payload
    if isinstance(me_payload, dict) and me_payload.get("ok") is False:
        raise StorageBagApiError(str(me_payload.get("error") or "储物袋 API 返回失败"))
    me_result_data = storage_bag_api_apply_payload(me_payload if isinstance(me_payload, dict) else {}, write_empty=write_empty)
    updated_identity_ids.update(me_result_data.get("updated_identity_ids") or [])
    total_updated += int(me_result_data.get("updated_count") or 0)
    total_changed += int(me_result_data.get("changed_count") or 0)
    total_skipped += int(me_result_data.get("skipped_count") or 0)

    for identity_id in target_ids:
        if identity_id <= 0 or identity_id in updated_identity_ids:
            continue
        candidates = storage_bag_api_cultivator_candidates(identity_id)
        if not candidates:
            total_skipped += 1
            continue
        candidate_success = False
        for candidate in candidates:
            try:
                api_result = await fetch(active_config, build_cultivator_path(candidate))
                active_config = storage_bag_api_store_session(api_result.cookie, api_result.api_token)
                api_payload = api_result.payload
                if isinstance(api_payload, dict) and api_payload.get("ok") is False:
                    raise StorageBagApiError(str(api_payload.get("error") or "储物袋 API 返回失败"))
                result = storage_bag_api_apply_payload(
                    api_payload if isinstance(api_payload, dict) else {},
                    fallback_identity_id=identity_id,
                    fallback_owner_text=candidate,
                    write_empty=write_empty,
                )
                total_updated += int(result.get("updated_count") or 0)
                total_changed += int(result.get("changed_count") or 0)
                total_skipped += int(result.get("skipped_count") or 0)
                updated_identity_ids.update(result.get("updated_identity_ids") or [])
                if int(result.get("updated_count") or 0) > 0:
                    candidate_success = True
                    break
            except StorageBagApiError as exc:
                storage_bag_api_store_session(exc.cookie, exc.api_token)
                active_config = get_storage_bag_api_config()
                if exc.auth_failed or exc.rate_limited:
                    raise
                if exc.status_code == 404:
                    continue
                continue
        if not candidate_success:
            total_skipped += 1

    return {
        "ok": total_updated > 0,
        "message": _format_storage_bag_api_refresh_message(total_updated, total_changed),
        "updated_count": int(total_updated),
        "changed_count": int(total_changed),
        "skipped_count": int(total_skipped),
        "updated_identity_ids": sorted(updated_identity_ids),
    }


__all__ = [
    "refresh_storage_bag_records_from_api",
    "storage_bag_api_apply_payload",
    "storage_bag_api_candidate_from_value",
    "storage_bag_api_cultivator_candidates",
    "storage_bag_api_extract_items",
    "storage_bag_api_extract_owner_fields",
    "storage_bag_api_identity_lookup",
    "storage_bag_api_resolve_identity_id",
    "storage_bag_api_store_failure",
    "storage_bag_api_store_session",
]
