from copy import deepcopy
from types import SimpleNamespace

import pytest

from model.features.fishing_dwelling_miniapp import run_native_fishing_flow
from test_fishing_dwelling_journal import Store, context, response, fighting
from test_fishing_dwelling_protocol import Clock


def run(store, *, transport=None, current=lambda: True, clock=None, recovery_only=False, capture_sink=None,
        upload_checkpoints=True):
    clock = clock or Clock()
    calls = []

    def dispatch(request):
        action = request["safe_summary"]["endpoint"]
        calls.append(action)
        if action in {"cast", "hook", "checkpoint", "fight"}:
            assert store.record["pending_action"] == action
        if transport:
            return transport(action, request, clock)
        if action == "context":
            return context()
        if action in {"cast", "state"}:
            return response()
        if action == "hook":
            return fighting()
        if action == "checkpoint":
            return {"ok": True}
        if action == "fight":
            return response(settled=True)
        pytest.fail(action)

    result = run_native_fishing_flow(
        journal=store.open(), token="df_FIXTURE_NATIVE", init_data="fixture-init-data",
        site_id="west-shore", model_id="ngw", bait_id="bait", transport=dispatch,
        operation_check=current, monotonic=lambda: clock.now, sleeper=clock.sleep,
        recovery_only=recovery_only,
        capture_sink=capture_sink,
        upload_checkpoints=upload_checkpoints,
    )
    return result, calls


def test_recovery_only_empty_journal_does_not_start_a_rod():
    store = Store()
    result, calls = run(store, recovery_only=True)
    assert calls == [] and store.record == {}
    assert result["status"] == "recovery_not_needed"


@pytest.mark.parametrize("reason", ["fishing_rod_missing", "fishing_daily_limit_reached",
                                    "fishing_companion_sailing", "fishing_companion_missing"])
def test_ineligible_identity_with_no_bait_never_prepares_supply_or_cast(reason):
    store, calls, clock = Store(), [], Clock()

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        data = context()
        data["context"]["baits"] = []
        if reason == "fishing_rod_missing":
            data["context"]["rod"] = None
        elif reason == "fishing_daily_limit_reached":
            data["context"]["quota"] = {"used": 5, "remaining": 0, "limit": 5}
        else:
            data["context"]["unavailable"] = reason
            data["context"]["enabled"] = False
            if reason == "fishing_companion_missing":
                data["context"]["rod"] = None
        return data

    result = run_native_fishing_flow(
        journal=store.open(), token="df_FIXTURE_NATIVE", init_data="fixture", site_id="west-shore",
        model_id="ngw", bait_id="", bait_choice="bait", supply_settings={"auto_buy": True},
        transport=transport, operation_check=lambda: True, monotonic=lambda: clock.now, sleeper=clock.sleep,
    )
    assert result["error"] == reason and not result["outcome_unknown"]
    assert calls == ["context"] and store.record == {}


def test_unowned_active_rod_is_not_treated_as_a_no_rod_skip():
    data = context()
    data["context"]["rod"] = None
    data["session"] = response()["session"]
    store = Store()
    result, calls = run(store, transport=lambda *args: data)
    assert result["error"] == "native_existing_session"
    assert calls == ["context"] and store.record == {}


@pytest.mark.parametrize("settled", [False, True])
def test_recovery_only_reads_original_session_without_resuming_fight(settled):
    store = Store()
    ledger = store.open()
    query, _ = ledger.start(context=context(), site_id="west-shore", model_id="ngw", bait_id="bait")
    ledger.accept(query, fighting())
    before = deepcopy(store.record)
    result, calls = run(store, recovery_only=True, transport=lambda *args: response(settled=True) if settled else fighting())
    assert calls == ["state"]
    assert store.record["cast_id"] == before["cast_id"]
    assert result["status"] == ("settled" if settled else "recovery_wait")


def test_full_round_runs_checkpoints_and_settles_once():
    store = Store()
    result, calls = run(store)
    assert result["ok"] and result["status"] == "settled", result
    assert result["data"]["catches"] == {"fish": 2}
    assert calls[:3] == ["context", "cast", "hook"]
    assert calls[-1] == "fight" and calls.count("cast") == calls.count("hook") == calls.count("fight") == 1
    assert calls.count("checkpoint") >= 2


def test_diagnostic_write_failure_does_not_change_gameplay_result():
    def broken_capture(_):
        raise OSError("fixture disk unavailable")
    result, calls = run(Store(), capture_sink=broken_capture)
    assert result["ok"] and result["status"] == "settled"
    assert calls.count("cast") == calls.count("fight") == 1


def test_final_only_fight_retains_full_real_time_proof_and_durable_intent():
    from model.features.fishing_dwelling_protocol import fight_steps
    store, clock = Store(), Clock()
    hook_time = []
    planned = list(fight_steps(fighting()["session"]["fight"]))[-1][1]

    def transport(action, request, clock):
        if action == "hook":
            hook_time.append(clock.now)
            return fighting()
        if action == "fight":
            assert clock.now - hook_time[0] >= planned["durationMs"] / 1000 - 1e-8
            assert request["payload"]["fishingProof"] == planned
            assert store.record["pending_payload"]["fishingProof"] == planned
            assert store.record["pending_action"] == "fight"
            return response(settled=True)
        return {"context": context(), "cast": response()}[action]

    result, calls = run(store, clock=clock, transport=transport, upload_checkpoints=False)
    assert result["ok"] and not result["outcome_unknown"]
    assert calls == ["context", "cast", "hook", "fight"]


def test_final_only_fight_timeout_keeps_exact_final_intent_without_retries():
    store = Store()
    def transport(action, request, clock):
        if action == "fight":
            raise TimeoutError("final outcome unknown")
        return {"context": context(), "cast": response(), "hook": fighting()}[action]
    result, calls = run(store, transport=transport, upload_checkpoints=False)
    assert result["outcome_unknown"] and calls == ["context", "cast", "hook", "fight"]
    assert store.record["pending_action"] == "fight"
    assert store.record["pending_payload"]["fishingProof"]["landed"] is True


def test_final_only_fight_still_checks_cancellation_between_game_ticks():
    store, clock = Store(), Clock()
    active = [True]
    def transport(action, request, clock):
        if action == "hook":
            active[0] = False
            return fighting()
        return {"context": context(), "cast": response()}[action]
    result, calls = run(store, clock=clock, transport=transport, current=lambda: active[0], upload_checkpoints=False)
    assert result["status"] == "cancelled"
    assert calls == ["context", "cast", "hook"]


def test_full_round_accepts_official_checkpoint_reply_without_echo():
    store = Store()
    def transport(action, request, clock):
        return {"context": context(), "cast": response(), "hook": fighting(),
                "checkpoint": fighting(), "fight": response(settled=True)}[action]
    result, calls = run(store, transport=transport)
    assert result["ok"] and result["data"]["catches"] == {"fish": 2}
    assert calls.count("cast") == calls.count("hook") == calls.count("fight") == 1
    assert calls.count("checkpoint") >= 2


@pytest.mark.parametrize("reply_delay", [.02, .3, 1.0])
def test_checkpoint_confirmations_do_not_compress_next_send_interval(reply_delay):
    store, clock = Store(), Clock()
    last_confirmation = [None]
    interval = fighting()["session"]["fight"].get("checkpointIntervalMs", 2500) / 1000

    def transport(action, request, clock):
        if action == "checkpoint":
            if last_confirmation[0] is not None:
                assert clock.now - last_confirmation[0] >= interval - 1e-9
            clock.sleep(reply_delay)
            last_confirmation[0] = clock.now
            return {"ok": True}
        return {"context": context(), "cast": response(), "hook": fighting(),
                "fight": response(settled=True)}[action]

    result, calls = run(store, transport=transport, clock=clock)
    assert result["ok"], result
    assert calls.count("checkpoint") >= 2


@pytest.mark.parametrize("cast_up,cast_down,hook_up", [(.330, .058, .050), (.950, .020, .030),
                                                    (.050, .338, .050), (.194, .194, .050)])
def test_fast_hook_after_asymmetric_cast_does_not_precede_server_bite(cast_up, cast_down, hook_up):
    window = {}
    captures = []

    def transport(action, request, clock):
        if action == "cast":
            clock.sleep(cast_up)
            stamp = round(10000 + clock.now * 1000)
            window.update(biteAt=stamp + 29000, expiresAt=stamp + 33000)
            data = response()
            data["session"].update(serverNow=stamp, **window)
            clock.sleep(cast_down)
            return data
        if action == "hook":
            clock.sleep(hook_up)
            assert window["biteAt"] <= 10000 + clock.now * 1000 < window["expiresAt"]
            return fighting()
        return {"context": context(), "checkpoint": fighting(), "fight": response(settled=True)}[action]

    result, calls = run(Store(), transport=transport, capture_sink=captures, upload_checkpoints=False)
    assert result["ok"], result
    assert calls == ["context", "cast", "hook", "fight"]
    observed = result["checkpoint_observations"]
    cast = next(row for row in observed if row["action"] == "cast")
    assert cast["bite_at_ms"] == window["biteAt"]
    assert cast["expires_at_ms"] == window["expiresAt"]
    assert cast["same_session"] is False  # The cast response establishes ownership.
    assert captures[-1]["observations"] == observed


def test_slow_asymmetric_transport_can_still_hook_inside_four_second_window():
    store = Store()
    window = {}
    def transport(action, request, clock):
        if action == "cast":
            clock.sleep(.950)
            stamp = 10000 + clock.now * 1000
            window.update(biteAt=stamp + 15000, expiresAt=stamp + 19000)
            data = response()
            data["session"].update(serverNow=stamp, **window)
            clock.sleep(2.312)
            return data
        if action == "hook":
            clock.sleep(2.7)
            received = 10000 + clock.now * 1000
            assert window["biteAt"] <= received < window["expiresAt"]
            clock.sleep(.68)
            return fighting()
        return {"context": context(), "checkpoint": fighting(), "fight": response(settled=True)}[action]
    result, calls = run(store, transport=transport)
    assert result["ok"] and calls.count("cast") == calls.count("hook") == calls.count("fight") == 1


@pytest.mark.parametrize("check_delay", [6, 10, 30])
def test_pre_sleep_validation_time_counts_toward_bite_wait(monkeypatch, check_delay):
    from model.features import fishing_dwelling_protocol as protocol
    clock, store = Clock(), Store()
    original = protocol.next_session_action
    delayed = [False]
    window = {}

    def next_action(*args):
        result = original(*args)
        if result[0] == "wait":
            delayed[0] = True
        return result

    def current():
        if delayed[0]:
            delayed[0] = False
            clock.sleep(check_delay)
        return True

    def transport(action, request, clock):
        if action == "cast":
            stamp = 10000 + clock.now * 1000
            window.update(biteAt=stamp + 20000, expiresAt=stamp + 24000)
            data = response()
            data["session"].update(serverNow=stamp, **window)
            return data
        if action == "hook":
            assert window["biteAt"] <= 10000 + clock.now * 1000 < window["expiresAt"]
            return fighting()
        return {"context": context(), "checkpoint": fighting(), "fight": response(settled=True)}[action]

    monkeypatch.setattr(protocol, "next_session_action", next_action)
    result, calls = run(store, clock=clock, current=current, transport=transport)
    if check_delay >= 24:
        assert not result["ok"] and result["error"] == "native_hook_outside_window"
        assert calls == ["context", "cast"]
        assert store.record["phase"] == "session_owned" and not store.record["pending_action"]
        return
    assert result["ok"], result
    assert calls.count("cast") == calls.count("hook") == calls.count("fight") == 1


@pytest.mark.parametrize("failed", ["cast", "hook", "checkpoint", "fight"])
@pytest.mark.parametrize("failure", ["timeout", "429", "unadvanced"])
def test_uncertain_mutation_never_retries_or_recasts(failed, failure):
    store = Store()

    def transport(action, request, clock):
        if action == failed:
            if failure == "timeout":
                raise TimeoutError("fixture")
            if failure == "429":
                return SimpleNamespace(status_code=429, headers={"Retry-After": "60"}, json=lambda: {"ok": False})
            if action == "checkpoint":
                reply = fighting()
                reply["session"]["fight"]["checkpoint"] = {"durationMs": 0, "events": [], "details": {}}
                return reply
            return {"ok": True, "session": None} if action == "cast" else response() if action == "hook" else fighting()
        return {"context": context(), "cast": response(), "hook": fighting(), "checkpoint": {"ok": True}}[action]

    result, calls = run(store, transport=transport)
    assert result["outcome_unknown"] and result["status"] == "operation_pending", result
    assert calls[-1] == failed and calls.count(failed) == 1
    assert store.record["pending_action"] == failed
    if failure == "429":
        assert result["retry_after_sec"] == 60


def test_missing_rod_or_bait_and_voyage_never_cast_or_change_toggle():
    for changes in ({"rod": None}, {"baits": []}, {"unavailable": "fishing_companion_sailing"}):
        store = Store()
        data = context()
        data["context"].update(changes)
        result, calls = run(store, transport=lambda *args: data)
        assert not result["ok"] and not result["outcome_unknown"]
        assert calls == ["context"] and store.record == {}


@pytest.mark.parametrize("readback", ["old", "accepted", "timeout", "foreign"])
def test_stale_checkpoint_reads_same_rod_once_without_replay(readback):
    store = Store()
    captures = []
    last_accepted = []
    clock = Clock()
    rejected_at = []

    def transport(action, request, clock):
        if action == "checkpoint":
            if not last_accepted:
                last_accepted.append(deepcopy(request["payload"]))
                return fighting()
            rejected_at.append(clock.now)
            return SimpleNamespace(status_code=409, headers={}, json=lambda: {"ok": False, "error": "fishing_checkpoint_stale"})
        if action == "state":
            assert clock.now - rejected_at[0] < 1
            assert request["payload"]["sessionId"] == store.record["session_id"]
            if readback == "timeout":
                raise TimeoutError("read unavailable")
            data = fighting()
            payload = store.record["pending_payload"] if readback == "accepted" else last_accepted[0]
            data["session"]["fight"]["checkpoint"] = {
                "durationMs": payload["fishingProof"]["durationMs"], "events": payload["fishingProof"]["events"],
                "details": payload["checkpointState"],
            }
            if readback == "foreign":
                data["session"]["sessionId"] = "other"
            data["token"] = "do-not-expose"
            return data
        return {"context": context(), "cast": response(), "hook": fighting()}[action]

    result, calls = run(store, transport=transport, clock=clock, capture_sink=captures)
    assert calls == ["context", "cast", "hook", "checkpoint", "checkpoint", "state"]
    assert result["error"] == "fishing_checkpoint_stale"
    assert store.record["phase"] == "session_owned"
    assert store.record["pending_action"] == ("" if readback == "accepted" else "checkpoint")
    observed = result["checkpoint_observations"]
    assert captures[-1]["step_key"] == "native_checkpoint_observation"
    assert captures[-1]["observations"] == observed
    assert "do-not-expose" not in str(observed)
    if readback in {"old", "accepted"}:
        assert observed[-1]["same_session"] is True
        assert observed[-1]["checkpoint_ms"] == (5000 if readback == "accepted" else 2500)
    else:
        assert observed[-1]["readback_failed"] is True


def test_recovered_completed_round_never_requests_context_or_new_cast():
    store = Store()
    def fail_cast(action, request, clock):
        if action == "context":
            return context()
        raise TimeoutError("cast response lost")
    run(store, transport=fail_cast)
    old_cast = store.record["cast_id"]
    result, calls = run(store, transport=lambda *args: response(settled=True))
    assert calls == ["state"] and result["ok"]
    assert store.record["cast_id"] == old_cast
    result, calls = run(store)
    assert result["ok"] and calls == []


def test_budget_wait_cannot_hook_after_expiry():
    store = Store()
    clock = Clock()
    def transport(action, request, clock):
        if action == "context":
            return context()
        data = response()
        data["session"].update(serverNow=17900, biteAt=17900, expiresAt=18000)
        return data
    result, calls = run(store, transport=transport, clock=clock)
    assert calls == ["context", "cast"]
    assert store.record["pending_action"] == ""
    assert not result["outcome_unknown"]


def test_confirmed_result_survives_ui_disable_but_does_not_send_more():
    store = Store()
    allowed = [True]
    def transport(action, request, clock):
        if action == "context":
            return context()
        if action == "cast":
            allowed[0] = False
            return response(settled=True)
        pytest.fail("unexpected next request")
    result, calls = run(store, transport=transport, current=lambda: allowed[0])
    assert calls == ["context", "cast"] and result["data"]["settled_count"] == 1
    assert store.record["phase"] == "settled"


def test_replaced_owner_receipt_cannot_be_applied():
    store = Store()
    def transport(action, request, clock):
        if action == "context":
            return context()
        store.owned = False
        return response(settled=True)
    result, calls = run(store, transport=transport)
    assert calls == ["context", "cast"]
    assert not result["ok"] and store.record["phase"] == "cast_pending"


def test_settling_only_uses_bounded_read_queries():
    store = Store()
    def transport(action, request, clock):
        if action == "context":
            return context()
        data = response()
        data["session"]["status"] = "settling"
        return data
    result, calls = run(store, transport=transport)
    assert calls == ["context", "cast"] + ["state"] * 8
    assert result["status"] == "wait_state" and not result["outcome_unknown"]
