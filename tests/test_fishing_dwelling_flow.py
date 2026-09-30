from copy import deepcopy
from types import SimpleNamespace

import pytest

from model.features.fishing_dwelling_miniapp import run_native_fishing_flow
from test_fishing_dwelling_journal import Store, context, response, fighting
from test_fishing_dwelling_protocol import Clock


def run(store, *, transport=None, current=lambda: True, clock=None):
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
    )
    return result, calls


def test_full_round_runs_checkpoints_and_settles_once():
    store = Store()
    result, calls = run(store)
    assert result["ok"] and result["status"] == "settled", result
    assert result["data"]["catches"] == {"fish": 2}
    assert calls[:3] == ["context", "cast", "hook"]
    assert calls[-1] == "fight" and calls.count("cast") == calls.count("hook") == calls.count("fight") == 1
    assert calls.count("checkpoint") >= 2


def test_full_round_accepts_official_checkpoint_reply_without_echo():
    store = Store()
    def transport(action, request, clock):
        return {"context": context(), "cast": response(), "hook": fighting(),
                "checkpoint": fighting(), "fight": response(settled=True)}[action]
    result, calls = run(store, transport=transport)
    assert result["ok"] and result["data"]["catches"] == {"fish": 2}
    assert calls.count("cast") == calls.count("hook") == calls.count("fight") == 1
    assert calls.count("checkpoint") >= 2


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
