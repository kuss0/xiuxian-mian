import hashlib
import random
import re
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from ..config import (
    CD_BUFFER_SEC,
    CMD_MULAN_COLLECT,
    CMD_MULAN_JUDGE,
    CMD_MULAN_PUBLISH,
    CMD_MULAN_SHADOW,
    CMD_MULAN_SUPPORT,
    CMD_MULAN_WAR_PANEL,
    MULAN_CD,
    MULAN_JITTER_MAX_SEC,
    MULAN_JITTER_MIN_SEC,
    MULAN_REPLY_TIMEOUT_SEC,
    RETRY_MAX_SEC,
    TZ_LOCAL,
)
from ..action_guard import close_action as close_action_guard_action
from ..message_log_recovery import find_message_log_replies
from ..persistence import mark_dirty, save_state
from ..runtime import classify_game_send_block, console_log, get_sent_message_chat_id, mark_bot_health_suspect, send_audit_log, send_game_command, was_last_game_send_blocked_by_global
from ..state import _meta_state, get_current_identity_id, get_game_group_id, get_identity_account, has_identity, state
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from ._phaseful import get_phaseful_summary_risk_reason


MULAN_DEFAULT_IDS = (1, 2, 3)
MULAN_INITIAL_WINDOW_LEAD_SEC = 5 * 60
MULAN_INITIAL_WINDOW_END_MARGIN_SEC = 60
MULAN_LEGACY_MIDNIGHT_REBUCKET_MINUTE = 30
MULAN_INITIAL_BATCH_WINDOWS = (
    {"key": "wave1", "start_hour": 1, "start_minute": 0, "end_hour": 4, "end_minute": 0},
    {"key": "wave2", "start_hour": 5, "start_minute": 0, "end_hour": 8, "end_minute": 0},
)
MULAN_PHASEFUL_DEFER_MIN_SEC = 5 * 60
MULAN_PHASEFUL_DEFER_MAX_SEC = 10 * 60
MULAN_SEND_QUEUE_TIMEOUT_SEC = 90
MULAN_CRITICAL_SEND_QUEUE_TIMEOUT_SEC = 120
MULAN_SEND_QUEUE_RETRY_MIN_SEC = 10 * 60
MULAN_SEND_QUEUE_RETRY_MAX_SEC = 20 * 60
MULAN_SEND_UNKNOWN_BACKOFF_SEC = 10 * 60
MULAN_GLOBAL_SEND_GAP_MIN_SEC = 45
MULAN_GLOBAL_SEND_GAP_MAX_SEC = 75
MULAN_GLOBAL_DEFER_JITTER_MIN_SEC = 5
MULAN_GLOBAL_DEFER_JITTER_MAX_SEC = 30
MULAN_GLOBAL_SEND_GATE_KEY = "mulan_global_send_gate"
MULAN_PHASE_IDLE = "idle"
MULAN_PHASE_COLLECT_PENDING = "collect_pending"
MULAN_PHASE_READY_TO_JUDGE = "ready_to_judge"
MULAN_PHASE_JUDGE_PENDING = "judge_pending"
MULAN_PHASE_READY_TO_PUBLISH = "ready_to_publish"
MULAN_PHASE_PUBLISH_PENDING = "publish_pending"
MULAN_PHASE_READY_TO_PANEL = "ready_to_panel"
MULAN_PHASE_PANEL_PENDING = "panel_pending"
MULAN_PHASE_READY_TO_SUPPORT = "ready_to_support"
MULAN_PHASE_SUPPORT_PENDING = "support_pending"
MULAN_PHASE_COOLDOWN = "cooldown"
MULAN_PENDING_PHASES = {
    MULAN_PHASE_COLLECT_PENDING,
    MULAN_PHASE_JUDGE_PENDING,
    MULAN_PHASE_PUBLISH_PENDING,
    MULAN_PHASE_PANEL_PENDING,
    MULAN_PHASE_SUPPORT_PENDING,
}
MULAN_READY_PHASES = {
    MULAN_PHASE_READY_TO_JUDGE,
    MULAN_PHASE_READY_TO_PUBLISH,
    MULAN_PHASE_READY_TO_PANEL,
    MULAN_PHASE_READY_TO_SUPPORT,
}

RE_REPORT_ID = re.compile(r"(?:军报|情报|编号|第)?\s*([1-9]\d*)\s*(?:号|：|:|、|\.|．|\)|）)")
RE_COMMAND_ID = re.compile(r"^\.辨报\s+([1-9]\d*)")
RE_PUBLISH_COMMAND_ID = re.compile(r"^\.公开军报\s+([1-9]\d*)")
RE_REPORT_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:编号|第)?\s*([1-9]\d*)\s*(?:号|[.、:：．\)）])\s*(.+?)\s*$")
RE_TRUE_REPORT_TEXT = re.compile(r"前线采信了你的军报[:：]\s*(.+?)(?:\n|$)")
RE_SUPPORT_ROUTE = re.compile(r"今日支援【([^】]+)】")
RE_MILITARY_ORDER = re.compile(r"军议密令[:：]\s*([^，。\n]+)")

MULAN_SUPPORT_ROUTE_TO_ACTION = {
    "斥候探草原": "斥候",
    "破慕兰圣灯": "破灯",
    "固守边境法阵": "护阵",
    "夜袭法士营": "奇袭",
}
MULAN_SUPPORT_ACTIONS = ("斥候", "破灯", "护阵", "奇袭")
MULAN_FIXED_REPORTS = {
    report_text: (verdict, action)
    for report_text, verdict, action in (
        ("今夜圣灯换焰，主灯会短暂离开护灯法士三十息", "reliable", "破灯"),
        ("边境粮道将过西岭，阵师缺人护送一批阵旗", "reliable", "护阵"),
        ("法士营北帐换防，附灵蛇胆与妖丹暂存在同一灵袋", "reliable", "奇袭"),
        ("有小股法士借草沟绕行，似在寻找黄龙山外阵缺口", "reliable", "斥候"),
        ("黄龙阵旗已全部撤回，护阵路线今日无事", "suspicious", ""),
        ("圣灯已熄，只需正面冲阵便可夺灯", "suspicious", ""),
        ("慕兰主力已退三百里，草原前线今日几乎无兵", "suspicious", ""),
        ("南营无人防守，所有法士都在主帐议事", "suspicious", ""),
    )
}

RELIABLE_KEYWORDS = (
    "可靠性较高",
    "研判较高",
    "可信度较高",
    "可信较高",
    "情报可靠",
    "较为可靠",
    "确认为真",
    "确认属实",
    "基本属实",
    "可以公开",
    "可公开",
    "较高",
    "可靠",
    "可信",
    "属实",
    "准确",
    "无误",
)
SUSPICIOUS_KEYWORDS = (
    "研判可疑",
    "情报可疑",
    "明显可疑",
    "存疑",
    "虚假",
    "不可靠",
    "可靠性低",
    "可信度低",
    "疑点",
    "伪报",
    "误报",
    "可疑",
)
CD_KEYWORDS = ("尚未", "冷却", "稍后", "后再", "不可频繁")
DONE_KEYWORDS = (
    "今日已",
    "今天已",
    "本日已",
    "已经提交",
    "已提交",
    "已经公开",
    "已公开",
    "不可重复",
    "无需重复",
    "莫要重复",
    "没有可公开",
    "暂无可公开",
)
SUPPORT_DONE_KEYWORDS = (
    "今日状态：已支援",
    "今日状态: 已支援",
    "今日已支援",
    "已经支援",
    "已支援过",
)
SUPPORT_RESULT_KEYWORDS = (
    "【慕兰烽烟 ·",
    "获得修为",
    "获得灵石",
    "边境军功",
    "连续支援",
)
LIMITED_JUDGE_KEYWORDS = (
    "辨报受限",
    "今日神识只够细辨一条军报",
    "剩余消息只能凭文本线索自行判断",
)
FALSE_PUBLISH_KEYWORDS = (
    "未被采信",
    "不予采信",
    "前线未采信",
    "军报有误",
    "假报",
    "伪报",
    "误导前线",
)


def _parse_int(value, default=0):
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _encode_ids(ids):
    normalized = []
    seen = set()
    for value in ids or ():
        report_id = _parse_int(value)
        if report_id <= 0 or report_id in seen:
            continue
        seen.add(report_id)
        normalized.append(str(report_id))
    return ",".join(normalized)


def _decode_ids(value):
    ids = []
    seen = set()
    for part in re.split(r"[,，\s]+", str(value or "").strip()):
        report_id = _parse_int(part)
        if report_id <= 0 or report_id in seen:
            continue
        seen.add(report_id)
        ids.append(report_id)
    return ids


def _mulan_day_key(now):
    return datetime.fromtimestamp(float(now or time.time()), TZ_LOCAL).strftime("%Y-%m-%d")


def _mulan_window_bounds(local_now, window, *, day_offset=0):
    base = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
    start = base + timedelta(hours=int(window["start_hour"]), minutes=int(window.get("start_minute", 0) or 0))
    end = base + timedelta(hours=int(window["end_hour"]), minutes=int(window.get("end_minute", 0) or 0))
    if end <= start:
        end += timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _mulan_initial_window_index(identity_id):
    try:
        return abs(int(identity_id or 0)) % len(MULAN_INITIAL_BATCH_WINDOWS)
    except (TypeError, ValueError, OverflowError):
        return 0


def _mulan_initial_batch_time(now, identity_id=None):
    now = float(now or time.time())
    identity_id = int(identity_id or get_current_identity_id() or 0)
    local_now = datetime.fromtimestamp(now, TZ_LOCAL)
    assigned_index = _mulan_initial_window_index(identity_id)
    today_bounds = [
        _mulan_window_bounds(local_now, window)
        for window in MULAN_INITIAL_BATCH_WINDOWS
    ]
    assigned_start, assigned_end = today_bounds[assigned_index]
    if now < assigned_end:
        start_ts, end_ts = assigned_start, assigned_end
    else:
        future_today = [(start_ts, end_ts) for start_ts, end_ts in today_bounds if now < end_ts]
        if future_today:
            start_ts, end_ts = future_today[0]
        else:
            start_ts, end_ts = _mulan_window_bounds(local_now, MULAN_INITIAL_BATCH_WINDOWS[assigned_index], day_offset=1)

    earliest = max(now + MULAN_INITIAL_WINDOW_LEAD_SEC, start_ts)
    latest = end_ts - MULAN_INITIAL_WINDOW_END_MARGIN_SEC
    if latest <= earliest:
        return float(earliest)
    return float(random.uniform(earliest, latest))


def _mulan_next_daily_batch_time(now, identity_id=None):
    now = float(now or time.time())
    identity_id = int(identity_id or get_current_identity_id() or 0)
    local_now = datetime.fromtimestamp(now, TZ_LOCAL)
    assigned_index = _mulan_initial_window_index(identity_id)
    start_ts, end_ts = _mulan_window_bounds(
        local_now,
        MULAN_INITIAL_BATCH_WINDOWS[assigned_index],
        day_offset=1,
    )
    latest = end_ts - MULAN_INITIAL_WINDOW_END_MARGIN_SEC
    if latest <= start_ts:
        return float(start_ts)
    return float(random.uniform(start_ts, latest))


def _normalize_report_text(text):
    raw_text = str(text or "").strip()
    raw_text = re.sub(r"^[1-9]\d*\s*(?:号|[.、:：．\)）])\s*", "", raw_text)
    raw_text = re.sub(r"（[^）]*辨[^）]*）", "", raw_text)
    raw_text = re.sub(r"\([^)]*辨[^)]*\)", "", raw_text)
    raw_text = re.sub(r"\s+", "", raw_text)
    return raw_text.strip("。；;，, ")


def _report_key(text):
    normalized = _normalize_report_text(text)
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _fixed_mulan_intel(report_text):
    normalized = _normalize_report_text(report_text)
    verdict, action = MULAN_FIXED_REPORTS.get(normalized, ("", ""))
    if not verdict:
        return {}
    return {
        "verdict": verdict,
        "text": str(report_text or "").strip(),
        "report_id": 0,
        "support_action": action,
        "support_route": "",
        "source_identity_id": 0,
        "updated_at": 0,
        "fixed": True,
    }


def parse_mulan_report_texts(text):
    report_texts = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = RE_REPORT_LINE.match(line)
        if not match:
            continue
        report_id = _parse_int(match.group(1))
        if report_id <= 0:
            continue
        report_text = re.sub(r"（[^）]*辨[^）]*）", "", match.group(2)).strip()
        report_text = re.sub(r"\([^)]*辨[^)]*\)", "", report_text).strip()
        report_text = report_text.strip("。；;，, ")
        if report_text:
            report_texts[str(report_id)] = report_text
    return report_texts


def parse_mulan_report_ids(text):
    raw_text = str(text or "")
    report_texts = parse_mulan_report_texts(raw_text)
    if report_texts:
        return [_parse_int(report_id) for report_id in report_texts.keys()]
    ids = []
    seen = set()
    for match in RE_REPORT_ID.finditer(raw_text):
        report_id = _parse_int(match.group(1))
        if report_id <= 0 or report_id in seen:
            continue
        seen.add(report_id)
        ids.append(report_id)
    return ids or list(MULAN_DEFAULT_IDS)


def classify_mulan_judgement(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return "unknown"
    if any(keyword in raw_text for keyword in LIMITED_JUDGE_KEYWORDS):
        return "limited"
    if any(keyword in raw_text for keyword in SUSPICIOUS_KEYWORDS):
        return "suspicious"
    if any(keyword in raw_text for keyword in RELIABLE_KEYWORDS):
        return "reliable"
    return "unknown"


def classify_mulan_publish(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return "unknown"
    if any(keyword in raw_text for keyword in FALSE_PUBLISH_KEYWORDS):
        return "false"
    if "慕兰谍影·真报" in raw_text or "前线采信了你的军报" in raw_text:
        return "true"
    if _is_daily_done_text(raw_text):
        return "done"
    return "unknown"


def parse_mulan_publish_result(text):
    raw_text = str(text or "")
    report_match = RE_TRUE_REPORT_TEXT.search(raw_text)
    route_match = RE_SUPPORT_ROUTE.search(raw_text)
    report_text = report_match.group(1).strip() if report_match else ""
    route = route_match.group(1).strip() if route_match else ""
    action = MULAN_SUPPORT_ROUTE_TO_ACTION.get(route, "")
    return report_text, route, action


def _get_report_text(report_id):
    report_texts = state.get("mulan_report_texts")
    if not isinstance(report_texts, dict):
        return ""
    return str(report_texts.get(str(report_id)) or report_texts.get(report_id) or "").strip()


def _clear_mulan_report_pool():
    state["mulan_pending_ids"] = ""
    state["mulan_report_texts"] = {}
    state["mulan_report_day"] = ""
    state["mulan_current_id"] = 0
    state["mulan_public_id"] = 0
    state["mulan_public_text"] = ""
    state["mulan_support_action"] = ""


def _invalidate_stale_mulan_report_pool(now):
    report_texts = state.get("mulan_report_texts")
    has_report_state = bool(report_texts) or bool(state.get("mulan_pending_ids")) or int(state.get("mulan_public_id", 0) or 0) > 0
    if not has_report_state:
        return False
    if str(state.get("mulan_report_day") or "") == _mulan_day_key(now):
        return False
    phase = str(state.get("mulan_phase") or MULAN_PHASE_IDLE).strip()
    next_time = float(state.get("next_mulan_time", 0) or 0)
    _clear_mulan_pending()
    _clear_mulan_report_pool()
    if phase == MULAN_PHASE_COOLDOWN and next_time > float(now or 0):
        return True
    state["mulan_phase"] = MULAN_PHASE_IDLE
    state["next_mulan_time"] = float(now or 0)
    state["mulan_last_result"] = "旧日军报已失效，重新搜集"
    state["mulan_last_error"] = ""
    return True


def _is_mulan_reports_missing_text(text):
    raw_text = str(text or "")
    return "今日尚未搜集军报" in raw_text or "先用 .搜集军报" in raw_text


def _normalize_mulan_intel_state(now):
    day_key = _mulan_day_key(now)
    intel_state = _meta_state.get("mulan_intel_state")
    if not isinstance(intel_state, dict) or intel_state.get("day") != day_key:
        intel_state = {"day": day_key, "reports": {}}
        _meta_state["mulan_intel_state"] = intel_state
    reports = intel_state.get("reports")
    if not isinstance(reports, dict):
        reports = {}
        intel_state["reports"] = reports
    return intel_state


def _record_mulan_intel(report_text, verdict, now, *, report_id=0, support_action="", support_route=""):
    key = _report_key(report_text)
    if not key:
        return False
    verdict = str(verdict or "").strip()
    if verdict not in {"reliable", "suspicious"}:
        return False
    intel_state = _normalize_mulan_intel_state(now)
    reports = intel_state["reports"]
    existing = reports.get(key) if isinstance(reports.get(key), dict) else {}
    if existing.get("verdict") == "reliable" and verdict != "reliable":
        return False
    reports[key] = {
        **existing,
        "verdict": verdict,
        "text": str(report_text or "").strip(),
        "report_id": int(report_id or existing.get("report_id", 0) or 0),
        "support_action": str(support_action or existing.get("support_action", "") or ""),
        "support_route": str(support_route or existing.get("support_route", "") or ""),
        "source_identity_id": int(get_current_identity_id() or existing.get("source_identity_id", 0) or 0),
        "updated_at": float(now or time.time()),
    }
    mark_dirty()
    return True


def _known_mulan_intel(report_text, now):
    fixed = _fixed_mulan_intel(report_text)
    if fixed:
        return fixed
    key = _report_key(report_text)
    if not key:
        return {}
    reports = _normalize_mulan_intel_state(now).get("reports", {})
    item = reports.get(key)
    return item if isinstance(item, dict) else {}


def _heuristic_support_action_from_text(text):
    normalized = _normalize_report_text(text)
    if not normalized:
        return ""
    if any(keyword in normalized for keyword in ("法士营", "灵袋", "蛇胆", "妖丹", "夜袭", "北帐")):
        return "奇袭"
    if any(keyword in normalized for keyword in ("粮道", "阵旗", "护送", "固守", "护阵", "黄龙阵旗")):
        return "护阵"
    if any(keyword in normalized for keyword in ("圣灯", "残焰", "破灯")):
        return "破灯"
    if any(keyword in normalized for keyword in ("小股", "草沟", "阵缺", "斥候", "绕行", "探草原")):
        return "斥候"
    return ""


def _support_action_from_panel(text):
    raw_text = str(text or "")
    match = RE_MILITARY_ORDER.search(raw_text)
    if not match:
        return ""
    return _heuristic_support_action_from_text(match.group(1))


def _fallback_support_action(report_texts, now=None):
    scored = []
    for report_id, text in (report_texts or {}).items():
        if now is not None and _known_mulan_intel(text, now).get("verdict") == "suspicious":
            continue
        action = _heuristic_support_action_from_text(text)
        if action == "护阵":
            score = 0
        elif action == "斥候":
            score = 1
        elif action == "破灯":
            score = 2
        elif action == "奇袭":
            score = 3
        else:
            score = 9
        scored.append((score, _parse_int(report_id), action))
    scored.sort()
    for _, _, action in scored:
        if action:
            return action
    return "护阵"


def _report_texts_for_pending_ids(pending_ids=None):
    report_texts = state.get("mulan_report_texts")
    if not isinstance(report_texts, dict):
        return {}
    if pending_ids is None:
        return {str(key): str(value or "") for key, value in report_texts.items() if str(value or "").strip()}
    selected = {}
    for report_id in pending_ids or ():
        text = str(report_texts.get(str(report_id)) or report_texts.get(report_id) or "").strip()
        if text:
            selected[str(report_id)] = text
    return selected


def _prepare_mulan_support(now, action, *, result="准备支援"):
    action = str(action or "").strip()
    if action not in MULAN_SUPPORT_ACTIONS:
        action = "护阵"
    _clear_mulan_pending()
    state["mulan_phase"] = MULAN_PHASE_READY_TO_SUPPORT
    state["mulan_support_action"] = action
    state["mulan_last_result"] = f"{result}：{action}"
    state["mulan_last_error"] = ""
    state["next_mulan_time"] = float(now)


def _prepare_conservative_support_or_panel(now, result="按文本保守支援", *, pending_ids=None):
    report_texts = _report_texts_for_pending_ids(pending_ids)
    if report_texts:
        _prepare_mulan_support(now, _fallback_support_action(report_texts, now), result=result)
        return True
    state["mulan_phase"] = MULAN_PHASE_READY_TO_PANEL
    state["mulan_last_result"] = f"{result}，缺文本需校准"
    state["mulan_last_error"] = ""
    state["next_mulan_time"] = float(now)
    return False


def _pending_command_family(reply_to=None, matched_family=None):
    family = str(matched_family or "").strip()
    if family in {"mulan_collect", "mulan_judge", "mulan_publish", "mulan_panel", "mulan_support"}:
        return family
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    if orig_cmd == CMD_MULAN_COLLECT or orig_cmd.startswith(f"{CMD_MULAN_COLLECT} "):
        return "mulan_collect"
    if (
        orig_cmd == CMD_MULAN_SHADOW
        or orig_cmd.startswith(f"{CMD_MULAN_SHADOW} ")
        or orig_cmd == CMD_MULAN_WAR_PANEL
        or orig_cmd.startswith(f"{CMD_MULAN_WAR_PANEL} ")
    ):
        return "mulan_panel"
    if orig_cmd == CMD_MULAN_PUBLISH or orig_cmd.startswith(f"{CMD_MULAN_PUBLISH} "):
        return "mulan_publish"
    if orig_cmd == CMD_MULAN_JUDGE or orig_cmd.startswith(f"{CMD_MULAN_JUDGE} "):
        return "mulan_judge"
    if orig_cmd == CMD_MULAN_SUPPORT or orig_cmd.startswith(f"{CMD_MULAN_SUPPORT} "):
        return "mulan_support"
    return ""


def _action_key_for_family(family):
    if family in {"mulan_collect", "mulan_panel"}:
        return "mulan_collect"
    if family == "mulan_judge":
        return "mulan_judge"
    if family == "mulan_publish":
        return "mulan_publish"
    if family == "mulan_support":
        return "mulan_support"
    return ""


def _close_mulan_action_guard(family, now):
    action_key = _action_key_for_family(family)
    if action_key:
        close_action_guard_action(action_key, send_as_id=get_current_identity_id(), now=now, reason="mulan_reply")


def _daily_done_delay_sec(now):
    now = float(now or time.time())
    due_at = _mulan_next_daily_batch_time(now)
    return max(60, due_at - now)


def _legacy_midnight_mulan_due_bucket(due_at, now):
    try:
        due_at = float(due_at or 0)
        now = float(now or time.time())
    except (TypeError, ValueError, OverflowError):
        return ""
    if due_at <= 0:
        return ""
    due_local = datetime.fromtimestamp(due_at, TZ_LOCAL)
    now_local = datetime.fromtimestamp(now, TZ_LOCAL)
    if not (due_local.hour == 0 and due_local.minute <= MULAN_LEGACY_MIDNIGHT_REBUCKET_MINUTE):
        return ""
    if due_local.date() > now_local.date():
        return "future"
    if due_local.date() == now_local.date():
        _last_start, last_end = _mulan_window_bounds(now_local, MULAN_INITIAL_BATCH_WINDOWS[-1])
        return "today" if now < last_end else "future"
    return ""


def _rebucket_mulan_cooldown_if_needed(now):
    if state.get("mulan_phase") != MULAN_PHASE_COOLDOWN:
        return False
    bucket = _legacy_midnight_mulan_due_bucket(state.get("next_mulan_time", 0), now)
    if not bucket:
        return False
    next_due = _mulan_initial_batch_time(now) if bucket == "today" else _mulan_next_daily_batch_time(now)
    state["next_mulan_time"] = float(next_due)
    state["mulan_last_error"] = ""
    last_result = str(state.get("mulan_last_result") or "").strip()
    if "批次窗口" not in last_result:
        state["mulan_last_result"] = f"{last_result or '已完成'}，已调整至批次窗口"
    save_state()
    console_log(f"🕵️ 慕兰 cooldown 已调整至批次窗口：{fmt_abs_ts(next_due)}", scope="identity", limit=180)
    return True


def _is_daily_done_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return False
    if not ("军报" in raw_text or "慕兰" in raw_text):
        return False
    return any(keyword in raw_text for keyword in DONE_KEYWORDS)


def _is_support_done_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return False
    if "慕兰" not in raw_text:
        return False
    return any(keyword in raw_text for keyword in SUPPORT_DONE_KEYWORDS)


def _schedule_next_mulan(now, delay_sec=None):
    if delay_sec is None:
        delay_sec = MULAN_CD + random.uniform(MULAN_JITTER_MIN_SEC, MULAN_JITTER_MAX_SEC)
    state["next_mulan_time"] = float(now + max(1, delay_sec))
    return state["next_mulan_time"]


def _mulan_next_time_blocks(now):
    return cd_blocks(state.get("next_mulan_time", 0), now, 0)


def _clear_mulan_pending():
    state["mulan_reply_to_msg_id"] = 0
    state["mulan_reply_due_at"] = 0
    state["mulan_sent_at"] = 0


def _defer_mulan_for_phaseful_summary(now, command):
    reason = get_phaseful_summary_risk_reason(now, lead_sec=60)
    if not reason:
        return False
    _clear_mulan_pending()
    state["next_mulan_time"] = float(now + random.uniform(MULAN_PHASEFUL_DEFER_MIN_SEC, MULAN_PHASEFUL_DEFER_MAX_SEC))
    state["mulan_last_command"] = str(command or "").strip()
    state["mulan_last_result"] = f"{reason}，慕兰延后发送"
    state["mulan_last_error"] = ""
    save_state()
    console_log(
        f"🕵️ 慕兰避让结算：{command}｜{reason}，延后到 {fmt_abs_ts(state['next_mulan_time'])}",
        scope="identity",
        limit=180,
    )
    return True


def _mulan_global_send_gate():
    gate = _meta_state.get(MULAN_GLOBAL_SEND_GATE_KEY)
    if not isinstance(gate, dict):
        gate = {}
        _meta_state[MULAN_GLOBAL_SEND_GATE_KEY] = gate
    return gate


def _defer_mulan_for_global_send_gate(now, command):
    gate = _mulan_global_send_gate()
    try:
        next_send_at = float(gate.get("next_send_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        next_send_at = 0
    if int(gate.get("last_identity_id") or 0) == int(get_current_identity_id() or 0):
        return False
    if next_send_at <= now:
        return False
    defer_until = next_send_at + random.uniform(MULAN_GLOBAL_DEFER_JITTER_MIN_SEC, MULAN_GLOBAL_DEFER_JITTER_MAX_SEC)
    state["next_mulan_time"] = float(defer_until)
    state["mulan_last_command"] = str(command or "").strip()
    state["mulan_last_result"] = f"慕兰全局错峰，延后到 {fmt_abs_ts(defer_until)}"
    state["mulan_last_error"] = ""
    save_state()
    return True


def _record_mulan_global_send(now, command):
    gate = _mulan_global_send_gate()
    sent_at = float(now or time.time())
    gate["last_sent_at"] = sent_at
    gate["next_send_at"] = sent_at + random.uniform(MULAN_GLOBAL_SEND_GAP_MIN_SEC, MULAN_GLOBAL_SEND_GAP_MAX_SEC)
    gate["last_identity_id"] = int(get_current_identity_id() or 0)
    gate["last_command"] = str(command or "").strip()


def _recover_mulan_unanswered_pending(now, phase, *, reply_to_msg_id=0):
    _clear_mulan_pending()
    if phase == MULAN_PHASE_JUDGE_PENDING:
        pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
        current_id = int(state.get("mulan_current_id", 0) or 0)
        pending_ids = [item for item in pending_ids if item != current_id]
        state["mulan_current_id"] = 0
        state["mulan_public_id"] = 0
        state["mulan_public_text"] = ""
        _prepare_conservative_support_or_panel(now, "辨报无回复，按文本支援", pending_ids=pending_ids)
        return f"⚠️ 慕兰辨报无回复，消息ID={reply_to_msg_id or '无'}，已按文本支援兜底。"
    if phase == MULAN_PHASE_SUPPORT_PENDING:
        action = str(state.get("mulan_support_action") or "").strip()
        state["mulan_phase"] = MULAN_PHASE_READY_TO_PANEL
        state["mulan_last_result"] = f"支援结果无回复，等待军功面板校准：{action or '未知'}"
        state["mulan_last_error"] = "支援终态缺失，未按完成结算"
        state["next_mulan_time"] = float(now + 60)
        mark_bot_health_suspect(
            f"慕兰支援 {action or '未知'} 超时无终态",
            reference_at=now,
            now=now,
        )
        return f"⚠️ 慕兰支援结果无回复，消息ID={reply_to_msg_id or '无'}，已暂停完成结算并等待军功面板校准。"
    if phase == MULAN_PHASE_PUBLISH_PENDING:
        state["mulan_phase"] = MULAN_PHASE_READY_TO_PANEL
        state["mulan_last_result"] = f"{phase} 无回复，准备面板校准"
        state["mulan_last_error"] = ""
        state["next_mulan_time"] = float(now)
        return f"⚠️ 慕兰{phase}无回复，消息ID={reply_to_msg_id or '无'}，转军功面板校准。"
    if phase == MULAN_PHASE_PANEL_PENDING:
        pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
        if not _prepare_conservative_support_or_panel(now, "军功面板无回复，按文本支援", pending_ids=pending_ids):
            _prepare_mulan_support(now, "护阵", result="军功面板无回复，保守支援")
        return f"⚠️ 慕兰军功面板无回复，消息ID={reply_to_msg_id or '无'}，已转保守支援。"

    state["mulan_phase"] = MULAN_PHASE_IDLE
    state["mulan_pending_ids"] = ""
    state["mulan_report_texts"] = {}
    state["mulan_current_id"] = 0
    state["mulan_public_id"] = 0
    state["mulan_public_text"] = ""
    state["mulan_support_action"] = ""
    state["mulan_last_result"] = "搜集无回复，等待重试"
    state["mulan_last_error"] = ""
    next_time = float(state.get("next_mulan_time", 0) or 0)
    if next_time <= 0 or next_time <= now:
        state["next_mulan_time"] = float(now + RETRY_MAX_SEC)
    return f"⚠️ 慕兰搜集军报无回复，消息ID={reply_to_msg_id or '无'}，稍后重试。"


def _finish_mulan_cycle(now, result, *, delay_sec=None, error=""):
    _clear_mulan_pending()
    state["mulan_phase"] = MULAN_PHASE_COOLDOWN
    _clear_mulan_report_pool()
    state["mulan_last_result"] = str(result or "").strip()
    state["mulan_last_error"] = str(error or "").strip()
    state["mulan_cycle_count"] = int(state.get("mulan_cycle_count", 0) or 0) + 1
    _schedule_next_mulan(now, delay_sec=delay_sec)


def clear_mulan_state(*, persist=False, keep_last_error=False):
    last_error = state.get("mulan_last_error") if keep_last_error else ""
    state["next_mulan_time"] = 0
    state["mulan_phase"] = MULAN_PHASE_IDLE
    state["mulan_reply_to_msg_id"] = 0
    state["mulan_reply_due_at"] = 0
    state["mulan_pending_ids"] = ""
    state["mulan_current_id"] = 0
    state["mulan_public_id"] = 0
    state["mulan_public_text"] = ""
    state["mulan_support_action"] = ""
    state["mulan_sent_at"] = 0
    state["mulan_last_msg_id"] = 0
    state["mulan_last_command"] = ""
    state["mulan_last_result"] = ""
    state["mulan_last_error"] = last_error or ""
    if persist:
        save_state()
    else:
        mark_dirty()


def _mulan_unavailable_reason():
    identity_id = int(get_current_identity_id() or 0)
    if identity_id <= 0 or not has_identity(identity_id):
        return f"身份不存在：{identity_id or 'unknown'}"
    if int(get_identity_account(identity_id) or 0) <= 0:
        return f"身份未绑定账号：{identity_id}"
    return ""


async def _disable_mulan_for_unavailable_identity(reason):
    _clear_mulan_pending()
    state["mulan_enabled"] = False
    state["mulan_phase"] = MULAN_PHASE_IDLE
    state["mulan_pending_ids"] = ""
    state["mulan_current_id"] = 0
    state["mulan_public_id"] = 0
    state["next_mulan_time"] = 0
    state["mulan_last_error"] = str(reason or "身份不可发送")
    save_state()
    await send_audit_log(f"⚠️ 慕兰已暂停：{state['mulan_last_error']}。", scope="identity", limit=220)


def get_mulan_status_text():
    lines = [
        "🕵️ 慕兰烽烟",
        f"- 已启用：{'是' if state.get('mulan_enabled') else '否'}",
        f"- 阶段：{state.get('mulan_phase') or MULAN_PHASE_IDLE}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_mulan_time', 0))}（{fmt_remaining(state.get('next_mulan_time', 0))}）",
        f"- 候选编号：{state.get('mulan_pending_ids') or '默认 1,2,3'}",
        f"- 当前辨报：{int(state.get('mulan_current_id', 0) or 0) or '无'}",
        f"- 待公开：{int(state.get('mulan_public_id', 0) or 0) or '无'}",
        f"- 支援动作：{state.get('mulan_support_action') or '无'}",
        f"- 待回复命令ID：{int(state.get('mulan_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('mulan_reply_due_at', 0))}（{fmt_remaining(state.get('mulan_reply_due_at', 0))}）",
        f"- 最近命令：{state.get('mulan_last_command') or '无'}",
        f"- 最近结果：{state.get('mulan_last_result') or '无'}",
    ]
    if state.get("mulan_last_msg_id"):
        lines.append(f"- 最近结果消息ID：{state['mulan_last_msg_id']}")
    if state.get("mulan_last_error"):
        lines.append(f"- 最近异常：{state['mulan_last_error']}")
    return "\n".join(lines)


async def _send_mulan_command(command, now, phase):
    unavailable_reason = _mulan_unavailable_reason()
    if unavailable_reason:
        await _disable_mulan_for_unavailable_identity(unavailable_reason)
        return False

    identity_id = get_current_identity_id()
    if _defer_mulan_for_phaseful_summary(now, command):
        return False
    if _defer_mulan_for_global_send_gate(now, command):
        return False

    queue_timeout = MULAN_SEND_QUEUE_TIMEOUT_SEC
    is_critical_chain = command.startswith(CMD_MULAN_PUBLISH) or command.startswith(CMD_MULAN_SUPPORT)
    if is_critical_chain:
        queue_timeout = MULAN_CRITICAL_SEND_QUEUE_TIMEOUT_SEC

    send_kwargs = {
        "track": False,
        "max_retry": 0,
        "source_module": "慕兰烽烟",
        "queue_timeout": queue_timeout,
    }
    if is_critical_chain:
        send_kwargs["priority"] = "chain"
    msg = await send_game_command(command, **send_kwargs)
    if not msg:
        if was_last_game_send_blocked_by_global(identity_id, command):
            state["next_mulan_time"] = float(now + random.uniform(10 * 60, 30 * 60))
            state["mulan_last_command"] = command
            state["mulan_last_result"] = "全局暂停，等待恢复错峰"
            state["mulan_last_error"] = ""
            save_state()
            return False
        send_block = classify_game_send_block(identity_id, command)
        if str(send_block.get("code") or "") == "send_queue_timeout":
            state["next_mulan_time"] = float(now + random.uniform(MULAN_SEND_QUEUE_RETRY_MIN_SEC, MULAN_SEND_QUEUE_RETRY_MAX_SEC))
            state["mulan_last_command"] = command
            state["mulan_last_result"] = "发送队列拥挤，慕兰错峰重试"
            state["mulan_last_error"] = ""
            save_state()
            return False
        if send_block.get("status") == "unsent":
            state["next_mulan_time"] = float(now + RETRY_MAX_SEC)
            state["mulan_last_command"] = command
            state["mulan_last_result"] = "运行保护拦截，慕兰延后重试"
            state["mulan_last_error"] = str(send_block.get("reason") or send_block.get("code") or "未发送")
            save_state()
            return False
        if send_block.get("status") == "unknown":
            state["next_mulan_time"] = float(now + MULAN_SEND_UNKNOWN_BACKOFF_SEC)
            state["mulan_last_command"] = command
            state["mulan_last_result"] = "发送状态未知，等待被动回复或稍后校准"
            state["mulan_last_error"] = str(send_block.get("reason") or send_block.get("code") or "发送状态未知")
            save_state()
            return False
        state["next_mulan_time"] = float(now + RETRY_MAX_SEC)
        state["mulan_last_command"] = command
        state["mulan_last_error"] = f"{command} 发送失败"
        save_state()
        await send_audit_log(f"❌ 慕兰发送失败：{command}，稍后重试。", scope="identity", limit=180)
        return False

    sent_at = float(getattr(msg, "sent_at", 0) or time.time())
    state["mulan_phase"] = phase
    state["mulan_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["mulan_reply_due_at"] = sent_at + MULAN_REPLY_TIMEOUT_SEC
    state["mulan_sent_at"] = sent_at
    state["mulan_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["mulan_last_command"] = command
    state["mulan_last_result"] = "已发送"
    state["mulan_last_error"] = ""
    state["next_mulan_time"] = state["mulan_reply_due_at"]
    _record_mulan_global_send(sent_at, command)
    save_state()
    console_log(f"🕵️ 慕兰已发送：{command}，等待回复→{fmt_abs_ts(state['mulan_reply_due_at'])}", scope="identity", limit=180)
    return True


def _known_reliable_report_for_current_ids(now, pending_ids):
    for report_id in pending_ids or ():
        report_text = _get_report_text(report_id)
        intel = _known_mulan_intel(report_text, now)
        if intel.get("verdict") == "reliable":
            return int(report_id), report_text, intel
    return 0, "", {}


def _unknown_report_ids_for_current_ids(now, pending_ids):
    unknown_ids = []
    for report_id in pending_ids or ():
        report_text = _get_report_text(report_id)
        intel = _known_mulan_intel(report_text, now)
        if not intel.get("verdict"):
            unknown_ids.append(report_id)
    return unknown_ids


def _support_action_from_known_reliable(now, pending_ids):
    _, report_text, intel = _known_reliable_report_for_current_ids(now, pending_ids)
    action = str(intel.get("support_action") or "").strip()
    if action in MULAN_SUPPORT_ACTIONS:
        return action
    return _heuristic_support_action_from_text(report_text)


def _prepare_conservative_support(now, result="无可靠军报，保守支援"):
    report_texts = state.get("mulan_report_texts")
    if not isinstance(report_texts, dict):
        report_texts = {}
    action = _fallback_support_action(report_texts, now)
    _prepare_mulan_support(now, action, result=result)


def _mulan_pending_family_for_phase(phase):
    phase = str(phase or "").strip()
    if phase == MULAN_PHASE_COLLECT_PENDING:
        return "mulan_collect"
    if phase == MULAN_PHASE_JUDGE_PENDING:
        return "mulan_judge"
    if phase == MULAN_PHASE_PUBLISH_PENDING:
        return "mulan_publish"
    if phase == MULAN_PHASE_PANEL_PENDING:
        return "mulan_panel"
    if phase == MULAN_PHASE_SUPPORT_PENDING:
        return "mulan_support"
    return ""


def _mulan_reply_matches_pending(family, reply_to, raw_text=""):
    family = str(family or "").strip()
    if family not in {"mulan_collect", "mulan_judge", "mulan_publish", "mulan_panel", "mulan_support"}:
        return True, ""
    pending_msg_id = int(state.get("mulan_reply_to_msg_id", 0) or 0)
    reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0)
    pending_family = _mulan_pending_family_for_phase(state.get("mulan_phase"))
    raw_text = str(raw_text or "")
    if pending_msg_id <= 0:
        if pending_family == family or {pending_family, family} <= {"mulan_collect", "mulan_panel"}:
            return True, ""
        if has_wait_time(raw_text) and any(keyword in raw_text for keyword in CD_KEYWORDS):
            return True, ""
        return False, "慕兰回复已无等待中的命令，按迟到回复忽略。"
    if reply_to_msg_id > 0 and reply_to_msg_id != pending_msg_id:
        return False, f"慕兰回复消息ID不匹配，当前等待 {pending_msg_id}，收到 {reply_to_msg_id}。"
    if pending_family and pending_family != family and not ({pending_family, family} <= {"mulan_collect", "mulan_panel"}):
        return False, f"慕兰回复阶段不匹配，当前等待 {pending_family}，收到 {family}。"
    return True, ""


def _is_mulan_reply_log_entry(entry):
    raw_text = str((entry or {}).get("text") or "").strip()
    if not raw_text:
        return False
    return (
        "慕兰" in raw_text
        or "军报" in raw_text
        or "辨报" in raw_text
        or "前线采信" in raw_text
        or "今日支援" in raw_text
        or "边境军功" in raw_text
        or has_wait_time(raw_text)
    )


def _is_mulan_support_start_notice(raw_text):
    raw_text = str(raw_text or "").strip()
    if not raw_text:
        return False
    if "正赶往" not in raw_text and "领了【" not in raw_text:
        return False
    if any(keyword in raw_text for keyword in SUPPORT_RESULT_KEYWORDS):
        return False
    if _is_support_done_text(raw_text) or _is_daily_done_text(raw_text):
        return False
    return True


def _is_mulan_terminal_recovery_text(phase, raw_text):
    raw_text = str(raw_text or "").strip()
    if not raw_text:
        return False
    if phase != MULAN_PHASE_SUPPORT_PENDING:
        return True
    if has_wait_time(raw_text) and any(keyword in raw_text for keyword in CD_KEYWORDS):
        return True
    if any(keyword in raw_text for keyword in SUPPORT_RESULT_KEYWORDS):
        return True
    if _is_support_done_text(raw_text) or _is_daily_done_text(raw_text):
        return True
    if _is_mulan_support_start_notice(raw_text):
        return False
    return "慕兰" in raw_text


async def _recover_mulan_pending_from_message_log(now, phase, reply_to_msg_id):
    reply_to_msg_id = int(reply_to_msg_id or 0)
    if reply_to_msg_id <= 0:
        return False
    family = _mulan_pending_family_for_phase(phase)
    if not family:
        return False
    replies = find_message_log_replies(
        reply_to_msg_id,
        now,
        lookback_sec=max(15 * 60, MULAN_REPLY_TIMEOUT_SEC * 5),
        lookahead_sec=30,
        chat_id=get_sent_message_chat_id(reply_to_msg_id, default=get_game_group_id(), send_as_id=get_current_identity_id()),
        predicate=_is_mulan_reply_log_entry,
    )
    if not replies:
        return False
    command = str(state.get("mulan_last_command") or "").strip()
    reply_to = SimpleNamespace(id=reply_to_msg_id, raw_text=command)
    for entry in replies:
        raw_text = str(entry.get("text") or "")
        if not _is_mulan_terminal_recovery_text(phase, raw_text):
            continue
        handled = await handle_mulan_reply(
            raw_text,
            float(entry.get("ts_epoch") or now),
            reply_to=reply_to,
            matched_family=family,
            result_msg_id=int(entry.get("message_id") or 0),
        )
        if handled:
            state["mulan_last_error"] = ""
            state["mulan_last_result"] = state.get("mulan_last_result") or "日志补偿"
            return True
    return False


async def handle_mulan_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0):
    if not state.get("mulan_enabled"):
        return False
    family = _pending_command_family(reply_to=reply_to, matched_family=matched_family)
    if not family:
        return False
    raw_text = str(text or "").strip()
    matched, mismatch_reason = _mulan_reply_matches_pending(family, reply_to, raw_text)
    if not matched:
        result_msg_id = int(result_msg_id or 0)
        if result_msg_id > 0:
            state["mulan_last_msg_id"] = result_msg_id
        state["mulan_last_error"] = mismatch_reason
        save_state()
        return True
    _close_mulan_action_guard(family, now)

    result_msg_id = int(result_msg_id or 0)
    if result_msg_id > 0:
        state["mulan_last_msg_id"] = result_msg_id

    if has_wait_time(raw_text) and any(keyword in raw_text for keyword in CD_KEYWORDS):
        wait_sec = parse_wait_time(raw_text)
        _finish_mulan_cycle(now, "冷却中", delay_sec=wait_sec + CD_BUFFER_SEC)
        state["mulan_last_error"] = ""
        save_state()
        await send_audit_log(f"🕵️ 慕兰 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}", scope="identity", limit=180)
        return True

    if family in {"mulan_judge", "mulan_publish", "mulan_panel"} and _is_mulan_reports_missing_text(raw_text):
        _clear_mulan_pending()
        _clear_mulan_report_pool()
        state["mulan_phase"] = MULAN_PHASE_IDLE
        state["next_mulan_time"] = float(now)
        state["mulan_last_result"] = "游戏确认今日尚未搜集军报，退回搜集"
        state["mulan_last_error"] = ""
        save_state()
        await send_audit_log("🕵️ 慕兰旧军报已失效，退回重新搜集；本轮不直接支援。", scope="identity", limit=220)
        return True

    if family in {"mulan_collect", "mulan_panel"}:
        report_texts = parse_mulan_report_texts(raw_text)
        is_report_panel = bool(report_texts) and ("军报匣" in raw_text or "慕兰谍影" in raw_text or "辨报" in raw_text)
        if family == "mulan_panel" and not is_report_panel:
            if _is_support_done_text(raw_text):
                _finish_mulan_cycle(now, "今日已支援", delay_sec=_daily_done_delay_sec(now))
                state["mulan_last_error"] = ""
                save_state()
                return True
            action = _support_action_from_panel(raw_text)
            if not action:
                pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
                action = _support_action_from_known_reliable(now, pending_ids)
            if not action:
                report_texts = state.get("mulan_report_texts") if isinstance(state.get("mulan_report_texts"), dict) else {}
                action = _fallback_support_action(report_texts, now)
            _prepare_mulan_support(now, action, result="军功面板校准后支援")
            save_state()
            return True

        if _is_daily_done_text(raw_text):
            pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
            action = _support_action_from_known_reliable(now, pending_ids)
            if action:
                _prepare_mulan_support(now, action, result="军报已处理，接支援")
            else:
                report_texts = _report_texts_for_pending_ids(pending_ids)
                action = _fallback_support_action(report_texts, now) if report_texts else "护阵"
                _prepare_mulan_support(now, action, result="军报已处理，保守支援")
            _clear_mulan_pending()
            save_state()
            return True

        ids = parse_mulan_report_ids(raw_text)
        if not report_texts:
            report_texts = {str(report_id): "" for report_id in ids}
        state["mulan_phase"] = MULAN_PHASE_READY_TO_JUDGE
        state["mulan_pending_ids"] = _encode_ids(ids)
        state["mulan_report_texts"] = report_texts
        state["mulan_report_day"] = _mulan_day_key(now)
        state["mulan_current_id"] = 0
        state["mulan_public_id"] = 0
        state["mulan_public_text"] = ""
        state["mulan_support_action"] = ""
        published_note = "｜已公开" if "状态：已公开" in raw_text or "状态: 已公开" in raw_text else ""
        state["mulan_last_result"] = f"已搜集：{state['mulan_pending_ids'] or '1,2,3'}{published_note}"
        state["mulan_last_error"] = ""
        _clear_mulan_pending()
        state["next_mulan_time"] = float(now)
        save_state()
        return True

    if family == "mulan_judge":
        report_id = int(state.get("mulan_current_id", 0) or 0)
        if report_id <= 0:
            match = RE_COMMAND_ID.search(str(getattr(reply_to, "raw_text", "") or "").strip())
            report_id = _parse_int(match.group(1) if match else 0)
        verdict = classify_mulan_judgement(raw_text)
        report_text = _get_report_text(report_id)
        pending_ids = [item for item in _decode_ids(state.get("mulan_pending_ids")) if item != report_id]
        _clear_mulan_pending()
        if verdict == "reliable":
            _record_mulan_intel(report_text, "reliable", now, report_id=report_id)
            state["mulan_phase"] = MULAN_PHASE_READY_TO_PUBLISH
            state["mulan_pending_ids"] = _encode_ids(pending_ids)
            state["mulan_public_id"] = report_id
            state["mulan_current_id"] = 0
            state["mulan_public_text"] = report_text
            state["mulan_last_result"] = f"{report_id}号可靠，准备公开"
            state["mulan_last_error"] = ""
            state["next_mulan_time"] = float(now)
            save_state()
            return True

        if verdict == "suspicious":
            _record_mulan_intel(report_text, "suspicious", now, report_id=report_id)
        state["mulan_public_id"] = 0
        state["mulan_current_id"] = 0
        state["mulan_pending_ids"] = _encode_ids(pending_ids)
        state["mulan_public_text"] = ""
        if verdict == "limited":
            result = "辨报受限，按文本支援"
        else:
            result = f"{report_id or '?'}号{('可疑' if verdict == 'suspicious' else '未识别')}，按文本支援"
        _prepare_conservative_support_or_panel(now, result, pending_ids=pending_ids)
        save_state()
        return True

    if family == "mulan_publish":
        public_id = int(state.get("mulan_public_id", 0) or 0)
        if public_id <= 0:
            match = RE_PUBLISH_COMMAND_ID.search(str(getattr(reply_to, "raw_text", "") or "").strip())
            public_id = _parse_int(match.group(1) if match else 0)
        publish_verdict = classify_mulan_publish(raw_text)
        report_text, support_route, support_action = parse_mulan_publish_result(raw_text)
        if not report_text:
            report_text = state.get("mulan_public_text") or _get_report_text(public_id)
        if publish_verdict == "true":
            state["mulan_report_day"] = _mulan_day_key(now)
            _record_mulan_intel(
                report_text,
                "reliable",
                now,
                report_id=public_id,
                support_action=support_action,
                support_route=support_route,
            )
            if not support_action:
                support_action = _heuristic_support_action_from_text(report_text)
            _prepare_mulan_support(now, support_action, result=f"已公开{public_id or ''}号真报".strip())
            state["mulan_public_text"] = report_text
            save_state()
            await send_audit_log(
                f"🕵️ 慕兰真报公开：{public_id or '未知'}号，支援={state.get('mulan_support_action') or '护阵'}。",
                scope="identity",
                limit=180,
            )
            return True
        if publish_verdict == "false":
            state["mulan_report_day"] = _mulan_day_key(now)
            _record_mulan_intel(report_text, "suspicious", now, report_id=public_id)
            _clear_mulan_pending()
            pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
            _prepare_conservative_support_or_panel(now, f"{public_id or '?'}号未采信，按文本支援", pending_ids=pending_ids)
            save_state()
            return True
        if publish_verdict == "done":
            state["mulan_report_day"] = _mulan_day_key(now)
            _clear_mulan_pending()
            pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
            action = _support_action_from_known_reliable(now, pending_ids)
            if action:
                _prepare_mulan_support(now, action, result="军报已公开，接支援")
            else:
                _prepare_conservative_support_or_panel(now, "军报已公开，按文本支援", pending_ids=pending_ids)
            save_state()
            return True
        _clear_mulan_pending()
        pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
        _prepare_conservative_support_or_panel(now, "公开回复未识别，按文本支援", pending_ids=pending_ids)
        save_state()
        await send_audit_log(f"⚠️ 慕兰公开回复未识别，已按文本支援兜底：{public_id or '未知'}号。", scope="identity", limit=220)
        return True

    if family == "mulan_support":
        if any(keyword in raw_text for keyword in SUPPORT_RESULT_KEYWORDS):
            action = state.get("mulan_support_action") or ""
            _finish_mulan_cycle(now, f"支援完成：{action or '未知'}", delay_sec=_daily_done_delay_sec(now))
            state["mulan_last_error"] = ""
            save_state()
            await send_audit_log(f"🕵️ 慕兰支援完成：{action or '未知'}。", scope="identity", limit=180)
            return True
        if _is_support_done_text(raw_text) or _is_daily_done_text(raw_text):
            _finish_mulan_cycle(now, "今日已支援", delay_sec=_daily_done_delay_sec(now))
            state["mulan_last_error"] = ""
            save_state()
            return True
        if "正赶往" in raw_text or "领了【" in raw_text:
            state["mulan_last_result"] = "支援进行中"
            state["mulan_last_error"] = ""
            state["mulan_phase"] = MULAN_PHASE_SUPPORT_PENDING
            state["mulan_reply_due_at"] = max(float(state.get("mulan_reply_due_at", 0) or 0), float(now + MULAN_REPLY_TIMEOUT_SEC))
            save_state()
            return True
        state["mulan_last_result"] = "支援回复未识别，按完成冷却"
        _finish_mulan_cycle(now, "支援回复未识别", delay_sec=_daily_done_delay_sec(now))
        state["mulan_last_error"] = ""
        save_state()
        await send_audit_log("⚠️ 慕兰支援回复未识别，已按今日完成处理。", scope="identity", limit=220)
        return True

    return False


async def run_mulan_scheduler(now):
    if not state.get("mulan_enabled"):
        return

    if _invalidate_stale_mulan_report_pool(now):
        save_state()

    reply_to_msg_id = int(state.get("mulan_reply_to_msg_id", 0) or 0)
    reply_due_at = float(state.get("mulan_reply_due_at", 0) or 0)
    if reply_to_msg_id > 0:
        if reply_due_at > now:
            return
        phase = state.get("mulan_phase") or MULAN_PHASE_IDLE
        if await _recover_mulan_pending_from_message_log(now, phase, reply_to_msg_id):
            save_state()
            await send_audit_log(f"🕵️ 慕兰日志补偿：已采纳超时回包，消息ID={reply_to_msg_id}", scope="identity", limit=220)
            return
        audit_text = _recover_mulan_unanswered_pending(now, phase, reply_to_msg_id=reply_to_msg_id)
        save_state()
        await send_audit_log(audit_text, scope="identity", limit=220)
        return

    phase = str(state.get("mulan_phase") or MULAN_PHASE_IDLE).strip()
    if phase in MULAN_PENDING_PHASES:
        _recover_mulan_unanswered_pending(now, phase)
        save_state()
        return
    if phase in MULAN_READY_PHASES and _mulan_next_time_blocks(now):
        return
    if phase == MULAN_PHASE_READY_TO_JUDGE:
        pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
        public_id, report_text, intel = _known_reliable_report_for_current_ids(now, pending_ids)
        if public_id > 0:
            support_action = str(intel.get("support_action") or "").strip()
            if "已公开" in str(state.get("mulan_last_result") or "") and support_action in MULAN_SUPPORT_ACTIONS:
                _prepare_mulan_support(now, support_action, result="已公开真报，接支援")
                save_state()
                return
            state["mulan_public_id"] = public_id
            state["mulan_public_text"] = report_text
            state["mulan_current_id"] = 0
            state["mulan_last_result"] = f"共享情报命中：{public_id}号可靠，准备公开"
            state["mulan_last_error"] = ""
            state["mulan_phase"] = MULAN_PHASE_READY_TO_PUBLISH
            state["next_mulan_time"] = float(now)
            save_state()
            return
        unknown_ids = _unknown_report_ids_for_current_ids(now, pending_ids)
        report_id = unknown_ids[0] if unknown_ids else 0
        if report_id <= 0:
            _prepare_mulan_support(now, "护阵", result="共享情报均非可靠，保守支援")
            save_state()
            return
        state["mulan_current_id"] = report_id
        await _send_mulan_command(f"{CMD_MULAN_JUDGE} {report_id}", now, MULAN_PHASE_JUDGE_PENDING)
        return

    if phase == MULAN_PHASE_READY_TO_PUBLISH:
        public_id = int(state.get("mulan_public_id", 0) or 0)
        if public_id <= 0:
            _finish_mulan_cycle(now, "待公开编号丢失，跳过", error="待公开编号丢失")
            save_state()
            return
        await _send_mulan_command(f"{CMD_MULAN_PUBLISH} {public_id}", now, MULAN_PHASE_PUBLISH_PENDING)
        return

    if phase == MULAN_PHASE_READY_TO_PANEL:
        last_command = str(state.get("mulan_last_command") or "").strip()
        last_result = str(state.get("mulan_last_result") or "").strip()
        if last_command.startswith(CMD_MULAN_SUPPORT) and (
            "support_pending 无回复" in last_result
            or "支援进行中" in last_result
            or "支援结果超时" in last_result
        ):
            action = str(state.get("mulan_support_action") or "").strip()
            _finish_mulan_cycle(now, f"支援校准旧状态已收束：{action or '未知'}", delay_sec=_daily_done_delay_sec(now))
            state["mulan_last_error"] = ""
            save_state()
            return
        pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
        action = _support_action_from_known_reliable(now, pending_ids)
        if action:
            _prepare_mulan_support(now, action, result="面板前命中共享路线")
            save_state()
            return
        if _prepare_conservative_support_or_panel(now, "面板前按文本支援", pending_ids=pending_ids):
            save_state()
            return
        await _send_mulan_command(CMD_MULAN_WAR_PANEL, now, MULAN_PHASE_PANEL_PENDING)
        return

    if phase == MULAN_PHASE_READY_TO_SUPPORT:
        action = str(state.get("mulan_support_action") or "").strip()
        if action not in MULAN_SUPPORT_ACTIONS:
            report_texts = state.get("mulan_report_texts") if isinstance(state.get("mulan_report_texts"), dict) else {}
            action = _fallback_support_action(report_texts, now)
            state["mulan_support_action"] = action
        await _send_mulan_command(f"{CMD_MULAN_SUPPORT} {action}", now, MULAN_PHASE_SUPPORT_PENDING)
        return

    if _rebucket_mulan_cooldown_if_needed(now):
        return

    if _mulan_next_time_blocks(now):
        return

    if phase not in {MULAN_PHASE_IDLE, MULAN_PHASE_COOLDOWN, ""}:
        state["mulan_phase"] = MULAN_PHASE_IDLE
        state["mulan_last_error"] = f"异常阶段 {phase}，已复位"
        state["next_mulan_time"] = float(now + RETRY_MAX_SEC)
        save_state()
        await send_audit_log(f"⚠️ 慕兰阶段异常已复位：{phase}", scope="identity", limit=180)
        return

    await _send_mulan_command(CMD_MULAN_COLLECT, now, MULAN_PHASE_COLLECT_PENDING)


def schedule_mulan_initial_check(now, *, persist=False, keep_last_error=True):
    last_error = state.get("mulan_last_error") if keep_last_error else ""
    state["mulan_phase"] = MULAN_PHASE_IDLE
    state["mulan_reply_to_msg_id"] = 0
    state["mulan_reply_due_at"] = 0
    state["mulan_pending_ids"] = ""
    state["mulan_report_texts"] = {}
    state["mulan_report_day"] = ""
    state["mulan_current_id"] = 0
    state["mulan_public_id"] = 0
    state["mulan_public_text"] = ""
    state["mulan_support_action"] = ""
    state["mulan_sent_at"] = 0
    state["mulan_last_error"] = last_error or ""
    state["next_mulan_time"] = _mulan_initial_batch_time(now)
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_mulan_time"]


__all__ = [
    "MULAN_DEFAULT_IDS",
    "classify_mulan_judgement",
    "clear_mulan_state",
    "get_mulan_status_text",
    "handle_mulan_reply",
    "parse_mulan_report_ids",
    "run_mulan_scheduler",
    "schedule_mulan_initial_check",
]
