"""Native external-commission evidence from a bounded local log batch."""

from dataclasses import dataclass, replace
import re

from .message_log_recovery import sender_matches_identity
from .profile_observation import timestamp
from .resource_accounting import compare_points


MAX_RECORDS = 8192
MAX_REPLY_DEPTH = 8
MAX_NATIVE_ID = 2 ** 63
_COMMAND = re.compile(r"\.(发布解咒委托|接取解咒委托|取消解咒委托|剥离咒源)(?:\s+([^\s]+))?")
_ACTIONS = {"发布解咒委托": "publish", "接取解咒委托": "accept", "取消解咒委托": "cancel", "剥离咒源": "strip"}


def _command(text):
    if not isinstance(text, str) or len(text) > 256:
        return None
    match = _COMMAND.fullmatch(text.strip())
    if not match:
        return None
    action, argument = _ACTIONS[match[1]], match[2]
    if action in {"publish", "accept"}:
        if not argument or not re.fullmatch(r"[0-9]{1,19}", argument) or not 0 < int(argument) < 2 ** 63:
            return None
        argument = int(argument)
    elif action == "strip":
        if not argument or not re.fullmatch(r"@[A-Za-z0-9_]{1,32}", argument):
            return None
        argument = argument[1:].casefold()
    elif argument is not None:
        return None
    return action, argument


def _point(at, chat, msg_id, *, edited=False):
    return {"at": at, "evidence": {"source": "telegram", "chat_id": chat, "msg_id": msg_id, "edited": edited}}


@dataclass(frozen=True)
class NativeCommand:
    action: str
    argument: object
    command_sender: int
    chat_id: int
    root_msg_id: int
    start: dict


@dataclass(frozen=True)
class NativeReply(NativeCommand):
    result_msg_id: int
    end: dict
    revision: dict
    parsed: dict | None
    text: str = ""
    bot_sender: int = 0


@dataclass(frozen=True)
class CommissionEvidence:
    publication: NativeReply
    acceptance: NativeReply
    completion: NativeReply | None


def _parsed_reply(native, family, parse_reply):
    try:
        parsed = parse_reply(native["text"], now=native["server_event_at"], family=family)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _fact_key(parsed, owner_aliases):
    if not parsed:
        return None
    kind = parsed.get("type")
    if kind == "commission_published":
        return kind, parsed.get("commission_id")
    if kind not in {"commission_accepted", "assist_strip_success", "assist_strip_failed"}:
        return None
    target = str(parsed.get("target_username") or "").removeprefix("@").casefold()
    if not target:
        return None
    return kind, ("owner",) if target in owner_aliases else ("username", target)


def _native_history(entries, *, game_chats, game_bots, now, parse_reply, owner_aliases,
                    command_parser=None, action_families=None):
    """Retain unknown latest edits; never recover the older successful wording."""
    if not isinstance(entries, (list, tuple)) or len(entries) > MAX_RECORDS or timestamp(now) <= 0 or not callable(parse_reply):
        return None
    if (not isinstance(game_chats, (list, tuple, set, frozenset))
            or not isinstance(game_bots, (list, tuple, set, frozenset))
            or any(type(item) is not int or not 0 < abs(item) < MAX_NATIVE_ID for item in game_chats)
            or any(type(item) is not int or not 0 < item < MAX_NATIVE_ID for item in game_bots)):
        return None
    command_parser = command_parser or _command
    action_families = action_families or {
        "publish": "wanxin_commission", "accept": "wanxin_accept", "cancel": "wanxin_cancel", "strip": "wanxin_assist_strip",
    }
    originals, revisions = {}, {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("event_type") not in ("message", "edit"):
            continue
        chat, msg_id = entry.get("chat_id"), entry.get("message_id")
        if type(chat) is not int or chat not in game_chats or type(msg_id) is not int or not 0 < msg_id < MAX_NATIVE_ID:
            continue
        key = chat, msg_id
        native = {field: entry.get(field) for field in ("sender_id", "server_event_at", "text")}
        native["reply_to_msg_id"] = entry.get("reply_to_msg_id", 0)
        if entry["event_type"] == "message":
            if key not in originals:
                originals[key] = native
            elif originals[key] is None or originals[key] != native or any(
                type(originals[key][field]) is not type(native[field])
                for field in ("sender_id", "text", "reply_to_msg_id")
            ):
                originals[key] = None
        revisions.setdefault(key, []).append({**native, "edited": entry["event_type"] == "edit"})

    invalid_nodes = set()
    for key, items in revisions.items():
        original = originals.get(key)
        if (key in originals and original is None) or any(
            type(item["sender_id"]) is not int or not 0 < abs(item["sender_id"]) < MAX_NATIVE_ID
            or item["sender_id"] != items[0]["sender_id"]
            or type(item["reply_to_msg_id"]) is not int or not 0 <= item["reply_to_msg_id"] < MAX_NATIVE_ID
            or item["reply_to_msg_id"] != items[0]["reply_to_msg_id"]
            or not 0 < timestamp(item["server_event_at"]) <= now + 1
            or not isinstance(item["text"], str) or len(item["text"]) > 32000
            or (original is not None and timestamp(item["server_event_at"]) < timestamp(original["server_event_at"]))
            for item in items
        ):
            invalid_nodes.add(key)

    commands, uncertain_commands = {}, []
    for (chat, msg_id), native in originals.items():
        if (chat, msg_id) in invalid_nodes or native is None or native["sender_id"] in game_bots:
            continue
        command = command_parser(native["text"])
        if command is not None:
            commands[chat, msg_id] = NativeCommand(
                *command, native["sender_id"], chat, msg_id, _point(native["server_event_at"], chat, msg_id),
            )
    # Conflicted commands are not reply roots, but cannot be treated as absent.
    for chat, msg_id in invalid_nodes:
        for native in revisions[chat, msg_id]:
            command = command_parser(native["text"])
            if (command is not None and not native["edited"] and type(native["sender_id"]) is int
                    and 0 < abs(native["sender_id"]) < MAX_NATIVE_ID and native["sender_id"] not in game_bots
                    and 0 < timestamp(native["server_event_at"]) <= now + 1):
                uncertain_commands.append(NativeCommand(
                    *command, native["sender_id"], chat, msg_id, _point(native["server_event_at"], chat, msg_id),
                ))

    def root_for(chat, msg_id, native):
        child_id, child_at, parent_id = msg_id, timestamp(native["server_event_at"]), native["reply_to_msg_id"]
        for _ in range(MAX_REPLY_DEPTH):
            if type(parent_id) is not int or not 0 < parent_id < child_id:
                return None
            parent = originals.get((chat, parent_id))
            if (chat, parent_id) in invalid_nodes or parent is None or not 0 < timestamp(parent["server_event_at"]) <= child_at:
                return None
            sender = parent["sender_id"]
            if type(sender) is not int or sender == 0:
                return None
            if sender not in game_bots:
                return commands.get((chat, parent_id))
            child_id, child_at, parent_id = parent_id, timestamp(parent["server_event_at"]), parent["reply_to_msg_id"]
        return None

    by_command, invalid_roots = {}, set()
    for (chat, msg_id), items in revisions.items():
        if not any(item["sender_id"] in game_bots for item in items if type(item["sender_id"]) is int):
            continue
        if (chat, msg_id) in invalid_nodes:
            # A corrupt later reply must not expose an earlier sibling success.
            for item in items:
                root = root_for(chat, msg_id, item)
                if root is not None:
                    invalid_roots.add((chat, root.root_msg_id))
            continue
        latest_key = max((item["server_event_at"], item["edited"]) for item in items)
        latest = [item for item in items if (item["server_event_at"], item["edited"]) == latest_key]
        native = latest[0]
        root = root_for(chat, msg_id, native)
        if root is None:
            continue
        family = action_families[root.action]
        parsed = _parsed_reply(native, family, parse_reply) if all(item == native for item in latest) else None
        revision = _point(native["server_event_at"], chat, msg_id, edited=native["edited"])
        end = revision
        fact = _fact_key(parsed, owner_aliases)
        if fact is not None:
            versions = [
                ((item["server_event_at"], item["edited"]), _fact_key(_parsed_reply(item, family, parse_reply), owner_aliases))
                for item in items
            ]
            last_change = max((key for key, previous_fact in versions if previous_fact != fact), default=None)
            confirmations = [key for key, previous_fact in versions
                             if previous_fact == fact and (last_change is None or key > last_change)]
            # Cosmetic edits keep the first confirmation, not a new 24h clock.
            if confirmations:
                first_at, first_edited = min(confirmations)
                end = _point(first_at, chat, msg_id, edited=first_edited)
        reply = NativeReply(
            root.action, root.argument, root.command_sender, chat, root.root_msg_id, root.start, msg_id,
            end, revision, parsed, native["text"], native["sender_id"],
        )
        key = chat, root.root_msg_id
        previous = by_command.get(key, [])
        if not previous or reply.revision["at"] > previous[0].revision["at"]:
            by_command[key] = [reply]
        elif reply.revision["at"] == previous[0].revision["at"]:
            previous.append(reply)
    result = []
    for key, items in by_command.items():
        latest = max(items, key=lambda item: item.result_msg_id)
        if key in invalid_roots or (len(items) > 1 and any(item.revision["evidence"]["edited"] for item in items)):
            latest = replace(latest, parsed=None)
        result.append(latest)
    return result, [*commands.values(), *uncertain_commands]


def find_native_action_replies(entries, *, game_chats, game_bots, now, parse_reply, command_parser, action_families):
    """Share native ancestry/revision validation with pending-command recovery."""
    history = _native_history(
        entries, game_chats=game_chats, game_bots=game_bots, now=now, parse_reply=parse_reply,
        owner_aliases=set(), command_parser=command_parser, action_families=action_families,
    )
    return history[0] if history is not None else []


def find_commission_evidence(entries, *, owner_id, helper_id, commission, identity_usernames,
                             game_chats, game_bots, now, parse_reply):
    """Publication ID and native command senders bind an external result.

    Usernames confirm the target only; neither display names, log delivery
    timestamps, nor local 'claimed' flags establish the commission's lifecycle.
    """
    if (type(owner_id) is not int or not 0 < owner_id < MAX_NATIVE_ID
            or type(helper_id) is not int or not 0 < helper_id < MAX_NATIVE_ID
            or not isinstance(commission, dict) or type(commission.get("id")) is not int or not 0 < commission["id"] < MAX_NATIVE_ID
            or type(commission.get("publish_msg_id", 0)) is not int or not 0 <= commission.get("publish_msg_id", 0) < MAX_NATIVE_ID
            or not isinstance(identity_usernames, dict)
            or any(type(identity) is not int or identity <= 0 or not isinstance(names, (set, frozenset))
                   or any(not isinstance(name, str) for name in names)
                   for identity, names in identity_usernames.items())):
        return None
    aliases = {name for name in identity_usernames.get(owner_id, set())
               if {identity for identity, names in identity_usernames.items() if name in names} == {owner_id}}

    def target_matches(name):
        if not isinstance(name, str):
            return False
        name = name.removeprefix("@").casefold()
        return name in aliases

    history = _native_history(entries, game_chats=game_chats, game_bots=game_bots, now=now,
                              parse_reply=parse_reply, owner_aliases=aliases)
    if history is None:
        return None
    replies, commands = history
    publications = [item for item in replies if item.action == "publish" and item.parsed
                    and item.parsed.get("type") == "commission_published"
                    and item.parsed.get("commission_id") == commission["id"]
                    and sender_matches_identity(item.command_sender, owner_id)
                    and (not commission.get("publish_msg_id") or commission["publish_msg_id"] == item.root_msg_id)]
    if len(publications) != 1:
        return None
    publication = publications[0]
    reply_by_root = {(reply.chat_id, reply.root_msg_id): reply for reply in replies}
    for command in commands:
        key = command.chat_id, command.root_msg_id
        if (key == (publication.chat_id, publication.root_msg_id)
                or command.action not in {"publish", "cancel"}
                or not sender_matches_identity(command.command_sender, owner_id)):
            continue
        reply = reply_by_root.get(key)
        if reply is not None and compare_points(reply.end, publication.start) == -1:
            continue
        parsed = reply.parsed if reply is not None and reply.parsed else {}
        unchanged = (
            command.action == "publish" and parsed.get("type") == "commission_existing"
            and parsed.get("commission_id") == commission["id"]
        ) or (command.action == "cancel" and parsed.get("type") == "commission_cancel_blocked")
        if not unchanged:
            return None

    accepts = [item for item in replies if item.action == "accept" and item.argument == commission["id"]
               and item.parsed and item.parsed.get("type") == "commission_accepted"
               and target_matches(item.parsed.get("target_username"))
               and item.parsed.get("helper_username")
               and compare_points(publication.end, item.start) == -1]
    if len(accepts) != 1 or sender_matches_identity(accepts[0].command_sender, helper_id):
        return None
    acceptance = accepts[0]
    completions = [item for item in replies if item.action == "strip" and target_matches(item.argument)
                   and item.command_sender == acceptance.command_sender
                   and item.parsed and item.parsed.get("type") in {"assist_strip_success", "assist_strip_failed"}
                   and target_matches(item.parsed.get("target_username"))
                   and compare_points(acceptance.end, item.start) == -1]
    return CommissionEvidence(publication, acceptance, completions[0] if len(completions) == 1 else None)
