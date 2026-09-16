"""Owned tree checkpoints and local-only publication recovery."""

import asyncio
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
import hashlib
import json
import re
import threading
import time
import uuid

from ..miniapp_state import sanitize_miniapp_state
from ..persistence import _bounded_miniapp_operation, mark_dirty, save_state
from ..state import TREE_OPERATION_MAX_BYTES, get_miniapp_state_records, set_miniapp_state_records
from ..webapp_core import require_miniapp_operation, sanitize_webapp_secret_text
from .miniapp_common import MiniAppIdentityOwner
from .tree_receipts import tree_integer


STATE_KEY = "tree_operation"
CHECKPOINT_TIMEOUT_SEC = 30
_ACTIVE = {}
_RESULT_FIELDS = {"ok", "status", "error", "data", "open_run", "outcome_unknown", "retry_after_sec"}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _same(left, right):
    try:
        return _encoded(left) == _encoded(right)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _key(value, size=64):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(size) + "}", value) is not None


def _time(value):
    return type(value) in (int, float) and 0 <= value < 1e12


def project_result(result):
    return {key: sanitize_miniapp_state(result.get("data", {})) if key == "data"
            else sanitize_webapp_secret_text(result.get(key, ""), limit=240) if key in {"status", "error"}
            else deepcopy(result.get(key, 0 if key == "retry_after_sec" else False)) for key in _RESULT_FIELDS}


def _counts(value):
    return (isinstance(value, dict) and len(value) <= 128 and all(
        isinstance(name, str) and 0 < len(name) <= 80 and type(amount) is int
        and tree_integer(amount) is not None and amount > 0 for name, amount in value.items()))


def _rewards(value):
    return (isinstance(value, dict) and set(value) <= {"items", "gains"}
            and all(_counts(counts) for counts in value.values()))


def _facts(result):
    data = result["data"]
    facts = list(data.get("runs", []))
    submit = data.get("submit", {})
    if submit.get("confirmed") is True:
        facts.append({"round_key": submit["round_key"], "score": submit["score"],
                      "rewards": data.get("rewards", {})})
    return facts


def _valid_result(value):
    if (not isinstance(value, dict) or set(value) != _RESULT_FIELDS
            or any(type(value[key]) is not bool for key in ("ok", "open_run", "outcome_unknown"))
            or not _time(value["retry_after_sec"])
            or any(not isinstance(value[key], str) or len(value[key]) > 240 for key in ("status", "error"))
            or not isinstance(value["data"], dict)):
        return False
    data = value["data"]
    if not _same(sanitize_miniapp_state(data), data) or not _rewards(data.get("rewards", {})):
        return False
    runs, partials, submit = data.get("runs", []), data.get("partial_receipts", []), data.get("submit", {})
    if not isinstance(runs, list) or len(runs) > 64 or not isinstance(partials, list) or len(partials) > 1:
        return False
    for run in runs:
        if (not isinstance(run, dict) or not _key(run.get("round_key")) or run.get("mode") not in ("jump", "fly")
                or type(run.get("score")) is not int or tree_integer(run["score"]) is None
                or not _rewards(run.get("rewards", {}))):
            return False
    if not isinstance(submit, dict) or submit and (
        type(submit.get("confirmed")) is not bool or not _key(submit.get("round_key"))
        or submit["confirmed"] and (type(submit.get("score")) is not int or tree_integer(submit["score"]) is None)
    ):
        return False
    for partial in partials:
        if (not isinstance(partial, dict) or not _key(partial.get("round_key"))
                or not _rewards(partial.get("rewards", {}))):
            return False
    facts = _facts(value)
    totals = {"items": {}, "gains": {}}
    for fact in facts + partials:
        for kind, counts in fact.get("rewards", {}).items():
            for name, amount in counts.items():
                totals[kind][name] = totals[kind].get(name, 0) + amount
    return (len({fact["round_key"] for fact in facts}) == len(facts)
            and all(data.get("rewards", {}).get(kind, {}) == counts for kind, counts in totals.items()))


def _request(value):
    return (isinstance(value, dict) and set(value) == {"action", "entry_key", "mode", "round_key"}
            and value["action"] in ("run_start", "allocated", "run_submit")
            and value["mode"] in ("jump", "fly") and _key(value["entry_key"])
            and (value["round_key"] == "" if value["action"] == "run_start" else _key(value["round_key"])))


def valid_checkpoint(value):
    if (not isinstance(value, dict) or set(value) != {"version", "sequence", "phase", "pending", "resolution", "dispatched", "result"}
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["sequence"]) is not int or not 0 < value["sequence"] < 10000
            or value["phase"] not in ("intent", "response", "settled", "complete")
            or type(value["dispatched"]) is not bool or not _valid_result(value["result"])):
        return False
    pending, resolution, result = value["pending"], value["resolution"], value["result"]
    if pending != {} and not _request(pending):
        return False
    if resolution != {} and (
        not isinstance(resolution, dict) or set(resolution) != {"kind", "request"}
        or resolution["kind"] not in ("not_sent", "rejected") or not _request(resolution["request"])
        or resolution["request"]["action"] == "allocated"
    ):
        return False
    if (value["phase"] == "intent" and (pending.get("action") not in ("run_start", "run_submit") or resolution)
            or value["phase"] == "settled" and (pending or resolution or not _facts(result))
            or pending.get("action") == "allocated" and not result["open_run"]
            or not pending and (result["open_run"] or result["outcome_unknown"])
            or result["ok"] and pending and result["status"] != "prepared"):
        return False
    try:
        encoded = _encoded(value)
        return (_bounded_miniapp_operation(value) and len(encoded.encode()) <= TREE_OPERATION_MAX_BYTES
                and sanitize_webapp_secret_text(encoded, limit=len(encoded) + 1) == encoded)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def valid_record(value):
    if (not isinstance(value, dict) or set(value) != {"version", "operation_id", "identity_id", "account_id", "auth",
            "created_at", "updated_at", "checkpoint", "pending_save", "published", "projection_before"}
            or type(value["version"]) is not int or value["version"] != 1 or not _key(value["operation_id"], 32)
            or type(value["identity_id"]) is not int or value["identity_id"] <= 0
            or type(value["account_id"]) is not int or value["account_id"] < 0
            or any(type(value[key]) is not bool for key in ("pending_save", "published"))
            or value["pending_save"] and value["published"]
            or any(not _time(value[key]) for key in ("created_at", "updated_at"))
            or not 0 < value["created_at"] <= value["updated_at"] or not _key(value["projection_before"])
            or not valid_checkpoint(value["checkpoint"])):
        return False
    auth = value["auth"]
    if (not isinstance(auth, dict) or set(auth) != {"kind", "day_key"} or auth["kind"] not in ("daily", "manual")
            or not isinstance(auth["day_key"], str) or len(auth["day_key"]) > 32):
        return False
    try:
        return _bounded_miniapp_operation(value) and len(_encoded(value).encode()) <= TREE_OPERATION_MAX_BYTES
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _owned(owner, record):
    return (owner is not None and owner.is_current() and valid_record(record)
            and record["identity_id"] == owner.identity_id and record["account_id"] == owner.account_id)


def admission_allowed(owner):
    if owner is None or not owner.is_current():
        return False
    record = owner.identity.get(STATE_KEY, {})
    return record == {} or (_owned(owner, record) and record["published"] and not record["pending_save"]
                           and not record["checkpoint"]["pending"])


def _miniapp(owner):
    return get_miniapp_state_records().get(f"{owner.identity_id}:tree", {})


def _restore_miniapp(owner, before):
    records = dict(get_miniapp_state_records())
    key = f"{owner.identity_id}:tree"
    if before:
        records[key] = before
    else:
        records.pop(key, None)
    set_miniapp_state_records(records)


def _save(owner, previous, record, *, retain_failed=False):
    if not owner.is_current() or not _same(previous, owner.identity.get(STATE_KEY, {})):
        return False
    owner.identity[STATE_KEY] = deepcopy(record)
    mark_dirty()
    try:
        saved = save_state() is True
    except Exception:
        saved = False
    if not owner.is_current() or not _same(record, owner.identity.get(STATE_KEY, {})):
        return False
    if not saved:
        owner.identity[STATE_KEY] = deepcopy(record if retain_failed else previous)
        if retain_failed:
            owner.identity[STATE_KEY]["pending_save"] = True
        mark_dirty()
    return saved


class CheckpointWriter:
    def __init__(self, owner, auth, *, operation_check):
        if not admission_allowed(owner) or owner.identity_id in _ACTIVE:
            raise ValueError("tree_previous_operation_pending")
        self.owner, self.operation_check = owner, operation_check
        self.auth = {"kind": auth.get("kind", "manual"), "day_key": auth.get("day_key", "")}
        self.current = deepcopy(owner.identity.get(STATE_KEY, {}))
        self.projection_before = digest(_miniapp(owner))
        self.loop, self.thread_id = asyncio.get_running_loop(), threading.get_ident()
        self.operation_id, self.sequence = uuid.uuid4().hex, 0
        self.last_checkpoint, self.request_previous = None, {}
        self.closed = False
        _ACTIVE[owner.identity_id] = self

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
            return future.result() is True
        except RuntimeError:
            return False

    def _transition(self, value):
        before = self.last_checkpoint
        phase, target, result = value["phase"], value["pending"], value["result"]
        if before is None:
            return phase == "intent" and target["action"] == "run_start" and not _facts(result) and not value["dispatched"]
        prior, facts = _facts(before["result"]), _facts(result)
        if (len(facts) < len(prior) or not _same(facts[:len(prior)], prior)
                or before["dispatched"] and not value["dispatched"] or before["phase"] == "complete"):
            return False
        added, source = facts[len(prior):], before["pending"]
        if phase == "intent":
            if added or before["resolution"]:
                return False
            if source.get("action") == "allocated":
                return target == {**source, "action": "run_submit"}
            return not source and bool(prior) and target["action"] == "run_start"
        if added:
            return (phase in ("settled", "complete") and len(added) == 1 and source.get("action") == "run_submit"
                    and added[0]["round_key"] == source["round_key"] and not target and not value["resolution"])
        if phase == "settled":
            return False
        if target == source and value["resolution"] == before["resolution"]:
            return True
        resolution = value["resolution"]
        if resolution:
            return (before["phase"] == "intent" and resolution["request"] == source
                    and target == self.request_previous
                    and (resolution["kind"] == "rejected" and value["dispatched"]
                         or resolution["kind"] == "not_sent" and value["dispatched"] == before["dispatched"]))
        return (before["phase"] == "intent" and source.get("action") == "run_start"
                and target.get("action") == "allocated" and target["entry_key"] == source["entry_key"]
                and target["mode"] == source["mode"] and value["dispatched"])

    def _accept(self, checkpoint, *, retain_failed=False):
        if self.last_checkpoint is not None and _same(checkpoint, self.last_checkpoint) and self.is_current():
            return not self.current["pending_save"]
        if (not self.is_current() or not valid_checkpoint(checkpoint) or checkpoint["sequence"] != self.sequence + 1
                or not self._transition(checkpoint)):
            return False
        if checkpoint["phase"] == "intent":
            require_miniapp_operation(self.operation_check)
        now = time.time()
        record = deepcopy(self.current) if self.sequence else {
            "version": 1, "operation_id": self.operation_id, "identity_id": self.owner.identity_id,
            "account_id": self.owner.account_id, "auth": self.auth, "created_at": now,
            "projection_before": self.projection_before,
        }
        record.update(updated_at=max(now, record["created_at"]), checkpoint=deepcopy(checkpoint), pending_save=False, published=False)
        if not valid_record(record):
            return False
        saved = _save(self.owner, self.current, record, retain_failed=retain_failed)
        if saved or retain_failed and _same({**record, "pending_save": True}, self.owner.identity.get(STATE_KEY)):
            if checkpoint["phase"] == "intent":
                self.request_previous = deepcopy((self.last_checkpoint or {}).get("pending", {}))
            self.current = deepcopy(self.owner.identity[STATE_KEY])
            self.last_checkpoint, self.sequence = deepcopy(checkpoint), checkpoint["sequence"]
        return saved

    def close(self, result):
        self.closed = True
        if _ACTIVE.get(self.owner.identity_id) is self:
            _ACTIVE.pop(self.owner.identity_id)
        if not self.is_current():
            return False
        if not self.sequence:
            return True
        checkpoint = result.get("operation_evidence")
        return isinstance(checkpoint, dict) and self._accept(checkpoint, retain_failed=True)

    def publish(self, callback):
        return _publish(self.owner, self.current, callback)


def projected_result(record):
    checkpoint = record["checkpoint"]
    result = deepcopy(checkpoint["result"])
    if checkpoint["phase"] != "complete" or (
        not checkpoint["pending"] and result["status"] in ("cancelled", "persistence_pending")
        and "checkpoint" in result["error"]
    ):
        unknown = bool(checkpoint["pending"])
        result.update(ok=False, status="result_unknown" if unknown else "interrupted",
                      error="tree_interrupted_local_recovery", outcome_unknown=unknown,
                      open_run=bool(checkpoint["pending"]))
        if "phase" in result["data"]:
            result["data"]["phase"] = "unknown" if unknown else "blocked"
    return result


def _publish(owner, record, callback):
    if (not _owned(owner, record) or not _same(record, owner.identity.get(STATE_KEY, {}))
            or record["pending_save"] or digest(_miniapp(owner)) != record["projection_before"]):
        return False
    before = deepcopy(_miniapp(owner))
    staged = {**record, "published": True}
    try:
        callback(projected_result(record))
        projection = deepcopy(_miniapp(owner))
        saved = _save(owner, record, staged)
    except Exception:
        projection, saved = deepcopy(_miniapp(owner)), False
    if not saved and owner.is_current() and _same(_miniapp(owner), projection):
        _restore_miniapp(owner, before)
        mark_dirty()
    return saved


def recover_local(identity_id, callback):
    if identity_id in _ACTIVE:
        return None
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None:
        return None
    record = deepcopy(owner.identity.get(STATE_KEY, {}))
    if not _owned(owner, record) or record["published"] and not record["pending_save"]:
        return None
    if record["pending_save"]:
        staged = {**record, "pending_save": False}
        if not _save(owner, record, staged):
            return None
        record = staged
    if not _publish(owner, record, lambda result: callback(owner, record, result)):
        return None
    return {"status": "recovered", "identity_id": identity_id, "persistence_only": True,
            "outcome_unknown": bool(record["checkpoint"]["pending"])}
