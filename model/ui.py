import asyncio
import glob
import html
import importlib.util
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

try:
    import segno
except ImportError:
    segno = None
    project_root = os.path.dirname(os.path.dirname(__file__))
    for segno_init in sorted(glob.glob(os.path.join(project_root, ".venv", "lib", "python*", "site-packages", "segno", "__init__.py"))):
        try:
            package_dir = os.path.dirname(segno_init)
            spec = importlib.util.spec_from_file_location("segno", segno_init, submodule_search_locations=[package_dir])
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules["segno"] = module
            spec.loader.exec_module(module)
            segno = module
            break
        except Exception:
            sys.modules.pop("segno", None)
            segno = None

from .config import (
    CMD_TIANTI_GANGFENG,
    MESSAGES_DIR,
    MODULE_KEY_MAP,
    STARGAZER_STAR_CHOICES,
    TAIYI_VALID_ELEMENTS,
    TIANTI_RANK_CHOICES,
    TZ_LOCAL,
    UI_AUTH_COOKIE_NAME,
    UI_AUTH_IDLE_TIMEOUT_SEC,
    UI_AUTH_SESSION_TIMEOUT_SEC,
    UI_AUTO_REFRESH_SEC,
    UI_HOST,
    UI_PORT,
    UI_PUBLIC_BASE_URL,
    create_account_client,
    get_account_offline_reason,
    get_all_clients,
    get_registered_client,
    is_account_offline,
    register_client,
    unregister_client,
)
from .control import (
    delete_identity as delete_control_identity,
    get_identity_info_refresh_state,
    get_module_status_text,
    get_single_module_status_text,
    get_startup_module_alerts,
    refresh_identity_info,
    register_identity,
    set_identity_enabled as set_control_identity_enabled,
    set_module_enabled,
    set_module_window_config,
    toggle_global_enabled,
)
from .features.deep_retreat import get_deep_retreat_phase_text
from .features.guanxing import get_guanxing_round_summary_text
from .features.guanxing_monitor import get_guanxing_monitor_summary_text
from .features.jiyin import apply_jiyin_choice, get_jiyin_choice_label, normalize_jiyin_choice, resolve_jiyin_choice
from .features.nanlong import apply_nanlong_choice, get_nanlong_choice_label, normalize_nanlong_choice, resolve_nanlong_choice
from .features.stargazer import sync_stargazer_total_slots
from .features.storage_bag import CMD_STORAGE_BAG
from .features.tianti import sync_tianti_status
from .features.wild_training import apply_wild_training_strategy, normalize_wild_training_strategy
from .features.yuanying import get_yuanying_phase_text
from .official_schedule import (
    build_preset_plan as build_official_schedule_preset_plan,
    create_official_messages_for_batch,
    delete_local_schedule_records as delete_official_schedule_records,
    list_local_schedules as list_local_official_schedules,
    replace_planned_batch as replace_official_schedule_planned_batch,
)
from .persistence import save_state
from .runtime import consume_unseen_startup_alerts, console_log, fetch_forum_topics, get_game_send_queue_snapshot, redeem_ui_login_token, send_audit_log, send_game_command, touch_ui_session
from .state import (
    convert_window_hours_local_to_utc,
    format_window_text,
    get_accounts,
    get_available_module_names,
    get_forum_topics,
    get_forum_topics_updated_at,
    get_game_bot_ids,
    get_game_group_id,
    get_game_topic_id,
    get_global_enabled,
    get_tiandao_judgement_enabled,
    get_guanxing_monitor_enabled,
    get_guanxing_monitor_target_options,
    get_guanxing_monitor_targets,
    get_guanxing_shift_target,
    is_auto_delete_sent_messages_enabled,
    get_identity_display_name,
    get_identity_enabled,
    get_identity_ids,
    get_identity_account,
    get_identity_account_map,
    get_identity_ui_display_name,
    get_identity_state,
    get_module_window_hours_local,
    get_realm_sort_key,
    get_send_as_profile,
    get_stargazer_star_choice,
    get_stargazer_total_slots,
    get_storage_bag_item_rules,
    get_storage_bag_records,
    get_tianti_rank_choice,
    get_wild_training_strategy,
    set_account,
    set_accounts,
    set_auto_delete_sent_messages,
    set_tiandao_judgement_enabled,
    set_forum_topics,
    set_game_bot_ids,
    set_game_group_id,
    set_game_topic_id,
    set_guanxing_monitor_enabled,
    set_guanxing_monitor_targets,
    set_guanxing_shift_target,
    set_identity_account,
    set_identity_account_map,
    set_identity_enabled as set_identity_enabled_profile,
    set_pet_name,
    set_pet_warm_name,
    set_pet_trial_name,
    set_storage_bag_item_rules,
    set_stargazer_star_choice,
    set_tianti_rank_choice,
    state,
    use_identity,
)
from .timing import fmt_abs_ts

_ui_server = None
_STORAGE_BAG_TRANSFER_METHODS = {"basic", "gift", "blocked", "unknown"}
_STORAGE_BAG_DEFAULT_TAG = "未知"
_STORAGE_BAG_DEFAULT_TAGS = ["未知", "灵草", "种子", "丹药", "材料", "装备", "特殊"]
UI_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
UI_FAVICON_PNG_PATH = os.path.join(UI_PROJECT_ROOT, "favicon.png")
UI_STORAGE_BAG_ITEM_RULES_PATH = os.path.join(UI_PROJECT_ROOT, "data", "storage_bag_item_rules.json")
UI_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
UI_TEMPLATE_DIR = os.path.join(UI_WEB_DIR, "pages")
UI_STATIC_DIR = os.path.join(UI_WEB_DIR, "static")
UI_STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
_storage_bag_sync_state = {"running": False, "pending_ids": [], "completed_ids": []}
_LOG_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")


def _is_storage_bag_protected_identity(send_as_id):
    profile = get_send_as_profile(send_as_id)
    candidates = (
        profile.get("username"),
        profile.get("label"),
        profile.get("daohao"),
        get_identity_ui_display_name(send_as_id),
    )
    return any("wa2000" in str(candidate or "").casefold() for candidate in candidates)


def _format_storage_bag_updated_at(record):
    try:
        updated_at = float((record or {}).get("updated_at") or 0)
    except (TypeError, ValueError):
        updated_at = 0
    if updated_at <= 0:
        return (record or {}).get("updated_at_text") or "未解析"
    return datetime.fromtimestamp(updated_at, TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S")


_storage_bag_base_item_rules_cache = None
_storage_bag_base_item_rules_mtime = None


def _normalize_storage_bag_item_rule(item_name, raw_rule=None):
    rule = raw_rule if isinstance(raw_rule, dict) else {}
    method = str(rule.get("method") or "unknown").strip().lower()
    if method not in _STORAGE_BAG_TRANSFER_METHODS:
        method = "unknown"
    tags = rule.get("tags") if isinstance(rule.get("tags"), list) else []
    normalized_tags = []
    seen = set()
    for raw_tag in tags:
        tag = str(raw_tag or "").strip()
        if tag and tag not in seen:
            seen.add(tag)
            normalized_tags.append(tag)
    if not normalized_tags:
        normalized_tags = [_STORAGE_BAG_DEFAULT_TAG]
    return {
        "item_name": str(item_name or ""),
        "method": method,
        "tags": normalized_tags,
        "reason": str(rule.get("reason") or "").strip(),
    }


def _load_storage_bag_base_item_rules():
    global _storage_bag_base_item_rules_cache, _storage_bag_base_item_rules_mtime
    try:
        stat = os.stat(UI_STORAGE_BAG_ITEM_RULES_PATH)
    except OSError:
        _storage_bag_base_item_rules_cache = {}
        _storage_bag_base_item_rules_mtime = None
        return {}
    if _storage_bag_base_item_rules_cache is not None and _storage_bag_base_item_rules_mtime == stat.st_mtime:
        return _storage_bag_base_item_rules_cache
    try:
        with open(UI_STORAGE_BAG_ITEM_RULES_PATH, "r", encoding="utf-8") as fp:
            raw_data = json.load(fp)
    except Exception:
        _storage_bag_base_item_rules_cache = {}
        _storage_bag_base_item_rules_mtime = stat.st_mtime
        return {}
    raw_items = raw_data.get("items") if isinstance(raw_data, dict) and isinstance(raw_data.get("items"), dict) else {}
    rules = {}
    for raw_item_name, raw_rule in raw_items.items():
        item_name = str(raw_item_name or "").strip()
        if item_name:
            rules[item_name] = _normalize_storage_bag_item_rule(item_name, raw_rule)
    _storage_bag_base_item_rules_cache = rules
    _storage_bag_base_item_rules_mtime = stat.st_mtime
    return rules


def _get_storage_bag_item_rule(item_name):
    item_name = str(item_name or "").strip()
    base_rule = _load_storage_bag_base_item_rules().get(item_name)
    saved_rule = get_storage_bag_item_rules().get(item_name)
    if isinstance(base_rule, dict) and isinstance(saved_rule, dict):
        raw_rule = {**base_rule, **saved_rule}
    elif isinstance(saved_rule, dict):
        raw_rule = saved_rule
    else:
        raw_rule = base_rule
    return _normalize_storage_bag_item_rule(item_name, raw_rule)


def _storage_bag_transfer_method_label(method):
    return {
        "basic": "买卖",
        "gift": "赠送",
        "blocked": "不可转移",
        "unknown": "未知",
    }.get(str(method or "unknown"), "未知")


def _format_storage_bag_identity_options(rows):
    return [
        {
            "identity_id": int(row.get("identity_id") or 0),
            "label": row.get("label") or row.get("display_name") or str(row.get("identity_id") or ""),
            "protected": bool(row.get("protected")),
        }
        for row in rows or []
    ]


def _get_storage_bag_item_count(rows, identity_id, item_name):
    identity_id = int(identity_id or 0)
    item_name = str(item_name or "")
    for row in rows or []:
        if int(row.get("identity_id") or 0) != identity_id:
            continue
        return int((row.get("items") or {}).get(item_name) or 0)
    return 0


def get_storage_bag_sync_snapshot():
    return {
        "running": bool(_storage_bag_sync_state.get("running")),
        "pending_ids": list(_storage_bag_sync_state.get("pending_ids") or []),
        "completed_ids": list(_storage_bag_sync_state.get("completed_ids") or []),
    }


async def _run_storage_bag_sync(identity_ids):
    try:
        for identity_id in identity_ids:
            _storage_bag_sync_state["pending_ids"] = [
                item for item in _storage_bag_sync_state.get("pending_ids", []) if int(item or 0) != int(identity_id)
            ]
            msg = await send_game_command(CMD_STORAGE_BAG, send_as_id=int(identity_id), priority="normal", max_retry=1)
            if msg:
                _storage_bag_sync_state.setdefault("completed_ids", []).append(int(identity_id))
    finally:
        _storage_bag_sync_state["running"] = False
        _storage_bag_sync_state["pending_ids"] = []


async def ui_start_storage_bag_sync(identity_ids):
    if _storage_bag_sync_state.get("running"):
        return False, "储物袋同步正在进行中"
    normalized_ids = []
    for raw_id in identity_ids or []:
        try:
            identity_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if identity_id not in get_identity_ids():
            continue
        if _is_storage_bag_protected_identity(identity_id):
            continue
        if identity_id not in normalized_ids:
            normalized_ids.append(identity_id)
    if not normalized_ids:
        return False, "请至少勾选一个非保护身份"
    _storage_bag_sync_state["running"] = True
    _storage_bag_sync_state["pending_ids"] = list(normalized_ids)
    _storage_bag_sync_state["completed_ids"] = []
    asyncio.create_task(_run_storage_bag_sync(normalized_ids))
    return True, f"已开始同步 {len(normalized_ids)} 个身份的储物袋"


def ui_set_storage_bag_item_rule(item_name, method, tags=None, reason=""):
    item_name = str(item_name or "").strip()
    if not item_name:
        return False, "物品名不能为空"
    method = str(method or "unknown").strip().lower()
    if method not in _STORAGE_BAG_TRANSFER_METHODS:
        return False, "无效的转移方式"
    rules = dict(get_storage_bag_item_rules())
    previous_rule = _normalize_storage_bag_item_rule(item_name, rules.get(item_name))
    if tags is None:
        normalized_tags = previous_rule.get("tags") or [_STORAGE_BAG_DEFAULT_TAG]
    else:
        raw_tags = tags.replace("，", ",").split(",") if isinstance(tags, str) else tags or []
        normalized_tags = []
        seen = set()
        for raw_tag in raw_tags:
            tag = str(raw_tag or "").strip()
            if tag and tag not in seen:
                seen.add(tag)
                normalized_tags.append(tag)
        if not normalized_tags:
            normalized_tags = [_STORAGE_BAG_DEFAULT_TAG]
    normalized_reason = previous_rule.get("reason") or "" if reason is None else str(reason or "").strip()
    rules[item_name] = {
        "method": method,
        "tags": normalized_tags,
        "reason": normalized_reason,
        "updated_at": time.time(),
    }
    set_storage_bag_item_rules(rules)
    save_state()
    return True, f"已更新物品规则：{item_name}"


def ui_preview_storage_bag_transfer(payload):
    payload = payload if isinstance(payload, dict) else {}
    try:
        source_identity_id = int(payload.get("source_identity_id") or 0)
        target_identity_id = int(payload.get("target_identity_id") or 0)
    except (TypeError, ValueError):
        return False, "身份参数无效", None
    known_ids = set(int(item) for item in get_identity_ids())
    if source_identity_id not in known_ids:
        return False, "来源身份无效", None
    if target_identity_id not in known_ids:
        return False, "目标身份无效", None
    if source_identity_id == target_identity_id:
        return False, "来源和目标身份不能相同", None

    storage_bag = get_storage_bag_snapshot()
    rows = storage_bag.get("rows") or []
    selected_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not selected_items:
        return False, "请至少选择一个转移物品", None

    normalized_items = []
    exchange_parts = []
    gift_items = []
    for raw_item in selected_items:
        if not isinstance(raw_item, dict):
            continue
        item_name = str(raw_item.get("item_name") or "").strip()
        if not item_name:
            continue
        try:
            quantity = int(raw_item.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        source_count = _get_storage_bag_item_count(rows, source_identity_id, item_name)
        target_count = _get_storage_bag_item_count(rows, target_identity_id, item_name)
        rule = _get_storage_bag_item_rule(item_name)
        method = rule.get("method") or "unknown"
        if method == "blocked":
            return False, f"{item_name} 不可转移", None
        if quantity <= 0 or quantity > source_count:
            return False, f"{item_name} 数量必须在 1 到 {source_count} 之间", None
        item = {
            "item_name": item_name,
            "quantity": quantity,
            "source_count": source_count,
            "target_count": target_count,
            "method": method,
            "method_label": _storage_bag_transfer_method_label(method),
            "tags": rule.get("tags") or [_STORAGE_BAG_DEFAULT_TAG],
        }
        normalized_items.append(item)
        if method == "gift":
            gift_items.append(item)
        else:
            exchange_parts.append(f"{item_name}*{quantity}")

    if not normalized_items:
        return False, "请至少选择一个有效物品", None

    listing_item = str(payload.get("listing_item") or "").strip()
    listing_count = 0
    commands = []
    if exchange_parts:
        if not listing_item:
            return False, "请选择目标身份用于上架的物品", None
        listing_count = _get_storage_bag_item_count(rows, target_identity_id, listing_item)
        if listing_count <= 0:
            return False, "目标身份没有该上架物", None
        commands.extend([
            {
                "identity_id": target_identity_id,
                "command": f".上架 {listing_item} 1 换 {' '.join(exchange_parts)}",
                "note": "目标身份上架换购物品",
            },
            {
                "identity_id": source_identity_id,
                "command": ".购买 <挂单ID>",
                "note": "上架成功后来源身份购买挂单",
            },
        ])
    if gift_items:
        commands.append({
            "identity_id": target_identity_id,
            "command": "转移标记 <本次转移ID>",
            "note": "目标身份先发送一条可回复的标记消息",
        })
        for item in gift_items:
            commands.append({
                "identity_id": source_identity_id,
                "command": f".赠送 {item['item_name']} {item['quantity']}",
                "note": "来源身份回复目标身份标记消息发送",
            })

    preview = {
        "source_identity_id": source_identity_id,
        "target_identity_id": target_identity_id,
        "listing_item": listing_item,
        "listing_count": listing_count,
        "items": normalized_items,
        "commands": commands,
        "summary": f"预览 {len(normalized_items)} 个物品，当前只生成命令，不会自动发送",
    }
    return True, "已生成转移预览", preview


def get_storage_bag_snapshot():
    records = get_storage_bag_records()
    rows = []
    item_names = set()
    totals = {}
    for identity_id in get_identity_ids():
        identity_id = int(identity_id)
        profile = get_send_as_profile(identity_id)
        record = records.get(str(identity_id)) or {}
        items = record.get("items") if isinstance(record, dict) else {}
        items = items if isinstance(items, dict) else {}
        item_names.update(str(name) for name in items.keys())
        normalized_items = {str(name): int(count or 0) for name, count in items.items()}
        for name, count in normalized_items.items():
            totals[name] = totals.get(name, 0) + int(count or 0)
        label = profile.get("label") or profile.get("username") or str(identity_id)
        rows.append({
            "identity_id": identity_id,
            "label": label,
            "display_name": get_identity_ui_display_name(identity_id),
            "protected": _is_storage_bag_protected_identity(identity_id),
            "updated_at": _format_storage_bag_updated_at(record),
            "updated_at_raw": float((record or {}).get("updated_at") or 0),
            "items": normalized_items,
            "empty": bool((record or {}).get("empty")),
        })
    rows.sort(key=lambda row: get_realm_sort_key(get_send_as_profile(row["identity_id"]).get("realm"), row["identity_id"]))
    sorted_item_names = sorted(item_names, key=lambda name: (name != "灵石", name))
    item_rules = {}
    for item_name in sorted_item_names:
        rule = _get_storage_bag_item_rule(item_name)
        item_rules[item_name] = {
            **rule,
            "method_label": _storage_bag_transfer_method_label(rule.get("method")),
            "transfer_visible": rule.get("method") != "blocked",
            "transfer_selectable": rule.get("method") != "blocked",
        }
    return {
        "rows": rows,
        "items": sorted_item_names,
        "totals": totals,
        "item_rules": item_rules,
        "rule_methods": ["basic", "gift", "blocked", "unknown"],
        "default_tags": list(_STORAGE_BAG_DEFAULT_TAGS),
        "transfer_identities": _format_storage_bag_identity_options(rows),
    }


def _list_message_log_days():
    days = []
    try:
        for file_name in os.listdir(MESSAGES_DIR):
            matched = _LOG_FILE_RE.match(file_name)
            if matched:
                days.append(matched.group(1))
    except OSError:
        return []
    return sorted(days, reverse=True)


def _read_log_entries(date_str, q_text="", types_set=None, sender_id=0, offset=0, limit=80, newest_first=True):
    date_str = str(date_str or "").strip()
    if not date_str or not _LOG_FILE_RE.match(f"{date_str}.log"):
        return {"entries": [], "total": 0, "has_more": False, "offset": 0, "limit": int(limit or 80)}

    full_path = os.path.abspath(os.path.join(MESSAGES_DIR, f"{date_str}.log"))
    messages_dir = os.path.abspath(MESSAGES_DIR)
    if not full_path.startswith(messages_dir + os.sep) or not os.path.isfile(full_path):
        return {"entries": [], "total": 0, "has_more": False, "offset": 0, "limit": int(limit or 80)}

    q_text = str(q_text or "").strip().casefold()
    types_set = {str(item or "").strip() for item in (types_set or []) if str(item or "").strip()}
    try:
        sender_id = int(sender_id or 0)
    except (TypeError, ValueError):
        sender_id = 0
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = min(200, max(1, int(limit or 80)))
    except (TypeError, ValueError):
        limit = 80

    entries = []
    try:
        with open(full_path, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                event_type = str(item.get("event_type") or "")
                text = str(item.get("text") or "")
                if types_set and event_type not in types_set:
                    continue
                if sender_id and int(item.get("sender_id") or 0) != sender_id:
                    continue
                if q_text:
                    haystack = "\n".join(
                        [
                            str(item.get("ts") or ""),
                            event_type,
                            str(item.get("sender_id") or ""),
                            str(item.get("message_id") or ""),
                            text,
                        ]
                    ).casefold()
                    if q_text not in haystack:
                        continue
                entries.append(
                    {
                        "ts": str(item.get("ts") or ""),
                        "event_type": event_type,
                        "message_id": int(item.get("message_id") or 0),
                        "sender_id": int(item.get("sender_id") or 0),
                        "topic_id": int(item.get("topic_id") or 0),
                        "reply_to_msg_id": int(item.get("reply_to_msg_id") or 0),
                        "text": text,
                    }
                )
    except OSError:
        entries = []

    if newest_first:
        entries.reverse()
    total = len(entries)
    sliced = entries[offset: offset + limit]
    return {
        "entries": sliced,
        "total": total,
        "has_more": offset + limit < total,
        "offset": offset,
        "limit": limit,
    }


def get_identity_ui_snapshot(send_as_id):
    send_as_id = int(send_as_id)
    now = time.time()
    identity_enabled = get_identity_enabled(send_as_id)
    global_enabled = get_global_enabled()
    account_id = int(get_identity_account(send_as_id) or 0)
    account_offline = bool(account_id and is_account_offline(account_id))
    account_offline_reason = get_account_offline_reason(account_id) if account_offline else ""
    with use_identity(send_as_id):
        identity_state = get_identity_state(send_as_id)
        profile = get_send_as_profile(send_as_id)
        modules = []
        available_module_names = get_available_module_names(send_as_id)
        for module_name in available_module_names:
            configured_enabled = bool(identity_state.get(MODULE_KEY_MAP[module_name], False))
            effective_enabled = bool(global_enabled and identity_enabled and configured_enabled and not account_offline)
            effective_reason = ""
            if configured_enabled and account_offline:
                effective_reason = f"账号离线，调度已跳过；原因：{account_offline_reason or '账号不可用'}"
            elif configured_enabled and not global_enabled:
                effective_reason = "全局已暂停，恢复后会按保存状态继续运行。"
            elif configured_enabled and not identity_enabled:
                effective_reason = "当前身份已暂停，该模块配置已保留，重新开启身份后会按保存状态恢复运行。"
            elif not configured_enabled:
                effective_reason = "模块已关闭"
            modules.append({
                "name": module_name,
                "enabled": configured_enabled,
                "effective_enabled": effective_enabled,
                "effective_reason": effective_reason,
                "detail": _format_module_detail_for_ui(module_name, get_single_module_status_text(module_name, send_as_id)),
            })
        checkin_window_local = get_module_window_hours_local("点卯", send_as_id)
        tower_window_local = get_module_window_hours_local("闯塔", send_as_id)
        sect_refresh_state = get_identity_info_refresh_state(send_as_id)
        sect_refresh_pending = bool(sect_refresh_state.get("pending"))
        sect_refresh_error = sect_refresh_state.get("error") or ""
        saved_jiyin_choice = normalize_jiyin_choice(profile.get("jiyin_choice") or "")
        effective_jiyin_choice, _jiyin_choice_source = resolve_jiyin_choice(send_as_id)
        jiyin_reply_to_msg_id = int(identity_state.get("jiyin_reply_to_msg_id", 0) or 0)
        saved_nanlong_choice = normalize_nanlong_choice(profile.get("nanlong_choice") or "")
        effective_nanlong_choice, _nanlong_choice_source = resolve_nanlong_choice(send_as_id)
        nanlong_reply_to_msg_id = int(identity_state.get("nanlong_reply_to_msg_id", 0) or 0)
        stargazer_followup_due_at = float(identity_state.get("stargazer_followup_due_at", 0) or 0)
        stargazer_next_panel_time = float(identity_state.get("next_stargazer_panel_time", 0) or 0)
        if stargazer_followup_due_at > 0 and stargazer_next_panel_time > 0:
            stargazer_next_action_time = min(stargazer_followup_due_at, stargazer_next_panel_time)
        else:
            stargazer_next_action_time = stargazer_followup_due_at or stargazer_next_panel_time
        pending_tasks = []
        for msg_id, item in sorted(
            (identity_state.get("pending_tasks") or {}).items(),
            key=lambda pair: float((pair[1] or {}).get("sent_at", 0) or 0),
        ):
            pending_tasks.append({
                "msg_id": int(msg_id or 0),
                "cmd": str((item or {}).get("cmd") or ""),
                "retry": int((item or {}).get("retry", 0) or 0),
                "max_retry": int((item or {}).get("max_retry", 0) or 0),
                "priority": str((item or {}).get("priority") or ""),
                "sent_at": fmt_abs_ts((item or {}).get("sent_at", 0) or 0),
                "timeout_sec": int((item or {}).get("timeout", 0) or 0),
                "reply_to_msg_id": int((item or {}).get("reply_to_msg_id", 0) or 0),
            })
        jiyin_deadline_at = float(identity_state.get("next_jiyin_time", 0) or 0)
        nanlong_deadline_at = float(identity_state.get("next_nanlong_time", 0) or 0)
        nanlong_reply_due_at = float(identity_state.get("nanlong_reply_due_at", 0) or 0)
        identity_status_text = "运行中"
        if not global_enabled:
            identity_status_text = "全局暂停"
        elif not identity_enabled:
            identity_status_text = "已暂停"
        elif account_offline:
            identity_status_text = "账号离线"

        snapshot = {
            "send_as_id": send_as_id,
            "display_name": get_identity_ui_display_name(send_as_id),
            "identity_enabled": identity_enabled,
            "identity_status_text": identity_status_text,
            "account_id": account_id,
            "account_offline": account_offline,
            "account_offline_reason": account_offline_reason,
            "username": profile.get("username") or "",
            "label": profile.get("label") or "",
            "daohao": profile.get("daohao") or "",
            "realm": profile.get("realm") or "",
            "spiritual_root_type": profile.get("spiritual_root_type") or "",
            "spiritual_root_attrs": profile.get("spiritual_root_attrs") or "",
            "replica_professions": profile.get("replica_professions") or "",
            "pet_name": profile.get("pet_name") or "",
            "pet_warm_name": profile.get("pet_warm_name") or profile.get("pet_name") or "",
            "pet_trial_name": profile.get("pet_trial_name") or profile.get("pet_name") or "",
            "sect_name": profile.get("sect_name") or "",
            "xiuwei_current": int(profile.get("xiuwei_current") or 0),
            "xiuwei_max": int(profile.get("xiuwei_max") or 0),
            "sect_updated_at": fmt_abs_ts(profile.get("sect_updated_at") or 0),
            "sect_refresh_pending": sect_refresh_pending,
            "sect_refresh_error": sect_refresh_error,
            "jiyin_choice": saved_jiyin_choice,
            "jiyin_choice_label": get_jiyin_choice_label(saved_jiyin_choice),
            "nanlong_choice": saved_nanlong_choice,
            "nanlong_choice_label": get_nanlong_choice_label(saved_nanlong_choice),
            "stargazer_star_choice": get_stargazer_star_choice(send_as_id),
            "stargazer_star_choices": list(STARGAZER_STAR_CHOICES),
            "stargazer_total_slots": get_stargazer_total_slots(send_as_id),
            "tianti_rank_choice": get_tianti_rank_choice(send_as_id),
            "tianti_rank_choices": list(TIANTI_RANK_CHOICES),
            "wild_training_strategy": get_wild_training_strategy(send_as_id),
            "wild_training_strategy_choices": ["谨慎", "均衡", "深入"],
            "second_soul_auto_choice_enabled": bool(identity_state.get("second_soul_auto_choice_enabled", True)),
            "second_soul_choice_strategy": identity_state.get("second_soul_choice_strategy") or "stable",
            "second_soul_choice_strategy_choices": [
                {"value": "stable", "label": "稳固道心"},
                {"value": "break", "label": "强行突破"},
            ],
            "tianti_cycle_count": int(identity_state.get("tianti_cycle_count", 0) or 0),
            "tianti_wenxin_enabled": bool(identity_state.get("tianti_wenxin_enabled", True)),
            "tianti_gangfeng_enabled": bool(identity_state.get("tianti_gangfeng_enabled", True)),
            "small_world_preach_enabled": bool(identity_state.get("small_world_preach_enabled", True)),
            "small_world_manifest_enabled": bool(identity_state.get("small_world_manifest_enabled", False)),
            "small_world_harvest_enabled": bool(identity_state.get("small_world_harvest_enabled", False)),
            "small_world_refine_enabled": bool(identity_state.get("small_world_refine_enabled", False)),
            "small_world_refresh_enabled": bool(identity_state.get("small_world_refresh_enabled", False)),
            "jiyin_effective_choice": effective_jiyin_choice,
            "jiyin_effective_choice_label": get_jiyin_choice_label(effective_jiyin_choice),
            "jiyin_choice_source": _jiyin_choice_source,
            "jiyin_pending": bool(jiyin_reply_to_msg_id > 0 and jiyin_deadline_at > now),
            "jiyin_deadline_at": fmt_abs_ts(jiyin_deadline_at),
            "jiyin_reply_to_msg_id": jiyin_reply_to_msg_id,
            "jiyin_last_error": identity_state.get("jiyin_last_error") or "",
            "nanlong_effective_choice": effective_nanlong_choice,
            "nanlong_effective_choice_label": get_nanlong_choice_label(effective_nanlong_choice),
            "nanlong_choice_source": _nanlong_choice_source,
            "nanlong_pending": bool(nanlong_reply_to_msg_id > 0 and nanlong_deadline_at > now),
            "nanlong_deadline_at": fmt_abs_ts(nanlong_deadline_at),
            "nanlong_reply_due_at": fmt_abs_ts(nanlong_reply_due_at),
            "nanlong_reply_to_msg_id": nanlong_reply_to_msg_id,
            "nanlong_last_error": identity_state.get("nanlong_last_error") or "",
            "checkin_window_local": {
                "start_hour": checkin_window_local[0],
                "end_hour": checkin_window_local[1],
                "text": format_window_text("点卯", send_as_id),
            },
            "tower_window_local": {
                "start_hour": tower_window_local[0],
                "end_hour": tower_window_local[1],
                "text": format_window_text("闯塔", send_as_id),
            },
            "module_summary": get_module_status_text(send_as_id),
            "modules": modules,
            "timers": {
                "next_irr_time": fmt_abs_ts(identity_state.get("next_irr_time", 0)),
                "next_pet_time": fmt_abs_ts(identity_state.get("next_pet_time", 0)),
                "next_stargazer_panel_time": fmt_abs_ts(identity_state.get("next_stargazer_panel_time", 0)),
                "next_stargazer_action_time": fmt_abs_ts(stargazer_next_action_time),
                "stargazer_followup_due_at": fmt_abs_ts(stargazer_followup_due_at),
                "stargazer_collect_due_at": fmt_abs_ts(identity_state.get("stargazer_collect_due_at", 0)),
                "next_quiz_time": fmt_abs_ts(identity_state.get("next_quiz_time", 0)),
                "next_checkin_time": fmt_abs_ts(identity_state.get("next_checkin_time", 0)),
                "next_tower_time": fmt_abs_ts(identity_state.get("next_tower_time", 0)),
                "next_deep_retreat_time": fmt_abs_ts(identity_state.get("next_deep_retreat_time", 0)),
                "next_yuanying_time": fmt_abs_ts(identity_state.get("next_yuanying_time", 0)),
            },
            "phases": {
                "yuanying": get_yuanying_phase_text(identity_state.get("yuanying_phase"), now),
                "deep_retreat": get_deep_retreat_phase_text(identity_state.get("deep_retreat_phase"), now),
            },
            "taiyi_yindao_element": identity_state.get("taiyi_yindao_element", "水"),
            "taiyi_yindao_choices": sorted(TAIYI_VALID_ELEMENTS, key=lambda e: ["金","木","水","火","土"].index(e)),
            "taiyi_node_search_enabled": bool(identity_state.get("taiyi_node_search_enabled", False)),
            "pending_tasks": pending_tasks,
            "pending_task_count": len(pending_tasks),
            "message_count": len(identity_state.get("my_msg_ids", {})),
        }
    return snapshot


def get_ui_snapshot(session_token=None):
    identities = sorted(
        (get_identity_ui_snapshot(identity_id) for identity_id in get_identity_ids()),
        key=lambda identity: get_realm_sort_key(
            identity.get("realm"),
            identity.get("send_as_id"),
            xiuwei_max=identity.get("xiuwei_max", 0),
            xiuwei_current=identity.get("xiuwei_current", 0),
        ),
    )
    startup_alerts = get_startup_module_alerts()
    if session_token:
        startup_alerts = consume_unseen_startup_alerts(session_token, startup_alerts)
    return {
        "generated_at": datetime.now(TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "ui_url": UI_PUBLIC_BASE_URL,
        "account_user_id": state.get("my_user_id") or 0,
        "game_group_id": get_game_group_id(),
        "game_bot_ids": get_game_bot_ids(),
        "game_topic_id": get_game_topic_id(),
        "forum_topics": get_forum_topics(),
        "forum_topics_updated_at": fmt_abs_ts(get_forum_topics_updated_at()),
        "auto_delete_sent_messages": is_auto_delete_sent_messages_enabled(),
        "global_enabled": get_global_enabled(),
        "tiandao_judgement_enabled": get_tiandao_judgement_enabled(),
        "guanxing_monitor_enabled": get_guanxing_monitor_enabled(),
        "guanxing_monitor_target_options": get_guanxing_monitor_target_options(),
        "guanxing_monitor_targets": get_guanxing_monitor_targets(),
        "guanxing_shift_target": get_guanxing_shift_target(),
        "guanxing_monitor_summary": get_guanxing_monitor_summary_text(),
        "guanxing_round_summary": get_guanxing_round_summary_text(),
        "auth_idle_timeout_sec": UI_AUTH_IDLE_TIMEOUT_SEC,
        "refresh_interval_sec": UI_AUTO_REFRESH_SEC,
        "startup_alerts": startup_alerts,
        "game_send_queue": [
            {
                **item,
                "enqueued_at": fmt_abs_ts((item or {}).get("enqueued_at") or 0),
                "not_before_at": fmt_abs_ts((item or {}).get("not_before_at") or 0),
            }
            for item in get_game_send_queue_snapshot()
        ],
        "official_schedules": list_local_official_schedules(limit=200),
        "storage_bag": get_storage_bag_snapshot(),
        "storage_bag_sync": get_storage_bag_sync_snapshot(),
        "accounts": _get_runtime_accounts_snapshot(),
        "identities": identities,
        "config_needed": not get_game_group_id() or not get_game_bot_ids(),
    }


def ui_preview_official_schedule(payload):
    send_as_id = payload.get("send_as_id")
    template_key = str(payload.get("template_key") or "").strip()
    if send_as_id in {None, ""}:
        return False, "缺少 send_as_id", None
    if not template_key:
        return False, "缺少 template_key", None
    try:
        send_as_id = int(send_as_id)
    except (TypeError, ValueError):
        return False, "send_as_id 无效", None
    profile = get_send_as_profile(send_as_id)
    with use_identity(send_as_id):
        identity_state = get_identity_state(send_as_id)
        inferred_anchor_at = None
        now = time.time()
        if template_key == "deep_retreat":
            next_time = float(identity_state.get("next_deep_retreat_time", 0) or 0)
            inferred_anchor_at = next_time - 8 * 3600 if next_time > now else now
        elif template_key == "pet_touch":
            next_time = float(identity_state.get("next_pet_time", 0) or 0)
            inferred_anchor_at = next_time - 2 * 3600 if next_time > now else now
        elif template_key == "pet_warm":
            next_time = float(identity_state.get("next_pet_warm_time", 0) or 0)
            inferred_anchor_at = next_time - 6 * 3600 if next_time > now else now
        elif template_key == "pet_trial":
            next_time = float(identity_state.get("next_pet_trial_time", 0) or 0)
            inferred_anchor_at = next_time - 8 * 3600 if next_time > now else now
    pet_name = str(payload.get("pet_name") or "").strip()
    if not pet_name:
        if template_key == "pet_warm":
            pet_name = profile.get("pet_warm_name") or profile.get("pet_name") or ""
        elif template_key == "pet_trial":
            pet_name = profile.get("pet_trial_name") or profile.get("pet_name") or ""
        else:
            pet_name = profile.get("pet_name") or ""
    plan = build_official_schedule_preset_plan(
        template_key,
        anchor_at=payload.get("anchor_at") or inferred_anchor_at,
        horizon_days=payload.get("horizon_days") or 3,
        pet_name=pet_name,
    )
    if not plan.get("template_key"):
        return False, "未知官方定时预设", None
    return True, f"已生成 {len(plan.get('items') or [])} 条官方定时预览", plan


def ui_prepare_official_schedule(payload):
    ok, message, plan = ui_preview_official_schedule(payload)
    if not ok:
        return ok, message, None
    send_as_id = int(payload.get("send_as_id"))
    items = plan.get("items") or []
    if not items:
        return False, "预设没有生成任何定时消息", None
    existing = [
        item for item in list_local_official_schedules(send_as_id=send_as_id, include_inactive=False, limit=300)
        if item.get("template_key") == plan.get("template_key") and int(item.get("scheduled_msg_id") or 0) > 0
    ]
    if existing:
        return False, "该身份/预设已有 Telegram 官方定时消息，请先在排班器中删除旧批次再重新准备", None
    batch_id = replace_official_schedule_planned_batch(
        send_as_id,
        plan.get("template_key"),
        items,
        anchor_at=plan.get("anchor_at"),
        horizon_days=plan.get("horizon_days") or 3,
        options={
            "prepared_only": True,
            "pet_name": str(payload.get("pet_name") or "").strip(),
        },
        source="ui_prepare",
    )
    plan["batch_id"] = batch_id
    return True, f"已准备官方定时排班 {batch_id}，尚未创建 Telegram 官方定时消息", plan


def html_escape(value):
    return html.escape(str(value or ""), quote=True)


def _format_module_detail_for_ui(module_name, detail_text):
    lines = str(detail_text or "").splitlines()

    filtered_lines = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if index == 0 and stripped.startswith("👤 "):
            continue
        if index <= 1 and module_name and module_name in stripped:
            continue
        if stripped.startswith("- 当前名称：") or stripped.startswith("- 抚摸名称：") or stripped.startswith("- 温养名称：") or stripped.startswith("- 试炼名称："):
            continue
        if stripped.startswith("- 执行窗口："):
            continue
        filtered_lines.append(line)

    text = "\n".join(filtered_lines).strip()
    return text or "暂无详情"


def _resolve_selected_send_as_id(snapshot, selected_send_as_id=None):
    identity_ids = [identity["send_as_id"] for identity in snapshot.get("identities", [])]
    if not identity_ids:
        return None
    try:
        selected_id = int(selected_send_as_id)
    except (TypeError, ValueError):
        selected_id = None
    if selected_id in identity_ids:
        return selected_id
    return identity_ids[0]


def _cookie_is_secure():
    return UI_PUBLIC_BASE_URL.lower().startswith("https://")


def _load_ui_template(template_name):
    with open(os.path.join(UI_TEMPLATE_DIR, template_name), "r", encoding="utf-8") as fp:
        return fp.read()


def _render_ui_template(template_name, context):
    template = _load_ui_template(template_name)
    for key, value in context.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


def _load_ui_static_asset(asset_path):
    normalized_path = (asset_path or "").lstrip("/")
    asset_full_path = os.path.normpath(os.path.join(UI_STATIC_DIR, normalized_path))
    if not asset_full_path.startswith(UI_STATIC_DIR + os.sep):
        return None, None
    if not os.path.isfile(asset_full_path):
        return None, None
    content_type = UI_STATIC_CONTENT_TYPES.get(os.path.splitext(asset_full_path)[1].lower())
    if not content_type:
        return None, None
    with open(asset_full_path, "rb") as fp:
        return fp.read(), content_type


def _build_session_cookie_header(session_token, *, clear=False):
    cookie = SimpleCookie()
    cookie[UI_AUTH_COOKIE_NAME] = "" if clear else (session_token or "")
    morsel = cookie[UI_AUTH_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    if _cookie_is_secure():
        morsel["secure"] = True
    if clear:
        morsel["max-age"] = 0
        morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    else:
        morsel["max-age"] = int(UI_AUTH_SESSION_TIMEOUT_SEC)
    return morsel.OutputString()


def _parse_cookies(headers):
    cookie = SimpleCookie()
    raw_cookie = headers.get("cookie", "")
    if raw_cookie:
        cookie.load(raw_cookie)
    return {key: morsel.value for key, morsel in cookie.items()}


def _get_authenticated_session(headers, now=None):
    if now is None:
        now = time.time()
    cookies = _parse_cookies(headers)
    session_token = (cookies.get(UI_AUTH_COOKIE_NAME) or "").strip()
    if not session_token:
        return None, None
    session = touch_ui_session(session_token, now)
    if not session:
        return None, _build_session_cookie_header("", clear=True)
    return session, _build_session_cookie_header(session["session_token"], clear=False)


def _parse_request_body(headers, body_bytes):
    if not body_bytes:
        return {}
    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    body_text = body_bytes.decode("utf-8", errors="ignore")
    if content_type == "application/json":
        try:
            data = json.loads(body_text or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    if content_type == "application/x-www-form-urlencoded":
        parsed = parse_qs(body_text, keep_blank_values=False)
        return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
    return {}


def _make_json_payload(ok, *, message="", error="", snapshot=None, extra=None):
    payload = {"ok": bool(ok)}
    if message:
        payload["message"] = message
    if error:
        payload["error"] = error
    if snapshot is not None:
        payload["snapshot"] = snapshot
    if isinstance(extra, dict):
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_login_page(message=""):
    message_html = f"<div class='flash'>{html_escape(message)}</div>" if message else ""
    login_timeout_minutes = max(1, UI_AUTH_IDLE_TIMEOUT_SEC // 60)
    session_timeout_hours = max(1, UI_AUTH_SESSION_TIMEOUT_SEC // 3600)
    secure_note = "HTTPS 公网地址下会自动使用 Secure Cookie。" if _cookie_is_secure() else "如需 Secure Cookie，请将 UI_PUBLIC_BASE_URL 配置为 https 地址。"
    return _render_ui_template(
        "login.html",
        {
            "message_html": message_html,
            "ui_public_base_url": html_escape(UI_PUBLIC_BASE_URL),
            "login_timeout_minutes": login_timeout_minutes,
            "session_timeout_hours": session_timeout_hours,
            "secure_note": html_escape(secure_note),
        },
    )


def render_ui_page(message="", selected_send_as_id=None, session_token=None):
    snapshot = get_ui_snapshot(session_token=session_token)
    selected_id = _resolve_selected_send_as_id(snapshot, selected_send_as_id)
    boot_data = json.dumps(
        {
            "snapshot": snapshot,
            "selected_send_as_id": selected_id,
            "flash_message": message or "",
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return _render_ui_template(
        "index.html",
        {
            "boot_data": boot_data,
            "ui_auto_refresh_sec": html_escape(str(UI_AUTO_REFRESH_SEC)),
            "poll_interval_ms": int(UI_AUTO_REFRESH_SEC) * 1000,
        },
    )



async def ui_set_identity_enabled(send_as_id, enabled, actor_id=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    ok, message = await set_control_identity_enabled(send_as_id, enabled, source="ui", actor_id=actor_id)
    if not ok:
        return False, message or f"切换失败: {get_identity_display_name(send_as_id)}"
    return True, message


async def ui_set_module_enabled(send_as_id, module_name, enabled):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    if module_name not in MODULE_KEY_MAP:
        return False, f"未知模块: {module_name}"
    ok, message = await set_module_enabled(module_name, enabled, send_as_id=send_as_id)
    if not ok:
        return False, message or f"切换失败: {module_name}"
    action_text = "开启" if enabled else "关闭"
    return True, f"已{action_text}{module_name}[{get_identity_display_name(send_as_id)}]"


async def ui_set_pet_name(send_as_id, pet_name, pet_warm_name=None, pet_trial_name=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    pet_name = (pet_name or "").strip()
    if not pet_name:
        return False, "法宝名称不能为空"
    pet_warm_name = (pet_warm_name or "").strip() or pet_name
    pet_trial_name = (pet_trial_name or "").strip() or pet_name
    set_pet_name(send_as_id, pet_name)
    set_pet_warm_name(send_as_id, pet_warm_name)
    set_pet_trial_name(send_as_id, pet_trial_name)
    save_state()
    await send_audit_log(
        f"🗡️ 已更新法宝名称：抚摸={pet_name}，温养={pet_warm_name}，试炼={pet_trial_name}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新法宝名称[{get_identity_display_name(send_as_id)}]：抚摸={pet_name}，温养={pet_warm_name}，试炼={pet_trial_name}"


async def ui_set_small_world_feature_enabled(send_as_id, feature_name, enabled):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    feature_name = str(feature_name or "").strip()
    feature_map = {
        "preach": ("small_world_preach_enabled", "浩劫布道"),
        "manifest": ("small_world_manifest_enabled", "自动显灵"),
        "harvest": ("small_world_harvest_enabled", "收割香火"),
        "refine": ("small_world_refine_enabled", "神识淬炼"),
        "refresh": ("small_world_refresh_enabled", "祈愿刷新"),
    }
    field_name, display_name = feature_map.get(feature_name, ("", ""))
    if not field_name:
        return False, f"未知小世界子功能: {feature_name}"
    enabled = bool(enabled)
    with use_identity(send_as_id):
        state[field_name] = enabled
        save_state()
    action_text = "开启" if enabled else "关闭"
    await send_audit_log(
        f"🌍 已{action_text}小世界{display_name}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已{action_text}小世界{display_name}[{get_identity_display_name(send_as_id)}]"


async def ui_set_stargazer_star_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    choice = (choice or "").strip()
    if choice not in STARGAZER_STAR_CHOICES:
        return False, "无效的牵引星种"
    set_stargazer_star_choice(send_as_id, choice)
    save_state()
    await send_audit_log(
        f"🔭 已更新牵引星种：{choice}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新牵引星种[{get_identity_display_name(send_as_id)}]：{choice}"


async def ui_set_tianti_rank_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    choice = (choice or "").strip()
    if choice not in TIANTI_RANK_CHOICES:
        return False, "无效的登天阶档位"
    set_tianti_rank_choice(send_as_id, choice)
    save_state()
    await send_audit_log(
        f"☁️ 已更新登天阶档位：{choice}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新登天阶档位[{get_identity_display_name(send_as_id)}]：{choice}"


async def ui_set_wild_training_strategy(send_as_id, choice):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    with use_identity(send_as_id):
        ok, message = await apply_wild_training_strategy(normalize_wild_training_strategy(choice))
    if ok:
        await send_audit_log(
            f"🏞️ 已更新野外历练策略：{normalize_wild_training_strategy(choice)}",
            scope="identity",
            send_as_id=send_as_id,
        )
    return ok, f"{message}[{get_identity_display_name(send_as_id)}]" if ok else message


async def ui_set_second_soul_choice_config(send_as_id, *, auto_choice_enabled=None, strategy=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    strategy = str(strategy or "").strip().lower()
    if strategy and strategy not in {"stable", "break"}:
        return False, "无效的第二元神心魔抉择策略"
    changed = []
    with use_identity(send_as_id):
        if auto_choice_enabled is not None:
            enabled = bool(auto_choice_enabled)
            state["second_soul_auto_choice_enabled"] = enabled
            changed.append(f"自动抉择={'开' if enabled else '关'}")
        if strategy:
            state["second_soul_choice_strategy"] = strategy
            changed.append(f"策略={'稳固道心' if strategy == 'stable' else '强行突破'}")
        save_state()
    if not changed:
        return False, "没有可更新的第二元神心魔抉择配置"
    await send_audit_log(
        f"🌀 已更新第二元神心魔抉择配置：{'，'.join(changed)}",
        scope="identity",
        send_as_id=send_as_id,
        limit=220,
    )
    return True, f"已更新第二元神心魔抉择[{get_identity_display_name(send_as_id)}]：{'，'.join(changed)}"


async def ui_sync_stargazer_total_slots(send_as_id):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    ok, message = await sync_stargazer_total_slots(send_as_id)
    return ok, message


async def ui_sync_tianti_status(send_as_id):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    ok, message = await sync_tianti_status(send_as_id)
    return ok, message


async def ui_set_tianti_feature_enabled(send_as_id, feature_name, enabled):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    feature_name = str(feature_name or "").strip()
    feature_map = {
        "wenxin": ("tianti_wenxin_enabled", "问心台"),
        "gangfeng": ("tianti_gangfeng_enabled", "九天罡风"),
    }
    field_name, display_name = feature_map.get(feature_name, ("", ""))
    if not field_name:
        return False, f"未知登天阶子功能: {feature_name}"

    should_prime_gangfeng = False
    with use_identity(send_as_id):
        enabled = bool(enabled)
        state[field_name] = enabled
        if feature_name == "gangfeng" and enabled and int(state.get("tianti_cycle_count", 0) or 0) >= 1:
            next_gangfeng_time = float(state.get("next_tianti_gangfeng_time", 0) or 0)
            should_prime_gangfeng = next_gangfeng_time <= time.time()
        save_state()

    action_text = "开启" if enabled else "关闭"
    audit_suffix = ""
    if should_prime_gangfeng:
        msg = await send_game_command(CMD_TIANTI_GANGFENG, track=False, send_as_id=send_as_id)
        if msg:
            with use_identity(send_as_id):
                state["tianti_last_gangfeng_msg_id"] = int(getattr(msg, "id", 0) or 0)
                save_state()
            audit_suffix = "，已补发一次九天罡风"
        else:
            audit_suffix = "，补发九天罡风失败"
    await send_audit_log(
        f"☁️ 已{action_text}登天阶{display_name}{audit_suffix}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已{action_text}登天阶{display_name}[{get_identity_display_name(send_as_id)}]{audit_suffix}"


async def ui_set_taiyi_yindao_element(send_as_id, element):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    element = (element or "").strip()
    if element not in TAIYI_VALID_ELEMENTS:
        return False, f"无效的引道元素: {element}（合法值: 金/木/水/火/土）"
    with use_identity(send_as_id):
        state["taiyi_yindao_element"] = element
        save_state()
    await send_audit_log(
        f"🌟 已更新太一引道元素：{element}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新太一引道元素[{get_identity_display_name(send_as_id)}]：{element}"


async def ui_set_taiyi_node_search_enabled(send_as_id, enabled):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    enabled = bool(enabled)
    with use_identity(send_as_id):
        state["taiyi_node_search_enabled"] = enabled
        save_state()
    action_text = "开启" if enabled else "关闭"
    await send_audit_log(
        f"🌟 已{action_text}太一搜寻节点子模块",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已{action_text}太一搜寻节点[{get_identity_display_name(send_as_id)}]"


async def ui_set_jiyin_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    with use_identity(send_as_id):
        ok, message = await apply_jiyin_choice(choice)
    if not ok:
        return False, message
    return True, f"{message}[{get_identity_display_name(send_as_id)}]"


async def ui_set_nanlong_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    with use_identity(send_as_id):
        ok, message = await apply_nanlong_choice(choice)
    if not ok:
        return False, message
    return True, f"{message}[{get_identity_display_name(send_as_id)}]"


async def ui_set_module_window(send_as_id, module_name, start_hour_local, end_hour_local):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    try:
        start_hour_local = int(start_hour_local)
        end_hour_local = int(end_hour_local)
    except (TypeError, ValueError):
        return False, "窗口时间必须是整数小时"
    if not (0 <= start_hour_local <= 23 and 0 <= end_hour_local <= 23):
        return False, "窗口时间必须在 0-23 之间"
    if start_hour_local >= end_hour_local:
        return False, "开始时间必须早于结束时间，暂不支持跨天"
    start_hour_utc, end_hour_utc = convert_window_hours_local_to_utc(start_hour_local, end_hour_local)
    if start_hour_utc >= end_hour_utc:
        return False, "当前版本暂不支持跨 UTC 日期的窗口，请避免设置跨北京时间 08:00 的区间"
    ok, message = await set_module_window_config(module_name, start_hour_utc, end_hour_utc, send_as_id=send_as_id)
    if not ok:
        return False, message
    return True, f"已更新{module_name}执行窗口[{get_identity_display_name(send_as_id)}]：UTC+8 {start_hour_local:02d}:00-{end_hour_local:02d}:00"


async def ui_add_identity(send_as_id_raw, actor_id=None, account_id=None):
    ok, message, canonical_id = await register_identity(send_as_id_raw, source="ui", actor_id=actor_id, account_id=account_id)
    if ok and canonical_id and account_id:
        set_identity_account(canonical_id, int(account_id))
        save_state()
    return ok, message, canonical_id


async def ui_delete_identity(send_as_id, actor_id=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    return await delete_control_identity(send_as_id, source="ui", actor_id=actor_id)


# ================= 多账号登录 =================
_pending_login = {}  # {session_key: {mode, status, client, flow_id, ...}}


def _get_runtime_accounts_snapshot():
    accounts = dict(get_accounts())
    for account_id_raw, info in list(accounts.items()):
        try:
            account_id = int(account_id_raw)
        except (TypeError, ValueError):
            continue
        account_info = dict(info or {})
        offline = is_account_offline(account_id)
        account_info["api_source"] = "custom" if account_info.get("api_id") and account_info.get("api_hash") else "env"
        account_info.pop("api_hash", None)
        account_info["offline"] = offline
        account_info["status"] = "offline" if offline else "online"
        if offline:
            account_info["offline_reason"] = get_account_offline_reason(account_id) or "账号不可用"
        else:
            account_info.pop("offline_reason", None)
        accounts[str(account_id)] = account_info
    for account_id in sorted(get_all_clients().keys()):
        account_id = int(account_id or 0)
        if account_id <= 0 or is_account_offline(account_id):
            continue
        account_info = dict(accounts.get(str(account_id)) or {})
        account_info.update({
            "session": "main" if account_id == int(state.get("my_user_id") or 0) else f"account_{account_id}",
            "username": account_info.get("username") or str(account_id),
            "api_source": account_info.get("api_source") or "env",
            "offline": False,
            "status": "online",
        })
        account_info.pop("offline_reason", None)
        accounts[str(account_id)] = account_info
    return accounts


def _resolve_runtime_account_id(account_id=None):
    try:
        resolved = int(account_id or 0)
    except (TypeError, ValueError):
        resolved = 0
    if resolved > 0:
        return resolved
    live_account_ids = [
        int(candidate_id)
        for candidate_id in sorted(get_all_clients().keys())
        if int(candidate_id or 0) > 0 and not is_account_offline(candidate_id)
    ]
    return live_account_ids[0] if len(live_account_ids) == 1 else 0


def _cleanup_pending_temp_session_files(session_key):
    from .config import SESSION_DIR

    temp_prefix = os.path.join(SESSION_DIR, f"account_pending_{session_key}")
    for temp_file in glob.glob(f"{temp_prefix}*"):
        try:
            os.remove(temp_file)
        except OSError:
            pass


def _iter_account_session_files(account_id):
    from .config import SESSION_DIR

    account_id = int(account_id)
    session_dir = os.path.normcase(os.path.abspath(SESSION_DIR))
    patterns = [
        os.path.join(SESSION_DIR, f"account_{account_id}.session*"),
        os.path.join(SESSION_DIR, f"account_{account_id}"),
    ]
    seen = set()
    for pattern in patterns:
        for session_file in glob.glob(pattern):
            resolved = os.path.normcase(os.path.abspath(session_file))
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved != session_dir and not resolved.startswith(session_dir + os.sep):
                continue
            if os.path.isfile(session_file):
                yield session_file


def _delete_account_session_files(account_id):
    deleted_count = 0
    for session_file in list(_iter_account_session_files(account_id)):
        try:
            os.remove(session_file)
            deleted_count += 1
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return deleted_count


async def _logout_account_client(account_id, tc):
    if tc is None:
        return "未找到运行中 client，仅清理本地登录态"
    try:
        await asyncio.wait_for(tc.log_out(), timeout=25)
        return "Telegram 远端已登出"
    except Exception as exc:
        try:
            await tc.disconnect()
        except Exception:
            pass
        return f"Telegram 远端登出未确认：{type(exc).__name__}"


async def ui_logout_account(account_id, actor_id=None):
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return False, "账号 ID 无效"
    if account_id <= 0:
        return False, "账号 ID 无效"

    accounts = dict(get_accounts())
    if str(account_id) not in accounts and not list(_iter_account_session_files(account_id)):
        return False, f"未知账号: {account_id}"

    bound_identity_ids = []
    identity_account_map = get_identity_account_map()
    for raw_send_as_id, raw_account_id in list(identity_account_map.items()):
        try:
            send_as_id = int(raw_send_as_id or 0)
            mapped_account_id = int(raw_account_id or 0)
        except (TypeError, ValueError):
            continue
        if send_as_id > 0 and mapped_account_id == account_id:
            bound_identity_ids.append(send_as_id)

    tc = get_all_clients().get(account_id)
    logout_result = await _logout_account_client(account_id, tc)
    unregister_client(account_id)

    remaining_map = {}
    for raw_send_as_id, raw_account_id in identity_account_map.items():
        try:
            mapped_account_id = int(raw_account_id or 0)
        except (TypeError, ValueError):
            mapped_account_id = 0
        if mapped_account_id != account_id:
            remaining_map[str(raw_send_as_id)] = raw_account_id
    set_identity_account_map(remaining_map)

    for send_as_id in bound_identity_ids:
        if send_as_id in get_identity_ids():
            set_identity_enabled_profile(send_as_id, False)
            with use_identity(send_as_id) as identity_state:
                identity_state["pending_tasks"] = {}

    accounts.pop(str(account_id), None)
    set_accounts(accounts)
    deleted_file_count = _delete_account_session_files(account_id)
    save_state()

    actor_suffix = f"｜操作者：{actor_id}" if actor_id is not None else ""
    await send_audit_log(
        (
            f"🚪 已退出账号：{account_id}｜暂停并解绑身份 {len(bound_identity_ids)} 个｜"
            f"session 文件 {deleted_file_count} 个｜{logout_result}{actor_suffix}"
        ),
        scope="global",
    )
    return True, f"已退出账号 {account_id}，暂停并解绑身份 {len(bound_identity_ids)} 个"


async def _clear_pending_login(session_key, *, disconnect=True, remove_temp_files=False):
    pending = _pending_login.pop(session_key, None)
    if not pending:
        if remove_temp_files:
            _cleanup_pending_temp_session_files(session_key)
        return

    wait_task = pending.get("wait_task")
    current_task = asyncio.current_task()
    if wait_task and wait_task is not current_task and not wait_task.done():
        wait_task.cancel()

    tc = pending.get("client")
    if disconnect and tc:
        try:
            await tc.disconnect()
        except Exception:
            pass

    if remove_temp_files:
        _cleanup_pending_temp_session_files(session_key)


def _set_pending_login_state(session_key, flow_id=None, **updates):
    pending = _pending_login.get(session_key)
    if not pending:
        return False
    if flow_id is not None and str(pending.get("flow_id") or "") != str(flow_id):
        return False
    next_pending = dict(pending)
    next_pending.update(updates)
    _pending_login[session_key] = next_pending
    return True


def _build_qr_svg_markup(qr_url):
    qr_url = str(qr_url or "").strip()
    if not qr_url:
        return ""
    if segno is None:
        return ""
    try:
        return segno.make(qr_url).svg_inline(scale=6, omitsize=True)
    except Exception:
        return ""


async def _finalize_account_login(session_key, tc, *, flow_id=None):
    me = await tc.get_me()
    account_id = int(getattr(me, "id", 0) or 0)
    if account_id <= 0:
        raise RuntimeError("无法解析有效账号 ID")
    username = me.username or me.first_name or str(account_id)

    if flow_id is not None:
        pending = _pending_login.get(session_key)
        if not pending or str(pending.get("flow_id") or "") != str(flow_id):
            try:
                await tc.disconnect()
            except Exception:
                pass
            return False, "登录流程已更新，请重新发起", None
    else:
        pending = _pending_login.get(session_key) or {}
    api_id = pending.get("api_id") if isinstance(pending, dict) else None
    api_hash = pending.get("api_hash") if isinstance(pending, dict) else None

    await tc.disconnect()

    from .config import SESSION_DIR

    temp_prefix = os.path.join(SESSION_DIR, f"account_pending_{session_key}")
    real_prefix = os.path.join(SESSION_DIR, f"account_{account_id}")
    for temp_file in glob.glob(f"{temp_prefix}*"):
        suffix = temp_file[len(temp_prefix):]
        real_file = f"{real_prefix}{suffix}"
        try:
            os.replace(temp_file, real_file)
        except OSError:
            pass

    real_tc = create_account_client(account_id, api_id=api_id, api_hash=api_hash)
    await real_tc.connect()
    if not await real_tc.is_user_authorized():
        try:
            await real_tc.disconnect()
        except Exception:
            pass
        return False, "登录完成但新 session 未授权，请重新登录", None
    try:
        await real_tc.get_dialogs()
    except Exception:
        pass
    register_client(account_id, real_tc)

    from .app import _register_event_handlers
    _register_event_handlers(real_tc)

    account_info = {"session": f"account_{account_id}", "username": username}
    if api_id and api_hash:
        account_info["api_id"] = int(api_id)
        account_info["api_hash"] = str(api_hash)
    set_account(account_id, account_info)

    ok, _message, canonical_id = await register_identity(account_id, source="ui_login", account_id=account_id)
    if canonical_id:
        set_identity_account(canonical_id, account_id)

    try:
        from .control import hydrate_identity_profile
        entity = await real_tc.get_me()
        hydrate_identity_profile(entity)
    except Exception:
        pass

    save_state()
    await send_audit_log(f"🔑 新账号登录成功：@{username}｜{account_id}", scope="global")
    return True, f"登录成功: @{username}", account_id


async def _wait_pending_qr_login(session_key, flow_id, tc, qr_login):
    try:
        await qr_login.wait()
    except asyncio.TimeoutError:
        try:
            await tc.disconnect()
        except Exception:
            pass
        _cleanup_pending_temp_session_files(session_key)
        _set_pending_login_state(
            session_key,
            flow_id,
            status="expired",
            message="二维码已过期，请刷新后重试",
            client=None,
            wait_task=None,
            qr_url="",
            qr_expires_at=0,
        )
        return
    except Exception as e:
        err_str = str(e)
        if "Two-steps verification" in err_str or "SessionPasswordNeeded" in err_str or "2FA" in err_str:
            _set_pending_login_state(
                session_key,
                flow_id,
                status="need_2fa",
                message="扫码成功，请输入两步验证密码",
                wait_task=None,
                qr_url="",
                qr_expires_at=0,
            )
            return
        try:
            await tc.disconnect()
        except Exception:
            pass
        _cleanup_pending_temp_session_files(session_key)
        _set_pending_login_state(
            session_key,
            flow_id,
            status="error",
            message=f"二维码登录失败: {e}",
            client=None,
            wait_task=None,
            qr_url="",
            qr_expires_at=0,
        )
        return

    try:
        ok, message, account_id = await _finalize_account_login(session_key, tc, flow_id=flow_id)
    except Exception as e:
        _cleanup_pending_temp_session_files(session_key)
        _set_pending_login_state(
            session_key,
            flow_id,
            status="error",
            message=f"二维码登录失败: {e}",
            client=None,
            wait_task=None,
            qr_url="",
            qr_expires_at=0,
        )
        return

    if ok:
        _set_pending_login_state(
            session_key,
            flow_id,
            status="done",
            message=message,
            account_id=account_id,
            client=None,
            wait_task=None,
            qr_url="",
            qr_expires_at=0,
        )
    else:
        _cleanup_pending_temp_session_files(session_key)


def _parse_account_login_api(api_id=None, api_hash=None):
    api_id_text = str(api_id or "").strip()
    api_hash_text = str(api_hash or "").strip()
    if not api_id_text and not api_hash_text:
        return None, None
    if not api_id_text or not api_hash_text:
        raise ValueError("API_ID 和 API_HASH 需要同时填写")
    try:
        parsed_api_id = int(api_id_text)
    except (TypeError, ValueError):
        raise ValueError("API_ID 必须是数字") from None
    if parsed_api_id <= 0:
        raise ValueError("API_ID 必须大于 0")
    return parsed_api_id, api_hash_text


async def ui_account_login_start(phone, session_key, api_id=None, api_hash=None):
    phone = (phone or "").strip()
    if not phone:
        return False, "请输入手机号", None
    try:
        parsed_api_id, parsed_api_hash = _parse_account_login_api(api_id, api_hash)
    except ValueError as e:
        return False, str(e), None

    await _clear_pending_login(session_key, remove_temp_files=True)

    tc = create_account_client(f"pending_{session_key}", api_id=parsed_api_id, api_hash=parsed_api_hash)
    await tc.connect()
    try:
        sent = await tc.send_code_request(phone)
        _pending_login[session_key] = {
            "mode": "phone",
            "status": "waiting_code",
            "message": "验证码已发送，请查收",
            "client": tc,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "qr_url": "",
            "qr_expires_at": 0,
            "wait_task": None,
            "flow_id": str(time.time_ns()),
            "account_id": 0,
            "api_id": parsed_api_id,
            "api_hash": parsed_api_hash,
        }
        return True, "验证码已发送", None
    except Exception as e:
        try:
            await tc.disconnect()
        except Exception:
            pass
        _cleanup_pending_temp_session_files(session_key)
        return False, f"发送验证码失败: {e}", None


async def ui_account_login_qr_start(session_key, api_id=None, api_hash=None):
    try:
        parsed_api_id, parsed_api_hash = _parse_account_login_api(api_id, api_hash)
    except ValueError as e:
        return False, str(e), None
    await _clear_pending_login(session_key, remove_temp_files=True)

    tc = create_account_client(f"pending_{session_key}", api_id=parsed_api_id, api_hash=parsed_api_hash)
    await tc.connect()
    try:
        ignored_ids = []
        for raw_account_id in get_accounts().keys():
            try:
                ignored_ids.append(int(raw_account_id))
            except (TypeError, ValueError):
                continue
        qr_login = await tc.qr_login(ignored_ids=ignored_ids or None)
        expires_at = float(qr_login.expires.timestamp()) if getattr(qr_login, "expires", None) else 0
        flow_id = str(time.time_ns())
        _pending_login[session_key] = {
            "mode": "qr",
            "status": "waiting_scan",
            "message": "请使用已登录 Telegram 的手机扫码确认",
            "client": tc,
            "phone": "",
            "phone_code_hash": "",
            "qr_url": qr_login.url,
            "qr_expires_at": expires_at,
            "wait_task": None,
            "flow_id": flow_id,
            "account_id": 0,
            "api_id": parsed_api_id,
            "api_hash": parsed_api_hash,
        }
        wait_task = asyncio.create_task(_wait_pending_qr_login(session_key, flow_id, tc, qr_login))
        _set_pending_login_state(session_key, flow_id, wait_task=wait_task)
        qr_svg = _build_qr_svg_markup(qr_login.url)
        return True, "二维码已生成，请使用 Telegram 扫码确认", {
            "status": "waiting_scan",
            "qr_url": qr_login.url,
            "qr_svg": qr_svg,
            "qr_expires_at": fmt_abs_ts(expires_at),
            "qr_expires_at_ts": expires_at,
            "remaining_sec": max(0, int(expires_at - time.time())),
        }
    except Exception as e:
        try:
            await tc.disconnect()
        except Exception:
            pass
        _cleanup_pending_temp_session_files(session_key)
        return False, f"生成二维码失败: {e}", None


def ui_account_login_qr_status(session_key):
    pending = _pending_login.get(session_key)
    if not pending or pending.get("mode") != "qr":
        return {
            "status": "cancelled",
            "message": "当前没有进行中的二维码登录",
        }

    qr_expires_at = float(pending.get("qr_expires_at", 0) or 0)
    status = str(pending.get("status") or "waiting_scan")
    payload = {
        "status": status,
        "message": pending.get("message") or "",
        "qr_expires_at": fmt_abs_ts(qr_expires_at),
        "qr_expires_at_ts": qr_expires_at,
    }
    if status == "waiting_scan":
        qr_url = pending.get("qr_url") or ""
        payload.update({
            "qr_url": qr_url,
            "qr_svg": _build_qr_svg_markup(qr_url),
            "remaining_sec": max(0, int(qr_expires_at - time.time())),
        })
    account_id = int(pending.get("account_id", 0) or 0)
    if account_id > 0:
        payload["account_id"] = account_id
    return payload


async def ui_account_login_cancel(session_key):
    await _clear_pending_login(session_key, remove_temp_files=True)
    return True, "已取消当前登录流程"


async def ui_account_login_verify(code, session_key, password=None):
    pending = _pending_login.get(session_key)
    if not pending:
        return False, "登录会话已过期，请重新开始", None

    tc = pending.get("client")
    mode = str(pending.get("mode") or "phone")
    phone = pending.get("phone") or ""
    phone_code_hash = pending.get("phone_code_hash") or ""
    flow_id = pending.get("flow_id")
    status = str(pending.get("status") or "")
    code = (code or "").strip()

    if mode == "qr" and password and status != "need_2fa":
        return False, "当前二维码登录尚未进入两步验证", None

    try:
        if password:
            await tc.sign_in(password=password)
        elif mode == "phone":
            await tc.sign_in(phone, code, phone_code_hash=phone_code_hash)
        else:
            return False, "当前二维码登录尚未进入两步验证", None
    except Exception as e:
        err_str = str(e)
        if "Two-steps verification" in err_str or "SessionPasswordNeeded" in err_str or "2FA" in err_str:
            _set_pending_login_state(session_key, flow_id, status="need_2fa", message="需要两步验证密码")
            return False, "need_2fa", None
        await _clear_pending_login(session_key, remove_temp_files=True)
        return False, f"登录失败: {e}", None

    try:
        ok, message, account_id = await _finalize_account_login(session_key, tc, flow_id=flow_id)
    except Exception as e:
        await _clear_pending_login(session_key, disconnect=False, remove_temp_files=True)
        return False, f"登录失败: {e}", None

    await _clear_pending_login(session_key, disconnect=False, remove_temp_files=False)
    if not ok:
        return False, message, None
    return True, message, account_id


async def ui_get_send_as_peers(account_id):
    """获取指定账号在游戏群中可用的 send_as 身份列表"""
    account_id = _resolve_runtime_account_id(account_id)
    if account_id <= 0:
        return False, "请先选择一个已登录账号", [], []
    game_group_id = get_game_group_id()
    if not game_group_id:
        return False, "请先在基础配置中设置游戏群聊 ID", [], []
    if is_account_offline(account_id):
        return False, f"账号 {account_id} 离线，请重新登录", [], []
    tc = get_registered_client(account_id)
    if not tc:
        return False, f"账号 {account_id} 未登录", [], []
    try:
        from telethon.tl.functions.channels import GetSendAsRequest
        result = await tc(GetSendAsRequest(peer=game_group_id))
        peers = []
        for send_as_peer in result.peers:
            peer_obj = send_as_peer.peer
            try:
                entity = await tc.get_entity(peer_obj)
                peer_id = int(entity.id)
                name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(peer_id)
                username = getattr(entity, "username", "") or ""
                peer_type = "channel" if hasattr(entity, "title") else "user"
                peers.append({"id": peer_id, "name": name, "username": username, "type": peer_type})
            except Exception:
                continue
        existing_ids = list(get_identity_ids())
        return True, f"获取到 {len(peers)} 个可用身份", peers, existing_ids
    except Exception as e:
        return False, f"获取 send_as 列表失败: {e}", [], []


async def ui_refresh_identity_info(send_as_id, actor_id=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    ok, message = await refresh_identity_info(send_as_id, source="ui", actor_id=actor_id)
    return ok, message


async def ui_refresh_forum_topics(game_group_id, actor_id=None):
    ok, message, topics = await fetch_forum_topics(game_group_id)
    if not ok:
        return False, message, []
    set_forum_topics(topics, updated_at=time.time())
    save_state()
    actor_suffix = f"｜操作者：{actor_id}" if actor_id is not None else ""
    console_log(f"🧩 已刷新话题：群={int(game_group_id)}｜数量={len(topics)}{actor_suffix}", scope="global")
    return True, message, topics


async def ui_set_basic_config(game_group_id, game_bot_ids, game_topic_id, auto_delete_sent_messages, tiandao_judgement_enabled=False, guanxing_monitor_enabled=False, guanxing_shift_target="", guanxing_monitor_targets=None, actor_id=None):
    raw_group_id = (str(game_group_id or "")).strip()
    if not raw_group_id:
        return False, "游戏群聊 ID 不能为空"
    try:
        group_id = int(raw_group_id)
    except (TypeError, ValueError):
        return False, "游戏群聊 ID 必须是整数"
    if group_id == 0:
        return False, "游戏群聊 ID 不能为 0"

    raw_bot_ids = (str(game_bot_ids or "")).strip()
    if not raw_bot_ids:
        return False, "游戏 BOT ID 不能为空"
    parsed_bot_ids = []
    seen_bot_ids = set()
    for part in raw_bot_ids.replace("，", ",").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            bot_id = int(item)
        except (TypeError, ValueError):
            return False, "游戏 BOT ID 必须是整数，多个请用逗号分隔"
        if bot_id in seen_bot_ids:
            continue
        seen_bot_ids.add(bot_id)
        parsed_bot_ids.append(bot_id)
    if not parsed_bot_ids:
        return False, "至少需要一个 游戏 BOT ID"

    raw_topic_id = (str(game_topic_id or "")).strip()
    if not raw_topic_id:
        topic_id = 0
    else:
        try:
            topic_id = int(raw_topic_id)
        except (TypeError, ValueError):
            return False, "话题 ID 必须是整数"
        if topic_id < 0:
            return False, "话题 ID 不能为负数"

    auto_delete_enabled = bool(auto_delete_sent_messages)
    tiandao_judgement_switch_enabled = bool(tiandao_judgement_enabled)
    guanxing_monitor_switch_enabled = bool(guanxing_monitor_enabled)
    raw_shift_target = str(guanxing_shift_target or "").strip()
    if any(char.isspace() for char in raw_shift_target):
        return False, "观星改换目标不能包含空白字符"
    if raw_shift_target == "@":
        return False, "观星改换目标需填写用户名或 @用户名"
    if isinstance(guanxing_monitor_targets, str):
        raw_monitor_targets = [guanxing_monitor_targets]
    else:
        try:
            raw_monitor_targets = list(guanxing_monitor_targets or [])
        except TypeError:
            raw_monitor_targets = []
    target_options = get_guanxing_monitor_target_options()
    normalized_monitor_targets = []
    for target in raw_monitor_targets:
        target_text = str(target or "").strip()
        if target_text in target_options and target_text not in normalized_monitor_targets:
            normalized_monitor_targets.append(target_text)
    if guanxing_monitor_switch_enabled and not normalized_monitor_targets:
        return False, "开启观星监控前请至少选择一个命中结果"

    set_game_group_id(group_id)
    normalized_bot_ids = set_game_bot_ids(parsed_bot_ids)
    set_game_topic_id(topic_id)
    set_auto_delete_sent_messages(auto_delete_enabled)
    set_tiandao_judgement_enabled(tiandao_judgement_switch_enabled)
    set_guanxing_monitor_enabled(guanxing_monitor_switch_enabled)
    set_guanxing_monitor_targets(normalized_monitor_targets)
    normalized_shift_target = set_guanxing_shift_target(guanxing_shift_target)
    save_state()
    actor_suffix = f"｜操作者：{actor_id}" if actor_id is not None else ""
    display_topic = str(topic_id) if topic_id > 0 else "未启用"
    display_bots = ", ".join(str(bot_id) for bot_id in normalized_bot_ids)
    display_auto_delete = "开启" if auto_delete_enabled else "关闭"
    display_tiandao_judgement = "开启" if tiandao_judgement_switch_enabled else "关闭"
    display_guanxing_monitor = "开启" if guanxing_monitor_switch_enabled else "关闭"
    display_monitor_targets = ", ".join(normalized_monitor_targets) or "未选择"
    console_log(
        f"🧩 已更新基础配置：群={group_id}｜bot={display_bots}｜话题={display_topic}｜自动删消息={display_auto_delete}｜天道审判={display_tiandao_judgement}｜观星监控={display_guanxing_monitor}｜观星监控目标={display_monitor_targets}｜观星目标={normalized_shift_target or '未设置'}{actor_suffix}",
        scope="global",
        limit=320,
    )
    return True, f"已更新基础配置：群聊 {group_id} ｜ bot {display_bots} ｜ 话题 {display_topic} ｜ 自动删消息 {display_auto_delete} ｜ 天道审判 {display_tiandao_judgement} ｜ 观星监控 {display_guanxing_monitor} ｜ 观星监控目标 {display_monitor_targets} ｜ 观星目标 {normalized_shift_target or '未设置'}"


def _write_response(writer, status_line, body, *, content_type, extra_headers=None):
    body_bytes = body if isinstance(body, bytes) else str(body).encode("utf-8")
    headers = [
        status_line,
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body_bytes)}",
        "Connection: close",
        "Cache-Control: no-store",
    ]
    headers.extend(extra_headers or [])
    headers.extend(["", ""])
    writer.write("\r\n".join(headers).encode("utf-8") + body_bytes)


def _write_method_not_allowed(writer):
    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")


def _write_json_unauthorized(writer, extra_headers=None):
    body = _make_json_payload(False, error="未登录或登录已失效")
    _write_response(writer, "HTTP/1.1 401 Unauthorized", body, content_type="application/json; charset=utf-8", extra_headers=extra_headers)


def _write_json_bad_request(writer, message, extra_headers=None):
    body = _make_json_payload(False, error=message)
    _write_response(writer, "HTTP/1.1 400 Bad Request", body, content_type="application/json; charset=utf-8", extra_headers=extra_headers)


def _write_json_result(writer, ok, message, *, session_token=None, extra_headers=None, extra=None, include_snapshot=True):
    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
    body = _make_json_payload(
        ok,
        message=message if ok else "",
        error="" if ok else message,
        snapshot=get_ui_snapshot(session_token=session_token) if ok and include_snapshot else None,
        extra=extra if ok else None,
    )
    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=extra_headers)


async def handle_ui_http(reader, writer):
    peer = writer.get_extra_info("peername")
    method = ""
    path = ""
    try:
        try:
            request_head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError as e:
            request_head = e.partial
        except Exception:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
            return

        header_text = request_head.decode("utf-8", errors="ignore")
        request_lines = header_text.split("\r\n")
        request_line = request_lines[0] if request_lines else ""
        parts = request_line.split()
        if len(parts) < 2:
            writer.close()
            await writer.wait_closed()
            return

        method, raw_target = parts[0].upper(), parts[1]
        parsed = urlsplit(raw_target)
        path = parsed.path or "/"
        query = parse_qs(parsed.query, keep_blank_values=False)
        headers = {}
        for line in request_lines[1:]:
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()

        content_length = 0
        try:
            content_length = max(0, int(headers.get("content-length", "0") or 0))
        except (TypeError, ValueError):
            content_length = 0
        body_bytes = b""
        if content_length > 0:
            try:
                body_bytes = await reader.readexactly(content_length)
            except asyncio.IncompleteReadError as e:
                body_bytes = e.partial

        payload = _parse_request_body(headers, body_bytes)
        now = time.time()

        if path == "/api/login/exchange":
            if method != "POST":
                _write_method_not_allowed(writer)
            else:
                login_token = (payload.get("token") or "").strip()
                if not login_token:
                    body = _make_json_payload(False, error="缺少 token")
                    _write_response(writer, "HTTP/1.1 400 Bad Request", body, content_type="application/json; charset=utf-8")
                else:
                    session_token = redeem_ui_login_token(login_token, now)
                    if not session_token:
                        body = _make_json_payload(False, error="登录 token 无效或已失效，请重新在日志群发送 .登录")
                        _write_response(
                            writer,
                            "HTTP/1.1 401 Unauthorized",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=[f"Set-Cookie: {_build_session_cookie_header('', clear=True)}"],
                        )
                    else:
                        body = _make_json_payload(True, message="登录成功")
                        _write_response(
                            writer,
                            "HTTP/1.1 200 OK",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=[f"Set-Cookie: {_build_session_cookie_header(session_token)}"],
                        )
        else:
            session, session_cookie_header = _get_authenticated_session(headers, now)
            auth_headers = [f"Set-Cookie: {session_cookie_header}"] if session_cookie_header else []

            # 初始化模式：无账号且无身份时跳过认证，允许直接使用 UI
            _setup_mode = not get_accounts() and not get_identity_ids()
            if _setup_mode and session is None:
                session = {"session_token": "__setup__", "sender_id": 0, "created_at": now, "last_active_at": now}

            if path == "/favicon.png":
                if method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    with open(UI_FAVICON_PNG_PATH, "rb") as favicon_fp:
                        _write_response(writer, "HTTP/1.1 200 OK", favicon_fp.read(), content_type="image/png")
            elif path.startswith("/static/"):
                if method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    asset_body, asset_content_type = _load_ui_static_asset(path[len("/static/"):])
                    if asset_body is None:
                        _write_response(writer, "HTTP/1.1 404 Not Found", "Not Found", content_type="text/plain; charset=utf-8")
                    else:
                        _write_response(writer, "HTTP/1.1 200 OK", asset_body, content_type=asset_content_type)
            elif path == "/":
                if method != "GET":
                    _write_method_not_allowed(writer)
                elif session is None:
                    message = "登录已失效，请重新在日志群发送 .登录" if session_cookie_header else ""
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        _render_login_page(message),
                        content_type="text/html; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                else:
                    selected_send_as_id = query.get("send_as_id", [""])[0]
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        render_ui_page(selected_send_as_id=selected_send_as_id, session_token=(session or {}).get("session_token")),
                        content_type="text/html; charset=utf-8",
                        extra_headers=auth_headers,
                    )
            elif path == "/api/state":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    body = _make_json_payload(True, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")))
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
            elif path == "/api/logs/days":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    body = _make_json_payload(True, extra={"days": _list_message_log_days()})
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
            elif path == "/api/logs/entries":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    date_str = query.get("date", [""])[0]
                    q_text = query.get("q", [""])[0]
                    types_text = query.get("types", [""])[0]
                    types_set = {item.strip() for item in str(types_text or "").split(",") if item.strip()}
                    sender_text = query.get("sender_id", ["0"])[0]
                    offset_text = query.get("offset", ["0"])[0]
                    limit_text = query.get("limit", ["80"])[0]
                    try:
                        sender_id_val = int(sender_text or 0)
                    except (TypeError, ValueError):
                        sender_id_val = 0
                    try:
                        offset_val = int(offset_text or 0)
                    except (TypeError, ValueError):
                        offset_val = 0
                    try:
                        limit_val = int(limit_text or 80)
                    except (TypeError, ValueError):
                        limit_val = 80
                    log_data = _read_log_entries(date_str, q_text, types_set, sender_id_val, offset_val, limit_val)
                    body = _make_json_payload(True, extra=log_data)
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
            elif path == "/api/official-schedules":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = query.get("send_as_id", [None])[0]
                    schedules = list_local_official_schedules(send_as_id=send_as_id, include_inactive=True, limit=300)
                    body = _make_json_payload(True, extra={"official_schedules": schedules})
                    _write_response(writer, "HTTP/1.1 200 OK", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/official-schedule-preview":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, plan = ui_preview_official_schedule(payload)
                    _write_json_result(
                        writer,
                        ok,
                        message,
                        session_token=(session or {}).get("session_token"),
                        extra_headers=auth_headers,
                        extra={"plan": plan} if plan else None,
                        include_snapshot=False,
                    )
            elif path == "/api/official-schedule-prepare":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, plan = ui_prepare_official_schedule(payload)
                    _write_json_result(
                        writer,
                        ok,
                        message,
                        session_token=(session or {}).get("session_token"),
                        extra_headers=auth_headers,
                        extra={"plan": plan} if plan else None,
                    )
            elif path == "/api/official-schedule-create":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    if str(payload.get("confirm") or "") != "CREATE_OFFICIAL_SCHEDULE":
                        _write_json_result(
                            writer,
                            False,
                            "缺少确认串 CREATE_OFFICIAL_SCHEDULE，已拒绝创建官方定时消息",
                            session_token=(session or {}).get("session_token"),
                            extra_headers=auth_headers,
                            include_snapshot=False,
                        )
                    else:
                        try:
                            result = await create_official_messages_for_batch(payload.get("batch_id"))
                            ok = result.get("failed", 0) == 0
                            body = _make_json_payload(
                                ok,
                                message=f"官方定时创建：成功 {result.get('created', 0)} / {result.get('total', 0)}" if ok else "",
                                error="" if ok else f"官方定时部分失败：成功 {result.get('created', 0)} / {result.get('total', 0)}",
                                snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")),
                                extra={"result": result},
                            )
                            _write_response(
                                writer,
                                "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request",
                                body,
                                content_type="application/json; charset=utf-8",
                                extra_headers=auth_headers,
                            )
                        except Exception as e:
                            _write_json_result(
                                writer,
                                False,
                                f"创建失败: {e}",
                                session_token=(session or {}).get("session_token"),
                                extra_headers=auth_headers,
                            )
            elif path == "/api/official-schedule-delete":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    try:
                        result = await delete_official_schedule_records(
                            record_ids=payload.get("record_ids"),
                            batch_id=payload.get("batch_id"),
                            delete_official=bool(payload.get("delete_official")),
                        )
                        _write_json_result(
                            writer,
                            True,
                            f"已删除本地排班记录 {result.get('records', 0)} 条，官方定时 {result.get('official', 0)} 条",
                            session_token=(session or {}).get("session_token"),
                            extra_headers=auth_headers,
                        )
                    except Exception as e:
                        _write_json_result(
                            writer,
                            False,
                            f"删除失败: {e}",
                            session_token=(session or {}).get("session_token"),
                            extra_headers=auth_headers,
                        )
            elif path == "/api/storage-bag-sync":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = await ui_start_storage_bag_sync(payload.get("identity_ids"))
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(
                        ok,
                        message=message if ok else "",
                        error="" if ok else message,
                        snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                    )
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/storage-bag-item-rule":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = ui_set_storage_bag_item_rule(payload.get("item_name"), payload.get("method"), payload.get("tags"), payload.get("reason"))
                    _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/storage-bag-transfer-preview":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, preview = ui_preview_storage_bag_transfer(payload)
                    _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers, extra={"preview": preview} if preview else None)
            elif path == "/api/basic-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = await ui_set_basic_config(payload.get("game_group_id"), payload.get("game_bot_ids"), payload.get("game_topic_id"), payload.get("auto_delete_sent_messages"), payload.get("tiandao_judgement_enabled"), payload.get("guanxing_monitor_enabled"), payload.get("guanxing_shift_target"), payload.get("guanxing_monitor_targets"), actor_id=(session or {}).get("sender_id"))
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/forum-topics":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, topics = await ui_refresh_forum_topics(payload.get("game_group_id"), actor_id=(session or {}).get("sender_id"))
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(
                        ok,
                        message=message if ok else "",
                        error="" if ok else message,
                        snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                        extra={"forum_topics": topics, "forum_topics_updated_at": fmt_abs_ts(get_forum_topics_updated_at())} if ok else None,
                    )
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/send-as-peers":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    raw_account_id = payload.get("account_id")
                    try:
                        ok, message, peers, existing_ids = await ui_get_send_as_peers(raw_account_id)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(
                            ok,
                            message=message if ok else "",
                            error="" if ok else message,
                            extra={"peers": peers, "existing_ids": existing_ids} if ok else None,
                        )
                    except Exception as e:
                        body = _make_json_payload(False, error=f"获取失败: {e}")
                        status_line = "HTTP/1.1 500 Internal Server Error"
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/identity":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id_raw = payload.get("send_as_id")
                    send_as_ids_raw = payload.get("send_as_ids")
                    batch_account_id = payload.get("account_id")
                    actor_id = (session or {}).get("sender_id")
                    if send_as_ids_raw and isinstance(send_as_ids_raw, list):
                        # 批量添加
                        results = []
                        last_canonical_id = None
                        for raw_id in send_as_ids_raw:
                            ok, msg, cid = await ui_add_identity(raw_id, actor_id=actor_id, account_id=batch_account_id)
                            results.append({"id": raw_id, "ok": ok, "message": msg, "canonical_id": cid})
                            if ok and cid:
                                last_canonical_id = cid
                        success_count = sum(1 for r in results if r["ok"])
                        fail_count = len(results) - success_count
                        message = f"批量添加完成：成功 {success_count} 个"
                        if fail_count > 0:
                            message += f"，失败 {fail_count} 个"
                        body = _make_json_payload(
                            True,
                            message=message,
                            snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")),
                            extra={"send_as_id": last_canonical_id, "results": results},
                        )
                        _write_response(writer, "HTTP/1.1 200 OK", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                    elif send_as_id_raw in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message, canonical_id = await ui_add_identity(send_as_id_raw, actor_id=(session or {}).get("sender_id"), account_id=batch_account_id)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(
                            ok,
                            message=message if ok else "",
                            error="" if ok else message,
                            snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                            extra={"send_as_id": canonical_id} if canonical_id is not None else None,
                        )
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/identity-refresh":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_refresh_identity_info(send_as_id, actor_id=(session or {}).get("sender_id"))
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/account-logout":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    account_id = payload.get("account_id")
                    if account_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 account_id 参数", auth_headers)
                    else:
                        ok, message = await ui_logout_account(account_id, actor_id=(session or {}).get("sender_id"))
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/identity-delete":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_delete_identity(send_as_id, actor_id=(session or {}).get("sender_id"))
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/global-enabled":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    enabled = bool(payload.get("enabled"))
                    ok, message = await toggle_global_enabled(enabled, source="ui", actor_id=(session or {}).get("sender_id"))
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/identity-enabled":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    enabled = bool(payload.get("enabled"))
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_identity_enabled(send_as_id, enabled, actor_id=(session or {}).get("sender_id"))
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/toggle":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    module_name = payload.get("module")
                    enabled = bool(payload.get("enabled"))
                    if send_as_id in {None, ""} or not module_name:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 module 参数", auth_headers)
                    else:
                        ok, message = await ui_set_module_enabled(send_as_id, module_name, enabled)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/jiyin-choice":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    choice = payload.get("choice")
                    if send_as_id in {None, ""} or not choice:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 choice 参数", auth_headers)
                    else:
                        ok, message = await ui_set_jiyin_choice(send_as_id, choice)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/nanlong-choice":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    choice = payload.get("choice")
                    if send_as_id in {None, ""} or not choice:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 choice 参数", auth_headers)
                    else:
                        ok, message = await ui_set_nanlong_choice(send_as_id, choice)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/pet-name":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    pet_name = payload.get("pet_name")
                    pet_warm_name = payload.get("pet_warm_name")
                    pet_trial_name = payload.get("pet_trial_name")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_pet_name(send_as_id, pet_name, pet_warm_name=pet_warm_name, pet_trial_name=pet_trial_name)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/small-world-feature-toggle":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    feature_name = payload.get("feature")
                    enabled = bool(payload.get("enabled"))
                    if send_as_id in {None, ""} or not feature_name:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 feature 参数", auth_headers)
                    else:
                        ok, message = await ui_set_small_world_feature_enabled(send_as_id, feature_name, enabled)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/stargazer-star-choice":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    choice = payload.get("choice")
                    if send_as_id in {None, ""} or not choice:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 choice 参数", auth_headers)
                    else:
                        ok, message = await ui_set_stargazer_star_choice(send_as_id, choice)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/tianti-rank-choice":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    choice = payload.get("choice")
                    if send_as_id in {None, ""} or not choice:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 choice 参数", auth_headers)
                    else:
                        ok, message = await ui_set_tianti_rank_choice(send_as_id, choice)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/wild-training-strategy":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    choice = payload.get("choice")
                    if send_as_id in {None, ""} or not choice:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 choice 参数", auth_headers)
                    else:
                        ok, message = await ui_set_wild_training_strategy(send_as_id, choice)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/second-soul-choice-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_second_soul_choice_config(
                            send_as_id,
                            auto_choice_enabled=payload.get("auto_choice_enabled") if "auto_choice_enabled" in payload else None,
                            strategy=payload.get("strategy"),
                        )
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/stargazer-sync":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_sync_stargazer_total_slots(send_as_id)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/tianti-sync":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_sync_tianti_status(send_as_id)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/tianti-feature-toggle":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    feature_name = payload.get("feature")
                    enabled = bool(payload.get("enabled"))
                    if send_as_id in {None, ""} or not feature_name:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 feature 参数", auth_headers)
                    else:
                        ok, message = await ui_set_tianti_feature_enabled(send_as_id, feature_name, enabled)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/taiyi-yindao-element":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    element = payload.get("element")
                    if send_as_id in {None, ""} or not element:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 element 参数", auth_headers)
                    else:
                        ok, message = await ui_set_taiyi_yindao_element(send_as_id, element)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/taiyi-node-search-toggle":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    enabled = bool(payload.get("enabled"))
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_taiyi_node_search_enabled(send_as_id, enabled)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/module-window":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    module_name = payload.get("module")
                    start_hour_local = payload.get("start_hour_local")
                    end_hour_local = payload.get("end_hour_local")
                    if send_as_id in {None, ""} or not module_name:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 module 参数", auth_headers)
                    else:
                        ok, message = await ui_set_module_window(send_as_id, module_name, start_hour_local, end_hour_local)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/account/login-start":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    phone = payload.get("phone", "")
                    api_id = payload.get("api_id")
                    api_hash = payload.get("api_hash")
                    session_key = (session or {}).get("session_token", "")
                    ok, message, _extra = await ui_account_login_start(phone, session_key, api_id=api_id, api_hash=api_hash)
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/login-qr-start":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    session_key = (session or {}).get("session_token", "")
                    api_id = payload.get("api_id")
                    api_hash = payload.get("api_hash")
                    ok, message, qr_info = await ui_account_login_qr_start(session_key, api_id=api_id, api_hash=api_hash)
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, extra=qr_info if ok else None)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/login-qr-status":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    session_key = (session or {}).get("session_token", "")
                    qr_status = ui_account_login_qr_status(session_key)
                    extra = dict(qr_status)
                    status = str(extra.get("status") or "")
                    message = extra.pop("message", "")
                    account_id = int(extra.get("account_id", 0) or 0)
                    snapshot = get_ui_snapshot(session_token=(session or {}).get("session_token")) if status == "done" and account_id > 0 else None
                    body = _make_json_payload(True, message=message, snapshot=snapshot, extra=extra)
                    _write_response(writer, "HTTP/1.1 200 OK", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/login-cancel":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    session_key = (session or {}).get("session_token", "")
                    ok, message = await ui_account_login_cancel(session_key)
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/login-verify":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    code = payload.get("code", "")
                    password = payload.get("password")
                    session_key = (session or {}).get("session_token", "")
                    ok, message, account_id = await ui_account_login_verify(code, session_key, password=password)
                    if not ok and message == "need_2fa":
                        body = _make_json_payload(False, error="need_2fa")
                        _write_response(writer, "HTTP/1.1 200 OK", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                    else:
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(
                            ok,
                            message=message if ok else "",
                            error="" if ok else message,
                            snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                            extra={"account_id": account_id} if account_id else None,
                        )
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            else:
                _write_response(writer, "HTTP/1.1 404 Not Found", "Not Found", content_type="text/plain; charset=utf-8")
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    except Exception as e:
        traceback.print_exc()
        try:
            _write_response(writer, "HTTP/1.1 500 Internal Server Error", f"Internal Server Error\n{e}\n", content_type="text/plain; charset=utf-8")
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
    finally:
        try:
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        if peer:
            print(f"[{datetime.now(TZ_LOCAL).strftime('%Y-%m-%d %H:%M:%S')}] ui request: {peer} {method or '-'} {path or '-'}")


async def start_ui_server():
    global _ui_server
    if _ui_server is not None:
        return _ui_server
    _ui_server = await asyncio.start_server(handle_ui_http, UI_HOST, UI_PORT)
    sockets = _ui_server.sockets or []
    bind_text = ", ".join(str(sock.getsockname()) for sock in sockets) or f"{UI_HOST}:{UI_PORT}"
    await send_audit_log(f"🖥️ UI 已启动：{bind_text}", scope="global")
    return _ui_server


async def stop_ui_server():
    global _ui_server
    if _ui_server is None:
        return
    _ui_server.close()
    await _ui_server.wait_closed()
    _ui_server = None


__all__ = [
    "get_identity_ui_snapshot",
    "get_storage_bag_snapshot",
    "get_storage_bag_sync_snapshot",
    "get_ui_snapshot",
    "handle_ui_http",
    "html_escape",
    "render_ui_page",
    "start_ui_server",
    "stop_ui_server",
    "ui_add_identity",
    "ui_logout_account",
    "ui_preview_storage_bag_transfer",
    "ui_start_storage_bag_sync",
    "ui_set_storage_bag_item_rule",
    "ui_refresh_forum_topics",
    "ui_refresh_identity_info",
    "ui_set_basic_config",
    "ui_set_identity_enabled",
    "ui_set_module_enabled",
    "ui_set_jiyin_choice",
    "ui_set_nanlong_choice",
    "ui_set_module_window",
    "ui_set_pet_name",
    "ui_set_small_world_feature_enabled",
    "ui_set_stargazer_star_choice",
    "ui_sync_stargazer_total_slots",
    "ui_sync_tianti_status",
    "ui_set_tianti_feature_enabled",
]
