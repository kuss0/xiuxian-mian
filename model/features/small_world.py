import asyncio
import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_PREACH,
    CMD_SMALL_WORLD_QUERY,
    CMD_SMALL_WORLD_RELIEF,
    CMD_SMALL_WORLD_REFINE,
    SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC,
)
from ..persistence import mark_dirty, save_state
from ..runtime import clear_pending_tasks_by_commands, console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_identity_enabled, get_identity_ids, get_send_as_tags, state
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
SMALL_WORLD_CHAIN_PENDING = {"query_pending", "manifest_pending", "harvest_pending", "refine_pending"}
SMALL_WORLD_PENDING_TIMEOUT_SEC = 20 * 60
SMALL_WORLD_REFRESH_MIN_SEC = 60
SMALL_WORLD_REFRESH_MAX_SEC = 60
SMALL_WORLD_MAX_REFRESH_ATTEMPTS = 7
SMALL_WORLD_REFRESH_ROUND_PAUSE_SEC = 5 * 60
SMALL_WORLD_CYCLE_CD_SEC = 8 * 3600
SMALL_WORLD_MANIFEST_CD_SEC = 6 * 3600
SMALL_WORLD_LONG_PAUSE_SEC = 8 * 3600
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
SMALL_WORLD_GOD_FOLLOWUP_SEC = 3 * 3600
SMALL_WORLD_GOD_RESEND_GUARD_SEC = 5 * 60
SMALL_WORLD_GOD_PRIORITY_MAINTENANCE = 10
SMALL_WORLD_GOD_PRIORITY_DISASTER = 100
SMALL_WORLD_DISASTER_WAVE_INTERVAL_SEC = 3 * 3600
SMALL_WORLD_DISASTER_GUARD_BEFORE_SEC = 30 * 60
SMALL_WORLD_DISASTER_GUARD_AFTER_SEC = 25 * 60
SMALL_WORLD_RELIEF_POPULATION_RATIO_TRIGGER = 0.95
SMALL_WORLD_RELIEF_STABILITY_RATIO_TRIGGER = 0.80

_SMALL_WORLD_GOD_ACTION_LOCK = asyncio.Lock()

RE_SMALL_WORLD_DISASTER = re.compile(r"【小世界·天降浩劫】")
RE_SMALL_WORLD_TARGET_TAG = re.compile(rf"道友\s*@({SMALL_WORLD_TARGET_TAG_PATTERN})\s*的小世界遭遇\s*【([^】]+)】")
RE_SMALL_WORLD_FAITH_DAMAGE = re.compile(r"惨重代价\s*[:：]\s*信仰(?:崩塌|动摇)\s*-\s*\d+\s*点")
RE_SMALL_WORLD_RELIEF_DAMAGE = re.compile(r"惨重代价\s*[:：].*(?:人口|稳定|瘟疫|王朝更迭)")
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
RE_NEXT_TEMPLE_COST = re.compile(r"下一阶【([^】]+)】消耗\s*[:：]\s*([^\n]+)")
RE_MANIFEST_COST = re.compile(r"显灵消耗\s*[:：]\s*([^\n]+)")
RE_MANIFEST_DELTA = re.compile(r"信仰\s*([+-]\d+).*?稳定\s*([+-]\d+).*?人口\s*([+-]\d+)", re.S)
RE_HARVEST_STOCK = re.compile(r"当前香火库存\s*[:：]\s*(\d+)")
RE_REFINE_BURNED = re.compile(r"燃烧了\s*(\d+)\s*点香火")
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


def _clear_chain_pending():
    state["small_world_query_msg_id"] = 0
    state["small_world_manifest_msg_id"] = 0
    state["small_world_manifest_cost_text"] = ""
    state["small_world_harvest_msg_id"] = 0
    state["small_world_refine_msg_id"] = 0
    if _phase() in SMALL_WORLD_CHAIN_PENDING or _phase() in {"harvest_sent", "harvest_before_manifest_sent", "refine_sent"}:
        _set_phase("idle")


def _clear_all_runtime_pending():
    _clear_preach_pending()
    _clear_pending_god_action()
    _clear_chain_pending()
    state["small_world_refresh_count"] = 0


def _schedule_after(now, min_sec, max_sec):
    state["next_small_world_time"] = float(now + random.uniform(float(min_sec), float(max_sec)))
    return state["next_small_world_time"]


def _schedule_next_cycle(now):
    return _schedule_after(now, SMALL_WORLD_CYCLE_CD_SEC + SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_CYCLE_CD_SEC + SMALL_WORLD_JITTER_MAX_SEC)


def _schedule_short_retry(now):
    return _schedule_after(now, 10 * 60, 30 * 60)


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
    state["small_world_god_cooldown_until"] = float(now + SMALL_WORLD_GOD_FOLLOWUP_SEC)
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


def _schedule_resource_pause(now, label, raw_text):
    due_at = float(now + SMALL_WORLD_LONG_PAUSE_SEC + random.uniform(SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC))
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
    reply_to_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    deadline = _get_preach_deadline()
    return reply_to_msg_id > 0 and deadline > now


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


async def _disable_for_realm(raw_text):
    _clear_all_runtime_pending()
    state["small_world_enabled"] = False
    state["next_small_world_time"] = 0
    state["small_world_last_error"] = "境界不足，已关闭小世界模块"
    clear_pending_tasks_by_commands(SMALL_WORLD_CHAIN_COMMANDS | SMALL_WORLD_GOD_COMMANDS, send_as_id=get_current_identity_id())
    save_state()
    await send_audit_log("⚠️ 小世界境界不足，已关闭该身份的小世界模块。", scope="identity")
    return True


async def _send_small_world_god_action(now, command, reason):
    async with _SMALL_WORLD_GOD_ACTION_LOCK:
        command = CMD_SMALL_WORLD_RELIEF if command == CMD_SMALL_WORLD_RELIEF else CMD_SMALL_WORLD_PREACH
        action = _command_god_action(command)
        action_name = _god_action_name(action)

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
            save_state()
            return True

        previous_action = str(state.get("small_world_last_god_action") or "")
        previous_sent_at = float(state.get("small_world_last_god_sent_at", 0) or 0)
        optimistic_sent_at = float(now or time.time())
        state["small_world_last_god_action"] = action
        state["small_world_last_god_sent_at"] = optimistic_sent_at
        save_state()

        sent_msg = await send_game_command(command, track=True, max_retry=0, source_module="小世界")
        sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time()) if sent_msg else time.time()
        if not sent_msg:
            if (
                str(state.get("small_world_last_god_action") or "") == action
                and float(state.get("small_world_last_god_sent_at", 0) or 0) == optimistic_sent_at
            ):
                state["small_world_last_god_action"] = previous_action
                state["small_world_last_god_sent_at"] = previous_sent_at
            state["small_world_last_error"] = f"神迹{action_name}指令发送失败"
            _schedule_short_retry(sent_at)
            save_state()
            await send_audit_log(f"❌ 小世界{action_name}发送失败，稍后重试。", scope="identity")
            return False

        _set_phase("preach_pending")
        state["small_world_preach_reply_to_msg_id"] = int(getattr(sent_msg, "id", 0) or 0)
        state["small_world_preach_due_at"] = float(sent_at + SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC)
        state["small_world_last_god_action"] = action
        state["small_world_last_god_sent_at"] = sent_at
        state["next_small_world_time"] = state["small_world_preach_due_at"]
        state["small_world_last_error"] = ""
        save_state()
        console_log(f"🌍 小世界{reason}，已发送神迹{action_name}。")
        return True


async def _send_small_world_preach(now, reason):
    return await _send_small_world_god_action(now, CMD_SMALL_WORLD_PREACH, reason)


async def _send_small_world_relief(now, reason):
    return await _send_small_world_god_action(now, CMD_SMALL_WORLD_RELIEF, reason)


async def _send_query(now, reason, *, refresh_attempt=None):
    msg = await send_game_command(CMD_SMALL_WORLD_QUERY, track=True, max_retry=0, priority="chain", source_module="小世界")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["small_world_last_error"] = f"{reason}发送 .小世界 失败"
        _set_phase("idle")
        _schedule_short_retry(sent_at)
        save_state()
        await send_audit_log("❌ 小世界查询发送失败，稍后重试。", scope="identity")
        return False

    _set_phase("query_pending")
    state["small_world_query_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_small_world_time"] = sent_at + SMALL_WORLD_PENDING_TIMEOUT_SEC
    if refresh_attempt is not None:
        state["small_world_refresh_count"] = max(0, int(refresh_attempt or 0))
    state["small_world_last_error"] = ""
    save_state()
    console_log(f"🌍 小世界查询已发送：{reason}。")
    return True


async def _send_manifest(now):
    msg = await send_game_command(CMD_SMALL_WORLD_MANIFEST, track=False, max_retry=0, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["small_world_last_error"] = "发送 .显灵 失败"
        _clear_chain_pending()
        _schedule_short_retry(sent_at)
        save_state()
        await send_audit_log("❌ 小世界显灵发送失败，稍后重试。", scope="identity")
        return False

    _set_phase("manifest_pending")
    state["small_world_manifest_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_small_world_time"] = sent_at + SMALL_WORLD_PENDING_TIMEOUT_SEC
    state["small_world_last_error"] = ""
    save_state()
    return True


async def _send_harvest(now):
    msg = await send_game_command(CMD_SMALL_WORLD_HARVEST, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["small_world_last_error"] = "发送 .收割香火 失败"
        _clear_chain_pending()
        _schedule_short_retry(sent_at)
        save_state()
        await send_audit_log("❌ 小世界收割香火发送失败，稍后重试。", scope="identity")
        return False

    _set_phase("harvest_sent")
    state["small_world_harvest_msg_id"] = int(getattr(msg, "id", 0) or 0)
    _schedule_tool_step(sent_at)
    state["small_world_last_error"] = "收割香火已发送，等待回执确认"
    save_state()
    return True


async def _send_harvest_before_manifest(now):
    if not await _send_harvest(now):
        return False
    _set_phase("harvest_before_manifest_sent")
    state["small_world_last_error"] = "显灵前收割香火已发送，等待回执确认"
    save_state()
    return True


async def _send_refine(now, amount):
    amount = _calc_refine_amount(amount)
    if amount < 10:
        return await _send_query(now, "淬炼数量不足，复查小世界")

    command = f"{CMD_SMALL_WORLD_REFINE} {amount}"
    msg = await send_game_command(command, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["small_world_last_error"] = f"发送 {command} 失败"
        _clear_chain_pending()
        _schedule_short_retry(sent_at)
        save_state()
        await send_audit_log("❌ 小世界神识淬炼发送失败，稍后重试。", scope="identity")
        return False

    _set_phase("refine_sent")
    state["small_world_refine_msg_id"] = int(getattr(msg, "id", 0) or 0)
    _schedule_tool_step(sent_at)
    state["small_world_last_error"] = "神识淬炼已发送，等待回执确认"
    save_state()
    return True


def _schedule_refresh(now):
    current_count = int(state.get("small_world_refresh_count", 0) or 0)
    if current_count >= SMALL_WORLD_MAX_REFRESH_ATTEMPTS:
        _clear_chain_pending()
        state["small_world_refresh_count"] = 1
        _set_phase("refresh_wait")
        _schedule_after(now, SMALL_WORLD_REFRESH_ROUND_PAUSE_SEC, SMALL_WORLD_REFRESH_ROUND_PAUSE_SEC)
        state["small_world_last_error"] = f"祈愿刷新 {SMALL_WORLD_MAX_REFRESH_ATTEMPTS} 次未出现，5 分钟后继续刷新"
        save_state()
        return False

    state["small_world_refresh_count"] = current_count + 1
    _set_phase("refresh_wait")
    _schedule_after(now, SMALL_WORLD_REFRESH_MIN_SEC, SMALL_WORLD_REFRESH_MAX_SEC)
    state["small_world_last_error"] = ""
    save_state()
    return True


async def _finish_no_prayer_panel(now, panel, *, allow_refresh=True):
    _clear_chain_pending()
    if panel.get("has_wait"):
        _schedule_panel_wait(now, int(panel.get("wait_sec", 0) or 0) + CD_BUFFER_SEC)
        state["small_world_last_error"] = ""
        save_state()
        return True

    if allow_refresh and state.get("small_world_refresh_enabled"):
        if not _schedule_refresh(now):
            await send_audit_log(
                f"🌍 小世界祈愿刷新 {SMALL_WORLD_MAX_REFRESH_ATTEMPTS} 次仍未出现，5 分钟后继续下一轮刷新。",
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

    if panel.get("has_prayer"):
        state["small_world_refresh_count"] = 0
        _clear_chain_pending()
        _clear_maintenance_god_action()
        if state.get("small_world_manifest_enabled"):
            state["small_world_manifest_cost_text"] = str(panel.get("manifest_cost") or "").strip()
            if state.get("small_world_harvest_enabled") and float(panel.get("pending_incense", 0) or 0) >= SMALL_WORLD_MIN_HARVEST_INCENSE:
                save_state()
                return await _send_harvest_before_manifest(now)
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

    if not manifest_refresh_enabled and state.get("small_world_preach_enabled", False) and not _has_active_small_world_pending(now):
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
    clear_pending_tasks_by_commands(SMALL_WORLD_CHAIN_COMMANDS | SMALL_WORLD_GOD_COMMANDS, send_as_id=get_current_identity_id())
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
    if RE_SMALL_WORLD_RELIEF_DAMAGE.search(raw_text) or kind in {"灭世瘟疫", "王朝更迭"}:
        return "relief", f"灾害: {kind or '小世界'}，赈灾安抚"
    if "邪神" in raw_text or RE_SMALL_WORLD_FAITH_DAMAGE.search(raw_text):
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

    raw_text = text or ""
    wait_sec, wait_text = _parse_wait_from_text(raw_text)
    if wait_sec > 0 and RE_SMALL_WORLD_GOD_COOLDOWN.search(raw_text):
        _clear_preach_pending()
        _clear_god_pending_tasks()
        state["small_world_last_error"] = f"神迹冷却中: {wait_text}"
        state["small_world_god_cooldown_until"] = float(now + wait_sec + CD_BUFFER_SEC)
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
    save_state()
    return True


async def handle_small_world_query_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_query":
        return False
    if not state.get("small_world_enabled") or not _chain_enabled():
        return False

    raw_text = text or ""
    panel = _parse_small_world_panel(raw_text)
    if not panel:
        return False
    if not _is_current_query_reply(reply_to, matched_family):
        return False

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

    if _is_resource_shortage_text(raw_text):
        label = _resource_label_from_text(raw_text)
        due_at = _schedule_resource_pause(now, f"显灵/{label}", raw_text)
        save_state()
        await send_audit_log(
            f"⚠️ 小世界显灵资源不足（{label}），本轮停止，{fmt_time_after(max(0, due_at - now))} 后再查；请手动补资源。",
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
        wait_sec, _wait_text = _parse_wait_from_text(raw_text)
        _clear_chain_pending()
        state["small_world_refresh_count"] = 0
        if "显灵成功" in raw_text:
            state["small_world_last_error"] = ""
        elif "天机已散" in raw_text:
            state["small_world_last_error"] = "祈愿已超过 24 小时，天机已散"
        else:
            state["small_world_last_error"] = "显灵失败，停止本轮"
        if wait_sec > 0:
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
    if _SMALL_WORLD_SCHEDULER_LOCK.locked():
        return
    async with _SMALL_WORLD_SCHEDULER_LOCK:
        await _run_small_world_scheduler(now)


async def _run_small_world_scheduler(now):
    if not state.get("small_world_enabled"):
        return

    preach_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    preach_deadline = _get_preach_deadline()
    if preach_msg_id > 0 and preach_deadline > 0:
        if now >= preach_deadline:
            state["small_world_last_error"] = "小世界神迹回复超时"
            _clear_preach_pending()
            _clear_god_pending_tasks()
            if state.get("small_world_pending_god_action"):
                _schedule_after(now, SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC)
            save_state()
            await send_audit_log(f"⚠️ 小世界神迹回复超时，消息ID={preach_msg_id}", scope="identity")
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
        state["small_world_last_error"] = f"{phase} 等待回复超时，停止本轮"
        _clear_chain_pending()
        if phase == "manifest_pending":
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
        _clear_chain_pending()
        state["small_world_last_error"] = "神识淬炼未收到可解析回执，复查面板校准"
        await _send_query(now, "淬炼后复查")
        return

    next_time = float(state.get("next_small_world_time", 0) or 0)
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
