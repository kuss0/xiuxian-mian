import copy
import json
import math
import os
import random
import re
import time
from datetime import datetime, timedelta

from ..config import CMD_HEHUAN_DUAL, MESSAGES_DIR, TZ_LOCAL
from ..persistence import save_state
from ..runtime import send_game_command
from ..state import (
    get_current_identity_id,
    get_game_group_id,
    get_game_topic_id,
    get_identity_enabled,
    get_identity_ids,
    get_send_as_profile,
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
HEHUAN_BAIJI_SEND_AS_ID = 301299112
HEHUAN_BAIJI_USERNAME = "jfdffdddd"
HEHUAN_BAIJI_NAME = "吧唧"
HEHUAN_ANCHOR_TEXT = "。"

PATH_FANCHEN = "凡尘缘"
PATH_TONGCAN = "同参道"
PATH_MORAN = "魔染道"

RE_AT_NAME = re.compile(r"@[\w\d_]+")
RE_PARTNER = re.compile(r"你与\s*(?P<partner>@[\w\d_]+)")
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
        "auto_retry_count": 0,
        "auto_retry_reason": "",
        "auto_retry_max_interval_min": HEHUAN_RETRY_DEFAULT_MAX_INTERVAL_MIN,
        "auto_pending_msg_id": 0,
        "auto_pending_sent_at": 0,
        "auto_pending_deadline_at": 0,
        "auto_reply_anchor_msg_id": 0,
        "auto_anchor_requested_at": 0,
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
    for key in ("auto_retry_count", "auto_pending_msg_id", "auto_reply_anchor_msg_id"):
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
        observed["auto_next_time"] = float(now + HEHUAN_AUTO_BLOCK_BACKOFF_SEC)
        return observed
    observed["auto_retry_count"] = retry_count + 1
    observed["auto_retry_reason"] = str(reason or "retry")
    observed["auto_last_action"] = "warm"
    observed["auto_last_error"] = str(reason or "")
    observed["auto_next_time"] = float(now + _hehuan_retry_delay_sec(observed))
    _reset_hehuan_auto_pending(observed)
    return observed


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


def _is_baiji_log_entry(payload):
    try:
        sender_id = int(payload.get("sender_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        sender_id = 0
    username = str(payload.get("sender_username") or "").strip().lstrip("@").lower()
    sender_name = str(payload.get("sender_name") or "").strip()
    return (
        sender_id == HEHUAN_BAIJI_SEND_AS_ID
        or username == HEHUAN_BAIJI_USERNAME.lower()
        or sender_name == HEHUAN_BAIJI_NAME
    )


def _is_game_topic_entry(payload):
    topic_id = int(get_game_topic_id() or 0)
    if topic_id <= 0:
        return True
    try:
        payload_topic_id = int(payload.get("topic_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        payload_topic_id = 0
    try:
        reply_to_msg_id = int(payload.get("reply_to_msg_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        reply_to_msg_id = 0
    return payload_topic_id == topic_id or reply_to_msg_id == topic_id


def find_recent_baiji_anchor_msg_id(now=None, *, max_age_sec=HEHUAN_REPLY_ANCHOR_MAX_AGE_SEC):
    now = float(now if now is not None else time.time())
    min_ts = now - max(1, int(max_age_sec or HEHUAN_REPLY_ANCHOR_MAX_AGE_SEC))
    game_group_id = int(get_game_group_id() or 0)
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
            if not _is_game_topic_entry(payload):
                continue
            if not _is_baiji_log_entry(payload):
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
    observed["last_observed_at"] = now
    observed["last_path"] = parsed.get("path") or observed.get("last_path", "")
    observed["last_action"] = parsed.get("action") or ""
    observed["last_result"] = parsed.get("result") or ""
    observed["last_summary"] = parsed.get("summary") or _short_summary(text)
    observed["last_partner"] = parsed.get("partner") or ""
    observed["last_target"] = parsed.get("target") or ""
    observed["last_error"] = parsed.get("error") or ""
    result = str(parsed.get("result") or "").strip().lower()
    action = str(parsed.get("action") or "").strip()
    if parsed.get("next_hehuan_time"):
        observed["next_hehuan_time"] = float(parsed.get("next_hehuan_time") or 0)
    if parsed.get("contract_until"):
        observed["contract_until"] = max(0.0, float(parsed.get("contract_until") or 0))
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
        _reset_hehuan_retry(observed)
        auto_next_handled = True
    elif result == "cooldown":
        parsed_next_time = float(parsed.get("next_hehuan_time") or 0)
        last_success_at = float(observed.get("last_warm_success_at", 0) or 0)
        if parsed_next_time > 0:
            observed["next_hehuan_time"] = parsed_next_time
            observed["auto_next_time"] = max(parsed_next_time, now + 60)
            observed["auto_last_error"] = "心神尚未恢复，已按真实等待时间校准"
            _reset_hehuan_auto_pending(observed)
        elif last_success_at > 0:
            corrected_next_time = float(last_success_at + HEHUAN_WARM_OBSERVED_CD_SEC)
            observed["next_hehuan_time"] = corrected_next_time
            if corrected_next_time > now:
                observed["auto_next_time"] = corrected_next_time
                observed["auto_last_error"] = "心神尚未恢复，已按上次成功+1小时校准"
                _reset_hehuan_auto_pending(observed)
            else:
                observed = _schedule_hehuan_retry(observed, now, "心神尚未恢复")
        else:
            observed = _schedule_hehuan_retry(observed, now, "心神尚未恢复且缺少成功时间")
        auto_next_handled = True
    elif result == "pending":
        observed = _schedule_hehuan_retry(observed, now, "温养结算无最终推进")
        auto_next_handled = True
    if not auto_next_handled:
        if observed.get("next_hehuan_time"):
            observed["auto_next_time"] = max(float(observed.get("next_hehuan_time") or 0), now + 60)
        else:
            observed["auto_next_time"] = min(float(observed.get("auto_next_time") or 0) or now + 60, now + 60)
        observed["auto_last_error"] = ""
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
    observed["auto_next_time"] = float(next_time or now + HEHUAN_AUTO_BLOCK_BACKOFF_SEC)
    _reset_hehuan_auto_pending(observed)
    state["hehuan_observation"] = observed
    save_state()


async def _ensure_hehuan_reply_anchor(observed, now):
    anchor_msg_id = find_recent_baiji_anchor_msg_id(now)
    if anchor_msg_id > 0:
        return anchor_msg_id, ""

    baiji_identity_id = find_baiji_identity_id()
    if baiji_identity_id <= 0:
        return 0, "10分钟内没有吧唧发言，且未找到吧唧身份用于创建回复锚点"
    if int(baiji_identity_id) == int(get_current_identity_id() or 0):
        return 0, "当前身份是吧唧，不能对自己的锚点自动温养"

    anchor_msg = await send_game_command(
        HEHUAN_ANCHOR_TEXT,
        track=False,
        send_as_id=baiji_identity_id,
        max_retry=0,
        priority="normal",
        source_module="合欢宗",
        op_id=f"hehuan-anchor-{int(now)}",
        delete_policy="manual_keep",
    )
    if not anchor_msg:
        return 0, "10分钟内没有吧唧发言，锚点发送失败或被安全策略拦截"

    anchor_msg_id = int(getattr(anchor_msg, "id", 0) or 0)
    if anchor_msg_id <= 0:
        return 0, "吧唧锚点发送后未返回消息ID"
    observed["auto_anchor_requested_at"] = float(now)
    return anchor_msg_id, ""


def _has_unresolved_hehuan_pending(observed, now):
    pending_msg_id = int(observed.get("auto_pending_msg_id", 0) or 0)
    pending_sent_at = float(observed.get("auto_pending_sent_at", 0) or 0)
    pending_deadline_at = float(observed.get("auto_pending_deadline_at", 0) or 0)
    if pending_msg_id <= 0 or pending_sent_at <= 0 or pending_deadline_at <= 0:
        return False
    if float(observed.get("last_observed_at", 0) or 0) >= pending_sent_at:
        _reset_hehuan_auto_pending(observed)
        return False
    return now >= pending_deadline_at


async def run_hehuan_scheduler(now):
    now = float(now if now is not None else time.time())
    if not state.get("hehuan_enabled"):
        return

    dirty_fields = _dirty_hehuan_time_fields(state.get("hehuan_observation"))
    if dirty_fields:
        return

    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    auto_next_time = float(observed.get("auto_next_time", 0) or 0)
    if auto_next_time > 0 and now < auto_next_time:
        return

    if _has_unresolved_hehuan_pending(observed, now):
        observed = _schedule_hehuan_retry(observed, now, "温养回复超时或被吞")
        state["hehuan_observation"] = observed
        save_state()
        if float(observed.get("auto_next_time", 0) or 0) > now:
            return

    plan = build_hehuan_manual_plan("warm", now=now)
    if not plan.get("allowed"):
        next_time = float(observed.get("next_hehuan_time", 0) or 0)
        if next_time <= now:
            next_time = now + HEHUAN_AUTO_BLOCK_BACKOFF_SEC
        _set_hehuan_auto_block(observed, now, plan.get("reason") or "合欢宗自动温养未满足条件", next_time)
        return

    anchor_msg_id, anchor_error = await _ensure_hehuan_reply_anchor(observed, now)
    if anchor_msg_id <= 0:
        _set_hehuan_auto_block(observed, now, anchor_error or "缺少吧唧回复锚点", now + 5 * 60)
        return

    msg = await send_game_command(
        plan["command"],
        track=True,
        reply_to=anchor_msg_id,
        max_retry=0,
        priority="normal",
        source_module="合欢宗",
        op_id=f"hehuan-auto-warm-{int(now)}",
        reply_timeout=max(1, int(_hehuan_retry_delay_sec(observed))),
        delete_policy="manual_keep",
    )
    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    if not msg:
        _set_hehuan_auto_block(observed, now, "合欢宗自动温养发送失败或被安全策略拦截", now + HEHUAN_AUTO_SEND_FAIL_BACKOFF_SEC)
        return

    parsed_sent_at, sent_at_dirty = _parse_observation_float(getattr(msg, "sent_at", 0))
    sent_at = now if sent_at_dirty or parsed_sent_at <= 0 else parsed_sent_at
    observed["auto_last_action"] = "warm"
    observed["auto_last_error"] = ""
    observed["auto_pending_msg_id"] = int(getattr(msg, "id", 0) or 0)
    observed["auto_pending_sent_at"] = float(sent_at)
    observed["auto_pending_deadline_at"] = float(sent_at + _hehuan_retry_delay_sec(observed))
    observed["auto_reply_anchor_msg_id"] = int(anchor_msg_id)
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
    "get_hehuan_status_text",
    "looks_like_hehuan_text",
    "normalize_hehuan_observation",
    "parse_hehuan_text",
    "run_hehuan_scheduler",
    "set_hehuan_retry_max_interval_min",
]
