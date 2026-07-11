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
_DUNGEON_QUIET_RELEASE_MARKERS = (
    "最终判定",
    "通关保底",
    "本场失败",
    "镇蛟未竟",
    "LDC 红包池",
    "本次未能带出",
    "副本已解散",
    "队伍已解散",
    "房间已解散",
)


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


def is_dungeon_quiet_release_notice(text):
    raw = str(text or "")
    if not raw:
        return False
    has_title = bool(_DUNGEON_NAME_RE.search(raw))
    if not has_title:
        return False
    if any(marker in raw for marker in _DUNGEON_QUIET_RELEASE_MARKERS):
        return True
    return (
        "最终状态" in raw
        and any(marker in raw for marker in ("全员获得", "每位队员获得", "判定分"))
    )


def get_dungeon_quiet_until():
    try:
        return float(state.get("dungeon_quiet_until", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def get_dungeon_quiet_reason():
    return str(state.get("dungeon_quiet_reason", "") or "")


def clear_dungeon_quiet(now=None):
    had_quiet = get_dungeon_quiet_until() > 0 or bool(get_dungeon_quiet_reason()) or bool(state.get("dungeon_quiet_last_log_at", 0))
    if not had_quiet:
        return False
    state["dungeon_quiet_until"] = 0
    state["dungeon_quiet_reason"] = ""
    state["dungeon_quiet_last_log_at"] = 0
    mark_dirty()
    return True


def clear_expired_dungeon_quiet(now=None):
    now = _now(now)
    until = get_dungeon_quiet_until()
    if until <= 0 or until > now:
        return False
    return clear_dungeon_quiet(now)


def is_dungeon_quiet_active(now=None):
    now = _now(now)
    if get_dungeon_quiet_until() > now:
        return True
    clear_expired_dungeon_quiet(now)
    return False


def observe_dungeon_quiet_text(text, now=None):
    now = _now(now)
    if is_dungeon_quiet_active(now) and is_dungeon_quiet_release_notice(text):
        reason = get_dungeon_quiet_reason()
        clear_dungeon_quiet(now)
        return {"changed": True, "cleared": True, "until": 0, "reason": reason}
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
    clear_expired_dungeon_quiet()
    until = get_dungeon_quiet_until()
    return fmt_abs_ts(until) if until > 0 else "未生效"


__all__ = [
    "DUNGEON_QUIET_MAX_SEC",
    "DUNGEON_QUIET_MIN_SEC",
    "clear_dungeon_quiet",
    "clear_expired_dungeon_quiet",
    "format_dungeon_quiet_until",
    "get_dungeon_quiet_reason",
    "get_dungeon_quiet_until",
    "is_dungeon_quiet_active",
    "is_dungeon_quiet_active_notice",
    "is_dungeon_quiet_prepare_notice",
    "is_dungeon_quiet_release_notice",
    "observe_dungeon_quiet_text",
    "should_log_dungeon_quiet_block",
]
