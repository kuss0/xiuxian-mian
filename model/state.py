import copy
import contextvars
from contextlib import contextmanager

from .config import (
    CHECKIN_WINDOW_END_HOUR_UTC,
    CHECKIN_WINDOW_START_HOUR_UTC,
    DEFAULT_PET_NAME,
    GAME_BOT_IDS,
    GAME_GROUP_ID,
    MODULE_NAMES,
    SEND_AS_DEFAULT_ID,
    SEND_AS_ID,
    SEND_AS_IDS,
    TOWER_WINDOW_END_HOUR_UTC,
    TOWER_WINDOW_START_HOUR_UTC,
    TZ_LOCAL,
)

_current_identity_id = contextvars.ContextVar("current_identity_id", default=SEND_AS_DEFAULT_ID)

IDENTITY_MODULE_COLUMNS = [
    "tree_enabled", "pet_enabled", "quiz_enabled", "yuanying_enabled", "deep_retreat_enabled", "checkin_enabled", "tower_enabled",
    "is_maturing", "is_invading", "is_harvested", "pending_irrigation", "tree_bootstrap_check_needed",
    "checkin_teach_count", "checkin_teach_day", "last_checkin_done_day", "last_tower_day",
]
IDENTITY_TIMER_COLUMNS = [
    "next_irr_time", "next_guard_time", "next_pet_time", "next_checkin_time", "next_sect_teach_time",
    "next_tower_time", "next_quiz_time", "next_yuanying_time", "next_deep_retreat_time",
]
IDENTITY_RUNTIME_COLUMNS = [
    "sect_teach_reply_to_msg_id", "last_checkin_msg_id", "last_sect_teach_msg_id", "checkin_cleanup_msg_ids",
    "last_tower_msg_id",
    "quiz_reply_to_msg_id", "quiz_question", "quiz_options", "quiz_answer", "quiz_last_error", "quiz_last_matched_at",
    "yuanying_phase", "yuanying_probe_pending", "yuanying_summary_sent_at", "last_yuanying_summary_msg_id", "last_yuanying_command_time",
    "deep_retreat_phase", "deep_retreat_probe_pending", "deep_retreat_summary_sent_at", "last_deep_retreat_summary_msg_id", "last_deep_retreat_command_time",
    "identity_info_reply_msg_ids", "last_identity_info_msg_id", "identity_info_last_error", "identity_info_last_requested_at",
]
IDENTITY_JSON_COLUMNS = {"checkin_cleanup_msg_ids", "identity_info_reply_msg_ids", "quiz_options"}
IDENTITY_BOOL_FIELDS = {
    "tree_enabled", "pet_enabled", "quiz_enabled", "yuanying_enabled", "deep_retreat_enabled", "checkin_enabled", "tower_enabled",
    "is_maturing", "is_invading", "is_harvested", "pending_irrigation", "tree_bootstrap_check_needed",
    "yuanying_probe_pending", "deep_retreat_probe_pending",
}
LEGACY_PERSIST_KEYS = IDENTITY_MODULE_COLUMNS + IDENTITY_TIMER_COLUMNS + IDENTITY_RUNTIME_COLUMNS
PERSIST_KEYS = LEGACY_PERSIST_KEYS  # 兼容旧持久化函数，后续切换为 SQLite 实现
META_STATE_KEYS = {"my_user_id", "game_group_id", "game_bot_ids", "game_topic_id", "forum_topics", "forum_topics_updated_at", "auto_delete_sent_messages", "global_enabled", "send_as_profiles", "identity_states", "identity_ids"}
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


def infer_realm_from_xiuwei_max(xiuwei_max):
    try:
        xiuwei_max = int(xiuwei_max or 0)
    except (TypeError, ValueError):
        return ""
    return REALM_XIUWEI_MAX_MAP.get(xiuwei_max, "")

IDENTITY_STATE_TEMPLATE = {
    # 业务对象开关（启动默认全开）
    "tree_enabled": True,
    "pet_enabled": True,
    "quiz_enabled": True,
    "yuanying_enabled": True,
    "deep_retreat_enabled": True,
    "checkin_enabled": True,
    "tower_enabled": True,

    # 灵树模块
    "next_irr_time": 0,
    "next_guard_time": 0,
    "is_maturing": False,
    "is_invading": False,
    "is_harvested": False,
    "pending_irrigation": False,
    "tree_bootstrap_check_needed": False,

    # 灵树日志去重（运行态，不持久化）
    "tree_maturing_logged": False,

    # 法宝模块
    "next_pet_time": 0,

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

    # 玄骨考校模块
    "next_quiz_time": 0,
    "quiz_reply_to_msg_id": 0,
    "quiz_question": "",
    "quiz_options": {},
    "quiz_answer": "",
    "quiz_last_error": "",
    "quiz_last_matched_at": 0,

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

    # 运行态
    "identity_info_reply_msg_ids": [],
    "last_identity_info_msg_id": 0,
    "identity_info_last_error": "",
    "identity_info_last_requested_at": 0,
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
    "game_topic_id": 0,
    "forum_topics": [],
    "forum_topics_updated_at": 0,
    "auto_delete_sent_messages": True,
    "global_enabled": True,
    "send_as_profiles": {},
    "identity_states": {},
    "identity_ids": list(SEND_AS_IDS),
}
_meta_state = copy.deepcopy(GLOBAL_STATE_DEFAULTS)


def new_identity_state():
    return copy.deepcopy(IDENTITY_STATE_TEMPLATE)


def ensure_identity_registered(send_as_id):
    send_as_id = int(send_as_id)
    if send_as_id not in _meta_state["identity_ids"]:
        _meta_state["identity_ids"].append(send_as_id)
    if send_as_id not in _meta_state["identity_states"]:
        _meta_state["identity_states"][send_as_id] = new_identity_state()
    return _meta_state["identity_states"][send_as_id]


def get_identity_ids():
    if not _meta_state["identity_ids"]:
        _meta_state["identity_ids"] = list(SEND_AS_IDS)
    for send_as_id in list(_meta_state["identity_ids"]):
        ensure_identity_registered(send_as_id)
    return list(_meta_state["identity_ids"])


def get_current_identity_id():
    send_as_id = _current_identity_id.get()
    return int(send_as_id or SEND_AS_DEFAULT_ID)


def get_identity_state(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    return ensure_identity_registered(send_as_id)


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
        checkin_window_start_hour_utc=checkin_window_start_hour_utc,
        checkin_window_end_hour_utc=checkin_window_end_hour_utc,
        tower_window_start_hour_utc=tower_window_start_hour_utc,
        tower_window_end_hour_utc=tower_window_end_hour_utc,
        enabled=enabled,
    )


def update_send_as_profile(send_as_id, **changes):
    send_as_id = int(send_as_id)
    profile = dict(SEND_AS_PROFILE_DEFAULTS)
    profile.update(_meta_state["send_as_profiles"].get(send_as_id, {}))

    if "username" in changes:
        profile["username"] = changes.get("username") or ""
    if "label" in changes:
        profile["label"] = changes.get("label") or ""
    if "daohao" in changes:
        profile["daohao"] = (changes.get("daohao") or "").strip()
    if "realm" in changes:
        profile["realm"] = (changes.get("realm") or "").strip()
    if "pet_name" in changes:
        profile["pet_name"] = (changes.get("pet_name") or "").strip() or DEFAULT_PET_NAME
    if "sect_name" in changes:
        profile["sect_name"] = (changes.get("sect_name") or "").strip()
    if "sect_updated_at" in changes:
        profile["sect_updated_at"] = float(changes.get("sect_updated_at") or 0)
    if "xiuwei_current" in changes:
        profile["xiuwei_current"] = int(changes.get("xiuwei_current") or 0)
    if "xiuwei_max" in changes:
        profile["xiuwei_max"] = int(changes.get("xiuwei_max") or 0)
    if "checkin_window_start_hour_utc" in changes:
        profile["checkin_window_start_hour_utc"] = int(changes.get("checkin_window_start_hour_utc"))
    if "checkin_window_end_hour_utc" in changes:
        profile["checkin_window_end_hour_utc"] = int(changes.get("checkin_window_end_hour_utc"))
    if "tower_window_start_hour_utc" in changes:
        profile["tower_window_start_hour_utc"] = int(changes.get("tower_window_start_hour_utc"))
    if "tower_window_end_hour_utc" in changes:
        profile["tower_window_end_hour_utc"] = int(changes.get("tower_window_end_hour_utc"))
    if "enabled" in changes and changes.get("enabled") is not None:
        profile["enabled"] = bool(changes.get("enabled"))

    _meta_state["send_as_profiles"][send_as_id] = profile
    ensure_identity_registered(send_as_id)
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


def get_game_group_id():
    return int(_meta_state.get("game_group_id") or 0)


def set_game_group_id(group_id):
    _meta_state["game_group_id"] = int(group_id or 0)
    return get_game_group_id()


def get_game_bot_ids():
    raw_ids = _meta_state.get("game_bot_ids") or []
    normalized = []
    seen = set()
    for raw_id in raw_ids:
        try:
            bot_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if bot_id in seen:
            continue
        seen.add(bot_id)
        normalized.append(bot_id)
    return normalized


def set_game_bot_ids(bot_ids):
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
    _meta_state["game_bot_ids"] = sorted(normalized)
    return get_game_bot_ids()


def get_game_topic_id():
    return int(_meta_state.get("game_topic_id") or 0)


def set_game_topic_id(topic_id):
    _meta_state["game_topic_id"] = int(topic_id or 0)
    return get_game_topic_id()


def get_forum_topics():
    topics = []
    for item in _meta_state.get("forum_topics") or []:
        if not isinstance(item, dict):
            continue
        try:
            topic_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if topic_id <= 0:
            continue
        topics.append({
            "id": topic_id,
            "title": str(item.get("title") or "").strip() or f"话题 {topic_id}",
            "top_message": int(item.get("top_message") or 0),
        })
    return topics


def set_forum_topics(topics, updated_at=None):
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


def get_realm_sort_key(realm, send_as_id=0, xiuwei_max=0):
    return (get_realm_sort_index(realm, xiuwei_max=xiuwei_max), int(send_as_id or 0))


def is_yuanying_realm_available(send_as_id=None):
    profile = get_send_as_profile(send_as_id)
    realm = (profile.get("realm") or "").strip() or infer_realm_from_xiuwei_max(profile.get("xiuwei_max", 0))
    if not realm:
        return True
    realm_index = REALM_SORT_INDEX.get(realm)
    if realm_index is None:
        return True
    return realm_index >= YUANYING_MIN_REALM_INDEX


def get_available_module_names(send_as_id=None):
    available_module_names = list(MODULE_NAMES)
    sect_name = (get_send_as_profile(send_as_id).get("sect_name") or "").strip()
    if sect_name and sect_name != "落云宗":
        available_module_names = [module_name for module_name in available_module_names if module_name != "灵树"]
    if not is_yuanying_realm_available(send_as_id):
        available_module_names = [module_name for module_name in available_module_names if module_name != "元婴"]
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
    if send_as_id == SEND_AS_DEFAULT_ID:
        raw_candidates.append("wxjerry")

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
    ensure_identity_registered(send_as_id)
    token = _current_identity_id.set(send_as_id)
    try:
        yield _meta_state["identity_states"][send_as_id]
    finally:
        _current_identity_id.reset(token)


class StateProxy:
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

    def pop(self, key, default=None):
        if key in META_STATE_KEYS:
            return _meta_state.pop(key, default)
        if default is None:
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
for _send_as_id in SEND_AS_IDS:
    ensure_identity_registered(_send_as_id)


__all__ = [
    "GLOBAL_STATE_DEFAULTS",
    "IDENTITY_BOOL_FIELDS",
    "IDENTITY_JSON_COLUMNS",
    "IDENTITY_MODULE_COLUMNS",
    "IDENTITY_RUNTIME_COLUMNS",
    "IDENTITY_STATE_TEMPLATE",
    "IDENTITY_TIMER_COLUMNS",
    "LEGACY_PERSIST_KEYS",
    "META_STATE_KEYS",
    "PERSIST_KEYS",
    "REALM_SORT_ORDER",
    "SEND_AS_DEFAULT_ID",
    "SEND_AS_ID",
    "SEND_AS_IDS",
    "StateProxy",
    "ensure_identity_registered",
    "get_current_identity_id",
    "get_game_group_id",
    "get_game_bot_ids",
    "get_game_topic_id",
    "get_forum_topics",
    "get_forum_topics_updated_at",
    "get_global_enabled",
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
    "get_pet_command",
    "get_pet_name",
    "get_realm_sort_index",
    "get_realm_sort_key",
    "get_send_as_label",
    "is_module_available",
    "is_yuanying_realm_available",
    "get_send_as_profile",
    "get_send_as_tags",
    "new_identity_state",
    "resolve_identity_selector",
    "set_game_group_id",
    "set_game_bot_ids",
    "set_game_topic_id",
    "set_forum_topics",
    "set_global_enabled",
    "set_auto_delete_sent_messages",
    "set_identity_enabled",
    "set_module_window_hours",
    "set_pet_name",
    "set_send_as_profile",
    "split_command_identity_selector",
    "update_send_as_profile",
    "state",
    "use_identity",
]
