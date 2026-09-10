"""Decode an existing bounded log batch; never fetch history or apply reducers."""

from .profile_observation import timestamp
from .verified_event import VerifiedGameEvent
from .yinluo_resource_facts import admit_yinluo_resource_source, parse_yinluo_resource_command


MAX_LOG_RECORDS = 8192
MAX_REPLY_DEPTH = 8


def owned_yinluo_log_events(entries, *, identity_accounts, game_chats, game_bots, now):
    """Return native command/result pairs, including empty or unknown edits.

    A `sent` bookkeeping row and its local ts_epoch are not original-command
    evidence. Every intermediate reply node must be an official bot's native
    message in the same chat, with strictly decreasing message IDs and native
    times that do not move forward while traversing towards the command.
    """
    if not isinstance(entries, (list, tuple)) or len(entries) > MAX_LOG_RECORDS:
        raise ValueError("Yinluo resource replay requires a bounded log batch")
    if not isinstance(game_chats, (list, tuple, set, frozenset)) or not isinstance(game_bots, (list, tuple, set, frozenset)):
        raise ValueError("Yinluo resource replay requires explicit trust sets")
    if any(type(chat) is not int or chat == 0 for chat in game_chats) or any(type(bot) is not int or bot <= 0 for bot in game_bots):
        raise ValueError("Invalid Yinluo resource replay trust sets")

    originals = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("event_type") != "message":
            continue
        chat_id, msg_id = entry.get("chat_id"), entry.get("message_id")
        if type(chat_id) is not int or chat_id not in game_chats or type(msg_id) is not int or msg_id <= 0:
            continue
        key = (chat_id, msg_id)
        original = {field: entry.get(field) for field in ("sender_id", "text", "server_event_at")}
        original["reply_to_msg_id"] = entry.get("reply_to_msg_id", 0)
        # Once two original deliveries disagree, another duplicate cannot
        # quietly repair that provenance conflict by winning arrival order.
        previous = originals.get(key)
        if key not in originals:
            originals[key] = original
        elif previous is None or (
            previous != original
            or any(type(previous[field]) is not type(original[field]) for field in ("sender_id", "text", "reply_to_msg_id"))
            or timestamp(previous["server_event_at"]) != timestamp(original["server_event_at"])
        ):
            originals[key] = None

    def original_command(entry):
        chat_id, child_id = entry.get("chat_id"), entry.get("message_id")
        child_at = timestamp(entry.get("server_event_at"))
        parent_id = entry.get("reply_to_msg_id")
        for _ in range(MAX_REPLY_DEPTH):
            if type(parent_id) is not int or not 0 < parent_id < child_id:
                return None
            parent = originals.get((chat_id, parent_id))
            if parent is None:
                return None
            parent_at = timestamp(parent.get("server_event_at"))
            sender = parent.get("sender_id")
            if type(sender) is not int or sender == 0 or not 0 < parent_at <= child_at:
                return None
            if sender not in game_bots:
                if parse_yinluo_resource_command(parent.get("text")) is not None:
                    return parent_id, parent
                return None
            child_id, child_at, parent_id = parent_id, parent_at, parent.get("reply_to_msg_id")
        return None

    result = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("event_type") not in {"message", "edit"}:
            continue
        chat_id, msg_id, sender = entry.get("chat_id"), entry.get("message_id"), entry.get("sender_id")
        if (
            type(chat_id) is not int or chat_id not in game_chats
            or type(msg_id) is not int or msg_id <= 0
            or type(sender) is not int or sender not in game_bots
            or not isinstance(entry.get("text"), str)
        ):
            continue
        original = original_command(entry)
        if original is None:
            continue
        root_id, command = original
        context = {
            "chat_id": chat_id, "reply_to_msg_id": root_id, "root_msg_id": root_id,
            "reply_to_command": command["text"], "reply_to_server_at": command["server_event_at"],
            "reply_to_sender_id": command["sender_id"], "reply_to_command_edited": False,
            "resource_direct_reply_id": entry.get("reply_to_msg_id"),
        }
        event = VerifiedGameEvent(
            event_type=entry["event_type"], chat_id=chat_id, msg_id=msg_id, sender_id=sender,
            text=entry["text"], reply_context=context, identity_id=0, family="",
            root_msg_id=root_id, route_source="yinluo_resource_log", reply_to_sender_id=command["sender_id"],
            server_event_at=entry.get("server_event_at"),
        )
        source = admit_yinluo_resource_source(
            event, identity_accounts=identity_accounts, game_chats=game_chats, game_bots=game_bots, now=now,
        )
        if source is not None:
            result.append((event, source))
    return result
