"""Identity-owned Yinluo facts and reservations, saved in the normal state transaction.

Balances are observations, reservations are unresolved commands. A transport
receipt binds those commands but is never a financial or gameplay completion.
"""

import copy
import re
import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from . import cultivation_accounting as cultivation
from . import persistence, resource_accounting as scalar
from . import yinluo_archive as archive
from .message_keys import find_message_key, message_key_parts
from .profile_observation import timestamp
from .state import (
    get_game_bot_ids, get_game_group_ids, get_identity_account, get_identity_ids,
    get_identity_state, get_send_as_profile, has_identity, update_send_as_profile,
)
from .verified_event import VerifiedGameEvent
from .yinluo_resource_book import (
    MAX_RECEIPTS, _book_keys, _grouped_receipts, _materialized, _operation, _owned_cultivation_sources_match,
    _project, _receipt_scopes, _same_financial_projection,
    new_yinluo_book, read_yinluo_book, stage_unresolved_yinluo_edit,
    stage_yinluo_panel, stage_yinluo_reply, yinluo_resource_balance,
)
from .yinluo_resource_facts import (
    admit_yinluo_resource_source, parse_yinluo_resource_command,
    parse_yinluo_resource_panel, parse_yinluo_resource_reply, qualify_yinluo_resource_reply,
)


STATE_KEY = "yinluo_accounting"
MAX_OPERATIONS = 64
MAX_BUSINESS_POINTS = 120
LIVE_PHASES = {"prepared", "sent", "unknown"}
TERMINAL_PHASES = {"success", "failed", "denied", "panel"}
LEGACY_PENDING_FIELDS = ("auto_collect_pending", "auto_refine_pending", "auto_soothe_pending")
LEGACY_ACTION_TAGS = {
    "collect": "collect", "refine": "refine", "soothe": "soothe", "convert": "convert",
    "sacrifice": "daily_sacrifice", "forest": "blood_forest", "summon": "demon_summon",
}


def reservation_budget(command):
    """Protocol planning limits, never observed debits."""
    parsed = parse_yinluo_resource_command(command)
    if parsed is None:
        return None
    costs, exclusive = {}, []
    if parsed.action == "convert":
        costs["cultivation"] = parsed.amount
    elif parsed.action == "soothe":
        costs["cultivation"] = 50
    elif parsed.action == "summon":
        costs["cultivation"] = 5000
        exclusive.append("cultivation")
    elif parsed.action == "forest":
        # Existing text does not establish a numeric upper bound for this cost.
        exclusive.append("sha")
    elif parsed.action == "refine":
        costs[f"soul:{parsed.target}"] = 1
        costs["sha"] = 400 if any(token in parsed.target for token in ("妖兽", "精魄")) and not any(
            token in parsed.target for token in ("凶兽", "戾魄")
        ) else 1000
    elif parsed.action in {"assist_banner", "assist_strip"}:
        costs["sha"] = 80 if parsed.action == "assist_banner" else 120
        if parsed.action == "assist_strip":
            costs["cultivation"] = 500
            exclusive.append("cultivation")
    return {"costs": costs, "exclusive": exclusive}


def _legacy_observation_pending(observed):
    if not isinstance(observed, dict):
        return True
    return observed.get("legacy_pending_invalid", False) is not False or any(
        not isinstance(observed.get(key, {}), dict) or bool(observed.get(key, {}))
        for key in LEGACY_PENDING_FIELDS
    )


def legacy_observation_pending(identity_id):
    return _legacy_observation_pending(get_identity_state(identity_id).get("yinluo_observation"))


def _legacy_tagged_pending(item):
    if (not isinstance(item, dict) or item.get("source_module") != "阴罗宗"
            or not isinstance(item.get("op_id"), str) or not item["op_id"].startswith("yinluo-")):
        return False
    command = parse_yinluo_resource_command(item.get("cmd"))
    return command is None or command.action != "banner"


def _legacy_pending(identity_id, *, identity=None):
    identity = get_identity_state(identity_id) if identity is None else identity
    if _legacy_observation_pending(identity.get("yinluo_observation")) or identity["yinluo_observation"].get("last_result") == "pending":
        return True
    pending = identity.get("pending_tasks")
    if not isinstance(pending, dict):
        return True
    for item in pending.values():
        if not isinstance(item, dict):
            return True
        if _legacy_tagged_pending(item):
            return True
        command = parse_yinluo_resource_command(item.get("cmd"))
        if command is not None and command.action != "banner":
            return True
    for owner_id in get_identity_ids():
        wanxin = get_identity_state(owner_id).get("wanxin_observation", {})
        if not isinstance(wanxin, dict):
            continue
        operations = [wanxin.get("pending", {})]
        held = wanxin.get("unresolved_actions", {})
        assist = wanxin.get("assist", {})
        if isinstance(assist, dict) and assist.get("send_as_id") == identity_id and (
            wanxin.get("unresolved_invalid") or not isinstance(held, dict)
        ):
            return True
        if isinstance(held, dict):
            operations.extend(held.values())
        for pending in operations:
            if (isinstance(pending, dict) and pending.get("send_as_id") == identity_id
                    and (not isinstance(pending.get("action"), str) or pending["action"] in {"banner", "strip"})):
                return True
    return False


def _valid_operation(item):
    if not isinstance(item, dict) or item.keys() != {
        "op_id", "command", "chat_id", "started_at", "msg_id", "sent_at", "phase", "budget", "source_module",
        "beneficiary",
    }:
        return False
    command = parse_yinluo_resource_command(item["command"])
    budget = item["budget"]
    if (
        not isinstance(budget, dict) or budget.keys() != {"costs", "exclusive"}
        or not isinstance(budget["costs"], dict) or any(type(amount) is not int or amount < 0 for amount in budget["costs"].values())
        or not isinstance(budget["exclusive"], list) or any(not isinstance(resource, str) for resource in budget["exclusive"])
    ):
        return False
    beneficiary = item["beneficiary"]
    if not isinstance(beneficiary, dict) or (beneficiary and (
        beneficiary.keys() != {"identity_id", "account_id", "commission_id", "published_at", "accepted_at"}
        or any(type(beneficiary[key]) is not int or beneficiary[key] <= 0
               for key in ("identity_id", "account_id", "commission_id"))
        or any(type(beneficiary[key]) not in {int, float} or timestamp(beneficiary[key]) != beneficiary[key]
               for key in ("published_at", "accepted_at"))
        or not 0 <= beneficiary["published_at"] <= beneficiary["accepted_at"] <= timestamp(item["started_at"])
        or command is None or command.action not in {"assist_banner", "assist_strip"}
        or item["source_module"] != "婉心封魂"
    )):
        return False
    return bool(
        command is not None and command.text == item["command"]
        and isinstance(item["op_id"], str) and re.fullmatch(r"[a-f0-9]{32}", item["op_id"])
        and type(item["chat_id"]) is int and item["chat_id"] != 0
        and type(item["started_at"]) in {int, float} and timestamp(item["started_at"]) > 0
        and type(item["msg_id"]) is int and item["msg_id"] >= 0
        and type(item["sent_at"]) in {int, float} and item["sent_at"] >= 0
        and timestamp(item["sent_at"]) == item["sent_at"]
        and ((item["msg_id"] == 0 and item["sent_at"] == 0) or (item["msg_id"] > 0 and item["sent_at"] >= item["started_at"] - 1))
        and isinstance(item["phase"], str) and item["phase"] in LIVE_PHASES | {"complete", "unsent", "read_expired"}
        and (item["phase"] not in {"sent", "complete"} or item["msg_id"] > 0)
        and (item["phase"] not in {"prepared", "unsent"} or item["msg_id"] == 0)
        and (item["phase"] != "read_expired" or command.action == "banner")
        and item["budget"] == reservation_budget(item["command"])
        and isinstance(item["source_module"], str) and item["source_module"] in {"阴罗宗", "婉心封魂"}
    )


def _valid_business_interval(points):
    return bool(
        isinstance(points, dict) and points.keys() == {"start", "end"}
        and all(scalar.valid_point(point, telegram_only=True) for point in points.values())
        and not points["start"]["evidence"]["edited"]
        and points["start"]["evidence"]["chat_id"] == points["end"]["evidence"]["chat_id"]
        and points["start"]["evidence"]["msg_id"] < points["end"]["evidence"]["msg_id"]
        and points["start"]["at"] <= points["end"]["at"]
    )


def _valid_business_manifest(value):
    return bool(
        isinstance(value, dict) and value.keys() == {"count", "digest"}
        and type(value["count"]) is int and 0 <= value["count"] < 2 ** 63
        and isinstance(value["digest"], str) and re.fullmatch(r"[0-9a-f]{64}", value["digest"])
        and (value["count"] != 0 or value == archive.empty_business_manifest())
    )


def _validated_accounting(value, identity_id, account_id):
    if isinstance(value, dict) and value.keys() == {"book", "operations", "hold", "business"}:
        value = {**value, "restored_roots": []}
    if isinstance(value, dict) and value.keys() == {"book", "operations", "hold", "business", "restored_roots"}:
        value = {**value, "business_archive": archive.empty_business_manifest()}
    if (
        not isinstance(value, dict) or value.keys() != {"book", "operations", "hold", "business", "restored_roots", "business_archive"}
        or not _valid_business_manifest(value["business_archive"])
        or not isinstance(value["operations"], list) or len(value["operations"]) > MAX_OPERATIONS
        or any(not _valid_operation(item) for item in value["operations"])
        or not isinstance(value["hold"], str) or value["hold"] not in {"", "legacy_pending", "capacity", "receipt_conflict"}
        or not isinstance(value["restored_roots"], list) or len(value["restored_roots"]) > MAX_RECEIPTS
        or any(not isinstance(root, list) or len(root) != 2
               or type(root[0]) is not int or root[0] == 0 or type(root[1]) is not int or root[1] <= 0
               for root in value["restored_roots"])
        or not isinstance(value["business"], dict) or len(value["business"]) > MAX_BUSINESS_POINTS
        or any(not isinstance(key, str) or len(key) > 40 or not _valid_business_interval(points)
               for key, points in value["business"].items())
    ):
        return None, "corrupt_yinluo_accounting"
    book = read_yinluo_book(value["book"])
    if book is None or book["identity_id"] != identity_id:
        return None, "corrupt_yinluo_accounting"
    if book["account_id"] != account_id:
        return None, "yinluo_account_changed"
    restored = {tuple(root) for root in value["restored_roots"]}
    if len(restored) != len(value["restored_roots"]) or not restored <= {_operation(item) for item in book["receipts"]}:
        return None, "corrupt_yinluo_accounting"
    ids = [item["op_id"] for item in value["operations"]]
    roots = [(item["chat_id"], item["msg_id"]) for item in value["operations"] if item["msg_id"]]
    if len(ids) != len(set(ids)) or len(roots) != len(set(roots)):
        return None, "corrupt_yinluo_accounting"
    for item in value["operations"]:
        if item["phase"] == "complete" and (
            not _receipt_matches_operation(book, item)
            or command_outcome(book, item["chat_id"], item["msg_id"]) not in TERMINAL_PHASES
        ):
            return None, "corrupt_yinluo_accounting"
    return copy.deepcopy(value), ""


def read_accounting(identity_id):
    if type(identity_id) is not int or identity_id <= 0 or not has_identity(identity_id):
        return None, "missing_identity"
    account_id = get_identity_account(identity_id)
    if account_id <= 0:
        return None, "missing_account"
    value = get_identity_state(identity_id).get(STATE_KEY, {})
    if value == {}:
        value = {
            "book": new_yinluo_book(identity_id, account_id), "operations": [],
            "hold": "legacy_pending" if _legacy_pending(identity_id) else "", "business": {},
        }
    value, reason = _validated_accounting(value, identity_id, account_id)
    if value is not None and not value["hold"]:
        pending = get_identity_state(identity_id).get("pending_tasks")
        if legacy_observation_pending(identity_id) or (
            isinstance(pending, dict) and any(_legacy_tagged_pending(item) for item in pending.values())
        ):
            value["hold"] = "legacy_pending"
    return value, reason


def identity_usernames():
    result = {}
    for identity_id in get_identity_ids():
        profile = get_send_as_profile(identity_id)
        result[identity_id] = {
            str(name).strip().lstrip("@").casefold()
            for name in [profile.get("username"), *(profile.get("username_aliases") or [])]
            if isinstance(name, str) and re.fullmatch(r"@?[A-Za-z0-9_]{1,32}", name.strip())
        }
    return result


def event_trust(now):
    # The shared source list also contains broadcast channels. Only configured
    # bot users can authorize Yinluo resource replies or replay intermediates.
    game_bots = [sender for sender in get_game_bot_ids() if type(sender) is int and sender > 0]
    return {
        "identity_accounts": {identity_id: get_identity_account(identity_id) for identity_id in get_identity_ids()},
        "game_chats": get_game_group_ids(), "game_bots": game_bots, "now": now,
    }


def _archive_connection():
    persistence.init_db()
    return persistence.get_db_conn()


def _read_archived_command(book, chat_id, msg_id):
    stored = archive.read_command(_archive_connection(), book["identity_id"], book["account_id"], chat_id, msg_id)
    if stored is None:
        return None
    payload, digest = stored
    _validate_archive_payload(payload, book["identity_id"], book["account_id"], chat_id, msg_id)
    indices = _archive_connection().execute(
        "SELECT msg_id, sender_id FROM yinluo_archive_messages "
        "WHERE identity_id=? AND account_id=? AND chat_id=? AND command_msg_id=?",
        (book["identity_id"], book["account_id"], chat_id, msg_id),
    ).fetchall()
    expected = {(item["end"]["evidence"]["msg_id"], item["sender_id"]) for item in payload["receipts"]}
    if {tuple(row) for row in indices} != expected:
        raise archive.ArchiveConflict("corrupt_yinluo_archive_result_index")
    return payload, digest


def _validate_archive_payload(payload, identity_id, account_id, chat_id, msg_id):
    if (not isinstance(payload, dict) or payload.keys() != {"version", "receipts", "operation"}
            or type(payload["version"]) is not int or payload["version"] not in {1, 2}):
        raise archive.ArchiveConflict("corrupt_yinluo_archive_contract")
    probe = new_yinluo_book(identity_id, account_id)
    probe["receipts"] = payload["receipts"]
    if read_yinluo_book(probe) is None or not probe["receipts"] or any(
        _operation(receipt) != (chat_id, msg_id) for receipt in probe["receipts"]
    ):
        raise archive.ArchiveConflict("corrupt_yinluo_archive_receipts")
    operation = payload["operation"]
    outcome = command_outcome(probe, chat_id, msg_id)
    if payload["version"] == 2:
        if (not isinstance(operation, dict) or operation.get("command") != ".我的阴罗幡"
                or outcome not in {"unknown", "conflict"}
                or any(receipt["phase"] not in {"panel", "unknown", "denied", "conflict"}
                       or receipt["effects"] or receipt["required"] or receipt["scopes"]
                       for receipt in probe["receipts"])):
            raise archive.ArchiveConflict("corrupt_yinluo_expired_read")
    elif outcome not in TERMINAL_PHASES:
        raise archive.ArchiveConflict("corrupt_yinluo_archive_receipts")
    if operation is not None and (
        not _valid_operation(operation) or operation["phase"] != ("read_expired" if payload["version"] == 2 else "complete")
        or (operation["chat_id"], operation["msg_id"]) != (chat_id, msg_id)
        or not _receipt_matches_operation(probe, operation)
    ):
        raise archive.ArchiveConflict("corrupt_yinluo_archive_operation")


def _group_coverage_deficits(book, ledger, group):
    single = {**book, "receipts": group}
    materialized = _materialized(single)
    deficits = {}

    def require(resource, point):
        destination = ledger if resource == "cultivation" else book["ledgers"].get(resource, {})
        baseline = destination.get("baseline")
        if not baseline or baseline["conflicted"] or scalar.compare_points(point, baseline["point"]) not in {-1, 0}:
            deficits.setdefault(resource, []).append(point)

    for effect in materialized:
        if effect["resource"] == "souls":
            if not effect["known_name"] and not any(
                later["key"] == effect["key"] and later["known_name"]
                and scalar.compare_points(effect["end"], later["end"]) == -1
                for later in materialized
            ):
                return None
            continue
        require(effect["resource"], effect["end"])
    for receipt in group:
        for _component, resource in receipt["required"]:
            if resource == "souls":
                if not any(effect["known_name"] for effect in materialized):
                    return None
                continue
            require(resource, receipt["end"])
    return deficits


def _group_has_native_coverage(book, ledger, group):
    return _group_coverage_deficits(book, ledger, group) == {}


def _group_in_flight(value, group):
    root = _operation(group[0])
    return any(item["phase"] in LIVE_PHASES and (
        (item["chat_id"], item["msg_id"]) == root or (
            not item["msg_id"] and item["chat_id"] == root[0] and item["command"] == group[0]["command"]
            and item["started_at"] - 1 <= group[0]["start"]["at"]
        )
    ) for item in value["operations"])


def _compact_value(value, ledger, changes, *, exclude=None, force=False):
    book = value["book"]
    pressure = len(book["receipts"]) >= MAX_RECEIPTS * 3 // 4 or len(value["operations"]) >= MAX_OPERATIONS * 3 // 4
    if (not force and not pressure) or value["hold"] or book["gap"] or not _owned_cultivation_sources_match(book, ledger):
        return False
    projected = _project(copy.deepcopy(book), ledger)
    if (projected is None or projected.book["gap"] or projected.book["ledgers"].keys() != book["ledgers"].keys()
            or not _same_financial_projection(projected.cultivation, ledger)
            or any(not _same_financial_projection(item, book["ledgers"][key]) for key, item in projected.book["ledgers"].items())):
        return False
    roots, retired_keys = set(), set()
    for group in _grouped_receipts(book):
        root = _operation(group[0])
        if root == exclude or command_outcome(book, *root) not in TERMINAL_PHASES:
            continue
        matches = [item for item in value["operations"] if (item["chat_id"], item["msg_id"]) == root]
        if _group_in_flight(value, group) or not _group_has_native_coverage(book, ledger, group):
            continue
        keys = _book_keys({**book, "receipts": group})
        if any(scalar._relative_to_baseline(entry, destination["baseline"]) != "included"
               for destination in [ledger, *book["ledgers"].values()]
               for entry in destination["entries"] if entry["key"] in keys):
            continue
        if _read_archived_command(book, *root) is not None:
            raise archive.ArchiveConflict("duplicate_hot_and_archived_command")
        changes.append(archive.ArchiveChange(
            book["identity_id"], book["account_id"], *root, None,
            {"version": 1, "receipts": copy.deepcopy(group), "operation": copy.deepcopy(matches[0]) if matches else None},
        ))
        roots.add(root)
        retired_keys.update(keys)
    book["receipts"] = [item for item in book["receipts"] if _operation(item) not in roots]
    value["operations"] = [item for item in value["operations"] if (item["chat_id"], item["msg_id"]) not in roots]
    value["restored_roots"] = [root for root in value["restored_roots"] if tuple(root) not in roots]
    for destination in [ledger, *book["ledgers"].values()]:
        destination["entries"] = [item for item in destination["entries"] if item["key"] not in retired_keys]
    return True


def capacity_status(identity_id):
    """Inspect reclaimable capacity without saving or inventing resource coverage."""
    value, reason = read_accounting(identity_id)
    ledger = cultivation.read_cultivation_ledger(identity_id)
    result = {"status": reason or "unverified_cultivation", "operations": 0, "receipts": 0, "resources": []}
    if value is None or ledger is None:
        return result
    result.update(operations=len(value["operations"]), receipts=len(value["book"]["receipts"]))
    if value["hold"] or value["book"]["gap"]:
        return {**result, "status": value["hold"] or value["book"]["gap"]["reason"]}
    try:
        _verify_business_manifest(value)
    except (archive.ArchiveConflict, sqlite3.Error):
        return {**result, "status": "archive_conflict"}

    def fits(operations, receipts):
        return operations < MAX_OPERATIONS - 1 and receipts < MAX_RECEIPTS - 1

    if fits(result["operations"], result["receipts"]):
        return {**result, "status": "ready"}
    if any(item["phase"] in LIVE_PHASES for item in value["operations"]):
        return {**result, "status": "yinluo_command_in_flight"}
    expired = tuple(item["op_id"] for item in value["operations"] if item["phase"] == "read_expired")
    if _expired_read_pending(value, expired) != {}:
        return {**result, "status": "yinluo_read_cleanup_pending"}
    ledger = copy.deepcopy(ledger)
    changes = []
    try:
        _retire_expired_reads(value, changes, expired)
        value["operations"] = [item for item in value["operations"] if item["phase"] not in {"unsent", "read_expired"}]
        coherent = _compact_value(value, ledger, changes, force=True)
    except (archive.ArchiveConflict, sqlite3.Error):
        return {**result, "status": "archive_conflict"}
    if not coherent:
        return {**result, "status": "unreconciled_capacity"}
    operations, receipts = len(value["operations"]), len(value["book"]["receipts"])
    if fits(operations, receipts):
        return {**result, "status": "ready"}
    if operations >= MAX_OPERATIONS or receipts >= MAX_RECEIPTS:
        return {**result, "status": "capacity_read_slot"}
    last_banner = None
    previous_banner = value["business"].get("action:banner")
    if previous_banner is not None:
        root = previous_banner["start"]["evidence"]
        try:
            history, _operations = _command_records(value, root["chat_id"], root["msg_id"], archive_changes=changes)
        except (archive.ArchiveConflict, sqlite3.Error):
            return {**result, "status": "archive_conflict"}
        if (command_outcome({"receipts": history}, root["chat_id"], root["msg_id"]) in TERMINAL_PHASES
                and any(item["command"] == ".我的阴罗幡" and item["start"] == previous_banner["start"]
                        and item["end"] == previous_banner["end"] for item in history)):
            last_banner = previous_banner["end"]
    coverable, needed = set(), set()
    for group in _grouped_receipts(value["book"]):
        root = _operation(group[0])
        if command_outcome(value["book"], *root) not in TERMINAL_PHASES or _group_in_flight(value, group):
            continue
        deficits = _group_coverage_deficits(value["book"], ledger, group)
        if deficits is None:
            needed.add("souls")
            continue
        needed.update(deficits)
        if deficits and all(
            (resource == "sha" or resource.startswith("soul:")) and (
                last_banner is None or any(scalar.compare_points(point, last_banner) not in {-1, 0} for point in points)
            ) for resource, points in deficits.items()
        ):
            coverable.add(root)
    remaining_operations = sum((item["chat_id"], item["msg_id"]) not in coverable for item in value["operations"])
    remaining_receipts = sum(_operation(item) not in coverable for item in value["book"]["receipts"])
    status = "banner_required" if coverable and fits(remaining_operations, remaining_receipts) else "native_coverage_required"
    return {**result, "status": status, "resources": sorted(needed)}


def _restore_archived_command(value, chat_id, msg_id, changes, *, stored=None):
    book = value["book"]
    if any(_operation(receipt) == (chat_id, msg_id) for receipt in book["receipts"]):
        raise archive.ArchiveConflict("duplicate_hot_and_archived_command")
    stored = stored or _read_archived_command(book, chat_id, msg_id)
    if stored is None:
        return None
    payload, digest = stored
    if len(book["receipts"]) + len(payload["receipts"]) > MAX_RECEIPTS or (
        payload["operation"] and len(value["operations"]) >= MAX_OPERATIONS
    ):
        raise archive.ArchiveConflict("yinluo_archive_restore_capacity")
    book["receipts"].extend(copy.deepcopy(payload["receipts"]))
    value["restored_roots"].append([chat_id, msg_id])
    if payload["operation"]:
        value["operations"].append(copy.deepcopy(payload["operation"]))
    changes.append(archive.ArchiveChange(book["identity_id"], book["account_id"], chat_id, msg_id, digest, None))
    return payload


def _command_records(value, chat_id, msg_id, *, archive_changes=()):
    receipts = [item for item in value["book"]["receipts"] if _operation(item) == (chat_id, msg_id)]
    operations = [item for item in value["operations"] if (item["chat_id"], item["msg_id"]) == (chat_id, msg_id)]
    if not receipts:
        book = value["book"]
        key = book["identity_id"], book["account_id"], chat_id, msg_id
        staged = [change for change in archive_changes if change.key == key]
        if len(staged) > 1:
            raise archive.ArchiveConflict("duplicate_yinluo_archive_change")
        if staged:
            payload = staged[0].payload
            if payload is not None:
                _validate_archive_payload(payload, *key)
            stored = (payload, None) if payload is not None else None
        else:
            stored = _read_archived_command(book, chat_id, msg_id)
        if stored is not None:
            payload, _digest = stored
            receipts = payload["receipts"]
            if payload["operation"] is not None:
                operations.append(payload["operation"])
    return receipts, operations


def _business_key_parts(key):
    matched = re.fullmatch(r"assist:([1-9][0-9]{0,18}):(banner|strip)", key) if isinstance(key, str) else None
    if matched is None or int(matched[1]) >= 2 ** 63:
        return None
    return int(matched[1]), matched[2]


def _validate_business_payload(payload, identity_id, account_id, business_key):
    if (not isinstance(payload, dict)
            or any(type(item) is not int or not 0 < item < 2 ** 63 for item in (identity_id, account_id))
            or payload.keys() != {"version", "identity_id", "account_id", "business_key", "points"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or any(type(payload[key]) is not int or payload[key] != expected
                   for key, expected in (("identity_id", identity_id), ("account_id", account_id)))
            or payload["business_key"] != business_key or _business_key_parts(business_key) is None
            or not _valid_business_interval(payload["points"])):
        raise archive.ArchiveConflict("corrupt_yinluo_business_contract")


def _business_point_supported(value, key, points, *, archive_changes=(), complete_only=False):
    parts = _business_key_parts(key)
    if parts is None or not _valid_business_interval(points):
        return False
    root = points["start"]["evidence"]
    receipts, operations = _command_records(value, root["chat_id"], root["msg_id"], archive_changes=archive_changes)
    if len(operations) != 1:
        return False
    operation = operations[0]
    command = parse_yinluo_resource_command(operation["command"])
    return bool(
        command.action == f"assist_{parts[1]}" and operation["beneficiary"].get("identity_id") == parts[0]
        and _receipt_matches_operation({"receipts": receipts}, operation)
        and (not complete_only or (operation["phase"] == "complete"
             and command_outcome({"receipts": receipts}, root["chat_id"], root["msg_id"]) in TERMINAL_PHASES))
        and any(item["start"] == points["start"] and item["end"] == points["end"]
                and item["phase"] in {"success", "failed", "denied"} for item in receipts)
    )


def _stored_business_point(value, key, *, archive_changes=()):
    if _business_key_parts(key) is None:
        return None
    book = value["book"]
    _verify_business_manifest(value)
    stored = archive.read_business_point(_archive_connection(), book["identity_id"], book["account_id"], key)
    if stored is not None and not _business_point_supported(value, key, stored[0]["points"], archive_changes=archive_changes):
        raise archive.ArchiveConflict("unproven_yinluo_business_point")
    return stored


def _verify_business_manifest(value):
    book = value["book"]
    actual = archive.business_manifest(_archive_connection(), book["identity_id"], book["account_id"])
    if actual != value.get("business_archive", archive.empty_business_manifest()):
        raise archive.ArchiveConflict("yinluo_business_manifest_mismatch")


def _business_point_is_newer(points, previous):
    return bool(scalar.compare_points(points["end"], previous["end"]) == 1 and (
        points["start"] == previous["start"] or scalar.compare_points(points["start"], previous["end"]) == 1
    ))


def _reclaim_business_slot(update):
    value = update.value
    for key, points in sorted(value["business"].items(), key=lambda item: (item[1]["end"]["at"], item[0])):
        if not _same_legacy_snapshot(points, update.previous.get("business", {}).get(key)):
            continue
        if not _business_point_supported(value, key, points, archive_changes=update.archive_changes, complete_only=True):
            continue
        if _stored_business_point(value, key, archive_changes=update.archive_changes) is not None:
            raise archive.ArchiveConflict("duplicate_hot_and_cold_business_point")
        book = value["book"]
        payload = {"version": 1, "identity_id": book["identity_id"], "account_id": book["account_id"],
                   "business_key": key, "points": copy.deepcopy(points)}
        update.business_changes += (archive.BusinessPointChange(book["identity_id"], book["account_id"], key, None, payload),)
        value["business"].pop(key)
        return True
    return False


def accept_business_point(update, key):
    points = {"start": update.source.start, "end": update.source.end}
    previous = update.value["business"].get(key)
    try:
        stored = _stored_business_point(update.value, key, archive_changes=update.archive_changes)
        if previous is not None and stored is not None:
            raise archive.ArchiveConflict("duplicate_hot_and_cold_business_point")
        if stored is not None:
            previous = stored[0]["points"]
        if previous is not None and not _business_point_is_newer(points, previous):
            return False
        if key not in update.value["business"] and len(update.value["business"]) >= MAX_BUSINESS_POINTS:
            if not _reclaim_business_slot(update):
                update.value["hold"] = update.value["hold"] or "capacity"
                return False
        if stored is not None:
            book = update.value["book"]
            update.business_changes += (archive.BusinessPointChange(book["identity_id"], book["account_id"], key, stored[1], None),)
        update.value["business"][key] = copy.deepcopy(points)
        return True
    except (archive.ArchiveConflict, sqlite3.Error):
        update.value["hold"] = update.value["hold"] or "receipt_conflict"
        return False


def _business_change_supported(value, change, *, archive_changes=()):
    stored = archive.read_business_point(_archive_connection(), *change.key)
    if ((stored[1] if stored is not None else None) != change.previous_digest
            or (stored is not None and not _business_point_supported(
                value, change.business_key, stored[0]["points"], archive_changes=archive_changes))):
        return False
    if change.payload is None:
        points = value["business"].get(change.business_key)
        return stored is not None and points is not None and _business_point_is_newer(points, stored[0]["points"])
    _validate_business_payload(change.payload, *change.key)
    return bool(
        stored is None and change.business_key not in value["business"]
        and _business_point_supported(value, change.business_key, change.payload["points"],
                                       archive_changes=archive_changes, complete_only=True)
    )


def _business_changes_valid(update):
    changes = update.business_changes
    book = update.value["book"]
    if (not isinstance(changes, tuple)
            or not _same_legacy_snapshot(update.owner.get(STATE_KEY, {}), update.previous)
            or any(not isinstance(change, archive.BusinessPointChange)
                   or change.key[:2] != (book["identity_id"], book["account_id"])
                   or _business_key_parts(change.business_key) is None for change in changes)
            or len({change.key for change in changes}) != len(changes)):
        return False
    previous = update.previous.get("business", {})
    current = update.value["business"]
    retired = {change.business_key for change in changes if change.payload is not None}
    restored = {change.business_key for change in changes if change.payload is None}
    # Every hot/cold move needs both halves, even if the caller omits all changes.
    if (previous.keys() - current.keys() != retired or not restored <= current.keys() - previous.keys()
            or not _same_legacy_snapshot(update.value.get("business_archive", archive.empty_business_manifest()),
                                         update.previous.get("business_archive", archive.empty_business_manifest()))):
        return False
    try:
        _verify_business_manifest(update.value)
        for key in current.keys() - previous.keys():
            stored = _stored_business_point(update.value, key, archive_changes=update.archive_changes)
            if (stored is not None) != (key in restored):
                return False
        for change in changes:
            if not _business_change_supported(update.value, change, archive_changes=update.archive_changes):
                return False
            if change.payload is not None and not _same_legacy_snapshot(
                change.payload["points"], previous.get(change.business_key),
            ):
                return False
    except (archive.ArchiveConflict, sqlite3.Error):
        return False
    return True


def _reply_matches(item, source, reply):
    return (
        item["command"] == source.command.text and item["start"] == source.start and item["end"] == source.end
        and item["sender_id"] == source.result_sender_id and item["phase"] == reply.phase
        and item["scopes"] == list(reply.scopes) and item["required"] == [list(part) for part in reply.required]
        and item["effects"] == [{"component": part.component, "resource": part.resource, "amount": part.amount}
                                for part in reply.effects]
    )


def command_outcome(book, chat_id, msg_id):
    receipts = [item for item in book["receipts"] if (
        item["start"]["evidence"]["chat_id"] == chat_id and item["start"]["evidence"]["msg_id"] == msg_id
    )]
    if not receipts:
        return ""
    outcomes = [item for item in receipts if (
        item["phase"] == "panel" or "outcome" in _receipt_scopes(item, receipts)
        or (item["phase"] == "unknown" and item["end"]["evidence"]["edited"] and any(
            previous["phase"] == "panel" and previous["end"]["evidence"]["msg_id"] == item["end"]["evidence"]["msg_id"]
            for previous in receipts
        ))
    )]
    # A newer edit of the summon charge does not revoke a separate final result.
    receipts = outcomes or receipts
    latest = [item for item in receipts if not any(scalar.compare_points(item["end"], other["end"]) == -1 for other in receipts)]
    phases = {item["phase"] for item in latest}
    return phases.pop() if len(phases) == 1 else "conflict"


def command_complete(value, chat_id, msg_id, *, command=None, sent_at=None):
    try:
        receipts, operations = _command_records(value, chat_id, msg_id)
    except (archive.ArchiveConflict, sqlite3.Error):
        return False
    read_only = bool(receipts) and all(item["command"] == ".我的阴罗幡" for item in receipts)
    if ((value["book"]["gap"] and not read_only)
            or command_outcome({"receipts": receipts}, chat_id, msg_id) not in TERMINAL_PHASES):
        return False
    if sent_at is not None and (
        timestamp(sent_at) <= 0 or any(operation["sent_at"] != sent_at for operation in operations)
        or any(receipt["start"]["at"] > sent_at + 1 for receipt in receipts)
    ):
        return False
    return not any(item["phase"] != "complete" for item in operations) and any(
        command is None or item["command"] == command for item in receipts
    )


def reply_complete(event, *, now):
    """Completion requires this native revision to have committed, not just its family."""
    source = admit_yinluo_resource_source(event, **event_trust(now))
    if source is None:
        return False
    value, _reason = read_accounting(source.identity_id)
    reply = qualify_yinluo_resource_reply(
        source, parse_yinluo_resource_reply(source.command.text, event.text), identity_usernames(),
    )
    if value is None or reply is None or reply.phase not in TERMINAL_PHASES or not command_complete(
        value, source.chat_id, source.command_msg_id, command=source.command.text,
    ):
        return False
    try:
        receipts, _operations = _command_records(value, source.chat_id, source.command_msg_id)
        return any(_reply_matches(item, source, reply) for item in receipts)
    except (archive.ArchiveConflict, sqlite3.Error):
        return False


def completed_reply_operation(value, source, reply):
    """Read one proven operation without restoring its archived resource history."""
    if (value["hold"] or value["book"]["gap"] or reply.phase not in TERMINAL_PHASES
            or value["book"]["identity_id"] != source.identity_id
            or value["book"]["account_id"] != source.account_id):
        return None
    try:
        receipts, operations = _command_records(value, source.chat_id, source.command_msg_id)
    except (archive.ArchiveConflict, sqlite3.Error):
        return None
    if (len(operations) != 1 or operations[0]["phase"] != "complete"
            or operations[0]["command"] != source.command.text
            or command_outcome({"receipts": receipts}, source.chat_id, source.command_msg_id) not in TERMINAL_PHASES
            or not any(_reply_matches(item, source, reply) for item in receipts)):
        return None
    return copy.deepcopy(operations[0])


def _receipt_matches_operation(book, operation):
    receipts = [item for item in book["receipts"] if (
        item["start"]["evidence"]["chat_id"] == operation["chat_id"]
        and item["start"]["evidence"]["msg_id"] == operation["msg_id"]
    )]
    return bool(receipts and all(item["command"] == operation["command"]
                and operation["started_at"] - 1 <= item["start"]["at"] <= operation["sent_at"] + 1 for item in receipts))


def _refresh_operations(value):
    for item in value["operations"]:
        if item["msg_id"] and item["phase"] != "unsent":
            outcome = command_outcome(value["book"], item["chat_id"], item["msg_id"])
            if outcome and not _receipt_matches_operation(value["book"], item):
                value["hold"] = "receipt_conflict"
                item["phase"] = "unknown"
            elif outcome in TERMINAL_PHASES and (not value["book"]["gap"] or item["command"] == ".我的阴罗幡"):
                item["phase"] = "complete"
            elif outcome and item["phase"] == "complete":
                item["phase"] = "unknown"


def _open_native_commands(value):
    roots = {
        (item["start"]["evidence"]["chat_id"], item["start"]["evidence"]["msg_id"])
        for item in value["book"]["receipts"]
        if parse_yinluo_resource_command(item["command"]).action != "banner"
    }
    return {root for root in roots if command_outcome(value["book"], *root) not in TERMINAL_PHASES}


def _reserved(value, resource, *, exclude=""):
    if value["hold"] or value["book"]["gap"]:
        return {"status": value["hold"] or value["book"]["gap"]["reason"], "value": None}
    amount = 0
    for item in value["operations"]:
        if item["phase"] not in LIVE_PHASES or item["op_id"] == exclude:
            continue
        if resource in item["budget"]["exclusive"]:
            return {"status": "yinluo_resource_in_flight", "value": None}
        amount += item["budget"]["costs"].get(resource, 0)
    return {"status": "ready", "value": amount}


def reserved_cultivation(identity_id):
    # Unrelated identities do not acquire a new resource dependency just by
    # reading cultivation. Legacy in-flight Yinluo work must still hold it.
    if not has_identity(identity_id):
        return {"status": "missing_identity", "value": None}
    if get_identity_state(identity_id).get(STATE_KEY, {}) == {} and not _legacy_pending(identity_id):
        return {"status": "ready", "value": 0}
    value, reason = read_accounting(identity_id)
    if value is None:
        return {"status": reason, "value": None}
    ledger = cultivation.read_cultivation_ledger(identity_id)
    balance = yinluo_resource_balance(value["book"], ledger, "cultivation")
    if balance["status"] != "ready":
        return balance
    return _reserved(value, "cultivation")


def resource_balance(identity_id, resource, *, available=True, exclude=""):
    value, reason = read_accounting(identity_id)
    ledger = cultivation.read_cultivation_ledger(identity_id)
    if value is None or ledger is None:
        return {"status": reason or "unverified_cultivation", "value": None}
    balance = yinluo_resource_balance(value["book"], ledger, resource)
    if resource == "sha" and balance["status"] == "ready":
        shortage = value["business"].get("shortage_sha")
        baseline = value["book"]["ledgers"].get("sha", {}).get("baseline")
        if shortage and (not baseline or scalar.compare_points(baseline["point"], shortage["end"]) != 1):
            return {"status": "sha_shortage_unreconciled", "value": None}
    if resource == "cultivation" and balance["status"] == "ready":
        balance = cultivation.observed_cultivation_balance(identity_id)
    if balance["status"] != "ready" or not available:
        return balance
    reserved = _reserved(value, resource, exclude=exclude)
    if reserved["status"] != "ready":
        return reserved
    return {"status": "ready", "value": max(0, balance["value"] - reserved["value"])}


def admission_reason(identity_id, command, *, exclude=""):
    parsed = parse_yinluo_resource_command(command)
    value, reason = read_accounting(identity_id)
    if value is None or parsed is None:
        return reason or "invalid_command"
    if parsed.action != "banner" and (value["hold"] or value["book"]["gap"]):
        return value["hold"] or value["book"]["gap"]["reason"]
    if any(item["phase"] in LIVE_PHASES and item["op_id"] != exclude and (
        parsed.action != "banner" or parse_yinluo_resource_command(item["command"]).action == "banner"
    ) for item in value["operations"]):
        return "yinluo_command_in_flight"
    if parsed.action != "banner" and _open_native_commands(value):
        return "yinluo_native_outcome_unknown"
    budget = reservation_budget(command)
    for resource in set(budget["costs"]) | set(budget["exclusive"]):
        balance = resource_balance(identity_id, resource, exclude=exclude)
        if balance["status"] != "ready":
            return f"{resource}:{balance['status']}"
        if balance["value"] < budget["costs"].get(resource, 0):
            return f"{resource}:insufficient"
    return ""


@dataclass
class ResourceUpdate:
    identity_id: int
    owner: dict
    previous: object
    previous_cultivation: object
    value: dict
    cultivation: dict
    source: object = None
    reply: object = None
    archive_changes: tuple = ()
    expired_reads: tuple = ()
    legacy_before: object = None
    legacy_pending_keys: tuple = ()
    business_changes: tuple = ()


def _update(identity_id, value, ledger, source=None, reply=None, *, archive_changes=(), expired_reads=()):
    owner = get_identity_state(identity_id)
    return ResourceUpdate(
        identity_id, owner, copy.deepcopy(owner.get(STATE_KEY, {})),
        copy.deepcopy(owner.get(cultivation.STATE_KEY, {})), value, ledger, source, reply,
        tuple(archive_changes), tuple(expired_reads),
    )


def _operation_pending(value, record, pending):
    owner_id, account_id = value["book"]["identity_id"], value["book"]["account_id"]
    op_id = record["op_id"]
    if not record["msg_id"]:
        if any(isinstance(item, dict) and item.get("op_id") == op_id for item in pending.values()):
            return None
        return {}
    root = record["chat_id"], record["msg_id"]
    matches = []
    for key in (root, record["msg_id"]):
        if key not in pending:
            continue
        item = pending[key]
        try:
            parts = message_key_parts(key, item)
        except (TypeError, ValueError, OverflowError):
            return None
        if parts != root:
            continue
        if (not isinstance(item, dict) or item.get("op_id") != op_id
                or item.get("source_module") != record["source_module"]
                or item.get("cmd") != record["command"]
                or type(item.get("chat_id")) is not int or item["chat_id"] != root[0]
                or type(item.get("sent_at")) not in {int, float} or item["sent_at"] != record["sent_at"]
                or type(item.get("account_id", account_id)) is not int
                or item.get("account_id", account_id) != account_id
                or any(type(item.get(field, owner_id)) is not int or item.get(field, owner_id) != owner_id
                       for field in ("send_as_id", "identity_id"))):
            return None
        matches.append(key)
    if len(matches) > 1:
        return None
    for key, item in pending.items():
        if key in matches or not isinstance(item, dict) or item.get("op_id") != op_id:
            continue
        try:
            chat_id, _msg_id = message_key_parts(key, item)
        except (TypeError, ValueError, OverflowError):
            return None
        if chat_id == root[0]:
            return None
    return {key: pending[key] for key in matches}


def _expired_read_pending(value, op_ids, *, archive_changes=()):
    if not op_ids:
        return {}
    if (not isinstance(op_ids, tuple) or len(op_ids) > MAX_OPERATIONS
            or any(not isinstance(op_id, str) for op_id in op_ids) or len(set(op_ids)) != len(op_ids)):
        return None
    pending = get_identity_state(value["book"]["identity_id"]).get("pending_tasks")
    if not isinstance(pending, dict):
        return None
    removals = {}
    for op_id in op_ids:
        records = [item for item in value["operations"] if item["op_id"] == op_id]
        for change in archive_changes:
            if not isinstance(change, archive.ArchiveChange) or change.payload is None:
                continue
            try:
                _validate_archive_payload(change.payload, *change.key)
            except archive.ArchiveConflict:
                return None
            operation = change.payload["operation"]
            if operation is not None and operation["op_id"] == op_id:
                if change.key[:2] != (value["book"]["identity_id"], value["book"]["account_id"]):
                    return None
                records.append(operation)
        if len(records) != 1 or records[0]["phase"] != "read_expired" or records[0]["command"] != ".我的阴罗幡":
            return None
        owned = _operation_pending(value, records[0], pending)
        if owned is None:
            return None
        removals.update(owned)
    return removals


def _retire_expired_reads(value, changes, op_ids):
    """Keep expired query ownership in cold storage without altering its facts."""
    book = value["book"]
    retired = set()
    for record in value["operations"]:
        root = record["chat_id"], record["msg_id"]
        if (record["op_id"] not in op_ids or record["phase"] != "read_expired" or not record["msg_id"]
                or record["command"] != ".我的阴罗幡"):
            continue
        group = [item for item in book["receipts"] if _operation(item) == root]
        if not group:
            continue
        payload = {"version": 2, "receipts": copy.deepcopy(group), "operation": copy.deepcopy(record)}
        _validate_archive_payload(payload, book["identity_id"], book["account_id"], *root)
        if _read_archived_command(book, *root) is not None:
            raise archive.ArchiveConflict("duplicate_hot_and_archived_command")
        changes.append(archive.ArchiveChange(book["identity_id"], book["account_id"], *root, None, payload))
        retired.add(root)
    if retired:
        book["receipts"] = [item for item in book["receipts"] if _operation(item) not in retired]
        value["operations"] = [item for item in value["operations"] if (item["chat_id"], item["msg_id"]) not in retired]
        value["restored_roots"] = [root for root in value["restored_roots"] if tuple(root) not in retired]


def _completed_resource_pending(value, *, archive_changes=()):
    book = value["book"]
    owner_id, account_id = book["identity_id"], book["account_id"]
    pending = get_identity_state(owner_id).get("pending_tasks")
    if not isinstance(pending, dict) or not pending:
        return {}
    removals = {}
    for key, item in pending.items():
        if not isinstance(item, dict):
            continue
        command = parse_yinluo_resource_command(item.get("cmd"))
        if command is None:
            continue
        # Query completion does not repair a resource gap or authorize spending.
        if command.action != "banner" and (value["hold"] or book["gap"]):
            continue
        try:
            chat_id, msg_id = message_key_parts(key, item)
        except (TypeError, ValueError, OverflowError):
            continue
        if (find_message_key(pending, msg_id, chat_id=chat_id) != key
                or type(item.get("chat_id")) is not int or item["chat_id"] != chat_id
                or type(item.get("account_id", account_id)) is not int
                or item.get("account_id", account_id) != account_id
                or any(type(item.get(field, owner_id)) is not int or item.get(field, owner_id) != owner_id
                       for field in ("send_as_id", "identity_id"))
                or timestamp(item.get("sent_at")) <= 0):
            continue
        try:
            receipts, operations = _command_records(value, chat_id, msg_id, archive_changes=archive_changes)
        except (archive.ArchiveConflict, sqlite3.Error):
            continue
        if (not receipts or any(receipt["command"] != command.text for receipt in receipts)
                or command_outcome({"receipts": receipts}, chat_id, msg_id) not in TERMINAL_PHASES
                or len(operations) > 1 or any(operation["phase"] != "complete" for operation in operations)):
            continue
        if operations:
            owned = _operation_pending(value, operations[0], pending)
            if owned is None or key not in owned:
                continue
        else:
            # Legacy/manual sends need their native command interval. Missing
            # operation ownership cannot be inferred from a matching family.
            started = timestamp(item.get("send_started_at"))
            op_id = item.get("op_id")
            if (started <= 0 or started > item["sent_at"]
                    or any(operation["op_id"] == op_id for operation in value["operations"])
                    or (isinstance(op_id, str) and re.fullmatch(r"[a-f0-9]{32}", op_id))
                    or any(not started - 1 <= receipt["start"]["at"] <= item["sent_at"] + 1 for receipt in receipts)):
                continue
        removals[key] = item
    return removals


def _legacy_slot_matches(payload, slot, action, key, pending, value):
    command = parse_yinluo_resource_command(pending["cmd"])
    if (command.action != action or command.slot != slot
            or timestamp(payload.get("sent_at")) <= 0 or payload["sent_at"] != pending["sent_at"]
            or (action == "refine" and payload.get("target") != command.target)):
        return False
    chat_id, msg_id = message_key_parts(key, pending)
    expected = {
        "chat_id": chat_id, "msg_id": msg_id, "account_id": value["book"]["account_id"],
        "identity_id": value["book"]["identity_id"], "send_as_id": value["book"]["identity_id"],
        "op_id": pending["op_id"], "command": command.text, "cmd": command.text,
    }
    if any(field in payload and (type(payload[field]) is not type(wanted) or payload[field] != wanted)
           for field, wanted in expected.items()):
        return False
    return "send_started_at" not in payload or (
        timestamp(payload["send_started_at"]) > 0 and payload["send_started_at"] == pending["send_started_at"]
    )


def _same_legacy_snapshot(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return len(left) == len(right) and all(
            any(_same_legacy_snapshot(key, other) for other in right)
            and _same_legacy_snapshot(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_same_legacy_snapshot(a, b) for a, b in zip(left, right))
    return left == right


def stage_legacy_completion(update):
    """Join old slot evidence to exact terminal receipts, without sending or saving."""
    value, owner = update.value, update.owner
    observed, pending = owner.get("yinluo_observation"), owner.get("pending_tasks")
    if (value["hold"] != "legacy_pending" or value["book"]["gap"] or not isinstance(observed, dict)
            or observed.get("legacy_pending_invalid", False) is not False or not isinstance(pending, dict)):
        return None
    proven = _completed_resource_pending({**value, "hold": ""}, archive_changes=update.archive_changes)
    candidates = {}
    for key, item in proven.items():
        if not _legacy_tagged_pending(item):
            continue
        command = parse_yinluo_resource_command(item["cmd"])
        _chat_id, msg_id = message_key_parts(key, item)
        if (not (type(key) is int or (type(key) is tuple and all(type(part) is int for part in key)))
                or ("msg_id" in item and (type(item["msg_id"]) is not int or item["msg_id"] != msg_id))
                or ("command" in item and item["command"] != command.text)):
            continue
        tag = re.fullmatch(r"yinluo-(?:auto-)?([a-z_]+)-([1-9][0-9]{0,12})", item["op_id"])
        if (tag is not None and tag[1] == LEGACY_ACTION_TAGS.get(command.action)
                and int(tag[2]) <= timestamp(item.get("send_started_at"))):
            candidates[key] = item
    if not candidates:
        return None
    projected = copy.deepcopy(observed)
    if observed.get("last_result") == "pending":
        summary = value["business"].get("summary")
        if not summary or observed.get("last_observed_at") != summary["end"]["at"]:
            return None
        root = summary["start"]["evidence"]["chat_id"], summary["start"]["evidence"]["msg_id"]
        if root not in {message_key_parts(key, item) for key, item in candidates.items()}:
            return None
        try:
            receipts, _operations = _command_records(value, *root, archive_changes=update.archive_changes)
        except (archive.ArchiveConflict, sqlite3.Error):
            return None
        if not any(item["start"] == summary["start"] and item["end"] == summary["end"]
                   and item["phase"] == "pending" for item in receipts):
            return None
        projected["last_result"] = "reconciled"
        projected["last_summary"] = "旧任务终局已由原生回包核对，等待幡面板校准。"
    for field in LEGACY_PENDING_FIELDS:
        payload = observed.get(field, {})
        if not isinstance(payload, dict):
            return None
        if not payload:
            continue
        action = field.removeprefix("auto_").removesuffix("_pending")
        slot = payload.get("slot")
        if "slot" in payload and (type(slot) is not int or not 1 <= slot <= 99):
            return None
        slots = payload.get("slots", [slot])
        if (not isinstance(slots, list) or not slots or any(type(slot) is not int or not 1 <= slot <= 99 for slot in slots)
                or len(slots) != len(set(slots)) or ("slot" in payload and slots != [slot])
                or (action != "collect" and "slot" not in payload)):
            return None
        if any(sum(_legacy_slot_matches(payload, slot, action, key, item, value)
                   for key, item in candidates.items()) != 1 for slot in slots):
            return None
        projected[field] = {}
    remaining = {key: item for key, item in pending.items() if key not in candidates}
    if _legacy_pending(value["book"]["identity_id"], identity={"yinluo_observation": projected, "pending_tasks": remaining}):
        return None
    projected.update(
        auto_calibrate_reason="旧阴罗操作已由原生终局回包核对，等待幡面板校准。",
        auto_next_time=0.0, auto_last_action="legacy_reconciled", auto_last_error="",
    )
    update.legacy_before = {"yinluo_observation": copy.deepcopy(observed), "pending_tasks": copy.deepcopy(pending)}
    update.legacy_pending_keys = tuple(candidates)
    value["hold"] = ""
    return projected


def reconcile_legacy_pending(identity_id):
    value, _reason = read_accounting(identity_id)
    ledger = cultivation.read_cultivation_ledger(identity_id)
    if value is None or ledger is None:
        return False
    update = _update(identity_id, value, ledger)
    observed = stage_legacy_completion(update)
    return observed is not None and commit_update(update, observations={identity_id: {"yinluo_observation": observed}})


def clear_completed_pending(identity_id):
    """Reconcile only durable native completion, without replay or fresh sends."""
    value, _reason = read_accounting(identity_id)
    ledger = cultivation.read_cultivation_ledger(identity_id)
    if value is None or ledger is None:
        return {}
    removals = _completed_resource_pending(value)
    if not removals or not commit_update(_update(identity_id, value, ledger)):
        return {}
    return removals


def commit_update(update, *, observations=None):
    """No await: failed SQLite commits restore only this transaction's fields."""
    identity_id, owner = update.identity_id, update.owner
    validated, _reason = _validated_accounting(update.value, identity_id, get_identity_account(identity_id))
    if (
        validated is None or not has_identity(identity_id) or get_identity_state(identity_id) is not owner
        or owner.get(STATE_KEY, {}) != update.previous
        or owner.get(cultivation.STATE_KEY, {}) != update.previous_cultivation
        or any(not isinstance(change, archive.ArchiveChange)
               or change.key[:2] != (identity_id, get_identity_account(identity_id))
               for change in update.archive_changes)
        or not _business_changes_valid(update)
    ):
        return False
    if update.business_changes:
        try:
            validated["business_archive"] = archive.business_manifest(
                _archive_connection(), identity_id, get_identity_account(identity_id), changes=update.business_changes,
            )
        except (archive.ArchiveConflict, sqlite3.Error):
            return False
    removed_pending = _expired_read_pending(validated, update.expired_reads, archive_changes=update.archive_changes)
    if removed_pending is None:
        return False
    removed_pending.update(_completed_resource_pending(validated, archive_changes=update.archive_changes))
    patches = dict(observations or {})
    if any(type(target) is not int or not has_identity(target) or not isinstance(fields, dict)
           or fields.keys() - {"yinluo_observation", "wanxin_observation"}
           or any(not isinstance(value, dict) for value in fields.values()) for target, fields in patches.items()):
        return False
    if update.legacy_before is not None:
        if any(not _same_legacy_snapshot(owner.get(key), expected) for key, expected in update.legacy_before.items()):
            return False
        proven = _completed_resource_pending({**validated, "hold": ""}, archive_changes=update.archive_changes)
        if any(key not in proven for key in update.legacy_pending_keys):
            return False
        prospective = {
            "yinluo_observation": patches.get(identity_id, {}).get("yinluo_observation"),
            "pending_tasks": {key: item for key, item in owner["pending_tasks"].items() if key not in update.legacy_pending_keys},
        }
        if _legacy_pending(identity_id, identity=prospective):
            return False
        removed_pending.update({key: proven[key] for key in update.legacy_pending_keys})
    before = {target: (get_identity_state(target), get_identity_account(target), {
        key: copy.deepcopy(get_identity_state(target).get(key)) for key in fields
    }) for target, fields in patches.items()}
    old_profile = get_send_as_profile(identity_id).get("xiuwei_current", 0)
    if cultivation.apply_cultivation_projection(identity_id, update.cultivation) is None:
        return False
    owner[STATE_KEY] = validated
    for key in removed_pending:
        owner["pending_tasks"].pop(key)
    for target, fields in patches.items():
        get_identity_state(target).update(copy.deepcopy(fields))
    save_error = None
    try:
        if update.business_changes:
            saved = persistence.save_state(yinluo_archive_changes=update.archive_changes, yinluo_business_changes=update.business_changes)
        else:
            saved = (persistence.save_state(yinluo_archive_changes=update.archive_changes)
                     if update.archive_changes else persistence.save_state())
    except Exception as exc:
        save_error, saved = exc, False
    if saved is not False:
        return True
    if has_identity(identity_id) and get_identity_state(identity_id) is owner and get_identity_account(identity_id) == update.value["book"]["account_id"]:
        owner[STATE_KEY] = update.previous
        owner[cultivation.STATE_KEY] = update.previous_cultivation
        update_send_as_profile(identity_id, xiuwei_current=old_profile)
        owner["pending_tasks"].update(removed_pending)
    for target, (target_owner, account, fields) in before.items():
        if has_identity(target) and get_identity_state(target) is target_owner and get_identity_account(target) == account:
            target_owner.update(fields)
    persistence.mark_dirty()
    if save_error is not None:
        raise save_error
    return False


def _withdraw_panel_fields(book, source, text):
    parsed = parse_yinluo_resource_panel(text)
    present = set()
    if parsed is not None:
        if parsed.sha is not None:
            present.add("sha")
        present.update(f"soul:{name}" for name, _amount in parsed.souls or ())
    for resource, ledger in book["ledgers"].items():
        baseline = ledger["baseline"]
        if baseline is None or resource in present:
            continue
        point = baseline["point"]
        if (
            (point["evidence"]["chat_id"], point["evidence"]["msg_id"]) == (source.chat_id, source.result_msg_id)
            and scalar.compare_points(source.end, point) in {0, 1}
        ):
            baseline["conflicted"] = True
            baseline["point"] = copy.deepcopy(source.end)


def stage_event(event, *, now):
    trust = event_trust(now)
    source = admit_yinluo_resource_source(event, **trust)
    if source is None:
        return None
    value, _reason = read_accounting(source.identity_id)
    ledger = cultivation.read_cultivation_ledger(source.identity_id)
    if value is None or ledger is None:
        return None
    reply = parse_yinluo_resource_reply(source.command.text, event.text)
    reply = qualify_yinluo_resource_reply(source, reply, identity_usernames())
    if reply is None:
        return None
    changes = []
    try:
        root = source.chat_id, source.command_msg_id
        if any(
            (_operation(item) == root and (item["command"] != source.command.text or item["start"] != source.start))
            or ((item["end"]["evidence"]["chat_id"], item["end"]["evidence"]["msg_id"]) == (source.chat_id, source.result_msg_id)
                and (_operation(item) != root or item["sender_id"] != source.result_sender_id))
            for item in value["book"]["receipts"]
        ):
            raise archive.ArchiveConflict("conflicting_yinluo_hot_result_owner")
        owners = archive.result_owners(_archive_connection(), source.chat_id, source.result_msg_id)
        if owners and owners != [(source.identity_id, source.account_id, source.command_msg_id, source.result_sender_id)]:
            raise archive.ArchiveConflict("conflicting_yinluo_archive_result_owner")
        stored = _read_archived_command(value["book"], source.chat_id, source.command_msg_id)
        if owners and stored is None:
            raise archive.ArchiveConflict("orphan_yinluo_archive_result")
        if stored is not None:
            if any(_operation(item) == (source.chat_id, source.command_msg_id) for item in value["book"]["receipts"]):
                raise archive.ArchiveConflict("duplicate_hot_and_archived_command")
            if source.command.action != "banner" and any(_reply_matches(item, source, reply) for item in stored[0]["receipts"]):
                if source.command.action in {"assist_banner", "assist_strip"}:
                    return _update(source.identity_id, value, ledger, source, reply)
                return _update(source.identity_id, value, ledger)
        _compact_value(value, ledger, changes, exclude=(source.chat_id, source.command_msg_id))
        if stored is not None:
            _restore_archived_command(value, source.chat_id, source.command_msg_id, changes, stored=stored)
        # A verified read can supply coverage before retiring full terminal
        # history. Its balance, receipt and retirement still commit together.
        if source.command.action == "banner" and reply.phase == "panel":
            book = stage_yinluo_panel(value["book"], source, event.text)
            if book is None:
                return None
            value["book"] = book
            _compact_value(value, ledger, changes, exclude=(source.chat_id, source.command_msg_id))
    except (archive.ArchiveConflict, sqlite3.Error):
        value, _reason = read_accounting(source.identity_id)
        value["hold"] = "receipt_conflict"
        return _update(source.identity_id, value, cultivation.read_cultivation_ledger(source.identity_id))
    projection = stage_yinluo_reply(value["book"], ledger, source, reply, identity_usernames=identity_usernames())
    if projection is None:
        return None
    value["book"] = projection.book
    if source.command.action == "banner" and source.edited:
        _withdraw_panel_fields(value["book"], source, event.text)
    _refresh_operations(value)
    return _update(source.identity_id, value, projection.cultivation, source, reply, archive_changes=changes)


def stage_unresolved_edits(event, *, now):
    if not isinstance(event, VerifiedGameEvent) or not event.is_edited_delivery:
        return []
    updates = []
    trust = event_trust(now)
    if event.sender_id not in trust["game_bots"] or event.chat_id not in trust["game_chats"]:
        return updates
    try:
        archived = archive.result_owners(_archive_connection(), event.chat_id, event.msg_id, event.sender_id)
    except sqlite3.Error:
        return updates
    for identity_id in get_identity_ids():
        archived_roots = [root for owner, account, root, _sender in archived
                          if owner == identity_id and account == get_identity_account(identity_id)]
        stored = get_identity_state(identity_id).get(STATE_KEY)
        book = stored.get("book") if isinstance(stored, dict) else None
        receipts = book.get("receipts") if isinstance(book, dict) else None
        hot_match = isinstance(receipts, list) and len(receipts) <= MAX_RECEIPTS and any(
            isinstance(receipt, dict) and isinstance(receipt.get("end"), dict)
            and isinstance(receipt["end"].get("evidence"), dict)
            and (receipt["end"]["evidence"].get("chat_id"), receipt["end"]["evidence"].get("msg_id")) == (event.chat_id, event.msg_id)
            for receipt in receipts
        )
        if not archived_roots and not hot_match:
            continue
        value, _reason = read_accounting(identity_id)
        ledger = cultivation.read_cultivation_ledger(identity_id)
        if value is None or ledger is None:
            continue
        changes = []
        try:
            root = archived_roots[0] if archived_roots else next(
                _operation(item)[1] for item in value["book"]["receipts"] if (
                    item["end"]["evidence"]["chat_id"], item["end"]["evidence"]["msg_id"]
                ) == (event.chat_id, event.msg_id)
            )
            _compact_value(value, ledger, changes, exclude=(event.chat_id, root))
            for root in archived_roots:
                if _restore_archived_command(value, event.chat_id, root, changes) is None:
                    raise archive.ArchiveConflict("orphan_yinluo_archive_result")
        except (archive.ArchiveConflict, sqlite3.Error):
            value, _reason = read_accounting(identity_id)
            value["hold"] = "receipt_conflict"
            updates.append(_update(identity_id, value, cultivation.read_cultivation_ledger(identity_id)))
            continue
        projection = stage_unresolved_yinluo_edit(value["book"], ledger, event, **trust)
        if projection is not None:
            value["book"] = projection.book
            for stored in value["book"]["ledgers"].values():
                baseline = stored["baseline"]
                if baseline is not None and (
                    baseline["point"]["evidence"]["chat_id"], baseline["point"]["evidence"]["msg_id"]
                ) == (event.chat_id, event.msg_id) and baseline["point"]["at"] <= event.server_event_at:
                    baseline["conflicted"] = True
            _refresh_operations(value)
            updates.append(_update(identity_id, value, projection.cultivation, archive_changes=changes))
    return updates


def prepare_operation(identity_id, command, chat_id, now, *, source_module, beneficiary=None):
    reason = admission_reason(identity_id, command)
    if reason or type(chat_id) is not int or chat_id not in get_game_group_ids() or timestamp(now) <= 0:
        return None, reason or "invalid_dispatch"
    value, _reason = read_accounting(identity_id)
    ledger = cultivation.read_cultivation_ledger(identity_id)
    if ledger is None:
        return None, "unverified_cultivation"
    expired = tuple(item["op_id"] for item in value["operations"] if item["phase"] == "read_expired")
    pending = _expired_read_pending(value, expired)
    if pending is None or pending:
        return None, "yinluo_read_cleanup_pending"
    changes = []
    try:
        _retire_expired_reads(value, changes, expired)
        value["operations"] = [item for item in value["operations"] if item["phase"] not in {"unsent", "read_expired"}]
        _compact_value(value, ledger, changes)
    except (archive.ArchiveConflict, sqlite3.Error):
        return None, "archive_conflict"
    is_read = parse_yinluo_resource_command(command).action == "banner"
    # Reserve one operation slot for native calibration, not another mutation.
    if (len(value["operations"]) >= MAX_OPERATIONS - (0 if is_read else 1)
            or len(value["book"]["receipts"]) >= MAX_RECEIPTS - (0 if is_read else 1)):
        return None, "capacity"
    record = {
        "op_id": uuid4().hex, "command": parse_yinluo_resource_command(command).text,
        "chat_id": chat_id, "started_at": float(now), "msg_id": 0, "sent_at": 0.0,
        "phase": "prepared", "budget": reservation_budget(command), "source_module": source_module,
        "beneficiary": copy.deepcopy(beneficiary) if beneficiary is not None else {},
    }
    if not _valid_operation(record):
        return None, "invalid_operation"
    value["operations"].append(record)
    if not commit_update(_update(identity_id, value, ledger, archive_changes=changes)):
        return None, "persistence_failed"
    return copy.deepcopy(record), ""


def _retained_receipt_operation(value, op_id, chat_id, msg_id):
    if type(chat_id) is not int or not chat_id or type(msg_id) is not int or msg_id <= 0:
        return None
    try:
        _receipts, operations = _command_records(value, chat_id, msg_id)
    except (archive.ArchiveConflict, sqlite3.Error):
        return None
    if len(operations) != 1 or operations[0]["op_id"] != op_id or operations[0]["phase"] not in {"complete", "read_expired"}:
        return None
    return operations[0]


def current_operation(identity_id, op_id, *, chat_id=0, msg_id=0):
    """Optional receipt keys locate retained ownership without restoring history."""
    value, _reason = read_accounting(identity_id)
    if value is None:
        return None
    record = next((item for item in value["operations"] if item["op_id"] == op_id), None)
    return record or _retained_receipt_operation(value, op_id, chat_id, msg_id)


def expire_unanswered_reads(identity_id, *, now, timeout, active_ops=()):
    """Only read-only banner queries may expire without an action receipt."""
    if (timestamp(now) <= 0 or type(timeout) not in {int, float} or timestamp(timeout) != timeout or timeout < 60
            or not isinstance(active_ops, (tuple, list, set, frozenset))
            or any(not isinstance(op_id, str) for op_id in active_ops)):
        return False
    value, _reason = read_accounting(identity_id)
    if value is None:
        return False
    for operation in value["operations"]:
        if (operation["command"] == ".我的阴罗幡" and not operation["msg_id"]
                and operation["op_id"] not in active_ops
                and operation["phase"] in LIVE_PHASES | {"read_expired"}):
            adopt_pending_receipt(identity_id, operation["op_id"])
    value, _reason = read_accounting(identity_id)
    ledger = cultivation.read_cultivation_ledger(identity_id)
    if value is None or ledger is None:
        return False
    expired = []
    for operation in value["operations"]:
        if (operation["phase"] in LIVE_PHASES | {"read_expired"} and operation["command"] == ".我的阴罗幡"
                and operation["op_id"] not in active_ops
                and (operation["sent_at"] or operation["started_at"]) + timeout <= now):
            operation["phase"] = "read_expired"
            expired.append(operation["op_id"])
    if not expired:
        return False
    changes = []
    try:
        _retire_expired_reads(value, changes, expired)
    except (archive.ArchiveConflict, sqlite3.Error):
        return False
    update = _update(identity_id, value, ledger, expired_reads=expired, archive_changes=changes)
    pending = _expired_read_pending(value, update.expired_reads, archive_changes=changes)
    if pending is None or (not pending and update.value == update.previous):
        return False
    return commit_update(update)


def record_transport(identity_id, op_id, *, phase, msg_id=0, chat_id=0, sent_at=0):
    value, _reason = read_accounting(identity_id)
    ledger = cultivation.read_cultivation_ledger(identity_id)
    if value is None or ledger is None or phase not in {"sent", "unsent", "unknown"}:
        return False
    record = next((item for item in value["operations"] if item["op_id"] == op_id), None)
    if record is None and phase == "sent":
        record = _retained_receipt_operation(value, op_id, chat_id, msg_id)
    if record is not None and record["phase"] == "complete":
        # A native result may commit while the transport caller is still awaiting.
        return bool(
            phase == "sent" and type(msg_id) is int and msg_id == record["msg_id"]
            and type(chat_id) is int and chat_id == record["chat_id"]
            and type(sent_at) in {int, float} and sent_at == record["sent_at"]
        )
    expired_read = bool(record and record["phase"] == "read_expired" and record["command"] == ".我的阴罗幡" and phase == "sent")
    if record is None or (record["phase"] not in LIVE_PHASES and not expired_read):
        return False
    if phase == "sent":
        if (
            type(msg_id) is not int or msg_id <= 0 or type(chat_id) is not int or chat_id != record["chat_id"]
            or type(sent_at) not in {int, float} or timestamp(sent_at) != sent_at
            or timestamp(sent_at) < record["started_at"] - 1
            or (record["msg_id"] and (record["msg_id"] != msg_id or record["sent_at"] != sent_at))
            or any(item["op_id"] != op_id and (item["chat_id"], item["msg_id"]) == (chat_id, msg_id) for item in value["operations"])
        ):
            return False
        if expired_read and record["msg_id"]:
            return True
        record.update(msg_id=msg_id, sent_at=sent_at)
    elif phase == "unsent" and record["msg_id"]:
        return False
    record["phase"] = phase
    _refresh_operations(value)
    update = _update(identity_id, value, ledger)
    if update.value == update.previous and update.cultivation == cultivation.read_cultivation_ledger(identity_id):
        return True
    return commit_update(update)


def adopt_pending_receipt(identity_id, op_id):
    record = current_operation(identity_id, op_id)
    if not record or record["msg_id"]:
        return False
    pending_tasks = get_identity_state(identity_id).get("pending_tasks")
    if not isinstance(pending_tasks, dict):
        return False
    account_id = get_identity_account(identity_id)
    matches = []
    for key, pending in pending_tasks.items():
        if not isinstance(pending, dict) or any(pending.get(field) != record[field] for field in ("op_id", "source_module")) or pending.get("cmd") != record["command"]:
            continue
        try:
            chat_id, msg_id = message_key_parts(key, pending)
        except (TypeError, ValueError, OverflowError):
            continue
        # Runtime receipts predate account_id metadata. The exact operation UUID
        # belongs to this account-bound book; optional contradictory metadata
        # must still be rejected. This is transport evidence, never a debit.
        if (type(pending.get("account_id", account_id)) is not int
                or pending.get("account_id", account_id) != account_id
                or any(type(pending.get(field, identity_id)) is not int or pending.get(field, identity_id) != identity_id
                       for field in ("send_as_id", "identity_id"))
                or chat_id != record["chat_id"]):
            continue
        matches.append((chat_id, msg_id, pending.get("sent_at")))
    if len(matches) != 1:
        return False
    chat_id, msg_id, sent_at = matches[0]
    return record_transport(identity_id, op_id, phase="sent", chat_id=chat_id, msg_id=msg_id, sent_at=sent_at)
