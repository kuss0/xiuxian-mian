import random
import re
import time

from ..persistence import mark_dirty
from ..state import state
from ..timing import fmt_abs_ts


DUNGEON_QUIET_MIN_SEC = 5 * 60
DUNGEON_QUIET_MAX_SEC = 10 * 60
DUNGEON_QUIET_LOG_INTERVAL_SEC = 60

_DUNGEON_NAME_RE = re.compile(r"【([^】]+?)】")
_DUNGEON_CONTEXT_RE = re.compile(r"(?:为)?本次【([^】]+?)】(?:立下静场令|进行期间)")


def _now(now=None):
    return float(time.time() if now is None else now)


def _extract_reason(text):
    raw = str(text or "")
    match = _DUNGEON_CONTEXT_RE.search(raw)
    if match:
        title = match.group(1).split("·", 1)[0].strip()
        return f"{title}静场令" if title else "副本静场令"
    for match in _DUNGEON_NAME_RE.finditer(raw):
        title = match.group(1).split("·", 1)[0].strip()
        if title and title != "稳控全场":
            return f"{title}静场令"
    return "副本静场令"


def is_dungeon_quiet_prepare_notice(text):
    raw = str(text or "")
    return "你已祭出【稳控全场】" in raw and "立下静场令" in raw


def is_dungeon_quiet_active_notice(text):
    raw = str(text or "")
    if "【稳控全场】已展开" not in raw:
        return False
    if "天机阁将暂不响应本话题中的其他修仙指令" not in raw:
        return False
    return "副本结束前" in raw or "进行期间" in raw


def get_dungeon_quiet_until():
    try:
        return float(state.get("dungeon_quiet_until", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def get_dungeon_quiet_reason():
    return str(state.get("dungeon_quiet_reason", "") or "")


def is_dungeon_quiet_active(now=None):
    return get_dungeon_quiet_until() > _now(now)


def observe_dungeon_quiet_text(text, now=None):
    now = _now(now)
    if not is_dungeon_quiet_active_notice(text):
        return None
    if is_dungeon_quiet_active(now):
        return {
            "changed": False,
            "until": get_dungeon_quiet_until(),
            "reason": get_dungeon_quiet_reason(),
        }
    duration = random.randint(DUNGEON_QUIET_MIN_SEC, DUNGEON_QUIET_MAX_SEC)
    until = now + duration
    reason = _extract_reason(text)
    state["dungeon_quiet_until"] = until
    state["dungeon_quiet_reason"] = reason
    state["dungeon_quiet_last_log_at"] = 0
    mark_dirty()
    return {"changed": True, "until": until, "reason": reason}


def should_log_dungeon_quiet_block(now=None):
    now = _now(now)
    try:
        last_log_at = float(state.get("dungeon_quiet_last_log_at", 0) or 0)
    except (TypeError, ValueError):
        last_log_at = 0.0
    if now - last_log_at < DUNGEON_QUIET_LOG_INTERVAL_SEC:
        return False
    state["dungeon_quiet_last_log_at"] = now
    mark_dirty()
    return True


def format_dungeon_quiet_until():
    until = get_dungeon_quiet_until()
    return fmt_abs_ts(until) if until > 0 else "未生效"


__all__ = [
    "DUNGEON_QUIET_MAX_SEC",
    "DUNGEON_QUIET_MIN_SEC",
    "format_dungeon_quiet_until",
    "get_dungeon_quiet_reason",
    "get_dungeon_quiet_until",
    "is_dungeon_quiet_active",
    "is_dungeon_quiet_active_notice",
    "is_dungeon_quiet_prepare_notice",
    "observe_dungeon_quiet_text",
    "should_log_dungeon_quiet_block",
]
