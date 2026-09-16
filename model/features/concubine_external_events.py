"""External partner observations invalidate cached readiness, never settle commands."""

import copy
import re

from ..state import get_game_group_ids, get_identity_ids
from ..verified_event import telegram_event_timestamp
from . import concubine as c


STATE_KEY = "concubine_external_observation"
RETRY_SEC = 60
TAG = r"[A-Za-z0-9_]{1,32}"
NAME = r"[^\r\n\u3011]{1,120}"
NUMBER = r"(?:[0-9]{1,19}|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,6})"
RE_TAG = re.compile(r"(?<![A-Za-z0-9_@])@(" + TAG + r")(?![A-Za-z0-9_])")
RE_CONTRACT = re.compile(
    r"\u9053\u53cb\s+@(?P<owner>" + TAG + r")\s+\u5df2\u4ee5 LDC \u5951\u7ea6\u8bf7\u5f97\s*\u3010\u5357\u5bab\u5a49\u3011\s*\u76f8\u968f[\u3002.!\uff01]"
)
RE_INITIAL = re.compile(r"\u521d\u59cb\u60c5\u7f18[\uff1a:]\s*(?P<amount>" + NUMBER + r")(?=[\uff0c,\u3002.!\uff01\s]|$)")
RE_GAIN = re.compile(r"\u4f8d\u59be\u3010(?P<name>" + NAME + r")\u3011[^\r\n]*?\u60c5\u7f18\u589e\u52a0\u4e86\s*(?P<amount>" + NUMBER + r")\s*\u70b9")
RE_LOSS = re.compile(r"\u4f8d\u59be\u3010(?P<name>" + NAME + r")\u3011(?:\u88ab\u5357\u9647\u4faf\u63b3\u8d70|\u63b3\u8d70|\u4e0e\u5357\u9647\u4faf\u4ea4\u6362)")


def _name(value):
    return isinstance(value, str) and 0 < len(value) <= 120 and value == value.strip() and all(ord(char) >= 32 for char in value)


def _zero_time(value):
    return type(value) in {int, float} and (value == 0 or c._query_time(value) is not None)


def parse(text):
    if not isinstance(text, str) or len(text) > 4096:
        return None
    text = re.sub(r"[*`]+", "", text).replace("\r\n", "\n").strip()
    if not text or c._is_phaseful_summary_text(text) or c._is_heavenly_ban_text(text) or c._parse_gift_success(text):
        return None
    kinds = {
        "contract": "\u6708\u6bbf\u56e0\u679c" in text or "\u5357\u5bab\u5a49\u5165\u4e16" in text,
        "selfless": "\u3010\u65e0\u6211\u4e4b\u5883\u3011" in text,
        "gain": "\u60c5\u7f18\u589e\u52a0\u4e86" in text,
        "loss": "\u5357\u9647\u4faf" in text and ("\u63b3\u8d70" in text or "\u9009\u62e9\u5c06\u4f8d\u59be" in text),
    }
    if sum(kinds.values()) != 1:
        return None
    kind = next(key for key, present in kinds.items() if present)
    tags = {tag.casefold() for tag in RE_TAG.findall(text)}
    owner = next(iter(tags)) if len(tags) == 1 else ""
    amount = 0
    if kind == "contract":
        contracts, initials = list(RE_CONTRACT.finditer(text)), list(RE_INITIAL.finditer(text))
        if (len(contracts) != 1 or len(initials) != 1 or text.count("\u521d\u59cb\u60c5\u7f18") != 1
                or len(re.findall(r"\u3010\u6708\u6bbf\u56e0\u679c\s*\u00b7\s*\u5357\u5bab\u5a49\u5165\u4e16\u3011", text)) != 1
                or "\u5357\u5bab\u5a49\u4e0d\u4f1a\u88ab\u5357\u9647\u4faf\u593a\u8d70" not in text or "\u4e5f\u4e0d\u4f1a\u88ab\u6d1e\u5e9c\u8bbf\u5ba2\u62d0\u8d70" not in text):
            return None
        owner, name = contracts[0]["owner"].casefold(), "\u5357\u5bab\u5a49"
        amount = int(initials[0]["amount"].replace(",", ""))
    elif kind == "selfless":
        matches = list(c.RE_SELFLESS_PARTNER.finditer(text))
        if (len(matches) != 1 or text.count("\u3010\u65e0\u6211\u4e4b\u5883\u3011") != 1
                or text.count("\u8017\u5c3d\u4e0e\u4f60\u7684\u6240\u6709\u60c5\u7f18") != 1 or text.count("\u6321\u4e0b\u6b64\u52ab") != 1):
            return None
        name = matches[0]["name"]
    else:
        matches = list((RE_GAIN if kind == "gain" else RE_LOSS).finditer(text))
        if len(matches) != 1 or (kind == "gain" and text.count("\u60c5\u7f18\u589e\u52a0\u4e86") != 1):
            return None
        name = matches[0]["name"]
        if kind == "gain":
            amount = int(matches[0]["amount"].replace(",", ""))
    if not _name(name) or not 0 <= amount < 2 ** 63 or (kind == "gain" and amount == 0):
        return None
    if kind != "contract" and len(tags) > 1:
        return None
    return {"kind": kind, "owner": owner, "partner": name, "amount": amount, "text": text}


def record():
    value = c.state.get(STATE_KEY, {})
    if not isinstance(value, dict):
        return None
    if not value:
        return {}
    if (value.keys() != {"identity_id", "account_id", "event", "status", "retry_at", "resolved_at"}
            or c._query_int(value["identity_id"]) != c.get_current_identity_id()
            or c._query_int(value["account_id"]) <= 0
            or value["status"] not in ("pending", "complete")
            or not _zero_time(value["retry_at"]) or not _zero_time(value["resolved_at"])):
        return None
    event = value["event"]
    if (not isinstance(event, dict)
            or event.keys() != {"msg_id", "chat_id", "sender_id", "at", "event_type", "facts", "binding", "root_msg_id"}
            or c._query_int(event["msg_id"]) <= 0 or not c._query_int(event["chat_id"])
            or c._query_int(event["sender_id"]) <= 0 or c._query_time(event["at"]) is None
            or event["event_type"] not in ("message", "edit") or event["binding"] not in ("mention", "reply")
            or type(event["root_msg_id"]) is not int or not 0 <= event["root_msg_id"] < event["msg_id"]
            or (event["binding"] == "reply" and event["root_msg_id"] <= 0)
            or not isinstance(event["facts"], dict) or parse(event["facts"].get("text")) != event["facts"]
            or type(event["facts"].get("amount")) is not int
            or (event["binding"] == "mention") != bool(event["facts"].get("owner"))
            or (value["status"] == "pending" and value["resolved_at"] != 0)
            or (value["status"] == "complete" and value["resolved_at"] <= event["at"])):
        return None
    return copy.deepcopy(value)


def invalid():
    value = record()
    return value is None or bool(value and value["account_id"] != c.get_identity_account(c.get_current_identity_id()))


def needs_calibration():
    value = record()
    return invalid() or bool(value and value["status"] == "pending")


def _unique_owner(username):
    owners = []
    for identity_id in get_identity_ids():
        profile = c.get_send_as_profile(identity_id)
        aliases = profile.get("username_aliases") or []
        if not isinstance(aliases, list):
            continue
        names = [profile.get("username"), *aliases]
        if any(isinstance(name, str) and name.strip().lstrip("@").casefold() == username for name in names):
            owners.append(identity_id)
    return owners[0] if len(owners) == 1 else None


def _qualify(facts, event, now, event_type, reply_to, context, owner):
    if (not isinstance(context, dict) or event_type not in ("message", "edit") or c._query_time(now) is None
            or not c._owns_status_query(owner) or owner[2] <= 0):
        return None
    at = c._query_time(telegram_event_timestamp(event, event_type))
    chat, msg, sender = (c._query_int(getattr(event, field, 0)) for field in ("chat_id", "id", "sender_id"))
    if (at is None or at > now or chat not in get_game_group_ids() or msg <= 0 or sender not in c.get_game_bot_ids()
            or getattr(event, "fwd_from", None) or getattr(getattr(event, "message", None), "fwd_from", None)):
        return None
    if any(key in context and c._query_int(context[key]) != expected for key, expected in (
        ("send_as_id", owner[0]), ("account_id", owner[2]), ("chat_id", chat), ("sender_id", sender),
    )):
        return None
    if (("event_type" in context and context["event_type"] != event_type)
            or ("server_event_at" in context and c._query_time(context["server_event_at"]) != at)):
        return None
    root = c._query_int(getattr(reply_to, "id", 0))
    parent_sender = c._query_int(getattr(reply_to, "sender_id", 0))
    if reply_to is not None and (not 0 < root < msg or c._query_int(getattr(reply_to, "chat_id", 0)) != chat):
        return None
    native_root = getattr(event, "reply_to_msg_id", getattr(getattr(event, "message", None), "reply_to_msg_id", None))
    if native_root is not None:
        if (type(native_root) is not int or not 0 <= native_root < msg
                or (reply_to is not None and native_root != root)):
            return None
        root = native_root
    if "reply_to_sender_id" in context and c._query_int(context["reply_to_sender_id"]) != parent_sender:
        return None
    if any(key in context and c._query_int(context[key]) != root for key in ("root_msg_id", "reply_to_msg_id")):
        return None
    if facts["owner"]:
        if _unique_owner(facts["owner"]) != owner[0]:
            return None
        binding = "mention"
    else:
        if (facts["kind"] in {"contract", "loss"} or c._query_int(context.get("send_as_id")) != owner[0]
                or root <= 0 or root >= msg or c._query_int(getattr(reply_to, "chat_id", 0)) != chat
                or not parent_sender or not c.sender_matches_identity(parent_sender, owner[0])
                or context.get("reply_to_command_edited", False) is not False):
            return None
        binding = "reply"
    if facts["kind"] != "contract":
        current = c.state.get("concubine_name")
        previous = record()
        if previous and previous["status"] == "pending":
            current = previous["event"]["facts"]["partner"]
        if current and current != facts["partner"]:
            return None
        if facts["kind"] == "loss" and c._is_permanent_moon_partner(current):
            return None
    return {"msg_id": msg, "chat_id": chat, "sender_id": sender, "at": at,
            "event_type": event_type, "facts": facts, "binding": binding, "root_msg_id": root}


async def observe(text, now, event, *, loss=False, event_type="message", reply_to=None, reply_context=None):
    facts, owner, previous = parse(text), c._status_query_owner(), record()
    if facts is None or (facts["kind"] == "loss") is not loss or invalid():
        return False
    context = {} if reply_context is None else reply_context
    evidence = _qualify(facts, event, now, event_type, reply_to, context, owner)
    snapshot_at = c.state.get("concubine_last_snapshot_at", 0)
    if evidence is None or not _zero_time(snapshot_at) or evidence["at"] < snapshot_at:
        return False
    if previous:
        # A later absolute panel covers older events. Pending events coalesce
        # behind a timestamp barrier; no delta is discarded or applied twice.
        if (evidence["at"] <= previous["event"]["at"]
                or (previous["status"] == "complete" and evidence["at"] < previous["resolved_at"])):
            return False
    before = copy.deepcopy(owner[1])
    c.state[STATE_KEY] = {
        "identity_id": owner[0], "account_id": owner[2], "event": evidence,
        "status": "pending", "retry_at": previous.get("retry_at", 0) if previous else 0, "resolved_at": 0,
    }
    c.mark_dirty()
    if not c._save_query_projection(owner, before):
        return False
    if not previous or previous["status"] != "pending":
        try:
            await c.send_audit_log("\u4f8d\u59be\u5916\u90e8\u53d8\u52a8\u5df2\u8bb0\u5f55\uff0c\u7b49\u5f85\u72b6\u6001\u6821\u51c6\u3002", scope="identity", limit=160)
        except Exception as exc:
            c.console_log(f"Concubine observation saved; notification failed ({type(exc).__name__})")
    return True


def can_apply_snapshot(at, *, authoritative, panel=None):
    value = record()
    if invalid() or c._query_time(at) is None:
        return False
    if not value or value["status"] != "pending":
        return True
    if not authoritative or at <= value["event"]["at"]:
        return False
    if panel is None:
        return True
    if panel.get("has_partner") is True:
        # Coalescing may cover several affinity changes, even after a loss.
        return type(panel.get("affinity")) is int and 0 <= panel["affinity"] < 2 ** 63
    return (panel.get("has_partner") is False and not panel.get("manual_repair")
            and not c._is_permanent_moon_partner()
            and not c._is_permanent_moon_partner(value["event"]["facts"]["partner"]))


def snapshot_applied(at):
    value = record()
    if value and value["status"] == "pending" and can_apply_snapshot(at, authoritative=True):
        c.state[STATE_KEY] = dict(value, status="complete", resolved_at=at)
        if c._is_permanent_moon_partner():
            c.state["concubine_auto_reacquire"] = False
        c.mark_dirty()


def next_at():
    value = record()
    if invalid() or not value or value["status"] != "pending":
        return None
    query = c._status_query_record()
    replay_at = query.get("replay_after", 0) if query and query["status"] in c.CONCUBINE_QUERY_UNRESOLVED else 0
    return max(value["event"]["at"], value["retry_at"], replay_at)


async def recover(now):
    if not needs_calibration():
        return False
    owner, value = c._status_query_owner(), record()
    if (invalid() or not value or c._query_time(now) is None
            or not c._owns_status_query(owner, sending=True)
            or now <= value["event"]["at"] or now < value["retry_at"]):
        return True
    before = copy.deepcopy(owner[1])
    c.state[STATE_KEY] = dict(value, retry_at=now + RETRY_SEC)
    c.mark_dirty()
    if c._save_query_projection(owner, before):
        await c._send_status_command(now)
    return True
