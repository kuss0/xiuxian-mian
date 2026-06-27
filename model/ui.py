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
    CMD_DUNGEON_HUANGLONG_JOIN,
    CMD_DUNGEON_JOIN,
    CMD_DUNGEON_ZHUIMO_JOIN,
    CMD_REPLICA_CANGKUN_JOIN,
    CMD_REPLICA_LUOYUN_JOIN,
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
from .features.join_dungeon import get_dungeon_join_inbox_snapshot
from .features.jiyin import apply_jiyin_choice, get_jiyin_choice_label, normalize_jiyin_choice, resolve_jiyin_choice
from .features.nanlong import apply_nanlong_choice, get_nanlong_choice_label, normalize_nanlong_choice, resolve_nanlong_choice
from .features.passive_inbox import get_passive_inbox_snapshot
from .features.quiz_ai import list_quiz_ai_models
from .features.stargazer import sync_stargazer_total_slots
from .features.storage_bag import CMD_STORAGE_BAG, STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX, cancel_storage_bag_transfer_task, format_storage_bag_listing_command, get_storage_bag_transfer_snapshot, normalize_storage_bag_listing_count, normalize_storage_bag_listing_syntax, start_storage_bag_gift_batch, start_storage_bag_gift_task, start_storage_bag_transfer_batch, start_storage_bag_transfer_task
from .features.tianti import sync_tianti_status
from .features.wild_training import apply_wild_training_strategy, normalize_wild_training_strategy
from .features.yinluo import execute_yinluo_manual_action, get_yinluo_ui_state, set_yinluo_auto_config
from .features.duel import apply_duel_config, normalize_duel_target
from .features.fishing import (
    FISHING_BAITS,
    FISHING_CHUMS,
    FISHING_DEFAULT_BUY_BAIT_COUNT,
    FISHING_PONDS,
    clamp_fishing_buy_bait_count,
    clamp_fishing_daily_limit,
    format_fishing_chum_names,
    normalize_fishing_config,
    plan_fishing_commands,
)
from .features.fishing_behavior import parse_chum_usage_counts
from .features.yuanying import get_yuanying_phase_text
from .official_schedule import (
    build_preset_plan as build_official_schedule_preset_plan,
    create_official_messages_for_batch,
    delete_local_schedule_records as delete_official_schedule_records,
    list_local_schedules as list_local_official_schedules,
    replace_planned_batch as replace_official_schedule_planned_batch,
)
from .persistence import save_state
from .runtime import _fire_and_forget, consume_unseen_startup_alerts, console_log, fetch_forum_topics, get_game_send_queue_snapshot, redeem_ui_login_token, send_audit_log, send_game_command, touch_ui_session
from .storage_bag_api_client import (
    REFRESH_PATH as STORAGE_BAG_API_REFRESH_PATH,
    VERIFY_PATH as STORAGE_BAG_API_VERIFY_PATH,
    StorageBagApiError,
    build_cultivator_path,
    fetch_storage_bag_result,
    normalize_storage_bag_api_cookie,
    verify_storage_bag_api,
)
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
    get_dungeon_join_run_state,
    get_replica_dispatch_group_ids,
    get_replica_dispatch_listener_account_map,
    get_replica_dispatch_participant_identity_ids,
    get_replica_gold_dps_enabled,
    get_replica_group_ids,
    get_replica_listener_account_map,
    get_replica_participant_identity_ids,
    get_replica_query_aggregator_config,
    get_replica_success_cooldown_hours,
    get_replica_virtual_hall_match_enabled_map,
    get_tiandao_judgement_enabled,
    get_guanxing_monitor_enabled,
    get_guanxing_monitor_target_options,
    get_guanxing_monitor_targets,
    get_guanxing_shift_delay_sec,
    get_guanxing_shift_target,
    is_auto_delete_sent_messages_enabled,
    get_identity_display_name,
    get_identity_enabled,
    get_identity_ids,
    get_identity_account,
    get_identity_account_map,
    get_identity_ui_display_name,
    get_identity_state,
    get_divination_daily_limit,
    get_module_window_hours_local,
    get_pending_command,
    get_quiz_ai_config,
    get_realm_sort_key,
    get_send_as_profile,
    is_replica_gold_dps_allowed,
    get_stargazer_star_choice,
    get_stargazer_total_slots,
    get_storage_bag_api_config,
    get_storage_bag_item_rules,
    get_storage_bag_records,
    get_tianjige_dao_path_records,
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
    set_guanxing_shift_delay_sec,
    set_quiz_ai_config,
    set_guanxing_shift_target,
    set_identity_account,
    set_identity_account_map,
    set_identity_enabled as set_identity_enabled_profile,
    set_divination_daily_limit,
    set_pet_name,
    set_pet_warm_name,
    set_pet_trial_name,
    set_replica_gold_dps_enabled,
    set_replica_dispatch_group_ids,
    set_replica_dispatch_listener_account_map,
    set_replica_dispatch_participant_identity_ids,
    set_replica_group_ids,
    set_replica_listener_account_map,
    set_replica_participant_identity_ids,
    set_replica_query_aggregator_config,
    set_replica_success_cooldown_hours,
    set_replica_virtual_hall_match_enabled_map,
    set_storage_bag_api_config,
    set_storage_bag_item_rules,
    set_storage_bag_records,
    set_tianjige_dao_path_records,
    set_stargazer_star_choice,
    set_tianti_rank_choice,
    state,
    update_send_as_profile,
    use_identity,
)
from .timing import fmt_abs_ts

_ui_server = None
_STORAGE_BAG_TRANSFER_METHODS = {"basic", "gift", "blocked", "unknown"}
_STORAGE_BAG_DEFAULT_TAG = "未知"
_STORAGE_BAG_DEFAULT_TAGS = [
    "货币",
    "丹方图纸图谱",
    "装备武器防具",
    "称号",
    "丹药",
    "灵草",
    "种子",
    "材料",
    "法则",
    "副本",
    "符箓",
    "特殊",
    "未知",
]
_STORAGE_BAG_TITLE_ITEMS = {"乱星海炼体第一人", "降伏年兽", "始皇的新衣", "真仙试锋", "紫灵的轻吻"}
_STORAGE_BAG_SPECIAL_ITEMS = {"稳控全场"}
UI_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
UI_FAVICON_PNG_PATH = os.path.join(UI_PROJECT_ROOT, "favicon.png")
UI_STORAGE_BAG_ITEM_RULES_PATH = os.path.join(UI_PROJECT_ROOT, "data", "storage_bag_item_rules.json")
UI_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
UI_TEMPLATE_DIR = os.path.join(UI_WEB_DIR, "pages")
UI_STATIC_DIR = os.path.join(UI_WEB_DIR, "static")
UI_WEB_NEW_DIR = os.path.join(os.path.dirname(__file__), "web_new")
UI_NEW_STATIC_DIR = os.path.join(UI_WEB_NEW_DIR, "static")
UI_STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
_storage_bag_sync_state = {"running": False, "pending_ids": [], "completed_ids": []}
_storage_bag_api_state = {
    "running": False,
    "running_kind": "",
    "keepalive_running": False,
    "last_ok": False,
    "last_message": "",
    "last_updated_at": 0,
    "updated_count": 0,
    "changed_count": 0,
    "skipped_count": 0,
    "dao_path_last_ok": False,
    "dao_path_last_message": "",
    "dao_path_last_updated_at": 0,
    "dao_path_updated_count": 0,
    "dao_path_skipped_count": 0,
}
_STORAGE_BAG_API_KEEPALIVE_INTERVAL_SEC = 30 * 60
_STORAGE_BAG_API_KEEPALIVE_BACKOFF_SEC = 10 * 60
_LOG_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")
_REPLICA_UI_KIND_VIRTUAL_HALL = "virtual_hall"
_REPLICA_UI_KIND_CANGKUN = "cangkun"
_REPLICA_UI_KIND_ZHUIMO = "zhuimo"
_REPLICA_UI_KIND_HUANGLONG = "huanglong"
_REPLICA_UI_OPEN_PRIORITY = (
    _REPLICA_UI_KIND_VIRTUAL_HALL,
    _REPLICA_UI_KIND_CANGKUN,
    _REPLICA_UI_KIND_ZHUIMO,
    _REPLICA_UI_KIND_HUANGLONG,
)
_REPLICA_UI_TICKET_META = {
    _REPLICA_UI_KIND_VIRTUAL_HALL: {"name": "虚天殿", "short": "虚", "items": ("虚天残图",)},
    _REPLICA_UI_KIND_CANGKUN: {"name": "苍坤洞府", "short": "苍", "items": ("苍坤残图",)},
    _REPLICA_UI_KIND_ZHUIMO: {"name": "坠魔谷", "short": "坠", "items": ("坠魔谷禁制令",)},
    _REPLICA_UI_KIND_HUANGLONG: {"name": "黄龙山", "short": "黄", "items": ("黄龙急援令", "黄龙急援令（宗门版）")},
}


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


def _infer_storage_bag_item_tags(item_name):
    name = str(item_name or "").strip()
    if not name:
        return []
    if name == "灵石":
        return ["货币"]
    if any(keyword in name for keyword in ("丹方", "图纸", "图谱")):
        return ["丹方图纸图谱"]
    if name in _STORAGE_BAG_SPECIAL_ITEMS or "残篇" in name:
        return ["特殊"]
    if name in _STORAGE_BAG_TITLE_ITEMS or name.endswith("第一人") or "称号" in name:
        return ["称号"]
    if "法则碎片" in name:
        return ["法则"]
    if any(keyword in name for keyword in ("残图", "通行令", "禁制令", "急援令", "坐标", "阵旗残片")):
        return ["副本"]
    if "符" in name:
        return ["符箓"]
    if "种子" in name:
        return ["种子"]
    if "元磁山核" in name:
        return ["材料"]
    if any(keyword in name for keyword in ("草", "芝", "果", "参", "花", "菌", "藤", "叶", "莲")):
        return ["灵草"]
    if any(keyword in name for keyword in ("剑", "盾", "幡", "刃", "甲", "衣", "翅", "瓶", "匣", "傀", "牌", "扇", "轮", "环", "钟", "塔", "鼎", "冠", "靴", "履", "袍", "带")):
        return ["装备武器防具"]
    if any(keyword in name for keyword in ("妖丹", "矿", "木髓", "丝", "羽", "翎", "晶", "尘埃", "壤", "庚金", "碎片", "核")):
        return ["材料"]
    if name in {"万年灵乳", "太虚仙露"} or name.endswith(("丹", "散", "液", "露", "乳")):
        return ["丹药"]
    if any(keyword in name for keyword in ("剑", "盾", "幡", "刃", "甲", "衣", "翅", "瓶", "山", "匣", "傀", "牌", "扇", "轮", "环", "钟", "塔", "鼎", "冠", "靴", "履", "袍", "带")):
        return ["装备武器防具"]
    return ["材料"]


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
    if not normalized_tags or normalized_tags == [_STORAGE_BAG_DEFAULT_TAG]:
        normalized_tags = _infer_storage_bag_item_tags(item_name) or [_STORAGE_BAG_DEFAULT_TAG]
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


def _is_storage_bag_api_busy():
    return bool(_storage_bag_api_state.get("running") or _storage_bag_api_state.get("keepalive_running"))


def _is_tianjige_manual_api_busy():
    return bool(_storage_bag_api_state.get("running") or _storage_bag_api_state.get("keepalive_running"))


def _storage_bag_api_set_running_kind(kind=""):
    _storage_bag_api_state["running_kind"] = str(kind or "")


def _is_storage_bag_transfer_busy():
    snapshot = get_storage_bag_transfer_snapshot() or {}
    batch = snapshot.get("batch") if isinstance(snapshot.get("batch"), dict) else {}
    return bool(snapshot.get("running") or batch.get("running"))


def get_storage_bag_api_snapshot():
    config = get_storage_bag_api_config()
    return {
        "configured": bool(config.get("cookie")),
        "running": _is_storage_bag_api_busy(),
        "manual_running": bool(_storage_bag_api_state.get("running")),
        "keepalive_running": bool(_storage_bag_api_state.get("keepalive_running")),
        "base_url": config.get("base_url") or "https://asc.aiopenai.app",
        "verify_path": STORAGE_BAG_API_VERIFY_PATH,
        "refresh_path": STORAGE_BAG_API_REFRESH_PATH,
        "api_token_configured": bool(config.get("api_token")),
        "cookie_configured": bool(config.get("cookie")),
        "verified": bool(config.get("verified_at")),
        "verified_at": fmt_abs_ts(config.get("verified_at") or 0),
        "keepalive_enabled": bool(config.get("keepalive_enabled")),
        "last_keepalive_at": fmt_abs_ts(config.get("last_keepalive_at") or 0),
        "last_keepalive_ok": bool(config.get("last_keepalive_ok")),
        "last_keepalive_error": str(config.get("last_keepalive_error") or ""),
        "next_keepalive_at": fmt_abs_ts(config.get("next_keepalive_at") or 0),
        "item_name_map_count": len(config.get("item_name_map") or {}),
        "last_ok": bool(_storage_bag_api_state.get("last_ok")),
        "last_message": str(_storage_bag_api_state.get("last_message") or ""),
        "last_updated_at": fmt_abs_ts(_storage_bag_api_state.get("last_updated_at") or 0),
        "updated_count": int(_storage_bag_api_state.get("updated_count") or 0),
        "changed_count": int(_storage_bag_api_state.get("changed_count") or 0),
        "skipped_count": int(_storage_bag_api_state.get("skipped_count") or 0),
        "dao_path_running": _is_tianjige_manual_api_busy(),
        "dao_path_last_ok": bool(_storage_bag_api_state.get("dao_path_last_ok")),
        "dao_path_last_message": str(_storage_bag_api_state.get("dao_path_last_message") or ""),
        "dao_path_last_updated_at": fmt_abs_ts(_storage_bag_api_state.get("dao_path_last_updated_at") or 0),
        "dao_path_updated_count": int(_storage_bag_api_state.get("dao_path_updated_count") or 0),
        "dao_path_skipped_count": int(_storage_bag_api_state.get("dao_path_skipped_count") or 0),
    }


def get_quiz_ai_snapshot():
    config = get_quiz_ai_config()
    provider_choices = [
        {"value": "codex", "label": "Codex / OpenAI"},
        {"value": "claude", "label": "Claude / Anthropic"},
    ]
    providers = []
    raw_providers = config.get("providers") if isinstance(config.get("providers"), list) else []
    for index, provider in enumerate(raw_providers[:6]):
        if not isinstance(provider, dict):
            continue
        providers.append({
            "id": provider.get("id") or f"ai{index + 1}",
            "enabled": bool(provider.get("enabled", True)),
            "label": provider.get("label") or f"AI {index + 1}",
            "provider": provider.get("provider") or "codex",
            "base_url": provider.get("base_url") or "",
            "model": provider.get("model") or "",
            "api_key_configured": bool(provider.get("api_key")),
            "timeout_sec": int(provider.get("timeout_sec") or config.get("timeout_sec") or 20),
            "temperature": float(provider.get("temperature") or 0),
        })
    if not providers:
        providers.append({
            "id": "ai1",
            "enabled": True,
            "label": "AI 1",
            "provider": config.get("provider") or "codex",
            "base_url": config.get("base_url") or "",
            "model": config.get("model") or "",
            "api_key_configured": bool(config.get("api_key")),
            "timeout_sec": int(config.get("timeout_sec") or 20),
            "temperature": float(config.get("temperature") or 0),
        })
    while len(providers) < 5:
        index = len(providers)
        providers.append({
            "id": f"ai{index + 1}",
            "enabled": False,
            "label": f"AI {index + 1}",
            "provider": "codex",
            "base_url": "",
            "model": "",
            "api_key_configured": False,
            "timeout_sec": int(config.get("timeout_sec") or 20),
            "temperature": 0.0,
        })
    return {
        "enabled": bool(config.get("enabled")),
        "auto_answer_enabled": bool(config.get("auto_answer_enabled")),
        "provider": config.get("provider") or "codex",
        "provider_choices": provider_choices,
        "base_url": config.get("base_url") or "",
        "model": config.get("model") or "",
        "api_key_configured": bool(config.get("api_key")),
        "confidence_threshold": float(config.get("confidence_threshold") or 0.8),
        "timeout_sec": int(config.get("timeout_sec") or 20),
        "decision_timeout_sec": float(config.get("decision_timeout_sec") or 20),
        "answer_safety_margin_sec": float(config.get("answer_safety_margin_sec") or 12),
        "temperature": float(config.get("temperature") or 0),
        "providers": providers,
        "last_question": config.get("last_question") or "",
        "last_answer": config.get("last_answer") or "",
        "last_confidence": float(config.get("last_confidence") or 0),
        "last_reason": config.get("last_reason") or "",
        "last_error": config.get("last_error") or "",
        "last_provider": config.get("last_provider") or "",
        "last_results": config.get("last_results") if isinstance(config.get("last_results"), list) else [],
        "last_vote_summary": config.get("last_vote_summary") or "",
        "last_provider_count": int(config.get("last_provider_count") or 0),
        "last_valid_count": int(config.get("last_valid_count") or 0),
        "last_decision_timeout_sec": float(config.get("last_decision_timeout_sec") or 0),
        "last_updated_at": fmt_abs_ts(config.get("last_updated_at") or 0),
    }


def _resolve_quiz_ai_provider_payload(raw_provider, *, index=0, current=None):
    raw_provider = raw_provider if isinstance(raw_provider, dict) else {}
    current = current or get_quiz_ai_config()
    current_providers = current.get("providers") if isinstance(current.get("providers"), list) else []
    current_by_id = {
        str(provider.get("id") or "").strip(): provider
        for provider in current_providers
        if isinstance(provider, dict) and str(provider.get("id") or "").strip()
    }
    provider_id = str(raw_provider.get("id") or f"ai{int(index or 0) + 1}").strip()
    previous = current_by_id.get(provider_id)
    if previous is None and 0 <= int(index or 0) < len(current_providers) and isinstance(current_providers[int(index or 0)], dict):
        previous = current_providers[int(index or 0)]
    previous = previous or {}
    provider = str(raw_provider.get("provider") or previous.get("provider") or "codex").strip().lower()
    if provider not in {"codex", "openai", "claude", "anthropic"}:
        return None, f"{provider_id} AI provider 无效"
    input_key = str(raw_provider.get("api_key") or "").strip()
    clear_key = _coerce_ui_bool(raw_provider.get("clear_api_key"))
    return {
        "id": provider_id,
        "enabled": _coerce_ui_bool(raw_provider.get("enabled")),
        "label": str(raw_provider.get("label") or previous.get("label") or f"AI {int(index or 0) + 1}").strip(),
        "provider": "claude" if provider in {"claude", "anthropic"} else "codex",
        "base_url": str(raw_provider.get("base_url") or "").strip().rstrip("/"),
        "model": str(raw_provider.get("model") or previous.get("model") or "").strip(),
        "api_key": "" if clear_key else (input_key or previous.get("api_key") or ""),
        "timeout_sec": raw_provider.get("timeout_sec", previous.get("timeout_sec", current.get("timeout_sec"))),
        "temperature": raw_provider.get("temperature", previous.get("temperature", current.get("temperature"))),
    }, ""


def ui_set_quiz_ai_config(payload):
    payload = payload if isinstance(payload, dict) else {}
    current = get_quiz_ai_config()
    raw_providers = payload.get("providers") if isinstance(payload.get("providers"), list) else None
    providers = []
    if raw_providers is not None:
        seen_ids = set()
        for index, raw_provider in enumerate(raw_providers[:6]):
            if not isinstance(raw_provider, dict):
                continue
            provider_id = str(raw_provider.get("id") or f"ai{index + 1}").strip()
            if not provider_id or provider_id in seen_ids:
                provider_id = f"ai{index + 1}"
                raw_provider = {**raw_provider, "id": provider_id}
            seen_ids.add(provider_id)
            provider_config, error = _resolve_quiz_ai_provider_payload(raw_provider, index=index, current=current)
            if error:
                return False, error
            providers.append(provider_config)
    else:
        provider_config, error = _resolve_quiz_ai_provider_payload({
            "id": "ai1",
            "enabled": True,
            "label": "AI 1",
            "provider": payload.get("provider"),
            "base_url": payload.get("base_url"),
            "model": payload.get("model"),
            "api_key": payload.get("api_key"),
            "clear_api_key": payload.get("clear_api_key"),
            "timeout_sec": payload.get("timeout_sec", current.get("timeout_sec")),
            "temperature": payload.get("temperature", current.get("temperature")),
        }, index=0, current=current)
        if error:
            return False, error
        providers.append(provider_config)
    first_provider = providers[0] if providers else {}
    next_config = {
        **current,
        "enabled": _coerce_ui_bool(payload.get("enabled")),
        "auto_answer_enabled": _coerce_ui_bool(payload.get("auto_answer_enabled")),
        "provider": first_provider.get("provider") or current.get("provider") or "codex",
        "base_url": first_provider.get("base_url") or "",
        "model": first_provider.get("model") or "",
        "api_key": first_provider.get("api_key") or "",
        "confidence_threshold": payload.get("confidence_threshold", current.get("confidence_threshold")),
        "timeout_sec": first_provider.get("timeout_sec", payload.get("timeout_sec", current.get("timeout_sec"))),
        "decision_timeout_sec": payload.get("decision_timeout_sec", current.get("decision_timeout_sec")),
        "answer_safety_margin_sec": payload.get("answer_safety_margin_sec", current.get("answer_safety_margin_sec")),
        "temperature": first_provider.get("temperature", payload.get("temperature", current.get("temperature"))),
        "providers": providers,
    }
    set_quiz_ai_config(next_config)
    save_state()
    return True, "已更新玄骨 AI 辅助配置"


async def ui_fetch_quiz_ai_models(payload):
    payload = payload if isinstance(payload, dict) else {}
    raw_provider = payload.get("provider_config") if isinstance(payload.get("provider_config"), dict) else payload
    try:
        index = int(payload.get("index", raw_provider.get("index", 0)) or 0)
    except (TypeError, ValueError):
        index = 0
    provider_config, error = _resolve_quiz_ai_provider_payload(raw_provider, index=index)
    if error:
        return False, error, None
    result = await list_quiz_ai_models(provider_config)
    if not result.get("ok"):
        return False, result.get("error") or "获取模型失败", {
            "models": [],
            "provider": result.get("provider") or provider_config.get("provider") or "",
            "label": result.get("label") or provider_config.get("label") or "",
        }
    models = result.get("models") if isinstance(result.get("models"), list) else []
    return True, f"已获取 {len(models)} 个模型", {
        "models": models,
        "provider": result.get("provider") or provider_config.get("provider") or "",
        "label": result.get("label") or provider_config.get("label") or "",
        "elapsed_ms": int(result.get("elapsed_ms") or 0),
    }


def _storage_bag_api_identity_lookup():
    lookup = {}
    for identity_id in get_identity_ids():
        identity_id = int(identity_id)
        profile = get_send_as_profile(identity_id)
        candidates = (
            str(identity_id),
            profile.get("username"),
            profile.get("label"),
            profile.get("daohao"),
            get_identity_ui_display_name(identity_id),
        )
        for candidate in candidates:
            key = str(candidate or "").strip().lstrip("@").casefold()
            if key:
                lookup[key] = identity_id
    return lookup


def _storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup):
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


def _storage_bag_api_candidate_from_value(value, *, normalize_suffix=False):
    candidate = str(value or "").strip().lstrip("@")
    if not candidate:
        return ""
    if normalize_suffix:
        candidate = re.sub(r"-\d{4,}$", "", candidate).strip()
    return candidate


def _storage_bag_api_cultivator_candidates(identity_id):
    profile = get_send_as_profile(identity_id)
    raw_candidates = [
        (profile.get("username"), False),
        (profile.get("label"), True),
        (profile.get("daohao"), True),
        (get_identity_display_name(identity_id), True),
        (get_identity_ui_display_name(identity_id), True),
    ]
    candidates = []
    seen = set()
    for raw_value, normalize_suffix in raw_candidates:
        candidate = _storage_bag_api_candidate_from_value(raw_value, normalize_suffix=normalize_suffix)
        if not candidate:
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _storage_bag_api_normalize_item_name(value):
    text = str(value or "").strip()
    return text.strip("[]【】")


_STORAGE_BAG_API_DEFAULT_ITEM_NAME_MAP = {
    "item_fishing_bait_plain": "凡饵",
    "item_fishing_bait_spirit_rice": "灵米饵",
    "item_fishing_bait_demon_blood": "妖血饵",
}


def _storage_bag_api_item_count(value):
    try:
        return int(str(value or 0).replace(",", "") or 0)
    except (TypeError, ValueError):
        return 0


def _storage_bag_api_add_item(items, name, count):
    name = _storage_bag_api_normalize_item_name(name)
    count = _storage_bag_api_item_count(count)
    if not name or count <= 0:
        return
    items[name] = items.get(name, 0) + count


def _storage_bag_api_resolve_item_name(item_name, item_name_map):
    item_name = _storage_bag_api_normalize_item_name(item_name)
    return str((item_name_map or {}).get(item_name) or _STORAGE_BAG_API_DEFAULT_ITEM_NAME_MAP.get(item_name) or item_name).strip()


def _storage_bag_api_extract_items(raw_inventory, item_name_map=None):
    items = {}
    seen_inventory = False

    if isinstance(raw_inventory, list):
        seen_inventory = True
        for item in raw_inventory:
            if not isinstance(item, dict):
                continue
            _storage_bag_api_add_item(
                items,
                item.get("name")
                or item.get("item_name")
                or item.get("display_name")
                or item.get("title")
                or _storage_bag_api_resolve_item_name(item.get("item_id") or item.get("id"), item_name_map),
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
                    _storage_bag_api_add_item(
                        items,
                        item.get("name")
                        or item.get("item_name")
                        or item.get("display_name")
                        or item.get("title")
                        or _storage_bag_api_resolve_item_name(item.get("item_id") or item.get("id"), item_name_map),
                        item.get("quantity") or item.get("amount") or item.get("count") or item.get("num") or item.get("value"),
                    )
        elif isinstance(value, dict):
            if key in {"materials", "inventory", "storage", "bag"}:
                for item_name, amount in value.items():
                    if isinstance(amount, dict):
                        _storage_bag_api_add_item(
                            items,
                            amount.get("name")
                            or amount.get("item_name")
                            or amount.get("display_name")
                            or amount.get("title")
                            or _storage_bag_api_resolve_item_name(item_name, item_name_map),
                            amount.get("quantity") or amount.get("amount") or amount.get("count") or amount.get("num") or amount.get("value"),
                        )
                    else:
                        _storage_bag_api_add_item(items, _storage_bag_api_resolve_item_name(item_name, item_name_map), amount)
            elif key == "items":
                for item_name, amount in value.items():
                    _storage_bag_api_add_item(items, _storage_bag_api_resolve_item_name(item_name, item_name_map), amount)

    if not items:
        for item_name, amount in raw_inventory.items():
            if item_name in {"owner", "owner_username", "source", "event_time", "raw_message_id", "chat_id", "msg_id", "updated_at"}:
                continue
            if isinstance(amount, (int, float, str)):
                _storage_bag_api_add_item(items, _storage_bag_api_resolve_item_name(item_name, item_name_map), amount)
    return items, seen_inventory


def _storage_bag_api_extract_owner_fields(row):
    if not isinstance(row, dict):
        return 0, ""
    identity_id = 0
    row = _tianjige_flatten_api_row(row)
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


def _storage_bag_api_apply_payload(payload, *, fallback_identity_id=0, fallback_owner_text=""):
    payload = payload if isinstance(payload, dict) else {}
    if isinstance(payload.get("data"), dict):
        payload = payload.get("data") or {}
    lookup = _storage_bag_api_identity_lookup()
    item_name_map = get_storage_bag_api_config().get("item_name_map") or {}
    records = dict(get_storage_bag_records())
    updated = 0
    changed = 0
    skipped = 0
    updated_identity_ids = set()
    now = time.time()

    def update_record(identity_id, owner_text, items, *, source="storage_bag_api"):
        nonlocal updated, changed
        identity_id = _storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
        if identity_id == 0 and int(fallback_identity_id or 0):
            identity_id = int(fallback_identity_id or 0)
        if not owner_text:
            owner_text = fallback_owner_text
        if identity_id == 0 or str(identity_id) not in records and identity_id not in get_identity_ids():
            return
        if not items:
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
            "empty": False,
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
            identity_id, owner_text = _storage_bag_api_extract_owner_fields(row)
            identity_id = _storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
            key = identity_id or owner_text or "unknown"
            grouped.setdefault(key, {"identity_id": identity_id, "owner_text": owner_text, "items": {}})
            _storage_bag_api_add_item(grouped[key]["items"], row.get("name") or row.get("item_name"), row.get("amount") or row.get("quantity") or row.get("count"))
        for row in grouped.values():
            update_record(row["identity_id"], row["owner_text"], row["items"], source="storage_bag_api_current")
        skipped += max(0, len(payload.get("current") or []) - updated)

    for snapshot in payload.get("snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        identity_id, owner_text = _storage_bag_api_extract_owner_fields(snapshot)
        identity_id = _storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
        items, seen_inventory = _storage_bag_api_extract_items(
            snapshot.get("items") or snapshot.get("inventory") or snapshot.get("storage") or snapshot.get("bag") or snapshot,
            item_name_map,
        )
        if not seen_inventory or not items:
            skipped += 1
            continue
        update_record(identity_id, owner_text, items, source="storage_bag_api_snapshot")

    for character in payload.get("characters") or []:
        if not isinstance(character, dict):
            continue
        identity_id, owner_text = _storage_bag_api_extract_owner_fields(character)
        identity_id = _storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
        inventory = character.get("inventory") or character.get("storage_bag") or character.get("bag") or {}
        items, seen_inventory = _storage_bag_api_extract_items(inventory, item_name_map)
        if not seen_inventory or not items:
            skipped += 1
            continue
        update_record(identity_id, owner_text, items, source="storage_bag_api_character")

    if payload.get("inventory") or payload.get("storage_bag") or payload.get("bag"):
        identity_id, owner_text = _storage_bag_api_extract_owner_fields(payload)
        identity_id = _storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
        inventory = payload.get("inventory") or payload.get("storage_bag") or payload.get("bag") or {}
        items, seen_inventory = _storage_bag_api_extract_items(inventory, item_name_map)
        if seen_inventory and items:
            update_record(identity_id, owner_text, items, source="storage_bag_api_cultivator")
        else:
            skipped += 1

    for key in ("storage_bag_records", "records"):
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        for owner_key, record in value.items():
            if not isinstance(record, dict):
                continue
            identity_id, owner_text = _storage_bag_api_extract_owner_fields(record)
            identity_id = _storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
            if identity_id == 0:
                identity_id = lookup.get(str(owner_key or "").strip().lstrip("@").casefold(), 0)
            items, seen_inventory = _storage_bag_api_extract_items(record.get("items") or record.get("inventory") or record, item_name_map)
            if not seen_inventory or not items:
                skipped += 1
                continue
            update_record(identity_id, owner_text or str(owner_key or ""), items, source="storage_bag_api_records")

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


def _tianjige_string(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip()
    if text.lower() in {"none", "null"}:
        return ""
    return text


def _tianjige_number(value, default=0):
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value or 0))
    except (TypeError, ValueError):
        return int(default)


def _tianjige_parse_number(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value or value.lower() in {"none", "null"}:
            return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _tianjige_float(value, default=0.0):
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def _tianjige_spirit_root_parts(text):
    value = _tianjige_string(text)
    if not value:
        return "", ""
    match = re.match(r"^(.*?)[（(]([^()（）]+)[)）]$", value)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return value, ""


def _tianjige_clean_sect_name(value):
    text = _tianjige_string(value)
    return text.strip("【】[]")


def _tianjige_parse_battle_power_value(value):
    text = _tianjige_string(value)
    if not text:
        return 0
    compact = text.replace(",", "").replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([万亿]?)", compact)
    if match:
        number = float(match.group(1))
        unit = match.group(2)
        if unit == "万":
            number *= 10_000
        elif unit == "亿":
            number *= 100_000_000
        return int(number)
    return _tianjige_number(text)


def _tianjige_compact_jsonable(value, *, max_items=8, max_depth=2):
    if max_depth < 0:
        return _tianjige_string(value)[:120]
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:max_items]:
            out[str(key)] = _tianjige_compact_jsonable(item, max_items=max_items, max_depth=max_depth - 1)
        return out
    if isinstance(value, list):
        return [_tianjige_compact_jsonable(item, max_items=max_items, max_depth=max_depth - 1) for item in value[:max_items]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _tianjige_string(value)


def _tianjige_parse_json_maybe(value):
    if isinstance(value, str):
        text = value.strip()
        if text and text[0] in "[{":
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def _tianjige_flatten_api_row(row):
    row = row if isinstance(row, dict) else {}
    flat = {}
    containers = (
        "user",
        "owner",
        "profile",
        "character",
        "cultivator",
        "role",
        "player",
        "status_info",
        "state",
    )
    for key in containers:
        value = _tianjige_parse_json_maybe(row.get(key))
        if isinstance(value, dict):
            flat.update(value)
    flat.update(row)

    dongfu = _tianjige_parse_json_maybe(row.get("dongfu") or row.get("cave"))
    if isinstance(dongfu, dict):
        flat.setdefault("dongfu", dongfu)
    return flat


def _tianjige_extract_known_fields(row, names):
    result = []
    seen = set()
    row = _tianjige_flatten_api_row(row)
    for key in names:
        if key in seen or key not in row:
            continue
        seen.add(key)
        value = _tianjige_parse_json_maybe(row.get(key))
        if value in ("", None, [], {}):
            continue
        result.append({
            "key": key,
            "value": _tianjige_compact_jsonable(value),
            "text": _tianjige_string(value) if not isinstance(value, (dict, list)) else json.dumps(_tianjige_compact_jsonable(value), ensure_ascii=False),
        })
    return result


_DAO_PATH_CAVE_KEYS = (
    "dongfu", "cave", "home", "residence", "mansion", "abode", "estate",
    "lingqi_pool", "spirit_pool", "spiritual_pool", "qi_pool",
    "lingmai_level", "spirit_vein_level", "spiritual_vein_level",
    "jingshi_level", "quiet_room_level", "meditation_room_level",
    "danfang_level", "alchemy_room_level", "qishi_level", "artifact_room_level",
    "shouyuan_level", "lifespan_room_level",
    "dazhen", "dazhen_level", "dazhen_mode", "dazhen_active", "formation_level", "formation_mode", "formation_active",
)
_DAO_PATH_STATUS_KEYS = (
    "status", "combat_status", "active_buffs", "completed_tasks", "sect_leave_cooldown_until",
    "weak_until", "cooldown_until", "busy_until", "last_action_at",
)
_TIANJIGE_SPIRITUAL_SENSE_KEYS = (
    "shenshi_points",
    "spiritual_sense",
    "spiritual_sense_points",
    "divine_sense",
    "divine_sense_points",
    "sense_points",
)
_TIANJIGE_TAIYI_SPIRITUAL_SENSE_KEYS = (
    "taiyi_shenshi_points",
    "taiyi_spiritual_sense",
    "taiyi_spiritual_sense_points",
)
_TIANJIGE_SECT_CONTRIBUTION_KEYS = (
    "sect_contribution",
    "sect_contrib",
    "contribution",
    "contrib",
    "contribution_points",
    "sect_points",
    "zongmen_contribution",
)
_TIANJIGE_LEVEL_GENERIC_KEYS = ("level", "rank", "grade", "等级", "级别")
_TIANJIGE_YUANYING_LEVEL_KEYS = (
    "yuanying_level",
    "yuan_ying_level",
    "nascent_soul_level",
    "nascent_soul_rank",
    "infant_level",
    "元婴等级",
    "元婴级别",
)
_TIANJIGE_SECOND_SOUL_LEVEL_KEYS = (
    "second_soul_level",
    "second_soul_rank",
    "secondSoulLevel",
    "secondSoulRank",
    "yuan_shen_level",
    "yuanshen_level",
    "第二元神等级",
    "第二元神级别",
)
_TIANJIGE_STATUS_LABELS = {
    "normal": "正常",
    "idle": "空闲",
    "busy": "忙碌",
    "weak": "虚弱",
    "dead": "死亡",
    "combat": "战斗中",
    "in_combat": "战斗中",
    "retreat": "闭关中",
}


def _tianjige_state_label(*values):
    parts = []
    for value in values:
        text = _tianjige_string(value)
        if not text:
            continue
        for part in re.split(r"\s*/\s*|[，,、]+", text):
            key = part.strip()
            if not key:
                continue
            label = _TIANJIGE_STATUS_LABELS.get(key.lower(), key)
            if label and label not in parts:
                parts.append(label)
    return " / ".join(parts)


def _tianjige_first_number(row, keys, *, default=0):
    row = row if isinstance(row, dict) else {}
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if value in (None, ""):
            continue
        return _tianjige_number(value, default=default)
    return default


def _tianjige_format_role_level_value(value):
    text = _tianjige_format_level_value(value)
    if not text:
        return ""
    if re.search(r"(级|阶|层|境|未)", text):
        return text
    return f"{text}级"


def _tianjige_first_role_level(row, direct_keys, container_keys):
    row = row if isinstance(row, dict) else {}
    direct = _tianjige_flatten_api_row(row)
    for key in direct_keys:
        value = direct.get(key)
        if value not in ("", None, [], {}):
            return _tianjige_format_role_level_value(value)
    for container_key in container_keys:
        container = _tianjige_parse_json_maybe(direct.get(container_key))
        if not isinstance(container, dict):
            continue
        for key in tuple(direct_keys) + _TIANJIGE_LEVEL_GENERIC_KEYS:
            value = container.get(key)
            if value not in ("", None, [], {}):
                return _tianjige_format_role_level_value(value)
    return ""


def _tianjige_first_status_field_text(record, keys):
    fields = record.get("status_fields") if isinstance(record, dict) else []
    if not isinstance(fields, list):
        return ""
    wanted = {str(key) for key in keys}
    for field in fields:
        if not isinstance(field, dict):
            continue
        if str(field.get("key") or "") not in wanted:
            continue
        text = _tianjige_string(field.get("text") or field.get("value"))
        if text:
            return text
    return ""


def _tianjige_cave_lingqi_text(cave):
    _key, value = _tianjige_pick_cave_value(cave, ("lingqi_pool", "spirit_pool", "spiritual_pool", "qi_pool"))
    return _tianjige_format_amount_value(value) if value not in ("", None, [], {}) else ""


def _tianjige_yuanying_level_text(record):
    direct = _tianjige_first_role_level(
        record,
        _TIANJIGE_YUANYING_LEVEL_KEYS,
        ("yuanying", "yuan_ying", "nascent_soul", "infant", "元婴"),
    )
    if direct:
        return direct
    return _tianjige_first_status_field_text(record, _TIANJIGE_YUANYING_LEVEL_KEYS)


def _tianjige_second_soul_level_text(record):
    direct = _tianjige_first_role_level(
        record,
        _TIANJIGE_SECOND_SOUL_LEVEL_KEYS,
        ("second_soul", "secondSoul", "yuanshen", "yuan_shen", "second_soul_info", "第二元神"),
    )
    if direct:
        return direct
    return _tianjige_first_status_field_text(record, _TIANJIGE_SECOND_SOUL_LEVEL_KEYS)


def _tianjige_pick_cave_value(cave, keys):
    cave = cave if isinstance(cave, dict) else {}
    for key in keys:
        if key not in cave:
            continue
        value = cave.get(key)
        if value in ("", None, [], {}):
            continue
        return key, value
    return "", None


def _tianjige_format_level_value(value):
    text = _tianjige_string(value)
    if not text:
        return ""
    try:
        number = float(str(text).replace(",", ""))
        if number.is_integer():
            return str(int(number))
    except (TypeError, ValueError):
        pass
    return text


def _tianjige_format_amount_value(value):
    text = _tianjige_string(value)
    if not text:
        return ""
    try:
        number = float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return text
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _tianjige_bool_label(value):
    if isinstance(value, bool):
        return "已开启" if value else "未开启"
    text = _tianjige_string(value).strip().lower()
    if text in {"1", "true", "yes", "on", "active", "enabled", "启用", "已启用", "已开启"}:
        return "已开启"
    if text in {"0", "false", "no", "off", "inactive", "disabled", "未启用", "关闭", "未开启"}:
        return "未开启"
    return _tianjige_string(value)


def _tianjige_collection_count(value):
    value = _tianjige_parse_json_maybe(value)
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 0


def _tianjige_cave_summary_text(cave):
    cave = cave if isinstance(cave, dict) else {}
    if not cave:
        return "未读取"
    used = set()
    parts = []

    def add_level(label, keys):
        key, value = _tianjige_pick_cave_value(cave, keys)
        if not key:
            return
        used.add(key)
        level = _tianjige_format_level_value(value)
        if level:
            parts.append(f"{label} {level}级")

    add_level("灵脉", ("lingmai_level", "spirit_vein_level", "spiritual_vein_level"))
    add_level("静室", ("jingshi_level", "quiet_room_level", "meditation_room_level"))
    add_level("丹房", ("danfang_level", "alchemy_room_level"))
    add_level("器室", ("qishi_level", "artifact_room_level"))
    add_level("兽园", ("shouyuan_level", "lifespan_room_level"))
    dazhen_key, dazhen_level = _tianjige_pick_cave_value(cave, ("dazhen_level", "formation_level"))
    active_key, dazhen_active = _tianjige_pick_cave_value(cave, ("dazhen_active", "formation_active"))
    mode_key, dazhen_mode = _tianjige_pick_cave_value(cave, ("dazhen_mode", "formation_mode"))
    if dazhen_key:
        used.add(dazhen_key)
        level = _tianjige_format_level_value(dazhen_level)
        text = f"大阵 {level}级" if level else "大阵"
        if active_key:
            used.add(active_key)
            active_label = _tianjige_bool_label(dazhen_active)
            if active_label:
                text += f"（{active_label}"
                if mode_key:
                    used.add(mode_key)
                    mode_text = _tianjige_string(dazhen_mode)
                    if mode_text:
                        text += f"·{mode_text}"
                text += "）"
        elif mode_key:
            used.add(mode_key)
            mode_text = _tianjige_string(dazhen_mode)
            if mode_text:
                text += f"（{mode_text}）"
        parts.append(text)
    elif active_key:
        used.add(active_key)
        active_label = _tianjige_bool_label(dazhen_active)
        parts.append(f"大阵：{active_label}" if active_label else "大阵")

    lingqi_key, lingqi_pool = _tianjige_pick_cave_value(cave, ("lingqi_pool", "spirit_pool", "spiritual_pool", "qi_pool"))
    if lingqi_key:
        used.add(lingqi_key)
        lingqi_text = _tianjige_cave_lingqi_text(cave)
        if lingqi_text:
            parts.append(f"灵气池 {lingqi_text}")

    scenery_key, scenery_value = _tianjige_pick_cave_value(cave, ("scenery_slots", "unlocked_scenery"))
    scenery_count = _tianjige_collection_count(scenery_value)
    if scenery_key and scenery_count > 0:
        used.add(scenery_key)
        parts.append(f"景观 {scenery_count}个")

    pavilion_key, pavilion_value = _tianjige_pick_cave_value(cave, ("pavilion_slots",))
    pavilion_count = _tianjige_collection_count(pavilion_value)
    if pavilion_key and pavilion_count > 0:
        used.add(pavilion_key)
        parts.append(f"亭台 {pavilion_count}项")

    return "｜".join(parts) if parts else "未读取"


def _tianjige_extract_cave_summary(row):
    row = _tianjige_flatten_api_row(row)
    cave = {}
    container_keys = ("dongfu", "cave", "home", "residence", "mansion", "abode", "estate")
    for key in _DAO_PATH_CAVE_KEYS:
        if key not in row:
            continue
        value = _tianjige_parse_json_maybe(row.get(key))
        if value in ("", None, [], {}):
            continue
        if key in container_keys and isinstance(value, dict):
            continue
        cave[key] = _tianjige_compact_jsonable(value)
    for container_key in container_keys:
        value = _tianjige_parse_json_maybe(row.get(container_key))
        if isinstance(value, dict):
            for key, item in value.items():
                if item in ("", None, [], {}):
                    continue
                cave[str(key)] = _tianjige_compact_jsonable(item)
    return cave


def _tianjige_profile_updates_from_row(row):
    row = _tianjige_flatten_api_row(row)
    updates = {}
    username = _tianjige_string(
        row.get("username")
        or row.get("owner_username")
        or row.get("telegram_username")
    )
    if username:
        updates["username"] = username.lstrip("@")
    role_label = _tianjige_string(row.get("role_name") or row.get("role") or row.get("name") or row.get("display_name"))
    if role_label:
        updates["label"] = role_label
    dao_name = _tianjige_string(row.get("dao_name") or row.get("daohao"))
    if dao_name:
        updates["daohao"] = dao_name
    realm = _tianjige_string(row.get("cultivation_level") or row.get("realm") or row.get("level"))
    if realm:
        updates["realm"] = realm
    cultivation_points = row.get("cultivation_points")
    if cultivation_points in (None, ""):
        cultivation_points = row.get("points")
    if cultivation_points not in (None, ""):
        parsed_points = _tianjige_parse_number(cultivation_points)
        if parsed_points is not None:
            updates["xiuwei_current"] = parsed_points
    sect_name = _tianjige_clean_sect_name(row.get("sect_name") or row.get("sect"))
    if sect_name:
        updates["sect_name"] = sect_name
    if any(key in row for key in _TIANJIGE_SECT_CONTRIBUTION_KEYS):
        updates["sect_contribution"] = _tianjige_first_number(row, _TIANJIGE_SECT_CONTRIBUTION_KEYS)
        updates["sect_contribution_updated_at"] = time.time()
    root_text = _tianjige_string(row.get("spirit_root") or row.get("spiritual_root") or row.get("spiritual_root_type"))
    if root_text:
        root_type, root_attrs = _tianjige_spirit_root_parts(root_text)
        if root_type:
            updates["spiritual_root_type"] = root_type
            if root_attrs:
                updates["spiritual_root_attrs"] = root_attrs
    root_attrs_override = _tianjige_string(
        row.get("spiritual_root_attrs")
        or row.get("spirit_root_attrs")
        or row.get("root_attrs")
    )
    if root_attrs_override:
        updates["spiritual_root_attrs"] = root_attrs_override
    battle_text = _tianjige_string(
        row.get("battle_power_text")
        or row.get("battle_power")
        or row.get("combat_power_text")
        or row.get("combat_power")
        or row.get("power_text")
    )
    if battle_text:
        updates["battle_power_text"] = battle_text
    battle_value = row.get("battle_power_value")
    if battle_value in (None, ""):
        battle_value = row.get("battle_power") or row.get("combat_power") or row.get("power")
    if battle_value not in (None, ""):
        updates["battle_power_value"] = _tianjige_parse_battle_power_value(battle_value)
    elif battle_text:
        updates["battle_power_value"] = _tianjige_parse_battle_power_value(battle_text)
    return updates


def _tianjige_dao_path_record_from_row(row, *, fallback_identity_id=0, fallback_owner_text="", source="tianjige", allowed_identity_ids=None):
    row = _tianjige_flatten_api_row(row)
    identity_id, owner_text = _storage_bag_api_extract_owner_fields(row)
    lookup = _storage_bag_api_identity_lookup()
    identity_id = _storage_bag_api_resolve_identity_id(identity_id, owner_text, lookup)
    if identity_id == 0 and int(fallback_identity_id or 0):
        identity_id = int(fallback_identity_id or 0)
    if not owner_text:
        owner_text = fallback_owner_text
    allowed_ids = {int(item or 0) for item in allowed_identity_ids or []} if allowed_identity_ids is not None else None
    if identity_id == 0 or identity_id not in get_identity_ids():
        return None
    if allowed_ids is not None and identity_id not in allowed_ids:
        return None
    profile_updates = _tianjige_profile_updates_from_row(row)
    if profile_updates:
        profile_updates["sect_updated_at"] = time.time()
        profile = update_send_as_profile(identity_id, **profile_updates)
    else:
        profile = get_send_as_profile(identity_id)
    username = _tianjige_string(row.get("username") or profile.get("username"))
    dao_name = _tianjige_string(row.get("dao_name") or row.get("daohao") or profile.get("daohao"))
    cultivation_level = _tianjige_string(row.get("cultivation_level") or row.get("level") or profile.get("realm"))
    sect_name = _tianjige_clean_sect_name(row.get("sect_name") or row.get("sect") or profile.get("sect_name"))
    spirit_root = _tianjige_string(row.get("spirit_root") or row.get("spiritual_root") or profile.get("spiritual_root_type"))
    now = time.time()
    status_text = _tianjige_string(row.get("status"))
    combat_status = _tianjige_string(row.get("combat_status"))
    state_label = _tianjige_state_label(status_text, combat_status)
    cave = _tianjige_extract_cave_summary(row)
    return {
        "identity_id": int(identity_id),
        "owner": owner_text or username or dao_name or profile.get("label") or str(identity_id),
        "label": profile.get("label") or profile.get("username") or profile.get("daohao") or str(identity_id),
        "username": username,
        "dao_name": dao_name,
        "telegram_id": _tianjige_number(row.get("telegram_id") or row.get("character_id") or identity_id),
        "binding_kind": _tianjige_string(row.get("binding_kind")),
        "binding_kind_label": _tianjige_string(row.get("binding_kind_label")),
        "cultivation_level": cultivation_level,
        "cultivation_points": _tianjige_number(row.get("cultivation_points") or row.get("points")),
        "sect_id": _tianjige_number(row.get("sect_id"), default=0),
        "sect_name": sect_name,
        "sect_contribution": _tianjige_first_number(row, _TIANJIGE_SECT_CONTRIBUTION_KEYS),
        "spirit_root": spirit_root,
        "status": status_text,
        "combat_status": combat_status,
        "state_label": state_label or "未记录",
        "spiritual_sense": _tianjige_first_number(row, _TIANJIGE_SPIRITUAL_SENSE_KEYS),
        "taiyi_spiritual_sense": _tianjige_first_number(row, _TIANJIGE_TAIYI_SPIRITUAL_SENSE_KEYS),
        "yuanying_level": _tianjige_yuanying_level_text(row),
        "second_soul_level": _tianjige_second_soul_level_text(row),
        "cave_lingqi": _tianjige_cave_lingqi_text(cave),
        "cave": cave,
        "status_fields": _tianjige_extract_known_fields(row, _DAO_PATH_STATUS_KEYS),
        "updated_at": float(now),
        "updated_at_text": fmt_abs_ts(now),
        "source": source,
        "raw_keys": sorted(str(key) for key in row.keys()),
    }


def _tianjige_binding_summary(payload):
    binding = payload.get("binding") if isinstance(payload, dict) else {}
    binding = binding if isinstance(binding, dict) else {}
    return {
        "active_character_id": _tianjige_number(binding.get("active_character_id")),
        "personal_id": _tianjige_number(binding.get("personal_id")),
        "bound_character_ids": [_tianjige_number(item) for item in binding.get("bound_character_ids") or []],
        "bound_personal_character_ids": [_tianjige_number(item) for item in binding.get("bound_personal_character_ids") or []],
        "bound_channel_character_ids": [_tianjige_number(item) for item in binding.get("bound_channel_character_ids") or []],
        "verified_channel_ids": [_tianjige_number(item) for item in binding.get("verified_channel_ids") or []],
        "web_self_service_enabled": bool(binding.get("web_self_service_enabled")),
    }


def _tianjige_apply_dao_path_payload(payload, *, fallback_identity_id=0, fallback_owner_text="", source="tianjige", allowed_identity_ids=None):
    payload = payload if isinstance(payload, dict) else {}
    if isinstance(payload.get("data"), dict):
        payload = payload.get("data") or {}
    records = dict(get_tianjige_dao_path_records())
    updated = 0
    skipped = 0
    updated_identity_ids = set()
    rows = []
    if isinstance(payload.get("characters"), list):
        rows.extend(row for row in payload.get("characters") or [] if isinstance(row, dict))
    candidate_payload = _tianjige_flatten_api_row(payload)
    if any(key in candidate_payload for key in (
        "username",
        "telegram_id",
        "telegram_user_id",
        "tg_id",
        "user_id",
        "character_id",
        "dao_name",
        "daohao",
        "cultivation_level",
        "inventory",
        "status",
        "combat_status",
        "dongfu",
        "cave",
    )):
        rows.append(payload)
    if not rows:
        skipped += 1
    for row in rows:
        record = _tianjige_dao_path_record_from_row(
            row,
            fallback_identity_id=fallback_identity_id,
            fallback_owner_text=fallback_owner_text,
            source=source,
            allowed_identity_ids=allowed_identity_ids,
        )
        if not record:
            skipped += 1
            continue
        records[str(record["identity_id"])] = record
        updated += 1
        updated_identity_ids.add(int(record["identity_id"]))
    meta = records.get("_meta") if isinstance(records.get("_meta"), dict) else {}
    if payload.get("binding"):
        meta = {
            **meta,
            "binding": _tianjige_binding_summary(payload),
            "updated_at": time.time(),
            "updated_at_text": fmt_abs_ts(time.time()),
        }
        records["_meta"] = meta
    if updated > 0 or payload.get("binding"):
        set_tianjige_dao_path_records(records)
        save_state()
    return {"updated_count": updated, "skipped_count": skipped, "updated_identity_ids": sorted(updated_identity_ids), "records": records}


def get_tianjige_dao_path_snapshot():
    records = get_tianjige_dao_path_records()
    rows = []
    for identity_id in get_identity_ids():
        identity_id = int(identity_id)
        profile = get_send_as_profile(identity_id)
        record = records.get(str(identity_id)) if isinstance(records, dict) else {}
        record = record if isinstance(record, dict) else {}
        updated_at_raw = _tianjige_float(record.get("updated_at"))
        rows.append({
            "identity_id": identity_id,
            "label": profile.get("label") or profile.get("username") or profile.get("daohao") or str(identity_id),
            "display_name": get_identity_ui_display_name(identity_id),
            "username": record.get("username") or profile.get("username") or "",
            "dao_name": record.get("dao_name") or profile.get("daohao") or "",
            "cultivation_level": record.get("cultivation_level") or profile.get("realm") or "",
            "cultivation_points": _tianjige_number(record.get("cultivation_points")),
            "sect_name": record.get("sect_name") or profile.get("sect_name") or "",
            "sect_contribution": _tianjige_number(record.get("sect_contribution") or profile.get("sect_contribution")),
            "spirit_root": record.get("spirit_root") or profile.get("spiritual_root_type") or "",
            "status": record.get("status") or "",
            "combat_status": record.get("combat_status") or "",
            "state_label": _tianjige_state_label(record.get("status"), record.get("combat_status"), record.get("state_label")) or "未读取",
            "spiritual_sense": _tianjige_number(record.get("spiritual_sense")),
            "taiyi_spiritual_sense": _tianjige_number(record.get("taiyi_spiritual_sense")),
            "binding_kind": record.get("binding_kind") or "",
            "binding_kind_label": record.get("binding_kind_label") or "",
            "cave": record.get("cave") if isinstance(record.get("cave"), dict) else {},
            "cave_summary": _tianjige_cave_summary_text(record.get("cave") if isinstance(record.get("cave"), dict) else {}),
            "status_fields": record.get("status_fields") if isinstance(record.get("status_fields"), list) else [],
            "raw_keys": record.get("raw_keys") if isinstance(record.get("raw_keys"), list) else [],
            "updated_at": fmt_abs_ts(updated_at_raw),
            "updated_at_raw": updated_at_raw,
            "source": record.get("source") or "",
            "has_remote": bool(record),
        })
    rows.sort(key=lambda row: get_realm_sort_key(get_send_as_profile(row["identity_id"]).get("realm"), row["identity_id"]))
    meta = records.get("_meta") if isinstance(records, dict) and isinstance(records.get("_meta"), dict) else {}
    return {
        "rows": rows,
        "binding": meta.get("binding") if isinstance(meta.get("binding"), dict) else {},
        "last_updated_at": fmt_abs_ts(_tianjige_float(meta.get("updated_at"))),
        "dao_path_last_ok": bool(_storage_bag_api_state.get("dao_path_last_ok")),
        "dao_path_last_message": str(_storage_bag_api_state.get("dao_path_last_message") or ""),
        "dao_path_last_updated_at": fmt_abs_ts(_tianjige_float(_storage_bag_api_state.get("dao_path_last_updated_at"))),
        "dao_path_updated_count": _tianjige_number(_storage_bag_api_state.get("dao_path_updated_count")),
        "dao_path_skipped_count": _tianjige_number(_storage_bag_api_state.get("dao_path_skipped_count")),
    }


def ui_set_storage_bag_api_config(payload):
    payload = payload if isinstance(payload, dict) else {}
    current = get_storage_bag_api_config()
    base_url = str(payload.get("base_url") or current.get("base_url") or "https://asc.aiopenai.app").strip().rstrip("/")
    input_cookie = normalize_storage_bag_api_cookie(payload.get("cookie"))
    input_token = str(payload.get("api_token") or "").strip()
    next_cookie = input_cookie or current.get("cookie") or ""
    next_api_token = input_token or current.get("api_token") or ""
    reset_verified = (
        bool(input_cookie and input_cookie != current.get("cookie"))
        or bool(input_token and input_token != current.get("api_token"))
        or bool(base_url and base_url != current.get("base_url"))
    )
    next_config = {
        "base_url": base_url,
        "api_token": next_api_token,
        "cookie": next_cookie,
        "item_name_map": current.get("item_name_map") or {},
        "keepalive_enabled": bool(current.get("keepalive_enabled")) and not reset_verified,
        "verified_at": 0 if reset_verified else current.get("verified_at"),
        "last_keepalive_at": current.get("last_keepalive_at") if not reset_verified else 0,
        "last_keepalive_ok": bool(current.get("last_keepalive_ok")) and not reset_verified,
        "last_keepalive_error": "" if reset_verified else current.get("last_keepalive_error"),
        "next_keepalive_at": current.get("next_keepalive_at") if not reset_verified else 0,
    }
    set_storage_bag_api_config(next_config)
    save_state()
    return True, "已更新天机阁储物袋 API 配置"


def _storage_bag_api_store_verified_result(result, now):
    current = get_storage_bag_api_config()
    item_name_map = dict(current.get("item_name_map") or {})
    item_name_map.update(result.get("item_name_map") or {})
    set_storage_bag_api_config({
        **current,
        "cookie": result.get("cookie") or current.get("cookie") or "",
        "api_token": result.get("api_token") or current.get("api_token") or "",
        "item_name_map": item_name_map,
        "keepalive_enabled": True,
        "verified_at": float(now),
        "last_keepalive_at": float(now),
        "last_keepalive_ok": True,
        "last_keepalive_error": "",
        "next_keepalive_at": float(now) + _STORAGE_BAG_API_KEEPALIVE_INTERVAL_SEC,
    })
    save_state()


def _storage_bag_api_store_failure(exc, now):
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
        "next_keepalive_at": float(now) + _STORAGE_BAG_API_KEEPALIVE_BACKOFF_SEC,
    })
    save_state()


def _storage_bag_api_store_session(cookie="", api_token=""):
    current = get_storage_bag_api_config()
    set_storage_bag_api_config({
        **current,
        "cookie": str(cookie or current.get("cookie") or "").strip(),
        "api_token": str(api_token or current.get("api_token") or "").strip(),
    })
    save_state()
    return get_storage_bag_api_config()


async def ui_verify_storage_bag_api(payload=None):
    if _is_storage_bag_api_busy():
        return False, "储物袋 API 正在进行中", get_storage_bag_api_snapshot()
    if payload:
        ui_set_storage_bag_api_config(payload)
    config = get_storage_bag_api_config()
    if not config.get("cookie"):
        return False, "请先填写天机阁 session Cookie", get_storage_bag_api_snapshot()
    _storage_bag_api_state["running"] = True
    _storage_bag_api_set_running_kind("verify")
    now = time.time()
    ok = False
    try:
        result = await verify_storage_bag_api(config)
        _storage_bag_api_store_verified_result(result, now)
        ok = True
        message = "天机阁验证成功，已启用低频保活"
        _storage_bag_api_state.update({
            "last_ok": True,
            "last_message": message,
            "last_updated_at": now,
            "updated_count": 0,
            "changed_count": 0,
            "skipped_count": 0,
        })
    except Exception as exc:
        _storage_bag_api_store_failure(exc, now)
        message = f"天机阁验证失败: {exc}"
        _storage_bag_api_state.update({
            "last_ok": False,
            "last_message": message,
            "last_updated_at": now,
            "updated_count": 0,
            "changed_count": 0,
            "skipped_count": 0,
        })
    finally:
        _storage_bag_api_state["running"] = False
        _storage_bag_api_set_running_kind("")
    return ok, message, get_storage_bag_api_snapshot()


def _format_storage_bag_api_refresh_message(total_updated, total_changed):
    total_updated = int(total_updated or 0)
    total_changed = int(total_changed or 0)
    if total_updated <= 0:
        return "API 已返回，但未匹配到可刷新身份"
    if total_changed > 0:
        return f"已刷新 {total_updated} 个身份的储物袋（内容变化 {total_changed} 个）"
    return f"已刷新 {total_updated} 个身份的储物袋（内容未变化）"


def _format_storage_bag_api_refresh_audit(ok, message, *, updated_count=0, changed_count=0, skipped_count=0):
    if ok:
        return (
            "📦 储物袋 API 读取成功："
            f"刷新 {int(updated_count or 0)} 个身份｜内容变化 {int(changed_count or 0)}｜跳过 {int(skipped_count or 0)}｜{message}"
        )
    return f"📦 储物袋 API 读取失败：{message}"


def _notify_storage_bag_api_refresh(ok, message, *, updated_count=0, changed_count=0, skipped_count=0):
    text = _format_storage_bag_api_refresh_audit(
        ok,
        message,
        updated_count=updated_count,
        changed_count=changed_count,
        skipped_count=skipped_count,
    )
    _fire_and_forget(send_audit_log(text, scope="global", limit=280, priority="medium"))


async def ui_refresh_storage_bag_from_api(payload=None, *, notify_log_group=False):
    if _is_storage_bag_api_busy():
        return False, "储物袋 API 读取正在进行中", get_storage_bag_api_snapshot()
    payload = payload if isinstance(payload, dict) else {}
    if payload:
        ui_set_storage_bag_api_config(payload)
    config = get_storage_bag_api_config()
    if not config.get("cookie"):
        message = "请先配置储物袋 API"
        if notify_log_group:
            _notify_storage_bag_api_refresh(False, message)
        return False, message, get_storage_bag_api_snapshot()
    _storage_bag_api_state["running"] = True
    _storage_bag_api_set_running_kind("storage_bag")
    ok = False
    message = ""
    try:
        active_config = dict(config)
        updated_identity_ids = set()
        total_updated = 0
        total_changed = 0
        total_skipped = 0
        me_result = await fetch_storage_bag_result(active_config, STORAGE_BAG_API_REFRESH_PATH)
        active_config = _storage_bag_api_store_session(me_result.cookie, me_result.api_token)
        me_payload = me_result.payload
        if isinstance(me_payload, dict) and me_payload.get("ok") is False:
            raise StorageBagApiError(str(me_payload.get("error") or "储物袋 API 返回失败"))
        me_result_data = _storage_bag_api_apply_payload(me_payload if isinstance(me_payload, dict) else {})
        updated_identity_ids.update(me_result_data.get("updated_identity_ids") or [])
        total_updated += int(me_result_data.get("updated_count") or 0)
        total_changed += int(me_result_data.get("changed_count") or 0)
        total_skipped += int(me_result_data.get("skipped_count") or 0)

        local_identity_ids = [int(identity_id or 0) for identity_id in get_identity_ids()]
        for identity_id in local_identity_ids:
            if identity_id <= 0 or identity_id in updated_identity_ids:
                continue
            candidates = _storage_bag_api_cultivator_candidates(identity_id)
            if not candidates:
                total_skipped += 1
                continue
            candidate_success = False
            last_error = None
            for candidate in candidates:
                try:
                    api_result = await fetch_storage_bag_result(active_config, build_cultivator_path(candidate))
                    active_config = _storage_bag_api_store_session(api_result.cookie, api_result.api_token)
                    api_payload = api_result.payload
                    if isinstance(api_payload, dict) and api_payload.get("ok") is False:
                        raise StorageBagApiError(str(api_payload.get("error") or "储物袋 API 返回失败"))
                    result = _storage_bag_api_apply_payload(
                        api_payload if isinstance(api_payload, dict) else {},
                        fallback_identity_id=identity_id,
                    )
                    total_updated += int(result.get("updated_count") or 0)
                    total_changed += int(result.get("changed_count") or 0)
                    total_skipped += int(result.get("skipped_count") or 0)
                    updated_identity_ids.update(result.get("updated_identity_ids") or [])
                    if int(result.get("updated_count") or 0) > 0:
                        candidate_success = True
                        break
                except StorageBagApiError as exc:
                    _storage_bag_api_store_session(exc.cookie, exc.api_token)
                    active_config = get_storage_bag_api_config()
                    last_error = exc
                    if exc.auth_failed or exc.rate_limited:
                        raise
                    if exc.status_code == 404:
                        continue
                    continue
            if not candidate_success:
                total_skipped += 1
        ok = total_updated > 0
        message = _format_storage_bag_api_refresh_message(total_updated, total_changed)
        _storage_bag_api_state.update({
            "last_ok": ok,
            "last_message": message,
            "last_updated_at": time.time(),
            "updated_count": int(total_updated),
            "changed_count": int(total_changed),
            "skipped_count": int(total_skipped),
        })
        if notify_log_group:
            _notify_storage_bag_api_refresh(
                ok,
                message,
                updated_count=total_updated,
                changed_count=total_changed,
                skipped_count=total_skipped,
            )
    except Exception as exc:
        _storage_bag_api_store_failure(exc, time.time())
        message = f"储物袋 API 读取失败: {exc}"
        _storage_bag_api_state.update({
            "last_ok": False,
            "last_message": message,
            "last_updated_at": time.time(),
            "updated_count": 0,
            "changed_count": 0,
            "skipped_count": 0,
        })
        if notify_log_group:
            _notify_storage_bag_api_refresh(False, message)
    finally:
        _storage_bag_api_state["running"] = False
        _storage_bag_api_set_running_kind("")
    return ok, message, get_storage_bag_api_snapshot()


async def _ui_refresh_tianjige_profile_fields_from_api(payload=None, *, target_identity_id=None, refresh_all=False):
    if _is_storage_bag_api_busy():
        return False, "天机阁读取正在进行中", get_storage_bag_api_snapshot()
    payload = payload if isinstance(payload, dict) else {}
    if payload:
        ui_set_storage_bag_api_config(payload)
    config = get_storage_bag_api_config()
    if not config.get("cookie"):
        return False, "请先配置天机阁 Cookie", get_storage_bag_api_snapshot()
    if target_identity_id is not None:
        try:
            target_identity_id = int(target_identity_id or 0)
        except (TypeError, ValueError):
            return False, "身份不存在", get_storage_bag_api_snapshot()
        if target_identity_id <= 0 or target_identity_id not in get_identity_ids():
            return False, "身份不存在", get_storage_bag_api_snapshot()
    _storage_bag_api_state["running"] = True
    _storage_bag_api_set_running_kind("dao_path_all" if refresh_all else "dao_path_single")
    ok = False
    message = ""
    try:
        active_config = dict(config)
        updated_identity_ids = set()
        total_updated = 0
        total_skipped = 0
        local_identity_ids = [int(identity_id or 0) for identity_id in get_identity_ids()]
        allowed_identity_ids = {int(target_identity_id)} if target_identity_id is not None else set(local_identity_ids)
        if refresh_all:
            me_result = await fetch_storage_bag_result(active_config, STORAGE_BAG_API_REFRESH_PATH)
            active_config = _storage_bag_api_store_session(me_result.cookie, me_result.api_token)
            me_payload = me_result.payload
            if isinstance(me_payload, dict) and me_payload.get("ok") is False:
                raise StorageBagApiError(str(me_payload.get("error") or "天机阁道途 API 返回失败"))
            me_result_data = _tianjige_apply_dao_path_payload(
                me_payload if isinstance(me_payload, dict) else {},
                source="tianjige_me",
                allowed_identity_ids=allowed_identity_ids,
            )
            updated_identity_ids.update(me_result_data.get("updated_identity_ids") or [])
            total_updated += int(me_result_data.get("updated_count") or 0)
            total_skipped += int(me_result_data.get("skipped_count") or 0)

        target_ids = local_identity_ids if refresh_all else [int(target_identity_id)] if target_identity_id is not None else local_identity_ids
        for identity_id in target_ids:
            if identity_id <= 0 or identity_id in updated_identity_ids:
                continue
            if identity_id not in allowed_identity_ids:
                continue
            candidates = _storage_bag_api_cultivator_candidates(identity_id)
            if not candidates:
                if refresh_all:
                    total_skipped += 1
                    continue
            candidate_success = False
            if not refresh_all and identity_id == int(target_identity_id or 0):
                candidates = candidates[:1] if candidates else []
            for candidate in candidates:
                try:
                    api_result = await fetch_storage_bag_result(active_config, build_cultivator_path(candidate))
                    active_config = _storage_bag_api_store_session(api_result.cookie, api_result.api_token)
                    api_payload = api_result.payload
                    if isinstance(api_payload, dict) and api_payload.get("ok") is False:
                        raise StorageBagApiError(str(api_payload.get("error") or "天机阁道途 API 返回失败"))
                    result = _tianjige_apply_dao_path_payload(
                        api_payload if isinstance(api_payload, dict) else {},
                        fallback_identity_id=identity_id,
                        fallback_owner_text=candidate,
                        source="tianjige_cultivator",
                        allowed_identity_ids=allowed_identity_ids,
                    )
                    total_updated += int(result.get("updated_count") or 0)
                    total_skipped += int(result.get("skipped_count") or 0)
                    updated_identity_ids.update(result.get("updated_identity_ids") or [])
                    if int(result.get("updated_count") or 0) > 0:
                        candidate_success = True
                        break
                except StorageBagApiError as exc:
                    _storage_bag_api_store_session(exc.cookie, exc.api_token)
                    active_config = get_storage_bag_api_config()
                    if exc.auth_failed or exc.rate_limited:
                        raise
                    if exc.status_code == 404:
                        continue
                    continue
            if not candidate_success and not refresh_all:
                me_result = await fetch_storage_bag_result(active_config, STORAGE_BAG_API_REFRESH_PATH)
                active_config = _storage_bag_api_store_session(me_result.cookie, me_result.api_token)
                me_payload = me_result.payload
                if isinstance(me_payload, dict) and me_payload.get("ok") is False:
                    raise StorageBagApiError(str(me_payload.get("error") or "天机阁道途 API 返回失败"))
                result = _tianjige_apply_dao_path_payload(
                    me_payload if isinstance(me_payload, dict) else {},
                    fallback_identity_id=identity_id,
                    fallback_owner_text=candidates[0] if candidates else "",
                    source="tianjige_me",
                    allowed_identity_ids=allowed_identity_ids,
                )
                total_updated += int(result.get("updated_count") or 0)
                total_skipped += int(result.get("skipped_count") or 0)
                updated_identity_ids.update(result.get("updated_identity_ids") or [])
                if int(result.get("updated_count") or 0) > 0:
                    candidate_success = True
            if not candidate_success:
                total_skipped += 1
        ok = total_updated > 0
        message = f"已更新 {total_updated} 个身份的道途快照" if ok else "天机阁已返回，但未匹配到本地身份"
        _storage_bag_api_state.update({
            "dao_path_last_ok": ok,
            "dao_path_last_message": message,
            "dao_path_last_updated_at": time.time(),
            "dao_path_updated_count": int(total_updated),
            "dao_path_skipped_count": int(total_skipped),
        })
    except Exception as exc:
        _storage_bag_api_store_failure(exc, time.time())
        message = f"天机阁道途读取失败: {exc}"
        _storage_bag_api_state.update({
            "dao_path_last_ok": False,
            "dao_path_last_message": message,
            "dao_path_last_updated_at": time.time(),
            "dao_path_updated_count": 0,
            "dao_path_skipped_count": 0,
        })
    finally:
        _storage_bag_api_state["running"] = False
        _storage_bag_api_set_running_kind("")
    return ok, message, get_storage_bag_api_snapshot()


async def ui_refresh_tianjige_dao_path_from_api(payload=None):
    return await _ui_refresh_tianjige_profile_fields_from_api(payload, refresh_all=True)


async def ui_refresh_identity_from_api(send_as_id, payload=None, *, refresh_all=False):
    payload = payload if isinstance(payload, dict) else {}
    if refresh_all:
        return await _ui_refresh_tianjige_profile_fields_from_api(payload, refresh_all=True)
    return await _ui_refresh_tianjige_profile_fields_from_api(payload, target_identity_id=send_as_id, refresh_all=False)


async def run_storage_bag_api_keepalive_scheduler(now):
    config = get_storage_bag_api_config()
    if not config.get("keepalive_enabled") or not config.get("cookie"):
        return
    if _is_storage_bag_api_busy():
        return
    if float(config.get("next_keepalive_at") or 0) > float(now):
        return
    _storage_bag_api_state["keepalive_running"] = True
    try:
        result = await verify_storage_bag_api(config)
        _storage_bag_api_store_verified_result(result, now)
        _storage_bag_api_state.update({
            "last_ok": True,
            "last_message": "天机阁低频保活成功",
            "last_updated_at": now,
        })
    except Exception as exc:
        _storage_bag_api_store_failure(exc, now)
        _storage_bag_api_state.update({
            "last_ok": False,
            "last_message": f"天机阁低频保活失败: {exc}",
            "last_updated_at": now,
        })
    finally:
        _storage_bag_api_state["keepalive_running"] = False


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
    if _is_storage_bag_transfer_busy():
        return False, "储物袋转移正在进行中，暂不允许同步"
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
    _fire_and_forget(_run_storage_bag_sync(normalized_ids))
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


def _normalize_storage_bag_batch_items(payload):
    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    normalized = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item_name = str(raw_item.get("item_name") or "").strip()
        if not item_name:
            continue
        try:
            quantity = int(raw_item.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        normalized.append({"item_name": item_name, "quantity": quantity})
    return normalized


def _resolve_storage_bag_batch_sources(payload, target_identity_id):
    known_ids = [int(item) for item in get_identity_ids()]
    requested_ids = payload.get("source_identity_ids") if isinstance(payload.get("source_identity_ids"), list) else []
    include_protected = bool(payload.get("include_protected"))
    if requested_ids:
        normalized_ids = []
        seen = set()
        for raw_id in requested_ids:
            try:
                identity_id = int(raw_id or 0)
            except (TypeError, ValueError):
                continue
            if identity_id in seen or identity_id not in known_ids or identity_id == target_identity_id:
                continue
            if not include_protected and _is_storage_bag_protected_identity(identity_id):
                continue
            seen.add(identity_id)
            normalized_ids.append(identity_id)
        return normalized_ids
    return [
        identity_id
        for identity_id in known_ids
        if identity_id != target_identity_id and (include_protected or not _is_storage_bag_protected_identity(identity_id))
    ]


def ui_preview_storage_bag_transfer(payload, *, operation="transfer"):
    payload = payload if isinstance(payload, dict) else {}
    operation = "gift" if str(operation or "").strip().lower() == "gift" else "transfer"
    is_gift_operation = operation == "gift"
    if payload.get("batch"):
        return ui_preview_storage_bag_transfer_batch(payload, operation=operation)
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
        return False, "请至少选择一个赠送物品" if is_gift_operation else "请至少选择一个转移物品", None

    normalized_items = []
    exchange_parts = []
    gift_items = []
    warnings = []
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
            return False, f"{item_name} 不可赠送" if is_gift_operation else f"{item_name} 不可转移", None
        if is_gift_operation:
            method = "gift"
        if quantity <= 0:
            return False, f"{item_name} 数量必须大于 0", None
        if source_count <= 0:
            warnings.append(f"{item_name} 未在来源快照中确认库存")
        elif quantity > source_count:
            warnings.append(f"{item_name} 计划 {quantity}，来源快照仅 {source_count}")
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
    requested_listing_count = normalize_storage_bag_listing_count(payload.get("listing_count") or 1)
    listing_syntax = normalize_storage_bag_listing_syntax(payload.get("listing_syntax") or STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX)
    listing_stock_count = 0
    commands = []
    if exchange_parts:
        if not listing_item:
            return False, "请选择目标身份用于上架的物品", None
        listing_stock_count = _get_storage_bag_item_count(rows, target_identity_id, listing_item)
        if listing_stock_count <= 0:
            warnings.append(f"上架物 {listing_item} 未在目标快照中确认库存")
        elif listing_stock_count < requested_listing_count:
            warnings.append(f"上架物 {listing_item} 计划 {requested_listing_count}，目标快照仅 {listing_stock_count}")
        commands.extend([
            {
                "identity_id": target_identity_id,
                "command": format_storage_bag_listing_command(
                    listing_item,
                    requested_listing_count,
                    exchange_parts,
                    listing_syntax=listing_syntax,
                ),
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
            "command": "赠送标记 <本次赠送ID>" if is_gift_operation else "转移标记 <本次转移ID>",
            "note": "目标身份先发送一条可回复的赠送定位消息" if is_gift_operation else "目标身份先发送一条可回复的标记消息",
        })
        for item in gift_items:
            commands.append({
                "identity_id": source_identity_id,
                "command": f".赠送 {item['item_name']}*{item['quantity']}",
                "note": "来源身份回复目标身份标记消息发送",
            })

    preview = {
        "operation": operation,
        "source_identity_id": source_identity_id,
        "target_identity_id": target_identity_id,
        "listing_item": listing_item,
        "listing_count": requested_listing_count,
        "listing_syntax": listing_syntax,
        "listing_stock_count": listing_stock_count,
        "items": normalized_items,
        "commands": commands,
        "warnings": warnings,
        "summary": f"赠送预览 {len(normalized_items)} 个物品，可手动开始执行" if is_gift_operation else f"预览 {len(normalized_items)} 个物品，可手动开始执行",
    }
    return True, "已生成赠送预览" if is_gift_operation else "已生成转移预览", preview


async def ui_start_storage_bag_transfer(payload):
    payload = payload if isinstance(payload, dict) else {}
    if _storage_bag_sync_state.get("running"):
        return False, "储物袋同步正在进行中，暂不允许转移", None
    if payload.get("batch"):
        return await ui_start_storage_bag_transfer_batch(payload)
    ok, message, preview = ui_preview_storage_bag_transfer(payload)
    if not ok:
        return False, message, None
    transfer_snapshot = get_storage_bag_transfer_snapshot()
    transfer_batch = transfer_snapshot.get("batch") or {}
    if transfer_snapshot.get("running") or transfer_batch.get("running"):
        return await start_storage_bag_transfer_batch(
            [{
                "source_identity_id": preview["source_identity_id"],
                "target_identity_id": preview["target_identity_id"],
                "items": preview.get("items") or [],
                "listing_item": preview.get("listing_item") or "",
                "listing_count": preview.get("listing_count") or 1,
                "listing_syntax": preview.get("listing_syntax") or STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
            }],
            target_identity_id=preview["target_identity_id"],
            listing_item=preview.get("listing_item") or "",
            listing_count=preview.get("listing_count") or 1,
            listing_syntax=preview.get("listing_syntax") or STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
            stop_on_error=True,
        )
    return await start_storage_bag_transfer_task(
        preview["source_identity_id"],
        preview["target_identity_id"],
        preview.get("items") or [],
        preview.get("listing_item") or "",
        listing_count=preview.get("listing_count") or 1,
        listing_syntax=preview.get("listing_syntax") or STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
    )


def ui_preview_storage_bag_gift(payload):
    return ui_preview_storage_bag_transfer(payload, operation="gift")


async def ui_start_storage_bag_gift(payload):
    payload = payload if isinstance(payload, dict) else {}
    if _storage_bag_sync_state.get("running"):
        return False, "储物袋同步正在进行中，暂不允许赠送", None
    if payload.get("batch"):
        return await ui_start_storage_bag_gift_batch(payload)
    ok, message, preview = ui_preview_storage_bag_gift(payload)
    if not ok:
        return False, message, None
    transfer_snapshot = get_storage_bag_transfer_snapshot()
    transfer_batch = transfer_snapshot.get("batch") or {}
    if transfer_snapshot.get("running") or transfer_batch.get("running"):
        return await start_storage_bag_gift_batch(
            [{
                "source_identity_id": preview["source_identity_id"],
                "target_identity_id": preview["target_identity_id"],
                "items": preview.get("items") or [],
            }],
            target_identity_id=preview["target_identity_id"],
            stop_on_error=True,
        )
    return await start_storage_bag_gift_task(
        preview["source_identity_id"],
        preview["target_identity_id"],
        preview.get("items") or [],
    )


def ui_preview_storage_bag_transfer_batch(payload, *, operation="transfer"):
    payload = payload if isinstance(payload, dict) else {}
    operation = "gift" if str(operation or "").strip().lower() == "gift" else "transfer"
    is_gift_operation = operation == "gift"
    try:
        target_identity_id = int(payload.get("target_identity_id") or 0)
    except (TypeError, ValueError):
        return False, "目标身份无效", None
    known_ids = set(int(item) for item in get_identity_ids())
    if target_identity_id not in known_ids:
        return False, "目标身份无效", None
    requested_items = _normalize_storage_bag_batch_items(payload)
    if not requested_items:
        return False, "请至少填写一个赠送物品" if is_gift_operation else "请至少填写一个物品", None

    sources = _resolve_storage_bag_batch_sources(payload, target_identity_id)
    if not sources:
        return False, "没有可用来源身份", None
    storage_bag = get_storage_bag_snapshot()
    rows = storage_bag.get("rows") or []
    listing_item = str(payload.get("listing_item") or "").strip()
    listing_count = normalize_storage_bag_listing_count(payload.get("listing_count") or 1)
    listing_syntax = normalize_storage_bag_listing_syntax(payload.get("listing_syntax") or STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX)
    mode = str(payload.get("mode") or "all").strip().lower()
    if mode not in {"all", "fixed"}:
        mode = "all"

    tasks = []
    skipped = []
    warnings = []
    for source_id in sources:
        task_items = []
        for request_item in requested_items:
            item_name = request_item["item_name"]
            source_count = _get_storage_bag_item_count(rows, source_id, item_name)
            if source_count <= 0:
                continue
            rule = _get_storage_bag_item_rule(item_name)
            method = rule.get("method") or "unknown"
            if method == "blocked":
                warnings.append(f"{item_name} 不可赠送，已跳过" if is_gift_operation else f"{item_name} 不可转移，已跳过")
                continue
            if is_gift_operation:
                method = "gift"
            requested_quantity = int(request_item.get("quantity") or 0)
            quantity = source_count if mode == "all" or requested_quantity <= 0 else min(requested_quantity, source_count)
            if quantity <= 0:
                continue
            task_items.append({
                "item_name": item_name,
                "quantity": quantity,
                "source_count": source_count,
                "target_count": _get_storage_bag_item_count(rows, target_identity_id, item_name),
                "method": method,
                "method_label": _storage_bag_transfer_method_label(method),
                "tags": rule.get("tags") or [_STORAGE_BAG_DEFAULT_TAG],
            })
        if not task_items:
            skipped.append(source_id)
            continue
        if not is_gift_operation and any(str(item.get("method") or "unknown") != "gift" for item in task_items) and not listing_item:
            return False, "请选择集中号用于上架的物品", None
        task_exchange_parts = [
            f"{item['item_name']}*{int(item['quantity'])}"
            for item in task_items
            if str(item.get("method") or "unknown") != "gift"
        ]
        source_row = next((row for row in rows if int(row.get("identity_id") or 0) == int(source_id)), {})
        target_row = next((row for row in rows if int(row.get("identity_id") or 0) == int(target_identity_id)), {})
        tasks.append({
            "source_identity_id": int(source_id),
            "source_label": source_row.get("label") or source_row.get("display_name") or str(source_id),
            "target_identity_id": target_identity_id,
            "target_label": target_row.get("label") or target_row.get("display_name") or str(target_identity_id),
            "listing_item": "" if is_gift_operation else listing_item,
            "listing_count": listing_count,
            "listing_syntax": listing_syntax,
            "listing_command": format_storage_bag_listing_command(
                listing_item,
                listing_count,
                task_exchange_parts,
                listing_syntax=listing_syntax,
            ) if task_exchange_parts and not is_gift_operation else "",
            "operation": operation,
            "items": task_items,
        })
    if not tasks:
        return False, "没有匹配库存的来源身份", None
    total_items = sum(len(task.get("items") or []) for task in tasks)
    total_quantity = sum(int(item.get("quantity") or 0) for task in tasks for item in (task.get("items") or []))
    preview = {
        "operation": operation,
        "target_identity_id": target_identity_id,
        "listing_item": "" if is_gift_operation else listing_item,
        "listing_count": listing_count,
        "listing_syntax": listing_syntax,
        "mode": mode,
        "tasks": tasks,
        "skipped_source_ids": skipped,
        "warnings": sorted(set(warnings)),
        "summary": f"批量赠送预览 {len(tasks)} 个来源，{total_items} 个条目，合计 {total_quantity}" if is_gift_operation else f"批量预览 {len(tasks)} 个来源，{total_items} 个条目，合计 {total_quantity}",
    }
    return True, "已生成批量赠送预览" if is_gift_operation else "已生成批量转移预览", preview


async def ui_start_storage_bag_transfer_batch(payload):
    if _storage_bag_sync_state.get("running"):
        return False, "储物袋同步正在进行中，暂不允许转移", None
    ok, message, preview = ui_preview_storage_bag_transfer_batch(payload)
    if not ok:
        return False, message, None
    return await start_storage_bag_transfer_batch(
        preview.get("tasks") or [],
        target_identity_id=preview.get("target_identity_id") or 0,
        listing_item=preview.get("listing_item") or "",
        listing_count=preview.get("listing_count") or 1,
        listing_syntax=preview.get("listing_syntax") or STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX,
        stop_on_error=not bool(payload.get("continue_on_error")),
    )


async def ui_start_storage_bag_gift_batch(payload):
    if _storage_bag_sync_state.get("running"):
        return False, "储物袋同步正在进行中，暂不允许赠送", None
    ok, message, preview = ui_preview_storage_bag_transfer_batch(payload, operation="gift")
    if not ok:
        return False, message, None
    return await start_storage_bag_gift_batch(
        preview.get("tasks") or [],
        target_identity_id=preview.get("target_identity_id") or 0,
        stop_on_error=not bool(payload.get("continue_on_error")),
    )


async def ui_cancel_storage_bag_transfer():
    return await cancel_storage_bag_transfer_task()


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


def _get_fishing_bait_inventory(send_as_id):
    records = get_storage_bag_records()
    record_key = str(int(send_as_id or 0))
    if record_key not in records:
        return None
    record = records.get(record_key) or {}
    items = record.get("items") if isinstance(record, dict) else {}
    if not isinstance(items, dict):
        return None
    return {str(name): int(count or 0) for name, count in items.items() if str(name or "").strip()}


def _format_fishing_command_plan(plan):
    if not plan:
        return "未生成"
    commands = list(plan.commands or ())
    if commands:
        return " -> ".join(commands)
    missing_resources = [
        f"{item.item_name}x{int(item.missing_count or 0)}"
        for item in plan.resource_requirements or ()
        if int(item.missing_count or 0) > 0
    ]
    if missing_resources:
        return "资源不足：" + "、".join(missing_resources)
    if plan.purchase_commands:
        return "需先补鱼饵：" + " -> ".join(plan.purchase_commands)
    return plan.blocked_reason or "未生成"


def _get_fishing_ui_config(identity_state):
    config = normalize_fishing_config(
        identity_state.get("fishing_pond") or "青溪浅滩",
        identity_state.get("fishing_bait") or "凡饵",
        auto_chum_enabled=bool(identity_state.get("fishing_auto_chum_enabled")),
        chum_name=identity_state.get("fishing_chum_name") or "",
        chum_names=identity_state.get("fishing_chum_names") or None,
        auto_buy_bait_enabled=bool(identity_state.get("fishing_auto_buy_bait_enabled")),
        auto_buy_bait_count=identity_state.get("fishing_auto_buy_bait_count", FISHING_DEFAULT_BUY_BAIT_COUNT),
        auto_probe_enabled=bool(identity_state.get("fishing_auto_probe_enabled")),
    )
    return config


def _coerce_fishing_daily_limit(value):
    return clamp_fishing_daily_limit(value)


def _coerce_fishing_buy_bait_count(value):
    return clamp_fishing_buy_bait_count(value)


def get_fishing_ui_snapshot(send_as_id, identity_state=None):
    send_as_id = int(send_as_id)
    identity_state = identity_state or get_identity_state(send_as_id)
    try:
        config = _get_fishing_ui_config(identity_state)
    except ValueError:
        config = normalize_fishing_config()
    bait_inventory = _get_fishing_bait_inventory(send_as_id)
    plan = plan_fishing_commands(
        config,
        bait_inventory=bait_inventory,
        chum_usage_counts=parse_chum_usage_counts(identity_state.get("fishing_chum_counts")),
        active_chum_name=identity_state.get("fishing_active_chum_name") or "",
        active_chum_rods_remaining=int(identity_state.get("fishing_chum_rods_remaining", 0) or 0),
    )
    requirements = []
    for requirement in plan.bait_requirements or ():
        requirements.append({
            "bait": requirement.bait,
            "item_key": requirement.item_key,
            "required_count": int(requirement.required_count or 0),
            "available_count": requirement.available_count,
            "missing_count": int(requirement.missing_count or 0),
        })
    resource_requirements = []
    for requirement in plan.resource_requirements or ():
        resource_requirements.append({
            "item_name": requirement.item_name,
            "required_count": int(requirement.required_count or 0),
            "available_count": requirement.available_count,
            "missing_count": int(requirement.missing_count or 0),
        })
    return {
        "pond": config.pond,
        "bait": config.bait,
        "daily_limit": _coerce_fishing_daily_limit(identity_state.get("fishing_daily_limit", 20)),
        "daily_day": identity_state.get("fishing_daily_day") or "",
        "daily_count": int(identity_state.get("fishing_daily_count", 0) or 0),
        "auto_chum_enabled": bool(config.auto_chum_enabled),
        "chum_name": config.chum_name,
        "chum_names": list(config.chum_names or ()),
        "auto_buy_bait_enabled": bool(config.auto_buy_bait_enabled),
        "auto_buy_bait_count": int(config.auto_buy_bait_count or FISHING_DEFAULT_BUY_BAIT_COUNT),
        "auto_probe_enabled": bool(config.auto_probe_enabled),
        "auto_open_fish_enabled": bool(identity_state.get("fishing_auto_open_fish_enabled", True)),
        "active_chum_name": identity_state.get("fishing_active_chum_name") or "",
        "chum_rods_remaining": int(identity_state.get("fishing_chum_rods_remaining", 0) or 0),
        "chum_day": identity_state.get("fishing_chum_day") or "",
        "chum_counts": parse_chum_usage_counts(identity_state.get("fishing_chum_counts")),
        "pond_choices": list(FISHING_PONDS),
        "bait_choices": list(FISHING_BAITS),
        "chum_choices": list(FISHING_CHUMS),
        "bait_inventory": bait_inventory if bait_inventory is not None else {},
        "bait_inventory_known": bait_inventory is not None,
        "plan": {
            "allow_start": bool(plan.allow_start),
            "commands": list(plan.commands or ()),
            "purchase_commands": list(plan.purchase_commands or ()),
            "blocked_reason": plan.blocked_reason or "",
            "summary": _format_fishing_command_plan(plan),
            "requirements": requirements,
            "resource_requirements": resource_requirements,
        },
    }


def _to_float(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def _dungeon_join_status_text(record, now):
    record = record if isinstance(record, dict) else {}
    pending_until = _to_float(record.get("pending_until"))
    cooldown_until = _to_float(record.get("cooldown_until"))
    active_until = _to_float(record.get("active_until"))
    if pending_until > now:
        return "等待回复"
    if cooldown_until > now:
        return "冷却中"
    if record.get("participating") and active_until > now:
        return "副本中"
    result = str(record.get("last_result") or "").strip()
    if result == "joined":
        return "已加入"
    if result == "success_cooldown":
        return "通关冷却"
    if result == "cooldown":
        return "冷却"
    if result == "failed":
        return "失败"
    return "空闲"


def get_dungeon_join_snapshot():
    now = time.time()
    records = get_dungeon_join_run_state()
    records = records if isinstance(records, dict) else {}
    rows = []
    enabled_count = 0
    for identity_id in get_identity_ids():
        identity_id = int(identity_id)
        identity_state = get_identity_state(identity_id)
        module_enabled = bool(identity_state.get("dungeon_join_enabled"))
        if module_enabled:
            enabled_count += 1
        record = records.get(str(identity_id)) if isinstance(records, dict) else {}
        record = record if isinstance(record, dict) else {}
        pending_until = _to_float(record.get("pending_until"))
        cooldown_until = _to_float(record.get("cooldown_until"))
        active_until = _to_float(record.get("active_until"))
        updated_at = _to_float(record.get("updated_at"))
        rows.append({
            "identity_id": identity_id,
            "display_name": get_identity_ui_display_name(identity_id),
            "identity_enabled": get_identity_enabled(identity_id),
            "module_enabled": module_enabled,
            "status_text": _dungeon_join_status_text(record, now),
            "room_id": str(record.get("room_id") or ""),
            "pending_room_id": str(record.get("pending_room_id") or ""),
            "pending_msg_id": int(record.get("pending_msg_id", 0) or 0),
            "last_result": str(record.get("last_result") or ""),
            "last_error": str(record.get("last_error") or ""),
            "joined_at": fmt_abs_ts(record.get("joined_at", 0) or 0),
            "active_until": fmt_abs_ts(active_until),
            "cooldown_until": fmt_abs_ts(cooldown_until),
            "pending_until": fmt_abs_ts(pending_until),
            "updated_at": fmt_abs_ts(updated_at),
        })
    rows.sort(key=lambda row: (
        0 if row["module_enabled"] else 1,
        get_realm_sort_key(get_send_as_profile(row["identity_id"]).get("realm"), row["identity_id"]),
    ))
    return {
        "enabled_count": enabled_count,
        "identity_count": len(rows),
        "commands": [
            {"name": "虚天殿", "join_command": CMD_DUNGEON_JOIN},
            {"name": "坠魔谷", "join_command": CMD_DUNGEON_ZHUIMO_JOIN},
            {"name": "黄龙山", "join_command": CMD_DUNGEON_HUANGLONG_JOIN},
            {"name": "苍坤洞府", "join_command": CMD_REPLICA_CANGKUN_JOIN},
            {"name": "落云秘圃", "join_command": CMD_REPLICA_LUOYUN_JOIN},
        ],
        "recent_announcements": get_dungeon_join_inbox_snapshot(limit=20),
        "rows": rows,
    }


def _normalize_ui_int_list(raw_value, *, allow_negative=False):
    if isinstance(raw_value, str):
        candidates = raw_value.replace("，", ",").replace("\n", ",").split(",")
    elif isinstance(raw_value, (list, tuple, set)):
        candidates = raw_value
    else:
        candidates = []
    normalized = []
    seen = set()
    for raw_item in candidates:
        try:
            item = int(raw_item)
        except (TypeError, ValueError):
            continue
        if item == 0 or (item < 0 and not allow_negative) or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _coerce_ui_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "on", "open", "enable", "enabled", "开", "开启", "启用"}:
        return True
    if text in {"", "0", "false", "no", "n", "off", "close", "disable", "disabled", "关", "关闭", "禁用"}:
        return False
    return bool(default)


def _get_replica_account_options():
    account_options = []
    runtime_accounts = _get_runtime_accounts_snapshot()
    for raw_account_id, account in runtime_accounts.items():
        try:
            account_id = int(raw_account_id)
        except (TypeError, ValueError):
            continue
        label = str((account or {}).get("username") or (account or {}).get("session") or account_id)
        status = str((account or {}).get("status") or "")
        account_options.append({
            "account_id": account_id,
            "label": label,
            "status": status,
            "offline": bool((account or {}).get("offline")),
        })
    account_options.sort(key=lambda item: (item["offline"], item["account_id"]))
    return account_options


def _get_replica_ui_storage_item_count(records, identity_id, item_name):
    record = records.get(str(identity_id)) if isinstance(records, dict) else {}
    if not isinstance(record, dict):
        return 0
    items = record.get("items")
    if not isinstance(items, dict):
        return 0
    try:
        return max(0, int(items.get(str(item_name or "").strip()) or 0))
    except (TypeError, ValueError):
        return 0


def _get_replica_ui_ticket_counts(records, identity_id):
    counts = {}
    for replica_kind, meta in _REPLICA_UI_TICKET_META.items():
        counts[replica_kind] = sum(
            _get_replica_ui_storage_item_count(records, identity_id, item_name)
            for item_name in meta.get("items", ())
        )
    return counts


def _format_replica_ui_ticket_summary(counts):
    parts = []
    for replica_kind in _REPLICA_UI_OPEN_PRIORITY:
        count = int((counts or {}).get(replica_kind) or 0)
        if count > 0:
            parts.append(f"{_REPLICA_UI_TICKET_META[replica_kind]['short']}x{count}")
    return " ".join(parts)


def _is_replica_ui_cangkun_realm_available(profile):
    realm = str((profile or {}).get("realm") or "").strip()
    try:
        xiuwei_max = int((profile or {}).get("xiuwei_max") or 0)
    except (TypeError, ValueError):
        xiuwei_max = 0
    if not realm and xiuwei_max <= 0:
        return False
    return get_realm_sort_key(realm, xiuwei_max=xiuwei_max) <= get_realm_sort_key("结丹初期")


def _get_replica_ui_openable_kinds(counts, profile=None):
    openable_kinds = []
    for replica_kind in _REPLICA_UI_OPEN_PRIORITY:
        if replica_kind == _REPLICA_UI_KIND_CANGKUN and not _is_replica_ui_cangkun_realm_available(profile):
            continue
        if int((counts or {}).get(replica_kind) or 0) > 0:
            openable_kinds.append(replica_kind)
    return openable_kinds


def _select_replica_ui_open_kind(openable_kinds):
    return openable_kinds[0] if len(openable_kinds or []) == 1 else ""


def _format_replica_ui_open_commands(identity_id, username, openable_kinds):
    selector = ("@" + str(username or "").lstrip("@")) if str(username or "").strip() else str(identity_id)
    commands = []
    for replica_kind in openable_kinds or []:
        meta = _REPLICA_UI_TICKET_META.get(replica_kind) or {}
        short = meta.get("short") or meta.get("name") or ""
        if not short:
            continue
        commands.append({
            "kind": replica_kind,
            "label": meta.get("name") or short,
            "short": short,
            "command": f".开启副本 {selector} {short}",
        })
    return commands


def get_replica_config_snapshot():
    group_ids = get_replica_group_ids()
    listener_map = get_replica_listener_account_map()
    dispatch_group_ids = get_replica_dispatch_group_ids()
    dispatch_listener_map = get_replica_dispatch_listener_account_map()
    query_aggregator_config = get_replica_query_aggregator_config()
    participant_ids = get_replica_participant_identity_ids()
    dispatch_participant_ids = get_replica_dispatch_participant_identity_ids()
    match_map = get_replica_virtual_hall_match_enabled_map()
    success_cooldown_hours = get_replica_success_cooldown_hours()
    storage_records = get_storage_bag_records()
    identity_options = []
    participant_set = {int(identity_id) for identity_id in participant_ids}
    for identity_id in get_identity_ids():
        identity_id = int(identity_id)
        profile = get_send_as_profile(identity_id)
        ticket_counts = _get_replica_ui_ticket_counts(storage_records, identity_id)
        openable_kinds = _get_replica_ui_openable_kinds(ticket_counts, profile)
        preferred_open_kind = _select_replica_ui_open_kind(openable_kinds)
        open_commands = _format_replica_ui_open_commands(identity_id, profile.get("username") or "", openable_kinds)
        identity_options.append({
            "identity_id": identity_id,
            "display_name": get_identity_ui_display_name(identity_id),
            "username": profile.get("username") or "",
            "label": profile.get("label") or "",
            "realm": profile.get("realm") or "",
            "spiritual_root_attrs": profile.get("spiritual_root_attrs") or "",
            "replica_professions": profile.get("replica_professions") or "",
            "sect_name": profile.get("sect_name") or "",
            "sect_contribution": int(profile.get("sect_contribution") or 0),
            "sect_contribution_updated_at": fmt_abs_ts(profile.get("sect_contribution_updated_at") or 0),
            "account_id": int(get_identity_account(identity_id) or 0),
            "identity_enabled": get_identity_enabled(identity_id),
            "participant": identity_id in participant_set,
            "gold_dps_allowed": is_replica_gold_dps_allowed(identity_id),
            "gold_dps_enabled": get_replica_gold_dps_enabled(identity_id),
            "ticket_counts": ticket_counts,
            "ticket_summary": _format_replica_ui_ticket_summary(ticket_counts),
            "can_open": bool(openable_kinds),
            "openable_kinds": openable_kinds,
            "open_commands": open_commands,
            "preferred_open_kind": preferred_open_kind,
            "preferred_open_label": (
                (_REPLICA_UI_TICKET_META.get(preferred_open_kind) or {}).get("name")
                if preferred_open_kind
                else ("需指定类型" if len(openable_kinds) > 1 else "")
            ),
        })
    identity_options.sort(key=lambda row: get_realm_sort_key(get_send_as_profile(row["identity_id"]).get("realm"), row["identity_id"]))
    return {
        "group_ids": group_ids,
        "listener_account_map": {str(group_id): int(listener_map.get(str(group_id)) or 0) for group_id in group_ids},
        "dispatch_group_ids": dispatch_group_ids,
        "dispatch_enabled": False,
        "dispatch_listener_account_map": {str(group_id): int(dispatch_listener_map.get(str(group_id)) or 0) for group_id in dispatch_group_ids},
        "query_aggregator_config": {
            "base_url": query_aggregator_config.get("base_url") or "",
            "client_id": query_aggregator_config.get("client_id") or "",
            "secret_configured": bool(query_aggregator_config.get("secret")),
            "configured": bool(
                query_aggregator_config.get("base_url")
                and query_aggregator_config.get("client_id")
                and query_aggregator_config.get("secret")
            ),
        },
        "participant_identity_ids": participant_ids,
        "dispatch_participant_identity_ids": dispatch_participant_ids,
        "virtual_hall_match_enabled_map": {str(group_id): bool(match_map.get(str(group_id), False)) for group_id in group_ids},
        "success_cooldown_hours": success_cooldown_hours,
        "account_options": _get_replica_account_options(),
        "identity_options": identity_options,
        "commands": [
            {"name": "轻量副本", "query_command": ".查询副本", "auto_open_command": ".开启副本 @用户名 <类型>", "join_command": ".加入副本 @用户名...", "dissolve_command": ".解散副本"},
        ],
    }


def ui_set_replica_config(payload):
    payload = payload if isinstance(payload, dict) else {}
    group_ids = _normalize_ui_int_list(payload.get("group_ids"), allow_negative=True)
    listener_input = payload.get("listener_account_map") if isinstance(payload.get("listener_account_map"), dict) else {}
    listener_map = {}
    for group_id in group_ids:
        try:
            account_id = int(listener_input.get(str(group_id)) or listener_input.get(group_id) or 0)
        except (TypeError, ValueError):
            account_id = 0
        if account_id > 0:
            listener_map[str(group_id)] = account_id

    dispatch_group_input_present = "dispatch_group_ids" in payload
    dispatch_listener_input_present = isinstance(payload.get("dispatch_listener_account_map"), dict)
    raw_dispatch_group_ids = (
        _normalize_ui_int_list(payload.get("dispatch_group_ids"), allow_negative=True)
        if dispatch_group_input_present
        else get_replica_dispatch_group_ids()
    )
    dispatch_listener_input = payload.get("dispatch_listener_account_map") if dispatch_listener_input_present else get_replica_dispatch_listener_account_map()
    dispatch_listener_map = {}
    for group_id in raw_dispatch_group_ids:
        try:
            account_id = int(dispatch_listener_input.get(str(group_id)) or dispatch_listener_input.get(group_id) or 0)
        except (TypeError, ValueError):
            account_id = 0
        if account_id > 0:
            dispatch_listener_map[str(group_id)] = account_id

    participant_ids = _normalize_ui_int_list(payload.get("participant_identity_ids"))
    dispatch_participant_input_present = "dispatch_participant_identity_ids" in payload
    dispatch_participant_ids = (
        _normalize_ui_int_list(payload.get("dispatch_participant_identity_ids"))
        if dispatch_participant_input_present
        else get_replica_dispatch_participant_identity_ids()
    )
    match_input = payload.get("virtual_hall_match_enabled_map") if isinstance(payload.get("virtual_hall_match_enabled_map"), dict) else {}
    match_map = {
        str(group_id): _coerce_ui_bool(match_input.get(str(group_id), match_input.get(group_id)))
        for group_id in group_ids
    }
    query_aggregator_input = payload.get("query_aggregator_config")
    if isinstance(query_aggregator_input, dict):
        current_query_aggregator = get_replica_query_aggregator_config()
        next_secret = str(query_aggregator_input.get("secret") or "").strip()
        if not next_secret:
            next_secret = current_query_aggregator.get("secret") or ""
        set_replica_query_aggregator_config({
            "base_url": query_aggregator_input.get("base_url"),
            "client_id": query_aggregator_input.get("client_id"),
            "secret": next_secret,
        })
    success_cooldown_input = payload.get("success_cooldown_hours")
    if isinstance(success_cooldown_input, dict):
        set_replica_success_cooldown_hours(success_cooldown_input)

    set_replica_group_ids(group_ids)
    set_replica_listener_account_map(listener_map)
    set_replica_dispatch_group_ids(raw_dispatch_group_ids)
    set_replica_dispatch_listener_account_map(dispatch_listener_map)
    set_replica_participant_identity_ids(participant_ids)
    set_replica_dispatch_participant_identity_ids(dispatch_participant_ids)
    set_replica_virtual_hall_match_enabled_map(match_map)
    save_state()
    dispatch_group_ids = get_replica_dispatch_group_ids()
    ignored_dispatch_group_ids = [group_id for group_id in raw_dispatch_group_ids if group_id not in set(dispatch_group_ids)]
    message = f"已更新副本群配置：轻量群 {len(group_ids)} 个，主线拉人群 {len(dispatch_group_ids)} 个（已停用），本地参与 {len(get_replica_participant_identity_ids())} 个，主线参与 {len(get_replica_dispatch_participant_identity_ids())} 个"
    if ignored_dispatch_group_ids:
        message += f"，已忽略与游戏群/轻量群重叠的拉人群 {len(ignored_dispatch_group_ids)} 个"
    return True, message


def ui_set_replica_gold_dps_enabled(send_as_id, enabled):
    try:
        send_as_id = int(send_as_id)
    except (TypeError, ValueError):
        return False, "身份参数无效"
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    enabled = _coerce_ui_bool(enabled)
    if enabled and not is_replica_gold_dps_allowed(send_as_id):
        return False, "该身份灵根不满足金/雷 DPS 条件"
    set_replica_gold_dps_enabled(send_as_id, enabled)
    save_state()
    action_text = "开启" if get_replica_gold_dps_enabled(send_as_id) else "关闭"
    return True, f"已{action_text}金/雷 DPS：{get_identity_ui_display_name(send_as_id)}"


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


def _split_log_query_terms(q_text):
    terms = []
    seen = set()
    for term in re.split(r"\s+", str(q_text or "").strip()):
        normalized = term.casefold()
        if normalized and normalized not in seen:
            terms.append(normalized)
            seen.add(normalized)
    return terms


def _iter_log_button_texts(entry):
    for row in entry.get("buttons") or []:
        if not isinstance(row, list):
            continue
        for button in row:
            if isinstance(button, dict):
                text = str(button.get("text") or "")
                if text:
                    yield text


def _log_entry_matches_query(item, query_terms):
    if not query_terms:
        return True
    haystacks = [
        "\n".join(
            [
                str(item.get("ts") or ""),
                str(item.get("event_type") or ""),
                str(item.get("sender_id") or ""),
                str(item.get("message_id") or ""),
                str(item.get("text") or ""),
            ]
        ).casefold()
    ]
    haystacks.extend(text.casefold() for text in _iter_log_button_texts(item))
    return all(any(term in haystack for haystack in haystacks) for term in query_terms)


def _iter_log_lines(full_path, newest_first=True):
    with open(full_path, "rb") as fp:
        if not newest_first:
            for raw_line in fp:
                yield raw_line.decode("utf-8", errors="ignore")
            return

        fp.seek(0, os.SEEK_END)
        position = fp.tell()
        buffer = b""
        while position > 0:
            read_size = min(8192, position)
            position -= read_size
            fp.seek(position)
            chunk = fp.read(read_size)
            buffer = chunk + buffer
            lines = buffer.split(b"\n")
            buffer = lines[0]
            for raw_line in reversed(lines[1:]):
                if raw_line:
                    yield raw_line.decode("utf-8", errors="ignore")
        if buffer:
            yield buffer.decode("utf-8", errors="ignore")


def _read_log_entries(date_str, q_text="", types_set=None, sender_id=0, offset=0, limit=80, newest_first=True):
    date_str = str(date_str or "").strip()
    if not date_str or not _LOG_FILE_RE.match(f"{date_str}.log"):
        return {"entries": [], "total": 0, "has_more": False, "offset": 0, "limit": int(limit or 80)}

    full_path = os.path.abspath(os.path.join(MESSAGES_DIR, f"{date_str}.log"))
    messages_dir = os.path.abspath(MESSAGES_DIR)
    if not full_path.startswith(messages_dir + os.sep) or not os.path.isfile(full_path):
        return {"entries": [], "total": 0, "has_more": False, "offset": 0, "limit": int(limit or 80)}

    query_terms = _split_log_query_terms(q_text)
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
        for line in _iter_log_lines(full_path, newest_first=newest_first):
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
            if not _log_entry_matches_query(item, query_terms):
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
                    "buttons": item.get("buttons") if isinstance(item.get("buttons"), list) else [],
                }
            )
    except OSError:
        entries = []

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
                "cmd": get_pending_command(item),
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
        dao_path_records = get_tianjige_dao_path_records()
        dao_path_record = dao_path_records.get(str(send_as_id)) if isinstance(dao_path_records, dict) else {}
        dao_path_record = dao_path_record if isinstance(dao_path_record, dict) else {}
        dao_path_cave = dao_path_record.get("cave") if isinstance(dao_path_record.get("cave"), dict) else {}
        yuanying_level_text = (
            _tianjige_string(dao_path_record.get("yuanying_level"))
            or _tianjige_yuanying_level_text(dao_path_record)
        )
        second_soul_level_text = (
            _tianjige_string(dao_path_record.get("second_soul_level"))
            or _tianjige_second_soul_level_text(dao_path_record)
        )
        cave_lingqi_text = (
            _tianjige_string(dao_path_record.get("cave_lingqi"))
            or _tianjige_cave_lingqi_text(dao_path_cave)
        )

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
            "replica_gold_dps_allowed": is_replica_gold_dps_allowed(send_as_id),
            "replica_gold_dps_enabled": get_replica_gold_dps_enabled(send_as_id),
            "pet_name": profile.get("pet_name") or "",
            "pet_warm_name": profile.get("pet_warm_name") or profile.get("pet_name") or "",
            "pet_trial_name": profile.get("pet_trial_name") or profile.get("pet_name") or "",
            "sect_name": profile.get("sect_name") or "",
            "sect_contribution": int(profile.get("sect_contribution") or 0),
            "sect_contribution_updated_at": fmt_abs_ts(profile.get("sect_contribution_updated_at") or 0),
            "xiuwei_current": int(profile.get("xiuwei_current") or 0),
            "xiuwei_max": int(profile.get("xiuwei_max") or 0),
            "battle_power_text": profile.get("battle_power_text") or "",
            "battle_power_value": int(profile.get("battle_power_value") or 0),
            "spiritual_sense": _tianjige_number(dao_path_record.get("spiritual_sense")),
            "taiyi_spiritual_sense": _tianjige_number(dao_path_record.get("taiyi_spiritual_sense")),
            "yuanying_level_text": yuanying_level_text or "未读取",
            "second_soul_level_text": second_soul_level_text or "未读取",
            "cave_lingqi_text": cave_lingqi_text or "未读取",
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
            "duel_target": identity_state.get("duel_target") or "",
            "duel_total_count": int(identity_state.get("duel_total_count", 0) or 0),
            "duel_completed_count": int(identity_state.get("duel_completed_count", 0) or 0),
            "duel_next_time": fmt_abs_ts(identity_state.get("next_duel_time", 0) or 0),
            "duel_last_result": identity_state.get("duel_last_result") or "",
            "duel_last_error": identity_state.get("duel_last_error") or "",
            "fishing": get_fishing_ui_snapshot(send_as_id, identity_state),
            "divination_daily_limit": get_divination_daily_limit(send_as_id),
            "second_soul_auto_choice_enabled": bool(identity_state.get("second_soul_auto_choice_enabled", True)),
            "second_soul_choice_strategy": identity_state.get("second_soul_choice_strategy") or "stable",
            "second_soul_choice_strategy_choices": [
                {"value": "stable", "label": "稳固道心"},
                {"value": "break", "label": "强行突破"},
            ],
            "tianti_cycle_count": int(identity_state.get("tianti_cycle_count", 0) or 0),
            "tianti_wenxin_enabled": bool(identity_state.get("tianti_wenxin_enabled", True)),
            "tianti_gangfeng_enabled": bool(identity_state.get("tianti_gangfeng_enabled", True)),
            "small_world_preach_enabled": bool(identity_state.get("small_world_preach_enabled", False)),
            "small_world_manifest_enabled": bool(identity_state.get("small_world_manifest_enabled", False)),
            "small_world_harvest_enabled": bool(identity_state.get("small_world_harvest_enabled", False)),
            "small_world_refine_enabled": bool(identity_state.get("small_world_refine_enabled", False)),
            "small_world_refresh_enabled": bool(identity_state.get("small_world_refresh_enabled", False)),
            "small_world_barrier_enabled": bool(identity_state.get("small_world_barrier_enabled", True)),
            "small_world_barrier_min_stock": int(identity_state.get("small_world_barrier_min_stock", 130000) or 130000),
            "small_world_barrier_guard_before_min": int(identity_state.get("small_world_barrier_guard_before_min", 30) or 30),
            "small_world_barrier_min_interval_hours": float(identity_state.get("small_world_barrier_min_interval_hours", 18) or 18),
            "small_world_incense_stock": int(identity_state.get("small_world_incense_stock", 0) or 0),
            "small_world_faith_value": int(identity_state.get("small_world_faith_value", 0) or 0),
            "yinluo": get_yinluo_ui_state() if "阴罗宗" in available_module_names else {},
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
        "guanxing_shift_delay_sec": get_guanxing_shift_delay_sec(),
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
        "passive_inbox": get_passive_inbox_snapshot(),
        "official_schedules": list_local_official_schedules(limit=200),
        "storage_bag": get_storage_bag_snapshot(),
        "storage_bag_api": get_storage_bag_api_snapshot(),
        "quiz_ai": get_quiz_ai_snapshot(),
        "tianjige_dao_path": get_tianjige_dao_path_snapshot(),
        "storage_bag_sync": get_storage_bag_sync_snapshot(),
        "storage_bag_transfer": get_storage_bag_transfer_snapshot(),
        "dungeon_join": get_dungeon_join_snapshot(),
        "replica": get_replica_config_snapshot(),
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


def _load_static_asset_from(static_dir, asset_path):
    normalized_path = (asset_path or "").lstrip("/")
    asset_full_path = os.path.normpath(os.path.join(static_dir, normalized_path))
    if not asset_full_path.startswith(static_dir + os.sep):
        return None, None
    if not os.path.isfile(asset_full_path):
        return None, None
    content_type = UI_STATIC_CONTENT_TYPES.get(os.path.splitext(asset_full_path)[1].lower())
    if not content_type:
        return None, None
    with open(asset_full_path, "rb") as fp:
        return fp.read(), content_type


def _load_ui_static_asset(asset_path):
    return _load_static_asset_from(UI_STATIC_DIR, asset_path)


def _load_new_static_asset(asset_path):
    return _load_static_asset_from(UI_NEW_STATIC_DIR, asset_path)


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


def render_ui_page(message="", selected_send_as_id=None, session_token=None, variant="new"):
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
    is_new_variant = True
    ui_mode_link = ""
    return _render_ui_template(
        "index.html",
        {
            "boot_data": boot_data,
            "ui_auto_refresh_sec": html_escape(str(UI_AUTO_REFRESH_SEC)),
            "poll_interval_ms": int(UI_AUTO_REFRESH_SEC) * 1000,
            "new_ui_css_link": "<link rel='stylesheet' href='/static-new/css/app.css' />" if is_new_variant else "",
            "ui_body_class": "ui-new" if is_new_variant else "ui-legacy",
            "ui_mode_link": ui_mode_link,
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
    enabled = _coerce_ui_bool(enabled)
    ok, message = await set_module_enabled(module_name, enabled, send_as_id=send_as_id)
    if not ok:
        return False, message or f"切换失败: {module_name}"
    action_text = "开启" if enabled else "关闭"
    return True, f"已{action_text}{module_name}[{get_identity_display_name(send_as_id)}]"


async def ui_set_duel_config(send_as_id, *, target=None, total_count=None, reset_progress=False):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    if target is not None and not normalize_duel_target(target):
        return False, "斗法目标不能为空"
    with use_identity(send_as_id):
        config = apply_duel_config(
            target=target,
            total_count=total_count,
            reset_progress=bool(reset_progress),
            now=time.time(),
            persist=True,
        )
    count_text = config["total_count"] if config["total_count"] > 0 else "未配置"
    return True, f"斗法配置已更新：{config['target'] or '未配置'}｜次数 {count_text}"


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
        "preach": ("small_world_preach_enabled", "神迹维护"),
        "manifest": ("small_world_manifest_enabled", "自动显灵"),
        "harvest": ("small_world_harvest_enabled", "收割香火"),
        "refine": ("small_world_refine_enabled", "神识淬炼"),
        "refresh": ("small_world_refresh_enabled", "祈愿刷新"),
        "barrier": ("small_world_barrier_enabled", "护界禁制"),
    }
    field_name, display_name = feature_map.get(feature_name, ("", ""))
    if not field_name:
        return False, f"未知小世界子功能: {feature_name}"
    enabled = _coerce_ui_bool(enabled)
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


def _coerce_int_range(value, default, min_value, max_value):
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(min_value), min(int(max_value), parsed))


def _coerce_float_range(value, default, min_value, max_value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(float(min_value), min(float(max_value), parsed))


async def ui_set_small_world_barrier_config(
    send_as_id,
    *,
    enabled=None,
    min_stock=None,
    guard_before_min=None,
    min_interval_hours=None,
):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    with use_identity(send_as_id):
        if enabled is not None:
            state["small_world_barrier_enabled"] = _coerce_ui_bool(enabled)
        if min_stock is not None:
            state["small_world_barrier_min_stock"] = _coerce_int_range(min_stock, 130000, 0, 1000000)
        if guard_before_min is not None:
            state["small_world_barrier_guard_before_min"] = _coerce_int_range(guard_before_min, 30, 5, 180)
        if min_interval_hours is not None:
            state["small_world_barrier_min_interval_hours"] = _coerce_float_range(min_interval_hours, 18, 0, 72)
        enabled_text = "开启" if state.get("small_world_barrier_enabled", True) else "关闭"
        min_stock_value = int(state.get("small_world_barrier_min_stock", 130000) or 130000)
        guard_value = int(state.get("small_world_barrier_guard_before_min", 30) or 30)
        interval_value = float(state.get("small_world_barrier_min_interval_hours", 18) or 18)
        save_state()
    await send_audit_log(
        f"🌍 已更新小世界护界禁制：{enabled_text}，阈值={min_stock_value}，提前={guard_value}分钟，间隔={interval_value:g}小时",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新小世界护界禁制[{get_identity_display_name(send_as_id)}]"


async def ui_set_divination_config(send_as_id, daily_limit=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    if daily_limit is not None:
        set_divination_daily_limit(send_as_id, daily_limit)
    save_state()
    limit = get_divination_daily_limit(send_as_id)
    await send_audit_log(
        f"🔮 已更新卜筮问天次数：{limit}/日",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新卜筮问天次数[{get_identity_display_name(send_as_id)}]：{limit}/日"


async def ui_set_fishing_config(send_as_id, payload=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    payload = payload if isinstance(payload, dict) else {}
    pond = payload.get("pond")
    bait = payload.get("bait")
    chum_name = payload.get("chum_name")
    chum_names = payload.get("chum_names")
    daily_limit = _coerce_fishing_daily_limit(payload.get("daily_limit"))
    auto_buy_bait_count = _coerce_fishing_buy_bait_count(payload.get("auto_buy_bait_count"))
    auto_chum_enabled = _coerce_ui_bool(payload.get("auto_chum_enabled"))
    auto_buy_bait_enabled = _coerce_ui_bool(payload.get("auto_buy_bait_enabled"))
    auto_probe_enabled = _coerce_ui_bool(payload.get("auto_probe_enabled"))
    try:
        config = normalize_fishing_config(
            pond or "青溪浅滩",
            bait or "凡饵",
            auto_chum_enabled=auto_chum_enabled,
            chum_name=chum_name or "",
            chum_names=chum_names,
            auto_buy_bait_enabled=auto_buy_bait_enabled,
            auto_buy_bait_count=auto_buy_bait_count,
            auto_probe_enabled=auto_probe_enabled,
        )
    except ValueError as exc:
        return False, f"无效的钓鱼配置：{exc}"
    with use_identity(send_as_id):
        auto_open_fish_enabled = _coerce_ui_bool(
            payload.get("auto_open_fish_enabled"),
            default=state.get("fishing_auto_open_fish_enabled", True),
        )
        state["fishing_pond"] = config.pond
        state["fishing_bait"] = config.bait
        state["fishing_daily_limit"] = daily_limit
        state["fishing_auto_chum_enabled"] = bool(config.auto_chum_enabled)
        state["fishing_chum_name"] = config.chum_name
        state["fishing_chum_names"] = format_fishing_chum_names(config.chum_names)
        state["fishing_auto_buy_bait_enabled"] = bool(config.auto_buy_bait_enabled)
        state["fishing_auto_buy_bait_count"] = int(config.auto_buy_bait_count or FISHING_DEFAULT_BUY_BAIT_COUNT)
        state["fishing_auto_probe_enabled"] = bool(config.auto_probe_enabled)
        state["fishing_auto_open_fish_enabled"] = bool(auto_open_fish_enabled)
        save_state()
        saved_identity_state = dict(state.items())
    plan = plan_fishing_commands(
        config,
        bait_inventory=_get_fishing_bait_inventory(send_as_id),
        chum_usage_counts=parse_chum_usage_counts(saved_identity_state.get("fishing_chum_counts")),
        active_chum_name=saved_identity_state.get("fishing_active_chum_name") or "",
        active_chum_rods_remaining=int(saved_identity_state.get("fishing_chum_rods_remaining", 0) or 0),
    )
    await send_audit_log(
        "🎣 已更新灵溪垂钓配置："
        f"{config.pond}/{config.bait}｜"
        f"次数={daily_limit}/日｜"
        f"打窝={','.join(config.chum_names or ()) or '无'}｜"
        f"买饵={'开' if config.auto_buy_bait_enabled else '关'}x{config.auto_buy_bait_count}｜"
        f"试饵={'开' if config.auto_probe_enabled else '关'}｜"
        f"开鱼={'开' if auto_open_fish_enabled else '关'}｜"
        f"计划={_format_fishing_command_plan(plan)}",
        scope="identity",
        send_as_id=send_as_id,
        limit=260,
    )
    return True, f"已更新灵溪垂钓[{get_identity_display_name(send_as_id)}]：{daily_limit}/日｜买饵{config.auto_buy_bait_count}｜{_format_fishing_command_plan(plan)}"


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
            enabled = _coerce_ui_bool(auto_choice_enabled)
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
        enabled = _coerce_ui_bool(enabled)
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
    enabled = _coerce_ui_bool(enabled)
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


async def ui_execute_yinluo_action(send_as_id, action, arg=""):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    if not get_identity_enabled(send_as_id):
        return False, "身份已停用。"
    if "阴罗宗" not in get_available_module_names(send_as_id):
        return False, "阴罗宗对该身份不可用。"
    action = str(action or "").strip()
    if action not in {"banner", "daily_sacrifice", "collect", "refine", "convert", "blood_forest", "demon_summon"}:
        return False, "未知阴罗宗按钮动作"
    ok, message, _plan = await execute_yinluo_manual_action(action, str(arg or "").strip(), send_as_id=send_as_id)
    if not ok:
        return False, message
    return True, f"{message}[{get_identity_display_name(send_as_id)}]"


async def ui_set_yinluo_auto_config(send_as_id, config):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    if not get_identity_enabled(send_as_id):
        return False, "身份已停用。"
    if "阴罗宗" not in get_available_module_names(send_as_id):
        return False, "阴罗宗对该身份不可用。"
    with use_identity(send_as_id):
        ok, message, _snapshot = set_yinluo_auto_config(config if isinstance(config, dict) else {})
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


async def ui_set_basic_config(game_group_id, game_bot_ids, game_topic_id, auto_delete_sent_messages, tiandao_judgement_enabled=False, guanxing_monitor_enabled=False, guanxing_shift_target=None, guanxing_shift_delay_sec=None, guanxing_monitor_targets=None, actor_id=None):
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

    auto_delete_enabled = _coerce_ui_bool(auto_delete_sent_messages)
    tiandao_judgement_switch_enabled = _coerce_ui_bool(tiandao_judgement_enabled)
    guanxing_monitor_switch_enabled = _coerce_ui_bool(guanxing_monitor_enabled)
    shift_target_value = get_guanxing_shift_target() if guanxing_shift_target is None else guanxing_shift_target
    raw_shift_target = str(shift_target_value or "").strip()
    if any(char.isspace() for char in raw_shift_target):
        return False, "观星改换目标不能包含空白字符"
    if raw_shift_target == "@":
        return False, "观星改换目标需填写用户名或 @用户名"
    raw_shift_delay = str(guanxing_shift_delay_sec if guanxing_shift_delay_sec is not None else get_guanxing_shift_delay_sec()).strip()
    try:
        shift_delay_sec = int(float(raw_shift_delay))
    except (TypeError, ValueError):
        return False, "观星首发偏移秒数必须是数字"
    if shift_delay_sec < -180:
        return False, "观星首发偏移秒数不能小于 -180 秒"
    if isinstance(guanxing_monitor_targets, str):
        raw_monitor_targets = [guanxing_monitor_targets]
    elif guanxing_monitor_targets is None:
        raw_monitor_targets = get_guanxing_monitor_targets()
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
    normalized_shift_target = set_guanxing_shift_target(shift_target_value)
    normalized_shift_delay_sec = set_guanxing_shift_delay_sec(shift_delay_sec)
    save_state()
    actor_suffix = f"｜操作者：{actor_id}" if actor_id is not None else ""
    display_topic = str(topic_id) if topic_id > 0 else "未启用"
    display_bots = ", ".join(str(bot_id) for bot_id in normalized_bot_ids)
    display_auto_delete = "开启" if auto_delete_enabled else "关闭"
    display_tiandao_judgement = "开启" if tiandao_judgement_switch_enabled else "关闭"
    display_guanxing_monitor = "开启" if guanxing_monitor_switch_enabled else "关闭"
    display_monitor_targets = ", ".join(normalized_monitor_targets) or "未选择"
    display_shift_delay = f"{normalized_shift_delay_sec:+d}秒"
    console_log(
        f"🧩 已更新基础配置：群={group_id}｜bot={display_bots}｜话题={display_topic}｜自动删消息={display_auto_delete}｜天道审判={display_tiandao_judgement}｜观星监控={display_guanxing_monitor}｜观星监控目标={display_monitor_targets}｜观星目标={normalized_shift_target or '未设置'}｜观星首发偏移={display_shift_delay}{actor_suffix}",
        scope="global",
        limit=340,
    )
    return True, f"已更新基础配置：群聊 {group_id} ｜ bot {display_bots} ｜ 话题 {display_topic} ｜ 自动删消息 {display_auto_delete} ｜ 天道审判 {display_tiandao_judgement} ｜ 观星监控 {display_guanxing_monitor} ｜ 观星监控目标 {display_monitor_targets} ｜ 观星目标 {normalized_shift_target or '未设置'} ｜ 观星首发偏移 {display_shift_delay}"


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
            elif path.startswith("/static-new/"):
                if method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    asset_body, asset_content_type = _load_new_static_asset(path[len("/static-new/"):])
                    if asset_body is None:
                        _write_response(writer, "HTTP/1.1 404 Not Found", "Not Found", content_type="text/plain; charset=utf-8")
                    else:
                        _write_response(writer, "HTTP/1.1 200 OK", asset_body, content_type=asset_content_type)
            elif path == "/" or path == "/new":
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
                    variant = "new"
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        render_ui_page(
                            selected_send_as_id=selected_send_as_id,
                            session_token=(session or {}).get("session_token"),
                            variant=variant,
                        ),
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
                            delete_official=_coerce_ui_bool(payload.get("delete_official")),
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
            elif path == "/api/quiz-ai-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = ui_set_quiz_ai_config(payload)
                    _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/quiz-ai-models":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, model_payload = await ui_fetch_quiz_ai_models(payload)
                    _write_json_result(
                        writer,
                        ok,
                        message,
                        session_token=(session or {}).get("session_token"),
                        extra_headers=auth_headers,
                        extra={"models": model_payload} if model_payload else None,
                        include_snapshot=False,
                    )
            elif path == "/api/storage-bag-api-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = ui_set_storage_bag_api_config(payload)
                    _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/storage-bag-api-verify":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, api_snapshot = await ui_verify_storage_bag_api(payload)
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(
                        ok,
                        message=message if ok else "",
                        error="" if ok else message,
                        snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                        extra={"storage_bag_api": api_snapshot},
                    )
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/storage-bag-api-refresh":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, api_snapshot = await ui_refresh_storage_bag_from_api(payload, notify_log_group=True)
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(
                        ok,
                        message=message if ok else "",
                        error="" if ok else message,
                        snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                        extra={"storage_bag_api": api_snapshot},
                    )
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/tianjige-dao-path-refresh":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, api_snapshot = await ui_refresh_tianjige_dao_path_from_api(payload)
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(
                        ok,
                        message=message if ok else "",
                        error="" if ok else message,
                        snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                        extra={"storage_bag_api": api_snapshot},
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
                    _write_json_result(
                        writer,
                        ok,
                        message,
                        session_token=(session or {}).get("session_token"),
                        extra_headers=auth_headers,
                        extra={"preview": preview} if preview else None,
                        include_snapshot=False,
                    )
            elif path == "/api/storage-bag-transfer-start":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, transfer = await ui_start_storage_bag_transfer(payload)
                    _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers, extra={"transfer": transfer} if transfer else None)
            elif path == "/api/storage-bag-gift-preview":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, preview = ui_preview_storage_bag_gift(payload)
                    _write_json_result(
                        writer,
                        ok,
                        message,
                        session_token=(session or {}).get("session_token"),
                        extra_headers=auth_headers,
                        extra={"preview": preview} if preview else None,
                        include_snapshot=False,
                    )
            elif path == "/api/storage-bag-gift-start":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, transfer = await ui_start_storage_bag_gift(payload)
                    _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers, extra={"transfer": transfer} if transfer else None)
            elif path == "/api/storage-bag-transfer-cancel":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, transfer = await ui_cancel_storage_bag_transfer()
                    _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers, extra={"transfer": transfer} if transfer else None)
            elif path == "/api/replica-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = ui_set_replica_config(payload)
                    _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/replica-gold-dps-toggle":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = ui_set_replica_gold_dps_enabled(send_as_id, _coerce_ui_bool(payload.get("enabled")))
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/basic-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = await ui_set_basic_config(payload.get("game_group_id"), payload.get("game_bot_ids"), payload.get("game_topic_id"), payload.get("auto_delete_sent_messages"), payload.get("tiandao_judgement_enabled"), payload.get("guanxing_monitor_enabled"), payload.get("guanxing_shift_target"), payload.get("guanxing_shift_delay_sec"), payload.get("guanxing_monitor_targets"), actor_id=(session or {}).get("sender_id"))
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
            elif path == "/api/identity-refresh-api":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    scope = str(payload.get("scope") or "").strip().lower()
                    refresh_all = bool(payload.get("refresh_all")) or scope in {"all", "api_all", "all_roles"}
                    send_as_id = payload.get("send_as_id")
                    if not refresh_all and send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message, api_snapshot = await ui_refresh_identity_from_api(send_as_id, payload, refresh_all=refresh_all)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(
                            ok,
                            message=message if ok else "",
                            error="" if ok else message,
                            snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                            extra={"storage_bag_api": api_snapshot},
                        )
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
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
                    enabled = _coerce_ui_bool(payload.get("enabled"))
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
                    enabled = _coerce_ui_bool(payload.get("enabled"))
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
                    enabled = _coerce_ui_bool(payload.get("enabled"))
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
            elif path == "/api/yinluo-action":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    action = payload.get("action")
                    arg = payload.get("arg") or ""
                    if send_as_id in {None, ""} or not action:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 action 参数", auth_headers)
                    else:
                        ok, message = await ui_execute_yinluo_action(send_as_id, action, arg)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/yinluo-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    config = payload.get("config") or {}
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_yinluo_auto_config(send_as_id, config)
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
                    enabled = _coerce_ui_bool(payload.get("enabled"))
                    if send_as_id in {None, ""} or not feature_name:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 feature 参数", auth_headers)
                    else:
                        ok, message = await ui_set_small_world_feature_enabled(send_as_id, feature_name, enabled)
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/small-world-barrier-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_small_world_barrier_config(
                            send_as_id,
                            enabled=payload.get("enabled"),
                            min_stock=payload.get("min_stock"),
                            guard_before_min=payload.get("guard_before_min"),
                            min_interval_hours=payload.get("min_interval_hours"),
                        )
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/divination-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_divination_config(send_as_id, daily_limit=payload.get("daily_limit"))
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/fishing-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_fishing_config(send_as_id, payload)
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
            elif path == "/api/duel-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_duel_config(
                            send_as_id,
                            target=payload.get("target") if "target" in payload else None,
                            total_count=payload.get("total_count") if "total_count" in payload else None,
                            reset_progress=_coerce_ui_bool(payload.get("reset_progress")),
                        )
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
                    enabled = _coerce_ui_bool(payload.get("enabled"))
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
                    enabled = _coerce_ui_bool(payload.get("enabled"))
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
            _write_response(writer, "HTTP/1.1 500 Internal Server Error", "Internal Server Error\n", content_type="text/plain; charset=utf-8")
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
    "get_storage_bag_api_snapshot",
    "get_storage_bag_snapshot",
    "get_storage_bag_sync_snapshot",
    "get_tianjige_dao_path_snapshot",
    "get_replica_config_snapshot",
    "get_ui_snapshot",
    "handle_ui_http",
    "html_escape",
    "render_ui_page",
    "start_ui_server",
    "stop_ui_server",
    "ui_add_identity",
    "ui_logout_account",
    "ui_cancel_storage_bag_transfer",
    "ui_preview_storage_bag_transfer",
    "ui_start_storage_bag_transfer",
    "ui_start_storage_bag_sync",
    "ui_refresh_identity_from_api",
    "ui_refresh_storage_bag_from_api",
    "ui_refresh_tianjige_dao_path_from_api",
    "ui_set_storage_bag_api_config",
    "ui_verify_storage_bag_api",
    "run_storage_bag_api_keepalive_scheduler",
    "ui_set_storage_bag_item_rule",
    "ui_set_replica_config",
    "ui_set_replica_gold_dps_enabled",
    "ui_refresh_forum_topics",
    "ui_refresh_identity_info",
    "ui_set_basic_config",
    "ui_set_identity_enabled",
    "ui_set_module_enabled",
    "ui_set_jiyin_choice",
    "ui_set_nanlong_choice",
    "ui_execute_yinluo_action",
    "ui_set_yinluo_auto_config",
    "ui_set_module_window",
    "ui_set_pet_name",
    "ui_set_duel_config",
    "ui_set_small_world_feature_enabled",
    "ui_set_small_world_barrier_config",
    "ui_set_divination_config",
    "ui_set_stargazer_star_choice",
    "ui_sync_stargazer_total_slots",
    "ui_sync_tianti_status",
    "ui_set_tianti_feature_enabled",
]
