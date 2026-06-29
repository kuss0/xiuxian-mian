import asyncio
import copy
import math
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_DEEP_RETREAT_FORCE_EXIT,
    CMD_CRAFT,
    CMD_NORMAL_RETREAT,
    CMD_TIANXING_CHANGE_FATE,
    CMD_TIANXING_CLEAR_CALAMITY,
    CMD_TIANXING_OBSERVE,
    CMD_TIANXING_PANEL,
    CMD_TIANXING_PREDICT,
    CMD_TIANXING_SET_STAR,
    CMD_USE_HEQI_DAN,
    CMD_EXCHANGE_HEQI_DAN_PREFIX,
    CMD_SECT_DONATE_LINGSHI_PREFIX,
)
from ..persistence import save_state
from ..runtime import send_game_command
from ..state import get_current_identity_id, is_module_available, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, has_wait_time, parse_wait_time


TIANXING_PREDICTION_SEC = 8 * 3600
TIANXING_CHANGE_FATE_SEC = 24 * 3600
TIANXING_TIME_BUFFER_SEC = 60
TIANXING_OBSERVATION_STALE_SEC = 24 * 3600
TIANXING_AUTO_STATUS_BACKOFF_SEC = 6 * 3600
TIANXING_AUTO_BLOCK_BACKOFF_SEC = 60 * 60
TIANXING_AUTO_SEND_FAIL_BACKOFF_SEC = 30 * 60
TIANXING_TIMELINE_ACK_TIMEOUT_SEC = 90
TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC = 5 * 60
TIANXING_RETREAT_FARM_REPLY_TIMEOUT_SEC = 90
TIANXING_RETREAT_FARM_RETRY_SEC = 5 * 60
TIANXING_RETREAT_FARM_CALIBRATION_DELAY_SEC = 60
TIANXING_RETREAT_FARM_DEFAULT_RETREAT_CD_SEC = 15 * 60
TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC = 120
TIANXING_CRAFT_FARM_RETRY_SEC = 20
TIANXING_CRAFT_FARM_CALIBRATION_DELAY_SEC = 60
TIANXING_STARS = ("紫微", "天府", "太阴", "贪狼")
TIANXING_ROUTES = ("闭关", "炼制", "探索", "斗法")
TIANXING_ROUTE_AUTO = "auto"

RE_BRACKET = re.compile(r"【([^】]+)】")
RE_STAR_EFFECT = re.compile(r"命盘【(?P<star>[^】]+)】照命(?P<desc>[^。\n]*)")
RE_SET_STAR = re.compile(r"你将今日命轨定在\s*【(?P<star>[^】]+)】")
RE_PREDICT = re.compile(r"为\s*【(?P<route>[^】]+)】\s*推下了?一段命数")
RE_CHANGE_FATE = re.compile(r"为\s*【(?P<route>[^】]+)】\s*预留了?一次改命回天")
RE_TIANJI_GAIN = re.compile(r"天机值\s*\+(?P<gain>\d+)")
RE_CONTRIB_GAIN = re.compile(r"宗门贡献\s*\+(?P<gain>\d+)")
RE_TIANJI_VALUE = re.compile(r"天机值[:：]\s*(?P<value>\d+)")
RE_CALAMITY = re.compile(r"逆命劫[:：]\s*(?P<value>\d+)")
RE_CALAMITY_GAIN = re.compile(r"逆命劫\s*\+(?P<gain>\d+)")
RE_COUNTS = re.compile(r"命中\s*/\s*落空\s*/\s*改命[:：]\s*(?P<hit>\d+)\s*/\s*(?P<miss>\d+)\s*/\s*(?P<change>\d+)")
RE_BONUS_GAIN = re.compile(r"因【天星宗】灵脉加持，你额外获得了\s*(?P<gain>\d+)\s*点修为")
RE_CRAFT_PREPARE = re.compile(r"准备同时开炼\s*(?P<count>\d+)\s*炉【(?P<item>[^】]+)】")
RE_CRAFT_DONE = re.compile(r"共开炉\s*(?P<count>\d+)\s*次，成功\s*(?P<success>\d+)\s*次")
RE_CRAFT_GAIN = re.compile(r"最终获得【(?P<item>[^】]+)】x(?P<count>\d+)")
RE_CRAFT_MISSING = re.compile(r"缺少[:：]?(?P<missing>.+)")
RE_EXCHANGE_SUCCESS = re.compile(r"兑换成功！\s*你消耗了\s*(?P<cost>\d+)\s*点贡献，获得了【(?P<item>[^】]+)】x(?P<count>\d+)")
RE_EXCHANGE_CONTRIB_SHORTAGE = re.compile(r"你的宗门贡献不足！\s*兑换【(?P<item>[^】]+)】x(?P<count>\d+)\s*需要\s*(?P<need>\d+)\s*点贡献，你只有\s*(?P<have>\d+)\s*点")
RE_SECT_DONATE_SUCCESS = re.compile(r"你向宗门捐献了\s*【(?P<item>[^】]+)】x(?P<count>\d+)，获得了\s*(?P<gain>\d+)\s*点宗门贡献")

TIANXING_OBSERVATION_TIME_KEYS = (
    "last_observed_at",
    "current_prediction_until",
    "current_change_until",
    "auto_next_time",
    "auto_pending_sent_at",
    "auto_pending_due_at",
)

_TIANXING_TIMELINE_LOCKS = {}
_TIANXING_AUTO_LOCKS = {}


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


def _dirty_tianxing_time_fields(value=None):
    if not isinstance(value, dict):
        return []
    dirty_fields = []
    for key in TIANXING_OBSERVATION_TIME_KEYS:
        _parsed, dirty = _parse_observation_float(value.get(key, 0))
        if dirty:
            dirty_fields.append(key)
    return dirty_fields


def _default_tianxing_observation():
    return {
        "last_observed_at": 0,
        "last_action": "",
        "last_result": "",
        "last_summary": "",
        "last_error": "",
        "available_stars": [],
        "fixed_star": "",
        "current_prediction": "",
        "current_prediction_until": 0,
        "current_change": "",
        "current_change_until": 0,
        "tianji_value": 0,
        "calamity_count": 0,
        "hit_count": 0,
        "miss_count": 0,
        "change_count": 0,
        "last_route": "",
        "last_star_effect": "",
        "last_tianji_gain": 0,
        "last_contrib_gain": 0,
        "last_bonus_gain": 0,
        "auto_next_time": 0,
        "auto_last_action": "",
        "auto_last_error": "",
        "auto_last_plan": "",
        "auto_last_plan_at": 0,
        "auto_pending_action": "",
        "auto_pending_command": "",
        "auto_pending_msg_id": 0,
        "auto_pending_sent_at": 0,
        "auto_pending_due_at": 0,
        "recent": [],
    }


def _default_tianxing_auto_config():
    return {
        "auto_panel_enabled": True,
        "auto_observe_enabled": True,
        "auto_clear_calamity_enabled": True,
        "auto_set_star_enabled": False,
        "auto_predict_enabled": False,
        "auto_change_fate_enabled": False,
        "strategy_dry_run_enabled": True,
        "timeline_enabled": False,
        "timeline_dry_run_enabled": True,
        "star_priority": ["贪狼", "太阴", "天府", "紫微"],
        "predict_route": TIANXING_ROUTE_AUTO,
        "change_route": TIANXING_ROUTE_AUTO,
        "route_priority": ["探索", "闭关", "炼制", "斗法"],
        "change_route_priority": ["探索", "斗法", "闭关", "炼制"],
        "farm_route": "闭关",
        "farm_window_enabled": True,
        "farm_window_start": "02:00",
        "farm_window_duration_min": 60,
        "retreat_farm_enabled": False,
        "retreat_farm_dry_run_enabled": True,
        "retreat_farm_allow_force_exit": False,
        "retreat_farm_allow_heqi_dan": False,
        "retreat_farm_auto_exchange_heqi_dan": False,
        "retreat_farm_heqi_exchange_count": 10,
        "retreat_farm_auto_donate_lingshi": False,
        "retreat_farm_donate_lingshi_count": 200,
        "craft_farm_enabled": False,
        "craft_farm_dry_run_enabled": True,
        "craft_farm_item": "玄铁剑",
        "craft_farm_daily_limit": 42,
        "craft_farm_interval_sec": TIANXING_CRAFT_FARM_RETRY_SEC,
        "craft_farm_reply_timeout_sec": TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC,
        "duel_route_enabled": False,
        "allow_prediction_override_enabled": False,
        "consume_conflicting_prediction_enabled": False,
        "route_prepare_lead_sec": 5 * 60,
        "target_tianji_daily": 42,
        "min_tianji_for_change": 6,
        "min_calamity_to_clear": 1,
        "status_backoff_hours": 6,
        "ack_timeout_sec": TIANXING_TIMELINE_ACK_TIMEOUT_SEC,
        "calibration_backoff_sec": TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC,
        "max_replans_per_day": 3,
    }


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on", "open", "enable", "enabled", "开", "开启", "启用"}:
        return True
    if raw in {"0", "false", "no", "n", "off", "close", "disable", "disabled", "关", "关闭", "停用"}:
        return False
    return bool(default)


def _coerce_int_range(value, default, min_value, max_value):
    try:
        parsed = int(float(value))
    except (TypeError, ValueError, OverflowError):
        parsed = int(default)
    return max(int(min_value), min(int(max_value), parsed))


def _coerce_float_range(value, default, min_value, max_value):
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = float(default)
    if not math.isfinite(parsed):
        parsed = float(default)
    return max(float(min_value), min(float(max_value), parsed))


def _normalize_choice_list(value, allowed, default):
    allowed_set = set(allowed)
    raw_items = []
    if isinstance(value, (list, tuple)):
        raw_items = list(value)
    elif isinstance(value, str):
        raw_items = re.split(r"[\s,，、|/]+", value.strip())
    normalized = []
    for item in raw_items:
        item = str(item or "").strip()
        if item in allowed_set and item not in normalized:
            normalized.append(item)
    if not normalized:
        normalized = [item for item in default if item in allowed_set]
    return normalized


def _normalize_route_choice(value, default=TIANXING_ROUTE_AUTO):
    value = str(value or "").strip()
    if value == TIANXING_ROUTE_AUTO:
        return TIANXING_ROUTE_AUTO
    if value in TIANXING_ROUTES:
        return value
    return default


def _normalize_hhmm(value, default="00:00"):
    raw = str(value or "").strip()
    match = re.match(r"^(?P<hour>\d{1,2}):(?P<minute>\d{1,2})$", raw)
    if not match:
        return str(default or "00:00")
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return str(default or "00:00")
    return f"{hour:02d}:{minute:02d}"


def _hhmm_to_seconds(value):
    value = _normalize_hhmm(value, "00:00")
    hour, minute = value.split(":", 1)
    return int(hour) * 3600 + int(minute) * 60


def normalize_tianxing_auto_config(value=None):
    default = _default_tianxing_auto_config()
    config = copy.deepcopy(default)
    if isinstance(value, dict):
        config.update(value)
    for key in (
        "auto_panel_enabled",
        "auto_observe_enabled",
        "auto_clear_calamity_enabled",
        "auto_set_star_enabled",
        "auto_predict_enabled",
        "auto_change_fate_enabled",
        "strategy_dry_run_enabled",
        "timeline_enabled",
        "timeline_dry_run_enabled",
        "farm_window_enabled",
        "retreat_farm_enabled",
        "retreat_farm_dry_run_enabled",
        "retreat_farm_allow_force_exit",
        "retreat_farm_allow_heqi_dan",
        "retreat_farm_auto_exchange_heqi_dan",
        "retreat_farm_auto_donate_lingshi",
        "craft_farm_enabled",
        "craft_farm_dry_run_enabled",
        "duel_route_enabled",
        "allow_prediction_override_enabled",
        "consume_conflicting_prediction_enabled",
    ):
        config[key] = _coerce_bool(config.get(key), default.get(key, False))
    config["star_priority"] = _normalize_choice_list(config.get("star_priority"), TIANXING_STARS, default["star_priority"])
    config["route_priority"] = _normalize_choice_list(config.get("route_priority"), TIANXING_ROUTES, default["route_priority"])
    config["change_route_priority"] = _normalize_choice_list(config.get("change_route_priority"), TIANXING_ROUTES, default["change_route_priority"])
    config["predict_route"] = _normalize_route_choice(config.get("predict_route"), default["predict_route"])
    config["change_route"] = _normalize_route_choice(config.get("change_route"), default["change_route"])
    config["farm_route"] = _normalize_route_choice(config.get("farm_route"), default["farm_route"])
    config["craft_farm_item"] = str(config.get("craft_farm_item") or default["craft_farm_item"]).strip() or default["craft_farm_item"]
    config["farm_window_start"] = _normalize_hhmm(config.get("farm_window_start"), default["farm_window_start"])
    config["farm_window_duration_min"] = _coerce_int_range(config.get("farm_window_duration_min"), default["farm_window_duration_min"], 5, 8 * 60)
    config["retreat_farm_heqi_exchange_count"] = _coerce_int_range(config.get("retreat_farm_heqi_exchange_count"), default["retreat_farm_heqi_exchange_count"], 1, 999)
    config["retreat_farm_donate_lingshi_count"] = _coerce_int_range(config.get("retreat_farm_donate_lingshi_count"), default["retreat_farm_donate_lingshi_count"], 1, 99999)
    config["craft_farm_daily_limit"] = _coerce_int_range(config.get("craft_farm_daily_limit"), default["craft_farm_daily_limit"], 0, 999)
    config["craft_farm_interval_sec"] = _coerce_int_range(config.get("craft_farm_interval_sec"), default["craft_farm_interval_sec"], 5, 60 * 60)
    config["craft_farm_reply_timeout_sec"] = _coerce_int_range(config.get("craft_farm_reply_timeout_sec"), default["craft_farm_reply_timeout_sec"], 30, 30 * 60)
    config["route_prepare_lead_sec"] = _coerce_int_range(config.get("route_prepare_lead_sec"), default["route_prepare_lead_sec"], 30, 60 * 60)
    config["target_tianji_daily"] = _coerce_int_range(config.get("target_tianji_daily"), default["target_tianji_daily"], 0, 999)
    config["min_tianji_for_change"] = _coerce_int_range(config.get("min_tianji_for_change"), default["min_tianji_for_change"], 3, 999)
    config["min_calamity_to_clear"] = _coerce_int_range(config.get("min_calamity_to_clear"), default["min_calamity_to_clear"], 1, 99)
    config["status_backoff_hours"] = _coerce_float_range(config.get("status_backoff_hours"), default["status_backoff_hours"], 1, 24)
    config["ack_timeout_sec"] = _coerce_int_range(config.get("ack_timeout_sec"), default["ack_timeout_sec"], 15, 15 * 60)
    config["calibration_backoff_sec"] = _coerce_int_range(config.get("calibration_backoff_sec"), default["calibration_backoff_sec"], 60, 60 * 60)
    config["max_replans_per_day"] = _coerce_int_range(config.get("max_replans_per_day"), default["max_replans_per_day"], 0, 99)
    return config


def set_tianxing_auto_config(config):
    normalized = normalize_tianxing_auto_config(config)
    state["tianxing_auto_config"] = normalized
    return normalized


def _default_tianxing_timeline_state():
    return {
        "plan_id": "",
        "phase": "idle",
        "route": "",
        "reason": "",
        "created_at": 0,
        "updated_at": 0,
        "deadline_at": 0,
        "active_step_index": -1,
        "active_step": {},
        "steps": [],
        "released_routes": {},
        "blocked_until": 0,
        "last_error": "",
        "audit": [],
        "retreat_farm": {},
        "craft_farm": {},
    }


def _default_tianxing_retreat_farm_state():
    return {
        "phase": "idle",
        "started_at": 0,
        "updated_at": 0,
        "next_time": 0,
        "cooldown_until": 0,
        "target_tianji": 0,
        "start_tianji": 0,
        "last_action": "",
        "last_command": "",
        "last_msg_id": 0,
        "last_error": "",
        "last_result": "",
        "last_tianji_gain": 0,
        "handoff_ready": False,
        "dry_run_plan": {},
        "audit": [],
    }


def _default_tianxing_craft_farm_state():
    return {
        "phase": "idle",
        "started_at": 0,
        "updated_at": 0,
        "next_time": 0,
        "target_tianji": 0,
        "start_tianji": 0,
        "estimated_tianji": 0,
        "daily_limit": 0,
        "daily_count": 0,
        "success_count": 0,
        "hit_count": 0,
        "miss_count": 0,
        "last_item": "",
        "last_action": "",
        "last_command": "",
        "last_msg_id": 0,
        "last_error": "",
        "last_result": "",
        "last_tianji_gain": 0,
        "handoff_ready": False,
        "dry_run_plan": {},
        "audit": [],
    }


def normalize_tianxing_retreat_farm_state(value=None):
    farm = copy.deepcopy(_default_tianxing_retreat_farm_state())
    if isinstance(value, dict):
        farm.update(value)
    for key in ("started_at", "updated_at", "next_time", "cooldown_until"):
        farm[key], _dirty = _parse_observation_float(farm.get(key, 0))
    for key in ("target_tianji", "start_tianji", "last_msg_id", "last_tianji_gain"):
        try:
            farm[key] = int(farm.get(key, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            farm[key] = 0
    for key in ("phase", "last_action", "last_command", "last_error", "last_result"):
        farm[key] = str(farm.get(key) or "").strip()
    if not isinstance(farm.get("dry_run_plan"), dict):
        farm["dry_run_plan"] = {}
    audit = []
    for item in farm.get("audit") or []:
        if isinstance(item, dict):
            audit.append(dict(item))
    farm["audit"] = audit[-20:]
    farm["handoff_ready"] = _coerce_bool(farm.get("handoff_ready"), False)
    if not farm["phase"]:
        farm["phase"] = "idle"
    return farm


def normalize_tianxing_craft_farm_state(value=None):
    farm = copy.deepcopy(_default_tianxing_craft_farm_state())
    if isinstance(value, dict):
        farm.update(value)
    for key in ("started_at", "updated_at", "next_time"):
        farm[key], _dirty = _parse_observation_float(farm.get(key, 0))
    for key in ("target_tianji", "start_tianji", "estimated_tianji", "daily_limit", "daily_count", "success_count", "hit_count", "miss_count", "last_msg_id", "last_tianji_gain"):
        try:
            farm[key] = int(farm.get(key, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            farm[key] = 0
    for key in ("phase", "last_item", "last_action", "last_command", "last_error", "last_result"):
        farm[key] = str(farm.get(key) or "").strip()
    if not isinstance(farm.get("dry_run_plan"), dict):
        farm["dry_run_plan"] = {}
    audit = []
    for item in farm.get("audit") or []:
        if isinstance(item, dict):
            audit.append(dict(item))
    farm["audit"] = audit[-20:]
    farm["handoff_ready"] = _coerce_bool(farm.get("handoff_ready"), False)
    if not farm["phase"]:
        farm["phase"] = "idle"
    return farm


def normalize_tianxing_timeline_state(value=None):
    timeline = copy.deepcopy(_default_tianxing_timeline_state())
    if isinstance(value, dict):
        timeline.update(value)
    for key in ("created_at", "updated_at", "deadline_at", "blocked_until"):
        timeline[key], _dirty = _parse_observation_float(timeline.get(key, 0))
    try:
        raw_index = timeline.get("active_step_index", -1)
        if raw_index is None or raw_index == "":
            raw_index = -1
        timeline["active_step_index"] = int(raw_index)
    except (TypeError, ValueError, OverflowError):
        timeline["active_step_index"] = -1
    if not isinstance(timeline.get("active_step"), dict):
        timeline["active_step"] = {}
    if not isinstance(timeline.get("released_routes"), dict):
        timeline["released_routes"] = {}
    steps = []
    for item in timeline.get("steps") or []:
        if isinstance(item, dict):
            steps.append(dict(item))
    timeline["steps"] = steps
    audit = []
    for item in timeline.get("audit") or []:
        if isinstance(item, dict):
            audit.append(dict(item))
    timeline["audit"] = audit[-20:]
    for key in ("plan_id", "phase", "route", "reason", "last_error"):
        timeline[key] = str(timeline.get(key) or "").strip()
    timeline["retreat_farm"] = normalize_tianxing_retreat_farm_state(timeline.get("retreat_farm"))
    timeline["craft_farm"] = normalize_tianxing_craft_farm_state(timeline.get("craft_farm"))
    if not timeline["phase"]:
        timeline["phase"] = "idle"
    return timeline


def normalize_tianxing_observation(value=None):
    observed = copy.deepcopy(_default_tianxing_observation())
    if isinstance(value, dict):
        observed.update(value)
    if not isinstance(observed.get("available_stars"), list):
        observed["available_stars"] = []
    observed["available_stars"] = [str(item) for item in observed.get("available_stars") or [] if str(item or "").strip()]
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
    for key in TIANXING_OBSERVATION_TIME_KEYS:
        observed[key], _dirty = _parse_observation_float(observed.get(key, 0))
    for key in ("tianji_value", "calamity_count", "hit_count", "miss_count", "change_count", "last_tianji_gain", "last_contrib_gain", "last_bonus_gain", "auto_pending_msg_id"):
        try:
            observed[key] = int(observed.get(key, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            observed[key] = 0
    return observed


def _short_summary(text, limit=80):
    raw_text = " / ".join(part.strip() for part in str(text or "").splitlines() if part.strip())
    return raw_text[: int(limit or 80)]


def _is_normal_retreat_cooldown_text(text):
    raw_text = str(text or "")
    return "灵气尚未平复" in raw_text and "无法立即再次闭关" in raw_text and has_wait_time(raw_text)


def _stars_from_line(line):
    return [item.strip() for item in RE_BRACKET.findall(str(line or "")) if item.strip()]


def _available_stars_from_observe_text(text):
    stars = []
    for match in re.finditer(r"【(?P<star>紫微|天府|太阴|贪狼)】\s*[-－—]", str(text or "")):
        star = match.group("star").strip()
        if star and star not in stars:
            stars.append(star)
    if stars:
        return stars
    for line in str(text or "").splitlines():
        if " - " not in line and "－" not in line and "—" not in line:
            continue
        for star in _stars_from_line(line):
            if star in TIANXING_STARS and star not in stars:
                stars.append(star)
    return stars


def _wait_until(text, now, fallback=0):
    if has_wait_time(text):
        wait_sec = parse_wait_time(text)
        if wait_sec > 0:
            return float(now + wait_sec + TIANXING_TIME_BUFFER_SEC)
    if fallback:
        return float(now + fallback + TIANXING_TIME_BUFFER_SEC)
    return 0


def _parse_route_timer(value, now):
    raw = str(value or "").strip()
    if not raw or raw == "无":
        return "", 0
    route = raw.split("（", 1)[0].strip()
    return route, _wait_until(raw, now)


def looks_like_tianxing_text(text):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    if "逆天改命之物" in raw_text:
        return False
    if any(keyword in raw_text for keyword in ("【天星宗玩法帮助】", "【天机盘】", "【观命结果】")):
        return True
    if "此命星并未在你今日观命结果中显化" in raw_text and "观命" in raw_text:
        return True
    if "司命盘" in raw_text and any(keyword in raw_text for keyword in ("推命", "改命", "命星", "命轨")):
        return True
    if any(keyword in raw_text for keyword in ("【推命命中】", "【推命落空】", "【天星偏转】", "【改命待发】", "【改命回天】")):
        return True
    if RE_STAR_EFFECT.search(raw_text):
        return True
    if "因【天星宗】灵脉加持" in raw_text:
        return True
    if "【天星宗】的观星长老" in raw_text:
        return True
    if "你所属的宗门: 【天星宗】" in raw_text and "司命盘要诀" in raw_text:
        return True
    if "成功化去 1 层逆命劫" in raw_text:
        return True
    return False


def parse_tianxing_text(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    raw_text = str(text or "").strip()
    if not raw_text:
        return None

    parsed = {
        "action": "",
        "result": "",
        "summary": "",
        "last_error": "",
    }

    if "【天星宗玩法帮助】" in raw_text:
        parsed.update(action="玩法帮助", result="guide", summary="天星宗玩法帮助")
        return parsed

    if "【天星宗】的观星长老" in raw_text:
        parsed.update(action="拜入天星宗", result="not_qualified", summary="资质不足，未能拜入天星宗", last_error="无法感应九天星辰之力")
        return parsed

    if "【观命结果】" in raw_text or ("观命结果" in raw_text and "今日可定下的命星" in raw_text):
        stars = _available_stars_from_observe_text(raw_text)
        parsed.update(action="观命", result="success", summary="观命结果", available_stars=stars, available_stars_source="observe")
        return parsed

    if "【天机盘】" in raw_text:
        parsed.update(action="天机盘", result="panel", summary="天机盘状态")
        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("今日可选命星"):
                parsed["available_stars"] = _stars_from_line(stripped)
                parsed["available_stars_source"] = "panel"
            elif stripped.startswith("今日已定命星"):
                stars = _stars_from_line(stripped)
                parsed["fixed_star"] = stars[0] if stars else ""
            elif stripped.startswith("当前推命"):
                value = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped.split("：", 1)[-1].strip()
                route, until = _parse_route_timer(value, now)
                parsed["current_prediction"] = route
                parsed["current_prediction_until"] = until
            elif stripped.startswith("当前改命"):
                value = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped.split("：", 1)[-1].strip()
                route, until = _parse_route_timer(value, now)
                parsed["current_change"] = route
                parsed["current_change_until"] = until
        tianji_match = RE_TIANJI_VALUE.search(raw_text)
        calamity_match = RE_CALAMITY.search(raw_text)
        counts_match = RE_COUNTS.search(raw_text)
        if tianji_match:
            parsed["tianji_value"] = int(tianji_match.group("value") or 0)
        if calamity_match:
            parsed["calamity_count"] = int(calamity_match.group("value") or 0)
        if counts_match:
            parsed["hit_count"] = int(counts_match.group("hit") or 0)
            parsed["miss_count"] = int(counts_match.group("miss") or 0)
            parsed["change_count"] = int(counts_match.group("change") or 0)
        return parsed

    if "你所属的宗门: 【天星宗】" in raw_text and "司命盘要诀" in raw_text:
        parsed.update(action="宗门信息", result="guide", summary="天星宗宗门信息与司命盘要诀")
        return parsed

    if "此命星并未在你今日观命结果中显化" in raw_text and "观命" in raw_text:
        parsed.update(
            action="定命",
            result="need_observe",
            summary="定命失败，需先观命",
            fixed_star="",
            available_stars=[],
            available_stars_source="",
            last_error="此命星未在今日观命结果中显化",
        )
        return parsed

    set_match = RE_SET_STAR.search(raw_text)
    if set_match:
        star = set_match.group("star").strip()
        parsed.update(action="定命", result="success", summary=f"定命 {star}", fixed_star=star)
        return parsed

    predict_match = RE_PREDICT.search(raw_text)
    if predict_match:
        route = predict_match.group("route").strip()
        parsed.update(
            action="推命",
            result="success",
            summary=f"推命 {route}",
            last_route=route,
            current_prediction=route,
            current_prediction_until=_wait_until(raw_text, now, TIANXING_PREDICTION_SEC),
        )
        return parsed

    if "你已有一道关于" in raw_text and "推命尚未应验" in raw_text:
        route = (_stars_from_line(raw_text) or [""])[0]
        parsed.update(
            action="推命",
            result="cooldown",
            summary=f"推命尚未应验 {route}".strip(),
            last_route=route,
            current_prediction=route,
            current_prediction_until=_wait_until(raw_text, now),
            last_error="推命尚未应验",
        )
        return parsed

    change_match = RE_CHANGE_FATE.search(raw_text)
    if change_match:
        route = change_match.group("route").strip()
        parsed.update(
            action="改命",
            result="success",
            summary=f"改命 {route}",
            last_route=route,
            current_change=route,
            current_change_until=_wait_until(raw_text, now, TIANXING_CHANGE_FATE_SEC),
        )
        return parsed

    if "成功化去 1 层逆命劫" in raw_text:
        parsed.update(action="消劫", result="success", summary="成功化去 1 层逆命劫")
        return parsed

    if family == "tianxing_retreat_farm":
        if "储物袋中没有名为【合气丹】的可用物品" in raw_text:
            parsed.update(action="合气丹", result="missing", summary="缺少合气丹", last_error="储物袋中没有合气丹")
            return parsed

        exchange_success = RE_EXCHANGE_SUCCESS.search(raw_text)
        if exchange_success and exchange_success.group("item").strip() == "合气丹":
            parsed.update(
                action="兑换合气丹",
                result="success",
                summary=f"兑换合气丹 x{int(exchange_success.group('count') or 0)}",
                exchange_item="合气丹",
                exchange_count=int(exchange_success.group("count") or 0),
                exchange_cost=int(exchange_success.group("cost") or 0),
            )
            return parsed

        exchange_shortage = RE_EXCHANGE_CONTRIB_SHORTAGE.search(raw_text)
        if exchange_shortage and exchange_shortage.group("item").strip() == "合气丹":
            parsed.update(
                action="兑换合气丹",
                result="contribution_shortage",
                summary="兑换合气丹贡献不足",
                exchange_item="合气丹",
                exchange_count=int(exchange_shortage.group("count") or 0),
                contribution_need=int(exchange_shortage.group("need") or 0),
                contribution_have=int(exchange_shortage.group("have") or 0),
                last_error="宗门贡献不足",
            )
            return parsed

        donate_success = RE_SECT_DONATE_SUCCESS.search(raw_text)
        if donate_success and donate_success.group("item").strip() == "灵石":
            parsed.update(
                action="宗门捐献",
                result="success",
                summary=f"捐献灵石 x{int(donate_success.group('count') or 0)}",
                donate_item="灵石",
                donate_count=int(donate_success.group("count") or 0),
                contribution_gain=int(donate_success.group("gain") or 0),
            )
            return parsed

        if "储物袋中没有名为【灵石】" in raw_text or ("灵石" in raw_text and "无法捐献" in raw_text):
            parsed.update(action="宗门捐献", result="blocked", summary="捐献灵石失败", last_error=_short_summary(raw_text))
            return parsed

    if family == "tianxing_retreat_farm" and "你服下一枚【合气丹】" in raw_text and "可以继续闭关" in raw_text:
        parsed.update(action="合气丹", result="success", summary="合气丹已服用，可继续闭关", normal_retreat_next_time=now)
        return parsed

    if family == "tianxing_retreat_farm" and _is_normal_retreat_cooldown_text(raw_text):
        wait_sec = parse_wait_time(raw_text)
        parsed.update(
            action="闭关",
            result="cooldown",
            summary="普通闭关调息中",
            normal_retreat_next_time=now + wait_sec + CD_BUFFER_SEC if wait_sec > 0 else now + TIANXING_RETREAT_FARM_DEFAULT_RETREAT_CD_SEC,
            last_error="灵气尚未平复",
        )
        return parsed

    if family == "tianxing_craft_farm":
        prepare_match = RE_CRAFT_PREPARE.search(raw_text)
        if prepare_match:
            item = prepare_match.group("item").strip()
            parsed.update(
                action="炼制",
                result="preparing",
                summary=f"准备炼制 {item}",
                craft_item=item,
                craft_count=int(prepare_match.group("count") or 0),
            )
            return parsed

        done_match = RE_CRAFT_DONE.search(raw_text)
        if done_match or "炼制结束" in raw_text:
            success_count = int(done_match.group("success") or 0) if done_match else 0
            total_count = int(done_match.group("count") or 0) if done_match else 0
            gain_match = RE_CRAFT_GAIN.search(raw_text)
            item = gain_match.group("item").strip() if gain_match else ""
            result = "success" if success_count > 0 or gain_match else "failure"
            if "【改命回天】" in raw_text:
                result = "change_triggered"
            elif "【推命命中】" in raw_text:
                result = "prediction_hit"
            elif "【推命落空】" in raw_text:
                result = "prediction_miss"
            parsed.update(
                action="炼制",
                result=result,
                summary=f"炼制结束 {item}".strip(),
                last_route="炼制",
                craft_item=item,
                craft_count=total_count,
                craft_success_count=success_count,
            )
            star_match = RE_STAR_EFFECT.search(raw_text)
            if star_match:
                parsed["last_star_effect"] = f"{star_match.group('star').strip()} {star_match.group('desc').strip()}".strip()
            tianji_gain_match = RE_TIANJI_GAIN.search(raw_text)
            contrib_gain_match = RE_CONTRIB_GAIN.search(raw_text)
            calamity_gain_match = RE_CALAMITY_GAIN.search(raw_text)
            if tianji_gain_match:
                parsed["last_tianji_gain"] = int(tianji_gain_match.group("gain") or 0)
            if contrib_gain_match:
                parsed["last_contrib_gain"] = int(contrib_gain_match.group("gain") or 0)
            if calamity_gain_match:
                parsed["calamity_delta"] = int(calamity_gain_match.group("gain") or 0)
            if "【推命命中】" in raw_text or "【推命落空】" in raw_text:
                parsed["current_prediction"] = ""
                parsed["current_prediction_until"] = 0
            if "【改命回天】" in raw_text:
                parsed["current_change"] = ""
                parsed["current_change_until"] = 0
            return parsed

        if any(keyword in raw_text for keyword in ("材料不足", "灵石不足", "未习得", "尚未习得", "无法炼制")):
            missing_match = RE_CRAFT_MISSING.search(raw_text)
            error = missing_match.group("missing").strip() if missing_match else _short_summary(raw_text)
            parsed.update(action="炼制", result="blocked", summary="炼制受阻", last_error=error)
            return parsed

    is_retreat_result = "【闭关成功】" in raw_text or "【闭关失败】" in raw_text
    if "因【天星宗】灵脉加持" in raw_text or (family == "tianxing_retreat_farm" and is_retreat_result):
        bonus_match = RE_BONUS_GAIN.search(raw_text)
        result = "success" if "【闭关成功】" in raw_text else "failure" if "【闭关失败】" in raw_text else "observed"
        parsed.update(action="闭关", result=result, summary="普通闭关结算")
        if bonus_match:
            parsed["last_bonus_gain"] = int(bonus_match.group("gain") or 0)
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else 0
        parsed["normal_retreat_next_time"] = now + wait_sec + CD_BUFFER_SEC if wait_sec > 0 else now + TIANXING_RETREAT_FARM_DEFAULT_RETREAT_CD_SEC
        star_match = RE_STAR_EFFECT.search(raw_text)
        if star_match:
            parsed["last_star_effect"] = f"{star_match.group('star').strip()} {star_match.group('desc').strip()}".strip()
        tianji_gain_match = RE_TIANJI_GAIN.search(raw_text)
        contrib_gain_match = RE_CONTRIB_GAIN.search(raw_text)
        calamity_gain_match = RE_CALAMITY_GAIN.search(raw_text)
        if tianji_gain_match:
            parsed["last_tianji_gain"] = int(tianji_gain_match.group("gain") or 0)
        if contrib_gain_match:
            parsed["last_contrib_gain"] = int(contrib_gain_match.group("gain") or 0)
        if calamity_gain_match:
            parsed["calamity_delta"] = int(calamity_gain_match.group("gain") or 0)
        if "【推命命中】" in raw_text:
            parsed["result"] = "prediction_hit"
            parsed["summary"] = "普通闭关推命命中"
            parsed["current_prediction"] = ""
            parsed["current_prediction_until"] = 0
        elif "【推命落空】" in raw_text:
            parsed["result"] = "prediction_miss"
            parsed["summary"] = "普通闭关推命落空"
            parsed["current_prediction"] = ""
            parsed["current_prediction_until"] = 0
        return parsed

    if (
        "【推命命中】" in raw_text
        or "【推命落空】" in raw_text
        or "【天星偏转】" in raw_text
        or "【改命待发】" in raw_text
        or "【改命回天】" in raw_text
        or RE_STAR_EFFECT.search(raw_text)
    ):
        result = "modifier"
        if "【改命回天】" in raw_text:
            result = "change_triggered"
        elif "【推命命中】" in raw_text:
            result = "prediction_hit"
        elif "【推命落空】" in raw_text:
            result = "prediction_miss"
        star_effect = ""
        star_match = RE_STAR_EFFECT.search(raw_text)
        if star_match:
            star_effect = f"{star_match.group('star').strip()} {star_match.group('desc').strip()}".strip()
        parsed.update(action="命盘偏转", result=result, summary=_short_summary(raw_text), last_star_effect=star_effect)
        tianji_gain_match = RE_TIANJI_GAIN.search(raw_text)
        contrib_gain_match = RE_CONTRIB_GAIN.search(raw_text)
        calamity_gain_match = RE_CALAMITY_GAIN.search(raw_text)
        if tianji_gain_match:
            parsed["last_tianji_gain"] = int(tianji_gain_match.group("gain") or 0)
        if contrib_gain_match:
            parsed["last_contrib_gain"] = int(contrib_gain_match.group("gain") or 0)
        if calamity_gain_match:
            parsed["calamity_delta"] = int(calamity_gain_match.group("gain") or 0)
        if "【推命命中】" in raw_text or "【推命落空】" in raw_text:
            parsed["current_prediction"] = ""
            parsed["current_prediction_until"] = 0
        if "【改命回天】" in raw_text:
            parsed["current_change"] = ""
            parsed["current_change_until"] = 0
        elif "【改命待发】" in raw_text:
            parsed["current_change_until"] = _wait_until(raw_text, now)
        return parsed

    if not looks_like_tianxing_text(raw_text):
        return None
    parsed.update(action="未知天星宗文案", result="observed", summary=_short_summary(raw_text))
    return parsed


def _update_retreat_farm_from_parsed(parsed, observed, now, family=""):
    parsed = parsed if isinstance(parsed, dict) else {}
    farm = _current_retreat_farm_state()
    family = str(family or "")
    active = bool(farm.get("started_at")) or family.startswith("tianxing_retreat_farm")
    if not active:
        return False

    action = str(parsed.get("action") or "")
    result = str(parsed.get("result") or "")
    changed = False
    config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
    if action == "天机盘":
        target = int(farm.get("target_tianji", 0) or 0)
        current = int(observed.get("tianji_value", 0) or 0)
        if target > 0 and current >= target:
            farm["phase"] = "complete"
            farm["handoff_ready"] = True
            farm["next_time"] = 0
            farm["last_result"] = f"天机值 {current} 已达到目标 {target}"
            _retreat_farm_audit(farm, now, "calibration_complete", tianji=current, target=target)
        else:
            farm["phase"] = "ready"
            farm["handoff_ready"] = False
            farm["next_time"] = float(now)
            farm["last_result"] = f"天机值 {current} 未达到目标 {target}" if target else "天机盘已校准"
            _retreat_farm_audit(farm, now, "calibration_ready", tianji=current, target=target)
        changed = True
    elif action == "合气丹" and result == "success":
        farm["phase"] = "ready"
        farm["handoff_ready"] = False
        farm["next_time"] = float(now)
        farm["cooldown_until"] = float(now)
        farm["last_result"] = parsed.get("summary") or "合气丹已服用"
        farm["last_error"] = ""
        _retreat_farm_audit(farm, now, "heqi_dan_ready")
        changed = True
    elif action == "合气丹" and result == "missing":
        cooldown_until = _retreat_farm_cooldown_until(farm, now)
        if config.get("retreat_farm_auto_exchange_heqi_dan"):
            farm["phase"] = "need_heqi_exchange"
            farm["next_time"] = float(now)
            farm["last_error"] = parsed.get("last_error") or "缺少合气丹，准备自动兑换"
        else:
            farm["phase"] = "cooldown"
            farm["next_time"] = cooldown_until or float(now + TIANXING_RETREAT_FARM_RETRY_SEC)
            farm["last_error"] = parsed.get("last_error") or "缺少合气丹，自动兑换未开启"
        farm["cooldown_until"] = cooldown_until
        farm["handoff_ready"] = False
        farm["last_result"] = parsed.get("summary") or result
        _retreat_farm_audit(farm, now, "heqi_dan_missing", next_time=farm["next_time"], auto_exchange=bool(config.get("retreat_farm_auto_exchange_heqi_dan")))
        changed = True
    elif action == "兑换合气丹":
        cooldown_until = _retreat_farm_cooldown_until(farm, now)
        farm["cooldown_until"] = cooldown_until
        farm["handoff_ready"] = False
        if result == "success":
            farm["phase"] = "ready_to_use_heqi"
            farm["next_time"] = float(now)
            farm["last_result"] = parsed.get("summary") or "合气丹兑换成功"
            farm["last_error"] = ""
            _retreat_farm_audit(farm, now, "heqi_exchange_success", count=int(parsed.get("exchange_count", 0) or 0))
        elif result == "contribution_shortage":
            if config.get("retreat_farm_auto_donate_lingshi"):
                farm["phase"] = "need_lingshi_donation"
                farm["next_time"] = float(now)
            else:
                farm["phase"] = "cooldown"
                farm["next_time"] = cooldown_until or float(now + _status_backoff_sec(config))
            farm["last_result"] = parsed.get("summary") or result
            farm["last_error"] = parsed.get("last_error") or "兑换合气丹贡献不足"
            _retreat_farm_audit(
                farm,
                now,
                "heqi_exchange_contribution_shortage",
                need=int(parsed.get("contribution_need", 0) or 0),
                have=int(parsed.get("contribution_have", 0) or 0),
                auto_donate=bool(config.get("retreat_farm_auto_donate_lingshi")),
            )
        else:
            farm["phase"] = "cooldown"
            farm["next_time"] = cooldown_until or float(now + _status_backoff_sec(config))
            farm["last_result"] = parsed.get("summary") or result
            farm["last_error"] = parsed.get("last_error") or "兑换合气丹失败"
            _retreat_farm_audit(farm, now, "heqi_exchange_blocked", reason=farm["last_error"])
        changed = True
    elif action == "宗门捐献":
        cooldown_until = _retreat_farm_cooldown_until(farm, now)
        farm["cooldown_until"] = cooldown_until
        farm["handoff_ready"] = False
        if result == "success":
            farm["phase"] = "need_heqi_exchange"
            farm["next_time"] = float(now)
            farm["last_result"] = parsed.get("summary") or "灵石捐献成功"
            farm["last_error"] = ""
            _retreat_farm_audit(
                farm,
                now,
                "lingshi_donation_success",
                count=int(parsed.get("donate_count", 0) or 0),
                contribution_gain=int(parsed.get("contribution_gain", 0) or 0),
            )
        else:
            farm["phase"] = "cooldown"
            farm["next_time"] = cooldown_until or float(now + _status_backoff_sec(config))
            farm["last_result"] = parsed.get("summary") or result
            farm["last_error"] = parsed.get("last_error") or "捐献灵石失败"
            _retreat_farm_audit(farm, now, "lingshi_donation_blocked", reason=farm["last_error"])
        changed = True
    elif action == "闭关":
        next_time = float(parsed.get("normal_retreat_next_time", 0) or 0)
        if next_time <= 0:
            next_time = float(now + TIANXING_RETREAT_FARM_DEFAULT_RETREAT_CD_SEC + CD_BUFFER_SEC)
        farm["last_result"] = parsed.get("summary") or result
        farm["last_tianji_gain"] = int(parsed.get("last_tianji_gain", 0) or 0)
        farm["handoff_ready"] = False
        farm["cooldown_until"] = next_time
        if result == "cooldown":
            farm["phase"] = "cooldown"
            farm["next_time"] = next_time
            farm["last_error"] = parsed.get("last_error") or "普通闭关调息中"
            _retreat_farm_audit(farm, now, "retreat_cooldown", next_time=next_time)
        elif farm["last_tianji_gain"] > 0:
            farm["phase"] = "calibrating"
            farm["next_time"] = float(now + TIANXING_RETREAT_FARM_CALIBRATION_DELAY_SEC)
            farm["last_error"] = ""
            _retreat_farm_audit(farm, now, "retreat_hit_calibration_due", gain=farm["last_tianji_gain"])
        else:
            farm["phase"] = "cooldown"
            farm["next_time"] = next_time
            farm["last_error"] = "" if result in {"success", "failure"} else parsed.get("last_error") or ""
            _retreat_farm_audit(farm, now, "retreat_result", result=result, next_time=next_time)
        changed = True

    if changed:
        _set_tianxing_retreat_farm_state(farm, now)
    return changed


def _should_wake_tianxing_timeline(observed, config, now):
    observed = normalize_tianxing_observation(observed)
    config = normalize_tianxing_auto_config(config)
    if not config.get("timeline_enabled"):
        return False
    if not config.get("craft_farm_enabled"):
        return False
    windows = build_tianxing_farm_window(now=now, config=config, reason="天星自动调度")
    if not windows:
        return False
    plan = build_tianxing_timeline_plan(now=now, windows=windows, observed=observed, config=config)
    return any(str(step.get("action") or "").strip() for step in plan.get("steps") or [])


def apply_tianxing_passive(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    parsed = parse_tianxing_text(text, now=now, family=family)
    if not parsed:
        return False

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
    observed["last_observed_at"] = now
    for key in ("last_action", "last_result", "last_summary", "last_error", "fixed_star", "current_prediction", "current_change", "last_route", "last_star_effect", "available_stars_source"):
        source_key = key
        if key == "last_action":
            source_key = "action"
        elif key == "last_result":
            source_key = "result"
        elif key == "last_summary":
            source_key = "summary"
        value = parsed.get(source_key)
        if value is not None:
            observed[key] = value
    if parsed.get("available_stars") is not None:
        observed["available_stars"] = list(parsed.get("available_stars") or [])
    for key in ("current_prediction_until", "current_change_until"):
        if key in parsed:
            observed[key] = float(parsed.get(key) or 0)
    for key in ("tianji_value", "calamity_count", "hit_count", "miss_count", "change_count", "last_tianji_gain", "last_contrib_gain", "last_bonus_gain"):
        if parsed.get(key) is not None:
            observed[key] = int(parsed.get(key) or 0)
    if parsed.get("calamity_delta") is not None:
        observed["calamity_count"] = max(0, int(observed.get("calamity_count", 0) or 0) + int(parsed.get("calamity_delta") or 0))
    if parsed.get("action") == "消劫" and parsed.get("result") == "success":
        observed["calamity_count"] = max(0, int(observed.get("calamity_count", 0) or 0) - 1)
    if _auto_pending_matches_parsed(observed, parsed):
        _clear_tianxing_auto_pending(observed)
    observed["auto_last_error"] = ""
    if int(observed.get("calamity_count", 0) or 0) > 0:
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
    elif not observed.get("fixed_star"):
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
    elif _should_wake_tianxing_timeline(observed, config, now):
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
    else:
        observed["auto_next_time"] = max(float(observed.get("auto_next_time", 0) or 0), now + TIANXING_AUTO_STATUS_BACKOFF_SEC)
    observed["recent"].append({
        "ts": now,
        "action": observed.get("last_action", ""),
        "result": observed.get("last_result", ""),
        "summary": observed.get("last_summary", ""),
    })
    observed["recent"] = observed["recent"][-8:]
    state["tianxing_observation"] = observed
    _update_retreat_farm_from_parsed(parsed, observed, now, family)
    _update_craft_farm_from_parsed(parsed, observed, now, family)
    _update_tianxing_timeline_from_negative_observation(parsed, now)
    confirmed, _timeline = _confirm_tianxing_timeline_from_observation(now)
    if confirmed:
        observed = normalize_tianxing_observation(state.get("tianxing_observation"))
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
        state["tianxing_observation"] = observed
    return True


def _normalize_manual_action(action):
    raw = str(action or "").strip().lower()
    mapping = {
        "": "panel",
        "panel": "panel",
        "盘": "panel",
        "查盘": "panel",
        "天机盘": "panel",
        "observe": "observe",
        "观命": "observe",
        "set": "set_star",
        "star": "set_star",
        "set_star": "set_star",
        "定命": "set_star",
        "predict": "predict",
        "推命": "predict",
        "change": "change_fate",
        "change_fate": "change_fate",
        "改命": "change_fate",
        "clear": "clear_calamity",
        "clear_calamity": "clear_calamity",
        "消劫": "clear_calamity",
    }
    return mapping.get(raw, raw)


def _manual_block(action, reason, command="", family=""):
    return {
        "allowed": False,
        "action": action,
        "arg": "",
        "command": command,
        "family": family,
        "reason": reason,
    }


def _manual_allow(action, command, family, now):
    return {
        "allowed": True,
        "action": action,
        "arg": "",
        "command": command,
        "family": family,
        "reason": "天星宗手动动作允许发送。",
        "source_module": "天星宗",
        "op_id": f"tianxing-{action}-{int(now)}",
        "delete_policy": "manual_keep",
        "max_retry": 0,
    }


def _has_recent_observation(observed, now):
    last_observed_at = float(observed.get("last_observed_at", 0) or 0)
    return last_observed_at > 0 and now - last_observed_at <= TIANXING_OBSERVATION_STALE_SEC


def build_tianxing_manual_plan(action="panel", arg="", now=None, allow_prediction_override=False, allow_same_route_probe=False):
    now = float(now if now is not None else time.time())
    action = _normalize_manual_action(action)
    arg = str(arg or "").strip()
    if not state.get("tianxing_enabled"):
        return _manual_block(action, "天星宗模块未开启。")

    if action == "panel":
        return _manual_allow(action, CMD_TIANXING_PANEL, "tianxing_panel", now)
    if action == "observe":
        return _manual_allow(action, CMD_TIANXING_OBSERVE, "tianxing_observe", now)

    dirty_fields = _dirty_tianxing_time_fields(state.get("tianxing_observation"))
    if dirty_fields:
        return _manual_block(action, f"天星宗状态字段异常（{_format_list(dirty_fields)}），不猜测冷却或待办时间。")

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    last_observed_at = float(observed.get("last_observed_at", 0) or 0)
    if last_observed_at <= 0:
        return _manual_block(action, "缺少天星宗真实文案状态，先手动观命/查盘或等待消息盒子观察。")
    if now - last_observed_at > TIANXING_OBSERVATION_STALE_SEC:
        return _manual_block(action, f"天星宗状态过旧，最近观察 {fmt_abs_ts(last_observed_at)}。")

    if action == "set_star":
        star = arg
        if star not in TIANXING_STARS:
            return _manual_block(action, "定命必须指定：紫微、天府、太阴、贪狼。")
        fixed_star = str(observed.get("fixed_star") or "").strip()
        if fixed_star:
            return _manual_block(action, f"今日已定命星：{fixed_star}，不重复定命。")
        available_stars = [str(item) for item in observed.get("available_stars") or [] if str(item or "").strip()]
        if available_stars and star not in available_stars:
            return _manual_block(action, f"今日可选命星为 {_format_list(available_stars)}，不发送定命 {star}。")
        if not available_stars:
            return _manual_block(action, "未记录今日可选命星，先手动观命。")
        plan = _manual_allow(action, f"{CMD_TIANXING_SET_STAR} {star}", "tianxing_set_star", now)
        plan["arg"] = star
        return plan

    if action == "predict":
        route = arg
        if route not in TIANXING_ROUTES:
            return _manual_block(action, "推命必须指定：闭关、炼制、探索、斗法。")
        prediction_until = float(observed.get("current_prediction_until", 0) or 0)
        if prediction_until > now:
            current = str(observed.get("current_prediction") or "未记录").strip()
            if current == route:
                if not allow_same_route_probe:
                    return _manual_block(action, f"已有推命 {current} 尚未应验，{fmt_remaining(prediction_until)} 后再试。")
            elif not allow_prediction_override:
                return _manual_block(action, f"已有推命 {current} 尚未应验，{fmt_remaining(prediction_until)} 后再试。")
        if str(observed.get("current_prediction") or "").strip() and prediction_until <= 0:
            current = observed.get("current_prediction") or "未记录"
            return _manual_block(action, f"已有推命 {current} 尚未应验，但时间不可解析，不发送推命。")
        plan = _manual_allow(action, f"{CMD_TIANXING_PREDICT} {route}", "tianxing_predict", now)
        plan["arg"] = route
        return plan

    if action == "change_fate":
        route = arg
        if route not in TIANXING_ROUTES:
            return _manual_block(action, "改命必须指定：闭关、炼制、探索、斗法。")
        change_until = float(observed.get("current_change_until", 0) or 0)
        if change_until > now:
            current = observed.get("current_change") or "未记录"
            return _manual_block(action, f"已有改命 {current} 尚未触发，{fmt_remaining(change_until)} 后再试。")
        if str(observed.get("current_change") or "").strip() and change_until <= 0:
            current = observed.get("current_change") or "未记录"
            return _manual_block(action, f"已有改命 {current} 尚未触发，但时间不可解析，不发送改命。")
        if int(observed.get("tianji_value", 0) or 0) < 3:
            return _manual_block(action, f"天机值不足 3，当前记录为 {int(observed.get('tianji_value', 0) or 0)}。")
        plan = _manual_allow(action, f"{CMD_TIANXING_CHANGE_FATE} {route}", "tianxing_change_fate", now)
        plan["arg"] = route
        return plan

    if action == "clear_calamity":
        if int(observed.get("calamity_count", 0) or 0) <= 0:
            return _manual_block(action, "当前未记录逆命劫，不发送消劫。")
        return _manual_allow(action, CMD_TIANXING_CLEAR_CALAMITY, "tianxing_clear_calamity", now)

    return _manual_block(action, "未知天星宗手动动作。")


def _set_tianxing_auto_wait(observed, now, action, next_time=None, error=""):
    observed["auto_last_action"] = str(action or "")
    observed["auto_last_error"] = str(error or "")
    observed["auto_next_time"] = float(next_time or now + TIANXING_AUTO_BLOCK_BACKOFF_SEC)
    state["tianxing_observation"] = observed
    save_state()


_TIANXING_AUTO_PENDING_ACTIONS = {
    "panel": "天机盘",
    "observe": "观命",
    "set_star": "定命",
    "predict": "推命",
    "change_fate": "改命",
    "clear_calamity": "消劫",
}


def _clear_tianxing_auto_pending(observed):
    observed["auto_pending_action"] = ""
    observed["auto_pending_command"] = ""
    observed["auto_pending_msg_id"] = 0
    observed["auto_pending_sent_at"] = 0
    observed["auto_pending_due_at"] = 0


def _auto_pending_matches_parsed(observed, parsed):
    action = str((observed or {}).get("auto_pending_action") or "").strip()
    if not action:
        return False
    expected = _TIANXING_AUTO_PENDING_ACTIONS.get(action, "")
    return bool(expected) and str((parsed or {}).get("action") or "").strip() == expected


def _note_tianxing_auto_pending(observed, now, plan, config):
    action = str((plan or {}).get("action") or "").strip()
    command = str((plan or {}).get("command") or "").strip()
    timeout = int((config or {}).get("ack_timeout_sec", TIANXING_TIMELINE_ACK_TIMEOUT_SEC) or TIANXING_TIMELINE_ACK_TIMEOUT_SEC)
    observed["auto_pending_action"] = action
    observed["auto_pending_command"] = command
    observed["auto_pending_msg_id"] = 0
    observed["auto_pending_sent_at"] = float(now)
    observed["auto_pending_due_at"] = float(now + max(15, timeout))
    observed["auto_last_action"] = action
    observed["auto_last_error"] = ""
    observed["auto_last_plan"] = command
    observed["auto_last_plan_at"] = float(now)
    observed["auto_next_time"] = observed["auto_pending_due_at"]


def _handle_tianxing_auto_pending(observed, now):
    action = str((observed or {}).get("auto_pending_action") or "").strip()
    if not action:
        return False
    due_at = float((observed or {}).get("auto_pending_due_at", 0) or 0)
    if due_at <= 0 or now < due_at:
        return True
    command = str((observed or {}).get("auto_pending_command") or "").strip()
    _clear_tianxing_auto_pending(observed)
    observed["auto_last_action"] = action
    observed["auto_last_plan"] = command
    observed["auto_last_error"] = "天星宗自动动作回复超时，暂缓重试；不继续推进下游。"
    observed["auto_next_time"] = float(now + TIANXING_AUTO_SEND_FAIL_BACKOFF_SEC)
    state["tianxing_observation"] = observed
    save_state()
    return True


def _status_backoff_sec(config):
    return max(3600, int(float((config or {}).get("status_backoff_hours", 6) or 6) * 3600))


def _choose_by_priority(candidates, priority):
    candidates = [str(item).strip() for item in (candidates or []) if str(item or "").strip()]
    for item in priority or []:
        if item in candidates:
            return item
    return candidates[0] if candidates else ""


def _choose_config_route(config, key, observed=None):
    route = _normalize_route_choice((config or {}).get(key), TIANXING_ROUTE_AUTO)
    if route != TIANXING_ROUTE_AUTO:
        return route
    if key == "change_route":
        current_prediction = str((observed or {}).get("current_prediction") or "").strip()
        if current_prediction in TIANXING_ROUTES:
            return current_prediction
    return _choose_by_priority(TIANXING_ROUTES, (config or {}).get("route_priority") or [])


def _build_tianxing_strategy_plan(observed, config, now):
    if not _has_recent_observation(observed, now):
        return _manual_block("idle", "缺少近期天星宗状态。")

    fixed_star = str(observed.get("fixed_star") or "").strip()
    available_stars = [str(item).strip() for item in observed.get("available_stars") or [] if str(item or "").strip()]
    if config.get("timeline_enabled"):
        return _manual_block("timeline_required", "定命/推命/改命需由上层时间线规划器授权，当前自动调度不直接发送。")
    if not fixed_star:
        if available_stars and config.get("auto_set_star_enabled"):
            star = _choose_by_priority(available_stars, config.get("star_priority") or [])
            if not star:
                return _manual_block("set_star", "未能从今日可选命星中选出目标。")
            return build_tianxing_manual_plan("set_star", star, now=now)
        return _manual_block("idle", "未定命星，自动定命未开启。")
    if config.get("auto_predict_enabled") or config.get("auto_change_fate_enabled"):
        return _manual_block("timeline_required", "推命/改命需由上层时间线规划器授权，当前自动调度不直接发送。")
    return _manual_block("idle", "未满足自动定命条件。")


def _normalize_tianxing_window(item, now, horizon_end):
    if not isinstance(item, dict):
        return {}
    route = _normalize_route_choice(item.get("route"), "")
    if route not in TIANXING_ROUTES:
        return {}
    try:
        start_at = float(item.get("start_at", now) or now)
    except (TypeError, ValueError, OverflowError):
        start_at = float(now)
    try:
        end_at = float(item.get("end_at", start_at) or start_at)
    except (TypeError, ValueError, OverflowError):
        end_at = float(start_at)
    if end_at < start_at:
        end_at = start_at
    if end_at < now or start_at > horizon_end:
        return {}
    kind = str(item.get("kind") or "consume").strip().lower()
    if kind not in {"farm", "consume", "neutral"}:
        kind = "consume"
    try:
        weight = max(1.0, float(item.get("weight", 1.0) or 1.0))
    except (TypeError, ValueError, OverflowError):
        weight = 1.0
    reason = str(item.get("reason") or item.get("label") or route).strip() or route
    return {
        "route": route,
        "kind": kind,
        "start_at": start_at,
        "end_at": end_at,
        "weight": weight,
        "reason": reason,
    }


def build_tianxing_farm_window(*, now=None, config=None, reason="深度闭关"):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    if not config.get("farm_window_enabled"):
        return []
    route = _normalize_route_choice(config.get("farm_route"), "闭关")
    if route not in TIANXING_ROUTES:
        return []
    duration_sec = int(config.get("farm_window_duration_min", 60) or 60) * 60
    if duration_sec <= 0:
        return []
    local_time = time.localtime(now)
    midnight = time.mktime((
        local_time.tm_year,
        local_time.tm_mon,
        local_time.tm_mday,
        0,
        0,
        0,
        local_time.tm_wday,
        local_time.tm_yday,
        local_time.tm_isdst,
    ))
    start_offset = _hhmm_to_seconds(config.get("farm_window_start", "02:00"))
    for day_offset in (0, -24 * 3600):
        start_at = float(midnight + day_offset + start_offset)
        end_at = float(start_at + duration_sec)
        if start_at <= now <= end_at:
            return [{
                "route": route,
                "kind": "farm",
                "start_at": start_at,
                "end_at": end_at,
                "weight": 8,
                "reason": str(reason or "天星 Farm 窗口"),
            }]
    return []


def build_tianxing_consume_window(route, *, now=None, due_at=0, config=None, reason="路线动作"):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    route = _normalize_route_choice(route, "")
    if route not in TIANXING_ROUTES:
        return []
    try:
        due_at = float(due_at or now)
    except (TypeError, ValueError, OverflowError):
        due_at = float(now)
    lead_sec = int(config.get("route_prepare_lead_sec", 5 * 60) or 5 * 60)
    start_at = max(float(now), due_at - lead_sec)
    if now < start_at:
        return []
    return [{
        "route": route,
        "kind": "consume",
        "start_at": start_at,
        "end_at": max(due_at + TIANXING_TIME_BUFFER_SEC, now + TIANXING_TIME_BUFFER_SEC),
        "weight": 10,
        "reason": str(reason or route).strip() or route,
    }]


def _timeline_step_command(action, arg):
    action = str(action or "").strip()
    arg = str(arg or "").strip()
    if action == "set_star":
        return f"{CMD_TIANXING_SET_STAR} {arg}", "tianxing_set_star", {"fixed_star": arg}
    if action == "predict":
        return f"{CMD_TIANXING_PREDICT} {arg}", "tianxing_predict", {"current_prediction": arg}
    if action == "change_fate":
        return f"{CMD_TIANXING_CHANGE_FATE} {arg}", "tianxing_change_fate", {"current_change": arg}
    if action == "clear_calamity":
        return CMD_TIANXING_CLEAR_CALAMITY, "tianxing_clear_calamity", {"calamity_count_decreased": True}
    if action == "panel":
        return CMD_TIANXING_PANEL, "tianxing_panel", {"last_observed_at": "fresh"}
    if action == "observe":
        return CMD_TIANXING_OBSERVE, "tianxing_observe", {"available_stars": "known"}
    return "", "", {}


def _make_tianxing_timeline_step(action, arg="", *, route="", reason="", now=0, release_basis="", probe_existing_prediction=False):
    command, family, expected_state = _timeline_step_command(action, arg)
    route = _normalize_route_choice(route or arg, "") if action in {"predict", "change_fate", "release_downstream"} else str(route or "").strip()
    step = {
        "id": f"{action}:{arg or route}:{int(float(now or 0))}",
        "action": str(action or "").strip(),
        "arg": str(arg or "").strip(),
        "route": route,
        "command": command,
        "expected_family": family,
        "expected_state": expected_state,
        "send_msg_id": 0,
        "sent_at": 0,
        "ack_due_at": 0,
        "calibration_due_at": 0,
        "status": "pending",
        "reason": str(reason or "").strip(),
    }
    if action == "release_downstream" and release_basis:
        step["release_basis"] = str(release_basis or "").strip()
    if action == "predict" and probe_existing_prediction:
        step["probe_existing_prediction"] = True
    return step


def _append_tianxing_step(steps, action, arg="", *, route="", reason="", now=0, release_basis="", probe_existing_prediction=False):
    step = _make_tianxing_timeline_step(
        action,
        arg,
        route=route,
        reason=reason,
        now=now,
        release_basis=release_basis,
        probe_existing_prediction=probe_existing_prediction,
    )
    if not step.get("action"):
        return
    dedupe_key = (step.get("action"), step.get("arg"), step.get("route"))
    for existing in steps:
        if (existing.get("action"), existing.get("arg"), existing.get("route")) == dedupe_key:
            return
    steps.append(step)


def _next_consume_route(normalized_windows):
    consume_windows = [item for item in normalized_windows if item["kind"] == "consume"]
    if not consume_windows:
        return "", {}
    next_consume = sorted(consume_windows, key=lambda item: (float(item.get("start_at", 0) or 0), item.get("route") or ""))[0]
    return str(next_consume.get("route") or "").strip(), next_consume


def _last_craft_farm_result_at(timeline):
    farm = normalize_tianxing_craft_farm_state((timeline or {}).get("craft_farm"))
    latest = 0.0
    for entry in farm.get("audit") or []:
        if not isinstance(entry, dict) or str(entry.get("event") or "") != "craft_result":
            continue
        try:
            latest = max(latest, float(entry.get("ts", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            continue
    return latest


def _has_fresh_prediction_evidence(route, observed, timeline, now):
    route = _normalize_route_choice(route, "")
    if route not in TIANXING_ROUTES:
        return False
    observed = normalize_tianxing_observation(observed)
    if str(observed.get("current_prediction") or "").strip() != route:
        return False
    if float(observed.get("current_prediction_until", 0) or 0) <= float(now):
        return False
    if str(observed.get("last_action") or "").strip() != "推命":
        return False
    if str(observed.get("last_result") or "").strip() not in {"success", "cooldown"}:
        return False
    if _normalize_route_choice(observed.get("last_route"), "") != route:
        return False
    observed_at = float(observed.get("last_observed_at", 0) or 0)
    return observed_at > _last_craft_farm_result_at(timeline) + 0.001


def build_tianxing_timeline_plan(*, now=None, horizon_hours=8, windows=None, observed=None, config=None):
    now = float(now if now is not None else time.time())
    horizon_hours = _coerce_float_range(horizon_hours, 8, 1, 24)
    horizon_end = now + horizon_hours * 3600
    observed = normalize_tianxing_observation(observed if observed is not None else state.get("tianxing_observation"))
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    normalized_windows = []
    for item in windows or []:
        normalized = _normalize_tianxing_window(item, now, horizon_end)
        if normalized:
            normalized_windows.append(normalized)

    route_stats = {}
    for item in normalized_windows:
        route = item["route"]
        stats = route_stats.setdefault(
            route,
            {
                "route": route,
                "farm_weight": 0.0,
                "consume_weight": 0.0,
                "total_weight": 0.0,
                "window_count": 0,
                "first_start_at": item["start_at"],
            },
        )
        stats["total_weight"] += item["weight"]
        stats["window_count"] += 1
        stats["first_start_at"] = min(float(stats.get("first_start_at", item["start_at"]) or item["start_at"]), item["start_at"])
        if item["kind"] == "farm":
            stats["farm_weight"] += item["weight"]
        elif item["kind"] == "consume":
            stats["consume_weight"] += item["weight"]

    dominant_route = ""
    if route_stats:
        dominant_route = sorted(
            route_stats.values(),
            key=lambda item: (-float(item.get("farm_weight", 0) or 0), -float(item.get("total_weight", 0) or 0), float(item.get("first_start_at", 0) or 0), item.get("route") or ""),
        )[0]["route"]
    dominant_stats = route_stats.get(dominant_route) or {}
    dominant_is_farm = float(dominant_stats.get("farm_weight", 0) or 0) > 0

    current_prediction = str(observed.get("current_prediction") or "").strip()
    prediction_until = float(observed.get("current_prediction_until", 0) or 0)
    current_change = str(observed.get("current_change") or "").strip()
    change_until = float(observed.get("current_change_until", 0) or 0)
    fixed_star = str(observed.get("fixed_star") or "").strip()
    tianji_value = int(observed.get("tianji_value", 0) or 0)
    min_tianji = int(config.get("min_tianji_for_change", 6) or 6)

    stage = "idle"
    should_predict = False
    blocked_by_conflict = False
    blocked_until = 0.0
    predict_reason = ""
    recommended_change_route = ""
    change_reason = ""
    release_route = ""
    release_reason = ""
    steps = []

    if dominant_route and current_prediction and current_prediction != dominant_route and prediction_until > now and not config.get("allow_prediction_override_enabled"):
        blocked_by_conflict = True
        blocked_until = prediction_until
        stage = "prediction_conflict"
        predict_reason = f"已有 {current_prediction} 推命仍在生效，当前时间线不应切到 {dominant_route}。"
    elif dominant_route and current_prediction and current_prediction != dominant_route and prediction_until > now:
        should_predict = True
        stage = "need_predict_override"
        predict_reason = f"已有 {current_prediction} 推命仍在生效，配置允许尝试改押 {dominant_route}；以真实回包为准。"
        _append_tianxing_step(steps, "predict", dominant_route, route=dominant_route, reason=predict_reason, now=now)
    elif dominant_route and not fixed_star:
        stage = "need_set_star"
        predict_reason = "时间线已形成，但今日尚未定命。"
        available_stars = [str(item).strip() for item in observed.get("available_stars") or [] if str(item or "").strip()]
        star_source = str(observed.get("available_stars_source") or "").strip()
        if star_source != "observe" and config.get("auto_observe_enabled"):
            _append_tianxing_step(steps, "observe", reason="定命前需先取得今日观命结果。", now=now)
        elif available_stars and config.get("auto_set_star_enabled"):
            star = _choose_by_priority(available_stars, config.get("star_priority") or [])
            _append_tianxing_step(steps, "set_star", star, reason="时间线执行前先定命。", now=now)
        elif not available_stars and config.get("auto_observe_enabled"):
            _append_tianxing_step(steps, "observe", reason="时间线执行前先观命。", now=now)
    elif dominant_route and current_prediction == dominant_route and prediction_until > now:
        if dominant_is_farm and config.get("auto_predict_enabled") and not _has_fresh_prediction_evidence(dominant_route, observed, timeline, now):
            should_predict = True
            stage = "need_predict_probe"
            predict_reason = f"{dominant_route} 推命只来自面板或旧状态，Farm 先复核推命再放行。"
            _append_tianxing_step(
                steps,
                "predict",
                dominant_route,
                route=dominant_route,
                reason=predict_reason,
                now=now,
                probe_existing_prediction=True,
            )
        else:
            stage = "ready_prediction"
            predict_reason = f"{dominant_route} 推命已由近期真实回复确认，无需重复押注。"
    elif dominant_route and dominant_is_farm and config.get("auto_predict_enabled"):
        should_predict = True
        stage = "need_predict"
        predict_reason = f"{dominant_route} 在未来 {int(horizon_hours)}h 内承担主 Farm 窗口，应由时间线规划器决定是否推命。"
        _append_tianxing_step(steps, "predict", dominant_route, route=dominant_route, reason=predict_reason, now=now)
    elif dominant_route and dominant_is_farm:
        stage = "observe_only"
        predict_reason = f"{dominant_route} 在未来 {int(horizon_hours)}h 内承担主 Farm 窗口，但自动推命关闭。"
    elif dominant_route:
        stage = "observe_only"
        predict_reason = f"{dominant_route} 只有消费窗口，没有稳定 Farm 窗口，不建议盲发推命。"

    next_consume_route, next_consume = _next_consume_route(normalized_windows)
    if next_consume_route:
        if current_change == next_consume_route and change_until > now:
            recommended_change_route = next_consume_route
            change_reason = f"{recommended_change_route} 改命已待发。"
            release_route = recommended_change_route
            release_reason = f"{recommended_change_route} 改命已确认，可等待下游模块消费。"
        elif not current_change and config.get("auto_change_fate_enabled") and tianji_value >= min_tianji:
            recommended_change_route = next_consume_route
            if not change_reason:
                change_reason = f"最近消费窗口是 {recommended_change_route}，若要兜底可预留改命。"
            _append_tianxing_step(steps, "change_fate", recommended_change_route, route=recommended_change_route, reason=change_reason, now=now)
            release_route = recommended_change_route
            release_reason = f"{recommended_change_route} 改命确认后放行下游。"
        elif not current_change and not config.get("auto_change_fate_enabled"):
            change_reason = f"最近消费窗口是 {next_consume_route}，但自动改命关闭。"
        elif current_change and change_until > now:
            recommended_change_route = current_change
            change_reason = f"已有 {current_change} 改命待发，不覆盖。"
        elif tianji_value < min_tianji:
            change_reason = f"天机值 {tianji_value} 低于改命阈值 {min_tianji}。"

    if not release_route and dominant_route and (should_predict or current_prediction == dominant_route):
        release_route = dominant_route
        release_reason = f"{dominant_route} 推命确认后放行对应路线。"
    if release_route and not blocked_by_conflict:
        if current_prediction and current_prediction != release_route and prediction_until > now:
            release_reason = f"已有 {current_prediction} 推命未应验，暂不放行 {release_route}。"
        else:
            release_basis = "prediction" if should_predict or (current_prediction == release_route and prediction_until > now) else "change_fate"
            _append_tianxing_step(
                steps,
                "release_downstream",
                release_route,
                route=release_route,
                reason=release_reason,
                now=now,
                release_basis=release_basis,
            )

    return {
        "lab_only": True,
        "planned_at": now,
        "horizon_hours": horizon_hours,
        "dominant_route": dominant_route,
        "stage": stage,
        "should_predict": bool(should_predict),
        "predict_route": dominant_route if should_predict else "",
        "predict_reason": predict_reason,
        "recommended_change_route": recommended_change_route,
        "change_reason": change_reason,
        "release_route": release_route,
        "release_reason": release_reason,
        "steps": steps,
        "blocked_by_conflict": bool(blocked_by_conflict),
        "blocked_until": float(blocked_until or 0),
        "windows": normalized_windows,
        "route_stats": sorted(route_stats.values(), key=lambda item: (-float(item.get("farm_weight", 0) or 0), -float(item.get("total_weight", 0) or 0), item.get("route") or "")),
    }


def _timeline_audit(timeline, now, event, **extra):
    entry = {"ts": float(now or 0), "event": str(event or "")}
    for key, value in extra.items():
        if value is not None:
            entry[key] = value
    audit = list(timeline.get("audit") or [])
    audit.append(entry)
    timeline["audit"] = audit[-20:]


def _timeline_active_index(timeline):
    try:
        raw_index = (timeline or {}).get("active_step_index", -1)
        if raw_index is None or raw_index == "":
            raw_index = -1
        return int(raw_index)
    except (TypeError, ValueError, OverflowError):
        return -1


def _identity_lock(locks):
    send_as_id = int(get_current_identity_id() or 0)
    if send_as_id <= 0:
        send_as_id = 0
    lock = locks.get(send_as_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[send_as_id] = lock
    return lock


def _timeline_lock():
    return _identity_lock(_TIANXING_TIMELINE_LOCKS)


def _auto_lock():
    return _identity_lock(_TIANXING_AUTO_LOCKS)


def _timeline_plan_id(plan, now):
    route = str((plan or {}).get("release_route") or (plan or {}).get("dominant_route") or "idle").strip() or "idle"
    return f"tianxing-timeline-{route}-{int(float(now or 0))}"


def _set_timeline_step(timeline, index, step):
    steps = list(timeline.get("steps") or [])
    if 0 <= int(index) < len(steps):
        steps[int(index)] = dict(step or {})
        timeline["steps"] = steps
    timeline["active_step"] = dict(step or {})


def _activate_timeline_step(timeline, index, now):
    steps = list(timeline.get("steps") or [])
    if 0 <= int(index) < len(steps):
        step = dict(steps[int(index)] or {})
        if not step.get("status") or step.get("status") in {"state_confirmed", "confirmed"}:
            step["status"] = "pending"
        timeline["active_step_index"] = int(index)
        timeline["active_step"] = step
        timeline["phase"] = "waiting_send"
        timeline["updated_at"] = float(now)
        steps[int(index)] = step
        timeline["steps"] = steps
        _timeline_audit(timeline, now, "activate_step", action=step.get("action"), arg=step.get("arg"), route=step.get("route"))
        return True
    timeline["active_step_index"] = -1
    timeline["active_step"] = {}
    timeline["phase"] = "completed"
    timeline["updated_at"] = float(now)
    _timeline_audit(timeline, now, "completed")
    return False


def _timeline_step_is_confirmed(step, observed, now):
    action = str((step or {}).get("action") or "").strip()
    arg = str((step or {}).get("arg") or "").strip()
    sent_at = float((step or {}).get("sent_at", 0) or 0)
    observed_at = float((observed or {}).get("last_observed_at", 0) or 0)
    if sent_at > 0 and observed_at > 0 and observed_at + 0.001 < sent_at:
        return False
    if action == "set_star":
        return bool(arg) and str((observed or {}).get("fixed_star") or "").strip() == arg
    if action == "predict":
        return (
            bool(arg)
            and str((observed or {}).get("current_prediction") or "").strip() == arg
            and float((observed or {}).get("current_prediction_until", 0) or 0) > float(now)
        )
    if action == "change_fate":
        return (
            bool(arg)
            and str((observed or {}).get("current_change") or "").strip() == arg
            and float((observed or {}).get("current_change_until", 0) or 0) > float(now)
        )
    if action == "clear_calamity":
        return str((observed or {}).get("last_action") or "").strip() == "消劫" and str((observed or {}).get("last_result") or "").strip() == "success"
    if action == "panel":
        return observed_at > 0 and str((observed or {}).get("last_action") or "").strip() in {"天机盘", "玩法帮助", "宗门信息", ""}
    if action == "observe":
        return observed_at > 0 and bool((observed or {}).get("available_stars") or [])
    return False


def _confirm_tianxing_timeline_from_observation(now):
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    step = dict(timeline.get("active_step") or {})
    if str(step.get("status") or "") != "sent_waiting_ack":
        return False, timeline
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if not _timeline_step_is_confirmed(step, observed, now):
        return False, timeline
    step["status"] = "confirmed"
    step["confirmed_at"] = float(now)
    timeline["phase"] = "state_confirmed"
    timeline["last_error"] = ""
    timeline["updated_at"] = float(now)
    _set_timeline_step(timeline, _timeline_active_index(timeline), step)
    _timeline_audit(timeline, now, "state_confirmed", action=step.get("action"), arg=step.get("arg"), route=step.get("route"))
    state["tianxing_timeline_state"] = timeline
    return True, timeline


def _update_tianxing_timeline_from_negative_observation(parsed, now):
    parsed = parsed if isinstance(parsed, dict) else {}
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    step = dict(timeline.get("active_step") or {})
    action = str(parsed.get("action") or "")
    result = str(parsed.get("result") or "")

    if action == "定命" and result == "need_observe":
        if str(step.get("action") or "") != "set_star":
            return False
        step["status"] = "rejected_need_observe"
        step["rejected_at"] = float(now)
        step["last_error"] = parsed.get("last_error") or "定命失败，需先观命。"
        _set_timeline_step(timeline, _timeline_active_index(timeline), step)
        timeline["phase"] = "blocked_replan"
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["blocked_until"] = float(now)
        timeline["last_error"] = step["last_error"]
        timeline["updated_at"] = float(now)
        _timeline_audit(timeline, now, "set_star_need_observe", arg=step.get("arg"), reason=timeline["last_error"])
        state["tianxing_timeline_state"] = timeline
        return True

    if action != "推命" or result != "cooldown":
        return False
    if str(step.get("action") or "") != "predict":
        return False
    if str(step.get("status") or "") not in {"sending", "sent_waiting_ack", "ack_timeout"}:
        return False

    desired_route = _normalize_route_choice(step.get("route") or step.get("arg"), "")
    existing_route = _normalize_route_choice(parsed.get("current_prediction") or parsed.get("last_route"), "")
    prediction_until = float(parsed.get("current_prediction_until", 0) or 0)
    if prediction_until <= now:
        prediction_until = float(now + _status_backoff_sec(normalize_tianxing_auto_config(state.get("tianxing_auto_config"))))

    if desired_route and existing_route == desired_route:
        step["status"] = "confirmed_existing_prediction"
        step["confirmed_at"] = float(now)
        step["last_error"] = ""
        _set_timeline_step(timeline, _timeline_active_index(timeline), step)
        timeline["phase"] = "state_confirmed"
        timeline["blocked_until"] = 0
        timeline["last_error"] = ""
        timeline["updated_at"] = float(now)
        _timeline_audit(timeline, now, "predict_existing_confirmed", route=desired_route)
        state["tianxing_timeline_state"] = timeline
        return True

    step["status"] = "rejected_prediction_conflict"
    step["rejected_at"] = float(now)
    step["last_error"] = (
        f"已有 {existing_route or '其他'} 推命尚未应验，不能切到 {desired_route or '目标路线'}；"
        "等待当前推命消费或过期。"
    )
    _set_timeline_step(timeline, _timeline_active_index(timeline), step)
    timeline["phase"] = "prediction_conflict"
    timeline["active_step_index"] = -1
    timeline["active_step"] = {}
    timeline["blocked_until"] = prediction_until
    timeline["last_error"] = step["last_error"]
    timeline["updated_at"] = float(now)
    _timeline_audit(
        timeline,
        now,
        "predict_conflict_cooldown",
        desired=desired_route,
        existing=existing_route,
        blocked_until=prediction_until,
    )

    craft_farm = normalize_tianxing_craft_farm_state(timeline.get("craft_farm"))
    if desired_route == "炼制" and craft_farm.get("phase") in {"timeline_waiting", "ready", "idle"}:
        craft_farm["phase"] = "prediction_conflict"
        craft_farm["next_time"] = prediction_until
        craft_farm["last_result"] = "推命改押被游戏拒绝"
        craft_farm["last_error"] = timeline["last_error"]
        _craft_farm_audit(craft_farm, now, "prediction_conflict", existing=existing_route, blocked_until=prediction_until)
        timeline["craft_farm"] = craft_farm

    retreat_farm = normalize_tianxing_retreat_farm_state(timeline.get("retreat_farm"))
    if desired_route == "闭关" and retreat_farm.get("phase") in {"timeline_waiting", "ready", "idle"}:
        retreat_farm["phase"] = "prediction_conflict"
        retreat_farm["next_time"] = prediction_until
        retreat_farm["last_result"] = "推命改押被游戏拒绝"
        retreat_farm["last_error"] = timeline["last_error"]
        _retreat_farm_audit(retreat_farm, now, "prediction_conflict", existing=existing_route, blocked_until=prediction_until)
        timeline["retreat_farm"] = retreat_farm

    state["tianxing_timeline_state"] = timeline
    return True


def _build_tianxing_timeline_state_from_plan(plan, now, config):
    steps = [dict(item) for item in (plan or {}).get("steps") or [] if isinstance(item, dict)]
    previous = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    timeline = normalize_tianxing_timeline_state({})
    timeline["retreat_farm"] = normalize_tianxing_retreat_farm_state(previous.get("retreat_farm"))
    timeline["craft_farm"] = normalize_tianxing_craft_farm_state(previous.get("craft_farm"))
    timeline["plan_id"] = _timeline_plan_id(plan, now)
    timeline["route"] = str((plan or {}).get("release_route") or (plan or {}).get("dominant_route") or "").strip()
    timeline["reason"] = str((plan or {}).get("release_reason") or (plan or {}).get("predict_reason") or (plan or {}).get("change_reason") or "").strip()
    timeline["created_at"] = float(now)
    timeline["updated_at"] = float(now)
    timeline["deadline_at"] = float(now + float((plan or {}).get("horizon_hours", 8) or 8) * 3600)
    timeline["steps"] = steps
    if (plan or {}).get("blocked_by_conflict"):
        timeline["phase"] = "prediction_conflict"
        timeline["blocked_until"] = float((plan or {}).get("blocked_until", 0) or 0)
        timeline["last_error"] = str((plan or {}).get("predict_reason") or "已有异路推命未应验。")
        _timeline_audit(timeline, now, "prediction_conflict", blocked_until=timeline["blocked_until"])
        return timeline
    if not steps:
        timeline["phase"] = "idle"
        timeline["blocked_until"] = float(now + _status_backoff_sec(config))
        _timeline_audit(timeline, now, "no_steps", stage=(plan or {}).get("stage"))
        return timeline
    if config.get("timeline_dry_run_enabled"):
        dry_steps = []
        for step in steps:
            dry_step = dict(step)
            dry_step["status"] = "dry_run"
            dry_step["dry_run_at"] = float(now)
            dry_steps.append(dry_step)
        timeline["steps"] = dry_steps
        timeline["phase"] = "dry_run"
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["blocked_until"] = float(now + _status_backoff_sec(config))
        _timeline_audit(timeline, now, "dry_run", step_count=len(dry_steps))
        return timeline
    _activate_timeline_step(timeline, 0, now)
    _timeline_audit(timeline, now, "plan_created", step_count=len(steps))
    return timeline


def _release_tianxing_downstream(timeline, step, now):
    route = _normalize_route_choice((step or {}).get("route") or (step or {}).get("arg"), "")
    if route not in TIANXING_ROUTES:
        timeline["phase"] = "blocked_replan"
        timeline["last_error"] = "时间线放行步骤缺少有效路线。"
        timeline["updated_at"] = float(now)
        _timeline_audit(timeline, now, "release_blocked", reason=timeline["last_error"])
        return timeline
    released = dict(timeline.get("released_routes") or {})
    released[route] = {
        "released_at": float(now),
        "plan_id": timeline.get("plan_id") or "",
        "reason": (step or {}).get("reason") or timeline.get("reason") or "",
        "basis": (step or {}).get("release_basis") or "",
    }
    step = dict(step or {})
    step["status"] = "released"
    step["released_at"] = float(now)
    timeline["released_routes"] = released
    timeline["phase"] = "downstream_released"
    timeline["last_error"] = ""
    timeline["updated_at"] = float(now)
    _set_timeline_step(timeline, _timeline_active_index(timeline), step)
    _timeline_audit(timeline, now, "downstream_released", route=route)
    return timeline


def _schedule_tianxing_timeline_calibration(timeline, now):
    calibration = _make_tianxing_timeline_step("panel", reason="战略动作回复超时后查盘校准。", now=now)
    calibration["id"] = f"calibration:{int(float(now or 0))}"
    calibration["terminal_after_confirm"] = True
    steps = list(timeline.get("steps") or [])
    index = _timeline_active_index(timeline)
    insert_at = max(0, index + 1)
    steps = steps[:insert_at] + [calibration]
    timeline["steps"] = steps
    _activate_timeline_step(timeline, insert_at, now)
    timeline["phase"] = "calibrating"
    _timeline_audit(timeline, now, "calibration_scheduled")
    return timeline


async def _send_tianxing_timeline_step(timeline, step, now, config):
    action = str((step or {}).get("action") or "").strip()
    arg = str((step or {}).get("arg") or "").strip()
    if action == "release_downstream":
        return _release_tianxing_downstream(timeline, step, now)

    plan = build_tianxing_manual_plan(
        action,
        arg,
        now=now,
        allow_prediction_override=bool(config.get("allow_prediction_override_enabled")),
        allow_same_route_probe=bool((step or {}).get("probe_existing_prediction")),
    )
    if not plan.get("allowed"):
        step = dict(step or {})
        step["status"] = "send_blocked"
        step["blocked_at"] = float(now)
        step["last_error"] = plan.get("reason") or "天星时间线步骤不允许发送。"
        timeline["phase"] = "send_blocked"
        timeline["last_error"] = step["last_error"]
        timeline["updated_at"] = float(now)
        _set_timeline_step(timeline, _timeline_active_index(timeline), step)
        _timeline_audit(timeline, now, "send_blocked", action=action, arg=arg, reason=step["last_error"])
        return timeline

    step = dict(step or {})
    step["status"] = "sending"
    step["send_started_at"] = float(now)
    step["ack_due_at"] = float(now + int(config.get("ack_timeout_sec", TIANXING_TIMELINE_ACK_TIMEOUT_SEC) or TIANXING_TIMELINE_ACK_TIMEOUT_SEC))
    timeline["phase"] = "sending"
    timeline["last_error"] = ""
    timeline["updated_at"] = float(now)
    _set_timeline_step(timeline, _timeline_active_index(timeline), step)
    _timeline_audit(timeline, now, "sending", action=action, arg=arg)
    state["tianxing_timeline_state"] = timeline
    save_state()

    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=0,
        priority="normal",
        source_module="天星宗",
        op_id=f"tianxing-timeline-{action}-{int(now)}",
    )
    step = dict(step or {})
    sent_at = float(now)
    if msg:
        parsed_sent_at, sent_at_dirty = _parse_observation_float(getattr(msg, "sent_at", 0))
        if not sent_at_dirty and parsed_sent_at > 0:
            sent_at = parsed_sent_at
    if not msg:
        step["status"] = "ack_timeout"
        step["timeout_at"] = float(now)
        step["calibration_due_at"] = float(now + int(config.get("calibration_backoff_sec", TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC) or TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC))
        step["last_error"] = "天星时间线发送未返回消息ID，等待查盘校准；不重复发送。"
        timeline["phase"] = "ack_timeout"
        timeline["blocked_until"] = step["calibration_due_at"]
        timeline["last_error"] = step["last_error"]
        timeline["updated_at"] = float(now)
        _set_timeline_step(timeline, _timeline_active_index(timeline), step)
        _timeline_audit(timeline, now, "send_unknown_calibration_wait", action=action, arg=arg, reason=step["last_error"])
        return timeline

    step["status"] = "sent_waiting_ack"
    step["send_msg_id"] = int(getattr(msg, "id", 0) or 0)
    step["sent_at"] = float(sent_at)
    step["ack_due_at"] = float(sent_at + int(config.get("ack_timeout_sec", TIANXING_TIMELINE_ACK_TIMEOUT_SEC) or TIANXING_TIMELINE_ACK_TIMEOUT_SEC))
    timeline["phase"] = "sent_waiting_ack"
    timeline["last_error"] = ""
    timeline["updated_at"] = float(sent_at)
    _set_timeline_step(timeline, _timeline_active_index(timeline), step)
    _timeline_audit(timeline, sent_at, "sent_waiting_ack", action=action, arg=arg, msg_id=step["send_msg_id"])
    return timeline


def is_tianxing_route_released(route, *, now=None, max_age_sec=3600):
    now = float(now if now is not None else time.time())
    route = _normalize_route_choice(route, "")
    if route not in TIANXING_ROUTES:
        return False
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    released = timeline.get("released_routes") or {}
    item = released.get(route) or {}
    released_at = float(item.get("released_at", 0) or 0)
    if released_at <= 0 or now - released_at > float(max_age_sec or 3600):
        return False
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    prediction_active = (
        str(observed.get("current_prediction") or "").strip() == route
        and float(observed.get("current_prediction_until", 0) or 0) > now
    )
    change_active = (
        str(observed.get("current_change") or "").strip() == route
        and float(observed.get("current_change_until", 0) or 0) > now
    )
    basis = str(item.get("basis") or item.get("release_basis") or "").strip()
    if basis == "prediction":
        return prediction_active
    if basis == "change_fate":
        return change_active
    return prediction_active or change_active


async def _run_tianxing_timeline_scheduler_unlocked(now, *, windows=None, config=None, horizon_hours=8):
    now = float(now if now is not None else time.time())
    effective_config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    if not state.get("tianxing_enabled"):
        return {"phase": "disabled", "changed": False, "reason": "天星宗模块未开启。"}
    if not is_module_available("天星宗"):
        return {"phase": "unavailable", "changed": False, "reason": "当前身份不是天星宗。"}
    if not effective_config.get("timeline_enabled"):
        timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
        return {"phase": timeline.get("phase") or "idle", "changed": False, "reason": "时间线规划未开启。"}

    dirty_fields = _dirty_tianxing_time_fields(state.get("tianxing_observation"))
    if dirty_fields:
        timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
        timeline["phase"] = "dirty_state"
        timeline["last_error"] = f"天星宗状态字段异常（{_format_list(dirty_fields)}）。"
        timeline["updated_at"] = float(now)
        _timeline_audit(timeline, now, "dirty_state", fields=_format_list(dirty_fields))
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": "dirty_state", "changed": True, "reason": timeline["last_error"]}

    confirmed, timeline = _confirm_tianxing_timeline_from_observation(now)
    if confirmed:
        save_state()
        return {"phase": "state_confirmed", "changed": True, "reason": "天星战略动作已由真实状态确认。"}

    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    if float(timeline.get("blocked_until", 0) or 0) > now:
        return {"phase": timeline.get("phase") or "blocked", "changed": False, "reason": timeline.get("last_error") or "时间线等待中。"}

    active_step = dict(timeline.get("active_step") or {})
    active_status = str(active_step.get("status") or "")
    if active_status == "sending":
        started_at = float(active_step.get("send_started_at", 0) or active_step.get("sent_at", 0) or 0)
        ack_timeout = int(effective_config.get("ack_timeout_sec", TIANXING_TIMELINE_ACK_TIMEOUT_SEC) or TIANXING_TIMELINE_ACK_TIMEOUT_SEC)
        if started_at <= 0 or now < started_at + ack_timeout:
            return {"phase": "sending", "changed": False, "reason": "天星战略动作正在发送队列中，等待返回或真实回复。"}
        active_step["status"] = "ack_timeout"
        active_step["timeout_at"] = float(now)
        active_step["calibration_due_at"] = float(now + int(effective_config.get("calibration_backoff_sec", TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC) or TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC))
        timeline["phase"] = "ack_timeout"
        timeline["blocked_until"] = active_step["calibration_due_at"]
        timeline["last_error"] = "天星战略动作发送队列超时，等待查盘校准；不重复发送。"
        timeline["updated_at"] = float(now)
        _set_timeline_step(timeline, _timeline_active_index(timeline), active_step)
        _timeline_audit(timeline, now, "send_queue_timeout", action=active_step.get("action"), arg=active_step.get("arg"))
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": "ack_timeout", "changed": True, "reason": timeline["last_error"]}
    if active_status == "sent_waiting_ack":
        ack_due_at = float(active_step.get("ack_due_at", 0) or 0)
        if ack_due_at <= 0 or now < ack_due_at:
            return {"phase": "sent_waiting_ack", "changed": False, "reason": "等待天星战略动作真实回复。"}
        active_step["status"] = "ack_timeout"
        active_step["timeout_at"] = float(now)
        active_step["calibration_due_at"] = float(now + int(effective_config.get("calibration_backoff_sec", TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC) or TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC))
        timeline["phase"] = "ack_timeout"
        timeline["blocked_until"] = active_step["calibration_due_at"]
        timeline["last_error"] = "天星战略动作回复超时，等待查盘校准；不放行下游。"
        timeline["updated_at"] = float(now)
        _set_timeline_step(timeline, _timeline_active_index(timeline), active_step)
        _timeline_audit(timeline, now, "ack_timeout", action=active_step.get("action"), arg=active_step.get("arg"))
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": "ack_timeout", "changed": True, "reason": timeline["last_error"]}

    if active_status == "ack_timeout" and float(active_step.get("calibration_due_at", 0) or 0) <= now:
        timeline = _schedule_tianxing_timeline_calibration(timeline, now)
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": "calibrating", "changed": True, "reason": "已安排 .天机盘 校准。"}

    if (timeline.get("phase") == "state_confirmed" or active_status == "confirmed") and active_step.get("terminal_after_confirm"):
        timeline["phase"] = "blocked_replan"
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["blocked_until"] = float(now)
        timeline["last_error"] = "校准已完成，原战略动作未被确认；需重算时间线，不放行下游。"
        timeline["updated_at"] = float(now)
        _timeline_audit(timeline, now, "blocked_replan", reason=timeline["last_error"])
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": "blocked_replan", "changed": True, "reason": timeline["last_error"]}

    if timeline.get("phase") in {"state_confirmed", "downstream_released"} or active_status in {"confirmed", "released"}:
        next_index = _timeline_active_index(timeline) + 1
        _activate_timeline_step(timeline, next_index, now)
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": timeline.get("phase"), "changed": True, "reason": "时间线推进到下一步。"}

    if active_status == "pending":
        timeline = await _send_tianxing_timeline_step(timeline, active_step, now, effective_config)
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": timeline.get("phase"), "changed": True, "reason": timeline.get("last_error") or "时间线步骤已处理。"}

    plan = build_tianxing_timeline_plan(now=now, horizon_hours=horizon_hours, windows=windows or [], config=effective_config)
    timeline = _build_tianxing_timeline_state_from_plan(plan, now, effective_config)
    state["tianxing_timeline_state"] = timeline
    save_state()
    if timeline.get("phase") != "waiting_send":
        return {"phase": timeline.get("phase"), "changed": True, "reason": timeline.get("last_error") or timeline.get("reason") or "时间线计划已记录。"}

    active_step = dict(timeline.get("active_step") or {})
    timeline = await _send_tianxing_timeline_step(timeline, active_step, now, effective_config)
    state["tianxing_timeline_state"] = timeline
    save_state()
    return {"phase": timeline.get("phase"), "changed": True, "reason": timeline.get("last_error") or "时间线步骤已处理。"}


async def run_tianxing_timeline_scheduler(now, *, windows=None, config=None, horizon_hours=8):
    async with _timeline_lock():
        return await _run_tianxing_timeline_scheduler_unlocked(now, windows=windows, config=config, horizon_hours=horizon_hours)


def _route_preflight_result(route, stage, route_allowed, reason="", prepare_plan=None, deadline_at=0, now=0, blocked_until=0, timeline_required=False):
    prepare_plan = prepare_plan if isinstance(prepare_plan, dict) else {}
    return {
        "route": route,
        "stage": stage,
        "route_allowed": bool(route_allowed),
        "reason": str(reason or ""),
        "prepare_plan": prepare_plan,
        "prepare_command": str(prepare_plan.get("command") or ""),
        "prepare_action": str(prepare_plan.get("action") or ""),
        "prepare_arg": str(prepare_plan.get("arg") or ""),
        "deadline_at": float(deadline_at or 0),
        "planned_at": float(now or 0),
        "blocked_until": float(blocked_until or 0),
        "source_module": "天星宗",
        "lab_only": True,
        "timeline_required": bool(timeline_required),
    }


def _route_preflight_prepare(route, stage, plan, now, deadline_at, reason=""):
    if deadline_at and float(deadline_at) <= float(now):
        return _route_preflight_result(
            route,
            "deadline_expired",
            True,
            reason or "路线动作已到截止时间，本轮不插入天星预检。",
            plan,
            deadline_at,
            now,
        )
    return _route_preflight_result(
        route,
        stage,
        False,
        reason or plan.get("reason") or "需要先执行天星宗预检动作。",
        plan,
        deadline_at,
        now,
    )


def build_tianxing_route_preflight_plan(route, *, reason="", deadline_at=0, now=None, config=None):
    now = float(now if now is not None else time.time())
    route = _normalize_route_choice(route, "")
    deadline_at = float(deadline_at or 0)
    if route not in TIANXING_ROUTES:
        return _route_preflight_result("", "unsupported_route", True, "天星路线必须是：闭关、炼制、探索、斗法。", deadline_at=deadline_at, now=now)
    if not state.get("tianxing_enabled"):
        return _route_preflight_result(route, "disabled", True, "天星宗模块未开启，路线动作不等待天星预检。", deadline_at=deadline_at, now=now)
    if not is_module_available("天星宗"):
        return _route_preflight_result(route, "unavailable", True, "当前身份不是天星宗，路线动作不等待天星预检。", deadline_at=deadline_at, now=now)

    dirty_fields = _dirty_tianxing_time_fields(state.get("tianxing_observation"))
    if dirty_fields:
        return _route_preflight_result(
            route,
            "dirty_state",
            True,
            f"天星宗状态字段异常（{_format_list(dirty_fields)}），本轮不插入天星预检。",
            deadline_at=deadline_at,
            now=now,
        )

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    effective_config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    route_reason = str(reason or route).strip() or route

    current_prediction = str(observed.get("current_prediction") or "").strip()
    prediction_until = float(observed.get("current_prediction_until", 0) or 0)
    if current_prediction and current_prediction != route:
        if prediction_until > now:
            if effective_config.get("allow_prediction_override_enabled"):
                pass
            else:
                return _route_preflight_result(
                    route,
                    "prediction_conflict",
                    False,
                    f"已有 {current_prediction} 推命尚未应验，为避免逆命，本轮不发送{route_reason}。",
                    deadline_at=deadline_at,
                    now=now,
                    blocked_until=prediction_until,
                )
        elif not effective_config.get("allow_prediction_override_enabled"):
            return _route_preflight_result(
                route,
                "prediction_conflict",
                False,
                f"已有 {current_prediction} 推命尚未应验，为避免逆命，本轮不发送{route_reason}。",
                deadline_at=deadline_at,
                now=now,
                blocked_until=prediction_until,
            )
        else:
            return _route_preflight_result(
                route,
                "prediction_conflict_unknown",
                False,
                f"已有 {current_prediction} 推命尚未应验，但时间不可解析，为避免逆命，本轮不发送{route_reason}。",
                deadline_at=deadline_at,
                now=now,
            )

    if not effective_config.get("timeline_enabled"):
        return _route_preflight_result(route, "timeline_disabled", True, "天星时间线未开启，路线动作不等待天星预检。", deadline_at=deadline_at, now=now)

    if is_tianxing_route_released(route, now=now):
        return _route_preflight_result(route, "timeline_released", True, f"{route_reason} 已获天星时间线确认放行。", deadline_at=deadline_at, now=now)

    return _route_preflight_result(route, "timeline_waiting", False, f"{route_reason} 需等待天星时间线确认放行。", deadline_at=deadline_at, now=now, timeline_required=True)


def _retreat_farm_audit(farm, now, event, **extra):
    entry = {"ts": float(now or 0), "event": str(event or "")}
    for key, value in extra.items():
        if value is not None:
            entry[key] = value
    audit = list((farm or {}).get("audit") or [])
    audit.append(entry)
    farm["audit"] = audit[-20:]


def _craft_farm_audit(farm, now, event, **extra):
    entry = {"ts": float(now or 0), "event": str(event or "")}
    for key, value in extra.items():
        if value is not None:
            entry[key] = value
    audit = list((farm or {}).get("audit") or [])
    audit.append(entry)
    farm["audit"] = audit[-20:]


def _set_tianxing_retreat_farm_state(farm, now):
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    farm = normalize_tianxing_retreat_farm_state(farm)
    farm["updated_at"] = float(now or 0)
    timeline["retreat_farm"] = farm
    timeline["updated_at"] = float(now or 0)
    state["tianxing_timeline_state"] = timeline
    return farm


def _set_tianxing_craft_farm_state(farm, now):
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    farm = normalize_tianxing_craft_farm_state(farm)
    farm["updated_at"] = float(now or 0)
    timeline["craft_farm"] = farm
    timeline["updated_at"] = float(now or 0)
    state["tianxing_timeline_state"] = timeline
    return farm


def _current_retreat_farm_state():
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    return normalize_tianxing_retreat_farm_state(timeline.get("retreat_farm"))


def _current_craft_farm_state():
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    return normalize_tianxing_craft_farm_state(timeline.get("craft_farm"))


def _heqi_exchange_command(config):
    config = normalize_tianxing_auto_config(config)
    count = int(config.get("retreat_farm_heqi_exchange_count", 10) or 10)
    return f"{CMD_EXCHANGE_HEQI_DAN_PREFIX}{count}"


def _lingshi_donation_command(config):
    config = normalize_tianxing_auto_config(config)
    count = int(config.get("retreat_farm_donate_lingshi_count", 200) or 200)
    return f"{CMD_SECT_DONATE_LINGSHI_PREFIX}{count}"


def _is_tianxing_retreat_chain_command(command):
    command = str(command or "").strip()
    return (
        command in {CMD_DEEP_RETREAT_FORCE_EXIT, CMD_USE_HEQI_DAN}
        or command.startswith(CMD_EXCHANGE_HEQI_DAN_PREFIX)
        or command.startswith(CMD_SECT_DONATE_LINGSHI_PREFIX)
    )


def _retreat_farm_cooldown_until(farm, now):
    farm = normalize_tianxing_retreat_farm_state(farm)
    cooldown_until = float(farm.get("cooldown_until", 0) or 0)
    if cooldown_until > float(now or 0):
        return cooldown_until
    next_time = float(farm.get("next_time", 0) or 0)
    if str(farm.get("phase") or "") == "cooldown" and next_time > float(now or 0):
        return next_time
    return 0.0


def _update_craft_farm_from_parsed(parsed, observed, now, family=""):
    parsed = parsed if isinstance(parsed, dict) else {}
    farm = _current_craft_farm_state()
    family = str(family or "")
    active = bool(farm.get("started_at")) or family == "tianxing_craft_farm"
    if not active:
        return False

    action = str(parsed.get("action") or "")
    result = str(parsed.get("result") or "")
    changed = False
    config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
    target = int(farm.get("target_tianji", 0) or config.get("target_tianji_daily", 0) or 0)
    daily_limit = int(farm.get("daily_limit", 0) or config.get("craft_farm_daily_limit", 0) or 0)

    if action == "天机盘":
        current = int(observed.get("tianji_value", 0) or 0)
        farm["estimated_tianji"] = current
        farm["target_tianji"] = target
        farm["daily_limit"] = daily_limit
        if target > 0 and current >= target:
            farm["phase"] = "complete"
            farm["handoff_ready"] = True
            farm["next_time"] = 0
            farm["last_result"] = f"天机值 {current} 已达到目标 {target}"
            farm["last_error"] = ""
            _craft_farm_audit(farm, now, "calibration_complete", tianji=current, target=target)
        else:
            farm["phase"] = "ready"
            farm["handoff_ready"] = False
            farm["next_time"] = float(now)
            farm["last_result"] = f"天机值 {current} 未达到目标 {target}" if target else "天机盘已校准"
            farm["last_error"] = ""
            _craft_farm_audit(farm, now, "calibration_ready", tianji=current, target=target)
        changed = True
    elif action == "炼制":
        farm["target_tianji"] = target
        farm["daily_limit"] = daily_limit
        item = str(parsed.get("craft_item") or farm.get("last_item") or config.get("craft_farm_item") or "玄铁剑").strip()
        if item:
            farm["last_item"] = item
        if result == "preparing":
            farm["phase"] = "crafting_waiting_final"
            farm["next_time"] = float(now + int(config.get("craft_farm_reply_timeout_sec", TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC) or TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC))
            farm["last_result"] = parsed.get("summary") or "炼制开始，等待结算"
            farm["last_error"] = ""
            _craft_farm_audit(farm, now, "craft_preparing", item=item)
            changed = True
        elif result == "blocked":
            farm["phase"] = "blocked"
            farm["next_time"] = float(now + _status_backoff_sec(config))
            farm["last_error"] = parsed.get("last_error") or "炼制受阻"
            farm["last_result"] = ""
            _craft_farm_audit(farm, now, "craft_blocked", item=item, reason=farm["last_error"])
            changed = True
        elif result in {"success", "failure", "prediction_hit", "prediction_miss", "change_triggered"}:
            count = int(parsed.get("craft_count", 0) or 0) or 1
            success_count = int(parsed.get("craft_success_count", 0) or 0)
            gain = int(parsed.get("last_tianji_gain", 0) or 0)
            phase_before = str(farm.get("phase") or "")
            can_account = phase_before in {"sent_waiting_reply", "crafting_waiting_final"}
            has_prediction_settlement = result in {"prediction_hit", "prediction_miss", "change_triggered"}
            if can_account and has_prediction_settlement:
                farm["daily_count"] = int(farm.get("daily_count", 0) or 0) + count
                farm["success_count"] = int(farm.get("success_count", 0) or 0) + success_count
                if result == "prediction_hit":
                    farm["hit_count"] = int(farm.get("hit_count", 0) or 0) + 1
                elif result == "prediction_miss":
                    farm["miss_count"] = int(farm.get("miss_count", 0) or 0) + 1
                if gain > 0:
                    farm["last_tianji_gain"] = gain
                    observed["tianji_value"] = int(observed.get("tianji_value", 0) or 0) + gain
                    state["tianxing_observation"] = observed
            farm["estimated_tianji"] = int(observed.get("tianji_value", 0) or 0)
            farm["last_result"] = parsed.get("summary") or result
            farm["last_error"] = ""
            if result in {"success", "failure"} and can_account:
                farm["phase"] = "calibrating"
                farm["handoff_ready"] = False
                farm["next_time"] = float(now + TIANXING_CRAFT_FARM_CALIBRATION_DELAY_SEC)
                farm["last_error"] = "炼制结算未见推命命中/落空，等待查盘校准。"
            elif target > 0 and int(observed.get("tianji_value", 0) or 0) >= target:
                farm["phase"] = "complete"
                farm["handoff_ready"] = True
                farm["next_time"] = 0
            elif daily_limit > 0 and int(farm.get("daily_count", 0) or 0) >= daily_limit:
                farm["phase"] = "daily_limit_reached"
                farm["handoff_ready"] = True
                farm["next_time"] = 0
            else:
                farm["phase"] = "ready"
                farm["handoff_ready"] = False
                farm["next_time"] = float(now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC))
            _craft_farm_audit(
                farm,
                now,
                "craft_result",
                item=item,
                result=result,
                count=count,
                success=success_count,
                gain=gain,
                accounted=bool(can_account and has_prediction_settlement),
            )
            changed = True

    if changed:
        _set_tianxing_craft_farm_state(farm, now)
    return changed


def note_tianxing_retreat_force_exit_summary(text, now=None):
    now = float(now if now is not None else time.time())
    raw_text = str(text or "")
    if "强行出关" not in raw_text or "【闭关修炼】" not in raw_text or not has_wait_time(raw_text):
        return False

    farm = _current_retreat_farm_state()
    active = bool(farm.get("started_at")) and (
        str(farm.get("last_command") or "").strip() == CMD_DEEP_RETREAT_FORCE_EXIT
        or str(farm.get("last_action") or "").strip() == "force_exit"
        or str(farm.get("phase") or "").strip() == "sent_waiting_reply"
    )
    if not active:
        return False

    wait_sec = parse_wait_time(raw_text)
    if wait_sec <= 0:
        return False

    next_time = float(now + wait_sec + CD_BUFFER_SEC)
    farm["phase"] = "cooldown"
    farm["next_time"] = next_time
    farm["cooldown_until"] = next_time
    farm["last_action"] = "force_exit_summary"
    farm["last_command"] = CMD_DEEP_RETREAT_FORCE_EXIT
    farm["last_error"] = ""
    farm["last_result"] = "强行出关后普通闭关调息中"
    farm["handoff_ready"] = False
    _retreat_farm_audit(farm, now, "force_exit_cooldown", next_time=next_time, wait_sec=wait_sec)
    _set_tianxing_retreat_farm_state(farm, now)
    return True


def _retreat_farm_result(stage, *, active=False, takeover=False, handoff=True, reason="", action="", command="", next_time=0, timeline_required=False, dry_run=False):
    return {
        "stage": str(stage or ""),
        "active": bool(active),
        "takeover": bool(takeover),
        "handoff": bool(handoff),
        "reason": str(reason or ""),
        "action": str(action or ""),
        "command": str(command or ""),
        "next_time": float(next_time or 0),
        "timeline_required": bool(timeline_required),
        "dry_run": bool(dry_run),
    }


def build_tianxing_retreat_farm_plan(*, now=None, deep_retreat_phase="", config=None):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    if not state.get("tianxing_enabled"):
        return _retreat_farm_result("disabled", reason="天星宗模块未开启。")
    if not is_module_available("天星宗"):
        return _retreat_farm_result("unavailable", reason="当前身份不是天星宗。")
    if not config.get("retreat_farm_enabled"):
        return _retreat_farm_result("disabled", reason="普通闭关攒点未开启。")
    if _normalize_route_choice(config.get("farm_route"), "闭关") != "闭关":
        return _retreat_farm_result("route_not_retreat", reason="当前 Farm 路线不是闭关。")

    windows = build_tianxing_farm_window(now=now, config=config, reason="天星普通闭关攒点")
    if not windows:
        return _retreat_farm_result("outside_window", reason="当前不在闭关 Farm 窗口。")

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    target_tianji = int(config.get("target_tianji_daily", 0) or 0)
    current_tianji = int(observed.get("tianji_value", 0) or 0)
    if target_tianji <= 0:
        return _retreat_farm_result("target_disabled", active=True, reason="日目标天机为 0，不主动攒点。")
    if current_tianji >= target_tianji:
        return _retreat_farm_result("target_reached", active=True, reason=f"天机值 {current_tianji} 已达到目标 {target_tianji}。")
    current_prediction = _normalize_route_choice(observed.get("current_prediction"), "")

    farm = _current_retreat_farm_state()
    next_time = float(farm.get("next_time", 0) or 0)
    dry_run = bool(config.get("retreat_farm_dry_run_enabled"))
    farm_phase = str(farm.get("phase") or "").strip()
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    if timeline.get("phase") == "prediction_conflict" and float(timeline.get("blocked_until", 0) or 0) > now and current_prediction != "闭关":
        return _retreat_farm_result(
            "waiting_prediction_conflict",
            active=True,
            takeover=False,
            handoff=True,
            reason=timeline.get("last_error") or "已有异路推命尚未应验，普通闭关攒点等待。",
            next_time=float(timeline.get("blocked_until", 0) or 0),
            dry_run=dry_run,
        )
    if farm_phase in {"prediction_conflict", "timeline_waiting"} and next_time > now:
        return _retreat_farm_result(
            "waiting_prediction_conflict" if farm_phase == "prediction_conflict" else "waiting_timeline",
            active=True,
            takeover=False,
            handoff=True,
            reason=farm.get("last_error") or "普通闭关攒点等待天星时间线确认。",
            next_time=next_time,
            dry_run=dry_run,
        )
    if farm_phase in {"calibrating", "sent_waiting_reply"}:
        if next_time > now:
            stage = "waiting_calibration" if farm_phase == "calibrating" else "waiting_retreat_reply"
            reason = "普通闭关攒点后等待查盘校准。" if farm_phase == "calibrating" else "普通闭关攒点命令已发送，等待真实回复。"
            return _retreat_farm_result(
                stage,
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason=reason,
                next_time=next_time,
                dry_run=dry_run,
            )
        return _retreat_farm_result(
            "calibrate_panel",
            active=True,
            takeover=not dry_run,
            handoff=dry_run,
            reason="普通闭关攒点后需要查盘校准天机值。",
            action="panel",
            command=CMD_TIANXING_PANEL,
            next_time=now + TIANXING_RETREAT_FARM_RETRY_SEC,
            dry_run=dry_run,
        )

    if farm_phase in {"need_heqi_exchange", "ready_to_use_heqi", "need_lingshi_donation"}:
        if next_time > now:
            return _retreat_farm_result(
                "waiting_chain_step",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason=farm.get("last_error") or "普通闭关攒点链路等待下一步。",
                next_time=next_time,
                dry_run=dry_run,
            )
        cooldown_until = _retreat_farm_cooldown_until(farm, now)
        if farm_phase == "need_heqi_exchange":
            if not config.get("retreat_farm_allow_heqi_dan"):
                return _retreat_farm_result(
                    "waiting_retreat_cd",
                    active=True,
                    takeover=not dry_run,
                    handoff=dry_run,
                    reason="缺少合气丹，但未授权使用合气丹；等待普通闭关冷却。",
                    next_time=cooldown_until or now + TIANXING_RETREAT_FARM_RETRY_SEC,
                    dry_run=dry_run,
                )
            if not config.get("retreat_farm_auto_exchange_heqi_dan"):
                return _retreat_farm_result(
                    "waiting_retreat_cd",
                    active=True,
                    takeover=not dry_run,
                    handoff=dry_run,
                    reason="缺少合气丹，自动兑换未开启；等待普通闭关冷却。",
                    next_time=cooldown_until or now + TIANXING_RETREAT_FARM_RETRY_SEC,
                    dry_run=dry_run,
                )
            return _retreat_farm_result(
                "exchange_heqi_dan",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason="缺少合气丹，按配置自动兑换。",
                action="exchange_heqi_dan",
                command=_heqi_exchange_command(config),
                next_time=cooldown_until or now + TIANXING_RETREAT_FARM_RETRY_SEC,
                dry_run=dry_run,
            )
        if farm_phase == "ready_to_use_heqi":
            if not config.get("retreat_farm_allow_heqi_dan"):
                return _retreat_farm_result(
                    "waiting_retreat_cd",
                    active=True,
                    takeover=not dry_run,
                    handoff=dry_run,
                    reason="合气丹已兑换，但未授权服用；等待普通闭关冷却。",
                    next_time=cooldown_until or now + TIANXING_RETREAT_FARM_RETRY_SEC,
                    dry_run=dry_run,
                )
            return _retreat_farm_result(
                "use_heqi_dan",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason="合气丹已兑换，继续服用以解除普通闭关冷却。",
                action="use_heqi_dan",
                command=CMD_USE_HEQI_DAN,
                next_time=cooldown_until or now + TIANXING_RETREAT_FARM_RETRY_SEC,
                dry_run=dry_run,
            )
        if not config.get("retreat_farm_auto_donate_lingshi"):
            return _retreat_farm_result(
                "waiting_retreat_cd",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason="兑换合气丹贡献不足，自动捐献灵石未开启；等待普通闭关冷却。",
                next_time=cooldown_until or now + TIANXING_RETREAT_FARM_RETRY_SEC,
                dry_run=dry_run,
            )
        return _retreat_farm_result(
            "donate_lingshi",
            active=True,
            takeover=not dry_run,
            handoff=dry_run,
            reason="兑换合气丹贡献不足，按配置捐献灵石后重试兑换。",
            action="donate_lingshi",
            command=_lingshi_donation_command(config),
            next_time=cooldown_until or now + TIANXING_RETREAT_FARM_RETRY_SEC,
            dry_run=dry_run,
        )

    if next_time > now:
        if config.get("retreat_farm_allow_heqi_dan") and farm.get("phase") == "cooldown":
            return _retreat_farm_result(
                "use_heqi_dan",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason="普通闭关仍在调息，配置允许服用合气丹加速。",
                action="use_heqi_dan",
                command=CMD_USE_HEQI_DAN,
                next_time=next_time,
                dry_run=dry_run,
            )
        return _retreat_farm_result(
            "waiting_retreat_cd",
            active=True,
            takeover=not dry_run,
            handoff=dry_run,
            reason="普通闭关调息中，等待下一轮攒点。",
            next_time=next_time,
            dry_run=dry_run,
        )

    route_config = dict(config)
    route_config["timeline_enabled"] = bool(config.get("timeline_enabled"))
    preflight = build_tianxing_route_preflight_plan("闭关", reason="天星普通闭关攒点", now=now, config=route_config)
    if not preflight.get("route_allowed"):
        if preflight.get("timeline_required"):
            return _retreat_farm_result(
                "timeline_required",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason=preflight.get("reason") or "等待天星时间线确认闭关路线。",
                action="timeline",
                next_time=now + TIANXING_RETREAT_FARM_RETRY_SEC,
                timeline_required=True,
                dry_run=dry_run,
            )
        return _retreat_farm_result(
            preflight.get("stage") or "preflight_blocked",
            active=True,
            takeover=False,
            handoff=True,
            reason=preflight.get("reason") or "天星预检阻断普通闭关攒点。",
            next_time=preflight.get("blocked_until") or now + TIANXING_RETREAT_FARM_RETRY_SEC,
            dry_run=dry_run,
        )

    deep_phase = str(deep_retreat_phase or "").strip()
    if deep_phase in {"launching", "queued_launch", "running", "summary_due", "observing_summary", "waiting_summary"}:
        if config.get("retreat_farm_allow_force_exit"):
            return _retreat_farm_result(
                "force_exit_deep_retreat",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason="深度闭关占用普通闭关链，配置允许强行出关后攒天机。",
                action="force_exit",
                command=CMD_DEEP_RETREAT_FORCE_EXIT,
                next_time=now + TIANXING_RETREAT_FARM_RETRY_SEC,
                dry_run=dry_run,
            )
        return _retreat_farm_result(
            "deep_retreat_busy",
            active=True,
            takeover=False,
            handoff=True,
            reason="深度闭关正在占用普通闭关链，未允许强行出关。",
            next_time=now + TIANXING_RETREAT_FARM_RETRY_SEC,
            dry_run=dry_run,
        )

    return _retreat_farm_result(
        "send_normal_retreat",
        active=True,
        takeover=not dry_run,
        handoff=dry_run,
        reason="闭关路线已确认，发送普通闭关修炼获取天机点。",
        action="normal_retreat",
        command=CMD_NORMAL_RETREAT,
        next_time=now + TIANXING_RETREAT_FARM_RETRY_SEC,
        dry_run=dry_run,
    )


async def run_tianxing_retreat_farm_scheduler(now, *, deep_retreat_phase="", config=None):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    plan = build_tianxing_retreat_farm_plan(now=now, deep_retreat_phase=deep_retreat_phase, config=config)
    farm = _current_retreat_farm_state()
    if not plan.get("active"):
        return plan

    if not farm.get("started_at"):
        observed = normalize_tianxing_observation(state.get("tianxing_observation"))
        farm["started_at"] = float(now)
        farm["start_tianji"] = int(observed.get("tianji_value", 0) or 0)
    farm["target_tianji"] = int(config.get("target_tianji_daily", 0) or 0)
    farm["last_action"] = plan.get("action") or plan.get("stage") or ""
    farm["last_command"] = plan.get("command") or ""
    farm["last_error"] = plan.get("reason") or ""
    farm["next_time"] = float(plan.get("next_time", 0) or 0)
    if farm["last_action"] in {"use_heqi_dan", "exchange_heqi_dan", "donate_lingshi"} and farm["next_time"] > now:
        farm["cooldown_until"] = max(float(farm.get("cooldown_until", 0) or 0), farm["next_time"])
    farm["handoff_ready"] = bool(plan.get("handoff"))

    if plan.get("stage") == "target_reached":
        farm["phase"] = "complete"
        farm["last_result"] = plan.get("reason") or ""
        _retreat_farm_audit(farm, now, "target_reached", reason=plan.get("reason"))
        _set_tianxing_retreat_farm_state(farm, now)
        save_state()
        return plan

    if plan.get("dry_run"):
        farm["phase"] = "dry_run"
        farm["dry_run_plan"] = dict(plan)
        _retreat_farm_audit(farm, now, "dry_run", stage=plan.get("stage"), command=plan.get("command"), reason=plan.get("reason"))
        _set_tianxing_retreat_farm_state(farm, now)
        save_state()
        return plan

    if plan.get("stage") in {"waiting_prediction_conflict", "waiting_timeline"}:
        farm["phase"] = "prediction_conflict" if plan.get("stage") == "waiting_prediction_conflict" else "timeline_waiting"
        farm["last_result"] = plan.get("stage") or ""
        _retreat_farm_audit(farm, now, farm["phase"], reason=plan.get("reason"))
        _set_tianxing_retreat_farm_state(farm, now)
        save_state()
        return plan

    if plan.get("timeline_required"):
        timeline_result = await run_tianxing_timeline_scheduler(
            now,
            windows=build_tianxing_farm_window(now=now, config=config, reason="天星普通闭关攒点"),
            config=config,
        )
        current_timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
        if current_timeline.get("phase") == "prediction_conflict" and float(current_timeline.get("blocked_until", 0) or 0) > now:
            farm = current_timeline["retreat_farm"]
            farm["phase"] = "prediction_conflict"
            farm["last_result"] = str(timeline_result.get("phase") or "")
            farm["last_error"] = current_timeline.get("last_error") or timeline_result.get("reason") or ""
            farm["next_time"] = float(current_timeline.get("blocked_until", 0) or 0)
            _retreat_farm_audit(farm, now, "prediction_conflict", reason=farm["last_error"], blocked_until=farm["next_time"])
            _set_tianxing_retreat_farm_state(farm, now)
            save_state()
            return dict(plan, timeline_phase=timeline_result.get("phase") or "", timeline_reason=timeline_result.get("reason") or "")
        farm["phase"] = "timeline_waiting"
        farm["last_result"] = str(timeline_result.get("phase") or "")
        farm["next_time"] = float(now + TIANXING_RETREAT_FARM_RETRY_SEC)
        _retreat_farm_audit(farm, now, "timeline_waiting", phase=timeline_result.get("phase"), reason=timeline_result.get("reason"))
        _set_tianxing_retreat_farm_state(farm, now)
        save_state()
        return dict(plan, timeline_phase=timeline_result.get("phase") or "", timeline_reason=timeline_result.get("reason") or "")

    command = str(plan.get("command") or "")
    if not command:
        farm["phase"] = "waiting"
        _retreat_farm_audit(farm, now, "waiting", stage=plan.get("stage"), reason=plan.get("reason"))
        _set_tianxing_retreat_farm_state(farm, now)
        save_state()
        return plan

    source_module = "深度闭关" if command == CMD_DEEP_RETREAT_FORCE_EXIT else "天星宗"
    priority = "chain" if _is_tianxing_retreat_chain_command(command) else "normal"
    msg = await send_game_command(
        command,
        track=True,
        max_retry=0,
        priority=priority,
        source_module=source_module,
        op_id=f"tianxing-retreat-farm-{plan.get('action')}-{int(now)}",
    )
    if not msg:
        farm["phase"] = "send_blocked"
        farm["next_time"] = float(now + TIANXING_RETREAT_FARM_RETRY_SEC)
        farm["last_error"] = f"{command} 发送失败或被安全策略拦截。"
        _retreat_farm_audit(farm, now, "send_blocked", command=command)
        _set_tianxing_retreat_farm_state(farm, now)
        save_state()
        return dict(plan, stage="send_blocked", reason=farm["last_error"], next_time=farm["next_time"])

    sent_at = float(getattr(msg, "sent_at", 0) or now)
    farm["phase"] = "sent_waiting_reply"
    farm["last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    farm["next_time"] = float(sent_at + TIANXING_RETREAT_FARM_REPLY_TIMEOUT_SEC)
    farm["last_error"] = ""
    _retreat_farm_audit(farm, sent_at, "sent_waiting_reply", command=command, msg_id=farm["last_msg_id"])
    _set_tianxing_retreat_farm_state(farm, sent_at)
    save_state()
    return dict(plan, stage="sent_waiting_reply", msg_id=farm["last_msg_id"], next_time=farm["next_time"])


def _craft_farm_result(stage, *, active=False, takeover=False, handoff=True, reason="", action="", command="", next_time=0, timeline_required=False, dry_run=False):
    return {
        "stage": str(stage or ""),
        "active": bool(active),
        "takeover": bool(takeover),
        "handoff": bool(handoff),
        "reason": str(reason or ""),
        "action": str(action or ""),
        "command": str(command or ""),
        "next_time": float(next_time or 0),
        "timeline_required": bool(timeline_required),
        "dry_run": bool(dry_run),
    }


def _craft_farm_command(config):
    item = str((config or {}).get("craft_farm_item") or "玄铁剑").strip() or "玄铁剑"
    return f"{CMD_CRAFT} {item}", item


def _state_float(key):
    try:
        return float(state.get(key, 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _state_int(key):
    try:
        return int(state.get(key, 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _craft_farm_explore_consume_block(now, config):
    now = float(now or 0)
    lead_sec = int((config or {}).get("route_prepare_lead_sec", 5 * 60) or 5 * 60)
    interval_sec = int((config or {}).get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC)
    candidates = []
    module_specs = (
        ("野外历练", "wild_training_enabled", "next_wild_training_time", ("wild_training_reply_to_msg_id",), "wild_training_reply_due_at"),
        (
            "探寻裂缝",
            "explore_rift_enabled",
            "next_explore_rift_time",
            ("explore_rift_reply_to_msg_id", "explore_rift_pending_result_msg_id", "explore_rift_fatal_msg_id"),
            "explore_rift_reply_due_at",
        ),
    )
    for label, enabled_key, next_key, pending_keys, due_key in module_specs:
        if not state.get(enabled_key):
            continue
        pending = any(_state_int(key) > 0 for key in pending_keys)
        pending_due = _state_float(due_key)
        if pending:
            block_until = max(now + interval_sec, pending_due if pending_due > now else now + TIANXING_TIME_BUFFER_SEC)
            candidates.append((block_until, f"{label}探索消费正在等待回复，炼制攒点让路。"))
            continue
        due_at = _state_float(next_key)
        if due_at <= 0:
            continue
        if due_at <= now + lead_sec and due_at >= now - TIANXING_TIME_BUFFER_SEC:
            block_until = max(now + interval_sec, due_at + TIANXING_TIME_BUFFER_SEC)
            candidates.append((block_until, f"{label}探索消费窗口临近（{fmt_abs_ts(due_at)}），炼制攒点让路。"))
    if not candidates:
        return {}
    block_until, reason = sorted(candidates, key=lambda item: item[0])[0]
    return {"blocked_until": float(block_until), "reason": reason}


def _build_conflict_consume_retreat_config(config, now):
    config = normalize_tianxing_auto_config(config)
    local_time = time.localtime(float(now or time.time()))
    consume_config = dict(config)
    consume_config.update({
        "farm_route": "闭关",
        "farm_window_enabled": True,
        "farm_window_start": f"{local_time.tm_hour:02d}:{local_time.tm_min:02d}",
        "farm_window_duration_min": max(5, int(config.get("farm_window_duration_min", 60) or 60)),
        "retreat_farm_enabled": True,
        "retreat_farm_dry_run_enabled": bool(config.get("craft_farm_dry_run_enabled")),
        "allow_prediction_override_enabled": False,
    })
    return normalize_tianxing_auto_config(consume_config)


def build_tianxing_craft_farm_plan(*, now=None, config=None):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    if not state.get("tianxing_enabled"):
        return _craft_farm_result("disabled", reason="天星宗模块未开启。")
    if not is_module_available("天星宗"):
        return _craft_farm_result("unavailable", reason="当前身份不是天星宗。")
    if not config.get("craft_farm_enabled"):
        return _craft_farm_result("disabled", reason="炼制攒点未开启。")
    if _normalize_route_choice(config.get("farm_route"), "炼制") != "炼制":
        return _craft_farm_result("route_not_craft", reason="当前 Farm 路线不是炼制。")

    windows = build_tianxing_farm_window(now=now, config=config, reason="天星炼制攒点")
    if not windows:
        return _craft_farm_result("outside_window", reason="当前不在炼制 Farm 窗口。")

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    target_tianji = int(config.get("target_tianji_daily", 0) or 0)
    current_tianji = int(observed.get("tianji_value", 0) or 0)
    farm = _current_craft_farm_state()
    estimated_tianji = max(current_tianji, int(farm.get("estimated_tianji", 0) or 0))
    daily_limit = int(config.get("craft_farm_daily_limit", 0) or 0)
    if target_tianji <= 0:
        return _craft_farm_result("target_disabled", active=True, reason="日目标天机为 0，不主动炼制攒点。")
    if estimated_tianji >= target_tianji:
        return _craft_farm_result("target_reached", active=True, reason=f"天机值 {estimated_tianji} 已达到目标 {target_tianji}。")
    if daily_limit > 0 and int(farm.get("daily_count", 0) or 0) >= daily_limit:
        return _craft_farm_result("daily_limit_reached", active=True, reason=f"炼制攒点今日已达 {daily_limit} 轮。")

    explore_block = _craft_farm_explore_consume_block(now, config)
    if explore_block:
        return _craft_farm_result(
            "waiting_consume_window",
            active=True,
            takeover=False,
            handoff=True,
            reason=explore_block.get("reason") or "探索消费窗口临近，炼制攒点让路。",
            next_time=explore_block.get("blocked_until") or now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC),
        )

    next_time = float(farm.get("next_time", 0) or 0)
    dry_run = bool(config.get("craft_farm_dry_run_enabled"))
    farm_phase = str(farm.get("phase") or "").strip()
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    if timeline.get("phase") == "prediction_conflict" and float(timeline.get("blocked_until", 0) or 0) > now:
        current_prediction = _normalize_route_choice(observed.get("current_prediction"), "")
        prediction_until = float(observed.get("current_prediction_until", 0) or 0)
        if current_prediction and prediction_until > now:
            if config.get("consume_conflicting_prediction_enabled") and current_prediction == "闭关":
                return _craft_farm_result(
                    "consume_conflicting_prediction",
                    active=True,
                    takeover=not dry_run,
                    handoff=dry_run,
                    reason="已有闭关推命未应验；先按闭关路线消费该推命，再回到炼制攒点。",
                    action="consume_prediction",
                    next_time=now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC),
                    dry_run=dry_run,
                )
            return _craft_farm_result(
                "waiting_prediction_conflict",
                active=True,
                takeover=False,
                handoff=True,
                reason=timeline.get("last_error") or "已有异路推命尚未应验，炼制攒点等待。",
                next_time=float(timeline.get("blocked_until", 0) or 0),
                dry_run=dry_run,
            )
    if farm_phase in {"prediction_conflict", "timeline_waiting"} and next_time > now:
        return _craft_farm_result(
            "waiting_prediction_conflict" if farm_phase == "prediction_conflict" else "waiting_timeline",
            active=True,
            takeover=False,
            handoff=True,
            reason=farm.get("last_error") or "炼制攒点等待天星时间线确认。",
            next_time=next_time,
            dry_run=dry_run,
        )
    if farm_phase in {"sent_waiting_reply", "crafting_waiting_final", "calibrating"}:
        if next_time > now:
            return _craft_farm_result(
                "waiting_reply" if farm_phase != "calibrating" else "waiting_calibration",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason="炼制攒点等待真实回复或查盘校准。",
                next_time=next_time,
                dry_run=dry_run,
            )
        return _craft_farm_result(
            "calibrate_panel",
            active=True,
            takeover=not dry_run,
            handoff=dry_run,
            reason="炼制攒点回复超时，先查盘校准；不重复炼制。",
            action="panel",
            command=CMD_TIANXING_PANEL,
            next_time=now + int(config.get("craft_farm_reply_timeout_sec", TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC) or TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC),
            dry_run=dry_run,
        )

    route_config = dict(config)
    route_config["timeline_enabled"] = bool(config.get("timeline_enabled"))
    preflight = build_tianxing_route_preflight_plan("炼制", reason="天星炼制攒点", now=now, config=route_config)
    if not preflight.get("route_allowed"):
        if preflight.get("timeline_required"):
            return _craft_farm_result(
                "timeline_required",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason=preflight.get("reason") or "等待天星时间线确认炼制路线。",
                action="timeline",
                next_time=now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC),
                timeline_required=True,
                dry_run=dry_run,
            )
        return _craft_farm_result(
            preflight.get("stage") or "preflight_blocked",
            active=True,
            takeover=False,
            handoff=True,
            reason=preflight.get("reason") or "天星预检阻断炼制攒点。",
            next_time=preflight.get("blocked_until") or now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC),
            dry_run=dry_run,
        )

    command, item = _craft_farm_command(config)
    return _craft_farm_result(
        "send_craft",
        active=True,
        takeover=not dry_run,
        handoff=dry_run,
        reason=f"炼制路线已确认，发送炼制 {item} 获取天机点。",
        action="craft",
        command=command,
        next_time=now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC),
        dry_run=dry_run,
    )


async def run_tianxing_craft_farm_scheduler(now, *, config=None):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    plan = build_tianxing_craft_farm_plan(now=now, config=config)
    farm = _current_craft_farm_state()
    if not plan.get("active"):
        return plan

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if not farm.get("started_at"):
        farm["started_at"] = float(now)
        farm["start_tianji"] = int(observed.get("tianji_value", 0) or 0)
        farm["estimated_tianji"] = int(observed.get("tianji_value", 0) or 0)
    farm["target_tianji"] = int(config.get("target_tianji_daily", 0) or 0)
    farm["daily_limit"] = int(config.get("craft_farm_daily_limit", 0) or 0)
    farm["last_action"] = plan.get("action") or plan.get("stage") or ""
    farm["last_command"] = plan.get("command") or ""
    farm["last_error"] = plan.get("reason") or ""
    farm["next_time"] = float(plan.get("next_time", 0) or 0)
    farm["handoff_ready"] = bool(plan.get("handoff"))

    if plan.get("stage") in {"target_reached", "daily_limit_reached"}:
        farm["phase"] = "complete" if plan.get("stage") == "target_reached" else "daily_limit_reached"
        farm["last_result"] = plan.get("reason") or ""
        _craft_farm_audit(farm, now, plan.get("stage"), reason=plan.get("reason"))
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return plan

    if plan.get("dry_run"):
        farm["phase"] = "dry_run"
        farm["dry_run_plan"] = dict(plan)
        _craft_farm_audit(farm, now, "dry_run", stage=plan.get("stage"), command=plan.get("command"), reason=plan.get("reason"))
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return plan

    if plan.get("stage") == "consume_conflicting_prediction":
        consume_config = _build_conflict_consume_retreat_config(config, now)
        consume_result = await run_tianxing_retreat_farm_scheduler(
            now,
            deep_retreat_phase=state.get("deep_retreat_phase") or "",
            config=consume_config,
        )
        farm = _current_craft_farm_state()
        farm["phase"] = "consume_prediction"
        farm["last_result"] = str(consume_result.get("stage") or "")
        farm["last_error"] = consume_result.get("reason") or ""
        farm["next_time"] = float(consume_result.get("next_time", 0) or now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC))
        _craft_farm_audit(
            farm,
            now,
            "consume_conflicting_prediction",
            stage=consume_result.get("stage"),
            command=consume_result.get("command"),
            reason=consume_result.get("reason"),
        )
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return dict(
            plan,
            consume_stage=consume_result.get("stage") or "",
            consume_command=consume_result.get("command") or "",
            consume_reason=consume_result.get("reason") or "",
            next_time=farm["next_time"],
        )

    if plan.get("stage") in {"waiting_prediction_conflict", "waiting_timeline"}:
        farm["phase"] = "prediction_conflict" if plan.get("stage") == "waiting_prediction_conflict" else "timeline_waiting"
        farm["last_result"] = plan.get("stage") or ""
        _craft_farm_audit(farm, now, farm["phase"], reason=plan.get("reason"))
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return plan

    if plan.get("timeline_required"):
        timeline_result = await run_tianxing_timeline_scheduler(
            now,
            windows=build_tianxing_farm_window(now=now, config=config, reason="天星炼制攒点"),
            config=config,
        )
        current_timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
        if current_timeline.get("phase") == "prediction_conflict" and float(current_timeline.get("blocked_until", 0) or 0) > now:
            farm = current_timeline["craft_farm"]
            farm["phase"] = "prediction_conflict"
            farm["last_result"] = str(timeline_result.get("phase") or "")
            farm["last_error"] = current_timeline.get("last_error") or timeline_result.get("reason") or ""
            farm["next_time"] = float(current_timeline.get("blocked_until", 0) or 0)
            _craft_farm_audit(farm, now, "prediction_conflict", reason=farm["last_error"], blocked_until=farm["next_time"])
            _set_tianxing_craft_farm_state(farm, now)
            save_state()
            return dict(plan, timeline_phase=timeline_result.get("phase") or "", timeline_reason=timeline_result.get("reason") or "")
        farm["phase"] = "timeline_waiting"
        farm["last_result"] = str(timeline_result.get("phase") or "")
        farm["next_time"] = float(now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC))
        _craft_farm_audit(farm, now, "timeline_waiting", phase=timeline_result.get("phase"), reason=timeline_result.get("reason"))
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return dict(plan, timeline_phase=timeline_result.get("phase") or "", timeline_reason=timeline_result.get("reason") or "")

    if plan.get("stage") in {"waiting_reply", "waiting_calibration"}:
        previous_phase = str(farm.get("phase") or "").strip()
        if plan.get("stage") == "waiting_calibration":
            farm["phase"] = "calibrating"
        elif previous_phase in {"sent_waiting_reply", "crafting_waiting_final"}:
            farm["phase"] = previous_phase
        else:
            farm["phase"] = "sent_waiting_reply"
        farm["last_result"] = plan.get("stage") or ""
        _craft_farm_audit(farm, now, "waiting", stage=plan.get("stage"), reason=plan.get("reason"))
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return plan

    command = str(plan.get("command") or "")
    if not command:
        farm["phase"] = "waiting"
        _craft_farm_audit(farm, now, "waiting", stage=plan.get("stage"), reason=plan.get("reason"))
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return plan

    msg = await send_game_command(
        command,
        track=True,
        max_retry=0,
        priority="normal",
        source_module="天星宗",
        op_id=f"tianxing-craft-farm-{plan.get('action')}-{int(now)}",
    )
    if not msg:
        farm["phase"] = "send_blocked"
        farm["next_time"] = float(now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC))
        farm["last_error"] = f"{command} 发送失败或被安全策略拦截。"
        _craft_farm_audit(farm, now, "send_blocked", command=command)
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return dict(plan, stage="send_blocked", reason=farm["last_error"], next_time=farm["next_time"])

    sent_at = float(getattr(msg, "sent_at", 0) or now)
    if command == CMD_TIANXING_PANEL:
        farm["phase"] = "calibrating"
    else:
        _command, item = _craft_farm_command(config)
        farm["phase"] = "sent_waiting_reply"
        farm["last_item"] = item
    farm["last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    farm["next_time"] = float(sent_at + int(config.get("craft_farm_reply_timeout_sec", TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC) or TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC))
    farm["last_error"] = ""
    _craft_farm_audit(farm, sent_at, "sent_waiting_reply", command=command, msg_id=farm["last_msg_id"])
    _set_tianxing_craft_farm_state(farm, sent_at)
    save_state()
    return dict(plan, stage="sent_waiting_reply", msg_id=farm["last_msg_id"], next_time=farm["next_time"])


def _record_tianxing_dry_run(observed, now, plan, config):
    action = str(plan.get("action") or "")
    command = str(plan.get("command") or "")
    observed["auto_last_action"] = f"dry_run_{action}" if action else "dry_run"
    observed["auto_last_error"] = "dry-run：战略动作仅记录，不发送。"
    observed["auto_last_plan"] = command or plan.get("reason") or ""
    observed["auto_last_plan_at"] = float(now)
    observed["auto_next_time"] = float(now + _status_backoff_sec(config))
    state["tianxing_observation"] = observed
    save_state()


async def _run_tianxing_scheduler_unlocked(now):
    now = float(now if now is not None else time.time())
    if not state.get("tianxing_enabled"):
        return
    if not is_module_available("天星宗"):
        state["tianxing_enabled"] = False
        state["tianxing_observation"] = {}
        save_state()
        return

    dirty_fields = _dirty_tianxing_time_fields(state.get("tianxing_observation"))
    if dirty_fields:
        return

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
    if _handle_tianxing_auto_pending(observed, now):
        return
    auto_next_time = float(observed.get("auto_next_time", 0) or 0)
    if auto_next_time > 0 and now < auto_next_time:
        if not _should_wake_tianxing_timeline(observed, config, now):
            return

    calamity_count = int(observed.get("calamity_count", 0) or 0)
    calamity_threshold = int(config.get("min_calamity_to_clear", 1) or 1)
    if not _has_recent_observation(observed, now) and config.get("auto_panel_enabled"):
        plan = build_tianxing_manual_plan("panel", now=now)
    elif calamity_count >= calamity_threshold:
        if config.get("auto_clear_calamity_enabled"):
            plan = build_tianxing_manual_plan("clear_calamity", now=now)
        else:
            _set_tianxing_auto_wait(
                observed,
                now,
                "idle",
                now + _status_backoff_sec(config),
                f"逆命劫 {calamity_count} 已达阈值 {calamity_threshold}，自动消劫关闭，暂停战略动作。",
            )
            return
    elif (
        not observed.get("fixed_star")
        and str(observed.get("available_stars_source") or "").strip() != "observe"
        and config.get("auto_observe_enabled")
    ):
        plan = build_tianxing_manual_plan("observe", now=now)
    else:
        craft_result = await run_tianxing_craft_farm_scheduler(now, config=config)
        if craft_result.get("active"):
            observed = normalize_tianxing_observation(state.get("tianxing_observation"))
            observed["auto_last_action"] = "craft_farm"
            observed["auto_last_error"] = craft_result.get("reason") or ""
            observed["auto_last_plan"] = craft_result.get("stage") or ""
            observed["auto_last_plan_at"] = float(now)
            observed["auto_next_time"] = float(craft_result.get("next_time", 0) or now + int(config.get("craft_farm_interval_sec", TIANXING_CRAFT_FARM_RETRY_SEC) or TIANXING_CRAFT_FARM_RETRY_SEC))
            state["tianxing_observation"] = observed
            save_state()
            return
        plan = _build_tianxing_strategy_plan(observed, config, now)
        if not plan.get("allowed") and str(plan.get("action") or "") == "idle":
            _set_tianxing_auto_wait(observed, now, "idle", now + _status_backoff_sec(config), plan.get("reason") or "")
            return

    action = str(plan.get("action") or "")
    if not plan.get("allowed"):
        _set_tianxing_auto_wait(
            observed,
            now,
            action,
            now + TIANXING_AUTO_BLOCK_BACKOFF_SEC,
            plan.get("reason") or "天星宗自动调度未满足条件",
        )
        return

    if action in {"set_star", "predict", "change_fate"} and config.get("strategy_dry_run_enabled"):
        _record_tianxing_dry_run(observed, now, plan, config)
        return

    _note_tianxing_auto_pending(observed, now, plan, config)
    state["tianxing_observation"] = observed
    save_state()

    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=0,
        priority="normal",
        source_module="天星宗",
        op_id=f"tianxing-auto-{action}-{int(now)}",
    )
    sent_at = now
    if msg:
        parsed_sent_at, sent_at_dirty = _parse_observation_float(getattr(msg, "sent_at", 0))
        sent_at = now if sent_at_dirty or parsed_sent_at <= 0 else parsed_sent_at
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if not msg:
        _clear_tianxing_auto_pending(observed)
        _set_tianxing_auto_wait(
            observed,
            now,
            action,
            sent_at + TIANXING_AUTO_SEND_FAIL_BACKOFF_SEC,
            "天星宗自动调度发送失败或被安全策略拦截",
        )
        return

    observed["auto_pending_msg_id"] = int(getattr(msg, "id", 0) or 0)
    observed["auto_pending_sent_at"] = float(sent_at)
    observed["auto_pending_due_at"] = float(sent_at + int(config.get("ack_timeout_sec", TIANXING_TIMELINE_ACK_TIMEOUT_SEC) or TIANXING_TIMELINE_ACK_TIMEOUT_SEC))
    observed["auto_last_action"] = action
    observed["auto_last_error"] = ""
    observed["auto_last_plan"] = plan.get("command") or ""
    observed["auto_last_plan_at"] = float(sent_at)
    observed["auto_next_time"] = observed["auto_pending_due_at"]
    state["tianxing_observation"] = observed
    save_state()


async def run_tianxing_scheduler(now):
    async with _auto_lock():
        return await _run_tianxing_scheduler_unlocked(now)


async def execute_tianxing_manual_action(action="panel", arg="", *, send_as_id=None, now=None):
    now = float(now if now is not None else time.time())
    if send_as_id is not None:
        with use_identity(send_as_id):
            plan = build_tianxing_manual_plan(action, arg, now=now)
    else:
        plan = build_tianxing_manual_plan(action, arg, now=now)
    if not plan.get("allowed"):
        return False, plan.get("reason") or "天星宗动作未允许", plan
    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=int(plan.get("max_retry", 0) or 0),
        send_as_id=send_as_id,
        priority="normal",
        source_module=plan.get("source_module") or "天星宗",
        op_id=plan.get("op_id") or "",
        delete_policy=plan.get("delete_policy") or "manual_keep",
    )
    if not msg:
        return False, "发送被运行时安全策略拦截或账号不可用。", plan
    return True, f"已发送：{plan['command']}（msg_id={int(getattr(msg, 'id', 0) or 0)}）", plan


def _format_list(values):
    return "、".join(str(item) for item in values if str(item or "").strip()) or "未记录"


def get_tianxing_status_text():
    if not state.get("tianxing_enabled"):
        return "\n".join([
            "🌌 天星宗",
            "- 模块：关闭（不会主动发送）",
            "- 运行快照：关闭时不展示旧观察记录",
        ])
    dirty_fields = _dirty_tianxing_time_fields(state.get("tianxing_observation"))
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
    lines = [
        "🌌 天星宗",
        f"- 模块：{'开启' if state.get('tianxing_enabled') else '关闭'}（被动观察，手动动作受控发送）",
        "- 命令：.观命｜.定命 <紫微|天府|太阴|贪狼>｜.推命/.改命 <闭关|炼制|探索|斗法>｜.天机盘｜.消劫",
        f"- 命星：可选 {_format_list(observed.get('available_stars'))}｜已定 {observed.get('fixed_star') or '未记录'}",
        f"- 推命：{observed.get('current_prediction') or '无'}｜{fmt_abs_ts(observed.get('current_prediction_until', 0))}（{fmt_remaining(observed.get('current_prediction_until', 0))}）",
        f"- 改命：{observed.get('current_change') or '无'}｜{fmt_abs_ts(observed.get('current_change_until', 0))}（{fmt_remaining(observed.get('current_change_until', 0))}）",
        f"- 天机/逆命劫：{observed.get('tianji_value', 0)} / {observed.get('calamity_count', 0)}",
        f"- 命中/落空/改命：{observed.get('hit_count', 0)} / {observed.get('miss_count', 0)} / {observed.get('change_count', 0)}",
        f"- 最近动作：{observed.get('last_action') or '未记录'} / {observed.get('last_result') or '未记录'}",
        f"- 最近观察：{fmt_abs_ts(observed.get('last_observed_at', 0))}",
        f"- 自动调度：{fmt_abs_ts(observed.get('auto_next_time', 0))}（{fmt_remaining(observed.get('auto_next_time', 0))}）",
        f"- 自动策略：查盘{'开' if config.get('auto_panel_enabled') else '关'}｜观命{'开' if config.get('auto_observe_enabled') else '关'}｜消劫{'开' if config.get('auto_clear_calamity_enabled') else '关'}｜定命{'开' if config.get('auto_set_star_enabled') else '关'}｜推命{'开' if config.get('auto_predict_enabled') else '关'}｜改命{'开' if config.get('auto_change_fate_enabled') else '关'}｜dry-run{'开' if config.get('strategy_dry_run_enabled') else '关'}",
        f"- 策略优先级：命星 {_format_list(config.get('star_priority'))}｜路线 {_format_list(config.get('route_priority'))}",
    ]
    if dirty_fields:
        lines.append(f"- 状态异常：{_format_list(dirty_fields)} 不可解析，自动发送已暂停")
    if observed.get("last_star_effect"):
        lines.append(f"- 命盘照命：{observed.get('last_star_effect')}")
    if observed.get("last_tianji_gain") or observed.get("last_contrib_gain"):
        lines.append(f"- 最近收益：天机+{observed.get('last_tianji_gain', 0)}｜贡献+{observed.get('last_contrib_gain', 0)}")
    if observed.get("last_bonus_gain"):
        lines.append(f"- 闭关加成：修为+{observed.get('last_bonus_gain')}")
    if observed.get("last_error"):
        lines.append(f"- 异常：{observed.get('last_error')}")
    if observed.get("auto_last_error"):
        lines.append(f"- 自动异常：{observed.get('auto_last_error')}")
    if observed.get("auto_last_plan"):
        lines.append(f"- 最近自动计划：{observed.get('auto_last_plan')}｜{fmt_abs_ts(observed.get('auto_last_plan_at', 0))}")
    recent = observed.get("recent") or []
    if recent:
        lines.append("- 最近事件：")
        for item in recent[-3:]:
            lines.append(f"  {fmt_abs_ts(item.get('ts', 0))} {item.get('action') or '-'} {item.get('result') or '-'}")
    return "\n".join(lines)


__all__ = [
    "apply_tianxing_passive",
    "build_tianxing_manual_plan",
    "build_tianxing_route_preflight_plan",
    "build_tianxing_farm_window",
    "build_tianxing_consume_window",
    "build_tianxing_craft_farm_plan",
    "build_tianxing_retreat_farm_plan",
    "build_tianxing_timeline_plan",
    "execute_tianxing_manual_action",
    "get_tianxing_status_text",
    "is_tianxing_route_released",
    "looks_like_tianxing_text",
    "note_tianxing_retreat_force_exit_summary",
    "normalize_tianxing_auto_config",
    "normalize_tianxing_observation",
    "normalize_tianxing_timeline_state",
    "parse_tianxing_text",
    "run_tianxing_retreat_farm_scheduler",
    "run_tianxing_craft_farm_scheduler",
    "run_tianxing_scheduler",
    "run_tianxing_timeline_scheduler",
    "set_tianxing_auto_config",
]
