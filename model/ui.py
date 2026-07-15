import asyncio
import glob
import html
import ipaddress
import importlib.util
import json
import os
import re
import sqlite3
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
    CMD_FISHING,
    CMD_DUNGEON_HUANGLONG_JOIN,
    CMD_DUNGEON_JOIN,
    CMD_DUNGEON_ZHUIMO_JOIN,
    CMD_REPLICA_CANGKUN_JOIN,
    CMD_REPLICA_KUNWU_JOIN,
    CMD_REPLICA_LUOYUN_JOIN,
    CMD_STARGAZER_PANEL,
    CMD_TIANJI_TRIAL,
    CMD_TIANTI_GANGFENG,
    MESSAGES_DIR,
    MODULE_KEY_MAP,
    STATE_DIR,
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
from .features.explore_rift import REBIRTH_CHOICE_MODES, REBIRTH_ROOT_TYPES, get_rebirth_choice_config, set_rebirth_choice_config
from .features.guanxing import get_guanxing_round_summary_text
from .features.guanxing_monitor import get_guanxing_monitor_summary_text
from .features.hehuan import HEHUAN_AUTO_RETRY_LIMIT, HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN, normalize_hehuan_observation, set_hehuan_retry_max_interval_min
from .features.join_dungeon import get_dungeon_join_inbox_snapshot
from .features.jiyin import apply_jiyin_choice, get_jiyin_choice_label, normalize_jiyin_choice, resolve_jiyin_choice
from .features.nanlong import apply_nanlong_choice, get_nanlong_choice_label, normalize_nanlong_choice, resolve_nanlong_choice
from .features import miniapp_registry
from .features import miniapp_command_catalog
from .inventory_delta import build_inventory_freshness_snapshot
from .miniapp_state import get_miniapp_state_snapshot, record_miniapp_state
from .miniapp_capture_summary import get_miniapp_capture_summary, normalize_miniapp_game_key
from .webapp_core import get_miniapp_global_rate_limit_snapshot
from .features.passive_inbox import get_passive_inbox_snapshot
from .features.quiz_ai import list_quiz_ai_models
from .features.cave_treasure_runtime import (
    _parse_public_cave_entry_url,
    authorize_cave_treasure_miniapp_manual_run,
    revoke_cave_treasure_miniapp_manual_run,
    run_cave_public_deep_retreat_action,
    run_cave_public_fishing,
    run_cave_public_small_world_sync,
    run_cave_public_stargazer,
    run_cave_public_tree,
    run_cave_public_treasure,
    run_cave_public_trial,
    run_cave_public_yuanying,
)
from .features.stargazer import authorize_stargazer_miniapp_manual_run, revoke_stargazer_miniapp_manual_run, sync_stargazer_total_slots
from .features.storage_bag import CMD_STORAGE_BAG, STORAGE_TRANSFER_DEFAULT_LISTING_SYNTAX, cancel_storage_bag_transfer_task, format_storage_bag_listing_command, get_storage_bag_transfer_snapshot, normalize_storage_bag_listing_count, normalize_storage_bag_listing_syntax, start_storage_bag_gift_batch, start_storage_bag_gift_task, start_storage_bag_transfer_batch, start_storage_bag_transfer_task
from .features.tree_runtime import (
    authorize_tree_miniapp_manual_run,
    cancel_tree_miniapp_daily_run,
    check_tree_miniapp_eligibility,
    finalize_tree_miniapp_daily_command,
    get_tree_miniapp_coordinator_snapshot,
    prepare_tree_miniapp_daily_run,
    revoke_tree_miniapp_manual_run,
)
from .tree_score_policy import TREE_MINIAPP_MAX_TARGET_SCORE, TREE_MINIAPP_MIN_TARGET_SCORE, normalize_tree_score_profile
from .features.trial_runtime import (
    authorize_trial_miniapp_manual_run,
    maybe_finalize_trial_batch_run,
    note_trial_batch_send_result,
    revoke_trial_miniapp_manual_run,
    start_trial_miniapp_batch_run,
)
from .features.tianxing import get_tianxing_automation_pause_state, get_tianxing_automation_pause_text, normalize_tianxing_auto_config, normalize_tianxing_observation, normalize_tianxing_timeline_state, set_tianxing_auto_config
from .features.tianti import sync_tianti_status
from .features.wild_training import apply_wild_training_strategy, normalize_wild_training_strategy
from .features.yinluo import execute_yinluo_manual_action, get_yinluo_ui_state, set_yinluo_auto_config
from .features.duel import apply_duel_config, normalize_duel_target, normalize_duel_targets
from .features.fishing import (
    FISHING_BAITS,
    FISHING_CHUMS,
    FISHING_DEFAULT_BUY_BAIT_COUNT,
    FISHING_DEFAULT_CANCEL_AFTER_SEC,
    FISHING_PONDS,
    clamp_fishing_buy_bait_count,
    clamp_fishing_cancel_after_sec,
    clamp_fishing_daily_limit,
    format_fishing_chum_names,
    normalize_fishing_config,
    plan_fishing_commands,
)
from .features.fishing_behavior import next_planned_command as next_fishing_planned_command, parse_chum_usage_counts, parse_pending_open_fish
from .features.yuanying import get_yuanying_phase_text
from .features.wanxin import get_wanxin_ui_state, set_wanxin_config
from .official_schedule import (
    build_preset_plan as build_official_schedule_preset_plan,
    create_official_messages_for_batch,
    delete_local_schedule_records as delete_official_schedule_records,
    list_local_schedules as list_local_official_schedules,
    replace_planned_batch as replace_official_schedule_planned_batch,
)
from .persistence import save_state
from .runtime import MAINTENANCE_PAUSE_SOURCE, PHASEFUL_PASSIVE_TRIGGER_SOURCE_MODULE, PHASEFUL_PASSIVE_TRIGGER_TEXT, _fire_and_forget, consume_unseen_startup_alerts, console_log, fetch_forum_topics, get_game_send_queue_snapshot, redeem_ui_login_token, send_audit_log, send_game_command, touch_ui_session
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
    get_global_pause_source,
    get_dungeon_join_run_state,
    get_replica_dispatch_group_ids,
    get_replica_dispatch_listener_account_map,
    get_replica_dispatch_participant_identity_ids,
    get_replica_gold_dps_enabled,
    get_replica_group_ids,
    get_replica_kind_configs,
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
    is_cave_public_identity_available,
    get_miniapp_auto_config,
    get_miniapp_state_records,
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
    get_tree_miniapp_score_configs,
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
    set_replica_kind_configs,
    set_replica_listener_account_map,
    set_replica_participant_identity_ids,
    set_replica_query_aggregator_config,
    set_replica_success_cooldown_hours,
    set_replica_virtual_hall_match_enabled_map,
    set_storage_bag_api_config,
    set_storage_bag_item_rules,
    set_storage_bag_records,
    set_tianjige_dao_path_records,
    set_miniapp_auto_config,
    set_tree_miniapp_score_configs,
    set_stargazer_star_choice,
    set_tianti_rank_choice,
    state,
    update_send_as_profile,
    use_identity,
)
from .timing import fmt_abs_ts, get_day_key

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
_cave_public_batch_state = {
    "running": False,
    "batch_id": "",
    "started_at": 0,
    "finished_at": 0,
    "total": 0,
    "completed": 0,
    "succeeded": 0,
    "failed": 0,
    "current": "",
    "last_result": "",
    "delay_sec": 20,
}
_cave_public_ui_run_lock = asyncio.Lock()
_cave_public_background_state = {
    "running": False,
    "next_run_at": 0,
    "cursor": 0,
    "last_action": "",
    "last_result": "",
}
_cave_public_background_retry_at = {}
# A successful MiniApp response is authoritative for the current process even
# if a concurrent state snapshot save temporarily races with it. Keep this
# short-lived marker as a second guard; the persisted miniapp state remains the
# restart-safe source of truth.
_cave_public_background_daily_done = set()
TREE_MINIAPP_ENTRY_PENDING_TIMEOUT_SEC = 10 * 60
MINIAPP_ENTRY_PROBE_COMMANDS = {
    "cave_treasure": ".洞府",
    "fishing": CMD_FISHING,
    "stargazer": CMD_STARGAZER_PANEL,
    "tree": ".灵树",
    "trial": CMD_TIANJI_TRIAL,
}
MINIAPP_MANUAL_RUN_COMMANDS = {
    "cave_treasure": ".洞府",
    "stargazer": CMD_STARGAZER_PANEL,
    "tree": ".灵树",
    "trial": CMD_TIANJI_TRIAL,
}
MINIAPP_UI_GROUPS = {
    "stargazer": {"key": "sect", "label": "宗门玩法"},
    "cave_treasure": {"key": "miniapp", "label": "MiniApp合集"},
    "fishing": {"key": "miniapp", "label": "MiniApp合集"},
    "trial": {"key": "miniapp", "label": "MiniApp合集"},
    "tree": {"key": "sect", "label": "宗门玩法"},
    "world_boss": {"key": "miniapp", "label": "MiniApp合集"},
}
MINIAPP_AUTO_CONFIG_DEFAULT = {
    "trial_daily_enabled": False,
    "trial_daily_scheduler_confirmed": False,
    "trial_daily_start_hour_local": 1,
    "trial_daily_start_minute_local": 0,
    "trial_daily_end_hour_local": 4,
    "trial_daily_last_run_day": "",
    "trial_daily_last_batch_id": "",
    "trial_daily_last_run_at": 0,
    "trial_daily_last_result": "",
    "trial_daily_wave1_last_run_day": "",
    "trial_daily_wave1_last_batch_id": "",
    "trial_daily_wave1_last_run_at": 0,
    "trial_daily_wave1_last_result": "",
    "trial_daily_wave2_last_run_day": "",
    "trial_daily_wave2_last_batch_id": "",
    "trial_daily_wave2_last_run_at": 0,
    "trial_daily_wave2_last_result": "",
    "cave_public_small_world_enabled": True,
    "cave_public_deep_status_enabled": True,
    "cave_public_treasure_enabled": True,
    "cave_public_trial_enabled": True,
    "cave_public_fishing_enabled": False,
    "cave_public_fishing_identity_ids": [],
    "cave_public_stargazer_enabled": False,
    "cave_public_yuanying_enabled": False,
    "cave_public_entry_url": "",
    "cave_public_entry_urls": [],
    "cave_public_delay_sec": 20,
    "world_boss_auto_enabled": False,
    "world_boss_auto_account_limit": 1,
    "world_boss_auto_account_gap_sec": 3,
    "world_boss_auto_excluded_identity_ids": [],
    "tree_daily_enabled_identity_ids": [],
}
TRIAL_DAILY_BATCH_WAVES = (
    {"key": "wave1", "label": "第一批", "start_hour": 1, "start_minute": 0, "end_hour": 4, "end_minute": 0},
    {"key": "wave2", "label": "第二批", "start_hour": 5, "start_minute": 0, "end_hour": 8, "end_minute": 0},
)


def _miniapp_ui_group(game_key):
    return dict(MINIAPP_UI_GROUPS.get(str(game_key or "").strip().lower()) or {"key": "miniapp", "label": "MiniApp合集"})


def _normalize_cave_public_entry_urls_value(value):
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        raw_items = [item.strip() for item in re.split(r"[\r\n,，\s]+", value) if item.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        raw_items = []
    urls = []
    seen = set()
    for item in raw_items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        urls.append(item)
    return urls


def _cave_public_entry_urls_from_config(config=None):
    raw = dict(config or get_miniapp_auto_config() or {})
    urls = []
    urls.extend(_normalize_cave_public_entry_urls_value(raw.get("cave_public_entry_url")))
    urls.extend(_normalize_cave_public_entry_urls_value(raw.get("cave_public_entry_urls")))
    result = []
    seen = set()
    for url in urls:
        key = str(url or "").strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(str(url or "").strip())
    return result


def _with_miniapp_ui_group(item):
    result = dict(item or {})
    group = _miniapp_ui_group(result.get("game_key"))
    result["ui_group"] = group["key"]
    result["ui_group_label"] = group["label"]
    return result


def normalize_miniapp_auto_config(config=None):
    raw = dict(config or get_miniapp_auto_config() or {})
    result = dict(MINIAPP_AUTO_CONFIG_DEFAULT)
    result.update({key: raw.get(key, default) for key, default in MINIAPP_AUTO_CONFIG_DEFAULT.items()})
    result["trial_daily_enabled"] = bool(result.get("trial_daily_enabled"))
    if "trial_daily_scheduler_confirmed" in raw:
        result["trial_daily_scheduler_confirmed"] = bool(result.get("trial_daily_scheduler_confirmed"))
    else:
        result["trial_daily_scheduler_confirmed"] = bool(raw.get("trial_daily_enabled"))
    result["trial_daily_effective_enabled"] = bool(
        result["trial_daily_enabled"] and result["trial_daily_scheduler_confirmed"]
    )
    for key in (
        "cave_public_small_world_enabled",
        "cave_public_deep_status_enabled",
        "cave_public_treasure_enabled",
        "cave_public_trial_enabled",
        "cave_public_fishing_enabled",
        "cave_public_stargazer_enabled",
        "cave_public_yuanying_enabled",
        "world_boss_auto_enabled",
    ):
        result[key] = bool(result.get(key))
    try:
        result["cave_public_delay_sec"] = int(float(result.get("cave_public_delay_sec", 20) or 20))
    except (TypeError, ValueError, OverflowError):
        result["cave_public_delay_sec"] = 20
    result["cave_public_delay_sec"] = max(10, min(120, result["cave_public_delay_sec"]))
    urls = _cave_public_entry_urls_from_config(result)
    result["cave_public_entry_url"] = urls[0] if urls else ""
    result["cave_public_entry_urls"] = urls
    try:
        result["world_boss_auto_account_limit"] = int(result.get("world_boss_auto_account_limit", 1) or 1)
    except (TypeError, ValueError, OverflowError):
        result["world_boss_auto_account_limit"] = 1
    result["world_boss_auto_account_limit"] = max(1, min(4, result["world_boss_auto_account_limit"]))
    try:
        result["world_boss_auto_account_gap_sec"] = float(result.get("world_boss_auto_account_gap_sec", 3))
    except (TypeError, ValueError, OverflowError):
        result["world_boss_auto_account_gap_sec"] = 3
    result["world_boss_auto_account_gap_sec"] = max(1, min(15, result["world_boss_auto_account_gap_sec"]))
    excluded_ids = result.get("world_boss_auto_excluded_identity_ids") or []
    if not isinstance(excluded_ids, (list, tuple, set)):
        excluded_ids = []
    result["world_boss_auto_excluded_identity_ids"] = sorted({
        int(identity_id)
        for identity_id in excluded_ids
        if str(identity_id or "").strip().lstrip("-").isdigit() and int(identity_id) > 0
    })
    tree_identity_ids = result.get("tree_daily_enabled_identity_ids") or []
    if not isinstance(tree_identity_ids, (list, tuple, set)):
        tree_identity_ids = []
    result["tree_daily_enabled_identity_ids"] = sorted({
        int(identity_id)
        for identity_id in tree_identity_ids
        if str(identity_id or "").strip().lstrip("-").isdigit() and int(identity_id) > 0
    })
    fishing_identity_ids = result.get("cave_public_fishing_identity_ids") or []
    if not isinstance(fishing_identity_ids, (list, tuple, set)):
        fishing_identity_ids = []
    result["cave_public_fishing_identity_ids"] = sorted({
        int(identity_id)
        for identity_id in fishing_identity_ids
        if str(identity_id or "").strip().lstrip("-").isdigit() and int(identity_id) > 0
    })
    for key, default in (
        ("trial_daily_start_hour_local", 0),
        ("trial_daily_start_minute_local", 20),
        ("trial_daily_end_hour_local", 3),
    ):
        try:
            value = int(result.get(key, default) or 0)
        except (TypeError, ValueError, OverflowError):
            value = default
        if "hour" in key:
            value = max(0, min(23, value))
        else:
            value = max(0, min(59, value))
        result[key] = value
    for key in (
        "trial_daily_last_run_day",
        "trial_daily_last_batch_id",
        "trial_daily_last_result",
        "trial_daily_wave1_last_run_day",
        "trial_daily_wave1_last_batch_id",
        "trial_daily_wave1_last_result",
        "trial_daily_wave2_last_run_day",
        "trial_daily_wave2_last_batch_id",
        "trial_daily_wave2_last_result",
    ):
        result[key] = str(result.get(key) or "")
    for key in ("trial_daily_last_run_at", "trial_daily_wave1_last_run_at", "trial_daily_wave2_last_run_at"):
        try:
            result[key] = float(result.get(key, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            result[key] = 0
    legacy_run_day = result["trial_daily_last_run_day"]
    had_wave1_day = bool(result["trial_daily_wave1_last_run_day"])
    had_wave2_day = bool(result["trial_daily_wave2_last_run_day"])
    if legacy_run_day and not had_wave1_day and not had_wave2_day:
        result["trial_daily_wave1_last_run_day"] = result["trial_daily_last_run_day"]
        result["trial_daily_wave1_last_batch_id"] = result["trial_daily_last_batch_id"]
        result["trial_daily_wave1_last_run_at"] = result["trial_daily_last_run_at"]
        result["trial_daily_wave1_last_result"] = result["trial_daily_last_result"]
        result["trial_daily_wave2_last_run_day"] = result["trial_daily_last_run_day"]
        result["trial_daily_wave2_last_batch_id"] = result["trial_daily_last_batch_id"]
        result["trial_daily_wave2_last_run_at"] = result["trial_daily_last_run_at"]
        result["trial_daily_wave2_last_result"] = result["trial_daily_last_result"]
    return result


def _cave_public_actions_from_config(config=None):
    config = normalize_miniapp_auto_config(config)
    actions = []
    if config.get("cave_public_small_world_enabled"):
        actions.append("small_world")
    if config.get("cave_public_deep_status_enabled"):
        actions.append("deep_status")
    if config.get("cave_public_treasure_enabled"):
        actions.append("treasure")
    if config.get("cave_public_trial_enabled"):
        actions.append("trial")
    if config.get("cave_public_fishing_enabled"):
        actions.append("fishing")
    if config.get("cave_public_stargazer_enabled"):
        actions.append("stargazer")
    if config.get("cave_public_yuanying_enabled"):
        actions.append("yuanying")
    return actions


def _trial_daily_wave_window_text(wave):
    return (
        f"{int(wave['start_hour']):02d}:{int(wave['start_minute']):02d}-"
        f"{int(wave['end_hour']):02d}:{int(wave['end_minute']):02d}"
    )


def _minute_in_local_window(current_minute, start_minute, end_minute):
    if start_minute < end_minute:
        return start_minute <= current_minute < end_minute
    return current_minute >= start_minute or current_minute < end_minute


def _trial_daily_wave_for_now(config, local_now):
    current_minute = int(local_now.hour) * 60 + int(local_now.minute)
    today = local_now.strftime("%Y-%m-%d")
    for wave in TRIAL_DAILY_BATCH_WAVES:
        start_minute = int(wave["start_hour"]) * 60 + int(wave["start_minute"])
        end_minute = int(wave["end_hour"]) * 60 + int(wave["end_minute"])
        if not _minute_in_local_window(current_minute, start_minute, end_minute):
            continue
        wave_key = str(wave["key"])
        return {
            **wave,
            "done_today": config.get(f"trial_daily_{wave_key}_last_run_day") == today,
        }
    return None


def _split_trial_daily_identity_ids(identity_ids, wave_key):
    ids = list(identity_ids or [])
    if not ids:
        return []
    split_at = (len(ids) + 1) // 2
    if str(wave_key or "") == "wave2":
        return ids[split_at:]
    return ids[:split_at]


def get_miniapp_auto_config_snapshot(now=None):
    config = normalize_miniapp_auto_config()
    now = float(now or time.time())
    local_now = datetime.fromtimestamp(now, TZ_LOCAL)
    today = local_now.strftime("%Y-%m-%d")
    active_wave = _trial_daily_wave_for_now(config, local_now)
    wave_states = []
    for wave in TRIAL_DAILY_BATCH_WAVES:
        wave_key = str(wave["key"])
        wave_states.append({
            **wave,
            "window_text": _trial_daily_wave_window_text(wave),
            "done_today": config.get(f"trial_daily_{wave_key}_last_run_day") == today,
            "last_batch_id": config.get(f"trial_daily_{wave_key}_last_batch_id") or "",
            "last_result": config.get(f"trial_daily_{wave_key}_last_result") or "",
        })
    all_done = all(item["done_today"] for item in wave_states)
    safe_config = dict(config)
    safe_config.pop("cave_public_entry_url", None)
    safe_config.pop("cave_public_entry_urls", None)
    try:
        from .features.world_boss import select_world_boss_miniapp_entry_identities
        world_boss_candidate_ids = select_world_boss_miniapp_entry_identities()
    except Exception:
        world_boss_candidate_ids = []
    excluded_world_boss_ids = set(config.get("world_boss_auto_excluded_identity_ids") or [])
    world_boss_candidates = [
        {
            "identity_id": int(identity_id),
            "label": get_identity_ui_display_name(identity_id),
            "account_id": int(get_identity_account(identity_id) or 0),
            "auto_enabled": int(identity_id) not in excluded_world_boss_ids,
        }
        for identity_id in world_boss_candidate_ids
    ]
    fishing_public_ids = set(config.get("cave_public_fishing_identity_ids") or [])
    cave_public_fishing_candidates = []
    for identity_id in get_identity_ids():
        account_id = int(get_identity_account(identity_id) or 0)
        if account_id <= 0 or account_id == int(identity_id) or not is_cave_public_identity_available(identity_id):
            continue
        cave_public_fishing_candidates.append({
            "identity_id": int(identity_id),
            "label": get_identity_ui_display_name(identity_id),
            "account_id": account_id,
            "auto_enabled": int(identity_id) in fishing_public_ids,
        })
    return {
        **safe_config,
        "world_boss_candidates": world_boss_candidates,
        "cave_public_fishing_candidates": cave_public_fishing_candidates,
        "cave_public_entry_url_configured": bool(config.get("cave_public_entry_urls")),
        "cave_public_entry_url_count": len(config.get("cave_public_entry_urls") or []),
        "today": today,
        "trial_daily_done_today": all_done,
        "trial_daily_in_window": bool(active_wave and not active_wave.get("done_today")),
        "trial_daily_active_wave": active_wave or {},
        "trial_daily_waves": wave_states,
        "trial_daily_window_text": " / ".join(_trial_daily_wave_window_text(wave) for wave in TRIAL_DAILY_BATCH_WAVES),
    }


def _miniapp_tree_score_config_key(send_as_id=None):
    try:
        return int(send_as_id or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _tree_miniapp_score_config_for_key(key):
    records = get_tree_miniapp_score_configs()
    if not isinstance(records, dict):
        return {}
    return dict(records.get(str(key)) or records.get(int(key)) or {})


def get_tree_miniapp_score_config(send_as_id=None):
    key = _miniapp_tree_score_config_key(send_as_id)
    saved = _tree_miniapp_score_config_for_key(key)
    jump = normalize_tree_score_profile("jump", saved.get("jump"))
    fly = normalize_tree_score_profile("fly", saved.get("fly"))
    auto_config = normalize_miniapp_auto_config()
    auto_enabled = key in set(auto_config.get("tree_daily_enabled_identity_ids") or ())
    eligible, eligibility_reason = check_tree_miniapp_eligibility(key, enabled=True)
    tree_state = get_miniapp_state_snapshot(send_as_id=key, game_key="tree")
    rows = list(tree_state.get("rows") or ())
    latest_state = dict((rows[0].get("state") if rows else {}) or {})
    return {
        "identity_id": key,
        "jump": {
            "target_score_range": list(jump.get("target_score_range") or ()),
            "min_target_score": int(TREE_MINIAPP_MIN_TARGET_SCORE["jump"]),
            "max_target_score": int(TREE_MINIAPP_MAX_TARGET_SCORE["jump"]),
        },
        "fly": {
            "target_score_range": list(fly.get("target_score_range") or ()),
            "min_target_score": int(TREE_MINIAPP_MIN_TARGET_SCORE["fly"]),
            "max_target_score": int(TREE_MINIAPP_MAX_TARGET_SCORE["fly"]),
        },
        "submit_default": False,
        "auto_enabled": auto_enabled,
        "eligible": eligible,
        "eligibility_reason": eligibility_reason,
        "daily_state": latest_state,
        "coordinator": get_tree_miniapp_coordinator_snapshot(),
        "note": "灵树跳一跳/飞一飞使用低分随机区间；自动化仅对显式开启的落云宗身份生效。",
    }


async def ui_set_tree_miniapp_score_config(send_as_id, payload=None):
    payload = dict(payload or {})
    key = _miniapp_tree_score_config_key(send_as_id)
    if key not in get_identity_ids():
        return False, "身份不存在"
    current = get_tree_miniapp_score_config(key)
    updates = {}
    for mode in ("jump", "fly"):
        raw_value = payload.get(f"{mode}_target_score")
        nested = payload.get(mode) if isinstance(payload.get(mode), dict) else {}
        if raw_value in {None, ""}:
            raw_value = nested.get("target_score")
        if raw_value in {None, ""}:
            raw_range = (current.get(mode) or {}).get("target_score_range") or []
            raw_value = raw_range[0] if raw_range else 1
        try:
            target_score = int(str(raw_value).strip())
        except (TypeError, ValueError, OverflowError):
            return False, f"{mode} 目标分必须是数字"
        updates[mode] = normalize_tree_score_profile(mode, {"target_score": target_score})
    records = dict(get_tree_miniapp_score_configs())
    records[str(key)] = updates
    set_tree_miniapp_score_configs(records)
    save_state()
    refreshed = get_tree_miniapp_score_config(key)
    jump_range = refreshed["jump"]["target_score_range"] or [0, 0]
    fly_range = refreshed["fly"]["target_score_range"] or [0, 0]
    return True, f"灵树目标区间已更新：跳一跳 {jump_range[0]}-{jump_range[-1]}｜飞一飞 {fly_range[0]}-{fly_range[-1]}"


async def ui_set_tree_miniapp_auto_config(send_as_id, payload=None):
    payload = dict(payload or {})
    key = _miniapp_tree_score_config_key(send_as_id)
    if key not in get_identity_ids():
        return False, "身份不存在"
    enabled = _coerce_ui_bool(payload.get("enabled"))
    if enabled:
        eligible, reason = check_tree_miniapp_eligibility(key, enabled=True)
        if not eligible:
            return False, f"灵树自动化不可开启：{reason}"
    config = normalize_miniapp_auto_config()
    enabled_ids = set(config.get("tree_daily_enabled_identity_ids") or ())
    if enabled:
        enabled_ids.add(key)
    else:
        enabled_ids.discard(key)
    config["tree_daily_enabled_identity_ids"] = sorted(enabled_ids)
    set_miniapp_auto_config(config)
    save_state()
    return True, f"灵树 MiniApp 每日自动化已{'开启' if enabled else '关闭'}"


def get_miniapp_status_snapshot(send_as_id=None):
    registry = miniapp_registry.build_known_miniapp_registry()
    plans = miniapp_registry.build_known_miniapp_flow_plans()
    command_catalog = miniapp_command_catalog.build_command_catalog_snapshot()
    command_catalog_validation = miniapp_command_catalog.validate_command_catalog(
        flow_plans=plans,
        entry_probe_commands=MINIAPP_ENTRY_PROBE_COMMANDS,
    )
    return {
        "adapters": [
            _with_miniapp_ui_group(item)
            for item in registry.safe_snapshot()
        ],
        "flow_plans": {
            str(key): plan.safe_summary()
            for key, plan in sorted(plans.items())
        },
        "entry_probe_commands": [
            _with_miniapp_ui_group({
                "game_key": str(key),
                "command": str(command),
                "registered": key in registry.keys(),
                "has_flow_plan": key in plans,
            })
            for key, command in sorted(MINIAPP_ENTRY_PROBE_COMMANDS.items())
        ],
        "manual_run_commands": [
            _with_miniapp_ui_group({
                "game_key": str(key),
                "command": str(command),
                "registered": key in registry.keys(),
                "has_flow_plan": key in plans,
            })
            for key, command in sorted(MINIAPP_MANUAL_RUN_COMMANDS.items())
        ],
        "batch_run_commands": [
            _with_miniapp_ui_group({
                "game_key": "trial",
                "label": "天机试炼全号批量",
                "endpoint": "/api/miniapp-trial-batch-run",
                "command": MINIAPP_MANUAL_RUN_COMMANDS["trial"],
                "registered": "trial" in registry.keys(),
                "has_flow_plan": "trial" in plans,
            })
        ],
        "ui_groups": [
            {"key": "miniapp", "label": "MiniApp合集"},
            {"key": "sect", "label": "宗门玩法"},
        ],
        "policy": {
            "default_enabled": False,
            "manual_only": True,
            "raw_init_data_persisted": False,
            "raw_start_token_persisted": False,
            "global_rate_limit": get_miniapp_global_rate_limit_snapshot(),
        },
        "command_catalog": command_catalog,
        "command_catalog_validation": command_catalog_validation,
        "automation": get_miniapp_auto_config_snapshot(),
        "cave_public_batch": dict(_cave_public_batch_state),
        "cave_public_background": dict(_cave_public_background_state),
        "state_records": get_miniapp_state_snapshot(send_as_id=send_as_id),
        "score_controls": {
            "tree": get_tree_miniapp_score_config(send_as_id),
        },
    }
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
_STORAGE_BAG_PINNED_ITEM_ORDER = ("天雷竹", "二级妖丹", "金精矿")
_LOG_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")
_REPLICA_UI_KIND_VIRTUAL_HALL = "virtual_hall"
_REPLICA_UI_KIND_ZHUIMO = "zhuimo"
_REPLICA_UI_KIND_HUANGLONG = "huanglong"
_REPLICA_UI_KIND_CANGKUN = "cangkun"
_REPLICA_UI_KIND_KUNWU = "kunwu"
_REPLICA_UI_KIND_LUOYUN = "luoyun"
_REPLICA_UI_KINDS = (
    _REPLICA_UI_KIND_VIRTUAL_HALL,
    _REPLICA_UI_KIND_ZHUIMO,
    _REPLICA_UI_KIND_HUANGLONG,
    _REPLICA_UI_KIND_CANGKUN,
    _REPLICA_UI_KIND_KUNWU,
    _REPLICA_UI_KIND_LUOYUN,
)
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
    _REPLICA_UI_KIND_KUNWU: {"name": "昆吾山", "short": "昆", "items": ("昆吾通行令",)},
    _REPLICA_UI_KIND_LUOYUN: {"name": "落云秘圃", "short": "落", "items": ()},
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
        if isinstance(raw_rule, dict) and str(raw_rule.get("method") or "").strip().lower() == "blocked":
            raw_rule = {
                **raw_rule,
                "method": "gift",
                "reason": raw_rule.get("reason") or "基础规则不可上架，默认改走赠送。",
            }
    return _normalize_storage_bag_item_rule(item_name, raw_rule)


def _storage_bag_transfer_method_label(method):
    return {
        "basic": "买卖",
        "gift": "赠送",
        "blocked": "不可转移",
        "unknown": "未知",
    }.get(str(method or "unknown"), "未知")


def _coerce_non_negative_int(value, default=0):
    try:
        parsed = int(value if value not in {None, ""} else default)
    except (TypeError, ValueError):
        parsed = int(default or 0)
    return max(0, parsed)


def _storage_bag_item_sort_key(item_name):
    name = str(item_name or "")
    pinned = {item: index for index, item in enumerate(_STORAGE_BAG_PINNED_ITEM_ORDER)}
    if name in pinned:
        return pinned[name], name
    if name == "灵石":
        return len(_STORAGE_BAG_PINNED_ITEM_ORDER), name
    return len(_STORAGE_BAG_PINNED_ITEM_ORDER) + 1, name


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


def get_runtime_health_snapshot():
    path = os.path.join(STATE_DIR, "health_observer", "latest.json")
    fallback = {
        "available": False,
        "status": "unknown",
        "health": {"score": None, "level": "unknown", "risk_reasons": []},
        "module_summary": [],
        "evidence_refs": [],
        "path": path,
    }
    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except Exception:
        return fallback
    if not isinstance(payload, dict):
        return fallback
    business = payload.get("business") if isinstance(payload.get("business"), dict) else {}
    db_state = business.get("db_state") if isinstance(business.get("db_state"), dict) else {}
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    message_state = business.get("message_state") if isinstance(business.get("message_state"), dict) else {}
    module_summary = db_state.get("module_summary") if isinstance(db_state.get("module_summary"), list) else []
    evidence_refs = payload.get("evidence_refs") if isinstance(payload.get("evidence_refs"), list) else []
    return {
        "available": True,
        "status": payload.get("status") or "unknown",
        "ts": payload.get("ts") or "",
        "health": {
            "score": health.get("score"),
            "level": health.get("level") or payload.get("status") or "unknown",
            "risk_reasons": (health.get("risk_reasons") if isinstance(health.get("risk_reasons"), list) else [])[:8],
            "last_ok_at": health.get("last_ok_at") or "",
            "last_bad_at": health.get("last_bad_at") or "",
        },
        "module_summary": module_summary[:24],
        "evidence_refs": evidence_refs[:12],
        "sent_count": int(message_state.get("sent_count") or 0),
        "pending_total": int(db_state.get("pending_total") or 0),
        "path": path,
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
        # A successful authenticated /api/me response proves the same session
        # validity as the explicit verify action. Keep the UI/auth state aligned
        # with the data path instead of showing "未验证" after a real refresh.
        _storage_bag_api_store_verified_result({
            "cookie": me_result.cookie,
            "api_token": me_result.api_token,
            "item_name_map": {},
        }, time.time())
        active_config = get_storage_bag_api_config()
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


def _build_storage_bag_aggregate_money_task(tasks, item_plan, *, listing_item, listing_count, listing_syntax, target_identity_id, target_label, rows, min_transfer_count, listing_unit_price=0):
    if not tasks or not isinstance(item_plan, dict):
        return None
    if str(item_plan.get("item_name") or "").strip() != "灵石":
        return None
    if not str(listing_item or "").strip():
        return None
    source_quantities = []
    for task in tasks:
        items = task.get("items") if isinstance(task, dict) else []
        if len(items or []) != 1 or str((items[0] or {}).get("item_name") or "").strip() != "灵石":
            return None
        if str((items[0] or {}).get("method") or "unknown") == "gift":
            return None
        source_quantities.append(int((items[0] or {}).get("quantity") or 0))
    planned_quantity = int(item_plan.get("planned_quantity") or sum(source_quantities))
    if planned_quantity <= 0:
        return None
    target_listing_stock = _get_storage_bag_item_count(rows, target_identity_id, listing_item)
    unit_price = int(listing_unit_price or 0)
    if unit_price > 0:
        aggregate_listing_count = normalize_storage_bag_listing_count(listing_count)
    else:
        aggregate_listing_count = len(tasks)
    if target_listing_stock > 0:
        aggregate_listing_count = min(aggregate_listing_count, target_listing_stock)
    aggregate_listing_count = max(1, aggregate_listing_count)
    if aggregate_listing_count <= 0:
        return None
    ranked_tasks = sorted(
        tasks,
        key=lambda task: (-int(((task.get("items") or [{}])[0] or {}).get("quantity") or 0), int(task.get("source_identity_id") or 0)),
    )
    if unit_price <= 0:
        ranked_tasks = ranked_tasks[:aggregate_listing_count]
        unit_price = min(
            int(((task.get("items") or [{}])[0] or {}).get("quantity") or 0)
            for task in ranked_tasks
        )
    unit_price = max(1, unit_price)
    buyers = []
    remaining_units = aggregate_listing_count
    for task in ranked_tasks:
        if remaining_units <= 0:
            break
        item = (task.get("items") or [{}])[0]
        source_quantity = int(item.get("quantity") or 0)
        buyer_units = min(remaining_units, source_quantity // unit_price)
        if buyer_units <= 0:
            continue
        buyer_quantity = buyer_units * unit_price
        buyer_item = {**dict(item), "quantity": buyer_quantity}
        buyers.append({
            "source_identity_id": int(task.get("source_identity_id") or 0),
            "source_label": task.get("source_label") or task.get("source_identity_id") or "",
            "items": [buyer_item],
            "listing_count": buyer_units,
            "unit_price": unit_price,
        })
        remaining_units -= buyer_units
    if not buyers:
        return None
    aggregate_quantity = sum(
        int((buyer.get("items") or [{}])[0].get("quantity") or 0)
        for buyer in buyers
    )
    aggregate_items = [{
        **dict((tasks[0].get("items") or [{}])[0]),
        "quantity": aggregate_quantity,
        "source_count": aggregate_quantity,
        "source_left_count": 0,
    }]
    aggregate_task = {
        "source_identity_id": int(buyers[0].get("source_identity_id") or 0),
        "source_label": f"聚合购买 {len(buyers)} 个来源",
        "target_identity_id": int(target_identity_id or 0),
        "target_label": target_label,
        "listing_item": listing_item,
        "listing_count": aggregate_listing_count,
        "listing_syntax": listing_syntax,
        "listing_command": format_storage_bag_listing_command(
            listing_item,
            aggregate_listing_count,
            [f"灵石*{aggregate_listing_count * unit_price}"],
            listing_syntax=listing_syntax,
        ),
        "operation": "transfer",
        "items": aggregate_items,
        "aggregate_buyers": buyers,
        "aggregate_unit_price": unit_price,
        "aggregate_planned_quantity": aggregate_quantity,
        "aggregate_total_price": aggregate_listing_count * unit_price,
    }
    return aggregate_task


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
            "command": "复用5分钟内发言；无锚点则发赠送标记" if is_gift_operation else "复用5分钟内发言；无锚点则发转移标记",
            "note": "优先回复目标身份近期发言，找不到再由目标身份发送可回复定位消息",
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
    listing_unit_price = _coerce_non_negative_int(payload.get("listing_unit_price"), 0)
    reserve_count = _coerce_non_negative_int(payload.get("reserve_count"), 0)
    min_transfer_count = max(1, _coerce_non_negative_int(payload.get("min_transfer_count"), 1))
    mode = str(payload.get("mode") or "all").strip().lower()
    if mode not in {"all", "fixed"}:
        mode = "all"

    tasks = []
    skipped = []
    warnings = []
    item_plans = []
    source_row_map = {int(row.get("identity_id") or 0): row for row in rows}
    target_row = source_row_map.get(int(target_identity_id), {})
    requested_items = sorted(requested_items, key=lambda item: _storage_bag_item_sort_key(item.get("item_name")))
    for request_item in requested_items:
        item_name = str(request_item.get("item_name") or "").strip()
        if not item_name:
            continue
        rule = _get_storage_bag_item_rule(item_name)
        method = rule.get("method") or "unknown"
        if method == "blocked":
            warnings.append(f"{item_name} 不可赠送，已跳过" if is_gift_operation else f"{item_name} 不可转移，已跳过")
            continue
        if is_gift_operation:
            method = "gift"
        requested_quantity = int(request_item.get("quantity") or 0)
        demand_remaining = requested_quantity if requested_quantity > 0 else None
        candidates = []
        for source_id in sources:
            source_count = _get_storage_bag_item_count(rows, source_id, item_name)
            transferable_count = max(0, source_count - reserve_count)
            if transferable_count < min_transfer_count:
                continue
            candidates.append({
                "source_identity_id": int(source_id),
                "source_count": source_count,
                "transferable_count": transferable_count,
            })
        candidates.sort(key=lambda item: (-int(item.get("transferable_count") or 0), int(item.get("source_identity_id") or 0)))
        planned_quantity = 0
        used_sources = []
        for candidate in candidates:
            if demand_remaining is not None and demand_remaining <= 0:
                break
            quantity = int(candidate["transferable_count"])
            if demand_remaining is not None:
                quantity = min(quantity, demand_remaining)
            if quantity < min_transfer_count:
                continue
            source_id = int(candidate["source_identity_id"])
            source_row = source_row_map.get(source_id, {})
            task_item = {
                "item_name": item_name,
                "quantity": quantity,
                "source_count": int(candidate["source_count"]),
                "source_left_count": max(0, int(candidate["source_count"]) - quantity),
                "reserve_count": reserve_count,
                "min_transfer_count": min_transfer_count,
                "target_count": _get_storage_bag_item_count(rows, target_identity_id, item_name),
                "method": method,
                "method_label": _storage_bag_transfer_method_label(method),
                "tags": rule.get("tags") or [_STORAGE_BAG_DEFAULT_TAG],
            }
            task_exchange_parts = [] if method == "gift" else [f"{item_name}*{quantity}"]
            if task_exchange_parts and not listing_item:
                return False, "请选择集中号用于上架的物品", None
            tasks.append({
                "source_identity_id": source_id,
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
                "items": [task_item],
            })
            planned_quantity += quantity
            used_sources.append({
                "source_identity_id": source_id,
                "source_label": source_row.get("label") or source_row.get("display_name") or str(source_id),
                "quantity": quantity,
                "source_count": int(candidate["source_count"]),
                "source_left_count": max(0, int(candidate["source_count"]) - quantity),
            })
            if demand_remaining is not None:
                demand_remaining -= quantity
        if not candidates:
            skipped.extend(source_id for source_id in sources if source_id not in skipped)
            warnings.append(f"{item_name} 无来源满足保留/起送条件")
        elif requested_quantity > 0 and planned_quantity < requested_quantity:
            warnings.append(f"{item_name} 需求 {requested_quantity}，按保留/起送后仅规划 {planned_quantity}")
        item_plans.append({
            "item_name": item_name,
            "requested_quantity": requested_quantity,
            "planned_quantity": planned_quantity,
            "reserve_count": reserve_count,
            "min_transfer_count": min_transfer_count,
            "candidate_count": len(candidates),
            "used_source_count": len(used_sources),
            "sources": used_sources,
        })
    if not tasks:
        return False, "没有匹配库存的来源身份", None
    if not is_gift_operation and len(item_plans) == 1:
        aggregate_task = _build_storage_bag_aggregate_money_task(
            tasks,
            item_plans[0],
            listing_item=listing_item,
            listing_count=listing_count,
            listing_syntax=listing_syntax,
            target_identity_id=target_identity_id,
            target_label=target_row.get("label") or target_row.get("display_name") or str(target_identity_id),
            rows=rows,
            min_transfer_count=min_transfer_count,
            listing_unit_price=listing_unit_price,
        )
        if aggregate_task:
            original_planned = int(item_plans[0].get("planned_quantity") or 0)
            aggregate_quantity = int(aggregate_task.get("aggregate_planned_quantity") or 0)
            item_plans[0]["aggregate_listing_count"] = int(aggregate_task.get("listing_count") or 0)
            item_plans[0]["aggregate_unit_price"] = int(aggregate_task.get("aggregate_unit_price") or 0)
            item_plans[0]["aggregate_planned_quantity"] = aggregate_quantity
            item_plans[0]["aggregate_total_price"] = int(aggregate_task.get("aggregate_total_price") or 0)
            item_plans[0]["used_source_count"] = len(aggregate_task.get("aggregate_buyers") or [])
            if aggregate_quantity < original_planned:
                warnings.append(f"灵石聚合挂单按统一单价仅能覆盖 {aggregate_quantity}，原可搬 {original_planned}；需降低起送/增加上架物/降低单价或改用逐来源精确转移")
            tasks = [aggregate_task]
    total_items = sum(len(task.get("items") or []) for task in tasks)
    total_quantity = sum(int(item.get("quantity") or 0) for task in tasks for item in (task.get("items") or []))
    preview = {
        "operation": operation,
        "target_identity_id": target_identity_id,
        "listing_item": "" if is_gift_operation else listing_item,
        "listing_count": listing_count,
        "listing_unit_price": listing_unit_price,
        "listing_syntax": listing_syntax,
        "mode": mode,
        "reserve_count": reserve_count,
        "min_transfer_count": min_transfer_count,
        "tasks": tasks,
        "item_plans": item_plans,
        "skipped_source_ids": sorted(set(skipped)),
        "warnings": sorted(set(warnings)),
        "summary": f"批量赠送预览 {len(item_plans)} 个物品，{len(tasks)} 笔任务，合计 {total_quantity}" if is_gift_operation else f"批量预览 {len(item_plans)} 个物品，{len(tasks)} 笔任务，合计 {total_quantity}",
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
    identity_ids = [int(identity_id) for identity_id in get_identity_ids()]
    freshness_snapshot = build_inventory_freshness_snapshot(identity_ids, records)
    freshness_rows = {
        int(row.get("identity_id") or 0): row
        for row in freshness_snapshot.get("rows") or []
        if isinstance(row, dict)
    }
    rows = []
    item_names = set()
    totals = {}
    for identity_id in identity_ids:
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
            "inventory_freshness": freshness_rows.get(identity_id) or {},
            "pending_deltas": (freshness_rows.get(identity_id) or {}).get("pending_deltas") or {},
            "merged_items": (freshness_rows.get(identity_id) or {}).get("merged_items") or {},
        })
    rows.sort(key=lambda row: get_realm_sort_key(get_send_as_profile(row["identity_id"]).get("realm"), row["identity_id"]))
    sorted_item_names = sorted(item_names, key=_storage_bag_item_sort_key)
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
        "inventory_freshness": {
            "pending_record_count": int(freshness_snapshot.get("pending_record_count") or 0),
            "stale_record_count": int(freshness_snapshot.get("stale_record_count") or 0),
            "record_count": int(freshness_snapshot.get("record_count") or 0),
        },
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


def _fishing_runtime_command_plan(plan, identity_state, bait_inventory):
    command, runtime_plan = next_fishing_planned_command(dict(identity_state or {}), bait_inventory=bait_inventory)
    if command:
        commands = list((plan.commands if plan else ()) or ())
        if commands and commands[0] == command:
            return commands, plan
        return [command] + [item for item in commands if item != command], runtime_plan or plan
    return [], runtime_plan or plan


def _format_fishing_runtime_command_plan(plan, identity_state, bait_inventory):
    commands, runtime_plan = _fishing_runtime_command_plan(plan, identity_state, bait_inventory)
    if commands:
        return " -> ".join(commands)
    return _format_fishing_command_plan(runtime_plan)


def _parse_fishing_daily_catch_summary(value):
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    summary = {
        "day": str(raw.get("day") or "").strip(),
        "rods": 0,
        "fish": {},
        "rewards": {},
    }
    try:
        summary["rods"] = max(0, int(raw.get("rods", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        summary["rods"] = 0
    for key in ("fish", "rewards"):
        values = raw.get(key)
        if not isinstance(values, dict):
            continue
        for name, count in values.items():
            name = str(name or "").strip()
            try:
                amount = max(0, int(count or 0))
            except (TypeError, ValueError, OverflowError):
                amount = 0
            if name and amount > 0:
                summary[key][name] = amount
    return summary


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


def _coerce_fishing_cancel_after_sec(value):
    return clamp_fishing_cancel_after_sec(value)


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
    transfer_options = []
    for identity_id in get_identity_ids():
        identity_id = int(identity_id or 0)
        if identity_id <= 0 or identity_id == send_as_id:
            continue
        profile = get_send_as_profile(identity_id)
        transfer_options.append({
            "identity_id": identity_id,
            "label": profile.get("label") or profile.get("username") or str(identity_id),
            "protected": _is_storage_bag_protected_identity(identity_id),
        })
    transfer_options.sort(key=lambda row: get_realm_sort_key(get_send_as_profile(row["identity_id"]).get("realm"), row["identity_id"]))
    try:
        transfer_target_id = int(identity_state.get("fishing_transfer_target_id") or 0)
    except (TypeError, ValueError):
        transfer_target_id = 0
    transfer_target_label = "关闭"
    for option in transfer_options:
        if int(option.get("identity_id") or 0) == transfer_target_id:
            transfer_target_label = option.get("label") or str(transfer_target_id)
            break
    else:
        if transfer_target_id > 0:
            transfer_target_label = f"未知身份 {transfer_target_id}"
    caught_fish = parse_pending_open_fish(identity_state.get("fishing_caught_fish_json"))
    daily_catch_summary = _parse_fishing_daily_catch_summary(identity_state.get("fishing_daily_catch_summary_json"))
    runtime_commands, runtime_plan = _fishing_runtime_command_plan(plan, identity_state, bait_inventory)
    return {
        "pond": config.pond,
        "bait": config.bait,
        "flow_mode": "MiniApp",
        "daily_limit": _coerce_fishing_daily_limit(identity_state.get("fishing_daily_limit", 20)),
        "daily_day": identity_state.get("fishing_daily_day") or "",
        "daily_count": int(identity_state.get("fishing_daily_count", 0) or 0),
        "daily_catch_summary": daily_catch_summary,
        "auto_chum_enabled": bool(config.auto_chum_enabled),
        "chum_name": config.chum_name,
        "chum_names": list(config.chum_names or ()),
        "auto_buy_bait_enabled": bool(config.auto_buy_bait_enabled),
        "auto_buy_bait_count": int(config.auto_buy_bait_count or FISHING_DEFAULT_BUY_BAIT_COUNT),
        "auto_probe_enabled": bool(config.auto_probe_enabled),
        "auto_open_fish_enabled": bool(identity_state.get("fishing_auto_open_fish_enabled", True)),
        "cancel_after_sec": _coerce_fishing_cancel_after_sec(identity_state.get("fishing_cancel_after_sec", FISHING_DEFAULT_CANCEL_AFTER_SEC)),
        "transfer_target_id": transfer_target_id,
        "transfer_target_label": transfer_target_label,
        "transfer_identity_options": transfer_options,
        "transfer_due_at": float(identity_state.get("fishing_transfer_due_at", 0) or 0),
        "caught_fish": caught_fish,
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
            "allow_start": bool((runtime_plan or plan).allow_start),
            "commands": runtime_commands,
            "purchase_commands": list(plan.purchase_commands or ()),
            "blocked_reason": (runtime_plan or plan).blocked_reason or "",
            "summary": _format_fishing_runtime_command_plan(plan, identity_state, bait_inventory),
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
            {"name": "昆吾山", "join_command": CMD_REPLICA_KUNWU_JOIN},
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
    kind_configs = get_replica_kind_configs()
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
            "enabled": bool(query_aggregator_config.get("enabled")),
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
        "kind_configs": {
            replica_kind: {
                "kind": replica_kind,
                "name": _REPLICA_UI_TICKET_META[replica_kind]["name"],
                "short": _REPLICA_UI_TICKET_META[replica_kind]["short"],
                "enabled": bool((kind_configs.get(replica_kind) or {}).get("enabled", True)),
                "participant_identity_ids": list((kind_configs.get(replica_kind) or {}).get("participant_identity_ids") or []),
                "dispatch_participant_identity_ids": list((kind_configs.get(replica_kind) or {}).get("dispatch_participant_identity_ids") or []),
            }
            for replica_kind in _REPLICA_UI_KINDS
        },
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
            "enabled": _coerce_ui_bool(
                query_aggregator_input.get("enabled", current_query_aggregator.get("enabled", True))
            ),
            "base_url": query_aggregator_input.get("base_url"),
            "client_id": query_aggregator_input.get("client_id"),
            "secret": next_secret,
        })
    success_cooldown_input = payload.get("success_cooldown_hours")
    if isinstance(success_cooldown_input, dict):
        set_replica_success_cooldown_hours(success_cooldown_input)
    kind_config_input = payload.get("kind_configs")
    if isinstance(kind_config_input, dict):
        set_replica_kind_configs(kind_config_input)

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


def ui_set_replica_query_aggregator_enabled(enabled):
    current = get_replica_query_aggregator_config()
    current["enabled"] = _coerce_ui_bool(enabled)
    updated = set_replica_query_aggregator_config(current)
    save_state()
    action = "开启" if updated.get("enabled") else "关闭"
    return True, f"已{action}拉人汇聚服务提交"


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


def _format_tianxing_timeline_step(step):
    step = step if isinstance(step, dict) else {}
    return {
        "action": str(step.get("action") or ""),
        "arg": str(step.get("arg") or ""),
        "route": str(step.get("route") or ""),
        "reason": str(step.get("reason") or ""),
        "command": str(step.get("command") or ""),
        "family": str(step.get("family") or ""),
        "status": str(step.get("status") or ""),
        "last_error": str(step.get("last_error") or ""),
        "send_msg_id": int(step.get("send_msg_id", 0) or 0),
        "sent_at": fmt_abs_ts(step.get("sent_at", 0) or 0),
        "ack_due_at": fmt_abs_ts(step.get("ack_due_at", 0) or 0),
        "confirmed_at": fmt_abs_ts(step.get("confirmed_at", 0) or 0),
        "released_at": fmt_abs_ts(step.get("released_at", 0) or 0),
        "calibration_due_at": fmt_abs_ts(step.get("calibration_due_at", 0) or 0),
    }


def _format_tianxing_timeline_phase_text(timeline):
    timeline = normalize_tianxing_timeline_state(timeline)
    phase = str(timeline.get("phase") or "idle").strip() or "idle"
    if phase == "blocked_replan":
        blocked_until = float(timeline.get("blocked_until", 0) or 0)
        if blocked_until > time.time():
            return "下游已消费，等待下次重算"
        return "下游已消费，待重算"
    labels = {
        "idle": "空闲",
        "waiting_send": "等待发送",
        "sending": "发送中",
        "sent_waiting_ack": "等待确认",
        "state_confirmed": "前置已确认",
        "downstream_released": "已放行下游",
        "prediction_conflict": "推命冲突等待",
        "ack_timeout": "确认超时校准",
        "calibrating": "校准中",
        "completed": "已完成",
    }
    return labels.get(phase, phase)


def _format_tianxing_timeline_error_text(timeline):
    timeline = normalize_tianxing_timeline_state(timeline)
    phase = str(timeline.get("phase") or "").strip()
    last_error = str(timeline.get("last_error") or "").strip()
    if phase == "blocked_replan" and "放行已被下游动作消费" in last_error:
        return "下游已消费，等待下次时间线重算"
    return last_error


def _format_tianxing_timeline_ui(raw_timeline):
    timeline = normalize_tianxing_timeline_state(raw_timeline)
    retreat_farm = timeline.get("retreat_farm") or {}
    craft_farm = timeline.get("craft_farm") or {}
    released_routes = []
    for route, info in sorted((timeline.get("released_routes") or {}).items()):
        info = info if isinstance(info, dict) else {}
        released_routes.append({
            "route": str(route or ""),
            "released_at": fmt_abs_ts(info.get("released_at", 0) or 0),
            "plan_id": str(info.get("plan_id") or ""),
            "reason": str(info.get("reason") or ""),
        })
    audit = []
    for item in (timeline.get("audit") or [])[-5:]:
        if not isinstance(item, dict):
            continue
        audit.append({
            "ts": fmt_abs_ts(item.get("ts", 0) or 0),
            "event": str(item.get("event") or ""),
            "action": str(item.get("action") or ""),
            "arg": str(item.get("arg") or ""),
            "route": str(item.get("route") or ""),
            "reason": str(item.get("reason") or ""),
        })
    return {
        "plan_id": timeline.get("plan_id") or "",
        "phase": timeline.get("phase") or "idle",
        "phase_label": _format_tianxing_timeline_phase_text(timeline),
        "route": timeline.get("route") or "",
        "reason": timeline.get("reason") or "",
        "created_at": fmt_abs_ts(timeline.get("created_at", 0) or 0),
        "updated_at": fmt_abs_ts(timeline.get("updated_at", 0) or 0),
        "deadline_at": fmt_abs_ts(timeline.get("deadline_at", 0) or 0),
        "blocked_until": fmt_abs_ts(timeline.get("blocked_until", 0) or 0),
        "last_error": timeline.get("last_error") or "",
        "last_error_label": _format_tianxing_timeline_error_text(timeline),
        "active_step_index": int(timeline.get("active_step_index", -1) or -1),
        "active_step": _format_tianxing_timeline_step(timeline.get("active_step") or {}),
        "steps": [_format_tianxing_timeline_step(step) for step in (timeline.get("steps") or [])],
        "released_routes": released_routes,
        "audit": audit,
        "retreat_farm": {
            "phase": str(retreat_farm.get("phase") or "idle"),
            "next_time": fmt_abs_ts(retreat_farm.get("next_time", 0) or 0),
            "target_tianji": int(retreat_farm.get("target_tianji", 0) or 0),
            "start_tianji": int(retreat_farm.get("start_tianji", 0) or 0),
            "last_action": str(retreat_farm.get("last_action") or ""),
            "last_command": str(retreat_farm.get("last_command") or ""),
            "last_error": str(retreat_farm.get("last_error") or ""),
            "last_result": str(retreat_farm.get("last_result") or ""),
            "last_tianji_gain": int(retreat_farm.get("last_tianji_gain", 0) or 0),
            "handoff_ready": bool(retreat_farm.get("handoff_ready")),
        },
        "craft_farm": {
            "phase": str(craft_farm.get("phase") or "idle"),
            "next_time": fmt_abs_ts(craft_farm.get("next_time", 0) or 0),
            "target_tianji": int(craft_farm.get("target_tianji", 0) or 0),
            "start_tianji": int(craft_farm.get("start_tianji", 0) or 0),
            "estimated_tianji": int(craft_farm.get("estimated_tianji", 0) or 0),
            "daily_limit": int(craft_farm.get("daily_limit", 0) or 0),
            "daily_count": int(craft_farm.get("daily_count", 0) or 0),
            "success_count": int(craft_farm.get("success_count", 0) or 0),
            "hit_count": int(craft_farm.get("hit_count", 0) or 0),
            "miss_count": int(craft_farm.get("miss_count", 0) or 0),
            "last_item": str(craft_farm.get("last_item") or ""),
            "last_action": str(craft_farm.get("last_action") or ""),
            "last_command": str(craft_farm.get("last_command") or ""),
            "last_error": str(craft_farm.get("last_error") or ""),
            "last_result": str(craft_farm.get("last_result") or ""),
            "last_tianji_gain": int(craft_farm.get("last_tianji_gain", 0) or 0),
            "handoff_ready": bool(craft_farm.get("handoff_ready")),
        },
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
        hehuan_observation = normalize_hehuan_observation(identity_state.get("hehuan_observation"))
        tianxing_observation = normalize_tianxing_observation(identity_state.get("tianxing_observation"))
        tianxing_auto_config = normalize_tianxing_auto_config(identity_state.get("tianxing_auto_config"))
        tianxing_timeline = _format_tianxing_timeline_ui(identity_state.get("tianxing_timeline_state"))
        tianxing_pause_state = get_tianxing_automation_pause_state(observed=tianxing_observation)
        tianxing_fixed_star = str(tianxing_observation.get("fixed_star") or "").strip()
        tianxing_fixed_star_day = str(tianxing_observation.get("fixed_star_day") or "").strip()
        tianxing_fixed_star_today = (
            tianxing_fixed_star
            if tianxing_fixed_star and (not tianxing_fixed_star_day or tianxing_fixed_star_day == get_day_key(time.time()))
            else ""
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
            "explore_rift_rebirth": {
                **get_rebirth_choice_config(),
                "choice_mode_choices": [
                    {"value": "safe_first", "label": "稳妥优先"},
                    {"value": "root_first", "label": "灵根优先"},
                ],
                "root_type_choices": [{"value": item, "label": item or "不限"} for item in REBIRTH_ROOT_TYPES],
                "blind_index_choices": [1, 2, 3],
            },
            "hehuan_retry_max_interval_min": int(hehuan_observation.get("auto_retry_max_interval_min", HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN) or HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN),
            "hehuan_retry_count": int(hehuan_observation.get("auto_retry_count", 0) or 0),
            "hehuan_retry_limit": HEHUAN_AUTO_RETRY_LIMIT,
            "hehuan_last_warm_success_at": fmt_abs_ts(hehuan_observation.get("last_warm_success_at", 0) or 0),
            "hehuan_auto_next_time": fmt_abs_ts(hehuan_observation.get("auto_next_time", 0) or 0),
            "tianxing": {
                "auto_config": tianxing_auto_config,
                "timeline": tianxing_timeline,
                "available_stars": list(tianxing_observation.get("available_stars") or []),
                "fixed_star": tianxing_fixed_star_today,
                "stale_fixed_star": tianxing_fixed_star if tianxing_fixed_star and not tianxing_fixed_star_today else "",
                "fixed_star_day": tianxing_fixed_star_day,
                "current_prediction": tianxing_observation.get("current_prediction") or "",
                "current_prediction_until": fmt_abs_ts(tianxing_observation.get("current_prediction_until", 0) or 0),
                "current_change": tianxing_observation.get("current_change") or "",
                "current_change_until": fmt_abs_ts(tianxing_observation.get("current_change_until", 0) or 0),
                "tianji_value": int(tianxing_observation.get("tianji_value", 0) or 0),
                "calamity_count": int(tianxing_observation.get("calamity_count", 0) or 0),
                "hit_count": int(tianxing_observation.get("hit_count", 0) or 0),
                "miss_count": int(tianxing_observation.get("miss_count", 0) or 0),
                "change_count": int(tianxing_observation.get("change_count", 0) or 0),
                "auto_next_time": fmt_abs_ts(tianxing_observation.get("auto_next_time", 0) or 0),
                "auto_last_action": tianxing_observation.get("auto_last_action") or "",
                "auto_last_error": tianxing_observation.get("auto_last_error") or "",
                "auto_last_plan": tianxing_observation.get("auto_last_plan") or "",
                "auto_last_plan_at": fmt_abs_ts(tianxing_observation.get("auto_last_plan_at", 0) or 0),
                "automation_paused": bool(tianxing_pause_state.get("paused")),
                "automation_pause_text": get_tianxing_automation_pause_text(observed=tianxing_observation),
                "automation_paused_until": (
                    "手动恢复前"
                    if float(tianxing_pause_state.get("until", 0) or 0) < 0
                    else fmt_abs_ts(tianxing_pause_state.get("until", 0) or 0)
                ),
                "automation_paused_reason": tianxing_pause_state.get("reason") or "",
                "last_observed_at": fmt_abs_ts(tianxing_observation.get("last_observed_at", 0) or 0),
            },
            "mulan": {
                "phase": identity_state.get("mulan_phase") or "idle",
                "next_time": fmt_abs_ts(identity_state.get("next_mulan_time", 0) or 0),
                "reply_due_at": fmt_abs_ts(identity_state.get("mulan_reply_due_at", 0) or 0),
                "reply_to_msg_id": int(identity_state.get("mulan_reply_to_msg_id", 0) or 0),
                "pending_ids": identity_state.get("mulan_pending_ids") or "1,2,3",
                "current_id": int(identity_state.get("mulan_current_id", 0) or 0),
                "public_id": int(identity_state.get("mulan_public_id", 0) or 0),
                "public_text": identity_state.get("mulan_public_text") or "",
                "support_action": identity_state.get("mulan_support_action") or "",
                "last_command": identity_state.get("mulan_last_command") or "",
                "last_result": identity_state.get("mulan_last_result") or "",
                "last_error": identity_state.get("mulan_last_error") or "",
                "cycle_count": int(identity_state.get("mulan_cycle_count", 0) or 0),
            },
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
            "small_world_high_stock_silence_enabled": bool(identity_state.get("small_world_high_stock_silence_enabled", False)),
            "small_world_barrier_enabled": bool(identity_state.get("small_world_barrier_enabled", True)),
            "small_world_barrier_min_stock": int(identity_state.get("small_world_barrier_min_stock", 130000) or 130000),
            "small_world_barrier_guard_before_min": int(identity_state.get("small_world_barrier_guard_before_min", 30) or 30),
            "small_world_barrier_min_interval_hours": float(identity_state.get("small_world_barrier_min_interval_hours", 18) or 18),
            "small_world_incense_stock": int(identity_state.get("small_world_incense_stock", 0) or 0),
            "small_world_faith_value": int(identity_state.get("small_world_faith_value", 0) or 0),
            "wanxin": get_wanxin_ui_state() if "婉心封魂" in available_module_names else {},
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
                "next_pet_formation_time": fmt_abs_ts(identity_state.get("next_pet_formation_time", 0)),
                "next_stargazer_panel_time": fmt_abs_ts(identity_state.get("next_stargazer_panel_time", 0)),
                "next_stargazer_action_time": fmt_abs_ts(stargazer_next_action_time),
                "stargazer_followup_due_at": fmt_abs_ts(stargazer_followup_due_at),
                "stargazer_collect_due_at": fmt_abs_ts(identity_state.get("stargazer_collect_due_at", 0)),
                "next_quiz_time": fmt_abs_ts(identity_state.get("next_quiz_time", 0)),
                "next_mulan_time": fmt_abs_ts(identity_state.get("next_mulan_time", 0)),
                "quiz_deadline_at": fmt_abs_ts(identity_state.get("quiz_deadline_at", 0)),
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
        "runtime_health": get_runtime_health_snapshot(),
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


def _ui_static_asset_version():
    latest = 0
    for static_dir in (UI_STATIC_DIR, UI_NEW_STATIC_DIR):
        try:
            for root, _dirs, files in os.walk(static_dir):
                for file_name in files:
                    if not file_name.endswith((".js", ".css")):
                        continue
                    try:
                        latest = max(latest, int(os.path.getmtime(os.path.join(root, file_name))))
                    except OSError:
                        continue
        except OSError:
            continue
    return str(latest or int(time.time()))


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
            "asset_version": html_escape(_ui_static_asset_version()),
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
    if target is not None and not normalize_duel_targets(target):
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
        "high_stock_silence": ("small_world_high_stock_silence_enabled", "高香火静默"),
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


async def ui_set_hehuan_config(send_as_id, *, retry_max_interval_min=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    with use_identity(send_as_id):
        max_interval = set_hehuan_retry_max_interval_min(retry_max_interval_min)
        save_state()
    await send_audit_log(
        f"🌸 已更新合欢宗补发策略：随机 1-{int(max_interval)} 分钟，最多 {HEHUAN_AUTO_RETRY_LIMIT} 次",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新合欢宗补发策略[{get_identity_display_name(send_as_id)}]"


async def ui_set_tianxing_config(send_as_id, config=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    config = config if isinstance(config, dict) else {}
    with use_identity(send_as_id):
        merged = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
        merged.update(config)
        if "farm_route" not in config:
            preview = normalize_tianxing_auto_config(merged)
            if preview.get("craft_farm_enabled"):
                merged["farm_route"] = "炼制"
            elif preview.get("retreat_farm_enabled"):
                merged["farm_route"] = "闭关"
        normalized = set_tianxing_auto_config(merged)
        save_state()
    enabled_parts = []
    for key, label in (
        ("auto_panel_enabled", "查盘"),
        ("auto_observe_enabled", "观命"),
        ("auto_clear_calamity_enabled", "消劫"),
        ("auto_set_star_enabled", "定命"),
        ("auto_predict_enabled", "推命"),
        ("auto_change_fate_enabled", "改命"),
        ("strategy_dry_run_enabled", "dry-run"),
    ):
        enabled_parts.append(f"{label}={'开' if normalized.get(key) else '关'}")
    await send_audit_log(
        f"🌌 已更新天星宗自动策略：{'，'.join(enabled_parts)}",
        scope="identity",
        send_as_id=send_as_id,
        limit=260,
    )
    return True, f"已更新天星宗策略[{get_identity_display_name(send_as_id)}]"


async def ui_set_wanxin_config(send_as_id, config=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    config = config if isinstance(config, dict) else {}
    with use_identity(send_as_id):
        ok, message, snapshot = set_wanxin_config(config)
        save_state()
    if ok:
        auto_config = (snapshot or {}).get("auto_config") or {}
        assist = (snapshot or {}).get("assist") or {}
        await send_audit_log(
            "🌙 已更新婉心封魂策略："
            f"探望={'开' if auto_config.get('visit_enabled') else '关'}，"
            f"护持={'开' if auto_config.get('protect_enabled') else '关'}，"
            f"推演={'开' if auto_config.get('deduce_enabled') else '关'}，"
            f"委托={'开' if auto_config.get('publish_enabled') else '关'}，"
            f"协助={'开' if auto_config.get('assist_enabled') else '关'}，"
            f"阴罗={assist.get('send_as_id') or '未配置'}",
            scope="identity",
            send_as_id=send_as_id,
            limit=260,
        )
    return ok, f"{message}[{get_identity_display_name(send_as_id)}]" if ok else message


async def ui_set_explore_rift_rebirth_config(send_as_id, payload=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    payload = payload if isinstance(payload, dict) else {}
    choice_mode = payload.get("choice_mode")
    if choice_mode not in REBIRTH_CHOICE_MODES:
        choice_mode = "safe_first"
    with use_identity(send_as_id):
        config = set_rebirth_choice_config(
            choice_mode=choice_mode,
            preferred_root_type=payload.get("preferred_root_type"),
            preferred_attrs=payload.get("preferred_attrs"),
            blind_index=payload.get("blind_index"),
        )
        save_state()
    mode_label = "灵根优先" if config["choice_mode"] == "root_first" else "稳妥优先"
    root_label = config["preferred_root_type"] or "不限"
    attrs_label = config["preferred_attrs"] or "不限"
    await send_audit_log(
        f"🕳 已更新夺舍选择：{mode_label}，灵根 {root_label}，属性 {attrs_label}，盲选 {config['blind_index']}",
        scope="identity",
        send_as_id=send_as_id,
        limit=220,
    )
    return True, f"已更新夺舍选择[{get_identity_display_name(send_as_id)}]"


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
    current_identity_state = get_identity_state(send_as_id)
    daily_limit = _coerce_fishing_daily_limit(payload.get("daily_limit"))
    auto_buy_bait_count = _coerce_fishing_buy_bait_count(payload.get("auto_buy_bait_count"))
    cancel_after_sec = _coerce_fishing_cancel_after_sec(
        payload.get("cancel_after_sec", current_identity_state.get("fishing_cancel_after_sec", FISHING_DEFAULT_CANCEL_AFTER_SEC))
    )
    auto_chum_enabled = _coerce_ui_bool(payload.get("auto_chum_enabled"))
    auto_buy_bait_enabled = _coerce_ui_bool(payload.get("auto_buy_bait_enabled"))
    auto_probe_enabled = _coerce_ui_bool(payload.get("auto_probe_enabled"))
    raw_transfer_target_id = (
        payload.get("transfer_target_id")
        if "transfer_target_id" in payload
        else current_identity_state.get("fishing_transfer_target_id", 0)
    )
    try:
        transfer_target_id = int(str(raw_transfer_target_id or 0).replace(",", ""))
    except (TypeError, ValueError):
        return False, "无效的鱼获赠送目标"
    known_ids = {int(identity_id or 0) for identity_id in get_identity_ids()}
    if transfer_target_id < 0 or (transfer_target_id > 0 and transfer_target_id not in known_ids):
        return False, "鱼获赠送目标不存在"
    if transfer_target_id == send_as_id:
        return False, "鱼获赠送目标不能是当前身份"
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
        state["fishing_cancel_after_sec"] = int(cancel_after_sec)
        state["fishing_transfer_target_id"] = int(transfer_target_id)
        forced_bait = str(state.get("fishing_forced_buy_bait") or "").strip()
        if forced_bait and forced_bait != config.bait:
            state["fishing_forced_buy_bait"] = ""
            state["fishing_forced_buy_count"] = 0
        save_state()
        saved_identity_state = dict(state.items())
    plan = plan_fishing_commands(
        config,
        bait_inventory=_get_fishing_bait_inventory(send_as_id),
        chum_usage_counts=parse_chum_usage_counts(saved_identity_state.get("fishing_chum_counts")),
        active_chum_name=saved_identity_state.get("fishing_active_chum_name") or "",
        active_chum_rods_remaining=int(saved_identity_state.get("fishing_chum_rods_remaining", 0) or 0),
    )
    plan_summary = _format_fishing_runtime_command_plan(plan, saved_identity_state, _get_fishing_bait_inventory(send_as_id))
    await send_audit_log(
        "🎣 已更新灵溪垂钓配置："
        f"{config.pond}/{config.bait}｜"
        f"次数={daily_limit}/日｜"
        f"打窝={','.join(config.chum_names or ()) or '无'}｜"
        f"买饵={'开' if config.auto_buy_bait_enabled else '关'}x{config.auto_buy_bait_count}｜"
        f"试饵={'开' if config.auto_probe_enabled else '关'}｜"
        f"开鱼={'开' if auto_open_fish_enabled else '关'}｜"
        f"收竿={cancel_after_sec}秒｜"
        f"鱼获赠送={get_identity_display_name(transfer_target_id) if transfer_target_id else '关'}｜"
        f"计划={plan_summary}",
        scope="identity",
        send_as_id=send_as_id,
        limit=260,
    )
    return True, f"已更新灵溪垂钓[{get_identity_display_name(send_as_id)}]：{daily_limit}/日｜买饵{config.auto_buy_bait_count}｜{plan_summary}"


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
_pending_login_locks = {}
ACCOUNT_LOGIN_CONNECT_TIMEOUT_SEC = 10
ACCOUNT_LOGIN_QR_CONNECT_TIMEOUT_SEC = 45
ACCOUNT_LOGIN_ACTION_TIMEOUT_SEC = 12
ACCOUNT_LOGIN_DISCONNECT_TIMEOUT_SEC = 15
ACCOUNT_LOGIN_QR_REUSE_MIN_REMAINING_SEC = 10
ACCOUNT_LOGIN_PHONE_CODE_CONFIRM_WAIT_SEC = 8


def _get_pending_login_lock(session_key):
    session_key = str(session_key or "")
    lock = _pending_login_locks.get(session_key)
    if lock is None:
        lock = asyncio.Lock()
        _pending_login_locks[session_key] = lock
    return lock


def _pending_login_api_matches(pending, api_id, api_hash):
    if not isinstance(pending, dict):
        return False
    return (
        pending.get("api_id") == api_id
        and str(pending.get("api_hash") or "") == str(api_hash or "")
    )


def _build_pending_qr_payload(pending):
    pending = pending or {}
    qr_expires_at = float(pending.get("qr_expires_at", 0) or 0)
    qr_url = pending.get("qr_url") or ""
    status = str(pending.get("status") or "waiting_scan")
    payload = {
        "status": status,
        "message": pending.get("message") or "",
        "qr_expires_at": fmt_abs_ts(qr_expires_at),
        "qr_expires_at_ts": qr_expires_at,
        "remaining_sec": max(0, int(qr_expires_at - time.time())),
    }
    if qr_url:
        payload.update({
            "qr_url": qr_url,
            "qr_svg": _build_qr_svg_markup(qr_url),
        })
    return payload


def _format_account_login_error(prefix, exc):
    if isinstance(exc, asyncio.TimeoutError):
        return f"{prefix}: Telegram 连接超时，请稍后重试"
    if isinstance(exc, sqlite3.OperationalError):
        return f"{prefix}: 本地 session 数据库暂不可写，已清理临时登录态，请稍后重试"
    return f"{prefix}: {exc}"


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

    current_task = asyncio.current_task()
    for task_key in ("wait_task", "prepare_task", "phone_code_task"):
        pending_task = pending.get(task_key)
        if pending_task and pending_task is not current_task and not pending_task.done():
            pending_task.cancel()

    tc = pending.get("client")
    disconnected = True
    if disconnect and tc:
        disconnected = await _disconnect_pending_login_client(tc)

    if remove_temp_files and (disconnected or not tc):
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


async def _disconnect_pending_login_client(tc):
    if tc is None:
        return True
    try:
        await asyncio.wait_for(tc.disconnect(), timeout=ACCOUNT_LOGIN_DISCONNECT_TIMEOUT_SEC)
        return True
    except Exception:
        return False


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

    await _disconnect_pending_login_client(tc)

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
        disconnected = await _disconnect_pending_login_client(tc)
        if disconnected:
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
        disconnected = await _disconnect_pending_login_client(tc)
        if disconnected:
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


async def _prepare_pending_qr_login(session_key, flow_id, tc):
    try:
        await asyncio.wait_for(tc.connect(), timeout=ACCOUNT_LOGIN_QR_CONNECT_TIMEOUT_SEC)
        ignored_ids = []
        for raw_account_id in get_accounts().keys():
            try:
                ignored_ids.append(int(raw_account_id))
            except (TypeError, ValueError):
                continue
        qr_login = await asyncio.wait_for(
            tc.qr_login(ignored_ids=ignored_ids or None),
            timeout=ACCOUNT_LOGIN_ACTION_TIMEOUT_SEC,
        )
        expires_at = float(qr_login.expires.timestamp()) if getattr(qr_login, "expires", None) else 0
    except asyncio.CancelledError:
        await _disconnect_pending_login_client(tc)
        raise
    except Exception as e:
        disconnected = await _disconnect_pending_login_client(tc)
        if disconnected:
            _cleanup_pending_temp_session_files(session_key)
        _set_pending_login_state(
            session_key,
            flow_id,
            status="error",
            message=_format_account_login_error("生成二维码失败", e),
            client=None,
            prepare_task=None,
            wait_task=None,
            qr_url="",
            qr_expires_at=0,
        )
        return

    pending = _pending_login.get(session_key)
    if not pending or str(pending.get("flow_id") or "") != str(flow_id):
        await _disconnect_pending_login_client(tc)
        return

    _set_pending_login_state(
        session_key,
        flow_id,
        status="waiting_scan",
        message="请使用已登录 Telegram 的手机扫码确认",
        qr_url=qr_login.url,
        qr_expires_at=expires_at,
        prepare_task=None,
    )
    wait_task = asyncio.create_task(_wait_pending_qr_login(session_key, flow_id, tc, qr_login))
    _set_pending_login_state(session_key, flow_id, wait_task=wait_task)


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


async def _send_pending_phone_code(session_key, flow_id, tc, phone):
    try:
        sent = await tc.send_code_request(phone)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        disconnected = await _disconnect_pending_login_client(tc)
        if disconnected:
            _cleanup_pending_temp_session_files(session_key)
        _set_pending_login_state(
            session_key,
            flow_id,
            status="error",
            message=_format_account_login_error("发送验证码失败", e),
            client=None,
            phone_code_task=None,
            phone_code_hash="",
        )
        return False

    _set_pending_login_state(
        session_key,
        flow_id,
        status="waiting_code",
        message="验证码已发送，请查收",
        phone_code_hash=getattr(sent, "phone_code_hash", "") or "",
        phone_code_task=None,
    )
    return True


async def ui_account_login_start(phone, session_key, api_id=None, api_hash=None):
    phone = (phone or "").strip()
    if not phone:
        return False, "请输入手机号", None
    try:
        parsed_api_id, parsed_api_hash = _parse_account_login_api(api_id, api_hash)
    except ValueError as e:
        return False, str(e), None

    async with _get_pending_login_lock(session_key):
        pending = _pending_login.get(session_key)
        if (
            pending
            and pending.get("mode") == "phone"
            and pending.get("status") == "waiting_code"
            and str(pending.get("phone") or "") == phone
            and _pending_login_api_matches(pending, parsed_api_id, parsed_api_hash)
        ):
            return True, "验证码已发送，请查收", None

        await _clear_pending_login(session_key, remove_temp_files=True)

        flow_id = str(time.time_ns())
        tc = create_account_client(f"pending_{session_key}", api_id=parsed_api_id, api_hash=parsed_api_hash)
        _pending_login[session_key] = {
            "mode": "phone",
            "status": "connecting",
            "message": "正在连接 Telegram 并发送验证码",
            "client": tc,
            "phone": phone,
            "phone_code_hash": "",
            "qr_url": "",
            "qr_expires_at": 0,
            "wait_task": None,
            "phone_code_task": None,
            "flow_id": flow_id,
            "account_id": 0,
            "api_id": parsed_api_id,
            "api_hash": parsed_api_hash,
        }
        try:
            await asyncio.wait_for(tc.connect(), timeout=ACCOUNT_LOGIN_CONNECT_TIMEOUT_SEC)
        except Exception as e:
            await _clear_pending_login(session_key, remove_temp_files=True)
            return False, _format_account_login_error("发送验证码失败", e), None

        phone_code_task = asyncio.create_task(_send_pending_phone_code(session_key, flow_id, tc, phone))
        _set_pending_login_state(session_key, flow_id, phone_code_task=phone_code_task)
        try:
            sent_ok = await asyncio.wait_for(asyncio.shield(phone_code_task), timeout=ACCOUNT_LOGIN_ACTION_TIMEOUT_SEC)
            if not sent_ok:
                pending_after_error = _pending_login.get(session_key) or {}
                return False, pending_after_error.get("message") or "发送验证码失败", None
            return True, "验证码已发送", None
        except asyncio.TimeoutError:
            _set_pending_login_state(
                session_key,
                flow_id,
                status="waiting_code",
                message="验证码请求仍在确认；如果 Telegram 已收到验证码，可直接输入",
            )
            return True, "验证码请求仍在确认；如果已收到验证码，请直接输入", None
        except Exception as e:
            await _clear_pending_login(session_key, remove_temp_files=True)
            return False, _format_account_login_error("发送验证码失败", e), None


async def ui_account_login_qr_start(session_key, api_id=None, api_hash=None):
    try:
        parsed_api_id, parsed_api_hash = _parse_account_login_api(api_id, api_hash)
    except ValueError as e:
        return False, str(e), None

    async with _get_pending_login_lock(session_key):
        pending = _pending_login.get(session_key)
        if pending and pending.get("mode") == "qr" and _pending_login_api_matches(pending, parsed_api_id, parsed_api_hash):
            status = str(pending.get("status") or "")
            expires_at = float(pending.get("qr_expires_at", 0) or 0)
            if status == "connecting":
                return True, "二维码生成中，请稍后", _build_pending_qr_payload(pending)
            if (
                status == "waiting_scan"
                and pending.get("qr_url")
                and expires_at > time.time() + ACCOUNT_LOGIN_QR_REUSE_MIN_REMAINING_SEC
            ):
                return True, "复用当前二维码，请使用 Telegram 扫码确认", _build_pending_qr_payload(pending)

        await _clear_pending_login(session_key, remove_temp_files=True)

        flow_id = str(time.time_ns())
        tc = create_account_client(f"pending_{session_key}", api_id=parsed_api_id, api_hash=parsed_api_hash)
        _pending_login[session_key] = {
            "mode": "qr",
            "status": "connecting",
            "message": "正在生成二维码",
            "client": tc,
            "phone": "",
            "phone_code_hash": "",
            "qr_url": "",
            "qr_expires_at": 0,
            "wait_task": None,
            "prepare_task": None,
            "flow_id": flow_id,
            "account_id": 0,
            "api_id": parsed_api_id,
            "api_hash": parsed_api_hash,
        }
        prepare_task = asyncio.create_task(_prepare_pending_qr_login(session_key, flow_id, tc))
        _set_pending_login_state(session_key, flow_id, prepare_task=prepare_task)
        return True, "二维码生成中，请稍后", _build_pending_qr_payload(_pending_login.get(session_key))


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
    async with _get_pending_login_lock(session_key):
        await _clear_pending_login(session_key, remove_temp_files=True)
    return True, "已取消当前登录流程"


async def ui_account_login_verify(code, session_key, password=None):
    async with _get_pending_login_lock(session_key):
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

        if mode == "phone":
            phone_code_task = pending.get("phone_code_task")
            if phone_code_task and not phone_code_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(phone_code_task),
                        timeout=ACCOUNT_LOGIN_PHONE_CODE_CONFIRM_WAIT_SEC,
                    )
                except asyncio.TimeoutError:
                    return False, "验证码请求仍在确认，请稍后再验证", None
                pending = _pending_login.get(session_key)
                if not pending:
                    return False, "登录会话已过期，请重新开始", None
                tc = pending.get("client")
                phone = pending.get("phone") or phone
                phone_code_hash = pending.get("phone_code_hash") or ""
                flow_id = pending.get("flow_id")
                status = str(pending.get("status") or "")
            if status == "error":
                return False, pending.get("message") or "发送验证码失败，请重新开始", None

        if mode == "qr" and password and status != "need_2fa":
            return False, "当前二维码登录尚未进入两步验证", None

        try:
            if password:
                await asyncio.wait_for(tc.sign_in(password=password), timeout=ACCOUNT_LOGIN_ACTION_TIMEOUT_SEC)
            elif mode == "phone":
                await asyncio.wait_for(
                    tc.sign_in(phone, code, phone_code_hash=phone_code_hash),
                    timeout=ACCOUNT_LOGIN_ACTION_TIMEOUT_SEC,
                )
            else:
                return False, "当前二维码登录尚未进入两步验证", None
        except Exception as e:
            err_str = str(e)
            if "Two-steps verification" in err_str or "SessionPasswordNeeded" in err_str or "2FA" in err_str:
                _set_pending_login_state(session_key, flow_id, status="need_2fa", message="需要两步验证密码")
                return False, "need_2fa", None
            await _clear_pending_login(session_key, remove_temp_files=True)
            return False, _format_account_login_error("登录失败", e), None

        try:
            ok, message, account_id = await _finalize_account_login(session_key, tc, flow_id=flow_id)
        except Exception as e:
            await _clear_pending_login(session_key, disconnect=False, remove_temp_files=True)
            return False, _format_account_login_error("登录失败", e), None

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


async def ui_send_miniapp_entry_probe(send_as_id, game_key):
    try:
        identity_id = int(send_as_id or 0)
    except (TypeError, ValueError):
        identity_id = 0
    if identity_id not in get_identity_ids():
        return False, "身份不存在", {}
    if not get_identity_enabled(identity_id):
        return False, "身份已停用", {}

    normalized_game_key = str(game_key or "").strip().lower()
    command = MINIAPP_ENTRY_PROBE_COMMANDS.get(normalized_game_key)
    if not command:
        allowed = "/".join(sorted(MINIAPP_ENTRY_PROBE_COMMANDS))
        return False, f"MiniApp 入口诊断仅允许 {allowed}", {}

    op_id = f"miniapp_entry_probe:{normalized_game_key}:{identity_id}:{int(time.time())}"
    msg = await send_game_command(
        command,
        track=False,
        send_as_id=identity_id,
        priority="normal",
        max_retry=0,
        source_module="MiniApp诊断",
        op_id=op_id,
        chain_id="miniapp_entry_probe",
        delete_policy="keep",
        queue_timeout=90,
    )
    extra = {
        "game_key": normalized_game_key,
        "command": command,
    }
    if not msg:
        return False, "入口命令未发送，可能被全局暂停/安全锁/队列保护拦截", extra

    extra["msg_id"] = int(getattr(msg, "id", 0) or 0)
    await send_audit_log(
        f"🧪 MiniApp入口诊断已发送：{command}｜玩法={normalized_game_key}｜msg_id={extra['msg_id']}",
        scope="identity",
        send_as_id=identity_id,
        limit=220,
        priority="low",
    )
    return True, "已发送 MiniApp 入口诊断命令，等待真实按钮/回包入库", extra


def _phaseful_passive_trigger_targets(identity_id, now=None):
    """Return only phases where a harmless text can reveal a stuck settlement."""
    now = float(now if now is not None else time.time())
    targets = []
    with use_identity(identity_id):
        for label, enabled_key, phase_key, next_time_key in (
            ("元婴", "yuanying_enabled", "yuanying_phase", "next_yuanying_time"),
            ("深度闭关", "deep_retreat_enabled", "deep_retreat_phase", "next_deep_retreat_time"),
        ):
            if not state.get(enabled_key):
                continue
            phase = str(state.get(phase_key) or "idle").strip()
            try:
                next_time = float(state.get(next_time_key, 0) or 0)
            except (TypeError, ValueError, OverflowError):
                next_time = 0.0
            if phase in {"summary_due", "post_summary_wait"}:
                targets.append(f"{label}:{phase}")
            elif phase == "running" and next_time > 0 and next_time <= now:
                targets.append(f"{label}:running_due")
    return targets


async def ui_send_phaseful_passive_trigger(send_as_id):
    """Send the fixed ordinary-text trigger during a Tianzun maintenance pause.

    No raw text is accepted, it never retries, and it remains subject to the
    normal queue, per-account cooldown, health, and safety checks.
    """
    try:
        identity_id = int(send_as_id or 0)
    except (TypeError, ValueError):
        identity_id = 0
    if identity_id not in get_identity_ids():
        return False, "身份不存在", {}
    if not get_identity_enabled(identity_id):
        return False, "身份已停用", {}
    if get_global_enabled() or get_global_pause_source() != MAINTENANCE_PAUSE_SOURCE:
        return False, "仅允许在天尊维护导致的全局暂停期间执行", {}

    targets = _phaseful_passive_trigger_targets(identity_id)
    if not targets:
        return False, "当前没有到期或待结算的元婴/深度闭关状态，不发送普通触发文本", {}

    op_id = f"phaseful_passive_trigger:{identity_id}:{int(time.time())}"
    msg = await send_game_command(
        PHASEFUL_PASSIVE_TRIGGER_TEXT,
        track=False,
        send_as_id=identity_id,
        priority="normal",
        max_retry=0,
        source_module=PHASEFUL_PASSIVE_TRIGGER_SOURCE_MODULE,
        op_id=op_id,
        chain_id="phaseful_passive_trigger",
        delete_policy="keep",
        queue_timeout=120,
        allow_maintenance_pause=True,
    )
    extra = {
        "text": PHASEFUL_PASSIVE_TRIGGER_TEXT,
        "targets": targets,
    }
    if not msg:
        return False, "普通触发文本未发送，仍受队列、账号、天尊健康和安全保护约束", extra

    extra["msg_id"] = int(getattr(msg, "id", 0) or 0)
    await send_audit_log(
        f"🧩 被动结算触发已发送：{PHASEFUL_PASSIVE_TRIGGER_TEXT}｜目标={'、'.join(targets)}｜msg_id={extra['msg_id']}",
        scope="identity",
        send_as_id=identity_id,
        limit=220,
        priority="low",
    )
    return True, "已发送普通触发文本，等待真实游戏结算回包", extra


async def ui_send_miniapp_manual_run(send_as_id, game_key, payload=None):
    payload = dict(payload or {})
    try:
        identity_id = int(send_as_id or 0)
    except (TypeError, ValueError):
        identity_id = 0
    if identity_id not in get_identity_ids():
        return False, "身份不存在", {}
    if not get_identity_enabled(identity_id):
        return False, "身份已停用", {}

    normalized_game_key = str(game_key or "").strip().lower()
    command = MINIAPP_MANUAL_RUN_COMMANDS.get(normalized_game_key)
    if not command:
        allowed = "/".join(sorted(MINIAPP_MANUAL_RUN_COMMANDS))
        return False, f"MiniApp 手动执行仅允许 {allowed}", {}

    if normalized_game_key == "cave_treasure":
        authorize_cave_treasure_miniapp_manual_run(identity_id)
    if normalized_game_key == "stargazer":
        authorize_stargazer_miniapp_manual_run(identity_id)
    if normalized_game_key == "tree":
        mode = str(payload.get("mode") or "jump").strip().lower()
        if mode not in {"jump", "fly"}:
            return False, "灵树模式仅允许 jump/fly", {}
        score_config = get_tree_miniapp_score_config(identity_id)
        authorize_tree_miniapp_manual_run(
            identity_id,
            mode=mode,
            score_profile=dict(score_config.get(mode) or {}),
            submit=True,
        )
    if normalized_game_key == "trial":
        authorize_trial_miniapp_manual_run(identity_id)
    op_id = f"miniapp_manual_run:{normalized_game_key}:{identity_id}:{int(time.time())}"
    msg = await send_game_command(
        command,
        track=False,
        send_as_id=identity_id,
        priority="normal",
        max_retry=0,
        source_module="MiniApp手动",
        op_id=op_id,
        chain_id="miniapp_manual_run",
        delete_policy="keep",
        queue_timeout=90,
    )
    extra = {
        "game_key": normalized_game_key,
        "command": command,
    }
    if not msg:
        if normalized_game_key == "cave_treasure":
            revoke_cave_treasure_miniapp_manual_run(identity_id)
        if normalized_game_key == "stargazer":
            revoke_stargazer_miniapp_manual_run(identity_id)
        if normalized_game_key == "tree":
            revoke_tree_miniapp_manual_run(identity_id)
        if normalized_game_key == "trial":
            revoke_trial_miniapp_manual_run(identity_id)
        return False, "手动执行命令未发送，可能被全局暂停/安全锁/队列保护拦截", extra

    extra["msg_id"] = int(getattr(msg, "id", 0) or 0)
    await send_audit_log(
        f"🧪 MiniApp手动执行已发送：{command}｜玩法={normalized_game_key}｜msg_id={extra['msg_id']}",
        scope="identity",
        send_as_id=identity_id,
        limit=220,
        priority="low",
    )
    return True, "已发送 MiniApp 手动执行命令，等待入口按钮接管", extra


async def ui_run_cave_public_entry(send_as_id, action, public_entry_url):
    try:
        identity_id = int(send_as_id or 0)
    except (TypeError, ValueError):
        identity_id = 0
    if identity_id not in get_identity_ids():
        return False, "身份不存在", {}
    if not is_cave_public_identity_available(identity_id):
        return False, "身份已停用", {}
    if public_entry_url:
        candidate_urls = _normalize_cave_public_entry_urls_value(public_entry_url)
    else:
        candidate_urls = list(normalize_miniapp_auto_config().get("cave_public_entry_urls") or [])
    if not candidate_urls:
        return False, "缺少洞府公共入口 URL", {}
    if _cave_public_ui_run_lock.locked():
        return False, "洞府公共入口已有操作执行中，请等待当前请求完成", {}
    async with _cave_public_ui_run_lock:
        normalized_action = str(action or "").strip().lower()
        result = {}
        attempted = []
        for index, url in enumerate(candidate_urls):
            if normalized_action == "small_world":
                result = await run_cave_public_small_world_sync(identity_id, url)
            elif normalized_action in {"treasure", "hunt", "cave_treasure"}:
                result = await run_cave_public_treasure(identity_id, url)
            elif normalized_action in {"trial", "tianji_trial"}:
                result = await run_cave_public_trial(identity_id, url)
            elif normalized_action in {"fishing", "fish"}:
                result = await run_cave_public_fishing(identity_id, url)
            elif normalized_action in {"stargazer", "sect_farm", "star_farm"}:
                result = await run_cave_public_stargazer(identity_id, url)
            elif normalized_action in {"tree", "spirit_tree", "luoyun_tree"}:
                result = await run_cave_public_tree(
                    identity_id,
                    url,
                    score_profiles=get_tree_miniapp_score_config(identity_id),
                )
            elif normalized_action in {"yuanying", "yuan_ying", "yuanying_launch"}:
                result = await run_cave_public_yuanying(identity_id, url)
            elif normalized_action in {"deep_status", "deep_start", "deep_settle", "deep_force"}:
                deep_action = normalized_action.replace("deep_", "", 1)
                result = await run_cave_public_deep_retreat_action(identity_id, url, deep_action)
            else:
                return False, "洞府公共入口动作无效", {}
            message = str(result.get("message") or "")
            attempted.append({"index": index, "ok": bool(result.get("ok")), "message": message[:120]})
            if result.get("ok") or not _is_cave_public_entry_health_failure(message):
                extra = dict(result.get("extra") or {})
                extra["entry_index"] = index
                extra["entry_attempts"] = attempted
                return bool(result.get("ok")), message, extra
            if index + 1 < len(candidate_urls):
                console_log(
                    f"🧩 洞府公共入口候选失效，切换备用：{get_identity_display_name(identity_id)}｜{normalized_action}｜{message[:120]}",
                    scope="identity",
                    send_as_id=identity_id,
                    limit=220,
                )
        extra = dict(result.get("extra") or {})
        extra["entry_index"] = max(0, len(candidate_urls) - 1)
        extra["entry_attempts"] = attempted
        return bool(result.get("ok")), str(result.get("message") or ""), extra


def _is_cave_public_entry_health_failure(message):
    text = str(message or "")
    return any(keyword in text for keyword in (
        "入口 URL 无效",
        "身份读取失败",
        "入口读取失败",
        "会话初始化失败",
        "WebView",
        "tgWebAppData",
        "initial_start_failed",
        "selected_start_failed",
        "没有可用的官方游戏 Bot",
        "UsernameInvalidError",
        "UsernameNotOccupiedError",
        "BotInvalidError",
        "动态入口获取失败",
        "未返回可用 URL",
        "未开放落云灵树入口",
    ))


def _normalize_cave_public_batch_delay(value):
    try:
        delay = float(value)
    except (TypeError, ValueError, OverflowError):
        delay = 20.0
    return max(10.0, min(120.0, delay))


def _normalize_cave_public_batch_actions(payload):
    payload = dict(payload or {})
    raw_actions = payload.get("actions")
    if raw_actions in (None, "", []):
        raw_actions = _cave_public_actions_from_config()
    if isinstance(raw_actions, str):
        raw_actions = [item for item in re.split(r"[\s,，]+", raw_actions) if item]
    aliases = {
        "deep": "deep_status",
        "deep_retreat": "deep_status",
        "cave_treasure": "treasure",
        "hunt": "treasure",
        "tianji_trial": "trial",
        "fish": "fishing",
        "sect_farm": "stargazer",
        "star_farm": "stargazer",
        "yuan_ying": "yuanying",
        "yuanying_launch": "yuanying",
    }
    allowed = {"small_world", "deep_status", "treasure", "trial", "fishing", "stargazer", "yuanying"}
    actions = []
    seen = set()
    for raw in raw_actions or ():
        action = aliases.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())
        if action in allowed and action not in seen:
            actions.append(action)
            seen.add(action)
    return actions


def _cave_public_batch_identity_ids_for_action(action, all_identity_ids):
    # WebApp initData belongs to the physical account, while the dwelling panel
    # can select channel players by playerId. Only account-shared actions are
    # deduplicated to the physical account below.
    available_ids = []
    for raw_identity_id in all_identity_ids:
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if identity_id > 0 and is_cave_public_identity_available(identity_id):
            available_ids.append(identity_id)

    available_set = set(available_ids)
    normalized_action = str(action or "").strip().lower()
    if normalized_action == "fishing":
        selected_ids = set(normalize_miniapp_auto_config().get("cave_public_fishing_identity_ids") or [])
        return [identity_id for identity_id in available_ids if identity_id in selected_ids]
    if normalized_action in {"trial", "stargazer", "yuanying", "deep_status", "deep_start", "deep_settle", "deep_force"}:
        return available_ids
    result = []
    seen_accounts = set()
    for identity_id in available_ids:
        try:
            account_id = int(get_identity_account(identity_id) or 0)
        except (TypeError, ValueError, OverflowError):
            account_id = 0
        account_id = account_id if account_id > 0 else identity_id
        if account_id in seen_accounts:
            continue
        seen_accounts.add(account_id)
        # WebApp initData belongs to the physical Telegram account.  Never
        # fall back to a channel/send-as identity when its account identity is
        # absent or disabled: runtime will correctly reject that alias, and a
        # queued HTTP attempt would only add noise.
        if account_id != identity_id and account_id not in available_set:
            continue
        canonical_identity_id = account_id if account_id in available_set else identity_id
        result.append(canonical_identity_id)
    return result


def _build_cave_public_batch_steps(identity_ids, actions):
    steps = []
    for action in actions:
        for identity_id in _cave_public_batch_identity_ids_for_action(action, identity_ids):
            steps.append((int(identity_id), action))
    return steps


def _set_cave_public_batch_state(**updates):
    _cave_public_batch_state.update(updates)


def _merge_cave_public_batch_counts(target, source):
    for raw_name, raw_amount in dict(source or {}).items():
        name = str(raw_name or "").strip()
        try:
            amount = int(raw_amount or 0)
        except (TypeError, ValueError, OverflowError):
            amount = 0
        if name and amount > 0:
            target[name] = int(target.get(name, 0) or 0) + amount


def _record_cave_public_batch_outcome(summary, action, ok, extra):
    action = str(action or "").strip().lower() or "unknown"
    row = summary.setdefault(action, {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "settled_count": 0,
        "gains": {},
        "rewards": {},
        "action_counts": {},
    })
    row["attempted"] += 1
    row["succeeded" if ok else "failed"] += 1
    extra = dict(extra or {})
    try:
        row["settled_count"] += max(0, int(extra.get("settled_count") or 0))
    except (TypeError, ValueError, OverflowError):
        pass
    _merge_cave_public_batch_counts(row["gains"], extra.get("gains"))
    _merge_cave_public_batch_counts(row["rewards"], extra.get("rewards"))
    _merge_cave_public_batch_counts(row["action_counts"], extra.get("action_counts"))


def _format_cave_public_batch_outcomes(summary):
    labels = {
        "trial": "天机试炼",
        "tianji_trial": "天机试炼",
        "treasure": "洞府寻宝",
        "hunt": "洞府寻宝",
        "cave_treasure": "洞府寻宝",
        "stargazer": "观星台",
        "sect_farm": "观星台",
        "star_farm": "观星台",
    }
    lines = []
    for action, row in dict(summary or {}).items():
        material = bool(row.get("gains") or row.get("rewards"))
        settled_count = int(row.get("settled_count") or 0)
        if action not in labels or (not material and settled_count <= 0):
            continue
        parts = [f"{int(row.get('succeeded') or 0)}/{int(row.get('attempted') or 0)} 成功"]
        if settled_count > 0:
            unit = "次" if action in {"trial", "tianji_trial"} else "局"
            parts.append(f"结算 {settled_count}{unit}")
        gains = dict(row.get("gains") or {})
        rewards = dict(row.get("rewards") or {})
        if gains:
            parts.append("收益:" + "、".join(f"{name}+{amount}" for name, amount in sorted(gains.items())))
        if rewards:
            parts.append("奖励:" + "、".join(f"{name}x{amount}" for name, amount in sorted(rewards.items())))
        lines.append(f"- {labels[action]}：" + "｜".join(parts))
    return lines


async def ui_set_cave_public_config(payload=None):
    payload = dict(payload or {})
    config = normalize_miniapp_auto_config()
    mapping = {
        "small_world_enabled": "cave_public_small_world_enabled",
        "deep_status_enabled": "cave_public_deep_status_enabled",
        "treasure_enabled": "cave_public_treasure_enabled",
        "trial_enabled": "cave_public_trial_enabled",
        "fishing_enabled": "cave_public_fishing_enabled",
        "stargazer_enabled": "cave_public_stargazer_enabled",
        "yuanying_enabled": "cave_public_yuanying_enabled",
    }
    for payload_key, config_key in mapping.items():
        if payload_key in payload:
            config[config_key] = _coerce_ui_bool(payload.get(payload_key))
    if "fishing_identity_ids" in payload:
        raw_ids = payload.get("fishing_identity_ids") or []
        if not isinstance(raw_ids, (list, tuple, set)):
            raw_ids = []
        config["cave_public_fishing_identity_ids"] = sorted({
            int(identity_id)
            for identity_id in raw_ids
            if str(identity_id or "").strip().lstrip("-").isdigit() and int(identity_id) > 0
        })
    if "delay_sec" in payload:
        config["cave_public_delay_sec"] = int(_normalize_cave_public_batch_delay(payload.get("delay_sec")))
    if "public_entry_url" in payload or "public_entry_urls" in payload:
        public_urls = []
        public_urls.extend(_normalize_cave_public_entry_urls_value(payload.get("public_entry_url")))
        public_urls.extend(_normalize_cave_public_entry_urls_value(payload.get("public_entry_urls")))
        if public_urls:
            valid_urls = []
            for public_entry_url in public_urls:
                _token, _webview_url, error = _parse_public_cave_entry_url(public_entry_url)
                if error:
                    return False, f"洞府公共入口 URL 无效：{error}"
                valid_urls.append(public_entry_url)
            config["cave_public_entry_urls"] = valid_urls
            config["cave_public_entry_url"] = valid_urls[0]
    set_miniapp_auto_config(config)
    save_state()
    actions = _cave_public_actions_from_config(config)
    action_text = "、".join(actions) if actions else "无"
    return True, f"已保存洞府公共入口独立开关：{action_text}｜间隔 {config['cave_public_delay_sec']}s"


async def ui_set_world_boss_miniapp_config(payload=None):
    payload = dict(payload or {})
    config = normalize_miniapp_auto_config()
    if "enabled" in payload:
        config["world_boss_auto_enabled"] = _coerce_ui_bool(payload.get("enabled"))
    if "account_limit" in payload:
        try:
            config["world_boss_auto_account_limit"] = max(1, min(4, int(payload.get("account_limit") or 1)))
        except (TypeError, ValueError, OverflowError):
            return False, "世界 Boss 账户上限必须为 1-4"
    if "account_gap_sec" in payload:
        try:
            config["world_boss_auto_account_gap_sec"] = max(1, min(15, float(payload.get("account_gap_sec"))))
        except (TypeError, ValueError, OverflowError):
            return False, "世界 Boss 账户间隔必须为 1-15 秒"
    if "excluded_identity_ids" in payload:
        raw_ids = payload.get("excluded_identity_ids") or []
        if isinstance(raw_ids, str):
            raw_ids = re.split(r"[,，\s]+", raw_ids.strip()) if raw_ids.strip() else []
        if not isinstance(raw_ids, (list, tuple, set)):
            return False, "世界 Boss 排除身份格式无效"
        config["world_boss_auto_excluded_identity_ids"] = sorted({
            int(identity_id)
            for identity_id in raw_ids
            if str(identity_id or "").strip().lstrip("-").isdigit() and int(identity_id) > 0
        })
    set_miniapp_auto_config(config)
    save_state()
    status = "开启" if config["world_boss_auto_enabled"] else "关闭"
    return True, (
        f"世界 Boss MiniApp 自动化已{status}｜最多 {config['world_boss_auto_account_limit']} 个登录账户"
        "｜账户并行、账户内部串行"
        f"｜排除身份 {len(config['world_boss_auto_excluded_identity_ids'])} 个"
    )


async def _run_cave_public_entry_batch(batch_id, public_entry_url, identity_ids, actions, delay_sec):
    steps = _build_cave_public_batch_steps(identity_ids, actions)
    total = len(steps)
    _set_cave_public_batch_state(
        running=True,
        batch_id=batch_id,
        started_at=time.time(),
        finished_at=0,
        total=total,
        completed=0,
        succeeded=0,
        failed=0,
        current="",
        last_result="",
        delay_sec=delay_sec,
    )
    await send_audit_log(
        f"🧩 洞府公共入口串行批次启动：batch={batch_id}｜动作={','.join(actions)}｜步骤 {total}｜间隔 {int(delay_sec)}s。",
        scope="global",
        priority="normal",
        limit=360,
    )
    if total <= 0:
        _set_cave_public_batch_state(running=False, finished_at=time.time(), last_result="无可执行步骤")
        await send_audit_log("🧩 洞府公共入口串行批次结束：无可执行步骤。", scope="global", priority="low", limit=220)
        return

    try:
        succeeded = 0
        failed = 0
        outcomes = {}
        for index, (identity_id, action) in enumerate(steps, start=1):
            display = get_identity_display_name(identity_id)
            current = f"{index}/{total} {display} {action}"
            _set_cave_public_batch_state(current=current)
            ok, message, extra = await ui_run_cave_public_entry(identity_id, action, public_entry_url)
            _record_cave_public_batch_outcome(outcomes, action, ok, extra)
            result_text = f"{display} {action}: {'ok' if ok else 'fail'} {message}"
            if ok:
                succeeded += 1
            else:
                failed += 1
            _set_cave_public_batch_state(
                completed=index,
                succeeded=succeeded,
                failed=failed,
                last_result=result_text,
            )
            if (index % 5 == 0) or (not ok) or index == total:
                await send_audit_log(
                    f"🧩 洞府公共入口串行进度：batch={batch_id}｜{index}/{total}｜最近：{result_text}",
                    scope="global",
                    priority="low" if ok else "normal",
                    limit=420,
                )
            if index < total:
                await asyncio.sleep(delay_sec)
    except Exception as exc:
        message = f"批次异常：{type(exc).__name__}: {exc}"
        _set_cave_public_batch_state(running=False, finished_at=time.time(), last_result=message)
        await send_audit_log(
            f"🧩 洞府公共入口串行批次中止：batch={batch_id}｜{message}",
            scope="global",
            priority="normal",
            limit=420,
        )
        return

    _set_cave_public_batch_state(running=False, finished_at=time.time(), current="")
    await send_audit_log(
        f"🧩 洞府公共入口串行批次完成：batch={batch_id}｜完成 {total}/{total}｜成功 {succeeded}｜失败 {failed}。",
        scope="global",
        priority="normal",
        limit=260,
    )
    outcome_lines = _format_cave_public_batch_outcomes(outcomes)
    if outcome_lines:
        await send_audit_log(
            "🧩 洞府公共入口成果汇总\n" + "\n".join(outcome_lines),
            scope="global",
            priority="normal",
            limit=1200,
        )


async def ui_start_cave_public_entry_batch(payload=None):
    payload = dict(payload or {})
    if _cave_public_batch_state.get("running"):
        return False, "已有洞府公共入口串行批次正在运行", dict(_cave_public_batch_state)
    if _cave_public_background_state.get("running") or _cave_public_ui_run_lock.locked():
        return False, "洞府公共入口后台动作正在运行，请等待完成", dict(_cave_public_background_state)
    payload_urls = []
    payload_urls.extend(_normalize_cave_public_entry_urls_value(payload.get("public_entry_url")))
    payload_urls.extend(_normalize_cave_public_entry_urls_value(payload.get("public_entry_urls")))
    public_entry_url = "\n".join(payload_urls)
    configured_urls = list(normalize_miniapp_auto_config().get("cave_public_entry_urls") or [])
    if not public_entry_url and not configured_urls:
        return False, "缺少洞府公共入口 URL", {}
    identity_ids = _normalize_cave_public_batch_identity_ids(payload)
    if not identity_ids:
        return False, "没有可执行的启用身份", {}
    actions = _normalize_cave_public_batch_actions(payload)
    if not actions:
        return False, "洞府公共入口独立开关未开启任何动作", {}
    delay_sec = _normalize_cave_public_batch_delay(payload.get("delay_sec"))
    steps = _build_cave_public_batch_steps(identity_ids, actions)
    if not steps:
        return False, "没有匹配本批动作的启用身份", {}
    batch_id = f"cave_public_{int(time.time())}_{len(steps)}"
    account_identity_ids = _cave_public_batch_identity_ids_for_action("", identity_ids)
    # Claim the batch before creating the background task. Without this, two quick UI
    # clicks can both observe `running=False` and start concurrent HTTP batches.
    _set_cave_public_batch_state(
        running=True,
        batch_id=batch_id,
        started_at=time.time(),
        finished_at=0,
        total=len(steps),
        completed=0,
        succeeded=0,
        failed=0,
        current="等待启动",
        last_result="",
        delay_sec=delay_sec,
    )
    try:
        _fire_and_forget(_run_cave_public_entry_batch(batch_id, public_entry_url, identity_ids, actions, delay_sec))
    except Exception as exc:
        message = f"创建洞府公共入口批次失败：{type(exc).__name__}: {exc}"
        _set_cave_public_batch_state(running=False, finished_at=time.time(), current="", last_result=message)
        return False, message, dict(_cave_public_batch_state)
    return True, f"已启动洞府公共入口串行批次：{len(account_identity_ids)} 个登录账号｜{len(steps)} 步，间隔 {int(delay_sec)}s", {
        "batch_id": batch_id,
        "count": len(steps),
        "account_count": len(account_identity_ids),
        "actions": actions,
        "delay_sec": delay_sec,
    }


def _normalize_trial_batch_identity_ids(payload):
    payload = dict(payload or {})
    raw_ids = payload.get("send_as_ids")
    if raw_ids in (None, "", []):
        raw_ids = get_identity_ids()
    if isinstance(raw_ids, str):
        raw_ids = [item for item in re.split(r"[\s,，]+", raw_ids) if item]
    result = []
    seen = set()
    for raw_id in raw_ids or ():
        try:
            identity_id = int(raw_id or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if identity_id <= 0 or identity_id in seen:
            continue
        if identity_id not in get_identity_ids():
            continue
        if not get_identity_enabled(identity_id):
            continue
        seen.add(identity_id)
        result.append(identity_id)
    return result


def _normalize_cave_public_batch_identity_ids(payload):
    payload = dict(payload or {})
    raw_ids = payload.get("send_as_ids")
    if raw_ids in (None, "", []):
        raw_ids = get_identity_ids()
    if isinstance(raw_ids, str):
        raw_ids = [item for item in re.split(r"[\s,，]+", raw_ids) if item]
    result = []
    seen = set()
    registered_ids = set(get_identity_ids())
    for raw_id in raw_ids or ():
        try:
            identity_id = int(raw_id or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if identity_id <= 0 or identity_id in seen or identity_id not in registered_ids:
            continue
        if not is_cave_public_identity_available(identity_id):
            continue
        seen.add(identity_id)
        result.append(identity_id)
    return result


async def _run_trial_miniapp_batch(batch_id, identity_ids):
    sent = 0
    for identity_id in identity_ids:
        authorize_trial_miniapp_manual_run(identity_id, batch_id=batch_id)
        op_id = f"miniapp_trial_batch:{batch_id}:{identity_id}"
        msg = await send_game_command(
            MINIAPP_MANUAL_RUN_COMMANDS["trial"],
            track=False,
            send_as_id=identity_id,
            priority="normal",
            max_retry=0,
            source_module="MiniApp批量",
            op_id=op_id,
            chain_id="miniapp_trial_batch",
            delete_policy="keep",
            queue_timeout=120,
        )
        if msg:
            msg_id = int(getattr(msg, "id", 0) or 0)
            sent += 1
            note_trial_batch_send_result(batch_id, identity_id, ok=True, msg_id=msg_id)
        else:
            revoke_trial_miniapp_manual_run(identity_id)
            note_trial_batch_send_result(batch_id, identity_id, ok=False, error="发送被保护拦截")
        await maybe_finalize_trial_batch_run(batch_id)
        await asyncio.sleep(1.0)
    await send_audit_log(
        f"🧪 天机试炼批量已排队完成：{sent}/{len(identity_ids)} 条入口命令已发送，等待 MiniApp 回包汇总。",
        scope="global",
        priority="low",
        limit=260,
    )
    await maybe_finalize_trial_batch_run(batch_id)


async def ui_start_trial_miniapp_batch_run(payload=None):
    identity_ids = _normalize_trial_batch_identity_ids(payload)
    if not identity_ids:
        return False, "没有可执行的启用身份", {}
    batch_id = start_trial_miniapp_batch_run(identity_ids)
    if not batch_id:
        return False, "批量任务创建失败", {}
    _fire_and_forget(_run_trial_miniapp_batch(batch_id, identity_ids))
    await send_audit_log(
        f"🧪 天机试炼批量启动：{len(identity_ids)} 个身份，完成后合并通报。batch={batch_id}",
        scope="global",
        priority="normal",
        limit=320,
    )
    return True, f"已启动天机试炼批量：{len(identity_ids)} 个身份，完成后合并通报", {
        "batch_id": batch_id,
        "count": len(identity_ids),
    }


def _cave_public_background_action_due(action, identity_id, now):
    action = str(action or "").strip().lower()
    if int(identity_id or 0) not in get_identity_ids():
        return False
    with use_identity(identity_id):
        if action == "small_world":
            return bool(state.get("small_world_enabled")) and float(state.get("next_small_world_time", 0) or 0) <= now
        if action in {"deep_status", "deep_start", "deep_settle", "deep_force"}:
            return bool(state.get("deep_retreat_enabled")) and float(state.get("next_deep_retreat_time", 0) or 0) <= now
        if action == "treasure":
            daily_done_key = ("treasure", get_day_key(now), int(identity_id))
            if daily_done_key in _cave_public_background_daily_done:
                return False
            record = dict(get_miniapp_state_records().get(f"{int(identity_id)}:cave_treasure") or {})
            record_state = record.get("state") if isinstance(record.get("state"), dict) else {}
            updated_at = float(record.get("updated_at", 0) or 0)
            if updated_at > 0 and get_day_key(updated_at) == get_day_key(now):
                games_used = int(record_state.get("games_used", 0) or 0)
                games_limit = int(record_state.get("games_limit", 0) or 0)
                if games_limit > 0 and games_used >= games_limit:
                    return False
            return True
        if action == "fishing":
            if int(identity_id) not in set(normalize_miniapp_auto_config().get("cave_public_fishing_identity_ids") or []):
                return False
            if float(state.get("next_fishing_time", 0) or 0) > now:
                return False
            day_key = get_day_key(now)
            daily_done_key = ("fishing", day_key, int(identity_id))
            if daily_done_key in _cave_public_background_daily_done:
                return False
            if (
                str(state.get("fishing_daily_day") or "") == day_key
                and "daily_limit" in str(state.get("fishing_last_result") or "").lower()
            ):
                return False
            if str(state.get("fishing_daily_day") or "") != day_key:
                return True
            limit = max(1, int(state.get("fishing_daily_limit", 5) or 5))
            return int(state.get("fishing_daily_count", 0) or 0) < limit
        if action == "stargazer":
            if not state.get("stargazer_enabled"):
                return False
            next_time = float(state.get("next_stargazer_panel_time", 0) or 0)
            followup_time = float(state.get("stargazer_followup_due_at", 0) or 0)
            due_times = [item for item in (next_time, followup_time) if item > 0]
            return not due_times or min(due_times) <= now
        if action == "yuanying":
            if not state.get("yuanying_enabled"):
                return False
            next_time = float(state.get("next_yuanying_time", 0) or 0)
            phase = str(state.get("yuanying_phase") or "idle")
            return next_time <= now and phase in {
                "idle",
                "running",
                "summary_due",
                "observing_summary",
                "waiting_summary",
                "post_summary_wait",
            }
    return False


def _cave_public_background_deep_action(identity_id, now):
    with use_identity(identity_id):
        phase = str(state.get("deep_retreat_phase") or "idle")
    if phase in {"running", "summary_due", "observing_summary", "waiting_summary"}:
        return "deep_settle"
    if phase in {"idle", "post_summary_wait"}:
        return "deep_start"
    return "deep_status"


def _cave_public_background_candidate_sort_key(action, identity_id, now):
    action = str(action or "").strip().lower()
    priority = {
        "yuanying": 0,
        "deep_status": 1,
        "deep_settle": 1,
        "deep_start": 1,
        "deep_force": 1,
        "small_world": 2,
        "fishing": 3,
        "stargazer": 4,
        "treasure": 5,
    }.get(action, 9)
    due_at = 0.0
    with use_identity(identity_id):
        if action == "yuanying":
            due_at = float(state.get("next_yuanying_time", 0) or 0)
        elif action in {"deep_status", "deep_start", "deep_settle", "deep_force"}:
            due_at = float(state.get("next_deep_retreat_time", 0) or 0)
        elif action == "small_world":
            due_at = float(state.get("next_small_world_time", 0) or 0)
        elif action == "fishing":
            due_at = float(state.get("next_fishing_time", 0) or 0)
        elif action == "stargazer":
            due_times = [
                float(item)
                for item in (
                    state.get("next_stargazer_panel_time", 0),
                    state.get("stargazer_followup_due_at", 0),
                )
                if float(item or 0) > 0
            ]
            due_at = min(due_times) if due_times else float(now)
    if due_at <= 0:
        due_at = float(now)
    return priority, due_at, int(identity_id)


async def _execute_cave_public_background_action(identity_id, action, delay_sec):
    ok = False
    message = ""
    extra = {}
    try:
        ok, message, extra = await ui_run_cave_public_entry(identity_id, action, "")
        if action in {"treasure", "fishing"} and isinstance(extra, dict) and extra.get("daily_exhausted"):
            _cave_public_background_daily_done.add((action, get_day_key(time.time()), int(identity_id)))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = f"{type(exc).__name__}: {str(exc)[:180]}"
    finally:
        finished_at = time.time()
        retry_action = "deep_status" if action in {"deep_status", "deep_start", "deep_settle", "deep_force"} else action
        retry_sec = 60 if ok else 30 * 60
        if action in {"deep_status", "deep_settle"} and not ok:
            retry_sec = 30 * 60
        _cave_public_background_retry_at[(retry_action, int(identity_id))] = finished_at + retry_sec
        _cave_public_background_state.update({
            "running": False,
            "next_run_at": finished_at + delay_sec,
            "last_action": f"{identity_id}:{action}",
            "last_result": str(message or "")[:240],
        })
    console_log(
        f"🧭 洞府公共入口后台：{get_identity_display_name(identity_id)}｜{action}｜"
        f"{'成功' if ok else '失败'}｜{str(message or '无详情')[:180]}",
        scope="identity",
        send_as_id=identity_id,
        limit=260,
    )


async def _run_cave_public_background_scheduler(now, config):
    now = float(now or time.time())
    public_entry_urls = _cave_public_entry_urls_from_config(config)
    if not public_entry_urls:
        return {"started": False, "reason": "public_entry_url_missing"}
    if _cave_public_batch_state.get("running") or _cave_public_background_state.get("running") or _cave_public_ui_run_lock.locked():
        return {"started": False, "reason": "cave_public_busy"}
    if now < float(_cave_public_background_state.get("next_run_at", 0) or 0):
        return {"started": False, "reason": "background_throttled"}

    action_flags = (
        ("yuanying", "cave_public_yuanying_enabled"),
        ("deep_status", "cave_public_deep_status_enabled"),
        ("small_world", "cave_public_small_world_enabled"),
        ("fishing", "cave_public_fishing_enabled"),
        ("stargazer", "cave_public_stargazer_enabled"),
        ("treasure", "cave_public_treasure_enabled"),
    )
    enabled_action_flags = [(action, flag) for action, flag in action_flags if config.get(flag)]
    if not enabled_action_flags:
        return {"started": False, "reason": "background_disabled"}
    identity_ids = _normalize_cave_public_batch_identity_ids({})
    candidates = []
    for action, flag in enabled_action_flags:
        for identity_id in _cave_public_batch_identity_ids_for_action(action, identity_ids):
            retry_key = (action, int(identity_id))
            if now < float(_cave_public_background_retry_at.get(retry_key, 0) or 0):
                continue
            resolved_action = _cave_public_background_deep_action(identity_id, now) if action == "deep_status" else action
            if _cave_public_background_action_due(resolved_action, identity_id, now):
                candidates.append((int(identity_id), resolved_action))
    if not candidates:
        _cave_public_background_state["next_run_at"] = now + 60
        return {"started": False, "reason": "no_due_public_action"}

    candidates.sort(key=lambda item: _cave_public_background_candidate_sort_key(item[1], item[0], now))
    identity_id, action = candidates[0]
    _cave_public_background_state["cursor"] = 0
    delay_sec = _normalize_cave_public_batch_delay(config.get("cave_public_delay_sec"))
    _cave_public_background_state.update({
        "running": True,
        "next_run_at": now + delay_sec,
        "last_action": f"{identity_id}:{action}",
        "last_result": "执行中",
    })
    try:
        _fire_and_forget(_execute_cave_public_background_action(identity_id, action, delay_sec))
    except Exception:
        _cave_public_background_state["running"] = False
        raise
    return {
        "started": True,
        "kind": "background",
        "identity_id": identity_id,
        "action": action,
        "queued": True,
    }


def _tree_daily_state_for_identity(identity_id):
    snapshot = get_miniapp_state_snapshot(send_as_id=identity_id, game_key="tree")
    rows = list(snapshot.get("rows") or ())
    if not rows:
        return {}
    row = rows[0]
    result = dict(row.get("state") or {})
    result["_record_updated_at"] = float(row.get("updated_at", 0) or 0)
    result["_record_source_id"] = str(row.get("source_id") or "")
    return result


async def _mark_tree_daily_entry_unknown(identity_id, day_key, now, *, op_id="", command_msg_id=0):
    identity_id = int(identity_id or 0)
    day_key = str(day_key or get_day_key(now))
    op_id = str(op_id or "").strip()
    if op_id:
        cancel_tree_miniapp_daily_run(op_id, reason="入口命令无回包", now=now)
    if identity_id <= 0:
        return {"started": False, "reason": "tree_entry_timeout", "identity_id": 0}
    record_miniapp_state(
        identity_id,
        "tree",
        {
            "kind": "daily",
            "day_key": day_key,
            "phase": "unknown",
            "completed_today": False,
            "command_msg_id": int(command_msg_id or 0),
            "error": "入口命令无回包",
        },
        source="tree_daily_scheduler",
        source_id=op_id or f"tree_daily:{day_key}:{identity_id}",
        now=now,
        outputs=("daily_counter", "score_policy", "rewards"),
        replaces_commands=(".灵树",),
    )
    await send_audit_log(
        "🌳 灵树 MiniApp 入口 10 分钟无回包，已标记未知并停止今日补发。",
        scope="identity",
        send_as_id=identity_id,
        priority="normal",
        limit=220,
    )
    return {"started": False, "reason": "tree_entry_timeout", "identity_id": identity_id}


async def _run_tree_public_daily_worker(identity_id, entry_urls, *, day_key, op_id, score_profiles):
    final_result = {}
    try:
        for index, url in enumerate(entry_urls):
            final_result = await run_cave_public_tree(
                identity_id,
                url,
                day_key=day_key,
                op_id=op_id,
                score_profiles=score_profiles,
            )
            extra = dict(final_result.get("extra") or {})
            if final_result.get("ok") or extra.get("result"):
                return final_result
            if index + 1 >= len(entry_urls) or not _is_cave_public_entry_health_failure(final_result.get("message")):
                break
    except Exception as exc:
        final_result = {"ok": False, "message": f"{type(exc).__name__}: {exc}", "extra": {}}

    error = str(final_result.get("message") or "洞府落云灵树入口执行失败")
    record_miniapp_state(
        identity_id,
        "tree",
        {
            "kind": "daily",
            "day_key": day_key,
            "phase": "blocked",
            "completed_today": False,
            "command_msg_id": 0,
            "error": error,
        },
        source="tree_daily_scheduler",
        source_id=op_id,
        now=time.time(),
        outputs=("daily_counter", "score_policy", "rewards"),
        replaces_commands=(".灵树",),
    )
    await send_audit_log(
        f"🌳 洞府落云灵树未执行：{error}",
        scope="identity",
        send_as_id=identity_id,
        priority="normal",
        limit=320,
    )
    return final_result


async def _run_tree_miniapp_daily_scheduler(now, config):
    if not get_global_enabled():
        return {"started": False, "reason": "global_disabled"}
    enabled_ids = list(config.get("tree_daily_enabled_identity_ids") or ())
    if not enabled_ids:
        return {"started": False, "reason": "tree_disabled"}
    coordinator = get_tree_miniapp_coordinator_snapshot()
    coordinator_phase = str(coordinator.get("phase") or "")
    if coordinator_phase == "entry_pending":
        started_at = float(coordinator.get("started_at", 0) or 0)
        if started_at > 0 and float(now) - started_at >= TREE_MINIAPP_ENTRY_PENDING_TIMEOUT_SEC:
            identity_id = int(coordinator.get("identity_id", 0) or 0)
            op_id = str(coordinator.get("op_id") or "").strip()
            day_key = str(coordinator.get("day_key") or get_day_key(now))
            return await _mark_tree_daily_entry_unknown(
                identity_id,
                day_key,
                now,
                op_id=op_id,
                command_msg_id=int(coordinator.get("command_msg_id", 0) or 0),
            )
    if coordinator_phase in {"entry_pending", "running"}:
        return {"started": False, "reason": "tree_busy"}
    day_key = get_day_key(now)
    for identity_id in enabled_ids:
        eligible, reason = check_tree_miniapp_eligibility(identity_id, enabled=True)
        if not eligible:
            continue
        daily_state = _tree_daily_state_for_identity(identity_id)
        if (
            daily_state.get("kind") == "daily"
            and daily_state.get("day_key") == day_key
            and str(daily_state.get("phase") or "") == "running"
            and coordinator_phase not in {"entry_pending", "running"}
        ):
            updated_at = float(daily_state.get("_record_updated_at", 0) or 0)
            if updated_at > 0 and float(now) - updated_at >= TREE_MINIAPP_ENTRY_PENDING_TIMEOUT_SEC:
                record_miniapp_state(
                    identity_id,
                    "tree",
                    {
                        "kind": "daily",
                        "day_key": day_key,
                        "phase": "unknown",
                        "completed_today": False,
                        "command_msg_id": 0,
                        "error": "公共入口任务中断，结果未知",
                    },
                    source="tree_daily_scheduler",
                    source_id=str(daily_state.get("_record_source_id") or f"tree_daily:{day_key}:{identity_id}"),
                    now=now,
                    outputs=("daily_counter", "score_policy", "rewards"),
                    replaces_commands=(".灵树",),
                )
                await send_audit_log(
                    "🌳 灵树 MiniApp 公共入口任务中断，已标记未知并停止今日补发。",
                    scope="identity",
                    send_as_id=identity_id,
                    priority="normal",
                    limit=240,
                )
                return {"started": False, "reason": "tree_run_interrupted", "identity_id": identity_id}
        if (
            daily_state.get("kind") == "daily"
            and daily_state.get("day_key") == day_key
            and str(daily_state.get("phase") or "") == "entry_pending"
            and coordinator_phase not in {"entry_pending", "running"}
        ):
            updated_at = float(daily_state.get("_record_updated_at", 0) or 0)
            if updated_at > 0 and float(now) - updated_at >= TREE_MINIAPP_ENTRY_PENDING_TIMEOUT_SEC:
                return await _mark_tree_daily_entry_unknown(
                    identity_id,
                    day_key,
                    now,
                    op_id=str(daily_state.get("_record_source_id") or ""),
                    command_msg_id=int(daily_state.get("command_msg_id", 0) or 0),
                )
        tree_phase = str(daily_state.get("phase") or "")
        legacy_entry_unknown = (
            tree_phase == "unknown"
            and str(daily_state.get("error") or "").strip() == "入口命令无回包"
        )
        if (
            daily_state.get("kind") == "daily"
            and daily_state.get("day_key") == day_key
            and tree_phase in {"entry_pending", "running", "completed", "blocked", "unknown"}
            and not legacy_entry_unknown
        ):
            continue
        entry_urls = list(config.get("cave_public_entry_urls") or ())
        if not entry_urls:
            return {"started": False, "reason": "tree_public_entry_missing", "identity_id": identity_id}
        op_id = f"tree_daily:{day_key}:{int(identity_id)}"
        record_miniapp_state(
            identity_id,
            "tree",
            {
                "kind": "daily",
                "day_key": day_key,
                "phase": "running",
                "completed_today": False,
                "command_msg_id": 0,
            },
            source="tree_daily_scheduler",
            source_id=op_id,
            now=now,
            outputs=("daily_counter", "score_policy", "rewards"),
            replaces_commands=(".灵树",),
        )
        _fire_and_forget(_run_tree_public_daily_worker(
            identity_id,
            entry_urls,
            day_key=day_key,
            op_id=op_id,
            score_profiles=get_tree_miniapp_score_config(identity_id),
        ))
        return {"started": True, "identity_id": identity_id, "op_id": op_id, "source": "cave_public"}
    return {"started": False, "reason": "tree_done_or_ineligible"}


async def run_miniapp_daily_scheduler(now):
    raw_config = normalize_miniapp_auto_config()
    config = get_miniapp_auto_config_snapshot(now)
    if not get_global_enabled() and get_global_pause_source() != MAINTENANCE_PAUSE_SOURCE:
        return {"started": False, "reason": "global_disabled"}

    tree_daily = await _run_tree_miniapp_daily_scheduler(now, raw_config)
    if tree_daily.get("started"):
        return tree_daily

    active_wave = dict(config.get("trial_daily_active_wave") or {})
    wave_key = str(active_wave.get("key") or "").strip()
    wave_label = str(active_wave.get("label") or "").strip() or "批次"
    trial_ready = bool(
        config.get("trial_daily_effective_enabled")
        and raw_config.get("cave_public_trial_enabled")
        and raw_config.get("cave_public_entry_urls")
        and active_wave
        and not active_wave.get("done_today")
    )
    if trial_ready and not _cave_public_batch_state.get("running"):
        identity_ids = _split_trial_daily_identity_ids(_normalize_cave_public_batch_identity_ids({}) or [], wave_key)
        if not identity_ids:
            next_config = normalize_miniapp_auto_config()
            next_config[f"trial_daily_{wave_key}_last_run_day"] = str(config.get("today") or "")
            next_config[f"trial_daily_{wave_key}_last_run_at"] = float(now or time.time())
            next_config[f"trial_daily_{wave_key}_last_result"] = "本批无启用身份"
            set_miniapp_auto_config(next_config)
            save_state()
            return {"started": False, "reason": "no_enabled_identity", "wave": wave_key}
        ok, message, extra = await ui_start_cave_public_entry_batch({
            "send_as_ids": identity_ids,
            "actions": ["trial"],
            "delay_sec": raw_config.get("cave_public_delay_sec"),
        })
        if not ok:
            return {"started": False, "reason": "public_batch_create_failed", "message": message}
        batch_id = str(extra.get("batch_id") or "")
        next_config = normalize_miniapp_auto_config()
        next_config[f"trial_daily_{wave_key}_last_run_day"] = str(config.get("today") or "")
        next_config[f"trial_daily_{wave_key}_last_batch_id"] = batch_id
        next_config[f"trial_daily_{wave_key}_last_run_at"] = float(now or time.time())
        next_config[f"trial_daily_{wave_key}_last_result"] = f"{wave_label}已启动 {len(identity_ids)} 个身份"
        next_config["trial_daily_last_run_day"] = str(config.get("today") or "")
        next_config["trial_daily_last_batch_id"] = batch_id
        next_config["trial_daily_last_run_at"] = float(now or time.time())
        next_config["trial_daily_last_result"] = f"{wave_label}已启动 {len(identity_ids)} 个身份"
        set_miniapp_auto_config(next_config)
        save_state()
        return {"started": True, "batch_id": batch_id, "count": len(identity_ids), "wave": wave_key}

    background = await _run_cave_public_background_scheduler(now, raw_config)
    if background.get("started"):
        return background
    if not config.get("trial_daily_effective_enabled"):
        return {"started": False, "reason": "disabled"}
    if config.get("trial_daily_done_today"):
        return {"started": False, "reason": "done_today"}
    if not active_wave:
        return {"started": False, "reason": "outside_window"}
    if active_wave.get("done_today"):
        return {"started": False, "reason": f"{wave_key or 'wave'}_done_today"}
    if not raw_config.get("cave_public_entry_urls"):
        return {"started": False, "reason": "public_entry_url_missing"}
    return {"started": False, "reason": "cave_public_busy"}


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


def _is_loopback_peer(peer):
    if not peer:
        return False
    host = peer[0] if isinstance(peer, (tuple, list)) and peer else peer
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return str(host).lower() == "localhost"


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

            # 初始化模式只信任本机访问，避免公网首装窗口被抢绑账号。
            _setup_mode = not get_accounts() and not get_identity_ids()
            if _setup_mode and session is None and _is_loopback_peer(peer):
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
            elif path == "/api/miniapp-status":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = query.get("send_as_id", [""])[0]
                    body = _make_json_payload(True, extra={"miniapp": get_miniapp_status_snapshot(send_as_id=send_as_id)})
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
            elif path == "/api/miniapp-capture-summary":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "GET":
                    _write_method_not_allowed(writer)
                else:
                    game_key = normalize_miniapp_game_key(query.get("game_key", [""])[0])
                    if not game_key:
                        _write_json_bad_request(writer, "缺少或非法 game_key 参数", auth_headers)
                    else:
                        day = query.get("day", [""])[0]
                        limit = query.get("limit", ["200"])[0]
                        summary = get_miniapp_capture_summary(game_key, day=day, limit=limit)
                        body = _make_json_payload(True, extra={"capture": summary})
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
            elif path == "/api/replica-query-aggregator-toggle":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = ui_set_replica_query_aggregator_enabled(payload.get("enabled"))
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
            elif path == "/api/hehuan-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_hehuan_config(
                            send_as_id,
                            retry_max_interval_min=payload.get("retry_max_interval_min"),
                        )
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/tianxing-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_tianxing_config(send_as_id, payload.get("config") or {})
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/wanxin-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_wanxin_config(send_as_id, payload.get("config") or {})
                        _write_json_result(writer, ok, message, session_token=(session or {}).get("session_token"), extra_headers=auth_headers)
            elif path == "/api/explore-rift-rebirth-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_explore_rift_rebirth_config(send_as_id, payload)
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
            elif path == "/api/miniapp-entry-probe":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    game_key = payload.get("game_key")
                    if send_as_id in {None, ""} or not game_key:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 game_key 参数", auth_headers)
                    else:
                        ok, message, extra = await ui_send_miniapp_entry_probe(send_as_id, game_key)
                        _write_json_result(
                            writer,
                            ok,
                            message,
                            session_token=(session or {}).get("session_token"),
                            extra_headers=auth_headers,
                            extra=extra,
                            include_snapshot=False,
                        )
            elif path == "/api/phaseful-passive-trigger":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message, extra = await ui_send_phaseful_passive_trigger(send_as_id)
                        _write_json_result(
                            writer,
                            ok,
                            message,
                            session_token=(session or {}).get("session_token"),
                            extra_headers=auth_headers,
                            extra=extra,
                            include_snapshot=False,
                        )
            elif path == "/api/miniapp-manual-run":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    game_key = payload.get("game_key")
                    if send_as_id in {None, ""} or not game_key:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 game_key 参数", auth_headers)
                    else:
                        ok, message, extra = await ui_send_miniapp_manual_run(send_as_id, game_key, payload=payload)
                        _write_json_result(
                            writer,
                            ok,
                            message,
                            session_token=(session or {}).get("session_token"),
                            extra_headers=auth_headers,
                            extra=extra,
                            include_snapshot=False,
                        )
            elif path == "/api/cave-public-entry-run":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    action = payload.get("action")
                    public_entry_url = payload.get("public_entry_url")
                    if send_as_id in {None, ""} or not action:
                        _write_json_bad_request(writer, "缺少 send_as_id 或 action 参数", auth_headers)
                    else:
                        ok, message, extra = await ui_run_cave_public_entry(send_as_id, action, public_entry_url)
                        _write_json_result(
                            writer,
                            ok,
                            message,
                            session_token=(session or {}).get("session_token"),
                            extra_headers=auth_headers,
                            extra=extra,
                            include_snapshot=False,
                        )
            elif path == "/api/cave-public-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = await ui_set_cave_public_config(payload)
                    _write_json_result(
                        writer,
                        ok,
                        message,
                        session_token=(session or {}).get("session_token"),
                        extra_headers=auth_headers,
                        extra={"miniapp": get_miniapp_status_snapshot()},
                        include_snapshot=False,
                    )
            elif path == "/api/world-boss-miniapp-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message = await ui_set_world_boss_miniapp_config(payload)
                    _write_json_result(
                        writer,
                        ok,
                        message,
                        session_token=(session or {}).get("session_token"),
                        extra_headers=auth_headers,
                        extra={"miniapp": get_miniapp_status_snapshot()},
                        include_snapshot=False,
                    )
            elif path == "/api/cave-public-entry-batch-run":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, extra = await ui_start_cave_public_entry_batch(payload)
                    _write_json_result(
                        writer,
                        ok,
                        message,
                        session_token=(session or {}).get("session_token"),
                        extra_headers=auth_headers,
                        extra=extra,
                        include_snapshot=False,
                    )
            elif path == "/api/miniapp-trial-batch-run":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    ok, message, extra = await ui_start_trial_miniapp_batch_run(payload)
                    _write_json_result(
                        writer,
                        ok,
                        message,
                        session_token=(session or {}).get("session_token"),
                        extra_headers=auth_headers,
                        extra=extra,
                        include_snapshot=False,
                    )
            elif path == "/api/miniapp-tree-score-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_tree_miniapp_score_config(send_as_id, payload)
                        _write_json_result(
                            writer,
                            ok,
                            message,
                            session_token=(session or {}).get("session_token"),
                            extra_headers=auth_headers,
                            extra={"miniapp": get_miniapp_status_snapshot(send_as_id=send_as_id)},
                            include_snapshot=False,
                        )
            elif path == "/api/miniapp-tree-auto-config":
                if session is None:
                    _write_json_unauthorized(writer, auth_headers)
                elif method != "POST":
                    _write_method_not_allowed(writer)
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        _write_json_bad_request(writer, "缺少 send_as_id 参数", auth_headers)
                    else:
                        ok, message = await ui_set_tree_miniapp_auto_config(send_as_id, payload)
                        _write_json_result(
                            writer,
                            ok,
                            message,
                            session_token=(session or {}).get("session_token"),
                            extra_headers=auth_headers,
                        )
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
    "ui_set_hehuan_config",
    "ui_set_tianxing_config",
    "ui_set_wanxin_config",
    "ui_set_explore_rift_rebirth_config",
    "ui_set_divination_config",
    "ui_set_stargazer_star_choice",
    "ui_sync_stargazer_total_slots",
    "ui_sync_tianti_status",
    "ui_set_tianti_feature_enabled",
    "get_miniapp_status_snapshot",
    "get_tree_miniapp_score_config",
    "run_miniapp_daily_scheduler",
    "ui_send_miniapp_entry_probe",
    "ui_send_miniapp_manual_run",
    "ui_run_cave_public_entry",
    "ui_set_cave_public_config",
    "ui_start_cave_public_entry_batch",
    "ui_start_trial_miniapp_batch_run",
    "ui_set_tree_miniapp_auto_config",
    "ui_set_tree_miniapp_score_config",
]
