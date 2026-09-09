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


IDENTITY_ID = 990490001
ACCOUNT_ID = 7491
CHAT_ID = -100490001
NOW = 1780000000.0
SAMPLES = Path(__file__).parent / "fixtures" / "real_message_samples.json"
PREDICT = "\u70bc\u5236"
CHANGE = "\u63a2\u7d22"
STAR = "\u592a\u9634"
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
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(tianxing_enabled=True, tianxing_timeline_state={})
    identity["tianxing_observation"] = tianxing.normalize_tianxing_observation({
        "last_action": "\u5929\u673a\u76d8", "last_result": "panel",
        "last_observed_at": NOW - 1, "fixed_star": STAR,
        "fixed_star_day": tianxing.get_day_key(NOW),
        "available_stars": list(tianxing.TIANXING_STARS),
        "available_stars_day": tianxing.get_day_key(NOW),
        "current_prediction": PREDICT, "current_prediction_until": NOW + 3600,
        "current_change": CHANGE, "current_change_until": NOW + 3600,
        "tianji_value": 24, "calamity_count": 2,
    })
    send = AsyncMock(return_value=None)
    monkeypatch.setattr(tianxing, "send_game_command", send)
    monkeypatch.setattr(tianxing, "save_state", Mock())
    monkeypatch.setattr(action_guard, "mark_dirty", Mock())
    monkeypatch.setattr(tianxing, "_recover_tianxing_timeline_reply_from_message_log", lambda *_args: False)
    yield SimpleNamespace(identity=identity, send=send)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def note(action, *, msg_id=4901, chat_id=CHAT_ID, sent_at=NOW - 20, dispatch_at=None):
    action_guard.note_sent(COMMANDS[action], IDENTITY_ID, msg_id, sent_at=sent_at, chat_id=chat_id)
    if dispatch_at is not None:
        state_module.get_identity_state(IDENTITY_ID)["pending_tasks"][(chat_id, msg_id)] = {
            "cmd": COMMANDS[action], "chat_id": chat_id,
            "sent_at": sent_at, "send_started_at": dispatch_at,
        }
    return action_guard.get_action_guard_sessions(IDENTITY_ID)[f"tianxing_{action}"]


def context(**updates):
    return dict(send_as_id=IDENTITY_ID, chat_id=CHAT_ID, root_msg_id=4901, msg_id=4999) | updates


def apply(action, *, ctx=None, text=None, now=NOW):
    with state_module.use_identity(IDENTITY_ID):
        return tianxing.apply_tianxing_passive(
            reply_text(action) if text is None else text,
            now=now, family=f"tianxing_{action}", reply_context=ctx,
        )


def sessions():
    return action_guard.get_action_guard_sessions(IDENTITY_ID)


@pytest.mark.parametrize("action", tuple(COMMANDS))
def test_exact_direct_result_closes_only_its_own_guard(env, action):
    for other in COMMANDS:
        note(other, msg_id=4901 if other == action else 4800 + list(COMMANDS).index(other))
    assert apply(action, ctx=context())
    assert f"tianxing_{action}" not in sessions()
    assert set(sessions()) == {f"tianxing_{other}" for other in COMMANDS if other != action}


@pytest.mark.parametrize("action", tuple(COMMANDS))
@pytest.mark.parametrize("case", [
    "unanchored", "wrong_chat", "wrong_root", "wrong_identity", "missing_identity",
    "account_rebound", "legacy_account", "legacy_chat", "missing_sent_at", "nan_sent_at",
    "inf_sent_at", "future_sent_at", "invalid_root", "invalid_chat", "invalid_identity",
])
def test_unrelated_or_unverifiable_result_does_not_close_guard(env, action, case):
    session = note(action)
    ctx = context()
    if case == "unanchored":
        ctx = None
    elif case == "wrong_chat":
        ctx["chat_id"] -= 1
    elif case == "wrong_root":
        ctx["root_msg_id"] -= 1
    elif case == "wrong_identity":
        ctx["send_as_id"] += 1
    elif case == "missing_identity":
        ctx.pop("send_as_id")
    elif case == "account_rebound":
        state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID + 1)
    elif case == "legacy_account":
        session.pop("last_account_id", None)
    elif case == "legacy_chat":
        session.pop("last_chat_id")
    elif case == "missing_sent_at":
        session.pop("last_sent_at")
    elif case == "nan_sent_at":
        session["last_sent_at"] = float("nan")
    elif case == "inf_sent_at":
        session["last_sent_at"] = float("inf")
    elif case == "future_sent_at":
        session["last_sent_at"] = NOW + 10
    else:
        ctx[{"invalid_root": "root_msg_id", "invalid_chat": "chat_id", "invalid_identity": "send_as_id"}[case]] = "bad"
    apply(action, ctx=ctx)
    assert f"tianxing_{action}" in sessions()


@pytest.mark.parametrize("action", ["predict", "change_fate", "set_star", "observe"])
def test_partial_panel_cannot_borrow_fields_from_cached_observation(env, action):
    note(action)
    note("panel", msg_id=4902, sent_at=NOW - 5, dispatch_at=NOW - 6)
    assert apply("panel", ctx=context(root_msg_id=4902), text="\u3010\u5929\u673a\u76d8\u3011\n\u5929\u673a\u503c: 23")
    assert f"tianxing_{action}" in sessions()
    assert "tianxing_panel" not in sessions()


@pytest.mark.parametrize("case", [
    "after", "older_query", "missing_dispatch", "nan_dispatch", "future_dispatch",
    "receipt_mismatch", "wrong_query_command", "other_chat", "target_rebound",
    "query_rebound", "newer_target", "different_argument",
])
@pytest.mark.parametrize("action", ["predict", "change_fate", "set_star"])
def test_panel_calibration_requires_original_query_and_target_order(env, action, case):
    target = note(action)
    query = note("panel", msg_id=4902, sent_at=NOW - 5, dispatch_at=NOW - 6)
    record = env.identity["pending_tasks"][(CHAT_ID, 4902)]
    if case == "older_query":
        record["send_started_at"] = NOW - 30
    elif case == "missing_dispatch":
        record.pop("send_started_at")
    elif case == "nan_dispatch":
        record["send_started_at"] = float("nan")
    elif case == "future_dispatch":
        record["send_started_at"] = NOW + 1
    elif case == "receipt_mismatch":
        record["sent_at"] = NOW - 4
    elif case == "wrong_query_command":
        record["cmd"] = tianxing.CMD_TIANXING_OBSERVE
    elif case == "other_chat":
        target["last_chat_id"] -= 1
    elif case == "target_rebound":
        target["last_account_id"] = ACCOUNT_ID + 1
    elif case == "query_rebound":
        query["last_account_id"] = ACCOUNT_ID + 1
    elif case == "newer_target":
        target["last_sent_at"] = NOW - 1
        target["last_msg_id"] = 4903
    elif case == "different_argument":
        target["last_command"] = COMMANDS[action].split()[0] + " invalid"
    assert apply("panel", ctx=context(root_msg_id=4902))
    assert (f"tianxing_{action}" not in sessions()) is (case == "after")


@pytest.mark.parametrize("text", [
    "\u3010\u5929\u661f\u5b97\u73a9\u6cd5\u5e2e\u52a9\u3011",
    "\u4f60\u6240\u5c5e\u7684\u5b97\u95e8: \u3010\u5929\u661f\u5b97\u3011\n\u53f8\u547d\u76d8\u8981\u8bc0",
    "\u3010\u5929\u673a\u76d8\u3011",
])
def test_guide_or_empty_panel_is_not_a_query_result(env, text):
    note("panel")
    apply("panel", ctx=context(), text=text)
    assert "tianxing_panel" in sessions()


def test_passive_closure_supplies_exact_expected_message_and_chat(env, monkeypatch):
    note("predict")
    original_close = action_guard.close_action

    def replace_before_close(key, *args, **kwargs):
        assert kwargs["expected_msg_id"] == 4901
        assert kwargs["expected_chat_id"] == CHAT_ID
        note("predict", msg_id=4902, chat_id=CHAT_ID - 1, sent_at=NOW)
        return original_close(key, *args, **kwargs)

    monkeypatch.setattr(action_guard, "close_action", replace_before_close)
    assert apply("predict", ctx=context())
    assert sessions()["tianxing_predict"]["last_msg_id"] == 4902


def test_scheduler_does_not_close_a_guard_from_unattributed_cached_state(env, monkeypatch):
    note("predict")
    config = tianxing.normalize_tianxing_auto_config({"timeline_enabled": True, "dry_run": False})
    env.identity["tianxing_auto_config"] = config
    monkeypatch.setattr(tianxing, "_TIANXING_TIMELINE_LOCKS", {})
    with state_module.use_identity(IDENTITY_ID):
        asyncio.run(tianxing.run_tianxing_timeline_scheduler(
            NOW, windows=[{"route": PREDICT, "kind": "farm", "start_at": NOW, "end_at": NOW + 3600, "weight": 8}],
        ))
    assert "tianxing_predict" in sessions()


def test_closing_after_module_disable_still_reconciles_dispatched_work(env):
    note("predict")
    env.identity["tianxing_enabled"] = False
    assert apply("predict", ctx=context())
    assert "tianxing_predict" not in sessions()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("path", ["routed", "passive"])
@pytest.mark.parametrize("action", tuple(COMMANDS))
@pytest.mark.parametrize("case", ["exact", "wrong_root", "wrong_chat", "account_rebound", "incomplete"])
def test_dispatchers_do_not_bypass_tianxing_guard_evidence(env, monkeypatch, path, action, case):
    from model import app, app_runtime, runtime
    from model.features import passive_inbox

    note(action)
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock())
    monkeypatch.setattr(passive_inbox, "save_state", Mock())
    ctx = context(family=f"tianxing_{action}")
    text = reply_text(action)
    if case == "wrong_root":
        ctx["root_msg_id"] -= 1
    elif case == "wrong_chat":
        ctx["chat_id"] -= 1
    elif case == "account_rebound":
        state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID + 1)
    elif case == "incomplete":
        text = "\u3010\u5929\u673a\u76d8\u3011"
    ctx["reply_to_msg_id"] = ctx["root_msg_id"]
    event = SimpleNamespace(id=ctx["msg_id"], chat_id=ctx["chat_id"], sender_id=880490001)
    state_module.set_game_bot_ids([event.sender_id])
    if path == "routed":
        reply = SimpleNamespace(id=ctx["root_msg_id"], chat_id=ctx["chat_id"], raw_text=COMMANDS[action])
        assert asyncio.run(app._handle_routed_reply_event(event, text, NOW, reply, ctx))
    else:
        assert asyncio.run(passive_inbox.handle_passive_module_card(text, NOW, ctx, event, event_type="message"))
    assert (f"tianxing_{action}" not in sessions()) is (case == "exact")
    env.send.assert_not_awaited()


@pytest.mark.parametrize("reloaded", [False, True])
@pytest.mark.parametrize("older_query", [False, True])
def test_real_runtime_receipt_and_log_replay_preserve_query_order(env, monkeypatch, tmp_path, reloaded, older_query):
    from model import persistence, runtime

    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(runtime, "_GAME_SEND_BLOCK_LAST", {})
    for action, msg_id, sent_at, dispatch_at in (
        ("predict", 4901, NOW - 20, NOW - 21),
        ("panel", 4902, NOW - 5, NOW - 30 if older_query else NOW - 6),
    ):
        msg = runtime._finalize_game_command_sent(
            COMMANDS[action], msg_id=msg_id, sent_at=sent_at, send_started_at=dispatch_at,
            send_as_id=IDENTITY_ID, game_group_id=CHAT_ID, topic_id=0,
            max_retry=0, append_sent_log=False,
        )
        assert msg.id == msg_id
        assert sessions()[f"tianxing_{action}"]["last_account_id"] == ACCOUNT_ID
    if reloaded:
        monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "guard-receipts.db"))
        assert persistence.save_state()
        assert persistence.load_state()
        env.identity = state_module.get_identity_state(IDENTITY_ID)
        assert sessions()["tianxing_panel"]["last_account_id"] == ACCOUNT_ID
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing._apply_tianxing_log_reply({
            "chat_id": CHAT_ID, "reply_to_msg_id": 4902, "message_id": 4999,
            "ts_epoch": NOW, "text": PANEL,
        }, family="tianxing_panel")
    assert ("tianxing_predict" in sessions()) is older_query
    assert "tianxing_panel" not in sessions()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("action", ["predict", "change_fate", "set_star", "clear_calamity"])
def test_exact_negative_reply_closes_only_the_original_guard(env, action):
    texts = {
        "predict": "\u4f60\u5df2\u6709\u4e00\u9053\u5173\u4e8e \u3010\u63a2\u7d22\u3011 \u7684\u63a8\u547d\u5c1a\u672a\u5e94\u9a8c\uff0c\u8bf7\u7b49\u5f85 1\u5c0f\u65f6\u3002",
        "change_fate": "\u6539\u547d\u5931\u8d25\uff0c\u5929\u673a\u503c\u4e0d\u8db3\u3002",
        "set_star": "\u6b64\u547d\u661f\u5e76\u672a\u5728\u4f60\u4eca\u65e5\u89c2\u547d\u7ed3\u679c\u4e2d\u663e\u5316\uff0c\u8bf7\u5148 .\u89c2\u547d\u3002",
        "clear_calamity": "\u5f53\u524d\u5e76\u65e0\u9006\u547d\u52ab\u7f20\u8eab\u3002",
    }
    note(action)
    note("panel", msg_id=4902, sent_at=NOW - 5)
    assert apply(action, ctx=context(), text=texts[action])
    assert f"tianxing_{action}" not in sessions()
    assert "tianxing_panel" in sessions()


def test_old_replayed_result_does_not_close_a_new_guard(env):
    note("predict")
    assert apply("predict", ctx=context())
    note("predict", msg_id=4910, sent_at=NOW + 1)
    assert apply("predict", ctx=context(), now=NOW + 2)
    assert sessions()["tianxing_predict"]["last_msg_id"] == 4910


@pytest.mark.parametrize("field", ["root_msg_id", "chat_id", "send_as_id", "last_msg_id", "last_chat_id", "last_account_id"])
def test_fractional_reference_is_not_coerced_to_a_different_valid_reference(env, field):
    session = note("predict")
    ctx = context()
    data = session if field.startswith("last_") else ctx
    data[field] += 0.5 if data[field] > 0 else -0.5
    apply("predict", ctx=ctx)
    assert "tianxing_predict" in sessions()


def test_boolean_timestamp_is_not_sent_evidence(env):
    note("predict")["last_sent_at"] = True
    apply("predict", ctx=context())
    assert "tianxing_predict" in sessions()
