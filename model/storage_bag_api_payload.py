"""Pure payload parsing shared by the manual API UI and background readers."""

import json
import re

DEFAULT_STORAGE_BAG_ITEM_NAME_MAP = {
    "item_fishing_bait_plain": "凡饵",
    "item_fishing_bait_spirit_rice": "灵米饵",
    "item_fishing_bait_demon_blood": "妖血饵",
}


def parse_json_maybe(value):
    if isinstance(value, str):
        text = value.strip()
        if text and text[0] in "[{":
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def flatten_api_row(row):
    row = row if isinstance(row, dict) else {}
    flat = {}
    for key in (
        "user",
        "owner",
        "profile",
        "character",
        "cultivator",
        "role",
        "player",
        "status_info",
        "state",
    ):
        value = parse_json_maybe(row.get(key))
        if isinstance(value, dict):
            flat.update(value)
    flat.update(row)

    dongfu = parse_json_maybe(row.get("dongfu") or row.get("cave"))
    if isinstance(dongfu, dict):
        flat.setdefault("dongfu", dongfu)
    return flat


def storage_bag_api_candidate_from_value(value, *, normalize_suffix=False):
    candidate = str(value or "").strip().lstrip("@")
    if not candidate:
        return ""
    if normalize_suffix:
        candidate = re.sub(r"-\d{4,}$", "", candidate).strip()
    return candidate


def storage_bag_api_normalize_item_name(value):
    text = str(value or "").strip()
    return text.strip("[]【】")


def storage_bag_api_item_count(value):
    try:
        return int(str(value or 0).replace(",", "") or 0)
    except (TypeError, ValueError):
        return 0


def storage_bag_api_add_item(items, name, count):
    name = storage_bag_api_normalize_item_name(name)
    count = storage_bag_api_item_count(count)
    if not name or count <= 0:
        return
    items[name] = items.get(name, 0) + count


def storage_bag_api_resolve_item_name(item_name, item_name_map):
    item_name = storage_bag_api_normalize_item_name(item_name)
    return str((item_name_map or {}).get(item_name) or DEFAULT_STORAGE_BAG_ITEM_NAME_MAP.get(item_name) or item_name).strip()


def storage_bag_api_extract_items(raw_inventory, item_name_map=None):
    items = {}
    seen_inventory = False

    if isinstance(raw_inventory, list):
        seen_inventory = True
        for item in raw_inventory:
            if not isinstance(item, dict):
                continue
            storage_bag_api_add_item(
                items,
                item.get("name")
                or item.get("item_name")
                or item.get("display_name")
                or item.get("title")
                or storage_bag_api_resolve_item_name(item.get("item_id") or item.get("id"), item_name_map),
                item.get("quantity") or item.get("amount") or item.get("count") or item.get("num") or item.get("value"),
            )
        return items, seen_inventory

    if not isinstance(raw_inventory, dict):
        return items, seen_inventory

    seen_inventory = True
    for key in ("items", "current", "materials", "inventory", "storage", "bag", "snapshots"):
        value = raw_inventory.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    storage_bag_api_add_item(
                        items,
                        item.get("name")
                        or item.get("item_name")
                        or item.get("display_name")
                        or item.get("title")
                        or storage_bag_api_resolve_item_name(item.get("item_id") or item.get("id"), item_name_map),
                        item.get("quantity") or item.get("amount") or item.get("count") or item.get("num") or item.get("value"),
                    )
        elif isinstance(value, dict):
            if key in {"materials", "inventory", "storage", "bag"}:
                for item_name, amount in value.items():
                    if isinstance(amount, dict):
                        storage_bag_api_add_item(
                            items,
                            amount.get("name")
                            or amount.get("item_name")
                            or amount.get("display_name")
                            or amount.get("title")
                            or storage_bag_api_resolve_item_name(item_name, item_name_map),
                            amount.get("quantity") or amount.get("amount") or amount.get("count") or amount.get("num") or amount.get("value"),
                        )
                    else:
                        storage_bag_api_add_item(items, storage_bag_api_resolve_item_name(item_name, item_name_map), amount)
            elif key == "items":
                for item_name, amount in value.items():
                    storage_bag_api_add_item(items, storage_bag_api_resolve_item_name(item_name, item_name_map), amount)

    if not items:
        for item_name, amount in raw_inventory.items():
            if item_name in {"owner", "owner_username", "source", "event_time", "raw_message_id", "chat_id", "msg_id", "updated_at"}:
                continue
            if isinstance(amount, (int, float, str)):
                storage_bag_api_add_item(items, storage_bag_api_resolve_item_name(item_name, item_name_map), amount)
    return items, seen_inventory


def storage_bag_api_extract_owner_fields(row):
    if not isinstance(row, dict):
        return 0, ""
    identity_id = 0
    row = flatten_api_row(row)
    for key in (
        "identity_id",
        "send_as_id",
        "telegram_id",
        "telegram_user_id",
        "tg_id",
        "user_id",
        "character_id",
        "cultivator_id",
        "owner_id",
        "id",
    ):
        try:
            candidate = int(row.get(key) or 0)
        except (TypeError, ValueError):
            candidate = 0
        if candidate != 0:
            identity_id = candidate
            break
    owner_text = ""
    for key in ("owner", "owner_username", "username", "telegram_username", "dao_name", "daohao", "label", "role_name", "name"):
        value = str(row.get(key) or "").strip()
        if value:
            owner_text = value
            break
    return identity_id, owner_text
