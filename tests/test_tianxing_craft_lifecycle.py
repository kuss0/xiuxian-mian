import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import tianxing
from model.real_message_replay import get_real_message_text


IDENTITY_ID = 990470001
ACCOUNT_ID = 7471
CHAT_ID = -100470001
NOW = 1700000000.0
CRAFT_ROUTE = tianxing.TIANXING_ROUTES[1]
ITEM = "\u7384\u94c1\u5251"
COMMAND = f"{tianxing.CMD_CRAFT} {ITEM}"
KINDS = ("consume", "farm")
SAMPLES = Path(__file__).parent / "fixtures" / "real_message_samples.json"


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
            "craft_farm_enabled": True,
            "craft_farm_dry_run_enabled": False,
            "craft_farm_item": ITEM,
            "farm_route": CRAFT_ROUTE,
            "target_tianji_daily": 42,
        },
        tianxing_observation={
            "last_observed_at": NOW - 10,
            "fixed_star": tianxing.TIANXING_STARS[2],
            "fixed_star_day": tianxing.get_day_key(NOW),
            "current_prediction": CRAFT_ROUTE,
            "current_prediction_until": NOW + 3600,
            "current_prediction_set_at": NOW - 20,
            "tianji_value": 12,
        },
        tianxing_timeline_state={
            "released_routes": {
                CRAFT_ROUTE: {"released_at": NOW - 5, "basis": "prediction", "plan_id": "fixture"},
            },
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
    monkeypatch.setattr(tianxing, "_TIANXING_CRAFT_LOCKS", {}, raising=False)
    monkeypatch.setattr(tianxing, "_TIANXING_TIMELINE_LOCKS", {})
    yield SimpleNamespace(identity=identity, send=send, save=save, clock=clock, parent_current=True)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def receipt(**overrides):
    return SimpleNamespace(**dict(dict(id=4701, chat_id=CHAT_ID, sent_at=NOW, send_started_at=NOW), **overrides))


def farm(env):
    return tianxing.normalize_tianxing_timeline_state(env.identity.get("tianxing_timeline_state"))["craft_farm"]


def seed_farm(env, **values):
    current = farm(env)
    current.update(started_at=NOW - 100, daily_day=tianxing.get_day_key(NOW))
    current.update(values)
    env.identity["tianxing_timeline_state"]["craft_farm"] = current
    return current


async def run(kind="consume", now=NOW, **kwargs):
    scheduler = tianxing.run_tianxing_consume_craft_prediction if kind == "consume" else tianxing.run_tianxing_craft_farm_scheduler
    with state_module.use_identity(IDENTITY_ID):
        return await scheduler(now, **kwargs)


def invalidate(env, change):
    if change == "removed":
        state_module.remove_identity(IDENTITY_ID)
    elif change == "replaced":
        state_module.remove_identity(IDENTITY_ID)
        state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID)
        state_module.get_identity_state(IDENTITY_ID)["tianxing_enabled"] = True
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
        env.identity["tianxing_auto_config"]["craft_farm_item"] = "replacement"
    elif change == "parent":
        env.parent_current = False
    elif change == "new_farm":
        seed_farm(env, phase="calibrating", last_msg_id=4799, last_op_id="replacement", next_time=NOW + 600)
    elif change == "new_clock":
        env.identity["tianxing_timeline_state"]["craft_farm"]["next_time"] = NOW + 7200
    elif change == "prediction_changed":
        env.identity["tianxing_observation"]["current_prediction"] = tianxing.TIANXING_ROUTES[2]
    elif change == "prediction_expired":
        env.clock.value += 7200
    elif change == "auto_pending":
        env.identity["tianxing_observation"].update(
            auto_pending_action="predict", auto_pending_command="fixture-predict",
            auto_pending_sent_at=NOW, auto_pending_due_at=NOW + 600,
        )
    else:
        raise AssertionError(change)


@pytest.mark.parametrize("kind", KINDS)
def test_craft_normal_send(env, kind):
    result = asyncio.run(run(kind))
    assert result["stage"] == "sent_waiting_reply"
    env.send.assert_awaited_once()
    assert env.send.await_args.args[0] == COMMAND
    assert farm(env)["last_msg_id"] == 4701
    assert farm(env).get("last_chat_id") == CHAT_ID


@pytest.mark.parametrize("kind", KINDS)
def test_craft_claim_is_saved_before_transport(env, kind):
    async def send(command, **kwargs):
        pending = farm(env)
        assert pending["last_command"] == command
        assert pending["phase"] in {"sending", "sent_waiting_reply"}
        assert pending["last_msg_id"] == 0
        assert pending.get("last_op_id") == kwargs["op_id"]
        assert pending.get("last_account_id") == ACCOUNT_ID
        assert pending.get("last_send_started_at") == NOW
        assert env.save.call_count > 0
        assert kwargs["operation_check"]() is True
        return receipt()

    env.send.side_effect = send
    assert asyncio.run(run(kind))["stage"] == "sent_waiting_reply"


@pytest.mark.parametrize("kind", KINDS)
def test_craft_save_failure_prevents_transport(env, kind):
    env.save.return_value = False
    asyncio.run(run(kind))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", [
    "removed", "replaced", "rebound", "module_disabled", "identity_disabled",
    "global_disabled", "paused", "config", "new_farm", "new_clock",
    "prediction_changed", "prediction_expired", "auto_pending",
])
def test_craft_queue_rechecks_current_operation(env, kind, change):
    dispatched = []

    async def send(command, **kwargs):
        invalidate(env, change)
        check = kwargs.get("operation_check")
        if check is None or check() is True:
            dispatched.append(command)
            return receipt()
        return None

    env.send.side_effect = send
    asyncio.run(run(kind))
    assert dispatched == []


@pytest.mark.parametrize("kind", KINDS)
def test_craft_parent_cancellation_reaches_queue(env, kind):
    dispatched = []

    async def send(command, **kwargs):
        env.parent_current = False
        if kwargs["operation_check"]():
            dispatched.append(command)
        return None

    env.send.side_effect = send
    asyncio.run(run(kind, operation_check=lambda: env.parent_current))
    assert dispatched == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "new_farm"])
@pytest.mark.parametrize("outcome", ["receipt", "unknown", "cancelled"])
def test_craft_receipt_does_not_overwrite_replaced_work(env, kind, change, outcome):
    expected = None

    async def send(*_args, **_kwargs):
        nonlocal expected
        invalidate(env, change)
        expected = copy.deepcopy(state_module._meta_state)
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return receipt() if outcome == "receipt" else None

    env.send.side_effect = send
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run(kind))
    else:
        asyncio.run(run(kind))
    assert state_module._meta_state == expected


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["module_disabled", "identity_disabled", "global_disabled", "paused", "config", "new_clock"])
def test_craft_keeps_sent_receipt_after_controls_or_clock_change(env, kind, change):
    async def send(*_args, **_kwargs):
        invalidate(env, change)
        return receipt()

    env.send.side_effect = send
    asyncio.run(run(kind))
    current = farm(env)
    assert current["last_msg_id"] == 4701
    assert current.get("last_chat_id") == CHAT_ID
    if change == "new_clock":
        assert current["next_time"] == NOW + 7200


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("outcome", ["unknown", "exception", "cancelled", "invalid_id"])
def test_craft_uncertain_send_remains_pending_without_retry(env, monkeypatch, kind, outcome):
    env.send.return_value = None
    if outcome == "exception":
        env.send.side_effect = RuntimeError("fixture")
    elif outcome == "cancelled":
        env.send.side_effect = asyncio.CancelledError
    elif outcome == "invalid_id":
        env.send.return_value = receipt(id=True)
    if outcome != "unknown":
        monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": "global_disabled"})
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run(kind))
    else:
        asyncio.run(run(kind))
    original = farm(env)
    assert original["phase"] == "send_unknown"
    assert original["last_command"] == COMMAND
    assert original["last_msg_id"] == 0
    assert original.get("last_op_id")
    env.send.side_effect = None
    env.send.return_value = receipt()
    for delta in (1, 600, 4000, 86400):
        asyncio.run(run(kind, NOW + delta))
        assert farm(env)["last_op_id"] == original["last_op_id"]
        assert farm(env)["phase"] == "send_unknown"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("block", [{"code": "send_queue_timeout"}, {"code": "action_guard"}])
def test_craft_definitely_unsent_retains_bounded_retry(env, monkeypatch, kind, block):
    env.send.return_value = None
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: block)
    result = asyncio.run(run(kind))
    current = farm(env)
    assert result["stage"] == "send_blocked"
    assert current["next_time"] > NOW
    if block["code"] == "send_queue_timeout":
        assert NOW + 600 <= current["next_time"] <= NOW + 1200
    asyncio.run(run(kind, NOW + 1))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("first,second", [("consume", "consume"), ("consume", "farm"), ("farm", "consume"), ("farm", "farm")])
def test_craft_entrypoints_share_one_inflight_operation(env, first, second):
    async def execute():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def send(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return receipt()

        env.send.side_effect = send
        task = asyncio.create_task(run(first))
        await entered.wait()
        other = asyncio.create_task(run(second))
        await asyncio.sleep(0)
        try:
            env.send.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(task, other)
        env.send.assert_awaited_once()
        assert farm(env)["last_command"] == COMMAND

    asyncio.run(execute())


@pytest.mark.parametrize("kind", KINDS)
def test_craft_future_ready_clock_is_respected(env, kind):
    seed_farm(env, phase="ready", next_time=NOW + 600, last_result="action_guard_waiting")
    asyncio.run(run(kind))
    env.send.assert_not_awaited()
    assert farm(env)["next_time"] == NOW + 600


@pytest.mark.parametrize("kind", KINDS)
def test_calibration_guard_never_rearms_craft(env, monkeypatch, kind):
    seed_farm(env, phase="sent_waiting_reply", next_time=NOW - 1, last_msg_id=4699, last_command=COMMAND, last_action="consume_craft_prediction")
    monkeypatch.setattr(tianxing, "_tianxing_action_guard_wait", lambda *_args: (NOW + 100, "fixture guard"))
    asyncio.run(run(kind))
    current = farm(env)
    assert current["phase"] != "ready"
    assert current["last_msg_id"] == 4699
    monkeypatch.setattr(tianxing, "_tianxing_action_guard_wait", lambda *_args: (0, ""))
    asyncio.run(run(kind, NOW + 1))
    env.send.assert_not_awaited()
    asyncio.run(run(kind, NOW + 101))
    env.send.assert_awaited_once()
    assert env.send.await_args.args[0] == tianxing.CMD_TIANXING_PANEL


@pytest.mark.parametrize("kind", KINDS)
def test_early_craft_result_is_accounted_and_not_reopened(env, kind):
    async def send(command, **kwargs):
        env.identity["pending_tasks"] = {
            (CHAT_ID, 4701): {
                "chat_id": CHAT_ID, "msg_id": 4701, "cmd": command, "op_id": kwargs["op_id"],
                "source_module": "\u5929\u661f\u5b97", "send_started_at": NOW, "sent_at": NOW,
            },
        }
        assert tianxing.apply_tianxing_passive(
            get_real_message_text(SAMPLES, "tianxing.craft_farm.hit"),
            now=NOW + 1, family="tianxing_craft_farm",
            reply_context={"send_as_id": IDENTITY_ID, "chat_id": CHAT_ID, "root_msg_id": 4701, "msg_id": 4702},
        )
        return receipt(sent_at=NOW + 2)

    env.send.side_effect = send
    asyncio.run(run(kind))
    current = farm(env)
    assert current["phase"] == "ready"
    assert current["daily_count"] == 1
    assert current["last_msg_id"] == 4701
    assert current.get("last_chat_id") == CHAT_ID


@pytest.mark.parametrize("stage", ["timeline_required", "consume_conflicting_prediction"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "config", "module_disabled", "new_farm", "new_clock"])
def test_craft_child_await_cannot_overwrite_new_state(env, monkeypatch, stage, change):
    seed_farm(env, phase="ready")
    monkeypatch.setattr(tianxing, "build_tianxing_craft_farm_plan", lambda **_kwargs: tianxing._craft_farm_result(
        stage, active=True, action="timeline", timeline_required=stage == "timeline_required", next_time=NOW + 60,
    ))
    expected = None

    async def child(*_args, **_kwargs):
        nonlocal expected
        invalidate(env, change)
        expected = copy.deepcopy(state_module._meta_state)
        return {"phase": "sent_waiting_ack", "stage": "sent_waiting_reply", "next_time": NOW + 90}

    name = "run_tianxing_timeline_scheduler" if stage == "timeline_required" else "run_tianxing_retreat_farm_scheduler"
    monkeypatch.setattr(tianxing, name, child)
    asyncio.run(run("farm"))
    assert state_module._meta_state == expected
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module_disabled", "global_disabled", "paused", "config"])
def test_craft_lock_wait_rechecks_owner(env, kind, change):
    async def execute():
        lock = asyncio.Lock()
        tianxing._TIANXING_CRAFT_LOCKS[IDENTITY_ID] = lock
        await lock.acquire()
        task = asyncio.create_task(run(kind))
        await asyncio.sleep(0)
        invalidate(env, change)
        expected = copy.deepcopy(state_module._meta_state)
        lock.release()
        await task
        assert state_module._meta_state == expected

    asyncio.run(execute())
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("msg_id", [0, -1, "bad", True, 1.5, float("nan"), float("inf")])
def test_malformed_craft_receipts_are_unknown(env, monkeypatch, kind, msg_id):
    env.send.return_value = receipt(id=msg_id)
    monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": "action_guard"})
    result = asyncio.run(run(kind))
    assert result["stage"] == "send_unknown"
    assert farm(env)["pending_craft"]["status"] == "unknown"
    assert farm(env)["last_msg_id"] == 0


def context(root=4701, **overrides):
    return dict(dict(chat_id=CHAT_ID, root_msg_id=root, msg_id=root + 1, send_as_id=IDENTITY_ID), **overrides)


def apply_result(text, now, ctx, family="tianxing_craft_farm"):
    with state_module.use_identity(IDENTITY_ID):
        return tianxing.apply_tianxing_passive(text, now, family, reply_context=ctx)


@pytest.mark.parametrize("kind", KINDS)
def test_calibration_retains_original_craft_until_its_late_result(env, kind):
    asyncio.run(run(kind))
    original = copy.deepcopy(farm(env)["pending_craft"])
    env.send.return_value = receipt(id=4703, sent_at=NOW + 80, send_started_at=NOW + 80)
    asyncio.run(run(kind, NOW + 80))
    assert env.send.await_args.args[0] == tianxing.CMD_TIANXING_PANEL
    assert farm(env)["pending_craft"] == original
    panel = get_real_message_text(SAMPLES, "tianxing.panel.basic")
    assert apply_result(panel, NOW + 81, context(4703), "tianxing_panel")
    assert farm(env)["pending_craft"] == original
    assert farm(env)["phase"] == "sent_waiting_reply"
    assert env.send.await_count == 2
    assert apply_result(get_real_message_text(SAMPLES, "tianxing.craft_farm.hit"), NOW + 82, context())
    assert farm(env)["pending_craft"] == {}
    assert farm(env)["daily_count"] == 1
    assert farm(env)["last_msg_id"] == 4703
    assert farm(env)["last_craft_result"]["msg_id"] == 4701


@pytest.mark.parametrize("kind", KINDS)
def test_delayed_panel_cannot_roll_back_newer_craft_settlement(env, kind):
    asyncio.run(run(kind))
    env.send.return_value = receipt(id=4703, sent_at=NOW + 80, send_started_at=NOW + 80)
    asyncio.run(run(kind, NOW + 80))
    assert apply_result(get_real_message_text(SAMPLES, "tianxing.craft_farm.hit"), NOW + 81, context())
    expected = copy.deepcopy(env.identity)
    apply_result(get_real_message_text(SAMPLES, "tianxing.panel.basic"), NOW + 82, context(4703), "tianxing_panel")
    assert env.identity == expected


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("delta", [1, 2, 100])
def test_same_craft_settlement_never_applies_twice(env, kind, delta):
    asyncio.run(run(kind))
    text = get_real_message_text(SAMPLES, "tianxing.craft_farm.hit")
    assert apply_result(text, NOW + 1, context())
    expected = copy.deepcopy(env.identity)
    assert apply_result(text, NOW + delta, context())
    assert env.identity == expected


@pytest.mark.parametrize("invalid", ["chat", "root", "owner", "time", "item"])
def test_unrelated_craft_reply_does_not_settle_pending(env, invalid):
    asyncio.run(run())
    ctx = context()
    now = NOW + 1
    text = get_real_message_text(SAMPLES, "tianxing.craft_farm.hit")
    if invalid == "chat":
        ctx["chat_id"] -= 1
    elif invalid == "root":
        ctx["root_msg_id"] += 1
    elif invalid == "owner":
        ctx["send_as_id"] += 1
    elif invalid == "time":
        now = NOW - 2
    else:
        text = text.replace(ITEM, "another-item")
    original = farm(env)
    apply_result(text, now, ctx)
    assert farm(env)["pending_craft"] == original["pending_craft"]
    assert farm(env)["daily_count"] == 0
    assert farm(env)["phase"] == "sent_waiting_reply"


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_craft_is_not_closed_by_an_unrelated_panel(env, kind):
    env.send.return_value = None
    asyncio.run(run(kind))
    original = farm(env)
    apply_result(get_real_message_text(SAMPLES, "tianxing.panel.basic"), NOW + 1, context(4800), "tianxing_panel")
    assert farm(env) == original
    asyncio.run(run(kind, NOW + 4000))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("outcome", ["unknown", "exception", "cancelled", "unsent"])
def test_calibration_send_failure_preserves_original_craft(env, monkeypatch, outcome):
    asyncio.run(run())
    original = copy.deepcopy(farm(env)["pending_craft"])
    env.send.return_value = None
    if outcome == "exception":
        env.send.side_effect = RuntimeError("fixture")
    elif outcome == "cancelled":
        env.send.side_effect = asyncio.CancelledError
    elif outcome == "unsent":
        monkeypatch.setattr(tianxing, "get_last_game_send_block", lambda *_args: {"code": "action_guard"})
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run(now=NOW + 80))
    else:
        asyncio.run(run(now=NOW + 80))
    assert farm(env)["pending_craft"] == original
    assert farm(env)["calibration_required"]
    assert env.send.await_args.args[0] == tianxing.CMD_TIANXING_PANEL
    asyncio.run(run(now=NOW + 81))
    assert env.send.await_count == 2


def test_final_guard_admits_only_original_craft_dispatch(env):
    decisions = []

    async def send(command, **kwargs):
        for op_id in (kwargs["op_id"], "another-operation"):
            decisions.append(tianxing.tianxing_route_pre_send_guard(
                command, send_as_id=IDENTITY_ID, now=NOW,
                intent={"source_module": kwargs["source_module"], "op_id": op_id},
            )["allowed"])
        return receipt()

    env.send.side_effect = send
    asyncio.run(run("farm"))
    assert decisions == [True, False]


@pytest.mark.parametrize("route", tianxing.TIANXING_ROUTES)
def test_pending_craft_blocks_route_preflight(env, route):
    env.send.return_value = None
    asyncio.run(run())
    with state_module.use_identity(IDENTITY_ID):
        plan = tianxing.build_tianxing_route_preflight_plan(route, now=NOW + 600)
    assert not plan["route_allowed"]
    assert plan["stage"] == "craft_pending"


@pytest.mark.parametrize("command", [COMMAND, tianxing.CMD_EXPLORE_RIFT, f"{tianxing.CMD_TIANXING_PREDICT} {CRAFT_ROUTE}"])
def test_unknown_craft_blocks_final_game_guard(env, command):
    env.send.return_value = None
    asyncio.run(run())
    with state_module.use_identity(IDENTITY_ID):
        result = tianxing.tianxing_route_pre_send_guard(command, send_as_id=IDENTITY_ID, now=NOW + 600)
    assert not result["allowed"]
    assert result["code"] == "tianxing_craft_pending"


def test_unknown_craft_survives_sqlite_reload(env, monkeypatch, tmp_path):
    from model import persistence

    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "craft.db"))
    monkeypatch.setattr(tianxing, "save_state", persistence.save_state)
    env.send.return_value = None
    asyncio.run(run())
    original = farm(env)["pending_craft"]
    assert persistence.load_state() is True
    env.identity = state_module.get_identity_state(IDENTITY_ID)
    for kind in KINDS:
        asyncio.run(run(kind, NOW + 86400))
    assert farm(env)["pending_craft"] == original
    assert farm(env)["phase"] == "send_unknown"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("child", ["consume", "timeline"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module_disabled", "clock", "rift_disabled"])
def test_rift_preparation_does_not_outlive_parent(env, monkeypatch, child, change):
    from model.features import explore_rift

    env.identity["explore_rift_enabled"] = True
    env.identity["next_explore_rift_time"] = NOW
    expected = None
    checks = []

    async def invoke(*_args, **kwargs):
        nonlocal expected
        checks.append(kwargs["operation_check"]())
        if change == "clock":
            env.identity["next_explore_rift_time"] = NOW + 36000
        elif change == "rift_disabled":
            env.identity["explore_rift_enabled"] = False
        else:
            invalidate(env, change)
        checks.append(kwargs["operation_check"]())
        expected = copy.deepcopy(state_module._meta_state)
        return {"active": True, "stage": "sent_waiting_reply", "phase": "sent_waiting_ack"}

    monkeypatch.setattr(explore_rift, "save_state", Mock())
    monkeypatch.setattr(explore_rift, "build_tianxing_route_preflight_plan", lambda *_args, **_kwargs: {
        "route_allowed": False, "stage": "prediction_conflict" if child == "consume" else "timeline_waiting",
        "timeline_required": child == "timeline",
    })
    monkeypatch.setattr(explore_rift, "build_tianxing_consume_window", lambda *_args, **_kwargs: [{}])
    name = "run_tianxing_consume_craft_prediction" if child == "consume" else "run_tianxing_timeline_scheduler"
    monkeypatch.setattr(explore_rift, name, invoke)

    async def execute():
        with state_module.use_identity(IDENTITY_ID):
            return await explore_rift._prepare_explore_rift_tianxing_route(NOW)

    assert not asyncio.run(execute())
    assert checks == [True, False]
    assert state_module._meta_state == expected
    env.send.assert_not_awaited()


def test_existing_runtime_receipt_resolves_detached_craft_send(env, monkeypatch):
    from model import runtime

    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(runtime, "mark_dirty", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(runtime, "_GAME_SEND_BLOCK_LAST", {})

    async def send(command, **kwargs):
        runtime._finalize_game_command_sent(
            command, msg_id=4701, sent_at=NOW + 1, send_started_at=NOW,
            send_as_id=IDENTITY_ID, game_group_id=CHAT_ID, topic_id=0,
            send_intent={"source_module": kwargs["source_module"], "op_id": kwargs["op_id"]},
            max_retry=0, append_sent_log=False,
        )
        return None

    env.send.side_effect = send
    assert asyncio.run(run())["stage"] == "send_unknown"
    assert apply_result(get_real_message_text(SAMPLES, "tianxing.craft_farm.hit"), NOW + 2, context())
    current = farm(env)
    assert not current["pending_craft"]
    assert current["last_msg_id"] == 4701
    assert current["last_chat_id"] == CHAT_ID
    assert current["daily_count"] == 1
    env.send.assert_awaited_once()


@pytest.mark.parametrize("mismatch", ["op_id", "command", "source", "account", "older", "future", "ambiguous"])
def test_uncorrelated_runtime_receipt_cannot_resolve_unknown_craft(env, mismatch):
    env.send.return_value = None
    asyncio.run(run())
    pending = farm(env)["pending_craft"]
    record = {
        "op_id": pending["op_id"], "cmd": COMMAND, "source_module": "\u5929\u661f\u5b97",
        "chat_id": CHAT_ID, "sent_at": NOW + 1, "send_started_at": NOW,
    }
    if mismatch == "op_id":
        record["op_id"] = "unrelated"
    elif mismatch == "command":
        record["cmd"] = "unrelated"
    elif mismatch == "source":
        record["source_module"] = "unrelated"
    elif mismatch == "account":
        state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID + 1)
    elif mismatch == "older":
        record["send_started_at"] = NOW - 60
    elif mismatch == "future":
        record["sent_at"] = NOW + 60
    env.identity["pending_tasks"] = {(CHAT_ID, 4701): record}
    if mismatch == "ambiguous":
        env.identity["pending_tasks"][(CHAT_ID, 4801)] = dict(record)
    apply_result(get_real_message_text(SAMPLES, "tianxing.craft_farm.hit"), NOW + 2, context())
    assert farm(env)["pending_craft"] == pending
    assert farm(env)["daily_count"] == 0
    assert farm(env)["last_msg_id"] == 0


def test_early_craft_receipt_cannot_be_rebound_to_another_chat(env):
    async def send(command, **kwargs):
        env.identity["pending_tasks"] = {(CHAT_ID, 4701): {
            "chat_id": CHAT_ID, "op_id": kwargs["op_id"], "cmd": command,
            "source_module": "\u5929\u661f\u5b97", "sent_at": NOW, "send_started_at": NOW,
        }}
        apply_result(get_real_message_text(SAMPLES, "tianxing.craft_farm.hit"), NOW + 1, context())
        return receipt(chat_id=CHAT_ID - 1)

    env.send.side_effect = send
    asyncio.run(run())
    assert farm(env)["last_chat_id"] == CHAT_ID
    assert farm(env)["daily_count"] == 1
    assert not farm(env)["pending_craft"]


@pytest.mark.parametrize("sent_at", [0, -1, "bad", float("inf"), float("nan")])
def test_dirty_receipt_time_cannot_poison_craft_clock(env, sent_at):
    env.send.return_value = receipt(sent_at=sent_at)
    asyncio.run(run())
    assert farm(env)["last_sent_at"] == NOW
    assert farm(env)["next_time"] == NOW + tianxing.TIANXING_CRAFT_FARM_REPLY_TIMEOUT_SEC


def test_rift_preparation_retry_starts_after_child_wait(env, monkeypatch):
    from model.features import explore_rift

    env.identity["explore_rift_enabled"] = True
    env.identity["next_explore_rift_time"] = NOW

    async def child(*_args, **kwargs):
        assert kwargs["operation_check"]()
        env.clock.value = 90
        return {"active": True, "stage": "sent_waiting_reply", "takeover": True}

    monkeypatch.setattr(explore_rift, "save_state", Mock())
    monkeypatch.setattr(explore_rift, "build_tianxing_route_preflight_plan", lambda *_args, **_kwargs: {
        "route_allowed": False, "stage": "prediction_conflict",
    })
    monkeypatch.setattr(explore_rift, "run_tianxing_consume_craft_prediction", child)

    async def execute():
        with state_module.use_identity(IDENTITY_ID):
            return await explore_rift._prepare_explore_rift_tianxing_route(NOW)

    assert not asyncio.run(execute())
    assert env.identity["next_explore_rift_time"] == NOW + 90 + explore_rift.EXPLORE_RIFT_TIANXING_PREPARE_RETRY_SEC


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("retry_kind", KINDS)
@pytest.mark.parametrize("stale_day", [False, True])
def test_business_denial_backoff_prevents_another_craft(env, kind, retry_kind, stale_day):
    asyncio.run(run(kind))
    assert apply_result("\u6750\u6599\u4e0d\u8db3\uff0c\u7f3a\u5c11\uff1a\u7384\u94c1", NOW + 1, context())
    if stale_day:
        env.identity["tianxing_timeline_state"]["craft_farm"]["daily_day"] = tianxing.get_day_key(NOW - 86400)
    original = farm(env)
    assert original["phase"] == "blocked"
    assert not original["pending_craft"]
    result = asyncio.run(run(retry_kind, NOW + 2))
    assert result["stage"] == "blocked_waiting"
    env.send.assert_awaited_once()
    assert farm(env) == original


@pytest.mark.parametrize("kind", KINDS)
def test_business_denial_backoff_expires(env, kind):
    asyncio.run(run(kind))
    apply_result("\u6750\u6599\u4e0d\u8db3\uff0c\u7f3a\u5c11\uff1a\u7384\u94c1", NOW + 1, context())
    due_at = farm(env)["next_time"]
    env.identity["tianxing_observation"]["current_prediction_until"] = due_at + 600
    assert asyncio.run(run(kind, due_at - 0.001))["stage"] == "blocked_waiting"
    env.send.assert_awaited_once()
    env.send.return_value = receipt(id=4705, sent_at=due_at, send_started_at=due_at)
    assert asyncio.run(run(kind, due_at))["stage"] == "sent_waiting_reply"
    assert env.send.await_count == 2
    assert env.send.await_args.args[0] == COMMAND


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("newer_panel", [False, True])
def test_panel_arriving_before_receipt_respects_actual_dispatch_order(env, kind, newer_panel):
    asyncio.run(run(kind))
    expected_observed = None
    expected_result = None

    async def send(command, **kwargs):
        nonlocal expected_observed, expected_result
        assert command == tianxing.CMD_TIANXING_PANEL
        apply_result(get_real_message_text(SAMPLES, "tianxing.craft_farm.hit"), NOW + 81, context())
        expected_observed = copy.deepcopy(env.identity["tianxing_observation"])
        expected_result = copy.deepcopy(farm(env))
        dispatched_at = NOW + (82 if newer_panel else 80)
        env.identity["pending_tasks"][(CHAT_ID, 4703)] = {
            "chat_id": CHAT_ID, "op_id": kwargs["op_id"], "cmd": command,
            "source_module": "\u5929\u661f\u5b97", "sent_at": dispatched_at, "send_started_at": dispatched_at,
        }
        apply_result(get_real_message_text(SAMPLES, "tianxing.panel.basic"), NOW + 83, context(4703), "tianxing_panel")
        env.clock.value += 4
        return receipt(id=4703, sent_at=dispatched_at, send_started_at=dispatched_at)

    env.send.side_effect = send
    asyncio.run(run(kind, now=NOW + 80))
    if newer_panel:
        assert env.identity["tianxing_observation"]["last_action"] == "\u5929\u673a\u76d8"
    else:
        assert env.identity["tianxing_observation"] == expected_observed
        for key in ("phase", "next_time", "last_result", "last_error", "last_craft_result"):
            assert farm(env)[key] == expected_result[key]
    assert not farm(env)["pending_craft"]
    assert farm(env)["daily_count"] == 1
    assert farm(env)["last_msg_id"] == 4703
