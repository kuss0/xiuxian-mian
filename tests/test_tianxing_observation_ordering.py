import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import action_guard
from model import state as state_module
from model.features import tianxing
from model.real_message_replay import get_real_message_text


IDENTITY_ID = 990550001
ACCOUNT_ID = 7551
CHAT_ID = -100550001
NOW = 1780000000.0
ROUTE = "\u63a2\u7d22"
OTHER_ROUTE = "\u70bc\u5236"
SAMPLES = Path(__file__).parent / "fixtures" / "real_message_samples.json"
EFFECTS = {"prediction": "predict", "change": "change_fate"}


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
    identity.update(tianxing_enabled=True, tianxing_observation={})
    send = AsyncMock(return_value=None)
    monkeypatch.setattr(tianxing, "send_game_command", send)
    monkeypatch.setattr(tianxing, "save_state", Mock())
    monkeypatch.setattr(action_guard, "mark_dirty", Mock())
    yield SimpleNamespace(identity=identity, send=send)
    send.assert_not_awaited()
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)
    action_guard._recent_closed_command_guards.clear()
    action_guard._recent_closed_command_guards.update(closed)


def effect_text(effect, route=ROUTE):
    family = f"tianxing_{EFFECTS[effect]}"
    text = get_real_message_text(SAMPLES, f"tianxing.{EFFECTS[effect]}.basic")
    parsed = tianxing.parse_tianxing_text(text, NOW, family)
    return text.replace(parsed[f"current_{effect}"], route)


def panel_text(effect, route="", seconds=3600):
    label = "\u63a8\u547d" if effect == "prediction" else "\u6539\u547d"
    value = f"{route}\uff08\u5269\u4f59 {seconds}\u79d2\uff09" if route else "\u65e0"
    return f"\u3010\u5929\u673a\u76d8\u3011\n\u5f53\u524d{label}: {value}"


def apply(text, at=NOW, *, family="", msg_id=5511, chat_id=CHAT_ID, event_type="message"):
    with state_module.use_identity(IDENTITY_ID):
        return tianxing.apply_tianxing_passive(text, at, family, reply_context={
            "send_as_id": IDENTITY_ID, "msg_id": msg_id, "chat_id": chat_id,
            "root_msg_id": msg_id - 1, "processed_at": NOW + 600,
            "event_type": event_type,
        })


def effect_snapshot(env, effect):
    observed = env.identity["tianxing_observation"]
    keys = [f"current_{effect}", f"current_{effect}_until", f"current_{effect}_set_at"]
    if effect == "prediction":
        keys += ["prediction_consumed_route", "prediction_consumed_at"]
    return {key: observed.get(key) for key in keys}


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("kind", ["success", "cooldown", "panel"])
def test_older_effect_cannot_replace_newer_route(env, effect, kind):
    assert apply(effect_text(effect), family=f"tianxing_{EFFECTS[effect]}")
    expected = effect_snapshot(env, effect)
    text = effect_text(effect, OTHER_ROUTE)
    family = f"tianxing_{EFFECTS[effect]}"
    if kind == "panel":
        text, family = panel_text(effect, OTHER_ROUTE), "tianxing_panel"
    elif kind == "cooldown":
        label = "\u63a8\u547d\u5c1a\u672a\u5e94\u9a8c" if effect == "prediction" else "\u6539\u547d\u5c1a\u672a\u8017\u5c3d"
        text = f"\u4f60\u5df2\u6709\u4e00\u9053\u5173\u4e8e \u3010{OTHER_ROUTE}\u3011 \u7684{label}\uff0c\u8fd8\u9700\u7b49\u5f85 1\u5c0f\u65f6\u3002"
    apply(text, NOW - 30, family=family, msg_id=5501)
    assert effect_snapshot(env, effect) == expected
    assert env.identity["tianxing_observation"]["last_observed_at"] >= NOW


@pytest.mark.parametrize("effect", EFFECTS)
def test_explicit_absence_retains_a_watermark_against_old_success(env, effect):
    assert apply(effect_text(effect), NOW - 60, family=f"tianxing_{EFFECTS[effect]}", msg_id=5501)
    assert apply(panel_text(effect), family="tianxing_panel")
    expected = effect_snapshot(env, effect)
    assert expected[f"current_{effect}"] == ""
    apply(effect_text(effect), NOW - 30, family=f"tianxing_{EFFECTS[effect]}", msg_id=5505)
    assert effect_snapshot(env, effect) == expected


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("source", ["empty", "counter", "other_effect"])
def test_partial_panel_cannot_refresh_a_cached_effect_clock(env, effect, source):
    assert apply(effect_text(effect), NOW - 60, family=f"tianxing_{EFFECTS[effect]}", msg_id=5501)
    expected = effect_snapshot(env, effect)
    text = "\u3010\u5929\u673a\u76d8\u3011"
    if source == "counter":
        text += "\n\u5929\u673a\u503c: 35"
    elif source == "other_effect":
        text = panel_text("change" if effect == "prediction" else "prediction", OTHER_ROUTE)
    apply(text, family="tianxing_panel")
    assert effect_snapshot(env, effect) == expected


@pytest.mark.parametrize("result", ["prediction_hit", "prediction_miss", "change_triggered", "modifier"])
@pytest.mark.parametrize("new_route", [ROUTE, OTHER_ROUTE])
def test_old_game_result_does_not_consume_newer_effects_or_release(env, result, new_route):
    for index, effect in enumerate(EFFECTS):
        assert apply(effect_text(effect, new_route), family=f"tianxing_{EFFECTS[effect]}", msg_id=5521 + index * 2)
    expected = {effect: effect_snapshot(env, effect) for effect in EFFECTS}
    env.identity["tianxing_timeline_state"] = {
        "phase": "downstream_released",
        "released_routes": {new_route: {"released_at": NOW, "basis": "change_fate"}},
    }
    markers = {
        "prediction_hit": "\u3010\u63a8\u547d\u547d\u4e2d\u3011\u53f8\u547d\u6f14\u7b97\u543b\u5408\uff0c\u5929\u673a\u503c +1\uff0c\u5b97\u95e8\u8d21\u732e +30",
        "prediction_miss": "\u3010\u63a8\u547d\u843d\u7a7a\u3011\u53f8\u547d\u6f14\u7b97\u843d\u7a7a\uff0c\u9006\u547d\u52ab +1",
        "change_triggered": "\u3010\u6539\u547d\u56de\u5929\u3011",
        "modifier": "\u3010\u5929\u661f\u504f\u8f6c\u3011",
    }
    text = "\u3010\u91ce\u5916\u5386\u7ec3\u3011\n" + markers[result]
    apply(text, NOW - 30, family="wild_training", msg_id=5501)
    assert {effect: effect_snapshot(env, effect) for effect in EFFECTS} == expected
    assert env.identity["tianxing_timeline_state"]["released_routes"][new_route]["released_at"] == NOW


@pytest.mark.parametrize("kind", ["success", "cooldown", "panel"])
def test_consumed_prediction_cannot_be_restored_by_an_older_observation(env, kind):
    assert apply(effect_text("prediction"), NOW - 60, family="tianxing_predict", msg_id=5501)
    text = "\u3010\u91ce\u5916\u5386\u7ec3\u3011\n\u3010\u63a8\u547d\u547d\u4e2d\u3011\u5929\u673a\u503c +1"
    assert apply(text, family="wild_training")
    expected = effect_snapshot(env, "prediction")
    assert expected["prediction_consumed_at"] == NOW
    older, family = effect_text("prediction"), "tianxing_predict"
    if kind == "panel":
        older, family = panel_text("prediction", ROUTE), "tianxing_panel"
    elif kind == "cooldown":
        older = f"\u4f60\u5df2\u6709\u4e00\u9053\u5173\u4e8e \u3010{ROUTE}\u3011 \u7684\u63a8\u547d\u5c1a\u672a\u5e94\u9a8c\uff0c\u8fd8\u9700\u7b49\u5f85 1\u5c0f\u65f6\u3002"
    apply(older, NOW - 30, family=family, msg_id=5505)
    assert effect_snapshot(env, "prediction") == expected
    assert apply(effect_text("prediction"), NOW + 30, family="tianxing_predict", msg_id=5521)
    assert env.identity["tianxing_observation"]["current_prediction"] == ROUTE
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing._has_active_unconsumed_prediction(ROUTE, env.identity["tianxing_observation"], NOW + 30)


@pytest.mark.parametrize("effect", EFFECTS)
def test_same_second_message_order_is_scoped_to_the_chat(env, effect):
    assert apply(effect_text(effect), family=f"tianxing_{EFFECTS[effect]}", msg_id=5511)
    assert apply(effect_text(effect, OTHER_ROUTE), family=f"tianxing_{EFFECTS[effect]}", msg_id=5513)
    expected = effect_snapshot(env, effect)
    apply(effect_text(effect), family=f"tianxing_{EFFECTS[effect]}", msg_id=5511)
    assert effect_snapshot(env, effect) == expected
    apply(effect_text(effect), family=f"tianxing_{EFFECTS[effect]}", msg_id=99999, chat_id=CHAT_ID - 1)
    assert effect_snapshot(env, effect) == expected


@pytest.mark.parametrize("effect", EFFECTS)
def test_absence_watermark_survives_sqlite_reload(env, monkeypatch, tmp_path, effect):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "effect-order.db"))
    assert apply(effect_text(effect), NOW - 60, family=f"tianxing_{EFFECTS[effect]}", msg_id=5501)
    assert apply(panel_text(effect), family="tianxing_panel")
    expected = effect_snapshot(env, effect)
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(IDENTITY_ID)
    apply(effect_text(effect), NOW - 30, family=f"tianxing_{EFFECTS[effect]}", msg_id=5505)
    assert effect_snapshot(env, effect) == expected


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("path", ["routed", "passive", "verified", "message_box", "log_routed", "log_module"])
@pytest.mark.parametrize("event_kind", ["message", "edit"])
def test_native_effect_uses_server_event_time_not_delivery_time(env, monkeypatch, effect, path, event_kind):
    from model import app, app_runtime, runtime
    from model.features import passive_inbox
    from model.verified_event import from_telegram_event

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
    monkeypatch.setattr(tianxing.time, "time", lambda: NOW)
    family = f"tianxing_{EFFECTS[effect]}"
    prefix = tianxing.CMD_TIANXING_PREDICT if effect == "prediction" else tianxing.CMD_TIANXING_CHANGE_FATE
    command = f"{prefix} {ROUTE}"
    bot_id = 880550001
    state_module.set_game_bot_ids([bot_id])
    runtime._finalize_game_command_sent(
        command, msg_id=5510, sent_at=NOW - 90, send_started_at=NOW - 91,
        send_as_id=IDENTITY_ID, game_group_id=CHAT_ID, topic_id=0, max_retry=0, append_sent_log=False,
    )
    text = effect_text(effect)
    event_at = NOW - 30 if event_kind == "edit" else NOW - 60
    event = SimpleNamespace(
        id=5511, chat_id=CHAT_ID, sender_id=bot_id, raw_text=text,
        date=datetime.fromtimestamp(NOW - 60, timezone.utc),
        edit_date=datetime.fromtimestamp(NOW - 30, timezone.utc) if event_kind == "edit" else None,
    )
    ctx = dict(send_as_id=IDENTITY_ID, root_msg_id=5510, reply_to_msg_id=5510, msg_id=5511, chat_id=CHAT_ID, family=family)
    reply = SimpleNamespace(id=5510, raw_text=command, chat_id=CHAT_ID)
    if path == "routed":
        assert asyncio.run(app._handle_routed_reply_event(event, text, NOW, reply, ctx, event_kind=event_kind))
    elif path == "passive":
        assert asyncio.run(passive_inbox.handle_passive_module_card(text, NOW, ctx, event, event_kind))
    elif path in {"verified", "message_box"}:
        verified = from_telegram_event(event, text, ctx, event_kind=event_kind)
        if path == "message_box":
            from model.message_box import build_message_fact_from_event

            verified = build_message_fact_from_event(event, text, ctx, event_type=event_kind).to_verified_game_event()
        assert asyncio.run(passive_inbox.handle_passive_module_card(verified, NOW))
    else:
        from model.app_message_log import _build_message_log_payload

        event.reply_to = SimpleNamespace(reply_to_msg_id=5510)
        _, entry = _build_message_log_payload(event, event_type=event_kind)
        entry["ts_epoch"] = NOW
        if path == "log_module":
            with state_module.use_identity(IDENTITY_ID):
                assert tianxing._apply_tianxing_log_reply(entry, family=family, processed_at=NOW)
        else:
            pending = env.identity["pending_tasks"][(CHAT_ID, 5510)]
            assert asyncio.run(app._replay_pending_log_replies(IDENTITY_ID, 5510, pending, [entry], NOW))
    observed = env.identity["tianxing_observation"]
    expected = tianxing.parse_tianxing_text(text, event_at, family)
    assert observed[f"current_{effect}_until"] == expected[f"current_{effect}_until"]
    assert observed[f"current_{effect}_set_at"] == event_at


@pytest.mark.parametrize("effect", EFFECTS)
def test_result_does_not_acknowledge_an_unrelated_pending_mutation(env, effect):
    command = f"{tianxing.CMD_TIANXING_PREDICT if effect == 'prediction' else tianxing.CMD_TIANXING_CHANGE_FATE} {ROUTE}"
    step = {
        "id": "pending", "action": EFFECTS[effect], "arg": ROUTE, "route": ROUTE,
        "command": command, "status": "sent_waiting_ack", "sent_at": NOW - 10,
        "send_started_at": NOW - 11, "send_msg_id": 5590, "send_chat_id": CHAT_ID,
        "send_account_id": ACCOUNT_ID,
    }
    env.identity["tianxing_timeline_state"] = tianxing.normalize_tianxing_timeline_state({
        "active_step": copy.deepcopy(step), "active_step_index": 0, "steps": [copy.deepcopy(step)],
        "phase": "sent_waiting_ack",
    })
    action_guard.note_sent(command, IDENTITY_ID, 5590, sent_at=NOW - 10, chat_id=CHAT_ID)
    text = "\u3010\u91ce\u5916\u5386\u7ec3\u3011\n\u3010\u63a8\u547d\u547d\u4e2d\u3011\u5929\u673a\u503c +1"
    assert apply(text, NOW, family="wild_training", msg_id=5571)
    assert env.identity["tianxing_timeline_state"]["active_step"] == step
    assert f"tianxing_{EFFECTS[effect]}" in action_guard.get_action_guard_sessions(IDENTITY_ID)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("source", ["success", "panel"])
def test_repeated_complete_reply_does_not_extend_effect(env, effect, source):
    text = effect_text(effect) if source == "success" else panel_text(effect, ROUTE)
    family = f"tianxing_{EFFECTS[effect]}" if source == "success" else "tianxing_panel"
    assert apply(text, family=family)
    expected = effect_snapshot(env, effect)
    assert apply(text + "\n", NOW + 30, family=family, event_type="edit")
    assert effect_snapshot(env, effect) == expected


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("delay", [0, 30])
def test_genuine_panel_edit_can_clear_a_previously_complete_effect(env, effect, delay):
    assert apply(panel_text(effect, ROUTE), family="tianxing_panel")
    assert apply(panel_text(effect), NOW + delay, family="tianxing_panel", event_type="edit")
    assert env.identity["tianxing_observation"][f"current_{effect}"] == ""
    assert env.identity["tianxing_observation"][f"current_{effect}_until"] == 0


@pytest.mark.parametrize("marker", ["\u63a8\u547d\u843d\u7a7a", "\u6539\u547d\u56de\u5929", "\u5929\u661f\u504f\u8f6c"])
def test_result_for_another_route_cannot_clear_current_effects(env, marker):
    for index, effect in enumerate(EFFECTS):
        assert apply(effect_text(effect, OTHER_ROUTE), family=f"tianxing_{EFFECTS[effect]}", msg_id=5501 + index)
    expected = {effect: effect_snapshot(env, effect) for effect in EFFECTS}
    assert apply(f"\u3010\u91ce\u5916\u5386\u7ec3\u3011\n\u3010{marker}\u3011", NOW + 30, family="wild_training")
    assert {effect: effect_snapshot(env, effect) for effect in EFFECTS} == expected


def test_newer_same_second_panel_can_establish_a_new_prediction(env):
    assert apply(effect_text("prediction"), NOW - 60, family="tianxing_predict", msg_id=5501)
    assert apply("\u3010\u91ce\u5916\u5386\u7ec3\u3011\n\u3010\u63a8\u547d\u547d\u4e2d\u3011", family="wild_training", msg_id=5511)
    assert apply(panel_text("prediction", ROUTE), family="tianxing_panel", msg_id=5513)
    observed = env.identity["tianxing_observation"]
    assert observed["current_prediction"] == ROUTE
    assert tianxing._has_active_unconsumed_prediction(ROUTE, observed, NOW)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("value", [None, False, True, "1780000000", float("nan"), float("inf"), -1, NOW + 1000])
def test_missing_or_invalid_server_time_cannot_refresh_effect_or_close_guard(env, effect, value):
    assert apply(effect_text(effect), NOW - 60, family=f"tianxing_{EFFECTS[effect]}", msg_id=5501)
    expected = copy.deepcopy(env.identity["tianxing_observation"])
    command = f"{tianxing.CMD_TIANXING_PREDICT if effect == 'prediction' else tianxing.CMD_TIANXING_CHANGE_FATE} {ROUTE}"
    action_guard.note_sent(command, IDENTITY_ID, 5510, sent_at=NOW - 30, chat_id=CHAT_ID)
    context = {
        "send_as_id": IDENTITY_ID, "root_msg_id": 5510, "msg_id": 5511,
        "chat_id": CHAT_ID, "server_event_at": value, "processed_at": NOW,
    }
    family = f"tianxing_{EFFECTS[effect]}"
    with state_module.use_identity(IDENTITY_ID):
        assert not tianxing.apply_tianxing_passive(effect_text(effect), NOW, family, reply_context=context)
        assert tianxing.is_tianxing_waiting_reply(effect_text(effect), NOW, family, reply_context=context)
        assert not tianxing.close_tianxing_reply_guards(effect_text(effect), NOW, family, context)
    assert env.identity["tianxing_observation"] == expected
    assert family in action_guard.get_action_guard_sessions(IDENTITY_ID)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("corruption", ["container", "record", "nan_at", "bool_at", "bad_complete", "bad_event_type", "bad_signature", "bad_id"])
def test_corrupt_effect_evidence_cannot_authorize_routes_and_fresh_owned_panel_repairs_it(env, effect, corruption):
    assert apply(effect_text(effect), family=f"tianxing_{EFFECTS[effect]}")
    observed = env.identity["tianxing_observation"]
    if corruption == "container":
        observed["effect_evidence"] = ["broken"]
    elif corruption == "record":
        observed["effect_evidence"][effect] = ["broken"]
    elif corruption in {"nan_at", "bool_at"}:
        observed["effect_evidence"][effect]["at"] = float("nan") if corruption == "nan_at" else True
    elif corruption == "bad_complete":
        observed["effect_evidence"][effect]["complete"] = "yes"
    elif corruption == "bad_event_type":
        observed["effect_evidence"][effect]["event_type"] = []
    elif corruption == "bad_signature":
        observed["effect_evidence"][effect]["signature"] = ["bad"]
    else:
        observed["effect_evidence"][effect]["msg_id"] = True
    checker = tianxing._has_fresh_prediction_evidence if effect == "prediction" else tianxing._has_fresh_change_evidence
    with state_module.use_identity(IDENTITY_ID):
        assert not checker(ROUTE, observed, {}, NOW)
        assert tianxing._dirty_tianxing_time_fields(observed)
    before = effect_snapshot(env, effect)
    assert apply(panel_text(effect, OTHER_ROUTE), NOW + 30, family="tianxing_panel", msg_id=5521)
    assert effect_snapshot(env, effect) == before
    action_guard.note_sent(tianxing.CMD_TIANXING_PANEL, IDENTITY_ID, 5530, sent_at=NOW + 39, chat_id=CHAT_ID)
    env.identity["pending_tasks"][(CHAT_ID, 5530)] = {
        "cmd": tianxing.CMD_TIANXING_PANEL, "sent_at": NOW + 39, "send_started_at": NOW + 38,
        "chat_id": CHAT_ID,
    }
    assert apply(panel_text(effect, OTHER_ROUTE), NOW + 40, family="tianxing_panel", msg_id=5531)
    observed = env.identity["tianxing_observation"]
    assert observed[f"current_{effect}"] == OTHER_ROUTE
    assert checker(OTHER_ROUTE, observed, {}, NOW + 40)
    assert not tianxing._dirty_tianxing_time_fields(observed)


@pytest.mark.parametrize("effect", EFFECTS)
def test_old_correlated_panel_can_acknowledge_without_replacing_newer_effect(env, effect):
    action_guard.note_sent(tianxing.CMD_TIANXING_PANEL, IDENTITY_ID, 5500, sent_at=NOW - 40, chat_id=CHAT_ID)
    env.identity["pending_tasks"][(CHAT_ID, 5500)] = {
        "cmd": tianxing.CMD_TIANXING_PANEL, "sent_at": NOW - 40, "send_started_at": NOW - 41,
        "chat_id": CHAT_ID,
    }
    assert apply(effect_text(effect), family=f"tianxing_{EFFECTS[effect]}", msg_id=5531)
    expected = effect_snapshot(env, effect)
    assert apply(panel_text(effect, OTHER_ROUTE), NOW - 30, family="tianxing_panel", msg_id=5501)
    assert effect_snapshot(env, effect) == expected
    assert "tianxing_panel" not in action_guard.get_action_guard_sessions(IDENTITY_ID)
