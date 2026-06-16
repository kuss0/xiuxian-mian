import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import RE_HOURS, RE_MINUTES, RE_SECONDS, TZ_LOCAL
from .state import get_module_window_hours, state

_save_state = None

CD_STATE_NO_RECORD = "no_record"
CD_STATE_READY = "ready"
CD_STATE_ON_CD = "on_cd"
CD_STATE_UNPARSEABLE = "unparseable"

_CD_NO_RECORD_STRINGS = {"", "none", "null", "undefined"}
_CD_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S UTC+8",
    "%Y-%m-%d %H:%M:%S %Z",
)


@dataclass(frozen=True)
class CooldownDecision:
    state: str
    blocks: bool
    now: float
    window_sec: float
    last_at: float | None = None
    reason: str = ""

    @property
    def ready(self):
        return not self.blocks


def configure_timing(save_state_func):
    global _save_state
    _save_state = save_state_func


def _save_if_available():
    if _save_state is None:
        raise RuntimeError("timing.save_state is not configured")
    _save_state()


def parse_wait_time(text):
    total_seconds = 0
    h = RE_HOURS.search(text)
    m = RE_MINUTES.search(text)
    s = RE_SECONDS.search(text)
    if h:
        total_seconds += int(h.group(1)) * 3600
    if m:
        total_seconds += int(m.group(1)) * 60
    if s:
        total_seconds += int(s.group(1))
    return total_seconds


def has_wait_time(text):
    return any(pattern.search(text or "") for pattern in (RE_HOURS, RE_MINUTES, RE_SECONDS))


def _parse_cd_last_at(raw_last_at):
    if raw_last_at is None:
        return CD_STATE_NO_RECORD, None
    if isinstance(raw_last_at, datetime):
        dt = raw_last_at if raw_last_at.tzinfo else raw_last_at.replace(tzinfo=TZ_LOCAL)
        return CD_STATE_READY, dt.timestamp()
    if isinstance(raw_last_at, (int, float)):
        raw_ts = float(raw_last_at)
        if not math.isfinite(raw_ts):
            return CD_STATE_UNPARSEABLE, None
        if raw_ts <= 0:
            return CD_STATE_NO_RECORD, None
        return CD_STATE_READY, raw_ts

    raw_text = str(raw_last_at).strip()
    if raw_text.lower() in _CD_NO_RECORD_STRINGS:
        return CD_STATE_NO_RECORD, None
    try:
        raw_ts = float(raw_text)
        if not math.isfinite(raw_ts):
            return CD_STATE_UNPARSEABLE, None
        return CD_STATE_READY, raw_ts
    except (TypeError, ValueError):
        pass

    iso_text = raw_text[:-1] + "+00:00" if raw_text.endswith("Z") else raw_text
    try:
        dt = datetime.fromisoformat(iso_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_LOCAL)
        return CD_STATE_READY, dt.timestamp()
    except ValueError:
        pass

    for fmt in _CD_DATETIME_FORMATS:
        try:
            return CD_STATE_READY, datetime.strptime(raw_text, fmt).replace(tzinfo=TZ_LOCAL).timestamp()
        except ValueError:
            continue
    return CD_STATE_UNPARSEABLE, None


def cd_state(raw_last_at, now, window_sec):
    return cd_decision(raw_last_at, now, window_sec).state


def cd_decision(raw_last_at, now, window_sec):
    parse_state, last_at = _parse_cd_last_at(raw_last_at)
    now_ts = now.timestamp() if isinstance(now, datetime) else float(now)
    window = max(0.0, float(window_sec or 0))
    if parse_state != CD_STATE_READY:
        return CooldownDecision(
            state=parse_state,
            blocks=parse_state == CD_STATE_UNPARSEABLE,
            now=now_ts,
            window_sec=window,
            reason=parse_state,
        )
    if last_at > now_ts or now_ts - last_at < window:
        reason = "future_timestamp" if last_at > now_ts else "within_window"
        return CooldownDecision(
            state=CD_STATE_ON_CD,
            blocks=True,
            now=now_ts,
            window_sec=window,
            last_at=last_at,
            reason=reason,
        )
    return CooldownDecision(
        state=CD_STATE_READY,
        blocks=False,
        now=now_ts,
        window_sec=window,
        last_at=last_at,
        reason="off_cd",
    )


def cd_blocks(raw_last_at, now, window_sec):
    return cd_decision(raw_last_at, now, window_sec).blocks


def fmt_time_after(seconds):
    return (datetime.now(TZ_LOCAL) + timedelta(seconds=seconds)).strftime("%H:%M:%S")


def fmt_abs_ts(ts):
    if not ts or ts <= 0:
        return "未设置"
    return datetime.fromtimestamp(ts, TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")


def fmt_remaining(ts):
    if not ts or ts <= 0:
        return "未设置"
    remain = int(ts - time.time())
    if remain <= 0:
        return "已到时间"
    h, rem = divmod(remain, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}小时{m}分钟{s}秒后"
    if m > 0:
        return f"{m}分钟{s}秒后"
    return f"{s}秒后"


def fmt_slot_label(slot_start_at, slot_end_at):
    if not slot_start_at or slot_start_at <= 0 or not slot_end_at or slot_end_at <= 0:
        return "未设置"
    start_text = datetime.fromtimestamp(slot_start_at, TZ_LOCAL).strftime("%H:%M")
    end_text = datetime.fromtimestamp(slot_end_at, TZ_LOCAL).strftime("%H:%M")
    return f"{start_text}-{end_text}"


def calc_next_daily_window_time(start_hour_utc, end_hour_utc, now=None):
    if now is None:
        now = time.time()
    utc_now = datetime.fromtimestamp(now, timezone.utc)
    day_start = utc_now.replace(hour=start_hour_utc, minute=0, second=0, microsecond=0)
    day_end = utc_now.replace(hour=end_hour_utc, minute=0, second=0, microsecond=0)

    if utc_now >= day_end:
        day_start += timedelta(days=1)
        day_end += timedelta(days=1)

    start_ts = day_start.timestamp()
    end_ts = day_end.timestamp()
    min_ts = max(now + 1, start_ts)
    if min_ts >= end_ts:
        day_start += timedelta(days=1)
        day_end += timedelta(days=1)
        start_ts = day_start.timestamp()
        end_ts = day_end.timestamp()
        min_ts = start_ts

    return random.uniform(min_ts, end_ts)


def calc_next_daily_window_after_completion(start_hour_utc, end_hour_utc, now=None):
    if now is None:
        now = time.time()
    next_day_utc = datetime.fromtimestamp(now, timezone.utc) + timedelta(days=1)
    day_start = next_day_utc.replace(hour=start_hour_utc, minute=0, second=0, microsecond=0)
    day_end = next_day_utc.replace(hour=end_hour_utc, minute=0, second=0, microsecond=0)
    return random.uniform(day_start.timestamp(), day_end.timestamp())


def calc_next_checkin_time(now=None):
    start_hour_utc, end_hour_utc = get_module_window_hours("点卯")
    return calc_next_daily_window_time(start_hour_utc, end_hour_utc, now)


def calc_next_tower_time(now=None):
    start_hour_utc, end_hour_utc = get_module_window_hours("闯塔")
    return calc_next_daily_window_time(start_hour_utc, end_hour_utc, now)


def _get_local_day_key(now=None):
    if now is None:
        now = time.time()
    return datetime.fromtimestamp(now, TZ_LOCAL).strftime("%Y-%m-%d")


def get_checkin_day_key(now=None):
    return _get_local_day_key(now)


def reset_checkin_daily_state(now=None):
    state["checkin_teach_day"] = get_checkin_day_key(now)
    state["last_checkin_done_day"] = ""
    state["checkin_teach_count"] = 0
    state["next_sect_teach_time"] = 0
    state["sect_teach_reply_to_msg_id"] = 0
    state["last_checkin_msg_id"] = 0
    state["last_sect_teach_msg_id"] = 0
    state["checkin_cleanup_msg_ids"] = []


def schedule_next_checkin(now=None, persist=True):
    next_ts = calc_next_checkin_time(now)
    state["next_checkin_time"] = next_ts
    if persist:
        _save_if_available()
    return next_ts


def get_day_key(now=None):
    return _get_local_day_key(now)


def schedule_next_checkin_after_completion(now=None, persist=True):
    start_hour_utc, end_hour_utc = get_module_window_hours("点卯")
    next_ts = calc_next_daily_window_after_completion(start_hour_utc, end_hour_utc, now)
    state["next_checkin_time"] = next_ts
    if persist:
        _save_if_available()
    return next_ts


def schedule_next_tower(now=None, persist=True):
    next_ts = calc_next_tower_time(now)
    state["next_tower_time"] = next_ts
    if persist:
        _save_if_available()
    return next_ts


def schedule_next_tower_after_completion(now=None, persist=True):
    start_hour_utc, end_hour_utc = get_module_window_hours("闯塔")
    next_ts = calc_next_daily_window_after_completion(start_hour_utc, end_hour_utc, now)
    state["next_tower_time"] = next_ts
    if persist:
        _save_if_available()
    return next_ts


__all__ = [
    "CD_STATE_NO_RECORD",
    "CD_STATE_ON_CD",
    "CD_STATE_READY",
    "CD_STATE_UNPARSEABLE",
    "CooldownDecision",
    "calc_next_checkin_time",
    "calc_next_daily_window_after_completion",
    "calc_next_daily_window_time",
    "calc_next_tower_time",
    "cd_blocks",
    "cd_decision",
    "cd_state",
    "configure_timing",
    "fmt_abs_ts",
    "fmt_remaining",
    "fmt_slot_label",
    "fmt_time_after",
    "get_checkin_day_key",
    "get_day_key",
    "parse_wait_time",
    "reset_checkin_daily_state",
    "schedule_next_checkin",
    "schedule_next_checkin_after_completion",
    "schedule_next_tower",
    "schedule_next_tower_after_completion",
]
