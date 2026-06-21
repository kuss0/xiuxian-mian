import math
import random
import re
import time

from ..config import (
    CMD_CONCUBINE_PLACE,
    CMD_CONCUBINE_RECALL,
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
    get_tianjige_dao_path_records,
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
NANLONG_PROTECT_PLACE_PENDING = "place_pending"
NANLONG_PROTECT_EXCHANGE_PENDING = "exchange_pending"
NANLONG_PROTECT_RECALL_PENDING = "recall_pending"
NANLONG_PROTECT_PHASES = {
    NANLONG_PROTECT_PLACE_PENDING,
    NANLONG_PROTECT_EXCHANGE_PENDING,
    NANLONG_PROTECT_RECALL_PENDING,
}
NANLONG_PLACE_FAILURE_KEYWORDS = ("无法安置", "不能安置", "暂无道侣", "没有道侣", "尚无道侣", "未拥有洞府", "尚未开辟洞府")
NANLONG_RECALL_FAILURE_KEYWORDS = ("无法召回", "无需召回", "藏娇阁中暂无", "尚无红颜")
NANLONG_CAVE_STATUS_AVAILABLE = "available"
NANLONG_CAVE_STATUS_EMPTY = "empty"
NANLONG_CAVE_STATUS_UNKNOWN = "unknown"


def _normalize_text(text):
    return RE_WHITESPACE.sub("", text or "").strip().lower()


def _parse_nanlong_pending_int(value):
    if value is None:
        return 0, True
    if isinstance(value, str) and not value.strip():
        return 0, True
    try:
        return int(value), True
    except (TypeError, ValueError, OverflowError):
        return 0, False


def _parse_nanlong_pending_float(value):
    if value is None:
        return 0.0, True
    if isinstance(value, str) and not value.strip():
        return 0.0, True
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0, False
    if not math.isfinite(parsed):
        return 0.0, False
    return parsed, True


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
    reply_to_msg_id, reply_to_valid = _parse_nanlong_pending_int(state.get("nanlong_reply_to_msg_id", 0))
    deadline, deadline_valid = _parse_nanlong_pending_float(state.get("next_nanlong_time", 0))
    reply_due_at, reply_due_valid = _parse_nanlong_pending_float(state.get("nanlong_reply_due_at", 0))
    return (
        reply_to_msg_id,
        deadline,
        reply_due_at,
        reply_to_valid and deadline_valid and reply_due_valid,
    )


def _has_nanlong_inflight_state():
    for key in ("nanlong_last_msg_id", "nanlong_place_msg_id", "nanlong_recall_msg_id"):
        value, valid = _parse_nanlong_pending_int(state.get(key, 0))
        if valid and value > 0:
            return True
    return bool(_get_nanlong_protect_phase())


def _is_nanlong_success_reply(text):
    return any(keyword in str(text or "") for keyword in NANLONG_SUCCESS_KEYWORDS)


def _is_nanlong_trade_success_reply(text):
    return "【天机异闻·南陇侯的交易】" in str(text or "")


def _is_concubine_place_success(text):
    raw_text = str(text or "")
    return "藏娇阁" in raw_text and "安置" in raw_text and ("已将" in raw_text or "已经" in raw_text)


def _is_concubine_place_failure(text):
    raw_text = str(text or "")
    return any(keyword in raw_text for keyword in NANLONG_PLACE_FAILURE_KEYWORDS)


def _is_concubine_recall_success(text):
    raw_text = str(text or "")
    return "藏娇阁" in raw_text and "召回" in raw_text and ("已将" in raw_text or "已经" in raw_text)


def _is_concubine_recall_failure(text):
    raw_text = str(text or "")
    return any(keyword in raw_text for keyword in NANLONG_RECALL_FAILURE_KEYWORDS)


def _get_nanlong_protect_phase():
    phase = str(state.get("nanlong_protect_phase") or "").strip()
    return phase if phase in NANLONG_PROTECT_PHASES else ""


def _reply_to_msg_id(reply_to):
    try:
        return int(getattr(reply_to, "id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _is_reply_to_nanlong_last_msg(reply_to):
    expected_msg_id, valid = _parse_nanlong_pending_int(state.get("nanlong_last_msg_id", 0))
    return valid and expected_msg_id > 0 and _reply_to_msg_id(reply_to) == expected_msg_id


def _nanlong_exchange_requires_protection(choice):
    return normalize_nanlong_choice(choice) in NANLONG_CONFIRM_CHOICES


def _get_nanlong_cave_status(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    try:
        identity_id = int(send_as_id or 0)
    except (TypeError, ValueError, OverflowError):
        identity_id = 0
    if identity_id <= 0:
        return NANLONG_CAVE_STATUS_UNKNOWN

    records = get_tianjige_dao_path_records()
    if not isinstance(records, dict):
        return NANLONG_CAVE_STATUS_UNKNOWN
    record = records.get(str(identity_id)) or records.get(identity_id)
    if not isinstance(record, dict):
        return NANLONG_CAVE_STATUS_UNKNOWN
    cave = record.get("cave")
    if isinstance(cave, dict):
        return NANLONG_CAVE_STATUS_AVAILABLE if cave else NANLONG_CAVE_STATUS_EMPTY
    return NANLONG_CAVE_STATUS_UNKNOWN


def _get_nanlong_cave_status_label(cave_status):
    if cave_status == NANLONG_CAVE_STATUS_EMPTY:
        return "本地洞府为空"
    if cave_status == NANLONG_CAVE_STATUS_UNKNOWN:
        return "未读到本地洞府缓存"
    return "本地洞府可用"


def is_nanlong_protected_trade_active(now=None):
    if not state.get("nanlong_enabled"):
        return False
    if now is None:
        now = time.time()
    phase = _get_nanlong_protect_phase()
    if phase == NANLONG_PROTECT_RECALL_PENDING:
        _reply_to_msg_id, _deadline, reply_due_at, pending_valid = _get_nanlong_pending_state()
        return pending_valid and (reply_due_at <= 0 or reply_due_at + NANLONG_CONFIRM_RETRY_DELAY_SEC > now)
    if not _has_active_nanlong_pending(now):
        return False
    return phase in {NANLONG_PROTECT_PLACE_PENDING, NANLONG_PROTECT_EXCHANGE_PENDING, NANLONG_PROTECT_RECALL_PENDING}


def _has_active_nanlong_pending(now):
    reply_to_msg_id, deadline, _reply_due_at, pending_valid = _get_nanlong_pending_state()
    if not pending_valid:
        return False
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
    state["nanlong_protect_phase"] = ""
    state["nanlong_place_msg_id"] = 0
    state["nanlong_recall_msg_id"] = 0
    state["nanlong_last_error"] = ""
    _schedule_nanlong_reply_due(now)


def _set_nanlong_error_and_save(message):
    state["nanlong_last_error"] = message
    save_state()


def _clear_nanlong_prompt_anchor():
    state["nanlong_reply_to_msg_id"] = 0
    state["next_nanlong_time"] = 0


async def _send_nanlong_command(command, reply_to_msg_id):
    return await send_game_command(command, track=False, reply_to=reply_to_msg_id)


async def _send_nanlong_place_command():
    return await send_game_command(CMD_CONCUBINE_PLACE, track=False)


async def _send_nanlong_recall_command():
    return await send_game_command(CMD_CONCUBINE_RECALL, track=False)


async def _maybe_audit_nanlong_prompt_override(previous_reply_to, previous_deadline, now, new_reply_to):
    if previous_reply_to > 0 and previous_reply_to != new_reply_to and previous_deadline > now:
        await send_audit_log(f"🤝 南陇侯新抉择覆盖旧消息：{previous_reply_to}->{new_reply_to}")


async def _finalize_nanlong_success(audit_text):
    await send_audit_log(audit_text)
    clear_nanlong_state(persist=True)


def _set_nanlong_waiting_for_place(sent_msg):
    sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time())
    msg_id = int(getattr(sent_msg, "id", 0) or 0)
    state["nanlong_protect_phase"] = NANLONG_PROTECT_PLACE_PENDING
    state["nanlong_place_msg_id"] = msg_id
    state["nanlong_last_msg_id"] = msg_id
    state["nanlong_last_command"] = CMD_CONCUBINE_PLACE
    state["nanlong_retry_count"] = 0
    state["nanlong_reply_due_at"] = sent_at + NANLONG_CONFIRM_RETRY_DELAY_SEC
    state["nanlong_last_error"] = "等待侍妾安置确认"
    return sent_at


def _set_nanlong_waiting_for_exchange(sent_msg, command, *, retry_count=0, protected=False):
    sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time())
    state["nanlong_protect_phase"] = NANLONG_PROTECT_EXCHANGE_PENDING if protected and command in {CMD_NANLONG_EXCHANGE_FABAO, CMD_NANLONG_EXCHANGE_GONGFA} else ""
    state["nanlong_last_msg_id"] = int(getattr(sent_msg, "id", 0) or 0)
    state["nanlong_last_command"] = command
    state["nanlong_retry_count"] = max(0, int(retry_count or 0))
    state["nanlong_reply_due_at"] = sent_at + NANLONG_CONFIRM_RETRY_DELAY_SEC
    state["nanlong_last_error"] = "等待南陇侯交易结果"
    return sent_at


def _set_nanlong_waiting_for_recall(sent_msg, *, retry_count=0):
    sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time())
    msg_id = int(getattr(sent_msg, "id", 0) or 0)
    state["nanlong_protect_phase"] = NANLONG_PROTECT_RECALL_PENDING
    state["nanlong_recall_msg_id"] = msg_id
    state["nanlong_last_msg_id"] = msg_id
    state["nanlong_last_command"] = CMD_CONCUBINE_RECALL
    state["nanlong_retry_count"] = max(0, int(retry_count or 0))
    state["nanlong_reply_due_at"] = sent_at + NANLONG_CONFIRM_RETRY_DELAY_SEC
    state["nanlong_last_error"] = "等待侍妾召回确认"
    return sent_at


async def _send_nanlong_exchange_after_place(now):
    reply_to_msg_id, next_nanlong_time, _reply_due_at, pending_valid = _get_nanlong_pending_state()
    if not pending_valid or reply_to_msg_id <= 0 or next_nanlong_time <= now:
        state["nanlong_last_error"] = "南陇侯安置后原提示已失效"
        await send_audit_log("⚠️ 南陇侯安置成功，但原抉择提示已失效，停止自动交换。")
        clear_nanlong_state(persist=True, keep_last_error=True)
        return False

    choice = normalize_nanlong_choice(get_nanlong_choice())
    command = get_nanlong_choice_command(choice)
    sent_msg = await _send_nanlong_command(command, reply_to_msg_id)
    if not sent_msg:
        state["nanlong_last_error"] = "南陇侯安置后交换发送失败"
        _schedule_nanlong_reply_due(now)
        save_state()
        await send_audit_log("❌ 南陇侯安置成功，但交换发送失败，待处理已保留。")
        return False
    _set_nanlong_waiting_for_exchange(sent_msg, command, protected=True)
    save_state()
    return True


async def _send_nanlong_exchange_command(command, reply_to_msg_id, now, *, retry_count=0, audit_retry=False, protected=False):
    sent_msg = await _send_nanlong_command(command, reply_to_msg_id)
    sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time()) if sent_msg else time.time()
    if not sent_msg:
        state["nanlong_last_error"] = "南陇侯自动回复发送失败"
        _schedule_nanlong_reply_due(sent_at)
        save_state()
        await send_audit_log("❌ 南陇侯自动回复失败，待处理已保留。")
        return False
    _set_nanlong_waiting_for_exchange(sent_msg, command, retry_count=retry_count, protected=protected)
    save_state()
    if audit_retry:
        await send_audit_log(f"🤝 南陇侯自动选择重发 {retry_count}/{NANLONG_CONFIRM_RETRY_LIMIT}：{command}，等待确认")
    return True


async def _send_nanlong_recall_after_trade(now, *, retry_count=0):
    _clear_nanlong_prompt_anchor()
    state["nanlong_protect_phase"] = NANLONG_PROTECT_RECALL_PENDING
    state["nanlong_last_command"] = CMD_CONCUBINE_RECALL
    sent_msg = await _send_nanlong_recall_command()
    if not sent_msg:
        state["nanlong_last_error"] = "南陇侯交易已确认但召回发送失败"
        _schedule_nanlong_reply_due(now)
        save_state()
        await send_audit_log("⚠️ 南陇侯交易已确认，但侍妾召回发送失败，待处理已保留。")
        return False
    _set_nanlong_waiting_for_recall(sent_msg, retry_count=retry_count)
    save_state()
    if retry_count:
        await send_audit_log(f"🤝 南陇侯侍妾召回重发 {retry_count}/{NANLONG_CONFIRM_RETRY_LIMIT}，等待确认。")
    else:
        await send_audit_log("🤝 南陇侯交易结果已确认，已发送侍妾召回。")
    return True


async def _handle_nanlong_trade_confirmed(text, now, audit_text):
    if _get_nanlong_protect_phase() == NANLONG_PROTECT_EXCHANGE_PENDING and _is_nanlong_trade_success_reply(text):
        return await _send_nanlong_recall_after_trade(now)
    await _finalize_nanlong_success(audit_text)
    return True


def get_nanlong_status_text():
    choice = normalize_nanlong_choice(get_nanlong_choice())
    reply_to_msg_id, deadline, reply_due_at, _pending_valid = _get_nanlong_pending_state()
    phase = _get_nanlong_protect_phase() or "无"
    lines = [
        "🤝 南陇侯",
        f"- 当前选择：{get_nanlong_choice_label(choice)}",
        f"- 洞府保护阶段：{phase}",
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
    state["nanlong_protect_phase"] = ""
    state["nanlong_place_msg_id"] = 0
    state["nanlong_recall_msg_id"] = 0
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

    _pending_reply_to, _deadline, reply_due_at, pending_valid = _get_nanlong_pending_state()
    if not pending_valid:
        save_state()
        return True, f"已保存南陇侯选择：{get_nanlong_choice_label(normalized_choice)}，待回复状态异常，未自动回复"

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
    prev_reply_to_msg_id, prev_deadline, _prev_due_at, prev_pending_valid = _get_nanlong_pending_state()
    if not prev_pending_valid:
        prev_reply_to_msg_id = 0
        prev_deadline = 0
    await _maybe_audit_nanlong_prompt_override(prev_reply_to_msg_id, prev_deadline, now, reply_to_msg_id)

    _set_nanlong_pending(reply_to_msg_id, now + float(parsed["timeout_sec"]), now)
    save_state()
    return True


async def run_nanlong_scheduler(now):
    if not state.get("nanlong_enabled"):
        return

    reply_to_msg_id, next_nanlong_time, reply_due_at, pending_valid = _get_nanlong_pending_state()
    if not pending_valid:
        return
    phase = _get_nanlong_protect_phase()
    if phase == NANLONG_PROTECT_RECALL_PENDING:
        if reply_due_at <= 0 or now < reply_due_at:
            return
        retry_count = int(state.get("nanlong_retry_count", 0) or 0)
        if retry_count >= NANLONG_CONFIRM_RETRY_LIMIT:
            state["nanlong_last_error"] = "南陇侯交易完成但侍妾召回未确认"
            await send_audit_log("⚠️ 南陇侯交易完成但侍妾召回未确认，已停止自动处理，请人工核对。")
            clear_nanlong_state(persist=True, keep_last_error=True)
            return
        await _send_nanlong_recall_after_trade(now, retry_count=retry_count + 1)
        return
    if reply_to_msg_id <= 0 or next_nanlong_time <= 0:
        if _has_nanlong_inflight_state():
            state["nanlong_last_error"] = "南陇侯待处理状态不完整，已清理"
            clear_nanlong_state(persist=True, keep_last_error=True)
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

    if phase == NANLONG_PROTECT_PLACE_PENDING:
        state["nanlong_protect_phase"] = ""
        state["nanlong_place_msg_id"] = 0
        state["nanlong_last_msg_id"] = 0
        state["nanlong_retry_count"] = 0
        await send_audit_log("⚠️ 南陇侯侍妾安置未确认，降级直接回复南陇侯。")
        await _send_nanlong_exchange_command(command, reply_to_msg_id, now, protected=False)
        return

    is_confirmation_retry = bool(state.get("nanlong_last_msg_id"))
    if requires_confirmation and is_confirmation_retry and int(state.get("nanlong_retry_count", 0) or 0) >= NANLONG_CONFIRM_RETRY_LIMIT:
        state["nanlong_last_error"] = "南陇侯交易结果未确认"
        if phase == NANLONG_PROTECT_EXCHANGE_PENDING:
            await send_audit_log("⚠️ 南陇侯交易结果未确认，先尝试召回洞府侍妾后停止。")
            await _send_nanlong_recall_after_trade(now)
            return
        await send_audit_log(f"⚠️ 南陇侯自动回复重发 {NANLONG_CONFIRM_RETRY_LIMIT} 次仍未确认，已停止。")
        clear_nanlong_state(persist=True, keep_last_error=True)
        return

    if requires_confirmation and not is_confirmation_retry and _nanlong_exchange_requires_protection(choice):
        cave_status = _get_nanlong_cave_status()
        if cave_status != NANLONG_CAVE_STATUS_AVAILABLE:
            await send_audit_log(f"🤝 南陇侯{_get_nanlong_cave_status_label(cave_status)}，跳过安置，直接交易后交由侍妾补领链路处理。")
            await _send_nanlong_exchange_command(command, reply_to_msg_id, now, protected=False)
            return
        sent_msg = await _send_nanlong_place_command()
        sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time()) if sent_msg else time.time()
        if not sent_msg:
            state["nanlong_last_error"] = "南陇侯侍妾安置发送失败"
            _schedule_nanlong_reply_due(sent_at)
            save_state()
            await send_audit_log("❌ 南陇侯侍妾安置发送失败，待处理已保留。")
            return
        _set_nanlong_waiting_for_place(sent_msg)
        save_state()
        return

    if not requires_confirmation:
        sent_msg = await _send_nanlong_command(command, reply_to_msg_id)
        sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time()) if sent_msg else time.time()
        if not sent_msg:
            state["nanlong_last_error"] = "南陇侯自动回复发送失败"
            _schedule_nanlong_reply_due(sent_at)
            save_state()
            await send_audit_log("❌ 南陇侯自动回复失败，待处理已保留。")
            return
        await _finalize_nanlong_success(f"🤝 南陇侯自动选择：{get_nanlong_choice_label(choice)}")
        return

    retry_count = int(state.get("nanlong_retry_count", 0) or 0) + 1 if is_confirmation_retry else 0
    await _send_nanlong_exchange_command(
        command,
        reply_to_msg_id,
        now,
        retry_count=retry_count,
        audit_retry=is_confirmation_retry,
        protected=phase == NANLONG_PROTECT_EXCHANGE_PENDING,
    )


async def handle_nanlong_reply(text, now, reply_to, matched_family=None):
    if not state.get("nanlong_enabled"):
        return False
    if matched_family != "nanlong":
        return False
    if not state.get("nanlong_last_msg_id"):
        return False
    if not _is_reply_to_nanlong_last_msg(reply_to):
        return False
    phase = _get_nanlong_protect_phase()
    if phase == NANLONG_PROTECT_PLACE_PENDING:
        if _is_concubine_place_success(text):
            await _send_nanlong_exchange_after_place(now)
            return True
        if _is_concubine_place_failure(text):
            state["nanlong_protect_phase"] = ""
            state["nanlong_place_msg_id"] = 0
            state["nanlong_last_msg_id"] = 0
            state["nanlong_retry_count"] = 0
            state["nanlong_last_error"] = "侍妾安置失败，降级直接交换"
            save_state()
            await send_audit_log("⚠️ 南陇侯侍妾安置失败，降级直接回复南陇侯。")
            await _send_nanlong_exchange_command(get_nanlong_choice_command(get_nanlong_choice()), state.get("nanlong_reply_to_msg_id", 0), now)
            return True
        return True
    if phase == NANLONG_PROTECT_RECALL_PENDING:
        if _is_concubine_recall_success(text):
            await _finalize_nanlong_success("🤝 南陇侯交易完成，侍妾已召回")
            return True
        if _is_concubine_recall_failure(text):
            state["nanlong_last_error"] = "南陇侯交易完成但侍妾召回失败"
            await send_audit_log("⚠️ 南陇侯交易完成但侍妾召回失败，请人工核对。")
            clear_nanlong_state(persist=True, keep_last_error=True)
            return True
        return True
    if not _is_nanlong_success_reply(text):
        return True

    await _handle_nanlong_trade_confirmed(text, now, "🤝 南陇侯交易结果已确认")
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

    await _handle_nanlong_trade_confirmed(text, now, "🤝 南陇侯交易结果已确认")
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
    "is_nanlong_protected_trade_active",
    "normalize_nanlong_choice",
    "resolve_nanlong_choice",
    "run_nanlong_scheduler",
]
