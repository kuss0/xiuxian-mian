import asyncio
import json
import random
import re
import time

from ..config import (
    CMD_FISHING,
    CMD_FISHING_BASKET,
    CMD_FISHING_BUY_BAIT,
    CMD_FISHING_CANCEL,
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
    get_identity_display_name,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    get_storage_bag_records,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key
from . import fishing_behavior
from .fishing import parse_open_fish_result, plan_fishing_commands
from .storage_bag import apply_storage_bag_item_counts, apply_storage_bag_item_deltas, start_storage_bag_gift_batch


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
FISHING_RESET_JITTER_MAX_SEC = 100
FISHING_MAX_ACTIVE_IDENTITIES = 2
FISHING_QUEUE_DELAY_MIN_SEC = 3
FISHING_QUEUE_DELAY_MAX_SEC = 5
FISHING_DUPLICATE_COMMAND_SUPPRESS_SEC = 65
FISHING_TRANSFER_RETRY_DELAY_SEC = 5 * 60
FISHING_VALUABLE_REMINDER_OFFSETS_SEC = (0, 3 * 3600, 6 * 3600)
FISHING_COMMON_OPEN_REWARD_ITEMS = {"灵石", "灵鱼肉", "灵鱼鳞", "清灵草", "水草"}
FISHING_VALUABLE_KEYWORDS = (
    "图纸",
    "丹方",
    "图谱",
    "功法",
    "剑诀",
    "法则",
    "残图",
    "通行令",
    "昆吾",
    "大衍诀",
    "空间节点",
    "坐标",
    "灵眼之树",
    "至宝",
    "真仙试锋",
)
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


def _format_count_map(counts):
    normalized = []
    for name, count in sorted((counts or {}).items(), key=lambda item: str(item[0])):
        name = str(name or "").strip()
        try:
            amount = int(count or 0)
        except (TypeError, ValueError):
            amount = 0
        if name and amount > 0:
            normalized.append(f"{name}x{amount}")
    return "、".join(normalized) if normalized else "无"


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


def _normalize_fishing_valuable_drop_reminders(value=None):
    raw = state.get("fishing_valuable_drop_reminders") if value is None else value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        raw = []
    reminders = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        for key in ("event_at", "next_reminder_at"):
            try:
                entry[key] = float(entry.get(key, 0) or 0)
            except (TypeError, ValueError, OverflowError):
                entry[key] = 0.0
        try:
            entry["next_index"] = max(0, min(len(FISHING_VALUABLE_REMINDER_OFFSETS_SEC), int(entry.get("next_index", 0) or 0)))
        except (TypeError, ValueError, OverflowError):
            entry["next_index"] = 0
        try:
            entry["result_msg_id"] = max(0, int(entry.get("result_msg_id", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            entry["result_msg_id"] = 0
        for key in ("event_id", "source", "item", "fish", "last_error"):
            entry[key] = str(entry.get(key) or "").strip()
        entry["done"] = bool(entry.get("done")) or entry["next_index"] >= len(FISHING_VALUABLE_REMINDER_OFFSETS_SEC)
        if entry["item"]:
            reminders.append(entry)
    return reminders[-12:]


def _clean_fishing_reward_name(value):
    name = str(value or "").strip()
    while len(name) >= 2 and ((name[0], name[-1]) in {("【", "】"), ("[", "]"), ("(", ")"), ("（", "）")}):
        name = name[1:-1].strip()
    return name


def _is_common_fishing_reward_item(name):
    normalized = _clean_fishing_reward_name(name)
    return normalized in FISHING_COMMON_OPEN_REWARD_ITEMS or normalized in {"修为", "宗门贡献"}


def _is_valuable_fishing_reward_item(name, *, companion=False):
    normalized = _clean_fishing_reward_name(name)
    if not normalized or _is_common_fishing_reward_item(normalized):
        return False
    if companion:
        return True
    return any(keyword in normalized for keyword in FISHING_VALUABLE_KEYWORDS)


def _fishing_valuable_items_from_text(raw_text, open_result=None):
    text = str(raw_text or "")
    companion = "伴生机缘" in text
    parsed = open_result or parse_open_fish_result(text)
    items = []
    if parsed:
        for item_name in (parsed.items or {}).keys():
            normalized = _clean_fishing_reward_name(item_name)
            if _is_valuable_fishing_reward_item(normalized, companion=companion):
                items.append(normalized)
    for match in re.finditer(r"【(?P<name>[^】]+)】(?:x\d+)?", text):
        normalized = _clean_fishing_reward_name(match.group("name"))
        if _is_valuable_fishing_reward_item(normalized, companion=companion):
            items.append(normalized)
    deduped = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _queue_fishing_valuable_drop_reminders(raw_text, now, *, result_msg_id=0, open_result=None):
    items = _fishing_valuable_items_from_text(raw_text, open_result=open_result)
    if not items:
        return False
    parsed = open_result or parse_open_fish_result(raw_text)
    fish = str(getattr(parsed, "fish", "") or "").strip()
    item_text = "、".join(items)
    result_msg_id = int(result_msg_id or 0)
    event_key = result_msg_id if result_msg_id > 0 else int(float(now or 0) // 60)
    event_id = f"fishing-valuable:{event_key}:{fish}:{item_text}"
    reminders = _normalize_fishing_valuable_drop_reminders()
    existing_ids = {str(item.get("event_id") or "") for item in reminders if isinstance(item, dict)}
    if event_id in existing_ids:
        return False
    reminders.append({
        "event_id": event_id,
        "source": "灵溪垂钓伴生机缘",
        "item": item_text,
        "fish": fish,
        "event_at": float(now or time.time()),
        "next_index": 0,
        "next_reminder_at": float(now or time.time()),
        "done": False,
        "result_msg_id": result_msg_id,
        "last_error": "",
    })
    state["fishing_valuable_drop_reminders"] = reminders[-12:]
    mark_dirty()
    return True


def _format_fishing_valuable_reminder(event, index):
    labels = ("即时", "+3h", "+6h")
    label = labels[index] if 0 <= int(index or 0) < len(labels) else f"第{int(index or 0) + 1}次"
    item = str((event or {}).get("item") or "").strip() or "未解析宝物"
    fish = str((event or {}).get("fish") or "").strip()
    suffix = f"｜来源 {fish}" if fish else ""
    return f"🎣 灵溪垂钓伴生机缘提醒（{label}/3）：{item}{suffix}"


async def _run_fishing_valuable_drop_reminders(now):
    reminders = _normalize_fishing_valuable_drop_reminders()
    changed = False
    sent_any = False
    for event in reminders:
        if not isinstance(event, dict) or event.get("done"):
            continue
        next_index = int(event.get("next_index", 0) or 0)
        if next_index >= len(FISHING_VALUABLE_REMINDER_OFFSETS_SEC):
            event["done"] = True
            changed = True
            continue
        due_at = float(event.get("next_reminder_at", 0) or 0)
        if due_at <= 0:
            due_at = float(event.get("event_at", now) or now) + FISHING_VALUABLE_REMINDER_OFFSETS_SEC[next_index]
            event["next_reminder_at"] = float(due_at)
            changed = True
        if float(now or 0) < due_at or sent_any:
            continue
        ok = await send_audit_log(
            _format_fishing_valuable_reminder(event, next_index),
            scope="identity",
            priority="high",
            limit=260,
        )
        sent_any = True
        changed = True
        if not ok:
            event["next_reminder_at"] = float(now + 5 * 60)
            event["last_error"] = "日志提醒发送失败，5分钟后重试"
            continue
        next_index += 1
        event["next_index"] = next_index
        event["last_error"] = ""
        if next_index >= len(FISHING_VALUABLE_REMINDER_OFFSETS_SEC):
            event["done"] = True
            event["next_reminder_at"] = 0
        else:
            event["next_reminder_at"] = float(event.get("event_at", now) or now) + FISHING_VALUABLE_REMINDER_OFFSETS_SEC[next_index]
    if changed:
        state["fishing_valuable_drop_reminders"] = reminders[-12:]
        save_state()
    return sent_any


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
        if fishing_behavior.is_new_fishing_flow_in_progress(identity_state):
            active_ids.append(identity_id)
    return active_ids


def _new_fishing_command_is_capacity_limited(command):
    return fishing_behavior.command_phase(command) in {"buying", "chumming", "fishing"}


def _defer_new_fishing_for_capacity(now, command):
    if not _new_fishing_command_is_capacity_limited(command):
        return False
    active_ids = _active_fishing_identity_ids(exclude_identity_id=get_current_identity_id())
    if len(active_ids) < FISHING_MAX_ACTIVE_IDENTITIES:
        return False
    state["next_fishing_time"] = float(now + random.uniform(FISHING_QUEUE_DELAY_MIN_SEC, FISHING_QUEUE_DELAY_MAX_SEC))
    state["fishing_last_error"] = f"钓鱼排队中：已有 {len(active_ids)} 个身份正在垂钓或准备"
    mark_dirty()
    return True


def _fishing_reset_jitter_sec(send_as_id=None):
    min_sec = max(0, int(FISHING_RESET_JITTER_MIN_SEC or 0))
    max_sec = max(min_sec, int(FISHING_RESET_JITTER_MAX_SEC or 0))
    if max_sec <= min_sec:
        return float(min_sec)
    identity_id = int(send_as_id or get_current_identity_id() or 0)
    return float(min_sec + (abs(identity_id) % (max_sec - min_sec)))


def _is_fishing_reply(reply_to=None, matched_family=None):
    if matched_family == "fishing":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return orig_cmd in {
        CMD_FISHING,
        CMD_FISHING_STATUS,
        CMD_FISHING_PROBE,
        CMD_FISHING_LIFT,
        CMD_FISHING_CANCEL,
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
    if raw.startswith((CMD_FISHING_PROBE, CMD_FISHING_LIFT, CMD_FISHING_CANCEL, CMD_FISHING_OPEN, CMD_FISHING_BASKET)):
        return SEND_PRIORITY_EVENT_BURST
    return None


def _reply_timeout_for_fishing_command(command):
    raw = str(command or "").strip()
    if raw.startswith((CMD_FISHING_STATUS, CMD_FISHING_PROBE)):
        return FISHING_STATUS_REPLY_TIMEOUT_SEC
    if raw.startswith(CMD_FISHING):
        return FISHING_FAST_REPLY_TIMEOUT_SEC
    if raw.startswith((CMD_FISHING_LIFT, CMD_FISHING_CANCEL)):
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
            "fishing_cancel_after_sec",
            "fishing_transfer_target_id",
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
        "fishing_transfer_due_at": 0,
        "fishing_caught_fish_json": "",
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
    transfer_target_id = _parse_int(state.get("fishing_transfer_target_id", 0))
    transfer_target = get_identity_display_name(transfer_target_id) if transfer_target_id in get_identity_ids() else "关"
    transfer_items = fishing_behavior.pending_fishing_transfer_items(snapshot)
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
        f"- 卡竿收竿：{int(state.get('fishing_cancel_after_sec', 120) or 0)}秒",
        f"- 鱼获赠送：{transfer_target}",
        f"- 待赠鱼获：{_format_count_map(transfer_items)}",
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


async def _run_pending_fishing_transfer(now):
    snapshot = _state_snapshot()
    if not snapshot.get("fishing_enabled"):
        return False
    transfer_items = fishing_behavior.pending_fishing_transfer_items(snapshot)
    if not transfer_items:
        return False
    due_at = float(snapshot.get("fishing_transfer_due_at", 0) or 0)
    if due_at > float(now or 0):
        return False
    if fishing_behavior.is_rod_in_progress(snapshot):
        return False
    if (
        _parse_int(snapshot.get("fishing_reply_to_msg_id", 0)) > 0
        and float(snapshot.get("fishing_reply_due_at", 0) or 0) > float(now or 0)
    ):
        return False

    source_id = int(get_current_identity_id() or 0)
    target_id = _parse_int(snapshot.get("fishing_transfer_target_id", 0))
    known_ids = {int(identity_id) for identity_id in get_identity_ids()}
    item_text = _format_count_map(transfer_items)
    if source_id <= 0 or target_id <= 0 or source_id == target_id or target_id not in known_ids:
        state["fishing_transfer_due_at"] = float(now + FISHING_TRANSFER_RETRY_DELAY_SEC)
        state["fishing_last_error"] = f"鱼获赠送目标无效，保留待赠鱼获：{item_text}"
        save_state()
        await send_audit_log(
            f"⚠️ 灵溪垂钓鱼获赠送目标无效，已保留队列：{item_text}",
            scope="identity",
            limit=220,
        )
        return True

    gift_items = [
        {"item_name": fish, "quantity": count, "method": "gift"}
        for fish, count in sorted(transfer_items.items())
        if str(fish or "").strip() and int(count or 0) > 0
    ]
    if not gift_items:
        state["fishing_transfer_due_at"] = 0
        state["fishing_caught_fish_json"] = ""
        mark_dirty()
        return False

    try:
        ok, message, _transfer = await start_storage_bag_gift_batch(
            [{
                "source_identity_id": source_id,
                "target_identity_id": target_id,
                "items": gift_items,
            }],
            target_identity_id=target_id,
            stop_on_error=True,
        )
    except Exception as exc:
        ok = False
        message = str(exc)

    target_label = get_identity_display_name(target_id)
    if ok:
        state["fishing_caught_fish_json"] = ""
        state["fishing_transfer_due_at"] = 0
        state["fishing_last_result"] = f"鱼获赠送已入队：{item_text} -> {target_label}"
        state["fishing_last_error"] = ""
        save_state()
        await send_audit_log(
            f"🎣 灵溪垂钓鱼获已加入储物袋赠送队列：{item_text} -> {target_label}",
            scope="identity",
            limit=240,
        )
        return True

    state["fishing_transfer_due_at"] = float(now + FISHING_TRANSFER_RETRY_DELAY_SEC)
    state["fishing_last_error"] = f"鱼获赠送入队失败：{message or '未知错误'}"
    save_state()
    await send_audit_log(
        f"⚠️ 灵溪垂钓鱼获赠送入队失败，5分钟后重试：{message or '未知错误'}",
        scope="identity",
        limit=240,
    )
    return True


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
            or parse_open_fish_result(raw_text)
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
        if _queue_fishing_valuable_drop_reminders(
            raw_text,
            now,
            result_msg_id=int(result_msg_id or _parse_int(getattr(reply_to, "id", 0)) or 0),
        ):
            save_state()
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
    if _queue_fishing_valuable_drop_reminders(raw_text, now, result_msg_id=result_msg_id):
        save_state()
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
        and _parse_int(state.get("fishing_reply_to_msg_id", 0)) > 0
        and float(state.get("fishing_reply_due_at", 0) or 0) > float(now)
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
    if await _run_fishing_valuable_drop_reminders(now):
        return

    if await _run_pending_fishing_transfer(now):
        return

    effect = fishing_behavior.decide_scheduler(
        _state_snapshot(),
        now,
        bait_inventory=_get_bait_inventory_from_storage(),
        next_day_jitter_sec=_fishing_reset_jitter_sec(),
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
