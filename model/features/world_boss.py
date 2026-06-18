import asyncio
import hashlib
import re
import time
from datetime import datetime

from ..config import (
    CMD_QINGYUANZI_ATTACK,
    CMD_QINGYUANZI_GUARD,
    CMD_QINGYUANZI_SUPPRESS,
    CMD_WORLD_BOSS_STATUS,
    TZ_LOCAL,
)
from ..persistence import mark_dirty, save_state
from ..runtime import clear_pending_tasks_by_commands, console_log, send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    get_world_boss_run_state,
    set_world_boss_run_state,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, get_day_key, parse_wait_time


WORLD_BOSS_MODULE_NAME = "真仙试锋"
WORLD_BOSS_ACTIONS = {"镇魂", "护阵", "强攻", "破幡"}
WORLD_BOSS_MAINTENANCE_ACTIONS = {"镇魂", "护阵"}
WORLD_BOSS_ACTION_COMMANDS = {
    "镇魂": CMD_QINGYUANZI_SUPPRESS,
    "护阵": CMD_QINGYUANZI_GUARD,
    "强攻": CMD_QINGYUANZI_ATTACK,
}
WORLD_BOSS_PENDING_TIMEOUT_SEC = 90
WORLD_BOSS_REPLY_TIMEOUT_SEC = 90
WORLD_BOSS_ACTION_GAP_SEC = 1.0
WORLD_BOSS_ROUND_GAP_SEC = 70.0
WORLD_BOSS_MAX_ACTIONS_PER_TICK = 64
WORLD_BOSS_STATUS_AFTER_QUERY_GAP_SEC = 0
WORLD_BOSS_STATUS_STALE_SEC = 120
WORLD_BOSS_STATUS_QUERY_GAP_SEC = 3 * 60
WORLD_BOSS_EVENT_TTL_SEC = 35 * 60
WORLD_BOSS_STRONG_ATTACK_LIMIT = 2
WORLD_BOSS_DEFAULT_ACTION_LIMIT = 5
WORLD_BOSS_PROGRESS_LOG_GAP_SEC = 5 * 60
WORLD_BOSS_FALLBACK_START_MINUTE = 13 * 60 + 25
WORLD_BOSS_FALLBACK_END_MINUTE = 14 * 60 + 10
WORLD_BOSS_PENDING_MAX_RETRY = 2
WORLD_BOSS_PHASE_TWO_CRITICAL_ZHEN = 35
WORLD_BOSS_PHASE_TWO_GUARD_MOYA_LIMIT = 95
WORLD_BOSS_STRONG_ATTACK_IDS = {8659059191, 301299112}
WORLD_BOSS_STRONG_ATTACK_NAMES = {"walterwa2000", "wa2000", "jfdffdddd", "吧唧"}
WORLD_BOSS_PENDING_COMMANDS = set(WORLD_BOSS_ACTION_COMMANDS.values()) | {f"{CMD_WORLD_BOSS_STATUS} 查看战况"}
WORLD_BOSS_EVENT_PRIORITY = "event_burst"

RE_WORLD_BOSS_OPEN = re.compile(r"【世界通告｜真仙试锋开启】")
RE_WORLD_BOSS_NOTICE = re.compile(r"【世界通告｜真仙试锋(?P<title>[^】]+)】")
RE_WORLD_BOSS_STATUS = re.compile(r"【真仙试锋\s*·\s*青元子】")
RE_WORLD_BOSS_ACTION = re.compile(r"【讨伐青元子】")
RE_PHASE = re.compile(r"阶段[:：]\s*([^\n\r]+)")
RE_HP_PERCENT = re.compile(r"血量[:：][^\n\r]*?(\d{1,3})%")
RE_STATUS_MECHANICS = re.compile(
    r"幡魂[:：]\s*(\d+)\s*层\s*｜\s*破幡进度[:：]\s*(\d+).*?"
    r"魔压[:：]\s*(\d+)\s*/\s*100\s*｜\s*阵势[:：]\s*(\d+)\s*/\s*120",
    re.S,
)
RE_ACTION_MECHANICS = re.compile(
    r"当前[:：]\s*\[[^\]]+\]\s*(\d{1,3})%\s*｜\s*幡魂\s*(\d+)\s*｜\s*魔压\s*(\d+)\s*/\s*100\s*｜\s*阵势\s*(\d+)\s*/\s*120"
)
RE_REMAINING = re.compile(r"剩余时间[:：]\s*([^\n\r]+)")
RE_OWN_ACTIONS = re.compile(r"你的出手[:：]\s*(\d+)\s*/\s*(\d+)")
RE_EXHAUSTED = re.compile(r"本期真仙试锋出手已尽.*?(\d+)\s*/\s*(\d+)")
RE_DAMAGE = re.compile(r"造成\s*([^\s。]+)\s*伤害")
RE_PARTICIPANTS = re.compile(r"参战[:：]\s*(\d+)\s*人")

_WORLD_BOSS_SCHEDULER_LOCK = asyncio.Lock()
_WORLD_BOSS_ROUND_TASK = None


def _empty_summary():
    return {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0}


def _blank_run_state(now=None):
    now = float(now if now is not None else time.time())
    return {
        "active": False,
        "event_key": "",
        "opened_at": 0,
        "closed_at": 0,
        "phase": "",
        "hp_percent": -1,
        "fanhun": -1,
        "break_progress": -1,
        "moya": -1,
        "zhen": -1,
        "remaining_sec": 0,
        "last_status_at": 0,
        "last_status_msg_id": 0,
        "last_action_at": 0,
        "next_action_at": 0,
        "round_started_at": 0,
        "round_completed_at": 0,
        "next_status_query_at": 0,
        "summary": _empty_summary(),
        "last_summary_log_at": 0,
        "last_summary_log_total": 0,
        "last_phase_log": "",
        "last_open_log_key": "",
        "last_conclusion_key": "",
        "last_conclusion_at": 0,
        "last_result": "",
        "participants": 0,
        "fallback_status_day": "",
    }


def _coerce_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_summary(value):
    summary = _empty_summary()
    if isinstance(value, dict):
        for action in summary:
            summary[action] = max(0, _coerce_int(value.get(action), 0))
    return summary


def _normalize_run_state(raw=None, now=None):
    now = float(now if now is not None else time.time())
    source = raw if isinstance(raw, dict) else {}
    record = _blank_run_state(now)
    record.update(source)
    record["active"] = bool(record.get("active"))
    for key in (
        "opened_at",
        "closed_at",
        "remaining_sec",
        "last_status_at",
        "last_action_at",
        "next_action_at",
        "round_started_at",
        "round_completed_at",
        "next_status_query_at",
        "last_summary_log_at",
        "last_conclusion_at",
    ):
        record[key] = max(0.0, _coerce_float(record.get(key), 0))
    for key in ("hp_percent", "fanhun", "break_progress", "moya", "zhen", "last_status_msg_id", "last_summary_log_total", "participants"):
        record[key] = _coerce_int(record.get(key), -1 if key in {"hp_percent", "fanhun", "break_progress", "moya", "zhen"} else 0)
    record["summary"] = _normalize_summary(record.get("summary"))
    for key in ("event_key", "phase", "last_phase_log", "last_open_log_key", "last_conclusion_key", "last_result", "fallback_status_day"):
        record[key] = str(record.get(key) or "").strip()
    if record["active"] and record["opened_at"] > 0 and now - record["opened_at"] > WORLD_BOSS_EVENT_TTL_SEC:
        record["active"] = False
        record["closed_at"] = record["closed_at"] or now
        record["last_result"] = record["last_result"] or "超时结束"
    return record


def _get_run_state(now=None):
    return _normalize_run_state(get_world_boss_run_state(), now)


def _set_run_state(record, *, persist=True):
    set_world_boss_run_state(_normalize_run_state(record))
    if persist:
        if save_state() is False:
            mark_dirty()
    else:
        mark_dirty()


def _short_text(text, limit=90):
    raw = " / ".join(part.strip() for part in str(text or "").splitlines() if part.strip())
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)].rstrip() + "..."


def _event_hash(text):
    normalized = "\n".join(part.strip() for part in str(text or "").splitlines() if part.strip())
    return hashlib.sha1(normalized.encode("utf-8", "ignore")).hexdigest()[:16]


def _remaining_sec(text):
    match = RE_REMAINING.search(str(text or ""))
    if not match:
        return 0
    fragment = match.group(1)
    total = 0
    matched = False
    for pattern, multiplier in (
        (re.compile(r"(\d+)\s*小时"), 3600),
        (re.compile(r"(\d+)\s*分(?:钟)?"), 60),
        (re.compile(r"(\d+)\s*秒"), 1),
    ):
        duration_match = pattern.search(fragment)
        if duration_match:
            matched = True
            total += _coerce_int(duration_match.group(1), 0) * multiplier
    if matched:
        return max(0, total)
    return max(0, int(parse_wait_time(fragment) or 0))


def _parse_status_text(raw_text):
    if not RE_WORLD_BOSS_STATUS.search(raw_text):
        return None
    parsed = {
        "type": "status",
        "phase": "",
        "hp_percent": -1,
        "fanhun": -1,
        "break_progress": -1,
        "moya": -1,
        "zhen": -1,
        "remaining_sec": _remaining_sec(raw_text),
        "own_actions": 0,
        "own_action_limit": 0,
    }
    match = RE_PHASE.search(raw_text)
    if match:
        parsed["phase"] = match.group(1).strip()
    match = RE_HP_PERCENT.search(raw_text)
    if match:
        parsed["hp_percent"] = _coerce_int(match.group(1), -1)
    match = RE_STATUS_MECHANICS.search(raw_text)
    if match:
        parsed["fanhun"] = _coerce_int(match.group(1), -1)
        parsed["break_progress"] = _coerce_int(match.group(2), -1)
        parsed["moya"] = _coerce_int(match.group(3), -1)
        parsed["zhen"] = _coerce_int(match.group(4), -1)
    match = RE_OWN_ACTIONS.search(raw_text)
    if match:
        parsed["own_actions"] = _coerce_int(match.group(1), 0)
        parsed["own_action_limit"] = _coerce_int(match.group(2), WORLD_BOSS_DEFAULT_ACTION_LIMIT)
    return parsed


def _action_from_text(raw_text):
    if "镇住阴魂回潮" in raw_text:
        return "镇魂"
    if "护住天机阵势" in raw_text:
        return "护阵"
    if "祭出攻势" in raw_text:
        return "强攻"
    if "推进破幡进度" in raw_text:
        return "破幡"
    return ""


def _parse_action_text(raw_text):
    if not RE_WORLD_BOSS_ACTION.search(raw_text):
        return None
    action = _action_from_text(raw_text)
    parsed = {
        "type": "action",
        "action": action,
        "summary": "",
        "hp_percent": -1,
        "fanhun": -1,
        "break_progress": -1,
        "moya": -1,
        "zhen": -1,
        "damage": "",
    }
    if action == "强攻":
        match = RE_DAMAGE.search(raw_text)
        parsed["damage"] = match.group(1).strip() if match else ""
        parsed["summary"] = f"强攻 {parsed['damage']}".strip()
    elif action:
        parsed["summary"] = action
    match = RE_ACTION_MECHANICS.search(raw_text)
    if match:
        parsed["hp_percent"] = _coerce_int(match.group(1), -1)
        parsed["fanhun"] = _coerce_int(match.group(2), -1)
        parsed["moya"] = _coerce_int(match.group(3), -1)
        parsed["zhen"] = _coerce_int(match.group(4), -1)
    return parsed


def parse_world_boss_text(text, now=None):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return None
    if RE_WORLD_BOSS_OPEN.search(raw_text):
        return {"type": "open", "event_key": f"{get_day_key(now or time.time())}:{_event_hash(raw_text)}"}
    notice_match = RE_WORLD_BOSS_NOTICE.search(raw_text)
    if notice_match and "开启" not in notice_match.group("title"):
        title = notice_match.group("title").strip()
        if "败退" in title:
            result = "败退"
        elif any(keyword in title for keyword in ("击退", "胜", "捷", "凯旋")):
            result = "击退"
        else:
            result = title or "结束"
        participants_match = RE_PARTICIPANTS.search(raw_text)
        return {
            "type": "conclusion",
            "result": result,
            "title": title,
            "participants": _coerce_int(participants_match.group(1), 0) if participants_match else 0,
            "key": _event_hash(raw_text),
        }
    if "当前没有进行中的【真仙试锋】" in raw_text:
        return {"type": "inactive"}
    exhausted_match = RE_EXHAUSTED.search(raw_text)
    if exhausted_match:
        return {
            "type": "exhausted",
            "own_actions": _coerce_int(exhausted_match.group(1), WORLD_BOSS_DEFAULT_ACTION_LIMIT),
            "own_action_limit": _coerce_int(exhausted_match.group(2), WORLD_BOSS_DEFAULT_ACTION_LIMIT),
        }
    status = _parse_status_text(raw_text)
    if status:
        return status
    action = _parse_action_text(raw_text)
    if action:
        return action
    return None


def looks_like_world_boss_text(text):
    return parse_world_boss_text(text) is not None


def clear_world_boss_identity_state(send_as_id=None, *, persist=True, keep_last_error=True):
    identity_state = get_identity_state(send_as_id) if send_as_id is not None else state
    last_error = str(identity_state.get("world_boss_last_error") or "")
    identity_state["world_boss_action_count"] = 0
    identity_state["world_boss_action_limit"] = WORLD_BOSS_DEFAULT_ACTION_LIMIT
    identity_state["world_boss_attack_count"] = 0
    identity_state["world_boss_pending_msg_id"] = 0
    identity_state["world_boss_pending_action"] = ""
    identity_state["world_boss_pending_since"] = 0
    identity_state["world_boss_pending_retry_count"] = 0
    identity_state["world_boss_pending_action_seq"] = 0
    identity_state["world_boss_last_action"] = ""
    identity_state["world_boss_last_action_at"] = 0
    identity_state["world_boss_last_reply_msg_id"] = 0
    identity_state["world_boss_exhausted"] = False
    identity_state["world_boss_last_error"] = last_error if keep_last_error else ""
    if persist:
        save_state()


def _reset_all_identity_event_state(*, persist=False):
    for identity_id in get_identity_ids():
        try:
            clear_world_boss_identity_state(identity_id, persist=False, keep_last_error=False)
        except KeyError:
            continue
    if persist:
        save_state()


def _clear_world_boss_pending_tasks():
    clear_pending_tasks_by_commands(WORLD_BOSS_PENDING_COMMANDS)


def _clear_world_boss_pending_action(identity_state):
    identity_state["world_boss_pending_msg_id"] = 0
    identity_state["world_boss_pending_action"] = ""
    identity_state["world_boss_pending_since"] = 0
    identity_state["world_boss_pending_retry_count"] = 0
    identity_state["world_boss_pending_action_seq"] = 0


def _reset_world_boss_round(run_state, now, *, persist=True):
    run_state["round_started_at"] = float(now)
    run_state["round_completed_at"] = 0
    run_state["next_action_at"] = float(now)
    _set_run_state(run_state, persist=persist)
    return run_state


def _complete_world_boss_round(run_state, now, *, persist=True):
    completed_at = max(float(now), _coerce_float(run_state.get("last_action_at"), 0))
    run_state["round_completed_at"] = completed_at
    return _schedule_next_world_boss_action(run_state, completed_at, persist=persist)


def _has_pending_world_boss_action(identity_state):
    pending_msg_id = _coerce_int(identity_state.get("world_boss_pending_msg_id"), 0)
    pending_action = str(identity_state.get("world_boss_pending_action") or "").strip()
    pending_since = _coerce_float(identity_state.get("world_boss_pending_since"), 0)
    return pending_msg_id > 0 and pending_action in WORLD_BOSS_ACTION_COMMANDS and pending_since > 0


def _pending_action_due_at(identity_state):
    if not _has_pending_world_boss_action(identity_state):
        return 0
    return _coerce_float(identity_state.get("world_boss_pending_since"), 0) + WORLD_BOSS_PENDING_TIMEOUT_SEC


def _next_pending_action_due_at():
    due_times = []
    for identity_id in _enabled_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        due_at = _pending_action_due_at(identity_state)
        if due_at > 0:
            due_times.append(due_at)
    return min(due_times) if due_times else 0


def _has_any_pending_action():
    for identity_id in _enabled_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        if _has_pending_world_boss_action(identity_state):
            return True
    return False


def _has_due_pending_action(now):
    due_at = _next_pending_action_due_at()
    return due_at > 0 and float(now) >= due_at


def _next_new_round_at(run_state):
    completed_at = _coerce_float(run_state.get("round_completed_at"), 0)
    if completed_at <= 0:
        return 0
    return completed_at + WORLD_BOSS_ROUND_GAP_SEC


def _schedule_next_world_boss_action(run_state, now, *, persist=True):
    candidates = []
    next_round_at = _next_new_round_at(run_state)
    if next_round_at > 0:
        candidates.append(next_round_at)
    next_pending_due_at = _next_pending_action_due_at()
    if next_pending_due_at > 0:
        candidates.append(next_pending_due_at)
    if candidates:
        run_state["next_action_at"] = max(float(now), min(candidates))
    _set_run_state(run_state, persist=persist)
    return run_state


def _enabled_identity_ids():
    result = []
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        if bool(identity_state.get("world_boss_enabled")):
            result.append(int(identity_id))
    return result


def _strong_attacker(identity_id):
    if int(identity_id or 0) in WORLD_BOSS_STRONG_ATTACK_IDS:
        return True
    profile = get_send_as_profile(identity_id)
    candidates = {
        str(profile.get("username") or "").strip().lower(),
        str(profile.get("label") or "").strip().lower(),
    }
    return any(candidate in WORLD_BOSS_STRONG_ATTACK_NAMES for candidate in candidates if candidate)


def _identity_label(identity_id):
    profile = get_send_as_profile(identity_id)
    return str(profile.get("label") or profile.get("username") or identity_id).strip()


def _status_is_fresh(run_state, now):
    return bool(run_state.get("active")) and float(run_state.get("last_status_at") or 0) > 0 and now - float(run_state.get("last_status_at") or 0) <= WORLD_BOSS_STATUS_STALE_SEC


def _event_chain_id(run_state, now=None):
    event_key = str((run_state or {}).get("event_key") or "").strip() or f"{get_day_key(now or time.time())}:unknown"
    return f"world_boss:{event_key}"


def _strong_attack_allowed(run_state):
    phase = str(run_state.get("phase") or "")
    if "第二阶段" not in phase:
        return False
    hp = _coerce_int(run_state.get("hp_percent"), -1)
    moya = _coerce_int(run_state.get("moya"), -1)
    zhen = _coerce_int(run_state.get("zhen"), -1)
    if hp < 0 or hp > 80:
        return False
    return 0 <= moya <= 70 and zhen >= 75


def _phase_two_guard_target(run_state, summary):
    phase = str(run_state.get("phase") or "")
    if "第二阶段" not in phase:
        return 0
    zhen = _coerce_int(run_state.get("zhen"), -1)
    moya = _coerce_int(run_state.get("moya"), -1)
    if zhen < 0 or moya < 0:
        return 0
    if zhen > WORLD_BOSS_PHASE_TWO_CRITICAL_ZHEN:
        return 0
    if moya >= WORLD_BOSS_PHASE_TWO_GUARD_MOYA_LIMIT:
        return 0
    if moya >= 90:
        return 1
    if moya >= 85:
        return 2
    return max(2, min(4, max(1, summary["镇魂"] // 3)))


def _choose_maintenance_action(run_state):
    phase = str(run_state.get("phase") or "")
    moya = _coerce_int(run_state.get("moya"), -1)
    zhen = _coerce_int(run_state.get("zhen"), -1)
    summary = _normalize_summary(run_state.get("summary"))
    phase_two_guard_target = _phase_two_guard_target(run_state, summary)
    if phase_two_guard_target > 0 and summary["护阵"] < phase_two_guard_target:
        return "护阵"
    if "第二阶段" in phase and zhen <= WORLD_BOSS_PHASE_TWO_CRITICAL_ZHEN:
        return "镇魂"
    if moya >= 65:
        return "镇魂"
    if "第二阶段" in phase and moya >= 55:
        return "镇魂"
    if 0 <= zhen <= 35 and moya < 85:
        return "护阵"
    if 0 <= zhen <= 50 and moya < 70 and summary["护阵"] <= max(0, summary["镇魂"] // 3):
        return "护阵"
    if 0 <= zhen <= 65 and moya < 50 and summary["护阵"] * 4 <= max(1, summary["镇魂"]):
        return "护阵"
    return "镇魂"


def choose_world_boss_action(identity_id, identity_state, run_state, now=None):
    now = float(now if now is not None else time.time())
    if not _status_is_fresh(run_state, now):
        return ""
    if bool(identity_state.get("world_boss_exhausted")):
        return ""
    action_limit = max(1, _coerce_int(identity_state.get("world_boss_action_limit"), WORLD_BOSS_DEFAULT_ACTION_LIMIT))
    if _coerce_int(identity_state.get("world_boss_action_count"), 0) >= action_limit:
        return ""
    if _strong_attack_allowed(run_state) and _strong_attacker(identity_id):
        attack_count = _coerce_int(identity_state.get("world_boss_attack_count"), 0)
        if attack_count < WORLD_BOSS_STRONG_ATTACK_LIMIT:
            return "强攻"
    return _choose_maintenance_action(run_state)


def _clear_expired_pending(identity_id, identity_state, now):
    pending_msg_id = _coerce_int(identity_state.get("world_boss_pending_msg_id"), 0)
    if pending_msg_id <= 0:
        return False
    pending_since = _coerce_float(identity_state.get("world_boss_pending_since"), 0)
    if pending_since > 0 and now - pending_since <= WORLD_BOSS_PENDING_TIMEOUT_SEC:
        return True
    pending_action = str(identity_state.get("world_boss_pending_action") or "").strip()
    retry_count = _coerce_int(identity_state.get("world_boss_pending_retry_count"), 0)
    if pending_action in WORLD_BOSS_ACTION_COMMANDS and retry_count < WORLD_BOSS_PENDING_MAX_RETRY:
        return False
    _clear_world_boss_pending_action(identity_state)
    identity_state["world_boss_last_error"] = f"{pending_action or '指令'}等待回复超时，已补发 {retry_count} 次后放弃"
    console_log(
        f"🗡 真仙试锋[{_identity_label(identity_id)}] {pending_action or '指令'}超时，已补发 {retry_count} 次后放弃。",
        scope="identity",
        send_as_id=identity_id,
        limit=180,
    )
    mark_dirty()
    return False


def _eligible_identity_action(identity_id, run_state, now):
    try:
        identity_state = get_identity_state(identity_id)
    except KeyError:
        return "", None
    if _clear_expired_pending(identity_id, identity_state, now):
        return "", None
    action = choose_world_boss_action(identity_id, identity_state, run_state, now)
    return action, identity_state


def _identity_sort_key(identity_id, identity_state, run_state):
    return (
        _coerce_float(identity_state.get("world_boss_last_action_at"), 0),
        _coerce_int(identity_state.get("world_boss_action_count"), 0),
        _coerce_int(identity_state.get("world_boss_pending_retry_count"), 0),
        _coerce_int(identity_state.get("world_boss_pending_action_seq"), 0),
        int(identity_id),
    )


def _select_identity_and_action(run_state, now, *, allow_new_actions=True):
    candidates = []
    round_started_at = _coerce_float(run_state.get("round_started_at"), 0)
    for identity_id in _enabled_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        pending_msg_id = _coerce_int(identity_state.get("world_boss_pending_msg_id"), 0)
        pending_action = str(identity_state.get("world_boss_pending_action") or "").strip()
        if pending_msg_id > 0 and pending_action:
            if _clear_expired_pending(identity_id, identity_state, now):
                continue
            if _coerce_int(identity_state.get("world_boss_pending_msg_id"), 0) > 0 and pending_action in WORLD_BOSS_ACTION_COMMANDS:
                candidates.append((0.0, 0, 0, 0, int(identity_id), identity_state, pending_action))
            continue
        if not allow_new_actions:
            continue
        if _coerce_int(identity_state.get("world_boss_action_count"), 0) >= max(1, _coerce_int(identity_state.get("world_boss_action_limit"), WORLD_BOSS_DEFAULT_ACTION_LIMIT)):
            continue
        last_action_at = _coerce_float(identity_state.get("world_boss_last_action_at"), 0)
        if round_started_at > 0 and last_action_at >= round_started_at:
            continue
        candidates.append((*_identity_sort_key(identity_id, identity_state, run_state)[:-1], int(identity_id), identity_state, ""))
    candidates.sort()
    if _strong_attack_allowed(run_state):
        candidates.sort(key=lambda item: (0 if _strong_attacker(item[4]) else 1, item[0], item[1], item[2], item[4]))
    for _last_at, _count, _retry_count, _action_seq, identity_id, identity_state, pending_action in candidates:
        if pending_action:
            return identity_id, identity_state, pending_action
        action = choose_world_boss_action(identity_id, identity_state, run_state, now)
        if action:
            return identity_id, identity_state, action
    return 0, None, ""


def _update_run_metrics(run_state, parsed, now, current_msg_id=0):
    changed = False
    for key in ("phase",):
        value = str(parsed.get(key) or "").strip()
        if value and run_state.get(key) != value:
            run_state[key] = value
            changed = True
    for key in ("hp_percent", "fanhun", "break_progress", "moya", "zhen", "remaining_sec"):
        value = _coerce_int(parsed.get(key), -1)
        if key == "remaining_sec":
            value = max(0, value)
        if value >= 0 and run_state.get(key) != value:
            run_state[key] = value
            changed = True
    if parsed.get("type") in {"status", "action"}:
        run_state["last_status_at"] = float(now)
        if current_msg_id:
            run_state["last_status_msg_id"] = int(current_msg_id)
        changed = True
    return changed


async def _maybe_log_phase_change(run_state, now):
    phase = str(run_state.get("phase") or "").strip()
    if not phase or run_state.get("last_phase_log") == phase:
        return
    run_state["last_phase_log"] = phase
    await send_audit_log(
        f"🗡 真仙试锋阶段：{phase}｜血量 {run_state.get('hp_percent', '?')}%｜魔压 {run_state.get('moya', '?')}/100｜阵势 {run_state.get('zhen', '?')}/120",
        scope="global",
        priority="medium",
        limit=220,
    )


async def _maybe_log_progress(run_state, now, *, force=False):
    summary = _normalize_summary(run_state.get("summary"))
    total = sum(summary.values())
    if total <= 0:
        return
    last_total = _coerce_int(run_state.get("last_summary_log_total"), 0)
    last_at = _coerce_float(run_state.get("last_summary_log_at"), 0)
    if not force and (total == last_total or now - last_at < WORLD_BOSS_PROGRESS_LOG_GAP_SEC):
        return
    run_state["last_summary_log_at"] = float(now)
    run_state["last_summary_log_total"] = total
    await send_audit_log(
        f"🗡 真仙试锋进度：{run_state.get('phase') or '阶段未明'}｜血量 {run_state.get('hp_percent', '?')}%｜魔压 {run_state.get('moya', '?')}/100｜阵势 {run_state.get('zhen', '?')}/120｜镇魂 {summary['镇魂']}｜护阵 {summary['护阵']}｜强攻 {summary['强攻']}",
        scope="global",
        priority="low",
        limit=260,
    )


async def _open_event(parsed, now, current_msg_id=0):
    run_state = _get_run_state(now)
    event_key = str(parsed.get("event_key") or f"{get_day_key(now)}:{current_msg_id or int(now)}")
    if run_state.get("active") and run_state.get("event_key") == event_key:
        return True
    if run_state.get("last_open_log_key") != event_key:
        enabled_count = len(_enabled_identity_ids())
        if enabled_count > 0:
            await send_audit_log(
                f"🗡 真仙试锋开启：已启用 {enabled_count} 个身份，等待战况后快速参与。",
                scope="global",
                priority="medium",
                limit=220,
            )
    _clear_world_boss_pending_tasks()
    _reset_all_identity_event_state(persist=False)
    run_state = _blank_run_state(now)
    run_state["active"] = True
    run_state["event_key"] = event_key
    run_state["opened_at"] = float(now)
    run_state["last_open_log_key"] = event_key
    run_state["next_status_query_at"] = float(now) + 15
    _set_run_state(run_state)
    return True


async def _close_event(parsed, now, *, log=True):
    run_state = _get_run_state(now)
    result = str(parsed.get("result") or "结束").strip()
    conclusion_key = str(parsed.get("key") or result or get_day_key(now))
    duplicate = conclusion_key and conclusion_key == run_state.get("last_conclusion_key") and now - _coerce_float(run_state.get("last_conclusion_at"), 0) < 2 * 3600
    run_state["active"] = False
    run_state["closed_at"] = float(now)
    run_state["last_result"] = result
    run_state["last_conclusion_key"] = conclusion_key
    run_state["last_conclusion_at"] = float(now)
    participants = _coerce_int(parsed.get("participants"), 0)
    if participants > 0:
        run_state["participants"] = participants
    for identity_id in get_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        _clear_world_boss_pending_action(identity_state)
    _clear_world_boss_pending_tasks()
    if log and not duplicate:
        await _maybe_log_progress(run_state, now, force=True)
        summary = _normalize_summary(run_state.get("summary"))
        await send_audit_log(
            f"🗡 真仙试锋{result}：参战 {run_state.get('participants') or participants or '未知'}｜本脚本确认 镇魂 {summary['镇魂']} / 护阵 {summary['护阵']} / 强攻 {summary['强攻']}。",
            scope="global",
            priority="medium",
            limit=320,
        )
    _set_run_state(run_state)
    return True


async def _mark_inactive(now):
    run_state = _get_run_state(now)
    if run_state.get("active"):
        run_state["active"] = False
        run_state["closed_at"] = float(now)
        run_state["last_result"] = run_state.get("last_result") or "已结束"
    for identity_id in get_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        _clear_world_boss_pending_action(identity_state)
    _clear_world_boss_pending_tasks()
    _set_run_state(run_state)
    return True


def _apply_identity_own_count(identity_state, parsed):
    own_actions = _coerce_int(parsed.get("own_actions"), 0)
    own_limit = _coerce_int(parsed.get("own_action_limit"), 0)
    if own_limit > 0:
        identity_state["world_boss_action_limit"] = own_limit
    if own_actions > 0:
        identity_state["world_boss_action_count"] = max(_coerce_int(identity_state.get("world_boss_action_count"), 0), own_actions)
    if own_limit > 0 and own_actions >= own_limit:
        identity_state["world_boss_exhausted"] = True


def _note_identity_action_reply(identity_state, action, now, *, pending_action=""):
    action = str(action or "").strip()
    pending_action = str(pending_action or "").strip()
    if action not in WORLD_BOSS_ACTIONS:
        return False
    counted_by_send = bool(pending_action and pending_action == action)
    if not counted_by_send:
        identity_state["world_boss_action_count"] = _coerce_int(identity_state.get("world_boss_action_count"), 0) + 1
        if action == "强攻":
            identity_state["world_boss_attack_count"] = _coerce_int(identity_state.get("world_boss_attack_count"), 0) + 1
        identity_state["world_boss_last_action_at"] = float(now)
    identity_state["world_boss_last_action"] = action
    identity_state["world_boss_last_error"] = ""
    return True


async def _handle_status(parsed, now, *, identity_id=0, current_msg_id=0):
    run_state = _get_run_state(now)
    if not run_state.get("active"):
        run_state["active"] = True
        run_state["event_key"] = run_state.get("event_key") or f"{get_day_key(now)}:status"
        run_state["opened_at"] = run_state.get("opened_at") or float(now)
    _update_run_metrics(run_state, parsed, now, current_msg_id=current_msg_id)
    if identity_id:
        try:
            identity_state = get_identity_state(identity_id)
            _apply_identity_own_count(identity_state, parsed)
            if identity_state.get("world_boss_pending_action") == "status":
                _clear_world_boss_pending_action(identity_state)
        except KeyError:
            pass
    await _maybe_log_phase_change(run_state, now)
    _set_run_state(run_state)
    return True


async def _handle_action(parsed, now, *, identity_id=0, current_msg_id=0):
    run_state = _get_run_state(now)
    _update_run_metrics(run_state, parsed, now, current_msg_id=current_msg_id)
    if identity_id:
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            identity_state = None
        if identity_state is not None:
            if current_msg_id and _coerce_int(identity_state.get("world_boss_last_reply_msg_id"), 0) == int(current_msg_id):
                return True
            pending_action = str(identity_state.get("world_boss_pending_action") or "").strip()
            action = str(parsed.get("action") or pending_action).strip()
            _clear_world_boss_pending_action(identity_state)
            identity_state["world_boss_last_reply_msg_id"] = int(current_msg_id or 0)
            if _note_identity_action_reply(identity_state, action, now, pending_action=pending_action):
                summary = _normalize_summary(run_state.get("summary"))
                summary[action] = summary.get(action, 0) + 1
                run_state["summary"] = summary
    await _maybe_log_phase_change(run_state, now)
    await _maybe_log_progress(run_state, now)
    _set_run_state(run_state)
    return True


async def _handle_exhausted(parsed, now, *, identity_id=0):
    if identity_id:
        try:
            identity_state = get_identity_state(identity_id)
            _apply_identity_own_count(identity_state, parsed)
            identity_state["world_boss_exhausted"] = True
            _clear_world_boss_pending_action(identity_state)
            identity_state["world_boss_last_error"] = "本期出手已尽"
            save_state()
        except KeyError:
            pass
    return True


async def handle_world_boss_reply(text, now, reply_to=None, *, matched_family=None, reply_context=None, current_msg_id=0):
    if matched_family != "world_boss" and not looks_like_world_boss_text(text):
        return False
    parsed = parse_world_boss_text(text, now)
    if not parsed:
        return False
    identity_id = _coerce_int((reply_context or {}).get("send_as_id"), 0) or get_current_identity_id()
    event_id = _coerce_int(current_msg_id or getattr(reply_to, "id", 0), 0)
    parsed_type = parsed.get("type")
    if parsed_type == "open":
        return await _open_event(parsed, now, current_msg_id=event_id)
    if parsed_type == "conclusion":
        return await _close_event(parsed, now)
    if parsed_type == "inactive":
        return await _mark_inactive(now)
    if parsed_type == "exhausted":
        return await _handle_exhausted(parsed, now, identity_id=identity_id)
    if parsed_type == "status":
        return await _handle_status(parsed, now, identity_id=identity_id, current_msg_id=event_id)
    if parsed_type == "action":
        return await _handle_action(parsed, now, identity_id=identity_id, current_msg_id=event_id)
    return False


async def handle_world_boss_broadcast(text, now, event=None):
    parsed = parse_world_boss_text(text, now)
    if not parsed:
        return False
    event_id = _coerce_int(getattr(event, "id", 0), 0)
    parsed_type = parsed.get("type")
    enabled = bool(_enabled_identity_ids())
    if not enabled:
        run_state = _get_run_state(now)
        if parsed_type == "conclusion" and run_state.get("active"):
            return await _close_event(parsed, now, log=False)
        if parsed_type == "inactive" and run_state.get("active"):
            return await _mark_inactive(now)
        return False
    if parsed_type == "open":
        return await _open_event(parsed, now, current_msg_id=event_id)
    if parsed_type == "conclusion":
        return await _close_event(parsed, now)
    if parsed_type == "inactive":
        return await _mark_inactive(now)
    if parsed_type == "status":
        return await _handle_status(parsed, now, current_msg_id=event_id)
    if parsed_type == "action":
        return await _handle_action(parsed, now, current_msg_id=event_id)
    return False


async def _send_status_query(identity_id, now, run_state, reason):
    chain_id = _event_chain_id(run_state, now)
    msg = await send_game_command(
        f"{CMD_WORLD_BOSS_STATUS} 查看战况",
        track=True,
        max_retry=0,
        reply_timeout=WORLD_BOSS_REPLY_TIMEOUT_SEC,
        send_as_id=identity_id,
        priority=WORLD_BOSS_EVENT_PRIORITY,
        source_module=WORLD_BOSS_MODULE_NAME,
        op_id=f"{chain_id}:status:{identity_id}:{int(now)}",
        chain_id=chain_id,
    )
    sent_at = _coerce_float(getattr(msg, "sent_at", 0), 0) or time.time()
    if not msg:
        try:
            identity_state = get_identity_state(identity_id)
            identity_state["world_boss_last_error"] = f"{reason}战况查询发送失败"
        except KeyError:
            pass
        run_state["next_status_query_at"] = sent_at + WORLD_BOSS_STATUS_QUERY_GAP_SEC
        _set_run_state(run_state)
        return False
    try:
        identity_state = get_identity_state(identity_id)
        if not _has_pending_world_boss_action(identity_state):
            identity_state["world_boss_pending_msg_id"] = int(getattr(msg, "id", 0) or 0)
            identity_state["world_boss_pending_action"] = "status"
            identity_state["world_boss_pending_since"] = sent_at
            identity_state["world_boss_pending_retry_count"] = 0
            identity_state["world_boss_pending_action_seq"] = 0
        identity_state["world_boss_last_error"] = ""
    except KeyError:
        pass
    run_state["next_status_query_at"] = sent_at + WORLD_BOSS_STATUS_QUERY_GAP_SEC
    run_state["next_action_at"] = max(_coerce_float(run_state.get("next_action_at"), 0), sent_at + WORLD_BOSS_STATUS_AFTER_QUERY_GAP_SEC)
    _set_run_state(run_state)
    return True


async def _send_action(identity_id, identity_state, action, now, run_state):
    command = WORLD_BOSS_ACTION_COMMANDS.get(action)
    if not command:
        return False
    chain_id = _event_chain_id(run_state, now)
    is_retry = (
        _coerce_int(identity_state.get("world_boss_pending_msg_id"), 0) > 0
        and str(identity_state.get("world_boss_pending_action") or "").strip() == action
    )
    action_seq = (
        _coerce_int(identity_state.get("world_boss_pending_action_seq"), 0)
        if is_retry
        else _coerce_int(identity_state.get("world_boss_action_count"), 0) + 1
    )
    retry_count = _coerce_int(identity_state.get("world_boss_pending_retry_count"), 0) + 1 if is_retry else 0
    msg = await send_game_command(
        command,
        track=True,
        max_retry=0,
        reply_timeout=WORLD_BOSS_REPLY_TIMEOUT_SEC,
        send_as_id=identity_id,
        priority=WORLD_BOSS_EVENT_PRIORITY,
        source_module=WORLD_BOSS_MODULE_NAME,
        op_id=f"{chain_id}:action:{identity_id}:{action}:{action_seq}:try{retry_count}",
        chain_id=chain_id,
    )
    sent_at = _coerce_float(getattr(msg, "sent_at", 0), 0) or time.time()
    if not msg:
        identity_state["world_boss_last_error"] = f"{action}发送失败"
        run_state["next_action_at"] = sent_at + WORLD_BOSS_STATUS_QUERY_GAP_SEC
        _set_run_state(run_state)
        return False
    latest_run_state = _get_run_state(sent_at)
    identity_state["world_boss_pending_msg_id"] = int(getattr(msg, "id", 0) or 0)
    identity_state["world_boss_pending_action"] = action
    identity_state["world_boss_pending_since"] = sent_at
    identity_state["world_boss_pending_retry_count"] = retry_count
    identity_state["world_boss_pending_action_seq"] = action_seq
    if not latest_run_state.get("active") or latest_run_state.get("event_key") != run_state.get("event_key"):
        _clear_world_boss_pending_action(identity_state)
        identity_state["world_boss_last_error"] = "发送后事件已结束，等待无进行中回包"
        clear_pending_tasks_by_commands({command}, send_as_id=identity_id)
        _set_run_state(latest_run_state)
        return sent_at
    run_state = latest_run_state
    identity_state["world_boss_last_action"] = action
    identity_state["world_boss_last_action_at"] = sent_at
    identity_state["world_boss_last_error"] = ""
    if not is_retry:
        identity_state["world_boss_action_count"] = action_seq
    if action == "强攻" and not is_retry:
        identity_state["world_boss_attack_count"] = _coerce_int(identity_state.get("world_boss_attack_count"), 0) + 1
    run_state["last_action_at"] = sent_at
    run_state["next_action_at"] = sent_at + WORLD_BOSS_ACTION_GAP_SEC
    _set_run_state(run_state)
    retry_suffix = f"补发{retry_count}" if is_retry else ""
    console_log(
        f"🗡 真仙试锋[{_identity_label(identity_id)}] 已发送{action}{retry_suffix}，快速轮询下一身份。",
        scope="identity",
        send_as_id=identity_id,
        limit=180,
    )
    return sent_at


def _world_boss_round_task_running():
    return _WORLD_BOSS_ROUND_TASK is not None and not _WORLD_BOSS_ROUND_TASK.done()


def _world_boss_round_done(task):
    global _WORLD_BOSS_ROUND_TASK
    if _WORLD_BOSS_ROUND_TASK is task:
        _WORLD_BOSS_ROUND_TASK = None
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        import traceback

        console_log("⚠️ 真仙试锋轮转任务异常：\n" + traceback.format_exc(), limit=1200)


def _start_world_boss_round_task(now):
    global _WORLD_BOSS_ROUND_TASK
    if _world_boss_round_task_running():
        return False
    try:
        task = asyncio.create_task(_run_world_boss_action_round(float(now or time.time())))
    except RuntimeError:
        return False
    _WORLD_BOSS_ROUND_TASK = task
    task.add_done_callback(_world_boss_round_done)
    return True


async def _run_world_boss_action_round(now):
    current_now = float(now)
    sent_count = 0
    allow_new_actions = True
    async with _WORLD_BOSS_SCHEDULER_LOCK:
        run_state = _get_run_state(current_now)
        if not run_state.get("active"):
            return
        if not _status_is_fresh(run_state, current_now) and not _has_due_pending_action(current_now):
            if _has_any_pending_action():
                _schedule_next_world_boss_action(run_state, current_now)
            return
        if current_now < _coerce_float(run_state.get("next_action_at"), 0):
            return
        next_round_at = _next_new_round_at(run_state)
        if next_round_at > 0:
            if current_now >= next_round_at:
                run_state = _reset_world_boss_round(run_state, current_now)
            elif _has_due_pending_action(current_now):
                allow_new_actions = False
            else:
                _schedule_next_world_boss_action(run_state, current_now)
                return
        elif _coerce_float(run_state.get("round_completed_at"), 0) > 0:
            _schedule_next_world_boss_action(run_state, current_now)
            return
        elif _coerce_float(run_state.get("round_started_at"), 0) <= 0 and _status_is_fresh(run_state, current_now):
            run_state = _reset_world_boss_round(run_state, current_now)
        elif not _status_is_fresh(run_state, current_now):
            allow_new_actions = False

        while sent_count < WORLD_BOSS_MAX_ACTIONS_PER_TICK:
            run_state = _get_run_state(current_now)
            if not run_state.get("active"):
                return
            if not _status_is_fresh(run_state, current_now) and not _has_due_pending_action(current_now):
                if sent_count > 0 and allow_new_actions:
                    _complete_world_boss_round(run_state, current_now)
                else:
                    _schedule_next_world_boss_action(run_state, current_now)
                return
            next_action_at = _coerce_float(run_state.get("next_action_at"), 0)
            if current_now < next_action_at:
                await asyncio.sleep(min(1.0, max(0.0, next_action_at - current_now)))
                current_now = max(current_now, next_action_at)
                continue

            identity_id, identity_state, action = _select_identity_and_action(run_state, current_now, allow_new_actions=allow_new_actions)
            if not identity_id or not identity_state or not action:
                if sent_count > 0 and allow_new_actions:
                    _complete_world_boss_round(run_state, current_now)
                else:
                    _schedule_next_world_boss_action(run_state, current_now)
                return
            sent_at = await _send_action(identity_id, identity_state, action, current_now, run_state)
            if not sent_at:
                return
            sent_count += 1
            current_now = max(current_now, _coerce_float(sent_at, current_now))
            if sent_count < WORLD_BOSS_MAX_ACTIONS_PER_TICK:
                run_state = _get_run_state(current_now)
                delay = max(0.0, _coerce_float(run_state.get("next_action_at"), 0) - current_now)
                if delay > 0:
                    await asyncio.sleep(delay)
                    current_now = max(current_now, _coerce_float(run_state.get("next_action_at"), current_now))

        run_state = _get_run_state(current_now)
        if allow_new_actions:
            _complete_world_boss_round(run_state, current_now)
        else:
            _schedule_next_world_boss_action(run_state, current_now)


def _fallback_window_open(now):
    local = datetime.fromtimestamp(float(now), TZ_LOCAL)
    minute = local.hour * 60 + local.minute
    return WORLD_BOSS_FALLBACK_START_MINUTE <= minute <= WORLD_BOSS_FALLBACK_END_MINUTE


async def run_world_boss_scheduler(now):
    if _WORLD_BOSS_SCHEDULER_LOCK.locked():
        return
    async with _WORLD_BOSS_SCHEDULER_LOCK:
        now = float(now or time.time())
        enabled_ids = _enabled_identity_ids()
        run_state = _get_run_state(now)
        if not enabled_ids:
            if run_state != get_world_boss_run_state():
                _set_run_state(run_state, persist=False)
            return

        if not run_state.get("active"):
            day_key = get_day_key(now)
            if _fallback_window_open(now) and run_state.get("fallback_status_day") != day_key:
                run_state["fallback_status_day"] = day_key
                await _send_status_query(enabled_ids[0], now, run_state, "日常观测")
            else:
                _set_run_state(run_state, persist=False)
            return

        if run_state.get("remaining_sec", 0) <= 0 and run_state.get("last_status_at", 0) > 0 and now - float(run_state.get("last_status_at") or 0) > 120:
            run_state["active"] = False
            run_state["closed_at"] = now
            run_state["last_result"] = run_state.get("last_result") or "等待结算"
            _set_run_state(run_state)
            return

        if not _status_is_fresh(run_state, now):
            if _has_any_pending_action():
                _schedule_next_world_boss_action(run_state, now)
                if not _has_due_pending_action(now):
                    return
                if not _world_boss_round_task_running():
                    _start_world_boss_round_task(now)
                return
            if now >= _coerce_float(run_state.get("next_status_query_at"), 0):
                await _send_status_query(enabled_ids[0], now, run_state, "战况过期")
            else:
                _set_run_state(run_state, persist=False)
            return
        if not _world_boss_round_task_running():
            _start_world_boss_round_task(now)


def get_world_boss_status_text():
    now = time.time()
    run_state = _get_run_state(now)
    enabled = bool(state.get("world_boss_enabled"))
    pending_action = str(state.get("world_boss_pending_action") or "").strip()
    pending_msg_id = _coerce_int(state.get("world_boss_pending_msg_id"), 0)
    pending_text = f"{pending_action or '待回复'}#{pending_msg_id}" if pending_msg_id > 0 else "无"
    next_action_at = _coerce_float(run_state.get("next_action_at"), 0)
    last_error = str(state.get("world_boss_last_error") or "").strip() or "无"
    summary = _normalize_summary(run_state.get("summary"))
    lines = [
        "🗡 真仙试锋",
        f"- 开关：{'开启' if enabled else '关闭'}",
        f"- 事件：{'进行中' if run_state.get('active') else (run_state.get('last_result') or '未观测')}",
        f"- 阶段：{run_state.get('phase') or '未知'}",
        f"- 战况：血量 {run_state.get('hp_percent', '?')}%｜魔压 {run_state.get('moya', '?')}/100｜阵势 {run_state.get('zhen', '?')}/120",
        f"- 本身份：出手 {state.get('world_boss_action_count', 0)}/{state.get('world_boss_action_limit', WORLD_BOSS_DEFAULT_ACTION_LIMIT)}｜强攻 {state.get('world_boss_attack_count', 0)}/{WORLD_BOSS_STRONG_ATTACK_LIMIT}｜{'已耗尽' if state.get('world_boss_exhausted') else '可参与'}",
        f"- 全局确认：镇魂 {summary['镇魂']}｜护阵 {summary['护阵']}｜强攻 {summary['强攻']}",
        f"- 待回复：{pending_text}",
        f"- 下次动作：{fmt_abs_ts(next_action_at)}（{fmt_remaining(next_action_at)}）",
        f"- 最近错误：{last_error}",
    ]
    return "\n".join(lines)


__all__ = [
    "choose_world_boss_action",
    "clear_world_boss_identity_state",
    "get_world_boss_status_text",
    "handle_world_boss_broadcast",
    "handle_world_boss_reply",
    "looks_like_world_boss_text",
    "parse_world_boss_text",
    "run_world_boss_scheduler",
]
