import random
import re
import time
from datetime import datetime, timezone

from .config import (
    ADMIN_ID,
    CMD_BATTLE_POWER,
    CMD_CHECKIN,
    CMD_DEEP_RETREAT,
    CMD_DEEP_RETREAT_QUERY,
    CMD_IDENTITY_INFO,
    CMD_PET,
    CMD_QUIZ_ANSWER,
    CMD_SECT_TEACH,
    CMD_TOWER,
    CMD_TREE_GUARD,
    CMD_TREE_HARVEST,
    CMD_TREE_STATUS,
    CMD_TREE_WATER,
    CMD_YUANYING,
    CMD_YUANYING_STATUS,
    LAUNCHING_TIMEOUT_SEC,
    LOG_GROUP_ID,
    MODULE_KEY_MAP,
    MODULE_NAMES,
    RE_CMD_DISABLE_ALL,
    RE_CMD_ENABLE_ALL,
    RE_CMD_ENABLE_PATTERNS,
    RE_CMD_GLOBAL_PAUSE,
    RE_CMD_GLOBAL_RESUME,
    RE_CMD_LOGIN,
    RE_CMD_SINGLE_STATUS_PATTERNS,
    RE_CMD_STATUS,
    RE_WHITESPACE,
    SUMMARY_TIMEOUT_SEC,
    client,
    format_battle_power_command,
    format_identity_info_command,
    is_battle_power_command_text,
    is_identity_info_command_text,
)
from .features.checkin import get_checkin_status_text
from .features.deep_retreat import get_deep_retreat_status_detail_text
from .features.pet import get_pet_status_text
from .features.quiz import clear_quiz_state, get_quiz_status_text
from .features.tower import get_tower_status_text
from .features.tree import get_tree_status_text
from .features.yuanying import get_yuanying_status_detail_text
from .persistence import mark_dirty, save_state
from .runtime import (
    IDENTITY_INFO_REFRESH_ERROR_TEXT,
    build_ui_login_url,
    issue_ui_login_token,
    reply_log_group_message,
    send_audit_log,
    send_game_command,
)
from .state import (
    ensure_identity_registered,
    get_available_module_names,
    get_current_identity_id,
    get_game_group_id,
    get_identity_display_name,
    get_identity_enabled,
    get_identity_ids,
    get_identity_ui_display_name,
    get_global_enabled,
    set_global_enabled as set_global_enabled_state,
    get_module_window_hours,
    get_pet_name,
    infer_realm_from_xiuwei_max,
    is_auto_delete_sent_messages_enabled,
    get_send_as_profile,
    get_send_as_tags,
    is_module_available,
    is_yuanying_realm_available,
    set_identity_enabled as set_identity_enabled_profile,
    set_module_window_hours,
    split_command_identity_selector,
    state,
    update_send_as_profile,
    use_identity,
)
from .timing import calc_next_daily_window_after_completion, calc_next_daily_window_time, get_checkin_day_key, get_day_key, reset_checkin_daily_state, schedule_next_checkin, schedule_next_checkin_after_completion, schedule_next_tower, schedule_next_tower_after_completion

RE_IDENTITY_INFO_CARD = re.compile(r"天命玉牒")
RE_BATTLE_POWER_CARD = re.compile(r"📊\s*【天机阁[\s\S]*?战力评估】")
RE_IDENTITY_INFO_NAME = re.compile(r"道号[:：]\s*(\S+)")
RE_IDENTITY_INFO_REALM_SECT = re.compile(r"境界[:：]\s*(\S+)")
RE_IDENTITY_INFO_SECT = re.compile(r"宗门[:：]\s*【([^】]+)】")
RE_IDENTITY_INFO_XIUWEI = re.compile(r"修为[:：]\s*([\d,]+)\s*/\s*([\d,]+)")
RE_REALM_BREAKTHROUGH = re.compile(r"成功突破至【([^】]+)】")
IDENTITY_INFO_REFRESH_TIMEOUT_SEC = 180
IDENTITY_INFO_FOLLOWUP_DELAY_MIN_SEC = 20
IDENTITY_INFO_FOLLOWUP_DELAY_MAX_SEC = 25
IDENTITY_REFRESH_REQUIRED_FIELDS = ("daohao", "sect_name", "realm", "xiuwei")
_IMMEDIATE_ENABLE_RETRY_DELAY_SEC = 1


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
    if module_name == "点卯":
        state["next_checkin_time"] = retry_at
        return retry_at
    if module_name == "闯塔":
        state["next_tower_time"] = retry_at
        return retry_at
    if module_name == "元婴":
        state["next_yuanying_time"] = retry_at
        return retry_at
    if module_name == "深度闭关":
        state["next_deep_retreat_time"] = retry_at
        return retry_at
    raise ValueError(f"未知模块: {module_name}")


def get_module_unavailable_reason(module_name, send_as_id=None):
    if is_module_available(module_name, send_as_id):
        return ""
    if module_name == "灵树":
        return f"当前宗门未提供{module_name}模块"
    if module_name == "元婴" and not is_yuanying_realm_available(send_as_id):
        return f"当前境界未达到{module_name}模块开启条件"
    return f"当前条件未提供{module_name}模块"


def _clear_pending_tasks_by_commands(commands):
    commands = set(commands)
    remove_ids = [msg_id for msg_id, pending in state["pending_tasks"].items() if pending.get("cmd") in commands]
    for msg_id in remove_ids:
        state["pending_tasks"].pop(msg_id, None)


def _disable_tree_module_state():
    state["tree_enabled"] = False
    state["next_irr_time"] = 0
    state["next_guard_time"] = 0
    state["is_maturing"] = False
    state["is_invading"] = False
    state["is_harvested"] = False
    state["pending_irrigation"] = False
    state["tree_bootstrap_check_needed"] = False
    state["tree_maturing_logged"] = False
    _clear_pending_tasks_by_commands({CMD_TREE_WATER, CMD_TREE_GUARD, CMD_TREE_STATUS, CMD_TREE_HARVEST})


def _disable_pet_module_state():
    state["pet_enabled"] = False
    state["next_pet_time"] = 0
    _clear_pending_tasks_by_commands({CMD_PET})


def _disable_quiz_module_state():
    state["quiz_enabled"] = False
    clear_quiz_state(persist=False)
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


def _get_checkin_resume_time():
    next_sect_teach_time = float(state.get("next_sect_teach_time", 0) or 0)
    sect_teach_reply_to_msg_id = int(state.get("sect_teach_reply_to_msg_id", 0) or 0)
    if next_sect_teach_time > 0 and sect_teach_reply_to_msg_id > 0 and int(state.get("checkin_teach_count", 0) or 0) < 3:
        return next_sect_teach_time
    return float(state.get("next_checkin_time", 0) or 0)


def _manual_disable_checkin_module_state():
    state["checkin_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_CHECKIN, CMD_SECT_TEACH})


def _manual_enable_checkin_module_state(now):
    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
    state["checkin_enabled"] = True
    if _get_checkin_resume_time() > now:
        return
    state["next_sect_teach_time"] = 0
    state["sect_teach_reply_to_msg_id"] = 0
    state["last_checkin_msg_id"] = 0
    state["last_sect_teach_msg_id"] = 0
    state["checkin_cleanup_msg_ids"] = []
    _set_checkin_module_enabled(True, now)


def _manual_disable_tower_module_state():
    state["tower_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_TOWER})


def _manual_enable_tower_module_state(now):
    state["tower_enabled"] = True
    if float(state.get("next_tower_time", 0) or 0) > now:
        return
    _set_tower_module_enabled(True, now)


def _manual_disable_quiz_module_state():
    state["quiz_enabled"] = False
    clear_quiz_state(persist=False)
    _clear_pending_tasks_by_commands({CMD_QUIZ_ANSWER})


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


def _manual_disable_yuanying_module_state():
    state["yuanying_enabled"] = False
    _clear_pending_tasks_by_commands({CMD_YUANYING, CMD_YUANYING_STATUS})


def _manual_enable_yuanying_module_state(now):
    state["yuanying_enabled"] = True
    if float(state.get("next_yuanying_time", 0) or 0) > now:
        return
    _set_yuanying_module_enabled(True, now)


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
    state["next_sect_teach_time"] = 0
    state["sect_teach_reply_to_msg_id"] = 0
    state["last_checkin_msg_id"] = 0
    state["last_sect_teach_msg_id"] = 0
    state["checkin_cleanup_msg_ids"] = []
    _clear_pending_tasks_by_commands({CMD_CHECKIN, CMD_SECT_TEACH})


def _set_tower_module_enabled(enabled, now):
    state["tower_enabled"] = bool(enabled)
    if enabled:
        day_key = get_day_key(now)
        if state["last_tower_day"] != day_key:
            state["last_tower_day"] = ""
            state["last_tower_msg_id"] = 0
        if state["last_tower_day"] == day_key:
            schedule_next_tower_after_completion(now, persist=False)
        elif _is_within_module_window("闯塔", now):
            state["next_tower_time"] = now
        else:
            _schedule_module_next_window_after_enable("闯塔", now)
        return
    state["next_tower_time"] = 0
    state["last_tower_msg_id"] = 0
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
    CMD_TREE_HARVEST: "灵树",
    CMD_PET: "法宝",
    CMD_QUIZ_ANSWER: "玄骨考校",
    CMD_CHECKIN: "点卯",
    CMD_SECT_TEACH: "点卯",
    CMD_TOWER: "闯塔",
    CMD_YUANYING: "元婴",
    CMD_YUANYING_STATUS: "元婴",
    CMD_DEEP_RETREAT: "深度闭关",
    CMD_DEEP_RETREAT_QUERY: "深度闭关",
}
MANUAL_MODULE_TOGGLE_HANDLERS = {
    "玄骨考校": (_manual_enable_quiz_module_state, _manual_disable_quiz_module_state),
    "点卯": (_manual_enable_checkin_module_state, _manual_disable_checkin_module_state),
    "闯塔": (_manual_enable_tower_module_state, _manual_disable_tower_module_state),
    "元婴": (_manual_enable_yuanying_module_state, _manual_disable_yuanying_module_state),
    "深度闭关": (_manual_enable_deep_retreat_module_state, _manual_disable_deep_retreat_module_state),
}
MODULE_DISABLE_HANDLERS = {
    "灵树": _disable_tree_module_state,
    "法宝": _disable_pet_module_state,
    "玄骨考校": _disable_quiz_module_state,
    "元婴": _disable_yuanying_module_state,
    "深度闭关": _disable_deep_retreat_module_state,
    "点卯": lambda: _set_checkin_module_enabled(False, time.time()),
    "闯塔": lambda: _set_tower_module_enabled(False, time.time()),
}
MODULE_STATE_SETTERS = {
    "checkin_enabled": _set_checkin_module_enabled,
    "tower_enabled": _set_tower_module_enabled,
    "yuanying_enabled": _set_yuanying_module_enabled,
    "deep_retreat_enabled": _set_deep_retreat_module_enabled,
}


def _get_module_display_name(module_name, send_as_id=None):
    if module_name == "法宝":
        return f"法宝({get_pet_name(send_as_id)})"
    return module_name


def get_module_status_text(send_as_id=None):
    def status_dot(enabled):
        return "🟢" if enabled else "🔴"

    def cell(enabled, name):
        return f"{status_dot(enabled)} {name}"

    def row(cells):
        if not cells:
            return ""
        return "  ｜  ".join(cells)

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    show_identity_header = len(target_ids) > 1 or (send_as_id is not None and len(get_identity_ids()) > 1)
    blocks = []
    for identity_id in target_ids:
        available_module_names = get_available_module_names(identity_id)
        with use_identity(identity_id):
            rows = []
            current_row = []
            for module_name in available_module_names:
                current_row.append(cell(state[MODULE_KEY_MAP[module_name]], _get_module_display_name(module_name, identity_id)))
                if len(current_row) == 2:
                    rows.append(row(current_row))
                    current_row = []
            if current_row:
                rows.append(row(current_row))
            body = "📋 模块状态"
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


def get_single_module_status_text(module_name, send_as_id=None):
    status_map = {
        "灵树": get_tree_status_text,
        "法宝": get_pet_status_text,
        "玄骨考校": get_quiz_status_text,
        "元婴": get_yuanying_status_detail_text,
        "深度闭关": get_deep_retreat_status_detail_text,
        "点卯": get_checkin_status_text,
        "闯塔": get_tower_status_text,
    }
    getter = status_map.get(module_name)
    if not getter:
        return "❌ 未知模块"

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    blocks = []
    for identity_id in target_ids:
        unavailable_reason = get_module_unavailable_reason(module_name, identity_id)
        if unavailable_reason:
            body = f"❌ {unavailable_reason}"
        else:
            with use_identity(identity_id):
                body = getter()
        if len(target_ids) > 1 or (send_as_id is not None and len(get_identity_ids()) > 1):
            body = f"👤 {get_identity_display_name(identity_id)}\n{body}"
        blocks.append(body)

    if len(blocks) == 1:
        return blocks[0]
    return "\n\n".join(blocks)


def hydrate_identity_profile(send_as_entity):
    send_as_id = int(getattr(send_as_entity, "id", 0) or 0)
    if send_as_id <= 0:
        raise ValueError("无法解析 SEND_AS_ID")
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
        if not is_module_available("元婴", send_as_id) and state.get("yuanying_enabled"):
            _disable_yuanying_module_state()
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

    state["next_sect_teach_time"] = 0
    state["sect_teach_reply_to_msg_id"] = 0
    state["last_checkin_msg_id"] = 0
    state["last_sect_teach_msg_id"] = 0
    state["checkin_cleanup_msg_ids"] = []
    _set_checkin_module_enabled(True, now)



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
    _set_tower_module_enabled(True, now)



def _restore_tree_runtime(now):
    if state["is_maturing"] or state["is_invading"] or state["pending_irrigation"]:
        state["tree_bootstrap_check_needed"] = True
        return
    if state["next_irr_time"] <= 0:
        _schedule_module_immediate_retry("灵树", now)



def _restore_phaseful_runtime(module_name, now):
    if module_name == "元婴":
        phase_key = "yuanying_phase"
        next_time_key = "next_yuanying_time"
        last_command_time_key = "last_yuanying_command_time"
        enable_handler = _set_yuanying_module_enabled
    elif module_name == "深度闭关":
        phase_key = "deep_retreat_phase"
        next_time_key = "next_deep_retreat_time"
        last_command_time_key = "last_deep_retreat_command_time"
        enable_handler = _set_deep_retreat_module_enabled
    else:
        raise ValueError(f"不支持的模块恢复: {module_name}")

    phase = str(state.get(phase_key) or "idle")
    valid_phases = {"idle", "launching", "running", "waiting_summary", "post_summary_wait"}
    if phase not in valid_phases:
        enable_handler(True, now)
        return
    if phase == "launching" and float(state.get(last_command_time_key, 0) or 0) <= 0:
        enable_handler(True, now)
        return
    if phase == "idle" and float(state.get(next_time_key, 0) or 0) <= now:
        enable_handler(True, now)



def initialize_identity_runtime(send_as_id, now=None):
    send_as_id = int(send_as_id)
    if now is None:
        now = time.time()
    if not get_identity_enabled(send_as_id):
        return
    with use_identity(send_as_id):
        if state["tree_enabled"]:
            _restore_tree_runtime(now)
        if state["pet_enabled"] and state["next_pet_time"] <= 0:
            _schedule_module_immediate_retry("法宝", now)
        if state["checkin_enabled"]:
            _restore_checkin_runtime(now)
        if state["tower_enabled"]:
            _restore_tower_runtime(now)
        if state["deep_retreat_enabled"]:
            _restore_phaseful_runtime("深度闭关", now)
        if state["yuanying_enabled"]:
            _restore_phaseful_runtime("元婴", now)


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
    return PENDING_TASK_COMMAND_TO_MODULE.get(raw_command, "")


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

            for item in list(state.get("pending_tasks", {}).values()):
                sent_at = float(item.get("sent_at", 0) or 0)
                timeout = float(item.get("timeout", 0) or 0)
                if sent_at <= 0 or timeout <= 0 or now - sent_at <= timeout:
                    continue
                module_name = _get_pending_task_module_name(item.get("cmd"))
                if not module_name:
                    continue
                alert = _disable_module_for_startup_timeout(
                    identity_id,
                    module_name,
                    f"启动时检测到旧{module_name}任务等待超时，已自动关闭该模块。",
                    "pending_timeout",
                )
                if alert:
                    alerts.append(alert)
                    affected_identity_ids.add(identity_id)

            if state.get("next_sect_teach_time", 0) > 0 and state.get("sect_teach_reply_to_msg_id", 0) > 0 and now >= state["next_sect_teach_time"]:
                alert = _disable_module_for_startup_timeout(
                    identity_id,
                    "点卯",
                    "启动时检测到宗门传功续链已超时，已自动关闭点卯模块。",
                    "checkin_teach_overdue",
                )
                if alert:
                    alerts.append(alert)
                    affected_identity_ids.add(identity_id)

            if state.get("yuanying_phase") == "launching" and state.get("last_yuanying_command_time", 0) > 0 and now - state["last_yuanying_command_time"] >= LAUNCHING_TIMEOUT_SEC:
                alert = _disable_module_for_startup_timeout(
                    identity_id,
                    "元婴",
                    "启动时检测到元婴出窍等待回复超时，已自动关闭元婴模块。",
                    "yuanying_launching_timeout",
                )
                if alert:
                    alerts.append(alert)
                    affected_identity_ids.add(identity_id)
            elif state.get("yuanying_phase") == "waiting_summary":
                summary_sent_at = float(state.get("yuanying_summary_sent_at", 0) or 0)
                if summary_sent_at <= 0:
                    alert = _disable_module_for_startup_timeout(
                        identity_id,
                        "元婴",
                        "启动时检测到元婴归窍总结等待状态异常，已自动关闭元婴模块。",
                        "yuanying_summary_missing",
                    )
                    if alert:
                        alerts.append(alert)
                        affected_identity_ids.add(identity_id)
                elif now - summary_sent_at >= SUMMARY_TIMEOUT_SEC:
                    alert = _disable_module_for_startup_timeout(
                        identity_id,
                        "元婴",
                        "启动时检测到元婴归窍总结等待超时，已自动关闭元婴模块。",
                        "yuanying_summary_timeout",
                    )
                    if alert:
                        alerts.append(alert)
                        affected_identity_ids.add(identity_id)
            elif state.get("yuanying_phase") == "post_summary_wait" and state.get("next_yuanying_time", 0) > 0 and now >= state["next_yuanying_time"]:
                alert = _disable_module_for_startup_timeout(
                    identity_id,
                    "元婴",
                    "启动时检测到元婴总结后的缓冲等待已过期，已自动关闭元婴模块。",
                    "yuanying_post_summary_overdue",
                )
                if alert:
                    alerts.append(alert)
                    affected_identity_ids.add(identity_id)

            if state.get("deep_retreat_phase") == "launching" and state.get("last_deep_retreat_command_time", 0) > 0 and now - state["last_deep_retreat_command_time"] >= LAUNCHING_TIMEOUT_SEC:
                alert = _disable_module_for_startup_timeout(
                    identity_id,
                    "深度闭关",
                    "启动时检测到深度闭关等待回复超时，已自动关闭深度闭关模块。",
                    "deep_retreat_launching_timeout",
                )
                if alert:
                    alerts.append(alert)
                    affected_identity_ids.add(identity_id)
            elif state.get("deep_retreat_phase") == "waiting_summary":
                summary_sent_at = float(state.get("deep_retreat_summary_sent_at", 0) or 0)
                if summary_sent_at <= 0:
                    alert = _disable_module_for_startup_timeout(
                        identity_id,
                        "深度闭关",
                        "启动时检测到闭关总结等待状态异常，已自动关闭深度闭关模块。",
                        "deep_retreat_summary_missing",
                    )
                    if alert:
                        alerts.append(alert)
                        affected_identity_ids.add(identity_id)
                elif now - summary_sent_at >= SUMMARY_TIMEOUT_SEC:
                    alert = _disable_module_for_startup_timeout(
                        identity_id,
                        "深度闭关",
                        "启动时检测到闭关总结等待超时，已自动关闭深度闭关模块。",
                        "deep_retreat_summary_timeout",
                    )
                    if alert:
                        alerts.append(alert)
                        affected_identity_ids.add(identity_id)
            elif state.get("deep_retreat_phase") == "post_summary_wait" and state.get("next_deep_retreat_time", 0) > 0 and now >= state["next_deep_retreat_time"]:
                alert = _disable_module_for_startup_timeout(
                    identity_id,
                    "深度闭关",
                    "启动时检测到闭关总结后的缓冲等待已过期，已自动关闭深度闭关模块。",
                    "deep_retreat_post_summary_overdue",
                )
                if alert:
                    alerts.append(alert)
                    affected_identity_ids.add(identity_id)

    return {
        "closed_count": len(alerts),
        "affected_identity_ids": sorted(affected_identity_ids),
        "alerts": alerts,
    }


def _is_identity_refresh_command(command):
    return is_identity_info_command_text(command) or is_battle_power_command_text(command)


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
    state["identity_info_last_error"] = (error or "").strip()
    if clear_pending:
        remove_ids = [
            msg_id
            for msg_id, pending in state.get("pending_tasks", {}).items()
            if _is_identity_refresh_command(pending.get("cmd"))
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
        has_pending_cmd = any(_is_identity_refresh_command(pending.get("cmd")) for pending in state["pending_tasks"].values())
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

    xiuwei_match = RE_IDENTITY_INFO_XIUWEI.search(raw_text)
    if xiuwei_match:
        xiuwei_current = int(xiuwei_match.group(1).replace(",", ""))
        xiuwei_max = int(xiuwei_match.group(2).replace(",", ""))
        if xiuwei_max > 0:
            payload["xiuwei_current"] = xiuwei_current
            payload["xiuwei_max"] = xiuwei_max

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


def _merge_identity_refresh_payload(profile, payload):
    merged = {
        "daohao": (profile.get("daohao") or "").strip(),
        "realm": (profile.get("realm") or "").strip(),
        "sect_name": (profile.get("sect_name") or "").strip(),
        "xiuwei_current": int(profile.get("xiuwei_current") or 0),
        "xiuwei_max": int(profile.get("xiuwei_max") or 0),
    }
    if payload.get("daohao"):
        merged["daohao"] = (payload.get("daohao") or "").strip()
    if payload.get("realm"):
        merged["realm"] = (payload.get("realm") or "").strip()
    if payload.get("sect_name"):
        merged["sect_name"] = (payload.get("sect_name") or "").strip()
    if int(payload.get("xiuwei_max") or 0) > 0:
        merged["xiuwei_current"] = int(payload.get("xiuwei_current") or 0)
        merged["xiuwei_max"] = int(payload.get("xiuwei_max") or 0)
    if not merged["realm"] and merged["xiuwei_max"] > 0:
        merged["realm"] = infer_realm_from_xiuwei_max(merged["xiuwei_max"])
    return merged


def _get_identity_refresh_missing_fields(merged_profile):
    missing_fields = []
    for field_name in IDENTITY_REFRESH_REQUIRED_FIELDS:
        if field_name == "xiuwei":
            if int(merged_profile.get("xiuwei_current") or 0) <= 0 or int(merged_profile.get("xiuwei_max") or 0) <= 0:
                missing_fields.append(field_name)
            continue
        if not str(merged_profile.get(field_name) or "").strip():
            missing_fields.append(field_name)
    return missing_fields


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

    update_send_as_profile(target_id, realm=realm)
    enforce_identity_module_availability(target_id, persist=False)
    save_state()
    await send_audit_log(
        f"🌟 检测到突破消息[{get_identity_display_name(target_id)}]，境界已从{old_realm or '未获取'}更新为{realm}。"
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
            await client.delete_messages(get_game_group_id(), [msg_id])
        except Exception as e:
            print(f"delete_identity_info_trigger_msg failed: {e} | send_as_id={send_as_id} msg_id={msg_id}")
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

    with use_identity(send_as_id):
        state["identity_info_last_error"] = ""
        state["identity_info_last_requested_at"] = time.time()
        state["identity_info_reply_msg_ids"] = []
        state["last_identity_info_msg_id"] = 0
        state["identity_info_followup_due_at"] = 0
        mark_dirty()

    msg = await send_game_command(command, send_as_id=send_as_id)
    if not msg:
        _set_identity_info_error(send_as_id, "获取请求发送失败，请手动重新获取")
        return False, "角色信息获取发送失败，请手动重新获取"

    with use_identity(send_as_id):
        sent_msg_id = int(getattr(msg, "id", 0) or 0)
        state["last_identity_info_msg_id"] = sent_msg_id
        state["identity_info_reply_msg_ids"] = [sent_msg_id] if sent_msg_id else []
        mark_dirty()

    display_name = get_identity_display_name(send_as_id)
    actor_suffix = f"，操作者：{actor_id}" if actor_id is not None else ""
    await send_audit_log(f"🪪 已发起身份信息刷新[{display_name}]，指令：{command}，来源：{source}{actor_suffix}。")
    return True, "已开始获取角色信息，请等待"


async def run_identity_info_followup_scheduler(now):
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue

        with use_identity(identity_id):
            due_at = float(state.get("identity_info_followup_due_at", 0) or 0)
            if due_at <= 0 or due_at > now:
                continue

            profile = get_send_as_profile(identity_id)
            merged_profile = _merge_identity_refresh_payload(profile, {})
            missing_fields = _get_identity_refresh_missing_fields(merged_profile)
            if not missing_fields:
                trigger_msg_ids = _collect_identity_refresh_trigger_msg_ids()
                _clear_identity_refresh_runtime()
                save_state()
                for trigger_msg_id in trigger_msg_ids:
                    await delete_identity_info_trigger_msg(identity_id, trigger_msg_id, persist=False)
                save_state()
                continue

            if any(_is_identity_refresh_command(pending.get("cmd")) for pending in state["pending_tasks"].values()):
                continue

            command = format_battle_power_command(profile.get("username"))

        msg = await send_game_command(command, send_as_id=identity_id)
        if not msg:
            with use_identity(identity_id):
                _clear_identity_refresh_runtime(error="角色信息补全请求发送失败，请手动重新获取")
                save_state()
            continue

        with use_identity(identity_id):
            sent_msg_id = int(getattr(msg, "id", 0) or 0)
            tracked_ids = _get_identity_refresh_tracking_ids()
            if sent_msg_id > 0:
                tracked_ids.add(sent_msg_id)
            state["identity_info_last_requested_at"] = now
            state["identity_info_followup_due_at"] = 0
            state["last_identity_info_msg_id"] = sent_msg_id
            state["identity_info_reply_msg_ids"] = sorted(tracked_ids)
            state["identity_info_last_error"] = ""
            mark_dirty()

        await send_audit_log(f"🪪 已触发身份信息补全[{get_identity_display_name(identity_id)}]，指令：{command}。")


async def handle_identity_info_reply(text, now, reply_to, current_msg_id):
    reply_msg_id = int(getattr(reply_to, "id", 0) or 0)
    current_msg_id = int(current_msg_id or 0)
    if not reply_msg_id or not current_msg_id:
        return False

    send_as_id = get_current_identity_id()
    display_name = get_identity_display_name(send_as_id)
    merged_profile = None
    trigger_msg_ids = []
    with use_identity(send_as_id):
        reply_msg_ids = _get_identity_refresh_tracking_ids()
        if reply_msg_id not in reply_msg_ids:
            return False

        parsed = _parse_identity_info_partial(text)
        is_followup_reply = False
        if not parsed:
            parsed = _parse_battle_power_info(text)
            is_followup_reply = bool(parsed)
        if not parsed:
            _track_identity_refresh_message(current_msg_id)
            return False

        _track_identity_refresh_message(current_msg_id)
        profile = get_send_as_profile(send_as_id)
        merged_profile = _merge_identity_refresh_payload(profile, parsed)
        missing_fields = _get_identity_refresh_missing_fields(merged_profile)
        update_send_as_profile(
            send_as_id,
            daohao=merged_profile["daohao"],
            realm=merged_profile["realm"],
            sect_name=merged_profile["sect_name"],
            xiuwei_current=merged_profile.get("xiuwei_current", 0),
            xiuwei_max=merged_profile.get("xiuwei_max", 0),
            sect_updated_at=now,
        )
        state["identity_info_last_error"] = ""

        if missing_fields:
            if not is_followup_reply:
                _schedule_identity_refresh_followup(now)
            else:
                state["identity_info_followup_due_at"] = 0
                mark_dirty()
        else:
            trigger_msg_ids = _collect_identity_refresh_trigger_msg_ids()
            _clear_identity_refresh_runtime()
    enforce_identity_module_availability(send_as_id, persist=False)

    if trigger_msg_ids:
        for trigger_msg_id in trigger_msg_ids:
            await delete_identity_info_trigger_msg(send_as_id, trigger_msg_id, persist=False)
    save_state()
    if not merged_profile:
        return False
    if trigger_msg_ids:
        await send_audit_log(
            f"🪪 已更新身份信息[{display_name}]，道号：{merged_profile['daohao']}，境界：{merged_profile['realm']}，宗门：{merged_profile['sect_name']}。"
        )
    return True


async def register_identity(send_as_id_raw, *, source="ui", actor_id=None, account_id=None):
    send_as_id_text = str(send_as_id_raw or "").strip()
    if not send_as_id_text:
        return False, "SEND_AS_ID 不能为空", None
    try:
        candidate_id = int(send_as_id_text)
    except (TypeError, ValueError):
        return False, "SEND_AS_ID 必须是数字", None
    try:
        # 优先使用关联账号的 client，回退到任意已登录 client
        from .config import get_client, get_all_clients
        if account_id:
            tc = get_client(account_id)
        else:
            _all = get_all_clients()
            tc = next(iter(_all.values())) if _all else client
        send_as_entity = await tc.get_entity(candidate_id)
    except Exception:
        return False, "身份不存在或当前账号无权访问该 SEND_AS_ID", None

    canonical_id = int(getattr(send_as_entity, "id", 0) or 0)
    if canonical_id <= 0:
        return False, "无法解析有效的 SEND_AS_ID", None
    if canonical_id in get_identity_ids():
        display_name = get_identity_display_name(canonical_id)
        await send_audit_log(f"🪪 新增身份请求命中已存在身份[{display_name}]，来源：{source}。")
        return True, f"身份已存在：{display_name}", canonical_id

    ensure_identity_registered(canonical_id)
    hydrate_identity_profile(send_as_entity)
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
    await send_audit_log(f"🪪 已新增身份[{display_name}]，来源：{source}{actor_suffix}，默认全部模块关闭。")

    refresh_ok, refresh_message = await refresh_identity_info(canonical_id, source=source, actor_id=actor_id)
    if refresh_ok:
        return True, f"已新增身份：{display_name}，默认全部模块已关闭；已开始获取角色信息", canonical_id
    return True, f"已新增身份：{display_name}，默认全部模块已关闭；{refresh_message}", canonical_id


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
    await send_audit_log(f"🕒 已更新{module_name}执行窗口[{get_identity_display_name(target_ids[0]) if len(target_ids) == 1 else '全部身份'}]。")
    return True, f"已更新{module_name}执行窗口"


async def set_identity_enabled(send_as_id, enabled, *, source="ui", actor_id=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"

    enabled = bool(enabled)
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
    await send_audit_log(f"🎭 已{action_text}身份[{get_identity_display_name(send_as_id)}]，来源：{source}{actor_suffix}。")
    return True, f"已{action_text}身份[{get_identity_display_name(send_as_id)}]"


async def toggle_global_enabled(enabled, *, source="ui", actor_id=None):
    enabled = bool(enabled)
    if get_global_enabled() == enabled:
        return True, "全局状态未变化"
    set_global_enabled_state(enabled)
    now = time.time()
    for identity_id in get_identity_ids():
        if enabled:
            if get_identity_enabled(identity_id):
                initialize_identity_runtime(identity_id, now)
        else:
            with use_identity(identity_id):
                _clear_startup_module_alerts()
    save_state()
    action_text = "恢复运行" if enabled else "全局暂停"
    actor_suffix = f"，操作者：{actor_id}" if actor_id is not None else ""
    await send_audit_log(f"🌐 已{action_text}，来源：{source}{actor_suffix}。")
    return True, f"已{action_text}"


async def set_module_enabled(module_name, enabled, send_as_id=None):
    key = MODULE_KEY_MAP.get(module_name)
    if not key:
        return False

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    if enabled:
        unavailable_ids = [identity_id for identity_id in target_ids if get_module_unavailable_reason(module_name, identity_id)]
        if unavailable_ids:
            if len(unavailable_ids) == 1:
                identity_id = unavailable_ids[0]
                return False, f"{get_identity_display_name(identity_id)} {get_module_unavailable_reason(module_name, identity_id)}"
            return False, f"存在身份{get_module_unavailable_reason(module_name, unavailable_ids[0])}"
    now = time.time()
    module_state_setter = MODULE_STATE_SETTERS.get(key)
    manual_toggle_handler = MANUAL_MODULE_TOGGLE_HANDLERS.get(module_name)
    for identity_id in target_ids:
        with use_identity(identity_id):
            if bool(state.get(key, False)) == bool(enabled):
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
    if len(target_ids) == 1:
        await send_audit_log(f"🎛️ 已{action_text}{module_name}模块[{get_identity_display_name(target_ids[0])}]。")
    else:
        await send_audit_log(f"🎛️ 已{action_text}全部身份的{module_name}模块。")
    return True, ""


async def handle_log_group_command(event):
    if event.chat_id != LOG_GROUP_ID:
        return False

    sender_id = getattr(event, "sender_id", None)
    if ADMIN_ID and sender_id != ADMIN_ID:
        return False

    raw_text = (event.raw_text or "").strip()
    if not raw_text:
        return False

    text, explicit_identity_id = split_command_identity_selector(raw_text)

    if RE_CMD_GLOBAL_PAUSE.match(text):
        ok, message = await toggle_global_enabled(False, source="log_group", actor_id=sender_id)
        status_text = "🌐 全局状态：已暂停" if ok else f"❌ {message}"
        await reply_log_group_message(event, status_text, error_prefix="❌ 全局暂停回复失败")
        return True

    if RE_CMD_GLOBAL_RESUME.match(text):
        ok, message = await toggle_global_enabled(True, source="log_group", actor_id=sender_id)
        status_text = "🌐 全局状态：运行中" if ok else f"❌ {message}"
        await reply_log_group_message(event, status_text, error_prefix="❌ 全局恢复回复失败")
        return True

    if RE_CMD_ENABLE_ALL.match(text):
        for module_name in MODULE_NAMES:
            ok, message = await set_module_enabled(module_name, True, send_as_id=explicit_identity_id)
            if not ok:
                await reply_log_group_message(event, f"❌ {message}", error_prefix="❌ 模块状态回复失败")
                return True
        prefix = f"✅ 已开启全部模块[{get_identity_display_name(explicit_identity_id)}]" if explicit_identity_id is not None else "✅ 已开启全部模块"
        await reply_log_group_message(event, f"{prefix}\n{get_module_status_text(explicit_identity_id)}", error_prefix="❌ 模块状态回复失败")
        return True

    if RE_CMD_DISABLE_ALL.match(text):
        for module_name in MODULE_NAMES:
            ok, message = await set_module_enabled(module_name, False, send_as_id=explicit_identity_id)
            if not ok:
                await reply_log_group_message(event, f"❌ {message}", error_prefix="❌ 模块状态回复失败")
                return True
        prefix = f"✅ 已关闭全部模块[{get_identity_display_name(explicit_identity_id)}]" if explicit_identity_id is not None else "✅ 已关闭全部模块"
        await reply_log_group_message(event, f"{prefix}\n{get_module_status_text(explicit_identity_id)}", error_prefix="❌ 模块状态回复失败")
        return True

    for pattern, module_name, enabled in RE_CMD_ENABLE_PATTERNS:
        if pattern.match(text):
            ok, message = await set_module_enabled(module_name, enabled, send_as_id=explicit_identity_id)
            if not ok:
                await reply_log_group_message(event, f"❌ {message}", error_prefix="❌ 模块状态回复失败")
                return True
            action_text = "开启" if enabled else "关闭"
            status_text = get_module_status_text(explicit_identity_id)
            if explicit_identity_id is not None:
                prefix = f"✅ 已{action_text}{module_name}模块[{get_identity_display_name(explicit_identity_id)}]"
            else:
                prefix = f"✅ 已{action_text}{module_name}模块"
            await reply_log_group_message(event, f"{prefix}\n{status_text}", error_prefix="❌ 模块状态回复失败")
            return True

    if RE_CMD_LOGIN.match(text):
        login_token = issue_ui_login_token(sender_id)
        login_url = build_ui_login_url(login_token)
        await reply_log_group_message(
            event,
            "🔐 UI 登录链接\n"
            f"{login_url}\n\n"
            "- 请在浏览器中打开该链接完成登录\n"
            "- 登录链接与登录后会话均按 1 小时无请求自动失效\n"
            "- 支持多个登录链接和多个浏览器会话并存",
            error_prefix="❌ UI 登录链接发送失败",
            link_preview=False,
        )
        return True

    if RE_CMD_STATUS.match(text):
        await reply_log_group_message(
            event,
            get_module_status_text(explicit_identity_id),
            error_prefix="❌ 模块状态发送失败",
        )
        return True

    for pattern, module_name in RE_CMD_SINGLE_STATUS_PATTERNS:
        if pattern.match(text):
            await reply_log_group_message(
                event,
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
    "refresh_identity_info",
    "run_identity_info_followup_scheduler",
    "register_identity",
    "scan_startup_timeout_tasks",
    "set_module_enabled",
    "set_module_window_config",
    "toggle_global_enabled",
]
