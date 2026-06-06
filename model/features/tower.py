import random
import time
from datetime import datetime, timezone

from ..config import CMD_TOWER, RETRY_MAX_SEC
from ..persistence import mark_dirty, save_state
from ..runtime import clear_pending_tasks_by_commands, console_log, send_audit_log, send_game_command
from ..state import format_window_text, get_module_window_hours, get_pending_command, state
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key, schedule_next_tower, schedule_next_tower_after_completion


TOWER_DONE_HINTS = ("已经闯过", "已闯塔", "已在塔中", "你今日已挑战失败，道心受挫。")
TOWER_REPLY_TIMEOUT_SEC = 120
TOWER_REPLAY_DELAY_MIN_SEC = 2
TOWER_REPLAY_DELAY_MAX_SEC = 5
TOWER_RETRY_LIMIT = 1
TOWER_DUPLICATE_SEND_GUARD_SEC = TOWER_REPLY_TIMEOUT_SEC


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


def _clear_tower_waiting():
    state["last_tower_msg_id"] = 0
    state["tower_reply_due_at"] = 0


def _mark_tower_command_attempt(now):
    attempted_at = float(now or time.time())
    state["last_tower_command_sent_at"] = attempted_at
    state["tower_reply_due_at"] = attempted_at + TOWER_REPLY_TIMEOUT_SEC
    state["next_tower_time"] = state["tower_reply_due_at"]


def _mark_tower_sent_waiting(msg_id, sent_at=None):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return
    sent_at = float(sent_at if sent_at is not None else time.time())
    due_at = sent_at + TOWER_REPLY_TIMEOUT_SEC
    state["last_tower_command_sent_at"] = sent_at
    state["last_tower_msg_id"] = msg_id
    state["tower_reply_due_at"] = due_at
    state["next_tower_time"] = due_at


def _mark_tower_done_today(now):
    day_key = get_day_key(now)
    next_tower_time = float(state.get("next_tower_time", 0) or 0)
    already_done = state.get("last_tower_day") == day_key
    state["last_tower_day"] = day_key
    _clear_tower_waiting()
    state["tower_retry_count"] = 0
    if already_done and next_tower_time > now and get_day_key(next_tower_time) != day_key:
        next_ts = next_tower_time
    else:
        next_ts = _schedule_tower_next_day(now)
    save_state()
    return next_ts



def _normalize_tower_schedule(now):
    day_key = get_day_key(now)
    next_tower_time = float(state.get("next_tower_time", 0) or 0)
    if _has_tower_pending():
        return next_tower_time, True

    last_msg_id = int(state.get("last_tower_msg_id", 0) or 0)
    reply_due_at = float(state.get("tower_reply_due_at", 0) or 0)
    retry_count = int(state.get("tower_retry_count", 0) or 0)
    if last_msg_id > 0 or reply_due_at > 0:
        if str(state.get("last_tower_day") or "") == day_key:
            _clear_tower_waiting()
            state["tower_retry_count"] = 0
            if next_tower_time <= 0 or get_day_key(next_tower_time) == day_key:
                next_tower_time = _schedule_tower_next_day(now)
            mark_dirty()
            return next_tower_time, True
        if reply_due_at > 0 and now < reply_due_at:
            return reply_due_at, True
        _clear_tower_waiting()
        if retry_count < TOWER_RETRY_LIMIT:
            state["tower_retry_count"] = retry_count + 1
            state["next_tower_time"] = now + random.uniform(TOWER_REPLAY_DELAY_MIN_SEC, TOWER_REPLAY_DELAY_MAX_SEC)
            mark_dirty()
            return float(state.get("next_tower_time", 0) or 0), True
        state["tower_retry_count"] = 0
        _schedule_tower_next_day(now)
        mark_dirty()
        return float(state.get("next_tower_time", 0) or 0), True

    last_sent_at = float(state.get("last_tower_command_sent_at", 0) or 0)
    if last_sent_at > 0 and get_day_key(last_sent_at) == day_key and state.get("last_tower_day") != day_key:
        duplicate_guard_until = last_sent_at + TOWER_DUPLICATE_SEND_GUARD_SEC
        if now < duplicate_guard_until:
            state["tower_reply_due_at"] = duplicate_guard_until
            state["next_tower_time"] = duplicate_guard_until
            mark_dirty()
            return duplicate_guard_until, True
        if retry_count < TOWER_RETRY_LIMIT and next_tower_time <= now:
            state["tower_retry_count"] = retry_count + 1
            state["next_tower_time"] = now + random.uniform(TOWER_REPLAY_DELAY_MIN_SEC, TOWER_REPLAY_DELAY_MAX_SEC)
            mark_dirty()
            return float(state.get("next_tower_time", 0) or 0), True

    if state["last_tower_day"] == day_key:
        if next_tower_time <= 0 or get_day_key(next_tower_time) == day_key:
            next_tower_time = _schedule_tower_next_day(now)
            save_state()
        return next_tower_time, True

    if next_tower_time <= 0:
        state["last_tower_day"] = ""
        _clear_tower_waiting()
        state["tower_retry_count"] = 0
        schedule_next_tower(now, persist=False)
        mark_dirty()
        return float(state.get("next_tower_time", 0) or 0), True

    if retry_count > 0:
        if get_day_key(next_tower_time) == day_key and now <= next_tower_time + TOWER_REPLY_TIMEOUT_SEC:
            return next_tower_time, now < next_tower_time
        state["tower_retry_count"] = 0
        _clear_tower_waiting()
        _schedule_tower_next_day(now)
        mark_dirty()
        return float(state.get("next_tower_time", 0) or 0), True

    if not _is_tower_window_time(next_tower_time):
        state["last_tower_day"] = ""
        _clear_tower_waiting()
        state["tower_retry_count"] = 0
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
        f"- 上次发送：{fmt_abs_ts(state.get('last_tower_command_sent_at', 0))}（{fmt_remaining(state.get('last_tower_command_sent_at', 0))}）",
        f"- 回复等待：{fmt_abs_ts(state.get('tower_reply_due_at', 0))}（{fmt_remaining(state.get('tower_reply_due_at', 0))}）",
        f"- 补发次数：{int(state.get('tower_retry_count', 0) or 0)}/{TOWER_RETRY_LIMIT}",
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

    if reply_to:
        state["last_tower_msg_id"] = int(reply_to.id or 0)

    is_success_text = "【琉璃问心塔】" in text or "【试炼古塔" in text
    is_done_text = any(keyword in text for keyword in TOWER_DONE_HINTS)
    if is_success_text or is_done_text:
        already_done = state.get("last_tower_day") == get_day_key(now)
        clear_pending_tasks_by_commands({CMD_TOWER})
        next_ts = _mark_tower_done_today(now)
        if not already_done:
            label = "闯塔成功" if is_success_text else "今日已完成"
            await send_audit_log(f"🗼 {label}→{fmt_abs_ts(next_ts)}")
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
        send_priority = "retry" if int(state.get("tower_retry_count", 0) or 0) > 0 else None
        _mark_tower_command_attempt(now)
        save_state()
        msg = await send_game_command(CMD_TOWER, track=False, max_retry=0, priority=send_priority, source_module="闯塔")
        if not msg:
            failed_at = time.time()
            state["last_tower_command_sent_at"] = 0
            state["tower_reply_due_at"] = 0
            state["next_tower_time"] = failed_at + RETRY_MAX_SEC
            save_state()
            await send_audit_log("❌ 闯塔发送失败，稍后重试。")
            return
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        _mark_tower_sent_waiting(msg.id, sent_at=sent_at)
        save_state()
        console_log(f"🗼 执行闯塔，等待回复→{fmt_abs_ts(state['next_tower_time'])}")


__all__ = [
    "get_tower_status_text",
    "handle_tower_reply",
    "run_tower_scheduler",
]
