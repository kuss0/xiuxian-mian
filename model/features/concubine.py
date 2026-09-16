import asyncio
import copy
import hashlib
import json
import math
import os
import random
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from ..config import (
    CD_BUFFER_SEC,
    CMD_CONCUBINE_DAILY_GREET,
    CMD_CONCUBINE_DREAM,
    CMD_CONCUBINE_FRAGMENT,
    CMD_CONCUBINE_GIFT_STONE,
    CMD_CONCUBINE_HEART,
    CMD_CONCUBINE_HEART_STEADY,
    CMD_CONCUBINE_PUZZLE,
    CMD_CONCUBINE_ROMANCE,
    CMD_CONCUBINE_SECT_MARRY,
    CMD_CONCUBINE_STATUS,
    CMD_CONCUBINE_TIANJI,
    CMD_CONCUBINE_VOYAGE,
    CMD_CONCUBINE_VOYAGE_RETURN,
    CMD_CONCUBINE_VOYAGE_STATUS,
    CONCUBINE_CHAIN_DELAY_MAX_SEC,
    CONCUBINE_CHAIN_DELAY_MIN_SEC,
    CONCUBINE_DREAM_CD_SEC,
    CONCUBINE_HEART_CD_SEC,
    CONCUBINE_HEART_CHOICE_DELAY_MAX_SEC,
    CONCUBINE_HEART_CHOICE_DELAY_MIN_SEC,
    CONCUBINE_NO_PARTNER_RETRY_SEC,
    CONCUBINE_PHASE_TIMEOUT_SEC,
    CONCUBINE_REACQUIRE_RETRY_SEC,
    CONCUBINE_STATUS_RECHECK_MAX_SEC,
    CONCUBINE_STATUS_RECHECK_MIN_SEC,
    CONCUBINE_STATUS_STALE_SEC,
    CONCUBINE_TIANJI_CD_SEC,
    CONCUBINE_VOYAGE_DEFAULT_ROUTE,
    CONCUBINE_VOYAGE_MOON_ROUTE,
    CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC,
    MESSAGES_DIR,
    RE_WHITESPACE,
    TZ_LOCAL,
)
from ..persisted_state import PersistedState
from ..persistence import mark_dirty, save_state
from ..message_keys import find_message_key, message_key_parts
from ..message_log_recovery import find_message_log_replies, find_recent_message_log_commands, sender_matches_identity
from ..runtime import _fire_and_forget, classify_game_send_block, clear_pending_by_reply, clear_pending_tasks_by_commands, console_log, get_last_game_send_block, get_sent_message_chat_id, send_audit_log, send_game_command, was_last_game_send_blocked_by_global
from ..state import get_current_identity_id, get_game_bot_ids, get_game_group_id, get_game_group_ids, get_game_topic_id, get_global_enabled, get_identity_account, get_identity_enabled, get_identity_ids, get_identity_state, get_pending_command, get_send_as_profile, get_send_as_tags, has_identity, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from ..verified_event import telegram_event_timestamp
from ..resource_accounting import valid_point
from . import workflow_log
from . import concubine_affinity_actions as affinity_actions
from . import concubine_fragment_actions as fragment_actions
from . import concubine_voyage_actions as voyage_actions
from . import concubine_divination_actions as divination_actions
from . import concubine_heart_contract as heart_contract
from . import concubine_heart_actions as heart_actions
from . import concubine_reacquire_actions as reacquire_actions
from . import concubine_external_events as external_events
from . import heavenly_ban as heavenly_ban_mod
from .wanxin import wanxin_affinity_snapshot_is_stale
from .resource_backoff import record_resource_shortage, reset_resource_shortage
from .storage_bag import CMD_STORAGE_BAG, apply_storage_bag_item_deltas, parse_storage_bag_reply, resolve_storage_bag_identity_id
from ..action_guard import close_action as close_action_guard


CONCUBINE_PENDING_COMMANDS = {
    CMD_CONCUBINE_STATUS,
    CMD_CONCUBINE_DAILY_GREET,
    CMD_CONCUBINE_GIFT_STONE,
    CMD_CONCUBINE_DREAM,
    CMD_CONCUBINE_FRAGMENT,
    CMD_CONCUBINE_PUZZLE,
    CMD_CONCUBINE_SECT_MARRY,
    CMD_CONCUBINE_ROMANCE,
    CMD_CONCUBINE_TIANJI,
    CMD_CONCUBINE_HEART,
    CMD_CONCUBINE_HEART_STEADY,
    CMD_CONCUBINE_VOYAGE,
    CMD_CONCUBINE_VOYAGE_RETURN,
    CMD_CONCUBINE_VOYAGE_STATUS,
}

_CONCUBINE_SCHEDULER_LOCK = asyncio.Lock()
_CONCUBINE_QUERY_INFLIGHT = {}
CONCUBINE_QUERY_SOURCE = "concubine_status"
CONCUBINE_QUERY_UNRESOLVED = {"sending", "sent", "unknown"}
CONCUBINE_QUERY_REPLAY_SEC = 60
CONCUBINE_QUERY_KEYS = {
    "status": "concubine_status_msg_id",
    "gift_status": "concubine_gift_status_msg_id",
    "fragment": "concubine_fragment_msg_id",
    "voyage_status": "concubine_voyage_msg_id",
}
CONCUBINE_QUERY_COMMANDS = {
    "status": CMD_CONCUBINE_STATUS,
    "gift_status": CMD_CONCUBINE_STATUS,
    "fragment": CMD_CONCUBINE_FRAGMENT,
    "voyage_status": CMD_CONCUBINE_VOYAGE_STATUS,
}
CONCUBINE_QUERY_FAMILIES = {
    "status": "concubine_status", "gift_status": "concubine_status",
    "fragment": "concubine_fragment", "voyage_status": "concubine_voyage",
}
CONCUBINE_MAIN_PENDING_COMMANDS = CONCUBINE_PENDING_COMMANDS - {
    CMD_CONCUBINE_TIANJI,
    CMD_CONCUBINE_VOYAGE,
    CMD_CONCUBINE_VOYAGE_RETURN,
    CMD_CONCUBINE_VOYAGE_STATUS,
}
CONCUBINE_ERROR_KEYS = (
    "concubine_last_error",
    "concubine_tianji_last_error",
    "concubine_greet_last_error",
    "concubine_gift_last_error",
    "concubine_heart_last_error",
    "concubine_voyage_last_error",
)
CONCUBINE_REACQUIRE_COMMANDS = {CMD_CONCUBINE_SECT_MARRY, CMD_CONCUBINE_ROMANCE}
IDENTITY_TAG_PATTERN = r"[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+"

RE_CONCUBINE_HEAD = re.compile(r"你的(?P<kind>道心侍妾|红尘道侣)[：:]\s*【(?P<name>[^】]+)】\s*[(（]状态[：:]\s*(?P<location>[^)）\n]+)[)）]")
CONCUBINE_STATUS_FIELDS = {
    "情缘值": "affinity",
    "当前誓约": "oath",
    "入梦寻图冷却": "dream_due_at",
    "天机代卜冷却": "tianji_due_at",
    "共历心劫冷却": "heart_due_at",
    "天机代卜链": "tianji_chain",
    "梦图拼片": "fragment_progresses",
    "远航状态": "voyage",
}
RE_CONCUBINE_STATUS_FIELD = re.compile(
    r"^[ \t]*(?:-[ \t]*)?(?P<label>" + "|".join(CONCUBINE_STATUS_FIELDS)
    + r")[：:][ \t]*(?P<value>[^\r\n]*)$", re.MULTILINE,
)
RE_CONCUBINE_STATUS_WAIT = re.compile(
    r"(?:(?P<hours>[0-9]+)\s*(?:小时|时))?\s*"
    r"(?:(?P<minutes>[0-9]+)\s*(?:分钟|分))?\s*"
    r"(?:(?P<seconds>[0-9]+)\s*秒)?"
)
RE_TIANJI_CHAIN_REMAINING = re.compile(r"(?P<name>[^（(]+)[（(]\s*剩余\s*(?P<wait>[^）)]+)\s*[）)]")
RE_TIANJI_XIUWEI_SHORTAGE = re.compile(r"修为不足[，,]\s*代卜天机需消耗\s*\d+\s*点?修为")
RE_DREAM_PARTNER = re.compile(r"你与侍妾【(?P<name>[^】]+)】")
RE_AFFINITY_GAIN = re.compile(r"侍妾【(?P<name>[^】]+)】[\s\S]*?情缘增加了\s*(?P<amount>\d+)\s*点")
RE_CONCUBINE_GIFT_SUCCESS = re.compile(
    r"你将【灵石】[x×]\s*(?P<stone>[\d,]+)\s*赠予了侍妾【(?P<name>[^】]+)】[\s\S]*?情缘增加了\s*(?P<amount>[\d,]+)\s*点"
)
RE_SELFLESS_PARTNER = re.compile(r"侍妾\s*【?(?P<name>[^】\s，,。]+)】?\s*挺身而出")
RE_FRAGMENT_PROGRESS = re.compile(r"(?:虚天残图拼片|拼片进度|当前进度)\s*[：:]?\s*(\d+)\s*/\s*(\d+)")
RE_FRAGMENT_TYPED_PROGRESS = re.compile(r"(?P<kind>虚天|苍坤)\s*(?:残图)?(?:拼片|进度)?\s*(?:已至)?\s*[：:]?\s*(?P<count>\d+)\s*/\s*(?P<total>\d+)")
RE_FRAGMENT_CONTEXT_KIND = re.compile(r"[【\[][^\]】]*(?P<kind>虚天|苍坤)残图[^\]】]*[】\]]")
RE_FRAGMENT_PANEL_HEAD = re.compile(r"^[ \t]*【(?P<kind>[^】\r\n]+)残图卷】[ \t]*$", re.MULTILINE)
RE_FRAGMENT_PANEL_OWNER = re.compile(r"侍妾【(?P<name>[^】\r\n]{1,120})】[（(][^）)\r\n]+[）)]的残图卷轴如下[：:]")
RE_FRAGMENT_PANEL_FIELD = re.compile(
    r"^[ \t]*(?P<label>拼片进度|已收集|缺失残纹)[：:][ \t]*(?P<value>[^\r\n]*)$", re.MULTILINE,
)
RE_DREAM_BROADCAST_PROGRESS = re.compile(r"残图进度已至\s*(\d+)\s*/\s*(\d+)")
RE_IDENTITY_TAG = re.compile(rf"@({IDENTITY_TAG_PATTERN})")
RE_VOYAGE_PANEL = re.compile(r"远航状态[：:]\s*(?P<route>[^\n。]{1,40}?)航线(?P<state>进行中|已归航)(?:[，,](?P<tail>[^\n。]+))?")
RE_VOYAGE_STATUS_SAILING = re.compile(r"侍妾【(?P<name>[^】\r\n]{1,120})】正在执行【(?P<route>[^】\r\n]{1,40})】远航[，,]\s*预计归航还需\s*(?P<wait>[^。\r\n]+)")
RE_VOYAGE_STATUS_RETURNED = re.compile(r"侍妾【(?P<name>[^】\r\n]{1,120})】已自【(?P<route>[^】\r\n]{1,40})】航线归来(?:[，,]待结算[（(]\.远航归来[）)])?")
RE_VOYAGE_LOCK = re.compile(r"侍妾(?:【(?P<name>[^】\r\n]{1,120})】)?(?:(?:正在|仍在)远航(?:途中|中)|尚未归航)")
RE_VOYAGE_START = re.compile(r"【乱星海远航·启】[\s\S]*?你命侍妾【(?P<name>[^】]+)】沿\s*(?P<route>\S+)\s*航线远行[\s\S]*?预计归航时间[：:]\s*(?P<wait>[^。\n]+)")
RE_VOYAGE_RETURN = re.compile(r"【乱星海远航·归】[\s\S]*?侍妾【(?P<name>[^】]+)】已自\s*(?P<route>\S+)\s*航线归来")
RE_VOYAGE_AFFINITY_LOSS = re.compile(r"情缘减少\s*(?P<amount>[\d,]+)\s*点")
RE_VOYAGE_SPIRIT_RESERVE = re.compile(r"蓄灵\s*(?P<amount>[\d,]+)\s*点")
RE_VOYAGE_AFFINITY_REQUIREMENT = re.compile(r"此航线至少需要\s*(?P<amount>[\d,]+)\s*情缘值")

CONCUBINE_DREAM_RESOURCE_KEY = "concubine_dream"
CONCUBINE_TIANJI_RESOURCE_KEY = "concubine_tianji"
CONCUBINE_HEART_RESOURCE_KEY = "concubine_heart"
CONCUBINE_LOG_REPLAY_LOOKBACK_SEC = CONCUBINE_PHASE_TIMEOUT_SEC + 5 * 60
CONCUBINE_LOG_REPLAY_LOOKAHEAD_SEC = 5
CONCUBINE_TIMEOUT_CANDIDATE_LOOKBACK_SEC = 2 * 60
CONCUBINE_TIMEOUT_CANDIDATE_MAX_LINES = 1200
CONCUBINE_PANEL_REUSE_MAX_AGE_SEC = 10 * 60
CONCUBINE_HEART_PANEL_MAX_AGE_SEC = CONCUBINE_PANEL_REUSE_MAX_AGE_SEC
CONCUBINE_HEART_GLOBAL_START_GAP_SEC = 5 * 60
CONCUBINE_DREAM_MIN_RETRY_SEC = 90
CONCUBINE_TIANJI_MIN_AFFINITY = 300
CONCUBINE_VOYAGE_MIN_AFFINITY = 120
CONCUBINE_VOYAGE_MOON_MIN_AFFINITY = 160
CONCUBINE_STATUS_REUSE_DEFER_MIN_SEC = 30
CONCUBINE_STATUS_REUSE_DEFER_MAX_SEC = 90
CONCUBINE_HEART_ACTIVE_PHASES = {"heart_pending", "heart_choice_pending", "heart_choice_reply_pending"}
CONCUBINE_VOYAGE_PENDING_PHASES = {"voyage_pending", "voyage_return_pending"}
CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC = 60 * 60
CONCUBINE_GIFT_PHASES = {"gift_status_pending", "gift_bag_pending", "gift_pending"}
CONCUBINE_GREET_DEFER_MIN_SEC = 60
CONCUBINE_GREET_DEFER_MAX_SEC = 180
CONCUBINE_ACTIVE_DEFER_MIN_SEC = 60
CONCUBINE_ACTIVE_DEFER_MAX_SEC = 180
CONCUBINE_SEND_FAILURE_RETRY_MIN_SEC = 10 * 60
CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC = 20 * 60
CONCUBINE_DUE_SCAN_SEND_QUEUE_TIMEOUT_SEC = 90
PHASEFUL_SUMMARY_GUARD_PHASES = {"summary_due", "observing_summary", "waiting_summary", "post_summary_wait"}
CONCUBINE_PARTNER_SNAPSHOT_KEYS = (
    "concubine_availability",
    "concubine_last_panel_msg_id",
    "concubine_last_panel_chat_id",
    "concubine_name",
    "concubine_kind",
    "concubine_location",
    "concubine_affinity",
    "concubine_oath",
    "concubine_dream_due_at",
    "concubine_last_snapshot_at",
    "concubine_fragment_count",
    "concubine_fragment_total",
    "concubine_fragment_xutian_count",
    "concubine_fragment_xutian_total",
    "concubine_fragment_cangkun_count",
    "concubine_fragment_cangkun_total",
    "concubine_fragment_confirm_key",
    "concubine_fragment_confirmed_at",
)
CONCUBINE_VOYAGE_RUNTIME_KEYS = (
    "concubine_voyage_msg_id",
    "concubine_voyage_status",
    "concubine_voyage_route",
    "concubine_voyage_return_at",
    "concubine_voyage_last_result",
    "concubine_voyage_last_error",
    "concubine_voyage_retry_count",
)
_SEND_QUEUE_TIMEOUT_OVERRIDE = ContextVar("concubine_send_queue_timeout_override", default=None)
CONCUBINE_UNSENT_BLOCK_CODES = {
    "send_queue_timeout",
    "send_prepare_timeout",
    "global_disabled",
    "global_recovery_cooldown",
    "dungeon_quiet",
    "account_offline",
    "account_unbound",
    "account_client_missing",
    "account_client_not_ready",
    "account_session_error",
    "send_as_peer_invalid",
    "bot_health",
    "identity_weak",
    "pre_send_guard",
    "action_guard",
    "supervisor_quiesce",
}


@contextmanager
def concubine_send_queue_timeout(timeout_sec):
    try:
        timeout_value = float(timeout_sec or 0)
    except (TypeError, ValueError, OverflowError):
        timeout_value = 0.0
    token = _SEND_QUEUE_TIMEOUT_OVERRIDE.set(timeout_value if timeout_value > 0 else None)
    try:
        yield
    finally:
        _SEND_QUEUE_TIMEOUT_OVERRIDE.reset(token)


async def _send_concubine_game_command(command, **kwargs):
    timeout_value = _SEND_QUEUE_TIMEOUT_OVERRIDE.get()
    if timeout_value is not None and "queue_timeout" not in kwargs:
        kwargs["queue_timeout"] = max(1, float(timeout_value))
    return await send_game_command(command, **kwargs)


DREAM_KIND_XUTIAN = "xutian"
DREAM_KIND_CANGKUN = "cangkun"
FRAGMENT_KIND_ORDER = (DREAM_KIND_XUTIAN, DREAM_KIND_CANGKUN)
FRAGMENT_LABELS = {
    DREAM_KIND_XUTIAN: "虚天",
    DREAM_KIND_CANGKUN: "苍坤",
}
FRAGMENT_FIELDS = {
    DREAM_KIND_XUTIAN: ("concubine_fragment_xutian_count", "concubine_fragment_xutian_total"),
    DREAM_KIND_CANGKUN: ("concubine_fragment_cangkun_count", "concubine_fragment_cangkun_total"),
}


def _phase():
    return state.get("concubine_phase", "idle")


def _set_phase(new_phase):
    state["concubine_phase"] = str(new_phase or "idle")


def _set_availability(value):
    state["concubine_availability"] = str(value or "unknown")


def _clear_non_heart_pending_msg_ids():
    state["concubine_status_msg_id"] = 0
    state["concubine_greet_msg_id"] = 0
    state["concubine_gift_status_msg_id"] = 0
    state["concubine_gift_bag_msg_id"] = 0
    state["concubine_gift_msg_id"] = 0
    state["concubine_gift_amount"] = 0
    state["concubine_dream_msg_id"] = 0
    state["concubine_fragment_msg_id"] = 0
    state["concubine_puzzle_msg_id"] = 0
    state["concubine_reacquire_msg_id"] = 0
    state["concubine_tianji_msg_id"] = 0
    state["concubine_voyage_msg_id"] = 0
    state["concubine_voyage_retry_count"] = 0


def _clear_pending_msg_ids():
    _clear_non_heart_pending_msg_ids()
    state["concubine_heart_msg_id"] = 0
    state["concubine_heart_prompt_msg_id"] = 0
    state["concubine_heart_round"] = 0
    _clear_heart_choice_guard()




def _is_heart_anchor_lost_text(text):
    return heart_contract.is_anchor_lost(text)


def _clear_heart_choice_guard():
    state["concubine_heart_choice_prompt_msg_id"] = 0
    state["concubine_heart_choice_round"] = 0
    state["concubine_heart_choice_sent_at"] = 0
    state["concubine_heart_choice_retry_count"] = 0
    state["concubine_last_recovered_reply_key"] = ""
    state["concubine_last_recovered_reply_at"] = 0




def _schedule_after(now, min_sec, max_sec):
    state["next_concubine_time"] = float(now + random.uniform(min_sec, max_sec))
    return state["next_concubine_time"]


def _schedule_status_recheck(now):
    return _schedule_after(now, CONCUBINE_STATUS_RECHECK_MIN_SEC, CONCUBINE_STATUS_RECHECK_MAX_SEC)


def _schedule_chain_action(now):
    return _schedule_after(now, CONCUBINE_CHAIN_DELAY_MIN_SEC, CONCUBINE_CHAIN_DELAY_MAX_SEC)


def _handle_send_queue_timeout(command, now, *, due_key=None, error_key="concubine_last_error", label="侍妾指令"):
    send_block = get_last_game_send_block(get_current_identity_id(), command)
    code = str((send_block or {}).get("code") or "")
    if code not in CONCUBINE_UNSENT_BLOCK_CODES and not code.startswith("flood_wait"):
        return False
    retry_at = _schedule_after(
        float(now or time.time()),
        CONCUBINE_SEND_FAILURE_RETRY_MIN_SEC,
        CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC,
    )
    if due_key and float(state.get(due_key, 0) or 0) <= float(now or time.time()):
        state[due_key] = retry_at
    state[error_key] = ""
    if code == "send_queue_timeout":
        state["concubine_last_result"] = f"{label}发送队列拥堵，已错峰重试"
    else:
        reason = str((send_block or {}).get("reason") or "").strip()
        detail = f"{code}: {reason}" if reason else code or "runtime_block"
        state["concubine_last_result"] = f"{label}未发送，已错峰重试（{detail}）"
    _set_phase("idle")
    return True


def _normalize_resolved_puzzle_send_error():
    if str(state.get("concubine_last_error") or "") != "发送 .拼图 失败":
        return False
    if _phase() != "idle" or _is_puzzle_ready():
        return False
    state["concubine_last_error"] = ""
    if not str(state.get("concubine_last_result") or "").strip():
        state["concubine_last_result"] = "拼图发送失败已退回残图重查"
    return True


def _schedule_at_due_or_chain(now, due_at):
    due_at = float(due_at or 0)
    if due_at <= now:
        return _schedule_chain_action(now)
    state["next_concubine_time"] = due_at + random.uniform(60, 600)
    return state["next_concubine_time"]


def _local_day_key(now):
    return datetime.fromtimestamp(float(now), TZ_LOCAL).strftime("%Y-%m-%d")


def _parse_count(value):
    try:
        return int(str(value or "0").replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0


def _next_local_day_at(now):
    local_now = datetime.fromtimestamp(float(now), TZ_LOCAL)
    next_day = (local_now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return float(next_day.timestamp() + random.uniform(0, 55 * 60))


def _is_star_palace_identity():
    profile = get_send_as_profile()
    return str(profile.get("sect_name") or "").strip() == "星宫"


def _is_moon_voyage_partner():
    return str(state.get("concubine_name") or "").strip() == "南宫婉·月影"


def _preferred_voyage_route():
    if _is_moon_voyage_partner():
        return CONCUBINE_VOYAGE_MOON_ROUTE
    return CONCUBINE_VOYAGE_DEFAULT_ROUTE


def _voyage_min_affinity(route=None):
    route = str(route or _preferred_voyage_route()).strip()
    if route == CONCUBINE_VOYAGE_MOON_ROUTE:
        return CONCUBINE_VOYAGE_MOON_MIN_AFFINITY
    return CONCUBINE_VOYAGE_MIN_AFFINITY


def _parse_voyage_rejection(text, now):
    raw_text = re.sub(r"[*`]+", "", str(text or "")).strip()
    locks = list(RE_VOYAGE_LOCK.finditer(raw_text))
    if (len(raw_text) > 4096 or len(locks) != 1 or _status_timestamp(now) is None
            or _is_no_partner_text(raw_text) or _is_phaseful_summary_text(raw_text)
            or any(token in raw_text for token in ("当前并未执行远航任务", "并无可结算的远航任务", "航线已归航"))):
        return None
    matched = locks[0]
    # Only the wait clause in this rejection sentence is a voyage clock.
    tail = re.split(r"[。\r\n]", raw_text[matched.end():], maxsplit=1)[0].strip(" ，,")
    wait = re.fullmatch(r"(?:预计归航)?(?:还需|尚需|剩余(?:约)?)\s*(.+?)(?:\s*后(?:归来|归航))?", tail)
    if wait is None:
        wait = re.fullmatch(r"请在\s*(.+?)\s*后再试", tail)
    return_at = 0.0
    if wait:
        return_at = _parse_wait_due_at(wait.group(1), now)
        if return_at is None or return_at <= now:
            return None
    elif any(token in tail for token in ("还需", "尚需", "剩余", "请在", "预计归航")):
        return None
    return {"status": "sailing", "route": "", "partner": (matched["name"] or "").strip(),
            "return_at": return_at, "result": "", "error": raw_text if not return_at else ""}


def _parse_voyage_status_text(text, now):
    raw_text = re.sub(r"[*`]+", "", str(text or "")).strip()
    if not raw_text or len(raw_text) > 4096 or _status_timestamp(now) is None:
        return None
    raw_text = re.sub(r"^【(?:侍妾远航|远航状态)】\s*", "", raw_text).removesuffix("。").strip()
    matched = re.fullmatch(r"远航状态[：:]\s*(.+)", raw_text)
    if matched:
        return _parse_status_voyage(matched.group(1), now)
    matched = RE_VOYAGE_STATUS_SAILING.fullmatch(raw_text)
    if matched:
        return_at = _parse_wait_due_at(matched["wait"], now)
        if return_at is None or return_at <= now:
            return None
        return {"status": "sailing", "partner": matched["name"].strip(),
                "route": matched["route"].strip(), "return_at": return_at}
    matched = RE_VOYAGE_STATUS_RETURNED.fullmatch(raw_text)
    if matched:
        return {"status": "returned", "partner": matched["name"].strip(),
                "route": matched["route"].strip(), "return_at": float(now)}
    matched = re.fullmatch(r"侍妾(?:【(?P<name>[^】\r\n]{1,120})】)?当前并未执行远航任务", raw_text)
    if matched:
        return {"status": "no_task", "clear_idle": True, "partner": (matched["name"] or "").strip()}
    if raw_text == "侍妾当前并无可结算的远航任务":
        return {"status": "needs_status", "return_at": 0.0, "error": raw_text}
    return _parse_voyage_rejection(raw_text, now)


def _is_voyage_lock_text(text):
    return RE_VOYAGE_LOCK.search(str(text or "")) is not None


def _apply_voyage_blocked_action(parsed, now, *, error_key, label):
    _apply_voyage_snapshot(parsed, now)
    state[error_key] = f"{label}被远航锁拦截，等待归航"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _schedule_voyage_wait(now)


def _handle_action_blocked_by_voyage(raw_text, now, *, error_key, label):
    voyage = _parse_voyage_rejection(raw_text, now)
    if not voyage:
        return False
    _apply_voyage_blocked_action(voyage, now, error_key=error_key, label=label)
    return True


def _apply_voyage_snapshot(parsed, now):
    if not parsed:
        return False
    status = str(parsed.get("status") or "").strip()
    route = str(parsed.get("route") or "").strip()
    partner = str(parsed.get("partner") or "").strip()
    if partner:
        state["concubine_name"] = partner
    if route:
        state["concubine_voyage_route"] = route
    if status:
        state["concubine_voyage_status"] = status
    if status == "sailing":
        _clear_stale_tianji_summary_wait_error()
        return_at = float(parsed.get("return_at", 0) or 0)
        state["concubine_voyage_return_at"] = return_at
        state["concubine_voyage_last_result"] = ""
        state["concubine_voyage_last_error"] = str(parsed.get("error") or "")
        state["concubine_voyage_retry_count"] = 0
        if _phase() in CONCUBINE_VOYAGE_PENDING_PHASES:
            _set_phase("idle")
            state["concubine_voyage_msg_id"] = 0
        if return_at > 0 and return_at <= now:
            _schedule_chain_action(now)
        else:
            _schedule_voyage_wait(now)
        return True
    if status == "returned":
        state["concubine_voyage_return_at"] = float(parsed.get("return_at", now) or now)
        state["concubine_voyage_last_error"] = str(parsed.get("error") or "")
        state["concubine_voyage_retry_count"] = 0
        if _phase() in CONCUBINE_VOYAGE_PENDING_PHASES:
            _set_phase("idle")
            state["concubine_voyage_msg_id"] = 0
        _schedule_chain_action(now)
        return True
    if status == "idle":
        state["concubine_voyage_return_at"] = float(parsed.get("return_at", 0) or 0)
        result = str(parsed.get("result") or "").strip()
        if result:
            state["concubine_voyage_last_result"] = result
        state["concubine_voyage_last_error"] = str(parsed.get("error") or "")
        state["concubine_voyage_retry_count"] = 0
        if _phase() in CONCUBINE_VOYAGE_PENDING_PHASES:
            _set_phase("idle")
            state["concubine_voyage_msg_id"] = 0
        _schedule_chain_action(now)
        return True
    if status in {"no_task", "needs_status"}:
        state["concubine_voyage_last_error"] = str(parsed.get("error") or "")
        if status == "no_task" and parsed.get("clear_idle") is True:
            state["concubine_voyage_status"] = "idle"
            state["concubine_voyage_return_at"] = 0
            state["concubine_voyage_retry_count"] = 0
            if _phase() in CONCUBINE_VOYAGE_PENDING_PHASES:
                _set_phase("idle")
                state["concubine_voyage_msg_id"] = 0
            _schedule_chain_action(now)
            return True
        state["concubine_voyage_status"] = "needs_status"
        state["concubine_voyage_return_at"] = 0
        if _phase() in CONCUBINE_VOYAGE_PENDING_PHASES:
            _set_phase("idle")
            state["concubine_voyage_msg_id"] = 0
        state["concubine_voyage_retry_count"] = max(int(state.get("concubine_voyage_retry_count", 0) or 0), 2)
        _schedule_voyage_wait(now)
        return True
    return False


def _format_voyage_reward_line(line):
    text = str(line or "").strip().lstrip("-").strip()
    if not text:
        return ""
    if "已自" in text or "呈上收获" in text or text.startswith("【乱星海远航"):
        return ""
    affinity_loss = RE_VOYAGE_AFFINITY_LOSS.search(text)
    if affinity_loss:
        return f"情缘-{_parse_count(affinity_loss.group('amount'))}"
    spirit = RE_VOYAGE_SPIRIT_RESERVE.search(text)
    if spirit:
        return f"蓄灵+{_parse_count(spirit.group('amount'))}"
    text = re.sub(r"\s*([+＋x×])\s*", r"\1", text)
    text = text.replace("＋", "+").replace("×", "x")
    return text


def _format_voyage_result_audit(parsed):
    parsed = parsed if isinstance(parsed, dict) else {}
    partner = str(parsed.get("partner") or state.get("concubine_name") or "侍妾").strip()
    route = str(parsed.get("route") or state.get("concubine_voyage_route") or CONCUBINE_VOYAGE_DEFAULT_ROUTE).strip()
    result = str(parsed.get("result") or "").strip()
    rewards = []
    for line in result.splitlines():
        reward = _format_voyage_reward_line(line)
        if reward:
            rewards.append(reward)
    summary = "、".join(rewards[:8]) if rewards else result.replace("\n", " / ").strip()
    parts = [f"🌸 远航归来：{partner}", route]
    if summary:
        parts.append(summary)
    if state.get("concubine_kind") == "道心侍妾":
        affinity = int(state.get("concubine_affinity", 0) or 0)
        if affinity < CONCUBINE_TIANJI_MIN_AFFINITY:
            parts.append(f"情缘 {affinity}/{CONCUBINE_TIANJI_MIN_AFFINITY}，等待恢复")
    return "｜".join(part for part in parts if part)


async def _send_voyage_result_audit(parsed):
    if not parsed or parsed.get("status") != "idle" or not str(parsed.get("result") or "").strip():
        return False
    await send_audit_log(
        _format_voyage_result_audit(parsed),
        scope="identity",
        send_as_id=get_current_identity_id(),
        limit=480,
        priority="medium",
    )
    return True


def _clear_voyage_snapshot():
    state["concubine_voyage_status"] = ""
    state["concubine_voyage_route"] = ""
    state["concubine_voyage_return_at"] = 0
    state["concubine_voyage_last_result"] = ""
    state["concubine_voyage_last_error"] = ""
    state["concubine_voyage_retry_count"] = 0


def _partner_runtime_snapshot():
    return {key: state.get(key) for key in CONCUBINE_PARTNER_SNAPSHOT_KEYS}


def _restore_partner_runtime_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        return
    for key in CONCUBINE_PARTNER_SNAPSHOT_KEYS:
        if key in snapshot:
            state[key] = snapshot[key]


def _voyage_runtime_snapshot():
    return {
        "phase": _phase() if _phase() in CONCUBINE_VOYAGE_PENDING_PHASES else "",
        "concubine_voyage_msg_id": state.get("concubine_voyage_msg_id", 0),
        "concubine_voyage_status": state.get("concubine_voyage_status", ""),
        "concubine_voyage_route": state.get("concubine_voyage_route", ""),
        "concubine_voyage_return_at": state.get("concubine_voyage_return_at", 0),
        "concubine_voyage_last_result": state.get("concubine_voyage_last_result", ""),
        "concubine_voyage_last_error": state.get("concubine_voyage_last_error", ""),
        "concubine_voyage_retry_count": state.get("concubine_voyage_retry_count", 0),
    }


def _restore_voyage_runtime_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        return False
    current = _voyage_runtime_snapshot()
    restored = dict(current)
    for key in CONCUBINE_VOYAGE_RUNTIME_KEYS:
        restored[key] = snapshot.get(key, current.get(key))
    if state.get("concubine_voyage_enabled") and snapshot.get("phase") in CONCUBINE_VOYAGE_PENDING_PHASES:
        restored["phase"] = snapshot.get("phase")

    persisted = PersistedState({})
    persisted.restore(current)
    persisted.set(restored)
    payload = persisted.snapshot_if_dirty()
    if payload is None:
        return False

    for key in CONCUBINE_VOYAGE_RUNTIME_KEYS:
        state[key] = payload.get(key, state.get(key))
    if payload.get("phase") in CONCUBINE_VOYAGE_PENDING_PHASES:
        _set_phase(payload.get("phase"))
    return True


def _is_voyage_sailing(now):
    if str(state.get("concubine_voyage_status") or "") != "sailing":
        return False
    return_at = float(state.get("concubine_voyage_return_at", 0) or 0)
    return return_at <= 0 or return_at > float(now)


def _is_voyage_return_due(now):
    status = str(state.get("concubine_voyage_status") or "")
    if status == "returned":
        return True
    if status != "sailing":
        return False
    return_at = float(state.get("concubine_voyage_return_at", 0) or 0)
    return return_at > 0 and return_at <= float(now)


def _is_voyage_probe_due(now):
    if str(state.get("concubine_voyage_status") or "") not in {"sailing", "needs_status"}:
        return False
    return_at = float(state.get("concubine_voyage_return_at", 0) or 0)
    if return_at > 0:
        return False
    return float(state.get("next_concubine_time", 0) or 0) <= float(now)


def _is_voyage_return_retry_exhausted(now):
    if int(state.get("concubine_voyage_retry_count", 0) or 0) < 2:
        return False
    status = str(state.get("concubine_voyage_status") or "")
    if status == "returned":
        return True
    if status != "sailing":
        return False
    return_at = float(state.get("concubine_voyage_return_at", 0) or 0)
    return return_at > 0 and return_at <= float(now)


def _has_voyage_runtime_state(now):
    phase = _phase()
    if phase in CONCUBINE_VOYAGE_PENDING_PHASES:
        return True
    return (
        state.get("concubine_voyage_status") == "needs_status"
        or _is_voyage_sailing(now)
        or _is_voyage_return_due(now)
        or _is_voyage_probe_due(now)
        or _is_voyage_return_retry_exhausted(now)
    )




def _schedule_voyage_wait(now):
    return_at = float(state.get("concubine_voyage_return_at", 0) or 0)
    if return_at > now:
        state["next_concubine_time"] = return_at + random.uniform(60, 600)
    else:
        state["next_concubine_time"] = now + CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC
    return state["next_concubine_time"]


def _is_voyage_affinity_eligible(route=None):
    return int(state.get("concubine_affinity", 0) or 0) >= _voyage_min_affinity(route)


def _is_voyage_eligible(now):
    if not state.get("concubine_voyage_enabled"):
        return False
    if not _has_available_partner():
        state["concubine_voyage_last_error"] = "远航需先确认侍妾"
        return False
    if not _is_voyage_affinity_eligible():
        affinity = int(state.get("concubine_affinity", 0) or 0)
        route = _preferred_voyage_route()
        minimum = _voyage_min_affinity(route)
        state["concubine_voyage_last_error"] = f"{route}情缘不足（{affinity}/{minimum}），暂不远航"
        return False
    if _is_voyage_sailing(now) or _is_voyage_return_due(now) or state.get("concubine_voyage_status") == "needs_status":
        return False
    state["concubine_voyage_last_error"] = ""
    return True


def _is_daily_greet_due(now):
    if affinity_actions.finished_today(now, "greet"):
        return False
    if not state.get("concubine_tianji_enabled"):
        return False
    if not _is_star_palace_identity():
        return False
    if not _has_available_partner():
        return False
    if state.get("concubine_kind") != "道心侍妾":
        return False
    affinity = state.get("concubine_affinity")
    if type(affinity) is not int or not 0 <= affinity < CONCUBINE_TIANJI_MIN_AFFINITY:
        return False
    return str(state.get("concubine_last_greet_day") or "") != _local_day_key(now)


def _is_gift_recovery_eligible(now):
    if affinity_actions.finished_today(now):
        return False
    if not state.get("concubine_tianji_enabled"):
        return False
    if not _is_star_palace_identity():
        return False
    if not _has_available_partner():
        return False
    if state.get("concubine_kind") != "道心侍妾":
        return False
    if int(state.get("concubine_affinity", 0) or 0) >= CONCUBINE_TIANJI_MIN_AFFINITY:
        return False
    today = _local_day_key(now)
    if str(state.get("concubine_last_greet_day") or "") != today:
        return False
    if str(state.get("concubine_last_gift_day") or "") == today:
        return False
    return True


def _is_gift_recovery_due(now):
    if not _is_gift_recovery_eligible(now):
        return False
    today = _local_day_key(now)
    if str(state.get("concubine_gift_attempt_day") or "") == today:
        return False
    return True


def _can_use_cached_panel_for_gift_recovery(now):
    if not _is_gift_recovery_eligible(now):
        return False
    return _has_recent_concubine_status_panel(now)


def _has_recent_concubine_status_panel(now):
    if not _has_available_partner():
        return False
    panel_msg_id = _msg_id_int(state.get("concubine_last_panel_msg_id"))
    panel_seen_at = _status_timestamp(state.get("concubine_last_snapshot_at"))
    timestamp = _status_timestamp(now)
    if panel_msg_id <= 0 or panel_seen_at is None or timestamp is None:
        return False
    return timestamp - CONCUBINE_PANEL_REUSE_MAX_AGE_SEC <= panel_seen_at <= timestamp


def _reuse_recent_status_panel(now, reason):
    state["concubine_status_msg_id"] = 0
    if _phase() == "status_pending":
        _set_phase("idle")
    _clear_status_calibration_timeout_errors()
    state["concubine_last_error"] = str(reason or "已复用近期侍妾面板")
    current_next = float(state.get("next_concubine_time", 0) or 0)
    reuse_next = float(now) + random.uniform(CONCUBINE_STATUS_REUSE_DEFER_MIN_SEC, CONCUBINE_STATUS_REUSE_DEFER_MAX_SEC)
    state["next_concubine_time"] = min(current_next, reuse_next) if current_next > float(now) else reuse_next
    _record_concubine_event(
        "复用近期侍妾面板",
        kind="skipped",
        reason="concubine_recent_status_panel_reused",
        phase=_phase(),
        command=CMD_CONCUBINE_STATUS,
        msg_id=int(state.get("concubine_last_panel_msg_id", 0) or 0),
        detail=str(reason or ""),
        workflow_status="reused",
    )


def _can_continue_gift_recovery(now):
    if not _is_gift_recovery_eligible(now):
        return False
    today = _local_day_key(now)
    if str(state.get("concubine_gift_attempt_day") or "") == today:
        return True
    phase = _phase()
    if phase not in CONCUBINE_GIFT_PHASES:
        return False
    return (
        int(state.get("concubine_gift_status_msg_id", 0) or 0) > 0
        or int(state.get("concubine_gift_bag_msg_id", 0) or 0) > 0
        or int(state.get("concubine_gift_msg_id", 0) or 0) > 0
    )


def _phaseful_summary_guard_state(now):
    for phase_key, next_time_key, enabled_key in (
        ("deep_retreat_phase", "next_deep_retreat_time", "deep_retreat_enabled"),
        ("yuanying_phase", "next_yuanying_time", "yuanying_enabled"),
    ):
        if not state.get(enabled_key):
            continue
        phase = str(state.get(phase_key) or "idle")
        if phase in {"observing_summary", "waiting_summary", "post_summary_wait"}:
            return "blocking"
        if phase == "summary_due":
            return "summary_due"
        if phase == "running" and 0 < float(state.get(next_time_key, 0) or 0) <= float(now):
            return "summary_due"
    return ""


def _has_phaseful_summary_window(now, *, allow_replayable_trigger=False):
    guard_state = _phaseful_summary_guard_state(now)
    if not guard_state:
        return False
    if allow_replayable_trigger and guard_state == "summary_due":
        return False
    return True


def _defer_active_for_phaseful_summary(now, action, *, error_key="concubine_last_error", allow_replayable_trigger=False):
    if not _has_phaseful_summary_window(now, allow_replayable_trigger=allow_replayable_trigger):
        return False
    _set_phase("idle")
    _clear_non_heart_pending_msg_ids()
    _schedule_after(now, CONCUBINE_ACTIVE_DEFER_MIN_SEC, CONCUBINE_ACTIVE_DEFER_MAX_SEC)
    state[error_key] = f"{action}等待闭关/元婴结算，稍后处理"
    return True


def _defer_daily_greet_for_phaseful_summary(now):
    return _defer_active_for_phaseful_summary(now, "每日问安", error_key="concubine_greet_last_error")


def _defer_gift_for_phaseful_summary(now):
    return _defer_active_for_phaseful_summary(now, "赠予侍妾", error_key="concubine_gift_last_error")


PHASEFUL_SUMMARY_WAIT_ERROR_SUFFIX = "等待闭关/元婴结算，稍后处理"


def _is_phaseful_summary_wait_error(value):
    return str(value or "").strip().endswith(PHASEFUL_SUMMARY_WAIT_ERROR_SUFFIX)


def _clear_stale_phaseful_summary_wait_errors(now):
    if _has_phaseful_summary_window(now):
        return False
    changed = False
    for key in (
        "concubine_last_error",
        "concubine_tianji_last_error",
        "concubine_greet_last_error",
        "concubine_gift_last_error",
        "concubine_heart_last_error",
        "concubine_voyage_last_error",
    ):
        if _is_phaseful_summary_wait_error(state.get(key)):
            state[key] = ""
            changed = True
    return changed


def _clear_stale_tianji_summary_wait_error():
    if _is_phaseful_summary_wait_error(state.get("concubine_tianji_last_error")):
        state["concubine_tianji_last_error"] = ""


def _schedule_next_daily_greet_check(now):
    due_at = _next_local_day_at(now)
    next_time = float(state.get("next_concubine_time", 0) or 0)
    if next_time <= now or due_at < next_time:
        state["next_concubine_time"] = due_at
    return state["next_concubine_time"]


def _schedule_after_tianji(now):
    if _has_due_action(now):
        return _schedule_chain_action(now)
    due_times = []
    tianji_due_at = float(state.get("concubine_tianji_due_at", 0) or 0)
    if tianji_due_at > now:
        due_times.append(tianji_due_at)
    heart_due_at = float(state.get("concubine_heart_due_at", 0) or 0)
    if state.get("concubine_heart_enabled") and heart_due_at > now:
        due_times.append(heart_due_at)
    dream_due_at = fragment_actions.next_dream_at()
    if state.get("concubine_enabled") and dream_due_at > now:
        due_times.append(dream_due_at)
    if not due_times:
        state["next_concubine_time"] = float(now + random.uniform(60, 600))
        return state["next_concubine_time"]
    state["next_concubine_time"] = min(due_times) + random.uniform(60, 600)
    return state["next_concubine_time"]


def _schedule_affinity_recovery(now):
    if _is_daily_greet_due(now):
        return _schedule_chain_action(now)
    if _is_gift_recovery_due(now):
        return _schedule_chain_action(now)
    if state.get("concubine_enabled"):
        dream_due_at = fragment_actions.next_dream_at()
        if dream_due_at <= now:
            return _schedule_chain_action(now)
        return _schedule_at_due_or_chain(now, dream_due_at)
    return _schedule_after_tianji(now)


def _is_affinity_shortage_error():
    error_text = str(state.get("concubine_tianji_last_error") or "")
    return error_text.startswith("情缘不足") or error_text.startswith("情缘恢复中") or error_text.startswith("无我之境耗尽情缘")


def _mark_tianji_affinity_shortage(now, reason, *, force_affinity_zero=False, infer_low_affinity=False):
    if state.get("concubine_kind") == "道心侍妾":
        current_affinity = int(state.get("concubine_affinity", 0) or 0)
        if force_affinity_zero or (infer_low_affinity and current_affinity >= CONCUBINE_TIANJI_MIN_AFFINITY):
            state["concubine_affinity"] = 0
        elif infer_low_affinity:
            state["concubine_affinity"] = max(0, min(current_affinity, CONCUBINE_TIANJI_MIN_AFFINITY - 1))
    state["concubine_tianji_last_error"] = str(reason or "情缘不足，暂缓天机代卜")
    if state.get("concubine_tianji_enabled") and float(state.get("concubine_tianji_due_at", 0) or 0) <= now:
        state["concubine_tianji_due_at"] = now + CONCUBINE_TIANJI_CD_SEC
    return _schedule_affinity_recovery(now)


def _normalize_tianji_affinity_error(now):
    if not _is_affinity_shortage_error():
        return False
    if _is_tianji_affinity_blocked():
        return False
    state["concubine_tianji_last_error"] = ""
    if state.get("concubine_tianji_enabled"):
        tianji_due_at = float(state.get("concubine_tianji_due_at", 0) or 0)
        if tianji_due_at <= float(now):
            _schedule_chain_action(now)
        else:
            _schedule_after_tianji(now)
    return True


def _backoff_after_pending_timeout(now, phase):
    """Legacy non-heart backoff; owned heart sessions retain their own clocks."""
    retry_at = _schedule_status_recheck(now)
    if phase == "status_pending":
        if state.get("concubine_enabled") and float(state.get("concubine_dream_due_at", 0) or 0) <= now:
            state["concubine_dream_due_at"] = retry_at
        if state.get("concubine_tianji_enabled") and float(state.get("concubine_tianji_due_at", 0) or 0) <= now:
            state["concubine_tianji_due_at"] = retry_at
    elif phase in CONCUBINE_GIFT_PHASES:
        state["concubine_last_gift_day"] = _local_day_key(now)
        state["concubine_gift_last_error"] = f"{phase} 等待回复超时，今日不再赠予"
        state["concubine_gift_amount"] = 0
    elif phase in {"fragment_pending", "puzzle_pending"}:
        _clear_fragment_confirmation()
    return retry_at


async def _apply_concubine_resource_backoff(now, action_key, due_key, error_key, label, raw_text):
    backoff = record_resource_shortage(action_key, now, reason=raw_text)
    due_at = float(backoff.get("next_at", 0) or 0)
    state[due_key] = due_at
    state[error_key] = f"{label}资源不足: {str(raw_text or '')[:80]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _schedule_at_due_or_chain(now, due_at)
    await send_audit_log(
        f"⚠️ {label}资源不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}",
        scope="identity",
        limit=220,
    )


def _is_current_reply(reply_to, state_key):
    expected_msg_id = int(state.get(state_key, 0) or 0)
    reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0)
    if expected_msg_id <= 0:
        return True
    if reply_to_msg_id <= 0:
        return False
    return reply_to_msg_id == expected_msg_id


def _msg_id_int(value):
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _payload_matches_game_topic(payload):
    try:
        game_topic_id = int(get_game_topic_id() or 0)
    except (TypeError, ValueError):
        game_topic_id = 0
    if game_topic_id <= 0:
        return True
    if not isinstance(payload, dict) or "topic_id" not in payload:
        return True
    topic_id = _msg_id_int(payload.get("topic_id"))
    if topic_id == game_topic_id:
        return True
    if topic_id > 0:
        return False
    reply_to_msg_id = _msg_id_int(payload.get("reply_to_msg_id"))
    if reply_to_msg_id == game_topic_id:
        return True
    if reply_to_msg_id > 0:
        return False
    return True


def _sender_matches_current_identity(sender_id):
    current_id = int(get_current_identity_id() or 0)
    try:
        sender_id = int(sender_id or 0)
    except (TypeError, ValueError):
        return False
    if current_id <= 0 or sender_id == 0:
        return False
    if sender_id == current_id:
        return True
    if sender_id < 0:
        sender_abs = str(abs(sender_id))
        if sender_abs.startswith("100"):
            try:
                return int(sender_abs[3:] or 0) == current_id
            except ValueError:
                return False
    return False


def _parse_message_log_ts(raw_ts):
    ts_text = str(raw_ts or "").strip()
    if not ts_text:
        return 0.0
    ts_text = ts_text.replace(" UTC+8", "")
    try:
        return datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except ValueError:
        return 0.0


def _iter_message_log_entries_between(start_ts, end_ts):
    try:
        start_day = datetime.fromtimestamp(float(start_ts), TZ_LOCAL).date()
        end_day = datetime.fromtimestamp(float(end_ts), TZ_LOCAL).date()
    except (TypeError, ValueError, OSError):
        return

    day = start_day
    while day <= end_day:
        log_file = os.path.join(MESSAGES_DIR, f"{day.isoformat()}.log")
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass
        day += timedelta(days=1)


def _concubine_family_for_command(command):
    command_text = str(command or "").strip()
    if command_text == CMD_CONCUBINE_STATUS:
        return "concubine_status"
    if command_text == CMD_CONCUBINE_DAILY_GREET:
        return "concubine_greet"
    if command_text.startswith(CMD_CONCUBINE_GIFT_STONE):
        return "concubine_gift"
    if command_text == CMD_CONCUBINE_DREAM:
        return "concubine_dream"
    if command_text == CMD_CONCUBINE_FRAGMENT:
        return "concubine_fragment"
    if command_text == CMD_CONCUBINE_PUZZLE:
        return "concubine_puzzle"
    if command_text in CONCUBINE_REACQUIRE_COMMANDS:
        return "concubine_reacquire"
    if command_text == CMD_CONCUBINE_TIANJI:
        return "concubine_tianji"
    if command_text == CMD_CONCUBINE_HEART:
        return "concubine_heart"
    if command_text == CMD_CONCUBINE_HEART_STEADY:
        return "concubine_heart"
    if command_text == CMD_STORAGE_BAG:
        return "concubine_storage_bag"
    return "concubine"


def _resolve_heart_panel_anchor(now):
    return heart_actions.panel_anchor(now)


def _record_concubine_event(
    event,
    *,
    kind="skipped",
    reason="",
    phase="",
    state_key="",
    reply_to=None,
    current_msg_id=0,
    detail="",
    family="",
    command="",
    msg_id=0,
    matched_text="",
    decision="",
    route_source="concubine",
    workflow_status="",
):
    try:
        event_text = str(event or "侍妾事件").strip() or "侍妾事件"
        parts = [event_text]
        phase_text = str(phase or _phase() or "").strip()
        if phase_text:
            parts.append(f"phase={phase_text}")
        expected_msg_id = _msg_id_int(state.get(state_key, 0)) if state_key else 0
        reply_to_msg_id = _msg_id_int(getattr(reply_to, "id", 0))
        current_msg_id = _msg_id_int(current_msg_id)
        msg_id = _msg_id_int(msg_id)
        family = str(family or "").strip() or _concubine_family_for_command(command)
        if expected_msg_id:
            parts.append(f"expected_msg_id={expected_msg_id}")
        if reply_to_msg_id:
            parts.append(f"reply_to_msg_id={reply_to_msg_id}")
        if current_msg_id:
            parts.append(f"current_msg_id={current_msg_id}")
        if msg_id:
            parts.append(f"msg_id={msg_id}")
        if command:
            parts.append(str(command).strip())
        if detail:
            parts.append(str(detail).strip())
        identity_id = get_current_identity_id()
        workflow_log.append_workflow_event(
            "concubine",
            op_id=f"{identity_id}:{phase_text}" if identity_id and phase_text else "",
            step=phase_text,
            event=event_text,
            status=workflow_status or kind,
            identity_id=identity_id,
            msg_id=msg_id or current_msg_id,
            reply_to_msg_id=reply_to_msg_id,
            family=family,
            command=command,
            text=matched_text,
            decision=decision or event_text,
            detail={
                "reason": reason,
                "state_key": state_key,
                "expected_msg_id": expected_msg_id,
                "current_msg_id": current_msg_id,
                "detail": detail,
            },
            route_source=route_source,
            state_after=phase_text,
        )
        from . import passive_inbox

        return passive_inbox.record_passive_inbox_event(
            kind,
            module="concubine",
            identity_id=identity_id,
            reason=reason,
            summary="｜".join(part for part in parts if part),
            family=family,
            msg_id=msg_id or current_msg_id,
            reply_to_msg_id=reply_to_msg_id,
            route_source=route_source,
            matched_text=matched_text,
            decision=decision or event_text,
            state_after=phase_text,
            command=command,
        )
    except Exception:
        return False


def _record_concubine_ignored_reply(label, *, reason="concubine_reply_ignored", phase="", state_key="", reply_to=None, current_msg_id=0, detail=""):
    return _record_concubine_event(
        f"忽略{label}回复",
        kind="skipped",
        reason=reason,
        phase=phase,
        state_key=state_key,
        reply_to=reply_to,
        current_msg_id=current_msg_id,
        detail=detail,
    )


def _is_heavenly_ban_text(text):
    return heavenly_ban_mod.is_heavenly_ban_text(text)


def _is_strong_dream_terminal_text(text):
    raw_text = str(text or "")
    return (
        _is_dream_cooldown_text(raw_text)
        or _is_heavenly_ban_text(raw_text)
        or "修为不足，共梦寻图" in raw_text
        or "【入梦寻图】" in raw_text
        or ("【全群异闻·" in raw_text and "残图】" in raw_text)
        or _is_voyage_lock_text(raw_text)
        or ("尚无侍妾" in raw_text and "共梦寻图" in raw_text)
    )


def _is_phaseful_summary_text(text):
    compact_text = RE_WHITESPACE.sub("", str(text or ""))
    return (
        ("天道感应：检测到" in compact_text and "神魂正在归位" in compact_text)
        or "深度闭关总结" in compact_text
        or "元神归窍总结" in compact_text
        or "元婴闭关结算" in compact_text
        or ("元神回响" in compact_text and "神游归来" in compact_text and "清点收获" in compact_text)
    )


def _is_concubine_candidate_text_for_phase(text, phase):
    raw_text = str(text or "")
    if not raw_text:
        return False
    if phase == "status_pending":
        return "侍妾" in raw_text or "红尘道侣" in raw_text or "道心侍妾" in raw_text or _is_no_partner_text(raw_text)
    if phase == "dream_pending":
        return (
            _is_strong_dream_terminal_text(raw_text)
            or "入梦寻图" in raw_text
            or "残图" in raw_text
            or "掉落率" in raw_text
        )
    if phase == "greet_pending":
        return "问安" in raw_text or "情缘增加" in raw_text or _is_no_partner_text(raw_text) or _is_phaseful_summary_text(raw_text) or _is_voyage_lock_text(raw_text)
    if phase == "gift_status_pending":
        return "侍妾" in raw_text or "情缘值" in raw_text or _is_no_partner_text(raw_text)
    if phase == "gift_bag_pending":
        return "储物袋" in raw_text or "灵石" in raw_text or "空空如也" in raw_text
    if phase == "gift_pending":
        return "赠予了侍妾" in raw_text or "赠予侍妾" in raw_text or "灵石不足" in raw_text or "情缘增加" in raw_text or _is_voyage_lock_text(raw_text)
    if phase == "heart_choice_reply_pending":
        return (
            "【坠魔心劫·第1轮已定】" in raw_text
            or "【坠魔心劫·第2轮已定】" in raw_text
            or "【坠魔心劫·结算】" in raw_text
            or _is_heart_anchor_lost_text(raw_text)
            or "心劫余波" in raw_text
            or "心劫抉择正在进行" in raw_text
            or _is_voyage_lock_text(raw_text)
        )
    if phase in {"heart_pending", "heart_choice_pending"}:
        return (
            "坠魔心劫" in raw_text
            or "共历心劫" in raw_text
            or _is_heart_anchor_lost_text(raw_text)
            or "心劫余波" in raw_text
            or "心劫抉择正在进行" in raw_text
            or "开启共历心劫" in raw_text
            or _is_voyage_lock_text(raw_text)
        )
    if phase == "voyage_pending":
        return (
            "乱星海远航" in raw_text
            or "侍妾远航" in raw_text
            or "远航" in raw_text
            or "归航" in raw_text
            or "开启远航需要" in raw_text
            or "无可结算的远航任务" in raw_text
        )
    if phase == "voyage_return_pending":
        return (
            "乱星海远航·归" in raw_text
            or "远航" in raw_text
            or "归航" in raw_text
            or "已自" in raw_text
            or "还需" in raw_text
            or "尚未归航" in raw_text
            or "无可结算的远航任务" in raw_text
        )
    if phase == "fragment_pending":
        return "残图" in raw_text or "拼片" in raw_text or _is_voyage_lock_text(raw_text)
    if phase == "puzzle_pending":
        return "拼图" in raw_text or "虚天" in raw_text or "苍坤" in raw_text or "残图" in raw_text or _is_voyage_lock_text(raw_text)
    return False


def _candidate_mentions_identity_or_partner(text):
    raw_text = str(text or "")
    if _text_matches_current_identity(raw_text):
        return True
    partner_name = str(state.get("concubine_name") or "").strip()
    return bool(partner_name and partner_name in raw_text)


def _read_recent_message_log_candidates(now, phase):
    log_file = os.path.join(MESSAGES_DIR, f"{datetime.fromtimestamp(float(now), TZ_LOCAL).strftime('%Y-%m-%d')}.log")
    if not os.path.exists(log_file):
        return []
    start = float(now) - CONCUBINE_TIMEOUT_CANDIDATE_LOOKBACK_SEC
    candidates = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()[-CONCUBINE_TIMEOUT_CANDIDATE_MAX_LINES:]
    except OSError:
        return []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not _payload_matches_game_topic(payload):
            continue
        if payload.get("event_type") not in {"message", "edit"}:
            continue
        event_ts = _parse_message_log_ts(payload.get("ts"))
        if event_ts <= 0:
            continue
        if event_ts < start or event_ts > float(now) + 5:
            continue
        text = str(payload.get("text") or "")
        if not _is_concubine_candidate_text_for_phase(text, phase):
            continue
        if not _candidate_mentions_identity_or_partner(text):
            continue
        candidates.append(payload)
    return candidates[-3:]


async def _audit_pending_timeout_candidates(now, phase):
    if phase == "status_pending":
        return
    candidates = _read_recent_message_log_candidates(now, phase)
    if not candidates:
        return
    parts = []
    for item in candidates:
        text = str(item.get("text") or "").replace("\n", " ")
        parts.append(f"{item.get('message_id')}: {text[:90]}")
    await send_audit_log(
        f"🌸 侍妾 {phase} 超时旁路观察：发现疑似未匹配回复，仅记录不接管｜" + "｜".join(parts),
        scope="identity",
        limit=360,
    )


def _find_observed_status_query_reply(now, phase):
    owner = _status_query_owner()
    root = _query_int(state.get(CONCUBINE_QUERY_KEYS[phase.removesuffix("_pending")]))
    pending = state.get("pending_tasks")
    if _query_time(now) is None or not _owns_status_query(owner) or root <= 0 or not isinstance(pending, dict):
        return None
    refs = [_status_query_pending_ref(key, item) for key, item in pending.items() if isinstance(item, dict)]
    refs = [ref for ref in refs if ref and ref[1] == root and ref[0] in get_game_group_ids()]
    if len(refs) != 1:
        return None
    chat = refs[0][0]
    start = max(0, now - CONCUBINE_LOG_REPLAY_LOOKBACK_SEC)
    rows = [row for row in _iter_message_log_entries_between(start, now)
            if isinstance(row, dict) and _query_int(row.get("chat_id")) == chat
            and row.get("event_type") in ("message", "edit")
            and start <= _parse_message_log_ts(row.get("ts")) <= now]
    commands = [row for row in rows if _query_int(row.get("message_id")) == root]
    if not commands:
        return None
    original = commands[0]
    original_at = _query_time(original.get("server_event_at"))
    actor = _query_int(original.get("sender_id"))
    if (original_at is None or not start <= original_at <= now
            or any(row.get("event_type") != "message"
                   or (row.get("sender_is_bot") is not False and not (actor < 0 and "sender_is_bot" not in row))
                   or row.get("text") != CMD_CONCUBINE_STATUS
                   or row.get("message_edited") is not False or row.get("forwarded") is not False
                   or row.get("fwd_from") or row.get("edit_date")
                   or _query_int(row.get("sender_id")) != actor
                   or _query_time(row.get("server_event_at")) != original_at
                   or ("account_id" in row and _query_int(row["account_id"]) != owner[2])
                   for row in commands)):
        return None
    reply_ids = {_query_int(row.get("message_id")) for row in rows if _query_int(row.get("reply_to_msg_id")) == root} - {0}
    replies = [row for row in rows if _query_int(row.get("message_id")) in reply_ids]
    if not replies or any(_query_time(row.get("server_event_at")) is None for row in replies):
        return None
    # Select the latest revision before validating it; an invalid edit must not
    # expose an older good-looking panel from the same read.
    last = max(replies, key=lambda row: (row["server_event_at"], _query_int(row.get("message_id"))))
    fact_keys = ("text", "sender_id", "sender_is_bot", "reply_to_msg_id", "account_id",
                 "forwarded", "message_edited", "fwd_from", "edit_date")
    if (any(row["server_event_at"] == last["server_event_at"] and row.get("message_id") == last.get("message_id")
            and any(row.get(key) != last.get(key) for key in fact_keys) for row in replies)
            or last.get("sender_is_bot") is not True or last.get("forwarded") is not False or last.get("fwd_from")
            or type(last.get("message_edited")) is not bool
            or _query_int(last.get("reply_to_msg_id")) != root
            or ("account_id" in last and _query_int(last["account_id"]) != owner[2])
            or last["server_event_at"] > now):
        return None
    parent = SimpleNamespace(id=root, chat_id=chat, sender_id=original.get("sender_id"),
                             raw_text=original["text"], server_event_at=original_at, edit_date=None)
    context = {"send_as_id": owner[0], "account_id": owner[2], "chat_id": chat,
               "family": "concubine_status", "root_msg_id": root, "reply_to_msg_id": root,
               "reply_to_command": original["text"], "reply_to_server_at": original_at,
               "reply_to_sender_id": original.get("sender_id"), "reply_to_command_edited": False,
               "sender_id": last.get("sender_id"), "event_type": last["event_type"],
               "server_event_at": last["server_event_at"]}
    record = _observed_status_query_source(parent, owner, last["server_event_at"], last.get("message_id"), chat, context)
    if record is None or _parse_query_reply(record, last.get("text"), last["server_event_at"]) is None:
        return None
    key, valid = _observed_status_query_pending(record)
    return (last, parent, context) if valid and key is not None else None


def _pending_log_replay_spec(phase):
    if phase == "gift_bag_pending":
        return {
            "state_key": "concubine_gift_bag_msg_id",
            "command": CMD_STORAGE_BAG,
            "family": "storage_bag",
            "handler": handle_concubine_storage_bag_reply,
        }
    if phase == "gift_pending":
        return {
            "state_key": "concubine_gift_msg_id",
            "command": CMD_CONCUBINE_GIFT_STONE,
            "family": "concubine_gift",
            "handler": handle_concubine_gift_reply,
        }
    return None


def _find_logged_pending_reply(now, phase):
    spec = _pending_log_replay_spec(phase)
    if not spec:
        return None
    expected_msg_id = _msg_id_int(state.get(spec["state_key"]))
    if expected_msg_id <= 0:
        return None

    end_ts = float(now or 0) + CONCUBINE_LOG_REPLAY_LOOKAHEAD_SEC
    start_ts = max(0.0, end_ts - CONCUBINE_LOG_REPLAY_LOOKBACK_SEC)
    found = None
    for payload in _iter_message_log_entries_between(start_ts, end_ts):
        if not _payload_matches_game_topic(payload):
            continue
        if payload.get("event_type") not in {"message", "edit"}:
            continue
        if spec.get("match_message_id"):
            if _msg_id_int(payload.get("message_id")) != expected_msg_id:
                continue
        elif _msg_id_int(payload.get("reply_to_msg_id")) != expected_msg_id:
            continue
        event_ts = _parse_message_log_ts(payload.get("ts"))
        if event_ts <= 0 or event_ts < start_ts or event_ts > end_ts:
            continue
        text = str(payload.get("text") or "")
        if not _is_concubine_candidate_text_for_phase(text, phase):
            continue
        found = {
            "ts": event_ts,
            "message_id": _msg_id_int(payload.get("message_id")),
            "reply_to_msg_id": expected_msg_id,
            "text": text,
            "spec": spec,
        }
    return found


def _find_recent_logged_sent_command(now, command, *, lookback_sec=None):
    command_text = str(command or "").strip()
    if not command_text:
        return None
    try:
        lookback = float(lookback_sec or (CONCUBINE_DUE_SCAN_SEND_QUEUE_TIMEOUT_SEC + 30))
    except (TypeError, ValueError):
        lookback = CONCUBINE_DUE_SCAN_SEND_QUEUE_TIMEOUT_SEC + 30
    end_ts = float(now or 0) + CONCUBINE_LOG_REPLAY_LOOKAHEAD_SEC
    start_ts = max(0.0, float(now or 0) - max(5.0, lookback))
    expected_family = _concubine_family_for_command(command_text)
    found = None
    for payload in _iter_message_log_entries_between(start_ts, end_ts):
        if not _payload_matches_game_topic(payload):
            continue
        event_ts = _parse_message_log_ts(payload.get("ts"))
        if event_ts <= 0 or event_ts < start_ts or event_ts > end_ts:
            continue
        event_type = str(payload.get("event_type") or "").strip()
        if event_type not in {"sent", "message"}:
            continue
        if str(payload.get("text") or "").strip() != command_text:
            continue
        if not _sender_matches_current_identity(payload.get("sender_id")):
            continue
        payload_family = str(payload.get("family") or "").strip()
        if payload_family and expected_family and payload_family != expected_family:
            continue
        msg_id = _msg_id_int(payload.get("message_id"))
        if msg_id <= 0:
            continue
        if found is None or event_ts > float(found.get("ts", 0) or 0):
            found = {
                "ts": event_ts,
                "message_id": msg_id,
                "event_type": event_type,
                "family": payload_family or expected_family,
            }
    return found


async def _recover_sent_command_after_empty_send(now, phase, command, state_key, *, label, decision):
    logged = _find_recent_logged_sent_command(now, command)
    if not logged:
        return False
    event_ts = float(logged.get("ts") or now)
    msg_id = _msg_id_int(logged.get("message_id"))
    if msg_id <= 0:
        return False
    _set_phase(phase)
    state[state_key] = msg_id
    state["next_concubine_time"] = max(event_ts + CONCUBINE_PHASE_TIMEOUT_SEC, float(now or 0) + 10)
    _record_concubine_event(
        f"{label}发送回捞",
        kind="changed",
        reason="logged_sent_after_empty_send",
        phase=phase,
        command=command,
        msg_id=msg_id,
        detail=f"event_type={logged.get('event_type')}｜sent_at={fmt_abs_ts(event_ts)}",
        decision=decision,
        workflow_status="pending",
    )
    if await _recover_concubine_pending_from_message_log(now, phase):
        return True
    save_state()
    return True




async def _recover_concubine_pending_from_message_log(now, phase):
    if phase in {"status_pending", "gift_status_pending"}:
        query, owner = _status_query_record(), _status_query_owner()
        if query is None or (query and query["status"] in CONCUBINE_QUERY_UNRESOLVED):
            return False
        observation = _find_observed_status_query_reply(now, phase)
        if observation is None:
            return False
        reply, parent, context = observation
        result = await _handle_observed_status_query_reply(
            query, reply["text"], now, parent, context["server_event_at"],
            reply["message_id"], context["chat_id"], context,
        )
        if result == "complete" and _owns_status_query(owner):
            try:
                await send_audit_log(f"🌸 侍妾日志补偿：{phase} 已保存核验后的状态（msg_id={reply['message_id']}）。",
                                     scope="identity", send_as_id=owner[0], limit=220)
            except Exception as exc:
                console_log(f"侍妾查询已保存，通知失败 ({type(exc).__name__})")
        # Only a failed commit holds the read; stale/blocked evidence is not a retry.
        return result in {"complete", "save_failed"}
    logged_reply = _find_logged_pending_reply(now, phase)
    if not logged_reply:
        return False
    spec = logged_reply["spec"]
    before_phase = _phase()
    before_next = float(state.get("next_concubine_time", 0) or 0)
    reply_to = SimpleNamespace(raw_text=spec["command"], id=logged_reply["reply_to_msg_id"])
    handler = spec["handler"]
    event_ts = float(logged_reply["ts"] or now)
    if spec.get("current_msg_id"):
        handled = await handler(
            logged_reply["text"],
            event_ts,
            reply_to,
            matched_family=spec["family"],
            current_msg_id=logged_reply["message_id"],
        )
    else:
        handled = await handler(
            logged_reply["text"],
            event_ts,
            reply_to,
            matched_family=spec["family"],
        )
    state_changed = _phase() != before_phase or float(state.get("next_concubine_time", 0) or 0) != before_next
    if not handled and not state_changed:
        return False
    await send_audit_log(
        f"🌸 侍妾日志补偿：{phase} 已按真实回复接管（msg_id={logged_reply['message_id']}）。",
        scope="identity",
        limit=220,
    )
    return True


def _normalize_identity_text(text):
    return RE_WHITESPACE.sub("", str(text or "").strip().lstrip("@")).casefold()


def _text_matches_current_identity(text):
    raw_text = str(text or "")
    compact_text = _normalize_identity_text(raw_text)
    mentioned_tags = {
        _normalize_identity_text(tag)
        for tag in RE_IDENTITY_TAG.findall(raw_text)
        if str(tag or "").strip()
    }
    for raw_tag in get_send_as_tags():
        tag = str(raw_tag or "").strip().lstrip("@")
        if not tag:
            continue
        normalized_tag = _normalize_identity_text(tag)
        if mentioned_tags:
            if normalized_tag in mentioned_tags:
                return True
            continue
        if len(normalized_tag) >= 3 and normalized_tag in compact_text:
            return True
    return False


def _parse_wait_due_at(raw_text, now, *, coarse_minute_buffer=False):
    text = str(raw_text or "").strip()
    if text in {"可施展", "可用"}:
        return 0.0
    matched = RE_CONCUBINE_STATUS_WAIT.fullmatch(text) if len(text) <= 64 else None
    if not matched or not any(matched.groups()):
        return None
    wait_sec = sum(int(matched.group(key) or 0) * scale for key, scale in (
        ("hours", 3600), ("minutes", 60), ("seconds", 1),
    ))
    minute_buffer = 60 if coarse_minute_buffer and matched.group("seconds") is None else 0
    return _status_timestamp(now + wait_sec + CD_BUFFER_SEC + minute_buffer)


def _parse_tianji_chain(raw_text, now):
    text = str(raw_text or "").strip()
    if text == "无":
        return "", 0.0
    matched = RE_TIANJI_CHAIN_REMAINING.fullmatch(text)
    if not matched:
        return None
    name = matched.group("name").strip()
    wait_text = matched.group("wait").strip()
    due_at = _parse_wait_due_at(wait_text, now)
    if not name or due_at is None or due_at <= 0:
        return None
    return name, due_at


def _is_tianji_timeout_calibration_error(value):
    text = str(value or "")
    return "tianji_pending 等待回复超时" in text or "天机代卜等待回复超时" in text


def _clear_expired_tianji_chain(now):
    chain = str(state.get("concubine_tianji_chain") or "").strip()
    chain_due_at = float(state.get("concubine_tianji_chain_due_at", 0) or 0)
    if not chain and chain_due_at <= 0:
        return False
    if chain and chain_due_at > float(now or 0):
        return False
    state["concubine_tianji_chain"] = ""
    state["concubine_tianji_chain_due_at"] = 0
    return True


def _normalize_fragment_kind(raw_kind):
    text = str(raw_kind or "").strip().lower()
    if text == DREAM_KIND_CANGKUN or "苍坤" in text:
        return DREAM_KIND_CANGKUN
    return DREAM_KIND_XUTIAN


def _coerce_fragment_parts(count, total):
    try:
        return max(0, int(count)), max(1, int(total))
    except (TypeError, ValueError):
        return None


def _get_fragment_progress(kind):
    normalized_kind = _normalize_fragment_kind(kind)
    count_key, total_key = FRAGMENT_FIELDS[normalized_kind]
    count_default = state.get("concubine_fragment_count", 0) if normalized_kind == DREAM_KIND_XUTIAN else 0
    total_default = state.get("concubine_fragment_total", 4) if normalized_kind == DREAM_KIND_XUTIAN else 4
    count = int(state.get(count_key, count_default) or 0)
    total = int(state.get(total_key, total_default) or 4)
    return max(0, count), max(1, total)


def _set_fragment_progress(kind, count, total):
    normalized_kind = _normalize_fragment_kind(kind)
    parts = _coerce_fragment_parts(count, total)
    if not parts:
        return False
    count, total = parts
    count_key, total_key = FRAGMENT_FIELDS[normalized_kind]
    state[count_key] = count
    state[total_key] = total
    if normalized_kind == DREAM_KIND_XUTIAN:
        state["concubine_fragment_count"] = count
        state["concubine_fragment_total"] = total
    _clear_stale_fragment_confirmation()
    return True


def _clear_fragment_progress(kind=None):
    if kind:
        _set_fragment_progress(kind, 0, 4)
        return
    for fragment_kind in FRAGMENT_KIND_ORDER:
        _set_fragment_progress(fragment_kind, 0, 4)
    state["concubine_fragment_count"] = 0
    state["concubine_fragment_total"] = 4


def _parse_fragment_progresses(text):
    raw_text = str(text or "")
    progresses = {}
    for matched in RE_FRAGMENT_TYPED_PROGRESS.finditer(raw_text):
        parts = _coerce_fragment_parts(matched.group("count"), matched.group("total"))
        if parts:
            progresses[_normalize_fragment_kind(matched.group("kind"))] = parts

    context_match = RE_FRAGMENT_CONTEXT_KIND.search(raw_text)
    context_kind = _normalize_fragment_kind(context_match.group("kind")) if context_match else ""
    for pattern in (RE_FRAGMENT_PROGRESS, RE_DREAM_BROADCAST_PROGRESS):
        for matched in pattern.finditer(raw_text):
            parts = _coerce_fragment_parts(matched.group(1), matched.group(2))
            if not parts:
                continue
            snippet_start = max(0, matched.start() - 24)
            snippet = raw_text[snippet_start:matched.end()]
            kind = _normalize_fragment_kind(snippet) if ("虚天" in snippet or "苍坤" in snippet) else context_kind or DREAM_KIND_XUTIAN
            progresses[kind] = parts
    return progresses


def _parse_fragment_panel(text):
    raw_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    headings = list(RE_FRAGMENT_PANEL_HEAD.finditer(raw_text))
    labels = {label: kind for kind, label in FRAGMENT_LABELS.items()}
    if len(headings) != len(labels) or {match["kind"] for match in headings} != labels.keys():
        return None
    owner = RE_FRAGMENT_PANEL_OWNER.fullmatch(raw_text[:headings[0].start()].strip())
    if not owner or not owner["name"].strip():
        return None

    progresses = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(raw_text)
        fields = {}
        for field in RE_FRAGMENT_PANEL_FIELD.finditer(raw_text[heading.end():end]):
            if field["label"] in fields:
                return None
            fields[field["label"]] = field["value"].strip()
        if fields.keys() != {"拼片进度", "已收集", "缺失残纹"}:
            return None
        progress = re.fullmatch(r"([0-4])[ \t]*/[ \t]*4", fields["拼片进度"])
        if not progress:
            return None
        count = int(progress[1])
        pieces = []
        for label, size in (("已收集", count), ("缺失残纹", 4 - count)):
            value = fields[label]
            items = [] if value == "无" else [item.strip() for item in re.split(r"[、,，]", value)]
            if len(items) != size or len(set(items)) != size or any(not item or len(item) > 120 for item in items):
                return None
            pieces.append(set(items))
        if pieces[0] & pieces[1]:
            return None
        progresses[labels[heading["kind"]]] = (count, 4)
    return {"partner": owner["name"].strip(), "progresses": progresses}


def _confirmed_completed_fragment_kinds_from_reply(text):
    parsed = _parse_fragment_panel(text)
    if not parsed:
        return []
    return [kind for kind in FRAGMENT_KIND_ORDER if parsed["progresses"][kind] == (4, 4)]


def _apply_fragment_progresses(progresses):
    applied = {}
    for kind, progress in (progresses or {}).items():
        if not progress:
            continue
        if _set_fragment_progress(kind, progress[0], progress[1]):
            applied[_normalize_fragment_kind(kind)] = _coerce_fragment_parts(progress[0], progress[1])
    return applied


def _is_fragment_ready(kind):
    count, total = _get_fragment_progress(kind)
    return total > 0 and count >= total


def _completed_fragment_kinds():
    return [kind for kind in FRAGMENT_KIND_ORDER if _is_fragment_ready(kind)]


def _fragment_confirmation_key():
    parts = []
    for kind in _completed_fragment_kinds():
        count, total = _get_fragment_progress(kind)
        parts.append(f"{kind}:{count}/{total}")
    return "|".join(parts)


def _clear_fragment_confirmation():
    state["concubine_fragment_confirm_key"] = ""
    state["concubine_fragment_confirmed_at"] = 0


def _clear_stale_fragment_confirmation():
    current_key = _fragment_confirmation_key()
    confirmed_key = str(state.get("concubine_fragment_confirm_key") or "")
    if confirmed_key and confirmed_key != current_key:
        _clear_fragment_confirmation()


def _mark_fragment_confirmation(now):
    key = _fragment_confirmation_key()
    if not key:
        _clear_fragment_confirmation()
        return ""
    state["concubine_fragment_confirm_key"] = key
    state["concubine_fragment_confirmed_at"] = float(now or 0)
    return key


def _is_current_fragment_confirmed(now=None):
    key = _fragment_confirmation_key()
    at = _query_time(state.get("concubine_fragment_confirmed_at"))
    now = _query_time(time.time() if now is None else now)
    record = _status_query_record()
    return bool(key and key == str(state.get("concubine_fragment_confirm_key") or "")
                and at is not None and now is not None and 0 <= now - at <= CONCUBINE_PANEL_REUSE_MAX_AGE_SEC
                and record and record["kind"] == "fragment" and record["status"] == "complete"
                and record.get("outcome") == "panel" and record.get("confirmation_key") == key
                and record["account_id"] == get_identity_account(record["identity_id"])
                and at == record["reply_at"] and _current_partner_matches(record["partner"]))


def _format_fragment_progresses(progresses=None):
    use_state = progresses is None
    progress_map = progresses or {}
    parts = []
    for kind in FRAGMENT_KIND_ORDER:
        if kind in progress_map:
            count, total = progress_map[kind]
        elif use_state:
            count, total = _get_fragment_progress(kind)
        else:
            continue
        parts.append(f"{FRAGMENT_LABELS[kind]}{count}/{total}")
    return "，".join(parts)


def _format_completed_fragment_progresses():
    completed = {kind: _get_fragment_progress(kind) for kind in _completed_fragment_kinds()}
    return _format_fragment_progresses(completed) if completed else ""


def _current_partner_matches(name):
    expected_name = str(state.get("concubine_name") or "").strip()
    actual_name = str(name or "").strip()
    return bool(expected_name and actual_name and expected_name == actual_name)


def _is_permanent_moon_partner(name=None):
    partner_name = str(name if name is not None else state.get("concubine_name") or "").strip()
    return partner_name == "南宫婉" or partner_name.startswith("南宫婉·")


def _parse_gift_success(text):
    matched = RE_CONCUBINE_GIFT_SUCCESS.search(str(text or ""))
    if not matched:
        return None
    return {
        "stone": _parse_count(matched.group("stone")),
        "name": matched.group("name").strip(),
        "amount": _parse_count(matched.group("amount")),
    }


def _is_selfless_affinity_depletion_text(text):
    raw_text = str(text or "")
    return (
        "【无我之境】" in raw_text
        and "侍妾" in raw_text
        and "耗尽与你的所有情缘" in raw_text
        and "挡下此劫" in raw_text
    )


def is_concubine_affinity_event_candidate(text):
    raw_text = str(text or "")
    if "【月殿因果" in raw_text and "南宫婉入世" in raw_text and "初始情缘" in raw_text:
        return True
    return "侍妾" in raw_text and "情缘" in raw_text and (
        "情缘增加了" in raw_text or _is_selfless_affinity_depletion_text(raw_text)
    )


def _is_no_partner_text(text):
    raw_text = str(text or "")
    return (
        "尚无红颜知己" in raw_text
        or "尚未被指派道心侍妾" in raw_text
        or "尚无侍妾" in raw_text
        or "没有侍妾" in raw_text
        or ("无侍妾" in raw_text and "无法共梦寻图" in raw_text)
    )


def _is_partner_not_eligible_text(text):
    raw_text = str(text or "")
    return "尚未筑基" in raw_text or "根基不稳" in raw_text


def _is_partner_manual_repair_text(text):
    raw_text = str(text or "")
    return "数据已损坏" in raw_text or "联系管理员" in raw_text or ".admin 补赐侍妾" in raw_text


def _is_tianji_resource_shortage_text(text):
    raw_text = str(text or "")
    return bool(RE_TIANJI_XIUWEI_SHORTAGE.search(raw_text)) or (
        "修为不足" in raw_text
        and "代卜天机" in raw_text
        and "消耗" in raw_text
    )


def _is_dream_cooldown_text(text):
    raw_text = str(text or "")
    return (
        "梦图感应尚未重启" in raw_text
        or "神念尚在恢复" in raw_text
        or "无法再次强行入梦" in raw_text
    )


def _parse_status_panel(text, now):
    # Tianjige command-center replies may wrap the same Telegram panel fields
    # in Markdown emphasis. Normalize decoration before applying the existing
    # business parser so the HTTP and Telegram paths share one reducer.
    raw_text = re.sub(r"[*_`]+", "", str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    observed_at = _status_timestamp(now)
    if observed_at is None:
        return None
    headers = list(RE_CONCUBINE_HEAD.finditer(raw_text))
    if not headers:
        if _is_no_partner_text(raw_text):
            return {
                "has_partner": False,
                "observed_at": observed_at,
                "not_eligible": _is_partner_not_eligible_text(raw_text),
                "manual_repair": _is_partner_manual_repair_text(raw_text),
            }
        return None
    if len(headers) != 1 or _is_no_partner_text(raw_text):
        return None
    matched = headers[0]
    parsed = {
        "has_partner": True,
        "observed_at": observed_at,
        "kind": matched.group("kind").strip(),
        "name": matched.group("name").strip(),
        "location": matched.group("location").strip(),
    }
    for field in RE_CONCUBINE_STATUS_FIELD.finditer(raw_text):
        key = CONCUBINE_STATUS_FIELDS[field.group("label")]
        value = field.group("value").strip()
        if key in parsed or not value or len(value) > 512:
            return None
        if key == "affinity":
            if len(value) > 20 or not re.fullmatch(r"(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)", value):
                return None
            parsed[key] = int(value.replace(",", ""))
            if parsed[key] > 2**63 - 1:
                return None
        elif key == "oath":
            oath = re.fullmatch(r"([^\s()（）]+)(?:\s*[（(][^）)]*[）)])?", value)
            if not oath:
                return None
            parsed[key] = oath.group(1)
        elif key == "tianji_chain":
            chain = _parse_tianji_chain(value, observed_at)
            if chain is None:
                return None
            parsed[key], parsed["tianji_chain_due_at"] = chain
        elif key == "fragment_progresses":
            progresses = {}
            for part in value.split("|"):
                progress = re.fullmatch(r"\s*(虚天|苍坤)\s*([0-9]{1,19})\s*/\s*([0-9]{1,19})\s*", part)
                if not progress:
                    return None
                kind = _normalize_fragment_kind(progress.group(1))
                count, total = int(progress.group(2)), int(progress.group(3))
                if kind in progresses or not 0 <= count <= total <= 2**63 - 1 or total == 0:
                    return None
                progresses[kind] = (count, total)
            parsed[key] = progresses
        elif key == "voyage":
            voyage = _parse_status_voyage(value, observed_at)
            if voyage is None:
                return None
            parsed[key] = voyage
        else:
            due_at = _parse_wait_due_at(value, observed_at, coarse_minute_buffer=True)
            if due_at is None:
                return None
            parsed[key] = due_at
    return parsed


def _parse_status_voyage(value, now):
    value = str(value or "").strip().removesuffix("。")
    if value == "当前并未执行远航任务":
        return {"status": "no_task", "clear_idle": True}
    matched = RE_VOYAGE_PANEL.fullmatch("远航状态:" + value)
    if not matched:
        return None
    status = "sailing" if matched.group("state") == "进行中" else "returned"
    return_at = float(now) if status == "returned" else 0.0
    tail = str(matched.group("tail") or "").strip()
    if tail:
        if status == "returned":
            if re.fullmatch(r"待结算(?:[（(]\.远航归来[）)])?", tail) is None:
                return None
        else:
            wait = re.fullmatch(r"(?:剩余约|剩余|预计归航还需)\s*(.+)", tail)
            return_at = _parse_wait_due_at(wait.group(1), now) if wait else None
            if return_at is None or return_at <= now:
                return None
    return {"status": status, "route": matched.group("route").strip(), "return_at": return_at}


def _status_timestamp(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            return None
        datetime.fromtimestamp(number, TZ_LOCAL)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return number


def _is_complete_status_panel(parsed):
    if not isinstance(parsed, dict) or _status_timestamp(parsed.get("observed_at")) is None:
        return False
    if parsed.get("has_partner") is False:
        return True
    if parsed.get("has_partner") is not True or not all(parsed.get(key) for key in ("kind", "name", "location")):
        return False
    # Red-dust panels legitimately omit affinity/oath; absent data is not zero.
    required = {"dream_due_at", "tianji_due_at", "heart_due_at"}
    if parsed["kind"] == "道心侍妾":
        required.add("affinity")
    return required <= parsed.keys()


def _merge_future_cooldown(state_key, parsed_due_at, now):
    parsed_due_at = float(parsed_due_at or 0)
    existing_due_at = float(state.get(state_key, 0) or 0)
    if existing_due_at > float(now) and parsed_due_at < existing_due_at:
        return existing_due_at
    return parsed_due_at


def _is_puzzle_ready():
    return bool(_completed_fragment_kinds())


def _has_available_partner():
    return (not external_events.needs_calibration() and not affinity_actions.needs_calibration()
            and state.get("concubine_availability") == "available"
            and bool((state.get("concubine_name") or "").strip()))


def _has_main_due_action(now):
    if not _has_available_partner():
        return False
    if _is_puzzle_ready():
        return True
    return fragment_actions.next_dream_at() <= float(now)


def _is_tianji_affinity_blocked():
    return state.get("concubine_kind") == "道心侍妾" and int(state.get("concubine_affinity", 0) or 0) < CONCUBINE_TIANJI_MIN_AFFINITY


def _has_tianji_due_action(now):
    if not state.get("concubine_tianji_enabled"):
        return False
    if not _has_available_partner() or _is_tianji_affinity_blocked():
        return False
    return divination_actions.next_at() <= float(now)


def _has_heart_due_action(now):
    if not state.get("concubine_heart_enabled"):
        return False
    if not _has_available_partner():
        return False
    return heart_actions.next_at() <= float(now)


def _has_due_action(now):
    return (
        (state.get("concubine_enabled") and _has_main_due_action(now))
        or _is_daily_greet_due(now)
        or _is_gift_recovery_due(now)
        or _has_tianji_due_action(now)
        or _has_heart_due_action(now)
        or _is_voyage_return_due(now)
        or _is_voyage_eligible(now)
    )


def _has_affinity_recovery_due(now):
    return _is_daily_greet_due(now) or _is_gift_recovery_due(now)


def _should_start_voyage_as_summary_trigger(now):
    if _phaseful_summary_guard_state(now) != "summary_due":
        return False
    if state.get("concubine_enabled") and _has_main_due_action(now):
        return False
    if _has_tianji_due_action(now):
        return False
    if _has_heart_due_action(now):
        return False
    return _is_voyage_eligible(now)


def _has_active_cooldown_action_due(now):
    if state.get("concubine_enabled") and _has_main_due_action(now):
        return True
    if _has_tianji_due_action(now):
        return True
    if _has_heart_due_action(now):
        return True
    return False


def _needs_active_status_calibration(now):
    if not _has_available_partner():
        return False
    if _has_recent_concubine_status_panel(now):
        _clear_status_calibration_timeout_errors()
        return False
    if state.get("concubine_tianji_enabled") and _is_tianji_timeout_calibration_error(state.get("concubine_tianji_last_error")):
        return True
    if state.get("concubine_enabled") and _has_main_due_action(now):
        last_error = str(state.get("concubine_last_error") or "")
        if "dream_pending 等待回复超时" in last_error or "入梦寻图等待回复超时" in last_error:
            return True
    if _has_heart_due_action(now):
        return not bool(_resolve_heart_panel_anchor(now))
    return False


def _active_status_calibration_context(now):
    if _has_tianji_due_action(now) or _is_tianji_timeout_calibration_error(state.get("concubine_tianji_last_error")):
        return "天机代卜", "concubine_tianji_last_error"
    if state.get("concubine_enabled") and _has_main_due_action(now):
        return "入梦寻图", "concubine_last_error"
    if _has_heart_due_action(now):
        return "共历心劫", "concubine_heart_last_error"
    return "侍妾状态校准", "concubine_last_error"


def _clear_status_calibration_timeout_errors():
    changed = False
    tianji_error = str(state.get("concubine_tianji_last_error") or "")
    if _is_tianji_timeout_calibration_error(tianji_error):
        state["concubine_tianji_last_error"] = ""
        changed = True
    main_error = str(state.get("concubine_last_error") or "")
    if "dream_pending 等待回复超时" in main_error or "入梦寻图等待回复超时" in main_error:
        state["concubine_last_error"] = ""
        changed = True
    return changed


def _has_active_nanlong_pending(now):
    if not state.get("nanlong_enabled"):
        return False
    return (
        int(state.get("nanlong_reply_to_msg_id", 0) or 0) > 0
        and float(state.get("next_nanlong_time", 0) or 0) > 0
    )


def _clear_partner_snapshot(*, clear_voyage=True):
    state["concubine_last_panel_msg_id"] = 0
    state["concubine_last_panel_chat_id"] = 0
    state["concubine_name"] = ""
    state["concubine_kind"] = ""
    state["concubine_location"] = ""
    state["concubine_affinity"] = 0
    state["concubine_oath"] = ""
    state["concubine_dream_due_at"] = 0
    state["concubine_tianji_due_at"] = 0
    state["concubine_heart_due_at"] = 0
    state["concubine_tianji_chain"] = ""
    state["concubine_tianji_chain_due_at"] = 0
    if clear_voyage:
        _clear_voyage_snapshot()
    _clear_fragment_progress()


def _schedule_no_partner_check(now, *, allow_reacquire=True):
    retry_at = float(now + CONCUBINE_NO_PARTNER_RETRY_SEC)
    blocked_until = reacquire_actions.next_at()
    if allow_reacquire and state.get("concubine_enabled") and state.get("concubine_auto_reacquire") and blocked_until > now:
        retry_at = min(retry_at, blocked_until)
    state["next_concubine_time"] = retry_at
    return retry_at


def _mark_no_partner(now, reason, *, allow_reacquire=True, clear_pending=True):
    if _is_permanent_moon_partner():
        _set_availability("available")
        _set_phase("idle")
        _clear_pending_msg_ids()
        state["concubine_last_error"] = f"南宫婉永久契约忽略失去判定：{str(reason or '暂无侍妾')}"
        _schedule_status_recheck(now)
        mark_dirty()
        return
    _clear_partner_snapshot()
    _set_availability("no_partner")
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    state["concubine_last_snapshot_at"] = 0
    state["concubine_last_error"] = str(reason or "暂无侍妾")
    if clear_pending:
        clear_pending_tasks_by_commands(CONCUBINE_PENDING_COMMANDS, send_as_id=get_current_identity_id())
    blocked_until = float(state.get("concubine_reacquire_blocked_until", 0) or 0)
    if allow_reacquire and state.get("concubine_auto_reacquire") and now >= blocked_until:
        _schedule_after(now, 60, 1200)
    else:
        _schedule_no_partner_check(now, allow_reacquire=allow_reacquire)
    mark_dirty()


def _freeze_no_partner_until(until, reason, *, clear_pending=True):
    now = time.time()
    blocked_until = max(float(until or 0), now + 60)
    _clear_partner_snapshot()
    _set_availability("no_partner")
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    if clear_pending:
        clear_pending_tasks_by_commands(CONCUBINE_PENDING_COMMANDS, send_as_id=get_current_identity_id())
    state["concubine_last_snapshot_at"] = now
    state["concubine_last_error"] = str(reason or "侍妾暂不可补领")
    state["concubine_reacquire_blocked_until"] = blocked_until
    state["next_concubine_time"] = blocked_until
    mark_dirty()


def _apply_status_snapshot(parsed, now, *, allow_status_pending=False, owned_query=False, reconciles_external=False, read_point=None):
    if not _is_complete_status_panel(parsed):
        return False
    observed_at = parsed["observed_at"]
    processed_at = _status_timestamp(now)
    if processed_at is None or observed_at > processed_at:
        return False
    if read_point is not None and (not valid_point(read_point, telegram_only=True) or read_point["at"] > observed_at):
        return False
    affinity_at = read_point["at"] if read_point is not None else observed_at
    if affinity_actions.snapshot_is_stale(affinity_at, now=processed_at):
        return False
    if wanxin_affinity_snapshot_is_stale(
        state.get("wanxin_observation"), affinity_at, now=processed_at, observation_point=read_point,
    ):
        return False
    if not external_events.can_apply_snapshot(observed_at, authoritative=owned_query or reconciles_external, panel=parsed):
        return False
    if _status_snapshot_block_reason(
        observed_at, allow_status_pending=allow_status_pending,
        allow_fragment_ready=reconciles_external and external_events.needs_calibration(),
    ):
        return False
    if not parsed.get("has_partner"):
        if parsed.get("manual_repair"):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "侍妾数据异常，等待人工修复", clear_pending=not owned_query)
        else:
            not_eligible = bool(parsed.get("not_eligible"))
            reason = "侍妾不可用：境界不足" if not_eligible else "暂无侍妾"
            _mark_no_partner(now, reason, allow_reacquire=bool(state.get("concubine_enabled")) and not not_eligible, clear_pending=not owned_query)
        state["concubine_last_snapshot_at"] = observed_at
        state["concubine_last_panel_msg_id"] = 0
        state["concubine_last_panel_chat_id"] = 0
        external_events.snapshot_applied(observed_at)
        return True

    same_partner = (
        state.get("concubine_name") == parsed["name"]
        and state.get("concubine_kind") == parsed["kind"]
    )
    state["concubine_name"] = parsed.get("name", "")
    state["concubine_kind"] = parsed.get("kind", "")
    state["concubine_location"] = parsed.get("location", "")
    for field, default in (("affinity", 0), ("oath", ""), ("tianji_chain", ""), ("tianji_chain_due_at", 0)):
        if field in parsed or not same_partner:
            state[f"concubine_{field}"] = parsed.get(field, default)
    for field in ("dream_due_at", "tianji_due_at", "heart_due_at"):
        key, due_at = f"concubine_{field}", parsed.get(field, 0)
        state[key] = _merge_future_cooldown(key, due_at, now) if same_partner else due_at
    _apply_fragment_progresses(parsed.get("fragment_progresses") or {})
    _apply_voyage_snapshot(parsed.get("voyage"), now)
    state["concubine_last_snapshot_at"] = observed_at
    # An HTTP panel has no Telegram reply anchor. Native callers set their own.
    state["concubine_last_panel_msg_id"] = 0
    state["concubine_last_panel_chat_id"] = 0
    state["concubine_reacquire_command_override"] = ""
    external_events.snapshot_applied(observed_at)
    state["concubine_last_error"] = ""
    _clear_status_calibration_timeout_errors()
    _normalize_tianji_affinity_error(now)
    _set_availability("available")
    if int(state.get("concubine_affinity", 0) or 0) >= CONCUBINE_TIANJI_MIN_AFFINITY:
        state["concubine_greet_retry_count"] = 0
        state["concubine_gift_last_error"] = ""
    _set_phase("idle")
    _clear_pending_msg_ids()

    if _is_voyage_return_due(now):
        _schedule_chain_action(now)
        return True
    if _is_voyage_sailing(now) or state.get("concubine_voyage_status") == "needs_status":
        _schedule_voyage_wait(now)
        return True

    if _is_puzzle_ready() and state.get("concubine_enabled"):
        _schedule_chain_action(now)
    elif _has_tianji_due_action(now) and not state.get("concubine_enabled"):
        _schedule_chain_action(now)
    else:
        due_times = []
        if state.get("concubine_enabled"):
            due_times.append(fragment_actions.next_dream_at())
        if state.get("concubine_tianji_enabled") and not _is_tianji_affinity_blocked():
            due_times.append(float(state.get("concubine_tianji_due_at", 0) or 0))
        if state.get("concubine_heart_enabled"):
            due_times.append(float(state.get("concubine_heart_due_at", 0) or 0))
        due_at = min(due_times) if due_times else now + CONCUBINE_STATUS_STALE_SEC
        _schedule_at_due_or_chain(now, due_at)
    return True


def _status_snapshot_block_reason(now, *, allow_status_pending=False, allow_fragment_ready=False):
    if external_events.invalid():
        return "invalid_external_observation"
    if reacquire_actions.block_reason():
        return "reacquire_action_pending"
    if heart_actions.block_reason():
        return "heart_session_pending"
    if divination_actions.block_reason():
        return "divination_action_pending"
    if voyage_actions.block_reason():
        return "voyage_action_pending"
    if fragment_actions.block_reason():
        return "fragment_action_pending"
    if affinity_actions.block_reason():
        return "affinity_action_pending"
    query = _status_query_record()
    if query is None:
        return "invalid_status_query"
    if query and query["status"] in CONCUBINE_QUERY_UNRESOLVED and not allow_status_pending:
        return "status_query_pending"
    phase = _phase()
    allowed_phases = {"idle", "no_partner"}
    if allow_status_pending:
        allowed_phases.update({"status_pending", "gift_status_pending"})
        if query:
            allowed_phases.add(query["kind"] + "_pending")
    if allow_fragment_ready:
        allowed_phases.add("puzzle_ready")
    if phase not in allowed_phases:
        return "active_phase"
    pending_keys = {
        "concubine_greet_msg_id", "concubine_gift_status_msg_id", "concubine_gift_bag_msg_id",
        "concubine_gift_msg_id", "concubine_dream_msg_id", "concubine_fragment_msg_id",
        "concubine_puzzle_msg_id", "concubine_reacquire_msg_id", "concubine_tianji_msg_id",
        "concubine_voyage_msg_id", "concubine_heart_msg_id", "concubine_heart_prompt_msg_id",
        "concubine_heart_choice_prompt_msg_id",
    }
    if allow_status_pending and phase == "gift_status_pending":
        pending_keys.remove("concubine_gift_status_msg_id")
    if allow_status_pending and query and phase == query["kind"] + "_pending":
        pending_keys.discard(CONCUBINE_QUERY_KEYS[query["kind"]])
    if any(state.get(key) for key in pending_keys):
        return "active_pending"
    try:
        timestamp = float(now)
        seen_at = float(state.get("concubine_last_snapshot_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return "invalid_clock"
    if not all(math.isfinite(value) for value in (timestamp, seen_at)) or timestamp <= 0:
        return "invalid_clock"
    if seen_at > timestamp:
        return "stale_observation"
    pending_tasks = state.get("pending_tasks")
    if not isinstance(pending_tasks, dict):
        return "invalid_pending"
    read_commands = {CMD_CONCUBINE_STATUS, CMD_CONCUBINE_VOYAGE_STATUS}
    read_families = {"concubine_status"}
    if allow_status_pending and query and query["kind"] == "fragment":
        read_commands.add(CMD_CONCUBINE_FRAGMENT)
        read_families.add("concubine_fragment")
    for pending in pending_tasks.values():
        if not isinstance(pending, dict):
            return "invalid_pending"
        command = get_pending_command(pending).split()
        family = str(pending.get("family") or "")
        voyage_read = family == "concubine_voyage" and command == [CMD_CONCUBINE_VOYAGE_STATUS]
        if (
            (command and command[0] in CONCUBINE_PENDING_COMMANDS - read_commands)
            or (family.startswith("concubine_") and family not in read_families and not voyage_read)
        ):
            return "active_pending"
    return ""


def concubine_miniapp_status_block_reason(now, *, processed_at=None):
    if affinity_actions.snapshot_is_stale(now, now=now if processed_at is None else processed_at):
        return "stale_owned_affinity"
    if wanxin_affinity_snapshot_is_stale(
        state.get("wanxin_observation"), now, now=now if processed_at is None else processed_at,
    ):
        return "stale_wanxin_affinity"
    return _status_snapshot_block_reason(now, allow_fragment_ready=external_events.needs_calibration())


def sync_concubine_miniapp_status(text, now):
    """Apply an idle Tianjige status panel without driving the active chain."""
    phase = _phase()
    block_reason = concubine_miniapp_status_block_reason(now)
    if block_reason:
        return {
            "handled": False,
            "reason": block_reason,
            "phase": phase,
            "summary": {},
        }
    parsed = _parse_status_panel(text, now)
    if not parsed:
        return {
            "handled": False,
            "reason": "unparsed_panel",
            "phase": phase,
            "summary": {},
        }
    if not parsed.get("has_partner"):
        return {
            "handled": False,
            "reason": "no_partner_requires_module_flow",
            "phase": phase,
            "summary": {},
        }
    if not _is_complete_status_panel(parsed):
        return {
            "handled": False,
            "reason": "incomplete_panel",
            "phase": phase,
            "summary": {},
        }
    owner = _status_query_owner()
    if not owner:
        return {"handled": False, "reason": "missing_owner", "phase": phase, "summary": {}}
    before = copy.deepcopy(owner[1])
    if not _apply_status_snapshot(parsed, now, reconciles_external=True):
        return {
            "handled": False,
            "reason": "snapshot_rejected",
            "phase": phase,
            "summary": {},
        }
    if not _save_query_projection(owner, before):
        return {"handled": False, "reason": "snapshot_not_saved", "phase": phase, "summary": {}}
    return {
        "handled": True,
        "reason": "",
        "phase": _phase(),
        "summary": {
            "name": str(state.get("concubine_name") or ""),
            "kind": str(state.get("concubine_kind") or ""),
            "location": str(state.get("concubine_location") or ""),
            "affinity": int(state.get("concubine_affinity", 0) or 0),
            "voyage_status": str(state.get("concubine_voyage_status") or ""),
        },
    }


def _get_reacquire_command():
    override = str(state.get("concubine_reacquire_command_override") or "").strip()
    if override in CONCUBINE_REACQUIRE_COMMANDS:
        return override
    profile = get_send_as_profile()
    return CMD_CONCUBINE_SECT_MARRY if str(profile.get("sect_name") or "").strip() == "星宫" else CMD_CONCUBINE_ROMANCE


def get_concubine_status_text():
    if external_events.needs_calibration():
        return "侍妾外部观测记录异常，等待核对" if external_events.invalid() else "侍妾外部变动待校准，原有操作保留"
    reacquire_block = reacquire_actions.block_reason()
    if reacquire_block:
        if reacquire_block != "pending":
            return "侍妾补领归属记录缺失或异常，等待核对"
        action = reacquire_actions.record()
        return f"侍妾补领：{action['status']}，等待原操作反馈，不重复补领"
    heart_block = heart_actions.block_reason()
    if heart_block:
        if heart_block != "pending":
            return "心劫归属记录缺失或异常，等待核对"
        session = heart_actions.record()
        step = session["steps"][-1]
        return f"共历心劫：第 {step['round']}/3 轮，{step['status']}；只读校准 {session['probe_count']}/{heart_actions.PROBE_LIMIT}，不盲补消费命令"
    divination_block = divination_actions.block_reason()
    if divination_block:
        return "天机代卜归属记录异常，等待核对" if divination_block == "invalid" else "天机代卜结果待确认，保留原操作且不自动补发"
    voyage_block = voyage_actions.block_reason()
    if voyage_block:
        return "远航归属记录异常，等待核对" if voyage_block == "invalid" else "远航结果待确认，保留原操作且不自动补发"
    fragment_block = fragment_actions.block_reason()
    if fragment_block:
        return "入梦/拼图归属记录异常，等待核对" if fragment_block == "invalid" else "入梦/拼图结果待确认，保留原操作且不自动补发"
    action_block = affinity_actions.block_reason()
    if action_block:
        return "问安/赠礼归属记录异常，等待核对" if action_block == "invalid" else "问安/赠礼结果待确认，保留原操作且不自动补发"
    if affinity_actions.needs_calibration():
        return "问安/赠礼已确认，情缘余额待校准"
    query = _status_query_record()
    if query is None:
        return "🌸 侍妾 - 查询归属记录异常，等待核对"
    if _legacy_status_query_pending(query):
        return "🌸 侍妾查询缺少归属记录，等待核对"
    if (
        not state.get("concubine_enabled", False)
        and not state.get("concubine_tianji_enabled", False)
        and not state.get("concubine_heart_enabled", False)
        and not state.get("concubine_voyage_enabled", False)
    ):
        return "🌸 侍妾 - 未启用"

    phase_label = {
        "idle": "闲置",
        "status_pending": "侍妾状态校准中...",
        "greet_pending": "每日问安中...",
        "gift_status_pending": "赠予前确认侍妾中...",
        "gift_bag_pending": "赠予前查询储物袋中...",
        "gift_pending": "赠予侍妾中...",
        "dream_pending": "入梦寻图中...",
        "fragment_pending": "残图确认中...",
        "puzzle_ready": "残图已确认，等待拼图...",
        "puzzle_pending": "残图拼合中...",
        "reacquire_pending": "补领侍妾中...",
        "tianji_pending": "天机代卜中...",
        "heart_pending": "共历心劫发起中...",
        "heart_choice_pending": "共历心劫待抉择...",
        "heart_choice_reply_pending": "共历心劫等待回合推进...",
        "voyage_pending": "侍妾远航发起中...",
        "voyage_return_pending": "远航归来结算中...",
        "voyage_status_pending": "远航状态只读校准中...",
        "no_partner": "暂无侍妾",
    }.get(_phase(), _phase())
    strategy_label = {
        "reacquire_after_loss": "失去后自动补领",
        "shelter_trade_recall": "洞府安置-交易-召回（预留，未启用）",
    }.get(state.get("concubine_nanlong_strategy") or "", state.get("concubine_nanlong_strategy") or "未记录")

    lines = [
        "🌸 侍妾",
        f"- 当前阶段: {phase_label}",
        f"- 入梦寻图: {'开启' if state.get('concubine_enabled') else '关闭'}",
        f"- 天机代卜: {'开启' if state.get('concubine_tianji_enabled') else '关闭'}",
        f"- 共历心劫: {'开启' if state.get('concubine_heart_enabled') else '关闭'}",
        f"- 侍妾远航: {'开启' if state.get('concubine_voyage_enabled') else '关闭'}",
        f"- 自动补领: {'开启' if state.get('concubine_auto_reacquire') else '关闭'}",
        f"- 南陇侯策略: {strategy_label}",
    ]
    override = str(state.get("concubine_reacquire_command_override") or "").strip()
    if override:
        lines.append(f"- 补领指令校正: {override}")
    if _has_available_partner():
        kind = state.get("concubine_kind") or "侍妾"
        location = state.get("concubine_location") or "未知"
        lines.append(f"- 当前{kind}: {state.get('concubine_name')}（{location}）")
    else:
        lines.append("- 当前侍妾: 无/未确认")

    for fragment_kind in FRAGMENT_KIND_ORDER:
        count, total = _get_fragment_progress(fragment_kind)
        lines.append(f"- {FRAGMENT_LABELS[fragment_kind]}残图: {count}/{total}")
    dream_due_at = float(state.get("concubine_dream_due_at", 0) or 0)
    if dream_due_at > 0:
        lines.append(f"- 入梦寻图: {fmt_abs_ts(dream_due_at)}（{fmt_remaining(dream_due_at)}）")
    else:
        lines.append("- 入梦寻图: 可施展/待确认")
    dream_retry_at = fragment_actions.next_dream_at()
    if math.isfinite(dream_retry_at) and dream_retry_at > dream_due_at:
        lines.append(f"- 入梦本地重试: {fmt_abs_ts(dream_retry_at)}（非游戏冷却）")
    tianji_due_at = float(state.get("concubine_tianji_due_at", 0) or 0)
    if tianji_due_at > 0:
        lines.append(f"- 天机代卜: {fmt_abs_ts(tianji_due_at)}（{fmt_remaining(tianji_due_at)}）")
    else:
        lines.append("- 天机代卜: 可施展/待确认")
    heart_due_at = float(state.get("concubine_heart_due_at", 0) or 0)
    if heart_due_at > 0:
        lines.append(f"- 共历心劫: {fmt_abs_ts(heart_due_at)}（{fmt_remaining(heart_due_at)}）")
    else:
        lines.append("- 共历心劫: 可施展/待确认")
    if state.get("concubine_heart_round"):
        lines.append(f"- 心劫抉择: 第 {int(state.get('concubine_heart_round', 0) or 0)}/3 轮")
    if state.get("concubine_tianji_chain"):
        chain_due_at = float(state.get("concubine_tianji_chain_due_at", 0) or 0)
        suffix = f"（{fmt_remaining(chain_due_at)}）" if chain_due_at > 0 else ""
        lines.append(f"- 天机卦象: {state.get('concubine_tianji_chain')}{suffix}")
    voyage_status = str(state.get("concubine_voyage_status") or "").strip()
    voyage_route = str(state.get("concubine_voyage_route") or "").strip() or CONCUBINE_VOYAGE_DEFAULT_ROUTE
    voyage_return_at = float(state.get("concubine_voyage_return_at", 0) or 0)
    if voyage_status == "sailing":
        if voyage_return_at > 0:
            lines.append(f"- 远航状态: {voyage_route}航线远航中，{fmt_abs_ts(voyage_return_at)}（{fmt_remaining(voyage_return_at)}）归航")
        else:
            lines.append(f"- 远航状态: {voyage_route}航线远航中，归航时间待确认")
    elif voyage_status == "returned":
        lines.append(f"- 远航状态: {voyage_route}航线已归航，待 .远航归来")
    elif voyage_status == "needs_status":
        lines.append("- 远航状态: 待只读校准，暂不结算或发起远航")
    elif voyage_status == "idle":
        lines.append(f"- 远航状态: 空闲（默认 {CONCUBINE_VOYAGE_DEFAULT_ROUTE}）")
    elif state.get("concubine_voyage_enabled"):
        lines.append(f"- 远航状态: 未记录（默认 {CONCUBINE_VOYAGE_DEFAULT_ROUTE}）")
    if state.get("concubine_voyage_last_result"):
        result = str(state.get("concubine_voyage_last_result") or "").replace("\n", " / ").strip()
        lines.append(f"- 远航上次结算: {result[:120]}")
    if state.get("concubine_voyage_last_error"):
        lines.append(f"- 远航异常: {state.get('concubine_voyage_last_error')}")
    blocked_until = float(state.get("concubine_reacquire_blocked_until", 0) or 0)
    if blocked_until > 0:
        lines.append(f"- 补领冻结: {fmt_abs_ts(blocked_until)}（{fmt_remaining(blocked_until)}）")
    next_time = float(state.get("next_concubine_time", 0) or 0)
    if next_time > 0:
        lines.append(f"- 下次动作: {fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    if state.get("concubine_last_error"):
        lines.append(f"- 最近异常: {state.get('concubine_last_error')}")
    if state.get("concubine_tianji_last_error"):
        lines.append(f"- 代卜异常: {state.get('concubine_tianji_last_error')}")
    if state.get("concubine_kind") == "道心侍妾":
        affinity = int(state.get("concubine_affinity", 0) or 0)
        if affinity < CONCUBINE_TIANJI_MIN_AFFINITY:
            greet_label = "今日已问安" if str(state.get("concubine_last_greet_day") or "") == _local_day_key(time.time()) else "待问安"
            today = _local_day_key(time.time())
            if str(state.get("concubine_last_gift_day") or "") == today:
                gift_label = "今日已处理"
            elif str(state.get("concubine_gift_attempt_day") or "") == today:
                gift_label = "今日已尝试"
            else:
                gift_label = "待确认"
        else:
            greet_label = "情缘达标停用"
            gift_label = "情缘达标停用"
        lines.append(f"- 每日问安: {greet_label}")
        lines.append(f"- 赠予灵石: {gift_label}")
    if state.get("concubine_greet_last_error"):
        lines.append(f"- 问安异常: {state.get('concubine_greet_last_error')}")
    if state.get("concubine_gift_last_error"):
        lines.append(f"- 赠予异常: {state.get('concubine_gift_last_error')}")
    if state.get("concubine_heart_last_error"):
        lines.append(f"- 心劫异常: {state.get('concubine_heart_last_error')}")
    return "\n".join(lines)


def clear_concubine_state(*, persist=False, keep_last_error=False, include_tianji=False):
    partner_snapshot = _partner_runtime_snapshot()
    tianji_snapshot = {
        "concubine_tianji_due_at": state.get("concubine_tianji_due_at", 0),
        "concubine_tianji_chain": state.get("concubine_tianji_chain", ""),
        "concubine_tianji_chain_due_at": state.get("concubine_tianji_chain_due_at", 0),
        "concubine_tianji_last_error": state.get("concubine_tianji_last_error", ""),
    }
    heart_snapshot = {
        "concubine_heart_due_at": state.get("concubine_heart_due_at", 0),
        "concubine_heart_last_error": state.get("concubine_heart_last_error", ""),
    }
    voyage_snapshot = _voyage_runtime_snapshot()
    state["next_concubine_time"] = 0
    state["concubine_phase"] = "idle"
    state["concubine_availability"] = "unknown"
    state["concubine_reacquire_command_override"] = ""
    _clear_partner_snapshot(clear_voyage=include_tianji)
    _clear_pending_msg_ids()
    if include_tianji:
        clear_pending_tasks_by_commands(CONCUBINE_PENDING_COMMANDS, send_as_id=get_current_identity_id())
        state["concubine_tianji_due_at"] = 0
        state["concubine_tianji_chain"] = ""
        state["concubine_tianji_chain_due_at"] = 0
        state["concubine_tianji_last_error"] = ""
        state["concubine_last_greet_day"] = ""
        state["concubine_greet_retry_count"] = 0
        state["concubine_greet_last_error"] = ""
        state["concubine_last_gift_day"] = ""
        state["concubine_gift_last_error"] = ""
        state["concubine_heart_due_at"] = 0
        state["concubine_heart_last_error"] = ""
        _clear_voyage_snapshot()
    else:
        clear_pending_tasks_by_commands(CONCUBINE_MAIN_PENDING_COMMANDS, send_as_id=get_current_identity_id())
        for key, value in tianji_snapshot.items():
            state[key] = value
        for key, value in heart_snapshot.items():
            state[key] = value
        _restore_voyage_runtime_snapshot(voyage_snapshot)
    _restore_partner_runtime_snapshot(partner_snapshot)
    if not keep_last_error:
        state["concubine_last_error"] = ""
    if persist:
        save_state()
    else:
        mark_dirty()


def clear_concubine_tianji_state(*, persist=False, keep_last_error=False):
    state["concubine_tianji_msg_id"] = 0
    state["concubine_greet_msg_id"] = 0
    state["concubine_gift_status_msg_id"] = 0
    state["concubine_gift_bag_msg_id"] = 0
    state["concubine_gift_msg_id"] = 0
    state["concubine_gift_amount"] = 0
    state["concubine_tianji_due_at"] = 0
    state["concubine_tianji_chain"] = ""
    state["concubine_tianji_chain_due_at"] = 0
    clear_pending_tasks_by_commands({CMD_CONCUBINE_TIANJI, CMD_CONCUBINE_DAILY_GREET, CMD_CONCUBINE_GIFT_STONE}, send_as_id=get_current_identity_id())
    if not keep_last_error:
        state["concubine_tianji_last_error"] = ""
        state["concubine_greet_retry_count"] = 0
        state["concubine_greet_last_error"] = ""
        state["concubine_gift_last_error"] = ""
    if not state.get("concubine_enabled"):
        state["next_concubine_time"] = 0
        state["concubine_phase"] = "idle"
    if persist:
        save_state()
    else:
        mark_dirty()


def restore_concubine_runtime(now):
    if external_events.needs_calibration():
        return float(state.get("next_concubine_time", 0) or 0)
    if reacquire_actions.block_reason():
        return float(state.get("next_concubine_time", 0) or 0)
    if heart_actions.block_reason():
        return float(state.get("next_concubine_time", 0) or 0)
    if divination_actions.block_reason():
        return float(state.get("next_concubine_time", 0) or 0)
    if voyage_actions.block_reason():
        return float(state.get("next_concubine_time", 0) or 0)
    if fragment_actions.block_reason():
        return float(state.get("next_concubine_time", 0) or 0)
    if affinity_actions.block_reason():
        return float(state.get("next_concubine_time", 0) or 0)
    query = _status_query_record()
    if query is None or (query and query["status"] in CONCUBINE_QUERY_UNRESOLVED) or _legacy_status_query_pending(query):
        return float(state.get("next_concubine_time", 0) or 0)
    ban_texts = _persisted_heavenly_ban_texts()
    if ban_texts:
        identity_id = int(get_current_identity_id() or 0)
        _clear_persisted_heavenly_ban_runtime()
        _fire_and_forget(_recover_persisted_heavenly_ban(now, ban_texts=ban_texts, identity_id_hint=identity_id))
        mark_dirty()
        return 0
    if _clear_expired_tianji_chain(now):
        mark_dirty()
    if _phase() in {"status_pending", "greet_pending", "gift_status_pending", "gift_bag_pending", "gift_pending"} | CONCUBINE_VOYAGE_PENDING_PHASES:
        if _has_available_partner():
            _set_phase("idle")
        elif state.get("concubine_availability") == "no_partner":
            _set_phase("no_partner")
        else:
            _set_phase("idle")
        _clear_pending_msg_ids()
    if _query_time(state.get("next_concubine_time")) is None:
        if _is_voyage_return_due(now):
            _schedule_chain_action(now)
        elif _is_voyage_sailing(now) or state.get("concubine_voyage_status") == "needs_status":
            _schedule_voyage_wait(now)
    if float(state.get("next_concubine_time", 0) or 0) <= 0:
        state["next_concubine_time"] = float(now + random.uniform(60, 1200))
    mark_dirty()
    return float(state.get("next_concubine_time", 0) or 0)


def _status_query_owner():
    identity_id = get_current_identity_id()
    if not has_identity(identity_id):
        return None
    return identity_id, get_identity_state(identity_id), get_identity_account(identity_id)


def _owns_status_query(owner, *, sending=False, kind="status"):
    if not owner:
        return False
    identity_id, identity, account_id = owner
    return bool(
        has_identity(identity_id) and get_identity_state(identity_id) is identity
        and get_identity_account(identity_id) == account_id
        and (not sending or (
            account_id > 0 and get_global_enabled() and get_identity_enabled(identity_id)
            and (identity.get("concubine_enabled") if kind == "fragment" else
                 identity.get("concubine_tianji_enabled") if kind == "gift_status" else any(
                identity.get(key) for key in ("concubine_enabled", "concubine_tianji_enabled", "concubine_heart_enabled", "concubine_voyage_enabled")
            ))
        ))
    )


def _status_query_plan(owner):
    if not _owns_status_query(owner):
        return ""
    identity_id, identity, _account_id = owner
    values = {key: (float(value) if type(value) in {int, float} else value)
              for key, value in identity.items()
              if (key.startswith("concubine_") or key == "next_concubine_time")
              and key not in {"concubine_status_query", "concubine_gift_actions", "concubine_greet_action", "concubine_fragment_actions", "concubine_voyage_actions", "concubine_tianji_action", "concubine_heart_session", "concubine_reacquire_action", "concubine_external_observation"}
              and key not in CONCUBINE_ERROR_KEYS}
    controls = [get_global_enabled(), get_identity_enabled(identity_id), get_game_group_id(),
                get_game_topic_id(), get_send_as_profile(identity_id).get("sect_name") or ""]
    try:
        encoded = json.dumps([values, controls], sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        return ""
    return hashlib.sha256(encoded.encode()).hexdigest()


def _query_int(value):
    return value if type(value) is int and -(2 ** 63) < value < 2 ** 63 else 0


def _query_time(value):
    return _status_timestamp(value) if type(value) in {int, float} else None


def _observed_status_query_record(record):
    fields = {"origin", "op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at",
              "msg_id", "status", "reply_at", "reply_msg_id", "sender_id", "actor_id", "outcome"}
    if (record.keys() != fields or record["origin"] != "observed" or record["kind"] != "status"
            or record["status"] != "complete" or record["outcome"] not in ("panel", "summary")
            or record["command"] != CMD_CONCUBINE_STATUS
            or _query_int(record["identity_id"]) != get_current_identity_id()
            or _query_int(record["account_id"]) <= 0 or not _query_int(record["chat_id"])
            or _query_int(record["msg_id"]) <= 0 or _query_int(record["reply_msg_id"]) <= record["msg_id"]
            or _query_int(record["sender_id"]) <= 0 or not _query_int(record["actor_id"])
            or not sender_matches_identity(record["actor_id"], record["identity_id"])
            or _query_time(record["started_at"]) is None or _query_time(record["reply_at"]) is None
            or record["started_at"] > record["reply_at"]
            or record["op_id"] != f"observed:{record['chat_id']}:{record['msg_id']}"):
        return None
    return dict(record)


def _status_query_record():
    record = state.get("concubine_status_query", {})
    if not isinstance(record, dict):
        return None
    if not record:
        return {}
    if "origin" in record:
        return _observed_status_query_record(record)
    fields = {"op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at",
              "msg_id", "status", "plan_key", "sent_at", "dispatch_at", "reply_at", "reply_msg_id", "replay_after",
              "partner", "outcome", "confirmation_key"}
    if (
        record.keys() - fields or not isinstance(record.get("kind"), str)
        or record["kind"] not in CONCUBINE_QUERY_KEYS
        or record.get("command") != CONCUBINE_QUERY_COMMANDS[record["kind"]]
        or (record["kind"] == "fragment" and (
            not isinstance(record.get("partner"), str) or not 0 < len(record["partner"].strip()) <= 120))
        or (record["kind"] == "voyage_status" and (
            not isinstance(record.get("partner"), str) or len(record["partner"]) > 120))
        or (record["kind"] not in {"fragment", "voyage_status"} and "partner" in record)
        or (record["kind"] == "fragment" and record.get("status") == "complete" and (
            not isinstance(record.get("outcome"), str)
            or record["outcome"] not in {"panel", "no_partner", "summary", "voyage_lock"}))
        or (record["kind"] == "voyage_status" and record.get("status") == "complete" and (
            not isinstance(record.get("outcome"), str)
            or record["outcome"] not in {"panel", "summary", "voyage_lock", "needs_status"}))
        or ("outcome" in record and (record["kind"] not in {"fragment", "voyage_status"} or record.get("status") != "complete"))
        or ("confirmation_key" in record and (
            record["kind"] != "fragment" or record.get("status") != "complete"
            or not isinstance(record["confirmation_key"], str)
            or record["confirmation_key"] not in {"", "xutian:4/4", "cangkun:4/4", "xutian:4/4|cangkun:4/4"}
            or (record.get("outcome") != "panel" and record["confirmation_key"])))
        or _query_int(record.get("identity_id")) != get_current_identity_id()
        or _query_int(record.get("account_id")) <= 0 or not _query_int(record.get("chat_id"))
        or type(record.get("msg_id")) is not int or not 0 <= record["msg_id"] < 2 ** 63
        or not isinstance(record.get("op_id"), str) or not 0 < len(record["op_id"]) <= 128
        or not isinstance(record.get("plan_key"), str) or re.fullmatch(r"[0-9a-f]{64}", record["plan_key"]) is None
        or not isinstance(record.get("status"), str)
        or record["status"] not in CONCUBINE_QUERY_UNRESOLVED | {"unsent", "complete", "expired"}
        or type(record.get("started_at")) not in {int, float} or _status_timestamp(record["started_at"]) is None
        or any(type(record[key]) not in {int, float} or _status_timestamp(record[key]) is None
               for key in ("sent_at", "dispatch_at", "reply_at", "replay_after") if key in record)
        or (record["status"] in {"sent", "complete"} and record["msg_id"] <= 0)
        or (record["status"] == "unsent" and record["msg_id"] != 0)
        or (record["msg_id"] and not (
            record["started_at"] - 1 <= (record.get("dispatch_at") or 0) <= (record.get("sent_at") or 0)
        ))
        or (record["status"] == "complete" and (
            _query_int(record.get("reply_msg_id")) <= (record["msg_id"] if record["kind"] in {"fragment", "voyage_status"} else 0)
            or (record.get("reply_at") or 0) < record.get("dispatch_at", record["started_at"]) - 1
        ))
    ):
        return None
    return dict(record)


def _store_status_query(record):
    state["concubine_status_query"] = dict(record)
    mark_dirty()


def same_clock_native_status_covers(point, *, now):
    """Prove inclusion before the original read, never from a scalar panel ID."""
    owner, query = _status_query_owner(), _status_query_record()
    if (not _owns_status_query(owner) or not query or query["kind"] not in {"status", "gift_status"}
            or query["status"] != "complete" or owner[2] != query["account_id"]
            or query.get("origin") == "observed" and query["outcome"] != "panel"
            or not valid_point(point, telegram_only=True) or _query_time(now) is None
            or not point["at"] == _query_time(state.get("concubine_last_snapshot_at")) == query["reply_at"] <= now
            or not 0 < query["msg_id"] < query["reply_msg_id"]
            or _query_int(state.get("concubine_last_panel_msg_id")) != query["reply_msg_id"]
            or _query_int(state.get("concubine_last_panel_chat_id")) != query["chat_id"]):
        return False
    evidence = point["evidence"]
    return (not evidence["edited"] and evidence["chat_id"] == query["chat_id"]
            and evidence["msg_id"] < query["msg_id"])


def _legacy_status_query_pending(record):
    for kind in ("fragment", "voyage_status"):
        if record and record["kind"] == kind and record["status"] in CONCUBINE_QUERY_UNRESOLVED:
            continue
        if _phase() == kind + "_pending" or (kind == "fragment" and state.get(CONCUBINE_QUERY_KEYS[kind])):
            return True
    return False


def _query_error_key(kind):
    return {"gift_status": "concubine_gift_last_error", "voyage_status": "concubine_voyage_last_error"}.get(kind, "concubine_last_error")


def _save_query_projection(owner, before):
    try:
        saved = save_state() is not False
    except Exception:
        owner[1].clear()
        owner[1].update(before)
        mark_dirty()
        raise
    if not saved:
        owner[1].clear()
        owner[1].update(before)
        mark_dirty()
    return saved


def _status_query_pending_matches(record, pending, *, source_module=CONCUBINE_QUERY_SOURCE):
    # Runtime pending rows may omit account_id; the unique persisted op_id binds it.
    return bool(isinstance(pending, dict) and pending.get("op_id") == record["op_id"]
                and pending.get("source_module") == source_module
                and get_pending_command(pending) == record["command"]
                and ("account_id" not in pending or _query_int(pending["account_id"]) == record["account_id"]))


def _status_query_pending_ref(key, item):
    if isinstance(key, tuple):
        if len(key) != 2 or not _query_int(key[0]) or _query_int(key[1]) <= 0:
            return None
    elif _query_int(key) <= 0:
        return None
    if "chat_id" in item and not _query_int(item["chat_id"]):
        return None
    try:
        chat, root = message_key_parts(key, item)
    except (TypeError, ValueError, OverflowError):
        return None
    if "message_id" in item and _query_int(item["message_id"]) != root:
        return None
    return chat, root


def _observed_status_query_pending(record):
    pending = state.get("pending_tasks")
    if not isinstance(pending, dict):
        return None, False
    matched = None
    for key, item in pending.items():
        if not isinstance(item, dict):
            return None, False
        ref = _status_query_pending_ref(key, item)
        if ref is None:
            return None, False
        if ref != (record["chat_id"], record["msg_id"]):
            continue
        if (matched is not None or get_pending_command(item) != record["command"]
                or any(name in item and item[name] != record["command"] for name in ("cmd", "command"))
                or item.get("family", "concubine_status") != "concubine_status"
                or item.get("op_id") not in (None, "")
                or item.get("source_module") not in (None, "", "concubine", CONCUBINE_QUERY_SOURCE)
                or any(name in item and _query_int(item[name]) != expected for name, expected in (
                    ("account_id", record["account_id"]), ("send_as_id", record["identity_id"])))):
            return None, False
        matched = key
    return matched, True


def _clear_status_query_pending(record, *, source_module=CONCUBINE_QUERY_SOURCE, family=None):
    if record["msg_id"] <= 0:
        return
    pending = state.get("pending_tasks")
    if not isinstance(pending, dict):
        return
    if record.get("origin") == "observed":
        key, valid = _observed_status_query_pending(record)
        if valid and key is not None:
            phase = _phase()
            if phase in {"status_pending", "gift_status_pending"}:
                phase_key = CONCUBINE_QUERY_KEYS[phase.removesuffix("_pending")]
                if _query_int(state.get(phase_key)) == record["msg_id"]:
                    state[phase_key] = 0
                    _set_phase("idle")
            pending.pop(key)
            mark_dirty()
        return
    key = find_message_key(pending, record["msg_id"], chat_id=record["chat_id"])
    if (not _status_query_pending_matches(record, pending.get(key), source_module=source_module)
            or _status_query_pending_ref(key, pending[key]) != (record["chat_id"], record["msg_id"])):
        return
    family = family or CONCUBINE_QUERY_FAMILIES[record["kind"]]
    clear_pending_by_reply(send_as_id=record["identity_id"], reply_context={
        "send_as_id": record["identity_id"], "root_msg_id": record["msg_id"],
        "reply_to_msg_id": record["msg_id"], "chat_id": record["chat_id"], "family": family,
    }, clear_family=False)


def _release_owned_concubine_phase(record, key, *, unchanged):
    # A zero compatibility anchor cannot identify a replacement plan.
    anchor = state.get(key)
    if (_phase() == record["kind"] + "_pending" and type(anchor) is int
            and ((anchor > 0 and anchor == record["msg_id"]) or (anchor == 0 and unchanged))):
        state[key] = 0
        _set_phase("idle")
        return True
    return False


def _release_status_query_phase(record, *, unchanged):
    return _release_owned_concubine_phase(record, CONCUBINE_QUERY_KEYS[record["kind"]], unchanged=unchanged)


def _status_query_admitted(kind, now):
    if kind not in CONCUBINE_QUERY_KEYS or _query_time(now) is None:
        return False
    owner = _status_query_owner()
    record = _status_query_record()
    return bool(
        _owns_status_query(owner, sending=True, kind=kind) and get_game_group_id()
        and owner[0] not in _CONCUBINE_QUERY_INFLIGHT
        and record is not None and (not record or record["status"] not in CONCUBINE_QUERY_UNRESOLVED)
        and not _status_snapshot_block_reason(
            now, allow_fragment_ready=kind == "fragment" or (kind == "status" and external_events.needs_calibration()),
        )
        and (kind != "fragment" or (
            isinstance(state.get("concubine_name"), str) and 0 < len(state["concubine_name"].strip()) <= 120
            and _has_available_partner() and _is_puzzle_ready() and not _is_current_fragment_confirmed(now)
            and not _has_voyage_runtime_state(now)))
        and (kind != "voyage_status" or (
            isinstance(state.get("concubine_name"), str) and len(state["concubine_name"].strip()) <= 120
            and float(state.get("next_concubine_time", 0) or 0) <= now))
        and (not record or record["status"] not in {"unsent", "expired"}
             or float(state.get("next_concubine_time", 0) or 0) <= now)
    )


async def _send_status_query(kind, now):
    if not _status_query_admitted(kind, now):
        return False
    owner = _status_query_owner()
    identity_id, identity, account_id = owner
    started_at = max(float(now), time.time())
    command = CONCUBINE_QUERY_COMMANDS[kind]
    _set_phase(kind + "_pending")
    identity[CONCUBINE_QUERY_KEYS[kind]] = 0
    identity["next_concubine_time"] = started_at + CONCUBINE_PHASE_TIMEOUT_SEC
    record = {
        "op_id": uuid4().hex, "kind": kind, "identity_id": identity_id, "account_id": account_id,
        "chat_id": get_game_group_id(), "command": command, "started_at": started_at,
        "status": "sending", "msg_id": 0, "plan_key": _status_query_plan(owner),
    }
    if kind in {"fragment", "voyage_status"}:
        record["partner"] = identity["concubine_name"].strip()
    if not record["plan_key"]:
        _set_phase("idle")
        return False
    _store_status_query(record)
    _CONCUBINE_QUERY_INFLIGHT[identity_id] = record["op_id"]

    def current_operation():
        if not _owns_status_query(owner):
            return None
        with use_identity(identity_id):
            current = _status_query_record()
        return current if current and all(current.get(key) == record[key] for key in (
            "op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at",
        )) else None

    def can_send():
        if not _owns_status_query(owner, sending=True, kind=kind):
            return False
        with use_identity(identity_id):
            return bool(current_operation() == record and _status_query_plan(owner) == record["plan_key"]
                        and not _status_snapshot_block_reason(time.time(), allow_status_pending=True)
                        and not _has_phaseful_summary_window(time.time()))

    def note_unsent(reason, *, persist=True):
        unchanged = _status_query_plan(owner) == record["plan_key"]
        _store_status_query(dict(record, status="unsent"))
        _release_status_query_phase(record, unchanged=unchanged)
        if unchanged:
            identity["next_concubine_time"] = max(started_at, time.time()) + random.uniform(
                CONCUBINE_SEND_FAILURE_RETRY_MIN_SEC, CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC)
            identity[_query_error_key(kind)] = reason
        if persist:
            save_state()

    try:
        try:
            saved = save_state() is not False
        except Exception:
            note_unsent("侍妾查询在途状态保存异常，本次未发送", persist=False)
            raise
        if not saved:
            note_unsent("侍妾查询在途状态未保存，本次未发送")
            return False
        previous_block = dict(classify_game_send_block(identity_id, command))
        try:
            msg = await _send_concubine_game_command(
                command, track=True, max_retry=0, reply_timeout=CONCUBINE_PHASE_TIMEOUT_SEC,
                send_as_id=identity_id, target_chat_id=record["chat_id"], source_module=CONCUBINE_QUERY_SOURCE,
                op_id=record["op_id"], operation_check=can_send,
                **({"priority": "chain"} if kind in {"fragment", "voyage_status"} else {}),
            )
        except (asyncio.CancelledError, Exception):
            current = current_operation()
            if current and current["status"] == "sending":
                _store_status_query(dict(current, status="unknown"))
                save_state()
            elif current and current["status"] == "complete":
                _clear_status_query_pending(current)
                save_state()
            raise
        current = current_operation()
        if current is None:
            return False
        if current["status"] != "sending":
            if current["status"] == "complete":
                _clear_status_query_pending(current)
                save_state()
            return current["status"] in {"sent", "complete"}
        at = max(started_at, time.time())
        if not msg:
            block = classify_game_send_block(identity_id, command)
            if (block.get("status") == "unsent" and block != previous_block
                    and started_at <= (_status_timestamp(block.get("at")) or 0) <= at + 1):
                note_unsent(f"侍妾查询未发送：{block.get('code') or 'blocked'}")
                return False
        msg_id, chat = _query_int(getattr(msg, "id", 0)), _query_int(getattr(msg, "chat_id", 0))
        sent_at = _query_time(getattr(msg, "sent_at", 0)) or 0
        dispatch_at = _query_time(getattr(msg, "send_started_at", 0)) or 0
        known = bool(msg_id > 0 and chat == record["chat_id"] and started_at - 1 <= dispatch_at <= sent_at <= at + 1)
        current = dict(record, status="sent" if known else "unknown")
        if known:
            current.update(msg_id=msg_id, sent_at=sent_at, dispatch_at=dispatch_at)
        if _status_query_plan(owner) == record["plan_key"]:
            if known:
                identity[CONCUBINE_QUERY_KEYS[kind]] = msg_id
                identity["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
            identity[_query_error_key(kind)] = (
                "" if known else "侍妾查询发送状态未知，保留原操作等待反馈")
            current["plan_key"] = _status_query_plan(owner)
        _store_status_query(current)
        save_state()
        return known
    finally:
        if _CONCUBINE_QUERY_INFLIGHT.get(identity_id) == record["op_id"]:
            _CONCUBINE_QUERY_INFLIGHT.pop(identity_id, None)


def _adopt_status_query_receipt(record, now, *, source_module=CONCUBINE_QUERY_SOURCE, include_logs=True):
    if record["msg_id"] or record["account_id"] != get_identity_account(record["identity_id"]):
        return record
    candidates = {}
    pending = state.get("pending_tasks")
    if not isinstance(pending, dict):
        return record
    for key, item in pending.items():
        if not _status_query_pending_matches(record, item, source_module=source_module):
            continue
        reference = _status_query_pending_ref(key, item)
        if reference is None:
            continue
        chat, root = reference
        sent_at = _query_time(item.get("sent_at")) or 0
        dispatch_at = _query_time(item.get("send_started_at")) or 0
        if chat == record["chat_id"] and root > 0 and record["started_at"] - 1 <= dispatch_at <= sent_at <= now + 1:
            candidates[chat, root] = (sent_at, dispatch_at)
    if not candidates and include_logs:
        def owned(entry):
            return bool(isinstance(entry, dict) and entry.get("event_type") == "sent"
                        and entry.get("op_id") == record["op_id"] and entry.get("source_module") == source_module
                        and ("account_id" not in entry or _query_int(entry["account_id"]) == record["account_id"])
                        and entry.get("text") == record["command"] and _query_int(entry.get("chat_id")) == record["chat_id"]
                        and sender_matches_identity(entry.get("sender_id"), record["identity_id"])
                        and _query_int(entry.get("message_id")) > 0)

        for entry in find_recent_message_log_commands(
            min(now, record["started_at"] + CONCUBINE_LOG_REPLAY_LOOKBACK_SEC), command_predicate=owned,
            start_ts=record["started_at"] - 1, chat_id=record["chat_id"], lookahead_sec=1,
        ):
            sent_at = _query_time(entry.get("ts_epoch")) or 0
            if owned(entry) and record["started_at"] - 1 <= sent_at <= now + 1:
                candidates[record["chat_id"], entry["message_id"]] = (sent_at, sent_at)
    if len(candidates) != 1:
        return record
    (chat, root), (sent_at, dispatch_at) = next(iter(candidates.items()))
    return dict(record, status="sent", msg_id=root, chat_id=chat, sent_at=sent_at, dispatch_at=dispatch_at)


def _expire_status_query(record, owner, now):
    before = copy.deepcopy(owner[1])
    unchanged = owner[2] == record["account_id"] and _status_query_plan(owner) == record["plan_key"]
    _store_status_query(dict(record, status="expired"))
    _clear_status_query_pending(record)
    _release_status_query_phase(record, unchanged=unchanged)
    if unchanged:
        if record["kind"] == "fragment":
            _clear_fragment_confirmation()
        if record["kind"] == "voyage_status":
            _schedule_voyage_wait(now)
        else:
            _schedule_status_recheck(now)
        state[_query_error_key(record["kind"])] = "侍妾查询未收到完整面板，稍后重新校准"
    return _save_query_projection(owner, before)


def _find_owned_concubine_replies(record, now):
    if not record["msg_id"]:
        return []

    def trusted(entry):
        observed_at = _query_time(entry.get("server_event_at")) if isinstance(entry, dict) else None
        return bool(observed_at and entry.get("event_type") in {"message", "edit"}
                    and entry.get("sender_is_bot") is True and _query_int(entry.get("sender_id")) in get_game_bot_ids()
                    and _query_int(entry.get("chat_id")) == record["chat_id"]
                    and _query_int(entry.get("reply_to_msg_id")) == record["msg_id"]
                    and _query_int(entry.get("message_id")) > record["msg_id"]
                    and record.get("dispatch_at", record["started_at"]) - 1 <= observed_at <= now)

    revisions = {}
    for end_at in sorted({min(now, record["started_at"] + CONCUBINE_LOG_REPLAY_LOOKBACK_SEC), now}):
        for entry in find_message_log_replies(
            record["msg_id"], end_at, lookback_sec=CONCUBINE_LOG_REPLAY_LOOKBACK_SEC + 1,
            lookahead_sec=1, chat_id=record["chat_id"], predicate=trusted,
        ):
            if not trusted(entry):
                continue
            key, at = entry["message_id"], entry["server_event_at"]
            previous, conflict = revisions.get(key, ({"server_event_at": 0}, False))
            if at > previous["server_event_at"]:
                revisions[key] = (entry, False)
            elif at == previous["server_event_at"]:
                revisions[key] = (previous, conflict or entry.get("text") != previous.get("text"))
    return sorted((entry for entry, conflict in revisions.values() if not conflict),
                  key=lambda item: (item["server_event_at"], item["message_id"]), reverse=True)


def _parse_query_reply(record, text, observed_at):
    raw_text = str(text or "")
    if record["kind"] == "voyage_status":
        voyage = _parse_voyage_status_text(raw_text, observed_at)
        summary = _is_phaseful_summary_text(raw_text)
        if summary:
            if voyage or "远航" in raw_text:
                return None
            return {"outcome": "summary", "panel": None, "voyage": None}
        if not voyage or (record["partner"] and voyage.get("partner") not in (None, "", record["partner"])):
            return None
        outcome = "needs_status" if voyage["status"] == "needs_status" else "voyage_lock" if _is_voyage_lock_text(raw_text) else "panel"
        return {"outcome": outcome, "panel": None, "voyage": voyage}
    if record["kind"] != "fragment":
        summary = _is_phaseful_summary_text(raw_text)
        parsed = None if summary else _parse_status_panel(raw_text, observed_at)
        if not summary and not _is_complete_status_panel(parsed):
            return None
        return {"outcome": "summary" if summary else "panel", "panel": parsed}

    parsed = _parse_fragment_panel(raw_text)
    if (RE_FRAGMENT_PANEL_HEAD.search(raw_text) and not parsed) or (parsed and parsed["partner"] != record["partner"]):
        return None
    voyage = _parse_voyage_rejection(raw_text, observed_at)
    outcomes = [name for name, matched in (
        ("panel", parsed), ("no_partner", _is_no_partner_text(raw_text)),
        ("summary", _is_phaseful_summary_text(raw_text)), ("voyage_lock", voyage),
    ) if matched]
    if len(outcomes) != 1 or (voyage and voyage.get("partner") not in (None, "", record["partner"])):
        return None
    return {"outcome": outcomes[0], "panel": parsed, "voyage": voyage}


async def _recover_status_query(now):
    owner = _status_query_owner()
    record = _status_query_record()
    if not owner or record is None:
        return True
    if record and record["status"] == "complete" and owner[2] == record["account_id"]:
        before = copy.deepcopy(owner[1])
        _clear_status_query_pending(record)
        if owner[1] != before:
            _save_query_projection(owner, before)
            return True
    if not record or record["status"] not in CONCUBINE_QUERY_UNRESOLVED:
        return _legacy_status_query_pending(record)
    if owner[0] in _CONCUBINE_QUERY_INFLIGHT or now < record["started_at"]:
        return True
    if owner[2] != record["account_id"]:
        if now >= record.get("sent_at", record["started_at"]) + CONCUBINE_PHASE_TIMEOUT_SEC:
            _expire_status_query(record, owner, now)
        return True
    if now < record.get("replay_after", 0):
        return True
    before = copy.deepcopy(owner[1])
    record = _adopt_status_query_receipt(record, now)
    record["replay_after"] = now + CONCUBINE_QUERY_REPLAY_SEC
    _store_status_query(record)
    if not _save_query_projection(owner, before):
        return True
    for entry in _find_owned_concubine_replies(record, now):
        if _parse_query_reply(record, entry.get("text", ""), entry["server_event_at"]) is None:
            continue
        handler = {"fragment": handle_concubine_fragment_reply, "voyage_status": handle_concubine_voyage_reply}.get(
            record["kind"], handle_concubine_status_reply)
        await handler(
            entry.get("text", ""), now,
            SimpleNamespace(id=record["msg_id"], chat_id=record["chat_id"], raw_text=record["command"]),
            matched_family=CONCUBINE_QUERY_FAMILIES[record["kind"]], current_msg_id=entry["message_id"],
            current_chat_id=record["chat_id"], observed_at=entry["server_event_at"],
            reply_context={"sender_id": entry["sender_id"]},
        )
        # Terminal evidence needs its local commit retried, not read-timeout expiry.
        return True
    if now >= record.get("sent_at", record["started_at"]) + CONCUBINE_PHASE_TIMEOUT_SEC:
        # A status read may expire; this policy does not authorize retrying mutations.
        _expire_status_query(record, owner, now)
    return True


async def _send_status_command(now):
    if not _status_query_admitted("status", now):
        return False
    if _defer_active_for_phaseful_summary(now, "侍妾状态校准"):
        save_state()
        return False
    if _has_recent_concubine_status_panel(now) and not (_has_heart_due_action(now) and not heart_actions.panel_anchor(now)):
        _reuse_recent_status_panel(now, "10分钟内已有侍妾面板，跳过重复 .我的侍妾")
        save_state()
        return False
    return await _send_status_query("status", now)


async def _send_greet_command(now):
    return await affinity_actions.send("greet", now)


async def _send_gift_status_command(now):
    if not _status_query_admitted("gift_status", now):
        return False
    if _defer_active_for_phaseful_summary(now, "赠予侍妾", error_key="concubine_gift_last_error"):
        save_state()
        return False
    if _can_use_cached_panel_for_gift_recovery(now):
        state["concubine_gift_status_msg_id"] = 0
        state["concubine_gift_last_error"] = ""
        return await _send_gift_bag_command(now)
    return await _send_status_query("gift_status", now)


async def _send_gift_bag_command(now):
    return await affinity_actions.send("gift_bag", now)


async def _send_gift_command(now, amount):
    return await affinity_actions.send("gift", now, amount)


async def _send_dream_command(now):
    return await fragment_actions.send("dream", now)


async def _send_fragment_command(now):
    if not _status_query_admitted("fragment", now):
        return False
    if _defer_active_for_phaseful_summary(now, "残图确认"):
        save_state()
        return False
    return await _send_status_query("fragment", now)


async def _send_puzzle_command(now):
    return await fragment_actions.send("puzzle", now)


async def _send_reacquire_command(now):
    return await reacquire_actions.send(now)


async def _send_tianji_command(now):
    return await divination_actions.send(now)


async def _send_heart_command(now):
    return await heart_actions.send(now)


async def _send_voyage_return_command(now):
    return await voyage_actions.send("voyage_return", now)


async def _send_voyage_status_command(now):
    if not _status_query_admitted("voyage_status", now):
        return False
    if _defer_active_for_phaseful_summary(now, "远航状态校准", error_key="concubine_voyage_last_error"):
        save_state()
        return False
    return await _send_status_query("voyage_status", now)


async def _send_voyage_command(now):
    return await voyage_actions.send("voyage", now)


async def _send_heart_choice(now):
    return await heart_actions.send_choice(now)


async def _handle_owned_status_query_reply(record, text, now, reply_to, observed_at, current_msg_id, current_chat_id, context):
    owner = _status_query_owner()
    root = _query_int(getattr(reply_to, "id", 0))
    reply_chat = _query_int(getattr(reply_to, "chat_id", 0))
    command = str(getattr(reply_to, "raw_text", "") or "").strip()
    if (
        not _owns_status_query(owner) or owner[2] != record["account_id"]
        or record["status"] not in CONCUBINE_QUERY_UNRESOLVED
        or root <= 0 or _query_int(current_msg_id) <= 0
        or _query_int(current_chat_id) != record["chat_id"]
        or (reply_chat and reply_chat != record["chat_id"])
        or (command and command != record["command"])
    ):
        return False
    record = _adopt_status_query_receipt(record, now)
    if root != record["msg_id"] or observed_at < record.get("dispatch_at", record["started_at"]) - 1:
        return False
    result = _parse_query_reply(record, text, observed_at)
    if result is None:
        return False
    summary, parsed = result["outcome"] == "summary", result["panel"]
    source_context = dict(context)
    if source_context.get("op_id") == record["op_id"]:
        source_context.pop("op_id")
    native_source = _observed_status_query_source(
        reply_to, owner, observed_at, current_msg_id, current_chat_id, source_context)
    read_point = _status_read_point(native_source) if native_source is not None else None

    before = copy.deepcopy(owner[1])
    current_plan = _status_query_plan(owner) == record["plan_key"]
    applied = False
    if (current_plan and not _status_snapshot_block_reason(observed_at, allow_status_pending=True)
            and external_events.can_apply_snapshot(record.get("dispatch_at", record["started_at"]), authoritative=True)):
        if summary:
            _release_status_query_phase(record, unchanged=current_plan)
            _schedule_status_recheck(now)
            state["concubine_gift_last_error" if record["kind"] == "gift_status" else "concubine_last_error"] = "侍妾状态查询触发闭关/元婴结算，稍后重新校准"
        else:
            applied = _apply_status_snapshot(parsed, now, allow_status_pending=True, owned_query=True, read_point=read_point)
            if applied and parsed.get("has_partner"):
                state["concubine_last_panel_msg_id"] = current_msg_id
                state["concubine_last_panel_chat_id"] = current_chat_id
    if not applied:
        _release_status_query_phase(record, unchanged=current_plan)

    completed = dict(record, status="complete", reply_at=observed_at, reply_msg_id=current_msg_id)
    completed.pop("replay_after", None)
    _store_status_query(completed)
    _clear_status_query_pending(completed)
    gift_continue = bool(
        applied and record["kind"] == "gift_status"
        and _owns_status_query(owner, sending=True, kind="gift_status")
        and _has_recent_concubine_status_panel(now) and _is_gift_recovery_eligible(now)
    )
    if not gift_continue and applied and record["kind"] == "gift_status":
        if int(state.get("concubine_affinity", 0) or 0) >= CONCUBINE_TIANJI_MIN_AFFINITY:
            state["concubine_gift_last_error"] = ""
            _schedule_after_tianji(now)
        else:
            state["concubine_gift_last_error"] = "状态确认后不满足赠予条件，暂不赠予"
            _schedule_affinity_recovery(now)
    # The read and its exact pending root commit before any downstream await.
    if not _save_query_projection(owner, before):
        return False
    if gift_continue:
        await _send_gift_bag_command(now)
    elif applied and _is_puzzle_ready():
        completed_text = _format_completed_fragment_progresses()
        try:
            await send_audit_log(f"🌸 残图已凑齐（{completed_text}），先自动 .残图 确认后再拼图。", scope="identity", send_as_id=owner[0])
        except Exception as exc:
            console_log(f"侍妾查询已保存，通知失败 ({type(exc).__name__})")
    return True


def _observed_status_query_source(reply_to, owner, at, msg_id, chat_id, context):
    if not _owns_status_query(owner) or owner[2] <= 0 or not isinstance(context, dict):
        return None
    root = _query_int(getattr(reply_to, "id", 0))
    actor = _query_int(getattr(reply_to, "sender_id", 0))
    sender = _query_int(context.get("sender_id"))
    command = getattr(reply_to, "raw_text", None)
    native_at = telegram_event_timestamp(reply_to)
    started_at = _query_time(context.get("reply_to_server_at", native_at))
    if (started_at is None or started_at > at or not 0 < root < _query_int(msg_id)
            or not _query_int(chat_id) or chat_id not in get_game_group_ids()
            or _query_int(getattr(reply_to, "chat_id", 0)) != chat_id
            or sender <= 0 or sender not in get_game_bot_ids() or not actor
            or not isinstance(command, str) or command.strip() != CMD_CONCUBINE_STATUS
            or context.get("reply_to_command_edited", False) is not False
            or getattr(reply_to, "edit_date", None) is not None
            or ("reply_to_command_edited" not in context and not hasattr(reply_to, "edit_date"))
            or context.get("event_type", "message") not in ("message", "edit")
            or context.get("forwarded") or context.get("reply_to_command_forwarded") or getattr(reply_to, "fwd_from", None)
            or ((hasattr(reply_to, "server_event_at") or hasattr(reply_to, "date")) and native_at != started_at)
            or ("server_event_at" in context and _query_time(context["server_event_at"]) != at)
            or any(name in context and (not isinstance(context[name], str) or context[name].strip() != command.strip())
                   for name in ("reply_to_command", "command"))
            or context.get("family") not in (None, "", "concubine_status")
            or context.get("op_id") not in (None, "")
            or context.get("source_module") not in (None, "", "concubine", CONCUBINE_QUERY_SOURCE)):
        return None
    if [identity_id for identity_id in get_identity_ids() if sender_matches_identity(actor, identity_id)] != [owner[0]]:
        return None
    expected = {"send_as_id": owner[0], "account_id": owner[2], "chat_id": chat_id,
                "root_msg_id": root, "reply_to_msg_id": root, "reply_to_sender_id": actor}
    if any(name in context and _query_int(context[name]) != value for name, value in expected.items()):
        return None
    return {"origin": "observed", "op_id": f"observed:{chat_id}:{root}", "kind": "status",
            "identity_id": owner[0], "account_id": owner[2], "chat_id": chat_id, "msg_id": root,
            "command": CMD_CONCUBINE_STATUS, "started_at": started_at, "reply_at": at,
            "reply_msg_id": msg_id, "sender_id": sender, "actor_id": actor, "status": "complete"}


def _status_read_point(source):
    return {"at": source["started_at"], "evidence": {
        "source": "telegram", "chat_id": source["chat_id"], "msg_id": source["msg_id"], "edited": False,
    }}


async def _handle_observed_status_query_reply(query, text, now, reply_to, at, msg_id, chat_id, context):
    """Separate rejected evidence from a failed commit that must remain replayable."""
    owner = _status_query_owner()
    record = _observed_status_query_source(reply_to, owner, at, msg_id, chat_id, context)
    if record is None:
        return "ignored"
    started_at = record["started_at"]
    read_point = _status_read_point(record)
    if query and (started_at < query.get("reply_at", query["started_at"])
                  or (chat_id == query["chat_id"] and record["msg_id"] <= max(query["msg_id"], query.get("reply_msg_id", 0)))
                  or (chat_id != query["chat_id"] and started_at == query.get("reply_at", query["started_at"]))):
        return "ignored"
    pending_key, pending_valid = _observed_status_query_pending(record)
    phase = _phase()
    phase_key = CONCUBINE_QUERY_KEYS[phase.removesuffix("_pending")] if phase in {"status_pending", "gift_status_pending"} else None
    if (not pending_valid
            or (phase_key and (pending_key is None or _query_int(state.get(phase_key)) != record["msg_id"]))
            or any(state.get(key) for key in ("concubine_status_msg_id", "concubine_gift_status_msg_id") if key != phase_key)
            or _status_snapshot_block_reason(started_at, allow_status_pending=True,
                                             allow_fragment_ready=external_events.needs_calibration())
            or wanxin_affinity_snapshot_is_stale(state.get("wanxin_observation"), started_at, now=now, observation_point=read_point)
            or not external_events.can_apply_snapshot(started_at, authoritative=True)):
        return "ignored"
    result = _parse_query_reply(record, text, at)
    if result is None:
        return "ignored"
    before = copy.deepcopy(owner[1])
    if result["outcome"] == "summary":
        if phase_key:
            state[phase_key] = 0
            _set_phase("idle")
        if any(state.get(key) for key in ("concubine_enabled", "concubine_tianji_enabled", "concubine_heart_enabled", "concubine_voyage_enabled")):
            _schedule_status_recheck(now)
            state["concubine_last_error"] = "侍妾状态查询触发闭关/元婴结算，稍后重新校准"
    else:
        if not _apply_status_snapshot(result["panel"], now, allow_status_pending=True, owned_query=True, reconciles_external=True, read_point=read_point):
            return "ignored"
        if result["panel"]["has_partner"]:
            state["concubine_last_panel_msg_id"] = msg_id
            state["concubine_last_panel_chat_id"] = chat_id
    _store_status_query(dict(record, outcome=result["outcome"]))
    _clear_status_query_pending(record)
    # A proven read is not a legacy gift intent. Only the scheduler may choose new work.
    if not _save_query_projection(owner, before):
        return "save_failed"
    if result["outcome"] == "panel" and _is_puzzle_ready() and state.get("concubine_enabled"):
        try:
            await send_audit_log(f"🌸 残图已凑齐（{_format_completed_fragment_progresses()}），先自动 .残图 确认后再拼图。", scope="identity", send_as_id=owner[0])
        except Exception as exc:
            console_log(f"侍妾查询已保存，通知失败 ({type(exc).__name__})")
    return "complete"


async def handle_concubine_status_reply(
    text, now, reply_to, matched_family=None, current_msg_id=0, *,
    observed_at=None, current_chat_id=0, reply_context=None,
):
    if heart_actions.owns_probe(_query_int(getattr(reply_to, "id", 0)), current_chat_id, now):
        return await heart_actions.handle_reply(
            text, now, reply_to, current_msg_id=current_msg_id, current_chat_id=current_chat_id,
            observed_at=observed_at, reply_context=reply_context, probe=True,
        )
    query = _status_query_record()
    if query is None or matched_family not in (None, "", "concubine_status"):
        return False
    if query and query["status"] in CONCUBINE_QUERY_UNRESOLVED:
        if query["kind"] not in {"status", "gift_status"}:
            return False
        context = {} if reply_context is None else reply_context
        if not isinstance(context, dict):
            return False
        expected = {"send_as_id": get_current_identity_id(), "account_id": query["account_id"],
                    "root_msg_id": _query_int(getattr(reply_to, "id", 0)),
                    "reply_to_msg_id": _query_int(getattr(reply_to, "id", 0)), "chat_id": current_chat_id}
        if (any(key in context and _query_int(context[key]) != value for key, value in expected.items())
                or ("sender_id" in context and _query_int(context["sender_id"]) not in get_game_bot_ids())
                or context.get("reply_to_command_edited")
                or (context.get("reply_to_command") and context["reply_to_command"] != CMD_CONCUBINE_STATUS)):
            return False
        observed_at = now if observed_at is None else observed_at
    observed_at, now = _query_time(observed_at), _query_time(now)
    if observed_at is None or now is None or observed_at > now:
        return False
    if query and query["status"] in CONCUBINE_QUERY_UNRESOLVED:
        return await _handle_owned_status_query_reply(
            query, text, now, reply_to, observed_at, current_msg_id, current_chat_id, context)
    return await _handle_observed_status_query_reply(
        query, text, now, reply_to, observed_at, current_msg_id, current_chat_id, reply_context) == "complete"


async def handle_concubine_dream_reply(
    text, now, reply_to, matched_family=None, *, current_msg_id=0,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    return await fragment_actions.handle_reply(
        "dream", text, now, reply_to, current_msg_id=current_msg_id,
        current_chat_id=current_chat_id, observed_at=observed_at, reply_context=reply_context,
    )


async def handle_concubine_fragment_reply(
    text, now, reply_to, matched_family=None, *, current_msg_id=0,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    now, at = _query_time(now), _query_time(observed_at)
    owner, record = _status_query_owner(), _status_query_record()
    context = reply_context if isinstance(reply_context, dict) else {}
    root = _query_int(getattr(reply_to, "id", 0))
    if (
        now is None or at is None or at > now or not _owns_status_query(owner)
        or not record or record["kind"] != "fragment" or record["status"] not in CONCUBINE_QUERY_UNRESOLVED
        or owner[2] != record["account_id"] or matched_family not in {None, "concubine_fragment"}
        or _query_int(context.get("sender_id")) not in get_game_bot_ids()
        or _query_int(current_chat_id) != record["chat_id"]
        or _query_int(getattr(reply_to, "chat_id", 0)) not in (0, record["chat_id"])
        or str(getattr(reply_to, "raw_text", "") or "").strip() not in ("", record["command"])
    ):
        return False
    expected = {
        "send_as_id": record["identity_id"], "account_id": record["account_id"],
        "chat_id": record["chat_id"], "root_msg_id": root, "reply_to_msg_id": root,
    }
    if (any(key in context and _query_int(context[key]) != value for key, value in expected.items())
            or context.get("reply_to_command_edited")
            or (context.get("reply_to_command") and context["reply_to_command"] != record["command"])):
        return False
    record = _adopt_status_query_receipt(record, now)
    if record["msg_id"] <= 0 or root != record["msg_id"] or _query_int(current_msg_id) <= root or at < record["dispatch_at"] - 1:
        return False

    result = _parse_query_reply(record, text, at)
    if result is None:
        return False
    outcome, parsed, voyage = result["outcome"], result["panel"], result["voyage"]
    unchanged = (_status_query_plan(owner) == record["plan_key"]
                 and not _status_snapshot_block_reason(at, allow_status_pending=True))
    before = copy.deepcopy(owner[1])
    _release_status_query_phase(record, unchanged=unchanged)
    notify = ""
    confirmation_key = ""
    if unchanged:
        _clear_fragment_confirmation()
        state["concubine_last_error"] = ""
        if now - at > CONCUBINE_PANEL_REUSE_MAX_AGE_SEC:
            _schedule_status_recheck(now)
            state["concubine_last_error"] = "残图回包已过确认有效期，稍后重新查询"
        elif parsed:
            _apply_fragment_progresses(parsed["progresses"])
            if _is_puzzle_ready():
                confirmation_key = _mark_fragment_confirmation(at)
                _set_phase("puzzle_ready")
                _schedule_chain_action(now)
                notify = f"🌸 残图确认 4/4（{_format_completed_fragment_progresses()}），已排队自动 .拼图。"
            else:
                _schedule_after_tianji(now)
        elif voyage:
            _apply_voyage_snapshot(voyage, at)
            _schedule_voyage_wait(now)
            state["concubine_last_error"] = "残图确认被远航锁拦截，等待归航"
        else:
            if outcome == "no_partner":
                _set_availability("unknown")
                state["concubine_last_snapshot_at"] = 0
            _schedule_status_recheck(now)
            state["concubine_last_error"] = "残图查询未返回可用面板，稍后校准侍妾状态"
    completed = dict(record, status="complete", reply_at=at, reply_msg_id=current_msg_id,
                     outcome=outcome, confirmation_key=confirmation_key)
    completed.pop("replay_after", None)
    _store_status_query(completed)
    _clear_status_query_pending(completed)
    if not _save_query_projection(owner, before):
        return False
    if notify:
        try:
            await send_audit_log(notify, scope="identity", send_as_id=owner[0])
        except Exception as exc:
            console_log(f"残图确认已保存，通知失败 ({type(exc).__name__})")
    return True


async def handle_concubine_puzzle_reply(
    text, now, reply_to, matched_family=None, *, current_msg_id=0,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    return await fragment_actions.handle_reply(
        "puzzle", text, now, reply_to, current_msg_id=current_msg_id,
        current_chat_id=current_chat_id, observed_at=observed_at, reply_context=reply_context,
    )


async def handle_concubine_reacquire_reply(
    text, now, reply_to, matched_family=None, *, current_msg_id=0,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    return await reacquire_actions.handle_reply(
        text, now, reply_to, current_msg_id=current_msg_id,
        current_chat_id=current_chat_id, observed_at=observed_at, reply_context=reply_context,
    )


async def handle_concubine_tianji_reply(
    text, now, reply_to, matched_family=None, *, current_msg_id=0,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    if matched_family not in {None, "concubine_tianji"}:
        return False
    return await divination_actions.handle_reply(
        text, now, reply_to, current_msg_id=current_msg_id, current_chat_id=current_chat_id,
        observed_at=observed_at, reply_context=reply_context,
    )


def _is_heart_resource_shortage_text(text):
    raw_text = str(text or "")
    return "修为不足" in raw_text and "开启共历心劫" in raw_text


def _heart_next_choice_delay():
    return random.uniform(CONCUBINE_HEART_CHOICE_DELAY_MIN_SEC, CONCUBINE_HEART_CHOICE_DELAY_MAX_SEC)


async def handle_concubine_heart_reply(
    text, now, reply_to, matched_family=None, current_msg_id=0, *,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    if matched_family not in (None, "concubine_heart"):
        return False
    return await heart_actions.handle_reply(
        text, now, reply_to, current_msg_id=current_msg_id, current_chat_id=current_chat_id,
        observed_at=observed_at, reply_context=reply_context,
    )


def owns_concubine_voyage_status_reply(root, chat_id, command=""):
    if str(command or "").strip() == CMD_CONCUBINE_VOYAGE_STATUS:
        return True
    query = _status_query_record()
    if query is None:
        return True
    if not query or query["kind"] != "voyage_status":
        return False
    return query["status"] in CONCUBINE_QUERY_UNRESOLVED or (
        _query_int(chat_id) == query["chat_id"] and 0 < _query_int(root) <= query["msg_id"])


async def _handle_owned_voyage_status_reply(
    text, now, reply_to, *, current_msg_id, current_chat_id, observed_at, reply_context,
):
    now, at = _query_time(now), _query_time(observed_at)
    owner, record = _status_query_owner(), _status_query_record()
    context = reply_context if isinstance(reply_context, dict) else {}
    root = _query_int(getattr(reply_to, "id", 0))
    if (
        now is None or at is None or at > now or not _owns_status_query(owner)
        or not record or record["kind"] != "voyage_status" or record["status"] not in CONCUBINE_QUERY_UNRESOLVED
        or owner[2] != record["account_id"] or _query_int(context.get("sender_id")) not in get_game_bot_ids()
        or _query_int(current_chat_id) != record["chat_id"]
        or _query_int(getattr(reply_to, "chat_id", 0)) not in (0, record["chat_id"])
        or str(getattr(reply_to, "raw_text", "") or "").strip() not in ("", record["command"])
    ):
        return False
    expected = {"send_as_id": record["identity_id"], "account_id": record["account_id"],
                "chat_id": record["chat_id"], "root_msg_id": root, "reply_to_msg_id": root}
    if (any(key in context and _query_int(context[key]) != value for key, value in expected.items())
            or any(key in context and context[key] != value for key, value in {
                "op_id": record["op_id"], "source_module": CONCUBINE_QUERY_SOURCE, "family": "concubine_voyage",
            }.items())
            or (getattr(reply_to, "sender_id", 0) and not sender_matches_identity(reply_to.sender_id, record["identity_id"]))
            or context.get("reply_to_command_edited")
            or (context.get("reply_to_command") and context["reply_to_command"] != record["command"])):
        return False
    record = _adopt_status_query_receipt(record, now)
    if record["msg_id"] <= 0 or root != record["msg_id"] or _query_int(current_msg_id) <= root or at < record["dispatch_at"] - 1:
        return False
    result = _parse_query_reply(record, text, at)
    if result is None:
        return False
    unchanged = (_status_query_plan(owner) == record["plan_key"]
                 and not _status_snapshot_block_reason(at, allow_status_pending=True))
    before = copy.deepcopy(owner[1])
    _release_status_query_phase(record, unchanged=unchanged)
    if unchanged:
        if result["outcome"] == "summary" or now - at > CONCUBINE_PANEL_REUSE_MAX_AGE_SEC:
            _apply_voyage_snapshot({"status": "needs_status"}, now)
            state["concubine_voyage_last_error"] = "远航状态尚未确认，稍后只读校准"
        else:
            _apply_voyage_snapshot(result["voyage"], now)
    completed = dict(record, status="complete", reply_at=at, reply_msg_id=current_msg_id, outcome=result["outcome"])
    completed.pop("replay_after", None)
    _store_status_query(completed)
    _clear_status_query_pending(completed)
    return _save_query_projection(owner, before)


async def handle_concubine_voyage_reply(
    text, now, reply_to, matched_family=None, *, current_msg_id=0,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    if owns_concubine_voyage_status_reply(
        getattr(reply_to, "id", 0), current_chat_id or getattr(reply_to, "chat_id", 0),
        getattr(reply_to, "raw_text", "") or (reply_context or {}).get("reply_to_command"),
    ):
        if matched_family not in {None, "concubine_voyage"}:
            return False
        return await _handle_owned_voyage_status_reply(
            text, now, reply_to, current_msg_id=current_msg_id, current_chat_id=current_chat_id,
            observed_at=observed_at, reply_context=reply_context,
        )
    if matched_family not in {None, "concubine_voyage"}:
        return False
    return await voyage_actions.handle_reply(
        text, now, reply_to, current_msg_id=current_msg_id, current_chat_id=current_chat_id,
        observed_at=observed_at, reply_context=reply_context,
    )


async def handle_concubine_greet_reply(
    text, now, reply_to, matched_family=None, *, current_msg_id=0,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_greet" and CMD_CONCUBINE_DAILY_GREET not in orig_cmd:
        return False
    return await affinity_actions.handle_reply(
        "greet", text, now, reply_to, current_msg_id=current_msg_id,
        current_chat_id=current_chat_id, observed_at=observed_at, reply_context=reply_context,
    )


async def handle_concubine_storage_bag_reply(
    text, now, reply_to, matched_family=None, *, current_msg_id=0,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "storage_bag" and CMD_STORAGE_BAG not in orig_cmd:
        return False
    return await affinity_actions.handle_reply(
        "gift_bag", text, now, reply_to, current_msg_id=current_msg_id,
        current_chat_id=current_chat_id, observed_at=observed_at, reply_context=reply_context,
    )


async def handle_concubine_gift_reply(
    text, now, reply_to, matched_family=None, *, current_msg_id=0,
    current_chat_id=0, observed_at=0, reply_context=None,
):
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_gift" and CMD_CONCUBINE_GIFT_STONE not in orig_cmd:
        return False
    return await affinity_actions.handle_reply(
        "gift", text, now, reply_to, current_msg_id=current_msg_id,
        current_chat_id=current_chat_id, observed_at=observed_at, reply_context=reply_context,
    )


def owns_concubine_affinity_reply(family, root, chat_id):
    return affinity_actions.owns_reply(family, root, chat_id)


async def handle_concubine_affinity_event(
    text, now, event=None, matched_family=None, require_identity_hint=False, *,
    event_type="message", reply_to=None, reply_context=None,
):
    if matched_family in {"concubine_greet", "concubine_gift"}:
        return False
    return await external_events.observe(
        text, now, event, event_type=event_type, reply_to=reply_to, reply_context=reply_context,
    )


async def handle_concubine_loss_broadcast(text, now, event, *, event_type="message"):
    return await external_events.observe(text, now, event, loss=True, event_type=event_type)


async def run_concubine_scheduler(now):
    if _CONCUBINE_SCHEDULER_LOCK.locked():
        return
    async with _CONCUBINE_SCHEDULER_LOCK:
        await _run_concubine_scheduler(now)


async def run_concubine_phaseful_cleanup_scheduler(now):
    if _CONCUBINE_SCHEDULER_LOCK.locked():
        return
    async with _CONCUBINE_SCHEDULER_LOCK:
        await _run_concubine_phaseful_cleanup_scheduler(now)


def _persisted_heavenly_ban_texts():
    return [str(state.get(key) or "").strip() for key in CONCUBINE_ERROR_KEYS if heavenly_ban_mod.is_heavenly_ban_text(state.get(key))]


def _clear_persisted_heavenly_ban_runtime():
    for key in CONCUBINE_ERROR_KEYS:
        if heavenly_ban_mod.is_heavenly_ban_text(state.get(key)):
            state[key] = ""
    state["concubine_dream_due_at"] = 0
    state["next_concubine_time"] = 0
    _set_phase("idle")
    _clear_pending_msg_ids()


async def _recover_persisted_heavenly_ban(now, *, ban_texts=None, identity_id_hint=0):
    ban_texts = [str(item or "").strip() for item in (ban_texts or _persisted_heavenly_ban_texts()) if str(item or "").strip()]
    if not ban_texts:
        return False
    await heavenly_ban_mod.handle_heavenly_ban_text(
        "\n".join(ban_texts),
        now=now,
        identity_id_hint=identity_id_hint or get_current_identity_id(),
        source="concubine_recovery",
    )
    _clear_persisted_heavenly_ban_runtime()
    save_state()
    return True


async def _run_concubine_phaseful_cleanup_scheduler(now):
    await reacquire_actions.recover(now)
    await heart_actions.recover(now, allow_send=False)


async def _run_concubine_scheduler(now):
    if await reacquire_actions.recover(now):
        return
    if await heart_actions.recover(now):
        return
    if await divination_actions.recover(now):
        return
    if await voyage_actions.recover(now):
        return
    if await fragment_actions.recover(now):
        return
    if await affinity_actions.recover(now):
        return
    if await _recover_status_query(now):
        return
    if await external_events.recover(now):
        return
    if await _recover_persisted_heavenly_ban(now):
        return

    if (
        not state.get("concubine_enabled", False)
        and not state.get("concubine_tianji_enabled", False)
        and not state.get("concubine_heart_enabled", False)
        and not state.get("concubine_voyage_enabled", False)
        and not _has_voyage_runtime_state(now)
    ):
        return

    if _clear_expired_tianji_chain(now):
        save_state()

    if _has_active_nanlong_pending(now):
        state["concubine_last_error"] = "南陇侯抉择中，侍妾模块暂缓"
        _schedule_after(now, 60, 600)
        save_state()
        return

    if _normalize_tianji_affinity_error(now):
        save_state()

    if _clear_stale_phaseful_summary_wait_errors(now):
        save_state()

    if _normalize_resolved_puzzle_send_error():
        save_state()

    phase = _phase()
    if phase in {"status_pending", "greet_pending", "gift_status_pending", "gift_bag_pending", "gift_pending"}:
        pending_until = float(state.get("next_concubine_time", 0) or 0)
        if pending_until > now:
            return
        if await _recover_concubine_pending_from_message_log(now, phase):
            return
        await _audit_pending_timeout_candidates(now, phase)
        timeout_error = f"{phase} 等待回复超时，已转状态校准" if phase != "status_pending" else "侍妾状态查询等待回复超时"
        state["concubine_last_error"] = timeout_error
        if _has_available_partner():
            _set_phase("idle")
        elif state.get("concubine_availability") == "no_partner":
            _set_phase("no_partner")
        else:
            _set_phase("idle")
        _clear_pending_msg_ids()
        retry_at = _backoff_after_pending_timeout(now, phase)
        save_state()
        if phase != "status_pending":
            await send_audit_log(
                f"⚠️ 侍妾模块 {phase} 超时，已停止当前链路；{fmt_time_after(max(0, retry_at - now))} 后再做状态校准。",
                scope="identity",
            )
        else:
            await send_audit_log(
                f"⚠️ 侍妾状态查询超时，已停止当前链路；{fmt_time_after(max(0, retry_at - now))} 后再校准。",
                scope="identity",
            )
        return

    if _is_voyage_return_due(now) or _is_voyage_probe_due(now):
        next_time = float(state.get("next_concubine_time", 0) or 0)
        if next_time > now:
            return
        if not _has_available_partner():
            await _send_status_command(now)
            return
        if _is_voyage_probe_due(now) or _is_voyage_return_retry_exhausted(now):
            await _send_voyage_status_command(now)
            return
        await _send_voyage_return_command(now)
        return

    if _is_voyage_sailing(now) or state.get("concubine_voyage_status") == "needs_status":
        next_time = float(state.get("next_concubine_time", 0) or 0)
        if next_time <= now:
            _schedule_voyage_wait(now)
            save_state()
        return

    next_time = float(state.get("next_concubine_time", 0) or 0)
    if next_time > 0 and now < next_time and not _has_affinity_recovery_due(now) and not _is_voyage_eligible(now):
        return

    if phase == "no_partner" or state.get("concubine_availability") == "no_partner":
        if state.get("concubine_enabled") and state.get("concubine_auto_reacquire") and now >= reacquire_actions.next_at():
            await _send_reacquire_command(now)
            return
        _schedule_no_partner_check(now)
        save_state()
        return

    if phase == "puzzle_ready":
        if state.get("concubine_enabled") and _is_puzzle_ready():
            if _is_current_fragment_confirmed(now):
                await _send_puzzle_command(now)
            else:
                await _send_fragment_command(now)
            return
        _set_phase("idle")
        _schedule_status_recheck(now)
        save_state()
        return

    if not _has_available_partner():
        if any(state.get(key) for key in ("concubine_enabled", "concubine_tianji_enabled", "concubine_heart_enabled", "concubine_voyage_enabled")):
            await _send_status_command(now)
        else:
            state["concubine_voyage_last_error"] = "侍妾远航需先确认侍妾"
            _schedule_status_recheck(now)
            save_state()
        return

    if state.get("concubine_enabled") and _is_puzzle_ready():
        if _is_current_fragment_confirmed(now):
            _set_phase("puzzle_ready")
            await _send_puzzle_command(now)
        else:
            await _send_fragment_command(now)
        return

    if _is_daily_greet_due(now):
        if _defer_daily_greet_for_phaseful_summary(now):
            save_state()
            return
        await _send_greet_command(now)
        return

    if _is_gift_recovery_due(now):
        if _defer_gift_for_phaseful_summary(now):
            save_state()
            return
        await _send_gift_status_command(now)
        return

    if _should_start_voyage_as_summary_trigger(now):
        await _send_voyage_command(now)
        return

    if _needs_active_status_calibration(now):
        action, error_key = _active_status_calibration_context(now)
        if _defer_active_for_phaseful_summary(now, action, error_key=error_key):
            save_state()
            return
        state["concubine_last_error"] = "主动动作前状态校准，避免旧冷却快照误发"
        await _send_status_command(now)
        return

    if state.get("concubine_tianji_enabled"):
        if _is_tianji_affinity_blocked():
            affinity = int(state.get("concubine_affinity", 0) or 0)
            _mark_tianji_affinity_shortage(now, f"情缘不足（{affinity}/{CONCUBINE_TIANJI_MIN_AFFINITY}），暂缓天机代卜")
            save_state()
            return
        tianji_due_at = float(state.get("concubine_tianji_due_at", 0) or 0)
        if tianji_due_at <= now:
            await _send_tianji_command(now)
            return

    if state.get("concubine_enabled"):
        dream_due_at = fragment_actions.next_dream_at()
        if dream_due_at <= now:
            await _send_dream_command(now)
            return

    if state.get("concubine_heart_enabled"):
        heart_due_at = heart_actions.next_at()
        if heart_due_at <= now:
            await _send_heart_command(now)
            return

    if _is_voyage_eligible(now):
        await _send_voyage_command(now)
        return

    due_times = []
    if state.get("concubine_enabled"):
        due_times.append(fragment_actions.next_dream_at())
    if state.get("concubine_tianji_enabled") and not _is_tianji_affinity_blocked():
        due_times.append(float(state.get("concubine_tianji_due_at", 0) or 0))
    if state.get("concubine_heart_enabled"):
        due_times.append(heart_actions.next_at())
    due_times = [due_at for due_at in due_times if due_at > now]
    if due_times:
        state["next_concubine_time"] = min(due_times) + random.uniform(60, 600)
    else:
        _schedule_status_recheck(now)
    save_state()


__all__ = [
    "CONCUBINE_PENDING_COMMANDS",
    "clear_concubine_state",
    "clear_concubine_tianji_state",
    "get_concubine_status_text",
    "handle_concubine_affinity_event",
    "handle_concubine_dream_reply",
    "handle_concubine_fragment_reply",
    "handle_concubine_gift_reply",
    "handle_concubine_greet_reply",
    "handle_concubine_loss_broadcast",
    "handle_concubine_puzzle_reply",
    "handle_concubine_reacquire_reply",
    "handle_concubine_status_reply",
    "handle_concubine_storage_bag_reply",
    "handle_concubine_heart_reply",
    "handle_concubine_tianji_reply",
    "handle_concubine_voyage_reply",
    "owns_concubine_voyage_status_reply",
    "is_concubine_affinity_event_candidate",
    "restore_concubine_runtime",
    "run_concubine_phaseful_cleanup_scheduler",
    "run_concubine_scheduler",
    "sync_concubine_miniapp_status",
]
