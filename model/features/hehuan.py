import copy
import json
import math
import os
import random
import re
import time
from datetime import datetime, timedelta

from ..config import CMD_HEHUAN_DUAL, GAME_TOPIC_ID, MESSAGES_DIR, TZ_LOCAL
from ..message_log_recovery import find_message_log_replies
from ..persistence import save_state
from ..runtime import get_last_game_send_block, send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_game_group_id,
    get_game_topic_id,
    get_identity_enabled,
    get_identity_ids,
    get_send_as_profile,
    is_module_available,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, has_wait_time, parse_wait_time


HEHUAN_CONTRACT_SEC = 7 * 24 * 3600
HEHUAN_HEART_SEAL_SEC = 3 * 24 * 3600
HEHUAN_WARM_OBSERVED_CD_SEC = 60 * 60
HEHUAN_CD_BUFFER_SEC = 60
HEHUAN_OBSERVATION_STALE_SEC = 8 * 24 * 3600
HEHUAN_AUTO_BLOCK_BACKOFF_SEC = 60 * 60
HEHUAN_AUTO_SEND_FAIL_BACKOFF_SEC = 30 * 60
HEHUAN_AUTO_RETRY_LIMIT = 5
HEHUAN_RETRY_MIN_INTERVAL_MIN = 1
HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN = 5
HEHUAN_RETRY_MAX_INTERVAL_MIN = 30
HEHUAN_REPLY_ANCHOR_MAX_AGE_SEC = 10 * 60
HEHUAN_VALUABLE_REMINDER_OFFSETS_SEC = (0, 3 * 3600, 6 * 3600)
HEHUAN_BAIJI_SEND_AS_ID = 301299112
HEHUAN_BAIJI_USERNAME = "jfdffdddd"
HEHUAN_BAIJI_NAME = "吧唧"
HEHUAN_ANCHOR_TEXT = "。"
HEHUAN_LOG_REPLAY_LOOKBACK_SEC = 15 * 60
HEHUAN_LOG_REPLAY_LOOKAHEAD_SEC = 30
HEHUAN_FINAL_EDIT_WAIT_SEC = 3 * 60
HEHUAN_UNSENT_BLOCK_CODES = {
    "send_queue_timeout",
    "send_prepare_timeout",
    "global_disabled",
    "dungeon_quiet",
    "account_offline",
    "account_client_missing",
    "account_client_not_ready",
    "account_session_error",
    "bot_health",
    "identity_weak",
    "pre_send_guard",
    "action_guard",
}

PATH_FANCHEN = "凡尘缘"
PATH_TONGCAN = "同参道"
PATH_MORAN = "魔染道"

RE_AT_NAME = re.compile(r"@[\w\d_]+")
RE_PARTNER = re.compile(r"你与\s*(?P<partner>@[\w\d_]+)")
RE_CONTRACT_DONE = re.compile(r"(?P<first>@[\w\d_]+)\s*与\s*(?P<second>@[\w\d_]+)\s*已成功缔结同参契印")
RE_GAIN_LINE = re.compile(
    r"(?P<name>@[\w\d_]+)\s*修为增加了\s*(?P<gain>\d+)\s*点(?:，并获得\s*(?P<contrib>\d+)\s*点宗门贡献)?"
)
RE_INSIGHT = re.compile(r"共同领悟了【(?P<item>[^】]+)】")
RE_FINAL_GAIN = re.compile(r"本次闭关，你的修为最终增加了\s*(?P<gain>\d+)\s*点")
RE_BASE_GAIN = re.compile(r"基础修为增加了\s*(?P<gain>\d+)\s*点")
RE_BONUS_GAIN = re.compile(r"因【合欢宗】灵脉加持，你额外获得了\s*(?P<gain>\d+)\s*点修为")

HEHUAN_OBSERVATION_TIME_KEYS = (
    "last_observed_at",
    "last_warm_success_at",
    "next_hehuan_time",
    "contract_until",
    "heart_seal_until",
    "auto_next_time",
    "auto_last_error_at",
    "auto_pending_sent_at",
    "auto_pending_deadline_at",
    "auto_anchor_requested_at",
)


def _is_empty_state_value(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_observation_float(value):
    if _is_empty_state_value(value):
        return 0.0, False
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0, True
    if not math.isfinite(parsed):
        return 0.0, True
    return parsed, False


def _dirty_hehuan_time_fields(value=None):
    if not isinstance(value, dict):
        return []
    dirty_fields = []
    for key in HEHUAN_OBSERVATION_TIME_KEYS:
        _parsed, dirty = _parse_observation_float(value.get(key, 0))
        if dirty:
            dirty_fields.append(key)
    return dirty_fields


def _default_hehuan_observation():
    return {
        "last_observed_at": 0,
        "last_path": "",
        "last_action": "",
        "last_result": "",
        "last_summary": "",
        "last_partner": "",
        "last_partner_identity_id": 0,
        "last_target": "",
        "last_error": "",
        "last_warm_success_at": 0,
        "next_hehuan_time": 0,
        "contract_until": 0,
        "heart_seal_until": 0,
        "last_gains": {},
        "last_contrib_gain": 0,
        "last_insight": "",
        "auto_next_time": 0,
        "auto_last_action": "",
        "auto_last_error": "",
        "auto_last_error_at": 0,
        "auto_retry_count": 0,
        "auto_retry_reason": "",
        "auto_retry_max_interval_min": HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN,
        "auto_pending_msg_id": 0,
        "auto_pending_sent_at": 0,
        "auto_pending_deadline_at": 0,
        "auto_reply_anchor_msg_id": 0,
        "auto_anchor_requested_at": 0,
        "valuable_drop_reminders": [],
        "recent": [],
    }


def normalize_hehuan_observation(value=None):
    observed = copy.deepcopy(_default_hehuan_observation())
    if isinstance(value, dict):
        observed.update(value)
    if not isinstance(observed.get("last_gains"), dict):
        observed["last_gains"] = {}
    if not isinstance(observed.get("recent"), list):
        observed["recent"] = []
    reminders = []
    for item in observed.get("valuable_drop_reminders", []):
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        for key in ("event_at", "next_reminder_at"):
            entry[key], _dirty = _parse_observation_float(entry.get(key, 0))
        try:
            entry["next_index"] = max(0, min(len(HEHUAN_VALUABLE_REMINDER_OFFSETS_SEC), int(entry.get("next_index", 0) or 0)))
        except (TypeError, ValueError, OverflowError):
            entry["next_index"] = 0
        for key in ("event_id", "item", "partner", "source"):
            entry[key] = str(entry.get(key) or "").strip()
        entry["done"] = bool(entry.get("done")) or entry["next_index"] >= len(HEHUAN_VALUABLE_REMINDER_OFFSETS_SEC)
        if entry["item"]:
            reminders.append(entry)
    observed["valuable_drop_reminders"] = reminders[-12:]
    recent = []
    for item in observed.get("recent", []):
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        entry["ts"], _dirty = _parse_observation_float(entry.get("ts", 0))
        recent.append(entry)
    observed["recent"] = recent[-8:]
    for key in HEHUAN_OBSERVATION_TIME_KEYS:
        observed[key], _dirty = _parse_observation_float(observed.get(key, 0))
    try:
        observed["last_contrib_gain"] = int(observed.get("last_contrib_gain", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        observed["last_contrib_gain"] = 0
    for key in ("last_partner_identity_id", "auto_retry_count", "auto_pending_msg_id", "auto_reply_anchor_msg_id"):
        try:
            observed[key] = max(0, int(observed.get(key, 0) or 0))
        except (TypeError, ValueError, OverflowError):
            observed[key] = 0
    try:
        observed["auto_retry_max_interval_min"] = max(
            HEHUAN_RETRY_MIN_INTERVAL_MIN,
            min(HEHUAN_RETRY_MAX_INTERVAL_MIN, int(observed.get("auto_retry_max_interval_min", HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN) or HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN)),
        )
    except (TypeError, ValueError, OverflowError):
        observed["auto_retry_max_interval_min"] = HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN
    auto_error = str(observed.get("auto_last_error") or "").strip()
    last_result = str(observed.get("last_result") or "").strip().lower()
    if not auto_error:
        observed["auto_last_error_at"] = 0
    elif (
        last_result == "success"
        and int(observed.get("auto_pending_msg_id", 0) or 0) <= 0
        and int(observed.get("auto_retry_count", 0) or 0) <= 0
        and (
            float(observed.get("auto_last_error_at", 0) or 0) <= 0
            or float(observed.get("auto_last_error_at", 0) or 0) <= float(observed.get("last_observed_at", 0) or 0)
        )
    ):
        observed["auto_last_error"] = ""
        observed["auto_last_error_at"] = 0
    return observed


def _reset_hehuan_auto_pending(observed):
    observed["auto_pending_msg_id"] = 0
    observed["auto_pending_sent_at"] = 0
    observed["auto_pending_deadline_at"] = 0
    observed["auto_reply_anchor_msg_id"] = 0


def _reset_hehuan_retry(observed):
    observed["auto_retry_count"] = 0
    observed["auto_retry_reason"] = ""
    _reset_hehuan_auto_pending(observed)


def _hehuan_retry_delay_sec(observed):
    max_min = int((observed or {}).get("auto_retry_max_interval_min", HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN) or HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN)
    max_min = max(HEHUAN_RETRY_MIN_INTERVAL_MIN, min(HEHUAN_RETRY_MAX_INTERVAL_MIN, max_min))
    min_sec = HEHUAN_RETRY_MIN_INTERVAL_MIN * 60
    max_sec = max_min * 60
    if max_sec <= min_sec:
        return float(min_sec)
    return float(random.uniform(min_sec, max_sec))


def _schedule_hehuan_retry(observed, now, reason):
    observed = normalize_hehuan_observation(observed)
    retry_count = int(observed.get("auto_retry_count", 0) or 0)
    if retry_count >= HEHUAN_AUTO_RETRY_LIMIT:
        _reset_hehuan_auto_pending(observed)
        observed["auto_last_action"] = "warm"
        observed["auto_last_error"] = f"{reason}，补发已达 {HEHUAN_AUTO_RETRY_LIMIT} 次上限"
        observed["auto_last_error_at"] = float(now)
        observed["auto_next_time"] = float(now + HEHUAN_AUTO_BLOCK_BACKOFF_SEC)
        return observed
    observed["auto_retry_count"] = retry_count + 1
    observed["auto_retry_reason"] = str(reason or "retry")
    observed["auto_last_action"] = "warm"
    observed["auto_last_error"] = str(reason or "")
    observed["auto_last_error_at"] = float(now) if observed["auto_last_error"] else 0
    observed["auto_next_time"] = float(now + _hehuan_retry_delay_sec(observed))
    _reset_hehuan_auto_pending(observed)
    return observed


def _mark_hehuan_pending_ack(observed, now):
    observed = normalize_hehuan_observation(observed)
    pending_msg_id = int(observed.get("auto_pending_msg_id", 0) or 0)
    if pending_msg_id > 0:
        pending_sent_at = float(observed.get("auto_pending_sent_at", 0) or 0)
        if pending_sent_at <= 0:
            observed["auto_pending_sent_at"] = float(now)
        pending_deadline_at = float(observed.get("auto_pending_deadline_at", 0) or 0)
        observed["auto_pending_deadline_at"] = max(
            pending_deadline_at,
            float(now + HEHUAN_FINAL_EDIT_WAIT_SEC),
        )
        observed["auto_next_time"] = observed["auto_pending_deadline_at"]
    else:
        observed["auto_next_time"] = float(now + HEHUAN_FINAL_EDIT_WAIT_SEC)
    observed["auto_last_error"] = "温养已收到起手回复，等待最终结算"
    observed["auto_last_error_at"] = 0
    return observed


def _latest_hehuan_pending_warm_at(observed):
    latest = 0.0
    for item in (observed or {}).get("recent") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        result = str(item.get("result") or "").strip().lower()
        if action != "双修 温养" or result != "pending":
            continue
        try:
            ts_value = float(item.get("ts") or 0)
        except (TypeError, ValueError, OverflowError):
            ts_value = 0.0
        latest = max(latest, ts_value)
    return latest


def _hehuan_pending_consumed_base_at(observed):
    observed = normalize_hehuan_observation(observed)
    if str(observed.get("last_result") or "").strip().lower() != "pending":
        return 0.0
    return max(
        float(observed.get("last_observed_at", 0) or 0),
        float(observed.get("auto_pending_sent_at", 0) or 0),
        _latest_hehuan_pending_warm_at(observed),
    )


def _assume_hehuan_pending_consumed(observed, now):
    now = float(now if now is not None else time.time())
    observed = normalize_hehuan_observation(observed)
    base_at = _hehuan_pending_consumed_base_at(observed)
    if base_at <= 0:
        return observed, False
    final_wait_until = float(base_at + HEHUAN_FINAL_EDIT_WAIT_SEC)
    if now < final_wait_until:
        observed["auto_next_time"] = final_wait_until
        observed["auto_last_action"] = "warm"
        observed["auto_last_error"] = "温养已收到起手回复，等待最终结算"
        observed["auto_last_error_at"] = 0
        return observed, True
    cooldown_until = float(base_at + HEHUAN_WARM_OBSERVED_CD_SEC + HEHUAN_CD_BUFFER_SEC)
    observed["last_result"] = "assumed_consumed"
    observed["last_summary"] = "温养起手已确认，最终结算未入库，按已消费保守冷却"
    observed["next_hehuan_time"] = max(float(observed.get("next_hehuan_time", 0) or 0), cooldown_until)
    observed["auto_next_time"] = observed["next_hehuan_time"] if observed["next_hehuan_time"] > now else float(now)
    observed["auto_last_action"] = "warm"
    observed["auto_last_error"] = "温养起手已确认但最终编辑未入库，已按起手+1小时保守冷却"
    observed["auto_last_error_at"] = float(now)
    observed["auto_retry_count"] = 0
    observed["auto_retry_reason"] = ""
    _reset_hehuan_auto_pending(observed)
    observed["recent"].append({
        "ts": now,
        "path": observed.get("last_path") or PATH_TONGCAN,
        "action": "双修 温养",
        "result": "assumed_consumed",
        "summary": observed["last_summary"],
    })
    observed["recent"] = observed["recent"][-8:]
    return observed, observed["next_hehuan_time"] > now


def _block_hehuan_until(observed, until_ts, reason, now=None):
    now = float(now if now is not None else time.time())
    observed = normalize_hehuan_observation(observed)
    observed["auto_retry_count"] = 0
    observed["auto_retry_reason"] = ""
    observed["auto_last_action"] = "warm"
    observed["auto_last_error"] = str(reason or "")
    observed["auto_last_error_at"] = float(now) if observed["auto_last_error"] else 0
    observed["next_hehuan_time"] = float(until_ts)
    observed["auto_next_time"] = float(until_ts)
    _reset_hehuan_auto_pending(observed)
    return observed


def _hehuan_valuable_items_from_parsed(parsed):
    items = []
    insight = str((parsed or {}).get("last_insight") or "").strip()
    if insight and insight not in {"修为", "宗门贡献"}:
        items.append(insight)
    return items


def _queue_hehuan_valuable_drop_reminders(observed, parsed, now):
    observed = normalize_hehuan_observation(observed)
    items = _hehuan_valuable_items_from_parsed(parsed)
    if not items:
        return observed
    reminders = list(observed.get("valuable_drop_reminders") or [])
    existing_ids = {str(item.get("event_id") or "") for item in reminders if isinstance(item, dict)}
    partner = str((parsed or {}).get("partner") or "").strip()
    event_minute = int(float(now or 0) // 60)
    for item in items:
        event_id = f"hehuan-warm:{partner}:{item}:{event_minute}"
        if event_id in existing_ids:
            continue
        reminders.append({
            "event_id": event_id,
            "source": "合欢双修温养",
            "item": item,
            "partner": partner,
            "event_at": float(now),
            "next_index": 0,
            "next_reminder_at": float(now),
            "done": False,
        })
        existing_ids.add(event_id)
    observed["valuable_drop_reminders"] = reminders[-12:]
    return observed


def _format_hehuan_valuable_reminder(event, index):
    item = str((event or {}).get("item") or "").strip() or "未解析物品"
    partner = str((event or {}).get("partner") or "").strip() or "未解析道侣"
    labels = ("即时", "+3h", "+6h")
    index = int(index or 0)
    label = labels[index] if 0 <= index < len(labels) else "补发"
    return f"🌸 合欢温养出货提醒（第{index + 1}/3次，{label}）：{item}｜道侣 {partner}"


async def _run_hehuan_valuable_drop_reminders(observed, now):
    observed = normalize_hehuan_observation(observed)
    reminders = list(observed.get("valuable_drop_reminders") or [])
    changed = False
    sent_any = False
    for event in reminders:
        if not isinstance(event, dict) or event.get("done"):
            continue
        next_index = int(event.get("next_index", 0) or 0)
        if next_index >= len(HEHUAN_VALUABLE_REMINDER_OFFSETS_SEC):
            event["done"] = True
            changed = True
            continue
        due_at = float(event.get("next_reminder_at", 0) or 0)
        if due_at <= 0:
            event_at = float(event.get("event_at", now) or now)
            due_at = event_at + HEHUAN_VALUABLE_REMINDER_OFFSETS_SEC[next_index]
            event["next_reminder_at"] = float(due_at)
            changed = True
        if float(now) < due_at or sent_any:
            continue
        ok = await send_audit_log(
            _format_hehuan_valuable_reminder(event, next_index),
            scope="identity",
            priority="high",
            limit=260,
        )
        if not ok:
            event["next_reminder_at"] = float(now + 5 * 60)
            event["last_error"] = "日志提醒发送失败，5分钟后重试"
            changed = True
            sent_any = True
            continue
        next_index += 1
        event["next_index"] = next_index
        event["last_error"] = ""
        if next_index >= len(HEHUAN_VALUABLE_REMINDER_OFFSETS_SEC):
            event["done"] = True
            event["next_reminder_at"] = 0
        else:
            event["next_reminder_at"] = float(event.get("event_at", now) or now) + HEHUAN_VALUABLE_REMINDER_OFFSETS_SEC[next_index]
        changed = True
        sent_any = True
    if changed:
        observed["valuable_drop_reminders"] = reminders[-12:]
    return changed, observed, sent_any


def set_hehuan_retry_max_interval_min(value, now=None):
    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN
    observed["auto_retry_max_interval_min"] = max(HEHUAN_RETRY_MIN_INTERVAL_MIN, min(HEHUAN_RETRY_MAX_INTERVAL_MIN, parsed))
    state["hehuan_observation"] = observed
    return observed["auto_retry_max_interval_min"]


def _parse_message_log_ts(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    if text.endswith(" UTC+8"):
        text = text[:-6].strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _recent_message_log_paths(now, days=2):
    base = datetime.fromtimestamp(float(now or time.time()), TZ_LOCAL)
    return [
        os.path.join(MESSAGES_DIR, f"{(base - timedelta(days=offset)).strftime('%Y-%m-%d')}.log")
        for offset in range(max(1, int(days or 1)))
    ]


def _read_message_log_tail(path, *, max_lines=5000, max_bytes=512 * 1024):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - int(max_bytes or 0))
            handle.seek(start)
            if start > 0:
                handle.readline()
            data = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    return data.splitlines()[-max(1, int(max_lines or 1)):]


def _normalize_at_name(value):
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith("@") else f"@{text}"


def _identity_matches_name(identity_id, name):
    target = str(name or "").strip().lstrip("@").lower()
    if not target:
        return False
    profile = get_send_as_profile(identity_id) or {}
    values = {
        str(profile.get("username") or "").strip().lstrip("@").lower(),
        str(profile.get("label") or "").strip().lstrip("@").lower(),
        str(profile.get("daohao") or "").strip().lstrip("@").lower(),
    }
    values.update(
        str(alias or "").strip().lstrip("@").lower()
        for alias in profile.get("username_aliases", [])
    )
    return target in values


def _current_hehuan_partner_from_names(first, second):
    first = _normalize_at_name(first)
    second = _normalize_at_name(second)
    current_id = get_current_identity_id()
    if _identity_matches_name(current_id, first):
        return second
    if _identity_matches_name(current_id, second):
        return first
    return second or first


def _is_named_log_entry(payload, target_name="", target_id=0):
    try:
        sender_id = int(payload.get("sender_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        sender_id = 0
    username = str(payload.get("sender_username") or "").strip().lstrip("@").lower()
    sender_name = str(payload.get("sender_name") or "").strip()
    if int(target_id or 0) > 0 and sender_id == int(target_id or 0):
        return True
    target = str(target_name or "").strip().lstrip("@").lower()
    if target and username == target:
        return True
    if target and sender_name.strip().lstrip("@").lower() == target:
        return True
    return False


def _is_baiji_log_entry(payload):
    sender_name = str((payload or {}).get("sender_name") or "").strip()
    return (
        _is_named_log_entry(payload, HEHUAN_BAIJI_USERNAME, HEHUAN_BAIJI_SEND_AS_ID)
        or sender_name == HEHUAN_BAIJI_NAME
    )


def _is_game_topic_entry(payload):
    topic_id = int(get_game_topic_id() or GAME_TOPIC_ID or 0)
    if topic_id <= 0:
        return False
    try:
        payload_topic_id = int(payload.get("topic_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        payload_topic_id = 0
    try:
        reply_to_msg_id = int(payload.get("reply_to_msg_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        reply_to_msg_id = 0
    return payload_topic_id == topic_id or reply_to_msg_id == topic_id


def _is_baiji_anchor_candidate(payload):
    text = str((payload or {}).get("text") or "").strip()
    if not text:
        return False
    return not text.startswith(".")


def _resolve_identity_id_by_at_name(name):
    target = str(name or "").strip().lstrip("@").lower()
    if not target:
        return 0
    fallback = 0
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        profile = get_send_as_profile(identity_id) or {}
        username = str(profile.get("username") or "").strip().lstrip("@").lower()
        label = str(profile.get("label") or "").strip().lstrip("@").lower()
        daohao = str(profile.get("daohao") or "").strip().lstrip("@").lower()
        aliases = {
            str(alias or "").strip().lstrip("@").lower()
            for alias in profile.get("username_aliases", [])
        }
        if username == target:
            return int(identity_id or 0)
        if target in aliases:
            return int(identity_id or 0)
        if label == target or daohao == target:
            fallback = int(identity_id or 0) or fallback
    return fallback


def find_recent_hehuan_partner_anchor_msg_id(
    partner="",
    now=None,
    *,
    max_age_sec=HEHUAN_REPLY_ANCHOR_MAX_AGE_SEC,
    target_id=0,
    require_game_topic=True,
):
    partner = _normalize_at_name(partner)
    if not partner:
        return 0
    now = float(now if now is not None else time.time())
    min_ts = now - max(1, int(max_age_sec or HEHUAN_REPLY_ANCHOR_MAX_AGE_SEC))
    game_group_id = int(get_game_group_id() or 0)
    partner_id = int(target_id or 0) or _resolve_identity_id_by_at_name(partner)
    for path in _recent_message_log_paths(now):
        if not os.path.exists(path):
            continue
        for line in reversed(_read_message_log_tail(path)):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("event_type") or "") not in {"message", "sent"}:
                continue
            if game_group_id and int(payload.get("chat_id", 0) or 0) != game_group_id:
                continue
            if require_game_topic and not _is_game_topic_entry(payload):
                continue
            if not _is_named_log_entry(payload, partner, partner_id):
                continue
            if not _is_baiji_anchor_candidate(payload):
                continue
            msg_ts = _parse_message_log_ts(payload.get("ts"))
            if msg_ts <= 0 or msg_ts < min_ts or msg_ts > now + 60:
                continue
            try:
                msg_id = int(payload.get("message_id", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                msg_id = 0
            if msg_id > 0:
                return msg_id
    return 0


def find_recent_baiji_anchor_msg_id(now=None, *, max_age_sec=HEHUAN_REPLY_ANCHOR_MAX_AGE_SEC):
    return find_recent_hehuan_partner_anchor_msg_id(
        HEHUAN_BAIJI_USERNAME,
        now=now,
        max_age_sec=max_age_sec,
        target_id=HEHUAN_BAIJI_SEND_AS_ID,
        require_game_topic=True,
    )


def _is_baiji_partner_name(name):
    target = str(name or "").strip().lstrip("@").lower()
    if not target:
        return False
    return target in {
        str(HEHUAN_BAIJI_USERNAME or "").strip().lstrip("@").lower(),
        str(HEHUAN_BAIJI_NAME or "").strip().lstrip("@").lower(),
    }


def find_baiji_identity_id():
    fallback = 0
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        profile = get_send_as_profile(identity_id) or {}
        try:
            candidate_id = int(identity_id or 0)
        except (TypeError, ValueError, OverflowError):
            candidate_id = 0
        username = str(profile.get("username") or "").strip().lstrip("@").lower()
        label = str(profile.get("label") or "").strip()
        daohao = str(profile.get("daohao") or "").strip()
        if candidate_id == HEHUAN_BAIJI_SEND_AS_ID:
            return candidate_id
        if username == HEHUAN_BAIJI_USERNAME.lower():
            fallback = candidate_id
        elif label == HEHUAN_BAIJI_NAME or daohao == HEHUAN_BAIJI_NAME:
            fallback = candidate_id or fallback
    return fallback


def _cooldown_matches_current_identity(observed):
    target = str((observed or {}).get("last_target") or "").strip().lstrip("@").lower()
    if not target:
        return False
    profile = get_send_as_profile(get_current_identity_id()) or {}
    username = str(profile.get("username") or "").strip().lstrip("@").lower()
    return bool(username and target == username)


def looks_like_hehuan_text(text):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    if "【入梦成功】" in raw_text:
        return False
    if all(keyword in raw_text for keyword in ("凡尘缘", "同参道", "魔染道")):
        return True
    if "【温养双修" in raw_text or "契印感应" in raw_text:
        return True
    if "无法进行双修" in raw_text or "道友若欲双修" in raw_text:
        return True
    if "闭关双修" in raw_text or ".双修 温养" in raw_text or ".双修 采补" in raw_text:
        return True
    if "种下心印" in raw_text or "挣脱心印" in raw_text:
        return True
    if "心印" in raw_text and any(keyword in raw_text for keyword in ("炉鼎", "采补", "拘我", "挣脱")):
        return True
    if "炉鼎" in raw_text and any(keyword in raw_text for keyword in ("玩物", "拘我", "采补", "沦为")):
        return True
    if "【闭关成功】" in raw_text and "因【合欢宗】灵脉加持" in raw_text:
        return True
    if "合欢宗" in raw_text and any(keyword in raw_text for keyword in ("双修", "同参", "心印", "采补")):
        return True
    return False


def _short_summary(text, limit=80):
    raw_text = " / ".join(part.strip() for part in str(text or "").splitlines() if part.strip())
    return raw_text[: int(limit or 80)]


def _extract_wait_until(text, now):
    if has_wait_time(text):
        wait_sec = parse_wait_time(text)
        if wait_sec > 0:
            return float(now + wait_sec + HEHUAN_CD_BUFFER_SEC)
    return 0


def _extract_warm_success(text, now):
    gains = {}
    contrib_gain = 0
    for match in RE_GAIN_LINE.finditer(text):
        name = str(match.group("name") or "").strip()
        if not name:
            continue
        gains[name] = int(match.group("gain") or 0)
        if match.group("contrib"):
            contrib_gain += int(match.group("contrib") or 0)
    partner_match = RE_PARTNER.search(text)
    insight_match = RE_INSIGHT.search(text)
    return {
        "path": PATH_TONGCAN,
        "action": "双修 温养",
        "result": "success",
        "summary": "温养双修成功",
        "partner": partner_match.group("partner") if partner_match else "",
        "target": "",
        "next_hehuan_time": float(now + HEHUAN_WARM_OBSERVED_CD_SEC),
        "contract_until": float(now + HEHUAN_CONTRACT_SEC),
        "heart_seal_until": 0,
        "last_gains": gains,
        "last_contrib_gain": contrib_gain,
        "last_insight": insight_match.group("item").strip() if insight_match else "",
        "error": "",
    }


def parse_hehuan_text(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    raw_text = str(text or "").strip()
    family = str(family or "").strip()
    if not raw_text:
        return None

    contract_match = RE_CONTRACT_DONE.search(raw_text)
    if contract_match:
        partner = _current_hehuan_partner_from_names(contract_match.group("first"), contract_match.group("second"))
        return {
            "path": PATH_TONGCAN,
            "action": "缔结同参",
            "result": "contract_success",
            "summary": "同参契印已成",
            "partner": partner,
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": float(now + HEHUAN_CONTRACT_SEC),
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "",
        }
    if "【温养双修" in raw_text:
        return _extract_warm_success(raw_text, now)
    if "契印感应" in raw_text and "温养双修" in raw_text:
        return {
            "path": PATH_TONGCAN,
            "action": "双修 温养",
            "result": "pending",
            "summary": "契印感应，温养双修结算中",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": float(now + HEHUAN_CONTRACT_SEC),
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "",
        }
    if "心神尚未恢复" in raw_text and "无法进行双修" in raw_text:
        names = RE_AT_NAME.findall(raw_text)
        return {
            "path": PATH_TONGCAN if family == "hehuan_dual" or "温养" in family else "",
            "action": "双修",
            "result": "cooldown",
            "summary": "双修冷却中",
            "partner": "",
            "target": names[0] if names else "",
            "next_hehuan_time": _extract_wait_until(raw_text, now),
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "心神尚未恢复",
        }
    if "双方或其中一方尚未踏入仙途" in raw_text and "无法进行双修" in raw_text:
        return {
            "path": PATH_FANCHEN if family == "hehuan_retreat" else PATH_TONGCAN if family == "hehuan_dual" else "",
            "action": "双修",
            "result": "realm_blocked",
            "summary": "双修失败：尚未踏入仙途",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "双方或其中一方尚未踏入仙途",
        }
    if "对方并非你的同参道侣" in raw_text and "无法进行灵力交融" in raw_text:
        return {
            "path": PATH_TONGCAN,
            "action": "双修 温养",
            "result": "contract_invalid",
            "summary": "温养失败：非同参道侣",
            "partner": "",
            "target": "",
            "next_hehuan_time": float(now + HEHUAN_AUTO_BLOCK_BACKOFF_SEC),
            "contract_until": -1,
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "对方并非你的同参道侣",
        }
    if (
        "道友若欲双修" in raw_text
        or ("合欢宗" in raw_text and "双修、同参、心印与采补" in raw_text)
        or all(keyword in raw_text for keyword in ("凡尘缘", "同参道", "魔染道"))
    ):
        return {
            "path": "指南",
            "action": "玩法指南",
            "result": "guide",
            "summary": "合欢宗三层玩法说明",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "",
        }
    if "对方只是凡人" in raw_text and "种下心印" in raw_text:
        return {
            "path": PATH_MORAN,
            "action": "种下心印",
            "result": "invalid_target",
            "summary": "种下心印失败：对方只是凡人",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "对方只是凡人",
        }
    if "炉鼎" in raw_text and any(keyword in raw_text for keyword in ("拘我", "玩物", "沦为")):
        is_controlled = "沦为炉鼎" in raw_text or "炉鼎玩物" in raw_text
        return {
            "path": PATH_MORAN,
            "action": "心印/炉鼎",
            "result": "controlled" if is_controlled else "challenged",
            "summary": "炉鼎文案已观察",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": 0,
            "heart_seal_until": float(now + HEHUAN_HEART_SEAL_SEC) if is_controlled else 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "",
        }
    if "【闭关成功】" in raw_text and "因【合欢宗】灵脉加持" in raw_text:
        final_match = RE_FINAL_GAIN.search(raw_text)
        base_match = RE_BASE_GAIN.search(raw_text)
        bonus_match = RE_BONUS_GAIN.search(raw_text)
        gains = {}
        if base_match:
            gains["基础"] = int(base_match.group("gain") or 0)
        if bonus_match:
            gains["合欢宗加成"] = int(bonus_match.group("gain") or 0)
        if final_match:
            gains["最终"] = int(final_match.group("gain") or 0)
        return {
            "path": PATH_FANCHEN,
            "action": "闭关双修",
            "result": "success",
            "summary": "闭关成功，合欢宗灵脉加持",
            "partner": "",
            "target": "",
            "next_hehuan_time": _extract_wait_until(raw_text, now),
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": gains,
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "",
        }
    if not looks_like_hehuan_text(raw_text):
        return None
    return {
        "path": "",
        "action": "未知合欢宗文案",
        "result": "observed",
        "summary": _short_summary(raw_text),
        "partner": "",
        "target": "",
        "next_hehuan_time": 0,
        "contract_until": 0,
        "heart_seal_until": 0,
        "last_gains": {},
        "last_contrib_gain": 0,
        "last_insight": "",
        "error": "",
    }


def apply_hehuan_passive(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    parsed = parse_hehuan_text(text, now=now, family=family)
    if not parsed:
        return False

    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    previous_partner = str(observed.get("last_partner") or "").strip()
    previous_contract_until = float(observed.get("contract_until", 0) or 0)
    previous_success_at = float(observed.get("last_warm_success_at", 0) or 0)
    result = str(parsed.get("result") or "").strip().lower()
    parsed_partner = str(parsed.get("partner") or "").strip()
    if (
        result == "pending"
        and previous_success_at > 0
        and now <= previous_success_at + HEHUAN_FINAL_EDIT_WAIT_SEC
        and int(observed.get("auto_pending_msg_id", 0) or 0) <= 0
    ):
        return True
    observed["last_observed_at"] = now
    observed["last_path"] = parsed.get("path") or observed.get("last_path", "")
    observed["last_action"] = parsed.get("action") or ""
    observed["last_result"] = parsed.get("result") or ""
    observed["last_summary"] = parsed.get("summary") or _short_summary(text)
    if parsed_partner:
        observed["last_partner"] = parsed_partner
        observed["last_partner_identity_id"] = _resolve_identity_id_by_at_name(parsed_partner)
    elif result in {"contract_invalid", "cooldown", "pending"} and previous_partner:
        observed["last_partner"] = previous_partner
    else:
        observed["last_partner"] = ""
        observed["last_partner_identity_id"] = 0
    observed["last_target"] = parsed.get("target") or ""
    observed["last_error"] = parsed.get("error") or ""
    action = str(parsed.get("action") or "").strip()
    if parsed.get("next_hehuan_time"):
        observed["next_hehuan_time"] = float(parsed.get("next_hehuan_time") or 0)
    if parsed.get("contract_until"):
        parsed_contract_until = float(parsed.get("contract_until") or 0)
        if result == "contract_invalid" and previous_partner:
            fallback_until = previous_contract_until
            if fallback_until <= now and previous_success_at > 0:
                fallback_until = previous_success_at + HEHUAN_CONTRACT_SEC
            observed["contract_until"] = max(0.0, fallback_until)
        else:
            observed["contract_until"] = max(0.0, parsed_contract_until)
    if parsed.get("heart_seal_until"):
        observed["heart_seal_until"] = float(parsed.get("heart_seal_until") or 0)
    observed["last_gains"] = parsed.get("last_gains") if isinstance(parsed.get("last_gains"), dict) else {}
    observed["last_contrib_gain"] = int(parsed.get("last_contrib_gain", 0) or 0)
    observed["last_insight"] = parsed.get("last_insight") or ""
    auto_next_handled = False
    if result == "success" and action == "双修 温养":
        observed["last_warm_success_at"] = now
        observed["next_hehuan_time"] = float(now + HEHUAN_WARM_OBSERVED_CD_SEC)
        observed["auto_next_time"] = observed["next_hehuan_time"]
        observed["auto_last_error"] = ""
        observed["auto_last_error_at"] = 0
        _reset_hehuan_retry(observed)
        observed = _queue_hehuan_valuable_drop_reminders(observed, parsed, now)
        auto_next_handled = True
    elif result == "contract_success":
        observed["next_hehuan_time"] = 0
        observed["auto_next_time"] = float(now)
        observed["auto_last_error"] = ""
        observed["auto_last_error_at"] = 0
        _reset_hehuan_retry(observed)
        auto_next_handled = True
    elif result == "cooldown":
        parsed_next_time = float(parsed.get("next_hehuan_time") or 0)
        last_success_at = float(observed.get("last_warm_success_at", 0) or 0)
        latest_pending_at = _latest_hehuan_pending_warm_at(observed)
        latest_consumed_at = max(last_success_at, latest_pending_at)
        if parsed_next_time > 0:
            observed["next_hehuan_time"] = parsed_next_time
            observed["auto_next_time"] = max(parsed_next_time, now + 60)
            observed["auto_last_error"] = "心神尚未恢复，已按真实等待时间校准"
            observed["auto_last_error_at"] = float(now)
            _reset_hehuan_retry(observed)
        elif latest_consumed_at > 0:
            corrected_next_time = float(latest_consumed_at + HEHUAN_WARM_OBSERVED_CD_SEC)
            if corrected_next_time > now:
                observed["next_hehuan_time"] = corrected_next_time
                observed["auto_next_time"] = corrected_next_time
                if latest_pending_at > last_success_at:
                    observed["auto_last_error"] = "心神尚未恢复，已按上次起手+1小时校准"
                else:
                    observed["auto_last_error"] = "心神尚未恢复，已按上次成功+1小时校准"
                observed["auto_last_error_at"] = float(now)
                _reset_hehuan_retry(observed)
            else:
                corrected_next_time = float(now + HEHUAN_WARM_OBSERVED_CD_SEC)
                observed = _block_hehuan_until(
                    observed,
                    corrected_next_time,
                    "心神尚未恢复，上次成功冷却已失效，已按当前+1小时保守校准",
                    now=now,
                )
        else:
            corrected_next_time = float(now + HEHUAN_WARM_OBSERVED_CD_SEC)
            observed = _block_hehuan_until(
                observed,
                corrected_next_time,
                "心神尚未恢复，缺少成功时间，已按1小时冷却保守校准",
                now=now,
            )
        auto_next_handled = True
    elif result == "contract_invalid" and observed.get("last_partner") and float(observed.get("contract_until", 0) or 0) > now:
        observed["auto_next_time"] = float(now + 5 * 60)
        observed["auto_last_error"] = "温养失败疑似错误锚点，已保留既有同参关系并等待新锚点"
        observed["auto_last_error_at"] = float(now)
        _reset_hehuan_auto_pending(observed)
        auto_next_handled = True
    elif result == "pending":
        observed = _mark_hehuan_pending_ack(observed, now)
        auto_next_handled = True
    if not auto_next_handled:
        if observed.get("next_hehuan_time"):
            observed["auto_next_time"] = max(float(observed.get("next_hehuan_time") or 0), now + 60)
        else:
            observed["auto_next_time"] = min(float(observed.get("auto_next_time") or 0) or now + 60, now + 60)
        observed["auto_last_error"] = ""
        observed["auto_last_error_at"] = 0
    observed["recent"].append({
        "ts": now,
        "path": observed["last_path"],
        "action": observed["last_action"],
        "result": observed["last_result"],
        "summary": observed["last_summary"],
    })
    observed["recent"] = observed["recent"][-8:]
    state["hehuan_observation"] = observed
    return True


def _set_hehuan_auto_block(observed, now, reason, next_time=None):
    observed["auto_last_action"] = "warm"
    observed["auto_last_error"] = str(reason or "")
    observed["auto_last_error_at"] = float(now) if observed["auto_last_error"] else 0
    observed["auto_next_time"] = float(next_time or now + HEHUAN_AUTO_BLOCK_BACKOFF_SEC)
    _reset_hehuan_auto_pending(observed)
    state["hehuan_observation"] = observed
    save_state()


def _hehuan_unsent_block(command):
    block = get_last_game_send_block(get_current_identity_id(), command)
    code = str((block or {}).get("code") or "")
    if code in HEHUAN_UNSENT_BLOCK_CODES or code.startswith("flood_wait"):
        reason = str((block or {}).get("reason") or "").strip()
        return code, reason
    return "", ""


def _hehuan_block_label(code, reason):
    code = str(code or "runtime_block")
    reason = str(reason or "").strip()
    return f"{code}: {reason}" if reason else code


async def _ensure_hehuan_reply_anchor(observed, now):
    partner = str((observed or {}).get("last_partner") or "").strip()
    if partner:
        partner_id = int((observed or {}).get("last_partner_identity_id", 0) or 0)
        if partner_id <= 0:
            partner_id = _resolve_identity_id_by_at_name(partner)
        anchor_msg_id = find_recent_hehuan_partner_anchor_msg_id(
            partner,
            now=now,
            target_id=partner_id,
        )
        if anchor_msg_id > 0:
            observed["last_partner_identity_id"] = partner_id
            return anchor_msg_id, ""
        if partner_id <= 0:
            return 0, f"缺少同参对象 {partner} 近10分钟游戏话题锚点，且无法定位本地身份，暂不裸发温养双修。"
        observed["last_partner_identity_id"] = partner_id
        anchor_requested_at = float((observed or {}).get("auto_anchor_requested_at", 0) or 0)
        if now - anchor_requested_at < 60:
            return 0, f"已请求同参对象 {partner} 发言锚点，等待监听入库。"
        anchor_msg = await send_game_command(
            HEHUAN_ANCHOR_TEXT,
            track=False,
            max_retry=0,
            send_as_id=partner_id,
            priority="normal",
            source_module="合欢宗",
            op_id=f"hehuan-anchor-{int(now)}",
            delete_policy="manual_keep",
        )
        observed["auto_anchor_requested_at"] = float(now)
        state["hehuan_observation"] = observed
        save_state()
        anchor_msg_id = int(getattr(anchor_msg, "id", 0) or 0) if anchor_msg else 0
        if anchor_msg_id > 0:
            return anchor_msg_id, ""
        return 0, f"同参对象 {partner} 发言锚点发送失败，暂不裸发温养双修。"

    anchor_msg_id = find_recent_baiji_anchor_msg_id(now)
    if anchor_msg_id > 0:
        return anchor_msg_id, ""

    return 0, "缺少同参对象近10分钟游戏话题锚点，暂不裸发温养双修。"


def _has_unresolved_hehuan_pending(observed, now):
    pending_msg_id = int(observed.get("auto_pending_msg_id", 0) or 0)
    pending_sent_at = float(observed.get("auto_pending_sent_at", 0) or 0)
    pending_deadline_at = float(observed.get("auto_pending_deadline_at", 0) or 0)
    if pending_msg_id <= 0 or pending_sent_at <= 0 or pending_deadline_at <= 0:
        return False
    last_result = str(observed.get("last_result") or "").strip().lower()
    if last_result != "pending" and float(observed.get("last_observed_at", 0) or 0) >= pending_sent_at:
        _reset_hehuan_auto_pending(observed)
        return False
    return now >= pending_deadline_at


def _is_hehuan_recoverable_reply_log_entry(entry):
    text = (entry or {}).get("text") or ""
    if not looks_like_hehuan_text(text):
        return False
    parsed = parse_hehuan_text(
        text,
        now=float((entry or {}).get("ts_epoch") or time.time()),
        family="hehuan_dual",
    )
    if not parsed:
        return False
    result = str(parsed.get("result") or "").strip().lower()
    action = str(parsed.get("action") or "").strip()
    path = str(parsed.get("path") or "").strip()
    if path != PATH_TONGCAN:
        return False
    return result in {"pending", "success", "cooldown", "contract_invalid", "realm_blocked"} and action in {
        "双修",
        "双修 温养",
    }


def _recover_hehuan_pending_from_message_log(observed, now):
    pending_msg_id = int((observed or {}).get("auto_pending_msg_id", 0) or 0)
    if pending_msg_id <= 0:
        return False
    replies = find_message_log_replies(
        pending_msg_id,
        now,
        lookback_sec=HEHUAN_LOG_REPLAY_LOOKBACK_SEC,
        lookahead_sec=HEHUAN_LOG_REPLAY_LOOKAHEAD_SEC,
        chat_id=get_game_group_id(),
        predicate=_is_hehuan_recoverable_reply_log_entry,
    )
    if not replies:
        return False
    handled_any = False
    for entry in replies:
        handled = apply_hehuan_passive(
            entry.get("text") or "",
            now=float(entry.get("ts_epoch") or now),
            family="hehuan_dual",
        )
        handled_any = handled_any or handled
    if handled_any:
        observed_after = normalize_hehuan_observation(state.get("hehuan_observation"))
        if str(observed_after.get("last_result") or "").strip().lower() == "pending":
            observed_after, _wait_for_cooldown = _assume_hehuan_pending_consumed(observed_after, now)
            state["hehuan_observation"] = observed_after
    return handled_any


def _find_recent_hehuan_sent_from_message_log(send_as_id, command, now, *, reply_to_msg_id=0, op_id="", lookback_sec=180):
    now = float(now if now is not None else time.time())
    min_ts = now - max(1, int(lookback_sec or 180))
    game_group_id = int(get_game_group_id() or 0)
    expected_command = str(command or "").strip()
    expected_reply_to = int(reply_to_msg_id or 0)
    expected_op_id = str(op_id or "").strip()
    expected_sender_id = int(send_as_id or 0)
    best = None
    for path in _recent_message_log_paths(now):
        if not os.path.exists(path):
            continue
        for line in reversed(_read_message_log_tail(path)):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            event_type = str(payload.get("event_type") or "")
            if event_type not in {"sent", "message"}:
                continue
            if game_group_id and int(payload.get("chat_id", 0) or 0) != game_group_id:
                continue
            if str(payload.get("text") or "").strip() != expected_command:
                continue
            msg_ts = _parse_message_log_ts(payload.get("ts"))
            if msg_ts <= 0 or msg_ts < min_ts or msg_ts > now + 60:
                continue
            try:
                sender_id = int(payload.get("sender_id", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                sender_id = 0
            if expected_sender_id and sender_id not in {expected_sender_id, -expected_sender_id}:
                continue
            try:
                payload_reply_to = int(payload.get("reply_to_msg_id", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                payload_reply_to = 0
            if expected_reply_to and payload_reply_to != expected_reply_to:
                continue
            if event_type == "sent" and expected_op_id:
                payload_op_id = str(payload.get("op_id") or "").strip()
                if payload_op_id and payload_op_id != expected_op_id:
                    continue
            try:
                msg_id = int(payload.get("message_id", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                msg_id = 0
            if msg_id <= 0:
                continue
            best = {
                "message_id": msg_id,
                "ts_epoch": msg_ts,
                "reply_to_msg_id": payload_reply_to,
                "event_type": event_type,
            }
            break
        if best:
            break
    return best


async def run_hehuan_scheduler(now):
    now = float(now if now is not None else time.time())
    dirty_fields = _dirty_hehuan_time_fields(state.get("hehuan_observation"))
    if dirty_fields:
        return

    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    reminders_changed, observed, reminder_sent = await _run_hehuan_valuable_drop_reminders(observed, now)
    if reminders_changed:
        state["hehuan_observation"] = observed
        save_state()
    if reminder_sent:
        return

    if not state.get("hehuan_enabled"):
        return
    if not is_module_available("合欢宗"):
        state["hehuan_enabled"] = False
        state["hehuan_observation"] = {}
        save_state()
        return

    auto_next_time = float(observed.get("auto_next_time", 0) or 0)
    if auto_next_time > 0 and now < auto_next_time:
        return

    if _has_unresolved_hehuan_pending(observed, now):
        if _recover_hehuan_pending_from_message_log(observed, now):
            save_state()
            return
        if str(observed.get("last_result") or "").strip().lower() == "pending":
            observed, wait_for_cooldown = _assume_hehuan_pending_consumed(observed, now)
            state["hehuan_observation"] = observed
            save_state()
            if wait_for_cooldown:
                return
        else:
            observed = _schedule_hehuan_retry(observed, now, "温养回复超时或被吞")
            state["hehuan_observation"] = observed
            save_state()
            if float(observed.get("auto_next_time", 0) or 0) > now:
                return

    if str(observed.get("last_result") or "").strip().lower() == "pending":
        observed, wait_for_cooldown = _assume_hehuan_pending_consumed(observed, now)
        state["hehuan_observation"] = observed
        save_state()
        if wait_for_cooldown:
            return

    plan = build_hehuan_manual_plan("warm", now=now)
    if not plan.get("allowed"):
        next_time = float(observed.get("next_hehuan_time", 0) or 0)
        if next_time <= now:
            next_time = now + HEHUAN_AUTO_BLOCK_BACKOFF_SEC
        _set_hehuan_auto_block(observed, now, plan.get("reason") or "合欢宗自动温养未满足条件", next_time)
        return

    anchor_msg_id, anchor_error = await _ensure_hehuan_reply_anchor(observed, now)
    if anchor_msg_id <= 0 and anchor_error:
        _set_hehuan_auto_block(observed, now, anchor_error or "缺少吧唧回复锚点", now + 5 * 60)
        return

    retry_delay_sec = max(1.0, float(_hehuan_retry_delay_sec(observed)))
    send_kwargs = {}
    if anchor_msg_id > 0:
        send_kwargs["reply_to"] = anchor_msg_id
    op_id = f"hehuan-auto-warm-{int(now)}"
    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=0,
        priority="normal",
        source_module="合欢宗",
        op_id=op_id,
        reply_timeout=max(1, int(retry_delay_sec)),
        delete_policy="manual_keep",
        **send_kwargs,
    )
    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    if not msg:
        block_code, block_reason = _hehuan_unsent_block(plan["command"])
        if block_code:
            _set_hehuan_auto_block(
                observed,
                now,
                f"合欢宗自动温养未发送：{_hehuan_block_label(block_code, block_reason)}",
                now + HEHUAN_AUTO_SEND_FAIL_BACKOFF_SEC,
            )
            return
        recovered_sent = _find_recent_hehuan_sent_from_message_log(
            get_current_identity_id(),
            plan["command"],
            now,
            reply_to_msg_id=anchor_msg_id,
            op_id=op_id,
        )
        if recovered_sent:
            sent_at = float(recovered_sent.get("ts_epoch") or now)
            observed["auto_last_action"] = "warm"
            observed["auto_last_error"] = ""
            observed["auto_last_error_at"] = 0
            observed["auto_pending_msg_id"] = int(recovered_sent.get("message_id") or 0)
            observed["auto_pending_sent_at"] = sent_at
            observed["auto_pending_deadline_at"] = max(
                float(sent_at + retry_delay_sec),
                float(now + HEHUAN_FINAL_EDIT_WAIT_SEC),
            )
            observed["auto_reply_anchor_msg_id"] = int(recovered_sent.get("reply_to_msg_id") or anchor_msg_id or 0)
            observed["auto_next_time"] = observed["auto_pending_deadline_at"]
            state["hehuan_observation"] = observed
            if _recover_hehuan_pending_from_message_log(observed, now):
                save_state()
                return
            save_state()
            return
        _set_hehuan_auto_block(observed, now, "合欢宗自动温养发送失败或被安全策略拦截", now + HEHUAN_AUTO_SEND_FAIL_BACKOFF_SEC)
        return

    parsed_sent_at, sent_at_dirty = _parse_observation_float(getattr(msg, "sent_at", 0))
    sent_at = now if sent_at_dirty or parsed_sent_at <= 0 else parsed_sent_at
    observed["auto_last_action"] = "warm"
    observed["auto_last_error"] = ""
    observed["auto_last_error_at"] = 0
    observed["auto_pending_msg_id"] = int(getattr(msg, "id", 0) or 0)
    observed["auto_pending_sent_at"] = float(sent_at)
    observed["auto_pending_deadline_at"] = float(sent_at + retry_delay_sec)
    observed["auto_reply_anchor_msg_id"] = int(anchor_msg_id or 0)
    observed["auto_next_time"] = observed["auto_pending_deadline_at"]
    state["hehuan_observation"] = observed
    save_state()


def build_hehuan_manual_plan(action="warm", now=None):
    now = float(now if now is not None else time.time())
    normalized_action = str(action or "warm").strip().lower()
    if normalized_action in {"", "warm", "温养", "双修温养"}:
        normalized_action = "warm"
    if normalized_action != "warm":
        return {
            "allowed": False,
            "action": normalized_action,
            "command": "",
            "family": "",
            "reason": "合欢宗当前只开放温养双修的受控发送；缔结同参、种下心印、采补仍仅观察/人工处理。",
        }
    if not state.get("hehuan_enabled"):
        return {
            "allowed": False,
            "action": normalized_action,
            "command": "",
            "family": "hehuan_dual",
            "reason": "合欢宗模块未开启。",
        }

    dirty_fields = _dirty_hehuan_time_fields(state.get("hehuan_observation"))
    if dirty_fields:
        return {
            "allowed": False,
            "action": normalized_action,
            "command": f"{CMD_HEHUAN_DUAL} 温养",
            "family": "hehuan_dual",
            "reason": f"合欢宗状态字段异常（{'、'.join(dirty_fields)}），不猜测冷却或契印时间。",
        }

    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    next_time = float(observed.get("next_hehuan_time", 0) or 0)
    if next_time > now:
        return {
            "allowed": False,
            "action": normalized_action,
            "command": f"{CMD_HEHUAN_DUAL} 温养",
            "family": "hehuan_dual",
            "reason": f"温养双修仍在冷却中，{fmt_remaining(next_time)} 后再试。",
        }
    retry_count = int(observed.get("auto_retry_count", 0) or 0)
    last_result = str(observed.get("last_result") or "").strip().lower()
    if last_result == "cooldown" and next_time <= 0 and retry_count <= 0:
        return {
            "allowed": False,
            "action": normalized_action,
            "command": f"{CMD_HEHUAN_DUAL} 温养",
            "family": "hehuan_dual",
            "reason": "合欢宗冷却时间不可解析，不发送温养双修。",
        }

    if last_result == "pending" and retry_count <= 0:
        return {
            "allowed": False,
            "action": normalized_action,
            "command": f"{CMD_HEHUAN_DUAL} 温养",
            "family": "hehuan_dual",
            "reason": "合欢宗温养结算仍待真实结果，不发送温养双修。",
        }

    last_observed_at = float(observed.get("last_observed_at", 0) or 0)
    if last_observed_at <= 0:
        return {
            "allowed": False,
            "action": normalized_action,
            "command": f"{CMD_HEHUAN_DUAL} 温养",
            "family": "hehuan_dual",
            "reason": "缺少合欢宗真实文案状态，先等待消息盒子观察到温养/契印/冷却结果。",
        }
    if now - last_observed_at > HEHUAN_OBSERVATION_STALE_SEC:
        return {
            "allowed": False,
            "action": normalized_action,
            "command": f"{CMD_HEHUAN_DUAL} 温养",
            "family": "hehuan_dual",
            "reason": f"合欢宗状态过旧，最近观察 {fmt_abs_ts(last_observed_at)}。",
        }

    contract_until = float(observed.get("contract_until", 0) or 0)
    cooldown_identity_hint = last_result == "cooldown" and _cooldown_matches_current_identity(observed)
    if contract_until <= now and not cooldown_identity_hint:
        return {
            "allowed": False,
            "action": normalized_action,
            "command": f"{CMD_HEHUAN_DUAL} 温养",
            "family": "hehuan_dual",
            "reason": "未确认有效同参契印，不发送温养双修。",
        }

    return {
        "allowed": True,
        "action": normalized_action,
        "command": f"{CMD_HEHUAN_DUAL} 温养",
        "family": "hehuan_dual",
        "reason": "同参温养状态允许发送。",
        "source_module": "合欢宗",
        "op_id": f"hehuan-warm-{int(now)}",
        "delete_policy": "manual_keep",
        "max_retry": 0,
    }


async def execute_hehuan_manual_action(action="warm", *, send_as_id=None, now=None):
    now = float(now if now is not None else time.time())
    if send_as_id is not None:
        with use_identity(send_as_id):
            plan = build_hehuan_manual_plan(action, now=now)
    else:
        plan = build_hehuan_manual_plan(action, now=now)
    if not plan.get("allowed"):
        return False, plan.get("reason") or "合欢宗动作未允许", plan
    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=int(plan.get("max_retry", 0) or 0),
        send_as_id=send_as_id,
        priority="normal",
        source_module=plan.get("source_module") or "合欢宗",
        op_id=plan.get("op_id") or "",
        delete_policy=plan.get("delete_policy") or "manual_keep",
    )
    if not msg:
        return False, "发送被运行时安全策略拦截或账号不可用。", plan
    return True, f"已发送：{plan['command']}（msg_id={int(getattr(msg, 'id', 0) or 0)}）", plan


def reconcile_hehuan_timeout_from_pending(msg_id, now=None):
    """Reconcile a tracked Hehuan warm pending after runtime reply timeout."""
    now = float(now if now is not None else time.time())
    try:
        msg_id = int(msg_id or 0)
    except (TypeError, ValueError, OverflowError):
        msg_id = 0
    if msg_id <= 0:
        return False
    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    if int(observed.get("auto_pending_msg_id", 0) or 0) != msg_id:
        return False
    if _recover_hehuan_pending_from_message_log(observed, now):
        save_state()
        return True
    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    if str(observed.get("last_result") or "").strip().lower() == "pending":
        observed, _wait_for_cooldown = _assume_hehuan_pending_consumed(observed, now)
    else:
        observed["auto_last_action"] = "warm"
        observed["auto_last_error"] = "温养命令已发送但未回捞到回复，退避等待被动回复；不立即重发"
        observed["auto_last_error_at"] = float(now)
        observed["auto_next_time"] = float(now + HEHUAN_AUTO_SEND_FAIL_BACKOFF_SEC)
        _reset_hehuan_auto_pending(observed)
    state["hehuan_observation"] = observed
    save_state()
    return True


def _format_gain_map(gains):
    if not isinstance(gains, dict) or not gains:
        return "未记录"
    return "、".join(f"{key}:{value}" for key, value in gains.items())


def get_hehuan_status_text():
    dirty_fields = _dirty_hehuan_time_fields(state.get("hehuan_observation"))
    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    lines = [
        "🌸 合欢宗",
        f"- 模块：{'开启' if state.get('hehuan_enabled') else '关闭'}（被动观察，自动温养受控发送）",
        "- 三层：凡尘缘 .闭关双修｜同参道 .缔结同参/.双修 温养｜魔染道 .种下心印/.双修 采补/.挣脱心印",
        f"- 最近路径：{observed.get('last_path') or '未记录'}",
        f"- 最近动作：{observed.get('last_action') or '未记录'} / {observed.get('last_result') or '未记录'}",
        f"- 最近观察：{fmt_abs_ts(observed.get('last_observed_at', 0))}",
        f"- 最近成功：{fmt_abs_ts(observed.get('last_warm_success_at', 0))}",
        f"- 下次可试：{fmt_abs_ts(observed.get('next_hehuan_time', 0))}（{fmt_remaining(observed.get('next_hehuan_time', 0))}）",
        f"- 自动调度：{fmt_abs_ts(observed.get('auto_next_time', 0))}（{fmt_remaining(observed.get('auto_next_time', 0))}）",
        f"- 补发策略：随机 1-{int(observed.get('auto_retry_max_interval_min', HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN) or HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN)} 分钟，{int(observed.get('auto_retry_count', 0) or 0)}/{HEHUAN_AUTO_RETRY_LIMIT}",
        f"- 待回复：{int(observed.get('auto_pending_msg_id', 0) or 0) or '无'}｜锚点 {int(observed.get('auto_reply_anchor_msg_id', 0) or 0) or '无'}",
        f"- 同参契印：{fmt_abs_ts(observed.get('contract_until', 0))}（{fmt_remaining(observed.get('contract_until', 0))}）",
        f"- 心印/炉鼎：{fmt_abs_ts(observed.get('heart_seal_until', 0))}（{fmt_remaining(observed.get('heart_seal_until', 0))}）",
        f"- 修为/贡献：{_format_gain_map(observed.get('last_gains'))}｜贡献 {int(observed.get('last_contrib_gain', 0) or 0)}",
    ]
    if dirty_fields:
        lines.append(f"- 状态异常：{'、'.join(dirty_fields)} 不可解析，自动发送已暂停")
    if observed.get("last_partner"):
        lines.append(f"- 道友：{observed.get('last_partner')}")
    if observed.get("last_target"):
        lines.append(f"- 目标：{observed.get('last_target')}")
    if observed.get("last_insight"):
        lines.append(f"- 顿悟：{observed.get('last_insight')}")
    if observed.get("last_error"):
        lines.append(f"- 异常：{observed.get('last_error')}")
    if observed.get("auto_last_error"):
        lines.append(f"- 自动异常：{observed.get('auto_last_error')}")
    if observed.get("auto_retry_reason"):
        lines.append(f"- 补发原因：{observed.get('auto_retry_reason')}")
    recent = observed.get("recent") or []
    if recent:
        lines.append("- 最近事件：")
        for item in recent[-3:]:
            lines.append(
                f"  {fmt_abs_ts(item.get('ts', 0))} "
                f"{item.get('path') or '-'} {item.get('action') or '-'} {item.get('result') or '-'}"
            )
    return "\n".join(lines)


__all__ = [
    "apply_hehuan_passive",
    "build_hehuan_manual_plan",
    "execute_hehuan_manual_action",
    "find_baiji_identity_id",
    "find_recent_baiji_anchor_msg_id",
    "find_recent_hehuan_partner_anchor_msg_id",
    "get_hehuan_status_text",
    "looks_like_hehuan_text",
    "normalize_hehuan_observation",
    "parse_hehuan_text",
    "reconcile_hehuan_timeout_from_pending",
    "run_hehuan_scheduler",
    "set_hehuan_retry_max_interval_min",
]
