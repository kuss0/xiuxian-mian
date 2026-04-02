import random
import time
from datetime import datetime, timedelta, timezone

from .config import RE_HOURS, RE_MINUTES, RE_SECONDS, TZ_LOCAL
from .state import get_module_window_hours, state

_save_state = None


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
    "calc_next_checkin_time",
    "calc_next_daily_window_after_completion",
    "calc_next_daily_window_time",
    "calc_next_tower_time",
    "configure_timing",
    "fmt_abs_ts",
    "fmt_remaining",
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
