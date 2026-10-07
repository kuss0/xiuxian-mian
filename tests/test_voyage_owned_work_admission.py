"""Public voyage admission must respect existing concubine work."""

import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from model import state as state_module
from model import ui
from model.features import cave_treasure_runtime, concubine
from test_cave_tianjige_read_only import ENTRY, NOW, runtime as runtime, voyage_runtime as voyage_runtime


@pytest.mark.parametrize("command", [".远航归来", ".侍妾远航 月殿寻痕"])
@pytest.mark.parametrize("field,value", [
    ("concubine_phase", "heart_pending"),
    ("concubine_heart_msg_id", 42),
    ("concubine_status_query", {"invalid": True}),
    ("concubine_voyage_actions", {"invalid": True}),
    ("concubine_gift_actions", {"invalid": True}),
    ("concubine_fragment_actions", {"invalid": True}),
    ("pending_tasks", {42: {"cmd": ".共历心劫", "family": "concubine_heart"}}),
    ("pending_tasks", None),
])
def test_owned_work_blocks_before_login(voyage_runtime, command, field, value):
    env = voyage_runtime
    if command != ".远航归来":
        env.identity.update(concubine_availability="available", concubine_voyage_status="idle",
                            concubine_voyage_return_at=0)
    env.identity[field] = value
    before = copy.deepcopy(env.identity)
    response = asyncio.run(cave_treasure_runtime.run_cave_public_tianjige_action(1001, ENTRY, command, now=NOW))
    assert not response["ok"] and response["extra"]["status"] == "blocked"
    assert response["extra"]["reason"]
    assert env.identity == before
    env.session.assert_not_awaited()
    env.flow.assert_not_awaited()
    env.save.assert_not_called()
    env.audit.assert_not_awaited()


@pytest.mark.parametrize("kind", ["return", "launch"])
def test_admission_block_never_falls_back_to_command(voyage_runtime, monkeypatch, kind):
    env = voyage_runtime
    if kind == "launch":
        env.identity.update(concubine_availability="available", concubine_voyage_status="idle",
                            concubine_voyage_return_at=0)
    monkeypatch.setattr(concubine, "get_miniapp_auto_config", lambda: {"cave_public_entry_url": ENTRY})
    entry = AsyncMock(return_value=(False, "owned work pending", {"status": "blocked", "reason": "active_pending"}))
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", entry)
    command = AsyncMock()
    monkeypatch.setattr(concubine.voyage_actions, "send", command)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(1001):
        action = concubine._send_voyage_return_command if kind == "return" else concubine._send_voyage_command
        assert asyncio.run(action(NOW)) is False
    command.assert_not_awaited()
    assert env.identity == before
    env.save.assert_not_called()


def test_unrelated_work_does_not_block_return(voyage_runtime):
    env = voyage_runtime
    env.identity.update(deep_retreat_phase="running", next_deep_retreat_time=NOW + 3600,
                        pending_tasks={42: {"cmd": ".状态", "family": "status"}})
    assert asyncio.run(cave_treasure_runtime.run_cave_public_tianjige_action(1001, ENTRY, ".远航归来", now=NOW))["ok"]
    env.flow.assert_awaited_once()


@pytest.mark.parametrize("frozen", [False, True])
def test_public_identity_gate_still_distinguishes_manual_disable(voyage_runtime, frozen):
    env = voyage_runtime
    state_module.set_identity_enabled(1001, False)
    state_module._meta_state["channel_send_as_health"] = {
        "status": "closed", "restore_identity_ids": [1001] if frozen else [],
    }
    response = asyncio.run(cave_treasure_runtime.run_cave_public_tianjige_action(1001, ENTRY, ".远航归来", now=NOW))
    assert response["ok"] is frozen
    if frozen:
        env.flow.assert_awaited_once()
    else:
        env.session.assert_not_awaited()


def test_disabled_launch_switch_preserves_confirmed_settlement(voyage_runtime):
    env = voyage_runtime
    env.identity["concubine_voyage_enabled"] = False
    assert asyncio.run(cave_treasure_runtime.run_cave_public_tianjige_action(1001, ENTRY, ".远航归来", now=NOW))["ok"]
    assert env.identity["concubine_voyage_enabled"] is False


@pytest.mark.parametrize("outcome", ["definitely_unsent", "dispatched"])
def test_non_admission_outcomes_keep_existing_policy(voyage_runtime, monkeypatch, outcome):
    monkeypatch.setattr(concubine, "get_miniapp_auto_config", lambda: {"cave_public_entry_url": ENTRY})
    entry = AsyncMock(return_value=(False, "fixture failure", {"action_dispatched": outcome == "dispatched"}))
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", entry)
    with state_module.use_identity(1001):
        result = asyncio.run(concubine._send_voyage_miniapp_command("return", NOW))
    if outcome == "definitely_unsent":
        assert result is None
        voyage_runtime.save.assert_not_called()
    else:
        assert result is False
        assert voyage_runtime.identity["concubine_voyage_retry_count"] == 2
        voyage_runtime.save.assert_called_once_with()


def test_status_read_keeps_existing_command_fallback(voyage_runtime, monkeypatch):
    monkeypatch.setattr(concubine, "get_miniapp_auto_config", lambda: {"cave_public_entry_url": ENTRY})
    entry = AsyncMock(return_value=(False, "status unavailable", {"status": "blocked"}))
    monkeypatch.setattr(ui, "ui_run_cave_public_entry", entry)
    before = copy.deepcopy(voyage_runtime.identity)
    with state_module.use_identity(1001):
        assert asyncio.run(concubine._send_voyage_miniapp_command("status", NOW)) is None
    assert voyage_runtime.identity == before
    voyage_runtime.save.assert_not_called()
