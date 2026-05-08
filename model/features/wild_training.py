import random
import re
import time

from ..config import CD_BUFFER_SEC, CMD_WILD_TRAINING, WILD_TRAINING_STRATEGIES
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_wild_training_strategy, set_wild_training_strategy, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time


WILD_TRAINING_CYCLE_MIN_SEC = 2 * 3600
WILD_TRAINING_CYCLE_MAX_SEC = 3 * 3600
WILD_TRAINING_REPLY_TIMEOUT_SEC = 10 * 60
WILD_TRAINING_RETRY_MIN_SEC = 2 * 60
WILD_TRAINING_RETRY_MAX_SEC = 3 * 60
WILD_TRAINING_TITLE = "【野外历练"
WILD_TRAINING_RESULT_MARKERS = ("【野外历练", "修为", "获得", "负伤", "妖兽", "灵机")
WILD_TRAINING_CD_KEYWORDS = ("山中灵机未复", "冷却", "请在", "等待")
RE_WILD_TRAINING_XIUWEI = re.compile(r"修为(?:折损)?\s*([+-]\s*[\d,]+)")
RE_WILD_TRAINING_REWARD = re.compile(r"获得\s+【([^】]+)】x(\d+)")


def normalize_wild_training_strategy(strategy):
    normalized = str(strategy or "").strip()
    return normalized if normalized in WILD_TRAINING_STRATEGIES else "深入"


def get_wild_training_command(strategy=None):
    strategy = normalize_wild_training_strategy(strategy or get_wild_training_strategy())
    return f"{CMD_WILD_TRAINING} {strategy}"


def _schedule_next(now):
    state["next_wild_training_time"] = float(now + random.uniform(WILD_TRAINING_CYCLE_MIN_SEC, WILD_TRAINING_CYCLE_MAX_SEC))
    state["wild_training_retry_count"] = 0
    return state["next_wild_training_time"]


def _schedule_retry(now):
    state["next_wild_training_time"] = float(now + random.uniform(WILD_TRAINING_RETRY_MIN_SEC, WILD_TRAINING_RETRY_MAX_SEC))


def clear_wild_training_state(*, persist=False, keep_last_error=False):
    last_error = state.get("wild_training_last_error") if keep_last_error else ""
    strategy = normalize_wild_training_strategy(state.get("wild_training_strategy"))
    state["next_wild_training_time"] = 0
    state["wild_training_strategy"] = strategy
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_msg_id"] = 0
    state["wild_training_last_result"] = ""
    state["wild_training_last_error"] = last_error or ""
    if persist:
        save_state()
    else:
        mark_dirty()


def schedule_wild_training_initial_check(now, *, persist=False, keep_last_error=True):
    clear_wild_training_state(persist=False, keep_last_error=keep_last_error)
    state["next_wild_training_time"] = float(now + random.uniform(10 * 60, 30 * 60))
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_wild_training_time"]


def get_wild_training_status_text():
    strategy = normalize_wild_training_strategy(get_wild_training_strategy())
    lines = [
        "🏞️ 野外历练",
        f"- 已启用：{'是' if state.get('wild_training_enabled') else '否'}",
        f"- 当前策略：{strategy}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_wild_training_time', 0))}（{fmt_remaining(state.get('next_wild_training_time', 0))}）",
        f"- 待回复消息ID：{int(state.get('wild_training_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('wild_training_reply_due_at', 0))}（{fmt_remaining(state.get('wild_training_reply_due_at', 0))}）",
        f"- 补发次数：{int(state.get('wild_training_retry_count', 0) or 0)}/1",
        f"- 最近结果：{state.get('wild_training_last_result') or '无'}",
    ]
    if state.get("wild_training_last_error"):
        lines.append(f"- 最近异常：{state.get('wild_training_last_error')}")
    return "\n".join(lines)


async def apply_wild_training_strategy(strategy):
    normalized = normalize_wild_training_strategy(strategy)
    set_wild_training_strategy(get_current_identity_id(), normalized)
    save_state()
    return True, f"已保存野外历练策略：{normalized}"


def _is_wild_training_reply(text, reply_to, matched_family=None):
    if matched_family == "wild_training":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    raw_text = str(text or "")
    return orig_cmd == CMD_WILD_TRAINING or orig_cmd.startswith(f"{CMD_WILD_TRAINING} ") or raw_text.startswith(WILD_TRAINING_TITLE)


def _result_summary(text):
    raw_text = str(text or "").strip()
    parts = []
    xiuwei_match = RE_WILD_TRAINING_XIUWEI.search(raw_text)
    if xiuwei_match:
        parts.append(f"修为{xiuwei_match.group(1).replace(' ', '')}")
    rewards = [f"{name}x{count}" for name, count in RE_WILD_TRAINING_REWARD.findall(raw_text)]
    if rewards:
        parts.append("奖励:" + "、".join(rewards))
    if parts:
        return " ｜ ".join(parts)
    for line in raw_text.splitlines():
        line = line.strip()
        if line.startswith("【野外历练"):
            return line[:40]
    return raw_text[:60] or "已处理"


async def handle_wild_training_reply(text, now, reply_to, matched_family=None):
    if not state.get("wild_training_enabled"):
        return False
    if not _is_wild_training_reply(text, reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "").strip()
    if has_wait_time(raw_text) and any(keyword in raw_text for keyword in WILD_TRAINING_CD_KEYWORDS):
        wait_sec = parse_wait_time(raw_text)
        state["next_wild_training_time"] = float(now + wait_sec + CD_BUFFER_SEC + random.uniform(10, 60))
        state["wild_training_reply_to_msg_id"] = 0
        state["wild_training_reply_due_at"] = 0
        state["wild_training_retry_count"] = 0
        state["wild_training_last_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
        state["wild_training_last_result"] = "冷却中"
        state["wild_training_last_error"] = ""
        save_state()
        await send_audit_log(f"🏞️ 野外历练 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}", scope="identity")
        return True

    if not any(marker in raw_text for marker in WILD_TRAINING_RESULT_MARKERS):
        return False

    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_last_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
    state["wild_training_last_result"] = _result_summary(raw_text)
    state["wild_training_last_error"] = ""
    _schedule_next(now)
    save_state()
    await send_audit_log(f"🏞️ 野外历练结果：{state['wild_training_last_result']}", scope="identity", limit=220)
    return True


async def run_wild_training_scheduler(now):
    if not state.get("wild_training_enabled"):
        return

    reply_to_msg_id = int(state.get("wild_training_reply_to_msg_id", 0) or 0)
    if reply_to_msg_id > 0:
        if now < float(state.get("wild_training_reply_due_at", 0) or 0):
            return
        state["wild_training_reply_to_msg_id"] = 0
        state["wild_training_reply_due_at"] = 0
        if int(state.get("wild_training_retry_count", 0) or 0) < 1:
            state["wild_training_retry_count"] = int(state.get("wild_training_retry_count", 0) or 0) + 1
            _schedule_retry(now)
            state["wild_training_last_error"] = f"野外历练回复超时，准备补发一次，原消息ID={reply_to_msg_id}"
        else:
            _schedule_next(now)
            state["wild_training_last_error"] = f"野外历练补发后仍无回复，进入下一轮，原消息ID={reply_to_msg_id}"
        save_state()
        await send_audit_log(f"⚠️ {state['wild_training_last_error']}", scope="identity")
        return

    if now < float(state.get("next_wild_training_time", 0) or 0):
        return

    strategy = normalize_wild_training_strategy(get_wild_training_strategy())
    msg = await send_game_command(get_wild_training_command(strategy), track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        if int(state.get("wild_training_retry_count", 0) or 0) < 1:
            state["wild_training_retry_count"] = int(state.get("wild_training_retry_count", 0) or 0) + 1
            _schedule_retry(sent_at)
            state["wild_training_last_error"] = "野外历练发送失败，准备补发一次"
        else:
            _schedule_next(sent_at)
            state["wild_training_last_error"] = "野外历练补发发送失败，进入下一轮"
        save_state()
        await send_audit_log(f"❌ {state['wild_training_last_error']}。", scope="identity")
        return

    state["wild_training_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["wild_training_reply_due_at"] = sent_at + WILD_TRAINING_REPLY_TIMEOUT_SEC
    state["wild_training_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["wild_training_last_result"] = f"已发送：{strategy}"
    state["wild_training_last_error"] = ""
    save_state()
    console_log(f"🏞️ 野外历练已发送：{strategy}（msg_id={msg.id}）", scope="identity")


__all__ = [
    "WILD_TRAINING_REPLY_TIMEOUT_SEC",
    "apply_wild_training_strategy",
    "clear_wild_training_state",
    "get_wild_training_command",
    "get_wild_training_status_text",
    "handle_wild_training_reply",
    "normalize_wild_training_strategy",
    "run_wild_training_scheduler",
    "schedule_wild_training_initial_check",
]
