import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import action_guard
from model import state as state_module
from model.features import tianxing
from model.real_message_replay import get_real_message_text


IDENTITY_ID = 990530001
ACCOUNT_ID = 7531
CHAT_ID = -100530001
BOT_ID = 880530001
NOW = 1780000000.0
ROOT_ID = 5301
SAMPLES = Path(__file__).parent / "fixtures" / "real_message_samples.json"
STAR = "\u592a\u9634"
PREDICT = "\u70bc\u5236"
CHANGE = "\u63a2\u7d22"
ARGS = {"predict": PREDICT, "change_fate": CHANGE, "set_star": STAR}
COMMANDS = {
    "predict": f"{tianxing.CMD_TIANXING_PREDICT} {PREDICT}",
    "change_fate": f"{tianxing.CMD_TIANXING_CHANGE_FATE} {CHANGE}",
    "set_star": f"{tianxing.CMD_TIANXING_SET_STAR} {STAR}",
    "panel": tianxing.CMD_TIANXING_PANEL,
    "observe": tianxing.CMD_TIANXING_OBSERVE,
    "clear_calamity": tianxing.CMD_TIANXING_CLEAR_CALAMITY,
}
PANEL = (
    "\u3010\u5929\u673a\u76d8\u3011\n"
    f"\u4eca\u65e5\u5df2\u5b9a\u547d\u661f: \u3010{STAR}\u3011\n"
    f"\u5f53\u524d\u63a8\u547d: {PREDICT}\uff08\u5269\u4f59 7\u5c0f\u65f6\uff09\n"
    f"\u5f53\u524d\u6539\u547d: {CHANGE}\uff08\u5269\u4f59 7\u5c0f\u65f6\uff09\n"
    "\u5929\u673a\u503c: 23\n\u9006\u547d\u52ab: 0"
)
PARTIAL_PANEL = "\u3010\u5929\u673a\u76d8\u3011\n\u5929\u673a\u503c: 23"
NEGATIVE = {
    "predict": f"\u4f60\u5df2\u6709\u4e00\u9053\u5173\u4e8e \u3010{CHANGE}\u3011 \u7684\u63a8\u547d\u5c1a\u672a\u5e94\u9a8c\uff0c\u8fd8\u9700\u7b49\u5f85 7\u5c0f\u65f633\u5206\u949f\u3002",
    "change_fate": f"\u4f60\u5df2\u6709\u4e00\u9053\u5173\u4e8e \u3010{PREDICT}\u3011 \u7684\u6539\u547d\u5c1a\u672a\u8017\u5c3d\uff0c\u8fd8\u53ef\u7ef4\u6301 21\u5c0f\u65f657\u5206\u949f\u3002",
    "set_star": "\u6b64\u547d\u661f\u5e76\u672a\u5728\u4f60\u4eca\u65e5\u89c2\u547d\u7ed3\u679c\u4e2d\u663e\u5316\uff0c\u8bf7\u5148 .\u89c2\u547d\u3002",
}


def reply_text(action):
    if action == "panel":
        return PANEL
    if action == "set_star":
        return f"\u4f60\u5c06\u4eca\u65e5\u547d\u8f68\u5b9a\u5728 \u3010{STAR}\u3011\u3002"
    if action == "clear_calamity":
        return "\u6210\u529f\u5316\u53bb 1 \u5c42\u9006\u547d\u52ab"
    return get_real_message_text(SAMPLES, f"tianxing.{action}.basic")


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID)
    state_module.update_send_as_profile(IDENTITY_ID, sect_name="\u5929\u661f\u5b97")
    state_module.set_game_bot_ids([BOT_ID])
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(tianxing_enabled=True, tianxing_timeline_state={})
    identity["tianxing_observation"] = tianxing.normalize_tianxing_observation({
        "last_action": "\u5929\u673a\u76d8", "last_result": "panel", "last_observed_at": NOW - 1,
        "fixed_star": STAR, "fixed_star_day": tianxing.get_day_key(NOW),
        "available_stars": list(tianxing.TIANXING_STARS),
        "available_stars_day": tianxing.get_day_key(NOW),
        "current_prediction": PREDICT, "current_prediction_until": NOW + 3600,
        "current_prediction_set_at": NOW - 1,
        "current_change": CHANGE, "current_change_until": NOW + 3600,
        "current_change_set_at": NOW - 1, "tianji_value": 24, "calamity_count": 2,
    })
    send = AsyncMock(return_value=None)
    monkeypatch.setattr(tianxing, "send_game_command", send)
    monkeypatch.setattr(tianxing, "save_state", Mock())
    monkeypatch.setattr(action_guard, "mark_dirty", Mock())
    yield SimpleNamespace(identity=identity, send=send)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def seed(env, action, **updates):
    step = {
        "action": action, "arg": ARGS.get(action, ""), "command": COMMANDS[action],
        "route": ARGS.get(action, "") if action in {"predict", "change_fate"} else "",
        "status": "sent_waiting_ack", "send_msg_id": ROOT_ID, "send_chat_id": CHAT_ID,
        "send_account_id": ACCOUNT_ID, "send_op_id": f"timeline-{action}-fixture",
        "queued_at": NOW - 30, "send_started_at": NOW - 20, "sent_at": NOW - 19,
        "ack_due_at": NOW + 60,
    } | updates
    env.identity["tianxing_timeline_state"] = tianxing.normalize_tianxing_timeline_state({
        "plan_id": "timeline-fixture", "phase": step["status"], "route": step["route"],
        "active_step_index": 0, "active_step": copy.deepcopy(step), "steps": [copy.deepcopy(step)],
    })
    return env.identity["tianxing_timeline_state"]["active_step"]


def context(**updates):
    return dict(send_as_id=IDENTITY_ID, chat_id=CHAT_ID, root_msg_id=ROOT_ID, msg_id=ROOT_ID + 1) | updates


def apply(action, *, ctx=None, text=None, now=NOW):
    with state_module.use_identity(IDENTITY_ID):
        return tianxing.apply_tianxing_passive(
            reply_text(action) if text is None else text,
            now=now, family=f"tianxing_{action}", reply_context=ctx,
        )


@pytest.mark.parametrize("action", tuple(COMMANDS))
def test_direct_terminal_result_confirms_its_own_timeline_step(env, action):
    seed(env, action)
    assert apply(action, ctx=context())
    assert env.identity["tianxing_timeline_state"]["active_step"]["status"] == "confirmed"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("action", tuple(COMMANDS))
@pytest.mark.parametrize("case", [
    "unanchored", "wrong_root", "wrong_chat", "wrong_identity", "missing_identity",
    "rebound", "missing_account", "missing_chat", "missing_dispatch", "before_dispatch",
    "nan_dispatch", "bool_dispatch", "fraction_root", "fraction_chat", "bool_root",
    "unbound_account", "missing_receipt", "bool_receipt", "nan_receipt", "before_receipt",
    "reversed_receipt", "wrong_command", "fraction_pending_id", "fraction_pending_account",
])
def test_unowned_timeline_reply_cannot_mutate_observation_or_step(env, action, case):
    step = seed(env, action)
    ctx = context()
    if case == "unanchored":
        ctx = None
    elif case == "wrong_root":
        ctx["root_msg_id"] += 10
    elif case == "wrong_chat":
        ctx["chat_id"] -= 1
    elif case == "wrong_identity":
        ctx["send_as_id"] += 1
    elif case == "missing_identity":
        ctx.pop("send_as_id")
    elif case == "rebound":
        state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID + 1)
    elif case == "missing_account":
        step.pop("send_account_id")
    elif case == "missing_chat":
        step.pop("send_chat_id")
    elif case == "missing_dispatch":
        step.pop("send_started_at")
    elif case == "before_dispatch":
        step["send_started_at"] = NOW + 10
    elif case == "nan_dispatch":
        step["send_started_at"] = float("nan")
    elif case == "bool_dispatch":
        step["send_started_at"] = True
    elif case == "fraction_root":
        ctx["root_msg_id"] += 0.5
    elif case == "fraction_chat":
        ctx["chat_id"] -= 0.5
    elif case == "bool_root":
        ctx["root_msg_id"] = True
    elif case == "unbound_account":
        state_module.set_identity_account(IDENTITY_ID, 0)
        step["send_account_id"] = 0
    elif case == "missing_receipt":
        step.pop("sent_at")
    elif case == "bool_receipt":
        step["sent_at"] = True
    elif case == "nan_receipt":
        step["sent_at"] = float("nan")
    elif case == "before_receipt":
        step["sent_at"] = NOW + 10
    elif case == "reversed_receipt":
        step["sent_at"] = NOW - 25
    elif case == "wrong_command":
        step["command"] = COMMANDS["panel"] if action != "panel" else COMMANDS["observe"]
    elif case == "fraction_pending_id":
        step["send_msg_id"] += 0.5
    elif case == "fraction_pending_account":
        step["send_account_id"] += 0.5
    before = copy.deepcopy(env.identity)
    assert not apply(action, ctx=ctx)
    assert env.identity["tianxing_observation"] == before["tianxing_observation"]
    assert env.identity["tianxing_timeline_state"] == before["tianxing_timeline_state"]


@pytest.mark.parametrize("action", tuple(COMMANDS))
def test_cached_observation_cannot_confirm_timeline_without_new_evidence(env, action):
    seed(env, action)
    if action == "clear_calamity":
        env.identity["tianxing_observation"].update(last_action="\u6d88\u52ab", last_result="success")
    before = copy.deepcopy(env.identity["tianxing_timeline_state"])
    with state_module.use_identity(IDENTITY_ID):
        changed, _ = tianxing._confirm_tianxing_timeline_from_observation(NOW)
    assert not changed
    assert env.identity["tianxing_timeline_state"] == before


@pytest.mark.parametrize("field", ["action", "status"])
@pytest.mark.parametrize("value", [[], {}, True, 1])
def test_malformed_timeline_pending_retains_ownership_without_crashing(env, field, value):
    step = seed(env, "predict")
    step[field] = value
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.has_tianxing_pending_reply("tianxing_predict")
        assert not tianxing._recover_tianxing_timeline_reply_from_message_log(NOW)
    assert not apply("predict", ctx=context())
    assert env.identity["tianxing_observation"] == before["tianxing_observation"]
    assert env.identity["tianxing_timeline_state"] == before["tianxing_timeline_state"]


@pytest.mark.parametrize("action", ["predict", "change_fate", "set_star", "observe"])
def test_partial_panel_does_not_complete_step_using_cached_fields(env, action):
    seed(env, action)
    before = copy.deepcopy(env.identity["tianxing_timeline_state"])
    apply("panel", ctx=context(root_msg_id=ROOT_ID + 10), text=PARTIAL_PANEL)
    assert env.identity["tianxing_timeline_state"] == before


@pytest.mark.parametrize("action", tuple(NEGATIVE))
@pytest.mark.parametrize("owned", [False, True])
def test_negative_reply_requires_current_operation_ownership(env, action, owned):
    seed(env, action)
    before = copy.deepcopy(env.identity)
    apply(action, ctx=context(root_msg_id=ROOT_ID if owned else ROOT_ID + 10), text=NEGATIVE[action])
    if owned:
        assert env.identity["tianxing_timeline_state"] != before["tianxing_timeline_state"]
    else:
        assert env.identity["tianxing_timeline_state"] == before["tianxing_timeline_state"]
        assert env.identity["tianxing_observation"] == before["tianxing_observation"]


@pytest.mark.parametrize("action", tuple(COMMANDS))
def test_early_reply_waits_for_exact_detached_receipt_then_completes(env, action):
    step = seed(env, action, status="sending", send_msg_id=0, send_chat_id=0, sent_at=0, send_started_at=0)
    assert not apply(action, ctx=context())
    assert env.identity["tianxing_timeline_state"]["active_step"]["status"] == "sending"
    env.identity["pending_tasks"][(CHAT_ID, ROOT_ID)] = {
        "cmd": COMMANDS[action], "source_module": "\u5929\u661f\u5b97", "op_id": step["send_op_id"],
        "chat_id": CHAT_ID, "send_started_at": NOW - 1, "sent_at": NOW + 2,
    }
    assert apply(action, ctx=context(processed_at=NOW + 3))
    completed = env.identity["tianxing_timeline_state"]["active_step"]
    assert completed["status"] == "confirmed"
    assert completed["send_msg_id"] == ROOT_ID
    assert completed["send_started_at"] == NOW - 1
    assert env.identity["tianxing_observation"]["last_observed_at"] == NOW
    env.send.assert_not_awaited()


def record_panel_query(env):
    root = ROOT_ID + 10
    action_guard.note_sent(
        COMMANDS["panel"], IDENTITY_ID, root, sent_at=NOW - 5, chat_id=CHAT_ID,
    )
    env.identity["pending_tasks"][(CHAT_ID, root)] = {
        "cmd": COMMANDS["panel"], "chat_id": CHAT_ID,
        "send_started_at": NOW - 6, "sent_at": NOW - 5,
    }
    return context(root_msg_id=root, msg_id=root + 1)


@pytest.mark.parametrize("action", ["predict", "change_fate", "set_star"])
@pytest.mark.parametrize("case", [
    "matching", "partial", "explicit_empty", "old_query", "missing_dispatch", "other_account",
    "other_chat", "wrong_command", "wrong_receipt_time", "unanchored",
])
def test_panel_calibration_uses_only_its_actual_fields_and_verified_query(env, action, case):
    seed(env, action, status="ack_timeout")
    ctx = record_panel_query(env)
    record = env.identity["pending_tasks"][(CHAT_ID, ROOT_ID + 10)]
    guard = action_guard.get_action_guard_sessions(IDENTITY_ID)["tianxing_panel"]
    text = PANEL
    if case == "partial":
        text = PARTIAL_PANEL
    elif case == "explicit_empty":
        text = "\u3010\u5929\u673a\u76d8\u3011\n\u5f53\u524d\u63a8\u547d: \u65e0\n\u5f53\u524d\u6539\u547d: \u65e0\n\u4eca\u65e5\u5df2\u5b9a\u547d\u661f: \u672a\u5b9a\u547d"
    elif case == "old_query":
        record["send_started_at"] = NOW - 30
    elif case == "missing_dispatch":
        record.pop("send_started_at")
    elif case == "other_account":
        guard["last_account_id"] += 1
    elif case == "other_chat":
        ctx["chat_id"] -= 1
    elif case == "wrong_command":
        record["cmd"] = COMMANDS["observe"]
    elif case == "wrong_receipt_time":
        record["sent_at"] += 1
    elif case == "unanchored":
        ctx = None
    before = copy.deepcopy(env.identity)
    accepted = apply("panel", ctx=ctx, text=text)
    timeline = env.identity["tianxing_timeline_state"]
    if case == "matching":
        assert accepted and timeline["active_step"]["status"] == "confirmed"
    elif case == "explicit_empty":
        assert accepted and timeline["phase"] == "blocked_replan" and not timeline["active_step"]
    else:
        assert timeline == before["tianxing_timeline_state"]
        if case != "partial":
            assert not accepted
            assert env.identity["tianxing_observation"] == before["tianxing_observation"]
    env.send.assert_not_awaited()


@pytest.mark.parametrize("action", tuple(COMMANDS))
@pytest.mark.parametrize("case", ["exact", "unthreaded", "wrong_chat", "wrong_bot", "before_dispatch", "latest_edit_incomplete"])
def test_log_recovery_requires_trusted_exact_reply_and_latest_edit(env, monkeypatch, action, case):
    seed(env, action)
    entry = {
        "chat_id": CHAT_ID, "message_id": ROOT_ID + 1, "reply_to_msg_id": ROOT_ID,
        "sender_id": BOT_ID, "sender_is_bot": True, "text": reply_text(action),
        "event_type": "message", "ts_epoch": NOW,
    }
    if case == "unthreaded":
        entry["reply_to_msg_id"] = 0
    elif case == "wrong_chat":
        entry["chat_id"] -= 1
    elif case == "wrong_bot":
        entry["sender_id"] += 1
    elif case == "before_dispatch":
        entry["ts_epoch"] = NOW - 25
    entries = [entry]
    if case == "latest_edit_incomplete":
        entries.append(dict(entry, event_type="edit", text="\u53f8\u547d\u76d8\u6b63\u5728\u63a8\u6f14\u3002", ts_epoch=NOW + 1))
    find = Mock(return_value=entries)
    monkeypatch.setattr(tianxing, "find_message_log_replies_tail", find)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(IDENTITY_ID):
        changed = tianxing._recover_tianxing_timeline_reply_from_message_log(NOW + 2)
    assert changed is (case == "exact")
    assert find.call_args.args[0] == ROOT_ID
    assert find.call_args.kwargs["chat_id"] == CHAT_ID
    assert find.call_args.kwargs["max_bytes"] == 512 * 1024
    if case == "exact":
        assert env.identity["tianxing_timeline_state"]["active_step"]["status"] == "confirmed"
    else:
        assert env.identity["tianxing_timeline_state"] == before["tianxing_timeline_state"]
        assert env.identity["tianxing_observation"] == before["tianxing_observation"]
    env.send.assert_not_awaited()


@pytest.mark.parametrize("return_id", [ROOT_ID, ROOT_ID + 0.5, True])
@pytest.mark.parametrize("early_reply", [False, True])
def test_real_send_receipt_replays_early_result_without_guessing_or_resending(env, monkeypatch, return_id, early_reply):
    step = seed(env, "predict", status="pending", send_msg_id=0, send_chat_id=0)
    env.identity["tianxing_observation"].update(current_prediction="", current_prediction_until=0, current_prediction_set_at=0)
    monkeypatch.setattr(tianxing._TianxingOperation, "current_time", lambda _self: NOW + 3)
    entries = []

    async def send(command, **kwargs):
        current = env.identity["tianxing_timeline_state"]["active_step"]
        assert current["status"] == "sending"
        assert current["send_account_id"] == ACCOUNT_ID
        assert current["send_op_id"] == kwargs["op_id"]
        assert kwargs["operation_check"]()
        assert current["queued_at"] == NOW
        assert current["send_started_at"] == 0
        if early_reply:
            assert not apply("predict", ctx=context(), now=NOW + 1)
            entries.append({
                "event_type": "message", "chat_id": CHAT_ID, "message_id": ROOT_ID + 1,
                "reply_to_msg_id": ROOT_ID, "sender_id": BOT_ID, "text": reply_text("predict"),
                "ts_epoch": NOW + 1,
            })
        return SimpleNamespace(id=return_id, chat_id=CHAT_ID, send_started_at=NOW + 0.5, sent_at=NOW + 2)

    env.send.side_effect = send
    monkeypatch.setattr(tianxing, "find_message_log_replies_tail", Mock(side_effect=lambda *_a, **_k: entries))
    with state_module.use_identity(IDENTITY_ID):
        operation = tianxing._TianxingOperation.capture(NOW)
        asyncio.run(tianxing._send_tianxing_timeline_step(
            env.identity["tianxing_timeline_state"], step, NOW, operation.config, operation=operation,
        ))
    current = env.identity["tianxing_timeline_state"]["active_step"]
    if type(return_id) is int:
        assert current["send_started_at"] == NOW + 0.5
        assert current["status"] == ("confirmed" if early_reply else "sent_waiting_ack")
    else:
        assert current["status"] == "ack_timeout"
        assert not current["send_msg_id"]
        assert env.identity["tianxing_observation"]["current_prediction"] == ""
    env.send.assert_awaited_once()


def test_timeline_receipt_survives_sqlite_reload_and_rejects_rebound_account(env, monkeypatch, tmp_path):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "timeline-evidence.db"))
    step = seed(env, "predict")
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(IDENTITY_ID)
    assert env.identity["tianxing_timeline_state"]["active_step"] == step
    state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID + 1)
    assert not apply("predict", ctx=context())
    state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID)
    assert apply("predict", ctx=context())
    assert env.identity["tianxing_timeline_state"]["active_step"]["status"] == "confirmed"


def test_partial_terminal_panel_waits_for_target_field_in_final_edit(env):
    target = copy.deepcopy(seed(env, "predict", status="ack_timeout"))
    panel = copy.deepcopy(seed(env, "panel", terminal_after_confirm=True))
    env.identity["tianxing_timeline_state"].update(
        active_step_index=1, steps=[target, panel],
    )
    before = copy.deepcopy(env.identity["tianxing_timeline_state"])
    assert apply("panel", ctx=context(), text=PARTIAL_PANEL)
    assert env.identity["tianxing_timeline_state"] == before
    text = "\u3010\u5929\u673a\u76d8\u3011\n\u5f53\u524d\u63a8\u547d: \u65e0\n\u5929\u673a\u503c: 23"
    assert apply("panel", ctx=context(), text=text, now=NOW + 5)
    assert env.identity["tianxing_timeline_state"]["phase"] == "blocked_replan"


def test_confirmed_panel_preserves_correlated_guard_effect_timestamp(env):
    seed(env, "panel", send_msg_id=ROOT_ID + 10, send_started_at=NOW - 6, sent_at=NOW - 5)
    env.identity["tianxing_observation"].update(current_prediction="", current_prediction_set_at=0)
    action_guard.note_sent(COMMANDS["predict"], IDENTITY_ID, ROOT_ID, sent_at=NOW - 19, chat_id=CHAT_ID)
    ctx = record_panel_query(env)
    assert apply("panel", ctx=ctx, text=PANEL)
    assert env.identity["tianxing_timeline_state"]["active_step"]["status"] == "confirmed"
    assert env.identity["tianxing_observation"]["current_prediction_set_at"] == NOW - 19


@pytest.mark.parametrize("action", tuple(COMMANDS))
def test_routed_timeline_reply_preserves_native_pending_until_real_terminal_result(env, monkeypatch, action):
    from model import app, app_runtime, runtime
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
    seed(env, action)
    runtime._finalize_game_command_sent(
        COMMANDS[action], msg_id=ROOT_ID, sent_at=NOW - 19, send_started_at=NOW - 20,
        send_as_id=IDENTITY_ID, game_group_id=CHAT_ID, topic_id=0,
        max_retry=0, append_sent_log=False,
    )
    ctx = context(family=f"tianxing_{action}", reply_to_msg_id=ROOT_ID)
    reply = SimpleNamespace(id=ROOT_ID, chat_id=CHAT_ID, raw_text=COMMANDS[action])
    incomplete = "\u53f8\u547d\u76d8\u6b63\u5728\u63a8\u6f14\u3002"
    event = SimpleNamespace(id=ROOT_ID + 1, chat_id=CHAT_ID, sender_id=BOT_ID, raw_text=incomplete)
    asyncio.run(app._handle_routed_reply_event(event, incomplete, NOW, reply, ctx))
    assert (CHAT_ID, ROOT_ID) in env.identity["pending_tasks"]
    assert env.identity["tianxing_timeline_state"]["active_step"]["status"] == "sent_waiting_ack"
    assert not app._has_runtime_message_consumed(event, ctx["family"])
    event.raw_text = reply_text(action)
    assert asyncio.run(app._handle_routed_reply_event(event, event.raw_text, NOW + 1, reply, ctx, event_kind="edit"))
    assert env.identity["tianxing_timeline_state"]["active_step"]["status"] == "confirmed"
    assert (CHAT_ID, ROOT_ID) not in env.identity["pending_tasks"]
    assert app._has_runtime_message_consumed(event, ctx["family"])
    env.send.assert_not_awaited()
