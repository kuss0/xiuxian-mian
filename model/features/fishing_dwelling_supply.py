"""Native supply planning and receipts, isolated from cast/session recovery."""

from copy import deepcopy
import json
import re
import time
import uuid

from . import fishing_dwelling_protocol as p


STATE_KEY = "fishing_native_supply"
MAX_BYTES = 64 * 1024


def parse_shop(payload):
    facts = p.parse_context(payload)
    raw = p._mapping(payload["context"].get("shop"), "fishing_shop")
    if type(raw.get("castActive")) is not bool:
        raise p.ProtocolError("invalid_shop_cast_active")
    resources = {}

    def inventory(item_id, name, count):
        previous = resources.setdefault(item_id, {"name": name, "count": count})
        if previous != {"name": name, "count": count}:
            raise p.ProtocolError("inconsistent_shop_inventory")

    for bait in facts["baits"]:
        inventory(bait["itemId"], bait["name"], bait["count"])

    def rows(key):
        value = raw.get(key)
        if not isinstance(value, list) or len(value) > 100:
            raise p.ProtocolError("invalid_shop_" + key)
        return value

    def costs(row):
        value = row.get("cost")
        if not isinstance(value, list) or not 1 <= len(value) <= 16:
            raise p.ProtocolError("invalid_shop_cost")
        result = []
        for item in value:
            p._mapping(item, "shop_cost")
            cost = {"itemId": p._identifier(item.get("itemId"), "cost_id"),
                    "name": p._text(item.get("name"), "cost_name"),
                    "qty": p._number(item.get("qty"), "cost_quantity", 1, 10**9, integer=True),
                    "owned": p._number(item.get("owned"), "cost_owned", 0, 2**53 - 1, integer=True)}
            inventory(cost["itemId"], cost["name"], cost["owned"])
            result.append(cost)
        if len({cost["itemId"] for cost in result}) != len(result):
            raise p.ProtocolError("duplicate_shop_cost")
        return result

    baits = []
    for row in rows("baits"):
        p._mapping(row, "shop_bait")
        item = next((b for b in facts["baits"] if b["itemId"] == row.get("itemId")), None)
        if item is None or any(row.get(key) != item[key] or type(row.get(key)) is not type(item[key]) for key in item):
            raise p.ProtocolError("inconsistent_shop_bait")
        baits.append({**item, "cost": costs(row)})
    if len(baits) != len(facts["baits"]) or len({b["itemId"] for b in baits}) != len(baits):
        raise p.ProtocolError("incomplete_shop_baits")
    chums = []
    for row in rows("chums"):
        p._mapping(row, "shop_chum")
        item = {"key": p._identifier(row.get("key"), "chum_key"),
                "name": p._text(row.get("name"), "chum_name"), "cost": costs(row)}
        for key in ("usedToday", "remainingToday", "dailyLimit"):
            item[key] = p._number(row.get(key), key, 0, 1000, integer=True)
        if item["usedToday"] + item["remainingToday"] != item["dailyLimit"]:
            raise p.ProtocolError("inconsistent_chum_quota")
        affordable = all(cost["owned"] >= cost["qty"] for cost in item["cost"])
        if type(row.get("affordable")) is not bool or row["affordable"] != affordable:
            raise p.ProtocolError("inconsistent_chum_affordable")
        item["affordable"] = affordable
        chums.append(item)
    if (len({c["key"] for c in chums}) != len(chums) or len({c["name"] for c in chums}) != len(chums)
            or len({b["name"] for b in baits}) != len(baits)):
        raise p.ProtocolError("duplicate_shop_item")
    if "activeChum" not in raw:
        raise p.ProtocolError("missing_active_chum")
    active = raw["activeChum"]
    if active is not None:
        p._mapping(active, "active_chum")
        active = {"name": p._text(active.get("name"), "active_chum_name"),
                  "remaining": p._number(active.get("remaining"), "active_chum_remaining", 1, 1000, integer=True)}
    if len({r["name"] for r in resources.values()}) != len(resources):
        raise p.ProtocolError("ambiguous_shop_resource_names")
    return {"baits": baits, "chums": chums, "activeChum": active,
            "castActive": raw["castActive"], "resources": resources}


def idle_context(payload):
    if "session" not in payload:
        raise p.ProtocolError("native_existing_session")
    remote = payload["session"]
    if remote is not None:
        if (not isinstance(remote, dict) or remote.get("status") in ("active", "settling")
                or p.parse_owned_session(payload, session_id=remote.get("sessionId"),
                                         site_id=remote.get("siteId"))["phase"] != "settled"):
            raise p.ProtocolError("native_existing_session")


def plan_supply(payload, *, bait_choice, auto_buy=False, buy_count=20, chum_names=()):
    """Return one action from the user's plan, using live costs and daily limits."""
    facts = p.parse_context(payload)
    idle_context(payload)
    bait = next((b for b in facts["baits"] if bait_choice in (b["name"], b["itemId"])), None)
    if bait is None:
        raise p.ProtocolError("fishing_bait_unavailable")
    reason = p.cast_block_reason(facts, bait["itemId"])
    if reason and reason != "fishing_bait_missing":
        raise p.ProtocolError(reason)
    if facts["rod"] is None or facts["quota"]["remaining"] == 0 or not bait["unlocked"]:
        raise p.ProtocolError("fishing_supply_ineligible")
    if type(auto_buy) is not bool:
        raise p.ProtocolError("invalid_supply_setting")
    p._number(buy_count, "supply_buy_count", 1, 99, integer=True)
    if not isinstance(chum_names, (tuple, list)) or any(not isinstance(name, str) for name in chum_names):
        raise p.ProtocolError("invalid_supply_chums")
    shop = parse_shop(payload)
    if shop["castActive"]:
        raise p.ProtocolError("native_existing_session")
    target = None
    if not shop["activeChum"]:
        for name in chum_names:
            row = next((c for c in shop["chums"] if c["name"] == name), None)
            if row is None:
                raise p.ProtocolError("fishing_chum_unavailable")
            if row["remainingToday"]:
                target = row
                break
    requirements = {bait["itemId"]: 1}
    if target:
        for cost in target["cost"]:
            if any(b["itemId"] == cost["itemId"] for b in shop["baits"]):
                requirements[cost["itemId"]] = requirements.get(cost["itemId"], 0) + cost["qty"]
            elif cost["owned"] < cost["qty"]:
                raise p.ProtocolError("fishing_shop_cost_missing")
    purchases = []
    reserved = {c["itemId"]: c["qty"] for c in target["cost"] if c["itemId"] not in requirements} if target else {}
    for item_id, needed in requirements.items():
        row = next(b for b in shop["baits"] if b["itemId"] == item_id)
        if row["count"] >= needed:
            continue
        if not auto_buy:
            raise p.ProtocolError("fishing_bait_missing")
        quantity = max(buy_count, needed - row["count"])
        if quantity > 99 or not row["unlocked"]:
            raise p.ProtocolError("fishing_supply_ineligible")
        purchases.append({"baitItemId": item_id, "quantity": quantity})
        for cost in row["cost"]:
            reserved[cost["itemId"]] = reserved.get(cost["itemId"], 0) + cost["qty"] * quantity
    if any(shop["resources"][item_id]["count"] < cost for item_id, cost in reserved.items()):
        raise p.ProtocolError("fishing_shop_cost_missing")
    if purchases:
        return "buy-bait", purchases[0]
    if target:
        if not target["affordable"]:
            raise p.ProtocolError("fishing_shop_cost_missing")
        return "chum", {"chumKey": target["key"]}
    return "", {}


def supply_deltas(shop, action, payload):
    if action == "buy-bait":
        if set(payload) != {"baitItemId", "quantity"}:
            raise p.ProtocolError("invalid_supply_payload")
        count = p._number(payload["quantity"], "buy_quantity", 1, 99, integer=True)
        row = next((b for b in shop["baits"] if b["itemId"] == payload["baitItemId"]), None)
        if row is None or not row["unlocked"]:
            raise p.ProtocolError("invalid_supply_bait")
        deltas = {row["itemId"]: count}
    elif action == "chum":
        if set(payload) != {"chumKey"}:
            raise p.ProtocolError("invalid_supply_payload")
        row = next((c for c in shop["chums"] if c["key"] == payload["chumKey"]), None)
        if row is None or not row["remainingToday"] or shop["activeChum"]:
            raise p.ProtocolError("invalid_supply_chum")
        deltas, count = {}, 1
    else:
        raise p.ProtocolError("invalid_supply_action")
    for cost in row["cost"]:
        if cost["owned"] < cost["qty"] * count:
            raise p.ProtocolError("fishing_shop_cost_missing")
        deltas[cost["itemId"]] = deltas.get(cost["itemId"], 0) - cost["qty"] * count
    return deltas


def validate(record):
    keys = {"version", "identity_id", "account_id", "player_id", "operation_id", "site_id", "model_id",
            "revision", "phase", "action", "payload", "before", "after", "basis", "created_at"}
    if not isinstance(record, dict) or set(record) != keys or type(record["version"]) is not int or record["version"] != 1:
        raise p.ProtocolError("invalid_supply_record")
    for key in ("identity_id", "account_id"):
        p._number(record[key], key, 1, 2**53 - 1, integer=True)
    p._number(record["player_id"], "player_id", -(2**53 - 1), 2**53 - 1, integer=True)
    if not record["player_id"]:
        raise p.ProtocolError("invalid_supply_player")
    p._identifier(record["operation_id"], "supply_operation_id")
    p.placement(record["site_id"], record["model_id"])
    p._number(record["revision"], "supply_revision", 1, 100000, integer=True)
    p._number(record["created_at"], "supply_created", 1, 10**12)
    p._text(record["basis"], "supply_basis", empty=True)
    if record["basis"] and not re.fullmatch(r"[a-f0-9]{64}", record["basis"]):
        raise p.ProtocolError("invalid_supply_basis")
    if record["phase"] not in ("pending", "confirmed", "accounted"):
        raise p.ProtocolError("invalid_supply_phase")
    before = parse_shop(record["before"])
    if record["before"] != receipt_context(record["before"]):
        raise p.ProtocolError("invalid_supply_receipt_fields")
    deltas = supply_deltas(before, record["action"], record["payload"])
    if record["phase"] == "pending":
        if record["after"] is not None:
            raise p.ProtocolError("premature_supply_result")
    else:
        after = parse_shop(record["after"])
        if (record["after"] != receipt_context(record["after"])
                or record["before"]["context"]["quota"] != record["after"]["context"]["quota"]):
            raise p.ProtocolError("supply_context_changed")
        for item_id, delta in deltas.items():
            if after["resources"].get(item_id) != {**before["resources"][item_id],
                                                   "count": before["resources"][item_id]["count"] + delta}:
                raise p.ProtocolError("supply_delta_unconfirmed")
        if record["action"] == "chum":
            old = next(c for c in before["chums"] if c["key"] == record["payload"]["chumKey"])
            new = next((c for c in after["chums"] if c["key"] == old["key"]), None)
            if (new is None or new["usedToday"] != old["usedToday"] + 1
                    or new["remainingToday"] != old["remainingToday"] - 1 or new["name"] != old["name"]
                    or not after["activeChum"] or after["activeChum"]["name"] != old["name"]):
                raise p.ProtocolError("supply_chum_unconfirmed")
    if len(json.dumps(record, ensure_ascii=True, allow_nan=False).encode()) > MAX_BYTES:
        raise p.ProtocolError("supply_record_too_large")
    return record


def receipt_context(payload):
    """Keep only validated, bounded facts needed for an exact supply receipt."""
    facts, shop = p.parse_context(payload), parse_shop(payload)
    return {"ok": True, "context": {**facts, "conflict": {} if facts["conflict"] else None,
                                     "shop": {k: v for k, v in shop.items() if k != "resources"}}}


class SupplyJournal:
    def __init__(self, *, owner, read_current, compare_and_save, is_owner_current):
        self.owner = tuple(owner)
        self.read_current, self.compare_and_save, self.is_owner_current = read_current, compare_and_save, is_owner_current
        self.failed, self.previous = False, None
        self.record = deepcopy(read_current())
        self._check()

    def _check(self):
        if self.record != {}:
            validate(self.record)
            if tuple(self.record[k] for k in ("identity_id", "account_id", "player_id")) != self.owner:
                raise p.ProtocolError("supply_owner_mismatch")
        if self.failed or self.is_owner_current() is not True or self.read_current() != self.record:
            raise p.ProtocolError("supply_owner_changed")

    def _commit(self, updated):
        self._check()
        if updated:
            validate(updated)
        try:
            if self.compare_and_save(deepcopy(self.record), deepcopy(updated)) is not True:
                raise p.ProtocolError("supply_save_failed")
            if self.is_owner_current() is not True or self.read_current() != updated:
                raise p.ProtocolError("supply_owner_changed")
        except BaseException:
            self.failed = True
            raise
        self.record = deepcopy(updated)

    def prepare(self, *, context, site_id, model_id, basis="", **settings):
        self._check()
        if self.record and self.record["phase"] != "accounted":
            raise p.ProtocolError("native_supply_unresolved")
        action, payload = plan_supply(context, **settings)
        if not action:
            return "", None, {}
        updated = {"version": 1, "identity_id": self.owner[0], "account_id": self.owner[1], "player_id": self.owner[2],
                   "site_id": site_id, "model_id": model_id, "operation_id": uuid.uuid4().hex,
                   "revision": 1, "phase": "pending", "action": action, "payload": payload,
                   "before": receipt_context(context), "after": None, "basis": basis, "created_at": time.time()}
        self.previous = deepcopy(self.record)
        self._commit(updated)
        return action, deepcopy(updated), {**p.placement(site_id, model_id), **payload, "operationId": updated["operation_id"]}

    def accept(self, expected, response):
        self._check()
        if self.record != expected or self.record["phase"] != "pending":
            raise p.ProtocolError("stale_supply_response")
        root = p._response(response)
        for key, expected_value in (("operationId", self.record["operation_id"]), ("playerId", self.owner[2])):
            if key in root and (type(root[key]) is not type(expected_value) or root[key] != expected_value):
                raise p.ProtocolError("supply_response_mismatch")
        self._commit({**self.record, "phase": "confirmed", "revision": self.record["revision"] + 1,
                      "after": receipt_context(response)})

    def cancel_undispatched(self, expected):
        self._check()
        if self.previous is None or self.record != expected or self.record["phase"] != "pending":
            raise p.ProtocolError("cannot_cancel_recovered_supply")
        self._commit(self.previous)
