import asyncio
import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from ..config import CD_BUFFER_SEC, CMD_WILD_TRAINING, MESSAGES_DIR, TZ_LOCAL, WILD_TRAINING_STRATEGIES
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_wild_training_strategy, set_wild_training_strategy, state
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .dungeon_quiet import get_dungeon_quiet_reason, get_dungeon_quiet_until, is_dungeon_quiet_active
from .tianxing import (
    apply_tianxing_passive,
    build_tianxing_consume_window,
    build_tianxing_route_preflight_plan,
    looks_like_tianxing_route_result,
    normalize_tianxing_observation,
    run_tianxing_consume_craft_prediction,
    run_tianxing_timeline_scheduler,
)


WILD_TRAINING_CYCLE_MIN_SEC = 2 * 3600
WILD_TRAINING_CYCLE_MAX_SEC = 2 * 3600
WILD_TRAINING_RECOVERY_SPREAD_MIN_SEC = 2 * 60
WILD_TRAINING_RECOVERY_SPREAD_MAX_SEC = 10 * 60
WILD_TRAINING_REPLY_TIMEOUT_SEC = 10 * 60
WILD_TRAINING_RETRY_MIN_SEC = 2 * 60
WILD_TRAINING_RETRY_MAX_SEC = 3 * 60
WILD_TRAINING_DUNGEON_QUIET_RESUME_MIN_SEC = 10
WILD_TRAINING_DUNGEON_QUIET_RESUME_MAX_SEC = 40
WILD_TRAINING_LOG_REPLAY_LOOKBACK_SEC = 20 * 60
WILD_TRAINING_LOG_REPLAY_LOOKAHEAD_SEC = 2 * 60
WILD_TRAINING_RECENT_RESULT_GUARD_GRACE_SEC = 60
WILD_TRAINING_TITLE = "【野外历练"
WILD_TRAINING_RESULT_TITLES = (
    "【野外历练 · 妖兽遭遇】",
    "【野外历练 · 负伤而归】",
    "【野外历练 · 灵机暗藏】",
    "【野外历练 · 改命脱险】",
)
WILD_TRAINING_RESULT_MARKERS = ("【野外历练", "修为", "获得", "带回", "负伤", "妖兽", "灵机", "改命")
WILD_TRAINING_CD_KEYWORDS = ("山中灵机未复", "冷却", "请在", "等待")
RE_WILD_TRAINING_XIUWEI = re.compile(r"修为(?:折损)?\s*([+-]\s*[\d,]+)")
RE_WILD_TRAINING_REWARD = re.compile(r"(?:获得|带回了?)\s+【([^】]+)】x(\d+)")
RE_WILD_TRAINING_START_STRATEGY = re.compile(r"选择【([^】]+)】")
RE_WILD_TRAINING_TIANJI = re.compile(r"天机值\s*([+-]\s*\d+)")
RE_WILD_TRAINING_CONTRIB = re.compile(r"宗门贡献\s*([+-]\s*\d+)")
_WILD_TRAINING_LOCKS = {}


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


def get_wild_training_command(strategy=None):
    strategy = normalize_wild_training_strategy(strategy or get_wild_training_strategy())
    return f"{CMD_WILD_TRAINING} {strategy}"


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
    if str(observed.get("prediction_consumed_route") or "").strip() != "探索":
        return True
    consumed_at = float(observed.get("prediction_consumed_at", 0) or 0)
    set_at = float(observed.get("current_prediction_set_at", 0) or 0)
    return consumed_at <= 0 or consumed_at < set_at


def _effective_wild_training_strategy(now):
    if state.get("tianxing_enabled"):
        return "深入" if _has_active_tianxing_explore_change(now) else "谨慎"
    return normalize_wild_training_strategy(get_wild_training_strategy())


def _schedule_next(now):
    state["next_wild_training_time"] = float(now + random.uniform(WILD_TRAINING_CYCLE_MIN_SEC, WILD_TRAINING_CYCLE_MAX_SEC))
    state["wild_training_retry_count"] = 0
    return state["next_wild_training_time"]


def _schedule_retry(now):
    state["next_wild_training_time"] = float(now + random.uniform(WILD_TRAINING_RETRY_MIN_SEC, WILD_TRAINING_RETRY_MAX_SEC))


def _schedule_tianxing_prepare_retry(now):
    state["wild_training_tianxing_prepare_retry_at"] = float(now + random.uniform(WILD_TRAINING_RETRY_MIN_SEC, WILD_TRAINING_RETRY_MAX_SEC))


def _clear_tianxing_prepare_retry():
    state["wild_training_tianxing_prepare_retry_at"] = 0


def _tianxing_prepare_retry_blocks(now):
    try:
        retry_at = float(state.get("wild_training_tianxing_prepare_retry_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        retry_at = 0.0
    return retry_at > float(now or 0)


def _schedule_after_dungeon_quiet(now):
    if not is_dungeon_quiet_active(now):
        return 0.0
    until = get_dungeon_quiet_until()
    next_time = float(until + random.uniform(WILD_TRAINING_DUNGEON_QUIET_RESUME_MIN_SEC, WILD_TRAINING_DUNGEON_QUIET_RESUME_MAX_SEC))
    state["next_wild_training_time"] = next_time
    return next_time


def _is_completed_wild_training_summary(summary):
    text = str(summary or "").strip()
    if not text:
        return False
    if text.startswith(("已发送", "已出发")):
        return False
    if text in {"冷却中"}:
        return False
    if any(marker in text for marker in ("发送失败", "回复超时", "补发", "冷却")):
        return False
    return True


def _guard_recent_completed_result(now):
    last_result_at = float(state.get("wild_training_last_result_at", 0) or 0)
    if last_result_at <= 0:
        return False
    if not _is_completed_wild_training_summary(state.get("wild_training_last_result")):
        return False
    if float(now or 0) - last_result_at >= WILD_TRAINING_CYCLE_MIN_SEC - WILD_TRAINING_RECENT_RESULT_GUARD_GRACE_SEC:
        return False
    _schedule_next(last_result_at)
    state["wild_training_last_error"] = "野外历练结果后计时器异常，已按正常周期顺延"
    save_state()
    console_log(f"🏞️ {state['wild_training_last_error']}→{fmt_abs_ts(state['next_wild_training_time'])}", scope="identity")
    return True


async def _defer_wild_training_for_dungeon_quiet(now, *, action):
    next_time = _schedule_after_dungeon_quiet(now)
    if next_time <= 0:
        return False
    reason = get_dungeon_quiet_reason() or "副本静场令"
    state["wild_training_last_error"] = f"野外历练{action}撞到{reason}，延后至 {fmt_abs_ts(next_time)}"
    save_state()
    await send_audit_log(f"🤫 {state['wild_training_last_error']}。", scope="identity")
    return True


def _parse_message_log_ts(raw_ts):
    ts_text = str(raw_ts or "").strip()
    if not ts_text:
        return 0.0
    ts_text = ts_text.replace(" UTC+8", "")
    try:
        return datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except ValueError:
        return 0.0


def _iter_message_log_entries_between(start_ts, end_ts):
    try:
        start_day = datetime.fromtimestamp(float(start_ts), TZ_LOCAL).date()
        end_day = datetime.fromtimestamp(float(end_ts), TZ_LOCAL).date()
    except (TypeError, ValueError, OSError):
        return

    day = start_day
    while day <= end_day:
        log_path = Path(MESSAGES_DIR) / f"{day.isoformat()}.log"
        if log_path.exists():
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass
        day += timedelta(days=1)


def clear_wild_training_state(*, persist=False, keep_last_error=False):
    last_error = state.get("wild_training_last_error") if keep_last_error else ""
    strategy = normalize_wild_training_strategy(state.get("wild_training_strategy"))
    state["next_wild_training_time"] = 0
    state["wild_training_strategy"] = strategy
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_msg_id"] = 0
    state["wild_training_last_result"] = ""
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = last_error or ""
    if persist:
        save_state()
    else:
        mark_dirty()


def schedule_wild_training_initial_check(now, *, persist=False, keep_last_error=True):
    clear_wild_training_state(persist=False, keep_last_error=keep_last_error)
    state["next_wild_training_time"] = float(now + random.uniform(10 * 60, 30 * 60))
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_wild_training_time"]


def get_wild_training_status_text():
    strategy = normalize_wild_training_strategy(get_wild_training_strategy())
    lines = [
        "🏞️ 野外历练",
        f"- 已启用：{'是' if state.get('wild_training_enabled') else '否'}",
        f"- 当前策略：{strategy}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_wild_training_time', 0))}（{fmt_remaining(state.get('next_wild_training_time', 0))}）",
        f"- 待回复消息ID：{int(state.get('wild_training_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('wild_training_reply_due_at', 0))}（{fmt_remaining(state.get('wild_training_reply_due_at', 0))}）",
        f"- 补发次数：{int(state.get('wild_training_retry_count', 0) or 0)}/1",
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


def _is_wild_training_reply(text, reply_to, matched_family=None):
    if matched_family == "wild_training":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    raw_text = str(text or "")
    return orig_cmd == CMD_WILD_TRAINING or orig_cmd.startswith(f"{CMD_WILD_TRAINING} ") or raw_text.startswith(WILD_TRAINING_TITLE)


def _extract_result_title(text):
    raw_text = str(text or "").strip()
    for title in WILD_TRAINING_RESULT_TITLES:
        if raw_text.startswith(title):
            return title.replace("【野外历练 · ", "").replace("】", "")
    match = re.match(r"^【野外历练\s*·\s*([^】]+)】", raw_text)
    if match:
        return match.group(1).strip()
    return ""


def _is_start_notice(text):
    raw_text = str(text or "").strip()
    return raw_text.startswith("【野外历练】") and "选择【" in raw_text and "荒野深处行去" in raw_text


def _is_result_notice(text):
    raw_text = str(text or "").strip()
    return not _is_start_notice(raw_text) and any(marker in raw_text for marker in WILD_TRAINING_RESULT_MARKERS)


def _start_summary(text):
    match = RE_WILD_TRAINING_START_STRATEGY.search(str(text or ""))
    if match:
        return f"已出发：{normalize_wild_training_strategy(match.group(1))}"
    return "已出发"


def _result_summary(text):
    raw_text = str(text or "").strip()
    parts = []
    xiuwei_match = RE_WILD_TRAINING_XIUWEI.search(raw_text)
    if xiuwei_match:
        parts.append(f"修为{xiuwei_match.group(1).replace(' ', '')}")
    tianji_match = RE_WILD_TRAINING_TIANJI.search(raw_text)
    if tianji_match:
        parts.append(f"天机{tianji_match.group(1).replace(' ', '')}")
    contrib_match = RE_WILD_TRAINING_CONTRIB.search(raw_text)
    if contrib_match:
        parts.append(f"贡献{contrib_match.group(1).replace(' ', '')}")
    rewards = [f"{name}x{count}" for name, count in RE_WILD_TRAINING_REWARD.findall(raw_text)]
    if rewards:
        parts.append("奖励:" + "、".join(rewards))
    if "未损修为" in raw_text:
        parts.append("未损修为")
    if parts:
        return " ｜ ".join(parts)
    return _extract_result_title(raw_text) or "未知结果"


def _apply_wild_training_result(raw_text, now, msg_id):
    if looks_like_tianxing_route_result(raw_text):
        apply_tianxing_passive(raw_text, now=now)
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_last_msg_id"] = int(msg_id or 0)
    state["wild_training_last_result"] = _result_summary(raw_text)
    state["wild_training_last_result_at"] = float(now or 0)
    state["wild_training_last_error"] = ""
    _schedule_next(now)


async def _send_tianxing_wild_training_result_audit(raw_text):
    if not looks_like_tianxing_route_result(raw_text):
        return False
    await send_audit_log(
        f"🌌 天星探索结果｜野外历练：{state.get('wild_training_last_result') or '未知结果'}",
        scope="identity",
        priority="high",
        limit=260,
    )
    return True


def _find_logged_entry_by_msg_id(msg_id, now, *, result=False):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return None
    end_ts = float(now or 0) + WILD_TRAINING_LOG_REPLAY_LOOKAHEAD_SEC
    start_ts = max(0.0, end_ts - WILD_TRAINING_LOG_REPLAY_LOOKBACK_SEC)
    found = None
    for entry in _iter_message_log_entries_between(start_ts, end_ts):
        if int((entry or {}).get("message_id") or 0) != msg_id:
            continue
        entry_ts = _parse_message_log_ts((entry or {}).get("ts"))
        if entry_ts <= 0 or entry_ts < start_ts or entry_ts > end_ts:
            continue
        raw_text = str((entry or {}).get("text") or "").strip()
        if result and not _is_result_notice(raw_text):
            continue
        if not result and not _is_start_notice(raw_text):
            continue
        found = {"ts": entry_ts, "msg_id": msg_id, "text": raw_text}
    return found


def _find_logged_start_for_command(command_msg_id, now):
    command_msg_id = int(command_msg_id or 0)
    if command_msg_id <= 0:
        return None
    end_ts = float(now or 0) + WILD_TRAINING_LOG_REPLAY_LOOKAHEAD_SEC
    start_ts = max(0.0, end_ts - WILD_TRAINING_LOG_REPLAY_LOOKBACK_SEC)
    found = None
    for entry in _iter_message_log_entries_between(start_ts, end_ts):
        if int((entry or {}).get("reply_to_msg_id") or 0) != command_msg_id:
            continue
        entry_ts = _parse_message_log_ts((entry or {}).get("ts"))
        if entry_ts <= 0 or entry_ts < start_ts or entry_ts > end_ts:
            continue
        raw_text = str((entry or {}).get("text") or "").strip()
        if not _is_start_notice(raw_text):
            continue
        found = {"ts": entry_ts, "msg_id": int((entry or {}).get("message_id") or 0), "text": raw_text}
    return found


def _recover_wild_training_from_message_log(now):
    reply_to_msg_id = int(state.get("wild_training_reply_to_msg_id", 0) or 0)
    if reply_to_msg_id <= 0:
        return ""

    last_result = str(state.get("wild_training_last_result") or "")
    if last_result.startswith("已出发"):
        result_entry = _find_logged_entry_by_msg_id(reply_to_msg_id, now, result=True)
        if not result_entry:
            return ""
        _apply_wild_training_result(result_entry["text"], result_entry["ts"] or now, result_entry["msg_id"])
        return "result"

    start_entry = _find_logged_start_for_command(reply_to_msg_id, now)
    if not start_entry:
        return ""
    result_entry = _find_logged_entry_by_msg_id(start_entry["msg_id"], now, result=True)
    if result_entry:
        _apply_wild_training_result(result_entry["text"], result_entry["ts"] or now, result_entry["msg_id"])
        return "result"

    state["wild_training_reply_to_msg_id"] = int(start_entry["msg_id"] or 0)
    state["wild_training_last_msg_id"] = int(start_entry["msg_id"] or 0)
    state["wild_training_last_result"] = _start_summary(start_entry["text"])
    state["wild_training_last_error"] = ""
    state["wild_training_reply_due_at"] = max(
        float(state.get("wild_training_reply_due_at", 0) or 0),
        float(start_entry["ts"] or now) + WILD_TRAINING_REPLY_TIMEOUT_SEC,
    )
    return "start"


async def handle_wild_training_reply(text, now, reply_to, matched_family=None, current_msg_id=None):
    if not state.get("wild_training_enabled"):
        return False
    if not _is_wild_training_reply(text, reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "").strip()
    msg_id = int(current_msg_id or getattr(reply_to, "id", 0) or 0)
    if has_wait_time(raw_text) and any(keyword in raw_text for keyword in WILD_TRAINING_CD_KEYWORDS):
        wait_sec = parse_wait_time(raw_text)
        state["next_wild_training_time"] = float(now + wait_sec + CD_BUFFER_SEC + random.uniform(10, 60))
        state["wild_training_reply_to_msg_id"] = 0
        state["wild_training_reply_due_at"] = 0
        state["wild_training_retry_count"] = 0
        state["wild_training_last_msg_id"] = msg_id
        state["wild_training_last_result"] = "冷却中"
        state["wild_training_last_result_at"] = float(now or 0)
        state["wild_training_last_error"] = ""
        save_state()
        await send_audit_log(f"🏞️ 野外历练 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}", scope="identity")
        return True

    if _is_start_notice(raw_text):
        if msg_id > 0:
            state["wild_training_reply_to_msg_id"] = msg_id
        state["wild_training_last_msg_id"] = msg_id
        state["wild_training_last_result"] = _start_summary(raw_text)
        state["wild_training_last_result_at"] = 0
        state["wild_training_last_error"] = ""
        if float(state.get("wild_training_reply_due_at", 0) or 0) <= now:
            state["wild_training_reply_due_at"] = float(now + WILD_TRAINING_REPLY_TIMEOUT_SEC)
        save_state()
        console_log(f"🏞️ 野外历练已出发，等待结果编辑（msg_id={msg_id}）", scope="identity")
        return True

    if not _is_result_notice(raw_text):
        return False

    _apply_wild_training_result(raw_text, now, msg_id)
    save_state()
    await _send_tianxing_wild_training_result_audit(raw_text)
    await send_audit_log(f"🏞️ 野外历练结果：{state['wild_training_last_result']}", scope="identity", limit=220)
    return True


async def _prepare_wild_training_tianxing_route(now, *, due_at=0):
    due_at = float(due_at or now)
    preflight = build_tianxing_route_preflight_plan("探索", reason="野外历练", now=now, require_change_fate=True)
    if preflight.get("route_allowed"):
        _clear_tianxing_prepare_retry()
        return True
    if str(preflight.get("stage") or "") == "prediction_conflict":
        consume_result = await run_tianxing_consume_craft_prediction(now, reason="野外历练前消费炼制推命")
        if consume_result.get("active"):
            if due_at <= now:
                _schedule_retry(now)
            else:
                _schedule_tianxing_prepare_retry(now)
            state["wild_training_last_result"] = f"天星先炼制消费推命：{consume_result.get('stage') or 'waiting'}"
            state["wild_training_last_result_at"] = 0
            state["wild_training_last_error"] = "" if consume_result.get("takeover") or consume_result.get("stage") == "waiting_reply" else str(consume_result.get("reason") or "")
            save_state()
            return False
    blocked_until = float(preflight.get("blocked_until", 0) or 0)
    if blocked_until > now:
        if due_at <= now:
            _schedule_retry(now)
        else:
            _schedule_tianxing_prepare_retry(now)
        state["wild_training_last_error"] = str(preflight.get("reason") or "野外历练天星预检阻断")
        save_state()
        return False
    if preflight.get("timeline_required"):
        windows = build_tianxing_consume_window(
            "探索",
            now=now,
            due_at=max(due_at, now),
            reason="野外历练",
            require_change_fate=True,
        )
        if not windows:
            return True
        timeline_result = await run_tianxing_timeline_scheduler(now, windows=windows)
        followup = build_tianxing_route_preflight_plan("探索", reason="野外历练", now=now, require_change_fate=True)
        if followup.get("route_allowed"):
            _clear_tianxing_prepare_retry()
            return True
        phase = str(timeline_result.get("phase") or "").strip()
        if (
            due_at <= now
            and _has_active_tianxing_explore_prediction(now)
            and not _has_active_tianxing_explore_change(now)
            and str(followup.get("stage") or "") in {"timeline_waiting", "timeline_waiting_change_fate"}
            and phase in {"idle", "completed", "dry_run", "blocked_replan", "observe_only", "need_tianji_for_change", "change_fate_conflict"}
        ):
            state["wild_training_last_result"] = "天星已有探索推命但无探索改命，野外降级谨慎"
            state["wild_training_last_result_at"] = 0
            state["wild_training_last_error"] = ""
            save_state()
            return True
        if due_at <= now:
            _schedule_retry(now)
        else:
            _schedule_tianxing_prepare_retry(now)
        state["wild_training_last_result"] = f"天星时间线：{timeline_result.get('phase') or 'waiting'}"
        state["wild_training_last_result_at"] = 0
        state["wild_training_last_error"] = "" if timeline_result.get("changed") else str(preflight.get("reason") or "")
        save_state()
        return False
    if due_at <= now:
        _schedule_retry(now)
    else:
        _schedule_tianxing_prepare_retry(now)
    state["wild_training_last_error"] = str(preflight.get("reason") or "野外历练天星预检阻断")
    save_state()
    return False


async def _cleanup_wild_training_pending_timeout(now):
    reply_to_msg_id = int(state.get("wild_training_reply_to_msg_id", 0) or 0)
    if reply_to_msg_id <= 0:
        return False
    if now < float(state.get("wild_training_reply_due_at", 0) or 0):
        return False
    recovered = _recover_wild_training_from_message_log(now)
    if recovered == "result":
        save_state()
        await send_audit_log(f"🏞️ 野外历练日志补偿：{state['wild_training_last_result']}", scope="identity", limit=220)
        return True
    if recovered == "start" and now < float(state.get("wild_training_reply_due_at", 0) or 0):
        save_state()
        console_log(
            f"🏞️ 野外历练日志补偿：已出发，继续等待结果编辑（msg_id={state['wild_training_reply_to_msg_id']}）",
            scope="identity",
        )
        return True
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    if str(state.get("wild_training_last_result") or "").startswith("已出发"):
        _schedule_next(now)
        state["wild_training_last_result"] = f"结果编辑未留存，已按正常周期恢复，原消息ID={reply_to_msg_id}"
        state["wild_training_last_result_at"] = float(now or 0)
        state["wild_training_last_error"] = ""
        save_state()
        console_log(f"🏞️ 野外历练{state['wild_training_last_result']}", scope="identity")
        return True
    if int(state.get("wild_training_retry_count", 0) or 0) < 1:
        state["wild_training_retry_count"] = int(state.get("wild_training_retry_count", 0) or 0) + 1
        _schedule_retry(now)
        state["wild_training_last_error"] = f"野外历练回复超时，准备补发一次，原消息ID={reply_to_msg_id}"
    else:
        _schedule_next(now)
        state["wild_training_last_error"] = f"野外历练补发后仍无回复，进入下一轮，原消息ID={reply_to_msg_id}"
    save_state()
    await send_audit_log(f"⚠️ {state['wild_training_last_error']}", scope="identity")
    return True


async def run_wild_training_phaseful_cleanup_scheduler(now):
    if not state.get("wild_training_enabled"):
        return False
    async with _wild_training_lock():
        return await _cleanup_wild_training_pending_timeout(now)


async def _run_wild_training_scheduler_unlocked(now):
    if not state.get("wild_training_enabled"):
        return

    if await _cleanup_wild_training_pending_timeout(now):
        return

    try:
        next_wild_training_time = float(state.get("next_wild_training_time", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        next_wild_training_time = 0
    if next_wild_training_time > now:
        windows = build_tianxing_consume_window(
            "探索",
            now=now,
            due_at=next_wild_training_time,
            reason="野外历练",
            require_change_fate=True,
        )
        if windows and not _tianxing_prepare_retry_blocks(now) and not await _prepare_wild_training_tianxing_route(now, due_at=next_wild_training_time):
            return
    if cd_blocks(state.get("next_wild_training_time", 0), now, 0):
        return
    if _guard_recent_completed_result(now):
        return

    strategy = normalize_wild_training_strategy(get_wild_training_strategy())
    retry_count = int(state.get("wild_training_retry_count", 0) or 0)
    if await _defer_wild_training_for_dungeon_quiet(
        now,
        action="补发" if retry_count > 0 else "发送",
    ):
        return

    if not await _prepare_wild_training_tianxing_route(now, due_at=now):
        return

    strategy = _effective_wild_training_strategy(now)
    msg = await send_game_command(get_wild_training_command(strategy), track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or now) if msg else float(now)
    if not msg:
        retry_count = int(state.get("wild_training_retry_count", 0) or 0)
        if await _defer_wild_training_for_dungeon_quiet(
            sent_at,
            action="补发" if retry_count > 0 else "发送",
        ):
            return
        if int(state.get("wild_training_retry_count", 0) or 0) < 1:
            state["wild_training_retry_count"] = int(state.get("wild_training_retry_count", 0) or 0) + 1
            _schedule_retry(sent_at)
            state["wild_training_last_error"] = "野外历练发送失败，准备补发一次"
        else:
            _schedule_next(sent_at)
            state["wild_training_last_error"] = "野外历练补发发送失败，进入下一轮"
        save_state()
        await send_audit_log(f"❌ {state['wild_training_last_error']}。", scope="identity")
        return

    state["wild_training_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["wild_training_reply_due_at"] = sent_at + WILD_TRAINING_REPLY_TIMEOUT_SEC
    state["wild_training_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["wild_training_last_result"] = f"已发送：{strategy}"
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = ""
    save_state()
    console_log(f"🏞️ 野外历练已发送：{strategy}（msg_id={msg.id}）", scope="identity")


async def run_wild_training_scheduler(now):
    async with _wild_training_lock():
        return await _run_wild_training_scheduler_unlocked(now)


__all__ = [
    "WILD_TRAINING_REPLY_TIMEOUT_SEC",
    "apply_wild_training_strategy",
    "clear_wild_training_state",
    "get_wild_training_command",
    "get_wild_training_status_text",
    "handle_wild_training_reply",
    "normalize_wild_training_strategy",
    "run_wild_training_phaseful_cleanup_scheduler",
    "run_wild_training_scheduler",
    "schedule_wild_training_initial_check",
]
