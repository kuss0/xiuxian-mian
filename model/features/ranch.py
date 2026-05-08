import random
import time

from ..config import CMD_RANCH
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import state
from ..timing import fmt_abs_ts, fmt_remaining


RANCH_CYCLE_MIN_SEC = int(8.5 * 3600)
RANCH_CYCLE_MAX_SEC = 9 * 3600
RANCH_REPLY_TIMEOUT_SEC = 10 * 60
RANCH_RETRY_MIN_SEC = 2 * 60
RANCH_RETRY_MAX_SEC = 3 * 60
RANCH_SUCCESS_PREFIX = "【万兽奔腾】"
RANCH_NO_IDLE_PET_TEXT = "你当前没有处于【休息中】的灵兽可供放养。"
RANCH_WRONG_SECT_TEXT = "你并非万灵宗弟子，不知如何开启万兽谷的群体传送阵。"


def _schedule_next_ranch(now):
    state["next_ranch_time"] = float(now + random.uniform(RANCH_CYCLE_MIN_SEC, RANCH_CYCLE_MAX_SEC))
    state["ranch_retry_count"] = 0
    return state["next_ranch_time"]


def _schedule_retry(now):
    state["next_ranch_time"] = float(now + random.uniform(RANCH_RETRY_MIN_SEC, RANCH_RETRY_MAX_SEC))


def clear_ranch_state(*, persist=False, keep_last_error=False):
    last_error = state.get("ranch_last_error") if keep_last_error else ""
    state["next_ranch_time"] = 0
    state["ranch_reply_to_msg_id"] = 0
    state["ranch_reply_due_at"] = 0
    state["ranch_retry_count"] = 0
    state["ranch_last_msg_id"] = 0
    state["ranch_last_result"] = ""
    state["ranch_last_error"] = last_error or ""
    if persist:
        save_state()
    else:
        mark_dirty()


def schedule_ranch_initial_check(now, *, persist=False, keep_last_error=True):
    clear_ranch_state(persist=False, keep_last_error=keep_last_error)
    state["next_ranch_time"] = float(now + random.uniform(10 * 60, 30 * 60))
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_ranch_time"]


def get_ranch_status_text():
    lines = [
        "🐾 放养",
        f"- 已启用：{'是' if state.get('ranch_enabled') else '否'}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_ranch_time', 0))}（{fmt_remaining(state.get('next_ranch_time', 0))}）",
        f"- 待回复消息ID：{int(state.get('ranch_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('ranch_reply_due_at', 0))}（{fmt_remaining(state.get('ranch_reply_due_at', 0))}）",
        f"- 补发次数：{int(state.get('ranch_retry_count', 0) or 0)}/1",
        f"- 最近结果：{state.get('ranch_last_result') or '无'}",
    ]
    if state.get("ranch_last_error"):
        lines.append(f"- 最近异常：{state.get('ranch_last_error')}")
    return "\n".join(lines)


def _is_ranch_reply(text, reply_to, matched_family=None):
    if matched_family == "ranch":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    return CMD_RANCH in orig_cmd or "万兽谷" in str(text or "") or str(text or "").startswith(RANCH_SUCCESS_PREFIX)


async def handle_ranch_reply(text, now, reply_to, matched_family=None):
    if not state.get("ranch_enabled"):
        return False
    if not _is_ranch_reply(text, reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "").strip()
    if raw_text.startswith(RANCH_SUCCESS_PREFIX):
        state["ranch_last_result"] = "放养成功"
    elif RANCH_NO_IDLE_PET_TEXT in raw_text:
        state["ranch_last_result"] = "无休息中灵兽"
    elif RANCH_WRONG_SECT_TEXT in raw_text:
        state["ranch_enabled"] = False
        clear_ranch_state(persist=False, keep_last_error=False)
        state["ranch_last_result"] = "非万灵宗弟子"
        state["ranch_last_error"] = RANCH_WRONG_SECT_TEXT
        save_state()
        await send_audit_log("⚠️ 当前身份并非万灵宗弟子，已暂停放养模块。", scope="identity")
        return True
    else:
        return False

    state["ranch_reply_to_msg_id"] = 0
    state["ranch_reply_due_at"] = 0
    state["ranch_last_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
    state["ranch_last_error"] = ""
    next_time = _schedule_next_ranch(now)
    save_state()
    await send_audit_log(f"🐾 放养：{state['ranch_last_result']}，下次 {fmt_abs_ts(next_time)}。", scope="identity")
    return True


async def run_ranch_scheduler(now):
    if not state.get("ranch_enabled"):
        return

    reply_to_msg_id = int(state.get("ranch_reply_to_msg_id", 0) or 0)
    if reply_to_msg_id > 0:
        if now < float(state.get("ranch_reply_due_at", 0) or 0):
            return
        state["ranch_reply_to_msg_id"] = 0
        state["ranch_reply_due_at"] = 0
        if int(state.get("ranch_retry_count", 0) or 0) < 1:
            state["ranch_retry_count"] = int(state.get("ranch_retry_count", 0) or 0) + 1
            _schedule_retry(now)
            state["ranch_last_error"] = f"放养回复超时，准备补发一次，原消息ID={reply_to_msg_id}"
        else:
            _schedule_next_ranch(now)
            state["ranch_last_error"] = f"放养补发后仍无回复，进入下一轮，原消息ID={reply_to_msg_id}"
        save_state()
        await send_audit_log(f"⚠️ {state['ranch_last_error']}", scope="identity")
        return

    if now < float(state.get("next_ranch_time", 0) or 0):
        return

    msg = await send_game_command(CMD_RANCH, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        if int(state.get("ranch_retry_count", 0) or 0) < 1:
            state["ranch_retry_count"] = int(state.get("ranch_retry_count", 0) or 0) + 1
            _schedule_retry(sent_at)
            state["ranch_last_error"] = "放养发送失败，准备补发一次"
        else:
            _schedule_next_ranch(sent_at)
            state["ranch_last_error"] = "放养补发发送失败，进入下一轮"
        save_state()
        await send_audit_log(f"❌ {state['ranch_last_error']}。", scope="identity")
        return

    state["ranch_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["ranch_reply_due_at"] = sent_at + RANCH_REPLY_TIMEOUT_SEC
    state["ranch_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["ranch_last_result"] = "已发送"
    state["ranch_last_error"] = ""
    save_state()
    console_log(f"🐾 一键放养已发送，等待结果（msg_id={msg.id}）", scope="identity")


__all__ = [
    "clear_ranch_state",
    "get_ranch_status_text",
    "handle_ranch_reply",
    "run_ranch_scheduler",
    "schedule_ranch_initial_check",
]
