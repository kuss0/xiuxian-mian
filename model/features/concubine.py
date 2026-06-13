import asyncio
import json
import os
import random
import re
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

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
    CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC,
    MESSAGES_DIR,
    RE_WHITESPACE,
    TZ_LOCAL,
)
from ..persistence import mark_dirty, save_state
from ..runtime import _fire_and_forget, clear_pending_tasks_by_commands, console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_game_topic_id, get_send_as_profile, get_send_as_tags, has_identity, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from . import workflow_log
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
CONCUBINE_MAIN_PENDING_COMMANDS = CONCUBINE_PENDING_COMMANDS - {
    CMD_CONCUBINE_TIANJI,
    CMD_CONCUBINE_VOYAGE,
    CMD_CONCUBINE_VOYAGE_RETURN,
    CMD_CONCUBINE_VOYAGE_STATUS,
}
CONCUBINE_REACQUIRE_COMMANDS = {CMD_CONCUBINE_SECT_MARRY, CMD_CONCUBINE_ROMANCE}
IDENTITY_TAG_PATTERN = r"[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+"

RE_CONCUBINE_HEAD = re.compile(r"你的(?P<kind>道心侍妾|红尘道侣)[：:]\s*【(?P<name>[^】]+)】\s*[(（]状态[：:]\s*(?P<location>[^)）\n]+)[)）]")
RE_CONCUBINE_AFFINITY = re.compile(r"情缘值[：:]\s*(\d+)")
RE_CONCUBINE_OATH = re.compile(r"当前誓约[：:]\s*([^\s(（\n]+)")
RE_DREAM_COOLDOWN = re.compile(r"入梦寻图冷却[：:]\s*([^\n]+)")
RE_TIANJI_COOLDOWN = re.compile(r"天机代卜冷却[：:]\s*([^\n]+)")
RE_HEART_COOLDOWN = re.compile(r"共历心劫冷却[：:]\s*([^\n]+)")
RE_TIANJI_CHAIN = re.compile(r"天机代卜链[：:]\s*([^\n]+)")
RE_TIANJI_CHAIN_REMAINING = re.compile(r"(?P<name>[^（(]+)[（(]\s*剩余\s*(?P<wait>[^）)]+)\s*[）)]")
RE_TIANJI_GUA = re.compile(r"得卦【(?P<name>[^】]+)】")
RE_TIANJI_XIUWEI_SHORTAGE = re.compile(r"修为不足[，,]\s*代卜天机需消耗\s*\d+\s*点?修为")
RE_DREAM_PARTNER = re.compile(r"你与侍妾【(?P<name>[^】]+)】")
RE_AFFINITY_GAIN = re.compile(r"侍妾【(?P<name>[^】]+)】[\s\S]*?情缘增加了\s*(?P<amount>\d+)\s*点")
RE_CONCUBINE_GIFT_SUCCESS = re.compile(
    r"你将【灵石】[x×]\s*(?P<stone>[\d,]+)\s*赠予了侍妾【(?P<name>[^】]+)】[\s\S]*?情缘增加了\s*(?P<amount>[\d,]+)\s*点"
)
RE_SELFLESS_PARTNER = re.compile(r"侍妾\s*【?(?P<name>[^】\s，,。]+)】?\s*挺身而出")
RE_HEART_AFFINITY_SETTLEMENT = re.compile(r"情缘结算[：:]\s*\+?\s*(?P<amount>[\d,]+)")
RE_FRAGMENT_PROGRESS = re.compile(r"(?:虚天残图拼片|拼片进度|当前进度)\s*[：:]?\s*(\d+)\s*/\s*(\d+)")
RE_FRAGMENT_TYPED_PROGRESS = re.compile(r"(?P<kind>虚天|苍坤)\s*(?:残图)?(?:拼片|进度)?\s*(?:已至)?\s*[：:]?\s*(?P<count>\d+)\s*/\s*(?P<total>\d+)")
RE_FRAGMENT_CONTEXT_KIND = re.compile(r"[【\[][^\]】]*(?P<kind>虚天|苍坤)残图[^\]】]*[】\]]")
RE_DREAM_BROADCAST_PROGRESS = re.compile(r"残图进度已至\s*(\d+)\s*/\s*(\d+)")
RE_PUZZLE_SUCCESS_KIND = re.compile(r"^【(?P<kind>虚天|苍坤)残图·拼合成功】")
RE_PUZZLE_MISSING = re.compile(r"(?:仍缺|缺失残纹)[：:]\s*([^\n。]+)")
RE_NEW_SECT_PARTNER = re.compile(r"新的道心侍妾\s*【(?P<name>[^】]+)】\s*已被指派")
RE_NEW_ROMANCE_PARTNER = re.compile(r"名为\s*【(?P<name>[^】]+)】\s*的女子[\s\S]*成为你的侍妾")
RE_LOST_PARTNER_NAME = re.compile(r"侍妾【(?P<name>[^】]+)】(?:掳走|与南陇侯交换)")
RE_IDENTITY_TAG = re.compile(rf"@({IDENTITY_TAG_PATTERN})")
RE_VOYAGE_PANEL = re.compile(r"远航状态[：:]\s*(?P<route>[^航线\n。]+)航线(?P<state>进行中|已归航)(?:，(?P<tail>[^\n。]+))?")
RE_VOYAGE_STATUS_SAILING = re.compile(r"侍妾【(?P<name>[^】]+)】正在执行【(?P<route>[^】]+)】远航[\s\S]*?预计归航还需\s*(?P<wait>[^。\n]+)")
RE_VOYAGE_STATUS_RETURNED = re.compile(r"侍妾【(?P<name>[^】]+)】已自【(?P<route>[^】]+)】航线归来")
RE_VOYAGE_START = re.compile(r"【乱星海远航·启】[\s\S]*?你命侍妾【(?P<name>[^】]+)】沿\s*(?P<route>\S+)\s*航线远行[\s\S]*?预计归航时间[：:]\s*(?P<wait>[^。\n]+)")
RE_VOYAGE_RETURN = re.compile(r"【乱星海远航·归】[\s\S]*?侍妾【(?P<name>[^】]+)】已自\s*(?P<route>\S+)\s*航线归来")
RE_VOYAGE_RETURN_WAIT = re.compile(r"(?:远航|归航)[\s\S]{0,80}?(?:还需|尚需|剩余(?:约)?)\s*(?P<wait>[^。\n]+)")
RE_VOYAGE_LOCK_WAIT = re.compile(r"远航(?:中|途中)[\s\S]{0,80}?请在\s*(?P<wait>[^。\n]+?)\s*后再试")
RE_VOYAGE_AFFINITY_LOSS = re.compile(r"情缘减少\s*(?P<amount>[\d,]+)\s*点")
RE_VOYAGE_SPIRIT_RESERVE = re.compile(r"蓄灵\s*(?P<amount>[\d,]+)\s*点")

CONCUBINE_DREAM_RESOURCE_KEY = "concubine_dream"
CONCUBINE_TIANJI_RESOURCE_KEY = "concubine_tianji"
CONCUBINE_HEART_RESOURCE_KEY = "concubine_heart"
CONCUBINE_LOG_REPLAY_LOOKBACK_SEC = CONCUBINE_PHASE_TIMEOUT_SEC + 5 * 60
CONCUBINE_LOG_REPLAY_LOOKAHEAD_SEC = 5
CONCUBINE_TIANJI_LOG_GUARD_LOOKBACK_SEC = CONCUBINE_TIANJI_CD_SEC + 2 * CONCUBINE_PHASE_TIMEOUT_SEC
CONCUBINE_TIMEOUT_CANDIDATE_LOOKBACK_SEC = 2 * 60
CONCUBINE_TIMEOUT_CANDIDATE_MAX_LINES = 1200
CONCUBINE_HEART_PANEL_MAX_AGE_SEC = 10 * 60
CONCUBINE_HEART_CHOICE_ACK_TIMEOUT_SEC = 3
CONCUBINE_HEART_CHOICE_MAX_RETRY_COUNT = 1
CONCUBINE_HEART_GLOBAL_START_GAP_SEC = 5 * 60
CONCUBINE_HEART_GLOBAL_DEFER_MIN_SEC = 60
CONCUBINE_HEART_GLOBAL_DEFER_MAX_SEC = 180
CONCUBINE_DREAM_MIN_RETRY_SEC = 90
CONCUBINE_TIANJI_MIN_AFFINITY = 300
CONCUBINE_VOYAGE_MIN_AFFINITY = 120
CONCUBINE_HEART_ACTIVE_PHASES = {"heart_pending", "heart_choice_pending", "heart_choice_reply_pending"}
CONCUBINE_VOYAGE_PENDING_PHASES = {"voyage_pending", "voyage_return_pending"}
CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC = 60 * 60
CONCUBINE_VOYAGE_LOG_SETTLE_SEC = 12
CONCUBINE_GIFT_PHASES = {"gift_status_pending", "gift_bag_pending", "gift_pending"}
CONCUBINE_GREET_MAX_RETRY_COUNT = 1
CONCUBINE_GREET_RETRY_MIN_SEC = 90
CONCUBINE_GREET_RETRY_MAX_SEC = 180
CONCUBINE_GREET_DEFER_MIN_SEC = 60
CONCUBINE_GREET_DEFER_MAX_SEC = 180
CONCUBINE_ACTIVE_DEFER_MIN_SEC = 60
CONCUBINE_ACTIVE_DEFER_MAX_SEC = 180
PHASEFUL_SUMMARY_GUARD_PHASES = {"summary_due", "observing_summary", "waiting_summary", "post_summary_wait"}
CONCUBINE_PARTNER_SNAPSHOT_KEYS = (
    "concubine_availability",
    "concubine_last_panel_msg_id",
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


def _is_heart_chain_active():
    return _phase() in CONCUBINE_HEART_ACTIVE_PHASES or int(state.get("concubine_heart_prompt_msg_id", 0) or 0) > 0


def _clear_heart_choice_guard():
    state["concubine_heart_choice_prompt_msg_id"] = 0
    state["concubine_heart_choice_round"] = 0
    state["concubine_heart_choice_sent_at"] = 0
    state["concubine_heart_choice_retry_count"] = 0


def _has_sent_heart_choice(prompt_msg_id, round_no):
    return (
        int(state.get("concubine_heart_choice_prompt_msg_id", 0) or 0) == int(prompt_msg_id or 0)
        and int(state.get("concubine_heart_choice_round", 0) or 0) == int(round_no or 0)
        and float(state.get("concubine_heart_choice_sent_at", 0) or 0) > 0
    )


def _mark_heart_choice_sent(prompt_msg_id, round_no, sent_at):
    state["concubine_heart_choice_prompt_msg_id"] = int(prompt_msg_id or 0)
    state["concubine_heart_choice_round"] = int(round_no or 0)
    state["concubine_heart_choice_sent_at"] = float(sent_at or 0)
    state["concubine_heart_choice_retry_count"] = 0


def _wait_for_existing_heart_choice(now):
    sent_at = float(state.get("concubine_heart_choice_sent_at", 0) or 0)
    _set_phase("heart_choice_reply_pending")
    retry_count = int(state.get("concubine_heart_choice_retry_count", 0) or 0)
    ack_timeout = CONCUBINE_HEART_CHOICE_ACK_TIMEOUT_SEC if retry_count < CONCUBINE_HEART_CHOICE_MAX_RETRY_COUNT else 45
    state["next_concubine_time"] = max(float(now) + ack_timeout, sent_at + ack_timeout)
    return state["next_concubine_time"]


def _close_heart_action_guard(now, reason):
    close_action_guard("concubine_heart", send_as_id=get_current_identity_id(), reason=reason, now=now)


def _heart_action_guard_session():
    sessions = state.get("action_guard_sessions")
    if not isinstance(sessions, dict):
        return None
    session = sessions.get("concubine_heart")
    return session if isinstance(session, dict) else None


def _heart_action_guard_last_sent_at():
    session = _heart_action_guard_session()
    if not session:
        return 0.0
    return max(
        float(session.get("last_sent_at", 0) or 0),
        float(session.get("first_sent_at", 0) or 0),
    )


def _heart_action_guard_blocks_until(now):
    session = _heart_action_guard_session()
    if not session or float(session.get("closed_at", 0) or 0) > 0:
        return 0.0
    attempt = int(session.get("attempt", 0) or 0)
    next_allowed_at = float(session.get("next_allowed_at", 0) or 0)
    if attempt > 0 and next_allowed_at > float(now):
        return next_allowed_at
    if attempt >= 2:
        return max(float(now) + 60, _heart_action_guard_last_sent_at() + CONCUBINE_HEART_CD_SEC)
    return 0.0


def _close_heart_chain_without_settlement(now, reason, *, detail=""):
    choice_sent_at = float(state.get("concubine_heart_choice_sent_at", 0) or 0)
    guard_sent_at = _heart_action_guard_last_sent_at()
    cooldown_from = max(choice_sent_at, guard_sent_at)
    if cooldown_from <= 0:
        cooldown_from = float(now)
    existing_due_at = float(state.get("concubine_heart_due_at", 0) or 0)
    retry_at = max(
        existing_due_at if existing_due_at > float(now) else 0.0,
        cooldown_from + CONCUBINE_HEART_CD_SEC + CD_BUFFER_SEC,
    )
    _close_heart_action_guard(now, reason)
    state["concubine_heart_due_at"] = retry_at
    state["concubine_heart_last_error"] = "心劫链路未见结算，按长冷却等待"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _schedule_at_due_or_chain(now, retry_at)
    detail_parts = [f"due_at={fmt_abs_ts(retry_at)}"]
    if detail:
        detail_parts.append(str(detail))
    _record_concubine_event(
        "共历心劫链路收尾",
        kind="skipped",
        reason=reason,
        phase="idle",
        command=CMD_CONCUBINE_HEART,
        detail="｜".join(detail_parts),
        decision="heart_chain_closed_without_settlement",
        workflow_status="skipped",
    )
    return retry_at


def _reconcile_stale_heart_action_guard(now, reason):
    if _phase() in CONCUBINE_HEART_ACTIVE_PHASES:
        return False
    if not _heart_action_guard_session():
        return False
    _close_heart_chain_without_settlement(now, reason)
    return True


def _schedule_heart_choice_followup(send_as_id, due_at, prompt_msg_id, round_no):
    send_as_id = int(send_as_id or 0)
    prompt_msg_id = int(prompt_msg_id or 0)
    round_no = int(round_no or 0)
    due_at = float(due_at or 0)
    if send_as_id <= 0 or prompt_msg_id <= 0 or round_no not in {1, 2, 3}:
        return False

    async def delayed_choice():
        delay = max(0.0, due_at - time.time())
        if delay > 0:
            await asyncio.sleep(delay)
        if not has_identity(send_as_id):
            return
        async with _CONCUBINE_SCHEDULER_LOCK:
            with use_identity(send_as_id):
                if _phase() != "heart_choice_pending":
                    return
                if int(state.get("concubine_heart_prompt_msg_id", 0) or 0) != prompt_msg_id:
                    return
                if int(state.get("concubine_heart_round", 0) or 0) != round_no:
                    return
                now = time.time()
                if float(state.get("next_concubine_time", 0) or 0) > now:
                    return
                await _send_heart_choice(now)

    _fire_and_forget(delayed_choice())
    return True


def _activate_heart_choice_round(now, prompt_msg_id, round_no):
    prompt_msg_id = int(prompt_msg_id or 0)
    round_no = int(round_no or 0)
    current_prompt_msg_id = int(state.get("concubine_heart_prompt_msg_id", 0) or 0)
    current_round_no = int(state.get("concubine_heart_round", 0) or 0)
    if current_prompt_msg_id == prompt_msg_id and current_round_no > round_no:
        return
    if current_prompt_msg_id == prompt_msg_id and current_round_no == round_no and _phase() == "heart_choice_pending":
        return
    state["concubine_heart_prompt_msg_id"] = int(prompt_msg_id or 0)
    state["concubine_heart_round"] = int(round_no or 0)
    state["concubine_heart_last_error"] = ""
    if _has_sent_heart_choice(prompt_msg_id, round_no):
        _wait_for_existing_heart_choice(now)
        return
    _set_phase("heart_choice_pending")
    state["next_concubine_time"] = now + _heart_next_choice_delay()
    _schedule_heart_choice_followup(
        get_current_identity_id(),
        state["next_concubine_time"],
        prompt_msg_id,
        round_no,
    )


def _schedule_after(now, min_sec, max_sec):
    state["next_concubine_time"] = float(now + random.uniform(min_sec, max_sec))
    return state["next_concubine_time"]


def _schedule_status_recheck(now):
    return _schedule_after(now, CONCUBINE_STATUS_RECHECK_MIN_SEC, CONCUBINE_STATUS_RECHECK_MAX_SEC)


def _schedule_chain_action(now):
    return _schedule_after(now, CONCUBINE_CHAIN_DELAY_MIN_SEC, CONCUBINE_CHAIN_DELAY_MAX_SEC)


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


def _voyage_command():
    return f"{CMD_CONCUBINE_VOYAGE} {CONCUBINE_VOYAGE_DEFAULT_ROUTE}"


def _voyage_return_at_from_wait(wait_text, now):
    wait_text = str(wait_text or "")
    if not has_wait_time(wait_text):
        return 0.0
    return float(now + parse_wait_time(wait_text) + CD_BUFFER_SEC)


def _voyage_unknown_return_at(now):
    existing = float(state.get("concubine_voyage_return_at", 0) or 0)
    if existing > now:
        return existing
    return 0.0


def _voyage_wait_text_from_return(raw_text):
    text = str(raw_text or "")
    matched = RE_VOYAGE_RETURN_WAIT.search(text) or RE_VOYAGE_LOCK_WAIT.search(text)
    if not matched:
        return ""
    wait_text = str(matched.group("wait") or "").strip()
    return wait_text if has_wait_time(wait_text) else ""


def _parse_voyage_text(text, now):
    raw_text = str(text or "")
    if not raw_text:
        return None

    matched = RE_VOYAGE_START.search(raw_text)
    if matched:
        return {
            "status": "sailing",
            "route": matched.group("route").strip(),
            "partner": matched.group("name").strip(),
            "return_at": _voyage_return_at_from_wait(matched.group("wait"), now),
            "result": "",
            "error": "",
        }

    matched = RE_VOYAGE_RETURN.search(raw_text)
    if matched:
        affinity_loss = RE_VOYAGE_AFFINITY_LOSS.search(raw_text)
        return {
            "status": "idle",
            "route": matched.group("route").strip(),
            "partner": matched.group("name").strip(),
            "return_at": 0.0,
            "result": raw_text.strip(),
            "error": "",
            "affinity_loss": _parse_count(affinity_loss.group("amount")) if affinity_loss else 0,
        }

    matched = RE_VOYAGE_STATUS_SAILING.search(raw_text)
    if matched:
        return {
            "status": "sailing",
            "route": matched.group("route").strip(),
            "partner": matched.group("name").strip(),
            "return_at": _voyage_return_at_from_wait(matched.group("wait"), now),
            "result": "",
            "error": "",
        }

    matched = RE_VOYAGE_STATUS_RETURNED.search(raw_text)
    if matched:
        return {
            "status": "returned",
            "route": matched.group("route").strip(),
            "partner": matched.group("name").strip(),
            "return_at": float(now),
            "result": "",
            "error": "",
        }

    matched = RE_VOYAGE_PANEL.search(raw_text)
    if matched:
        route = matched.group("route").strip()
        state_text = matched.group("state")
        tail = matched.group("tail") or ""
        if state_text == "进行中":
            return {
                "status": "sailing",
                "route": route,
                "partner": "",
                "return_at": _voyage_return_at_from_wait(tail, now),
                "result": "",
                "error": "",
            }
        return {
            "status": "returned",
            "route": route,
            "partner": "",
            "return_at": float(now),
            "result": "",
            "error": "",
        }

    if "当前并未执行远航任务" in raw_text:
        return {
            "status": "no_task",
            "route": "",
            "partner": "",
            "return_at": 0.0,
            "result": "",
            "error": raw_text.strip(),
            "clear_idle": True,
        }

    if "侍妾当前并无可结算的远航任务" in raw_text:
        return {"status": "no_task", "route": "", "partner": "", "return_at": 0.0, "result": "", "error": raw_text.strip()}

    wait_text = _voyage_wait_text_from_return(raw_text)
    if wait_text:
        return {
            "status": "sailing",
            "route": str(state.get("concubine_voyage_route") or "").strip(),
            "partner": "",
            "return_at": _voyage_return_at_from_wait(wait_text, now),
            "result": "",
            "error": "",
        }

    if _is_voyage_lock_text(raw_text):
        return {
            "status": "sailing",
            "route": str(state.get("concubine_voyage_route") or "").strip(),
            "partner": "",
            "return_at": _voyage_unknown_return_at(now),
            "result": "",
            "error": raw_text.strip(),
        }

    if "开启远航需要" in raw_text or ("远航" in raw_text and ("灵石不足" in raw_text or "修为不足" in raw_text or "资源不足" in raw_text)):
        return {"status": "idle", "route": "", "partner": "", "return_at": 0.0, "result": "", "error": raw_text.strip()}

    return None


def _is_voyage_lock_text(text):
    raw_text = str(text or "")
    return any(
        marker in raw_text
        for marker in (
            "侍妾正在远航途中",
            "侍妾仍在远航途中",
            "侍妾正在远航中",
            "侍妾仍在远航中",
        )
    )


def _apply_voyage_blocked_action(parsed, now, *, error_key, label):
    _apply_voyage_snapshot(parsed, now)
    state[error_key] = f"{label}被远航锁拦截，等待归航"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _schedule_voyage_wait(now)


def _handle_action_blocked_by_voyage(raw_text, now, *, error_key, label):
    voyage = _parse_voyage_text(raw_text, now)
    if not voyage or voyage.get("status") != "sailing":
        return False
    _apply_voyage_blocked_action(voyage, now, error_key=error_key, label=label)
    return True


def _apply_voyage_snapshot(parsed, now):
    if not parsed:
        return False
    status = str(parsed.get("status") or "").strip()
    route = str(parsed.get("route") or "").strip()
    partner = str(parsed.get("partner") or "").strip()
    previous_status = str(state.get("concubine_voyage_status") or "").strip()
    if partner:
        state["concubine_name"] = partner
    if route:
        state["concubine_voyage_route"] = route
    if status:
        state["concubine_voyage_status"] = status
    if status == "sailing":
        _clear_stale_tianji_summary_wait_error()
        return_at = float(parsed.get("return_at", 0) or 0)
        if return_at <= now:
            return_at = _voyage_unknown_return_at(now)
        state["concubine_voyage_return_at"] = return_at
        state["concubine_voyage_last_result"] = ""
        state["concubine_voyage_last_error"] = str(parsed.get("error") or "")
        state["concubine_voyage_retry_count"] = 0
        if _phase() in CONCUBINE_VOYAGE_PENDING_PHASES:
            _set_phase("idle")
            state["concubine_voyage_msg_id"] = 0
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
        affinity_loss = int(parsed.get("affinity_loss", 0) or 0)
        if affinity_loss > 0:
            _apply_affinity_loss(affinity_loss, now)
        state["concubine_voyage_retry_count"] = 0
        if _phase() in CONCUBINE_VOYAGE_PENDING_PHASES:
            _set_phase("idle")
            state["concubine_voyage_msg_id"] = 0
        _schedule_chain_action(now)
        return True
    if status == "no_task":
        should_clear = bool(parsed.get("clear_idle")) or previous_status not in {"sailing", "returned"}
        state["concubine_voyage_last_error"] = str(parsed.get("error") or "")
        if should_clear:
            state["concubine_voyage_status"] = "idle"
            state["concubine_voyage_return_at"] = 0
            state["concubine_voyage_retry_count"] = 0
            if _phase() in CONCUBINE_VOYAGE_PENDING_PHASES:
                _set_phase("idle")
                state["concubine_voyage_msg_id"] = 0
            _schedule_chain_action(now)
            return True
        state["concubine_voyage_status"] = previous_status or "sailing"
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
        return
    for key in (
        "concubine_voyage_msg_id",
        "concubine_voyage_status",
        "concubine_voyage_route",
        "concubine_voyage_return_at",
        "concubine_voyage_last_result",
        "concubine_voyage_last_error",
        "concubine_voyage_retry_count",
    ):
        state[key] = snapshot.get(key, state.get(key))
    if state.get("concubine_voyage_enabled") and snapshot.get("phase") in CONCUBINE_VOYAGE_PENDING_PHASES:
        _set_phase(snapshot.get("phase"))


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
    if str(state.get("concubine_voyage_status") or "") != "sailing":
        return False
    if int(state.get("concubine_voyage_retry_count", 0) or 0) > 0:
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


def _voyage_retry_send_kwargs(command):
    identity_id = int(get_current_identity_id() or 0)
    old_msg_id = int(state.get("concubine_voyage_msg_id", 0) or 0)
    chain_id = f"concubine_voyage_retry:{identity_id}:{old_msg_id}"
    return {
        "priority": "retry",
        "source_module": "侍妾远航",
        "op_id": f"{chain_id}:{str(command or '').strip()}",
        "chain_id": chain_id,
    }


def _schedule_voyage_wait(now):
    return_at = float(state.get("concubine_voyage_return_at", 0) or 0)
    if return_at > now:
        state["next_concubine_time"] = return_at + random.uniform(60, 600)
    else:
        state["next_concubine_time"] = now + CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC
    return state["next_concubine_time"]


def _is_voyage_affinity_eligible():
    return int(state.get("concubine_affinity", 0) or 0) >= CONCUBINE_VOYAGE_MIN_AFFINITY


def _is_voyage_eligible(now):
    if not state.get("concubine_voyage_enabled"):
        return False
    if not _has_available_partner():
        state["concubine_voyage_last_error"] = "远航需先确认侍妾"
        return False
    if not _is_voyage_affinity_eligible():
        affinity = int(state.get("concubine_affinity", 0) or 0)
        state["concubine_voyage_last_error"] = f"情缘不足（{affinity}/{CONCUBINE_VOYAGE_MIN_AFFINITY}），暂不远航"
        return False
    if _is_voyage_sailing(now) or _is_voyage_return_due(now):
        return False
    state["concubine_voyage_last_error"] = ""
    return True


def _is_daily_greet_due(now):
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
    return str(state.get("concubine_last_greet_day") or "") != _local_day_key(now)


def _is_gift_recovery_eligible(now):
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
    dream_due_at = float(state.get("concubine_dream_due_at", 0) or 0)
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
        dream_due_at = float(state.get("concubine_dream_due_at", 0) or 0)
        if dream_due_at <= now:
            return _schedule_chain_action(now)
        return _schedule_at_due_or_chain(now, dream_due_at)
    return _schedule_after_tianji(now)


def _retry_or_stop_daily_greet(now, reason):
    retry_count = max(0, int(state.get("concubine_greet_retry_count", 0) or 0))
    _set_phase("idle")
    _clear_non_heart_pending_msg_ids()
    if retry_count < CONCUBINE_GREET_MAX_RETRY_COUNT:
        state["concubine_greet_retry_count"] = retry_count + 1
        state["concubine_greet_last_error"] = (
            f"{reason}，稍后补发"
            f"（{state['concubine_greet_retry_count']}/{CONCUBINE_GREET_MAX_RETRY_COUNT}）"
        )
        return _schedule_after(now, CONCUBINE_GREET_RETRY_MIN_SEC, CONCUBINE_GREET_RETRY_MAX_SEC)
    state["concubine_last_greet_day"] = _local_day_key(now)
    state["concubine_greet_retry_count"] = 0
    state["concubine_greet_last_error"] = f"{reason}，已补发一次，今日不再补发"
    return _schedule_next_daily_greet_check(now)


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
    """Pending 超时后必须压住对应 due，避免下一轮因旧 due_at 立即重发。"""
    retry_at = _schedule_status_recheck(now)
    if phase == "status_pending":
        if state.get("concubine_enabled") and float(state.get("concubine_dream_due_at", 0) or 0) <= now:
            state["concubine_dream_due_at"] = retry_at
        if state.get("concubine_tianji_enabled") and float(state.get("concubine_tianji_due_at", 0) or 0) <= now:
            state["concubine_tianji_due_at"] = retry_at
        if state.get("concubine_heart_enabled") and float(state.get("concubine_heart_due_at", 0) or 0) <= now:
            state["concubine_heart_due_at"] = retry_at
    elif phase == "greet_pending":
        retry_at = _retry_or_stop_daily_greet(now, "每日问安等待回复超时")
    elif phase in CONCUBINE_GIFT_PHASES:
        state["concubine_last_gift_day"] = _local_day_key(now)
        state["concubine_gift_last_error"] = f"{phase} 等待回复超时，今日不再赠予"
        state["concubine_gift_amount"] = 0
    elif phase == "dream_pending":
        state["concubine_dream_due_at"] = retry_at
    elif phase == "tianji_pending":
        state["concubine_tianji_due_at"] = retry_at
    elif phase in {"heart_pending", "heart_choice_pending", "heart_choice_reply_pending"}:
        state["concubine_heart_due_at"] = retry_at
    elif phase in {"fragment_pending", "puzzle_pending"}:
        _mark_completed_fragment_incomplete_after_failed_chain()
        if float(state.get("concubine_dream_due_at", 0) or 0) <= now:
            state["concubine_dream_due_at"] = retry_at
    elif phase == "reacquire_pending":
        state["concubine_reacquire_blocked_until"] = retry_at
    return retry_at


async def _apply_concubine_resource_backoff(now, action_key, due_key, error_key, label, raw_text):
    backoff = record_resource_shortage(action_key, now, reason=raw_text)
    due_at = float(backoff.get("next_at", 0) or 0)
    state[due_key] = due_at
    state[error_key] = f"{label}资源不足: {str(raw_text or '')[:80]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    if action_key == CONCUBINE_TIANJI_RESOURCE_KEY:
        _schedule_after_tianji(now)
    else:
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
    try:
        return int(value or 0)
    except (TypeError, ValueError):
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
                with open(log_file, "r", encoding="utf-8") as handle:
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


def _tianji_due_from_logged_reply(text, event_ts):
    raw_text = str(text or "")
    event_ts = float(event_ts or 0)
    if event_ts <= 0:
        return None
    if "【天机代卜链】" in raw_text:
        gua_match = RE_TIANJI_GUA.search(raw_text)
        return {
            "due_at": event_ts + CONCUBINE_TIANJI_CD_SEC + CD_BUFFER_SEC,
            "chain": gua_match.group("name").strip() if gua_match else "",
            "source": "success",
        }
    if "天机链路尚未重铸" in raw_text:
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else CONCUBINE_TIANJI_CD_SEC
        return {
            "due_at": event_ts + wait_sec + CD_BUFFER_SEC,
            "chain": "",
            "source": "cooldown",
        }
    return None


def _find_recent_logged_tianji_cooldown(now):
    end_ts = float(now or 0) + CONCUBINE_LOG_REPLAY_LOOKAHEAD_SEC
    start_ts = max(0.0, end_ts - CONCUBINE_TIANJI_LOG_GUARD_LOOKBACK_SEC)
    sent_msgs = {}
    best = None
    for payload in _iter_message_log_entries_between(start_ts, end_ts):
        if not _payload_matches_game_topic(payload):
            continue
        event_ts = _parse_message_log_ts(payload.get("ts"))
        if event_ts <= 0 or event_ts < start_ts or event_ts > end_ts:
            continue
        event_type = str(payload.get("event_type") or "").strip()
        text = str(payload.get("text") or "").strip()
        msg_id = _msg_id_int(payload.get("message_id"))
        if (
            text == CMD_CONCUBINE_TIANJI
            and event_type in {"sent", "message"}
            and _sender_matches_current_identity(payload.get("sender_id"))
            and msg_id > 0
        ):
            sent_msgs.setdefault(msg_id, event_ts)
            continue
        if event_type not in {"message", "edit"}:
            continue
        reply_to_msg_id = _msg_id_int(payload.get("reply_to_msg_id"))
        if reply_to_msg_id <= 0 or reply_to_msg_id not in sent_msgs:
            continue
        due_info = _tianji_due_from_logged_reply(text, event_ts)
        if not due_info:
            continue
        due_at = float(due_info.get("due_at", 0) or 0)
        if due_at <= float(now or 0):
            continue
        if not best or due_at > float(best.get("due_at", 0) or 0):
            best = {
                "due_at": due_at,
                "chain": due_info.get("chain", ""),
                "source": due_info.get("source", ""),
                "msg_id": msg_id,
                "reply_to_msg_id": reply_to_msg_id,
                "event_ts": event_ts,
            }
    return best


def _guard_tianji_send_with_message_log(now):
    logged = _find_recent_logged_tianji_cooldown(now)
    if not logged:
        return False
    due_at = float(logged.get("due_at", 0) or 0)
    if due_at <= float(now or 0):
        return False
    state["concubine_tianji_due_at"] = max(float(state.get("concubine_tianji_due_at", 0) or 0), due_at)
    chain = str(logged.get("chain") or "").strip()
    if chain:
        state["concubine_tianji_chain"] = chain
        state["concubine_tianji_chain_due_at"] = max(float(state.get("concubine_tianji_chain_due_at", 0) or 0), due_at)
    state["concubine_tianji_last_error"] = ""
    if _phase() == "tianji_pending":
        _set_phase("idle")
    state["concubine_tianji_msg_id"] = 0
    _schedule_after_tianji(now)
    _record_concubine_event(
        "天机代卜临发拦截",
        kind="skipped",
        reason="logged_tianji_cooldown",
        phase=_phase(),
        command=CMD_CONCUBINE_TIANJI,
        msg_id=_msg_id_int(logged.get("msg_id")),
        detail=f"due_at={fmt_abs_ts(due_at)}｜source={logged.get('source')}",
        decision="tianji_send_blocked_by_message_log",
    )
    return True


def _find_recent_logged_heart_start(now):
    end_ts = float(now or 0) + CONCUBINE_LOG_REPLAY_LOOKAHEAD_SEC
    start_ts = max(0.0, float(now or 0) - CONCUBINE_HEART_GLOBAL_START_GAP_SEC)
    best = None
    for payload in _iter_message_log_entries_between(start_ts, end_ts):
        if not _payload_matches_game_topic(payload):
            continue
        event_ts = _parse_message_log_ts(payload.get("ts"))
        if event_ts <= 0 or event_ts < start_ts or event_ts > end_ts:
            continue
        if str(payload.get("event_type") or "").strip() != "sent":
            continue
        if str(payload.get("text") or "").strip() != CMD_CONCUBINE_HEART:
            continue
        source_module = str(payload.get("source_module") or "").strip()
        family = str(payload.get("family") or "").strip()
        if source_module and source_module != "共历心劫":
            continue
        if family and family != "concubine_heart":
            continue
        if best is None or event_ts > float(best.get("event_ts", 0) or 0):
            best = {
                "event_ts": event_ts,
                "sender_id": int(payload.get("sender_id", 0) or 0),
                "msg_id": _msg_id_int(payload.get("message_id")),
            }
    return best


def _guard_heart_start_with_message_log(now):
    logged = _find_recent_logged_heart_start(now)
    if not logged:
        return False
    event_ts = float(logged.get("event_ts", 0) or 0)
    if event_ts <= 0:
        return False
    remaining = event_ts + CONCUBINE_HEART_GLOBAL_START_GAP_SEC - float(now or 0)
    if remaining <= 0:
        return False
    delay = remaining + random.uniform(CONCUBINE_HEART_GLOBAL_DEFER_MIN_SEC, CONCUBINE_HEART_GLOBAL_DEFER_MAX_SEC)
    state["next_concubine_time"] = max(float(state.get("next_concubine_time", 0) or 0), float(now or 0) + delay)
    state["concubine_heart_last_error"] = "共历心劫全局串行等待，避免多号三轮抉择叠发"
    _record_concubine_event(
        "共历心劫全局串行等待",
        kind="skipped",
        reason="concubine_heart_global_start_guard",
        phase=_phase(),
        command=CMD_CONCUBINE_HEART,
        msg_id=_msg_id_int(logged.get("msg_id")),
        detail=f"last_sender={int(logged.get('sender_id', 0) or 0)}｜wait={int(delay)}s",
        decision="heart_start_global_guard",
    )
    return True


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


def _is_current_heart_prompt_message(reply_to=None, current_msg_id=0):
    expected_msg_id = _msg_id_int(state.get("concubine_heart_prompt_msg_id", 0))
    if expected_msg_id <= 0:
        return False
    return _msg_id_int(current_msg_id) == expected_msg_id or _msg_id_int(getattr(reply_to, "id", 0)) == expected_msg_id


def _is_strong_dream_terminal_text(text):
    raw_text = str(text or "")
    return (
        _is_dream_cooldown_text(raw_text)
        or "修为不足，共梦寻图" in raw_text
        or "【入梦寻图】" in raw_text
        or ("【全群异闻·" in raw_text and "残图】" in raw_text)
        or _is_voyage_lock_text(raw_text)
        or ("尚无侍妾" in raw_text and "共梦寻图" in raw_text)
    )


def _is_strong_tianji_terminal_text(text):
    raw_text = str(text or "")
    return (
        "【天机代卜链】" in raw_text
        or "天机链路尚未重铸" in raw_text
        or _is_tianji_resource_shortage_text(raw_text)
        or "情缘未至" in raw_text
        or "情缘未深" in raw_text
        or "无法为你卜算天机" in raw_text
        or _is_voyage_lock_text(raw_text)
        or ("尚无侍妾" in raw_text and "代卜天机" in raw_text)
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
    if phase == "tianji_pending":
        return _is_strong_tianji_terminal_text(raw_text) or "天机代卜" in raw_text or "代卜天机" in raw_text
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
            or "心劫余波" in raw_text
            or "心劫抉择正在进行" in raw_text
            or _is_voyage_lock_text(raw_text)
        )
    if phase in {"heart_pending", "heart_choice_pending"}:
        return (
            "坠魔心劫" in raw_text
            or "共历心劫" in raw_text
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
    if phase == "reacquire_pending":
        return (
            "新的道心侍妾" in raw_text
            or "成为你的侍妾" in raw_text
            or _is_no_partner_text(raw_text)
            or "赐婚" in raw_text
            or "红尘寻缘" in raw_text
            or "神念消耗过剧" in raw_text
            or ("请在" in raw_text and "后再试" in raw_text)
            or "冷却" in raw_text
            or _is_voyage_lock_text(raw_text)
        )
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
        with open(log_file, "r", encoding="utf-8") as handle:
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


def _pending_log_replay_spec(phase):
    if phase == "status_pending":
        return {
            "state_key": "concubine_status_msg_id",
            "command": CMD_CONCUBINE_STATUS,
            "family": "concubine_status",
            "handler": handle_concubine_status_reply,
            "current_msg_id": True,
        }
    if phase == "gift_status_pending":
        return {
            "state_key": "concubine_gift_status_msg_id",
            "command": CMD_CONCUBINE_STATUS,
            "family": "concubine_status",
            "handler": handle_concubine_status_reply,
            "current_msg_id": True,
        }
    if phase == "greet_pending":
        return {
            "state_key": "concubine_greet_msg_id",
            "command": CMD_CONCUBINE_DAILY_GREET,
            "family": "concubine_greet",
            "handler": handle_concubine_greet_reply,
        }
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
    if phase == "dream_pending":
        return {
            "state_key": "concubine_dream_msg_id",
            "command": CMD_CONCUBINE_DREAM,
            "family": "concubine_dream",
            "handler": handle_concubine_dream_reply,
        }
    if phase == "fragment_pending":
        return {
            "state_key": "concubine_fragment_msg_id",
            "command": CMD_CONCUBINE_FRAGMENT,
            "family": "concubine_fragment",
            "handler": handle_concubine_fragment_reply,
        }
    if phase == "puzzle_pending":
        return {
            "state_key": "concubine_puzzle_msg_id",
            "command": CMD_CONCUBINE_PUZZLE,
            "family": "concubine_puzzle",
            "handler": handle_concubine_puzzle_reply,
        }
    if phase == "reacquire_pending":
        command = str(state.get("concubine_reacquire_command_override") or "") or _get_reacquire_command()
        return {
            "state_key": "concubine_reacquire_msg_id",
            "command": command,
            "family": "concubine_reacquire",
            "handler": handle_concubine_reacquire_reply,
        }
    if phase == "tianji_pending":
        return {
            "state_key": "concubine_tianji_msg_id",
            "command": CMD_CONCUBINE_TIANJI,
            "family": "concubine_tianji",
            "handler": handle_concubine_tianji_reply,
        }
    if phase == "heart_pending":
        return {
            "state_key": "concubine_heart_msg_id",
            "command": CMD_CONCUBINE_HEART,
            "family": "concubine_heart",
            "handler": handle_concubine_heart_reply,
            "current_msg_id": True,
        }
    if phase == "heart_choice_reply_pending":
        return {
            "state_key": "concubine_heart_prompt_msg_id",
            "command": CMD_CONCUBINE_HEART_STEADY,
            "family": "concubine_heart",
            "handler": handle_concubine_heart_reply,
            "current_msg_id": True,
            "match_message_id": True,
        }
    if phase == "voyage_pending":
        return {
            "state_key": "concubine_voyage_msg_id",
            "command": _voyage_command(),
            "family": "concubine_voyage",
            "handler": handle_concubine_voyage_reply,
        }
    if phase == "voyage_return_pending":
        return {
            "state_key": "concubine_voyage_msg_id",
            "command": CMD_CONCUBINE_VOYAGE_RETURN,
            "family": "concubine_voyage",
            "handler": handle_concubine_voyage_reply,
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


async def _recover_concubine_pending_from_message_log(now, phase):
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


def _parse_wait_due_at(raw_text, now):
    text = str(raw_text or "").strip()
    if not text or "可施展" in text or "可用" in text:
        return 0.0
    if has_wait_time(text):
        return float(now + parse_wait_time(text) + CD_BUFFER_SEC)
    return 0.0


def _parse_tianji_chain(raw_text, now):
    text = str(raw_text or "").strip()
    if not text or text == "无":
        return "", 0.0
    matched = RE_TIANJI_CHAIN_REMAINING.search(text)
    if not matched:
        return text, 0.0
    name = matched.group("name").strip()
    wait_text = matched.group("wait").strip()
    due_at = float(now + parse_wait_time(wait_text) + CD_BUFFER_SEC) if has_wait_time(wait_text) else 0.0
    return name, due_at


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


def _iter_fragment_sections(text):
    raw_text = str(text or "")
    matches = list(RE_FRAGMENT_CONTEXT_KIND.finditer(raw_text))
    for index, matched in enumerate(matches):
        start = matched.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
        yield _normalize_fragment_kind(matched.group("kind")), raw_text[start:end]


def _confirmed_completed_fragment_kinds_from_reply(text):
    confirmed = []
    for kind, section in _iter_fragment_sections(text):
        progress = _parse_fragment_progresses(section).get(kind)
        if not progress:
            continue
        count, total = progress
        missing_match = RE_PUZZLE_MISSING.search(section)
        missing_text = missing_match.group(1).strip() if missing_match else ""
        if total > 0 and count >= total and (not missing_text or missing_text == "无"):
            confirmed.append(kind)
    if confirmed:
        return confirmed

    missing_match = RE_PUZZLE_MISSING.search(str(text or ""))
    missing_text = missing_match.group(1).strip() if missing_match else ""
    if _is_puzzle_ready() and (not missing_text or missing_text == "无"):
        return _completed_fragment_kinds()
    return []


def _parse_fragment_progress(text):
    progresses = _parse_fragment_progresses(text)
    if DREAM_KIND_XUTIAN in progresses:
        return progresses[DREAM_KIND_XUTIAN]
    for fragment_kind in FRAGMENT_KIND_ORDER:
        if fragment_kind in progresses:
            return progresses[fragment_kind]
    return None


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


def _is_current_fragment_confirmed():
    key = _fragment_confirmation_key()
    return bool(key and key == str(state.get("concubine_fragment_confirm_key") or ""))


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


def _parse_puzzle_success_kind(text):
    matched = RE_PUZZLE_SUCCESS_KIND.match(str(text or ""))
    if matched:
        return _normalize_fragment_kind(matched.group("kind"))
    raw_text = str(text or "")
    if "全群广播" in raw_text and "残图拼合" in raw_text:
        if "苍坤" in raw_text:
            return DREAM_KIND_CANGKUN
        if "虚天" in raw_text:
            return DREAM_KIND_XUTIAN
    return ""


def _select_fragment_kind_for_puzzle_result(text):
    parsed_kind = _parse_puzzle_success_kind(text)
    if parsed_kind:
        return parsed_kind
    raw_text = str(text or "")
    if "苍坤" in raw_text:
        return DREAM_KIND_CANGKUN
    if "虚天" in raw_text:
        return DREAM_KIND_XUTIAN
    completed = _completed_fragment_kinds()
    return completed[0] if completed else DREAM_KIND_XUTIAN


def _mark_completed_fragment_incomplete_after_failed_chain():
    completed = _completed_fragment_kinds()
    if not completed:
        return
    kind = completed[0]
    _, total = _get_fragment_progress(kind)
    _set_fragment_progress(kind, max(0, total - 1), total)


def _apply_dream_partner_hint(text):
    matched = RE_DREAM_PARTNER.search(str(text or ""))
    if not matched:
        return
    name = matched.group("name").strip()
    if name:
        state["concubine_name"] = name
        _set_availability("available")


def _current_partner_matches(name):
    expected_name = str(state.get("concubine_name") or "").strip()
    actual_name = str(name or "").strip()
    return bool(expected_name and actual_name and expected_name == actual_name)


def _parse_gift_success(text):
    matched = RE_CONCUBINE_GIFT_SUCCESS.search(str(text or ""))
    if not matched:
        return None
    return {
        "stone": _parse_count(matched.group("stone")),
        "name": matched.group("name").strip(),
        "amount": _parse_count(matched.group("amount")),
    }


def _finish_gift_recovery_today(now, reason):
    today = _local_day_key(now)
    state["concubine_last_gift_day"] = today
    state["concubine_gift_attempt_day"] = today
    state["concubine_gift_last_error"] = str(reason or "")
    _set_phase("idle")
    _clear_non_heart_pending_msg_ids()
    _schedule_affinity_recovery(now)


def _is_selfless_affinity_depletion_text(text):
    raw_text = str(text or "")
    return (
        "【无我之境】" in raw_text
        and "侍妾" in raw_text
        and "耗尽与你的所有情缘" in raw_text
        and "挡下此劫" in raw_text
    )


def _parse_selfless_partner_name(text):
    matched = RE_SELFLESS_PARTNER.search(str(text or ""))
    return matched.group("name").strip() if matched else ""


def is_concubine_affinity_event_candidate(text):
    raw_text = str(text or "")
    return "侍妾" in raw_text and "情缘" in raw_text and (
        "情缘增加了" in raw_text or _is_selfless_affinity_depletion_text(raw_text)
    )


def _apply_affinity_gain(partner_name, amount, now):
    if not _current_partner_matches(partner_name):
        return False
    return _apply_affinity_amount(amount, now)


def _apply_affinity_loss(amount, now):
    loss_amount = _parse_count(amount)
    if loss_amount <= 0:
        return False

    current_affinity = max(0, int(state.get("concubine_affinity", 0) or 0))
    new_affinity = max(0, current_affinity - loss_amount)
    state["concubine_affinity"] = new_affinity
    _set_availability("available")
    if state.get("concubine_kind") == "道心侍妾":
        if new_affinity < CONCUBINE_TIANJI_MIN_AFFINITY:
            state["concubine_tianji_last_error"] = f"远航损耗情缘（{new_affinity}/{CONCUBINE_TIANJI_MIN_AFFINITY}），等待问安/赠予恢复"
            _schedule_affinity_recovery(now)
        else:
            _normalize_tianji_affinity_error(now)
    return True


def _apply_affinity_amount(amount, now):
    try:
        gain_amount = int(str(amount or "").replace(",", ""))
    except (TypeError, ValueError):
        return False
    if gain_amount <= 0:
        return False

    current_affinity = max(0, int(state.get("concubine_affinity", 0) or 0))
    new_affinity = current_affinity + gain_amount
    state["concubine_affinity"] = new_affinity
    _set_availability("available")
    if state.get("concubine_kind") == "道心侍妾":
        if new_affinity < CONCUBINE_TIANJI_MIN_AFFINITY:
            state["concubine_tianji_last_error"] = f"情缘恢复中（{new_affinity}/{CONCUBINE_TIANJI_MIN_AFFINITY}），暂缓天机代卜"
            _schedule_affinity_recovery(now)
        else:
            _normalize_tianji_affinity_error(now)
    return True


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
    raw_text = text or ""
    matched = RE_CONCUBINE_HEAD.search(raw_text)
    if not matched:
        if _is_no_partner_text(raw_text):
            return {
                "has_partner": False,
                "not_eligible": _is_partner_not_eligible_text(raw_text),
                "manual_repair": _is_partner_manual_repair_text(raw_text),
            }
        return None

    affinity_match = RE_CONCUBINE_AFFINITY.search(raw_text)
    oath_match = RE_CONCUBINE_OATH.search(raw_text)
    dream_match = RE_DREAM_COOLDOWN.search(raw_text)
    tianji_match = RE_TIANJI_COOLDOWN.search(raw_text)
    heart_match = RE_HEART_COOLDOWN.search(raw_text)
    tianji_chain_match = RE_TIANJI_CHAIN.search(raw_text)
    tianji_chain, tianji_chain_due_at = _parse_tianji_chain(tianji_chain_match.group(1), now) if tianji_chain_match else ("", 0.0)
    progresses = _parse_fragment_progresses(raw_text)
    voyage = _parse_voyage_text(raw_text, now)

    return {
        "has_partner": True,
        "kind": matched.group("kind").strip(),
        "name": matched.group("name").strip(),
        "location": matched.group("location").strip(),
        "affinity": int(affinity_match.group(1)) if affinity_match else 0,
        "oath": oath_match.group(1).strip() if oath_match else "",
        "dream_due_at": _parse_wait_due_at(dream_match.group(1), now) if dream_match else 0.0,
        "tianji_due_at": _parse_wait_due_at(tianji_match.group(1), now) if tianji_match else 0.0,
        "heart_due_at": _parse_wait_due_at(heart_match.group(1), now) if heart_match else 0.0,
        "tianji_chain": tianji_chain,
        "tianji_chain_due_at": tianji_chain_due_at,
        "fragment_progresses": progresses,
        "voyage": voyage,
    }


def _merge_future_cooldown(state_key, parsed_due_at, now):
    parsed_due_at = float(parsed_due_at or 0)
    existing_due_at = float(state.get(state_key, 0) or 0)
    if existing_due_at > float(now) and parsed_due_at < existing_due_at:
        return existing_due_at
    return parsed_due_at


def _is_puzzle_ready():
    return bool(_completed_fragment_kinds())


def _has_available_partner():
    return state.get("concubine_availability") == "available" and bool((state.get("concubine_name") or "").strip())


def _has_main_due_action(now):
    if not _has_available_partner():
        return False
    if _is_puzzle_ready():
        return True
    return float(state.get("concubine_dream_due_at", 0) or 0) <= float(now)


def _is_tianji_affinity_blocked():
    return state.get("concubine_kind") == "道心侍妾" and int(state.get("concubine_affinity", 0) or 0) < CONCUBINE_TIANJI_MIN_AFFINITY


def _has_tianji_due_action(now):
    if not state.get("concubine_tianji_enabled"):
        return False
    if not _has_available_partner() or _is_tianji_affinity_blocked():
        return False
    return float(state.get("concubine_tianji_due_at", 0) or 0) <= float(now)


def _has_heart_due_action(now):
    if not state.get("concubine_heart_enabled"):
        return False
    if not _has_available_partner():
        return False
    return float(state.get("concubine_heart_due_at", 0) or 0) <= float(now)


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
    snapshot_at = float(state.get("concubine_last_snapshot_at", 0) or 0)
    if _has_heart_due_action(now):
        panel_msg_id = int(state.get("concubine_last_panel_msg_id", 0) or 0)
        return snapshot_at <= 0 or panel_msg_id <= 0 or float(now) - snapshot_at > CONCUBINE_HEART_PANEL_MAX_AGE_SEC
    return False


def _active_status_calibration_context(now):
    if state.get("concubine_enabled") and _has_main_due_action(now):
        return "入梦寻图", "concubine_last_error"
    if _has_tianji_due_action(now):
        return "天机代卜", "concubine_tianji_last_error"
    if _has_heart_due_action(now):
        return "共历心劫", "concubine_heart_last_error"
    return "侍妾状态校准", "concubine_last_error"


def _has_active_nanlong_pending(now):
    if not state.get("nanlong_enabled"):
        return False
    return (
        int(state.get("nanlong_reply_to_msg_id", 0) or 0) > 0
        and float(state.get("next_nanlong_time", 0) or 0) > 0
    )


def _clear_partner_snapshot(*, clear_voyage=True):
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
    blocked_until = float(state.get("concubine_reacquire_blocked_until", 0) or 0)
    if allow_reacquire and state.get("concubine_enabled") and state.get("concubine_auto_reacquire") and blocked_until > now:
        retry_at = min(retry_at, blocked_until)
    state["next_concubine_time"] = retry_at
    return retry_at


def _mark_no_partner(now, reason, *, allow_reacquire=True):
    _clear_partner_snapshot()
    _set_availability("no_partner")
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    state["concubine_last_snapshot_at"] = 0
    state["concubine_last_error"] = str(reason or "暂无侍妾")
    clear_pending_tasks_by_commands(CONCUBINE_PENDING_COMMANDS, send_as_id=get_current_identity_id())
    blocked_until = float(state.get("concubine_reacquire_blocked_until", 0) or 0)
    if allow_reacquire and state.get("concubine_auto_reacquire") and now >= blocked_until:
        _schedule_after(now, 60, 1200)
    else:
        _schedule_no_partner_check(now, allow_reacquire=allow_reacquire)
    mark_dirty()


def _freeze_no_partner_until(until, reason):
    now = time.time()
    blocked_until = max(float(until or 0), now + 60)
    _clear_partner_snapshot()
    _set_availability("no_partner")
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    clear_pending_tasks_by_commands(CONCUBINE_PENDING_COMMANDS, send_as_id=get_current_identity_id())
    state["concubine_last_snapshot_at"] = now
    state["concubine_last_error"] = str(reason or "侍妾暂不可补领")
    state["concubine_reacquire_blocked_until"] = blocked_until
    state["next_concubine_time"] = blocked_until
    mark_dirty()


def _apply_partner_acquired(name, now, *, kind="侍妾"):
    state["concubine_name"] = str(name or "").strip()
    state["concubine_kind"] = kind
    state["concubine_location"] = "待确认"
    state["concubine_affinity"] = 0
    state["concubine_oath"] = ""
    state["concubine_dream_due_at"] = 0
    state["concubine_tianji_due_at"] = 0
    state["concubine_heart_due_at"] = 0
    state["concubine_tianji_chain"] = ""
    state["concubine_tianji_chain_due_at"] = 0
    _clear_fragment_progress()
    state["concubine_last_snapshot_at"] = 0
    state["concubine_reacquire_attempts"] = 0
    state["concubine_reacquire_blocked_until"] = 0
    state["concubine_reacquire_command_override"] = ""
    state["concubine_last_error"] = ""
    _set_availability("available")
    _set_phase("idle")
    _clear_pending_msg_ids()
    _schedule_chain_action(now)


def _apply_status_snapshot(parsed, now):
    if not parsed:
        return False
    if not parsed.get("has_partner"):
        if parsed.get("manual_repair"):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "侍妾数据异常，等待人工修复")
            return True
        not_eligible = bool(parsed.get("not_eligible"))
        reason = "侍妾不可用：境界不足" if not_eligible else "暂无侍妾"
        _mark_no_partner(now, reason, allow_reacquire=bool(state.get("concubine_enabled")) and not not_eligible)
        return True

    heart_runtime = None
    if _is_heart_chain_active():
        heart_phase = _phase()
        if heart_phase not in CONCUBINE_HEART_ACTIVE_PHASES:
            heart_phase = "heart_choice_pending"
        heart_runtime = {
            "phase": heart_phase,
            "heart_msg_id": int(state.get("concubine_heart_msg_id", 0) or 0),
            "prompt_msg_id": int(state.get("concubine_heart_prompt_msg_id", 0) or 0),
            "round": int(state.get("concubine_heart_round", 0) or 0),
            "heart_due_at": float(state.get("concubine_heart_due_at", 0) or 0),
            "next_time": float(state.get("next_concubine_time", 0) or 0),
        }

    state["concubine_name"] = parsed.get("name", "")
    state["concubine_kind"] = parsed.get("kind", "")
    state["concubine_location"] = parsed.get("location", "")
    state["concubine_affinity"] = int(parsed.get("affinity", 0) or 0)
    state["concubine_oath"] = parsed.get("oath", "")
    state["concubine_dream_due_at"] = _merge_future_cooldown("concubine_dream_due_at", parsed.get("dream_due_at", 0), now)
    state["concubine_tianji_due_at"] = _merge_future_cooldown("concubine_tianji_due_at", parsed.get("tianji_due_at", 0), now)
    state["concubine_heart_due_at"] = _merge_future_cooldown("concubine_heart_due_at", parsed.get("heart_due_at", 0), now)
    state["concubine_tianji_chain"] = parsed.get("tianji_chain", "")
    state["concubine_tianji_chain_due_at"] = float(parsed.get("tianji_chain_due_at", 0) or 0)
    _apply_fragment_progresses(parsed.get("fragment_progresses") or {})
    _apply_voyage_snapshot(parsed.get("voyage"), now)
    state["concubine_last_snapshot_at"] = float(now)
    state["concubine_reacquire_command_override"] = ""
    state["concubine_last_error"] = ""
    _normalize_tianji_affinity_error(now)
    _set_availability("available")
    if int(state.get("concubine_affinity", 0) or 0) >= CONCUBINE_TIANJI_MIN_AFFINITY:
        state["concubine_greet_retry_count"] = 0
        state["concubine_gift_last_error"] = ""
    if heart_runtime:
        _clear_non_heart_pending_msg_ids()
        state["concubine_phase"] = heart_runtime["phase"]
        state["concubine_heart_msg_id"] = heart_runtime["heart_msg_id"]
        state["concubine_heart_prompt_msg_id"] = heart_runtime["prompt_msg_id"]
        state["concubine_heart_round"] = heart_runtime["round"]
        state["concubine_heart_due_at"] = heart_runtime["heart_due_at"]
        state["next_concubine_time"] = heart_runtime["next_time"]
        return True

    _set_phase("idle")
    _clear_pending_msg_ids()

    if _is_voyage_return_due(now):
        _schedule_chain_action(now)
        return True
    if _is_voyage_sailing(now):
        _schedule_voyage_wait(now)
        return True

    if _is_puzzle_ready() and state.get("concubine_enabled"):
        _schedule_chain_action(now)
    elif _has_tianji_due_action(now) and not state.get("concubine_enabled"):
        _schedule_chain_action(now)
    else:
        due_times = []
        if state.get("concubine_enabled"):
            due_times.append(float(state.get("concubine_dream_due_at", 0) or 0))
        if state.get("concubine_tianji_enabled") and not _is_tianji_affinity_blocked():
            due_times.append(float(state.get("concubine_tianji_due_at", 0) or 0))
        if state.get("concubine_heart_enabled"):
            due_times.append(float(state.get("concubine_heart_due_at", 0) or 0))
        due_at = min(due_times) if due_times else now + CONCUBINE_STATUS_STALE_SEC
        _schedule_at_due_or_chain(now, due_at)
    return True


def _get_reacquire_command():
    override = str(state.get("concubine_reacquire_command_override") or "").strip()
    if override in CONCUBINE_REACQUIRE_COMMANDS:
        return override
    profile = get_send_as_profile()
    return CMD_CONCUBINE_SECT_MARRY if str(profile.get("sect_name") or "").strip() == "星宫" else CMD_CONCUBINE_ROMANCE


def _switch_reacquire_command(now, command, reason):
    state["concubine_reacquire_command_override"] = command if command in CONCUBINE_REACQUIRE_COMMANDS else ""
    state["concubine_last_error"] = reason
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    _schedule_chain_action(now)


def get_concubine_status_text():
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
    if _phase() in CONCUBINE_HEART_ACTIVE_PHASES:
        _close_heart_chain_without_settlement(now, f"{_phase()}_startup_restore")
        mark_dirty()
        return float(state.get("next_concubine_time", 0) or 0)
    if _reconcile_stale_heart_action_guard(now, "heart_stale_guard_startup_restore"):
        mark_dirty()
        return float(state.get("next_concubine_time", 0) or 0)
    if _phase() in {"status_pending", "greet_pending", "gift_status_pending", "gift_bag_pending", "gift_pending", "dream_pending", "fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending", "heart_pending", "heart_choice_pending", "heart_choice_reply_pending"} | CONCUBINE_VOYAGE_PENDING_PHASES:
        if _has_available_partner():
            _set_phase("idle")
        elif state.get("concubine_availability") == "no_partner":
            _set_phase("no_partner")
        else:
            _set_phase("idle")
        _clear_pending_msg_ids()
    if _is_voyage_return_due(now):
        _schedule_chain_action(now)
    elif _is_voyage_sailing(now):
        _schedule_voyage_wait(now)
    if float(state.get("next_concubine_time", 0) or 0) <= 0:
        state["next_concubine_time"] = float(now + random.uniform(60, 1200))
    mark_dirty()
    return float(state.get("next_concubine_time", 0) or 0)


async def _send_status_command(now):
    if _defer_active_for_phaseful_summary(now, "侍妾状态校准"):
        save_state()
        return False
    msg = await send_game_command(CMD_CONCUBINE_STATUS, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = "发送 .我的侍妾 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "status_pending")
        save_state()
        return False
    _set_phase("status_pending")
    state["concubine_status_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_greet_command(now):
    if _defer_active_for_phaseful_summary(now, "每日问安", error_key="concubine_greet_last_error"):
        save_state()
        return False
    msg = await send_game_command(CMD_CONCUBINE_DAILY_GREET, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_greet_last_error"] = "发送 .每日问安 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "greet_pending")
        save_state()
        return False
    _set_phase("greet_pending")
    state["concubine_greet_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["concubine_greet_last_error"] = ""
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_gift_status_command(now):
    if _defer_active_for_phaseful_summary(now, "赠予侍妾", error_key="concubine_gift_last_error"):
        save_state()
        return False
    msg = await send_game_command(CMD_CONCUBINE_STATUS, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        _finish_gift_recovery_today(sent_at, "发送 .我的侍妾 失败，今日不再赠予")
        save_state()
        return False
    state["concubine_gift_attempt_day"] = _local_day_key(sent_at)
    _set_phase("gift_status_pending")
    state["concubine_gift_status_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["concubine_gift_last_error"] = ""
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_gift_bag_command(now):
    if _defer_active_for_phaseful_summary(now, "赠予侍妾", error_key="concubine_gift_last_error"):
        save_state()
        return False
    msg = await send_game_command(CMD_STORAGE_BAG, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        _finish_gift_recovery_today(sent_at, "发送 .储物袋 失败，今日不再赠予")
        save_state()
        return False
    _set_phase("gift_bag_pending")
    state["concubine_gift_bag_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["concubine_gift_last_error"] = ""
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_gift_command(now, amount):
    if _defer_active_for_phaseful_summary(now, "赠予侍妾", error_key="concubine_gift_last_error"):
        save_state()
        return False
    gift_amount = max(0, int(amount or 0))
    if gift_amount <= 0:
        state["concubine_gift_last_error"] = "赠予数量为 0，跳过"
        _set_phase("idle")
        _clear_non_heart_pending_msg_ids()
        _schedule_affinity_recovery(now)
        save_state()
        return False
    command = f"{CMD_CONCUBINE_GIFT_STONE} 灵石*{gift_amount}"
    msg = await send_game_command(command, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        _finish_gift_recovery_today(sent_at, f"发送 {command} 失败，今日不再赠予")
        save_state()
        return False
    _set_phase("gift_pending")
    state["concubine_gift_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["concubine_gift_amount"] = gift_amount
    state["concubine_gift_last_error"] = ""
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_dream_command(now):
    if _defer_active_for_phaseful_summary(now, "入梦寻图", allow_replayable_trigger=True):
        save_state()
        return False
    msg = await send_game_command(CMD_CONCUBINE_DREAM, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = "发送 .入梦寻图 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "dream_pending")
        _record_concubine_event(
            "入梦寻图发送失败",
            kind="skipped",
            reason="concubine_send_failed",
            phase="idle",
            command=CMD_CONCUBINE_DREAM,
            decision="dream_send_failed",
            workflow_status="failed",
        )
        save_state()
        return False
    _set_phase("dream_pending")
    state["concubine_dream_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    _record_concubine_event(
        "入梦寻图已发送",
        kind="changed",
        phase="dream_pending",
        command=CMD_CONCUBINE_DREAM,
        msg_id=state["concubine_dream_msg_id"],
        decision="dream_sent",
        workflow_status="sent",
    )
    save_state()
    return True


async def _send_fragment_command(now):
    if _defer_active_for_phaseful_summary(now, "残图确认"):
        save_state()
        return False
    if _is_current_fragment_confirmed():
        _set_phase("puzzle_ready")
        next_time = float(state.get("next_concubine_time", 0) or 0)
        if next_time <= 0 or next_time > now:
            state["next_concubine_time"] = float(now)
        save_state()
        return False

    msg = await send_game_command(CMD_CONCUBINE_FRAGMENT, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = "发送 .残图 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "fragment_pending")
        save_state()
        return False
    _set_phase("fragment_pending")
    state["concubine_fragment_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_puzzle_command(now):
    if _defer_active_for_phaseful_summary(now, "残图拼合"):
        save_state()
        return False
    msg = await send_game_command(CMD_CONCUBINE_PUZZLE, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = "发送 .拼图 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "puzzle_pending")
        save_state()
        return False
    _set_phase("puzzle_pending")
    state["concubine_puzzle_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_reacquire_command(now):
    if _defer_active_for_phaseful_summary(now, "侍妾补领"):
        save_state()
        return False
    command = _get_reacquire_command()
    msg = await send_game_command(command, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = f"发送 {command} 失败"
        _set_phase("no_partner")
        _backoff_after_pending_timeout(sent_at, "reacquire_pending")
        save_state()
        return False
    _set_phase("reacquire_pending")
    state["concubine_reacquire_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["concubine_reacquire_attempts"] = int(state.get("concubine_reacquire_attempts", 0) or 0) + 1
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_tianji_command(now):
    if _guard_tianji_send_with_message_log(now):
        save_state()
        return False
    if _defer_active_for_phaseful_summary(now, "天机代卜", error_key="concubine_tianji_last_error"):
        save_state()
        return False
    msg = await send_game_command(CMD_CONCUBINE_TIANJI, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_tianji_last_error"] = "发送 .天机代卜 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "tianji_pending")
        _record_concubine_event(
            "天机代卜发送失败",
            kind="skipped",
            reason="concubine_send_failed",
            phase="idle",
            command=CMD_CONCUBINE_TIANJI,
            decision="tianji_send_failed",
            workflow_status="failed",
        )
        save_state()
        return False
    _set_phase("tianji_pending")
    state["concubine_tianji_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    _record_concubine_event(
        "天机代卜已发送",
        kind="changed",
        phase="tianji_pending",
        command=CMD_CONCUBINE_TIANJI,
        msg_id=state["concubine_tianji_msg_id"],
        decision="tianji_sent",
        workflow_status="sent",
    )
    save_state()
    return True


async def _send_heart_command(now):
    if _defer_active_for_phaseful_summary(now, "共历心劫", error_key="concubine_heart_last_error"):
        save_state()
        return False
    if _is_heart_chain_active():
        if int(state.get("concubine_heart_prompt_msg_id", 0) or 0) > 0 and _phase() not in CONCUBINE_HEART_ACTIVE_PHASES:
            _set_phase("heart_choice_pending")
            state["next_concubine_time"] = now + random.uniform(
                CONCUBINE_HEART_CHOICE_DELAY_MIN_SEC,
                CONCUBINE_HEART_CHOICE_DELAY_MAX_SEC,
            )
        elif float(state.get("next_concubine_time", 0) or 0) <= now:
            state["next_concubine_time"] = now + random.uniform(5 * 60, 10 * 60)
        state["concubine_heart_last_error"] = "已有心劫链路未结算，跳过重复发起"
        _record_concubine_event(
            "共历心劫已有链路",
            kind="skipped",
            reason="concubine_heart_chain_active",
            phase=_phase(),
            command=CMD_CONCUBINE_HEART,
            detail=f"prompt_msg_id={int(state.get('concubine_heart_prompt_msg_id', 0) or 0)}｜round={int(state.get('concubine_heart_round', 0) or 0)}",
            decision="heart_chain_already_active",
        )
        save_state()
        return False

    if _guard_heart_start_with_message_log(now):
        save_state()
        return False

    panel_msg_id = int(state.get("concubine_last_panel_msg_id", 0) or 0)
    panel_seen_at = float(state.get("concubine_last_snapshot_at", 0) or 0)
    if panel_msg_id <= 0 or panel_seen_at <= 0 or now - panel_seen_at > CONCUBINE_HEART_PANEL_MAX_AGE_SEC:
        state["concubine_heart_last_error"] = "共历心劫需先刷新侍妾面板"
        await _send_status_command(now)
        return False
    msg = await send_game_command(CMD_CONCUBINE_HEART, track=False, reply_to=panel_msg_id, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else float(now)
    if not msg:
        guard_blocks_until = _heart_action_guard_blocks_until(sent_at)
        if guard_blocks_until > sent_at:
            _close_heart_chain_without_settlement(
                sent_at,
                "heart_send_blocked_by_stale_guard",
                detail=f"panel_msg_id={panel_msg_id}｜guard_until={fmt_abs_ts(guard_blocks_until)}",
            )
            save_state()
            return False
        state["concubine_heart_last_error"] = "发送 .共历心劫 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "heart_pending")
        _record_concubine_event(
            "共历心劫发送失败",
            kind="skipped",
            reason="concubine_send_failed",
            phase="idle",
            command=CMD_CONCUBINE_HEART,
            detail=f"panel_msg_id={panel_msg_id}",
            decision="heart_send_failed",
            workflow_status="failed",
        )
        save_state()
        return False
    _set_phase("heart_pending")
    state["concubine_heart_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["concubine_heart_prompt_msg_id"] = 0
    state["concubine_heart_round"] = 0
    _clear_heart_choice_guard()
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    _record_concubine_event(
        "共历心劫已发送",
        kind="changed",
        phase="heart_pending",
        command=CMD_CONCUBINE_HEART,
        msg_id=state["concubine_heart_msg_id"],
        detail=f"panel_msg_id={panel_msg_id}",
        decision="heart_sent",
        workflow_status="sent",
    )
    save_state()
    return True


async def _send_voyage_return_command(now, *, is_retry=False):
    if _defer_active_for_phaseful_summary(now, "远航归来", error_key="concubine_voyage_last_error", allow_replayable_trigger=True):
        save_state()
        return False
    if not is_retry:
        state["concubine_voyage_retry_count"] = 0
    send_kwargs = _voyage_retry_send_kwargs(CMD_CONCUBINE_VOYAGE_RETURN) if is_retry else {"priority": "chain"}
    msg = await send_game_command(CMD_CONCUBINE_VOYAGE_RETURN, track=False, **send_kwargs)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_voyage_last_error"] = "发送 .远航归来 失败"
        if is_retry:
            state["concubine_voyage_retry_count"] = max(int(state.get("concubine_voyage_retry_count", 0) or 0), 2)
        _set_phase("idle")
        state["concubine_voyage_msg_id"] = 0
        state["next_concubine_time"] = sent_at + CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC
        save_state()
        return False
    _set_phase("voyage_return_pending")
    state["concubine_voyage_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC
    save_state()
    return True


async def _send_voyage_status_command(now):
    if _defer_active_for_phaseful_summary(now, "远航状态校准", error_key="concubine_voyage_last_error"):
        save_state()
        return False
    msg = await send_game_command(
        CMD_CONCUBINE_VOYAGE_STATUS,
        track=False,
        priority="chain",
        source_module="侍妾远航",
    )
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_voyage_last_error"] = "发送 .远航状态 失败"
        state["next_concubine_time"] = sent_at + CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC
        save_state()
        return False
    state["concubine_voyage_last_error"] = "远航结算补发已耗尽，已改为状态校准"
    state["next_concubine_time"] = sent_at + CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC
    save_state()
    return True


async def _send_voyage_command(now, *, is_retry=False):
    if _defer_active_for_phaseful_summary(now, "侍妾远航", error_key="concubine_voyage_last_error", allow_replayable_trigger=True):
        save_state()
        return False
    if not _is_voyage_eligible(now):
        _schedule_after_tianji(now)
        save_state()
        return False
    if not is_retry:
        state["concubine_voyage_retry_count"] = 0
    command = _voyage_command()
    send_kwargs = _voyage_retry_send_kwargs(command) if is_retry else {"priority": "chain"}
    msg = await send_game_command(command, track=False, **send_kwargs)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_voyage_last_error"] = f"发送 {command} 失败"
        if is_retry:
            state["concubine_voyage_retry_count"] = max(int(state.get("concubine_voyage_retry_count", 0) or 0), 2)
        _set_phase("idle")
        state["concubine_voyage_msg_id"] = 0
        state["next_concubine_time"] = sent_at + CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC
        save_state()
        return False
    _set_phase("voyage_pending")
    state["concubine_voyage_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["concubine_voyage_route"] = command.replace(CMD_CONCUBINE_VOYAGE, "", 1).strip() or CONCUBINE_VOYAGE_DEFAULT_ROUTE
    _clear_stale_tianji_summary_wait_error()
    state["next_concubine_time"] = sent_at + CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC
    save_state()
    return True


def _mark_voyage_pending_exhausted(now, phase):
    route = str(state.get("concubine_voyage_route") or "").strip() or CONCUBINE_VOYAGE_DEFAULT_ROUTE
    if not state.get("concubine_voyage_status"):
        state["concubine_voyage_status"] = "sailing"
    if not state.get("concubine_voyage_route"):
        state["concubine_voyage_route"] = route
    state["concubine_voyage_msg_id"] = 0
    state["concubine_voyage_retry_count"] = max(int(state.get("concubine_voyage_retry_count", 0) or 0), 2)
    state["concubine_voyage_last_error"] = f"{phase} 两次无回复，保持远航锁等待后续状态"
    _set_phase("idle")
    _schedule_voyage_wait(now)
    return float(state.get("next_concubine_time", 0) or 0)


def _defer_voyage_timeout_for_log_settle(now, phase):
    pending_until = float(state.get("next_concubine_time", 0) or 0)
    if pending_until <= 0:
        return False
    settle_until = pending_until + CONCUBINE_VOYAGE_LOG_SETTLE_SEC
    if float(now or 0) >= settle_until:
        return False
    state["next_concubine_time"] = settle_until
    state["concubine_voyage_last_error"] = f"{phase} 等待日志沉淀，暂缓补发"
    return True


async def _handle_voyage_pending_timeout(now, phase):
    if await _recover_concubine_pending_from_message_log(now, phase):
        return True
    if _defer_voyage_timeout_for_log_settle(now, phase):
        save_state()
        return True
    await _audit_pending_timeout_candidates(now, phase)
    retry_count = int(state.get("concubine_voyage_retry_count", 0) or 0)
    if retry_count < 1:
        state["concubine_voyage_retry_count"] = retry_count + 1
        if phase == "voyage_return_pending":
            sent = await _send_voyage_return_command(now, is_retry=True)
        else:
            sent = await _send_voyage_command(now, is_retry=True)
        if sent:
            await send_audit_log(
                f"↩️ 侍妾远航 {phase} 未见回复，短保护窗后已补发一次。",
                scope="identity",
                limit=180,
                priority="low",
            )
        return True

    retry_at = _mark_voyage_pending_exhausted(now, phase)
    save_state()
    await send_audit_log(
        f"⚠️ 侍妾远航 {phase} 补发后仍无回复，保持本地远航锁；{fmt_time_after(max(0, retry_at - now))} 后再观察。",
        scope="identity",
        limit=260,
    )
    return True


async def _send_heart_choice(now):
    prompt_msg_id = int(state.get("concubine_heart_prompt_msg_id", 0) or 0)
    round_no = int(state.get("concubine_heart_round", 0) or 0)
    if prompt_msg_id <= 0:
        state["concubine_heart_last_error"] = "心劫抉择缺少提示消息ID"
        _set_phase("idle")
        _backoff_after_pending_timeout(now, "heart_choice_pending")
        _record_concubine_event(
            "心劫抉择缺少提示",
            kind="skipped",
            reason="concubine_heart_missing_prompt",
            phase="idle",
            command=CMD_CONCUBINE_HEART_STEADY,
            decision="heart_choice_missing_prompt",
        )
        save_state()
        return False
    if round_no not in {1, 2, 3}:
        state["concubine_heart_last_error"] = "心劫抉择轮次异常，暂停自动处理"
        _set_phase("idle")
        _backoff_after_pending_timeout(now, "heart_choice_pending")
        _record_concubine_event(
            "心劫抉择轮次异常",
            kind="skipped",
            reason="concubine_heart_invalid_round",
            phase="idle",
            command=CMD_CONCUBINE_HEART_STEADY,
            detail=f"prompt_msg_id={prompt_msg_id}｜round={round_no}",
            decision="heart_choice_invalid_round",
        )
        save_state()
        return False
    if _has_sent_heart_choice(prompt_msg_id, round_no):
        _wait_for_existing_heart_choice(now)
        state["concubine_heart_last_error"] = f"心劫第 {round_no} 轮已发送 .稳，等待回合推进"
        _record_concubine_event(
            "心劫抉择已发送",
            kind="skipped",
            reason="concubine_heart_choice_duplicate_guard",
            phase="heart_choice_reply_pending",
            command=CMD_CONCUBINE_HEART_STEADY,
            detail=f"prompt_msg_id={prompt_msg_id}｜round={round_no}",
            decision="heart_choice_duplicate_guard",
        )
        save_state()
        return False
    msg = await send_game_command(CMD_CONCUBINE_HEART_STEADY, track=False, reply_to=prompt_msg_id, priority="urgent_reactive")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_heart_last_error"] = "发送 .稳 失败"
        state["next_concubine_time"] = sent_at + random.uniform(10 * 60, 30 * 60)
        _record_concubine_event(
            "心劫抉择发送失败",
            kind="skipped",
            reason="concubine_send_failed",
            phase=_phase(),
            command=CMD_CONCUBINE_HEART_STEADY,
            detail=f"prompt_msg_id={prompt_msg_id}｜round={round_no}",
            decision="heart_choice_send_failed",
            workflow_status="failed",
        )
        save_state()
        return False
    _mark_heart_choice_sent(prompt_msg_id, round_no, sent_at)
    _set_phase("heart_choice_reply_pending")
    state["next_concubine_time"] = sent_at + 45
    _record_concubine_event(
        "心劫抉择已发送",
        kind="changed",
        phase="heart_choice_reply_pending",
        command=CMD_CONCUBINE_HEART_STEADY,
        msg_id=int(getattr(msg, "id", 0) or 0),
        detail=f"prompt_msg_id={prompt_msg_id}｜round={round_no}",
        decision="heart_choice_sent",
        workflow_status="sent",
    )
    save_state()
    return True


async def _retry_heart_choice_once(now):
    prompt_msg_id = int(state.get("concubine_heart_prompt_msg_id", 0) or 0)
    round_no = int(state.get("concubine_heart_round", 0) or 0)
    retry_count = int(state.get("concubine_heart_choice_retry_count", 0) or 0)
    if prompt_msg_id <= 0 or round_no not in {1, 2, 3}:
        return False
    if retry_count >= CONCUBINE_HEART_CHOICE_MAX_RETRY_COUNT:
        return False

    msg = await send_game_command(CMD_CONCUBINE_HEART_STEADY, track=False, reply_to=prompt_msg_id, priority="urgent_reactive")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    state["concubine_heart_choice_retry_count"] = retry_count + 1
    state["concubine_heart_choice_sent_at"] = sent_at
    _set_phase("heart_choice_reply_pending")
    state["next_concubine_time"] = sent_at + 45
    if not msg:
        state["concubine_heart_last_error"] = f"心劫第 {round_no} 轮 .稳 补发失败，等待回合推进"
        _record_concubine_event(
            "心劫抉择补发失败",
            kind="skipped",
            reason="concubine_heart_choice_retry_send_failed",
            phase="heart_choice_reply_pending",
            command=CMD_CONCUBINE_HEART_STEADY,
            detail=f"prompt_msg_id={prompt_msg_id}｜round={round_no}｜retry={retry_count + 1}",
            decision="heart_choice_retry_send_failed",
            workflow_status="failed",
        )
        save_state()
        return True

    state["concubine_heart_last_error"] = f"心劫第 {round_no} 轮 .稳 未见推进，已补发一次"
    _record_concubine_event(
        "心劫抉择已补发",
        kind="changed",
        phase="heart_choice_reply_pending",
        command=CMD_CONCUBINE_HEART_STEADY,
        msg_id=int(getattr(msg, "id", 0) or 0),
        detail=f"prompt_msg_id={prompt_msg_id}｜round={round_no}｜retry={retry_count + 1}",
        decision="heart_choice_retry_sent",
        workflow_status="sent",
    )
    save_state()
    return True


async def handle_concubine_status_reply(text, now, reply_to, matched_family=None, current_msg_id=0):
    if (
        not state.get("concubine_enabled", False)
        and not state.get("concubine_tianji_enabled", False)
        and not state.get("concubine_heart_enabled", False)
        and not state.get("concubine_voyage_enabled", False)
    ):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_status" and CMD_CONCUBINE_STATUS not in orig_cmd:
        return False
    phase = _phase()
    gift_status_flow = phase == "gift_status_pending"
    gift_status_can_continue = gift_status_flow and (
        str(state.get("concubine_gift_attempt_day") or "") == _local_day_key(now)
        or int(state.get("concubine_gift_status_msg_id", 0) or 0) > 0
    )
    if phase not in {"status_pending", "gift_status_pending"}:
        if phase in {"greet_pending", "gift_bag_pending", "gift_pending", "dream_pending", "fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending", "heart_pending", "heart_choice_pending", "heart_choice_reply_pending"} | CONCUBINE_VOYAGE_PENDING_PHASES:
            console_log(f"🌸 忽略非等待期侍妾状态回复（phase={phase}）。")
            _record_concubine_ignored_reply("侍妾状态", reason="concubine_phase_mismatch", phase=phase, reply_to=reply_to, current_msg_id=current_msg_id)
            return True
        console_log(f"🌸 接受迟到的侍妾状态回复（phase={phase}）。")
    elif not _is_current_reply(reply_to, "concubine_gift_status_msg_id" if gift_status_flow else "concubine_status_msg_id"):
        console_log("🌸 忽略迟到的侍妾状态回复。")
        _record_concubine_ignored_reply(
            "侍妾状态",
            reason="concubine_msg_id_mismatch",
            phase=phase,
            state_key="concubine_gift_status_msg_id" if gift_status_flow else "concubine_status_msg_id",
            reply_to=reply_to,
            current_msg_id=current_msg_id,
        )
        return True

    parsed = _parse_status_panel(text, now)
    if not parsed:
        state["concubine_last_error"] = f"未识别的侍妾状态回复: {(text or '')[:60]}"
        _set_phase("idle")
        _clear_pending_msg_ids()
        _backoff_after_pending_timeout(now, "gift_status_pending" if gift_status_flow else "status_pending")
        save_state()
        return False

    _apply_status_snapshot(parsed, now)
    state["concubine_last_panel_msg_id"] = int(current_msg_id or 0)
    if gift_status_flow:
        if gift_status_can_continue and _is_gift_recovery_eligible(now):
            await _send_gift_bag_command(now)
            return True
        if int(state.get("concubine_affinity", 0) or 0) >= CONCUBINE_TIANJI_MIN_AFFINITY:
            state["concubine_gift_last_error"] = ""
            _schedule_after_tianji(now)
        else:
            state["concubine_gift_last_error"] = "状态确认后不满足赠予条件，暂不赠予"
            _schedule_affinity_recovery(now)
        save_state()
        return True
    save_state()
    if _is_puzzle_ready():
        completed_text = _format_completed_fragment_progresses()
        await send_audit_log(f"🌸 残图已凑齐（{completed_text}），先自动 .残图 确认后再拼图。", scope="identity")
    return True


async def handle_concubine_dream_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_dream" and CMD_CONCUBINE_DREAM not in orig_cmd:
        return False
    phase = _phase()
    if phase != "dream_pending":
        if phase in {"greet_pending", "fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending", "heart_pending", "heart_choice_pending", "heart_choice_reply_pending"}:
            console_log(f"🌸 忽略非等待期入梦寻图回复（phase={phase}）。")
            _record_concubine_ignored_reply("入梦寻图", reason="concubine_phase_mismatch", phase=phase, reply_to=reply_to)
            return True
        console_log(f"🌸 接受手动/迟到的入梦寻图回复（phase={phase}）。")
    elif not _is_current_reply(reply_to, "concubine_dream_msg_id") and not _is_strong_dream_terminal_text(text):
        console_log("🌸 忽略迟到的入梦寻图回复。")
        _record_concubine_ignored_reply(
            "入梦寻图",
            reason="concubine_msg_id_mismatch",
            phase=phase,
            state_key="concubine_dream_msg_id",
            reply_to=reply_to,
        )
        return True

    raw_text = text or ""
    voyage = _parse_voyage_text(raw_text, now)
    if voyage and voyage.get("status") == "sailing":
        _apply_voyage_blocked_action(voyage, now, error_key="concubine_last_error", label="入梦寻图")
        save_state()
        return True

    if _is_dream_cooldown_text(raw_text):
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else CONCUBINE_DREAM_CD_SEC
        state["concubine_dream_due_at"] = now + max(wait_sec + CD_BUFFER_SEC, CONCUBINE_DREAM_MIN_RETRY_SEC)
        state["concubine_last_error"] = ""
        reset_resource_shortage(CONCUBINE_DREAM_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_after_tianji(now)
        save_state()
        return True

    if "修为不足" in raw_text or "灵石不足" in raw_text or "资源不足" in raw_text:
        await _apply_concubine_resource_backoff(
            now,
            CONCUBINE_DREAM_RESOURCE_KEY,
            "concubine_dream_due_at",
            "concubine_last_error",
            "入梦寻图",
            raw_text,
        )
        save_state()
        return True

    if _is_no_partner_text(raw_text):
        if _is_partner_manual_repair_text(raw_text):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "入梦寻图失败：侍妾数据异常，等待人工修复")
            save_state()
            return True
        not_eligible = _is_partner_not_eligible_text(raw_text)
        reason = "入梦寻图失败：境界不足" if not_eligible else "入梦寻图失败：暂无侍妾"
        _mark_no_partner(now, reason, allow_reacquire=not not_eligible)
        save_state()
        return True

    progresses = _parse_fragment_progresses(raw_text)
    if "【入梦寻图】" in raw_text:
        _apply_dream_partner_hint(raw_text)
        _apply_fragment_progresses(progresses)
        state["concubine_dream_due_at"] = now + CONCUBINE_DREAM_CD_SEC + CD_BUFFER_SEC
        state["concubine_last_error"] = ""
        reset_resource_shortage(CONCUBINE_DREAM_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        if _is_puzzle_ready():
            _schedule_chain_action(now)
            save_state()
            await send_audit_log(f"🌸 入梦寻图已达 4/4（{_format_completed_fragment_progresses()}），将先 .残图 确认。", scope="identity")
        else:
            _schedule_after_tianji(now)
            save_state()
        return True

    if "【全群异闻·" in raw_text and "残图】" in raw_text:
        _apply_fragment_progresses(progresses)
        state["concubine_dream_due_at"] = now + CONCUBINE_DREAM_CD_SEC + CD_BUFFER_SEC
        state["concubine_last_error"] = ""
        reset_resource_shortage(CONCUBINE_DREAM_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        if _is_puzzle_ready():
            _schedule_chain_action(now)
            save_state()
            await send_audit_log(f"🌸 入梦寻图广播已达 4/4（{_format_completed_fragment_progresses()}），将先 .残图 确认。", scope="identity")
        else:
            _schedule_after_tianji(now)
            save_state()
        return True

    state["concubine_last_error"] = f"未识别的入梦寻图回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "dream_pending")
    save_state()
    return False


async def handle_concubine_fragment_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_fragment" and CMD_CONCUBINE_FRAGMENT not in orig_cmd:
        return False
    if _phase() != "fragment_pending":
        phase = _phase()
        console_log(f"🌸 忽略非等待期残图回复（phase={phase}）。")
        _record_concubine_ignored_reply("残图", reason="concubine_phase_mismatch", phase=phase, reply_to=reply_to)
        return True
    if not _is_current_reply(reply_to, "concubine_fragment_msg_id"):
        console_log("🌸 忽略迟到的残图回复。")
        _record_concubine_ignored_reply(
            "残图",
            reason="concubine_msg_id_mismatch",
            phase=_phase(),
            state_key="concubine_fragment_msg_id",
            reply_to=reply_to,
        )
        return True

    raw_text = text or ""
    if _handle_action_blocked_by_voyage(raw_text, now, error_key="concubine_last_error", label="残图确认"):
        save_state()
        return True

    if _is_no_partner_text(raw_text):
        if _is_partner_manual_repair_text(raw_text):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "残图确认失败：侍妾数据异常，等待人工修复")
            save_state()
            return True
        not_eligible = _is_partner_not_eligible_text(raw_text)
        reason = "残图确认失败：境界不足" if not_eligible else "残图确认失败：暂无侍妾"
        _mark_no_partner(now, reason, allow_reacquire=not not_eligible)
        save_state()
        return True

    progresses = _parse_fragment_progresses(raw_text)
    if progresses:
        _apply_fragment_progresses(progresses)
    if "【虚天残图卷】" in raw_text or "【苍坤残图卷】" in raw_text:
        _set_phase("idle")
        _clear_pending_msg_ids()
        if _confirmed_completed_fragment_kinds_from_reply(raw_text):
            state["concubine_last_error"] = ""
            _mark_fragment_confirmation(now)
            _set_phase("puzzle_ready")
            _schedule_chain_action(now)
            save_state()
            await send_audit_log(f"🌸 残图确认 4/4（{_format_completed_fragment_progresses()}），已排队自动 .拼图。", scope="identity")
        else:
            state["concubine_last_error"] = ""
            _schedule_after_tianji(now)
            save_state()
        return True

    state["concubine_last_error"] = f"未识别的残图回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "fragment_pending")
    save_state()
    return False


async def handle_concubine_puzzle_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_puzzle" and CMD_CONCUBINE_PUZZLE not in orig_cmd:
        return False
    if _phase() != "puzzle_pending":
        phase = _phase()
        console_log(f"🌸 忽略非等待期拼图回复（phase={phase}）。")
        _record_concubine_ignored_reply("拼图", reason="concubine_phase_mismatch", phase=phase, reply_to=reply_to)
        return True
    if not _is_current_reply(reply_to, "concubine_puzzle_msg_id"):
        console_log("🌸 忽略迟到的拼图回复。")
        _record_concubine_ignored_reply(
            "拼图",
            reason="concubine_msg_id_mismatch",
            phase=_phase(),
            state_key="concubine_puzzle_msg_id",
            reply_to=reply_to,
        )
        return True

    raw_text = text or ""
    if _handle_action_blocked_by_voyage(raw_text, now, error_key="concubine_last_error", label="拼图"):
        save_state()
        return True

    success_kind = _parse_puzzle_success_kind(raw_text)
    if success_kind:
        _clear_fragment_progress(success_kind)
        if float(state.get("concubine_dream_due_at", 0) or 0) <= now:
            state["concubine_dream_due_at"] = now + CONCUBINE_DREAM_CD_SEC + CD_BUFFER_SEC
        state["concubine_last_error"] = ""
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_after_tianji(now)
        save_state()
        await send_audit_log(f"🌸 {FRAGMENT_LABELS[success_kind]}残图拼合成功，已继续等待下一轮入梦。", scope="identity")
        return True

    if "残图尚未齐全" in raw_text:
        missing_match = RE_PUZZLE_MISSING.search(raw_text)
        if missing_match:
            missing_items = [item.strip() for item in re.split(r"[、,，]\s*", missing_match.group(1)) if item.strip()]
            total = max(4, len(missing_items))
            missing_kind = _select_fragment_kind_for_puzzle_result(raw_text)
            _set_fragment_progress(missing_kind, max(0, total - len(missing_items)), total)
        state["concubine_last_error"] = "拼图失败：残图尚未齐全"
        _set_phase("idle")
        _clear_pending_msg_ids()
        _backoff_after_pending_timeout(now, "puzzle_pending")
        save_state()
        return True

    if "【全群广播·" in raw_text and "残图拼合】" in raw_text:
        broadcast_kind = _select_fragment_kind_for_puzzle_result(raw_text)
        _clear_fragment_progress(broadcast_kind)
        if float(state.get("concubine_dream_due_at", 0) or 0) <= now:
            state["concubine_dream_due_at"] = now + CONCUBINE_DREAM_CD_SEC + CD_BUFFER_SEC
        state["concubine_last_error"] = ""
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_after_tianji(now)
        save_state()
        return True

    state["concubine_last_error"] = f"未识别的拼图回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "puzzle_pending")
    save_state()
    return False


async def handle_concubine_reacquire_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_reacquire" and not any(cmd in orig_cmd for cmd in {CMD_CONCUBINE_SECT_MARRY, CMD_CONCUBINE_ROMANCE}):
        return False
    if _phase() != "reacquire_pending":
        phase = _phase()
        console_log(f"🌸 忽略非等待期补领侍妾回复（phase={phase}）。")
        _record_concubine_ignored_reply("补领侍妾", reason="concubine_phase_mismatch", phase=phase, reply_to=reply_to)
        return True
    if not _is_current_reply(reply_to, "concubine_reacquire_msg_id"):
        console_log("🌸 忽略迟到的补领侍妾回复。")
        _record_concubine_ignored_reply(
            "补领侍妾",
            reason="concubine_msg_id_mismatch",
            phase=_phase(),
            state_key="concubine_reacquire_msg_id",
            reply_to=reply_to,
        )
        return True

    raw_text = text or ""
    if _handle_action_blocked_by_voyage(raw_text, now, error_key="concubine_last_error", label="补领侍妾"):
        save_state()
        return True

    if "开启了一段寻缘之旅" in raw_text:
        _set_phase("reacquire_pending")
        state["next_concubine_time"] = now + CONCUBINE_PHASE_TIMEOUT_SEC
        save_state()
        # 红尘寻缘常把这条中间态消息编辑成最终结果，不能提前消费。
        return False

    sect_match = RE_NEW_SECT_PARTNER.search(raw_text)
    romance_match = RE_NEW_ROMANCE_PARTNER.search(raw_text)
    if sect_match or romance_match:
        name = (sect_match or romance_match).group("name")
        _apply_partner_acquired(name, now, kind="道心侍妾" if sect_match else "红尘道侣")
        save_state()
        await send_audit_log(f"🌸 已补领侍妾【{name}】，稍后自动校准状态。", scope="identity")
        return True

    if "已有道侣" in raw_text or "已觅得红颜知己" in raw_text:
        state["concubine_last_error"] = "补领返回已有侍妾，将直接入梦校准"
        _set_availability("unknown")
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_chain_action(now)
        save_state()
        return True

    if "贡献不足" in raw_text or "灵石不足" in raw_text or "修为不足" in raw_text or "资源不足" in raw_text:
        _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, f"补领侍妾资源不足: {raw_text[:80]}")
        save_state()
        await send_audit_log(f"⚠️ 补领侍妾资源不足，已冻结 {int(CONCUBINE_REACQUIRE_RETRY_SEC / 3600)} 小时。", scope="identity")
        return True

    if _is_partner_not_eligible_text(raw_text):
        _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, f"补领侍妾条件不足: {raw_text[:80]}")
        save_state()
        await send_audit_log(f"⚠️ 补领侍妾条件不足，已冻结 {int(CONCUBINE_REACQUIRE_RETRY_SEC / 3600)} 小时。", scope="identity")
        return True

    if "踏遍万千红尘" in raw_text or "未能寻得有缘之人" in raw_text:
        _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "红尘寻缘未成功，停止连续尝试")
        save_state()
        return True

    if "神念消耗过剧" in raw_text:
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else CONCUBINE_REACQUIRE_RETRY_SEC
        _freeze_no_partner_until(now + wait_sec + CD_BUFFER_SEC, "补领侍妾冷却中")
        save_state()
        return True

    if "你乃星宫弟子" in raw_text or "宗门自有道心侍妾" in raw_text:
        if CMD_CONCUBINE_SECT_MARRY in orig_cmd:
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, f"补领指令校正异常: {raw_text[:80]}")
            save_state()
            return True
        _switch_reacquire_command(now, CMD_CONCUBINE_SECT_MARRY, "补领指令校正：改用宗门赐婚")
        save_state()
        await send_audit_log("🌸 检测到星宫身份，补领侍妾已改用 .宗门赐婚。", scope="identity")
        return True

    if "并非星宫弟子" in raw_text or "若为散修或其他宗门" in raw_text:
        if CMD_CONCUBINE_ROMANCE in orig_cmd:
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, f"补领指令校正异常: {raw_text[:80]}")
            save_state()
            return True
        _switch_reacquire_command(now, CMD_CONCUBINE_ROMANCE, "补领指令校正：改用红尘寻缘")
        save_state()
        await send_audit_log("🌸 检测到非星宫身份，补领侍妾已改用 .红尘寻缘。", scope="identity")
        return True

    state["concubine_last_error"] = f"未识别的补领回复: {raw_text[:60]}"
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "reacquire_pending")
    save_state()
    return False


async def handle_concubine_tianji_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_tianji_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_tianji" and CMD_CONCUBINE_TIANJI not in orig_cmd:
        return False
    phase = _phase()
    if phase != "tianji_pending":
        if phase in {"status_pending", "greet_pending", "dream_pending", "fragment_pending", "puzzle_pending", "reacquire_pending", "heart_pending", "heart_choice_pending", "heart_choice_reply_pending"}:
            console_log(f"🌸 忽略非等待期天机代卜回复（phase={phase}）。")
            _record_concubine_ignored_reply("天机代卜", reason="concubine_phase_mismatch", phase=phase, reply_to=reply_to)
            return True
        console_log(f"🌸 接受手动/迟到的天机代卜回复（phase={phase}）。")
    elif not _is_current_reply(reply_to, "concubine_tianji_msg_id") and not _is_strong_tianji_terminal_text(text):
        console_log("🌸 忽略迟到的天机代卜回复。")
        _record_concubine_ignored_reply(
            "天机代卜",
            reason="concubine_msg_id_mismatch",
            phase=phase,
            state_key="concubine_tianji_msg_id",
            reply_to=reply_to,
        )
        return True

    raw_text = text or ""
    voyage = _parse_voyage_text(raw_text, now)
    if voyage and voyage.get("status") == "sailing":
        _apply_voyage_blocked_action(voyage, now, error_key="concubine_tianji_last_error", label="天机代卜")
        save_state()
        return True

    if "【天机代卜链】" in raw_text:
        gua_match = RE_TIANJI_GUA.search(raw_text)
        state["concubine_tianji_chain"] = gua_match.group("name").strip() if gua_match else ""
        state["concubine_tianji_due_at"] = now + CONCUBINE_TIANJI_CD_SEC + CD_BUFFER_SEC
        state["concubine_tianji_chain_due_at"] = state["concubine_tianji_due_at"]
        state["concubine_tianji_last_error"] = ""
        reset_resource_shortage(CONCUBINE_TIANJI_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_after_tianji(now)
        save_state()
        return True

    if "天机链路尚未重铸" in raw_text:
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else CONCUBINE_TIANJI_CD_SEC
        state["concubine_tianji_due_at"] = now + wait_sec + CD_BUFFER_SEC
        state["concubine_tianji_last_error"] = ""
        reset_resource_shortage(CONCUBINE_TIANJI_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_after_tianji(now)
        save_state()
        return True

    if _is_no_partner_text(raw_text):
        if _is_partner_manual_repair_text(raw_text):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "天机代卜失败：侍妾数据异常，等待人工修复")
            save_state()
            return True
        not_eligible = _is_partner_not_eligible_text(raw_text)
        reason = "天机代卜失败：境界不足" if not_eligible else "天机代卜失败：暂无侍妾"
        _mark_no_partner(now, reason, allow_reacquire=bool(state.get("concubine_enabled")) and not not_eligible)
        save_state()
        return True

    if "情缘未至" in raw_text or "情缘未深" in raw_text or "无法为你卜算天机" in raw_text:
        _mark_tianji_affinity_shortage(
            now,
            "情缘不足，暂缓天机代卜",
            infer_low_affinity=True,
        )
        reset_resource_shortage(CONCUBINE_TIANJI_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        save_state()
        return True

    if _is_tianji_resource_shortage_text(raw_text):
        await _apply_concubine_resource_backoff(
            now,
            CONCUBINE_TIANJI_RESOURCE_KEY,
            "concubine_tianji_due_at",
            "concubine_tianji_last_error",
            "天机代卜",
            raw_text,
        )
        save_state()
        return True

    state["concubine_tianji_last_error"] = f"未识别的天机代卜回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "tianji_pending")
    save_state()
    return False


def _is_heart_resource_shortage_text(text):
    raw_text = str(text or "")
    return "修为不足" in raw_text and "开启共历心劫" in raw_text


def _is_heart_cd_text(text):
    return "心劫余波未散" in str(text or "")


def _heart_next_choice_delay():
    return random.uniform(CONCUBINE_HEART_CHOICE_DELAY_MIN_SEC, CONCUBINE_HEART_CHOICE_DELAY_MAX_SEC)


async def handle_concubine_heart_reply(text, now, reply_to, matched_family=None, current_msg_id=0):
    if not state.get("concubine_heart_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if (
        matched_family != "concubine_heart"
        and CMD_CONCUBINE_HEART not in orig_cmd
        and CMD_CONCUBINE_HEART_STEADY not in orig_cmd
        and not _is_current_heart_prompt_message(reply_to=reply_to, current_msg_id=current_msg_id)
    ):
        return False

    raw_text = text or ""
    voyage = _parse_voyage_text(raw_text, now)
    if voyage and voyage.get("status") == "sailing":
        _close_heart_action_guard(now, "heart_blocked_by_voyage")
        _apply_voyage_blocked_action(voyage, now, error_key="concubine_heart_last_error", label="共历心劫")
        save_state()
        return True

    if _is_heart_cd_text(raw_text):
        _close_heart_action_guard(now, "heart_cd")
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else CONCUBINE_HEART_CD_SEC
        state["concubine_heart_due_at"] = now + wait_sec + CD_BUFFER_SEC
        state["concubine_heart_last_error"] = ""
        reset_resource_shortage(CONCUBINE_HEART_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_at_due_or_chain(now, state["concubine_heart_due_at"])
        save_state()
        return True

    if _is_heart_resource_shortage_text(raw_text):
        _close_heart_action_guard(now, "heart_resource_shortage")
        await _apply_concubine_resource_backoff(
            now,
            CONCUBINE_HEART_RESOURCE_KEY,
            "concubine_heart_due_at",
            "concubine_heart_last_error",
            "共历心劫",
            raw_text,
        )
        save_state()
        return True

    if "请回复一条包含侍妾/道侣内容的消息" in raw_text:
        _close_heart_action_guard(now, "heart_missing_panel_reply")
        state["concubine_heart_last_error"] = "共历心劫需要回复侍妾面板，已改为状态校准"
        state["concubine_last_panel_msg_id"] = 0
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_chain_action(now)
        save_state()
        return True

    if "你已有一场心劫抉择正在进行" in raw_text:
        _close_heart_action_guard(now, "heart_already_in_progress")
        state["concubine_heart_last_error"] = "已有心劫抉择进行中，暂停自动补发"
        _set_phase("idle")
        _clear_pending_msg_ids()
        if has_wait_time(raw_text):
            state["concubine_heart_due_at"] = now + parse_wait_time(raw_text) + CD_BUFFER_SEC
        else:
            state["concubine_heart_due_at"] = now + random.uniform(30 * 60, 60 * 60)
        _schedule_at_due_or_chain(now, state["concubine_heart_due_at"])
        save_state()
        await send_audit_log("🌸 共历心劫已有抉择进行中，已暂停补发并稍后校准。", scope="identity")
        return True

    if "【坠魔心劫·结算】" in raw_text:
        _close_heart_action_guard(now, "heart_settlement")
        affinity_match = RE_HEART_AFFINITY_SETTLEMENT.search(raw_text)
        if affinity_match:
            _apply_affinity_amount(affinity_match.group("amount"), now)
        state["concubine_heart_due_at"] = now + CONCUBINE_HEART_CD_SEC + random.uniform(10 * 60, 40 * 60)
        state["concubine_heart_last_error"] = ""
        state["concubine_heart_prompt_msg_id"] = 0
        state["concubine_heart_round"] = 0
        reset_resource_shortage(CONCUBINE_HEART_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_at_due_or_chain(now, state["concubine_heart_due_at"])
        save_state()
        await send_audit_log("🌸 共历心劫已结算，按 12h+缓冲等待下一轮。", scope="identity")
        return True

    if "【坠魔心劫·第一轮】" in raw_text:
        prompt_msg_id = int(current_msg_id or getattr(reply_to, "id", 0) or 0)
        _activate_heart_choice_round(now, prompt_msg_id, 1)
        save_state()
        return True

    if "【坠魔心劫·第1轮已定】" in raw_text and "【坠魔心劫·第2轮】" in raw_text:
        prompt_msg_id = int(current_msg_id or state.get("concubine_heart_prompt_msg_id", 0) or 0)
        _activate_heart_choice_round(now, prompt_msg_id, 2)
        save_state()
        return True

    if "【坠魔心劫·第2轮已定】" in raw_text and "【坠魔心劫·第3轮】" in raw_text:
        prompt_msg_id = int(current_msg_id or state.get("concubine_heart_prompt_msg_id", 0) or 0)
        _activate_heart_choice_round(now, prompt_msg_id, 3)
        save_state()
        return True

    _close_heart_action_guard(now, "heart_unrecognized")
    state["concubine_heart_last_error"] = f"未识别的共历心劫回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "heart_pending")
    save_state()
    return False


async def handle_concubine_voyage_reply(text, now, reply_to, matched_family=None):
    if not (
        state.get("concubine_enabled", False)
        or state.get("concubine_tianji_enabled", False)
        or state.get("concubine_heart_enabled", False)
        or state.get("concubine_voyage_enabled", False)
    ):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    voyage_commands = {CMD_CONCUBINE_VOYAGE, CMD_CONCUBINE_VOYAGE_RETURN, CMD_CONCUBINE_VOYAGE_STATUS}
    if matched_family != "concubine_voyage" and not any(cmd in orig_cmd for cmd in voyage_commands):
        return False

    phase = _phase()
    raw_text = text or ""
    parsed = _parse_voyage_text(raw_text, now)
    should_audit_voyage_result = False
    if phase in CONCUBINE_VOYAGE_PENDING_PHASES:
        if not _is_current_reply(reply_to, "concubine_voyage_msg_id"):
            console_log("🌸 忽略迟到的侍妾远航回复。")
            _record_concubine_ignored_reply(
                "侍妾远航",
                reason="concubine_msg_id_mismatch",
                phase=phase,
                state_key="concubine_voyage_msg_id",
                reply_to=reply_to,
            )
            return True
        if parsed and parsed.get("status") == "no_task" and phase == "voyage_return_pending":
            parsed["clear_idle"] = True
        should_audit_voyage_result = bool(parsed and phase == "voyage_return_pending" and parsed.get("status") == "idle" and parsed.get("result"))
    elif phase in {"status_pending", "greet_pending", "gift_status_pending", "gift_bag_pending", "gift_pending", "dream_pending", "fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending", "heart_pending", "heart_choice_pending", "heart_choice_reply_pending"}:
        if not parsed:
            console_log(f"🌸 忽略非等待期侍妾远航回复（phase={phase}）。")
            _record_concubine_ignored_reply("侍妾远航", reason="concubine_phase_mismatch", phase=phase, reply_to=reply_to)
            return True

    if parsed:
        _apply_voyage_snapshot(parsed, now)
        if should_audit_voyage_result:
            await _send_voyage_result_audit(parsed)
        save_state()
        return True

    state["concubine_voyage_last_error"] = f"未识别的侍妾远航回复: {raw_text[:60]}"
    if phase in CONCUBINE_VOYAGE_PENDING_PHASES:
        _set_phase("idle")
        state["concubine_voyage_msg_id"] = 0
        _schedule_after(now, CONCUBINE_STATUS_RECHECK_MIN_SEC, CONCUBINE_STATUS_RECHECK_MAX_SEC)
    save_state()
    return False


async def handle_concubine_greet_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_tianji_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_greet" and CMD_CONCUBINE_DAILY_GREET not in orig_cmd:
        return False
    if _phase() == "greet_pending" and not _is_current_reply(reply_to, "concubine_greet_msg_id"):
        console_log("🌸 忽略迟到的每日问安回复。")
        _record_concubine_ignored_reply(
            "每日问安",
            reason="concubine_msg_id_mismatch",
            phase=_phase(),
            state_key="concubine_greet_msg_id",
            reply_to=reply_to,
        )
        return True

    raw_text = text or ""
    if _handle_action_blocked_by_voyage(raw_text, now, error_key="concubine_greet_last_error", label="每日问安"):
        save_state()
        return True

    today = _local_day_key(now)
    if _is_phaseful_summary_text(raw_text):
        _retry_or_stop_daily_greet(now, "每日问安触发闭关/元婴结算")
        save_state()
        return True

    if "今日已经问安过了" in raw_text:
        state["concubine_last_greet_day"] = today
        state["concubine_greet_retry_count"] = 0
        state["concubine_greet_last_error"] = "今日已经问安过"
        _set_phase("idle")
        _clear_non_heart_pending_msg_ids()
        if _is_gift_recovery_due(now):
            _schedule_chain_action(now)
        else:
            _schedule_next_daily_greet_check(now)
        save_state()
        return True

    gain_match = RE_AFFINITY_GAIN.search(raw_text)
    if gain_match:
        state["concubine_last_greet_day"] = today
        state["concubine_greet_retry_count"] = 0
        state["concubine_greet_last_error"] = ""
        if not _apply_affinity_gain(gain_match.group("name").strip(), gain_match.group("amount"), now):
            state["concubine_greet_last_error"] = f"问安情缘回复未匹配当前侍妾: {raw_text[:60]}"
            _set_phase("idle")
            _clear_non_heart_pending_msg_ids()
            _backoff_after_pending_timeout(now, "greet_pending")
            save_state()
            return False
        _set_phase("idle")
        _clear_non_heart_pending_msg_ids()
        if int(state.get("concubine_affinity", 0) or 0) < CONCUBINE_TIANJI_MIN_AFFINITY:
            _schedule_affinity_recovery(now)
        save_state()
        return True

    if _is_no_partner_text(raw_text):
        if _is_partner_manual_repair_text(raw_text):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "每日问安失败：侍妾数据异常，等待人工修复")
        else:
            not_eligible = _is_partner_not_eligible_text(raw_text)
            reason = "每日问安失败：境界不足" if not_eligible else "每日问安失败：暂无侍妾"
            _mark_no_partner(now, reason, allow_reacquire=bool(state.get("concubine_enabled")) and not not_eligible)
        save_state()
        return True

    state["concubine_greet_last_error"] = f"未识别的每日问安回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_non_heart_pending_msg_ids()
    _backoff_after_pending_timeout(now, "greet_pending")
    save_state()
    return False


async def handle_concubine_storage_bag_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_tianji_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "storage_bag" and CMD_STORAGE_BAG not in orig_cmd:
        return False
    phase = _phase()
    can_continue = _can_continue_gift_recovery(now)
    if phase == "gift_bag_pending" and not _is_current_reply(reply_to, "concubine_gift_bag_msg_id"):
        console_log("🌸 忽略迟到的侍妾赠予储物袋回复。")
        _record_concubine_ignored_reply(
            "侍妾赠予储物袋",
            reason="concubine_msg_id_mismatch",
            phase=phase,
            state_key="concubine_gift_bag_msg_id",
            reply_to=reply_to,
        )
        return True
    if phase != "gift_bag_pending":
        if not can_continue:
            return False
        console_log(f"🌸 接受侍妾赠予储物袋回复（phase={phase}，按当日赠予链路续接）。")

    parsed = parse_storage_bag_reply(text)
    if not parsed:
        _finish_gift_recovery_today(now, f"未识别的储物袋回复: {(text or '')[:60]}")
        save_state()
        return True

    resolved_identity_id = resolve_storage_bag_identity_id(parsed.get("owner"))
    current_identity_id = get_current_identity_id()
    if resolved_identity_id > 0 and current_identity_id > 0 and int(resolved_identity_id) != int(current_identity_id):
        _finish_gift_recovery_today(now, f"储物袋归属不匹配，今日不赠予: {parsed.get('owner') or '未知'}")
        save_state()
        return True

    if not _can_continue_gift_recovery(now):
        if int(state.get("concubine_affinity", 0) or 0) >= CONCUBINE_TIANJI_MIN_AFFINITY:
            state["concubine_gift_last_error"] = ""
            _set_phase("idle")
            _clear_non_heart_pending_msg_ids()
            _schedule_after_tianji(now)
        else:
            _finish_gift_recovery_today(now, "储物袋返回时不满足赠予条件，今日不再赠予")
        save_state()
        return True

    amount = CONCUBINE_TIANJI_MIN_AFFINITY - max(0, int(state.get("concubine_affinity", 0) or 0))
    stones = _parse_count((parsed.get("items") or {}).get("灵石", 0))
    if stones < amount:
        _finish_gift_recovery_today(now, f"灵石不足（{stones}/{amount}），今日不赠予")
        save_state()
        return True

    await _send_gift_command(now, amount)
    return True


async def handle_concubine_gift_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_tianji_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_gift" and CMD_CONCUBINE_GIFT_STONE not in orig_cmd:
        return False
    if _phase() == "gift_pending" and not _is_current_reply(reply_to, "concubine_gift_msg_id"):
        console_log("🌸 忽略迟到的侍妾赠予回复。")
        _record_concubine_ignored_reply(
            "侍妾赠予",
            reason="concubine_msg_id_mismatch",
            phase=_phase(),
            state_key="concubine_gift_msg_id",
            reply_to=reply_to,
        )
        return True

    raw_text = text or ""
    if _handle_action_blocked_by_voyage(raw_text, now, error_key="concubine_gift_last_error", label="赠予侍妾"):
        save_state()
        return True

    gift_success = _parse_gift_success(raw_text)
    if gift_success:
        today = _local_day_key(now)
        state["concubine_last_gift_day"] = today
        state["concubine_gift_attempt_day"] = today
        state["concubine_gift_last_error"] = ""
        state["concubine_gift_amount"] = 0
        if not _current_partner_matches(gift_success["name"]):
            state["concubine_gift_last_error"] = f"赠予回复未匹配当前侍妾: {raw_text[:60]}"
            _set_phase("idle")
            _clear_non_heart_pending_msg_ids()
            _schedule_affinity_recovery(now)
            save_state()
            return False
        if not _apply_affinity_gain(gift_success["name"], gift_success["amount"], now):
            state["concubine_gift_last_error"] = f"赠予情缘回复未生效: {raw_text[:60]}"
            _set_phase("idle")
            _clear_non_heart_pending_msg_ids()
            _schedule_affinity_recovery(now)
            save_state()
            return False
        apply_storage_bag_item_deltas(get_current_identity_id(), {"灵石": -gift_success["stone"]})
        _set_phase("idle")
        _clear_non_heart_pending_msg_ids()
        if int(state.get("concubine_affinity", 0) or 0) >= CONCUBINE_TIANJI_MIN_AFFINITY:
            _normalize_tianji_affinity_error(now)
        save_state()
        return True

    if "灵石不足" in raw_text or "灵石不够" in raw_text or "数量不足" in raw_text:
        _finish_gift_recovery_today(now, f"赠予失败：灵石不足｜{raw_text[:60]}")
        save_state()
        return True
    if _is_no_partner_text(raw_text):
        _finish_gift_recovery_today(now, f"赠予失败：暂无侍妾｜{raw_text[:60]}")
        save_state()
        return True
    if "今日" in raw_text and ("赠予" in raw_text or "送" in raw_text):
        _finish_gift_recovery_today(now, f"赠予受限：{raw_text[:60]}")
        save_state()
        return True

    _finish_gift_recovery_today(now, f"未识别的赠予回复: {raw_text[:60]}")
    save_state()
    return False


async def handle_concubine_affinity_event(text, now, event=None, matched_family=None, require_identity_hint=False):
    if matched_family in {"concubine_greet", "concubine_gift"}:
        return False
    if (
        not state.get("concubine_enabled", False)
        and not state.get("concubine_tianji_enabled", False)
        and not state.get("concubine_heart_enabled", False)
        and not state.get("concubine_voyage_enabled", False)
    ):
        return False
    raw_text = text or ""
    if _parse_gift_success(raw_text):
        return False
    if not is_concubine_affinity_event_candidate(raw_text):
        return False
    if require_identity_hint and not _text_matches_current_identity(raw_text):
        return False

    if _is_selfless_affinity_depletion_text(raw_text):
        partner_name = _parse_selfless_partner_name(raw_text)
        if partner_name and not _current_partner_matches(partner_name):
            return False
        if state.get("concubine_kind") and state.get("concubine_kind") != "道心侍妾":
            return False
        if partner_name:
            state["concubine_name"] = partner_name
        state["concubine_kind"] = "道心侍妾"
        _set_availability("available")
        _mark_tianji_affinity_shortage(
            now,
            "无我之境耗尽情缘，等待问安恢复",
            force_affinity_zero=True,
        )
        state["concubine_last_error"] = ""
        save_state()
        await send_audit_log("🌸 无我之境已耗尽侍妾情缘，暂停天机代卜，等待问安等情缘恢复。", scope="identity")
        return True

    gain_match = RE_AFFINITY_GAIN.search(raw_text)
    if not gain_match:
        return False
    partner_name = gain_match.group("name").strip()
    if not _apply_affinity_gain(partner_name, gain_match.group("amount"), now):
        return False
    save_state()
    return True


async def handle_concubine_loss_broadcast(text, now, event):
    if (
        not state.get("concubine_enabled", False)
        and not state.get("concubine_tianji_enabled", False)
        and not state.get("concubine_heart_enabled", False)
        and not state.get("concubine_voyage_enabled", False)
    ):
        return False
    raw_text = text or ""
    if "南陇侯" not in raw_text or "侍妾" not in raw_text:
        return False
    if "掳走" not in raw_text and "选择将侍妾" not in raw_text:
        return False
    if not _text_matches_current_identity(raw_text):
        return False

    matched = RE_LOST_PARTNER_NAME.search(raw_text)
    partner_name = matched.group("name") if matched else (state.get("concubine_name") or "")
    _mark_no_partner(now, f"南陇侯导致侍妾失去: {partner_name or '未知'}", allow_reacquire=bool(state.get("concubine_enabled")))
    save_state()
    if state.get("concubine_auto_reacquire"):
        await send_audit_log("🌸 侍妾已被南陇侯带走，已进入自动补领等待。", scope="identity")
    else:
        await send_audit_log("🌸 侍妾已被南陇侯带走，入梦/拼图已熔断；自动补领未开启。", scope="identity")
    return True


async def run_concubine_scheduler(now):
    if _CONCUBINE_SCHEDULER_LOCK.locked():
        return
    async with _CONCUBINE_SCHEDULER_LOCK:
        await _run_concubine_scheduler(now)


async def _run_concubine_scheduler(now):
    if (
        not state.get("concubine_enabled", False)
        and not state.get("concubine_tianji_enabled", False)
        and not state.get("concubine_heart_enabled", False)
        and not state.get("concubine_voyage_enabled", False)
    ):
        return

    if _has_active_nanlong_pending(now):
        state["concubine_last_error"] = "南陇侯抉择中，侍妾模块暂缓"
        _schedule_after(now, 60, 600)
        save_state()
        return

    if _normalize_tianji_affinity_error(now):
        save_state()

    if _clear_stale_phaseful_summary_wait_errors(now):
        save_state()

    phase = _phase()
    if phase == "heart_choice_pending":
        next_time = float(state.get("next_concubine_time", 0) or 0)
        if next_time > now:
            return
        if int(state.get("concubine_heart_round", 0) or 0) in {1, 2, 3}:
            await _send_heart_choice(now)
            return
        state["concubine_heart_last_error"] = "心劫抉择轮次异常，暂停自动处理"
        _close_heart_chain_without_settlement(now, "heart_choice_invalid_round")
        save_state()
        return

    if phase == "heart_choice_reply_pending":
        pending_until = float(state.get("next_concubine_time", 0) or 0)
        if pending_until > now:
            return
        if await _recover_concubine_pending_from_message_log(now, phase):
            return
        if await _retry_heart_choice_once(now):
            return
        retry_at = _close_heart_chain_without_settlement(now, "heart_choice_reply_timeout")
        save_state()
        await send_audit_log(
            f"⚠️ 共历心劫抉择无回合推进，已停止旧 prompt；按长冷却等待 {fmt_time_after(max(0, retry_at - now))}。",
            scope="identity",
        )
        return

    if phase in CONCUBINE_VOYAGE_PENDING_PHASES:
        pending_until = float(state.get("next_concubine_time", 0) or 0)
        if pending_until > now:
            return
        await _handle_voyage_pending_timeout(now, phase)
        return

    if phase in {"status_pending", "greet_pending", "gift_status_pending", "gift_bag_pending", "gift_pending", "dream_pending", "fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending", "heart_pending"}:
        pending_until = float(state.get("next_concubine_time", 0) or 0)
        if pending_until > now:
            return
        if await _recover_concubine_pending_from_message_log(now, phase):
            return
        await _audit_pending_timeout_candidates(now, phase)
        state["concubine_last_error"] = f"{phase} 等待回复超时，已转状态校准" if phase != "status_pending" else "侍妾状态查询等待回复超时"
        if _has_available_partner():
            _set_phase("idle")
        elif state.get("concubine_availability") == "no_partner":
            _set_phase("no_partner")
        else:
            _set_phase("idle")
        _clear_pending_msg_ids()
        if phase in CONCUBINE_HEART_ACTIVE_PHASES:
            retry_at = _close_heart_chain_without_settlement(now, f"{phase}_timeout")
        else:
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
        if _is_voyage_return_retry_exhausted(now):
            await _send_voyage_status_command(now)
            return
        await _send_voyage_return_command(now)
        return

    if _is_voyage_sailing(now):
        next_time = float(state.get("next_concubine_time", 0) or 0)
        if next_time <= now:
            _schedule_voyage_wait(now)
            save_state()
        return

    next_time = float(state.get("next_concubine_time", 0) or 0)
    if next_time > 0 and now < next_time and not _has_affinity_recovery_due(now) and not _is_voyage_eligible(now):
        return

    if phase == "no_partner" or state.get("concubine_availability") == "no_partner":
        if state.get("concubine_enabled") and state.get("concubine_auto_reacquire") and now >= float(state.get("concubine_reacquire_blocked_until", 0) or 0):
            await _send_reacquire_command(now)
            return
        _schedule_no_partner_check(now)
        save_state()
        return

    if phase == "puzzle_ready":
        if state.get("concubine_enabled") and _is_puzzle_ready():
            await _send_puzzle_command(now)
            return
        _set_phase("idle")
        _schedule_status_recheck(now)
        save_state()
        return

    if not _has_available_partner():
        if state.get("concubine_tianji_enabled") or state.get("concubine_heart_enabled") or state.get("concubine_voyage_enabled"):
            await _send_status_command(now)
        else:
            await _send_dream_command(now)
        return

    if state.get("concubine_enabled") and _is_puzzle_ready():
        if _is_current_fragment_confirmed():
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

    if (
        state.get("concubine_tianji_enabled")
        and not _is_tianji_affinity_blocked()
        and float(state.get("concubine_tianji_due_at", 0) or 0) <= now
        and _guard_tianji_send_with_message_log(now)
    ):
        save_state()
        return

    if _needs_active_status_calibration(now):
        action, error_key = _active_status_calibration_context(now)
        if _defer_active_for_phaseful_summary(now, action, error_key=error_key):
            save_state()
            return
        state["concubine_last_error"] = "主动动作前状态校准，避免旧冷却快照误发"
        await _send_status_command(now)
        return

    if state.get("concubine_enabled"):
        dream_due_at = float(state.get("concubine_dream_due_at", 0) or 0)
        if dream_due_at <= now:
            await _send_dream_command(now)
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

    if state.get("concubine_heart_enabled"):
        heart_due_at = float(state.get("concubine_heart_due_at", 0) or 0)
        if heart_due_at <= now:
            await _send_heart_command(now)
            return

    if _is_voyage_eligible(now):
        await _send_voyage_command(now)
        return

    due_times = []
    if state.get("concubine_enabled"):
        due_times.append(float(state.get("concubine_dream_due_at", 0) or 0))
    if state.get("concubine_tianji_enabled") and not _is_tianji_affinity_blocked():
        due_times.append(float(state.get("concubine_tianji_due_at", 0) or 0))
    if state.get("concubine_heart_enabled"):
        due_times.append(float(state.get("concubine_heart_due_at", 0) or 0))
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
    "is_concubine_affinity_event_candidate",
    "restore_concubine_runtime",
    "run_concubine_scheduler",
]
