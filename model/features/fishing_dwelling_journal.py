"""Single-cast native journal. Not wired to production state or HTTP yet.

The store callback must atomically compare and persist the identity-owned record
before returning True. Settlement projection must share a transaction with its
accounted marker; this module deliberately does not perform that projection.
"""

from copy import deepcopy
from dataclasses import dataclass
import json
import re
import uuid

from . import fishing_dwelling_protocol as protocol


MAX_BYTES = 8192
FIELDS = {"version", "mode", "identity_id", "account_id", "player_id", "cast_id",
          "session_id", "site_id", "model_id", "bait_id", "revision", "phase", "catches"}


def validate(record):
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise protocol.ProtocolError("invalid_native_record")
    if type(record["version"]) is not int or record["version"] != 1 or record["mode"] != protocol.MODE:
        raise protocol.ProtocolError("invalid_native_version")
    for key in ("identity_id", "account_id"):
        protocol._number(record[key], key, 1, 2**53 - 1, integer=True)
    protocol._number(record["player_id"], "player_id", -(2**53 - 1), 2**53 - 1, integer=True)
    if record["player_id"] == 0:
        raise protocol.ProtocolError("invalid_player_id")
    protocol._number(record["revision"], "revision", 1, 100000, integer=True)
    if not isinstance(record["cast_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", record["cast_id"]):
        raise protocol.ProtocolError("invalid_cast_id")
    protocol.placement(record["site_id"], record["model_id"])
    protocol._identifier(record["bait_id"], "bait_id")
    if record["phase"] not in ("cast_pending", "session_owned", "settled"):
        raise protocol.ProtocolError("invalid_native_phase")
    if record["phase"] == "cast_pending":
        if record["session_id"] != "" or record["revision"] != 1:
            raise protocol.ProtocolError("invalid_cast_pending")
    else:
        protocol._identifier(record["session_id"], "session_id")
        if record["revision"] < 2:
            raise protocol.ProtocolError("invalid_session_revision")
    catches = record["catches"]
    if record["phase"] != "settled":
        if catches is not None:
            raise protocol.ProtocolError("premature_catches")
    else:
        protocol._mapping(catches, "catches")
        if len(catches) > 1:
            raise protocol.ProtocolError("invalid_catches")
        for name, count in catches.items():
            protocol._text(name, "fish_name")
            protocol._number(count, "fish_count", 1, 1000, integer=True)
    if len(json.dumps(record, ensure_ascii=True, allow_nan=False).encode()) > MAX_BYTES:
        raise protocol.ProtocolError("native_record_too_large")
    return record


@dataclass(frozen=True)
class Query:
    """Receipt scope, not credentials. Built only after ownership validation."""

    identity_id: int
    account_id: int
    player_id: int
    cast_id: str
    session_id: str
    site_id: str
    revision: int
    action: str


def _query(record, action):
    return Query(*(record[key] for key in ("identity_id", "account_id", "player_id", "cast_id",
                                          "session_id", "site_id", "revision")), action)


def query_payload(record, query):
    validate(record)
    if query.action not in ("cast", "state") or query != _query(record, query.action):
        raise protocol.ProtocolError("stale_native_query")
    if query.action == "cast":
        if record["phase"] != "cast_pending":
            raise protocol.ProtocolError("cast_already_owned")
        return {**protocol.placement(record["site_id"], record["model_id"]),
                "baitItemId": record["bait_id"], "operationId": record["cast_id"], "playerId": record["player_id"]}
    return {"siteId": record["site_id"], "playerId": record["player_id"], "refreshContext": True,
            **({"sessionId": record["session_id"]} if record["session_id"]
               else {"castOperationId": record["cast_id"]})}


def reconcile(record, query, payload):
    """An unscoped context, stale query or missing session cannot close a cast."""
    query_payload(record, query)
    root = protocol._response(payload)
    if "playerId" in root and (type(root["playerId"]) is not int or root["playerId"] != record["player_id"]):
        raise protocol.ProtocolError("player_mismatch")
    remote = protocol._mapping(root.get("session"), "session")
    if "playerId" in remote and (type(remote["playerId"]) is not int or remote["playerId"] != record["player_id"]):
        raise protocol.ProtocolError("player_mismatch")
    for key in ("castOperationId", "operationId"):
        if key in remote and remote[key] != record["cast_id"]:
            raise protocol.ProtocolError("cast_operation_mismatch")
    session_id = record["session_id"] or protocol._identifier(remote.get("sessionId"), "session_id")
    # First binding is only from a captured cast response or exact cast-ID state
    # query. Once bound, every response must match the original session ID.
    parsed = protocol.parse_owned_session(root, session_id=session_id, site_id=record["site_id"])
    settled = parsed["phase"] == "settled"
    next_phase = "settled" if settled else "session_owned"
    catches = parsed.get("catches") if settled else None
    if record["phase"] == "settled":
        if not settled or catches != record["catches"]:
            raise protocol.ProtocolError("settlement_changed")
        return deepcopy(record), parsed
    updated = {**record, "session_id": session_id, "phase": next_phase, "catches": catches}
    if updated != record:
        updated["revision"] += 1
    return validate(updated), parsed


class NativeCastJournal:
    """Persist-before-dispatch barrier; reopening exposes state reads only.

    read_current and compare_and_save access one identity's native record.
    is_owner_current must check the actual identity object and account mapping,
    not just an account username. A failed save leaves this object unusable.
    """

    def __init__(self, *, owner, read_current, compare_and_save, is_owner_current):
        self.owner = tuple(owner)
        if len(self.owner) != 3:
            raise protocol.ProtocolError("invalid_native_owner")
        self.read_current = read_current
        self.compare_and_save = compare_and_save
        self.is_owner_current = is_owner_current
        self.failed = False
        self.record = deepcopy(read_current())
        if self.record != {}:
            validate(self.record)
            if self.owner != tuple(self.record[key] for key in ("identity_id", "account_id", "player_id")):
                raise protocol.ProtocolError("native_owner_mismatch")

    def _check(self):
        current = self.read_current()
        if current != {}:
            validate(current)
        if self.failed or self.is_owner_current() is not True or current != self.record:
            raise protocol.ProtocolError("native_owner_changed")

    def _commit(self, updated):
        self._check()
        validate(updated)
        try:
            if self.compare_and_save(deepcopy(self.record), deepcopy(updated)) is not True:
                raise protocol.ProtocolError("native_save_failed")
            validate(self.read_current())
            if self.is_owner_current() is not True or self.read_current() != updated:
                raise protocol.ProtocolError("native_owner_changed")
        except BaseException:
            self.failed = True
            raise
        self.record = deepcopy(updated)

    def start(self, *, context, site_id, model_id, bait_id):
        self._check()
        if self.record != {}:
            raise protocol.ProtocolError("native_cast_unresolved")
        facts = protocol.parse_context(context)
        if "session" not in context or context["session"] is not None:
            raise protocol.ProtocolError("native_existing_session")
        reason = protocol.cast_block_reason(facts, bait_id)
        if reason:
            raise protocol.ProtocolError(reason)
        record = {"version": 1, "mode": protocol.MODE,
                  "identity_id": self.owner[0], "account_id": self.owner[1], "player_id": self.owner[2],
                  "cast_id": uuid.uuid4().hex, "session_id": "", "site_id": site_id,
                  "model_id": model_id, "bait_id": bait_id, "revision": 1, "phase": "cast_pending", "catches": None}
        self._commit(record)
        query = _query(self.record, "cast")
        return query, query_payload(self.record, query)

    def recovery(self):
        self._check()
        validate(self.record)
        query = _query(self.record, "state")
        return query, query_payload(self.record, query)

    def accept(self, query, payload):
        self._check()
        updated, parsed = reconcile(self.record, query, payload)
        if updated != self.record:
            self._commit(updated)
        self._check()
        return parsed
