import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import trial_miniapp as trial
from model.features import trial_runtime as runtime
from model.features.trial_receipts import parse_trial_finish_receipt, parse_trial_rewards
from model.webapp_core import MiniAppRequestPolicy


PLAYER = 991220001


def challenge(index=1):
    return {
        "challengeId": f"fixture122-{index}", "mode": "tianjiMeridianV1", "sequence": ["p1"],
        "points": [{"id": "p1", "x": 12, "y": 34}], "minDurationMs": 20, "maxDurationMs": 1000,
    }


def receipt(**extra):
    return {"ok": True, "result": {"settled_in_app": True, "traceGain": 3}, **extra}


def run(finishes, *, max_rounds=1):
    calls = []
    finish = Mock(side_effect=finishes)

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            return finish()
        return {"ok": True, "challenge": challenge(1 + calls.count("next"))}

    result = trial.run_trial_miniapp_loop_lab_flow(
        token="trial_FIXTURE122", init_data="query_id=fixture&hash=FIXTURE122_SECRET",
        player_id=PLAYER, transport=transport, max_rounds=max_rounds, sleeper=lambda _delay: None,
        adapter=replace(trial.build_trial_miniapp_adapter(), request_policy=MiniAppRequestPolicy(min_interval_sec=0)),
    )
    return result, calls


@pytest.mark.parametrize("body", [
    {"ok": True}, {"ok": True, "result": None}, {"ok": True, "result": {}},
    {"ok": True, "result": {"ok": False, "traceGain": 99}},
    {"ok": True, "result": {"settled_in_app": False, "traceGain": 99}},
    {"ok": True, "result": {"settled_in_app": "true", "traceGain": 99}},
    {"ok": True, "result": {"status": "pending", "traceGain": 99}},
    {"ok": True, "account": {"rewards": [{"name": "fixture material", "qty": 99}]}},
])
def test_unconfirmed_finish_cannot_count_a_round_or_continue(body):
    result, calls = run([body, receipt()], max_rounds=2)
    assert not result["ok"]
    assert result["status"] == "result_unconfirmed"
    assert result["data"]["settled_count"] == 0
    assert calls == ["start", "finish"]
    assert runtime._trial_batch_materials(result) == ({}, {})


@pytest.mark.parametrize("key,value", [("challengeId", "foreign-round"), ("playerId", PLAYER + 1)])
def test_finish_explicit_binding_mismatch_cannot_settle(key, value):
    body = receipt()
    body["result"][key] = value
    result, calls = run([body])
    assert not result["ok"]
    assert result["status"] == "result_unconfirmed"
    assert result["data"]["settled_count"] == 0
    assert calls == ["start", "finish"]


@pytest.mark.parametrize("remaining", [False, True, -1, 0.9, "0.9", None, "", float("nan")])
def test_invalid_quota_keeps_current_reward_but_cannot_claim_daily_completion(remaining):
    body = receipt(dailyProgress={"remaining": remaining}, nextChallenge=challenge(2))
    result, calls = run([body, receipt()], max_rounds=2)
    assert result["status"] == "partial"
    assert "quota" in result["error"]
    assert result["data"]["settled_count"] == 1
    assert runtime._trial_batch_materials(result) == ({}, {"\u5929\u673a\u6b8b\u75d5": 3})
    assert calls == ["start", "finish"]


def test_conflicting_quota_sources_do_not_authorize_next_round():
    body = receipt(
        dailyProgress={"completed": 1, "limit": 3, "remaining": 2},
        nextTrial={"dailyLimit": 3, "remainingToday": 0}, nextChallenge=challenge(2),
    )
    result, calls = run([body, receipt()], max_rounds=2)
    assert result["status"] == "partial"
    assert "quota" in result["error"]
    assert calls == ["start", "finish"]


def test_unrelated_remaining_field_is_not_daily_exhaustion():
    body = receipt()
    body["result"]["remaining"] = 0
    result, calls = run([body, receipt()], max_rounds=2)
    assert result["data"]["settled_count"] == 2
    assert calls == ["start", "finish", "next", "finish"]


def test_duplicate_embedded_challenge_is_not_submitted_twice():
    body = receipt(nextChallenge=challenge(1))
    result, calls = run([body, receipt()], max_rounds=2)
    assert result["status"] == "partial"
    assert result["data"]["settled_count"] == 1
    assert calls == ["start", "finish"]


def test_explicit_zero_reward_settlement_is_not_discarded():
    body = {"ok": True, "result": {"settled_in_app": True, "traceGain": 0}}
    result, calls = run([body])
    assert result["ok"]
    assert result["data"]["settled_count"] == 1
    assert calls == ["start", "finish"]


@pytest.mark.parametrize("result", [
    {"settled_in_app": True}, {"settledInApp": True}, {"traceGain": 0}, {"expGain": 2},
    {"reward_trace": 3}, {"rewardTrace": "3"}, {"reward": 3, "ready": True},
    {"score": 0, "grade": "fixture-grade"},
    {"settled_in_app": True, "daily_progress": 3, "daily_limit": 3, "reward_trace": 3},
    {"rewards": [{"name": "fixture item", "qty": 2}]},
])
@pytest.mark.parametrize("nested", [False, True])
def test_supported_finish_shapes_still_complete(result, nested):
    body = {"ok": True, "result": result}
    if nested:
        body = {"ok": True, "data": body}
    output, calls = run([body])
    assert output["ok"]
    assert output["status"] == "settled"
    assert output["data"]["settled_count"] == 1
    assert calls == ["start", "finish"]


@pytest.mark.parametrize("scope", ["root", "result", "nested"])
@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_explicit_completion_flags_must_be_true_booleans(scope, value):
    body = receipt()
    if scope == "root":
        body["settled_in_app"] = value
    elif scope == "result":
        body["result"]["settled_in_app"] = value
    else:
        body = {"ok": True, "data": {**body, "ok": value}}
    result, _calls = run([body])
    assert not result["ok"]
    assert result["data"]["settled_count"] == 0


@pytest.mark.parametrize("field,value", [
    ("challengeId", None), ("challenge_id", "foreign"), ("challengeId", True),
    ("playerId", False), ("player_id", "wrong"), ("playerId", 0), ("playerId", None),
])
@pytest.mark.parametrize("scope", ["root", "result"])
def test_invalid_or_conflicting_binding_stops_projection(field, value, scope):
    body = receipt()
    (body if scope == "root" else body["result"])[field] = value
    result, calls = run([body])
    assert not result["ok"]
    assert result["data"]["results"] == []
    assert calls == ["start", "finish"]


def test_matching_binding_aliases_are_allowed_without_republishing_ids():
    body = receipt()
    body.update(playerId=str(PLAYER), challenge_id="fixture122-1")
    body["result"].update(player_id=PLAYER, challengeId="fixture122-1")
    result, _calls = run([body])
    assert result["ok"]
    projection = result["data"]["results"][0]
    assert not {"playerId", "player_id", "challengeId", "challenge_id"} & projection.keys()


@pytest.mark.parametrize("field", ["history", "account", "previous", "data", "challenge"])
def test_unrelated_result_metadata_cannot_inject_materials(field):
    body = receipt()
    body["result"][field] = {"traceGain": 900, "rewards": {"fixture false item": 99}, "challengeId": "foreign"}
    result, _calls = run([body])
    assert result["ok"]
    assert runtime._trial_batch_materials(result) == ({}, {"\u5929\u673a\u6b8b\u75d5": 3})
    assert field not in result["data"]["results"][0]


@pytest.mark.parametrize("quota,expected", [
    ({"dailyProgress": {"completed": 1, "limit": 3}}, {"completed": 1, "limit": 3, "remaining": 2}),
    ({"dailyProgress": {"done": "3", "limit": 3.0, "remaining": 0}}, {"completed": 3, "limit": 3, "remaining": 0}),
    ({"nextTrial": {"dailyLimit": 3, "remainingToday": 2}}, {"completed": 1, "limit": 3, "remaining": 2}),
    ({"dailyProgress": {"remaining": 0}, "nextTrial": None}, {"remaining": 0}),
    ({"dailyProgress": {}}, {}), ({}, {}),
])
def test_current_quota_fields_and_exact_arithmetic(quota, expected):
    result = parse_trial_finish_receipt(receipt(**quota), challenge_id="fixture122-1", player_id=PLAYER)
    assert result["confirmed"]
    assert not result["quota_error"]
    assert result["quota"] == expected


@pytest.mark.parametrize("quota", [
    {"dailyProgress": None}, {"dailyProgress": []}, {"nextTrial": "unknown"},
    {"dailyProgress": {"remaining": 2, "remainingCount": 1}},
    {"dailyProgress": {"completed": 1, "done": 2}},
    {"dailyProgress": {"completed": 1, "limit": 3, "remaining": 0}},
    {"dailyProgress": {"completed": 4, "limit": 3}},
    {"dailyProgress": {"remaining": 4, "limit": 3}},
    {"dailyProgress": {"limit": 0}}, {"dailyProgress": {"limit": True}},
    {"dailyProgress": {"remaining": float("inf")}}, {"dailyProgress": {"remaining": 2**53}},
    {"dailyProgress": {"remaining": "99999999999999999999999"}},
])
def test_bad_quota_cannot_erase_receipt_or_authorize_another_round(quota):
    result, calls = run([receipt(**quota), receipt()], max_rounds=2)
    assert result["ok"]
    assert result["status"] == "partial"
    assert result["data"]["settled_count"] == 1
    assert "quota" in result["error"]
    assert calls == ["start", "finish"]


@pytest.mark.parametrize("quota", [
    {"remaining": 0}, {"trial": {"remainingToday": 0}},
    {"account": {"dailyProgress": {"remaining": 0}}},
    {"history": {"dailyProgress": {"remaining": 0}}},
])
def test_unscoped_or_historical_quota_is_not_current(quota):
    result, calls = run([receipt(**quota), receipt()], max_rounds=2)
    assert result["status"] == "settled"
    assert result["data"]["settled_count"] == 2
    assert calls == ["start", "finish", "next", "finish"]


def test_valid_exhaustion_stops_without_opening_next():
    result, calls = run([receipt(dailyProgress={"completed": 3, "limit": 3, "remaining": 0})], max_rounds=99)
    assert result["status"] == "settled"
    assert result["data"]["settled_count"] == 1
    assert calls == ["start", "finish"]


def test_requested_one_round_completion_keeps_quota_warning_without_extra_request():
    result, calls = run([receipt(dailyProgress={"remaining": False})])
    assert result["ok"]
    assert result["status"] == "settled"
    assert result["events"][-1]["step"] == "quota"
    assert calls == ["start", "finish"]


def test_legacy_daily_quota_is_scoped_and_derived():
    body = receipt()
    body["result"].update(daily_progress=3, daily_limit=3)
    result, calls = run([body], max_rounds=99)
    assert result["status"] == "settled"
    assert calls == ["start", "finish"]


@pytest.mark.parametrize("value", [False, "3.4", -2, None, float("nan")])
def test_invalid_gain_does_not_erase_explicit_settlement_or_create_gain(value):
    body = receipt()
    body["result"]["traceGain"] = value
    result, calls = run([body, receipt()], max_rounds=2)
    assert result["status"] == "partial"
    assert result["data"]["settled_count"] == 1
    assert runtime._trial_batch_materials(result) == ({}, {})
    assert calls == ["start", "finish"]


@pytest.mark.parametrize("gain", [{"reward": 3}, {"traceGain": 3, "reward_trace": "3", "rewardTrace": 3}])
def test_legacy_and_matching_gain_aliases_are_counted_once(gain):
    result, _calls = run([{"ok": True, "result": gain}])
    assert result["status"] == "settled"
    assert runtime._trial_batch_materials(result) == ({}, {"\u5929\u673a\u6b8b\u75d5": 3})


@pytest.mark.parametrize("gain", [
    {"traceGain": 3, "reward_trace": 4}, {"reward_trace": 4, "traceGain": 3},
    {"traceGain": 3, "reward_trace": False}, {"traceGain": False, "reward_trace": 3},
])
def test_conflicting_gain_aliases_hold_only_the_uncertain_gain(gain):
    body = {"ok": True, "result": {"settled_in_app": True, "expGain": 5, **gain}}
    result, calls = run([body, receipt()], max_rounds=2)
    assert result["status"] == "partial"
    assert result["data"]["settled_count"] == 1
    assert runtime._trial_batch_materials(result) == ({}, {"\u7ecf\u9a8c": 5})
    assert calls == ["start", "finish"]


@pytest.mark.parametrize("value,expected", [
    ([{"name": "a", "qty": 2}], [{"name": "a", "qty": 2}]),
    ({"a": 2, "b": 0}, [{"name": "a", "qty": 2}]),
    ({"name": "a", "quantity": "2", "count": 2}, [{"name": "a", "qty": 2}]),
    ({"a": {"qty": 2}}, [{"name": "a", "qty": 2}]),
    (["a", "b"], [{"name": "a", "qty": 1}, {"name": "b", "qty": 1}]),
    ([{"name": "a", "qty": 0}], []),
])
def test_supported_rewards_have_explicit_normalized_quantities(value, expected):
    assert parse_trial_rewards(value) == (expected, "")


@pytest.mark.parametrize("bad", [
    {"name": "bad", "qty": False}, {"name": "bad", "qty": -1}, {"name": "bad", "qty": 0.1},
    {"name": "bad", "qty": None}, {"name": "bad"}, {"name": "bad", "qty": 1, "count": 2},
    {"name": "bad", "itemName": "conflict", "qty": 2}, {"name": None, "qty": 2},
])
def test_bad_reward_retains_known_receipt_and_other_valid_reward(bad):
    body = receipt()
    body["result"]["rewards"] = [{"name": "valid", "qty": 2}, bad]
    result, calls = run([body, receipt()], max_rounds=2)
    assert result["status"] == "partial"
    assert result["data"]["settled_count"] == 1
    assert runtime._trial_batch_materials(result) == ({"valid": 2}, {"\u5929\u673a\u6b8b\u75d5": 3})
    assert calls == ["start", "finish"]


def test_zero_item_quantity_is_never_invented_as_one():
    body = receipt()
    body["result"]["rewards"] = [{"name": "zero", "qty": 0}]
    result, _calls = run([body])
    assert result["status"] == "settled"
    assert runtime._trial_batch_materials(result) == ({}, {"\u5929\u673a\u6b8b\u75d5": 3})


def test_later_unconfirmed_finish_preserves_only_earlier_settlement():
    result, calls = run([receipt(), {"ok": True, "result": {"ok": False, "traceGain": 99}}], max_rounds=2)
    assert result["status"] == "partial"
    assert result["data"]["settled_count"] == 1
    assert runtime._trial_batch_materials(result) == ({}, {"\u5929\u673a\u6b8b\u75d5": 3})
    assert calls == ["start", "finish", "next", "finish"]


@pytest.mark.parametrize("player_id", [False, True, 0, "", "invalid", 1.2, "1.2", float("nan"), 2**53])
def test_invalid_selection_stops_before_authorization_and_http(monkeypatch, player_id):
    authorize, transport = AsyncMock(), Mock()
    monkeypatch.setattr(trial, "request_trial_miniapp_init_data", authorize)
    result = asyncio.run(trial.run_trial_miniapp_production_flow(
        PLAYER, token="trial_FIXTURE122", webview_url="https://t.me/fanrenxiuxian_bot?startapp=trial_FIXTURE122",
        player_id=player_id, transport=transport,
    ))
    assert not result["ok"]
    assert result["error"] == "trial_player_id_invalid"
    authorize.assert_not_awaited()
    transport.assert_not_called()


def test_unselected_auth_identity_does_not_accept_conflicting_reflected_players():
    body = receipt()
    body["playerId"] = PLAYER
    body["result"]["player_id"] = PLAYER + 1
    result = parse_trial_finish_receipt(body, challenge_id="fixture122-1")
    assert not result["confirmed"]
    assert result["error"] == "trial_finish_player_mismatch"


@pytest.mark.parametrize("player_id", [PLAYER, str(PLAYER), -100991220001, "-100991220001"])
def test_valid_user_and_channel_player_selections_are_preserved(player_id):
    request = trial.build_trial_miniapp_request("start", token="trial_FIXTURE122", init_data="fixture", player_id=player_id)
    assert request["payload"]["playerId"] == int(player_id)
    body = receipt()
    body["result"]["playerId"] = str(player_id)
    assert parse_trial_finish_receipt(body, challenge_id="fixture122-1", player_id=player_id)["confirmed"]


@pytest.mark.parametrize("player_id", [None, PLAYER, -100991220001])
@pytest.mark.parametrize("key,value", [
    ("token", "trial_OTHER122_SECRET"), ("playerId", PLAYER + 1),
    ("initData", "query_id=other&hash=OTHER122_SECRET"),
])
def test_request_payload_cannot_override_reserved_metadata(player_id, key, value):
    with pytest.raises(ValueError, match="^trial_payload_reserved$"):
        trial.build_trial_miniapp_request(
            "finish", token="trial_FIXTURE122", init_data="fixture", player_id=player_id,
            payload={"trialProof": {"challengeId": "fixture122-1"}, key: value},
        )


@pytest.mark.parametrize("player_id", [None, PLAYER, "-100991220001"])
def test_request_keeps_proof_and_explicit_metadata_without_mutating_payload(player_id):
    payload = {"trialProof": {"challengeId": "fixture122-1"}}
    request = trial.build_trial_miniapp_request(
        "finish", token="trial_FIXTURE122", init_data="fixture", player_id=player_id, payload=payload,
    )
    expected = {"token": "trial_FIXTURE122", "initData": "fixture", **payload}
    if player_id is not None:
        expected["playerId"] = int(player_id)
    assert request["payload"] == expected
    assert payload == {"trialProof": {"challengeId": "fixture122-1"}}


@pytest.mark.parametrize("ids", [
    {"challengeId": True}, {"challengeId": False}, {"challengeId": {"id": "fixture"}},
    {"challengeId": ["fixture"]}, {"challengeId": 1.5}, {"challengeId": float("nan")},
    {"challengeId": float("inf")}, {"challengeId": None, "id": "fixture"},
    {"challengeId": "", "id": "fixture"}, {"challengeId": "fixture", "id": "other"},
    {"challengeId": "fixture", "id": True}, {"challengeId": 2**53},
])
def test_invalid_challenge_identifiers_cannot_reach_finish(ids):
    current = {key: value for key, value in challenge().items() if key != "challengeId"}
    current.update(ids)
    transport = Mock(return_value={"ok": True, "challenge": current})
    result = trial.run_trial_miniapp_loop_lab_flow(
        token="trial_FIXTURE122", init_data="fixture", player_id=PLAYER,
        transport=transport, max_rounds=1, sleeper=lambda _delay: None,
    )
    assert not result["ok"]
    assert result["status"] == "solve_failed"
    assert result["data"]["settled_count"] == 0
    assert [call.args[0]["safe_summary"]["endpoint"] for call in transport.call_args_list] == ["start"]


@pytest.mark.parametrize("ids,expected", [
    ({"challengeId": " fixture "}, "fixture"), ({"id": "legacy-fixture"}, "legacy-fixture"),
    ({"challengeId": 123}, "123"), ({"challengeId": 123, "id": "123"}, "123"),
    ({"challengeId": "fixture", "id": " fixture "}, "fixture"),
])
def test_supported_challenge_identifiers_preserve_proof_binding(ids, expected):
    current = {key: value for key, value in challenge().items() if key != "challengeId"}
    current.update(ids)
    assert trial.build_trial_proof(current)["challengeId"] == expected


@pytest.mark.parametrize("value", [True, False, 1.0, [], {"id": "fixture"}, 2**53, None, ""])
def test_finish_requires_a_typed_expected_challenge_identifier(value):
    result = parse_trial_finish_receipt(receipt(), challenge_id=value, player_id=PLAYER)
    assert not result["confirmed"]
    assert result["error"] == "trial_finish_challenge_missing"


@pytest.mark.parametrize("status", ["settled", "completed", "done", "success"])
def test_explicit_terminal_result_with_no_reward_is_still_a_settlement(status):
    result, calls = run([{"ok": True, "result": {"status": status}}])
    assert result["ok"]
    assert result["data"]["settled_count"] == 1
    assert runtime._trial_batch_materials(result) == ({}, {})
    assert calls == ["start", "finish"]


@pytest.mark.parametrize("extra", ["result", "dailyProgress", "nextTrial"])
def test_mixed_current_envelopes_are_not_combined(extra):
    body = {"ok": True, "data": receipt(), extra: {"traceGain": 99, "remaining": 0}}
    result, calls = run([body])
    assert not result["ok"]
    assert result["data"]["settled_count"] == 0
    assert "ambiguous" in result["error"]
    assert calls == ["start", "finish"]
