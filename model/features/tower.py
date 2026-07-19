"""琉璃问心塔 scheduler.

The tower moved to the dwelling MiniApp.  This module intentionally keeps the
old identity switch, daily window, and completion timer, but has no text-command
send or reply-retry path.  A run is one serialized public-entry HTTP workflow.
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone

from ..miniapp_state import get_miniapp_state_snapshot
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log
from ..state import (
    get_current_identity_id,
    format_window_text,
    get_miniapp_auto_config,
    get_module_window_hours,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key, schedule_next_tower, schedule_next_tower_after_completion
from .cave_treasure_runtime import run_cave_public_tower


TOWER_MINIAPP_RUN_LEASE_SEC = 30 * 60
TOWER_MINIAPP_FAILURE_BACKOFF_SEC = 30 * 60
TOWER_MINIAPP_MAX_FAILURE_BACKOFF_SEC = 4 * 60 * 60
TOWER_MINIAPP_ENTRY_RETRY_SEC = 60 * 60
TOWER_MINIAPP_MIN_GAP_SEC = 5
TOWER_MINIAPP_UPSTREAM_CIRCUIT_SEC = 10 * 60

_TOWER_TASKS = {}
_TOWER_RUN_LOCK = None
_TOWER_LAST_RUN_AT = 0.0
_TOWER_UPSTREAM_CIRCUIT_UNTIL = 0.0
_TOWER_PREFERRED_ENTRY_INDEX = 0


def _tower_run_lock():
    global _TOWER_RUN_LOCK
    if _TOWER_RUN_LOCK is None:
        _TOWER_RUN_LOCK = asyncio.Lock()
    return _TOWER_RUN_LOCK


def _read_timestamp(field_name):
    raw = state.get(field_name, 0)
    if raw in (None, ""):
        return 0.0, False
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return 0.0, True
    if not math.isfinite(value):
        return 0.0, True
    return value, False


def _is_tower_window_time(ts):
    start_hour_utc, end_hour_utc = get_module_window_hours("闯塔")
    current = datetime.fromtimestamp(float(ts), timezone.utc)
    start = current.replace(hour=start_hour_utc, minute=0, second=0, microsecond=0)
    end = current.replace(hour=end_hour_utc, minute=0, second=0, microsecond=0)
    return start <= current < end


def _configured_entry_urls():
    config = get_miniapp_auto_config()
    values = config.get("cave_public_entry_urls") or config.get("cave_public_entry_url") or ()
    if isinstance(values, str):
        values = [values]
    result = []
    for value in values or ():
        value = str(value or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _is_upstream_failure(message):
    text = str(message or "").casefold()
    return any(item in text for item in (
        "http 5", "server_error", "timeout", "connection reset", "connection refused",
        "cannot connect", "remote disconnected", "上游", "会话初始化失败",
    ))


def _is_entry_health_failure(message):
    text = str(message or "")
    return _is_upstream_failure(text) or any(item in text for item in (
        "入口 URL 无效", "身份读取失败", "入口未返回", "动态入口获取失败", "未开放琉璃问心塔",
    ))


def _ordered_entry_urls(urls):
    if not urls:
        return []
    index = max(0, min(len(urls) - 1, int(_TOWER_PREFERRED_ENTRY_INDEX or 0)))
    return urls[index:] + urls[:index]


def _schedule_next_day(now):
    return schedule_next_tower_after_completion(now, persist=False)


def _clear_legacy_waiting():
    state["last_tower_msg_id"] = 0
    state["tower_reply_due_at"] = 0


def _mark_done_today(now):
    state["last_tower_day"] = get_day_key(now)
    state["tower_retry_count"] = 0
    _clear_legacy_waiting()
    next_ts = _schedule_next_day(now)
    save_state()
    return next_ts


def _set_failure_retry(now, *, entry_missing=False):
    retry_count = int(state.get("tower_retry_count", 0) or 0) + 1
    state["tower_retry_count"] = retry_count
    _clear_legacy_waiting()
    if entry_missing:
        delay = TOWER_MINIAPP_ENTRY_RETRY_SEC
    else:
        delay = min(
            TOWER_MINIAPP_MAX_FAILURE_BACKOFF_SEC,
            TOWER_MINIAPP_FAILURE_BACKOFF_SEC * (2 ** min(retry_count - 1, 3)),
        )
    state["next_tower_time"] = float(now) + delay
    save_state()
    return state["next_tower_time"]


def _latest_tower_record():
    snapshot = get_miniapp_state_snapshot(game_key="tower")
    rows = snapshot.get("rows") or []
    current_identity = int(get_current_identity_id() or 0)
    for row in rows:
        if current_identity and int(row.get("identity_id", 0) or 0) == current_identity:
            return row.get("state") or {}
    return rows[-1].get("state") if rows else {}


def get_tower_status_text():
    today_key = get_day_key()
    latest = _latest_tower_record()
    lines = [
        "🗼 闯塔 MiniApp",
        f"- 今日是否已完成：{'是' if state.get('last_tower_day') == today_key else '否'}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_tower_time', 0))}（{fmt_remaining(state.get('next_tower_time', 0))}）",
        f"- 执行窗口：{format_window_text('闯塔')}",
        f"- 上次 MiniApp 启动：{fmt_abs_ts(state.get('last_tower_command_sent_at', 0))}",
        f"- 最近阶段：{latest.get('phase') or '未运行'}｜通过 {latest.get('cleared_count', 0)} 层",
        f"- 最近收益：修为 +{(latest.get('gains') or {}).get('修为', 0)}｜塔印 +{(latest.get('gains') or {}).get('塔印', 0)}",
        "- 自动链：公共洞府入口 → pagoda start → challenge（不自动重铸）",
    ]
    return "\n".join(lines)


def _normalize_tower_schedule(now):
    next_ts, next_dirty = _read_timestamp("next_tower_time")
    lease_ts, lease_dirty = _read_timestamp("tower_reply_due_at")
    if next_dirty or lease_dirty:
        return next_ts, True
    if state.get("last_tower_day") == get_day_key(now):
        if next_ts <= now or get_day_key(next_ts) == get_day_key(now):
            return _schedule_next_day(now), True
        return next_ts, True
    if next_ts <= 0:
        schedule_next_tower(now, persist=False)
        mark_dirty()
        return float(state.get("next_tower_time", 0) or 0), True
    if lease_ts > now:
        return lease_ts, True
    if not _is_tower_window_time(next_ts):
        schedule_next_tower(now, persist=False)
        mark_dirty()
        return float(state.get("next_tower_time", 0) or 0), True
    if now >= next_ts and not _is_tower_window_time(now):
        schedule_next_tower(now, persist=False)
        mark_dirty()
        return float(state.get("next_tower_time", 0) or 0), True
    return next_ts, False


async def _run_tower_worker(identity_id, urls, *, scheduled_at):
    global _TOWER_LAST_RUN_AT, _TOWER_UPSTREAM_CIRCUIT_UNTIL, _TOWER_PREFERRED_ENTRY_INDEX
    try:
        async with _tower_run_lock():
            now = time.time()
            if now < _TOWER_UPSTREAM_CIRCUIT_UNTIL:
                with use_identity(identity_id):
                    _set_failure_retry(now)
                return
            gap = now - _TOWER_LAST_RUN_AT
            if gap < TOWER_MINIAPP_MIN_GAP_SEC:
                await asyncio.sleep(TOWER_MINIAPP_MIN_GAP_SEC - gap)
            ordered = _ordered_entry_urls(urls)
            result = {"ok": False, "message": "无公共入口", "extra": {}}
            for offset, url in enumerate(ordered[:3]):
                result = await run_cave_public_tower(identity_id, url, now=time.time())
                if result.get("ok") or not _is_entry_health_failure(result.get("message")):
                    if result.get("ok"):
                        _TOWER_PREFERRED_ENTRY_INDEX = (int(_TOWER_PREFERRED_ENTRY_INDEX or 0) + offset) % max(1, len(urls))
                    break
                if _is_upstream_failure(result.get("message")):
                    _TOWER_UPSTREAM_CIRCUIT_UNTIL = time.time() + TOWER_MINIAPP_UPSTREAM_CIRCUIT_SEC
                    break
            _TOWER_LAST_RUN_AT = time.time()
        with use_identity(identity_id):
            if result.get("ok"):
                next_ts = _mark_done_today(time.time())
                console_log(f"🗼 闯塔 MiniApp 完成，下一次→{fmt_abs_ts(next_ts)}", scope="identity", limit=220)
            else:
                entry_missing = "公共入口" in str(result.get("message") or "")
                next_ts = _set_failure_retry(time.time(), entry_missing=entry_missing)
                await send_audit_log(
                    f"⚠️ 闯塔 MiniApp 未完成：{str(result.get('message') or 'unknown')[:180]}，延后至 {fmt_abs_ts(next_ts)}",
                    scope="identity",
                    send_as_id=identity_id,
                    priority="normal",
                    limit=240,
                )
    except Exception as exc:
        with use_identity(identity_id):
            next_ts = _set_failure_retry(time.time())
        console_log(f"⚠️ 闯塔 MiniApp 后台异常：{type(exc).__name__}: {exc}，延后至 {fmt_abs_ts(next_ts)}", scope="identity", limit=240)


def _launch_tower_worker(identity_id, urls, *, scheduled_at):
    identity_id = int(identity_id or 0)
    if identity_id <= 0 or identity_id in _TOWER_TASKS:
        return False
    task = asyncio.create_task(_run_tower_worker(identity_id, list(urls), scheduled_at=scheduled_at))
    _TOWER_TASKS[identity_id] = task

    def _done(done_task):
        _TOWER_TASKS.pop(identity_id, None)
        try:
            done_task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            console_log(f"⚠️ 闯塔 MiniApp 任务异常：{type(exc).__name__}: {exc}", scope="identity", limit=220)

    task.add_done_callback(_done)
    return True


async def run_tower_scheduler(now):
    """Queue one serialized MiniApp run when the legacy tower window is due."""
    if not state.get("tower_enabled"):
        return
    next_ts, should_return = _normalize_tower_schedule(float(now or time.time()))
    if should_return or float(now or time.time()) < next_ts:
        return
    identity_id = int(get_current_identity_id() or 0)
    urls = _configured_entry_urls()
    if not urls:
        next_retry = _set_failure_retry(float(now or time.time()), entry_missing=True)
        console_log(f"⚠️ 闯塔 MiniApp 缺少洞府公共入口，延后至 {fmt_abs_ts(next_retry)}", scope="identity", limit=220)
        return
    if time.time() < _TOWER_UPSTREAM_CIRCUIT_UNTIL:
        state["next_tower_time"] = _TOWER_UPSTREAM_CIRCUIT_UNTIL
        state["tower_reply_due_at"] = _TOWER_UPSTREAM_CIRCUIT_UNTIL
        mark_dirty()
        return
    lease_at = time.time() + TOWER_MINIAPP_RUN_LEASE_SEC
    state["last_tower_command_sent_at"] = time.time()
    state["tower_reply_due_at"] = lease_at
    state["next_tower_time"] = lease_at
    save_state()
    if not _launch_tower_worker(identity_id, urls, scheduled_at=float(now or time.time())):
        return
    console_log("🗼 闯塔 MiniApp 已排队：洞府公共入口，等待串行执行", scope="identity", limit=220)


__all__ = ["get_tower_status_text", "run_tower_scheduler"]
