from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from model.features import fishing_dwelling_protocol as p


def context():
    return {"ok": True, "session": None, "context": {
        "enabled": True, "unavailable": "", "quota": {"used": 0, "remaining": 5, "limit": 5},
        "conflict": None, "rod": {"itemId": "item_fishing_rod_basic", "name": "rod"},
        "baits": [{"itemId": "bait", "name": "bait", "count": 8}],
    }}


def challenge(**changes):
    return {"challengeId": "fixture-native-20260930", "targetLow": 41, "targetHigh": 68,
            "fishPower": 1.7, "fishSeed": "native-fixture", "minDurationMs": 5200,
            "maxDurationMs": 70000, "checkpointIntervalMs": 2500, "maxInputEvents": 1000,
            "behaviorVersion": 2, "behavior": "steady", "struggles": [
                {"startMs": 2800, "durationMs": 900, "strength": .9},
                {"startMs": 5400, "durationMs": 1200, "strength": 1.4},
            ], **changes}


def waiting():
    return {"ok": True, "session": {"sessionId": "s1", "siteId": "west-shore", "mode": p.MODE,
            "status": "active", "phase": "waiting", "serverNow": 10000, "biteAt": 12000,
            "expiresAt": 18000, "result": None}}


def owned(payload):
    return p.parse_owned_session(payload, session_id="s1", site_id="west-shore")


def restored():
    source = challenge()
    _, proof, details = next(p.fight_steps(source))
    source["checkpoint"] = {"durationMs": proof["durationMs"], "events": proof["events"], "details": details}
    return source


def test_context_is_read_only_and_does_not_count_historical_results():
    data = context()
    data["session"] = {"result": {"ready": True, "caught": True}}
    before = deepcopy(data)
    parsed = p.parse_context(data)
    assert parsed["quota"] == {"used": 0, "remaining": 5, "limit": 5}
    assert p.cast_block_reason(parsed, "bait") == ""
    assert "catches" not in parsed
    parsed["baits"][0]["count"] = 0
    assert data == before


@pytest.mark.parametrize("changes, reason", [
    ({"rod": None}, "fishing_rod_missing"),
    ({"unavailable": "fishing_companion_sailing"}, "fishing_companion_sailing"),
    ({"enabled": False}, "fishing_site_unavailable"),
    ({"conflict": {"message": "old cast"}}, "session_conflict"),
    ({"baits": []}, "fishing_bait_missing"),
    ({"quota": {"limit": 5, "used": 5, "remaining": 0}}, "fishing_daily_limit_reached"),
    ({"baits": [{"itemId": "bait", "name": "bait", "count": 3, "unlocked": False}]}, "fishing_bait_level_low"),
])
def test_context_refusals_are_distinct(changes, reason):
    data = context()
    data["context"].update(changes)
    assert p.cast_block_reason(p.parse_context(data), "bait") == reason


@pytest.mark.parametrize("changes", [
    {"quota": {"limit": 5, "used": 1, "remaining": 5}},
    {"quota": {"limit": 5, "used": False, "remaining": 5}},
    {"quota": {"limit": 5, "used": 0, "remaining": "5"}},
    {"enabled": "true"}, {"rod": {}}, {"rod": False}, {"conflict": False},
    {"baits": None}, {"unavailable": []},
    {"baits": [{"itemId": "bait", "name": "bait", "count": float("nan")}]},
    {"baits": [{"itemId": "bait", "name": "bait", "count": 1}] * 2},
])
def test_context_invalid_contract_never_succeeds(changes):
    data = context()
    data["context"].update(changes)
    with pytest.raises(p.ProtocolError):
        p.parse_context(data)


@pytest.mark.parametrize("payload", [None, [], {}, {"ok": 1}, {"ok": "true"}, {"ok": False}])
def test_success_is_literal(payload):
    with pytest.raises(p.ProtocolError):
        p.parse_context(payload)


def test_missing_rod_is_not_an_explicit_no_rod():
    data = context()
    del data["context"]["rod"]
    with pytest.raises(p.ProtocolError, match="missing_rod_field"):
        p.parse_context(data)


@pytest.mark.parametrize("site", list(p.SITES))
def test_placement_has_no_default_companion(site):
    assert p.placement(site, "ngw")["position"] == list(p.SITES[site])
    with pytest.raises(p.ProtocolError):
        p.placement(site, "")


def test_owned_sessions_do_not_trust_http_success_alone():
    assert owned(waiting())["phase"] == "waiting"
    for key, value in [("sessionId", "other"), ("siteId", "east-shore"), ("mode", "legacy"),
                       ("phase", "unknown"), ("status", "completed"), ("biteAt", 19000)]:
        data = waiting()
        data["session"][key] = value
        with pytest.raises(p.ProtocolError):
            owned(data)
    with pytest.raises(p.ProtocolError):
        owned({"ok": True, "session": None})


def test_settlement_requires_exact_session_and_literal_ready_caught():
    data = waiting()
    data["session"]["result"] = {"ready": True, "caught": True, "fish": {"name": "fish", "quantity": 2}}
    assert owned(data)["catches"] == {"fish": 2}
    data["session"]["sessionId"] = "historical"
    with pytest.raises(p.ProtocolError, match="session_mismatch"):
        owned(data)


@pytest.mark.parametrize("result", [
    {"ready": 1}, {"ready": True, "caught": 1},
    {"ready": True, "caught": True},
    {"ready": True, "caught": True, "fish": {"name": "fish", "count": -1}},
    {"ready": True, "caught": True, "fish": {"name": "fish", "quantity": 1, "count": True}},
    {"ready": True, "caught": True, "fish": {"name": "fish", "quantity": 1, "count": 2}},
])
def test_bad_settlement_cannot_be_accounted(result):
    data = waiting()
    data["session"]["result"] = result
    with pytest.raises(p.ProtocolError):
        owned(data)


def test_settling_is_not_done_and_empty_catch_is_valid():
    data = waiting()
    data["session"]["status"] = "settling"
    assert owned(data)["phase"] == "settling"
    data["session"]["result"] = {"ready": True, "caught": False}
    assert owned(data) == {"session_id": "s1", "site_id": "west-shore", "phase": "settled", "catches": {}}


def test_monotonic_server_clock_retains_actual_rtt_for_slow_transport():
    assert p.ServerClock.capture(10000, 2, 2.2).now_ms(3.2) == pytest.approx(11100)
    assert p.ServerClock.capture(10000, 2, 4).now_ms(5) == 12000
    assert p.ServerClock.capture(10000, 2, 4).round_trip_ms == 2000
    with pytest.raises(p.ProtocolError):
        p.ServerClock.capture(10000, 4, 2)
    with pytest.raises(p.ProtocolError):
        p.ServerClock.capture(10000, 2, 4).now_ms(3)


def test_slow_reply_does_not_add_the_browser_cap_lag_to_four_second_bite_window():
    session = {"phase": "waiting", "serverNow": 10000, "biteAt": 25000, "expiresAt": 29000}
    clock = p.ServerClock.capture(10000, 0, 3.262)
    action, delay = p.next_session_action(session, clock, 3.262)
    assert action == "wait" and delay == pytest.approx(13.738)
    assert p.next_session_action(session, clock, 3.262 + delay) == ("hook", 0)
    assert p.next_session_action(session, clock, 3.262 + delay + 1) == ("state", 0)


def test_latency_equal_to_bite_window_cannot_authorize_hook():
    session = {"phase": "bite", "serverNow": 10000, "biteAt": 12000, "expiresAt": 16000}
    clock = p.ServerClock.capture(10000, 0, 4)
    assert p.next_session_action(session, clock, 4) == ("state", 0)


@pytest.mark.parametrize("rtt", [100, 388, 970, 2000, 3262, 3900])
def test_opening_cushion_preserves_expiry_budget(rtt):
    session = {"phase": "waiting", "serverNow": 10000, "biteAt": 25000, "expiresAt": 29000}
    clock = p.ServerClock.capture(10000, 0, rtt / 1000)
    action, delay = p.next_session_action(session, clock, rtt / 1000)
    assert action == "wait"
    at = rtt / 1000 + delay
    assert p.next_session_action(session, clock, at + 1e-9) == ("hook", 0)
    assert 25000 < clock.now_ms(at) < 29000 - rtt
    assert p.next_session_action(session, clock, at + 4) == ("state", 0)


@pytest.mark.parametrize("changes", [
    {"targetLow": 70}, {"targetHigh": 0}, {"fishPower": 0}, {"fishPower": float("inf")},
    {"fishPower": float("nan")}, {"fishPower": 10**1000},
    {"behaviorVersion": 3}, {"behaviorVersion": True}, {"behavior": "unknown"},
    {"maxDurationMs": 0}, {"maxDurationMs": 70001}, {"minDurationMs": 80000},
    {"checkpointIntervalMs": 0}, {"maxInputEvents": 100000},
    {"fishSeed": []}, {"fishSeed": 10**1000}, {"checkpoint": []}, {"checkpoint": {"durationMs": 20}},
    {"struggles": [{"startMs": 200, "durationMs": 0, "strength": 1}]},
    {"struggles": [{"startMs": 200, "durationMs": 20, "strength": float("nan")}]},
])
def test_invalid_challenge_stops_before_any_proof(changes):
    with pytest.raises(p.ProtocolError):
        list(p.fight_steps(challenge(**changes)))


@pytest.mark.parametrize("field, value", [
    ("progress", float("nan")), ("tension", -1), ("holding", 1), ("samples", 0),
    ("stable_samples", 1000000), ("danger_ms", 1), ("slack_ms", -20),
])
def test_invalid_restored_state_is_not_reset_to_fresh(field, value):
    c = restored()
    c["checkpoint"]["details"][field] = value
    with pytest.raises(p.ProtocolError):
        list(p.fight_steps(c))


@pytest.mark.parametrize("events", [
    [{"t": -20, "holding": False}], [{"t": 3000, "holding": False}],
    [{"t": 100, "holding": True}, {"t": 80, "holding": False}],
    [{"t": 100, "holding": "true"}], [{"t": 100, "holding": False, "extra": 1}],
])
def test_invalid_checkpoint_events_are_not_silently_filtered(events):
    c = restored()
    c["checkpoint"]["events"] = events
    with pytest.raises(p.ProtocolError):
        list(p.fight_steps(c))


def test_checkpoint_and_final_proofs_are_bounded_and_detached():
    c = challenge()
    before = deepcopy(c)
    steps = list(p.fight_steps(c))
    assert c == before
    assert len(steps) >= 3
    assert steps[-1][0] and steps[-1][1]["landed"]
    assert steps[-1][1]["durationMs"] >= c["minDurationMs"]
    for final, proof, details in steps:
        assert all(e["t"] <= proof["durationMs"] for e in proof["events"])
        assert proof["durationMs"] <= c["maxDurationMs"]
        assert len(proof["events"]) <= c["maxInputEvents"]
        assert final == ("landed" in proof)
        assert details["samples"] * 20 == proof["durationMs"]
    steps[0][1]["events"][0]["holding"] = "mutated"
    assert all(type(e["holding"]) is bool for e in steps[-1][1]["events"])


def test_caller_cannot_change_challenge_mid_flight():
    c = challenge()
    steps = p.fight_steps(c)
    next(steps)
    c["challengeId"] = "different"
    c["struggles"].clear()
    assert all(proof["challengeId"] == "fixture-native-20260930" for _, proof, _ in steps)


def test_restore_releases_hold_and_retains_checkpoint_prefix():
    c = challenge()
    for _, proof, details in p.fight_steps(c):
        if details["holding"]:
            break
    assert details["holding"]
    c["checkpoint"] = {"durationMs": proof["durationMs"], "events": proof["events"], "details": details}
    old_events = deepcopy(proof["events"])
    _, next_proof, _ = next(p.fight_steps(c))
    assert next_proof["events"][:len(old_events)] == old_events
    assert next_proof["events"][len(old_events)] == {"t": proof["durationMs"] + 20, "holding": False}


def test_completed_checkpoint_has_no_future_events_or_extra_game_ticks():
    c = challenge()
    _, proof, details = list(p.fight_steps(c))[-1]
    c["checkpoint"] = {"durationMs": proof["durationMs"], "events": proof["events"], "details": details}
    steps = list(p.fight_steps(c))
    assert len(steps) == 1
    assert steps[0][1] == proof
    assert steps[0][2] == details


def test_maximum_duration_finishes_even_without_catch():
    final, proof, _ = list(p.fight_steps(challenge(minDurationMs=20, maxDurationMs=20, struggles=[])))[-1]
    assert final and proof["durationMs"] == 20 and proof["landed"] is False


def test_event_limit_is_not_satisfied_by_truncating_evidence():
    with pytest.raises(p.ProtocolError, match="input_event_limit"):
        list(p.fight_steps(challenge(maxInputEvents=1)))


@pytest.mark.parametrize("version", [1, 2])
def test_python_physics_matches_official_javascript_replay(version):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the independent official-controller replay")
    cases, expected = [], []
    for behavior in ("steady", "leap", "surge"):
        for power in (.8, 1.7, 3.2):
            for seed in ("native-seed", 12345, "\U0001f319"):
                c = challenge(behaviorVersion=version, behavior=behavior, fishPower=power, fishSeed=seed)
                steps = list(p.fight_steps(c))
                for _, proof, details in steps:
                    cases.append({"challenge": c, "proof": proof})
                    expected.append(details)
                checkpoint = next(((proof, details) for final, proof, details in steps if not final and details["holding"]), None)
                if checkpoint:
                    proof, details = checkpoint
                    c = deepcopy(c)
                    c["checkpoint"] = {"durationMs": proof["durationMs"], "events": proof["events"], "details": details}
                    for _, proof, details in p.fight_steps(c):
                        cases.append({"challenge": c, "proof": proof})
                        expected.append(details)
    completed = subprocess.run([node, str(Path(__file__).parent / "fixtures/fishing_dwelling_physics.cjs")],
                               input=json.dumps(cases), text=True, capture_output=True, check=True, timeout=15)
    actual = json.loads(completed.stdout)
    assert len(actual) == len(expected)
    for index, (left, right) in enumerate(zip(actual, expected)):
        assert left == pytest.approx(right, abs=1e-8), index


@pytest.mark.parametrize("elapsed, action, delay", [(0, "wait", 2), (1.99, "wait", .01),
                                                   (2, "hook", 0), (7.99, "hook", 0),
                                                   (8, "state", 0), (90, "state", 0)])
def test_bite_window_uses_server_clock_not_local_wall_time(elapsed, action, delay):
    clock = p.ServerClock.capture(10000, 100, 100)
    actual, seconds = p.next_session_action(owned(waiting()), clock, 100 + elapsed)
    assert actual == action
    assert seconds == pytest.approx(delay)


class Clock:
    def __init__(self):
        self.now = 100.

    def sleep(self, delay):
        self.now += delay


def test_proof_cannot_be_submitted_before_its_real_duration():
    clock = Clock()
    c = challenge()
    for _, proof, _ in p.timed_fight_steps(c, is_current=lambda: True, monotonic=lambda: clock.now, sleeper=clock.sleep):
        assert clock.now - 100 >= proof["durationMs"] / 1000 - 1e-9
        # Checkpoint HTTP takes time too; it must not restart the fight clock.
        clock.now += .2


def test_resumed_proof_waits_only_for_new_game_ticks():
    clock = Clock()
    c = restored()
    steps = p.timed_fight_steps(c, is_current=lambda: True, monotonic=lambda: clock.now, sleeper=clock.sleep)
    _, proof, _ = next(steps)
    assert clock.now - 100 == pytest.approx((proof["durationMs"] - c["checkpoint"]["durationMs"]) / 1000)


def test_fake_noop_wait_cannot_authorize_a_future_proof():
    with pytest.raises(p.ProtocolError, match="clock_stalled"):
        list(p.timed_fight_steps(challenge(), is_current=lambda: True, monotonic=lambda: 0, sleeper=lambda _: None))


def test_owner_rechecked_after_wait_and_after_each_checkpoint():
    clock = Clock()
    current = [True]

    def sleep(delay):
        clock.sleep(delay)
        current[0] = False

    with pytest.raises(p.ProtocolError, match="owner_changed"):
        next(p.timed_fight_steps(challenge(), is_current=lambda: current[0], monotonic=lambda: clock.now, sleeper=sleep))
    current[0] = True
    steps = p.timed_fight_steps(challenge(), is_current=lambda: current[0], monotonic=lambda: clock.now, sleeper=clock.sleep)
    next(steps)
    current[0] = False
    with pytest.raises(p.ProtocolError, match="owner_changed"):
        next(steps)


def test_wall_clock_or_monotonic_regression_does_not_release_proof():
    clock = Clock()

    def regress(_):
        clock.now -= 1

    with pytest.raises(p.ProtocolError, match="fight_clock"):
        next(p.timed_fight_steps(challenge(), is_current=lambda: True, monotonic=lambda: clock.now, sleeper=regress))


def test_remote_fight_projection_drops_unowned_extra_fields():
    c = restored()
    c["credential"] = "do-not-persist"
    c["checkpoint"]["credential"] = "do-not-persist"
    c["checkpoint"]["details"]["credential"] = "do-not-persist"
    c["struggles"][0]["credential"] = "do-not-persist"
    clean = p.normalize_fight_challenge(c)
    assert "do-not-persist" not in json.dumps(clean)
    assert "do-not-persist" in json.dumps(c)
