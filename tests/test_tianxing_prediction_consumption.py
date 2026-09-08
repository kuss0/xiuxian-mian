import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import tianxing
from model.real_message_replay import get_real_message_text


IDENTITY_ID = 990510001
ACCOUNT_ID = 7511
NOW = 1780000000.0
ROUTE = "\u63a2\u7d22"
CONSUMED_AT = NOW - 10
SAMPLES = Path(__file__).parent / "fixtures" / "real_message_samples.json"
ACTIONS = ["\u89c2\u547d", "\u5b9a\u547d", "\u63a8\u547d", "\u6539\u547d", "\u5929\u673a\u76d8", "\u6d88\u52ab"]


def consumed_observation(**overrides):
    return dict({
        "last_observed_at": NOW - 1, "last_action": "\u6539\u547d", "last_result": "success",
        "current_prediction": ROUTE, "current_prediction_until": NOW + 3600,
        "current_prediction_set_at": NOW - 600, "prediction_consumed_route": ROUTE,
        "prediction_consumed_at": CONSUMED_AT, "current_change": ROUTE,
        "current_change_until": NOW + 86400, "current_change_set_at": NOW - 1,
        "fixed_star": "\u592a\u9634", "fixed_star_day": tianxing.get_day_key(NOW),
        "tianji_value": 35,
    }, **overrides)


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID)
    state_module.update_send_as_profile(IDENTITY_ID, sect_name="\u5929\u661f\u5b97")
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(
        tianxing_enabled=True,
        tianxing_auto_config={"timeline_enabled": True},
        tianxing_observation=consumed_observation(),
        tianxing_timeline_state=tianxing.normalize_tianxing_timeline_state({}),
    )
    send = AsyncMock()
    monkeypatch.setattr(tianxing, "save_state", Mock())
    monkeypatch.setattr(tianxing, "send_game_command", send)
    yield SimpleNamespace(identity=identity, send=send)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("result", ["success", "panel", "cooldown", "blocked", "noop", "need_observe"])
def test_normalization_cannot_resurrect_consumed_prediction_from_last_action(action, result):
    source = consumed_observation(last_action=action, last_result=result)
    original = copy.deepcopy(source)
    observed = tianxing.normalize_tianxing_observation(source)
    assert source == original
    assert observed["prediction_consumed_route"] == ROUTE
    assert observed["prediction_consumed_at"] == CONSUMED_AT
    assert observed["current_prediction"] == ""
    assert observed["current_prediction_until"] == 0
    assert not tianxing._has_active_unconsumed_prediction(ROUTE, observed, NOW)
    assert tianxing.normalize_tianxing_observation(observed) == observed


@pytest.mark.parametrize("action", ACTIONS)
def test_route_admission_requires_new_prediction_after_consumption(env, action):
    env.identity["tianxing_observation"] = consumed_observation(last_action=action)
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)
    assert not plan["route_allowed"]
    env.send.assert_not_awaited()


@pytest.mark.parametrize("action", ACTIONS)
def test_newer_prediction_timestamp_is_not_cancelled_by_older_consumption(action):
    observed = tianxing.normalize_tianxing_observation(consumed_observation(
        last_action=action, current_prediction_set_at=NOW - 2,
    ))
    assert observed["current_prediction"] == ROUTE
    assert tianxing._has_active_unconsumed_prediction(ROUTE, observed, NOW)


@pytest.mark.parametrize("sample,family", [
    ("observe.basic", "tianxing_observe"), ("set_star.tanlang", "tianxing_set_star"),
    ("change_fate.basic", "tianxing_change_fate"), ("clear_calamity.basic", "tianxing_clear_calamity"),
])
def test_unrelated_real_result_does_not_restore_consumed_prediction(env, sample, family):
    text = get_real_message_text(SAMPLES, f"tianxing.{sample}")
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(text, NOW, family)
    observed = env.identity["tianxing_observation"]
    assert observed["current_prediction"] == ""
    assert observed["prediction_consumed_route"] == ROUTE
    assert observed["prediction_consumed_at"] == CONSUMED_AT
    env.send.assert_not_awaited()


def test_partial_panel_cannot_borrow_consumed_prediction(env):
    text = "\u3010\u5929\u673a\u76d8\u3011\n\u5929\u673a\u503c\uff1a35"
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(text, NOW, "tianxing_panel")
        plan = tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)
    assert not plan["route_allowed"]
    assert env.identity["tianxing_observation"]["prediction_consumed_at"] == CONSUMED_AT


def test_new_predict_result_can_replace_consumed_prediction(env):
    text = get_real_message_text(SAMPLES, "tianxing.predict.basic")
    parsed = tianxing.parse_tianxing_text(text, NOW, "tianxing_predict")
    route = parsed["current_prediction"]
    env.identity["tianxing_observation"] = consumed_observation(current_prediction=route, prediction_consumed_route=route)
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(text, NOW, "tianxing_predict")
    observed = env.identity["tianxing_observation"]
    assert observed["current_prediction"] == route
    assert observed["current_prediction_set_at"] == NOW
    assert not observed["prediction_consumed_route"]
    assert tianxing._has_active_unconsumed_prediction(route, observed, NOW)


def test_consumption_survives_sqlite_reload_and_read_only_normalization(env, monkeypatch, tmp_path):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "consumption.db"))
    assert persistence.save_state()
    assert persistence.load_state()
    restored = state_module.get_identity_state(IDENTITY_ID)
    monkeypatch.setattr(tianxing.time, "time", lambda: NOW)
    with state_module.use_identity(IDENTITY_ID):
        status = tianxing.get_tianxing_status_text()
        plan = tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)
    assert "- \u63a8\u547d\uff1a\u65e0" in status
    assert not plan["route_allowed"]
    assert restored["tianxing_observation"]["prediction_consumed_at"] == CONSUMED_AT
    env.send.assert_not_awaited()
