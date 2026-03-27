import re
import time

from ..config import (
    CMD_JIYIN_HIDE_AURA,
    CMD_JIYIN_OFFER_SOUL,
    JIYIN_REPLY_TIMEOUT_SEC,
    RE_WHITESPACE,
)
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_identity_display_name,
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


def get_jiyin_status_text():
    saved_choice = normalize_jiyin_choice(get_jiyin_choice())
    effective_choice, choice_source = resolve_jiyin_choice()
    strategy_text = (
        f"已手动保存：{get_jiyin_choice_label(saved_choice)}"
        if saved_choice
        else "自动按境界"
    )
    return (
        "🌑 极阴祖师\n"
        f"- 当前策略：{strategy_text}\n"
        f"- 当前生效：{get_jiyin_choice_label(effective_choice)}（{'手动' if choice_source == 'manual' else '自动'}）\n"
        f"- 待回复消息ID：{int(state.get('jiyin_reply_to_msg_id', 0) or 0) or '无'}\n"
        f"- 截止时间：{fmt_abs_ts(state.get('next_jiyin_time', 0))}（{fmt_remaining(state.get('next_jiyin_time', 0))}）\n"
        f"- 最近错误：{state.get('jiyin_last_error') or '无'}"
    )


def clear_jiyin_state(*, persist=False, keep_last_error=False):
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

    pending_reply_to = int(state.get("jiyin_reply_to_msg_id", 0) or 0)
    pending_deadline = float(state.get("next_jiyin_time", 0) or 0)
    effective_choice, choice_source = resolve_jiyin_choice()
    if not state.get("jiyin_enabled") or pending_reply_to <= 0 or pending_deadline <= now:
        save_state()
        if reset_to_auto:
            return True, f"已恢复极阴祖师自动判断：{get_jiyin_choice_label(effective_choice)}"
        return True, f"已保存极阴祖师选择：{get_jiyin_choice_label(normalized_choice)}"

    command = get_jiyin_choice_command(effective_choice)
    sent_msg = await send_game_command(command, track=False, reply_to=pending_reply_to)
    if not sent_msg:
        state["jiyin_last_error"] = "极阴祖师选择发送失败"
        save_state()
        if reset_to_auto:
            return False, f"已恢复自动判断，但发送失败：{get_jiyin_choice_label(effective_choice)}"
        return False, f"已保存极阴祖师选择，但发送失败：{get_jiyin_choice_label(normalized_choice)}"

    await send_audit_log(
        f"🌑 极阴祖师[{get_identity_display_name()}] 已{'恢复自动判断' if reset_to_auto else '按手动保存选择'}回复：{get_jiyin_choice_label(effective_choice)}（{'手动保存' if choice_source == 'manual' else '按境界自动判断'}）。"
    )
    clear_jiyin_state(persist=True)
    if reset_to_auto:
        return True, f"已恢复极阴祖师自动判断并执行：{get_jiyin_choice_label(effective_choice)}"
    return True, f"已保存并执行极阴祖师选择：{get_jiyin_choice_label(normalized_choice)}"


async def handle_jiyin_prompt(text, now, event):
    if not state.get("jiyin_enabled"):
        return False

    parsed = _parse_jiyin_prompt(text)
    if not parsed:
        return False

    identity_id = _find_jiyin_identity_id(text)
    if identity_id is None or identity_id != get_current_identity_id():
        return False

    reply_to_msg_id = int(getattr(event, "id", 0) or 0)
    prev_reply_to_msg_id = int(state.get("jiyin_reply_to_msg_id", 0) or 0)
    prev_deadline = float(state.get("next_jiyin_time", 0) or 0)
    if prev_reply_to_msg_id > 0 and prev_reply_to_msg_id != reply_to_msg_id and prev_deadline > now:
        await send_audit_log(
            f"🌑 极阴祖师[{get_identity_display_name()}] 收到新的抉择事件，已覆盖旧待处理消息：{prev_reply_to_msg_id} -> {reply_to_msg_id}"
        )

    state["jiyin_reply_to_msg_id"] = reply_to_msg_id
    state["next_jiyin_time"] = now + float(parsed["timeout_sec"])
    state["jiyin_last_error"] = ""
    save_state()

    choice, choice_source = resolve_jiyin_choice()
    command = get_jiyin_choice_command(choice)
    if not command:
        state["jiyin_last_error"] = "极阴祖师选择无效"
        save_state()
        return True

    sent_msg = await send_game_command(command, track=False, reply_to=reply_to_msg_id)
    if not sent_msg:
        state["jiyin_last_error"] = "极阴祖师自动回复发送失败"
        save_state()
        await send_audit_log(
            f"❌ 极阴祖师[{get_identity_display_name()}] 自动回复发送失败，已保留待处理事件。"
        )
        return True

    await send_audit_log(
        f"🌑 极阴祖师[{get_identity_display_name()}] 已自动选择{get_jiyin_choice_label(choice)}（{'手动保存' if choice_source == 'manual' else '按境界自动判断'}）。"
    )
    clear_jiyin_state(persist=True)
    return True


async def run_jiyin_scheduler(now):
    if not state.get("jiyin_enabled"):
        return

    reply_to_msg_id = int(state.get("jiyin_reply_to_msg_id", 0) or 0)
    next_jiyin_time = float(state.get("next_jiyin_time", 0) or 0)
    if reply_to_msg_id <= 0 or next_jiyin_time <= 0 or now < next_jiyin_time:
        return

    state["jiyin_last_error"] = "极阴祖师提示已超时"
    await send_audit_log(
        f"⚠️ 极阴祖师[{get_identity_display_name()}] 抉择事件已超时，待回复消息ID：{reply_to_msg_id}。"
    )
    clear_jiyin_state(persist=True, keep_last_error=True)


__all__ = [
    "JIYIN_CHOICE_HIDE_AURA",
    "JIYIN_CHOICE_OFFER_SOUL",
    "apply_jiyin_choice",
    "clear_jiyin_state",
    "get_jiyin_choice_command",
    "get_jiyin_choice_label",
    "get_jiyin_status_text",
    "handle_jiyin_prompt",
    "normalize_jiyin_choice",
    "resolve_jiyin_choice",
    "run_jiyin_scheduler",
]
