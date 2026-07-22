from dataclasses import dataclass, field
from math import isfinite
from typing import Awaitable, Callable


DEFAULT_DELAYED_ACTION_RETRY_SEC = 60
DEFAULT_DELAYED_ACTION_MAX_ATTEMPTS = 3
DELAYED_ACTIONS_STATE_KEY = "delayed_actions_state"


@dataclass
class DelayedAction:
    id: int
    command: str
    due_at: float
    send_as_id: int = 0
    track: bool = True
    reply_to_msg_id: int = 0
    priority: str = ""
    max_retry: int | None = None
    reply_timeout: float | None = None
    source_module: str = ""
    op_id: str = ""
    chain_id: str = ""
    delete_policy: str = ""
    dedupe_key: str = ""
    max_send_attempts: int = DEFAULT_DELAYED_ACTION_MAX_ATTEMPTS
    retry_delay_sec: float = DEFAULT_DELAYED_ACTION_RETRY_SEC
    attempts: int = 0
    created_at: float = 0
    updated_at: float = 0
    last_error: str = ""
    status: str = "pending"
    extra: dict = field(default_factory=dict)

    def snapshot(self):
        return {
            "id": self.id,
            "command": self.command,
            "due_at": self.due_at,
            "send_as_id": self.send_as_id,
            "track": self.track,
            "reply_to_msg_id": self.reply_to_msg_id,
            "priority": self.priority,
            "max_retry": self.max_retry,
            "reply_timeout": self.reply_timeout,
            "source_module": self.source_module,
            "op_id": self.op_id,
            "chain_id": self.chain_id,
            "delete_policy": self.delete_policy,
            "dedupe_key": self.dedupe_key,
            "max_send_attempts": self.max_send_attempts,
            "retry_delay_sec": self.retry_delay_sec,
            "attempts": self.attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
            "status": self.status,
            "extra": dict(self.extra or {}),
        }


DelayedSendFunc = Callable[..., Awaitable[object]]

_DELAYED_ACTIONS: dict[int, DelayedAction] = {}
_NEXT_DELAYED_ACTION_ID = 0
_RESTORABLE_STATUSES = {"pending"}


def _mark_dirty():
    try:
        from .persistence import mark_dirty
    except ModuleNotFoundError as exc:
        if exc.name == "telethon":
            return
        raise
    mark_dirty()


def reset_delayed_actions_for_tests():
    global _NEXT_DELAYED_ACTION_ID
    _DELAYED_ACTIONS.clear()
    _NEXT_DELAYED_ACTION_ID = 0


def _next_action_id():
    global _NEXT_DELAYED_ACTION_ID
    _NEXT_DELAYED_ACTION_ID += 1
    return _NEXT_DELAYED_ACTION_ID


def _clean_command(command):
    return str(command or "").strip()


def _optional_int(value):
    if value is None:
        return None
    return int(value)


def _non_negative_int(value, field_name):
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return parsed


def _bool_flag(value, default=True):
    if value is None:
        return bool(default)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "off", "no"}:
            return False
        if normalized in {"1", "true", "on", "yes"}:
            return True
    return bool(value)


def _finite_float(value, field_name):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be finite") from exc
    if not isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _optional_finite_float(value, field_name):
    if value is None:
        return None
    return _finite_float(value, field_name)


def _snapshot_finite_float(item, field_name, default=None):
    if field_name in item:
        value = item.get(field_name)
    else:
        value = default
    return _finite_float(value, field_name)


def _coerce_snapshot_action(item):
    if not isinstance(item, dict):
        return None
    action_id = int(item.get("id") or 0)
    if action_id <= 0:
        return None
    command = _clean_command(item.get("command"))
    if not command:
        return None

    send_as_id = int(item.get("send_as_id") or 0)
    status = str(item.get("status") or "pending")
    if status not in _RESTORABLE_STATUSES:
        return None
    last_error = str(item.get("last_error") or "")
    if send_as_id <= 0:
        return None

    extra = item.get("extra")
    if not isinstance(extra, dict):
        extra = {}

    return DelayedAction(
        id=action_id,
        command=command,
        due_at=_snapshot_finite_float(item, "due_at"),
        send_as_id=send_as_id,
        track=_bool_flag(item.get("track"), True),
        reply_to_msg_id=_non_negative_int(item.get("reply_to_msg_id", 0), "reply_to_msg_id"),
        priority=str(item.get("priority") or ""),
        max_retry=_optional_int(item.get("max_retry")),
        reply_timeout=_optional_finite_float(item.get("reply_timeout"), "reply_timeout"),
        source_module=str(item.get("source_module") or ""),
        op_id=str(item.get("op_id") or ""),
        chain_id=str(item.get("chain_id") or ""),
        delete_policy=str(item.get("delete_policy") or ""),
        dedupe_key=str(item.get("dedupe_key") or "").strip(),
        max_send_attempts=max(1, int(item.get("max_send_attempts") or DEFAULT_DELAYED_ACTION_MAX_ATTEMPTS)),
        retry_delay_sec=max(
            1.0,
            _snapshot_finite_float(item, "retry_delay_sec", DEFAULT_DELAYED_ACTION_RETRY_SEC),
        ),
        attempts=max(0, int(item.get("attempts") or 0)),
        created_at=_snapshot_finite_float(item, "created_at", 0),
        updated_at=_snapshot_finite_float(item, "updated_at", 0),
        last_error=last_error[:200],
        status=status,
        extra=dict(extra),
    )


def snapshot_delayed_actions():
    return {
        "next_id": _NEXT_DELAYED_ACTION_ID,
        "actions": [
            action.snapshot()
            for action in sorted(_DELAYED_ACTIONS.values(), key=lambda item: int(item.id or 0))
        ],
    }


def restore_delayed_actions(payload):
    global _NEXT_DELAYED_ACTION_ID
    if isinstance(payload, dict):
        raw_actions = payload.get("actions")
        raw_next_id = payload.get("next_id")
    elif isinstance(payload, list):
        raw_actions = payload
        raw_next_id = None
    else:
        raw_actions = []
        raw_next_id = None

    restored = {}
    max_restored_id = 0
    for item in raw_actions if isinstance(raw_actions, list) else []:
        try:
            action = _coerce_snapshot_action(item)
        except (TypeError, ValueError, OverflowError):
            action = None
        if action is None:
            continue
        restored[action.id] = action
        max_restored_id = max(max_restored_id, action.id)

    try:
        next_id = int(raw_next_id or 0)
    except (TypeError, ValueError, OverflowError):
        next_id = 0

    _DELAYED_ACTIONS.clear()
    _DELAYED_ACTIONS.update(restored)
    _NEXT_DELAYED_ACTION_ID = max(next_id, max_restored_id)
    return snapshot_delayed_actions()


def export_to_state(state_dict):
    snapshot = snapshot_delayed_actions()
    if isinstance(state_dict, dict):
        state_dict[DELAYED_ACTIONS_STATE_KEY] = snapshot
    return snapshot


def restore_from_state(state_dict):
    if not isinstance(state_dict, dict):
        return restore_delayed_actions({})
    payload = state_dict.get(DELAYED_ACTIONS_STATE_KEY)
    if not isinstance(payload, dict):
        payload = {}
    restored = restore_delayed_actions(payload)
    state_dict[DELAYED_ACTIONS_STATE_KEY] = restored
    return restored


def _find_pending_by_dedupe_key(dedupe_key):
    key = str(dedupe_key or "").strip()
    if not key:
        return None
    for action in _DELAYED_ACTIONS.values():
        if action.status == "pending" and action.dedupe_key == key:
            return action
    return None


def schedule_delayed_action(
    command,
    due_at,
    *,
    send_as_id=0,
    track=True,
    reply_to_msg_id=0,
    priority="",
    max_retry=None,
    reply_timeout=None,
    source_module="",
    op_id="",
    chain_id="",
    delete_policy="",
    dedupe_key="",
    max_send_attempts=DEFAULT_DELAYED_ACTION_MAX_ATTEMPTS,
    retry_delay_sec=DEFAULT_DELAYED_ACTION_RETRY_SEC,
    now=0,
    extra=None,
):
    clean_command = _clean_command(command)
    if not clean_command:
        raise ValueError("delayed action command is required")
    due_at = _finite_float(due_at, "due_at")
    now = _finite_float(now, "now")
    retry_delay_sec = max(
        1.0,
        _finite_float(
            DEFAULT_DELAYED_ACTION_RETRY_SEC if retry_delay_sec is None else retry_delay_sec,
            "retry_delay_sec",
        ),
    )
    reply_timeout = _optional_finite_float(reply_timeout, "reply_timeout")
    reply_to_msg_id = _non_negative_int(reply_to_msg_id, "reply_to_msg_id")
    max_send_attempts = max(1, int(max_send_attempts or DEFAULT_DELAYED_ACTION_MAX_ATTEMPTS))

    action = _find_pending_by_dedupe_key(dedupe_key)
    if action is None:
        action = DelayedAction(
            id=_next_action_id(),
            command=clean_command,
            due_at=due_at,
            created_at=now,
            updated_at=now,
        )
        _DELAYED_ACTIONS[action.id] = action

    action.command = clean_command
    action.due_at = due_at
    action.send_as_id = int(send_as_id or 0)
    action.track = bool(track)
    action.reply_to_msg_id = reply_to_msg_id
    action.priority = str(priority or "")
    action.max_retry = None if max_retry is None else int(max_retry)
    action.reply_timeout = reply_timeout
    action.source_module = str(source_module or "")
    action.op_id = str(op_id or "")
    action.chain_id = str(chain_id or "")
    action.delete_policy = str(delete_policy or "")
    action.dedupe_key = str(dedupe_key or "").strip()
    action.max_send_attempts = max_send_attempts
    action.retry_delay_sec = retry_delay_sec
    action.updated_at = now
    action.last_error = ""
    action.status = "pending"
    action.extra = dict(extra or {})
    _mark_dirty()
    return action.snapshot()


def cancel_delayed_action(action_id=0, *, dedupe_key=""):
    if action_id:
        action = _DELAYED_ACTIONS.pop(int(action_id), None)
        if action is None:
            return False
        _mark_dirty()
        return True
    action = _find_pending_by_dedupe_key(dedupe_key)
    if action is None:
        return False
    _DELAYED_ACTIONS.pop(action.id, None)
    _mark_dirty()
    return True


def list_delayed_actions(*, include_non_pending=False):
    actions = [
        action.snapshot()
        for action in _DELAYED_ACTIONS.values()
        if include_non_pending or action.status == "pending"
    ]
    return sorted(actions, key=lambda item: (_finite_float(item.get("due_at"), "due_at"), int(item.get("id") or 0)))


def _send_kwargs(action):
    kwargs = {"send_as_id": action.send_as_id, "track": bool(action.track)}
    if action.reply_to_msg_id > 0:
        kwargs["reply_to"] = action.reply_to_msg_id
    if action.priority:
        kwargs["priority"] = action.priority
    if action.max_retry is not None:
        kwargs["max_retry"] = action.max_retry
    if action.reply_timeout is not None:
        kwargs["reply_timeout"] = _finite_float(action.reply_timeout, "reply_timeout")
    if action.source_module:
        kwargs["source_module"] = action.source_module
    if action.op_id:
        kwargs["op_id"] = action.op_id
    if action.chain_id:
        kwargs["chain_id"] = action.chain_id
    if action.delete_policy:
        kwargs["delete_policy"] = action.delete_policy
    return kwargs


async def drain_due_actions(now, send_func: DelayedSendFunc, *, max_items=20):
    now = _finite_float(now, "now")
    max_items = max(1, int(max_items or 1))
    results = []
    changed = False
    due_actions = []
    for action in list(_DELAYED_ACTIONS.values()):
        if action.status != "pending":
            continue
        try:
            due_at = _finite_float(action.due_at, "due_at")
        except ValueError as exc:
            action.status = "failed"
            action.due_at = now
            action.updated_at = now
            action.last_error = str(exc)[:200]
            changed = True
            results.append(_result_payload(action, "failed", reason=action.last_error))
            _DELAYED_ACTIONS.pop(action.id, None)
            continue
        if due_at <= now:
            due_actions.append((due_at, action))
    due_actions.sort(key=lambda item: (item[0], int(item[1].id or 0)))

    for _due_at, action in due_actions[:max_items]:
        if action.send_as_id <= 0:
            action.status = "failed"
            action.updated_at = now
            action.last_error = "missing send_as_id"
            changed = True
            results.append(_result_payload(action, "failed", reason=action.last_error))
            _DELAYED_ACTIONS.pop(action.id, None)
            continue
        try:
            send_kwargs = _send_kwargs(action)
        except ValueError as exc:
            action.status = "failed"
            action.reply_timeout = None
            action.updated_at = now
            action.last_error = str(exc)[:200]
            changed = True
            results.append(_result_payload(action, "failed", reason=action.last_error))
            _DELAYED_ACTIONS.pop(action.id, None)
            continue
        action.attempts += 1
        action.status = "sending"
        action.updated_at = now
        try:
            sent = await send_func(action.command, **send_kwargs)
        except Exception as exc:
            sent = None
            action.last_error = str(exc)[:200]
        if sent is not None:
            _DELAYED_ACTIONS.pop(action.id, None)
            changed = True
            results.append(_result_payload(action, "sent", message_id=int(getattr(sent, "id", 0) or 0)))
            continue

        if not action.last_error:
            action.last_error = "send returned none"
        if action.attempts >= action.max_send_attempts:
            action.status = "failed"
            changed = True
            results.append(_result_payload(action, "failed"))
            _DELAYED_ACTIONS.pop(action.id, None)
            continue

        try:
            retry_delay_sec = max(1.0, _finite_float(action.retry_delay_sec, "retry_delay_sec"))
        except ValueError as exc:
            action.status = "failed"
            action.retry_delay_sec = DEFAULT_DELAYED_ACTION_RETRY_SEC
            action.updated_at = now
            action.last_error = str(exc)[:200]
            changed = True
            results.append(_result_payload(action, "failed", reason=action.last_error))
            _DELAYED_ACTIONS.pop(action.id, None)
            continue

        action.status = "pending"
        action.due_at = now + retry_delay_sec
        action.updated_at = now
        changed = True
        results.append(_result_payload(action, "rescheduled", due_at=action.due_at))

    if changed:
        _mark_dirty()

    return results


def _result_payload(action, status, **extra):
    payload = {
        "id": action.id,
        "status": status,
        "command": action.command,
        "send_as_id": action.send_as_id,
        "reply_to_msg_id": action.reply_to_msg_id,
        "source_module": action.source_module,
        "op_id": action.op_id,
        "chain_id": action.chain_id,
        "attempts": action.attempts,
        "extra": dict(action.extra or {}),
    }
    payload.update(extra)
    return payload


__all__ = [
    "DELAYED_ACTIONS_STATE_KEY",
    "DEFAULT_DELAYED_ACTION_MAX_ATTEMPTS",
    "DEFAULT_DELAYED_ACTION_RETRY_SEC",
    "DelayedAction",
    "cancel_delayed_action",
    "drain_due_actions",
    "export_to_state",
    "list_delayed_actions",
    "reset_delayed_actions_for_tests",
    "restore_delayed_actions",
    "restore_from_state",
    "schedule_delayed_action",
    "snapshot_delayed_actions",
]
