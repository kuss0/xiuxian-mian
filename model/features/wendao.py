import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_WENDAO,
    RETRY_MAX_SEC,
    WENDAO_CD,
    WENDAO_JITTER_MAX_SEC,
    WENDAO_JITTER_MIN_SEC,
    WENDAO_REPLY_TIMEOUT_SEC,
)
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_send_as_profile, state
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .storage_bag import apply_storage_bag_item_deltas


WENDAO_PENDING_KEYWORD = "虔诚地向宗门长老问道"
WENDAO_RESULT_TITLE = "【问道得宝】"
WENDAO_CD_KEYWORD = "天机不可频繁窥探"
WENDAO_SECT_NAME = "元婴宗"
WENDAO_MIN_XIUWEI = 1000
WENDAO_RECOVERY_MIN_SEC = 60
WENDAO_RECOVERY_MAX_SEC = 180
RE_WENDAO_XIUWEI = re.compile(r"修为增加了\s*([\d,]+)\s*点")
RE_WENDAO_REWARD_BRACKET = re.compile(r"^【([^】]+)】\s*[xX*＊]\s*([\d,]+)$")
RE_WENDAO_REWARD_PLAIN = re.compile(r"^(.+?)\s*[xX*＊]\s*([\d,]+)$")
RE_WENDAO_NOISE_PREFIX = re.compile(r"^[\-•·\s]+")


def _parse_int(value):
    try:
        return int(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0


def _schedule_next_wendao(now, delay_sec=None):
    if delay_sec is None:
        delay_sec = _resolve_wendao_cd_sec() + random.uniform(WENDAO_JITTER_MIN_SEC, WENDAO_JITTER_MAX_SEC)
    state["next_wendao_time"] = float(now + max(1, delay_sec))
    return state["next_wendao_time"]


def _resolve_wendao_cd_sec():
    return WENDAO_CD


def _get_profile_field(name, default=None):
    profile = get_send_as_profile(get_current_identity_id()) or {}
    return profile.get(name, default)


def _get_profile_sect_name():
    return str(_get_profile_field("sect_name", "") or "").strip()


def _is_profile_unknown_sect():
    return not _get_profile_sect_name()


def _is_profile_known_wrong_sect():
    sect_name = _get_profile_sect_name()
    return bool(sect_name and sect_name != WENDAO_SECT_NAME)


def _has_known_enough_xiuwei():
    xiuwei_current = _parse_int(_get_profile_field("xiuwei_current", 0))
    if xiuwei_current <= 0:
        return True
    return xiuwei_current >= WENDAO_MIN_XIUWEI


def _set_wendao_error(message, *, next_delay=None, now=None, persist=True):
    state["wendao_last_error"] = str(message or "").strip()
    if next_delay is not None:
        if now is None:
            now = time.time()
        state["next_wendao_time"] = float(now + max(1, next_delay))
    if persist:
        save_state()
    else:
        mark_dirty()


def _wendao_next_time_blocks(now):
    return cd_blocks(state.get("next_wendao_time", 0), now, 0)


def _clear_wendao_pending():
    state["wendao_reply_to_msg_id"] = 0
    state["wendao_reply_due_at"] = 0
    state["wendao_pending_result_msg_id"] = 0
    state["wendao_sent_at"] = 0


def _is_wendao_reply(reply_to=None, matched_family=None):
    if matched_family == "wendao":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return orig_cmd == CMD_WENDAO or orig_cmd.startswith(f"{CMD_WENDAO} ")


def _parse_reward_line(line):
    raw_line = RE_WENDAO_NOISE_PREFIX.sub("", str(line or "").strip())
    if not raw_line or "修为增加" in raw_line:
        return "", 0
    match = RE_WENDAO_REWARD_BRACKET.match(raw_line)
    if not match:
        match = RE_WENDAO_REWARD_PLAIN.match(raw_line)
    if not match:
        return "", 0
    item_name = str(match.group(1) or "").strip()
    count = _parse_int(match.group(2))
    if not item_name or count <= 0:
        return "", 0
    return item_name, count


def parse_wendao_result_summary(text):
    raw_text = str(text or "").strip()
    parts = []
    item_deltas = {}

    xiuwei_match = RE_WENDAO_XIUWEI.search(raw_text)
    if xiuwei_match:
        xiuwei_gain = _parse_int(xiuwei_match.group(1))
        if xiuwei_gain > 0:
            parts.append(f"修为 +{xiuwei_gain}")

    for line in raw_text.splitlines():
        item_name, count = _parse_reward_line(line)
        if not item_name or count <= 0:
            continue
        item_deltas[item_name] = item_deltas.get(item_name, 0) + count

    if item_deltas:
        parts.append("奖励：" + "、".join(f"{name}x{count}" for name, count in item_deltas.items()))

    return (" ｜ ".join(parts) if parts else "问道得宝"), item_deltas


def is_wendao_reply_text(text):
    raw_text = str(text or "").strip()
    return (
        WENDAO_PENDING_KEYWORD in raw_text
        or raw_text.startswith(WENDAO_RESULT_TITLE)
        or WENDAO_CD_KEYWORD in raw_text
    )


def clear_wendao_state(*, persist=False, keep_last_error=False):
    last_error = state.get("wendao_last_error") if keep_last_error else ""
    state["next_wendao_time"] = 0
    state["wendao_reply_to_msg_id"] = 0
    state["wendao_reply_due_at"] = 0
    state["wendao_pending_result_msg_id"] = 0
    state["wendao_sent_at"] = 0
    state["wendao_last_msg_id"] = 0
    state["wendao_last_result"] = ""
    state["wendao_last_error"] = last_error or ""
    if persist:
        save_state()
    else:
        mark_dirty()


def get_wendao_status_text():
    reply_to_msg_id = int(state.get("wendao_reply_to_msg_id", 0) or 0)
    pending_result_msg_id = int(state.get("wendao_pending_result_msg_id", 0) or 0)
    lines = [
        "🧭 问道",
        f"- 已启用：{'是' if state.get('wendao_enabled') else '否'}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_wendao_time', 0))}（{fmt_remaining(state.get('next_wendao_time', 0))}）",
        f"- 宗门：{str(_get_profile_field('sect_name', '') or '未知')}",
        f"- 当前修为：{_parse_int(_get_profile_field('xiuwei_current', 0)) or '未知'}",
        "- CD口径：默认 12h（未确认已装备风雷翅不提前）",
        f"- 待回复命令ID：{reply_to_msg_id or '无'}",
        f"- 待编辑结果ID：{pending_result_msg_id or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('wendao_reply_due_at', 0))}（{fmt_remaining(state.get('wendao_reply_due_at', 0))}）",
        f"- 最近结果：{state.get('wendao_last_result') or '无'}",
    ]
    if state.get("wendao_last_msg_id"):
        lines.append(f"- 最近结果消息ID：{state['wendao_last_msg_id']}")
    if state.get("wendao_last_error"):
        lines.append(f"- 最近异常：{state['wendao_last_error']}")
    return "\n".join(lines)


async def handle_wendao_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0):
    if not state.get("wendao_enabled"):
        return False
    if not _is_wendao_reply(reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "").strip()
    result_msg_id = int(result_msg_id or 0)

    if WENDAO_CD_KEYWORD in raw_text and has_wait_time(raw_text):
        wait_sec = parse_wait_time(raw_text)
        state["next_wendao_time"] = float(now + wait_sec + CD_BUFFER_SEC)
        _clear_wendao_pending()
        state["wendao_last_msg_id"] = result_msg_id or int(getattr(reply_to, "id", 0) or 0)
        state["wendao_last_result"] = "冷却中"
        state["wendao_last_error"] = ""
        save_state()
        await send_audit_log(f"🧭 问道 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    if WENDAO_PENDING_KEYWORD in raw_text:
        if result_msg_id > 0:
            state["wendao_pending_result_msg_id"] = result_msg_id
            state["wendao_last_msg_id"] = result_msg_id
        state["wendao_last_result"] = "问道中"
        state["wendao_last_error"] = ""
        save_state()
        return True

    if raw_text.startswith(WENDAO_RESULT_TITLE):
        result_summary, item_deltas = parse_wendao_result_summary(raw_text)
        _clear_wendao_pending()
        state["wendao_last_msg_id"] = result_msg_id or int(getattr(reply_to, "id", 0) or 0)
        state["wendao_last_result"] = result_summary
        state["wendao_last_error"] = ""
        _schedule_next_wendao(now)
        save_state()
        if item_deltas:
            apply_storage_bag_item_deltas(get_current_identity_id(), item_deltas)
        await send_audit_log(f"🧭 问道结果：{result_summary}", scope="identity", limit=220)
        return True

    if any(keyword in raw_text for keyword in ("修为不足", "神魂之力不足", "修为不够")):
        _clear_wendao_pending()
        _set_wendao_error("修为不足，延后校准", next_delay=RETRY_MAX_SEC, now=now)
        await send_audit_log("🧭 问道被拦截：修为不足，已延后。", scope="identity", limit=180)
        return True

    if "并非元婴宗" in raw_text or "非元婴宗" in raw_text:
        state["wendao_enabled"] = False
        _clear_wendao_pending()
        _set_wendao_error("宗门不符，已关闭问道", persist=True)
        await send_audit_log("🧭 问道被拦截：当前不是元婴宗，已关闭模块。", scope="identity", limit=180)
        return True

    return False


async def run_wendao_scheduler(now):
    if not state.get("wendao_enabled"):
        return

    if _is_profile_unknown_sect():
        if not _wendao_next_time_blocks(now):
            _set_wendao_error("宗门未知，等待身份资料确认后再问道", next_delay=RETRY_MAX_SEC, now=now)
        return

    if _is_profile_known_wrong_sect():
        state["wendao_enabled"] = False
        _clear_wendao_pending()
        _set_wendao_error("宗门不符，已关闭问道", persist=True)
        await send_audit_log("🧭 问道已关闭：身份资料显示当前不是元婴宗。", scope="identity", limit=180)
        return

    if not _has_known_enough_xiuwei():
        if not _wendao_next_time_blocks(now):
            _set_wendao_error("修为不足1000，暂不发送问道", next_delay=RETRY_MAX_SEC, now=now)
        return

    reply_to_msg_id = int(state.get("wendao_reply_to_msg_id", 0) or 0)
    reply_due_at = float(state.get("wendao_reply_due_at", 0) or 0)
    if reply_to_msg_id > 0:
        if reply_due_at > now:
            return
        _clear_wendao_pending()
        state["next_wendao_time"] = float(now + RETRY_MAX_SEC)
        state["wendao_last_error"] = "问道回复超时"
        save_state()
        await send_audit_log(f"⚠️ 问道回复超时，消息ID={reply_to_msg_id}，稍后重试。", scope="identity", limit=220)
        return

    if _wendao_next_time_blocks(now):
        return

    msg = await send_game_command(CMD_WENDAO, track=False, max_retry=0, source_module="问道")
    if not msg:
        state["next_wendao_time"] = float(now + RETRY_MAX_SEC)
        state["wendao_last_error"] = "问道发送失败"
        save_state()
        await send_audit_log("❌ 问道发送失败，稍后重试。", scope="identity", limit=180)
        return

    sent_at = float(getattr(msg, "sent_at", 0) or time.time())
    state["wendao_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["wendao_reply_due_at"] = sent_at + WENDAO_REPLY_TIMEOUT_SEC
    state["wendao_pending_result_msg_id"] = 0
    state["wendao_sent_at"] = sent_at
    state["wendao_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["wendao_last_result"] = "已发送"
    state["wendao_last_error"] = ""
    state["next_wendao_time"] = state["wendao_reply_due_at"]
    save_state()
    console_log(f"🧭 问道已发送，等待回复→{fmt_abs_ts(state['wendao_reply_due_at'])}", scope="identity", limit=180)


def schedule_wendao_initial_check(now, *, persist=False, keep_last_error=True):
    last_error = state.get("wendao_last_error") if keep_last_error else ""
    state["wendao_reply_to_msg_id"] = 0
    state["wendao_reply_due_at"] = 0
    state["wendao_pending_result_msg_id"] = 0
    state["wendao_sent_at"] = 0
    state["wendao_last_error"] = last_error or ""
    state["next_wendao_time"] = float(now + random.uniform(WENDAO_RECOVERY_MIN_SEC, WENDAO_RECOVERY_MAX_SEC))
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_wendao_time"]


__all__ = [
    "WENDAO_CD_KEYWORD",
    "WENDAO_PENDING_KEYWORD",
    "WENDAO_RESULT_TITLE",
    "clear_wendao_state",
    "get_wendao_status_text",
    "handle_wendao_reply",
    "is_wendao_reply_text",
    "parse_wendao_result_summary",
    "run_wendao_scheduler",
    "schedule_wendao_initial_check",
]
