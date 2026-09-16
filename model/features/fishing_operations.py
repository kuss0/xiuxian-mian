"""Identity-owned fishing checkpoints; no transport or credential storage."""

import asyncio
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
import hashlib
import json
import re
import threading
import uuid

from ..state import FISHING_OPERATION_MAX_BYTES
from ..persistence import _bounded_miniapp_operation
from . import fishing_miniapp as worker
from . import fishing_runtime as runtime
from .miniapp_common import MiniAppIdentityOwner


STATE_KEY = "fishing_operation"
MAX_BYTES = FISHING_OPERATION_MAX_BYTES
CHECKPOINT_TIMEOUT_SEC = 30
RECOVERY_GAP_SEC = 30 * 60
_EVIDENCE_FIELDS = {
    "action_dispatched", "outcome_unknown", "unresolved_action", "unresolved_round_key",
    "unresolved_round_known", "settled_round_keys", "round_receipts", "retry_after_sec",
}


def pending(identity):
    return identity.get(STATE_KEY, {}) != {}


def _key(value, length=64):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value) is not None


def _time(value):
    return type(value) in (int, float) and 0 <= value < 1e12


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _same(left, right):
    try:
        return _encoded(left) == _encoded(right)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return False


def _valid_receipt(receipt):
    if not isinstance(receipt, dict) or not _key(receipt.get("round_key")):
        return False
    if "projection_error" in receipt:
        return (set(receipt) == {"round_key", "data", "projection_error"}
                and receipt["projection_error"] is True and _same(receipt["data"], {"settled_count": 1}))
    if set(receipt) != {"round_key", "data"} or not isinstance(receipt["data"], dict):
        return False
    try:
        return _same(receipt, worker._fishing_round_receipt(receipt["round_key"], receipt["data"]))
    except (ValueError, TypeError, OverflowError):
        return False


def valid_checkpoint(value):
    if (not isinstance(value, dict) or set(value) != _EVIDENCE_FIELDS | {"version", "phase", "sequence"}
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["sequence"]) is not int or not 0 < value["sequence"] < 10000
            or value["phase"] not in ("intent", "response", "idle", "settled", "complete")
            or any(type(value[key]) is not bool for key in ("action_dispatched", "outcome_unknown", "unresolved_round_known"))
            or not _time(value["retry_after_sec"])):
        return False
    if value["outcome_unknown"]:
        if (value["unresolved_action"] not in ("start", "finish", "next")
                or not _key(value["unresolved_round_key"])
                or (not value["unresolved_round_known"] and value["unresolved_action"] != "next")):
            return False
    elif (value["unresolved_action"] != "" or value["unresolved_round_key"] != ""
          or value["unresolved_round_known"]):
        return False
    receipts, keys = value["round_receipts"], value["settled_round_keys"]
    if (not isinstance(receipts, list) or len(receipts) > worker.FISHING_MAX_DAILY_LIMIT
            or not isinstance(keys, list) or len(keys) != len(receipts)
            or any(not _valid_receipt(receipt) for receipt in receipts)
            or keys != [receipt["round_key"] for receipt in receipts] or len(set(keys)) != len(keys)):
        return False
    try:
        encoded = _encoded(value)
        return (len(encoded.encode()) <= MAX_BYTES
                and runtime.sanitize_webapp_secret_text(encoded, limit=len(encoded) + 1) == encoded)
    except (ValueError, TypeError, OverflowError):
        return False


def valid_record(record):
    fields = {"version", "operation_id", "identity_id", "account_id", "created_at", "updated_at",
              "revision", "checkpoint", "accounted_round_keys", "facts", "plan", "inventory", "retry_at"}
    if (not isinstance(record, dict) or set(record) != fields or not _bounded_miniapp_operation(record)
            or type(record["version"]) is not int or record["version"] != 1
            or not _key(record["operation_id"], 32)
            or any(type(record[key]) is not int or record[key] <= 0 for key in ("identity_id", "account_id", "revision"))
            or record["revision"] >= 100000
            or any(not _time(record[key]) for key in ("created_at", "updated_at", "retry_at"))
            or not 0 < record["created_at"] <= record["updated_at"]
            or any(not _key(record[key]) for key in ("facts", "plan", "inventory"))
            or not valid_checkpoint(record["checkpoint"])):
        return False
    accounted = record["accounted_round_keys"]
    complete = [receipt["round_key"] for receipt in record["checkpoint"]["round_receipts"]
                if not receipt.get("projection_error")]
    if (not isinstance(accounted, list) or len(accounted) > len(complete)
            or any(not _key(key) for key in accounted) or len(set(accounted)) != len(accounted)
            or any(key not in complete for key in accounted)):
        return False
    try:
        return len(_encoded(record).encode()) <= MAX_BYTES
    except (ValueError, TypeError, OverflowError):
        return False


def inventory_basis(identity_id):
    record = runtime.get_storage_bag_records().get(str(identity_id), {})
    return runtime._fishing_projection_digest({"items": record.get("items") or {},
                                               "updated_at": record.get("updated_at", 0)})


def _owned(owner, record):
    return (owner is not None and owner.is_current() and valid_record(record)
            and record["identity_id"] == owner.identity_id and record["account_id"] == owner.account_id)


def reason(record, *, now=None):
    if not valid_record(record):
        return "invalid_operation"
    checkpoint = record["checkpoint"]
    if any(receipt.get("projection_error") for receipt in checkpoint["round_receipts"]):
        return "receipt_projection_pending"
    if checkpoint["outcome_unknown"]:
        return "original_round_required" if checkpoint["unresolved_round_known"] else "unknown_next_round"
    if record["retry_at"] > (runtime.time.time() if now is None else now):
        return "retry_after"
    return "local_receipts_pending"


def response(record, *, why=""):
    why = why or reason(record)
    return {"ok": False, "message": "钓鱼有待恢复记录，未启动新一轮",
            "extra": {"status": "operation_pending", "reason": why, "persistence_only": True}}


def status_text(record):
    return {
        "invalid_operation": "钓鱼恢复记录异常，需核对",
        "receipt_projection_pending": "已结算，鱼获明细待核对",
        "original_round_required": "钓鱼结果待确认，需原轮次入口",
        "unknown_next_round": "下一轮回执缺失，不能重开",
        "retry_after": "等待服务器重试时间",
        "local_receipts_pending": "钓鱼状态待本地恢复",
    }[reason(record)]


class CheckpointWriter:
    def __init__(self, operation, *, operation_check=None, recovery=False):
        self.owner = operation.owner
        self.operation_check = operation_check or operation.is_current
        self.loop = asyncio.get_running_loop()
        self.thread_id = threading.get_ident()
        self.current = deepcopy(self.owner.identity.get(STATE_KEY, {}))
        if (self.current != {} and not recovery) or (recovery and not _owned(self.owner, self.current)):
            raise runtime.FishingMiniAppCommitError("unresolved_operation")
        self.recovery = recovery
        self.prefix = deepcopy(self.current["checkpoint"]["round_receipts"]) if recovery else []
        self.pending_key = self.current["checkpoint"]["unresolved_round_key"] if recovery else ""
        self.sequence = 0
        self.last_checkpoint = None
        self.closed = False

    def is_current(self):
        return (self.owner.is_current() and _same(self.current, self.owner.identity.get(STATE_KEY, {}))
                and self.owner.identity.get("fishing_result_pending", {}) == {})

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
                future.set_result(self._accept(checkpoint))
            except Exception:
                future.set_result(False)

        try:
            self.loop.call_soon_threadsafe(apply)
            return future.result(timeout=CHECKPOINT_TIMEOUT_SEC) is True
        except (RuntimeError, TimeoutError):
            future.cancel()
            return False

    def _prepare(self, checkpoint):
        if not self.is_current() or not valid_checkpoint(checkpoint):
            return None
        if checkpoint["sequence"] != self.sequence + 1:
            return None
        if checkpoint["phase"] == "intent" and (self.recovery or not self.operation_check()):
            return None
        receipts = checkpoint["round_receipts"]
        if not self.current and (checkpoint["phase"] != "intent" or receipts):
            return None
        previous = (self.last_checkpoint or {}).get("round_receipts", [])
        if len(receipts) < len(previous) or not _same(receipts[:len(previous)], previous):
            return None
        added = receipts[len(previous):]
        if added and not self.recovery:
            pending = self.current["checkpoint"]
            if (len(added) != 1 or not pending["outcome_unknown"] or not pending["unresolved_round_known"]
                    or added[0]["round_key"] != pending["unresolved_round_key"]):
                return None
        if self.recovery:
            if (len(receipts) > 1 or any(item["round_key"] != self.pending_key for item in receipts)
                    or (checkpoint["outcome_unknown"] and checkpoint["unresolved_round_key"] != self.pending_key)
                    or (not checkpoint["outcome_unknown"] and not receipts)):
                return None
        merged = deepcopy(checkpoint)
        merged["round_receipts"] = deepcopy(self.prefix) + receipts
        merged["settled_round_keys"] = [item["round_key"] for item in merged["round_receipts"]]
        now = runtime.time.time()
        if self.current:
            record = deepcopy(self.current)
            record["revision"] += 1
        else:
            record = {
                "version": 1, "operation_id": uuid.uuid4().hex,
                "identity_id": self.owner.identity_id, "account_id": self.owner.account_id,
                "created_at": now, "revision": 1, "accounted_round_keys": [],
                "facts": runtime._fishing_result_basis(self.owner.identity, runtime._FISHING_MINIAPP_FACT_KEYS),
                "plan": runtime._fishing_result_basis(self.owner.identity, runtime._FISHING_RESULT_PLAN_KEYS),
                "inventory": inventory_basis(self.owner.identity_id), "retry_at": 0,
            }
        record.update(checkpoint=merged, updated_at=max(now, record["created_at"]),
                      retry_at=max(record["retry_at"], now + checkpoint["retry_after_sec"]
                                   if checkpoint["retry_after_sec"] else 0))
        return record if valid_record(record) else None

    def _accept(self, checkpoint, *, retain_failed=False):
        if (self.last_checkpoint is not None and _same(checkpoint, self.last_checkpoint)
                and self.is_current()):
            return True
        record = self._prepare(checkpoint)
        if record is None:
            return False
        self.owner.identity[STATE_KEY] = deepcopy(record)
        runtime.mark_dirty()
        try:
            saved = runtime.save_state() is True
        except Exception:
            saved = False
        if not saved and not retain_failed:
            self.owner.identity[STATE_KEY] = deepcopy(self.current)
            runtime.mark_dirty()
            return False
        self.current = deepcopy(record)
        self.last_checkpoint = deepcopy(checkpoint)
        self.sequence = checkpoint["sequence"]
        return saved

    def finish(self, result):
        # Final evidence is only from this drained worker, never a fresh entry.
        if self.closed:
            return False
        self.closed = True
        if (not isinstance(result, dict) or type(result.get("checkpoint_sequence")) is not int
                or result["checkpoint_sequence"] != self.sequence):
            return False
        checkpoint = {key: deepcopy(result.get(key)) for key in _EVIDENCE_FIELDS}
        checkpoint.update(version=1, phase="complete", sequence=self.sequence + 1)
        if (not self.current and not checkpoint.get("action_dispatched") and not checkpoint.get("round_receipts")
                and not checkpoint.get("outcome_unknown")):
            return True
        return self._accept(checkpoint, retain_failed=True)


def matching_recovery_launch(record, launch):
    if not valid_record(record) or not isinstance(launch, dict):
        return False
    checkpoint = record["checkpoint"]
    token = launch.get("token")
    return (checkpoint["outcome_unknown"] and checkpoint["unresolved_round_known"]
            and checkpoint["unresolved_round_key"] not in checkpoint["settled_round_keys"]
            and isinstance(token, str) and bool(token.strip())
            and hashlib.sha256(token.strip().encode()).hexdigest() == checkpoint["unresolved_round_key"])


def unaccounted_receipts(record):
    return [item for item in record["checkpoint"]["round_receipts"]
            if not item.get("projection_error") and item["round_key"] not in record["accounted_round_keys"]]


def local_recovery_due(record, now):
    return valid_record(record) and (bool(unaccounted_receipts(record)) or (
        not record["checkpoint"]["outcome_unknown"] and record["retry_at"] <= now
        and not any(item.get("projection_error") for item in record["checkpoint"]["round_receipts"])
    ))


def projection(owner, result, now):
    record = owner.identity.get(STATE_KEY)
    if not _owned(owner, record):
        raise runtime.FishingMiniAppCommitError("invalid_operation_owner")
    if (record["facts"] != runtime._fishing_result_basis(owner.identity, runtime._FISHING_MINIAPP_FACT_KEYS)
            or record["inventory"] != inventory_basis(owner.identity_id)):
        raise runtime.FishingMiniAppCommitError("operation_basis_changed")
    receipts = unaccounted_receipts(record)
    data = {"settled_count": len(receipts), "catches": [], "rewards": [], "daily": {}}
    for receipt in receipts:
        payload = receipt["data"]
        data["catches"].extend(deepcopy(payload["catches"]))
        data["rewards"].extend(deepcopy(payload["rewards"]))
        data["daily"] = deepcopy(payload["daily"])
        worker._merge_loop_gain_fields(data, payload)
    checkpoint = record["checkpoint"]
    held = (checkpoint["outcome_unknown"]
            or any(item.get("projection_error") for item in checkpoint["round_receipts"]))
    prepared = dict(result, data=data, settled_count=len(receipts))
    if held:
        prepared.update(ok=False, status="operation_pending", error=reason(record, now=now))
    prepared["retry_after_sec"] = max(runtime.miniapp_retry_after_sec(result), record["retry_at"] - now, 0)
    reference = {"operation_id": record["operation_id"], "revision": record["revision"],
                 "basis": runtime._fishing_projection_digest(record),
                 "round_keys": [item["round_key"] for item in receipts]}
    plan_current = record["plan"] == runtime._fishing_result_basis(owner.identity, runtime._FISHING_RESULT_PLAN_KEYS)
    return prepared, reference, plan_current, record["created_at"]


def valid_reference(reference):
    return (isinstance(reference, dict) and set(reference) == {"operation_id", "revision", "basis", "round_keys"}
            and _key(reference["operation_id"], 32) and _key(reference["basis"])
            and type(reference["revision"]) is int and 0 < reference["revision"] < 100000
            and isinstance(reference["round_keys"], list)
            and len(reference["round_keys"]) <= worker.FISHING_MAX_DAILY_LIMIT
            and all(_key(key) for key in reference["round_keys"])
            and len(set(reference["round_keys"])) == len(reference["round_keys"]))


def check_reference(owner, reference):
    record = owner.identity.get(STATE_KEY)
    if (not _owned(owner, record) or not valid_reference(reference)
            or reference["operation_id"] != record["operation_id"] or reference["revision"] != record["revision"]
            or reference["basis"] != runtime._fishing_projection_digest(record)
            or reference["round_keys"] != [item["round_key"] for item in unaccounted_receipts(record)]):
        raise runtime.FishingMiniAppCommitError("operation_reference_changed")


def advance_accounting(owner, reference):
    record = deepcopy(owner.identity[STATE_KEY])
    record["accounted_round_keys"].extend(reference["round_keys"])
    now = runtime.time.time()
    record.update(revision=record["revision"] + 1, updated_at=max(record["updated_at"], now),
                  facts=runtime._fishing_result_basis(owner.identity, runtime._FISHING_MINIAPP_FACT_KEYS),
                  inventory=inventory_basis(owner.identity_id),
                  plan=runtime._fishing_result_basis(owner.identity, runtime._FISHING_RESULT_PLAN_KEYS))
    if not valid_record(record):
        raise runtime.FishingMiniAppCommitError("invalid_operation_advancement")
    if local_recovery_due(record, now) and not unaccounted_receipts(record):
        record = {}
    owner.identity[STATE_KEY] = record


def recover_local(identity_id, now):
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None or not pending(owner.identity):
        return None
    record = owner.identity[STATE_KEY]
    if not _owned(owner, record):
        return response(record, why="invalid_operation_owner")
    if local_recovery_due(record, now):
        try:
            with runtime.use_identity(identity_id):
                summary = runtime._apply_fishing_miniapp_result(
                    {"ok": True, "status": "settled", "data": {}}, now,
                )
        except runtime.FishingMiniAppCommitError as exc:
            return response(record, why=exc.reason)
        if not pending(owner.identity):
            return runtime._fishing_result_commit_response(summary=summary)
    return response(owner.identity[STATE_KEY])


async def recover_entry(identity_id, launch, operation_check):
    """Caller holds the fishing game lock. Never refresh an unrelated entry."""
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None or not pending(owner.identity):
        return None
    record = deepcopy(owner.identity[STATE_KEY])
    now = runtime.time.time()
    if not _owned(owner, record) or not matching_recovery_launch(record, launch):
        return response(record)
    if record["retry_at"] > now:
        return response(record, why="retry_after")
    if not operation_check() or owner.identity.get("fishing_result_pending", {}) != {}:
        return response(record, why="recovery_not_admitted")
    admitted = deepcopy(record)
    admitted.update(retry_at=now + RECOVERY_GAP_SEC, updated_at=max(record["updated_at"], now),
                    revision=record["revision"] + 1)
    if not valid_record(admitted):
        return response(record, why="invalid_recovery_admission")
    owner.identity[STATE_KEY] = admitted
    try:
        saved = runtime.save_state() is True
    except Exception:
        saved = False
    if not saved:
        owner.identity[STATE_KEY] = record
        runtime.mark_dirty()
        return response(record, why="persistence_pending")
    operation = runtime.FishingMiniAppOperation.capture(identity_id)
    writer = CheckpointWriter(operation, operation_check=operation_check, recovery=True)
    checkpoint = record["checkpoint"]
    cancelled = None
    try:
        result = await worker.run_fishing_miniapp_recovery_production_flow(
            identity_id, token=launch["token"], webview_url=launch.get("webview_url", ""),
            expected_round_key=checkpoint["unresolved_round_key"],
            pending_action=checkpoint["unresolved_action"], pending_round_known=checkpoint["unresolved_round_known"],
            settled_round_keys=checkpoint["settled_round_keys"],
            operation_check=lambda: operation_check() and writer.is_current(), checkpoint=writer,
        )
    except runtime.MiniAppFlowCancelled as exc:
        cancelled = exc
        result = exc.result or {}
    if not writer.is_current():
        if cancelled is not None:
            raise runtime.MiniAppFlowCancelled() from None
        retained = response(record, why="operation_changed")
        retained["extra"].update(persistence_only=False, recovery_result_only=True)
        return retained
    writer.finish(result)
    retained = recover_local(identity_id, runtime.time.time()) or response(record)
    retained["extra"].update(persistence_only=False, recovery_result_only=True)
    if cancelled is not None:
        raise runtime.MiniAppFlowCancelled(retained) from None
    return retained
