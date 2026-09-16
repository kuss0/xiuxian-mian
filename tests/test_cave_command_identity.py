import asyncio
import copy
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_miniapp as api
from model.features import cave_treasure_runtime as cave
from model.features import tianti, yinluo, yuanying
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppRequestPolicy, miniapp_retry_after_sec


IDENTITY_ID = 990440001
ACCOUNT_ID = 7441
PLAYER_ID = -1_000_000_000_000 - IDENTITY_ID
NOW = 1_700_000_000.0
ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE44"
PANELS = {
    "yuanying": "【元婴状态】\n状态: 元婴闭关",
    "tianti": (
        "【凌霄云阶】\n当前进度：3 / 12 阶\n已完成周天：1 轮\n"
        "罡风淬体：2 / 12 层\n登阶冷却：可用\n问心状态：今日尚未问心"
    ),
    "read_only": (
        "**【fixture的阴罗幡】**\n**本命魔兵：** `乌龙幡`\n"
        "**幡体等阶：** 玄阶\n**煞气池：** 120 / 500 (24%)\n"
        "- **1号槽：** `[空闲]`"
    ),
}
READY = "\u3010\u5143\u5a74\u72b6\u6001\u3011\n\u72b6\u6001: \u7a8d\u4e2d\u6e29\u517b"


def command_payload(player_id=PLAYER_ID, *, message=READY):
    return {
        "ok": True, "account": {"playerId": player_id},
        "actionResult": {"ok": True, "completed": True, "rawMessage": message},
    }


def unowned_payload(variant, *, message=READY):
    raw = command_payload(message=message)
    if variant in {"missing", "selector_only"}:
        raw["account"].pop("playerId")
    elif variant == "contradiction":
        raw["identity"] = {"selectedPlayerId": ACCOUNT_ID}
    else:
        raw["account"]["playerId"] = {
            "other": ACCOUNT_ID, "bool": True, "float": float(IDENTITY_ID), "null": None, "container": [],
        }[variant]
    if variant == "selector_only":
        raw["identity"] = {"selectedPlayerId": PLAYER_ID}
    return raw


def run_flow(transport, *, command=yuanying.CMD_YUANYING, **kwargs):
    adapter = replace(api.build_cave_treasure_miniapp_adapter(), request_policy=MiniAppRequestPolicy(
        min_interval_sec=0, max_requests_per_run=1,
    ))
    return api.run_cave_tianjige_command_production_flow(
        IDENTITY_ID, token="df_FIXTURE44", webview_url=ENTRY, command=command,
        init_data="fixture-init", player_id=PLAYER_ID, transport=transport, adapter=adapter, **kwargs,
    )


@pytest.fixture
def command_env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY_ID, ACCOUNT_ID)
    identity = state_module.get_identity_state(IDENTITY_ID)
    identity.update(yuanying_enabled=True, yuanying_phase="running", next_yuanying_time=NOW - 1)
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(cave, "_capture_store", Mock(return_value=None))
    monkeypatch.setattr(cave, "save_state", Mock())
    monkeypatch.setattr(cave, "send_audit_log", AsyncMock())
    monkeypatch.setattr(yuanying, "save_state", Mock())
    monkeypatch.setattr(yuanying, "send_audit_log", AsyncMock())
    monkeypatch.setattr(yuanying, "send_game_command", AsyncMock())
    monkeypatch.setattr(tianti, "save_state", Mock())
    monkeypatch.setattr(yinluo, "save_state", Mock())
    session = {"ok": True, "player_id": PLAYER_ID, "init_data": "fixture-init", "result": {
        "ok": True, "data": {"raw": command_payload()},
    }}
    loader = AsyncMock(return_value=session)
    flow = AsyncMock(return_value={"ok": True, "data": command_payload()})
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_tianjige_command_production_flow", flow)
    yield SimpleNamespace(identity=identity, loader=loader, flow=flow, session=session)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


@pytest.mark.parametrize("command", sorted(api.CAVE_TIANJIGE_ALLOWED_COMMANDS))
def test_command_builder_requires_explicit_valid_player(command):
    with pytest.raises(TypeError):
        api.build_cave_tianjige_command_request(command, token="df_FIXTURE44")
    for invalid in (None, True, False, 0, "", -1001, float(IDENTITY_ID), [], {}):
        with pytest.raises(ValueError):
            api.build_cave_tianjige_command_request(command, token="df_FIXTURE44", player_id=invalid)
    request = api.build_cave_tianjige_command_request(command, token="df_FIXTURE44", player_id=PLAYER_ID)
    assert request["payload"]["playerId"] == PLAYER_ID


@pytest.mark.parametrize("player_id", [None, True, False, 0, "", -1001, float(IDENTITY_ID), ACCOUNT_ID, []])
def test_command_selection_is_checked_before_auth_or_http(monkeypatch, player_id):
    auth = AsyncMock(return_value="fixture-init")
    monkeypatch.setattr(api, "request_cave_treasure_miniapp_init_data", auth)
    transport = Mock(return_value=command_payload())
    result = asyncio.run(api.run_cave_tianjige_command_production_flow(
        IDENTITY_ID, token="df_FIXTURE44", webview_url=ENTRY,
        command=yuanying.CMD_YUANYING, player_id=player_id, transport=transport,
    ))
    assert not result["ok"]
    assert result["error"].startswith("cave_action_player_")
    auth.assert_not_awaited()
    transport.assert_not_called()


@pytest.mark.parametrize("command", [None, "", ".unknown", ".元婴出窍 extra"])
def test_command_whitelist_is_checked_before_auth_or_http(monkeypatch, command):
    auth = AsyncMock(return_value="fixture-init")
    monkeypatch.setattr(api, "request_cave_treasure_miniapp_init_data", auth)
    transport = Mock(return_value=command_payload())
    result = asyncio.run(api.run_cave_tianjige_command_production_flow(
        IDENTITY_ID, token="df_FIXTURE44", webview_url=ENTRY,
        command=command, player_id=PLAYER_ID, transport=transport,
    ))
    assert not result["ok"]
    auth.assert_not_awaited()
    transport.assert_not_called()


@pytest.mark.parametrize("variant", ["missing", "selector_only", "other", "bool", "float", "null", "container", "contradiction"])
def test_command_adapter_rejects_unowned_response_without_exporting_business_data(variant):
    transport = Mock(return_value={"ok": True, "data": unowned_payload(variant)})
    result = asyncio.run(run_flow(transport))
    assert not result["ok"]
    assert result["status"] == "identity_unverified"
    assert not result["data"]
    assert result["action_dispatched"]
    assert result["outcome_unknown"]
    assert result["events"][0]["status_code"] == 200
    transport.assert_called_once()


@pytest.mark.parametrize("command", sorted(api.CAVE_TIANJIGE_ALLOWED_COMMANDS))
@pytest.mark.parametrize("player_id", [PLAYER_ID, IDENTITY_ID])
def test_command_transport_preserves_selected_channel_and_accepts_its_normalized_reply(command, player_id):
    transport = Mock(return_value={"ok": True, "data": command_payload(player_id)})
    result = asyncio.run(run_flow(transport, command=command))
    assert result["ok"]
    assert result["action_dispatched"]
    assert not result["outcome_unknown"]
    assert transport.call_args.args[0]["payload"]["playerId"] == PLAYER_ID
    assert transport.call_args.args[0]["payload"]["command"] == command


@pytest.mark.parametrize("command", [yuanying.CMD_YUANYING_STATUS, yuanying.CMD_YUANYING])
@pytest.mark.parametrize("status", [429, 503])
def test_command_transport_keeps_rejection_uncertainty_and_retry_after(command, status):
    transport = Mock(return_value=SimpleNamespace(
        status_code=status, headers={"Retry-After": "50000"}, json=lambda: {"ok": False, "error": "fixture-limit"},
    ))
    result = asyncio.run(run_flow(transport, command=command))
    assert not result["ok"]
    assert result["action_dispatched"]
    assert result["outcome_unknown"] is (status == 503 and command == yuanying.CMD_YUANYING)
    assert result["events"][0]["status_code"] == status
    assert miniapp_retry_after_sec(result) == 50000
    transport.assert_called_once()


def test_cancelled_command_flow_carries_the_confirmed_dto_after_draining_http():
    entered, release = threading.Event(), threading.Event()

    def transport(_request):
        entered.set()
        assert release.wait(3)
        return command_payload()

    async def run():
        task = asyncio.create_task(run_flow(transport))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as raised:
                await task
            result = raised.value.result
            assert isinstance(result, dict)
            assert result["ok"]
            assert result["action_dispatched"]
            assert result["data"]["account"]["playerId"] == PLAYER_ID
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


@pytest.mark.parametrize("variant", ["missing", "selector_only", "other", "float", "contradiction"])
def test_yuanying_bridge_rejects_unowned_ready_status_before_any_state_change(command_env, variant):
    before = copy.deepcopy(state_module._meta_state)
    result = asyncio.run(cave.sync_cave_tianjige_yuanying_result(
        IDENTITY_ID, unowned_payload(variant), now=NOW, command=yuanying.CMD_YUANYING_STATUS,
    ))
    assert not result["handled"]
    assert result["reason"].startswith("cave_action_player_")
    assert state_module._meta_state == before


@pytest.mark.parametrize("completed", [False, 0, "false", None])
def test_yuanying_bridge_requires_typed_completed_receipt(command_env, completed):
    raw = command_payload()
    raw["actionResult"]["completed"] = completed
    before = copy.deepcopy(state_module._meta_state)
    result = asyncio.run(cave.sync_cave_tianjige_yuanying_result(
        IDENTITY_ID, raw, now=NOW, command=yuanying.CMD_YUANYING_STATUS,
    ))
    assert not result["handled"]
    assert state_module._meta_state == before


def run_public_command(feature):
    calls = {
        "yuanying": lambda: cave.run_cave_public_yuanying(IDENTITY_ID, ENTRY, now=NOW),
        "tianti": lambda: cave.run_cave_public_tianti_status(IDENTITY_ID, ENTRY, now=NOW),
        "read_only": lambda: cave.run_cave_public_tianjige_read_only(IDENTITY_ID, ENTRY, ".我的阴罗幡", now=NOW),
    }
    return asyncio.run(calls[feature]())


@pytest.mark.parametrize("feature", PANELS)
def test_public_command_callers_accept_matching_native_data(command_env, feature):
    h = command_env
    h.flow.return_value["data"] = command_payload(message=PANELS[feature])
    result = run_public_command(feature)
    assert result["ok"]
    h.flow.assert_awaited_once()
    if feature == "yuanying":
        assert h.identity["next_yuanying_time"] == NOW + cave.CAVE_YUANYING_STATUS_RECHECK_SEC
    elif feature == "tianti":
        assert h.identity["tianti_progress_current"] == 3
        assert h.identity["tianti_progress_total"] == 12
    else:
        assert h.identity["yinluo_observation"]["sha_current"] == 120
        assert h.identity["yinluo_observation"]["empty_slot_numbers"] == [1]


@pytest.mark.parametrize("feature", PANELS)
@pytest.mark.parametrize("stage", ["session", "result"])
def test_public_command_callers_reject_unowned_native_data(command_env, feature, stage):
    h = command_env
    h.flow.return_value["data"] = command_payload(message=PANELS[feature])
    if stage == "session":
        h.session["result"]["data"]["raw"] = unowned_payload("selector_only", message=PANELS[feature])
    else:
        h.flow.return_value["data"] = unowned_payload("selector_only", message=PANELS[feature])
    before = copy.deepcopy(state_module._meta_state)
    result = run_public_command(feature)
    assert not result["ok"]
    assert "cave_action_player_" in result["message"]
    assert state_module._meta_state == before
    if stage == "session":
        h.flow.assert_not_awaited()
    else:
        h.flow.assert_awaited_once()
