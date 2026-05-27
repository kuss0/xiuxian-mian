import asyncio
import random
import re
import time
from datetime import datetime, timedelta

from ..config import (
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_GANGFENG,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    RETRY_MAX_SEC,
    TIANTI_CD_RANDOM_MAX_SEC,
    TIANTI_CD_RANDOM_MIN_SEC,
    TIANTI_GANGFENG_CD_SECONDS,
    TIANTI_RANK_CD_SECONDS,
    TZ_LOCAL,
)
from ..persistence import mark_dirty, save_state
from ..runtime import _fire_and_forget, console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_pending_command, get_tianti_rank_choice, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, get_day_key, has_wait_time, parse_wait_time
from .resource_backoff import record_resource_shortage, reset_resource_shortage

RE_TIANTI_PANEL = re.compile(r"【凌霄云阶】")
RE_TIANTI_PROGRESS = re.compile(r"当前进度[:：]\s*(\d+)\s*/\s*(\d+)\s*阶")
RE_TIANTI_CYCLE = re.compile(r"已完成周天[:：]\s*(\d+)\s*轮")
RE_TIANTI_GANGFENG = re.compile(r"罡风淬体[:：]\s*(\d+)\s*/\s*(\d+)\s*层")
RE_TIANTI_COOLDOWN = re.compile(r"登阶冷却[:：]\s*(.+)")
RE_TIANTI_WENXIN = re.compile(r"问心状态[:：]\s*(.+)")
RE_TIANTI_WENXIN_PANEL = re.compile(r"【问心台回响】")
RE_TIANTI_WENXIN_GAIN_CONTRIB = re.compile(r"你因此获得了\s*(\d+)\s*点宗门贡献")
RE_TIANTI_WENXIN_EXTRA_GANGFENG = re.compile(r"九天罡风顺势入体，你的【罡风淬体】额外提升了\s*(\d+)\s*层")
RE_TIANTI_WENXIN_FAIL = re.compile(r"你今日已在问心台前静坐过一次，道台不会再回应你。")
RE_TIANTI_CLIMB_COST = re.compile(r"你消耗了\s*(\d+)\s*点修为")
RE_TIANTI_CLIMB_GAIN = re.compile(r"本次获得\s*(\d+)\s*点修为[、,，]\s*(\d+)\s*点宗门贡献")
RE_TIANTI_CLIMB_CYCLE = re.compile(r"完成了第\s*(\d+)\s*轮【周天巡天】")
RE_TIANTI_CLIMB_RESULT = re.compile(r"当前云阶进度[:：]\s*(\d+)\s*/\s*(\d+)[，,]\s*罡风淬体[:：]\s*(\d+)\s*/\s*(\d+)")
RE_TIANTI_GANGFENG_PANEL = re.compile(r"【九天罡风】")
RE_TIANTI_GANGFENG_COST = re.compile(r"消耗了\s*(\d+)\s*点修为")
RE_TIANTI_GANGFENG_RESULT = re.compile(r"【罡风淬体】提升至\s*(\d+)\s*/\s*(\d+)\s*层")
RE_TIANTI_GANGFENG_FAIL = re.compile(r"九天罡风尚未再聚，请在\s*(.+?)\s*后再(?:施展此术|试)。")
RE_TIANTI_GANGFENG_COOLDOWN = re.compile(r"\.引九天罡风[:：]\s*(.+)")
TIANTI_CLIMB_RESOURCE_KEY = "tianti_climb"
TIANTI_GANGFENG_RESOURCE_KEY = "tianti_gangfeng"
TIANTI_CLIMB_INFLIGHT_SEC = 180
TIANTI_STATUS_FRESH_SEC = 30 * 60
TIANTI_TRIGGER_BUCKET_SEC = 600
TIANTI_GANGFENG_INFLIGHT_GATE_SEC = RETRY_MAX_SEC + 10
TIANTI_WENXIN_INFLIGHT_GATE_SEC = RETRY_MAX_SEC + 10
TIANTI_WENXIN_DAY_END_FALLBACK_SEC = 45 * 60
_TIANTI_CLIMB_INFLIGHT_UNTIL = {}


def _set_tianti_next_wenxin_time(next_time, *, persist=False):
    state["next_tianti_wenxin_time"] = float(next_time or 0)
    if persist:
        save_state()
    else:
        mark_dirty()


def _set_tianti_next_climb_time(next_time, *, persist=False):
    state["next_tianti_climb_time"] = float(next_time or 0)
    if persist:
        save_state()
    else:
        mark_dirty()


def _set_tianti_next_gangfeng_time(next_time, *, persist=False):
    state["next_tianti_gangfeng_time"] = float(next_time or 0)
    if persist:
        save_state()
    else:
        mark_dirty()


def _schedule_tianti_wenxin_retry(now, *, persist=False):
    next_time = _get_tianti_day_end_ts(now) + random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
    _set_tianti_next_wenxin_time(next_time, persist=persist)
    return next_time


def _get_tianti_cd_seconds(rank_choice=None):
    rank_choice = (rank_choice or get_tianti_rank_choice()).strip()
    return int(TIANTI_RANK_CD_SECONDS.get(rank_choice, TIANTI_RANK_CD_SECONDS["普通"]))


def _log_tianti_plan(prefix, *, scope="auto"):
    console_log(
        f"☁️ {prefix}：current={int(state.get('tianti_progress_current', 0) or 0)} remain={int(state.get('tianti_remaining_climb_count', 0) or 0)} target={int(state.get('tianti_theoretical_max_stage', 0) or 0)} trigger={int(state.get('tianti_wenxin_trigger_stage', 0) or 0)} next={fmt_abs_ts(float(state.get('next_tianti_climb_time', 0) or 0))}",
        scope=scope,
    )


def _set_tianti_skip_reason(reason):
    if str(state.get("tianti_last_skip_reason") or "") == reason:
        return False
    state["tianti_last_skip_reason"] = reason
    return True


def _has_pending_tianti_command(command):
    command = str(command or "").strip()
    if not command:
        return False
    for pending in state.get("pending_tasks", {}).values():
        pending_command = get_pending_command(pending)
        if pending_command == command or pending_command.startswith(f"{command} "):
            return True
    return False


def _tianti_inflight_key(send_as_id=None):
    identity_id = int(send_as_id or get_current_identity_id() or 0)
    return identity_id


def _has_tianti_climb_send_inflight(now=None, send_as_id=None):
    now = float(now or time.time())
    key = _tianti_inflight_key(send_as_id)
    if key <= 0:
        return False
    until = float(_TIANTI_CLIMB_INFLIGHT_UNTIL.get(key, 0) or 0)
    if until <= now:
        _TIANTI_CLIMB_INFLIGHT_UNTIL.pop(key, None)
        return False
    return True


def _reserve_tianti_climb_send(now=None, send_as_id=None):
    now = float(now or time.time())
    key = _tianti_inflight_key(send_as_id)
    if key <= 0:
        return False
    if _has_tianti_climb_send_inflight(now, send_as_id=key):
        return False
    if _has_pending_tianti_command(CMD_TIANTI_CLIMB):
        return False
    _TIANTI_CLIMB_INFLIGHT_UNTIL[key] = now + TIANTI_CLIMB_INFLIGHT_SEC
    return True


def _clear_tianti_climb_send_inflight(send_as_id=None):
    key = _tianti_inflight_key(send_as_id)
    if key > 0:
        _TIANTI_CLIMB_INFLIGHT_UNTIL.pop(key, None)


async def _send_due_tianti_climb_after_status(send_as_id, delay=None, reserved=False):
    if delay is None:
        delay = random.uniform(2, 5)
    await asyncio.sleep(delay)
    now = time.time()
    with use_identity(send_as_id):
        if not state.get("tianti_enabled"):
            return
        next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
        if next_climb_time <= 0 or now < next_climb_time:
            if reserved:
                _clear_tianti_climb_send_inflight(send_as_id)
            return
        if not reserved and not _reserve_tianti_climb_send(now, send_as_id=send_as_id):
            return
        should_trigger_wenxin, _wenxin_state = _should_trigger_tianti_wenxin(now)
        should_trigger_gangfeng, _gangfeng_state = _should_trigger_tianti_gangfeng(now)
        if should_trigger_wenxin or should_trigger_gangfeng:
            _clear_tianti_climb_send_inflight(send_as_id)
            return
        console_log("☁️ 天阶状态显示可登，接续排队登天阶。")

    msg = await send_game_command(CMD_TIANTI_CLIMB, max_retry=1, send_as_id=send_as_id, priority="chain")
    with use_identity(send_as_id):
        if not msg:
            _clear_tianti_climb_send_inflight(send_as_id)
            _set_tianti_next_climb_time(time.time() + RETRY_MAX_SEC, persist=True)
            state["tianti_last_error"] = "登天阶接续发送失败"
            await send_audit_log("❌ 登天阶接续发送失败，稍后重试。", scope="identity", send_as_id=send_as_id)
            return
        state["tianti_last_climb_msg_id"] = int(getattr(msg, "id", 0) or 0)
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        _schedule_tianti_climb_retry(sent_at, persist=True)
        _calc_tianti_wenxin_plan(sent_at)
        _log_tianti_plan("状态接续登阶后")
        console_log(f"☁️ 执行登天阶→{fmt_abs_ts(float(state.get('next_tianti_climb_time', 0) or 0))}")


def _reset_tianti_wenxin_daily_state(now):
    state["tianti_last_wenxin_day"] = ""
    state["tianti_wenxin_last_trigger_key"] = ""
    state["tianti_theoretical_max_stage"] = 0
    state["tianti_wenxin_trigger_stage"] = 0
    state["tianti_last_skip_reason"] = ""
    state["next_tianti_wenxin_time"] = 0


def _ensure_tianti_wenxin_daily_state(now):
    today_key = get_day_key(now)
    last_wenxin_day = str(state.get("tianti_last_wenxin_day") or "")
    trigger_key = str(state.get("tianti_wenxin_last_trigger_key") or "")

    should_reset = False
    if last_wenxin_day and last_wenxin_day != today_key:
        should_reset = True
    elif trigger_key and not trigger_key.startswith(f"{today_key}|"):
        should_reset = True

    if not should_reset:
        return False

    _reset_tianti_wenxin_daily_state(now)
    console_log("☁️ 问心日切：reset daily state")
    return True


def _get_tianti_day_end_ts(now):
    local_now = datetime.fromtimestamp(now, TZ_LOCAL)
    next_day = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_day.timestamp()



def _estimate_tianti_remaining_climb_count(now):
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    day_end_ts = _get_tianti_day_end_ts(now)
    cd_seconds = _get_tianti_cd_seconds()
    if next_climb_time <= 0 or cd_seconds <= 0 or next_climb_time >= day_end_ts:
        return 0
    remaining_count = 1 + int((day_end_ts - next_climb_time - 1) // cd_seconds)
    return max(0, remaining_count)



def _sync_tianti_remaining_climb_count(now):
    remaining_count = _estimate_tianti_remaining_climb_count(now)
    if int(state.get("tianti_remaining_climb_count", 0) or 0) == remaining_count:
        return False
    state["tianti_remaining_climb_count"] = remaining_count
    return True



def _calc_tianti_wenxin_plan(now=None):
    if now is not None:
        _sync_tianti_remaining_climb_count(now)

    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    max_stage = int(state.get("tianti_progress_total", 12) or 12)
    remaining_count = int(state.get("tianti_remaining_climb_count", 0) or 0)

    target_stage = 0
    trigger_stage = 0
    if remaining_count > 0 and 0 <= current_stage <= max_stage:
        if current_stage >= max_stage:
            target_stage = max_stage
            trigger_stage = 0
        else:
            target_stage = max_stage if current_stage + remaining_count >= max_stage else current_stage + remaining_count
            trigger_stage = max(0, target_stage - 1)

    changed = False
    if int(state.get("tianti_theoretical_max_stage", 0) or 0) != target_stage:
        state["tianti_theoretical_max_stage"] = target_stage
        changed = True
    if int(state.get("tianti_wenxin_trigger_stage", 0) or 0) != trigger_stage:
        state["tianti_wenxin_trigger_stage"] = trigger_stage
        changed = True
    return target_stage, trigger_stage, changed


def _tianti_window_bucket(ts):
    return int(float(ts or 0) // TIANTI_TRIGGER_BUCKET_SEC)


def _build_tianti_wenxin_trigger_key(now, trigger_reason=""):
    target_stage = int(state.get("tianti_theoretical_max_stage", 0) or 0)
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    if target_stage <= 0 or next_climb_time <= 0:
        return ""
    return (
        f"{get_day_key(now)}|{current_stage}|{target_stage}|"
        f"bucket={_tianti_window_bucket(next_climb_time)}|{trigger_reason}"
    )


def _is_same_tianti_wenxin_trigger_window(stored_key, trigger_key, now, current_stage, target_stage, next_climb_time, trigger_reason):
    stored_key = str(stored_key or "").strip()
    if not stored_key:
        return False
    if stored_key == str(trigger_key or ""):
        return True

    # Legacy trigger keys used exact next-climb seconds:
    # YYYY-MM-DD|current|target|next_climb_ts|reason.
    parts = stored_key.split("|")
    if len(parts) != 5 or parts[0] != get_day_key(now):
        return False
    if parts[1] != str(int(current_stage or 0)) or parts[2] != str(int(target_stage or 0)):
        return False
    if parts[4] != str(trigger_reason or ""):
        return False
    try:
        stored_next_climb = int(float(parts[3]))
    except (TypeError, ValueError):
        return False
    return _tianti_window_bucket(stored_next_climb) == _tianti_window_bucket(next_climb_time)


def _should_defer_wenxin_by_timer(now, today_key):
    next_wenxin_time = float(state.get("next_tianti_wenxin_time", 0) or 0)
    if next_wenxin_time <= 0 or now >= next_wenxin_time:
        return False
    if str(state.get("tianti_last_error") or "") == "问心台发送失败":
        return True
    if next_wenxin_time - now <= RETRY_MAX_SEC + 60:
        return True
    next_day_key = get_day_key(next_wenxin_time)
    return bool(next_day_key and next_day_key > today_key)


def _should_advance_tianti_wenxin_for_gangfeng(now, current_stage, trigger_stage):
    if trigger_stage <= 0 or current_stage != trigger_stage - 1:
        return False
    if not state.get("tianti_gangfeng_enabled"):
        return False
    if int(state.get("tianti_cycle_count", 0) or 0) < 1:
        return False
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    next_gangfeng_time = float(state.get("next_tianti_gangfeng_time", 0) or 0)
    if next_climb_time <= 0 or next_gangfeng_time <= now:
        return False
    original_wenxin_cycle_start = next_climb_time
    original_wenxin_cycle_end = next_climb_time + _get_tianti_cd_seconds()
    return original_wenxin_cycle_start <= next_gangfeng_time <= original_wenxin_cycle_end


def _should_trigger_tianti_wenxin(now):
    if not state.get("tianti_wenxin_enabled"):
        return False, "wenxin_disabled"
    today_key = get_day_key(now)
    if str(state.get("tianti_last_wenxin_day") or "") == today_key:
        return False, "already_done"
    if _has_pending_tianti_command(CMD_TIANTI_WENXIN):
        return False, "wenxin_pending"
    if _should_defer_wenxin_by_timer(now, today_key):
        return False, "wenxin_not_today"

    remaining_count = int(state.get("tianti_remaining_climb_count", 0) or 0)
    day_end_ts = _get_tianti_day_end_ts(now)
    if remaining_count <= 0:
        if 0 < day_end_ts - now <= TIANTI_WENXIN_DAY_END_FALLBACK_SEC:
            trigger_key = f"{today_key}|day_end_fallback"
            if str(state.get("tianti_wenxin_last_trigger_key") or "") == trigger_key:
                return False, "trigger_key_hit"
            return True, trigger_key
        return False, "remain=0"

    target_stage = int(state.get("tianti_theoretical_max_stage", 0) or 0)
    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    progress_total = int(state.get("tianti_progress_total", 12) or 12)
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    if target_stage <= 0:
        return False, "target=0"
    if next_climb_time <= 0:
        return False, "next_climb=0"

    if target_stage >= progress_total:
        should_use = current_stage == progress_total - 1
        trigger_reason = "final_stage"
    else:
        should_use = remaining_count == 1
        trigger_reason = "last_climb_today"
    if not should_use:
        return False, f"wait_{trigger_reason}"

    window_start = next_climb_time - 600
    if not (window_start <= now < next_climb_time):
        return False, "window_closed"
    trigger_key = _build_tianti_wenxin_trigger_key(now, trigger_reason)
    if _is_same_tianti_wenxin_trigger_window(
        state.get("tianti_wenxin_last_trigger_key"),
        trigger_key,
        now,
        current_stage,
        target_stage,
        next_climb_time,
        trigger_reason,
    ):
        return False, "trigger_key_hit"
    return True, trigger_key


def get_tianti_estimated_wenxin_window_text(now=None):
    if now is None:
        now = datetime.now(TZ_LOCAL).timestamp()

    if not state.get("tianti_wenxin_enabled"):
        return "已关闭"

    if str(state.get("tianti_last_wenxin_day") or "") == get_day_key(now):
        return "今日已问心"

    remaining_count = int(state.get("tianti_remaining_climb_count", 0) or 0)
    if remaining_count <= 0:
        day_end_ts = _get_tianti_day_end_ts(now)
        if 0 < day_end_ts - now <= TIANTI_WENXIN_DAY_END_FALLBACK_SEC:
            return f"日切前兜底：{fmt_abs_ts(now)} - {fmt_abs_ts(day_end_ts)}"
        return f"今日无预计登阶，日切前 {int(TIANTI_WENXIN_DAY_END_FALLBACK_SEC // 60)} 分钟兜底"

    target_stage = int(state.get("tianti_theoretical_max_stage", 0) or 0)
    trigger_stage = int(state.get("tianti_wenxin_trigger_stage", 0) or 0)
    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    progress_total = int(state.get("tianti_progress_total", 12) or 12)
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)

    if target_stage <= 0:
        return "等待状态同步"
    if next_climb_time <= 0:
        return "等待下次登阶时间"
    if current_stage >= progress_total:
        return "当前已满阶，无需问心"

    note = ""
    if target_stage >= progress_total:
        trigger_stage = progress_total - 1
        if current_stage == trigger_stage:
            window_end = next_climb_time
            note = "下一次登第12阶，优先使用问心台"
        elif current_stage < trigger_stage:
            climbs_until_trigger = trigger_stage - current_stage
            window_end = next_climb_time + climbs_until_trigger * _get_tianti_cd_seconds()
            note = f"预计到达 {trigger_stage}/{progress_total} 后，留给第12阶"
        else:
            return "当前进度已超过第12阶触发点，等待重新规划"
    elif remaining_count == 1:
        window_end = next_climb_time
        note = "今日到不了第12阶，留给今日最后一次登阶"
    else:
        climbs_until_last = max(0, remaining_count - 1)
        window_end = next_climb_time + climbs_until_last * _get_tianti_cd_seconds()
        note = "今日到不了第12阶，留给今日最后一次登阶"

    window_start = max(0, window_end - 600)
    return f"{fmt_abs_ts(window_start)} - {fmt_abs_ts(window_end)}（{note}）"


def _apply_tianti_wenxin_result(raw_text, now, reply_to):
    handled = False
    if RE_TIANTI_WENXIN_FAIL.search(raw_text):
        state["tianti_last_wenxin_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
        state["tianti_wenxin_status"] = "今日已问心"
        state["tianti_last_wenxin_day"] = get_day_key(now)
        _schedule_tianti_wenxin_retry(now, persist=False)
        console_log("☁️ 问心收口：今日已问心")
        return True

    if not RE_TIANTI_WENXIN_PANEL.search(raw_text):
        return False

    state["tianti_last_wenxin_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
    state["tianti_wenxin_status"] = "今日已问心，下次登天阶奖励提升"
    state["tianti_last_wenxin_day"] = get_day_key(now)
    console_log("☁️ 问心收口：成功，下次登天阶奖励提升")
    handled = True

    extra_gangfeng_match = RE_TIANTI_WENXIN_EXTRA_GANGFENG.search(raw_text)
    if extra_gangfeng_match:
        extra_level = int(extra_gangfeng_match.group(1) or 0)
        if extra_level > 0:
            state["tianti_gangfeng_level"] = int(state.get("tianti_gangfeng_level", 0) or 0) + extra_level
            handled = True

    _schedule_tianti_wenxin_retry(now, persist=False)
    return handled


def _schedule_tianti_climb_retry(now, rank_choice=None, *, persist=False):
    rank_choice = (rank_choice or get_tianti_rank_choice()).strip()
    cd_seconds = int(TIANTI_RANK_CD_SECONDS.get(rank_choice, TIANTI_RANK_CD_SECONDS["普通"]))
    random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
    next_time = float(now) + cd_seconds + random_delay
    _set_tianti_next_climb_time(next_time, persist=persist)
    state["tianti_cooldown_text"] = fmt_time_after(cd_seconds + random_delay)
    if persist:
        save_state()
    else:
        mark_dirty()
    return next_time


def _schedule_tianti_gangfeng_retry(now, wait_sec=None, *, persist=False):
    base_wait = TIANTI_GANGFENG_CD_SECONDS if wait_sec is None else max(0, int(wait_sec or 0))
    random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
    total_wait_sec = base_wait + random_delay
    next_time = float(now) + total_wait_sec
    state["next_tianti_gangfeng_time"] = float(next_time or 0)
    state["tianti_gangfeng_status"] = fmt_time_after(total_wait_sec)
    if persist:
        save_state()
    else:
        mark_dirty()
    return next_time


def _parse_tianti_gangfeng_wait_reply(raw_text):
    match = RE_TIANTI_GANGFENG_FAIL.search(str(raw_text or ""))
    if not match:
        return 0
    wait_text = str(match.group(1) or "").strip()
    return parse_wait_time(wait_text) if has_wait_time(wait_text) else 0


def _build_tianti_gangfeng_trigger_key(now, next_climb_time=None):
    if next_climb_time is None:
        next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    next_climb_time = float(next_climb_time or 0)
    if next_climb_time <= 0:
        return ""
    bucket = _tianti_window_bucket(next_climb_time)
    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    return f"{get_day_key(now)}|stage={current_stage}|bucket={bucket}"


def _is_same_tianti_gangfeng_trigger_window(stored_key, trigger_key, now, next_climb_time):
    stored_key = str(stored_key or "").strip()
    if not stored_key:
        return False
    if stored_key == str(trigger_key or ""):
        return True

    # Legacy trigger keys used exact second timestamps: YYYY-MM-DD|next_climb_ts.
    # Treat those as the same 10-minute pre-climb window so a small status jitter
    # cannot unlock a second gangfeng send.
    parts = stored_key.split("|")
    if len(parts) != 2 or parts[0] != get_day_key(now):
        return False
    try:
        stored_next_climb = int(float(parts[1]))
    except (TypeError, ValueError):
        return False
    current_bucket = _tianti_window_bucket(next_climb_time)
    stored_bucket = _tianti_window_bucket(stored_next_climb)
    return current_bucket == stored_bucket


def _should_trigger_tianti_gangfeng(now):
    if not state.get("tianti_gangfeng_enabled"):
        return False, "gangfeng_disabled"
    if _has_pending_tianti_command(CMD_TIANTI_GANGFENG):
        return False, "gangfeng_pending"
    if not _has_fresh_tianti_status_snapshot(now):
        return False, "gangfeng_status_stale"
    if int(state.get("tianti_cycle_count", 0) or 0) < 1:
        return False, "cycle<1"
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    next_gangfeng_time = float(state.get("next_tianti_gangfeng_time", 0) or 0)
    if next_climb_time <= 0:
        return False, "next_climb=0"
    if next_gangfeng_time > now:
        return False, "gangfeng_cd"
    trigger_key = _build_tianti_gangfeng_trigger_key(now, next_climb_time)
    if _is_same_tianti_gangfeng_trigger_window(
        state.get("tianti_gangfeng_last_trigger_key"),
        trigger_key,
        now,
        next_climb_time,
    ):
        return False, "gangfeng_trigger_key_hit"
    window_start = next_climb_time - 600
    if not (window_start <= now < next_climb_time):
        return False, "gangfeng_window_closed"
    return True, trigger_key


def _has_tianti_status_snapshot():
    return any(
        value not in {None, "", 0, "未记录"}
        for value in (
            state.get("tianti_progress_current"),
            state.get("tianti_cycle_count"),
            state.get("tianti_gangfeng_level"),
            state.get("tianti_cooldown_text"),
            state.get("tianti_wenxin_status"),
        )
    )


def _has_fresh_tianti_status_snapshot(now):
    if not _has_tianti_status_snapshot():
        return False
    seen_at = float(state.get("tianti_last_status_seen_at", 0) or 0)
    return seen_at > 0 and float(now) - seen_at <= TIANTI_STATUS_FRESH_SEC


def _active_tianti_status_sync_due(now):
    if _has_fresh_tianti_status_snapshot(now):
        return False
    if state.get("tianti_gangfeng_enabled"):
        next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
        next_gangfeng_time = float(state.get("next_tianti_gangfeng_time", 0) or 0)
        if next_climb_time > 0 and next_gangfeng_time <= now and next_climb_time - 600 <= now < next_climb_time:
            return True
    return False


def _tianti_status_sync_due(now):
    next_status_time = float(state.get("next_tianti_status_time", 0) or 0)
    if next_status_time > 0 and now >= next_status_time:
        return True
    if not _has_tianti_status_snapshot():
        return True
    if _active_tianti_status_sync_due(now):
        return True
    return False


async def sync_tianti_status(send_as_id):
    send_as_id = int(send_as_id)
    msg = await send_game_command(CMD_TIANTI_STATUS, track=False, send_as_id=send_as_id)
    if not msg:
        return False, "天阶状态同步发送失败"
    return True, f"已发送天阶状态同步指令[{send_as_id}]，等待回复"


def _is_tianti_reply(text, reply_to, matched_family=None):
    if matched_family in {"tianti_status", "tianti_wenxin", "tianti_climb", "tianti_gangfeng"}:
        return True
    raw_text = str(text or "")
    if RE_TIANTI_PANEL.search(raw_text) or RE_TIANTI_GANGFENG_PANEL.search(raw_text) or RE_TIANTI_GANGFENG_FAIL.search(raw_text):
        return True
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    return any(command in orig_cmd for command in {CMD_TIANTI_STATUS, CMD_TIANTI_WENXIN, CMD_TIANTI_CLIMB, CMD_TIANTI_GANGFENG})


def _parse_tianti_panel(text):
    raw_text = str(text or "")
    if not RE_TIANTI_PANEL.search(raw_text):
        return None

    payload = {}
    progress_match = RE_TIANTI_PROGRESS.search(raw_text)
    if progress_match:
        payload["progress_current"] = int(progress_match.group(1) or 0)
        payload["progress_total"] = int(progress_match.group(2) or 0)
    cycle_match = RE_TIANTI_CYCLE.search(raw_text)
    if cycle_match:
        payload["cycle_count"] = int(cycle_match.group(1) or 0)
    gangfeng_match = RE_TIANTI_GANGFENG.search(raw_text)
    if gangfeng_match:
        payload["gangfeng_level"] = int(gangfeng_match.group(1) or 0)
        payload["gangfeng_total"] = int(gangfeng_match.group(2) or 0)
    cooldown_match = RE_TIANTI_COOLDOWN.search(raw_text)
    if cooldown_match:
        payload["cooldown_text"] = str(cooldown_match.group(1) or "").strip()
    wenxin_match = RE_TIANTI_WENXIN.search(raw_text)
    if wenxin_match:
        payload["wenxin_status"] = str(wenxin_match.group(1) or "").strip()
    gangfeng_cd_match = RE_TIANTI_GANGFENG_COOLDOWN.search(raw_text)
    if gangfeng_cd_match:
        payload["gangfeng_cooldown_text"] = str(gangfeng_cd_match.group(1) or "").strip()
    return payload or None


def _apply_tianti_panel_payload(payload, now=None):
    if not isinstance(payload, dict):
        return False
    if now is None:
        now = datetime.now(TZ_LOCAL).timestamp()
    changed = False
    mapping = {
        "progress_current": "tianti_progress_current",
        "progress_total": "tianti_progress_total",
        "cycle_count": "tianti_cycle_count",
        "gangfeng_level": "tianti_gangfeng_level",
        "gangfeng_total": "tianti_gangfeng_total",
        "cooldown_text": "tianti_cooldown_text",
        "wenxin_status": "tianti_wenxin_status",
    }
    for payload_key, state_key in mapping.items():
        if payload_key not in payload:
            continue
        value = payload[payload_key]
        if state.get(state_key) != value:
            state[state_key] = value
            changed = True

    cooldown_text = str(payload.get("cooldown_text") or "")
    if cooldown_text:
        if has_wait_time(cooldown_text):
            wait_sec = parse_wait_time(cooldown_text)
            if wait_sec > 0:
                random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
                total_wait_sec = wait_sec + random_delay
                next_climb = float(now + total_wait_sec)
                if abs(float(state.get("next_tianti_climb_time", 0) or 0) - next_climb) > 1:
                    _set_tianti_next_climb_time(next_climb, persist=False)
                    changed = True
                display_text = fmt_time_after(total_wait_sec)
                if state.get("tianti_cooldown_text") != display_text:
                    state["tianti_cooldown_text"] = display_text
                    changed = True
        else:
            next_climb = float(state.get("next_tianti_climb_time", 0) or 0)
            if not _has_pending_tianti_command(CMD_TIANTI_CLIMB) and (next_climb <= 0 or next_climb > now):
                _set_tianti_next_climb_time(now, persist=False)
                changed = True

    wenxin_text = str(payload.get("wenxin_status") or "")
    if wenxin_text:
        today_key = get_day_key(now)
        if "今日尚未问心" in wenxin_text:
            if str(state.get("tianti_last_wenxin_day") or "") == today_key:
                state["tianti_last_wenxin_day"] = ""
                changed = True
            if float(state.get("next_tianti_wenxin_time", 0) or 0) > 0:
                _set_tianti_next_wenxin_time(0, persist=False)
                changed = True
        elif "今日已问心" in wenxin_text or "不会再回应" in wenxin_text:
            if str(state.get("tianti_last_wenxin_day") or "") != today_key:
                state["tianti_last_wenxin_day"] = today_key
                changed = True
            if float(state.get("next_tianti_wenxin_time", 0) or 0) <= now:
                _schedule_tianti_wenxin_retry(now, persist=False)
                changed = True

    gangfeng_cd_text = str(payload.get("gangfeng_cooldown_text") or "")
    if gangfeng_cd_text:
        if "未解锁" in gangfeng_cd_text:
            pass
        elif "可用" in gangfeng_cd_text:
            if float(state.get("next_tianti_gangfeng_time", 0) or 0) > 0:
                _set_tianti_next_gangfeng_time(0, persist=False)
                changed = True
            if state.get("tianti_gangfeng_status") != "可用":
                state["tianti_gangfeng_status"] = "可用"
                changed = True
        elif has_wait_time(gangfeng_cd_text):
            wait_sec = parse_wait_time(gangfeng_cd_text)
            if wait_sec > 0:
                random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
                total_wait_sec = wait_sec + random_delay
                next_gangfeng = float(now + total_wait_sec)
                if abs(float(state.get("next_tianti_gangfeng_time", 0) or 0) - next_gangfeng) > 1:
                    _set_tianti_next_gangfeng_time(next_gangfeng, persist=False)
                    changed = True
                display_text = fmt_time_after(total_wait_sec)
                if state.get("tianti_gangfeng_status") != display_text:
                    state["tianti_gangfeng_status"] = display_text
                    changed = True
    return changed


def get_tianti_status_text():
    now = datetime.now(TZ_LOCAL).timestamp()
    lines = [
        "☁️ 登天阶",
        f"- 当前进度：{int(state.get('tianti_progress_current', 0) or 0)} / {int(state.get('tianti_progress_total', 12) or 12)} 阶",
        f"- 已完成周天：{int(state.get('tianti_cycle_count', 0) or 0)} 轮",
        f"- 罡风淬体：{int(state.get('tianti_gangfeng_level', 0) or 0)} / {int(state.get('tianti_gangfeng_total', 12) or 12)} 层",
        f"- 问心状态：{state.get('tianti_wenxin_status') or '未记录'}",
        f"- 今日剩余次数：{int(state.get('tianti_remaining_climb_count', 0) or 0)}",
        f"- 今日目标阶：{int(state.get('tianti_theoretical_max_stage', 0) or 0)} ｜ 触发阶：{int(state.get('tianti_wenxin_trigger_stage', 0) or 0)}",
        f"- 下次问心：{fmt_abs_ts(float(state.get('next_tianti_wenxin_time', 0) or 0))}（{fmt_remaining(float(state.get('next_tianti_wenxin_time', 0) or 0))}）",
        f"- 预计问心窗口：{get_tianti_estimated_wenxin_window_text(now)}",
        f"- 下次登阶：{fmt_abs_ts(float(state.get('next_tianti_climb_time', 0) or 0))}（{fmt_remaining(float(state.get('next_tianti_climb_time', 0) or 0))}）",
        f"- 下次罡风：{fmt_abs_ts(float(state.get('next_tianti_gangfeng_time', 0) or 0))}（{fmt_remaining(float(state.get('next_tianti_gangfeng_time', 0) or 0))}）",
    ]
    last_gain_xiuwei = int(state.get("tianti_last_gain_xiuwei", 0) or 0)
    last_gain_contrib = int(state.get("tianti_last_gain_contrib", 0) or 0)
    last_cost_xiuwei = int(state.get("tianti_last_cost_xiuwei", 0) or 0)
    if last_gain_xiuwei > 0 or last_gain_contrib > 0 or last_cost_xiuwei > 0:
        lines.append(
            f"- 最近登阶：消耗 {last_cost_xiuwei} 修为｜获得 {last_gain_xiuwei} 修为 / {last_gain_contrib} 贡献"
        )
    if state.get("tianti_last_error"):
        lines.append(f"- 最近异常：{state.get('tianti_last_error')}")
    return "\n".join(lines)


async def handle_tianti_reply(text, now, reply_to, matched_family=None):
    if not state.get("tianti_enabled"):
        return False
    if not _is_tianti_reply(text, reply_to, matched_family=matched_family):
        return False

    _ensure_tianti_wenxin_daily_state(now)
    raw_text = str(text or "")
    handled = False

    if matched_family == "tianti_climb" and ("修为不足" in raw_text or "资源不足" in raw_text):
        _clear_tianti_climb_send_inflight()
        backoff = record_resource_shortage(TIANTI_CLIMB_RESOURCE_KEY, now, reason=raw_text)
        due_at = float(backoff.get("next_at", 0) or 0)
        _set_tianti_next_climb_time(due_at, persist=False)
        state["tianti_cooldown_text"] = fmt_time_after(max(0, due_at - now))
        state["tianti_last_error"] = f"登天阶资源不足: {raw_text[:80]}"
        _calc_tianti_wenxin_plan(now)
        save_state()
        await send_audit_log(
            f"⚠️ 登天阶修为不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{state['tianti_cooldown_text']}"
        )
        return True

    if matched_family == "tianti_gangfeng" and ("修为不足" in raw_text or "资源不足" in raw_text):
        backoff = record_resource_shortage(TIANTI_GANGFENG_RESOURCE_KEY, now, reason=raw_text)
        due_at = float(backoff.get("next_at", 0) or 0)
        _set_tianti_next_gangfeng_time(due_at, persist=False)
        state["tianti_gangfeng_status"] = fmt_time_after(max(0, due_at - now))
        state["tianti_last_error"] = f"九天罡风资源不足: {raw_text[:80]}"
        save_state()
        await send_audit_log(
            f"⚠️ 九天罡风修为不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{state['tianti_gangfeng_status']}"
        )
        return True

    panel_payload = _parse_tianti_panel(raw_text)
    if panel_payload:
        state["tianti_last_status_seen_at"] = float(now)
        if _apply_tianti_panel_payload(panel_payload, now=now):
            handled = True
        if matched_family == "tianti_status":
            state["tianti_last_status_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
            state["tianti_status_reply_to_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
            state["next_tianti_status_time"] = 0
            cooldown = str(panel_payload.get("cooldown_text") or "")
            _calc_tianti_wenxin_plan(now)
            _log_tianti_plan("天阶状态同步后")
            if (
                cooldown
                and not has_wait_time(cooldown)
                and float(state.get("next_tianti_climb_time", 0) or 0) <= now
            ):
                send_as_id = int(get_current_identity_id() or 0)
                if send_as_id > 0 and _reserve_tianti_climb_send(now, send_as_id=send_as_id):
                    _fire_and_forget(_send_due_tianti_climb_after_status(send_as_id, reserved=True))
            if cooldown and has_wait_time(cooldown):
                await send_audit_log(f"⏳ 天阶 CD→{state.get('tianti_cooldown_text')}")
            handled = True

    if matched_family == "tianti_wenxin":
        handled = _apply_tianti_wenxin_result(raw_text, now, reply_to) or handled
        _calc_tianti_wenxin_plan(now)
        if handled:
            await send_audit_log(f"☁️ 问心完成：{state.get('tianti_wenxin_status')}")
            _log_tianti_plan("问心收口后")

    if matched_family == "tianti_gangfeng":
        gangfeng_wait_sec = _parse_tianti_gangfeng_wait_reply(raw_text)
        gangfeng_result_match = RE_TIANTI_GANGFENG_RESULT.search(raw_text)
        if RE_TIANTI_GANGFENG_PANEL.search(raw_text) and gangfeng_result_match:
            state["tianti_last_gangfeng_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
            state["tianti_gangfeng_level"] = int(gangfeng_result_match.group(1) or 0)
            state["tianti_gangfeng_total"] = int(gangfeng_result_match.group(2) or 0)
            state["tianti_gangfeng_status"] = "已施展，下次登天阶成功率显著提高"
            reset_resource_shortage(TIANTI_GANGFENG_RESOURCE_KEY)
            _schedule_tianti_gangfeng_retry(now, persist=True)
            await send_audit_log(
                f"🌪️ 九天罡风成功：{int(state.get('tianti_gangfeng_level', 0) or 0)}/{int(state.get('tianti_gangfeng_total', 12) or 12)}｜下次 {state.get('tianti_gangfeng_status')}"
            )
            handled = True
        elif gangfeng_wait_sec > 0:
            state["tianti_last_gangfeng_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
            reset_resource_shortage(TIANTI_GANGFENG_RESOURCE_KEY)
            _schedule_tianti_gangfeng_retry(now, wait_sec=gangfeng_wait_sec, persist=True)
            await send_audit_log(f"⏳ 九天罡风 CD→{state.get('tianti_gangfeng_status')}")
            handled = True

    climb_cost_match = RE_TIANTI_CLIMB_COST.search(raw_text)
    climb_gain_match = RE_TIANTI_CLIMB_GAIN.search(raw_text)
    climb_cycle_match = RE_TIANTI_CLIMB_CYCLE.search(raw_text)
    climb_result_match = RE_TIANTI_CLIMB_RESULT.search(raw_text)
    if climb_cost_match and climb_gain_match and climb_result_match:
        _clear_tianti_climb_send_inflight()
        state["tianti_last_climb_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
        state["tianti_last_cost_xiuwei"] = int(climb_cost_match.group(1) or 0)
        state["tianti_last_gain_xiuwei"] = int(climb_gain_match.group(1) or 0)
        state["tianti_last_gain_contrib"] = int(climb_gain_match.group(2) or 0)
        if climb_cycle_match:
            state["tianti_cycle_count"] = int(climb_cycle_match.group(1) or 0)
        state["tianti_progress_current"] = int(climb_result_match.group(1) or 0)
        state["tianti_progress_total"] = int(climb_result_match.group(2) or 0)
        state["tianti_gangfeng_level"] = int(climb_result_match.group(3) or 0)
        state["tianti_gangfeng_total"] = int(climb_result_match.group(4) or 0)
        state["tianti_last_error"] = ""
        reset_resource_shortage(TIANTI_CLIMB_RESOURCE_KEY)
        _schedule_tianti_climb_retry(now, persist=False)
        _calc_tianti_wenxin_plan(now)
        _log_tianti_plan("登阶成功后")
        await send_audit_log(
            f"☁️ 登阶成功：{int(state.get('tianti_progress_current', 0) or 0)}/{int(state.get('tianti_progress_total', 12) or 12)}｜罡风 {int(state.get('tianti_gangfeng_level', 0) or 0)}/{int(state.get('tianti_gangfeng_total', 12) or 12)}｜下次 {state.get('tianti_cooldown_text')}"
        )
        handled = True

    if matched_family == "tianti_climb" and _parse_tianti_gangfeng_wait_reply(raw_text) > 0:
        wait_sec = _parse_tianti_gangfeng_wait_reply(raw_text)
        _clear_tianti_climb_send_inflight()
        random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
        total_wait_sec = wait_sec + random_delay
        state["tianti_last_climb_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
        reset_resource_shortage(TIANTI_CLIMB_RESOURCE_KEY)
        reset_resource_shortage(TIANTI_GANGFENG_RESOURCE_KEY)
        _set_tianti_next_climb_time(now + total_wait_sec, persist=False)
        _set_tianti_next_gangfeng_time(now + total_wait_sec, persist=False)
        state["tianti_cooldown_text"] = fmt_time_after(total_wait_sec)
        state["tianti_gangfeng_status"] = fmt_time_after(total_wait_sec)
        _calc_tianti_wenxin_plan(now)
        _log_tianti_plan("登阶罡风冷却回复后")
        await send_audit_log(f"⏳ 登阶等待九天罡风→{state.get('tianti_cooldown_text')}")
        handled = True

    if has_wait_time(raw_text) and matched_family == "tianti_climb" and not handled and not (climb_cost_match and climb_gain_match and climb_result_match):
        wait_sec = parse_wait_time(raw_text)
        if wait_sec > 0:
            _clear_tianti_climb_send_inflight()
            random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
            total_wait_sec = wait_sec + random_delay
            state["tianti_last_climb_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
            reset_resource_shortage(TIANTI_CLIMB_RESOURCE_KEY)
            _set_tianti_next_climb_time(now + total_wait_sec, persist=False)
            state["tianti_cooldown_text"] = fmt_time_after(total_wait_sec)
            _calc_tianti_wenxin_plan(now)
            _log_tianti_plan("登阶冷却回复后")
            await send_audit_log(f"⏳ 天阶 CD→{state.get('tianti_cooldown_text')}")
            handled = True

    if handled:
        state["tianti_last_error"] = ""
        save_state()
        return True

    return False


async def run_tianti_scheduler(now):
    if not state.get("tianti_enabled"):
        return

    if _ensure_tianti_wenxin_daily_state(now):
        mark_dirty()
    _calc_tianti_wenxin_plan(now)

    if _tianti_status_sync_due(now):
        if _has_pending_tianti_command(CMD_TIANTI_STATUS):
            return
        msg = await send_game_command(CMD_TIANTI_STATUS, max_retry=1)
        if not msg:
            state["tianti_last_error"] = "天阶状态发送失败"
            state["next_tianti_status_time"] = time.time() + RETRY_MAX_SEC
            await send_audit_log("❌ 登天阶状态发送失败，稍后重试。")
            return
        state["tianti_status_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        state["next_tianti_status_time"] = sent_at + RETRY_MAX_SEC
        _log_tianti_plan("调度前快照")
        console_log("☁️ 查询天阶状态")
        save_state()
        return

    should_trigger_wenxin, wenxin_state = _should_trigger_tianti_wenxin(now)
    if should_trigger_wenxin:
        msg = await send_game_command(CMD_TIANTI_WENXIN, max_retry=1)
        if not msg:
            state["tianti_last_error"] = "问心台发送失败"
            _set_tianti_next_wenxin_time(time.time() + RETRY_MAX_SEC, persist=True)
            await send_audit_log("❌ 问心台发送失败，稍后重试。")
            return
        state["tianti_last_wenxin_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["tianti_wenxin_last_trigger_key"] = str(wenxin_state or "")
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        state["next_tianti_wenxin_time"] = sent_at + TIANTI_WENXIN_INFLIGHT_GATE_SEC
        save_state()
        console_log(
            f"☁️ 执行问心台：target={int(state.get('tianti_theoretical_max_stage', 0) or 0)}｜trigger={int(state.get('tianti_wenxin_trigger_stage', 0) or 0)}"
        )
        return
    if _set_tianti_skip_reason(str(wenxin_state or "")):
        console_log(f"☁️ 问心跳过：{wenxin_state}")

    should_trigger_gangfeng, gangfeng_state = _should_trigger_tianti_gangfeng(now)
    if should_trigger_gangfeng:
        msg = await send_game_command(CMD_TIANTI_GANGFENG, max_retry=1)
        if not msg:
            state["tianti_last_error"] = "九天罡风发送失败"
            _set_tianti_next_gangfeng_time(time.time() + RETRY_MAX_SEC, persist=True)
            await send_audit_log("❌ 九天罡风发送失败，稍后重试。")
            return
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        state["tianti_last_gangfeng_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["tianti_gangfeng_last_trigger_key"] = str(gangfeng_state or "")
        state["next_tianti_gangfeng_time"] = sent_at + TIANTI_GANGFENG_INFLIGHT_GATE_SEC
        state["tianti_gangfeng_status"] = "等待回复"
        save_state()
        console_log("🌪️ 执行九天罡风")
        return

    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    if next_climb_time > 0 and now >= next_climb_time:
        send_as_id = int(get_current_identity_id() or 0)
        if not _reserve_tianti_climb_send(now, send_as_id=send_as_id):
            return
        msg = await send_game_command(CMD_TIANTI_CLIMB, max_retry=1)
        if not msg:
            _clear_tianti_climb_send_inflight(send_as_id)
            _set_tianti_next_climb_time(time.time() + RETRY_MAX_SEC, persist=True)
            state["tianti_last_error"] = "登天阶发送失败"
            await send_audit_log("❌ 登天阶发送失败，稍后重试。")
            return
        state["tianti_last_climb_msg_id"] = int(getattr(msg, "id", 0) or 0)
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        _schedule_tianti_climb_retry(sent_at, persist=True)
        _calc_tianti_wenxin_plan(sent_at)
        _log_tianti_plan("执行登阶后")
        console_log(f"☁️ 执行登天阶→{fmt_abs_ts(float(state.get('next_tianti_climb_time', 0) or 0))}")


__all__ = [
    "get_tianti_estimated_wenxin_window_text",
    "get_tianti_status_text",
    "handle_tianti_reply",
    "run_tianti_scheduler",
    "sync_tianti_status",
]
