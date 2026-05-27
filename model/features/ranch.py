import random
import re
import time

from ..config import CMD_RANCH
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, mono, send_audit_log, send_game_command
from ..state import get_identity_display_name, get_identity_enabled, get_identity_ids, get_send_as_tags, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining


RANCH_CYCLE_MIN_SEC = 4 * 3600 + 10 * 60
RANCH_CYCLE_MAX_SEC = 4 * 3600 + 30 * 60
RANCH_REPLY_TIMEOUT_SEC = 10 * 60
RANCH_RETRY_MIN_SEC = 2 * 60
RANCH_RETRY_MAX_SEC = 3 * 60
RANCH_SUCCESS_PREFIX = "【万兽奔腾】"
RANCH_NO_IDLE_PET_TEXT = "你当前没有处于【休息中】的灵兽可供放养。"
RANCH_WRONG_SECT_TEXT = "你并非万灵宗弟子，不知如何开启万兽谷的群体传送阵。"
RANCH_RETURN_SUMMARY_PREFIX = "【灵兽归来】"
RANCH_RETURN_INITIAL_TEXT = "已自行归来"
RANCH_RETURN_READY_GRACE_SEC = 30 * 60
RANCH_RETURN_WAIT_LOG_INTERVAL_SEC = 15 * 60
RANCH_RETURN_MAX_WAIT_SEC = 6 * 3600
RANCH_RETURN_STALE_REPROBE_MIN_SEC = 2 * 60
RANCH_RETURN_STALE_REPROBE_MAX_SEC = 3 * 60
RANCH_STALE_RETURN_ERROR_PREFIX = "灵兽归来广播等待超时"
RE_WHITESPACE = re.compile(r"\s+")


def _schedule_next_ranch(now):
    state["next_ranch_time"] = float(now + random.uniform(RANCH_CYCLE_MIN_SEC, RANCH_CYCLE_MAX_SEC))
    state["ranch_retry_count"] = 0
    return state["next_ranch_time"]


def _schedule_retry(now):
    state["next_ranch_time"] = float(now + random.uniform(RANCH_RETRY_MIN_SEC, RANCH_RETRY_MAX_SEC))


def _set_ranch_return_pending(now):
    state["ranch_return_pending"] = True
    state["ranch_return_seen_msg_id"] = 0
    state["ranch_return_wait_since"] = float(now or 0)
    state["ranch_return_last_notified_at"] = 0


def _clear_ranch_return_wait():
    state["ranch_return_pending"] = False
    state["ranch_return_wait_since"] = 0
    state["ranch_return_last_notified_at"] = 0


def _compact_text(text):
    return RE_WHITESPACE.sub("", str(text or "")).lower()


def _is_ranch_return_broadcast_text(text):
    raw_text = str(text or "")
    return "你放养的" in raw_text and (RANCH_RETURN_INITIAL_TEXT in raw_text or RANCH_RETURN_SUMMARY_PREFIX in raw_text)


def _is_ranch_return_ready(now):
    if not state.get("ranch_return_pending"):
        return False
    next_ranch_time = float(state.get("next_ranch_time", 0) or 0)
    return next_ranch_time <= 0 or float(now or 0) >= next_ranch_time - RANCH_RETURN_READY_GRACE_SEC


def _is_ranch_return_wait_stale(now):
    if not state.get("ranch_return_pending"):
        return False
    wait_since = float(state.get("ranch_return_wait_since", 0) or 0)
    if wait_since > 0 and float(now or 0) - wait_since >= RANCH_RETURN_MAX_WAIT_SEC:
        return True
    next_ranch_time = float(state.get("next_ranch_time", 0) or 0)
    return next_ranch_time > 0 and float(now or 0) - next_ranch_time >= 2 * 3600


def _schedule_ranch_return_reprobe(now):
    state["next_ranch_time"] = float(now + random.uniform(RANCH_RETURN_STALE_REPROBE_MIN_SEC, RANCH_RETURN_STALE_REPROBE_MAX_SEC))
    return state["next_ranch_time"]


def _is_ranch_wrong_sect_text(text):
    raw_text = str(text or "")
    return (
        RANCH_WRONG_SECT_TEXT in raw_text
        or ("并非万灵宗弟子" in raw_text and ("御兽" in raw_text or "灵兽" in raw_text or "万兽谷" in raw_text))
    )


def _match_ranch_return_identity_ids(text, now):
    compact_text = _compact_text(text)
    matched_ids = []
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id):
            if not state.get("ranch_enabled"):
                continue
            if not _is_ranch_return_ready(now):
                continue
            compact_tags = {_compact_text(tag) for tag in get_send_as_tags(identity_id) if tag}
            if any(tag and tag in compact_text for tag in compact_tags):
                matched_ids.append(identity_id)
    return matched_ids


def clear_ranch_state(*, persist=False, keep_last_error=False):
    last_error = state.get("ranch_last_error") if keep_last_error else ""
    state["next_ranch_time"] = 0
    state["ranch_reply_to_msg_id"] = 0
    state["ranch_reply_due_at"] = 0
    state["ranch_retry_count"] = 0
    state["ranch_last_msg_id"] = 0
    state["ranch_last_result"] = ""
    state["ranch_last_error"] = last_error or ""
    state["ranch_return_seen_msg_id"] = 0
    _clear_ranch_return_wait()
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
    return_pending = bool(state.get("ranch_return_pending"))
    lines = [
        "🐾 放养",
        f"- 已启用：{'是' if state.get('ranch_enabled') else '否'}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_ranch_time', 0))}（{fmt_remaining(state.get('next_ranch_time', 0))}）",
        f"- 等待归来广播：{'是' if return_pending else '否'}",
        f"- 待回复消息ID：{int(state.get('ranch_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('ranch_reply_due_at', 0))}（{fmt_remaining(state.get('ranch_reply_due_at', 0))}）",
        f"- 补发次数：{int(state.get('ranch_retry_count', 0) or 0)}/1",
        f"- 最近结果：{state.get('ranch_last_result') or '无'}",
    ]
    if return_pending:
        lines.append(f"- 等待归来起点：{fmt_abs_ts(state.get('ranch_return_wait_since', 0))}（{fmt_remaining(state.get('ranch_return_wait_since', 0))}）")
    if state.get("ranch_return_seen_msg_id"):
        lines.append(f"- 最近归来消息ID：{state.get('ranch_return_seen_msg_id')}")
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
        state["ranch_last_result"] = "放养成功，等待灵兽归来"
        _set_ranch_return_pending(now)
    elif RANCH_NO_IDLE_PET_TEXT in raw_text:
        stale_return_probe = str(state.get("ranch_last_error") or "").startswith(RANCH_STALE_RETURN_ERROR_PREFIX)
        if state.get("ranch_return_pending") or stale_return_probe:
            if not state.get("ranch_return_pending"):
                _set_ranch_return_pending(now)
            state["ranch_reply_to_msg_id"] = 0
            state["ranch_reply_due_at"] = 0
            state["ranch_last_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
            state["ranch_last_result"] = "无休息中灵兽，继续等待归来"
            state["ranch_last_error"] = ""
            save_state()
            await send_audit_log("🐾 当前无休息中灵兽，继续等待归来广播。", scope="identity")
            return True
        state["ranch_last_result"] = "无休息中灵兽"
        _clear_ranch_return_wait()
    elif _is_ranch_wrong_sect_text(raw_text):
        state["ranch_enabled"] = False
        clear_ranch_state(persist=False, keep_last_error=False)
        state["ranch_last_result"] = "非万灵宗弟子"
        state["ranch_last_error"] = raw_text[:120] or RANCH_WRONG_SECT_TEXT
        _clear_ranch_return_wait()
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


async def handle_ranch_return_broadcast(text, now, event=None):
    if not _is_ranch_return_broadcast_text(text):
        return False

    msg_id = int(getattr(event, "id", 0) or 0) if event is not None else 0
    if msg_id > 0:
        for identity_id in get_identity_ids():
            with use_identity(identity_id):
                if int(state.get("ranch_return_seen_msg_id", 0) or 0) == msg_id:
                    return True

    matched_ids = _match_ranch_return_identity_ids(text, now)
    if len(matched_ids) != 1:
        if len(matched_ids) > 1:
            names = ", ".join(mono(get_identity_display_name(identity_id)) for identity_id in matched_ids)
            await send_audit_log(f"🐾 灵兽归来命中多个身份，跳过：{names}", scope="global", limit=260)
        return False

    target_id = matched_ids[0]
    with use_identity(target_id):
        _clear_ranch_return_wait()
        state["ranch_return_seen_msg_id"] = msg_id
        state["ranch_retry_count"] = 0
        state["ranch_last_result"] = "灵兽归来已确认"
        state["ranch_last_error"] = ""
        save_state()
        await send_audit_log("🐾 灵兽归来已确认。", scope="identity")
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
    if state.get("ranch_return_pending"):
        if _is_ranch_return_wait_stale(now):
            wait_since = float(state.get("ranch_return_wait_since", 0) or 0)
            _clear_ranch_return_wait()
            state["ranch_last_result"] = "归来广播失联，准备重新探测"
            state["ranch_last_error"] = f"{RANCH_STALE_RETURN_ERROR_PREFIX}，等待起点={fmt_abs_ts(wait_since)}"
            next_time = _schedule_ranch_return_reprobe(now)
            save_state()
            await send_audit_log(
                f"⚠️ 放养归来广播等待超过 {int(RANCH_RETURN_MAX_WAIT_SEC // 3600)} 小时，{fmt_remaining(next_time)} 后重新探测 .一键放养。",
                scope="identity",
            )
            return
        last_notified_at = float(state.get("ranch_return_last_notified_at", 0) or 0)
        if last_notified_at <= 0 or now - last_notified_at >= RANCH_RETURN_WAIT_LOG_INTERVAL_SEC:
            state["ranch_return_last_notified_at"] = now
            state["ranch_last_result"] = "CD 到期，等待灵兽归来"
            save_state()
            await send_audit_log("⏳ 放养 CD 到期，等待灵兽归来广播。", scope="identity")
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
    "handle_ranch_return_broadcast",
    "run_ranch_scheduler",
    "schedule_ranch_initial_check",
]
