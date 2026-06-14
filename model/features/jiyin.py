import math
import random
import re
import time

from ..config import (
    CMD_JIYIN_HIDE_AURA,
    CMD_JIYIN_OFFER_SOUL,
    JIYIN_REPLY_TIMEOUT_SEC,
    RE_WHITESPACE,
)
from ..delayed_actions import cancel_delayed_action, schedule_delayed_action
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_identity_enabled,
    get_identity_ids,
    REALM_SORT_ORDER,
    get_jiyin_choice,
    get_realm_sort_index,
    get_send_as_profile,
    get_send_as_tags,
    set_jiyin_choice,
    state,
)
from ..timing import fmt_abs_ts, fmt_remaining

JIYIN_CHOICE_OFFER_SOUL = "offer_soul"
JIYIN_CHOICE_HIDE_AURA = "hide_aura"
JIYIN_THRESHOLD_REALM = "元婴后期"
JIYIN_THRESHOLD_REALM_INDEX = get_realm_sort_index(JIYIN_THRESHOLD_REALM)
JIYIN_CHOICE_LABELS = {
    JIYIN_CHOICE_OFFER_SOUL: "献上魂魄",
    JIYIN_CHOICE_HIDE_AURA: "收敛气息",
}
JIYIN_CHOICE_COMMANDS = {
    JIYIN_CHOICE_OFFER_SOUL: CMD_JIYIN_OFFER_SOUL,
    JIYIN_CHOICE_HIDE_AURA: CMD_JIYIN_HIDE_AURA,
}
JIYIN_TARGET_TAG_PATTERN = r"[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+"
RE_JIYIN_TARGET_TAG = re.compile(rf"@({JIYIN_TARGET_TAG_PATTERN})")
RE_JIYIN_MINUTES = re.compile(r"你必须在\s*(\d+)\s*分钟")
JIYIN_REPLY_DELAY_MIN_SEC = 20
JIYIN_REPLY_DELAY_MAX_SEC = 30
JIYIN_REPLY_DEADLINE_GRACE_SEC = 5
JIYIN_DELAYED_SOURCE_MODULE = "jiyin"
JIYIN_DELAYED_OP_ID = "jiyin_prompt_reply"


def _normalize_text(text):
    return RE_WHITESPACE.sub("", text or "").strip().lower()


def normalize_jiyin_choice(choice):
    normalized = str(choice or "").strip().lower()
    if normalized in JIYIN_CHOICE_COMMANDS:
        return normalized
    return ""


def get_jiyin_choice_label(choice):
    normalized = normalize_jiyin_choice(choice)
    return JIYIN_CHOICE_LABELS.get(normalized, "未设置")


def get_jiyin_choice_command(choice):
    normalized = normalize_jiyin_choice(choice)
    return JIYIN_CHOICE_COMMANDS.get(normalized, "")


def resolve_jiyin_choice(send_as_id=None):
    saved_choice = normalize_jiyin_choice(get_jiyin_choice(send_as_id))
    if saved_choice:
        return saved_choice, "manual"

    profile = get_send_as_profile(send_as_id)
    realm = str(profile.get("realm") or "").strip()
    if not realm or realm not in REALM_SORT_ORDER:
        return JIYIN_CHOICE_HIDE_AURA, "auto"

    realm_index = get_realm_sort_index(realm, xiuwei_max=profile.get("xiuwei_max", 0))
    if realm_index > JIYIN_THRESHOLD_REALM_INDEX:
        return JIYIN_CHOICE_OFFER_SOUL, "auto"
    return JIYIN_CHOICE_HIDE_AURA, "auto"


def _extract_jiyin_target_key(text):
    matched_tags = {}
    for raw_tag in RE_JIYIN_TARGET_TAG.findall(text or ""):
        tag = str(raw_tag or "").strip().lstrip("@")
        tag_key = _normalize_text(tag)
        if tag_key and tag_key not in matched_tags:
            matched_tags[tag_key] = f"@{tag}"
    if len(matched_tags) != 1:
        return ""
    return next(iter(matched_tags.keys()))


def _find_jiyin_identity_id(text):
    target_key = _extract_jiyin_target_key(text)
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


def _parse_jiyin_prompt(text):
    raw_text = text or ""
    if "做出抉择" not in raw_text:
        return None
    if "回复本消息 .献上魂魄" not in raw_text or "回复本消息 .收敛气息" not in raw_text:
        return None

    timeout_sec = JIYIN_REPLY_TIMEOUT_SEC
    matched = RE_JIYIN_MINUTES.search(raw_text)
    if matched:
        try:
            timeout_sec = max(60, int(matched.group(1)) * 60)
        except (TypeError, ValueError):
            timeout_sec = JIYIN_REPLY_TIMEOUT_SEC
    return {
        "timeout_sec": timeout_sec,
    }


def _is_empty_state_value(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_state_int(value):
    if _is_empty_state_value(value):
        return 0, False
    try:
        return int(value or 0), False
    except (TypeError, ValueError, OverflowError):
        return 0, True


def _parse_state_timestamp(value):
    if _is_empty_state_value(value):
        return 0.0, False
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0, True
    if not math.isfinite(parsed):
        return 0.0, True
    return parsed, False


def _get_jiyin_pending_state():
    reply_to_msg_id, reply_dirty = _parse_state_int(state.get("jiyin_reply_to_msg_id", 0))
    deadline, deadline_dirty = _parse_state_timestamp(state.get("next_jiyin_time", 0))
    return reply_to_msg_id, deadline, reply_dirty or deadline_dirty


def _has_active_jiyin_pending(now):
    reply_to_msg_id, deadline, pending_dirty = _get_jiyin_pending_state()
    return not pending_dirty and reply_to_msg_id > 0 and deadline > now


def _resolve_effective_jiyin_command(send_as_id=None):
    effective_choice, choice_source = resolve_jiyin_choice(send_as_id)
    return effective_choice, choice_source, get_jiyin_choice_command(effective_choice)


def _match_jiyin_prompt_for_current_identity(text):
    parsed = _parse_jiyin_prompt(text)
    if not parsed:
        return None
    identity_id = _find_jiyin_identity_id(text)
    if identity_id is None or identity_id != get_current_identity_id():
        return None
    return parsed


def _set_jiyin_pending(reply_to_msg_id, deadline_at):
    state["jiyin_reply_to_msg_id"] = int(reply_to_msg_id or 0)
    state["next_jiyin_time"] = float(deadline_at or 0)
    state["jiyin_last_error"] = ""


def _jiyin_delayed_dedupe_key(send_as_id=None):
    identity_id = int(send_as_id or get_current_identity_id() or 0)
    return f"jiyin-prompt-reply:{identity_id}"


def _calc_jiyin_reply_due_at(now, timeout_sec):
    timeout_sec = max(1, int(timeout_sec or JIYIN_REPLY_TIMEOUT_SEC))
    latest_delay = max(1, timeout_sec - JIYIN_REPLY_DEADLINE_GRACE_SEC)
    max_delay = min(JIYIN_REPLY_DELAY_MAX_SEC, latest_delay)
    min_delay = min(JIYIN_REPLY_DELAY_MIN_SEC, max_delay)
    return float(now + random.randint(min_delay, max_delay))


def _schedule_jiyin_delayed_reply(command, reply_to_msg_id, now, timeout_sec, *, choice="", choice_source=""):
    identity_id = get_current_identity_id()
    due_at = _calc_jiyin_reply_due_at(now, timeout_sec)
    return schedule_delayed_action(
        command,
        due_at,
        send_as_id=identity_id,
        track=False,
        reply_to_msg_id=reply_to_msg_id,
        priority="reactive",
        source_module=JIYIN_DELAYED_SOURCE_MODULE,
        op_id=JIYIN_DELAYED_OP_ID,
        chain_id=f"jiyin:{identity_id}:{reply_to_msg_id}",
        dedupe_key=_jiyin_delayed_dedupe_key(identity_id),
        max_send_attempts=1,
        retry_delay_sec=30,
        now=now,
        extra={"choice": choice, "choice_source": choice_source},
    )


def _set_jiyin_error_and_save(message):
    state["jiyin_last_error"] = message
    save_state()


async def _send_jiyin_command(command, reply_to_msg_id):
    return await send_game_command(command, track=False, reply_to=reply_to_msg_id)


async def _maybe_audit_jiyin_prompt_override(previous_reply_to, previous_deadline, now, new_reply_to):
    if previous_reply_to > 0 and previous_reply_to != new_reply_to and previous_deadline > now:
        await send_audit_log(f"🌑 新抉择覆盖旧消息：{previous_reply_to}->{new_reply_to}")


async def _finalize_jiyin_success(audit_text):
    await send_audit_log(audit_text)
    clear_jiyin_state(persist=True)


def get_jiyin_status_text():
    saved_choice = normalize_jiyin_choice(get_jiyin_choice())
    effective_choice, choice_source = resolve_jiyin_choice()
    reply_to_msg_id, deadline, _ = _get_jiyin_pending_state()
    strategy_text = (
        f"已手动保存：{get_jiyin_choice_label(saved_choice)}"
        if saved_choice
        else "自动按境界"
    )
    lines = [
        "🌑 极阴祖师",
        f"- 当前策略：{strategy_text}",
        f"- 当前生效：{get_jiyin_choice_label(effective_choice)}（{'手动' if choice_source == 'manual' else '自动'}）",
        f"- 待回复消息ID：{reply_to_msg_id or '无'}",
        f"- 截止时间：{fmt_abs_ts(deadline)}（{fmt_remaining(deadline)}）",
        f"- 最近错误：{state.get('jiyin_last_error') or '无'}",
    ]
    return "\n".join(lines)


def clear_jiyin_state(*, persist=False, keep_last_error=False):
    cancel_delayed_action(dedupe_key=_jiyin_delayed_dedupe_key())
    state["next_jiyin_time"] = 0
    state["jiyin_reply_to_msg_id"] = 0
    if not keep_last_error:
        state["jiyin_last_error"] = ""
    if persist:
        save_state()
    else:
        mark_dirty()


async def apply_jiyin_choice(choice, now=None):
    raw_choice = str(choice or "").strip().lower()
    reset_to_auto = raw_choice == "auto"
    normalized_choice = "" if reset_to_auto else normalize_jiyin_choice(choice)
    if not reset_to_auto and not normalized_choice:
        return False, "未知极阴祖师选项"

    if now is None:
        now = time.time()

    set_jiyin_choice(get_current_identity_id(), normalized_choice)

    pending_reply_to, _, pending_dirty = _get_jiyin_pending_state()
    effective_choice, choice_source, command = _resolve_effective_jiyin_command()
    if not state.get("jiyin_enabled") or pending_dirty or not _has_active_jiyin_pending(now):
        save_state()
        if reset_to_auto:
            return True, f"已恢复极阴祖师自动判断：{get_jiyin_choice_label(effective_choice)}"
        return True, f"已保存极阴祖师选择：{get_jiyin_choice_label(normalized_choice)}"

    sent_msg = await _send_jiyin_command(command, pending_reply_to)
    if not sent_msg:
        cancel_delayed_action(dedupe_key=_jiyin_delayed_dedupe_key())
        _set_jiyin_error_and_save("极阴祖师选择发送失败")
        if reset_to_auto:
            return False, f"已恢复自动判断，但发送失败：{get_jiyin_choice_label(effective_choice)}"
        return False, f"已保存极阴祖师选择，但发送失败：{get_jiyin_choice_label(normalized_choice)}"

    cancel_delayed_action(dedupe_key=_jiyin_delayed_dedupe_key())
    await _finalize_jiyin_success(
        f"🌑 {'恢复自动' if reset_to_auto else '执行选择'}：{get_jiyin_choice_label(effective_choice)}（{'手动' if choice_source == 'manual' else '自动'}）"
    )
    if reset_to_auto:
        return True, f"已恢复极阴祖师自动判断并执行：{get_jiyin_choice_label(effective_choice)}"
    return True, f"已保存并执行极阴祖师选择：{get_jiyin_choice_label(normalized_choice)}"


async def handle_jiyin_prompt(text, now, event):
    if not state.get("jiyin_enabled"):
        return False

    parsed = _match_jiyin_prompt_for_current_identity(text)
    if not parsed:
        return False

    reply_to_msg_id = int(getattr(event, "id", 0) or 0)
    prev_reply_to_msg_id, prev_deadline, _ = _get_jiyin_pending_state()
    await _maybe_audit_jiyin_prompt_override(prev_reply_to_msg_id, prev_deadline, now, reply_to_msg_id)

    _set_jiyin_pending(reply_to_msg_id, now + float(parsed["timeout_sec"]))

    choice, choice_source, command = _resolve_effective_jiyin_command()
    if not command:
        _set_jiyin_error_and_save("极阴祖师选择无效")
        return True

    _schedule_jiyin_delayed_reply(
        command,
        reply_to_msg_id,
        now,
        parsed["timeout_sec"],
        choice=choice,
        choice_source=choice_source,
    )
    save_state()
    return True


async def handle_jiyin_delayed_action_result(result):
    if not isinstance(result, dict):
        return False
    if result.get("source_module") != JIYIN_DELAYED_SOURCE_MODULE or result.get("op_id") != JIYIN_DELAYED_OP_ID:
        return False

    reply_to_msg_id, _deadline, pending_dirty = _get_jiyin_pending_state()
    if pending_dirty:
        return True
    if reply_to_msg_id <= 0 or int(result.get("reply_to_msg_id") or 0) != reply_to_msg_id:
        return True

    status = str(result.get("status") or "")
    extra = result.get("extra") if isinstance(result.get("extra"), dict) else {}
    choice = normalize_jiyin_choice(extra.get("choice")) or resolve_jiyin_choice()[0]
    choice_source = str(extra.get("choice_source") or "auto")
    choice_source_text = "手动" if choice_source == "manual" else "自动"
    if status == "sent":
        await _finalize_jiyin_success(f"🌑 延迟自动选择：{get_jiyin_choice_label(choice)}（{choice_source_text}）")
        return True
    if status == "failed":
        state["jiyin_last_error"] = "极阴祖师延迟回复发送失败"
        save_state()
        await send_audit_log("❌ 极阴延迟回复失败，待处理已保留。")
        return True
    return True


async def run_jiyin_scheduler(now):
    if not state.get("jiyin_enabled"):
        return

    reply_to_msg_id, next_jiyin_time, pending_dirty = _get_jiyin_pending_state()
    if pending_dirty:
        return
    if reply_to_msg_id <= 0 or next_jiyin_time <= 0 or now < next_jiyin_time:
        return

    state["jiyin_last_error"] = "极阴祖师提示已超时"
    await send_audit_log(f"⚠️ 极阴抉择超时，消息ID={reply_to_msg_id}")
    clear_jiyin_state(persist=True, keep_last_error=True)


__all__ = [
    "JIYIN_CHOICE_HIDE_AURA",
    "JIYIN_CHOICE_OFFER_SOUL",
    "apply_jiyin_choice",
    "clear_jiyin_state",
    "get_jiyin_choice_command",
    "get_jiyin_choice_label",
    "get_jiyin_status_text",
    "handle_jiyin_delayed_action_result",
    "handle_jiyin_prompt",
    "normalize_jiyin_choice",
    "resolve_jiyin_choice",
    "run_jiyin_scheduler",
]
