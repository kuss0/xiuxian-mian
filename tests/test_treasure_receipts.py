from copy import deepcopy
import asyncio
from unittest.mock import Mock

import pytest

from model.features import cave_treasure_runtime as runtime
from model.features import cave_treasure_miniapp as worker
from model.features.treasure_receipts import project_treasure_settlement, treasure_quota_exhausted
from model import state as state_module, ui
from test_treasure_lifecycle import IDENTITY, INIT, TOKEN, call, panel, receipt, round_state, run, scripted_transport
from test_treasure_lifecycle import h as h


@pytest.mark.parametrize("payload", [
    {"ok": True, "account": {"playerId": IDENTITY}},
    {"ok": True, "account": {"playerId": IDENTITY}, "history": {"hunt": {"used": 0, "limit": 3}}},
    {"ok": True, "account": {"playerId": IDENTITY}, "dwelling": {"hunt": {"used": 0}}},
])
def test_missing_current_quota_cannot_authorize_a_new_round(payload):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "start":
            calls.append("start")
            return deepcopy(payload)
        return base(request)

    result = run(transport)
    assert calls == ["start"]
    assert not result["ok"]


def test_remaining_quota_does_not_reset_used_to_zero():
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "start":
            calls.append("start")
            value = panel(2, 2)
            value["dwelling"]["hunt"].pop("used")
            return value
        return base(request)

    result = run(transport)
    assert calls == ["start"]
    assert result["ok"] and result["status"] == "daily_limit"
    assert result["data"]["state"]["games_used"] == 2


@pytest.mark.parametrize("changes", [
    {"used": False}, {"used": 0.5}, {"used": -1}, {"limit": True}, {"limit": 2.5},
    {"remaining": 99}, {"remaining": None}, {"used": 2, "remaining": 1},
])
def test_invalid_or_conflicting_quota_cannot_dispatch(changes):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "start":
            calls.append("start")
            value = panel()
            value["dwelling"]["hunt"].update(changes)
            return value
        return base(request)

    result = run(transport)
    assert calls == ["start"]
    assert not result["ok"]


@pytest.mark.parametrize("endpoint", ["start", "hunt", "hunt_reveal", "hunt_settle"])
def test_foreign_player_response_never_drives_followup_or_rewards(endpoint):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] == endpoint:
            value["account"] = {"playerId": IDENTITY + 1}
        return value

    result = run(transport)
    assert calls[-1] == endpoint
    assert not result["ok"] and not result["data"]["results"]
    assert bool(result.get("outcome_unknown")) is (endpoint != "start")


def test_reveal_cannot_switch_to_a_different_hunt_session():
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] == "hunt_reveal":
            value["huntRun"]["sessionId"] = "foreign-session"
        return value

    result = run(transport)
    assert calls == ["start", "hunt", "hunt_reveal"]
    assert result["outcome_unknown"] and not result["data"]["results"]


@pytest.mark.parametrize("value", [
    {"unexpected": True}, {"sessionId": "foreign-session", "loot": []},
    {"sessionId": "fixture125-1", "ok": False, "loot": [{"name": "phantom", "quantity": 9}]},
    {"sessionId": "fixture125-1", "completed": False, "loot": []},
])
def test_unrecognized_or_contradictory_settlement_never_counts(value):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        data = base(request)
        if request["safe_summary"]["endpoint"] == "hunt_settle":
            data["huntResult"] = deepcopy(value)
        return data

    result = run(transport)
    assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
    assert result["outcome_unknown"] and not result["data"]["results"]


@pytest.mark.parametrize("quantity", [0, -1, False, True, None, 1.5, "bad"])
def test_invalid_or_zero_reward_quantity_is_not_fabricated(quantity):
    item = receipt()
    item["loot"][0]["quantity"] = quantity
    result = {"ok": True, "status": "daily_limit", "data": {"settled_count": 1, "results": [item]}}
    assert runtime._cave_treasure_inventory_items(result) == {}


def test_nested_history_or_diagnostics_are_not_current_rewards():
    item = receipt()
    item["diagnostic"] = {"loot": [{"name": "phantom", "quantity": 99}], "cultivationGain": 999}
    result = {"ok": False, "status": "result_unknown", "data": {"settled_count": 1, "results": [item]}}
    assert runtime._cave_treasure_inventory_items(result) == {"fixture_item": 1}
    assert "phantom" not in runtime._format_cave_treasure_summary(result)
    assert "999" not in runtime._format_cave_treasure_summary(result)


def test_log_only_rewards_sum_between_distinct_settlements():
    result = {"ok": True, "status": "daily_limit", "data": {"settled_count": 2, "results": [
        {"sessionId": "first", "grade": "fixture", "loot": [], "logs": ["\u83b7\u5f97fixture_item x2\u3002"]},
        {"sessionId": "second", "grade": "fixture", "loot": [], "logs": ["\u83b7\u5f97fixture_item x3\u3002"]},
    ]}}
    assert runtime._cave_treasure_inventory_items(result) == {"fixture_item": 5}


def flow_result(*rows, ok=True):
    return {"ok": ok, "status": "daily_limit" if ok else "result_unknown", "data": {
        "settled_count": len(rows), "results": list(rows),
    }}


@pytest.mark.parametrize("quota", [
    {"used": 0, "limit": 2}, {"remaining": 2, "limit": 2},
    {"used": "0", "limit": "2", "remaining": "2"},
    {"used": 0.0, "limit": 2.0, "remaining": 2.0},
])
def test_valid_current_quota_completes_two_distinct_rounds(quota):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] == "start":
            value["dwelling"]["hunt"] = quota
        return value

    result = run(transport)
    assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle", "hunt", "hunt_reveal", "hunt_settle"]
    assert result["ok"] and result["data"]["settled_count"] == 2
    assert treasure_quota_exhausted(result["data"]["state"])
    assert runtime._cave_treasure_inventory_items(result) == {"fixture_item": 2}


@pytest.mark.parametrize("settle_quota", [True, False])
def test_known_active_session_can_finish_without_start_or_reveal_quota(settle_quota):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return {"ok": True, "account": {"playerId": IDENTITY}, "huntRun": round_state()}
        if endpoint == "hunt_reveal":
            return {"ok": True, "huntRun": round_state(revealed=True)}
        if endpoint == "hunt_settle":
            return {**(panel(1, 1) if settle_quota else {"ok": True}), "huntResult": receipt()}
        raise AssertionError(endpoint)

    result = run(transport)
    assert calls == ["start", "hunt_reveal", "hunt_settle"]
    assert result["data"]["settled_count"] == 1
    assert not result["outcome_unknown"]
    assert result["status"] == ("daily_limit" if settle_quota else "blocked")


@pytest.mark.parametrize("endpoint", ["hunt", "hunt_reveal", "hunt_settle"])
def test_missing_mutation_quota_does_not_authorize_unobserved_extra_round(endpoint):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] == endpoint:
            value.pop("dwelling", None)
        return value

    result = run(transport)
    assert not result["outcome_unknown"]
    if endpoint == "hunt_settle":
        assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
        assert result["status"] == "blocked" and result["data"]["settled_count"] == 1
    else:
        assert result["ok"] and result["data"]["settled_count"] == 2


@pytest.mark.parametrize("quota", [
    {"used": 0, "limit": 2, "remaining": 2},
    {"used": 1, "limit": 3, "remaining": 2},
    {"used": 1, "limit": 2, "remaining": False},
])
def test_bad_post_settlement_quota_preserves_receipt_but_cannot_open_next_round(quota):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] == "hunt_settle":
            value["dwelling"]["hunt"] = quota
        return value

    result = run(transport)
    assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
    assert result["status"] == "blocked" and not result["outcome_unknown"]
    assert result["data"]["results"] == [receipt()]
    assert not treasure_quota_exhausted(result["data"]["state"])


def test_unchanged_quota_across_an_entered_round_cannot_authorize_more_rounds():
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if "dwelling" in value:
            value["dwelling"]["hunt"] = panel()["dwelling"]["hunt"]
        return value

    result = run(transport)
    assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
    assert result["error"] == "hunt_quota_not_advanced"
    assert result["data"]["results"] == [receipt()]


@pytest.mark.parametrize("endpoint", ["hunt", "hunt_reveal", "hunt_settle"])
def test_second_round_wrong_player_keeps_only_the_first_receipt(endpoint):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if calls.count(endpoint) == 2 and request["safe_summary"]["endpoint"] == endpoint:
            value["account"] = {"playerId": IDENTITY + 1}
        return value

    result = run(transport)
    assert calls[-1] == endpoint and calls.count(endpoint) == 2
    assert result["outcome_unknown"] and result["data"]["results"] == [receipt()]
    assert runtime._cave_treasure_inventory_items(result) == {"fixture_item": 1}


@pytest.mark.parametrize("scope", ["outer", "identity", "run", "result"])
@pytest.mark.parametrize("player", [None, True, 0, "bad", IDENTITY + 1])
def test_all_present_player_echoes_are_checked(scope, player):
    calls = []
    base = scripted_transport(calls)
    endpoint = "hunt_settle" if scope == "result" else "hunt"

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] != endpoint:
            return value
        if scope == "outer":
            return {"ok": True, "playerId": player, "data": value}
        if scope == "identity":
            value["identity"] = {"selectedPlayerId": player}
        else:
            value["huntResult" if scope == "result" else "huntRun"]["player_id"] = player
        return value

    result = run(transport)
    assert calls[-1] == endpoint and result["outcome_unknown"]
    assert result["data"]["settled_count"] == 0


def test_selected_start_requires_account_not_just_selector_echo():
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        value = panel()
        value.pop("account")
        value["identity"] = {"selectedPlayerId": IDENTITY}
        return value

    result = run(transport)
    assert calls == ["start"] and result["error"] == "cave_action_player_missing"


def test_current_hint_search_ignores_historical_found_and_settled_text():
    value = {**panel(), "huntRun": round_state(), "history": {
        "text": "\u53d1\u73b0\u4e3b\u5b9d\uff0c\u7ed3\u7b97\u5b8c\u6210", "huntResult": receipt(),
    }}
    parsed = worker.parse_cave_treasure_state(value)
    assert not parsed["treasure_found"] and not parsed["settled"]
    assert worker.choose_cave_treasure_action(parsed)["action"] == "search"


@pytest.mark.parametrize("bad_session", [None, True, 1, "", " ", [], {}])
@pytest.mark.parametrize("action", ["search", "settle"])
def test_action_builder_rejects_missing_or_invalid_session(action, bad_session):
    with pytest.raises(ValueError, match="hunt_run_session_missing"):
        worker.build_cave_treasure_action_request(
            {"action": action, "sessionId": bad_session, "targetIndex": 1}, token=TOKEN, init_data=INIT,
        )


def test_repeated_settled_session_is_not_revealed_or_counted_again():
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] == "hunt" and calls.count("hunt") == 2:
            value["huntRun"] = round_state()
        return value

    result = run(transport)
    assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle", "hunt"]
    assert result["outcome_unknown"] and result["data"]["results"] == [receipt()]


@pytest.mark.parametrize("endpoint", ["hunt_reveal", "hunt_settle"])
def test_daily_limit_reply_does_not_complete_an_active_round(endpoint):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == endpoint:
            calls.append(endpoint)
            return 409, {"ok": False, "error": "hunt_daily_limit"}
        return base(request)

    result = run(transport)
    assert result["outcome_unknown"] and result["status"] == "result_unknown"
    assert not result["data"]["results"]


@pytest.mark.parametrize("quota", [None, {"used": 2, "limit": 2, "remaining": 0}])
def test_explicit_enter_daily_limit_is_terminal_without_inferred_counts(quota):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "hunt":
            calls.append("hunt")
            return 409, {"ok": False, "error": "hunt_daily_limit", **({"hunt": quota} if quota else {})}
        return base(request)

    result = run(transport)
    assert result["ok"] and calls == ["start", "hunt"]
    assert treasure_quota_exhausted(result["data"]["state"])
    assert result["data"]["state"]["games_used"] == (2 if quota else 0)


@pytest.mark.parametrize("bad_quota", [
    {"used": 0, "limit": 2, "remaining": 2}, {"used": True, "limit": 2},
    {"used": 2}, {"remaining": None, "limit": 2},
])
def test_explicit_daily_limit_with_contradictory_quota_is_not_daily_completion(bad_quota):
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return 409, {"ok": False, "error": "hunt_daily_limit", "hunt": bad_quota}

    result = run(transport)
    assert calls == ["start"] and not result["ok"]
    assert not treasure_quota_exhausted(result["data"]["state"])


@pytest.mark.parametrize("status", [429, 500, 503])
def test_server_or_throttle_error_cannot_assert_daily_completion(status):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "hunt":
            calls.append("hunt")
            return status, {"ok": False, "error": "hunt_daily_limit"}
        return base(request)

    result = run(transport)
    assert calls == ["start", "hunt"]
    assert result["outcome_unknown"] and not result["ok"]


@pytest.mark.parametrize("quantity", [None, True, -1, 1.5, "1.5", "1e2", float("inf"), 2**53])
def test_invalid_structured_quantity_cannot_be_resurrected_by_log(quantity):
    row = {"loot": [{"name": "fixture_item", "quantity": quantity}], "logs": ["\u83b7\u5f97fixture_item x2."]}
    assert runtime._cave_treasure_inventory_items(flow_result(row)) == {}
    assert project_treasure_settlement(row)["material_error"]


@pytest.mark.parametrize("item", [
    {"name": "fixture_item", "quantity": 2, "qty": 3},
    {"name": "fixture_item", "itemName": "different", "quantity": 2},
    {"name": True, "quantity": 2}, {"name": "fixture_item"},
])
def test_conflicting_or_missing_reward_aliases_do_not_invent_materials(item):
    assert runtime._cave_treasure_inventory_items(flow_result({"loot": [item]})) == {}


def test_valid_reward_aliases_and_container_mirrors_are_counted_once():
    row = {"loot": [{"name": "fixture_item", "itemName": "fixture_item", "quantity": "2", "qty": 2.0}],
           "rewards": {"fixture_item": 2}, "text": "\u83b7\u5f97fixture_item x2.",
           "logs": ["\u83b7\u5f97fixture_item x2."]}
    assert runtime._cave_treasure_inventory_items(flow_result(row)) == {"fixture_item": 2}


@pytest.mark.parametrize("reverse", [True, False])
def test_text_mirrors_are_order_independent_and_distinct_log_gains_sum(reverse):
    fields = [("text", "\u83b7\u5f97fixture_item x5."), ("logs", ["\u83b7\u5f97fixture_item x2.", "\u83b7\u5f97fixture_item x3."])]
    if reverse:
        fields.reverse()
    assert runtime._cave_treasure_inventory_items(flow_result(dict(fields))) == {"fixture_item": 5}


@pytest.mark.parametrize("text", ["fixture_item x1.5", "fixture_item x1e3", "fixture_item x1,5", "fixture_item x-2"])
def test_invalid_log_amount_is_not_truncated_into_a_reward(text):
    assert runtime._cave_treasure_inventory_items(flow_result({"loot": [], "logs": [text]})) == {}


def test_structured_zero_and_invalid_gain_aliases_do_not_use_log_fallback():
    row = {"loot": [{"name": "fixture_item", "quantity": 0}], "cultivationGain": 0,
           "stoneGain": 1, "spiritStoneGain": True,
           "text": "\u83b7\u5f97fixture_item x5. \u4fee\u4e3a +5 \u7075\u77f3 +8"}
    assert runtime._cave_treasure_materials(flow_result(row)) == ({}, {})


def test_distinct_rounds_sum_numeric_and_text_gains_without_recursive_diagnostics():
    first = {"sessionId": "one", "cultivationGain": 2, "xiuwei_gain": "2", "loot": [],
             "logs": ["\u7075\u77f3 +25"], "debug": {"cultivationGain": 999}}
    second = {"sessionId": "two", "cultivationGain": 3, "loot": [], "logs": ["\u7075\u77f3 +33"]}
    assert runtime._cave_treasure_materials(flow_result(first, second, ok=False)) == ({}, {"\u4fee\u4e3a": 5, "\u7075\u77f3": 58})


def test_empty_business_failure_receipt_is_still_a_completed_round():
    row = {"grade": "\u5931\u8d25", "score": 0, "loot": [], "foundMain": False}
    parsed = project_treasure_settlement(row, session_id="request-session")
    assert parsed["confirmed"] and not parsed["error"]
    assert "sessionId" not in parsed["result"]
    assert runtime._cave_treasure_materials(flow_result(row)) == ({}, {})


def test_native_consumers_count_only_recognized_current_receipts(h, monkeypatch):
    result = flow_result(receipt(), {"unexpected": True}, {"ok": False, "loot": [{"name": "phantom", "quantity": 9}]})
    result["data"]["settled_count"] = 99
    result["data"]["history"] = {"loot": [{"name": "phantom", "quantity": 88}]}
    h.flow.return_value = result
    capture = Mock(return_value={})
    monkeypatch.setattr(runtime, "append_business_capture", capture)
    h.originals["_record_cave_treasure_business_capture"](None, result, source="fixture", now=h.now)
    response = asyncio.run(call(h, "public"))
    assert response["extra"]["settled_count"] == 1
    assert response["extra"]["rewards"] == {"fixture_item": 1}
    assert not response["extra"]["daily_exhausted"]
    assert capture.call_args.kwargs["detail"]["settled_count"] == 1
    assert "phantom" not in response["message"]


@pytest.mark.parametrize("record_state, due", [
    ({"games_used": 2, "games_limit": 2}, True),
    ({"games_used": True, "games_limit": 1, "quota_verified": True}, True),
    ({"games_used": 2, "games_limit": 2, "quota_verified": False}, True),
    ({"games_used": 2, "games_limit": 2, "quota_verified": True}, False),
    ({"games_used": 2, "games_limit": 2, "quota_verified": True, "in_round": True}, True),
    ({"daily_limit_confirmed": True}, False),
])
def test_scheduler_uses_verified_quota_not_legacy_or_invalid_counts(h, record_state, due):
    state_module.set_miniapp_state_records({f"{IDENTITY}:cave_treasure": {"updated_at": h.now, "state": record_state}})
    assert ui._cave_public_background_action_due("treasure", IDENTITY, h.now) is due


@pytest.mark.parametrize("field, value", [("completed", False), ("finished", 1), ("status", "pending")])
@pytest.mark.parametrize("nested", [False, True])
def test_settlement_cannot_contradict_outer_completion_flags(field, value, nested):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        data = base(request)
        if request["safe_summary"]["endpoint"] == "hunt_settle":
            data = {"ok": True, "data": data} if nested else data
            data[field] = value
        return data

    result = run(transport)
    assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
    assert result["outcome_unknown"] and result["data"]["settled_count"] == 0


def test_start_limit_rejection_with_active_round_is_not_daily_completion():
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return 409, {"ok": False, "error": "hunt_daily_limit", "huntRun": round_state(),
                     "hunt": {"used": 2, "limit": 2, "remaining": 0}}

    result = run(transport)
    assert calls == ["start"] and not result["ok"]
    assert result["data"]["state"]["in_round"]
    assert not treasure_quota_exhausted(result["data"]["state"])


@pytest.mark.parametrize("session", [False, "different"])
def test_start_rejects_conflicting_session_aliases_before_a_mutation(session):
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return {**panel(), "huntRun": {**round_state(), "session_id": session}}

    result = run(transport)
    assert calls == ["start"] and result["status"] == "blocked"


def test_native_materials_do_not_double_count_same_normalized_session():
    first, second = receipt(), receipt()
    second["sessionId"] = " " + second["sessionId"] + " "
    assert runtime._cave_treasure_inventory_items(flow_result(first, second)) == {"fixture_item": 1}


def test_invalid_materials_do_not_erase_settlement_and_are_reported():
    calls = []
    base = scripted_transport(calls, limit=1)

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] == "hunt_settle":
            value["huntResult"]["loot"][0]["quantity"] = -1
        return value

    result = run(transport)
    assert result["ok"] and result["data"]["settled_count"] == 1
    assert result["data"]["material_errors"] and not result["outcome_unknown"]
    assert runtime._cave_treasure_inventory_items(result) == {}
    assert "\u672a\u8ba1\u5165" in runtime._format_cave_treasure_summary(result)


def test_terminal_start_snapshot_is_not_an_active_run_or_a_new_receipt():
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return {**panel(1, 1), "huntRun": {"sessionId": "previous", "status": "settled"},
                "huntResult": {"sessionId": "previous", "loot": [{"name": "old", "quantity": 99}]}}

    result = run(transport)
    assert calls == ["start"] and result["ok"]
    assert result["data"]["state"]["settled"] and not result["data"]["state"]["in_round"]
    assert not result["data"]["results"]
    assert runtime._cave_treasure_inventory_items(result) == {}


def test_inventory_capture_identity_ignores_diagnostic_and_history_sessions():
    original = {"results": [receipt()]}
    extended = deepcopy(original)
    extended["history"] = {"sessionId": "old", "loot": [{"name": "phantom", "quantity": 99}]}
    extended["results"][0]["diagnostic"] = {"sessionId": "different"}
    assert runtime._cave_treasure_inventory_source_id(original) == runtime._cave_treasure_inventory_source_id(extended)


@pytest.mark.parametrize("text", ["\u5c1a\u672a\u627e\u5230\u4e3b\u5b9d", "\u7ee7\u7eed\u5bfb\u627e\u79d8\u5b9d"])
def test_explicit_not_found_cannot_be_overridden_by_narrative(text):
    parsed = worker.parse_cave_treasure_state({**panel(), "huntRun": {**round_state(), "text": text}})
    assert not parsed["treasure_found"]
    assert worker.choose_cave_treasure_action(parsed)["action"] == "search"


@pytest.mark.parametrize("flag", [None, 0, 1, "false", "true", [], {}])
def test_invalid_main_flag_cannot_authorize_early_settlement_or_search(flag):
    parsed = worker.parse_cave_treasure_state({**panel(), "huntRun": {**round_state(), "foundMain": flag}})
    assert worker.choose_cave_treasure_action(parsed)["action"] == "blocked"
