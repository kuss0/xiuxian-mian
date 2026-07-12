import asyncio
import random
import re
import time
from types import SimpleNamespace

from ..config import (
    CD_BUFFER_SEC,
    CMD_SMALL_WORLD_BARRIER,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_PREACH,
    CMD_SMALL_WORLD_QUERY,
    CMD_SMALL_WORLD_RELIEF,
    CMD_SMALL_WORLD_REFINE,
    SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC,
)
from ..action_guard import clear_remote_block as clear_action_guard_remote_block
from ..action_guard import close_action as close_action_guard_action
from ..action_guard import get_blocked_until as get_action_guard_blocked_until
from ..action_guard import note_remote_block as note_action_guard_remote_block
from ..message_log_recovery import find_message_log_replies
from ..persistence import mark_dirty, save_state
from ..runtime import classify_game_send_block, clear_pending_tasks_by_commands, console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_identity_enabled, get_identity_ids, get_send_as_tags, is_cave_public_auto_enabled, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .storage_bag import apply_storage_bag_item_text_delta

SMALL_WORLD_TARGET_TAG_PATTERN = r"[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+"
SMALL_WORLD_CHAIN_COMMANDS = {
    CMD_SMALL_WORLD_QUERY,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_REFINE,
}
SMALL_WORLD_GOD_COMMANDS = {CMD_SMALL_WORLD_PREACH, CMD_SMALL_WORLD_RELIEF}
SMALL_WORLD_BARRIER_COMMANDS = {CMD_SMALL_WORLD_BARRIER}
SMALL_WORLD_CHAIN_PENDING = {"query_pending", "manifest_pending", "harvest_pending", "refine_pending"}
SMALL_WORLD_PENDING_TIMEOUT_SEC = 20 * 60
SMALL_WORLD_MANIFEST_PENDING_TIMEOUT_SEC = 3 * 60
SMALL_WORLD_REFRESH_MIN_SEC = 10 * 60
SMALL_WORLD_REFRESH_MAX_SEC = 10 * 60
SMALL_WORLD_MAX_REFRESH_ATTEMPTS = 5
SMALL_WORLD_CYCLE_CD_SEC = 6 * 3600
SMALL_WORLD_MANIFEST_CD_SEC = 6 * 3600
SMALL_WORLD_MANIFEST_RESOURCE_PAUSE_SEC = 6 * 3600
SMALL_WORLD_LONG_PAUSE_SEC = 6 * 3600
SMALL_WORLD_JITTER_MIN_SEC = 60
SMALL_WORLD_JITTER_MAX_SEC = 20 * 60
SMALL_WORLD_INITIAL_CHECK_MIN_SEC = 10 * 60
SMALL_WORLD_INITIAL_CHECK_MAX_SEC = 30 * 60
SMALL_WORLD_TOOL_STEP_MIN_SEC = 120
SMALL_WORLD_TOOL_STEP_MAX_SEC = 240
SMALL_WORLD_THEFT_CALIBRATION_MIN_SEC = 30
SMALL_WORLD_THEFT_CALIBRATION_MAX_SEC = 90
SMALL_WORLD_MIN_HARVEST_INCENSE = 10.0
SMALL_WORLD_DEFAULT_STATUS_MAX = 100
# 布道、赈灾和安抚信徒共享同一条约 3 小时的神谕冷却。
# 小世界面板与显灵仍按各自 6 小时周期运行。
SMALL_WORLD_GOD_FOLLOWUP_SEC = 3 * 3600
SMALL_WORLD_GOD_RESEND_GUARD_SEC = 5 * 60
SMALL_WORLD_GOD_PRIORITY_MAINTENANCE = 10
SMALL_WORLD_GOD_PRIORITY_DISASTER = 100
SMALL_WORLD_DISASTER_WAVE_INTERVAL_SEC = 3 * 3600
SMALL_WORLD_DISASTER_GUARD_BEFORE_SEC = 30 * 60
SMALL_WORLD_DISASTER_GUARD_AFTER_SEC = 25 * 60
SMALL_WORLD_RELIEF_POPULATION_RATIO_TRIGGER = 0.95
SMALL_WORLD_RELIEF_STABILITY_RATIO_TRIGGER = 0.80
SMALL_WORLD_BARRIER_REPLY_TIMEOUT_SEC = 10 * 60
SMALL_WORLD_BARRIER_PANEL_MAX_AGE_SEC = 6 * 3600
SMALL_WORLD_SAME_COMMAND_GUARD_SEC = 95
SMALL_WORLD_LOG_REPLAY_LOOKBACK_SEC = 20 * 60
SMALL_WORLD_LOCAL_UNSENT_BLOCK_KINDS = {
    "action_guard",
    "account_offline",
    "bot_health",
    "dungeon_quiet",
    "global_disabled",
    "global_recovery_cooldown",
    "identity_weak",
    "pre_send_guard",
    "send_blocked",
    "send_prepare_timeout",
    "send_queue_timeout",
}
SMALL_WORLD_LOG_REPLAY_LOOKAHEAD_SEC = 30
SMALL_WORLD_BARRIER_COST_BY_LEVEL = {
    1: 600,
    3: 5400,
    4: 9600,
}
_SMALL_WORLD_GOD_ACTION_LOCK = asyncio.Lock()

RE_SMALL_WORLD_DISASTER = re.compile(r"【小世界·天降浩劫】")
RE_SMALL_WORLD_TARGET_TAG = re.compile(rf"道友\s*@({SMALL_WORLD_TARGET_TAG_PATTERN})\s*的小世界遭遇\s*【([^】]+)】")
RE_SMALL_WORLD_FAITH_DAMAGE = re.compile(r"惨重代价\s*[:：]\s*信仰(?:崩塌|动摇)\s*-\s*\d+\s*点")
RE_SMALL_WORLD_RELIEF_DAMAGE = re.compile(r"惨重代价\s*[:：].*(?:人口|稳定|瘟疫|王朝更迭)")
RE_SMALL_WORLD_EXPLICIT_RELIEF = re.compile(r"(?:请|需|建议|应|立即|速速).{0,12}(?:使用\s*)?\.?\s*神迹\s+赈灾")
RE_SMALL_WORLD_INCENSE_LOSS = re.compile(r"惨重代价\s*[:：]\s*库存香火损失\s*(\d+)\s*点")
RE_SMALL_WORLD_PREACH_PANEL = re.compile(r"【神音浩荡】")
RE_SMALL_WORLD_RELIEF_PANEL = re.compile(r"【天降甘霖】")
RE_SMALL_WORLD_FAITH_VALUE = re.compile(r"信仰(?:值大幅)?提升至\s*(\d+)")
RE_SMALL_WORLD_STABILITY_VALUE = re.compile(r"稳定提升至\s*(\d+)")
RE_SMALL_WORLD_RELIEF_POPULATION = re.compile(r"人口恢复了\s*(\d+)\s*人")
RE_SMALL_WORLD_GOD_COOLDOWN = re.compile(r"凡间方才承受神谕，需再等待\s*([^\n。)）]+)")
RE_SMALL_WORLD_GOD_RESOURCE_NEED = re.compile(r"需要\s*\d+\s*([^\s。！？!，,、]+)")

RE_SMALL_WORLD_PANEL = re.compile(r"【(?P<owner>[^】]+)的小世界】")
RE_TEMPLE = re.compile(r"神庙\s*[:：]\s*Lv\.(\d+)(?:【([^】]+)】)?")
RE_POPULATION = re.compile(r"人口\s*[:：]\s*(\d+)\s*人")
RE_CAPACITY = re.compile(r"承载上限\s*[:：]\s*(\d+)\s*人")
RE_PANEL_FAITH = re.compile(r"信仰\s*[:：]\s*(\d+)\s*/\s*(\d+)")
RE_STABILITY = re.compile(r"稳定\s*[:：]\s*(\d+)\s*/\s*(\d+)")
RE_PENDING_INCENSE = re.compile(r"待收香火\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)")
RE_INCENSE_STOCK = re.compile(r"香火库存\s*[:：]\s*(\d+)")
RE_INCENSE_OUTPUT = re.compile(r"预计产出\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*香火/小时")
RE_BARRIER_STATUS = re.compile(r"护界禁制\s*[:：]\s*([^\n]+)")
RE_SPIRITUAL_STRENGTH = re.compile(r"神识强度\s*[:：]\s*(\d+)")
RE_PRAYER = re.compile(r"凡人祈愿\s*[：:]\s*([^\n]+)")
RE_PRAYER_WAIT = re.compile(r"下一次(?:凡人)?祈愿感应需等待\s*[：:]?\s*([^\n。)）]+)")
RE_PRAYER_DATA_ERROR = re.compile(r"祈愿数据异常")
RE_NEXT_TEMPLE_COST = re.compile(r"下一阶【([^】]+)】消耗\s*[:：]\s*([^\n]+)")
RE_MANIFEST_COST = re.compile(r"显灵消耗\s*[:：]\s*([^\n]+)")
RE_MANIFEST_DELTA = re.compile(r"信仰\s*([+-]\d+).*?稳定\s*([+-]\d+).*?人口\s*([+-]\d+)", re.S)
RE_HARVEST_STOCK = re.compile(r"当前香火库存\s*[:：]\s*(\d+)")
RE_REFINE_BURNED = re.compile(r"燃烧了\s*(\d+)\s*点香火")
RE_BARRIER_BURNED = re.compile(r"燃烧\s*(\d+)\s*香火")
RE_STOCK_SHORTAGE = re.compile(r"香火库存不足\s*[(（]\s*拥有\s*[:：]\s*(\d+)\s*[)）]")
RE_RESOURCE_NAME = re.compile(r"【([^】]+)】不足")

_SMALL_WORLD_SCHEDULER_LOCK = asyncio.Lock()


def _normalize_tag(text):
    return str(text or "").strip().lstrip("@").lower()


def _truncate(text, limit=80):
    raw = str(text or "").strip().replace("\n", " ")
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)].rstrip() + "…"


def _phase():
    return str(state.get("small_world_phase") or "idle")


def _set_phase(value):
    state["small_world_phase"] = str(value or "idle")


def _chain_enabled():
    return any(
        bool(state.get(key))
        for key in (
            "small_world_manifest_enabled",
            "small_world_harvest_enabled",
            "small_world_refine_enabled",
            "small_world_refresh_enabled",
        )
    )


def _clear_preach_pending():
    state["small_world_preach_reply_to_msg_id"] = 0
    state["small_world_preach_due_at"] = 0
    if _phase() == "preach_pending":
        _set_phase("idle")


def _clear_pending_god_action():
    state["small_world_pending_god_action"] = ""
    state["small_world_pending_god_reason"] = ""
    state["small_world_pending_god_priority"] = 0
    state["small_world_pending_god_at"] = 0


def _clear_maintenance_god_action():
    if state.get("small_world_pending_god_action") and _pending_god_priority() < SMALL_WORLD_GOD_PRIORITY_DISASTER:
        _clear_pending_god_action()


def _clear_god_pending_tasks():
    clear_pending_tasks_by_commands(SMALL_WORLD_GOD_COMMANDS, send_as_id=get_current_identity_id())


def _command_god_action(command):
    return "relief" if command == CMD_SMALL_WORLD_RELIEF else "preach"


def _god_action_name(action):
    return "赈灾" if action == "relief" else "布道"


def _recent_god_send_guard_until(command, now):
    action = _command_god_action(command)
    last_action = str(state.get("small_world_last_god_action") or "")
    last_sent_at = float(state.get("small_world_last_god_sent_at", 0) or 0)
    if last_action != action or last_sent_at <= 0:
        return 0
    guard_until = last_sent_at + SMALL_WORLD_GOD_RESEND_GUARD_SEC
    if guard_until <= float(now or time.time()):
        return 0
    return guard_until


def _note_small_world_god_remote_block(command, now, block_until, reason, kind):
    _note_small_world_remote_block(
        "small_world_preach",
        command,
        now,
        block_until,
        reason,
        kind,
    )


def _note_small_world_remote_block(action_key, command, now, block_until, reason, kind):
    note_action_guard_remote_block(
        action_key,
        send_as_id=get_current_identity_id(),
        block_until=block_until,
        reason=reason,
        kind=kind,
        now=now,
        command=command,
    )


def _action_guard_session(action_key):
    sessions = state.get("action_guard_sessions")
    if not isinstance(sessions, dict):
        return {}
    session = sessions.get(str(action_key or ""))
    return session if isinstance(session, dict) else {}


def _action_guard_session_has_send_evidence(session):
    if not isinstance(session, dict):
        return False
    return (
        int(session.get("attempt", 0) or 0) > 0
        or float(session.get("first_sent_at", 0) or 0) > 0
        or float(session.get("last_sent_at", 0) or 0) > 0
        or int(session.get("last_msg_id", 0) or 0) > 0
    )


def _is_local_unsent_action_guard_block(action_key):
    session = _action_guard_session(action_key)
    if not session or _action_guard_session_has_send_evidence(session):
        return False
    kind = str(session.get("remote_block_kind") or "").strip()
    reason = str(session.get("remote_block_reason") or "").strip()
    if kind in {"send_unknown", "pending_reply", "recent_send"}:
        return False
    if kind in SMALL_WORLD_LOCAL_UNSENT_BLOCK_KINDS or kind.startswith("flood_wait"):
        return True
    return "未发送" in reason


def _clear_local_unsent_action_guard_block(action_key, now):
    if not _is_local_unsent_action_guard_block(action_key):
        return False
    clear_action_guard_remote_block(
        action_key,
        send_as_id=get_current_identity_id(),
        reason="local_unsent_not_remote",
        now=now,
    )
    close_action_guard_action(
        action_key,
        send_as_id=get_current_identity_id(),
        reason="local_unsent_not_remote",
        now=now,
    )
    return True


def _reconcile_god_action_guard_cooldown(now):
    session = _action_guard_session("small_world_preach")
    if not session:
        return False
    if str(session.get("remote_block_kind") or "").strip() != "success":
        return False
    if "神迹成功" not in str(session.get("remote_block_reason") or ""):
        return False
    guard_until = float(session.get("remote_block_until", 0) or 0)
    cooldown_until = _god_cooldown_until()
    if cooldown_until <= 0 or guard_until <= cooldown_until + 1:
        return False
    clear_action_guard_remote_block(
        "small_world_preach",
        send_as_id=get_current_identity_id(),
        reason="small_world_cooldown_reconciled",
        now=now,
    )
    if cooldown_until > float(now or time.time()):
        _note_small_world_god_remote_block(
            CMD_SMALL_WORLD_PREACH,
            now,
            cooldown_until,
            "神迹成功后的游戏冷却",
            "success",
        )
    return True


def _defer_for_action_guard_block(action_key, command, now, label):
    if action_key == "small_world_preach":
        _reconcile_god_action_guard_cooldown(now)
    if _clear_local_unsent_action_guard_block(action_key, now):
        return False
    blocked_until, guard_reason = get_action_guard_blocked_until(
        command,
        send_as_id=get_current_identity_id(),
        now=now,
    )
    if blocked_until <= float(now or time.time()):
        return False
    _clear_chain_pending()
    state["next_small_world_time"] = float(blocked_until)
    state["small_world_last_error"] = f"{label}延后至安全窗后复查: {guard_reason or '安全锁短窗'}"
    save_state()
    return True


def _close_small_world_action_guard(action_key, now):
    close_action_guard_action(
        action_key,
        send_as_id=get_current_identity_id(),
        reason="small_world_reply_handled",
        now=now,
    )


def _clear_chain_pending():
    state["small_world_query_msg_id"] = 0
    state["small_world_manifest_msg_id"] = 0
    state["small_world_manifest_cost_text"] = ""
    state["small_world_harvest_msg_id"] = 0
    state["small_world_refine_msg_id"] = 0
    if _phase() in SMALL_WORLD_CHAIN_PENDING or _phase() in {"harvest_sent", "harvest_before_manifest_sent", "refine_sent"}:
        _set_phase("idle")


def _clear_barrier_pending():
    state["small_world_barrier_msg_id"] = 0
    state["small_world_barrier_due_at"] = 0


def _clear_all_runtime_pending():
    _clear_preach_pending()
    _clear_pending_god_action()
    _clear_chain_pending()
    _clear_barrier_pending()
    state["small_world_refresh_count"] = 0


def _schedule_after(now, min_sec, max_sec):
    state["next_small_world_time"] = float(now + random.uniform(float(min_sec), float(max_sec)))
    return state["next_small_world_time"]


def _schedule_next_cycle(now):
    return _schedule_after(now, SMALL_WORLD_CYCLE_CD_SEC + SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_CYCLE_CD_SEC + SMALL_WORLD_JITTER_MAX_SEC)


def _schedule_short_retry(now):
    return _schedule_after(now, 10 * 60, 30 * 60)


def _small_world_definitely_unsent_block(command):
    block = classify_game_send_block(get_current_identity_id(), command)
    code = str((block or {}).get("code") or "")
    if str((block or {}).get("status") or "") == "unsent":
        return True, code, str((block or {}).get("reason") or "")
    return False, code, str((block or {}).get("reason") or "")


def _small_world_block_label(code, reason):
    code = str(code or "runtime_block")
    reason = str(reason or "").strip()
    return f"{code}: {reason}" if reason else code


def _schedule_theft_calibration(now, loss_amount):
    _clear_chain_pending()
    stock = max(0, int(state.get("small_world_incense_stock", 0) or 0) - max(0, int(loss_amount or 0)))
    state["small_world_incense_stock"] = stock
    _set_phase("calibration_wait")
    state["small_world_refresh_count"] = 0
    due_at = _schedule_after(now, SMALL_WORLD_THEFT_CALIBRATION_MIN_SEC, SMALL_WORLD_THEFT_CALIBRATION_MAX_SEC)
    state["small_world_last_error"] = f"库存香火失窃 {int(loss_amount or 0)} 点，等待面板校准"
    return due_at


def _schedule_initial_check(now):
    return _schedule_after(now, SMALL_WORLD_INITIAL_CHECK_MIN_SEC, SMALL_WORLD_INITIAL_CHECK_MAX_SEC)


def _schedule_tool_step(now):
    return _schedule_after(now, SMALL_WORLD_TOOL_STEP_MIN_SEC, SMALL_WORLD_TOOL_STEP_MAX_SEC)


def _schedule_panel_wait(now, wait_sec):
    wait_sec = max(0, int(wait_sec or 0))
    state["next_small_world_time"] = float(now + wait_sec + random.uniform(SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC))
    state["small_world_refresh_count"] = 0
    return state["next_small_world_time"]


def _schedule_god_followup(now):
    cooldown_until = float(now + SMALL_WORLD_GOD_FOLLOWUP_SEC)
    state["small_world_god_cooldown_until"] = cooldown_until
    if state.get("small_world_pending_god_action"):
        return _schedule_pending_god_action(now)
    state["next_small_world_time"] = float(
        now + SMALL_WORLD_GOD_FOLLOWUP_SEC + random.uniform(SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC)
    )
    state["small_world_refresh_count"] = 0
    return state["next_small_world_time"]


def _mark_disaster_wave(now):
    state["small_world_last_disaster_wave_at"] = float(now or time.time())


def _next_disaster_wave_at(now):
    last_wave = float(state.get("small_world_last_disaster_wave_at", 0) or 0)
    if last_wave <= 0:
        return 0
    next_wave = last_wave + SMALL_WORLD_DISASTER_WAVE_INTERVAL_SEC
    now = float(now or time.time())
    while next_wave + SMALL_WORLD_DISASTER_GUARD_AFTER_SEC < now:
        next_wave += SMALL_WORLD_DISASTER_WAVE_INTERVAL_SEC
    return next_wave


def _disaster_guard_end_at(now):
    next_wave = _next_disaster_wave_at(now)
    if next_wave <= 0:
        return 0
    now = float(now or time.time())
    if next_wave - SMALL_WORLD_DISASTER_GUARD_BEFORE_SEC <= now <= next_wave + SMALL_WORLD_DISASTER_GUARD_AFTER_SEC:
        return float(next_wave + SMALL_WORLD_DISASTER_GUARD_AFTER_SEC)
    return 0


def _coerce_int_state(key, default=0, *, min_value=None, max_value=None):
    try:
        value = int(float(state.get(key, default) or default))
    except (TypeError, ValueError):
        value = int(default or 0)
    if min_value is not None:
        value = max(int(min_value), value)
    if max_value is not None:
        value = min(int(max_value), value)
    return value


def _coerce_float_state(key, default=0.0, *, min_value=None, max_value=None):
    try:
        value = float(state.get(key, default) or default)
    except (TypeError, ValueError):
        value = float(default or 0)
    if min_value is not None:
        value = max(float(min_value), value)
    if max_value is not None:
        value = min(float(max_value), value)
    return value


def _barrier_guard_before_sec():
    minutes = _coerce_int_state("small_world_barrier_guard_before_min", 30, min_value=5, max_value=180)
    return minutes * 60


def _barrier_min_stock():
    return _coerce_int_state("small_world_barrier_min_stock", 130000, min_value=0, max_value=1000000)


def _barrier_min_interval_sec():
    hours = _coerce_float_state("small_world_barrier_min_interval_hours", 18, min_value=0, max_value=72)
    return hours * 3600


def _barrier_guard_window(now):
    next_wave = _next_disaster_wave_at(now)
    if next_wave <= 0:
        return 0.0, 0.0
    now = float(now or time.time())
    if next_wave - _barrier_guard_before_sec() <= now <= next_wave + SMALL_WORLD_DISASTER_GUARD_AFTER_SEC:
        return float(next_wave), float(next_wave + SMALL_WORLD_DISASTER_GUARD_AFTER_SEC)
    return float(next_wave), 0.0


def _barrier_status_active(status):
    text = str(status or "").strip()
    if not text:
        return False
    inactive_markers = ("未开启", "未布", "无", "失效", "已散", "0秒")
    return not any(marker in text for marker in inactive_markers)


def _barrier_cost_for_panel(panel):
    level = _panel_int(panel, "temple_level")
    return int(SMALL_WORLD_BARRIER_COST_BY_LEVEL.get(level, 0) or 0)


def _snapshot_for_barrier(now):
    snapshot = state.get("small_world_panel_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        return {}, "missing"
    updated_at = float(snapshot.get("updated_at", 0) or state.get("small_world_last_panel_at", 0) or 0)
    if updated_at <= 0 or float(now or time.time()) - updated_at > SMALL_WORLD_BARRIER_PANEL_MAX_AGE_SEC:
        return snapshot, "stale"
    return snapshot, ""


def _barrier_decision(now, panel=None, *, fresh_panel=False):
    if not state.get("small_world_barrier_enabled", True):
        return "skip", "护界禁制未开启"

    next_wave_at, guard_end_at = _barrier_guard_window(now)
    if guard_end_at <= 0:
        return "skip", ""

    if panel is None:
        panel, panel_reason = _snapshot_for_barrier(now)
        if panel_reason:
            return "query", f"护界禁制临灾校准: {panel_reason}"
    else:
        panel_reason = ""

    if not isinstance(panel, dict) or not panel:
        return "query", "护界禁制临灾校准: missing"

    if not fresh_panel and panel_reason:
        return "query", f"护界禁制临灾校准: {panel_reason}"

    if _barrier_status_active(panel.get("barrier_status")):
        return "skip", "护界禁制已开启"

    stock = _panel_int(panel, "stock")
    min_stock = _barrier_min_stock()
    if stock < min_stock:
        return "skip", f"香火库存 {stock} 未达护界阈值 {min_stock}"

    cost = _barrier_cost_for_panel(panel)
    if cost <= 0:
        level = _panel_int(panel, "temple_level")
        return "skip", f"神庙 Lv.{level or '未知'} 护界成本未校准"
    if stock < cost:
        return "skip", f"香火库存 {stock} 不足护界成本 {cost}"

    last_sent_at = float(state.get("small_world_last_barrier_sent_at", 0) or 0)
    min_interval = _barrier_min_interval_sec()
    if min_interval > 0 and last_sent_at > 0 and float(now or time.time()) - last_sent_at < min_interval:
        return "skip", "护界禁制最小间隔保护中"

    return "send", f"下一灾害波 {fmt_abs_ts(next_wave_at)}，库存 {stock}，成本约 {cost}"


async def _maybe_send_barrier_or_query(now, panel=None, *, fresh_panel=False):
    action, reason = _barrier_decision(now, panel, fresh_panel=fresh_panel)
    if action == "send":
        _clear_chain_pending()
        return await _send_barrier(now, reason)
    if action == "query":
        return await _send_query(now, reason)
    return False


def _god_cooldown_until():
    return float(state.get("small_world_god_cooldown_until", 0) or 0)


def _pending_god_priority():
    try:
        return int(state.get("small_world_pending_god_priority", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _queue_god_action(action, reason, priority, now):
    action = "relief" if action == "relief" else "preach"
    priority = int(priority or 0)
    current_action = str(state.get("small_world_pending_god_action") or "")
    if current_action and _pending_god_priority() > priority:
        return False
    state["small_world_pending_god_action"] = action
    state["small_world_pending_god_reason"] = str(reason or "").strip()
    state["small_world_pending_god_priority"] = priority
    state["small_world_pending_god_at"] = float(now or time.time())
    return True


def _schedule_pending_god_action(now):
    action = str(state.get("small_world_pending_god_action") or "")
    if not action:
        return 0
    priority = _pending_god_priority()
    due_at = max(float(now or time.time()), _god_cooldown_until())
    if priority < SMALL_WORLD_GOD_PRIORITY_DISASTER:
        guard_end = _disaster_guard_end_at(due_at) or _disaster_guard_end_at(now)
        if guard_end > 0:
            due_at = max(due_at, guard_end)
            state["small_world_last_error"] = "日常神迹维护已让位下一波灾害"
    if due_at > float(now or time.time()):
        due_at += random.uniform(SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC)
    state["next_small_world_time"] = float(due_at)
    return state["next_small_world_time"]


def _queue_maintenance_from_snapshot(now):
    snapshot = state.get("small_world_panel_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        return False
    return _queue_maintenance_god_action(snapshot, now)


async def _try_send_pending_god_action(now):
    action = str(state.get("small_world_pending_god_action") or "")
    if action not in {"preach", "relief"}:
        return False

    preach_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    preach_deadline = _get_preach_deadline()
    if preach_msg_id > 0 and preach_deadline > float(now or time.time()):
        return True

    priority = _pending_god_priority()
    if _god_cooldown_until() > float(now or time.time()):
        _schedule_pending_god_action(now)
        save_state()
        return True

    if priority < SMALL_WORLD_GOD_PRIORITY_DISASTER and _disaster_guard_end_at(now) > 0:
        _schedule_pending_god_action(now)
        save_state()
        return True

    reason = str(state.get("small_world_pending_god_reason") or "").strip()
    if priority >= SMALL_WORLD_GOD_PRIORITY_DISASTER:
        _clear_chain_pending()
    sent = await (_send_small_world_relief(now, reason) if action == "relief" else _send_small_world_preach(now, reason))
    return sent


def _schedule_resource_pause(now, label, raw_text, *, pause_sec=SMALL_WORLD_LONG_PAUSE_SEC):
    due_at = float(now + float(pause_sec) + random.uniform(SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC))
    _clear_chain_pending()
    state["next_small_world_time"] = due_at
    state["small_world_last_error"] = f"{label}资源不足: {_truncate(raw_text)}"
    return due_at


def _find_small_world_identity_id(text):
    matched = RE_SMALL_WORLD_TARGET_TAG.search(text or "")
    if not matched:
        return None

    target_key = _normalize_tag(matched.group(1))
    if not target_key:
        return None

    matched_ids = []
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        normalized_tags = {_normalize_tag(tag) for tag in get_send_as_tags(identity_id) if tag}
        if target_key in normalized_tags:
            matched_ids.append(identity_id)
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


def _reply_to_message_id(reply_to):
    try:
        return int(getattr(reply_to, "id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _is_reply_to_tracked_message(reply_to, state_key):
    expected_msg_id = int(state.get(state_key, 0) or 0)
    return expected_msg_id > 0 and _reply_to_message_id(reply_to) == expected_msg_id


def _is_current_query_reply(reply_to, matched_family):
    if _is_reply_to_tracked_message(reply_to, "small_world_query_msg_id"):
        return True
    return matched_family == "small_world_query" and _phase() == "query_pending"


def _get_preach_deadline():
    deadline = float(state.get("small_world_preach_due_at", 0) or 0)
    if deadline > 0:
        return deadline
    if int(state.get("small_world_preach_reply_to_msg_id", 0) or 0) > 0 and _phase() in {"idle", "preach_pending"}:
        return float(state.get("next_small_world_time", 0) or 0)
    return 0.0


def _has_active_small_world_pending(now):
    deadline = _get_preach_deadline()
    return _phase() == "preach_pending" and deadline > now


def _parse_wait_from_text(raw_text):
    raw_text = str(raw_text or "")
    matched = RE_PRAYER_WAIT.search(raw_text)
    if not matched:
        matched = RE_SMALL_WORLD_GOD_COOLDOWN.search(raw_text)
    if not matched:
        return 0, ""
    wait_text = matched.group(1).strip()
    if not has_wait_time(wait_text):
        return 0, wait_text
    return parse_wait_time(wait_text), wait_text


def _parse_signed_int(raw_value, default=0):
    try:
        return int(str(raw_value or "").replace("+", ""))
    except (TypeError, ValueError):
        return default


def _int_match(pattern, raw_text, default=0):
    matched = pattern.search(raw_text)
    if not matched:
        return default
    try:
        return int(matched.group(1))
    except (TypeError, ValueError):
        return default


def _float_match(pattern, raw_text, default=0.0):
    matched = pattern.search(raw_text)
    if not matched:
        return default
    try:
        return float(matched.group(1))
    except (TypeError, ValueError):
        return default


def _apply_small_world_panel_snapshot(now, panel):
    state["small_world_last_panel_at"] = float(now)
    state["small_world_faith_value"] = int(panel.get("faith", 0) or 0)
    state["small_world_pending_incense"] = float(panel.get("pending_incense", 0) or 0)
    state["small_world_incense_stock"] = int(panel.get("stock", 0) or 0)
    snapshot = dict(panel)
    snapshot.pop("realm_blocked", None)
    snapshot["updated_at"] = float(now)
    state["small_world_panel_snapshot"] = snapshot


def _update_snapshot_field(key, value):
    snapshot = state.get("small_world_panel_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    snapshot[key] = value
    state["small_world_panel_snapshot"] = snapshot


def _apply_god_result(raw_text, now):
    changed = False
    faith_matched = RE_SMALL_WORLD_FAITH_VALUE.search(raw_text)
    if faith_matched:
        faith_value = int(faith_matched.group(1))
        state["small_world_faith_value"] = faith_value
        _update_snapshot_field("faith", faith_value)
        changed = True

    stability_matched = RE_SMALL_WORLD_STABILITY_VALUE.search(raw_text)
    if stability_matched:
        _update_snapshot_field("stability", int(stability_matched.group(1)))
        changed = True

    population_matched = RE_SMALL_WORLD_RELIEF_POPULATION.search(raw_text)
    if population_matched:
        snapshot = state.get("small_world_panel_snapshot")
        current_population = int((snapshot or {}).get("population", 0) or 0) if isinstance(snapshot, dict) else 0
        recovered = int(population_matched.group(1))
        if current_population > 0:
            capacity = int((snapshot or {}).get("capacity", 0) or 0) if isinstance(snapshot, dict) else 0
            population = current_population + recovered
            if capacity > 0:
                population = min(capacity, population)
            _update_snapshot_field("population", population)
        changed = True

    if changed:
        _update_snapshot_field("updated_at", float(now))
    return changed


def _apply_manifest_delta(raw_text, now):
    matched = RE_MANIFEST_DELTA.search(raw_text)
    if not matched:
        return False
    snapshot = state.get("small_world_panel_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}

    faith_delta = _parse_signed_int(matched.group(1))
    stability_delta = _parse_signed_int(matched.group(2))
    population_delta = _parse_signed_int(matched.group(3))

    faith = int(snapshot.get("faith", 0) or 0)
    if faith > 0:
        faith_max = int(snapshot.get("faith_max", 100) or 100)
        faith_value = max(0, min(faith_max, faith + faith_delta))
        state["small_world_faith_value"] = faith_value
        snapshot["faith"] = faith_value

    stability = int(snapshot.get("stability", 0) or 0)
    if stability > 0:
        stability_max = int(snapshot.get("stability_max", 100) or 100)
        snapshot["stability"] = max(0, min(stability_max, stability + stability_delta))

    population = int(snapshot.get("population", 0) or 0)
    if population > 0:
        capacity = int(snapshot.get("capacity", 0) or 0)
        population = max(0, population + population_delta)
        if capacity > 0:
            population = min(capacity, population)
        snapshot["population"] = population

    snapshot["updated_at"] = float(now)
    state["small_world_panel_snapshot"] = snapshot
    return True


def _clear_manifest_snapshot_prayer(now):
    snapshot = state.get("small_world_panel_snapshot")
    if not isinstance(snapshot, dict):
        return False
    changed = False
    for key, value in (("has_prayer", False), ("prayer_name", ""), ("manifest_cost", "")):
        if snapshot.get(key) != value:
            snapshot[key] = value
            changed = True
    if changed:
        snapshot["updated_at"] = float(now or time.time())
        state["small_world_panel_snapshot"] = snapshot
    return changed


def _cache_manifest_snapshot_prayer(now, raw_text):
    prayer_matched = RE_PRAYER.search(str(raw_text or ""))
    cost_matched = RE_MANIFEST_COST.search(str(raw_text or ""))
    if not prayer_matched:
        return False
    snapshot = state.get("small_world_panel_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    snapshot["has_prayer"] = True
    snapshot["prayer_name"] = prayer_matched.group(1).strip()
    snapshot["manifest_cost"] = cost_matched.group(1).strip() if cost_matched else ""
    snapshot["has_wait"] = False
    snapshot["wait_sec"] = 0
    snapshot["wait_text"] = ""
    snapshot["updated_at"] = float(now or time.time())
    state["small_world_panel_snapshot"] = snapshot
    return True


def _has_ready_manifest_snapshot(now):
    if not state.get("small_world_manifest_enabled"):
        return False
    snapshot = state.get("small_world_panel_snapshot")
    if not isinstance(snapshot, dict):
        return False
    if not snapshot.get("has_prayer") or snapshot.get("has_wait"):
        return False
    try:
        updated_at = float(snapshot.get("updated_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        updated_at = 0
    if updated_at <= 0:
        return False
    return float(now or time.time()) - updated_at <= SMALL_WORLD_BARRIER_PANEL_MAX_AGE_SEC


def _parse_small_world_panel(text):
    raw_text = str(text or "")
    if "境界不足" in raw_text and "紫府小世界" in raw_text:
        return {"realm_blocked": True}
    owner_matched = RE_SMALL_WORLD_PANEL.search(raw_text)
    if not owner_matched:
        return None

    temple_level = 0
    temple_name = ""
    matched = RE_TEMPLE.search(raw_text)
    if matched:
        temple_level = int(matched.group(1))
        temple_name = (matched.group(2) or "").strip()

    faith = 0
    faith_max = 0
    matched = RE_PANEL_FAITH.search(raw_text)
    if matched:
        faith = int(matched.group(1))
        faith_max = int(matched.group(2))

    stability = 0
    stability_max = 0
    matched = RE_STABILITY.search(raw_text)
    if matched:
        stability = int(matched.group(1))
        stability_max = int(matched.group(2))

    wait_sec = 0
    wait_text = ""
    wait_sec, wait_text = _parse_wait_from_text(raw_text)

    prayer_matched = RE_PRAYER.search(raw_text)
    prayer_data_error = bool(RE_PRAYER_DATA_ERROR.search(raw_text))
    cost_matched = RE_MANIFEST_COST.search(raw_text)
    next_temple_matched = RE_NEXT_TEMPLE_COST.search(raw_text)
    barrier_matched = RE_BARRIER_STATUS.search(raw_text)
    return {
        "realm_blocked": False,
        "owner": owner_matched.group("owner").strip(),
        "temple_level": temple_level,
        "temple_name": temple_name,
        "population": _int_match(RE_POPULATION, raw_text),
        "capacity": _int_match(RE_CAPACITY, raw_text),
        "faith": faith,
        "faith_max": faith_max,
        "stability": stability,
        "stability_max": stability_max,
        "pending_incense": _float_match(RE_PENDING_INCENSE, raw_text),
        "stock": _int_match(RE_INCENSE_STOCK, raw_text),
        "hourly_output": _float_match(RE_INCENSE_OUTPUT, raw_text),
        "barrier_status": barrier_matched.group(1).strip() if barrier_matched else "",
        "spiritual_strength": _int_match(RE_SPIRITUAL_STRENGTH, raw_text),
        "has_prayer": bool(prayer_matched),
        "prayer_data_error": prayer_data_error,
        "prayer_name": prayer_matched.group(1).strip() if prayer_matched else "",
        "manifest_cost": cost_matched.group(1).strip() if cost_matched else "",
        "wait_sec": wait_sec,
        "wait_text": wait_text.strip(),
        "has_wait": wait_sec > 0,
        "next_temple_name": next_temple_matched.group(1).strip() if next_temple_matched else "",
        "next_temple_cost": next_temple_matched.group(2).strip() if next_temple_matched else "",
    }


def _calc_refine_amount(stock):
    try:
        stock = int(stock or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, (stock // 10) * 10)


def _small_world_population_deficit(panel):
    try:
        population = int(panel.get("population", 0) or 0)
        capacity = int(panel.get("capacity", 0) or 0)
    except (TypeError, ValueError):
        return 0, 0, 1.0
    if population <= 0 or capacity <= 0:
        return 0, population, 1.0
    deficit = max(0, capacity - population)
    ratio = max(0.0, min(1.0, population / capacity))
    return deficit, population, ratio


def _panel_int(panel, key, default=0):
    try:
        return int(panel.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _is_panel_value_below_max(panel, value_key, max_key, *, default_max=SMALL_WORLD_DEFAULT_STATUS_MAX):
    value = _panel_int(panel, value_key)
    max_value = _panel_int(panel, max_key, default_max)
    return value > 0 and max_value > 0 and value < max_value


def _should_relief(panel):
    deficit, _population, ratio = _small_world_population_deficit(panel)
    if deficit > 0 and ratio <= SMALL_WORLD_RELIEF_POPULATION_RATIO_TRIGGER:
        return True

    stability = _panel_int(panel, "stability")
    stability_max = _panel_int(panel, "stability_max", SMALL_WORLD_DEFAULT_STATUS_MAX)
    if stability <= 0 or stability_max <= 0:
        return False
    return stability / stability_max <= SMALL_WORLD_RELIEF_STABILITY_RATIO_TRIGGER


def _relief_reason(panel):
    deficit, population, _ratio = _small_world_population_deficit(panel)
    if deficit > 0:
        return f"人口 {population} 缺口 {deficit}，优先赈灾"
    stability = _panel_int(panel, "stability")
    stability_max = _panel_int(panel, "stability_max", SMALL_WORLD_DEFAULT_STATUS_MAX)
    if stability > 0 and stability_max > 0:
        return f"稳定 {stability}/{stability_max}，赈灾维护"
    return "小世界状态未满，赈灾维护"


def _should_preach(panel):
    return _is_panel_value_below_max(panel, "faith", "faith_max")


def _preach_reason(panel):
    faith = _panel_int(panel, "faith")
    faith_max = _panel_int(panel, "faith_max", SMALL_WORLD_DEFAULT_STATUS_MAX)
    if faith > 0 and faith_max > 0:
        return f"信仰 {faith}/{faith_max}，布道维护"
    return "信仰未满，布道维护"


def _queue_maintenance_god_action(panel, now):
    if _should_preach(panel):
        return _queue_god_action("preach", _preach_reason(panel), SMALL_WORLD_GOD_PRIORITY_MAINTENANCE, now)
    if _should_relief(panel):
        return _queue_god_action("relief", _relief_reason(panel), SMALL_WORLD_GOD_PRIORITY_MAINTENANCE, now)
    return False


def _is_resource_shortage_text(text):
    raw_text = str(text or "")
    return (
        "显灵所需" in raw_text and "不足" in raw_text
    ) or (
        "修为不足" in raw_text and "无法调动天地灵气" in raw_text
    ) or (
        "灵石不足" in raw_text
        or ("清灵丹" in raw_text and "不足" in raw_text)
        or "资源不足" in raw_text
        or "材料不足" in raw_text
    )


def _resource_label_from_text(text):
    raw_text = str(text or "")
    matched = RE_RESOURCE_NAME.search(raw_text)
    if matched:
        return matched.group(1).strip()
    if "香火" in raw_text and "不足" in raw_text:
        return "香火"
    if "修为不足" in raw_text:
        return "修为"
    if "灵石不足" in raw_text:
        return "灵石"
    if "清灵丹" in raw_text and "不足" in raw_text:
        return "清灵丹"
    return "资源"


def _is_god_resource_shortage_text(text):
    raw_text = str(text or "")
    return (
        "国库空虚" in raw_text
        or _is_resource_shortage_text(raw_text)
        or ("神迹" in raw_text and "不足" in raw_text)
        or ("布道" in raw_text and "不足" in raw_text)
        or ("赈灾" in raw_text and "不足" in raw_text)
    )


def _god_resource_label_from_text(text):
    raw_text = str(text or "")
    if "国库空虚" in raw_text:
        return "灵石"
    matched = RE_SMALL_WORLD_GOD_RESOURCE_NEED.search(raw_text)
    if matched:
        return matched.group(1).strip()
    return _resource_label_from_text(raw_text)


def _god_action_name_from_context(reply_to, matched_family):
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    if matched_family == "small_world_relief" or CMD_SMALL_WORLD_RELIEF in orig_cmd:
        return "赈灾"
    if matched_family == "small_world_preach" or CMD_SMALL_WORLD_PREACH in orig_cmd:
        return "布道"
    action = str(state.get("small_world_pending_god_action") or "")
    return "赈灾" if action == "relief" else "布道"


def _is_current_god_reply(reply_to, matched_family):
    if matched_family in {"small_world_preach", "small_world_relief"}:
        return True
    if _is_reply_to_tracked_message(reply_to, "small_world_preach_reply_to_msg_id"):
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    return CMD_SMALL_WORLD_PREACH in orig_cmd or CMD_SMALL_WORLD_RELIEF in orig_cmd


def _is_current_barrier_reply(reply_to, matched_family):
    if matched_family == "small_world_barrier":
        return True
    if _is_reply_to_tracked_message(reply_to, "small_world_barrier_msg_id"):
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    return CMD_SMALL_WORLD_BARRIER in orig_cmd


def _is_small_world_reply_log_entry(entry):
    raw_text = str((entry or {}).get("text") or "").strip()
    if not raw_text:
        return False
    if _parse_small_world_panel(raw_text):
        return True
    return any(
        marker in raw_text
        for marker in (
            "小世界",
            "凡人祈愿",
            "显灵",
            "香火",
            "神识淬炼",
            "护界禁制",
            "愿力金幕",
            "神音浩荡",
            "天降甘霖",
            "祈愿数据异常",
            "天机已散",
            "境界不足",
            "库存不足",
            "资源不足",
        )
    )


async def _recover_small_world_reply_from_log(now, *, msg_id=0, family="", command="", handler=None):
    msg_id = int(msg_id or 0)
    if msg_id <= 0 or handler is None:
        return False
    replies = find_message_log_replies(
        msg_id,
        now,
        lookback_sec=SMALL_WORLD_LOG_REPLAY_LOOKBACK_SEC,
        lookahead_sec=SMALL_WORLD_LOG_REPLAY_LOOKAHEAD_SEC,
        predicate=_is_small_world_reply_log_entry,
    )
    if not replies:
        return False
    reply_to = SimpleNamespace(id=msg_id, raw_text=str(command or ""))
    handled_any = False
    for entry in replies:
        handled = await handler(
            entry.get("text") or "",
            float(entry.get("ts_epoch") or now),
            reply_to,
            matched_family=family,
        )
        handled_any = handled_any or handled
    if handled_any:
        console_log(f"🌏 小世界日志补偿：已采纳超时回包，消息ID={msg_id}", scope="identity", limit=200)
    return handled_any


async def _recover_current_small_world_pending_from_log(now, phase=None):
    phase = str(phase or _phase() or "")
    barrier_msg_id = int(state.get("small_world_barrier_msg_id", 0) or 0)
    if barrier_msg_id > 0:
        if await _recover_small_world_reply_from_log(
            now,
            msg_id=barrier_msg_id,
            family="small_world_barrier",
            command=CMD_SMALL_WORLD_BARRIER,
            handler=handle_small_world_barrier_reply,
        ):
            return True
    preach_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    if preach_msg_id > 0:
        pending_action = str(state.get("small_world_pending_god_action") or "")
        is_relief = pending_action == "relief"
        if await _recover_small_world_reply_from_log(
            now,
            msg_id=preach_msg_id,
            family="small_world_relief" if is_relief else "small_world_preach",
            command=CMD_SMALL_WORLD_RELIEF if is_relief else CMD_SMALL_WORLD_PREACH,
            handler=handle_small_world_preach_reply,
        ):
            return True
    chain_specs = {
        "query_pending": ("small_world_query_msg_id", "small_world_query", CMD_SMALL_WORLD_QUERY, handle_small_world_query_reply),
        "manifest_pending": ("small_world_manifest_msg_id", "small_world_manifest", CMD_SMALL_WORLD_MANIFEST, handle_small_world_manifest_reply),
        "harvest_pending": ("small_world_harvest_msg_id", "small_world_harvest", CMD_SMALL_WORLD_HARVEST, handle_small_world_harvest_reply),
        "harvest_sent": ("small_world_harvest_msg_id", "small_world_harvest", CMD_SMALL_WORLD_HARVEST, handle_small_world_harvest_reply),
        "harvest_before_manifest_sent": ("small_world_harvest_msg_id", "small_world_harvest", CMD_SMALL_WORLD_HARVEST, handle_small_world_harvest_reply),
        "refine_pending": ("small_world_refine_msg_id", "small_world_refine", CMD_SMALL_WORLD_REFINE, handle_small_world_refine_reply),
        "refine_sent": ("small_world_refine_msg_id", "small_world_refine", CMD_SMALL_WORLD_REFINE, handle_small_world_refine_reply),
    }
    spec = chain_specs.get(phase)
    if not spec:
        return False
    state_key, family, command, handler = spec
    msg_id = int(state.get(state_key, 0) or 0)
    return await _recover_small_world_reply_from_log(now, msg_id=msg_id, family=family, command=command, handler=handler)


async def _disable_for_realm(raw_text):
    _clear_all_runtime_pending()
    state["small_world_enabled"] = False
    state["next_small_world_time"] = 0
    state["small_world_last_error"] = "境界不足，已关闭小世界模块"
    clear_pending_tasks_by_commands(
        SMALL_WORLD_CHAIN_COMMANDS | SMALL_WORLD_GOD_COMMANDS | SMALL_WORLD_BARRIER_COMMANDS,
        send_as_id=get_current_identity_id(),
    )
    save_state()
    await send_audit_log("⚠️ 小世界境界不足，已关闭该身份的小世界模块。", scope="identity")
    return True


async def _send_small_world_god_action(now, command, reason):
    async with _SMALL_WORLD_GOD_ACTION_LOCK:
        command = CMD_SMALL_WORLD_RELIEF if command == CMD_SMALL_WORLD_RELIEF else CMD_SMALL_WORLD_PREACH
        action = _command_god_action(command)
        action_name = _god_action_name(action)
        _reconcile_god_action_guard_cooldown(float(now or time.time()))
        prev_last_god_action = str(state.get("small_world_last_god_action") or "")
        prev_last_god_sent_at = float(state.get("small_world_last_god_sent_at", 0) or 0)

        preach_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
        preach_deadline = _get_preach_deadline()
        if preach_msg_id > 0 and preach_deadline > float(now or time.time()):
            state["next_small_world_time"] = preach_deadline
            state["small_world_last_error"] = f"神迹{action_name}等待回执，跳过重复发送"
            save_state()
            return True

        guard_until = _recent_god_send_guard_until(command, now)
        if guard_until > 0:
            state["next_small_world_time"] = guard_until
            state["small_world_last_error"] = f"神迹{action_name}等待回执，跳过重复发送"
            _note_small_world_god_remote_block(command, now, guard_until, "神迹短窗重复发送保护", "recent_send")
            save_state()
            return True

        optimistic_sent_at = float(now or time.time())
        _set_phase("preach_pending")
        state["small_world_preach_reply_to_msg_id"] = 0
        state["small_world_preach_due_at"] = float(optimistic_sent_at + SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC)
        state["next_small_world_time"] = state["small_world_preach_due_at"]
        state["small_world_last_god_action"] = action
        state["small_world_last_god_sent_at"] = optimistic_sent_at
        state["small_world_last_error"] = f"神迹{action_name}已发起，等待回执确认"
        save_state()

        sent_msg = await send_game_command(command, track=True, max_retry=0, source_module="小世界")
        sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time()) if sent_msg else time.time()
        if not sent_msg:
            if _phase() != "preach_pending" or int(state.get("small_world_preach_reply_to_msg_id", 0) or 0) > 0:
                return True
            definitely_unsent, block_code, block_reason = _small_world_definitely_unsent_block(command)
            _clear_preach_pending()
            state["small_world_last_god_action"] = prev_last_god_action
            state["small_world_last_god_sent_at"] = prev_last_god_sent_at
            if state.get("small_world_pending_god_action"):
                if _pending_god_priority() >= SMALL_WORLD_GOD_PRIORITY_DISASTER:
                    _schedule_after(optimistic_sent_at, SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC)
                else:
                    _schedule_short_retry(optimistic_sent_at)
            if definitely_unsent:
                state["small_world_last_error"] = (
                    f"神迹{action_name}未发送，保留待办并短退避重试: "
                    f"{_small_world_block_label(block_code, block_reason)}"
                )
                _clear_local_unsent_action_guard_block("small_world_preach", optimistic_sent_at)
            else:
                state["small_world_last_error"] = f"神迹{action_name}发送结果未知，保留待办并短退避重试"
                _note_small_world_god_remote_block(
                    command,
                    optimistic_sent_at,
                    state.get("next_small_world_time", 0),
                    f"神迹{action_name}发送结果未知，短退避重试",
                    "send_unknown",
                )
            save_state()
            return True

        if _phase() == "preach_pending" and int(state.get("small_world_preach_reply_to_msg_id", 0) or 0) <= 0:
            state["small_world_preach_reply_to_msg_id"] = int(getattr(sent_msg, "id", 0) or 0)
            state["small_world_preach_due_at"] = float(sent_at + SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC)
            state["small_world_last_god_action"] = action
            state["small_world_last_god_sent_at"] = sent_at
            state["next_small_world_time"] = state["small_world_preach_due_at"]
            state["small_world_last_error"] = ""
            _note_small_world_god_remote_block(command, sent_at, state["small_world_preach_due_at"], "等待神迹回执", "pending_reply")
            save_state()
        console_log(f"🌍 小世界{reason}，已发送神迹{action_name}。")
        return True


async def _send_small_world_preach(now, reason):
    return await _send_small_world_god_action(now, CMD_SMALL_WORLD_PREACH, reason)


async def _send_small_world_relief(now, reason):
    return await _send_small_world_god_action(now, CMD_SMALL_WORLD_RELIEF, reason)


async def _send_query(now, reason, *, refresh_attempt=None):
    started_at = float(now or time.time())
    blocked_until, guard_reason = get_action_guard_blocked_until(
        CMD_SMALL_WORLD_QUERY,
        send_as_id=get_current_identity_id(),
        now=started_at,
    )
    if blocked_until > started_at:
        if _clear_local_unsent_action_guard_block("small_world_query", started_at):
            return await _send_query(now, reason, refresh_attempt=refresh_attempt)
        state["small_world_query_msg_id"] = 0
        state["next_small_world_time"] = float(blocked_until)
        state["small_world_last_error"] = f"{reason}延后至安全窗后复查: {guard_reason or '安全锁短窗'}"
        save_state()
        return True

    _set_phase("query_pending")
    state["small_world_query_msg_id"] = 0
    state["next_small_world_time"] = started_at + SMALL_WORLD_PENDING_TIMEOUT_SEC
    if refresh_attempt is not None:
        state["small_world_refresh_count"] = max(0, int(refresh_attempt or 0))
    state["small_world_last_error"] = f"{reason}已发起，等待小世界面板"
    save_state()

    msg = await send_game_command(CMD_SMALL_WORLD_QUERY, track=True, max_retry=0, priority="chain", source_module="小世界")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        if _phase() != "query_pending" or int(state.get("small_world_query_msg_id", 0) or 0) > 0:
            return True
        definitely_unsent, block_code, block_reason = _small_world_definitely_unsent_block(CMD_SMALL_WORLD_QUERY)
        if definitely_unsent:
            _clear_chain_pending()
            _schedule_short_retry(started_at)
            state["small_world_last_error"] = (
                f"{reason}发送 .小世界 未发送，短退避后重试: "
                f"{_small_world_block_label(block_code, block_reason)}"
            )
            _clear_local_unsent_action_guard_block("small_world_query", started_at)
            save_state()
            return True
        state["small_world_last_error"] = f"{reason}发送 .小世界 结果未知，等待小世界面板"
        _note_small_world_remote_block(
            "small_world_query",
            CMD_SMALL_WORLD_QUERY,
            started_at,
            state["next_small_world_time"],
            "小世界查询发送结果未知，等待面板",
            "send_unknown",
        )
        save_state()
        return True

    if _phase() == "query_pending" and int(state.get("small_world_query_msg_id", 0) or 0) <= 0:
        state["small_world_query_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["next_small_world_time"] = sent_at + SMALL_WORLD_PENDING_TIMEOUT_SEC
        state["small_world_last_error"] = ""
        save_state()
    console_log(f"🌍 小世界查询已发送：{reason}。")
    return True


async def _send_manifest(now):
    started_at = float(now or time.time())
    if _defer_for_action_guard_block("small_world_manifest", CMD_SMALL_WORLD_MANIFEST, started_at, "小世界显灵"):
        return True
    _set_phase("manifest_pending")
    state["small_world_manifest_msg_id"] = 0
    state["next_small_world_time"] = started_at + SMALL_WORLD_MANIFEST_PENDING_TIMEOUT_SEC
    state["small_world_last_error"] = "显灵已发起，等待回执"
    save_state()

    msg = await send_game_command(CMD_SMALL_WORLD_MANIFEST, track=False, max_retry=0, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        if _phase() != "manifest_pending" or int(state.get("small_world_manifest_msg_id", 0) or 0) > 0:
            return True
        definitely_unsent, block_code, block_reason = _small_world_definitely_unsent_block(CMD_SMALL_WORLD_MANIFEST)
        if definitely_unsent:
            _clear_chain_pending()
            _schedule_short_retry(started_at)
            state["small_world_last_error"] = (
                f"发送 .显灵 未发送，短退避后重试: "
                f"{_small_world_block_label(block_code, block_reason)}"
            )
            _clear_local_unsent_action_guard_block("small_world_manifest", started_at)
            save_state()
            return True
        state["small_world_last_error"] = "发送 .显灵 结果未知，等待回执"
        _note_small_world_remote_block(
            "small_world_manifest",
            CMD_SMALL_WORLD_MANIFEST,
            started_at,
            state["next_small_world_time"],
            "小世界显灵发送结果未知，等待回执",
            "send_unknown",
        )
        save_state()
        return True

    if _phase() == "manifest_pending" and int(state.get("small_world_manifest_msg_id", 0) or 0) <= 0:
        state["small_world_manifest_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["next_small_world_time"] = sent_at + SMALL_WORLD_MANIFEST_PENDING_TIMEOUT_SEC
        state["small_world_last_error"] = ""
        save_state()
    return True


async def _send_harvest(now):
    started_at = float(now or time.time())
    if _defer_for_action_guard_block("small_world_harvest", CMD_SMALL_WORLD_HARVEST, started_at, "小世界收割香火"):
        return True
    _set_phase("harvest_sent")
    state["small_world_harvest_msg_id"] = 0
    _schedule_tool_step(started_at)
    state["small_world_last_error"] = "收割香火已发起，等待回执确认"
    save_state()

    msg = await send_game_command(CMD_SMALL_WORLD_HARVEST, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        if _phase() != "harvest_sent" or int(state.get("small_world_harvest_msg_id", 0) or 0) > 0:
            return True
        definitely_unsent, block_code, block_reason = _small_world_definitely_unsent_block(CMD_SMALL_WORLD_HARVEST)
        if definitely_unsent:
            _clear_chain_pending()
            _schedule_short_retry(started_at)
            state["small_world_last_error"] = (
                f"发送 .收割香火 未发送，短退避后重试: "
                f"{_small_world_block_label(block_code, block_reason)}"
            )
            _clear_local_unsent_action_guard_block("small_world_harvest", started_at)
            save_state()
            return True
        state["small_world_last_error"] = "发送 .收割香火 结果未知，等待回执或复查"
        _note_small_world_remote_block(
            "small_world_harvest",
            CMD_SMALL_WORLD_HARVEST,
            started_at,
            state["next_small_world_time"],
            "小世界收割香火发送结果未知，等待回执",
            "send_unknown",
        )
        save_state()
        return True

    if _phase() == "harvest_sent" and int(state.get("small_world_harvest_msg_id", 0) or 0) <= 0:
        state["small_world_harvest_msg_id"] = int(getattr(msg, "id", 0) or 0)
        _schedule_tool_step(sent_at)
        state["small_world_last_error"] = "收割香火已发送，等待回执确认"
        save_state()
    return True


async def _send_harvest_before_manifest(now):
    if not await _send_harvest(now):
        return False
    if _phase() != "harvest_sent" and int(state.get("small_world_harvest_msg_id", 0) or 0) <= 0:
        return True
    _set_phase("harvest_before_manifest_sent")
    state["small_world_last_error"] = "显灵前收割香火已发送，等待回执确认"
    save_state()
    return True


async def _send_refine(now, amount):
    amount = _calc_refine_amount(amount)
    if amount < 10:
        return await _send_query(now, "淬炼数量不足，复查小世界")

    command = f"{CMD_SMALL_WORLD_REFINE} {amount}"
    started_at = float(now or time.time())
    if _defer_for_action_guard_block("small_world_refine", command, started_at, "小世界神识淬炼"):
        return True
    _set_phase("refine_sent")
    state["small_world_refine_msg_id"] = 0
    _schedule_tool_step(started_at)
    state["small_world_last_error"] = "神识淬炼已发起，等待回执确认"
    save_state()

    msg = await send_game_command(command, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        if _phase() != "refine_sent" or int(state.get("small_world_refine_msg_id", 0) or 0) > 0:
            return True
        definitely_unsent, block_code, block_reason = _small_world_definitely_unsent_block(command)
        if definitely_unsent:
            _clear_chain_pending()
            _schedule_short_retry(started_at)
            state["small_world_last_error"] = (
                f"发送 {command} 未发送，短退避后重试: "
                f"{_small_world_block_label(block_code, block_reason)}"
            )
            _clear_local_unsent_action_guard_block("small_world_refine", started_at)
            save_state()
            return True
        state["small_world_last_error"] = f"发送 {command} 结果未知，等待回执或复查"
        _note_small_world_remote_block(
            "small_world_refine",
            command,
            started_at,
            state["next_small_world_time"],
            "小世界神识淬炼发送结果未知，等待回执",
            "send_unknown",
        )
        save_state()
        return True

    if _phase() == "refine_sent" and int(state.get("small_world_refine_msg_id", 0) or 0) <= 0:
        state["small_world_refine_msg_id"] = int(getattr(msg, "id", 0) or 0)
        _schedule_tool_step(sent_at)
        state["small_world_last_error"] = "神识淬炼已发送，等待回执确认"
        save_state()
    return True


async def _send_barrier(now, reason):
    started_at = float(now or time.time())
    if _defer_for_action_guard_block("small_world_barrier", CMD_SMALL_WORLD_BARRIER, started_at, "小世界护界禁制"):
        return True
    state["small_world_barrier_msg_id"] = 0
    state["small_world_barrier_due_at"] = float(started_at + SMALL_WORLD_BARRIER_REPLY_TIMEOUT_SEC)
    state["next_small_world_time"] = state["small_world_barrier_due_at"]
    state["small_world_last_error"] = "护界禁制已发起，等待回执确认"
    save_state()

    msg = await send_game_command(CMD_SMALL_WORLD_BARRIER, track=True, max_retry=0, priority="chain", source_module="小世界")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        if int(state.get("small_world_barrier_msg_id", 0) or 0) > 0 or float(state.get("small_world_barrier_due_at", 0) or 0) <= 0:
            return True
        definitely_unsent, block_code, block_reason = _small_world_definitely_unsent_block(CMD_SMALL_WORLD_BARRIER)
        if definitely_unsent:
            _clear_barrier_pending()
            _schedule_short_retry(started_at)
            state["small_world_last_error"] = (
                f"发送 .护界禁制 未发送，短退避后重试: {reason}: "
                f"{_small_world_block_label(block_code, block_reason)}"
            )
            _clear_local_unsent_action_guard_block("small_world_barrier", started_at)
            save_state()
            return True
        state["small_world_last_barrier_sent_at"] = started_at
        state["small_world_last_error"] = f"发送 .护界禁制 结果未知，等待回执: {reason}"
        _note_small_world_remote_block(
            "small_world_barrier",
            CMD_SMALL_WORLD_BARRIER,
            started_at,
            state["small_world_barrier_due_at"],
            "小世界护界禁制发送结果未知，等待回执",
            "send_unknown",
        )
        save_state()
        return True

    if int(state.get("small_world_barrier_msg_id", 0) or 0) <= 0 and float(state.get("small_world_barrier_due_at", 0) or 0) > 0:
        state["small_world_barrier_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["small_world_barrier_due_at"] = float(sent_at + SMALL_WORLD_BARRIER_REPLY_TIMEOUT_SEC)
        state["small_world_last_barrier_sent_at"] = sent_at
        state["next_small_world_time"] = state["small_world_barrier_due_at"]
        state["small_world_last_error"] = ""
        save_state()
    console_log(f"🌍 小世界临灾护界，已发送禁制：{reason}。")
    return True


def _schedule_refresh(now):
    current_count = int(state.get("small_world_refresh_count", 0) or 0)
    next_count = current_count + 1
    if next_count >= SMALL_WORLD_MAX_REFRESH_ATTEMPTS:
        _clear_chain_pending()
        state["small_world_refresh_count"] = 0
        _set_phase("idle")
        _schedule_next_cycle(now)
        state["small_world_last_error"] = f"祈愿刷新 {SMALL_WORLD_MAX_REFRESH_ATTEMPTS} 次未出现，已退避 6 小时"
        save_state()
        return False

    state["small_world_refresh_count"] = next_count
    _set_phase("refresh_wait")
    _schedule_after(now, SMALL_WORLD_REFRESH_MIN_SEC, SMALL_WORLD_REFRESH_MAX_SEC)
    state["small_world_last_error"] = ""
    save_state()
    return True


async def _finish_no_prayer_panel(now, panel, *, allow_refresh=True):
    _clear_chain_pending()
    if panel.get("prayer_data_error"):
        _schedule_panel_wait(now, SMALL_WORLD_MANIFEST_CD_SEC + CD_BUFFER_SEC)
        _set_phase("idle")
        state["small_world_last_error"] = ""
        save_state()
        return True

    if panel.get("has_wait"):
        _schedule_panel_wait(now, int(panel.get("wait_sec", 0) or 0) + CD_BUFFER_SEC)
        state["small_world_last_error"] = ""
        save_state()
        return True

    if allow_refresh and state.get("small_world_refresh_enabled"):
        if not _schedule_refresh(now):
            await send_audit_log(
                f"🌍 小世界祈愿刷新 {SMALL_WORLD_MAX_REFRESH_ATTEMPTS} 次仍未出现，已退避 6 小时。",
                scope="identity",
                limit=240,
            )
        return True

    if not allow_refresh:
        state["small_world_last_error"] = ""
        save_state()
        return True

    _schedule_next_cycle(now)
    state["small_world_last_error"] = ""
    save_state()
    return True


async def _handle_panel_decision(now, panel, *, allow_tool_chain=True):
    if panel.get("realm_blocked"):
        return await _disable_for_realm("境界不足")

    _apply_small_world_panel_snapshot(now, panel)

    if await _maybe_send_barrier_or_query(now, panel, fresh_panel=True):
        return True

    if panel.get("has_prayer"):
        state["small_world_refresh_count"] = 0
        _clear_chain_pending()
        _clear_maintenance_god_action()
        if state.get("small_world_manifest_enabled"):
            state["small_world_manifest_cost_text"] = str(panel.get("manifest_cost") or "").strip()
            save_state()
            return await _send_manifest(now)
        _schedule_next_cycle(now)
        state["small_world_last_error"] = "检测到祈愿，但自动显灵未开启"
        save_state()
        return True

    manifest_refresh_enabled = bool(
        allow_tool_chain
        and state.get("small_world_manifest_enabled")
        and state.get("small_world_refresh_enabled")
    )

    # A real prayer wait means refresh cannot run yet. Do not let the enabled
    # refresh switch suppress independent faith/stability maintenance for the
    # whole six-hour prayer window.
    maintenance_before_wait = bool(panel.get("has_wait") or not manifest_refresh_enabled)
    if maintenance_before_wait and state.get("small_world_preach_enabled", False) and not _has_active_small_world_pending(now):
        if _queue_maintenance_god_action(panel, now):
            _clear_chain_pending()
            return await _try_send_pending_god_action(now)

    if panel.get("has_wait"):
        return await _finish_no_prayer_panel(now, panel)

    # 香火只作为本轮刷新祈愿前的工具。进入刷新轮后继续收割会导致
    # ".小世界 -> 收割 -> 淬炼 -> 复查" 在每次刷新间重复出现。
    allow_tool_actions = int(state.get("small_world_refresh_count", 0) or 0) <= 0
    if (
        allow_tool_chain
        and
        allow_tool_actions
        and
        state.get("small_world_harvest_enabled")
        and float(panel.get("pending_incense", 0) or 0) >= SMALL_WORLD_MIN_HARVEST_INCENSE
    ):
        save_state()
        return await _send_harvest(now)

    refine_amount = _calc_refine_amount(panel.get("stock", 0))
    if allow_tool_chain and allow_tool_actions and state.get("small_world_refine_enabled") and refine_amount >= 10:
        save_state()
        return await _send_refine(now, refine_amount)

    if manifest_refresh_enabled:
        return await _finish_no_prayer_panel(now, panel, allow_refresh=True)

    if state.get("small_world_preach_enabled", False) and not _has_active_small_world_pending(now):
        if _queue_maintenance_god_action(panel, now):
            _clear_chain_pending()
            return await _try_send_pending_god_action(now)

    return await _finish_no_prayer_panel(now, panel, allow_refresh=allow_tool_chain)


def _god_action_label(action, priority, reason, queued_at):
    if action not in {"preach", "relief"}:
        return "无"
    action_name = "赈灾" if action == "relief" else "布道"
    level = "灾害" if int(priority or 0) >= SMALL_WORLD_GOD_PRIORITY_DISASTER else "维护"
    parts = [f"{action_name}（{level}）"]
    if reason:
        parts.append(reason)
    if queued_at:
        parts.append(f"排队于 {fmt_abs_ts(float(queued_at))}")
    return " / ".join(parts)


def _disaster_wave_label(next_wave_at, guard_end_at):
    if next_wave_at <= 0:
        return "未记录"
    if guard_end_at > 0:
        return f"{fmt_abs_ts(next_wave_at)}，保护至 {fmt_abs_ts(guard_end_at)}"
    return fmt_abs_ts(next_wave_at)


def get_small_world_status_text():
    now = time.time()
    faith_value = int(state.get("small_world_faith_value", 0) or 0)
    preach_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    next_time = float(state.get("next_small_world_time", 0) or 0)
    cooldown_until = _god_cooldown_until()
    pending_action = str(state.get("small_world_pending_god_action") or "")
    pending_reason = str(state.get("small_world_pending_god_reason") or "").strip()
    pending_priority = _pending_god_priority()
    pending_at = float(state.get("small_world_pending_god_at", 0) or 0)
    last_wave_at = float(state.get("small_world_last_disaster_wave_at", 0) or 0)
    next_wave_at = _next_disaster_wave_at(now)
    guard_end_at = _disaster_guard_end_at(now)
    snapshot = state.get("small_world_panel_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    lines = [
        "🌍 小世界",
        f"- 已启用：{'是' if state.get('small_world_enabled') else '否'}",
        f"- 神迹维护：{'开启' if state.get('small_world_preach_enabled', False) else '关闭'}",
        f"- 自动显灵：{'开启' if state.get('small_world_manifest_enabled') else '关闭'}",
        f"- 收割香火：{'开启' if state.get('small_world_harvest_enabled') else '关闭'}",
        f"- 神识淬炼：{'开启' if state.get('small_world_refine_enabled') else '关闭'}",
        f"- 祈愿刷新：{'开启' if state.get('small_world_refresh_enabled') else '关闭'}",
        f"- 护界禁制：{'开启' if state.get('small_world_barrier_enabled', True) else '关闭'}｜阈值 {int(state.get('small_world_barrier_min_stock', 130000) or 130000)}｜提前 {int(state.get('small_world_barrier_guard_before_min', 30) or 30)} 分钟｜间隔 {float(state.get('small_world_barrier_min_interval_hours', 18) or 18):g} 小时",
        f"- 当前阶段：{_phase()}",
        f"- 当前信仰：{faith_value if faith_value > 0 else '未记录'}",
        f"- 待收香火：{state.get('small_world_pending_incense', 0) or 0}",
        f"- 香火库存：{state.get('small_world_incense_stock', 0) or 0}",
    ]
    if snapshot:
        temple_parts = []
        if snapshot.get("owner"):
            temple_parts.append(str(snapshot.get("owner")))
        if snapshot.get("temple_level"):
            temple_text = f"Lv.{int(snapshot.get('temple_level') or 0)}"
            if snapshot.get("temple_name"):
                temple_text += f"【{snapshot.get('temple_name')}】"
            temple_parts.append(temple_text)
        if temple_parts:
            lines.append(f"- 神庙：{' / '.join(temple_parts)}")
        if snapshot.get("population") or snapshot.get("capacity"):
            lines.append(f"- 人口：{int(snapshot.get('population') or 0)} / {int(snapshot.get('capacity') or 0)}")
        if snapshot.get("stability") or snapshot.get("stability_max"):
            lines.append(f"- 稳定：{int(snapshot.get('stability') or 0)} / {int(snapshot.get('stability_max') or 0)}")
        if snapshot.get("hourly_output"):
            lines.append(f"- 预计产出：{float(snapshot.get('hourly_output') or 0):.2f} 香火/小时")
        if snapshot.get("barrier_status"):
            lines.append(f"- 护界禁制：{snapshot.get('barrier_status')}")
        if snapshot.get("spiritual_strength"):
            lines.append(f"- 神识强度：{int(snapshot.get('spiritual_strength') or 0)}")
        if snapshot.get("prayer_name"):
            cost_text = f"（{snapshot.get('manifest_cost')}）" if snapshot.get("manifest_cost") else ""
            lines.append(f"- 当前祈愿：{snapshot.get('prayer_name')}{cost_text}")
        elif snapshot.get("wait_text"):
            lines.append(f"- 祈愿感应：{snapshot.get('wait_text')}")
        if snapshot.get("next_temple_name") or snapshot.get("next_temple_cost"):
            lines.append(f"- 下一阶：{snapshot.get('next_temple_name') or '未记录'} / {snapshot.get('next_temple_cost') or '未记录'}")
    lines.extend([
        f"- 本轮刷新：{int(state.get('small_world_refresh_count', 0) or 0)}/{SMALL_WORLD_MAX_REFRESH_ATTEMPTS}",
        f"- 待神迹消息ID：{preach_msg_id or '无'}",
        f"- 神迹冷却：{fmt_abs_ts(cooldown_until) if cooldown_until > now else '可用'}",
        f"- 待执行神迹：{_god_action_label(pending_action, pending_priority, pending_reason, pending_at)}",
        f"- 最近灾害波：{fmt_abs_ts(last_wave_at) if last_wave_at > 0 else '未记录'}",
        f"- 下一灾害波：{_disaster_wave_label(next_wave_at, guard_end_at)}",
        f"- 下次动作：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）",
        f"- 最近错误：{state.get('small_world_last_error') or '无'}",
    ])
    return "\n".join(lines)


def clear_small_world_state(*, persist=False, keep_last_error=False):
    _clear_all_runtime_pending()
    state["next_small_world_time"] = 0
    state["small_world_faith_value"] = 0
    state["small_world_pending_incense"] = 0
    state["small_world_incense_stock"] = 0
    state["small_world_panel_snapshot"] = {}
    state["small_world_last_panel_at"] = 0
    state["small_world_barrier_msg_id"] = 0
    state["small_world_barrier_due_at"] = 0
    clear_pending_tasks_by_commands(
        SMALL_WORLD_CHAIN_COMMANDS | SMALL_WORLD_GOD_COMMANDS | SMALL_WORLD_BARRIER_COMMANDS,
        send_as_id=get_current_identity_id(),
    )
    if not keep_last_error:
        state["small_world_last_error"] = ""
    if persist:
        save_state()
    else:
        mark_dirty()


def schedule_small_world_initial_check(now, *, persist=False, keep_last_error=True):
    _clear_all_runtime_pending()
    _schedule_initial_check(now)
    if not keep_last_error:
        state["small_world_last_error"] = ""
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_small_world_time"]


def restore_small_world_runtime(now, *, persist=False):
    now = float(now or time.time())
    phase = _phase()
    pending_tasks = state.get("pending_tasks")
    if not isinstance(pending_tasks, dict):
        pending_tasks = {}

    pending_action = str(state.get("small_world_pending_god_action") or "")
    pending_reason = str(state.get("small_world_pending_god_reason") or "")
    if pending_action == "relief" and pending_reason.startswith("灾害:") and pending_reason.endswith("，赈灾安抚"):
        state["small_world_pending_god_action"] = "preach"
        state["small_world_pending_god_reason"] = pending_reason.removesuffix("，赈灾安抚") + "，布道安抚"
        state["small_world_last_error"] = "灾害神迹已按默认布道策略校正"
        mark_dirty()

    query_msg_id = int(state.get("small_world_query_msg_id", 0) or 0)
    if phase == "query_pending" and query_msg_id > 0 and query_msg_id not in pending_tasks:
        _clear_chain_pending()
        sessions = state.get("action_guard_sessions")
        if isinstance(sessions, dict):
            sessions.pop("small_world_query", None)
        state["small_world_last_error"] = f"{phase} 遗留等待已恢复清理"
        _schedule_short_retry(now)
        if persist:
            save_state()
        else:
            mark_dirty()
        return True

    if float(state.get("next_small_world_time", 0) or 0) <= 0:
        schedule_small_world_initial_check(now, persist=persist, keep_last_error=True)
        return True
    return False


def _disaster_kind(raw_text):
    matched = RE_SMALL_WORLD_TARGET_TAG.search(raw_text or "")
    return matched.group(2).strip() if matched else ""


def _disaster_god_action(raw_text):
    raw_text = str(raw_text or "")
    kind = _disaster_kind(raw_text)
    if RE_SMALL_WORLD_EXPLICIT_RELIEF.search(raw_text):
        return "relief", f"灾害: {kind or '小世界'}，赈灾安抚"
    if (
        "邪神" in raw_text
        or RE_SMALL_WORLD_FAITH_DAMAGE.search(raw_text)
        or RE_SMALL_WORLD_RELIEF_DAMAGE.search(raw_text)
        or kind in {"灭世瘟疫", "王朝更迭"}
    ):
        return "preach", f"灾害: {kind or '信仰异常'}，布道安抚"
    return "", ""


def _apply_disaster_incense_loss(loss_amount):
    stock = max(0, int(state.get("small_world_incense_stock", 0) or 0) - max(0, int(loss_amount or 0)))
    state["small_world_incense_stock"] = stock
    state["small_world_last_error"] = f"库存香火失窃 {int(loss_amount or 0)} 点"


async def handle_small_world_disaster_broadcast(text, now, event):
    raw_text = text or ""
    if not RE_SMALL_WORLD_DISASTER.search(raw_text):
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_preach_enabled", False):
        return False

    _mark_disaster_wave(now)

    identity_id = _find_small_world_identity_id(raw_text)
    if identity_id is None or identity_id != get_current_identity_id():
        return False

    incense_loss = RE_SMALL_WORLD_INCENSE_LOSS.search(raw_text)
    action, reason = _disaster_god_action(raw_text)
    if incense_loss:
        loss_amount = int(incense_loss.group(1))
        if not action:
            due_at = _schedule_theft_calibration(now, loss_amount)
            save_state()
            await send_audit_log(
                f"⚠️ 小世界库存香火失窃 {loss_amount} 点，已记录，{fmt_time_after(max(0, due_at - now))} 后校准面板。",
                scope="identity",
                limit=240,
            )
            return True
        _apply_disaster_incense_loss(loss_amount)

    if not action:
        save_state()
        return False

    _queue_god_action(action, reason, SMALL_WORLD_GOD_PRIORITY_DISASTER, now)
    return await _try_send_pending_god_action(now)


async def handle_small_world_preach_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family not in {"small_world_preach", "small_world_relief"}:
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_preach_enabled", False):
        return False
    if not _is_current_god_reply(reply_to, matched_family):
        return False
    _close_small_world_action_guard("small_world_preach", now)

    raw_text = text or ""
    wait_sec, wait_text = _parse_wait_from_text(raw_text)
    if wait_sec > 0 and RE_SMALL_WORLD_GOD_COOLDOWN.search(raw_text):
        _clear_preach_pending()
        _clear_god_pending_tasks()
        state["small_world_last_error"] = f"神迹冷却中: {wait_text}"
        state["small_world_god_cooldown_until"] = float(now + wait_sec + CD_BUFFER_SEC)
        _note_small_world_god_remote_block(
            CMD_SMALL_WORLD_RELIEF if matched_family == "small_world_relief" else CMD_SMALL_WORLD_PREACH,
            now,
            state["small_world_god_cooldown_until"],
            "游戏提示神迹冷却",
            "cooldown",
        )
        if state.get("small_world_pending_god_action"):
            _schedule_pending_god_action(now)
        else:
            _schedule_panel_wait(now, wait_sec + CD_BUFFER_SEC)
        save_state()
        return True

    if _is_god_resource_shortage_text(raw_text):
        action_name = _god_action_name_from_context(reply_to, matched_family)
        label = _god_resource_label_from_text(raw_text)
        if action_name == "赈灾" and label == "灵石":
            priority = _pending_god_priority() or SMALL_WORLD_GOD_PRIORITY_DISASTER
            _clear_preach_pending()
            _clear_god_pending_tasks()
            _clear_pending_god_action()
            _queue_god_action("preach", "赈灾没钱转布道", priority, now)
            save_state()
            await _send_small_world_preach(now, "赈灾没钱转布道")
            return True
        due_at = _schedule_resource_pause(now, f"神迹{action_name}/{label}", raw_text)
        _clear_preach_pending()
        _clear_god_pending_tasks()
        _clear_pending_god_action()
        save_state()
        await send_audit_log(
            f"⚠️ 小世界神迹{action_name}资源不足（{label}），本轮停止，{fmt_time_after(max(0, due_at - now))} 后再查；请手动补资源。",
            scope="identity",
            limit=260,
        )
        return True

    is_preach = RE_SMALL_WORLD_PREACH_PANEL.search(raw_text)
    is_relief = RE_SMALL_WORLD_RELIEF_PANEL.search(raw_text)
    if not is_preach and not is_relief:
        return False

    if not _apply_god_result(raw_text, now):
        state["small_world_last_error"] = "小世界神迹回复未解析到状态"
        _clear_preach_pending()
        _clear_god_pending_tasks()
        _clear_pending_god_action()
        _schedule_god_followup(now)
        save_state()
        return True

    _clear_preach_pending()
    _clear_god_pending_tasks()
    _clear_pending_god_action()
    state["small_world_last_error"] = ""
    if state.get("small_world_preach_enabled", False):
        _queue_maintenance_from_snapshot(now)
    _schedule_god_followup(now)
    _note_small_world_god_remote_block(
        CMD_SMALL_WORLD_RELIEF if is_relief else CMD_SMALL_WORLD_PREACH,
        now,
        state["small_world_god_cooldown_until"],
        "神迹成功后的游戏冷却",
        "success",
    )
    save_state()
    return True


async def handle_small_world_barrier_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_barrier":
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_barrier_enabled", True):
        return False
    if not _is_current_barrier_reply(reply_to, matched_family):
        return False
    _close_small_world_action_guard("small_world_barrier", now)

    raw_text = str(text or "")
    if "境界不足" in raw_text and "紫府小世界" in raw_text:
        return await _disable_for_realm(raw_text)

    if _is_resource_shortage_text(raw_text) or "香火不足" in raw_text or "库存不足" in raw_text:
        _clear_barrier_pending()
        clear_pending_tasks_by_commands(SMALL_WORLD_BARRIER_COMMANDS, send_as_id=get_current_identity_id())
        state["small_world_last_error"] = f"护界禁制资源不足: {_truncate(raw_text)}"
        _schedule_next_cycle(now)
        save_state()
        await send_audit_log("⚠️ 小世界护界禁制资源不足，已停止本轮，约 8 小时后再查。", scope="identity")
        return True

    if "护界禁制" not in raw_text:
        return False

    burned_match = RE_BARRIER_BURNED.search(raw_text)
    if burned_match:
        burned = int(burned_match.group(1))
        stock = max(0, int(state.get("small_world_incense_stock", 0) or 0) - burned)
        state["small_world_incense_stock"] = stock
        _update_snapshot_field("stock", stock)

    if (
        burned_match
        or "不会遭受随机天灾" in raw_text
        or "愿力金幕" in raw_text
        or "已开启" in raw_text
        or "尚未消散" in raw_text
        or "仍在" in raw_text
    ):
        _clear_barrier_pending()
        clear_pending_tasks_by_commands(SMALL_WORLD_BARRIER_COMMANDS, send_as_id=get_current_identity_id())
        _update_snapshot_field("barrier_status", "已开启")
        _update_snapshot_field("updated_at", float(now))
        state["small_world_last_error"] = ""
        _schedule_next_cycle(now)
        save_state()
        return True

    if "尚未" in raw_text or "冷却" in raw_text or "稍后" in raw_text:
        _clear_barrier_pending()
        clear_pending_tasks_by_commands(SMALL_WORLD_BARRIER_COMMANDS, send_as_id=get_current_identity_id())
        state["small_world_last_error"] = f"护界禁制暂不可用: {_truncate(raw_text)}"
        _schedule_next_cycle(now)
        save_state()
        return True

    state["small_world_last_error"] = f"未识别的护界禁制回复: {_truncate(raw_text)}"
    _clear_barrier_pending()
    _schedule_short_retry(now)
    save_state()
    return False


async def handle_small_world_query_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_query":
        return False
    if not state.get("small_world_enabled") or not (_chain_enabled() or state.get("small_world_barrier_enabled", True)):
        return False

    raw_text = text or ""
    panel = _parse_small_world_panel(raw_text)
    if not panel:
        return False
    if not _is_current_query_reply(reply_to, matched_family):
        return False
    _close_small_world_action_guard("small_world_query", now)

    if panel.get("realm_blocked"):
        return await _disable_for_realm(raw_text)

    return await _handle_panel_decision(now, panel)


async def handle_small_world_manifest_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_manifest":
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_manifest_enabled"):
        return False

    raw_text = str(text or "")
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    if matched_family != "small_world_manifest" and CMD_SMALL_WORLD_MANIFEST not in orig_cmd:
        return False
    _close_small_world_action_guard("small_world_manifest", now)

    if _is_resource_shortage_text(raw_text):
        label = _resource_label_from_text(raw_text)
        due_at = _schedule_resource_pause(
            now,
            f"显灵/{label}",
            raw_text,
            pause_sec=SMALL_WORLD_MANIFEST_RESOURCE_PAUSE_SEC,
        )
        _clear_manifest_snapshot_prayer(now)
        save_state()
        await send_audit_log(
            f"⚠️ 小世界显灵资源不足（{label}），本轮停止并退避 6 小时，{fmt_time_after(max(0, due_at - now))} 后再查；请手动处理。",
            scope="identity",
            limit=260,
        )
        return True

    if "当前没有凡人祈愿需要处理" in raw_text:
        _clear_chain_pending()
        if state.get("small_world_refresh_enabled"):
            _schedule_refresh(now)
        else:
            _schedule_next_cycle(now)
        state["small_world_last_error"] = ""
        save_state()
        return True

    if "显灵成功" in raw_text or "显灵失败" in raw_text or "天机已散" in raw_text:
        if "显灵成功" in raw_text:
            apply_storage_bag_item_text_delta(
                get_current_identity_id(),
                state.get("small_world_manifest_cost_text"),
                sign=-1,
                allow_plain=True,
            )
        _apply_manifest_delta(raw_text, now)
        new_prayer_cached = False
        if "天机已散" in raw_text and "新的凡人祈愿" in raw_text:
            new_prayer_cached = _cache_manifest_snapshot_prayer(now, raw_text)
        else:
            _clear_manifest_snapshot_prayer(now)
        wait_sec, _wait_text = _parse_wait_from_text(raw_text)
        _clear_chain_pending()
        state["small_world_refresh_count"] = 0
        if "显灵成功" in raw_text:
            state["small_world_last_error"] = ""
        elif new_prayer_cached:
            state["small_world_last_error"] = "旧祈愿已散，新的凡人祈愿待显灵"
        elif "天机已散" in raw_text:
            state["small_world_last_error"] = "祈愿已超过 24 小时，天机已散"
        else:
            state["small_world_last_error"] = "显灵失败，停止本轮"
        if new_prayer_cached and state.get("small_world_manifest_enabled"):
            state["next_small_world_time"] = float(now + SMALL_WORLD_SAME_COMMAND_GUARD_SEC)
        elif wait_sec > 0:
            _schedule_panel_wait(now, wait_sec + CD_BUFFER_SEC)
        else:
            _schedule_panel_wait(now, SMALL_WORLD_MANIFEST_CD_SEC + CD_BUFFER_SEC)
        save_state()
        return True

    state["small_world_last_error"] = f"未识别的显灵回复: {_truncate(raw_text)}"
    _clear_chain_pending()
    _schedule_next_cycle(now)
    save_state()
    return False


async def handle_small_world_harvest_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_harvest":
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_harvest_enabled"):
        return False

    raw_text = str(text or "")
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    if _phase() not in {"harvest_sent", "harvest_pending"}:
        if _phase() != "harvest_before_manifest_sent":
            return False
    if matched_family != "small_world_harvest" and not _is_reply_to_tracked_message(reply_to, "small_world_harvest_msg_id") and CMD_SMALL_WORLD_HARVEST not in orig_cmd:
        return False
    _close_small_world_action_guard("small_world_harvest", now)

    was_before_manifest = _phase() == "harvest_before_manifest_sent"

    if "境界不足" in raw_text and "紫府小世界" in raw_text:
        return await _disable_for_realm(raw_text)

    stock_match = RE_HARVEST_STOCK.search(raw_text)
    if stock_match:
        stock = int(stock_match.group(1))
        state["small_world_incense_stock"] = stock
        state["small_world_pending_incense"] = 0
        state["small_world_last_error"] = ""
        _clear_chain_pending()
        refine_amount = _calc_refine_amount(stock)
        save_state()
        if was_before_manifest:
            return await _send_query(now, "显灵前收割后复查")
        if state.get("small_world_refine_enabled") and refine_amount >= 10:
            return await _send_refine(now, refine_amount)
        return await _send_query(now, "收割后复查")

    shortage_match = RE_STOCK_SHORTAGE.search(raw_text)
    if shortage_match:
        state["small_world_incense_stock"] = int(shortage_match.group(1))
        state["small_world_last_error"] = f"收割香火库存不足: {_truncate(raw_text)}"
        _clear_chain_pending()
        _schedule_next_cycle(now)
        save_state()
        return True

    state["small_world_last_error"] = f"未识别的收割回复: {_truncate(raw_text)}"
    _clear_chain_pending()
    _schedule_next_cycle(now)
    save_state()
    return False


async def handle_small_world_refine_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_refine":
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_refine_enabled"):
        return False

    raw_text = str(text or "")
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    if _phase() not in {"refine_sent", "refine_pending"}:
        return False
    if matched_family != "small_world_refine" and not _is_reply_to_tracked_message(reply_to, "small_world_refine_msg_id") and CMD_SMALL_WORLD_REFINE not in orig_cmd:
        return False
    _close_small_world_action_guard("small_world_refine", now)

    burned_match = RE_REFINE_BURNED.search(raw_text)
    if "神识淬炼" in raw_text and burned_match:
        burned = int(burned_match.group(1))
        state["small_world_incense_stock"] = max(0, int(state.get("small_world_incense_stock", 0) or 0) - burned)
        state["small_world_last_error"] = ""
        _clear_chain_pending()
        save_state()
        return await _send_query(now, "淬炼后复查")

    shortage_match = RE_STOCK_SHORTAGE.search(raw_text)
    if shortage_match:
        state["small_world_incense_stock"] = int(shortage_match.group(1))
        state["small_world_last_error"] = f"淬炼库存不足: {_truncate(raw_text)}"
        _clear_chain_pending()
        _schedule_next_cycle(now)
        save_state()
        await send_audit_log("⚠️ 小世界神识淬炼库存不足，已停止本轮，约 8 小时后再查。", scope="identity")
        return True

    if _is_resource_shortage_text(raw_text):
        label = _resource_label_from_text(raw_text)
        due_at = _schedule_resource_pause(now, f"神识淬炼/{label}", raw_text)
        save_state()
        await send_audit_log(
            f"⚠️ 小世界神识淬炼资源不足（{label}），本轮停止，{fmt_time_after(max(0, due_at - now))} 后再查。",
            scope="identity",
            limit=240,
        )
        return True

    state["small_world_last_error"] = f"未识别的淬炼回复: {_truncate(raw_text)}"
    _clear_chain_pending()
    _schedule_next_cycle(now)
    save_state()
    return False


async def run_small_world_scheduler(now):
    if is_cave_public_auto_enabled("small_world"):
        return
    if _SMALL_WORLD_SCHEDULER_LOCK.locked():
        return
    async with _SMALL_WORLD_SCHEDULER_LOCK:
        await _run_small_world_scheduler(now)


async def _run_small_world_scheduler(now):
    if not state.get("small_world_enabled"):
        return

    barrier_msg_id = int(state.get("small_world_barrier_msg_id", 0) or 0)
    barrier_deadline = float(state.get("small_world_barrier_due_at", 0) or 0)
    if barrier_deadline > 0:
        if now >= barrier_deadline:
            if await _recover_current_small_world_pending_from_log(now, "barrier_pending"):
                save_state()
                return
            state["small_world_last_error"] = "小世界护界禁制回复超时"
            _clear_barrier_pending()
            clear_pending_tasks_by_commands(SMALL_WORLD_BARRIER_COMMANDS, send_as_id=get_current_identity_id())
            _schedule_short_retry(now)
            save_state()
            await send_audit_log(f"⚠️ 小世界护界禁制回复超时，消息ID={barrier_msg_id or '未知'}", scope="identity")
        return

    preach_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    preach_deadline = _get_preach_deadline()
    if _phase() == "preach_pending" and preach_deadline > 0:
        if now >= preach_deadline:
            if preach_msg_id <= 0:
                state["small_world_last_error"] = "小世界神迹发送结果未知，已保留待办并短退避重试"
                _clear_preach_pending()
                _clear_god_pending_tasks()
                if state.get("small_world_pending_god_action"):
                    if _pending_god_priority() >= SMALL_WORLD_GOD_PRIORITY_DISASTER:
                        _schedule_after(now, SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC)
                    else:
                        _schedule_short_retry(now)
                save_state()
                return
            if await _recover_current_small_world_pending_from_log(now, "preach_pending"):
                save_state()
                return
            state["small_world_last_error"] = "小世界神迹回复超时"
            _clear_preach_pending()
            _clear_god_pending_tasks()
            if state.get("small_world_pending_god_action"):
                _schedule_after(now, SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC)
            save_state()
            await send_audit_log(f"⚠️ 小世界神迹回复超时，消息ID={preach_msg_id or '未知'}", scope="identity")
        return

    if (
        state.get("small_world_pending_god_action")
        and _pending_god_priority() >= SMALL_WORLD_GOD_PRIORITY_DISASTER
        and _god_cooldown_until() <= float(now or time.time())
    ):
        if await _try_send_pending_god_action(now):
            return

    phase = _phase()
    if phase in SMALL_WORLD_CHAIN_PENDING:
        deadline = float(state.get("next_small_world_time", 0) or 0)
        if deadline <= 0 or now < deadline:
            return
        if await _recover_current_small_world_pending_from_log(now, phase):
            save_state()
            return
        state["small_world_last_error"] = f"{phase} 等待回复超时，停止本轮"
        _clear_chain_pending()
        if phase == "manifest_pending":
            if _has_ready_manifest_snapshot(now):
                state["small_world_last_error"] = "显灵回执超时，复查小世界面板后再决定是否补显灵"
                save_state()
                await send_audit_log(
                    "⚠️ 小世界显灵回执超时，先复查面板，避免盲目重复显灵。",
                    scope="identity",
                    limit=220,
                )
                await _send_query(now, "显灵回执超时复查")
                return
            _schedule_next_cycle(now)
        else:
            _schedule_short_retry(now)
        save_state()
        await send_audit_log(
            f"⚠️ 小世界模块 {phase} 超时，已停止当前链路，{fmt_time_after(max(0, state['next_small_world_time'] - now))} 后再校准。",
            scope="identity",
            limit=260,
        )
        return

    if phase in {"harvest_sent", "harvest_before_manifest_sent"}:
        next_time = float(state.get("next_small_world_time", 0) or 0)
        if next_time > 0 and now < next_time:
            return
        if await _recover_current_small_world_pending_from_log(now, phase):
            save_state()
            return
        _clear_chain_pending()
        if phase == "harvest_before_manifest_sent":
            state["small_world_last_error"] = "显灵前收割香火未收到可解析回执，复查面板校准"
            await _send_query(now, "显灵前收割后复查")
        else:
            state["small_world_last_error"] = "收割香火未收到可解析回执，复查面板校准"
            await _send_query(now, "收割后复查")
        return

    if phase == "refine_sent":
        next_time = float(state.get("next_small_world_time", 0) or 0)
        if next_time > 0 and now < next_time:
            return
        if await _recover_current_small_world_pending_from_log(now, phase):
            save_state()
            return
        _clear_chain_pending()
        state["small_world_last_error"] = "神识淬炼未收到可解析回执，复查面板校准"
        await _send_query(now, "淬炼后复查")
        return

    next_time = float(state.get("next_small_world_time", 0) or 0)
    if await _maybe_send_barrier_or_query(now):
        return

    if phase == "idle" and _chain_enabled() and _has_ready_manifest_snapshot(now):
        state["small_world_refresh_count"] = 0
        state["small_world_manifest_cost_text"] = str(
            (state.get("small_world_panel_snapshot") or {}).get("manifest_cost") or ""
        ).strip()
        save_state()
        await _send_manifest(now)
        return

    if next_time > 0 and now < next_time:
        return

    if state.get("small_world_pending_god_action"):
        if await _try_send_pending_god_action(now):
            return

    if phase == "calibration_wait":
        await _send_query(now, "失窃后校准")
        return

    if not _chain_enabled():
        return

    if phase == "refresh_wait":
        refresh_attempt = int(state.get("small_world_refresh_count", 0) or 0)
        await _send_query(now, f"祈愿刷新 {refresh_attempt}/{SMALL_WORLD_MAX_REFRESH_ATTEMPTS}", refresh_attempt=refresh_attempt)
        return

    state["small_world_refresh_count"] = 0
    await _send_query(now, "周期自查")


__all__ = [
    "clear_small_world_state",
    "get_small_world_status_text",
    "handle_small_world_barrier_reply",
    "handle_small_world_disaster_broadcast",
    "handle_small_world_harvest_reply",
    "handle_small_world_manifest_reply",
    "handle_small_world_preach_reply",
    "handle_small_world_query_reply",
    "handle_small_world_refine_reply",
    "run_small_world_scheduler",
    "restore_small_world_runtime",
    "schedule_small_world_initial_check",
]
