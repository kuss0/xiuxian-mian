import copy
import contextvars
from contextlib import contextmanager

from .config import (
    CHECKIN_WINDOW_END_HOUR_UTC,
    CHECKIN_WINDOW_START_HOUR_UTC,
    DEFAULT_PET_NAME,
    GAME_BOT_IDS,
    GAME_GROUP_ID,
    GAME_TOPIC_ID,
    GUANXING_TARGET_KEYWORDS,
    MODULE_NAMES,
    STARGAZER_STAR_CHOICES,
    TIANTI_RANK_CHOICES,
    TOWER_WINDOW_END_HOUR_UTC,
    TOWER_WINDOW_START_HOUR_UTC,
    TZ_LOCAL,
)

_current_identity_id = contextvars.ContextVar("current_identity_id", default=0)
_identity_context_active = contextvars.ContextVar("identity_context_active", default=False)

IDENTITY_MODULE_COLUMNS = [
    "tree_enabled", "pet_enabled", "stargazer_enabled", "guanxing_enabled", "tianti_enabled", "tianti_wenxin_enabled", "tianti_gangfeng_enabled", "quiz_enabled", "jiyin_enabled", "nanlong_enabled", "yuanying_enabled", "deep_retreat_enabled", "small_world_enabled", "checkin_enabled", "tower_enabled",
    "second_soul_enabled", "taiyi_enabled", "taiyi_node_search_enabled",
    "is_maturing", "is_invading", "is_harvested", "pending_irrigation", "tree_bootstrap_check_needed",
    "checkin_teach_count", "checkin_teach_day", "last_checkin_done_day", "last_tower_day", "last_guanxing_done_day",
]
IDENTITY_TIMER_COLUMNS = [
    "next_irr_time", "next_guard_time", "next_pet_time", "next_stargazer_panel_time", "stargazer_collect_due_at", "next_tianti_status_time", "next_tianti_wenxin_time", "next_tianti_climb_time", "next_tianti_gangfeng_time", "next_checkin_time", "next_sect_teach_time",
    "next_tower_time", "next_quiz_time", "next_jiyin_time", "next_nanlong_time", "next_small_world_time", "next_yuanying_time", "next_deep_retreat_time",
    "next_second_soul_time", "second_soul_heart_demon_deadline",
    "next_taiyi_cycle_time", "taiyi_phase_entered_at", "taiyi_freeze_until",
]
IDENTITY_RUNTIME_COLUMNS = [
    "sect_teach_reply_to_msg_id", "last_checkin_msg_id", "last_sect_teach_msg_id", "checkin_cleanup_msg_ids",
    "last_tower_msg_id", "pet_last_error",
    "stargazer_last_panel_msg_id", "stargazer_last_action", "stargazer_idle_slot_count", "stargazer_dim_slot_count", "stargazer_ready_slot_count",
    "stargazer_busy_until", "stargazer_followup_due_at", "stargazer_wait_full_collect", "stargazer_collect_ready", "stargazer_soothe_before_collect",
    "guanxing_last_query_msg_id", "guanxing_last_panel_msg_id", "guanxing_panel_slot_key", "guanxing_last_panel_seen_at", "guanxing_last_shift_msg_id", "guanxing_last_shift_slot_key", "guanxing_last_shift_target", "guanxing_last_error",
    "tianti_status_reply_to_msg_id", "tianti_last_status_msg_id", "tianti_last_wenxin_msg_id", "tianti_last_climb_msg_id", "tianti_last_gangfeng_msg_id", "tianti_progress_current", "tianti_progress_total", "tianti_cycle_count", "tianti_gangfeng_level", "tianti_gangfeng_total", "tianti_cooldown_text", "tianti_wenxin_status", "tianti_gangfeng_status", "tianti_remaining_climb_count", "tianti_last_wenxin_day", "tianti_wenxin_last_trigger_key", "tianti_gangfeng_last_trigger_key", "tianti_last_skip_reason", "tianti_theoretical_max_stage", "tianti_wenxin_trigger_stage", "tianti_last_cost_xiuwei", "tianti_last_gain_xiuwei", "tianti_last_gain_contrib", "tianti_last_error",
    "quiz_reply_to_msg_id", "quiz_question", "quiz_options", "quiz_answer", "quiz_retry_count", "quiz_match_mode", "quiz_last_error", "quiz_last_matched_at",
    "jiyin_reply_to_msg_id", "jiyin_last_error",
    "nanlong_reply_to_msg_id", "nanlong_reply_due_at", "nanlong_last_msg_id", "nanlong_retry_count", "nanlong_last_command", "nanlong_last_error",
    "small_world_preach_reply_to_msg_id", "small_world_faith_value", "small_world_last_error",
    "yuanying_phase", "yuanying_probe_pending", "yuanying_summary_sent_at", "last_yuanying_summary_msg_id", "last_yuanying_command_time",
    "deep_retreat_phase", "deep_retreat_probe_pending", "deep_retreat_summary_sent_at", "last_deep_retreat_summary_msg_id", "last_deep_retreat_command_time",
    "second_soul_phase", "second_soul_heart_demon_msg_id", "second_soul_heart_demon_notified", "second_soul_last_error",
    "taiyi_yindao_element", "taiyi_phase", "taiyi_pending_node_name", "taiyi_freeze_reason", "taiyi_failure_history", "taiyi_last_error",
    "identity_info_reply_msg_ids", "last_identity_info_msg_id", "identity_info_last_error", "identity_info_last_requested_at", "identity_info_followup_due_at", "identity_info_primary_payload",
]
IDENTITY_JSON_COLUMNS = {"checkin_cleanup_msg_ids", "identity_info_reply_msg_ids", "quiz_options", "identity_info_primary_payload", "taiyi_failure_history"}
IDENTITY_BOOL_FIELDS = {
    "tree_enabled", "pet_enabled", "stargazer_enabled", "guanxing_enabled", "tianti_enabled", "tianti_wenxin_enabled", "tianti_gangfeng_enabled", "quiz_enabled", "jiyin_enabled", "nanlong_enabled", "yuanying_enabled", "deep_retreat_enabled", "small_world_enabled", "checkin_enabled", "tower_enabled",
    "second_soul_enabled", "taiyi_enabled", "taiyi_node_search_enabled",
    "is_maturing", "is_invading", "is_harvested", "pending_irrigation", "tree_bootstrap_check_needed",
    "stargazer_wait_full_collect", "stargazer_collect_ready", "stargazer_soothe_before_collect",
    "yuanying_probe_pending", "deep_retreat_probe_pending",
    "second_soul_heart_demon_notified",
}
META_STATE_KEYS = {"my_user_id", "game_group_id", "game_bot_ids", "game_topic_id", "forum_topics", "forum_topics_updated_at", "auto_delete_sent_messages", "global_enabled", "tiandao_judgement_enabled", "tiandao_judgement_pending", "tianji_quiz_pending", "guanxing_monitor_enabled", "guanxing_monitor_targets", "guanxing_shift_target", "next_guanxing_monitor_notify_time", "guanxing_monitor_slot_key", "guanxing_monitor_slot_start_at", "guanxing_monitor_slot_end_at", "guanxing_monitor_seen_panel", "guanxing_monitor_matched_keyword", "guanxing_monitor_matched_value", "guanxing_monitor_last_evolution_value", "guanxing_monitor_last_seen_at", "guanxing_monitor_last_notified_slot_key", "guanxing_round_state", "send_as_profiles", "identity_states", "identity_ids", "quiz_learning_watchers", "accounts", "identity_account_map"}
SEND_AS_PROFILE_DEFAULTS = {
    "username": "",
    "label": "",
    "daohao": "",
    "realm": "",
    "pet_name": DEFAULT_PET_NAME,
    "sect_name": "",
    "sect_updated_at": 0,
    "xiuwei_current": 0,
    "xiuwei_max": 0,
    "jiyin_choice": "",
    "nanlong_choice": "reject",
    "stargazer_star_choice": STARGAZER_STAR_CHOICES[0],
    "tianti_rank_choice": TIANTI_RANK_CHOICES[0],
    "stargazer_total_slots": 0,
    "checkin_window_start_hour_utc": CHECKIN_WINDOW_START_HOUR_UTC,
    "checkin_window_end_hour_utc": CHECKIN_WINDOW_END_HOUR_UTC,
    "tower_window_start_hour_utc": TOWER_WINDOW_START_HOUR_UTC,
    "tower_window_end_hour_utc": TOWER_WINDOW_END_HOUR_UTC,
    "enabled": True,
}
REALM_SORT_ORDER = [
    "炼气一层",
    "炼气二层",
    "炼气三层",
    "炼气四层",
    "炼气五层",
    "炼气六层",
    "炼气七层",
    "炼气八层",
    "炼气九层",
    "炼气十层",
    "炼气十一层",
    "炼气十二层",
    "炼气十三层",
    "筑基初期",
    "筑基中期",
    "筑基后期",
    "结丹初期",
    "结丹中期",
    "结丹后期",
    "元婴初期",
    "元婴中期",
    "元婴后期",
    "化神初期",
    "化神中期",
    "化神后期",
    "化神后期大圆满",
]
REALM_SORT_INDEX = {realm: index for index, realm in enumerate(REALM_SORT_ORDER)}
REALM_XIUWEI_MAX_MAP = {
    100: "炼气一层",
    150: "炼气二层",
    220: "炼气三层",
    300: "炼气四层",
    400: "炼气五层",
    520: "炼气六层",
    650: "炼气七层",
    800: "炼气八层",
    1000: "炼气九层",
    1250: "炼气十层",
    1500: "炼气十一层",
    1800: "炼气十二层",
    2200: "炼气十三层",
    5000: "筑基初期",
    10000: "筑基中期",
    30000: "筑基后期",
    50000: "结丹初期",
    100000: "结丹中期",
    200000: "结丹后期",
    500000: "元婴初期",
    1000000: "元婴中期",
    2000000: "元婴后期",
    4000000: "化神初期",
    8000000: "化神中期",
    16000000: "化神后期",
    32000000: "化神后期大圆满",
}
YUANYING_MIN_REALM = "元婴初期"
YUANYING_MIN_REALM_INDEX = REALM_SORT_INDEX[YUANYING_MIN_REALM]
SMALL_WORLD_MIN_REALM = "化神初期"
SMALL_WORLD_MIN_REALM_INDEX = REALM_SORT_INDEX[SMALL_WORLD_MIN_REALM]


def infer_realm_from_xiuwei_max(xiuwei_max):
    try:
        xiuwei_max = int(xiuwei_max or 0)
    except (TypeError, ValueError):
        return ""
    return REALM_XIUWEI_MAX_MAP.get(xiuwei_max, "")

IDENTITY_STATE_TEMPLATE = {
    # 业务对象开关（新身份默认全关，需手动开启）
    "tree_enabled": False,
    "pet_enabled": False,
    "quiz_enabled": False,
    "jiyin_enabled": False,
    "nanlong_enabled": False,
    "tianti_enabled": False,
    "tianti_wenxin_enabled": True,
    "tianti_gangfeng_enabled": True,
    "yuanying_enabled": False,
    "deep_retreat_enabled": False,
    "small_world_enabled": False,
    "checkin_enabled": False,
    "tower_enabled": False,
    "stargazer_enabled": False,
    "guanxing_enabled": False,

    # 灵树模块
    "next_irr_time": 0,
    "next_guard_time": 0,
    "is_maturing": False,
    "is_invading": False,
    "is_harvested": False,
    "pending_irrigation": False,
    "tree_bootstrap_check_needed": False,

    # 灵树运行态（不持久化）
    "tree_maturing_logged": False,
    "tree_harvest_followup_due_at": 0,

    # 法宝模块
    "next_pet_time": 0,
    "pet_last_error": "",

    # 观星台模块
    "next_stargazer_panel_time": 0,
    "stargazer_collect_due_at": 0,
    "stargazer_last_panel_msg_id": 0,
    "stargazer_last_action": "",
    "stargazer_idle_slot_count": 0,
    "stargazer_dim_slot_count": 0,
    "stargazer_ready_slot_count": 0,
    "stargazer_busy_until": 0,
    "stargazer_followup_due_at": 0,
    "stargazer_wait_full_collect": False,
    "stargazer_collect_ready": False,
    "stargazer_soothe_before_collect": False,

    # 观星模块
    "guanxing_last_query_msg_id": 0,
    "guanxing_last_panel_msg_id": 0,
    "guanxing_panel_slot_key": "",
    "guanxing_last_panel_seen_at": 0,
    "guanxing_last_shift_msg_id": 0,
    "guanxing_last_shift_slot_key": "",
    "guanxing_last_shift_target": "",
    "guanxing_last_error": "",

    # 登天阶模块
    "next_tianti_status_time": 0,
    "next_tianti_wenxin_time": 0,
    "next_tianti_climb_time": 0,
    "next_tianti_gangfeng_time": 0,
    "tianti_status_reply_to_msg_id": 0,
    "tianti_last_status_msg_id": 0,
    "tianti_last_wenxin_msg_id": 0,
    "tianti_last_climb_msg_id": 0,
    "tianti_last_gangfeng_msg_id": 0,
    "tianti_progress_current": 0,
    "tianti_progress_total": 12,
    "tianti_cycle_count": 0,
    "tianti_gangfeng_level": 0,
    "tianti_gangfeng_total": 12,
    "tianti_cooldown_text": "未记录",
    "tianti_wenxin_status": "未记录",
    "tianti_gangfeng_status": "未记录",
    "tianti_remaining_climb_count": 0,
    "tianti_last_wenxin_day": "",
    "tianti_wenxin_last_trigger_key": "",
    "tianti_gangfeng_last_trigger_key": "",
    "tianti_last_skip_reason": "",
    "tianti_theoretical_max_stage": 0,
    "tianti_wenxin_trigger_stage": 0,
    "tianti_last_cost_xiuwei": 0,
    "tianti_last_gain_xiuwei": 0,
    "tianti_last_gain_contrib": 0,
    "tianti_last_error": "",

    # 点卯模块
    "next_checkin_time": 0,
    "checkin_teach_count": 0,
    "checkin_teach_day": "",
    "last_checkin_done_day": "",
    "next_sect_teach_time": 0,
    "sect_teach_reply_to_msg_id": 0,
    "last_checkin_msg_id": 0,
    "last_sect_teach_msg_id": 0,
    "checkin_cleanup_msg_ids": [],

    # 闯塔模块
    "next_tower_time": 0,
    "last_tower_day": "",
    "last_tower_msg_id": 0,

    # 观星模块
    "last_guanxing_done_day": "",

    # 玄骨考校模块
    "next_quiz_time": 0,
    "quiz_reply_to_msg_id": 0,
    "quiz_question": "",
    "quiz_options": {},
    "quiz_answer": "",
    "quiz_retry_count": 0,
    "quiz_match_mode": "",
    "quiz_last_error": "",
    "quiz_last_matched_at": 0,

    # 极阴祖师模块
    "next_jiyin_time": 0,
    "jiyin_reply_to_msg_id": 0,
    "jiyin_last_error": "",

    # 南陇侯模块
    "next_nanlong_time": 0,
    "nanlong_reply_to_msg_id": 0,
    "nanlong_reply_due_at": 0,
    "nanlong_last_msg_id": 0,
    "nanlong_retry_count": 0,
    "nanlong_last_command": "",
    "nanlong_last_error": "",

    # 元婴模块
    "yuanying_phase": "idle",  # idle|launching|running|waiting_summary|post_summary_wait
    "next_yuanying_time": 0,
    "yuanying_probe_pending": False,
    "yuanying_summary_sent_at": 0,
    "last_yuanying_summary_msg_id": 0,
    "last_yuanying_command_time": 0,

    # 深度闭关模块
    "deep_retreat_phase": "idle",  # idle|launching|running|waiting_summary|post_summary_wait
    "next_deep_retreat_time": 0,
    "deep_retreat_probe_pending": False,
    "deep_retreat_summary_sent_at": 0,
    "last_deep_retreat_summary_msg_id": 0,
    "last_deep_retreat_command_time": 0,

    # 小世界模块
    "next_small_world_time": 0,
    "small_world_preach_reply_to_msg_id": 0,
    "small_world_faith_value": 0,
    "small_world_last_error": "",

    # 第二元神模块
    "second_soul_enabled": False,
    "second_soul_phase": "idle",  # idle|status_pending|cultivating|heart_demon_pending|injured|not_unlocked
    "next_second_soul_time": 0,
    "second_soul_heart_demon_msg_id": 0,
    "second_soul_heart_demon_deadline": 0,
    "second_soul_heart_demon_notified": False,
    "second_soul_status_msg_id": 0,
    "second_soul_train_msg_id": 0,
    "second_soul_last_error": "",

    # 太一门模块
    "taiyi_enabled": False,
    "taiyi_yindao_element": "水",
    "taiyi_node_search_enabled": False,
    "taiyi_phase": "idle",  # idle|yindao_pending|search_pending|define_pending|frozen
    "taiyi_pending_node_name": "",
    "taiyi_yindao_msg_id": 0,
    "taiyi_node_search_msg_id": 0,
    "taiyi_node_define_msg_id": 0,
    "next_taiyi_cycle_time": 0,
    "taiyi_phase_entered_at": 0,
    "taiyi_freeze_until": 0,
    "taiyi_freeze_reason": "",
    "taiyi_failure_history": [],
    "taiyi_last_error": "",

    # 运行态
    "identity_info_reply_msg_ids": [],
    "last_identity_info_msg_id": 0,
    "identity_info_last_error": "",
    "identity_info_last_requested_at": 0,
    "identity_info_followup_due_at": 0,
    "identity_info_primary_payload": {},
    "startup_module_alerts": [],

    # 元婴阻塞日志去重（运行态，不持久化）
    "yuanying_waiting_logged": False,
    "yuanying_protect_logged": False,

    # 深度闭关阻塞日志去重（运行态，不持久化）
    "deep_retreat_waiting_logged": False,
    "deep_retreat_protect_logged": False,

    # 追踪补发模块（运行态，不持久化）
    "pending_tasks": {},
    # { msg_id: sent_at }
    "my_msg_ids": {},
}

GLOBAL_STATE_DEFAULTS = {
    "my_user_id": None,
    "game_group_id": int(GAME_GROUP_ID),
    "game_bot_ids": sorted(int(bot_id) for bot_id in GAME_BOT_IDS),
    "game_topic_id": int(GAME_TOPIC_ID),
    "forum_topics": [],
    "forum_topics_updated_at": 0,
    "auto_delete_sent_messages": True,
    "global_enabled": True,
    "tiandao_judgement_enabled": False,
    "tiandao_judgement_pending": {},
    "tianji_quiz_pending": {},
    "guanxing_monitor_enabled": False,
    "guanxing_monitor_targets": list(GUANXING_TARGET_KEYWORDS[:2]),
    "guanxing_shift_target": "",
    "next_guanxing_monitor_notify_time": 0,
    "guanxing_monitor_slot_key": "",
    "guanxing_monitor_slot_start_at": 0,
    "guanxing_monitor_slot_end_at": 0,
    "guanxing_monitor_seen_panel": False,
    "guanxing_monitor_matched_keyword": "",
    "guanxing_monitor_matched_value": "",
    "guanxing_monitor_last_evolution_value": "",
    "guanxing_monitor_last_seen_at": 0,
    "guanxing_monitor_last_notified_slot_key": "",
    "guanxing_round_state": {},
    "send_as_profiles": {},
    "identity_states": {},
    "identity_ids": [],
    "quiz_learning_watchers": {},
    "accounts": {},
    "identity_account_map": {},
    "identity_membership_initialized": False,
}
_meta_state = copy.deepcopy(GLOBAL_STATE_DEFAULTS)


def new_identity_state():
    return copy.deepcopy(IDENTITY_STATE_TEMPLATE)


def has_identity(send_as_id):
    try:
        send_as_id = int(send_as_id)
    except (TypeError, ValueError):
        return False
    return send_as_id in _meta_state["identity_ids"]


def ensure_identity_registered(send_as_id):
    send_as_id = int(send_as_id)
    if send_as_id not in _meta_state["identity_ids"]:
        _meta_state["identity_ids"].append(send_as_id)
    if send_as_id not in _meta_state["identity_states"]:
        _meta_state["identity_states"][send_as_id] = new_identity_state()
    return _meta_state["identity_states"][send_as_id]


def remove_identity(send_as_id):
    send_as_id = int(send_as_id)
    removed = False
    if send_as_id in _meta_state["identity_ids"]:
        _meta_state["identity_ids"] = [identity_id for identity_id in _meta_state["identity_ids"] if identity_id != send_as_id]
        removed = True
    if _meta_state["identity_states"].pop(send_as_id, None) is not None:
        removed = True
    if _meta_state["send_as_profiles"].pop(send_as_id, None) is not None:
        removed = True
    identity_account_map = get_identity_account_map()
    if identity_account_map.pop(str(send_as_id), None) is not None:
        set_identity_account_map(identity_account_map)
        removed = True
    if not has_active_identity_context() and int(_current_identity_id.get() or 0) == send_as_id:
        fallback_identity_id = int((_meta_state["identity_ids"] or [0])[0] or 0)
        _current_identity_id.set(fallback_identity_id)
    return removed


def get_identity_ids():
    for send_as_id in list(_meta_state["identity_ids"]):
        if send_as_id not in _meta_state["identity_states"]:
            _meta_state["identity_states"][send_as_id] = new_identity_state()
    return list(_meta_state["identity_ids"])


def get_current_identity_id():
    send_as_id = int(_current_identity_id.get() or 0)
    if has_identity(send_as_id):
        return send_as_id
    identity_ids = get_identity_ids()
    if identity_ids:
        return int(identity_ids[0])
    return int(send_as_id or 0)


def has_active_identity_context():
    return bool(_identity_context_active.get())


def get_active_identity_id():
    if not has_active_identity_context():
        return None
    send_as_id = _current_identity_id.get()
    return int(send_as_id or 0) or None


def get_identity_state(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id or 0)
    if not has_identity(send_as_id):
        raise KeyError(f"unknown identity: {send_as_id}")
    if send_as_id not in _meta_state["identity_states"]:
        _meta_state["identity_states"][send_as_id] = new_identity_state()
    return _meta_state["identity_states"][send_as_id]


def _coerce_send_as_profile_field(field_name, value):
    if field_name in {"username", "label"}:
        return value or ""
    if field_name in {"daohao", "realm", "sect_name", "jiyin_choice", "nanlong_choice"}:
        return (value or "").strip()
    if field_name == "stargazer_star_choice":
        normalized = (value or "").strip()
        return normalized if normalized in STARGAZER_STAR_CHOICES else STARGAZER_STAR_CHOICES[0]
    if field_name == "tianti_rank_choice":
        normalized = (value or "").strip()
        return normalized if normalized in TIANTI_RANK_CHOICES else TIANTI_RANK_CHOICES[0]
    if field_name == "pet_name":
        return (value or "").strip() or DEFAULT_PET_NAME
    if field_name == "sect_updated_at":
        return float(value or 0)
    if field_name in {"xiuwei_current", "xiuwei_max", "stargazer_total_slots"}:
        return int(value or 0)
    if field_name in {
        "checkin_window_start_hour_utc",
        "checkin_window_end_hour_utc",
        "tower_window_start_hour_utc",
        "tower_window_end_hour_utc",
    }:
        return int(value)
    if field_name == "enabled":
        return bool(value)
    return value


def _normalize_send_as_profile_updates(changes):
    normalized = {}
    for field_name, raw_value in (changes or {}).items():
        if raw_value is None:
            continue
        normalized[field_name] = _coerce_send_as_profile_field(field_name, raw_value)
    return normalized


def _normalize_game_bot_ids(bot_ids):
    normalized = []
    seen = set()
    for raw_id in bot_ids or []:
        try:
            bot_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if bot_id in seen:
            continue
        seen.add(bot_id)
        normalized.append(bot_id)
    return normalized


def _normalize_forum_topics(topics):
    normalized = []
    seen_topic_ids = set()
    for item in topics or []:
        if not isinstance(item, dict):
            continue
        try:
            topic_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if topic_id <= 0 or topic_id in seen_topic_ids:
            continue
        seen_topic_ids.add(topic_id)
        normalized.append({
            "id": topic_id,
            "title": str(item.get("title") or "").strip() or f"话题 {topic_id}",
            "top_message": int(item.get("top_message") or 0),
        })
    return normalized


def set_send_as_profile(
    send_as_id,
    username="",
    label="",
    daohao=None,
    realm=None,
    pet_name=None,
    sect_name=None,
    sect_updated_at=None,
    xiuwei_current=None,
    xiuwei_max=None,
    jiyin_choice=None,
    nanlong_choice=None,
    stargazer_star_choice=None,
    tianti_rank_choice=None,
    stargazer_total_slots=None,
    checkin_window_start_hour_utc=None,
    checkin_window_end_hour_utc=None,
    tower_window_start_hour_utc=None,
    tower_window_end_hour_utc=None,
    enabled=None,
):
    return update_send_as_profile(
        send_as_id,
        username=username,
        label=label,
        daohao=daohao,
        realm=realm,
        pet_name=pet_name,
        sect_name=sect_name,
        sect_updated_at=sect_updated_at,
        xiuwei_current=xiuwei_current,
        xiuwei_max=xiuwei_max,
        jiyin_choice=jiyin_choice,
        nanlong_choice=nanlong_choice,
        stargazer_star_choice=stargazer_star_choice,
        tianti_rank_choice=tianti_rank_choice,
        stargazer_total_slots=stargazer_total_slots,
        checkin_window_start_hour_utc=checkin_window_start_hour_utc,
        checkin_window_end_hour_utc=checkin_window_end_hour_utc,
        tower_window_start_hour_utc=tower_window_start_hour_utc,
        tower_window_end_hour_utc=tower_window_end_hour_utc,
        enabled=enabled,
    )


def update_send_as_profile(send_as_id, **changes):
    send_as_id = int(send_as_id)
    ensure_identity_registered(send_as_id)
    profile = dict(SEND_AS_PROFILE_DEFAULTS)
    profile.update(_meta_state["send_as_profiles"].get(send_as_id, {}))
    profile.update(_normalize_send_as_profile_updates(changes))
    _meta_state["send_as_profiles"][send_as_id] = profile
    return profile


def get_send_as_profile(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    profile = dict(SEND_AS_PROFILE_DEFAULTS)
    profile.update(_meta_state["send_as_profiles"].get(int(send_as_id), {}))
    if not (profile.get("realm") or "").strip():
        inferred_realm = infer_realm_from_xiuwei_max(profile.get("xiuwei_max", 0))
        if inferred_realm:
            profile["realm"] = inferred_realm
    return profile


def get_identity_enabled(send_as_id=None):
    return bool(get_send_as_profile(send_as_id).get("enabled", True))


def set_identity_enabled(send_as_id, enabled):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, enabled=bool(enabled))
    return get_identity_enabled(send_as_id)


def get_send_as_label(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    profile = get_send_as_profile(send_as_id)
    return profile.get("username") or profile.get("label") or str(send_as_id)


def get_pet_name(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    return get_send_as_profile(send_as_id).get("pet_name") or DEFAULT_PET_NAME


def get_jiyin_choice(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    return (get_send_as_profile(send_as_id).get("jiyin_choice") or "").strip()


def set_jiyin_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, jiyin_choice=choice)
    return get_jiyin_choice(send_as_id)


def get_nanlong_choice(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    return (get_send_as_profile(send_as_id).get("nanlong_choice") or "reject").strip() or "reject"


def set_nanlong_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, nanlong_choice=choice or "reject")
    return get_nanlong_choice(send_as_id)


def get_stargazer_star_choice(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    choice = (get_send_as_profile(send_as_id).get("stargazer_star_choice") or "").strip()
    return choice if choice in STARGAZER_STAR_CHOICES else STARGAZER_STAR_CHOICES[0]


def set_stargazer_star_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, stargazer_star_choice=choice)
    return get_stargazer_star_choice(send_as_id)


def get_stargazer_total_slots(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    return int(get_send_as_profile(send_as_id).get("stargazer_total_slots", 0) or 0)


def set_stargazer_total_slots(send_as_id, total_slots):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, stargazer_total_slots=max(0, int(total_slots or 0)))
    return get_stargazer_total_slots(send_as_id)


def get_tianti_rank_choice(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    choice = (get_send_as_profile(send_as_id).get("tianti_rank_choice") or "").strip()
    return choice if choice in TIANTI_RANK_CHOICES else TIANTI_RANK_CHOICES[0]


def set_tianti_rank_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, tianti_rank_choice=choice)
    return get_tianti_rank_choice(send_as_id)


def get_game_group_id():
    return int(_meta_state.get("game_group_id") or 0)


def set_game_group_id(group_id):
    _meta_state["game_group_id"] = int(group_id or 0)
    return get_game_group_id()


def get_game_bot_ids():
    return _normalize_game_bot_ids(_meta_state.get("game_bot_ids") or [])


def set_game_bot_ids(bot_ids):
    _meta_state["game_bot_ids"] = sorted(_normalize_game_bot_ids(bot_ids))
    return get_game_bot_ids()


def get_game_topic_id():
    return int(_meta_state.get("game_topic_id") or 0)


def set_game_topic_id(topic_id):
    _meta_state["game_topic_id"] = int(topic_id or 0)
    return get_game_topic_id()


def get_tiandao_judgement_enabled():
    return bool(_meta_state.get("tiandao_judgement_enabled", False))


def set_tiandao_judgement_enabled(enabled):
    _meta_state["tiandao_judgement_enabled"] = bool(enabled)
    return get_tiandao_judgement_enabled()


def get_guanxing_monitor_enabled():
    return bool(_meta_state.get("guanxing_monitor_enabled", False))


def set_guanxing_monitor_enabled(enabled):
    _meta_state["guanxing_monitor_enabled"] = bool(enabled)
    return get_guanxing_monitor_enabled()


def get_guanxing_monitor_target_options():
    return list(GUANXING_TARGET_KEYWORDS)


def _normalize_guanxing_monitor_targets(targets):
    if targets is None:
        return []
    if isinstance(targets, str):
        candidates = [targets]
    else:
        try:
            candidates = list(targets)
        except TypeError:
            candidates = []
    normalized = []
    for target in candidates:
        target_text = str(target or "").strip()
        if target_text in GUANXING_TARGET_KEYWORDS and target_text not in normalized:
            normalized.append(target_text)
    return normalized


def get_guanxing_monitor_targets():
    return _normalize_guanxing_monitor_targets(_meta_state.get("guanxing_monitor_targets"))


def set_guanxing_monitor_targets(targets):
    _meta_state["guanxing_monitor_targets"] = _normalize_guanxing_monitor_targets(targets)
    return get_guanxing_monitor_targets()


def _normalize_guanxing_shift_target(value):
    target = str(value or "").strip()
    if not target:
        return ""
    if not target.startswith("@"):
        target = f"@{target}"
    return target if len(target) > 1 else ""


def get_guanxing_shift_target():
    return _normalize_guanxing_shift_target(_meta_state.get("guanxing_shift_target"))


def set_guanxing_shift_target(target):
    _meta_state["guanxing_shift_target"] = _normalize_guanxing_shift_target(target)
    return get_guanxing_shift_target()


def get_forum_topics():
    return _normalize_forum_topics(_meta_state.get("forum_topics") or [])


def set_forum_topics(topics, updated_at=None):
    normalized = _normalize_forum_topics(topics)
    normalized.sort(key=lambda item: item["id"])
    _meta_state["forum_topics"] = normalized
    if updated_at is not None:
        _meta_state["forum_topics_updated_at"] = float(updated_at or 0)
    return get_forum_topics()


def get_forum_topics_updated_at():
    return float(_meta_state.get("forum_topics_updated_at") or 0)


def is_auto_delete_sent_messages_enabled():
    return bool(_meta_state.get("auto_delete_sent_messages", True))


def set_auto_delete_sent_messages(enabled):
    _meta_state["auto_delete_sent_messages"] = bool(enabled)
    return is_auto_delete_sent_messages_enabled()


def get_global_enabled():
    return bool(_meta_state.get("global_enabled", True))


def set_global_enabled(enabled):
    _meta_state["global_enabled"] = bool(enabled)
    return get_global_enabled()


def get_guanxing_round_state():
    value = _meta_state.get("guanxing_round_state") or {}
    return value if isinstance(value, dict) else {}


def set_guanxing_round_state(round_state):
    _meta_state["guanxing_round_state"] = round_state if isinstance(round_state, dict) else {}
    return get_guanxing_round_state()


def get_quiz_learning_watchers():
    watchers = _meta_state.get("quiz_learning_watchers") or {}
    return watchers if isinstance(watchers, dict) else {}


def set_quiz_learning_watchers(watchers):
    normalized = {}
    for raw_key, item in (watchers or {}).items():
        watcher_key = str(raw_key or "").strip().lower()
        if not watcher_key or not isinstance(item, dict):
            continue
        options = item.get("options") or {}
        normalized_options = {
            option_key: str(options.get(option_key) or "").strip()
            for option_key in ("A", "B", "C", "D")
        }
        matched_answer = str(item.get("matched_answer") or "").strip().upper()
        raw_identity_id = item.get("identity_id")
        try:
            identity_id = int(raw_identity_id or 0) or None
        except (TypeError, ValueError):
            identity_id = None
        normalized[watcher_key] = {
            "target_tag": str(item.get("target_tag") or "").strip(),
            "identity_id": identity_id,
            "question": str(item.get("question") or "").strip(),
            "options": normalized_options,
            "expire_at": float(item.get("expire_at") or 0),
            "matched_answer": matched_answer if matched_answer in {"A", "B", "C", "D"} else "",
        }
    _meta_state["quiz_learning_watchers"] = normalized
    return get_quiz_learning_watchers()


# ================= 多账号管理 =================

def get_accounts():
    accounts = _meta_state.get("accounts") or {}
    return accounts if isinstance(accounts, dict) else {}


def set_accounts(accounts):
    _meta_state["accounts"] = dict(accounts or {})


def get_account(account_id):
    return get_accounts().get(str(account_id))


def set_account(account_id, info):
    accounts = get_accounts()
    accounts[str(account_id)] = info
    set_accounts(accounts)


def get_identity_account_map():
    m = _meta_state.get("identity_account_map") or {}
    return m if isinstance(m, dict) else {}


def set_identity_account_map(m):
    _meta_state["identity_account_map"] = dict(m or {})


def get_identity_account(send_as_id):
    m = get_identity_account_map()
    return int(m.get(str(send_as_id), 0) or 0)


def set_identity_account(send_as_id, account_id):
    send_as_id = int(send_as_id)
    ensure_identity_registered(send_as_id)
    m = get_identity_account_map()
    m[str(send_as_id)] = int(account_id)
    set_identity_account_map(m)


def get_module_window_profile_keys(module_name):
    module_name = (module_name or "").strip()
    if module_name == "点卯":
        return "checkin_window_start_hour_utc", "checkin_window_end_hour_utc"
    if module_name == "闯塔":
        return "tower_window_start_hour_utc", "tower_window_end_hour_utc"
    raise ValueError(f"不支持窗口设置的模块: {module_name}")


def get_module_window_hours(module_name, send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    profile = get_send_as_profile(send_as_id)
    start_key, end_key = get_module_window_profile_keys(module_name)
    start_hour = int(profile.get(start_key, SEND_AS_PROFILE_DEFAULTS[start_key]))
    end_hour = int(profile.get(end_key, SEND_AS_PROFILE_DEFAULTS[end_key]))
    return start_hour, end_hour


def _get_local_offset_hours():
    return int(TZ_LOCAL.utcoffset(None).total_seconds() // 3600)


def get_module_window_hours_local(module_name, send_as_id=None):
    start_hour_utc, end_hour_utc = get_module_window_hours(module_name, send_as_id)
    offset_hours = _get_local_offset_hours()
    return (start_hour_utc + offset_hours) % 24, (end_hour_utc + offset_hours) % 24


def convert_window_hours_local_to_utc(start_hour_local, end_hour_local):
    offset_hours = _get_local_offset_hours()
    return (int(start_hour_local) - offset_hours) % 24, (int(end_hour_local) - offset_hours) % 24


def format_window_text(module_name, send_as_id=None):
    start_hour_utc, end_hour_utc = get_module_window_hours(module_name, send_as_id)
    start_hour_local, end_hour_local = get_module_window_hours_local(module_name, send_as_id)
    return (
        f"UTC+0 {start_hour_utc:02d}:00-{end_hour_utc:02d}:00"
        f"（UTC+8 {start_hour_local:02d}:00-{end_hour_local:02d}:00）"
    )


def set_module_window_hours(module_name, send_as_id, start_hour_utc, end_hour_utc):
    send_as_id = int(send_as_id)
    start_hour_utc = int(start_hour_utc)
    end_hour_utc = int(end_hour_utc)
    if not (0 <= start_hour_utc <= 23 and 0 <= end_hour_utc <= 23):
        raise ValueError("窗口时间必须在 0-23 之间")
    if start_hour_utc >= end_hour_utc:
        raise ValueError("窗口开始时间必须小于结束时间，暂不支持跨天")
    start_key, end_key = get_module_window_profile_keys(module_name)
    update_send_as_profile(
        send_as_id,
        **{
            start_key: start_hour_utc,
            end_key: end_hour_utc,
        },
    )


def get_realm_sort_index(realm, xiuwei_max=0):
    realm = (realm or "").strip() or infer_realm_from_xiuwei_max(xiuwei_max)
    return REALM_SORT_INDEX.get(realm, len(REALM_SORT_INDEX))


def get_realm_sort_key(realm, send_as_id=0, xiuwei_max=0, xiuwei_current=0):
    index = get_realm_sort_index(realm, xiuwei_max=xiuwei_max)
    is_unknown = index >= len(REALM_SORT_ORDER)
    # 按境界从高到低排序；同境界按修为从高到低；未知排最后
    return (1 if is_unknown else 0, -index, -int(xiuwei_current or 0), int(send_as_id or 0))


def is_yuanying_realm_available(send_as_id=None):
    profile = get_send_as_profile(send_as_id)
    realm = (profile.get("realm") or "").strip() or infer_realm_from_xiuwei_max(profile.get("xiuwei_max", 0))
    if not realm:
        return True
    realm_index = REALM_SORT_INDEX.get(realm)
    if realm_index is None:
        return True
    return realm_index >= YUANYING_MIN_REALM_INDEX


def is_small_world_realm_available(send_as_id=None):
    profile = get_send_as_profile(send_as_id)
    realm = (profile.get("realm") or "").strip() or infer_realm_from_xiuwei_max(profile.get("xiuwei_max", 0))
    if not realm:
        return False
    realm_index = REALM_SORT_INDEX.get(realm)
    if realm_index is None:
        return False
    return realm_index >= SMALL_WORLD_MIN_REALM_INDEX


def get_available_module_names(send_as_id=None):
    available_module_names = [module_name for module_name in MODULE_NAMES if module_name != "观星监控"]
    sect_name = (get_send_as_profile(send_as_id).get("sect_name") or "").strip()
    if sect_name and sect_name != "落云宗":
        available_module_names = [module_name for module_name in available_module_names if module_name != "灵树"]
    if sect_name and sect_name != "星宫":
        available_module_names = [module_name for module_name in available_module_names if module_name not in {"观星台", "观星"}]
    if sect_name and sect_name != "凌霄宫":
        available_module_names = [module_name for module_name in available_module_names if module_name != "登天阶"]
    if sect_name and sect_name != "太一门":
        available_module_names = [module_name for module_name in available_module_names if module_name != "太一"]
    if not is_yuanying_realm_available(send_as_id):
        available_module_names = [module_name for module_name in available_module_names if module_name != "元婴"]
    if not is_small_world_realm_available(send_as_id):
        available_module_names = [module_name for module_name in available_module_names if module_name != "小世界"]
    return available_module_names


def is_module_available(module_name, send_as_id=None):
    return module_name in get_available_module_names(send_as_id)


def get_pet_command(send_as_id=None):
    return f".抚摸法宝 {get_pet_name(send_as_id)}"


def set_pet_name(send_as_id, pet_name):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, pet_name=pet_name)


def get_send_as_tags(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id)
    profile = get_send_as_profile(send_as_id)

    raw_candidates = [
        profile.get("username", ""),
        profile.get("label", ""),
        profile.get("daohao", ""),
        str(send_as_id),
    ]

    tags = []
    seen = set()
    for candidate in raw_candidates:
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        candidate = candidate.lstrip("@")
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        tags.append(f"@{candidate}")
    return tags


def get_identity_ui_display_name(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id)
    profile = get_send_as_profile(send_as_id)
    role_name = profile.get("label") or profile.get("username") or str(send_as_id)
    daohao = profile.get("daohao") or str(send_as_id)
    return f"{role_name}[{daohao}]"


def get_identity_display_name(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id)
    return f"{get_send_as_label(send_as_id)}[{send_as_id}]"


def resolve_identity_selector(selector):
    selector = (selector or "").strip()
    if not selector:
        return None

    normalized = selector.lstrip("@")
    if normalized.isdigit():
        identity_id = int(normalized)
        if identity_id in get_identity_ids():
            return identity_id

    for identity_id in get_identity_ids():
        profile = get_send_as_profile(identity_id)
        candidates = {
            str(identity_id),
            (profile.get("username") or "").strip(),
            (profile.get("label") or "").strip(),
            get_send_as_label(identity_id),
        }
        if normalized in {candidate.lstrip("@") for candidate in candidates if candidate}:
            return identity_id
    return None


def split_command_identity_selector(text):
    parts = (text or "").strip().rsplit(maxsplit=1)
    if len(parts) == 2:
        target_id = resolve_identity_selector(parts[1])
        if target_id is not None:
            return parts[0].strip(), target_id
    return (text or "").strip(), None


@contextmanager
def use_identity(send_as_id):
    send_as_id = int(send_as_id)
    if not has_identity(send_as_id):
        raise KeyError(f"unknown identity: {send_as_id}")
    if send_as_id not in _meta_state["identity_states"]:
        _meta_state["identity_states"][send_as_id] = new_identity_state()
    token = _current_identity_id.set(send_as_id)
    active_token = _identity_context_active.set(True)
    try:
        yield _meta_state["identity_states"][send_as_id]
    finally:
        _identity_context_active.reset(active_token)
        _current_identity_id.reset(token)


class StateProxy:
    _MISSING = object()

    def _bucket(self):
        return get_identity_state()

    def __getitem__(self, key):
        if key in META_STATE_KEYS:
            return _meta_state[key]
        return self._bucket()[key]

    def __setitem__(self, key, value):
        if key in META_STATE_KEYS:
            _meta_state[key] = value
            return
        self._bucket()[key] = value

    def get(self, key, default=None):
        if key in META_STATE_KEYS:
            return _meta_state.get(key, default)
        return self._bucket().get(key, default)

    def setdefault(self, key, default=None):
        if key in META_STATE_KEYS:
            return _meta_state.setdefault(key, default)
        return self._bucket().setdefault(key, default)

    def pop(self, key, default=_MISSING):
        if key in META_STATE_KEYS:
            if default is StateProxy._MISSING:
                return _meta_state.pop(key)
            return _meta_state.pop(key, default)
        if default is StateProxy._MISSING:
            return self._bucket().pop(key)
        return self._bucket().pop(key, default)

    def items(self):
        merged = {**self._bucket(), **_meta_state}
        return merged.items()

    def keys(self):
        merged = {**self._bucket(), **_meta_state}
        return merged.keys()

    def values(self):
        merged = {**self._bucket(), **_meta_state}
        return merged.values()


state = StateProxy()


__all__ = [
    "GLOBAL_STATE_DEFAULTS",
    "IDENTITY_BOOL_FIELDS",
    "IDENTITY_JSON_COLUMNS",
    "IDENTITY_MODULE_COLUMNS",
    "IDENTITY_RUNTIME_COLUMNS",
    "IDENTITY_STATE_TEMPLATE",
    "IDENTITY_TIMER_COLUMNS",
    "META_STATE_KEYS",
    "REALM_SORT_ORDER",
    "StateProxy",
    "ensure_identity_registered",
    "has_identity",
    "remove_identity",
    "get_active_identity_id",
    "get_current_identity_id",
    "get_game_group_id",
    "get_game_bot_ids",
    "get_game_topic_id",
    "get_forum_topics",
    "get_forum_topics_updated_at",
    "get_global_enabled",
    "get_tiandao_judgement_enabled",
    "get_guanxing_monitor_enabled",
    "get_guanxing_monitor_target_options",
    "get_guanxing_monitor_targets",
    "get_guanxing_shift_target",
    "has_active_identity_context",
    "get_quiz_learning_watchers",
    "is_auto_delete_sent_messages_enabled",
    "get_identity_display_name",
    "get_identity_enabled",
    "get_identity_ui_display_name",
    "get_identity_ids",
    "get_identity_state",
    "convert_window_hours_local_to_utc",
    "format_window_text",
    "get_module_window_hours",
    "get_module_window_hours_local",
    "get_module_window_profile_keys",
    "get_available_module_names",
    "get_jiyin_choice",
    "get_nanlong_choice",
    "get_pet_command",
    "get_pet_name",
    "get_stargazer_star_choice",
    "get_stargazer_total_slots",
    "get_tianti_rank_choice",
    "get_realm_sort_index",
    "get_realm_sort_key",
    "get_send_as_label",
    "is_module_available",
    "is_yuanying_realm_available",
    "is_small_world_realm_available",
    "get_send_as_profile",
    "get_send_as_tags",
    "new_identity_state",
    "resolve_identity_selector",
    "set_game_group_id",
    "set_game_bot_ids",
    "set_game_topic_id",
    "set_forum_topics",
    "set_global_enabled",
    "set_tiandao_judgement_enabled",
    "set_guanxing_monitor_enabled",
    "set_guanxing_monitor_targets",
    "set_guanxing_shift_target",
    "set_quiz_learning_watchers",
    "set_auto_delete_sent_messages",
    "set_identity_enabled",
    "set_jiyin_choice",
    "set_nanlong_choice",
    "set_module_window_hours",
    "set_pet_name",
    "set_stargazer_star_choice",
    "set_stargazer_total_slots",
    "set_tianti_rank_choice",
    "set_send_as_profile",
    "split_command_identity_selector",
    "update_send_as_profile",
    "state",
    "use_identity",
]
