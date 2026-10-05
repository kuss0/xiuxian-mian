"""Server-clock timing tests; success is not supplied by an unconditional mock."""

import random

import pytest

from model.features import world_boss_miniapp as boss


def run_battle(*, charge_delays=(0.05, 0.05), hit_delays=(0.05, 0.05), reveal_ms=0, oversleep=0):
    now = 100.0
    origin = now
    charged_at = None
    server_hits = []
    requests = []
    captures = []
    revealed = False

    def sleep(seconds):
        nonlocal now
        now += seconds

    def flow_sleep(seconds):
        sleep(seconds + (oversleep if revealed and charged_at is None else 0))

    def transport(request):
        nonlocal charged_at, origin, revealed
        endpoint = request["safe_summary"]["endpoint"]
        requests.append(endpoint)
        if endpoint == "start":
            return 200, {
                "ok": True,
                "boss": {"roomStatus": "battle", "actionLimit": 1, "actionsRemaining": 1},
                "challenge": {
                    "mode": "qyz_focus_burst_v2", "challengeId": "timing-fixture",
                    "windowCount": 1, "windows": [], "durationMs": 6000,
                },
            }
        if endpoint == "begin":
            origin = now
            return 200, {"ok": True, "startsInMs": 0}
        if endpoint == "window":
            sleep(reveal_ms / 1000)
            revealed = True
            return 200, {
                "ok": True, "windowCount": 1,
                "window": {"id": "w1", "centerMs": 3000, "perfectMs": 210, "hitMs": 620},
            }
        if endpoint == "charge_start":
            sleep(charge_delays[0])
            charged_at = now
            sleep(charge_delays[1])
            return 200, {"ok": True, "chargeTicket": "secret-charge-ticket"}
        if endpoint == "hit":
            sleep(hit_delays[0])
            hold_ms = (now - charged_at) * 1000
            delta_ms = (now - origin) * 1000 - 3000
            server_hit = {
                "attemptConsumed": True, "damageYi": 100,
                "holdMs": hold_ms, "deltaMs": abs(delta_ms),
                "perfect": 520 <= hold_ms <= 1250 and abs(delta_ms) <= 210,
            }
            server_hits.append(server_hit)
            sleep(hit_delays[1])
            return 200, {"ok": True, "hit": server_hit}
        if endpoint == "finish":
            return 200, {"ok": True, "result": {"hits": len(server_hits), "score": 100}}
        raise AssertionError(endpoint)

    result = boss.run_world_boss_joined_battle_lab_flow(
        boss.WorldBossJoinReceipt(True, "joined", player_id="77"),
        token="qyz_TEST", init_data="test-init", transport=transport,
        sleeper=flow_sleep, clock=lambda: now, rng=random.Random(9), capture_sink=captures,
    )
    return result, server_hits, requests, captures


def test_moderate_hit_delay_does_not_overcharge():
    result, hits, requests, _ = run_battle(hit_delays=(0.14, 0.05))
    assert result["ok"]
    assert hits[0]["perfect"], hits
    assert requests.count("hit") == requests.count("finish") == 1


def test_near_ceiling_baseline_fails_the_same_server_clock_case(monkeypatch):
    monkeypatch.setattr(boss, "WORLD_BOSS_OPTIMAL_HOLD_MIN_MS", 1200)
    monkeypatch.setattr(boss, "WORLD_BOSS_OPTIMAL_HOLD_MAX_MS", 1235)
    _, hits, _, _ = run_battle(hit_delays=(0.14, 0.05))
    assert hits[0]["holdMs"] > 1250
    assert not hits[0]["perfect"]


def test_full_rtt_subtraction_baseline_produces_short_server_hold(monkeypatch):
    monkeypatch.setattr(boss, "_world_boss_charge_release_target_ms", lambda action, **kw: max(
        action["elapsedMs"], kw["received_ms"],
    ))
    _, hits, _, _ = run_battle(charge_delays=(0.60, 0.05), reveal_ms=1950)
    assert hits[0]["holdMs"] < 520
    assert not hits[0]["perfect"]


@pytest.mark.parametrize("charge_delays", [(0.60, 0.05), (0.05, 0.60)])
def test_slow_charge_reply_keeps_a_real_hold_when_window_allows(charge_delays):
    result, hits, _, _ = run_battle(charge_delays=charge_delays, reveal_ms=1950)
    assert result["ok"]
    assert hits[0]["perfect"], hits
    release = next(e for e in result["events"] if e["step"] == "release_window")
    assert release["hold_ms"] == release["release_elapsed_ms"] - release["charge_start_ms"]


def test_impossible_latency_is_reported_not_retried_or_declared_perfect():
    result, hits, requests, captures = run_battle(hit_delays=(0.60, 0.05))
    assert result["ok"]
    assert not hits[0]["perfect"]
    assert result["data"]["result"]["accepted_perfect_count"] == 0
    assert requests.count("hit") == requests.count("finish") == 1
    business = next(c["business"] for c in captures if c.get("step_key") == "hit_business")
    assert business["local_hold_ms"] > 0
    assert business["charge_round_trip_ms"] >= 99
    assert business["hit_round_trip_ms"] >= 649
    assert "secret-charge-ticket" not in str(captures)


def test_skipped_window_is_counted_and_captured_without_a_charge():
    result, hits, requests, captures = run_battle(reveal_ms=3200)
    assert not hits
    assert "charge_start" not in requests
    assert result["data"]["result"]["skipped_window_count"] == 1
    skip = next(c["business"] for c in captures if c.get("step_key") == "window_skipped_business")
    assert skip["window_index"] == 1
    assert skip["center_ms"] == 3000
    assert skip["current_elapsed_ms"] >= 3200
    reveal = next(c["business"] for c in captures if c.get("step_key") == "window_reveal_business")
    assert reveal["center_ms"] == 3000
    assert requests.count("finish") == 1


def test_expired_charge_reply_never_sends_a_late_hit():
    result, hits, requests, captures = run_battle(charge_delays=(2.0, 0.05))
    assert not hits
    assert requests.count("charge_start") == 1
    assert requests.count("finish") == 1
    assert result["data"]["result"]["skipped_window_count"] == 1
    skip = next(c["business"] for c in captures if c.get("step_key") == "window_skipped_business")
    assert skip["reason"] == "expired_before_release"


def test_oversleep_is_rechecked_before_sending_charge_start():
    result, hits, requests, captures = run_battle(oversleep=2)
    assert not hits
    assert "charge_start" not in requests
    assert requests.count("finish") == 1
    assert result["data"]["result"]["skipped_window_count"] == 1
    skip = next(c["business"] for c in captures if c.get("step_key") == "window_skipped_business")
    assert skip["reason"] == "expired_before_charge"


@pytest.mark.parametrize("received_ms", [2100, 2400, 2700, 3050, 3400, 4000])
def test_charge_extension_never_waits_past_window_or_local_cap(received_ms):
    action = {"elapsedMs": 2800, "centerMs": 3000, "perfectMs": 210, "hitMs": 620, "releaseLeadMs": 200}
    target = boss._world_boss_charge_release_target_ms(action, started_ms=2000, received_ms=received_ms)
    assert target >= received_ms
    assert target <= max(received_ms, 3110)
    assert target <= max(received_ms, 3250)
