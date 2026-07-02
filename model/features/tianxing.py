import asyncio
import copy
import math
import random
import re
import time
from datetime import datetime

from ..config import (
    CD_BUFFER_SEC,
    CMD_DEEP_RETREAT_FORCE_EXIT,
    CMD_CRAFT,
    CMD_DUEL,
    CMD_EXPLORE_RIFT,
    CMD_NORMAL_RETREAT,
    CMD_TIANXING_CHANGE_FATE,
    CMD_TIANXING_CLEAR_CALAMITY,
    CMD_TIANXING_OBSERVE,
    CMD_TIANXING_PANEL,
    CMD_TIANXING_PREDICT,
    CMD_TIANXING_SET_STAR,
    CMD_USE_HEQI_DAN,
    CMD_WILD_TRAINING,
    CMD_EXCHANGE_HEQI_DAN_PREFIX,
    CMD_SECT_DONATE_LINGSHI_PREFIX,
    TZ_LOCAL,
)
from ..persistence import save_state
from ..runtime import console_log, get_last_game_send_block, register_game_command_pre_send_guard, send_game_command
from ..state import get_current_identity_id, is_module_available, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key, has_wait_time, parse_wait_time
from ._phaseful import get_phaseful_summary_risk_reason


TIANXING_PREDICTION_SEC = 8 * 3600
TIANXING_CHANGE_FATE_SEC = 24 * 3600
TIANXING_TIME_BUFFER_SEC = 60
TIANXING_OBSERVATION_STALE_SEC = 24 * 3600
TIANXING_AUTO_STATUS_BACKOFF_SEC = 6 * 3600
TIANXING_AUTO_BLOCK_BACKOFF_SEC = 60 * 60
TIANXING_AUTO_SEND_FAIL_BACKOFF_SEC = 30 * 60
TIANXING_DAILY_BOOTSTRAP_RETRY_SEC = 2 * 60
TIANXING_DAILY_STAR_CORRECTION_WINDOW_SEC = 6 * 3600
TIANXING_TIMELINE_ACK_TIMEOUT_SEC = 90
TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC = 5 * 60
TIANXING_TIMELINE_LEGACY_SEND_TIMEOUT_SEC = 35
TIANXING_TIMELINE_SEND_TIMEOUT_SEC = 75
TIANXING_RETREAT_FARM_REPLY_TIMEOUT_SEC = 90
TIANXING_RETREAT_FARM_RETRY_SEC = 5 * 60
TIANXING_RETREAT_FARM_CALIBRATION_DELAY_SEC = 60
TIANXING_RETREAT_FARM_DEFAULT_RETREAT_CD_SEC = 15 * 60
TIANXING_CRAFT_FARM_LEGACY_REPLY_TIMEOUT_SEC = 120
TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC = 75
TIANXING_CRAFT_FARM_RETRY_SEC = 20
TIANXING_CRAFT_FARM_CALIBRATION_DELAY_SEC = 60
TIANXING_FARM_WINDOWS_DEFAULT_TEXT = "02:00-05:00,06:00-11:50,14:30-17:30,23:00-23:35"
TIANXING_CRAFT_FARM_LEGACY_INTERVAL_MIN_SEC = 3 * 60
TIANXING_CRAFT_FARM_LEGACY_INTERVAL_MAX_SEC = 7 * 60
TIANXING_CRAFT_FARM_INTERVAL_MIN_SEC = 2 * 60
TIANXING_CRAFT_FARM_INTERVAL_MAX_SEC = 5 * 60
TIANXING_CRAFT_FARM_OFF_WINDOW_INTERVAL_MIN_SEC = 30 * 60
TIANXING_CRAFT_FARM_OFF_WINDOW_INTERVAL_MAX_SEC = 60 * 60
TIANXING_ROUTE_LEASE_GUARD_MAX_AGE_SEC = 30 * 60
TIANXING_PHASEFUL_DEFER_MIN_SEC = 60
TIANXING_PHASEFUL_DEFER_MAX_SEC = 180
TIANXING_STARS = ("紫微", "天府", "太阴", "贪狼")
TIANXING_ROUTES = ("闭关", "炼制", "探索", "斗法")
TIANXING_AUTO_CHANGE_FATE_ROUTES = ("探索",)
TIANXING_ROUTE_AUTO = "auto"
TIANXING_ROUTE_RESULT_MARKERS = (
    "命盘【",
    "【推命命中】",
    "【推命落空】",
    "【改命待发】",
    "【改命回天】",
    "【天星偏转】",
)

RE_BRACKET = re.compile(r"【([^】]+)】")
RE_STAR_EFFECT = re.compile(r"命盘【(?P<star>[^】]+)】照命(?P<desc>[^。\n]*)")
RE_SET_STAR = re.compile(r"你将今日命轨定在\s*【(?P<star>[^】]+)】")
RE_PREDICT = re.compile(r"为\s*【(?P<route>[^】]+)】\s*推下了?一段命数")
RE_CHANGE_FATE = re.compile(r"为\s*【(?P<route>[^】]+)】\s*预留了?一次改命回天")
RE_EXISTING_ROUTE = re.compile(r"你已有一道关于\s*【(?P<route>[^】]+)】\s*的")
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
    "current_prediction_set_at",
    "prediction_consumed_at",
    "current_change_until",
    "auto_next_time",
    "auto_last_error_at",
    "auto_pending_sent_at",
    "auto_pending_due_at",
    "automation_paused_until",
    "automation_paused_at",
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


def looks_like_tianxing_route_result(text):
    raw_text = str(text or "")
    return any(marker in raw_text for marker in TIANXING_ROUTE_RESULT_MARKERS)


def _infer_route_from_modifier_text(text):
    raw_text = str(text or "")
    if any(
        marker in raw_text
        for marker in (
            "【野外历练",
            "此为 NPC 历练",
            "此为玩家对 NPC 历练",
            "空间裂缝",
            "【探寻成功】",
            "【激战得胜】",
            "【遭遇风暴】",
            "【不敌败退】",
            "元婴满载而归",
            "元婴在无尽的虚空",
        )
    ):
        return "探索"
    return ""


def _default_tianxing_observation():
    return {
        "last_observed_at": 0,
        "last_action": "",
        "last_result": "",
        "last_summary": "",
        "last_error": "",
        "available_stars": [],
        "available_stars_day": "",
        "fixed_star": "",
        "fixed_star_day": "",
        "current_prediction": "",
        "current_prediction_until": 0,
        "current_prediction_set_at": 0,
        "prediction_consumed_route": "",
        "prediction_consumed_at": 0,
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
        "auto_last_error_at": 0,
        "auto_last_plan": "",
        "auto_last_plan_at": 0,
        "auto_pending_action": "",
        "auto_pending_command": "",
        "auto_pending_msg_id": 0,
        "auto_pending_sent_at": 0,
        "auto_pending_due_at": 0,
        "automation_paused_until": 0,
        "automation_paused_at": 0,
        "automation_paused_reason": "",
        "recent": [],
    }


def _default_tianxing_auto_config():
    return {
        "auto_panel_enabled": True,
        "auto_observe_enabled": True,
        "auto_clear_calamity_enabled": True,
        "auto_set_star_enabled": False,
        "daily_observe_enabled": True,
        "daily_set_star_enabled": True,
        "auto_predict_enabled": False,
        "auto_change_fate_enabled": False,
        "route_special_star_enabled": False,
        "strategy_dry_run_enabled": True,
        "timeline_enabled": False,
        "timeline_dry_run_enabled": True,
        "star_priority": ["太阴", "贪狼", "天府", "紫微"],
        "predict_route": TIANXING_ROUTE_AUTO,
        "change_route": TIANXING_ROUTE_AUTO,
        "route_priority": ["探索", "闭关", "炼制", "斗法"],
        "change_route_priority": ["探索"],
        "farm_route": "闭关",
        "farm_window_enabled": True,
        "farm_windows_text": TIANXING_FARM_WINDOWS_DEFAULT_TEXT,
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
        "craft_farm_interval_min_sec": TIANXING_CRAFT_FARM_INTERVAL_MIN_SEC,
        "craft_farm_interval_max_sec": TIANXING_CRAFT_FARM_INTERVAL_MAX_SEC,
        "craft_farm_off_window_enabled": True,
        "craft_farm_off_window_interval_min_sec": TIANXING_CRAFT_FARM_OFF_WINDOW_INTERVAL_MIN_SEC,
        "craft_farm_off_window_interval_max_sec": TIANXING_CRAFT_FARM_OFF_WINDOW_INTERVAL_MAX_SEC,
        "craft_farm_reply_timeout_sec": TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC,
        "duel_route_enabled": False,
        "allow_prediction_override_enabled": False,
        "consume_conflicting_prediction_enabled": False,
        "deep_retreat_consume_enabled": False,
        "route_prepare_lead_sec": 5 * 60,
        "target_tianji_daily": 42,
        "min_tianji_for_change": 6,
        "min_calamity_to_clear": 1,
        "status_backoff_hours": 6,
        "ack_timeout_sec": TIANXING_TIMELINE_ACK_TIMEOUT_SEC,
        "calibration_backoff_sec": TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC,
        "send_timeout_sec": TIANXING_TIMELINE_SEND_TIMEOUT_SEC,
        "max_replans_per_day": 3,
    }


_TIANXING_LEGACY_BAD_STAR_PRIORITY = ("天府", "贪狼", "太阴", "紫微")


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


def _parse_hhmm_strict(value):
    raw = str(value or "").strip()
    match = re.match(r"^(?P<hour>\d{1,2}):(?P<minute>\d{1,2})$", raw)
    if not match:
        return ""
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _hhmm_to_seconds(value):
    value = _normalize_hhmm(value, "00:00")
    hour, minute = value.split(":", 1)
    return int(hour) * 3600 + int(minute) * 60


def _seconds_to_hhmm(value):
    value = int(value or 0) % (24 * 3600)
    return f"{value // 3600:02d}:{(value % 3600) // 60:02d}"


def _legacy_farm_window_text(start, duration_min):
    start = _normalize_hhmm(start, "02:00")
    duration_sec = _coerce_int_range(duration_min, 60, 5, 8 * 60) * 60
    end_sec = (_hhmm_to_seconds(start) + duration_sec) % (24 * 3600)
    return f"{start}-{_seconds_to_hhmm(end_sec)}"


def _parse_tianxing_farm_window_specs(value):
    raw = str(value or "").strip()
    if not raw:
        return []
    specs = []
    for part in re.split(r"[,，、;；\s]+", raw):
        token = str(part or "").strip()
        if not token:
            continue
        match = re.match(
            r"^(?P<start>\d{1,2}:\d{1,2})\s*(?:-|~|～|至|到)\s*(?P<end>\d{1,2}:\d{1,2})$",
            token,
        )
        if not match:
            continue
        start = _parse_hhmm_strict(match.group("start"))
        end = _parse_hhmm_strict(match.group("end"))
        if not start or not end:
            continue
        start_sec = _hhmm_to_seconds(start)
        end_sec = _hhmm_to_seconds(end)
        duration_sec = (end_sec - start_sec) % (24 * 3600)
        if duration_sec <= 0:
            continue
        specs.append({
            "start": start,
            "end": end,
            "start_sec": start_sec,
            "duration_sec": duration_sec,
        })
    return specs


def _normalize_farm_windows_text(value, default_text=TIANXING_FARM_WINDOWS_DEFAULT_TEXT):
    specs = _parse_tianxing_farm_window_specs(value)
    if not specs:
        specs = _parse_tianxing_farm_window_specs(default_text)
    if not specs:
        specs = _parse_tianxing_farm_window_specs("02:00-05:00")
    return ",".join(f"{item['start']}-{item['end']}" for item in specs)


def _craft_interval_bounds(config):
    config = config or {}
    min_sec = _coerce_int_range(
        config.get("craft_farm_interval_min_sec"),
        TIANXING_CRAFT_FARM_INTERVAL_MIN_SEC,
        5,
        60 * 60,
    )
    max_sec = _coerce_int_range(
        config.get("craft_farm_interval_max_sec"),
        TIANXING_CRAFT_FARM_INTERVAL_MAX_SEC,
        5,
        60 * 60,
    )
    if max_sec < min_sec:
        max_sec = min_sec
    return int(min_sec), int(max_sec)


def _craft_off_window_interval_bounds(config):
    config = config or {}
    min_sec = _coerce_int_range(
        config.get("craft_farm_off_window_interval_min_sec"),
        TIANXING_CRAFT_FARM_OFF_WINDOW_INTERVAL_MIN_SEC,
        60,
        6 * 60 * 60,
    )
    max_sec = _coerce_int_range(
        config.get("craft_farm_off_window_interval_max_sec"),
        TIANXING_CRAFT_FARM_OFF_WINDOW_INTERVAL_MAX_SEC,
        60,
        6 * 60 * 60,
    )
    if max_sec < min_sec:
        max_sec = min_sec
    return int(min_sec), int(max_sec)


def _craft_farm_interval_sec(config, *, off_window=False):
    min_sec, max_sec = _craft_off_window_interval_bounds(config) if off_window else _craft_interval_bounds(config)
    if min_sec >= max_sec:
        return float(min_sec)
    return float(random.uniform(min_sec, max_sec))


def _current_craft_farm_interval_sec(config, now):
    _windows, off_window_active = _build_tianxing_craft_farm_windows(now, config, reason="天星炼制攒点间隔")
    return _craft_farm_interval_sec(config, off_window=off_window_active)


def normalize_tianxing_auto_config(value=None):
    default = _default_tianxing_auto_config()
    config = copy.deepcopy(default)
    raw_value = value if isinstance(value, dict) else {}
    if isinstance(value, dict):
        config.update(value)
    for key in (
        "auto_panel_enabled",
        "auto_observe_enabled",
        "auto_clear_calamity_enabled",
        "auto_set_star_enabled",
        "daily_observe_enabled",
        "daily_set_star_enabled",
        "auto_predict_enabled",
        "auto_change_fate_enabled",
        "route_special_star_enabled",
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
        "craft_farm_off_window_enabled",
        "duel_route_enabled",
        "allow_prediction_override_enabled",
        "consume_conflicting_prediction_enabled",
        "deep_retreat_consume_enabled",
    ):
        config[key] = _coerce_bool(config.get(key), default.get(key, False))
    config["star_priority"] = _normalize_choice_list(config.get("star_priority"), TIANXING_STARS, default["star_priority"])
    if tuple(config["star_priority"]) == _TIANXING_LEGACY_BAD_STAR_PRIORITY:
        config["star_priority"] = list(default["star_priority"])
    config["route_priority"] = _normalize_choice_list(config.get("route_priority"), TIANXING_ROUTES, default["route_priority"])
    config["change_route_priority"] = _normalize_choice_list(config.get("change_route_priority"), TIANXING_AUTO_CHANGE_FATE_ROUTES, default["change_route_priority"])
    config["predict_route"] = _normalize_route_choice(config.get("predict_route"), default["predict_route"])
    config["change_route"] = _normalize_route_choice(config.get("change_route"), default["change_route"])
    if config["change_route"] not in (TIANXING_ROUTE_AUTO, *TIANXING_AUTO_CHANGE_FATE_ROUTES):
        config["change_route"] = default["change_route"]
    config["farm_route"] = _normalize_route_choice(config.get("farm_route"), default["farm_route"])
    config["craft_farm_item"] = str(config.get("craft_farm_item") or default["craft_farm_item"]).strip() or default["craft_farm_item"]
    config["farm_window_start"] = _normalize_hhmm(config.get("farm_window_start"), default["farm_window_start"])
    config["farm_window_duration_min"] = _coerce_int_range(config.get("farm_window_duration_min"), default["farm_window_duration_min"], 5, 8 * 60)
    if str(raw_value.get("farm_windows_text") or "").strip():
        config["farm_windows_text"] = _normalize_farm_windows_text(raw_value.get("farm_windows_text"), default["farm_windows_text"])
    elif (
        "farm_window_start" in raw_value
        or "farm_window_duration_min" in raw_value
    ) and (
        config["farm_window_start"] != default["farm_window_start"]
        or int(config["farm_window_duration_min"]) != int(default["farm_window_duration_min"])
    ):
        config["farm_windows_text"] = _normalize_farm_windows_text(
            _legacy_farm_window_text(config["farm_window_start"], config["farm_window_duration_min"]),
            default["farm_windows_text"],
        )
    else:
        config["farm_windows_text"] = _normalize_farm_windows_text(config.get("farm_windows_text"), default["farm_windows_text"])
    config["retreat_farm_heqi_exchange_count"] = _coerce_int_range(config.get("retreat_farm_heqi_exchange_count"), default["retreat_farm_heqi_exchange_count"], 1, 999)
    config["retreat_farm_donate_lingshi_count"] = _coerce_int_range(config.get("retreat_farm_donate_lingshi_count"), default["retreat_farm_donate_lingshi_count"], 1, 99999)
    config["craft_farm_daily_limit"] = _coerce_int_range(config.get("craft_farm_daily_limit"), default["craft_farm_daily_limit"], 0, 999)
    legacy_interval = _coerce_int_range(config.get("craft_farm_interval_sec"), default["craft_farm_interval_sec"], 5, 60 * 60)
    if "craft_farm_interval_min_sec" in raw_value or "craft_farm_interval_max_sec" in raw_value:
        configured_min = _coerce_int_range(
            config.get("craft_farm_interval_min_sec"),
            default["craft_farm_interval_min_sec"],
            5,
            60 * 60,
        )
        configured_max = _coerce_int_range(
            config.get("craft_farm_interval_max_sec"),
            default["craft_farm_interval_max_sec"],
            5,
            60 * 60,
        )
        if (
            int(configured_min) == TIANXING_CRAFT_FARM_LEGACY_INTERVAL_MIN_SEC
            and int(configured_max) == TIANXING_CRAFT_FARM_LEGACY_INTERVAL_MAX_SEC
        ):
            config["craft_farm_interval_min_sec"] = default["craft_farm_interval_min_sec"]
            config["craft_farm_interval_max_sec"] = default["craft_farm_interval_max_sec"]
        else:
            config["craft_farm_interval_min_sec"] = configured_min
            config["craft_farm_interval_max_sec"] = configured_max
    elif "craft_farm_interval_sec" in raw_value and legacy_interval != TIANXING_CRAFT_FARM_RETRY_SEC:
        config["craft_farm_interval_min_sec"] = legacy_interval
        config["craft_farm_interval_max_sec"] = legacy_interval
    else:
        config["craft_farm_interval_min_sec"] = default["craft_farm_interval_min_sec"]
        config["craft_farm_interval_max_sec"] = default["craft_farm_interval_max_sec"]
    min_interval, max_interval = _craft_interval_bounds(config)
    config["craft_farm_interval_min_sec"] = min_interval
    config["craft_farm_interval_max_sec"] = max_interval
    config["craft_farm_interval_sec"] = min_interval if min_interval == max_interval else default["craft_farm_interval_sec"]
    off_min_interval = _coerce_int_range(
        config.get("craft_farm_off_window_interval_min_sec"),
        default["craft_farm_off_window_interval_min_sec"],
        60,
        6 * 60 * 60,
    )
    off_max_interval = _coerce_int_range(
        config.get("craft_farm_off_window_interval_max_sec"),
        default["craft_farm_off_window_interval_max_sec"],
        60,
        6 * 60 * 60,
    )
    if off_max_interval < off_min_interval:
        off_max_interval = off_min_interval
    config["craft_farm_off_window_interval_min_sec"] = int(off_min_interval)
    config["craft_farm_off_window_interval_max_sec"] = int(off_max_interval)
    config["craft_farm_reply_timeout_sec"] = _coerce_int_range(config.get("craft_farm_reply_timeout_sec"), default["craft_farm_reply_timeout_sec"], 30, 30 * 60)
    if int(config["craft_farm_reply_timeout_sec"]) == TIANXING_CRAFT_FARM_LEGACY_REPLY_TIMEOUT_SEC:
        config["craft_farm_reply_timeout_sec"] = default["craft_farm_reply_timeout_sec"]
    config["route_prepare_lead_sec"] = _coerce_int_range(config.get("route_prepare_lead_sec"), default["route_prepare_lead_sec"], 30, 60 * 60)
    config["target_tianji_daily"] = _coerce_int_range(config.get("target_tianji_daily"), default["target_tianji_daily"], 0, 999)
    config["min_tianji_for_change"] = _coerce_int_range(config.get("min_tianji_for_change"), default["min_tianji_for_change"], 3, 999)
    config["min_calamity_to_clear"] = _coerce_int_range(config.get("min_calamity_to_clear"), default["min_calamity_to_clear"], 1, 99)
    config["status_backoff_hours"] = _coerce_float_range(config.get("status_backoff_hours"), default["status_backoff_hours"], 1, 24)
    config["ack_timeout_sec"] = _coerce_int_range(config.get("ack_timeout_sec"), default["ack_timeout_sec"], 15, 15 * 60)
    config["calibration_backoff_sec"] = _coerce_int_range(config.get("calibration_backoff_sec"), default["calibration_backoff_sec"], 60, 60 * 60)
    config["send_timeout_sec"] = _coerce_int_range(config.get("send_timeout_sec"), default["send_timeout_sec"], 1, 5 * 60)
    config["max_replans_per_day"] = _coerce_int_range(config.get("max_replans_per_day"), default["max_replans_per_day"], 0, 99)
    return config


def _effective_tianxing_timeline_send_timeout(config):
    try:
        value = int((config or {}).get("send_timeout_sec", TIANXING_TIMELINE_SEND_TIMEOUT_SEC) or TIANXING_TIMELINE_SEND_TIMEOUT_SEC)
    except (TypeError, ValueError, OverflowError):
        value = TIANXING_TIMELINE_SEND_TIMEOUT_SEC
    if value == TIANXING_TIMELINE_LEGACY_SEND_TIMEOUT_SEC:
        return TIANXING_TIMELINE_SEND_TIMEOUT_SEC
    return max(1, value)


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
    current_prediction = _normalize_route_choice(observed.get("current_prediction"), "")
    consumed_route = _normalize_route_choice(observed.get("prediction_consumed_route"), "")
    if current_prediction and consumed_route == current_prediction:
        consumed_at = float(observed.get("prediction_consumed_at", 0) or 0)
        set_at = float(observed.get("current_prediction_set_at", 0) or 0)
        if consumed_at > 0 and (set_at <= 0 or consumed_at + 0.001 >= set_at):
            observed["current_prediction"] = ""
            observed["current_prediction_until"] = 0
    last_action = str(observed.get("last_action") or "").strip()
    last_result = str(observed.get("last_result") or "").strip()
    last_error = str(observed.get("last_error") or "").strip()
    if last_result == "cooldown" and (
        (last_action == "推命" and last_error == "推命尚未应验")
        or (last_action == "改命" and last_error == "改命尚未耗尽")
    ):
        observed["last_error"] = ""
        last_error = ""
    auto_last_error = str(observed.get("auto_last_error") or "").strip()
    if (
        last_result == "cooldown"
        and not str(observed.get("auto_pending_action") or "").strip()
        and not last_error
        and auto_last_error == "天星宗自动动作回复超时，暂缓重试；不继续推进下游。"
    ):
        observed["auto_last_error"] = ""
        observed["auto_last_error_at"] = 0
        auto_last_error = ""
    auto_error_at = float(observed.get("auto_last_error_at", 0) or 0)
    if not auto_last_error:
        observed["auto_last_error_at"] = 0
    elif (
        "发送失败或被安全策略拦截" in auto_last_error
        and not str(observed.get("auto_pending_action") or "").strip()
        and int(observed.get("auto_pending_msg_id", 0) or 0) <= 0
        and (
            (
                auto_error_at <= 0
                and last_result == "cooldown"
                and float(observed.get("current_prediction_until", 0) or 0) > 0
                and not last_error
            )
            or (
                auto_error_at > 0
                and float(observed.get("last_observed_at", 0) or 0) >= auto_error_at
            )
        )
    ):
        observed["auto_last_error"] = ""
        observed["auto_last_error_at"] = 0
    return observed


def _available_stars_for_day(observed, now):
    observed = observed if isinstance(observed, dict) else {}
    stars = [str(item).strip() for item in observed.get("available_stars") or [] if str(item or "").strip()]
    day_key = str(observed.get("available_stars_day") or "").strip()
    if day_key and day_key != get_day_key(now):
        return []
    return stars


def _fixed_star_day_matches(observed, now):
    observed = observed if isinstance(observed, dict) else {}
    fixed_star = str(observed.get("fixed_star") or "").strip()
    if not fixed_star:
        return False
    day_key = str(observed.get("fixed_star_day") or "").strip()
    return not day_key or day_key == get_day_key(now)


def _effective_fixed_star(observed, now):
    observed = observed if isinstance(observed, dict) else {}
    fixed_star = str(observed.get("fixed_star") or "").strip()
    if fixed_star and _fixed_star_day_matches(observed, now):
        return fixed_star
    return ""


def _choose_daily_star(available_stars, config):
    stars = [str(item).strip() for item in available_stars or [] if str(item or "").strip()]
    for star in normalize_tianxing_auto_config(config).get("star_priority") or []:
        if star in stars:
            return star
    return stars[0] if stars else ""


def _seconds_since_local_day_start(now):
    local_dt = datetime.fromtimestamp(float(now or 0), TZ_LOCAL)
    return local_dt.hour * 3600 + local_dt.minute * 60 + local_dt.second


def _in_daily_star_correction_window(now):
    elapsed = _seconds_since_local_day_start(now)
    return 0 <= elapsed <= TIANXING_DAILY_STAR_CORRECTION_WINDOW_SEC


def _should_correct_daily_fixed_star(observed, desired_star, now):
    observed = observed if isinstance(observed, dict) else {}
    desired_star = str(desired_star or "").strip()
    if not desired_star:
        return False
    if not _in_daily_star_correction_window(now):
        return False
    today_key = get_day_key(now)
    if str(observed.get("fixed_star_day") or "").strip() != today_key:
        return False
    fixed_star = _effective_fixed_star(observed, now)
    return bool(fixed_star and fixed_star != desired_star)


def _has_available_stars_today(observed, now):
    observed = observed if isinstance(observed, dict) else {}
    day_key = str(observed.get("available_stars_day") or "").strip()
    today_key = get_day_key(now)
    if day_key:
        return day_key == today_key
    if not observed.get("available_stars"):
        return False
    observed_at = float(observed.get("last_observed_at", 0) or 0)
    return observed_at > 0 and get_day_key(observed_at) == today_key


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
    if any(keyword in raw_text for keyword in ("【天星宗玩法帮助】", "【天星宗 · 司命推演】", "【天机盘】", "【观命结果】")):
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
    if "你并非天星宗弟子" in raw_text and "司命盘" in raw_text:
        return True
    if "成功拜入【天星宗】" in raw_text and "司命盘" in raw_text:
        return True
    if "你已是【天星宗】的弟子" in raw_text:
        return True
    if "你所属的宗门: 【天星宗】" in raw_text and "司命盘要诀" in raw_text:
        return True
    if "成功化去 1 层逆命劫" in raw_text:
        return True
    if "当前并无逆命劫缠身" in raw_text:
        return True
    if "你已有一道关于" in raw_text and ("推命尚未应验" in raw_text or "改命尚未耗尽" in raw_text):
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

    if "【天星宗玩法帮助】" in raw_text or "【天星宗 · 司命推演】" in raw_text:
        parsed.update(action="玩法帮助", result="guide", summary="天星宗玩法帮助")
        return parsed

    if "【天星宗】的观星长老" in raw_text:
        parsed.update(action="拜入天星宗", result="not_qualified", summary="资质不足，未能拜入天星宗", last_error="无法感应九天星辰之力")
        return parsed

    if "你并非天星宗弟子" in raw_text and "司命盘" in raw_text:
        parsed.update(
            action="天星身份",
            result="not_member",
            summary="并非天星宗弟子",
            last_error="非天星宗弟子，司命盘不会显化命轨",
        )
        return parsed

    if "成功拜入【天星宗】" in raw_text and "司命盘" in raw_text:
        parsed.update(action="拜入天星宗", result="success", summary="成功拜入天星宗")
        return parsed

    if "你已是【天星宗】的弟子" in raw_text:
        parsed.update(action="拜入天星宗", result="already_member", summary="已是天星宗弟子")
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
        route_match = RE_EXISTING_ROUTE.search(raw_text)
        route = route_match.group("route").strip() if route_match else (_stars_from_line(raw_text) or [""])[0]
        parsed.update(
            action="推命",
            result="cooldown",
            summary=f"推命尚未应验 {route}".strip(),
            last_route=route,
            current_prediction=route,
            current_prediction_until=_wait_until(raw_text, now),
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

    if "你已有一道关于" in raw_text and "改命尚未耗尽" in raw_text:
        route_match = RE_EXISTING_ROUTE.search(raw_text)
        route = route_match.group("route").strip() if route_match else (_stars_from_line(raw_text) or [""])[0]
        parsed.update(
            action="改命",
            result="cooldown",
            summary=f"改命尚未耗尽 {route}".strip(),
            last_route=route,
            current_change=route,
            current_change_until=_wait_until(raw_text, now),
        )
        return parsed

    if "成功化去 1 层逆命劫" in raw_text:
        parsed.update(action="消劫", result="success", summary="成功化去 1 层逆命劫")
        return parsed

    if "当前并无逆命劫缠身" in raw_text:
        parsed.update(action="消劫", result="noop", summary="当前并无逆命劫", calamity_count=0)
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
            if "【推命落空】" in raw_text:
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
        route = _infer_route_from_modifier_text(raw_text)
        if route:
            parsed["last_route"] = route
        tianji_gain_match = RE_TIANJI_GAIN.search(raw_text)
        contrib_gain_match = RE_CONTRIB_GAIN.search(raw_text)
        calamity_gain_match = RE_CALAMITY_GAIN.search(raw_text)
        if tianji_gain_match:
            parsed["last_tianji_gain"] = int(tianji_gain_match.group("gain") or 0)
        if contrib_gain_match:
            parsed["last_contrib_gain"] = int(contrib_gain_match.group("gain") or 0)
        if calamity_gain_match:
            parsed["calamity_delta"] = int(calamity_gain_match.group("gain") or 0)
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
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    active_status = str((timeline.get("active_step") or {}).get("status") or "").strip()
    if timeline.get("phase") in {"state_confirmed", "downstream_released", "waiting_send"} or active_status in {"confirmed", "released", "pending"}:
        return True
    if active_status in {"sending", "sent_waiting_ack", "ack_timeout"}:
        active_step = timeline.get("active_step") or {}
        wake_at = float(
            active_step.get("calibration_due_at")
            or active_step.get("ack_due_at")
            or timeline.get("blocked_until")
            or 0
        )
        if wake_at > 0 and wake_at <= float(now):
            return True
    craft_farm = timeline.get("craft_farm") if isinstance(timeline.get("craft_farm"), dict) else {}
    craft_next_time = float(craft_farm.get("next_time", 0) or 0)
    craft_phase = str(craft_farm.get("phase") or "").strip()
    if craft_next_time > float(now) and craft_phase in {
        "waiting",
        "complete",
        "daily_limit_reached",
        "dry_run",
        "prediction_conflict",
        "timeline_waiting",
        "sent_waiting_reply",
        "crafting_waiting_final",
        "calibrating",
        "send_blocked",
    }:
        if _craft_farm_stale_consume_wait_should_wake(craft_farm, now, config):
            return True
        return False
    if timeline.get("phase") == "blocked_replan":
        return float(timeline.get("blocked_until", 0) or 0) <= float(now)
    windows, _off_window = _build_tianxing_craft_farm_windows(now, config, reason="天星自动调度")
    if not windows:
        return False
    plan = build_tianxing_timeline_plan(now=now, windows=windows, observed=observed, config=config)
    return any(str(step.get("action") or "").strip() for step in plan.get("steps") or [])


def _timeline_has_existing_work(now=None):
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    active_step = timeline.get("active_step") or {}
    active_status = str(active_step.get("status") or "").strip()
    phase = str(timeline.get("phase") or "").strip()
    if active_status in {"sending", "sent_waiting_ack", "ack_timeout"}:
        if now is None:
            return True
        try:
            now_value = float(now)
        except (TypeError, ValueError, OverflowError):
            now_value = time.time()
        due_candidates = []
        for key in ("calibration_due_at", "ack_due_at"):
            try:
                value = float(active_step.get(key, 0) or 0)
            except (TypeError, ValueError, OverflowError):
                value = 0.0
            if value > 0:
                due_candidates.append(value)
        try:
            blocked_until = float(timeline.get("blocked_until", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            blocked_until = 0.0
        if blocked_until > 0:
            due_candidates.append(blocked_until)
        if active_status == "sending" and not due_candidates:
            return True
        return bool(due_candidates and min(due_candidates) <= now_value)
    return bool(
        phase in {"state_confirmed", "waiting_send"}
        or active_status in {"confirmed", "pending"}
    )


def _timeline_followup_time(timeline, now, config):
    timeline = normalize_tianxing_timeline_state(timeline)
    active_step = timeline.get("active_step") or {}
    for key in ("ack_due_at", "calibration_due_at"):
        try:
            value = float(active_step.get(key, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            value = 0.0
        if value > float(now):
            return value
    try:
        blocked_until = float(timeline.get("blocked_until", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        blocked_until = 0.0
    if blocked_until > float(now):
        return blocked_until
    return float(now + min(60, max(5, _craft_farm_interval_sec(config))))


async def _drain_existing_tianxing_timeline(now, config):
    """Advance an already-created timeline even when the farm window is closed."""
    if not _timeline_has_existing_work(now):
        return {}
    last_result = {}
    mutated = False
    for _ in range(3):
        if not _timeline_has_existing_work(now):
            break
        before = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
        before_key = (
            before.get("phase"),
            before.get("active_step_index"),
            str((before.get("active_step") or {}).get("status") or ""),
            str((before.get("active_step") or {}).get("action") or ""),
        )
        result = await run_tianxing_timeline_scheduler(now, config=config)
        last_result = dict(result or {})
        after = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
        after_key = (
            after.get("phase"),
            after.get("active_step_index"),
            str((after.get("active_step") or {}).get("status") or ""),
            str((after.get("active_step") or {}).get("action") or ""),
        )
        if after_key == before_key:
            break
        mutated = True
        if str((after.get("active_step") or {}).get("status") or "").strip() in {"sending", "sent_waiting_ack", "ack_timeout"}:
            break
    if not mutated:
        return {}
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    return dict(
        last_result,
        active=True,
        next_time=_timeline_followup_time(timeline, now, config),
        timeline_phase=timeline.get("phase") or "",
        timeline_action=str((timeline.get("active_step") or {}).get("action") or ""),
    )


def apply_tianxing_passive(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    parsed = parse_tianxing_text(text, now=now, family=family)
    if not parsed:
        return False

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
    previous_prediction = _normalize_route_choice(observed.get("current_prediction"), "")
    prediction_reward_this_text = False
    today_key = get_day_key(now)
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
    if parsed.get("result") == "cooldown" and parsed.get("action") in {"推命", "改命"}:
        observed["last_error"] = ""
    if parsed.get("available_stars") is not None:
        observed["available_stars"] = list(parsed.get("available_stars") or [])
        if parsed.get("available_stars"):
            observed["available_stars_day"] = today_key
        else:
            observed["available_stars_day"] = ""
    if parsed.get("fixed_star") is not None:
        if parsed.get("fixed_star"):
            observed["fixed_star_day"] = today_key
        else:
            observed["fixed_star_day"] = ""
    for key in ("current_prediction_until", "current_change_until"):
        if key in parsed:
            observed[key] = float(parsed.get(key) or 0)
    if parsed.get("action") == "推命":
        predicted_route = _normalize_route_choice(parsed.get("current_prediction") or parsed.get("last_route"), "")
        if parsed.get("result") in {"success", "cooldown"} and predicted_route:
            observed["current_prediction_set_at"] = now
            if _normalize_route_choice(observed.get("prediction_consumed_route"), "") == predicted_route:
                observed["prediction_consumed_route"] = ""
                observed["prediction_consumed_at"] = 0
    raw_text_for_prediction = str(text or "")
    prediction_hit_text = "【推命命中】" in raw_text_for_prediction
    prediction_miss_text = "【推命落空】" in raw_text_for_prediction
    if parsed.get("result") in {"prediction_hit", "prediction_miss", "change_triggered"} and (prediction_hit_text or prediction_miss_text):
        consumed_route = previous_prediction or _normalize_route_choice(parsed.get("last_route"), "") or _normalize_route_choice(observed.get("current_prediction"), "")
        if consumed_route:
            _consume_tianxing_released_route(consumed_route, now)
            prediction_reward_this_text = True
            if prediction_miss_text:
                observed["prediction_consumed_route"] = consumed_route
                observed["prediction_consumed_at"] = now
            elif prediction_hit_text:
                observed["prediction_consumed_route"] = consumed_route
                observed["prediction_consumed_at"] = now
        if prediction_miss_text or prediction_hit_text:
            observed["current_prediction"] = ""
            observed["current_prediction_until"] = 0
    elif parsed.get("result") in {"success", "failure"}:
        observed_route = _normalize_route_choice(parsed.get("last_route"), "")
        if observed_route and previous_prediction == observed_route and _has_active_unconsumed_prediction(observed_route, observed, now):
            _consume_tianxing_released_route(observed_route, now, reason="route_result_observed_without_prediction")
            observed["prediction_consumed_route"] = observed_route
            observed["prediction_consumed_at"] = now
    elif parsed.get("result") in {"modifier", "change_triggered"}:
        observed_route = _normalize_route_choice(parsed.get("last_route"), "")
        if observed_route:
            _consume_tianxing_released_route(observed_route, now, reason="route_result_observed_without_prediction")
        if observed_route and previous_prediction == observed_route:
            observed["current_prediction"] = ""
            observed["current_prediction_until"] = 0
            observed["prediction_consumed_route"] = ""
            observed["prediction_consumed_at"] = 0
    for key in ("tianji_value", "calamity_count", "hit_count", "miss_count", "change_count", "last_tianji_gain", "last_contrib_gain", "last_bonus_gain"):
        if parsed.get(key) is not None:
            observed[key] = int(parsed.get(key) or 0)
    passive_gain_family = family != "tianxing_craft_farm" and not str(family or "").startswith("tianxing_retreat_farm")
    tianji_gain = int(parsed.get("last_tianji_gain", 0) or 0)
    if passive_gain_family and prediction_reward_this_text and tianji_gain > 0 and parsed.get("tianji_value") is None:
        observed["tianji_value"] = int(observed.get("tianji_value", 0) or 0) + tianji_gain
    if parsed.get("calamity_delta") is not None:
        observed["calamity_count"] = max(0, int(observed.get("calamity_count", 0) or 0) + int(parsed.get("calamity_delta") or 0))
    if parsed.get("action") == "消劫" and parsed.get("result") == "success":
        observed["calamity_count"] = max(0, int(observed.get("calamity_count", 0) or 0) - 1)
    if _auto_pending_matches_parsed(observed, parsed):
        _clear_tianxing_auto_pending(observed)
    observed["auto_last_error"] = ""
    observed["auto_last_error_at"] = 0
    if int(observed.get("calamity_count", 0) or 0) > 0:
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
    elif not _effective_fixed_star(observed, now):
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
    _close_tianxing_guards_from_observation(observed, now)
    _prune_tianxing_released_routes(observed, now)
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
        fixed_star = _effective_fixed_star(observed, now)
        fixed_star_day = str(observed.get("fixed_star_day") or "").strip()
        if fixed_star == star and (not fixed_star_day or fixed_star_day == get_day_key(now)):
            return _manual_block(action, f"当前已是目标命星：{fixed_star}，不重复定命。")
        available_stars_day = str(observed.get("available_stars_day") or "").strip()
        available_stars = _available_stars_for_day(observed, now)
        if available_stars_day and available_stars_day != get_day_key(now):
            return _manual_block(action, "未记录今日可选命星，先手动观命。")
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
            else:
                return _manual_block(action, f"已有推命 {current} 尚未应验，{fmt_remaining(prediction_until)} 后再试。")
        if str(observed.get("current_prediction") or "").strip() and prediction_until <= 0:
            current = observed.get("current_prediction") or "未记录"
            if not (current == route and allow_same_route_probe):
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
    observed["auto_last_error_at"] = float(now) if observed["auto_last_error"] else 0
    observed["auto_next_time"] = float(next_time or now + TIANXING_AUTO_BLOCK_BACKOFF_SEC)
    state["tianxing_observation"] = observed
    save_state()


def _tianxing_phaseful_defer_payload(now, action):
    reason = get_phaseful_summary_risk_reason(now, lead_sec=60)
    if not reason:
        return {}
    defer_until = float(now + random.uniform(TIANXING_PHASEFUL_DEFER_MIN_SEC, TIANXING_PHASEFUL_DEFER_MAX_SEC))
    action = str(action or "自动命令").strip() or "自动命令"
    return {
        "reason": reason,
        "next_time": defer_until,
        "error": f"{reason}，天星{action}延后发送",
    }


def _log_tianxing_phaseful_defer(action, command, payload):
    if not payload:
        return
    command = str(command or "").strip() or "未生成命令"
    console_log(
        f"🌌 天星避让结算：{action}｜{command}｜{payload['reason']}，延后到 {fmt_abs_ts(payload['next_time'])}",
        scope="identity",
        limit=180,
    )


def _defer_tianxing_timeline_step_for_phaseful_summary(timeline, step, now, action, command):
    payload = _tianxing_phaseful_defer_payload(now, "时间线前置命令")
    if not payload:
        return False
    step = dict(step or {})
    step["status"] = "pending"
    step["deferred_at"] = float(now)
    step["last_error"] = payload["error"]
    timeline["phase"] = "phaseful_deferred"
    timeline["blocked_until"] = payload["next_time"]
    timeline["last_error"] = payload["error"]
    timeline["updated_at"] = float(now)
    _set_timeline_step(timeline, _timeline_active_index(timeline), step)
    _timeline_audit(timeline, now, "phaseful_deferred", action=action, command=command, reason=payload["reason"], next_time=payload["next_time"])
    _log_tianxing_phaseful_defer("时间线", command, payload)
    return True


def _defer_tianxing_farm_for_phaseful_summary(farm, now, *, kind, action, command):
    payload = _tianxing_phaseful_defer_payload(now, action)
    if not payload:
        return {}
    farm["phase"] = "phaseful_deferred"
    farm["next_time"] = payload["next_time"]
    farm["last_command"] = str(command or "").strip()
    farm["last_error"] = payload["error"]
    farm["last_result"] = "phaseful_deferred"
    if kind == "retreat":
        _retreat_farm_audit(farm, now, "phaseful_deferred", action=action, command=command, reason=payload["reason"], next_time=payload["next_time"])
        _set_tianxing_retreat_farm_state(farm, now)
    else:
        _craft_farm_audit(farm, now, "phaseful_deferred", action=action, command=command, reason=payload["reason"], next_time=payload["next_time"])
        _set_tianxing_craft_farm_state(farm, now)
    save_state()
    _log_tianxing_phaseful_defer(action, command, payload)
    return payload


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


def _defer_tianxing_auto_plan_for_phaseful_summary(observed, now, plan):
    action = str((plan or {}).get("action") or "auto").strip()
    command = str((plan or {}).get("command") or "").strip()
    payload = _tianxing_phaseful_defer_payload(now, "自动命令")
    if not payload:
        return False
    _clear_tianxing_auto_pending(observed)
    observed["auto_last_plan"] = command
    observed["auto_last_plan_at"] = float(now)
    _set_tianxing_auto_wait(observed, now, action, payload["next_time"], payload["error"])
    _log_tianxing_phaseful_defer("自动调度", command, payload)
    return True


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
    observed["auto_last_error_at"] = 0
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
    observed["auto_last_error_at"] = float(now)
    observed["auto_next_time"] = float(now + _tianxing_send_fail_backoff_sec(action, observed, now))
    state["tianxing_observation"] = observed
    save_state()
    return True


def get_tianxing_automation_pause_state(now=None, observed=None):
    now = float(now if now is not None else time.time())
    observed = normalize_tianxing_observation(observed if observed is not None else state.get("tianxing_observation"))
    until = float(observed.get("automation_paused_until", 0) or 0)
    paused = bool(until < 0 or until > now)
    reason = str(observed.get("automation_paused_reason") or "").strip()
    return {
        "paused": paused,
        "until": until,
        "reason": reason,
        "paused_at": float(observed.get("automation_paused_at", 0) or 0),
    }


def is_tianxing_automation_paused(now=None, observed=None):
    return bool(get_tianxing_automation_pause_state(now=now, observed=observed).get("paused"))


def _format_tianxing_pause_line(pause_state):
    if not pause_state.get("paused"):
        return "未暂停"
    until = float(pause_state.get("until", 0) or 0)
    reason = str(pause_state.get("reason") or "手动暂停").strip()
    if until < 0:
        return f"已暂停（手动恢复前不接管，原因：{reason}）"
    return f"已暂停至 {fmt_abs_ts(until)}（{fmt_remaining(until)}，原因：{reason}）"


def get_tianxing_automation_pause_text(now=None, observed=None):
    return _format_tianxing_pause_line(get_tianxing_automation_pause_state(now=now, observed=observed))


def _tianxing_pause_block_until(now, observed=None, config=None):
    pause_state = get_tianxing_automation_pause_state(now=now, observed=observed)
    until = float(pause_state.get("until", 0) or 0)
    if until > now:
        return until
    return float(now + _status_backoff_sec(config or normalize_tianxing_auto_config(state.get("tianxing_auto_config"))))


def _apply_tianxing_pause_wait(observed, now, config=None):
    changed = False
    if str(observed.get("auto_pending_action") or "").strip():
        _clear_tianxing_auto_pending(observed)
        changed = True
    next_time = _tianxing_pause_block_until(now, observed=observed, config=config)
    if (
        changed
        or str(observed.get("auto_last_action") or "") != "paused"
        or str(observed.get("auto_last_error") or "") != "天星自动调度已暂停；等待日志群 .天星恢复。"
        or float(observed.get("auto_next_time", 0) or 0) <= now
    ):
        observed["auto_last_action"] = "paused"
        observed["auto_last_error"] = "天星自动调度已暂停；等待日志群 .天星恢复。"
        observed["auto_last_error_at"] = float(now)
        observed["auto_last_plan"] = ""
        observed["auto_last_plan_at"] = float(now)
        observed["auto_next_time"] = next_time
        state["tianxing_observation"] = observed
        save_state()
    return changed


def set_tianxing_automation_paused(paused=True, *, now=None, duration_sec=0, reason="手动暂停"):
    now = float(now if now is not None else time.time())
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if paused:
        duration = float(duration_sec or 0)
        observed["automation_paused_until"] = float(now + duration) if duration > 0 else -1.0
        observed["automation_paused_at"] = float(now)
        observed["automation_paused_reason"] = str(reason or "手动暂停").strip() or "手动暂停"
        _clear_tianxing_auto_pending(observed)
        observed["auto_last_action"] = "paused"
        observed["auto_last_error"] = "天星自动调度已暂停；手动恢复前不接管路线。"
        observed["auto_last_error_at"] = float(now)
        observed["auto_last_plan"] = ""
        observed["auto_last_plan_at"] = float(now)
        observed["auto_next_time"] = _tianxing_pause_block_until(now, observed=observed)
    else:
        observed["automation_paused_until"] = 0
        observed["automation_paused_at"] = 0
        observed["automation_paused_reason"] = ""
        _clear_tianxing_auto_pending(observed)
        observed["auto_last_action"] = "resumed"
        observed["auto_last_error"] = ""
        observed["auto_last_error_at"] = 0
        observed["auto_last_plan"] = ""
        observed["auto_last_plan_at"] = float(now)
        observed["auto_next_time"] = float(now)
    state["tianxing_observation"] = observed
    save_state()
    return get_tianxing_automation_pause_state(now=now, observed=observed)


def _status_backoff_sec(config):
    return max(3600, int(float((config or {}).get("status_backoff_hours", 6) or 6) * 3600))


def _choose_by_priority(candidates, priority):
    candidates = [str(item).strip() for item in (candidates or []) if str(item or "").strip()]
    for item in priority or []:
        if item in candidates:
            return item
    return candidates[0] if candidates else ""


_TIANXING_STABLE_STAR_PRIORITY = ["太阴", "贪狼"]
_TIANXING_ROUTE_STAR_PRIORITY = {
    "探索": ["太阴", "贪狼"],
    "闭关": ["太阴", "贪狼"],
    "炼制": ["太阴", "贪狼"],
    "斗法": ["太阴", "贪狼"],
}
_TIANXING_SPECIAL_ROUTE_STAR_PRIORITY = {
    "探索": ["贪狼"],
    "闭关": ["紫微"],
    "炼制": ["天府"],
    "斗法": ["太阴"],
}


def _route_star_priority(route, config=None):
    route = _normalize_route_choice(route, "")
    if route not in TIANXING_ROUTES:
        return []
    if (config or {}).get("route_special_star_enabled"):
        return list(_TIANXING_SPECIAL_ROUTE_STAR_PRIORITY.get(route) or [])
    return list(_TIANXING_ROUTE_STAR_PRIORITY.get(route) or _TIANXING_STABLE_STAR_PRIORITY)


def _choose_route_star(route, available_stars, config=None, fixed_star=""):
    route = _normalize_route_choice(route, "")
    available_stars = [str(item).strip() for item in (available_stars or []) if str(item or "").strip()]
    fixed_star = str(fixed_star or "").strip()
    if route not in TIANXING_ROUTES or not available_stars:
        return ""
    priority = _route_star_priority(route, config)
    if fixed_star in priority:
        return fixed_star
    for item in priority:
        if item in available_stars:
            return item
    return ""


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

    fixed_star = _effective_fixed_star(observed, now)
    available_stars = _available_stars_for_day(observed, now)
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
        "require_change_fate": _coerce_bool(item.get("require_change_fate"), False),
    }


def build_tianxing_farm_window(*, now=None, config=None, reason="深度闭关"):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    if not config.get("farm_window_enabled"):
        return []
    route = _normalize_route_choice(config.get("farm_route"), "闭关")
    if route not in TIANXING_ROUTES:
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
    specs = _parse_tianxing_farm_window_specs(config.get("farm_windows_text"))
    if not specs:
        duration_sec = int(config.get("farm_window_duration_min", 60) or 60) * 60
        if duration_sec <= 0:
            return []
        specs = [{
            "start": config.get("farm_window_start", "02:00"),
            "end": _seconds_to_hhmm(_hhmm_to_seconds(config.get("farm_window_start", "02:00")) + duration_sec),
            "start_sec": _hhmm_to_seconds(config.get("farm_window_start", "02:00")),
            "duration_sec": duration_sec,
        }]
    active_windows = []
    for item in specs:
        for day_offset in (0, -24 * 3600):
            start_at = float(midnight + day_offset + int(item.get("start_sec", 0) or 0))
            end_at = float(start_at + int(item.get("duration_sec", 0) or 0))
            if start_at <= now <= end_at:
                active_windows.append({
                    "route": route,
                    "kind": "farm",
                    "start_at": start_at,
                    "end_at": end_at,
                    "weight": 8,
                    "reason": str(reason or "天星攒天机窗口"),
                })
    return sorted(active_windows, key=lambda item: (float(item.get("start_at", 0) or 0), float(item.get("end_at", 0) or 0)))


def next_tianxing_farm_window_start(*, now=None, config=None):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    if not config.get("farm_window_enabled"):
        return 0.0
    specs = _parse_tianxing_farm_window_specs(config.get("farm_windows_text"))
    if not specs:
        return 0.0
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
    candidates = []
    for day_offset in (0, 24 * 3600, 2 * 24 * 3600):
        for item in specs:
            start_at = float(midnight + day_offset + int(item.get("start_sec", 0) or 0))
            if start_at > now:
                candidates.append(start_at)
    return min(candidates) if candidates else 0.0


def _build_tianxing_craft_farm_windows(now, config, *, reason="天星炼制攒点"):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config)
    windows = build_tianxing_farm_window(now=now, config=config, reason=reason)
    if windows or not config.get("craft_farm_off_window_enabled"):
        return windows, False
    min_sec, max_sec = _craft_off_window_interval_bounds(config)
    return ([{
        "route": "炼制",
        "kind": "farm",
        "start_at": now,
        "end_at": now + max(min_sec, min(max_sec, 2 * 3600)),
        "weight": 2,
        "reason": "窗口外低频炼制攒点",
    }], True)


def build_tianxing_consume_window(route, *, now=None, due_at=0, config=None, reason="路线动作", require_change_fate=False):
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
        "require_change_fate": bool(require_change_fate),
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


def _preferred_star_route(normalized_windows, dominant_route, now):
    candidates = []
    for item in normalized_windows or []:
        route = _normalize_route_choice(item.get("route"), "")
        if route not in TIANXING_ROUTES:
            continue
        kind = str(item.get("kind") or "").strip()
        kind_order = 0 if kind == "consume" else 1
        candidates.append((
            max(float(item.get("start_at", 0) or 0), float(now or 0)),
            kind_order,
            -float(item.get("weight", 0) or 0),
            route,
        ))
    if candidates:
        return sorted(candidates)[0][3]
    return _normalize_route_choice(dominant_route, "")


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
    if _prediction_effective_until(route, observed, now) <= float(now):
        return False
    if _is_prediction_consumed(route, observed, now):
        return False
    observed_at = float(observed.get("last_observed_at", 0) or 0)
    if observed_at <= _last_craft_farm_result_at(timeline) + 0.001:
        return False
    set_at = float(observed.get("current_prediction_set_at", 0) or 0)
    if set_at <= 0:
        last_action = str(observed.get("last_action") or "").strip()
        last_result = str(observed.get("last_result") or "").strip()
        if last_action == "推命" and last_result in {"success", "cooldown"} and _normalize_route_choice(observed.get("last_route"), "") == route:
            set_at = observed_at
    if set_at <= 0:
        released = (timeline or {}).get("released_routes") or {}
        release_item = released.get(route) if isinstance(released, dict) else {}
        try:
            released_at = float((release_item or {}).get("released_at", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            released_at = 0.0
        if released_at > 0:
            set_at = released_at
    if set_at <= 0 or float(now) - set_at > TIANXING_ROUTE_LEASE_GUARD_MAX_AGE_SEC:
        return False
    return True


def _prediction_effective_until(route, observed, now=None):
    now = float(now if now is not None else time.time())
    route = _normalize_route_choice(route, "")
    if route not in TIANXING_ROUTES:
        return 0.0
    observed = normalize_tianxing_observation(observed)
    if str(observed.get("current_prediction") or "").strip() != route:
        return 0.0
    prediction_until = float(observed.get("current_prediction_until", 0) or 0)
    if prediction_until > now:
        return prediction_until
    set_at = float(observed.get("current_prediction_set_at", 0) or 0)
    if set_at > 0 and set_at <= now and now - set_at < TIANXING_PREDICTION_SEC:
        return set_at + TIANXING_PREDICTION_SEC
    return 0.0


def _is_prediction_consumed(route, observed, now=None):
    route = _normalize_route_choice(route, "")
    if route not in TIANXING_ROUTES:
        return False
    observed = normalize_tianxing_observation(observed)
    consumed_route = _normalize_route_choice(observed.get("prediction_consumed_route"), "")
    if consumed_route != route:
        return False
    consumed_at = float(observed.get("prediction_consumed_at", 0) or 0)
    if consumed_at <= 0:
        return False
    set_at = float(observed.get("current_prediction_set_at", 0) or 0)
    return consumed_at + 0.001 >= set_at


def _has_active_unconsumed_prediction(route, observed, now=None):
    now = float(now if now is not None else time.time())
    route = _normalize_route_choice(route, "")
    observed = normalize_tianxing_observation(observed)
    return bool(
        route
        and str(observed.get("current_prediction") or "").strip() == route
        and _prediction_effective_until(route, observed, now) > now
        and not _is_prediction_consumed(route, observed, now)
    )


def _route_arg_from_command(command, prefix):
    raw = str(command or "").strip()
    prefix = str(prefix or "").strip()
    if not prefix or not raw.startswith(prefix):
        return ""
    return _normalize_route_choice(raw[len(prefix):].strip(), "")


def _star_arg_from_command(command):
    raw = str(command or "").strip()
    prefix = CMD_TIANXING_SET_STAR
    if not raw.startswith(prefix):
        return ""
    star = raw[len(prefix):].strip()
    return star if star in TIANXING_STARS else ""


def _close_tianxing_guards_from_observation(observed, now):
    send_as_id = int(get_current_identity_id() or 0)
    if send_as_id <= 0:
        return 0
    try:
        from .. import action_guard
    except Exception:
        return 0
    sessions = action_guard.get_action_guard_sessions(send_as_id)
    if not isinstance(sessions, dict) or not sessions:
        return 0

    now = float(now if now is not None else time.time())
    original_observed = observed if isinstance(observed, dict) else None
    observed = normalize_tianxing_observation(observed)
    observed_at = float(observed.get("last_observed_at", 0) or 0)
    last_action = str(observed.get("last_action") or "").strip()
    closed = 0

    def _session_sent_before_observation(session):
        try:
            sent_at = float((session or {}).get("last_sent_at", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            sent_at = 0.0
        return observed_at <= 0 or sent_at <= 0 or observed_at + 0.001 >= sent_at

    def _session_sent_at(session):
        try:
            return float((session or {}).get("last_sent_at", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return 0.0

    predict = sessions.get("tianxing_predict") or {}
    predicted_route = _route_arg_from_command(predict.get("last_command"), CMD_TIANXING_PREDICT)
    predict_sent_at = _session_sent_at(predict)
    if (
        predicted_route
        and _session_sent_before_observation(predict)
        and str(observed.get("current_prediction") or "").strip() == predicted_route
        and float(observed.get("current_prediction_until", 0) or 0) > now
        and action_guard.close_action("tianxing_predict", send_as_id=send_as_id, reason="tianxing_observed_prediction", now=now)
    ):
        if predict_sent_at > 0:
            observed["current_prediction_set_at"] = max(float(observed.get("current_prediction_set_at", 0) or 0), predict_sent_at)
        closed += 1

    change = sessions.get("tianxing_change_fate") or {}
    changed_route = _route_arg_from_command(change.get("last_command"), CMD_TIANXING_CHANGE_FATE)
    if (
        changed_route
        and _session_sent_before_observation(change)
        and str(observed.get("current_change") or "").strip() == changed_route
        and float(observed.get("current_change_until", 0) or 0) > now
        and action_guard.close_action("tianxing_change_fate", send_as_id=send_as_id, reason="tianxing_observed_change_fate", now=now)
    ):
        closed += 1

    set_star = sessions.get("tianxing_set_star") or {}
    star = _star_arg_from_command(set_star.get("last_command"))
    if (
        star
        and _session_sent_before_observation(set_star)
        and str(observed.get("fixed_star") or "").strip() == star
        and action_guard.close_action("tianxing_set_star", send_as_id=send_as_id, reason="tianxing_observed_star", now=now)
    ):
        closed += 1

    panel = sessions.get("tianxing_panel") or {}
    if (
        panel
        and _session_sent_before_observation(panel)
        and last_action in {"天机盘", "玩法帮助", "宗门信息"}
        and action_guard.close_action("tianxing_panel", send_as_id=send_as_id, reason="tianxing_observed_panel", now=now)
    ):
        closed += 1

    observe = sessions.get("tianxing_observe") or {}
    if (
        observe
        and _session_sent_before_observation(observe)
        and bool(observed.get("available_stars") or [])
        and action_guard.close_action("tianxing_observe", send_as_id=send_as_id, reason="tianxing_observed_stars", now=now)
    ):
        closed += 1

    if closed and original_observed is not None and original_observed is not observed:
        original_observed.clear()
        original_observed.update(observed)
    return closed


def _tianxing_reply_family_for_action(action):
    action = str(action or "").strip()
    return {
        "panel": "tianxing_panel",
        "observe": "tianxing_observe",
        "set_star": "tianxing_set_star",
        "predict": "tianxing_predict",
        "change_fate": "tianxing_change_fate",
        "clear_calamity": "tianxing_clear_calamity",
    }.get(action, "")


def _close_tianxing_guard_for_timeline_step(step, now, *, reason="timeline_send_unknown"):
    send_as_id = int(get_current_identity_id() or 0)
    if send_as_id <= 0:
        return False
    family = _tianxing_reply_family_for_action((step or {}).get("action"))
    if not family:
        return False
    try:
        from .. import action_guard
    except Exception:
        return False
    return bool(action_guard.close_by_family(family, send_as_id=send_as_id, reason=reason, now=now))


def _tianxing_action_guard_wait(command, now):
    command = str(command or "").strip()
    if not command:
        return 0.0, ""
    send_as_id = int(get_current_identity_id() or 0)
    if send_as_id <= 0:
        return 0.0, ""
    try:
        from .. import action_guard
    except Exception:
        return 0.0, ""
    try:
        blocked_until, reason = action_guard.get_timing_blocked_until(command, send_as_id=send_as_id, now=now)
    except Exception:
        return 0.0, ""
    try:
        blocked_until = float(blocked_until or 0)
    except (TypeError, ValueError, OverflowError):
        blocked_until = 0.0
    if blocked_until <= float(now or time.time()):
        return 0.0, ""
    return blocked_until + 2, str(reason or "安全锁短窗保护中，延后发送。").strip()


def _prune_tianxing_released_routes(observed, now):
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    released = dict(timeline.get("released_routes") or {})
    observed = normalize_tianxing_observation(observed)
    now = float(now if now is not None else time.time())
    kept = {}
    removed = []
    current_prediction = _normalize_route_choice(observed.get("current_prediction"), "")
    prediction_until = float(observed.get("current_prediction_until", 0) or 0)
    current_change = _normalize_route_choice(observed.get("current_change"), "")
    change_until = float(observed.get("current_change_until", 0) or 0)
    def _release_basis_valid(route, basis):
        route = _normalize_route_choice(route, "")
        prediction_ready = _has_active_unconsumed_prediction(route, observed, now) and _has_fresh_prediction_evidence(route, observed, timeline, now)
        if basis == "prediction":
            return prediction_ready
        if basis == "change_fate":
            return current_change == route and change_until > now and prediction_ready
        return (
                prediction_ready
                or (current_change == route and change_until > now)
            )

    for route, item in released.items():
        route = _normalize_route_choice(route, "")
        if route not in TIANXING_ROUTES or not isinstance(item, dict):
            removed.append(route or "?")
            continue
        basis = str(item.get("basis") or item.get("release_basis") or "").strip()
        valid = _release_basis_valid(route, basis)
        if valid:
            kept[route] = item
        else:
            removed.append(route)

    changed = False
    if removed:
        timeline["released_routes"] = kept
        timeline["updated_at"] = float(now)
        _timeline_audit(timeline, now, "released_routes_pruned", routes=",".join(sorted(set(removed))))
        changed = True

    active_step = dict(timeline.get("active_step") or {})
    active_status = str(active_step.get("status") or "").strip()
    active_action = str(active_step.get("action") or "").strip()
    active_route = _normalize_route_choice(active_step.get("route") or active_step.get("arg"), "")
    active_basis = str(active_step.get("basis") or active_step.get("release_basis") or "").strip()
    if (
        active_status == "released"
        and active_action == "release_downstream"
        and active_route
        and not _release_basis_valid(active_route, active_basis)
    ):
        timeline["phase"] = "blocked_replan"
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["blocked_until"] = float(now)
        timeline["last_error"] = f"{active_route} 放行依据已失效，需重算时间线。"
        timeline["updated_at"] = float(now)
        _timeline_audit(timeline, now, "released_step_invalidated", route=active_route, basis=active_basis)
        changed = True

    if changed:
        state["tianxing_timeline_state"] = timeline
    return changed


def _consume_tianxing_released_route(route, now, reason="route_result_consumed"):
    route = _normalize_route_choice(route, "")
    if route not in TIANXING_ROUTES:
        return False
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    released = dict(timeline.get("released_routes") or {})
    changed = False
    if route in released:
        released.pop(route, None)
        timeline["released_routes"] = released
        timeline["updated_at"] = float(now or time.time())
        _timeline_audit(timeline, now, "released_route_consumed", route=route, reason=reason)
        changed = True
    active_step = dict(timeline.get("active_step") or {})
    active_action = str(active_step.get("action") or "").strip()
    active_status = str(active_step.get("status") or "").strip()
    active_route = _normalize_route_choice(active_step.get("route") or active_step.get("arg"), "")
    if (
        active_status == "released"
        and active_action == "release_downstream"
        and active_route == route
    ):
        timeline["phase"] = "blocked_replan"
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["blocked_until"] = float(now or time.time())
        timeline["last_error"] = f"{route} 放行已被下游动作消费，需重算时间线。"
        timeline["updated_at"] = float(now or time.time())
        _timeline_audit(timeline, now, "released_step_consumed", route=route)
        changed = True
    elif (
        active_action in {"predict", "change_fate"}
        and active_status in {"sending", "sent_waiting_ack", "ack_timeout", "send_blocked"}
        and active_route == route
    ):
        active_step["status"] = "consumed_by_route_result"
        active_step["consumed_at"] = float(now or time.time())
        active_step["last_error"] = f"{route} 路线结果已出现，未确认前置步骤停止校准。"
        _set_timeline_step(timeline, _timeline_active_index(timeline), active_step)
        _close_tianxing_guard_for_timeline_step(active_step, now, reason="route_result_consumed")
        timeline["phase"] = "blocked_replan"
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["blocked_until"] = float(now or time.time())
        timeline["last_error"] = f"{route} 路线结果已出现，需重算时间线。"
        timeline["updated_at"] = float(now or time.time())
        _timeline_audit(timeline, now, "unconfirmed_step_consumed_by_route_result", route=route, action=active_action, status=active_status)
        changed = True
    if changed:
        state["tianxing_timeline_state"] = timeline
    return changed


def _clear_unconfirmed_timeline_step_for_observed_route_result(observed, now):
    observed = normalize_tianxing_observation(observed)
    observed_route = _normalize_route_choice(observed.get("last_route"), "")
    if observed_route not in TIANXING_ROUTES:
        return False
    if str(observed.get("last_result") or "").strip() not in {"prediction_hit", "prediction_miss", "change_triggered", "modifier"}:
        return False

    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    active_step = dict(timeline.get("active_step") or {})
    active_action = str(active_step.get("action") or "").strip()
    active_status = str(active_step.get("status") or "").strip()
    active_route = _normalize_route_choice(active_step.get("route") or active_step.get("arg"), "")
    observed_at = float(observed.get("last_observed_at", 0) or 0)

    def _step_latest_mark(step):
        latest = 0.0
        for key in ("send_started_at", "sent_at", "timeout_at", "blocked_at"):
            try:
                latest = max(latest, float((step or {}).get(key, 0) or 0))
            except (TypeError, ValueError, OverflowError):
                continue
        return latest

    def _observed_after_step(step):
        step_at = _step_latest_mark(step)
        return not (step_at > 0 and observed_at > 0 and observed_at + 0.001 < step_at)

    active_index = _timeline_active_index(timeline)
    route_step_index = -1
    route_step = {}
    steps = list(timeline.get("steps") or [])
    for index in range(min(active_index, len(steps)) - 1, -1, -1):
        candidate = dict(steps[index] or {})
        candidate_action = str(candidate.get("action") or "").strip()
        candidate_route = _normalize_route_choice(candidate.get("route") or candidate.get("arg"), "")
        if candidate_action in {"predict", "change_fate"} and candidate_route == observed_route:
            route_step_index = index
            route_step = candidate
            break

    if active_action in {"predict", "change_fate"}:
        if active_status not in {"sending", "sent_waiting_ack", "ack_timeout", "send_blocked"}:
            return False
        if active_route != observed_route:
            return False
        if not _observed_after_step(active_step):
            return False
        consumed_status = "consumed_by_observed_route_result"
        audit_event = "unconfirmed_step_consumed_by_observed_route_result"
    elif (
        active_action == "panel"
        and active_status in {"pending", "sending", "sent_waiting_ack", "ack_timeout", "send_blocked"}
        and bool(active_step.get("terminal_after_confirm"))
        and route_step
        and _observed_after_step(route_step)
    ):
        consumed_status = "calibration_consumed_by_observed_route_result"
        audit_event = "calibration_consumed_by_observed_route_result"
        if route_step_index >= 0:
            route_step["status"] = "consumed_by_observed_route_result"
            route_step["consumed_at"] = float(now or time.time())
            route_step["last_error"] = f"{observed_route} 路线结果已观察到，停止后续查盘校准。"
            steps[route_step_index] = route_step
            timeline["steps"] = steps
    else:
        return False

    active_step["status"] = consumed_status
    active_step["consumed_at"] = float(now or time.time())
    active_step["last_error"] = f"{observed_route} 路线结果已观察到，未确认前置步骤停止校准。"
    _set_timeline_step(timeline, _timeline_active_index(timeline), active_step)
    _close_tianxing_guard_for_timeline_step(active_step, now, reason="observed_route_result_consumed")
    timeline["phase"] = "blocked_replan"
    timeline["active_step_index"] = -1
    timeline["active_step"] = {}
    timeline["blocked_until"] = float(now or time.time())
    timeline["last_error"] = f"{observed_route} 路线结果已观察到，需重算时间线。"
    timeline["updated_at"] = float(now or time.time())
    _timeline_audit(
        timeline,
        now,
        audit_event,
        route=observed_route,
        action=active_action,
        status=active_status,
    )
    state["tianxing_timeline_state"] = timeline
    return True


def mark_tianxing_route_result_unknown(route, *, now=None, reason=""):
    """Conservatively invalidate a released route when the downstream result text is lost."""
    now = float(now if now is not None else time.time())
    route = _normalize_route_choice(route, "")
    if route not in TIANXING_ROUTES:
        return False
    if not state.get("tianxing_enabled") or not is_module_available("天星宗"):
        return False

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    changed = False
    summary_reason = str(reason or "下游路线结果未留存").strip()

    if _has_active_unconsumed_prediction(route, observed, now):
        observed["prediction_consumed_route"] = route
        observed["prediction_consumed_at"] = now
        observed["current_prediction"] = ""
        observed["current_prediction_until"] = 0
        changed = True

    current_change = _normalize_route_choice(observed.get("current_change"), "")
    change_until = float(observed.get("current_change_until", 0) or 0)
    if current_change == route and change_until > now:
        observed["current_change"] = ""
        observed["current_change_until"] = 0
        changed = True

    if changed:
        observed["last_observed_at"] = now
        observed["last_action"] = route
        observed["last_result"] = "unknown_result"
        observed["last_route"] = route
        observed["last_summary"] = f"{route}结果未留存，已按保守策略重算天星路线"
        observed["last_error"] = summary_reason
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
        observed["recent"].append({
            "ts": now,
            "action": observed.get("last_action", ""),
            "result": observed.get("last_result", ""),
            "summary": observed.get("last_summary", ""),
        })
        observed["recent"] = observed["recent"][-8:]
        state["tianxing_observation"] = observed

    timeline_changed = _consume_tianxing_released_route(route, now, reason=summary_reason)
    return bool(changed or timeline_changed)


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
    prediction_effective_until = _prediction_effective_until(current_prediction, observed, now) if current_prediction else 0.0
    prediction_unconsumed = _has_active_unconsumed_prediction(current_prediction, observed, now) if current_prediction else False
    raw_current_change = str(observed.get("current_change") or "").strip()
    change_until = float(observed.get("current_change_until", 0) or 0)
    current_change = raw_current_change if raw_current_change and change_until > now else ""
    fixed_star = _effective_fixed_star(observed, now)
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

    next_consume_route, next_consume = _next_consume_route(normalized_windows)
    next_consume_requires_change = bool((next_consume or {}).get("require_change_fate"))
    critical_change_lacks_tianji = bool(
        next_consume_requires_change
        and next_consume_route
        and next_consume_route == dominant_route
        and tianji_value < min_tianji
    )
    preferred_star_route = _preferred_star_route(normalized_windows, dominant_route, now)
    available_stars = [str(item).strip() for item in observed.get("available_stars") or [] if str(item or "").strip()]
    star_source = str(observed.get("available_stars_source") or "").strip()
    target_star = ""
    route_star_priority = _route_star_priority(preferred_star_route, config)
    fixed_star_matches_route = bool(fixed_star and fixed_star in route_star_priority)
    change_fate_is_primary = bool(
        next_consume_requires_change
        and next_consume_route == preferred_star_route == "探索"
        and not dominant_is_farm
    )
    if fixed_star_matches_route:
        target_star = fixed_star
    elif preferred_star_route and config.get("auto_set_star_enabled"):
        target_star = _choose_route_star(preferred_star_route, available_stars, config, fixed_star=fixed_star)
    needs_star_observe = bool(
        dominant_route
        and preferred_star_route
        and config.get("auto_set_star_enabled")
        and config.get("auto_observe_enabled")
        and not change_fate_is_primary
        and not fixed_star_matches_route
        and (not available_stars or star_source != "observe")
    )
    star_action_needed = bool(
        dominant_route
        and config.get("auto_set_star_enabled")
        and not change_fate_is_primary
        and not fixed_star_matches_route
        and (needs_star_observe or (target_star and target_star != fixed_star))
    )
    star_gate_blocks_plan = False
    active_change_route = current_change
    consume_change_ready = bool(next_consume_route and active_change_route == next_consume_route)
    consume_change_conflicted = bool(next_consume_route and active_change_route and active_change_route != next_consume_route)
    consume_change_can_be_prepared = bool(
        next_consume_route
        and not active_change_route
        and config.get("auto_change_fate_enabled")
        and next_consume_route in TIANXING_AUTO_CHANGE_FATE_ROUTES
        and tianji_value >= min_tianji
    )
    consume_predict_allowed = bool(
        dominant_route
        and next_consume_route == dominant_route
        and config.get("auto_predict_enabled")
        and (
            consume_change_ready
            or consume_change_can_be_prepared
            or consume_change_conflicted
            or (not next_consume_requires_change and config.get("auto_change_fate_enabled"))
        )
    )

    if critical_change_lacks_tianji:
        stage = "need_tianji_for_change"
        change_reason = f"天机值 {tianji_value} 低于改命阈值 {min_tianji}。"
        predict_reason = f"{next_consume_route} 需要先确认改命；天机值不足，等待攒点。"
    elif dominant_route and current_prediction and current_prediction != dominant_route and prediction_unconsumed:
        blocked_by_conflict = True
        blocked_until = prediction_effective_until
        stage = "prediction_conflict"
        predict_reason = f"已有 {current_prediction} 推命仍在生效，当前时间线不应切到 {dominant_route}。"
    elif dominant_route and star_action_needed:
        stage = "need_set_star"
        preferred_star_text = _format_list(route_star_priority) or preferred_star_route or dominant_route
        predict_reason = f"时间线已形成，{preferred_star_route or dominant_route} 前优先尝试 {preferred_star_text}。"
        if needs_star_observe:
            _append_tianxing_step(steps, "observe", reason="定命前需先取得今日观命结果。", now=now)
            star_gate_blocks_plan = True
        elif available_stars and target_star and config.get("auto_set_star_enabled"):
            _append_tianxing_step(
                steps,
                "set_star",
                target_star,
                route=preferred_star_route,
                reason=f"{preferred_star_route or dominant_route} 前切到 {target_star}。",
                now=now,
            )
            if not fixed_star:
                star_gate_blocks_plan = True
    elif dominant_route and current_prediction == dominant_route and prediction_unconsumed:
        prediction_is_fresh = _has_fresh_prediction_evidence(dominant_route, observed, timeline, now)
        consume_needs_fresh_prediction = bool(
            next_consume_requires_change
            and next_consume_route == dominant_route
            and not dominant_is_farm
        )
        if (
            config.get("auto_predict_enabled")
            and not prediction_is_fresh
            and (dominant_is_farm or consume_needs_fresh_prediction)
        ):
            should_predict = True
            stage = "need_predict_probe"
            predict_reason = f"{dominant_route} 推命只来自面板或旧状态，路线消费前先复核推命再放行。"
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
        predict_reason = f"{dominant_route} 在未来 {int(horizon_hours)}h 内承担主攒天机窗口，应由时间线决定是否推命。"
        _append_tianxing_step(steps, "predict", dominant_route, route=dominant_route, reason=predict_reason, now=now)
    elif dominant_route and dominant_is_farm:
        stage = "observe_only"
        predict_reason = f"{dominant_route} 在未来 {int(horizon_hours)}h 内承担主攒天机窗口，但自动推命关闭。"
    elif consume_predict_allowed:
        should_predict = True
        stage = "need_predict_consume"
        predict_reason = f"{dominant_route} 消费窗口即将到来，先推命以匹配下游动作。"
        _append_tianxing_step(steps, "predict", dominant_route, route=dominant_route, reason=predict_reason, now=now)
    elif dominant_route:
        stage = "observe_only"
        predict_reason = f"{dominant_route} 只有消费窗口，没有稳定攒天机窗口，不建议盲发推命。"

    if not star_gate_blocks_plan and not blocked_by_conflict and next_consume_route:
        if active_change_route == next_consume_route:
            recommended_change_route = next_consume_route
            change_reason = f"{recommended_change_route} 改命已待发。"
            release_route = recommended_change_route
            release_reason = f"{recommended_change_route} 改命已确认，可等待下游模块消费。"
        elif (
            not current_change
            and config.get("auto_change_fate_enabled")
            and next_consume_route not in TIANXING_AUTO_CHANGE_FATE_ROUTES
        ):
            change_reason = f"{next_consume_route} 不在自动改命白名单内；自动改命仅允许：{_format_list(TIANXING_AUTO_CHANGE_FATE_ROUTES)}。"
            if next_consume_requires_change:
                stage = "auto_change_fate_route_forbidden"
                predict_reason = change_reason
        elif not current_change and config.get("auto_change_fate_enabled") and tianji_value >= min_tianji:
            recommended_change_route = next_consume_route
            if not change_reason:
                change_reason = f"最近消费窗口是 {recommended_change_route}，若要兜底可预留改命。"
            _append_tianxing_step(steps, "change_fate", recommended_change_route, route=recommended_change_route, reason=change_reason, now=now)
            release_route = recommended_change_route
            release_reason = f"{recommended_change_route} 改命确认后放行下游。"
        elif not current_change and not config.get("auto_change_fate_enabled"):
            change_reason = f"最近消费窗口是 {next_consume_route}，但自动改命关闭。"
            if next_consume_requires_change:
                stage = "need_change_fate"
                predict_reason = f"{next_consume_route} 需要先确认改命，当前自动改命关闭。"
        elif active_change_route:
            recommended_change_route = active_change_route
            change_reason = f"已有 {active_change_route} 改命待发，不覆盖。"
            if next_consume_requires_change:
                stage = "change_fate_conflict"
                blocked_until = change_until
                predict_reason = (
                    f"{next_consume_route} 需要探索改命，但当前已有 {active_change_route} 改命待发；"
                    "本轮不放行需要改命兜底的探索动作。"
                )
        elif tianji_value < min_tianji:
            change_reason = f"天机值 {tianji_value} 低于改命阈值 {min_tianji}。"
            if next_consume_requires_change:
                stage = "need_tianji_for_change"
                predict_reason = f"{next_consume_route} 需要先确认改命；天机值不足，等待攒点。"

    release_requires_change = bool(next_consume_requires_change and next_consume_route == dominant_route)
    if not star_gate_blocks_plan and not release_route and dominant_route and (should_predict or (current_prediction == dominant_route and prediction_unconsumed)) and not release_requires_change:
        release_route = dominant_route
        release_reason = f"{dominant_route} 推命确认后放行对应路线。"
    if release_route and not blocked_by_conflict:
        has_predict_step = any(
            str(step.get("action") or "") == "predict" and _normalize_route_choice(step.get("route") or step.get("arg"), "") == release_route
            for step in steps
        )
        release_prediction_unconsumed = _has_active_unconsumed_prediction(release_route, observed, now)
        if current_prediction and current_prediction != release_route and prediction_unconsumed and not has_predict_step:
            release_reason = f"已有 {current_prediction} 推命未应验，暂不放行 {release_route}。"
        else:
            has_change_step = any(
                str(step.get("action") or "") == "change_fate" and _normalize_route_choice(step.get("route") or step.get("arg"), "") == release_route
                for step in steps
            )
            if (
                (active_change_route == release_route)
                or has_change_step
                or (next_consume_requires_change and next_consume_route == release_route)
            ):
                release_basis = "change_fate"
            else:
                release_basis = "prediction" if should_predict or release_prediction_unconsumed else "change_fate"
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


def _window_target_routes(windows, now, horizon_hours):
    horizon_end = float(now or 0) + float(horizon_hours or 8) * 3600
    routes = set()
    for item in windows or []:
        normalized = _normalize_tianxing_window(item, now, horizon_end)
        route = _normalize_route_choice(normalized.get("route") if normalized else "", "")
        if route:
            routes.add(route)
    return routes


def _timeline_should_replan_for_window_route(timeline, windows, now, horizon_hours):
    target_routes = _window_target_routes(windows, now, horizon_hours)
    if not target_routes:
        return False, ""
    timeline_route = _normalize_route_choice((timeline or {}).get("route"), "")
    if (
        str((timeline or {}).get("phase") or "").strip() == "prediction_conflict"
        and timeline_route
        and timeline_route not in target_routes
    ):
        return True, f"旧天星时间线为 {timeline_route} 冲突等待，新窗口为 {_format_list(sorted(target_routes))}，已重算。"
    active_step = dict((timeline or {}).get("active_step") or {})
    active_status = str(active_step.get("status") or "").strip()
    if active_status != "ack_timeout":
        return False, ""
    if int(active_step.get("send_msg_id", 0) or 0) > 0:
        return False, ""
    action = str(active_step.get("action") or "").strip()
    if action not in {"predict", "change_fate", "set_star"}:
        return False, ""
    active_route = _normalize_route_choice(active_step.get("route") or active_step.get("arg") or timeline_route, "")
    if not active_route or active_route in target_routes:
        return False, ""
    return True, f"旧天星时间线 {active_route} 发送队列超时且无消息ID，新窗口为 {_format_list(sorted(target_routes))}，已重算。"


def _timeline_should_replan_empty_block(timeline, windows, observed, config, now, horizon_hours):
    if not windows:
        return False, ""
    if dict((timeline or {}).get("active_step") or {}):
        return False, ""
    phase = str((timeline or {}).get("phase") or "").strip()
    blocked_until = float((timeline or {}).get("blocked_until", 0) or 0)
    if phase == "blocked_replan":
        if blocked_until > float(now):
            return False, ""
    elif phase in {
        "ready_prediction",
        "observe_only",
        "need_change_fate",
        "need_tianji_for_change",
        "auto_change_fate_route_forbidden",
        "change_fate_conflict",
    }:
        if list((timeline or {}).get("steps") or []):
            return False, ""
        if blocked_until <= float(now):
            return False, ""
    else:
        return False, ""
    plan = build_tianxing_timeline_plan(
        now=now,
        horizon_hours=horizon_hours,
        windows=windows or [],
        observed=observed,
        config=config,
    )
    if not plan.get("steps"):
        return False, ""
    actions = _format_list([str(step.get("action") or "") for step in plan.get("steps") or [] if isinstance(step, dict)])
    return True, f"旧天星时间线为空阻塞计划，最新状态已可执行 {actions or '后续步骤'}，立即重算。"


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
        return bool(arg) and _effective_fixed_star(observed, now) == arg
    if action == "predict":
        return (
            bool(arg)
            and str((observed or {}).get("current_prediction") or "").strip() == arg
            and _prediction_effective_until(arg, observed, now) > float(now)
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
    _close_tianxing_guards_from_observation(observed, now)
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
        plan_stage = str((plan or {}).get("stage") or "idle").strip() or "idle"
        timeline["phase"] = plan_stage
        if plan_stage in {"need_tianji_for_change", "observe_only", "change_fate_conflict"}:
            timeline["blocked_until"] = float(now)
            timeline["last_error"] = str((plan or {}).get("predict_reason") or (plan or {}).get("change_reason") or "")
        else:
            timeline["blocked_until"] = float(now + _status_backoff_sec(config))
        _timeline_audit(timeline, now, "no_steps", stage=plan_stage)
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
    calibration = _make_tianxing_timeline_step("panel", reason="天星前置命令回复超时后查盘校准。", now=now)
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


def _mark_tianxing_timeline_send_unknown(timeline, step, now, config, *, reason, event="send_unknown_calibration_wait"):
    step = dict(step or {})
    step["status"] = "ack_timeout"
    step["timeout_at"] = float(now)
    step["calibration_due_at"] = float(
        now + int(config.get("calibration_backoff_sec", TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC) or TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC)
    )
    step["last_error"] = str(reason or "天星时间线发送未确认，等待查盘校准；不重复发送。")
    timeline["phase"] = "ack_timeout"
    timeline["blocked_until"] = step["calibration_due_at"]
    timeline["last_error"] = step["last_error"]
    timeline["updated_at"] = float(now)
    _set_timeline_step(timeline, _timeline_active_index(timeline), step)
    _close_tianxing_guard_for_timeline_step(step, now, reason=event)
    _timeline_audit(timeline, now, event, action=step.get("action"), arg=step.get("arg"), reason=step["last_error"])
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

    if _defer_tianxing_timeline_step_for_phaseful_summary(timeline, step, now, action, plan.get("command") or ""):
        return timeline

    guard_next_time, guard_reason = _tianxing_action_guard_wait(plan.get("command") or "", now)
    if guard_next_time > now:
        step = dict(step or {})
        step["status"] = "pending"
        step["last_error"] = guard_reason
        step["guard_until"] = float(guard_next_time)
        timeline["phase"] = "waiting_send"
        timeline["blocked_until"] = float(guard_next_time)
        timeline["last_error"] = guard_reason
        timeline["updated_at"] = float(now)
        _set_timeline_step(timeline, _timeline_active_index(timeline), step)
        _timeline_audit(timeline, now, "action_guard_waiting", action=action, arg=arg, reason=guard_reason, next_time=guard_next_time)
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

    send_timeout = _effective_tianxing_timeline_send_timeout(config)
    try:
        msg = await send_game_command(
            plan["command"],
            track=True,
            max_retry=0,
            priority="normal",
            source_module="天星宗",
            op_id=f"tianxing-timeline-{action}-{int(now)}",
            queue_timeout=max(1, send_timeout),
        )
    except asyncio.CancelledError:
        _mark_tianxing_timeline_send_unknown(
            timeline,
            step,
            now,
            config,
            reason="天星时间线发送被外层调度取消，等待查盘校准；不重复发送。",
            event="send_cancelled",
        )
        state["tianxing_timeline_state"] = timeline
        save_state()
        raise
    step = dict(step or {})
    sent_at = float(now)
    if msg:
        parsed_sent_at, sent_at_dirty = _parse_observation_float(getattr(msg, "sent_at", 0))
        if not sent_at_dirty and parsed_sent_at > 0:
            sent_at = parsed_sent_at
    if not msg:
        send_block = get_last_game_send_block(get_current_identity_id(), plan["command"])
        if str(send_block.get("code") or "") == "send_queue_timeout":
            return _mark_tianxing_timeline_send_unknown(
                timeline,
                step,
                now,
                config,
                reason=f"天星时间线排队超过 {send_timeout}s 未发送，等待查盘校准；不重复发送。",
                event="send_queue_timeout",
            )
        return _mark_tianxing_timeline_send_unknown(
            timeline,
            step,
            now,
            config,
            reason="天星时间线发送未返回消息ID，等待查盘校准；不重复发送。",
        )

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


def is_tianxing_route_released(route, *, now=None, max_age_sec=3600, require_change_fate=False):
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
    prediction_active = _has_active_unconsumed_prediction(route, observed, now)
    prediction_fresh = prediction_active and _has_fresh_prediction_evidence(route, observed, timeline, now)
    change_active = (
        str(observed.get("current_change") or "").strip() == route
        and float(observed.get("current_change_until", 0) or 0) > now
    )
    basis = str(item.get("basis") or item.get("release_basis") or "").strip()
    if require_change_fate and basis != "change_fate":
        return False
    if basis == "prediction":
        return prediction_fresh
    if basis == "change_fate":
        return change_active and (prediction_fresh or not require_change_fate)
    if require_change_fate:
        return False
    return prediction_fresh or change_active


def _command_has_prefix(command, prefix):
    raw = str(command or "").strip()
    prefix = str(prefix or "").strip()
    return bool(prefix) and (raw == prefix or raw.startswith(f"{prefix} "))


def _command_matches_tianxing_route(route, command):
    route = _normalize_route_choice(route, "")
    if route == "探索":
        return _command_has_prefix(command, CMD_WILD_TRAINING) or _command_has_prefix(command, CMD_EXPLORE_RIFT)
    if route == "炼制":
        return _command_has_prefix(command, CMD_CRAFT)
    if route == "闭关":
        return _command_has_prefix(command, CMD_NORMAL_RETREAT)
    if route == "斗法":
        return _command_has_prefix(command, CMD_DUEL)
    return False


def _tianxing_route_has_pending_downstream(route):
    route = _normalize_route_choice(route, "")
    if route == "探索":
        return any(
            int(state.get(key, 0) or 0) > 0
            for key in (
                "wild_training_reply_to_msg_id",
                "explore_rift_reply_to_msg_id",
                "explore_rift_pending_result_msg_id",
            )
        )
    if route == "炼制":
        farm = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state")).get("craft_farm") or {}
        return str(farm.get("phase") or "").strip() in {"sent_waiting_reply", "crafting_waiting_final", "calibrating"}
    if route == "闭关":
        farm = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state")).get("retreat_farm") or {}
        return str(farm.get("phase") or "").strip() in {"sent_waiting_reply", "calibrating"}
    if route == "斗法":
        return int(state.get("duel_reply_to_msg_id", 0) or 0) > 0
    return False


def _active_tianxing_route_lease(now):
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    active_step = dict(timeline.get("active_step") or {})
    candidates = []
    active_route = _normalize_route_choice(active_step.get("route") or active_step.get("arg"), "")
    if (
        str(active_step.get("action") or "").strip() == "release_downstream"
        and str(active_step.get("status") or "").strip() == "released"
        and active_route
    ):
        candidates.append({
            "route": active_route,
            "released_at": float(active_step.get("released_at", 0) or 0),
            "reason": active_step.get("reason") or timeline.get("reason") or "",
        })
    for route, item in (timeline.get("released_routes") or {}).items():
        route = _normalize_route_choice(route, "")
        if not route or not isinstance(item, dict):
            continue
        candidates.append({
            "route": route,
            "released_at": float(item.get("released_at", 0) or 0),
            "reason": item.get("reason") or timeline.get("reason") or "",
        })
    candidates = [
        item for item in candidates
        if item["released_at"] > 0 and float(now or 0) - item["released_at"] <= TIANXING_ROUTE_LEASE_GUARD_MAX_AGE_SEC
    ]
    if not candidates:
        return {}
    return sorted(candidates, key=lambda item: item["released_at"], reverse=True)[0]


def tianxing_route_pre_send_guard(command, *, send_as_id=0, priority="", intent=None, now=None):
    now = float(now if now is not None else time.time())
    if str(priority or "").strip().lower() in {"p0", "probe"}:
        return {"allowed": True}
    send_as_id = int(send_as_id or 0)
    if send_as_id <= 0:
        return {"allowed": True}
    with use_identity(send_as_id):
        if not state.get("tianxing_enabled") or not is_module_available("天星宗"):
            return {"allowed": True}
        lease = _active_tianxing_route_lease(now)
        if not lease:
            return {"allowed": True}
        route = _normalize_route_choice(lease.get("route"), "")
        if not route or _tianxing_route_has_pending_downstream(route):
            return {"allowed": True}
        if _command_matches_tianxing_route(route, command):
            return {"allowed": True}
        return {
            "allowed": False,
            "code": f"tianxing_route_lease:{route}",
            "reason": f"天星已放行 {route}，下一条自动指令必须先交给对应路线动作；{str(command or '').strip()} 暂缓。",
        }


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

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if _close_tianxing_guards_from_observation(observed, now):
        state["tianxing_observation"] = observed
        save_state()
    if _prune_tianxing_released_routes(observed, now):
        save_state()
    if _clear_unconfirmed_timeline_step_for_observed_route_result(observed, now):
        timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
        save_state()
        return {"phase": timeline.get("phase") or "blocked_replan", "changed": True, "reason": timeline.get("last_error") or "路线结果已观察到，清理未确认前置步骤。"}

    confirmed, timeline = _confirm_tianxing_timeline_from_observation(now)
    if confirmed:
        save_state()

    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    should_replan, replan_reason = _timeline_should_replan_for_window_route(timeline, windows or [], now, horizon_hours)
    if not should_replan:
        should_replan, replan_reason = _timeline_should_replan_empty_block(
            timeline,
            windows or [],
            observed,
            effective_config,
            now,
            horizon_hours,
        )
    if should_replan:
        timeline["phase"] = "blocked_replan"
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["blocked_until"] = float(now)
        timeline["last_error"] = replan_reason
        timeline["updated_at"] = float(now)
        _timeline_audit(timeline, now, "stale_route_replan", reason=replan_reason)
        state["tianxing_timeline_state"] = timeline
        save_state()

    if float(timeline.get("blocked_until", 0) or 0) > now:
        return {"phase": timeline.get("phase") or "blocked", "changed": False, "reason": timeline.get("last_error") or "时间线等待中。"}

    active_step = dict(timeline.get("active_step") or {})
    active_status = str(active_step.get("status") or "")
    if active_status == "sending":
        started_at = float(
            active_step.get("send_started_at", 0)
            or active_step.get("sent_at", 0)
            or active_step.get("updated_at", 0)
            or timeline.get("updated_at", 0)
            or 0
        )
        ack_timeout = int(effective_config.get("ack_timeout_sec", TIANXING_TIMELINE_ACK_TIMEOUT_SEC) or TIANXING_TIMELINE_ACK_TIMEOUT_SEC)
        if started_at <= 0:
            started_at = float(now)
            active_step["send_started_at"] = started_at
            timeline["updated_at"] = float(now)
            _set_timeline_step(timeline, _timeline_active_index(timeline), active_step)
            state["tianxing_timeline_state"] = timeline
            save_state()
            return {"phase": "sending", "changed": True, "reason": "天星前置命令发送态缺少时间戳，已补记录并继续等待。"}
        if now < started_at + ack_timeout:
            return {"phase": "sending", "changed": False, "reason": "天星前置命令正在发送队列中，等待返回或真实回复。"}
        active_step["status"] = "ack_timeout"
        active_step["timeout_at"] = float(now)
        active_step["calibration_due_at"] = float(now + int(effective_config.get("calibration_backoff_sec", TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC) or TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC))
        timeline["phase"] = "ack_timeout"
        timeline["blocked_until"] = active_step["calibration_due_at"]
        timeline["last_error"] = "天星前置命令发送队列超时，等待查盘校准；不重复发送。"
        timeline["updated_at"] = float(now)
        _set_timeline_step(timeline, _timeline_active_index(timeline), active_step)
        _timeline_audit(timeline, now, "send_queue_timeout", action=active_step.get("action"), arg=active_step.get("arg"))
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": "ack_timeout", "changed": True, "reason": timeline["last_error"]}
    if active_status == "sent_waiting_ack":
        ack_due_at = float(active_step.get("ack_due_at", 0) or 0)
        if ack_due_at <= 0 or now < ack_due_at:
            return {"phase": "sent_waiting_ack", "changed": False, "reason": "等待天星前置命令真实回复。"}
        active_step["status"] = "ack_timeout"
        active_step["timeout_at"] = float(now)
        active_step["calibration_due_at"] = float(now + int(effective_config.get("calibration_backoff_sec", TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC) or TIANXING_TIMELINE_CALIBRATION_BACKOFF_SEC))
        timeline["phase"] = "ack_timeout"
        timeline["blocked_until"] = active_step["calibration_due_at"]
        timeline["last_error"] = "天星前置命令回复超时，等待查盘校准；不放行下游。"
        timeline["updated_at"] = float(now)
        _set_timeline_step(timeline, _timeline_active_index(timeline), active_step)
        _timeline_audit(timeline, now, "ack_timeout", action=active_step.get("action"), arg=active_step.get("arg"))
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": "ack_timeout", "changed": True, "reason": timeline["last_error"]}

    if active_status == "ack_timeout" and float(active_step.get("calibration_due_at", 0) or 0) <= now:
        if str(active_step.get("action") or "").strip() == "panel":
            active_step["status"] = "calibration_timeout"
            active_step["timeout_at"] = float(active_step.get("timeout_at", 0) or now)
            active_step["last_error"] = "天机盘校准回复超时，回到时间线重算；不连续查盘。"
            _set_timeline_step(timeline, _timeline_active_index(timeline), active_step)
            timeline["phase"] = "blocked_replan"
            timeline["active_step_index"] = -1
            timeline["active_step"] = {}
            timeline["blocked_until"] = float(now)
            timeline["last_error"] = active_step["last_error"]
            timeline["updated_at"] = float(now)
            _timeline_audit(timeline, now, "panel_calibration_timeout_replan", reason=timeline["last_error"])
            state["tianxing_timeline_state"] = timeline
            save_state()
            return {"phase": "blocked_replan", "changed": True, "reason": timeline["last_error"]}
        timeline = _schedule_tianxing_timeline_calibration(timeline, now)
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": "calibrating", "changed": True, "reason": "已安排 .天机盘 校准。"}

    if (timeline.get("phase") == "state_confirmed" or active_status == "confirmed") and active_step.get("terminal_after_confirm"):
        timeline["phase"] = "blocked_replan"
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["blocked_until"] = float(now)
        timeline["last_error"] = "校准已完成，原前置命令未被确认；需重算时间线，不放行下游。"
        timeline["updated_at"] = float(now)
        _timeline_audit(timeline, now, "blocked_replan", reason=timeline["last_error"])
        state["tianxing_timeline_state"] = timeline
        save_state()
        return {"phase": "blocked_replan", "changed": True, "reason": timeline["last_error"]}

    if timeline.get("phase") in {"state_confirmed", "downstream_released"} or active_status in {"confirmed", "released"}:
        next_index = _timeline_active_index(timeline) + 1
        _activate_timeline_step(timeline, next_index, now)
        next_step = dict(timeline.get("active_step") or {})
        if (
            str(next_step.get("status") or "").strip() == "pending"
            and str(next_step.get("action") or "").strip() == "release_downstream"
        ):
            timeline = await _send_tianxing_timeline_step(timeline, next_step, now, effective_config)
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


def build_tianxing_route_preflight_plan(route, *, reason="", deadline_at=0, now=None, config=None, require_change_fate=False):
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
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    route_reason = str(reason or route).strip() or route
    if is_tianxing_automation_paused(now=now, observed=observed):
        return _route_preflight_result(
            route,
            "automation_paused",
            False,
            f"天星自动调度已暂停，为避免逆命，本轮不发送{route_reason}；需要手动处理或使用 .天星恢复 @身份。",
            deadline_at=deadline_at,
            now=now,
            blocked_until=_tianxing_pause_block_until(now, observed=observed, config=effective_config),
        )

    current_prediction = str(observed.get("current_prediction") or "").strip()
    prediction_until = float(observed.get("current_prediction_until", 0) or 0)
    prediction_unconsumed = _has_active_unconsumed_prediction(current_prediction, observed, now) if current_prediction else False
    if current_prediction and current_prediction != route and prediction_unconsumed:
        if prediction_until > now:
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

    current_change = str(observed.get("current_change") or "").strip()
    change_until = float(observed.get("current_change_until", 0) or 0)
    if not effective_config.get("timeline_enabled"):
        return _route_preflight_result(route, "timeline_disabled", True, "天星时间线未开启，路线动作不等待天星预检。", deadline_at=deadline_at, now=now)

    if require_change_fate and current_change == route and change_until > now and prediction_unconsumed:
        if not _has_fresh_prediction_evidence(route, observed, timeline, now):
            return _route_preflight_result(
                route,
                "timeline_waiting_fresh_prediction",
                False,
                f"{route_reason} 已有 {route} 改命待发，但推命确认已过短租约，需重新推命后再放行。",
                deadline_at=deadline_at,
                now=now,
                timeline_required=True,
            )
        return _route_preflight_result(
            route,
            "change_fate_active",
            True,
            f"{route_reason} 已有未消费 {route} 推命与 {route} 改命待发，允许直接消耗。",
            deadline_at=deadline_at,
            now=now,
        )

    if is_tianxing_route_released(route, now=now, require_change_fate=bool(require_change_fate)):
        return _route_preflight_result(route, "timeline_released", True, f"{route_reason} 已获天星时间线确认放行。", deadline_at=deadline_at, now=now)

    if require_change_fate:
        return _route_preflight_result(
            route,
            "timeline_waiting_change_fate",
            False,
            f"{route_reason} 需等待天星时间线确认 {route} 改命后放行。",
            deadline_at=deadline_at,
            now=now,
            timeline_required=True,
        )

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
                farm["next_time"] = float(now + _current_craft_farm_interval_sec(config, now))
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
        return _retreat_farm_result("route_not_retreat", reason="当前攒天机路线不是闭关。")

    windows = build_tianxing_farm_window(now=now, config=config, reason="天星普通闭关攒点")
    if not windows:
        return _retreat_farm_result("outside_window", reason="当前不在闭关攒天机窗口。")

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
    if farm_phase == "phaseful_deferred" and next_time > now:
        return _retreat_farm_result(
            "waiting_phaseful_deferred",
            active=True,
            takeover=False,
            handoff=True,
            reason=farm.get("last_error") or "闭关/元婴结算窗口内，普通闭关攒点延后。",
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

    if plan.get("stage") in {"waiting_prediction_conflict", "waiting_timeline", "waiting_phaseful_deferred"}:
        if plan.get("stage") == "waiting_prediction_conflict":
            farm["phase"] = "prediction_conflict"
        elif plan.get("stage") == "waiting_phaseful_deferred":
            farm["phase"] = "phaseful_deferred"
        else:
            farm["phase"] = "timeline_waiting"
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
        return dict(plan, timeline_phase=timeline_result.get("phase") or "", timeline_reason=timeline_result.get("reason") or "", next_time=farm["next_time"])

    command = str(plan.get("command") or "")
    if not command:
        farm["phase"] = "waiting"
        _retreat_farm_audit(farm, now, "waiting", stage=plan.get("stage"), reason=plan.get("reason"))
        _set_tianxing_retreat_farm_state(farm, now)
        save_state()
        return plan

    source_module = "深度闭关" if command == CMD_DEEP_RETREAT_FORCE_EXIT else "天星宗"
    priority = "chain" if _is_tianxing_retreat_chain_command(command) else "normal"
    payload = _defer_tianxing_farm_for_phaseful_summary(
        farm,
        now,
        kind="retreat",
        action=plan.get("action") or plan.get("stage") or "普通闭关攒点",
        command=command,
    )
    if payload:
        return dict(plan, stage="phaseful_deferred", reason=payload["error"], next_time=payload["next_time"])

    guard_next_time, guard_reason = _tianxing_action_guard_wait(command, now)
    if guard_next_time > now:
        farm["phase"] = "ready"
        farm["last_command"] = ""
        farm["last_result"] = "action_guard_waiting"
        farm["last_error"] = guard_reason
        farm["next_time"] = float(guard_next_time)
        _retreat_farm_audit(farm, now, "action_guard_waiting", command=command, reason=guard_reason, next_time=farm["next_time"])
        _set_tianxing_retreat_farm_state(farm, now)
        save_state()
        return dict(plan, stage="action_guard_waiting", command="", reason=guard_reason, next_time=farm["next_time"])

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


def _craft_farm_result(stage, *, active=False, takeover=False, handoff=True, reason="", action="", command="", next_time=0, timeline_required=False, dry_run=False, **extra):
    result = {
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
    result.update(extra)
    return result


def _craft_farm_command(config):
    item = str((config or {}).get("craft_farm_item") or "玄铁剑").strip() or "玄铁剑"
    return f"{CMD_CRAFT} {item}", item


def _has_active_craft_prediction(now, observed=None):
    observed = normalize_tianxing_observation(observed if observed is not None else state.get("tianxing_observation"))
    current_prediction = _normalize_route_choice(observed.get("current_prediction"), "")
    prediction_until = float(observed.get("current_prediction_until", 0) or 0)
    return (
        current_prediction == "炼制"
        and prediction_until > float(now or time.time())
        and not _is_prediction_consumed("炼制", observed, now)
    )


async def run_tianxing_consume_craft_prediction(now, *, reason="", config=None):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    if not state.get("tianxing_enabled"):
        return _craft_farm_result("disabled", reason="天星宗模块未开启。")
    if not is_module_available("天星宗"):
        return _craft_farm_result("unavailable", reason="当前身份不是天星宗。")
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if not _has_active_craft_prediction(now, observed):
        return _craft_farm_result("no_active_craft_prediction", reason="当前没有可消费的炼制推命。")

    farm = _current_craft_farm_state()
    farm_phase = str(farm.get("phase") or "").strip()
    farm_next_time = float(farm.get("next_time", 0) or 0)
    craft_command, item = _craft_farm_command(config)
    if farm_phase == "send_blocked" and farm_next_time > now:
        return _craft_farm_result(
            "send_blocked_waiting",
            active=True,
            takeover=False,
            handoff=True,
            reason=farm.get("last_error") or "炼制推命消费已有等待窗口，暂不重复炼制。",
            action=farm.get("last_action") or farm_phase,
            command=farm.get("last_command") or craft_command,
            next_time=farm_next_time,
        )
    if farm_phase in {"sent_waiting_reply", "crafting_waiting_final", "calibrating"} and farm_next_time > now:
        return _craft_farm_result(
            "waiting_reply",
            active=True,
            takeover=False,
            handoff=True,
            reason="炼制推命消费已发送，等待炼制回复或查盘校准。",
            next_time=farm_next_time,
        )
    if farm_phase == "phaseful_deferred" and farm_next_time > now:
        return _craft_farm_result(
            "phaseful_deferred",
            active=True,
            takeover=True,
            handoff=True,
            reason=farm.get("last_error") or "闭关/元婴结算窗口内，先炼制消费推命延后。",
            next_time=farm_next_time,
        )
    should_calibrate = farm_phase in {"sent_waiting_reply", "crafting_waiting_final", "calibrating"} and farm_next_time <= now
    if (
        farm_phase == "send_blocked"
        and farm_next_time <= now
        and str(farm.get("last_action") or "") in {"consume_craft_prediction", "consume_craft_prediction_calibration"}
        and str(farm.get("last_command") or "") in {craft_command, CMD_TIANXING_PANEL}
    ):
        should_calibrate = True
    if should_calibrate:
        command = CMD_TIANXING_PANEL
        if not farm.get("started_at"):
            farm["started_at"] = float(now)
            farm["start_tianji"] = int(observed.get("tianji_value", 0) or 0)
            farm["estimated_tianji"] = int(observed.get("tianji_value", 0) or 0)
        farm["target_tianji"] = int(config.get("target_tianji_daily", 0) or 0)
        farm["daily_limit"] = int(config.get("craft_farm_daily_limit", 0) or 0)
        farm["last_action"] = "consume_craft_prediction_calibration"
        farm["last_command"] = command
        farm["last_error"] = "炼制推命消费回复超时，查盘校准；不重复炼制。"
        farm["handoff_ready"] = True
        payload = _defer_tianxing_farm_for_phaseful_summary(
            farm,
            now,
            kind="craft",
            action="消费炼制推命校准",
            command=command,
        )
        if payload:
            return _craft_farm_result(
                "phaseful_deferred",
                active=True,
                takeover=True,
                handoff=True,
                reason=payload["error"],
                action="consume_craft_prediction_calibration",
                command=command,
                next_time=payload["next_time"],
            )

        guard_next_time, guard_reason = _tianxing_action_guard_wait(command, now)
        if guard_next_time > now:
            farm["phase"] = "ready"
            farm["last_command"] = ""
            farm["last_result"] = "action_guard_waiting"
            farm["last_error"] = guard_reason
            farm["next_time"] = float(guard_next_time)
            _craft_farm_audit(
                farm,
                now,
                "consume_craft_prediction_calibration_guard_wait",
                command=command,
                reason=guard_reason,
                next_time=farm["next_time"],
            )
            _set_tianxing_craft_farm_state(farm, now)
            save_state()
            return _craft_farm_result(
                "action_guard_waiting",
                active=True,
                takeover=False,
                handoff=True,
                reason=guard_reason,
                action="consume_craft_prediction_calibration",
                command="",
                next_time=farm["next_time"],
            )

        msg = await send_game_command(
            command,
            track=True,
            max_retry=0,
            priority="normal",
            source_module="天星宗",
            op_id=f"tianxing-consume-craft-calibration-{int(now)}",
        )
        if not msg:
            farm["phase"] = "send_blocked"
            farm["next_time"] = float(now + _craft_farm_interval_sec(config))
            farm["last_error"] = f"{command} 发送失败或被安全策略拦截。"
            _craft_farm_audit(farm, now, "consume_craft_prediction_calibration_blocked", command=command, reason=reason)
            _set_tianxing_craft_farm_state(farm, now)
            save_state()
            return _craft_farm_result(
                "send_blocked",
                active=True,
                takeover=False,
                handoff=True,
                reason=farm["last_error"],
                action="consume_craft_prediction_calibration",
                command=command,
                next_time=farm["next_time"],
            )

        sent_at = float(getattr(msg, "sent_at", 0) or now)
        farm["phase"] = "calibrating"
        farm["last_msg_id"] = int(getattr(msg, "id", 0) or 0)
        farm["next_time"] = float(sent_at + int(config.get("craft_farm_reply_timeout_sec", TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC) or TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC))
        farm["last_result"] = "waiting_calibration"
        farm["last_error"] = "炼制推命消费回复超时，查盘校准；不重复炼制。"
        _craft_farm_audit(farm, sent_at, "consume_craft_prediction_calibration_sent", command=command, msg_id=farm["last_msg_id"], reason=reason)
        _set_tianxing_craft_farm_state(farm, sent_at)
        save_state()
        return _craft_farm_result(
            "waiting_calibration",
            active=True,
            takeover=True,
            handoff=True,
            reason=farm["last_error"],
            action="consume_craft_prediction_calibration",
            command=command,
            next_time=farm["next_time"],
            msg_id=farm["last_msg_id"],
        )

    command = craft_command
    if not farm.get("started_at"):
        farm["started_at"] = float(now)
        farm["start_tianji"] = int(observed.get("tianji_value", 0) or 0)
        farm["estimated_tianji"] = int(observed.get("tianji_value", 0) or 0)
    farm["target_tianji"] = int(config.get("target_tianji_daily", 0) or 0)
    farm["daily_limit"] = int(config.get("craft_farm_daily_limit", 0) or 0)
    farm["last_action"] = "consume_craft_prediction"
    farm["last_command"] = command
    farm["last_item"] = item
    farm["last_error"] = ""
    farm["handoff_ready"] = True

    if config.get("craft_farm_dry_run_enabled"):
        farm["phase"] = "dry_run"
        farm["last_result"] = "consume_craft_prediction_dry_run"
        farm["next_time"] = float(now + _craft_farm_interval_sec(config))
        _craft_farm_audit(farm, now, "consume_craft_prediction_dry_run", command=command, reason=reason)
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return _craft_farm_result(
            "dry_run",
            active=True,
            takeover=False,
            handoff=True,
            reason="试运行：检测到炼制推命阻断探索，只记录不发送炼制。",
            action="consume_craft_prediction",
            command=command,
            next_time=farm["next_time"],
            dry_run=True,
        )

    payload = _defer_tianxing_farm_for_phaseful_summary(
        farm,
        now,
        kind="craft",
        action="消费炼制推命",
        command=command,
    )
    if payload:
        return _craft_farm_result(
            "phaseful_deferred",
            active=True,
            takeover=True,
            handoff=True,
            reason=payload["error"],
            action="consume_craft_prediction",
            command=command,
            next_time=payload["next_time"],
        )

    guard_next_time, guard_reason = _tianxing_action_guard_wait(command, now)
    if guard_next_time > now:
        farm["phase"] = "ready"
        farm["last_command"] = ""
        farm["last_result"] = "action_guard_waiting"
        farm["last_error"] = guard_reason
        farm["next_time"] = float(guard_next_time)
        _craft_farm_audit(
            farm,
            now,
            "consume_craft_prediction_guard_wait",
            command=command,
            reason=guard_reason,
            next_time=farm["next_time"],
        )
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return _craft_farm_result(
            "action_guard_waiting",
            active=True,
            takeover=False,
            handoff=True,
            reason=guard_reason,
            action="consume_craft_prediction",
            command="",
            next_time=farm["next_time"],
        )

    msg = await send_game_command(
        command,
        track=True,
        max_retry=0,
        priority="normal",
        source_module="天星宗",
        op_id=f"tianxing-consume-craft-prediction-{int(now)}",
    )
    if not msg:
        farm["phase"] = "send_blocked"
        farm["next_time"] = float(now + _craft_farm_interval_sec(config))
        farm["last_error"] = f"{command} 发送失败或被安全策略拦截。"
        _craft_farm_audit(farm, now, "consume_craft_prediction_blocked", command=command, reason=reason)
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return _craft_farm_result(
            "send_blocked",
            active=True,
            takeover=False,
            handoff=True,
            reason=farm["last_error"],
            action="consume_craft_prediction",
            command=command,
            next_time=farm["next_time"],
        )

    sent_at = float(getattr(msg, "sent_at", 0) or now)
    farm["phase"] = "sent_waiting_reply"
    farm["last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    farm["next_time"] = float(sent_at + int(config.get("craft_farm_reply_timeout_sec", TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC) or TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC))
    farm["last_result"] = "sent_waiting_reply"
    farm["last_error"] = ""
    _craft_farm_audit(farm, sent_at, "consume_craft_prediction_sent", command=command, msg_id=farm["last_msg_id"], reason=reason)
    _set_tianxing_craft_farm_state(farm, sent_at)
    save_state()
    return _craft_farm_result(
        "sent_waiting_reply",
        active=True,
        takeover=True,
        handoff=True,
        reason=reason or "已有炼制推命阻断探索，已先发送炼制消费推命。",
        action="consume_craft_prediction",
        command=command,
        next_time=farm["next_time"],
        msg_id=farm["last_msg_id"],
    )


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


def _craft_farm_explore_module_specs():
    return (
        ("野外历练", "wild_training_enabled", "next_wild_training_time", ("wild_training_reply_to_msg_id",), "wild_training_reply_due_at"),
        (
            "探寻裂缝",
            "explore_rift_enabled",
            "next_explore_rift_time",
            ("explore_rift_reply_to_msg_id", "explore_rift_pending_result_msg_id", "explore_rift_fatal_msg_id"),
            "explore_rift_reply_due_at",
        ),
    )


def _has_explore_consume_timer():
    for _label, enabled_key, next_key, pending_keys, _due_key in _craft_farm_explore_module_specs():
        if not state.get(enabled_key):
            continue
        if _state_float(next_key) > 0:
            return True
        if any(_state_int(key) > 0 for key in pending_keys):
            return True
    return False


def _craft_farm_stale_consume_wait_should_wake(craft_farm, now, config):
    craft_farm = normalize_tianxing_craft_farm_state(craft_farm)
    if str(craft_farm.get("phase") or "").strip() != "waiting":
        return False
    if str(craft_farm.get("last_action") or "").strip() != "waiting_consume_window":
        return False
    if not _has_explore_consume_timer():
        return False
    return not bool(_craft_farm_explore_consume_block(now, config))


def _craft_farm_explore_consume_block(now, config):
    now = float(now or 0)
    lead_sec = int((config or {}).get("route_prepare_lead_sec", 5 * 60) or 5 * 60)
    interval_sec = _craft_interval_bounds(config)[0]
    reply_timeout_sec = int((config or {}).get("craft_farm_reply_timeout_sec", TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC) or TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC)
    lead_sec = max(
        lead_sec,
        10 * 60,
        interval_sec + reply_timeout_sec + TIANXING_CRAFT_FARM_CALIBRATION_DELAY_SEC + 2 * TIANXING_TIME_BUFFER_SEC,
    )
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    current_change = _normalize_route_choice(observed.get("current_change"), "")
    change_until = float(observed.get("current_change_until", 0) or 0)
    tianji_value = int(observed.get("tianji_value", 0) or 0)
    min_tianji = int((config or {}).get("min_tianji_for_change", 6) or 6)
    candidates = []
    for label, enabled_key, next_key, pending_keys, due_key in _craft_farm_explore_module_specs():
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
        if due_at <= now + lead_sec:
            if current_change != "探索" and tianji_value < min_tianji:
                continue
            block_until = max(now + interval_sec, due_at + TIANXING_TIME_BUFFER_SEC)
            candidates.append((block_until, f"{label}探索消费窗口临近（{fmt_abs_ts(due_at)}），炼制攒点让路。"))
            continue
    if not candidates:
        return {}
    block_until, reason = sorted(candidates, key=lambda item: item[0])[0]
    return {"blocked_until": float(block_until), "reason": reason}


def _craft_farm_unpredicted_override_reason(now, config, observed, estimated_tianji):
    observed = normalize_tianxing_observation(observed)
    config = normalize_tianxing_auto_config(config)
    min_tianji = int(config.get("min_tianji_for_change", 3) or 3)
    current_prediction = _normalize_route_choice(observed.get("current_prediction"), "")
    prediction_until = float(observed.get("current_prediction_until", 0) or 0)
    if not current_prediction or current_prediction == "炼制" or prediction_until <= now:
        return ""
    if current_prediction == "闭关":
        return ""
    current_change = _normalize_route_choice(observed.get("current_change"), "")
    change_until = float(observed.get("current_change_until", 0) or 0)
    if current_change and change_until > now and current_change != "探索":
        return ""
    if min_tianji > 0 and int(estimated_tianji or 0) < min_tianji:
        return (
            f"天机值 {int(estimated_tianji or 0)} 低于改命阈值 {min_tianji}；"
            f"已有 {current_prediction} 推命未应验，本轮允许裸炼制补点并承担逆命风险。"
        )
    return (
        f"已有 {current_prediction} 推命未应验；炼制攒点目标未完成，"
        "不再发送新的炼制推命，直接裸炼制切换路线并承担逆命风险。"
    )


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
        return _craft_farm_result("route_not_craft", reason="当前攒天机路线不是炼制。")
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if is_tianxing_automation_paused(now=now, observed=observed):
        return _craft_farm_result(
            "automation_paused",
            active=True,
            takeover=False,
            handoff=True,
            reason="天星自动调度已暂停，炼制攒点不接管。",
            next_time=_tianxing_pause_block_until(now, observed=observed, config=config),
        )

    windows, off_window_active = _build_tianxing_craft_farm_windows(now, config, reason="天星炼制攒点")
    interval_sec = lambda: _craft_farm_interval_sec(config, off_window=off_window_active)
    target_tianji = int(config.get("target_tianji_daily", 0) or 0)
    current_tianji = int(observed.get("tianji_value", 0) or 0)
    farm = _current_craft_farm_state()
    estimated_tianji = max(current_tianji, int(farm.get("estimated_tianji", 0) or 0))
    next_time = float(farm.get("next_time", 0) or 0)
    farm_phase = str(farm.get("phase") or "").strip()
    unpredicted_override_reason = _craft_farm_unpredicted_override_reason(now, config, observed, estimated_tianji)
    daily_limit = int(config.get("craft_farm_daily_limit", 0) or 0)
    if target_tianji <= 0:
        return _craft_farm_result("target_disabled", active=True, reason="日目标天机为 0，不主动炼制攒点。", next_time=now + _status_backoff_sec(config))
    if estimated_tianji >= target_tianji:
        return _craft_farm_result("target_reached", active=True, reason=f"天机值 {estimated_tianji} 已达到目标 {target_tianji}。", next_time=now + _status_backoff_sec(config))
    if daily_limit > 0 and int(farm.get("daily_count", 0) or 0) >= daily_limit:
        return _craft_farm_result("daily_limit_reached", active=True, reason=f"炼制攒点今日已达 {daily_limit} 轮。", next_time=now + _status_backoff_sec(config))

    if farm_phase == "send_blocked":
        if next_time > now:
            return _craft_farm_result(
                "send_blocked_waiting",
                active=True,
                takeover=False,
                handoff=True,
                reason=farm.get("last_error") or "炼制攒点发送被拦截，等待短重试窗口。",
                next_time=next_time,
            )
        return _craft_farm_result(
            "calibrate_panel",
            active=True,
            takeover=not bool(config.get("craft_farm_dry_run_enabled")),
            handoff=bool(config.get("craft_farm_dry_run_enabled")),
            reason="炼制攒点发送被拦截后已到重试点，先查盘校准；不重复炼制。",
            action="panel",
            command=CMD_TIANXING_PANEL,
            next_time=now + int(config.get("craft_farm_reply_timeout_sec", TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC) or TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC),
            dry_run=bool(config.get("craft_farm_dry_run_enabled")),
        )

    explore_block = _craft_farm_explore_consume_block(now, config)
    if explore_block:
        return _craft_farm_result(
            "waiting_consume_window",
            active=True,
            takeover=False,
            handoff=True,
            reason=explore_block.get("reason") or "探索消费窗口临近，炼制攒点让路。",
            next_time=explore_block.get("blocked_until") or now + interval_sec(),
        )

    if not windows:
        next_window = next_tianxing_farm_window_start(now=now, config=config)
        return _craft_farm_result(
            "outside_window",
            active=True,
            reason="当前不在炼制攒天机窗口，窗口外低频炼制未开启。",
            next_time=next_window or now + _status_backoff_sec(config),
        )

    dry_run = bool(config.get("craft_farm_dry_run_enabled"))
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    if timeline.get("phase") == "prediction_conflict" and float(timeline.get("blocked_until", 0) or 0) > now:
        current_prediction = _normalize_route_choice(observed.get("current_prediction"), "")
        prediction_until = float(observed.get("current_prediction_until", 0) or 0)
        if current_prediction and prediction_until > now and not unpredicted_override_reason:
            if config.get("consume_conflicting_prediction_enabled") and current_prediction == "闭关":
                return _craft_farm_result(
                    "consume_conflicting_prediction",
                    active=True,
                    takeover=not dry_run,
                    handoff=dry_run,
                    reason="已有闭关推命未应验；先按闭关路线消费该推命，再回到炼制攒点。",
                    action="consume_prediction",
                    next_time=now + interval_sec(),
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
    if farm_phase in {"prediction_conflict", "timeline_waiting"} and next_time > now and not unpredicted_override_reason:
        return _craft_farm_result(
            "waiting_prediction_conflict" if farm_phase == "prediction_conflict" else "waiting_timeline",
            active=True,
            takeover=False,
            handoff=True,
            reason=farm.get("last_error") or "炼制攒点等待天星时间线确认。",
            next_time=next_time,
            dry_run=dry_run,
        )
    if farm_phase == "phaseful_deferred" and next_time > now:
        return _craft_farm_result(
            "waiting_phaseful_deferred",
            active=True,
            takeover=False,
            handoff=True,
            reason=farm.get("last_error") or "闭关/元婴结算窗口内，炼制攒点延后。",
            next_time=next_time,
            dry_run=dry_run,
        )
    if farm_phase == "ready" and next_time > now:
        return _craft_farm_result(
            "waiting_interval",
            active=True,
            takeover=False,
            handoff=True,
            reason="炼制攒点等待下一次间隔。",
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
        if preflight.get("stage") == "prediction_conflict" and unpredicted_override_reason:
            command, item = _craft_farm_command(config)
            return _craft_farm_result(
                "send_craft_unpredicted",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason=unpredicted_override_reason,
                action="craft",
                command=command,
                next_time=now + interval_sec(),
                dry_run=dry_run,
                allow_prediction_conflict=True,
                item=item,
            )
        if preflight.get("timeline_required"):
            return _craft_farm_result(
                "timeline_required",
                active=True,
                takeover=not dry_run,
                handoff=dry_run,
                reason=preflight.get("reason") or "等待天星时间线确认炼制路线。",
                action="timeline",
                next_time=now + interval_sec(),
                timeline_required=True,
                dry_run=dry_run,
            )
        return _craft_farm_result(
            preflight.get("stage") or "preflight_blocked",
            active=True,
            takeover=False,
            handoff=True,
            reason=preflight.get("reason") or "天星预检阻断炼制攒点。",
            next_time=preflight.get("blocked_until") or now + interval_sec(),
            dry_run=dry_run,
        )

    command, item = _craft_farm_command(config)
    return _craft_farm_result(
        "send_craft",
        active=True,
        takeover=not dry_run,
        handoff=dry_run,
        reason=(
            f"炼制路线已确认，发送炼制 {item} 获取天机点。"
            if not off_window_active
            else f"窗口外低频补点，发送炼制 {item} 获取天机点。"
        ),
        action="craft",
        command=command,
        next_time=now + interval_sec(),
        dry_run=dry_run,
        allow_prediction_conflict=False,
        off_window=off_window_active,
    )


async def run_tianxing_craft_farm_scheduler(now, *, config=None):
    now = float(now if now is not None else time.time())
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    plan_windows, run_off_window_active = _build_tianxing_craft_farm_windows(now, config, reason="天星炼制攒点")
    interval_sec = lambda: _craft_farm_interval_sec(config, off_window=run_off_window_active)
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
        farm["next_time"] = float(consume_result.get("next_time", 0) or now + interval_sec())
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

    if plan.get("stage") in {"waiting_prediction_conflict", "waiting_timeline", "waiting_phaseful_deferred"}:
        if plan.get("stage") == "waiting_prediction_conflict":
            farm["phase"] = "prediction_conflict"
        elif plan.get("stage") == "waiting_phaseful_deferred":
            farm["phase"] = "phaseful_deferred"
        else:
            farm["phase"] = "timeline_waiting"
        farm["last_result"] = plan.get("stage") or ""
        _craft_farm_audit(farm, now, farm["phase"], reason=plan.get("reason"))
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return plan

    if plan.get("timeline_required"):
        timeline_result = await run_tianxing_timeline_scheduler(
            now,
            windows=plan_windows,
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
        timeline_phase = str(timeline_result.get("phase") or current_timeline.get("phase") or "").strip()
        active_step = current_timeline.get("active_step") or {}
        followup_at = 0.0
        if timeline_phase in {"sending", "sent_waiting_ack"}:
            followup_at = float(active_step.get("ack_due_at", 0) or 0)
        elif timeline_phase in {"ack_timeout", "calibrating"}:
            followup_at = float(active_step.get("calibration_due_at", 0) or current_timeline.get("blocked_until", 0) or 0)
        if followup_at > now:
            farm["next_time"] = float(followup_at)
        else:
            followup_sec = (
                TIANXING_CRAFT_FARM_RETRY_SEC
                if timeline_phase in {"state_confirmed", "downstream_released", "waiting_send", "calibrating", "blocked_replan", "completed"}
                else interval_sec()
            )
            farm["next_time"] = float(now + followup_sec)
        _craft_farm_audit(farm, now, "timeline_waiting", phase=timeline_result.get("phase"), reason=timeline_result.get("reason"))
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return dict(plan, timeline_phase=timeline_result.get("phase") or "", timeline_reason=timeline_result.get("reason") or "", next_time=farm["next_time"])

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

    if str(plan.get("action") or "") == "craft":
        lease = _active_tianxing_route_lease(now)
        lease_route = _normalize_route_choice((lease or {}).get("route"), "")
        if lease_route and lease_route != "炼制":
            farm["phase"] = "waiting_consume_window"
            farm["last_command"] = ""
            farm["last_result"] = "route_lease_waiting"
            farm["last_error"] = f"天星已放行 {lease_route}，炼制攒点让路等待下游消费。"
            farm["next_time"] = float(now + min(60, interval_sec()))
            _craft_farm_audit(farm, now, "route_lease_waiting", route=lease_route, reason=farm["last_error"])
            _set_tianxing_craft_farm_state(farm, now)
            save_state()
            return dict(plan, stage="route_lease_waiting", command="", reason=farm["last_error"], next_time=farm["next_time"])

        final_preflight = build_tianxing_route_preflight_plan("炼制", reason="天星炼制攒点发送前复核", now=now, config=config)
        if (
            not final_preflight.get("route_allowed")
            and not (
                plan.get("allow_prediction_conflict")
                and final_preflight.get("stage") == "prediction_conflict"
            )
        ):
            farm["phase"] = "timeline_waiting" if final_preflight.get("timeline_required") else "prediction_conflict"
            farm["last_command"] = ""
            farm["last_result"] = str(final_preflight.get("stage") or "final_preflight_blocked")
            farm["last_error"] = final_preflight.get("reason") or "发送前路线复核未通过。"
            farm["next_time"] = float(final_preflight.get("blocked_until") or now + interval_sec())
            _craft_farm_audit(farm, now, "final_preflight_blocked", stage=final_preflight.get("stage"), reason=farm["last_error"])
            _set_tianxing_craft_farm_state(farm, now)
            save_state()
            return dict(plan, stage="final_preflight_blocked", command="", reason=farm["last_error"], next_time=farm["next_time"])

    payload = _defer_tianxing_farm_for_phaseful_summary(
        farm,
        now,
        kind="craft",
        action=plan.get("action") or plan.get("stage") or "炼制攒点",
        command=command,
    )
    if payload:
        return dict(plan, stage="phaseful_deferred", reason=payload["error"], next_time=payload["next_time"])

    guard_next_time, guard_reason = _tianxing_action_guard_wait(command, now)
    if guard_next_time > now:
        farm["phase"] = "ready"
        farm["last_command"] = ""
        farm["last_result"] = "action_guard_waiting"
        farm["last_error"] = guard_reason
        farm["next_time"] = float(guard_next_time)
        _craft_farm_audit(farm, now, "action_guard_waiting", command=command, reason=guard_reason, next_time=farm["next_time"])
        _set_tianxing_craft_farm_state(farm, now)
        save_state()
        return dict(plan, stage="action_guard_waiting", command="", reason=guard_reason, next_time=farm["next_time"])

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
        farm["next_time"] = float(now + interval_sec())
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
    observed["auto_last_error"] = "试运行：只记录，不发送。"
    observed["auto_last_error_at"] = float(now)
    observed["auto_last_plan"] = command or plan.get("reason") or ""
    observed["auto_last_plan_at"] = float(now)
    observed["auto_next_time"] = float(now + _status_backoff_sec(config))
    state["tianxing_observation"] = observed
    save_state()


def _build_tianxing_daily_plan(observed, config, now):
    observed = normalize_tianxing_observation(observed)
    config = normalize_tianxing_auto_config(config)
    today_key = get_day_key(now)
    if (
        config.get("daily_observe_enabled")
        and config.get("auto_observe_enabled")
        and not _has_available_stars_today(observed, now)
    ):
        plan = build_tianxing_manual_plan("observe", now=now)
        if plan.get("allowed"):
            plan["daily_bootstrap"] = True
        return plan

    available_stars = _available_stars_for_day(observed, now)
    desired_star = _choose_daily_star(available_stars, config)
    if (
        config.get("daily_set_star_enabled")
        and config.get("auto_set_star_enabled")
        and str(observed.get("available_stars_day") or "").strip() == today_key
        and (
            str(observed.get("fixed_star_day") or "").strip() != today_key
            or _should_correct_daily_fixed_star(observed, desired_star, now)
        )
    ):
        star = desired_star
        if star:
            plan = build_tianxing_manual_plan("set_star", star, now=now)
            if plan.get("allowed"):
                plan["daily_bootstrap"] = True
            return plan
    return {}


def _is_daily_bootstrap_action(action):
    return str(action or "").strip() in {"observe", "set_star"}


def _daily_bootstrap_retry_until(observed, now):
    observed = observed if isinstance(observed, dict) else {}
    error_at = float(observed.get("auto_last_error_at", 0) or 0)
    if error_at <= 0:
        return 0.0
    return float(error_at + TIANXING_DAILY_BOOTSTRAP_RETRY_SEC)


def _daily_plan_should_wait_for_backoff(plan, observed, now):
    if not (plan or {}).get("allowed"):
        return False
    action = str((plan or {}).get("action") or "").strip()
    if action not in {"observe", "set_star", "predict", "change_fate"}:
        return False
    auto_next_time = float((observed or {}).get("auto_next_time", 0) or 0)
    if auto_next_time <= now:
        return False
    last_action = str((observed or {}).get("auto_last_action") or "").strip()
    last_error = str((observed or {}).get("auto_last_error") or "").strip()
    if last_action != action or not last_error:
        return False
    if _is_daily_bootstrap_action(action) and (plan or {}).get("daily_bootstrap"):
        retry_until = _daily_bootstrap_retry_until(observed, now)
        return retry_until <= 0 or now < retry_until
    return True


def _tianxing_send_fail_backoff_sec(action, observed, now):
    if _is_daily_bootstrap_action(action):
        today_key = get_day_key(now)
        if action == "observe" and not _has_available_stars_today(observed, now):
            return TIANXING_DAILY_BOOTSTRAP_RETRY_SEC
        if action == "set_star":
            fixed_day = str((observed or {}).get("fixed_star_day") or "").strip()
            if fixed_day != today_key:
                return TIANXING_DAILY_BOOTSTRAP_RETRY_SEC
            if _in_daily_star_correction_window(now) and _has_available_stars_today(observed, now):
                return TIANXING_DAILY_BOOTSTRAP_RETRY_SEC
    return TIANXING_AUTO_SEND_FAIL_BACKOFF_SEC


def _tianxing_auto_send_priority(plan):
    if (plan or {}).get("daily_bootstrap") and _is_daily_bootstrap_action((plan or {}).get("action")):
        return "reactive"
    return "normal"


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
    if is_tianxing_automation_paused(now=now, observed=observed):
        _apply_tianxing_pause_wait(observed, now, config=config)
        return
    if _handle_tianxing_auto_pending(observed, now):
        return
    daily_plan = _build_tianxing_daily_plan(observed, config, now)
    auto_next_time = float(observed.get("auto_next_time", 0) or 0)
    if auto_next_time > 0 and now < auto_next_time:
        if _daily_plan_should_wait_for_backoff(daily_plan, observed, now):
            return
        if not daily_plan and not _should_wake_tianxing_timeline(observed, config, now):
            return

    calamity_count = int(observed.get("calamity_count", 0) or 0)
    calamity_threshold = int(config.get("min_calamity_to_clear", 1) or 1)
    if daily_plan:
        plan = daily_plan
    elif not _has_recent_observation(observed, now) and config.get("auto_panel_enabled"):
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
                f"逆命劫 {calamity_count} 已达阈值 {calamity_threshold}，自动消劫关闭，暂停天星命令。",
            )
            return
    elif (
        not _effective_fixed_star(observed, now)
        and str(observed.get("available_stars_source") or "").strip() != "observe"
        and config.get("auto_observe_enabled")
    ):
        plan = build_tianxing_manual_plan("observe", now=now)
    else:
        timeline_result = await _drain_existing_tianxing_timeline(now, config)
        if timeline_result.get("active"):
            observed = normalize_tianxing_observation(state.get("tianxing_observation"))
            observed["auto_last_action"] = "timeline"
            observed["auto_last_error"] = timeline_result.get("reason") or ""
            observed["auto_last_error_at"] = float(now) if observed["auto_last_error"] else 0
            observed["auto_last_plan"] = timeline_result.get("timeline_phase") or timeline_result.get("phase") or ""
            observed["auto_last_plan_at"] = float(now)
            observed["auto_next_time"] = float(timeline_result.get("next_time", 0) or now + min(60, _craft_farm_interval_sec(config)))
            state["tianxing_observation"] = observed
            save_state()
            if _timeline_has_existing_work(now):
                return

        craft_result = await run_tianxing_craft_farm_scheduler(now, config=config)
        if craft_result.get("active"):
            observed = normalize_tianxing_observation(state.get("tianxing_observation"))
            observed["auto_last_action"] = "craft_farm"
            observed["auto_last_error"] = craft_result.get("reason") or ""
            observed["auto_last_error_at"] = float(now) if observed["auto_last_error"] else 0
            observed["auto_last_plan"] = craft_result.get("stage") or ""
            observed["auto_last_plan_at"] = float(now)
            observed["auto_next_time"] = float(craft_result.get("next_time", 0) or now + _craft_farm_interval_sec(config))
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

    await _execute_tianxing_auto_plan(plan, observed, config, now)


async def _execute_tianxing_auto_plan(plan, observed, config, now):
    action = str((plan or {}).get("action") or "")
    if _defer_tianxing_auto_plan_for_phaseful_summary(observed, now, plan):
        return

    _note_tianxing_auto_pending(observed, now, plan, config)
    state["tianxing_observation"] = observed
    save_state()

    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=0,
        priority=_tianxing_auto_send_priority(plan),
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
            sent_at + _tianxing_send_fail_backoff_sec(action, observed, now),
            "天星宗自动命令发送失败或被安全策略拦截",
        )
        return

    observed["auto_pending_msg_id"] = int(getattr(msg, "id", 0) or 0)
    observed["auto_pending_sent_at"] = float(sent_at)
    observed["auto_pending_due_at"] = float(sent_at + int(config.get("ack_timeout_sec", TIANXING_TIMELINE_ACK_TIMEOUT_SEC) or TIANXING_TIMELINE_ACK_TIMEOUT_SEC))
    observed["auto_last_action"] = action
    observed["auto_last_error"] = ""
    observed["auto_last_error_at"] = 0
    observed["auto_last_plan"] = plan.get("command") or ""
    observed["auto_last_plan_at"] = float(sent_at)
    observed["auto_next_time"] = observed["auto_pending_due_at"]
    state["tianxing_observation"] = observed
    save_state()


async def _run_tianxing_daily_bootstrap_scheduler_unlocked(now):
    """Run only the 0点日切观命/定命 preflight for the current identity."""
    now = float(now if now is not None else time.time())
    if not state.get("tianxing_enabled"):
        return {"active": False, "reason": "disabled"}
    if not is_module_available("天星宗"):
        return {"active": False, "reason": "unavailable"}
    dirty_fields = _dirty_tianxing_time_fields(state.get("tianxing_observation"))
    if dirty_fields:
        return {"active": False, "reason": f"dirty:{','.join(dirty_fields)}"}

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
    if is_tianxing_automation_paused(now=now, observed=observed):
        _apply_tianxing_pause_wait(observed, now, config=config)
        return {"active": False, "reason": "paused"}
    if _handle_tianxing_auto_pending(observed, now):
        return {"active": True, "reason": "pending"}

    plan = _build_tianxing_daily_plan(observed, config, now)
    if not plan.get("allowed") or not plan.get("daily_bootstrap"):
        return {"active": False, "reason": plan.get("reason") or "no_daily_bootstrap"}
    if _daily_plan_should_wait_for_backoff(plan, observed, now):
        return {"active": False, "reason": "backoff", "next_time": observed.get("auto_next_time", 0)}
    if str(plan.get("action") or "") in {"set_star", "predict", "change_fate"} and config.get("strategy_dry_run_enabled"):
        _record_tianxing_dry_run(observed, now, plan, config)
        return {"active": True, "reason": "dry_run"}

    await _execute_tianxing_auto_plan(plan, observed, config, now)
    return {"active": True, "action": plan.get("action") or "", "command": plan.get("command") or ""}


async def run_tianxing_daily_bootstrap_scheduler(now):
    async with _auto_lock():
        return await _run_tianxing_daily_bootstrap_scheduler_unlocked(now)


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
    pause_state = get_tianxing_automation_pause_state(observed=observed)
    effective_fixed_star = _effective_fixed_star(observed, time.time())
    fixed_star_text = effective_fixed_star or (
        f"未定（旧：{observed.get('fixed_star')} / {observed.get('fixed_star_day') or '未知日期'}）"
        if observed.get("fixed_star")
        else "未记录"
    )
    lines = [
        "🌌 天星宗",
        f"- 模块：{'开启' if state.get('tianxing_enabled') else '关闭'}（被动观察，手动动作受控发送）",
        f"- 自动接管：{_format_tianxing_pause_line(pause_state)}",
        "- 命令：.观命｜.定命 <紫微|天府|太阴|贪狼>｜.推命/.改命 <闭关|炼制|探索|斗法>｜.天机盘｜.消劫",
        f"- 命星：可选 {_format_list(observed.get('available_stars'))}｜已定 {fixed_star_text}",
        f"- 推命：{observed.get('current_prediction') or '无'}｜{fmt_abs_ts(observed.get('current_prediction_until', 0))}（{fmt_remaining(observed.get('current_prediction_until', 0))}）",
        f"- 改命：{observed.get('current_change') or '无'}｜{fmt_abs_ts(observed.get('current_change_until', 0))}（{fmt_remaining(observed.get('current_change_until', 0))}）",
        f"- 天机/逆命劫：{observed.get('tianji_value', 0)} / {observed.get('calamity_count', 0)}",
        f"- 命中/落空/改命：{observed.get('hit_count', 0)} / {observed.get('miss_count', 0)} / {observed.get('change_count', 0)}",
        f"- 最近动作：{observed.get('last_action') or '未记录'} / {observed.get('last_result') or '未记录'}",
        f"- 最近观察：{fmt_abs_ts(observed.get('last_observed_at', 0))}",
        f"- 自动调度：{fmt_abs_ts(observed.get('auto_next_time', 0))}（{fmt_remaining(observed.get('auto_next_time', 0))}）",
        f"- 自动策略：查盘{'开' if config.get('auto_panel_enabled') else '关'}｜观命{'开' if config.get('auto_observe_enabled') else '关'}｜消劫{'开' if config.get('auto_clear_calamity_enabled') else '关'}｜定命{'开' if config.get('auto_set_star_enabled') else '关'}｜推命{'开' if config.get('auto_predict_enabled') else '关'}｜改命{'开' if config.get('auto_change_fate_enabled') else '关'}｜特化命星{'开' if config.get('route_special_star_enabled') else '关'}｜试运行{'开' if config.get('strategy_dry_run_enabled') else '关'}",
        "- 定星规则：默认优先太阴/贪狼；特化命星开启后才按探索贪狼、闭关紫微、炼制天府、斗法太阴处理",
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


register_game_command_pre_send_guard(tianxing_route_pre_send_guard)


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
    "get_tianxing_automation_pause_state",
    "get_tianxing_automation_pause_text",
    "get_tianxing_status_text",
    "is_tianxing_automation_paused",
    "is_tianxing_route_released",
    "looks_like_tianxing_text",
    "mark_tianxing_route_result_unknown",
    "note_tianxing_retreat_force_exit_summary",
    "normalize_tianxing_auto_config",
    "normalize_tianxing_observation",
    "normalize_tianxing_timeline_state",
    "parse_tianxing_text",
    "run_tianxing_retreat_farm_scheduler",
    "run_tianxing_craft_farm_scheduler",
    "run_tianxing_consume_craft_prediction",
    "run_tianxing_daily_bootstrap_scheduler",
    "run_tianxing_scheduler",
    "run_tianxing_timeline_scheduler",
    "set_tianxing_auto_config",
    "set_tianxing_automation_paused",
    "tianxing_route_pre_send_guard",
]
