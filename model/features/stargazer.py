import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_STARGAZER_COLLECT,
    CMD_STARGAZER_GUIDE,
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_SOOTHE,
    RETRY_MAX_SEC,
    STARGAZER_STAR_DURATIONS,
)
from ..persistence import save_state
from ..runtime import console_log, send_audit_log, send_game_command, track_reply_chain_message
from ..state import get_current_identity_id, get_pending_command, get_stargazer_star_choice, get_stargazer_total_slots, set_stargazer_total_slots, state
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .resource_backoff import record_resource_shortage, reset_resource_shortage
from .storage_bag import apply_storage_bag_item_text_delta


RE_STARGAZER_SLOT_LINE = re.compile(r"^\s*(\d+)号引星盘[:：]\s*(.+)$")
RE_STARGAZER_DECLARED_TOTAL_SLOTS = re.compile(r"引星盘总数[:：]\s*(\d+)座")
RE_STARGAZER_COLLECTED_SLOT_COUNT = re.compile(r"成功从\s*(\d+)\s*座引星盘上收集")
RE_STARGAZER_SOOTHE_SUCCESS = re.compile(r"成功安抚了\s*\d+\s*座引星盘")
STARGAZER_CD_HINT_KEYWORDS = ("尚未恢复", "冷却", "等待", "不足", "休息")
STARGAZER_SOOTHE_INSUFFICIENT_POWER_KEYWORDS = ("灵力不足", "安抚", "座引星盘共需要", "点修为")
STARGAZER_SOOTHE_NO_NEED_KEYWORDS = ("观星台", "没有需要安抚", "星辰")
STARGAZER_GUIDE_INSUFFICIENT_POWER_KEYWORDS = ("修为不足", "同时牵引", "座引星盘")
STARGAZER_AFTER_DEEP_RETREAT_DELAY_SEC = 3 * 60
STARGAZER_LOST_FOLLOWUP_BACKOFF_SEC = 60 * 60
STARGAZER_PENDING_COMMANDS = (
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_SOOTHE,
    CMD_STARGAZER_COLLECT,
    CMD_STARGAZER_GUIDE,
)
STARGAZER_GUIDE_RESOURCE_KEY = "stargazer_guide"
STARGAZER_SOOTHE_RESOURCE_KEY = "stargazer_soothe"


def _has_pending_stargazer_command():
    for pending in state.get("pending_tasks", {}).values():
        pending_command = get_pending_command(pending)
        if any(
            pending_command == command or pending_command.startswith(f"{command} ")
            for command in STARGAZER_PENDING_COMMANDS
        ):
            return True
    return False


def _build_stargazer_guide_command(choice=None):
    star_choice = (choice or get_stargazer_star_choice()).strip()
    return f"{CMD_STARGAZER_GUIDE} {star_choice}"


def _extract_guide_star_choice(command_text):
    raw_command = str(command_text or "").strip()
    if not raw_command.startswith(CMD_STARGAZER_GUIDE):
        return ""
    suffix = raw_command[len(CMD_STARGAZER_GUIDE):].strip()
    return suffix.split()[0] if suffix else ""


def _extract_stargazer_collected_slot_count(text):
    match = RE_STARGAZER_COLLECTED_SLOT_COUNT.search(str(text or ""))
    return int(match.group(1) or 0) if match else 0


def _safe_float(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def _parse_stargazer_panel(text):
    raw_text = str(text or "")
    if "观星台" not in raw_text or "引星盘" not in raw_text:
        return None

    declared_total_slots = 0
    declared_match = RE_STARGAZER_DECLARED_TOTAL_SLOTS.search(raw_text)
    if declared_match:
        declared_total_slots = int(declared_match.group(1) or 0)

    total_slots = 0
    idle_slot_count = 0
    dim_slot_count = 0
    ready_slot_count = 0
    busy_waits = []

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        match = RE_STARGAZER_SLOT_LINE.match(line)
        if not match:
            continue
        total_slots += 1
        if "星光黯淡" in line or "元磁紊乱" in line:
            dim_slot_count += 1
            continue
        if "精华已成" in line or "可收集" in line:
            ready_slot_count += 1
            continue
        if "空闲" in line:
            idle_slot_count += 1
            continue
        if "凝聚中" in line and has_wait_time(line):
            busy_waits.append(parse_wait_time(line))

    if total_slots <= 0:
        return None

    min_wait = min(busy_waits) if busy_waits else 0
    max_wait = max(busy_waits) if busy_waits else 0
    all_ready = ready_slot_count == total_slots and total_slots > 0
    return {
        "total_slots": total_slots,
        "declared_total_slots": declared_total_slots,
        "idle_slot_count": idle_slot_count,
        "dim_slot_count": dim_slot_count,
        "ready_slot_count": ready_slot_count,
        "busy_waits": busy_waits,
        "min_wait": min_wait,
        "max_wait": max_wait,
        "all_ready": all_ready,
    }


def _sync_stargazer_panel_state(parsed, now):
    state["stargazer_idle_slot_count"] = int(parsed.get("idle_slot_count", 0) or 0)
    state["stargazer_dim_slot_count"] = int(parsed.get("dim_slot_count", 0) or 0)
    state["stargazer_ready_slot_count"] = int(parsed.get("ready_slot_count", 0) or 0)
    declared_total_slots = int(parsed.get("declared_total_slots", 0) or 0)
    if declared_total_slots > 0:
        set_stargazer_total_slots(get_current_identity_id(), declared_total_slots)
    max_wait = int(parsed.get("max_wait", 0) or 0)
    if max_wait > 0:
        state["stargazer_busy_until"] = now + max_wait + CD_BUFFER_SEC
        state["stargazer_collect_due_at"] = now + max_wait + CD_BUFFER_SEC
    elif parsed.get("all_ready"):
        state["stargazer_busy_until"] = 0
        state["stargazer_collect_due_at"] = now
    else:
        state["stargazer_busy_until"] = 0
        state["stargazer_collect_due_at"] = 0


def _schedule_next_stargazer_action(next_time):
    state["next_stargazer_panel_time"] = float(next_time or 0)


def _schedule_stargazer_timeout_panel_fallback(msg, sent_at, fallback_delay=RETRY_MAX_SEC):
    msg_id = int(getattr(msg, "id", 0) or 0)
    delay = float(fallback_delay or RETRY_MAX_SEC)
    pending = state.get("pending_tasks", {}).get(msg_id) if msg_id > 0 else None
    if isinstance(pending, dict):
        delay = float(pending.get("timeout", 0) or 0) or delay
    _schedule_next_stargazer_action(float(sent_at or time.time()) + delay + random.uniform(5, 10))


def _get_queued_stargazer_action():
    queued_action = str(state.get("stargazer_queued_action") or "").strip()
    if queued_action:
        return queued_action
    last_action = str(state.get("stargazer_last_action") or "")
    if not last_action.startswith("queue_"):
        return ""
    return last_action[len("queue_"):].strip()


def _queue_stargazer_followup_action(now, action, delay):
    state["stargazer_followup_due_at"] = float(now + max(1, delay))
    state["stargazer_queued_action"] = str(action or "").strip()
    state["stargazer_last_action"] = f"queue_{action}"
    state["next_stargazer_panel_time"] = 0


def _clear_stargazer_collect_flags():
    state["stargazer_collect_ready"] = False
    state["stargazer_soothe_before_collect"] = False


def _stargazer_next_panel_time_blocks(now):
    return cd_blocks(state.get("next_stargazer_panel_time", 0), now, 0)


def _stargazer_followup_due_blocks(now):
    return cd_blocks(state.get("stargazer_followup_due_at", 0), now, 0)


async def _queue_stargazer_action(now, action, delay=None, audit_text=None):
    if delay is None:
        delay = random.uniform(5, 10)
    _queue_stargazer_followup_action(now, action, delay)
    save_state()
    if audit_text:
        await send_audit_log(f"{audit_text}→{fmt_time_after(delay)}")


async def _send_stargazer_panel(now, audit_text=None):
    state["stargazer_last_action"] = "panel"
    state["stargazer_followup_due_at"] = 0
    _schedule_next_stargazer_action(now + RETRY_MAX_SEC)
    msg = await send_game_command(CMD_STARGAZER_PANEL, max_retry=1)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        retry_delay = RETRY_MAX_SEC + random.uniform(5, 10)
        _queue_stargazer_followup_action(sent_at, "panel", retry_delay)
        save_state()
        await send_audit_log("❌ 观星台发送失败，稍后重试。")
        return False
    _schedule_next_stargazer_action(sent_at + RETRY_MAX_SEC)
    save_state()
    if audit_text:
        await send_audit_log(audit_text)
    return True


async def _send_stargazer_soothe(now, audit_text=None):
    state["stargazer_last_action"] = "soothe"
    state["stargazer_followup_due_at"] = 0
    _schedule_next_stargazer_action(now + RETRY_MAX_SEC)
    msg = await send_game_command(CMD_STARGAZER_SOOTHE, max_retry=1)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        retry_delay = RETRY_MAX_SEC + random.uniform(5, 10)
        _queue_stargazer_followup_action(sent_at, "soothe", retry_delay)
        save_state()
        await send_audit_log("❌ 安抚星辰发送失败，稍后重试。")
        return False
    _schedule_next_stargazer_action(sent_at + RETRY_MAX_SEC)
    save_state()
    if audit_text:
        await send_audit_log(audit_text)
    return True


async def _send_stargazer_collect(now, audit_text=None):
    state["stargazer_last_action"] = "collect"
    state["stargazer_followup_due_at"] = 0
    _schedule_next_stargazer_action(now + RETRY_MAX_SEC)
    msg = await send_game_command(CMD_STARGAZER_COLLECT, max_retry=1)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        retry_delay = RETRY_MAX_SEC + random.uniform(5, 10)
        _queue_stargazer_followup_action(sent_at, "collect", retry_delay)
        save_state()
        await send_audit_log("❌ 收集精华发送失败，稍后重试。")
        return False
    _schedule_next_stargazer_action(sent_at + RETRY_MAX_SEC)
    save_state()
    if audit_text:
        await send_audit_log(audit_text)
    return True


async def _send_stargazer_guide(now, audit_text=None):
    state["stargazer_last_action"] = "guide"
    state["stargazer_followup_due_at"] = 0
    _schedule_next_stargazer_action(now + RETRY_MAX_SEC)
    guide_command = _build_stargazer_guide_command()
    msg = await send_game_command(guide_command, max_retry=0)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        retry_delay = RETRY_MAX_SEC + random.uniform(5, 10)
        _queue_stargazer_followup_action(sent_at, "guide", retry_delay)
        save_state()
        await send_audit_log("❌ 牵引星辰发送失败，稍后重试。")
        return False
    _schedule_stargazer_timeout_panel_fallback(msg, sent_at)
    save_state()
    if audit_text:
        await send_audit_log(audit_text)
    return True


def get_stargazer_status_text():
    total_slots = int(get_stargazer_total_slots() or 0)
    idle_slot_count = int(state.get('stargazer_idle_slot_count', 0) or 0)
    dim_slot_count = int(state.get('stargazer_dim_slot_count', 0) or 0)
    ready_slot_count = int(state.get('stargazer_ready_slot_count', 0) or 0)
    guiding_slot_count = max(0, total_slots - idle_slot_count - dim_slot_count - ready_slot_count)
    followup_due_at = _safe_float(state.get("stargazer_followup_due_at", 0), 0)

    next_panel_time = _safe_float(state.get("next_stargazer_panel_time", 0), 0)
    next_due_at = 0
    if followup_due_at > 0 and next_panel_time > 0:
        next_due_at = min(followup_due_at, next_panel_time)
    else:
        next_due_at = followup_due_at or next_panel_time
    return (
        "🔭 观星台\n"
        f"- 总星盘：{total_slots}\n"
        f"- 空闲盘：{idle_slot_count} ｜ 牵引中：{guiding_slot_count} ｜ 黯淡盘：{dim_slot_count} ｜ 精华已成：{ready_slot_count}\n"
        f"- 下次动作：{fmt_abs_ts(next_due_at)}（{fmt_remaining(next_due_at)}）"
    )


async def handle_stargazer_panel(text, now, is_reply_to_me, matched_family=None):
    if not state.get("stargazer_enabled"):
        return False

    parsed = _parse_stargazer_panel(text)
    if not parsed:
        return False
    if not is_reply_to_me:
        return False

    _sync_stargazer_panel_state(parsed, now)
    state["stargazer_followup_due_at"] = 0

    if parsed["dim_slot_count"] > 0:
        state["stargazer_wait_full_collect"] = False
        _clear_stargazer_collect_flags()
        await _queue_stargazer_action(now, "soothe", audit_text="🌠 黯淡盘，安抚")
        return True

    if parsed["all_ready"]:
        state["stargazer_wait_full_collect"] = False
        _clear_stargazer_collect_flags()
        await _queue_stargazer_action(now, "collect", audit_text="💎 全盘成熟，收集")
        return True

    if state.get("stargazer_wait_full_collect") and parsed["max_wait"] > 0:
        next_panel_time = now + parsed["max_wait"] + CD_BUFFER_SEC + random.uniform(5, 10)
        _schedule_next_stargazer_action(next_panel_time)
        state["stargazer_last_action"] = "waiting_full_collect"
        save_state()
        console_log(f"🔭 存在未成熟星盘→{fmt_time_after(max(0, next_panel_time - now))} 后再走安抚→收集")
        return True

    if parsed["max_wait"] > 0:
        state["stargazer_wait_full_collect"] = False
        next_panel_time = now + parsed["max_wait"] + CD_BUFFER_SEC + random.uniform(5, 10)
        _schedule_next_stargazer_action(next_panel_time)
        state["stargazer_last_action"] = "waiting_panel"
        save_state()
        console_log(f"🔭 回查→{fmt_time_after(max(0, next_panel_time - now))}")
        return True

    if parsed["idle_slot_count"] > 0:
        state["stargazer_wait_full_collect"] = False
        _clear_stargazer_collect_flags()
        await _queue_stargazer_action(
            now,
            "guide",
            audit_text=f"🌌 空闲盘，牵引 {get_stargazer_star_choice()}",
        )
        return True

    state["stargazer_wait_full_collect"] = False
    next_panel_time = now + RETRY_MAX_SEC + random.uniform(5, 10)
    _schedule_next_stargazer_action(next_panel_time)
    state["stargazer_last_action"] = "waiting_panel"
    save_state()
    console_log(f"🔭 回查→{fmt_time_after(max(0, next_panel_time - now))}")
    return True


async def handle_stargazer_guide_reply(text, now, reply_to, matched_family=None):
    if not state.get("stargazer_enabled"):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "stargazer_guide" and CMD_STARGAZER_GUIDE not in orig_cmd:
        return False

    if "已无空闲的引星盘" in text:
        _clear_stargazer_collect_flags()
        await _queue_stargazer_action(now, "panel", audit_text="🔄 无空闲引星盘，回查")
        return True

    if _is_stargazer_guide_insufficient_power(text):
        await _queue_stargazer_resource_backoff(
            now,
            "guide",
            STARGAZER_GUIDE_RESOURCE_KEY,
            "⏳ 牵引修为不足",
            text,
        )
        return True

    if any(keyword in text for keyword in STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(text):
        wait_sec = parse_wait_time(text)
        follow_delay = wait_sec + CD_BUFFER_SEC + random.uniform(5, 10)
        _queue_stargazer_followup_action(now, "guide", follow_delay)
        state["stargazer_last_action"] = "queue_guide"
        reset_resource_shortage(STARGAZER_GUIDE_RESOURCE_KEY)
        save_state()
        await send_audit_log(f"⏳ 牵引 CD→{fmt_time_after(follow_delay)}")
        return True

    if "牵引成功" not in text:
        return False

    star_choice = _extract_guide_star_choice(orig_cmd)
    duration_sec = int(STARGAZER_STAR_DURATIONS.get(star_choice, 0) or 0)
    due_at = now + duration_sec + CD_BUFFER_SEC if duration_sec > 0 else 0
    next_action_time = due_at + random.uniform(5, 10) if due_at > 0 else now + RETRY_MAX_SEC + random.uniform(5, 10)
    existing_collect_due = float(state.get("stargazer_collect_due_at", 0) or 0)

    state["stargazer_idle_slot_count"] = 0
    state["stargazer_dim_slot_count"] = 0
    if due_at > 0:
        state["stargazer_collect_due_at"] = max(existing_collect_due, due_at)
        state["next_stargazer_panel_time"] = next_action_time
    _clear_stargazer_collect_flags()
    state["stargazer_last_action"] = "guide_success"
    reset_resource_shortage(STARGAZER_GUIDE_RESOURCE_KEY)
    save_state()
    if due_at > 0:
        await send_audit_log(f"🌌 牵引{star_choice}成功，收集→{fmt_time_after(duration_sec + CD_BUFFER_SEC)}")
    else:
        await send_audit_log("🌌 牵引星辰成功。")
    return True


def _is_stargazer_soothe_insufficient_power(text):
    return all(keyword in str(text or "") for keyword in STARGAZER_SOOTHE_INSUFFICIENT_POWER_KEYWORDS)


def _is_stargazer_soothe_no_need(text):
    return all(keyword in str(text or "") for keyword in STARGAZER_SOOTHE_NO_NEED_KEYWORDS)


def _is_stargazer_soothe_success(text):
    raw_text = str(text or "")
    return "安抚完成" in raw_text or bool(RE_STARGAZER_SOOTHE_SUCCESS.search(raw_text))


def _is_stargazer_guide_insufficient_power(text):
    return all(keyword in str(text or "") for keyword in STARGAZER_GUIDE_INSUFFICIENT_POWER_KEYWORDS)


def _get_stargazer_after_deep_retreat_delay(now):
    next_deep_retreat_time = float(state.get("next_deep_retreat_time", 0) or 0)
    target_time = max(now, next_deep_retreat_time) + STARGAZER_AFTER_DEEP_RETREAT_DELAY_SEC
    return max(1, target_time - now)


async def _queue_stargazer_action_after_deep_retreat(now, action, audit_text):
    delay = _get_stargazer_after_deep_retreat_delay(now)
    _queue_stargazer_followup_action(now, action, delay)
    save_state()
    await send_audit_log(f"{audit_text}→{fmt_time_after(delay)}")


async def _queue_stargazer_resource_backoff(now, action, action_key, audit_text, raw_text):
    backoff = record_resource_shortage(action_key, now, reason=raw_text)
    due_at = float(backoff.get("next_at", 0) or 0)
    delay = max(1, due_at - now)
    _queue_stargazer_followup_action(now, action, delay)
    save_state()
    await send_audit_log(
        f"{audit_text}，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(delay)}"
    )


async def handle_stargazer_soothe_reply(text, now, reply_to, matched_family=None):
    if not state.get("stargazer_enabled"):
        return False

    raw_text = str(text or "").strip()
    if raw_text.startswith(CMD_STARGAZER_SOOTHE):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "stargazer_soothe" and CMD_STARGAZER_SOOTHE not in orig_cmd:
        return False

    if _is_stargazer_soothe_insufficient_power(text):
        await _queue_stargazer_resource_backoff(
            now,
            "soothe",
            STARGAZER_SOOTHE_RESOURCE_KEY,
            "⏳ 安抚灵力不足",
            text,
        )
        return True

    if _is_stargazer_soothe_no_need(text):
        _clear_stargazer_collect_flags()
        reset_resource_shortage(STARGAZER_SOOTHE_RESOURCE_KEY)
        await _queue_stargazer_action(now, "panel", audit_text="🌠 无需安抚，回查观星台")
        return True

    soothe_before_collect = bool(state.get("stargazer_soothe_before_collect"))
    if soothe_before_collect and not (any(keyword in text for keyword in STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(text)):
        _clear_stargazer_collect_flags()
        reset_resource_shortage(STARGAZER_SOOTHE_RESOURCE_KEY)
        await _queue_stargazer_action(now, "collect", audit_text="🌠 已收到安抚回复，收集")
        return True

    if any(keyword in text for keyword in STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(text):
        wait_sec = parse_wait_time(text)
        follow_delay = wait_sec + CD_BUFFER_SEC + random.uniform(5, 10)
        _queue_stargazer_followup_action(now, "soothe", follow_delay)
        state["stargazer_last_action"] = "queue_soothe"
        reset_resource_shortage(STARGAZER_SOOTHE_RESOURCE_KEY)
        save_state()
        await send_audit_log(f"⏳ 安抚 CD→{fmt_time_after(follow_delay)}")
        return True

    if _is_stargazer_soothe_success(text):
        _clear_stargazer_collect_flags()
        await _queue_stargazer_action(now, "collect", audit_text="🌠 安抚完成，收集")
        return True

    _clear_stargazer_collect_flags()
    await _queue_stargazer_action(now, "panel", audit_text="🔭 安抚异常，回查")
    return True


async def handle_stargazer_collect_reply(text, now, reply_to, matched_family=None):
    if not state.get("stargazer_enabled"):
        return False

    raw_text = str(text or "").strip()
    if raw_text.startswith(CMD_STARGAZER_COLLECT):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "stargazer_collect" and CMD_STARGAZER_COLLECT not in orig_cmd:
        return False

    if any(keyword in text for keyword in STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(text):
        wait_sec = parse_wait_time(text)
        follow_delay = wait_sec + CD_BUFFER_SEC + random.uniform(5, 10)
        _queue_stargazer_followup_action(now, "collect", follow_delay)
        state["stargazer_last_action"] = "queue_collect"
        save_state()
        await send_audit_log(f"⏳ 收集 CD→{fmt_time_after(follow_delay)}")
        return True

    if "收集完成" in text:
        collected_slot_count = _extract_stargazer_collected_slot_count(text)
        total_slots = get_stargazer_total_slots()
        apply_storage_bag_item_text_delta(get_current_identity_id(), raw_text)
        _clear_stargazer_collect_flags()
        state["stargazer_collect_due_at"] = 0
        state["stargazer_busy_until"] = 0
        state["stargazer_ready_slot_count"] = max(0, int(state.get("stargazer_ready_slot_count", 0) or 0) - collected_slot_count)
        if collected_slot_count > 0:
            if total_slots > 0 and collected_slot_count >= total_slots:
                state["stargazer_wait_full_collect"] = False
                state["stargazer_idle_slot_count"] = total_slots
                state["stargazer_dim_slot_count"] = 0
                await _queue_stargazer_action(
                    now,
                    "guide",
                    audit_text=f"🌌 已完成 {collected_slot_count}/{total_slots} 座收集，牵引 {get_stargazer_star_choice()}",
                )
                return True
            state["stargazer_wait_full_collect"] = True
            state["stargazer_idle_slot_count"] = 0
            await _queue_stargazer_action(
                now,
                "panel",
                audit_text=f"🔭 已收集 {collected_slot_count} 座，回查等待其余成熟",
            )
            return True
        _schedule_next_stargazer_action(now + RETRY_MAX_SEC + random.uniform(5, 10))
        state["stargazer_last_action"] = "collect_success"
        save_state()
        await send_audit_log("💎 收集完成。")
        return True

    if "没有已凝聚成形的星辰精华可供收集" in text:
        _clear_stargazer_collect_flags()
        state["stargazer_collect_due_at"] = 0
        state["stargazer_busy_until"] = 0
        state["stargazer_wait_full_collect"] = False
        await _queue_stargazer_action(now, "panel", audit_text="🔭 当前无可收集精华，回查")
        return True

    _clear_stargazer_collect_flags()
    state["stargazer_wait_full_collect"] = False
    await _queue_stargazer_action(now, "panel", audit_text="🔭 收集异常，回查")
    return True


async def run_stargazer_scheduler(now):
    if not state.get("stargazer_enabled"):
        return

    if _has_pending_stargazer_command():
        return

    if _stargazer_followup_due_blocks(now):
        return

    followup_due_at = _safe_float(state.get("stargazer_followup_due_at", 0), 0)
    if followup_due_at > 0:
        if now < followup_due_at:
            return
        queued_action = _get_queued_stargazer_action()
        state["stargazer_followup_due_at"] = 0
        state["stargazer_queued_action"] = ""
        save_state()
        if queued_action == "collect":
            await _send_stargazer_collect(now)
            return
        if queued_action == "panel":
            await _send_stargazer_panel(now)
            return
        if queued_action == "guide":
            await _send_stargazer_guide(now)
            return
        if queued_action == "soothe":
            await _send_stargazer_soothe(now)
            return
        next_panel_time = now + STARGAZER_LOST_FOLLOWUP_BACKOFF_SEC + random.uniform(30, 90)
        _schedule_next_stargazer_action(next_panel_time)
        state["stargazer_last_action"] = "lost_followup_action"
        save_state()
        await send_audit_log(
            f"🧯 观星台队列动作丢失，停止立即回查，延后→{fmt_time_after(max(1, next_panel_time - now))}"
        )
        return

    if _stargazer_next_panel_time_blocks(now):
        return

    next_panel_time = _safe_float(state.get("next_stargazer_panel_time", 0), 0)
    if next_panel_time <= 0:
        _schedule_next_stargazer_action(now)
        save_state()
        next_panel_time = now

    if now >= next_panel_time:
        state["stargazer_soothe_before_collect"] = False
        await _queue_stargazer_action(now, "panel", audit_text="🌠 观星台到时，查面板")


async def sync_stargazer_total_slots(send_as_id):
    send_as_id = int(send_as_id)
    msg = await send_game_command(CMD_STARGAZER_PANEL, track=False, send_as_id=send_as_id)
    if not msg:
        return False, "观星台同步发送失败"
    track_reply_chain_message(msg.id, send_as_id, "stargazer_sync", root_msg_id=msg.id)
    return True, f"已发送同步指令[{send_as_id}]，等待观星台回复"


def handle_stargazer_sync_reply(text, now=None):
    parsed = _parse_stargazer_panel(text)
    if not parsed:
        return False
    declared_total_slots = int(parsed.get("declared_total_slots", 0) or 0)
    if declared_total_slots <= 0:
        return False
    set_stargazer_total_slots(get_current_identity_id(), declared_total_slots)
    if now is not None:
        _sync_stargazer_panel_state(parsed, now)
        state["stargazer_followup_due_at"] = 0
    save_state()
    return parsed


__all__ = [
    "get_stargazer_status_text",
    "handle_stargazer_collect_reply",
    "handle_stargazer_guide_reply",
    "handle_stargazer_panel",
    "handle_stargazer_soothe_reply",
    "handle_stargazer_sync_reply",
    "run_stargazer_scheduler",
    "sync_stargazer_total_slots",
]
