import random
import re
import time

from ..config import (
    CMD_NANLONG_EXCHANGE_FABAO,
    CMD_NANLONG_EXCHANGE_GONGFA,
    CMD_NANLONG_REJECT,
    NANLONG_REPLY_DELAY_MAX_SEC,
    NANLONG_REPLY_DELAY_MIN_SEC,
    NANLONG_REPLY_TIMEOUT_SEC,
    RE_WHITESPACE,
)
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_identity_enabled,
    get_identity_ids,
    get_nanlong_choice,
    get_send_as_tags,
    set_nanlong_choice,
    state,
)
from ..timing import fmt_abs_ts, fmt_remaining

NANLONG_CHOICE_EXCHANGE_FABAO = "exchange_fabao"
NANLONG_CHOICE_EXCHANGE_GONGFA = "exchange_gongfa"
NANLONG_CHOICE_REJECT = "reject"
NANLONG_CHOICE_LABELS = {
    NANLONG_CHOICE_EXCHANGE_FABAO: "交换法宝",
    NANLONG_CHOICE_EXCHANGE_GONGFA: "交换功法",
    NANLONG_CHOICE_REJECT: "拒绝交易",
}
NANLONG_CHOICE_COMMANDS = {
    NANLONG_CHOICE_EXCHANGE_FABAO: CMD_NANLONG_EXCHANGE_FABAO,
    NANLONG_CHOICE_EXCHANGE_GONGFA: CMD_NANLONG_EXCHANGE_GONGFA,
    NANLONG_CHOICE_REJECT: CMD_NANLONG_REJECT,
}
NANLONG_TARGET_TAG_PATTERN = r"[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+"
RE_NANLONG_TARGET_TAG = re.compile(rf"@({NANLONG_TARGET_TAG_PATTERN})")
RE_NANLONG_MINUTES = re.compile(r"你有\s*(\d+)\s*分钟")
NANLONG_SUCCESS_KEYWORDS = ("【天机异闻·魔君之怒】", "【天机异闻·南陇侯的交易】")
NANLONG_CONFIRM_RETRY_DELAY_SEC = 60
NANLONG_CONFIRM_RETRY_LIMIT = 1
NANLONG_CONFIRM_CHOICES = {NANLONG_CHOICE_EXCHANGE_FABAO, NANLONG_CHOICE_EXCHANGE_GONGFA}


def _normalize_text(text):
    return RE_WHITESPACE.sub("", text or "").strip().lower()


def normalize_nanlong_choice(choice):
    normalized = str(choice or "").strip().lower()
    if normalized in NANLONG_CHOICE_COMMANDS:
        return normalized
    return NANLONG_CHOICE_REJECT


def get_nanlong_choice_label(choice):
    return NANLONG_CHOICE_LABELS.get(normalize_nanlong_choice(choice), NANLONG_CHOICE_LABELS[NANLONG_CHOICE_REJECT])


def get_nanlong_choice_command(choice):
    return NANLONG_CHOICE_COMMANDS.get(normalize_nanlong_choice(choice), CMD_NANLONG_REJECT)


def resolve_nanlong_choice(send_as_id=None):
    return normalize_nanlong_choice(get_nanlong_choice(send_as_id)), "manual"


def _extract_nanlong_target_key(text):
    matched_tags = {}
    for raw_tag in RE_NANLONG_TARGET_TAG.findall(text or ""):
        tag = str(raw_tag or "").strip().lstrip("@")
        tag_key = _normalize_text(tag)
        if tag_key and tag_key not in matched_tags:
            matched_tags[tag_key] = f"@{tag}"
    if len(matched_tags) != 1:
        return ""
    return next(iter(matched_tags.keys()))


def _find_nanlong_identity_id(text):
    target_key = _extract_nanlong_target_key(text)
    if target_key:
        matched_ids = []
        for identity_id in get_identity_ids():
            if not get_identity_enabled(identity_id):
                continue
            normalized_tags = {_normalize_text(tag.lstrip("@")) for tag in get_send_as_tags(identity_id) if tag}
            if target_key in normalized_tags:
                matched_ids.append(identity_id)
        if len(matched_ids) == 1:
            return matched_ids[0]
        return None

    compact_text = _normalize_text(text)
    if not compact_text:
        return None
    matched_ids = []
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        normalized_tags = {_normalize_text(tag) for tag in get_send_as_tags(identity_id) if tag}
        if any(tag and tag in compact_text for tag in normalized_tags):
            matched_ids.append(identity_id)
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


def _parse_nanlong_prompt(text):
    raw_text = text or ""
    compact_text = _normalize_text(raw_text)
    if "南陇侯" not in raw_text or "做出抉择" not in raw_text:
        return None
    if "回复本消息.交换法宝" not in compact_text or "回复本消息.交换功法" not in compact_text or "回复本消息.拒绝交易" not in compact_text:
        return None

    timeout_sec = NANLONG_REPLY_TIMEOUT_SEC
    matched = RE_NANLONG_MINUTES.search(raw_text)
    if matched:
        try:
            timeout_sec = max(60, int(matched.group(1)) * 60)
        except (TypeError, ValueError):
            timeout_sec = NANLONG_REPLY_TIMEOUT_SEC
    return {"timeout_sec": timeout_sec}


def _get_nanlong_pending_state():
    return (
        int(state.get("nanlong_reply_to_msg_id", 0) or 0),
        float(state.get("next_nanlong_time", 0) or 0),
        float(state.get("nanlong_reply_due_at", 0) or 0),
    )


def _is_nanlong_success_reply(text):
    return any(keyword in str(text or "") for keyword in NANLONG_SUCCESS_KEYWORDS)


def _has_active_nanlong_pending(now):
    reply_to_msg_id, deadline, _reply_due_at = _get_nanlong_pending_state()
    return reply_to_msg_id > 0 and deadline > now


def _match_nanlong_prompt_for_current_identity(text):
    parsed = _parse_nanlong_prompt(text)
    if not parsed:
        return None
    identity_id = _find_nanlong_identity_id(text)
    if identity_id is None or identity_id != get_current_identity_id():
        return None
    return parsed


def _schedule_nanlong_reply_due(now):
    delay = random.randint(NANLONG_REPLY_DELAY_MIN_SEC, NANLONG_REPLY_DELAY_MAX_SEC)
    state["nanlong_reply_due_at"] = float(now + delay)
    return state["nanlong_reply_due_at"]


def _set_nanlong_pending(reply_to_msg_id, deadline_at, now):
    state["nanlong_reply_to_msg_id"] = int(reply_to_msg_id or 0)
    state["next_nanlong_time"] = float(deadline_at or 0)
    state["nanlong_last_msg_id"] = 0
    state["nanlong_retry_count"] = 0
    state["nanlong_last_command"] = ""
    state["nanlong_last_error"] = ""
    _schedule_nanlong_reply_due(now)


def _set_nanlong_error_and_save(message):
    state["nanlong_last_error"] = message
    save_state()


async def _send_nanlong_command(command, reply_to_msg_id):
    return await send_game_command(command, track=False, reply_to=reply_to_msg_id)


async def _maybe_audit_nanlong_prompt_override(previous_reply_to, previous_deadline, now, new_reply_to):
    if previous_reply_to > 0 and previous_reply_to != new_reply_to and previous_deadline > now:
        await send_audit_log(f"🤝 南陇侯新抉择覆盖旧消息：{previous_reply_to}->{new_reply_to}")


async def _finalize_nanlong_success(audit_text):
    await send_audit_log(audit_text)
    clear_nanlong_state(persist=True)


def get_nanlong_status_text():
    choice = normalize_nanlong_choice(get_nanlong_choice())
    reply_to_msg_id, deadline, reply_due_at = _get_nanlong_pending_state()
    lines = [
        "🤝 南陇侯",
        f"- 当前选择：{get_nanlong_choice_label(choice)}",
        f"- 待回复消息ID：{reply_to_msg_id or '无'}",
        f"- 计划回复时间：{fmt_abs_ts(reply_due_at)}（{fmt_remaining(reply_due_at)}）",
        f"- 截止时间：{fmt_abs_ts(deadline)}（{fmt_remaining(deadline)}）",
        f"- 最近错误：{state.get('nanlong_last_error') or '无'}",
    ]
    return "\n".join(lines)


def clear_nanlong_state(*, persist=False, keep_last_error=False):
    state["next_nanlong_time"] = 0
    state["nanlong_reply_to_msg_id"] = 0
    state["nanlong_reply_due_at"] = 0
    state["nanlong_last_msg_id"] = 0
    state["nanlong_retry_count"] = 0
    state["nanlong_last_command"] = ""
    if not keep_last_error:
        state["nanlong_last_error"] = ""
    if persist:
        save_state()
    else:
        mark_dirty()


async def apply_nanlong_choice(choice, now=None):
    normalized_choice = normalize_nanlong_choice(choice)
    if now is None:
        now = time.time()

    set_nanlong_choice(get_current_identity_id(), normalized_choice)

    pending_reply_to, _deadline, reply_due_at = _get_nanlong_pending_state()
    if not state.get("nanlong_enabled") or not _has_active_nanlong_pending(now):
        save_state()
        return True, f"已保存南陇侯选择：{get_nanlong_choice_label(normalized_choice)}"

    if reply_due_at <= now:
        _schedule_nanlong_reply_due(now)
    save_state()
    return True, f"已保存南陇侯选择：{get_nanlong_choice_label(normalized_choice)}，将按计划回复"


async def handle_nanlong_prompt(text, now, event):
    if not state.get("nanlong_enabled"):
        return False

    parsed = _match_nanlong_prompt_for_current_identity(text)
    if not parsed:
        return False

    reply_to_msg_id = int(getattr(event, "id", 0) or 0)
    prev_reply_to_msg_id, prev_deadline, _prev_due_at = _get_nanlong_pending_state()
    await _maybe_audit_nanlong_prompt_override(prev_reply_to_msg_id, prev_deadline, now, reply_to_msg_id)

    _set_nanlong_pending(reply_to_msg_id, now + float(parsed["timeout_sec"]), now)
    save_state()
    return True


async def run_nanlong_scheduler(now):
    if not state.get("nanlong_enabled"):
        return

    reply_to_msg_id, next_nanlong_time, reply_due_at = _get_nanlong_pending_state()
    if reply_to_msg_id <= 0 or next_nanlong_time <= 0:
        return
    if now >= next_nanlong_time:
        state["nanlong_last_error"] = "南陇侯提示已超时"
        await send_audit_log(f"⚠️ 南陇侯抉择超时，消息ID={reply_to_msg_id}")
        clear_nanlong_state(persist=True, keep_last_error=True)
        return
    if reply_due_at <= 0 or now < reply_due_at:
        return

    choice = normalize_nanlong_choice(get_nanlong_choice())
    command = get_nanlong_choice_command(choice)
    requires_confirmation = choice in NANLONG_CONFIRM_CHOICES
    is_confirmation_retry = bool(state.get("nanlong_last_msg_id"))
    if requires_confirmation and is_confirmation_retry and int(state.get("nanlong_retry_count", 0) or 0) >= NANLONG_CONFIRM_RETRY_LIMIT:
        state["nanlong_last_error"] = "南陇侯交易结果未确认"
        await send_audit_log(f"⚠️ 南陇侯自动回复重发 {NANLONG_CONFIRM_RETRY_LIMIT} 次仍未确认，已停止。")
        clear_nanlong_state(persist=True, keep_last_error=True)
        return
    sent_msg = await _send_nanlong_command(command, reply_to_msg_id)
    sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time()) if sent_msg else time.time()
    if not sent_msg:
        state["nanlong_last_error"] = "南陇侯自动回复发送失败"
        _schedule_nanlong_reply_due(sent_at)
        save_state()
        await send_audit_log("❌ 南陇侯自动回复失败，待处理已保留。")
        return

    if not requires_confirmation:
        await _finalize_nanlong_success(f"🤝 南陇侯自动选择：{get_nanlong_choice_label(choice)}")
        return

    state["nanlong_last_msg_id"] = int(getattr(sent_msg, "id", 0) or 0)
    state["nanlong_last_command"] = command
    if is_confirmation_retry:
        state["nanlong_retry_count"] = int(state.get("nanlong_retry_count", 0) or 0) + 1
    state["nanlong_reply_due_at"] = sent_at + NANLONG_CONFIRM_RETRY_DELAY_SEC
    state["nanlong_last_error"] = "等待南陇侯交易结果"
    save_state()
    if is_confirmation_retry:
        await send_audit_log(f"🤝 南陇侯自动选择重发 {state['nanlong_retry_count']}/{NANLONG_CONFIRM_RETRY_LIMIT}：{get_nanlong_choice_label(choice)}，等待确认")


async def handle_nanlong_reply(text, now, reply_to, matched_family=None):
    if not state.get("nanlong_enabled"):
        return False
    if matched_family != "nanlong":
        return False
    if not state.get("nanlong_last_msg_id"):
        return False
    if not _is_nanlong_success_reply(text):
        return True

    await _finalize_nanlong_success("🤝 南陇侯交易结果已确认")
    return True


async def handle_nanlong_result_broadcast(text, now, event):
    if not state.get("nanlong_enabled"):
        return False
    if not _is_nanlong_success_reply(text):
        return False
    if not state.get("nanlong_last_msg_id"):
        return False
    identity_id = _find_nanlong_identity_id(text)
    if identity_id is None or identity_id != get_current_identity_id():
        return False

    await _finalize_nanlong_success("🤝 南陇侯交易结果已确认")
    return True


__all__ = [
    "NANLONG_CHOICE_EXCHANGE_FABAO",
    "NANLONG_CHOICE_EXCHANGE_GONGFA",
    "NANLONG_CHOICE_REJECT",
    "apply_nanlong_choice",
    "clear_nanlong_state",
    "get_nanlong_choice_command",
    "get_nanlong_choice_label",
    "get_nanlong_status_text",
    "handle_nanlong_prompt",
    "handle_nanlong_reply",
    "handle_nanlong_result_broadcast",
    "normalize_nanlong_choice",
    "resolve_nanlong_choice",
    "run_nanlong_scheduler",
]
