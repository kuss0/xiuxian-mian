"""Atomic projection of returned treasure facts, without replaying gameplay."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
import uuid

from ..inventory_delta import (
    INVENTORY_DELTA_STATUS_PENDING, _prune_delta_records, _record_key,
    normalize_inventory_items,
)
from ..miniapp_state import _prune_state_records, sanitize_miniapp_state
from ..persistence import _bounded_miniapp_operation, mark_dirty, save_state
from ..state import (
    TREASURE_RESULT_MAX_BYTES, get_identity_ids, get_inventory_delta_records,
    get_miniapp_state_records, set_inventory_delta_records, set_miniapp_state_records,
)
from ..webapp_core import sanitize_webapp_secret_text
from .miniapp_common import MiniAppFlowCancelled, MiniAppIdentityOwner
from .treasure_receipts import treasure_integer, treasure_quota_exhausted


STATE_KEY = "treasure_result"
_SOURCE = "cave_treasure_miniapp"


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _same(left, right):
    try:
        return _encoded(left) == _encoded(right)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _basis(records, key):
    try:
        return hashlib.sha256(_encoded([key in records, records.get(key)]).encode()).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError):
        return ""


def _empty(value):
    return type(value) is dict and not value


def _key(value, size=64):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(size) + "}", value) is not None


def _time(value):
    return type(value) in (int, float) and 0 < value < 1e12


def _counts(value):
    return isinstance(value, dict) and all(
        isinstance(name, str) and bool(name.strip()) and type(amount) is int
        and treasure_integer(amount) not in (None, 0) for name, amount in value.items()
    )


def _secret_free(value):
    values = [value]
    while values:
        item = values.pop()
        if isinstance(item, dict):
            values.extend(item.keys())
            values.extend(item.values())
        elif isinstance(item, list):
            values.extend(item)
        elif isinstance(item, str) and sanitize_webapp_secret_text(item, limit=len(item) + 1) != item:
            return False
    return True


def _valid_projection(value, *, identity_id, account_id, inventory):
    if _empty(value):
        return True
    if (not isinstance(value, dict) or set(value) != {"key", "before", "record"}
            or not _key(value["before"]) or not isinstance(value["record"], dict)):
        return False
    row = value["record"]
    if (type(row.get("identity_id")) is not int or row["identity_id"] != identity_id
            or row.get("source") != _SOURCE or not _time(row.get("updated_at"))
            or not isinstance(row.get("updated_at_text"), str)
            or not isinstance(row.get("source_id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", row["source_id"])):
        return False
    if inventory:
        summary = row.get("source_summary")
        return (
            set(row) == {"identity_id", "source", "source_id", "status", "items",
                         "updated_at", "updated_at_text", "source_summary"}
            and value["key"] == _record_key(identity_id, _SOURCE, row["source_id"])
            and row["status"] == INVENTORY_DELTA_STATUS_PENDING
            and bool(row["items"]) and _counts(row["items"])
            and _same(normalize_inventory_items(row["items"]), row["items"])
            and isinstance(summary, dict)
            and {"settled_count", "result_msg_id"} <= set(summary) <= {"status", "settled_count", "result_msg_id"}
            and all(type(summary[key]) is int and treasure_integer(summary[key]) is not None
                    for key in ("settled_count", "result_msg_id"))
            and summary["settled_count"] > 0
            and ("status" not in summary or isinstance(summary["status"], str))
        )
    return (
        set(row) == {"identity_id", "game_key", "source", "source_id", "state", "outputs",
                     "replaces_commands", "updated_at", "updated_at_text"}
        and value["key"] == f"{identity_id}:cave_treasure" and row["game_key"] == "cave_treasure"
        and isinstance(row["state"], dict) and bool(row["state"])
        and type(row["state"].get("owner_account_id")) is int
        and row["state"]["owner_account_id"] == (account_id or identity_id)
        and _same(sanitize_miniapp_state(row["state"]), row["state"])
        and row["outputs"] == ["module_snapshot", "daily_counter", "inventory_delta"]
        and row["replaces_commands"] == [".\u6d1e\u5e9c"]
    )


def valid_record(value):
    fields = {"version", "operation_id", "identity_id", "account_id", "created_at", "phase",
              "inventory", "miniapp", "response"}
    if isinstance(value, dict) and value.get("version") == 2:
        fields.add("operation_checkpoint")
    if (not isinstance(value, dict) or set(value) != fields or not _bounded_miniapp_operation(value)
            or type(value["version"]) is not int or value["version"] not in (1, 2)
            or (value["version"] == 2 and not _key(value["operation_checkpoint"]))
            or not _key(value["operation_id"], 32)
            or type(value["identity_id"]) is not int or value["identity_id"] <= 0
            or type(value["account_id"]) is not int or value["account_id"] < 0
            or not _time(value["created_at"]) or value["phase"] not in ("pending", "complete")):
        return False
    for name in ("inventory", "miniapp"):
        if not _valid_projection(value[name], identity_id=value["identity_id"],
                                 account_id=value["account_id"], inventory=name == "inventory"):
            return False
    if value["version"] == 2 and value["inventory"] and (
        value["inventory"]["record"]["source_id"] != "operation:" + value["operation_id"]
    ):
        return False
    response = value["response"]
    if (not isinstance(response, dict) or set(response) != {"ok", "message", "extra"}
            or type(response["ok"]) is not bool or not isinstance(response["message"], str)
            or not response["message"] or len(response["message"]) > 4096
            or not isinstance(response["extra"], dict)):
        return False
    extra = response["extra"]
    required = {"inventory_record_key", "state_record_key", "status", "outcome_unknown", "games_used",
                "games_limit", "settled_count", "gains", "rewards", "daily_exhausted", "operation_cancelled"}
    if (not required <= set(extra) <= required | {"retry_after_sec", "shared_rate_limit", "shared_retry_after_sec"}
            or any(type(extra[key]) is not bool for key in ("outcome_unknown", "daily_exhausted", "operation_cancelled"))
            or any(type(extra[key]) is not int or treasure_integer(extra[key]) is None
                   for key in ("games_used", "games_limit", "settled_count"))
            or not isinstance(extra["status"], str) or len(extra["status"]) > 240
            or any(not _counts(extra[key]) for key in ("gains", "rewards"))
            or extra["inventory_record_key"] != value["inventory"].get("key", "")
            or extra["state_record_key"] != value["miniapp"].get("key", "")
            or any(not _time(extra[key]) for key in ("retry_after_sec", "shared_retry_after_sec") if key in extra)
            or ("shared_rate_limit" in extra and extra["shared_rate_limit"] is not True)
            or (extra["outcome_unknown"] and (response["ok"] or extra["daily_exhausted"]))):
        return False
    if extra["daily_exhausted"] and (not response["ok"] or not treasure_quota_exhausted(
            value["miniapp"].get("record", {}).get("state", {}))):
        return False
    if extra["outcome_unknown"] != bool(value["miniapp"].get("record", {}).get("state", {}).get("outcome_unknown")):
        return False
    items = normalize_inventory_items(extra["rewards"])
    if extra["gains"].get("\u7075\u77f3"):
        items["\u7075\u77f3"] = items.get("\u7075\u77f3", 0) + extra["gains"]["\u7075\u77f3"]
    if not _same(value["inventory"].get("record", {}).get("items", {}), items):
        return False
    if value["inventory"] and value["inventory"]["record"]["source_summary"]["settled_count"] != extra["settled_count"]:
        return False
    try:
        encoded = _encoded(value)
        return (len(encoded.encode()) <= TREASURE_RESULT_MAX_BYTES
                and _secret_free(value))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _owned(owner, record):
    return (owner is not None and owner.is_current() and valid_record(record)
            and record["identity_id"] == owner.identity_id and record["account_id"] == owner.account_id)


def pending(identity):
    record = identity.get(STATE_KEY, {})
    return not _empty(record) and (not valid_record(record) or record["phase"] == "pending")


def hold_reason(identity_id):
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None:
        return "owner_changed"
    record = owner.identity.get(STATE_KEY, {})
    if not _empty(record):
        if not valid_record(record):
            return "invalid_projection"
        if not _owned(owner, record):
            return "owner_changed"
        if not _journal_current(owner, record):
            return "operation_checkpoint_changed"
        if record["phase"] == "pending":
            return "persistence_pending"
        if record["response"]["extra"]["outcome_unknown"]:
            return "outcome_unknown_hold"
    return account_hold_reason(owner)


def account_hold_reason(owner):
    identity_id = owner.identity_id
    # Treasure quotas belong to the login account, including disabled roles.
    account_id = owner.account_id or owner.identity_id
    for other_id in get_identity_ids():
        if other_id == identity_id:
            continue
        other = MiniAppIdentityOwner.capture(other_id)
        value = other.identity.get(STATE_KEY, {})
        if _empty(value) or (valid_record(value) and value["phase"] == "complete"
                             and not value["response"]["extra"]["outcome_unknown"] and _journal_current(other, value)):
            continue
        original_account = value.get("account_id") if isinstance(value, dict) else None
        if original_account == 0 and isinstance(value, dict) and type(value.get("identity_id")) is int:
            original_account = value["identity_id"]
        if ((other.account_id or other.identity_id) == account_id
                or type(original_account) is int and original_account > 0 and original_account == account_id):
            return "account_result_pending"
    return ""


def admission_allowed(owner):
    return owner is not None and owner.is_current() and not hold_reason(owner.identity_id)


def held_response(reason="persistence_pending"):
    return {
        "ok": False, "message": "\u6d1e\u5e9c\u5bfb\u5b9d\u7ed3\u679c\u5f85\u672c\u5730\u6838\u5b9e\u5165\u8d26\uff0c\u672a\u542f\u52a8\u65b0\u4e00\u8f6e",
        "extra": {"status": "persistence_pending", "reason": reason, "persistence_only": True,
                  "daily_exhausted": False},
    }


def _bases_current(record):
    return all(not record[name] or record[name]["before"] == _basis(getter(), record[name]["key"])
               for name, getter in (("inventory", get_inventory_delta_records), ("miniapp", get_miniapp_state_records)))


def recovery_due(identity_id):
    owner = MiniAppIdentityOwner.capture(identity_id)
    record = owner.identity.get(STATE_KEY, {}) if owner else {}
    return _owned(owner, record) and record["phase"] == "pending" and _bases_current(record) and _journal_current(owner, record)


def _journal_current(owner, record):
    if record["version"] == 1:
        return True
    from .treasure_operations import result_matches
    return result_matches(owner, record)


def _restore_changes(before, staged, current):
    restored = dict(current)
    for key in before.keys() | staged.keys():
        if _basis(before, key) == _basis(staged, key) or _basis(current, key) != _basis(staged, key):
            continue
        if key in before:
            restored[key] = before[key]
        else:
            restored.pop(key, None)
    return restored


def _commit(owner, record):
    if not _owned(owner, record) or not _same(record, owner.identity.get(STATE_KEY, {})):
        return held_response("owner_changed")
    if not _bases_current(record):
        return held_response("result_basis_changed")
    if not _journal_current(owner, record):
        return held_response("operation_checkpoint_changed")
    staged_roots = []
    complete = {**deepcopy(record), "phase": "complete"}
    cancelled = False
    try:
        for name, getter, setter, prune in (
            ("inventory", get_inventory_delta_records, set_inventory_delta_records, _prune_delta_records),
            ("miniapp", get_miniapp_state_records, set_miniapp_state_records, _prune_state_records),
        ):
            projection = record[name]
            if not projection:
                continue
            before = deepcopy(getter())
            if _same(before.get(projection["key"]), projection["record"]):
                continue
            staged = prune({**before, projection["key"]: deepcopy(projection["record"])})
            if not _same(staged.get(projection["key"]), projection["record"]):
                raise ValueError("projection_not_retained")
            staged_roots.append((before, deepcopy(staged), getter, setter))
            setter(staged)
        if (not _owned(owner, record) or not _same(owner.identity.get(STATE_KEY, {}), record)
                or not _journal_current(owner, record)):
            raise ValueError("projection_owner_changed")
        owner.identity[STATE_KEY] = complete
        mark_dirty()
        saved = save_state() is True
    except asyncio.CancelledError:
        cancelled, saved = True, False
    except Exception:
        saved = False
    if not saved:
        for before, staged, getter, setter in reversed(staged_roots):
            setter(_restore_changes(before, staged, getter()))
        if _same(owner.identity.get(STATE_KEY, {}), complete):
            owner.identity[STATE_KEY] = deepcopy(record)
        # A replacement may have copied our staged marker during the failed
        # save. Demote that exact operation only, leaving its other state alone.
        current = MiniAppIdentityOwner.capture(owner.identity_id)
        if current is not None and _same(current.identity.get(STATE_KEY, {}), complete):
            current.identity[STATE_KEY] = deepcopy(record)
        mark_dirty()
        response = held_response()
        if cancelled:
            raise MiniAppFlowCancelled(response) from None
        return response
    if (not owner.is_current() or not _same(owner.identity.get(STATE_KEY, {}), complete)
            or not _journal_current(owner, record)):
        return held_response("owner_changed")
    return deepcopy(record["response"])


def recover_local(identity_id):
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None:
        return None
    record = owner.identity.get(STATE_KEY, {})
    if _owned(owner, record) and record["phase"] == "pending":
        response = _commit(owner, record)
        if not response["extra"].get("persistence_only"):
            response["extra"].update(status="result_committed", persistence_only=True,
                                     persistence_saved=True, daily_exhausted=False)
        return response
    reason = hold_reason(identity_id)
    return held_response(reason) if reason else None


@dataclass(frozen=True)
class ResultProjection:
    owner: MiniAppIdentityOwner
    previous: object
    inventory_bases: dict
    miniapp_basis: str

    @classmethod
    def capture(cls, owner, *, resume=False):
        from .treasure_operations import resume_allowed
        if not (resume_allowed(owner.identity_id) if resume and owner is not None else admission_allowed(owner)):
            raise ValueError("treasure_previous_result_pending")
        records = get_inventory_delta_records()
        return cls(owner, deepcopy(owner.identity.get(STATE_KEY, {})),
                   {key: _basis(records, key) for key in records},
                   _basis(get_miniapp_state_records(), f"{owner.identity_id}:cave_treasure"))

    def is_current(self):
        return self.owner.is_current() and _same(self.previous, self.owner.identity.get(STATE_KEY, {}))

    def invalidate(self):
        if self.is_current():
            self.owner.identity[STATE_KEY] = {
                "invalid": True, "identity_id": self.owner.identity_id, "account_id": self.owner.account_id,
            }
            mark_dirty()
        return held_response("invalid_projection")

    def apply(self, inventory, miniapp, response, *, now, operation_record=None):
        if not self.is_current():
            return held_response("owner_changed")
        try:
            projections = {}
            for name, value in (("inventory", inventory), ("miniapp", miniapp)):
                key = value["record_key"]
                projections[name] = ({"key": key, "record": deepcopy(value["record"]), "before": (
                    self.inventory_bases.get(key, _basis({}, key)) if name == "inventory" else self.miniapp_basis
                )} if key else {})
            response = deepcopy(response)
            response["message"] = sanitize_webapp_secret_text(response["message"], limit=4096)
            response["extra"]["status"] = sanitize_webapp_secret_text(response["extra"]["status"], limit=240)
            record = {"version": 1, "operation_id": uuid.uuid4().hex, "identity_id": self.owner.identity_id,
                      "account_id": self.owner.account_id, "created_at": now, "phase": "pending",
                      **projections, "response": response}
            if operation_record is not None:
                from .treasure_operations import digest
                record.update(version=2, operation_id=operation_record["operation_id"],
                              operation_checkpoint=digest(operation_record["checkpoint"]))
            valid = valid_record(record)
        except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
            valid = False
        if not valid:
            return self.invalidate()
        if operation_record is None:
            from . import treasure_operations
            if not _empty(self.owner.identity.get(treasure_operations.STATE_KEY, {})):
                reason = treasure_operations.hold_reason(self.owner.identity_id)
                if reason:
                    return held_response(reason)
                extra = response["extra"]
                if response["ok"] or extra["outcome_unknown"] or extra["settled_count"] or extra["rewards"] or extra["gains"]:
                    return self.invalidate()
                # A failed read cannot replace the receipt paired with the last journal.
                extra.update(inventory_record_key="", state_record_key="")
                return response
        self.owner.identity[STATE_KEY] = record
        mark_dirty()
        return _commit(self.owner, record)
