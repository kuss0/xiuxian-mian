import asyncio
import copy
import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ..config import (
    CD_BUFFER_SEC,
    CMD_WANXIN_ACCEPT_COMMISSION,
    CMD_WANXIN_ASSIST_BANNER,
    CMD_WANXIN_ASSIST_IDENTIFY,
    CMD_WANXIN_ASSIST_STRIP,
    CMD_WANXIN_CANCEL_COMMISSION,
    CMD_WANXIN_DEDUCE,
    CMD_WANXIN_HELP,
    CMD_WANXIN_MOON_GREET,
    CMD_WANXIN_MOON_JOIN,
    CMD_WANXIN_MOON_SEAL,
    CMD_WANXIN_MOON_STATUS,
    CMD_WANXIN_PROTECT,
    CMD_WANXIN_PUBLISH_COMMISSION,
    CMD_WANXIN_STATUS,
    CMD_WANXIN_VISIT,
    MESSAGES_DIR,
    RETRY_MAX_SEC,
    TZ_LOCAL,
)
from ..message_keys import message_key_parts
from ..message_log_recovery import _read_log_tail_lines, sender_matches_identity
from ..persistence import save_state
from .. import yinluo_accounting as resource_accounting
from ..runtime import classify_game_send_block, clear_pending_by_reply, send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_channel_send_as_health,
    get_game_group_id,
    get_global_enabled,
    get_identity_display_name,
    get_identity_enabled,
    get_identity_account,
    get_identity_ids,
    get_identity_state,
    get_pending_command,
    get_send_as_profile,
    has_identity,
    normalize_sect_name,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key, has_wait_time, parse_wait_time
from ..wanxin_commission_replay import MAX_RECORDS as WANXIN_LOG_MAX_RECORDS, find_commission_evidence, find_native_action_replies
from ..profile_observation import timestamp
from ..resource_accounting import compare_points, valid_point
from ..verified_event import VerifiedGameEvent
from ._phaseful import get_phaseful_summary_risk_reason
from .yinluo import (
    _accept_business_point,
    get_yinluo_sha_recovery_status,
    handle_yinluo_resource_reply,
    recover_yinluo_resources,
    send_owned_yinluo_command,
)


WANXIN_MODULE_NAME = "婉心封魂"
WANXIN_DEFAULT_ASSIST_SEND_AS_ID = 8613500668
WANXIN_REPLY_TIMEOUT_SEC = 90
WANXIN_RECOVERY_RETRY_SEC = 10 * 60
WANXIN_SEND_QUEUE_TIMEOUT_SEC = 90
WANXIN_SEND_QUEUE_RETRY_SEC = 10 * 60
WANXIN_CHAIN_STEP_SEC = 20
WANXIN_PROTECT_CD_SEC = 6 * 3600
WANXIN_DEDUCE_CD_SEC = 8 * 3600
WANXIN_IDENTIFY_CD_SEC = 4 * 3600
WANXIN_BANNER_CD_SEC = 6 * 3600
WANXIN_STRIP_CD_SEC = 8 * 3600
WANXIN_RESOURCE_RECOVERY_RETRY_SEC = 15 * 60
WANXIN_BANNER_SHA_COST = 80
WANXIN_STRIP_SHA_COST = 120
WANXIN_MOON_GREET_CD_SEC = 24 * 3600
WANXIN_MOON_SEAL_CD_SEC = 8 * 3600
WANXIN_MOON_JOIN_CD_SEC = 24 * 3600
WANXIN_MOON_VOYAGE_AFFINITY_RESERVE = 160
WANXIN_MOON_SEAL_AFFINITY_COST = 24
WANXIN_MOON_SEAL_MIN_AFFINITY = WANXIN_MOON_VOYAGE_AFFINITY_RESERVE + WANXIN_MOON_SEAL_AFFINITY_COST
WANXIN_ANCHOR_MAX_AGE_SEC = 24 * 3600
WANXIN_UNAVAILABLE_BACKOFF_SEC = 24 * 3600
WANXIN_PHASEFUL_DEFER_SEC = 5 * 60
WANXIN_COMMISSION_TTL_SEC = 24 * 3600
WANXIN_COMMISSION_CANCEL_RETRY_SEC = 10 * 60
WANXIN_LOG_LOOKBACK_SEC = 26 * 3600
WANXIN_LOG_MAX_BYTES = 1024 * 1024
# The current game panel caps 咒源 at 120. Keep this in one place so a game
# balance change only needs one calibration update.
WANXIN_CURSE_SOURCE_CAP = 120
_WANXIN_INFLIGHT = {}
_WANXIN_OBSERVATION_UNSET = object()

WANXIN_ACTION_VISIT = "visit"
WANXIN_ACTION_PROTECT = "protect"
WANXIN_ACTION_DEDUCE = "deduce"
WANXIN_ACTION_PUBLISH = "publish"
WANXIN_ACTION_CANCEL = "cancel"
WANXIN_ACTION_ACCEPT = "accept"
WANXIN_ACTION_IDENTIFY = "identify"
WANXIN_ACTION_BANNER = "banner"
WANXIN_ACTION_STRIP = "strip"
WANXIN_ACTION_STATUS = "status"
WANXIN_ACTION_MOON_STATUS = "moon_status"
WANXIN_ACTION_MOON_GREET = "moon_greet"
WANXIN_ACTION_MOON_SEAL = "moon_seal"
WANXIN_ACTION_MOON_JOIN = "moon_join"

WANXIN_SELF_ACTIONS = (
    WANXIN_ACTION_MOON_GREET,
    WANXIN_ACTION_VISIT,
    WANXIN_ACTION_PROTECT,
    WANXIN_ACTION_DEDUCE,
    WANXIN_ACTION_MOON_SEAL,
    WANXIN_ACTION_MOON_JOIN,
)
WANXIN_ASSIST_ACTIONS = (WANXIN_ACTION_IDENTIFY, WANXIN_ACTION_BANNER, WANXIN_ACTION_STRIP)
WANXIN_RESOURCE_ACTIONS = {WANXIN_ACTION_BANNER, WANXIN_ACTION_STRIP}
WANXIN_COMMISSION_ACTIONS = {
    WANXIN_ACTION_PUBLISH, WANXIN_ACTION_CANCEL, WANXIN_ACTION_ACCEPT,
    WANXIN_ACTION_IDENTIFY, WANXIN_ACTION_BANNER, WANXIN_ACTION_STRIP,
}
WANXIN_AFFINITY_SPENDING_ACTIONS = {WANXIN_ACTION_MOON_SEAL, WANXIN_ACTION_MOON_JOIN}
WANXIN_RESOURCE_REPLY_TYPES = {
    "assist_banner_success", "assist_strip_success", "assist_strip_failed", "assist_strip_blocked",
    "assist_banner_resource_blocked", "assist_strip_resource_blocked",
}

WANXIN_ACTION_COMMANDS = {
    WANXIN_ACTION_STATUS: CMD_WANXIN_STATUS,
    WANXIN_ACTION_VISIT: CMD_WANXIN_VISIT,
    WANXIN_ACTION_PROTECT: CMD_WANXIN_PROTECT,
    WANXIN_ACTION_DEDUCE: CMD_WANXIN_DEDUCE,
    WANXIN_ACTION_PUBLISH: CMD_WANXIN_PUBLISH_COMMISSION,
    WANXIN_ACTION_CANCEL: CMD_WANXIN_CANCEL_COMMISSION,
    WANXIN_ACTION_ACCEPT: CMD_WANXIN_ACCEPT_COMMISSION,
    WANXIN_ACTION_IDENTIFY: CMD_WANXIN_ASSIST_IDENTIFY,
    WANXIN_ACTION_BANNER: CMD_WANXIN_ASSIST_BANNER,
    WANXIN_ACTION_STRIP: CMD_WANXIN_ASSIST_STRIP,
    WANXIN_ACTION_MOON_STATUS: CMD_WANXIN_MOON_STATUS,
    WANXIN_ACTION_MOON_GREET: CMD_WANXIN_MOON_GREET,
    WANXIN_ACTION_MOON_SEAL: CMD_WANXIN_MOON_SEAL,
    WANXIN_ACTION_MOON_JOIN: CMD_WANXIN_MOON_JOIN,
}

WANXIN_ACTION_LABELS = {
    WANXIN_ACTION_STATUS: "查婉心",
    WANXIN_ACTION_VISIT: "探望南宫婉",
    WANXIN_ACTION_PROTECT: "护持神魂",
    WANXIN_ACTION_DEDUCE: "推演封魂咒",
    WANXIN_ACTION_PUBLISH: "发布解咒委托",
    WANXIN_ACTION_CANCEL: "取消解咒委托",
    WANXIN_ACTION_ACCEPT: "接取解咒委托",
    WANXIN_ACTION_IDENTIFY: "辨认咒纹",
    WANXIN_ACTION_BANNER: "借幡镇魂",
    WANXIN_ACTION_STRIP: "剥离咒源",
    WANXIN_ACTION_MOON_STATUS: "婉影状态",
    WANXIN_ACTION_MOON_GREET: "婉影问安",
    WANXIN_ACTION_MOON_SEAL: "同参封魂",
    WANXIN_ACTION_MOON_JOIN: "月下合参",
}

WANXIN_ACTION_FAMILIES = {
    WANXIN_ACTION_STATUS: "wanxin_panel",
    WANXIN_ACTION_VISIT: "wanxin_visit",
    WANXIN_ACTION_PROTECT: "wanxin_protect",
    WANXIN_ACTION_DEDUCE: "wanxin_deduce",
    WANXIN_ACTION_PUBLISH: "wanxin_commission",
    WANXIN_ACTION_CANCEL: "wanxin_cancel",
    WANXIN_ACTION_ACCEPT: "wanxin_accept",
    WANXIN_ACTION_IDENTIFY: "wanxin_assist_identify",
    WANXIN_ACTION_BANNER: "wanxin_assist_banner",
    WANXIN_ACTION_STRIP: "wanxin_assist_strip",
    WANXIN_ACTION_MOON_STATUS: "wanxin_moon_panel",
    WANXIN_ACTION_MOON_GREET: "wanxin_moon_greet",
    WANXIN_ACTION_MOON_SEAL: "wanxin_moon_seal",
    WANXIN_ACTION_MOON_JOIN: "wanxin_moon_join",
}

WANXIN_PANEL_FIELDS = {"婉心": "wanxin", "魂封": "soul_seal", "月魄": "moon_soul", "咒源": "curse_source"}
WANXIN_RESULT_HEADERS = (
    "婉心封魂", "婉心封魂指令", "婉影觉醒", "月影同参", "婉影问安",
    "同参封魂", "月下合参", "推演封魂咒", "探望南宫婉", "护持神魂",
    "解咒委托已发布", "咒契协定已成", "阴罗辨咒", "借幡镇魂", "剥离咒源失败", "剥离咒源成功",
)
RE_WANXIN_RESULT_HEADER = re.compile("【(?:" + "|".join(WANXIN_RESULT_HEADERS) + ")】")
RE_WANXIN_VALUE = re.compile(r"(婉心|魂封|月魄|咒源)(?=[:：\s0-9]|$)(.*)")
RE_WANXIN_WAIT = re.compile(r"请在\s*([^，。；;\n]+?)\s*后再[^，。；;\n]*")
RE_WANXIN_DURATION = re.compile(r"(?:[0-9]{1,3}\s*小时\s*)?(?:[0-9]{1,3}\s*分钟?\s*)?(?:[0-9]{1,3}\s*秒\s*)?")
RE_COMMISSION_ID = re.compile(r"(?m)^[ \t]*委托[ \t]*ID[:：][ \t]*([^\n]*)$")
RE_EXISTING_COMMISSION_ID = re.compile(
    r"你已有进行中的解咒委托[（(]\s*ID[:：]\s*([0-9]{1,19})\s*[）)][，,]不可重复发布[。.]?"
)
RE_ACCEPT_CONTRACT = re.compile(
    r"阴罗宗弟子[ \t]+@(?P<helper>[A-Za-z0-9_]{1,32})[ \t]+已接取[ \t]+"
    r"@(?P<owner>[A-Za-z0-9_]{1,32})[ \t]+的解咒委托[。.]"
)
RE_IDENTIFY_CONTRACT = re.compile(
    r"@(?P<helper>[A-Za-z0-9_]{1,32})[ \t]+(?:以阴罗秘法辨认封魂咒纹[，,][ \t]*)?"
    r"替[ \t]+@(?P<owner>[A-Za-z0-9_]{1,32})[ \t]+锁定咒源[。.]"
)
RE_TARGET_USER = re.compile(r"替\s*@(?P<owner>[\w\d_]+)|@(?P<owner2>[\w\d_]+)\s*魂封")
RE_SOURCE_GAIN = re.compile(r"咒源\s*\+(?P<gain>\d+)")
RE_SEAL_DOWN = re.compile(r"魂封\s*-(?P<down>\d+)")
RE_MOON_GAIN = re.compile(r"月魄\s*\+(?P<gain>\d+)")
RE_CONTRIB_GAIN = re.compile(r"(?:咒师)?贡献\s*\+(?P<gain>\d+)")
RE_ASSIST_SHA_COST = re.compile(r"(?:幡面煞气被削去|阴罗幡煞气被吞去)\s*(?P<amount>\d+)\s*点")
RE_ASSIST_SHA_REQUIRED = re.compile(r"(?P<action>借幡镇魂|剥离咒源)至少需要\s*(?P<amount>\d+)\s*点煞气")


def _entry_ts(value):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    if raw.endswith(" UTC+8"):
        raw = raw[:-6]
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return 0.0


def _iter_message_log_entries_between(start_ts, end_ts):
    start_ts, end_ts = timestamp(start_ts), timestamp(end_ts)
    if not start_ts or not end_ts or start_ts > end_ts:
        return
    start_ts = max(start_ts, end_ts - WANXIN_LOG_LOOKBACK_SEC)
    try:
        start = datetime.fromtimestamp(start_ts, TZ_LOCAL).date()
        end = datetime.fromtimestamp(end_ts, TZ_LOCAL).date()
    except (ValueError, OverflowError, OSError):
        return
    day = start
    entries = []
    while day <= end:
        log_path = Path(MESSAGES_DIR) / f"{day.isoformat()}.log"
        for line in _read_log_tail_lines(log_path, max_bytes=WANXIN_LOG_MAX_BYTES):
            try:
                entry = json.loads(line)
            except (TypeError, ValueError, RecursionError):
                continue
            if not isinstance(entry, dict):
                continue
            ts = _entry_ts(entry.get("ts"))
            if start_ts <= ts <= end_ts:
                entries.append((entry, ts))
                if len(entries) > WANXIN_LOG_MAX_RECORDS:
                    return
        if day == end:
            break
        day += timedelta(days=1)
    yield from entries


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
        "panel_observed_at": 0,
        "next_visit_time": 0,
        "next_protect_time": 0,
        "next_deduce_time": 0,
        "moon_awakened": False,
        "next_moon_greet_time": 0,
        "next_moon_seal_time": 0,
        "next_moon_join_time": 0,
        "last_visit_day": "",
        "auto_next_time": 0,
        "auto_last_action": "",
        "auto_last_result": "",
        "auto_last_error": "",
        "auto_config": _default_wanxin_auto_config(),
        "pending": {},
        "unresolved_actions": {},
        "recovery_next_time": 0,
        "reply_points": {},
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
        "moon_greet_enabled": True,
        "moon_seal_enabled": False,
        "moon_join_enabled": False,
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
        "claimed_elsewhere": False,
        "claim_helper_username": "",
        "cancel_due_at": 0,
        "cancel_msg_id": 0,
    }


def _default_wanxin_assist():
    return {
        "send_as_id": WANXIN_DEFAULT_ASSIST_SEND_AS_ID,
        "identify_enabled": True,
        "banner_enabled": True,
        "strip_enabled": True,
        "next_identify_time": 0,
        "next_banner_time": 0,
        "next_strip_time": 0,
        "helper_next_identify_time": 0,
        "helper_next_banner_time": 0,
        "helper_next_strip_time": 0,
        "identified_commission_id": 0,
        "bannered_commission_id": 0,
        "last_anchor_msg_id": 0,
        "last_anchor_at": 0,
        "last_sha_cost_msg_id": 0,
        "last_sha_cost_at": 0,
        "last_sha_cost_action": "",
        "last_sha_cost_target": "",
        "last_sha_cost_amount": 0,
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
    for key in (
        "visit_enabled", "protect_enabled", "deduce_enabled", "publish_enabled", "assist_enabled",
        "moon_greet_enabled", "moon_seal_enabled", "moon_join_enabled",
    ):
        config[key] = _normalize_bool(config.get(key), _default_wanxin_auto_config()[key])
    reward = _safe_int(config.get("reward_lingshi"), 1)
    config["reward_lingshi"] = max(1, min(1_000_000, reward))
    return config


def _valid_wanxin_reply_point(value):
    if not isinstance(value, dict) or type(value.get("handled")) is not bool:
        return False
    fields = {"point", "command_point", "root", "fingerprint", "handled"}
    if value["handled"]:
        fields.update({"affinity_delta", "completed_point", "type", "commission_id"})
    if value.keys() != fields:
        return False
    point, command = value["point"], value["command_point"]
    if (not valid_point(point, telegram_only=True) or not valid_point(command, telegram_only=True)
            or type(value["root"]) is not int or value["root"] != command["evidence"]["msg_id"]
            or command["evidence"]["edited"] or command["evidence"]["chat_id"] != point["evidence"]["chat_id"]
            or not value["root"] < point["evidence"]["msg_id"] or command["at"] > point["at"]
            or not isinstance(value["fingerprint"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["fingerprint"])):
        return False
    if not value["handled"]:
        return True
    completed = value["completed_point"]
    return bool(
        valid_point(completed, telegram_only=True)
        and completed["evidence"]["chat_id"] == point["evidence"]["chat_id"]
        and command["at"] <= completed["at"] <= point["at"]
        and value["root"] < completed["evidence"]["msg_id"]
        and type(value["affinity_delta"]) is int and abs(value["affinity_delta"]) < 2 ** 63
        and type(value["commission_id"]) is int and 0 <= value["commission_id"] < 2 ** 63
        and isinstance(value["type"], str) and 0 < len(value["type"]) <= 64
    )


def wanxin_affinity_snapshot_is_stale(observed, observed_at, *, now, observation_point=None):
    """Use retained receipts only to reject an older snapshot, never to grant a spend."""
    at, processed_at = timestamp(observed_at), timestamp(now)
    points = observed.get("reply_points") if isinstance(observed, dict) else None
    if not isinstance(points, dict) or not 0 < at <= processed_at:
        return False
    for action, outcome in (
        (WANXIN_ACTION_MOON_STATUS, "moon_panel"),
        (WANXIN_ACTION_MOON_GREET, "moon_greet_success"),
        (WANXIN_ACTION_MOON_SEAL, "moon_seal_success"),
    ):
        receipt = points.get(action)
        if (not _valid_wanxin_reply_point(receipt) or not receipt["handled"]
                or receipt["type"] != outcome or receipt["point"]["at"] > processed_at + 1):
            continue
        point = receipt["command_point"] if action == WANXIN_ACTION_MOON_STATUS else receipt["point"]
        if at < point["at"]:
            return True
        if (at == point["at"] and valid_point(observation_point, telegram_only=True)
                and observation_point["at"] == at):
            # An edited result can lose same-second order; its original command
            # still proves that an earlier read cannot contain this operation.
            if (compare_points(observation_point, point) == -1
                    or compare_points(observation_point, receipt["command_point"]) == -1):
                return True
    return False


def wanxin_affinity_read_covers(observed, completed_at, *, now):
    """Only an original later absolute read can reconcile a known owned gain."""
    at, processed_at = timestamp(completed_at), timestamp(now)
    points = observed.get("reply_points") if isinstance(observed, dict) else None
    receipt = points.get(WANXIN_ACTION_MOON_STATUS) if isinstance(points, dict) else None
    return bool(
        0 < at <= processed_at and _valid_wanxin_reply_point(receipt)
        and receipt["handled"] and receipt["type"] == "moon_panel"
        and at < receipt["command_point"]["at"] <= receipt["point"]["at"] <= processed_at + 1
    )


def _normalize_wanxin_pending(pending):
    if not isinstance(pending, dict) or not pending:
        return {}
    cleaned = {
        "action": str(pending.get("action") or "").strip(),
        "family": str(pending.get("family") or "").strip(),
        "msg_id": max(0, _safe_int(pending.get("msg_id"), 0)),
        "send_as_id": max(0, _safe_int(pending.get("send_as_id"), 0)),
        "reply_to_msg_id": max(0, _safe_int(pending.get("reply_to_msg_id"), 0)),
        "sent_at": timestamp(pending.get("sent_at")),
        "reply_due_at": timestamp(pending.get("reply_due_at")),
        "chat_id": _safe_int(pending.get("chat_id"), 0),
        "resource_op_id": str(pending.get("resource_op_id") or ""),
        "op_id": str(pending.get("op_id") or ""),
        "status": str(pending.get("status") or "sent"),
        "account_id": max(0, _safe_int(pending.get("account_id"))),
        "owner_id": max(0, _safe_int(pending.get("owner_id"))),
        "owner_account_id": max(0, _safe_int(pending.get("owner_account_id"))),
        "command": str(pending.get("command") or ""),
        "started_at": timestamp(pending.get("started_at")),
        "commission_id": max(0, _safe_int(pending.get("commission_id"))),
        "commission_published_at": timestamp(pending.get("commission_published_at")),
        "commission_accepted_at": timestamp(pending.get("commission_accepted_at")),
    }
    integer_fields = ("msg_id", "send_as_id", "chat_id", "account_id", "owner_id", "owner_account_id", "commission_id")
    if any(field in pending and type(pending[field]) is not int for field in integer_fields):
        cleaned["status"] = "legacy_unknown"
    if not cleaned["action"] or cleaned["msg_id"] <= 0:
        if not cleaned["op_id"] or cleaned["status"] not in {"sending", "unknown"}:
            cleaned["status"] = "legacy_unknown"
    return cleaned


def _can_isolate_wanxin_pending(pending):
    if (not isinstance(pending, dict) or not isinstance(pending.get("status"), str)
            or pending["status"] not in {"sent", "unknown"}):
        return False
    action = pending.get("action")
    if (not isinstance(action, str) or action not in WANXIN_ACTION_FAMILIES
            or pending.get("family") != WANXIN_ACTION_FAMILIES[action]):
        return False
    if (any(type(pending.get(field)) is not int or not 0 < pending[field] < 2 ** 63
            for field in ("send_as_id", "account_id", "owner_id", "owner_account_id"))
            or type(pending.get("chat_id")) is not int or not 0 < abs(pending["chat_id"]) < 2 ** 63
            or type(pending.get("msg_id")) is not int or not 0 <= pending["msg_id"] < 2 ** 63
            or timestamp(pending.get("started_at")) <= 0
            or not isinstance(pending.get("op_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", pending["op_id"])
            or not isinstance(pending.get("command"), str) or len(pending["command"]) > 256):
        return False
    if pending["msg_id"] and not timestamp(pending.get("sent_at")):
        return False
    if action in WANXIN_RESOURCE_ACTIONS:
        command = resource_accounting.parse_yinluo_resource_command(pending["command"])
        return bool(command and command.action == f"assist_{action}" and pending.get("resource_op_id") == pending["op_id"])
    command = _parse_wanxin_command(pending["command"])
    return command is not None and command[0] == action


def _wanxin_pending_operations(observed):
    active = observed.get("pending")
    held = observed.get("unresolved_actions")
    return ([active] if isinstance(active, dict) and active else []) + (
        [item for item in held.values() if isinstance(item, dict)] if isinstance(held, dict) else []
    )


def _unresolved_wanxin_blocks(observed, action):
    if observed.get("unresolved_invalid"):
        return True
    held = observed.get("unresolved_actions") or {}
    for item in held.values():
        if (not _can_isolate_wanxin_pending(item)
                or item["owner_id"] != get_current_identity_id()
                or not has_identity(item["send_as_id"])
                or get_identity_account(item["owner_id"]) != item["owner_account_id"]
                or get_identity_account(item["send_as_id"]) != item["account_id"]):
            return True
    if action in held:
        return True
    return bool(
        action in WANXIN_COMMISSION_ACTIONS and WANXIN_COMMISSION_ACTIONS.intersection(held)
        or action in WANXIN_AFFINITY_SPENDING_ACTIONS and WANXIN_AFFINITY_SPENDING_ACTIONS.intersection(held)
    )


def _remove_wanxin_pending_operation(observed, pending):
    if pending and observed.get("pending") == pending:
        observed["pending"] = {}
    held = observed.get("unresolved_actions", {})
    if pending and isinstance(held, dict) and held.get(pending.get("action")) == pending:
        held.pop(pending["action"])


def normalize_wanxin_observation(value=_WANXIN_OBSERVATION_UNSET):
    observed = copy.deepcopy(_default_wanxin_observation())
    if value is _WANXIN_OBSERVATION_UNSET:
        value = {}
    if isinstance(value, dict):
        observed.update(copy.deepcopy(value))
        if any(key in value for key in ("invalid", "raw_json", "raw_bytes_hex", "invalid_observation")):
            observed["unresolved_invalid"] = True
    else:
        # An explicit invalid record is not the no-argument default factory.
        observed["unresolved_invalid"] = True
        observed["invalid_observation"] = copy.deepcopy(value)
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
        "panel_observed_at",
        "next_visit_time",
        "next_protect_time",
        "next_deduce_time",
        "next_moon_greet_time",
        "next_moon_seal_time",
        "next_moon_join_time",
        "auto_next_time",
        "recovery_next_time",
    ):
        observed[key] = max(0.0, _safe_float(observed.get(key), 0))
    observed["moon_awakened"] = _normalize_bool(observed.get("moon_awakened"), False)

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
    commission["claimed_elsewhere"] = _normalize_bool(commission.get("claimed_elsewhere"), False)
    commission["cancel_due_at"] = max(0.0, _safe_float(commission.get("cancel_due_at"), 0))
    commission["cancel_msg_id"] = max(0, _safe_int(commission.get("cancel_msg_id"), 0))
    for key in ("owner_username", "helper_username", "claim_helper_username"):
        commission[key] = str(commission.get(key) or "").strip().lstrip("@")
    observed["commission"] = commission

    assist = _default_wanxin_assist()
    if isinstance(observed.get("assist"), dict):
        assist.update(observed["assist"])
    assist["send_as_id"] = max(0, _safe_int(assist.get("send_as_id"), WANXIN_DEFAULT_ASSIST_SEND_AS_ID))
    for key in ("identify_enabled", "banner_enabled", "strip_enabled"):
        assist[key] = _normalize_bool(assist.get(key), _default_wanxin_assist()[key])
    for key in (
        "next_identify_time",
        "next_banner_time",
        "next_strip_time",
        "helper_next_identify_time",
        "helper_next_banner_time",
        "helper_next_strip_time",
        "last_anchor_at",
        "last_sha_cost_at",
    ):
        assist[key] = max(0.0, _safe_float(assist.get(key), 0))
    for key in ("identified_commission_id", "bannered_commission_id"):
        assist[key] = max(0, _safe_int(assist.get(key), 0))
    for key in ("last_anchor_msg_id", "last_sha_cost_msg_id", "last_sha_cost_amount", "last_contrib_gain"):
        assist[key] = max(0, _safe_int(assist.get(key), 0))
    for key in ("last_sha_cost_action", "last_sha_cost_target", "last_action", "last_result", "last_error"):
        assist[key] = str(assist.get(key) or "").strip()
    observed["assist"] = assist

    raw_pending = observed.get("pending")
    observed["pending"] = _normalize_wanxin_pending(raw_pending)
    if not isinstance(raw_pending, dict) or any(
        key in raw_pending and not isinstance(raw_pending[key], str)
        for key in ("action", "family", "status", "command", "op_id", "resource_op_id")
    ):
        observed["unresolved_invalid"] = True
        observed.setdefault("invalid_pending", copy.deepcopy(raw_pending))
    held = observed.get("unresolved_actions")
    if not isinstance(held, dict) or any(
        key not in WANXIN_ACTION_FAMILIES or not _can_isolate_wanxin_pending(item) or item["action"] != key
        for key, item in held.items()
    ):
        observed["unresolved_invalid"] = True
    else:
        observed["unresolved_actions"] = {key: _normalize_wanxin_pending(item) for key, item in held.items()}
        operations = _wanxin_pending_operations(observed)
        if any(
            left.get("action") == right.get("action") or _same_wanxin_operation(left, right)
            or (left["msg_id"] > 0 and left["chat_id"] != 0
                and (left["chat_id"], left["msg_id"]) == (right["chat_id"], right["msg_id"]))
            for index, left in enumerate(operations) for right in operations[index + 1:]
        ):
            observed["unresolved_invalid"] = True
    points = observed.get("reply_points")
    if (not isinstance(points, dict) or any(key not in WANXIN_ACTION_FAMILIES for key in points)
            or any(not _valid_wanxin_reply_point(item) for item in points.values())):
        observed["reply_points_invalid"] = True
        points = points if isinstance(points, dict) else {}
    observed["reply_points"] = {key: value for key, value in points.items() if key in WANXIN_ACTION_FAMILIES}

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


def _capture_wanxin_owner(identity_id=None):
    identity_id = int(identity_id or get_current_identity_id() or 0)
    if not has_identity(identity_id):
        return None
    return identity_id, get_identity_state(identity_id), get_identity_account(identity_id)


def _wanxin_owner_current(owner, *, enabled=False):
    return bool(
        owner and has_identity(owner[0]) and get_identity_state(owner[0]) is owner[1]
        and get_identity_account(owner[0]) == owner[2]
        and (not enabled or (owner[2] > 0 and get_identity_enabled(owner[0])
                            and owner[1].get("wanxin_enabled") and get_global_enabled()))
    )


def _commit_wanxin_observation(owner, before, observed, *, completed_source=None, identity_updates=None):
    if not _wanxin_owner_current(owner) or owner[1].get("wanxin_observation") != before:
        return False
    updates = identity_updates or {}
    previous_fields = {key: copy.deepcopy(owner[1][key]) for key in updates if key in owner[1]}
    actor = _capture_wanxin_owner(completed_source["actor_id"]) if completed_source and completed_source["actor_id"] else None
    previous_pending = copy.deepcopy(actor[1].get("pending_tasks")) if actor else None

    def rollback():
        owner[1]["wanxin_observation"] = copy.deepcopy(before)
        for key in updates:
            if key in previous_fields:
                owner[1][key] = previous_fields[key]
            else:
                owner[1].pop(key, None)
        if actor:
            actor[1]["pending_tasks"] = previous_pending

    with use_identity(owner[0]):
        _set_observed(observed)
        owner[1].update(updates)
        if completed_source is not None:
            _clear_wanxin_source_pending(completed_source)
        try:
            if save_state() is False:
                rollback()
                return False
        except Exception:
            rollback()
            raise
    return True


def _refresh_wanxin_observation(owner, observed):
    if not _wanxin_owner_current(owner):
        return False
    current = normalize_wanxin_observation(owner[1].get("wanxin_observation"))
    observed.clear()
    observed.update(current)
    return True


def _owner_username(send_as_id=None):
    profile = get_send_as_profile(send_as_id)
    return str(profile.get("username") or "").strip().lstrip("@")


def _identity_username_keys(send_as_id):
    profile = get_send_as_profile(send_as_id)
    values = [profile.get("username")]
    aliases = profile.get("username_aliases")
    if isinstance(aliases, (list, tuple, set)):
        values.extend(aliases)
    return {
        str(value or "").strip().lstrip("@").casefold()
        for value in values
        if str(value or "").strip().lstrip("@")
    }


def _parse_wanxin_command(text):
    if not isinstance(text, str) or len(text) > 256:
        return None
    words = text.strip().split()
    if not words:
        return None
    actions = {command: action for action, command in WANXIN_ACTION_COMMANDS.items()}
    actions[CMD_WANXIN_HELP] = WANXIN_ACTION_STATUS
    action = actions.get(words[0])
    if action in WANXIN_RESOURCE_ACTIONS or action is None:
        return None
    if action in {WANXIN_ACTION_PUBLISH, WANXIN_ACTION_ACCEPT}:
        if len(words) != 2 or not re.fullmatch(r"[0-9]{1,19}", words[1]) or not 0 < int(words[1]) < 2 ** 63:
            return None
        return action, int(words[1])
    if action == WANXIN_ACTION_IDENTIFY:
        if len(words) != 2 or not re.fullmatch(r"@[A-Za-z0-9_]{1,32}", words[1]):
            return None
        return action, words[1][1:].casefold()
    return (action, None) if len(words) == 1 else None


def _native_wanxin_source(event, now):
    trust = resource_accounting.event_trust(now)
    if (not isinstance(event, VerifiedGameEvent) or event.event_type not in {"message", "edit"}
            or type(event.chat_id) is not int or event.chat_id not in trust["game_chats"]
            or type(event.sender_id) is not int or event.sender_id not in trust["game_bots"]
            or not isinstance(event.reply_context, dict)):
        return None
    context = event.reply_context
    command = _parse_wanxin_command(context.get("reply_to_command"))
    root = context.get("reply_to_msg_id")
    sender = context.get("reply_to_sender_id")
    at = timestamp(context.get("reply_to_server_at"))
    point = {"at": event.server_event_at, "evidence": {
        "source": "telegram", "chat_id": event.chat_id, "msg_id": event.msg_id, "edited": event.is_edited_delivery,
    }}
    if (command is None or context.get("reply_to_command_edited") is not False
            or not valid_point(point, telegram_only=True) or timestamp(now) <= 0
            or not 0 < at <= event.server_event_at <= now + 1
            or type(root) is not int or not 0 < root < event.msg_id
            or type(sender) is not int or not sender or sender in trust["game_bots"]
            or type(event.reply_to_sender_id) is not int or event.reply_to_sender_id != sender
            or type(event.root_msg_id) is not int or event.root_msg_id not in {0, root}
            or any(key in context and (type(context[key]) is not int or context[key] != value)
                   for key, value in (("chat_id", event.chat_id), ("root_msg_id", root)))):
        return None
    actors = [identity for identity in trust["identity_accounts"] if sender_matches_identity(sender, identity)]
    if len(actors) > 1 or (not actors and command[0] != WANXIN_ACTION_ACCEPT):
        return None
    actor = actors[0] if actors else 0
    account = trust["identity_accounts"].get(actor, 0)
    if (type(event.identity_id) is not int or event.identity_id not in {0, actor}
            or (context.get("send_as_id") is not None and (type(context["send_as_id"]) is not int or context["send_as_id"] not in {0, actor}))
            or ("account_id" in context and (type(context["account_id"]) is not int or context["account_id"] != account))
            or (actor and account <= 0)):
        return None
    return {
        "action": command[0], "argument": command[1], "actor_id": actor, "account_id": account,
        "command": context["reply_to_command"].strip(), "chat_id": event.chat_id, "msg_id": root,
        "command_at": at, "point": point, "native_sender": sender,
    }


def _wanxin_source_owner(source, parsed):
    action = source["action"]
    if action not in {WANXIN_ACTION_ACCEPT, WANXIN_ACTION_IDENTIFY}:
        return source["actor_id"]
    target = str(parsed.get("target_username") or "").strip().lstrip("@").casefold()
    if action == WANXIN_ACTION_IDENTIFY:
        if target and target != source["argument"]:
            aliases = resource_accounting.identity_usernames()
            matching = [identity for identity, names in aliases.items()
                        if target in names and source["argument"] in names]
            if len(matching) != 1:
                return 0
        target = source["argument"]
    owners = []
    for identity in get_identity_ids():
        if target and target not in _identity_username_keys(identity):
            continue
        observed = normalize_wanxin_observation(get_identity_state(identity).get("wanxin_observation"))
        if action == WANXIN_ACTION_ACCEPT and observed["commission"]["id"] != source["argument"]:
            continue
        if action == WANXIN_ACTION_IDENTIFY and observed["assist"]["send_as_id"] != source["actor_id"]:
            if (observed.get("unresolved_invalid") or observed.get("reply_points_invalid")
                    or not any(_pending_matches_wanxin_source(item, source, identity)
                               for item in _wanxin_pending_operations(observed))):
                continue
        owners.append(identity)
    return owners[0] if len(owners) == 1 else 0


def _pending_matches_wanxin_source(pending, source, owner_id):
    if (not pending or pending["action"] != source["action"]
            or pending["send_as_id"] != source["actor_id"]
            or pending.get("account_id", 0) not in {0, source["account_id"]}
            or pending.get("owner_id", 0) not in {0, owner_id}
            or pending.get("owner_account_id", 0) not in {0, get_identity_account(owner_id)}
            or source["command_at"] < timestamp(pending.get("started_at")) - 1
            or pending.get("command") and _parse_wanxin_command(pending["command"]) != _parse_wanxin_command(source["command"])):
        return False
    if pending["msg_id"] > 0:
        return pending["msg_id"] == source["msg_id"] and pending["chat_id"] == source["chat_id"]
    if not pending.get("op_id") or not has_identity(source["actor_id"]):
        return False
    actor = get_identity_state(source["actor_id"])
    for key, item in actor.get("pending_tasks", {}).items():
        if not isinstance(item, dict) or item.get("op_id") != pending["op_id"] or item.get("cmd") != source["command"]:
            continue
        try:
            chat_id, msg_id = message_key_parts(key, item)
        except (ValueError, TypeError, OverflowError):
            continue
        if (chat_id, msg_id) == (source["chat_id"], source["msg_id"]):
            return item.get("account_id", source["account_id"]) == source["account_id"]
    return False


def _wanxin_reply_type_matches(action, parsed):
    if parsed.get("type") == "unavailable":
        return action not in {WANXIN_ACTION_ACCEPT, WANXIN_ACTION_IDENTIFY}
    if parsed.get("type") == "cooldown":
        return parsed.get("cooldown_action") in {"", action}
    expected = {
        "status": {"panel", "help"}, "moon_status": {"moon_panel", "moon_awakened", "moon_unavailable"},
        "visit": {"visit_success", "visit_already"}, "protect": {"protect_success"}, "deduce": {"deduce_success"},
        "moon_greet": {"moon_greet_success", "moon_greet_already", "moon_unavailable"},
        "moon_seal": {"moon_seal_success", "moon_unavailable"},
        "moon_join": {"moon_join_success", "moon_join_blocked", "moon_unavailable"},
        "publish": {"commission_published", "commission_existing"},
        "cancel": {"commission_cancelled", "commission_cancel_blocked"},
        "accept": {"commission_accepted", "commission_claimed_elsewhere", "commission_expired", "commission_invalid"},
        "identify": {"assist_identify_success", "assist_missing_target", "assist_not_yinluo", "commission_invalid"},
    }
    return parsed.get("type") in expected.get(action, set())


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
        "委托不存在或已被他人接取",
        "委托已被接取",
        "委托已过期",
        "可取消的解咒委托",
        "咒契协定",
        "阴罗辨咒",
        "借幡镇魂",
        "剥离咒源",
        "剥离咒源失败",
        "咒源尚未辨明",
        "探望南宫婉",
        "探望过南宫婉",
        "护持神魂",
        "月殿余咒",
        "阴罗咒源",
        "玄冰丹方",
        "婉影觉醒",
        "婉影共鸣",
        "月影同参",
        "婉影问安",
        "同参封魂",
        "月下合参",
        "北冥小极宫",
        "北冥寒令",
        "封魂咒纹变化极慢",
        "咒源剥离牵涉神魂反噬",
    ))


def _wanxin_uint(raw):
    if not re.fullmatch(r"[0-9]{1,19}", raw):
        return None
    value = int(raw)
    return value if value < 2 ** 63 else None


def _parse_panel_values(text):
    """An absolute panel is complete or absent; deltas are not panel fields."""
    values = {}
    stages = {}
    for part in re.split(r"[\n|]", text):
        part = part.strip()
        stage = re.fullmatch(r"(阶段|婉心封魂)[:：]\s*(.*)", part)
        if stage:
            name, value = stage.groups()
            if name in stages or not value or len(value) > 96:
                return None
            stages[name] = value
            continue
        match = RE_WANXIN_VALUE.fullmatch(part)
        if match is None:
            continue
        name, raw_value = match.groups()
        raw_value = raw_value.strip()
        if raw_value.startswith(("+", "-")):
            continue
        if raw_value.startswith((":", "：")):
            raw_value = raw_value[1:].strip()
        value = _wanxin_uint(raw_value)
        key = WANXIN_PANEL_FIELDS[name]
        if value is None or key in values:
            return None
        values[key] = value
    if (values or stages) and values.keys() != set(WANXIN_PANEL_FIELDS.values()):
        return None
    if stages:
        values["stage"] = stages.get("阶段") or stages["婉心封魂"]
    return values


def _wanxin_reply_number(text, label, operator):
    labels = (label,) if isinstance(label, str) else label
    name = "(?:" + "|".join(re.escape(value) for value in labels) + ")"
    fields = re.findall(r"(?:^|[\n，,。；;|])[ \t]*" + name
                        + r"(?=[:：+\-\s]|$)([^，,。；;|\n]*)", text)
    if label in WANXIN_PANEL_FIELDS and operator != "[:：]":
        fields = [value for value in fields if not re.match(r"\s*(?:[:：]|[0-9])", value)]
    if len(fields) != 1:
        return None
    match = re.fullmatch(r"\s*" + operator + r"\s*([0-9]{1,19})[ \t]*\.?", fields[0])
    return _wanxin_uint(match[1]) if match else None


def _wanxin_affinity_cost(text):
    fields = re.findall(r"(?m)^[ \t]*消耗([^\n]*)$", text)
    if len(fields) != 1:
        return None
    match = re.fullmatch(r"[:：]\s*(?:([0-9]{1,19})\s*修为[、，,]\s*)?"
                        r"([0-9]{1,19})\s*情缘[。.]?", fields[0].strip())
    if match is None or match[1] is not None and _wanxin_uint(match[1]) is None:
        return None
    return _wanxin_uint(match[2])


def _apply_panel_values(observed, values, now):
    if timestamp(now) < timestamp(observed.get("panel_observed_at")):
        return
    if values:
        observed.update(values)
        observed["available"] = "yes"
        observed["last_observed_at"] = float(now)
        observed["panel_observed_at"] = float(now)


def _parse_target_username(text):
    match = RE_TARGET_USER.search(str(text or ""))
    if not match:
        return ""
    return (match.group("owner") or match.group("owner2") or "").strip().lstrip("@")


def _cooldown_action_from_text(text):
    raw = str(text or "")
    names = {
        WANXIN_ACTION_BANNER: ("借幡镇魂",),
        WANXIN_ACTION_STRIP: ("剥离咒源", "咒源剥离"),
        WANXIN_ACTION_IDENTIFY: ("辨认咒纹", "阴罗辨咒", "辨咒"),
        WANXIN_ACTION_DEDUCE: ("推演封魂咒", "封魂咒纹变化极慢"),
        WANXIN_ACTION_PROTECT: ("护持神魂",),
        WANXIN_ACTION_VISIT: ("探望南宫婉",),
        WANXIN_ACTION_MOON_GREET: ("婉影问安",),
        WANXIN_ACTION_MOON_SEAL: ("同参封魂",),
        WANXIN_ACTION_MOON_JOIN: ("月下合参",),
    }
    actions = [action for action, labels in names.items() if any(label in raw for label in labels)]
    if actions:
        return actions[0] if len(actions) == 1 else ""
    if "咒纹" in raw and "推演" in raw:
        return WANXIN_ACTION_DEDUCE
    if "神魂" in raw and "冷却" in raw:
        return WANXIN_ACTION_PROTECT
    return ""


def _parse_wanxin_owner_result(raw, now, parsed):
    header = RE_WANXIN_RESULT_HEADER.search(raw)
    body = raw[len(header[0]):].strip() if header and raw.startswith(header[0]) else raw
    effects = bool(re.search(r"(?:婉心|魂封|月魄|咒源|情缘)\s*[+-]|消耗[:：]|护持神魂成功|月下合参成功", body))
    unavailable = "需先成功通关【掩月抢亲】" in raw or "方可开启【婉心封魂】" in raw
    refusals = {
        "unavailable": unavailable,
        "moon_join_blocked": "封魂咒尚未解除" in raw and "月下合参" in raw,
        "moon_unavailable": "婉影尚未觉醒" in raw or "并非【南宫婉】一系" in raw,
        "visit_already": "已探望过南宫婉" in raw,
        "moon_greet_already": "今日已与婉影问安" in raw or "今日已经问候过婉影" in raw,
    }
    # Status/help footers describe several cooldowns; they are not refusals.
    panel_header = header and header[0] in {"【婉心封魂】", "【月影同参】", "【婉心封魂指令】", "【婉影觉醒】"}
    waits = RE_WANXIN_WAIT.findall(raw) if not panel_header else []
    cooldown_action = _cooldown_action_from_text(raw)
    if "请在" in raw and not panel_header and (
        len(waits) != 1 or not RE_WANXIN_DURATION.fullmatch(waits[0].strip())
        or not has_wait_time(waits[0]) or not parse_wait_time(waits[0]) or not cooldown_action
    ):
        return parsed
    refusals["cooldown"] = bool(waits and cooldown_action)
    if sum(refusals.values()) > 1 or any(refusals.values()) and effects:
        return parsed
    if any(refusals.values()):
        kind = next(key for key, present in refusals.items() if present)
        if kind in {"visit_already", "moon_greet_already", "moon_join_blocked"}:
            expected_header, acknowledgement = {
                "visit_already": ("【探望南宫婉】", r"(?:你)?(?:今日)?已探望过南宫婉[。，.!]"),
                "moon_greet_already": ("【婉影问安】", r"今日(?:已与婉影问安|已经问候过婉影)[。，.!]"),
                "moon_join_blocked": ("【月下合参】", r"封魂咒尚未解除[，,]"),
            }[kind]
            if (header and header[0] != expected_header) or not re.match(acknowledgement, body):
                return parsed
        if kind == "moon_unavailable" and not body.startswith((
            "婉影尚未觉醒", "并非【南宫婉】一系", "你的侍妾并非【南宫婉】一系",
            "你当前的侍妾并非【南宫婉】一系",
        )):
            return parsed
        if kind == "unavailable" and not re.match(r"(?:你)?需先成功通关【掩月抢亲】", body):
            return parsed
        summaries = {
            "unavailable": "缺少婉心封魂前置", "moon_join_blocked": "封魂未解，月下合参暂缓",
            "moon_unavailable": "婉影玩法尚不可用", "visit_already": "今日已探望",
            "moon_greet_already": "今日已与婉影问安", "cooldown": "冷却中",
        }
        parsed.update(type=kind, available="no" if kind == "unavailable" else "yes", summary=summaries[kind])
        if kind == "cooldown":
            wait_seconds = parse_wait_time(waits[0])
            parsed.update(next_time=now + wait_seconds + CD_BUFFER_SEC, wait_seconds=wait_seconds,
                          cooldown_action=cooldown_action)
        return parsed
    if any(marker in raw for marker in (
        "失败", "未能", "无法", "不足", "正在", "处理中", "请稍候", "请稍后", "尚未完成", "未完成",
    )) or not panel_header and any(marker in raw for marker in ("未成功", "未执行", "尚未", "预览", "不可", "不能", "没能")):
        return parsed
    if header and not raw.startswith(header[0]):
        return parsed
    if header and header[0] == "【婉心封魂指令】":
        if body:
            parsed.update(type="help", available="unknown", summary="婉心帮助")
        return parsed
    if header and header[0] == "【婉心封魂】":
        if set(WANXIN_PANEL_FIELDS.values()).issubset(parsed["values"]):
            parsed.update(type="panel", available="yes", summary="婉心状态")
        return parsed
    if header and header[0] == "【婉影觉醒】":
        if re.match(r"【南宫婉】已觉醒为\s*【南宫婉·月影】[。.]", body) and parsed["values"]:
            parsed.update(type="moon_awakened", available="yes", summary="婉影觉醒")
        return parsed
    if (header and header[0] == "【月影同参】") or raw.startswith(("婉影共鸣:", "婉影共鸣：")):
        affinity = _wanxin_reply_number(raw, "情缘", "[:：]")
        resonance = re.findall(r"(?m)^[ \t]*(?:婉影共鸣|共鸣)[:：][ \t]*(.*)$", raw)
        partners = re.findall(r"(?m)^[ \t]*侍妾[:：][ \t]*【([^】]+)】[^\n]*$", raw)
        if affinity is not None and [value.strip() for value in resonance] == ["已觉醒"] and partners == ["南宫婉·月影"]:
            parsed.update(type="moon_panel", available="yes", affinity=affinity, summary="婉影状态")
        return parsed
    if header and header[0] == "【婉影问安】":
        gain = _wanxin_reply_number(body, "情缘", r"\+")
        if gain is not None:
            parsed.update(type="moon_greet_success", available="yes", affinity_gain=gain, summary="婉影问安成功")
        return parsed
    if header and header[0] == "【同参封魂】":
        cost = _wanxin_affinity_cost(body)
        if cost is not None:
            parsed.update(type="moon_seal_success", available="yes", affinity_cost=cost, summary="同参封魂成功")
        return parsed
    if header and header[0] == "【推演封魂咒】":
        gain = _wanxin_reply_number(body, "咒源", r"\+")
        if gain is not None:
            parsed.update(type="deduce_success", available="yes", source_gain=gain, summary="推演成功")
        return parsed
    visit = bool(
        (not header or header[0] == "【探望南宫婉】") and body.startswith((
            "你稳住了她的神魂。", "你以月殿旧令稳住她的神魂，南宫婉短暂醒转片刻。",
            "你探望南宫婉，婉心微动，封魂稍缓。", "探望南宫婉后，婉心微明，魂封略有松动。",
        ))
    )
    expense = re.match(r"你耗费 ([0-9]{1,19}) 修为，以神识与月魄护住南宫婉识海。", body)
    protect = bool(
        (not header or header[0] == "【护持神魂】")
        and (body.startswith("护持神魂成功，") or expense and _wanxin_uint(expense[1]) is not None)
        and _wanxin_reply_number(body, "魂封", "-") is not None
    )
    if visit and not protect:
        parsed.update(type="visit_success", available="yes", summary="探望成功")
    elif protect and not visit:
        parsed.update(type="protect_success", available="yes", summary="护持成功")
    # No captured moon-join success body exists. Its title is not a receipt.
    return parsed


def _parse_wanxin_commission_result(raw, now, parsed):
    header = RE_WANXIN_RESULT_HEADER.search(raw)
    title = header[0] if header else ""
    body = raw.removeprefix(title).strip()
    claims = {
        "commission_published": "【解咒委托已发布】" in raw,
        "commission_existing": "你已有进行中的解咒委托" in raw,
        "commission_accepted": "【咒契协定已成】" in raw,
        "commission_cancelled": "解咒委托已取消" in raw or "当前没有可取消的解咒委托" in raw,
        "commission_cancel_blocked": "委托已被接取" in raw and "无法直接取消" in raw,
        "commission_expired": "该委托已过期" in raw and "取消解咒委托" in raw,
        "commission_claimed_elsewhere": "该委托不存在或已被他人接取" in raw,
        "commission_invalid": "没有有效的咒契协定" in raw and "发布委托" in raw and "接取" in raw,
        "assist_missing_target": "请回复委托发布者" in raw or "命令后指定对方" in raw,
        "assist_not_yinluo": "并非阴罗宗弟子" in raw and "无法插手" in raw,
    }
    kinds = [kind for kind, present in claims.items() if present]
    if not kinds and title != "【阴罗辨咒】":
        return None
    if len(kinds) > 1 or (title and not raw.startswith(title)) or any(marker in raw for marker in (
        "失败", "未能", "处理中", "正在处理", "请稍候", "请稍后", "尚未确认", "示例", "预览",
        "未完成", "未发布", "未接取", "未辨认", "正在发布", "正在接取", "正在辨认",
        "未执行", "未成功", "灵石不足", "无法发布", "不能发布", "不可发布", "无法接取", "不能接取", "不可接取",
    )):
        return parsed
    if not kinds:
        if "请在" in body:
            if "锁定咒源" in body:
                return parsed
            return _parse_wanxin_owner_result(raw, now, parsed)
        contract = RE_IDENTIFY_CONTRACT.match(body)
        source_gain = _wanxin_reply_number(body, "咒源", r"\+")
        contrib_gain = _wanxin_reply_number(body, ("咒师贡献", "贡献"), r"\+")
        if (contract is None or body.count("@") != 2 or source_gain is None or contrib_gain is None
                or any(marker in body for marker in ("尚未", "不能", "不可", "不足", "无法"))):
            return parsed
        parsed.update(type="assist_identify_success", available="yes", summary="辨认咒纹成功",
                      helper_username=contract["helper"], target_username=contract["owner"],
                      source_gain=source_gain, contrib_gain=contrib_gain)
        return parsed
    kind = kinds[0]
    if kind == "commission_published":
        values = RE_COMMISSION_ID.findall(body)
        commission_id = _wanxin_uint(values[0].strip()) if len(values) == 1 else None
        if title != "【解咒委托已发布】" or not commission_id:
            return parsed
        parsed.update(type=kind, commission_id=commission_id, available="yes", summary="委托已发布")
        return parsed
    if kind == "commission_accepted":
        contract = RE_ACCEPT_CONTRACT.match(body)
        if title != "【咒契协定已成】" or contract is None or body.count("@") != 2:
            return parsed
        parsed.update(type=kind, helper_username=contract["helper"], target_username=contract["owner"],
                      available="yes", summary="咒契已成")
        return parsed
    if title and (title != "【阴罗辨咒】" or kind not in {"commission_invalid", "assist_missing_target", "assist_not_yinluo"}):
        return parsed
    if re.search(r"(?:咒源|魂封|月魄|情缘|贡献)\s*[+-]|锁定咒源", body):
        return parsed
    if kind == "commission_existing":
        match = RE_EXISTING_COMMISSION_ID.fullmatch(body)
        commission_id = _wanxin_uint(match[1]) if match else None
        if not commission_id:
            return parsed
        parsed.update(type=kind, commission_id=commission_id, available="yes", summary="已有进行中委托")
        return parsed
    if kind == "commission_cancelled":
        empty = re.fullmatch(r"(?:你)?当前没有可取消的解咒委托[。.]?", body)
        cancelled = re.fullmatch(r"解咒委托已取消(?:[，,]已退回\s*([0-9]{1,19})\s*灵石)?[。.]?", body)
        if not empty and (cancelled is None or cancelled[1] is not None and _wanxin_uint(cancelled[1]) is None):
            return parsed
        parsed.update(type=kind, available="yes", summary="当前无可取消委托" if empty else "委托已取消")
        return parsed
    patterns = {
        "commission_cancel_blocked": r"(?:该)?委托已被接取[，,](?:[0-9]{1,3}\s*小时内)?无法直接取消[。.]?",
        "commission_claimed_elsewhere": r"该委托不存在或已被他人接取[。.]?",
        "commission_invalid": r"你与对方没有有效的咒契协定[。，,.](?:需先由对方|请对方先)发布委托[，,]再由你接取[。.]?",
    }
    if kind in patterns and not re.fullmatch(patterns[kind], body):
        return parsed
    if kind == "commission_expired" and not body.startswith(("该委托已过期，", "该委托已过期。")):
        return parsed
    if kind == "assist_missing_target" and not body.startswith(("请回复委托发布者", "请在命令后指定对方")):
        return parsed
    if kind == "assist_not_yinluo" and not body.startswith(("你并非阴罗宗弟子", "并非阴罗宗弟子")):
        return parsed
    summaries = {
        "commission_cancel_blocked": "委托尚未满协定期限",
        "commission_expired": "委托已过期，等待发布者取消",
        "commission_claimed_elsewhere": "委托已被他人接取",
        "commission_invalid": "咒契失效，需重新发布并接取",
        "assist_missing_target": "协助缺少委托方锚点",
        "assist_not_yinluo": "协助身份不是阴罗宗",
    }
    parsed.update(type=kind, available="yes", summary=summaries[kind])
    return parsed


def parse_wanxin_text(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    raw = text.replace("\r\n", "\n").replace("\r", "\n").strip() if isinstance(text, str) and len(text) <= 4096 else ""
    if not raw or not looks_like_wanxin_text(raw):
        return None

    parsed = {
        "type": "unknown",
        "family": str(family or ""),
        "values": {},
        "available": "",
        "next_time": 0,
        "target_username": "",
        "commission_id": 0,
        "helper_username": "",
        "contrib_gain": 0,
        "source_gain": 0,
        "seal_down": 0,
        "moon_gain": 0,
        "sha_cost": 0,
        "required_sha": 0,
        "cooldown_action": "",
        "summary": "",
    }

    values = _parse_panel_values(raw)
    if values is None or len(RE_WANXIN_RESULT_HEADER.findall(raw)) > 1:
        return parsed
    parsed["values"] = values
    if "你没有【北冥寒令】" in raw or "无法开启北冥小极宫" in raw:
        parsed.update({"type": "beiming_blocked", "available": "yes", "summary": "缺少北冥寒令"})
        return parsed
    commission_result = _parse_wanxin_commission_result(raw, now, parsed)
    if commission_result is not None:
        return commission_result
    shortage_match = RE_ASSIST_SHA_REQUIRED.search(raw)
    if "阴罗幡煞气不足" in raw and shortage_match:
        shortage_action = shortage_match.group("action")
        parsed.update({
            "type": "assist_banner_resource_blocked" if shortage_action == "借幡镇魂" else "assist_strip_resource_blocked",
            "available": "yes",
            "required_sha": _safe_int(shortage_match.group("amount"), 0),
            "summary": f"阴罗幡煞气不足，{shortage_action}暂缓",
        })
        return parsed
    if "【借幡镇魂】" in raw:
        seal_match = RE_SEAL_DOWN.search(raw)
        moon_match = RE_MOON_GAIN.search(raw)
        contrib_match = RE_CONTRIB_GAIN.search(raw)
        sha_cost_match = RE_ASSIST_SHA_COST.search(raw)
        parsed.update({
            "type": "assist_banner_success",
            "available": "yes",
            "target_username": _parse_target_username(raw),
            "seal_down": _safe_int(seal_match.group("down"), 0) if seal_match else 0,
            "moon_gain": _safe_int(moon_match.group("gain"), 0) if moon_match else 0,
            "contrib_gain": _safe_int(contrib_match.group("gain"), 0) if contrib_match else 0,
            "sha_cost": _safe_int(sha_cost_match.group("amount"), WANXIN_BANNER_SHA_COST) if sha_cost_match else WANXIN_BANNER_SHA_COST,
            "summary": "借幡镇魂成功",
        })
        return parsed
    if "【剥离咒源失败】" in raw:
        contrib_match = RE_CONTRIB_GAIN.search(raw)
        sha_cost_match = RE_ASSIST_SHA_COST.search(raw)
        parsed.update({
            "type": "assist_strip_failed",
            "available": "yes",
            "target_username": _parse_target_username(raw),
            "contrib_gain": _safe_int(contrib_match.group("gain"), 0) if contrib_match else 0,
            "sha_cost": _safe_int(sha_cost_match.group("amount"), WANXIN_STRIP_SHA_COST) if sha_cost_match else WANXIN_STRIP_SHA_COST,
            "summary": "剥离咒源失败",
        })
        return parsed
    if "【剥离咒源成功】" in raw or "剥下一段阴罗残咒" in raw or "剥离阴罗残咒" in raw:
        source_match = RE_SOURCE_GAIN.search(raw)
        seal_match = RE_SEAL_DOWN.search(raw)
        contrib_match = RE_CONTRIB_GAIN.search(raw)
        parsed.update({
            "type": "assist_strip_success",
            "available": "yes",
            "target_username": _parse_target_username(raw),
            "source_gain": _safe_int(source_match.group("gain"), 0) if source_match else 0,
            "seal_down": _safe_int(seal_match.group("down"), 0) if seal_match else 0,
            "contrib_gain": _safe_int(contrib_match.group("gain"), 0) if contrib_match else 0,
            "sha_cost": WANXIN_STRIP_SHA_COST,
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
    return _parse_wanxin_owner_result(raw, now, parsed)


def _pending_blocks(observed, now):
    pending = observed.get("pending") if isinstance(observed.get("pending"), dict) else {}
    if not pending:
        return False
    due = float(pending.get("reply_due_at", 0) or 0)
    if due > now:
        observed["auto_next_time"] = due
        return True
    action = str(pending.get("action") or "")
    sending = pending.get("status") == "sending"
    if sending and pending.get("op_id") and _WANXIN_INFLIGHT.get((get_current_identity_id(), action)) == pending["op_id"]:
        if observed["auto_next_time"] <= now:
            observed["auto_next_time"] = now + WANXIN_RECOVERY_RETRY_SEC
        observed["auto_last_error"] = "婉心传输仍在途，等待原发送完成，不启动另一动作"
        return True
    held = observed.get("unresolved_actions", {})
    # A restored intent has no live caller. Keep its uncertainty, not a process lock.
    candidate = dict(pending, status="unknown") if sending else pending
    if (_can_isolate_wanxin_pending(candidate) and not observed.get("unresolved_invalid")
            and action not in held and pending["owner_id"] == get_current_identity_id()
            and _wanxin_owner_current(_capture_wanxin_owner(pending["owner_id"]))
            and get_identity_account(pending["owner_id"]) == pending["owner_account_id"]
            and has_identity(pending["send_as_id"])
            and get_identity_account(pending["send_as_id"]) == pending["account_id"]):
        candidate["status"] = "unknown"
        held[action] = copy.deepcopy(candidate)
        observed["unresolved_actions"] = held
        observed["pending"] = {}
        observed["auto_next_time"] = now
        observed["auto_last_error"] = f"{WANXIN_ACTION_LABELS[action]}结果未确认，保留原操作；只阻断相关动作"
        return False
    if action in {WANXIN_ACTION_BANNER, WANXIN_ACTION_STRIP}:
        observed["auto_last_error"] = "阴罗协助结果未确认，保留预留等待原回包，不按超时重新花费。"
        if float(observed.get("auto_next_time", 0) or 0) <= now:
            observed["auto_next_time"] = now + WANXIN_RECOVERY_RETRY_SEC
        return True
    pending["status"] = "unknown" if pending.get("op_id") or pending.get("msg_id") else "legacy_unknown"
    label = WANXIN_ACTION_LABELS.get(action, action)
    observed["auto_last_action"] = action
    observed["auto_last_result"] = "结果未确认，等待原回包"
    observed["auto_last_error"] = f"{label} 回复超时，保留原操作，不按超时补发"
    if float(observed.get("auto_next_time", 0) or 0) <= now:
        observed["auto_next_time"] = float(now + WANXIN_RECOVERY_RETRY_SEC)
    return True


def _mark_resource_pending(observed, action, operation, now, *, owner, actor):
    msg_id = int(operation.get("msg_id", 0) or 0)
    commission = observed["commission"]
    observed["pending"] = {
        "action": action,
        "family": WANXIN_ACTION_FAMILIES.get(action, ""),
        "msg_id": msg_id,
        "send_as_id": actor[0],
        "sent_at": timestamp(operation.get("sent_at")),
        "reply_due_at": float(now + WANXIN_REPLY_TIMEOUT_SEC),
        "chat_id": operation["chat_id"],
        "op_id": operation["op_id"], "resource_op_id": operation["op_id"],
        "command": operation["command"], "started_at": operation["started_at"],
        "status": "sent" if msg_id else "unknown",
        "account_id": actor[2],
        "owner_id": owner[0], "owner_account_id": owner[2],
        "commission_id": commission["id"], "commission_published_at": commission["published_at"],
        "commission_accepted_at": commission["accepted_at"],
    }
    observed["auto_last_action"] = action
    observed["auto_last_result"] = "已发送" if msg_id else "发送状态未知，等待原回包"
    observed["auto_last_error"] = "" if msg_id else "阴罗协助结果未确认（unknown），保留资源预留，不重复发送"
    observed["auto_next_time"] = float(now + WANXIN_REPLY_TIMEOUT_SEC)


def _mark_commission_invalid(observed, now, reason="", *, owner_id):
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
        commission["owner_username"] = _owner_username(owner_id)
    observed["commission"] = commission

    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else _default_wanxin_assist()
    assist["last_anchor_msg_id"] = 0
    assist["last_anchor_at"] = 0
    assist["identified_commission_id"] = 0
    assist["bannered_commission_id"] = 0
    assist["last_result"] = ""
    assist["last_error"] = reason
    observed["assist"] = assist

    observed["auto_last_result"] = ""
    observed["auto_last_error"] = reason
    _schedule_next(observed, now)


def _commission_cancel_due_at(commission, now):
    published_at = float((commission or {}).get("published_at", 0) or 0)
    if published_at > 0:
        return published_at + WANXIN_COMMISSION_TTL_SEC + CD_BUFFER_SEC
    return float(now + WANXIN_COMMISSION_TTL_SEC + CD_BUFFER_SEC)


def _mark_commission_claimed_elsewhere(observed, now, helper_username="", reason=""):
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else _default_wanxin_commission()
    commission["accepted"] = False
    commission["accepted_at"] = 0
    commission["helper_username"] = ""
    commission["claimed_elsewhere"] = True
    commission["claim_helper_username"] = str(helper_username or "").strip().lstrip("@")
    commission["cancel_due_at"] = max(
        float(commission.get("cancel_due_at", 0) or 0),
        _commission_cancel_due_at(commission, now),
    )
    observed["commission"] = commission
    observed["auto_last_action"] = WANXIN_ACTION_ACCEPT
    observed["auto_last_result"] = reason or "委托已被他人接取"
    observed["auto_last_error"] = ""
    observed["auto_next_time"] = float(commission["cancel_due_at"])
    _push_recent(
        observed,
        now,
        WANXIN_ACTION_ACCEPT,
        "claimed_elsewhere",
        f"helper={commission['claim_helper_username'] or 'unknown'} cancel_at={fmt_abs_ts(commission['cancel_due_at'])}",
    )


def _consume_commission(observed, *, owner_id):
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else _default_wanxin_commission()
    owner_username = str(commission.get("owner_username") or _owner_username(owner_id) or "").strip().lstrip("@")
    commission.update(_default_wanxin_commission())
    commission["owner_username"] = owner_username
    observed["commission"] = commission

    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else _default_wanxin_assist()
    assist["last_anchor_msg_id"] = 0
    assist["last_anchor_at"] = 0
    assist["identified_commission_id"] = 0
    assist["bannered_commission_id"] = 0
    observed["assist"] = assist


def _assist_identity_ready(observed):
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    assist_send_as_id = int(assist.get("send_as_id", 0) or 0)
    return bool(
        assist_send_as_id > 0
        and has_identity(assist_send_as_id)
        and get_identity_enabled(assist_send_as_id)
        and _is_yinluo_identity(assist_send_as_id)
    )


def _recover_external_commission_claim_from_log(observed, now):
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    if (int(commission.get("id", 0) or 0) <= 0 or commission.get("accepted")
            or commission.get("claimed_elsewhere") or observed.get("pending")
            or _unresolved_wanxin_blocks(observed, WANXIN_ACTION_ACCEPT)):
        return False
    evidence = _external_commission_evidence(observed, now)
    if evidence is None:
        return False
    if evidence.completion is not None:
        return _apply_external_commission_completion(observed, evidence, now)
    commission["published_at"] = evidence.publication.end["at"]
    commission["cancel_due_at"] = _commission_cancel_due_at(commission, now)
    _mark_commission_claimed_elsewhere(
        observed, evidence.acceptance.end["at"], evidence.acceptance.parsed["helper_username"],
        "委托已被其他阴罗咒师接取",
    )
    return True


def _external_commission_evidence(observed, now):
    commission = observed.get("commission", {})
    published_at = timestamp(commission.get("published_at"))
    if not published_at or timestamp(now) <= 0:
        return None
    entries = []
    for entry, _received_at in _iter_message_log_entries_between(max(1, published_at - WANXIN_REPLY_TIMEOUT_SEC), now):
        entries.append(entry)
        if len(entries) > WANXIN_LOG_MAX_RECORDS:
            return None
    trust = resource_accounting.event_trust(now)
    return find_commission_evidence(
        entries, owner_id=get_current_identity_id(), helper_id=observed.get("assist", {}).get("send_as_id", 0),
        commission=commission, identity_usernames=resource_accounting.identity_usernames(),
        game_chats=trust["game_chats"], game_bots=trust["game_bots"], now=now, parse_reply=parse_wanxin_text,
    )


def _recover_claimed_commission_completion_from_log(observed, now):
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    if (int(commission.get("id", 0) or 0) <= 0 or not commission.get("claimed_elsewhere") or observed.get("pending")
            or _unresolved_wanxin_blocks(observed, WANXIN_ACTION_STRIP)):
        return False
    evidence = _external_commission_evidence(observed, now)
    if evidence is None or evidence.completion is None:
        return False
    return _apply_external_commission_completion(observed, evidence, now)


def _apply_external_commission_completion(observed, evidence, now):
    parsed, entry_ts = evidence.completion.parsed, evidence.completion.revision["at"]
    if entry_ts > timestamp(observed.get("panel_observed_at")):
        _apply_panel_values(observed, parsed.get("values") or {}, entry_ts)
    _consume_commission(observed, owner_id=get_current_identity_id())
    observed["auto_last_action"] = WANXIN_ACTION_STRIP
    observed["auto_last_result"] = "外部咒师已完成剥离，委托已结清"
    observed["auto_last_error"] = ""
    _schedule_next(observed, now, result=observed["auto_last_result"])
    _push_recent(
        observed,
        entry_ts,
        WANXIN_ACTION_STRIP,
        "external_completed",
        parsed.get("summary") or "剥离已完成",
    )
    return True


def _schedule_next(observed, now, delay_sec=WANXIN_CHAIN_STEP_SEC, *, result="", error=""):
    observed["auto_next_time"] = float(now + max(1, delay_sec))
    if result:
        observed["auto_last_result"] = result
    if error:
        observed["auto_last_error"] = error


def _handle_unsent_send(observed, action, command, now, *, block):
    code = str((block or {}).get("code") or "").strip()
    reason = str((block or {}).get("reason") or "").strip()
    if code == "send_as_peer_invalid":
        health = get_channel_send_as_health()
        probe_at = float(health.get("next_probe_at", 0) or 0)
        blocked_until = float((block or {}).get("blocked_until", 0) or 0)
        if str(health.get("status") or "") == "closed" and probe_at > now:
            retry_at = probe_at
        else:
            retry_at = blocked_until
        if retry_at <= now:
            retry_at = now + 30 * 60
        waiting_channel_probe = str(health.get("status") or "") == "closed" and probe_at > now
        observed["auto_next_time"] = retry_at + CD_BUFFER_SEC
        observed["auto_last_action"] = action
        observed["auto_last_result"] = (
            f"{WANXIN_ACTION_LABELS.get(action, action)} 未发送，等待频道身份复查"
            if waiting_channel_probe else
            f"{WANXIN_ACTION_LABELS.get(action, action)} 未发送，按身份退避"
        )
        observed["auto_last_error"] = reason or "频道身份不可用于当前游戏群"
        _push_recent(observed, now, action, "send_as_peer_invalid", observed["auto_last_error"])
        return False
    if code == "send_queue_timeout":
        _schedule_next(observed, now, WANXIN_SEND_QUEUE_RETRY_SEC, error=reason or f"{command} 排队超时未发送")
        return False
    if code == "global_disabled":
        _schedule_next(observed, now, RETRY_MAX_SEC, error=reason or "全局暂停，婉心延后")
        return False
    _schedule_next(observed, now, RETRY_MAX_SEC, error=reason or f"{command} 未发送，延后重试")
    return False


def project_assist_resource_reply(update, text):
    source, reply = update.source, update.reply
    if update.value["book"]["gap"] or reply.phase not in {"success", "failed", "denied"}:
        return {}
    archived_duplicate = not any(
        (item["start"]["evidence"]["chat_id"], item["start"]["evidence"]["msg_id"])
        == (source.chat_id, source.command_msg_id) for item in update.value["book"]["receipts"]
    )
    archived_operation = resource_accounting.completed_reply_operation(update.value, source, reply) if archived_duplicate else None
    if archived_duplicate and (archived_operation is None or not archived_operation["beneficiary"]):
        return {}
    names = resource_accounting.identity_usernames()
    if archived_duplicate or [source.chat_id, source.command_msg_id] in update.value["restored_roots"]:
        originals = [archived_operation] if archived_duplicate else [
            item for item in update.value["operations"]
            if (item["chat_id"], item["msg_id"]) == (source.chat_id, source.command_msg_id)
        ]
        if len(originals) != 1 or not originals[0]["beneficiary"]:
            return {}
        owners = [originals[0]["beneficiary"]["identity_id"]]
        if owners[0] not in names:
            return {}
    else:
        owners = [identity_id for identity_id, aliases in names.items() if source.command.target in aliases]
    if len(owners) != 1 or owners[0] == source.identity_id:
        return {}
    owner_id = owners[0]
    observed = normalize_wanxin_observation(get_identity_state(owner_id).get("wanxin_observation"))
    if observed.get("unresolved_invalid") or observed.get("reply_points_invalid"):
        return {}
    action = WANXIN_ACTION_BANNER if source.command.action == "assist_banner" else WANXIN_ACTION_STRIP
    commission = observed["commission"]
    candidates = [archived_operation] if archived_duplicate else [item for item in update.value["operations"] if (
        item["command"] == source.command.text and item["chat_id"] == source.chat_id
        and item["beneficiary"] and (
            item["msg_id"] == source.command_msg_id
            or (not item["msg_id"] and item["phase"] in resource_accounting.LIVE_PHASES
                and item["started_at"] - 1 <= source.command_at)
        )
    )]
    if len(candidates) > 1:
        return {}
    # Command text/time alone cannot bind a no-ID reservation to this result.
    if candidates and (candidates[0]["msg_id"] != source.command_msg_id or candidates[0]["phase"] != "complete"):
        return {}
    matching = [item for item in _wanxin_pending_operations(observed) if (
        item["action"] == action and item["send_as_id"] == source.identity_id
        and item["chat_id"] == source.chat_id
        and item.get("account_id", 0) in {0, source.account_id}
        and item.get("owner_id", 0) in {0, owner_id}
        and item.get("owner_account_id", 0) in {0, get_identity_account(owner_id)}
        and source.command_at >= timestamp(item.get("started_at")) - 1
        and (not item.get("command") or item["command"] == source.command.text)
        and (item["msg_id"] == source.command_msg_id or (
            not item["msg_id"] and len(candidates) == 1
            and item.get("resource_op_id") == candidates[0]["op_id"]
        ))
    )]
    if len(matching) > 1:
        return {}
    pending = matching[0] if matching else None
    if not pending and observed["assist"]["send_as_id"] != source.identity_id:
        return {}
    completion_changes = {}
    if (pending and len(candidates) == 1 and candidates[0]["phase"] == "complete"
            and pending.get("op_id") == pending.get("resource_op_id") == candidates[0]["op_id"]
            and pending.get("account_id") == source.account_id
            and pending.get("owner_id") == owner_id
            and pending.get("owner_account_id") == get_identity_account(owner_id)
            and candidates[0]["beneficiary"] == {
                "identity_id": owner_id, "account_id": pending["owner_account_id"],
                "commission_id": pending.get("commission_id"),
                "published_at": pending.get("commission_published_at"),
                "accepted_at": pending.get("commission_accepted_at"),
            }):
        # A completed old operation can close its slot without projecting onto
        # a replacement commission or applying the same resource result again.
        _remove_wanxin_pending_operation(observed, pending)
        completion_changes = {owner_id: {"wanxin_observation": observed}}
    if archived_duplicate:
        return completion_changes
    if candidates and candidates[0]["beneficiary"] != {
        "identity_id": owner_id, "account_id": get_identity_account(owner_id),
        "commission_id": commission["id"], "published_at": commission["published_at"],
        "accepted_at": commission["accepted_at"],
    }:
        return completion_changes
    if any(item["action"] == action for item in _wanxin_pending_operations(observed)) and not pending:
        return completion_changes
    if max(commission["published_at"], commission["accepted_at"]) > source.command_at:
        return completion_changes
    if not candidates and not pending and not (
        commission["id"] > 0 and commission["accepted"]
        and 0 < commission["published_at"] <= commission["accepted_at"] <= source.command_at
    ):
        return completion_changes
    parsed = parse_wanxin_text(text, now=source.result_at, family=WANXIN_ACTION_FAMILIES[action])
    if not parsed or not _accept_business_point(update, f"assist:{owner_id}:{action}"):
        return completion_changes
    if reply.phase in {"success", "failed"}:
        _apply_success_cooldown(observed, action, source.result_at, parsed)
        _apply_panel_values(observed, parsed.get("values") or {}, source.result_at)
        if action == WANXIN_ACTION_STRIP:
            _consume_commission(observed, owner_id=owner_id)
        else:
            observed["assist"]["bannered_commission_id"] = observed["commission"]["id"]
        observed["assist"]["last_contrib_gain"] = int(parsed.get("contrib_gain", 0) or 0)
        observed["assist"]["last_action"] = action
        observed["assist"]["last_result"] = parsed.get("summary") or reply.phase
        observed["assist"]["last_error"] = parsed.get("summary", "") if reply.phase == "failed" else ""
        observed["auto_last_result"] = observed["assist"]["last_result"]
        observed["auto_last_error"] = observed["assist"]["last_error"]
    elif parsed.get("type") in {"assist_banner_resource_blocked", "assist_strip_resource_blocked"}:
        _accept_business_point(update, "shortage_sha")
        _set_next_time_for_action(observed, action, source.result_at + WANXIN_RESOURCE_RECOVERY_RETRY_SEC)
        observed["auto_last_error"] = parsed.get("summary") or "阴罗幡煞气不足"
    elif parsed.get("type") == "cooldown":
        _set_cooldown_from_reply(observed, action, parsed["next_time"])
    elif parsed.get("type") == "commission_invalid":
        _mark_commission_invalid(observed, source.result_at, parsed.get("summary", ""), owner_id=owner_id)
    else:
        _set_next_time_for_action(observed, action, source.result_at + WANXIN_RECOVERY_RETRY_SEC)
    _remove_wanxin_pending_operation(observed, pending)
    _schedule_next(observed, source.result_at)
    if observed["pending"]:
        observed["auto_next_time"] = max(source.result_at, observed["pending"]["reply_due_at"])
    _push_recent(observed, source.result_at, action, parsed.get("summary") or reply.phase)
    changes = {owner_id: {"wanxin_observation": observed}}
    if parsed.get("type") in {"assist_banner_resource_blocked", "assist_strip_resource_blocked"}:
        from .yinluo import normalize_yinluo_observation
        provider = normalize_yinluo_observation(update.owner.get("yinluo_observation"))
        provider["resource_recovery_min_sha"] = max(provider["resource_recovery_min_sha"], parsed["required_sha"])
        provider["auto_calibrate_reason"] = "协助煞气不足，等待原生幡面板校准。"
        provider["auto_next_time"] = source.result_at + WANXIN_CHAIN_STEP_SEC
        changes[update.identity_id] = {"yinluo_observation": provider}
    return changes


def _same_wanxin_operation(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if left.get("op_id") or right.get("op_id"):
        return bool(left.get("op_id") and left.get("op_id") == right.get("op_id"))
    return bool(left.get("msg_id") and all(left.get(key) == right.get(key) for key in (
        "action", "send_as_id", "chat_id", "msg_id",
    )))


async def _recover_wanxin_pending_from_message_log(observed, now, *, pending=None, entries=None):
    owner = _capture_wanxin_owner()
    pending = copy.deepcopy(observed["pending"] if pending is None else pending)
    if not pending or not _wanxin_owner_current(owner):
        return False
    action = pending["action"]
    if action in WANXIN_RESOURCE_ACTIONS:
        if not pending["chat_id"]:
            return False
        recover_yinluo_resources(pending["send_as_id"], now, entries=entries)
        refreshed = normalize_wanxin_observation(owner[1].get("wanxin_observation"))
        if not any(_same_wanxin_operation(item, pending) for item in _wanxin_pending_operations(refreshed)):
            observed.clear()
            observed.update(refreshed)
            return True
        return False
    if action not in WANXIN_ACTION_COMMANDS or not has_identity(pending["send_as_id"]):
        return False
    if (pending.get("account_id") and pending["account_id"] != get_identity_account(pending["send_as_id"])
            or pending.get("owner_account_id") and pending["owner_account_id"] != owner[2]):
        return False
    start = timestamp(pending.get("started_at")) or timestamp(pending.get("sent_at"))
    if not start:
        return False
    if entries is None:
        entries = [entry for entry, _received in _iter_message_log_entries_between(max(1, start - 1), now)]
    if len(entries) > WANXIN_LOG_MAX_RECORDS:
        return False
    trust = resource_accounting.event_trust(now)
    replies = find_native_action_replies(
        entries, game_chats=trust["game_chats"], game_bots=trust["game_bots"], now=now,
        parse_reply=parse_wanxin_text, command_parser=_parse_wanxin_command, action_families=WANXIN_ACTION_FAMILIES,
    )
    actor_id = pending["send_as_id"]
    candidates = [
        item for item in replies if item.action == action and sender_matches_identity(item.command_sender, actor_id)
        and item.start["at"] >= start - 1
        and (not pending["chat_id"] or item.chat_id == pending["chat_id"])
        and (not pending["msg_id"] or item.root_msg_id == pending["msg_id"])
    ]
    if not pending["msg_id"]:
        # A log 'sent' receipt proves the transport binding, not the result.
        receipts = {
            (entry.get("chat_id"), entry.get("message_id"))
            for entry in entries if entry.get("event_type") == "sent" and pending.get("op_id")
            and entry.get("op_id") == pending["op_id"] and entry.get("source_module") == WANXIN_MODULE_NAME
            and entry.get("account_id") == pending["account_id"]
            and sender_matches_identity(entry.get("sender_id"), actor_id)
            and _parse_wanxin_command(entry.get("text")) == _parse_wanxin_command(pending["command"])
        }
        if len(receipts) != 1:
            return False
        candidates = [item for item in candidates if (item.chat_id, item.root_msg_id) in receipts]
    if len(candidates) != 1 or candidates[0].parsed is None:
        return False
    reply = candidates[0]
    originals = [entry for entry in entries if entry.get("event_type") == "message"
                 and (entry.get("chat_id"), entry.get("message_id")) == (reply.chat_id, reply.root_msg_id)]
    if not originals:
        return False
    command = originals[0]["text"]
    before = copy.deepcopy(owner[1].get("wanxin_observation"))
    current = normalize_wanxin_observation(before)
    slots = [item for item in _wanxin_pending_operations(current) if item == pending]
    if len(slots) != 1:
        return False
    if not pending["chat_id"] or not pending["msg_id"]:
        slots[0].update(chat_id=reply.chat_id, msg_id=reply.root_msg_id)
        if not _commit_wanxin_observation(owner, before, current):
            return False
    event = VerifiedGameEvent(
        event_type="edit" if reply.revision["evidence"]["edited"] else "message",
        chat_id=reply.chat_id, msg_id=reply.result_msg_id, sender_id=reply.bot_sender, text=reply.text,
        identity_id=actor_id, family=WANXIN_ACTION_FAMILIES[action], root_msg_id=reply.root_msg_id,
        route_source="wanxin_native_log", reply_to_sender_id=reply.command_sender,
        server_event_at=reply.revision["at"], reply_context={
            "chat_id": reply.chat_id, "root_msg_id": reply.root_msg_id, "reply_to_msg_id": reply.root_msg_id,
            "reply_to_command": command, "reply_to_sender_id": reply.command_sender,
            "reply_to_server_at": reply.start["at"], "reply_to_command_edited": False,
            "send_as_id": actor_id, "account_id": get_identity_account(actor_id),
        },
    )
    handled = await handle_wanxin_reply(
        reply.text, now, matched_family=WANXIN_ACTION_FAMILIES[action], result_msg_id=reply.result_msg_id, event=event,
    )
    if not _refresh_wanxin_observation(owner, observed):
        return False
    return bool(handled and not any(_same_wanxin_operation(item, pending) for item in _wanxin_pending_operations(observed)))


async def _recover_wanxin_operations(observed, now):
    owner = _capture_wanxin_owner()
    if not _wanxin_owner_current(owner) or observed.get("unresolved_invalid") or observed.get("reply_points_invalid"):
        return False
    active = observed.get("pending") or {}
    active_due = bool(active and max(active["reply_due_at"], observed["auto_next_time"]) <= now)
    held_due = bool(observed["unresolved_actions"] and observed["recovery_next_time"] <= now)
    if not active_due and not held_due:
        return False
    pending = ([active] if active_due else []) + list(observed["unresolved_actions"].values())
    before = copy.deepcopy(owner[1].get("wanxin_observation"))
    observed["recovery_next_time"] = now + WANXIN_RECOVERY_RETRY_SEC
    if not _commit_wanxin_observation(owner, before, observed):
        return False
    starts = [timestamp(item.get("started_at")) or timestamp(item.get("sent_at")) for item in pending]
    since = min((value for value in starts if value > 0), default=now)
    entries = [entry for entry, _received in _iter_message_log_entries_between(max(1, since - 1), now)]
    recovered = False
    for item in pending:
        if not _refresh_wanxin_observation(owner, observed):
            return recovered
        if not any(current == item for current in _wanxin_pending_operations(observed)):
            continue
        recovered = await _recover_wanxin_pending_from_message_log(observed, now, pending=item, entries=entries) or recovered
    _refresh_wanxin_observation(owner, observed)
    return recovered


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
    if action == WANXIN_ACTION_MOON_GREET:
        return float(observed.get("next_moon_greet_time", 0) or 0)
    if action == WANXIN_ACTION_MOON_SEAL:
        return float(observed.get("next_moon_seal_time", 0) or 0)
    if action == WANXIN_ACTION_MOON_JOIN:
        return float(observed.get("next_moon_join_time", 0) or 0)
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    if action == WANXIN_ACTION_IDENTIFY:
        return float(assist.get("next_identify_time", 0) or 0)
    if action == WANXIN_ACTION_BANNER:
        return float(assist.get("next_banner_time", 0) or 0)
    if action == WANXIN_ACTION_STRIP:
        return float(assist.get("next_strip_time", 0) or 0)
    return 0.0


def _assist_due_field(action, *, helper=False):
    prefix = "helper_next" if helper else "next"
    if action == WANXIN_ACTION_IDENTIFY:
        return f"{prefix}_identify_time"
    if action == WANXIN_ACTION_BANNER:
        return f"{prefix}_banner_time"
    if action == WANXIN_ACTION_STRIP:
        return f"{prefix}_strip_time"
    return ""


def _set_next_time_for_action(observed, action, next_time):
    next_time = float(next_time or 0)
    if action == WANXIN_ACTION_VISIT:
        observed["next_visit_time"] = next_time
    elif action == WANXIN_ACTION_PROTECT:
        observed["next_protect_time"] = next_time
    elif action == WANXIN_ACTION_DEDUCE:
        observed["next_deduce_time"] = next_time
    elif action == WANXIN_ACTION_MOON_GREET:
        observed["next_moon_greet_time"] = next_time
    elif action == WANXIN_ACTION_MOON_SEAL:
        observed["next_moon_seal_time"] = next_time
    elif action == WANXIN_ACTION_MOON_JOIN:
        observed["next_moon_join_time"] = next_time
    elif action == WANXIN_ACTION_IDENTIFY:
        observed["assist"]["next_identify_time"] = next_time
    elif action == WANXIN_ACTION_BANNER:
        observed["assist"]["next_banner_time"] = next_time
    elif action == WANXIN_ACTION_STRIP:
        observed["assist"]["next_strip_time"] = next_time


def _action_enabled(observed, action):
    if _unresolved_wanxin_blocks(observed, action):
        return False
    config = normalize_wanxin_auto_config(observed.get("auto_config"))
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    # Older state has no panel_observed_at. A persisted stage is enough to
    # recognize that its numeric fields came from a real panel reply.
    panel_observed = bool(
        float(observed.get("panel_observed_at", 0) or 0) > 0
        or str(observed.get("stage") or "").strip()
    )
    if action == WANXIN_ACTION_VISIT:
        return bool(config.get("visit_enabled"))
    if action == WANXIN_ACTION_PROTECT:
        return bool(config.get("protect_enabled")) and not (
            panel_observed and int(observed.get("soul_seal", 0) or 0) <= 0
        )
    if action == WANXIN_ACTION_DEDUCE:
        return bool(config.get("deduce_enabled")) and not (
            panel_observed
            and int(observed.get("curse_source", 0) or 0) >= WANXIN_CURSE_SOURCE_CAP
        )
    if action == WANXIN_ACTION_MOON_GREET:
        return bool(observed.get("moon_awakened") and config.get("moon_greet_enabled"))
    if action == WANXIN_ACTION_MOON_SEAL:
        from .concubine_affinity_actions import needs_calibration

        return bool(
            observed.get("moon_awakened")
            and config.get("moon_seal_enabled")
            and int(state.get("concubine_affinity", 0) or 0) >= WANXIN_MOON_SEAL_MIN_AFFINITY
            and not needs_calibration()
        )
    if action == WANXIN_ACTION_MOON_JOIN:
        return bool(observed.get("moon_awakened") and config.get("moon_join_enabled"))
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
        if action in WANXIN_ASSIST_ACTIONS and not _assist_action_needed(observed, action):
            continue
        due_at = _due_time_for_action(observed, action)
        if due_at <= now:
            candidates.append((due_at, action))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], actions.index(item[1])))
    return candidates[0][1]


def _assist_action_needed(observed, action):
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    commission_id = int(commission.get("id", 0) or 0)
    if commission_id <= 0 or not commission.get("accepted"):
        return False
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    identify_required = bool(assist.get("identify_enabled"))
    banner_required = bool(assist.get("banner_enabled"))
    identified = int(assist.get("identified_commission_id", 0) or 0) == commission_id
    bannered = int(assist.get("bannered_commission_id", 0) or 0) == commission_id
    if action == WANXIN_ACTION_IDENTIFY:
        return identify_required and not identified
    if action == WANXIN_ACTION_BANNER:
        return banner_required and (identified or not identify_required) and not bannered
    if action == WANXIN_ACTION_STRIP:
        return (identified or not identify_required) and (bannered or not banner_required)
    return False


async def _send_nonfinancial_action(observed, action, command, now, *, send_as_id):
    owner = _capture_wanxin_owner()
    actor = _capture_wanxin_owner(send_as_id)
    if not _wanxin_owner_current(owner, enabled=True) or not _wanxin_owner_current(actor):
        return False
    if (not actor[2] or not get_identity_enabled(actor[0]) or observed.get("pending")
            or observed.get("reply_points_invalid") or _unresolved_wanxin_blocks(observed, action)):
        return False
    commission = observed["commission"]
    pending = {
        "action": action, "family": WANXIN_ACTION_FAMILIES[action], "status": "sending",
        "op_id": uuid4().hex, "command": command, "msg_id": 0,
        "chat_id": get_game_group_id(), "send_as_id": actor[0], "account_id": actor[2],
        "owner_id": owner[0], "owner_account_id": owner[2], "started_at": now,
        "sent_at": 0, "reply_due_at": now + WANXIN_REPLY_TIMEOUT_SEC,
        "commission_id": commission["id"], "commission_published_at": commission["published_at"],
        "commission_accepted_at": commission["accepted_at"],
    }
    if not pending["chat_id"]:
        return False
    observed["pending"] = pending
    observed["auto_next_time"] = pending["reply_due_at"]
    with use_identity(owner[0]):
        _set_observed(observed)
        if save_state() is False:
            observed["pending"] = {}
            _schedule_next(observed, now, WANXIN_RECOVERY_RETRY_SEC, error="婉心在途状态未保存，本次未发送")
            _set_observed(observed)
            return False
    expected = copy.deepcopy(owner[1]["wanxin_observation"])
    owner_username = _owner_username(owner[0])

    def current_pending():
        if not _wanxin_owner_current(owner) or not _wanxin_owner_current(actor):
            return None
        current = normalize_wanxin_observation(owner[1].get("wanxin_observation"))
        if current["pending"].get("op_id") != pending["op_id"]:
            return None
        return current

    def can_send():
        return bool(
            _wanxin_owner_current(owner, enabled=True) and _wanxin_owner_current(actor)
            and get_identity_enabled(actor[0]) and current_pending() == expected
            and (actor[0] == owner[0] or _is_yinluo_identity(actor[0]))
            and _owner_username(owner[0]) == owner_username
        )

    def finish(msg=None, *, uncertain=False):
        current = current_pending()
        if current is None:
            _refresh_wanxin_observation(owner, observed)
            return bool(msg)
        before = copy.deepcopy(owner[1]["wanxin_observation"])
        record = current["pending"]
        msg_id = getattr(msg, "id", 0)
        chat_id = _safe_int(getattr(msg, "chat_id", 0))
        sent_at = timestamp(getattr(msg, "sent_at", 0))
        known = type(msg_id) is int and 0 < msg_id < 2 ** 63 and chat_id == record["chat_id"] and now - 1 <= sent_at <= max(now, time.time()) + 1
        block = classify_game_send_block(actor[0], command) if msg is None and not uncertain else {}
        if not uncertain and msg is None and block.get("status") == "unsent":
            current["pending"] = {}
            _handle_unsent_send(current, action, command, now, block=block)
        else:
            record["status"] = "sent" if known else "unknown"
            record["msg_id"] = msg_id if known else 0
            record["sent_at"] = sent_at if known else 0
            record["reply_due_at"] = (sent_at if known else max(now, time.time())) + WANXIN_REPLY_TIMEOUT_SEC
            current["auto_next_time"] = record["reply_due_at"]
            current["auto_last_action"] = action
            current["auto_last_result"] = "已发送" if known else "发送状态未知，等待原回包"
            current["auto_last_error"] = "" if known else "保留原操作，不推定成功、冷却或重新发送"
            if known and all(current["commission"][key] == pending[field] for key, field in (
                ("id", "commission_id"), ("published_at", "commission_published_at"),
                ("accepted_at", "commission_accepted_at"),
            )):
                if actor[0] == owner[0]:
                    current["assist"].update(last_anchor_msg_id=msg_id, last_anchor_at=sent_at)
                field = {"publish": "publish_msg_id", "accept": "accept_msg_id", "cancel": "cancel_msg_id"}.get(action)
                if field:
                    current["commission"][field] = msg_id
        _commit_wanxin_observation(owner, before, current)
        _refresh_wanxin_observation(owner, observed)
        return known

    inflight_key = (owner[0], action)
    _WANXIN_INFLIGHT[inflight_key] = pending["op_id"]
    try:
        msg = await send_game_command(
            command, track=True, max_retry=0, reply_timeout=WANXIN_REPLY_TIMEOUT_SEC,
            send_as_id=actor[0], source_module=WANXIN_MODULE_NAME, op_id=pending["op_id"],
            target_chat_id=pending["chat_id"], queue_timeout=WANXIN_SEND_QUEUE_TIMEOUT_SEC,
            operation_check=can_send,
        )
    except (asyncio.CancelledError, Exception):
        finish(uncertain=True)
        raise
    else:
        return finish(msg)
    finally:
        if _WANXIN_INFLIGHT.get(inflight_key) == pending["op_id"]:
            _WANXIN_INFLIGHT.pop(inflight_key, None)


async def _send_owner_action(observed, action, now, *, command_override="", cancel_evidence=None):
    command = str(command_override or WANXIN_ACTION_COMMANDS.get(action, "")).strip()
    if not command:
        return False
    if action == WANXIN_ACTION_CANCEL and (
        cancel_evidence is None or cancel_evidence.completion is not None
        or cancel_evidence.publication.parsed.get("commission_id") != observed["commission"]["id"]
        or cancel_evidence.publication.end["at"] + WANXIN_COMMISSION_TTL_SEC + CD_BUFFER_SEC > now
    ):
        return False
    # 结算避让要按本次动作的时刻判断，与下面的 _schedule_next 用同一个基准
    phaseful_reason = get_phaseful_summary_risk_reason(now)
    if phaseful_reason:
        _schedule_next(observed, now, WANXIN_PHASEFUL_DEFER_SEC, result=f"避让结算：{phaseful_reason}")
        return False
    return await _send_nonfinancial_action(observed, action, command, now, send_as_id=get_current_identity_id())


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
    return await _send_nonfinancial_action(observed, WANXIN_ACTION_ACCEPT, command, now, send_as_id=assist_send_as_id)


async def _send_assist_action(observed, action, now):
    owner = _capture_wanxin_owner()
    if not _wanxin_owner_current(owner) or _unresolved_wanxin_blocks(observed, action):
        return False
    owner_id, owner_state, owner_account = owner
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    assist_send_as_id = int(assist.get("send_as_id", 0) or 0)
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    current_owner_username = _owner_username()
    owner_username = str(current_owner_username or commission.get("owner_username") or "").strip().lstrip("@")
    if current_owner_username and current_owner_username.casefold() != str(commission.get("owner_username") or "").strip().lstrip("@").casefold():
        commission["owner_username"] = current_owner_username
        observed["commission"] = commission
    if assist_send_as_id <= 0 or not has_identity(assist_send_as_id):
        _schedule_next(observed, now, 30 * 60, error=f"协助身份不存在：{assist_send_as_id or '未配置'}")
        return False
    actor = _capture_wanxin_owner(assist_send_as_id)
    if not get_identity_enabled(assist_send_as_id):
        health = get_channel_send_as_health()
        frozen_ids = {
            int(identity_id)
            for identity_id in health.get("frozen_identity_ids") or ()
            if str(identity_id or "").strip().lstrip("-").isdigit()
        }
        restore_ids = {
            int(identity_id)
            for identity_id in health.get("restore_identity_ids") or ()
            if str(identity_id or "").strip().lstrip("-").isdigit()
        }
        if str(health.get("status") or "") == "closed" and assist_send_as_id in (frozen_ids | restore_ids):
            probe_at = float(health.get("next_probe_at", 0) or 0)
            retry_at = max(probe_at, now + 15) + CD_BUFFER_SEC
            observed["auto_next_time"] = retry_at
            observed["auto_last_action"] = action
            observed["auto_last_result"] = f"{WANXIN_ACTION_LABELS.get(action, action)} 等待频道身份恢复"
            observed["auto_last_error"] = "频道身份已冻结，等待后台权限复查"
            _push_recent(observed, now, action, "channel_identity_frozen", observed["auto_last_error"])
        else:
            _schedule_next(observed, now, 30 * 60, error="协助身份已手动停用，未发送")
        return False
    if not _is_yinluo_identity(assist_send_as_id):
        _schedule_next(observed, now, 60 * 60, error=f"协助身份不是阴罗宗：{get_identity_display_name(assist_send_as_id)}")
        return False
    recovery = get_yinluo_sha_recovery_status(assist_send_as_id)
    if recovery.get("blocked"):
        required_sha = int(recovery.get("required_sha", 0) or 0)
        current_sha = int(recovery.get("sha_current", 0) or 0)
        _set_next_time_for_action(observed, action, now + WANXIN_RESOURCE_RECOVERY_RETRY_SEC)
        _schedule_next(
            observed,
            now,
            WANXIN_RESOURCE_RECOVERY_RETRY_SEC,
            error=f"阴罗幡补煞气中：{current_sha}/{required_sha}，本轮不发送{WANXIN_ACTION_LABELS.get(action, action)}。",
        )
        return False
    if not owner_username or not _commission_accept_evidence_valid(observed):
        _schedule_next(observed, now, 30 * 60, error="婉心协助缺少有效咒契或委托方")
        return False
    base_command = WANXIN_ACTION_COMMANDS.get(action, "")
    command = f"{base_command} @{owner_username}"
    if action in {WANXIN_ACTION_BANNER, WANXIN_ACTION_STRIP}:
        expected = copy.deepcopy(owner_state.get("wanxin_observation"))
        resource_op_id = ""

        def controller_current():
            return bool(
                _wanxin_owner_current(owner, enabled=True) and _wanxin_owner_current(actor)
                and get_identity_enabled(owner_id) and _is_yinluo_identity(assist_send_as_id)
                and owner_state.get("wanxin_observation") == expected
                and _owner_username(owner_id) == current_owner_username
            )

        async def send_with_owner_intent(text, **options):
            nonlocal expected, resource_op_id
            resource_op_id = options["op_id"]
            operation = resource_accounting.current_operation(assist_send_as_id, resource_op_id)
            current = normalize_wanxin_observation(owner_state.get("wanxin_observation"))
            if not controller_current() or current["pending"] or not operation or operation["phase"] != "prepared":
                resource_accounting.record_transport(assist_send_as_id, resource_op_id, phase="unsent")
                return None
            _mark_resource_pending(current, action, operation, now, owner=owner, actor=actor)
            current["pending"]["status"] = "sending"
            current["auto_last_result"] = "发送中"
            current["auto_last_error"] = ""
            try:
                saved = _commit_wanxin_observation(owner, expected, current)
            except Exception:
                resource_accounting.record_transport(assist_send_as_id, resource_op_id, phase="unsent")
                raise
            if not saved:
                resource_accounting.record_transport(assist_send_as_id, resource_op_id, phase="unsent")
                return None
            expected = copy.deepcopy(owner_state.get("wanxin_observation"))
            _WANXIN_INFLIGHT[owner_id, action] = resource_op_id
            return await send_game_command(text, **options)

        def finish(operation, reason="", *, uncertain=False):
            if not _wanxin_owner_current(owner) or not _wanxin_owner_current(actor):
                return False
            before = copy.deepcopy(owner_state.get("wanxin_observation"))
            current = normalize_wanxin_observation(before)
            pending = current["pending"]
            if not resource_op_id or pending.get("resource_op_id") != resource_op_id:
                if before == expected:
                    _schedule_next(current, now, WANXIN_RESOURCE_RECOVERY_RETRY_SEC, error=f"阴罗协助等待：{reason}")
                    _commit_wanxin_observation(owner, before, current)
                _refresh_wanxin_observation(owner, observed)
                return bool(operation and operation["phase"] == "complete")
            if operation and operation["phase"] == "unsent":
                _remove_wanxin_pending_operation(current, pending)
                _schedule_next(current, now, WANXIN_RESOURCE_RECOVERY_RETRY_SEC, error="阴罗协助未发送，延后重试")
            else:
                known = bool(operation and operation["msg_id"] > 0)
                pending["status"] = "sent" if known and not uncertain else "unknown"
                if known:
                    pending.update(msg_id=operation["msg_id"], sent_at=operation["sent_at"])
                pending["reply_due_at"] = max(now, time.time()) + WANXIN_REPLY_TIMEOUT_SEC
                current["auto_next_time"] = pending["reply_due_at"]
                current["auto_last_result"] = "已发送" if known else "发送状态未知，等待原回包"
                current["auto_last_error"] = "" if known else "阴罗协助结果未确认（unknown），保留资源预留，不重复发送"
            _commit_wanxin_observation(owner, before, current)
            _refresh_wanxin_observation(owner, observed)
            return bool(operation and operation["phase"] in {"sent", "complete"})

        try:
            _msg, reason, operation = await send_owned_yinluo_command(
                command, now, send_as_id=assist_send_as_id, source_module=WANXIN_MODULE_NAME,
                operation_check=controller_current, sender=send_with_owner_intent,
                reply_timeout=WANXIN_REPLY_TIMEOUT_SEC, queue_timeout=WANXIN_SEND_QUEUE_TIMEOUT_SEC,
                beneficiary={
                    "identity_id": owner_id, "account_id": owner_account, "commission_id": commission["id"],
                    "published_at": commission["published_at"], "accepted_at": commission["accepted_at"],
                },
            )
        except (asyncio.CancelledError, Exception):
            finish(resource_accounting.current_operation(assist_send_as_id, resource_op_id), uncertain=True)
            raise
        else:
            return finish(operation, reason)
        finally:
            if resource_op_id and _WANXIN_INFLIGHT.get((owner_id, action)) == resource_op_id:
                _WANXIN_INFLIGHT.pop((owner_id, action), None)
    return await _send_nonfinancial_action(observed, action, command, now, send_as_id=assist_send_as_id)


def _owner_needs_commission(observed, now):
    if _unresolved_wanxin_blocks(observed, WANXIN_ACTION_PUBLISH):
        return False
    config = normalize_wanxin_auto_config(observed.get("auto_config"))
    if not config.get("publish_enabled") or not config.get("assist_enabled"):
        return False
    if not _assist_identity_ready(observed):
        return False
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    if int(commission.get("id", 0) or 0) > 0:
        return False
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    if not assist.get("strip_enabled"):
        return False
    return _due_time_for_action(observed, WANXIN_ACTION_STRIP) <= now


def _owner_needs_accept(observed):
    if _unresolved_wanxin_blocks(observed, WANXIN_ACTION_ACCEPT):
        return False
    config = normalize_wanxin_auto_config(observed.get("auto_config"))
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    return bool(
        config.get("assist_enabled")
        and _assist_identity_ready(observed)
        and int(commission.get("id", 0) or 0) > 0
        and not commission.get("accepted")
        and not commission.get("claimed_elsewhere")
    )


def _owner_needs_cancel(observed, now):
    if _unresolved_wanxin_blocks(observed, WANXIN_ACTION_CANCEL):
        return False
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    return bool(
        int(commission.get("id", 0) or 0) > 0
        and commission.get("claimed_elsewhere")
        and float(commission.get("cancel_due_at", 0) or 0) <= now
    )


def _commission_accept_evidence_valid(observed):
    commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
    if not commission.get("accepted"):
        return False
    helper_username = str(commission.get("helper_username") or "").strip().lstrip("@").casefold()
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else {}
    assist_send_as_id = int(assist.get("send_as_id", 0) or 0)
    assist_usernames = _identity_username_keys(assist_send_as_id)
    if helper_username and assist_usernames and helper_username not in assist_usernames:
        return False
    return bool(
        helper_username
        or int(commission.get("accept_msg_id", 0) or 0) > 0
        or float(commission.get("accepted_at", 0) or 0) > 0
    )


async def run_wanxin_scheduler(now):
    owner = _capture_wanxin_owner()
    if not _wanxin_owner_current(owner, enabled=True):
        return
    now = float(now or time.time())
    before = copy.deepcopy(owner[1].get("wanxin_observation"))
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))

    def commit():
        return _commit_wanxin_observation(owner, before, observed)

    try:
        current_identity_id = get_current_identity_id()
        if _is_yinluo_identity(current_identity_id):
            observed["auto_last_result"] = "阴罗协助身份：等待委托方锚点，不主动跑婉心主线"
            observed["auto_next_time"] = now + 30 * 60
            commit()
            return
        if observed.get("reply_points_invalid") or observed.get("unresolved_invalid"):
            if float(observed.get("auto_next_time", 0) or 0) <= now:
                _schedule_next(observed, now, WANXIN_RECOVERY_RETRY_SEC, error="婉心回包证据损坏，保留记录等待核实，不自动重发")
                commit()
            return
        if await _recover_wanxin_operations(observed, now):
            if _wanxin_owner_current(owner):
                await send_audit_log("🧊 婉心日志补偿：已采纳超时回包。", scope="identity", send_as_id=owner[0], limit=180)
            return
        if not _wanxin_owner_current(owner, enabled=True):
            return
        before = copy.deepcopy(owner[1].get("wanxin_observation"))
        observed = normalize_wanxin_observation(before)
        if _pending_blocks(observed, now):
            commit()
            return
        if observed != normalize_wanxin_observation(before):
            if not commit():
                return
            before = copy.deepcopy(owner[1].get("wanxin_observation"))
            observed = normalize_wanxin_observation(before)
        if observed.get("available") == "no":
            if float(observed.get("auto_next_time", 0) or 0) <= now:
                observed["auto_next_time"] = now + WANXIN_UNAVAILABLE_BACKOFF_SEC
            commit()
            return
        if float(observed.get("auto_next_time", 0) or 0) > now:
            return
        if not (observed.get("commission") or {}).get("owner_username"):
            observed["commission"]["owner_username"] = _owner_username()
        if _recover_external_commission_claim_from_log(observed, now):
            commit()
            return
        if _recover_claimed_commission_completion_from_log(observed, now):
            commit()
            return
        if _owner_needs_cancel(observed, now):
            evidence = _external_commission_evidence(observed, now)
            if evidence is not None and evidence.completion is None:
                await _send_owner_action(observed, WANXIN_ACTION_CANCEL, now, cancel_evidence=evidence)
                commit()
                return
            observed["auto_last_error"] = "旧委托缺少当前原生归属证据，暂不取消；其他婉心动作照常判断"
        if _owner_needs_commission(observed, now):
            reward = int(observed.get("auto_config", {}).get("reward_lingshi", 1) or 1)
            await _send_owner_action(
                observed,
                WANXIN_ACTION_PUBLISH,
                now,
                command_override=f"{CMD_WANXIN_PUBLISH_COMMISSION} {reward}",
            )
            commit()
            return
        if _owner_needs_accept(observed):
            await _send_accept_action(observed, now)
            commit()
            return
        commission = observed.get("commission") if isinstance(observed.get("commission"), dict) else {}
        if bool(commission.get("accepted")) and not _commission_accept_evidence_valid(observed):
            commission["accepted"] = False
            observed["commission"] = commission
            if int(commission.get("id", 0) or 0) > 0:
                observed["auto_next_time"] = now
                observed["auto_last_error"] = "咒契缺少真实接取证据，先重新接取"
            else:
                _schedule_next(observed, now, 30 * 60, error="咒契缺少真实接取证据，需重新发布并接取")
            commit()
            return
        if bool(commission.get("accepted")):
            assist_action = _next_due_action(observed, now, WANXIN_ASSIST_ACTIONS)
            if assist_action:
                await _send_assist_action(observed, assist_action, now)
                commit()
                return
        self_action = _next_due_action(observed, now, WANXIN_SELF_ACTIONS)
        if self_action:
            await _send_owner_action(observed, self_action, now)
            commit()
            return
        next_times = [
            _due_time_for_action(observed, action)
            for action in WANXIN_SELF_ACTIONS + WANXIN_ASSIST_ACTIONS
            if _action_enabled(observed, action) and _due_time_for_action(observed, action) > now
        ]
        if observed["unresolved_actions"]:
            next_times.append(max(now + WANXIN_CHAIN_STEP_SEC, observed["recovery_next_time"]))
        observed["auto_next_time"] = min(next_times) if next_times else now + 30 * 60
        if not observed["unresolved_actions"]:
            observed["auto_last_error"] = ""
        commit()
    except Exception as exc:
        observed["auto_last_error"] = f"婉心调度异常：{exc}"
        observed["auto_next_time"] = now + WANXIN_RECOVERY_RETRY_SEC
        commit()
        if _wanxin_owner_current(owner):
            await send_audit_log(f"⚠️ 婉心调度异常：{exc}", scope="identity", send_as_id=owner[0], limit=220)


async def run_wanxin_phaseful_cleanup_scheduler(now):
    now = float(now or time.time())
    await _cleanup_wanxin_pending_only(now)


async def _cleanup_wanxin_pending_only(now):
    owner = _capture_wanxin_owner()
    if not _wanxin_owner_current(owner):
        return False
    before = copy.deepcopy(owner[1].get("wanxin_observation"))
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))
    pending_before = dict(observed.get("pending") or {})
    if not _wanxin_pending_operations(observed):
        return False
    recovered = await _recover_wanxin_operations(observed, now)
    if not _wanxin_owner_current(owner):
        return recovered
    before = copy.deepcopy(owner[1].get("wanxin_observation"))
    observed = normalize_wanxin_observation(before)
    if observed["pending"] != pending_before:
        return recovered
    _pending_blocks(observed, now)
    _commit_wanxin_observation(owner, before, observed)
    return recovered or not _wanxin_pending_operations(observed)


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
            await _cleanup_wanxin_pending_only(now)


def _apply_success_cooldown(observed, action, now, parsed=None):
    parsed = parsed or {}
    now = timestamp(parsed.get("_completed_at")) or now
    if action == WANXIN_ACTION_VISIT:
        observed["last_visit_day"] = get_day_key(now)
        observed["next_visit_time"] = _next_daily_after(now)
    elif action == WANXIN_ACTION_PROTECT:
        observed["next_protect_time"] = now + WANXIN_PROTECT_CD_SEC + CD_BUFFER_SEC
    elif action == WANXIN_ACTION_DEDUCE:
        observed["next_deduce_time"] = now + WANXIN_DEDUCE_CD_SEC + CD_BUFFER_SEC
    elif action == WANXIN_ACTION_MOON_GREET:
        observed["next_moon_greet_time"] = now + WANXIN_MOON_GREET_CD_SEC + CD_BUFFER_SEC
    elif action == WANXIN_ACTION_MOON_SEAL:
        observed["next_moon_seal_time"] = now + WANXIN_MOON_SEAL_CD_SEC + CD_BUFFER_SEC
    elif action == WANXIN_ACTION_MOON_JOIN:
        observed["next_moon_join_time"] = now + WANXIN_MOON_JOIN_CD_SEC + CD_BUFFER_SEC
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


def _project_owner_reply(observed, parsed, now, *, owner_id, action, command_msg_id, result_msg_id, text, affinity):
    """Project one admitted result; the caller owns pending removal and commit."""
    updates = {}
    if result_msg_id:
        observed["last_observed_at"] = float(now)
    if command_msg_id > 0:
        observed["assist"]["last_anchor_msg_id"] = command_msg_id
        observed["assist"]["last_anchor_at"] = float(now)
    _apply_panel_values(observed, parsed.get("values") or {}, now)

    ptype = parsed.get("type")
    if ptype == "unavailable":
        observed["available"] = "no"
        observed["auto_last_error"] = parsed.get("summary") or "缺少婉心封魂前置"
        observed["auto_next_time"] = now + WANXIN_UNAVAILABLE_BACKOFF_SEC
        updates["wanxin_enabled"] = False
    elif ptype in {"commission_published", "commission_existing"}:
        commission = observed["commission"]
        previous_commission_id = int(commission.get("id", 0) or 0)
        commission["id"] = int(parsed.get("commission_id", 0) or 0)
        if ptype == "commission_published":
            commission["published_at"] = timestamp(parsed.get("_completed_at")) or float(now)
        elif commission["id"] != previous_commission_id:
            commission["published_at"] = 0
        commission["owner_username"] = commission.get("owner_username") or _owner_username(owner_id)
        if command_msg_id > 0:
            commission["publish_msg_id"] = command_msg_id
        if commission["id"] != previous_commission_id:
            commission["claimed_elsewhere"] = False
            commission["claim_helper_username"] = ""
            commission["cancel_due_at"] = 0
            commission["cancel_msg_id"] = 0
            commission["accepted"] = False
            commission["accepted_at"] = 0
            commission["accept_msg_id"] = 0
            commission["helper_username"] = ""
            observed["assist"]["identified_commission_id"] = 0
            observed["assist"]["bannered_commission_id"] = 0
        observed["auto_last_result"] = f"{'已有' if ptype == 'commission_existing' else '委托已发布'}：{commission['id'] or '未知'}"
        observed["auto_last_error"] = "" if commission["id"] else "委托存在但未解析到ID"
        observed["auto_next_time"] = float(now)
    elif ptype == "commission_cancelled":
        _consume_commission(observed, owner_id=owner_id)
        observed["auto_last_action"] = WANXIN_ACTION_CANCEL
        observed["auto_last_result"] = parsed.get("summary") or "委托已取消"
        observed["auto_last_error"] = ""
        _schedule_next(observed, now)
    elif ptype == "commission_cancel_blocked":
        commission = observed["commission"]
        commission["claimed_elsewhere"] = True
        commission["cancel_due_at"] = max(
            _commission_cancel_due_at(commission, now),
            now + WANXIN_COMMISSION_CANCEL_RETRY_SEC,
        )
        observed["auto_last_action"] = WANXIN_ACTION_CANCEL
        observed["auto_last_result"] = parsed.get("summary") or "委托尚未到期"
        observed["auto_last_error"] = ""
        observed["auto_next_time"] = float(commission["cancel_due_at"])
    elif ptype in {"commission_claimed_elsewhere", "commission_expired"}:
        _mark_commission_claimed_elsewhere(observed, now, reason=parsed.get("summary") or "委托已被他人接取")
    elif ptype in {"panel", "help", "moon_panel", "moon_awakened"}:
        if ptype in {"moon_panel", "moon_awakened"}:
            observed["moon_awakened"] = True
        if ptype == "moon_panel" and parsed.get("affinity") is not None:
            updates["concubine_affinity"] = max(0, int(parsed.get("affinity", 0) or 0))
        observed["auto_last_result"] = parsed.get("summary") or "已校准"
        observed["auto_last_error"] = ""
    elif ptype == "commission_invalid":
        _mark_commission_invalid(observed, now, parsed.get("summary") or "咒契失效", owner_id=owner_id)
    elif ptype == "cooldown":
        next_time = float(parsed.get("next_time", 0) or now + WANXIN_RECOVERY_RETRY_SEC)
        action = parsed.get("cooldown_action") or action
        _set_cooldown_from_reply(observed, action, next_time)
        observed["auto_last_result"] = f"{WANXIN_ACTION_LABELS.get(action, '婉心动作')}冷却中"
        observed["auto_last_error"] = ""
        _schedule_next(observed, now)
    elif ptype in {
        "visit_success", "visit_already", "protect_success", "deduce_success",
        "moon_greet_success", "moon_greet_already", "moon_seal_success", "moon_join_success",
    }:
        if ptype in {"visit_success", "visit_already"}:
            action = WANXIN_ACTION_VISIT
        elif ptype == "protect_success":
            action = WANXIN_ACTION_PROTECT
        elif ptype == "deduce_success":
            action = WANXIN_ACTION_DEDUCE
        elif ptype in {"moon_greet_success", "moon_greet_already"}:
            action = WANXIN_ACTION_MOON_GREET
            observed["moon_awakened"] = True
            if ptype == "moon_greet_success":
                updates["concubine_affinity"] = max(0, affinity + int(parsed.get("affinity_gain", 0) or 0))
        elif ptype == "moon_seal_success":
            action = WANXIN_ACTION_MOON_SEAL
            observed["moon_awakened"] = True
            updates["concubine_affinity"] = max(0, affinity - int(parsed.get("affinity_cost", 0) or 0))
        else:
            action = WANXIN_ACTION_MOON_JOIN
            observed["moon_awakened"] = True
        _apply_success_cooldown(observed, action, now, parsed)
        observed["auto_last_result"] = parsed.get("summary") or "成功"
        observed["auto_last_error"] = ""
        _schedule_next(observed, now)
    elif ptype == "moon_join_blocked":
        observed["next_moon_join_time"] = now + WANXIN_MOON_JOIN_CD_SEC
        observed["auto_last_result"] = parsed.get("summary") or "封魂未解"
        observed["auto_last_error"] = ""
        _schedule_next(observed, now)
    elif ptype == "moon_unavailable":
        observed["moon_awakened"] = False
        observed["auto_last_result"] = parsed.get("summary") or "婉影玩法尚不可用"
        observed["auto_last_error"] = ""
        _schedule_next(observed, now, 24 * 3600)
    else:
        if ptype == "unknown":
            return None
        observed["auto_last_result"] = parsed.get("summary") or ptype
        observed["auto_last_error"] = ""
        _schedule_next(observed, now)
    _push_recent(observed, now, action or ptype, parsed.get("summary") or ptype, text)
    return updates


def _project_assist_reply(observed, parsed, now, *, owner_id, action, result_msg_id):
    _apply_panel_values(observed, parsed.get("values") or {}, now)
    ptype = parsed.get("type")
    if ptype == "commission_accepted":
        commission = observed["commission"]
        helper_username = parsed.get("helper_username") or ""
        assist_send_as_id = int((observed.get("assist") or {}).get("send_as_id", 0) or 0)
        assist_usernames = _identity_username_keys(assist_send_as_id)
        if helper_username and assist_usernames and helper_username.casefold() not in assist_usernames:
            _mark_commission_claimed_elsewhere(observed, now, helper_username, f"委托被 @{helper_username} 接取")
        else:
            commission["accepted"] = True
            commission["accepted_at"] = timestamp(parsed.get("_completed_at")) or float(now)
            commission["helper_username"] = helper_username or commission.get("helper_username") or ""
            commission["claimed_elsewhere"] = False
            commission["claim_helper_username"] = ""
            commission["cancel_due_at"] = 0
            if result_msg_id:
                commission["accept_msg_id"] = int(result_msg_id)
            observed["auto_last_result"] = f"咒契已成：@{commission['helper_username'] or '阴罗咒师'}"
            observed["auto_last_error"] = ""
            _schedule_next(observed, now)
    elif ptype == "assist_identify_success":
        commission_id = int((observed.get("commission") or {}).get("id", 0) or 0)
        observed["assist"]["identified_commission_id"] = commission_id
        _apply_success_cooldown(observed, action, now, parsed)
        observed["assist"]["last_action"] = action
        observed["assist"]["last_result"] = parsed.get("summary") or "协助成功"
        observed["assist"]["last_error"] = ""
        observed["assist"]["last_contrib_gain"] = int(parsed.get("contrib_gain", 0) or 0)
        observed["auto_last_result"] = parsed.get("summary") or "协助成功"
        observed["auto_last_error"] = ""
        _schedule_next(observed, now)
    elif ptype in {"assist_missing_target", "assist_not_yinluo"}:
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
    elif ptype == "commission_invalid":
        _mark_commission_invalid(observed, now, parsed.get("summary") or "咒契失效", owner_id=owner_id)
    elif ptype in {"commission_claimed_elsewhere", "commission_expired"}:
        _mark_commission_claimed_elsewhere(observed, now, reason=parsed.get("summary") or "委托已被他人接取")
    elif ptype == "cooldown":
        next_time = float(parsed.get("next_time", 0) or now + WANXIN_RECOVERY_RETRY_SEC)
        _set_cooldown_from_reply(observed, parsed.get("cooldown_action") or action, next_time)
        observed["auto_last_result"] = f"{WANXIN_ACTION_LABELS.get(action, '协助动作')}冷却中"
        observed["auto_last_error"] = ""
        _schedule_next(observed, now)
    else:
        return None
    _push_recent(observed, now, action or ptype, parsed.get("summary") or ptype)
    return {}


def _clear_wanxin_source_pending(source):
    if not source["actor_id"]:
        return
    actor = get_identity_state(source["actor_id"])
    for key, pending in actor.get("pending_tasks", {}).items():
        if not isinstance(pending, dict):
            continue
        try:
            chat_id, msg_id = message_key_parts(key, pending)
        except (ValueError, TypeError, OverflowError):
            continue
        if (chat_id, msg_id) == (source["chat_id"], source["msg_id"]) and (
            pending.get("account_id", source["account_id"]) != source["account_id"]
            or _parse_wanxin_command(get_pending_command(pending)) != _parse_wanxin_command(source["command"])
        ):
            return
    clear_pending_by_reply(
        send_as_id=source["actor_id"], reply_context={
            "send_as_id": source["actor_id"], "chat_id": source["chat_id"],
            "root_msg_id": source["msg_id"], "reply_to_msg_id": source["msg_id"],
            "family": WANXIN_ACTION_FAMILIES[source["action"]],
        }, clear_family=False,
    )


async def handle_wanxin_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0, *, event=None):
    resource_source = resource_accounting.admit_yinluo_resource_source(event, **resource_accounting.event_trust(now))
    if resource_source is not None and resource_source.command.action in {"assist_banner", "assist_strip"}:
        return bool(event.text == text and handle_yinluo_resource_reply(event, now=now))
    source = _native_wanxin_source(event, now)
    if source is None or event.text != text or (result_msg_id and result_msg_id != event.msg_id):
        return False
    action = source["action"]
    family = WANXIN_ACTION_FAMILIES[action]
    if matched_family and matched_family != family:
        return False
    parsed = parse_wanxin_text(text, now=event.server_event_at, family=family)
    if not parsed:
        return False
    owner_id = _wanxin_source_owner(source, parsed)
    owner = _capture_wanxin_owner(owner_id) if owner_id else None
    if not _wanxin_owner_current(owner):
        return False
    before = copy.deepcopy(owner[1].get("wanxin_observation"))
    observed = normalize_wanxin_observation(before)
    if observed.get("reply_points_invalid") or observed.get("unresolved_invalid"):
        return False
    operations = _wanxin_pending_operations(observed)
    matching = [item for item in operations if _pending_matches_wanxin_source(item, source, owner_id)]
    if len(matching) > 1:
        return False
    pending = matching[0] if matching else None
    if any(item.get("action") == action and (
        item.get("account_id", 0) not in {0, source["account_id"]}
        or item.get("owner_account_id", 0) not in {0, owner[2]}
    ) for item in operations):
        return False
    if pending is None and any(item.get("op_id") and item["action"] == action and not item["msg_id"] for item in operations):
        # The shared early-reply cache replays this after an exact send receipt.
        return False
    commission = observed["commission"]
    command_point = {"at": source["command_at"], "evidence": {
        "source": "telegram", "chat_id": source["chat_id"], "msg_id": source["msg_id"], "edited": False,
    }}
    if action in {WANXIN_ACTION_CANCEL, WANXIN_ACTION_ACCEPT, WANXIN_ACTION_IDENTIFY}:
        fields = ["publication_point"] + (["acceptance_point"] if action == WANXIN_ACTION_IDENTIFY else [])
        if any(commission.get(field) is not None and compare_points(command_point, commission[field]) != 1 for field in fields):
            return False
    if action in {WANXIN_ACTION_CANCEL, WANXIN_ACTION_ACCEPT, WANXIN_ACTION_IDENTIFY} and (
        source["command_at"] < timestamp(commission.get("published_at"))
        or (action == WANXIN_ACTION_IDENTIFY and source["command_at"] < timestamp(commission.get("accepted_at")))
    ):
        return False
    if action == WANXIN_ACTION_PUBLISH and source["command_at"] < timestamp(commission.get("publish_command_at")):
        return False
    if (action, parsed["type"]) in {
        (WANXIN_ACTION_ACCEPT, "commission_accepted"), (WANXIN_ACTION_IDENTIFY, "assist_identify_success"),
    }:
        helper_name = str(parsed.get("helper_username") or "").casefold()
        helper_id = observed["assist"]["send_as_id"]
        if source["actor_id"]:
            if helper_name not in _identity_username_keys(source["actor_id"]):
                return False
        elif helper_name in _identity_username_keys(helper_id):
            return False
        target_name = str(parsed.get("target_username") or "").casefold()
        if target_name not in _identity_username_keys(owner_id):
            return False
    points = observed.get("reply_points") if isinstance(observed.get("reply_points"), dict) else {}
    previous = points.get(action)
    semantic = {key: value for key, value in parsed.items() if key not in {"family", "next_time"}}
    fingerprint = hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    if previous is not None:
        if (not isinstance(previous, dict) or not valid_point(previous.get("point"), telegram_only=True)
                or not valid_point(previous.get("command_point"), telegram_only=True)):
            return False
        command_relation = compare_points(command_point, previous["command_point"])
        if command_relation is None or command_relation < 0:
            return False
        if command_relation == 0:
            relation = compare_points(source["point"], previous["point"])
            if relation is None or relation < 0:
                return False
            if relation == 0 and not (previous.get("handled") and previous.get("fingerprint") == fingerprint):
                return False
    same_fact = bool(
        previous and previous.get("root") == source["msg_id"]
        and previous["point"]["evidence"]["chat_id"] == source["chat_id"]
        and previous.get("fingerprint") == fingerprint and previous.get("handled")
    )
    completed_same_command = bool(
        previous and previous.get("handled") and previous.get("root") == source["msg_id"]
        and previous["point"]["evidence"]["chat_id"] == source["chat_id"]
    )
    if completed_same_command and (
        not _wanxin_reply_type_matches(action, parsed) or previous.get("type") != parsed["type"]
    ):
        # Partial or contradictory edits cannot retire already-accounted effects.
        return False
    if not _wanxin_reply_type_matches(action, parsed):
        points[action] = {
            "point": source["point"], "command_point": command_point,
            "root": source["msg_id"], "fingerprint": fingerprint, "handled": False,
        }
        observed["reply_points"] = points
        if pending is not None:
            observed["auto_last_error"] = "原命令回包未形成可确认结果，保留原操作"
        _commit_wanxin_observation(owner, before, observed)
        return False
    if same_fact:
        observed["reply_points"][action]["point"] = source["point"]
        _remove_wanxin_pending_operation(observed, pending)
        return _commit_wanxin_observation(owner, before, observed, completed_source=source)
    affinity_delta = 0
    if parsed["type"] == "moon_greet_success":
        affinity_delta = int(parsed.get("affinity_gain", 0))
    elif parsed["type"] == "moon_seal_success":
        affinity_delta = -int(parsed.get("affinity_cost", 0))
    if previous and previous.get("handled") and previous.get("root") == source["msg_id"] and previous["point"]["evidence"]["chat_id"] == source["chat_id"]:
        prior_delta = previous.get("affinity_delta", 0)
        if type(prior_delta) is not int:
            return False
        if parsed["type"] == "moon_greet_success":
            parsed = dict(parsed, affinity_gain=affinity_delta - prior_delta)
        elif parsed["type"] == "moon_seal_success":
            parsed = dict(parsed, affinity_cost=-(affinity_delta - prior_delta))
    completed_point = source["point"]
    if (previous and previous.get("handled") and previous.get("root") == source["msg_id"]
            and previous["point"]["evidence"]["chat_id"] == source["chat_id"]
            and previous.get("type") == parsed["type"]
            and previous.get("commission_id", 0) == parsed.get("commission_id", 0)):
        completed_point = previous.get("completed_point") or previous["point"]
    parsed = dict(parsed, _completed_at=completed_point["at"])
    previous_commission_id = commission["id"]
    if action in {WANXIN_ACTION_ACCEPT, WANXIN_ACTION_IDENTIFY}:
        updates = _project_assist_reply(
            observed, parsed, event.server_event_at, owner_id=owner_id, action=action, result_msg_id=event.msg_id,
        )
    else:
        updates = _project_owner_reply(
            observed, parsed, event.server_event_at, owner_id=owner_id, action=action,
            command_msg_id=source["msg_id"], result_msg_id=event.msg_id, text=text,
            affinity=int(owner[1].get("concubine_affinity", 0) or 0),
        )
    if updates is None:
        return False
    if parsed["type"] == "moon_panel" and "concubine_affinity" in updates:
        from .concubine_affinity_actions import snapshot_is_stale

        with use_identity(owner_id):
            if snapshot_is_stale(source["command_at"], now=now):
                updates.pop("concubine_affinity")
    snapshot_at = timestamp(owner[1].get("concubine_last_snapshot_at"))
    if "concubine_affinity" in updates and 0 < snapshot_at <= now:
        # A read's later edit is not a new observation; a covered delta is not a new gain/cost.
        affinity_at = source["command_at"] if parsed["type"] == "moon_panel" else event.server_event_at
        if affinity_at < snapshot_at:
            updates.pop("concubine_affinity")
        elif affinity_at == snapshot_at:
            # Query validation belongs to concubine; import after both modules initialize.
            from .concubine import same_clock_native_status_covers

            point = command_point if parsed["type"] == "moon_panel" else source["point"]
            with use_identity(owner_id):
                if same_clock_native_status_covers(point, now=now):
                    updates.pop("concubine_affinity")
    _remove_wanxin_pending_operation(observed, pending)
    if observed["pending"]:
        observed["auto_next_time"] = max(now, observed["pending"]["reply_due_at"])
    if action == WANXIN_ACTION_PUBLISH and parsed["type"] in {"commission_published", "commission_existing"}:
        observed["commission"].update(
            publish_chat_id=source["chat_id"], publish_command_at=source["command_at"],
            owner_id=owner_id, owner_account_id=owner[2],
        )
        if parsed["type"] == "commission_published":
            observed["commission"]["publication_point"] = completed_point
            observed["commission"].pop("acceptance_point", None)
        elif observed["commission"]["id"] != previous_commission_id:
            observed["commission"].pop("publication_point", None)
            observed["commission"].pop("acceptance_point", None)
    if action == WANXIN_ACTION_ACCEPT and parsed["type"] == "commission_accepted" and observed["commission"]["accepted"]:
        observed["commission"]["accept_msg_id"] = source["msg_id"]
        observed["commission"]["acceptance_point"] = completed_point
    observed["reply_points"] = dict(points, **{action: {
        "point": source["point"], "command_point": command_point,
        "root": source["msg_id"], "fingerprint": fingerprint, "handled": True,
        "affinity_delta": affinity_delta,
        "completed_point": completed_point, "type": parsed["type"], "commission_id": parsed.get("commission_id", 0),
    }})
    return _commit_wanxin_observation(owner, before, observed, completed_source=source, identity_updates=updates)


def set_wanxin_config(config):
    if not isinstance(config, dict):
        return False, "婉心配置必须是对象。", get_wanxin_ui_state()
    observed = normalize_wanxin_observation(state.get("wanxin_observation"))
    auto_config = normalize_wanxin_auto_config(observed.get("auto_config"))
    assist = observed.get("assist") if isinstance(observed.get("assist"), dict) else _default_wanxin_assist()
    for key in (
        "visit_enabled", "protect_enabled", "deduce_enabled", "publish_enabled", "assist_enabled",
        "moon_greet_enabled", "moon_seal_enabled", "moon_join_enabled",
    ):
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
    held = observed.get("unresolved_actions")
    unresolved = [
        {"action": action, "label": WANXIN_ACTION_LABELS[action], "msg_id": held[action].get("msg_id", 0)}
        for action in WANXIN_ACTION_FAMILIES
        if isinstance(held, dict) and isinstance(held.get(action), dict)
    ]
    invalid = bool(observed.get("unresolved_invalid") or observed.get("reply_points_invalid"))
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
        "moon_awakened": bool(observed.get("moon_awakened")),
        "next_moon_greet_time": fmt_abs_ts(observed.get("next_moon_greet_time", 0) or 0),
        "next_moon_seal_time": fmt_abs_ts(observed.get("next_moon_seal_time", 0) or 0),
        "next_moon_join_time": fmt_abs_ts(observed.get("next_moon_join_time", 0) or 0),
        "commission": {
            "id": int(commission.get("id", 0) or 0),
            "accepted": bool(commission.get("accepted")),
            "owner_username": commission.get("owner_username") or "",
            "helper_username": commission.get("helper_username") or "",
            "publish_msg_id": int(commission.get("publish_msg_id", 0) or 0),
            "accepted_at": fmt_abs_ts(commission.get("accepted_at", 0) or 0),
            "claimed_elsewhere": bool(commission.get("claimed_elsewhere")),
            "claim_helper_username": commission.get("claim_helper_username") or "",
            "cancel_due_at": fmt_abs_ts(commission.get("cancel_due_at", 0) or 0),
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
        "unresolved_actions": unresolved,
        "unresolved_invalid": invalid,
        "unresolved_summary": "记录异常，待核实" if invalid else "、".join(item["label"] for item in unresolved),
        "recovery_next_time": fmt_abs_ts(observed.get("recovery_next_time", 0)),
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
        f"- 委托：ID {commission.get('id') or '无'}｜{'他人接取，待取消' if commission.get('claimed_elsewhere') else ('已接取' if commission.get('accepted') else '未接取')}",
        f"- 阴罗协助：{assist.get('send_as_id') or '未配置'}｜辨咒 {fmt_abs_ts(assist.get('next_identify_time', 0))}｜借幡 {fmt_abs_ts(assist.get('next_banner_time', 0))}｜剥离 {fmt_abs_ts(assist.get('next_strip_time', 0))}",
        f"- 锚点：{assist.get('last_anchor_msg_id') or '无'}",
        f"- 自动调度：{fmt_abs_ts(observed.get('auto_next_time', 0))}（{fmt_remaining(observed.get('auto_next_time', 0))}）",
    ]
    if observed.get("pending"):
        pending = observed["pending"]
        lines.append(f"- 待回复：{WANXIN_ACTION_LABELS.get(pending.get('action'), pending.get('action'))} msg={pending.get('msg_id')} due={fmt_abs_ts(pending.get('reply_due_at', 0))}")
    if observed.get("unresolved_invalid") or observed.get("reply_points_invalid"):
        lines.append("- 未确认记录异常：保留原证据，暂停新动作")
    elif observed.get("unresolved_actions"):
        labels = "、".join(WANXIN_ACTION_LABELS[action] for action in observed["unresolved_actions"])
        lines.append(f"- 未确认动作：{labels}；不重复发送")
        lines.append(f"- 回包复核：{fmt_abs_ts(observed.get('recovery_next_time', 0))}")
    if commission.get("claimed_elsewhere"):
        lines.append(f"- 委托解锁：{fmt_abs_ts(commission.get('cancel_due_at', 0))}（{fmt_remaining(commission.get('cancel_due_at', 0))}）")
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
