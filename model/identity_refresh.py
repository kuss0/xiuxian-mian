"""Ownership for one explicit profile refresh and its bounded read retries."""

import math
from types import SimpleNamespace
from uuid import uuid4

from .config import format_battle_power_command, format_identity_info_command
from .message_keys import find_message_key, message_key_parts
from .persistence import mark_dirty
from .state import (
    get_game_group_id, get_identity_account, get_identity_state, has_identity,
)


SOURCE = "identity_info_refresh"
COMMANDS = {"primary": format_identity_info_command(), "followup": format_battle_power_command()}
STATUSES = {"sending", "sent", "unknown", "unsent", "complete", "timeout"}
PROFILE_TEXT_FIELDS = {
    "daohao", "realm", "spiritual_root_type", "spiritual_root_attrs",
    "replica_professions", "sect_name", "battle_power_text",
}
PROFILE_NUMBER_FIELDS = {"xiuwei_current", "xiuwei_max", "battle_power_value"}
_owners = {}
_live_calls = set()


def number(value):
    if type(value) not in {int, float}:
        return 0.0
    try:
        return float(value) if math.isfinite(value) else 0.0
    except (OverflowError, ValueError):
        return 0.0


def request_for(identity_id):
    if not has_identity(identity_id):
        return None
    request = get_identity_state(identity_id).get("identity_info_refresh")
    if not isinstance(request, dict) or not request:
        return None
    if (
        type(request.get("version")) is not int or request["version"] != 1
        or len(request) > 12
        or not isinstance(request.get("id"), str) or len(request["id"]) != 32
        or type(request.get("identity_id")) is not int or request["identity_id"] != identity_id
        or type(request.get("account_id")) is not int or request["account_id"] <= 0
        or request["account_id"] != get_identity_account(identity_id)
        or type(request.get("chat_id")) is not int or not request["chat_id"]
        or number(request.get("requested_at")) <= 0
        or not isinstance(request.get("status"), str) or request["status"] not in {"active", "complete", "failed"}
        or not isinstance(request.get("commands"), list) or len(request["commands"]) > 4
    ):
        return None
    seen = set()
    for record in request["commands"]:
        if not isinstance(record, dict) or len(record) > 16:
            return None
        kind, attempt = record.get("kind"), record.get("attempt")
        if (
            not isinstance(kind, str) or kind not in COMMANDS
            or type(attempt) is not int or attempt not in {0, 1}
            or (kind, attempt) in seen
            or record.get("command") != COMMANDS[kind]
            or record.get("op_id") != f"{request['id']}:{kind}:{attempt}"
            or not isinstance(record.get("status"), str) or record["status"] not in STATUSES
            or number(record.get("started_at")) < request["requested_at"] - 1
            or any(
                type(record.get(key)) not in {int, float} or number(record[key]) != record[key] or record[key] < 0
                for key in ("started_at", "sent_at", "dispatch_at", "reply_at")
            )
            or type(record.get("msg_id")) is not int or record["msg_id"] < 0
            or (record["msg_id"] and number(record.get("sent_at")) < record["started_at"] - 1)
            or (record["status"] in {"sent", "complete", "timeout"} and not record["msg_id"])
            or (record["msg_id"] and not record["started_at"] - 1 <= record["dispatch_at"] <= record["sent_at"] + 1)
            or not isinstance(record.get("reply_ids"), list) or len(record["reply_ids"]) > 8
            or any(type(msg_id) is not int or msg_id <= 0 for msg_id in record["reply_ids"])
        ):
            return None
        payload = record.get("payload", {})
        if (
            not isinstance(payload, dict) or payload.keys() - PROFILE_TEXT_FIELDS - PROFILE_NUMBER_FIELDS
            or any(not isinstance(value, str) or len(value) > 512 for key, value in payload.items() if key in PROFILE_TEXT_FIELDS)
            or any(type(value) is not int or value < 0 for key, value in payload.items() if key in PROFILE_NUMBER_FIELDS)
            or (record["status"] == "complete" and (record["reply_at"] <= 0 or not payload))
        ):
            return None
        seen.add((kind, attempt))
    return request


def capture(identity_id):
    request = request_for(identity_id)
    if request is None:
        return None
    signature = tuple(request[key] for key in ("id", "identity_id", "account_id", "chat_id", "requested_at"))
    return identity_id, get_identity_state(identity_id), request, signature


def owns(owner):
    if owner is None:
        return False
    identity_id, identity, request, signature = owner
    return bool(
        has_identity(identity_id) and get_identity_state(identity_id) is identity
        and request_for(identity_id) is request
        and tuple(request[key] for key in ("id", "identity_id", "account_id", "chat_id", "requested_at")) == signature
    )


def begin(identity_id, now):
    account_id, chat_id = get_identity_account(identity_id), get_game_group_id()
    if not account_id or not chat_id or number(now) <= 0:
        return None
    identity = get_identity_state(identity_id)
    previous = request_for(identity_id)
    if previous is not None:
        for key, pending in identity["pending_tasks"].items():
            if owned_pending(previous, key, pending) is not None:
                pending["max_retry"] = 0
    request = {
        "version": 1, "id": uuid4().hex, "identity_id": identity_id,
        "account_id": account_id, "chat_id": chat_id, "requested_at": now,
        "status": "active", "commands": [],
    }
    identity.update(
        identity_info_refresh=request, identity_info_last_requested_at=now,
        identity_info_last_error="", identity_info_primary_payload={},
        identity_info_followup_due_at=0, identity_info_reply_msg_ids=[], last_identity_info_msg_id=0,
    )
    for old_id, owner in list(_owners.items()):
        if not owns(owner):
            _owners.pop(old_id, None)
    _owners[identity_id] = capture(identity_id)
    mark_dirty()
    return request


def reserve(request, kind, now, *, attempt=0):
    if request_for(request["identity_id"]) is not request or request["status"] != "active":
        return None
    if any(record["kind"] == kind and record["attempt"] == attempt for record in request["commands"]):
        return None
    record = {
        "kind": kind, "attempt": attempt, "command": COMMANDS[kind],
        "op_id": f"{request['id']}:{kind}:{attempt}", "status": "sending",
        "started_at": max(now, request["requested_at"]), "msg_id": 0,
        "sent_at": 0.0, "dispatch_at": 0.0, "reply_at": 0.0, "reply_ids": [],
    }
    request["commands"].append(record)
    mark_dirty()
    return record


def live_call(request, active):
    if active:
        _live_calls.add(request["id"])
    else:
        _live_calls.discard(request["id"])


def has_live_call(request):
    return request["id"] in _live_calls


def sync_tracking(request):
    identity = get_identity_state(request["identity_id"])
    ids = []
    if request["status"] == "active":
        for record in request["commands"]:
            if record["msg_id"]:
                ids.append(record["msg_id"])
            ids.extend(record["reply_ids"])
    identity["identity_info_reply_msg_ids"] = sorted(set(ids))
    identity["last_identity_info_msg_id"] = ids[-1] if ids else 0
    mark_dirty()


def record_for(request, op_id):
    return next((record for record in request["commands"] if record["op_id"] == op_id), None)


def note_receipt(identity_id, op_id, msg):
    request = request_for(identity_id)
    if request is None:
        return False
    owner = _owners.get(identity_id)
    if owner is not None and owner[2]["id"] == request["id"] and not owns(owner):
        return False
    record = record_for(request, op_id)
    msg_id = getattr(msg, "id", 0)
    chat_id = getattr(msg, "chat_id", 0)
    sent_at = number(getattr(msg, "sent_at", 0))
    if (
        record is None or type(msg_id) is not int or msg_id <= 0
        or chat_id != request["chat_id"] or sent_at < record["started_at"] - 1
        or (record["msg_id"] and record["msg_id"] != msg_id)
    ):
        return False
    if record["msg_id"]:
        return True
    dispatch_at = number(getattr(msg, "send_started_at", 0)) or record["started_at"]
    if not record["started_at"] - 1 <= dispatch_at <= sent_at + 1:
        return False
    record.update(msg_id=msg_id, sent_at=sent_at, dispatch_at=dispatch_at)
    if record["status"] in {"sending", "unknown", "unsent"}:
        record["status"] = "sent"
    sync_tracking(request)
    return True


def observe_sent(identity_id, command, *, now, msg_id, **metadata):
    if metadata.get("source_module") != SOURCE:
        return
    request = request_for(identity_id)
    if request is None or metadata.get("chain_id") != request["id"]:
        return
    record = record_for(request, metadata.get("op_id"))
    if record is None or record["command"] != command:
        return
    note_receipt(identity_id, record["op_id"], SimpleNamespace(
        id=msg_id, chat_id=metadata.get("game_group_id"), sent_at=now,
        send_started_at=now - number(metadata.get("send_elapsed_sec")),
    ))


def operation_owner_check(owner, record):
    request = owner[2]
    fields = ("kind", "attempt", "command", "op_id", "started_at")
    signature = tuple(record[key] for key in fields)
    op_id = record["op_id"]

    def check():
        return bool(
            owns(owner) and record_for(request, op_id) is record
            and tuple(record.get(key) for key in fields) == signature
        )

    return check


def send_args(owner, record):
    request = owner[2]
    owns_operation = operation_owner_check(owner, record)
    return {
        "source_module": SOURCE, "chain_id": request["id"], "op_id": record["op_id"],
        "target_chat_id": request["chat_id"],
        "operation_check": lambda: owns_operation() and request["status"] == "active" and record["status"] == "sending",
    }


def pre_send_guard(command, send_as_id, *, priority=None, intent=None, now=None):
    if not isinstance(intent, dict) or intent.get("source_module") != SOURCE:
        return True
    request = request_for(send_as_id)
    record = record_for(request, intent.get("op_id")) if request else None
    return bool(
        request and record and request["id"] == intent.get("chain_id")
        and request["status"] == "active" and record["status"] == "sending"
        and record["command"] == command
    )


def owned_pending(request, key, pending):
    if not isinstance(pending, dict) or pending.get("source_module") != SOURCE:
        return None
    if pending.get("chain_id") != request["id"] or pending.get("account_id", request["account_id"]) != request["account_id"]:
        return None
    record = record_for(request, pending.get("op_id"))
    try:
        chat, msg_id = message_key_parts(key, pending)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        record is not None and record["command"] == pending.get("cmd")
        and chat == request["chat_id"] and msg_id == record["msg_id"] and msg_id > 0
    ):
        return record
    return None


def clear_pending(request, *, kind=None):
    identity = get_identity_state(request["identity_id"])
    for key, pending in list(identity["pending_tasks"].items()):
        record = owned_pending(request, key, pending)
        if record is not None and (kind is None or record["kind"] == kind):
            identity["pending_tasks"].pop(key, None)
    mark_dirty()


def finish(request, *, error="", clear=True):
    identity = get_identity_state(request["identity_id"])
    request["status"] = "failed" if error else "complete"
    identity["identity_info_last_error"] = error
    identity["identity_info_followup_due_at"] = 0
    if clear:
        clear_pending(request)
    sync_tracking(request)


def retry_plan(identity_id, key, pending, now):
    request = request_for(identity_id)
    record = owned_pending(request, key, pending) if request else None
    if (
        request is None or record is None or request["status"] != "active" or record["status"] != "sent"
        or number(pending.get("reply_recovery_retry_at")) > now
    ):
        return None
    if record["attempt"] != 0:
        return None
    retry = next((item for item in request["commands"] if item["kind"] == record["kind"] and item["attempt"] == 1), None)
    if retry is not None:
        if retry["status"] != "unsent":
            return None
        retry.update(status="sending", started_at=now)
    else:
        retry = reserve(request, record["kind"], now, attempt=1)
    if retry is None:
        return None
    owner = capture(identity_id)
    _owners[identity_id] = owner
    args = send_args(owner, retry)
    check = args["operation_check"]
    snapshot = dict(pending)
    args["operation_check"] = lambda: bool(
        check() and owner[1]["pending_tasks"].get(key) is pending and pending == snapshot
    )
    mark_dirty()
    return owner, retry, args, operation_owner_check(owner, retry)


def fail_send(owner, record, *, definitely_unsent=False):
    if not owns(owner) or record["status"] not in {"sending", "unknown"} or record["msg_id"]:
        return
    record["status"] = "unsent" if definitely_unsent else "unknown"
    mark_dirty()


def timeout(identity_id, key, pending, error):
    request = request_for(identity_id)
    record = owned_pending(request, key, pending) if request else None
    if record is not None and record["status"] == "sent":
        record["status"] = "timeout"
        finish(request, error=error)


def trigger_keys(request):
    records = get_identity_state(request["identity_id"])["my_msg_ids"]
    return [
        (request["chat_id"], record["msg_id"])
        for record in request["commands"] if record["msg_id"]
        and find_message_key(records, record["msg_id"], chat_id=request["chat_id"]) is not None
    ]
