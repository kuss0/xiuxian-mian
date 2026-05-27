import random
import re
import time
import uuid

from ..persistence import save_state
from ..runtime import _get_identity_client, send_audit_log, send_game_command
from ..state import get_game_group_id, get_identity_ids, get_send_as_profile, get_storage_bag_item_rules, get_storage_bag_records, is_auto_delete_sent_messages_enabled, set_storage_bag_item_rules, set_storage_bag_records
from ..timing import fmt_abs_ts

CMD_STORAGE_BAG = ".储物袋"
CMD_STORAGE_BAG_LISTING = ".上架"
CMD_STORAGE_BAG_BUY = ".购买"
CMD_STORAGE_BAG_GIFT = ".赠送"
RE_STORAGE_BAG_TITLE = re.compile(r"^@?(.+?)\s+的储物袋\s*$")
RE_STORAGE_BAG_ITEM = re.compile(r"^-\s*(.+?)\s*[x×]\s*([\d,]+)(?:\s+.*)?$")
RE_STORAGE_BRACKET_ITEM_COUNT = re.compile(r"【([^】]+)】\s*[x×]\s*([\d,]+)")
RE_STORAGE_PLAIN_ITEM_COUNT = re.compile(r"(?<![\w])([^\s,，、:：；;()（）【】]+)\s*[x×]\s*([\d,]+)")
RE_STORAGE_TRANSFER_LISTING_SUCCESS = re.compile(
    r"^上架成功！\n你已将 【(?P<item>.+?)】x(?P<count>\d+) 上架至万宝楼。\n(?P<price_label>每件售价|捆绑总价): (?P<price>.+)\n挂单ID: (?P<id>\d+)"
)
RE_STORAGE_TRANSFER_BRACKET_ITEM = re.compile(r"【([^】]+)】")
RE_STORAGE_TRANSFER_GIFT_RESULT = re.compile(r"赠送了 【(?P<item>.+?)】x(?P<count>[\d,]+)")
RE_STORAGE_TRANSFER_GIFT_TAX = re.compile(r"额外支付了\s*(?P<tax>[\d,]+)\s*灵石")
STORAGE_BAG_SECTION_NAMES = ("法宝/丹药/杂物", "材料")
STORAGE_TRANSFER_REPLY_TIMEOUT_SEC = 180
STORAGE_TRANSFER_WAITING_PREFIX = "正在思考，请稍等"
STORAGE_TRANSFER_BLOCKED_KEYWORDS = ("此物不可交易", "【天道禁制】", "不可作为万宝楼交易货币流通", "🚫 操作禁止")
STORAGE_TRANSFER_NON_RULE_FAILURE_KEYWORDS = ("价格格式错误", "数量不足", "严重偏离天道估值")
STORAGE_TRANSFER_GIFT_SUCCESS_PREFIX = "【赠送成功】"
STORAGE_TRANSFER_LOCATOR_MESSAGES = ("稍等", "我看下", "转一下", "放这", "这边", "好了")
STORAGE_TRANSFER_GIFT_INTERVAL_SEC = 20
STORAGE_TRANSFER_EXEC_METHODS = {"basic", "gift", "unknown"}
STORAGE_BAG_NON_ITEM_NAMES = {
    "修为",
    "宗门贡献",
    "贡献",
    "经验",
    "塔印",
    "信仰",
    "人口",
    "默契",
    "神识",
    "香火",
}

_storage_bag_transfer_state = {
    "running": False,
    "op_id": "",
    "source_identity_id": 0,
    "target_identity_id": 0,
    "items": [],
    "basic_items": [],
    "gift_items": [],
    "listing_item": "",
    "listing_command": "",
    "listing_msg_id": 0,
    "listing_id": "",
    "buy_command": "",
    "buy_msg_id": 0,
    "gift_index": 0,
    "gift_locator_command": "",
    "gift_locator_msg_id": 0,
    "gift_locator_deleted": False,
    "gift_locator_delete_error": "",
    "gift_command": "",
    "gift_msg_id": 0,
    "gift_item": "",
    "gift_next_due_at": 0,
    "step": "idle",
    "logs": [],
    "last_error": "",
    "created_at": 0,
    "updated_at": 0,
    "reply_due_at": 0,
}


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


def _normalize_storage_item_name(item_name):
    return str(item_name or "").strip().strip("[]【】")


def _storage_item_count_value(raw_count):
    try:
        return int(str(raw_count or "0").replace(",", "") or 0)
    except (TypeError, ValueError):
        return 0


def _add_storage_item_count(items, item_name, count):
    item_name = _normalize_storage_item_name(item_name)
    count = int(count or 0)
    if not item_name or count <= 0 or item_name in STORAGE_BAG_NON_ITEM_NAMES:
        return
    items[item_name] = items.get(item_name, 0) + count


def parse_storage_bag_item_counts(text, *, allow_plain=False):
    raw_text = str(text or "")
    items = {}
    for match in RE_STORAGE_BRACKET_ITEM_COUNT.finditer(raw_text):
        _add_storage_item_count(items, match.group(1), _storage_item_count_value(match.group(2)))
    if allow_plain:
        for match in RE_STORAGE_PLAIN_ITEM_COUNT.finditer(raw_text):
            _add_storage_item_count(items, match.group(1), _storage_item_count_value(match.group(2)))
    return items


def apply_storage_bag_item_text_delta(identity_id, text, *, sign=1, allow_plain=False):
    item_counts = parse_storage_bag_item_counts(text, allow_plain=allow_plain)
    if not item_counts:
        return False
    multiplier = -1 if int(sign or 0) < 0 else 1
    return apply_storage_bag_item_deltas(identity_id, {item_name: multiplier * count for item_name, count in item_counts.items()})


def _get_storage_bag_identity_label(identity_id, parsed):
    if identity_id:
        profile = get_send_as_profile(identity_id)
        return profile.get("label") or profile.get("username") or profile.get("daohao") or str(identity_id)
    return parsed.get("owner_username") or parsed.get("owner") or "未知账号"


def is_storage_transfer_waiting_reply(text):
    return str(text or "").strip().startswith(STORAGE_TRANSFER_WAITING_PREFIX)


def _storage_transfer_log(message, *, level="info"):
    entry = {"ts": fmt_abs_ts(time.time()), "level": str(level or "info"), "message": str(message or "")}
    _storage_bag_transfer_state.setdefault("logs", []).append(entry)
    if len(_storage_bag_transfer_state["logs"]) > 80:
        _storage_bag_transfer_state["logs"] = _storage_bag_transfer_state["logs"][-80:]
    _storage_bag_transfer_state["updated_at"] = time.time()
    return entry


def get_storage_bag_transfer_snapshot():
    snapshot = dict(_storage_bag_transfer_state)
    snapshot["logs"] = list(_storage_bag_transfer_state.get("logs") or [])
    snapshot["items"] = [dict(item) for item in _storage_bag_transfer_state.get("items") or []]
    snapshot["basic_items"] = [dict(item) for item in _storage_bag_transfer_state.get("basic_items") or []]
    snapshot["gift_items"] = [dict(item) for item in _storage_bag_transfer_state.get("gift_items") or []]
    snapshot["created_at_text"] = fmt_abs_ts(float(snapshot.get("created_at") or 0))
    snapshot["updated_at_text"] = fmt_abs_ts(float(snapshot.get("updated_at") or 0))
    snapshot["reply_due_at_text"] = fmt_abs_ts(float(snapshot.get("reply_due_at") or 0))
    snapshot["gift_next_due_at_text"] = fmt_abs_ts(float(snapshot.get("gift_next_due_at") or 0))
    return snapshot


def _clear_storage_bag_transfer_state():
    _storage_bag_transfer_state.update({
        "running": False,
        "op_id": "",
        "source_identity_id": 0,
        "target_identity_id": 0,
        "items": [],
        "basic_items": [],
        "gift_items": [],
        "listing_item": "",
        "listing_command": "",
        "listing_msg_id": 0,
        "listing_id": "",
        "buy_command": "",
        "buy_msg_id": 0,
        "gift_index": 0,
        "gift_locator_command": "",
        "gift_locator_msg_id": 0,
        "gift_locator_deleted": False,
        "gift_locator_delete_error": "",
        "gift_command": "",
        "gift_msg_id": 0,
        "gift_item": "",
        "gift_next_due_at": 0,
        "step": "idle",
        "logs": [],
        "last_error": "",
        "created_at": 0,
        "updated_at": 0,
        "reply_due_at": 0,
    })


def _finalize_storage_bag_transfer(success, message):
    _storage_bag_transfer_state["running"] = False
    _storage_bag_transfer_state["step"] = "done" if success else "failed"
    _storage_bag_transfer_state["last_error"] = "" if success else str(message or "")
    _storage_bag_transfer_state["reply_due_at"] = 0
    _storage_bag_transfer_state["gift_next_due_at"] = 0
    _storage_transfer_log(message, level="success" if success else "error")


def _set_storage_bag_rule_method(item_name, method, reason=""):
    item_name = str(item_name or "").strip()
    method = str(method or "unknown").strip().lower()
    if not item_name or method not in {"basic", "gift", "blocked", "unknown"}:
        return False
    rules = dict(get_storage_bag_item_rules())
    previous = rules.get(item_name) if isinstance(rules.get(item_name), dict) else {}
    tags = previous.get("tags") if isinstance(previous.get("tags"), list) and previous.get("tags") else ["未知"]
    rules[item_name] = {
        **previous,
        "method": method,
        "tags": tags,
        "reason": str(reason or previous.get("reason") or "").strip(),
        "updated_at": time.time(),
    }
    set_storage_bag_item_rules(rules)
    save_state()
    return True


def _adjust_storage_bag_section_item(sections, item_name, new_value):
    item_name = str(item_name or "").strip()
    new_value = int(new_value or 0)
    if not item_name:
        return False
    for section_items in sections.values():
        if isinstance(section_items, dict) and item_name in section_items:
            if new_value > 0:
                section_items[item_name] = new_value
            else:
                section_items.pop(item_name, None)
            return True
    if new_value > 0:
        section_items = sections.setdefault("材料", {})
        section_items[item_name] = new_value
        return True
    return False


def _adjust_storage_bag_identity_item(records, identity_id, item_name, delta):
    identity_id = int(identity_id or 0)
    item_name = str(item_name or "").strip()
    delta = int(delta or 0)
    if identity_id <= 0 or not item_name or delta == 0:
        return False
    key = str(identity_id)
    record = records.setdefault(
        key,
        {
            "identity_id": identity_id,
            "label": _get_storage_bag_identity_label(identity_id, {}),
            "owner": "",
            "owner_username": "",
            "updated_at": 0,
            "updated_at_text": "",
            "items": {},
            "sections": {},
            "empty": False,
        },
    )
    items = record.setdefault("items", {})
    sections = record.setdefault("sections", {})
    old_value = int(items.get(item_name, 0) or 0)
    new_value = max(0, old_value + delta)
    if new_value == old_value:
        return False
    if new_value > 0:
        items[item_name] = new_value
    else:
        items.pop(item_name, None)
    _adjust_storage_bag_section_item(sections, item_name, new_value)
    record["empty"] = not bool(items)
    record["updated_at"] = time.time()
    record["updated_at_text"] = fmt_abs_ts(record["updated_at"])
    return True


def apply_storage_bag_item_deltas(identity_id, item_deltas):
    identity_id = int(identity_id or 0)
    if identity_id <= 0 or not isinstance(item_deltas, dict):
        return False
    records = get_storage_bag_records()
    changed = False
    for item_name, delta in item_deltas.items():
        changed = _adjust_storage_bag_identity_item(records, identity_id, item_name, delta) or changed
    if changed:
        set_storage_bag_records(records)
        save_state()
    return changed


def _storage_transfer_item_names_for_rule_update(raw_text="", *, fallback_all=True):
    listing_item = str(_storage_bag_transfer_state.get("listing_item") or "").strip()
    names = {listing_item}
    for item in _storage_bag_transfer_state.get("items") or []:
        names.add(str((item or {}).get("item_name") or "").strip())
    names.discard("")
    mentioned_names = {name for name in RE_STORAGE_TRANSFER_BRACKET_ITEM.findall(str(raw_text or "")) if name in names}
    if mentioned_names:
        return mentioned_names
    if fallback_all:
        return names
    return {listing_item} if listing_item else set()


def _storage_transfer_apply_item_move(item_name, quantity, *, extra_source_costs=None):
    item_name = str(item_name or "").strip()
    quantity = int(quantity or 0)
    if not item_name or quantity <= 0:
        return False
    source_id = int(_storage_bag_transfer_state.get("source_identity_id", 0) or 0)
    target_id = int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0)
    source_deltas = {item_name: -quantity}
    for cost_name, cost_quantity in (extra_source_costs or {}).items():
        source_deltas[cost_name] = source_deltas.get(cost_name, 0) - int(cost_quantity or 0)
    source_changed = apply_storage_bag_item_deltas(source_id, source_deltas)
    target_changed = apply_storage_bag_item_deltas(target_id, {item_name: quantity})
    return source_changed or target_changed


def _storage_transfer_apply_basic_items_move():
    changed_count = 0
    for item in _storage_bag_transfer_state.get("basic_items") or []:
        if _storage_transfer_apply_item_move(item.get("item_name"), int(item.get("quantity") or 0)):
            changed_count += 1
    listing_item = str(_storage_bag_transfer_state.get("listing_item") or "").strip()
    target_id = int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0)
    source_id = int(_storage_bag_transfer_state.get("source_identity_id", 0) or 0)
    if listing_item and apply_storage_bag_item_deltas(target_id, {listing_item: -1}):
        apply_storage_bag_item_deltas(source_id, {listing_item: 1})
    return changed_count


def _is_storage_bag_reply_to_transfer(reply_to, *, msg_id_key, command_prefix, reply_to_msg_id=0):
    expected_msg_id = int(_storage_bag_transfer_state.get(msg_id_key, 0) or 0)
    reply_msg_id = int(reply_to_msg_id or getattr(reply_to, "id", 0) or 0)
    if expected_msg_id > 0:
        return reply_msg_id == expected_msg_id
    raw_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return raw_cmd == command_prefix or raw_cmd.startswith(f"{command_prefix} ")


def _parse_listing_success(raw_text):
    match = RE_STORAGE_TRANSFER_LISTING_SUCCESS.match(str(raw_text or "").strip())
    if not match:
        return None
    return {"item": match.group("item"), "count": int(match.group("count") or 0), "price": match.group("price"), "id": match.group("id")}


def _parse_storage_transfer_gift_result(raw_text):
    match = RE_STORAGE_TRANSFER_GIFT_RESULT.search(str(raw_text or ""))
    if not match:
        return None
    tax_match = RE_STORAGE_TRANSFER_GIFT_TAX.search(str(raw_text or ""))
    return {
        "item_name": match.group("item").strip(),
        "quantity": int((match.group("count") or "0").replace(",", "") or 0),
        "tax": int((tax_match.group("tax") or "0").replace(",", "") or 0) if tax_match else 0,
    }


def _current_storage_transfer_gift_item():
    gift_items = _storage_bag_transfer_state.get("gift_items") or []
    gift_index = int(_storage_bag_transfer_state.get("gift_index", 0) or 0)
    if 0 <= gift_index < len(gift_items):
        item = gift_items[gift_index] or {}
        return {
            "item_name": str(item.get("item_name") or "").strip(),
            "quantity": int(item.get("quantity") or 0),
        }
    return {
        "item_name": str(_storage_bag_transfer_state.get("gift_item") or "").strip(),
        "quantity": 0,
    }


def _normalize_storage_transfer_task_item(raw_item):
    if not isinstance(raw_item, dict):
        return None, "转移物品格式无效"
    item_name = str(raw_item.get("item_name") or "").strip()
    if not item_name:
        return None, "物品名不能为空"
    try:
        quantity = int(raw_item.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0
    if quantity <= 0:
        return None, f"{item_name} 数量必须大于 0"
    method = str(raw_item.get("method") or "unknown").strip().lower() or "unknown"
    if method == "blocked":
        return None, f"{item_name} 不可转移"
    if method not in STORAGE_TRANSFER_EXEC_METHODS:
        return None, f"{item_name} 转移方式无效"
    item = dict(raw_item)
    item["item_name"] = item_name
    item["quantity"] = quantity
    item["method"] = method
    return item, ""


async def _delete_storage_bag_gift_locator():
    msg_id = int(_storage_bag_transfer_state.get("gift_locator_msg_id", 0) or 0)
    if msg_id <= 0 or _storage_bag_transfer_state.get("gift_locator_deleted"):
        return True
    if not is_auto_delete_sent_messages_enabled():
        _storage_transfer_log("自动删除消息未开启，保留赠送定位消息")
        return True
    try:
        client = _get_identity_client(int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0))
        await client.delete_messages(get_game_group_id(), [msg_id])
    except Exception as exc:
        error = str(exc)
        _storage_bag_transfer_state["gift_locator_delete_error"] = error
        _storage_transfer_log(f"赠送定位消息删除失败：{error}", level="warning")
        await send_audit_log("⚠️ 储物袋定位删除失败。", limit=240)
        return False
    _storage_bag_transfer_state["gift_locator_deleted"] = True
    _storage_bag_transfer_state["gift_locator_delete_error"] = ""
    _storage_transfer_log("已删除赠送定位消息")
    return True


async def _send_next_storage_bag_gift():
    gift_items = [dict(item) for item in _storage_bag_transfer_state.get("gift_items") or []]
    index = int(_storage_bag_transfer_state.get("gift_index", 0) or 0)
    if index >= len(gift_items):
        await _delete_storage_bag_gift_locator()
        message = "储物袋转移完成：购买和赠送成功" if _storage_bag_transfer_state.get("basic_items") else "储物袋转移完成：赠送成功"
        _finalize_storage_bag_transfer(True, message)
        await send_audit_log(f"✅ {message}", limit=220)
        return True, message

    item = gift_items[index]
    item_name = str(item.get("item_name") or "").strip()
    quantity = int(item.get("quantity") or 0)
    command = f"{CMD_STORAGE_BAG_GIFT} {item_name} {quantity}"
    _storage_bag_transfer_state.update({
        "gift_command": command,
        "gift_msg_id": 0,
        "gift_item": item_name,
        "gift_next_due_at": 0,
        "step": "gift_sending",
        "reply_due_at": time.time() + STORAGE_TRANSFER_REPLY_TIMEOUT_SEC,
    })
    _storage_transfer_log(f"来源身份回复定位消息发送赠送命令：{command}")
    msg = await send_game_command(
        command,
        track=False,
        reply_to=int(_storage_bag_transfer_state.get("gift_locator_msg_id", 0) or 0),
        send_as_id=int(_storage_bag_transfer_state.get("source_identity_id", 0) or 0),
        priority="normal",
    )
    if not msg:
        await _delete_storage_bag_gift_locator()
        message = f"赠送命令发送失败：{item_name}"
        _finalize_storage_bag_transfer(False, message)
        await send_audit_log(f"❌ 储物袋赠送发送失败：{item_name}", limit=240)
        return False, message
    _storage_bag_transfer_state["gift_msg_id"] = int(getattr(msg, "id", 0) or 0)
    _storage_bag_transfer_state["step"] = "waiting_gift_reply"
    _storage_bag_transfer_state["reply_due_at"] = time.time() + STORAGE_TRANSFER_REPLY_TIMEOUT_SEC
    _storage_transfer_log(f"已发送赠送命令，等待结果（消息ID={_storage_bag_transfer_state['gift_msg_id']}）")
    return True, "已发送赠送命令，等待结果"


async def _start_storage_bag_gift_phase():
    gift_items = _storage_bag_transfer_state.get("gift_items") or []
    if not gift_items:
        message = "储物袋转移完成：购买成功"
        _finalize_storage_bag_transfer(True, message)
        await send_audit_log(f"✅ {message}", limit=220)
        return True, message
    locator = random.choice(STORAGE_TRANSFER_LOCATOR_MESSAGES)
    _storage_bag_transfer_state.update({
        "gift_index": 0,
        "gift_locator_command": locator,
        "gift_locator_msg_id": 0,
        "gift_locator_deleted": False,
        "gift_locator_delete_error": "",
        "gift_next_due_at": 0,
        "step": "gift_marker",
        "reply_due_at": time.time() + STORAGE_TRANSFER_REPLY_TIMEOUT_SEC,
    })
    _storage_transfer_log(f"目标身份发送赠送定位消息：{locator}")
    msg = await send_game_command(locator, track=False, send_as_id=int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0), priority="normal")
    if not msg:
        message = "赠送定位消息发送失败"
        _finalize_storage_bag_transfer(False, message)
        await send_audit_log("❌ 储物袋定位发送失败。", limit=220)
        return False, message
    _storage_bag_transfer_state["gift_locator_msg_id"] = int(getattr(msg, "id", 0) or 0)
    _storage_transfer_log(f"已发送赠送定位消息（消息ID={_storage_bag_transfer_state['gift_locator_msg_id']}）")
    return await _send_next_storage_bag_gift()


async def start_storage_bag_transfer_task(source_identity_id, target_identity_id, items, listing_item):
    if _storage_bag_transfer_state.get("running"):
        return False, "已有储物袋转移任务正在执行", get_storage_bag_transfer_snapshot()
    try:
        source_identity_id = int(source_identity_id or 0)
        target_identity_id = int(target_identity_id or 0)
    except (TypeError, ValueError):
        return False, "身份参数无效", None
    known_ids = {int(identity_id) for identity_id in get_identity_ids()}
    if source_identity_id not in known_ids:
        return False, "来源身份无效", None
    if target_identity_id not in known_ids:
        return False, "目标身份无效", None
    if source_identity_id == target_identity_id:
        return False, "来源和目标身份不能相同", None

    normalized_items = []
    for raw_item in (items if isinstance(items, (list, tuple)) else []):
        item, error = _normalize_storage_transfer_task_item(raw_item)
        if error:
            return False, error, None
        normalized_items.append(item)
    if not normalized_items:
        return False, "请至少选择一个转移物品", None
    basic_items = [item for item in normalized_items if str(item.get("method") or "unknown") != "gift"]
    gift_items = [item for item in normalized_items if str(item.get("method") or "unknown") == "gift"]
    if not basic_items and not gift_items:
        return False, "当前没有可执行的转移物品", None
    listing_item = str(listing_item or "").strip()
    listing_command = ""
    if basic_items:
        if not listing_item:
            return False, "请选择目标身份用于上架的物品", None
        exchange_parts = [f"{item['item_name']}*{int(item['quantity'])}" for item in basic_items]
        listing_command = f"{CMD_STORAGE_BAG_LISTING} {listing_item} 1 换 {' '.join(exchange_parts)}"
    now = time.time()
    _clear_storage_bag_transfer_state()
    _storage_bag_transfer_state.update({
        "running": True,
        "op_id": uuid.uuid4().hex[:12],
        "source_identity_id": int(source_identity_id),
        "target_identity_id": int(target_identity_id),
        "items": normalized_items,
        "basic_items": basic_items,
        "gift_items": gift_items,
        "listing_item": listing_item,
        "listing_command": listing_command,
        "step": "listing" if basic_items else "gift_marker",
        "created_at": now,
        "updated_at": now,
        "reply_due_at": now + STORAGE_TRANSFER_REPLY_TIMEOUT_SEC,
    })
    if not basic_items:
        ok, message = await _start_storage_bag_gift_phase()
        return ok, message, get_storage_bag_transfer_snapshot()
    _storage_transfer_log(f"目标身份发送上架命令：{listing_command}")
    msg = await send_game_command(listing_command, track=False, send_as_id=int(target_identity_id), priority="normal")
    if not msg:
        _finalize_storage_bag_transfer(False, "上架命令发送失败")
        return False, "上架命令发送失败", get_storage_bag_transfer_snapshot()
    _storage_bag_transfer_state["listing_msg_id"] = int(getattr(msg, "id", 0) or 0)
    _storage_bag_transfer_state["step"] = "waiting_listing_reply"
    _storage_bag_transfer_state["reply_due_at"] = time.time() + STORAGE_TRANSFER_REPLY_TIMEOUT_SEC
    _storage_transfer_log(f"已发送上架命令，等待挂单结果（消息ID={_storage_bag_transfer_state['listing_msg_id']}）")
    return True, "已开始储物袋转移，等待上架结果", get_storage_bag_transfer_snapshot()


async def cancel_storage_bag_transfer_task():
    if not _storage_bag_transfer_state.get("running"):
        return False, "当前没有进行中的转移任务", get_storage_bag_transfer_snapshot()
    step = str(_storage_bag_transfer_state.get("step") or "")
    if step in {"waiting_listing_reply", "waiting_buy_reply", "waiting_gift_reply"}:
        return False, "命令已发送，不能安全取消；请等待回复或超时", get_storage_bag_transfer_snapshot()
    await _delete_storage_bag_gift_locator()
    _finalize_storage_bag_transfer(False, "用户取消转移任务")
    return True, "已取消转移任务", get_storage_bag_transfer_snapshot()


async def _handle_storage_bag_listing_reply(raw_text):
    if is_storage_transfer_waiting_reply(raw_text):
        _storage_transfer_log("上架命令正在处理，等待最终回复")
        return False
    success = _parse_listing_success(raw_text)
    if success:
        _storage_bag_transfer_state["listing_id"] = str(success["id"])
        for item_name in _storage_transfer_item_names_for_rule_update(raw_text):
            rule = get_storage_bag_item_rules().get(item_name)
            if not isinstance(rule, dict) or str(rule.get("method") or "unknown") == "unknown":
                _set_storage_bag_rule_method(item_name, "basic")
        buy_command = f"{CMD_STORAGE_BAG_BUY} {success['id']}"
        _storage_bag_transfer_state["buy_command"] = buy_command
        _storage_bag_transfer_state["step"] = "buying"
        _storage_bag_transfer_state["reply_due_at"] = time.time() + STORAGE_TRANSFER_REPLY_TIMEOUT_SEC
        _storage_transfer_log(f"上架成功，挂单ID={success['id']}，来源身份准备购买")
        msg = await send_game_command(buy_command, track=False, send_as_id=int(_storage_bag_transfer_state["source_identity_id"]), priority="normal")
        if not msg:
            _finalize_storage_bag_transfer(False, "购买命令发送失败")
            await send_audit_log("❌ 储物袋购买发送失败。", limit=220)
            return True
        _storage_bag_transfer_state["buy_msg_id"] = int(getattr(msg, "id", 0) or 0)
        _storage_bag_transfer_state["step"] = "waiting_buy_reply"
        _storage_bag_transfer_state["reply_due_at"] = time.time() + STORAGE_TRANSFER_REPLY_TIMEOUT_SEC
        _storage_transfer_log(f"已发送购买命令：{buy_command}（消息ID={_storage_bag_transfer_state['buy_msg_id']}）")
        return True
    reason = raw_text.splitlines()[0].strip() if raw_text.splitlines() else raw_text[:80]
    blocked = any(keyword in raw_text for keyword in STORAGE_TRANSFER_BLOCKED_KEYWORDS)
    non_rule_failure = any(keyword in raw_text for keyword in STORAGE_TRANSFER_NON_RULE_FAILURE_KEYWORDS)
    if blocked and not non_rule_failure:
        for item_name in _storage_transfer_item_names_for_rule_update(raw_text, fallback_all=False):
            rule = get_storage_bag_item_rules().get(item_name)
            if not isinstance(rule, dict) or str(rule.get("method") or "unknown") == "unknown":
                _set_storage_bag_rule_method(item_name, "blocked", reason=reason)
    _finalize_storage_bag_transfer(False, f"上架失败：{reason}")
    await send_audit_log("❌ 储物袋上架失败。", limit=260)
    return True


async def _handle_storage_bag_buy_reply(raw_text):
    if is_storage_transfer_waiting_reply(raw_text):
        _storage_transfer_log("购买命令正在处理，等待最终回复")
        return False
    if raw_text.startswith("交易成功！") or "你成功购得" in raw_text:
        moved_count = _storage_transfer_apply_basic_items_move()
        if moved_count:
            _storage_transfer_log(f"已同步本地储物袋数据：买卖转移 {moved_count} 项")
        if _storage_bag_transfer_state.get("gift_items"):
            _storage_transfer_log("购买成功，准备执行赠送物品")
            await _start_storage_bag_gift_phase()
            return True
        _finalize_storage_bag_transfer(True, "储物袋转移完成：购买成功")
        await send_audit_log("✅ 储物袋转移完成：购买成功", limit=220)
        return True
    reason = raw_text.splitlines()[0].strip() if raw_text.splitlines() else raw_text[:80]
    _finalize_storage_bag_transfer(False, f"购买失败：{reason}")
    await send_audit_log("❌ 储物袋购买失败。", limit=260)
    return True


async def _handle_storage_bag_gift_reply(raw_text):
    if is_storage_transfer_waiting_reply(raw_text):
        _storage_transfer_log("赠送命令正在处理，等待最终回复")
        return False
    expected_gift = _current_storage_transfer_gift_item()
    gift_item = str(expected_gift.get("item_name") or _storage_bag_transfer_state.get("gift_item") or "").strip()
    gift_quantity = int(expected_gift.get("quantity") or 0)
    if raw_text.startswith(STORAGE_TRANSFER_GIFT_SUCCESS_PREFIX):
        result = _parse_storage_transfer_gift_result(raw_text)
        if not result:
            await _delete_storage_bag_gift_locator()
            message = f"赠送结果无法识别：{gift_item}"
            _finalize_storage_bag_transfer(False, message)
            await send_audit_log(f"❌ 储物袋赠送结果无法识别：{gift_item}", limit=260)
            return True
        moved_item = str(result.get("item_name") or "").strip()
        moved_quantity = int(result.get("quantity") or 0)
        if moved_item != gift_item or moved_quantity != gift_quantity:
            await _delete_storage_bag_gift_locator()
            message = f"赠送结果不匹配：期望 {gift_item} x{gift_quantity}，实际 {moved_item or '未知'} x{moved_quantity}"
            _finalize_storage_bag_transfer(False, message)
            await send_audit_log(f"❌ 储物袋赠送结果不匹配：{gift_item}", limit=260)
            return True
        source_costs = {"灵石": int(result.get("tax") or 0)} if int(result.get("tax") or 0) > 0 else None
        if _storage_transfer_apply_item_move(moved_item, moved_quantity, extra_source_costs=source_costs):
            _storage_transfer_log(f"已同步本地储物袋数据：赠送 {moved_item} x{moved_quantity}")
        next_index = int(_storage_bag_transfer_state.get("gift_index", 0) or 0) + 1
        _storage_bag_transfer_state["gift_index"] = next_index
        if next_index < len(_storage_bag_transfer_state.get("gift_items") or []):
            next_due_at = time.time() + STORAGE_TRANSFER_GIFT_INTERVAL_SEC
            _storage_bag_transfer_state["step"] = "gift_waiting_interval"
            _storage_bag_transfer_state["gift_next_due_at"] = next_due_at
            _storage_bag_transfer_state["reply_due_at"] = next_due_at + STORAGE_TRANSFER_REPLY_TIMEOUT_SEC
            _storage_transfer_log(f"等待 {STORAGE_TRANSFER_GIFT_INTERVAL_SEC} 秒后继续赠送下一件物品")
            return True
        await _send_next_storage_bag_gift()
        return True
    reason = raw_text.splitlines()[0].strip() if raw_text.splitlines() else raw_text[:80]
    await _delete_storage_bag_gift_locator()
    prefix = "储物袋转移部分完成：" if _storage_bag_transfer_state.get("basic_items") else ""
    message = f"{prefix}赠送失败：{gift_item}：{reason}"
    _finalize_storage_bag_transfer(False, message)
    await send_audit_log(f"❌ 储物袋赠送失败：{gift_item}", limit=260)
    return True


async def handle_storage_bag_transfer_reply(text, now, reply_to=None, matched_family=None, reply_context=None):
    if not _storage_bag_transfer_state.get("running"):
        return False
    raw_text = str(text or "").strip()
    if not raw_text:
        return False
    reply_to_msg_id = int((reply_context or {}).get("reply_to_msg_id") or 0)
    step = str(_storage_bag_transfer_state.get("step") or "")
    if step == "waiting_listing_reply":
        if not _is_storage_bag_reply_to_transfer(reply_to, msg_id_key="listing_msg_id", command_prefix=CMD_STORAGE_BAG_LISTING, reply_to_msg_id=reply_to_msg_id):
            return False
        return await _handle_storage_bag_listing_reply(raw_text)
    if step == "waiting_buy_reply":
        if not _is_storage_bag_reply_to_transfer(reply_to, msg_id_key="buy_msg_id", command_prefix=CMD_STORAGE_BAG_BUY, reply_to_msg_id=reply_to_msg_id):
            return False
        return await _handle_storage_bag_buy_reply(raw_text)
    if step == "waiting_gift_reply":
        if not _is_storage_bag_reply_to_transfer(reply_to, msg_id_key="gift_msg_id", command_prefix=CMD_STORAGE_BAG_GIFT, reply_to_msg_id=reply_to_msg_id):
            return False
        return await _handle_storage_bag_gift_reply(raw_text)
    return False


async def run_storage_bag_transfer_scheduler(now):
    if not _storage_bag_transfer_state.get("running"):
        return
    reply_due_at = float(_storage_bag_transfer_state.get("reply_due_at", 0) or 0)
    step = str(_storage_bag_transfer_state.get("step") or "")
    if step == "gift_waiting_interval":
        next_due_at = float(_storage_bag_transfer_state.get("gift_next_due_at", 0) or 0)
        if reply_due_at <= 0:
            return
        if now < next_due_at or (next_due_at <= 0 and now < reply_due_at):
            return
        if now < reply_due_at and next_due_at > 0:
            return await _send_next_storage_bag_gift()
    elif reply_due_at <= 0 or now < reply_due_at:
        return
    if step in {"gift_marker", "gift_sending", "gift_waiting_interval", "waiting_gift_reply"}:
        await _delete_storage_bag_gift_locator()
    _finalize_storage_bag_transfer(False, f"储物袋转移等待回复超时：{step}")
    await send_audit_log(f"⚠️ 储物袋转移超时：{step}", limit=240)


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
    "CMD_STORAGE_BAG_BUY",
    "CMD_STORAGE_BAG_GIFT",
    "CMD_STORAGE_BAG_LISTING",
    "apply_storage_bag_item_deltas",
    "apply_storage_bag_item_text_delta",
    "cancel_storage_bag_transfer_task",
    "get_storage_bag_transfer_snapshot",
    "handle_storage_bag_reply",
    "handle_storage_bag_transfer_reply",
    "is_storage_transfer_waiting_reply",
    "parse_storage_bag_item_counts",
    "parse_storage_bag_reply",
    "resolve_storage_bag_identity_id",
    "run_storage_bag_transfer_scheduler",
    "start_storage_bag_transfer_task",
]
