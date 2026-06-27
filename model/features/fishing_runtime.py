import asyncio
import random
import time

from ..config import (
    CMD_FISHING,
    CMD_FISHING_BASKET,
    CMD_FISHING_BUY_BAIT,
    CMD_FISHING_CHUM,
    CMD_FISHING_LIFT,
    CMD_FISHING_OPEN,
    CMD_FISHING_PROBE,
    CMD_FISHING_STATUS,
    TZ_LOCAL,
)
from ..persistence import mark_dirty, save_state
from ..runtime import (
    SEND_PRIORITY_EVENT_BURST,
    SEND_PRIORITY_URGENT_REACTIVE,
    console_log,
    send_audit_log,
    send_game_command,
)
from ..state import (
    get_current_identity_id,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    get_storage_bag_records,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key
from . import fishing_behavior
from .fishing import plan_fishing_commands
from .storage_bag import apply_storage_bag_item_counts, apply_storage_bag_item_deltas


FISHING_REPLY_TIMEOUT_SEC = 90
FISHING_FAST_REPLY_TIMEOUT_SEC = 14
FISHING_STATUS_REPLY_TIMEOUT_SEC = 5
FISHING_ACTION_REPLY_TIMEOUT_SEC = 20
FISHING_SETUP_REPLY_TIMEOUT_SEC = 20
FISHING_ACTION_DELAY_MIN_SEC = 5
FISHING_ACTION_DELAY_MAX_SEC = 12
FISHING_RECOVERY_MIN_SEC = 15
FISHING_RECOVERY_MAX_SEC = 45
FISHING_POST_ROD_DELAY_MIN_SEC = 3
FISHING_POST_ROD_DELAY_MAX_SEC = 5
FISHING_RESET_JITTER_MIN_SEC = 0
FISHING_RESET_JITTER_MAX_SEC = 0
FISHING_MAX_ACTIVE_IDENTITIES = 2
FISHING_QUEUE_DELAY_MIN_SEC = 3
FISHING_QUEUE_DELAY_MAX_SEC = 5
FISHING_DUPLICATE_COMMAND_SUPPRESS_SEC = 65
_FOLLOWUP_TASKS = {}
_RECOVERY_TASKS = {}
_SEND_LOCKS = {}
_RECENT_COMMANDS = {}


def _parse_int(value, default=0):
    try:
        return int(str(value or default).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _state_snapshot():
    return dict(state.items())


def _apply_updates(updates):
    for key, value in (updates or {}).items():
        state[key] = value


def _apply_effect(effect, *, persist=True):
    if not effect or not effect.handled:
        return False
    _apply_updates(effect.updates)
    if effect.storage_deltas:
        apply_storage_bag_item_deltas(get_current_identity_id(), dict(effect.storage_deltas))
    if effect.storage_counts:
        apply_storage_bag_item_counts(get_current_identity_id(), dict(effect.storage_counts))
    if persist:
        save_state()
    elif effect.updates:
        mark_dirty()
    return True


def _get_bait_inventory_from_storage(send_as_id=None):
    send_as_id = int(send_as_id or get_current_identity_id() or 0)
    records = get_storage_bag_records()
    record = records.get(str(send_as_id)) if isinstance(records, dict) else None
    if not isinstance(record, dict):
        return None
    items = record.get("items")
    if not isinstance(items, dict):
        return None
    return {str(name): _parse_int(count) for name, count in items.items() if str(name or "").strip()}


def _active_fishing_identity_ids(exclude_identity_id=None):
    excluded = int(exclude_identity_id or 0)
    active_ids = []
    for identity_id in get_identity_ids():
        identity_id = int(identity_id or 0)
        if identity_id <= 0 or identity_id == excluded:
            continue
        if not get_identity_enabled(identity_id):
            continue
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        if fishing_behavior.is_rod_in_progress(identity_state):
            active_ids.append(identity_id)
    return active_ids


def _new_fishing_command_is_capacity_limited(command):
    return fishing_behavior.command_phase(command) == "fishing"


def _defer_new_fishing_for_capacity(now, command):
    if not _new_fishing_command_is_capacity_limited(command):
        return False
    active_ids = _active_fishing_identity_ids(exclude_identity_id=get_current_identity_id())
    if len(active_ids) < FISHING_MAX_ACTIVE_IDENTITIES:
        return False
    state["next_fishing_time"] = float(now + random.uniform(FISHING_QUEUE_DELAY_MIN_SEC, FISHING_QUEUE_DELAY_MAX_SEC))
    state["fishing_last_error"] = f"钓鱼排队中：已有 {len(active_ids)} 个身份正在垂钓"
    mark_dirty()
    return True


def _is_fishing_reply(reply_to=None, matched_family=None):
    if matched_family == "fishing":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return orig_cmd in {
        CMD_FISHING,
        CMD_FISHING_STATUS,
        CMD_FISHING_PROBE,
        CMD_FISHING_LIFT,
        CMD_FISHING_BASKET,
    } or orig_cmd.startswith((
        f"{CMD_FISHING} ",
        f"{CMD_FISHING_BUY_BAIT} ",
        f"{CMD_FISHING_CHUM} ",
        f"{CMD_FISHING_OPEN} ",
    ))


def _normalize_username(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not raw.startswith("@"):
        raw = f"@{raw}"
    return raw.lower()


def _current_identity_username():
    profile = get_send_as_profile(get_current_identity_id())
    return _normalize_username((profile or {}).get("username") or "")


def _explicit_fishing_angler(text):
    status = fishing_behavior.parse_fishing_status(text)
    if status:
        return _normalize_username(status.angler)
    catch = fishing_behavior.parse_fishing_catch(text)
    if catch:
        return _normalize_username(catch.angler)
    return ""


def _is_open_fish_reply_to_command(reply_to=None):
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return orig_cmd.startswith(f"{CMD_FISHING_OPEN} ")


def _priority_for_fishing_command(command):
    raw = str(command or "").strip()
    if raw.startswith(CMD_FISHING_STATUS):
        return SEND_PRIORITY_URGENT_REACTIVE
    if raw.startswith((CMD_FISHING_PROBE, CMD_FISHING_LIFT, CMD_FISHING_OPEN, CMD_FISHING_BASKET)):
        return SEND_PRIORITY_EVENT_BURST
    return None


def _reply_timeout_for_fishing_command(command):
    raw = str(command or "").strip()
    if raw.startswith((CMD_FISHING_STATUS, CMD_FISHING_PROBE)):
        return FISHING_STATUS_REPLY_TIMEOUT_SEC
    if raw.startswith(CMD_FISHING):
        return FISHING_FAST_REPLY_TIMEOUT_SEC
    if raw.startswith(CMD_FISHING_LIFT):
        return FISHING_ACTION_REPLY_TIMEOUT_SEC
    if raw.startswith((CMD_FISHING_BUY_BAIT, CMD_FISHING_CHUM)):
        return FISHING_SETUP_REPLY_TIMEOUT_SEC
    return FISHING_REPLY_TIMEOUT_SEC


def _fishing_followup_key(send_as_id):
    return int(send_as_id or 0)


def _cancel_fishing_followup(send_as_id):
    task = _FOLLOWUP_TASKS.pop(_fishing_followup_key(send_as_id), None)
    if task and not task.done():
        task.cancel()


def _cancel_fishing_recovery(send_as_id):
    task = _RECOVERY_TASKS.pop(_fishing_followup_key(send_as_id), None)
    if task and not task.done():
        task.cancel()


def _has_fishing_followup(send_as_id):
    task = _FOLLOWUP_TASKS.get(_fishing_followup_key(send_as_id))
    return bool(task and not task.done())


def _fishing_send_lock(send_as_id):
    key = _fishing_followup_key(send_as_id)
    lock = _SEND_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SEND_LOCKS[key] = lock
    return lock


def _recent_fishing_command_blocks(command, now):
    recent = _RECENT_COMMANDS.get(_fishing_followup_key(get_current_identity_id())) or {}
    if str(recent.get("command") or "").strip() != str(command or "").strip():
        return False
    sent_at = float(recent.get("sent_at") or 0)
    return sent_at > 0 and float(now or 0) - sent_at < FISHING_DUPLICATE_COMMAND_SUPPRESS_SEC


def _remember_fishing_command(command, sent_at, msg_id):
    _RECENT_COMMANDS[_fishing_followup_key(get_current_identity_id())] = {
        "command": str(command or "").strip(),
        "sent_at": float(sent_at or time.time()),
        "msg_id": int(msg_id or 0),
    }


def _schedule_fishing_followup(send_as_id, command, due_at):
    command = str(command or "").strip()
    if not command:
        return False
    send_as_id = int(send_as_id or get_current_identity_id() or 0)
    due_at = float(due_at or 0)
    if send_as_id <= 0 or due_at <= 0:
        return False
    key = _fishing_followup_key(send_as_id)
    _cancel_fishing_followup(send_as_id)
    task = asyncio.create_task(_run_fishing_followup(send_as_id, command, due_at))
    _FOLLOWUP_TASKS[key] = task

    def _done(done_task):
        if _FOLLOWUP_TASKS.get(key) is done_task:
            _FOLLOWUP_TASKS.pop(key, None)
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            console_log(f"⚠️ 灵溪垂钓短链任务异常：{str(exc)[:120]}", scope="identity", limit=180)

    task.add_done_callback(_done)
    return True


def _schedule_fishing_recovery(send_as_id, msg_id, due_at):
    send_as_id = int(send_as_id or get_current_identity_id() or 0)
    msg_id = int(msg_id or 0)
    due_at = float(due_at or 0)
    if send_as_id <= 0 or msg_id <= 0 or due_at <= 0:
        return False
    key = _fishing_followup_key(send_as_id)
    _cancel_fishing_recovery(send_as_id)
    task = asyncio.create_task(_run_fishing_recovery(send_as_id, msg_id, due_at))
    _RECOVERY_TASKS[key] = task

    def _done(done_task):
        if _RECOVERY_TASKS.get(key) is done_task:
            _RECOVERY_TASKS.pop(key, None)
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            console_log(f"⚠️ 灵溪垂钓恢复任务异常：{str(exc)[:120]}", scope="identity", limit=180)

    task.add_done_callback(_done)
    return True


async def _run_fishing_followup(send_as_id, command, due_at):
    wait_sec = max(0.0, float(due_at or 0) - time.time())
    if wait_sec > 0:
        await asyncio.sleep(wait_sec)
    with use_identity(send_as_id):
        if not state.get("fishing_enabled"):
            return
        if str(state.get("fishing_pending_action") or "").strip() != command:
            return
        if _parse_int(state.get("fishing_reply_to_msg_id", 0)) > 0 and float(state.get("fishing_reply_due_at", 0) or 0) > time.time():
            return
        await _send_fishing_command(command, time.time())


async def _run_fishing_recovery(send_as_id, msg_id, due_at):
    wait_sec = max(0.0, float(due_at or 0) - time.time())
    if wait_sec > 0:
        await asyncio.sleep(wait_sec)
    with use_identity(send_as_id):
        if not state.get("fishing_enabled"):
            return
        if _parse_int(state.get("fishing_reply_to_msg_id", 0)) != int(msg_id or 0):
            return
        if float(state.get("fishing_reply_due_at", 0) or 0) > time.time():
            return
        await run_fishing_scheduler(time.time())


def is_fishing_reply_text(text):
    return fishing_behavior.is_fishing_reply_text(text)


def _current_fishing_config():
    return fishing_behavior.current_fishing_config(_state_snapshot())


def _active_chum_plan_kwargs():
    return fishing_behavior.active_chum_plan_kwargs(_state_snapshot())


def clear_fishing_state(*, persist=False, keep_last_error=False, keep_config=True):
    _cancel_fishing_followup(get_current_identity_id())
    _cancel_fishing_recovery(get_current_identity_id())
    last_error = state.get("fishing_last_error") if keep_last_error else ""
    config_values = {}
    if keep_config:
        for key in (
            "fishing_pond",
            "fishing_bait",
            "fishing_daily_limit",
            "fishing_auto_chum_enabled",
            "fishing_chum_name",
            "fishing_chum_names",
            "fishing_auto_buy_bait_enabled",
            "fishing_auto_buy_bait_count",
            "fishing_auto_probe_enabled",
            "fishing_auto_open_fish_enabled",
        ):
            config_values[key] = state.get(key)
    updates = {
        "next_fishing_time": 0,
        **fishing_behavior.clear_pending_updates(keep_open_fish=False),
        "fishing_forced_buy_bait": "",
        "fishing_forced_buy_count": 0,
        "fishing_started_at": 0,
        "fishing_active_chum_name": "",
        "fishing_chum_rods_remaining": 0,
        "fishing_chum_day": "",
        "fishing_chum_counts": "",
        "fishing_last_msg_id": 0,
        "fishing_last_result": "",
        "fishing_last_error": last_error or "",
        **config_values,
    }
    _apply_updates(updates)
    if persist:
        save_state()
    else:
        mark_dirty()


def get_fishing_status_text():
    snapshot = _state_snapshot()
    config = fishing_behavior.current_fishing_config(snapshot)
    _day_key, daily_count, daily_limit, daily_updates = fishing_behavior.normalize_daily_counter(snapshot, time.time())
    if daily_updates:
        _apply_updates(daily_updates)
        mark_dirty()
        snapshot = _state_snapshot()
    plan = plan_fishing_commands(
        config,
        bait_inventory=_get_bait_inventory_from_storage(),
        chum_usage_counts=fishing_behavior.parse_chum_usage_counts(snapshot.get("fishing_chum_counts")),
        **fishing_behavior.active_chum_plan_kwargs(snapshot),
    )
    plan_summary = " -> ".join(plan.commands or ()) if plan.commands else (plan.blocked_reason or "未生成")
    active_chum = state.get("fishing_active_chum_name") or "无"
    configured_chums = ",".join(config.chum_names or ()) or "无"
    chum_rods = _parse_int(state.get("fishing_chum_rods_remaining", 0))
    lines = [
        "🎣 灵溪垂钓",
        f"- 已启用：{'是' if state.get('fishing_enabled') else '否'}",
        f"- 鱼塘/鱼饵：{config.pond}/{config.bait}",
        f"- 今日竿数：{daily_count}/{daily_limit}",
        f"- 自动打窝：{configured_chums}",
        f"- 当前窝料：{active_chum}（剩余 {chum_rods} 竿）",
        f"- 缺饵购买：{'开' if config.auto_buy_bait_enabled else '关'}",
        f"- 试饵：{'开' if config.auto_probe_enabled else '关'}",
        f"- 自动开鱼：{'开' if state.get('fishing_auto_open_fish_enabled') else '关'}",
        f"- 阶段：{state.get('fishing_phase') or 'idle'}",
        f"- 下次动作：{fmt_abs_ts(state.get('next_fishing_time', 0))}（{fmt_remaining(state.get('next_fishing_time', 0))}）",
        f"- 待回复命令ID：{int(state.get('fishing_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('fishing_reply_due_at', 0))}（{fmt_remaining(state.get('fishing_reply_due_at', 0))}）",
        f"- 待动作：{state.get('fishing_pending_action') or '无'}",
        f"- 待开鱼：{state.get('fishing_pending_open_fish') or '无'}",
        f"- 计划：{plan_summary}",
        f"- 最近结果：{state.get('fishing_last_result') or '无'}",
    ]
    if state.get("fishing_last_error"):
        lines.append(f"- 最近异常：{state['fishing_last_error']}")
    return "\n".join(lines)


async def _emit_effect_audits(effect, *, limit=180):
    for message in effect.audit_messages or ():
        await send_audit_log(message, scope="identity", limit=limit)


async def _run_immediate_fishing_commands(commands):
    ran = False
    for command in commands or ():
        command = str(command or "").strip()
        if not command:
            continue
        await _send_fishing_command(command, time.time())
        ran = True
    return ran


def _maybe_schedule_pending_fishing_action():
    command = str(state.get("fishing_pending_action") or "").strip()
    due_at = float(state.get("next_fishing_time", 0) or 0)
    if not command or due_at <= 0:
        return False
    return _schedule_fishing_followup(get_current_identity_id(), command, due_at)


async def handle_fishing_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0):
    raw_text = str(text or "").strip()
    is_routed_fishing = _is_fishing_reply(reply_to, matched_family=matched_family)
    if not state.get("fishing_enabled"):
        # Manual bait/chum/basket commands should still keep the local bag mirror fresh
        # when their replies are routed by reply_to context.
        if not is_routed_fishing:
            return False
        if not (
            fishing_behavior.parse_buy_bait_result(raw_text)
            or fishing_behavior.parse_fishing_basket(raw_text)
            or fishing_behavior.parse_chum_success_detail(raw_text)
            or fishing_behavior.parse_chum_duplicate_active_reply(raw_text)
            or fishing_behavior.parse_chum_daily_limit_reply(raw_text)
            or fishing_behavior.parse_generic_resource_shortage(raw_text)
            or fishing_behavior.parse_chum_shortage(raw_text)
        ):
            return False
        effect = fishing_behavior.decide_reply(
            _state_snapshot(),
            raw_text,
            now,
            result_msg_id=int(result_msg_id or _parse_int(getattr(reply_to, "id", 0)) or 0),
            action_delay_sec=random.uniform(FISHING_ACTION_DELAY_MIN_SEC, FISHING_ACTION_DELAY_MAX_SEC),
            post_rod_delay_sec=random.uniform(FISHING_POST_ROD_DELAY_MIN_SEC, FISHING_POST_ROD_DELAY_MAX_SEC),
        )
        if not effect.handled:
            return False
        _apply_effect(effect)
        _cancel_fishing_followup(get_current_identity_id())
        await _emit_effect_audits(effect)
        return True

    snapshot = _state_snapshot()
    looks_like_fishing = fishing_behavior.is_fishing_reply_text(raw_text)
    active_pending = (
        _parse_int(snapshot.get("fishing_reply_to_msg_id", 0)) > 0
        and float(snapshot.get("fishing_reply_due_at", 0) or 0) >= float(now)
    )
    if not is_routed_fishing and not (looks_like_fishing and active_pending):
        return False

    reply_to_msg_id = _parse_int(getattr(reply_to, "id", 0))
    active_ids = fishing_behavior.active_fishing_anchor_ids(snapshot)
    swallowed_reply = reply_to_msg_id <= 0 and looks_like_fishing and active_pending
    explicit_angler = _explicit_fishing_angler(raw_text)
    current_username = _current_identity_username()
    explicit_angler_matches = bool(explicit_angler and current_username and explicit_angler == current_username)
    if explicit_angler and not explicit_angler_matches:
        return False
    allow_routed_by_angler = matched_family == "fishing" and looks_like_fishing and explicit_angler_matches
    allow_open_reply = matched_family == "fishing" and _is_open_fish_reply_to_command(reply_to)
    allow_basket_reply = matched_family == "fishing" and fishing_behavior.parse_fishing_basket(raw_text)
    if active_ids and reply_to_msg_id not in active_ids and not swallowed_reply and not allow_routed_by_angler and not allow_open_reply and not allow_basket_reply:
        return False

    result_msg_id = int(result_msg_id or reply_to_msg_id or 0)
    effect = fishing_behavior.decide_reply(
        snapshot,
        raw_text,
        now,
        result_msg_id=result_msg_id,
        action_delay_sec=random.uniform(FISHING_ACTION_DELAY_MIN_SEC, FISHING_ACTION_DELAY_MAX_SEC),
        post_rod_delay_sec=random.uniform(FISHING_POST_ROD_DELAY_MIN_SEC, FISHING_POST_ROD_DELAY_MAX_SEC),
    )
    if not effect.handled:
        return False

    _apply_effect(effect)
    _cancel_fishing_followup(get_current_identity_id())
    _cancel_fishing_recovery(get_current_identity_id())
    await _emit_effect_audits(effect)
    if effect.immediate_commands:
        await _run_immediate_fishing_commands(effect.immediate_commands)
    else:
        _maybe_schedule_pending_fishing_action()
    return True


async def _send_fishing_command(command, now):
    lock = _fishing_send_lock(get_current_identity_id())
    if lock.locked():
        state["fishing_last_error"] = f"发送中重复指令已抑制：{command}"
        mark_dirty()
        return False
    async with lock:
        return await _send_fishing_command_locked(command, now)


async def _send_fishing_command_locked(command, now):
    phase = fishing_behavior.command_phase(command)
    if _recent_fishing_command_blocks(command, now):
        state["fishing_last_error"] = f"短窗重复指令已抑制：{command}"
        mark_dirty()
        return False
    if (
        phase != "idle"
        and str(state.get("fishing_phase") or "").strip() == phase
        and (
            (
                _parse_int(state.get("fishing_reply_to_msg_id", 0)) > 0
                and float(state.get("fishing_reply_due_at", 0) or 0) > float(now)
            )
            or float(state.get("next_fishing_time", 0) or 0) > float(now)
        )
    ):
        return False
    priority = _priority_for_fishing_command(command)
    send_kwargs = {
        "track": False,
        "max_retry": 0,
        "source_module": "灵溪垂钓",
    }
    if priority:
        send_kwargs["priority"] = priority
    if phase != "idle":
        _apply_updates({
            "fishing_phase": phase,
            "fishing_pending_action": "",
            "next_fishing_time": float(now + _reply_timeout_for_fishing_command(command)),
        })
        mark_dirty()
    msg = await send_game_command(command, **send_kwargs)
    if not msg:
        effect = fishing_behavior.build_send_failure_effect(command, now)
        _apply_effect(effect)
        await _emit_effect_audits(effect)
        return False

    sent_at = float(getattr(msg, "sent_at", 0) or time.time())
    msg_id = int(getattr(msg, "id", 0) or 0)
    _remember_fishing_command(command, sent_at, msg_id)
    effect = fishing_behavior.build_send_success_effect(
        _state_snapshot(),
        command,
        sent_at=sent_at,
        msg_id=msg_id,
        reply_timeout_sec=_reply_timeout_for_fishing_command(command),
    )
    _apply_effect(effect)
    if int(state.get("fishing_reply_to_msg_id", 0) or 0) == msg_id and float(state.get("fishing_reply_due_at", 0) or 0) > 0:
        _schedule_fishing_recovery(get_current_identity_id(), msg_id, state.get("fishing_reply_due_at", 0))
    console_log(
        f"🎣 灵溪垂钓已发送：{command}，等待回复→{fmt_abs_ts(state['fishing_reply_due_at'])}",
        scope="identity",
        limit=180,
    )
    return True


async def run_fishing_scheduler(now):
    effect = fishing_behavior.decide_scheduler(
        _state_snapshot(),
        now,
        bait_inventory=_get_bait_inventory_from_storage(),
        next_day_jitter_sec=random.uniform(FISHING_RESET_JITTER_MIN_SEC, FISHING_RESET_JITTER_MAX_SEC),
    )
    if not effect.handled:
        return

    if effect.command:
        if _defer_new_fishing_for_capacity(now, effect.command):
            return
        if _priority_for_fishing_command(effect.command) and _has_fishing_followup(get_current_identity_id()):
            return
        _apply_effect(effect, persist=False)
        if str(effect.command or "").strip():
            _cancel_fishing_followup(get_current_identity_id())
        await _send_fishing_command(effect.command, now)
        return

    _apply_effect(effect)
    await _emit_effect_audits(effect, limit=220)


def schedule_fishing_initial_check(now, *, persist=False, keep_last_error=True):
    last_error = state.get("fishing_last_error") if keep_last_error else ""
    reply_to_msg_id = _parse_int(state.get("fishing_reply_to_msg_id", 0))
    if reply_to_msg_id > 0:
        reply_due_at = float(state.get("fishing_reply_due_at", 0) or 0)
        state["fishing_last_error"] = last_error or ""
        state["next_fishing_time"] = float(reply_due_at if reply_due_at > now else now)
        if persist:
            save_state()
        else:
            mark_dirty()
        return state["next_fishing_time"]
    updates = fishing_behavior.clear_pending_updates()
    updates["fishing_last_error"] = last_error or ""
    updates["next_fishing_time"] = float(now + random.uniform(FISHING_RECOVERY_MIN_SEC, FISHING_RECOVERY_MAX_SEC))
    _apply_updates(updates)
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_fishing_time"]


__all__ = [
    "clear_fishing_state",
    "get_fishing_status_text",
    "get_day_key",
    "is_fishing_reply_text",
    "handle_fishing_reply",
    "run_fishing_scheduler",
    "schedule_fishing_initial_check",
    "TZ_LOCAL",
]
