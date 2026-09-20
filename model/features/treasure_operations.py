"""Owned treasure checkpoints and local accounting, never gameplay replay."""

import asyncio
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
import hashlib
import threading
import time
import uuid

from ..inventory_delta import _record_key
from ..miniapp_state import sanitize_miniapp_state
from ..persistence import _bounded_miniapp_operation, mark_dirty, save_state
from ..state import TREASURE_OPERATION_MAX_BYTES, get_identity_account, get_identity_ids, get_inventory_delta_records
from ..timing import get_day_key
from ..webapp_core import MiniAppRequestAborted, require_miniapp_operation, sanitize_webapp_secret_text
from . import treasure_results as results
from .miniapp_common import MiniAppIdentityOwner
from .treasure_receipts import treasure_integer, treasure_quota_exhausted
from .treasure_runs import (
    TREASURE_MAX_CELLS, treasure_reveal_progress_error, treasure_run_continuity_error,
    treasure_search_error, treasure_settlement_allowed,
)


STATE_KEY = "treasure_operation"
CHECKPOINT_TIMEOUT_SEC = 30
MAX_RECEIPTS = 99
_BOOLS = {"quota_present", "quota_verified", "quota_known", "in_round", "treasure_found",
          "settled", "daily_limit_confirmed", "on_treasure_tab", "run_verified", "board_verified", "board_complete"}
_COUNTS = {"games_used", "games_limit", "games_remaining", "action_remaining", "action_limit",
           "hint_target", "target_count", "board_size"}
_TARGETS = {"available_targets", "revealed_targets"}
_TEXT = {"quota_error", "state_error", "status", "hint_text"}
_GAIN_FIELDS = {"\u7ecf\u9a8c": "expGain", "\u4fee\u4e3a": "cultivationGain",
                "\u7075\u77f3": "lingshiGain", "\u8d21\u732e": "contribution"}
_TRACE = "\u5929\u673a\u6b8b\u75d5"
EVIDENCE_FIELDS = {"action_dispatched", "pending", "state", "receipts", "resolution",
                   "retry_after_sec", "ok", "status", "error"}


def session_key(value):
    return hashlib.sha256(value.encode()).hexdigest() if value else ""


def digest(value):
    return hashlib.sha256(results._encoded(value).encode()).hexdigest()


def state_evidence(state):
    value = {key: deepcopy(state[key]) for key in _BOOLS | _COUNTS | _TARGETS if key in state}
    value.update({key: sanitize_webapp_secret_text(state[key], limit=240) for key in _TEXT if key in state})
    value["session_key"] = session_key(state.get("session_id", ""))
    return value


def receipt_evidence(receipt, session_id):
    data = receipt["result"]
    return {"session_key": session_key(session_id), "rewards": deepcopy(receipt["rewards"]),
            "gains": deepcopy(receipt["gains"]), "found_main": data.get("foundMain", data.get("found_main")),
            "material_error": sanitize_webapp_secret_text(receipt["material_error"], limit=240)}


def _valid_state(value):
    if not isinstance(value, dict) or not set(value) <= _BOOLS | _COUNTS | _TEXT | _TARGETS | {"session_key"}:
        return False
    if any(type(value[key]) is not bool for key in _BOOLS & value.keys()):
        return False
    if any(type(value[key]) is not int or treasure_integer(value[key]) is None for key in _COUNTS & value.keys()):
        return False
    if any(not isinstance(value[key], str) or len(value[key]) > 240 for key in _TEXT & value.keys()):
        return False
    key = value.get("session_key", "")
    if key != "" and not results._key(key) or value.get("in_round") and not key:
        return False
    targets = []
    for field in _TARGETS:
        items = value.get(field, [])
        if (not isinstance(items, list) or len(items) > TREASURE_MAX_CELLS
                or any(type(item) is not int or treasure_integer(item) in (None, 0) for item in items)):
            return False
        targets.extend(items)
    if len(set(targets)) != len(targets):
        return False
    if value.get("board_verified"):
        count, size = value.get("target_count", 0), value.get("board_size", 0)
        if (not 0 < count <= TREASURE_MAX_CELLS or not targets or any(item > count for item in targets)
                or size and size * size != count
                or value.get("board_complete") and (not size or len(targets) != count)):
            return False
    if value.get("quota_verified"):
        used, limit, remaining = (value.get(key) for key in ("games_used", "games_limit", "games_remaining"))
        if (any(type(item) is not int for item in (used, limit, remaining)) or limit <= 0
                or used + remaining != limit or value.get("quota_error")):
            return False
    return True


def _valid_request(value):
    if (not isinstance(value, dict) or set(value) != {"action", "session_key", "target_index"}
            or value["action"] not in ("enter", "search", "settle")
            or type(value["target_index"]) is not int):
        return False
    return ((value["session_key"] == "" if value["action"] == "enter" else results._key(value["session_key"]))
            and (treasure_integer(value["target_index"]) not in (None, 0) if value["action"] == "search"
                 else value["target_index"] == 0))


def search_daily_reset_resolved(previous, current, pending, *, previous_at=0, current_at=0):
    """Prove that an unknown search belongs to a server-reset round.

    A reveal/search does not award loot by itself.  It is safe to retire only
    when a fresh owned snapshot has no active/settled round and its verified
    daily usage counter has moved backwards.  Missing round data alone is not
    enough evidence because it can also be a partial response.
    """
    if (not results._time(previous_at) or not results._time(current_at) or current_at <= previous_at
            or get_day_key(previous_at) == get_day_key(current_at)
            or not _valid_state(previous) or not _valid_state(current) or not _valid_request(pending)
            or pending["action"] != "search" or previous.get("in_round") is not True
            or pending["session_key"] != previous.get("session_key")
            or pending["target_index"] in previous.get("revealed_targets", [])
            or current.get("in_round") is not False or current.get("session_key", "")
            or current.get("settled") is not False or current.get("treasure_found") is not False
            or current.get("quota_verified") is not True):
        return False
    previous_used = previous.get("games_used")
    current_used = current.get("games_used")
    previous_limit = previous.get("games_limit")
    current_limit = current.get("games_limit")
    return (
        type(previous_used) is int and type(current_used) is int
        and type(previous_limit) is int and type(current_limit) is int
        and previous_limit > 0 and previous_limit == current_limit
        and 0 <= current_used < previous_used <= previous_limit
    )


def _valid_receipt(value):
    return (isinstance(value, dict) and set(value) == {"session_key", "rewards", "gains", "found_main", "material_error"}
            and results._key(value["session_key"]) and results._counts(value["rewards"])
            and results._counts(value["gains"]) and set(value["gains"]) <= _GAIN_FIELDS.keys() | {_TRACE}
            and (value["found_main"] is None or type(value["found_main"]) is bool)
            and isinstance(value["material_error"], str) and len(value["material_error"]) <= 240)


def valid_checkpoint(value):
    if (not isinstance(value, dict) or set(value) != EVIDENCE_FIELDS | {"version", "phase", "sequence"}
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["sequence"]) is not int or not 0 < value["sequence"] < 10000
            or value["phase"] not in ("response", "intent", "settled", "complete")
            or any(type(value[key]) is not bool for key in ("action_dispatched", "ok"))
            or type(value["retry_after_sec"]) not in (int, float) or not 0 <= value["retry_after_sec"] < 1e12
            or any(not isinstance(value[key], str) or len(value[key]) > 240 for key in ("status", "error"))
            or not _valid_state(value["state"])):
        return False
    pending, resolution, receipts = value["pending"], value["resolution"], value["receipts"]
    if not results._empty(pending) and not _valid_request(pending):
        return False
    if not results._empty(resolution) and (
        not isinstance(resolution, dict) or set(resolution) != {"kind", "request"}
        or resolution["kind"] not in ("not_sent", "rejected", "daily_limit", "daily_reset")
        or not _valid_request(resolution["request"]) or pending
    ):
        return False
    if (not isinstance(receipts, list) or len(receipts) > MAX_RECEIPTS
            or any(not _valid_receipt(item) for item in receipts)
            or len({item["session_key"] for item in receipts}) != len(receipts)
            or (receipts and not value["action_dispatched"])
            or (value["phase"] == "intent" and (not pending or resolution))
            or (value["phase"] == "settled" and (pending or resolution or not receipts or value["state"].get("in_round")))
            or value["ok"] and (pending or value["status"] != "daily_limit"
                                 or not treasure_quota_exhausted(value["state"]))):
        return False
    try:
        return (_bounded_miniapp_operation(value) and len(results._encoded(value).encode()) <= TREASURE_OPERATION_MAX_BYTES
                and results._secret_free(value))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def valid_record(value):
    fields = {"version", "operation_id", "identity_id", "account_id", "player_id", "created_at", "updated_at",
              "checkpoint", "pending_save", "inventory_before", "miniapp_before", "result_before"}
    if isinstance(value, dict) and value.get("version") == 2:
        fields.add("resume")
    if (not isinstance(value, dict) or set(value) != fields or not _bounded_miniapp_operation(value)
            or type(value["version"]) is not int or value["version"] not in (1, 2)
            or not results._key(value["operation_id"], 32)
            or type(value["identity_id"]) is not int or value["identity_id"] <= 0
            or type(value["account_id"]) is not int or value["account_id"] < 0
            or type(value["player_id"]) is not int
            or value["player_id"] not in (value["identity_id"], -1_000_000_000_000 - value["identity_id"])
            or any(not results._time(value[key]) for key in ("created_at", "updated_at"))
            or value["updated_at"] < value["created_at"] or type(value["pending_save"]) is not bool
            or any(not results._key(value[key]) for key in ("inventory_before", "miniapp_before", "result_before"))
            or not valid_checkpoint(value["checkpoint"])):
        return False
    try:
        if value["version"] == 2:
            resume = value["resume"]
            if (not isinstance(resume, dict) or set(resume) != {"operation_id", "checkpoint", "receipts", "receipt_digest"}
                    or not results._key(resume["operation_id"], 32) or resume["operation_id"] == value["operation_id"]
                    or not results._key(resume["checkpoint"]) or not results._key(resume["receipt_digest"])
                    or type(resume["receipts"]) is not int or not 0 <= resume["receipts"] <= len(value["checkpoint"]["receipts"])
                    or resume["receipt_digest"] != digest(value["checkpoint"]["receipts"][:resume["receipts"]])):
                return False
        return len(results._encoded(value).encode()) <= TREASURE_OPERATION_MAX_BYTES
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _owned(owner, value):
    return (owner is not None and owner.is_current() and valid_record(value)
            and value["identity_id"] == owner.identity_id and value["account_id"] == owner.account_id)


def inventory_key(record):
    return _record_key(record["identity_id"], "cave_treasure_miniapp", "operation:" + record["operation_id"])


def projected_receipts(record):
    return record["checkpoint"]["receipts"][record.get("resume", {}).get("receipts", 0):]


def result_matches(owner, result):
    record = owner.identity.get(STATE_KEY, {}) if owner else {}
    if not (_owned(owner, record) and not record["pending_save"] and results.valid_record(result)
            and result["identity_id"] == owner.identity_id and result["account_id"] == owner.account_id
            and result.get("version") == 2
            and result.get("operation_id") == record["operation_id"]
            and result.get("operation_checkpoint") == digest(record["checkpoint"])):
        return False
    checkpoint, extra = record["checkpoint"], result["response"]["extra"]
    rewards, gains = {}, {}
    for receipt in projected_receipts(record):
        for target, field in ((rewards, "rewards"), (gains, "gains")):
            for name, count in receipt[field].items():
                target[name] = target.get(name, 0) + count
    projected = projected_result(record)
    state = projected["data"]["state"]
    if projected["outcome_unknown"]:
        state["outcome_unknown_day"] = get_day_key(record["updated_at"])
    state["owner_account_id"] = owner.account_id or owner.identity_id
    return (extra["settled_count"] == len(projected_receipts(record))
            and results._same(extra["rewards"], rewards) and results._same(extra["gains"], gains)
            and extra["outcome_unknown"] == bool(checkpoint["pending"])
            and result["created_at"] == record["updated_at"] and result["response"]["ok"] == projected["ok"]
            and extra["status"] == projected["status"]
            and extra["daily_exhausted"] == (projected["ok"] and treasure_quota_exhausted(state))
            and all(extra[key] == state.get(key, 0) for key in ("games_used", "games_limit"))
            and results._same(result["miniapp"].get("record", {}).get("state", {}), sanitize_miniapp_state(state))
            and all(not result[field] or result[field]["before"] == record[field + "_before"]
                    for field in ("inventory", "miniapp")))


def _accounted(owner, record):
    value = owner.identity.get(results.STATE_KEY, {})
    return (results.valid_record(value) and value["identity_id"] == owner.identity_id
            and value["account_id"] == owner.account_id and value["phase"] == "complete"
            and result_matches(owner, value))


def _own_reason(owner, record):
    if results._empty(record):
        return ""
    if not _owned(owner, record):
        return "operation_owner_invalid"
    if not _accounted(owner, record):
        return "operation_pending"
    if record["checkpoint"]["pending"]:
        return "outcome_unknown_hold"
    if record["checkpoint"]["state"].get("in_round"):
        return "original_round_required"
    return ""


def hold_reason(identity_id):
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None:
        return "owner_changed"
    reason = _own_reason(owner, owner.identity.get(STATE_KEY, {}))
    if reason:
        return reason
    return _sibling_hold_reason(owner)


def _sibling_hold_reason(owner):
    identity_id = owner.identity_id
    account = owner.account_id or owner.identity_id
    for other_id in get_identity_ids():
        if other_id == identity_id:
            continue
        other = MiniAppIdentityOwner.capture(other_id)
        value = other.identity.get(STATE_KEY, {}) if other else {}
        if not _own_reason(other, value):
            continue
        original = value.get("account_id") if isinstance(value, dict) else None
        if original == 0:
            original = value.get("identity_id")
        if (other and (other.account_id or other_id) == account
                or type(original) is int and original == account):
            return "account_operation_pending"
    return ""


def resume_allowed(identity_id):
    owner = MiniAppIdentityOwner.capture(identity_id)
    record = owner.identity.get(STATE_KEY, {}) if owner else {}
    if not _owned(owner, record) or not _accounted(owner, record):
        return False
    checkpoint, result = record["checkpoint"], owner.identity[results.STATE_KEY]
    state, pending = checkpoint["state"], checkpoint["pending"]
    if (state.get("in_round") is not True or not results._key(state.get("session_key"))
            or pending and (pending["action"] not in {"search", "settle"}
                           or pending["session_key"] != state["session_key"])):
        return False
    if not results._same(results.get_miniapp_state_records().get(f"{identity_id}:cave_treasure"),
                         result["miniapp"].get("record")):
        return False
    if _sibling_hold_reason(owner) or results.account_hold_reason(owner):
        return False
    account_id = owner.account_id or identity_id
    for key, row in results.get_miniapp_state_records().items():
        if key == f"{identity_id}:cave_treasure" or not str(key).endswith(":cave_treasure") or not isinstance(row, dict):
            continue
        snapshot = row.get("state", {})
        if isinstance(snapshot, dict) and snapshot.get("outcome_unknown"):
            try:
                other_id = int(str(key).split(":", 1)[0])
                original_account = int(snapshot.get("owner_account_id") or 0) or get_identity_account(other_id) or other_id
            except (TypeError, ValueError, OverflowError):
                return False
            if other_id == identity_id or original_account == account_id:
                return False
    return True


def _resume_checkpoint_allowed(previous, value):
    old = previous["checkpoint"]
    state, current, pending = old["state"], value["state"], old["pending"]
    prior, receipts = old["receipts"], value["receipts"]
    resolution = value["resolution"]
    if (value["phase"] != "response" or value["pending"]
            or value["ok"] or value["status"] != "running" or value["error"]
            or value["action_dispatched"] != (old["action_dispatched"] or bool(pending) or len(receipts) > len(prior))
            or value["retry_after_sec"] < old["retry_after_sec"]
            or not results._same(receipts[:len(prior)], prior)):
        return False
    if resolution:
        return (
            len(receipts) == len(prior)
            and resolution.get("kind") == "daily_reset"
            and results._same(resolution.get("request"), pending)
            and search_daily_reset_resolved(
                state, current, pending,
                previous_at=previous["updated_at"], current_at=time.time(),
            )
        )
    if len(receipts) > len(prior):
        return (len(receipts) == len(prior) + 1 and receipts[-1]["session_key"] == state["session_key"]
                and current.get("in_round") is False and current.get("settled") is True)
    if pending.get("action") == "settle":
        return False
    if len(receipts) != len(prior) or treasure_run_continuity_error(state, current, session_field="session_key"):
        return False
    if pending and treasure_reveal_progress_error(state, current, pending["target_index"], session_field="session_key"):
        return False
    return (not state.get("board_size") or current.get("board_size") == state["board_size"]) and (
        set(state.get("revealed_targets", [])) <= set(current.get("revealed_targets", [])))


def held_response(reason="operation_pending"):
    response = results.held_response(reason)
    response["extra"]["status"] = "operation_pending"
    return response


def _save_owned(owner, previous, staged, *, retain_failed=False):
    if not owner.is_current() or not results._same(previous, owner.identity.get(STATE_KEY, {})):
        return False, False
    owner.identity[STATE_KEY] = deepcopy(staged)
    mark_dirty()
    cancelled = False
    try:
        saved = save_state() is True
    except asyncio.CancelledError:
        saved, cancelled = False, True
    except Exception:
        saved = False
    if not saved:
        replacement = {**deepcopy(staged), "pending_save": True} if retain_failed else deepcopy(previous)
        current_owner = MiniAppIdentityOwner.capture(owner.identity_id)
        for identity in (owner.identity, current_owner.identity if current_owner else {}):
            if results._same(identity.get(STATE_KEY, {}), staged):
                identity[STATE_KEY] = deepcopy(replacement)
        mark_dirty()
    return (saved and owner.is_current() and results._same(owner.identity.get(STATE_KEY), staged)), cancelled


class CheckpointWriter:
    def __init__(self, projection, *, player_id, operation_check=None, resume=False):
        self.owner, self.projection = projection.owner, projection
        if not projection.is_current() or (
            not resume_allowed(self.owner.identity_id) if resume else hold_reason(self.owner.identity_id)
        ):
            raise ValueError("treasure_previous_operation_pending")
        if type(player_id) is not int or player_id not in (self.owner.identity_id, -1_000_000_000_000 - self.owner.identity_id):
            raise ValueError("treasure_operation_player_invalid")
        self.player_id, self.operation_check = player_id, operation_check or self.owner.is_current
        self.loop, self.thread_id = asyncio.get_running_loop(), threading.get_ident()
        self.current = deepcopy(self.owner.identity.get(STATE_KEY, {}))
        self.resume_record = deepcopy(self.current) if resume else None
        self.operation_id, self.sequence = uuid.uuid4().hex, 0
        self.last_checkpoint, self.closed, self.cancelled = None, False, False

    def is_current(self):
        return (self.owner.is_current() and self.projection.is_current()
                and results._same(self.current, self.owner.identity.get(STATE_KEY, {})))

    def __call__(self, checkpoint):
        if self.closed:
            return False
        checkpoint = deepcopy(checkpoint)
        if threading.get_ident() == self.thread_id:
            return self._accept(checkpoint)
        future = Future()

        def apply():
            if not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(self._accept(checkpoint) if not self.closed else False)
            except Exception:
                future.set_result(False)

        try:
            self.loop.call_soon_threadsafe(apply)
            return future.result(timeout=CHECKPOINT_TIMEOUT_SEC) is True
        except TimeoutError:
            if future.cancel():
                return False
            # A save already running on the owner loop must finish before dispatch.
            return future.result() is True
        except RuntimeError:
            return False

    def _transition_allowed(self, value):
        before = self.last_checkpoint
        phase, target = value["phase"], value["pending"]
        if before is None:
            if self.resume_record is not None:
                return _resume_checkpoint_allowed(self.resume_record, value)
            return (phase == "response" and not target and not value["receipts"]
                    and not value["resolution"] and not value["action_dispatched"])
        receipts, prior = value["receipts"], before["receipts"]
        if (len(receipts) < len(prior) or not results._same(receipts[:len(prior)], prior)
                or value["retry_after_sec"] < before["retry_after_sec"]
                or before["action_dispatched"] and not value["action_dispatched"] or before["phase"] == "complete"):
            return False
        added = receipts[len(prior):]
        source, state, resolution = before["pending"], before["state"], value["resolution"]
        if phase == "intent":
            if (source or before["resolution"] or added or state.get("state_error")
                    or state.get("quota_present") and state.get("quota_error") not in {None, "", "hunt_quota_missing"}
                    or not results._same(state, value["state"])):
                return False
            if target["action"] == "enter":
                return not state.get("in_round") and state.get("quota_verified") is True and not treasure_quota_exhausted(state)
            return (state.get("in_round") is True and state.get("session_key") == target["session_key"]
                    and (treasure_settlement_allowed(state) if target["action"] == "settle"
                         else not treasure_search_error(state) and target["target_index"] in state["available_targets"]))
        if added:
            return (phase in ("settled", "complete") and len(added) == 1 and source.get("action") == "settle"
                    and source["session_key"] == added[0]["session_key"] and not target and not resolution
                    and not value["state"].get("in_round") and value["action_dispatched"])
        if phase == "settled":
            return False
        if (target == source and resolution == before["resolution"] and results._same(state, value["state"])):
            return phase == "complete" or bool(source)
        if not source or target or before["phase"] != "intent":
            return False
        if resolution:
            if resolution["request"] != source:
                return False
            if resolution["kind"] == "daily_limit":
                return source["action"] == "enter" and value["action_dispatched"] and treasure_quota_exhausted(value["state"])
            return (results._same(state, value["state"]) and (
                value["action_dispatched"] if resolution["kind"] == "rejected"
                else value["action_dispatched"] == before["action_dispatched"]
            ))
        new_state = value["state"]
        return (source["action"] in ("enter", "search") and value["action_dispatched"]
                and new_state.get("in_round") is True
                and (source["action"] == "enter" or new_state.get("session_key") == source["session_key"])
                and (source["action"] != "search" or not treasure_reveal_progress_error(
                    state, new_state, source["target_index"], session_field="session_key"))
                and (source["action"] != "search" or not state.get("board_size")
                     or new_state.get("board_size") == state["board_size"])
                and (source["action"] != "search" or set(state.get("revealed_targets", [])) <= set(new_state.get("revealed_targets", [])))
                and new_state.get("session_key") not in {item["session_key"] for item in prior})

    def _accept(self, checkpoint, *, retain_failed=False):
        if self.last_checkpoint is not None and results._same(checkpoint, self.last_checkpoint) and self.is_current():
            return not self.current["pending_save"]
        if (not self.is_current() or not valid_checkpoint(checkpoint)
                or checkpoint["sequence"] != self.sequence + 1 or not self._transition_allowed(checkpoint)):
            return False
        if self.resume_record is not None and not self.sequence and not resume_allowed(self.owner.identity_id):
            return False
        if checkpoint["phase"] == "intent":
            if self.cancelled:
                return False
            try:
                require_miniapp_operation(self.operation_check)
            except MiniAppRequestAborted:
                return False
        now = time.time()
        record = deepcopy(self.current) if self.sequence else {
            "version": 1, "operation_id": self.operation_id, "identity_id": self.owner.identity_id,
            "account_id": self.owner.account_id, "player_id": self.player_id, "created_at": now,
            "inventory_before": self.projection.inventory_bases.get(
                _record_key(self.owner.identity_id, "cave_treasure_miniapp", "operation:" + self.operation_id),
                results._basis({}, ""),
            ),
            "miniapp_before": self.projection.miniapp_basis, "result_before": digest(self.projection.previous),
        }
        if not self.sequence and self.resume_record is not None:
            previous = self.resume_record
            record.update(version=2, resume={
                "operation_id": previous["operation_id"], "checkpoint": digest(previous["checkpoint"]),
                "receipts": len(previous["checkpoint"]["receipts"]),
                "receipt_digest": digest(previous["checkpoint"]["receipts"]),
            })
        record.update(updated_at=max(now, record.get("updated_at", record["created_at"])),
                      checkpoint=deepcopy(checkpoint), pending_save=False)
        if not valid_record(record):
            return False
        saved, cancelled = _save_owned(self.owner, self.current, record, retain_failed=retain_failed)
        self.cancelled = self.cancelled or cancelled
        if saved or (retain_failed and self.owner.is_current() and results._same(
            {**record, "pending_save": True}, self.owner.identity.get(STATE_KEY, {}),
        )):
            self.current = deepcopy(self.owner.identity[STATE_KEY])
            self.last_checkpoint, self.sequence = deepcopy(checkpoint), checkpoint["sequence"]
        return saved

    def finish(self, result):
        if self.closed:
            return False
        self.closed = True
        if not self.is_current() or not isinstance(result, dict):
            return False
        if not self.sequence:
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            return not result.get("action_dispatched") and not result.get("outcome_unknown") and not data.get("results")
        if type(result.get("checkpoint_sequence")) is not int or result["checkpoint_sequence"] != self.sequence:
            return False
        checkpoint = deepcopy(result.get("operation_evidence"))
        if not isinstance(checkpoint, dict):
            return False
        checkpoint.update(version=1, phase="complete", sequence=self.sequence + 1)
        return self._accept(checkpoint, retain_failed=True)


def projected_result(record):
    checkpoint = record["checkpoint"]
    native = []
    for receipt in projected_receipts(record):
        data = {"sessionId": receipt["session_key"], "settled": True, "loot": deepcopy(receipt["rewards"])}
        if receipt["found_main"] is not None:
            data["foundMain"] = receipt["found_main"]
        for name, amount in receipt["gains"].items():
            if name == _TRACE:
                data["log"] = [f"{name}+{amount}"]
            else:
                data[_GAIN_FIELDS[name]] = amount
        native.append(data)
    state = deepcopy(checkpoint["state"])
    unknown = bool(checkpoint["pending"])
    if unknown:
        state.update(outcome_unknown=True, outcome_unknown_action=checkpoint["pending"]["action"])
    complete = checkpoint["phase"] == "complete"
    return {"ok": checkpoint["ok"] if complete else False,
            "status": checkpoint["status"] if complete else "result_unknown" if unknown else "partial" if native else "interrupted",
            "error": checkpoint["error"] if complete else "treasure_interrupted_local_recovery",
            "outcome_unknown": unknown, "action_dispatched": checkpoint["action_dispatched"],
            "retry_after_sec": checkpoint["retry_after_sec"], "events": [],
            "data": {"state": state, "results": native, "settled_count": len(native),
                     "material_errors": list(dict.fromkeys(item["material_error"] for item in checkpoint["receipts"]
                                                           if item["material_error"]))}}


def _projection(owner, record):
    current = owner.identity.get(results.STATE_KEY, {})
    if not _owned(owner, record):
        return None
    try:
        if digest(current) != record["result_before"]:
            return None
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    key = inventory_key(record)
    projection = results.ResultProjection(owner, deepcopy(current), {key: record["inventory_before"]}, record["miniapp_before"])
    if (results._basis(get_inventory_delta_records(), key) != record["inventory_before"]
            or results._basis(results.get_miniapp_state_records(), f"{owner.identity_id}:cave_treasure") != record["miniapp_before"]):
        return None
    return projection


def recovery_due(identity_id):
    owner = MiniAppIdentityOwner.capture(identity_id)
    record = owner.identity.get(STATE_KEY, {}) if owner else {}
    return _owned(owner, record) and not _accounted(owner, record) and _projection(owner, record) is not None


def recover_local(identity_id, commit):
    owner = MiniAppIdentityOwner.capture(identity_id)
    record = deepcopy(owner.identity.get(STATE_KEY, {})) if owner else {}
    if results._empty(record):
        return held_response(hold_reason(identity_id)) if hold_reason(identity_id) else None
    if not _owned(owner, record):
        return held_response("operation_owner_invalid")
    current = owner.identity.get(results.STATE_KEY, {})
    if results.valid_record(current) and result_matches(owner, current):
        if current["phase"] == "pending":
            return results.recover_local(identity_id)
        reason = hold_reason(identity_id)
        return held_response(reason) if reason else None
    projection = _projection(owner, record)
    if projection is None:
        return held_response("operation_projection_basis_changed")
    if record["pending_save"]:
        staged = {**record, "pending_save": False}
        saved, cancelled = _save_owned(owner, record, staged)
        if cancelled:
            from .miniapp_common import MiniAppFlowCancelled
            raise MiniAppFlowCancelled(held_response("checkpoint_persistence_pending"))
        if not saved:
            return held_response("checkpoint_persistence_pending")
        record = staged
    response = commit(projection, projected_result(record), now=record["updated_at"], operation_record=record)
    if not response["extra"].get("persistence_only"):
        response["extra"].update(status="result_committed", persistence_only=True,
                                 persistence_saved=True, daily_exhausted=False)
    return response
