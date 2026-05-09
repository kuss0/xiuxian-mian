import re

from ..persistence import save_state
from ..state import get_identity_ids, get_send_as_profile, get_storage_bag_records, set_storage_bag_records
from ..timing import fmt_abs_ts

CMD_STORAGE_BAG = ".储物袋"
RE_STORAGE_BAG_TITLE = re.compile(r"^@?(.+?)\s+的储物袋\s*$")
RE_STORAGE_BAG_ITEM = re.compile(r"^-\s*(.+?)\s*[x×]\s*([\d,]+)(?:\s+.*)?$")
STORAGE_BAG_SECTION_NAMES = ("法宝/丹药/杂物", "材料")


def _normalize_owner_key(value):
    return str(value or "").strip().lstrip("@").casefold()


def _normalize_username(value):
    username = str(value or "").strip()
    if not username:
        return ""
    return username if username.startswith("@") else f"@{username}"


def _build_identity_lookup():
    lookup = {}
    for identity_id in get_identity_ids():
        profile = get_send_as_profile(identity_id)
        for candidate in (profile.get("username"), profile.get("label"), profile.get("daohao")):
            key = _normalize_owner_key(candidate)
            if key:
                lookup[key] = int(identity_id)
    return lookup


def resolve_storage_bag_identity_id(owner_text):
    owner = str(owner_text or "").strip()
    if not owner:
        return 0
    lookup = _build_identity_lookup()
    candidates = [owner]
    if owner.startswith("@") and " " in owner:
        candidates.append(owner.split()[0])
    for candidate in candidates:
        identity_id = lookup.get(_normalize_owner_key(candidate))
        if identity_id:
            return identity_id
    return 0


def parse_storage_bag_reply(text):
    lines = str(text or "").splitlines()
    title_index = None
    owner = ""
    for index, line in enumerate(lines):
        match = RE_STORAGE_BAG_TITLE.match(line.strip())
        if match:
            title_index = index
            owner = match.group(1).strip()
            break
    if title_index is None:
        return None

    sections = {}
    current_section = ""
    is_empty = False
    for raw_line in lines[title_index + 1:]:
        line = raw_line.strip()
        if not line:
            continue
        if line == "空空如也，一贫如洗。":
            is_empty = True
            continue
        if line.endswith(":"):
            section_name = line[:-1].strip()
            current_section = section_name if section_name in STORAGE_BAG_SECTION_NAMES else ""
            if current_section:
                sections.setdefault(current_section, {})
            continue
        item_match = RE_STORAGE_BAG_ITEM.match(line)
        if item_match and current_section:
            item_name = item_match.group(1).strip()
            item_count = int(item_match.group(2).replace(",", "") or 0)
            if item_name:
                section_items = sections.setdefault(current_section, {})
                section_items[item_name] = section_items.get(item_name, 0) + item_count

    items = {}
    for section_items in sections.values():
        for item_name, item_count in section_items.items():
            items[item_name] = items.get(item_name, 0) + int(item_count or 0)

    owner_username = owner.split()[0] if owner.startswith("@") else owner
    return {
        "owner": owner,
        "owner_username": _normalize_username(owner_username),
        "sections": sections,
        "items": items,
        "empty": bool(is_empty and not items),
    }


def _get_storage_bag_identity_label(identity_id, parsed):
    if identity_id:
        profile = get_send_as_profile(identity_id)
        return profile.get("label") or profile.get("username") or profile.get("daohao") or str(identity_id)
    return parsed.get("owner_username") or parsed.get("owner") or "未知账号"


async def handle_storage_bag_reply(text, now, reply_to=None, matched_family=None):
    parsed = parse_storage_bag_reply(text)
    if not parsed:
        return False
    identity_id = resolve_storage_bag_identity_id(parsed.get("owner"))
    if identity_id <= 0:
        return False
    records = get_storage_bag_records()
    records[str(identity_id)] = {
        "identity_id": identity_id,
        "label": _get_storage_bag_identity_label(identity_id, parsed),
        "owner": parsed.get("owner") or "",
        "owner_username": parsed.get("owner_username") or "",
        "updated_at": float(now or 0),
        "updated_at_text": fmt_abs_ts(float(now or 0)),
        "items": parsed.get("items") or {},
        "sections": parsed.get("sections") or {},
        "empty": bool(parsed.get("empty")),
    }
    set_storage_bag_records(records)
    save_state()
    return True


__all__ = [
    "CMD_STORAGE_BAG",
    "handle_storage_bag_reply",
    "parse_storage_bag_reply",
    "resolve_storage_bag_identity_id",
]
