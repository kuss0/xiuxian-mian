import random

from ..state import state


RESOURCE_SHORTAGE_BACKOFF_STEPS_SEC = (
    2 * 3600,
    3 * 3600,
    5 * 3600,
    8 * 3600,
)
RESOURCE_SHORTAGE_JITTER_MIN_SEC = 3 * 60
RESOURCE_SHORTAGE_JITTER_MAX_SEC = 15 * 60
RESOURCE_SHORTAGE_KEYWORDS = (
    "修为不足",
    "灵力不足",
    "灵石不足",
    "贡献不足",
    "资源不足",
    "材料不足",
    "养魂木不足",
)


def is_resource_shortage_text(text, *, extra_keywords=()):
    raw_text = str(text or "")
    keywords = RESOURCE_SHORTAGE_KEYWORDS + tuple(extra_keywords or ())
    return any(keyword in raw_text for keyword in keywords)


def _get_backoff_store():
    store = state.get("resource_shortage_backoffs")
    if not isinstance(store, dict):
        store = {}
        state["resource_shortage_backoffs"] = store
    return store


def record_resource_shortage(action_key, now, *, reason="", jitter=True):
    store = _get_backoff_store()
    key = str(action_key or "").strip()
    if not key:
        key = "unknown"

    previous = store.get(key) if isinstance(store.get(key), dict) else {}
    count = int(previous.get("count", 0) or 0) + 1
    step_index = min(count - 1, len(RESOURCE_SHORTAGE_BACKOFF_STEPS_SEC) - 1)
    base_delay = float(RESOURCE_SHORTAGE_BACKOFF_STEPS_SEC[step_index])
    jitter_delay = random.uniform(RESOURCE_SHORTAGE_JITTER_MIN_SEC, RESOURCE_SHORTAGE_JITTER_MAX_SEC) if jitter else 0.0
    delay = base_delay + jitter_delay
    due_at = float(now) + delay

    store[key] = {
        "count": count,
        "step_index": step_index,
        "base_delay": base_delay,
        "delay": delay,
        "next_at": due_at,
        "last_at": float(now),
        "reason": str(reason or "")[:160],
    }
    return store[key]


def reset_resource_shortage(action_key):
    store = _get_backoff_store()
    key = str(action_key or "").strip()
    if not key or key not in store:
        return False
    store.pop(key, None)
    return True
