import random
import time
from datetime import datetime, timezone

from ..config import (
    CMD_CHECKIN,
    CMD_GUANXING,
    CMD_GUANXING_SHIFT,
    CMD_NODE_DEFINE,
    CMD_NODE_SEARCH,
    CMD_RANCH,
    CMD_SECT_TEACH,
    CMD_STARGAZER_COLLECT,
    CMD_STARGAZER_GUIDE,
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_SOOTHE,
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_GANGFENG,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    CMD_TOWER,
    CMD_TREE_GUARD,
    CMD_TREE_HARVEST,
    CMD_TREE_STATUS,
    CMD_TREE_WATER,
    CMD_YINDAO,
    RETRY_MAX_SEC,
    SECT_TEACH_DELAY_MAX_SEC,
    SECT_TEACH_DELAY_MIN_SEC,
)
from ..persistence import mark_dirty, save_state
from ..runtime import _get_identity_client, console_log, send_audit_log, send_game_command
from ..state import (
    format_window_text,
    get_current_identity_id,
    get_game_group_id,
    get_identity_state,
    get_module_window_hours,
    get_pending_command,
    is_auto_delete_sent_messages_enabled,
    state,
    update_send_as_profile,
)
from ..timing import (
    fmt_abs_ts,
    fmt_remaining,
    get_checkin_day_key,
    reset_checkin_daily_state,
    schedule_next_checkin,
    schedule_next_checkin_after_completion,
)


CHECKIN_DONE_HINTS = ("已点卯", "已经点过")
NO_SECT_CHECKIN_HINTS = ("散修无需点卯", "速速寻一宗门拜入")
SECT_DEPENDENT_PENDING_COMMANDS = {
    CMD_CHECKIN,
    CMD_SECT_TEACH,
    CMD_TOWER,
    CMD_TREE_WATER,
    CMD_TREE_GUARD,
    CMD_TREE_STATUS,
    CMD_TREE_HARVEST,
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_GUIDE,
    CMD_STARGAZER_SOOTHE,
    CMD_STARGAZER_COLLECT,
    CMD_GUANXING,
    CMD_GUANXING_SHIFT,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_GANGFENG,
    CMD_RANCH,
    CMD_YINDAO,
    CMD_NODE_SEARCH,
    CMD_NODE_DEFINE,
}


def _is_checkin_reply(reply_to, matched_family=None):
    if matched_family == "checkin":
        return True
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    return CMD_CHECKIN in orig_cmd



def _schedule_checkin_next_day(now):
    return schedule_next_checkin_after_completion(now, persist=False)


def _is_checkin_window_time(ts):
    start_hour_utc, end_hour_utc = get_module_window_hours("点卯")
    utc_time = datetime.fromtimestamp(float(ts), timezone.utc)
    day_start = utc_time.replace(hour=start_hour_utc, minute=0, second=0, microsecond=0)
    day_end = utc_time.replace(hour=end_hour_utc, minute=0, second=0, microsecond=0)
    return day_start <= utc_time < day_end


def _has_checkin_pending():
    pending_tasks = state.get("pending_tasks", {})
    last_msg_id = int(state.get("last_checkin_msg_id", 0) or 0)
    if last_msg_id > 0 and last_msg_id in pending_tasks:
        return True
    for pending in pending_tasks.values():
        if get_pending_command(pending) == CMD_CHECKIN:
            return True
    return False


def is_no_sect_checkin_text(text):
    raw_text = str(text or "")
    return any(keyword in raw_text for keyword in NO_SECT_CHECKIN_HINTS)


def _clear_pending_tasks_by_commands(identity_state, commands):
    pending_tasks = identity_state.get("pending_tasks", {})
    if not isinstance(pending_tasks, dict):
        return False
    changed = False
    normalized_commands = {str(command or "").strip() for command in commands}
    for msg_id, pending in list(pending_tasks.items()):
        command = get_pending_command(pending)
        if command in normalized_commands:
            pending_tasks.pop(msg_id, None)
            identity_state.get("my_msg_ids", {}).pop(msg_id, None)
            changed = True
    return changed


def disable_sect_modules_for_current_identity(now=None):
    identity_state = get_identity_state()
    send_as_id = get_current_identity_id()
    changed = False

    def set_field(name, value):
        nonlocal changed
        if identity_state.get(name) != value:
            identity_state[name] = value
            changed = True

    for field_name in (
        "checkin_enabled",
        "tower_enabled",
        "tree_enabled",
        "ranch_enabled",
        "stargazer_enabled",
        "guanxing_enabled",
        "tianti_enabled",
        "taiyi_enabled",
        "taiyi_node_search_enabled",
    ):
        set_field(field_name, False)

    for field_name in (
        "next_checkin_time",
        "next_sect_teach_time",
        "sect_teach_reply_to_msg_id",
        "last_checkin_msg_id",
        "last_sect_teach_msg_id",
        "next_tower_time",
        "last_tower_msg_id",
        "tower_reply_due_at",
        "tower_retry_count",
        "next_irr_time",
        "next_guard_time",
        "next_ranch_time",
        "ranch_reply_to_msg_id",
        "ranch_reply_due_at",
        "ranch_last_msg_id",
        "next_stargazer_panel_time",
        "stargazer_collect_due_at",
        "stargazer_last_panel_msg_id",
        "stargazer_followup_due_at",
        "guanxing_last_query_msg_id",
        "guanxing_last_panel_msg_id",
        "guanxing_last_shift_msg_id",
        "next_tianti_status_time",
        "next_tianti_wenxin_time",
        "next_tianti_climb_time",
        "next_tianti_gangfeng_time",
        "tianti_status_reply_to_msg_id",
        "tianti_last_status_msg_id",
        "tianti_last_wenxin_msg_id",
        "tianti_last_climb_msg_id",
        "tianti_last_gangfeng_msg_id",
        "next_taiyi_cycle_time",
        "taiyi_phase_entered_at",
        "taiyi_freeze_until",
        "taiyi_yindao_msg_id",
        "taiyi_node_search_msg_id",
        "taiyi_node_define_msg_id",
    ):
        set_field(field_name, 0)

    for field_name in (
        "checkin_cleanup_msg_ids",
        "ranch_return_pending",
        "is_maturing",
        "is_invading",
        "is_harvested",
        "pending_irrigation",
        "tree_bootstrap_check_needed",
        "stargazer_wait_full_collect",
        "stargazer_collect_ready",
        "stargazer_soothe_before_collect",
    ):
        empty_value = [] if field_name == "checkin_cleanup_msg_ids" else False
        set_field(field_name, empty_value)

    for field_name, value in (
        ("ranch_last_result", ""),
        ("ranch_last_error", "散修无宗门，已停止放养"),
        ("stargazer_last_action", ""),
        ("guanxing_panel_slot_key", ""),
        ("guanxing_last_shift_slot_key", ""),
        ("guanxing_last_shift_target", ""),
        ("guanxing_last_error", "散修无宗门，已停止观星"),
        ("tianti_cooldown_text", "散修无宗门"),
        ("tianti_wenxin_status", "散修无宗门"),
        ("tianti_gangfeng_status", "散修无宗门"),
        ("tianti_last_skip_reason", "散修无宗门"),
        ("tianti_last_error", "散修无宗门，已停止登天阶"),
        ("taiyi_phase", "idle"),
        ("taiyi_pending_node_name", ""),
        ("taiyi_freeze_reason", "散修无宗门"),
        ("taiyi_last_error", "散修无宗门，已停止太一"),
    ):
        set_field(field_name, value)

    if _clear_pending_tasks_by_commands(identity_state, SECT_DEPENDENT_PENDING_COMMANDS):
        changed = True

    profile = update_send_as_profile(send_as_id, sect_name="散修", sect_updated_at=float(now or time.time()))
    if profile.get("sect_name") == "散修":
        changed = True

    if changed:
        mark_dirty()
    return changed



def _handle_checkin_day_rollover(now, reply_to=None):
    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
        state["last_checkin_msg_id"] = reply_to.id if reply_to else 0
    return day_key



def _mark_checkin_done_and_schedule_teach(now, status_text):
    day_key = get_checkin_day_key(now)
    state["last_checkin_done_day"] = day_key
    next_ts = _schedule_checkin_next_day(now)
    scheduled = schedule_sect_teach_chain(now, state["last_checkin_msg_id"])
    save_state()
    console_log(f"📝 {status_text}→{fmt_abs_ts(next_ts)}")
    if scheduled:
        console_log(f"📘 传功已排队→{fmt_abs_ts(state['next_sect_teach_time'])}")
    return True



def _normalize_checkin_schedule(now):
    day_key = get_checkin_day_key(now)
    next_checkin_time = float(state.get("next_checkin_time", 0) or 0)
    if _has_checkin_pending():
        return next_checkin_time, True

    if state["last_checkin_done_day"] == day_key:
        if next_checkin_time <= 0 or get_checkin_day_key(next_checkin_time) == day_key:
            next_checkin_time = _schedule_checkin_next_day(now)
            save_state()
        return next_checkin_time, True

    if next_checkin_time <= 0:
        schedule_next_checkin(now, persist=False)
        mark_dirty()
        return float(state.get("next_checkin_time", 0) or 0), True

    if not _is_checkin_window_time(next_checkin_time):
        schedule_next_checkin(now, persist=False)
        mark_dirty()
        return float(state.get("next_checkin_time", 0) or 0), True

    if now >= next_checkin_time and not _is_checkin_window_time(now):
        schedule_next_checkin(now, persist=False)
        mark_dirty()
        return float(state.get("next_checkin_time", 0) or 0), True

    return next_checkin_time, False



def get_checkin_status_text():
    today_key = get_checkin_day_key()
    lines = [
        "📝 点卯",
        f"- 今日点卯是否已完成：{'是' if state['last_checkin_done_day'] == today_key else '否'}",
        f"- 今日传功是否已完成：{'是' if state['checkin_teach_count'] >= 3 else '否'}（{state['checkin_teach_count']}/3）",
        f"- 下次执行：{fmt_abs_ts(state['next_checkin_time'])}（{fmt_remaining(state['next_checkin_time'])}）",
        f"- 执行窗口：{format_window_text('点卯')}",
        f"- 下次传功：{fmt_abs_ts(state['next_sect_teach_time'])}（{fmt_remaining(state['next_sect_teach_time'])}）",
    ]
    return "\n".join(lines)


def schedule_sect_teach_chain(now, reply_to_msg_id):
    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)

    if state["checkin_teach_count"] >= 3 or not reply_to_msg_id:
        state["next_sect_teach_time"] = 0
        state["sect_teach_reply_to_msg_id"] = 0
        save_state()
        return False

    state["next_sect_teach_time"] = now + random.uniform(SECT_TEACH_DELAY_MIN_SEC, SECT_TEACH_DELAY_MAX_SEC)
    state["sect_teach_reply_to_msg_id"] = reply_to_msg_id
    save_state()
    return True


def is_checkin_already_done_text(text):
    return any(keyword in text for keyword in CHECKIN_DONE_HINTS)


def is_sect_teach_already_done_text(text):
    return any(k in text for k in ["已经传功", "已传功"])


def remember_checkin_cleanup_msg_id(msg_id):
    if not msg_id:
        return
    msg_ids = state.setdefault("checkin_cleanup_msg_ids", [])
    if msg_id not in msg_ids:
        msg_ids.append(msg_id)
        mark_dirty()


async def cleanup_checkin_chain_messages():
    msg_ids = [msg_id for msg_id in state.get("checkin_cleanup_msg_ids", []) if msg_id]
    if not msg_ids:
        return
    if not is_auto_delete_sent_messages_enabled():
        state["checkin_cleanup_msg_ids"] = []
        save_state()
        return
    try:
        await _get_identity_client().delete_messages(get_game_group_id(), msg_ids)
    except Exception as e:
        print(f"cleanup_checkin_chain_messages failed: {e} | msg_ids={msg_ids}")
    for msg_id in msg_ids:
        state["my_msg_ids"].pop(msg_id, None)
    state["checkin_cleanup_msg_ids"] = []
    save_state()


async def _notify_sect_teach_completed():
    try:
        await send_audit_log("📘 今日传功完成", scope="identity")
    except Exception as e:
        print(f"notify_sect_teach_completed failed: {e}")


async def handle_checkin_reply(text, now, reply_to, matched_family=None):
    if not _is_checkin_reply(reply_to, matched_family=matched_family):
        return False

    if is_no_sect_checkin_text(text):
        state["last_checkin_msg_id"] = reply_to.id if reply_to else 0
        remember_checkin_cleanup_msg_id(state["last_checkin_msg_id"])
        disable_sect_modules_for_current_identity(now)
        save_state()
        await send_audit_log("⚠️ 当前身份无宗门，已关闭点卯、传功及宗门限定模块。", scope="identity")
        console_log("⚠️ 散修无需点卯，已停止宗门功能。")
        return True

    if not state["checkin_enabled"]:
        return False

    state["last_checkin_msg_id"] = reply_to.id if reply_to else 0
    remember_checkin_cleanup_msg_id(state["last_checkin_msg_id"])
    _handle_checkin_day_rollover(now, reply_to=reply_to)

    next_ts = state["next_checkin_time"]
    if next_ts <= now:
        next_ts = schedule_next_checkin(now, persist=False)
    mark_dirty()

    if "点卯成功" in text:
        return _mark_checkin_done_and_schedule_teach(now, "点卯成功")

    if is_checkin_already_done_text(text):
        return _mark_checkin_done_and_schedule_teach(now, "点卯已完成")

    console_log(f"📝 收到点卯回复→{fmt_abs_ts(next_ts)}")
    return True


async def handle_sect_teach_reply(text, now, reply_to, matched_family=None):
    if not state["checkin_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "sect_teach" and CMD_SECT_TEACH not in orig_cmd:
        return False

    state["last_sect_teach_msg_id"] = reply_to.id if reply_to else 0
    remember_checkin_cleanup_msg_id(state["last_sect_teach_msg_id"])
    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
        state["last_sect_teach_msg_id"] = reply_to.id if reply_to else 0
    mark_dirty()

    if "传功玉简已记录！" in text:
        state["checkin_teach_count"] = min(3, state["checkin_teach_count"] + 1)
        if state["checkin_teach_count"] < 3:
            schedule_sect_teach_chain(now, state["last_sect_teach_msg_id"])
            console_log(f"📘 传功成功 {state['checkin_teach_count']}/3")
        else:
            state["next_sect_teach_time"] = 0
            state["sect_teach_reply_to_msg_id"] = 0
            save_state()
            await cleanup_checkin_chain_messages()
            console_log("📘 传功成功 3/3")
            await _notify_sect_teach_completed()
        return True

    if is_sect_teach_already_done_text(text):
        state["next_sect_teach_time"] = 0
        state["sect_teach_reply_to_msg_id"] = 0
        save_state()
        await cleanup_checkin_chain_messages()
        console_log(f"📘 传功暂不可执行 {state['checkin_teach_count']}/3")
        return True

    return True


async def run_checkin_scheduler(now):
    if not state["checkin_enabled"]:
        return

    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
        mark_dirty()

    if state["next_sect_teach_time"] > 0 and now >= state["next_sect_teach_time"]:
        reply_to_msg_id = state.get("sect_teach_reply_to_msg_id", 0)
        if reply_to_msg_id and state["checkin_teach_count"] < 3:
            msg = await send_game_command(CMD_SECT_TEACH, track=False, reply_to=reply_to_msg_id)
            if msg:
                state["last_sect_teach_msg_id"] = msg.id
                state["next_sect_teach_time"] = 0
                state["sect_teach_reply_to_msg_id"] = 0
                save_state()
                console_log(f"📘 执行传功 {state['checkin_teach_count'] + 1}/3")
            else:
                failed_at = time.time()
                state["next_sect_teach_time"] = failed_at + RETRY_MAX_SEC
                save_state()
                await send_audit_log("❌ 传功发送失败，稍后重试。")
        else:
            state["next_sect_teach_time"] = 0
            state["sect_teach_reply_to_msg_id"] = 0
            mark_dirty()

    next_checkin_time, should_return = _normalize_checkin_schedule(now)
    if should_return:
        return

    if now >= next_checkin_time:
        msg = await send_game_command(CMD_CHECKIN, max_retry=1)
        if not msg:
            failed_at = time.time()
            state["next_checkin_time"] = failed_at + RETRY_MAX_SEC
            save_state()
            await send_audit_log("❌ 点卯发送失败，稍后重试。")
            return
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        next_ts = _schedule_checkin_next_day(sent_at)
        save_state()
        console_log(f"📝 执行点卯，等待回复→{fmt_abs_ts(next_ts)}")


__all__ = [
    "cleanup_checkin_chain_messages",
    "get_checkin_status_text",
    "handle_checkin_reply",
    "handle_sect_teach_reply",
    "is_checkin_already_done_text",
    "is_no_sect_checkin_text",
    "is_sect_teach_already_done_text",
    "remember_checkin_cleanup_msg_id",
    "run_checkin_scheduler",
    "schedule_sect_teach_chain",
    "disable_sect_modules_for_current_identity",
]
