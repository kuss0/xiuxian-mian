"""Native fishing journal; mutations are persisted before dispatch.

The store callback must atomically compare and persist the identity-owned record
before returning True. Settlement projection must share a transaction with its
accounted marker; this module deliberately does not perform that projection.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import time
import uuid

from . import fishing_dwelling_protocol as protocol


MAX_BYTES = 128 * 1024
FIELDS = {"version", "mode", "identity_id", "account_id", "player_id", "cast_id",
          "session_id", "site_id", "model_id", "bait_id", "revision", "phase", "catches",
          "pending_action", "pending_payload", "remote", "created_at", "projection_basis", "settlement_quota"}
_RANK = {"casting": 0, "waiting": 0, "bite": 0, "fighting": 1, "settling": 2, "settled": 3}


def _proof(challenge, proof, *, final, details=None):
    config, _, elapsed, old_events = protocol._fight_state(challenge)
    protocol._mapping(proof, "proof")
    fields = {"mode", "challengeId", "durationMs", "events"} | ({"landed"} if final else set())
    if (set(proof) != fields or proof["mode"] != "xianxiaFishingV2"
            or proof["challengeId"] != challenge["challengeId"]
            or (final and type(proof["landed"]) is not bool)):
        raise protocol.ProtocolError("invalid_native_proof")
    duration = protocol._number(proof["durationMs"], "proof_duration", elapsed, config["maximum"], integer=True)
    if duration % 20 or (final and proof["landed"] and duration < config["minimum"]):
        raise protocol.ProtocolError("invalid_native_proof_duration")
    events = proof["events"]
    if not isinstance(events, list) or len(events) > config["event_limit"] or events[:len(old_events)] != old_events:
        raise protocol.ProtocolError("invalid_native_proof_prefix")
    prior = 0
    for event in events:
        protocol._mapping(event, "event")
        if set(event) != {"t", "holding"} or type(event["holding"]) is not bool:
            raise protocol.ProtocolError("invalid_native_proof_event")
        prior = protocol._number(event["t"], "proof_event_time", prior, duration, integer=True)
    if not final:
        if duration <= elapsed:
            raise protocol.ProtocolError("checkpoint_not_advanced")
        _, normalized, _, _ = protocol._fight_state({**challenge, "checkpoint": {
            "durationMs": duration, "events": events, "details": details,
        }})
        if set(details) != set(normalized):
            raise protocol.ProtocolError("invalid_native_checkpoint_fields")


def _validate_remote(record):
    remote = record["remote"]
    if record["phase"] == "cast_pending":
        if remote is not None:
            raise protocol.ProtocolError("premature_native_remote")
        return
    protocol._mapping(remote, "remote")
    if (type(remote.get("session_id")) is not type(record["session_id"])
            or remote.get("session_id") != record["session_id"] or remote.get("site_id") != record["site_id"]):
        raise protocol.ProtocolError("native_remote_mismatch")
    phase = remote.get("phase")
    if phase not in _RANK:
        raise protocol.ProtocolError("invalid_native_remote_phase")
    expected = {"session_id", "site_id", "phase"}
    if phase in ("casting", "waiting", "bite"):
        expected |= {"biteAt", "expiresAt", "serverNow"}
        for key in ("biteAt", "expiresAt", "serverNow"):
            protocol._number(remote.get(key), key, 1, 10**15)
        if remote["biteAt"] >= remote["expiresAt"]:
            raise protocol.ProtocolError("invalid_native_bite_window")
    elif phase == "fighting":
        expected.add("fight")
        if remote.get("fight") != protocol.normalize_fight_challenge(remote.get("fight")):
            raise protocol.ProtocolError("invalid_native_challenge_fields")
    elif phase == "settled":
        expected.add("catches")
        if remote.get("catches") != record["catches"]:
            raise protocol.ProtocolError("native_catches_mismatch")
    if set(remote) != expected:
        raise protocol.ProtocolError("invalid_native_remote_fields")
    if (phase == "settled") != (record["phase"] in ("settled", "accounted")):
        raise protocol.ProtocolError("invalid_native_settlement_phase")


def validate(record):
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise protocol.ProtocolError("invalid_native_record")
    if type(record["version"]) is not int or record["version"] != 2 or record["mode"] != protocol.MODE:
        raise protocol.ProtocolError("invalid_native_version")
    for key in ("identity_id", "account_id"):
        protocol._number(record[key], key, 1, 2**53 - 1, integer=True)
    protocol._number(record["player_id"], "player_id", -(2**53 - 1), 2**53 - 1, integer=True)
    if record["player_id"] == 0:
        raise protocol.ProtocolError("invalid_player_id")
    protocol._number(record["revision"], "revision", 1, 100000, integer=True)
    protocol._number(record["created_at"], "created_at", 1, 10**12)
    basis = protocol._mapping(record["projection_basis"], "projection_basis")
    if basis and (set(basis) != {"facts", "plan", "inventory"}
                  or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in basis.values())):
        raise protocol.ProtocolError("invalid_native_projection_basis")
    if not isinstance(record["cast_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", record["cast_id"]):
        raise protocol.ProtocolError("invalid_cast_id")
    protocol.placement(record["site_id"], record["model_id"])
    protocol._identifier(record["bait_id"], "bait_id")
    if record["phase"] not in ("cast_pending", "session_owned", "settled", "accounted"):
        raise protocol.ProtocolError("invalid_native_phase")
    if record["phase"] == "cast_pending":
        if record["session_id"] != "":
            raise protocol.ProtocolError("invalid_cast_pending")
    else:
        protocol.session_identifier(record["session_id"])
        if record["revision"] < 2:
            raise protocol.ProtocolError("invalid_session_revision")
    catches = record["catches"]
    if record["phase"] not in ("settled", "accounted"):
        if catches is not None:
            raise protocol.ProtocolError("premature_catches")
    else:
        protocol._mapping(catches, "catches")
        if len(catches) > 1:
            raise protocol.ProtocolError("invalid_catches")
        for name, count in catches.items():
            protocol._text(name, "fish_name")
            protocol._number(count, "fish_count", 1, 1000, integer=True)
    _validate_remote(record)
    quota = record["settlement_quota"]
    if quota is not None:
        protocol._mapping(quota, "settlement_quota")
        if (record["phase"] not in ("settled", "accounted") or set(quota) != {"day", "used", "limit", "remaining"}
                or not isinstance(quota["day"], str)):
            raise protocol.ProtocolError("invalid_native_quota")
        try:
            if datetime.strptime(quota["day"], "%Y-%m-%d").strftime("%Y-%m-%d") != quota["day"]:
                raise ValueError()
        except ValueError:
            raise protocol.ProtocolError("invalid_native_quota_day") from None
        for key in ("used", "limit", "remaining"):
            protocol._number(quota[key], "native_quota_" + key, 0, 1000, integer=True)
        if quota["used"] + quota["remaining"] != quota["limit"]:
            raise protocol.ProtocolError("inconsistent_native_quota")
    action, payload = record["pending_action"], record["pending_payload"]
    protocol._mapping(payload, "pending_payload")
    if action not in ("", "cast", "hook", "checkpoint", "fight"):
        raise protocol.ProtocolError("invalid_native_pending_action")
    if (record["phase"] == "cast_pending") != (action == "cast"):
        raise protocol.ProtocolError("invalid_native_cast_action")
    if record["phase"] in ("settled", "accounted") and action:
        raise protocol.ProtocolError("settled_native_action")
    if action in ("", "cast"):
        if payload:
            raise protocol.ProtocolError("invalid_native_pending_payload")
    elif action == "hook":
        if set(payload) != {"operationId"} or record["remote"]["phase"] not in ("casting", "waiting", "bite"):
            raise protocol.ProtocolError("invalid_native_hook")
        protocol._identifier(payload["operationId"], "hook_operation_id")
    else:
        expected = {"fishingProof"} | ({"checkpointState"} if action == "checkpoint" else set())
        if set(payload) != expected or record["remote"]["phase"] != "fighting":
            raise protocol.ProtocolError("invalid_native_fight")
        _proof(record["remote"]["fight"], payload["fishingProof"], final=action == "fight", details=payload.get("checkpointState"))
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
    session_id: str | int
    site_id: str
    revision: int
    action: str


def _query(record, action):
    return Query(*(record[key] for key in ("identity_id", "account_id", "player_id", "cast_id",
                                          "session_id", "site_id", "revision")), action)


def query_payload(record, query):
    validate(record)
    if (not isinstance(query, Query) or query.action not in ("cast", "state", "hook", "checkpoint", "fight")
            or type(query.session_id) is not type(record["session_id"]) or query != _query(record, query.action)):
        raise protocol.ProtocolError("stale_native_query")
    if query.action != "state" and query.action != record["pending_action"]:
        raise protocol.ProtocolError("native_action_not_pending")
    if query.action == "cast":
        if record["phase"] != "cast_pending":
            raise protocol.ProtocolError("cast_already_owned")
        return {**protocol.placement(record["site_id"], record["model_id"]),
                "baitItemId": record["bait_id"], "operationId": record["cast_id"], "playerId": record["player_id"]}
    if query.action != "state":
        return {**protocol.placement(record["site_id"], record["model_id"]), "playerId": record["player_id"],
                "sessionId": record["session_id"], **deepcopy(record["pending_payload"])}
    return {"siteId": record["site_id"], "playerId": record["player_id"], "refreshContext": True,
            **({"sessionId": record["session_id"]} if record["session_id"]
               else {"castOperationId": record["cast_id"]})}


def reconcile(record, query, payload):
    """An unscoped context, stale query or missing session cannot close a cast."""
    query_payload(record, query)
    root = protocol._response(payload)
    if "playerId" in root and (type(root["playerId"]) is not int or root["playerId"] != record["player_id"]):
        raise protocol.ProtocolError("player_mismatch")
    if query.action == "checkpoint" and "session" not in root:
        updated = deepcopy(record)
        pending = updated["pending_payload"]
        updated["remote"]["fight"]["checkpoint"] = {
            "durationMs": pending["fishingProof"]["durationMs"],
            "events": deepcopy(pending["fishingProof"]["events"]), "details": deepcopy(pending["checkpointState"]),
        }
        updated.update(pending_action="", pending_payload={}, revision=record["revision"] + 1)
        return validate(updated), deepcopy(updated["remote"])
    remote = protocol._mapping(root.get("session"), "session")
    if "playerId" in remote and (type(remote["playerId"]) is not int or remote["playerId"] != record["player_id"]):
        raise protocol.ProtocolError("player_mismatch")
    for key in ("castOperationId",):
        if key in remote and remote[key] != record["cast_id"]:
            raise protocol.ProtocolError("cast_operation_mismatch")
    if query.action == "cast" and "operationId" in remote and remote["operationId"] != record["cast_id"]:
        raise protocol.ProtocolError("cast_operation_mismatch")
    session_id = record["session_id"] or protocol.session_identifier(remote.get("sessionId"))
    # First binding is only from a captured cast response or exact cast-ID state
    # query. Once bound, every response must match the original session ID.
    parsed = protocol.parse_owned_session(root, session_id=session_id, site_id=record["site_id"])
    settled = parsed["phase"] == "settled"
    next_phase = "settled" if settled else "session_owned"
    catches = parsed.get("catches") if settled else None
    settlement_quota = record["settlement_quota"]
    if settled and isinstance(root.get("context"), dict):
        facts = protocol.parse_context(root)
        stamp = protocol._number(root["context"].get("serverNow"), "quota_time", 1, 253402214400000)
        day = datetime.fromtimestamp(stamp / 1000, timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        settlement_quota = {"day": day, **facts["quota"]}
    if record["phase"] in ("settled", "accounted"):
        if not settled or catches != record["catches"]:
            raise protocol.ProtocolError("settlement_changed")
        if record["phase"] == "settled" and record["settlement_quota"] is None and settlement_quota is not None:
            return validate({**deepcopy(record), "settlement_quota": settlement_quota,
                             "revision": record["revision"] + 1}), parsed
        return deepcopy(record), parsed
    previous = record["remote"]
    if previous and _RANK[parsed["phase"]] < _RANK[previous["phase"]]:
        raise protocol.ProtocolError("native_phase_regressed")
    if previous and previous["phase"] == parsed["phase"] == "fighting":
        old, new = previous["fight"], parsed["fight"]
        old_ms = (old.get("checkpoint") or {}).get("durationMs", 0)
        new_ms = (new.get("checkpoint") or {}).get("durationMs", 0)
        if old["challengeId"] != new["challengeId"] or new_ms < old_ms:
            raise protocol.ProtocolError("native_challenge_regressed")
    action = record["pending_action"]
    confirmed = (not action or action == "cast" or settled
                 or (action == "hook" and _RANK[parsed["phase"]] >= 1)
                 or (action == "fight" and parsed["phase"] == "settling"))
    if action == "checkpoint" and not settled:
        if parsed["phase"] != "fighting":
            raise protocol.ProtocolError("native_checkpoint_unconfirmed")
        proof = record["pending_payload"]["fishingProof"]
        check = parsed["fight"].get("checkpoint") or {}
        confirmed = (check.get("durationMs") == proof["durationMs"] and check.get("events") == proof["events"]
                     and check.get("details") == record["pending_payload"]["checkpointState"])
    if not confirmed:
        # Unknown mutations remain pinned to their original input and snapshot.
        return deepcopy(record), parsed
    updated = {**record, "session_id": session_id, "phase": next_phase, "catches": catches,
               "pending_action": "", "pending_payload": {}, "remote": deepcopy(parsed), "settlement_quota": settlement_quota}
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
        self.before_cast = None
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
        if updated != {}:
            validate(updated)
        try:
            if self.compare_and_save(deepcopy(self.record), deepcopy(updated)) is not True:
                raise protocol.ProtocolError("native_save_failed")
            if self.read_current() != {}:
                validate(self.read_current())
            if self.is_owner_current() is not True or self.read_current() != updated:
                raise protocol.ProtocolError("native_owner_changed")
        except BaseException:
            self.failed = True
            raise
        self.record = deepcopy(updated)

    def start(self, *, context, site_id, model_id, bait_id, now=None, projection_basis=None):
        self._check()
        if self.record != {} and self.record["phase"] != "accounted":
            raise protocol.ProtocolError("native_cast_unresolved")
        facts = protocol.parse_context(context)
        historical = context.get("session")
        if (isinstance(historical, dict) and historical.get("status") not in ("active", "settling")
                and isinstance(historical.get("result"), dict) and historical["result"].get("ready") is True):
            parsed_history = protocol.parse_owned_session(context, session_id=historical.get("sessionId"),
                                                         site_id=historical.get("siteId"))
            historical = None if parsed_history["phase"] == "settled" else historical
        if "session" not in context or historical is not None:
            raise protocol.ProtocolError("native_existing_session")
        reason = protocol.cast_block_reason(facts, bait_id)
        if reason:
            raise protocol.ProtocolError(reason)
        record = {"version": 2, "mode": protocol.MODE,
                  "identity_id": self.owner[0], "account_id": self.owner[1], "player_id": self.owner[2],
                  "cast_id": uuid.uuid4().hex, "session_id": "", "site_id": site_id,
                  "model_id": model_id, "bait_id": bait_id, "revision": 1, "phase": "cast_pending", "catches": None,
                  "pending_action": "cast", "pending_payload": {}, "remote": None,
                  "created_at": time.time() if now is None else now,
                  "projection_basis": deepcopy(projection_basis or {}), "settlement_quota": None}
        self.before_cast = deepcopy(self.record)
        self._commit(record)
        query = _query(self.record, "cast")
        return query, query_payload(self.record, query)

    def cancel_undispatched(self, query):
        """Caller has an explicit zero-attempt transport result for this intent."""
        self._check()
        query_payload(self.record, query)
        if query.action == "state":
            raise protocol.ProtocolError("cannot_cancel_native_read")
        if query.action == "cast":
            if self.before_cast is None:
                raise protocol.ProtocolError("cannot_cancel_recovered_cast")
            updated = deepcopy(self.before_cast)
        else:
            updated = {**self.record, "pending_action": "", "pending_payload": {},
                       "revision": self.record["revision"] + 1}
        self._commit(updated)

    def recovery(self):
        self._check()
        validate(self.record)
        if self.record["phase"] not in ("settled", "accounted"):
            self._commit({**self.record, "revision": self.record["revision"] + 1})
        query = _query(self.record, "state")
        return query, query_payload(self.record, query)

    def prepare(self, action, *, clock=None, monotonic_now=None, proof=None, details=None):
        self._check()
        validate(self.record)
        if self.record["phase"] != "session_owned" or self.record["pending_action"]:
            raise protocol.ProtocolError("native_mutation_unresolved")
        remote = self.record["remote"]
        if action == "hook":
            if (clock is None or remote["phase"] not in ("casting", "waiting", "bite")
                    or protocol.next_session_action(remote, clock, monotonic_now)[0] != "hook"):
                raise protocol.ProtocolError("native_hook_outside_window")
            payload = {"operationId": uuid.uuid4().hex}
        elif action in ("checkpoint", "fight"):
            if remote["phase"] != "fighting":
                raise protocol.ProtocolError("native_fight_not_ready")
            _proof(remote["fight"], proof, final=action == "fight", details=details)
            payload = {"fishingProof": deepcopy(proof)}
            if action == "checkpoint":
                payload["checkpointState"] = deepcopy(details)
        else:
            raise protocol.ProtocolError("unsupported_native_mutation")
        self._commit({**self.record, "pending_action": action, "pending_payload": payload,
                      "revision": self.record["revision"] + 1})
        query = _query(self.record, action)
        return query, query_payload(self.record, query)

    def accept(self, query, payload):
        self._check()
        updated, parsed = reconcile(self.record, query, payload)
        if updated != self.record:
            self._commit(updated)
        self._check()
        return parsed
