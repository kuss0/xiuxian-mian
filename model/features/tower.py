import time
from datetime import datetime, timezone

from ..config import CMD_TOWER, RETRY_MAX_SEC
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import format_window_text, get_module_window_hours, get_pending_command, state
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key, schedule_next_tower, schedule_next_tower_after_completion


TOWER_DONE_HINTS = ("已经闯过", "已闯塔", "已在塔中", "你今日已挑战失败，道心受挫。")


def _schedule_tower_next_day(now):
    return schedule_next_tower_after_completion(now, persist=False)


def _is_tower_window_time(ts):
    start_hour_utc, end_hour_utc = get_module_window_hours("闯塔")
    utc_time = datetime.fromtimestamp(float(ts), timezone.utc)
    day_start = utc_time.replace(hour=start_hour_utc, minute=0, second=0, microsecond=0)
    day_end = utc_time.replace(hour=end_hour_utc, minute=0, second=0, microsecond=0)
    return day_start <= utc_time < day_end


def _is_tower_reply(reply_to, matched_family=None):
    if matched_family == "tower":
        return True
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    return CMD_TOWER in orig_cmd


def _has_tower_pending():
    pending_tasks = state.get("pending_tasks", {})
    last_msg_id = int(state.get("last_tower_msg_id", 0) or 0)
    if last_msg_id > 0 and last_msg_id in pending_tasks:
        return True
    for pending in pending_tasks.values():
        if get_pending_command(pending) == CMD_TOWER:
            return True
    return False



def _mark_tower_done_today(now):
    state["last_tower_day"] = get_day_key(now)
    next_ts = _schedule_tower_next_day(now)
    save_state()
    return next_ts



def _normalize_tower_schedule(now):
    day_key = get_day_key(now)
    next_tower_time = float(state.get("next_tower_time", 0) or 0)
    if _has_tower_pending():
        return next_tower_time, True

    if state["last_tower_day"] == day_key:
        if next_tower_time <= 0 or get_day_key(next_tower_time) == day_key:
            next_tower_time = _schedule_tower_next_day(now)
            save_state()
        return next_tower_time, True

    if next_tower_time <= 0:
        state["last_tower_day"] = ""
        state["last_tower_msg_id"] = 0
        schedule_next_tower(now, persist=False)
        mark_dirty()
        return float(state.get("next_tower_time", 0) or 0), True

    if not _is_tower_window_time(next_tower_time):
        state["last_tower_day"] = ""
        state["last_tower_msg_id"] = 0
        schedule_next_tower(now, persist=False)
        mark_dirty()
        return float(state.get("next_tower_time", 0) or 0), True

    if now >= next_tower_time and not _is_tower_window_time(now):
        schedule_next_tower(now, persist=False)
        mark_dirty()
        return float(state.get("next_tower_time", 0) or 0), True

    return next_tower_time, False



def get_tower_status_text():
    today_key = get_day_key()
    lines = [
        "🗼 闯塔",
        f"- 今日是否已完成：{'是' if state['last_tower_day'] == today_key else '否'}",
        f"- 下次执行：{fmt_abs_ts(state['next_tower_time'])}（{fmt_remaining(state['next_tower_time'])}）",
        f"- 执行窗口：{format_window_text('闯塔')}",
        f"- 上次执行日：{state['last_tower_day'] or '未记录'}",
    ]
    return "\n".join(lines)


async def handle_tower_reply(text, now, reply_to, matched_family=None):
    if not state["tower_enabled"]:
        return False

    if not _is_tower_reply(reply_to, matched_family=matched_family):
        return False

    next_ts = state["next_tower_time"]
    if next_ts <= now:
        next_ts = schedule_next_tower(now)

    state["last_tower_msg_id"] = reply_to.id if reply_to else 0

    if "【琉璃问心塔】" in text:
        next_ts = _mark_tower_done_today(now)
        await send_audit_log(f"🗼 闯塔成功→{fmt_abs_ts(next_ts)}")
        return True

    if any(keyword in text for keyword in TOWER_DONE_HINTS):
        next_ts = _mark_tower_done_today(now)
        await send_audit_log(f"🗼 今日已完成→{fmt_abs_ts(next_ts)}")
        return True

    mark_dirty()
    console_log(f"🗼 收到闯塔回复→{fmt_abs_ts(next_ts)}")
    return True


async def run_tower_scheduler(now):
    if not state["tower_enabled"]:
        return

    next_tower_time, should_return = _normalize_tower_schedule(now)
    if should_return:
        return

    if now >= next_tower_time:
        msg = await send_game_command(CMD_TOWER, max_retry=0)
        if not msg:
            failed_at = time.time()
            state["next_tower_time"] = failed_at + RETRY_MAX_SEC
            save_state()
            await send_audit_log("❌ 闯塔发送失败，稍后重试。")
            return
        state["last_tower_msg_id"] = msg.id
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        next_ts = _schedule_tower_next_day(sent_at)
        save_state()
        console_log(f"🗼 执行闯塔，等待回复→{fmt_abs_ts(next_ts)}")


__all__ = [
    "get_tower_status_text",
    "handle_tower_reply",
    "run_tower_scheduler",
]
