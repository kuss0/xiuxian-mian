"""Pure state-machine decisions for the fishing module.

This module intentionally has no runtime, persistence, or global state imports.
It turns snapshots and real bot text into declarative effects; the runtime
adapter applies those effects and owns sending/persistence.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import re

from ..config import (
    CMD_FISHING,
    CMD_FISHING_BASKET,
    CMD_FISHING_BUY_BAIT,
    CMD_FISHING_CHUM,
    CMD_FISHING_LIFT,
    CMD_FISHING_OPEN,
    CMD_FISHING_PROBE,
    CMD_FISHING_STATUS,
    RETRY_MAX_SEC,
    TZ_LOCAL,
)
from ..timing import cd_blocks, get_day_key
from .fishing import (
    FISHING_BAITS,
    FISHING_CHUM_DAILY_LIMITS,
    FISHING_DEFAULT_BUY_BAIT_COUNT,
    FISHING_DEFAULT_DAILY_LIMIT,
    FISHING_BAIT_COSTS,
    FISHING_MAX_DAILY_LIMIT,
    clamp_fishing_buy_bait_count,
    clamp_fishing_daily_limit,
    fishing_bait_name_for_item_key,
    get_known_chum_cost,
    normalize_fishing_config,
    parse_buy_bait_result,
    parse_generic_resource_shortage,
    parse_chum_daily_limit_reply,
    parse_chum_duplicate_active_reply,
    parse_chum_shortage,
    parse_chum_success_detail,
    parse_empty_fishing_result,
    parse_fishing_catch,
    parse_fishing_basket,
    parse_fishing_daily_limit_reached,
    parse_fishing_in_progress_reply,
    parse_fishing_status,
    parse_missing_bait_reply,
    parse_no_active_fishing_reply,
    parse_no_fish_reply,
    parse_no_rod_reply,
    parse_open_fish_result,
    plan_fishing_commands,
)


FISHING_NO_ROD_RETRY_SEC = 6 * 3600
FISHING_BLOCKED_RETRY_SEC = 3600
FISHING_RESOURCE_SHORTAGE_RETRY_SEC = 6 * 3600
FISHING_ACTION_DEADLINE_BUFFER_SEC = 4


@dataclass(frozen=True)
class FishingEffect:
    handled: bool = False
    command: str = ""
    immediate_commands: tuple = ()
    updates: dict = field(default_factory=dict)
    storage_deltas: dict = field(default_factory=dict)
    storage_counts: dict = field(default_factory=dict)
    audit_messages: tuple = ()


def _parse_int(value, default=0):
    try:
        return int(str(value or default).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _with_next_delay(updates, now, delay_sec):
    out = dict(updates or {})
    out["next_fishing_time"] = float(now + max(1, float(delay_sec or 0)))
    return out


def _bounded_action_delay(action_delay_sec, lift_seconds=None):
    delay = max(1, float(action_delay_sec or 0))
    if lift_seconds is not None:
        latest = max(1, int(lift_seconds or 0) - FISHING_ACTION_DEADLINE_BUFFER_SEC)
        delay = min(delay, latest)
    return max(1, delay)


def _urgent_action_tuple(*commands):
    return tuple(str(command or "").strip() for command in commands if str(command or "").strip())


def parse_pending_open_fish(value):
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {raw: 1}
    if isinstance(parsed, dict):
        out = {}
        for fish, count in parsed.items():
            name = str(fish or "").strip()
            if not name:
                continue
            try:
                parsed_count = int(count or 0)
            except (TypeError, ValueError):
                parsed_count = 0
            if parsed_count > 0:
                out[name] = out.get(name, 0) + parsed_count
        return out
    if isinstance(parsed, list):
        out = {}
        for fish in parsed:
            name = str(fish or "").strip()
            if name:
                out[name] = out.get(name, 0) + 1
        return out
    return {}


def parse_chum_usage_counts(value):
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out = {}
    for chum, count in parsed.items():
        name = str(chum or "").strip()
        if not name:
            continue
        try:
            parsed_count = int(count or 0)
        except (TypeError, ValueError):
            parsed_count = 0
        if parsed_count > 0:
            out[name] = parsed_count
    return out


def format_chum_usage_counts(counts):
    cleaned = {
        str(chum).strip(): int(count)
        for chum, count in (counts or {}).items()
        if str(chum or "").strip() and int(count or 0) > 0
    }
    if not cleaned:
        return ""
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True)


def normalize_chum_counter(snapshot, now):
    day_key = get_day_key(now)
    if str(snapshot.get("fishing_chum_day") or "") != day_key:
        return {}, {"fishing_chum_day": day_key, "fishing_chum_counts": ""}
    return parse_chum_usage_counts(snapshot.get("fishing_chum_counts")), {}


def mark_chum_confirmed(snapshot, now, chum_name):
    counts, updates = normalize_chum_counter(snapshot, now)
    name = str(chum_name or "").strip()
    if name:
        counts[name] = int(counts.get(name, 0) or 0) + 1
    updates["fishing_chum_counts"] = format_chum_usage_counts(counts)
    return updates


def mark_chum_daily_limit_reached(snapshot, now, chum_name):
    counts, updates = normalize_chum_counter(snapshot, now)
    name = str(chum_name or "").strip()
    limit = FISHING_CHUM_DAILY_LIMITS.get(name)
    if name and limit:
        counts[name] = max(int(counts.get(name, 0) or 0), int(limit))
    updates["fishing_chum_counts"] = format_chum_usage_counts(counts)
    return updates


def format_pending_open_fish(queue):
    cleaned = {
        str(fish).strip(): int(count)
        for fish, count in (queue or {}).items()
        if str(fish or "").strip() and int(count or 0) > 0
    }
    if not cleaned:
        return ""
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True)


def _parse_basket_chum(current_chum):
    raw = str(current_chum or "").strip()
    if not raw or raw == "无":
        return "", 0
    match = re.search(r"(?P<name>[^（(]+)[（(]\s*剩余\s*(?P<rods>\d+)\s*竿\s*[）)]", raw)
    if match:
        return match.group("name").strip(), max(0, int(match.group("rods") or 0))
    return raw, 0


def _basket_storage_counts(basket):
    counts = {bait: int((basket.baits or {}).get(bait, 0) or 0) for bait in FISHING_BAITS}
    for fish, count in (basket.fish or {}).items():
        name = str(fish or "").strip()
        if name:
            counts[name] = max(0, int(count or 0))
    return counts


def add_pending_open_fish(value, fish, count=1):
    queue = parse_pending_open_fish(value)
    name = str(fish or "").strip()
    if name:
        queue[name] = queue.get(name, 0) + max(1, int(count or 1))
    return format_pending_open_fish(queue)


def remove_pending_open_fish(value, fish, count=1):
    queue = parse_pending_open_fish(value)
    name = str(fish or "").strip()
    if name in queue:
        queue[name] = max(0, int(queue.get(name, 0) or 0) - max(1, int(count or 1)))
        if queue[name] <= 0:
            queue.pop(name, None)
    return format_pending_open_fish(queue)


def last_sent_chum_name(snapshot):
    raw = str(snapshot.get("fishing_last_result") or "").strip()
    prefix = f"已发送：{CMD_FISHING_CHUM} "
    if not raw.startswith(prefix):
        return ""
    name = raw[len(prefix):].strip()
    return name if name in FISHING_CHUM_DAILY_LIMITS else ""


def next_pending_open_command(value):
    queue = parse_pending_open_fish(value)
    if not queue:
        return ""
    fish = sorted(queue)[0]
    count = int(queue.get(fish, 0) or 0)
    if count <= 0:
        return ""
    if count > 1:
        return f"{CMD_FISHING_OPEN} {fish} {count}"
    return f"{CMD_FISHING_OPEN} {fish}"


def next_fishing_day_timestamp(now, jitter_sec=0):
    local_now = datetime.fromtimestamp(float(now), TZ_LOCAL)
    next_day = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return float(next_day.timestamp() + max(0, float(jitter_sec or 0)))


def clear_pending_updates(*, keep_open_fish=True):
    updates = {
        "fishing_reply_to_msg_id": 0,
        "fishing_reply_due_at": 0,
        "fishing_status_msg_id": 0,
        "fishing_pending_action": "",
        "fishing_phase": "idle",
    }
    if not keep_open_fish:
        updates["fishing_pending_open_fish"] = ""
    return updates


def active_fishing_anchor_ids(snapshot):
    return {
        msg_id
        for msg_id in (
            _parse_int(snapshot.get("fishing_reply_to_msg_id", 0)),
            _parse_int(snapshot.get("fishing_status_msg_id", 0)),
            _parse_int(snapshot.get("fishing_last_msg_id", 0)),
        )
        if msg_id > 0
    }


def _apply_item_cost_deltas(deltas, item_costs, multiplier=1):
    multiplier = max(0, int(multiplier or 0))
    for item_name, count in item_costs or ():
        name = str(item_name or "").strip()
        if name:
            deltas[name] = deltas.get(name, 0) - int(count or 0) * multiplier
    return deltas


def is_fishing_reply_text(text):
    raw = str(text or "").strip()
    return (
        raw.startswith("【灵溪垂钓】")
        or raw.startswith("灵溪垂钓】")
        or raw.startswith("【提竿成功】")
        or raw.startswith("【空竿】")
        or raw.startswith("【剖鱼取机缘】")
        or parse_open_fish_result(raw) is not None
        or raw.startswith("【渔具铺】")
        or raw.startswith("【打窝已成】")
        or raw.startswith("【鱼篓】")
        or raw.startswith("打窝失败，资源不足：")
        or parse_chum_daily_limit_reply(raw)
        or parse_chum_duplicate_active_reply(raw) is not None
        or ("你今日已垂钓" in raw and "明日再来" in raw)
        or "你已有一竿尚未收起" in raw
        or "你当前没有正在进行的垂钓" in raw
        or parse_generic_resource_shortage(raw) is not None
        or "你的鱼篓中没有【" in raw
        or "你尚无【青竹钓竿】" in raw
        or "你的鱼篓中只有【" in raw
    )


def current_fishing_config(snapshot):
    return normalize_fishing_config(
        snapshot.get("fishing_pond") or "青溪浅滩",
        snapshot.get("fishing_bait") or "凡饵",
        auto_chum_enabled=bool(snapshot.get("fishing_auto_chum_enabled")),
        chum_name=snapshot.get("fishing_chum_name") or "",
        chum_names=snapshot.get("fishing_chum_names") or None,
        auto_buy_bait_enabled=bool(snapshot.get("fishing_auto_buy_bait_enabled")),
        auto_buy_bait_count=clamp_fishing_buy_bait_count(snapshot.get("fishing_auto_buy_bait_count", FISHING_DEFAULT_BUY_BAIT_COUNT)),
        auto_probe_enabled=bool(snapshot.get("fishing_auto_probe_enabled")),
    )


def active_chum_plan_kwargs(snapshot):
    return {
        "active_chum_name": snapshot.get("fishing_active_chum_name") or "",
        "active_chum_rods_remaining": _parse_int(snapshot.get("fishing_chum_rods_remaining", 0)),
    }


def next_planned_command(snapshot, *, bait_inventory=None):
    bait = str(snapshot.get("fishing_forced_buy_bait") or "").strip()
    count = max(1, _parse_int(snapshot.get("fishing_forced_buy_count", 0), 1))
    if bait and snapshot.get("fishing_auto_buy_bait_enabled"):
        return f"{CMD_FISHING_BUY_BAIT} {bait} {count}", None

    config = current_fishing_config(snapshot)
    plan = plan_fishing_commands(
        config,
        bait_inventory=bait_inventory,
        chum_usage_counts=parse_chum_usage_counts(snapshot.get("fishing_chum_counts")),
        **active_chum_plan_kwargs(snapshot),
    )
    if not plan.allow_start:
        return "", plan
    commands = list(plan.commands or ())
    return (commands[0] if commands else ""), plan


def fishing_daily_limit(snapshot):
    return clamp_fishing_daily_limit(snapshot.get("fishing_daily_limit", FISHING_DEFAULT_DAILY_LIMIT))


def fishing_buy_bait_count(snapshot):
    return clamp_fishing_buy_bait_count(snapshot.get("fishing_auto_buy_bait_count", FISHING_DEFAULT_BUY_BAIT_COUNT))


def normalize_daily_counter(snapshot, now):
    day_key = get_day_key(now)
    updates = {}
    if str(snapshot.get("fishing_daily_day") or "") != day_key:
        updates["fishing_daily_day"] = day_key
        count = 0
    else:
        count = max(0, _parse_int(snapshot.get("fishing_daily_count", 0), 0))
    limit = fishing_daily_limit(snapshot)
    if count > limit:
        count = limit
        updates["fishing_daily_count"] = count
    elif "fishing_daily_day" in updates:
        updates["fishing_daily_count"] = 0
    return day_key, count, limit, updates


def mark_rod_confirmed(snapshot, now):
    _day_key, count, limit, updates = normalize_daily_counter(snapshot, now)
    count = min(limit, count + 1)
    updates["fishing_daily_count"] = count
    return count, limit, updates


def consume_active_chum_updates(snapshot):
    remaining = _parse_int(snapshot.get("fishing_chum_rods_remaining", 0))
    if remaining <= 0:
        return {}
    remaining = max(0, remaining - 1)
    updates = {"fishing_chum_rods_remaining": remaining}
    if remaining <= 0:
        updates["fishing_active_chum_name"] = ""
    return updates


def command_phase(command):
    raw = str(command or "").strip()
    if raw.startswith(CMD_FISHING_BUY_BAIT):
        return "buying"
    if raw.startswith(CMD_FISHING_CHUM):
        return "chumming"
    if raw.startswith(CMD_FISHING_BASKET):
        return "basket"
    if raw.startswith(CMD_FISHING_STATUS):
        return "checking"
    if raw.startswith(CMD_FISHING_PROBE):
        return "probing"
    if raw.startswith(CMD_FISHING_LIFT):
        return "lifting"
    if raw.startswith(CMD_FISHING_OPEN):
        return "opening"
    if raw.startswith(CMD_FISHING):
        return "fishing"
    return "idle"


def is_rod_in_progress(snapshot):
    phase = str(snapshot.get("fishing_phase") or "idle").strip()
    if phase in {"fishing", "waiting", "checking", "probing", "lifting"}:
        return True
    pending_action = str(snapshot.get("fishing_pending_action") or "").strip()
    if pending_action in {CMD_FISHING_STATUS, CMD_FISHING_PROBE, CMD_FISHING_LIFT}:
        return True
    return False


def is_nonblocking_open_timeout(snapshot):
    return str(snapshot.get("fishing_phase") or "").strip() == "opening"


def should_preserve_current_flow_for_open_reply(snapshot):
    phase = str(snapshot.get("fishing_phase") or "idle").strip()
    return is_rod_in_progress(snapshot) or phase in {"buying", "chumming", "basket"}


def build_send_success_effect(snapshot, command, *, sent_at, msg_id, reply_timeout_sec):
    phase = command_phase(command)
    reply_due_at = float(sent_at) + float(reply_timeout_sec)
    updates = {
        "fishing_reply_to_msg_id": int(msg_id or 0),
        "fishing_reply_due_at": reply_due_at,
        "fishing_phase": phase,
        "fishing_last_msg_id": int(msg_id or 0),
        "fishing_last_result": f"已发送：{command}",
        "fishing_last_error": "",
        "fishing_pending_action": "",
        "next_fishing_time": reply_due_at,
    }
    if phase == "fishing":
        updates["fishing_started_at"] = float(sent_at)
    elif phase == "opening":
        updates["fishing_pending_open_fish"] = snapshot.get("fishing_pending_open_fish", "")
        updates["fishing_started_at"] = snapshot.get("fishing_started_at", 0)
    else:
        updates["fishing_started_at"] = snapshot.get("fishing_started_at", 0)
    return FishingEffect(handled=True, updates=updates)


def build_send_failure_effect(command, now):
    if command_phase(command) == "opening":
        updates = clear_pending_updates()
        updates.update({
            "fishing_last_error": f"发送失败：{command}，保留待开队列",
            "fishing_last_result": "开鱼发送失败，稍后重试",
            "next_fishing_time": float(now + FISHING_BLOCKED_RETRY_SEC),
        })
        return FishingEffect(
            handled=True,
            updates=updates,
            audit_messages=(f"❌ 灵溪垂钓开鱼发送失败：{command}，已保留待开队列。",),
        )
    return FishingEffect(
        handled=True,
        updates=_with_next_delay({"fishing_last_error": f"发送失败：{command}"}, now, RETRY_MAX_SEC),
        audit_messages=(f"❌ 灵溪垂钓发送失败：{command}",),
    )


def decide_scheduler(snapshot, now, *, bait_inventory=None, next_day_jitter_sec=0):
    if not snapshot.get("fishing_enabled"):
        return FishingEffect()

    reply_to_msg_id = _parse_int(snapshot.get("fishing_reply_to_msg_id", 0))
    reply_due_at = float(snapshot.get("fishing_reply_due_at", 0) or 0)
    if reply_to_msg_id > 0:
        if reply_due_at > now:
            return FishingEffect()
        if is_rod_in_progress(snapshot):
            phase = str(snapshot.get("fishing_phase") or "").strip()
            if phase in {"checking", "probing"}:
                return FishingEffect(
                    handled=True,
                    command=CMD_FISHING_LIFT,
                    updates={
                        "fishing_reply_to_msg_id": 0,
                        "fishing_reply_due_at": 0,
                        "fishing_pending_action": "",
                        "fishing_phase": "lifting",
                        "fishing_last_error": f"钓鱼状态回复超时：{reply_to_msg_id}，直接提竿兜底",
                    },
                    audit_messages=(f"⚠️ 灵溪垂钓状态回复超时，消息ID={reply_to_msg_id}，直接提竿避免错过鱼讯。",),
                )
            return FishingEffect(
                handled=True,
                command=CMD_FISHING_STATUS,
                updates={
                    "fishing_reply_to_msg_id": 0,
                    "fishing_reply_due_at": 0,
                    "fishing_pending_action": "",
                    "fishing_phase": "checking",
                    "fishing_last_error": f"钓鱼回复超时：{reply_to_msg_id}，改查状态推进",
                },
                audit_messages=(f"⚠️ 灵溪垂钓回复超时，消息ID={reply_to_msg_id}，改用钓鱼状态恢复。",),
            )
        if is_nonblocking_open_timeout(snapshot):
            updates = clear_pending_updates()
            updates.update({
                "fishing_last_error": f"开鱼回复超时：{reply_to_msg_id}，保留待开队列",
                "fishing_last_result": "开鱼回复超时，稍后重试",
                "next_fishing_time": float(now + FISHING_BLOCKED_RETRY_SEC),
            })
            return FishingEffect(
                handled=True,
                updates=updates,
                audit_messages=(f"⚠️ 灵溪垂钓开鱼回复超时，消息ID={reply_to_msg_id}，已保留待开队列。",),
            )
        updates = clear_pending_updates()
        updates["fishing_last_error"] = f"回复超时：{reply_to_msg_id}"
        return FishingEffect(
            handled=True,
            updates=_with_next_delay(updates, now, RETRY_MAX_SEC),
            audit_messages=(f"⚠️ 灵溪垂钓回复超时，消息ID={reply_to_msg_id}，稍后重试。",),
        )

    if cd_blocks(snapshot.get("next_fishing_time", 0), now, 0):
        return FishingEffect()

    pending_action = str(snapshot.get("fishing_pending_action") or "").strip()
    if pending_action:
        return FishingEffect(handled=True, command=pending_action, updates={"fishing_pending_action": ""})

    if is_rod_in_progress(snapshot):
        return FishingEffect(
            handled=True,
            command=CMD_FISHING_STATUS,
            updates={"fishing_phase": "checking", "fishing_last_error": "钓鱼一竿进行中，改查状态推进"},
        )

    _day_key, count, limit, daily_updates = normalize_daily_counter(snapshot, now)
    _chum_counts, chum_updates = normalize_chum_counter(snapshot, now)
    daily_updates.update(chum_updates)
    planning_snapshot = dict(snapshot)
    planning_snapshot.update(daily_updates)
    if count >= limit:
        pending_open_command = next_pending_open_command(snapshot.get("fishing_pending_open_fish"))
        if pending_open_command:
            return FishingEffect(handled=True, command=pending_open_command, updates=daily_updates)
        updates = clear_pending_updates()
        updates.update(daily_updates)
        updates["fishing_last_error"] = f"今日钓鱼次数已达上限：{count}/{limit}"
        updates["next_fishing_time"] = next_fishing_day_timestamp(now, next_day_jitter_sec)
        return FishingEffect(handled=True, updates=updates)

    command, plan = next_planned_command(planning_snapshot, bait_inventory=bait_inventory)
    if not command:
        reason = (plan.blocked_reason if plan else "") or "计划不可执行"
        return FishingEffect(
            handled=True,
            updates=_with_next_delay({"fishing_last_error": reason}, now, FISHING_BLOCKED_RETRY_SEC),
        )
    return FishingEffect(handled=True, command=command, updates=daily_updates)


def decide_reply(snapshot, text, now, *, result_msg_id=0, action_delay_sec=2, post_rod_delay_sec=30):
    raw_text = str(text or "").strip()
    result_msg_id = int(result_msg_id or 0)
    if not is_fishing_reply_text(raw_text):
        return FishingEffect()

    basket = parse_fishing_basket(raw_text)
    if basket:
        chum_name, chum_rods = _parse_basket_chum(basket.current_chum)
        updates = {
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": "鱼篓校准",
            "fishing_last_error": "",
            "fishing_pending_open_fish": format_pending_open_fish(basket.fish),
        }
        if basket.daily_rods_used is not None:
            updates["fishing_daily_day"] = get_day_key(now)
            updates["fishing_daily_count"] = max(0, int(basket.daily_rods_used or 0))
        if basket.daily_rods_limit is not None:
            updates["fishing_daily_limit"] = clamp_fishing_daily_limit(basket.daily_rods_limit)
        updates["fishing_active_chum_name"] = chum_name
        updates["fishing_chum_rods_remaining"] = chum_rods
        return FishingEffect(
            handled=True,
            updates=updates,
            storage_counts=_basket_storage_counts(basket),
        )

    buy_result = parse_buy_bait_result(raw_text)
    if buy_result:
        updates = clear_pending_updates()
        updates.update({
            "fishing_forced_buy_bait": "",
            "fishing_forced_buy_count": 0,
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"买饵：{buy_result.bait}x{buy_result.count}",
            "fishing_last_error": "",
        })
        deltas = {buy_result.bait: int(buy_result.count or 0)}
        _apply_item_cost_deltas(deltas, FISHING_BAIT_COSTS.get(buy_result.bait, ()), buy_result.count)
        return FishingEffect(
            handled=True,
            updates=_with_next_delay(updates, now, action_delay_sec),
            storage_deltas=deltas,
        )

    missing_bait = parse_missing_bait_reply(raw_text)
    if missing_bait:
        updates = clear_pending_updates()
        updates.update({
            "fishing_forced_buy_bait": missing_bait,
            "fishing_forced_buy_count": fishing_buy_bait_count(snapshot),
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"缺少鱼饵：{missing_bait}",
        })
        if snapshot.get("fishing_auto_buy_bait_enabled"):
            updates["fishing_last_error"] = ""
            delay = action_delay_sec
        else:
            updates["fishing_last_error"] = f"缺少鱼饵：{missing_bait}"
            delay = FISHING_BLOCKED_RETRY_SEC
        return FishingEffect(handled=True, updates=_with_next_delay(updates, now, delay))

    shortage = parse_chum_shortage(raw_text)
    if shortage:
        updates = clear_pending_updates()
        bait = fishing_bait_name_for_item_key(shortage.item_key)
        result = f"打窝缺料：{bait or shortage.item_key}x{shortage.count}"
        updates.update({
            "fishing_forced_buy_bait": bait,
            "fishing_forced_buy_count": max(int(shortage.count or 1), fishing_buy_bait_count(snapshot)) if bait else 0,
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": result,
        })
        if bait and snapshot.get("fishing_auto_buy_bait_enabled"):
            updates["fishing_last_error"] = ""
            delay = action_delay_sec
        else:
            updates["fishing_forced_buy_bait"] = ""
            updates["fishing_forced_buy_count"] = 0
            updates["fishing_last_error"] = result
            delay = FISHING_RESOURCE_SHORTAGE_RETRY_SEC
        return FishingEffect(
            handled=True,
            updates=_with_next_delay(updates, now, delay),
            audit_messages=() if bait and snapshot.get("fishing_auto_buy_bait_enabled") else (f"⚠️ 灵溪垂钓资源不足：{result}，已暂停本轮避免超发。",),
        )

    generic_shortage = parse_generic_resource_shortage(raw_text)
    if generic_shortage:
        result = f"资源不足：{generic_shortage.label}"
        updates = clear_pending_updates()
        updates.update({
            "fishing_forced_buy_bait": "",
            "fishing_forced_buy_count": 0,
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": result,
            "fishing_last_error": result,
        })
        return FishingEffect(
            handled=True,
            updates=_with_next_delay(updates, now, FISHING_RESOURCE_SHORTAGE_RETRY_SEC),
            audit_messages=(f"⚠️ 灵溪垂钓资源不足：{generic_shortage.label}，已暂停本轮避免超发。",),
        )

    if parse_chum_daily_limit_reply(raw_text):
        chum_name = last_sent_chum_name(snapshot)
        updates = clear_pending_updates()
        updates.update(mark_chum_daily_limit_reached(snapshot, now, chum_name))
        result = f"打窝次数已用尽：{chum_name or '未知窝料'}"
        updates.update({
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": result,
            "fishing_last_error": "",
        })
        return FishingEffect(
            handled=True,
            updates=_with_next_delay(updates, now, action_delay_sec),
            audit_messages=(f"⚠️ 灵溪垂钓{result}，已跳过该窝料重新规划。",) if chum_name else (),
        )

    duplicate_chum = parse_chum_duplicate_active_reply(raw_text)
    if duplicate_chum:
        updates = clear_pending_updates()
        updates.update({
            "fishing_active_chum_name": duplicate_chum.chum,
            "fishing_chum_rods_remaining": max(0, int(duplicate_chum.rods or 0)),
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"打窝已存在：{duplicate_chum.chum}，剩余{max(0, int(duplicate_chum.rods or 0))}竿",
            "fishing_last_error": "",
        })
        return FishingEffect(
            handled=True,
            updates=_with_next_delay(updates, now, action_delay_sec),
        )

    chum_success = parse_chum_success_detail(raw_text)
    if chum_success:
        updates = clear_pending_updates()
        updates.update(mark_chum_confirmed(snapshot, now, chum_success.chum))
        updates.update({
            "fishing_active_chum_name": chum_success.chum,
            "fishing_chum_rods_remaining": max(0, int(chum_success.rods or 0)),
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"打窝成功：{chum_success.chum}，剩余{max(0, int(chum_success.rods or 0))}竿",
            "fishing_last_error": "",
        })
        cost = get_known_chum_cost(chum_success.chum)
        bait = fishing_bait_name_for_item_key(cost.item_key) if cost else ""
        deltas = {bait: -int(cost.count or 0)} if bait else {}
        if cost:
            _apply_item_cost_deltas(deltas, cost.item_costs, 1)
        return FishingEffect(
            handled=True,
            updates=_with_next_delay(updates, now, action_delay_sec),
            storage_deltas=deltas,
        )

    if parse_no_rod_reply(raw_text):
        updates = clear_pending_updates()
        updates.update({
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": "缺少青竹钓竿",
            "fishing_last_error": "缺少青竹钓竿，需手动购买 LDC 商城鱼竿",
        })
        return FishingEffect(
            handled=True,
            updates=_with_next_delay(updates, now, FISHING_NO_ROD_RETRY_SEC),
            audit_messages=("🎣 灵溪垂钓暂停：缺少青竹钓竿，需要手动购买。",),
        )

    if parse_fishing_in_progress_reply(raw_text):
        updates = clear_pending_updates()
        updates.update({
            "fishing_phase": "waiting",
            "fishing_pending_action": CMD_FISHING_STATUS,
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": "一竿尚未收起，改查钓鱼状态",
            "fishing_last_error": "",
        })
        return FishingEffect(
            handled=True,
            updates=_with_next_delay(updates, now, _bounded_action_delay(action_delay_sec)),
        )

    if parse_no_active_fishing_reply(raw_text):
        updates = clear_pending_updates()
        updates.update({
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": "当前没有正在进行的垂钓",
            "fishing_last_error": "",
        })
        return FishingEffect(handled=True, updates=_with_next_delay(updates, now, post_rod_delay_sec))

    daily_limit = parse_fishing_daily_limit_reached(raw_text)
    if daily_limit:
        updates = clear_pending_updates()
        updates.update({
            "fishing_daily_day": get_day_key(now),
            "fishing_daily_count": max(0, int(daily_limit.used or 0)),
            "fishing_daily_limit": clamp_fishing_daily_limit(daily_limit.limit),
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"今日垂钓已满：{daily_limit.used}/{daily_limit.limit}",
            "fishing_last_error": f"今日钓鱼次数已达上限：{daily_limit.used}/{daily_limit.limit}",
            "next_fishing_time": next_fishing_day_timestamp(now, action_delay_sec),
        })
        pending_open_command = next_pending_open_command(snapshot.get("fishing_pending_open_fish"))
        if pending_open_command:
            updates["fishing_pending_action"] = pending_open_command
            updates["next_fishing_time"] = float(now + _bounded_action_delay(action_delay_sec))
        return FishingEffect(handled=True, updates=updates)

    status = parse_fishing_status(raw_text, auto_probe_enabled=bool(snapshot.get("fishing_auto_probe_enabled")))
    if status:
        updates = {}
        deltas = {}
        hit_daily_limit = False
        if snapshot.get("fishing_phase") == "fishing" and status.bait:
            deltas[status.bait] = deltas.get(status.bait, 0) - 1
            updates.update(consume_active_chum_updates(snapshot))
            count, limit, daily_updates = mark_rod_confirmed(snapshot, now)
            updates.update(daily_updates)
            hit_daily_limit = count >= limit
        updates.update({
            "fishing_reply_to_msg_id": 0,
            "fishing_reply_due_at": 0,
            "fishing_phase": "waiting",
            "fishing_status_msg_id": result_msg_id,
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"{status.pond}：{status.signal} {status.progress_percent}%",
            "fishing_last_error": "今日钓鱼次数已达上限" if hit_daily_limit else "",
        })
        if status.suggested_command:
            updates["fishing_pending_action"] = status.suggested_command
            delay = _bounded_action_delay(action_delay_sec, status.lift_seconds)
            immediate_commands = ()
        else:
            wait_sec = status.wait_seconds if status.wait_seconds is not None else status.expected_wait_seconds
            delay = int(wait_sec if wait_sec is not None else 30) + _bounded_action_delay(action_delay_sec)
            updates["fishing_pending_action"] = CMD_FISHING_STATUS
            immediate_commands = ()
        return FishingEffect(
            handled=True,
            immediate_commands=immediate_commands,
            updates=_with_next_delay(updates, now, delay) if delay else updates,
            storage_deltas=deltas,
        )

    catch = parse_fishing_catch(raw_text)
    if catch:
        updates = clear_pending_updates()
        pending_open_fish = add_pending_open_fish(snapshot.get("fishing_pending_open_fish"), catch.fish, 1)
        _day_key, count, limit, _daily_updates = normalize_daily_counter(snapshot, now)
        updates.update({
            "fishing_pending_open_fish": pending_open_fish,
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"钓获：{catch.fish} {catch.weight_jin:.2f}斤",
            "fishing_last_error": "",
        })
        if count >= limit:
            updates["fishing_pending_action"] = next_pending_open_command(pending_open_fish)
            return FishingEffect(handled=True, updates=_with_next_delay(updates, now, action_delay_sec), storage_deltas={catch.fish: 1})
        return FishingEffect(handled=True, updates=_with_next_delay(updates, now, post_rod_delay_sec), storage_deltas={catch.fish: 1})

    empty_summary = parse_empty_fishing_result(raw_text)
    if empty_summary:
        updates = clear_pending_updates()
        updates.update({
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"空竿：{empty_summary}",
            "fishing_last_error": "",
        })
        return FishingEffect(handled=True, updates=_with_next_delay(updates, now, post_rod_delay_sec))

    open_result = parse_open_fish_result(raw_text)
    if open_result:
        pending_open_fish = remove_pending_open_fish(snapshot.get("fishing_pending_open_fish"), open_result.fish, open_result.count)
        preserve_current_flow = should_preserve_current_flow_for_open_reply(snapshot)
        if preserve_current_flow:
            updates = {
                "fishing_pending_open_fish": pending_open_fish,
            }
        else:
            updates = clear_pending_updates()
            updates["next_fishing_time"] = float(now)
        updates.update({
            "fishing_pending_open_fish": pending_open_fish,
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"开鱼：{open_result.fish}，修为+{open_result.xiuwei_gain}",
            "fishing_last_error": "",
        })
        next_open = next_pending_open_command(pending_open_fish)
        if next_open and not preserve_current_flow:
            updates["fishing_pending_action"] = next_open
            updates["next_fishing_time"] = float(now + max(1, float(action_delay_sec or 0)))
        deltas = {open_result.fish: -int(open_result.count or 0)}
        for item_name, count in (open_result.items or {}).items():
            deltas[item_name] = deltas.get(item_name, 0) + int(count or 0)
        return FishingEffect(
            handled=True,
            updates=updates,
            storage_deltas=deltas,
        )

    no_fish = parse_no_fish_reply(raw_text)
    if no_fish:
        pending_open_fish = remove_pending_open_fish(snapshot.get("fishing_pending_open_fish"), no_fish, 999999)
        preserve_current_flow = should_preserve_current_flow_for_open_reply(snapshot)
        if preserve_current_flow:
            updates = {
                "fishing_pending_open_fish": pending_open_fish,
            }
        else:
            updates = clear_pending_updates()
            updates["next_fishing_time"] = float(now)
        updates.update({
            "fishing_pending_open_fish": pending_open_fish,
            "fishing_last_msg_id": result_msg_id,
            "fishing_last_result": f"无可开鱼获：{no_fish}",
            "fishing_last_error": "",
        })
        next_open = next_pending_open_command(pending_open_fish)
        if next_open and not preserve_current_flow:
            updates["fishing_pending_action"] = next_open
            updates["next_fishing_time"] = float(now + max(1, float(action_delay_sec or 0)))
        return FishingEffect(handled=True, updates=updates)

    return FishingEffect()
