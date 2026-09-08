import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import tianxing
from model.real_message_replay import get_real_message_text


IDENTITY_ID = 990460001
CHAT_ID = -100460001
BOT_ID = 880460001
NOW = 1700000000.0
MUTATIONS = ("set_star", "predict", "change_fate", "clear_calamity")
SAMPLES = Path(__file__).parent / "fixtures" / "real_message_samples.json"


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY_ID, 7461)
    state_module.update_send_as_profile(IDENTITY_ID, sect_name="\u5929\u661f\u5b97")
    state_module.set_game_bot_ids([BOT_ID])
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(
        tianxing_enabled=True,
        tianxing_auto_config={
            "timeline_enabled": False, "craft_farm_enabled": False,
            "daily_observe_enabled": False, "daily_set_star_enabled": False,
        },
        tianxing_observation=tianxing.normalize_tianxing_observation({
            "last_observed_at": NOW - 10,
            "available_stars": list(tianxing.TIANXING_STARS),
            "available_stars_day": tianxing.get_day_key(NOW),
            "tianji_value": 12, "calamity_count": 2,
        }),
    )
    send = AsyncMock(return_value=None)
    lookup = Mock(return_value=[])
    monkeypatch.setattr(tianxing, "send_game_command", send)
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {})
    monkeypatch.setattr(tianxing, "get_sent_message_chat_id", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(tianxing, "find_message_log_replies", lookup)
    monkeypatch.setattr(tianxing, "_recover_tianxing_daily_observe_from_message_log", lambda *_args: False)
    monkeypatch.setattr(tianxing, "_TIANXING_AUTO_LOCKS", {})
    monkeypatch.setattr(tianxing, "_TIANXING_TIMELINE_LOCKS", {})
    save = Mock()
    monkeypatch.setattr(tianxing, "save_state", save)
    yield SimpleNamespace(identity=identity, send=send, lookup=lookup, save=save)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def argument(action):
    if action == "set_star":
        return tianxing.TIANXING_STARS[2]
    if action == "predict":
        return tianxing.TIANXING_ROUTES[1]
    if action == "change_fate":
        return tianxing.TIANXING_ROUTES[2]
    return ""


def pending(env, action="clear_calamity", *, msg_id=4601, sent_at=NOW - 120, due_at=NOW - 1):
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_manual_plan(action, argument(action), now=NOW)
    assert plan["allowed"]
    env.identity["tianxing_observation"].update(
        auto_pending_action=action, auto_pending_command=plan["command"],
        auto_pending_msg_id=msg_id, auto_pending_chat_id=CHAT_ID if msg_id else 0,
        auto_pending_sent_at=sent_at, auto_pending_due_at=due_at, auto_next_time=due_at,
    )
    return env.identity["tianxing_observation"]


async def execute(action="clear_calamity"):
    with state_module.use_identity(IDENTITY_ID):
        observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])
        config = tianxing.normalize_tianxing_auto_config(state_module.state["tianxing_auto_config"])
        plan = tianxing.build_tianxing_manual_plan(action, argument(action), now=NOW)
        assert plan["allowed"]
        return await tianxing._execute_tianxing_auto_plan(plan, observed, config, NOW)


async def schedule(now):
    with state_module.use_identity(IDENTITY_ID):
        await tianxing.run_tianxing_scheduler(now)


@pytest.mark.parametrize("action", MUTATIONS)
@pytest.mark.parametrize("block", [{}, {"code": "send_timeout"}, {"code": "new_unknown_code"}])
def test_unknown_send_never_becomes_a_retry(env, monkeypatch, action, block):
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: block)
    assert not asyncio.run(execute(action))
    observed = env.identity["tianxing_observation"]
    assert observed["auto_pending_action"] == action
    original = {key: observed[key] for key in ("auto_pending_command", "auto_pending_sent_at", "auto_pending_msg_id")}
    for delta in (600, 4000, 24 * 3600, 48 * 3600):
        asyncio.run(schedule(NOW + delta))
    observed = env.identity["tianxing_observation"]
    assert observed["auto_pending_action"] == action
    assert {key: observed[key] for key in original} == original
    assert observed["auto_last_error"]
    env.send.assert_awaited_once()


@pytest.mark.parametrize("block", [
    {"code": "send_queue_timeout"}, {"code": "send_prepare_timeout"},
    {"code": "action_guard"}, {"code": "global_disabled"},
    {"code": "operation_changed", "definitely_unsent": True},
])
def test_explicit_unsent_result_can_back_off_without_pending(env, monkeypatch, block):
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: block)
    assert not asyncio.run(execute())
    observed = env.identity["tianxing_observation"]
    assert not observed["auto_pending_action"]
    assert observed["auto_next_time"] > NOW
    env.lookup.assert_not_called()


@pytest.mark.parametrize("msg_id", [0, -1, "bad", True, 1.5, float("nan"), float("inf")])
def test_invalid_receipt_is_unknown_not_success_or_unsent(env, monkeypatch, msg_id):
    env.send.return_value = SimpleNamespace(id=msg_id, sent_at=NOW, chat_id=CHAT_ID)
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": "global_disabled"})
    assert not asyncio.run(execute())
    observed = env.identity["tianxing_observation"]
    assert observed["auto_pending_action"] == "clear_calamity"
    assert observed["auto_pending_msg_id"] == 0
    assert observed["auto_last_error"]


@pytest.mark.parametrize("cancelled", [False, True])
def test_exception_preserves_unknown_and_cancellation_propagates(env, monkeypatch, cancelled):
    env.send.side_effect = asyncio.CancelledError if cancelled else RuntimeError("fixture transport failure")
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": "global_disabled"})
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(execute())
    else:
        assert not asyncio.run(execute())
    assert env.identity["tianxing_observation"]["auto_pending_action"] == "clear_calamity"
    assert env.identity["tianxing_observation"]["auto_last_error"]


@pytest.mark.parametrize("action", MUTATIONS)
@pytest.mark.parametrize("msg_id", [0, 4601])
def test_mutation_timeout_retains_original_evidence(env, action, msg_id):
    observed = pending(env, action, msg_id=msg_id)
    original = {key: value for key, value in observed.items() if key.startswith("auto_pending_") and key != "auto_pending_due_at"}
    asyncio.run(schedule(NOW))
    observed = env.identity["tianxing_observation"]
    assert {key: observed[key] for key in original} == original
    assert observed["auto_pending_due_at"] > NOW
    assert observed["auto_next_time"] >= observed["auto_pending_due_at"]
    lookup_count = env.lookup.call_count
    env.save.reset_mock()
    for delta in (1, 5, 10):
        asyncio.run(schedule(NOW + delta))
    assert env.lookup.call_count == lookup_count
    env.save.assert_not_called()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("action", ["observe", "panel"])
def test_read_only_timeout_keeps_bounded_retry(env, action):
    pending(env, action)
    asyncio.run(schedule(NOW))
    observed = env.identity["tianxing_observation"]
    assert not observed["auto_pending_action"]
    assert observed["auto_next_time"] > NOW
    env.send.assert_not_awaited()


def reply_context(**updates):
    return dict(send_as_id=IDENTITY_ID, root_msg_id=4601, chat_id=CHAT_ID, **updates)


@pytest.mark.parametrize("case", ["old", "wrong_chat", "wrong_root", "wrong_identity", "unanchored", "wrong_argument"])
def test_unrelated_result_cannot_clear_auto_pending(env, case):
    pending(env, "set_star")
    context = reply_context()
    reply_at = NOW
    text = f"\u4f60\u5c06\u4eca\u65e5\u547d\u8f68\u5b9a\u5728 \u3010{argument('set_star')}\u3011\u3002"
    if case == "old":
        reply_at = NOW - 500
    elif case == "wrong_chat":
        context["chat_id"] -= 1
    elif case == "wrong_root":
        context["root_msg_id"] += 1
    elif case == "wrong_identity":
        context["send_as_id"] += 1
    elif case == "unanchored":
        context = None
    else:
        text = text.replace(argument("set_star"), tianxing.TIANXING_STARS[0])
    expected = copy.deepcopy(env.identity["tianxing_observation"])
    with state_module.use_identity(IDENTITY_ID):
        tianxing.apply_tianxing_passive(text, now=reply_at, family="tianxing_set_star", reply_context=context)
    assert env.identity["tianxing_observation"]["auto_pending_action"] == "set_star"
    assert env.identity["tianxing_observation"] == expected


@pytest.mark.parametrize("action,sample", [
    ("predict", "tianxing.predict.basic"),
    ("change_fate", "tianxing.change_fate.basic"),
    ("clear_calamity", "tianxing.clear_calamity.basic"),
])
def test_exact_real_reply_resolves_pending(env, action, sample):
    pending(env, action)
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(
            get_real_message_text(SAMPLES, sample), now=NOW,
            family=tianxing._TIANXING_AUTO_PENDING_FAMILIES[action], reply_context=reply_context(),
        )
    assert not env.identity["tianxing_observation"]["auto_pending_action"]


def test_pending_mutation_blocks_other_timeline_and_downstream_entrypoints(env):
    pending(env)
    env.identity["tianxing_auto_config"]["timeline_enabled"] = True

    async def run():
        with state_module.use_identity(IDENTITY_ID):
            plan = tianxing.build_tianxing_route_preflight_plan(tianxing.TIANXING_ROUTES[2], now=NOW)
            assert not plan["route_allowed"]
            assert plan["stage"] == "auto_pending"
            result = await tianxing.run_tianxing_timeline_scheduler(NOW)
            assert result["phase"] == "auto_pending"
            result = tianxing.tianxing_route_pre_send_guard(
                tianxing.CMD_EXPLORE_RIFT, send_as_id=IDENTITY_ID,
                intent={"source_module": "\u5929\u661f\u5b97"}, now=NOW,
            )
            assert not result["allowed"]

    asyncio.run(run())
    env.send.assert_not_awaited()


def test_unknown_auto_send_survives_sqlite_reload(env, monkeypatch, tmp_path):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "auto.db"))
    monkeypatch.setattr(tianxing, "save_state", persistence.save_state)
    asyncio.run(execute())
    assert persistence.load_state() is True
    asyncio.run(schedule(NOW + 3600))
    assert persistence.load_state() is True
    observed = state_module.get_identity_state(IDENTITY_ID)["tianxing_observation"]
    assert observed["auto_pending_action"] == "clear_calamity"
    env.send.assert_awaited_once()


def logged_reply(**updates):
    return dict({
        "message_id": 4602, "reply_to_msg_id": 4601, "chat_id": CHAT_ID,
        "sender_id": BOT_ID, "sender_is_bot": True, "event_type": "message",
        "ts_epoch": NOW,
        "text": get_real_message_text(SAMPLES, "tianxing.clear_calamity.basic"),
    }, **updates)


@pytest.mark.parametrize("invalid", [
    {"sender_id": BOT_ID + 1}, {"event_type": "sent"}, {"chat_id": CHAT_ID - 1},
    {"reply_to_msg_id": 4600}, {"ts_epoch": NOW - 500}, {"ts_epoch": NOW + 10},
    {"ts_epoch": float("nan")}, {"message_id": 0},
    {"text": get_real_message_text(SAMPLES, "tianxing.observe.basic")},
])
def test_log_recovery_rejects_untrusted_or_unrelated_evidence(env, invalid):
    observed = pending(env)
    env.lookup.return_value = [logged_reply(**invalid)]
    before = copy.deepcopy(observed)
    with state_module.use_identity(IDENTITY_ID):
        assert not tianxing._recover_tianxing_pending_reply_from_message_log(observed, NOW)
    assert env.identity["tianxing_observation"] == before


def test_log_recovery_closes_exact_result_once(env):
    pending(env)
    env.identity["tianxing_auto_config"]["auto_clear_calamity_enabled"] = False
    env.lookup.return_value = [logged_reply()]
    asyncio.run(schedule(NOW))
    observed = env.identity["tianxing_observation"]
    assert not observed["auto_pending_action"]
    assert observed["calamity_count"] == 1
    env.send.assert_not_awaited()
    env.lookup.reset_mock()
    asyncio.run(schedule(NOW + 1))
    env.lookup.assert_not_called()
    assert env.identity["tianxing_observation"]["calamity_count"] == 1


def test_unmatched_observation_keeps_pending_diagnostic_and_retry_clock(env):
    observed = pending(env)
    observed.update(auto_last_error="unresolved", auto_last_error_at=NOW, auto_next_time=NOW + 1800)
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(get_real_message_text(SAMPLES, "tianxing.observe.basic"), now=NOW)
    observed = env.identity["tianxing_observation"]
    assert observed["auto_last_error"] == "unresolved"
    assert observed["auto_next_time"] == NOW + 1800


def test_successful_send_keeps_dispatch_bound_before_receipt_time(env):
    env.send.return_value = SimpleNamespace(id=4601, chat_id=CHAT_ID, send_started_at=NOW + 1, sent_at=NOW + 30)
    assert asyncio.run(execute())
    observed = env.identity["tianxing_observation"]
    with state_module.use_identity(IDENTITY_ID):
        assert tianxing.apply_tianxing_passive(
            get_real_message_text(SAMPLES, "tianxing.clear_calamity.basic"), now=NOW + 2,
            reply_context=reply_context(),
        )
    assert not env.identity["tianxing_observation"]["auto_pending_action"]
    assert observed["auto_pending_sent_at"] == NOW + 1


def test_empty_panel_title_is_not_a_read_only_completion(env):
    pending(env, "panel")
    with state_module.use_identity(IDENTITY_ID):
        tianxing.apply_tianxing_passive("\u3010\u5929\u673a\u76d8\u3011", now=NOW)
    assert env.identity["tianxing_observation"]["auto_pending_action"] == "panel"


@pytest.mark.parametrize("field", ["\u5f53\u524d\u63a8\u547d", "\u5f53\u524d\u6539\u547d", "\u4eca\u65e5\u5df2\u5b9a\u547d\u661f"])
def test_missing_panel_value_is_not_explicit_absence(env, field):
    observed = pending(env, "panel")
    observed.update(current_prediction=argument("predict"), current_change=argument("change_fate"))
    with state_module.use_identity(IDENTITY_ID):
        tianxing.apply_tianxing_passive(f"\u3010\u5929\u673a\u76d8\u3011\n{field}: ", now=NOW)
    observed = env.identity["tianxing_observation"]
    assert observed["auto_pending_action"] == "panel"
    assert observed["current_prediction"] == argument("predict")
    assert observed["current_change"] == argument("change_fate")


def remember_runtime_receipt(env, op_id, *, msg_id=4601, command=None):
    observed = env.identity["tianxing_observation"]
    env.identity["pending_tasks"][(CHAT_ID, msg_id)] = {
        "cmd": command or observed["auto_pending_command"],
        "source_module": "\u5929\u661f\u5b97", "op_id": op_id,
        "chat_id": CHAT_ID, "send_started_at": NOW, "sent_at": NOW + 1,
        "max_retry": 0,
    }


def test_detached_receipt_is_adopted_only_for_exact_business_operation(env):
    op_id = None

    async def send(_command, **kwargs):
        nonlocal op_id
        op_id = kwargs["op_id"]
        return None

    env.send.side_effect = send
    asyncio.run(execute())
    remember_runtime_receipt(env, op_id)
    env.lookup.return_value = [logged_reply(ts_epoch=NOW + 2)]
    env.identity["tianxing_auto_config"]["auto_clear_calamity_enabled"] = False
    asyncio.run(schedule(NOW + 4000))
    observed = env.identity["tianxing_observation"]
    assert not observed["auto_pending_action"]
    assert observed["calamity_count"] == 1
    env.send.assert_awaited_once()


@pytest.mark.parametrize("case", ["wrong_op", "wrong_command", "wrong_account", "duplicate", "old_dispatch"])
def test_unknown_pending_does_not_adopt_ambiguous_or_foreign_receipt(env, case):
    asyncio.run(execute())
    observed = env.identity["tianxing_observation"]
    op_id = observed["auto_pending_op_id"]
    remember_runtime_receipt(env, op_id)
    record = env.identity["pending_tasks"][(CHAT_ID, 4601)]
    if case == "wrong_op":
        record["op_id"] = "foreign"
    elif case == "wrong_command":
        record["cmd"] = tianxing.CMD_TIANXING_PANEL
    elif case == "wrong_account":
        state_module.set_identity_account(IDENTITY_ID, 7462)
    elif case == "duplicate":
        remember_runtime_receipt(env, op_id, msg_id=4602)
    else:
        record["send_started_at"] = NOW - 100
    env.lookup.return_value = [logged_reply(ts_epoch=NOW + 2)]
    asyncio.run(schedule(NOW + 4000))
    observed = env.identity["tianxing_observation"]
    assert observed["auto_pending_action"] == "clear_calamity"
    assert observed["auto_pending_msg_id"] == 0
    env.send.assert_awaited_once()
    env.lookup.assert_not_called()


@pytest.mark.parametrize("receipt_registered", [False, True])
def test_early_calamity_reply_is_not_applied_twice_during_recovery(env, receipt_registered):
    async def send(_command, **kwargs):
        if receipt_registered:
            remember_runtime_receipt(env, kwargs["op_id"])
        handled = tianxing.apply_tianxing_passive(
            get_real_message_text(SAMPLES, "tianxing.clear_calamity.basic"), now=NOW + 2,
            reply_context=dict(reply_context(), msg_id=4602),
        )
        assert handled is receipt_registered
        return SimpleNamespace(id=4601, chat_id=CHAT_ID, sent_at=NOW + 3, send_started_at=NOW)

    env.send.side_effect = send
    asyncio.run(execute())
    assert env.identity["tianxing_observation"]["calamity_count"] == (1 if receipt_registered else 2)
    env.identity["tianxing_auto_config"]["auto_clear_calamity_enabled"] = False
    env.lookup.return_value = [logged_reply(ts_epoch=NOW + 2)]
    asyncio.run(schedule(NOW + 300))
    observed = env.identity["tianxing_observation"]
    assert not observed["auto_pending_action"]
    assert observed["calamity_count"] == 1
    env.send.assert_awaited_once()


def test_latest_edit_is_not_replaced_with_older_matching_text(env):
    observed = pending(env)
    env.lookup.return_value = [logged_reply(), logged_reply(
        event_type="edit", ts_epoch=NOW + 1,
        text=get_real_message_text(SAMPLES, "tianxing.observe.basic"),
    )]
    with state_module.use_identity(IDENTITY_ID):
        assert not tianxing._recover_tianxing_pending_reply_from_message_log(observed, NOW + 1)
    assert env.identity["tianxing_observation"]["auto_pending_action"] == "clear_calamity"


def test_incomplete_panel_edit_can_later_supply_actual_state(env):
    pending(env, "panel")
    context = dict(reply_context(), msg_id=4602)
    with state_module.use_identity(IDENTITY_ID):
        tianxing.apply_tianxing_passive("\u3010\u5929\u673a\u76d8\u3011", now=NOW, reply_context=context)
        assert tianxing.apply_tianxing_passive(
            get_real_message_text(SAMPLES, "tianxing.panel.basic"), now=NOW + 1,
            reply_context=context,
        )
    observed = env.identity["tianxing_observation"]
    assert not observed["auto_pending_action"]
    assert observed["tianji_value"] == 63


@pytest.mark.parametrize("path", ["routed", "passive"])
@pytest.mark.parametrize("exact", [True, False])
def test_real_dispatcher_passes_pending_reply_ownership(env, monkeypatch, path, exact):
    from model import app, app_runtime, runtime
    from model.features import passive_inbox

    pending(env)
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock())
    monkeypatch.setattr(passive_inbox, "save_state", Mock())
    context = dict(reply_context(), family="tianxing_clear_calamity")
    if not exact:
        context["root_msg_id"] += 1
    context["reply_to_msg_id"] = context["root_msg_id"]
    event = SimpleNamespace(id=4602, chat_id=CHAT_ID, sender_id=BOT_ID)
    text = get_real_message_text(SAMPLES, "tianxing.clear_calamity.basic")
    if path == "routed":
        reply = SimpleNamespace(id=context["root_msg_id"], chat_id=CHAT_ID, raw_text=tianxing.CMD_TIANXING_CLEAR_CALAMITY)
        handled = asyncio.run(app._handle_routed_reply_event(event, text, NOW, reply, context))
    else:
        handled = asyncio.run(passive_inbox.handle_passive_module_card(text, NOW, context, event, event_type="message"))
    assert handled is exact
    assert bool(env.identity["tianxing_observation"]["auto_pending_action"]) is not exact
    env.send.assert_not_awaited()


def test_existing_runtime_receipt_contract_recovers_detached_auto_send(env, monkeypatch):
    from model import runtime

    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(runtime, "_GAME_SEND_BLOCK_LAST", {})

    async def send(command, **kwargs):
        receipt = runtime._finalize_game_command_sent(
            command, msg_id=4601, sent_at=NOW + 1, send_started_at=NOW,
            send_as_id=IDENTITY_ID, game_group_id=CHAT_ID, topic_id=0,
            send_intent={"source_module": kwargs["source_module"], "op_id": kwargs["op_id"]},
            max_retry=0, append_sent_log=False,
        )
        assert receipt.id == 4601
        return None

    env.send.side_effect = send
    asyncio.run(execute())
    env.lookup.return_value = [logged_reply(ts_epoch=NOW + 2)]
    env.identity["tianxing_auto_config"]["auto_clear_calamity_enabled"] = False
    asyncio.run(schedule(NOW + 4000))
    observed = env.identity["tianxing_observation"]
    assert not observed["auto_pending_action"]
    assert observed["calamity_count"] == 1
    env.send.assert_awaited_once()


def test_early_result_deduplication_survives_sqlite_reload(env, monkeypatch, tmp_path):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "early-auto.db"))
    monkeypatch.setattr(tianxing, "save_state", persistence.save_state)

    async def send(*_args, **_kwargs):
        tianxing.apply_tianxing_passive(
            get_real_message_text(SAMPLES, "tianxing.clear_calamity.basic"), now=NOW + 2,
            reply_context=dict(reply_context(), msg_id=4602),
        )
        raise asyncio.CancelledError

    env.send.side_effect = send
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execute())
    assert persistence.load_state() is True
    env.identity = state_module.get_identity_state(IDENTITY_ID)
    op_id = env.identity["tianxing_observation"]["auto_pending_op_id"]
    remember_runtime_receipt(env, op_id)
    env.lookup.return_value = [logged_reply(ts_epoch=NOW + 2)]
    env.identity["tianxing_auto_config"]["auto_clear_calamity_enabled"] = False
    asyncio.run(schedule(NOW + 4000))
    assert persistence.load_state() is True
    observed = state_module.get_identity_state(IDENTITY_ID)["tianxing_observation"]
    assert observed["calamity_count"] == 1
    assert not observed["auto_pending_action"]
    env.send.assert_awaited_once()


@pytest.mark.parametrize("field,value", [("auto_pending_op_id", "replacement"), ("auto_pending_account_id", 7462)])
def test_receipt_does_not_replace_changed_pending_owner_metadata(env, field, value):
    expected = None

    async def send(*_args, **_kwargs):
        nonlocal expected
        env.identity["tianxing_observation"][field] = value
        expected = copy.deepcopy(env.identity["tianxing_observation"])
        return SimpleNamespace(id=4601, chat_id=CHAT_ID, sent_at=NOW)

    env.send.side_effect = send
    asyncio.run(execute())
    assert env.identity["tianxing_observation"] == expected


def test_pending_reply_capacity_never_evicts_applied_delta_evidence(env):
    observed = pending(env, msg_id=0)
    observed["auto_pending_seen_replies"] = [f"fixture-{index}" for index in range(tianxing.TIANXING_AUTO_PENDING_REPLY_LIMIT)]
    before = copy.deepcopy(observed)
    with state_module.use_identity(IDENTITY_ID):
        assert not tianxing.apply_tianxing_passive(
            get_real_message_text(SAMPLES, "tianxing.clear_calamity.basic"), now=NOW,
            reply_context=dict(reply_context(), msg_id=4602),
        )
    assert env.identity["tianxing_observation"] == before


def test_pending_recovery_never_guesses_primary_chat_without_receipt(env, monkeypatch):
    from model import runtime

    observed = pending(env)
    observed["auto_pending_chat_id"] = 0
    monkeypatch.setattr(runtime, "_recent_sent_message_log_paths", lambda: [])
    monkeypatch.setattr(tianxing, "get_sent_message_chat_id", runtime.get_sent_message_chat_id)
    with state_module.use_identity(IDENTITY_ID):
        assert not tianxing._recover_tianxing_pending_reply_from_message_log(observed, NOW)
    env.lookup.assert_not_called()
