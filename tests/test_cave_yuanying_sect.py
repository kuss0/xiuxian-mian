import asyncio

import pytest

from model import state as state_module
from model.features import cave_treasure_miniapp as api
from model.features import cave_treasure_runtime as cave
from model.features import yuanying
from model.webapp_core import MiniAppHttpResult
from test_cave_yuanying_lifecycle import (
    env as env, IDENTITY_ID, PLAYER_ID, NOW, READY, COOLDOWN, result, record, run, install_unknown,
)


RETREAT_STARTED = "你心念一动，元婴已在你丹田的次元空间内开始闭关，它将为你持续提供修为。"
RETREAT_STATUS = "你的本命元婴\n状态: 元婴闭关\n已积累修为: 约 12238 点 (发言时自动结算)"


def select_sect():
    state_module.update_send_as_profile(IDENTITY_ID, sect_name=yuanying.YUANYING_SECT_NAME)


def test_sect_retreat_is_allowed_but_not_a_generic_read():
    assert api.normalize_cave_tianjige_command(yuanying.CMD_YUANYING_SECT_RETREAT) == yuanying.CMD_YUANYING_SECT_RETREAT
    assert yuanying.CMD_YUANYING_SECT_RETREAT not in api.CAVE_TIANJIGE_READ_ONLY_COMMANDS


def test_public_sect_dispatch_uses_original_strategy_and_confirms_retreat(env):
    select_sect()
    env.flow.side_effect = [result(READY), result(RETREAT_STARTED)]
    response = run()
    assert [call.kwargs["command"] for call in env.flow.await_args_list] == [
        yuanying.CMD_YUANYING_STATUS, yuanying.CMD_YUANYING_SECT_RETREAT,
    ]
    assert response["ok"]
    assert response["extra"]["launched"]
    assert record()["command"] == yuanying.CMD_YUANYING_SECT_RETREAT
    assert record()["status"] == "confirmed"
    assert env.identity["yuanying_phase"] == "running"
    assert env.identity["next_yuanying_time"] == NOW + cave.CAVE_YUANYING_STATUS_RECHECK_SEC
    yuanying.send_game_command.assert_not_awaited()


def test_existing_sect_retreat_is_only_observed(env):
    select_sect()
    env.flow.side_effect = [result(RETREAT_STATUS)]
    response = run()
    assert response["ok"] and not response["extra"]["launched"]
    assert env.flow.await_count == 1
    yuanying.send_game_command.assert_not_awaited()


@pytest.mark.parametrize("message,reconciled", [(RETREAT_STATUS, True), (COOLDOWN, False), (READY, False)])
def test_unknown_sect_launch_requires_retreat_postcondition(env, message, reconciled):
    select_sect()
    install_unknown()
    record()["command"] = yuanying.CMD_YUANYING_SECT_RETREAT
    env.flow.side_effect = [result(message)]
    response = run()
    assert response["ok"] is reconciled
    assert record()["outcome_unknown"] is (not reconciled)
    assert yuanying.is_public_yuanying_unresolved(
        state_module.get_miniapp_state_records()[f"{IDENTITY_ID}:cave_yuanying"]
    ) is (not reconciled)
    assert env.flow.await_count == 1
    yuanying.send_game_command.assert_not_awaited()


@pytest.mark.parametrize("stage", ["session", "status"])
def test_sect_change_during_read_cancels_dispatch(env, stage):
    async def changed(*_args, **_kwargs):
        select_sect()
        return env.loader.return_value if stage == "session" else result(READY)

    if stage == "session":
        env.loader.side_effect = changed
    else:
        env.flow.side_effect = changed
    response = run()
    assert not response["ok"]
    assert env.flow.await_count == (0 if stage == "session" else 1)


@pytest.mark.parametrize("command", [yuanying.CMD_YUANYING, yuanying.CMD_YUANYING_STATUS])
def test_retreat_success_does_not_confirm_a_different_command(env, command):
    synced = asyncio.run(cave.sync_cave_tianjige_yuanying_result(
        IDENTITY_ID, result(RETREAT_STARTED)["data"], now=NOW, command=command,
    ))
    assert not synced["handled"]


@pytest.mark.parametrize("command", [None, False, [], {}, ".unreviewed"])
def test_malformed_unknown_command_cannot_be_reconciled(env, command):
    install_unknown()
    record()["command"] = command
    env.flow.side_effect = [result(COOLDOWN)]
    response = run()
    assert not response["ok"]
    assert record()["outcome_unknown"]
    assert env.flow.await_count == 1


def test_conflicting_retreat_status_does_not_close_unknown(env):
    install_unknown()
    record()["command"] = yuanying.CMD_YUANYING_SECT_RETREAT
    env.flow.side_effect = [result(f"{RETREAT_STATUS}\n{COOLDOWN}")]
    response = run()
    assert not response["ok"]
    assert record()["outcome_unknown"]


def test_sect_change_after_dispatch_keeps_original_command_result(env):
    select_sect()

    async def flow(*_args, command, **_kwargs):
        if command == yuanying.CMD_YUANYING_STATUS:
            return result(READY)
        assert record()["command"] == command == yuanying.CMD_YUANYING_SECT_RETREAT
        state_module.update_send_as_profile(IDENTITY_ID, sect_name="天星宗")
        return result(RETREAT_STARTED)

    env.flow.side_effect = flow
    response = run()
    assert response["ok"]
    assert record()["command"] == yuanying.CMD_YUANYING_SECT_RETREAT
    assert record()["status"] == "confirmed"
    assert env.flow.await_count == 2


@pytest.mark.parametrize("status,unknown", [(0, True), (500, True), (429, False)])
def test_retreat_transport_is_single_attempt_and_unknown_is_retained(env, monkeypatch, status, unknown):
    calls = []

    def execute(request, *_args, **kwargs):
        calls.append(request["payload"]["command"])
        assert kwargs["backoff_sec"] == ()
        return MiniAppHttpResult(ok=False, data={}, attempts=1, status_code=status, error="fixture-failure")

    monkeypatch.setattr(api, "execute_miniapp_http_request", execute)
    response = asyncio.run(api.run_cave_tianjige_command_production_flow(
        IDENTITY_ID, token="fixture-token", webview_url="https://example.invalid/app",
        command=yuanying.CMD_YUANYING_SECT_RETREAT, init_data="fixture-init", player_id=PLAYER_ID,
    ))
    assert calls == [yuanying.CMD_YUANYING_SECT_RETREAT]
    assert response["action_dispatched"]
    assert response["outcome_unknown"] is unknown


def test_unknown_retreat_followup_never_dispatches_again(env):
    select_sect()
    env.flow.side_effect = [result(READY), {
        "ok": False, "status": "failed", "error": "timeout", "action_dispatched": True,
        "outcome_unknown": True, "data": {},
    }]
    assert not run()["ok"]
    assert record()["command"] == yuanying.CMD_YUANYING_SECT_RETREAT
    env.clock.now = NOW + 12 * 3600
    env.flow.reset_mock()
    env.flow.side_effect = [result(READY)]
    assert not run(env.clock.now)["ok"]
    assert env.flow.await_count == 1
    assert env.flow.await_args.kwargs["command"] == yuanying.CMD_YUANYING_STATUS
    assert record()["outcome_unknown"]


@pytest.mark.parametrize("suffix", ["已结束", "未开始", "失败"])
def test_retreat_status_prefix_is_not_a_running_postcondition(env, suffix):
    install_unknown()
    record()["command"] = yuanying.CMD_YUANYING_SECT_RETREAT
    env.flow.side_effect = [result("状态: 元婴闭关" + suffix)]
    response = run()
    assert not response["ok"]
    assert record()["outcome_unknown"]


@pytest.mark.parametrize("failure", [False, None, "exception"])
@pytest.mark.parametrize("sect", [False, True])
def test_no_dispatch_without_persisted_intent(env, failure, sect):
    if sect:
        select_sect()

    def save():
        if record().get("status") != "dispatching":
            return True
        if failure == "exception":
            raise OSError("fixture-write-failure")
        return failure

    env.save.side_effect = save
    response = run()
    assert not response["ok"]
    assert response["extra"]["status"] == "persistence_pending"
    assert response["extra"]["action_dispatched"] is False
    assert env.flow.await_count == 1
    assert record()["outcome_unknown"]
    assert env.identity["next_yuanying_time"] >= NOW + cave.CAVE_YUANYING_UNKNOWN_RECHECK_SEC
