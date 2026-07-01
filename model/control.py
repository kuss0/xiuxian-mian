import asyncio
import html
import json
import math
import re
import random
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .module_manifest import get_module_manifest, is_module_archived
from .config import (
    ADMIN_IDS,
    CMD_CHECKIN,
    CMD_CONCUBINE_DAILY_GREET,
    CMD_CONCUBINE_DREAM,
    CMD_CONCUBINE_FRAGMENT,
    CMD_CONCUBINE_PUZZLE,
    CMD_CONCUBINE_ROMANCE,
    CMD_CONCUBINE_SECT_MARRY,
    CMD_CONCUBINE_STATUS,
    CMD_CONCUBINE_TIANJI,
    CMD_CONCUBINE_HEART,
    CMD_CONCUBINE_VOYAGE,
    CMD_CONCUBINE_VOYAGE_RETURN,
    CMD_CONCUBINE_VOYAGE_STATUS,
    CMD_NORMAL_RETREAT,
    CMD_DEEP_RETREAT_FORCE_EXIT,
    CMD_USE_HEQI_DAN,
    CMD_EXCHANGE_HEQI_DAN_PREFIX,
    CMD_SECT_DONATE_LINGSHI_PREFIX,
    CMD_DEEP_RETREAT,
    CMD_DEEP_RETREAT_QUERY,
    CMD_DIVINATION,
    CMD_DIVINATION_EXCHANGE,
    CMD_EXPLORE_RIFT,
    CMD_REBIRTH_REQUEST,
    CMD_REBIRTH_SELECT_PREFIX,
    CMD_FORMATION_ASSIST,
    CMD_FORMATION_START,
    CMD_GUANXING,
    CMD_GUANXING_SHIFT,
    CMD_HEHUAN_CONTRACT,
    CMD_HEHUAN_DUAL,
    CMD_HEHUAN_ESCAPE,
    CMD_HEHUAN_RETREAT,
    CMD_HEHUAN_SEAL,
    CMD_NANLONG_EXCHANGE_FABAO,
    CMD_NANLONG_EXCHANGE_GONGFA,
    CMD_NANLONG_REJECT,
    CMD_NODE_DEFINE,
    CMD_NODE_SEARCH,
    CMD_PET,
    CMD_PET_WARM,
    CMD_PET_TRIAL,
    CMD_PET_FORMATION,
    CMD_QINGYUANZI_ATTACK,
    CMD_QINGYUANZI_BREAK,
    CMD_QINGYUANZI_GUARD,
    CMD_QINGYUANZI_SUPPRESS,
    CMD_QUIZ_ANSWER,
    CMD_RANCH,
    CMD_SECOND_SOUL_CHOICE_BREAK,
    CMD_SECOND_SOUL_CHOICE_STABLE,
    CMD_SECOND_SOUL_DEMON_STATUS,
    CMD_SECOND_SOUL_STATUS,
    CMD_SECOND_SOUL_TRAIN,
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_GANGFENG,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    CMD_TIANXING_CHANGE_FATE,
    CMD_TIANXING_CLEAR_CALAMITY,
    CMD_TIANXING_OBSERVE,
    CMD_TIANXING_PANEL,
    CMD_TIANXING_PREDICT,
    CMD_TIANXING_SET_STAR,
    CMD_SECT_TEACH,
    CMD_SMALL_WORLD_BARRIER,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_PREACH,
    CMD_SMALL_WORLD_QUERY,
    CMD_SMALL_WORLD_RELIEF,
    CMD_SMALL_WORLD_REFINE,
    CMD_STARGAZER_COLLECT,
    CMD_STARGAZER_GUIDE,
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_SOOTHE,
    CMD_TOWER,
    CMD_TREE_GUARD,
    CMD_TREE_HARVEST,
    CMD_TREE_PULSE,
    CMD_TREE_PULSE_STATUS,
    CMD_TREE_STATUS,
    CMD_TREE_WATER,
    CMD_WILD_TRAINING,
    CMD_WENDAO,
    CMD_DUEL,
    CMD_MULAN_COLLECT,
    CMD_MULAN_JUDGE,
    CMD_MULAN_PUBLISH,
    CMD_MULAN_SHADOW,
    CMD_FISHING,
    CMD_FISHING_BUY_BAIT,
    CMD_FISHING_CANCEL,
    CMD_FISHING_CHUM,
    CMD_FISHING_LIFT,
    CMD_FISHING_OPEN,
    CMD_FISHING_PROBE,
    CMD_FISHING_STATUS,
    CMD_WORLD_BOSS_STATUS,
    CMD_YINDAO,
    CMD_YINLUO_BANNER,
    CMD_YINLUO_BLOOD_FOREST,
    CMD_YINLUO_COLLECT,
    CMD_YINLUO_CONVERT,
    CMD_YINLUO_DAILY_SACRIFICE,
    CMD_YINLUO_DEMON_SUMMON,
    CMD_YINLUO_REFINE,
    CMD_YUANYING,
    CMD_YUANYING_SECT_RETREAT,
    CMD_YUANYING_STATUS,
    CD_BUFFER_SEC,
    DEEP_RETREAT_CD,
    LAUNCHING_TIMEOUT_SEC,
    LOG_GROUP_ID,
    MESSAGES_DIR,
    MODULE_KEY_MAP,
    MODULE_NAMES,
    PROJECT_ROOT_DIR,
    STATE_DIR,
    RE_CMD_DISABLE_ALL,
    RE_CMD_ENABLE_ALL,
    RE_CMD_ENABLE_PATTERNS,
    RE_CMD_GLOBAL_PAUSE,
    RE_CMD_GLOBAL_RESUME,
    RE_CMD_HELP,
    RE_CMD_LOGIN,
    RE_CMD_ANALYSIS_HEALTH,
    RE_CMD_ANALYSIS_LOG_GROUP,
    RE_CMD_ANALYSIS_SUMMARY,
    RE_CMD_ANALYSIS_UNKNOWN,
    RE_CMD_ANALYSIS_WEBMINI,
    RE_CMD_RUNTIME_HEALTH,
    RE_CMD_RUNTIME_HEALTH_DETAIL,
    RE_CMD_AUDIT_FLUSH_SUMMARY,
    RE_CMD_AUDIT_PUSH_STATUS,
    RE_CMD_STAGING_PREFLIGHT,
    RE_CMD_SINGLE_STATUS_PATTERNS,
    RE_CMD_STATUS,
    RE_WHITESPACE,
    RETRY_MAX_SEC,
    SUMMARY_TIMEOUT_SEC,
    TAIYI_CYCLE_CD_SEC,
    TZ_LOCAL,
    YUANYING_CD,
    format_battle_power_command,
    get_account_offline_reason,
    get_all_clients,
    get_registered_client,
    is_account_offline,
    format_identity_info_command,
    is_battle_power_command_text,
    is_identity_info_command_text,
    is_identity_refresh_command_text,
)
from .features.checkin import get_checkin_status_text, get_sect_teach_status_text
from .features.concubine import clear_concubine_state, clear_concubine_tianji_state, get_concubine_status_text, restore_concubine_runtime
from .features.deep_retreat import get_deep_retreat_status_detail_text
from .features.divination import get_divination_pending_health_lines, get_divination_status_text
from .features.formation import clear_formation_state, get_formation_status_text
from .features.guanxing import (
    clear_guanxing_identity_runtime,
    get_guanxing_status_text,
    restore_guanxing_round_runtime,
)
from .features.guanxing_monitor import get_guanxing_monitor_status_text, restore_guanxing_monitor_runtime_state
from .features.hehuan import execute_hehuan_manual_action, get_hehuan_status_text
from .features.jiyin import clear_jiyin_state, get_jiyin_status_text
from .features.join_dungeon import get_dungeon_join_inbox_snapshot
from .features.nanlong import clear_nanlong_state, get_nanlong_status_text
from .features import passive_event_ledger
from .features.passive_inbox import get_passive_inbox_snapshot, get_passive_inbox_status_text
from .message_box import message_fact_from_dict, write_message_box_snapshot_payload
from .message_contract import (
    MESSAGE_CONTRACT_GAP_REASONS,
    format_message_box_shadow_alignment,
    get_message_contract_status_text,
    summarize_message_box_shadow_alignment,
)
from .features.pet import get_pet_status_text
from .features.quiz import clear_quiz_state, get_quiz_status_text
from .features.ranch import clear_ranch_state, get_ranch_status_text, schedule_ranch_initial_check
from .features.small_world import clear_small_world_state, get_small_world_status_text, restore_small_world_runtime, schedule_small_world_initial_check
from .features.stargazer import get_stargazer_status_text
from .features.tianxing import execute_tianxing_manual_action, get_tianxing_automation_pause_text, get_tianxing_status_text, set_tianxing_automation_paused
from .features.tianti import get_tianti_status_text
from .features.tower import get_tower_status_text
from .features.tree import get_tree_status_text, request_tree_bootstrap_check
from .features.world_boss import clear_world_boss_identity_state, get_world_boss_status_text
from .features.second_soul import get_second_soul_status_text
from .features.taiyi import _has_yindao_send_evidence, _resolve_yindao_command, get_taiyi_status_text
from .features.explore_rift import (
    clear_explore_rift_state,
    get_explore_rift_status_text as get_explore_rift_feature_status_text,
    schedule_explore_rift_initial_check,
)
from .features.wendao import clear_wendao_state, get_wendao_status_text, schedule_wendao_initial_check
from .features.mulan import clear_mulan_state, get_mulan_status_text, schedule_mulan_initial_check
from .features.duel import apply_duel_config, clear_duel_state, get_duel_status_text, schedule_duel_initial_check
from .features.fishing_runtime import clear_fishing_state, get_fishing_status_text, schedule_fishing_initial_check
from .features.yuanying import get_yuanying_status_detail_text
from .features.yinluo import execute_yinluo_manual_action, get_yinluo_status_text
from .app_replica import (
    build_log_group_replica_panel,
    format_log_group_replica_cd_overview,
    format_log_group_replica_help,
    format_log_group_replica_panel,
)
from .features.wild_training import (
    WILD_TRAINING_CYCLE_MIN_SEC,
    WILD_TRAINING_RECOVERY_SPREAD_MAX_SEC,
    WILD_TRAINING_RECOVERY_SPREAD_MIN_SEC,
    WILD_TRAINING_RETRY_MAX_SEC,
    WILD_TRAINING_RETRY_MIN_SEC,
    clear_wild_training_state,
    get_wild_training_status_text,
    schedule_wild_training_initial_check,
)
from .action_guard import (
    close_actions as close_action_guard_actions,
    close_by_module as close_action_guard_by_module,
    reconcile_identity_sessions,
    resolve_action_key as resolve_action_guard_key,
)
from .persistence import delete_identity_from_db, mark_dirty, save_state
from .runtime import (
    IDENTITY_INFO_REFRESH_ERROR_TEXT,
    build_ui_login_url,
    clear_identity_runtime_tracking,
    clear_pending_tasks_by_commands,
    flush_low_priority_audit_summary,
    get_audit_push_status_text,
    get_game_send_queue_snapshot,
    get_low_priority_audit_pending_counts,
    console_log,
    issue_ui_login_token,
    reply_log_group_message,
    send_audit_log,
    send_game_command,
)
from .storage_bag_api_runtime import refresh_storage_bag_records_from_api
from .state import (
    ensure_identity_registered,
    get_accounts,
    get_available_module_names,
    get_current_identity_id,
    get_dungeon_join_run_state,
    get_game_group_id,
    get_identity_account,
    get_identity_display_name,
    get_identity_enabled,
    get_identity_ids,
    get_identity_ui_display_name,
    get_global_enabled,
    get_guanxing_monitor_enabled,
    get_guanxing_shift_target,
    has_sect_membership,
    has_identity,
    remove_identity,
    set_global_enabled as set_global_enabled_state,
    get_module_window_hours,
    get_pending_command,
    get_replica_dispatch_group_ids,
    get_pet_name,
    get_realm_sort_index,
    resolve_identity_selector,
    resolve_identity_selector_detail,
    infer_realm_from_xiuwei_max,
    is_auto_delete_sent_messages_enabled,
    get_send_as_profile,
    get_send_as_tags,
    get_stargazer_total_slots,
    get_storage_bag_records,
    get_tianti_rank_choice,
    REALM_SORT_ORDER,
    is_module_available,
    is_explore_rift_realm_available,
    is_small_world_realm_available,
    is_yuanying_realm_available,
    set_identity_account,
    set_identity_enabled as set_identity_enabled_profile,
    set_module_window_hours,
    set_guanxing_monitor_enabled,
    set_tianti_rank_choice,
    split_command_identity_selector,
    state,
    update_send_as_profile,
    use_identity,
)
from .timing import calc_next_daily_window_after_completion, calc_next_daily_window_time, fmt_abs_ts, fmt_remaining, get_checkin_day_key, get_day_key, reset_checkin_daily_state, schedule_next_checkin, schedule_next_checkin_after_completion, schedule_next_tower, schedule_next_tower_after_completion

RE_IDENTITY_INFO_CARD = re.compile(r"天命玉牒")
RE_BATTLE_POWER_CARD = re.compile(r"📊\s*【天机阁[\s\S]*?战力评估】")
RE_CMD_PASSIVE_INBOX_STATUS = re.compile(r"^\.(?:消息盒子|被动|被动盒子)(?:状态)?$")
RE_CMD_MESSAGE_BOX_SHADOW = re.compile(r"^\.(?:消息盒子shadow|shadow消息盒子|消息盒子影子)(?:\s+(\d{1,5}))?$", re.I)
RE_CMD_MESSAGE_CONTRACT_STATUS = re.compile(r"^\.(?:消息契约|契约缺口)(?:状态)?(?:\s+([\w_\-\u4e00-\u9fff]+))?$")
RE_CMD_DUEL_CONFIG = re.compile(r"^\.(?:设置斗法|斗法配置)\s+(\S+)(?:\s+(\d+))?$")
RE_CMD_DUNGEON_QUERY_ALIAS = re.compile(r"^\.(?:查询副本|查询\s*(?:副本|虚天殿|虚天|坠魔谷|坠魔|黄龙山|黄龙|苍坤洞府|苍坤|昆吾山|昆吾|落云秘圃|落云)|查询(?:虚|昆|苍|坠|黄|落))$")
RE_CMD_DUNGEON_CD_OVERVIEW = re.compile(r"^\.(?:副本(?:cd|冷却)(?:概览)?|查询副本(?:cd|冷却)(?:概览)?)$", re.I)
RE_CMD_DUNGEON_HELP = re.compile(r"^\.副本帮助$")
RE_CMD_STORAGE_BAG_REPORT = re.compile(r"^\.(储物袋汇总|储物袋盘点|材料汇总)(?:\s+([\s\S]+))?$")
RE_CMD_STORAGE_BAG_SIMPLE_FIND = re.compile(r"^\.(?:还有多少)\s+([\s\S]+?)\s*$")
RE_CMD_STORAGE_BAG_API_REFRESH = re.compile(r"^\.(?:更新储物袋|刷新储物袋|储物袋更新|储物袋刷新)$")
RE_CMD_HEHUAN_MANUAL = re.compile(r"^\.合欢(?:温养|双修温养)$")
RE_CMD_TIANXING_AUTOMATION_CONTROL = re.compile(r"^\.天星(?:自动)?(暂停|恢复)(?:\s+(\S+))?$")
RE_CMD_TIANXING_MANUAL = re.compile(r"^\.天星(查盘|观命|定命|推命|改命|消劫)(?:\s+(\S+))?$")
RE_CMD_YINLUO_MANUAL = re.compile(r"^\.阴罗(查幡|每日献祭|献祭|召唤魔影|召唤|收取精华|收取幡魂|收取|炼化|囚禁魂魄|囚禁|化煞|化功为煞|血洗山林|血洗|下咒|夺舍)(?:\s+([\s\S]+))?$")
RE_CMD_XUTIAN_FOLLOWUP_MANUAL = re.compile(r"^(?:\.选择道路\s+(?:冰|火)|\.阵策\s+(?:稳|压|势)|\.争鼎\s+(?:求稳|夺鼎)|\.后殿抉择\s+(?:收手|冲关)|\.后殿阵策\s+(?:镇|夺|卦))$")
RE_STORAGE_BAG_RECENT_DAYS = re.compile(r"近\s*(\d{1,2})\s*天")
STORAGE_BAG_REPORT_TIMEOUT_SEC = 30
STORAGE_BAG_REPORT_REPLY_LIMIT = 3300
ANALYSIS_REPORT_DIR = Path(PROJECT_ROOT_DIR) / "data" / "analysis" / "latest"
ANALYSIS_PAYLOAD_FILE = ANALYSIS_REPORT_DIR / "analysis_payload.json"
MESSAGE_BOX_SHADOW_DIR = Path(STATE_DIR) / "message_box_shadow"
MESSAGE_BOX_SHADOW_LATEST_FILE = MESSAGE_BOX_SHADOW_DIR / "latest.json"
HEALTH_OBSERVER_LATEST_FILE = Path(STATE_DIR) / "health_observer" / "latest.json"
HEALTH_OBSERVER_LATEST_MD_FILE = Path(STATE_DIR) / "health_observer" / "latest.md"
RE_IDENTITY_INFO_NAME = re.compile(r"(?:道号|修士)[:：]\s*(\S+)")
RE_IDENTITY_INFO_REALM_SECT = re.compile(r"境界[:：]\s*(\S+)")
RE_IDENTITY_INFO_REALM_WITH_SECT = re.compile(r"境界[:：]\s*\S+\s*\(([^)]+)\)")
RE_IDENTITY_INFO_SECT = re.compile(r"宗门[:：]\s*【([^】]+)】")
RE_IDENTITY_INFO_SPIRITUAL_ROOT = re.compile(r"灵根\s*[:：]\s*([^\n\r]+)")
RE_BATTLE_POWER_SPIRITUAL_ROOT = re.compile(r"灵根【([^】]+)】")
RE_IDENTITY_INFO_XIUWEI = re.compile(r"修为[:：]\s*([\d,]+)\s*/\s*([\d,]+)")
RE_BATTLE_POWER_OWNER = re.compile(r"👤\s*修士[:：]\s*(\S+)\s*\(@([^)\s]+)\)")
RE_BATTLE_POWER_VALUE = re.compile(r"综合战力[:：]\s*([\d,.]+)\s*([万亿]?)")
RE_IDENTITY_INFO_OWNER = re.compile(r"@([A-Za-z0-9_]+)\s*的天命玉牒")
RE_REALM_BREAKTHROUGH = re.compile(r"成功突破至【([^】]+)】")
IDENTITY_INFO_REFRESH_TIMEOUT_SEC = 180
IDENTITY_INFO_FOLLOWUP_DELAY_MIN_SEC = 20
IDENTITY_INFO_FOLLOWUP_DELAY_MAX_SEC = 25
IDENTITY_REFRESH_REQUIRED_FIELDS = ("daohao", "sect_name", "realm", "spiritual_root_type", "xiuwei")
_IMMEDIATE_ENABLE_RETRY_DELAY_SEC = 1
RECOVERY_SPREAD_MIN_SEC = 60
RECOVERY_SPREAD_MAX_SEC = 1200
RECOVERY_SPREAD_DUE_GRACE_SEC = 2
RECOVERY_SHORT_WINDOW_SEC = 180
RECOVERY_READY_MIN_SEC = 30
RECOVERY_READY_MAX_SEC = 90
RECOVERY_PHASEFUL_IDLE_MIN_SEC = 10 * 60
RECOVERY_PHASEFUL_IDLE_MAX_SEC = 30 * 60
TIANTI_RECOVERY_STATUS_FRESH_SEC = 30 * 60
TAIYI_PRESEND_RECOVERY_MAX_SEC = 300
RECOVERY_SPREAD_TIMER_KEYS = (
    "next_irr_time",
    "next_guard_time",
    "next_pet_time",
    "next_pet_trial_time",
    "next_ranch_time",
    "next_wild_training_time",
    "next_stargazer_panel_time",
    "stargazer_collect_due_at",
    "stargazer_followup_due_at",
    "next_tianti_status_time",
    "next_tianti_wenxin_time",
    "next_tianti_climb_time",
    "next_tianti_gangfeng_time",
    "next_checkin_time",
    "next_sect_teach_time",
    "next_tower_time",
    "next_quiz_time",
    "next_jiyin_time",
    "next_concubine_time",
    "next_nanlong_time",
    "next_small_world_time",
    "next_yuanying_time",
    "next_explore_rift_time",
    "next_wendao_time",
    "next_mulan_time",
    "next_duel_time",
    "next_deep_retreat_time",
    "next_second_soul_time",
    "next_taiyi_cycle_time",
)

TREE_HARVESTED_MATURING_STALE_SEC = 30 * 3600

_message_box_shadow_payload_provider = None


def register_message_box_shadow_payload_provider(provider):
    global _message_box_shadow_payload_provider
    _message_box_shadow_payload_provider = provider


def _payload_to_message_box_facts(payload):
    rows = []
    if isinstance(payload, dict):
        rows = payload.get("facts") or []
    elif isinstance(payload, list):
        rows = payload
    facts = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            facts.append(message_fact_from_dict(row))
    return facts


def get_message_box_shadow_status_text(limit=500):
    if _message_box_shadow_payload_provider is None:
        return "📦 MessageBox shadow 对账\n- 未注册 shadow provider，当前进程不能导出内存消息盒子。"
    safe_limit = max(1, min(int(limit or 500), 5000))
    try:
        payload = _message_box_shadow_payload_provider(include_edits=True, limit=safe_limit)
        path = write_message_box_snapshot_payload(MESSAGE_BOX_SHADOW_LATEST_FILE, payload)
        facts = _payload_to_message_box_facts(payload)
        summary = summarize_message_box_shadow_alignment(
            facts,
            passive_event_ledger.iter_passive_events(limit=safe_limit),
            latest_limit=8,
        )
    except Exception as exc:
        return f"📦 MessageBox shadow 对账\n- 导出失败：{str(exc)[:160]}"
    return "\n".join(
        [
            format_message_box_shadow_alignment(summary, latest_limit=8),
            f"- 快照文件：{path}",
        ]
    )


def _coerce_control_bool(value, default=False):
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


def _state_positive_int(key):
    try:
        return int(state.get(key, 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _is_tianti_ready_to_climb_snapshot():
    cooldown_text = str(state.get("tianti_cooldown_text") or "").strip()
    return bool(state.get("tianti_enabled") and cooldown_text and "可立即" in cooldown_text)


def _has_fresh_tianti_recovery_status(now):
    try:
        seen_at = float(state.get("tianti_last_status_seen_at", 0) or 0)
    except (TypeError, ValueError):
        return False
    return seen_at > 0 and float(now or 0) - seen_at <= TIANTI_RECOVERY_STATUS_FRESH_SEC


def _has_released_tianxing_explore_downstream(now):
    if not state.get("tianxing_enabled"):
        return False
    if _state_positive_int("wild_training_reply_to_msg_id"):
        return False
    observation = state.get("tianxing_observation") if isinstance(state.get("tianxing_observation"), dict) else {}
    timeline = state.get("tianxing_timeline_state") if isinstance(state.get("tianxing_timeline_state"), dict) else {}
    if str(timeline.get("phase") or "").strip() != "downstream_released":
        return False
    if str(timeline.get("route") or "").strip() != "探索":
        return False
    released_routes = timeline.get("released_routes") if isinstance(timeline.get("released_routes"), dict) else {}
    released_explore = released_routes.get("探索") if isinstance(released_routes.get("探索"), dict) else {}
    if not released_explore:
        return False
    if str(observation.get("current_prediction") or "").strip() != "探索":
        return False
    if str(observation.get("current_change") or "").strip() != "探索":
        return False
    try:
        prediction_until = float(observation.get("current_prediction_until", 0) or 0)
        change_until = float(observation.get("current_change_until", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    return prediction_until > float(now or 0) and change_until > float(now or 0)


def _has_stale_tianti_daily_marker(today_key):
    last_wenxin_day = str(state.get("tianti_last_wenxin_day") or "")
    trigger_key = str(state.get("tianti_wenxin_last_trigger_key") or "")
    if last_wenxin_day and last_wenxin_day != today_key:
        return True
    return bool(trigger_key and not trigger_key.startswith(f"{today_key}|"))


def _spread_recovery_timer_value(timer_key, now, due_cutoff):
    if timer_key == "next_wild_training_time":
        if _has_released_tianxing_explore_downstream(now):
            return now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC
        try:
            retry_count = int(state.get("wild_training_retry_count", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            retry_count = 0
        if retry_count > 0 and not _state_positive_int("wild_training_reply_to_msg_id"):
            return now + random.uniform(WILD_TRAINING_RETRY_MIN_SEC, WILD_TRAINING_RETRY_MAX_SEC)
        try:
            last_result_at = float(state.get("wild_training_last_result_at", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            last_result_at = 0
        if last_result_at > 0:
            true_due = last_result_at + WILD_TRAINING_CYCLE_MIN_SEC
            if true_due > now:
                return true_due
        return now + random.uniform(WILD_TRAINING_RECOVERY_SPREAD_MIN_SEC, WILD_TRAINING_RECOVERY_SPREAD_MAX_SEC)

    if timer_key == "next_tianti_climb_time" and _is_tianti_ready_to_climb_snapshot():
        status_time = float(state.get("next_tianti_status_time", 0) or 0)
        if 0 < status_time <= now + RECOVERY_SPREAD_MAX_SEC:
            state["next_tianti_status_time"] = 0
        state["next_tianti_status_time"] = now + random.uniform(RECOVERY_READY_MIN_SEC, RECOVERY_READY_MAX_SEC)
        return now + RECOVERY_SPREAD_MAX_SEC + random.uniform(60, 600)

    phaseful_timer_meta = {
        "next_yuanying_time": ("yuanying_enabled", "yuanying_phase"),
        "next_deep_retreat_time": ("deep_retreat_enabled", "deep_retreat_phase"),
    }
    phaseful_meta = phaseful_timer_meta.get(timer_key)
    if phaseful_meta and state.get(phaseful_meta[0]):
        phase = str(state.get(phaseful_meta[1]) or "idle")
        if phase in {"launching", "queued_launch", "summary_due", "observing_summary", "waiting_summary", "post_summary_wait"}:
            return now + random.uniform(RECOVERY_PHASEFUL_IDLE_MIN_SEC, RECOVERY_PHASEFUL_IDLE_MAX_SEC)
        if phase == "idle":
            return now + random.uniform(RECOVERY_PHASEFUL_IDLE_MIN_SEC, RECOVERY_PHASEFUL_IDLE_MAX_SEC)

    return now + random.uniform(RECOVERY_SPREAD_MIN_SEC, RECOVERY_SPREAD_MAX_SEC)


def _restore_tianti_ready_runtime(now):
    if not _is_tianti_ready_to_climb_snapshot():
        return
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    if next_climb_time <= 0 or next_climb_time > now + RECOVERY_SPREAD_MAX_SEC:
        return
    state["next_tianti_status_time"] = now + random.uniform(RECOVERY_READY_MIN_SEC, RECOVERY_READY_MAX_SEC)
    state["next_tianti_climb_time"] = now + RECOVERY_SPREAD_MAX_SEC + random.uniform(60, 600)


def _restore_tianti_active_cooldown_runtime(now):
    if not state.get("tianti_enabled"):
        return
    needs_status = False
    if state.get("tianti_gangfeng_enabled"):
        next_gangfeng_time = float(state.get("next_tianti_gangfeng_time", 0) or 0)
        has_gangfeng_snapshot = any(
            value not in {None, "", 0, "未记录"}
            for value in (
                state.get("tianti_cycle_count"),
                state.get("tianti_gangfeng_level"),
                state.get("tianti_gangfeng_status"),
            )
        )
        if next_gangfeng_time <= 0 and not has_gangfeng_snapshot:
            state["next_tianti_gangfeng_time"] = now + RECOVERY_SPREAD_MAX_SEC + random.uniform(60, 600)
            needs_status = True
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    cooldown_text = str(state.get("tianti_cooldown_text") or "").strip()
    if next_climb_time <= now and cooldown_text:
        state["next_tianti_climb_time"] = now + RECOVERY_SPREAD_MAX_SEC + random.uniform(60, 600)
        needs_status = True
    if needs_status and not _has_fresh_tianti_recovery_status(now):
        status_time = float(state.get("next_tianti_status_time", 0) or 0)
        if status_time <= 0 or status_time > now + RECOVERY_SPREAD_MAX_SEC:
            state["next_tianti_status_time"] = now + random.uniform(RECOVERY_READY_MIN_SEC, RECOVERY_READY_MAX_SEC)


def _is_within_module_window(module_name, now):
    start_hour_utc, end_hour_utc = get_module_window_hours(module_name)
    utc_now = datetime.fromtimestamp(now, timezone.utc)
    day_start = utc_now.replace(hour=start_hour_utc, minute=0, second=0, microsecond=0)
    day_end = utc_now.replace(hour=end_hour_utc, minute=0, second=0, microsecond=0)
    return day_start <= utc_now < day_end


def _schedule_module_next_window_after_enable(module_name, now):
    if module_name == "点卯":
        return schedule_next_checkin(now, persist=False)
    if module_name == "闯塔":
        return schedule_next_tower(now, persist=False)
    raise ValueError(f"模块不支持执行窗口调度: {module_name}")


def _schedule_module_immediate_retry(module_name, now):
    retry_at = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC
    if module_name == "灵树":
        state["next_irr_time"] = retry_at
        return retry_at
    if module_name == "法宝":
        state["next_pet_time"] = retry_at
        return retry_at
    if module_name == "温养器灵":
        state["next_pet_warm_time"] = retry_at
        return retry_at
    if module_name == "器灵试炼":
        state["next_pet_trial_time"] = retry_at
        return retry_at
    if module_name == "布下剑阵":
        state["next_pet_formation_time"] = retry_at
        return retry_at
    if module_name in {"侍妾", "天机代卜", "共历心劫", "侍妾远航"}:
        state["next_concubine_time"] = retry_at
        return retry_at
    if module_name == "观星台":
        state["next_stargazer_panel_time"] = retry_at
        return retry_at
    if module_name == "点卯":
        state["next_checkin_time"] = retry_at
        return retry_at
    if module_name == "闯塔":
        state["next_tower_time"] = retry_at
        return retry_at
    if module_name == "元婴":
        state["next_yuanying_time"] = retry_at
        return retry_at
    if module_name == "问道":
        state["next_wendao_time"] = retry_at
        return retry_at
    if module_name == "慕兰":
        state["next_mulan_time"] = retry_at
        return retry_at
    if module_name == "斗法":
        state["next_duel_time"] = retry_at
        return retry_at
    if module_name == "深度闭关":
        state["next_deep_retreat_time"] = retry_at
        return retry_at
    raise ValueError(f"未知模块: {module_name}")


def spread_overdue_runtime_timers(now=None, *, reason="recovery", window_sec=None):
    """启动/恢复时把已到期任务摊开，避免多身份集中发送。"""
    if now is None:
        now = time.time()
    now = float(now)
    if window_sec is None:
        window_sec = RECOVERY_SHORT_WINDOW_SEC
    due_cutoff = now + max(RECOVERY_SPREAD_DUE_GRACE_SEC, float(window_sec or 0))
    changed_count = 0
    affected_identity_ids = set()
    for identity_id in get_identity_ids():
        if not has_identity(identity_id) or not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id):
            for timer_key in RECOVERY_SPREAD_TIMER_KEYS:
                try:
                    timer_value = float(state.get(timer_key, 0) or 0)
                except (TypeError, ValueError):
                    continue
                if 0 < timer_value <= due_cutoff:
                    state[timer_key] = _spread_recovery_timer_value(timer_key, now, due_cutoff)
                    changed_count += 1
                    affected_identity_ids.add(int(identity_id))
    if changed_count > 0:
        mark_dirty()
        console_log(
            f"🧯 {reason} 错峰：{len(affected_identity_ids)} 个身份 / {changed_count} 个到期计时器已按恢复策略摊开",
            scope="global",
            limit=220,
        )
    return changed_count


def get_module_unavailable_reason(module_name, send_as_id=None):
    if module_name == "观星监控":
        return ""
    if module_name == "观星" and not get_guanxing_shift_target():
        return "请先在基础配置中填写观星改换目标"
    if is_module_archived(module_name):
        manifest = get_module_manifest(module_name)
        reason = str(getattr(manifest, "archive_reason", "") or "").strip()
        return f"{module_name}模块已归档" + (f"：{reason}" if reason else "")
    if is_module_available(module_name, send_as_id):
        return ""
    if module_name in {"点卯", "宗门传功", "闯塔"} and not has_sect_membership(send_as_id):
        return f"当前身份无宗门，无法执行{module_name}模块"
    if module_name == "灵树":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "观星台":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "观星":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "周天星斗":
        return "当前宗门未提供周天星斗模块"
    if module_name == "登天阶":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "太一":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "问道":
        return "当前宗门未提供问道模块"
    if module_name == "放养":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "合欢宗":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "天星宗":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "阴罗宗":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "元婴" and not is_yuanying_realm_available(send_as_id):
        return f"当前境界未达到{module_name}模块开启条件"
    if module_name == "探寻裂缝" and not is_explore_rift_realm_available(send_as_id):
        return f"当前境界未达到{module_name}模块开启条件"
    if module_name == "小世界" and not is_small_world_realm_available(send_as_id):
        return f"当前境界未达到{module_name}模块开启条件"
    return f"当前条件未提供{module_name}模块"


def _clear_pending_tasks_by_commands(commands):
    identity_id = get_current_identity_id()
    clear_pending_tasks_by_commands(commands, send_as_id=identity_id)
    action_keys = [
        action_key
        for action_key in (resolve_action_guard_key(command) for command in tuple(commands or ()))
        if action_key
    ]
    if action_keys:
        close_action_guard_actions(action_keys, send_as_id=identity_id, reason="pending_cleared_by_module")


def _close_module_action_guard_sessions(module_name, reason="module_disabled"):
    return close_action_guard_by_module(module_name, send_as_id=get_current_identity_id(), reason=reason)


def _disable_tree_module_state():
    state["tree_enabled"] = False
    state["next_irr_time"] = 0
    state["next_guard_time"] = 0
    state["is_maturing"] = False
    state["is_invading"] = False
    state["is_harvested"] = False
    state["pending_irrigation"] = False
    state["tree_bootstrap_check_needed"] = False
    state["tree_pulse_mode_seen"] = False
    state["tree_pulse_last_panel_at"] = 0
    state["tree_pulse_progress"] = 0.0
    state["tree_pulse_main"] = ""
    state["tree_pulse_aux"] = ""
    state["tree_pulse_reverse"] = ""
    state["tree_pulse_neutral"] = ""
    state["tree_pulse_stability"] = 0
    state["tree_pulse_stability_max"] = 0
    state["tree_pulse_turbidity"] = 0
    state["tree_pulse_turbidity_max"] = 0
    state["tree_pulse_daily_used"] = 0
    state["tree_pulse_daily_limit"] = 0
    state["tree_pulse_rush_used"] = 0
    state["tree_pulse_rush_limit"] = 0
    state["tree_pulse_last_action"] = ""
    state["tree_pulse_last_error"] = ""
    state["tree_pulse_blocked_until"] = 0
    state["tree_maturing_logged"] = False
    state["tree_harvest_followup_due_at"] = 0
    state["tree_harvest_inflight_until"] = 0
    _clear_pending_tasks_by_commands({CMD_TREE_WATER, CMD_TREE_GUARD, CMD_TREE_STATUS, CMD_TREE_PULSE_STATUS, CMD_TREE_PULSE, CMD_TREE_HARVEST})


def _disable_second_soul_module_state():
    state["second_soul_enabled"] = False
    state["second_soul_phase"] = "idle"
    state["next_second_soul_time"] = 0
    state["second_soul_heart_demon_msg_id"] = 0
    state["second_soul_heart_demon_deadline"] = 0
    state["second_soul_heart_demon_notified"] = False
    state["second_soul_status_msg_id"] = 0
    state["second_soul_train_msg_id"] = 0
    state["second_soul_last_error"] = ""
    _clear_pending_tasks_by_commands({CMD_SECOND_SOUL_STATUS, CMD_SECOND_SOUL_TRAIN, CMD_SECOND_SOUL_CHOICE_BREAK, CMD_SECOND_SOUL_CHOICE_STABLE})


def _disable_taiyi_module_state():
    state["taiyi_enabled"] = False
    state["taiyi_node_search_enabled"] = False
    state["taiyi_phase"] = "idle"
    state["taiyi_pending_node_name"] = ""
    state["taiyi_yindao_msg_id"] = 0
    state["taiyi_node_search_msg_id"] = 0
    state["taiyi_node_define_msg_id"] = 0
    state["next_taiyi_cycle_time"] = 0
    state["taiyi_phase_entered_at"] = 0
    state["taiyi_freeze_until"] = 0
    state["taiyi_freeze_reason"] = ""
    state["taiyi_failure_history"] = []
    state["taiyi_yindao_resend_count"] = 0
    state["taiyi_last_error"] = ""
    _clear_pending_tasks_by_commands({CMD_YINDAO, CMD_NODE_SEARCH, CMD_NODE_DEFINE})


def _disable_pet_module_state():
    state["pet_enabled"] = False
    state["next_pet_time"] = 0
    _clear_pending_tasks_by_commands({CMD_PET})


def _disable_stargazer_module_state():
    state["stargazer_enabled"] = False
    state["next_stargazer_panel_time"] = 0
    state["stargazer_collect_due_at"] = 0
    state["stargazer_last_panel_msg_id"] = 0
    state["stargazer_last_action"] = ""
    state["stargazer_idle_slot_count"] = 0
    state["stargazer_dim_slot_count"] = 0
    state["stargazer_ready_slot_count"] = 0
    state["stargazer_busy_until"] = 0
    state["stargazer_followup_due_at"] = 0
    state["stargazer_wait_full_collect"] = False
    state["stargazer_collect_ready"] = False
    state["stargazer_soothe_before_collect"] = False
    _clear_pending_tasks_by_commands({CMD_STARGAZER_PANEL, CMD_STARGAZER_GUIDE, CMD_STARGAZER_SOOTHE, CMD_STARGAZER_COLLECT})


def _manual_disable_stargazer_module_state():
    state["stargazer_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_STARGAZER_PANEL, CMD_STARGAZER_GUIDE, CMD_STARGAZER_SOOTHE, CMD_STARGAZER_COLLECT})


def _disable_guanxing_monitor_module_state():
    set_guanxing_monitor_enabled(False)
    state["next_guanxing_monitor_notify_time"] = 0
    state["guanxing_monitor_slot_key"] = ""
    state["guanxing_monitor_slot_start_at"] = 0
    state["guanxing_monitor_slot_end_at"] = 0
    state["guanxing_monitor_seen_panel"] = False
    state["guanxing_monitor_matched_keyword"] = ""
    state["guanxing_monitor_matched_value"] = ""
    state["guanxing_monitor_last_evolution_value"] = ""
    state["guanxing_monitor_last_seen_at"] = 0
    state["guanxing_monitor_last_notified_slot_key"] = ""


def _manual_disable_guanxing_monitor_module_state():
    set_guanxing_monitor_enabled(False)


def _disable_guanxing_module_state():
    state["guanxing_enabled"] = False
    clear_guanxing_identity_runtime(get_current_identity_id())
    _clear_pending_tasks_by_commands({CMD_GUANXING, CMD_GUANXING_SHIFT})


def _manual_disable_guanxing_module_state():
    state["guanxing_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_GUANXING, CMD_GUANXING_SHIFT})


def _manual_enable_stargazer_module_state(now):
    state["stargazer_enabled"] = True
    total_slots = int(get_stargazer_total_slots() or 0)
    followup_due_at = float(state.get("stargazer_followup_due_at", 0) or 0)
    next_panel_time = float(state.get("next_stargazer_panel_time", 0) or 0)
    collect_due_at = float(state.get("stargazer_collect_due_at", 0) or 0)
    has_live_timing = max(followup_due_at, next_panel_time, collect_due_at) > now
    if total_slots > 0 and has_live_timing:
        return
    state["stargazer_followup_due_at"] = float(now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC)
    state["next_stargazer_panel_time"] = 0
    state["stargazer_last_action"] = "queue_panel"


def _restore_guanxing_monitor_runtime(now):
    _slot_info, changed = restore_guanxing_monitor_runtime_state(now)
    if changed:
        mark_dirty()



def _manual_enable_guanxing_monitor_module_state(now):
    set_guanxing_monitor_enabled(True)
    _restore_guanxing_monitor_runtime(now)


def _manual_enable_guanxing_module_state(now):
    state["guanxing_enabled"] = True
    clear_guanxing_identity_runtime(get_current_identity_id())
    restore_guanxing_round_runtime(now)


def _disable_hehuan_module_state():
    state["hehuan_enabled"] = False
    state["hehuan_observation"] = {}
    _clear_pending_tasks_by_commands({
        CMD_HEHUAN_RETREAT,
        CMD_HEHUAN_CONTRACT,
        CMD_HEHUAN_DUAL,
        CMD_HEHUAN_SEAL,
        CMD_HEHUAN_ESCAPE,
    })


def _manual_disable_hehuan_module_state():
    _disable_hehuan_module_state()


def _manual_enable_hehuan_module_state(now):
    state["hehuan_enabled"] = True
    state["hehuan_observation"] = {}


def _disable_tianxing_module_state():
    state["tianxing_enabled"] = False
    state["tianxing_observation"] = {}
    state["tianxing_timeline_state"] = {}
    _clear_pending_tasks_by_commands({
        CMD_NORMAL_RETREAT,
        CMD_TIANXING_PANEL,
        CMD_TIANXING_OBSERVE,
        CMD_TIANXING_SET_STAR,
        CMD_TIANXING_PREDICT,
        CMD_TIANXING_CHANGE_FATE,
        CMD_TIANXING_CLEAR_CALAMITY,
        CMD_USE_HEQI_DAN,
        CMD_EXCHANGE_HEQI_DAN_PREFIX,
        CMD_SECT_DONATE_LINGSHI_PREFIX,
    })
    _close_module_action_guard_sessions("天星宗")


def _manual_disable_tianxing_module_state():
    _disable_tianxing_module_state()


def _manual_enable_tianxing_module_state(now):
    _close_module_action_guard_sessions("天星宗", reason="module_enabled_reset")
    state["tianxing_enabled"] = True
    state["tianxing_observation"] = {}
    state["tianxing_timeline_state"] = {}


def _disable_yinluo_module_state():
    state["yinluo_enabled"] = False
    state["yinluo_observation"] = {}
    _clear_pending_tasks_by_commands({
        CMD_YINLUO_BANNER,
        CMD_YINLUO_DAILY_SACRIFICE,
        CMD_YINLUO_BLOOD_FOREST,
        CMD_YINLUO_DEMON_SUMMON,
        CMD_YINLUO_CONVERT,
        CMD_YINLUO_COLLECT,
        CMD_YINLUO_REFINE,
    })


def _manual_disable_yinluo_module_state():
    _disable_yinluo_module_state()


def _manual_enable_yinluo_module_state(now):
    state["yinluo_enabled"] = True
    state["yinluo_observation"] = {}


def _disable_formation_module_state():
    state["formation_enabled"] = False
    clear_formation_state()
    _clear_pending_tasks_by_commands({CMD_FORMATION_START, CMD_FORMATION_ASSIST})


def _manual_disable_formation_module_state():
    state["formation_enabled"] = False
    clear_formation_state()
    _clear_pending_tasks_by_commands({CMD_FORMATION_START, CMD_FORMATION_ASSIST})


def _manual_enable_formation_module_state(now):
    state["formation_enabled"] = True
    state["formation_last_error"] = ""
    if float(state.get("next_formation_time", 0) or 0) <= 0:
        state["next_formation_time"] = float(now or 0)


def _disable_tianti_module_state():
    state["tianti_enabled"] = False
    state["next_tianti_status_time"] = 0
    state["next_tianti_wenxin_time"] = 0
    state["next_tianti_climb_time"] = 0
    state["next_tianti_gangfeng_time"] = 0
    state["tianti_status_reply_to_msg_id"] = 0
    state["tianti_last_status_msg_id"] = 0
    state["tianti_last_wenxin_msg_id"] = 0
    state["tianti_last_climb_msg_id"] = 0
    state["tianti_last_gangfeng_msg_id"] = 0
    state["tianti_remaining_climb_count"] = 0
    state["tianti_last_wenxin_day"] = ""
    state["tianti_wenxin_last_trigger_key"] = ""
    state["tianti_gangfeng_last_trigger_key"] = ""
    state["tianti_last_skip_reason"] = ""
    state["tianti_theoretical_max_stage"] = 0
    state["tianti_wenxin_trigger_stage"] = 0
    state["tianti_gangfeng_status"] = "未记录"
    state["tianti_last_error"] = ""
    _clear_pending_tasks_by_commands({CMD_TIANTI_STATUS, CMD_TIANTI_WENXIN, CMD_TIANTI_CLIMB, CMD_TIANTI_GANGFENG})


def _manual_disable_tianti_module_state():
    state["tianti_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_TIANTI_STATUS, CMD_TIANTI_WENXIN, CMD_TIANTI_CLIMB, CMD_TIANTI_GANGFENG})


def _manual_enable_tianti_module_state(now):
    state["tianti_enabled"] = True
    today_key = get_day_key(now)
    if _has_stale_tianti_daily_marker(today_key):
        state["tianti_last_wenxin_day"] = ""
        state["tianti_wenxin_last_trigger_key"] = ""
        state["tianti_gangfeng_last_trigger_key"] = ""
        state["tianti_last_skip_reason"] = ""
        state["tianti_theoretical_max_stage"] = 0
        state["tianti_wenxin_trigger_stage"] = 0
        state["next_tianti_wenxin_time"] = 0
    next_status_time = float(state.get("next_tianti_status_time", 0) or 0)
    next_wenxin_time = float(state.get("next_tianti_wenxin_time", 0) or 0)
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    next_gangfeng_time = float(state.get("next_tianti_gangfeng_time", 0) or 0)
    if next_status_time > now or next_wenxin_time > now or next_climb_time > now or next_gangfeng_time > now:
        return
    has_status_snapshot = any(
        value not in {None, "", 0, "未记录"}
        for value in (
            state.get("tianti_progress_current"),
            state.get("tianti_cycle_count"),
            state.get("tianti_gangfeng_level"),
            state.get("tianti_cooldown_text"),
            state.get("tianti_wenxin_status"),
        )
    )
    if not has_status_snapshot or (next_climb_time > 0 and now >= next_climb_time):
        state["next_tianti_status_time"] = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC


def _manual_disable_pet_module_state():
    state["pet_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_PET})



def _manual_enable_pet_module_state(now):
    state["pet_enabled"] = True
    state["pet_last_error"] = ""
    if float(state.get("next_pet_time", 0) or 0) > now:
        return
    _schedule_module_immediate_retry("法宝", now)


def _manual_disable_pet_warm_module_state():
    state["pet_warm_enabled"] = False
    state["next_pet_warm_time"] = 0
    _clear_pending_tasks_by_commands({CMD_PET_WARM})


def _manual_enable_pet_warm_module_state(now):
    state["pet_warm_enabled"] = True
    state["pet_warm_last_error"] = ""
    if float(state.get("next_pet_warm_time", 0) or 0) > now:
        return
    _schedule_module_immediate_retry("温养器灵", now)


def _manual_disable_pet_trial_module_state():
    state["pet_trial_enabled"] = False
    state["next_pet_trial_time"] = 0
    _clear_pending_tasks_by_commands({CMD_PET_TRIAL})


def _manual_enable_pet_trial_module_state(now):
    state["pet_trial_enabled"] = True
    state["pet_trial_last_error"] = ""
    if float(state.get("next_pet_trial_time", 0) or 0) > now:
        return
    _schedule_module_immediate_retry("器灵试炼", now)


def _manual_disable_pet_formation_module_state():
    state["pet_formation_enabled"] = False
    state["next_pet_formation_time"] = 0
    state["pet_formation_retry_count"] = 0
    _clear_pending_tasks_by_commands({CMD_PET_FORMATION})


def _manual_enable_pet_formation_module_state(now):
    state["pet_formation_enabled"] = True
    state["pet_formation_last_error"] = ""
    state["pet_formation_retry_count"] = 0
    if float(state.get("next_pet_formation_time", 0) or 0) > now:
        return
    _schedule_module_immediate_retry("布下剑阵", now)


def _manual_disable_ranch_module_state():
    state["ranch_enabled"] = False
    clear_ranch_state(persist=False, keep_last_error=True)
    _clear_pending_tasks_by_commands({CMD_RANCH})


def _manual_enable_ranch_module_state(now):
    state["ranch_enabled"] = True
    if float(state.get("next_ranch_time", 0) or 0) > now:
        return
    schedule_ranch_initial_check(now, persist=False, keep_last_error=True)


def _manual_disable_wild_training_module_state():
    state["wild_training_enabled"] = False
    clear_wild_training_state(persist=False, keep_last_error=True)
    _clear_pending_tasks_by_commands({CMD_WILD_TRAINING})


def _manual_enable_wild_training_module_state(now):
    state["wild_training_enabled"] = True
    if float(state.get("next_wild_training_time", 0) or 0) > now:
        return
    schedule_wild_training_initial_check(now, persist=False, keep_last_error=True)


def _disable_quiz_module_state():
    state["quiz_enabled"] = False
    clear_quiz_state(persist=False)
    _clear_pending_tasks_by_commands({CMD_QUIZ_ANSWER})


def _manual_disable_quiz_module_state():
    state["quiz_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_QUIZ_ANSWER})


def _disable_yuanying_module_state():
    state["yuanying_enabled"] = False
    state["yuanying_phase"] = "idle"
    state["next_yuanying_time"] = 0
    state["yuanying_probe_pending"] = False
    state["yuanying_summary_sent_at"] = 0
    state["last_yuanying_summary_msg_id"] = 0
    state["last_yuanying_command_time"] = 0
    state["yuanying_waiting_logged"] = False
    state["yuanying_protect_logged"] = False
    _clear_pending_tasks_by_commands({CMD_YUANYING, CMD_YUANYING_STATUS})


def _disable_wendao_module_state():
    state["wendao_enabled"] = False
    clear_wendao_state(persist=False, keep_last_error=True)
    _clear_pending_tasks_by_commands({CMD_WENDAO})


def _disable_mulan_module_state():
    state["mulan_enabled"] = False
    clear_mulan_state(persist=False, keep_last_error=True)
    _clear_pending_tasks_by_commands({CMD_MULAN_SHADOW, CMD_MULAN_COLLECT, CMD_MULAN_JUDGE, CMD_MULAN_PUBLISH})


def _disable_duel_module_state():
    state["duel_enabled"] = False
    clear_duel_state(persist=False, keep_last_error=True, keep_config=True)
    _clear_pending_tasks_by_commands({CMD_DUEL})


def _disable_fishing_module_state():
    state["fishing_enabled"] = False
    clear_fishing_state(persist=False, keep_last_error=True, keep_config=True)
    _clear_pending_tasks_by_commands({
        CMD_FISHING,
        CMD_FISHING_STATUS,
        CMD_FISHING_BUY_BAIT,
        CMD_FISHING_CHUM,
        CMD_FISHING_PROBE,
        CMD_FISHING_LIFT,
        CMD_FISHING_CANCEL,
        CMD_FISHING_OPEN,
    })


def _get_checkin_resume_time():
    return float(state.get("next_checkin_time", 0) or 0)


def _clear_sect_teach_runtime():
    state["next_sect_teach_time"] = 0
    state["sect_teach_reply_to_msg_id"] = 0
    state["last_sect_teach_msg_id"] = 0
    _clear_pending_tasks_by_commands({CMD_SECT_TEACH})


def _manual_disable_checkin_module_state():
    state["checkin_enabled"] = False
    state["next_checkin_time"] = 0
    _clear_pending_tasks_by_commands({CMD_CHECKIN})


def _manual_enable_checkin_module_state(now):
    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
    state["checkin_enabled"] = True
    if _get_checkin_resume_time() > now:
        return
    state["last_checkin_msg_id"] = 0
    _set_checkin_module_enabled(True, now)


def _manual_disable_sect_teach_module_state():
    state["sect_teach_enabled"] = False
    _clear_sect_teach_runtime()


def _manual_enable_sect_teach_module_state(now):
    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
    state["sect_teach_enabled"] = True
    if int(state.get("checkin_teach_count", 0) or 0) >= 3:
        _clear_sect_teach_runtime()
        return
    if float(state.get("next_sect_teach_time", 0) or 0) > now:
        return
    last_checkin_msg_id = int(state.get("last_checkin_msg_id", 0) or 0)
    if state.get("last_checkin_done_day") == day_key and last_checkin_msg_id > 0:
        state["next_sect_teach_time"] = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC
        state["sect_teach_reply_to_msg_id"] = last_checkin_msg_id


def _manual_disable_tower_module_state():
    state["tower_enabled"] = False
    state["next_tower_time"] = 0
    state["last_tower_msg_id"] = 0
    state["tower_reply_due_at"] = 0
    state["tower_retry_count"] = 0
    _clear_pending_tasks_by_commands({CMD_TOWER})


def _manual_enable_tower_module_state(now):
    _set_tower_module_enabled(True, now)


def _manual_enable_quiz_module_state(now):
    state["quiz_enabled"] = True
    if float(state.get("next_quiz_time", 0) or 0) > now:
        return
    state["next_quiz_time"] = 0
    state["quiz_reply_to_msg_id"] = 0
    state["quiz_question"] = ""
    state["quiz_options"] = {}
    state["quiz_answer"] = ""
    state["quiz_last_error"] = ""
    state["quiz_last_matched_at"] = 0
    state["quiz_deadline_at"] = 0


def _disable_jiyin_module_state():
    state["jiyin_enabled"] = False
    clear_jiyin_state(persist=False, keep_last_error=True)


def _manual_disable_jiyin_module_state():
    state["jiyin_enabled"] = False


def _parse_manual_toggle_next_time(module_name, timer_key):
    raw_next_time = state.get(timer_key, 0)
    try:
        next_time = float(raw_next_time or 0)
    except (TypeError, ValueError, OverflowError):
        next_time = 0.0
        timer_dirty = True
    else:
        timer_dirty = not math.isfinite(next_time)
    if timer_dirty:
        console_log(
            f"⚠️ 手动开启{module_name}模块时检测到异常计时：{timer_key}={raw_next_time!r}，已保留待回复状态",
            scope="identity",
            send_as_id=get_current_identity_id(),
        )
    return next_time, timer_dirty


def _manual_enable_jiyin_module_state(now):
    state["jiyin_enabled"] = True
    next_jiyin_time, timer_dirty = _parse_manual_toggle_next_time("极阴祖师", "next_jiyin_time")
    if timer_dirty or next_jiyin_time > now:
        return
    clear_jiyin_state(persist=False)


def _disable_nanlong_module_state():
    state["nanlong_enabled"] = False
    clear_nanlong_state(persist=False, keep_last_error=True)


def _manual_disable_nanlong_module_state():
    state["nanlong_enabled"] = False


def _manual_enable_nanlong_module_state(now):
    state["nanlong_enabled"] = True
    next_nanlong_time, timer_dirty = _parse_manual_toggle_next_time("南陇侯", "next_nanlong_time")
    if timer_dirty or next_nanlong_time > now:
        return
    clear_nanlong_state(persist=False)


def _disable_concubine_module_state():
    state["concubine_enabled"] = False
    clear_concubine_state(persist=False, keep_last_error=True)


def _manual_disable_concubine_module_state():
    state["concubine_enabled"] = False
    clear_concubine_state(persist=False, keep_last_error=True)


def _manual_enable_concubine_module_state(now):
    state["concubine_enabled"] = True
    if float(state.get("next_concubine_time", 0) or 0) > now:
        return
    clear_concubine_state(persist=False)
    restore_concubine_runtime(now)


def _manual_disable_concubine_tianji_module_state():
    state["concubine_tianji_enabled"] = False
    clear_concubine_tianji_state(persist=False, keep_last_error=True)


def _manual_enable_concubine_tianji_module_state(now):
    state["concubine_tianji_enabled"] = True
    state["concubine_tianji_last_error"] = ""
    next_time = float(state.get("next_concubine_time", 0) or 0)
    if next_time <= 0 or next_time > now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC:
        state["next_concubine_time"] = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC
    restore_concubine_runtime(now)


def _manual_disable_concubine_heart_module_state():
    state["concubine_heart_enabled"] = False
    state["concubine_heart_msg_id"] = 0
    state["concubine_heart_prompt_msg_id"] = 0
    state["concubine_heart_round"] = 0
    state["concubine_heart_choice_prompt_msg_id"] = 0
    state["concubine_heart_choice_round"] = 0
    state["concubine_heart_choice_sent_at"] = 0
    if state.get("concubine_phase") in {"heart_pending", "heart_choice_pending", "heart_choice_reply_pending"}:
        state["concubine_phase"] = "idle"


def _manual_enable_concubine_heart_module_state(now):
    state["concubine_heart_enabled"] = True
    state["concubine_heart_last_error"] = ""
    next_time = float(state.get("next_concubine_time", 0) or 0)
    if next_time <= 0 or next_time > now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC:
        state["next_concubine_time"] = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC
    restore_concubine_runtime(now)


def _manual_disable_concubine_voyage_module_state():
    state["concubine_voyage_enabled"] = False
    state["concubine_voyage_msg_id"] = 0
    state["concubine_voyage_retry_count"] = 0
    if state.get("concubine_phase") in {"voyage_pending", "voyage_return_pending"}:
        state["concubine_phase"] = "idle"
    _clear_pending_tasks_by_commands({CMD_CONCUBINE_VOYAGE, CMD_CONCUBINE_VOYAGE_RETURN, CMD_CONCUBINE_VOYAGE_STATUS})


def _manual_enable_concubine_voyage_module_state(now):
    state["concubine_voyage_enabled"] = True
    state["concubine_voyage_last_error"] = ""
    next_time = float(state.get("next_concubine_time", 0) or 0)
    if next_time <= 0 or next_time > now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC:
        state["next_concubine_time"] = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC
    restore_concubine_runtime(now)


def _manual_disable_second_soul_module_state():
    _disable_second_soul_module_state()


def _manual_enable_second_soul_module_state(now):
    state["second_soul_enabled"] = True
    state["second_soul_last_error"] = ""
    _restore_second_soul_runtime(now)


def _manual_disable_taiyi_module_state():
    _disable_taiyi_module_state()


def _manual_enable_taiyi_module_state(now):
    state["taiyi_enabled"] = True
    state["taiyi_last_error"] = ""
    _restore_taiyi_runtime(now)


def _disable_small_world_module_state():
    state["small_world_enabled"] = False
    clear_small_world_state(persist=False, keep_last_error=True)
    _clear_pending_tasks_by_commands({CMD_SMALL_WORLD_PREACH, CMD_SMALL_WORLD_RELIEF, CMD_SMALL_WORLD_QUERY, CMD_SMALL_WORLD_MANIFEST, CMD_SMALL_WORLD_HARVEST, CMD_SMALL_WORLD_REFINE, CMD_SMALL_WORLD_BARRIER})


def _manual_disable_small_world_module_state():
    state["small_world_enabled"] = False
    clear_small_world_state(persist=False, keep_last_error=True)
    _clear_pending_tasks_by_commands({CMD_SMALL_WORLD_PREACH, CMD_SMALL_WORLD_RELIEF, CMD_SMALL_WORLD_QUERY, CMD_SMALL_WORLD_MANIFEST, CMD_SMALL_WORLD_HARVEST, CMD_SMALL_WORLD_REFINE, CMD_SMALL_WORLD_BARRIER})


def _manual_enable_small_world_module_state(now):
    state["small_world_enabled"] = True
    has_runtime = str(state.get("small_world_phase") or "idle") != "idle" or any(
        int(state.get(key, 0) or 0) > 0
        for key in (
            "small_world_preach_reply_to_msg_id",
            "small_world_query_msg_id",
            "small_world_manifest_msg_id",
            "small_world_harvest_msg_id",
            "small_world_refine_msg_id",
        )
    )
    if not has_runtime and float(state.get("next_small_world_time", 0) or 0) > now:
        return
    schedule_small_world_initial_check(now, persist=False, keep_last_error=True)


def _manual_disable_yuanying_module_state():
    state["yuanying_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_YUANYING, CMD_YUANYING_STATUS})


def _manual_enable_yuanying_module_state(now):
    state["yuanying_enabled"] = True
    if float(state.get("next_yuanying_time", 0) or 0) > now:
        return
    _set_yuanying_module_enabled(True, now)


def _manual_disable_wendao_module_state():
    _disable_wendao_module_state()


def _manual_enable_wendao_module_state(now):
    state["wendao_enabled"] = True
    state["wendao_last_error"] = ""
    if float(state.get("next_wendao_time", 0) or 0) > now:
        return
    state["wendao_reply_to_msg_id"] = 0
    state["wendao_reply_due_at"] = 0
    state["wendao_pending_result_msg_id"] = 0
    state["wendao_sent_at"] = 0
    state["next_wendao_time"] = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC


def _manual_disable_mulan_module_state():
    _disable_mulan_module_state()


def _manual_enable_mulan_module_state(now):
    state["mulan_enabled"] = True
    state["mulan_last_error"] = ""
    if float(state.get("next_mulan_time", 0) or 0) > now:
        return
    state["mulan_phase"] = "idle"
    state["mulan_reply_to_msg_id"] = 0
    state["mulan_reply_due_at"] = 0
    state["mulan_pending_ids"] = ""
    state["mulan_current_id"] = 0
    state["mulan_public_id"] = 0
    state["mulan_sent_at"] = 0
    state["next_mulan_time"] = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC


def _manual_disable_duel_module_state():
    _disable_duel_module_state()


def _manual_enable_duel_module_state(now):
    state["duel_enabled"] = True
    state["duel_last_error"] = ""
    if float(state.get("next_duel_time", 0) or 0) > now:
        return
    state["duel_reply_to_msg_id"] = 0
    state["duel_reply_due_at"] = 0
    state["duel_open_msg_id"] = 0
    state["duel_magic_due_at"] = 0
    state["duel_magic_sent_at"] = 0
    state["duel_started_at"] = 0
    state["next_duel_time"] = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC


def _manual_disable_fishing_module_state():
    _disable_fishing_module_state()


def _manual_enable_fishing_module_state(now):
    state["fishing_enabled"] = True
    state["fishing_last_error"] = ""
    if float(state.get("next_fishing_time", 0) or 0) > now:
        return
    schedule_fishing_initial_check(now, persist=False, keep_last_error=True)


def _clear_explore_rift_runtime():
    state["next_explore_rift_time"] = 0
    state["explore_rift_reply_to_msg_id"] = 0
    state["explore_rift_reply_due_at"] = 0
    state["explore_rift_pending_result_msg_id"] = 0
    state["explore_rift_last_msg_id"] = 0
    state["explore_rift_nascent_escape_weak_until"] = 0
    state["explore_rift_rebirth_required"] = False
    state["explore_rift_rebirth_phase"] = "idle"
    state["explore_rift_rebirth_due_at"] = 0
    state["explore_rift_rebirth_request_msg_id"] = 0
    state["explore_rift_rebirth_options_msg_id"] = 0
    state["explore_rift_rebirth_select_msg_id"] = 0
    state["explore_rift_rebirth_options_text"] = ""
    state["explore_rift_rebirth_selected_index"] = 0
    state["explore_rift_fatal_msg_id"] = 0
    state["explore_rift_fatal_confirm_due_at"] = 0
    _clear_pending_tasks_by_commands({CMD_EXPLORE_RIFT, CMD_REBIRTH_REQUEST, CMD_REBIRTH_SELECT_PREFIX})


def _manual_disable_explore_rift_module_state():
    state["explore_rift_enabled"] = False
    _clear_explore_rift_runtime()
    clear_explore_rift_state(persist=False, keep_last_error=True)


def _manual_enable_explore_rift_module_state(now):
    state["explore_rift_enabled"] = True
    if float(state.get("next_explore_rift_time", 0) or 0) > now:
        state["explore_rift_manual_required"] = False
        return
    schedule_explore_rift_initial_check(now, persist=False, keep_last_error=True)
    state["explore_rift_manual_required"] = False


def _manual_disable_world_boss_module_state():
    state["world_boss_enabled"] = False
    clear_world_boss_identity_state(persist=False)
    _clear_pending_tasks_by_commands({CMD_WORLD_BOSS_STATUS, CMD_QINGYUANZI_BREAK, CMD_QINGYUANZI_SUPPRESS, CMD_QINGYUANZI_GUARD, CMD_QINGYUANZI_ATTACK})


def _manual_enable_world_boss_module_state(now):
    state["world_boss_enabled"] = True
    clear_world_boss_identity_state(persist=False, keep_last_error=False)


def get_explore_rift_status_text():
    return get_explore_rift_feature_status_text()


def _set_checkin_module_enabled(enabled, now):
    state["checkin_enabled"] = bool(enabled)
    if enabled:
        day_key = get_checkin_day_key(now)
        if state["checkin_teach_day"] != day_key:
            reset_checkin_daily_state(now)
        if state["last_checkin_done_day"] == day_key:
            schedule_next_checkin_after_completion(now, persist=False)
        elif _is_within_module_window("点卯", now):
            state["next_checkin_time"] = now
        else:
            _schedule_module_next_window_after_enable("点卯", now)
        return
    state["next_checkin_time"] = 0
    state["last_checkin_msg_id"] = 0
    _clear_pending_tasks_by_commands({CMD_CHECKIN})


def _set_tower_module_enabled(enabled, now):
    state["tower_enabled"] = bool(enabled)
    if enabled:
        day_key = get_day_key(now)
        if state["last_tower_day"] != day_key:
            state["last_tower_day"] = ""
            state["last_tower_msg_id"] = 0
            state["tower_reply_due_at"] = 0
            state["tower_retry_count"] = 0
        if state["last_tower_day"] == day_key:
            schedule_next_tower_after_completion(now, persist=False)
        elif _is_within_module_window("闯塔", now):
            state["next_tower_time"] = now
        else:
            _schedule_module_next_window_after_enable("闯塔", now)
        return
    state["next_tower_time"] = 0
    state["last_tower_msg_id"] = 0
    state["tower_reply_due_at"] = 0
    state["tower_retry_count"] = 0
    _clear_pending_tasks_by_commands({CMD_TOWER})


def _set_yuanying_module_enabled(enabled, now):
    if enabled:
        state["yuanying_enabled"] = True
        state["yuanying_phase"] = "idle"
        state["yuanying_probe_pending"] = False
        state["yuanying_summary_sent_at"] = 0
        state["last_yuanying_summary_msg_id"] = 0
        state["yuanying_waiting_logged"] = False
        state["yuanying_protect_logged"] = False
        state["last_yuanying_command_time"] = 0
        state["next_yuanying_time"] = now
        return
    _disable_yuanying_module_state()


def _disable_deep_retreat_module_state():
    state["deep_retreat_enabled"] = False
    state["deep_retreat_phase"] = "idle"
    state["next_deep_retreat_time"] = 0
    state["deep_retreat_probe_pending"] = False
    state["deep_retreat_summary_sent_at"] = 0
    state["last_deep_retreat_summary_msg_id"] = 0
    state["last_deep_retreat_command_time"] = 0
    state["deep_retreat_waiting_logged"] = False
    state["deep_retreat_protect_logged"] = False
    _clear_pending_tasks_by_commands({CMD_DEEP_RETREAT, CMD_DEEP_RETREAT_QUERY})


def _set_deep_retreat_module_enabled(enabled, now):
    if enabled:
        state["deep_retreat_enabled"] = True
        state["deep_retreat_phase"] = "idle"
        state["deep_retreat_probe_pending"] = False
        state["deep_retreat_summary_sent_at"] = 0
        state["last_deep_retreat_summary_msg_id"] = 0
        state["deep_retreat_waiting_logged"] = False
        state["deep_retreat_protect_logged"] = False
        state["last_deep_retreat_command_time"] = 0
        state["next_deep_retreat_time"] = now
        return
    _disable_deep_retreat_module_state()


def _manual_disable_deep_retreat_module_state():
    state["deep_retreat_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_DEEP_RETREAT, CMD_DEEP_RETREAT_QUERY})


def _manual_enable_deep_retreat_module_state(now):
    state["deep_retreat_enabled"] = True
    if float(state.get("next_deep_retreat_time", 0) or 0) > now:
        return
    _set_deep_retreat_module_enabled(True, now)


PENDING_TASK_COMMAND_TO_MODULE = {
    CMD_TREE_WATER: "灵树",
    CMD_TREE_GUARD: "灵树",
    CMD_TREE_STATUS: "灵树",
    CMD_TREE_PULSE_STATUS: "灵树",
    CMD_TREE_PULSE: "灵树",
    CMD_TREE_HARVEST: "灵树",
    CMD_PET: "法宝",
    CMD_PET_WARM: "温养器灵",
    CMD_PET_TRIAL: "器灵试炼",
    CMD_PET_FORMATION: "布下剑阵",
    CMD_STARGAZER_PANEL: "观星台",
    CMD_STARGAZER_GUIDE: "观星台",
    CMD_STARGAZER_SOOTHE: "观星台",
    CMD_STARGAZER_COLLECT: "观星台",
    CMD_GUANXING: "观星",
    CMD_GUANXING_SHIFT: "观星",
    CMD_FORMATION_START: "周天星斗",
    CMD_FORMATION_ASSIST: "周天星斗",
    CMD_TIANTI_STATUS: "登天阶",
    CMD_TIANTI_WENXIN: "登天阶",
    CMD_TIANTI_CLIMB: "登天阶",
    CMD_TIANTI_GANGFENG: "登天阶",
    CMD_HEHUAN_RETREAT: "合欢宗",
    CMD_HEHUAN_CONTRACT: "合欢宗",
    CMD_HEHUAN_DUAL: "合欢宗",
    CMD_HEHUAN_SEAL: "合欢宗",
    CMD_HEHUAN_ESCAPE: "合欢宗",
    CMD_QUIZ_ANSWER: "玄骨考校",
    CMD_CONCUBINE_STATUS: "侍妾",
    CMD_CONCUBINE_DAILY_GREET: "侍妾",
    CMD_CONCUBINE_DREAM: "侍妾",
    CMD_CONCUBINE_FRAGMENT: "侍妾",
    CMD_CONCUBINE_PUZZLE: "侍妾",
    CMD_CONCUBINE_SECT_MARRY: "侍妾",
    CMD_CONCUBINE_ROMANCE: "侍妾",
    CMD_CONCUBINE_TIANJI: "天机代卜",
    CMD_CONCUBINE_HEART: "共历心劫",
    CMD_CONCUBINE_VOYAGE: "侍妾远航",
    CMD_CONCUBINE_VOYAGE_RETURN: "侍妾远航",
    CMD_CONCUBINE_VOYAGE_STATUS: "侍妾远航",
    CMD_NANLONG_EXCHANGE_FABAO: "南陇侯",
    CMD_NANLONG_EXCHANGE_GONGFA: "南陇侯",
    CMD_NANLONG_REJECT: "南陇侯",
    CMD_SMALL_WORLD_QUERY: "小世界",
    CMD_SMALL_WORLD_MANIFEST: "小世界",
    CMD_SMALL_WORLD_HARVEST: "小世界",
    CMD_SMALL_WORLD_REFINE: "小世界",
    CMD_SMALL_WORLD_PREACH: "小世界",
    CMD_SMALL_WORLD_RELIEF: "小世界",
    CMD_SMALL_WORLD_BARRIER: "小世界",
    CMD_WORLD_BOSS_STATUS: "真仙试锋",
    CMD_QINGYUANZI_BREAK: "真仙试锋",
    CMD_QINGYUANZI_SUPPRESS: "真仙试锋",
    CMD_QINGYUANZI_GUARD: "真仙试锋",
    CMD_QINGYUANZI_ATTACK: "真仙试锋",
    CMD_RANCH: "放养",
    CMD_WILD_TRAINING: "野外历练",
    CMD_CHECKIN: "点卯",
    CMD_SECT_TEACH: "宗门传功",
    CMD_TOWER: "闯塔",
    CMD_YUANYING: "元婴",
    CMD_YUANYING_SECT_RETREAT: "元婴",
    CMD_YUANYING_STATUS: "元婴",
    CMD_EXPLORE_RIFT: "探寻裂缝",
    CMD_WENDAO: "问道",
    CMD_DUEL: "斗法",
    CMD_MULAN_SHADOW: "慕兰",
    CMD_MULAN_COLLECT: "慕兰",
    CMD_MULAN_JUDGE: "慕兰",
    CMD_MULAN_PUBLISH: "慕兰",
    CMD_NORMAL_RETREAT: "天星宗",
    CMD_DEEP_RETREAT_FORCE_EXIT: "深度闭关",
    CMD_USE_HEQI_DAN: "天星宗",
    CMD_EXCHANGE_HEQI_DAN_PREFIX: "天星宗",
    CMD_SECT_DONATE_LINGSHI_PREFIX: "天星宗",
    CMD_DEEP_RETREAT: "深度闭关",
    CMD_DEEP_RETREAT_QUERY: "深度闭关",
    CMD_DIVINATION: "卜筮问天",
    CMD_DIVINATION_EXCHANGE: "卜筮问天",
    CMD_SECOND_SOUL_STATUS: "第二元神",
    CMD_SECOND_SOUL_TRAIN: "第二元神",
    CMD_SECOND_SOUL_CHOICE_BREAK: "第二元神",
    CMD_SECOND_SOUL_CHOICE_STABLE: "第二元神",
    CMD_YINDAO: "太一",
    CMD_NODE_SEARCH: "太一",
    CMD_NODE_DEFINE: "太一",
}
MANUAL_MODULE_TOGGLE_HANDLERS = {
    "法宝": (_manual_enable_pet_module_state, _manual_disable_pet_module_state),
    "温养器灵": (_manual_enable_pet_warm_module_state, _manual_disable_pet_warm_module_state),
    "器灵试炼": (_manual_enable_pet_trial_module_state, _manual_disable_pet_trial_module_state),
    "布下剑阵": (_manual_enable_pet_formation_module_state, _manual_disable_pet_formation_module_state),
    "放养": (_manual_enable_ranch_module_state, _manual_disable_ranch_module_state),
    "野外历练": (_manual_enable_wild_training_module_state, _manual_disable_wild_training_module_state),
    "观星台": (_manual_enable_stargazer_module_state, _manual_disable_stargazer_module_state),
    "观星": (_manual_enable_guanxing_module_state, _manual_disable_guanxing_module_state),
    "观星监控": (_manual_enable_guanxing_monitor_module_state, _manual_disable_guanxing_monitor_module_state),
    "周天星斗": (_manual_enable_formation_module_state, _manual_disable_formation_module_state),
    "登天阶": (_manual_enable_tianti_module_state, _manual_disable_tianti_module_state),
    "玄骨考校": (_manual_enable_quiz_module_state, _manual_disable_quiz_module_state),
    "极阴祖师": (_manual_enable_jiyin_module_state, _manual_disable_jiyin_module_state),
    "侍妾": (_manual_enable_concubine_module_state, _manual_disable_concubine_module_state),
    "天机代卜": (_manual_enable_concubine_tianji_module_state, _manual_disable_concubine_tianji_module_state),
    "共历心劫": (_manual_enable_concubine_heart_module_state, _manual_disable_concubine_heart_module_state),
    "侍妾远航": (_manual_enable_concubine_voyage_module_state, _manual_disable_concubine_voyage_module_state),
    "合欢宗": (_manual_enable_hehuan_module_state, _manual_disable_hehuan_module_state),
    "天星宗": (_manual_enable_tianxing_module_state, _manual_disable_tianxing_module_state),
    "阴罗宗": (_manual_enable_yinluo_module_state, _manual_disable_yinluo_module_state),
    "慕兰": (_manual_enable_mulan_module_state, _manual_disable_mulan_module_state),
    "真仙试锋": (_manual_enable_world_boss_module_state, _manual_disable_world_boss_module_state),
    "南陇侯": (_manual_enable_nanlong_module_state, _manual_disable_nanlong_module_state),
    "小世界": (_manual_enable_small_world_module_state, _manual_disable_small_world_module_state),
    "探寻裂缝": (_manual_enable_explore_rift_module_state, _manual_disable_explore_rift_module_state),
    "点卯": (_manual_enable_checkin_module_state, _manual_disable_checkin_module_state),
    "宗门传功": (_manual_enable_sect_teach_module_state, _manual_disable_sect_teach_module_state),
    "闯塔": (_manual_enable_tower_module_state, _manual_disable_tower_module_state),
    "元婴": (_manual_enable_yuanying_module_state, _manual_disable_yuanying_module_state),
    "问道": (_manual_enable_wendao_module_state, _manual_disable_wendao_module_state),
    "斗法": (_manual_enable_duel_module_state, _manual_disable_duel_module_state),
    "灵溪垂钓": (_manual_enable_fishing_module_state, _manual_disable_fishing_module_state),
    "深度闭关": (_manual_enable_deep_retreat_module_state, _manual_disable_deep_retreat_module_state),
    "第二元神": (_manual_enable_second_soul_module_state, _manual_disable_second_soul_module_state),
    "太一": (_manual_enable_taiyi_module_state, _manual_disable_taiyi_module_state),
}
MODULE_DISABLE_HANDLERS = {
    "灵树": _disable_tree_module_state,
    "法宝": _disable_pet_module_state,
    "温养器灵": _manual_disable_pet_warm_module_state,
    "器灵试炼": _manual_disable_pet_trial_module_state,
    "布下剑阵": _manual_disable_pet_formation_module_state,
    "放养": _manual_disable_ranch_module_state,
    "野外历练": _manual_disable_wild_training_module_state,
    "观星台": _disable_stargazer_module_state,
    "观星": _disable_guanxing_module_state,
    "观星监控": _disable_guanxing_monitor_module_state,
    "周天星斗": _disable_formation_module_state,
    "登天阶": _disable_tianti_module_state,
    "玄骨考校": _disable_quiz_module_state,
    "极阴祖师": _disable_jiyin_module_state,
    "侍妾": _disable_concubine_module_state,
    "天机代卜": _manual_disable_concubine_tianji_module_state,
    "共历心劫": _manual_disable_concubine_heart_module_state,
    "侍妾远航": _manual_disable_concubine_voyage_module_state,
    "合欢宗": _disable_hehuan_module_state,
    "天星宗": _disable_tianxing_module_state,
    "阴罗宗": _disable_yinluo_module_state,
    "慕兰": _disable_mulan_module_state,
    "真仙试锋": _manual_disable_world_boss_module_state,
    "南陇侯": _disable_nanlong_module_state,
    "小世界": _disable_small_world_module_state,
    "元婴": _disable_yuanying_module_state,
    "探寻裂缝": _manual_disable_explore_rift_module_state,
    "问道": _disable_wendao_module_state,
    "斗法": _disable_duel_module_state,
    "灵溪垂钓": _disable_fishing_module_state,
    "深度闭关": _disable_deep_retreat_module_state,
    "第二元神": _disable_second_soul_module_state,
    "太一": _disable_taiyi_module_state,
    "点卯": _manual_disable_checkin_module_state,
    "宗门传功": _manual_disable_sect_teach_module_state,
    "闯塔": lambda: _set_tower_module_enabled(False, time.time()),
}
MODULE_STATE_SETTERS = {
    "checkin_enabled": _set_checkin_module_enabled,
    "tower_enabled": _set_tower_module_enabled,
    "yuanying_enabled": _set_yuanying_module_enabled,
    "deep_retreat_enabled": _set_deep_retreat_module_enabled,
}


def _get_module_display_name(module_name, send_as_id=None):
    return module_name


def _get_identity_account_offline_detail(send_as_id):
    account_id = int(get_identity_account(send_as_id) or 0)
    if account_id <= 0 or not is_account_offline(account_id):
        return 0, ""
    return account_id, get_account_offline_reason(account_id) or "账号不可用"


def _text_display_width(text):
    width = 0
    for char in str(text or ""):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _pad_display_width(text, target_width):
    text = str(text or "")
    return text + " " * max(0, int(target_width or 0) - _text_display_width(text))


def get_module_status_text(send_as_id=None):
    def status_dot(enabled, paused=False):
        if enabled and paused:
            return "🟡"
        return "🟢" if enabled else "🔴"

    def cell(enabled, name, paused=False):
        return f"{status_dot(enabled, paused=paused)} {name}"

    def row(cells, first_column_width=0):
        if not cells:
            return ""
        if len(cells) == 1:
            return cells[0]
        return f"{_pad_display_width(cells[0], first_column_width)}  ｜  {cells[1]}"

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    show_identity_header = len(target_ids) > 1 or (send_as_id is not None and len(get_identity_ids()) > 1)
    blocks = []
    for identity_id in target_ids:
        available_module_names = get_available_module_names(identity_id)
        offline_account_id, offline_reason = _get_identity_account_offline_detail(identity_id)
        with use_identity(identity_id):
            cells = [
                cell(
                    state[MODULE_KEY_MAP[module_name]],
                    _get_module_display_name(module_name, identity_id),
                    paused=bool(offline_account_id),
                )
                for module_name in available_module_names
            ]
            first_column_width = max(
                (_text_display_width(cells[index]) for index in range(0, len(cells), 2)),
                default=0,
            )
            rows = [
                row(cells[index:index + 2], first_column_width)
                for index in range(0, len(cells), 2)
            ]
            body = "📋 模块状态"
            if offline_account_id:
                body += (
                    f"\n⏸ 账号离线：acc={offline_account_id}，该身份调度已跳过。"
                    f"\n原因：{offline_reason}"
                )
            if rows:
                body += "\n" + "\n".join(rows)
            else:
                body += "\n- 当前无可用模块"
        if show_identity_header:
            body = f"👤 {get_identity_display_name(identity_id)}\n{body}"
        blocks.append(body)

    if not blocks:
        return "📋 模块状态\n- 无身份配置"
    if len(blocks) == 1:
        return blocks[0]
    return "📋 模块状态总览\n\n" + "\n\n".join(blocks)


def split_long_text(text, limit=3200):
    raw = str(text or "")
    if len(raw) <= limit:
        return [raw]
    chunks = []
    current = ""
    for block in raw.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(block[:cut])
            block = block[cut:].lstrip("\n")
        current = block
    if current:
        chunks.append(current)
    return chunks or [raw[:limit]]


async def reply_long_log_group_message(
    event,
    text,
    *,
    error_prefix="❌ 日志群回复失败",
    scope="global",
    limit=3200,
    link_preview=True,
    parse_mode=None,
    preformatted=False,
):
    chunks = split_long_text(text, limit=limit)
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        suffix = f"\n\n({index}/{total})" if total > 1 else ""
        ok = await reply_log_group_message(
            event,
            f"{chunk}{suffix}",
            error_prefix=error_prefix,
            scope=scope,
            limit=limit + 32,
            link_preview=link_preview,
            parse_mode=parse_mode,
            preformatted=preformatted,
        )
        if not ok:
            return False
    return True


def get_dungeon_join_status_text(send_as_id=None):
    now = time.time()
    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    records = get_dungeon_join_run_state()
    records = records if isinstance(records, dict) else {}
    lines = ["🧩 自动副本状态"]
    if not target_ids:
        lines.append("- 无身份配置")
    for identity_id in target_ids:
        record = records.get(str(int(identity_id))) if isinstance(records, dict) else {}
        record = record if isinstance(record, dict) else {}
        with use_identity(identity_id):
            enabled = bool(state.get("dungeon_join_enabled"))
            offline_account_id, offline_reason = _get_identity_account_offline_detail(identity_id)

        status_parts = ["开启" if enabled else "关闭"]
        pending_until = float(record.get("pending_until", 0) or 0)
        cooldown_until = float(record.get("cooldown_until", 0) or 0)
        active_until = float(record.get("active_until", 0) or 0)
        participating = bool(record.get("participating"))
        if offline_account_id:
            status_parts.append(f"账号离线 acc={offline_account_id}")
        if pending_until > now:
            room_id = record.get("pending_room_id") or record.get("room_id") or "-"
            status_parts.append(f"等待回复 房间 {room_id} 至 {fmt_abs_ts(pending_until)}（{fmt_remaining(pending_until)}）")
        elif cooldown_until > now:
            room_id = record.get("room_id") or "-"
            status_parts.append(f"冷却中 房间 {room_id} 至 {fmt_abs_ts(cooldown_until)}（{fmt_remaining(cooldown_until)}）")
        elif participating and active_until > now:
            room_id = record.get("room_id") or "-"
            status_parts.append(f"副本中 房间 {room_id} 至 {fmt_abs_ts(active_until)}（{fmt_remaining(active_until)}）")
        elif record.get("last_result"):
            detail = str(record.get("last_error") or record.get("last_result") or "").strip()
            status_parts.append(f"上次结果 {detail or record.get('last_result')}")
        else:
            status_parts.append("空闲")
        if offline_account_id and offline_reason:
            status_parts.append(f"原因 {offline_reason}")
        lines.append(f"- {get_identity_display_name(identity_id)}: " + "｜".join(status_parts))

    inbox_items = get_dungeon_join_inbox_snapshot(limit=5)
    dispatch_group_count = len(get_replica_dispatch_group_ids())
    lines.extend(["", "最近房间公告:"])
    if inbox_items:
        for item in inbox_items[-5:]:
            lines.append(
                "- "
                f"{item.get('dungeon_name') or '副本'} {item.get('dungeon_id') or '-'} "
                f"cmd {item.get('join_command') or '-'} msg {item.get('msg_id') or 0}"
            )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "副本群轻量指令:",
            "- .查询副本",
            "- .查询昆 / .查询虚 / .查询苍",
            "- .副本cd",
            "- .副本帮助",
            "- .开启副本 @用户名 <虚天|苍坤|坠魔|黄龙|昆吾>",
            "- .加入副本 @用户名 @用户名",
            "- .解散副本",
            "",
            f"主线拉人群: {dispatch_group_count} 个（已停用，仅保留配置）",
            "- 外部拉人指令会被识别并跳过，不发送加入命令",
        ]
    )
    return "\n".join(lines)


def get_single_module_status_text(module_name, send_as_id=None):
    if module_name == "自动副本":
        return get_dungeon_join_status_text(send_as_id)

    status_map = {
        "灵树": get_tree_status_text,
        "法宝": get_pet_status_text,
        "温养器灵": get_pet_status_text,
        "器灵试炼": get_pet_status_text,
        "布下剑阵": get_pet_status_text,
        "放养": get_ranch_status_text,
        "野外历练": get_wild_training_status_text,
        "观星台": get_stargazer_status_text,
        "观星": get_guanxing_status_text,
        "观星监控": get_guanxing_monitor_status_text,
        "周天星斗": get_formation_status_text,
        "登天阶": get_tianti_status_text,
        "玄骨考校": get_quiz_status_text,
        "极阴祖师": get_jiyin_status_text,
        "侍妾": get_concubine_status_text,
        "天机代卜": get_concubine_status_text,
        "共历心劫": get_concubine_status_text,
        "侍妾远航": get_concubine_status_text,
        "合欢宗": get_hehuan_status_text,
        "天星宗": get_tianxing_status_text,
        "阴罗宗": get_yinluo_status_text,
        "慕兰": get_mulan_status_text,
        "真仙试锋": get_world_boss_status_text,
        "南陇侯": get_nanlong_status_text,
        "小世界": get_small_world_status_text,
        "元婴": get_yuanying_status_detail_text,
        "探寻裂缝": get_explore_rift_status_text,
        "问道": get_wendao_status_text,
        "斗法": get_duel_status_text,
        "灵溪垂钓": get_fishing_status_text,
        "深度闭关": get_deep_retreat_status_detail_text,
        "卜筮问天": get_divination_status_text,
        "点卯": get_checkin_status_text,
        "宗门传功": get_sect_teach_status_text,
        "闯塔": get_tower_status_text,
        "第二元神": get_second_soul_status_text,
        "太一": get_taiyi_status_text,
    }
    getter = status_map.get(module_name)
    if not getter:
        return "❌ 未知模块"
    if module_name == "观星监控":
        return getter()

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    blocks = []
    for identity_id in target_ids:
        unavailable_reason = get_module_unavailable_reason(module_name, identity_id)
        if unavailable_reason:
            body = f"❌ {unavailable_reason}"
        else:
            with use_identity(identity_id):
                module_key = MODULE_KEY_MAP.get(module_name)
                offline_account_id, offline_reason = _get_identity_account_offline_detail(identity_id)
                if offline_account_id and module_key and state.get(module_key, False):
                    body = (
                        f"⏸ 账号离线：acc={offline_account_id}，调度已跳过。\n"
                        f"原因：{offline_reason}\n"
                        "说明：本地 timer 可能显示已到期，但离线账号不会发送指令，不属于漏发。"
                    )
                else:
                    body = getter()
        if len(target_ids) > 1 or (send_as_id is not None and len(get_identity_ids()) > 1):
            body = f"👤 {get_identity_display_name(identity_id)}\n{body}"
        blocks.append(body)

    if len(blocks) == 1:
        return blocks[0]
    return "\n\n".join(blocks)


def _format_log_group_card_html(title, body, *, note=None):
    body_text = str(body or "").strip() or "-"
    title_text = html.escape(str(title or "状态"))
    escaped_body = html.escape(body_text)
    lines = [f"<b>{title_text}</b>", f"<pre>{escaped_body}</pre>"]
    if note:
        lines.append(html.escape(str(note)))
    return "\n".join(lines)


async def _reply_log_group_card(event, title, body, *, error_prefix, buttons=None):
    chunks = split_long_text(str(body or ""), limit=2800)
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        chunk_title = f"{title} ({index}/{total})" if total > 1 else title
        chunk_buttons = buttons if index == 1 else None
        ok = await reply_log_group_message(
            event,
            _format_log_group_card_html(chunk_title, chunk),
            error_prefix=error_prefix,
            link_preview=False,
            scope="global",
            parse_mode="HTML",
            preformatted=True,
            limit=3200,
            buttons=chunk_buttons,
        )
        if not ok:
            return False
    return True


def _format_analysis_count(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value or 0)


def _analysis_list(value):
    return value if isinstance(value, list) else []


def _short_analysis_text(value, limit=96):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _format_analysis_mtime():
    try:
        mtime = ANALYSIS_PAYLOAD_FILE.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(mtime, TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S")


def _load_analysis_payload():
    if not ANALYSIS_PAYLOAD_FILE.exists():
        return None, (
            "离线分析报告不存在。\n"
            f"路径: {ANALYSIS_PAYLOAD_FILE}\n"
            "先离线运行: tools/analyze_game_records.py --run-name latest"
        )
    try:
        payload = json.loads(ANALYSIS_PAYLOAD_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"离线分析报告 JSON 损坏: {exc}"
    except OSError as exc:
        return None, f"读取离线分析报告失败: {exc}"
    if not isinstance(payload, dict):
        return None, "离线分析报告格式异常: 顶层不是 object。"
    return payload, None


def _format_analysis_rows(rows, *, limit=8, key_field="key", count_field="count"):
    rows = _analysis_list(rows)
    if not rows:
        return ["- 无"]
    lines = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        key = str(row.get(key_field) or "-")
        count = _format_analysis_count(row.get(count_field, 0))
        lines.append(f"- {key}: {count}")
    return lines or ["- 无"]


def _analysis_date_range(summary):
    dates = []
    for row in _analysis_list((summary or {}).get("dates")):
        key = str(row.get("key") or "").strip()
        if key:
            dates.append(key)
    if not dates:
        return "未知"
    dates = sorted(dates)
    if dates[0] == dates[-1]:
        return dates[0]
    return f"{dates[0]} 到 {dates[-1]}"


def _format_analysis_summary_text(payload):
    summary = payload.get("summary") or {}
    health = payload.get("health") or {}
    miniweb = payload.get("miniweb") or {}
    commands = _analysis_list(payload.get("commands"))
    source_files = _analysis_list(summary.get("source_files"))
    hard_stop_hits = _analysis_list(summary.get("hard_stop_hits"))
    raw_messages = miniweb.get("raw_messages") or {}
    mtime = _format_analysis_mtime()

    lines = [
        "离线报告: data/analysis/latest",
        f"生成时间: {mtime or '未知'}",
        f"扫描行数: {_format_analysis_count(summary.get('scanned_lines'))}",
        f"无效 JSON: {_format_analysis_count(summary.get('invalid_json'))}",
        f"日志文件: {_format_analysis_count(len(source_files))}",
        f"日期范围: {_analysis_date_range(summary)}",
        f"命令种类: {_format_analysis_count(len(commands))}",
        f"自动发送: {_format_analysis_count(health.get('sent_total'))}",
        f"硬停关键词命中: {_format_analysis_count(len(hard_stop_hits))}",
        f"日志群 ID: {summary.get('log_group_id') or '未配置'}",
        f"webmini: {'可用' if miniweb.get('available') else '不可用'}",
    ]
    if miniweb.get("available"):
        lines.append(
            "webmini 消息: "
            f"{_format_analysis_count(raw_messages.get('count'))} "
            f"({raw_messages.get('min_date') or '?'} 到 {raw_messages.get('max_date') or '?'})"
        )

    lines.extend(["", "命令家族 Top:"])
    lines.extend(_format_analysis_rows(summary.get("command_families"), limit=10))
    lines.extend(["", "自动发送家族 Top:"])
    lines.extend(_format_analysis_rows(summary.get("sent_by_family"), limit=10))
    lines.extend(
        [
            "",
            "说明: 这是已落盘日志的只读摘要，不触发游戏发送，也不刷新实时状态。",
        ]
    )
    return "\n".join(lines)


def _format_analysis_health_text(payload):
    health = payload.get("health") or {}
    summary = payload.get("summary") or {}
    duplicate = _analysis_list(health.get("duplicate_short_gap"))
    one_sec = _analysis_list(health.get("any_short_gap"))
    missing = _analysis_list(health.get("missing_direct_replies_sample"))
    hard_stop_hits = _analysis_list(summary.get("hard_stop_hits"))

    lines = [
        "发送健康码（离线候选）",
        f"自动发送总数: {_format_analysis_count(health.get('sent_total'))}",
        f"同身份同命令 90 秒内重复样本: {_format_analysis_count(len(duplicate))}",
        f"同身份 1 秒内连续发送样本: {_format_analysis_count(len(one_sec))}",
        f"未找到直接 reply 的 sent 记录: {_format_analysis_count(health.get('missing_direct_replies_total'))}",
        f"硬停关键词命中: {_format_analysis_count(len(hard_stop_hits))}",
        "",
        "初判:",
        "- 风暴: 看短间隔和高频分钟，当前这里只列候选样本，不能单独定性。",
        "- 错发: 离线报告不直接做语义错发判定，优先从未知指令和未回复样本复核。",
        "- 漏发: missing_direct_replies 不等于漏发，部分游戏回复不是直接 reply。",
        "",
        "高频分钟 Top:",
    ]
    busiest = _analysis_list(health.get("busiest_minutes"))
    if busiest:
        for row in busiest[:8]:
            minute_text = ""
            try:
                minute_text = datetime.fromtimestamp(int(row.get("minute_epoch")) * 60, TZ_LOCAL).strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError, OSError, OverflowError):
                minute_text = str(row.get("minute_epoch") or "")
            lines.append(f"- sender {row.get('sender_id')}: {row.get('count')} 条 @ {minute_text}")
    else:
        lines.append("- 无")

    lines.extend(["", "重复样本:"])
    if duplicate:
        for row in duplicate[:6]:
            lines.append(
                "- "
                f"{row.get('cur_ts')} sender {row.get('sender_id')} "
                f"gap {row.get('gap_sec')}s: {row.get('cur_command')}"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "1 秒内连续样本:"])
    if one_sec:
        for row in one_sec[:6]:
            lines.append(
                "- "
                f"{row.get('cur_ts')} sender {row.get('sender_id')} "
                f"gap {row.get('gap_sec')}s: {row.get('prev_command')} -> {row.get('cur_command')}"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "未直接 reply 样本:"])
    if missing:
        for row in missing[:6]:
            lines.append(
                "- "
                f"{row.get('ts')} sender {row.get('sender_id')} "
                f"{row.get('command')} msg {row.get('message_id')} family {row.get('family')}"
            )
    else:
        lines.append("- 无")

    return "\n".join(lines)


def _format_analysis_log_group_text(payload):
    summary = payload.get("summary") or {}
    commands = _analysis_list(summary.get("log_group_commands"))
    total = sum(int(row.get("count") or 0) for row in commands if isinstance(row, dict))
    lines = [
        "日志群指令分析（离线）",
        f"限定 LOG_GROUP_ID: {summary.get('log_group_id') or '未配置'}",
        f"观察到的日志群点命令种类: {_format_analysis_count(len(commands))}",
        f"观察到的日志群点命令总数: {_format_analysis_count(total)}",
        "",
        "观察样本:",
    ]
    if commands:
        for row in commands[:20]:
            senders = ", ".join(
                f"{sender.get('key')}({sender.get('count')})"
                for sender in _analysis_list(row.get("top_senders"))[:3]
                if isinstance(sender, dict)
            )
            lines.append(
                "- "
                f"{row.get('command')}: {row.get('count')} 次，"
                f"{row.get('first_ts') or '?'} 到 {row.get('last_ts') or '?'}"
                + (f"，sender {senders}" if senders else "")
            )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "说明: 这里按真实日志群 chat_id 过滤，和游戏群里的点命令统计是两件事。",
        ]
    )
    return "\n".join(lines)


def _format_analysis_webmini_text(payload):
    miniweb = payload.get("miniweb") or {}
    static_inventory = payload.get("static_inventory") or {}
    if not miniweb.get("available"):
        return f"webmini DB 不可用: {miniweb.get('db_path') or '未知'}"

    raw = miniweb.get("raw_messages") or {}
    lines = [
        "webmini 可吸收内容（离线）",
        f"DB: {miniweb.get('db_path') or '未知'}",
        f"raw_messages: {_format_analysis_count(raw.get('count'))}",
        f"时间范围: {raw.get('min_date') or '?'} 到 {raw.get('max_date') or '?'}",
        "",
        "parser 注册:",
    ]
    parsers = _analysis_list(static_inventory.get("miniweb_parsers"))
    lines.extend([f"- {parser}" for parser in parsers[:20]] or ["- 无"])
    lines.extend(["", "raw 点命令 Top:"])
    if miniweb.get("top_raw_commands"):
        for row in _analysis_list(miniweb.get("top_raw_commands"))[:12]:
            lines.append(f"- {row.get('command')}: {row.get('count')}")
    else:
        lines.append("- 无")
    lines.extend(["", "resource_events Top:"])
    if miniweb.get("resource_events"):
        for row in _analysis_list(miniweb.get("resource_events"))[:10]:
            lines.append(
                "- "
                f"{row.get('source_type')} / {row.get('source_name')} / "
                f"{row.get('result')}: {row.get('count')}"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "近期消息样本:"])
    for row in _analysis_list(miniweb.get("latest_messages"))[:6]:
        lines.append(
            "- "
            f"{row.get('date')} {row.get('source')}({row.get('sender_id')}): "
            f"{_short_analysis_text(row.get('text'), 110)}"
        )
    if not miniweb.get("latest_messages"):
        lines.append("- 无")
    return "\n".join(lines)


def _format_analysis_unknown_text(payload):
    rows = _analysis_list(payload.get("unknown_commands"))
    lines = [
        "未知/未归类指令 Top",
        "用途: 补命令分类、找本地新增功能、排查错发候选。",
        "",
    ]
    lines.extend(_format_analysis_rows(rows, limit=30))
    return "\n".join(lines)


def _format_analysis_report_text(kind):
    payload, error = _load_analysis_payload()
    if error:
        return error
    formatters = {
        "summary": _format_analysis_summary_text,
        "health": _format_analysis_health_text,
        "log_group": _format_analysis_log_group_text,
        "webmini": _format_analysis_webmini_text,
        "unknown": _format_analysis_unknown_text,
    }
    return formatters[kind](payload)


def _format_staging_preflight_text():
    identity_ids = get_identity_ids()
    pending_total = 0
    pending_rows = []
    for identity_id in identity_ids:
        with use_identity(identity_id):
            pending_count = len(state.get("pending_tasks", {}) or {})
        if pending_count:
            pending_total += pending_count
            pending_rows.append(f"- {get_identity_display_name(identity_id)}: {pending_count} 个 pending")

    queue_items = get_game_send_queue_snapshot()
    low_total, low_kind_count = get_low_priority_audit_pending_counts()
    payload, analysis_error = _load_analysis_payload()

    risk_notes = []
    if pending_total:
        risk_notes.append(f"有 {pending_total} 个游戏 pending，先观察回复或等超时处理。")
    if queue_items:
        risk_notes.append(f"游戏发送队列还有 {len(queue_items)} 条，避免此时重启造成判断偏差。")
    if low_total:
        risk_notes.append(f"低优先级日志还有 {low_total} 条 / {low_kind_count} 类未汇总。")
    if not risk_notes:
        risk_notes.append("当前内存队列和 pending 未见明显阻塞。")

    lines = [
        "待上线预检",
        f"全局状态: {'启用' if get_global_enabled() else '暂停'}",
        f"身份数: {len(identity_ids)}",
        f"游戏 pending: {pending_total}",
        f"游戏发送队列: {len(queue_items)}",
        f"低优先级日志待汇总: {low_total} 条 / {low_kind_count} 类",
        "",
        "风险提示:",
        *[f"- {note}" for note in risk_notes],
        "",
        "已加守卫:",
        "- 游戏发送仍走队列，默认最多补发一次。",
        "- 元婴/深度闭关等待类日志去重位已持久化，重启后不重复刷低优先级提示。",
        "- 副本旧群调度只回迁移提示，不批量自动加入。",
    ]

    if pending_rows:
        lines.extend(["", "pending 分布:"])
        lines.extend(pending_rows[:10])

    if queue_items:
        lines.extend(["", "发送队列前 10:"])
        for item in queue_items[:10]:
            ready_in = int(item.get("ready_in_sec") or 0)
            lines.append(
                f"- {item.get('identity_name') or item.get('identity_id')}: "
                f"{item.get('cmd') or '-'} / {item.get('status') or '-'} / {ready_in}s"
            )

    lines.append("")
    if analysis_error:
        lines.extend(["离线分析: 未读取", _short_analysis_text(analysis_error, limit=160)])
    else:
        summary = payload.get("summary") or {}
        health = payload.get("health") or {}
        duplicate = _analysis_list(health.get("duplicate_short_gap"))
        one_sec = _analysis_list(health.get("any_short_gap"))
        lines.extend(
            [
                "离线分析摘要:",
                f"- 扫描行数: {_format_analysis_count(summary.get('scanned_lines'))}",
                f"- 自动发送: {_format_analysis_count(health.get('sent_total'))}",
                f"- 同身份 1 秒内连续发送样本: {_format_analysis_count(len(one_sec))}",
                f"- 同身份同命令 90 秒内重复样本: {_format_analysis_count(len(duplicate))}",
                f"- 报告时间: {_format_analysis_mtime() or '未知'}",
            ]
        )

    lines.extend(
        [
            "",
            "说明: 这是上线前只读预检，不触发游戏发送，也不修改 live。",
        ]
    )
    return "\n".join(lines)


RUNTIME_HEALTH_ERROR_KEYS = [
    ("wild_training_last_error", "野外历练", "wild_training_enabled"),
    ("small_world_last_error", "小世界", "small_world_enabled"),
    ("taiyi_last_error", "太一", "taiyi_enabled"),
    ("concubine_last_error", "侍妾", "concubine_enabled"),
    ("concubine_tianji_last_error", "侍妾天机", "concubine_tianji_enabled"),
    ("concubine_heart_last_error", "侍妾心法", "concubine_heart_enabled"),
    ("deep_retreat_last_error", "深度闭关", "deep_retreat_enabled"),
    ("yuanying_last_error", "元婴", "yuanying_enabled"),
    ("identity_info_last_error", "身份", ""),
    ("ranch_last_error", "放养", "ranch_enabled"),
    ("pet_last_error", "灵兽", "pet_enabled"),
    ("pet_warm_last_error", "温养", "pet_warm_enabled"),
    ("pet_trial_last_error", "器灵试炼", "pet_trial_enabled"),
    ("pet_formation_last_error", "布下剑阵", "pet_formation_enabled"),
    ("stargazer_last_error", "观星台", "stargazer_enabled"),
    ("tianti_last_error", "登天阶", "tianti_enabled"),
    ("nanlong_last_error", "南陇侯", "nanlong_enabled"),
    ("second_soul_last_error", "第二元神", "second_soul_enabled"),
]

RUNTIME_HEALTH_PHASE_KEYS = [
    ("wild_training_reply_to_msg_id", "野外历练待回复", "wild_training_enabled"),
    ("small_world_phase", "小世界", "small_world_enabled"),
    ("taiyi_phase", "太一", "taiyi_enabled"),
    ("concubine_phase", "侍妾", "concubine_enabled"),
]


def _format_runtime_counter_map(items, limit=6):
    if not items:
        return "无"
    ordered = sorted(items.items(), key=lambda pair: (-int(pair[1] or 0), str(pair[0])))
    return "、".join(f"{key}:{value}" for key, value in ordered[:limit])


def _load_health_observer_snapshot():
    try:
        if not HEALTH_OBSERVER_LATEST_FILE.exists():
            return None
        payload = json.loads(HEALTH_OBSERVER_LATEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _format_health_observer_summary_lines(snapshot):
    if not snapshot:
        return [
            "外部健康包: 未生成",
            f"路径: {HEALTH_OBSERVER_LATEST_FILE}",
            "提示: tools/health_observer.py --once 可生成只读审计包。",
        ]
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    risks = health.get("risk_reasons") if isinstance(health.get("risk_reasons"), list) else []
    business = snapshot.get("business") if isinstance(snapshot.get("business"), dict) else {}
    message_state = business.get("message_state") if isinstance(business.get("message_state"), dict) else {}
    db_state = business.get("db_state") if isinstance(business.get("db_state"), dict) else {}
    lines = [
        f"外部健康包: {snapshot.get('ts') or '-'}｜score={health.get('score', '-')}｜level={health.get('level', snapshot.get('status', '-'))}",
        f"风险原因: {len(risks)}",
        f"近窗发送: {message_state.get('sent_count', 0)} 条｜pending={db_state.get('pending_total', 0)}",
    ]
    if risks:
        lines.append("主要风险:")
        for item in risks[:5]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('severity', 'warn')}｜{_short_analysis_text(item.get('message'), 90)}")
    else:
        lines.append("主要风险: 无")
    return lines


def _format_observer_module_line(item):
    who = item.get("username") or item.get("label") or item.get("identity_id") or "-"
    details = "；".join(str(part) for part in (item.get("details") or [])[:4])
    return (
        f"- {who}: {item.get('module_label') or item.get('module')}｜"
        f"{item.get('status')}｜{_short_analysis_text(details or '-', 120)}"
    )


def _runtime_health_module_active(module_key):
    if not module_key:
        return True
    return bool(state.get(module_key, False))


def _format_runtime_health_text():
    now = time.time()
    observer_snapshot = _load_health_observer_snapshot()
    identity_ids = get_identity_ids()
    enabled_count = sum(1 for identity_id in identity_ids if get_identity_enabled(identity_id))
    pending_total = 0
    pending_rows = []
    error_rows = []
    phase_rows = []

    for identity_id in identity_ids:
        display_name = get_identity_display_name(identity_id)
        with use_identity(identity_id):
            pending_tasks = state.get("pending_tasks", {}) or {}
            if pending_tasks:
                pending_total += len(pending_tasks)
                samples = []
                for pending in list(pending_tasks.values())[:3]:
                    command = get_pending_command(pending) or "unknown"
                    sent_at = float((pending or {}).get("sent_at", 0) or 0)
                    age = int(max(0, now - sent_at)) if sent_at > 0 else 0
                    retry = int((pending or {}).get("retry", 0) or 0)
                    max_retry = int((pending or {}).get("max_retry", 0) or 0)
                    retry_text = f" retry={retry}/{max_retry}" if max_retry or retry else ""
                    age_text = f" {age}s" if age else ""
                    samples.append(f"{command}{age_text}{retry_text}".strip())
                pending_rows.append(f"- {display_name}: {len(pending_tasks)} 个｜{'；'.join(samples)}")

            for key, label, module_key in RUNTIME_HEALTH_ERROR_KEYS:
                if not _runtime_health_module_active(module_key):
                    continue
                value = str(state.get(key) or "").strip()
                if value:
                    error_rows.append(f"- {display_name}: {label}｜{_short_analysis_text(value, 90)}")

            for key, label, module_key in RUNTIME_HEALTH_PHASE_KEYS:
                if not _runtime_health_module_active(module_key):
                    continue
                value = state.get(key)
                if key.endswith("_reply_to_msg_id"):
                    try:
                        msg_id = int(value or 0)
                    except (TypeError, ValueError):
                        msg_id = 0
                    if msg_id > 0:
                        phase_rows.append(f"- {display_name}: {label} msg={msg_id}")
                    continue
                value_text = str(value or "").strip()
                if value_text and value_text not in {"idle", "normal"}:
                    phase_rows.append(f"- {display_name}: {label}={value_text}")

    queue_items = get_game_send_queue_snapshot()
    low_total, low_kind_count = get_low_priority_audit_pending_counts()
    inbox = get_passive_inbox_snapshot()

    lines = [
        "运行健康摘要",
        "常驻检测: 脚本内状态/消息盒子常驻采样；外部 safety watchdog 负责风暴与进程熔断。",
        "只读: 不触发游戏命令，不读取天机阁 API。",
        "",
        *_format_health_observer_summary_lines(observer_snapshot),
        "",
        f"全局状态: {'启用' if get_global_enabled() else '暂停'}",
        f"身份: {enabled_count}/{len(identity_ids)} 启用",
        f"游戏 pending: {pending_total}",
        f"游戏发送队列: {len(queue_items)}",
        f"低优先级日志待汇总: {low_total} 条 / {low_kind_count} 类",
        f"消息盒子: total={inbox.get('total', 0)} changed={inbox.get('changed', 0)} skipped={inbox.get('skipped', 0)} attention={inbox.get('attention_total', 0)}",
        f"命中模块: {_format_runtime_counter_map(inbox.get('modules') or {})}",
        f"待关注分类: {_format_runtime_counter_map(inbox.get('attention_by_class') or {})}",
        f"待关注原因: {_format_runtime_counter_map(inbox.get('attention_by_reason') or {})}",
        f"跳过原因: {_format_runtime_counter_map(inbox.get('skip_reasons') or {})}",
    ]

    if pending_rows:
        lines.extend(["", "pending 样本:"])
        lines.extend(pending_rows[:10])

    divination_pending_lines = get_divination_pending_health_lines(now, limit=8)
    if divination_pending_lines:
        lines.extend(["", "卜筮问天待回复检查:"])
        lines.extend(divination_pending_lines)

    if queue_items:
        lines.extend(["", "发送队列前 8:"])
        for item in queue_items[:8]:
            ready_in = int(item.get("ready_in_sec") or 0)
            lines.append(
                f"- {item.get('identity_name') or item.get('identity_id')}: "
                f"{item.get('cmd') or '-'} / {item.get('priority') or '-'} / {item.get('status') or '-'} / {ready_in}s"
            )

    if phase_rows:
        lines.extend(["", "非空阶段:"])
        lines.extend(phase_rows[:12])

    if error_rows:
        lines.extend(["", "模块 last_error:"])
        lines.extend(error_rows[:12])
    else:
        lines.extend(["", "模块 last_error: 无"])

    recent = inbox.get("recent") or []
    if recent:
        lines.extend(["", "消息盒子最近证据:"])
        for item in recent[-5:]:
            module_name = item.get("module") or item.get("reason") or "unknown"
            identity_text = item.get("identity_id") or "-"
            msg_text = item.get("source_message_id") or item.get("msg_id") or "-"
            route_text = item.get("route_source") or "-"
            summary = item.get("summary") or item.get("matched_text") or ""
            lines.append(f"- {module_name}｜id={identity_text}｜msg={msg_text}｜{route_text}｜{_short_analysis_text(summary, 72)}")

    return "\n".join(lines)


def _format_runtime_health_detail_text():
    snapshot = _load_health_observer_snapshot()
    lines = [
        "运行健康详情",
        "只读: 来自 health_observer/latest.json；不触发游戏命令，不重启服务。",
        "",
    ]
    if not snapshot:
        lines.extend(_format_health_observer_summary_lines(None))
        return "\n".join(lines)

    lines.extend(_format_health_observer_summary_lines(snapshot))
    business = snapshot.get("business") if isinstance(snapshot.get("business"), dict) else {}
    db_state = business.get("db_state") if isinstance(business.get("db_state"), dict) else {}
    message_state = business.get("message_state") if isinstance(business.get("message_state"), dict) else {}
    module_summary = db_state.get("module_summary") if isinstance(db_state.get("module_summary"), list) else []
    abnormal = [item for item in module_summary if isinstance(item, dict) and item.get("status") in {"error", "warn"}]
    active = [item for item in module_summary if isinstance(item, dict) and item.get("status") == "active"]

    lines.extend(["", "异常模块:"])
    if abnormal:
        lines.extend(_format_observer_module_line(item) for item in abnormal[:12])
    else:
        lines.append("- 无")

    if active:
        lines.extend(["", "活跃模块样本:"])
        lines.extend(_format_observer_module_line(item) for item in active[:8])

    repeats = message_state.get("repeated_command_samples") if isinstance(message_state.get("repeated_command_samples"), list) else []
    if repeats:
        lines.extend(["", "重复命令样本:"])
        for item in repeats[:6]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('identity_id')}: {item.get('command')} x{item.get('count')}")

    evidence_refs = snapshot.get("evidence_refs") if isinstance(snapshot.get("evidence_refs"), list) else []
    lines.extend(["", "证据入口:"])
    if evidence_refs:
        for item in evidence_refs[:8]:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or "evidence"
            if kind == "message_log":
                lines.append(f"- message_log: {item.get('path')}｜sent={item.get('sent_count')}｜last={item.get('last_sent_ts')}")
            elif kind == "state_db":
                lines.append(f"- state_db: {item.get('path')}｜pending={item.get('pending_total')}")
            elif kind == "journal":
                lines.append(f"- journal: {item.get('service')}｜hard={item.get('hard_count')} warn={item.get('warn_count')}｜since={item.get('since')}")
            elif kind == "repeat_sample":
                lines.append(f"- repeat: {item.get('identity_id')} {item.get('command')} x{item.get('count')}")
            else:
                lines.append(f"- {kind}: {_short_analysis_text(item, 120)}")
    else:
        lines.append("- 无")
    lines.append(f"Markdown: {HEALTH_OBSERVER_LATEST_MD_FILE}")
    return "\n".join(lines)


def _format_log_group_help_html(send_as_id=None):
    suffix = ""
    if send_as_id is not None:
        suffix = f" @{get_identity_display_name(send_as_id)}"
    status_aliases = {"登天阶": ".天阶状态", "自动副本": ".自动副本状态"}
    module_commands = [".状态", ".消息盒子状态", ".消息盒子shadow", ".消息契约"] + [
        status_aliases.get(module_name, f".{module_name}状态")
        for module_name in MODULE_NAMES
    ]
    control_commands = [
        ".全局暂停",
        ".全局恢复",
        ".登录",
        ".开启/关闭<模块名>",
        ".开启全部 / .关闭全部",
    ]
    storage_commands = [
        ".储物袋汇总",
        ".储物袋盘点",
        ".材料汇总",
        ".还有多少 <物品名>",
        ".更新储物袋",
    ]
    analysis_commands = [
        ".上线预检",
        ".运行健康",
        ".健康详情",
        ".玩法总览",
        ".发送健康码",
        ".日志群分析",
        ".webmini分析",
        ".未知指令",
    ]
    audit_commands = [
        ".日志推送状态",
        ".发送日志汇总",
    ]
    replica_group_commands = [
        ".查询副本",
        ".查询昆 / .查询虚 / .查询苍",
        ".副本cd",
        ".副本帮助",
        ".开启副本 @用户名 <虚天|苍坤|坠魔|黄龙|昆吾>",
        ".加入副本 @用户名 @用户名",
        ".解散副本",
    ]
    replica_dispatch_commands = [
        ".虚天殿 123 @用户名",
        ".坠魔谷 123 @用户名",
        ".黄龙山 123 @用户名",
        ".苍坤洞府 123 @用户名",
    ]
    body = (
        "日志群指令\n"
        "身份选择：指令后可追加 @昵称 或身份 ID，例如 .状态 @竹灵1\n\n"
        "状态查询：\n"
        + "\n".join(f"- {cmd}{suffix}" for cmd in module_commands)
        + "\n\n储物袋只读查询：\n"
        + "\n".join(f"- {cmd}" for cmd in storage_commands)
        + "\n\n离线分析（只读）：\n"
        + "\n".join(f"- {cmd}" for cmd in analysis_commands)
        + "\n\n日志推送：\n"
        + "\n".join(f"- {cmd}" for cmd in audit_commands)
        + "\n\n三宗门手动发送（必须显式指定单个身份）：\n"
        + "\n".join(THREE_SECT_MANUAL_USAGE.splitlines()[1:])
        + "\n\n虚天后续兜底（必须显式指定单个身份，优先点日志按钮）：\n"
        + "\n".join(f"- {line}" for line in XUTIAN_FOLLOWUP_MANUAL_USAGE.splitlines()[1:])
        + "\n\n控制指令：\n"
        + "\n".join(f"- {cmd}{suffix if '<模块名>' in cmd or cmd.startswith('.开启全部') or cmd.startswith('.关闭全部') else ''}" for cmd in control_commands)
        + "\n\n副本群轻量指令（在副本群/游戏群使用）：\n"
        + "\n".join(f"- {cmd}" for cmd in replica_group_commands)
        + "\n\n主线拉人群兼容指令（已停用，仅只读保留）：\n"
        + "\n".join(f"- {cmd}" for cmd in replica_dispatch_commands)
        + "\n\n说明：日志群只处理监控、查询和开关；副本开房/加入/解散在副本群入口处理；游戏内指令仍由模块按全局锁排队。"
    )
    return _format_log_group_card_html("监控指令", body)


def hydrate_identity_profile(send_as_entity):
    send_as_id = int(getattr(send_as_entity, "id", 0) or 0)
    if send_as_id <= 0:
        raise ValueError("无法解析 身份 ID")
    username = getattr(send_as_entity, "username", "") or ""
    label = getattr(send_as_entity, "title", "") or getattr(send_as_entity, "first_name", "") or ""
    update_send_as_profile(send_as_id, username=username, label=label)
    return send_as_id


def enforce_identity_module_availability(send_as_id, *, persist=True):
    send_as_id = int(send_as_id)
    changed = False
    with use_identity(send_as_id):
        if not is_module_available("灵树", send_as_id) and state.get("tree_enabled"):
            _disable_tree_module_state()
            changed = True
        if not is_module_available("观星台", send_as_id) and state.get("stargazer_enabled"):
            _disable_stargazer_module_state()
            changed = True
        if not is_module_available("观星", send_as_id) and state.get("guanxing_enabled"):
            _disable_guanxing_module_state()
            changed = True
        if not is_module_available("周天星斗", send_as_id) and state.get("formation_enabled"):
            _disable_formation_module_state()
            changed = True
        if not is_module_available("登天阶", send_as_id) and state.get("tianti_enabled"):
            _disable_tianti_module_state()
            changed = True
        if not is_module_available("放养", send_as_id) and state.get("ranch_enabled"):
            _manual_disable_ranch_module_state()
            changed = True
        if not is_module_available("合欢宗", send_as_id) and state.get("hehuan_enabled"):
            _disable_hehuan_module_state()
            changed = True
        if not is_module_available("天星宗", send_as_id) and state.get("tianxing_enabled"):
            _disable_tianxing_module_state()
            changed = True
        if not is_module_available("阴罗宗", send_as_id) and state.get("yinluo_enabled"):
            _disable_yinluo_module_state()
            changed = True
        if not is_module_available("元婴", send_as_id) and state.get("yuanying_enabled"):
            _disable_yuanying_module_state()
            changed = True
        if not is_module_available("问道", send_as_id) and state.get("wendao_enabled"):
            _disable_wendao_module_state()
            changed = True
        if not is_module_available("小世界", send_as_id) and state.get("small_world_enabled"):
            _disable_small_world_module_state()
            changed = True
        if not is_module_available("太一", send_as_id) and state.get("taiyi_enabled"):
            _disable_taiyi_module_state()
            changed = True
        if not is_module_available("点卯", send_as_id) and state.get("checkin_enabled"):
            _manual_disable_checkin_module_state()
            changed = True
        if not is_module_available("宗门传功", send_as_id) and state.get("sect_teach_enabled"):
            _manual_disable_sect_teach_module_state()
            changed = True
        if not is_module_available("闯塔", send_as_id) and state.get("tower_enabled"):
            _manual_disable_tower_module_state()
            changed = True
    if changed and persist:
        save_state()
    return changed


def _restore_checkin_runtime(now):
    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
    if state["last_checkin_done_day"] == day_key:
        next_checkin_time = float(state.get("next_checkin_time", 0) or 0)
        if next_checkin_time > now and get_checkin_day_key(next_checkin_time) != day_key:
            return
        schedule_next_checkin_after_completion(now, persist=False)
        return

    resume_time = _get_checkin_resume_time()
    if resume_time > now and get_checkin_day_key(resume_time) == day_key:
        return

    state["last_checkin_msg_id"] = 0
    _set_checkin_module_enabled(True, now)


def _restore_sect_teach_runtime(now):
    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
    if int(state.get("checkin_teach_count", 0) or 0) >= 3:
        _clear_sect_teach_runtime()
        return
    next_sect_teach_time = float(state.get("next_sect_teach_time", 0) or 0)
    reply_to_msg_id = int(state.get("sect_teach_reply_to_msg_id", 0) or 0)
    if next_sect_teach_time > now and reply_to_msg_id > 0:
        return
    last_checkin_msg_id = int(state.get("last_checkin_msg_id", 0) or 0)
    if state.get("last_checkin_done_day") == day_key and last_checkin_msg_id > 0:
        state["next_sect_teach_time"] = now + random.uniform(RECOVERY_READY_MIN_SEC, RECOVERY_READY_MAX_SEC)
        state["sect_teach_reply_to_msg_id"] = last_checkin_msg_id



def _restore_tower_runtime(now):
    day_key = get_day_key(now)
    next_tower_time = float(state.get("next_tower_time", 0) or 0)
    if state["last_tower_day"] == day_key:
        if next_tower_time > now and get_day_key(next_tower_time) != day_key:
            return
        schedule_next_tower_after_completion(now, persist=False)
        return

    if next_tower_time > now and get_day_key(next_tower_time) == day_key:
        return

    state["last_tower_day"] = ""
    state["last_tower_msg_id"] = 0
    state["tower_reply_due_at"] = 0
    state["tower_retry_count"] = 0
    _set_tower_module_enabled(True, now)



def _restore_tree_runtime(now):
    state["tree_bootstrap_check_needed"] = False
    state["tree_bootstrap_check_due_at"] = 0
    if state["is_maturing"]:
        last_status_at = float(state.get("last_tree_status_sent_at", 0) or 0)
        stale_harvested_maturing = (
            state["is_harvested"]
            and last_status_at > 0
            and now - last_status_at > TREE_HARVESTED_MATURING_STALE_SEC
        )
        if stale_harvested_maturing:
            state["is_maturing"] = False
            state["is_harvested"] = False
            state["tree_harvest_followup_due_at"] = 0
            state["tree_harvest_inflight_until"] = 0
            state["tree_maturing_logged"] = False
            state["next_irr_time"] = now
            return
        if not state["is_harvested"] and float(state.get("tree_harvest_inflight_until", 0) or 0) <= now:
            request_tree_bootstrap_check(
                now,
                min_sec=30,
                max_sec=90,
            )
        return
    if state["is_invading"] or state["pending_irrigation"]:
        request_tree_bootstrap_check(now)
        return
    if state["next_irr_time"] <= 0:
        _schedule_module_immediate_retry("灵树", now)
        return


def _restore_second_soul_runtime(now):
    """启动恢复时：异常 phase 让 bootstrap_check 处理；idle 时立即调度查询。"""
    phase = state.get("second_soul_phase", "idle")
    # pending 残留（上次进程被 kill 时卡的）：清掉，重启后先查状态，不补发修炼指令
    if phase in ("status_pending", "train_pending"):
        state["second_soul_phase"] = "idle"
        state["next_second_soul_time"] = now
        state["second_soul_status_msg_id"] = 0
        state["second_soul_train_msg_id"] = 0
        return
    if phase == "ready_to_train":
        if state.get("next_second_soul_time", 0) <= 0:
            state["next_second_soul_time"] = now
        return
    if phase in ("cultivating", "injured", "heart_demon_pending"):
        # 真实状态保留，等 next_second_soul_time 到点
        return
    if phase == "not_unlocked":
        # 长冻结，不主动查
        return
    if state.get("next_second_soul_time", 0) <= 0:
        state["next_second_soul_time"] = now


_TAIYI_YINDAO_LOG_CALIBRATION_LOOKBACK_SEC = 6 * 3600
RE_TAIYI_YINDAO_SUCCESS_LOG = re.compile(r"你引动【([金木水火土])之道】")


def _parse_message_log_ts(raw_ts):
    ts_text = str(raw_ts or "").strip()
    if not ts_text:
        return 0.0
    ts_text = ts_text.replace(" UTC+8", "")
    try:
        return datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except ValueError:
        return 0.0


def _iter_message_log_entries_between(start_ts, end_ts):
    try:
        start_day = datetime.fromtimestamp(float(start_ts), TZ_LOCAL).date()
        end_day = datetime.fromtimestamp(float(end_ts), TZ_LOCAL).date()
    except (TypeError, ValueError, OSError):
        return
    day = start_day
    while day <= end_day:
        log_path = Path(MESSAGES_DIR) / f"{day.isoformat()}.log"
        if log_path.exists():
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass
        day += timedelta(days=1)


def _is_taiyi_yindao_command_text(text):
    raw = str(text or "").strip()
    return raw == CMD_YINDAO or raw.startswith(f"{CMD_YINDAO} ")


def _is_taiyi_yindao_success_text(text):
    raw = str(text or "")
    return bool(RE_TAIYI_YINDAO_SUCCESS_LOG.search(raw) and "100点神识" in raw)


def _find_taiyi_yindao_success_in_logs(send_as_id, start_ts, end_ts):
    send_as_id = int(send_as_id or 0)
    if send_as_id <= 0:
        return None
    commands = {}
    latest_hit = None
    for entry in _iter_message_log_entries_between(start_ts, end_ts):
        entry_ts = _parse_message_log_ts((entry or {}).get("ts"))
        if entry_ts <= 0 or entry_ts < start_ts or entry_ts > end_ts:
            continue
        msg_id = int((entry or {}).get("message_id") or 0)
        text = (entry or {}).get("text") or ""
        sender_id = int((entry or {}).get("sender_id") or 0)
        event_type = str((entry or {}).get("event_type") or "message")
        if (
            msg_id > 0
            and event_type in {"message", "sent"}
            and sender_id == send_as_id
            and _is_taiyi_yindao_command_text(text)
        ):
            commands[msg_id] = {"ts": entry_ts, "text": str(text)}
            continue
        reply_to_msg_id = int((entry or {}).get("reply_to_msg_id") or 0)
        if reply_to_msg_id in commands and _is_taiyi_yindao_success_text(text):
            latest_hit = {
                "command_ts": float(commands[reply_to_msg_id]["ts"]),
                "reply_ts": float(entry_ts),
                "command_text": str(commands[reply_to_msg_id]["text"]),
                "reply_text": str(text),
            }
    return latest_hit


def _calibrate_taiyi_yindao_from_recent_log(now):
    if state.get("taiyi_phase", "idle") != "idle":
        return False
    last_error = str(state.get("taiyi_last_error") or "")
    if "引道 reply 未回" not in last_error or "按正常12h周期兜底" not in last_error:
        return False
    phase_entered_at = float(state.get("taiyi_phase_entered_at", 0) or 0)
    start_ts = phase_entered_at if phase_entered_at > 0 else float(now) - _TAIYI_YINDAO_LOG_CALIBRATION_LOOKBACK_SEC
    start_ts = max(0.0, start_ts)
    hit = _find_taiyi_yindao_success_in_logs(get_current_identity_id(), start_ts, float(now) + 60)
    if not hit:
        return False
    next_cycle = float(hit["reply_ts"]) + TAIYI_CYCLE_CD_SEC + CD_BUFFER_SEC
    state["next_taiyi_cycle_time"] = next_cycle
    state["taiyi_phase_entered_at"] = float(hit["reply_ts"])
    state["taiyi_pending_node_name"] = ""
    state["taiyi_yindao_msg_id"] = 0
    state["taiyi_node_search_msg_id"] = 0
    state["taiyi_node_define_msg_id"] = 0
    state["taiyi_failure_history"] = []
    state["taiyi_last_error"] = ""
    mark_dirty()
    console_log(
        (
            "🌟 太一引道启动日志校准：检测到真实成功回复，"
            f"下次→{datetime.fromtimestamp(next_cycle, TZ_LOCAL).strftime('%Y-%m-%d %H:%M:%S %Z')}"
        ),
        scope="identity",
        send_as_id=get_current_identity_id(),
        limit=220,
    )
    return True


def _restore_taiyi_runtime(now):
    """启动恢复时修复太一链路阶段。

    引道发送窗口很短，热重载可能发生在 phase 已落库但出站消息尚未可靠
    登记时。这里宁可短延迟校准一次，也不要把不确定的引道误收口到 12h。
    """
    phase = state.get("taiyi_phase", "idle")
    if phase == "yindao_pending":
        yindao_msg_id = int(state.get("taiyi_yindao_msg_id", 0) or 0)
        entered_at = float(state.get("taiyi_phase_entered_at", 0) or 0)
        resend_count = int(state.get("taiyi_yindao_resend_count", 0) or 0)
        if resend_count > 0:
            if entered_at <= 0:
                state["taiyi_phase_entered_at"] = max(0, now - 120)
            return
        if yindao_msg_id > 0 and _has_yindao_send_evidence(
            get_current_identity_id(),
            yindao_msg_id,
            _resolve_yindao_command(),
            entered_at,
            now,
        ):
            if entered_at <= 0:
                state["taiyi_phase_entered_at"] = max(0, now - 120)
            return
        state["taiyi_phase"] = "idle"
        state["taiyi_phase_entered_at"] = 0
        state["taiyi_pending_node_name"] = ""
        state["taiyi_yindao_msg_id"] = 0
        state["taiyi_node_search_msg_id"] = 0
        state["taiyi_node_define_msg_id"] = 0
        state["taiyi_yindao_resend_count"] = 1
        state["next_taiyi_cycle_time"] = now + random.uniform(
            RECOVERY_SPREAD_MIN_SEC,
            TAIYI_PRESEND_RECOVERY_MAX_SEC,
        )
        state["taiyi_last_error"] = "启动恢复：引道发送边界不确定，已安排短延迟重试"
        return
    if _calibrate_taiyi_yindao_from_recent_log(now):
        phase = state.get("taiyi_phase", "idle")
    if phase in ("yindao_pending", "search_pending", "define_pending"):
        state["taiyi_yindao_msg_id"] = 0
        state["taiyi_node_search_msg_id"] = 0
        state["taiyi_node_define_msg_id"] = 0
        if phase == "define_pending" and not str(state.get("taiyi_pending_node_name") or "").strip():
            state["taiyi_phase"] = "idle"
            state["taiyi_phase_entered_at"] = 0
            phase = "idle"
        elif float(state.get("taiyi_phase_entered_at", 0) or 0) <= 0:
            state["taiyi_phase_entered_at"] = max(0, now - 120)
    if phase == "search_scheduled" and float(state.get("taiyi_phase_entered_at", 0) or 0) <= 0:
        state["taiyi_phase_entered_at"] = max(0, now - 120)
    if phase == "frozen":
        return
    if state.get("next_taiyi_cycle_time", 0) <= 0:
        # 0-30min 启动 stagger
        state["next_taiyi_cycle_time"] = now + random.uniform(0, 1800)



def _restore_stargazer_runtime(now):
    total_slots = int(get_stargazer_total_slots() or 0)
    followup_due_at = float(state.get("stargazer_followup_due_at", 0) or 0)
    next_panel_time = float(state.get("next_stargazer_panel_time", 0) or 0)
    collect_due_at = float(state.get("stargazer_collect_due_at", 0) or 0)
    has_live_timing = max(followup_due_at, next_panel_time, collect_due_at) > now
    if total_slots > 0 and has_live_timing:
        return
    state["stargazer_followup_due_at"] = float(now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC)
    state["next_stargazer_panel_time"] = 0
    state["stargazer_last_action"] = "queue_panel"



def _restore_phaseful_runtime(module_name, now):
    if module_name == "元婴":
        phase_key = "yuanying_phase"
        next_time_key = "next_yuanying_time"
        last_command_time_key = "last_yuanying_command_time"
        probe_pending_key = "yuanying_probe_pending"
        summary_sent_at_key = "yuanying_summary_sent_at"
        last_summary_msg_id_key = "last_yuanying_summary_msg_id"
    elif module_name == "深度闭关":
        phase_key = "deep_retreat_phase"
        next_time_key = "next_deep_retreat_time"
        last_command_time_key = "last_deep_retreat_command_time"
        probe_pending_key = "deep_retreat_probe_pending"
        summary_sent_at_key = "deep_retreat_summary_sent_at"
        last_summary_msg_id_key = "last_deep_retreat_summary_msg_id"
    else:
        raise ValueError(f"不支持的模块恢复: {module_name}")

    def recover_idle(delay_min=RECOVERY_PHASEFUL_IDLE_MIN_SEC, delay_max=RECOVERY_PHASEFUL_IDLE_MAX_SEC):
        state[phase_key] = "idle"
        state[probe_pending_key] = False
        state[summary_sent_at_key] = 0
        state[last_summary_msg_id_key] = 0
        state[next_time_key] = now + random.uniform(delay_min, delay_max)

    phase = str(state.get(phase_key) or "idle")
    valid_phases = {
        "idle",
        "launching",
        "queued_launch",
        "running",
        "summary_due",
        "observing_summary",
        "waiting_summary",
        "post_summary_wait",
    }
    if phase not in valid_phases:
        recover_idle()
        return
    if phase in ("launching", "queued_launch") and float(state.get(last_command_time_key, 0) or 0) <= 0:
        recover_idle()
        return
    next_time = float(state.get(next_time_key, 0) or 0)
    if phase in {"launching", "queued_launch", "summary_due", "observing_summary", "waiting_summary", "post_summary_wait"} and next_time <= now + RECOVERY_SPREAD_MAX_SEC:
        state[next_time_key] = now + random.uniform(RECOVERY_PHASEFUL_IDLE_MIN_SEC, RECOVERY_PHASEFUL_IDLE_MAX_SEC)
        return
    if phase == "idle" and next_time <= now + RECOVERY_SPREAD_MAX_SEC:
        state[next_time_key] = now + random.uniform(RECOVERY_PHASEFUL_IDLE_MIN_SEC, RECOVERY_PHASEFUL_IDLE_MAX_SEC)
        return


def _clear_disabled_passive_observations():
    changed = False
    if not state.get("tianxing_enabled") and state.get("tianxing_observation"):
        state["tianxing_observation"] = {}
        changed = True
    if not state.get("tianxing_enabled") and state.get("tianxing_timeline_state"):
        state["tianxing_timeline_state"] = {}
        changed = True
    if not state.get("yinluo_enabled") and state.get("yinluo_observation"):
        state["yinluo_observation"] = {}
        changed = True
    if changed:
        mark_dirty()



def initialize_identity_runtime(send_as_id, now=None):
    send_as_id = int(send_as_id)
    if now is None:
        now = time.time()
    if not get_identity_enabled(send_as_id):
        return
    with use_identity(send_as_id):
        _clear_disabled_passive_observations()
        if state["tree_enabled"]:
            _restore_tree_runtime(now)
        if state["pet_enabled"] and state["next_pet_time"] <= 0:
            _schedule_module_immediate_retry("法宝", now)
        if state.get("pet_warm_enabled") and state.get("next_pet_warm_time", 0) <= 0:
            _schedule_module_immediate_retry("温养器灵", now)
        if state.get("pet_trial_enabled") and state.get("next_pet_trial_time", 0) <= 0:
            _schedule_module_immediate_retry("器灵试炼", now)
        if state.get("ranch_enabled") and state.get("next_ranch_time", 0) <= 0:
            schedule_ranch_initial_check(now, persist=False, keep_last_error=True)
        if state.get("wild_training_enabled") and state.get("next_wild_training_time", 0) <= 0:
            state["wild_training_reply_to_msg_id"] = 0
            state["wild_training_reply_due_at"] = 0
            state["wild_training_retry_count"] = 0
            state["next_wild_training_time"] = now + random.uniform(
                WILD_TRAINING_RECOVERY_SPREAD_MIN_SEC,
                WILD_TRAINING_RECOVERY_SPREAD_MAX_SEC,
            )
        if state["stargazer_enabled"]:
            _restore_stargazer_runtime(now)
        if state["tianti_enabled"]:
            today_key = get_day_key(now)
            if _has_stale_tianti_daily_marker(today_key):
                state["tianti_last_wenxin_day"] = ""
                state["tianti_wenxin_last_trigger_key"] = ""
                state["tianti_gangfeng_last_trigger_key"] = ""
                state["tianti_last_skip_reason"] = ""
                state["tianti_theoretical_max_stage"] = 0
                state["tianti_wenxin_trigger_stage"] = 0
                state["next_tianti_wenxin_time"] = 0
            has_status_snapshot = any(
                value not in {None, "", 0, "未记录"}
                for value in (
                    state.get("tianti_progress_current"),
                    state.get("tianti_cycle_count"),
                    state.get("tianti_gangfeng_level"),
                    state.get("tianti_cooldown_text"),
                    state.get("tianti_wenxin_status"),
                )
            )
            next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
            if (not has_status_snapshot or (next_climb_time > 0 and now >= next_climb_time)) and not _has_fresh_tianti_recovery_status(now):
                state["next_tianti_status_time"] = now + _IMMEDIATE_ENABLE_RETRY_DELAY_SEC
            _restore_tianti_ready_runtime(now)
            _restore_tianti_active_cooldown_runtime(now)
        if state["checkin_enabled"]:
            _restore_checkin_runtime(now)
        if state.get("sect_teach_enabled"):
            _restore_sect_teach_runtime(now)
        if state["tower_enabled"]:
            _restore_tower_runtime(now)
        if state["deep_retreat_enabled"]:
            _restore_phaseful_runtime("深度闭关", now)
        if state["yuanying_enabled"]:
            _restore_phaseful_runtime("元婴", now)
        if state.get("explore_rift_enabled") and float(state.get("next_explore_rift_time", 0) or 0) <= 0:
            schedule_explore_rift_initial_check(now, persist=False, keep_last_error=True)
        if state.get("wendao_enabled") and float(state.get("next_wendao_time", 0) or 0) <= 0:
            schedule_wendao_initial_check(now, persist=False, keep_last_error=True)
        if state.get("mulan_enabled") and float(state.get("next_mulan_time", 0) or 0) <= 0:
            schedule_mulan_initial_check(now, persist=False, keep_last_error=True)
        if state.get("duel_enabled") and float(state.get("next_duel_time", 0) or 0) <= 0:
            schedule_duel_initial_check(now, persist=False, keep_last_error=True)
        if state.get("fishing_enabled") and float(state.get("next_fishing_time", 0) or 0) <= 0:
            schedule_fishing_initial_check(now, persist=False, keep_last_error=True)
        if state["second_soul_enabled"]:
            _restore_second_soul_runtime(now)
        if state["taiyi_enabled"]:
            _restore_taiyi_runtime(now)
        if state["concubine_enabled"] or state.get("concubine_tianji_enabled") or state.get("concubine_heart_enabled"):
            restore_concubine_runtime(now)
        if state.get("small_world_enabled"):
            restore_small_world_runtime(now, persist=False)
        reconcile_identity_sessions(send_as_id, now)


def _get_startup_module_alerts_bucket():
    alerts = state.get("startup_module_alerts")
    if isinstance(alerts, list):
        return alerts
    state["startup_module_alerts"] = []
    return state["startup_module_alerts"]


def _clear_startup_module_alerts(module_name=None):
    alerts = list(_get_startup_module_alerts_bucket())
    if module_name is None:
        if not alerts:
            return False
        state["startup_module_alerts"] = []
        return True
    filtered_alerts = [alert for alert in alerts if (alert or {}).get("module_name") != module_name]
    if len(filtered_alerts) == len(alerts):
        return False
    state["startup_module_alerts"] = filtered_alerts
    return True


def _build_startup_module_alert(send_as_id, module_name, reason, reason_code):
    send_as_id = int(send_as_id)
    module_key = MODULE_KEY_MAP.get(module_name, module_name)
    return {
        "key": f"{send_as_id}:{module_key}",
        "send_as_id": send_as_id,
        "display_name": get_identity_ui_display_name(send_as_id),
        "module_name": module_name,
        "reason": (reason or "").strip(),
        "reason_code": (reason_code or "").strip(),
    }


def _append_startup_module_alert(send_as_id, module_name, reason, reason_code):
    alert = _build_startup_module_alert(send_as_id, module_name, reason, reason_code)
    alerts = _get_startup_module_alerts_bucket()
    if any((existing or {}).get("key") == alert["key"] for existing in alerts):
        return None
    alerts.append(alert)
    return alert


def _get_pending_task_module_name(command):
    raw_command = (command or "").strip()
    if not raw_command:
        return ""
    if raw_command == CMD_PET or raw_command.startswith(f"{CMD_PET} "):
        return "法宝"
    if raw_command == CMD_PET_WARM or raw_command.startswith(f"{CMD_PET_WARM} "):
        return "温养器灵"
    if raw_command == CMD_PET_TRIAL or raw_command.startswith(f"{CMD_PET_TRIAL} "):
        return "器灵试炼"
    if raw_command == CMD_PET_FORMATION:
        return "布下剑阵"
    if raw_command == CMD_MULAN_JUDGE or raw_command.startswith(f"{CMD_MULAN_JUDGE} "):
        return "慕兰"
    if raw_command == CMD_MULAN_PUBLISH or raw_command.startswith(f"{CMD_MULAN_PUBLISH} "):
        return "慕兰"
    if raw_command.startswith(CMD_EXCHANGE_HEQI_DAN_PREFIX) or raw_command.startswith(CMD_SECT_DONATE_LINGSHI_PREFIX):
        return "天星宗"
    return PENDING_TASK_COMMAND_TO_MODULE.get(raw_command, "")


def _truncate_startup_account_detail(text, *, limit=80):
    raw_text = str(text or "").strip()
    if not raw_text or len(raw_text) <= limit:
        return raw_text
    return raw_text[: max(0, limit - 1)].rstrip() + "…"


def collect_startup_account_integrity(identity_ids, failed_accounts=None):
    accounts = get_accounts()
    runtime_account_ids = {
        int(account_id)
        for account_id in get_all_clients().keys()
        if int(account_id or 0) > 0 and not is_account_offline(account_id)
    }
    single_runtime_account_id = next(iter(runtime_account_ids)) if len(runtime_account_ids) == 1 else 0
    failed_accounts = list(failed_accounts or [])
    failed_account_ids = set()
    normalized_failed_accounts = []
    for item in failed_accounts:
        if isinstance(item, dict):
            try:
                account_id = int(item.get("account_id") or 0)
            except (TypeError, ValueError):
                account_id = 0
            error_text = _truncate_startup_account_detail(item.get("error") or "")
        else:
            try:
                account_id = int(item)
            except (TypeError, ValueError):
                account_id = 0
            error_text = ""
        if account_id <= 0:
            continue
        failed_account_ids.add(account_id)
        normalized_failed_accounts.append({
            "account_id": account_id,
            "error": error_text,
        })

    items = []
    for send_as_id in identity_ids or []:
        send_as_id = int(send_as_id)
        account_id = int(get_identity_account(send_as_id) or 0)
        display_name = get_identity_display_name(send_as_id)
        if account_id <= 0:
            if single_runtime_account_id > 0:
                continue
            items.append({
                "type": "identity_missing_account",
                "send_as_id": send_as_id,
                "display_name": display_name,
                "account_id": 0,
            })
            items.append({
                "type": "identity_hydrate_skipped_no_account",
                "send_as_id": send_as_id,
                "display_name": display_name,
                "account_id": 0,
            })
            continue
        if str(account_id) not in accounts:
            items.append({
                "type": "identity_account_not_found",
                "send_as_id": send_as_id,
                "display_name": display_name,
                "account_id": account_id,
            })

    for failed in normalized_failed_accounts:
        items.append({
            "type": "account_client_start_failed",
            "account_id": failed["account_id"],
            "error": failed["error"],
        })

    return {
        "identity_ids": [int(identity_id) for identity_id in identity_ids or []],
        "items": items,
        "failed_accounts": normalized_failed_accounts,
        "failed_account_ids": sorted(failed_account_ids),
    }


def repair_startup_account_integrity(scan_result):
    scan_result = scan_result or {}
    items = list(scan_result.get("items") or [])
    fixed_count = 0
    fixed_items = []
    type_counts = {}
    for item in items:
        issue_type = str(item.get("type") or "").strip()
        if not issue_type:
            continue
        type_counts[issue_type] = int(type_counts.get(issue_type, 0) or 0) + 1
        if issue_type != "identity_account_not_found":
            continue
        send_as_id = int(item.get("send_as_id") or 0)
        account_id = int(item.get("account_id") or 0)
        if send_as_id <= 0 or account_id <= 0:
            continue
        if int(get_identity_account(send_as_id) or 0) != account_id:
            continue
        set_identity_account(send_as_id, 0)
        fixed_count += 1
        fixed_items.append({
            "type": issue_type,
            "send_as_id": send_as_id,
            "display_name": item.get("display_name") or get_identity_display_name(send_as_id),
            "old_account_id": account_id,
        })

    return {
        "identity_count": len(scan_result.get("identity_ids") or []),
        "items": items,
        "type_counts": type_counts,
        "fixed_count": fixed_count,
        "fixed_items": fixed_items,
        "failed_accounts": list(scan_result.get("failed_accounts") or []),
    }


def build_startup_account_integrity_audit_lines(result):
    result = result or {}
    items = list(result.get("items") or [])
    if not items:
        return []
    type_counts = result.get("type_counts") or {}
    fixed_count = int(result.get("fixed_count") or 0)
    lines = [
        "🧩 启动账号自检：",
        f"- 检查身份: {int(result.get('identity_count') or 0)}",
        f"- 缺少账号绑定: {int(type_counts.get('identity_missing_account', 0) or 0)}",
        f"- 失效绑定已清理: {fixed_count}",
        f"- 账号启动失败: {int(type_counts.get('account_client_start_failed', 0) or 0)}",
        f"- hydrate 跳过: {int(type_counts.get('identity_hydrate_skipped_no_account', 0) or 0)}",
    ]
    detail_lines = []
    for item in result.get("fixed_items") or []:
        detail_lines.append(
            f"- 已清理失效绑定：{item.get('display_name') or item.get('send_as_id')} ← {int(item.get('old_account_id') or 0)}"
        )
    for item in items:
        issue_type = item.get("type")
        if issue_type == "identity_missing_account":
            detail_lines.append(f"- 未绑定账号：{item.get('display_name') or item.get('send_as_id')}")
        elif issue_type == "account_client_start_failed":
            account_id = int(item.get("account_id") or 0)
            error_text = _truncate_startup_account_detail(item.get("error") or "启动失败")
            detail_lines.append(f"- 账号启动失败：{account_id}（{error_text or '启动失败'}）")
    seen = set()
    compact_detail_lines = []
    for line in detail_lines:
        if line in seen:
            continue
        seen.add(line)
        compact_detail_lines.append(line)
        if len(compact_detail_lines) >= 5:
            break
    return lines + compact_detail_lines


def run_startup_account_integrity_check(identity_ids, failed_accounts=None):
    scan_result = collect_startup_account_integrity(identity_ids, failed_accounts)
    result = repair_startup_account_integrity(scan_result)
    result["audit_lines"] = build_startup_account_integrity_audit_lines(result)
    return result


def _disable_module_state(module_name):
    handler = MODULE_DISABLE_HANDLERS.get(module_name)
    if not handler:
        return False
    handler()
    return True


def _disable_module_for_startup_timeout(send_as_id, module_name, reason, reason_code):
    module_key = MODULE_KEY_MAP.get(module_name)
    was_enabled = bool(state.get(module_key, False)) if module_key else False
    _disable_module_state(module_name)
    if not was_enabled:
        return None
    return _append_startup_module_alert(send_as_id, module_name, reason, reason_code)


def _record_startup_timeout(send_as_id, module_name, reason, reason_code, alerts, affected_identity_ids):
    alert = _disable_module_for_startup_timeout(send_as_id, module_name, reason, reason_code)
    if not alert:
        return False
    alerts.append(alert)
    affected_identity_ids.add(send_as_id)
    return True


def _scan_pending_task_startup_timeouts(send_as_id, now, alerts, affected_identity_ids):
    for item in list(state.get("pending_tasks", {}).values()):
        sent_at = float(item.get("sent_at", 0) or 0)
        timeout = float(item.get("timeout", 0) or 0)
        if sent_at <= 0 or timeout <= 0 or now - sent_at <= timeout:
            continue
        module_name = _get_pending_task_module_name(get_pending_command(item))
        if not module_name:
            continue
        _record_startup_timeout(
            send_as_id,
            module_name,
            f"启动时检测到旧{module_name}任务等待超时，已自动关闭该模块。",
            "pending_timeout",
            alerts,
            affected_identity_ids,
        )


def _scan_phase_startup_timeouts(send_as_id, now, rule, alerts, affected_identity_ids):
    module_name = rule["module_name"]
    phase = state.get(rule["phase_key"])
    command_time_key = rule["command_time_key"]
    if phase == "launching" and state.get(command_time_key, 0) > 0 and now - state[command_time_key] >= LAUNCHING_TIMEOUT_SEC:
        reason, reason_code = rule["launching"]
        _record_startup_timeout(send_as_id, module_name, reason, reason_code, alerts, affected_identity_ids)
    elif phase == "waiting_summary":
        summary_sent_at = float(state.get(rule["summary_sent_at_key"], 0) or 0)
        if summary_sent_at <= 0:
            _phase_startup_normal_cd_fallback(send_as_id, now, rule, rule["summary_missing"])
        elif now - summary_sent_at >= SUMMARY_TIMEOUT_SEC:
            _phase_startup_normal_cd_fallback(send_as_id, now, rule, rule["summary_timeout"])


def _phase_startup_normal_cd_fallback(send_as_id, now, rule, reason_pair):
    reason, _reason_code = reason_pair
    state[rule["phase_key"]] = "idle"
    state[rule["summary_sent_at_key"]] = 0
    last_summary_msg_id_key = rule.get("last_summary_msg_id_key")
    if last_summary_msg_id_key:
        state[last_summary_msg_id_key] = 0
    probe_pending_key = rule.get("probe_pending_key")
    if probe_pending_key:
        state[probe_pending_key] = False
    retry_at = float(now) + float(rule["cd_sec"]) + CD_BUFFER_SEC + random.uniform(60, 600)
    state[rule["next_time_key"]] = retry_at
    mark_dirty()
    console_log(
        f"🧯 {rule['module_name']} 启动恢复：{reason} 已改为正常CD兜底→{datetime.fromtimestamp(retry_at, TZ_LOCAL).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        scope="identity",
        send_as_id=send_as_id,
        limit=220,
    )


def _scan_post_summary_startup_timeout(send_as_id, now, rule, alerts, affected_identity_ids):
    next_time = state.get(rule["next_time_key"], 0)
    if state.get(rule["phase_key"]) == "post_summary_wait" and next_time > 0 and now >= next_time:
        reason, reason_code = rule["post_summary"]
        _record_startup_timeout(send_as_id, rule["module_name"], reason, reason_code, alerts, affected_identity_ids)


_YUANYING_STARTUP_TIMEOUT_RULE = {
    "module_name": "元婴",
    "phase_key": "yuanying_phase",
    "command_time_key": "last_yuanying_command_time",
    "summary_sent_at_key": "yuanying_summary_sent_at",
    "last_summary_msg_id_key": "last_yuanying_summary_msg_id",
    "probe_pending_key": "yuanying_probe_pending",
    "next_time_key": "next_yuanying_time",
    "cd_sec": YUANYING_CD,
    "launching": ("启动时检测到元婴出窍等待回复超时，已自动关闭元婴模块。", "yuanying_launching_timeout"),
    "summary_missing": ("启动时检测到元婴归窍总结等待状态异常", "yuanying_summary_missing"),
    "summary_timeout": ("启动时检测到元婴归窍总结等待超时", "yuanying_summary_timeout"),
    "post_summary": ("启动时检测到元婴总结后的缓冲等待已过期，已自动关闭元婴模块。", "yuanying_post_summary_overdue"),
}

_DEEP_RETREAT_STARTUP_TIMEOUT_RULE = {
    "module_name": "深度闭关",
    "phase_key": "deep_retreat_phase",
    "command_time_key": "last_deep_retreat_command_time",
    "summary_sent_at_key": "deep_retreat_summary_sent_at",
    "last_summary_msg_id_key": "last_deep_retreat_summary_msg_id",
    "probe_pending_key": "deep_retreat_probe_pending",
    "next_time_key": "next_deep_retreat_time",
    "cd_sec": DEEP_RETREAT_CD,
    "launching": ("启动时检测到深度闭关等待回复超时，已自动关闭深度闭关模块。", "deep_retreat_launching_timeout"),
    "summary_missing": ("启动时检测到闭关总结等待状态异常", "deep_retreat_summary_missing"),
    "summary_timeout": ("启动时检测到闭关总结等待超时", "deep_retreat_summary_timeout"),
    "post_summary": ("启动时检测到闭关总结后的缓冲等待已过期，已自动关闭深度闭关模块。", "deep_retreat_post_summary_overdue"),
}


def get_startup_module_alerts():
    alerts = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            alerts.extend(list(_get_startup_module_alerts_bucket()))
    module_order = {module_name: index for index, module_name in enumerate(MODULE_NAMES)}
    return sorted(
        alerts,
        key=lambda alert: (
            int((alert or {}).get("send_as_id") or 0),
            module_order.get((alert or {}).get("module_name"), len(module_order)),
        ),
    )


def scan_startup_timeout_tasks(now=None):
    if now is None:
        now = time.time()
    alerts = []
    affected_identity_ids = set()
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            state["startup_module_alerts"] = []
            if not get_identity_enabled(identity_id):
                continue

            _scan_pending_task_startup_timeouts(identity_id, now, alerts, affected_identity_ids)

            if state.get("next_sect_teach_time", 0) > 0 and state.get("sect_teach_reply_to_msg_id", 0) > 0 and now >= state["next_sect_teach_time"]:
                _record_startup_timeout(
                    identity_id,
                    "宗门传功",
                    "启动时检测到宗门传功续链已超时，已自动关闭宗门传功模块。",
                    "checkin_teach_overdue",
                    alerts,
                    affected_identity_ids,
                )

            _scan_phase_startup_timeouts(identity_id, now, _YUANYING_STARTUP_TIMEOUT_RULE, alerts, affected_identity_ids)

            stargazer_followup_due_at = float(state.get("stargazer_followup_due_at", 0) or 0)
            if state.get("stargazer_enabled") and stargazer_followup_due_at > 0 and now - stargazer_followup_due_at >= RETRY_MAX_SEC:
                _record_startup_timeout(
                    identity_id,
                    "观星台",
                    "启动时检测到观星台后续动作等待超时，已自动关闭观星台模块。",
                    "stargazer_followup_timeout",
                    alerts,
                    affected_identity_ids,
                )
            # post_summary_wait means the summary was already observed and the next
            # action is a normal relaunch. Keep it alive across restarts; the
            # recovery spread will stagger the relaunch instead of disabling it.

            _scan_phase_startup_timeouts(identity_id, now, _DEEP_RETREAT_STARTUP_TIMEOUT_RULE, alerts, affected_identity_ids)

    return {
        "closed_count": len(alerts),
        "affected_identity_ids": sorted(affected_identity_ids),
        "alerts": alerts,
    }


def _is_identity_refresh_command(command):
    return is_identity_refresh_command_text(command)


def _get_identity_refresh_tracking_ids():
    tracked_ids = {
        int(msg_id)
        for msg_id in state.get("identity_info_reply_msg_ids", [])
        if int(msg_id or 0) > 0
    }
    last_msg_id = int(state.get("last_identity_info_msg_id", 0) or 0)
    if last_msg_id > 0:
        tracked_ids.add(last_msg_id)
    return tracked_ids


def _collect_identity_refresh_trigger_msg_ids():
    return sorted(msg_id for msg_id in _get_identity_refresh_tracking_ids() if msg_id in state.get("my_msg_ids", {}))


def _track_identity_refresh_message(msg_id):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return
    tracked_ids = _get_identity_refresh_tracking_ids()
    if msg_id not in tracked_ids:
        tracked_ids.add(msg_id)
        state["identity_info_reply_msg_ids"] = sorted(tracked_ids)
    state["last_identity_info_msg_id"] = msg_id
    mark_dirty()


def _clear_identity_refresh_runtime(*, error="", clear_pending=True):
    state["last_identity_info_msg_id"] = 0
    state["identity_info_reply_msg_ids"] = []
    state["identity_info_followup_due_at"] = 0
    state["identity_info_primary_payload"] = {}
    state["identity_info_last_error"] = (error or "").strip()
    if clear_pending:
        remove_ids = [
            msg_id
            for msg_id, pending in state.get("pending_tasks", {}).items()
            if _is_identity_refresh_command(get_pending_command(pending))
        ]
        for msg_id in remove_ids:
            state["pending_tasks"].pop(msg_id, None)
    mark_dirty()


def _schedule_identity_refresh_followup(now):
    current_due_at = float(state.get("identity_info_followup_due_at", 0) or 0)
    if current_due_at > now:
        return current_due_at
    due_at = now + random.randint(IDENTITY_INFO_FOLLOWUP_DELAY_MIN_SEC, IDENTITY_INFO_FOLLOWUP_DELAY_MAX_SEC)
    state["identity_info_followup_due_at"] = due_at
    mark_dirty()
    return due_at


def _get_identity_info_refresh_status(send_as_id, now=None):
    if now is None:
        now = time.time()
    with use_identity(send_as_id):
        requested_at = float(state.get("identity_info_last_requested_at", 0) or 0)
        has_pending_cmd = any(_is_identity_refresh_command(get_pending_command(pending)) for pending in state["pending_tasks"].values())
        waiting_reply = bool(_get_identity_refresh_tracking_ids())
        waiting_followup = float(state.get("identity_info_followup_due_at", 0) or 0) > 0
        is_pending = has_pending_cmd or waiting_reply or waiting_followup
        timed_out = requested_at > 0 and is_pending and now - requested_at >= IDENTITY_INFO_REFRESH_TIMEOUT_SEC
        if timed_out:
            return {
                "pending": False,
                "error": IDENTITY_INFO_REFRESH_ERROR_TEXT,
            }
        return {
            "pending": is_pending,
            "error": (state.get("identity_info_last_error") or "").strip(),
        }


def _parse_spiritual_root_text(text):
    raw_text = str(text or "").strip()
    raw_text = raw_text.split("\n", 1)[0].strip().strip(" !！。.,，")
    raw_text = raw_text.replace("（", "(").replace("）", ")")
    raw_text = raw_text.strip("【】")
    match = re.fullmatch(r"([^()]+)\(([^()]+)\)", raw_text)
    if match:
        return (match.group(1) or "").strip(), (match.group(2) or "").strip()
    raw_text = raw_text.split(",", 1)[0].split("，", 1)[0].strip()
    return raw_text, ""


def _infer_replica_professions(spiritual_root_attrs):
    attrs_text = str(spiritual_root_attrs or "")
    rules = (
        ("御山", {"土"}),
        ("灵医", {"木", "水"}),
        ("影刃", {"风", "冰"}),
        ("破军", {"金", "雷"}),
        ("咒师", {"火", "暗"}),
    )
    professions = []
    for profession, attrs in rules:
        if any(attr in attrs_text for attr in attrs):
            professions.append(profession)
    return "|".join(professions)


def _parse_compact_chinese_number(value, unit=""):
    try:
        number = float(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0
    unit = str(unit or "").strip()
    multiplier = 1
    if unit == "万":
        multiplier = 10_000
    elif unit == "亿":
        multiplier = 100_000_000
    return int(number * multiplier)


def _extract_identity_refresh_payload(text, *, card_pattern, require_xiuwei=False):
    raw_text = text or ""
    if not card_pattern.search(raw_text):
        return None

    payload = {}
    name_match = RE_IDENTITY_INFO_NAME.search(raw_text)
    if name_match:
        daohao = (name_match.group(1) or "").strip()
        if daohao:
            payload["daohao"] = daohao

    sect_match = RE_IDENTITY_INFO_SECT.search(raw_text)
    if sect_match:
        sect_name = (sect_match.group(1) or "").strip()
        if sect_name:
            payload["sect_name"] = sect_name
    if "sect_name" not in payload:
        sect_from_realm = RE_IDENTITY_INFO_REALM_WITH_SECT.search(raw_text)
        if sect_from_realm:
            sect_name = (sect_from_realm.group(1) or "").strip()
            if sect_name:
                payload["sect_name"] = sect_name

    spiritual_root_match = RE_IDENTITY_INFO_SPIRITUAL_ROOT.search(raw_text) or RE_BATTLE_POWER_SPIRITUAL_ROOT.search(raw_text)
    if spiritual_root_match:
        spiritual_root_type, spiritual_root_attrs = _parse_spiritual_root_text(spiritual_root_match.group(1))
        if spiritual_root_type:
            payload["spiritual_root_type"] = spiritual_root_type
            payload["spiritual_root_attrs"] = spiritual_root_attrs
            payload["replica_professions"] = _infer_replica_professions(spiritual_root_attrs)

    xiuwei_match = RE_IDENTITY_INFO_XIUWEI.search(raw_text)
    if xiuwei_match:
        xiuwei_current = int(xiuwei_match.group(1).replace(",", ""))
        xiuwei_max = int(xiuwei_match.group(2).replace(",", ""))
        if xiuwei_max > 0:
            payload["xiuwei_current"] = xiuwei_current
            payload["xiuwei_max"] = xiuwei_max

    battle_power_match = RE_BATTLE_POWER_VALUE.search(raw_text)
    if battle_power_match:
        value_text = f"{battle_power_match.group(1)}{battle_power_match.group(2) or ''}"
        payload["battle_power_text"] = value_text
        payload["battle_power_value"] = _parse_compact_chinese_number(battle_power_match.group(1), battle_power_match.group(2))

    if require_xiuwei and "xiuwei_max" not in payload:
        return None

    realm_match = RE_IDENTITY_INFO_REALM_SECT.search(raw_text)
    realm = (realm_match.group(1) or "").strip() if realm_match else ""
    if not realm and int(payload.get("xiuwei_max") or 0) > 0:
        realm = infer_realm_from_xiuwei_max(payload.get("xiuwei_max", 0))
    if realm:
        payload["realm"] = realm

    return payload or None


def _parse_identity_info_partial(text):
    return _extract_identity_refresh_payload(text, card_pattern=RE_IDENTITY_INFO_CARD, require_xiuwei=True)


def _parse_battle_power_info(text):
    return _extract_identity_refresh_payload(text, card_pattern=RE_BATTLE_POWER_CARD, require_xiuwei=False)


def _normalize_identity_refresh_payload(payload):
    normalized = {
        "daohao": str(payload.get("daohao") or "").strip(),
        "realm": str(payload.get("realm") or "").strip(),
        "spiritual_root_type": str(payload.get("spiritual_root_type") or "").strip(),
        "spiritual_root_attrs": str(payload.get("spiritual_root_attrs") or "").strip(),
        "replica_professions": str(payload.get("replica_professions") or "").strip(),
        "sect_name": str(payload.get("sect_name") or "").strip(),
        "xiuwei_current": int(payload.get("xiuwei_current") or 0),
        "xiuwei_max": int(payload.get("xiuwei_max") or 0),
        "battle_power_text": str(payload.get("battle_power_text") or "").strip(),
        "battle_power_value": int(payload.get("battle_power_value") or 0),
    }
    if not normalized["realm"] and normalized["xiuwei_max"] > 0:
        normalized["realm"] = infer_realm_from_xiuwei_max(normalized["xiuwei_max"])
    return normalized


def _merge_identity_refresh_payload(base_payload, overlay_payload):
    merged_payload = _normalize_identity_refresh_payload(base_payload or {})
    overlay_payload = _normalize_identity_refresh_payload(overlay_payload or {})
    if overlay_payload["daohao"]:
        merged_payload["daohao"] = overlay_payload["daohao"]
    if overlay_payload["realm"]:
        merged_payload["realm"] = overlay_payload["realm"]
    if overlay_payload["spiritual_root_type"]:
        merged_payload["spiritual_root_type"] = overlay_payload["spiritual_root_type"]
        merged_payload["spiritual_root_attrs"] = overlay_payload["spiritual_root_attrs"]
        merged_payload["replica_professions"] = overlay_payload["replica_professions"]
    if overlay_payload["sect_name"]:
        merged_payload["sect_name"] = overlay_payload["sect_name"]
    if int(overlay_payload.get("xiuwei_max") or 0) > 0:
        merged_payload["xiuwei_current"] = overlay_payload.get("xiuwei_current", 0)
        merged_payload["xiuwei_max"] = overlay_payload.get("xiuwei_max", 0)
    if overlay_payload["battle_power_text"]:
        merged_payload["battle_power_text"] = overlay_payload["battle_power_text"]
        merged_payload["battle_power_value"] = overlay_payload["battle_power_value"]
    return merged_payload


def _update_identity_profile_from_refresh_payload(send_as_id, payload, now):
    raw_payload = payload or {}
    normalized_payload = _normalize_identity_refresh_payload(payload or {})
    has_xiuwei = int(normalized_payload.get("xiuwei_max") or 0) > 0
    has_battle_power = bool(normalized_payload.get("battle_power_text"))
    has_spiritual_root = bool(normalized_payload.get("spiritual_root_type"))
    has_realm = bool(normalized_payload.get("realm")) and ("realm" in raw_payload or has_xiuwei)
    update_send_as_profile(
        send_as_id,
        daohao=normalized_payload["daohao"] if normalized_payload["daohao"] else None,
        realm=normalized_payload["realm"] if has_realm else None,
        spiritual_root_type=normalized_payload["spiritual_root_type"] if has_spiritual_root else None,
        spiritual_root_attrs=normalized_payload["spiritual_root_attrs"] if has_spiritual_root else None,
        replica_professions=normalized_payload["replica_professions"] if has_spiritual_root else None,
        sect_name=normalized_payload["sect_name"] if normalized_payload["sect_name"] else None,
        xiuwei_current=normalized_payload.get("xiuwei_current", 0) if has_xiuwei else None,
        xiuwei_max=normalized_payload.get("xiuwei_max", 0) if has_xiuwei else None,
        battle_power_text=normalized_payload.get("battle_power_text", "") if has_battle_power else None,
        battle_power_value=normalized_payload.get("battle_power_value", 0) if has_battle_power else None,
        sect_updated_at=now,
    )
    return normalized_payload


def _begin_identity_refresh_runtime(now):
    state["identity_info_last_error"] = ""
    state["identity_info_last_requested_at"] = float(now or 0)
    state["identity_info_reply_msg_ids"] = []
    state["last_identity_info_msg_id"] = 0
    state["identity_info_followup_due_at"] = 0
    state["identity_info_primary_payload"] = {}
    mark_dirty()


def _record_identity_refresh_message(msg_id, *, requested_at=None, clear_followup=False):
    sent_msg_id = int(msg_id or 0)
    tracked_ids = _get_identity_refresh_tracking_ids()
    if sent_msg_id > 0:
        tracked_ids.add(sent_msg_id)
    if requested_at is not None:
        state["identity_info_last_requested_at"] = float(requested_at or 0)
    if clear_followup:
        state["identity_info_followup_due_at"] = 0
    state["last_identity_info_msg_id"] = sent_msg_id
    state["identity_info_reply_msg_ids"] = sorted(tracked_ids)
    state["identity_info_last_error"] = ""
    mark_dirty()


def _finalize_identity_refresh_success(send_as_id, payload, now):
    final_payload = _update_identity_profile_from_refresh_payload(send_as_id, payload, now)
    trigger_msg_ids = _collect_identity_refresh_trigger_msg_ids()
    _clear_identity_refresh_runtime()
    return final_payload, trigger_msg_ids


def _get_identity_refresh_missing_fields(payload):
    missing_fields = []
    for field_name in IDENTITY_REFRESH_REQUIRED_FIELDS:
        if field_name == "xiuwei":
            if int(payload.get("xiuwei_current") or 0) <= 0 or int(payload.get("xiuwei_max") or 0) <= 0:
                missing_fields.append(field_name)
            continue
        if not str(payload.get(field_name) or "").strip():
            missing_fields.append(field_name)
    return missing_fields


def _match_identity_profile_owner(text):
    raw_text = str(text or "")
    username = ""
    battle_owner = RE_BATTLE_POWER_OWNER.search(raw_text)
    if battle_owner:
        username = (battle_owner.group(2) or "").strip()
    if not username:
        info_owner = RE_IDENTITY_INFO_OWNER.search(raw_text)
        if info_owner:
            username = (info_owner.group(1) or "").strip()
    if not username:
        return None
    username_key = username.casefold().lstrip("@")
    matched = []
    for identity_id in get_identity_ids():
        profile = get_send_as_profile(identity_id)
        candidates = [
            str(profile.get("username") or "").strip().casefold().lstrip("@"),
            str(profile.get("label") or "").strip().casefold().lstrip("@"),
        ]
        if username_key in candidates:
            matched.append(identity_id)
    return matched[0] if len(matched) == 1 else None


async def handle_passive_identity_profile_card(text, now):
    primary_payload = _parse_identity_info_partial(text)
    battle_payload = _parse_battle_power_info(text)
    if not primary_payload and not battle_payload:
        return False
    target_id = _match_identity_profile_owner(text)
    if target_id is None:
        return False
    merged_payload = _merge_identity_refresh_payload(primary_payload or {}, battle_payload or {})
    if not any(
        merged_payload.get(key)
        for key in ("daohao", "realm", "spiritual_root_type", "sect_name", "xiuwei_max", "battle_power_text")
    ):
        return False
    _update_identity_profile_from_refresh_payload(target_id, merged_payload, now)
    enforce_identity_module_availability(target_id, persist=False)
    save_state()
    return True


def match_realm_breakthrough_identity(text):
    compact_text = RE_WHITESPACE.sub("", text or "")
    if "灵光一闪" not in compact_text or "成功突破至【" not in compact_text:
        return None, None

    realm_match = RE_REALM_BREAKTHROUGH.search(text or "")
    if not realm_match:
        return None, None
    realm = (realm_match.group(1) or "").strip()
    if not realm:
        return None, None

    matched_ids = []
    for identity_id in get_identity_ids():
        tags = get_send_as_tags(identity_id)
        if not tags:
            continue
        compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags}
        if any(tag in compact_text for tag in compact_tags):
            matched_ids.append(identity_id)

    if len(matched_ids) == 1:
        return matched_ids[0], realm
    return None, realm


async def handle_realm_breakthrough_broadcast(text, now):
    target_id, realm = match_realm_breakthrough_identity(text)
    if target_id is None or not realm:
        return False

    profile = get_send_as_profile(target_id)
    old_realm = (profile.get("realm") or "").strip()
    if old_realm == realm:
        return True
    old_index = get_realm_sort_index(old_realm) if old_realm else len(REALM_SORT_ORDER)
    new_index = get_realm_sort_index(realm)
    if old_index < len(REALM_SORT_ORDER) and new_index < len(REALM_SORT_ORDER) and new_index < old_index:
        await send_audit_log(
            f"⚠️ 忽略疑似反向境界广播：{old_realm}→{realm}",
            scope="identity",
            send_as_id=target_id,
        )
        return True

    update_send_as_profile(target_id, realm=realm)
    enforce_identity_module_availability(target_id, persist=False)
    save_state()
    await send_audit_log(
        f"🌟 境界突破：{old_realm or '未获取'}→{realm}",
        scope="identity",
        send_as_id=target_id,
    )
    return True


def get_identity_info_refresh_state(send_as_id=None):
    if send_as_id is None:
        identity_ids = get_identity_ids()
        send_as_id = identity_ids[0] if identity_ids else None
    if send_as_id is None:
        return {"pending": False, "error": ""}
    return _get_identity_info_refresh_status(int(send_as_id), time.time())


def is_identity_info_refresh_pending(send_as_id=None):
    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    now = time.time()
    return any(_get_identity_info_refresh_status(identity_id, now)["pending"] for identity_id in target_ids)


def get_identity_info_refresh_error(send_as_id=None):
    return get_identity_info_refresh_state(send_as_id)["error"]


async def delete_identity_info_trigger_msg(send_as_id, msg_id, *, persist=True):
    send_as_id = int(send_as_id)
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return
    if is_auto_delete_sent_messages_enabled():
        try:
            from .runtime import _get_identity_client
            await _get_identity_client(send_as_id).delete_messages(get_game_group_id(), [msg_id])
        except Exception as e:
            console_log(
                f"❌ 删除身份信息触发消息失败：{e}｜msg={msg_id}",
                scope="identity",
                send_as_id=send_as_id,
            )
    with use_identity(send_as_id):
        state["my_msg_ids"].pop(msg_id, None)
        if state.get("last_identity_info_msg_id", 0) == msg_id:
            state["last_identity_info_msg_id"] = 0
        state["identity_info_reply_msg_ids"] = [
            tracked_msg_id
            for tracked_msg_id in state.get("identity_info_reply_msg_ids", [])
            if int(tracked_msg_id or 0) != msg_id
        ]
        if persist:
            save_state()


def _set_identity_info_error(send_as_id, message, *, persist=True):
    with use_identity(send_as_id):
        _clear_identity_refresh_runtime(error=message)
        if persist:
            save_state()


async def refresh_identity_info(send_as_id, *, source="ui", actor_id=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, "身份不存在"
    if is_identity_info_refresh_pending(send_as_id):
        return True, "该身份信息正在更新中，请稍后刷新查看"

    command = format_identity_info_command()
    requested_at = time.time()

    with use_identity(send_as_id):
        _begin_identity_refresh_runtime(requested_at)

    msg = await send_game_command(command, send_as_id=send_as_id, max_retry=1)
    if not msg:
        _set_identity_info_error(send_as_id, "获取请求发送失败，请手动重新获取")
        return False, "角色信息获取发送失败，请手动重新获取"

    with use_identity(send_as_id):
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        _record_identity_refresh_message(getattr(msg, "id", 0), requested_at=sent_at)

    extra_commands = (CMD_YUANYING_STATUS, CMD_SECOND_SOUL_STATUS)
    extra_sent = []
    extra_failed = []
    for extra_command in extra_commands:
        extra_msg = await send_game_command(extra_command, send_as_id=send_as_id, max_retry=1)
        if extra_msg:
            extra_sent.append(extra_command)
        else:
            extra_failed.append(extra_command)

    actor_suffix = f"，操作者：{actor_id}" if actor_id is not None else ""
    extra_suffix = f"，附加读取：{'、'.join(extra_sent)}" if extra_sent else ""
    failed_suffix = f"，失败：{'、'.join(extra_failed)}" if extra_failed else ""
    console_log(
        f"🪪 已发起身份信息刷新：{command}{extra_suffix}{failed_suffix}，来源：{source}{actor_suffix}",
        scope="identity",
        send_as_id=send_as_id,
    )
    if extra_failed:
        return True, f"已开始获取角色信息；附加读取部分发送失败：{'、'.join(extra_failed)}"
    return True, "已开始获取角色信息、元婴和第二元神信息，请等待"


async def run_identity_info_followup_scheduler(now):
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue

        trigger_msg_ids = []
        command = ""
        with use_identity(identity_id):
            due_at = float(state.get("identity_info_followup_due_at", 0) or 0)
            if due_at <= 0 or due_at > now:
                continue

            primary_payload = _normalize_identity_refresh_payload(state.get("identity_info_primary_payload") or {})
            missing_fields = _get_identity_refresh_missing_fields(primary_payload)
            if not missing_fields:
                _final_payload, trigger_msg_ids = _finalize_identity_refresh_success(identity_id, primary_payload, now)
                save_state()
            elif any(_is_identity_refresh_command(get_pending_command(pending)) for pending in state["pending_tasks"].values()):
                continue
            else:
                command = format_battle_power_command()

        if trigger_msg_ids:
            for trigger_msg_id in trigger_msg_ids:
                await delete_identity_info_trigger_msg(identity_id, trigger_msg_id, persist=False)
            save_state()
            continue
        if not command:
            continue

        msg = await send_game_command(command, send_as_id=identity_id, max_retry=1)
        if not msg:
            with use_identity(identity_id):
                _clear_identity_refresh_runtime(error="角色信息补全请求发送失败，请手动重新获取")
                save_state()
            continue

        with use_identity(identity_id):
            sent_at = float(getattr(msg, "sent_at", 0) or time.time())
            _record_identity_refresh_message(getattr(msg, "id", 0), requested_at=sent_at, clear_followup=True)

        console_log(
            f"🪪 已触发身份信息补全：{command}",
            scope="identity",
            send_as_id=identity_id,
        )


async def handle_identity_info_reply(text, now, reply_to, current_msg_id):
    reply_msg_id = int(getattr(reply_to, "id", 0) or 0)
    current_msg_id = int(current_msg_id or 0)
    if not reply_msg_id or not current_msg_id:
        return False

    send_as_id = get_current_identity_id()
    final_payload = None
    trigger_msg_ids = []
    with use_identity(send_as_id):
        reply_msg_ids = _get_identity_refresh_tracking_ids()
        if reply_msg_id not in reply_msg_ids:
            return False

        primary_parsed = _parse_identity_info_partial(text)
        battle_parsed = None if primary_parsed else _parse_battle_power_info(text)
        is_followup_reply = bool(battle_parsed)
        parsed = primary_parsed or battle_parsed
        if not parsed:
            _track_identity_refresh_message(current_msg_id)
            return False

        normalized_payload = _normalize_identity_refresh_payload(parsed)
        missing_fields = _get_identity_refresh_missing_fields(normalized_payload)
        _track_identity_refresh_message(current_msg_id)
        state["identity_info_last_error"] = ""

        if not is_followup_reply:
            state["identity_info_primary_payload"] = dict(normalized_payload)
            if missing_fields:
                _schedule_identity_refresh_followup(now)
                mark_dirty()
            else:
                final_payload, trigger_msg_ids = _finalize_identity_refresh_success(send_as_id, normalized_payload, now)
        else:
            state["identity_info_followup_due_at"] = 0
            primary_payload = _normalize_identity_refresh_payload(state.get("identity_info_primary_payload") or {})
            merged_payload = _merge_identity_refresh_payload(primary_payload, normalized_payload)
            final_payload, trigger_msg_ids = _finalize_identity_refresh_success(send_as_id, merged_payload, now)
    enforce_identity_module_availability(send_as_id, persist=False)

    if trigger_msg_ids:
        for trigger_msg_id in trigger_msg_ids:
            await delete_identity_info_trigger_msg(send_as_id, trigger_msg_id, persist=False)
    save_state()
    if not final_payload:
        return False
    if trigger_msg_ids:
        await send_audit_log(
            f"🪪 已更新身份信息：{final_payload['daohao']}｜{final_payload['realm']}｜{final_payload['sect_name']}",
            scope="identity",
            send_as_id=send_as_id,
        )
    return True


async def register_identity(send_as_id_raw, *, source="ui", actor_id=None, account_id=None):
    send_as_id_text = str(send_as_id_raw or "").strip()
    if not send_as_id_text:
        return False, "身份 ID 不能为空", None
    try:
        candidate_id = int(send_as_id_text)
    except (TypeError, ValueError):
        return False, "身份 ID 必须是数字", None
    try:
        if account_id:
            account_id = int(account_id)
            if is_account_offline(account_id):
                return False, f"账号 {account_id} 离线，请重新登录后再绑定身份", None
            tc = get_registered_client(account_id)
            if tc is None:
                return False, f"账号 {account_id} 未登录，请重新登录后再绑定身份", None
        else:
            tc = None
            for candidate_account_id, candidate_client in get_all_clients().items():
                if not is_account_offline(candidate_account_id):
                    tc = candidate_client
                    break
            if tc is None:
                return False, "尚无已登录账号，请先在「账号管理」中登录一个 Telegram 账号", None
        send_as_entity = await tc.get_entity(candidate_id)
    except Exception as e:
        return False, f"身份不存在或当前账号无权访问该身份 ID：{e}", None

    canonical_id = int(getattr(send_as_entity, "id", 0) or 0)
    if canonical_id <= 0:
        return False, "无法解析有效的 身份 ID", None
    if canonical_id in get_identity_ids():
        display_name = get_identity_display_name(canonical_id)
        console_log(
            f"🪪 新增身份命中已存在：来源={source}",
            scope="identity",
            send_as_id=canonical_id,
        )
        return True, f"身份已存在：{display_name}", canonical_id

    ensure_identity_registered(canonical_id)
    hydrate_identity_profile(send_as_entity)
    if account_id:
        set_identity_account(canonical_id, account_id)
    with use_identity(canonical_id):
        state["tree_enabled"] = False
        state["pet_enabled"] = False
        state["quiz_enabled"] = False
        state["yuanying_enabled"] = False
        state["deep_retreat_enabled"] = False
        state["checkin_enabled"] = False
        state["tower_enabled"] = False
    initialize_identity_runtime(canonical_id)
    save_state()
    display_name = get_identity_display_name(canonical_id)
    actor_suffix = f"，操作者：{actor_id}" if actor_id is not None else ""
    await send_audit_log(
        f"🪪 已新增身份，来源：{source}{actor_suffix}，默认全部模块关闭",
        scope="identity",
        send_as_id=canonical_id,
    )

    return True, f"已新增身份：{display_name}，默认全部模块已关闭", canonical_id


async def delete_identity(send_as_id, *, source="ui", actor_id=None):
    send_as_id = int(send_as_id)
    if not has_identity(send_as_id):
        return False, f"未知身份: {send_as_id}"
    display_name = get_identity_display_name(send_as_id)
    account_id = get_identity_account(send_as_id)
    clear_identity_runtime_tracking(send_as_id)
    remove_identity(send_as_id)
    delete_identity_from_db(send_as_id)
    save_state()
    actor_suffix = f"，操作者：{actor_id}" if actor_id is not None else ""
    account_suffix = f"，保留账号 {account_id} 登录态" if account_id > 0 else ""
    await send_audit_log(
        f"🗑️ 已删除身份，来源：{source}{actor_suffix}{account_suffix}",
        scope="global",
    )
    return True, f"已删除身份：{display_name}{account_suffix}"


async def set_module_window_config(module_name, start_hour_utc, end_hour_utc, send_as_id=None):
    if module_name not in {"点卯", "闯塔"}:
        return False, f"模块暂不支持窗口设置: {module_name}"
    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    now = time.time()
    for identity_id in target_ids:
        with use_identity(identity_id):
            set_module_window_hours(module_name, identity_id, start_hour_utc, end_hour_utc)
            profile = get_send_as_profile(identity_id)
            if module_name == "点卯":
                if state["checkin_enabled"]:
                    start_hour = int(profile.get("checkin_window_start_hour_utc"))
                    end_hour = int(profile.get("checkin_window_end_hour_utc"))
                    if state["last_checkin_done_day"] == get_checkin_day_key(now):
                        state["next_checkin_time"] = calc_next_daily_window_after_completion(start_hour, end_hour, now)
                    else:
                        state["next_checkin_time"] = calc_next_daily_window_time(start_hour, end_hour, now)
            elif module_name == "闯塔":
                if state["tower_enabled"]:
                    start_hour = int(profile.get("tower_window_start_hour_utc"))
                    end_hour = int(profile.get("tower_window_end_hour_utc"))
                    if state["last_tower_day"] == get_day_key(now):
                        state["next_tower_time"] = calc_next_daily_window_after_completion(start_hour, end_hour, now)
                    else:
                        state["next_tower_time"] = calc_next_daily_window_time(start_hour, end_hour, now)
            save_state()
    if len(target_ids) == 1:
        console_log(
            f"🕒 已更新{module_name}执行窗口",
            scope="identity",
            send_as_id=target_ids[0],
        )
    else:
        console_log(f"🕒 已更新{module_name}执行窗口：全部身份", scope="global")
    return True, f"已更新{module_name}执行窗口"


async def set_identity_enabled(send_as_id, enabled, *, source="ui", actor_id=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"

    enabled = _coerce_control_bool(enabled)
    if get_identity_enabled(send_as_id) == enabled:
        return True, f"身份状态未变化[{get_identity_display_name(send_as_id)}]"

    set_identity_enabled_profile(send_as_id, enabled)
    if enabled:
        initialize_identity_runtime(send_as_id, time.time())
    else:
        with use_identity(send_as_id):
            _clear_startup_module_alerts()
    save_state()

    action_text = "开启" if enabled else "暂停"
    actor_suffix = f"，操作者：{actor_id}" if actor_id is not None else ""
    await send_audit_log(
        f"🎭 已{action_text}身份，来源：{source}{actor_suffix}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已{action_text}身份[{get_identity_display_name(send_as_id)}]"


async def toggle_global_enabled(enabled, *, source="ui", actor_id=None):
    enabled = _coerce_control_bool(enabled)
    if get_global_enabled() == enabled:
        return True, "全局状态未变化"
    set_global_enabled_state(enabled)
    now = time.time()
    if enabled and get_guanxing_monitor_enabled():
        _restore_guanxing_monitor_runtime(now)
    for identity_id in get_identity_ids():
        if enabled:
            if get_identity_enabled(identity_id):
                initialize_identity_runtime(identity_id, now)
        else:
            with use_identity(identity_id):
                _clear_startup_module_alerts()
    if enabled:
        spread_overdue_runtime_timers(now, reason="全局恢复")
        _reset_safety_watchdog_fuse_marker(now)
    save_state()
    action_text = "恢复运行" if enabled else "全局暂停"
    actor_suffix = f"，操作者：{actor_id}" if actor_id is not None else ""
    await send_audit_log(
        f"🌐 已{action_text}，来源：{source}{actor_suffix}。",
        priority="high" if not enabled else "medium",
    )
    return True, f"已{action_text}"


def _reset_safety_watchdog_fuse_marker(now):
    state_dir = Path(STATE_DIR)
    fused_marker = state_dir / "safety_watchdog_fused.json"
    reset_marker = state_dir / "safety_watchdog_reset.json"
    try:
        if fused_marker.exists():
            fused_marker.unlink()
        reset_marker.parent.mkdir(parents=True, exist_ok=True)
        reset_marker.write_text(
            json.dumps(
                {
                    "reset_at": datetime.fromtimestamp(float(now), TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
                    "reset_at_epoch": float(now),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        console_log(f"⚠️ 重置安全熔断标记失败: {exc}")


async def set_module_enabled(module_name, enabled, send_as_id=None, *, skip_unavailable=False, allow_empty=False):
    key = MODULE_KEY_MAP.get(module_name)
    if not key:
        return False

    enabled = _coerce_control_bool(enabled)
    if module_name == "观星监控":
        now = time.time()
        if bool(get_guanxing_monitor_enabled()) != enabled:
            if enabled:
                _manual_enable_guanxing_monitor_module_state(now)
            else:
                _disable_guanxing_monitor_module_state()
            save_state()
        action_text = "开启" if enabled else "关闭"
        console_log(f"🎛️ 已{action_text}{module_name}模块", scope="global")
        return True, ""

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    original_target_count = len(target_ids)
    if enabled:
        unavailable_reasons = {
            identity_id: get_module_unavailable_reason(module_name, identity_id)
            for identity_id in target_ids
        }
        unavailable_reasons = {identity_id: reason for identity_id, reason in unavailable_reasons.items() if reason}
        unavailable_ids = list(unavailable_reasons.keys())
        if unavailable_ids:
            if skip_unavailable and send_as_id is None:
                unavailable_id_set = set(unavailable_ids)
                target_ids = [identity_id for identity_id in target_ids if identity_id not in unavailable_id_set]
                if not target_ids:
                    reason_set = set(unavailable_reasons.values())
                    message = next(iter(reason_set)) if len(reason_set) == 1 else f"没有身份可开启{module_name}模块"
                    if allow_empty:
                        console_log(f"🎛️ 已跳过{module_name}模块：无可用身份", scope="global")
                        return True, message
                    return False, message
            elif len(unavailable_ids) == 1:
                identity_id = unavailable_ids[0]
                return False, f"{get_identity_display_name(identity_id)} {unavailable_reasons[identity_id]}"
            else:
                return False, f"存在身份{unavailable_reasons[unavailable_ids[0]]}"
    now = time.time()
    module_state_setter = MODULE_STATE_SETTERS.get(key)
    manual_toggle_handler = MANUAL_MODULE_TOGGLE_HANDLERS.get(module_name)
    for identity_id in target_ids:
        with use_identity(identity_id):
            if bool(state.get(key, False)) == enabled:
                if enabled:
                    _clear_startup_module_alerts(module_name)
                continue
            if manual_toggle_handler:
                enable_handler, disable_handler = manual_toggle_handler
                if enabled:
                    enable_handler(now)
                else:
                    disable_handler()
            elif module_state_setter:
                module_state_setter(enabled, now)
            else:
                state[key] = enabled
            if enabled:
                _clear_startup_module_alerts(module_name)
            save_state()

    action_text = "开启" if enabled else "关闭"
    if send_as_id is not None and len(target_ids) == 1:
        console_log(
            f"🎛️ 已{action_text}{module_name}模块",
            scope="identity",
            send_as_id=target_ids[0],
        )
    elif enabled and skip_unavailable and len(target_ids) < original_target_count:
        console_log(f"🎛️ 已{action_text}{module_name}模块：可用身份 {len(target_ids)}/{original_target_count}", scope="global")
    else:
        console_log(f"🎛️ 已{action_text}{module_name}模块：全部身份", scope="global")
    return True, ""


def _split_storage_bag_report_chunks(text, limit=STORAGE_BAG_REPORT_REPLY_LIMIT):
    raw = str(text or "").strip()
    if not raw:
        return []
    chunks = []
    current = []
    current_len = 0
    for line in raw.splitlines():
        pending_len = len(line) + 1
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            for start in range(0, len(line), limit):
                chunks.append(line[start:start + limit])
            continue
        if current and current_len + pending_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += pending_len
    if current:
        chunks.append("\n".join(current))
    if len(chunks) <= 1:
        return chunks
    total = len(chunks)
    return [f"{chunk}\n\n({idx}/{total})" for idx, chunk in enumerate(chunks, 1)]


def _parse_storage_bag_report_options(raw_args):
    args_text = RE_WHITESPACE.sub(" ", str(raw_args or "").strip())
    if not args_text:
        return [], ""

    if args_text in {"帮助", "help", "-h", "--help"}:
        return None, (
            "【储物袋汇总】\n"
            ".储物袋汇总\n"
            ".储物袋汇总 竹星紫 4\n"
            ".储物袋汇总 详细\n\n"
            "默认读取 API/本地缓存，缓存为空再读历史快照；不发送游戏指令。"
        )

    report_args = ["--chunk-limit", "20000"]
    verbose = False
    for marker in ("最新", "latest", "全部", "all"):
        if args_text == marker or args_text.startswith(f"{marker} "):
            args_text = args_text.replace(marker, " ", 1)
    for marker in ("详细", "调试", "verbose"):
        if marker in args_text:
            verbose = True
            args_text = args_text.replace(marker, " ")

    today = datetime.now(TZ_LOCAL).date()
    if "今天" in args_text or "今日" in args_text:
        day_text = today.isoformat()
        report_args.extend(["--since", day_text, "--until", day_text])
        args_text = args_text.replace("今天", " ").replace("今日", " ")
    elif "昨天" in args_text or "昨日" in args_text:
        day_text = (today - timedelta(days=1)).isoformat()
        report_args.extend(["--since", day_text, "--until", day_text])
        args_text = args_text.replace("昨天", " ").replace("昨日", " ")
    else:
        recent_match = RE_STORAGE_BAG_RECENT_DAYS.search(args_text)
        if recent_match:
            days = max(1, min(30, int(recent_match.group(1))))
            since_text = (today - timedelta(days=days - 1)).isoformat()
            report_args.extend(["--since", since_text, "--until", today.isoformat()])
            args_text = f"{args_text[:recent_match.start()]} {args_text[recent_match.end():]}"

    if verbose:
        report_args.append("--verbose")

    selector = RE_WHITESPACE.sub(" ", args_text).strip()
    if selector:
        identity_id = resolve_identity_selector(selector)
        if identity_id is None:
            return None, f"❌ 找不到身份：{selector}"
        profile = get_send_as_profile(identity_id)
        username = (profile.get("username") or "").strip()
        report_args.extend(["--only-name", username or selector])

    return report_args, ""


async def _run_storage_bag_report(report_args):
    script_path = Path(PROJECT_ROOT_DIR) / "tools" / "storage_bag_report.py"
    if not script_path.exists():
        return False, f"❌ 储物袋汇总脚本不存在：{script_path}"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        str(script_path),
        *report_args,
        cwd=PROJECT_ROOT_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=STORAGE_BAG_REPORT_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return False, "❌ 储物袋汇总超时，已停止本次离线解析。"

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        error_text = stderr_text or stdout_text or f"脚本退出码 {proc.returncode}"
        return False, f"❌ 储物袋汇总失败：{error_text[:1000]}"
    return True, stdout_text or "无可用储物袋快照。"


async def _handle_storage_bag_report_command(event, raw_args):
    report_args, message = _parse_storage_bag_report_options(raw_args)
    if report_args is None:
        await reply_log_group_message(
            event,
            message,
            error_prefix="❌ 储物袋汇总回复失败",
            link_preview=False,
            scope="global",
            limit=1200,
        )
        return True

    ok, report_text = await _run_storage_bag_report(report_args)
    if not ok:
        await reply_log_group_message(
            event,
            report_text,
            error_prefix="❌ 储物袋汇总回复失败",
            link_preview=False,
            scope="global",
            limit=1200,
        )
        return True

    chunks = _split_storage_bag_report_chunks(report_text)
    if not chunks:
        chunks = ["无可用储物袋快照。"]
    for chunk in chunks:
        await reply_log_group_message(
            event,
            chunk,
            error_prefix="❌ 储物袋汇总回复失败",
            link_preview=False,
            scope="global",
            limit=STORAGE_BAG_REPORT_REPLY_LIMIT + 100,
        )
    return True


def _normalize_storage_bag_simple_find_query(raw_query):
    query = RE_WHITESPACE.sub(" ", str(raw_query or "").strip())
    if len(query) >= 2 and query[0] == query[-1] and query[0] in {"'", '"'}:
        query = query[1:-1].strip()
    return query


def _get_storage_bag_log_identity_name(identity_id):
    profile = get_send_as_profile(identity_id)
    label = str(profile.get("label") or "").strip()
    daohao = str(profile.get("daohao") or "").strip()
    username = str(profile.get("username") or "").strip()
    if label and daohao and label != daohao:
        return f"{label}[{daohao}]"
    return label or daohao or username or f"身份{int(identity_id or 0)}"


def _format_storage_bag_simple_find_text(raw_query):
    query = _normalize_storage_bag_simple_find_query(raw_query)
    if not query:
        return "用法：.还有多少 <物品名>"

    query_key = query.casefold()
    records = get_storage_bag_records()
    totals = {}
    holders = []
    configured_count = len(get_identity_ids())
    scanned_count = 0
    for identity_id in get_identity_ids():
        identity_id = int(identity_id)
        scanned_count += 1
        record = records.get(str(identity_id)) if isinstance(records, dict) else {}
        items = record.get("items") if isinstance(record, dict) else {}
        if not isinstance(items, dict):
            continue
        holder_items = {}
        for raw_name, raw_count in items.items():
            item_name = str(raw_name or "").strip()
            if not item_name or query_key not in item_name.casefold():
                continue
            try:
                count = int(raw_count or 0)
            except (TypeError, ValueError):
                count = 0
            if count <= 0:
                continue
            totals[item_name] = totals.get(item_name, 0) + count
            holder_items[item_name] = holder_items.get(item_name, 0) + count
        if holder_items:
            holders.append({
                "identity_id": identity_id,
                "name": _get_storage_bag_log_identity_name(identity_id),
                "items": holder_items,
                "total": sum(holder_items.values()),
            })

    lines = [
        f"📦 物资统计: {query}",
    ]
    if not totals:
        lines.extend([
            "📊 总计: 0",
            f"👥 角色: 配置 {configured_count} 个，扫描 {scanned_count}/{configured_count} 个，命中 0 个",
            "🎯 匹配: 无",
        ])
        return "\n".join(lines)

    total_all = sum(totals.values())
    exact_names = {item_name for item_name in totals if item_name.casefold() == query_key}
    if exact_names and len(exact_names) == len(totals):
        match_text = "精确匹配"
    elif exact_names:
        match_text = "精确+模糊"
    else:
        match_text = "模糊匹配"
    lines.extend([
        f"📊 总计: {total_all:,}",
        f"👥 角色: 配置 {configured_count} 个，扫描 {scanned_count}/{configured_count} 个，命中 {len(holders)} 个",
        f"🎯 匹配: {match_text}",
    ])

    if len(totals) > 1:
        lines.extend(["", f"📌 匹配物品 ({len(totals)})"])
        for item_name, count in sorted(totals.items(), key=lambda item: (item[0] != query, item[0])):
            lines.append(f"- {item_name}: {count:,}")

    lines.extend(["", f"📋 持有明细 ({len(holders)})"])
    for holder in sorted(holders, key=lambda item: (-int(item.get("total") or 0), str(item.get("name") or "")))[:30]:
        item_text = "，".join(
            f"{item_name} x{count:,}"
            for item_name, count in sorted((holder.get("items") or {}).items(), key=lambda item: (item[0] != query, item[0]))
        )
        lines.append(f"- {holder.get('name') or holder.get('identity_id')}: {item_text}")
    return "\n".join(lines)


async def _handle_storage_bag_simple_find_command(event, raw_query):
    await _reply_log_group_card(
        event,
        "物资统计",
        _format_storage_bag_simple_find_text(raw_query),
        error_prefix="❌ 储物袋轻查询回复失败",
    )
    return True


def _format_storage_bag_api_refresh_result(result, *, target_identity_id=None):
    result = result if isinstance(result, dict) else {}
    updated_ids = [int(identity_id or 0) for identity_id in result.get("updated_identity_ids") or [] if int(identity_id or 0)]
    target_text = _get_storage_bag_log_identity_name(target_identity_id) if target_identity_id else "全部身份"
    lines = [
        "📦 储物袋 API 更新",
        f"范围: {target_text}",
        f"结果: {'成功' if result.get('ok') else '未更新'}",
        f"刷新: {int(result.get('updated_count') or 0)} 个身份",
        f"内容变化: {int(result.get('changed_count') or 0)} 个身份",
        f"跳过: {int(result.get('skipped_count') or 0)} 个候选",
    ]
    if updated_ids:
        names = "、".join(_get_storage_bag_log_identity_name(identity_id) for identity_id in updated_ids[:8])
        if len(updated_ids) > 8:
            names += f" 等 {len(updated_ids)} 个"
        lines.append(f"明细: {names}")
    message = str(result.get("message") or "").strip()
    if message:
        lines.append(f"说明: {message}")
    return "\n".join(lines)


async def _handle_storage_bag_api_refresh_command(event, explicit_identity_id=None):
    target_ids = [int(explicit_identity_id)] if explicit_identity_id is not None else None
    try:
        result = await refresh_storage_bag_records_from_api(identity_ids=target_ids)
        body = _format_storage_bag_api_refresh_result(result, target_identity_id=explicit_identity_id)
    except Exception as exc:
        body = (
            "📦 储物袋 API 更新\n"
            f"范围: {_get_storage_bag_log_identity_name(explicit_identity_id) if explicit_identity_id else '全部身份'}\n"
            f"结果: 失败\n"
            f"原因: {exc}"
        )
    await _reply_log_group_card(
        event,
        "储物袋 API 更新",
        body,
        error_prefix="❌ 储物袋 API 更新回复失败",
    )
    return True


async def _handle_duel_config_command(event, text, explicit_identity_id=None):
    match = RE_CMD_DUEL_CONFIG.match(text)
    if not match:
        return False
    if explicit_identity_id is None:
        await _reply_log_group_card(
            event,
            "斗法配置",
            "必须指定单个身份：.设置斗法 <目标> [次数] @身份",
            error_prefix="❌ 斗法配置回复失败",
        )
        return True
    target = match.group(1)
    total_count = match.group(2)
    with use_identity(explicit_identity_id):
        config = apply_duel_config(target=target, total_count=total_count, reset_progress=True, now=time.time(), persist=True)
        body = "\n".join(
            [
                f"身份：{get_identity_display_name(explicit_identity_id)}",
                f"目标：{config['target'] or '未配置'}",
                f"次数：{config['total_count'] if config['total_count'] > 0 else '未配置'}",
                "进度：已重置",
            ]
        )
    await _reply_log_group_card(
        event,
        "斗法配置",
        body,
        error_prefix="❌ 斗法配置回复失败",
    )
    return True


THREE_SECT_MANUAL_USAGE = (
    "三宗门手动发送必须指定单个身份：\n"
    "- .合欢温养 @身份\n"
    "- .天星查盘 @身份\n"
    "- .天星观命 @身份\n"
    "- .天星定命 <紫微|天府|太阴|贪狼> @身份\n"
    "- .天星推命 <闭关|炼制|探索|斗法> @身份\n"
    "- .天星改命 <闭关|炼制|探索|斗法> @身份\n"
    "- .天星消劫 @身份\n"
    "- .天星暂停 @身份\n"
    "- .天星恢复 @身份\n"
    "- .阴罗查幡 @身份\n"
    "- .阴罗献祭 @身份\n"
    "- .阴罗召唤 @身份\n"
    "- .阴罗血洗 @身份\n"
    "- .阴罗收取 [槽位] @身份\n"
    "- .阴罗炼化 <槽位> <目标魂魄> @身份\n"
    "- .阴罗化煞 <数量> @身份"
)


XUTIAN_FOLLOWUP_MANUAL_USAGE = (
    "虚天后续抉择必须指定单个身份：\n"
    "- .选择道路 火 @身份\n"
    "- .阵策 稳 @身份\n"
    "- .争鼎 夺鼎 @身份\n"
    "- .后殿抉择 冲关 @身份\n"
    "- .后殿阵策 卦 @身份"
)


async def _reply_three_sect_manual_result(event, title, ok, message, identity_id=None, plan=None):
    lines = []
    if identity_id is not None:
        lines.append(f"身份：{get_identity_display_name(identity_id)}")
    lines.append(f"结果：{'已发送' if ok else '未发送'}")
    if message:
        lines.append(f"说明：{message}")
    if isinstance(plan, dict) and plan.get("command"):
        lines.append(f"命令：{plan.get('command')}")
    await _reply_log_group_card(
        event,
        title,
        "\n".join(lines),
        error_prefix=f"❌ {title}回复失败",
    )
    return True


async def _handle_xutian_followup_manual_command(event, text, explicit_identity_id):
    if not RE_CMD_XUTIAN_FOLLOWUP_MANUAL.match(text):
        return False
    if explicit_identity_id is None:
        await _reply_log_group_card(
            event,
            "虚天后续抉择",
            XUTIAN_FOLLOWUP_MANUAL_USAGE,
            error_prefix="❌ 虚天后续抉择回复失败",
        )
        return True
    if not get_identity_enabled(explicit_identity_id):
        await _reply_log_group_card(
            event,
            "虚天后续抉择",
            f"身份：{get_identity_display_name(explicit_identity_id)}\n结果：未发送\n说明：身份已停用。",
            error_prefix="❌ 虚天后续抉择回复失败",
        )
        return True
    msg = await send_game_command(
        text,
        track=False,
        send_as_id=explicit_identity_id,
        priority="urgent_reactive",
        source_module="自动副本",
        op_id=f"xutian_followup_manual:{int(getattr(event, 'id', 0) or 0)}:{explicit_identity_id}:{text}",
        chain_id="xutian_followup",
        delete_policy="keep",
    )
    ok = bool(msg)
    await _reply_log_group_card(
        event,
        "虚天后续抉择",
        "\n".join([
            f"身份：{get_identity_display_name(explicit_identity_id)}",
            f"结果：{'已发送' if ok else '未发送'}",
            f"命令：{text}",
            *([] if ok else ["说明：发送失败或被安全锁拦截。"]),
        ]),
        error_prefix="❌ 虚天后续抉择回复失败",
    )
    return True


async def _handle_three_sect_manual_command(event, text, explicit_identity_id):
    hehuan_match = RE_CMD_HEHUAN_MANUAL.match(text)
    tianxing_match = RE_CMD_TIANXING_MANUAL.match(text)
    yinluo_match = RE_CMD_YINLUO_MANUAL.match(text)
    if not (hehuan_match or tianxing_match or yinluo_match):
        return False

    if explicit_identity_id is None:
        await _reply_log_group_card(
            event,
            "三宗门手动发送",
            THREE_SECT_MANUAL_USAGE,
            error_prefix="❌ 三宗门手动发送回复失败",
        )
        return True
    if not get_identity_enabled(explicit_identity_id):
        return await _reply_three_sect_manual_result(
            event,
            "三宗门手动发送",
            False,
            "身份已停用。",
            explicit_identity_id,
        )

    if hehuan_match:
        module_name = "合欢宗"
        if not is_module_available(module_name, explicit_identity_id):
            return await _reply_three_sect_manual_result(
                event,
                "合欢宗手动发送",
                False,
                f"{module_name}对该身份不可用。",
                explicit_identity_id,
            )
        ok, message, plan = await execute_hehuan_manual_action(
            "warm",
            send_as_id=explicit_identity_id,
        )
        return await _reply_three_sect_manual_result(event, "合欢宗手动发送", ok, message, explicit_identity_id, plan)

    if tianxing_match:
        module_name = "天星宗"
        if not is_module_available(module_name, explicit_identity_id):
            return await _reply_three_sect_manual_result(
                event,
                "天星宗手动发送",
                False,
                f"{module_name}对该身份不可用。",
                explicit_identity_id,
            )
        action = tianxing_match.group(1) or ""
        arg = tianxing_match.group(2) or ""
        ok, message, plan = await execute_tianxing_manual_action(
            action,
            arg,
            send_as_id=explicit_identity_id,
        )
        return await _reply_three_sect_manual_result(event, "天星宗手动发送", ok, message, explicit_identity_id, plan)

    module_name = "阴罗宗"
    if not is_module_available(module_name, explicit_identity_id):
        return await _reply_three_sect_manual_result(
            event,
            "阴罗宗手动发送",
            False,
            f"{module_name}对该身份不可用。",
            explicit_identity_id,
        )
    action = yinluo_match.group(1) or ""
    arg = yinluo_match.group(2) or ""
    ok, message, plan = await execute_yinluo_manual_action(
        action,
        arg,
        send_as_id=explicit_identity_id,
    )
    return await _reply_three_sect_manual_result(event, "阴罗宗手动发送", ok, message, explicit_identity_id, plan)


async def _handle_tianxing_automation_control_command(event, raw_text):
    match = RE_CMD_TIANXING_AUTOMATION_CONTROL.match((raw_text or "").strip())
    if not match:
        return False
    action = match.group(1) or ""
    selector = match.group(2) or ""
    if not selector:
        await _reply_log_group_card(
            event,
            "天星自动接管",
            "必须指定单个身份：.天星暂停 @身份 或 .天星恢复 @身份",
            error_prefix="❌ 天星自动接管回复失败",
        )
        return True
    identity_id, error = resolve_identity_selector_detail(selector)
    if identity_id is None:
        await _reply_log_group_card(
            event,
            "天星自动接管",
            f"❌ {error or f'找不到身份：{selector}'}",
            error_prefix="❌ 天星自动接管回复失败",
        )
        return True
    if not get_identity_enabled(identity_id):
        await _reply_log_group_card(
            event,
            "天星自动接管",
            f"身份：{get_identity_display_name(identity_id)}\n结果：未变更\n说明：身份已停用。",
            error_prefix="❌ 天星自动接管回复失败",
        )
        return True
    if not is_module_available("天星宗", identity_id):
        await _reply_log_group_card(
            event,
            "天星自动接管",
            f"身份：{get_identity_display_name(identity_id)}\n结果：未变更\n说明：天星宗对该身份不可用。",
            error_prefix="❌ 天星自动接管回复失败",
        )
        return True

    paused = action == "暂停"
    with use_identity(identity_id):
        set_tianxing_automation_paused(paused, now=time.time(), reason="日志群手动暂停")
        pause_text = get_tianxing_automation_pause_text()
    await _reply_log_group_card(
        event,
        "天星自动接管",
        "\n".join(
            [
                f"身份：{get_identity_display_name(identity_id)}",
                f"结果：{'已暂停' if paused else '已恢复'}",
                f"自动接管：{pause_text}",
                "说明：暂停期间天星自动调度、炼制攒点和路线前置不接管；手动命令仍可使用。",
            ]
        ),
        error_prefix="❌ 天星自动接管回复失败",
    )
    return True


async def handle_log_group_command(event):
    if event.chat_id != LOG_GROUP_ID:
        return False

    try:
        sender_id = int(getattr(event, "sender_id", None) or 0)
    except (TypeError, ValueError):
        return False
    if sender_id not in ADMIN_IDS:
        return False

    raw_text = (event.raw_text or "").strip()
    if not raw_text:
        return False

    storage_bag_match = RE_CMD_STORAGE_BAG_REPORT.match(raw_text)
    if storage_bag_match:
        return await _handle_storage_bag_report_command(event, storage_bag_match.group(2) or "")
    storage_bag_simple_find_match = RE_CMD_STORAGE_BAG_SIMPLE_FIND.match(raw_text)
    if storage_bag_simple_find_match:
        return await _handle_storage_bag_simple_find_command(event, storage_bag_simple_find_match.group(1) or "")

    if await _handle_tianxing_automation_control_command(event, raw_text):
        return True

    text, explicit_identity_id = split_command_identity_selector(raw_text)

    if RE_CMD_STORAGE_BAG_API_REFRESH.match(text):
        return await _handle_storage_bag_api_refresh_command(event, explicit_identity_id)

    if await _handle_duel_config_command(event, text, explicit_identity_id):
        return True

    if await _handle_xutian_followup_manual_command(event, text, explicit_identity_id):
        return True

    if await _handle_three_sect_manual_command(event, text, explicit_identity_id):
        return True

    if RE_CMD_HELP.match(text):
        await reply_log_group_message(
            event,
            _format_log_group_help_html(explicit_identity_id),
            error_prefix="❌ 指令帮助发送失败",
            link_preview=False,
            scope="global",
            parse_mode="HTML",
            preformatted=True,
            limit=1800,
        )
        return True

    if RE_CMD_ANALYSIS_SUMMARY.match(text):
        await _reply_log_group_card(
            event,
            "离线分析",
            _format_analysis_report_text("summary"),
            error_prefix="❌ 离线分析发送失败",
        )
        return True

    if RE_CMD_ANALYSIS_HEALTH.match(text):
        await _reply_log_group_card(
            event,
            "发送健康码",
            _format_analysis_report_text("health"),
            error_prefix="❌ 发送健康码发送失败",
        )
        return True

    if RE_CMD_RUNTIME_HEALTH.match(text):
        await _reply_log_group_card(
            event,
            "运行健康摘要",
            _format_runtime_health_text(),
            error_prefix="❌ 运行健康摘要发送失败",
        )
        return True

    if RE_CMD_RUNTIME_HEALTH_DETAIL.match(text):
        await _reply_log_group_card(
            event,
            "运行健康详情",
            _format_runtime_health_detail_text(),
            error_prefix="❌ 运行健康详情发送失败",
        )
        return True

    if RE_CMD_ANALYSIS_LOG_GROUP.match(text):
        await _reply_log_group_card(
            event,
            "日志群分析",
            _format_analysis_report_text("log_group"),
            error_prefix="❌ 日志群分析发送失败",
        )
        return True

    if RE_CMD_ANALYSIS_WEBMINI.match(text):
        await _reply_log_group_card(
            event,
            "webmini分析",
            _format_analysis_report_text("webmini"),
            error_prefix="❌ webmini分析发送失败",
        )
        return True

    if RE_CMD_ANALYSIS_UNKNOWN.match(text):
        await _reply_log_group_card(
            event,
            "未知指令",
            _format_analysis_report_text("unknown"),
            error_prefix="❌ 未知指令发送失败",
        )
        return True

    if RE_CMD_STAGING_PREFLIGHT.match(text):
        await _reply_log_group_card(
            event,
            "待上线预检",
            _format_staging_preflight_text(),
            error_prefix="❌ 待上线预检发送失败",
        )
        return True

    if RE_CMD_AUDIT_PUSH_STATUS.match(text):
        await _reply_log_group_card(
            event,
            "日志推送状态",
            get_audit_push_status_text(),
            error_prefix="❌ 日志推送状态发送失败",
        )
        return True

    if RE_CMD_AUDIT_FLUSH_SUMMARY.match(text):
        total, kind_count = get_low_priority_audit_pending_counts()
        if total <= 0:
            body = "当前没有待汇总的低优先级日志。"
        else:
            flushed = await flush_low_priority_audit_summary()
            if flushed:
                body = f"已发送低优先级日志汇总：{total} 条 / {kind_count} 类。"
            else:
                body = f"发送失败，明细已保留，稍后会自动重试：{total} 条 / {kind_count} 类。"
        await _reply_log_group_card(
            event,
            "低优先级日志汇总",
            body,
            error_prefix="❌ 低优先级日志汇总状态发送失败",
        )
        return True

    if RE_CMD_GLOBAL_PAUSE.match(text):
        ok, message = await toggle_global_enabled(False, source="log_group", actor_id=sender_id)
        status_text = "🌐 全局状态：已暂停" if ok else f"❌ {message}"
        await _reply_log_group_card(
            event,
            "全局控制结果",
            status_text,
            error_prefix="❌ 全局暂停回复失败",
        )
        return True

    if RE_CMD_GLOBAL_RESUME.match(text):
        ok, message = await toggle_global_enabled(True, source="log_group", actor_id=sender_id)
        status_text = "🌐 全局状态：运行中" if ok else f"❌ {message}"
        await _reply_log_group_card(
            event,
            "全局控制结果",
            status_text,
            error_prefix="❌ 全局恢复回复失败",
        )
        return True

    if RE_CMD_ENABLE_ALL.match(text):
        for module_name in MODULE_NAMES:
            ok, message = await set_module_enabled(
                module_name,
                True,
                send_as_id=explicit_identity_id,
                skip_unavailable=explicit_identity_id is None,
                allow_empty=True,
            )
            if not ok:
                await _reply_log_group_card(
                    event,
                    "模块切换失败",
                    f"❌ {message}",
                    error_prefix="❌ 模块状态回复失败",
                )
                return True
        prefix = "✅ 已开启全部模块"
        await _reply_log_group_card(
            event,
            prefix,
            get_module_status_text(explicit_identity_id),
            error_prefix="❌ 模块状态回复失败",
        )
        return True

    if RE_CMD_DISABLE_ALL.match(text):
        for module_name in MODULE_NAMES:
            ok, message = await set_module_enabled(module_name, False, send_as_id=explicit_identity_id)
            if not ok:
                await _reply_log_group_card(
                    event,
                    "模块切换失败",
                    f"❌ {message}",
                    error_prefix="❌ 模块状态回复失败",
                )
                return True
        prefix = "✅ 已关闭全部模块"
        await _reply_log_group_card(
            event,
            prefix,
            get_module_status_text(explicit_identity_id),
            error_prefix="❌ 模块状态回复失败",
        )
        return True

    for pattern, module_name, enabled in RE_CMD_ENABLE_PATTERNS:
        if pattern.match(text):
            ok, message = await set_module_enabled(
                module_name,
                enabled,
                send_as_id=explicit_identity_id,
                skip_unavailable=enabled and explicit_identity_id is None,
            )
            if not ok:
                await _reply_log_group_card(
                    event,
                    "模块切换失败",
                    f"❌ {message}",
                    error_prefix="❌ 模块状态回复失败",
                )
                return True
            action_text = "开启" if enabled else "关闭"
            status_text = get_module_status_text(explicit_identity_id)
            prefix = f"✅ 已{action_text}{module_name}模块"
            await _reply_log_group_card(
                event,
                prefix,
                status_text,
                error_prefix="❌ 模块状态回复失败",
            )
            return True

    if RE_CMD_LOGIN.match(text):
        login_token = issue_ui_login_token(sender_id)
        login_url = build_ui_login_url(login_token)
        await reply_log_group_message(
            event,
            "🔐 UI 登录链接\n"
            f"{login_url}\n\n"
            "- 浏览器打开后即可登录\n"
            "- 链接 1 小时有效\n"
            "- 会话 24 小时无请求自动失效",
            error_prefix="❌ UI 登录链接发送失败",
            link_preview=False,
            scope="global",
        )
        return True

    if RE_CMD_STATUS.match(text):
        await _reply_log_group_card(
            event,
            "模块状态",
            get_module_status_text(explicit_identity_id),
            error_prefix="❌ 模块状态发送失败",
        )
        return True

    if RE_CMD_PASSIVE_INBOX_STATUS.match(text):
        await _reply_log_group_card(
            event,
            "消息盒子状态",
            get_passive_inbox_status_text(),
            error_prefix="❌ 消息盒子状态发送失败",
        )
        return True

    shadow_match = RE_CMD_MESSAGE_BOX_SHADOW.match(text)
    if shadow_match:
        await _reply_log_group_card(
            event,
            "消息盒子 shadow",
            get_message_box_shadow_status_text(limit=int(shadow_match.group(1) or 500)),
            error_prefix="❌ 消息盒子 shadow 发送失败",
        )
        return True

    contract_match = RE_CMD_MESSAGE_CONTRACT_STATUS.match(text)
    if contract_match:
        selector = str(contract_match.group(1) or "").strip()
        reason = selector if selector in MESSAGE_CONTRACT_GAP_REASONS else ""
        module = selector if selector and "_" not in selector else ""
        family = selector if selector and "_" in selector and not reason else ""
        await _reply_log_group_card(
            event,
            "消息契约",
            get_message_contract_status_text(module=module, family=family, reason=reason),
            error_prefix="❌ 消息契约状态发送失败",
        )
        return True

    if RE_CMD_DUNGEON_QUERY_ALIAS.match(text):
        panel = build_log_group_replica_panel(text, fallback_chat_id=getattr(event, "chat_id", 0))
        await _reply_log_group_card(
            event,
            "副本面板",
            panel.get("text") or format_log_group_replica_panel(text),
            error_prefix="❌ 副本面板发送失败",
            buttons=panel.get("buttons"),
        )
        return True

    if RE_CMD_DUNGEON_CD_OVERVIEW.match(text):
        await _reply_log_group_card(
            event,
            "副本 CD 概览",
            format_log_group_replica_cd_overview(),
            error_prefix="❌ 副本 CD 概览发送失败",
        )
        return True

    if RE_CMD_DUNGEON_HELP.match(text):
        await _reply_log_group_card(
            event,
            "副本帮助",
            format_log_group_replica_help(),
            error_prefix="❌ 副本帮助发送失败",
        )
        return True

    for pattern, module_name in RE_CMD_SINGLE_STATUS_PATTERNS:
        if pattern.match(text):
            await _reply_log_group_card(
                event,
                f"{module_name}状态",
                get_single_module_status_text(module_name, explicit_identity_id),
                error_prefix=f"❌ {module_name}状态发送失败",
            )
            return True

    return False


__all__ = [
    "enforce_identity_module_availability",
    "get_identity_info_refresh_error",
    "get_identity_info_refresh_state",
    "get_module_status_text",
    "set_identity_enabled",
    "get_single_module_status_text",
    "handle_identity_info_reply",
    "handle_log_group_command",
    "handle_realm_breakthrough_broadcast",
    "hydrate_identity_profile",
    "initialize_identity_runtime",
    "is_identity_info_refresh_pending",
    "get_startup_module_alerts",
    "run_startup_account_integrity_check",
    "refresh_identity_info",
    "run_identity_info_followup_scheduler",
    "register_identity",
    "delete_identity",
    "scan_startup_timeout_tasks",
    "set_module_enabled",
    "set_module_window_config",
    "spread_overdue_runtime_timers",
    "toggle_global_enabled",
]
