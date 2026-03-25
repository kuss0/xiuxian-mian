from ..config import CMD_TOWER, RETRY_MAX_SEC
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, send_game_command
from ..state import format_window_text, state
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key, schedule_next_tower, schedule_next_tower_after_completion


def _schedule_tower_next_day(now):
    return schedule_next_tower_after_completion(now, persist=False)


def get_tower_status_text():
    today_key = get_day_key()
    return (
        "🗼 闯塔\n"
        f"- 今日是否已完成：{'是' if state['last_tower_day'] == today_key else '否'}\n"
        f"- 下次执行：{fmt_abs_ts(state['next_tower_time'])}（{fmt_remaining(state['next_tower_time'])}）\n"
        f"- 执行窗口：{format_window_text('闯塔')}\n"
        f"- 上次执行日：{state['last_tower_day'] or '未记录'}"
    )


async def handle_tower_reply(text, now, reply_to):
    if not state["tower_enabled"]:
        return

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if CMD_TOWER not in orig_cmd:
        return

    next_ts = state["next_tower_time"]
    if next_ts <= now:
        next_ts = schedule_next_tower(now)

    state["last_tower_msg_id"] = reply_to.id if reply_to else 0

    if "【琉璃问心塔】" in text:
        state["last_tower_day"] = get_day_key(now)
        next_ts = _schedule_tower_next_day(now)
        save_state()
        await send_audit_log(f"🗼 闯塔成功，下次预计：{fmt_abs_ts(next_ts)}")
        return

    if any(k in text for k in ["已经闯过", "已闯塔", "已在塔中", "你今日已挑战失败，道心受挫。"]):
        state["last_tower_day"] = get_day_key(now)
        next_ts = _schedule_tower_next_day(now)
        save_state()
        await send_audit_log(f"🗼 今日闯塔已完成，下次预计：{fmt_abs_ts(next_ts)}")
        return

    mark_dirty()
    await send_audit_log(f"🗼 收到闯塔回复，下次预计：{fmt_abs_ts(next_ts)}")


async def run_tower_scheduler(now):
    if not state["tower_enabled"]:
        return

    day_key = get_day_key(now)
    if state["last_tower_day"] == day_key:
        next_tower_time = state.get("next_tower_time", 0)
        if next_tower_time <= 0 or get_day_key(next_tower_time) == day_key:
            _schedule_tower_next_day(now)
            save_state()
            return

    if state["last_tower_day"] != day_key and state["next_tower_time"] <= 0:
        state["last_tower_day"] = ""
        state["last_tower_msg_id"] = 0
        mark_dirty()

    if state["next_tower_time"] <= 0:
        schedule_next_tower(now, persist=False)
        mark_dirty()
        return

    if now >= state["next_tower_time"]:
        msg = await send_game_command(CMD_TOWER)
        if not msg:
            state["next_tower_time"] = now + RETRY_MAX_SEC
            save_state()
            await send_audit_log("❌ 闯塔发送失败，已改为稍后重试。")
            return
        state["last_tower_msg_id"] = msg.id
        next_ts = schedule_next_tower(now)
        await send_audit_log(f"🗼 执行闯塔，下次预计：{fmt_abs_ts(next_ts)}")


__all__ = [
    "get_tower_status_text",
    "handle_tower_reply",
    "run_tower_scheduler",
]
