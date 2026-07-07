import asyncio
import json
import random
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ..config import CD_BUFFER_SEC, CMD_TIANXING_PANEL, CMD_WILD_TRAINING, DB_FILE, MESSAGES_DIR, POST_SUMMARY_WAIT_SEC, TZ_LOCAL, WILD_TRAINING_STRATEGIES
from ..persistence import mark_dirty, save_state
from ..runtime import (
    classify_game_send_block,
    console_log,
    send_audit_log,
    send_game_command,
    was_last_game_send_blocked_by_global,
)
from ..state import get_current_identity_id, get_wild_training_strategy, set_wild_training_strategy, state
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .dungeon_quiet import get_dungeon_quiet_reason, get_dungeon_quiet_until, is_dungeon_quiet_active
from .tianxing import (
    apply_tianxing_passive,
    build_tianxing_consume_window,
    build_tianxing_route_preflight_plan,
    looks_like_tianxing_route_result,
    mark_tianxing_route_result_unknown,
    normalize_tianxing_observation,
    normalize_tianxing_timeline_state,
    run_tianxing_consume_craft_prediction,
    run_tianxing_craft_farm_scheduler,
    run_tianxing_timeline_scheduler,
)


WILD_TRAINING_CYCLE_MIN_SEC = 2 * 3600
WILD_TRAINING_CYCLE_MAX_SEC = 2 * 3600
WILD_TRAINING_RECOVERY_SPREAD_MIN_SEC = 2 * 60
WILD_TRAINING_RECOVERY_SPREAD_MAX_SEC = 10 * 60
WILD_TRAINING_REPLY_TIMEOUT_SEC = 10 * 60
# 普通野外会在深闭/元婴结算后批量到期；全局空闲时立即发，
# 连续发送才叠加 12-18s 全局间隔和 10s 身份间隔，75s 足够暴露真实拥堵。
WILD_TRAINING_SEND_TIMEOUT_SEC = 75
WILD_TRAINING_RETRY_MIN_SEC = 2 * 60
WILD_TRAINING_RETRY_MAX_SEC = 3 * 60
WILD_TRAINING_SEND_QUEUE_RETRY_MIN_SEC = 10 * 60
WILD_TRAINING_SEND_QUEUE_RETRY_MAX_SEC = 20 * 60
WILD_TRAINING_SEND_UNKNOWN_WAIT_SEC = 10 * 60
WILD_TRAINING_SEND_UNKNOWN_UNRECOVERED_MIN_SEC = 30 * 60
WILD_TRAINING_SEND_UNKNOWN_UNRECOVERED_MAX_SEC = 45 * 60
WILD_TRAINING_SEND_UNKNOWN_RETRY_MIN_SEC = 2 * 60
WILD_TRAINING_SEND_UNKNOWN_RETRY_MAX_SEC = 3 * 60
WILD_TRAINING_TIANXING_PANEL_QUEUE_TIMEOUT_SEC = 45
WILD_TRAINING_DUNGEON_QUIET_RESUME_MIN_SEC = 10
WILD_TRAINING_DUNGEON_QUIET_RESUME_MAX_SEC = 40
WILD_TRAINING_DEEP_RETREAT_GUARD_SEC = 5 * 60
WILD_TRAINING_DEEP_RETREAT_RESUME_MIN_SEC = 90
WILD_TRAINING_DEEP_RETREAT_RESUME_MAX_SEC = 180
WILD_TRAINING_STALE_RESULT_RESCHEDULE_MARGIN_SEC = 30
WILD_TRAINING_LOG_REPLAY_LOOKBACK_SEC = 20 * 60
WILD_TRAINING_LOG_REPLAY_LOOKAHEAD_SEC = 2 * 60
WILD_TRAINING_TIANXING_CONSUME_ATTEMPT_GRACE_SEC = 10 * 60
WILD_TRAINING_RESULT_DEDUPE_SEC = 5 * 60
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


def _close_wild_training_guard(reason, now):
    send_as_id = int(get_current_identity_id() or 0)
    if send_as_id <= 0:
        return False
    try:
        from .. import action_guard
    except Exception:
        return False
    return bool(action_guard.close_by_family("wild_training", send_as_id=send_as_id, reason=reason, now=now))


def _wild_training_action_guard_wait(command, now):
    command = str(command or "").strip()
    if not command:
        return 0.0, ""
    send_as_id = int(get_current_identity_id() or 0)
    if send_as_id <= 0:
        return 0.0, ""
    try:
        from .. import action_guard
    except Exception:
        return 0.0, ""
    try:
        blocked_until, reason = action_guard.get_timing_blocked_until(command, send_as_id=send_as_id, now=now)
    except Exception:
        return 0.0, ""
    try:
        blocked_until = float(blocked_until or 0)
    except (TypeError, ValueError, OverflowError):
        blocked_until = 0.0
    if blocked_until <= float(now or 0):
        return 0.0, ""
    return blocked_until + 2, str(reason or "野外历练安全锁短窗保护中，延后发送").strip()


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


def _resume_deep_retreat_after_wild_training(now):
    if not state.get("deep_retreat_enabled"):
        return False
    if str(state.get("deep_retreat_phase") or "").strip() != "post_summary_wait":
        return False
    target = float(now or 0) + POST_SUMMARY_WAIT_SEC
    try:
        current = float(state.get("next_deep_retreat_time", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        current = 0.0
    if current <= 0 or current <= target:
        return False
    state["next_deep_retreat_time"] = target
    return True


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


def _identity_sender_matches(sender_id, send_as_id):
    try:
        sender_id = int(sender_id or 0)
        send_as_id = int(send_as_id or 0)
    except (TypeError, ValueError):
        return False
    if sender_id == send_as_id:
        return True
    if sender_id < 0:
        sender_abs = str(abs(sender_id))
        if sender_abs.startswith("100") and len(sender_abs) > 3:
            try:
                return int(sender_abs[3:] or 0) == send_as_id
            except ValueError:
                return False
    return False


def _recent_craft_prediction_consume_attempt_for_due(due_at, now):
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    craft_farm = timeline.get("craft_farm") if isinstance(timeline.get("craft_farm"), dict) else {}
    last_action = str(craft_farm.get("last_action") or "").strip()
    if last_action not in {"consume_craft_prediction", "consume_craft_prediction_calibration"}:
        return False
    try:
        updated_at = float(craft_farm.get("updated_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        updated_at = 0.0
    if updated_at <= 0:
        return False
    due_at = float(due_at or now or 0)
    now = float(now or 0)
    if updated_at > now + 1:
        return False
    return updated_at >= due_at - WILD_TRAINING_TIANXING_CONSUME_ATTEMPT_GRACE_SEC


def _tianxing_timeline_prepare_failed(timeline_result, followup):
    phase = str((timeline_result or {}).get("phase") or "").strip()
    followup_stage = str((followup or {}).get("stage") or "").strip()
    if followup_stage not in {"timeline_waiting", "timeline_waiting_change_fate"}:
        return False
    return phase in {
        "ack_timeout",
        "calibrating",
        "send_blocked",
        "blocked_replan",
        "panel_calibration_timeout_replan",
        "need_tianji_for_change",
        "observe_only",
    }


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
    if _is_unknown_send_summary(text):
        return False
    if text in {"冷却中"}:
        return False
    if any(marker in text for marker in ("发送失败", "回复超时", "补发", "冷却")):
        return False
    return True


def _is_unknown_send_summary(summary):
    return str(summary or "").strip().startswith("发送状态未知")


def _mark_send_unknown(now):
    wait_until = float(now or 0) + WILD_TRAINING_SEND_UNKNOWN_WAIT_SEC
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = wait_until
    state["wild_training_retry_count"] = 0
    state["wild_training_last_result"] = "发送状态未知，等待被动回复或冷却校准"
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = "野外历练发送状态未知，先等待被动结果，避免重复消耗"
    state["next_wild_training_time"] = wait_until
    return wait_until


def _mark_unknown_send_unrecovered(now, reason):
    next_time = float(now or 0) + random.uniform(
        WILD_TRAINING_SEND_UNKNOWN_UNRECOVERED_MIN_SEC,
        WILD_TRAINING_SEND_UNKNOWN_UNRECOVERED_MAX_SEC,
    )
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_result"] = "未知发送未找回，已保守退避"
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = str(reason or "野外历练发送状态未知且消息日志未捞到反馈，已保守退避")
    state["next_wild_training_time"] = next_time
    return next_time


def _mark_unknown_send_short_retry(now, reason):
    next_time = float(now or 0) + random.uniform(
        WILD_TRAINING_SEND_UNKNOWN_RETRY_MIN_SEC,
        WILD_TRAINING_SEND_UNKNOWN_RETRY_MAX_SEC,
    )
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_result"] = "未知发送未找回，短退避后重试"
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = str(reason or "野外历练发送状态未知且消息日志未捞到命令，短退避后重试")
    state["next_wild_training_time"] = next_time
    return next_time


def _has_unknown_send_wait(now):
    if not _is_unknown_send_summary(state.get("wild_training_last_result")):
        return False
    due_at = float(state.get("wild_training_reply_due_at", 0) or 0)
    return due_at > float(now or 0)


def _last_completed_wild_training_at():
    anchors = []
    try:
        completed_at = float(state.get("wild_training_last_completed_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        completed_at = 0.0
    if completed_at > 0:
        anchors.append(completed_at)
    try:
        last_result_at = float(state.get("wild_training_last_result_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        last_result_at = 0.0
    if last_result_at > 0 and _is_completed_wild_training_summary(state.get("wild_training_last_result")):
        anchors.append(last_result_at)
    return max(anchors) if anchors else 0.0


def _guard_recent_completed_result(now):
    completed_at = _last_completed_wild_training_at()
    if completed_at <= 0:
        return False
    due_at = completed_at + WILD_TRAINING_CYCLE_MIN_SEC
    if float(now or 0) >= due_at:
        return False
    already_logged = str(state.get("wild_training_last_error") or "").startswith("野外历练结果后计时器异常")
    state["next_wild_training_time"] = float(
        max(
            due_at + WILD_TRAINING_STALE_RESULT_RESCHEDULE_MARGIN_SEC,
            float(now or 0) + WILD_TRAINING_RETRY_MIN_SEC,
        )
    )
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_error"] = "野外历练结果后计时器异常，已按正常周期顺延"
    save_state()
    if not already_logged:
        console_log(f"🏞️ {state['wild_training_last_error']}→{fmt_abs_ts(state['next_wild_training_time'])}", scope="identity")
    return True


def _has_active_wild_training_pending(now):
    reply_to_msg_id = int(state.get("wild_training_reply_to_msg_id", 0) or 0)
    if reply_to_msg_id <= 0:
        return False
    return float(state.get("wild_training_reply_due_at", 0) or 0) > float(now or 0)


async def _defer_wild_training_for_dungeon_quiet(now, *, action):
    next_time = _schedule_after_dungeon_quiet(now)
    if next_time <= 0:
        return False
    reason = get_dungeon_quiet_reason() or "副本静场令"
    state["wild_training_last_error"] = f"野外历练{action}撞到{reason}，延后至 {fmt_abs_ts(next_time)}"
    save_state()
    await send_audit_log(f"🤫 {state['wild_training_last_error']}。", scope="identity")
    return True


async def _defer_wild_training_for_deep_retreat_summary_window(now):
    if not state.get("deep_retreat_enabled"):
        return False
    phase = str(state.get("deep_retreat_phase") or "idle").strip()
    try:
        next_deep_time = float(state.get("next_deep_retreat_time", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        next_deep_time = 0.0
    blocking_phases = {"summary_due", "observing_summary", "waiting_summary", "queued_launch", "launching"}
    should_defer = phase in blocking_phases
    if not should_defer and phase == "running":
        should_defer = True
    if not should_defer:
        return False

    anchor = float(now or 0)
    if phase == "running" and next_deep_time > 0:
        anchor = max(anchor, next_deep_time)
    elif 0 < next_deep_time <= anchor + WILD_TRAINING_DEEP_RETREAT_GUARD_SEC:
        anchor = max(anchor, next_deep_time)
    next_time = anchor + random.uniform(WILD_TRAINING_DEEP_RETREAT_RESUME_MIN_SEC, WILD_TRAINING_DEEP_RETREAT_RESUME_MAX_SEC)
    state["next_wild_training_time"] = float(next_time)
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_result"] = "深闭结算窗口避让，未发送"
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = f"野外历练避让深度闭关结算窗口：phase={phase or 'idle'}，延后至 {fmt_abs_ts(next_time)}"
    save_state()
    console_log(f"🏞️ {state['wild_training_last_error']}", scope="identity")
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
    state["wild_training_last_completed_at"] = 0
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


def _apply_wild_training_cooldown(raw_text, now, msg_id=0):
    wait_sec = parse_wait_time(raw_text)
    state["next_wild_training_time"] = float(now + wait_sec + CD_BUFFER_SEC + random.uniform(10, 60))
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_msg_id"] = int(msg_id or 0)
    state["wild_training_last_result"] = "冷却中"
    state["wild_training_last_result_tianxing"] = False
    state["wild_training_last_result_at"] = 0
    state["wild_training_last_error"] = ""
    _resume_deep_retreat_after_wild_training(now)


def _apply_wild_training_result(raw_text, now, msg_id):
    is_tianxing_result = looks_like_tianxing_route_result(raw_text)
    if is_tianxing_result:
        apply_tianxing_passive(raw_text, now=now)
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_last_msg_id"] = int(msg_id or 0)
    state["wild_training_last_result"] = _result_summary(raw_text)
    state["wild_training_last_result_tianxing"] = bool(is_tianxing_result)
    state["wild_training_last_result_at"] = float(now or 0)
    state["wild_training_last_completed_at"] = float(now or 0)
    state["wild_training_last_error"] = ""
    state["wild_training_retry_count"] = 0
    _schedule_next(now)
    _resume_deep_retreat_after_wild_training(now)


def _is_duplicate_wild_training_result(raw_text, msg_id, now=None):
    msg_id = int(msg_id or 0)
    summary = _result_summary(raw_text)
    last_result_at = float(state.get("wild_training_last_result_at", 0) or 0)
    try:
        now_value = float(now or 0)
    except (TypeError, ValueError, OverflowError):
        now_value = 0.0
    if (
        summary
        and last_result_at > 0
        and now_value > 0
        and now_value - last_result_at <= WILD_TRAINING_RESULT_DEDUPE_SEC
        and str(state.get("wild_training_last_result") or "") == summary
    ):
        # 多 DC 监听和 message/edit 重放可能带来不同事件上下文；同身份同摘要短窗只播一次。
        return True
    if msg_id <= 0:
        return False
    if (
        int(state.get("wild_training_last_msg_id", 0) or 0) == msg_id
        and last_result_at > 0
        and str(state.get("wild_training_last_result") or "") == summary
    ):
        return True
    try:
        with sqlite3.connect(DB_FILE, timeout=2) as conn:
            row = conn.execute(
                """
                SELECT wild_training_last_msg_id, wild_training_last_result, wild_training_last_result_at
                FROM identity_runtime_state
                WHERE send_as_id = ?
                """,
                (int(get_current_identity_id() or 0),),
            ).fetchone()
    except sqlite3.Error:
        return False
    if not row:
        return False
    try:
        persisted_msg_id = int(row[0] or 0)
        persisted_result_at = float(row[2] or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    return persisted_msg_id == msg_id and persisted_result_at > 0 and str(row[1] or "") == summary


async def _send_tianxing_wild_training_result_audit(raw_text):
    if raw_text:
        is_tianxing_result = looks_like_tianxing_route_result(raw_text)
    else:
        is_tianxing_result = bool(state.get("wild_training_last_result_tianxing"))
    if not is_tianxing_result:
        return False
    await send_audit_log(
        f"🌌 天星探索结果｜野外历练：{state.get('wild_training_last_result') or '未知结果'}",
        scope="identity",
        priority="high",
        limit=260,
    )
    return True


async def _send_wild_training_recovery_audit(recovered):
    if recovered == "result":
        await _send_tianxing_wild_training_result_audit("")
    await send_audit_log(f"🏞️ 野外历练日志补偿：{state['wild_training_last_result']}", scope="identity", limit=220)


async def _send_tianxing_panel_calibration(now, reason):
    if not state.get("tianxing_enabled"):
        _schedule_retry(now)
        state["wild_training_last_error"] = str(reason or "野外历练状态不明，短退避后重试")
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
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_result"] = "野外历练状态不明，等待天机盘校准"
    state["wild_training_last_result_at"] = 0
    if msg:
        state["wild_training_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["wild_training_last_error"] = str(reason or "已发送天机盘校准，暂不发送野外历练")
        return True
    state["wild_training_last_error"] = str(reason or "天机盘校准未发出，短退避后重试")
    return False


async def _defer_wild_training_to_tianxing_craft(now, reason):
    craft_result = await run_tianxing_craft_farm_scheduler(now)
    retry_at = float(now or 0) + random.uniform(WILD_TRAINING_RETRY_MIN_SEC, WILD_TRAINING_RETRY_MAX_SEC)
    craft_next = 0.0
    try:
        craft_next = float((craft_result or {}).get("next_time", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        craft_next = 0.0
    if craft_next > float(now or 0):
        retry_at = max(retry_at, min(craft_next, float(now or 0) + WILD_TRAINING_REPLY_TIMEOUT_SEC))
    stage = str((craft_result or {}).get("stage") or "inactive").strip()
    craft_reason = str((craft_result or {}).get("reason") or "").strip()
    state["next_wild_training_time"] = retry_at
    state["wild_training_tianxing_prepare_retry_at"] = retry_at
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_result"] = f"天星缺探索改命，转炼制攒点：{stage}"
    state["wild_training_last_result_at"] = 0
    if (craft_result or {}).get("takeover") or stage in {
        "sent_waiting_reply",
        "waiting_reply",
        "send_craft",
        "send_craft_unpredicted",
        "timeline_required",
        "prediction_conflict_override_retry",
    }:
        state["wild_training_last_error"] = ""
    else:
        state["wild_training_last_error"] = craft_reason or str(reason or "天星缺探索改命，野外不降级谨慎")
    save_state()
    return craft_result or {}


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


def _find_logged_reply_for_command(command_msg_id, now):
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
        if not raw_text.startswith(WILD_TRAINING_TITLE):
            continue
        msg_id = int((entry or {}).get("message_id") or 0)
        kind = "other"
        if has_wait_time(raw_text) and any(keyword in raw_text for keyword in WILD_TRAINING_CD_KEYWORDS):
            kind = "cooldown"
        elif _is_result_notice(raw_text):
            kind = "result"
        elif _is_start_notice(raw_text):
            kind = "start"
        found = {"ts": entry_ts, "msg_id": msg_id, "text": raw_text, "kind": kind}
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


def _find_recent_logged_command_for_identity(now):
    send_as_id = int(get_current_identity_id() or 0)
    if send_as_id <= 0:
        return None
    wait_until = float(state.get("wild_training_reply_due_at", 0) or 0)
    sent_after = wait_until - WILD_TRAINING_SEND_UNKNOWN_WAIT_SEC - 60 if wait_until > 0 else float(now or 0) - WILD_TRAINING_LOG_REPLAY_LOOKBACK_SEC
    end_ts = float(now or 0) + WILD_TRAINING_LOG_REPLAY_LOOKAHEAD_SEC
    found = None
    seen = set()
    for entry in _iter_message_log_entries_between(max(0.0, sent_after), end_ts):
        msg_id = int((entry or {}).get("message_id") or 0)
        if msg_id in seen:
            continue
        seen.add(msg_id)
        if not _identity_sender_matches((entry or {}).get("sender_id"), send_as_id):
            continue
        raw_text = str((entry or {}).get("text") or "").strip()
        if not (raw_text == CMD_WILD_TRAINING or raw_text.startswith(f"{CMD_WILD_TRAINING} ")):
            continue
        entry_ts = _parse_message_log_ts((entry or {}).get("ts"))
        if entry_ts <= 0 or entry_ts < sent_after or entry_ts > end_ts:
            continue
        found = {"ts": entry_ts, "msg_id": msg_id, "text": raw_text}
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
        direct_reply = _find_logged_reply_for_command(reply_to_msg_id, now)
        if direct_reply and direct_reply.get("kind") == "cooldown":
            _apply_wild_training_cooldown(direct_reply["text"], direct_reply["ts"] or now, direct_reply["msg_id"])
            return "cooldown"
        if direct_reply and direct_reply.get("kind") == "result":
            _apply_wild_training_result(direct_reply["text"], direct_reply["ts"] or now, direct_reply["msg_id"])
            return "result"
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


def _recover_unknown_wild_training_from_message_log(now):
    command_entry = _find_recent_logged_command_for_identity(now)
    if not command_entry:
        return ""
    direct_reply = _find_logged_reply_for_command(command_entry["msg_id"], now)
    if direct_reply and direct_reply.get("kind") == "cooldown":
        _apply_wild_training_cooldown(direct_reply["text"], direct_reply["ts"] or now, direct_reply["msg_id"])
        return "cooldown"
    if direct_reply and direct_reply.get("kind") == "result":
        _apply_wild_training_result(direct_reply["text"], direct_reply["ts"] or now, direct_reply["msg_id"])
        return "result"
    start_entry = _find_logged_start_for_command(command_entry["msg_id"], now)
    if not start_entry:
        state["wild_training_reply_to_msg_id"] = int(command_entry["msg_id"] or 0)
        state["wild_training_last_msg_id"] = int(command_entry["msg_id"] or 0)
        strategy = str(command_entry.get("text") or CMD_WILD_TRAINING).replace(CMD_WILD_TRAINING, "", 1).strip()
        state["wild_training_last_result"] = f"已发送：{strategy or '未知策略'}"
        state["wild_training_last_error"] = "野外历练发送状态未知，但已从消息日志回捞到命令，继续等待回复或冷却校准"
        state["wild_training_reply_due_at"] = max(
            float(state.get("wild_training_reply_due_at", 0) or 0),
            float(command_entry["ts"] or now) + WILD_TRAINING_REPLY_TIMEOUT_SEC,
        )
        return "command"
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


def _recover_cleared_wild_training_retry_from_message_log(now):
    last_error = str(state.get("wild_training_last_error") or "")
    last_result = str(state.get("wild_training_last_result") or "")
    if int(state.get("wild_training_retry_count", 0) or 0) <= 0 and "结果编辑未留存" not in last_result:
        return ""
    match = re.search(r"原消息ID=(\d+)", last_error) or re.search(r"原消息ID=(\d+)", last_result)
    if not match:
        return ""
    original_msg_id = int(match.group(1) or 0)
    if original_msg_id <= 0:
        return ""
    result_entry = _find_logged_entry_by_msg_id(original_msg_id, now, result=True)
    if result_entry:
        _apply_wild_training_result(result_entry["text"], result_entry["ts"] or now, result_entry["msg_id"])
        return "result"
    command_msg_id = original_msg_id
    direct_reply = _find_logged_reply_for_command(command_msg_id, now)
    if direct_reply and direct_reply.get("kind") == "cooldown":
        _apply_wild_training_cooldown(direct_reply["text"], direct_reply["ts"] or now, direct_reply["msg_id"])
        return "cooldown"
    if direct_reply and direct_reply.get("kind") == "result":
        _apply_wild_training_result(direct_reply["text"], direct_reply["ts"] or now, direct_reply["msg_id"])
        return "result"
    start_entry = _find_logged_start_for_command(command_msg_id, now)
    if not start_entry:
        return ""
    result_entry = _find_logged_entry_by_msg_id(start_entry["msg_id"], now, result=True)
    if result_entry:
        _apply_wild_training_result(result_entry["text"], result_entry["ts"] or now, result_entry["msg_id"])
        return "result"
    return ""


async def handle_wild_training_reply(text, now, reply_to, matched_family=None, current_msg_id=None):
    if not state.get("wild_training_enabled"):
        return False
    if not _is_wild_training_reply(text, reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "").strip()
    msg_id = int(current_msg_id or getattr(reply_to, "id", 0) or 0)
    if has_wait_time(raw_text) and any(keyword in raw_text for keyword in WILD_TRAINING_CD_KEYWORDS):
        previous_result = str(state.get("wild_training_last_result") or "").strip()
        previous_retry_count = int(state.get("wild_training_retry_count", 0) or 0)
        tianxing_unknown = False
        if previous_retry_count > 0 or previous_result.startswith("已出发") or _is_unknown_send_summary(previous_result):
            tianxing_unknown = mark_tianxing_route_result_unknown(
                "探索",
                now=now,
                reason="野外历练补发撞冷却，上一轮结果可能已被服端结算",
            )
        wait_sec = parse_wait_time(raw_text)
        _apply_wild_training_cooldown(raw_text, now, msg_id)
        save_state()
        await send_audit_log(f"🏞️ 野外历练 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}", scope="identity")
        if tianxing_unknown:
            await send_audit_log(
                "🌌 天星探索结果不确定：野外补发撞冷却，已保守清理探索推命/改命缓存并等待下一轮预检。",
                scope="identity",
                priority="high",
            )
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

    if _is_duplicate_wild_training_result(raw_text, msg_id, now=now):
        console_log(f"🏞️ 野外历练重复结果已忽略（msg_id={msg_id}）", scope="identity")
        return True

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
        if due_at <= now and _recent_craft_prediction_consume_attempt_for_due(due_at, now):
            await _send_tianxing_panel_calibration(now, "天星炼制推命消费后需查盘确认探索推/改状态")
            state["wild_training_last_result"] = "天星炼制推命已尝试消费，等待查盘确认探索推/改状态"
            state["wild_training_last_result_at"] = 0
            state["wild_training_last_error"] = ""
            save_state()
            return False
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
            await _send_tianxing_panel_calibration(now, "野外历练缺少天星消费窗口，先查盘校准")
            save_state()
            return False
        timeline_result = await run_tianxing_timeline_scheduler(now, windows=windows)
        followup = build_tianxing_route_preflight_plan("探索", reason="野外历练", now=now, require_change_fate=True)
        if followup.get("route_allowed"):
            _clear_tianxing_prepare_retry()
            return True
        phase = str(timeline_result.get("phase") or "").strip()
        if due_at <= now and phase == "need_tianji_for_change":
            await _defer_wild_training_to_tianxing_craft(
                now,
                "天星天机不足且无探索改命，野外历练不降级谨慎，改走炼制攒点。",
            )
            return False
        if (
            due_at <= now
            and _has_active_tianxing_explore_prediction(now)
            and not _has_active_tianxing_explore_change(now)
            and str(followup.get("stage") or "") in {"timeline_waiting", "timeline_waiting_change_fate"}
            and phase in {"idle", "completed", "dry_run", "blocked_replan", "observe_only", "need_tianji_for_change", "change_fate_conflict"}
        ):
            await _send_tianxing_panel_calibration(now, "天星已有探索推命但无探索改命，先查盘校准")
            state["wild_training_last_result"] = "天星已有探索推命但无探索改命，等待查盘校准"
            state["wild_training_last_result_at"] = 0
            state["wild_training_last_error"] = ""
            save_state()
            return False
        if due_at <= now and _tianxing_timeline_prepare_failed(timeline_result, followup):
            await _send_tianxing_panel_calibration(now, "天星探索前置确认超时，先查盘校准")
            state["wild_training_last_result"] = "天星探索前置确认超时，等待查盘校准"
            state["wild_training_last_result_at"] = 0
            state["wild_training_last_error"] = ""
            save_state()
            return False
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


async def _guard_deep_wild_training_send(now):
    if not state.get("tianxing_enabled"):
        return True
    preflight = build_tianxing_route_preflight_plan("探索", reason="野外历练发送前复核", now=now, require_change_fate=True)
    if preflight.get("route_allowed"):
        return True
    await _send_tianxing_panel_calibration(now, preflight.get("reason") or "深入发送前复核未通过，先查盘校准")
    save_state()
    await send_audit_log("🌌 野外深入发送前复核未通过：已查天机盘校准，本轮不发送深入。", scope="identity", priority="high")
    return False


async def _cleanup_wild_training_pending_timeout(now):
    if _is_unknown_send_summary(state.get("wild_training_last_result")):
        recovered = _recover_unknown_wild_training_from_message_log(now)
        if recovered in {"result", "cooldown"}:
            save_state()
            await _send_wild_training_recovery_audit(recovered)
            return True
        if recovered == "command":
            save_state()
            await send_audit_log(
                f"🏞️ 野外历练发送回捞：已找回命令 msg_id={state['wild_training_reply_to_msg_id']}，继续等待回复或冷却校准。",
                scope="identity",
                limit=220,
            )
            return True
        if recovered == "start" and now < float(state.get("wild_training_reply_due_at", 0) or 0):
            save_state()
            console_log(
                f"🏞️ 野外历练日志补偿：已出发，继续等待结果编辑（msg_id={state['wild_training_reply_to_msg_id']}）",
                scope="identity",
            )
            return True
        if now < float(state.get("wild_training_reply_due_at", 0) or 0):
            return False
        if not state.get("tianxing_enabled"):
            next_time = _mark_unknown_send_short_retry(
                now,
                "野外历练发送状态未知且消息日志未捞到命令，普通野外短退避后重试",
            )
            save_state()
            await send_audit_log(
                f"🏞️ 野外历练发送状态未知：日志未捞到命令，普通野外短退避后重试至 {fmt_abs_ts(next_time)}。",
                scope="identity",
                priority="normal",
            )
            return True
        mark_tianxing_route_result_unknown(
            "探索",
            now=now,
            reason="野外历练发送状态未知且消息日志未捞到反馈，先查盘校准",
        )
        await _send_tianxing_panel_calibration(now, "野外历练发送状态未知且消息日志未捞到反馈，已转天机盘校准")
        next_time = _mark_unknown_send_short_retry(
            now,
            "野外历练发送状态未知且消息日志未捞到命令，已查天机盘校准后短退避重试",
        )
        save_state()
        await send_audit_log(
            f"🌌 野外历练发送状态未知：日志未捞到命令，已查天机盘校准；短退避至 {fmt_abs_ts(next_time)}。",
            scope="identity",
            priority="high",
        )
        return True

    reply_to_msg_id = int(state.get("wild_training_reply_to_msg_id", 0) or 0)
    if reply_to_msg_id <= 0:
        recovered = _recover_cleared_wild_training_retry_from_message_log(now)
        if recovered in {"result", "cooldown"}:
            save_state()
            await _send_wild_training_recovery_audit(recovered)
            return True
        return False
    recovered = _recover_wild_training_from_message_log(now)
    if recovered in {"result", "cooldown"}:
        save_state()
        await _send_wild_training_recovery_audit(recovered)
        return True
    if recovered == "start" and now < float(state.get("wild_training_reply_due_at", 0) or 0):
        save_state()
        console_log(
            f"🏞️ 野外历练日志补偿：已出发，继续等待结果编辑（msg_id={state['wild_training_reply_to_msg_id']}）",
            scope="identity",
        )
        return True
    if now < float(state.get("wild_training_reply_due_at", 0) or 0):
        return False
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    if str(state.get("wild_training_last_result") or "").startswith("已出发"):
        tianxing_unknown = mark_tianxing_route_result_unknown(
            "探索",
            now=now,
            reason="野外历练结果编辑未留存",
        )
        _schedule_next(now)
        state["wild_training_last_result"] = f"结果编辑未留存，已按正常周期恢复，原消息ID={reply_to_msg_id}"
        state["wild_training_last_result_at"] = float(now or 0)
        state["wild_training_last_completed_at"] = float(now or 0)
        state["wild_training_last_error"] = ""
        state["wild_training_retry_count"] = 0
        save_state()
        console_log(f"🏞️ 野外历练{state['wild_training_last_result']}", scope="identity")
        if tianxing_unknown:
            await send_audit_log(
                "🌌 天星探索结算未确认：未拿到最终结算编辑，本轮不计收益；已保守清理探索推命/改命缓存，下一次探索前会重新预检。",
                scope="identity",
                priority="high",
            )
        return True
    if int(state.get("wild_training_retry_count", 0) or 0) < 1:
        state["wild_training_reply_to_msg_id"] = 0
        state["wild_training_reply_due_at"] = 0
        state["wild_training_retry_count"] = int(state.get("wild_training_retry_count", 0) or 0) + 1
        _schedule_retry(now)
        state["wild_training_last_error"] = f"野外历练回复超时，准备补发一次，原消息ID={reply_to_msg_id}"
    else:
        state["wild_training_reply_to_msg_id"] = 0
        state["wild_training_reply_due_at"] = 0
        state["wild_training_retry_count"] = 0
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
    if _has_unknown_send_wait(now):
        return
    if _has_active_wild_training_pending(now):
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
    if await _defer_wild_training_for_deep_retreat_summary_window(now):
        return

    if not await _prepare_wild_training_tianxing_route(now, due_at=now):
        return

    strategy = _effective_wild_training_strategy(now)
    if strategy == "深入" and not await _guard_deep_wild_training_send(now):
        return
    command = get_wild_training_command(strategy)
    try:
        msg = await send_game_command(command, track=False, queue_timeout=WILD_TRAINING_SEND_TIMEOUT_SEC)
    except asyncio.CancelledError:
        _close_wild_training_guard("wild_training_send_cancelled", now)
        raise
    sent_at = float(getattr(msg, "sent_at", 0) or now) if msg else float(now)
    if not msg:
        send_block = classify_game_send_block(get_current_identity_id(), command)
        if was_last_game_send_blocked_by_global(get_current_identity_id(), command):
            state["wild_training_retry_count"] = 0
            state["wild_training_reply_to_msg_id"] = 0
            state["wild_training_reply_due_at"] = 0
            state["wild_training_last_result"] = "全局暂停，等待恢复错峰"
            state["wild_training_last_error"] = ""
            state["next_wild_training_time"] = sent_at + random.uniform(10 * 60, 30 * 60)
            save_state()
            return
        if str(send_block.get("code") or "") == "send_queue_timeout":
            state["wild_training_reply_to_msg_id"] = 0
            state["wild_training_reply_due_at"] = 0
            state["wild_training_last_result"] = "发送队列拥堵，未发出，延后重试"
            state["wild_training_last_result_at"] = 0
            state["wild_training_last_error"] = "野外历练排队超时未发送，延后重试"
            state["next_wild_training_time"] = float(
                sent_at + random.uniform(WILD_TRAINING_SEND_QUEUE_RETRY_MIN_SEC, WILD_TRAINING_SEND_QUEUE_RETRY_MAX_SEC)
            )
            save_state()
            await send_audit_log(f"⏳ {state['wild_training_last_error']}。", scope="identity")
            return
        if send_block.get("status") == "unknown":
            wait_until = _mark_send_unknown(sent_at)
            save_state()
            await send_audit_log(
                f"⚠️ 野外历练发送状态未知，等待被动回复或冷却校准至 {fmt_abs_ts(wait_until)}。",
                scope="identity",
                priority="high",
            )
            return
        guard_next_time, guard_reason = _wild_training_action_guard_wait(command, sent_at)
        if guard_next_time > sent_at:
            state["wild_training_reply_to_msg_id"] = 0
            state["wild_training_reply_due_at"] = 0
            state["wild_training_last_result"] = "安全锁短窗等待，未发送"
            state["wild_training_last_result_at"] = 0
            state["wild_training_last_error"] = guard_reason
            state["next_wild_training_time"] = float(guard_next_time)
            save_state()
            console_log(f"🏞️ 野外历练安全锁短窗等待，延后至 {fmt_abs_ts(guard_next_time)}：{guard_reason}", scope="identity")
            return
        if send_block.get("status") == "unsent":
            state["wild_training_reply_to_msg_id"] = 0
            state["wild_training_reply_due_at"] = 0
            state["wild_training_last_result"] = "运行保护拦截，未发送，延后重试"
            state["wild_training_last_result_at"] = 0
            state["wild_training_last_error"] = f"野外历练未发送: {send_block.get('code') or 'blocked'}"
            state["next_wild_training_time"] = float(
                sent_at + random.uniform(WILD_TRAINING_RETRY_MIN_SEC, WILD_TRAINING_RETRY_MAX_SEC)
            )
            save_state()
            await send_audit_log(f"⏳ {state['wild_training_last_error']}。", scope="identity")
            return
        retry_count = int(state.get("wild_training_retry_count", 0) or 0)
        if await _defer_wild_training_for_dungeon_quiet(
            sent_at,
            action="补发" if retry_count > 0 else "发送",
        ):
            return
        if not str(send_block.get("code") or "").strip():
            wait_until = _mark_send_unknown(sent_at)
            save_state()
            await send_audit_log(
                f"⚠️ 野外历练发送状态未知，等待被动回复或冷却校准至 {fmt_abs_ts(wait_until)}。",
                scope="identity",
                priority="high",
            )
            return
        if int(state.get("wild_training_retry_count", 0) or 0) < 1:
            state["wild_training_retry_count"] = int(state.get("wild_training_retry_count", 0) or 0) + 1
            _schedule_retry(sent_at)
            state["wild_training_last_error"] = state.get("wild_training_last_error") or "野外历练发送失败，准备补发一次"
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
