import math
import random
import re
import time
from types import SimpleNamespace

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
from ..message_log_recovery import iter_message_log_entries_between
from ..message_keys import message_key_parts
from ..module_manifest import get_module_name_for_reply_family
from ..persistence import mark_dirty, save_state
from ..runtime import _is_logged_game_bot_reply, classify_game_send_block, clear_pending_by_reply, get_sent_message_chat_id, send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_game_group_ids,
    get_game_group_topic_id,
    get_identity_enabled,
    get_identity_account,
    get_identity_ids,
    get_identity_state,
    get_nanlong_choice,
    get_send_as_tags,
    get_tianjige_dao_path_records,
    has_identity,
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
RE_NANLONG_REWARD = re.compile(r"南陇侯[^\n。！？]*?赐[^\n。！？]*?【([^】]+)】")
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
NANLONG_LOG_REPLAY_LOOKBACK_SEC = 15 * 60
NANLONG_LOG_REPLAY_LOOKAHEAD_SEC = 30
_NANLONG_OPERATION_KEYS = (
    "nanlong_reply_to_msg_id", "nanlong_reply_chat_id", "next_nanlong_time",
    "nanlong_reply_due_at", "nanlong_last_msg_id", "nanlong_last_chat_id",
    "nanlong_last_sent_at", "nanlong_last_command", "nanlong_protect_phase",
    "nanlong_retry_count", "nanlong_last_prompt_key",
)


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
    if RE_NANLONG_TARGET_TAG.search(text or ""):
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


def _extract_nanlong_reward_names(text):
    rewards = []
    seen = set()
    for matched in RE_NANLONG_REWARD.findall(str(text or "")):
        reward = str(matched or "").strip()
        if not reward or reward in seen:
            continue
        seen.add(reward)
        rewards.append(reward)
    return rewards


async def _send_nanlong_reward_audit(text):
    rewards = _extract_nanlong_reward_names(text)
    if not rewards:
        return
    await send_audit_log(
        f"🎁 南陇侯赏赐：{'、'.join(rewards)}",
        scope="identity",
        priority="high",
        limit=260,
    )


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


def _is_nanlong_recovery_log_entry(entry):
    if not isinstance(entry, dict) or entry.get("event_type") not in {"message", "edit"}:
        return False
    try:
        if int(entry.get("message_id") or 0) <= 0 or not int(entry.get("chat_id") or 0):
            return False
        int(entry.get("reply_to_msg_id") or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    if not _is_logged_game_bot_reply(entry):
        return False
    raw_text = str((entry or {}).get("text") or "").strip()
    if not raw_text:
        return False
    return (
        _is_nanlong_success_reply(raw_text)
        or _is_concubine_place_success(raw_text)
        or _is_concubine_place_failure(raw_text)
        or _is_concubine_recall_success(raw_text)
        or _is_concubine_recall_failure(raw_text)
    )


def _get_nanlong_protect_phase():
    phase = str(state.get("nanlong_protect_phase") or "").strip()
    return phase if phase in NANLONG_PROTECT_PHASES else ""


def _capture_nanlong_operation():
    identity_id = get_current_identity_id()
    identity = get_identity_state(identity_id)
    return identity_id, identity, tuple(identity.get(key) for key in _NANLONG_OPERATION_KEYS), get_nanlong_choice(identity_id), get_identity_account(identity_id)


def _nanlong_operation_is_current(expected, *, check_choice=True):
    identity_id, identity, values, choice, account_id = expected
    return (
        has_identity(identity_id)
        and get_identity_state(identity_id) is identity
        and get_identity_account(identity_id) == account_id
        and get_identity_enabled(identity_id)
        and bool(identity.get("nanlong_enabled"))
        and values == tuple(identity.get(key) for key in _NANLONG_OPERATION_KEYS)
        and (not check_choice or choice == get_nanlong_choice(identity_id))
    )


def _nanlong_event_time(event, now, *, edited=False):
    timestamp = (getattr(event, "edit_date", None) if edited else None) or getattr(event, "date", None)
    try:
        value = float(timestamp.timestamp())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return float(now)
    return value if math.isfinite(value) else float(now)


def _get_nanlong_last_chat_id():
    chat_id, valid = _parse_nanlong_pending_int(state.get("nanlong_last_chat_id", 0))
    if not valid:
        return 0
    if chat_id:
        return chat_id
    msg_id, valid = _parse_nanlong_pending_int(state.get("nanlong_last_msg_id", 0))
    return get_sent_message_chat_id(msg_id, default=0, send_as_id=get_current_identity_id()) if valid and msg_id > 0 else 0


def _reply_to_msg_id(reply_to):
    try:
        return int(getattr(reply_to, "id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _is_reply_to_nanlong_last_msg(reply_to):
    expected_msg_id, valid = _parse_nanlong_pending_int(state.get("nanlong_last_msg_id", 0))
    chat_id = _get_nanlong_last_chat_id()
    return (
        valid and expected_msg_id > 0 and bool(chat_id)
        and _reply_to_msg_id(reply_to) == expected_msg_id
        and int(getattr(reply_to, "chat_id", 0) or 0) == chat_id
    )


def _nanlong_exchange_requires_protection(choice):
    partner_name = str(state.get("concubine_name") or "").strip()
    permanent_moon_partner = partner_name == "南宫婉" or partner_name.startswith("南宫婉·")
    return not permanent_moon_partner and normalize_nanlong_choice(choice) in NANLONG_CONFIRM_CHOICES


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


def _set_nanlong_pending(reply_to_msg_id, deadline_at, now, *, chat_id=0, prompt_at=None):
    state["nanlong_reply_to_msg_id"] = int(reply_to_msg_id or 0)
    state["nanlong_reply_chat_id"] = int(chat_id or 0)
    state["nanlong_prompt_at"] = float(now if prompt_at is None else prompt_at)
    state["nanlong_last_prompt_key"] = f"{chat_id}:{reply_to_msg_id}" if chat_id and reply_to_msg_id else ""
    state["next_nanlong_time"] = float(deadline_at or 0)
    state["nanlong_last_msg_id"] = 0
    state["nanlong_last_chat_id"] = 0
    state["nanlong_last_sent_at"] = 0
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
    state["nanlong_reply_chat_id"] = 0
    state["next_nanlong_time"] = 0


def _nanlong_send_is_unresolved():
    return (
        bool(state.get("nanlong_last_command"))
        and not state.get("nanlong_last_msg_id")
        and not state.get("nanlong_reply_due_at")
    )


def _prepare_nanlong_send(command, now, *, protected=False, retry_count=0):
    if _nanlong_send_is_unresolved():
        return False
    chat_id, valid = (
        (_get_nanlong_last_chat_id(), True) if command == CMD_CONCUBINE_RECALL
        else _parse_nanlong_pending_int(state.get("nanlong_reply_chat_id"))
    )
    if not valid or not chat_id:
        return False
    before = {key: state.get(key) for key in _NANLONG_OPERATION_KEYS}
    state["nanlong_reply_due_at"] = 0
    state["nanlong_last_msg_id"] = 0
    # Identify the local attempt while the send is unresolved. A confirmed
    # receipt replaces this bound with dispatch time; this is not sent evidence.
    state["nanlong_last_sent_at"] = time.time()
    state["nanlong_last_chat_id"] = chat_id
    state["nanlong_last_command"] = command
    state["nanlong_retry_count"] = max(0, int(retry_count or 0))
    state["nanlong_protect_phase"] = (
        NANLONG_PROTECT_PLACE_PENDING if command == CMD_CONCUBINE_PLACE else
        NANLONG_PROTECT_RECALL_PENDING if command == CMD_CONCUBINE_RECALL else
        NANLONG_PROTECT_EXCHANGE_PENDING if protected else ""
    )
    state["nanlong_last_error"] = "等待南陇侯链路发送回执"
    if save_state() is False:
        for key, value in before.items():
            state[key] = value
        state["nanlong_reply_due_at"] = now + NANLONG_CONFIRM_RETRY_DELAY_SEC
        state["nanlong_last_error"] = "南陇侯发送前状态保存失败，未发送"
        mark_dirty()
        return False
    return before


def _nanlong_send_intent():
    identity_id = get_current_identity_id()
    prompt_key = str(state.get("nanlong_last_prompt_key") or "")
    started_at, valid = _parse_nanlong_pending_float(state.get("nanlong_last_sent_at"))
    retry_count, retry_valid = _parse_nanlong_pending_int(state.get("nanlong_retry_count"))
    if not prompt_key or not valid or started_at <= 0 or not retry_valid:
        return {}
    chain_id = f"nanlong:{identity_id}:{get_identity_account(identity_id)}:{prompt_key}"
    return {
        "source_module": get_module_name_for_reply_family("nanlong"), "chain_id": chain_id,
        "op_id": f"{chain_id}:{state.get('nanlong_last_command')}:{retry_count}:{started_at}",
    }


def _nanlong_detached_receipts(now):
    intent = _nanlong_send_intent()
    if not intent.get("op_id"):
        return []
    command = str(state.get("nanlong_last_command") or "")
    started_at, valid = _parse_nanlong_pending_float(state.get("nanlong_last_sent_at"))
    chat_id = _get_nanlong_last_chat_id()
    last_msg_id, msg_valid = _parse_nanlong_pending_int(state.get("nanlong_last_msg_id"))
    if not valid or not msg_valid or not chat_id:
        return []
    matches = []
    for key, item in state.get("pending_tasks", {}).items():
        if not isinstance(item, dict) or item.get("send_caller_detached") is not True:
            continue
        # After adoption, the exact chat/message owns the receipt, and the
        # time bound can have advanced from enqueue time to actual dispatch.
        if item.get("cmd") != command or any(
            item.get(key) != value for key, value in intent.items()
            if key != "op_id" or not last_msg_id
        ):
            continue
        try:
            item_chat, item_id = message_key_parts(key, item)
            reply_to = int(item.get("reply_to_msg_id") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        sent_at, sent_valid = _parse_nanlong_pending_float(item.get("sent_at"))
        dispatched_at, dispatch_valid = _parse_nanlong_pending_float(item.get("send_started_at"))
        if item_chat != chat_id or (last_msg_id and item_id != last_msg_id):
            continue
        if not sent_valid or not started_at <= sent_at <= now + NANLONG_LOG_REPLAY_LOOKAHEAD_SEC:
            continue
        if not dispatch_valid or (dispatched_at and not started_at <= dispatched_at <= sent_at):
            continue
        if command in NANLONG_CHOICE_COMMANDS.values() and reply_to != int(state.get("nanlong_reply_to_msg_id") or 0):
            continue
        matches.append(SimpleNamespace(id=item_id, chat_id=item_chat, sent_at=sent_at, send_started_at=dispatched_at))
    return matches


def _adopt_nanlong_detached_receipt(now):
    if not _nanlong_send_is_unresolved() or not _nanlong_operation_is_current(_capture_nanlong_operation()):
        return False
    matches = _nanlong_detached_receipts(now)
    if len(matches) != 1:
        return False
    command = state.get("nanlong_last_command")
    retry_count = int(state.get("nanlong_retry_count") or 0)
    if command == CMD_CONCUBINE_PLACE:
        _set_nanlong_waiting_for_place(matches[0])
    elif command == CMD_CONCUBINE_RECALL:
        _set_nanlong_waiting_for_recall(matches[0], retry_count=retry_count)
    elif command in NANLONG_CHOICE_COMMANDS.values():
        _set_nanlong_waiting_for_exchange(
            matches[0], command, retry_count=retry_count,
            protected=_get_nanlong_protect_phase() == NANLONG_PROTECT_EXCHANGE_PENDING,
        )
    else:
        return False
    save_state()
    return True


async def _handle_nanlong_send_failure(command, now, label, previous):
    block = classify_game_send_block(send_as_id=get_current_identity_id(), command=command)
    if block.get("status") == "unsent":
        for key, value in previous.items():
            state[key] = value
        _schedule_nanlong_reply_due(now)
        state["nanlong_last_error"] = f"{label}未发送，待处理已保留"
    else:
        state["nanlong_reply_due_at"] = 0
        state["nanlong_last_error"] = f"{label}发送状态未知，等待回包或人工核对"
    save_state()
    await send_audit_log(f"⚠️ {state['nanlong_last_error']}。")
    return False


def _nanlong_send_check(command):
    expected = _capture_nanlong_operation()
    deadline, valid = _parse_nanlong_pending_float(state.get("next_nanlong_time"))

    def is_current():
        recall = command == CMD_CONCUBINE_RECALL
        return (
            _nanlong_operation_is_current(expected, check_choice=not recall)
            and (recall or (valid and time.time() < deadline))
        )

    return is_current


async def _send_nanlong_command(command, reply_to_msg_id):
    chat_id, valid = _parse_nanlong_pending_int(state.get("nanlong_reply_chat_id", 0))
    if not valid or not chat_id or int(state.get("nanlong_reply_to_msg_id") or 0) != reply_to_msg_id:
        return None
    if not state.get("nanlong_enabled"):
        return None
    return await send_game_command(
        command, track=False, reply_to=reply_to_msg_id, target_chat_id=chat_id,
        send_as_id=get_current_identity_id(),
        operation_check=_nanlong_send_check(command),
        **_nanlong_send_intent(),
    )


async def _send_nanlong_place_command():
    chat_id, valid = _parse_nanlong_pending_int(state.get("nanlong_reply_chat_id", 0))
    if not valid or not chat_id or not state.get("nanlong_enabled"):
        return None
    return await send_game_command(
        CMD_CONCUBINE_PLACE, track=False, target_chat_id=chat_id, send_as_id=get_current_identity_id(),
        operation_check=_nanlong_send_check(CMD_CONCUBINE_PLACE), **_nanlong_send_intent(),
    )


async def _send_nanlong_recall_command():
    chat_id = _get_nanlong_last_chat_id()
    if not chat_id or not state.get("nanlong_enabled"):
        return None
    return await send_game_command(
        CMD_CONCUBINE_RECALL, track=False, target_chat_id=chat_id, send_as_id=get_current_identity_id(),
        operation_check=_nanlong_send_check(CMD_CONCUBINE_RECALL), **_nanlong_send_intent(),
    )


async def _maybe_audit_nanlong_prompt_override(previous_reply_to, previous_deadline, now, new_reply_to):
    if previous_reply_to > 0 and previous_reply_to != new_reply_to and previous_deadline > now:
        await send_audit_log(f"🤝 南陇侯新抉择覆盖旧消息：{previous_reply_to}->{new_reply_to}")


def _clear_nanlong_reply_pending():
    msg_id, valid = _parse_nanlong_pending_int(state.get("nanlong_last_msg_id", 0))
    chat_id = _get_nanlong_last_chat_id()
    if valid and msg_id > 0 and chat_id:
        identity_id = get_current_identity_id()
        clear_pending_by_reply(send_as_id=identity_id, reply_context={
            "send_as_id": identity_id, "family": "nanlong", "chat_id": chat_id,
            "root_msg_id": msg_id, "reply_to_msg_id": msg_id,
        })


async def _finalize_nanlong_success(audit_text):
    _clear_nanlong_reply_pending()
    clear_nanlong_state(persist=True)
    await send_audit_log(audit_text)


def _nanlong_dispatch_time_bound(sent_msg, sent_at):
    started_at, valid = _parse_nanlong_pending_float(getattr(sent_msg, "send_started_at", 0))
    return started_at if valid and 0 < started_at <= sent_at else sent_at


def _set_nanlong_waiting_for_place(sent_msg):
    sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time())
    msg_id = int(getattr(sent_msg, "id", 0) or 0)
    state["nanlong_protect_phase"] = NANLONG_PROTECT_PLACE_PENDING
    state["nanlong_place_msg_id"] = msg_id
    state["nanlong_last_msg_id"] = msg_id
    state["nanlong_last_chat_id"] = int(getattr(sent_msg, "chat_id", 0) or state.get("nanlong_reply_chat_id") or 0)
    state["nanlong_last_sent_at"] = _nanlong_dispatch_time_bound(sent_msg, sent_at)
    state["nanlong_last_command"] = CMD_CONCUBINE_PLACE
    state["nanlong_retry_count"] = 0
    state["nanlong_reply_due_at"] = sent_at + NANLONG_CONFIRM_RETRY_DELAY_SEC
    state["nanlong_last_error"] = "等待侍妾安置确认"
    return sent_at


def _set_nanlong_waiting_for_exchange(sent_msg, command, *, retry_count=0, protected=False):
    sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time())
    state["nanlong_protect_phase"] = NANLONG_PROTECT_EXCHANGE_PENDING if protected and command in {CMD_NANLONG_EXCHANGE_FABAO, CMD_NANLONG_EXCHANGE_GONGFA} else ""
    state["nanlong_last_msg_id"] = int(getattr(sent_msg, "id", 0) or 0)
    state["nanlong_last_chat_id"] = int(getattr(sent_msg, "chat_id", 0) or state.get("nanlong_reply_chat_id") or 0)
    state["nanlong_last_sent_at"] = _nanlong_dispatch_time_bound(sent_msg, sent_at)
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
    state["nanlong_last_chat_id"] = int(getattr(sent_msg, "chat_id", 0) or state.get("nanlong_last_chat_id") or 0)
    state["nanlong_last_sent_at"] = _nanlong_dispatch_time_bound(sent_msg, sent_at)
    state["nanlong_last_command"] = CMD_CONCUBINE_RECALL
    state["nanlong_retry_count"] = max(0, int(retry_count or 0))
    state["nanlong_reply_due_at"] = sent_at + NANLONG_CONFIRM_RETRY_DELAY_SEC
    state["nanlong_last_error"] = "等待侍妾召回确认"
    return sent_at


async def _send_nanlong_exchange_after_place(now):
    reply_to_msg_id, next_nanlong_time, _reply_due_at, pending_valid = _get_nanlong_pending_state()
    if not pending_valid or reply_to_msg_id <= 0 or next_nanlong_time <= now:
        state["nanlong_last_error"] = "南陇侯安置后原提示已失效"
        clear_nanlong_state(persist=True, keep_last_error=True)
        await send_audit_log("⚠️ 南陇侯安置成功，但原抉择提示已失效，停止自动交换。")
        return False

    choice = normalize_nanlong_choice(get_nanlong_choice())
    command = get_nanlong_choice_command(choice)
    state["nanlong_protect_phase"] = NANLONG_PROTECT_EXCHANGE_PENDING
    state["nanlong_last_msg_id"] = 0
    state["nanlong_last_sent_at"] = 0
    state["nanlong_last_command"] = ""
    return await _send_nanlong_exchange_command(command, reply_to_msg_id, now, protected=True)


async def _send_nanlong_exchange_command(command, reply_to_msg_id, now, *, retry_count=0, audit_retry=False, protected=False):
    expected = _capture_nanlong_operation()
    if not _nanlong_operation_is_current(expected):
        return False
    previous = _prepare_nanlong_send(command, now, protected=protected, retry_count=retry_count)
    if not previous:
        return False
    expected = _capture_nanlong_operation()
    sent_msg = await _send_nanlong_command(command, reply_to_msg_id)
    if not _nanlong_operation_is_current(expected, check_choice=False):
        return False
    if not sent_msg:
        return await _handle_nanlong_send_failure(command, time.time(), "南陇侯自动回复", previous)
    sent_at = _set_nanlong_waiting_for_exchange(sent_msg, command, retry_count=retry_count, protected=protected)
    save_state()
    expected = _capture_nanlong_operation()
    await _recover_nanlong_pending_reply_from_log(max(now, sent_at))
    if audit_retry and _nanlong_operation_is_current(expected, check_choice=False):
        await send_audit_log(f"🤝 南陇侯自动选择重发 {retry_count}/{NANLONG_CONFIRM_RETRY_LIMIT}：{command}，等待确认")
    return True


async def _send_nanlong_recall_after_trade(now, *, retry_count=0):
    expected = _capture_nanlong_operation()
    if not _nanlong_operation_is_current(expected):
        return False
    _clear_nanlong_prompt_anchor()
    state["nanlong_protect_phase"] = NANLONG_PROTECT_RECALL_PENDING
    previous = _prepare_nanlong_send(CMD_CONCUBINE_RECALL, now, retry_count=retry_count)
    if not previous:
        return False
    expected = _capture_nanlong_operation()
    sent_msg = await _send_nanlong_recall_command()
    if not _nanlong_operation_is_current(expected, check_choice=False):
        return False
    if not sent_msg:
        return await _handle_nanlong_send_failure(CMD_CONCUBINE_RECALL, time.time(), "南陇侯侍妾召回", previous)
    sent_at = _set_nanlong_waiting_for_recall(sent_msg, retry_count=retry_count)
    save_state()
    expected = _capture_nanlong_operation()
    await _recover_nanlong_pending_reply_from_log(max(now, sent_at))
    if not _nanlong_operation_is_current(expected, check_choice=False):
        return True
    if retry_count:
        await send_audit_log(f"🤝 南陇侯侍妾召回重发 {retry_count}/{NANLONG_CONFIRM_RETRY_LIMIT}，等待确认。")
    else:
        await send_audit_log("🤝 南陇侯交易结果已确认，已发送侍妾召回。")
    return True


async def _handle_nanlong_trade_confirmed(text, now, audit_text):
    identity_id = get_current_identity_id()
    identity = get_identity_state(identity_id)
    account_id = get_identity_account(identity_id)
    _clear_nanlong_reply_pending()
    partner_name = str(state.get("concubine_name") or "").strip()
    permanent_moon_partner = partner_name == "南宫婉" or partner_name.startswith("南宫婉·")
    if not permanent_moon_partner and _get_nanlong_protect_phase() == NANLONG_PROTECT_EXCHANGE_PENDING and _is_nanlong_trade_success_reply(text):
        handled = await _send_nanlong_recall_after_trade(now)
        if has_identity(identity_id) and get_identity_state(identity_id) is identity and get_identity_account(identity_id) == account_id:
            await _send_nanlong_reward_audit(text)
        return handled
    clear_nanlong_state(persist=True)
    await _send_nanlong_reward_audit(text)
    if has_identity(identity_id) and get_identity_state(identity_id) is identity and get_identity_account(identity_id) == account_id:
        await send_audit_log(audit_text)
    return True


async def _recover_nanlong_pending_reply_from_log(now):
    msg_id, valid = _parse_nanlong_pending_int(state.get("nanlong_last_msg_id", 0))
    if not valid or msg_id <= 0:
        return False
    chat_id = _get_nanlong_last_chat_id()
    if not chat_id:
        return False
    allowed_chats = {chat_id, *get_game_group_ids()}
    replies = []
    for entry, entry_at in iter_message_log_entries_between(
        max(0, now - NANLONG_LOG_REPLAY_LOOKBACK_SEC), now + NANLONG_LOG_REPLAY_LOOKAHEAD_SEC,
    ):
        if not _is_nanlong_recovery_log_entry(entry):
            continue
        entry_chat_id = int(entry.get("chat_id") or 0)
        direct = entry_chat_id == chat_id and int(entry.get("reply_to_msg_id") or 0) == msg_id
        if direct or (entry_chat_id in allowed_chats and _is_nanlong_success_reply(entry.get("text"))):
            replies.append((entry_at, int(entry["message_id"]), direct, entry))
    if not replies:
        return False
    reply_to = SimpleNamespace(id=msg_id, chat_id=chat_id, raw_text=str(state.get("nanlong_last_command") or ""))
    handled_any = False
    for entry_at, _message_id, direct, entry in sorted(replies, key=lambda item: item[:2]):
        expected = _capture_nanlong_operation()
        if direct:
            handled = await handle_nanlong_reply(
                entry.get("text") or "", entry_at, reply_to, matched_family="nanlong",
            )
        else:
            # Real trade outcomes can be unthreaded in the other game group.
            # Keep the live handler's identity, phase and time checks on replay.
            event = SimpleNamespace(
                id=int(entry["message_id"]), chat_id=int(entry["chat_id"]),
                reply_to_msg_id=int(entry.get("reply_to_msg_id") or 0),
            )
            handled = await handle_nanlong_result_broadcast(entry.get("text") or "", entry_at, event)
        handled_any = handled_any or handled
        if not _nanlong_operation_is_current(expected):
            break
    return handled_any


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
    state["nanlong_reply_chat_id"] = 0
    state["nanlong_reply_due_at"] = 0
    state["nanlong_last_msg_id"] = 0
    state["nanlong_last_chat_id"] = 0
    state["nanlong_last_sent_at"] = 0
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

    if _nanlong_send_is_unresolved() or _nanlong_detached_receipts(now):
        save_state()
        return True, f"已保存南陇侯选择：{get_nanlong_choice_label(normalized_choice)}，本次发送结果待确认，未重新排队"
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
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    prompt_key = f"{chat_id}:{reply_to_msg_id}" if chat_id and reply_to_msg_id else ""
    prompt_at = _nanlong_event_time(event, now)
    seen_at, valid = _parse_nanlong_pending_float(state.get("nanlong_prompt_at", 0))
    if (prompt_key and prompt_key == state.get("nanlong_last_prompt_key")) or (valid and seen_at > prompt_at + 1):
        return True
    prev_reply_to_msg_id, prev_deadline, _prev_due_at, prev_pending_valid = _get_nanlong_pending_state()
    if not prev_pending_valid:
        prev_reply_to_msg_id = 0
        prev_deadline = 0
    _set_nanlong_pending(reply_to_msg_id, prompt_at + float(parsed["timeout_sec"]), now, chat_id=chat_id, prompt_at=prompt_at)
    save_state()
    await _maybe_audit_nanlong_prompt_override(prev_reply_to_msg_id, prev_deadline, now, reply_to_msg_id)
    return True


async def run_nanlong_scheduler(now):
    if not state.get("nanlong_enabled"):
        return

    reply_to_msg_id, next_nanlong_time, reply_due_at, pending_valid = _get_nanlong_pending_state()
    if not pending_valid:
        return
    adopted = _adopt_nanlong_detached_receipt(now)
    if adopted:
        if state.get("nanlong_last_command") == CMD_NANLONG_REJECT:
            await _finalize_nanlong_success(f"🤝 南陇侯自动选择：{NANLONG_CHOICE_LABELS[NANLONG_CHOICE_REJECT]}")
            return
        reply_to_msg_id, next_nanlong_time, reply_due_at, pending_valid = _get_nanlong_pending_state()
    phase = _get_nanlong_protect_phase()
    if adopted or (reply_due_at > 0 and now >= reply_due_at):
        expected = _capture_nanlong_operation()
        if await _recover_nanlong_pending_reply_from_log(now) or not _nanlong_operation_is_current(expected):
            return
    if _nanlong_detached_receipts(now):
        if reply_due_at > 0 and now >= reply_due_at:
            error = "南陇侯迟到发送回执已登记，结果待核对，不自动重发"
            notify = state.get("nanlong_last_error") != error
            state["nanlong_reply_due_at"] = now + NANLONG_CONFIRM_RETRY_DELAY_SEC
            state["nanlong_last_error"] = error
            save_state()
            if notify:
                await send_audit_log(f"⚠️ {error}。")
        return
    if phase == NANLONG_PROTECT_RECALL_PENDING:
        if reply_due_at <= 0 or now < reply_due_at:
            return
        retry_count = int(state.get("nanlong_retry_count", 0) or 0)
        if retry_count >= NANLONG_CONFIRM_RETRY_LIMIT:
            state["nanlong_last_error"] = "南陇侯交易完成但侍妾召回未确认"
            clear_nanlong_state(persist=True, keep_last_error=True)
            await send_audit_log("⚠️ 南陇侯交易完成但侍妾召回未确认，已停止自动处理，请人工核对。")
            return
        await _send_nanlong_recall_after_trade(now, retry_count=retry_count + 1)
        return
    if reply_to_msg_id <= 0 or next_nanlong_time <= 0:
        if _has_nanlong_inflight_state():
            clear_nanlong_state(persist=True)
        return
    if now >= next_nanlong_time:
        state["nanlong_last_error"] = "南陇侯提示已超时"
        clear_nanlong_state(persist=True, keep_last_error=True)
        await send_audit_log(f"⚠️ 南陇侯抉择超时，消息ID={reply_to_msg_id}")
        return
    if reply_due_at <= 0 or now < reply_due_at:
        return
    chat_id, route_valid = _parse_nanlong_pending_int(state.get("nanlong_reply_chat_id", 0))
    if not route_valid or not chat_id:
        if state.get("nanlong_last_error") != "缺少南陇侯原题所在群，停止自动回复":
            _set_nanlong_error_and_save("缺少南陇侯原题所在群，停止自动回复")
        return

    choice = normalize_nanlong_choice(get_nanlong_choice())
    command = get_nanlong_choice_command(choice)
    requires_confirmation = choice in NANLONG_CONFIRM_CHOICES

    if phase == NANLONG_PROTECT_PLACE_PENDING:
        state["nanlong_protect_phase"] = ""
        state["nanlong_place_msg_id"] = 0
        state["nanlong_last_msg_id"] = 0
        state["nanlong_retry_count"] = 0
        expected = _capture_nanlong_operation()
        await send_audit_log("⚠️ 南陇侯侍妾安置未确认，降级直接回复南陇侯。")
        if not _nanlong_operation_is_current(expected):
            return
        await _send_nanlong_exchange_command(command, reply_to_msg_id, now, protected=False)
        return

    is_confirmation_retry = bool(state.get("nanlong_last_msg_id"))
    if requires_confirmation and is_confirmation_retry and int(state.get("nanlong_retry_count", 0) or 0) >= NANLONG_CONFIRM_RETRY_LIMIT:
        state["nanlong_last_error"] = "南陇侯交易结果未确认"
        if phase == NANLONG_PROTECT_EXCHANGE_PENDING:
            expected = _capture_nanlong_operation()
            await send_audit_log("⚠️ 南陇侯交易结果未确认，先尝试召回洞府侍妾后停止。")
            if not _nanlong_operation_is_current(expected):
                return
            await _send_nanlong_recall_after_trade(now)
            return
        clear_nanlong_state(persist=True, keep_last_error=True)
        await send_audit_log(f"⚠️ 南陇侯自动回复重发 {NANLONG_CONFIRM_RETRY_LIMIT} 次仍未确认，已停止。")
        return

    if requires_confirmation and not is_confirmation_retry and phase != NANLONG_PROTECT_EXCHANGE_PENDING and _nanlong_exchange_requires_protection(choice):
        cave_status = _get_nanlong_cave_status()
        if cave_status != NANLONG_CAVE_STATUS_AVAILABLE:
            expected = _capture_nanlong_operation()
            await send_audit_log(f"🤝 南陇侯{_get_nanlong_cave_status_label(cave_status)}，跳过安置，直接交易后交由侍妾补领链路处理。")
            if not _nanlong_operation_is_current(expected):
                return
            await _send_nanlong_exchange_command(command, reply_to_msg_id, now, protected=False)
            return
        previous = _prepare_nanlong_send(CMD_CONCUBINE_PLACE, now)
        if not previous:
            return
        expected = _capture_nanlong_operation()
        sent_msg = await _send_nanlong_place_command()
        if not _nanlong_operation_is_current(expected, check_choice=False):
            return
        if not sent_msg:
            await _handle_nanlong_send_failure(CMD_CONCUBINE_PLACE, time.time(), "南陇侯侍妾安置", previous)
            return
        sent_at = _set_nanlong_waiting_for_place(sent_msg)
        save_state()
        await _recover_nanlong_pending_reply_from_log(max(now, sent_at))
        return

    if not requires_confirmation:
        previous = _prepare_nanlong_send(command, now)
        if not previous:
            return
        expected = _capture_nanlong_operation()
        sent_msg = await _send_nanlong_command(command, reply_to_msg_id)
        if not _nanlong_operation_is_current(expected, check_choice=False):
            return
        if not sent_msg:
            await _handle_nanlong_send_failure(command, time.time(), "南陇侯自动回复", previous)
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
    _adopt_nanlong_detached_receipt(now)
    if not state.get("nanlong_last_msg_id"):
        return False
    if not _is_reply_to_nanlong_last_msg(reply_to):
        return False
    phase = _get_nanlong_protect_phase()
    if phase == NANLONG_PROTECT_PLACE_PENDING:
        if _is_concubine_place_success(text):
            _clear_nanlong_reply_pending()
            await _send_nanlong_exchange_after_place(now)
            return True
        if _is_concubine_place_failure(text):
            _clear_nanlong_reply_pending()
            state["nanlong_protect_phase"] = ""
            state["nanlong_place_msg_id"] = 0
            state["nanlong_last_msg_id"] = 0
            state["nanlong_retry_count"] = 0
            state["nanlong_last_error"] = "侍妾安置失败，降级直接交换"
            save_state()
            expected = _capture_nanlong_operation()
            await send_audit_log("⚠️ 南陇侯侍妾安置失败，降级直接回复南陇侯。")
            if not _nanlong_operation_is_current(expected):
                return True
            await _send_nanlong_exchange_command(get_nanlong_choice_command(get_nanlong_choice()), state.get("nanlong_reply_to_msg_id", 0), now)
            return True
        return False
    if phase == NANLONG_PROTECT_RECALL_PENDING:
        if _is_concubine_recall_success(text):
            await _finalize_nanlong_success("🤝 南陇侯交易完成，侍妾已召回")
            return True
        if _is_concubine_recall_failure(text):
            _clear_nanlong_reply_pending()
            state["nanlong_last_error"] = "南陇侯交易完成但侍妾召回失败"
            clear_nanlong_state(persist=True, keep_last_error=True)
            await send_audit_log("⚠️ 南陇侯交易完成但侍妾召回失败，请人工核对。")
            return True
        return False
    if not _is_nanlong_success_reply(text):
        return False

    await _handle_nanlong_trade_confirmed(text, now, "🤝 南陇侯交易结果已确认")
    return True


async def handle_nanlong_result_broadcast(text, now, event):
    if not state.get("nanlong_enabled"):
        return False
    if not _is_nanlong_success_reply(text):
        return False
    _adopt_nanlong_detached_receipt(now)
    if not state.get("nanlong_last_msg_id"):
        return False
    if str(state.get("nanlong_last_command") or "") not in {CMD_NANLONG_EXCHANGE_FABAO, CMD_NANLONG_EXCHANGE_GONGFA}:
        return False
    if _get_nanlong_protect_phase() not in {"", NANLONG_PROTECT_EXCHANGE_PENDING}:
        return False
    identity_id = _find_nanlong_identity_id(text)
    if identity_id is None or identity_id != get_current_identity_id():
        return False
    sent_at, valid = _parse_nanlong_pending_float(state.get("nanlong_last_sent_at", 0))
    if not valid or sent_at <= 0 or _nanlong_event_time(event, now, edited=True) + 1 < sent_at:
        return False
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    reply_id = int(getattr(event, "reply_to_msg_id", 0) or getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)
    if reply_id and reply_id != get_game_group_topic_id(chat_id, default=0):
        if chat_id != _get_nanlong_last_chat_id() or reply_id != int(state.get("nanlong_last_msg_id") or 0):
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
