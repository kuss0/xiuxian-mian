import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model.features import tree_miniapp as worker, tree_runtime as runtime
from test_tree_worker_lifecycle import adapter, scripted_transport


@pytest.fixture
def flow(monkeypatch):
    calls = []

    def proof(mode, _run, **_kwargs):
        score = 75 if mode == "jump" else 17
        return ({"clientScore": score, "durationMs": 1, "charges": [0.5]},
                {"mode": mode, "score": score, "targetScore": score, "durationMs": 1})

    monkeypatch.setattr(worker, "build_tree_game_proof", proof)
    return SimpleNamespace(calls=calls, transport=scripted_transport(calls))


def invoke(flow, kind, **kwargs):
    params = dict(token="tree_FIXTURE133", init_data="fixture133-init", transport=flow.transport,
                  adapter=adapter(), sleeper=lambda _delay: None)
    if kind == "game":
        params.update(mode="jump", submit=True)
    return getattr(worker, f"run_tree_miniapp_{kind}_lab_flow")(**dict(params, **kwargs))


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("change", ["missing_score", "fractional_score", "false_string", "wrong_mode", "wrong_run"])
def test_unconfirmed_submit_keeps_original_round_without_more_mutations(flow, kind, change):
    original = flow.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] != "run_submit":
            return response
        if change == "missing_score":
            response.pop("score")
        elif change == "fractional_score":
            response["score"] = 75.5
        elif change == "false_string":
            response["verified"] = {"ok": "false", "score": 75}
        elif change == "wrong_mode":
            response["mode"] = "fly"
        else:
            response["runToken"] = "different-private-round"
        return response

    flow.transport = transport
    result = invoke(flow, kind)
    assert not result["ok"]
    assert result["outcome_unknown"] and result["open_run"]
    assert flow.calls == [("start", ""), ("run_start", "jump"), ("run_submit", "jump")]
    assert not result["data"].get("runs")
    if change in {"wrong_mode", "wrong_run"}:
        assert not result["data"].get("rewards", {}).get("items")
    else:
        assert result["data"]["rewards"]["items"] == {"fixture_material": 1}
        assert len(result["data"]["partial_receipts"]) == 1


def test_reward_summary_uses_current_explicit_quantities_only():
    current = {"ok": True, "score": 75, "rewards": [{"name": "known", "qty": 2}, {"name": "missing"}],
               "history": [{"rewards": [{"name": "old", "qty": 100}]}],
               "council": {"cultivationGain": 999}}
    before = deepcopy(current)
    assert worker.summarize_tree_rewards(current) == {"items": {"known": 2}, "gains": {}}
    assert current == before


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("change", ["mode", "player", "season", "seed", "run_no", "not_ok"])
def test_invalid_allocation_does_not_submit_a_proof(flow, kind, change):
    original = flow.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "start":
            response["playerId"] = 133
            response["council"]["season"] = {"seasonId": "fixture-season"}
        if request["safe_summary"]["endpoint"] == "run_start":
            key, value = {"mode": ("mode", "fly"), "player": ("player_id", 134),
                          "season": ("seasonId", "other-season"), "seed": ("seed", []),
                          "run_no": ("runNo", True), "not_ok": ("ok", "false")}[change]
            response["run"][key] = value
        return response

    flow.transport = transport
    result = invoke(flow, kind)
    assert not result["ok"] and result["outcome_unknown"] and result["open_run"]
    assert flow.calls == [("start", ""), ("run_start", "jump")]


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("player_id", [133, 133.0, "133", -1_000_000_000_133])
def test_native_optional_metadata_and_data_envelope_remain_usable(flow, kind, player_id):
    original = flow.transport
    allocated = {}

    def transport(request):
        response = original(request)
        endpoint = request["safe_summary"]["endpoint"]
        response["playerId"] = player_id
        if endpoint == "run_start":
            allocated.update(response["run"])
            response["run"].update(playerId=player_id, seasonId="fixture-season", playDate="2026-09-15")
        elif endpoint == "run_submit":
            response.update(runToken=allocated["runToken"], seed=allocated["seed"], runNo=allocated["runNo"],
                            mode=allocated["mode"], seasonId="fixture-season", playDate="2026-09-15")
            response["verified"] = {"ok": True, "hit": False, "score": str(response["score"])}
        return {"ok": True, "data": response}

    flow.transport = transport
    result = invoke(flow, kind, player_id=133)
    assert result["ok"], result
    assert not result["outcome_unknown"] and not result["open_run"]
    public = json.dumps(result)
    assert "private-run131" not in public and "fixture133-init" not in public
    assert result["data"]["rewards"]["items"] == {"fixture_material": 2 if kind == "daily" else 1}


@pytest.mark.parametrize("kind", ["start", "game", "daily"])
def test_production_wrapper_passes_expected_role_to_worker(flow, monkeypatch, kind):
    monkeypatch.setattr(worker, "request_tree_miniapp_init_data", AsyncMock(return_value="fixture-init"))
    original = flow.transport

    def transport(request):
        response = original(request)
        response["playerId"] = 134
        return response

    kwargs = dict(token="tree_FIXTURE133", webview_url="https://t.me/fanrenxiuxian_bot?startapp=tree_FIXTURE133",
                  transport=transport, adapter=adapter(), sleeper=lambda _delay: None)
    if kind == "game":
        kwargs.update(mode="jump", submit=True)
    result = asyncio.run(getattr(worker, f"run_tree_miniapp_{kind}_production_flow")(133, **kwargs))
    assert not result["ok"]
    assert flow.calls == [("start", "")]


@pytest.mark.parametrize("player_id", [0, False])
def test_invalid_expected_role_cannot_be_treated_as_omitted(flow, player_id):
    result = invoke(flow, "daily", player_id=player_id)
    assert not result["ok"]
    assert flow.calls == [("start", "")]


def test_reissued_submitted_round_cannot_duplicate_reward_or_submit(flow):
    flow.transport = scripted_transport(flow.calls, limits={"jump": 2, "fly": 1})
    original = flow.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "run_start":
            response["run"].update(runToken="same-private-round", seed="same-seed")
        return response

    flow.transport = transport
    result = invoke(flow, "daily")
    assert result["outcome_unknown"] and not result["ok"]
    assert flow.calls == [("start", ""), ("run_start", "jump"), ("run_submit", "jump"), ("run_start", "jump")]
    assert len(result["data"]["runs"]) == 1
    assert result["data"]["rewards"]["items"] == {"fixture_material": 1}


@pytest.mark.parametrize("value", [None, True, -1, 1.5, "1e2", 2**53])
def test_invalid_reward_quantity_is_not_defaulted_or_rounded(value):
    assert worker.summarize_tree_rewards({"ok": True, "rewards": [{"name": "bad", "qty": value}]}) == {
        "items": {}, "gains": {},
    }


@pytest.mark.parametrize("second", [0, 1, 3])
def test_reward_aliases_are_not_added_or_allowed_to_override_zero(second):
    data = {"ok": True, "rewards": {"material": 1}, "loot": {"material": second},
            "cultivationGain": 3, "xiuweiGain": 3}
    assert worker.summarize_tree_rewards(data) == {
        "items": {"material": 1} if second == 1 else {}, "gains": {"\u4fee\u4e3a": 3},
    }


def test_partial_materials_retain_confirmed_score_and_stop_new_rounds(flow):
    original = flow.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "run_submit":
            response["rewards"].append({"name": "missing-quantity"})
        return response

    flow.transport = transport
    result = invoke(flow, "daily")
    assert result["status"] == "material_unknown" and not result["ok"]
    assert not result["open_run"] and not result["outcome_unknown"]
    assert result["data"]["runs"][0]["score"] == 75
    assert result["data"]["rewards"]["items"] == {"fixture_material": 1}
    assert len(flow.calls) == 3


@pytest.mark.parametrize("kind", ["game", "daily"])
def test_partial_submit_reports_rewards_without_claiming_completed_round(flow, monkeypatch, kind):
    original = flow.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "run_submit":
            response.pop("score")
        return response

    flow.transport = transport
    result = invoke(flow, kind)
    captured = []
    monkeypatch.setattr(runtime, "append_business_capture", lambda *args, **kwargs: captured.append(kwargs))
    runtime._record_tree_business_capture(None, result, source="fixture", now=133)
    assert captured[0]["detail"]["settled_count"] == 0
    assert captured[0]["detail"]["partial_receipt_count"] == 1
    assert captured[0]["detail"]["items"] == {"fixture_material": 1}
    summary = runtime._format_tree_summary(result)
    assert "fixture_materialx1" in summary
    assert "\u5b8c\u6210 1 \u5c40" not in summary and "\u8df3\u5206 0" not in summary


def test_single_game_summary_reports_actual_materials(flow):
    summary = runtime._format_tree_summary(invoke(flow, "game"))
    assert "fixture_materialx1" in summary
    assert "\u672a\u89e3\u6790\u5230\u65b0\u589e\u7269\u8d44" not in summary
