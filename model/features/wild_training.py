"""洞府 MiniApp 野外历练自动化。

野外历练已经迁移到洞府公共入口的「游历」页。这个模块只负责：

* 保留原有 UI 开关和「谨慎 / 均衡 / 深入」策略；
* 在服务端给出的 readyAt/remainingSeconds 到期后执行一次 MiniApp 动作；
* 在 MiniApp 动作前后衔接天星探索时间线。

这里不再发送 `.野外历练`，也不保留旧命令的消息回捞、补发或回复状态机。
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time

from ..config import CMD_TIANXING_PANEL, WILD_TRAINING_STRATEGIES
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_identity_enabled,
    get_miniapp_auto_config,
    get_miniapp_state_records,
    get_wild_training_strategy,
    is_cave_public_identity_available,
    set_wild_training_strategy,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining
from .cave_treasure_runtime import run_cave_public_wild_training
from .tianxing import (
    apply_tianxing_passive,
    build_tianxing_consume_window,
    build_tianxing_route_preflight_plan,
    looks_like_tianxing_route_result,
    mark_tianxing_route_result_unknown,
    normalize_tianxing_observation,
    normalize_tianxing_timeline_state,
    run_tianxing_consume_craft_prediction,
    run_tianxing_timeline_scheduler,
)


# These short intervals are only for Tianxing preparation or transport failure
# recovery. The actual wild-training cooldown is always supplied by MiniApp.
WILD_TRAINING_RECOVERY_SPREAD_MIN_SEC = 2 * 60
WILD_TRAINING_RECOVERY_SPREAD_MAX_SEC = 10 * 60
WILD_TRAINING_RETRY_MIN_SEC = 2 * 60
WILD_TRAINING_RETRY_MAX_SEC = 3 * 60
WILD_TRAINING_SCHEDULER_TIMEOUT_SEC = 30 * 60
WILD_TRAINING_TIANXING_PANEL_QUEUE_TIMEOUT_SEC = 45
WILD_TRAINING_TIANXING_CONSUME_ATTEMPT_GRACE_SEC = 10 * 60
WILD_TRAINING_MINIAPP_RUN_LEASE_SEC = 20 * 60
WILD_TRAINING_MINIAPP_FAILURE_BACKOFF_SEC = 30 * 60
WILD_TRAINING_MINIAPP_MAX_FAILURE_BACKOFF_SEC = 4 * 60 * 60
WILD_TRAINING_MINIAPP_ENTRY_RETRY_SEC = 60 * 60
WILD_TRAINING_MINIAPP_MIN_GAP_SEC = 30
WILD_TRAINING_MINIAPP_BUSY_RETRY_MIN_SEC = 2 * 60
WILD_TRAINING_MINIAPP_BUSY_RETRY_MAX_SEC = 5 * 60
WILD_TRAINING_DAILY_SPREAD_MIN_SEC = 30 * 60
WILD_TRAINING_DAILY_SPREAD_MAX_SEC = 90 * 60

_WILD_TRAINING_LOCKS = {}
_WILD_TRAINING_MINIAPP_TASKS = {}
_WILD_TRAINING_MINIAPP_RUN_LOCK = None
_WILD_TRAINING_MINIAPP_LAST_RUN_AT = 0.0


def _wild_training_lock():
    identity_id = int(get_current_identity_id() or 0)
    lock = _WILD_TRAINING_LOCKS.get(identity_id)
    if lock is None:
        lock = asyncio.Lock()
        _WILD_TRAINING_LOCKS[identity_id] = lock
    return lock


def normalize_wild_training_strategy(strategy):
    normalized = str(strategy or "").strip()
    return normalized if normalized in WILD_TRAINING_STRATEGIES else "谨慎"


def clear_wild_training_state(*, persist=False, keep_last_error=False):
    last_error = state.get("wild_training_last_error") if keep_last_error else ""
    state["next_wild_training_time"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_result"] = ""
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_completed_at"] = 0
    state["wild_training_last_error"] = last_error or ""
    state["wild_training_tianxing_prepare_retry_at"] = 0
    if persist:
        save_state()
    else:
        mark_dirty()


def schedule_wild_training_initial_check(now, *, persist=False, keep_last_error=True):
    clear_wild_training_state(persist=False, keep_last_error=keep_last_error)
    state["next_wild_training_time"] = float(now or time.time()) + random.uniform(10 * 60, 30 * 60)
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_wild_training_time"]


def get_wild_training_status_text():
    strategy = normalize_wild_training_strategy(get_wild_training_strategy())
    lines = [
        "🏞️ 野外历练 MiniApp",
        f"- 已启用：{'是' if state.get('wild_training_enabled') else '否'}",
        f"- 当前策略：{strategy}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_wild_training_time', 0))}（{fmt_remaining(state.get('next_wild_training_time', 0))}）",
        "- 执行出口：洞府公共入口 / 游历（不发送 .野外历练）",
        f"- MiniApp 失败次数：{int(state.get('wild_training_retry_count', 0) or 0)}",
        f"- 最近结果：{state.get('wild_training_last_result') or '无'}",
    ]
    if state.get("wild_training_last_error"):
        lines.append(f"- 最近异常：{state.get('wild_training_last_error')}")
    return "\n".join(lines)


async def apply_wild_training_strategy(strategy):
    normalized = normalize_wild_training_strategy(strategy)
    set_wild_training_strategy(get_current_identity_id(), normalized)
    save_state()
    return True, f"已保存野外历练策略：{normalized}"


def _has_active_tianxing_explore_change(now):
    if not state.get("tianxing_enabled"):
        return False
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    return (
        str(observed.get("current_change") or "").strip() == "探索"
        and float(observed.get("current_change_until", 0) or 0) > float(now or 0)
    )


def _has_active_tianxing_explore_prediction(now):
    if not state.get("tianxing_enabled"):
        return False
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if str(observed.get("current_prediction") or "").strip() != "探索":
        return False
    if float(observed.get("current_prediction_until", 0) or 0) <= float(now or 0):
        return False
    consumed_at = float(observed.get("prediction_consumed_at", 0) or 0)
    set_at = float(observed.get("current_prediction_set_at", 0) or 0)
    return consumed_at <= 0 or consumed_at < set_at


def _effective_wild_training_strategy(now):
    configured = normalize_wild_training_strategy(get_wild_training_strategy())
    if state.get("tianxing_enabled") and configured == "深入" and not _has_active_tianxing_explore_change(now):
        return "谨慎"
    return configured


def _schedule_tianxing_prepare_retry(now):
    state["wild_training_tianxing_prepare_retry_at"] = float(now) + random.uniform(
        WILD_TRAINING_RETRY_MIN_SEC,
        WILD_TRAINING_RETRY_MAX_SEC,
    )


def _clear_tianxing_prepare_retry():
    state["wild_training_tianxing_prepare_retry_at"] = 0


def _tianxing_prepare_retry_blocks(now):
    try:
        retry_at = float(state.get("wild_training_tianxing_prepare_retry_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        retry_at = 0.0
    return retry_at > float(now or 0)


def _schedule_retry(now):
    state["next_wild_training_time"] = float(now) + random.uniform(
        WILD_TRAINING_RETRY_MIN_SEC,
        WILD_TRAINING_RETRY_MAX_SEC,
    )


def _server_timestamp(value):
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if parsed > 10_000_000_000:
        parsed /= 1000.0
    return max(0.0, parsed)


def _daily_reset_spread_target(identity_id, wild):
    wild = wild if isinstance(wild, dict) else {}
    try:
        daily_remaining = int(wild.get("daily_remaining", -1))
        daily_count = int(wild.get("daily_count", -1))
        daily_limit = int(wild.get("daily_limit", -1))
    except (TypeError, ValueError, OverflowError):
        return 0.0, 0.0
    if daily_remaining != 0 or daily_limit <= 0 or daily_count < daily_limit:
        return 0.0, 0.0
    reset_at = _server_timestamp(wild.get("reset_at") or wild.get("ready_at"))
    if reset_at <= 0:
        return 0.0, 0.0
    spread_span = WILD_TRAINING_DAILY_SPREAD_MAX_SEC - WILD_TRAINING_DAILY_SPREAD_MIN_SEC
    digest = hashlib.sha256(f"{int(identity_id or 0)}:{int(reset_at)}".encode("ascii")).digest()
    spread = WILD_TRAINING_DAILY_SPREAD_MIN_SEC + int.from_bytes(digest[:8], "big") % (spread_span + 1)
    return reset_at, reset_at + spread


def _spread_server_next_time(identity_id, next_time, wild):
    _reset_at, spread_target = _daily_reset_spread_target(identity_id, wild)
    return max(float(next_time or 0), spread_target)


def reconcile_wild_training_daily_reset_spread(now):
    """Spread old exact-reset timers without overriding a later failure backoff."""
    identity_id = int(get_current_identity_id() or 0)
    record = dict(get_miniapp_state_records().get(f"{identity_id}:wild_training") or {})
    record_state = record.get("state") if isinstance(record.get("state"), dict) else {}
    wild = record_state.get("wild") if isinstance(record_state.get("wild"), dict) else {}
    reset_at, spread_target = _daily_reset_spread_target(identity_id, wild)
    try:
        next_time = float(state.get("next_wild_training_time", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    now = float(now or time.time())
    if (
        spread_target <= now
        or next_time <= 0
        or next_time >= spread_target
        or next_time > reset_at + 5 * 60
    ):
        return False
    state["next_wild_training_time"] = spread_target
    mark_dirty()
    return True


def _schedule_miniapp_busy_retry(now, reason="洞府公共入口繁忙"):
    state["next_wild_training_time"] = float(now) + random.uniform(
        WILD_TRAINING_MINIAPP_BUSY_RETRY_MIN_SEC,
        WILD_TRAINING_MINIAPP_BUSY_RETRY_MAX_SEC,
    )
    state["wild_training_last_result"] = "MiniApp 串行等待"
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = ""
    save_state()
    return state["next_wild_training_time"]


def _recent_craft_prediction_consume_attempt_for_due(due_at, now):
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    craft_farm = timeline.get("craft_farm") if isinstance(timeline.get("craft_farm"), dict) else {}
    if str(craft_farm.get("last_action") or "").strip() not in {
        "consume_craft_prediction",
        "consume_craft_prediction_calibration",
    }:
        return False
    updated_at = float(craft_farm.get("updated_at", 0) or 0)
    return updated_at > 0 and updated_at <= float(now or 0) + 1 and updated_at >= float(due_at or now) - WILD_TRAINING_TIANXING_CONSUME_ATTEMPT_GRACE_SEC


def _tianxing_timeline_prepare_failed(timeline_result, followup):
    followup_stage = str((followup or {}).get("stage") or "").strip()
    phase = str((timeline_result or {}).get("phase") or "").strip()
    return followup_stage in {"timeline_waiting", "timeline_waiting_change_fate"} and phase in {
        "ack_timeout", "calibrating", "send_blocked", "blocked_replan",
        "panel_calibration_timeout_replan", "need_tianji_for_change", "observe_only",
    }


async def _send_tianxing_panel_calibration(now, reason):
    if not state.get("tianxing_enabled"):
        _schedule_retry(now)
        state["wild_training_last_error"] = str(reason or "状态不明，短退避后复查")
        save_state()
        return False
    try:
        msg = await send_game_command(
            CMD_TIANXING_PANEL,
            track=True,
            priority="reactive",
            source_module="天星宗",
            op_id=f"wild-training-panel-calibration-{int(now)}",
            queue_timeout=WILD_TRAINING_TIANXING_PANEL_QUEUE_TIMEOUT_SEC,
        )
    except asyncio.CancelledError:
        raise
    _schedule_tianxing_prepare_retry(now)
    _schedule_retry(now)
    state["wild_training_last_result"] = "野外历练状态不明，等待天机盘校准"
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = str(reason or "天机盘校准未确认")
    save_state()
    return bool(msg)


async def _prepare_wild_training_tianxing_route(now, *, due_at=0):
    if not state.get("tianxing_enabled"):
        return True
    due_at = float(due_at or now)
    preflight = build_tianxing_route_preflight_plan(
        "探索",
        reason="野外历练 MiniApp",
        now=now,
        require_change_fate=True,
    )
    if preflight.get("route_allowed"):
        _clear_tianxing_prepare_retry()
        return True
    identity_id = int(get_current_identity_id() or 0)
    if not get_identity_enabled(identity_id) and is_cave_public_identity_available(identity_id):
        _schedule_retry(now)
        _schedule_tianxing_prepare_retry(now)
        state["wild_training_last_result"] = "频道身份冻结，等待天星探索前置恢复"
        state["wild_training_last_result_at"] = 0
        state["wild_training_last_error"] = "公共入口可用，但推命/改命群命令当前不可发送"
        save_state()
        return False
    if str(preflight.get("stage") or "") == "prediction_conflict":
        if due_at <= now and _recent_craft_prediction_consume_attempt_for_due(due_at, now):
            await _send_tianxing_panel_calibration(now, "炼制推命消费后需查盘确认探索路线")
            return False
        consume_result = await run_tianxing_consume_craft_prediction(now, reason="野外 MiniApp 前消费炼制推命")
        if consume_result.get("active"):
            _schedule_retry(now)
            _schedule_tianxing_prepare_retry(now)
            state["wild_training_last_result"] = f"天星先消费炼制推命：{consume_result.get('stage') or 'waiting'}"
            state["wild_training_last_error"] = ""
            save_state()
            return False
    blocked_until = float(preflight.get("blocked_until", 0) or 0)
    if blocked_until > now:
        _schedule_retry(now)
        state["wild_training_last_error"] = str(preflight.get("reason") or "天星探索路线暂不可用")
        save_state()
        return False
    if not preflight.get("timeline_required"):
        _schedule_retry(now)
        state["wild_training_last_error"] = str(preflight.get("reason") or "天星探索前置未满足")
        save_state()
        return False
    windows = build_tianxing_consume_window(
        "探索",
        now=now,
        due_at=max(due_at, now),
        reason="野外历练 MiniApp",
        require_change_fate=True,
    )
    if not windows:
        await _send_tianxing_panel_calibration(now, "野外 MiniApp 缺少天星消费窗口")
        return False
    timeline_result = await run_tianxing_timeline_scheduler(now, windows=windows)
    followup = build_tianxing_route_preflight_plan("探索", reason="野外历练 MiniApp", now=now, require_change_fate=True)
    if followup.get("route_allowed"):
        _clear_tianxing_prepare_retry()
        return True
    phase = str(timeline_result.get("phase") or "").strip()
    if due_at <= now and phase == "need_tianji_for_change" and _has_active_tianxing_explore_prediction(now):
        _clear_tianxing_prepare_retry()
        state["wild_training_last_result"] = "天机不足，按谨慎策略执行 MiniApp 野外"
        state["wild_training_last_error"] = ""
        save_state()
        return True
    if due_at <= now and _tianxing_timeline_prepare_failed(timeline_result, followup):
        await _send_tianxing_panel_calibration(now, "天星探索前置确认失败")
        return False
    _schedule_retry(now)
    _schedule_tianxing_prepare_retry(now)
    state["wild_training_last_result"] = f"天星时间线：{phase or 'waiting'}"
    state["wild_training_last_error"] = str(preflight.get("reason") or "")
    save_state()
    return False


async def _guard_deep_wild_training_send(now):
    if not state.get("tianxing_enabled"):
        return True
    preflight = build_tianxing_route_preflight_plan("探索", reason="野外 MiniApp 深入前复核", now=now, require_change_fate=True)
    if preflight.get("route_allowed"):
        return True
    await _send_tianxing_panel_calibration(now, preflight.get("reason") or "深入前置未确认")
    await send_audit_log("🌌 MiniApp 野外深入前置未确认，本轮不执行。", scope="identity", priority="high")
    return False


def _wild_training_public_entry_urls():
    config = get_miniapp_auto_config()
    values = config.get("cave_public_entry_urls") or config.get("cave_public_entry_url") or ()
    if isinstance(values, str):
        values = [values]
    urls = []
    for value in values or ():
        value = str(value or "").strip()
        if value and value not in urls:
            urls.append(value)
    return urls


def _set_miniapp_failure(now, message, *, entry_missing=False):
    retry_count = int(state.get("wild_training_retry_count", 0) or 0) + 1
    state["wild_training_retry_count"] = retry_count
    delay = WILD_TRAINING_MINIAPP_ENTRY_RETRY_SEC if entry_missing else min(
        WILD_TRAINING_MINIAPP_MAX_FAILURE_BACKOFF_SEC,
        WILD_TRAINING_MINIAPP_FAILURE_BACKOFF_SEC * (2 ** min(retry_count - 1, 3)),
    )
    state["next_wild_training_time"] = float(now) + delay
    state["wild_training_last_result"] = "MiniApp 未完成"
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = str(message or "MiniApp 野外历练失败")[:240]
    save_state()
    return state["next_wild_training_time"]


def _wild_training_entry_failure_can_fallback(result):
    extra = result.get("extra") if isinstance(result.get("extra"), dict) else {}
    if extra.get("acted"):
        return False
    phase = str(extra.get("phase") or "")
    message = str(result.get("message") or "").casefold()
    return phase in {"session_failed", "state_missing"} or any(
        marker in message for marker in ("入口", "身份读取失败", "session", "timeout", "http 5", "connection")
    )


def _wild_training_result_text(action_result):
    action_result = action_result if isinstance(action_result, dict) else {}
    return "\n".join(
        str(action_result.get(key) or "").strip()
        for key in ("title", "message", "rawMessage")
        if str(action_result.get(key) or "").strip()
    )


async def _apply_miniapp_result(result, now):
    extra = result.get("extra") if isinstance(result.get("extra"), dict) else {}
    wild = extra.get("wild") if isinstance(extra.get("wild"), dict) else {}
    next_time = _spread_server_next_time(
        get_current_identity_id(),
        float(extra.get("next_time", 0) or 0),
        wild,
    )
    if result.get("ok") and not extra.get("acted"):
        state["wild_training_retry_count"] = 0
        state["next_wild_training_time"] = max(float(now) + 60, next_time or float(now) + 30 * 60)
        state["wild_training_last_result"] = "MiniApp 冷却状态已同步"
        state["wild_training_last_result_at"] = float(now)
        state["wild_training_last_error"] = ""
        save_state()
        return "cooldown"
    action_result = extra.get("action_result") if isinstance(extra.get("action_result"), dict) else {}
    if result.get("ok") and extra.get("completed"):
        strategy = normalize_wild_training_strategy(extra.get("strategy") or get_wild_training_strategy())
        state["wild_training_retry_count"] = 0
        state["next_wild_training_time"] = max(float(now) + 60, next_time or float(now) + 30 * 60)
        state["wild_training_last_result"] = f"{strategy}｜{str(result.get('message') or 'MiniApp 野外历练完成').strip()}"[:300]
        state["wild_training_last_result_at"] = float(now)
        state["wild_training_last_completed_at"] = float(now)
        state["wild_training_last_error"] = ""
        raw_text = _wild_training_result_text(action_result)
        if state.get("tianxing_enabled"):
            changed = bool(raw_text and apply_tianxing_passive(raw_text, now=now, family="wild_training"))
            if not changed:
                mark_tianxing_route_result_unknown(
                    "探索",
                    now=now,
                    reason="MiniApp 野外已结算，但回包未提供可识别的推命/改命消费文案",
                )
        save_state()
        if raw_text and looks_like_tianxing_route_result(raw_text):
            await send_audit_log(
                f"🌌 天星探索结果｜野外历练：{state.get('wild_training_last_result')}",
                scope="identity",
                priority="high",
                limit=260,
            )
        return "completed"
    if extra.get("acted") and extra.get("transport_ok") and next_time > float(now):
        state["wild_training_retry_count"] = 0
        state["next_wild_training_time"] = next_time
        state["wild_training_last_result"] = "MiniApp 返回冷却/次数限制"
        state["wild_training_last_result_at"] = float(now)
        state["wild_training_last_error"] = str(result.get("message") or "野外历练当前不可执行")[:240]
        save_state()
        return "cooldown"
    if not extra.get("acted") and "操作执行中" in str(result.get("message") or ""):
        _schedule_miniapp_busy_retry(now, result.get("message"))
        return "busy"
    if extra.get("acted") and not extra.get("transport_ok") and state.get("tianxing_enabled"):
        mark_tianxing_route_result_unknown(
            "探索",
            now=now,
            reason="MiniApp 野外动作回包状态未知，下一轮前重新校准天星路线",
        )
    _set_miniapp_failure(now, result.get("message") or "MiniApp 野外历练未完成")
    return "failed"


def _wild_training_miniapp_worker_busy():
    if any(not task.done() for task in _WILD_TRAINING_MINIAPP_TASKS.values()):
        return True
    return bool(_WILD_TRAINING_MINIAPP_RUN_LOCK and _WILD_TRAINING_MINIAPP_RUN_LOCK.locked())


async def _run_wild_training_miniapp_worker(identity_id, urls, due_at):
    global _WILD_TRAINING_MINIAPP_LAST_RUN_AT
    result = {"ok": False, "message": "无公共洞府入口", "extra": {"phase": "entry_missing"}}
    try:
        global _WILD_TRAINING_MINIAPP_RUN_LOCK
        if _WILD_TRAINING_MINIAPP_RUN_LOCK is None:
            _WILD_TRAINING_MINIAPP_RUN_LOCK = asyncio.Lock()
        async with _WILD_TRAINING_MINIAPP_RUN_LOCK:
            with use_identity(identity_id):
                worker_now = time.time()
                if not state.get("wild_training_enabled"):
                    return
                if not await _prepare_wild_training_tianxing_route(worker_now, due_at=due_at):
                    return
                strategy = _effective_wild_training_strategy(worker_now)
                if strategy == "深入" and not await _guard_deep_wild_training_send(worker_now):
                    return
            gap = time.time() - float(_WILD_TRAINING_MINIAPP_LAST_RUN_AT or 0)
            if gap < WILD_TRAINING_MINIAPP_MIN_GAP_SEC:
                await asyncio.sleep(WILD_TRAINING_MINIAPP_MIN_GAP_SEC - gap)
            for url in list(urls)[:3]:
                result = await run_cave_public_wild_training(identity_id, url, strategy, now=time.time())
                if result.get("ok") or not _wild_training_entry_failure_can_fallback(result):
                    break
            _WILD_TRAINING_MINIAPP_LAST_RUN_AT = time.time()
        with use_identity(identity_id):
            outcome = await _apply_miniapp_result(result, time.time())
            if outcome == "completed":
                await send_audit_log(
                    f"🏞️ MiniApp 野外历练结果｜{state.get('wild_training_last_result') or '完成'}｜下次 {fmt_abs_ts(state.get('next_wild_training_time', 0))}",
                    scope="identity",
                    send_as_id=identity_id,
                    priority="normal",
                    limit=320,
                )
            elif outcome == "failed":
                await send_audit_log(
                    f"⚠️ MiniApp 野外历练未完成：{state.get('wild_training_last_error') or 'unknown'}｜复查 {fmt_abs_ts(state.get('next_wild_training_time', 0))}",
                    scope="identity",
                    send_as_id=identity_id,
                    priority="normal",
                    limit=300,
                )
    except Exception as exc:
        with use_identity(identity_id):
            next_time = _set_miniapp_failure(time.time(), f"{type(exc).__name__}: {exc}")
        console_log(f"⚠️ MiniApp 野外后台异常：{type(exc).__name__}: {exc}，复查→{fmt_abs_ts(next_time)}", scope="identity", limit=260)


def _launch_wild_training_miniapp_worker(identity_id, urls, due_at):
    identity_id = int(identity_id or 0)
    if identity_id <= 0 or _wild_training_miniapp_worker_busy():
        return False
    task = asyncio.create_task(_run_wild_training_miniapp_worker(identity_id, list(urls), float(due_at or time.time())))
    _WILD_TRAINING_MINIAPP_TASKS[identity_id] = task

    def _done(done_task):
        _WILD_TRAINING_MINIAPP_TASKS.pop(identity_id, None)
        try:
            done_task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            console_log(f"⚠️ MiniApp 野外后台异常：{type(exc).__name__}: {exc}", scope="identity", limit=240)

    task.add_done_callback(_done)
    return True


async def _run_wild_training_miniapp_scheduler_unlocked(now):
    if not state.get("wild_training_enabled"):
        return
    now = float(now or time.time())
    reconcile_wild_training_daily_reset_spread(now)
    try:
        next_time = float(state.get("next_wild_training_time", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        next_time = 0.0
    if next_time <= 0:
        schedule_wild_training_initial_check(now, persist=False, keep_last_error=True)
        save_state()
        return
    if next_time > now:
        if _wild_training_miniapp_worker_busy():
            return
        windows = build_tianxing_consume_window(
            "探索", now=now, due_at=next_time, reason="野外历练", require_change_fate=True
        )
        if windows and not _tianxing_prepare_retry_blocks(now):
            await _prepare_wild_training_tianxing_route(now, due_at=next_time)
        return
    urls = _wild_training_public_entry_urls()
    if not urls:
        _set_miniapp_failure(now, "缺少洞府公共入口", entry_missing=True)
        return
    identity_id = int(get_current_identity_id() or 0)
    if _wild_training_miniapp_worker_busy():
        _schedule_miniapp_busy_retry(now)
        return
    due_at = next_time
    strategy = normalize_wild_training_strategy(get_wild_training_strategy())
    state["next_wild_training_time"] = now + WILD_TRAINING_MINIAPP_RUN_LEASE_SEC
    state["wild_training_last_result"] = f"MiniApp 串行执行：{strategy}"
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = ""
    save_state()
    if _launch_wild_training_miniapp_worker(identity_id, urls, due_at):
        console_log(f"🏞️ MiniApp 野外开始串行执行：{strategy}", scope="identity", limit=220)
    else:
        _schedule_miniapp_busy_retry(now)


async def run_wild_training_scheduler(now):
    async with _wild_training_lock():
        return await _run_wild_training_miniapp_scheduler_unlocked(now)


__all__ = [
    "WILD_TRAINING_RECOVERY_SPREAD_MIN_SEC",
    "WILD_TRAINING_RECOVERY_SPREAD_MAX_SEC",
    "WILD_TRAINING_RETRY_MIN_SEC",
    "WILD_TRAINING_RETRY_MAX_SEC",
    "WILD_TRAINING_SCHEDULER_TIMEOUT_SEC",
    "apply_wild_training_strategy",
    "clear_wild_training_state",
    "get_wild_training_status_text",
    "normalize_wild_training_strategy",
    "run_wild_training_scheduler",
    "schedule_wild_training_initial_check",
    "reconcile_wild_training_daily_reset_spread",
    "_tianxing_prepare_retry_blocks",
]
