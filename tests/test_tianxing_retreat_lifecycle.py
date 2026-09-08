import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import tianxing
from model.real_message_replay import get_real_message_text


IDENTITY_ID = 990480001
ACCOUNT_ID = 7481
CHAT_ID = -100480001
NOW = 1700000000.0
ROUTE = tianxing.TIANXING_ROUTES[0]
MODES = ("retreat", "use", "exchange", "donate", "force_exit")
COMMANDS = {
    "retreat": tianxing.CMD_NORMAL_RETREAT,
    "use": tianxing.CMD_USE_HEQI_DAN,
    "exchange": tianxing.CMD_EXCHANGE_HEQI_DAN_PREFIX + "10",
    "donate": tianxing.CMD_SECT_DONATE_LINGSHI_PREFIX + "200",
    "force_exit": tianxing.CMD_DEEP_RETREAT_FORCE_EXIT,
}
SAMPLES = Path(__file__).parent / "fixtures" / "real_message_samples.json"
EXCHANGE_REPLY = "\u5151\u6362\u6210\u529f\uff01\n\u4f60\u6d88\u8017\u4e86 1500 \u70b9\u8d21\u732e\uff0c\u83b7\u5f97\u4e86\u3010\u5408\u6c14\u4e39\u3011x10\uff0c\u5df2\u653e\u5165\u4f60\u7684\u50a8\u7269\u888b\u3002"
DONATE_REPLY = "\u4f60\u5411\u5b97\u95e8\u6350\u732e\u4e86 \u3010\u7075\u77f3\u3011x200\uff0c\u83b7\u5f97\u4e86 1400 \u70b9\u5b97\u95e8\u8d21\u732e\uff01"
FORCE_REPLY = "\u3010\u6df1\u5ea6\u95ed\u5173\u603b\u7ed3\u3011\n\u56e0\u5f3a\u884c\u51fa\u5173\uff0c\u9700\u8c03\u606f40\u5206\u949f\u65b9\u53ef\u8fdb\u884c\u4e0b\u4e00\u6b21\u3010\u95ed\u5173\u4fee\u70bc\u3011\u3002"


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
        tianxing_auto_config={
            "timeline_enabled": False,
            "retreat_farm_enabled": True,
            "retreat_farm_dry_run_enabled": False,
            "retreat_farm_allow_heqi_dan": True,
            "retreat_farm_auto_exchange_heqi_dan": True,
            "retreat_farm_auto_donate_lingshi": True,
            "retreat_farm_allow_force_exit": True,
            "farm_route": ROUTE,
            "farm_window_enabled": True,
            "farm_windows_text": "00:00-23:59",
            "target_tianji_daily": 42,
        },
        tianxing_observation={
            "last_observed_at": NOW - 10,
            "fixed_star": tianxing.TIANXING_STARS[2],
            "fixed_star_day": tianxing.get_day_key(NOW),
            "current_prediction": ROUTE,
            "current_prediction_until": NOW + 3600,
            "current_prediction_set_at": NOW - 20,
            "tianji_value": 12,
        },
        tianxing_timeline_state={
            "released_routes": {ROUTE: {"released_at": NOW - 5, "basis": "prediction", "plan_id": "fixture"}},
        },
    )
    send = AsyncMock(return_value=receipt())
    save = Mock()
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(tianxing.time, "monotonic", lambda: clock.value)
    monkeypatch.setattr(tianxing, "send_game_command", send)
    monkeypatch.setattr(tianxing, "save_state", save)
    monkeypatch.setattr(tianxing, "get_sent_message_chat_id", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {})
    monkeypatch.setattr(tianxing, "_tianxing_action_guard_wait", lambda *_args: (0, ""))
    monkeypatch.setattr(tianxing, "get_phaseful_summary_risk_reason", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(tianxing, "_TIANXING_RETREAT_LOCKS", {}, raising=False)
    monkeypatch.setattr(tianxing, "_TIANXING_TIMELINE_LOCKS", {})
    yield SimpleNamespace(identity=identity, send=send, save=save, clock=clock, parent_current=True)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def receipt(**overrides):
    return SimpleNamespace(**dict(dict(id=4801, chat_id=CHAT_ID, sent_at=NOW, send_started_at=NOW), **overrides))


def farm(env):
    return tianxing.normalize_tianxing_timeline_state(env.identity.get("tianxing_timeline_state"))["retreat_farm"]


def seed(env, mode="retreat", **values):
    current = farm(env)
    current.update(started_at=NOW - 100, target_tianji=42)
    if mode in {"use", "exchange", "donate"}:
        current["phase"] = {"use": "cooldown", "exchange": "need_heqi_exchange", "donate": "need_lingshi_donation"}[mode]
        current["cooldown_until"] = NOW + 600
        current["next_time"] = NOW + 600 if mode == "use" else NOW
    if mode == "force_exit":
        env.identity["deep_retreat_phase"] = "running"
    current.update(values)
    env.identity["tianxing_timeline_state"]["retreat_farm"] = current
    return current


async def run(env, now=NOW, **kwargs):
    with state_module.use_identity(IDENTITY_ID):
        return await tianxing.run_tianxing_retreat_farm_scheduler(
            now, deep_retreat_phase=env.identity.get("deep_retreat_phase", ""),
            operation_check=lambda: env.parent_current, **kwargs,
        )


def context(root=4801, **overrides):
    return dict(dict(chat_id=CHAT_ID, root_msg_id=root, msg_id=root + 1, send_as_id=IDENTITY_ID), **overrides)


def reply(mode):
    if mode == "retreat":
        return get_real_message_text(SAMPLES, "tianxing.retreat.success")
    if mode == "use":
        return get_real_message_text(SAMPLES, "tianxing.retreat_farm.heqi_dan_success")
    return {"exchange": EXCHANGE_REPLY, "donate": DONATE_REPLY, "force_exit": FORCE_REPLY}[mode]


def apply(env, mode, now=NOW + 1, ctx=None, text=None):
    with state_module.use_identity(IDENTITY_ID):
        if mode == "force_exit":
            return tianxing.note_tianxing_retreat_force_exit_summary(
                text or reply(mode), now=now, reply_context=context() if ctx is None else ctx,
            )
        return tianxing.apply_tianxing_passive(
            text or reply(mode), now=now, family="tianxing_retreat_farm",
            reply_context=context() if ctx is None else ctx,
        )


def invalidate(env, change):
    if change == "removed":
        state_module.remove_identity(IDENTITY_ID)
    elif change == "replaced":
        state_module.remove_identity(IDENTITY_ID)
        state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID)
    elif change == "rebound":
        state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID + 1)
    elif change == "module_disabled":
        env.identity["tianxing_enabled"] = False
    elif change == "identity_disabled":
        state_module.set_identity_enabled(IDENTITY_ID, False)
    elif change == "global_disabled":
        state_module.set_global_enabled(False)
    elif change == "paused":
        env.identity["tianxing_observation"]["automation_paused_until"] = -1
    elif change == "config":
        env.identity["tianxing_auto_config"]["retreat_farm_enabled"] = False
    elif change == "parent":
        env.parent_current = False
    elif change == "new_farm":
        seed(env, phase="ready", last_op_id="replacement", next_time=NOW + 1200)
    elif change == "new_clock":
        env.identity["tianxing_timeline_state"]["retreat_farm"]["next_time"] = NOW + 7200
    elif change == "prediction":
        env.identity["tianxing_observation"]["current_prediction"] = tianxing.TIANXING_ROUTES[2]
    elif change == "expiry":
        env.clock.value += 7200
    elif change == "deep_phase":
        env.identity["deep_retreat_phase"] = "running"
    else:
        raise AssertionError(change)


@pytest.mark.parametrize("mode", MODES)
def test_retreat_claim_precedes_dispatch_and_receipt_is_scoped(env, mode):
    seed(env, mode)

    async def send(command, **kwargs):
        current = farm(env)
        assert current["phase"] == "sending"
        assert current["pending_command"]["op_id"] == kwargs["op_id"]
        assert current["pending_command"]["command"] == command == COMMANDS[mode]
        assert env.save.call_count > 0
        assert kwargs["operation_check"]() is True
        return receipt()

    env.send.side_effect = send
    assert asyncio.run(run(env))["stage"] == "sent_waiting_reply"
    assert farm(env)["pending_command"]["msg_id"] == 4801
    assert farm(env)["last_chat_id"] == CHAT_ID


@pytest.mark.parametrize("mode", MODES)
def test_retreat_wait_does_not_erase_pending_phase(env, mode):
    seed(env, mode)
    asyncio.run(run(env))
    original = farm(env)
    asyncio.run(run(env, NOW + 1))
    asyncio.run(run(env, NOW + 2))
    env.send.assert_awaited_once()
    assert farm(env) == original


@pytest.mark.parametrize("mode", MODES)
def test_retreat_entry_is_serial(env, mode):
    seed(env, mode)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def send(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return receipt()

        env.send.side_effect = send
        first = asyncio.create_task(run(env))
        await entered.wait()
        second = asyncio.create_task(run(env))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())
    env.send.assert_awaited_once()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("outcome", ["none", "timeout", "exception", "cancel", "bad_id"])
def test_retreat_unknown_outcome_never_rearms_mutation(env, monkeypatch, mode, outcome):
    seed(env, mode)
    env.send.return_value = receipt(id=True) if outcome == "bad_id" else None
    if outcome == "timeout":
        monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": "send_timeout"})
    if outcome == "exception":
        env.send.side_effect = RuntimeError("uncertain dispatch")
    if outcome == "cancel":
        env.send.side_effect = asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run(env))
    else:
        asyncio.run(run(env))
    original = farm(env)
    assert original.get("pending_command")
    assert original["pending_command"]["status"] in {"sending", "unknown"}
    env.send.side_effect = None
    env.send.return_value = receipt(id=4810)
    asyncio.run(run(env, NOW + 3601))
    asyncio.run(run(env, NOW + 7201))
    env.send.assert_awaited_once()
    assert farm(env) == original


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module_disabled", "identity_disabled", "global_disabled", "paused", "config", "parent", "new_farm", "new_clock"])
def test_retreat_queued_command_revalidates_owner(env, mode, change):
    seed(env, mode)

    async def send(_command, **kwargs):
        assert kwargs["operation_check"]() is True
        invalidate(env, change)
        assert kwargs["operation_check"]() is False
        return None

    env.send.side_effect = send
    asyncio.run(run(env))


@pytest.mark.parametrize("change", ["prediction", "expiry", "deep_phase"])
def test_normal_retreat_rechecks_business_admission_at_dispatch(env, change):
    seed(env)

    async def send(_command, **kwargs):
        assert kwargs["operation_check"]() is True
        invalidate(env, change)
        assert kwargs["operation_check"]() is False
        return None

    env.send.side_effect = send
    asyncio.run(run(env))


@pytest.mark.parametrize("mode", MODES)
def test_retreat_save_failure_prevents_dispatch(env, mode):
    seed(env, mode)
    env.save.return_value = False
    asyncio.run(run(env))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("change", ["module_disabled", "config", "new_clock"])
def test_retreat_late_receipt_preserves_fact_without_rewriting_controls(env, mode, change):
    seed(env, mode)

    async def send(_command, **_kwargs):
        invalidate(env, change)
        return receipt()

    env.send.side_effect = send
    asyncio.run(run(env))
    assert farm(env)["last_msg_id"] == 4801
    assert farm(env)["last_chat_id"] == CHAT_ID
    if change == "new_clock":
        assert farm(env)["next_time"] == NOW + 7200


@pytest.mark.parametrize("mode", MODES)
def test_retreat_early_result_is_not_lost_or_reopened(env, mode):
    seed(env, mode)

    async def send(command, **kwargs):
        env.identity["pending_tasks"][(CHAT_ID, 4801)] = {
            "chat_id": CHAT_ID, "op_id": kwargs["op_id"], "cmd": command,
            "source_module": kwargs["source_module"], "sent_at": NOW, "send_started_at": NOW,
        }
        assert apply(env, mode)
        return receipt()

    env.send.side_effect = send
    asyncio.run(run(env))
    assert farm(env)["last_msg_id"] == 4801
    assert farm(env)["last_chat_id"] == CHAT_ID
    assert not farm(env)["pending_command"]
    assert farm(env)["last_command_result"]["msg_id"] == 4801
    assert farm(env)["send_outcome"] == "resolved"


@pytest.mark.parametrize("mode", MODES)
def test_retreat_panel_cannot_complete_original_command(env, mode):
    seed(env, mode)
    asyncio.run(run(env))
    original = copy.deepcopy(farm(env)["pending_command"])
    env.send.return_value = receipt(id=4803, sent_at=NOW + 91, send_started_at=NOW + 91)
    asyncio.run(run(env, NOW + 91))
    assert env.send.await_args.args[0] == tianxing.CMD_TIANXING_PANEL
    with state_module.use_identity(IDENTITY_ID):
        tianxing.apply_tianxing_passive(
            get_real_message_text(SAMPLES, "tianxing.panel.basic"), now=NOW + 92,
            family="tianxing_panel", reply_context=context(4803),
        )
    assert farm(env)["pending_command"] == original
    assert apply(env, mode, NOW + 93)
    assert not farm(env)["pending_command"]
    assert farm(env)["last_msg_id"] == 4803
    assert farm(env)["last_command_result"]["msg_id"] == 4801


@pytest.mark.parametrize("mode", MODES)
def test_retreat_result_is_idempotent(env, mode):
    seed(env, mode)
    asyncio.run(run(env))
    assert apply(env, mode)
    expected = copy.deepcopy(env.identity)
    assert apply(env, mode, NOW + 3)
    assert env.identity == expected


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("mismatch", ["root", "chat", "owner", "account", "time"])
def test_retreat_result_requires_original_scope(env, mode, mismatch):
    seed(env, mode)
    asyncio.run(run(env))
    ctx = context()
    if mismatch == "root":
        ctx["root_msg_id"] += 10
    elif mismatch == "chat":
        ctx["chat_id"] -= 1
    elif mismatch == "owner":
        ctx["send_as_id"] += 1
    elif mismatch == "account":
        state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID + 1)
    expected = farm(env)
    apply(env, mode, NOW - 1 if mismatch == "time" else NOW + 1, ctx)
    assert farm(env) == expected


def test_retreat_missing_potion_does_not_loop_without_exchange(env):
    seed(env, "use")
    env.identity["tianxing_auto_config"]["retreat_farm_auto_exchange_heqi_dan"] = False
    asyncio.run(run(env))
    apply(env, "use", text="\u4f60\u7684\u50a8\u7269\u888b\u4e2d\u6ca1\u6709\u540d\u4e3a\u3010\u5408\u6c14\u4e39\u3011\u7684\u53ef\u7528\u7269\u54c1\u3002")
    original = farm(env)
    asyncio.run(run(env, NOW + 2))
    env.send.assert_awaited_once()
    assert farm(env) == original


@pytest.mark.parametrize("mode", MODES)
def test_unknown_retreat_survives_sqlite_reload(env, monkeypatch, tmp_path, mode):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "retreat.db"))
    monkeypatch.setattr(tianxing, "save_state", persistence.save_state)
    seed(env, mode)
    env.send.return_value = None
    asyncio.run(run(env))
    original = farm(env)["pending_command"]
    assert persistence.load_state() is True
    env.identity = state_module.get_identity_state(IDENTITY_ID)
    asyncio.run(run(env, NOW + 86400))
    assert farm(env)["pending_command"] == original
    assert farm(env)["phase"] == "send_unknown"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("code", ["send_queue_timeout", "pre_send_guard", "account_offline"])
def test_retreat_explicit_unsent_preserves_bounded_retry(env, monkeypatch, mode, code):
    seed(env, mode)
    env.send.return_value = None
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": code})
    asyncio.run(run(env))
    blocked = farm(env)
    assert blocked["phase"] == "send_blocked"
    assert not blocked["pending_command"]
    assert blocked["next_time"] > NOW
    asyncio.run(run(env, blocked["next_time"] - 1))
    assert farm(env) == blocked
    env.send.assert_awaited_once()
    env.send.return_value = receipt(id=4805, sent_at=blocked["next_time"], send_started_at=blocked["next_time"])
    asyncio.run(run(env, blocked["next_time"]))
    assert env.send.await_count == 2
    assert env.send.await_args.args[0] == COMMANDS[mode]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("finish", ["ok", "error", "removed", "replaced", "rebound"])
def test_force_exit_actual_broadcast_keeps_farm_fact_before_await(env, monkeypatch, enabled, finish):
    from model.features import deep_retreat

    seed(env, "force_exit")
    asyncio.run(run(env))
    env.identity["deep_retreat_enabled"] = enabled
    env.identity["tianxing_enabled"] = False
    original = env.identity
    text = FORCE_REPLY + "\n\u672c\u6b21\u7ed3\u7b97\u65f6\u957f 1\u5206\u949f\n\u795e\u9b42\u5410\u7eb3\u6b21\u6570 1"

    async def finish_summary(*_args, **_kwargs):
        assert not farm(env)["pending_command"]
        if finish == "error":
            raise RuntimeError("notification failed")
        if finish in {"removed", "replaced", "rebound"}:
            invalidate(env, finish)

    finalizer = AsyncMock(side_effect=finish_summary)
    clear = Mock()
    monkeypatch.setattr(deep_retreat, "finalize_summary_broadcast", finalizer)
    monkeypatch.setattr(deep_retreat, "_clear_deep_retreat_remote_block_after_summary", clear)
    monkeypatch.setattr(deep_retreat, "_record_deep_retreat_event", Mock())
    monkeypatch.setattr(deep_retreat, "save_state", env.save)
    work = deep_retreat.handle_deep_retreat_summary_broadcast(
        text, NOW + 1, reply_context=context(), event=SimpleNamespace(chat_id=CHAT_ID),
    )
    if finish == "error" and enabled:
        with pytest.raises(RuntimeError, match="notification failed"):
            asyncio.run(work)
    else:
        assert asyncio.run(work) is True
    assert not original["tianxing_timeline_state"]["retreat_farm"]["pending_command"]
    assert original["tianxing_timeline_state"]["retreat_farm"]["last_command_result"]["msg_id"] == 4801
    if not enabled:
        finalizer.assert_not_awaited()
    if not enabled or finish != "ok":
        clear.assert_not_called()


@pytest.mark.parametrize("mode", ["exchange", "donate"])
def test_retreat_quantity_mismatch_cannot_advance_chain(env, mode):
    seed(env, mode)
    asyncio.run(run(env))
    expected = farm(env)
    text = reply(mode).replace("x10", "x1").replace("x200", "x20")
    apply(env, mode, text=text)
    assert farm(env) == expected


@pytest.mark.parametrize("route", tianxing.TIANXING_ROUTES)
def test_pending_normal_retreat_blocks_other_route_consumption(env, route):
    seed(env)
    env.send.return_value = None
    asyncio.run(run(env))
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_route_preflight_plan(route, now=NOW + 1)
    assert not plan["route_allowed"]
    assert plan["stage"] == "retreat_pending"


@pytest.mark.parametrize("mode", ["use", "exchange", "donate", "force_exit"])
def test_material_or_deep_retreat_pending_does_not_consume_tianxing(env, mode):
    seed(env, mode)
    env.send.return_value = None
    asyncio.run(run(env))
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_route_preflight_plan(ROUTE, now=NOW + 1)
    assert plan["route_allowed"]


@pytest.mark.parametrize("mode", ["use", "exchange", "donate"])
def test_retreat_local_guard_preserves_next_chain_action(env, monkeypatch, mode):
    seed(env, mode)
    monkeypatch.setattr(tianxing, "_tianxing_action_guard_wait", lambda *_args: (NOW + 20, "guard"))
    asyncio.run(run(env))
    env.send.assert_not_awaited()
    monkeypatch.setattr(tianxing, "_tianxing_action_guard_wait", lambda *_args: (0, ""))
    env.send.return_value = receipt(sent_at=NOW + 21, send_started_at=NOW + 21)
    asyncio.run(run(env, NOW + 21))
    env.send.assert_awaited_once()
    assert env.send.await_args.args[0] == COMMANDS[mode]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("newer_panel", [False, True])
def test_retreat_early_panel_respects_original_result_order(env, mode, newer_panel):
    seed(env, mode)
    asyncio.run(run(env))
    expected = None
    expected_observed = None

    async def send(command, **kwargs):
        nonlocal expected, expected_observed
        assert command == tianxing.CMD_TIANXING_PANEL
        assert apply(env, mode, NOW + 92)
        expected = farm(env)
        expected_observed = copy.deepcopy(env.identity["tianxing_observation"])
        dispatch_at = NOW + (93 if newer_panel else 91)
        env.identity["pending_tasks"][(CHAT_ID, 4803)] = {
            "chat_id": CHAT_ID, "op_id": kwargs["op_id"], "cmd": command,
            "source_module": kwargs["source_module"], "sent_at": dispatch_at, "send_started_at": dispatch_at,
        }
        tianxing.apply_tianxing_passive(
            get_real_message_text(SAMPLES, "tianxing.panel.basic"), now=NOW + 94,
            family="tianxing_panel", reply_context=context(4803),
        )
        env.clock.value += 4
        return receipt(id=4803, sent_at=dispatch_at, send_started_at=dispatch_at)

    env.send.side_effect = send
    asyncio.run(run(env, NOW + 91))
    if not newer_panel:
        assert env.identity["tianxing_observation"] == expected_observed
        for key in ("phase", "next_time", "last_result", "last_error", "cooldown_until", "last_command_result"):
            assert farm(env)[key] == expected[key]
    else:
        assert env.identity["tianxing_observation"]["last_action"] == "\u5929\u673a\u76d8"
    assert not farm(env)["pending_command"]
    assert farm(env)["last_msg_id"] == 4803


@pytest.mark.parametrize("mode", MODES)
def test_retreat_exact_detached_runtime_receipt_resolves_pending(env, mode):
    seed(env, mode)
    env.send.return_value = None
    asyncio.run(run(env))
    env.identity["pending_tasks"][(CHAT_ID, 4801)] = {
        "chat_id": CHAT_ID, "op_id": env.send.await_args.kwargs["op_id"], "cmd": COMMANDS[mode],
        "source_module": env.send.await_args.kwargs["source_module"], "sent_at": NOW, "send_started_at": NOW,
    }
    assert apply(env, mode)
    assert not farm(env)["pending_command"]
    assert farm(env)["last_command_result"]["msg_id"] == 4801


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "config", "parent", "deep_phase"])
def test_retreat_lock_wait_revalidates_captured_owner(env, change):
    async def scenario():
        lock = asyncio.Lock()
        tianxing._TIANXING_RETREAT_LOCKS[IDENTITY_ID] = lock
        await lock.acquire()
        pending = asyncio.create_task(run(env))
        await asyncio.sleep(0)
        invalidate(env, change)
        lock.release()
        return await pending

    assert asyncio.run(scenario())["stage"] == "cancelled"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("mode", ["use", "exchange", "donate"])
def test_expired_retreat_cd_no_longer_needs_materials(env, mode):
    seed(env, mode)
    env.send.return_value = receipt(sent_at=NOW + 601, send_started_at=NOW + 601)
    result = asyncio.run(run(env, NOW + 601))
    assert result["stage"] == "sent_waiting_reply"
    env.send.assert_awaited_once()
    assert env.send.await_args.args[0] == tianxing.CMD_NORMAL_RETREAT
