"""Chat-scoped message references within one identity's state.

Legacy integer keys are read conservatively until the next save/reload. A bare
message ID is usable only when exactly one stored reference matches it.
"""

from collections.abc import Mapping


def message_key(msg_id, chat_id=0):
    if isinstance(msg_id, tuple):
        recorded_chat, msg_id = msg_id
        if chat_id and int(chat_id) != int(recorded_chat):
            raise ValueError("conflicting message chat")
        chat_id = recorded_chat
    elif hasattr(msg_id, "id"):
        recorded_chat = int(getattr(msg_id, "chat_id", 0) or 0)
        if chat_id and recorded_chat and int(chat_id) != recorded_chat:
            raise ValueError("conflicting message chat")
        chat_id = recorded_chat or chat_id
        msg_id = msg_id.id
    msg_id = int(msg_id)
    if msg_id <= 0:
        raise ValueError("message ID must be positive")
    return int(chat_id or 0), msg_id


def message_key_parts(key, item=None):
    chat_id = int(item.get("chat_id") or 0) if isinstance(item, Mapping) else 0
    return message_key(key, chat_id)


def find_message_key(records, msg_id, *, chat_id=None):
    try:
        explicit_chat, target_id = message_key(msg_id, chat_id or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    scoped = chat_id is not None or isinstance(msg_id, tuple) or bool(explicit_chat)
    matches = []
    candidates = (message_key(target_id, explicit_chat), target_id) if scoped else records
    for key in candidates:
        if key not in records:
            continue
        item = records[key]
        try:
            recorded_chat, recorded_id = message_key_parts(key, item)
        except (TypeError, ValueError, OverflowError):
            continue
        if recorded_id == target_id and (not scoped or recorded_chat == explicit_chat):
            matches.append(key)
    return matches[0] if len(matches) == 1 else None


def get_message_record(records, msg_id, default=None, *, chat_id=None):
    key = find_message_key(records, msg_id, chat_id=chat_id)
    return records.get(key, default) if key is not None else default


def pop_message_record(records, msg_id, default=None, *, chat_id=None):
    key = find_message_key(records, msg_id, chat_id=chat_id)
    return records.pop(key, default) if key is not None else default
