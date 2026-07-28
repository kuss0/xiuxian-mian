import random
import re
import time
from pathlib import Path

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
from ..runtime import console_log, send_audit_log
from ..state import get_current_identity_id, get_global_enabled, get_global_pause_source, get_identity_enabled, get_stargazer_star_choice, get_stargazer_total_slots, set_stargazer_total_slots, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, get_day_key, has_wait_time, parse_wait_time
from ..webapp_core import MiniAppCaptureStore
from .miniapp_common import append_business_capture
from .resource_backoff import record_resource_shortage, reset_resource_shortage
from .storage_bag import apply_storage_bag_item_deltas, apply_storage_bag_item_text_delta
from .stargazer_miniapp import extract_stargazer_miniapp_launch, run_stargazer_miniapp_production_flow


RE_STARGAZER_SLOT_LINE = re.compile(r"^\s*(\d+)号引星盘[:：]\s*(.+)$")
RE_STARGAZER_DECLARED_TOTAL_SLOTS = re.compile(r"引星盘总数[:：]\s*(\d+)座")
RE_STARGAZER_COLLECTED_SLOT_COUNT = re.compile(r"成功从\s*(\d+)\s*座引星盘上收集")
RE_STARGAZER_SOOTHE_SUCCESS = re.compile(r"成功安抚了\s*\d+\s*座引星盘")
STARGAZER_CD_HINT_KEYWORDS = ("尚未恢复", "冷却", "等待", "不足", "休息")
STARGAZER_SOOTHE_INSUFFICIENT_POWER_KEYWORDS = ("灵力不足", "安抚", "座引星盘共需要", "点修为")
STARGAZER_SOOTHE_NO_NEED_KEYWORDS = ("观星台", "没有需要安抚", "星辰")
STARGAZER_GUIDE_INSUFFICIENT_POWER_KEYWORDS = ("修为不足", "同时牵引", "座引星盘")
STARGAZER_AFTER_DEEP_RETREAT_DELAY_SEC = 3 * 60


STARGAZER_GUIDE_RESOURCE_KEY = "stargazer_guide"
STARGAZER_SOOTHE_RESOURCE_KEY = "stargazer_soothe"
STARGAZER_MINIAPP_PAUSED_ACTION = "miniapp_entry_seen"
STARGAZER_MINIAPP_MANUAL_AUTH_TTL_SEC = 10 * 60
STARGAZER_MINIAPP_ENTRY_KEYWORDS = (
    "小程序",
    "miniapp",
    "mini app",
    "webapp",
    "web app",
    "进入观星",
    "打开观星",
    "进入灵圃",
    "宗门灵圃",
    "迁入",
)
STARGAZER_MINIAPP_FAILURE_BACKOFF_SEC = 30 * 60
STARGAZER_MINIAPP_CAPTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "state" / "miniapp_capture"
_MINIAPP_MANUAL_AUTH_UNTIL = {}
_MINIAPP_RUN_LOCKS = {}


def _miniapp_http_allowed_during_pause():
    """天尊维护暂停期间仍允许 MiniApp HTTP。

    刻意保留在各模块本地而不是收进 miniapp_common：测试普遍用
    patch.object(<该模块>, "get_global_enabled") 打桩，判断一旦搬走，
    62 处 patch 点就都失效了。这点重复换来的是打桩位置符合直觉。
    """
    return (not get_global_enabled()) and get_global_pause_source() == "tianzun_maintenance"


def _identity_id(value=None):
    try:
        return int(value if value is not None else get_current_identity_id() or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def authorize_stargazer_miniapp_manual_run(identity_id, *, now=None, ttl_sec=STARGAZER_MINIAPP_MANUAL_AUTH_TTL_SEC):
    identity_id = _identity_id(identity_id)
    if identity_id <= 0:
        return 0
    now = float(now or time.time())
    _MINIAPP_MANUAL_AUTH_UNTIL[identity_id] = now + max(30, float(ttl_sec or STARGAZER_MINIAPP_MANUAL_AUTH_TTL_SEC))
    return _MINIAPP_MANUAL_AUTH_UNTIL[identity_id]


def revoke_stargazer_miniapp_manual_run(identity_id):
    _MINIAPP_MANUAL_AUTH_UNTIL.pop(_identity_id(identity_id), None)


def _has_stargazer_miniapp_manual_auth(identity_id, now):
    identity_id = _identity_id(identity_id)
    expires_at = float(_MINIAPP_MANUAL_AUTH_UNTIL.get(identity_id, 0) or 0)
    if expires_at <= 0:
        return False
    if float(now or time.time()) > expires_at:
        _MINIAPP_MANUAL_AUTH_UNTIL.pop(identity_id, None)
        return False
    return True


def _stargazer_miniapp_run_lock(identity_id):
    import asyncio

    identity_id = _identity_id(identity_id)
    lock = _MINIAPP_RUN_LOCKS.get(identity_id)
    if lock is None:
        lock = asyncio.Lock()
        _MINIAPP_RUN_LOCKS[identity_id] = lock
    return lock


def _is_stargazer_miniapp_paused():
    return str(state.get("stargazer_last_action") or "").strip() == STARGAZER_MINIAPP_PAUSED_ACTION


def _looks_like_stargazer_miniapp_entry(text):
    raw_text = str(text or "")
    if "观星" not in raw_text and "星台" not in raw_text:
        return False
    lowered = raw_text.lower()
    return any(keyword in lowered for keyword in STARGAZER_MINIAPP_ENTRY_KEYWORDS)


def _is_stargazer_panel_reply(reply_to=None, matched_family=None):
    family = str(matched_family or "").strip()
    if family in {"stargazer_panel", "stargazer_sync", "stargazer_panel_edit"}:
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return orig_cmd.startswith(CMD_STARGAZER_PANEL)


def _pause_stargazer_legacy_chain(now, *, result_msg_id=0):
    _clear_stargazer_collect_flags()
    state["stargazer_followup_due_at"] = 0
    state["stargazer_queued_action"] = ""
    state["next_stargazer_panel_time"] = 0
    state["stargazer_collect_due_at"] = 0
    state["stargazer_busy_until"] = 0
    state["stargazer_last_action"] = STARGAZER_MINIAPP_PAUSED_ACTION
    if int(result_msg_id or 0) > 0:
        state["stargazer_last_panel_msg_id"] = int(result_msg_id or 0)
    save_state()


def _stargazer_miniapp_capture_store(now):
    day_key = get_day_key(now)
    return MiniAppCaptureStore(STARGAZER_MINIAPP_CAPTURE_DIR / f"stargazer-{day_key}.jsonl", keep_memory=False)


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


def _queue_stargazer_followup_action(now, action, delay):
    state["stargazer_followup_due_at"] = float(now + max(1, delay))
    state["stargazer_queued_action"] = str(action or "").strip()
    state["stargazer_last_action"] = f"queue_{action}"
    state["next_stargazer_panel_time"] = 0


def _clear_stargazer_collect_flags():
    state["stargazer_collect_ready"] = False
    state["stargazer_soothe_before_collect"] = False


async def _queue_stargazer_action(now, action, delay=None, audit_text=None):
    if delay is None:
        delay = random.uniform(5, 10)
    _queue_stargazer_followup_action(now, action, delay)
    save_state()
    if audit_text:
        await send_audit_log(f"{audit_text}→{fmt_time_after(delay)}")


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
    pause_line = "\n- 状态：MiniApp入口已识别，旧文本链暂停" if _is_stargazer_miniapp_paused() else ""
    return (
        "🔭 观星台\n"
        f"- 总星盘：{total_slots}\n"
        f"- 空闲盘：{idle_slot_count} ｜ 牵引中：{guiding_slot_count} ｜ 黯淡盘：{dim_slot_count} ｜ 精华已成：{ready_slot_count}\n"
        f"- 下次动作：{fmt_abs_ts(next_due_at)}（{fmt_remaining(next_due_at)}）"
        f"{pause_line}"
    )


def _format_stargazer_item_deltas(item_deltas):
    return "、".join(
        f"{name}x{count}"
        for name, count in sorted((item_deltas or {}).items(), key=lambda item: str(item[0]))
        if str(name or "").strip() and int(count or 0) > 0
    )


def _format_stargazer_miniapp_action_summary(action_counts, item_deltas, star_choice, suffix=""):
    parts = []
    action_counts = dict(action_counts or {})
    if int(action_counts.get("soothe", 0) or 0) > 0:
        parts.append(f"安抚 {int(action_counts.get('soothe', 0) or 0)} 座")
    if int(action_counts.get("collect", 0) or 0) > 0:
        parts.append(f"收集 {int(action_counts.get('collect', 0) or 0)} 次")
    if int(action_counts.get("pull", 0) or 0) > 0:
        star_text = str(star_choice or get_stargazer_star_choice() or "").strip()
        parts.append(f"牵引 {int(action_counts.get('pull', 0) or 0)} 座{star_text}")
    item_summary = _format_stargazer_item_deltas(item_deltas)
    if item_summary:
        parts.append(item_summary)
    if suffix:
        parts.append(suffix)
    return "｜".join(parts)


async def _finish_stargazer_miniapp_result(result, now, *, star_choice=""):
    result = dict(result or {})
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    farm_state = data.get("farm_state") if isinstance(data.get("farm_state"), dict) else {}
    action_counts = data.get("action_counts") if isinstance(data.get("action_counts"), dict) else {}
    item_deltas = data.get("item_deltas") if isinstance(data.get("item_deltas"), dict) else {}

    if farm_state:
        _sync_stargazer_panel_state(farm_state, now)
    if item_deltas:
        apply_storage_bag_item_deltas(get_current_identity_id(), item_deltas)
    state["stargazer_followup_due_at"] = 0
    state["stargazer_queued_action"] = ""
    state["stargazer_wait_full_collect"] = False
    _clear_stargazer_collect_flags()

    if result.get("ok"):
        wait_sec = int(farm_state.get("max_wait", 0) or 0) if farm_state else 0
        if wait_sec > 0:
            next_panel_time = now + wait_sec + CD_BUFFER_SEC + random.uniform(5, 10)
        else:
            next_panel_time = now + RETRY_MAX_SEC + random.uniform(5, 10)
        _schedule_next_stargazer_action(next_panel_time)
        state["stargazer_last_action"] = "miniapp_waiting_panel"
        save_state()
        suffix = f"回查→{fmt_time_after(max(1, next_panel_time - now))}"
        summary = _format_stargazer_miniapp_action_summary(action_counts, item_deltas, star_choice, suffix)
        changed = bool(item_deltas) or any(int(count or 0) > 0 for count in action_counts.values())
        priority = "normal" if changed else "low"
        await send_audit_log(f"🔭 观星台 MiniApp：{summary or suffix}", scope="identity", priority=priority, limit=260)
        return True

    error_text = str(result.get("error") or "未知错误")
    if "修为不足" in error_text or "灵力不足" in error_text:
        delay = _get_stargazer_after_deep_retreat_delay(now)
        audit_text = "⏳ 观星台 MiniApp 修为/灵力不足，深闭 CD 后回查"
    else:
        delay = STARGAZER_MINIAPP_FAILURE_BACKOFF_SEC + random.uniform(30, 90)
        audit_text = "❌ 观星台 MiniApp 处理失败"
    _queue_stargazer_followup_action(now, "panel", delay)
    state["stargazer_last_action"] = "miniapp_error"
    save_state()
    await send_audit_log(f"{audit_text}：{error_text}→{fmt_time_after(delay)}", scope="identity", limit=260)
    return True


async def handle_stargazer_miniapp_entry(event, text, now, reply_to=None, matched_family=None, result_msg_id=0):
    identity_id = get_current_identity_id()
    manual_auth = _has_stargazer_miniapp_manual_auth(identity_id, now)

    launch = extract_stargazer_miniapp_launch(event, message_text=text)
    looks_like_entry = _looks_like_stargazer_miniapp_entry(text)
    if not launch and not looks_like_entry:
        return False
    if not launch and not _is_stargazer_panel_reply(reply_to, matched_family=matched_family):
        return False
    if not manual_auth and not state.get("stargazer_enabled"):
        return False

    was_paused = _is_stargazer_miniapp_paused()
    _pause_stargazer_legacy_chain(now, result_msg_id=result_msg_id or getattr(event, "id", 0) or 0)

    if not manual_auth:
        if not was_paused:
            await send_audit_log(
                "🔭 观星台已识别 MiniApp 入口，旧文本自动链路已暂停；未手动授权，不运行 WebView/HTTP。",
                scope="identity",
                priority="low",
                limit=190,
            )
        return True

    global_enabled = get_global_enabled()
    maintenance_miniapp_allowed = _miniapp_http_allowed_during_pause()
    identity_enabled = get_identity_enabled(identity_id)
    if (not global_enabled and not maintenance_miniapp_allowed) or not identity_enabled:
        revoke_stargazer_miniapp_manual_run(identity_id)
        reason = "全局暂停" if not global_enabled else "身份已停用"
        if not was_paused:
            await send_audit_log(
                f"🔭 观星台 MiniApp {reason}，旧文本自动链路已暂停；已跳过 WebView/HTTP 接管。",
                scope="identity",
                priority="low",
                limit=190,
            )
        return True

    if manual_auth:
        revoke_stargazer_miniapp_manual_run(identity_id)
    if not launch:
        if not was_paused:
            await send_audit_log(
                "🔭 观星台已识别 MiniApp 入口，旧文本自动链路已暂停；未拿到按钮，等待手动处理或下次入口。",
                scope="identity",
                priority="low",
                limit=180,
            )
        return True

    lock = _stargazer_miniapp_run_lock(identity_id)
    if lock.locked():
        await send_audit_log("🔭 观星台 MiniApp 已在执行，重复入口忽略。", scope="identity", priority="low", limit=160)
        return True

    star_choice = get_stargazer_star_choice()
    async with lock:
        if not was_paused:
            await send_audit_log(
                "🔭 观星台 MiniApp 接管入口，开始 WebView/HTTP 流程。"
                + ("（天尊维护暂停中，仅执行 MiniApp HTTP）" if maintenance_miniapp_allowed else ""),
                scope="identity",
                priority="low",
                limit=180,
            )
        capture_sink = _stargazer_miniapp_capture_store(now)
        capture_source = f"stargazer_runtime:{identity_id}:{int(result_msg_id or getattr(event, 'id', 0) or 0)}"
        result = await run_stargazer_miniapp_production_flow(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            star_choice=star_choice,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
        result = dict(result or {})
        result_data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else {}
        action_counts = result_data.get("action_counts") if isinstance(result_data.get("action_counts"), dict) else {}
        item_deltas = result_data.get("item_deltas") if isinstance(result_data.get("item_deltas"), dict) else {}
        collect_count = int(action_counts.get("collect", 0) or 0)
        if result.get("ok") and collect_count > 0:
            append_business_capture(
                capture_sink,
                adapter_key="stargazer",
                detail={"collect_count": collect_count, "items": item_deltas},
                source=capture_source,
                created_at=now,
            )
        return await _finish_stargazer_miniapp_result(result, now, star_choice=star_choice)


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
        await _queue_stargazer_action(now, "panel", audit_text="🌠 已收到安抚回复，回查观星台")
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
        await _queue_stargazer_action(now, "panel", audit_text="🌠 安抚完成，回查观星台")
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
    "authorize_stargazer_miniapp_manual_run",
    "get_stargazer_status_text",
    "handle_stargazer_collect_reply",
    "handle_stargazer_guide_reply",
    "handle_stargazer_miniapp_entry",
    "handle_stargazer_panel",
    "handle_stargazer_soothe_reply",
    "handle_stargazer_sync_reply",
    "revoke_stargazer_miniapp_manual_run",
]
