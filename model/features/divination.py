import hashlib
import json
import os
import random
import re
import time
from collections import deque
from datetime import datetime, timedelta

from ..action_guard import close_by_family as close_action_guard_by_family
from ..config import CMD_DIVINATION, CMD_DIVINATION_EXCHANGE, MESSAGES_DIR, PROJECT_ROOT_DIR, TZ_LOCAL
from ..persisted_state import PersistedState
from ..persistence import mark_dirty, save_state
from ..runtime import mono, send_audit_log, send_game_command
from ..state import (
    get_divination_daily_limit,
    get_divination_pending_exchanges,
    get_divination_run_state,
    get_current_identity_id,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    get_storage_bag_item_rules,
    get_storage_bag_records,
    set_divination_pending_exchanges,
    set_divination_run_state,
)
from ..storage_bag_api_runtime import refresh_storage_bag_records_from_api
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, get_day_key, parse_wait_time
from .storage_bag import (
    apply_storage_bag_item_deltas,
    get_storage_bag_transfer_snapshot,
    parse_storage_bag_item_counts,
    start_storage_bag_transfer_batch,
)


DIVINATION_EXCHANGE_WINDOW_SEC = 5 * 60
DIVINATION_TRANSFER_MIN_REMAINING_SEC = 45
DIVINATION_QUERY_GAP_SEC = 60
DIVINATION_QUERY_REPLY_TIMEOUT_SEC = 3 * 60
DIVINATION_SEND_FAILURE_BACKOFF_SEC = 5 * 60
DIVINATION_MESSAGE_LOG_PREREAD_INTERVAL_SEC = 5 * 60
DIVINATION_DAILY_START_MIN_SEC = 5 * 60
DIVINATION_DAILY_START_MAX_SEC = 75 * 60
DIVINATION_FIRST_START_MIN_SEC = 5
DIVINATION_FIRST_START_MAX_SEC = 30
DIVINATION_PHASE_IDLE = "idle"
DIVINATION_PHASE_WAITING_INTERMEDIATE = "waiting_intermediate"
DIVINATION_PHASE_WAITING_FINAL = "waiting_final"
DIVINATION_PHASE_DONE_TODAY = "done_today"
DIVINATION_PHASE_BLOCKED = "blocked"
DIVINATION_PHASES = {
    DIVINATION_PHASE_IDLE,
    DIVINATION_PHASE_WAITING_INTERMEDIATE,
    DIVINATION_PHASE_WAITING_FINAL,
    DIVINATION_PHASE_DONE_TODAY,
    DIVINATION_PHASE_BLOCKED,
}
DIVINATION_LISTING_ITEM_CANDIDATES = ("灵石", "下品灵石", "杂草")
DIVINATION_AUTO_EXCHANGE_TARGETS = ("昆吾通行令",)
DIVINATION_STORAGE_BAG_ITEM_RULES_PATH = os.path.join(PROJECT_ROOT_DIR, "data", "storage_bag_item_rules.json")
DIVINATION_PENDING_STATUS_LABELS = {
    "created": "已记录",
    "transfer_running": "资源转移中",
    "exchange_sent": "已发送换取",
    "manual_required": "需手动处理",
    "failed": "失败",
    "expired": "已过期",
}

RE_DIVINATION_TREASURE = re.compile(
    r"【神物现世】.*?卦象显示，?【(?P<target>[^】]+)】.*?"
    r"你是否愿意消耗\s*(?P<costs>.*?)\s*来换取它.*?"
    r"请在\s*(?P<window>[^\s]+)\s*内回复本消息\s*\.换取",
    re.S,
)
RE_DIVINATION_EXCHANGE_SUCCESS = re.compile(r"换取成功.*?【(?P<target>[^】]+)】已放入", re.S)
RE_DIVINATION_EXCHANGE_EXPIRED = re.compile(r"机缘.*?(?:消散|错过)|超时|已过期")
RE_DIVINATION_EXCHANGE_SHORTAGE = re.compile(r"(?:祭品|资源|材料).*?不足|不足以.*?换取|无法.*?换取")
RE_DIVINATION_DAILY_COUNT = re.compile(r"今日第\s*(?P<count>\d+)\s*次")
RE_DIVINATION_WAITING = re.compile(r"开始转动天机罗盘|卦象.*?(?:凝聚|推演|推算)|请稍候")
RE_DIVINATION_XIUWEI_SHORTAGE = re.compile(r"修为不足")
RE_DIVINATION_DAILY_LIMIT = re.compile(r"今日.*?(?:次数|问天|窥探).*?(?:已满|已达|达到|上限|用尽)|(?:已满|已达|达到).*?今日.*?(?:次数|上限)")
RE_DIVINATION_HEXAGRAM = re.compile(r"【卦象[：:]\s*(?P<bracket>[^】]+)】|得卦【(?P<gua>[^】]+)】")


def _normalize_costs(raw_costs):
    costs = parse_storage_bag_item_counts(str(raw_costs or ""), allow_plain=True)
    return {str(item or "").strip(): int(count or 0) for item, count in costs.items() if str(item or "").strip() and int(count or 0) > 0}


def parse_divination_treasure_text(text):
    raw_text = str(text or "")
    match = RE_DIVINATION_TREASURE.search(raw_text)
    if not match:
        return {}
    costs = _normalize_costs(match.group("costs"))
    if not costs:
        return {}
    wait_sec = parse_wait_time(match.group("window") or "")
    if wait_sec <= 0:
        wait_sec = DIVINATION_EXCHANGE_WINDOW_SEC
    return {
        "target_item": str(match.group("target") or "").strip(),
        "costs": costs,
        "window_sec": min(DIVINATION_EXCHANGE_WINDOW_SEC, int(wait_sec or DIVINATION_EXCHANGE_WINDOW_SEC)),
    }


def _save_or_mark_dirty(*, persist=True):
    if persist:
        if save_state() is False:
            mark_dirty()
    else:
        mark_dirty()


def _pending_records():
    records = get_divination_pending_exchanges()
    return records if isinstance(records, dict) else {}


def _set_pending_records(records, *, persist=True):
    set_divination_pending_exchanges(records if isinstance(records, dict) else {})
    _save_or_mark_dirty(persist=persist)


def _run_records():
    state = PersistedState({})
    state.restore(get_divination_run_state())
    records = state.get()
    return records if isinstance(records, dict) else {}


def _set_run_records(records, *, persist=True):
    state = PersistedState({})
    state.restore(get_divination_run_state())
    state.set(records if isinstance(records, dict) else {})
    snapshot = state.snapshot_if_dirty()
    if snapshot is None:
        return False
    set_divination_run_state(snapshot)
    _save_or_mark_dirty(persist=persist)
    return True


def _run_key(identity_id):
    return str(int(identity_id or 0))


def _calc_daily_start_at(now, *, next_day=False):
    local_now = datetime.fromtimestamp(float(now or time.time()), TZ_LOCAL)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if next_day:
        day_start += timedelta(days=1)
    return day_start.timestamp() + random.uniform(DIVINATION_DAILY_START_MIN_SEC, DIVINATION_DAILY_START_MAX_SEC)


def _next_same_day_start(now):
    return float(now or time.time()) + random.uniform(DIVINATION_FIRST_START_MIN_SEC, DIVINATION_FIRST_START_MAX_SEC)


def _blank_run_record(now):
    now = float(now or time.time())
    return {
        "day_key": get_day_key(now),
        "phase": DIVINATION_PHASE_IDLE,
        "count": 0,
        "sent_attempts": 0,
        "next_query_at": _calc_daily_start_at(now),
        "pending_query_msg_id": 0,
        "pending_reply_msg_id": 0,
        "pending_until": 0,
        "pending_count_recorded": False,
        "blocked_day": "",
        "block_reason": "",
        "last_error": "",
        "last_sent_at": 0,
        "last_result_at": 0,
        "last_observed_at": 0,
        "message_log_checked_day": "",
        "message_log_checked_at": 0,
        "message_log_observed_count": 0,
        "exchange_success_day": "",
        "exchange_success_target": "",
        "exchange_success_at": 0,
    }


def _coerce_phase(value, record=None):
    phase = str(value or "").strip()
    if phase in DIVINATION_PHASES:
        return phase
    record = record if isinstance(record, dict) else {}
    if int(record.get("pending_query_msg_id") or 0) > 0:
        if bool(record.get("pending_count_recorded")):
            return DIVINATION_PHASE_WAITING_FINAL
        return DIVINATION_PHASE_WAITING_INTERMEDIATE
    if record.get("blocked_day"):
        return DIVINATION_PHASE_BLOCKED
    return DIVINATION_PHASE_IDLE


def _coerce_run_record(record, now, *, schedule_missing=True):
    now = float(now or time.time())
    day_key = get_day_key(now)
    source = record if isinstance(record, dict) else {}
    if str(source.get("day_key") or "") != day_key:
        record = _blank_run_record(now)
        if record["next_query_at"] <= now:
            record["next_query_at"] = _next_same_day_start(now)
        return record, True

    record = dict(source)
    changed = False
    defaults = _blank_run_record(now)
    for key, value in defaults.items():
        if key not in record:
            record[key] = value
            changed = True
    int_keys = ("count", "sent_attempts", "pending_query_msg_id", "pending_reply_msg_id", "message_log_observed_count")
    float_keys = (
        "next_query_at",
        "pending_until",
        "last_sent_at",
        "last_result_at",
        "last_observed_at",
        "message_log_checked_at",
        "exchange_success_at",
    )
    for key in int_keys:
        try:
            normalized = int(record.get(key) or 0)
        except (TypeError, ValueError):
            normalized = 0
        if record.get(key) != normalized:
            record[key] = normalized
            changed = True
    for key in float_keys:
        try:
            normalized = float(record.get(key) or 0)
        except (TypeError, ValueError):
            normalized = 0.0
        if record.get(key) != normalized:
            record[key] = normalized
            changed = True
    record["pending_count_recorded"] = bool(record.get("pending_count_recorded"))
    normalized_phase = _coerce_phase(record.get("phase"), record)
    if record.get("phase") != normalized_phase:
        record["phase"] = normalized_phase
        changed = True
    if schedule_missing and record.get("next_query_at", 0) <= 0 and int(record.get("pending_query_msg_id") or 0) <= 0 and not record.get("blocked_day") and not _has_exchange_success_today(record, now):
        record["next_query_at"] = _next_same_day_start(now)
        record["phase"] = DIVINATION_PHASE_IDLE
        changed = True
    return record, changed


def _get_run_record(identity_id, now, *, records=None, schedule_missing=True):
    records = records if isinstance(records, dict) else _run_records()
    key = _run_key(identity_id)
    record, changed = _coerce_run_record(records.get(key), now, schedule_missing=schedule_missing)
    return key, record, changed


def _schedule_next_day(record, now):
    record["next_query_at"] = _calc_daily_start_at(now, next_day=True)
    record["pending_query_msg_id"] = 0
    record["pending_reply_msg_id"] = 0
    record["pending_until"] = 0
    record["pending_count_recorded"] = False
    record["phase"] = DIVINATION_PHASE_DONE_TODAY
    return record


def _has_exchange_success_today(record, now):
    record = record if isinstance(record, dict) else {}
    return str(record.get("exchange_success_day") or "") == get_day_key(now)


def _clamp_daily_count(value, limit):
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        count = 0
    try:
        daily_limit = int(limit or 0)
    except (TypeError, ValueError):
        daily_limit = 0
    count = max(0, count)
    if daily_limit > 0:
        return min(count, daily_limit)
    return count


def _normalize_record_count_to_limit(record, limit):
    record = record if isinstance(record, dict) else {}
    try:
        current = int(record.get("count") or 0)
    except (TypeError, ValueError):
        current = 0
    clamped = _clamp_daily_count(current, limit)
    if current == clamped:
        return False
    record["count"] = clamped
    return True


def _record_observed_daily_count(record, observed_count, limit, now):
    record = record if isinstance(record, dict) else {}
    try:
        observed_count = int(observed_count or 0)
    except (TypeError, ValueError):
        observed_count = 0
    if observed_count <= 0:
        return False
    changed = False
    if observed_count > int(record.get("message_log_observed_count") or 0):
        record["message_log_observed_count"] = observed_count
        changed = True
    current_count = _clamp_daily_count(record.get("count") or 0, limit)
    next_count = max(current_count, _clamp_daily_count(observed_count, limit))
    if int(record.get("count") or 0) != next_count:
        record["count"] = next_count
        changed = True
    if changed:
        record["last_observed_at"] = float(now or time.time())
        record["last_error"] = ""
    return changed


def _mark_exchange_success_today(identity_id, now, target_item):
    identity_id = int(identity_id or 0)
    if identity_id <= 0:
        return False
    records = _run_records()
    key, record, _changed = _get_run_record(identity_id, now, records=records, schedule_missing=False)
    record["exchange_success_day"] = get_day_key(now)
    record["exchange_success_target"] = str(target_item or "").strip()
    record["exchange_success_at"] = float(now or time.time())
    record["last_error"] = "今日已换取神物，停止问天"
    _schedule_next_day(record, now)
    records[key] = record
    _set_run_records(records)
    return True


def _schedule_after_round(record, now, limit):
    _normalize_record_count_to_limit(record, limit)
    if int(record.get("count") or 0) >= int(limit or 0):
        return _schedule_next_day(record, now)
    record["next_query_at"] = float(now or time.time()) + DIVINATION_QUERY_GAP_SEC
    record["pending_query_msg_id"] = 0
    record["pending_reply_msg_id"] = 0
    record["pending_until"] = 0
    record["pending_count_recorded"] = False
    record["phase"] = DIVINATION_PHASE_IDLE
    return record


def _extract_daily_count(text):
    match = RE_DIVINATION_DAILY_COUNT.search(str(text or ""))
    if not match:
        return 0
    try:
        return int(match.group("count") or 0)
    except (TypeError, ValueError):
        return 0


def _recent_message_log_paths(now, *, days=2):
    local_now = datetime.fromtimestamp(float(now or time.time()), TZ_LOCAL)
    paths = []
    for offset in range(max(1, int(days or 1))):
        day = local_now - timedelta(days=offset)
        paths.append(os.path.join(MESSAGES_DIR, f"{day.strftime('%Y-%m-%d')}.log"))
    return paths


def _recover_daily_count_from_message_log(record, now):
    record = record if isinstance(record, dict) else {}
    pending_query_msg_id = int(record.get("pending_query_msg_id") or 0)
    pending_reply_msg_id = int(record.get("pending_reply_msg_id") or 0)
    if pending_query_msg_id <= 0:
        return 0
    for path in _recent_message_log_paths(now):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = deque(handle, maxlen=2000)
        except OSError:
            continue
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            reply_to_msg_id = int(payload.get("reply_to_msg_id") or 0)
            message_id = int(payload.get("message_id") or 0)
            if reply_to_msg_id != pending_query_msg_id and (pending_reply_msg_id <= 0 or message_id != pending_reply_msg_id):
                continue
            daily_count = _extract_daily_count(payload.get("text") or "")
            if daily_count > 0:
                return daily_count
    return 0


def _is_divination_command_text(text):
    return str(text or "").strip() == CMD_DIVINATION


def _recover_identity_daily_count_from_message_log(identity_id, now):
    try:
        identity_id = int(identity_id or 0)
    except (TypeError, ValueError):
        identity_id = 0
    if identity_id <= 0:
        return 0
    command_msg_ids = set()
    reply_msg_ids = set()
    max_count = 0
    for path in _recent_message_log_paths(now, days=1):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = deque(handle, maxlen=5000)
        except OSError:
            continue
        for line in lines:
            try:
                payload = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            try:
                msg_id = int(payload.get("message_id") or 0)
                sender_id = int(payload.get("sender_id") or 0)
                reply_to_msg_id = int(payload.get("reply_to_msg_id") or 0)
            except (TypeError, ValueError):
                continue
            text = payload.get("text") or ""
            if msg_id > 0 and sender_id == identity_id and _is_divination_command_text(text):
                command_msg_ids.add(msg_id)
                continue
            daily_count = _extract_daily_count(text)
            if daily_count <= 0:
                continue
            if reply_to_msg_id in command_msg_ids or msg_id in reply_msg_ids:
                if msg_id > 0:
                    reply_msg_ids.add(msg_id)
                max_count = max(max_count, daily_count)
    return max_count


def _sync_daily_count_from_message_log(identity_id, record, now, limit, *, force=False):
    record = record if isinstance(record, dict) else {}
    if int(record.get("pending_query_msg_id") or 0) > 0:
        return False
    day_key = get_day_key(now)
    checked_day = str(record.get("message_log_checked_day") or "")
    checked_at = float(record.get("message_log_checked_at") or 0)
    if (
        not force
        and checked_day == day_key
        and checked_at > 0
        and float(now or 0) < checked_at + DIVINATION_MESSAGE_LOG_PREREAD_INTERVAL_SEC
    ):
        return False
    observed_count = _recover_identity_daily_count_from_message_log(identity_id, now)
    changed = False
    if _normalize_record_count_to_limit(record, limit):
        changed = True
    if record.get("message_log_checked_day") != day_key:
        record["message_log_checked_day"] = day_key
        changed = True
    if float(record.get("message_log_checked_at") or 0) != float(now or 0):
        record["message_log_checked_at"] = float(now or time.time())
        changed = True
    if _record_observed_daily_count(record, observed_count, limit, now):
        changed = True
    if int(record.get("count") or 0) >= int(limit or 0) and str(record.get("phase") or "") != DIVINATION_PHASE_DONE_TODAY:
        record["phase"] = DIVINATION_PHASE_DONE_TODAY
        changed = True
    return changed


def _is_waiting_result_text(text):
    raw_text = str(text or "")
    if parse_divination_treasure_text(raw_text):
        return False
    return bool(RE_DIVINATION_WAITING.search(raw_text))


def _is_daily_stop_text(text):
    raw_text = str(text or "")
    if RE_DIVINATION_XIUWEI_SHORTAGE.search(raw_text):
        return "修为不足"
    if RE_DIVINATION_DAILY_LIMIT.search(raw_text):
        return "今日次数已满"
    return ""


def _matches_pending_query_reply(record, event=None, reply_to=None, reply_context=None):
    record = record if isinstance(record, dict) else {}
    pending_query_msg_id = int(record.get("pending_query_msg_id") or 0)
    if pending_query_msg_id <= 0:
        return False
    event_msg_id = int(getattr(event, "id", 0) or 0)
    pending_reply_msg_id = int(record.get("pending_reply_msg_id") or 0)
    if event_msg_id > 0 and pending_reply_msg_id > 0 and event_msg_id == pending_reply_msg_id:
        return True
    reply_to_msg_id = int((reply_context or {}).get("reply_to_msg_id") or getattr(reply_to, "id", 0) or 0)
    return reply_to_msg_id > 0 and reply_to_msg_id == pending_query_msg_id


def _resolve_pending_query_identity_id(now, event=None, reply_to=None, reply_context=None):
    records = _run_records()
    for key, record in list(records.items()):
        if not isinstance(record, dict):
            continue
        try:
            identity_id = int(key)
        except (TypeError, ValueError):
            continue
        if identity_id <= 0:
            continue
        coerced, _changed = _coerce_run_record(record, now, schedule_missing=False)
        if _matches_pending_query_reply(coerced, event=event, reply_to=reply_to, reply_context=reply_context):
            return identity_id
    return 0


def _identity_has_active_exchange(identity_id, now):
    identity_id = int(identity_id or 0)
    for pending in _pending_records().values():
        if not isinstance(pending, dict):
            continue
        if int(pending.get("target_identity_id") or 0) != identity_id:
            continue
        status = str(pending.get("status") or "").strip()
        if status in {"failed", "expired", "manual_required"}:
            continue
        expires_at = float(pending.get("expires_at") or 0)
        if expires_at <= 0 or expires_at > float(now or time.time()):
            return True
    return False


def _note_query_reply(identity_id, now, text, *, event=None, final=False, stop_reason=""):
    identity_id = int(identity_id or 0)
    if identity_id <= 0:
        return False
    records = _run_records()
    key, record, _changed = _get_run_record(identity_id, now, records=records)
    limit = get_divination_daily_limit(identity_id)
    daily_count = _extract_daily_count(text)
    if daily_count <= 0 and final and int(record.get("pending_query_msg_id") or 0) > 0 and not record.get("pending_count_recorded"):
        daily_count = _recover_daily_count_from_message_log(record, now)
    if daily_count > 0:
        _record_observed_daily_count(record, daily_count, limit, now)
        record["pending_count_recorded"] = True

    result_msg_id = int(getattr(event, "id", 0) or 0)
    if result_msg_id > 0:
        record["pending_reply_msg_id"] = result_msg_id
    record["last_result_at"] = float(now or time.time())

    if stop_reason:
        record["blocked_day"] = get_day_key(now)
        record["block_reason"] = stop_reason
        record["last_error"] = stop_reason
        _schedule_next_day(record, now)
        record["phase"] = DIVINATION_PHASE_BLOCKED
    elif final:
        missing_observed_count = int(record.get("pending_query_msg_id") or 0) > 0 and not record.get("pending_count_recorded")
        _schedule_after_round(record, now, limit)
        if missing_observed_count and int(record.get("count") or 0) < int(limit or 0):
            record["last_error"] = "最终结果缺少今日次数，未计入今日次数"
        else:
            record["last_error"] = ""
    else:
        record["pending_until"] = float(now or time.time()) + DIVINATION_QUERY_REPLY_TIMEOUT_SEC
        record["last_error"] = "等待最终结果编辑"
        record["phase"] = DIVINATION_PHASE_WAITING_FINAL

    records[key] = record
    _set_run_records(records)
    close_action_guard_by_family(
        "divination",
        send_as_id=identity_id,
        reason="stop" if stop_reason else ("final" if final else "waiting"),
        now=now,
    )
    return True



def _make_pending_key(event, text):
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    msg_id = int(getattr(event, "id", 0) or 0)
    if chat_id and msg_id:
        return f"{chat_id}:{msg_id}"
    digest = hashlib.sha1(str(text or "").encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"text:{digest}"


def _normalize_identity_id_set(values):
    result = set()
    for value in values or []:
        try:
            identity_id = int(value or 0)
        except (TypeError, ValueError):
            identity_id = 0
        if identity_id > 0:
            result.add(identity_id)
    return result


def _divination_api_refresh_identity_ids(target_identity_id, *, include_sources=True):
    ids = {int(target_identity_id or 0)}
    if include_sources:
        for source_id in get_identity_ids():
            try:
                source_id = int(source_id or 0)
            except (TypeError, ValueError):
                continue
            if (
                source_id > 0
                and source_id != int(target_identity_id or 0)
                and get_identity_enabled(source_id)
                and not _is_protected_transfer_source(source_id)
            ):
                ids.add(source_id)
    return sorted(identity_id for identity_id in ids if identity_id > 0)


def _storage_items(identity_id, refreshed_identity_ids=None):
    if refreshed_identity_ids is not None and int(identity_id or 0) not in refreshed_identity_ids:
        return {}
    record = (get_storage_bag_records() or {}).get(str(int(identity_id or 0))) or {}
    items = record.get("items") if isinstance(record, dict) else {}
    if isinstance(items, dict) and items:
        return items
    sections = record.get("sections") if isinstance(record, dict) else {}
    if not isinstance(sections, dict):
        return {}
    merged = {}
    for section_items in sections.values():
        if not isinstance(section_items, dict):
            continue
        for item_name, count in section_items.items():
            item_name = str(item_name or "").strip()
            if not item_name:
                continue
            try:
                merged[item_name] = int(merged.get(item_name) or 0) + int(count or 0)
            except (TypeError, ValueError):
                continue
    return merged


def _is_supported_auto_exchange_target(target_item):
    normalized = str(target_item or "").strip()
    return normalized in DIVINATION_AUTO_EXCHANGE_TARGETS


def _base_storage_bag_item_rules():
    try:
        with open(DIVINATION_STORAGE_BAG_ITEM_RULES_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception:
        return {}
    items = data.get("items") if isinstance(data, dict) else {}
    if not isinstance(items, dict):
        return {}
    return {str(item_name or "").strip(): rule for item_name, rule in items.items() if str(item_name or "").strip() and isinstance(rule, dict)}


def _storage_bag_transfer_method(item_name):
    item_name = str(item_name or "").strip()
    if not item_name:
        return "unknown"
    base_rule = _base_storage_bag_item_rules().get(item_name)
    saved_rule = get_storage_bag_item_rules().get(item_name)
    rule = {}
    if isinstance(base_rule, dict):
        rule.update(base_rule)
    if isinstance(saved_rule, dict):
        rule.update(saved_rule)
    method = str(rule.get("method") or "unknown").strip().lower()
    return method if method in {"basic", "gift", "blocked", "unknown"} else "unknown"


def _is_transfer_blocked_item(item_name):
    return _storage_bag_transfer_method(item_name) == "blocked"


def _is_protected_transfer_source(identity_id):
    profile = get_send_as_profile(identity_id)
    candidates = []
    if isinstance(profile, dict):
        candidates.extend(profile.values())
    return any("wa2000" in str(candidate or "").casefold() for candidate in candidates)


async def _refresh_divination_assets_from_api(pending, now, *, include_sources=True):
    identity_id = int((pending or {}).get("target_identity_id") or 0)
    if identity_id <= 0:
        return set()
    try:
        result = await refresh_storage_bag_records_from_api(
            identity_ids=_divination_api_refresh_identity_ids(identity_id, include_sources=include_sources),
            write_empty=True,
        )
    except Exception as exc:
        pending["status"] = "manual_required"
        pending["last_error"] = f"天机阁API读取失败：{str(exc)[:120]}"
        pending["api_checked_at"] = float(now or time.time())
        _record_pending(pending)
        await send_audit_log(
            f"🔮 卜筮神物资源检查失败：{mono(pending.get('target_item'))}｜天机阁API读取失败：{str(exc)[:120]}",
            scope="identity",
            send_as_id=identity_id,
            limit=320,
        )
        return set()

    refreshed_ids = _normalize_identity_id_set((result or {}).get("updated_identity_ids") or [])
    pending["api_checked_at"] = float(now or time.time())
    pending["api_refreshed_identity_ids"] = sorted(refreshed_ids)
    if identity_id not in refreshed_ids:
        pending["status"] = "manual_required"
        pending["last_error"] = "天机阁API未匹配目标身份库存"
        _record_pending(pending)
        await send_audit_log(
            f"🔮 卜筮神物资源检查失败：{mono(pending.get('target_item'))}｜天机阁API未匹配 {_format_identity(identity_id)}",
            scope="identity",
            send_as_id=identity_id,
            limit=300,
        )
        return set()
    return refreshed_ids


def _item_count(identity_id, item_name, refreshed_identity_ids=None):
    try:
        return int((_storage_items(identity_id, refreshed_identity_ids=refreshed_identity_ids).get(str(item_name or "").strip()) or 0))
    except (TypeError, ValueError):
        return 0


def _has_costs(identity_id, costs, refreshed_identity_ids=None):
    return all(_item_count(identity_id, item_name, refreshed_identity_ids=refreshed_identity_ids) >= int(count or 0) for item_name, count in (costs or {}).items())


def _missing_costs(identity_id, costs, refreshed_identity_ids=None):
    missing = {}
    for item_name, required in (costs or {}).items():
        shortage = int(required or 0) - _item_count(identity_id, item_name, refreshed_identity_ids=refreshed_identity_ids)
        if shortage > 0:
            missing[item_name] = shortage
    return missing


def _choose_listing_item(target_identity_id, costs, refreshed_identity_ids=None):
    items = _storage_items(target_identity_id, refreshed_identity_ids=refreshed_identity_ids)
    cost_names = {str(item or "").strip() for item in (costs or {}).keys()}
    for item_name in DIVINATION_LISTING_ITEM_CANDIDATES:
        if item_name not in cost_names and not _is_transfer_blocked_item(item_name) and int(items.get(item_name) or 0) > 0:
            return item_name
    for item_name, count in items.items():
        item_name = str(item_name or "").strip()
        if not item_name or item_name in cost_names or _is_transfer_blocked_item(item_name):
            continue
        try:
            if int(count or 0) > 0:
                return item_name
        except (TypeError, ValueError):
            continue
    return ""


def _build_transfer_tasks(target_identity_id, costs, refreshed_identity_ids=None):
    missing = _missing_costs(target_identity_id, costs, refreshed_identity_ids=refreshed_identity_ids)
    if not missing:
        return [], {}

    refreshed_identity_ids = refreshed_identity_ids if refreshed_identity_ids is None else set(refreshed_identity_ids)
    tasks_by_source = {}
    for source_id in get_identity_ids():
        try:
            source_id = int(source_id or 0)
        except (TypeError, ValueError):
            continue
        if (
            source_id <= 0
            or source_id == int(target_identity_id or 0)
            or not get_identity_enabled(source_id)
            or _is_protected_transfer_source(source_id)
            or (refreshed_identity_ids is not None and source_id not in refreshed_identity_ids)
        ):
            continue
        for item_name in list(missing.keys()):
            need = int(missing.get(item_name) or 0)
            if need <= 0:
                continue
            method = _storage_bag_transfer_method(item_name)
            if method == "blocked":
                continue
            available = _item_count(source_id, item_name, refreshed_identity_ids=refreshed_identity_ids)
            if available <= 0:
                continue
            take = min(need, available)
            tasks_by_source.setdefault(source_id, []).append({
                "item_name": item_name,
                "quantity": take,
                "method": method if method in {"basic", "gift"} else "basic",
            })
            missing[item_name] = need - take
            if missing[item_name] <= 0:
                missing.pop(item_name, None)
    tasks = [
        {"source_identity_id": source_id, "items": items}
        for source_id, items in tasks_by_source.items()
        if items
    ]
    return tasks, missing


def _format_costs(costs):
    return "、".join(f"{item}x{count}" for item, count in (costs or {}).items())


def _format_identity(identity_id):
    profile = get_send_as_profile(identity_id)
    return profile.get("label") or profile.get("username") or str(identity_id)


def _compact_result_text(text, *, limit=160):
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= int(limit or 160):
        return compact
    return compact[: max(0, int(limit or 160) - 1)].rstrip() + "…"


def _format_divination_result_summary(text):
    raw_text = str(text or "")
    treasure = parse_divination_treasure_text(raw_text)
    if treasure:
        summary = f"神物现世：{treasure.get('target_item') or '神物'}"
        costs = treasure.get("costs") if isinstance(treasure.get("costs"), dict) else {}
        if costs:
            summary += f"｜需 {_format_costs(costs)}"
        return summary
    hexagram = RE_DIVINATION_HEXAGRAM.search(raw_text)
    if hexagram:
        name = str(hexagram.group("bracket") or hexagram.group("gua") or "").strip()
        tail = _compact_result_text(raw_text.replace(hexagram.group(0), "", 1), limit=120)
        return f"卦象：{name}" + (f"｜{tail}" if tail else "")
    return _compact_result_text(raw_text, limit=180)


async def _send_divination_result_audit(identity_id, now, text):
    identity_id = int(identity_id or 0)
    if identity_id <= 0:
        return False
    record = (_run_records().get(_run_key(identity_id)) or {})
    record = record if isinstance(record, dict) else {}
    limit = get_divination_daily_limit(identity_id)
    count = _clamp_daily_count(record.get("count") or 0, limit)
    treasure = parse_divination_treasure_text(text)
    if not treasure:
        if count < int(limit or 0):
            return False
        day_key = get_day_key(now)
        if str(record.get("completion_audit_day") or "") == day_key:
            return False
        records = _run_records()
        key, latest, _changed = _get_run_record(identity_id, now, records=records, schedule_missing=False)
        _normalize_record_count_to_limit(latest, limit)
        count = max(count, _clamp_daily_count(latest.get("count") or 0, limit))
        latest["completion_audit_day"] = day_key
        latest["completion_audit_count"] = count
        records[key] = latest
        _set_run_records(records)
    await send_audit_log(
        (
            f"🔮 卜筮问天结果：{_format_identity(identity_id)}｜{_format_divination_result_summary(text)}｜已确认 {count}/{limit}"
            if treasure
            else f"🔮 卜筮问天完成：{_format_identity(identity_id)}｜今日 {count}/{limit} 次"
        ),
        scope="identity",
        send_as_id=identity_id,
        limit=520,
        priority="medium" if treasure else "low",
    )
    return True


def _format_pending_status(pending):
    pending = pending if isinstance(pending, dict) else {}
    target_item = str(pending.get("target_item") or "神物").strip()
    status = str(pending.get("status") or "pending").strip()
    status_label = DIVINATION_PENDING_STATUS_LABELS.get(status, status or "待处理")
    costs = pending.get("costs") if isinstance(pending.get("costs"), dict) else {}
    error = str(pending.get("last_error") or "").strip()
    parts = [f"{target_item}: {status_label}"]
    if costs:
        parts.append(f"需 {_format_costs(costs)}")
    if error:
        parts.append(error[:80])
    return "；".join(parts)


def _is_divination_enabled(identity_id):
    try:
        identity_id = int(identity_id or 0)
    except (TypeError, ValueError):
        identity_id = 0
    if identity_id <= 0:
        return False
    return bool(get_identity_enabled(identity_id) and get_identity_state(identity_id).get("divination_enabled", False))


def _is_manual_reply_context(reply_context):
    if not isinstance(reply_context, dict):
        return False
    return str(reply_context.get("source") or "").strip() == "manual_game_command"


async def _send_exchange(pending, now, *, reason="", refreshed_identity_ids=None, fail_on_shortage=True):
    pending = pending if isinstance(pending, dict) else {}
    identity_id = int(pending.get("target_identity_id") or 0)
    source_msg_id = int(pending.get("source_msg_id") or 0)
    key = str(pending.get("key") or "").strip()
    costs = pending.get("costs") if isinstance(pending.get("costs"), dict) else {}
    if not _is_supported_auto_exchange_target(pending.get("target_item")):
        pending["status"] = "manual_required"
        pending["last_error"] = "仅自动处理昆吾通行令"
        return False
    if identity_id <= 0 or source_msg_id <= 0 or not key:
        return False
    expires_at = float(pending.get("expires_at") or 0)
    if expires_at > 0 and now >= expires_at:
        pending["status"] = "expired"
        pending["last_error"] = "换取窗口已过期"
        return False
    if refreshed_identity_ids is None:
        refreshed_identity_ids = await _refresh_divination_assets_from_api(pending, now, include_sources=False)
        if not refreshed_identity_ids:
            return False
    refreshed_identity_ids = set(refreshed_identity_ids)
    if not _has_costs(identity_id, costs, refreshed_identity_ids=refreshed_identity_ids):
        shortage_text = _format_costs(_missing_costs(identity_id, costs, refreshed_identity_ids=refreshed_identity_ids))
        if fail_on_shortage:
            pending["status"] = "failed"
            pending["last_error"] = f"天机阁API确认资源不足：{shortage_text}"
        else:
            pending["last_error"] = f"天机阁API暂未确认资源满足：{shortage_text}"
        return False

    msg = await send_game_command(
        CMD_DIVINATION_EXCHANGE,
        track=True,
        reply_to=source_msg_id,
        send_as_id=identity_id,
        priority="urgent_reactive",
        max_retry=0,
        reply_timeout=120,
        source_module="卜筮问天",
        op_id=f"divination_exchange:{key}",
        chain_id=f"divination:{key}",
    )
    if not msg:
        pending["status"] = "failed"
        pending["last_error"] = "换取发送失败"
        return False
    pending["status"] = "exchange_sent"
    pending["exchange_msg_id"] = int(getattr(msg, "id", 0) or 0)
    pending["exchange_sent_at"] = float(now or time.time())
    pending["last_error"] = ""
    await send_audit_log(
        f"🔮 卜筮神物换取已发送：{mono(pending.get('target_item'))}｜{_format_identity(identity_id)}"
        + (f"｜{reason}" if reason else ""),
        scope="identity",
        send_as_id=identity_id,
        limit=260,
    )
    return True


def _record_pending(pending):
    records = _pending_records()
    records[str(pending.get("key") or "")] = dict(pending)
    _set_pending_records(records)


def _remove_pending(key):
    records = _pending_records()
    if records.pop(str(key or ""), None) is not None:
        _set_pending_records(records)
        return True
    return False


async def _start_resource_transfer(pending, now, *, refreshed_identity_ids=None):
    identity_id = int(pending.get("target_identity_id") or 0)
    costs = pending.get("costs") if isinstance(pending.get("costs"), dict) else {}
    if refreshed_identity_ids is None:
        refreshed_identity_ids = await _refresh_divination_assets_from_api(pending, now, include_sources=True)
        if not refreshed_identity_ids:
            return True
    refreshed_identity_ids = set(refreshed_identity_ids)
    requested_missing = _missing_costs(identity_id, costs, refreshed_identity_ids=refreshed_identity_ids)
    tasks, still_missing = _build_transfer_tasks(identity_id, costs, refreshed_identity_ids=refreshed_identity_ids)
    if still_missing:
        pending["status"] = "manual_required"
        pending["last_error"] = f"可用库存不足：{_format_costs(still_missing)}"
        pending["missing_costs"] = dict(still_missing)
        _record_pending(pending)
        await send_audit_log(
            f"🔮 卜筮神物待换取，但全体库存仍不足：{mono(pending.get('target_item'))}｜缺 {_format_costs(still_missing)}",
            scope="identity",
            send_as_id=identity_id,
            limit=320,
        )
        return True

    listing_item = _choose_listing_item(identity_id, costs, refreshed_identity_ids=refreshed_identity_ids)
    if not listing_item:
        pending["status"] = "manual_required"
        pending["last_error"] = "目标身份缺少可上架标记物"
        _record_pending(pending)
        await send_audit_log(
            f"🔮 卜筮神物待换取，但 {_format_identity(identity_id)} 缺少可上架标记物：{mono(pending.get('target_item'))}",
            scope="identity",
            send_as_id=identity_id,
            limit=300,
        )
        return True

    remaining = float(pending.get("expires_at") or 0) - float(now or time.time())
    if remaining < DIVINATION_TRANSFER_MIN_REMAINING_SEC:
        pending["status"] = "manual_required"
        pending["last_error"] = f"换取窗口剩余过短：{fmt_time_after(max(0, remaining))}"
        _record_pending(pending)
        return True

    ok, message, snapshot = await start_storage_bag_transfer_batch(
        tasks,
        target_identity_id=identity_id,
        listing_item=listing_item,
        stop_on_error=True,
    )
    if not ok:
        pending["status"] = "manual_required"
        pending["last_error"] = str(message or "储物袋转移启动失败")
        _record_pending(pending)
        await send_audit_log(
            f"🔮 卜筮神物换取资源转移未启动：{pending['last_error']}｜{mono(pending.get('target_item'))}",
            scope="identity",
            send_as_id=identity_id,
            limit=320,
        )
        return True
    pending["status"] = "transfer_running"
    pending["batch_id"] = str((snapshot or {}).get("batch", {}).get("batch_id") or (snapshot or {}).get("batch_id") or "")
    pending["listing_item"] = listing_item
    pending["missing_costs"] = dict(requested_missing)
    pending["last_error"] = ""
    _record_pending(pending)
    await send_audit_log(
        f"🔮 卜筮神物资源不足，已启动储物袋转移：{mono(pending.get('target_item'))}｜缺 {_format_costs(requested_missing)}",
        scope="identity",
        send_as_id=identity_id,
        limit=360,
    )
    return True


async def handle_divination_reply(text, now, event=None, reply_to=None, matched_family=None, reply_context=None):
    if matched_family and matched_family != "divination":
        return False
    raw_text = str(text or "")
    reply_context = reply_context if isinstance(reply_context, dict) else {}
    identity_id = int(reply_context.get("send_as_id") or 0)
    if identity_id <= 0:
        identity_id = _resolve_pending_query_identity_id(now, event=event, reply_to=reply_to, reply_context=reply_context)
    is_pending_query_reply = False
    if identity_id > 0:
        _key, current_record, _changed = _get_run_record(identity_id, now, schedule_missing=False)
        is_pending_query_reply = _matches_pending_query_reply(
            current_record,
            event=event,
            reply_to=reply_to,
            reply_context=reply_context,
        )
    if identity_id > 0 and _is_divination_enabled(identity_id):
        stop_reason = _is_daily_stop_text(raw_text)
        if stop_reason:
            _note_query_reply(identity_id, now, raw_text, event=event, final=True, stop_reason=stop_reason)
            await send_audit_log(
                f"🔮 卜筮问天今日停止：{stop_reason}｜{_format_identity(identity_id)}",
                scope="identity",
                send_as_id=identity_id,
                limit=240,
            )
            return True
        if _is_waiting_result_text(raw_text):
            _note_query_reply(identity_id, now, raw_text, event=event, final=False)
            return True

    treasure = parse_divination_treasure_text(raw_text)
    if not treasure:
        if identity_id > 0 and _is_divination_enabled(identity_id):
            if _extract_daily_count(raw_text) > 0 or is_pending_query_reply:
                _note_query_reply(identity_id, now, raw_text, event=event, final=True)
                await _send_divination_result_audit(identity_id, now, raw_text)
                return True
        return False
    if identity_id > 0 and _is_divination_enabled(identity_id):
        _note_query_reply(identity_id, now, raw_text, event=event, final=True)
        await _send_divination_result_audit(identity_id, now, raw_text)
    if not _is_supported_auto_exchange_target(treasure.get("target_item")):
        return True
    if identity_id <= 0:
        return True
    if not _is_divination_enabled(identity_id):
        return True
    if _is_manual_reply_context(reply_context):
        return True
    key = _make_pending_key(event, raw_text)
    records = _pending_records()
    existing = records.get(key)
    if isinstance(existing, dict) and float(existing.get("expires_at") or 0) > float(now or time.time()):
        return True

    source_msg_id = int(getattr(event, "id", 0) or 0)
    window_sec = int(treasure.get("window_sec") or DIVINATION_EXCHANGE_WINDOW_SEC)
    pending = {
        "key": key,
        "status": "created",
        "target_identity_id": identity_id,
        "target_item": treasure.get("target_item") or "",
        "costs": dict(treasure.get("costs") or {}),
        "source_msg_id": source_msg_id,
        "command_msg_id": int((reply_context or {}).get("reply_to_msg_id") or getattr(reply_to, "id", 0) or 0),
        "created_at": float(now or time.time()),
        "expires_at": float(now or time.time()) + window_sec,
    }
    records[key] = dict(pending)
    _set_pending_records(records)

    refreshed_identity_ids = await _refresh_divination_assets_from_api(pending, now, include_sources=True)
    if not refreshed_identity_ids:
        return True
    if _has_costs(identity_id, pending["costs"], refreshed_identity_ids=refreshed_identity_ids):
        await _send_exchange(pending, now, reason="天机阁API确认本号库存足够", refreshed_identity_ids=refreshed_identity_ids)
        _record_pending(pending)
        return True
    return await _start_resource_transfer(pending, now, refreshed_identity_ids=refreshed_identity_ids)


def _find_pending_for_exchange_reply(reply_to=None, reply_context=None):
    reply_msg_id = int((reply_context or {}).get("reply_to_msg_id") or getattr(reply_to, "id", 0) or 0)
    send_as_id = int((reply_context or {}).get("send_as_id") or 0)
    for key, pending in list(_pending_records().items()):
        if not isinstance(pending, dict):
            continue
        if send_as_id > 0 and int(pending.get("target_identity_id") or 0) != send_as_id:
            continue
        if reply_msg_id > 0 and int(pending.get("exchange_msg_id") or 0) == reply_msg_id:
            return str(key), pending
    return "", {}


async def handle_divination_exchange_reply(text, now, reply_to=None, matched_family=None, reply_context=None):
    raw_text = str(text or "")
    key, pending = _find_pending_for_exchange_reply(reply_to=reply_to, reply_context=reply_context)
    if matched_family and matched_family != "divination_exchange" and not pending:
        return False
    if not pending:
        return False
    identity_id = int(pending.get("target_identity_id") or 0)
    success_match = RE_DIVINATION_EXCHANGE_SUCCESS.search(raw_text)
    if success_match:
        costs = pending.get("costs") if isinstance(pending.get("costs"), dict) else {}
        deltas = {item_name: -int(count or 0) for item_name, count in costs.items()}
        target_item = str(success_match.group("target") or pending.get("target_item") or "").strip()
        if target_item:
            deltas[target_item] = int(deltas.get(target_item) or 0) + 1
        if deltas:
            apply_storage_bag_item_deltas(identity_id, deltas)
        _mark_exchange_success_today(identity_id, now, target_item or pending.get("target_item"))
        _remove_pending(key)
        close_action_guard_by_family("divination_exchange", send_as_id=identity_id, reason="success", now=now)
        await send_audit_log(
            f"🔮 卜筮神物换取成功：{mono(target_item or pending.get('target_item'))}｜{_format_identity(identity_id)}",
            scope="identity",
            send_as_id=identity_id,
            limit=260,
        )
        return True
    if RE_DIVINATION_EXCHANGE_EXPIRED.search(raw_text) or RE_DIVINATION_EXCHANGE_SHORTAGE.search(raw_text):
        records = _pending_records()
        pending["status"] = "failed"
        pending["last_error"] = raw_text[:160]
        records[key] = pending
        _set_pending_records(records)
        close_action_guard_by_family("divination_exchange", send_as_id=identity_id, reason="failed", now=now)
        return True
    return False


async def _run_divination_exchange_scheduler(now):
    now = float(now or time.time())
    records = _pending_records()
    if not records:
        return
    changed = False
    snapshot = get_storage_bag_transfer_snapshot()
    batch = snapshot.get("batch") if isinstance(snapshot, dict) else {}
    if not isinstance(batch, dict):
        batch = {}
    for key, pending in list(records.items()):
        if not isinstance(pending, dict):
            records.pop(key, None)
            changed = True
            continue
        expires_at = float(pending.get("expires_at") or 0)
        if expires_at > 0 and now >= expires_at:
            if pending.get("status") not in {"exchange_sent", "failed", "expired"}:
                await send_audit_log(
                    f"🔮 卜筮神物换取窗口已过期：{mono(pending.get('target_item'))}",
                    scope="identity",
                    send_as_id=int(pending.get("target_identity_id") or 0),
                    limit=240,
                )
            records.pop(key, None)
            changed = True
            continue
        if pending.get("status") != "transfer_running":
            continue
        batch_id = str(pending.get("batch_id") or "").strip()
        target_identity_id = int(pending.get("target_identity_id") or 0)
        costs = pending.get("costs") if isinstance(pending.get("costs"), dict) else {}
        if not _is_divination_enabled(target_identity_id):
            pending["status"] = "manual_required"
            pending["last_error"] = "卜筮问天模块已关闭"
            records[key] = dict(pending)
            changed = True
            continue
        if target_identity_id > 0 and _has_costs(target_identity_id, costs):
            await _send_exchange(
                pending,
                now,
                reason=f"天机阁API确认库存已满足，剩余 {fmt_remaining(expires_at)}",
                fail_on_shortage=False,
            )
            records[key] = dict(pending)
            changed = True
            if pending.get("status") == "exchange_sent":
                continue
            if pending.get("status") != "transfer_running":
                continue
        if batch_id and str(batch.get("batch_id") or "").strip() != batch_id:
            continue
        batch_status = str(batch.get("status") or "").strip()
        if bool(batch.get("running")):
            continue
        if batch_status == "done":
            await _send_exchange(pending, now, reason=f"资源转移完成，剩余 {fmt_remaining(expires_at)}")
            records[key] = dict(pending)
            changed = True
        elif batch_status == "failed":
            pending["status"] = "failed"
            pending["last_error"] = str(batch.get("last_message") or "储物袋批量转移失败")
            records[key] = dict(pending)
            changed = True
    if changed:
        _set_pending_records(records)


async def _send_divination_query(identity_id, record, now, limit):
    day_key = str(record.get("day_key") or get_day_key(now))
    observed_count = _clamp_daily_count(record.get("count") or 0, limit)
    if int(limit or 0) > 0 and observed_count >= int(limit or 0):
        records = _run_records()
        key, latest, _changed = _get_run_record(identity_id, now, records=records)
        _normalize_record_count_to_limit(latest, limit)
        _schedule_next_day(latest, now)
        records[key] = latest
        _set_run_records(records)
        return False
    target_count = observed_count + 1
    attempt_no = int(record.get("sent_attempts") or 0) + 1
    msg = await send_game_command(
        CMD_DIVINATION,
        track=True,
        send_as_id=identity_id,
        priority="normal",
        max_retry=0,
        reply_timeout=DIVINATION_QUERY_REPLY_TIMEOUT_SEC,
        source_module="卜筮问天",
        op_id=f"divination_query:{identity_id}:{day_key}:{target_count}:try{attempt_no}",
        chain_id=f"divination:{identity_id}:{day_key}",
    )

    records = _run_records()
    key, latest, _changed = _get_run_record(identity_id, now, records=records)
    if not msg:
        latest["last_error"] = "问天发送失败或被安全锁拦截"
        latest["next_query_at"] = float(now or time.time()) + DIVINATION_SEND_FAILURE_BACKOFF_SEC
        latest["phase"] = DIVINATION_PHASE_IDLE
        records[key] = latest
        _set_run_records(records)
        return False

    sent_at = float(getattr(msg, "sent_at", 0) or now or time.time())
    latest_attempt_no = int(latest.get("sent_attempts") or 0) + 1
    latest["pending_query_msg_id"] = int(getattr(msg, "id", 0) or 0)
    latest["pending_reply_msg_id"] = 0
    latest["pending_until"] = sent_at + DIVINATION_QUERY_REPLY_TIMEOUT_SEC
    latest["pending_count_recorded"] = False
    latest["phase"] = DIVINATION_PHASE_WAITING_INTERMEDIATE
    latest["sent_attempts"] = max(attempt_no, latest_attempt_no)
    latest["next_query_at"] = 0
    latest["last_sent_at"] = sent_at
    latest["last_error"] = ""
    latest["blocked_day"] = ""
    latest["block_reason"] = ""
    records[key] = latest
    _set_run_records(records)
    return True


async def _run_divination_query_scheduler(now):
    now = float(now or time.time())
    records = _run_records()
    changed = False
    for identity_id in get_identity_ids():
        try:
            identity_id = int(identity_id or 0)
        except (TypeError, ValueError):
            continue
        if identity_id <= 0:
            continue
        key, record, record_changed = _get_run_record(identity_id, now, records=records)
        enabled = _is_divination_enabled(identity_id)
        if record_changed and (enabled or key in records):
            records[key] = record
            changed = True
        if not enabled:
            if int(record.get("pending_query_msg_id") or 0) > 0:
                record["pending_query_msg_id"] = 0
                record["pending_reply_msg_id"] = 0
                record["pending_until"] = 0
                record["pending_count_recorded"] = False
                record["phase"] = DIVINATION_PHASE_IDLE
                record["last_error"] = "卜筮问天模块已关闭"
                records[key] = record
                changed = True
            continue

        limit = get_divination_daily_limit(identity_id)
        if _normalize_record_count_to_limit(record, limit):
            records[key] = record
            changed = True
        day_key = get_day_key(now)
        if str(record.get("blocked_day") or "") == day_key:
            continue
        if _has_exchange_success_today(record, now):
            if str(record.get("phase") or "") != DIVINATION_PHASE_DONE_TODAY or float(record.get("next_query_at") or 0) <= now:
                _schedule_next_day(record, now)
                records[key] = record
                changed = True
            continue

        pending_query_msg_id = int(record.get("pending_query_msg_id") or 0)
        next_query_at = float(record.get("next_query_at") or 0)
        force_log_sync = next_query_at > 0 and now >= next_query_at
        if pending_query_msg_id <= 0 and _sync_daily_count_from_message_log(identity_id, record, now, limit, force=force_log_sync):
            records[key] = record
            changed = True
        pending_until = float(record.get("pending_until") or 0)
        if pending_query_msg_id > 0:
            if pending_until > 0 and now >= pending_until:
                close_action_guard_by_family("divination", send_as_id=identity_id, reason="timeout", now=now)
                phase = _coerce_phase(record.get("phase"), record)
                if phase == DIVINATION_PHASE_WAITING_INTERMEDIATE or not record.get("pending_count_recorded"):
                    timeout_error = "等待问天中间态超时，未计入今日次数"
                else:
                    timeout_error = "等待问天最终结果超时"
                _schedule_after_round(record, now, limit)
                record["last_error"] = timeout_error
                records[key] = record
                changed = True
            continue

        if int(record.get("count") or 0) >= limit:
            if str(record.get("phase") or "") != DIVINATION_PHASE_DONE_TODAY:
                record["phase"] = DIVINATION_PHASE_DONE_TODAY
                records[key] = record
                changed = True
            if float(record.get("next_query_at") or 0) <= now:
                _schedule_next_day(record, now)
                records[key] = record
                changed = True
            continue
        if _identity_has_active_exchange(identity_id, now):
            continue

        if next_query_at <= 0:
            record["next_query_at"] = _next_same_day_start(now)
            record["phase"] = DIVINATION_PHASE_IDLE
            records[key] = record
            changed = True
            continue
        if now < next_query_at:
            continue

        if changed:
            _set_run_records(records)
            changed = False
            records = _run_records()
            key, record, _record_changed = _get_run_record(identity_id, now, records=records)
        await _send_divination_query(identity_id, record, now, limit)
        records = _run_records()

    if changed:
        _set_run_records(records)


async def run_divination_scheduler(now):
    now = float(now or time.time())
    await _run_divination_exchange_scheduler(now)
    await _run_divination_query_scheduler(now)


def get_divination_status_text(send_as_id=None):
    identity_id = int(send_as_id or get_current_identity_id() or 0)
    if identity_id <= 0:
        return "🔮 卜筮问天\n- 未选择身份"
    enabled = _is_divination_enabled(identity_id)
    limit = get_divination_daily_limit(identity_id)
    now = time.time()
    day_key = get_day_key(now)
    raw_record = (_run_records().get(_run_key(identity_id)) or {})
    record = raw_record if isinstance(raw_record, dict) and str(raw_record.get("day_key") or "") == day_key else {}
    count = _clamp_daily_count(record.get("count") or 0, limit)
    sent_attempts = int(record.get("sent_attempts") or 0)
    phase = _coerce_phase(record.get("phase"), record)
    pending_query_msg_id = int(record.get("pending_query_msg_id") or 0)
    pending_until = float(record.get("pending_until") or 0)
    next_query_at = float(record.get("next_query_at") or 0)
    blocked_day = str(record.get("blocked_day") or "")
    block_reason = str(record.get("block_reason") or record.get("last_error") or "").strip()
    last_error = str(record.get("last_error") or "").strip()
    exchanged_today = _has_exchange_success_today(record, now)
    exchange_target = str(record.get("exchange_success_target") or "神物").strip()
    pending_items = [
        pending
        for pending in _pending_records().values()
        if isinstance(pending, dict) and int(pending.get("target_identity_id") or 0) == identity_id
    ]
    lines = [
        "🔮 卜筮问天",
        f"- 开关: {'开启' if enabled else '关闭'}",
        f"- 次数: {limit}/日",
        f"- 今日已确认: {count}/{limit}",
        "- 模式: 自动问天 + 天机阁资源检查",
    ]
    if sent_attempts > 0:
        lines.append(f"- 今日发送尝试: {sent_attempts}")
    if enabled:
        if blocked_day == day_key:
            lines.append(f"- 状态: 今日停止（{block_reason or '已停止'}）")
        elif exchanged_today:
            lines.append(f"- 状态: 今日已换取 {exchange_target}，停止问天")
        elif pending_query_msg_id > 0:
            suffix = f"｜{fmt_remaining(pending_until)} 后超时" if pending_until > now else ""
            if phase == DIVINATION_PHASE_WAITING_INTERMEDIATE:
                lines.append(f"- 状态: 等待问天中间态{suffix}")
            else:
                lines.append(f"- 状态: 等待问天最终编辑{suffix}")
        elif count >= limit:
            lines.append("- 状态: 今日次数已完成")
        elif next_query_at > now:
            lines.append(f"- 下次问天: {fmt_abs_ts(next_query_at)}（{fmt_remaining(next_query_at)}）")
        elif next_query_at > 0:
            lines.append("- 状态: 已到点，等待下一轮调度发送")
        else:
            lines.append("- 状态: 等待下一轮调度初始化")
    if last_error:
        lines.append(f"- 最近提示: {last_error[:80]}")
    if pending_items:
        lines.append(f"- 待处理: {len(pending_items)}")
        pending_items.sort(key=lambda item: float(item.get("created_at") or item.get("expires_at") or 0), reverse=True)
        for pending in pending_items[:3]:
            lines.append(f"- {_format_pending_status(pending)}")
    return "\n".join(lines)


__all__ = [
    "get_divination_status_text",
    "handle_divination_exchange_reply",
    "handle_divination_reply",
    "parse_divination_treasure_text",
    "run_divination_scheduler",
]
