from copy import deepcopy
from dataclasses import replace
import json

import pytest

from model.features import fishing_dwelling_journal as j
from model.features.fishing_dwelling_protocol import MODE, ProtocolError, ServerClock, fight_steps


OWNER = (3765328695, 301299112, -1003765328695)


def context():
    return {"ok": True, "session": None, "context": {
        "enabled": True, "unavailable": "", "quota": {"used": 0, "remaining": 5, "limit": 5},
        "conflict": None, "rod": {"itemId": "rod", "name": "rod"},
        "baits": [{"itemId": "bait", "name": "bait", "count": 5}],
    }}


def response(*, settled=False):
    data = {"ok": True, "session": {"sessionId": "native-session", "siteId": "west-shore", "mode": MODE,
        "status": "active", "phase": "waiting", "serverNow": 10000, "biteAt": 15000, "expiresAt": 18000,
        "result": {"ready": True, "caught": True, "fish": {"name": "fish", "count": 2}} if settled else None}}
    if settled:
        data["context"] = context()["context"]
        data["context"].update(serverNow=1700000000000, quota={"used": 1, "remaining": 4, "limit": 5})
    return data


class Store:
    def __init__(self):
        self.record = {}
        self.owned = True
        self.fail = False
        self.writes = 0

    def save(self, expected, updated):
        assert self.record == expected
        if self.fail:
            return False
        self.record = deepcopy(updated)
        self.writes += 1
        return True

    def open(self, **changes):
        return j.NativeCastJournal(**{
            "owner": OWNER, "read_current": lambda: self.record,
            "compare_and_save": self.save, "is_owner_current": lambda: self.owned, **changes})


def start(ledger, data=None):
    return ledger.start(context=context() if data is None else data,
                        site_id="west-shore", model_id="ngw", bait_id="bait")


def test_intent_is_persisted_before_a_cast_payload_is_released():
    store = Store()
    ledger = store.open()
    query, payload = start(ledger)
    assert store.writes == 1
    assert store.record["phase"] == "cast_pending"
    assert payload["operationId"] == store.record["cast_id"] == query.cast_id
    assert payload["playerId"] == OWNER[2]
    assert "token" not in json.dumps(store.record)


def test_numeric_session_id_binds_and_survives_exact_recovery():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    payload = response()
    payload["session"]["sessionId"] = 12345
    ledger.accept(query, payload)
    assert store.record["session_id"] == 12345 and type(store.record["session_id"]) is int
    query, request = store.open().recovery()
    assert request["sessionId"] == 12345 and type(request["sessionId"]) is int
    with pytest.raises(ProtocolError):
        j.query_payload(store.record, replace(query, session_id="12345"))


def test_numeric_missed_session_recovers_original_cast_without_recast():
    store = Store()
    ledger = store.open()
    start(ledger)
    ledger = store.open()
    query, request = ledger.recovery()
    assert request["castOperationId"] == store.record["cast_id"]
    payload = response(settled=True)
    payload["session"].update(sessionId=12345, status="missed", phase="missed", result={
        "ready": True, "caught": False, "reason": "timeout", "status": "missed", "fish": None,
        "expGain": 1, "bonusLoot": [],
    })
    ledger.accept(query, payload)
    assert store.record["phase"] == "settled" and store.record["catches"] == {}
    assert store.record["settlement_quota"]["used"] == 1


@pytest.mark.parametrize("value", [0, -1, True, False, 12345.0, 2**53, None, {}, "", "x:y"])
def test_invalid_session_identifiers_do_not_bind(value):
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    original = deepcopy(store.record)
    payload = response()
    payload["session"]["sessionId"] = value
    with pytest.raises(ProtocolError):
        ledger.accept(query, payload)
    assert store.record == original


@pytest.mark.parametrize("save_result", [False, None, 1])
def test_save_must_explicitly_succeed(save_result):
    store = Store()
    ledger = store.open(compare_and_save=lambda _old, _new: save_result)
    with pytest.raises(ProtocolError, match="native_save_failed"):
        start(ledger)
    assert store.record == {}
    with pytest.raises(ProtocolError):
        start(ledger)


def test_changed_owner_during_save_never_dispatches():
    store = Store()

    def save(old, new):
        store.save(old, new)
        store.owned = False
        return True

    ledger = store.open(compare_and_save=save)
    with pytest.raises(ProtocolError, match="owner_changed"):
        start(ledger)
    assert store.record["phase"] == "cast_pending"


def test_ambiguous_disk_failure_keeps_barrier_even_if_write_happened():
    store = Store()

    def save(old, new):
        store.save(old, new)
        raise OSError("fsync fixture")

    ledger = store.open(compare_and_save=save)
    with pytest.raises(OSError):
        start(ledger)
    with pytest.raises(ProtocolError):
        start(ledger)
    reopened = store.open()
    query, payload = reopened.recovery()
    assert query.action == "state" and payload["castOperationId"] == store.record["cast_id"]


def test_timeout_or_restart_can_only_query_original_cast(tmp_path):
    store = Store()
    query, _ = start(store.open())
    path = tmp_path / "native-record.json"
    path.write_text(json.dumps(store.record))
    restarted = Store()
    restarted.record = json.loads(path.read_text())
    ledger = restarted.open()
    with pytest.raises(ProtocolError, match="unresolved"):
        start(ledger)
    state_query, state_payload = ledger.recovery()
    assert state_query.action == "state"
    assert state_payload["castOperationId"] == query.cast_id
    assert "operationId" not in state_payload
    ledger.accept(state_query, response())
    assert ledger.recovery()[1]["sessionId"] == "native-session"


@pytest.mark.parametrize("data", [{"ok": True, "session": None}, {"ok": False}, {"ok": True}])
def test_missing_or_unconfirmed_response_keeps_original_intent(data):
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    saved = deepcopy(store.record)
    with pytest.raises(ProtocolError):
        ledger.accept(query, data)
    assert store.record == saved
    assert ledger.recovery()[0].cast_id == query.cast_id


def test_generic_context_cannot_bind_an_unrelated_historical_result():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    with pytest.raises(ProtocolError, match="stale_native_query"):
        ledger.accept(replace(query, action="context"), response(settled=True))
    assert store.record["phase"] == "cast_pending"


@pytest.mark.parametrize("field, value", [
    ("identity_id", 2), ("account_id", 2), ("player_id", 2), ("cast_id", "other"),
    ("session_id", "other"), ("site_id", "east-shore"), ("revision", 999),
])
def test_query_scope_cannot_be_swapped(field, value):
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    with pytest.raises(ProtocolError):
        ledger.accept(replace(query, **{field: value}), response())
    assert store.record["phase"] == "cast_pending"


def test_bound_session_cannot_be_replaced_by_another_current_or_old_catch():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    ledger.accept(query, response())
    state_query, _ = ledger.recovery()
    foreign = response(settled=True)
    foreign["session"]["sessionId"] = "other"
    with pytest.raises(ProtocolError, match="session_mismatch"):
        ledger.accept(state_query, foreign)
    assert store.record["catches"] is None


def test_owner_replaced_while_http_inflight_preserves_pending_for_original_owner():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    store.owned = False
    with pytest.raises(ProtocolError, match="owner_changed"):
        ledger.accept(query, response(settled=True))
    assert store.record["phase"] == "cast_pending"


def test_save_failure_after_real_settlement_does_not_release_another_cast():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    store.fail = True
    with pytest.raises(ProtocolError, match="save_failed"):
        ledger.accept(query, response(settled=True))
    assert store.record["phase"] == "cast_pending"
    with pytest.raises(ProtocolError):
        start(ledger)
    store.fail = False
    reopened = store.open()
    reopened.accept(reopened.recovery()[0], response(settled=True))
    assert store.record["phase"] == "settled"


def test_settlement_is_idempotent_and_does_not_itself_update_inventory_or_quota():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    ledger.accept(query, response(settled=True))
    saved = deepcopy(store.record)
    assert saved["catches"] == {"fish": 2}
    for _ in range(3):
        ledger.accept(ledger.recovery()[0], response(settled=True))
    assert store.record == saved and store.writes == 2
    assert not {"quota", "inventory", "accounted"} & store.record.keys()
    with pytest.raises(ProtocolError):
        ledger.accept(query, response(settled=True))
    with pytest.raises(ProtocolError):
        start(ledger)


def test_settled_receipt_never_regresses_or_changes_rewards():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    ledger.accept(query, response(settled=True))
    changed = response(settled=True)
    changed["session"]["result"]["fish"]["count"] = 8
    for data in (response(), changed):
        with pytest.raises(ProtocolError, match="settlement_changed"):
            ledger.accept(ledger.recovery()[0], data)


def test_explicit_operation_and_player_echoes_must_match():
    for key, value in (("castOperationId", "other"), ("operationId", "other"), ("playerId", OWNER[0])):
        store = Store()
        ledger = store.open()
        query, _ = start(ledger)
        data = response()
        data["session"][key] = value
        with pytest.raises(ProtocolError):
            ledger.accept(query, data)


def test_existing_or_unknown_session_never_authorizes_cast():
    for session in ({"sessionId": "old"}, False, []):
        store = Store()
        data = context()
        data["session"] = session
        with pytest.raises(ProtocolError, match="existing_session"):
            start(store.open(), data)
        assert store.record == {}


def test_legacy_or_wrong_owner_journal_cannot_be_reused():
    store = Store()
    store.record = {"version": 1, "checkpoint": {"unresolved_action": "start"}}
    with pytest.raises(ProtocolError):
        store.open()
    store.record = {}
    start(store.open())
    with pytest.raises(ProtocolError, match="owner_mismatch"):
        store.open(owner=(OWNER[0], 123, OWNER[2]))


def test_corrupt_journal_is_not_normalized_to_empty():
    store = Store()
    start(store.open())
    saved = deepcopy(store.record)
    for changes in ({"version": True}, {"revision": 0}, {"session_id": "unexpected"},
                    {"player_id": 0}, {"catches": {}}, {"token": "credential"}, {"phase": "unknown"}):
        store.record = {**saved, **changes}
        with pytest.raises(ProtocolError):
            store.open()


def fighting():
    data = response()
    data["session"].update(phase="fighting", fight={
        "challengeId": "challenge-native", "minDurationMs": 5200, "maxDurationMs": 70000,
        "checkpointIntervalMs": 2500, "maxInputEvents": 1000,
        "targetLow": 41, "targetHigh": 68, "fishPower": 1.7,
    })
    return data


def hooked():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    ledger.accept(query, response())
    hook, body = ledger.prepare("hook", clock=ServerClock.capture(15000, 0, 0), monotonic_now=0)
    assert store.record["pending_action"] == "hook" and body["operationId"]
    ledger.accept(hook, fighting())
    return store, ledger


@pytest.mark.parametrize("server_time", [14999, 18000, 20000])
def test_hook_outside_bite_window_is_not_authorized(server_time):
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    ledger.accept(query, response())
    with pytest.raises(ProtocolError, match="outside_window"):
        ledger.prepare("hook", clock=ServerClock.capture(server_time, 0, 0), monotonic_now=0)
    assert store.record["pending_action"] == ""


def test_unknown_hook_is_never_repeated_even_if_state_still_waiting():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    ledger.accept(query, response())
    ledger.prepare("hook", clock=ServerClock.capture(15000, 0, 0), monotonic_now=0)
    reopened = store.open()
    state_query, payload = reopened.recovery()
    assert payload["sessionId"] == "native-session" and state_query.action == "state"
    reopened.accept(state_query, response())
    assert store.record["pending_action"] == "hook"
    with pytest.raises(ProtocolError, match="unresolved"):
        reopened.prepare("hook", clock=ServerClock.capture(15500, 0, 0), monotonic_now=0)
    reopened.accept(reopened.recovery()[0], fighting())
    assert store.record["pending_action"] == ""


def test_hook_cannot_be_repeated_after_phase_advance_or_old_response():
    store, ledger = hooked()
    old, _ = ledger.recovery()
    current, _ = ledger.recovery()
    with pytest.raises(ProtocolError, match="stale_native_query"):
        ledger.accept(old, response())
    with pytest.raises(ProtocolError, match="phase_regressed"):
        ledger.accept(current, response())
    with pytest.raises(ProtocolError):
        ledger.prepare("hook", clock=ServerClock.capture(15500, 0, 0), monotonic_now=0)
    assert store.record["remote"]["phase"] == "fighting"


def test_every_checkpoint_and_final_fight_has_durable_intent():
    store, ledger = hooked()
    challenge = deepcopy(store.record["remote"]["fight"])
    for final, proof, details in fight_steps(challenge):
        action = "fight" if final else "checkpoint"
        query, body = ledger.prepare(action, proof=proof, details=details)
        assert store.record["pending_action"] == action
        assert store.record["pending_payload"]["fishingProof"] == body["fishingProof"] == proof
        ledger.accept(query, response(settled=True) if final else {"ok": True})
        assert store.record["pending_action"] == ""
    assert store.record["catches"] == {"fish": 2}


@pytest.mark.parametrize("action", ["checkpoint", "fight"])
def test_save_failure_cannot_authorize_checkpoint_or_fight(action):
    store, ledger = hooked()
    steps = list(fight_steps(store.record["remote"]["fight"]))
    _, proof, details = steps[-1] if action == "fight" else steps[0]
    store.fail = True
    with pytest.raises(ProtocolError, match="save_failed"):
        ledger.prepare(action, proof=proof, details=details)
    assert store.record["pending_action"] == ""


def test_lost_checkpoint_ack_requires_matching_server_checkpoint():
    store, ledger = hooked()
    _, proof, details = next(fight_steps(store.record["remote"]["fight"]))
    ledger.prepare("checkpoint", proof=proof, details=details)
    ledger = store.open()
    query, _ = ledger.recovery()
    ledger.accept(query, fighting())
    assert store.record["pending_action"] == "checkpoint"
    with pytest.raises(ProtocolError, match="unresolved"):
        ledger.prepare("checkpoint", proof=proof, details=details)
    remote = fighting()
    remote["session"]["fight"]["checkpoint"] = {
        "durationMs": proof["durationMs"], "events": proof["events"], "details": details,
    }
    ledger.accept(ledger.recovery()[0], remote)
    assert store.record["pending_action"] == ""
    assert store.record["remote"]["fight"]["checkpoint"]["durationMs"] == proof["durationMs"]


def test_lost_fight_ack_can_only_poll_and_accept_settling_or_final():
    store, ledger = hooked()
    _, proof, details = list(fight_steps(store.record["remote"]["fight"]))[-1]
    ledger.prepare("fight", proof=proof, details=details)
    ledger = store.open()
    ledger.accept(ledger.recovery()[0], fighting())
    assert store.record["pending_action"] == "fight"
    with pytest.raises(ProtocolError, match="unresolved"):
        ledger.prepare("fight", proof=proof, details=details)
    data = response()
    data["session"]["status"] = "settling"
    ledger.accept(ledger.recovery()[0], data)
    assert store.record["remote"]["phase"] == "settling" and not store.record["pending_action"]
    ledger.accept(ledger.recovery()[0], response(settled=True))
    assert store.record["phase"] == "settled"


@pytest.mark.parametrize("change", ["challenge", "duration", "event", "prefix"])
def test_invalid_proof_cannot_be_persisted(change):
    store, ledger = hooked()
    steps = list(fight_steps(store.record["remote"]["fight"]))
    _, proof, details = steps[0]
    query, _ = ledger.prepare("checkpoint", proof=proof, details=details)
    ledger.accept(query, {"ok": True})
    _, proof, details = deepcopy(steps[1])
    if change == "challenge":
        proof["challengeId"] = "wrong"
    elif change == "duration":
        proof["durationMs"] = 70020
    elif change == "event":
        proof["events"][-1]["holding"] = 1
    else:
        proof["events"] = proof["events"][1:]
    with pytest.raises(ProtocolError):
        ledger.prepare("checkpoint", proof=proof, details=details)
    assert not store.record["pending_action"]


def test_zero_dispatch_compensation_cannot_erase_a_recovered_unknown_cast():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    reopened = store.open()
    with pytest.raises(ProtocolError, match="recovered_cast"):
        reopened.cancel_undispatched(query)
    assert store.record["phase"] == "cast_pending"
    ledger.cancel_undispatched(query)
    assert store.record == {}


def test_unsent_hook_compensation_keeps_original_cast_and_does_not_recast():
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    cast_id = query.cast_id
    ledger.accept(query, response())
    query, _ = ledger.prepare("hook", clock=ServerClock.capture(15000, 0, 0), monotonic_now=0)
    ledger.cancel_undispatched(query)
    assert store.record["cast_id"] == cast_id and store.record["pending_action"] == ""
    with pytest.raises(ProtocolError):
        start(ledger)
