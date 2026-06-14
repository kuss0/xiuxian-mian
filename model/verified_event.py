from dataclasses import dataclass


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class VerifiedGameEvent:
    event_type: str
    chat_id: int
    msg_id: int
    sender_id: int
    text: str
    reply_context: object
    identity_id: int
    family: str
    root_msg_id: int
    route_source: str
    reply_to_sender_id: int


def from_telegram_event(event, text, reply_context, event_kind="message", root_msg_id=0):
    context = reply_context if isinstance(reply_context, dict) else {}
    event_type = str(event_kind or "message").strip() or "message"
    family = str(context.get("family") or "").strip()
    resolved_root_msg_id = (
        _safe_int(root_msg_id)
        or _safe_int(context.get("root_msg_id"))
        or _safe_int(context.get("reply_to_msg_id"))
    )
    return VerifiedGameEvent(
        event_type=event_type,
        chat_id=_safe_int(getattr(event, "chat_id", 0)),
        msg_id=_safe_int(getattr(event, "id", 0)),
        sender_id=_safe_int(getattr(event, "sender_id", 0)),
        text=str(text or ""),
        reply_context=reply_context,
        identity_id=_safe_int(context.get("send_as_id")),
        family=family,
        root_msg_id=resolved_root_msg_id,
        route_source=f"{event_type}:reply_context",
        reply_to_sender_id=_safe_int(context.get("reply_to_sender_id")),
    )


__all__ = [
    "VerifiedGameEvent",
    "from_telegram_event",
]
