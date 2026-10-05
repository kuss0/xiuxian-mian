"""Bounded, at-most-once handoff to the existing storage gift sender.

Only queued work is restartable. An interrupted sender requires evidence review;
neither an expired wait nor a missing in-memory task proves non-delivery.
"""

from copy import deepcopy
import json
import math
import time
import uuid

from .. import persistence
from ..state import get_game_group_ids, get_identity_enabled, get_storage_bag_records, set_storage_bag_records
from .miniapp_common import MiniAppIdentityOwner

STATE_KEY = "fishing_gift_handoff"
MAX_BYTES = 16384
PHASES = {"queued", "active", "sending", "waiting", "held", "done"}


class HandoffError(RuntimeError):
    pass


def _require(condition):
    if not condition:
        raise HandoffError("gift_handoff_invalid")


def validate(record):
    if not isinstance(record, dict) or record.get("version") != 1:
        raise HandoffError("gift_handoff_invalid")
    try:
        _require(set(record) == {"version", "id", "phase", "identity_id", "account_id", "target_id", "target_account_id",
                                 "items", "confirmed", "command", "family", "msg_id", "reply_to", "chat_id", "reason",
                                 "created_at", "updated_at"})
        _require(type(record["version"]) is int)
        _require(record["phase"] in PHASES)
        _require(isinstance(record["id"], str) and len(record["id"]) == 32 and all(c in "0123456789abcdef" for c in record["id"]))
        for key in ("identity_id", "account_id", "target_id", "target_account_id"):
            _require(type(record[key]) is int and record[key] > 0)
        _require(record["identity_id"] != record["target_id"])
        items = record["items"]
        _require(isinstance(items, list) and 0 < len(items) <= 64)
        _require(len({item["item_name"] for item in items}) == len(items))
        for item in items:
            _require(set(item) == {"item_name", "quantity", "method"})
            _require(isinstance(item["item_name"], str) and 0 < len(item["item_name"]) <= 160)
            _require(not any(c in item["item_name"] for c in "\n\r*"))
            _require(type(item["quantity"]) is int and 0 < item["quantity"] <= 10**9)
            _require(item["method"] == "gift")
        _require(type(record["confirmed"]) is int and 0 <= record["confirmed"] <= len(items))
        _require((record["phase"] == "done") == (record["confirmed"] == len(items)))
        for key in ("command", "family", "reason"):
            _require(isinstance(record[key], str) and len(record[key]) <= 256)
        for key in ("msg_id", "reply_to"):
            _require(type(record[key]) is int and record[key] >= 0)
        _require(type(record["chat_id"]) is int)
        if record["phase"] == "queued":
            _require(record["confirmed"] == 0 and not record["command"] and not record["msg_id"])
        for key in ("created_at", "updated_at"):
            _require(type(record[key]) in (int, float) and math.isfinite(record[key]) and record[key] > 0)
        _require(len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode()) <= MAX_BYTES)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise HandoffError("gift_handoff_invalid") from None
    return record


class FishingGiftHandoff:
    def __init__(self, identity_id, target_id):
        self.owner = MiniAppIdentityOwner.capture(identity_id)
        self.target = MiniAppIdentityOwner.capture(target_id)
        self.closed = False
        if self.owner is None or self.target is None:
            raise HandoffError("gift_owner_missing")
        current = self.owner.identity.get(STATE_KEY, {})
        self.operation_id = current.get("id") if isinstance(current, dict) else None

    def check(self):
        if self.closed or not self.owner.is_current() or not self.target.is_current():
            raise HandoffError("gift_owner_changed")

    def read(self):
        self.check()
        record = deepcopy(self.owner.identity.get(STATE_KEY, {}))
        if record != {}:
            validate(record)
            if self.operation_id and record["id"] != self.operation_id:
                raise HandoffError("gift_record_replaced")
            if (record["identity_id"], record["account_id"], record["target_id"], record["target_account_id"]) != (
                    self.owner.identity_id, self.owner.account_id, self.target.identity_id, self.target.account_id):
                raise HandoffError("gift_record_owner_mismatch")
        return record

    def commit(self, record, *, identity_updates=None, inventory=None):
        self.check()
        validate(record)
        updates = {STATE_KEY: deepcopy(record), **(identity_updates or {})}
        before = {key: deepcopy(self.owner.identity.get(key)) for key in updates}
        old_inventory = deepcopy(get_storage_bag_records()) if inventory is not None else None
        self.owner.identity.update(updates)
        if inventory is not None:
            set_storage_bag_records(inventory)
        persistence.mark_dirty()
        try:
            if persistence.save_state() is not True:
                raise HandoffError("gift_save_failed")
        except BaseException:
            if self.owner.is_current():
                self.owner.identity.update(before)
            if old_inventory is not None:
                set_storage_bag_records(old_inventory)
            persistence.mark_dirty()
            self.closed = True
            raise
        self.check()

    def prepare(self, items, now):
        previous = self.read()
        if previous and previous["phase"] != "done":
            return previous
        try:
            pending = json.loads(self.owner.identity.get("fishing_caught_fish_json") or "{}")
        except (TypeError, ValueError):
            raise HandoffError("gift_queue_invalid") from None
        if pending != {item["item_name"]: item["quantity"] for item in items}:
            raise HandoffError("gift_queue_changed")
        if any(type(count) is not int or count <= 0 for count in pending.values()):
            raise HandoffError("gift_queue_invalid")
        record = dict(version=1, id=uuid.uuid4().hex, phase="queued", identity_id=self.owner.identity_id,
                      account_id=self.owner.account_id, target_id=self.target.identity_id,
                      target_account_id=self.target.account_id, items=deepcopy(items), confirmed=0,
                      command="", family="", msg_id=0, reply_to=0, chat_id=0, reason="",
                      created_at=now, updated_at=now)
        # Queue ownership and queue removal must reach SQLite in the same commit.
        self.commit(record, identity_updates={"fishing_caught_fish_json": "", "fishing_transfer_due_at": 0,
                                             "fishing_last_error": ""})
        self.operation_id = record["id"]
        return record

    def transition(self, phase, **updates):
        record = self.read()
        self.commit({**record, **updates, "phase": phase, "updated_at": time.time()})

    def allowed(self):
        try:
            record = self.read()
            return (record["phase"] == "sending" and record["chat_id"] in get_game_group_ids()
                    and bool(self.owner.identity.get("fishing_enabled"))
                    and self.owner.identity.get("fishing_transfer_target_id") == self.target.identity_id
                    and get_identity_enabled(self.owner.identity_id) and get_identity_enabled(self.target.identity_id))
        except HandoffError:
            return False

    def before_send(self, command, family, reply_to, chat_id):
        record = self.read()
        if record["phase"] != "active":
            raise HandoffError("gift_send_already_attempted")
        if (not self.owner.identity.get("fishing_enabled")
                or self.owner.identity.get("fishing_transfer_target_id") != self.target.identity_id
                or not get_identity_enabled(self.owner.identity_id) or not get_identity_enabled(self.target.identity_id)):
            raise HandoffError("gift_config_changed")
        if not chat_id or chat_id not in get_game_group_ids():
            raise HandoffError("gift_anchor_group_unknown")
        if family not in {"storage_bag_gift", "storage_bag_gift_locator"}:
            raise HandoffError("gift_family_mismatch")
        if family == "storage_bag_gift":
            item = record["items"][record["confirmed"]]
            if command != f".赠送 {item['item_name']}*{item['quantity']}":
                raise HandoffError("gift_command_mismatch")
        self.transition("sending", command=command, family=family, reply_to=reply_to, msg_id=0, chat_id=chat_id)

    def sent(self, msg_id, chat_id, *, known_unsent=False):
        record = self.read()
        if record["phase"] != "sending":
            raise HandoffError("gift_send_state_changed")
        phase = "active" if known_unsent or (msg_id > 0 and record["family"] == "storage_bag_gift_locator") else "waiting"
        self.transition(phase, msg_id=msg_id, chat_id=chat_id)

    def confirm(self, item_name, quantity, tax, command_msg_id):
        from .storage_bag import _adjust_storage_bag_identity_item

        record = self.read()
        if record["phase"] != "waiting":
            raise HandoffError("gift_receipt_mismatch")
        expected = record["items"][record["confirmed"]]
        if (record["phase"] != "waiting" or record["family"] != "storage_bag_gift"
                or record["msg_id"] <= 0 or command_msg_id != record["msg_id"]
                or (item_name, quantity) != (expected["item_name"], expected["quantity"])):
            raise HandoffError("gift_receipt_mismatch")
        inventory = deepcopy(get_storage_bag_records())
        _adjust_storage_bag_identity_item(inventory, self.owner.identity_id, item_name, -quantity)
        _adjust_storage_bag_identity_item(inventory, self.target.identity_id, item_name, quantity)
        if tax:
            _adjust_storage_bag_identity_item(inventory, self.owner.identity_id, "灵石", -tax)
        confirmed = record["confirmed"] + 1
        self.commit({**record, "confirmed": confirmed, "phase": "done" if confirmed == len(record["items"]) else "active",
                     "updated_at": time.time()}, inventory=inventory,
                    identity_updates={"fishing_last_result": f"鱼获赠送已确认：{confirmed}/{len(record['items'])} 项",
                                      "fishing_last_error": ""})

    def finish(self, success, reason):
        record = self.read()
        if record["phase"] == "done":
            return
        if success:
            raise HandoffError("gift_completion_unconfirmed")
        self.commit({**record, "phase": "held", "reason": str(reason)[:256], "updated_at": time.time()},
                    identity_updates={"fishing_last_error": "鱼获赠送待核查，不自动补发：" + str(reason)[:160]})


async def dispatch(handoff):
    from . import storage_bag

    record = handoff.read()
    if record["phase"] == "done":
        return False
    active = storage_bag._storage_bag_fishing_handoff
    if active and active.owner.identity_id == handoff.owner.identity_id:
        return True
    if record["phase"] == "held":
        return True
    if record["phase"] != "queued":
        handoff.finish(False, "重启前发送未核销")
        await storage_bag.send_audit_log("⚠️ 鱼获赠送中断，已保留任务待核查，不自动补发。", scope="identity", priority="high")
        return True
    if storage_bag._storage_bag_transfer_state.get("running") or storage_bag._storage_bag_transfer_batch_state.get("running"):
        return True
    handoff.transition("active")
    try:
        ok, message, _ = await storage_bag.start_storage_bag_gift_task(
            record["identity_id"], record["target_id"], record["items"], gift_handoff=handoff)
        if not ok:
            handoff.finish(False, message)
    except Exception as exc:
        if not handoff.closed:
            handoff.finish(False, type(exc).__name__)
        raise
    return True
