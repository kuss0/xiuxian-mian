"""Offline Yinluo resource projection, separate from reservations and scheduling.

The cultivation ledger is supplied by the existing shared owner; it is not
stored a second time in this book. A projection returns both values without
writing either of them. Runtime adoption, persistence and dispatch admission
must be implemented together before this module can authorize an action.
"""

import copy
from dataclasses import dataclass

from . import resource_accounting as accounting
from .profile_observation import timestamp
from .verified_event import VerifiedGameEvent
from .yinluo_resource_facts import (
    OwnedYinluoSource,
    YinluoCommand,
    YinluoResourceReply,
    parse_yinluo_resource_command,
    parse_yinluo_resource_panel,
    parse_yinluo_resource_reply,
    qualify_yinluo_resource_reply,
)


MAX_RECEIPTS = 256
MAX_RESOURCE_TYPES = 64
_CHARGES = {"summon_cost", "forest_sha"}
_COSTS = {"convert_cost", "soothe_cost", "summon_cost", "summon_backlash", "forest_sha", "refine_sha", "refine_soul", "assist_sha", "assist_backlash"}
_COMPONENTS = {
    "banner": {}, "collect": {},
    "convert": {"convert_cost": "cultivation", "convert_sha": "sha", "convert_bonus": "sha"},
    "soothe": {"soothe_cost": "cultivation"},
    "sacrifice": {"sacrifice_sha": "sha", "sacrifice_bonus": "sha"},
    "summon": {"summon_cost": "cultivation", "summon_backlash": "cultivation", "summon_soul": "souls"},
    "forest": {"forest_sha": "sha", "forest_soul": "souls", "forest_bonus_soul": "souls"},
    "refine": {"refine_sha": "sha", "refine_soul": "souls"},
    "assist_banner": {"assist_sha": "sha"},
    "assist_strip": {"assist_sha": "sha", "assist_backlash": "cultivation"},
}


def _resource(value, *, concrete=False):
    if value in ("sha", "cultivation") or (not concrete and value == "souls"):
        return True
    if not isinstance(value, str) or not value.startswith("soul:"):
        return False
    name = value[5:]
    return 0 < len(name) <= 80 and name.strip() == name and not any(ord(char) < 32 or ord(char) == 127 for char in name)


def _component(command, component, resource):
    if not isinstance(component, str) or not _resource(resource):
        return False
    expected = _COMPONENTS[command.action].get(component)
    if component == "refine_soul":
        return resource == f"soul:{command.target}"
    return expected == resource or (expected == "souls" and resource.startswith("soul:"))


def _key(receipt, component):
    evidence = receipt["start"]["evidence"]
    return f"yinluo_{component}:command:{evidence['chat_id']}:{evidence['msg_id']}"


def _operation(receipt):
    return receipt["start"]["evidence"]["chat_id"], receipt["start"]["evidence"]["msg_id"]


def _valid_receipt(receipt):
    if not isinstance(receipt, dict) or receipt.keys() != {"command", "start", "end", "sender_id", "phase", "scopes", "required", "effects"}:
        return False
    command = parse_yinluo_resource_command(receipt["command"])
    if (
        command is None or command.text != receipt["command"]
        or not accounting.valid_point(receipt["start"], telegram_only=True)
        or not accounting.valid_point(receipt["end"], telegram_only=True)
        or receipt["start"]["evidence"]["edited"]
        or receipt["start"]["evidence"]["chat_id"] != receipt["end"]["evidence"]["chat_id"]
        or receipt["start"]["evidence"]["msg_id"] >= receipt["end"]["evidence"]["msg_id"]
        or receipt["start"]["at"] > receipt["end"]["at"]
        or type(receipt["sender_id"]) is not int or receipt["sender_id"] <= 0
        or receipt["phase"] not in ("panel", "pending", "success", "failed", "denied", "conflict", "unknown")
        or not isinstance(receipt["scopes"], list)
        or any(scope not in ("charge", "outcome") for scope in receipt["scopes"])
        or len(set(receipt["scopes"])) != len(receipt["scopes"])
        or not isinstance(receipt["required"], list) or len(receipt["required"]) > 8
        or not isinstance(receipt["effects"], list) or len(receipt["effects"]) > 8
    ):
        return False
    for requirement in receipt["required"]:
        if not isinstance(requirement, list) or len(requirement) != 2 or not _component(command, *requirement):
            return False
    if len({item[0] for item in receipt["required"]}) != len(receipt["required"]):
        return False
    for effect in receipt["effects"]:
        if not isinstance(effect, dict) or effect.keys() != {"component", "resource", "amount"}:
            return False
        if not _component(command, effect["component"], effect["resource"]):
            return False
        if [effect["component"], effect["resource"]] not in receipt["required"]:
            return False
        amount = effect["amount"]
        if amount is not None and (
            type(amount) is not int or abs(amount) > accounting.MAX_BALANCE
            or (amount > 0 if effect["component"] in _COSTS else amount < 0)
            or effect["resource"] == "souls"
        ):
            return False
    scopes = {"charge" if effect["component"] in _CHARGES else "outcome" for effect in receipt["effects"]}
    if receipt["phase"] in ("success", "failed"):
        scopes.add("outcome")
    if receipt["phase"] == "denied" and (receipt["required"] or receipt["effects"]):
        return False
    return (
        len({effect["component"] for effect in receipt["effects"]}) == len(receipt["effects"])
        and receipt["scopes"] == sorted(scopes)
    )


def new_yinluo_book(identity_id, account_id):
    if type(identity_id) is not int or identity_id <= 0 or type(account_id) is not int or account_id <= 0:
        raise ValueError("A Yinluo resource book needs an explicit identity/account")
    return {"version": 1, "identity_id": identity_id, "account_id": account_id, "ledgers": {}, "receipts": [], "gap": None}


def read_yinluo_book(value):
    """Corrupt/legacy data is not an empty verified history."""
    if (
        not isinstance(value, dict) or value.keys() != {"version", "identity_id", "account_id", "ledgers", "receipts", "gap"}
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["identity_id"]) is not int or value["identity_id"] <= 0
        or type(value["account_id"]) is not int or value["account_id"] <= 0
        or not isinstance(value["ledgers"], dict) or len(value["ledgers"]) > MAX_RESOURCE_TYPES
        or not isinstance(value["receipts"], list) or len(value["receipts"]) > MAX_RECEIPTS
        or any(not _valid_receipt(receipt) for receipt in value["receipts"])
    ):
        return None
    if any(not _resource(resource, concrete=True) or resource == "cultivation" or accounting.read_ledger(ledger) is None for resource, ledger in value["ledgers"].items()):
        return None
    gap = value["gap"]
    if gap is not None and (
        not isinstance(gap, dict) or gap.keys() != {"reason", "at"}
        or gap["reason"] not in ("capacity", "command_conflict") or timestamp(gap["at"]) <= 0
    ):
        return None
    commands = {}
    messages = {}
    for receipt in value["receipts"]:
        operation = _operation(receipt)
        command = receipt["command"], receipt["start"]
        if operation in commands and commands[operation] != command:
            return None
        commands[operation] = command
        result_key = receipt["end"]["evidence"]["chat_id"], receipt["end"]["evidence"]["msg_id"]
        owner = operation, receipt["sender_id"]
        if result_key in messages and messages[result_key] != owner:
            return None
        messages[result_key] = owner
    keys = _book_keys(value)
    if any(entry["key"] not in keys for ledger in value["ledgers"].values() for entry in accounting.read_ledger(ledger)["entries"]):
        return None
    return copy.deepcopy(value)


@dataclass(frozen=True)
class YinluoProjection:
    book: dict
    cultivation: dict


def _source_owned(book, source):
    return (
        isinstance(source, OwnedYinluoSource)
        and type(source.identity_id) is int and source.identity_id == book["identity_id"]
        and type(source.account_id) is int and source.account_id == book["account_id"]
        and isinstance(source.command, YinluoCommand)
        and parse_yinluo_resource_command(source.command.text) == source.command
        and type(source.result_sender_id) is int and source.result_sender_id > 0
        and accounting.valid_point(source.start, telegram_only=True)
        and accounting.valid_point(source.end, telegram_only=True)
        and source.command_msg_id < source.result_msg_id and source.command_at <= source.result_at
    )


def _gap(book, reason, point):
    previous_at = book["gap"]["at"] if book["gap"] else 0
    book["gap"] = {"reason": reason, "at": max(previous_at, point["at"])}


def _grouped_receipts(book):
    groups = {}
    for receipt in book["receipts"]:
        groups.setdefault(_operation(receipt), []).append(receipt)
    return groups.values()


def _receipt_scopes(receipt, group):
    if receipt["phase"] in ("denied", "conflict"):
        return {"charge", "outcome"}
    if receipt["phase"] != "unknown" or not receipt["end"]["evidence"]["edited"]:
        return set(receipt["scopes"])
    predecessors = [
        other for other in group
        if other["end"]["evidence"]["msg_id"] == receipt["end"]["evidence"]["msg_id"]
        and other["phase"] != "unknown" and accounting.compare_points(other["end"], receipt["end"]) in (-1, 0)
    ]
    if not predecessors:
        return set()
    latest_at = max(other["end"]["at"] for other in predecessors)
    return {
        scope for other in predecessors if other["end"]["at"] == latest_at
        for scope in ({"outcome"} if other["phase"] in ("success", "failed") else other["scopes"])
    }


def _materialized(book):
    """Expand edits within their semantic scope; never retract an earlier phase.

    Missing components are synthesized from all retained receipts, so learning
    an old bonus or an old soul name later cannot escape a newer withdrawal.
    These synthesized unknowns are projections, not stored financial facts.
    """
    records = []
    for group in _grouped_receipts(book):
        components = {}
        explicit = set()
        for receipt in group:
            for component, resource in receipt["required"]:
                components.setdefault(component, set()).add(resource)
            for effect in receipt["effects"]:
                components.setdefault(effect["component"], set()).add(effect["resource"])
                explicit.add(effect["component"])
        for component, resources in components.items():
            if any(resource.startswith("soul:") for resource in resources):
                resources.add("souls")
            scope = "charge" if component in _CHARGES else "outcome"
            for receipt in group:
                effect = next((item for item in receipt["effects"] if item["component"] == component), None)
                covered = scope in _receipt_scopes(receipt, group)
                if effect is None and (not covered or (receipt["phase"] == "denied" and component not in explicit)):
                    continue
                for resource in resources:
                    amount = effect["amount"] if effect is not None and effect["resource"] == resource else None
                    known_name = bool(effect is not None and effect["resource"].startswith("soul:") and effect["amount"] is not None)
                    records.append({
                        "key": _key(receipt, component), "resource": resource, "amount": amount,
                        "start": receipt["start"], "end": receipt["end"],
                        "known_name": known_name,
                    })
    return sorted(records, key=lambda item: (item["end"]["at"], item["end"]["evidence"]["msg_id"], item["end"]["evidence"]["edited"]))


def _book_keys(book):
    return {
        _key(receipt, component)
        for receipt in book["receipts"]
        for component in {item[0] for item in receipt["required"]} | {item["component"] for item in receipt["effects"]}
    }


def _project(book, cultivation):
    ledgers = {**copy.deepcopy(book["ledgers"]), "cultivation": copy.deepcopy(cultivation)}
    owned_keys = _book_keys(book)
    for resource, ledger in list(ledgers.items()):
        ledger = accounting.read_ledger(ledger)
        ledger["entries"] = [entry for entry in ledger["entries"] if entry["key"] not in owned_keys]
        ledgers[resource] = ledger
    grouped = {}
    for record in _materialized(book):
        if record["resource"] != "souls":
            grouped.setdefault((record["resource"], record["key"]), []).append(record)
    for (resource, _key_name), records in grouped.items():
        if resource not in ledgers and len(ledgers) - 1 >= MAX_RESOURCE_TYPES:
            _gap(book, "capacity", records[-1]["end"])
            return YinluoProjection(book, cultivation)
        destination = ledgers.setdefault(resource, accounting.empty_ledger())
        # Revision folding is local to one component/command. Copying and
        # validating the entire resource history for every frame is quadratic.
        single = {**accounting.empty_ledger(), "baseline": destination["baseline"]}
        for record in records:
            if record["amount"] is None:
                single = accounting.record_unresolved_delta(single, record["key"], record["start"], record["end"])
            else:
                single = accounting.record_delta(single, record["key"], record["amount"], record["start"], record["end"])
            if single is None:
                return None
        destination["overflow_at"] = max(destination["overflow_at"], single["overflow_at"])
        for entry in single["entries"]:
            if len(destination["entries"]) >= accounting.MAX_ENTRIES:
                # Reuse the scalar ledger's coverage contract on validated
                # entries. Uncovered history is never evicted for this batch.
                retired = next((item for item in destination["entries"] if accounting._relative_to_baseline(item, destination["baseline"]) == "included"), None)
                if retired is not None:
                    destination["entries"].remove(retired)
                elif accounting._relative_to_baseline(entry, destination["baseline"]) == "included":
                    continue
                else:
                    destination["overflow_at"] = max(destination["overflow_at"], entry["revision"]["at"])
                    continue
            destination["entries"].append(entry)
    if any(accounting.read_ledger(ledger) is None for ledger in ledgers.values()):
        return None
    cultivation = ledgers.pop("cultivation")
    book["ledgers"] = ledgers
    return YinluoProjection(book, cultivation)


def _owned_cultivation_sources_match(book, cultivation):
    sources = {}
    for record in _materialized(book):
        if record["resource"] == "cultivation":
            sources.setdefault(record["key"], set()).add(record["end"]["evidence"]["msg_id"])
    return all(
        not entry["key"].startswith("yinluo_") or set(entry["sources"]) <= sources.get(entry["key"], set())
        for entry in cultivation["entries"]
    )


def stage_yinluo_reply(book, cultivation, source, reply, *, identity_usernames):
    book = read_yinluo_book(book)
    cultivation = accounting.read_ledger(cultivation)
    if book is None or cultivation is None or not _source_owned(book, source):
        return None
    if not isinstance(reply, YinluoResourceReply) or source.command != reply.command:
        return None
    reply = qualify_yinluo_resource_reply(source, reply, identity_usernames)
    if reply is None:
        return None
    # Do not erase a delta imported from an unknown pre-journal owner.
    if not _owned_cultivation_sources_match(book, cultivation):
        return None
    receipt = {
        "command": source.command.text, "start": source.start, "end": source.end,
        "sender_id": source.result_sender_id, "phase": reply.phase, "scopes": list(reply.scopes),
        "required": [list(item) for item in reply.required],
        "effects": [{"component": item.component, "resource": item.resource, "amount": item.amount} for item in reply.effects],
    }
    if not _valid_receipt(receipt):
        return None
    if receipt in book["receipts"]:
        return _project(book, cultivation)
    for previous in book["receipts"]:
        same_command = _operation(previous) == _operation(receipt)
        same_message = previous["end"]["evidence"]["chat_id"] == source.chat_id and previous["end"]["evidence"]["msg_id"] == source.result_msg_id
        if (
            (same_command and (previous["command"] != receipt["command"] or previous["start"] != receipt["start"]))
            or (same_message and (not same_command or previous["sender_id"] != source.result_sender_id))
        ):
            _gap(book, "command_conflict", source.end)
            return YinluoProjection(book, cultivation)
    if len(book["receipts"]) >= MAX_RECEIPTS:
        _gap(book, "capacity", source.end)
        return YinluoProjection(book, cultivation)
    book["receipts"].append(receipt)
    return _project(book, cultivation)


def stage_yinluo_panel(book, source, text):
    book = read_yinluo_book(book)
    if book is None or not _source_owned(book, source) or source.command.action != "banner":
        return None
    panel = parse_yinluo_resource_panel(text)
    if panel is None or not accounting.valid_point(source.end, telegram_only=True):
        return None
    values = {} if panel.sha is None else {"sha": panel.sha}
    values.update({f"soul:{name}": count for name, count in panel.souls or ()})
    for resource, value in values.items():
        if resource not in book["ledgers"] and len(book["ledgers"]) >= MAX_RESOURCE_TYPES:
            _gap(book, "capacity", source.end)
            return book
        ledger = accounting.record_snapshot(book["ledgers"].get(resource, {}), value, source.end)
        if ledger is None:
            return None
        book["ledgers"][resource] = ledger
    return book


def stage_unresolved_yinluo_edit(book, cultivation, event, *, identity_accounts, game_chats, game_bots, now):
    """Invalidate retained facts when a known native edit has lost its context.

    This path cannot apply the new text as income, consume a reservation or
    complete gameplay. Original ownership comes only from this book's retained
    command/result pair; neither a routing hint nor a username can rebind it.
    """
    book = read_yinluo_book(book)
    if (
        book is None or not isinstance(event, VerifiedGameEvent) or event.event_type != "edit"
        or not isinstance(identity_accounts, dict)
        or any(type(identity_id) is not int or identity_id <= 0 for identity_id in identity_accounts)
        or type(identity_accounts.get(book["identity_id"])) is not int
        or identity_accounts[book["identity_id"]] != book["account_id"]
        or not isinstance(game_chats, (list, tuple, set, frozenset))
        or not isinstance(game_bots, (list, tuple, set, frozenset))
        or any(type(value) is not int or value == 0 for value in game_chats)
        or any(type(value) is not int or value <= 0 for value in game_bots)
        or type(event.chat_id) is not int or event.chat_id not in game_chats
        or type(event.sender_id) is not int or event.sender_id not in game_bots
        or type(event.msg_id) is not int or event.msg_id <= 0
        or timestamp(event.server_event_at) <= 0 or timestamp(now) <= 0
        or event.server_event_at > timestamp(now) + 1
    ):
        return None
    matches = [
        receipt for receipt in book["receipts"]
        if receipt["end"]["evidence"]["chat_id"] == event.chat_id
        and receipt["end"]["evidence"]["msg_id"] == event.msg_id
        and receipt["sender_id"] == event.sender_id
    ]
    if not matches:
        return None
    previous = matches[0]
    command = parse_yinluo_resource_command(previous["command"])
    source = OwnedYinluoSource(
        book["identity_id"], book["account_id"], command, event.chat_id,
        previous["start"]["evidence"]["msg_id"], previous["start"]["at"],
        event.msg_id, event.server_event_at, True, event.sender_id,
    )
    return stage_yinluo_reply(
        book, cultivation, source, parse_yinluo_resource_reply(command.text, ""), identity_usernames={},
    )


def _covered(point, ledger):
    baseline = ledger.get("baseline")
    return bool(baseline is not None and not baseline["conflicted"] and accounting.compare_points(point, baseline["point"]) in (-1, 0))


def _same_financial_projection(left, right):
    def active(ledger):
        return sorted(
            (entry for entry in ledger["entries"] if accounting._relative_to_baseline(entry, ledger["baseline"]) != "included"),
            key=lambda entry: entry["key"],
        )
    # Other shared cultivation writers may append after a Yinluo result or
    # retire covered entries. Neither changes this book's financial projection.
    return (
        left["version"] == right["version"] and left["baseline"] == right["baseline"]
        and left["overflow_at"] == right["overflow_at"] and active(left) == active(right)
    )


def yinluo_resource_balance(book, cultivation, resource):
    """Projected balance only. This does not subtract send reservations yet."""
    book = read_yinluo_book(book)
    cultivation = accounting.read_ledger(cultivation)
    if book is None or cultivation is None or not _resource(resource, concrete=True):
        return {"status": "corrupt", "value": None}
    if not _owned_cultivation_sources_match(book, cultivation):
        return {"status": "unowned_cultivation", "value": None}
    if book["gap"]:
        return {"status": book["gap"]["reason"], "value": None}
    projected = _project(copy.deepcopy(book), cultivation)
    if projected is None:
        return {"status": "inconsistent_projection", "value": None}
    if projected.book["gap"]:
        return {"status": projected.book["gap"]["reason"], "value": None}
    if (
        projected.book["ledgers"].keys() != book["ledgers"].keys()
        or any(not _same_financial_projection(ledger, accounting.read_ledger(book["ledgers"][key])) for key, ledger in projected.book["ledgers"].items())
        or not _same_financial_projection(projected.cultivation, cultivation)
    ):
        return {"status": "inconsistent_projection", "value": None}
    ledger = cultivation if resource == "cultivation" else accounting.read_ledger(book["ledgers"].get(resource, {}))
    balance = accounting.evaluate(ledger)
    if balance["status"] != "ready":
        return balance
    for group in _grouped_receipts(book):
        for receipt in group:
            if any(other["phase"] == "denied" and accounting.compare_points(receipt["end"], other["end"]) in (-1, 0) for other in group):
                continue
            for component, needed in receipt["required"]:
                if needed != resource or _covered(receipt["end"], ledger):
                    continue
                if not any(entry["key"] == _key(receipt, component) for entry in ledger["entries"]):
                    return {"status": "missing_effect", "value": None}
    if resource.startswith("soul:"):
        records = [item for item in _materialized(book) if item["resource"] == "souls"]
        for item in records:
            if item["known_name"] or _covered(item["end"], ledger):
                continue
            if not any(
                other["key"] == item["key"] and other["known_name"]
                and other["end"]["at"] > item["end"]["at"]
                and accounting.compare_points(item["end"], other["end"]) == -1
                for other in records
            ):
                return {"status": "unknown_soul_effect", "value": None}
    return balance
