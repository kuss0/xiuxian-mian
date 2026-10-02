"""Main-loop persistence boundary for native fishing workers."""

import asyncio
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
import threading
import json

from .. import persistence
from .miniapp_common import MiniAppIdentityOwner
from .fishing_dwelling_journal import NativeCastJournal, settlement_complete, validate
from .fishing_dwelling_protocol import ProtocolError
from . import fishing_dwelling_protocol as protocol
from . import fishing_dwelling_supply as supply


STATE_KEY = "fishing_native_operation"
STORE_TIMEOUT_SEC = 30


class NativeFishingStore:
    def __init__(self, identity_id, player_id):
        self.owner = MiniAppIdentityOwner.capture(identity_id)
        if self.owner is None:
            raise ProtocolError("native_owner_missing")
        self.player_id = player_id
        self.loop = asyncio.get_running_loop()
        self.thread_id = threading.get_ident()
        self.closed = False

    def _on_loop(self, callback):
        if self.closed:
            raise ProtocolError("native_store_closed")
        if threading.get_ident() == self.thread_id:
            return callback()
        future = Future()

        def apply():
            if not future.set_running_or_notify_cancel():
                return
            try:
                if self.closed:
                    raise ProtocolError("native_store_closed")
                future.set_result(callback())
            except BaseException as exc:
                future.set_exception(exc)

        try:
            self.loop.call_soon_threadsafe(apply)
            return future.result(timeout=STORE_TIMEOUT_SEC)
        except (RuntimeError, TimeoutError):
            future.cancel()
            self.closed = True
            raise ProtocolError("native_store_unavailable") from None

    def _owned(self):
        return not self.closed and self.owner.is_current()

    def is_current(self):
        return self._on_loop(self._owned)

    def _check(self):
        if not self._owned():
            raise ProtocolError("native_owner_changed")
        if (self.owner.identity.get("fishing_operation", {}) != {}
                or self.owner.identity.get("fishing_result_pending", {}) != {}):
            raise ProtocolError("legacy_fishing_unresolved")

    def read(self):
        def load():
            self._check()
            return deepcopy(self.owner.identity.get(STATE_KEY, {}))
        return self._on_loop(load)

    def compare_and_save(self, expected, updated):
        return self._compare_and_save(STATE_KEY, validate, expected, updated)

    def _compare_and_save(self, key, validator, expected, updated):
        expected, updated = deepcopy(expected), deepcopy(updated)

        def save():
            self._check()
            owned = validator(updated if updated != {} else expected)
            if updated == {} and owned["phase"] != ("cast_pending" if key == STATE_KEY else "pending"):
                raise ProtocolError("cannot_clear_native_session")
            other_key, other_validator = (supply.STATE_KEY, supply.validate) if key == STATE_KEY else (STATE_KEY, validate)
            other = self.owner.identity.get(other_key, {})
            if other != {} and other_validator(other)["phase"] != "accounted":
                raise ProtocolError("native_other_operation_unresolved")
            if (owned["identity_id"], owned["account_id"], owned["player_id"]) != (
                    self.owner.identity_id, self.owner.account_id, self.player_id):
                raise ProtocolError("native_record_owner_mismatch")
            current = self.owner.identity.get(key, {})
            if current != expected:
                raise ProtocolError("native_record_changed")
            self.owner.identity[key] = deepcopy(updated)
            persistence.mark_dirty()
            try:
                if persistence.save_state() is not True:
                    raise ProtocolError("native_save_failed")
            except BaseException:
                if self.owner.is_current() and self.owner.identity.get(key) == updated:
                    self.owner.identity[key] = deepcopy(expected)
                    persistence.mark_dirty()
                self.closed = True
                raise
            if not self._owned() or self.owner.identity.get(key) != updated:
                self.closed = True
                raise ProtocolError("native_owner_changed")
            return True

        return self._on_loop(save)

    def journal(self):
        return NativeCastJournal(owner=(self.owner.identity_id, self.owner.account_id, self.player_id),
                                 read_current=self.read, compare_and_save=self.compare_and_save,
                                 is_owner_current=self.is_current)

    def read_supply(self):
        def read():
            self._check()
            return deepcopy(self.owner.identity.get(supply.STATE_KEY, {}))
        return self._on_loop(read)

    def supply_journal(self):
        return supply.SupplyJournal(
            owner=(self.owner.identity_id, self.owner.account_id, self.player_id),
            read_current=self.read_supply, is_owner_current=self.is_current,
            compare_and_save=lambda expected, updated: self._compare_and_save(supply.STATE_KEY, supply.validate, expected, updated),
        )

    def project_supply(self):
        """The captured HTTP receipt, not a later inventory query, closes supply."""
        def apply():
            from . import fishing_runtime as fishing, storage_bag
            self._check()
            record = deepcopy(self.read_supply())
            supply.validate(record)
            if (record["identity_id"], record["account_id"], record["player_id"]) != (
                    self.owner.identity_id, self.owner.account_id, self.player_id):
                raise ProtocolError("native_record_owner_mismatch")
            if record["phase"] == "accounted":
                return False
            if record["phase"] != "confirmed" or record["basis"] != self.basis()["inventory"]:
                raise ProtocolError("native_supply_projection_held")
            before_shop, after_shop = supply.parse_shop(record["before"]), supply.parse_shop(record["after"])
            affected = supply.supply_deltas(before_shop, record["action"], record["payload"])
            before = deepcopy(self.owner.identity)
            inventory = deepcopy(fishing.get_storage_bag_records())
            records = deepcopy(inventory)
            try:
                for item_id in affected:
                    item = after_shop["resources"][item_id]
                    current = ((records.get(str(self.owner.identity_id)) or {}).get("items") or {}).get(item["name"], 0)
                    storage_bag._adjust_storage_bag_identity_item(
                        records, self.owner.identity_id, item["name"], item["count"] - int(current))
                fishing.set_storage_bag_records(records)
                self.owner.identity[supply.STATE_KEY] = {**record, "phase": "accounted", "revision": record["revision"] + 1}
                supply.validate(self.owner.identity[supply.STATE_KEY])
                persistence.mark_dirty()
                if persistence.save_state() is not True:
                    raise ProtocolError("native_supply_projection_save_failed")
            except BaseException:
                if self.owner.is_current():
                    self.owner.identity.clear()
                    self.owner.identity.update(before)
                    fishing.set_storage_bag_records(inventory)
                    persistence.mark_dirty()
                self.closed = True
                raise
            return True
        return self._on_loop(apply)

    def basis(self):
        def capture():
            from . import fishing_runtime as fishing
            self._check()
            return {"facts": fishing._fishing_result_basis(self.owner.identity, fishing._FISHING_MINIAPP_FACT_KEYS),
                    "plan": fishing._fishing_result_basis(self.owner.identity, fishing._FISHING_RESULT_PLAN_KEYS),
                    "inventory": fishing.fishing_operations.inventory_basis(self.owner.identity_id)}
        return self._on_loop(capture)

    def accept_recovery(self, ledger, query, payload, before_read):
        """Rebase only empty rods from a scoped fresh state read, never gains.

        Unrelated loot may change the bag while a missed rod is unresolved.
        A fresh response can replace bait balances if nothing changed during
        that read; it cannot establish whether old fish/rewards were applied.
        """
        def apply():
            if query.action != "state":
                raise ProtocolError("native_refresh_requires_state")
            parsed = ledger.accept(query, payload)
            record = ledger.record
            if (record["version"] != 3 or record["phase"] != "settled" or record["catches"]
                    or record["settlement_resources"]["rewards"] or payload.get("context") is None):
                return parsed
            current = self.basis()
            if (before_read != current or record["projection_basis"]["facts"] != current["facts"]):
                return parsed
            resources = protocol.parse_settlement_resources(
                payload, session_id=record["session_id"], site_id=record["site_id"])
            if resources["rewards"] or resources["baits"] is None:
                return parsed
            updated = deepcopy(record)
            updated["projection_basis"]["inventory"] = current["inventory"]
            updated["settlement_resources"] = resources
            updated["settlement_quota"] = protocol.parse_settlement_quota(payload)
            if updated != record:
                updated["revision"] += 1
                ledger._commit(updated)
            return parsed
        return self._on_loop(apply)

    def project(self, now, *, update_schedule=True):
        """Persist confirmed gains and the accounted marker in one state commit."""
        def apply():
            from . import fishing_runtime as fishing, storage_bag
            self._check()
            record = deepcopy(self.owner.identity.get(STATE_KEY, {}))
            validate(record)
            if (record["identity_id"], record["account_id"], record["player_id"]) != (
                    self.owner.identity_id, self.owner.account_id, self.player_id):
                raise ProtocolError("native_record_owner_mismatch")
            if record["phase"] == "accounted":
                return False
            if record["phase"] != "settled" or not settlement_complete(record):
                raise ProtocolError("native_settlement_incomplete")
            basis = self.basis()
            if any(record["projection_basis"].get(key) != basis[key] for key in ("facts", "inventory")):
                raise ProtocolError("native_projection_basis_changed")
            quota = record["settlement_quota"]
            day = fishing.get_day_key(now)
            if quota["day"] > day:
                raise ProtocolError("native_quota_from_future")
            identity = self.owner.identity
            if (quota["day"] == day and identity.get("fishing_daily_day") == day
                    and identity.get("fishing_daily_count", 0) > quota["used"]):
                raise ProtocolError("native_quota_regressed")
            can_schedule = (update_schedule and record["projection_basis"]["plan"] == basis["plan"]
                            and (bool(identity.get("fishing_enabled"))
                                 or fishing.is_cave_public_auto_enabled("fishing", self.owner.identity_id))
                            and fishing.is_cave_public_identity_available(self.owner.identity_id)
                            and (fishing.get_global_enabled() or fishing._miniapp_http_allowed_during_pause()))
            before = deepcopy(identity)
            inventory = deepcopy(fishing.get_storage_bag_records())
            resources = record.get("settlement_resources")
            rewards = resources["rewards"] if resources is not None else {}
            gains = deepcopy(record["catches"])
            for name, count in rewards.items():
                gains[name] = gains.get(name, 0) + count
            try:
                if gains and fishing.apply_storage_bag_item_deltas(
                        self.owner.identity_id, gains, persist=False) is not True:
                    raise ProtocolError("native_inventory_not_applied")
                if resources is not None:
                    # The settlement context already includes the cast's bait
                    # consumption and any bonus bait. Apply absolute counts last.
                    records = deepcopy(fishing.get_storage_bag_records())
                    for name, count in resources["baits"].items():
                        current = ((records.get(str(self.owner.identity_id)) or {}).get("items") or {}).get(name, 0)
                        if type(current) is not int or current < 0:
                            raise ProtocolError("invalid_native_inventory_count")
                        storage_bag._adjust_storage_bag_identity_item(records, self.owner.identity_id, name, count - current)
                    fishing.set_storage_bag_records(records)
                if quota["day"] == day:
                    identity.update(fishing_daily_day=day, fishing_daily_count=quota["used"],
                                    fishing_daily_limit=quota["limit"], fishing_basket_calibrated_day="")
                    # A recovery read carries today's quota, not the old rod's date.
                    if fishing.get_day_key(record["created_at"]) == day:
                        summary = fishing._normalize_fishing_daily_catch_summary(identity.get("fishing_daily_catch_summary_json"))
                        if summary["day"] != day:
                            summary = {"day": day, "rods": 0, "fish": {}, "rewards": {}}
                        summary["rods"] += 1
                        for name, count in record["catches"].items():
                            summary["fish"][name] = summary["fish"].get(name, 0) + count
                        for name, count in rewards.items():
                            summary["rewards"][name] = summary["rewards"].get(name, 0) + count
                        identity["fishing_daily_catch_summary_json"] = json.dumps(summary, ensure_ascii=False, sort_keys=True)
                    if resources is not None:
                        active = resources["active_chum"] or {}
                        identity.update(fishing_active_chum_name=active.get("name", ""),
                                        fishing_chum_rods_remaining=active.get("remaining", 0),
                                        fishing_chum_day=day,
                                        fishing_chum_counts=fishing.fishing_behavior.format_chum_usage_counts(resources["chum_usage"]))
                if can_schedule:
                    identity.update(fishing_phase="idle", fishing_last_error="",
                                    fishing_last_result="洞府钓鱼：" + (fishing._format_count_map(record["catches"]) if record["catches"] else "空竿"),
                                    next_fishing_time=(fishing.fishing_behavior.next_fishing_reset_timestamp(
                                        now, fishing._fishing_reset_jitter_sec(self.owner.identity_id))
                                        if quota["day"] == day and quota["remaining"] == 0 else now + 30))
                identity[STATE_KEY] = {**record, "phase": "accounted", "revision": record["revision"] + 1}
                validate(identity[STATE_KEY])
                persistence.mark_dirty()
                if persistence.save_state() is not True:
                    raise ProtocolError("native_projection_save_failed")
            except BaseException:
                if self.owner.is_current():
                    identity.clear()
                    identity.update(before)
                    fishing.set_storage_bag_records(inventory)
                    persistence.mark_dirty()
                self.closed = True
                raise
            return True
        return self._on_loop(apply)
