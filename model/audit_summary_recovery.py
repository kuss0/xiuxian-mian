"""Match an ambiguous summary to its own sender, destination, text and time."""
import hashlib

from telethon.extensions.html import parse


def delivery_proof(batch, messages, chat_id, sender_ids):
    if hashlib.sha256(batch["message"].encode("utf-8")).hexdigest() != batch["hash"]:
        return None
    text, _entities = parse(batch["message"])
    matches = []
    for message in messages:
        at = message.date.timestamp()
        if (message.chat_id == chat_id and message.sender_id in sender_ids
                and not getattr(message, "fwd_from", None)
                and batch["at"] - 5 <= at <= batch["at"] + 180
                and message.message == text and message.id > 0):
            matches.append(dict(message_id=message.id, sender_id=message.sender_id, chat_id=chat_id, at=at))
    return matches[0] if len(matches) == 1 else None
