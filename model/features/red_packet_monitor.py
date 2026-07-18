import re
from collections import OrderedDict

from ..runtime import console_log


RED_PACKET_MONITOR_CHAT_USERNAME = "ja_netfilter_group"
RE_RED_PACKET_COMMAND = re.compile(
    r"^\s*\.发红包\s+(?P<amount>\d+(?:\.\d+)?)\s+(?P<count>\d+)\s*$"
)
_SEEN_CANDIDATES = OrderedDict()
_SEEN_CANDIDATE_LIMIT = 1000


def parse_red_packet_command(text):
    match = RE_RED_PACKET_COMMAND.match(str(text or ""))
    if not match:
        return None
    return {
        "amount": float(match.group("amount")),
        "count": int(match.group("count")),
    }


async def _event_chat_username(event):
    chat = getattr(event, "chat", None)
    username = str(getattr(chat, "username", "") or "").strip().lstrip("@").casefold()
    if username:
        return username
    try:
        chat = await event.get_chat()
    except Exception:
        return ""
    return str(getattr(chat, "username", "") or "").strip().lstrip("@").casefold()


def _claim_candidate(chat_id, message_id, event_type, text):
    key = (
        int(chat_id or 0),
        int(message_id or 0),
        str(event_type or "message"),
        str(text or ""),
    )
    if key in _SEEN_CANDIDATES:
        return False
    _SEEN_CANDIDATES[key] = None
    while len(_SEEN_CANDIDATES) > _SEEN_CANDIDATE_LIMIT:
        _SEEN_CANDIDATES.popitem(last=False)
    return True


async def observe_red_packet_candidate(event, *, event_type="message"):
    text = str(getattr(event, "raw_text", "") or "").strip()
    if "红包" not in text:
        return False
    if await _event_chat_username(event) != RED_PACKET_MONITOR_CHAT_USERNAME.casefold():
        return False
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    message_id = int(getattr(event, "id", 0) or 0)
    safe_text = re.sub(r"\s+", " ", text)[:500]
    if not _claim_candidate(chat_id, message_id, event_type, safe_text):
        return True
    parsed = parse_red_packet_command(text)
    parsed_text = (
        f"amount={parsed['amount']:g} count={parsed['count']}"
        if parsed
        else "unparsed"
    )
    console_log(
        f"🧧 红包候选观察｜type={event_type}｜chat={chat_id}｜msg={message_id}｜"
        f"sender={int(getattr(event, 'sender_id', 0) or 0)}｜"
        f"{parsed_text}｜text={safe_text}",
        scope="global",
        limit=720,
    )
    return True


__all__ = [
    "RED_PACKET_MONITOR_CHAT_USERNAME",
    "observe_red_packet_candidate",
    "parse_red_packet_command",
]
