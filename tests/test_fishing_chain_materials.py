import asyncio
import copy
from unittest.mock import Mock

import pytest

from model import persistence, state as state_module
from model.features import fishing_miniapp as adapter
from model.features import fishing_runtime as fishing
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_worker_lifecycle import INIT, TOKEN, adapter_with_limit, response


fishing_env = lifecycle.fishing_env
fishing_db = lifecycle.fishing_db
REAL_HARVEST = fishing._send_fishing_miniapp_harvest_summary
EXPERIENCE = "\u9493\u672f\u7ecf\u9a8c"


def run_chain(rounds, *, failure=False):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        payload = response(endpoint)
        if endpoint == "next":
            payload["token"] = f"fish_CHAIN109_{calls.count('next')}"
        if endpoint == "result":
            index = calls.count("result") - 1
            if index < len(rounds):
                payload["result"] = copy.deepcopy(rounds[index])
            else:
                return {"ok": False, "error": "fixture later failure"}
        return payload

    result = adapter.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=len(rounds) + int(failure), result_poll_limit=1, sleeper=lambda _delay: None,
    )
    assert calls.count("finish") == len(rounds) + int(failure)
    return result


def reward_only(name="empty-reward", qty=2):
    return {"expGain": 4, "caught": False, "bonusLoot": [{"name": name, "qty": qty}]}


def fish_round():
    return {"expGain": 7, "details": {
        "fish": {"name": "fixture-fish"}, "rewards": [{"name": "attached-reward", "qty": 3}],
    }}


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("rounds,expected", [
    ([reward_only("first"), reward_only("second")], {"first": 2, "second": 2}),
    ([reward_only(), reward_only()], {"empty-reward": 4}),
    ([reward_only(), fish_round()], {"empty-reward": 2, "attached-reward": 3}),
    ([fish_round(), reward_only()], {"empty-reward": 2, "attached-reward": 3}),
])
def test_all_confirmed_round_materials_survive_the_chain(fishing_env, rounds, expected, failure):
    h = fishing_env
    result = run_chain(rounds, failure=failure)
    assert result["data"]["settled_count"] == len(rounds)
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(result, h.now)
    summary = fishing._normalize_fishing_daily_catch_summary(h.identity["fishing_daily_catch_summary_json"])
    assert summary["rewards"] == expected
    assert summary["rods"] == len(rounds)
    assert h.identity["fishing_daily_count"] == len(rounds)
    assert summary["fish"] == ({"fixture-fish": 1} if any("details" in item for item in rounds) else {})


@pytest.mark.parametrize("caller", ["public", "message"])
def test_callers_capture_and_commit_mixed_round_rewards_once(fishing_env, monkeypatch, caller):
    h = fishing_env
    h.result.clear()
    h.result.update(run_chain([reward_only(), fish_round()], failure=True))
    capture = Mock(return_value={})
    monkeypatch.setattr(fishing, "append_business_capture", capture)
    asyncio.run(lifecycle.run_caller(h, caller))
    summary = fishing._normalize_fishing_daily_catch_summary(h.identity["fishing_daily_catch_summary_json"])
    assert summary["rewards"] == {"empty-reward": 2, "attached-reward": 3}
    detail = capture.call_args.kwargs["detail"]
    assert detail["items"] == [{"name": "attached-reward", "qty": 3}, {"name": "empty-reward", "qty": 2}]
    assert detail["gains"] == {EXPERIENCE: 11}
    h.items.assert_called_once_with(h.identity_id, {"fixture-fish": 1}, persist=False)


@pytest.mark.parametrize("field", ["last_result", "last_details", "last_bonusLoot", "rounds", "shop", "session", "proof"])
def test_diagnostic_data_does_not_supply_current_materials(fishing_env, monkeypatch, field):
    h = fishing_env
    historical = {"bonusLoot": [{"name": "history-only", "qty": 9}], "expGain": 99}
    value = historical["bonusLoot"] if field == "last_bonusLoot" else historical
    h.result["data"] = {"settled_count": 1, "catches": [], "rewards": [], field: value}
    capture = Mock(return_value={})
    monkeypatch.setattr(fishing, "append_business_capture", capture)
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
        assert not asyncio.run(REAL_HARVEST(h.result))
        fishing._record_fishing_business_capture(Mock(), h.result, source="fixture", now=h.now)
    summary = fishing._normalize_fishing_daily_catch_summary(h.identity["fishing_daily_catch_summary_json"])
    assert summary["rewards"] == {}
    assert "history-only" not in h.identity["fishing_last_result"]
    detail = capture.call_args.kwargs["detail"]
    assert detail["items"] == [] and detail["gains"] == {}


@pytest.mark.parametrize("data", [
    {"expGain": 4, "experienceGain": 4},
    {"expGain": 4, "details": {"expGain": 4}},
    {"expGain": 4, "last_expGain": 4},
    {"expGain": 4, "last_result": {"expGain": 4}},
    {"expGain": 4, "rounds": [{"expGain": 4}]},
])
def test_gain_aliases_and_diagnostics_do_not_multiply_income(data):
    assert fishing._extract_miniapp_numeric_gains(data) == {EXPERIENCE: 4}


@pytest.mark.parametrize("data", [
    {"expGain": 0, "experienceGain": 4},
    {"expGain": 3, "experienceGain": 4},
    {"expGain": None, "experienceGain": 4},
    {"expGain": True}, {"expGain": "4"}, {"expGain": 1.5}, {"expGain": -1},
])
def test_invalid_or_conflicting_numeric_gains_do_not_authorize_income(data):
    assert fishing._extract_miniapp_numeric_gains(data) == {}


@pytest.mark.parametrize("qty", [0, -1, True, False, None, "3", 1.5])
def test_invalid_or_zero_item_quantities_are_not_fabricated(fishing_env, qty):
    h = fishing_env
    h.result["data"] = {"settled_count": 1, **reward_only(qty=qty)}
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    summary = fishing._normalize_fishing_daily_catch_summary(h.identity["fishing_daily_catch_summary_json"])
    assert summary["rewards"] == {}
    assert h.identity["fishing_daily_count"] == 1


@pytest.mark.parametrize("wrapper", ["result", "details", "detail"])
def test_current_single_round_wrappers_keep_reward_only_outcomes(fishing_env, wrapper):
    h = fishing_env
    h.result["data"] = {wrapper: reward_only()}
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    summary = fishing._normalize_fishing_daily_catch_summary(h.identity["fishing_daily_catch_summary_json"])
    assert summary["rewards"] == {"empty-reward": 2}
    assert fishing._extract_miniapp_numeric_gains(h.result["data"]) == {EXPERIENCE: 4}


def test_missing_quantity_is_one_but_explicit_empty_rewards_block_fallback(fishing_env):
    h = fishing_env
    h.result["data"] = {"settled_count": 1, "catches": [], "rewards": [{"name": "one-reward"}],
                        "last_result": reward_only("history-only")}
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    summary = fishing._normalize_fishing_daily_catch_summary(h.identity["fishing_daily_catch_summary_json"])
    assert summary["rewards"] == {"one-reward": 1}


@pytest.mark.parametrize("wrapper", ["result", "details", "detail"])
def test_explicit_empty_current_reward_family_is_not_replaced(wrapper):
    data = {"rewards": [], wrapper: reward_only()}
    assert fishing._extract_miniapp_loose_rewards(data) == []


@pytest.mark.parametrize("name", [[], {}, True, 7, None])
def test_malformed_reward_names_are_not_invented(name):
    assert fishing._extract_miniapp_loose_rewards({"rewards": [{"name": name, "qty": 2}]}) == []


def test_quantity_without_item_name_is_not_a_reward_map():
    assert fishing._extract_miniapp_loose_rewards({"rewards": {"qty": 2}}) == []


@pytest.mark.parametrize("data,expected", [
    ({"rewards": {"gem": 2}}, [{"name": "gem", "qty": 2}]),
    ({"rewards": "gem"}, [{"name": "gem", "qty": 1}]),
    ({"rewards": [{"name": "gem", "qty": 0}]}, []),
    ({"rewards": [{"name": "gem", "qty": 2, "count": 0}]}, []),
    ({"rewards": [{"name": "gem", "qty": float("nan")}]}, []),
    ({"rewards": [{"name": "gem", "qty": float("inf")}]}, []),
    ({"rewards": [{"name": "gem", "qty": 2}], "bonusLoot": [{"name": "gem", "qty": 2}]}, [{"name": "gem", "qty": 2}]),
    ({"rewards": [{"name": "gem", "qty": 2}], "bonusLoot": [{"name": "gem", "qty": 3}]}, []),
])
def test_current_reward_encodings_have_one_strict_quantity(data, expected):
    assert fishing._extract_miniapp_loose_rewards(data) == expected


@pytest.mark.parametrize("wrapper", ["result", "details", "detail"])
def test_nested_explicit_aggregate_keeps_separate_rewards(wrapper):
    data = {wrapper: {"catches": [{"fish": "fixture-fish"}], "rewards": [{"name": "gem", "qty": 2}]}}
    assert fishing._extract_miniapp_loose_rewards(data) == [{"name": "gem", "qty": 2}]


@pytest.mark.parametrize("shape", ["mapping", "nested", "text"])
def test_catch_attached_zero_or_empty_rewards_do_not_fall_back(shape):
    if shape == "text":
        data = "\u9493\u83b7\u3010fixture-fish\u3011\n\u4f34\u751f\u673a\u7f18\uff1a\u3010gem\u3011x0"
    elif shape == "nested":
        data = {"fish": {"name": "fixture-fish", "rewards": []}, "rewards": [{"name": "old", "qty": 2}]}
    else:
        data = {"catches": [{"fish": "fixture-fish", "rewards": [{"name": "gem", "qty": 0}]}]}
    catches = adapter.extract_fishing_miniapp_catches(data)
    assert len(catches) == 1
    assert catches[0]["rewards"] == []


def test_ambiguous_single_round_catches_do_not_start_another_round():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        payload = response(endpoint)
        if endpoint == "result":
            payload["result"] = {"catches": [{"fish": "first"}, {"fish": "second"}]}
        return payload

    result = adapter.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=2, result_poll_limit=1, sleeper=lambda _delay: None,
    )
    assert "next" not in calls
    assert result["data"]["settled_count"] == 1
    assert result["data"]["catches"] == []
    assert result["status"] == "failed"


@pytest.mark.parametrize("data,expected", [
    ({"expGain": 0, "result": {"expGain": 4}}, {}),
    ({"expGain": float("nan")}, {}),
    ({"expGain": float("inf")}, {}),
    ({"result": {"expGain": 4}, "details": {"experienceGain": 4}}, {EXPERIENCE: 4}),
    ({"result": {"expGain": 4}, "details": {"experienceGain": 5}}, {}),
    ({"lingShiGain": 8, "spiritStoneGain": 8}, {"\u7075\u77f3": 8}),
])
def test_gain_projection_uses_current_explicit_and_consistent_fields(data, expected):
    assert fishing._extract_miniapp_numeric_gains(data) == expected


@pytest.mark.parametrize("catches", [[], [{"fish": "same"}, {"fish": "same"}]])
def test_nested_current_catches_override_history_without_content_dedupe(catches):
    data = {"result": {"catches": catches, "last_result": {"fish": "history-only"}}}
    assert [row["fish"] for row in adapter.extract_fishing_miniapp_catches(data)] == [row["fish"] for row in catches]


def test_mixed_chain_materials_survive_failed_sqlite_commit_and_one_local_recovery(fishing_db):
    h = fishing_db
    result = run_chain([reward_only(), fish_round(), reward_only()], failure=True)
    assert persistence.save_state()
    conn = persistence.get_db_conn()
    conn.execute("""CREATE TEMP TRIGGER fail_chain_projection BEFORE UPDATE ON identity_runtime_state
                    WHEN NEW.fishing_daily_count > OLD.fishing_daily_count
                    BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END""")
    with state_module.use_identity(h.identity_id), pytest.raises(fishing.FishingMiniAppCommitError):
        fishing._apply_fishing_miniapp_result(result, h.now)
    assert h.identity["fishing_daily_count"] == 0
    pending = h.identity["fishing_result_pending"]
    assert pending
    pending_summary = fishing._normalize_fishing_daily_catch_summary(pending["updates"]["fishing_daily_catch_summary_json"])
    assert pending_summary["rewards"] == {"attached-reward": 3, "empty-reward": 4}
    conn.execute("DROP TRIGGER fail_chain_projection")
    conn.commit()
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    state_module.set_storage_bag_records({})
    assert persistence.load_state()
    assert fishing.recover_fishing_result_pending(h.identity_id)["ok"]
    assert fishing.recover_fishing_result_pending(h.identity_id) is None
    state_module._meta_state["identity_states"] = {}
    state_module.set_storage_bag_records({})
    assert persistence.load_state()
    identity = state_module.get_identity_state(h.identity_id)
    summary = fishing._normalize_fishing_daily_catch_summary(identity["fishing_daily_catch_summary_json"])
    assert summary["rods"] == identity["fishing_daily_count"] == 3
    assert summary["rewards"] == {"attached-reward": 3, "empty-reward": 4}
    assert summary["fish"] == {"fixture-fish": 1}
    assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fixture-fish"] == 1
    assert identity["fishing_result_pending"] == {}
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()
    h.external.assert_not_awaited()
