import copy
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from ..config import (
    CD_BUFFER_SEC,
    CMD_WANXIN_ACCEPT_COMMISSION,
    CMD_WANXIN_ASSIST_BANNER,
    CMD_WANXIN_ASSIST_IDENTIFY,
    CMD_WANXIN_ASSIST_STRIP,
    CMD_WANXIN_DEDUCE,
    CMD_WANXIN_HELP,
    CMD_WANXIN_PROTECT,
    CMD_WANXIN_PUBLISH_COMMISSION,
    CMD_WANXIN_STATUS,
    CMD_WANXIN_VISIT,
    MESSAGES_DIR,
    RETRY_MAX_SEC,
    TZ_LOCAL,
)
from ..action_guard import close_by_family as close_action_guard_by_family
from ..message_log_recovery import find_message_log_replies
from ..persistence import save_state
from ..runtime import console_log, get_last_game_send_block, send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_identity_display_name,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    has_identity,
    normalize_sect_name,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key, has_wait_time, parse_wait_time
from ._phaseful import get_phaseful_summary_risk_reason


WANXIN_MODULE_NAME = "婉心封魂"
WANXIN_DEFAULT_ASSIST_SEND_AS_ID = 3907536807
WANXIN_REPLY_TIMEOUT_SEC = 90
WANXIN_RECOVERY_RETRY_SEC = 10 * 60
WANXIN_SEND_QUEUE_TIMEOUT_SEC = 180
WANXIN_SEND_QUEUE_RETRY_SEC = 10 * 60
WANXIN_CHAIN_STEP_SEC = 20
WANXIN_VISIT_CD_SEC = 24 * 3600
WANXIN_PROTECT_CD_SEC = 6 * 3600
WANXIN_DEDUCE_CD_SEC = 8 * 3600
WANXIN_IDENTIFY_CD_SEC = 4 * 3600
WANXIN_BANNER_CD_SEC = 6 * 3600
WANXIN_STRIP_CD_SEC = 8 * 3600
WANXIN_ANCHOR_MAX_AGE_SEC = 24 * 3600
WANXIN_UNAVAILABLE_BACKOFF_SEC = 24 * 3600
WANXIN_PHASEFUL_DEFER_SEC = 5 * 60

WANXIN_ACTION_VISIT = "visit"
WANXIN_ACTION_PROTECT = "protect"
WANXIN_ACTION_DEDUCE = "deduce"
WANXIN_ACTION_PUBLISH = "publish"
WANXIN_ACTION_ACCEPT = "accept"
WANXIN_ACTION_IDENTIFY = "identify"
WANXIN_ACTION_BANNER = "banner"
WANXIN_ACTION_STRIP = "strip"
WANXIN_ACTION_STATUS = "status"

WANXIN_SELF_ACTIONS = (WANXIN_ACTION_VISIT, WANXIN_ACTION_PROTECT, WANXIN_ACTION_DEDUCE)
WANXIN_ASSIST_ACTIONS = (WANXIN_ACTION_IDENTIFY, WANXIN_ACTION_BANNER, WANXIN_ACTION_STRIP)

WANXIN_ACTION_COMMANDS = {
    WANXIN_ACTION_STATUS: CMD_WANXIN_STATUS,
    WANXIN_ACTION_VISIT: CMD_WANXIN_VISIT,
    WANXIN_ACTION_PROTECT: CMD_WANXIN_PROTECT,
    WANXIN_ACTION_DEDUCE: CMD_WANXIN_DEDUCE,
    WANXIN_ACTION_PUBLISH: CMD_WANXIN_PUBLISH_COMMISSION,
    WANXIN_ACTION_ACCEPT: CMD_WANXIN_ACCEPT_COMMISSION,
    WANXIN_ACTION_IDENTIFY: CMD_WANXIN_ASSIST_IDENTIFY,
    WANXIN_ACTION_BANNER: CMD_WANXIN_ASSIST_BANNER,
    WANXIN_ACTION_STRIP: CMD_WANXIN_ASSIST_STRIP,
}

WANXIN_ACTION_LABELS = {
    WANXIN_ACTION_STATUS: "查婉心",
    WANXIN_ACTION_VISIT: "探望南宫婉",
    WANXIN_ACTION_PROTECT: "护持神魂",
    WANXIN_ACTION_DEDUCE: "推演封魂咒",
    WANXIN_ACTION_PUBLISH: "发布解咒委托",
    WANXIN_ACTION_ACCEPT: "接取解咒委托",
    WANXIN_ACTION_IDENTIFY: "辨认咒纹",
    WANXIN_ACTION_BANNER: "借幡镇魂",
    WANXIN_ACTION_STRIP: "剥离咒源",
}

WANXIN_ACTION_FAMILIES = {
    WANXIN_ACTION_STATUS: "wanxin_panel",
    WANXIN_ACTION_VISIT: "wanxin_visit",
    WANXIN_ACTION_PROTECT: "wanxin_protect",
    WANXIN_ACTION_DEDUCE: "wanxin_deduce",
    WANXIN_ACTION_PUBLISH: "wanxin_commission",
    WANXIN_ACTION_ACCEPT: "wanxin_accept",
    WANXIN_ACTION_IDENTIFY: "wanxin_assist_identify",
    WANXIN_ACTION_BANNER: "wanxin_assist_banner",
    WANXIN_ACTION_STRIP: "wanxin_assist_strip",
}

WANXIN_ACTION_COOLDOWN_SEC = {
    WANXIN_ACTION_VISIT: WANXIN_VISIT_CD_SEC,
    WANXIN_ACTION_PROTECT: WANXIN_PROTECT_CD_SEC,
    WANXIN_ACTION_DEDUCE: WANXIN_DEDUCE_CD_SEC,
    WANXIN_ACTION_IDENTIFY: WANXIN_IDENTIFY_CD_SEC,
    WANXIN_ACTION_BANNER: WANXIN_BANNER_CD_SEC,
    WANXIN_ACTION_STRIP: WANXIN_STRIP_CD_SEC,
}

RE_WANXIN_STAGE = re.compile(r"阶段[:：]\s*(?P<stage>[^\n]+)")
RE_WANXIN_STAGE_INLINE = re.compile(r"婉心封魂[:：]\s*(?P<stage>[^\n|]+)")
RE_WANXIN_VALUE = re.compile(r"(?P<name>婉心|魂封|月魄|咒源)\s*(?:[:：]\s*)?(?P<value>\d+)")
RE_COMMISSION_ID = re.compile(r"委托\s*ID[:：]\s*(?P<id>\d+)")
RE_EXISTING_COMMISSION_ID = re.compile(r"解咒委托[（(]\s*ID[:：]?\s*(?P<id>\d+)")
RE_ACCEPT_CONTRACT = re.compile(r"阴罗宗弟子\s*@(?P<helper>[\w\d_]+)\s*已接取\s*@(?P<owner>[\w\d_]+)\s*的解咒委托")
RE_TARGET_USER = re.compile(r"替\s*@(?P<owner>[\w\d_]+)|@(?P<owner2>[\w\d_]+)\s*魂封")
RE_SOURCE_GAIN = re.compile(r"咒源\s*\+(?P<gain>\d+)")
RE_SEAL_DOWN = re.compile(r"魂封\s*-(?P<down>\d+)")
RE_MOON_GAIN = re.compile(r"月魄\s*\+(?P<gain>\d+)")
RE_CONTRIB_GAIN = re.compile(r"咒师贡献\s*\+(?P<gain>\d+)")


def _entry_ts(value):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    if raw.endswith(" UTC+8"):
        raw = raw[:-6]
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _iter_message_log_entries_between(start_ts, end_ts):
    start = datetime.fromtimestamp(float(start_ts or 0), TZ_LOCAL).date()
    end = datetime.fromtimestamp(float(end_ts or start_ts or 0), TZ_LOCAL).date()
    day = start
    while day <= end:
        log_path = Path(MESSAGES_DIR) / f"{day.isoformat()}.log"
        if log_path.exists():
            try:
                with log_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            entry = json.loads(line)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        ts = _entry_ts(entry.get("ts"))
                        if start_ts <= ts <= end_ts:
                            yield entry, ts
            except OSError:
                pass
        day += timedelta(days=1)


def _safe_int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return default


def _default_wanxin_observation():
    return {
        "available": "unknown",
        "stage": "",
        "wanxin": 0,
        "soul_seal": 0,
        "moon_soul": 0,
        "curse_source": 0,
        "last_observed_at": 0,
        "next_visit_time": 0,
        "next_protect_time": 0,
        "next_deduce_time": 0,
        "last_visit_day": "",
        "auto_next_time": 0,
        "auto_last_action": "",
        "auto_last_result": "",
        "auto_last_error": "",
        "auto_config": _default_wanxin_auto_config(),
        "pending": {},
        "commission": _default_wanxin_commission(),
        "assist": _default_wanxin_assist(),
        "recent": [],
    }


def _default_wanxin_auto_config():
    return {
        "visit_enabled": True,
        "protect_enabled": True,
        "deduce_enabled": True,
        "publish_enabled": False,
        "assist_enabled": True,
        "reward_lingshi": 1,
    }


def _default_wanxin_commission():
    return {
        "id": 0,
        "owner_username": "",
        "published_at": 0,
        "publish_msg_id": 0,
        "accepted": False,
        "accepted_at": 0,
        "accept_msg_id": 0,
        "helper_username": "",
    }


def _default_wanxin_assist():
    return {
        "send_as_id": WANXIN_DEFAULT_ASSIST_SEND_AS_ID,
        "identify_enabled": True,
        "banner_enabled": True,
        "strip_enabled": False,
        "next_identify_time": 0,
        "next_banner_time": 0,
        "next_strip_time": 0,
        "last_anchor_msg_id": 0,
        "last_anchor_at": 0,
        "last_action": "",
        "last_result": "",
        "last_error": "",
        "last_contrib_gain": 0,
    }


def _normalize_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on", "开", "开启"}:
        return True
    if raw in {"0", "false", "no", "off", "关", "关闭"}:
        return False
    return default


def normalize_wanxin_auto_config(value=None):
    config = _default_wanxin_auto_config()
    if isinstance(value, dict):
        config.update(value)
    for key in ("visit_enabled", "protect_enabled", "deduce_enabled", "publish_enabled", "assist_enabled"):
        config[key] = _normalize_bool(config.get(key), _default_wanxin_auto_config()[key])
    reward = _safe_int(config.get("reward_lingshi"), 1)
    config["reward_lingshi"] = max(1, min(1_000_000, reward))
    return config


def normalize_wanxin_observation(value=None):
    observed = copy.deepcopy(_default_wanxin_observation())
    if isinstance(value, dict):
        observed.update(value)
    observed["available"] = str(observed.get("available") or "unknown")
    for key in ("stage", "auto_last_action", "auto_last_result", "auto_last_error", "last_visit_day"):
        observed[key] = str(observed.get(key) or "").strip()
    for key in (
        "wanxin",
        "soul_seal",
        "moon_soul",
        "curse_source",
    ):
        observed[key] = max(0, _safe_int(observed.get(key), 0))
    for key in (
        "last_observed_at",
        "next_visit_time",
        "next_protect_time",
        "next_deduce_time",
        "auto_next_time",
    ):
        observed[key] = max(0.0, _safe_float(observed.get(key), 0))

    observed["auto_config"] = normalize_wanxin_auto_config(observed.get("auto_config"))

    commission = _default_wanxin_commission()
    if isinstance(observed.get("commission"), dict):
        commission.update(observed["commission"])
    commission["id"] = max(0, _safe_int(commission.get("id"), 0))
    commission["publish_msg_id"] = max(0, _safe_int(commission.get("publish_msg_id"), 0))
    commission["accept_msg_id"] = max(0, _safe_int(commission.get("accept_msg_id"), 0))
    commission["published_at"] = max(0.0, _safe_float(commission.get("published_at"), 0))
    commission["accepted_at"] = max(0.0, _safe_float(commission.get("accepted_at"), 0))
    commission["accepted"] = _normalize_bool(commission.get("accepted"), False)
    for key in ("owner_username", "helper_username"):
        commission[key] = str(commission.get(key) or "").strip().lstrip("@")
    observed["commission"] = commission

    assist = _default_wanxin_assist()
    if isinstance(observed.get("assist"), dict):
        assist.update(observed["assist"])
    assist["send_as_id"] = max(0, _safe_int(assist.get("send_as_id"), WANXIN_DEFAULT_ASSIST_SEND_AS_ID))
    for key in ("identify_enabled", "banner_enabled", "strip_enabled"):
        assist[key] = _normalize_bool(assist.get(key), _default_wanxin_assist()[key])
    for key in ("next_identify_time", "next_banner_time", "next_strip_time", "last_anchor_at"):
        assist[key] = max(0.0, _safe_float(assist.get(key), 0))
    assist["last_anchor_msg_id"] = max(0, _safe_int(assist.get("last_anchor_msg_id"), 0))
    assist["last_contrib_gain"] = max(0, _safe_int(assist.get("last_contrib_gain"), 0))
    for key in ("last_action", "last_result", "last_error"):
        assist[key] = str(assist.get(key) or "").strip()
    observed["assist"] = assist

    pending = observed.get("pending") if isinstance(observed.get("pending"), dict) else {}
    cleaned_pending = {}
    if pending:
        cleaned_pending = {
            "action": str(pending.get("action") or "").strip(),
            "family": str(pending.get("family") or "").strip(),
            "msg_id": max(0, _safe_int(pending.get("msg_id"), 0)),
            "send_as_id": max(0, _safe_int(pending.get("send_as_id"), 0)),
            "reply_to_msg_id": max(0, _safe_int(pending.get("reply_to_msg_id"), 0)),
            "sent_at": max(0.0, _safe_float(pending.get("sent_at"), 0)),
            "reply_due_at": max(0.0, _safe_float(pending.get("reply_due_at"), 0)),
        }
        if not cleaned_pending["action"] or cleaned_pending["msg_id"] <= 0:
            cleaned_pending = {}
    observed["pending"] = cleaned_pending

    recent = []
    for item in observed.get("recent") or []:
        if isinstance(item, dict):
            recent.append(item)
    observed["recent"] = recent[-8:]
    return observed


def _push_recent(observed, now, action, result, detail=""):
    recent = observed.get("recent") if isinstance(observed.get("recent"), list) else []
    recent.append({
        "ts": float(now),
        "action": str(action or ""),
        "result": str(result or ""),
        "detail": str(detail or "")[:160],
    })
    observed["recent"] = recent[-8:]


def _set_observed(observed):
    state["wanxin_observation"] = normalize_wanxin_observation(observed)


def _owner_username(send_as_id=None):
    profile = get_send_as_profile(send_as_id)
    return str(profile.get("username") or "").strip().lstrip("@")


def _find_identity_by_username(username):
    target = str(username or "").strip().lstrip("@").casefold()
    if not target:
        return 0
    for identity_id in get_identity_ids():
        profile = get_send_as_profile(identity_id)
        candidate = str(profile.get("username") or "").strip().lstrip("@").casefold()
        if candidate and candidate == target:
            return int(identity_id)
    return 0


def _find_owner_identity_by_pending(family="", reply_to_msg_id=0, send_as_id=0):
    family = str(family or "").strip()
    reply_to_msg_id = _safe_int(reply_to_msg_id, 0)
    send_as_id = _safe_int(send_as_id, 0)
    if not family.startswith("wanxin_"):
        return 0
    for identity_id in get_identity_ids():
        identity_state = get_identity_state(identity_id)
        observed = normalize_wanxin_observation(identity_state.get("wanxin_observation"))
        pending = observed.get("pending") if isinstance(observed.get("pending"), dict) else {}
        if not pending:
            continue
        if str(pending.get("family") or "").strip() != family:
            continue
        if reply_to_msg_id > 0 and int(pending.get("msg_id", 0) or 0) != reply_to_msg_id:
            continue
        if send_as_id > 0 and int(pending.get("send_as_id", 0) or 0) != send_as_id:
            continue
        return int(identity_id)
    return 0


def _is_yinluo_identity(send_as_id):
    profile = get_send_as_profile(send_as_id)
    return normalize_sect_name(profile.get("sect_name")) == "阴罗宗"


def looks_like_wanxin_text(text):
    raw = str(text or "")
    return any(marker in raw for marker in (
        "婉心封魂",
        "封魂咒",
        "南宫婉封魂",
        "解咒委托",
        "咒契协定",
        "阴罗辨咒",
        "借幡镇魂",
        "剥离咒源",
        "剥离咒源失败",
        "咒源尚未辨明",
        "探望南宫婉",
        "护持神魂",
        "月殿余咒",
        "阴罗咒源",
        "玄冰丹方",
        "婉影觉醒",
        "北冥小极宫",
        "北冥寒令",
        "封魂咒纹变化极慢",
        "咒源剥离牵涉神魂反噬",
    ))


def _parse_panel_values(text):
    raw = str(text or "")
    values = {}
    stage_match = RE_WANXIN_STAGE.search(raw)
    if not stage_match:
        stage_match = RE_WANXIN_STAGE_INLINE.search(raw)
    if stage_match:
        values["stage"] = stage_match.group("stage").strip()
    for match in RE_WANXIN_VALUE.finditer(raw):
        name = match.group("name")
        value = _safe_int(match.group("value"), 0)
        if name == "婉心":
            values["wanxin"] = value
        elif name == "魂封":
            values["soul_seal"] = value
        elif name == "月魄":
            values["moon_soul"] = value
        elif name == "咒源":
            values["curse_source"] = value
    return values


def _apply_panel_values(observed, values, now):
    if values:
        observed.update(values)
        observed["available"] = "yes"
        observed["last_observed_at"] = float(now)


def _parse_target_username(text):
    match = RE_TARGET_USER.search(str(text or ""))
    if not match:
        return ""
    return (match.group("owner") or match.group("owner2") or "").strip().lstrip("@")


def _wait_until_from_text(text, now, fallback_sec):
    if has_wait_time(text):
        wait_sec = max(1, parse_wait_time(text))
        return float(now + wait_sec + CD_BUFFER_SEC)
    return float(now + fallback_sec)


def _cooldown_action_from_text(text):
    raw = str(text or "")
    if "借幡镇魂" in raw:
        return WANXIN_ACTION_BANNER
    if "剥离咒源" in raw or "咒源剥离" in raw:
        return WANXIN_ACTION_STRIP
    if "辨认咒纹" in raw or "阴罗辨咒" in raw or "辨咒" in raw:
        return WANXIN_ACTION_IDENTIFY
    if "推演封魂咒" in raw or "封魂咒纹变化极慢" in raw or ("咒纹" in raw and "推演" in raw):
        return WANXIN_ACTION_DEDUCE
    if "护持神魂" in raw or ("神魂" in raw and "冷却" in raw):
        return WANXIN_ACTION_PROTECT
    if "探望南宫婉" in raw:
        return WANXIN_ACTION_VISIT
    return ""


def parse_wanxin_text(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    raw = str(text or "").strip()
    if not raw or not looks_like_wanxin_text(raw):
        return None

    parsed = {
        "type": "unknown",
        "family": str(family or ""),
        "values": _parse_panel_values(raw),
        "available": "",
        "next_time": 0,
        "target_username": "",
        "commission_id": 0,
        "helper_username": "",
        "contrib_gain": 0,
        "source_gain": 0,
        "seal_down": 0,
        "moon_gain": 0,
        "cooldown_action": "",
        "summary": "",
    }

    if "需先成功通关【掩月抢亲】" in raw or "方可开启【婉心封魂】" in raw:
        parsed.update({"type": "unavailable", "available": "no", "summary": "缺少婉心封魂前置"})
        return parsed
    if "【婉心封魂指令】" in raw:
        parsed.update({"type": "help", "available": "unknown", "summary": "婉心帮助"})
        return parsed
    if "【婉心封魂】" in raw:
        parsed.update({"type": "panel", "available": "yes", "summary": "婉心状态"})
        return parsed
    if "【婉影觉醒】" in raw:
        parsed.update({"type": "awakened", "available": "yes", "summary": "婉影觉醒"})
        return parsed
    if "你没有【北冥寒令】" in raw or "无法开启北冥小极宫" in raw:
        parsed.update({"type": "beiming_blocked", "available": "yes", "summary": "缺少北冥寒令"})
        return parsed
    if "【解咒委托已发布】" in raw:
        match = RE_COMMISSION_ID.search(raw)
        parsed.update({
            "type": "commission_published",
            "commission_id": _safe_int(match.group("id"), 0) if match else 0,
            "available": "yes",
            "summary": "委托已发布",
        })
        return parsed
    if "你已有进行中的解咒委托" in raw:
        match = RE_EXISTING_COMMISSION_ID.search(raw)
        parsed.update({
            "type": "commission_existing",
            "commission_id": _safe_int(match.group("id"), 0) if match else 0,
            "available": "yes",
            "summary": "已有进行中委托",
        })
        return parsed
    if "【咒契协定已成】" in raw:
        match = RE_ACCEPT_CONTRACT.search(raw)
        parsed.update({
            "type": "commission_accepted",
            "available": "yes",
            "helper_username": (match.group("helper") if match else "").strip().lstrip("@"),
            "target_username": (match.group("owner") if match else "").strip().lstrip("@"),
            "summary": "咒契已成",
        })
        return parsed
    if "没有有效的咒契协定" in raw and "发布委托" in raw and "接取" in raw:
        parsed.update({
            "type": "commission_invalid",
            "available": "yes",
            "summary": "咒契失效，需重新发布并接取",
        })
        return parsed
    if "【阴罗辨咒】" in raw:
        source_match = RE_SOURCE_GAIN.search(raw)
        contrib_match = RE_CONTRIB_GAIN.search(raw)
        parsed.update({
            "type": "assist_identify_success",
            "available": "yes",
            "target_username": _parse_target_username(raw),
            "source_gain": _safe_int(source_match.group("gain"), 0) if source_match else 0,
            "contrib_gain": _safe_int(contrib_match.group("gain"), 0) if contrib_match else 0,
            "summary": "辨认咒纹成功",
        })
        return parsed
    if "【借幡镇魂】" in raw:
        seal_match = RE_SEAL_DOWN.search(raw)
        moon_match = RE_MOON_GAIN.search(raw)
        contrib_match = RE_CONTRIB_GAIN.search(raw)
        parsed.update({
            "type": "assist_banner_success",
            "available": "yes",
            "target_username": _parse_target_username(raw),
            "seal_down": _safe_int(seal_match.group("down"), 0) if seal_match else 0,
            "moon_gain": _safe_int(moon_match.group("gain"), 0) if moon_match else 0,
            "contrib_gain": _safe_int(contrib_match.group("gain"), 0) if contrib_match else 0,
            "summary": "借幡镇魂成功",
        })
        return parsed
    if "【剥离咒源失败】" in raw:
        contrib_match = RE_CONTRIB_GAIN.search(raw)
        parsed.update({
            "type": "assist_strip_failed",
            "available": "yes",
            "target_username": _parse_target_username(raw),
            "contrib_gain": _safe_int(contrib_match.group("gain"), 0) if contrib_match else 0,
            "summary": "剥离咒源失败",
        })
        return parsed
    if "【剥离咒源】" in raw or "剥离阴罗残咒" in raw:
        contrib_match = RE_CONTRIB_GAIN.search(raw)
        parsed.update({
            "type": "assist_strip_success",
            "available": "yes",
            "target_username": _parse_target_username(raw),
            "contrib_gain": _safe_int(contrib_match.group("gain"), 0) if contrib_match else 0,
            "summary": "剥离咒源成功",
        })
        return parsed
    if "咒源尚未辨明" in raw:
        parsed.update({
            "type": "assist_strip_blocked",
            "available": "yes",
            "summary": "咒源不足，暂不剥离",
        })
        return parsed
    if "请回复委托发布者" in raw or "命令后指定对方" in raw:
        parsed.update({
            "type": "assist_missing_target",
            "available": "yes",
            "summary": "协助缺少委托方锚点",
        })
        return parsed
    if "并非阴罗宗弟子" in raw and "无法插手" in raw:
        parsed.update({
            "type": "assist_not_yinluo",
            "available": "yes",
            "summary": "协助身份不是阴罗宗",
        })
        return parsed
    if has_wait_time(raw) and (
        "封魂" in raw
        or "咒纹" in raw
        or "神魂" in raw
        or "南宫婉" in raw
        or "咒师" in raw
        or "辨认咒纹" in raw
        or "借幡镇魂" in raw
        or "剥离咒源" in raw
        or "咒源剥离" in raw
    ):
        parsed.update({
            "type": "cooldown",
            "available": "yes",
            "next_time": _wait_until_from_text(raw, now, WANXIN_RECOVERY_RETRY_SEC),
            "cooldown_action": _cooldown_action_from_text(raw),
            "summary": "冷却中",
        })
        return parsed
    if "【推演封魂咒】" in raw:
        source_match = RE_SOURCE_GAIN.search(raw)
        parsed.update({
            "type": "deduce_success",
            "available": "yes",
            "source_gain": _safe_int(source_match.group("gain"), 0) if source_match else 0,
            "summary": "推演成功",
        })
        return parsed
    if "今日已探望过南宫婉" in raw or "已探望过南宫婉" in raw:
        parsed.update({"type": "visit_already", "available": "yes", "summary": "今日已探望"})
        return parsed
    if "探望" in raw and "南宫婉" in raw:
        parsed.update({"type": "visit_success", "available": "yes", "summary": "探望成功"})
        return parsed
    if "护持" in raw and ("魂封" in raw or "神魂" in raw):
        parsed.update({"type": "protect_success", "available": "yes", "summary": "护持成功"})
        return parsed
    return parsed


def _pending_blocks(observed, now):
    pending = observed.get("pending") if isinstance(observed.get("pending"), dict) else {}
    if not pending:
        return False
    due = float(pending.get("reply_due_at", 0) or 0)
    if due > now:
        observed["auto_next_time"] = due
        return True
    action = str(pending.get("action") or "")
    family = str(pending.get("family") or WANXIN_ACTION_FAMILIES.get(action, "") or "")
    pending_send_as_id = _safe_int(pending.get("send_as_id"), get_current_identity_id())
    if family:
        close_action_guard_by_family(family, send_as_id=pending_send_as_id, reason="wanxin_timeout", now=now)
    observed["pending"] = {}
    msg_id = _safe_int(pending.get("msg_id"), 0)
    label = WANXIN_ACTION_LABELS.get(action, action)
    if msg_id > 0:
        retry_at = float(now + WANXIN_RECOVERY_RETRY_SEC)
        if action:
            _set_next_time_for_action(observed, action, retry_at)
        observed["auto_next_time"] = retry_at
        observed["auto_last_action"] = action
        observed["auto_last_result"] = "回复超时，短退避后校准"
        observed["auto_last_error"] = f"{label} 回复超时，未按技能冷却锁定"
        _push_recent(observed, now, action, "timeout_short_backoff", observed["auto_last_error"])
    else:
        observed["auto_last_action"] = action
        observed["auto_last_error"] = f"{label} 回复超时"
        observed["auto_next_time"] = float(now + WANXIN_RECOVERY_RETRY_SEC)
        _push_recent(observed, now, action, "timeout", observed["auto_last_error"])
    return True


def _mark_pending(observed, action, msg, now, *, send_as_id, reply_to_msg_id=0):
    msg_id = int(getattr(msg, "id", 0) or 0)
    observed["pending"] = {
        "action": action,
        "family": WANXIN_ACTION_FAMILIES.get(action, ""),
        "msg_id": msg_id,
        "send_as_id": int(send_as_id or 0),
        "reply_to_msg_id": int(reply_to_msg_id or 0),
        "sent_at": float(now),
        "reply_due_at": float(now + WANXIN_REPLY_TIMEOUT_SEC),
    }
    observed["auto_last_action"] = action
    observed["auto_last_result"] = "已发送"
    observed["auto_last_error"] = ""
    observed["auto_next_time"] = float(now + WANXIN_REPLY_TIMEOUT_SEC)


def _clear_pending(observed):
    observed["pending"] = {}


def _mark_commission_invalid(observed, now, reason=""):
    reason = reason or "咒契失效，需重新发布并接取"
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else _default_wanxin_commission()
    commission["id"] = 0
    commission["accepted"] = False
    commission["accepted_at"] = 0
    commission["accept_msg_id"] = 0
    commission["publish_msg_id"] = 0
    commission["published_at"] = 0
    commission["helper_username"] = ""
    if not commission.get("owner_username"):
        commission["owner_username"] = _owner_username()
    observed["commission"] = commission

    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else _default_wanxin_assist()
    assist["last_anchor_msg_id"] = 0
    assist["last_anchor_at"] = 0
    assist["last_result"] = ""
    assist["last_error"] = reason
    observed["assist"] = assist

    observed["auto_last_result"] = ""
    observed["auto_last_error"] = reason
    _clear_pending(observed)
    _schedule_next(observed, now)


def _schedule_next(observed, now, delay_sec=WANXIN_CHAIN_STEP_SEC, *, result="", error=""):
    observed["auto_next_time"] = float(now + max(1, delay_sec))
    if result:
        observed["auto_last_result"] = result
    if error:
        observed["auto_last_error"] = error


def _send_block_code(send_as_id, command):
    block = get_last_game_send_block(send_as_id, command)
    return str((block or {}).get("code") or "").strip(), str((block or {}).get("reason") or "").strip()


def _apply_uncertain_action_backoff(observed, action, now, reason="", *, result=""):
    action = str(action or "").strip()
    label = WANXIN_ACTION_LABELS.get(action, action or "婉心动作")
    if action == WANXIN_ACTION_VISIT:
        next_time = _next_daily_after(now)
        _set_next_time_for_action(observed, action, next_time)
        observed["auto_next_time"] = next_time
    elif action in WANXIN_ACTION_COOLDOWN_SEC:
        next_time = float(now + WANXIN_ACTION_COOLDOWN_SEC[action] + CD_BUFFER_SEC)
        _set_next_time_for_action(observed, action, next_time)
        observed["auto_next_time"] = next_time
    else:
        observed["auto_next_time"] = float(now + 30 * 60)
    observed["auto_last_action"] = action
    observed["auto_last_result"] = result or f"{label} 状态未知，按冷却退避"
    observed["auto_last_error"] = reason or f"{label} 状态未知"
    _push_recent(observed, now, action, "uncertain_backoff", observed["auto_last_error"])
    return observed["auto_next_time"]


def _handle_unsent_or_uncertain_send(observed, action, command, now, *, send_as_id):
    code, reason = _send_block_code(send_as_id, command)
    if code == "send_timeout":
        detail = reason or f"{command} 发送状态未知"
        _apply_uncertain_action_backoff(observed, action, now, detail)
        return False
    if code == "send_queue_timeout":
        _schedule_next(observed, now, WANXIN_SEND_QUEUE_RETRY_SEC, error=reason or f"{command} 排队超时未发送")
        return False
    if code == "global_disabled":
        _schedule_next(observed, now, RETRY_MAX_SEC, error=reason or "全局暂停，婉心延后")
        return False
    if code in {"action_guard", "pre_send_guard", "bot_health", "account_offline", "identity_weak"}:
        _schedule_next(observed, now, RETRY_MAX_SEC, error=reason or f"{command} 未发送，延后重试")
        return False
    detail = reason or f"{command} 发送未确认，延后重试"
    _schedule_next(observed, now, RETRY_MAX_SEC, error=detail)
    return False


def _apply_assist_success_to_observed(observed, action, parsed, now):
    _apply_success_cooldown(observed, action, now, parsed)
    observed["assist"]["last_action"] = action
    observed["assist"]["last_result"] = parsed.get("summary") or "协助成功"
    observed["assist"]["last_error"] = ""
    observed["assist"]["last_contrib_gain"] = int(parsed.get("contrib_gain", 0) or 0)
    _apply_panel_values(observed, parsed.get("values") or {}, now)
    observed["auto_last_action"] = action
    observed["auto_last_result"] = parsed.get("summary") or "协助成功"
    observed["auto_last_error"] = ""
    _clear_pending(observed)
    _schedule_next(observed, now)
    _push_recent(observed, now, action, parsed.get("summary") or "协助成功")


def _recover_recent_assist_success_from_log(observed, action, send_started_at):
    expected_types = {
        WANXIN_ACTION_IDENTIFY: "assist_identify_success",
        WANXIN_ACTION_BANNER: "assist_banner_success",
        WANXIN_ACTION_STRIP: "assist_strip_success",
    }
    expected_type = expected_types.get(action)
    if not expected_type:
        return False
    owner_username = str((observed.get("commission") or {}).get("owner_username") or _owner_username() or "").strip().lstrip("@").casefold()
    if not owner_username:
        return False
    end_ts = max(float(time.time()), float(send_started_at or 0)) + 5
    start_ts = max(0.0, float(send_started_at or 0) - 30)
    latest = None
    for entry, entry_ts in _iter_message_log_entries_between(start_ts, end_ts):
        if str(entry.get("event_type") or "") not in {"message", "edit"}:
            continue
        parsed = parse_wanxin_text(entry.get("text") or "", now=entry_ts, family=WANXIN_ACTION_FAMILIES.get(action, ""))
        if not parsed or parsed.get("type") != expected_type:
            continue
        target = str(parsed.get("target_username") or "").strip().lstrip("@").casefold()
        if target != owner_username:
            continue
        latest = (parsed, entry_ts)
    if not latest:
        return False
    parsed, entry_ts = latest
    _apply_assist_success_to_observed(observed, action, parsed, entry_ts)
    return True


def _is_wanxin_reply_log_entry(entry):
    return looks_like_wanxin_text(str((entry or {}).get("text") or "").strip())


async def _recover_wanxin_pending_from_message_log(observed, now):
    pending = observed.get("pending") if isinstance(observed.get("pending"), dict) else {}
    if not pending:
        return False
    msg_id = _safe_int(pending.get("msg_id"), 0)
    if msg_id <= 0:
        return False
    action = str(pending.get("action") or "")
    family = str(pending.get("family") or WANXIN_ACTION_FAMILIES.get(action, "") or "")
    if not family:
        return False
    replies = find_message_log_replies(
        msg_id,
        now,
        lookback_sec=max(15 * 60, WANXIN_REPLY_TIMEOUT_SEC * 5),
        lookahead_sec=30,
        predicate=_is_wanxin_reply_log_entry,
    )
    if not replies:
        return False
    command = WANXIN_ACTION_COMMANDS.get(action, "")
    reply_to = SimpleNamespace(id=msg_id, raw_text=command)
    for entry in replies:
        handled = await handle_wanxin_reply(
            entry.get("text") or "",
            float(entry.get("ts_epoch") or now),
            reply_to=reply_to,
            matched_family=family,
            result_msg_id=int(entry.get("message_id") or 0),
        )
        if handled:
            refreshed = normalize_wanxin_observation(state.get("wanxin_observation"))
            observed.clear()
            observed.update(refreshed)
            observed["auto_last_error"] = ""
            return True
    return False


def _next_daily_after(now):
    dt = datetime.fromtimestamp(now, TZ_LOCAL)
    next_day = (dt + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return next_day.timestamp()


def _due_time_for_action(observed, action):
    if action == WANXIN_ACTION_VISIT:
        return float(observed.get("next_visit_time", 0) or 0)
    if action == WANXIN_ACTION_PROTECT:
        return float(observed.get("next_protect_time", 0) or 0)
    if action == WANXIN_ACTION_DEDUCE:
        return float(observed.get("next_deduce_time", 0) or 0)
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    if action == WANXIN_ACTION_IDENTIFY:
        return float(assist.get("next_identify_time", 0) or 0)
    if action == WANXIN_ACTION_BANNER:
        return float(assist.get("next_banner_time", 0) or 0)
    if action == WANXIN_ACTION_STRIP:
        return float(assist.get("next_strip_time", 0) or 0)
    return 0.0


def _set_next_time_for_action(observed, action, next_time):
    next_time = float(next_time or 0)
    if action == WANXIN_ACTION_VISIT:
        observed["next_visit_time"] = next_time
    elif action == WANXIN_ACTION_PROTECT:
        observed["next_protect_time"] = next_time
    elif action == WANXIN_ACTION_DEDUCE:
        observed["next_deduce_time"] = next_time
    elif action == WANXIN_ACTION_IDENTIFY:
        observed["assist"]["next_identify_time"] = next_time
    elif action == WANXIN_ACTION_BANNER:
        observed["assist"]["next_banner_time"] = next_time
    elif action == WANXIN_ACTION_STRIP:
        observed["assist"]["next_strip_time"] = next_time


def _action_enabled(observed, action):
    config = normalize_wanxin_auto_config(observed.get("auto_config"))
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    if action == WANXIN_ACTION_VISIT:
        return bool(config.get("visit_enabled"))
    if action == WANXIN_ACTION_PROTECT:
        return bool(config.get("protect_enabled"))
    if action == WANXIN_ACTION_DEDUCE:
        return bool(config.get("deduce_enabled"))
    if action == WANXIN_ACTION_IDENTIFY:
        return bool(config.get("assist_enabled") and assist.get("identify_enabled"))
    if action == WANXIN_ACTION_BANNER:
        return bool(config.get("assist_enabled") and assist.get("banner_enabled"))
    if action == WANXIN_ACTION_STRIP:
        return bool(config.get("assist_enabled") and assist.get("strip_enabled"))
    return True


def _next_due_action(observed, now, actions):
    candidates = []
    for action in actions:
        if not _action_enabled(observed, action):
            continue
        due_at = _due_time_for_action(observed, action)
        if due_at <= now:
            candidates.append((due_at, action))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], actions.index(item[1])))
    return candidates[0][1]


async def _send_owner_action(observed, action, now, *, command_override=""):
    command = str(command_override or WANXIN_ACTION_COMMANDS.get(action, "")).strip()
    if not command:
        return False
    phaseful_reason = get_phaseful_summary_risk_reason()
    if phaseful_reason:
        _schedule_next(observed, now, WANXIN_PHASEFUL_DEFER_SEC, result=f"避让结算：{phaseful_reason}")
        return False
    msg = await send_game_command(
        command,
        track=True,
        max_retry=0,
        reply_timeout=WANXIN_REPLY_TIMEOUT_SEC,
        source_module=WANXIN_MODULE_NAME,
        op_id=f"wanxin-{action}-{int(now)}",
        queue_timeout=WANXIN_SEND_QUEUE_TIMEOUT_SEC,
    )
    if not msg:
        _handle_unsent_or_uncertain_send(observed, action, command, now, send_as_id=get_current_identity_id())
        return False
    sent_at = float(getattr(msg, "sent_at", 0) or now)
    msg_id = int(getattr(msg, "id", 0) or 0)
    if msg_id > 0:
        observed["assist"]["last_anchor_msg_id"] = msg_id
        observed["assist"]["last_anchor_at"] = sent_at
        if action == WANXIN_ACTION_PUBLISH:
            observed["commission"]["publish_msg_id"] = msg_id
            observed["commission"]["published_at"] = sent_at
    _mark_pending(observed, action, msg, sent_at, send_as_id=get_current_identity_id())
    console_log(f"🌙 婉心已发送：{command}，等待回复→{fmt_abs_ts(observed['pending']['reply_due_at'])}", scope="identity", limit=180)
    return True


async def _send_accept_action(observed, now):
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    assist_send_as_id = int(assist.get("send_as_id", 0) or 0)
    commission_id = int((observed.get("commission") or {}).get("id", 0) or 0)
    if assist_send_as_id <= 0 or not has_identity(assist_send_as_id):
        _schedule_next(observed, now, 30 * 60, error=f"协助身份不存在：{assist_send_as_id or '未配置'}")
        return False
    if not _is_yinluo_identity(assist_send_as_id):
        _schedule_next(observed, now, 60 * 60, error=f"协助身份不是阴罗宗：{get_identity_display_name(assist_send_as_id)}")
        return False
    if commission_id <= 0:
        return False
    command = f"{CMD_WANXIN_ACCEPT_COMMISSION} {commission_id}"
    msg = await send_game_command(
        command,
        track=True,
        max_retry=0,
        reply_timeout=WANXIN_REPLY_TIMEOUT_SEC,
        send_as_id=assist_send_as_id,
        source_module=WANXIN_MODULE_NAME,
        op_id=f"wanxin-accept-{commission_id}-{int(now)}",
        queue_timeout=WANXIN_SEND_QUEUE_TIMEOUT_SEC,
    )
    if not msg:
        _handle_unsent_or_uncertain_send(observed, WANXIN_ACTION_ACCEPT, command, now, send_as_id=assist_send_as_id)
        return False
    sent_at = float(getattr(msg, "sent_at", 0) or now)
    _mark_pending(observed, WANXIN_ACTION_ACCEPT, msg, sent_at, send_as_id=assist_send_as_id)
    observed["commission"]["accept_msg_id"] = int(getattr(msg, "id", 0) or 0)
    return True


async def _send_assist_action(observed, action, now):
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    assist_send_as_id = int(assist.get("send_as_id", 0) or 0)
    reply_to_msg_id = int(assist.get("last_anchor_msg_id", 0) or 0)
    anchor_at = float(assist.get("last_anchor_at", 0) or 0)
    if assist_send_as_id <= 0 or not has_identity(assist_send_as_id):
        _schedule_next(observed, now, 30 * 60, error=f"协助身份不存在：{assist_send_as_id or '未配置'}")
        return False
    if not _is_yinluo_identity(assist_send_as_id):
        _schedule_next(observed, now, 60 * 60, error=f"协助身份不是阴罗宗：{get_identity_display_name(assist_send_as_id)}")
        return False
    if reply_to_msg_id <= 0 or (anchor_at > 0 and now - anchor_at > WANXIN_ANCHOR_MAX_AGE_SEC):
        _schedule_next(observed, now, 30 * 60, error="缺少可回复的委托方锚点")
        return False
    command = WANXIN_ACTION_COMMANDS.get(action, "")
    msg = await send_game_command(
        command,
        track=True,
        max_retry=0,
        reply_timeout=WANXIN_REPLY_TIMEOUT_SEC,
        reply_to=reply_to_msg_id,
        send_as_id=assist_send_as_id,
        source_module=WANXIN_MODULE_NAME,
        op_id=f"wanxin-assist-{action}-{int(now)}",
        queue_timeout=WANXIN_SEND_QUEUE_TIMEOUT_SEC,
    )
    if not msg:
        if str(_send_block_code(assist_send_as_id, command)[0]) == "send_timeout" and _recover_recent_assist_success_from_log(observed, action, now):
            return True
        _handle_unsent_or_uncertain_send(observed, action, command, now, send_as_id=assist_send_as_id)
        return False
    sent_at = float(getattr(msg, "sent_at", 0) or now)
    _mark_pending(observed, action, msg, sent_at, send_as_id=assist_send_as_id, reply_to_msg_id=reply_to_msg_id)
    assist["last_action"] = action
    assist["last_error"] = ""
    observed["assist"] = assist
    return True


def _owner_needs_commission(observed):
    config = normalize_wanxin_auto_config(observed.get("auto_config"))
    if not config.get("publish_enabled") or not config.get("assist_enabled"):
        return False
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    return int(commission.get("id", 0) or 0) <= 0


def _owner_needs_accept(observed):
    config = normalize_wanxin_auto_config(observed.get("auto_config"))
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    return bool(config.get("assist_enabled") and int(commission.get("id", 0) or 0) > 0 and not commission.get("accepted"))


async def run_wanxin_scheduler(now):
    if not state.get("wanxin_enabled"):
        return
    now = float(now or time.time())
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))
    try:
        current_identity_id = get_current_identity_id()
        assist_send_as_id = int((observed.get("assist") or {}).get("send_as_id", 0) or 0)
        if _is_yinluo_identity(current_identity_id):
            observed["auto_last_result"] = "阴罗协助身份：等待委托方锚点，不主动跑婉心主线"
            observed["auto_next_time"] = now + 30 * 60
            _set_observed(observed)
            save_state()
            return
        pending = observed.get("pending") if isinstance(observed.get("pending"), dict) else {}
        if pending and float(pending.get("reply_due_at", 0) or 0) <= now:
            if await _recover_wanxin_pending_from_message_log(observed, now):
                _set_observed(observed)
                save_state()
                await send_audit_log("🧊 婉心日志补偿：已采纳超时回包。", scope="identity", limit=180)
                return
        if _pending_blocks(observed, now):
            _set_observed(observed)
            save_state()
            return
        if observed.get("available") == "no":
            if float(observed.get("auto_next_time", 0) or 0) <= now:
                observed["auto_next_time"] = now + WANXIN_UNAVAILABLE_BACKOFF_SEC
            _set_observed(observed)
            save_state()
            return
        if float(observed.get("auto_next_time", 0) or 0) > now:
            return
        if not (observed.get("commission") or {}).get("owner_username"):
            observed["commission"]["owner_username"] = _owner_username()
        if _owner_needs_commission(observed):
            reward = int(observed.get("auto_config", {}).get("reward_lingshi", 1) or 1)
            await _send_owner_action(
                observed,
                WANXIN_ACTION_PUBLISH,
                now,
                command_override=f"{CMD_WANXIN_PUBLISH_COMMISSION} {reward}",
            )
            _set_observed(observed)
            save_state()
            return
        if _owner_needs_accept(observed):
            await _send_accept_action(observed, now)
            _set_observed(observed)
            save_state()
            return
        self_action = _next_due_action(observed, now, WANXIN_SELF_ACTIONS)
        if self_action:
            await _send_owner_action(observed, self_action, now)
            _set_observed(observed)
            save_state()
            return
        if bool((observed.get("commission") or {}).get("accepted")):
            assist_action = _next_due_action(observed, now, WANXIN_ASSIST_ACTIONS)
            if assist_action:
                await _send_assist_action(observed, assist_action, now)
                _set_observed(observed)
                save_state()
                return
        next_times = [
            _due_time_for_action(observed, action)
            for action in WANXIN_SELF_ACTIONS + WANXIN_ASSIST_ACTIONS
            if _action_enabled(observed, action) and _due_time_for_action(observed, action) > now
        ]
        observed["auto_next_time"] = min(next_times) if next_times else now + 30 * 60
        observed["auto_last_error"] = ""
        _set_observed(observed)
        save_state()
    except Exception as exc:
        observed["auto_last_error"] = f"婉心调度异常：{exc}"
        observed["auto_next_time"] = now + WANXIN_RECOVERY_RETRY_SEC
        _set_observed(observed)
        save_state()
        await send_audit_log(f"⚠️ 婉心调度异常：{exc}", scope="identity", limit=220)


async def run_wanxin_phaseful_cleanup_scheduler(now):
    if not state.get("wanxin_enabled"):
        return
    now = float(now or time.time())
    _cleanup_wanxin_pending_only(now)


def _cleanup_wanxin_pending_only(now):
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))
    pending_before = dict(observed.get("pending") or {})
    if not pending_before:
        return False
    _pending_blocks(observed, now)
    if pending_before and not observed.get("pending"):
        _set_observed(observed)
        save_state()
        return True
    if observed.get("pending"):
        _set_observed(observed)
        save_state()
        return False
    return False


async def run_wanxin_global_cleanup_scheduler(now):
    now = float(now or time.time())
    for identity_id in get_identity_ids():
        try:
            identity_id = int(identity_id or 0)
        except (TypeError, ValueError):
            continue
        if identity_id <= 0 or not has_identity(identity_id):
            continue
        with use_identity(identity_id):
            if not state.get("wanxin_enabled"):
                continue
            _cleanup_wanxin_pending_only(now)


def _apply_success_cooldown(observed, action, now, parsed=None):
    parsed = parsed or {}
    if action == WANXIN_ACTION_VISIT:
        observed["last_visit_day"] = get_day_key(now)
        observed["next_visit_time"] = _next_daily_after(now)
    elif action == WANXIN_ACTION_PROTECT:
        observed["next_protect_time"] = now + WANXIN_PROTECT_CD_SEC + CD_BUFFER_SEC
    elif action == WANXIN_ACTION_DEDUCE:
        observed["next_deduce_time"] = now + WANXIN_DEDUCE_CD_SEC + CD_BUFFER_SEC
    elif action == WANXIN_ACTION_IDENTIFY:
        observed["assist"]["next_identify_time"] = now + WANXIN_IDENTIFY_CD_SEC + CD_BUFFER_SEC
    elif action == WANXIN_ACTION_BANNER:
        observed["assist"]["next_banner_time"] = now + WANXIN_BANNER_CD_SEC + CD_BUFFER_SEC
    elif action == WANXIN_ACTION_STRIP:
        observed["assist"]["next_strip_time"] = now + WANXIN_STRIP_CD_SEC + CD_BUFFER_SEC


def _set_cooldown_from_reply(observed, action, next_time):
    if not action:
        return
    _set_next_time_for_action(observed, action, next_time)


def _matching_pending_action(observed, matched_family):
    pending = observed.get("pending") if isinstance(observed.get("pending"), dict) else {}
    pending_action = str(pending.get("action") or "")
    if pending_action and WANXIN_ACTION_FAMILIES.get(pending_action) == matched_family:
        return pending_action
    for action, family in WANXIN_ACTION_FAMILIES.items():
        if family == matched_family:
            return action
    return pending_action


def _apply_owner_reply_to_current_identity(text, now, matched_family="", result_msg_id=0, reply_to=None):
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))
    parsed = parse_wanxin_text(text, now=now, family=matched_family)
    if not parsed:
        return False
    action = _matching_pending_action(observed, matched_family)
    if result_msg_id:
        observed["last_observed_at"] = float(now)
    if int(getattr(reply_to, "id", 0) or 0) > 0:
        observed["assist"]["last_anchor_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
        observed["assist"]["last_anchor_at"] = float(now)
    _apply_panel_values(observed, parsed.get("values") or {}, now)

    ptype = parsed.get("type")
    if ptype == "unavailable":
        observed["available"] = "no"
        observed["auto_last_error"] = parsed.get("summary") or "缺少婉心封魂前置"
        observed["auto_next_time"] = now + WANXIN_UNAVAILABLE_BACKOFF_SEC
        state["wanxin_enabled"] = False
        _clear_pending(observed)
    elif ptype in {"commission_published", "commission_existing"}:
        commission = observed["commission"]
        commission["id"] = int(parsed.get("commission_id", 0) or 0)
        if ptype == "commission_published" or not commission.get("published_at"):
            commission["published_at"] = float(now)
        commission["owner_username"] = commission.get("owner_username") or _owner_username()
        if int(getattr(reply_to, "id", 0) or 0) > 0:
            commission["publish_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
        observed["auto_last_result"] = f"{'已有' if ptype == 'commission_existing' else '委托已发布'}：{commission['id'] or '未知'}"
        observed["auto_last_error"] = "" if commission["id"] else "委托存在但未解析到ID"
        observed["auto_next_time"] = float(now)
        _clear_pending(observed)
    elif ptype in {"panel", "help"}:
        observed["auto_last_result"] = parsed.get("summary") or "已校准"
        observed["auto_last_error"] = ""
        _clear_pending(observed)
    elif ptype == "commission_invalid":
        _mark_commission_invalid(observed, now, parsed.get("summary") or "咒契失效")
    elif ptype == "cooldown":
        next_time = float(parsed.get("next_time", 0) or now + WANXIN_RECOVERY_RETRY_SEC)
        action = parsed.get("cooldown_action") or action
        _set_cooldown_from_reply(observed, action, next_time)
        observed["auto_last_result"] = f"{WANXIN_ACTION_LABELS.get(action, '婉心动作')}冷却中"
        observed["auto_last_error"] = ""
        _clear_pending(observed)
        _schedule_next(observed, now)
    elif ptype in {"visit_success", "visit_already", "protect_success", "deduce_success"}:
        if ptype in {"visit_success", "visit_already"}:
            action = WANXIN_ACTION_VISIT
        elif ptype == "protect_success":
            action = WANXIN_ACTION_PROTECT
        else:
            action = WANXIN_ACTION_DEDUCE
        _apply_success_cooldown(observed, action, now, parsed)
        observed["auto_last_result"] = parsed.get("summary") or "成功"
        observed["auto_last_error"] = ""
        _clear_pending(observed)
        _schedule_next(observed, now)
    else:
        if ptype == "unknown":
            return False
        observed["auto_last_result"] = parsed.get("summary") or ptype
        observed["auto_last_error"] = ""
        _clear_pending(observed)
        _schedule_next(observed, now)
    _push_recent(observed, now, action or ptype, parsed.get("summary") or ptype, text)
    _set_observed(observed)
    return True


def _apply_to_owner_identity(owner_id, parsed, now, matched_family="", result_msg_id=0):
    if owner_id <= 0 or not has_identity(owner_id):
        return False
    with use_identity(owner_id):
        observed = normalize_wanxin_observation(state.get("wanxin_observation"))
        action = _matching_pending_action(observed, matched_family)
        _apply_panel_values(observed, parsed.get("values") or {}, now)
        ptype = parsed.get("type")
        if ptype == "commission_accepted":
            commission = observed["commission"]
            commission["accepted"] = True
            commission["accepted_at"] = float(now)
            commission["helper_username"] = parsed.get("helper_username") or commission.get("helper_username") or ""
            if result_msg_id:
                commission["accept_msg_id"] = int(result_msg_id)
            observed["auto_last_result"] = f"咒契已成：@{commission['helper_username'] or '阴罗咒师'}"
            observed["auto_last_error"] = ""
            _clear_pending(observed)
            _schedule_next(observed, now)
        elif ptype in {"assist_identify_success", "assist_banner_success", "assist_strip_success", "assist_strip_failed"}:
            if ptype == "assist_identify_success":
                action = WANXIN_ACTION_IDENTIFY
            elif ptype == "assist_banner_success":
                action = WANXIN_ACTION_BANNER
            else:
                action = WANXIN_ACTION_STRIP
            _apply_success_cooldown(observed, action, now, parsed)
            observed["assist"]["last_action"] = action
            observed["assist"]["last_result"] = parsed.get("summary") or "协助成功"
            observed["assist"]["last_error"] = "" if ptype != "assist_strip_failed" else parsed.get("summary") or "剥离咒源失败"
            observed["assist"]["last_contrib_gain"] = int(parsed.get("contrib_gain", 0) or 0)
            observed["auto_last_result"] = parsed.get("summary") or "协助成功"
            observed["auto_last_error"] = "" if ptype != "assist_strip_failed" else parsed.get("summary") or "剥离咒源失败"
            _clear_pending(observed)
            _schedule_next(observed, now)
        elif ptype == "assist_strip_blocked":
            observed["assist"]["next_strip_time"] = now + WANXIN_STRIP_CD_SEC
            observed["assist"]["last_action"] = WANXIN_ACTION_STRIP
            observed["assist"]["last_result"] = "咒源不足，剥离延后"
            observed["assist"]["last_error"] = ""
            observed["auto_last_result"] = "剥离前置不足"
            observed["auto_last_error"] = ""
            _clear_pending(observed)
            _schedule_next(observed, now)
        elif ptype in {"assist_missing_target", "assist_not_yinluo"}:
            action = _matching_pending_action(observed, matched_family)
            delay = 60 * 60 if ptype == "assist_not_yinluo" else 30 * 60
            if action in WANXIN_ASSIST_ACTIONS:
                _set_next_time_for_action(observed, action, now + delay)
            if ptype == "assist_missing_target":
                observed["assist"]["last_anchor_msg_id"] = 0
                observed["assist"]["last_anchor_at"] = 0
            observed["assist"]["last_action"] = action
            observed["assist"]["last_result"] = ""
            observed["assist"]["last_error"] = parsed.get("summary") or ptype
            observed["auto_last_result"] = ""
            observed["auto_last_error"] = parsed.get("summary") or ptype
            observed["auto_next_time"] = now + delay
            _clear_pending(observed)
        elif ptype == "commission_invalid":
            action = _matching_pending_action(observed, matched_family)
            _mark_commission_invalid(observed, now, parsed.get("summary") or "咒契失效")
        elif ptype == "cooldown":
            next_time = float(parsed.get("next_time", 0) or now + WANXIN_RECOVERY_RETRY_SEC)
            action = parsed.get("cooldown_action") or action
            _set_cooldown_from_reply(observed, action, next_time)
            observed["auto_last_result"] = f"{WANXIN_ACTION_LABELS.get(action, '协助动作')}冷却中"
            observed["auto_last_error"] = ""
            _clear_pending(observed)
            _schedule_next(observed, now)
        else:
            return False
        _push_recent(observed, now, action or ptype, parsed.get("summary") or ptype)
        _set_observed(observed)
    return True


async def handle_wanxin_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0):
    family = str(matched_family or "")
    if not family.startswith("wanxin_") and not looks_like_wanxin_text(text):
        return False
    parsed = parse_wanxin_text(text, now=now, family=family)
    if not parsed:
        return False

    owner_username = parsed.get("target_username") or ""
    if owner_username:
        owner_id = _find_identity_by_username(owner_username)
    else:
        owner_id = _find_owner_identity_by_pending(
            family,
            reply_to_msg_id=int(getattr(reply_to, "id", 0) or 0),
            send_as_id=get_current_identity_id(),
        )
    if owner_id:
        handled = _apply_to_owner_identity(owner_id, parsed, float(now), matched_family=family, result_msg_id=result_msg_id)
        if handled:
            close_action_guard_by_family(family, send_as_id=get_current_identity_id(), reason="wanxin_assist_reply", now=now)
            save_state()
            return True

    handled = _apply_owner_reply_to_current_identity(text, float(now), matched_family=family, result_msg_id=result_msg_id, reply_to=reply_to)
    if handled:
        close_action_guard_by_family(family, send_as_id=get_current_identity_id(), reason="wanxin_reply", now=now)
        save_state()
    return handled


def set_wanxin_config(config):
    if not isinstance(config, dict):
        return False, "婉心配置必须是对象。", get_wanxin_ui_state()
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))
    auto_config = normalize_wanxin_auto_config(observed.get("auto_config"))
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else _default_wanxin_assist()
    for key in ("visit_enabled", "protect_enabled", "deduce_enabled", "publish_enabled", "assist_enabled"):
        if key in config:
            auto_config[key] = _normalize_bool(config.get(key), auto_config.get(key, True))
    if "reward_lingshi" in config:
        auto_config["reward_lingshi"] = max(1, min(1_000_000, _safe_int(config.get("reward_lingshi"), 1)))
    if "assist_send_as_id" in config:
        assist["send_as_id"] = max(0, _safe_int(config.get("assist_send_as_id"), assist.get("send_as_id", WANXIN_DEFAULT_ASSIST_SEND_AS_ID)))
    for key in ("identify_enabled", "banner_enabled", "strip_enabled"):
        if key in config:
            assist[key] = _normalize_bool(config.get(key), assist.get(key, False))
    observed["auto_config"] = normalize_wanxin_auto_config(auto_config)
    observed["assist"] = normalize_wanxin_observation({"assist": assist}).get("assist")
    state["wanxin_observation"] = observed
    save_state()
    return True, "已更新婉心封魂策略。", get_wanxin_ui_state()


def get_wanxin_ui_state(now=None):
    now = float(now if now is not None else time.time())
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))
    assist = observed.get("assist") or {}
    commission = observed.get("commission") or {}
    config = normalize_wanxin_auto_config(observed.get("auto_config"))
    return {
        "available": observed.get("available") or "unknown",
        "stage": observed.get("stage") or "",
        "wanxin": int(observed.get("wanxin", 0) or 0),
        "soul_seal": int(observed.get("soul_seal", 0) or 0),
        "moon_soul": int(observed.get("moon_soul", 0) or 0),
        "curse_source": int(observed.get("curse_source", 0) or 0),
        "auto_config": config,
        "auto_next_time": fmt_abs_ts(observed.get("auto_next_time", 0) or 0),
        "auto_last_action": WANXIN_ACTION_LABELS.get(observed.get("auto_last_action"), observed.get("auto_last_action") or ""),
        "auto_last_result": observed.get("auto_last_result") or "",
        "auto_last_error": observed.get("auto_last_error") or "",
        "next_visit_time": fmt_abs_ts(observed.get("next_visit_time", 0) or 0),
        "next_protect_time": fmt_abs_ts(observed.get("next_protect_time", 0) or 0),
        "next_deduce_time": fmt_abs_ts(observed.get("next_deduce_time", 0) or 0),
        "commission": {
            "id": int(commission.get("id", 0) or 0),
            "accepted": bool(commission.get("accepted")),
            "owner_username": commission.get("owner_username") or "",
            "helper_username": commission.get("helper_username") or "",
            "publish_msg_id": int(commission.get("publish_msg_id", 0) or 0),
            "accepted_at": fmt_abs_ts(commission.get("accepted_at", 0) or 0),
        },
        "assist": {
            "send_as_id": int(assist.get("send_as_id", 0) or 0),
            "send_as_label": get_identity_display_name(assist.get("send_as_id", 0)) if has_identity(int(assist.get("send_as_id", 0) or 0)) else str(assist.get("send_as_id", "") or ""),
            "identify_enabled": bool(assist.get("identify_enabled")),
            "banner_enabled": bool(assist.get("banner_enabled")),
            "strip_enabled": bool(assist.get("strip_enabled")),
            "next_identify_time": fmt_abs_ts(assist.get("next_identify_time", 0) or 0),
            "next_banner_time": fmt_abs_ts(assist.get("next_banner_time", 0) or 0),
            "next_strip_time": fmt_abs_ts(assist.get("next_strip_time", 0) or 0),
            "last_anchor_msg_id": int(assist.get("last_anchor_msg_id", 0) or 0),
            "last_action": WANXIN_ACTION_LABELS.get(assist.get("last_action"), assist.get("last_action") or ""),
            "last_result": assist.get("last_result") or "",
            "last_error": assist.get("last_error") or "",
            "last_contrib_gain": int(assist.get("last_contrib_gain", 0) or 0),
        },
        "pending": observed.get("pending") or {},
        "now": fmt_abs_ts(now),
    }


def get_wanxin_status_text():
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))
    commission = observed.get("commission") or {}
    assist = observed.get("assist") or {}
    lines = [
        "🌙 婉心封魂",
        f"- 模块：{'开启' if state.get('wanxin_enabled') else '关闭'}",
        f"- 可用：{observed.get('available') or 'unknown'}",
        f"- 阶段：{observed.get('stage') or '未记录'}",
        f"- 数值：婉心 {observed.get('wanxin', 0)}｜魂封 {observed.get('soul_seal', 0)}｜月魄 {observed.get('moon_soul', 0)}｜咒源 {observed.get('curse_source', 0)}",
        f"- 下次探望：{fmt_abs_ts(observed.get('next_visit_time', 0))}（{fmt_remaining(observed.get('next_visit_time', 0))}）",
        f"- 下次护持：{fmt_abs_ts(observed.get('next_protect_time', 0))}（{fmt_remaining(observed.get('next_protect_time', 0))}）",
        f"- 下次推演：{fmt_abs_ts(observed.get('next_deduce_time', 0))}（{fmt_remaining(observed.get('next_deduce_time', 0))}）",
        f"- 委托：ID {commission.get('id') or '无'}｜{'已接取' if commission.get('accepted') else '未接取'}",
        f"- 阴罗协助：{assist.get('send_as_id') or '未配置'}｜辨咒 {fmt_abs_ts(assist.get('next_identify_time', 0))}｜借幡 {fmt_abs_ts(assist.get('next_banner_time', 0))}｜剥离 {fmt_abs_ts(assist.get('next_strip_time', 0))}",
        f"- 锚点：{assist.get('last_anchor_msg_id') or '无'}",
        f"- 自动调度：{fmt_abs_ts(observed.get('auto_next_time', 0))}（{fmt_remaining(observed.get('auto_next_time', 0))}）",
    ]
    if observed.get("pending"):
        pending = observed["pending"]
        lines.append(f"- 待回复：{WANXIN_ACTION_LABELS.get(pending.get('action'), pending.get('action'))} msg={pending.get('msg_id')} due={fmt_abs_ts(pending.get('reply_due_at', 0))}")
    if observed.get("auto_last_result"):
        lines.append(f"- 最近结果：{observed.get('auto_last_result')}")
    if observed.get("auto_last_error"):
        lines.append(f"- 最近异常：{observed.get('auto_last_error')}")
    return "\n".join(lines)


def schedule_wanxin_initial_check(now=None, *, persist=True):
    now = float(now if now is not None else time.time())
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))
    if float(observed.get("auto_next_time", 0) or 0) <= 0:
        observed["auto_next_time"] = now + WANXIN_CHAIN_STEP_SEC
        _set_observed(observed)
        if persist:
            save_state()


def clear_wanxin_state(*, persist=False):
    state["wanxin_observation"] = _default_wanxin_observation()
    if persist:
        save_state()


__all__ = [
    "WANXIN_DEFAULT_ASSIST_SEND_AS_ID",
    "clear_wanxin_state",
    "get_wanxin_status_text",
    "get_wanxin_ui_state",
    "handle_wanxin_reply",
    "looks_like_wanxin_text",
    "normalize_wanxin_auto_config",
    "normalize_wanxin_observation",
    "parse_wanxin_text",
    "run_wanxin_phaseful_cleanup_scheduler",
    "run_wanxin_global_cleanup_scheduler",
    "run_wanxin_scheduler",
    "schedule_wanxin_initial_check",
    "set_wanxin_config",
]
