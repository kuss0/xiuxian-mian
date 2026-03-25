import random

from ..config import CMD_CHECKIN, CMD_SECT_TEACH, RETRY_MAX_SEC, SECT_TEACH_DELAY_MAX_SEC, SECT_TEACH_DELAY_MIN_SEC, client
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, send_game_command
from ..state import format_window_text, get_game_group_id, is_auto_delete_sent_messages_enabled, state
from ..timing import (
    fmt_abs_ts,
    fmt_remaining,
    get_checkin_day_key,
    reset_checkin_daily_state,
    schedule_next_checkin,
    schedule_next_checkin_after_completion,
)


def get_checkin_status_text():
    today_key = get_checkin_day_key()
    return (
        "📝 点卯\n"
        f"- 今日点卯是否已完成：{'是' if state['last_checkin_done_day'] == today_key else '否'}\n"
        f"- 今日传功是否已完成：{'是' if state['checkin_teach_count'] >= 3 else '否'}（{state['checkin_teach_count']}/3）\n"
        f"- 下次执行：{fmt_abs_ts(state['next_checkin_time'])}（{fmt_remaining(state['next_checkin_time'])}）\n"
        f"- 执行窗口：{format_window_text('点卯')}\n"
        f"- 下次传功：{fmt_abs_ts(state['next_sect_teach_time'])}（{fmt_remaining(state['next_sect_teach_time'])}）"
    )


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
    return any(k in text for k in ["已点卯", "已经点过"])


def is_sect_teach_already_done_text(text):
    return any(k in text for k in ["已经传功", "已传功"])


def _schedule_checkin_next_day(now):
    return schedule_next_checkin_after_completion(now, persist=False)


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
        await client.delete_messages(get_game_group_id(), msg_ids)
    except Exception as e:
        print(f"cleanup_checkin_chain_messages failed: {e} | msg_ids={msg_ids}")
    for msg_id in msg_ids:
        state["my_msg_ids"].pop(msg_id, None)
    state["checkin_cleanup_msg_ids"] = []
    save_state()


async def handle_checkin_reply(text, now, reply_to):
    if not state["checkin_enabled"]:
        return

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if CMD_CHECKIN not in orig_cmd:
        return

    day_key = get_checkin_day_key(now)
    state["last_checkin_msg_id"] = reply_to.id if reply_to else 0
    remember_checkin_cleanup_msg_id(state["last_checkin_msg_id"])

    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
        state["last_checkin_msg_id"] = reply_to.id if reply_to else 0

    next_ts = state["next_checkin_time"]
    if next_ts <= now:
        next_ts = schedule_next_checkin(now, persist=False)
    mark_dirty()

    if "点卯成功" in text:
        state["last_checkin_done_day"] = day_key
        next_ts = _schedule_checkin_next_day(now)
        scheduled = schedule_sect_teach_chain(now, state["last_checkin_msg_id"])
        save_state()
        await send_audit_log(f"📝 宗门点卯已执行，下次预计：{fmt_abs_ts(next_ts)}")
        if scheduled:
            await send_audit_log(f"📘 宗门传功已排队：{fmt_abs_ts(state['next_sect_teach_time'])}")
        return

    if is_checkin_already_done_text(text):
        state["last_checkin_done_day"] = day_key
        next_ts = _schedule_checkin_next_day(now)
        scheduled = schedule_sect_teach_chain(now, state["last_checkin_msg_id"])
        save_state()
        await send_audit_log(f"📝 点卯已完成或仍在周期内，下次预计：{fmt_abs_ts(next_ts)}")
        if scheduled:
            await send_audit_log(f"📘 宗门传功已排队：{fmt_abs_ts(state['next_sect_teach_time'])}")
        return

    await send_audit_log(f"📝 收到点卯回复，下次预计：{fmt_abs_ts(next_ts)}")


async def handle_sect_teach_reply(text, now, reply_to):
    if not state["checkin_enabled"]:
        return

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if CMD_SECT_TEACH not in orig_cmd:
        return

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
        else:
            state["next_sect_teach_time"] = 0
            state["sect_teach_reply_to_msg_id"] = 0
            save_state()
            await cleanup_checkin_chain_messages()
        await send_audit_log(f"📘 宗门传功成功，今日进度：{state['checkin_teach_count']}/3")
        return

    if is_sect_teach_already_done_text(text):
        state["next_sect_teach_time"] = 0
        state["sect_teach_reply_to_msg_id"] = 0
        save_state()
        await cleanup_checkin_chain_messages()
        await send_audit_log(f"📘 宗门传功暂不可执行，今日进度：{state['checkin_teach_count']}/3")
        return


async def run_checkin_scheduler(now):
    if not state["checkin_enabled"]:
        return

    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
        mark_dirty()

    if state["last_checkin_done_day"] == day_key:
        next_checkin_time = state.get("next_checkin_time", 0)
        if next_checkin_time <= 0 or get_checkin_day_key(next_checkin_time) == day_key:
            _schedule_checkin_next_day(now)
            save_state()
            return

    if state["next_sect_teach_time"] > 0 and now >= state["next_sect_teach_time"]:
        reply_to_msg_id = state.get("sect_teach_reply_to_msg_id", 0)
        if reply_to_msg_id and state["checkin_teach_count"] < 3:
            msg = await send_game_command(CMD_SECT_TEACH, track=False, reply_to=reply_to_msg_id)
            if msg:
                state["last_sect_teach_msg_id"] = msg.id
                state["next_sect_teach_time"] = 0
                state["sect_teach_reply_to_msg_id"] = 0
                save_state()
                await send_audit_log(f"📘 执行宗门传功（今日第 {state['checkin_teach_count'] + 1}/3 次）。")
            else:
                state["next_sect_teach_time"] = now + RETRY_MAX_SEC
                save_state()
                await send_audit_log("❌ 宗门传功发送失败，已改为稍后重试。")
        else:
            state["next_sect_teach_time"] = 0
            state["sect_teach_reply_to_msg_id"] = 0
            mark_dirty()

    if state["next_checkin_time"] <= 0:
        schedule_next_checkin(now, persist=False)
        mark_dirty()
        return

    if now >= state["next_checkin_time"]:
        next_ts = schedule_next_checkin(now, persist=False)
        msg = await send_game_command(CMD_CHECKIN)
        if not msg:
            state["next_checkin_time"] = now + RETRY_MAX_SEC
            save_state()
            await send_audit_log("❌ 宗门点卯发送失败，已改为稍后重试。")
            return
        save_state()
        await send_audit_log(f"📝 执行宗门点卯，下次预计：{fmt_abs_ts(next_ts)}")


__all__ = [
    "cleanup_checkin_chain_messages",
    "get_checkin_status_text",
    "handle_checkin_reply",
    "handle_sect_teach_reply",
    "is_checkin_already_done_text",
    "is_sect_teach_already_done_text",
    "remember_checkin_cleanup_msg_id",
    "run_checkin_scheduler",
    "schedule_sect_teach_chain",
]
