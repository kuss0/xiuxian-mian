import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import action_guard
from model import state as state_module
from model.features import duel, tianxing, wild_training
from model.real_message_replay import get_real_message_text


IDENTITY_ID = 990540001
ACCOUNT_ID = 7541
CHAT_ID = -100540001
ROOT_ID = 5401
NOW = 1780000000.0
ROUTE = "\u63a2\u7d22"
OTHER_ROUTE = "\u70bc\u5236"
STAR = "\u592a\u9634"
SAMPLES = Path(__file__).parent / "fixtures" / "real_message_samples.json"
EFFECTS = {"prediction": "predict", "change": "change_fate"}


def observation(**updates):
    return {
        "last_observed_at": NOW - 1,
        "last_action": "\u5929\u673a\u76d8", "last_result": "panel", "last_route": ROUTE,
        "fixed_star": STAR, "fixed_star_day": tianxing.get_day_key(NOW),
        "current_prediction": ROUTE, "current_prediction_until": NOW + 3600,
        "current_prediction_set_at": NOW - 30,
        "current_change": ROUTE, "current_change_until": NOW + 86400,
        "current_change_set_at": NOW - 20, "tianji_value": 35,
    } | updates


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    closed = copy.deepcopy(action_guard._recent_closed_command_guards)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    action_guard._recent_closed_command_guards.clear()
    state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID)
    state_module.update_send_as_profile(IDENTITY_ID, sect_name="\u5929\u661f\u5b97")
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(
        tianxing_enabled=True,
        tianxing_auto_config={
            "timeline_enabled": True, "timeline_dry_run_enabled": False,
            "auto_predict_enabled": True, "auto_change_fate_enabled": True,
        },
        tianxing_observation=observation(), tianxing_timeline_state={},
        wild_training_strategy="\u6df1\u5165",
    )
    send = AsyncMock(return_value=None)
    monkeypatch.setattr(tianxing, "send_game_command", send)
    monkeypatch.setattr(tianxing, "save_state", Mock())
    monkeypatch.setattr(duel, "save_state", Mock())
    monkeypatch.setattr(action_guard, "mark_dirty", Mock())
    monkeypatch.setattr(tianxing, "_TIANXING_TIMELINE_LOCKS", {})
    yield SimpleNamespace(identity=identity, send=send)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)
    action_guard._recent_closed_command_guards.clear()
    action_guard._recent_closed_command_guards.update(closed)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("seconds", [0, 1, 59, 60, 3600])
def test_effect_deadline_does_not_receive_cooldown_buffer(effect, seconds):
    label = "\u63a8\u547d" if effect == "prediction" else "\u6539\u547d"
    text = f"\u3010\u5929\u673a\u76d8\u3011\n\u5f53\u524d{label}: {ROUTE}\uff08\u5269\u4f59 {seconds}\u79d2\uff09"
    parsed = tianxing.parse_tianxing_text(text, NOW, "tianxing_panel")
    assert parsed[f"current_{effect}_until"] == NOW + seconds


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("with_duration", [True, False])
def test_success_requires_reported_duration_not_an_invented_lifetime(effect, with_duration):
    text = get_real_message_text(SAMPLES, f"tianxing.{EFFECTS[effect]}.basic")
    duration = 8 * 3600 if effect == "prediction" else 24 * 3600
    if not with_duration:
        text = text.splitlines()[0]
    parsed = tianxing.parse_tianxing_text(text, NOW, f"tianxing_{EFFECTS[effect]}")
    assert parsed[f"current_{effect}_until"] == (NOW + duration if with_duration else 0)


@pytest.mark.parametrize("position", ["before", "after"])
def test_change_effect_timer_is_not_replaced_by_unrelated_business_cooldown(position):
    text = get_real_message_text(SAMPLES, "tianxing.modifier.wild")
    cooldown = "\u4e0b\u6b21\u52a8\u4f5c\u8bf7\u5728 6\u5c0f\u65f6\u540e\u518d\u8bd5\u3002"
    text = f"{cooldown}\n{text}" if position == "before" else f"{text}\n{cooldown}"
    parsed = tianxing.parse_tianxing_text(text, NOW)
    assert parsed["current_change_until"] == NOW + 23 * 3600 + 5 * 60


def test_change_pending_without_timer_is_unknown_not_a_parse_error():
    parsed = tianxing.parse_tianxing_text("\u3010\u6539\u547d\u5f85\u53d1\u3011", NOW)
    assert parsed["current_change_until"] == 0


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("case", ["missing_set", "future_set", "bool_set", "negative_set", "missing_until"])
def test_labels_and_release_records_do_not_prove_effect_freshness(effect, case):
    source = observation(
        last_action="\u63a8\u547d" if effect == "prediction" else "\u6539\u547d",
        last_result="success",
    )
    field = f"current_{effect}_set_at"
    source[field] = {"missing_set": 0, "future_set": NOW + 1, "bool_set": True, "negative_set": -1}.get(case, NOW - 20)
    if case == "missing_until":
        source[f"current_{effect}_until"] = 0
    timeline = {"released_routes": {ROUTE: {"released_at": NOW - 1, "basis": "change_fate"}}}
    fresh = tianxing._has_fresh_prediction_evidence if effect == "prediction" else tianxing._has_fresh_change_evidence
    assert not fresh(ROUTE, source, timeline, NOW)


def test_prediction_deadline_is_not_reconstructed_from_set_time():
    source = observation(current_prediction_until=0)
    assert tianxing._prediction_effective_until(ROUTE, source, NOW) == 0
    assert not tianxing._has_active_unconsumed_prediction(ROUTE, source, NOW)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("offset,expected", [(-0.001, True), (0, False), (0.001, False), (59, False)])
def test_real_effect_stops_authorizing_at_its_exact_deadline(effect, offset, expected):
    source = observation(**{f"current_{effect}_until": NOW})
    fresh = tianxing._has_fresh_prediction_evidence if effect == "prediction" else tianxing._has_fresh_change_evidence
    assert fresh(ROUTE, source, {}, NOW + offset) is expected


def test_unrelated_observation_cannot_refresh_prediction_after_craft_result():
    source = observation(current_prediction=OTHER_ROUTE, current_prediction_set_at=NOW - 40)
    timeline = {"craft_farm": {"audit": [{"event": "craft_result", "ts": NOW - 30}]}}
    assert not tianxing._has_fresh_prediction_evidence(OTHER_ROUTE, source, timeline, NOW)


@pytest.mark.parametrize("effect", EFFECTS)
def test_downstream_preflight_and_http_action_reject_unproven_effect(env, effect):
    env.identity["tianxing_observation"][f"current_{effect}_set_at"] = 0
    env.identity["tianxing_timeline_state"] = {
        "released_routes": {ROUTE: {"released_at": NOW - 1, "basis": "change_fate"}},
    }
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)
        assert not plan["route_allowed"]
        assert not tianxing.is_tianxing_route_released(ROUTE, now=NOW, require_change_fate=True)
        assert not wild_training.wild_training_http_route_is_ready("\u6df1\u5165", NOW)
        helper = wild_training._has_active_tianxing_explore_prediction if effect == "prediction" else wild_training._has_active_tianxing_explore_change
        assert not helper(NOW)
    env.send.assert_not_awaited()


def test_unknown_other_route_prediction_blocks_without_inventing_expiry(env):
    env.identity["tianxing_observation"].update(current_prediction=OTHER_ROUTE, current_prediction_until=0)
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)
        assert not plan["route_allowed"]
        assert plan["stage"] == "prediction_conflict_unknown"
        assert not tianxing._prediction_conflict_stale_reason(env.identity["tianxing_observation"], NOW)


@pytest.mark.parametrize("deep_until", [0, NOW - 90, NOW + 86400])
def test_real_prediction_and_change_remain_usable_during_deep_retreat(env, deep_until):
    env.identity["next_deep_retreat_time"] = deep_until
    env.identity["deep_retreat_phase"] = "waiting" if deep_until else "idle"
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)
        assert plan["route_allowed"]
        assert wild_training.wild_training_http_route_is_ready("\u6df1\u5165", NOW)
    env.send.assert_not_awaited()


def seed_calibration(env, action="predict"):
    command = tianxing.CMD_TIANXING_PREDICT if action == "predict" else tianxing.CMD_TIANXING_CHANGE_FATE
    original = {
        "id": "original", "action": action, "arg": ROUTE, "route": ROUTE,
        "command": f"{command} {ROUTE}", "status": "ack_timeout",
        "send_account_id": ACCOUNT_ID, "send_msg_id": ROOT_ID, "send_chat_id": CHAT_ID,
        "send_started_at": NOW - 181, "sent_at": NOW - 180, "queued_at": NOW - 182,
        "send_op_id": "original-op", "ack_due_at": NOW - 90,
    }
    panel = {
        "id": "calibration", "action": "panel", "arg": "", "route": "",
        "command": tianxing.CMD_TIANXING_PANEL, "status": "sent_waiting_ack",
        "terminal_after_confirm": True, "send_account_id": ACCOUNT_ID,
        "send_msg_id": ROOT_ID + 10, "send_chat_id": CHAT_ID,
        "send_started_at": NOW - 3, "sent_at": NOW - 2, "queued_at": NOW - 4,
        "send_op_id": "panel-op", "ack_due_at": NOW + 60,
    }
    release = {
        "id": "release", "action": "release_downstream", "arg": ROUTE,
        "route": ROUTE, "release_basis": "change_fate", "status": "pending",
    }
    env.identity["tianxing_timeline_state"] = tianxing.normalize_tianxing_timeline_state({
        "plan_id": "effect-calibration", "phase": "sent_waiting_ack", "route": ROUTE,
        "active_step_index": 1, "active_step": copy.deepcopy(panel),
        "steps": [original, panel, release],
    })
    action_guard.note_sent(tianxing.CMD_TIANXING_PANEL, IDENTITY_ID, ROOT_ID + 10, sent_at=NOW - 2, chat_id=CHAT_ID)
    env.identity["pending_tasks"][(CHAT_ID, ROOT_ID + 10)] = {
        "cmd": tianxing.CMD_TIANXING_PANEL, "chat_id": CHAT_ID,
        "send_started_at": NOW - 3, "sent_at": NOW - 2,
    }
    return dict(send_as_id=IDENTITY_ID, root_msg_id=ROOT_ID + 10, msg_id=ROOT_ID + 11, chat_id=CHAT_ID)


@pytest.mark.parametrize("case", ["cached_effects", "cached_modifier", "cached_miss"])
def test_scheduler_does_not_skip_unresolved_calibration_using_cached_fields(env, case):
    seed_calibration(env)
    if case != "cached_effects":
        env.identity["tianxing_observation"].update(
            last_result="modifier" if case == "cached_modifier" else "prediction_miss",
            current_prediction="", current_prediction_until=0,
        )
    before = copy.deepcopy(env.identity["tianxing_timeline_state"])
    with state_module.use_identity(IDENTITY_ID):
        asyncio.run(tianxing.run_tianxing_timeline_scheduler(NOW))
    assert env.identity["tianxing_timeline_state"] == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("action", EFFECTS.values())
def test_owned_terminal_panel_confirms_original_effect_and_continues_without_resend(env, action):
    ctx = seed_calibration(env, action)
    text = (
        "\u3010\u5929\u673a\u76d8\u3011\n"
        f"\u5f53\u524d\u63a8\u547d: {ROUTE}\uff08\u5269\u4f59 7\u5c0f\u65f6\uff09\n"
        f"\u5f53\u524d\u6539\u547d: {ROUTE}\uff08\u5269\u4f59 23\u5c0f\u65f6\uff09"
    )
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(text, NOW, "tianxing_panel", reply_context=ctx)
        timeline = env.identity["tianxing_timeline_state"]
        assert timeline["steps"][0]["status"] == "confirmed"
        assert timeline["active_step"]["status"] == "confirmed"
        assert tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)["route_allowed"]
        asyncio.run(tianxing.run_tianxing_timeline_scheduler(NOW + 1))
        asyncio.run(tianxing.run_tianxing_timeline_scheduler(NOW + 2))
    assert env.identity["tianxing_timeline_state"]["released_routes"][ROUTE]
    env.send.assert_not_awaited()


@pytest.mark.parametrize("action", EFFECTS.values())
@pytest.mark.parametrize("case", ["missing_root", "missing_account", "missing_dispatch", "bool_dispatch", "reversed_receipt", "old_query", "partial"])
def test_terminal_panel_cannot_close_unknown_original_operation(env, action, case):
    ctx = seed_calibration(env, action)
    original = env.identity["tianxing_timeline_state"]["steps"][0]
    if case == "missing_root":
        original["send_msg_id"] = 0
    elif case == "missing_account":
        original["send_account_id"] = 0
    elif case == "old_query":
        original["sent_at"] = NOW - 1
    elif case == "missing_dispatch":
        original["send_started_at"] = 0
    elif case == "bool_dispatch":
        original["send_started_at"] = True
    elif case == "reversed_receipt":
        original["send_started_at"] = NOW - 179
    text = "\u3010\u5929\u673a\u76d8\u3011\n\u5929\u673a\u503c: 35"
    if case != "partial":
        text += f"\n\u5f53\u524d\u63a8\u547d: {ROUTE}\uff08\u5269\u4f59 7\u5c0f\u65f6\uff09\n\u5f53\u524d\u6539\u547d: {ROUTE}\uff08\u5269\u4f59 23\u5c0f\u65f6\uff09"
    before = copy.deepcopy(env.identity["tianxing_timeline_state"])
    with state_module.use_identity(IDENTITY_ID):
        tianxing.apply_tianxing_passive(text, NOW, "tianxing_panel", reply_context=ctx)
    assert env.identity["tianxing_timeline_state"] == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("active", [True, False])
def test_unresolved_mutation_blocks_all_route_release_paths(env, active):
    seed_calibration(env)
    timeline = env.identity["tianxing_timeline_state"]
    timeline["released_routes"] = {ROUTE: {"released_at": NOW - 1, "basis": "change_fate"}}
    if not active:
        timeline.update(active_step={}, active_step_index=-1, phase="blocked_replan")
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)
        assert not plan["route_allowed"]
        assert not tianxing.is_tianxing_route_released(ROUTE, now=NOW, require_change_fate=True)
        assert not wild_training.wild_training_http_route_is_ready("\u6df1\u5165", NOW)
        assert not tianxing._timeline_route_release_ready(ROUTE, "change_fate", env.identity["tianxing_observation"], timeline, NOW)
        asyncio.run(tianxing.run_tianxing_timeline_scheduler(NOW))
    assert env.identity["tianxing_timeline_state"]["steps"][0]["status"] == "ack_timeout"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("status", ["pending", "sent_waiting_ack", "ack_timeout"])
def test_original_reply_resolves_calibration_without_another_query(env, status):
    seed_calibration(env)
    timeline = env.identity["tianxing_timeline_state"]
    timeline["active_step"]["status"] = status
    timeline["steps"][1]["status"] = status
    text = get_real_message_text(SAMPLES, "tianxing.predict.basic").replace(OTHER_ROUTE, ROUTE)
    ctx = dict(send_as_id=IDENTITY_ID, root_msg_id=ROOT_ID, msg_id=ROOT_ID + 1, chat_id=CHAT_ID)
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(text, NOW, "tianxing_predict", reply_context=ctx)
        assert env.identity["tianxing_timeline_state"]["steps"][0]["status"] == "confirmed"
        asyncio.run(tianxing.run_tianxing_timeline_scheduler(NOW + 1))
        asyncio.run(tianxing.run_tianxing_timeline_scheduler(NOW + 2))
    assert env.identity["tianxing_timeline_state"]["released_routes"][ROUTE]
    env.send.assert_not_awaited()


def test_released_route_in_future_is_not_a_current_authorization(env):
    env.identity["tianxing_timeline_state"] = {"released_routes": {ROUTE: {"released_at": NOW + 1, "basis": "change_fate"}}}
    with state_module.use_identity(IDENTITY_ID):
        assert not tianxing.is_tianxing_route_released(ROUTE, now=NOW, require_change_fate=True)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "invalid"])
def test_invalid_effect_clock_never_authorizes_a_duel(env, effect, value):
    env.identity["tianxing_observation"][f"current_{effect}_set_at"] = value
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_route_preflight_plan("\u6597\u6cd5", now=NOW)
        assert not plan["route_allowed"]
        assert not asyncio.run(duel._prepare_duel_tianxing_route(NOW))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("action", EFFECTS.values())
@pytest.mark.parametrize("result", ["success", "cooldown", "conflict"])
def test_late_original_reply_can_resolve_retained_operation_after_calibration_timeout(env, monkeypatch, tmp_path, action, result):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "retained-operation.db"))
    seed_calibration(env, action)
    timeline = env.identity["tianxing_timeline_state"]
    timeline.update(active_step={}, active_step_index=-1, phase="blocked_replan")
    timeline["steps"][1]["status"] = "calibration_timeout"
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(IDENTITY_ID)
    assert env.identity["tianxing_timeline_state"] == timeline
    text = get_real_message_text(SAMPLES, f"tianxing.{action}.basic").replace(OTHER_ROUTE, ROUTE)
    if result != "success":
        route = OTHER_ROUTE if result == "conflict" else ROUTE
        effect = "\u63a8\u547d\u5c1a\u672a\u5e94\u9a8c" if action == "predict" else "\u6539\u547d\u5c1a\u672a\u8017\u5c3d"
        text = f"\u4f60\u5df2\u6709\u4e00\u9053\u5173\u4e8e \u3010{route}\u3011 \u7684{effect}\uff0c\u8fd8\u9700\u7b49\u5f85 1\u5c0f\u65f6\u3002"
    ctx = dict(send_as_id=IDENTITY_ID, root_msg_id=ROOT_ID, msg_id=ROOT_ID + 1, chat_id=CHAT_ID)
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.has_tianxing_pending_reply(f"tianxing_{action}")
        assert not tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)["route_allowed"]
        asyncio.run(tianxing.run_tianxing_timeline_scheduler(NOW))
        assert tianxing.apply_tianxing_passive(text, NOW + 1, f"tianxing_{action}", reply_context=ctx)
        status = env.identity["tianxing_timeline_state"]["steps"][0]["status"]
        assert status.startswith("rejected_") if result == "conflict" else status == "confirmed"
        assert not tianxing.has_tianxing_pending_reply(f"tianxing_{action}")
        assert tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW + 1, require_change_fate=True)["route_allowed"] is (result != "conflict")
    resolved = copy.deepcopy(env.identity["tianxing_timeline_state"])
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(IDENTITY_ID)
    assert env.identity["tianxing_timeline_state"] == resolved
    env.send.assert_not_awaited()


@pytest.mark.parametrize("effect_time", [0, NOW + 1])
def test_duel_target_priority_requires_confirmed_prediction_time(env, effect_time):
    env.identity.update(duel_enabled=True, duel_target="@target", duel_total_count=5, duel_unequip_prepared=True, next_duel_time=NOW)
    env.identity["tianxing_observation"].update(current_prediction="\u6597\u6cd5", current_prediction_set_at=effect_time)
    with state_module.use_identity(IDENTITY_ID):
        assert duel._prepared_tianxing_duel_priority_owner("@target", NOW) == 0


@pytest.mark.parametrize("action", EFFECTS.values())
@pytest.mark.parametrize("case", ["matching", "other_route", "missing_timer"])
def test_original_cooldown_reply_is_reconciled_during_calibration(env, action, case):
    ctx = seed_calibration(env, action)
    ctx.update(root_msg_id=ROOT_ID, msg_id=ROOT_ID + 1)
    route = OTHER_ROUTE if case == "other_route" else ROUTE
    effect = "\u63a8\u547d\u5c1a\u672a\u5e94\u9a8c" if action == "predict" else "\u6539\u547d\u5c1a\u672a\u8017\u5c3d"
    text = f"\u4f60\u5df2\u6709\u4e00\u9053\u5173\u4e8e \u3010{route}\u3011 \u7684{effect}"
    if case != "missing_timer":
        text += "\uff0c\u8fd8\u9700\u7b49\u5f85 1\u5c0f\u65f6\u3002"
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(text, NOW, f"tianxing_{action}", reply_context=ctx) is (case != "missing_timer")
        timeline = env.identity["tianxing_timeline_state"]
        if case == "matching":
            assert timeline["steps"][0]["status"] == "confirmed"
            assert tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)["route_allowed"]
        elif case == "other_route":
            assert timeline["steps"][0]["status"].startswith("rejected_")
            assert not timeline["active_step"]
            assert not tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW, require_change_fate=True)["route_allowed"]
            panel_ctx = dict(ctx, root_msg_id=ROOT_ID + 10, msg_id=ROOT_ID + 11)
            assert tianxing.apply_tianxing_passive(
                "\u3010\u5929\u673a\u76d8\u3011\n\u5929\u673a\u503c: 35", NOW + 1, "tianxing_panel", reply_context=panel_ctx,
            )
            assert not tianxing.has_tianxing_pending_reply("tianxing_panel")
        else:
            assert timeline["steps"][0]["status"] == "ack_timeout"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("active_panel", [True, False])
@pytest.mark.parametrize("path", ["routed", "log"])
@pytest.mark.parametrize("source", ["panel", "original"])
@pytest.mark.parametrize("action", EFFECTS.values())
@pytest.mark.parametrize("restart", [False, True])
def test_native_reply_path_retains_partial_calibration_until_final_edit(env, monkeypatch, tmp_path, active_panel, path, source, action, restart):
    from model import app, app_runtime, persistence, runtime
    from model.features import passive_inbox

    for module, name in (
        (runtime, "_notify_game_command_sent_observers"), (runtime, "note_game_command_sent"),
        (app, "_remember_early_routed_reply"), (passive_inbox, "_record_passive_event"),
        (passive_inbox, "save_state"),
    ):
        monkeypatch.setattr(module, name, Mock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    bot_id = 880540001
    state_module.set_game_bot_ids([bot_id])
    ctx = seed_calibration(env, action)
    family = "tianxing_panel" if source == "panel" else f"tianxing_{action}"
    command = tianxing.CMD_TIANXING_PANEL if source == "panel" else env.identity["tianxing_timeline_state"]["steps"][0]["command"]
    root_id = ROOT_ID + 10 if source == "panel" else ROOT_ID
    result_id = root_id + 1
    ctx.update(family=family, reply_to_msg_id=root_id, root_msg_id=root_id, msg_id=result_id)
    sent_step = env.identity["tianxing_timeline_state"]["steps"][1 if source == "panel" else 0]
    if not active_panel:
        timeline = env.identity["tianxing_timeline_state"]
        original = copy.deepcopy(timeline["steps"][0])
        timeline.update(active_step=original, active_step_index=0, steps=[copy.deepcopy(original)], phase="ack_timeout")
    runtime._finalize_game_command_sent(
        command, msg_id=root_id, sent_at=sent_step["sent_at"], send_started_at=sent_step["send_started_at"],
        send_as_id=IDENTITY_ID, game_group_id=CHAT_ID, topic_id=0, max_retry=0, append_sent_log=False,
    )
    pending = env.identity["pending_tasks"][(CHAT_ID, root_id)]

    async def deliver(text, event_kind, now):
        if path == "log":
            return await app._replay_pending_log_replies(IDENTITY_ID, root_id, pending, [{
                "chat_id": CHAT_ID, "message_id": result_id, "sender_id": bot_id,
                "reply_to_msg_id": root_id, "text": text, "ts_epoch": now, "event_type": event_kind,
                "server_event_at": now,
            }], now)
        event = SimpleNamespace(id=result_id, chat_id=CHAT_ID, sender_id=bot_id, raw_text=text, server_event_at=now)
        reply = SimpleNamespace(id=root_id, chat_id=CHAT_ID, raw_text=command)
        return await app._handle_routed_reply_event(event, text, now, reply, ctx, event_kind=event_kind)

    partial = "\u3010\u5929\u673a\u76d8\u3011\n\u5929\u673a\u503c: 35"
    label = "\u63a8\u547d" if action == "predict" else "\u6539\u547d"
    full = f"{partial}\n\u5f53\u524d{label}: {ROUTE}\uff08\u5269\u4f59 7\u5c0f\u65f6\uff09"
    if source == "original":
        full = get_real_message_text(SAMPLES, f"tianxing.{action}.basic").replace(OTHER_ROUTE, ROUTE)
        partial = full.splitlines()[0]
    asyncio.run(deliver(partial, "message", NOW))
    assert (CHAT_ID, root_id) in env.identity["pending_tasks"]
    assert family in action_guard.get_action_guard_sessions(IDENTITY_ID)
    if restart:
        monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "partial-calibration.db"))
        assert persistence.save_state()
        assert persistence.load_state()
        env.identity = state_module.get_identity_state(IDENTITY_ID)
        pending = env.identity["pending_tasks"][(CHAT_ID, root_id)]
        app_runtime._runtime_event_claims.clear()
        app_runtime._runtime_message_consumed.clear()
        runtime._reply_chain_tracker.clear()
        assert family in action_guard.get_action_guard_sessions(IDENTITY_ID)
    asyncio.run(deliver(full, "edit", NOW + 1))
    assert env.identity["tianxing_timeline_state"]["steps"][0]["status"] == "confirmed"
    assert (CHAT_ID, root_id) not in env.identity["pending_tasks"]
    assert family not in action_guard.get_action_guard_sessions(IDENTITY_ID)
    if restart:
        resolved = copy.deepcopy(env.identity["tianxing_timeline_state"])
        assert persistence.save_state()
        assert persistence.load_state()
        env.identity = state_module.get_identity_state(IDENTITY_ID)
        assert env.identity["tianxing_timeline_state"] == resolved
        assert (CHAT_ID, root_id) not in env.identity["pending_tasks"]
        assert family not in action_guard.get_action_guard_sessions(IDENTITY_ID)
    env.send.assert_not_awaited()
