import asyncio
import hashlib
import json
import re
import time
from collections import deque
from datetime import datetime
from types import SimpleNamespace

from ..config import (
    CMD_QINGYUANZI_ATTACK,
    CMD_QINGYUANZI_BREAK,
    CMD_QINGYUANZI_GUARD,
    CMD_QINGYUANZI_SUPPRESS,
    CMD_WORLD_BOSS_STATUS,
    MESSAGES_DIR,
    TZ_LOCAL,
)
from ..message_log_recovery import find_message_log_replies
from ..persistence import mark_dirty, save_state
from ..runtime import clear_pending_tasks_by_commands, console_log, get_sent_message_chat_id, send_audit_log, send_game_command
from ..state import (
    REALM_SORT_INDEX,
    YUANYING_MIN_REALM_INDEX,
    get_current_identity_id,
    get_game_group_id,
    get_identity_account,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    is_cave_public_identity_available,
    get_miniapp_auto_config,
    get_send_as_profile,
    get_world_boss_run_state,
    get_world_boss_rotation_state,
    set_world_boss_run_state,
    set_world_boss_rotation_state,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, get_day_key, parse_wait_time
from .world_boss_miniapp_runtime import (
    WORLD_BOSS_MINIAPP_FINISH_RESERVE_WINDOWS,
    extract_world_boss_miniapp_launch,
    run_world_boss_miniapp_event,
)


WORLD_BOSS_MODULE_NAME = "真仙试锋"
WORLD_BOSS_ACTIONS = {"镇魂", "护阵", "强攻", "破幡"}
WORLD_BOSS_MAINTENANCE_ACTIONS = {"镇魂", "护阵"}
WORLD_BOSS_ACTION_COMMANDS = {
    "破幡": CMD_QINGYUANZI_BREAK,
    "镇魂": CMD_QINGYUANZI_SUPPRESS,
    "护阵": CMD_QINGYUANZI_GUARD,
    "强攻": CMD_QINGYUANZI_ATTACK,
}
WORLD_BOSS_PENDING_TIMEOUT_SEC = 5
WORLD_BOSS_REPLY_TIMEOUT_SEC = 90
WORLD_BOSS_ACTION_GAP_SEC = 1.0
WORLD_BOSS_ACTION_COOLDOWN_SEC = 90
WORLD_BOSS_ROUND_GAP_SEC = 90.0
WORLD_BOSS_MAX_ACTIONS_PER_TICK = 64
WORLD_BOSS_STATUS_QUERY_COMMAND = CMD_WORLD_BOSS_STATUS
WORLD_BOSS_STATUS_PENDING_TIMEOUT_SEC = WORLD_BOSS_REPLY_TIMEOUT_SEC
WORLD_BOSS_STATUS_MAX_RETRIES = 2
WORLD_BOSS_STATUS_AFTER_QUERY_GAP_SEC = 0
WORLD_BOSS_STATUS_STALE_SEC = 120
WORLD_BOSS_STATUS_QUERY_GAP_SEC = 3 * 60
WORLD_BOSS_EVENT_TTL_SEC = 35 * 60
WORLD_BOSS_RECOVERY_PROBE_DELAY_SEC = 45
WORLD_BOSS_RECOVERY_LOG_GAP_MIN_SEC = 8 * 60
WORLD_BOSS_RECOVERY_LOG_LOOKBACK_SEC = 2 * 3600
WORLD_BOSS_RECOVERY_LOG_TAIL_LINES = 2500
WORLD_BOSS_RECOVERY_PENDING_TTL_SEC = WORLD_BOSS_STATUS_PENDING_TIMEOUT_SEC * (WORLD_BOSS_STATUS_MAX_RETRIES + 1) + 30
WORLD_BOSS_STRONG_ATTACK_LIMIT = 5
WORLD_BOSS_DEFAULT_ACTION_LIMIT = 5
WORLD_BOSS_PROGRESS_LOG_GAP_SEC = 5 * 60
WORLD_BOSS_RESCUE_MOYA_THRESHOLD = 80
WORLD_BOSS_RESCUE_ZHEN_THRESHOLD = 10
WORLD_BOSS_PHASE_TWO_CRITICAL_ZHEN = 35
WORLD_BOSS_PHASE_TWO_GUARD_MOYA_LIMIT = 95
WORLD_BOSS_OPENING_GROUP_SIZE = 11
WORLD_BOSS_STRONG_MIN_REALM = "元婴初期"
WORLD_BOSS_STRONG_MIN_REALM_INDEX = YUANYING_MIN_REALM_INDEX
WORLD_BOSS_STRONG_ATTACK_IDS = {8659059191, 301299112}
WORLD_BOSS_STRONG_ATTACK_NAMES = {"walterwa2000", "wa2000", "jfdffdddd", "吧唧"}
WORLD_BOSS_MINIAPP_ACCOUNT_LIMIT = 4
WORLD_BOSS_ROTATION_DEFAULT_REWARD = "斩青元者"
WORLD_BOSS_ROTATION_REWARD_ALIASES = {
    "斩青玉元": WORLD_BOSS_ROTATION_DEFAULT_REWARD,
}
WORLD_BOSS_MINIAPP_BATTLE_PRIORITY_GAP_SEC = 0.25
WORLD_BOSS_IDENTITY_REJECTION_TTL_SEC = 7 * 24 * 3600
WORLD_BOSS_IDENTITY_REJECTION_STATUSES = {"boss_identity_invalid"}
WORLD_BOSS_IDENTITY_REJECTION_PROTOCOL_VERSION = 2
WORLD_BOSS_LOCAL_USERNAME_ALIASES = {
    8659059191: ("WalterWA2000", "wa2000"),
    301299112: ("jfdffdddd",),
}
WORLD_BOSS_PENDING_COMMANDS = set(WORLD_BOSS_ACTION_COMMANDS.values()) | {WORLD_BOSS_STATUS_QUERY_COMMAND}
WORLD_BOSS_EVENT_PRIORITY = "event_burst"
WORLD_BOSS_STATUS_PRIORITY = "reactive"

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
RE_CONTRIBUTION_ROW = re.compile(
    r"^\s*(?P<rank>\d+)\.\s*@(?P<username>[A-Za-z0-9_]+)\s*-\s*(?P<score>[\d,]+)\s*分"
    r"(?:\s*｜\s*强攻\s*(?P<attacks>\d+))?"
    r"(?:\s*｜\s*伤害\s*(?P<damage>[^\n\r]+))?",
    re.M,
)
RE_SETTLEMENT_ROW = re.compile(r"^\s*-\s*@(?P<username>[A-Za-z0-9_]+)[:：](?P<rewards>[^\n\r]+)", re.M)
RE_RARE_DROP_ROW = re.compile(
    r"^\s*-\s*@(?P<username>[A-Za-z0-9_]+)\s*获得\s*(?P<reward>[^\n\r]+)",
    re.M,
)

_WORLD_BOSS_SCHEDULER_LOCK = asyncio.Lock()
_WORLD_BOSS_ROUND_TASK = None
_WORLD_BOSS_MINIAPP_TASK = None
_WORLD_BOSS_RECOVERY_BOOT_AT = time.time()
_WORLD_BOSS_RECOVERY_PROBE_DONE = False


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
        "miniapp_only": False,
        "miniapp_entry_identity_ids": [],
        "miniapp_auto_status": "",
        "miniapp_auto_started_at": 0,
        "miniapp_auto_finished_at": 0,
        "miniapp_auto_progress": [],
        "miniapp_auto_results": [],
        "miniapp_conclusion_evidence": {},
        "fallback_status_day": "",
        "fallback_status_at": 0,
        "last_priority_window_key": "",
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


def _event_day_from_key(run_state):
    event_key = str((run_state or {}).get("event_key") or "").strip()
    day_key = event_key.split(":", 1)[0].strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_key):
        return day_key
    return ""


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
        "fallback_status_at",
        "miniapp_auto_started_at",
        "miniapp_auto_finished_at",
    ):
        record[key] = max(0.0, _coerce_float(record.get(key), 0))
    for key in ("hp_percent", "fanhun", "break_progress", "moya", "zhen", "last_status_msg_id", "last_summary_log_total", "participants"):
        record[key] = _coerce_int(record.get(key), -1 if key in {"hp_percent", "fanhun", "break_progress", "moya", "zhen"} else 0)
    record["summary"] = _normalize_summary(record.get("summary"))
    raw_entry_ids = record.get("miniapp_entry_identity_ids") or []
    if not isinstance(raw_entry_ids, (list, tuple)):
        raw_entry_ids = []
    record["miniapp_entry_identity_ids"] = [
        int(identity_id)
        for identity_id in raw_entry_ids
        if _coerce_int(identity_id, 0) > 0
    ]
    for key in (
        "event_key",
        "phase",
        "last_phase_log",
        "last_open_log_key",
        "last_conclusion_key",
        "last_result",
        "fallback_status_day",
        "last_priority_window_key",
    ):
        record[key] = str(record.get(key) or "").strip()
    record["miniapp_only"] = bool(record.get("miniapp_only"))
    record["miniapp_auto_status"] = str(record.get("miniapp_auto_status") or "").strip()
    raw_results = record.get("miniapp_auto_results") or []
    record["miniapp_auto_results"] = [dict(item) for item in raw_results if isinstance(item, dict)][:16]
    raw_progress = record.get("miniapp_auto_progress") or []
    record["miniapp_auto_progress"] = [
        {
            "identity_id": _coerce_int(item.get("identity_id"), 0),
            "phase": str(item.get("phase") or ""),
            "ok": bool(item.get("ok")),
            "status": str(item.get("status") or ""),
            "error": _short_text(item.get("error") or "", 120),
            "summary": dict(item.get("summary") or {}) if isinstance(item.get("summary"), dict) else {},
            "updated_at": max(0.0, _coerce_float(item.get("updated_at"), 0)),
        }
        for item in raw_progress
        if isinstance(item, dict) and _coerce_int(item.get("identity_id"), 0) > 0
    ][:16]
    if record["active"] and record["opened_at"] > 0 and now - record["opened_at"] > WORLD_BOSS_EVENT_TTL_SEC:
        record["active"] = False
        record["closed_at"] = record["closed_at"] or now
        record["last_result"] = record["last_result"] or "超时结束"
    elif record["active"] and _event_day_from_key(record) and _event_day_from_key(record) != get_day_key(now):
        record["active"] = False
        record["closed_at"] = record["closed_at"] or now
        record["last_result"] = record["last_result"] or "跨日过期"
    return record


def _get_run_state(now=None):
    return _normalize_run_state(get_world_boss_run_state(), now)


def _set_run_state(record, *, persist=True, now=None):
    # 归一化时必须沿用调用方的 now。省略它会让写入按真实墙钟重新判定过期，于是
    # 一份刚以 now 建立的状态可能立刻被判成「跨日过期」而失活——调用方推进时间、
    # 回放历史事件或恰好跨过午夜时都会踩到。
    set_world_boss_run_state(_normalize_run_state(record, now))
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


def _new_miniapp_world_boss_open(raw_text):
    return (
        "点击下方按钮进入真仙战场" in raw_text
        or "进入真仙战场" in raw_text
        or "本轮废弃破幡、镇魂、护阵" in raw_text
        or "个人战斗：一次完整参战" in raw_text
    )


def _parse_contribution_rows(raw_text):
    rows = []
    for match in RE_CONTRIBUTION_ROW.finditer(str(raw_text or "")):
        rows.append({
            "rank": _coerce_int(match.group("rank"), 0),
            "username": str(match.group("username") or "").strip(),
            "score": _coerce_int(str(match.group("score") or "0").replace(",", ""), 0),
            "attacks": _coerce_int(match.group("attacks"), 0),
            "damage": str(match.group("damage") or "").strip(),
        })
    return rows


def _parse_settlement_rows(raw_text):
    rows = []
    for match in RE_SETTLEMENT_ROW.finditer(str(raw_text or "")):
        rows.append({
            "username": str(match.group("username") or "").strip(),
            "rewards": str(match.group("rewards") or "").strip(),
        })
    return rows


def _parse_rare_drop_rows(raw_text):
    rows = []
    for match in RE_RARE_DROP_ROW.finditer(str(raw_text or "")):
        rows.append({
            "username": str(match.group("username") or "").strip(),
            "reward": str(match.group("reward") or "").strip(),
        })
    return rows


def parse_world_boss_text(text, now=None):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return None
    if RE_WORLD_BOSS_OPEN.search(raw_text):
        return {
            "type": "open",
            "event_key": f"{get_day_key(now or time.time())}:{_event_hash(raw_text)}",
            "miniapp_only": _new_miniapp_world_boss_open(raw_text),
        }
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
            "participants_present": participants_match is not None,
            "contributions": _parse_contribution_rows(raw_text),
            "settlements": _parse_settlement_rows(raw_text),
            "rare_drops": _parse_rare_drop_rows(raw_text),
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
    identity_state["world_boss_last_sent_at"] = 0
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


def _is_closed_event_state(run_state):
    return (
        not bool(run_state.get("active"))
        and bool(str(run_state.get("event_key") or "").strip())
    )


def _inactive_event_expired(run_state, now):
    if not _is_closed_event_state(run_state):
        return False
    event_day = _event_day_from_key(run_state)
    if event_day and event_day != get_day_key(now):
        return True
    closed_at = _coerce_float(run_state.get("closed_at"), 0)
    opened_at = _coerce_float(run_state.get("opened_at"), 0)
    reference_at = closed_at or opened_at
    return reference_at > 0 and float(now) - reference_at > WORLD_BOSS_EVENT_TTL_SEC


def _archive_inactive_event_state(run_state, now, reason):
    last_result = str(run_state.get("last_result") or reason or "事件已过期").strip()
    last_conclusion_key = str(run_state.get("last_conclusion_key") or "").strip()
    last_conclusion_at = _coerce_float(run_state.get("last_conclusion_at"), 0)
    participants = _coerce_int(run_state.get("participants"), 0)
    fallback_status_day = str(run_state.get("fallback_status_day") or "").strip()
    fallback_status_at = _coerce_float(run_state.get("fallback_status_at"), 0)
    run_state.clear()
    run_state.update(_blank_run_state(now))
    run_state["last_result"] = last_result
    run_state["last_conclusion_key"] = last_conclusion_key
    run_state["last_conclusion_at"] = last_conclusion_at
    run_state["participants"] = participants
    run_state["fallback_status_day"] = fallback_status_day
    run_state["fallback_status_at"] = fallback_status_at


def _status_retry_allowed(run_state, now):
    event_day = _event_day_from_key(run_state)
    if event_day and event_day != get_day_key(now):
        return False
    if bool(run_state.get("active")):
        opened_at = _coerce_float(run_state.get("opened_at"), 0)
        if opened_at <= 0:
            return True
        return float(now) - opened_at <= WORLD_BOSS_EVENT_TTL_SEC
    return False


def _mark_recovery_probe_attempt(run_state, now):
    global _WORLD_BOSS_RECOVERY_PROBE_DONE
    _WORLD_BOSS_RECOVERY_PROBE_DONE = True
    run_state["fallback_status_day"] = get_day_key(now)
    run_state["fallback_status_at"] = float(now)


def _recovery_probe_pending(run_state, now):
    if bool(run_state.get("active")):
        return False
    probe_at = _coerce_float(run_state.get("fallback_status_at"), 0)
    return probe_at > 0 and float(now) - probe_at <= WORLD_BOSS_RECOVERY_PENDING_TTL_SEC


def _parse_message_log_epoch(raw_ts):
    text = str(raw_ts or "")[:19]
    if not text:
        return 0.0
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except ValueError:
        return 0.0


def _message_log_path_for_day(day_key):
    return f"{MESSAGES_DIR}/{day_key}.log"


def _recent_message_log_epochs(now):
    now = float(now or time.time())
    day_keys = [get_day_key(now)]
    previous_day = get_day_key(now - 24 * 3600)
    if previous_day not in day_keys:
        day_keys.append(previous_day)
    epochs = []
    cutoff = now - WORLD_BOSS_RECOVERY_LOG_LOOKBACK_SEC
    for day_key in day_keys:
        try:
            with open(_message_log_path_for_day(day_key), "r", encoding="utf-8", errors="replace") as handle:
                lines = deque(handle, maxlen=WORLD_BOSS_RECOVERY_LOG_TAIL_LINES)
        except OSError:
            continue
        for line in lines:
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            epoch = _parse_message_log_epoch(payload.get("ts"))
            if cutoff <= epoch <= now:
                epochs.append(epoch)
    return sorted(set(epochs))


def _recent_message_log_gap_since(reference_at, now):
    """Return True when recent game-message logs show a real listener outage."""
    reference_at = float(reference_at or 0)
    epochs = _recent_message_log_epochs(now)
    if len(epochs) < 2:
        return False
    for previous, current in zip(epochs, epochs[1:]):
        if current <= reference_at:
            continue
        if current - previous >= WORLD_BOSS_RECOVERY_LOG_GAP_MIN_SEC:
            return True
    return False


def _recovery_probe_due(run_state, now):
    global _WORLD_BOSS_RECOVERY_PROBE_DONE
    if _WORLD_BOSS_RECOVERY_PROBE_DONE or bool(run_state.get("active")):
        return False
    if float(now) < _WORLD_BOSS_RECOVERY_BOOT_AT + WORLD_BOSS_RECOVERY_PROBE_DELAY_SEC:
        return False
    probe_at = _coerce_float(run_state.get("fallback_status_at"), 0)
    if not _recent_message_log_gap_since(probe_at, now):
        _WORLD_BOSS_RECOVERY_PROBE_DONE = True
        return False
    return True


def _clear_all_world_boss_pending(reason=""):
    cleared = False
    for identity_id in get_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        if _coerce_int(identity_state.get("world_boss_pending_msg_id"), 0) > 0:
            _clear_world_boss_pending_action(identity_state)
            if reason:
                identity_state["world_boss_last_error"] = reason
            cleared = True
    if cleared:
        _clear_world_boss_pending_tasks()
        mark_dirty()
    return cleared


def _clear_inactive_world_boss_status_residue(run_state, now):
    changed = False
    if _coerce_float(run_state.get("next_status_query_at"), 0) > 0:
        run_state["next_status_query_at"] = 0
        changed = True
    if _coerce_float(run_state.get("next_action_at"), 0) > 0:
        run_state["next_action_at"] = 0
        changed = True
    for identity_id in get_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        last_error = str(identity_state.get("world_boss_last_error") or "")
        if "战况查询" not in last_error:
            continue
        _clear_world_boss_pending_action(identity_state)
        identity_state["world_boss_last_error"] = ""
        changed = True
    if changed:
        _set_run_state(run_state, persist=False, now=now)
    return changed


def _clear_stale_inactive_event_pending(run_state, now):
    if not _is_closed_event_state(run_state):
        return False
    expired = _inactive_event_expired(run_state, now)
    reason = "事件已过期" if expired else (run_state.get("last_result") or "事件已结束")
    cleared = _clear_all_world_boss_pending(reason)
    if expired:
        _archive_inactive_event_state(run_state, now, reason)
        _set_run_state(run_state, persist=False, now=now)
        console_log(
            "🗡 真仙试锋已归档过期事件状态。",
            scope="global",
            limit=180,
        )
        return cleared
    if cleared:
        run_state["next_action_at"] = 0
        run_state["next_status_query_at"] = 0
        _set_run_state(run_state, persist=False, now=now)
        console_log(
            "🗡 真仙试锋已清理过期事件残留待回复。",
            scope="global",
            limit=180,
        )
    return cleared


def _reset_world_boss_round(run_state, now, *, persist=True):
    run_state["round_started_at"] = float(now)
    run_state["round_completed_at"] = 0
    run_state["next_action_at"] = float(now)
    _set_run_state(run_state, persist=persist, now=now)
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


def _has_pending_world_boss_status(identity_state):
    pending_msg_id = _coerce_int(identity_state.get("world_boss_pending_msg_id"), 0)
    pending_action = str(identity_state.get("world_boss_pending_action") or "").strip()
    pending_since = _coerce_float(identity_state.get("world_boss_pending_since"), 0)
    return pending_msg_id > 0 and pending_action == "status" and pending_since > 0


def _pending_action_due_at(identity_state):
    if not _has_pending_world_boss_action(identity_state):
        return 0
    return _coerce_float(identity_state.get("world_boss_pending_since"), 0) + WORLD_BOSS_PENDING_TIMEOUT_SEC


def _pending_status_due_at(identity_state):
    if not _has_pending_world_boss_status(identity_state):
        return 0
    return _coerce_float(identity_state.get("world_boss_pending_since"), 0) + WORLD_BOSS_STATUS_PENDING_TIMEOUT_SEC


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


def _next_pending_status_due_at():
    due_times = []
    for identity_id in _enabled_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        due_at = _pending_status_due_at(identity_state)
        if due_at > 0:
            due_times.append(due_at)
    return min(due_times) if due_times else 0


def _next_identity_cooldown_due_at(now):
    due_times = []
    for identity_id in _enabled_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        if _has_pending_world_boss_action(identity_state):
            continue
        if bool(identity_state.get("world_boss_exhausted")):
            continue
        action_limit = max(1, _coerce_int(identity_state.get("world_boss_action_limit"), WORLD_BOSS_DEFAULT_ACTION_LIMIT))
        if _coerce_int(identity_state.get("world_boss_action_count"), 0) >= action_limit:
            continue
        last_action_at = _coerce_float(identity_state.get("world_boss_last_action_at"), 0)
        if last_action_at <= 0:
            return 0
        due_times.append(last_action_at + WORLD_BOSS_ACTION_COOLDOWN_SEC)
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


def _pending_status_identity(now):
    candidates = []
    for identity_id in _enabled_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        if not _has_pending_world_boss_status(identity_state):
            continue
        due_at = _pending_status_due_at(identity_state)
        candidates.append((due_at, int(identity_id), identity_state))
    if not candidates:
        return 0, None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1], candidates[0][2]


def _has_any_pending_status():
    identity_id, _identity_state = _pending_status_identity(time.time())
    return bool(identity_id)


def _has_due_pending_status(now):
    due_at = _next_pending_status_due_at()
    return due_at > 0 and float(now) >= due_at


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
        if next_round_at > float(now):
            candidates.append(next_round_at)
        else:
            cooldown_due_at = _next_identity_cooldown_due_at(now)
            candidates.append(cooldown_due_at if cooldown_due_at > float(now) else float(now))
    next_pending_due_at = _next_pending_action_due_at()
    if next_pending_due_at > 0:
        candidates.append(next_pending_due_at)
    next_pending_status_due_at = _next_pending_status_due_at()
    if next_pending_status_due_at > 0:
        candidates.append(next_pending_status_due_at)
    if candidates:
        run_state["next_action_at"] = max(float(now), min(candidates))
    _set_run_state(run_state, persist=persist, now=now)
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


def _miniapp_entry_candidate_identity_ids():
    result = []
    for identity_id in get_identity_ids():
        if not is_cave_public_identity_available(identity_id):
            continue
        try:
            get_identity_state(identity_id)
        except KeyError:
            continue
        if get_identity_account(identity_id) <= 0:
            continue
        result.append(int(identity_id))
    return result


def _opening_strategy_active(run_state):
    phase = str(run_state.get("phase") or "")
    return not phase or "第一阶段" in phase


def _identity_profile(identity_id):
    profile = get_send_as_profile(identity_id)
    return profile if isinstance(profile, dict) else {}


def _identity_realm_index(identity_id):
    profile = _identity_profile(identity_id)
    realm = str(profile.get("realm") or "").strip()
    return REALM_SORT_INDEX.get(realm, -1)


def _identity_battle_power_value(identity_id):
    profile = _identity_profile(identity_id)
    return max(0, _coerce_int(profile.get("battle_power_value"), 0))


def _strong_attacker(identity_id):
    identity_id = int(identity_id or 0)
    if identity_id in WORLD_BOSS_STRONG_ATTACK_IDS:
        return True
    profile = _identity_profile(identity_id)
    candidates = {
        str(profile.get("username") or "").strip().lower(),
        str(profile.get("label") or "").strip().lower(),
    }
    if any(candidate in WORLD_BOSS_STRONG_ATTACK_NAMES for candidate in candidates if candidate):
        return True
    realm_index = _identity_realm_index(identity_id)
    return realm_index >= WORLD_BOSS_STRONG_MIN_REALM_INDEX


def _strong_attacker_priority_key(identity_id):
    return (
        0 if _strong_attacker(identity_id) else 1,
        -_identity_realm_index(identity_id),
        -_identity_battle_power_value(identity_id),
        int(identity_id or 0),
    )


def _miniapp_account_key(identity_id):
    account_id = get_identity_account(identity_id)
    if account_id > 0:
        return f"account:{account_id}"
    # Missing account binding is treated as a separate bucket so lab tests and
    # partially configured identities stay conservative instead of collapsing.
    return f"identity:{int(identity_id or 0)}"


def _rotation_config():
    raw = dict(get_miniapp_auto_config() or {})
    account_ids = []
    for raw_account_id in raw.get("world_boss_rotation_account_ids") or ():
        account_id = _coerce_int(raw_account_id, 0)
        if account_id > 0 and account_id not in account_ids:
            account_ids.append(account_id)
    target_reward = _normalize_rotation_target_reward(raw.get("world_boss_rotation_target_reward"))
    return {
        "account_ids": account_ids,
        "target_reward": target_reward,
    }


def _normalize_rotation_target_reward(value):
    reward = str(value or WORLD_BOSS_ROTATION_DEFAULT_REWARD).strip() or WORLD_BOSS_ROTATION_DEFAULT_REWARD
    for legacy, current in WORLD_BOSS_ROTATION_REWARD_ALIASES.items():
        reward = reward.replace(legacy, current)
    return reward


def _account_rotation_identity_ids(account_id):
    account_id = int(account_id or 0)
    return sorted(
        int(identity_id)
        for identity_id in _miniapp_entry_candidate_identity_ids()
        if int(get_identity_account(identity_id) or 0) == account_id
    )


def _normalized_rotation_state():
    raw = get_world_boss_rotation_state()
    accounts = raw.get("accounts") if isinstance(raw, dict) else {}
    target_reward = _rotation_config()["target_reward"]
    stored_target_reward = _normalize_rotation_target_reward((raw or {}).get("target_reward")) if (raw or {}).get("target_reward") else ""
    if stored_target_reward != target_reward:
        accounts = {}
    calibrated_account_ids = {
        _coerce_int(account_id, 0)
        for account_id in (raw or {}).get("inventory_calibrated_account_ids") or ()
        if _coerce_int(account_id, 0) > 0
    }
    if stored_target_reward != target_reward:
        calibrated_account_ids = set()
    raw_rejections = (raw or {}).get("identity_rejections") or {}
    identity_rejections = {}
    if isinstance(raw_rejections, dict):
        for raw_identity_id, raw_record in raw_rejections.items():
            identity_id = _coerce_int(raw_identity_id, 0)
            if identity_id <= 0 or not isinstance(raw_record, dict):
                continue
            if _coerce_int(raw_record.get("protocol_version"), 0) != WORLD_BOSS_IDENTITY_REJECTION_PROTOCOL_VERSION:
                # Historical 403s were recorded before channel identities used
                # the public MiniApp playerId encoding. Do not keep suppressing
                # those identities after the wire protocol changes.
                continue
            rejected_at = _coerce_float(raw_record.get("rejected_at"), 0)
            suppress_until = _coerce_float(raw_record.get("suppress_until"), 0)
            if suppress_until <= 0 and rejected_at > 0:
                suppress_until = rejected_at + WORLD_BOSS_IDENTITY_REJECTION_TTL_SEC
            if suppress_until <= 0:
                continue
            identity_rejections[str(identity_id)] = {
                "identity_id": identity_id,
                "account_id": _coerce_int(raw_record.get("account_id"), 0),
                "status": str(raw_record.get("status") or "boss_identity_invalid"),
                "reason": _short_text(raw_record.get("reason") or "boss_identity_invalid", 120),
                "rejected_at": rejected_at,
                "suppress_until": suppress_until,
                "protocol_version": _coerce_int(raw_record.get("protocol_version"), 0),
            }
    return {
        "accounts": dict(accounts or {}) if isinstance(accounts, dict) else {},
        "inventory_calibrated_account_ids": sorted(calibrated_account_ids),
        "identity_rejections": identity_rejections,
        "last_conclusion_key": str((raw or {}).get("last_conclusion_key") or "") if stored_target_reward == target_reward else "",
        "target_reward": target_reward,
    }


def _identity_rejection_active(rotation_state, identity_id, now=None):
    now = float(now if now is not None else time.time())
    record = dict((rotation_state.get("identity_rejections") or {}).get(str(int(identity_id or 0))) or {})
    return bool(record) and _coerce_float(record.get("suppress_until"), 0) > now


def _record_world_boss_identity_rejection(identity_id, status, reason="", now=None):
    identity_id = int(identity_id or 0)
    status = str(status or "").strip().lower()
    if identity_id <= 0 or status not in WORLD_BOSS_IDENTITY_REJECTION_STATUSES:
        return False
    now = float(now if now is not None else time.time())
    rotation_state = _normalized_rotation_state()
    rotation_state.setdefault("identity_rejections", {})[str(identity_id)] = {
        "identity_id": identity_id,
        "account_id": int(get_identity_account(identity_id) or 0),
        "status": status,
        "reason": _short_text(reason or status, 120),
        "rejected_at": now,
        "suppress_until": now + WORLD_BOSS_IDENTITY_REJECTION_TTL_SEC,
        "protocol_version": WORLD_BOSS_IDENTITY_REJECTION_PROTOCOL_VERSION,
    }
    set_world_boss_rotation_state(rotation_state)
    mark_dirty()
    return True


def _clear_world_boss_identity_rejection(identity_id):
    identity_id = int(identity_id or 0)
    if identity_id <= 0:
        return False
    rotation_state = _normalized_rotation_state()
    if not rotation_state.setdefault("identity_rejections", {}).pop(str(identity_id), None):
        return False
    set_world_boss_rotation_state(rotation_state)
    mark_dirty()
    return True


def _update_world_boss_identity_eligibility_from_result(item, now=None):
    if not isinstance(item, dict) or str(item.get("phase") or "") != "join":
        return False
    identity_id = _coerce_int(item.get("identity_id"), 0)
    if bool(item.get("ok")):
        return _clear_world_boss_identity_rejection(identity_id)
    status = str(item.get("status") or "").strip().lower()
    # Older runtime results could classify the HTTP 403 as generic ``failed``
    # while preserving the useful application error in ``error``. Keep the
    # rejection guard compatible with those already-persisted result shapes.
    error = str(item.get("error") or "").strip().lower()
    if status not in WORLD_BOSS_IDENTITY_REJECTION_STATUSES and "boss_identity_invalid" in error:
        status = "boss_identity_invalid"
    return _record_world_boss_identity_rejection(
        identity_id,
        status,
        item.get("error") or status,
        now=now,
    )


def _rotation_account_record(account_id):
    account_id = int(account_id or 0)
    rotation_state = _normalized_rotation_state()
    raw = dict(rotation_state["accounts"].get(str(account_id)) or {})
    candidates = _account_rotation_identity_ids(account_id)
    eligible_candidates = [
        identity_id
        for identity_id in candidates
        if not _identity_rejection_active(rotation_state, identity_id)
    ]
    completed_ids = {
        _coerce_int(identity_id, 0)
        for identity_id in raw.get("completed_identity_ids") or ()
        if _coerce_int(identity_id, 0) > 0
    }
    completed_ids.intersection_update(candidates)
    current_identity_id = _coerce_int(raw.get("current_identity_id"), 0)
    if current_identity_id not in eligible_candidates or current_identity_id in completed_ids:
        current_identity_id = next(
            (identity_id for identity_id in eligible_candidates if identity_id not in completed_ids),
            0,
        )
    return {
        "account_id": account_id,
        "identity_ids": candidates,
        "current_identity_id": current_identity_id,
        "completed_identity_ids": sorted(completed_ids),
        "suppressed_identity_ids": sorted(set(candidates) - set(eligible_candidates)),
        "last_completed_identity_id": _coerce_int(raw.get("last_completed_identity_id"), 0),
        "last_completed_at": _coerce_float(raw.get("last_completed_at"), 0),
        "last_reward": str(raw.get("last_reward") or ""),
    }


def get_world_boss_rotation_account_record(account_id):
    return _rotation_account_record(account_id)


def _save_rotation_account_record(record, *, conclusion_key=""):
    rotation_state = _normalized_rotation_state()
    account_id = int(record.get("account_id") or 0)
    if account_id > 0:
        rotation_state["accounts"][str(account_id)] = dict(record)
    if conclusion_key:
        rotation_state["last_conclusion_key"] = str(conclusion_key)
    rotation_state["target_reward"] = _rotation_config()["target_reward"]
    set_world_boss_rotation_state(rotation_state)
    mark_dirty()


def _rotation_identity_for_account(account_id):
    return int(_rotation_account_record(account_id).get("current_identity_id") or 0)


def select_world_boss_miniapp_entry_identities(limit=WORLD_BOSS_MINIAPP_ACCOUNT_LIMIT):
    """Pick at most one world-boss MiniApp entry identity per login account."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = WORLD_BOSS_MINIAPP_ACCOUNT_LIMIT
    limit = max(0, limit)
    if limit <= 0:
        return []
    best_by_account = {}
    rotation_accounts = set(_rotation_config()["account_ids"])
    rotation_state = _normalized_rotation_state()
    for identity_id in _miniapp_entry_candidate_identity_ids():
        if _identity_rejection_active(rotation_state, identity_id):
            continue
        account_key = _miniapp_account_key(identity_id)
        account_id = int(get_identity_account(identity_id) or 0)
        if account_id in rotation_accounts:
            current_identity_id = _rotation_identity_for_account(account_id)
            if current_identity_id > 0:
                best_by_account[account_key] = current_identity_id
            continue
        current = best_by_account.get(account_key)
        if current is None or _strong_attacker_priority_key(identity_id) < _strong_attacker_priority_key(current):
            best_by_account[account_key] = int(identity_id)
    return sorted(best_by_account.values(), key=_strong_attacker_priority_key)[:limit]


def _opening_identity_order():
    enabled_ids = _enabled_identity_ids()
    return sorted(enabled_ids, key=_strong_attacker_priority_key)


def _opening_action_for_identity(identity_id, identity_state):
    action_count = _coerce_int(identity_state.get("world_boss_action_count"), 0)
    if action_count != 0:
        return ""
    enabled_ids = _opening_identity_order()
    if not enabled_ids:
        return ""
    try:
        index = enabled_ids.index(int(identity_id))
    except ValueError:
        return ""
    group_size = min(WORLD_BOSS_OPENING_GROUP_SIZE, max(1, (len(enabled_ids) + 1) // 2))
    return "破幡" if index < group_size else ""


def _identity_label(identity_id):
    profile = get_send_as_profile(identity_id)
    return str(profile.get("label") or profile.get("username") or identity_id).strip()


def _status_is_fresh(run_state, now):
    return bool(run_state.get("active")) and float(run_state.get("last_status_at") or 0) > 0 and now - float(run_state.get("last_status_at") or 0) <= WORLD_BOSS_STATUS_STALE_SEC


def _miniapp_only_event_recent(run_state, now):
    if not bool((run_state or {}).get("miniapp_only")):
        return False
    opened_at = _coerce_float((run_state or {}).get("opened_at"), 0)
    if opened_at <= 0:
        return False
    return float(now) - opened_at <= WORLD_BOSS_EVENT_TTL_SEC


def _event_chain_id(run_state, now=None):
    event_key = str((run_state or {}).get("event_key") or "").strip() or f"{get_day_key(now or time.time())}:unknown"
    return f"world_boss:{event_key}"


def _open_event_key(parsed, now, current_msg_id=0):
    msg_id = _coerce_int(current_msg_id, 0)
    if msg_id > 0:
        return f"{get_day_key(now)}:{msg_id}"
    return str(parsed.get("event_key") or f"{get_day_key(now)}:{int(now)}")


def _status_event_key(now, current_msg_id=0):
    msg_id = _coerce_int(current_msg_id, 0)
    if msg_id > 0:
        return f"{get_day_key(now)}:status:{msg_id}"
    return f"{get_day_key(now)}:status:{int(now)}"


def _strong_attack_allowed(run_state):
    phase = str(run_state.get("phase") or "")
    is_phase_two = "第二阶段" in phase
    is_phase_three = "第三阶段" in phase
    if not is_phase_two and not is_phase_three:
        return False
    hp = _coerce_int(run_state.get("hp_percent"), -1)
    moya = _coerce_int(run_state.get("moya"), -1)
    zhen = _coerce_int(run_state.get("zhen"), -1)
    if hp < 0 or hp > 80:
        return False
    if not 0 <= moya <= 70:
        return False
    zhen_floor = 70 if is_phase_three else 75
    return zhen >= zhen_floor


def _priority_window_key(run_state):
    if not _strong_attack_allowed(run_state):
        return ""
    phase = str(run_state.get("phase") or "").strip()
    hp = _coerce_int(run_state.get("hp_percent"), -1)
    # Bucket HP so frequent status refreshes inside the same damage band do not
    # keep reopening the same strong-attack burst.
    hp_bucket = (hp // 10) * 10 if hp >= 0 else -1
    return f"strong:{phase}:{hp_bucket}"


def _has_ready_priority_identity(run_state, now):
    if not _strong_attack_allowed(run_state):
        return False
    summary = _normalize_summary(run_state.get("summary"))
    if summary.get("强攻", 0) >= WORLD_BOSS_STRONG_ATTACK_LIMIT:
        return False
    for identity_id in _enabled_identity_ids():
        if not _strong_attacker(identity_id):
            continue
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        if _has_pending_world_boss_action(identity_state):
            continue
        if bool(identity_state.get("world_boss_exhausted")):
            continue
        action_limit = max(1, _coerce_int(identity_state.get("world_boss_action_limit"), WORLD_BOSS_DEFAULT_ACTION_LIMIT))
        if _coerce_int(identity_state.get("world_boss_action_count"), 0) >= action_limit:
            continue
        if _coerce_int(identity_state.get("world_boss_attack_count"), 0) >= WORLD_BOSS_STRONG_ATTACK_LIMIT:
            continue
        last_action_at = _coerce_float(identity_state.get("world_boss_last_action_at"), 0)
        if last_action_at > 0 and float(now) - last_action_at < WORLD_BOSS_ACTION_COOLDOWN_SEC:
            continue
        return True
    return False


def _maybe_interrupt_round_for_priority_window(run_state, now):
    window_key = _priority_window_key(run_state)
    if not window_key or run_state.get("last_priority_window_key") == window_key:
        return False
    if _coerce_float(run_state.get("round_completed_at"), 0) <= 0:
        return False
    if not _has_ready_priority_identity(run_state, now):
        return False
    run_state["last_priority_window_key"] = window_key
    run_state["round_started_at"] = 0
    run_state["round_completed_at"] = 0
    run_state["next_action_at"] = float(now)
    return True


def _urgent_rescue_action(run_state):
    moya = _coerce_int(run_state.get("moya"), -1)
    zhen = _coerce_int(run_state.get("zhen"), -1)
    moya_danger = moya >= WORLD_BOSS_RESCUE_MOYA_THRESHOLD if moya >= 0 else False
    zhen_danger = 0 <= zhen <= WORLD_BOSS_RESCUE_ZHEN_THRESHOLD if zhen >= 0 else False
    if not moya_danger and not zhen_danger:
        return ""
    if moya_danger and not zhen_danger:
        return "镇魂"
    if zhen_danger and not moya_danger:
        return "护阵"
    moya_margin = max(0, 100 - moya)
    zhen_margin = max(0, zhen)
    return "护阵" if zhen_margin <= moya_margin else "镇魂"


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
    rescue_action = _urgent_rescue_action(run_state)
    if rescue_action:
        return rescue_action
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
    rescue_action = _urgent_rescue_action(run_state)
    if rescue_action:
        return rescue_action
    if _opening_strategy_active(run_state):
        opening_action = _opening_action_for_identity(identity_id, identity_state)
        if opening_action:
            return opening_action
    if _strong_attack_allowed(run_state) and _strong_attacker(identity_id):
        attack_count = _coerce_int(identity_state.get("world_boss_attack_count"), 0)
        summary = _normalize_summary(run_state.get("summary"))
        if attack_count < WORLD_BOSS_STRONG_ATTACK_LIMIT and summary.get("强攻", 0) < WORLD_BOSS_STRONG_ATTACK_LIMIT:
            return "强攻"
    if _strong_attacker(identity_id):
        return ""
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
    if pending_action in WORLD_BOSS_ACTION_COMMANDS:
        return False
    _clear_world_boss_pending_action(identity_state)
    identity_state["world_boss_last_error"] = f"{pending_action or '指令'}等待回复超时"
    console_log(
        f"🗡 真仙试锋[{_identity_label(identity_id)}] {pending_action or '指令'}超时。",
        scope="identity",
        send_as_id=identity_id,
        limit=180,
    )
    mark_dirty()
    return False


def _is_world_boss_reply_log_entry(entry):
    return looks_like_world_boss_text(str((entry or {}).get("text") or "").strip())


async def _recover_world_boss_pending_from_message_log(identity_id, identity_state, now):
    pending_msg_id = _coerce_int(identity_state.get("world_boss_pending_msg_id"), 0)
    if pending_msg_id <= 0:
        return False
    replies = find_message_log_replies(
        pending_msg_id,
        now,
        lookback_sec=max(15 * 60, WORLD_BOSS_REPLY_TIMEOUT_SEC * 5),
        lookahead_sec=30,
        chat_id=get_sent_message_chat_id(pending_msg_id, default=get_game_group_id(), send_as_id=identity_id),
        predicate=_is_world_boss_reply_log_entry,
    )
    if not replies:
        return False
    pending_action = str(identity_state.get("world_boss_pending_action") or "").strip()
    command = WORLD_BOSS_STATUS_QUERY_COMMAND if pending_action == "status" else WORLD_BOSS_ACTION_COMMANDS.get(pending_action, "")
    reply_to = SimpleNamespace(id=pending_msg_id, raw_text=command)
    handled_any = False
    for entry in replies:
        handled = await handle_world_boss_reply(
            entry.get("text") or "",
            float(entry.get("ts_epoch") or now),
            reply_to=reply_to,
            matched_family="world_boss",
            reply_context={"send_as_id": int(identity_id or 0), "reply_to_msg_id": pending_msg_id},
            current_msg_id=int(entry.get("message_id") or 0),
        )
        handled_any = handled_any or handled
    if handled_any:
        console_log(
            f"🗡 真仙试锋[{_identity_label(identity_id)}] 日志补偿：已采纳超时回包，消息ID={pending_msg_id}",
            scope="identity",
            send_as_id=identity_id,
            limit=220,
        )
    return handled_any


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
        max(
            _coerce_float(identity_state.get("world_boss_last_action_at"), 0),
            _coerce_float(identity_state.get("world_boss_last_sent_at"), 0),
        ),
        _coerce_int(identity_state.get("world_boss_action_count"), 0),
        _coerce_int(identity_state.get("world_boss_pending_retry_count"), 0),
        _coerce_int(identity_state.get("world_boss_pending_action_seq"), 0),
        int(identity_id),
    )


def _select_identity_and_action(run_state, now, *, allow_new_actions=True):
    candidates = []
    pending_candidates = []
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
                pending_candidates.append((0.0, 0, 0, 0, int(identity_id), identity_state, pending_action))
            continue
        if not allow_new_actions:
            continue
        if _coerce_int(identity_state.get("world_boss_action_count"), 0) >= max(1, _coerce_int(identity_state.get("world_boss_action_limit"), WORLD_BOSS_DEFAULT_ACTION_LIMIT)):
            continue
        last_action_at = _coerce_float(identity_state.get("world_boss_last_action_at"), 0)
        if last_action_at > 0 and now - last_action_at < WORLD_BOSS_ACTION_COOLDOWN_SEC:
            continue
        last_sent_at = _coerce_float(identity_state.get("world_boss_last_sent_at"), 0)
        if round_started_at > 0 and max(last_action_at, last_sent_at) >= round_started_at:
            continue
        candidates.append((*_identity_sort_key(identity_id, identity_state, run_state)[:-1], int(identity_id), identity_state, ""))
    candidates.sort()
    pending_candidates.sort()
    if _strong_attack_allowed(run_state):
        candidates.sort(key=lambda item: (*_strong_attacker_priority_key(item[4]), item[0], item[1], item[2]))
    elif _opening_strategy_active(run_state):
        candidates.sort(
            key=lambda item: (
                0 if _opening_action_for_identity(item[4], item[5]) else 1,
                *_strong_attacker_priority_key(item[4]),
                item[0],
                item[1],
                item[2],
            )
        )
    search_order = candidates if allow_new_actions and candidates else pending_candidates
    for _last_at, _count, _retry_count, _action_seq, identity_id, identity_state, pending_action in search_order:
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
        f"🗡 真仙试锋进度：{run_state.get('phase') or '阶段未明'}｜血量 {run_state.get('hp_percent', '?')}%｜魔压 {run_state.get('moya', '?')}/100｜阵势 {run_state.get('zhen', '?')}/120｜破幡 {summary['破幡']}｜镇魂 {summary['镇魂']}｜护阵 {summary['护阵']}｜强攻 {summary['强攻']}",
        scope="global",
        priority="low",
        limit=260,
    )


async def _open_event(parsed, now, current_msg_id=0):
    if bool(parsed.get("miniapp_only")):
        return await _notify_world_boss_open_only(parsed, now, current_msg_id=current_msg_id)
    run_state = _get_run_state(now)
    event_key = _open_event_key(parsed, now, current_msg_id=current_msg_id)
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
    _set_run_state(run_state, now=now)
    return True


def _world_boss_miniapp_auto_config():
    raw = dict(get_miniapp_auto_config() or {})
    try:
        account_limit = max(1, min(WORLD_BOSS_MINIAPP_ACCOUNT_LIMIT, int(raw.get("world_boss_auto_account_limit", 1) or 1)))
    except (TypeError, ValueError, OverflowError):
        account_limit = 1
    excluded_ids = raw.get("world_boss_auto_excluded_identity_ids") or []
    if not isinstance(excluded_ids, (list, tuple, set)):
        excluded_ids = []
    raw_window_skips = raw.get("world_boss_auto_window_skip_by_identity") or {}
    if not isinstance(raw_window_skips, dict):
        raw_window_skips = {}
    window_skip_by_identity = {}
    for raw_identity_id, raw_skip_count in raw_window_skips.items():
        identity_id = _coerce_int(raw_identity_id, 0)
        skip_count = max(0, min(32, _coerce_int(raw_skip_count, 0)))
        if identity_id > 0 and skip_count > 0:
            window_skip_by_identity[identity_id] = skip_count
    rotation = _rotation_config()
    # A rotation account's current identity always uses the full attack plan.
    # Ignore stale per-identity skip values left from a previous fixed setup.
    for account_id in rotation["account_ids"]:
        current_identity_id = _rotation_identity_for_account(account_id)
        if current_identity_id > 0:
            window_skip_by_identity.pop(current_identity_id, None)
    return {
        "enabled": bool(raw.get("world_boss_auto_enabled")),
        "account_limit": account_limit,
        # Compatibility-only field. Entry is intentionally parallel; battle
        # launch spacing is controlled by battle_priority_gap_sec.
        "account_gap_sec": 0,
        "excluded_identity_ids": {
            _coerce_int(identity_id, 0)
            for identity_id in excluded_ids
            if _coerce_int(identity_id, 0) > 0
        },
        "window_skip_by_identity": window_skip_by_identity,
        "rotation": rotation,
    }


def _world_boss_miniapp_task_running():
    return _WORLD_BOSS_MINIAPP_TASK is not None and not _WORLD_BOSS_MINIAPP_TASK.done()


async def _run_world_boss_miniapp_automation(
    event_key,
    identity_ids,
    event,
    text,
    opened_at,
    account_gap_sec,
    window_skip_by_identity=None,
):
    async def record_progress(item):
        run_state = _get_run_state()
        if run_state.get("event_key") != event_key:
            return
        _update_world_boss_identity_eligibility_from_result(item)
        record = {
            "identity_id": _coerce_int(item.get("identity_id"), 0),
            "phase": str(item.get("phase") or ""),
            "ok": bool(item.get("ok")),
            "status": str(item.get("status") or ""),
            "error": _short_text(item.get("error") or "", 120),
            "retry_after_sec": max(0.0, _coerce_float(item.get("retry_after_sec"), 0)),
            "summary": dict(item.get("summary") or {}) if isinstance(item.get("summary"), dict) else {},
            "updated_at": time.time(),
        }
        progress = [
            existing
            for existing in (run_state.get("miniapp_auto_progress") or [])
            if not (
                _coerce_int(existing.get("identity_id"), 0) == record["identity_id"]
                and str(existing.get("phase") or "") == record["phase"]
            )
        ]
        progress.append(record)
        run_state["miniapp_auto_progress"] = progress[-16:]
        completed = sum(1 for entry in progress if entry.get("phase") == "battle")
        run_state["last_result"] = f"MiniApp 自动执行中｜战斗完成 {completed}/{len(identity_ids)}"
        _set_run_state(run_state)

    try:
        result = await run_world_boss_miniapp_event(
            identity_ids,
            event,
            message_text=text,
            opened_at=opened_at,
            account_gap_sec=account_gap_sec,
            battle_priority_gap_sec=WORLD_BOSS_MINIAPP_BATTLE_PRIORITY_GAP_SEC,
            progress_callback=record_progress,
            window_skip_by_identity=window_skip_by_identity,
        )
    except Exception as exc:
        result = {"ok": False, "status": "runtime_error", "joined_count": 0, "results": [], "error": _short_text(exc)}
    run_state = _get_run_state()
    if run_state.get("event_key") != event_key:
        return
    run_state["miniapp_auto_status"] = str(result.get("status") or "failed")
    run_state["miniapp_auto_finished_at"] = time.time()
    run_state["miniapp_auto_results"] = [
        {
            "identity_id": _coerce_int(item.get("identity_id"), 0),
            "phase": str(item.get("phase") or ""),
            "ok": bool(item.get("ok")),
            "status": str(item.get("status") or ""),
            "error": _short_text(item.get("error") or "", 120),
            "retry_after_sec": max(0.0, _coerce_float(item.get("retry_after_sec"), 0)),
            "summary": dict(item.get("summary") or {}) if isinstance(item.get("summary"), dict) else {},
        }
        for item in (result.get("results") or [])
        if isinstance(item, dict)
    ][:16]
    for item in run_state["miniapp_auto_results"]:
        _update_world_boss_identity_eligibility_from_result(item)
    run_state["miniapp_auto_progress"] = [
        {
            **item,
            "updated_at": time.time(),
        }
        for item in run_state["miniapp_auto_results"]
    ]
    _reconcile_world_boss_miniapp_results(run_state)
    joined_count = _coerce_int(result.get("joined_count"), 0)
    settled = [
        item
        for item in run_state["miniapp_auto_results"]
        if item.get("phase") == "battle"
        and item.get("ok")
        and item.get("status") in {"settled", "conclusion_confirmed"}
    ]
    partial = [
        item
        for item in run_state["miniapp_auto_results"]
        if item.get("phase") == "battle"
        and not item.get("ok")
        and item.get("status") in {"event_closed_partial"}
    ]
    failed = [
        item
        for item in run_state["miniapp_auto_results"]
        if not item.get("ok") and item not in partial
    ]
    run_state["last_result"] = (
        f"MiniApp 入场 {joined_count}｜结算 {len(settled)}｜部分 {len(partial)}｜失败 {len(failed)}"
    )
    _set_run_state(run_state)
    detail_parts = []
    for item in run_state["miniapp_auto_results"]:
        if item.get("phase") != "battle" and item.get("ok"):
            continue
        detail = f"{_identity_label(item['identity_id'])}:{item.get('status') or ('ok' if item.get('ok') else 'failed')}"
        retry_after_sec = max(0.0, _coerce_float(item.get("retry_after_sec"), 0))
        if retry_after_sec > 0:
            detail += f"｜限流等待{retry_after_sec:g}秒"
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        hits = max(
            _coerce_int(summary.get("realtime_hit_count"), 0),
            _coerce_int(summary.get("hits"), 0),
            _coerce_int(summary.get("accepted_hit_count"), 0),
        )
        perfects = max(
            _coerce_int(summary.get("perfects"), 0),
            _coerce_int(summary.get("accepted_perfect_count"), 0),
        )
        damage = max(
            _coerce_float(summary.get("realtime_damage_yi"), 0),
            _coerce_float(summary.get("accepted_damage_yi"), 0),
        )
        score = _coerce_int(summary.get("score"), 0)
        if hits or perfects or damage or score:
            planned = _coerce_int(summary.get("planned_window_count"), 0)
            hit_text = f"{hits}/{planned}" if planned > 0 else str(hits)
            detail += f"｜命中{hit_text} 完美{perfects} 伤害{damage:g}亿 质量分{score}"
            rejected = _coerce_int(summary.get("rejected_window_count"), 0)
            if rejected:
                detail += f" 窗口拒绝{rejected}"
            skipped = _coerce_int(summary.get("window_skip_count"), 0)
            finish_reserve = _coerce_int(summary.get("finish_reserve_window_count"), 0)
            identity_extra = _coerce_int(summary.get("identity_extra_window_skip_count"), 0)
            if finish_reserve:
                detail += f" 结算预留{finish_reserve}"
            if identity_extra:
                detail += f" 额外少出手{identity_extra}"
            elif skipped and not finish_reserve:
                detail += f" 主动少出手{skipped}"
        detail_parts.append(detail)
    details = "、".join(detail_parts) or "无明细"
    await send_audit_log(
        f"🗡 真仙试锋 MiniApp 合并结果：入场 {joined_count}｜结算 {len(settled)}｜部分 {len(partial)}｜失败 {len(failed)}\n{details}",
        scope="global",
        priority="high" if failed else "medium",
        limit=420,
    )


def _start_world_boss_miniapp_automation(
    event_key,
    identity_ids,
    event,
    text,
    opened_at,
    account_gap_sec,
    window_skip_by_identity=None,
):
    global _WORLD_BOSS_MINIAPP_TASK
    if _world_boss_miniapp_task_running():
        return False
    _WORLD_BOSS_MINIAPP_TASK = asyncio.create_task(
        _run_world_boss_miniapp_automation(
            event_key,
            identity_ids,
            event,
            text,
            opened_at,
            account_gap_sec,
            window_skip_by_identity,
        )
    )
    return True


async def _notify_world_boss_open_only(parsed, now, current_msg_id=0, *, event=None, text=""):
    run_state = _get_run_state(now)
    event_key = _open_event_key(parsed, now, current_msg_id=current_msg_id)
    if run_state.get("last_open_log_key") == event_key:
        return True
    _clear_world_boss_pending_tasks()
    _reset_all_identity_event_state(persist=False)
    run_state["active"] = False
    run_state["event_key"] = event_key
    run_state["opened_at"] = float(now)
    run_state["closed_at"] = 0
    run_state["miniapp_only"] = bool(parsed.get("miniapp_only"))
    auto_config = _world_boss_miniapp_auto_config()
    entry_identity_ids = select_world_boss_miniapp_entry_identities()
    auto_identity_ids = [
        identity_id
        for identity_id in entry_identity_ids
        if identity_id not in auto_config["excluded_identity_ids"]
    ][: auto_config["account_limit"]]
    run_state["miniapp_entry_identity_ids"] = entry_identity_ids if run_state["miniapp_only"] else []
    run_state["last_open_log_key"] = event_key
    run_state["last_result"] = "小程序开打提醒" if run_state["miniapp_only"] else "未启用提醒"
    run_state["miniapp_auto_status"] = "disabled"
    run_state["miniapp_auto_started_at"] = 0
    run_state["miniapp_auto_finished_at"] = 0
    run_state["miniapp_auto_progress"] = []
    run_state["miniapp_auto_results"] = []
    run_state["miniapp_conclusion_evidence"] = {}
    run_state["next_status_query_at"] = 0
    run_state["next_action_at"] = 0
    if run_state["miniapp_only"]:
        labels = "、".join(_identity_label(identity_id) for identity_id in entry_identity_ids) or "无"
        launch = extract_world_boss_miniapp_launch(event, message_text=text) if event is not None else {}
        if auto_config["enabled"] and launch and auto_identity_ids:
            run_state["miniapp_auto_status"] = "running"
            run_state["miniapp_auto_started_at"] = float(now)
            run_state["last_result"] = "MiniApp 自动执行中"
            started = _start_world_boss_miniapp_automation(
                event_key,
                auto_identity_ids,
                event,
                text,
                now,
                0,
                auto_config["window_skip_by_identity"],
            )
            if started:
                message = (
                    f"🗡 真仙试锋小程序（MiniApp）自动参与已启动：{'、'.join(_identity_label(identity_id) for identity_id in auto_identity_ids)}"
                    f"\n{len(auto_identity_ids)} 个登录账户并行入场并各自运行战斗时间线；单账户内部请求保持串行。"
                )
                if WORLD_BOSS_MINIAPP_FINISH_RESERVE_WINDOWS:
                    message += (
                        f"统一预留尾部 {WORLD_BOSS_MINIAPP_FINISH_RESERVE_WINDOWS} 个窗口用于提前结算。"
                    )
            else:
                message = "🗡 真仙试锋 MiniApp 已有事件任务运行，本次广播仅去重记录。"
        elif auto_config["enabled"] and not launch:
            run_state["miniapp_auto_status"] = "entry_missing"
            message = "🗡 真仙试锋已开打，但广播中未提取到有效 MiniApp 入口，已保守停止自动参与。"
        else:
            message = (
                "🗡 真仙试锋已开打：小程序（MiniApp）自动参与当前关闭，不会自动入场。"
                f"\n候选按登录账号去重，最多 {WORLD_BOSS_MINIAPP_ACCOUNT_LIMIT} 个：{labels}"
            )
    else:
        message = "🗡 真仙试锋已开打：当前没有启用身份，脚本不出手。"
    await send_audit_log(
        message,
        scope="global",
        priority="high",
        limit=260,
    )
    _set_run_state(run_state, now=now)
    return True


def _local_world_boss_usernames():
    usernames = {}
    for identity_id in get_identity_ids():
        profile = _identity_profile(identity_id)
        aliases = WORLD_BOSS_LOCAL_USERNAME_ALIASES.get(int(identity_id), ())
        for value in (profile.get("username"), profile.get("label"), *aliases):
            username = str(value or "").strip().lstrip("@")
            if username:
                usernames.setdefault(username.lower(), int(identity_id))
    return usernames


def _world_boss_conclusion_evidence(parsed):
    username_map = _local_world_boss_usernames()
    evidence = {}
    for item in parsed.get("contributions") or ():
        identity_id = username_map.get(str(item.get("username") or "").strip().lower(), 0)
        if identity_id <= 0:
            continue
        record = evidence.setdefault(str(identity_id), {"identity_id": identity_id})
        record.update({
            "rank": _coerce_int(item.get("rank"), 0),
            "score": _coerce_int(item.get("score"), 0),
            "attacks": _coerce_int(item.get("attacks"), 0),
            "damage": str(item.get("damage") or "").strip(),
        })
    for item in parsed.get("settlements") or ():
        identity_id = username_map.get(str(item.get("username") or "").strip().lower(), 0)
        if identity_id <= 0:
            continue
        evidence.setdefault(str(identity_id), {"identity_id": identity_id})["rewards"] = _short_text(
            item.get("rewards") or "",
            160,
        )
    return evidence


def _reconcile_world_boss_miniapp_results(run_state, evidence=None):
    evidence = evidence if isinstance(evidence, dict) else run_state.get("miniapp_conclusion_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    if not evidence:
        return 0
    confirmed_ids = set()
    for field in ("miniapp_auto_results", "miniapp_auto_progress"):
        reconciled = []
        for raw_item in run_state.get(field) or ():
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            identity_key = str(_coerce_int(item.get("identity_id"), 0))
            if (
                item.get("phase") == "battle"
                and item.get("status") == "event_closed_partial"
                and identity_key in evidence
            ):
                summary = dict(item.get("summary") or {}) if isinstance(item.get("summary"), dict) else {}
                summary["conclusion_confirmed"] = True
                summary["conclusion"] = dict(evidence[identity_key])
                item.update({
                    "ok": True,
                    "status": "conclusion_confirmed",
                    "error": "",
                    "summary": summary,
                })
                confirmed_ids.add(identity_key)
            reconciled.append(item)
        run_state[field] = reconciled
    if confirmed_ids and str(run_state.get("miniapp_auto_status") or "").lower() == "partial":
        battle_results = [
            item
            for item in run_state.get("miniapp_auto_results") or ()
            if isinstance(item, dict) and item.get("phase") == "battle"
        ]
        if battle_results and all(bool(item.get("ok")) for item in battle_results):
            run_state["miniapp_auto_status"] = "conclusion_confirmed"
    return len(confirmed_ids)


def _advance_world_boss_rotations(parsed, now, conclusion_key):
    config = _rotation_config()
    if not config["account_ids"]:
        return []
    rotation_state = _normalized_rotation_state()
    if conclusion_key and rotation_state.get("last_conclusion_key") == conclusion_key:
        return []
    username_map = _local_world_boss_usernames()
    completion_evidence = []
    seen_usernames = set()
    for drop in parsed.get("rare_drops") or ():
        reward = str(drop.get("reward") or "").strip()
        if config["target_reward"] not in _normalize_rotation_target_reward(reward):
            continue
        username = str(drop.get("username") or "").strip().lower()
        if username and username not in seen_usernames:
            completion_evidence.append({"username": username, "reward": reward})
            seen_usernames.add(username)
    if config["target_reward"] == WORLD_BOSS_ROTATION_DEFAULT_REWARD:
        for contribution in parsed.get("contributions") or ():
            if _coerce_int(contribution.get("rank"), 0) != 1:
                continue
            username = str(contribution.get("username") or "").strip().lower()
            if username and username not in seen_usernames:
                completion_evidence.append({
                    "username": username,
                    "reward": f"第1名（{WORLD_BOSS_ROTATION_DEFAULT_REWARD}）",
                })
                seen_usernames.add(username)
    advanced = []
    rotation_accounts = set(config["account_ids"])
    for evidence in completion_evidence:
        reward = str(evidence.get("reward") or "").strip()
        identity_id = username_map.get(str(evidence.get("username") or "").strip().lower(), 0)
        account_id = int(get_identity_account(identity_id) or 0)
        if identity_id <= 0 or account_id not in rotation_accounts:
            continue
        record = _rotation_account_record(account_id)
        if int(record.get("current_identity_id") or 0) != identity_id:
            continue
        completed_ids = set(record.get("completed_identity_ids") or ())
        if identity_id in completed_ids:
            continue
        completed_ids.add(identity_id)
        record["completed_identity_ids"] = sorted(completed_ids)
        record["last_completed_identity_id"] = identity_id
        record["last_completed_at"] = float(now)
        record["last_reward"] = reward
        record["current_identity_id"] = next(
            (candidate for candidate in record.get("identity_ids") or () if candidate not in completed_ids),
            0,
        )
        _save_rotation_account_record(record, conclusion_key=conclusion_key)
        advanced.append(record)
    return advanced


def _format_local_world_boss_result(parsed):
    username_map = _local_world_boss_usernames()
    if not username_map:
        return ""
    contributions = []
    for item in parsed.get("contributions") or []:
        username = str(item.get("username") or "").strip()
        identity_id = username_map.get(username.lower())
        if not identity_id:
            continue
        label = _identity_label(identity_id)
        detail = f"{label} {item.get('score', 0)}分"
        attacks = _coerce_int(item.get("attacks"), 0)
        if attacks:
            detail += f"｜强攻{attacks}"
        damage = str(item.get("damage") or "").strip()
        if damage:
            detail += f"｜伤害{damage}"
        rank = _coerce_int(item.get("rank"), 0)
        if rank:
            detail += f"｜第{rank}"
        contributions.append(detail)
    reward_by_user = {}
    for item in parsed.get("settlements") or []:
        username = str(item.get("username") or "").strip()
        identity_id = username_map.get(username.lower())
        if identity_id:
            reward_by_user[_identity_label(identity_id)] = str(item.get("rewards") or "").strip()
    if not contributions and not reward_by_user:
        return ""
    lines = []
    if contributions:
        lines.append("本方上榜：" + "；".join(contributions))
    if reward_by_user:
        reward_text = "；".join(f"{label}：{reward}" for label, reward in reward_by_user.items() if reward)
        if reward_text:
            lines.append("保底收获：" + reward_text)
    return "\n".join(lines)


def _miniapp_confirmed_contribution(run_state):
    identities = 0
    hits = 0
    perfects = 0
    damage_yi = 0.0
    for item in (run_state or {}).get("miniapp_auto_results") or ():
        if not isinstance(item, dict) or item.get("phase") != "battle":
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        item_hits = max(
            _coerce_int(summary.get("realtime_hit_count"), 0),
            _coerce_int(summary.get("hits"), 0),
            _coerce_int(summary.get("accepted_hit_count"), 0),
        )
        item_perfects = max(
            _coerce_int(summary.get("perfects"), 0),
            _coerce_int(summary.get("accepted_perfect_count"), 0),
        )
        item_damage = max(
            _coerce_float(summary.get("realtime_damage_yi"), 0),
            _coerce_float(summary.get("accepted_damage_yi"), 0),
        )
        if item_hits > 0 or item_damage > 0:
            identities += 1
        hits += item_hits
        perfects += item_perfects
        damage_yi += item_damage
    return {
        "identities": identities,
        "hits": hits,
        "perfects": perfects,
        "damage_yi": damage_yi,
    }


def _start_world_boss_round_if_ready(now):
    run_state = _get_run_state(now)
    if (
        run_state.get("active")
        and _status_is_fresh(run_state, now)
        and float(now) >= _coerce_float(run_state.get("next_action_at"), 0)
        and not _world_boss_round_task_running()
    ):
        _start_world_boss_round_task(now)


async def _close_event(parsed, now, *, log=True):
    run_state = _get_run_state(now)
    event_key = str(run_state.get("event_key") or "")
    result = str(parsed.get("result") or "结束").strip()
    conclusion_key = str(parsed.get("key") or result or get_day_key(now))
    conclusion_evidence = _world_boss_conclusion_evidence(parsed)
    run_state["miniapp_conclusion_evidence"] = conclusion_evidence
    _reconcile_world_boss_miniapp_results(run_state, conclusion_evidence)
    rotation_advances = _advance_world_boss_rotations(parsed, now, conclusion_key)
    duplicate = conclusion_key and conclusion_key == run_state.get("last_conclusion_key") and now - _coerce_float(run_state.get("last_conclusion_at"), 0) < 2 * 3600
    run_state["active"] = False
    run_state["closed_at"] = float(now)
    run_state["last_result"] = result
    run_state["last_conclusion_key"] = conclusion_key
    run_state["last_conclusion_at"] = float(now)
    participants = _coerce_int(parsed.get("participants"), 0)
    if parsed.get("participants_present") or participants > 0:
        run_state["participants"] = participants
    for identity_id in get_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        _clear_world_boss_pending_action(identity_state)
    _clear_world_boss_pending_tasks()
    # Persist the terminal event boundary before awaiting log delivery. The
    # MiniApp task can finish while the log-group send is queued; keeping this
    # only in a local snapshot lets the later stale write erase its results.
    _set_run_state(run_state, now=now)
    if log and not duplicate:
        await _maybe_log_progress(run_state, now, force=True)
        summary = _normalize_summary(run_state.get("summary"))
        local_result = _format_local_world_boss_result(parsed)
        participant_display = (
            run_state.get("participants")
            if parsed.get("participants_present") or _coerce_int(run_state.get("participants"), 0) > 0
            else "未知"
        )
        base = (
            f"🗡 真仙试锋{result}：参战 {participant_display}"
            f"｜命令链确认 破幡 {summary['破幡']} / 镇魂 {summary['镇魂']} / 护阵 {summary['护阵']} / 强攻 {summary['强攻']}。"
        )
        miniapp_contribution = _miniapp_confirmed_contribution(run_state)
        if miniapp_contribution["hits"] or miniapp_contribution["damage_yi"]:
            base += (
                f"\nMiniApp确认：{miniapp_contribution['identities']} 身份｜有效强攻 {miniapp_contribution['hits']}"
                f"｜完美 {miniapp_contribution['perfects']}｜伤害 {miniapp_contribution['damage_yi']:g}亿。"
            )
        if local_result:
            base += "\n" + local_result
        if rotation_advances:
            rotation_text = []
            for record in rotation_advances:
                completed_id = int(record.get("last_completed_identity_id") or 0)
                next_id = int(record.get("current_identity_id") or 0)
                detail = f"{_identity_label(completed_id)} 已获目标奖励"
                detail += f"，下场切换 {_identity_label(next_id)}" if next_id else "，该账户轮换完成"
                rotation_text.append(detail)
            base += "\n身份轮换：" + "；".join(rotation_text)
        await send_audit_log(
            base,
            scope="global",
            priority="medium",
            limit=700,
        )
    latest_state = _get_run_state(now)
    if str(latest_state.get("event_key") or "") == event_key:
        latest_state["miniapp_conclusion_evidence"] = conclusion_evidence
        _reconcile_world_boss_miniapp_results(latest_state, conclusion_evidence)
        latest_state["active"] = False
        latest_state["closed_at"] = max(_coerce_float(latest_state.get("closed_at"), 0), float(now))
        latest_state["last_result"] = result
        latest_state["last_conclusion_key"] = conclusion_key
        latest_state["last_conclusion_at"] = float(now)
        if parsed.get("participants_present") or participants > 0:
            latest_state["participants"] = participants
        _set_run_state(latest_state, now=now)
    return True


async def _mark_inactive(now):
    run_state = _get_run_state(now)
    if run_state.get("active"):
        run_state["active"] = False
        run_state["closed_at"] = float(now)
        run_state["last_result"] = run_state.get("last_result") or "已结束"
    _mark_recovery_probe_attempt(run_state, now)
    run_state["next_status_query_at"] = 0
    run_state["next_action_at"] = 0
    for identity_id in get_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        _clear_world_boss_pending_action(identity_state)
    _clear_world_boss_pending_tasks()
    _set_run_state(run_state, now=now)
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
        _clear_world_boss_pending_tasks()
        _reset_all_identity_event_state(persist=False)
        run_state = _blank_run_state(now)
        run_state["active"] = True
        run_state["event_key"] = _status_event_key(now, current_msg_id=current_msg_id)
        run_state["opened_at"] = float(now)
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
    _maybe_interrupt_round_for_priority_window(run_state, now)
    _set_run_state(run_state, now=now)
    _start_world_boss_round_if_ready(now)
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
    _maybe_interrupt_round_for_priority_window(run_state, now)
    _set_run_state(run_state, now=now)
    _start_world_boss_round_if_ready(now)
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
    run_state = _get_run_state(now)
    if _miniapp_only_event_recent(run_state, now):
        if parsed_type == "open":
            return await _notify_world_boss_open_only(parsed, now, current_msg_id=event_id)
        if parsed_type == "conclusion":
            return await _close_event(parsed, now)
        if parsed_type == "inactive":
            return await _mark_inactive(now)
        return True
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
    run_state = _get_run_state(now)
    if _miniapp_only_event_recent(run_state, now):
        if parsed_type == "open":
            return await _notify_world_boss_open_only(parsed, now, current_msg_id=event_id, event=event, text=text)
        if parsed_type == "conclusion":
            return await _close_event(parsed, now)
        if parsed_type == "inactive":
            return await _mark_inactive(now)
        # MiniApp status/action broadcasts are useful evidence, but they must
        # not resurrect the deprecated text-command action chain.
        return True
    if not enabled:
        if parsed_type == "open":
            return await _notify_world_boss_open_only(parsed, now, current_msg_id=event_id, event=event, text=text)
        if parsed_type == "conclusion" and (run_state.get("active") or _miniapp_only_event_recent(run_state, now)):
            return await _close_event(parsed, now, log=False)
        if parsed_type == "inactive" and run_state.get("active"):
            return await _mark_inactive(now)
        return False
    if parsed_type == "open":
        if bool(parsed.get("miniapp_only")):
            return await _notify_world_boss_open_only(parsed, now, current_msg_id=event_id, event=event, text=text)
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


async def _send_status_query(identity_id, now, run_state, reason, *, allow_inactive_probe=False):
    recovery_probe = bool(allow_inactive_probe and not run_state.get("active"))
    if not recovery_probe and not _status_retry_allowed(run_state, now):
        _clear_all_world_boss_pending("战况查询已过期")
        run_state["next_status_query_at"] = 0
        run_state["next_action_at"] = 0
        _set_run_state(run_state, persist=False, now=now)
        return False
    chain_id = _event_chain_id(run_state, now)
    try:
        identity_state = get_identity_state(identity_id)
    except KeyError:
        identity_state = None
    is_retry = bool(identity_state is not None and _has_pending_world_boss_status(identity_state))
    retry_count = _coerce_int(identity_state.get("world_boss_pending_retry_count"), 0) + 1 if is_retry and identity_state is not None else 0
    if is_retry and retry_count > WORLD_BOSS_STATUS_MAX_RETRIES:
        if identity_state is not None:
            _clear_world_boss_pending_action(identity_state)
            identity_state["world_boss_last_error"] = "战况查询无回复，补查已达上限"
        clear_pending_tasks_by_commands({WORLD_BOSS_STATUS_QUERY_COMMAND}, send_as_id=identity_id)
        if recovery_probe:
            _mark_recovery_probe_attempt(run_state, now)
            run_state["next_status_query_at"] = 0
            run_state["next_action_at"] = 0
        else:
            run_state["next_status_query_at"] = float(now) + WORLD_BOSS_STATUS_QUERY_GAP_SEC
            run_state["next_action_at"] = max(
                _coerce_float(run_state.get("next_action_at"), 0),
                run_state["next_status_query_at"],
            )
        _set_run_state(run_state, now=now)
        console_log(
            f"🗡 真仙试锋[{_identity_label(identity_id)}] 战况查询无回复，补查已达上限，暂停本轮战况补查。",
            scope="identity",
            send_as_id=identity_id,
            limit=180,
        )
        return False
    if is_retry:
        clear_pending_tasks_by_commands({WORLD_BOSS_STATUS_QUERY_COMMAND}, send_as_id=identity_id)
    msg = await send_game_command(
        WORLD_BOSS_STATUS_QUERY_COMMAND,
        track=True,
        max_retry=0,
        reply_timeout=WORLD_BOSS_REPLY_TIMEOUT_SEC,
        send_as_id=identity_id,
        priority=WORLD_BOSS_STATUS_PRIORITY,
        source_module=WORLD_BOSS_MODULE_NAME,
        op_id=f"{chain_id}:status:{identity_id}:try{retry_count}:{int(now)}",
        chain_id=chain_id,
    )
    sent_at = _coerce_float(getattr(msg, "sent_at", 0), 0) or time.time()
    if recovery_probe:
        _mark_recovery_probe_attempt(run_state, sent_at)
    if not msg:
        if identity_state is not None:
            identity_state["world_boss_last_error"] = f"{reason}战况查询发送失败"
        run_state["next_status_query_at"] = sent_at + WORLD_BOSS_STATUS_PENDING_TIMEOUT_SEC
        if recovery_probe:
            run_state["next_action_at"] = 0
        _set_run_state(run_state, now=now)
        return False
    if identity_state is not None:
        if not _has_pending_world_boss_action(identity_state):
            identity_state["world_boss_pending_msg_id"] = int(getattr(msg, "id", 0) or 0)
            identity_state["world_boss_pending_action"] = "status"
            identity_state["world_boss_pending_since"] = sent_at
            identity_state["world_boss_pending_retry_count"] = retry_count
            identity_state["world_boss_pending_action_seq"] = 0
        identity_state["world_boss_last_error"] = ""
    run_state["next_status_query_at"] = sent_at + WORLD_BOSS_STATUS_PENDING_TIMEOUT_SEC
    if recovery_probe:
        run_state["next_action_at"] = 0
    else:
        run_state["next_action_at"] = max(_coerce_float(run_state.get("next_action_at"), 0), sent_at + WORLD_BOSS_STATUS_AFTER_QUERY_GAP_SEC)
    _set_run_state(run_state, now=now)
    if is_retry:
        console_log(
            f"🗡 真仙试锋[{_identity_label(identity_id)}] 战况查询无回复，已补查{retry_count}。",
            scope="identity",
            send_as_id=identity_id,
            limit=180,
        )
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
        _set_run_state(run_state, now=now)
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
        _set_run_state(latest_run_state, now=now)
        return sent_at
    run_state = latest_run_state
    identity_state["world_boss_last_action"] = action
    identity_state["world_boss_last_sent_at"] = sent_at
    identity_state["world_boss_last_error"] = ""
    if not is_retry:
        identity_state["world_boss_action_count"] = action_seq
    if action == "强攻" and not is_retry:
        identity_state["world_boss_attack_count"] = _coerce_int(identity_state.get("world_boss_attack_count"), 0) + 1
    run_state["last_action_at"] = sent_at
    run_state["next_action_at"] = sent_at + (WORLD_BOSS_PENDING_TIMEOUT_SEC if is_retry else WORLD_BOSS_ACTION_GAP_SEC)
    _set_run_state(run_state, now=now)
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
            is_pending_retry = _coerce_int(identity_state.get("world_boss_pending_msg_id"), 0) > 0
            if is_pending_retry and await _recover_world_boss_pending_from_message_log(identity_id, identity_state, current_now):
                _schedule_next_world_boss_action(_get_run_state(current_now), current_now)
                return
            if is_pending_retry and sent_count > 0 and allow_new_actions:
                _complete_world_boss_round(run_state, current_now)
                return
            sent_at = await _send_action(identity_id, identity_state, action, current_now, run_state)
            if not sent_at:
                return
            sent_count += 1
            current_now = max(current_now, _coerce_float(sent_at, current_now))
            if is_pending_retry:
                _schedule_next_world_boss_action(_get_run_state(current_now), current_now)
                return
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


async def run_world_boss_scheduler(now):
    if _WORLD_BOSS_SCHEDULER_LOCK.locked():
        return
    async with _WORLD_BOSS_SCHEDULER_LOCK:
        now = float(now or time.time())
        run_state = _get_run_state(now)
        if (
            run_state.get("miniapp_only")
            and run_state.get("miniapp_auto_status") == "running"
            and _coerce_float(run_state.get("miniapp_auto_started_at"), 0) > 0
            and not _world_boss_miniapp_task_running()
        ):
            run_state["miniapp_auto_status"] = "interrupted"
            run_state["miniapp_auto_finished_at"] = now
            run_state["last_result"] = "MiniApp 任务中断，禁止自动续跑"
            _set_run_state(run_state, now=now)
            await send_audit_log(
                "🗡 真仙试锋 MiniApp 任务检测到服务中断，已保守停止本场自动续跑；请按脱敏抓包与进度账本复核，避免重复入场或重复结算。",
                scope="global",
                priority="high",
                limit=320,
            )
            return
        enabled_ids = _enabled_identity_ids()
        if not enabled_ids:
            if run_state != get_world_boss_run_state():
                _set_run_state(run_state, persist=False, now=now)
            return

        if not run_state.get("active"):
            if _clear_stale_inactive_event_pending(run_state, now):
                return
            if _has_any_pending_status():
                if _recovery_probe_pending(run_state, now):
                    if not _has_due_pending_status(now):
                        _set_run_state(run_state, persist=False, now=now)
                        return
                    status_identity_id, _identity_state = _pending_status_identity(now)
                    if status_identity_id:
                        if _identity_state is not None and await _recover_world_boss_pending_from_message_log(status_identity_id, _identity_state, now):
                            _set_run_state(_get_run_state(now), persist=False, now=now)
                            return
                        await _send_status_query(
                            status_identity_id,
                            now,
                            run_state,
                            "服务恢复探测无回复",
                            allow_inactive_probe=True,
                        )
                    else:
                        _set_run_state(run_state, persist=False, now=now)
                    return
                _clear_all_world_boss_pending("未观测到进行中事件，停止战况补查")
                run_state["next_status_query_at"] = 0
                run_state["next_action_at"] = 0
                _set_run_state(run_state, persist=False, now=now)
                return
            if _clear_inactive_world_boss_status_residue(run_state, now):
                return
            if _recovery_probe_due(run_state, now):
                await _send_status_query(enabled_ids[0], now, run_state, "服务恢复探测", allow_inactive_probe=True)
                return
            _set_run_state(run_state, persist=False, now=now)
            return

        if run_state.get("remaining_sec", 0) <= 0 and run_state.get("last_status_at", 0) > 0 and now - float(run_state.get("last_status_at") or 0) > 120:
            run_state["active"] = False
            run_state["closed_at"] = now
            run_state["last_result"] = run_state.get("last_result") or "等待结算"
            _set_run_state(run_state, now=now)
            return

        if not _status_is_fresh(run_state, now):
            if _has_any_pending_action():
                _schedule_next_world_boss_action(run_state, now)
                if not _has_due_pending_action(now):
                    return
                if not _world_boss_round_task_running():
                    _start_world_boss_round_task(now)
                return
            if _has_any_pending_status():
                if not _status_retry_allowed(run_state, now):
                    _clear_all_world_boss_pending("战况查询已过期")
                    _set_run_state(run_state, persist=False, now=now)
                    return
                if not _has_due_pending_status(now):
                    _schedule_next_world_boss_action(run_state, now)
                    return
                status_identity_id, _identity_state = _pending_status_identity(now)
                if status_identity_id:
                    if _identity_state is not None and await _recover_world_boss_pending_from_message_log(status_identity_id, _identity_state, now):
                        _schedule_next_world_boss_action(_get_run_state(now), now)
                        return
                    await _send_status_query(status_identity_id, now, run_state, "战况查询无回复")
                else:
                    _set_run_state(run_state, persist=False, now=now)
                return
            if now >= _coerce_float(run_state.get("next_status_query_at"), 0):
                await _send_status_query(enabled_ids[0], now, run_state, "战况过期")
            else:
                _set_run_state(run_state, persist=False, now=now)
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
        f"- 全局确认：破幡 {summary['破幡']}｜镇魂 {summary['镇魂']}｜护阵 {summary['护阵']}｜强攻 {summary['强攻']}",
        f"- 待回复：{pending_text}",
        f"- 下次动作：{fmt_abs_ts(next_action_at)}（{fmt_remaining(next_action_at)}）",
        f"- 最近错误：{last_error}",
    ]
    return "\n".join(lines)


__all__ = [
    "choose_world_boss_action",
    "clear_world_boss_identity_state",
    "get_world_boss_rotation_account_record",
    "get_world_boss_status_text",
    "handle_world_boss_broadcast",
    "handle_world_boss_reply",
    "looks_like_world_boss_text",
    "parse_world_boss_text",
    "run_world_boss_scheduler",
    "select_world_boss_miniapp_entry_identities",
]
