"""Owned trial checkpoints and local recovery, without transport credentials."""

import asyncio
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
import json
import re
import threading
import time
import uuid

from ..persistence import _bounded_miniapp_operation, mark_dirty, save_state
from ..state import TRIAL_OPERATION_MAX_BYTES
from ..webapp_core import sanitize_webapp_secret_text
from .miniapp_common import MiniAppIdentityOwner
from .trial_miniapp import _trial_round_key
from .trial_receipts import normalize_trial_player_id, parse_trial_finish_receipt


STATE_KEY = "trial_operation"
CHECKPOINT_TIMEOUT_SEC = 30
MAX_RECEIPTS = 99
EVIDENCE_FIELDS = {"action_dispatched", "pending", "outcome_unknown", "round_receipts",
                   "request_resolution", "retry_after_sec", "status", "error"}
REQUEST_ACTIONS = {"start", "finish", "next"}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _same(left, right):
    try:
        return _encoded(left) == _encoded(right)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return False


def _key(value, length=64):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value) is not None


def _time(value):
    return type(value) in (int, float) and 0 <= value < 1e12


def _valid_receipt(value):
    if (not isinstance(value, dict) or set(value) != {"round_key", "data"}
            or not _key(value["round_key"]) or not isinstance(value["data"], dict)):
        return False
    data = value["data"]
    parsed = parse_trial_finish_receipt({"ok": True, "result": {"settled_in_app": True, **data}},
                                       challenge_id=value["round_key"])
    projected = parsed["result"]
    if "settled_in_app" not in data:
        projected.pop("settled_in_app", None)
    return parsed["confirmed"] and not parsed["material_error"] and _same(data, projected)


def _valid_pending(value):
    return (isinstance(value, dict) and set(value) == {"action", "entry_key", "challenge_key"}
            and isinstance(value["action"], str)
            and value["action"] in REQUEST_ACTIONS | {"challenge", "entry"}
            and _key(value["entry_key"])
            and (_key(value["challenge_key"]) if value["action"] in {"finish", "challenge"}
                 else value["challenge_key"] == ""))


def valid_checkpoint(value):
    if (not isinstance(value, dict) or set(value) != EVIDENCE_FIELDS | {"version", "phase", "sequence"}
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["sequence"]) is not int or not 0 < value["sequence"] < 10000
            or not isinstance(value["phase"], str) or value["phase"] not in {"intent", "response", "settled", "complete"}
            or any(type(value[key]) is not bool for key in ("action_dispatched", "outcome_unknown"))
            or not _time(value["retry_after_sec"])
            or any(not isinstance(value[key], str) or len(value[key]) > 240 for key in ("status", "error"))):
        return False
    pending = value["pending"]
    if pending != {} and not _valid_pending(pending):
        return False
    if value["outcome_unknown"] != bool(pending and pending["action"] not in {"challenge", "entry"}):
        return False
    resolution = value["request_resolution"]
    if resolution != {} and (
        not isinstance(resolution, dict) or set(resolution) != {"kind", "request"}
        or resolution["kind"] not in ("not_sent", "rejected")
        or not _valid_pending(resolution["request"]) or resolution["request"]["action"] not in REQUEST_ACTIONS
        or pending.get("action") in REQUEST_ACTIONS
    ):
        return False
    receipts = value["round_receipts"]
    if (not isinstance(receipts, list) or len(receipts) > MAX_RECEIPTS
            or any(not _valid_receipt(item) for item in receipts)
            or len({item["round_key"] for item in receipts}) != len(receipts)):
        return False
    if ((value["phase"] == "intent" and (pending.get("action") not in REQUEST_ACTIONS or resolution))
            or (value["phase"] == "settled" and (pending or resolution or not receipts))
            or (not value["action_dispatched"] and (receipts or pending.get("action") in {"challenge", "entry"}
                                                    or resolution.get("kind") == "rejected"))):
        return False
    try:
        encoded = _encoded(value)
        return (len(encoded.encode()) <= TRIAL_OPERATION_MAX_BYTES
                and sanitize_webapp_secret_text(encoded, limit=len(encoded) + 1) == encoded)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return False


def valid_record(value):
    fields = {"version", "operation_id", "identity_id", "account_id", "player_id", "created_at",
              "updated_at", "revision", "checkpoint", "pending_save"}
    if (not isinstance(value, dict) or set(value) != fields or not _bounded_miniapp_operation(value)
            or type(value["version"]) is not int or value["version"] != 1
            or not _key(value["operation_id"], 32)
            or type(value["identity_id"]) is not int or value["identity_id"] <= 0
            or type(value["account_id"]) is not int or value["account_id"] < 0
            or type(value["revision"]) is not int or not 0 < value["revision"] < 100000
            or type(value["pending_save"]) is not bool
            or any(not _time(value[key]) for key in ("created_at", "updated_at"))
            or not 0 < value["created_at"] <= value["updated_at"]
            or not valid_checkpoint(value["checkpoint"])):
        return False
    try:
        player = value["player_id"]
        return ((player is None or type(player) is int and normalize_trial_player_id(player) == player)
                and len(_encoded(value).encode()) <= TRIAL_OPERATION_MAX_BYTES)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return False


def pending(identity):
    value = identity.get(STATE_KEY, {})
    return value != {} and (not valid_record(value) or value["pending_save"]
                           or value["checkpoint"]["phase"] != "complete" or bool(value["checkpoint"]["pending"]))


def _owned(owner, value):
    return (owner is not None and owner.is_current() and valid_record(value)
            and value["identity_id"] == owner.identity_id and value["account_id"] == owner.account_id)


def admission_allowed(owner):
    if owner is None or not owner.is_current():
        return False
    value = owner.identity.get(STATE_KEY, {})
    return value == {} or (_owned(owner, value) and not pending(owner.identity))


def held_result(record, reason="trial_previous_operation_pending"):
    count = len(record["checkpoint"]["round_receipts"]) if valid_record(record) else 0
    return {"ok": False, "status": "operation_pending", "error": reason,
            "data": {}, "events": [], "confirmed_rounds_retained": count,
            "persistence_only": True, "action_dispatched": False}


def _save_owned(owner, previous, staged, *, retain_failed=False):
    if not owner.is_current() or not _same(previous, owner.identity.get(STATE_KEY, {})):
        return False
    owner.identity[STATE_KEY] = deepcopy(staged)
    mark_dirty()
    try:
        saved = save_state() is True
    except Exception:
        saved = False
    if not owner.is_current() or not _same(staged, owner.identity.get(STATE_KEY, {})):
        return False
    if not saved:
        owner.identity[STATE_KEY] = deepcopy(staged if retain_failed else previous)
        if retain_failed:
            owner.identity[STATE_KEY]["pending_save"] = True
        mark_dirty()
    return saved


class CheckpointWriter:
    def __init__(self, owner, *, player_id=None, operation_check=None):
        if not admission_allowed(owner):
            raise ValueError("trial_previous_operation_pending")
        self.owner, self.player_id = owner, normalize_trial_player_id(player_id)
        self.operation_check = operation_check or owner.is_current
        self.loop, self.thread_id = asyncio.get_running_loop(), threading.get_ident()
        self.current = deepcopy(owner.identity.get(STATE_KEY, {}))
        self.operation_id, self.sequence = uuid.uuid4().hex, 0
        self.last_checkpoint, self.closed = None, False
        self.last_request, self.request_previous = {}, {}

    def is_current(self):
        return self.owner.is_current() and _same(self.current, self.owner.identity.get(STATE_KEY, {}))

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
            # A synchronous save already running on the owner loop must drain.
            # Returning early would lose its acknowledgement and strand an unsent intent.
            return future.result() is True
        except RuntimeError:
            return False

    def _transition_allowed(self, checkpoint, added):
        previous = self.last_checkpoint
        target, phase = checkpoint["pending"], checkpoint["phase"]
        resolution = checkpoint["request_resolution"]
        if previous is None:
            return (phase == "intent" and target["action"] == "start"
                    and not checkpoint["round_receipts"] and not checkpoint["action_dispatched"])
        source = previous["pending"]
        if ((previous["action_dispatched"] and not checkpoint["action_dispatched"])
                or checkpoint["retry_after_sec"] < previous["retry_after_sec"]):
            return False
        if previous["phase"] == "complete":
            return phase == "complete" and all(_same(checkpoint[key], previous[key])
                                                for key in EVIDENCE_FIELDS - {"status", "error"})
        if phase == "intent":
            if added or previous["request_resolution"]:
                return False
            action = {"challenge": "finish", "entry": "start"}.get(source.get("action"))
            if action:
                return target == {**source, "action": action}
            return (not source and bool(previous["round_receipts"]) and self.last_request.get("action") == "finish"
                    and target == {"action": "next", "entry_key": self.last_request["entry_key"], "challenge_key": ""})
        if added:
            return (phase in {"settled", "complete"} and len(added) == 1 and source.get("action") == "finish"
                    and added[0]["round_key"] == _trial_round_key(source) and not target and not resolution)
        if phase == "settled":
            return False
        if target == source and resolution == previous["request_resolution"]:
            return True
        if resolution:
            return (previous["phase"] == "intent" and resolution["request"] == source
                    and target == self.request_previous
                    and (resolution["kind"] == "rejected"
                         or checkpoint["action_dispatched"] == previous["action_dispatched"]))
        if not checkpoint["action_dispatched"]:
            return False
        if target.get("action") == "challenge":
            return (previous["phase"] == "intent" and (
                source.get("action") == "next"
                or source.get("action") == "start" and source["entry_key"] == target["entry_key"]
            )) or (previous["phase"] == "settled" and self.last_request.get("action") == "finish"
                   and self.last_request["entry_key"] == target["entry_key"])
        return (target.get("action") == "entry" and previous["phase"] == "intent"
                and source.get("action") == "next")

    def _prepare(self, checkpoint):
        if not self.is_current() or not valid_checkpoint(checkpoint) or checkpoint["sequence"] != self.sequence + 1:
            return None
        if checkpoint["phase"] == "intent" and self.operation_check() is not True:
            return None
        prior = (self.last_checkpoint or {}).get("round_receipts", [])
        receipts = checkpoint["round_receipts"]
        if len(receipts) < len(prior) or not _same(receipts[:len(prior)], prior):
            return None
        added = receipts[len(prior):]
        if not self._transition_allowed(checkpoint, added):
            return None
        now = time.time()
        record = deepcopy(self.current) if self.sequence else {
            "version": 1, "operation_id": self.operation_id, "identity_id": self.owner.identity_id,
            "account_id": self.owner.account_id, "player_id": self.player_id,
            "created_at": now, "revision": 0,
        }
        record.update(updated_at=max(now, record.get("updated_at", record["created_at"])), revision=record["revision"] + 1,
                      checkpoint=deepcopy(checkpoint), pending_save=False)
        return record if valid_record(record) else None

    def _accept(self, checkpoint, *, retain_failed=False):
        if self.last_checkpoint is not None and _same(checkpoint, self.last_checkpoint) and self.is_current():
            return True
        record = self._prepare(checkpoint)
        if record is None:
            return False
        saved = _save_owned(self.owner, self.current, record, retain_failed=retain_failed)
        if saved or retain_failed and self.owner.is_current() and _same(record, {
            **self.owner.identity.get(STATE_KEY, {}), "pending_save": False,
        }):
            if checkpoint["phase"] == "intent":
                self.last_request = deepcopy(checkpoint["pending"])
                self.request_previous = deepcopy((self.last_checkpoint or {}).get("pending", {}))
            self.current = deepcopy(self.owner.identity[STATE_KEY])
            self.last_checkpoint, self.sequence = deepcopy(checkpoint), checkpoint["sequence"]
        return saved

    def finish(self, result):
        if self.closed:
            return False
        self.closed = True
        if not self.is_current() or not isinstance(result, dict):
            return False
        if not self.sequence and not result.get("action_dispatched") and not result.get("round_receipts"):
            return True
        if type(result.get("checkpoint_sequence")) is not int or result["checkpoint_sequence"] != self.sequence:
            return False
        checkpoint = {key: deepcopy(result.get(key)) for key in EVIDENCE_FIELDS}
        checkpoint.update(version=1, phase="complete", sequence=self.sequence + 1)
        return self._accept(checkpoint, retain_failed=True)


def recover_local(identity_id):
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None:
        return None
    record = deepcopy(owner.identity.get(STATE_KEY, {}))
    if record == {}:
        return None
    if not _owned(owner, record):
        return held_result(record, "trial_operation_owner_invalid")
    resaved = record["pending_save"]
    if resaved:
        staged = {**record, "pending_save": False}
        if not _save_owned(owner, record, staged):
            return held_result(record, "trial_persistence_pending")
        record = staged
    checkpoint = record["checkpoint"]
    if checkpoint["pending"]:
        reason = {"challenge": "trial_original_challenge_pending", "entry": "trial_original_entry_pending"}.get(
            checkpoint["pending"]["action"], "trial_previous_outcome_unknown",
        )
        return held_result(record, reason)
    if checkpoint["phase"] == "complete":
        if resaved:
            return {**held_result(record, ""), "status": "recovered", "operation_id": record["operation_id"],
                    "outcome_unknown": False}
        return None
    staged = deepcopy(record)
    status = "partial" if checkpoint["round_receipts"] else "failed"
    staged["checkpoint"].update(phase="complete", sequence=checkpoint["sequence"] + 1, status=status,
                                error="trial_interrupted_local_recovery")
    staged.update(revision=record["revision"] + 1, updated_at=max(time.time(), record["updated_at"]))
    if not valid_record(staged) or not _save_owned(owner, record, staged):
        return held_result(record, "trial_persistence_pending")
    results = [deepcopy(item["data"]) for item in checkpoint["round_receipts"]]
    return {"ok": bool(results), "status": status, "error": "trial_interrupted_local_recovery",
            "data": {"results": results, "settled_count": len(results)}, "events": [],
            "operation_id": record["operation_id"], "persistence_only": True,
            "action_dispatched": False, "outcome_unknown": False}


def finish_result(writer, result):
    result = dict(result or {})
    if writer.finish(result):
        return {**result, "operation_id": writer.operation_id} if writer.sequence else result
    return {**result, "status": "partial" if result.get("ok") is True else "persistence_pending",
            "error": "trial_persistence_pending", "persistence_pending": True}
