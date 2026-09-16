import asyncio
import copy
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_miniapp as api
from model.features import cave_treasure_runtime as cave
from model.features import yuanying
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppHttpResult


IDENTITY_ID = 1001
ACCOUNT_ID = 11
PLAYER_ID = -1_000_000_001_001
NOW = 1_700_000_500.0
ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE999"
READY = "\u3010\u5143\u5a74\u72b6\u6001\u3011\n\u72b6\u6001: \u7a8d\u4e2d\u6e29\u517b"
LAUNCHED = (
    "\u4f60\u5fc3\u5ff5\u4e00\u52a8\uff0c\u4e39\u7530\u4e2d\u7684\u5143\u5a74\u5316\u4f5c\u4e00\u9053\u6d41\u5149\u98de\u51fa\uff0c"
    "\u6d88\u5931\u5728\u5929\u9645\u3002\n\u5b83\u5c06\u5728\u5916\u4e91\u6e38 8 \u5c0f\u65f6\u3002"
)
COOLDOWN = "\u3010\u5143\u5a74\u72b6\u6001\u3011\n\u5f52\u6765\u5012\u8ba1\u65f6 1\u5c0f\u65f6\u3002"


def payload(message):
    return {
        "ok": True, "account": {"playerId": PLAYER_ID},
        "actionResult": {"ok": True, "completed": True, "rawMessage": message},
    }


def result(message):
    return {
        "ok": True, "status": "ok", "action_dispatched": True,
        "data": payload(message), "outcome_unknown": False,
    }


def record():
    return state_module.get_miniapp_state_records().get(f"{IDENTITY_ID}:cave_yuanying", {}).get("state", {})


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID)
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(yuanying_enabled=True, yuanying_phase="idle", next_yuanying_time=NOW - 1)
    session = {
        "ok": True, "init_data": "fixture-init", "player_id": PLAYER_ID,
        "result": {"ok": True, "data": {"raw": payload(READY)}},
    }
    loader = AsyncMock(return_value=session)
    flow = AsyncMock(side_effect=[result(READY), result(LAUNCHED)])
    audit, save = AsyncMock(), Mock()
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_tianjige_command_production_flow", flow)
    monkeypatch.setattr(cave, "_capture_store", lambda _now: None)
    monkeypatch.setattr(cave, "send_audit_log", audit)
    monkeypatch.setattr(cave, "save_state", save)
    monkeypatch.setattr(cave.time, "time", lambda: clock.now)
    monkeypatch.setattr(yuanying, "save_state", save)
    monkeypatch.setattr(yuanying, "send_audit_log", AsyncMock())
    monkeypatch.setattr(yuanying, "send_game_command", AsyncMock())
    monkeypatch.setattr(yuanying, "_note_yuanying_remote_block", Mock())
    try:
        yield SimpleNamespace(identity=identity, loader=loader, flow=flow, audit=audit, save=save, clock=clock)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(saved)


def run(now=NOW):
    return asyncio.run(cave.run_cave_public_yuanying(IDENTITY_ID, ENTRY, now=now))


def change_owner_or_work(env, change):
    if change == "delete":
        state_module.remove_identity(IDENTITY_ID)
    elif change == "replace":
        state_module._meta_state["identity_states"][IDENTITY_ID] = copy.deepcopy(env.identity)
    elif change == "rebind":
        state_module.set_identity_account(IDENTITY_ID, 22)
    elif change == "disable":
        state_module.set_identity_enabled(IDENTITY_ID, False)
    elif change == "module_off":
        env.identity["yuanying_enabled"] = False
    elif change == "pause":
        state_module.set_global_enabled(False)
    elif change == "new_clock":
        env.identity["next_yuanying_time"] = NOW + 12345
    elif change == "new_phase":
        env.identity["yuanying_phase"] = "waiting_summary"
    else:
        state_module.set_miniapp_state_records({
            f"{IDENTITY_ID}:cave_yuanying": {"updated_at": NOW + 1, "state": {"replacement": True}},
        })


@pytest.mark.parametrize("change", [
    "delete", "replace", "rebind", "disable", "module_off", "pause", "new_clock", "new_phase", "new_record",
])
@pytest.mark.parametrize("stage", ["session", "status"])
def test_no_launch_or_projection_after_read_await_is_invalidated(env, change, stage):
    after = {}

    async def changed(*_args, **_kwargs):
        change_owner_or_work(env, change)
        after.update(copy.deepcopy(state_module._meta_state))
        return env.loader.return_value if stage == "session" else result(READY)

    if stage == "session":
        env.loader.side_effect = changed
    else:
        env.flow.side_effect = changed
    response = run()
    assert not response["ok"]
    assert state_module._meta_state == after
    assert env.flow.await_count == (0 if stage == "session" else 1)


@pytest.mark.parametrize("stage", ["session", "status"])
def test_parent_entry_guard_is_honored_between_requests(env, stage):
    allowed = True

    async def changed(*_args, **kwargs):
        nonlocal allowed
        assert kwargs["operation_check"]()
        allowed = False
        return env.loader.return_value if stage == "session" else result(READY)

    if stage == "session":
        env.loader.side_effect = changed
    else:
        env.flow.side_effect = changed
    before = copy.deepcopy(state_module._meta_state)
    with cave.observe_cave_public_entry(IDENTITY_ID, ENTRY, operation_check=lambda: allowed):
        response = run()
    assert not response["ok"]
    assert state_module._meta_state == before
    assert env.flow.await_count == (0 if stage == "session" else 1)


@pytest.mark.parametrize("change", ["delete", "replace", "rebind", "new_clock", "new_phase", "new_record"])
def test_late_launch_does_not_overwrite_changed_owner_or_business_state(env, change):
    after = {}

    async def launched(*_args, command, **_kwargs):
        if command == yuanying.CMD_YUANYING_STATUS:
            return result(READY)
        change_owner_or_work(env, change)
        after.update(copy.deepcopy(state_module._meta_state))
        return result(LAUNCHED)

    env.flow.side_effect = launched
    response = run()
    assert not response["ok"]
    assert state_module._meta_state == after
    assert env.flow.await_count == 2


@pytest.mark.parametrize("change", ["disable", "module_off", "pause"])
def test_dispatched_success_is_retained_after_controls_change(env, change):
    async def launched(*_args, command, **_kwargs):
        if command == yuanying.CMD_YUANYING_STATUS:
            return result(READY)
        change_owner_or_work(env, change)
        return result(LAUNCHED)

    env.flow.side_effect = launched
    response = run()
    assert response["ok"]
    assert env.identity["yuanying_phase"] == "running"
    assert env.identity["next_yuanying_time"] == NOW + 8 * 3600 + yuanying.CD_BUFFER_SEC
    assert record()["status"] == "confirmed"
    assert not record()["outcome_unknown"]
    if change == "module_off":
        assert not env.identity["yuanying_enabled"]


@pytest.mark.parametrize("message,command", [
    (LAUNCHED, yuanying.CMD_YUANYING),
    (COOLDOWN, yuanying.CMD_YUANYING_STATUS),
])
def test_miniapp_bridge_never_enters_legacy_async_handlers(env, monkeypatch, message, command):
    success, status = AsyncMock(return_value=True), AsyncMock(return_value=True)
    monkeypatch.setattr(yuanying, "handle_yuanying_success_reply", success)
    monkeypatch.setattr(yuanying, "handle_yuanying_status_reply", status)
    response = asyncio.run(cave.sync_cave_tianjige_yuanying_result(
        IDENTITY_ID, payload(message), now=NOW, command=command,
    ))
    assert response["handled"]
    success.assert_not_awaited()
    status.assert_not_awaited()
    assert env.identity["next_yuanying_time"] > NOW


@pytest.mark.parametrize("ok", [False, 0, None, "false"])
def test_false_outer_envelope_cannot_authorize_status_or_launch(env, ok):
    before = copy.deepcopy(state_module._meta_state)
    response = asyncio.run(cave.sync_cave_tianjige_yuanying_result(
        IDENTITY_ID, {"ok": ok, "data": payload(READY)}, now=NOW, command=yuanying.CMD_YUANYING_STATUS,
    ))
    assert not response["handled"]
    assert state_module._meta_state == before


def test_failed_transport_cannot_apply_successful_looking_status_data(env):
    env.flow.side_effect = None
    env.flow.return_value = {**result(READY), "ok": False, "error": "fixture-failed"}
    before = copy.deepcopy(env.identity)
    response = run()
    assert not response["ok"]
    assert env.identity == before
    env.flow.assert_awaited_once()


def test_audit_failure_does_not_lose_confirmed_launch(env):
    env.audit.side_effect = RuntimeError("audit unavailable")
    response = run()
    assert response["ok"]
    assert env.identity["yuanying_phase"] == "running"
    assert record()["status"] == "confirmed"
    assert env.flow.await_count == 2


@pytest.mark.parametrize("stage", ["status", "launch"])
def test_cancelled_worker_keeps_lock_and_only_commits_dispatched_success(env, monkeypatch, stage):
    entered, release = threading.Event(), threading.Event()

    def execute(request, *_args, **_kwargs):
        is_status = request["payload"]["command"] == yuanying.CMD_YUANYING_STATUS
        if is_status == (stage == "status"):
            entered.set()
            assert release.wait(3)
        return MiniAppHttpResult(ok=True, data=payload(READY if is_status else LAUNCHED), attempts=1, status_code=200)

    monkeypatch.setattr(api, "execute_miniapp_http_request", execute)
    monkeypatch.setattr(cave, "run_cave_tianjige_command_production_flow", api.run_cave_tianjige_command_production_flow)

    async def scenario():
        task = asyncio.create_task(cave.run_cave_public_yuanying(IDENTITY_ID, ENTRY, now=NOW))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0.01)
            assert cave._public_entry_lock(IDENTITY_ID).locked()
            assert not task.done()
            task.cancel()
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as error:
                await task
            assert error.value.result["ok"] is (stage == "launch")
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert not cave._public_entry_lock(IDENTITY_ID).locked()

    asyncio.run(scenario())
    if stage == "launch":
        assert env.identity["yuanying_phase"] == "running"
        assert record()["status"] == "confirmed"
    else:
        assert env.identity["yuanying_phase"] == "idle"
        assert not record()


def test_launch_intent_exists_before_http_and_unknown_status_never_replays_it(env):
    async def launched(*_args, command, **_kwargs):
        if command == yuanying.CMD_YUANYING_STATUS:
            return result(READY)
        assert record()["outcome_unknown"]
        assert record()["status"] == "dispatching"
        assert record()["account_id"] == ACCOUNT_ID
        return {
            "ok": False, "status": "failed", "error": "timeout", "action_dispatched": True,
            "outcome_unknown": True, "data": {}, "events": [{"status_code": 0}],
        }

    env.flow.side_effect = launched
    response = run()
    assert not response["ok"]
    assert response["extra"]["outcome_unknown"]
    assert record()["outcome_unknown"]
    env.clock.now = NOW + 12 * 3600
    env.flow.reset_mock()
    env.flow.side_effect = None
    env.flow.return_value = result(READY)
    response = run(env.clock.now)
    assert not response["ok"]
    assert response["extra"]["outcome_unknown"]
    env.flow.assert_awaited_once()
    assert env.flow.await_args.kwargs["command"] == yuanying.CMD_YUANYING_STATUS
    assert record()["outcome_unknown"]


@pytest.mark.parametrize("stage", ["status", "launch"])
def test_rejection_retains_retry_after_without_claiming_launch(env, stage):
    rejected = {
        "ok": False, "status": "failed", "error": "fixture-limit", "data": {},
        "action_dispatched": True, "outcome_unknown": False,
        "events": [{"status_code": 429, "retry_after_sec": 50000}],
    }
    env.flow.side_effect = [rejected] if stage == "status" else [result(READY), rejected]
    response = run()
    assert not response["ok"]
    assert response["extra"]["retry_after_sec"] == 50000
    assert not response["extra"].get("outcome_unknown")
    assert not response["extra"].get("launched")
    assert not record().get("outcome_unknown")


def test_unknown_public_launch_blocks_legacy_group_fallback(env, monkeypatch):
    state_module.set_miniapp_state_records({
        f"{IDENTITY_ID}:cave_yuanying": {"state": {"account_id": ACCOUNT_ID, "outcome_unknown": True}},
    })
    scheduler = AsyncMock()
    monkeypatch.setattr(yuanying, "run_phaseful_scheduler", scheduler)
    monkeypatch.setattr(yuanying, "is_cave_public_auto_enabled", lambda _feature: False)
    with state_module.use_identity(IDENTITY_ID):
        asyncio.run(yuanying.run_yuanying_scheduler(NOW))
    scheduler.assert_not_awaited()


def install_unknown(*, account_id=ACCOUNT_ID, outcome_unknown=True):
    state_module.set_miniapp_state_records({
        f"{IDENTITY_ID}:cave_yuanying": {"state": {
            "account_id": account_id, "outcome_unknown": outcome_unknown, "status": "unknown",
        }},
    })


@pytest.mark.parametrize("message,reconciled", [
    (COOLDOWN, True),
    (COOLDOWN.replace("1", "0"), False),
    ("\u3010\u5143\u5a74\u72b6\u6001\u3011\n\u5c1a\u672a\u6062\u590d\uff0c\u8bf7\u4f11\u606f 1\u5c0f\u65f6\u3002", False),
    ("\u3010\u5143\u5a74\u72b6\u6001\u3011\n\u72b6\u6001: \u5143\u5a74\u95ed\u5173", False),
    (READY, False),
])
def test_unknown_recovery_needs_cloud_travel_postcondition(env, message, reconciled):
    install_unknown()
    env.flow.side_effect = None
    env.flow.return_value = result(message)
    response = run()
    assert response["ok"] is reconciled
    assert record()["outcome_unknown"] is not reconciled
    assert not response["extra"]["launched"]
    env.flow.assert_awaited_once()
    assert env.flow.await_args.kwargs["command"] == yuanying.CMD_YUANYING_STATUS


@pytest.mark.parametrize("account_id", [None, True, "11", 22])
def test_unresolved_record_with_unverified_owner_cannot_be_rebound_or_replayed(env, account_id):
    install_unknown(account_id=account_id)
    before = copy.deepcopy(state_module._meta_state)
    response = run()
    assert not response["ok"]
    assert response["extra"]["outcome_unknown"]
    assert state_module._meta_state == before
    env.loader.assert_not_awaited()
    env.flow.assert_not_awaited()


@pytest.mark.parametrize("flag", [None, "false", "true", 0, 1, False])
def test_corrupt_unknown_marker_cannot_reopen_dispatch(env, flag):
    install_unknown(outcome_unknown=flag)
    env.flow.side_effect = None
    env.flow.return_value = result(READY)
    response = run()
    assert not response["ok"]
    assert record()["outcome_unknown"] is True
    env.flow.assert_awaited_once()


@pytest.mark.parametrize("summary_pressure", [False, True])
def test_unknown_survives_normal_codec_and_only_schedules_status(env, summary_pressure):
    from model import miniapp_state, persistence, ui

    env.flow.side_effect = [result(READY), {
        "ok": False, "status": "failed", "error": "timeout", "outcome_unknown": True, "action_dispatched": True,
    }]
    assert not run()["ok"]
    if summary_pressure:
        for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 5):
            miniapp_state.record_miniapp_state(
                IDENTITY_ID, f"fixture_{number}", {"value": number}, now=NOW + number + 1, persist=False,
            )
    _get, encode, restore = persistence._META_STATE_CODEC["miniapp_state_records"]
    encoded = encode(state_module.get_miniapp_state_records())
    state_module.set_miniapp_state_records({})
    restore(encoded)
    assert record()["outcome_unknown"]
    assert not ui._cave_public_background_action_due("yuanying", IDENTITY_ID, NOW)
    env.clock.now = env.identity["next_yuanying_time"]
    assert ui._cave_public_background_action_due("yuanying", IDENTITY_ID, env.clock.now)
    env.flow.reset_mock()
    env.flow.side_effect = None
    env.flow.return_value = result(READY)
    assert not run(env.clock.now)["ok"]
    env.flow.assert_awaited_once()
    assert record()["outcome_unknown"]


@pytest.mark.parametrize("variant", ["other", "missing", "selector_only"])
def test_unowned_launch_receipt_remains_unknown_and_cannot_publish_foreign_clock(env, variant):
    unowned = result(LAUNCHED)
    if variant == "other":
        unowned["data"]["account"]["playerId"] = ACCOUNT_ID
    else:
        unowned["data"].pop("account")
        if variant == "selector_only":
            unowned["data"]["identity"] = {"selectedPlayerId": PLAYER_ID}
    env.flow.side_effect = [result(READY), unowned]
    response = run()
    assert not response["ok"]
    assert response["extra"]["outcome_unknown"]
    assert env.identity["yuanying_phase"] == "launching"
    assert env.identity["next_yuanying_time"] == NOW + cave.CAVE_YUANYING_UNKNOWN_RECHECK_SEC
    assert record()["outcome_unknown"]


@pytest.mark.parametrize("completed", [False, None, 0, "true"])
def test_incomplete_launch_is_not_a_rejection_or_a_confirmed_result(env, completed):
    incomplete = result(LAUNCHED)
    incomplete["data"]["actionResult"]["completed"] = completed
    env.flow.side_effect = [result(READY), incomplete]
    response = run()
    assert not response["ok"]
    assert response["extra"]["outcome_unknown"]
    assert record()["outcome_unknown"]


def test_http_business_rejection_is_not_made_unknown_again_by_runtime(env):
    env.flow.side_effect = [result(READY), {
        "ok": False, "status": "failed", "error": "fixture-denied", "data": {},
        "action_dispatched": True, "outcome_unknown": False, "events": [{"status_code": 200}],
    }]
    response = run()
    assert not response["ok"]
    assert not response["extra"]["outcome_unknown"]
    assert record()["status"] == "rejected"


def test_notification_cancellation_carries_saved_completion(env):
    env.audit.side_effect = asyncio.CancelledError()
    with pytest.raises(MiniAppFlowCancelled) as error:
        run()
    assert error.value.result["ok"]
    assert record()["status"] == "confirmed"
    assert env.identity["yuanying_phase"] == "running"


def test_legacy_warm_status_cannot_rearm_unresolved_public_launch(env):
    install_unknown()
    before = copy.deepcopy(state_module._meta_state)
    with state_module.use_identity(IDENTITY_ID):
        asyncio.run(yuanying.handle_yuanying_status_reply(
            READY, NOW, reply_to=SimpleNamespace(raw_text=yuanying.CMD_YUANYING_STATUS),
            matched_family="yuanying",
        ))
    yuanying.send_game_command.assert_not_awaited()
    assert state_module._meta_state == before


@pytest.mark.parametrize("command,allowed", [
    (yuanying.CMD_YUANYING, False),
    (yuanying.CMD_YUANYING_SECT_RETREAT, False),
    (yuanying.CMD_YUANYING_STATUS, True),
    (".\u6df1\u5ea6\u95ed\u5173", True),
    (".\u63a8\u547d \u63a2\u7d22", True),
])
def test_shared_send_guard_blocks_only_unresolved_yuanying_mutations(env, command, allowed):
    from model import runtime

    install_unknown()
    with state_module.use_identity(IDENTITY_ID):
        accepted, _reason, code = asyncio.run(runtime._run_game_command_pre_send_guards(
            command, send_as_id=IDENTITY_ID, priority="normal",
        ))
    assert accepted is allowed
    if not allowed:
        assert code == "pre_send_guard"


@pytest.mark.parametrize("other", [
    "\u72b6\u6001: \u5143\u5a74\u95ed\u5173",
    "\u5f52\u6765\u5012\u8ba1\u65f6 1\u5c0f\u65f6",
])
def test_contradictory_ready_panel_cannot_authorize_launch(env, other):
    env.flow.side_effect = None
    env.flow.return_value = result(f"{READY}\n{other}")
    before = copy.deepcopy(env.identity)
    response = run()
    assert not response["ok"]
    env.flow.assert_awaited_once()
    assert env.identity == before


@pytest.mark.parametrize("status", [None, 0, [], {}, "", "unexpected"])
def test_malformed_terminal_status_fails_closed_without_crashing(env, status):
    broken = {"state": {"account_id": ACCOUNT_ID, "outcome_unknown": False, "status": status}}
    assert yuanying.is_public_yuanying_unresolved(broken)


def test_owned_status_rejection_keeps_game_reason_without_calibrating(env):
    rejected = result("\u5143\u5a74\u5c1a\u672a\u82cf\u9192\uff0c\u6682\u4e0d\u53ef\u51fa\u7a8d\u3002")
    rejected["data"]["actionResult"]["ok"] = False
    env.flow.side_effect = [rejected]
    before = copy.deepcopy(env.identity)
    response = run()
    assert not response["ok"]
    assert rejected["data"]["actionResult"]["rawMessage"] in response["message"]
    assert env.identity == before
    env.flow.assert_awaited_once()
