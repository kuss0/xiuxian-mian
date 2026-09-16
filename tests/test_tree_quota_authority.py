from copy import deepcopy
from types import SimpleNamespace

import pytest

from model.features import tree_miniapp as worker
from test_tree_worker_lifecycle import adapter, panel, scripted_transport


BAD_COUNTS = [None, True, False, -1, 1.5, "1.5", "bad", "1e2", float("nan"), float("inf"), 2**53]


@pytest.fixture
def h(monkeypatch):
    calls = []

    def proof(mode, _run, **_kwargs):
        score = 75 if mode == "jump" else 17
        return ({"clientScore": score, "durationMs": 1, "charges": [0.5]},
                {"mode": mode, "score": score, "targetScore": score, "durationMs": 1})

    monkeypatch.setattr(worker, "build_tree_game_proof", proof)
    return SimpleNamespace(calls=calls, transport=scripted_transport(calls, limits={"jump": 2, "fly": 1}))


def run(h, kind):
    kwargs = dict(token="tree_FIXTURE132", init_data="fixture132-init", transport=h.transport,
                  adapter=adapter(), sleeper=lambda _delay: None)
    if kind == "game":
        kwargs.update(mode="jump", submit=True)
    return getattr(worker, f"run_tree_miniapp_{kind}_lab_flow")(**kwargs)


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("field", ["used", "limit", "remaining"])
@pytest.mark.parametrize("value", BAD_COUNTS)
def test_invalid_current_count_cannot_allocate_a_round(h, kind, field, value):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            response["council"]["daily"]["jump"][field] = value
        return response

    h.transport = transport
    result = run(h, kind)
    assert h.calls == [("start", "")]
    assert not result["ok"] and result["status"] == "quota_unknown"


@pytest.mark.parametrize("quota", [
    {"used": 3, "limit": 2, "remaining": 0},
    {"used": 0, "limit": 0, "remaining": 1},
    {"used": 0, "limit": 2, "remaining": 1},
    {"used": 1, "limit": 2, "remaining": 2},
    {"used": 0, "limit": 2, "remaining": 3},
])
@pytest.mark.parametrize("kind", ["game", "daily"])
def test_inconsistent_quota_cannot_allocate_or_complete(h, kind, quota):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            response["council"]["daily"]["jump"] = quota
        return response

    h.transport = transport
    result = run(h, kind)
    assert h.calls == [("start", "")]
    assert not result["ok"] and result["status"] == "quota_unknown"


@pytest.mark.parametrize("field,value", [("used", "bad"), ("limit", False), ("remaining", -1)])
def test_malformed_exhaustion_is_not_daily_completion(h, field, value):
    def transport(request):
        h.calls.append((request["safe_summary"]["endpoint"], ""))
        response = panel({"jump": 1, "fly": 1})
        response["council"]["daily"]["jump"][field] = value
        return response

    h.transport = transport
    result = run(h, "daily")
    assert h.calls == [("start", "")]
    assert not result["ok"] and result["data"]["phase"] != "completed"


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("convert", [int, str, float])
@pytest.mark.parametrize("remaining", [True, False])
def test_exact_current_counts_remain_usable(h, kind, convert, remaining):
    original = h.transport

    def transport(request):
        response = original(request)
        for quota in response.get("council", {}).get("daily", {}).values():
            for field in ("used", "limit", "remaining"):
                quota[field] = convert(quota[field])
            if not remaining:
                quota.pop("remaining")
        return response

    h.transport = transport
    result = run(h, kind)
    assert result["ok"]
    assert result["status"] == ("completed" if kind == "daily" else "settled")
    assert len([call for call in h.calls if call[0] == "run_start"]) == (3 if kind == "daily" else 1)


@pytest.mark.parametrize("container", ["result", "state", "councilState", "seasonState"])
def test_start_does_not_find_quota_in_unrelated_nested_panels(h, container):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            nested = response["council"] if container.endswith("State") else response
            return {"ok": True, container: nested}
        return response

    h.transport = transport
    result = run(h, "daily")
    assert h.calls == [("start", "")]
    assert not result["ok"] and result["status"] == "quota_unknown"


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("inner_ok", [True, False])
def test_single_data_envelope_is_usable(h, kind, inner_ok):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            if not inner_ok:
                response.pop("ok")
            return {"ok": True, "data": response}
        return response

    h.transport = transport
    result = run(h, kind)
    assert result["ok"] and result["data"]["state"]["ok"]


@pytest.mark.parametrize("kind", ["game", "daily"])
def test_conflicting_current_envelopes_cannot_authorize(h, kind):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            response["data"] = panel({"jump": 1, "fly": 1})
        return response

    h.transport = transport
    result = run(h, kind)
    assert h.calls == [("start", "")]
    assert not result["ok"] and result["status"] == "quota_unknown"


@pytest.mark.parametrize("change", [
    "inconsistent", "limit_change", "sibling_invalid", "sibling_reset", "season_change", "day_change",
])
def test_after_submit_bad_or_backward_quota_stops_new_mutations(h, change):
    original = h.transport

    def transport(request):
        response = original(request)
        if "council" not in response:
            return response
        response["council"]["season"] = {"seasonId": "fixture-season", "dayIndex": 1}
        quotas = response["council"]["daily"]
        if change == "sibling_reset":
            quotas["fly"] = {"used": 1, "limit": 2, "remaining": 1}
        if ("run_submit", "jump") not in h.calls:
            return response
        if change == "inconsistent":
            quotas["jump"] = {"used": 0, "limit": 2, "remaining": 1}
        elif change == "limit_change":
            quotas["jump"] = {"used": 1, "limit": 3, "remaining": 2}
        elif change == "sibling_invalid":
            quotas["fly"]["used"] = False
        elif change == "sibling_reset":
            quotas["fly"] = {"used": 0, "limit": 2, "remaining": 2}
        elif change == "season_change":
            response["council"]["season"]["seasonId"] = "new-season"
        elif change == "day_change":
            response["council"]["season"]["dayIndex"] = 2
        return response

    h.transport = transport
    result = run(h, "daily")
    assert h.calls.count(("run_start", "jump")) == 1
    assert h.calls.count(("run_submit", "jump")) == 1
    assert not any(mode == "fly" for _, mode in h.calls)
    assert not result["ok"] and result["status"] == "quota_unknown"
    assert result["data"]["rewards"]["items"] == {"fixture_material": 1}
    assert len(result["data"]["runs"]) == 1


@pytest.mark.parametrize("issue", ["missing_sibling", "invalid_sibling", "invalid_current", "lost_context"])
def test_authoritative_read_repairs_incomplete_submit_panel(h, issue):
    original = h.transport

    def transport(request):
        response = original(request)
        if "council" not in response:
            return response
        response["council"]["season"] = {"seasonId": "fixture-season", "dayIndex": 1}
        if request["safe_summary"]["endpoint"] != "run_submit":
            return response
        quotas = response["council"]["daily"]
        if issue == "missing_sibling":
            quotas.pop("fly")
        elif issue == "invalid_sibling":
            quotas["fly"]["used"] = "bad"
        elif issue == "invalid_current":
            quotas[request["payload"]["mode"]]["used"] = "bad"
        else:
            response["council"].pop("season")
        return response

    h.transport = transport
    result = run(h, "daily")
    assert result["ok"] and result["status"] == "completed"
    assert h.calls.count(("start", "")) == 4
    assert [mode for endpoint, mode in h.calls if endpoint == "run_start"] == ["jump", "jump", "fly"]
    assert result["data"]["rewards"]["items"] == {"fixture_material": 3}


def test_native_submit_season_state_avoids_unnecessary_read(h):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "run_submit":
            response["seasonState"] = response.pop("council")
        return response

    h.transport = transport
    assert run(h, "daily")["ok"]
    assert h.calls.count(("start", "")) == 1


def test_bad_live_panel_cannot_fall_back_to_nested_historical_quota(h):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            response["result"] = deepcopy(response)
            response["council"] = None
        return response

    h.transport = transport
    result = run(h, "daily")
    assert h.calls == [("start", "")]
    assert not result["ok"]


@pytest.mark.parametrize("season", [None, [], False, "bad", {"seasonId": None}, {"seasonId": ""},
                                   {"dayIndex": False}, {"dayIndex": 1.5}])
@pytest.mark.parametrize("kind", ["game", "daily"])
def test_declared_invalid_season_context_does_not_authorize(h, kind, season):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            response["council"]["season"] = season
        return response

    h.transport = transport
    result = run(h, kind)
    assert h.calls == [("start", "")]
    assert not result["ok"] and result["status"] == "quota_unknown"


@pytest.mark.parametrize("field", ["used", "limit"])
@pytest.mark.parametrize("kind", ["game", "daily"])
def test_missing_required_quota_field_does_not_authorize(h, kind, field):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            response["council"]["daily"]["jump"].pop(field)
        return response

    h.transport = transport
    result = run(h, kind)
    assert h.calls == [("start", "")]
    assert not result["ok"] and result["status"] == "quota_unknown"


@pytest.mark.parametrize("remaining", [True, False])
def test_explicit_zero_limit_is_known_exhaustion(h, remaining):
    def transport(request):
        h.calls.append((request["safe_summary"]["endpoint"], ""))
        response = panel(limits={"jump": 0, "fly": 0})
        if not remaining:
            for quota in response["council"]["daily"].values():
                quota.pop("remaining")
        return response

    h.transport = transport
    assert run(h, "daily")["ok"]
    assert h.calls == [("start", "")]


def test_current_external_quota_consumption_is_not_a_regression(h):
    used, limits = {"jump": 0, "fly": 0}, {"jump": 2, "fly": 1}

    def transport(request):
        endpoint, mode = request["safe_summary"]["endpoint"], request["payload"].get("mode", "")
        h.calls.append((endpoint, mode))
        if endpoint == "run_start":
            return {"ok": True, "run": {"runToken": "fixture132-run", "seed": "fixture132-seed"}}
        if endpoint == "run_submit":
            used[mode] = limits[mode]
            return {**panel(used, limits=limits), "score": 75 if mode == "jump" else 17}
        assert endpoint == "start"
        return panel(used, limits=limits)

    h.transport = transport
    assert run(h, "daily")["ok"]
    assert [mode for endpoint, mode in h.calls if endpoint == "run_start"] == ["jump", "fly"]


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("ok", [False, "false", None, 0])
@pytest.mark.parametrize("scope", ["council", "daily", "mode"])
def test_negative_current_panel_cannot_authorize_using_its_counts(h, kind, ok, scope):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            target = response["council"]
            if scope != "council":
                target = target["daily"]
            if scope == "mode":
                target = target["jump"]
            target["ok"] = ok
        return response

    h.transport = transport
    result = run(h, kind)
    assert h.calls == [("start", "")]
    assert not result["ok"] and result["status"] == "quota_unknown"


@pytest.mark.parametrize("container", ["council", "seasonState"])
def test_negative_submit_panel_needs_current_read_before_more_rounds(h, container):
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "run_submit":
            council = response.pop("council")
            response[container] = {**council, "ok": False}
        return response

    h.transport = transport
    assert run(h, "daily")["ok"]
    assert h.calls.count(("start", "")) == 4
