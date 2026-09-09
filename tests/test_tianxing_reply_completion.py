import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import action_guard, app, app_runtime, runtime
from model import state as state_module
from model.features import passive_inbox, tianxing
from model.real_message_replay import get_real_message_text


IDENTITY_ID = 990500001
ACCOUNT_ID = 7501
CHAT_ID = -100500001
BOT_ID = 880500001
NOW = 1780000000.0
ROOT_ID = 5001
RESULT_ID = 5002
SAMPLES = Path(__file__).parent / "fixtures" / "real_message_samples.json"
ITEM = "\u7384\u94c1\u5251"
COMMANDS = {
    "panel": tianxing.CMD_TIANXING_PANEL,
    "observe": tianxing.CMD_TIANXING_OBSERVE,
    "craft": f"{tianxing.CMD_CRAFT} {ITEM}",
    "retreat": tianxing.CMD_NORMAL_RETREAT,
    "use": tianxing.CMD_USE_HEQI_DAN,
    "exchange": tianxing.CMD_EXCHANGE_HEQI_DAN_PREFIX + "10",
    "donate": tianxing.CMD_SECT_DONATE_LINGSHI_PREFIX + "200",
}
EXCHANGE = "\u5151\u6362\u6210\u529f\uff01\n\u4f60\u6d88\u8017\u4e86 1500 \u70b9\u8d21\u732e\uff0c\u83b7\u5f97\u4e86\u3010\u5408\u6c14\u4e39\u3011x10\uff0c\u5df2\u653e\u5165\u4f60\u7684\u50a8\u7269\u888b\u3002"
DONATE = "\u4f60\u5411\u5b97\u95e8\u6350\u732e\u4e86 \u3010\u7075\u77f3\u3011x200\uff0c\u83b7\u5f97\u4e86 1400 \u70b9\u5b97\u95e8\u8d21\u732e\uff01"
UNKNOWN = "\u53f8\u547d\u76d8\u6b63\u5728\u63a8\u6f14\uff0c\u8bf7\u7a0d\u5019\u3002"


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID)
    state_module.update_send_as_profile(IDENTITY_ID, sect_name="\u5929\u661f\u5b97")
    state_module.set_game_bot_ids([BOT_ID])
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(tianxing_enabled=True, tianxing_auto_config={"timeline_enabled": False})
    identity["tianxing_timeline_state"] = tianxing.normalize_tianxing_timeline_state({})
    identity["tianxing_observation"] = tianxing.normalize_tianxing_observation({
        "last_action": "\u63a8\u547d", "last_result": "success", "last_observed_at": NOW - 60,
        "current_prediction": tianxing.TIANXING_ROUTES[1],
        "current_prediction_set_at": NOW - 60, "current_prediction_until": NOW + 3600,
        "tianji_value": 12,
    })
    for module, name in (
        (tianxing, "save_state"), (action_guard, "mark_dirty"),
        (runtime, "_notify_game_command_sent_observers"), (runtime, "note_game_command_sent"),
        (app, "_remember_early_routed_reply"), (passive_inbox, "_record_passive_event"),
        (passive_inbox, "save_state"),
    ):
        monkeypatch.setattr(module, name, Mock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(runtime, "_GAME_SEND_BLOCK_LAST", {})
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    send = AsyncMock()
    monkeypatch.setattr(tianxing, "send_game_command", send)
    yield SimpleNamespace(identity=identity, send=send)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def seed(env, kind):
    command = COMMANDS[kind]
    runtime._finalize_game_command_sent(
        command, msg_id=ROOT_ID, sent_at=NOW - 10, send_started_at=NOW - 11,
        send_as_id=IDENTITY_ID, game_group_id=CHAT_ID, topic_id=0,
        max_retry=0, append_sent_log=False,
    )
    if kind in {"panel", "observe"}:
        env.identity["tianxing_observation"].update(
            auto_pending_action=kind, auto_pending_command=command,
            auto_pending_account_id=ACCOUNT_ID, auto_pending_op_id="fixture",
            auto_pending_msg_id=ROOT_ID, auto_pending_chat_id=CHAT_ID,
            auto_pending_sent_at=NOW - 11, auto_pending_due_at=NOW + 80,
        )
    else:
        farm_key = "craft_farm" if kind == "craft" else "retreat_farm"
        pending_key = "pending_craft" if kind == "craft" else "pending_command"
        env.identity["tianxing_timeline_state"][farm_key].update(
            phase="sent_waiting_reply", started_at=NOW - 20, next_time=NOW + 80,
            daily_day=tianxing.get_day_key(NOW), last_command=command,
            last_msg_id=ROOT_ID, last_chat_id=CHAT_ID, last_account_id=ACCOUNT_ID,
            last_op_id="fixture", last_send_started_at=NOW - 11, last_sent_at=NOW - 10,
            send_outcome="sent", **{pending_key: {
                "op_id": "fixture", "command": command, "msg_id": ROOT_ID,
                "chat_id": CHAT_ID, "account_id": ACCOUNT_ID, "started_at": NOW - 11,
                "sent_at": NOW - 10, "status": "sent",
            }},
        )
    return env.identity["pending_tasks"][(CHAT_ID, ROOT_ID)]


def text_for(kind, final=False):
    if not final:
        return {
            "panel": "\u3010\u5929\u673a\u76d8\u3011",
            "observe": "\u3010\u89c2\u547d\u7ed3\u679c\u3011",
            "craft": f"\u51c6\u5907\u540c\u65f6\u5f00\u70bc 1 \u7089\u3010{ITEM}\u3011...",
            "retreat": UNKNOWN, "use": UNKNOWN,
            "exchange": EXCHANGE.replace("x10", "x0"),
            "donate": DONATE.replace("x200", "x0"),
        }[kind]
    if kind in {"exchange", "donate"}:
        return {"exchange": EXCHANGE, "donate": DONATE}[kind]
    sample = {
        "panel": "panel.basic", "observe": "observe.basic", "craft": "craft_farm.hit",
        "retreat": "retreat.success", "use": "retreat_farm.heqi_dan_success",
    }[kind]
    return get_real_message_text(SAMPLES, f"tianxing.{sample}")


def event_context(kind):
    return {
        "send_as_id": IDENTITY_ID, "chat_id": CHAT_ID, "family": runtime.resolve_reply_family(COMMANDS[kind]),
        "root_msg_id": ROOT_ID, "reply_to_msg_id": ROOT_ID, "msg_id": RESULT_ID,
    }


async def deliver(kind, text, now, *, path="routed", edit=False, pending=None):
    event = SimpleNamespace(id=RESULT_ID, chat_id=CHAT_ID, sender_id=BOT_ID, raw_text=text, server_event_at=now)
    context = event_context(kind)
    event_kind = "edit" if edit else "message"
    if path == "routed":
        reply = SimpleNamespace(id=ROOT_ID, chat_id=CHAT_ID, raw_text=COMMANDS[kind])
        return await app._handle_routed_reply_event(event, text, now, reply, context, event_kind=event_kind)
    if path == "passive":
        return await passive_inbox.handle_passive_module_card(text, now, context, event, event_type=event_kind)
    return await app._replay_pending_log_replies(IDENTITY_ID, ROOT_ID, pending, [{
        "chat_id": CHAT_ID, "message_id": RESULT_ID, "sender_id": BOT_ID,
        "reply_to_msg_id": ROOT_ID, "text": text, "ts_epoch": now, "event_type": event_kind,
        "server_event_at": now,
    }], now)


def business_pending(env, kind):
    if kind in {"panel", "observe"}:
        return env.identity["tianxing_observation"]["auto_pending_action"]
    farm_key = "craft_farm" if kind == "craft" else "retreat_farm"
    pending_key = "pending_craft" if kind == "craft" else "pending_command"
    return env.identity["tianxing_timeline_state"][farm_key][pending_key]


@pytest.mark.parametrize("kind", ["panel", "observe"])
@pytest.mark.parametrize("latest_complete", [False, True])
@pytest.mark.parametrize("path", ["pending", "early_cache", "early_log"])
def test_batch_log_replay_uses_latest_server_revision_before_clearing_pending(env, monkeypatch, kind, latest_complete, path):
    pending = seed(env, kind)
    older = {
        "chat_id": CHAT_ID, "message_id": RESULT_ID, "sender_id": BOT_ID, "reply_to_msg_id": ROOT_ID,
        "event_type": "message", "server_event_at": NOW - 1, "ts_epoch": NOW + 2,
        "text": text_for(kind, not latest_complete),
    }
    latest = dict(older, event_type="edit", server_event_at=NOW, ts_epoch=NOW + 1, text=text_for(kind, latest_complete))
    if path == "pending":
        handled = asyncio.run(app._replay_pending_log_replies(IDENTITY_ID, ROOT_ID, pending, [latest, older], NOW + 3))
    else:
        monkeypatch.setattr(app, "_EARLY_ROUTED_REPLY_REPLAY_DELAY_SEC", 0)
        monkeypatch.setattr(app, "_early_routed_replies", {})
        monkeypatch.setattr(app, "console_log", Mock())
        monkeypatch.setattr(app, "find_message_log_replies_tail", Mock(return_value=[latest, older]))
        if path == "early_cache":
            items = []
            for entry in (latest, older):
                event, reply = app._logged_reply_event(entry, COMMANDS[kind], IDENTITY_ID)
                items.append({
                    "event": event, "reply_to": reply, "event_id": RESULT_ID,
                    "event_kind": entry["event_type"], "event_at": entry["ts_epoch"],
                    "text": entry["text"], "remembered_at": app.time.time(),
                })
            app._early_routed_replies[(CHAT_ID, IDENTITY_ID, ROOT_ID)] = items
        handled = asyncio.run(app._replay_early_replies_after_sent(
            IDENTITY_ID, COMMANDS[kind], NOW - 10, ROOT_ID, game_group_id=CHAT_ID, allow_log_fallback=True,
        ))
    assert handled
    assert bool(business_pending(env, kind)) is not latest_complete
    assert ((CHAT_ID, ROOT_ID) in env.identity["pending_tasks"]) is not latest_complete
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["panel", "observe"])
def test_legacy_log_without_server_time_keeps_pending_for_fresh_evidence(env, kind):
    pending = seed(env, kind)
    entry = {
        "chat_id": CHAT_ID, "message_id": RESULT_ID, "sender_id": BOT_ID, "reply_to_msg_id": ROOT_ID,
        "event_type": "message", "ts_epoch": NOW + 2, "text": text_for(kind, True),
    }
    assert not asyncio.run(app._replay_pending_log_replies(IDENTITY_ID, ROOT_ID, pending, [entry], NOW + 3))
    assert business_pending(env, kind)
    assert (CHAT_ID, ROOT_ID) in env.identity["pending_tasks"]
    env.send.assert_not_awaited()


@pytest.mark.parametrize("path", ["routed", "passive", "log"])
@pytest.mark.parametrize("kind", tuple(COMMANDS))
def test_intermediate_reply_keeps_pending_and_final_edit_settles(env, path, kind):
    pending = seed(env, kind)
    guard = action_guard.resolve_action_key(COMMANDS[kind])
    asyncio.run(deliver(kind, text_for(kind), NOW, path=path, pending=pending))
    assert (CHAT_ID, ROOT_ID) in env.identity["pending_tasks"]
    assert guard in action_guard.get_action_guard_sessions(IDENTITY_ID)
    assert business_pending(env, kind)
    event = SimpleNamespace(id=RESULT_ID, chat_id=CHAT_ID)
    assert not app._has_runtime_message_consumed(event, event_context(kind)["family"])
    assert asyncio.run(deliver(kind, text_for(kind, True), NOW + 5, path=path, edit=True, pending=pending))
    assert not business_pending(env, kind)
    if path != "passive":
        assert (CHAT_ID, ROOT_ID) not in env.identity["pending_tasks"]
    assert guard not in action_guard.get_action_guard_sessions(IDENTITY_ID)
    if kind == "craft":
        assert env.identity["tianxing_timeline_state"]["craft_farm"]["daily_count"] == 1
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["panel", "observe", "craft", "retreat", "use"])
def test_intermediate_reply_is_not_new_authoritative_tianxing_state(env, kind):
    pending = seed(env, kind)
    before = copy.deepcopy(env.identity["tianxing_observation"])
    asyncio.run(deliver(kind, text_for(kind), NOW, pending=pending))
    after = env.identity["tianxing_observation"]
    for key in (
        "last_observed_at", "last_action", "last_result", "current_prediction_set_at",
        "current_prediction_until", "current_prediction", "auto_pending_action", "tianji_value",
    ):
        assert after[key] == before[key], key


def test_empty_panel_does_not_confirm_active_timeline_step(env):
    pending = seed(env, "panel")
    step = {
        "action": "panel", "command": COMMANDS["panel"], "status": "sent_waiting_ack",
        "send_msg_id": ROOT_ID, "send_chat_id": CHAT_ID, "sent_at": NOW - 10,
    }
    env.identity["tianxing_timeline_state"].update(
        phase="sent_waiting_ack", active_step=step, active_step_index=0, steps=[copy.deepcopy(step)],
    )
    asyncio.run(deliver("panel", text_for("panel"), NOW, pending=pending))
    assert env.identity["tianxing_timeline_state"]["active_step"]["status"] == "sent_waiting_ack"


@pytest.mark.parametrize("kind", ["panel", "craft", "exchange"])
def test_intermediate_receipt_survives_sqlite_reload_then_final_edit(env, monkeypatch, tmp_path, kind):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "intermediate.db"))
    pending = seed(env, kind)
    asyncio.run(deliver(kind, text_for(kind), NOW, path="log", pending=pending))
    assert (CHAT_ID, ROOT_ID) in env.identity["pending_tasks"]
    assert pending["reply_recovery_applied"] and not any(pending["reply_recovery_applied"].values())
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(IDENTITY_ID)
    pending = env.identity["pending_tasks"][(CHAT_ID, ROOT_ID)]
    app_runtime._runtime_event_claims.clear()
    app_runtime._runtime_message_consumed.clear()
    asyncio.run(deliver(kind, text_for(kind), NOW + 30, path="log", pending=pending))
    assert business_pending(env, kind)
    asyncio.run(deliver(kind, text_for(kind, True), NOW + 60, path="log", edit=True, pending=pending))
    assert not business_pending(env, kind)
    assert (CHAT_ID, ROOT_ID) not in env.identity["pending_tasks"]
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", tuple(COMMANDS))
@pytest.mark.parametrize("known_root", [False, True])
def test_result_before_transport_receipt_is_reconciled_without_losing_or_repeating_business(env, kind, known_root):
    seed(env, kind)
    env.identity["pending_tasks"].clear()
    env.identity["action_guard_sessions"].clear()
    if not known_root:
        if kind in {"panel", "observe"}:
            env.identity["tianxing_observation"].update(auto_pending_msg_id=0, auto_pending_chat_id=0)
        else:
            pending = business_pending(env, kind)
            pending.update(msg_id=0, chat_id=0, status="sending")
            farm_key = "craft_farm" if kind == "craft" else "retreat_farm"
            env.identity["tianxing_timeline_state"][farm_key].update(last_msg_id=0, last_chat_id=0)
    asyncio.run(deliver(kind, text_for(kind, True), NOW))
    if not known_root and kind not in {"panel", "observe"}:
        assert business_pending(env, kind)
        assert not app._has_runtime_message_consumed(SimpleNamespace(id=RESULT_ID, chat_id=CHAT_ID), event_context(kind)["family"])
    runtime._finalize_game_command_sent(
        COMMANDS[kind], msg_id=ROOT_ID, sent_at=NOW + 10, send_started_at=NOW - 11,
        send_as_id=IDENTITY_ID, game_group_id=CHAT_ID, topic_id=0,
        send_intent={"source_module": "\u5929\u661f\u5b97", "op_id": "fixture"},
        max_retry=0, append_sent_log=False,
    )
    pending = env.identity["pending_tasks"][(CHAT_ID, ROOT_ID)]
    assert asyncio.run(deliver(kind, text_for(kind, True), NOW, path="log", pending=pending))
    assert not business_pending(env, kind)
    assert (CHAT_ID, ROOT_ID) not in env.identity["pending_tasks"]
    assert action_guard.resolve_action_key(COMMANDS[kind]) not in action_guard.get_action_guard_sessions(IDENTITY_ID)
    if kind == "craft":
        assert env.identity["tianxing_timeline_state"]["craft_farm"]["daily_count"] == 1
    env.send.assert_not_awaited()


@pytest.mark.parametrize("path", ["routed", "passive", "log"])
@pytest.mark.parametrize("text", [
    "\u70bc\u5236\u7ed3\u675f",
    "\u70bc\u5236\u7ed3\u675f\n\u5171\u5f00\u7089 0 \u6b21\uff0c\u6210\u529f 0 \u6b21",
    "\u70bc\u5236\u7ed3\u675f\n\u5171\u5f00\u7089 0 \u6b21\uff0c\u6210\u529f 1 \u6b21\n\u3010\u63a8\u547d\u547d\u4e2d\u3011",
    "\u70bc\u5236\u7ed3\u675f\n\u5171\u5f00\u7089 1 \u6b21\uff0c\u6210\u529f 2 \u6b21",
])
def test_incomplete_craft_result_does_not_consume_or_invent_one_craft(env, path, text):
    pending = seed(env, "craft")
    expected_farm = copy.deepcopy(env.identity["tianxing_timeline_state"]["craft_farm"])
    expected_observation = copy.deepcopy(env.identity["tianxing_observation"])
    assert asyncio.run(deliver("craft", text, NOW, path=path, pending=pending))
    assert env.identity["tianxing_timeline_state"]["craft_farm"] == expected_farm
    for key, value in expected_observation.items():
        if key != "recent":
            assert env.identity["tianxing_observation"][key] == value, key
    assert (CHAT_ID, ROOT_ID) in env.identity["pending_tasks"]
    assert action_guard.resolve_action_key(COMMANDS["craft"]) in action_guard.get_action_guard_sessions(IDENTITY_ID)
    assert asyncio.run(deliver("craft", text_for("craft", True), NOW + 5, path=path, edit=True, pending=pending))
    assert not business_pending(env, "craft")
    assert env.identity["tianxing_timeline_state"]["craft_farm"]["daily_count"] == 1
    env.send.assert_not_awaited()


def test_craft_reducer_rejects_zero_count_without_outer_dispatcher(env):
    seed(env, "craft")
    expected = copy.deepcopy(env.identity["tianxing_timeline_state"]["craft_farm"])
    parsed = tianxing.parse_tianxing_text("\u70bc\u5236\u7ed3\u675f", NOW, "tianxing_craft_farm")
    with state_module.use_identity(IDENTITY_ID):
        assert not tianxing._update_craft_farm_from_parsed(
            parsed, env.identity["tianxing_observation"], NOW, "tianxing_craft_farm", reply_context=event_context("craft"),
        )
    assert env.identity["tianxing_timeline_state"]["craft_farm"] == expected


@pytest.mark.parametrize("value", [["broken"], "broken", True, 7])
@pytest.mark.parametrize("field,family", [
    ("observation", "tianxing_panel"), ("action", "tianxing_panel"),
    ("timeline", "tianxing_craft_farm"), ("farm", "tianxing_retreat_farm"),
])
def test_malformed_pending_state_cannot_certify_completion(env, value, field, family):
    if field == "observation":
        env.identity["tianxing_observation"] = value
    elif field == "action":
        env.identity["tianxing_observation"]["auto_pending_action"] = value
    elif field == "timeline":
        env.identity["tianxing_timeline_state"] = value
    else:
        env.identity["tianxing_timeline_state"]["retreat_farm"] = value
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.has_tianxing_pending_reply(family)
        assert not tianxing.has_tianxing_pending_reply("unrelated")


@pytest.mark.parametrize("value", [["broken"], "broken", True, 7])
def test_intermediate_diagnostics_tolerate_malformed_observation(env, value):
    env.identity["tianxing_observation"] = value
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(text_for("panel"), NOW, "tianxing_panel", reply_context=event_context("panel"))
    observed = env.identity["tianxing_observation"]
    assert observed["recent"][-1]["ts"] == NOW
    assert not observed.get("last_observed_at")
    env.send.assert_not_awaited()


@pytest.mark.parametrize("context", [["broken"], "broken", True, 7])
def test_guard_completion_tolerates_malformed_context(env, context):
    seed(env, "panel")
    assert tianxing.close_tianxing_reply_guards(text_for("panel", True), NOW, "tianxing_panel", context) == 0
    assert "tianxing_panel" in action_guard.get_action_guard_sessions(IDENTITY_ID)


@pytest.mark.parametrize("command", [None, "", " ", False, [], {}])
def test_empty_retreat_pending_command_is_not_a_harmless_material_step(env, command):
    seed(env, "retreat")
    business_pending(env, "retreat")["command"] = command
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing._tianxing_pending_retreat_effect()
        plan = tianxing.build_tianxing_route_preflight_plan(tianxing.TIANXING_ROUTES[1], now=NOW + 1)
    assert not plan["route_allowed"]
    assert plan["stage"] == "retreat_pending"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", tuple(COMMANDS))
@pytest.mark.parametrize("field", ["send_as_id", "root_msg_id", "chat_id"])
def test_fractional_reply_reference_is_not_truncated_into_ownership(env, kind, field):
    seed(env, kind)
    expected = copy.deepcopy(env.identity)
    context = event_context(kind)
    context[field] += 0.5
    with state_module.use_identity(IDENTITY_ID):
        assert not tianxing.apply_tianxing_passive(
            text_for(kind, True), NOW, context["family"], reply_context=context,
        )
    assert env.identity == expected
    env.send.assert_not_awaited()
