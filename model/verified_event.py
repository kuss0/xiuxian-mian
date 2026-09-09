from dataclasses import dataclass
from datetime import datetime, timezone
import math

DELIVERY_NEW = "New"
DELIVERY_EDITED = "Edited"


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def clean_event_type(value):
    event_type = str(value or "message").strip().lower() or "message"
    if event_type in {"edit", "edited", "message_edited"}:
        return "edit"
    return "message"


def delivery_kind_for_event_type(value):
    return DELIVERY_EDITED if clean_event_type(value) == "edit" else DELIVERY_NEW


def is_new_delivery(value):
    return delivery_kind_for_event_type(value) == DELIVERY_NEW


def is_edited_delivery(value):
    return delivery_kind_for_event_type(value) == DELIVERY_EDITED


def telegram_event_timestamp(event, event_type="message"):
    """Return server evidence time, never a local receipt-time fallback."""
    if hasattr(event, "server_event_at"):
        value = getattr(event, "server_event_at")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        try:
            return float(value) if math.isfinite(value) and value > 0 else 0.0
        except (ValueError, OverflowError):
            return 0.0
    field = "edit_date" if clean_event_type(event_type) == "edit" else "date"
    value = getattr(event, field, None)
    if value is None:
        value = getattr(getattr(event, "message", None), field, None)
    if not isinstance(value, datetime):
        return 0.0
    try:
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        stamp = value.timestamp()
        return stamp if math.isfinite(stamp) and stamp > 0 else 0.0
    except (ValueError, OverflowError, OSError):
        return 0.0


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
    server_event_at: float = 0.0

    @property
    def delivery_kind(self):
        return delivery_kind_for_event_type(self.event_type)

    @property
    def is_new_delivery(self):
        return self.delivery_kind == DELIVERY_NEW

    @property
    def is_edited_delivery(self):
        return self.delivery_kind == DELIVERY_EDITED


def from_telegram_event(event, text, reply_context, event_kind="message", root_msg_id=0):
    context = reply_context if isinstance(reply_context, dict) else {}
    event_type = clean_event_type(event_kind)
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
        server_event_at=telegram_event_timestamp(event, event_type),
    )


__all__ = [
    "DELIVERY_EDITED",
    "DELIVERY_NEW",
    "VerifiedGameEvent",
    "clean_event_type",
    "delivery_kind_for_event_type",
    "from_telegram_event",
    "is_edited_delivery",
    "is_new_delivery",
    "telegram_event_timestamp",
]
