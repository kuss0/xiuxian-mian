import json
import os
import random
import re
import time
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from ..config import MESSAGES_DIR, TZ_LOCAL
from ..message_log_recovery import find_message_log_replies
from ..persistence import save_state
from ..runtime import _get_identity_client_with_account as _runtime_get_identity_client_with_account
from ..runtime import _run_account_rpc, get_last_game_send_block, get_sent_message_chat_id, send_audit_log, send_game_command
from ..state import get_game_group_id, get_game_topic_id, get_identity_ids, get_send_as_profile, get_storage_bag_item_rules, get_storage_bag_records, is_auto_delete_sent_messages_enabled, set_storage_bag_item_rules, set_storage_bag_records
from ..timing import fmt_abs_ts
from . import workflow_log

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
RE_STORAGE_BAG_GIFT_SUCCESS = re.compile(
    r"道友\s*(?P<source>@\S+)\s*向\s*(?P<target>@\S+)\s*赠送了\s*【(?P<item>.+?)】x(?P<count>[\d,]+)"
)
RE_STORAGE_TRANSFER_GIFT_TAX = re.compile(r"额外支付了\s*(?P<tax>[\d,]+)\s*灵石")
STORAGE_BAG_SECTION_NAMES = ("法宝/丹药/杂物", "材料")
STORAGE_TRANSFER_REPLY_TIMEOUT_SEC = 20
STORAGE_TRANSFER_RETRY_INTERVAL_SEC = 5
STORAGE_TRANSFER_LISTING_REPLY_TIMEOUT_SEC = 60
STORAGE_TRANSFER_LISTING_RETRY_INTERVAL_SEC = 20
STORAGE_TRANSFER_MAX_RETRY = 3
STORAGE_TRANSFER_LISTING_REPEAT_GAP_SEC = 62
STORAGE_TRANSFER_MODULE_NAME = "储物袋"
STORAGE_TRANSFER_WAITING_PREFIX = "正在思考，请稍等"
STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX = "compact"
STORAGE_TRANSFER_BLOCKED_KEYWORDS = ("此物不可交易", "【天道禁制】", "🚫 操作禁止")
STORAGE_TRANSFER_GIFT_FALLBACK_KEYWORDS = ("不可作为万宝楼交易货币流通", "禁止其作为交易货币流通")
STORAGE_TRANSFER_NON_RULE_FAILURE_KEYWORDS = ("价格格式错误", "数量不足", "严重偏离天道估值")
STORAGE_TRANSFER_GIFT_SUCCESS_PREFIX = "【赠送成功】"
STORAGE_TRANSFER_LOCATOR_MESSAGES = ("稍等", "我看下", "转一下", "放这", "这边", "好了")
STORAGE_TRANSFER_GIFT_ANCHOR_LOOKBACK_SEC = 5 * 60
STORAGE_TRANSFER_GIFT_INTERVAL_SEC = 20
STORAGE_TRANSFER_EXEC_METHODS = {"basic", "gift", "unknown"}
STORAGE_TRANSFER_LISTING_SYNTAXES = {"space", "compact"}
STORAGE_TRANSFER_SEND_BLOCK_DEFER_CODES = {
    "send_timeout",
    "send_exception",
    "send_queue_timeout",
    "send_prepare_timeout",
    "global_disabled",
    "dungeon_quiet",
    "account_offline",
    "account_unbound",
    "account_client_missing",
    "account_client_not_ready",
    "account_session_error",
    "bot_health",
    "identity_weak",
    "pre_send_guard",
    "action_guard",
}
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
    "operation": "transfer",
    "op_id": "",
    "source_identity_id": 0,
    "target_identity_id": 0,
    "items": [],
    "basic_items": [],
    "gift_items": [],
    "listing_item": "",
    "listing_count": 1,
    "listing_syntax": STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
    "listing_command": "",
    "listing_msg_id": 0,
    "listing_msg_ids": [],
    "listing_id": "",
    "buy_command": "",
    "buy_msg_id": 0,
    "aggregate_buyers": [],
    "aggregate_buy_index": 0,
    "aggregate_listing_count": 0,
    "gift_index": 0,
    "gift_locator_command": "",
    "gift_locator_msg_id": 0,
    "gift_locator_chat_id": 0,
    "gift_locator_reused": False,
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
    "retry_count": 0,
    "retry_command": "",
    "retry_identity_id": 0,
    "retry_reply_to": 0,
    "retry_msg_id_key": "",
    "retry_wait_step": "",
    "retry_family": "",
    "retry_last_at": 0,
    "task_key": "",
    "listing_safe_due_at": 0,
}

_storage_bag_transfer_batch_state = {
    "running": False,
    "operation": "transfer",
    "batch_id": "",
    "target_identity_id": 0,
    "listing_item": "",
    "listing_count": 1,
    "listing_syntax": STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
    "queue": [],
    "active_task": None,
    "completed": [],
    "failed": [],
    "total": 0,
    "stop_on_error": True,
    "status": "idle",
    "last_message": "",
    "logs": [],
    "created_at": 0,
    "updated_at": 0,
    "next_task_due_at": 0,
    "waiting_task_key": "",
}

_storage_bag_recent_listing_sends = {}


def _get_identity_client(identity_id=None):
    _account_id, client = _runtime_get_identity_client_with_account(identity_id)
    return client


def _get_identity_client_for_rpc(identity_id=None):
    client = _get_identity_client(identity_id)
    if client is None:
        return 0, None
    try:
        account_id, runtime_client = _runtime_get_identity_client_with_account(identity_id)
        if runtime_client is client:
            return int(account_id or 0), client
    except Exception:
        pass
    return 0, client


def _storage_bag_operation_label(operation=None):
    operation = str(operation or _storage_bag_transfer_batch_state.get("operation") or "transfer").strip().lower()
    return "赠送" if operation == "gift" else "转移"


def _normalize_owner_key(value):
    return str(value or "").strip().lstrip("@").casefold()


def normalize_storage_bag_listing_count(value, default=1):
    try:
        count = int(value if value not in {None, ""} else default)
    except (TypeError, ValueError):
        count = int(default or 1)
    return max(1, count)


def normalize_storage_bag_listing_syntax(value):
    syntax = str(value or STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX).strip().lower()
    return syntax if syntax in STORAGE_TRANSFER_LISTING_SYNTAXES else STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX


def format_storage_bag_listing_command(listing_item, listing_count, exchange_parts, *, listing_syntax=STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX):
    listing_item = str(listing_item or "").strip()
    listing_count = normalize_storage_bag_listing_count(listing_count)
    syntax = normalize_storage_bag_listing_syntax(listing_syntax)
    exchange_text = " ".join(str(part or "").strip() for part in (exchange_parts or []) if str(part or "").strip())
    if syntax == "compact":
        listing_text = f"{listing_item}*{listing_count}"
    else:
        listing_text = f"{listing_item} {listing_count}"
    return f"{CMD_STORAGE_BAG_LISTING} {listing_text} 换 {exchange_text}".strip()


def _storage_transfer_items_key(items):
    normalized = []
    for raw_item in items if isinstance(items, (list, tuple)) else []:
        if not isinstance(raw_item, dict):
            continue
        item_name = str(raw_item.get("item_name") or "").strip()
        method = str(raw_item.get("method") or "unknown").strip().lower() or "unknown"
        try:
            quantity = int(raw_item.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if item_name and quantity > 0:
            normalized.append((item_name, quantity, method))
    return sorted(normalized)


def _storage_transfer_listing_command_for_items(items, listing_item, listing_count=1, listing_syntax=STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX):
    basic_items = [
        item for item in items if isinstance(item, dict) and str(item.get("method") or "unknown") != "gift"
    ]
    if not basic_items:
        return ""
    exchange_parts = [f"{item['item_name']}*{int(item['quantity'])}" for item in basic_items]
    return format_storage_bag_listing_command(
        listing_item,
        listing_count,
        exchange_parts,
        listing_syntax=listing_syntax,
    )


def _storage_transfer_task_key(
    *,
    source_identity_id,
    target_identity_id,
    items,
    listing_item="",
    listing_count=1,
    listing_syntax=STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
    operation="transfer",
):
    payload = [
        "gift" if str(operation or "").strip().lower() == "gift" else "transfer",
        int(source_identity_id or 0),
        int(target_identity_id or 0),
        normalize_storage_bag_listing_count(listing_count),
        normalize_storage_bag_listing_syntax(listing_syntax),
        str(listing_item or "").strip(),
        _storage_transfer_items_key(items),
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _storage_transfer_task_key_from_task(task):
    task = task if isinstance(task, dict) else {}
    return str(task.get("task_key") or _storage_transfer_task_key(
        source_identity_id=task.get("source_identity_id"),
        target_identity_id=task.get("target_identity_id"),
        items=task.get("items") or [],
        listing_item=task.get("listing_item") or "",
        listing_count=task.get("listing_count") or 1,
        listing_syntax=task.get("listing_syntax") or STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
        operation=task.get("operation") or "transfer",
    ))


def _storage_transfer_active_task_keys():
    keys = set()
    if _storage_bag_transfer_state.get("running"):
        task_key = str(_storage_bag_transfer_state.get("task_key") or "").strip()
        if task_key:
            keys.add(task_key)
    active_task = _storage_bag_transfer_batch_state.get("active_task")
    if isinstance(active_task, dict) and active_task:
        keys.add(_storage_transfer_task_key_from_task(active_task))
    for task in _storage_bag_transfer_batch_state.get("queue") or []:
        if isinstance(task, dict):
            keys.add(_storage_transfer_task_key_from_task(task))
    return keys


def _storage_listing_repeat_key(identity_id, command):
    return int(identity_id or 0), str(command or "").strip()


def _storage_listing_initial_wait_sec(identity_id, command, now=None):
    command = str(command or "").strip()
    identity_id = int(identity_id or 0)
    if identity_id <= 0 or not command:
        return 0.0
    now = time.time() if now is None else float(now)
    last_at = float(_storage_bag_recent_listing_sends.get(_storage_listing_repeat_key(identity_id, command), 0) or 0)
    if last_at <= 0:
        return 0.0
    return max(0.0, last_at + STORAGE_TRANSFER_LISTING_REPEAT_GAP_SEC - now)


def _mark_storage_listing_initial_sent(identity_id, command, now=None):
    command = str(command or "").strip()
    identity_id = int(identity_id or 0)
    if identity_id <= 0 or not command:
        return
    _storage_bag_recent_listing_sends[_storage_listing_repeat_key(identity_id, command)] = time.time() if now is None else float(now)


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
    op_id = str(_storage_bag_transfer_state.get("op_id") or "").strip()
    if op_id:
        workflow_log.append_workflow_event(
            "storage_bag_transfer",
            op_id=op_id,
            step=str(_storage_bag_transfer_state.get("step") or ""),
            event=str(message or ""),
            status=str(level or "info"),
            identity_id=int(
                _storage_bag_transfer_state.get("source_identity_id")
                or _storage_bag_transfer_state.get("target_identity_id")
                or 0
            ),
            state_after=str(_storage_bag_transfer_state.get("step") or ""),
        )
    return entry


def _record_storage_transfer_event(
    event,
    *,
    kind="changed",
    identity_id=0,
    reason="",
    family="",
    listing_id="",
    command="",
    msg_id=0,
    reply_msg_id=0,
    step="",
    detail="",
    matched_text="",
    decision="",
    route_source="storage_bag_transfer",
):
    try:
        if not family and command:
            command_text = str(command or "").strip()
            if command_text == CMD_STORAGE_BAG or command_text.startswith(f"{CMD_STORAGE_BAG} "):
                family = "storage_bag"
            elif command_text == CMD_STORAGE_BAG_LISTING or command_text.startswith(f"{CMD_STORAGE_BAG_LISTING} "):
                family = "storage_bag_listing"
            elif command_text == CMD_STORAGE_BAG_BUY or command_text.startswith(f"{CMD_STORAGE_BAG_BUY} "):
                family = "storage_bag_buy"
            elif command_text == CMD_STORAGE_BAG_GIFT or command_text.startswith(f"{CMD_STORAGE_BAG_GIFT} "):
                family = "storage_bag_gift"
        op_id = str(_storage_bag_transfer_state.get("op_id") or "").strip()
        parts = []
        if op_id:
            parts.append(f"op={op_id}")
        parts.append(str(event or "事件").strip() or "事件")
        if listing_id:
            parts.append(f"挂单ID={listing_id}")
        if msg_id:
            parts.append(f"msg_id={int(msg_id)}")
        if reply_msg_id:
            parts.append(f"reply_msg_id={int(reply_msg_id)}")
        if step:
            parts.append(f"step={step}")
        if command:
            parts.append(str(command).strip())
        if detail:
            parts.append(str(detail).strip())
        workflow_identity_id = int(identity_id or _storage_bag_transfer_state.get("source_identity_id") or 0)
        workflow_step = step or str(_storage_bag_transfer_state.get("step") or "")
        workflow_detail = {
            "listing_id": str(listing_id or ""),
            "reason": str(reason or ""),
            "detail": str(detail or ""),
        }
        workflow_log.append_workflow_event(
            "storage_bag_transfer",
            op_id=op_id,
            step=workflow_step,
            event=str(event or "事件").strip() or "事件",
            status=kind,
            identity_id=workflow_identity_id,
            msg_id=msg_id,
            reply_to_msg_id=reply_msg_id,
            family=family,
            command=command,
            text=matched_text,
            decision=decision or str(event or "").strip(),
            detail=workflow_detail,
            route_source=route_source,
            state_after=workflow_step,
        )
        from . import passive_inbox

        return passive_inbox.record_passive_inbox_event(
            kind,
            module="storage_bag_transfer",
            identity_id=workflow_identity_id,
            reason=reason,
            summary="｜".join(part for part in parts if part),
            family=family,
            msg_id=msg_id,
            reply_to_msg_id=reply_msg_id,
            route_source=route_source,
            matched_text=matched_text,
            decision=decision or str(event or "").strip(),
            state_after=step or str(_storage_bag_transfer_state.get("step") or ""),
            command=command,
        )
    except Exception:
        return False


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
    snapshot["listing_safe_due_at_text"] = fmt_abs_ts(float(snapshot.get("listing_safe_due_at") or 0))
    batch = dict(_storage_bag_transfer_batch_state)
    batch["queue"] = [dict(task) for task in _storage_bag_transfer_batch_state.get("queue") or []]
    batch["active_task"] = dict(_storage_bag_transfer_batch_state.get("active_task") or {}) or None
    batch["completed"] = [dict(task) for task in _storage_bag_transfer_batch_state.get("completed") or []]
    batch["failed"] = [dict(task) for task in _storage_bag_transfer_batch_state.get("failed") or []]
    batch["logs"] = list(_storage_bag_transfer_batch_state.get("logs") or [])
    batch["created_at_text"] = fmt_abs_ts(float(batch.get("created_at") or 0))
    batch["updated_at_text"] = fmt_abs_ts(float(batch.get("updated_at") or 0))
    batch["next_task_due_at_text"] = fmt_abs_ts(float(batch.get("next_task_due_at") or 0))
    snapshot["batch"] = batch
    return snapshot


def _clear_storage_bag_transfer_state():
    _storage_bag_transfer_state.update({
        "running": False,
        "operation": "transfer",
        "op_id": "",
        "source_identity_id": 0,
        "target_identity_id": 0,
        "items": [],
        "basic_items": [],
        "gift_items": [],
        "listing_item": "",
        "listing_count": 1,
        "listing_syntax": STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
        "listing_command": "",
        "listing_msg_id": 0,
        "listing_msg_ids": [],
        "listing_id": "",
        "buy_command": "",
        "buy_msg_id": 0,
        "aggregate_buyers": [],
        "aggregate_buy_index": 0,
        "aggregate_listing_count": 0,
        "gift_index": 0,
        "gift_locator_command": "",
        "gift_locator_msg_id": 0,
        "gift_locator_chat_id": 0,
        "gift_locator_reused": False,
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
        "retry_count": 0,
        "retry_command": "",
        "retry_identity_id": 0,
        "retry_reply_to": 0,
        "retry_msg_id_key": "",
        "retry_wait_step": "",
        "retry_family": "",
        "retry_last_at": 0,
        "task_key": "",
        "listing_safe_due_at": 0,
    })


def _clear_storage_bag_transfer_batch_state():
    _storage_bag_transfer_batch_state.update({
        "running": False,
        "operation": "transfer",
        "batch_id": "",
        "target_identity_id": 0,
        "listing_item": "",
        "listing_count": 1,
        "listing_syntax": STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
        "queue": [],
        "active_task": None,
        "completed": [],
        "failed": [],
        "total": 0,
        "stop_on_error": True,
        "status": "idle",
        "last_message": "",
        "logs": [],
        "created_at": 0,
        "updated_at": 0,
        "next_task_due_at": 0,
        "waiting_task_key": "",
    })


def _storage_transfer_batch_log(message, *, level="info"):
    entry = {"ts": fmt_abs_ts(time.time()), "level": str(level or "info"), "message": str(message or "")}
    _storage_bag_transfer_batch_state.setdefault("logs", []).append(entry)
    if len(_storage_bag_transfer_batch_state["logs"]) > 80:
        _storage_bag_transfer_batch_state["logs"] = _storage_bag_transfer_batch_state["logs"][-80:]
    _storage_bag_transfer_batch_state["updated_at"] = time.time()
    return entry


def _finalize_storage_bag_transfer_batch(success, message):
    _storage_bag_transfer_batch_state["running"] = False
    _storage_bag_transfer_batch_state["status"] = "done" if success else "failed"
    _storage_bag_transfer_batch_state["last_message"] = str(message or "")
    _storage_bag_transfer_batch_state["updated_at"] = time.time()
    _storage_transfer_batch_log(message, level="success" if success else "error")


async def _maybe_advance_storage_bag_transfer_batch(success, message, *, op_id=""):
    if not _storage_bag_transfer_batch_state.get("running"):
        return False
    active_task = _storage_bag_transfer_batch_state.get("active_task")
    if not isinstance(active_task, dict):
        if _storage_bag_transfer_batch_state.get("queue") and not _storage_bag_transfer_state.get("running"):
            return await _start_next_storage_bag_transfer_batch_task()
        return False
    active_op_id = str(active_task.get("op_id") or "")
    completed_op_id = str(op_id or "")
    if completed_op_id and active_op_id and completed_op_id != active_op_id:
        return False
    task_record = dict(active_task)
    task_record["message"] = str(message or "")
    task_record["finished_at"] = time.time()
    if success:
        _storage_bag_transfer_batch_state.setdefault("completed", []).append(task_record)
        _storage_transfer_batch_log(
            f"完成 {task_record.get('source_label') or task_record.get('source_identity_id')}：{message}",
            level="success",
        )
    else:
        _storage_bag_transfer_batch_state.setdefault("failed", []).append(task_record)
        _storage_transfer_batch_log(
            f"失败 {task_record.get('source_label') or task_record.get('source_identity_id')}：{message}",
            level="error",
        )
    _storage_bag_transfer_batch_state["active_task"] = None
    if not success and _storage_bag_transfer_batch_state.get("stop_on_error", True):
        _storage_bag_transfer_batch_state["queue"] = []
        op_label = _storage_bag_operation_label()
        _finalize_storage_bag_transfer_batch(False, f"批量{op_label}停止：{message}")
        await send_audit_log(f"❌ 储物袋批量{op_label}停止：{message}", limit=260)
        return True
    if not _storage_bag_transfer_batch_state.get("queue"):
        total = int(_storage_bag_transfer_batch_state.get("total") or 0)
        failed = len(_storage_bag_transfer_batch_state.get("failed") or [])
        completed = len(_storage_bag_transfer_batch_state.get("completed") or [])
        op_label = _storage_bag_operation_label()
        if failed:
            final_message = f"批量{op_label}结束：完成 {completed}/{total}，失败 {failed}"
            _finalize_storage_bag_transfer_batch(False, final_message)
            await send_audit_log(f"⚠️ {final_message}", limit=260)
        else:
            final_message = f"批量{op_label}完成：{completed}/{total}"
            _finalize_storage_bag_transfer_batch(True, final_message)
            await send_audit_log(f"✅ {final_message}", limit=220)
        return True
    return await _start_next_storage_bag_transfer_batch_task()


def _finalize_storage_bag_transfer(success, message, *, advance_batch=True):
    completed_op_id = str(_storage_bag_transfer_state.get("op_id") or "")
    _storage_bag_transfer_state["running"] = False
    _storage_bag_transfer_state["step"] = "done" if success else "failed"
    _storage_bag_transfer_state["last_error"] = "" if success else str(message or "")
    _storage_bag_transfer_state["reply_due_at"] = 0
    _storage_bag_transfer_state["gift_next_due_at"] = 0
    _storage_bag_transfer_state["retry_command"] = ""
    _storage_bag_transfer_state["retry_identity_id"] = 0
    _storage_bag_transfer_state["retry_reply_to"] = 0
    _storage_bag_transfer_state["retry_msg_id_key"] = ""
    _storage_bag_transfer_state["retry_wait_step"] = ""
    _storage_bag_transfer_state["retry_family"] = ""
    _storage_bag_transfer_state["retry_last_at"] = 0
    _storage_transfer_log(message, level="success" if success else "error")
    if advance_batch:
        try:
            from ..runtime import _fire_and_forget
            _fire_and_forget(_maybe_advance_storage_bag_transfer_batch(bool(success), str(message or ""), op_id=completed_op_id))
        except Exception:
            pass


def _storage_transfer_chain_id():
    op_id = str(_storage_bag_transfer_state.get("op_id") or "").strip()
    return f"storage_bag:{op_id or 'manual'}"


def _storage_transfer_note_waiting_reply(label):
    step = str(_storage_bag_transfer_state.get("step") or "")
    _storage_bag_transfer_state["reply_due_at"] = time.time() + _storage_transfer_reply_timeout_sec(step, retry=False)
    _storage_transfer_log(f"{label}命令正在处理，等待最终回复")


def _storage_transfer_reply_timeout_sec(wait_step, *, retry=False):
    step = str(wait_step or "")
    if step == "waiting_listing_reply":
        return STORAGE_TRANSFER_LISTING_RETRY_INTERVAL_SEC if retry else STORAGE_TRANSFER_LISTING_REPLY_TIMEOUT_SEC
    return STORAGE_TRANSFER_RETRY_INTERVAL_SEC if retry else STORAGE_TRANSFER_REPLY_TIMEOUT_SEC


def _storage_transfer_deferred_send_result(code, reason):
    return SimpleNamespace(storage_transfer_send_deferred=True, code=str(code or ""), reason=str(reason or ""))


def _is_storage_transfer_deferred_send(msg):
    return bool(getattr(msg, "storage_transfer_send_deferred", False))


def _storage_transfer_send_block(identity_id, command):
    block = get_last_game_send_block(identity_id, command)
    code = str((block or {}).get("code") or "")
    if not code:
        return "", ""
    return code, str((block or {}).get("reason") or "")


def _storage_transfer_defer_label(code, reason):
    code = str(code or "runtime_block")
    reason = str(reason or "").strip()
    return f"{code}: {reason}" if reason else code


def _remember_storage_transfer_msg_id(msg_id_key, msg_id):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return
    if msg_id_key == "listing_msg_id":
        msg_ids = list(_storage_bag_transfer_state.get("listing_msg_ids") or [])
        if msg_id not in msg_ids:
            msg_ids.append(msg_id)
        _storage_bag_transfer_state["listing_msg_ids"] = msg_ids


def _storage_transfer_listing_msg_ids():
    msg_ids = set()
    for raw_msg_id in (_storage_bag_transfer_state.get("listing_msg_ids") or []):
        try:
            msg_id = int(raw_msg_id or 0)
        except (TypeError, ValueError):
            continue
        if msg_id > 0:
            msg_ids.add(msg_id)
    return msg_ids


async def _send_storage_bag_transfer_command(
    command,
    *,
    identity_id,
    msg_id_key,
    wait_step,
    family,
    reply_to=0,
    retry=False,
):
    command = str(command or "").strip()
    identity_id = int(identity_id or 0)
    reply_to = int(reply_to or 0)
    if not command or identity_id <= 0 or not msg_id_key:
        return None
    if not retry:
        _storage_bag_transfer_state["retry_count"] = 0
    retry_count = int(_storage_bag_transfer_state.get("retry_count") or 0)
    priority = "retry" if retry else "chain"
    op_suffix = f"{family or 'command'}:{'retry' if retry else 'send'}:{retry_count}"
    kwargs = {
        "track": False,
        "send_as_id": identity_id,
        "priority": priority,
        "max_retry": 0,
        "source_module": STORAGE_TRANSFER_MODULE_NAME,
        "op_id": f"{_storage_transfer_chain_id()}:{op_suffix}",
        "chain_id": _storage_transfer_chain_id(),
    }
    if reply_to > 0:
        kwargs["reply_to"] = reply_to
    _storage_bag_transfer_state["reply_due_at"] = 0
    msg = await send_game_command(command, **kwargs)
    now = time.time()
    if not msg:
        block_code, block_reason = _storage_transfer_send_block(identity_id, command)
        if block_code in STORAGE_TRANSFER_SEND_BLOCK_DEFER_CODES or block_code.startswith("flood_wait"):
            retry_wait = block_code not in {"send_timeout", "send_exception"}
            _storage_bag_transfer_state["step"] = str(wait_step or "")
            _storage_bag_transfer_state["reply_due_at"] = now + _storage_transfer_reply_timeout_sec(wait_step, retry=retry_wait)
            _storage_bag_transfer_state["retry_command"] = command
            _storage_bag_transfer_state["retry_identity_id"] = identity_id
            _storage_bag_transfer_state["retry_reply_to"] = reply_to
            _storage_bag_transfer_state["retry_msg_id_key"] = str(msg_id_key or "")
            _storage_bag_transfer_state["retry_wait_step"] = str(wait_step or "")
            _storage_bag_transfer_state["retry_family"] = str(family or "")
            _storage_bag_transfer_state["retry_last_at"] = now
            _record_storage_transfer_event(
                "发送暂缓",
                identity_id=identity_id,
                family=family,
                command=command,
                step=str(wait_step or ""),
                detail=_storage_transfer_defer_label(block_code, block_reason),
                decision="send_deferred_by_runtime_block",
            )
            _storage_transfer_log(
                f"{command} 发送暂缓，链路保留等待补发：{_storage_transfer_defer_label(block_code, block_reason)}",
                level="warning",
            )
            return _storage_transfer_deferred_send_result(block_code, block_reason)
        if retry:
            _storage_bag_transfer_state["reply_due_at"] = now + _storage_transfer_reply_timeout_sec(wait_step, retry=True)
            _storage_bag_transfer_state["retry_last_at"] = now
        return None
    if family == "storage_bag_listing" and not retry:
        _mark_storage_listing_initial_sent(identity_id, command, now=now)
    msg_id = int(getattr(msg, "id", 0) or 0)
    _storage_bag_transfer_state[msg_id_key] = msg_id
    _remember_storage_transfer_msg_id(msg_id_key, msg_id)
    _storage_bag_transfer_state["step"] = str(wait_step or "")
    _storage_bag_transfer_state["reply_due_at"] = now + _storage_transfer_reply_timeout_sec(wait_step, retry=retry)
    _storage_bag_transfer_state["retry_command"] = command
    _storage_bag_transfer_state["retry_identity_id"] = identity_id
    _storage_bag_transfer_state["retry_reply_to"] = reply_to
    _storage_bag_transfer_state["retry_msg_id_key"] = str(msg_id_key or "")
    _storage_bag_transfer_state["retry_wait_step"] = str(wait_step or "")
    _storage_bag_transfer_state["retry_family"] = str(family or "")
    _storage_bag_transfer_state["retry_last_at"] = now
    return msg


async def _send_next_storage_bag_aggregate_buy(listing_id):
    listing_id = str(listing_id or _storage_bag_transfer_state.get("listing_id") or "").strip()
    buyers = _storage_bag_transfer_state.get("aggregate_buyers") or []
    index = int(_storage_bag_transfer_state.get("aggregate_buy_index") or 0)
    if not listing_id or index >= len(buyers):
        if _storage_bag_transfer_state.get("gift_items"):
            _storage_transfer_log("聚合购买完成，准备执行赠送物品")
            return await _start_storage_bag_gift_phase()
        _finalize_storage_bag_transfer(True, f"储物袋聚合转移完成：购买成功 {len(buyers)}/{len(buyers)}")
        await send_audit_log(f"✅ 储物袋聚合转移完成：购买成功 {len(buyers)}/{len(buyers)}", limit=240)
        return True
    buyer = dict(buyers[index] or {})
    source_id = int(buyer.get("source_identity_id") or 0)
    listing_units = normalize_storage_bag_listing_count(buyer.get("listing_count") or 1)
    items = [dict(item) for item in buyer.get("items") or [] if isinstance(item, dict)]
    if source_id <= 0 or not items:
        _storage_bag_transfer_state["aggregate_buy_index"] = index + 1
        return await _send_next_storage_bag_aggregate_buy(listing_id)
    buy_command = f"{CMD_STORAGE_BAG_BUY} {listing_id}"
    if listing_units > 1:
        buy_command = f"{buy_command}*{listing_units}"
    _storage_bag_transfer_state["source_identity_id"] = source_id
    _storage_bag_transfer_state["basic_items"] = items
    _storage_bag_transfer_state["listing_count"] = listing_units
    _storage_bag_transfer_state["buy_command"] = buy_command
    _storage_bag_transfer_state["step"] = "buying"
    _storage_transfer_log(
        f"聚合挂单购买 {index + 1}/{len(buyers)}：{buyer.get('source_label') or source_id} 购买 {listing_units} 份"
    )
    _record_storage_transfer_event(
        "准备聚合购买",
        identity_id=source_id,
        listing_id=listing_id,
        command=buy_command,
        step="buying",
        detail=f"{index + 1}/{len(buyers)}｜份数={listing_units}",
        decision="aggregate_buy_queue",
    )
    msg = await _send_storage_bag_transfer_command(
        buy_command,
        identity_id=source_id,
        msg_id_key="buy_msg_id",
        wait_step="waiting_buy_reply",
        family="storage_bag_buy",
    )
    if _is_storage_transfer_deferred_send(msg):
        return True
    if not msg:
        _record_storage_transfer_event(
            "聚合购买发送失败",
            identity_id=source_id,
            family="storage_bag_buy",
            listing_id=listing_id,
            command=buy_command,
        )
        _finalize_storage_bag_transfer(False, "聚合购买命令发送失败")
        await send_audit_log("❌ 储物袋聚合购买发送失败。", limit=220)
        return False
    _storage_transfer_log(f"已发送聚合购买命令：{buy_command}（消息ID={_storage_bag_transfer_state['buy_msg_id']}）")
    _record_storage_transfer_event(
        "聚合购买已发送",
        identity_id=source_id,
        family="storage_bag_buy",
        listing_id=listing_id,
        command=buy_command,
        msg_id=_storage_bag_transfer_state["buy_msg_id"],
        decision="aggregate_buy_sent",
    )
    return True


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


def apply_storage_bag_item_counts(identity_id, item_counts):
    identity_id = int(identity_id or 0)
    if identity_id <= 0 or not isinstance(item_counts, dict):
        return False
    records = get_storage_bag_records()
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
    changed = False
    for item_name, raw_count in item_counts.items():
        item_name = str(item_name or "").strip()
        if not item_name:
            continue
        try:
            count = max(0, int(raw_count or 0))
        except (TypeError, ValueError):
            count = 0
        old_value = int(items.get(item_name, 0) or 0)
        if count == old_value:
            continue
        if count > 0:
            items[item_name] = count
        else:
            items.pop(item_name, None)
        _adjust_storage_bag_section_item(sections, item_name, count)
        changed = True
    if changed:
        record["empty"] = not bool(items)
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
    listing_count = normalize_storage_bag_listing_count(_storage_bag_transfer_state.get("listing_count") or 1)
    target_id = int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0)
    source_id = int(_storage_bag_transfer_state.get("source_identity_id", 0) or 0)
    if listing_item and apply_storage_bag_item_deltas(target_id, {listing_item: -listing_count}):
        apply_storage_bag_item_deltas(source_id, {listing_item: listing_count})
    return changed_count


def _storage_transfer_expected_exchange_items():
    expected = {}
    for item in _storage_bag_transfer_state.get("basic_items") or []:
        item_name = str((item or {}).get("item_name") or "").strip()
        quantity = int((item or {}).get("quantity") or 0)
        if item_name and quantity > 0:
            expected[item_name] = expected.get(item_name, 0) + quantity
    return expected


def _storage_transfer_price_items(price_text):
    return parse_storage_bag_item_counts(str(price_text or "").replace("*", "x"), allow_plain=True)


def _storage_transfer_listing_success_matches_expected(success):
    if not success:
        return False
    listing_item = str(_storage_bag_transfer_state.get("listing_item") or "").strip()
    listing_count = normalize_storage_bag_listing_count(_storage_bag_transfer_state.get("listing_count") or 1)
    if str(success.get("item") or "").strip() != listing_item:
        return False
    if int(success.get("count") or 0) != listing_count:
        return False
    return _storage_transfer_price_items(success.get("price")) == _storage_transfer_expected_exchange_items()


def _is_manual_storage_transfer_listing_reply(success, reply_to, reply_context):
    if not _storage_transfer_listing_success_matches_expected(success):
        return False
    if str((reply_context or {}).get("family") or "") != "storage_bag_listing":
        return False
    target_id = int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0)
    if int((reply_context or {}).get("send_as_id") or 0) != target_id:
        return False
    raw_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return raw_cmd == str(_storage_bag_transfer_state.get("listing_command") or "").strip()


def _is_storage_bag_reply_to_transfer(reply_to, *, msg_id_key, command_prefix, reply_to_msg_id=0):
    expected_msg_id = int(_storage_bag_transfer_state.get(msg_id_key, 0) or 0)
    reply_msg_id = int(reply_to_msg_id or getattr(reply_to, "id", 0) or 0)
    if msg_id_key == "listing_msg_id" and reply_msg_id > 0:
        if reply_msg_id in _storage_transfer_listing_msg_ids():
            return True
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


def parse_storage_bag_gift_success(text):
    raw_text = str(text or "").strip()
    if not raw_text.startswith(STORAGE_TRANSFER_GIFT_SUCCESS_PREFIX):
        return None
    match = RE_STORAGE_BAG_GIFT_SUCCESS.search(raw_text)
    if not match:
        return None
    tax_match = RE_STORAGE_TRANSFER_GIFT_TAX.search(raw_text)
    return {
        "source_username": _normalize_username(match.group("source")),
        "target_username": _normalize_username(match.group("target")),
        "item_name": match.group("item").strip(),
        "quantity": int((match.group("count") or "0").replace(",", "") or 0),
        "tax": int((tax_match.group("tax") or "0").replace(",", "") or 0) if tax_match else 0,
    }


def apply_storage_bag_gift_success(text):
    parsed = parse_storage_bag_gift_success(text)
    if not parsed:
        return False
    item_name = str(parsed.get("item_name") or "").strip()
    quantity = int(parsed.get("quantity") or 0)
    if not item_name or quantity <= 0:
        return False
    source_id = resolve_storage_bag_identity_id(parsed.get("source_username"))
    target_id = resolve_storage_bag_identity_id(parsed.get("target_username"))
    if source_id <= 0 and target_id <= 0:
        return False

    changed = False
    tax = int(parsed.get("tax") or 0)
    if source_id > 0:
        source_deltas = {item_name: -quantity}
        if tax > 0:
            source_deltas["灵石"] = source_deltas.get("灵石", 0) - tax
        changed = apply_storage_bag_item_deltas(source_id, source_deltas) or changed
    if target_id > 0:
        changed = apply_storage_bag_item_deltas(target_id, {item_name: quantity}) or changed
    return changed


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


def _parse_storage_message_log_ts(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    if text.endswith(" UTC+8"):
        text = text[:-6].strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _recent_storage_message_log_paths(now, days=2):
    try:
        base = datetime.fromtimestamp(float(now or time.time()), TZ_LOCAL)
    except (TypeError, ValueError, OverflowError, OSError):
        base = datetime.now(TZ_LOCAL)
    return [
        os.path.join(MESSAGES_DIR, f"{(base - timedelta(days=offset)).strftime('%Y-%m-%d')}.log")
        for offset in range(max(1, int(days or 1)))
    ]


def _read_storage_message_log_tail(path, *, max_lines=5000, max_bytes=512 * 1024):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - int(max_bytes or 0))
            handle.seek(start)
            if start > 0:
                handle.readline()
            data = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    return data.splitlines()[-max(1, int(max_lines or 1)):]


def _is_storage_gift_anchor_topic(payload):
    try:
        chat_id = int((payload or {}).get("chat_id") or 0)
    except (TypeError, ValueError, OverflowError):
        chat_id = 0
    from ..state import get_game_group_topic_id
    topic_id = int(get_game_group_topic_id(chat_id, default=get_game_topic_id()) or 0)
    if topic_id <= 0:
        return True
    try:
        payload_topic_id = int(payload.get("topic_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        payload_topic_id = 0
    try:
        reply_to_msg_id = int(payload.get("reply_to_msg_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        reply_to_msg_id = 0
    return payload_topic_id == topic_id or reply_to_msg_id == topic_id


def _is_storage_gift_anchor_sender(payload, target_identity_id):
    target_identity_id = int(target_identity_id or 0)
    try:
        sender_id = int(payload.get("sender_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        sender_id = 0
    if target_identity_id > 0 and sender_id == target_identity_id:
        return True
    profile = get_send_as_profile(target_identity_id) if target_identity_id > 0 else {}
    username = str(payload.get("sender_username") or "").strip().lstrip("@").casefold()
    expected_username = str(profile.get("username") or "").strip().lstrip("@").casefold()
    return bool(username and expected_username and username == expected_username)


def find_recent_storage_bag_gift_anchor(target_identity_id, now=None, *, max_age_sec=STORAGE_TRANSFER_GIFT_ANCHOR_LOOKBACK_SEC):
    try:
        now = float(now if now is not None else time.time())
    except (TypeError, ValueError, OverflowError):
        now = time.time()
    min_ts = now - max(1, int(max_age_sec or STORAGE_TRANSFER_GIFT_ANCHOR_LOOKBACK_SEC))
    for path in _recent_storage_message_log_paths(now):
        if not os.path.exists(path):
            continue
        for line in reversed(_read_storage_message_log_tail(path)):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("event_type") or "") not in {"message", "sent"}:
                continue
            try:
                chat_id = int(payload.get("chat_id", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                chat_id = 0
            if not _is_storage_gift_anchor_topic(payload):
                continue
            if not _is_storage_gift_anchor_sender(payload, target_identity_id):
                continue
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            msg_ts = _parse_storage_message_log_ts(payload.get("ts"))
            if msg_ts <= 0 or msg_ts < min_ts or msg_ts > now + 60:
                continue
            try:
                msg_id = int(payload.get("message_id", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                msg_id = 0
            if msg_id > 0:
                return {"msg_id": msg_id, "chat_id": chat_id, "ts": msg_ts, "text": text}
    return {}


async def _delete_storage_bag_gift_locator():
    msg_id = int(_storage_bag_transfer_state.get("gift_locator_msg_id", 0) or 0)
    if msg_id <= 0 or _storage_bag_transfer_state.get("gift_locator_deleted"):
        return True
    if _storage_bag_transfer_state.get("gift_locator_reused"):
        _storage_bag_transfer_state["gift_locator_deleted"] = True
        _storage_bag_transfer_state["gift_locator_delete_error"] = ""
        _storage_transfer_log("赠送锚点为复用目标近期发言，跳过删除")
        return True
    if not is_auto_delete_sent_messages_enabled():
        _storage_transfer_log("自动删除消息未开启，保留赠送定位消息")
        return True
    try:
        account_id, client = _get_identity_client_for_rpc(int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0))
        if client is None:
            raise RuntimeError("身份客户端不可用")
        chat_id = int(_storage_bag_transfer_state.get("gift_locator_chat_id", 0) or 0)
        if not chat_id:
            chat_id = get_sent_message_chat_id(
                msg_id,
                default=get_game_group_id(),
                send_as_id=int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0),
            )
        await _run_account_rpc(
            client.delete_messages(chat_id, [msg_id]),
            account_id=account_id,
            client_obj=client,
        )
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
        if _storage_bag_transfer_state.get("operation") == "gift":
            message = "储物袋赠送完成：赠送成功"
        else:
            message = "储物袋转移完成：购买和赠送成功" if _storage_bag_transfer_state.get("basic_items") else "储物袋转移完成：赠送成功"
        _record_storage_transfer_event(
            "赠送完成" if _storage_bag_transfer_state.get("operation") == "gift" else "转移完成",
            identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
            step=str(_storage_bag_transfer_state.get("step") or ""),
            detail=message,
            decision="transfer_completed",
        )
        _finalize_storage_bag_transfer(True, message)
        await send_audit_log(f"✅ {message}", limit=220)
        return True, message

    item = gift_items[index]
    item_name = str(item.get("item_name") or "").strip()
    quantity = int(item.get("quantity") or 0)
    command = f"{CMD_STORAGE_BAG_GIFT} {item_name}*{quantity}"
    _storage_bag_transfer_state.update({
        "gift_command": command,
        "gift_msg_id": 0,
        "gift_item": item_name,
        "gift_next_due_at": 0,
        "step": "gift_sending",
    })
    _storage_transfer_log(f"来源身份回复定位消息发送赠送命令：{command}")
    msg = await _send_storage_bag_transfer_command(
        command,
        identity_id=int(_storage_bag_transfer_state.get("source_identity_id", 0) or 0),
        msg_id_key="gift_msg_id",
        wait_step="waiting_gift_reply",
        family="storage_bag_gift",
        reply_to=int(_storage_bag_transfer_state.get("gift_locator_msg_id", 0) or 0),
    )
    if _is_storage_transfer_deferred_send(msg):
        return True, "赠送命令发送暂缓，等待补发"
    if not msg:
        _record_storage_transfer_event(
            "赠送发送失败",
            identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
            family="storage_bag_gift",
            command=command,
            detail=item_name,
        )
        await _delete_storage_bag_gift_locator()
        message = f"赠送命令发送失败：{item_name}"
        _finalize_storage_bag_transfer(False, message)
        await send_audit_log(f"❌ 储物袋赠送发送失败：{item_name}", limit=240)
        return False, message
    _storage_transfer_log(f"已发送赠送命令，等待结果（消息ID={_storage_bag_transfer_state['gift_msg_id']}）")
    _record_storage_transfer_event(
        "赠送已发送",
        identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
        family="storage_bag_gift",
        command=command,
        msg_id=_storage_bag_transfer_state["gift_msg_id"],
        reply_msg_id=int(_storage_bag_transfer_state.get("gift_locator_msg_id", 0) or 0),
        detail=f"{item_name}x{quantity}",
        decision="gift_sent",
    )
    return True, "已发送赠送命令，等待结果"


async def _start_storage_bag_gift_phase():
    gift_items = _storage_bag_transfer_state.get("gift_items") or []
    if not gift_items:
        message = "储物袋转移完成：购买成功"
        _finalize_storage_bag_transfer(True, message)
        await send_audit_log(f"✅ {message}", limit=220)
        return True, message
    target_identity_id = int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0)
    anchor = find_recent_storage_bag_gift_anchor(target_identity_id)
    if anchor:
        anchor_msg_id = int(anchor.get("msg_id") or 0)
        anchor_text = str(anchor.get("text") or "").strip()
        _storage_bag_transfer_state.update({
            "gift_index": 0,
            "gift_locator_command": anchor_text[:80],
            "gift_locator_msg_id": anchor_msg_id,
            "gift_locator_chat_id": int(anchor.get("chat_id") or 0),
            "gift_locator_reused": True,
            "gift_locator_deleted": False,
            "gift_locator_delete_error": "",
            "gift_next_due_at": 0,
            "step": "gift_marker",
        })
        _storage_transfer_log(f"复用目标身份 5 分钟内发言作为赠送锚点（消息ID={anchor_msg_id}）")
        _record_storage_transfer_event(
            "赠送定位复用",
            identity_id=target_identity_id,
            msg_id=anchor_msg_id,
            step="gift_marker",
            detail=anchor_text[:80],
            decision="gift_locator_reused",
        )
        return await _send_next_storage_bag_gift()
    locator = random.choice(STORAGE_TRANSFER_LOCATOR_MESSAGES)
    _storage_bag_transfer_state.update({
        "gift_index": 0,
        "gift_locator_command": locator,
        "gift_locator_msg_id": 0,
        "gift_locator_chat_id": 0,
        "gift_locator_reused": False,
        "gift_locator_deleted": False,
        "gift_locator_delete_error": "",
        "gift_next_due_at": 0,
        "step": "gift_marker",
    })
    _storage_transfer_log(f"目标身份发送赠送定位消息：{locator}")
    msg = await _send_storage_bag_transfer_command(
        locator,
        identity_id=int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0),
        msg_id_key="gift_locator_msg_id",
        wait_step="gift_marker",
        family="storage_bag_gift_locator",
    )
    if _is_storage_transfer_deferred_send(msg):
        return True, "赠送定位发送暂缓，等待补发"
    if not msg:
        _record_storage_transfer_event(
            "赠送定位发送失败",
            identity_id=int(_storage_bag_transfer_state.get("target_identity_id") or 0),
            command=locator,
            step="gift_marker",
        )
        message = "赠送定位消息发送失败"
        _finalize_storage_bag_transfer(False, message)
        await send_audit_log("❌ 储物袋定位发送失败。", limit=220)
        return False, message
    _storage_transfer_log(f"已发送赠送定位消息（消息ID={_storage_bag_transfer_state['gift_locator_msg_id']}）")
    _storage_bag_transfer_state["gift_locator_chat_id"] = get_sent_message_chat_id(
        _storage_bag_transfer_state["gift_locator_msg_id"],
        default=get_game_group_id(),
        send_as_id=int(_storage_bag_transfer_state.get("target_identity_id", 0) or 0),
    )
    _record_storage_transfer_event(
        "赠送定位已发送",
        identity_id=int(_storage_bag_transfer_state.get("target_identity_id") or 0),
        command=locator,
        msg_id=_storage_bag_transfer_state["gift_locator_msg_id"],
        step="gift_marker",
        decision="gift_locator_sent",
    )
    return await _send_next_storage_bag_gift()


async def start_storage_bag_transfer_task(
    source_identity_id,
    target_identity_id,
    items,
    listing_item,
    *,
    listing_count=1,
    listing_syntax=STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
    listing_command_override="",
    aggregate_buyers=None,
    batch_child=False,
    operation="transfer",
):
    if _storage_bag_transfer_state.get("running"):
        return False, "已有储物袋转移任务正在执行", get_storage_bag_transfer_snapshot()
    if _storage_bag_transfer_batch_state.get("running") and not batch_child:
        return False, "已有储物袋批量转移正在执行", get_storage_bag_transfer_snapshot()
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
    normalized_aggregate_buyers = []
    for raw_buyer in aggregate_buyers if isinstance(aggregate_buyers, (list, tuple)) else []:
        if not isinstance(raw_buyer, dict):
            continue
        try:
            buyer_source_id = int(raw_buyer.get("source_identity_id") or 0)
        except (TypeError, ValueError):
            continue
        if buyer_source_id not in known_ids or buyer_source_id == target_identity_id:
            continue
        buyer_items = []
        for raw_item in raw_buyer.get("items") if isinstance(raw_buyer.get("items"), (list, tuple)) else []:
            item, error = _normalize_storage_transfer_task_item(raw_item)
            if error:
                return False, error, None
            buyer_items.append(item)
        if not buyer_items:
            continue
        normalized_aggregate_buyers.append({
            "source_identity_id": buyer_source_id,
            "source_label": str(raw_buyer.get("source_label") or "").strip(),
            "items": buyer_items,
            "listing_count": normalize_storage_bag_listing_count(raw_buyer.get("listing_count") or 1),
        })
    basic_items = [item for item in normalized_items if str(item.get("method") or "unknown") != "gift"]
    gift_items = [item for item in normalized_items if str(item.get("method") or "unknown") == "gift"]
    if not basic_items and not gift_items:
        return False, "当前没有可执行的转移物品", None
    listing_item = str(listing_item or "").strip()
    listing_count = normalize_storage_bag_listing_count(listing_count)
    listing_syntax = normalize_storage_bag_listing_syntax(listing_syntax)
    operation = "gift" if str(operation or "").strip().lower() == "gift" else "transfer"
    listing_command = ""
    listing_command_override = str(listing_command_override or "").strip()
    if basic_items:
        if not listing_item:
            return False, "请选择目标身份用于上架的物品", None
        if listing_command_override:
            listing_command = listing_command_override
        else:
            exchange_parts = [f"{item['item_name']}*{int(item['quantity'])}" for item in basic_items]
            listing_command = format_storage_bag_listing_command(
                listing_item,
                listing_count,
                exchange_parts,
                listing_syntax=listing_syntax,
            )
    task_key = _storage_transfer_task_key(
        source_identity_id=source_identity_id,
        target_identity_id=target_identity_id,
        items=normalized_items,
        listing_item=listing_item,
        listing_count=listing_count,
        listing_syntax=listing_syntax,
        operation=operation,
    )
    if listing_command and not batch_child:
        wait_sec = _storage_listing_initial_wait_sec(target_identity_id, listing_command)
        if wait_sec > 0:
            return False, f"相同上架命令刚发送，约 {int(wait_sec) + 1} 秒后再试", get_storage_bag_transfer_snapshot()
    now = time.time()
    _clear_storage_bag_transfer_state()
    _storage_bag_transfer_state.update({
        "running": True,
        "operation": operation,
        "op_id": uuid.uuid4().hex[:12],
        "source_identity_id": int(source_identity_id),
        "target_identity_id": int(target_identity_id),
        "items": normalized_items,
        "basic_items": basic_items,
        "gift_items": gift_items,
        "listing_item": listing_item,
        "listing_count": listing_count,
        "listing_syntax": listing_syntax,
        "listing_command": listing_command,
        "aggregate_buyers": normalized_aggregate_buyers,
        "aggregate_buy_index": 0,
        "aggregate_listing_count": listing_count if normalized_aggregate_buyers else 0,
        "task_key": task_key,
        "step": "listing" if basic_items else "gift_marker",
        "created_at": now,
        "updated_at": now,
    })
    if not basic_items:
        ok, message = await _start_storage_bag_gift_phase()
        return ok, message, get_storage_bag_transfer_snapshot()
    _storage_transfer_log(f"目标身份发送上架命令：{listing_command}")
    msg = await _send_storage_bag_transfer_command(
        listing_command,
        identity_id=int(target_identity_id),
        msg_id_key="listing_msg_id",
        wait_step="waiting_listing_reply",
        family="storage_bag_listing",
    )
    if _is_storage_transfer_deferred_send(msg):
        return True, "上架发送暂缓，等待补发", get_storage_bag_transfer_snapshot()
    if not msg:
        _record_storage_transfer_event(
            "上架发送失败",
            identity_id=target_identity_id,
            command=listing_command,
        )
        _finalize_storage_bag_transfer(False, "上架命令发送失败")
        return False, "上架命令发送失败", get_storage_bag_transfer_snapshot()
    _storage_transfer_log(f"已发送上架命令，等待挂单结果（消息ID={_storage_bag_transfer_state['listing_msg_id']}）")
    _record_storage_transfer_event(
        "上架已发送",
        identity_id=target_identity_id,
        command=listing_command,
        msg_id=_storage_bag_transfer_state["listing_msg_id"],
    )
    return True, "已开始储物袋转移，等待上架结果", get_storage_bag_transfer_snapshot()


def _force_storage_bag_gift_items(items):
    forced = []
    for raw_item in items if isinstance(items, (list, tuple)) else []:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        item["method"] = "gift"
        forced.append(item)
    return forced


def _force_storage_bag_gift_tasks(tasks):
    forced_tasks = []
    for raw_task in tasks if isinstance(tasks, (list, tuple)) else []:
        if not isinstance(raw_task, dict):
            continue
        task = dict(raw_task)
        task["items"] = _force_storage_bag_gift_items(task.get("items") or [])
        task["listing_item"] = ""
        task["operation"] = "gift"
        forced_tasks.append(task)
    return forced_tasks


async def start_storage_bag_gift_task(
    source_identity_id,
    target_identity_id,
    items,
    *,
    batch_child=False,
):
    return await start_storage_bag_transfer_task(
        source_identity_id,
        target_identity_id,
        _force_storage_bag_gift_items(items),
        "",
        batch_child=batch_child,
        operation="gift",
    )


async def start_storage_bag_gift_batch(
    tasks,
    *,
    target_identity_id=0,
    stop_on_error=True,
):
    return await start_storage_bag_transfer_batch(
        _force_storage_bag_gift_tasks(tasks),
        target_identity_id=target_identity_id,
        listing_item="",
        stop_on_error=stop_on_error,
        operation="gift",
    )


async def _start_next_storage_bag_transfer_batch_task():
    if not _storage_bag_transfer_batch_state.get("running"):
        return False
    if _storage_bag_transfer_state.get("running"):
        return False
    queue = _storage_bag_transfer_batch_state.get("queue")
    if not isinstance(queue, list) or not queue:
        completed = len(_storage_bag_transfer_batch_state.get("completed") or [])
        total = int(_storage_bag_transfer_batch_state.get("total") or 0)
        op_label = _storage_bag_operation_label()
        _finalize_storage_bag_transfer_batch(True, f"批量{op_label}完成：{completed}/{total}")
        return True
    candidate = dict(queue[0] or {})
    listing_command = str(candidate.get("listing_command") or "").strip()
    target_identity_id = int(candidate.get("target_identity_id") or _storage_bag_transfer_batch_state.get("target_identity_id") or 0)
    if listing_command:
        wait_sec = _storage_listing_initial_wait_sec(target_identity_id, listing_command)
        if wait_sec > 0:
            due_at = time.time() + wait_sec
            task_key = _storage_transfer_task_key_from_task(candidate)
            previous_waiting_key = str(_storage_bag_transfer_batch_state.get("waiting_task_key") or "")
            _storage_bag_transfer_batch_state["status"] = "waiting_safe_gap"
            _storage_bag_transfer_batch_state["next_task_due_at"] = due_at
            _storage_bag_transfer_batch_state["waiting_task_key"] = task_key
            _storage_bag_transfer_batch_state["updated_at"] = time.time()
            if previous_waiting_key != task_key:
                _storage_transfer_batch_log(
                    f"相同上架命令刚发送，下一笔延后约 {int(wait_sec) + 1} 秒：{listing_command}",
                    level="warning",
                )
            return True
    _storage_bag_transfer_batch_state["next_task_due_at"] = 0
    _storage_bag_transfer_batch_state["waiting_task_key"] = ""
    task = dict(queue.pop(0) or {})
    _storage_bag_transfer_batch_state["active_task"] = task
    _storage_bag_transfer_batch_state["status"] = "running_task"
    _storage_bag_transfer_batch_state["updated_at"] = time.time()
    _storage_transfer_batch_log(
        f"开始 {task.get('source_label') or task.get('source_identity_id')} -> {task.get('target_label') or task.get('target_identity_id')}：{len(task.get('items') or [])} 项"
    )
    ok, message, _snapshot = await start_storage_bag_transfer_task(
        task.get("source_identity_id"),
        task.get("target_identity_id"),
        task.get("items") or [],
        task.get("listing_item") or "",
        listing_count=task.get("listing_count") or _storage_bag_transfer_batch_state.get("listing_count") or 1,
        listing_syntax=task.get("listing_syntax") or _storage_bag_transfer_batch_state.get("listing_syntax") or STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
        listing_command_override=task.get("listing_command") or "",
        aggregate_buyers=task.get("aggregate_buyers") or [],
        batch_child=True,
        operation=task.get("operation") or _storage_bag_transfer_batch_state.get("operation") or "transfer",
    )
    if ok:
        active_task = _storage_bag_transfer_batch_state.get("active_task")
        if isinstance(active_task, dict):
            active_task["op_id"] = str(_storage_bag_transfer_state.get("op_id") or "")
    if not ok:
        _storage_bag_transfer_batch_state["active_task"] = None
        failed_task = {**task, "message": str(message or ""), "finished_at": time.time()}
        _storage_bag_transfer_batch_state.setdefault("failed", []).append(failed_task)
        if _storage_bag_transfer_batch_state.get("stop_on_error", True):
            _storage_bag_transfer_batch_state["queue"] = []
            op_label = _storage_bag_operation_label()
            _finalize_storage_bag_transfer_batch(False, f"批量{op_label}停止：{message}")
            await send_audit_log(f"❌ 储物袋批量{op_label}停止：{message}", limit=260)
            return False
        return await _start_next_storage_bag_transfer_batch_task()
    return True


async def _enqueue_storage_bag_transfer_batch_tasks(
    normalized_tasks,
    *,
    target_identity_id=0,
    listing_item="",
    listing_count=1,
    listing_syntax=STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
    stop_on_error=True,
    operation="transfer",
):
    now = time.time()
    operation = "gift" if str(operation or "").strip().lower() == "gift" else "transfer"
    if not _storage_bag_transfer_batch_state.get("running"):
        _clear_storage_bag_transfer_batch_state()
        _storage_bag_transfer_batch_state.update({
            "running": True,
            "operation": operation,
            "batch_id": uuid.uuid4().hex[:12],
            "target_identity_id": int(target_identity_id or 0),
            "listing_item": str(listing_item or "").strip(),
            "listing_count": normalize_storage_bag_listing_count(listing_count),
            "listing_syntax": normalize_storage_bag_listing_syntax(listing_syntax),
            "queue": [],
            "active_task": None,
            "completed": [],
            "failed": [],
            "total": 0,
            "stop_on_error": bool(stop_on_error),
            "status": "queued",
            "last_message": "",
            "created_at": now,
            "updated_at": now,
        })
    queue = _storage_bag_transfer_batch_state.get("queue")
    if not isinstance(queue, list):
        queue = []
        _storage_bag_transfer_batch_state["queue"] = queue
    added_tasks = [{**dict(task), "operation": operation} for task in normalized_tasks]
    queue.extend(added_tasks)
    op_label = _storage_bag_operation_label(operation)
    _storage_bag_transfer_batch_state["total"] = int(_storage_bag_transfer_batch_state.get("total") or 0) + len(added_tasks)
    _storage_bag_transfer_batch_state["status"] = "queued" if not _storage_bag_transfer_batch_state.get("active_task") else "running_task"
    _storage_bag_transfer_batch_state["last_message"] = f"已加入{op_label}队列：{len(added_tasks)} 个来源"
    _storage_bag_transfer_batch_state["updated_at"] = now
    _storage_transfer_batch_log(
        f"加入{op_label}队列：{len(added_tasks)} 个来源，待跑 {len(queue)}"
    )
    if not _storage_bag_transfer_state.get("running") and not _storage_bag_transfer_batch_state.get("active_task"):
        await _start_next_storage_bag_transfer_batch_task()
    return True, f"已加入储物袋{op_label}队列：{len(added_tasks)} 个来源", get_storage_bag_transfer_snapshot()


async def start_storage_bag_transfer_batch(
    tasks,
    *,
    target_identity_id=0,
    listing_item="",
    listing_count=1,
    listing_syntax=STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
    stop_on_error=True,
    operation="transfer",
):
    normalized_tasks = []
    known_ids = {int(identity_id) for identity_id in get_identity_ids()}
    try:
        target_identity_id = int(target_identity_id or 0)
    except (TypeError, ValueError):
        target_identity_id = 0
    if target_identity_id not in known_ids:
        return False, "目标身份无效", get_storage_bag_transfer_snapshot()
    operation = "gift" if str(operation or "").strip().lower() == "gift" else "transfer"
    listing_count = normalize_storage_bag_listing_count(listing_count)
    listing_syntax = normalize_storage_bag_listing_syntax(listing_syntax)
    for raw_task in tasks if isinstance(tasks, (list, tuple)) else []:
        if not isinstance(raw_task, dict):
            continue
        try:
            source_identity_id = int(raw_task.get("source_identity_id") or 0)
            raw_target_id = int(raw_task.get("target_identity_id") or target_identity_id or 0)
        except (TypeError, ValueError):
            return False, "身份参数无效", get_storage_bag_transfer_snapshot()
        if source_identity_id not in known_ids:
            return False, "来源身份无效", get_storage_bag_transfer_snapshot()
        if raw_target_id != target_identity_id:
            return False, "批量转移目标身份不一致", get_storage_bag_transfer_snapshot()
        if source_identity_id == target_identity_id:
            continue
        normalized_items = []
        for raw_item in raw_task.get("items") if isinstance(raw_task.get("items"), (list, tuple)) else []:
            item, error = _normalize_storage_transfer_task_item(raw_item)
            if error:
                return False, error, get_storage_bag_transfer_snapshot()
            normalized_items.append(item)
        if not normalized_items:
            continue
        task_listing_item = str(raw_task.get("listing_item") or listing_item or "").strip()
        task_listing_count = normalize_storage_bag_listing_count(raw_task.get("listing_count") or listing_count)
        task_listing_syntax = normalize_storage_bag_listing_syntax(raw_task.get("listing_syntax") or listing_syntax)
        if any(str(item.get("method") or "unknown") != "gift" for item in normalized_items) and not task_listing_item:
            return False, "请选择目标身份用于上架的物品", get_storage_bag_transfer_snapshot()
        source_profile = get_send_as_profile(source_identity_id)
        target_profile = get_send_as_profile(target_identity_id)
        task_listing_command = str(raw_task.get("listing_command") or "").strip()
        if not task_listing_command:
            task_listing_command = _storage_transfer_listing_command_for_items(
                normalized_items,
                task_listing_item,
                task_listing_count,
                task_listing_syntax,
            )
        task_key = _storage_transfer_task_key(
            source_identity_id=source_identity_id,
            target_identity_id=target_identity_id,
            items=normalized_items,
            listing_item=task_listing_item,
            listing_count=task_listing_count,
            listing_syntax=task_listing_syntax,
            operation=operation,
        )
        normalized_tasks.append({
            "source_identity_id": source_identity_id,
            "source_label": source_profile.get("label") or source_profile.get("username") or str(source_identity_id),
            "target_identity_id": target_identity_id,
            "target_label": target_profile.get("label") or target_profile.get("username") or str(target_identity_id),
            "items": normalized_items,
            "aggregate_buyers": [dict(buyer) for buyer in raw_task.get("aggregate_buyers") or [] if isinstance(buyer, dict)],
            "listing_item": task_listing_item,
            "listing_count": task_listing_count,
            "listing_syntax": task_listing_syntax,
            "listing_command": task_listing_command,
            "task_key": task_key,
            "operation": operation,
        })
    op_label = _storage_bag_operation_label(operation)
    if not normalized_tasks:
        return False, f"没有可执行的批量{op_label}任务", get_storage_bag_transfer_snapshot()
    if _storage_bag_transfer_state.get("running") or _storage_bag_transfer_batch_state.get("running"):
        existing_keys = _storage_transfer_active_task_keys()
        fresh_tasks = []
        duplicate_count = 0
        for task in normalized_tasks:
            task_key = _storage_transfer_task_key_from_task(task)
            if task_key in existing_keys:
                duplicate_count += 1
                continue
            existing_keys.add(task_key)
            fresh_tasks.append(task)
        if duplicate_count:
            _storage_transfer_batch_log(f"忽略重复{op_label}任务：{duplicate_count} 个", level="warning")
        if not fresh_tasks:
            return False, f"相同储物袋{op_label}任务已在执行或排队，已忽略重复启动", get_storage_bag_transfer_snapshot()
        return await _enqueue_storage_bag_transfer_batch_tasks(
            fresh_tasks,
            target_identity_id=target_identity_id,
            listing_item=listing_item,
            listing_count=listing_count,
            listing_syntax=listing_syntax,
            stop_on_error=stop_on_error,
            operation=operation,
        )
    now = time.time()
    _clear_storage_bag_transfer_state()
    _clear_storage_bag_transfer_batch_state()
    _storage_bag_transfer_batch_state.update({
        "running": True,
        "operation": operation,
        "batch_id": uuid.uuid4().hex[:12],
        "target_identity_id": target_identity_id,
        "listing_item": str(listing_item or "").strip(),
        "listing_count": listing_count,
        "listing_syntax": listing_syntax,
        "queue": [dict(task) for task in normalized_tasks],
        "active_task": None,
        "completed": [],
        "failed": [],
        "total": len(normalized_tasks),
        "stop_on_error": bool(stop_on_error),
        "status": "queued",
        "last_message": "",
        "created_at": now,
        "updated_at": now,
    })
    _storage_transfer_batch_log(f"已创建批量{op_label}：{len(normalized_tasks)} 个来源，目标 {target_identity_id}")
    ok = await _start_next_storage_bag_transfer_batch_task()
    message = f"已开始批量{op_label}：{len(normalized_tasks)} 个来源"
    return bool(ok), message if ok else (_storage_bag_transfer_batch_state.get("last_message") or f"批量{op_label}启动失败"), get_storage_bag_transfer_snapshot()


async def cancel_storage_bag_transfer_task():
    if not _storage_bag_transfer_state.get("running"):
        if _storage_bag_transfer_batch_state.get("running"):
            _storage_bag_transfer_batch_state["queue"] = []
            _storage_bag_transfer_batch_state["active_task"] = None
            _finalize_storage_bag_transfer_batch(False, "用户取消批量转移")
            return True, "已取消批量转移任务", get_storage_bag_transfer_snapshot()
        return False, "当前没有进行中的转移任务", get_storage_bag_transfer_snapshot()
    step = str(_storage_bag_transfer_state.get("step") or "")
    if step in {"listing", "buying", "gift_marker", "gift_sending", "waiting_listing_reply", "waiting_buy_reply", "waiting_gift_reply"}:
        _record_storage_transfer_event(
            "取消被拒绝",
            kind="skipped",
            reason="storage_bag_transfer_cancel_rejected",
            identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
            step=step,
        )
        return False, "命令已发送，不能安全取消；请等待回复或超时", get_storage_bag_transfer_snapshot()
    await _delete_storage_bag_gift_locator()
    _finalize_storage_bag_transfer(False, "用户取消转移任务")
    if _storage_bag_transfer_batch_state.get("running"):
        _storage_bag_transfer_batch_state["queue"] = []
        _storage_bag_transfer_batch_state["active_task"] = None
        _finalize_storage_bag_transfer_batch(False, "用户取消批量转移")
    return True, "已取消转移任务", get_storage_bag_transfer_snapshot()


async def _handle_storage_bag_listing_reply(raw_text, parsed_success=None, *, reply_msg_id=0, family="storage_bag_listing"):
    if is_storage_transfer_waiting_reply(raw_text):
        _storage_transfer_note_waiting_reply("上架")
        return False
    success = parsed_success or _parse_listing_success(raw_text)
    if success:
        _storage_bag_transfer_state["listing_id"] = str(success["id"])
        for item_name in _storage_transfer_item_names_for_rule_update(raw_text):
            rule = get_storage_bag_item_rules().get(item_name)
            if not isinstance(rule, dict) or str(rule.get("method") or "unknown") == "unknown":
                _set_storage_bag_rule_method(item_name, "basic")
        if _storage_bag_transfer_state.get("aggregate_buyers"):
            _storage_transfer_log(f"聚合上架成功，挂单ID={success['id']}，准备分摊购买")
            _record_storage_transfer_event(
                "准备聚合分摊购买",
                identity_id=int(_storage_bag_transfer_state.get("target_identity_id") or 0),
                family=family,
                listing_id=success["id"],
                reply_msg_id=reply_msg_id,
                matched_text=raw_text,
                decision="aggregate_listing_success_queue_buyers",
            )
            await _send_next_storage_bag_aggregate_buy(success["id"])
            return True
        buy_command = f"{CMD_STORAGE_BAG_BUY} {success['id']}"
        _storage_bag_transfer_state["buy_command"] = buy_command
        _storage_bag_transfer_state["step"] = "buying"
        _storage_transfer_log(f"上架成功，挂单ID={success['id']}，来源身份准备购买")
        _record_storage_transfer_event(
            "准备购买",
            identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
            family=family,
            listing_id=success["id"],
            command=buy_command,
            reply_msg_id=reply_msg_id,
            matched_text=raw_text,
            decision="listing_success_queue_buy",
        )
        msg = await _send_storage_bag_transfer_command(
            buy_command,
            identity_id=int(_storage_bag_transfer_state["source_identity_id"]),
            msg_id_key="buy_msg_id",
            wait_step="waiting_buy_reply",
            family="storage_bag_buy",
        )
        if _is_storage_transfer_deferred_send(msg):
            return True
        if not msg:
            _record_storage_transfer_event(
                "购买发送失败",
                identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
                family="storage_bag_buy",
                listing_id=success["id"],
                command=buy_command,
                reply_msg_id=reply_msg_id,
                matched_text=raw_text,
            )
            _finalize_storage_bag_transfer(False, "购买命令发送失败")
            await send_audit_log("❌ 储物袋购买发送失败。", limit=220)
            return True
        _storage_transfer_log(f"已发送购买命令：{buy_command}（消息ID={_storage_bag_transfer_state['buy_msg_id']}）")
        _record_storage_transfer_event(
            "购买已发送",
            identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
            family="storage_bag_buy",
            listing_id=success["id"],
            command=buy_command,
            msg_id=_storage_bag_transfer_state["buy_msg_id"],
            reply_msg_id=reply_msg_id,
            matched_text=raw_text,
            decision="buy_sent",
        )
        return True
    reason = raw_text.splitlines()[0].strip() if raw_text.splitlines() else raw_text[:80]
    blocked = any(keyword in raw_text for keyword in STORAGE_TRANSFER_BLOCKED_KEYWORDS)
    gift_fallback = any(keyword in raw_text for keyword in STORAGE_TRANSFER_GIFT_FALLBACK_KEYWORDS)
    non_rule_failure = any(keyword in raw_text for keyword in STORAGE_TRANSFER_NON_RULE_FAILURE_KEYWORDS)
    if gift_fallback and not non_rule_failure:
        for item_name in _storage_transfer_item_names_for_rule_update(raw_text, fallback_all=False):
            rule = get_storage_bag_item_rules().get(item_name)
            method = str((rule or {}).get("method") or "unknown") if isinstance(rule, dict) else "unknown"
            reason_text = str((rule or {}).get("reason") or "") if isinstance(rule, dict) else ""
            can_learn = method == "unknown" or (method == "blocked" and any(keyword in reason_text for keyword in STORAGE_TRANSFER_GIFT_FALLBACK_KEYWORDS))
            if can_learn:
                _set_storage_bag_rule_method(item_name, "gift", reason=reason)
    elif blocked and not non_rule_failure:
        for item_name in _storage_transfer_item_names_for_rule_update(raw_text, fallback_all=False):
            rule = get_storage_bag_item_rules().get(item_name)
            if not isinstance(rule, dict) or str(rule.get("method") or "unknown") == "unknown":
                _set_storage_bag_rule_method(item_name, "blocked", reason=reason)
    _record_storage_transfer_event(
        "上架失败",
        identity_id=int(_storage_bag_transfer_state.get("target_identity_id") or 0),
        family=family,
        step=str(_storage_bag_transfer_state.get("step") or ""),
        detail=reason,
        reply_msg_id=reply_msg_id,
        matched_text=raw_text,
    )
    _finalize_storage_bag_transfer(False, f"上架失败：{reason}")
    await send_audit_log("❌ 储物袋上架失败。", limit=260)
    return True


async def _handle_storage_bag_buy_reply(raw_text, *, reply_msg_id=0, family="storage_bag_buy"):
    if is_storage_transfer_waiting_reply(raw_text):
        _storage_transfer_note_waiting_reply("购买")
        return False
    if raw_text.startswith("交易成功！") or "你成功购得" in raw_text:
        moved_count = _storage_transfer_apply_basic_items_move()
        _record_storage_transfer_event(
            "购买成功",
            identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
            family=family,
            listing_id=str(_storage_bag_transfer_state.get("listing_id") or ""),
            reply_msg_id=reply_msg_id,
            detail=f"本地核销={moved_count}项",
            matched_text=raw_text,
            decision="buy_success_inventory_synced",
        )
        if moved_count:
            _storage_transfer_log(f"已同步本地储物袋数据：买卖转移 {moved_count} 项")
        if _storage_bag_transfer_state.get("aggregate_buyers"):
            next_index = int(_storage_bag_transfer_state.get("aggregate_buy_index") or 0) + 1
            _storage_bag_transfer_state["aggregate_buy_index"] = next_index
            buyers = _storage_bag_transfer_state.get("aggregate_buyers") or []
            if next_index < len(buyers):
                await _send_next_storage_bag_aggregate_buy(str(_storage_bag_transfer_state.get("listing_id") or ""))
                return True
            if _storage_bag_transfer_state.get("gift_items"):
                _storage_transfer_log("聚合购买完成，准备执行赠送物品")
                await _start_storage_bag_gift_phase()
                return True
            _finalize_storage_bag_transfer(True, f"储物袋聚合转移完成：购买成功 {next_index}/{len(buyers)}")
            await send_audit_log(f"✅ 储物袋聚合转移完成：购买成功 {next_index}/{len(buyers)}", limit=240)
            return True
        if _storage_bag_transfer_state.get("gift_items"):
            _storage_transfer_log("购买成功，准备执行赠送物品")
            await _start_storage_bag_gift_phase()
            return True
        _finalize_storage_bag_transfer(True, "储物袋转移完成：购买成功")
        await send_audit_log("✅ 储物袋转移完成：购买成功", limit=220)
        return True
    reason = raw_text.splitlines()[0].strip() if raw_text.splitlines() else raw_text[:80]
    _record_storage_transfer_event(
        "购买失败",
        identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
        family=family,
        listing_id=str(_storage_bag_transfer_state.get("listing_id") or ""),
        detail=reason,
        reply_msg_id=reply_msg_id,
        matched_text=raw_text,
    )
    _finalize_storage_bag_transfer(False, f"购买失败：{reason}")
    await send_audit_log("❌ 储物袋购买失败。", limit=260)
    return True


async def _handle_storage_bag_gift_reply(raw_text, *, reply_msg_id=0, family="storage_bag_gift"):
    if is_storage_transfer_waiting_reply(raw_text):
        _storage_transfer_note_waiting_reply("赠送")
        return False
    expected_gift = _current_storage_transfer_gift_item()
    gift_item = str(expected_gift.get("item_name") or _storage_bag_transfer_state.get("gift_item") or "").strip()
    gift_quantity = int(expected_gift.get("quantity") or 0)
    if raw_text.startswith(STORAGE_TRANSFER_GIFT_SUCCESS_PREFIX):
        result = _parse_storage_transfer_gift_result(raw_text)
        if not result:
            _record_storage_transfer_event(
                "赠送结果无法识别",
                identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
                family=family,
                reply_msg_id=reply_msg_id,
                detail=gift_item,
                matched_text=raw_text,
            )
            await _delete_storage_bag_gift_locator()
            message = f"赠送结果无法识别：{gift_item}"
            _finalize_storage_bag_transfer(False, message)
            await send_audit_log(f"❌ 储物袋赠送结果无法识别：{gift_item}", limit=260)
            return True
        moved_item = str(result.get("item_name") or "").strip()
        moved_quantity = int(result.get("quantity") or 0)
        if moved_item != gift_item or moved_quantity != gift_quantity:
            _record_storage_transfer_event(
                "赠送结果不匹配",
                kind="skipped",
                reason="storage_bag_transfer_gift_mismatch",
                identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
                family=family,
                reply_msg_id=reply_msg_id,
                detail=f"期望={gift_item}x{gift_quantity}｜实际={moved_item or '未知'}x{moved_quantity}",
                matched_text=raw_text,
                decision="gift_result_mismatch",
            )
            await _delete_storage_bag_gift_locator()
            message = f"赠送结果不匹配：期望 {gift_item} x{gift_quantity}，实际 {moved_item or '未知'} x{moved_quantity}"
            _finalize_storage_bag_transfer(False, message)
            await send_audit_log(f"❌ 储物袋赠送结果不匹配：{gift_item}", limit=260)
            return True
        source_costs = {"灵石": int(result.get("tax") or 0)} if int(result.get("tax") or 0) > 0 else None
        gift_synced = _storage_transfer_apply_item_move(moved_item, moved_quantity, extra_source_costs=source_costs)
        if gift_synced:
            _storage_transfer_log(f"已同步本地储物袋数据：赠送 {moved_item} x{moved_quantity}")
        _record_storage_transfer_event(
            "赠送成功",
            identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
            family=family,
            reply_msg_id=reply_msg_id,
            detail=f"{moved_item}x{moved_quantity}｜因果税={int(result.get('tax') or 0)}｜本地核销={'是' if gift_synced else '否'}",
            matched_text=raw_text,
            decision="gift_success_inventory_synced",
        )
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
    _record_storage_transfer_event(
        "赠送失败",
        identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
        family=family,
        reply_msg_id=reply_msg_id,
        detail=f"{gift_item}：{reason}",
        matched_text=raw_text,
    )
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
    reply_family = str(matched_family or (reply_context or {}).get("family") or "").strip()
    route_source = str((reply_context or {}).get("matched_via") or "storage_bag_transfer").strip()
    step = str(_storage_bag_transfer_state.get("step") or "")
    if step == "waiting_listing_reply":
        is_expected_reply = _is_storage_bag_reply_to_transfer(
            reply_to,
            msg_id_key="listing_msg_id",
            command_prefix=CMD_STORAGE_BAG_LISTING,
            reply_to_msg_id=reply_to_msg_id,
        )
        parsed_success = _parse_listing_success(raw_text)
        if not is_expected_reply:
            if not _is_manual_storage_transfer_listing_reply(parsed_success, reply_to, reply_context):
                if parsed_success:
                    _record_storage_transfer_event(
                        "忽略上架回执",
                        kind="skipped",
                        reason="storage_bag_transfer_reply_mismatch",
                        identity_id=int(_storage_bag_transfer_state.get("target_identity_id") or 0),
                        family=reply_family or "storage_bag_listing",
                        reply_msg_id=reply_to_msg_id or getattr(reply_to, "id", 0) or 0,
                        detail=f"回执挂单ID={parsed_success['id']}｜未匹配当前上架命令",
                        matched_text=raw_text,
                        decision="listing_reply_mismatch",
                        route_source=route_source,
                    )
                return False
            _storage_bag_transfer_state["listing_msg_id"] = int(reply_to_msg_id or getattr(reply_to, "id", 0) or 0)
            _storage_transfer_log(f"采纳手动补发上架回执，挂单ID={parsed_success['id']}")
            _record_storage_transfer_event(
                "采纳手动补发",
                identity_id=int(_storage_bag_transfer_state.get("target_identity_id") or 0),
                family=reply_family or "storage_bag_listing",
                listing_id=parsed_success["id"],
                reply_msg_id=_storage_bag_transfer_state["listing_msg_id"],
                command=str(getattr(reply_to, "raw_text", "") or "").strip(),
                matched_text=raw_text,
                decision="manual_listing_accepted",
                route_source=route_source,
            )
        return await _handle_storage_bag_listing_reply(
            raw_text,
            parsed_success=parsed_success,
            reply_msg_id=reply_to_msg_id or getattr(reply_to, "id", 0) or 0,
            family=reply_family or "storage_bag_listing",
        )
    if step == "waiting_buy_reply":
        if not _is_storage_bag_reply_to_transfer(reply_to, msg_id_key="buy_msg_id", command_prefix=CMD_STORAGE_BAG_BUY, reply_to_msg_id=reply_to_msg_id):
            parsed_success = _parse_listing_success(raw_text)
            if parsed_success:
                current_listing_id = str(_storage_bag_transfer_state.get("listing_id") or "").strip()
                if current_listing_id:
                    detail = f"回执挂单ID={parsed_success['id']}｜当前挂单ID={current_listing_id}"
                else:
                    detail = f"回执挂单ID={parsed_success['id']}｜当前等待购买回执"
                _record_storage_transfer_event(
                    "忽略上架回执",
                    kind="skipped",
                    reason="storage_bag_transfer_reply_mismatch",
                    identity_id=int(_storage_bag_transfer_state.get("target_identity_id") or 0),
                    family=reply_family or "storage_bag_listing",
                    reply_msg_id=reply_to_msg_id or getattr(reply_to, "id", 0) or 0,
                    detail=detail,
                    matched_text=raw_text,
                    decision="stale_listing_reply_ignored",
                    route_source=route_source,
                )
            return False
        return await _handle_storage_bag_buy_reply(
            raw_text,
            reply_msg_id=reply_to_msg_id or getattr(reply_to, "id", 0) or 0,
            family=reply_family or "storage_bag_buy",
        )
    if step == "waiting_gift_reply":
        if not _is_storage_bag_reply_to_transfer(reply_to, msg_id_key="gift_msg_id", command_prefix=CMD_STORAGE_BAG_GIFT, reply_to_msg_id=reply_to_msg_id):
            _record_storage_transfer_event(
                "忽略赠送回执",
                kind="skipped",
                reason="storage_bag_transfer_reply_mismatch",
                identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
                family=reply_family or "storage_bag_gift",
                reply_msg_id=reply_to_msg_id or getattr(reply_to, "id", 0) or 0,
                matched_text=raw_text,
                decision="gift_reply_mismatch",
                route_source=route_source,
            )
            return False
        return await _handle_storage_bag_gift_reply(
            raw_text,
            reply_msg_id=reply_to_msg_id or getattr(reply_to, "id", 0) or 0,
            family=reply_family or "storage_bag_gift",
        )
    return False


def _storage_transfer_retry_config_for_step(step):
    step = str(step or "")
    if step == "waiting_listing_reply":
        return {
            "label": "上架",
            "command": str(_storage_bag_transfer_state.get("listing_command") or ""),
            "identity_id": int(_storage_bag_transfer_state.get("target_identity_id") or 0),
            "reply_to": 0,
            "msg_id_key": "listing_msg_id",
            "wait_step": "waiting_listing_reply",
            "family": "storage_bag_listing",
        }
    if step == "waiting_buy_reply":
        return {
            "label": "购买",
            "command": str(_storage_bag_transfer_state.get("buy_command") or ""),
            "identity_id": int(_storage_bag_transfer_state.get("source_identity_id") or 0),
            "reply_to": 0,
            "msg_id_key": "buy_msg_id",
            "wait_step": "waiting_buy_reply",
            "family": "storage_bag_buy",
        }
    if step == "waiting_gift_reply":
        return {
            "label": "赠送",
            "command": str(_storage_bag_transfer_state.get("gift_command") or ""),
            "identity_id": int(_storage_bag_transfer_state.get("source_identity_id") or 0),
            "reply_to": int(_storage_bag_transfer_state.get("gift_locator_msg_id") or 0),
            "msg_id_key": "gift_msg_id",
            "wait_step": "waiting_gift_reply",
            "family": "storage_bag_gift",
        }
    if step == "gift_marker":
        return {
            "label": "赠送定位",
            "command": str(_storage_bag_transfer_state.get("gift_locator_command") or ""),
            "identity_id": int(_storage_bag_transfer_state.get("target_identity_id") or 0),
            "reply_to": 0,
            "msg_id_key": "gift_locator_msg_id",
            "wait_step": "gift_marker",
            "family": "storage_bag_gift_locator",
        }
    return {}


def _is_storage_transfer_reply_log_entry(entry):
    raw_text = str((entry or {}).get("text") or "").strip()
    if not raw_text:
        return False
    return (
        bool(_parse_listing_success(raw_text))
        or raw_text.startswith("交易成功！")
        or "你成功购得" in raw_text
        or raw_text.startswith(STORAGE_TRANSFER_GIFT_SUCCESS_PREFIX)
        or is_storage_transfer_waiting_reply(raw_text)
        or any(keyword in raw_text for keyword in STORAGE_TRANSFER_BLOCKED_KEYWORDS)
        or any(keyword in raw_text for keyword in STORAGE_TRANSFER_GIFT_FALLBACK_KEYWORDS)
        or any(keyword in raw_text for keyword in STORAGE_TRANSFER_NON_RULE_FAILURE_KEYWORDS)
    )


async def _recover_storage_bag_transfer_waiting_step(step, now):
    config = _storage_transfer_retry_config_for_step(step)
    if not config:
        return False
    msg_id_key = str(config.get("msg_id_key") or "")
    command_msg_id = int(_storage_bag_transfer_state.get(msg_id_key) or 0)
    if command_msg_id <= 0:
        return False
    replies = find_message_log_replies(
        command_msg_id,
        now,
        lookback_sec=max(15 * 60, STORAGE_TRANSFER_REPLY_TIMEOUT_SEC * 10),
        lookahead_sec=30,
        chat_id=get_sent_message_chat_id(
            command_msg_id,
            default=get_game_group_id(),
            send_as_id=int(config.get("identity_id") or 0),
        ),
        predicate=_is_storage_transfer_reply_log_entry,
    )
    if not replies:
        return False
    command = str(config.get("command") or "").strip()
    reply_to = SimpleNamespace(id=command_msg_id, raw_text=command)
    family = str(config.get("family") or "storage_bag_transfer")
    for entry in replies:
        handled = await handle_storage_bag_transfer_reply(
            entry.get("text") or "",
            float(entry.get("ts_epoch") or now),
            reply_to=reply_to,
            matched_family=family,
            reply_context={
                "family": family,
                "reply_to_msg_id": command_msg_id,
                "matched_via": "message_log_recovery",
            },
        )
        if handled:
            _storage_transfer_log(f"{config.get('label') or '命令'}日志补偿：已采纳消息ID={entry.get('message_id') or '无'}")
            return True
    return False


async def _retry_storage_bag_transfer_waiting_step(step):
    config = _storage_transfer_retry_config_for_step(step)
    if not config:
        return False
    retry_count = int(_storage_bag_transfer_state.get("retry_count") or 0)
    if retry_count >= STORAGE_TRANSFER_MAX_RETRY:
        return False
    retry_count += 1
    _storage_bag_transfer_state["retry_count"] = retry_count
    label = str(config.get("label") or "命令")
    command = str(config.get("command") or "").strip()
    _storage_transfer_log(f"{label}等待超时，补发第 {retry_count}/{STORAGE_TRANSFER_MAX_RETRY} 次：{command}")
    _record_storage_transfer_event(
        f"{label}补发",
        identity_id=int(config.get("identity_id") or 0),
        family=str(config.get("family") or "storage_bag_transfer"),
        command=command,
        step=str(step or ""),
        detail=f"第 {retry_count}/{STORAGE_TRANSFER_MAX_RETRY} 次",
        decision="storage_transfer_retry",
    )
    msg = await _send_storage_bag_transfer_command(
        command,
        identity_id=int(config.get("identity_id") or 0),
        msg_id_key=str(config.get("msg_id_key") or ""),
        wait_step=str(config.get("wait_step") or step),
        family=str(config.get("family") or "storage_bag_transfer"),
        reply_to=int(config.get("reply_to") or 0),
        retry=True,
    )
    if not msg:
        _storage_transfer_log(f"{label}补发发送失败，稍后继续检查", level="warning")
    elif not _is_storage_transfer_deferred_send(msg) and str(step or "") == "gift_marker":
        await _send_next_storage_bag_gift()
    return True


async def run_storage_bag_transfer_scheduler(now):
    if _storage_bag_transfer_batch_state.get("running") and not _storage_bag_transfer_state.get("running") and not _storage_bag_transfer_batch_state.get("active_task"):
        await _start_next_storage_bag_transfer_batch_task()
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
    if step in {"waiting_listing_reply", "waiting_buy_reply", "waiting_gift_reply", "gift_marker"}:
        if await _recover_storage_bag_transfer_waiting_step(step, now):
            save_state()
            return
        if await _retry_storage_bag_transfer_waiting_step(step):
            return
    if step in {"gift_marker", "gift_sending", "gift_waiting_interval", "waiting_gift_reply"}:
        await _delete_storage_bag_gift_locator()
    _record_storage_transfer_event(
        "等待超时",
        kind="skipped",
        reason="storage_bag_transfer_timeout",
        identity_id=int(_storage_bag_transfer_state.get("source_identity_id") or 0),
        step=step,
    )
    _finalize_storage_bag_transfer(False, f"储物袋转移等待回复超时：{step}")
    await send_audit_log(f"⚠️ 储物袋转移超时：{step}", limit=240)


async def handle_storage_bag_reply(text, now, reply_to=None, matched_family=None):
    if apply_storage_bag_gift_success(text):
        return True
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
    "STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX",
    "apply_storage_bag_gift_success",
    "apply_storage_bag_item_counts",
    "apply_storage_bag_item_deltas",
    "apply_storage_bag_item_text_delta",
    "cancel_storage_bag_transfer_task",
    "get_storage_bag_transfer_snapshot",
    "handle_storage_bag_reply",
    "handle_storage_bag_transfer_reply",
    "is_storage_transfer_waiting_reply",
    "parse_storage_bag_gift_success",
    "parse_storage_bag_item_counts",
    "parse_storage_bag_reply",
    "resolve_storage_bag_identity_id",
    "run_storage_bag_transfer_scheduler",
    "start_storage_bag_gift_batch",
    "start_storage_bag_gift_task",
    "start_storage_bag_transfer_batch",
    "start_storage_bag_transfer_task",
]
