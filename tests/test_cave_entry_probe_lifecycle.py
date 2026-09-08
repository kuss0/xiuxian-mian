import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model import ui
from model.features import cave_treasure_runtime as cave


@pytest.fixture
def probe_harness(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    identity_id = 990380001
    state_module.set_identity_account(identity_id, 7401)
    url = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE38"
    now = 1_700_000_000.0
    config = {
        "cave_public_entry_url": url,
        "cave_public_entry_urls": [url],
        "cave_public_entry_token_blocked_signature": cave.cave_public_entry_urls_signature([url]),
        "cave_public_entry_token_blocked_at": now - 7200,
        "cave_public_entry_token_retry_at": now - 1,
        "cave_public_entry_token_canary_at": now,
        "cave_public_entry_token_failure_count": 3,
        "cave_public_entry_token_blocked_reason": "dwelling_token_expired",
    }
    state_module.set_miniapp_auto_config(config)
    save = Mock()
    start = AsyncMock(return_value={
        "ok": True,
        "data": {"overview": {"player_id": identity_id}, "raw": {}},
    })
    monkeypatch.setattr(cave, "save_state", save)
    monkeypatch.setattr(cave, "_capture_store", Mock())
    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", AsyncMock(return_value="fixture-init"))
    monkeypatch.setattr(cave, "run_cave_dwelling_start_production_flow", start)
    yield SimpleNamespace(identity_id=identity_id, url=url, now=now, config=config, save=save, start=start)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def assert_cancelled_without_health_backoff(h, result):
    assert not result["ok"]
    expected = {**h.config, "cave_public_entry_token_canary_at": 0}
    assert state_module.get_miniapp_auto_config() == expected
    assert result.get("extra", {}).get("status") == "cancelled"
    assert cave.get_cave_public_entry_gate(now=h.now + 1)["canary_due"]


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "disabled", "paused"])
def test_local_invalidation_during_real_loader_does_not_poison_shared_entry(probe_harness, monkeypatch, change):
    h = probe_harness

    async def init_data(*_args, **_kwargs):
        if change == "removed":
            state_module.remove_identity(h.identity_id)
        elif change == "replaced":
            state_module.remove_identity(h.identity_id)
            state_module.set_identity_account(h.identity_id, 7401)
        elif change == "rebound":
            state_module.set_identity_account(h.identity_id, 7402)
        elif change == "disabled":
            state_module.set_identity_enabled(h.identity_id, False)
        else:
            state_module.set_global_enabled(False)
            state_module.set_global_pause_source("manual")
        return "fixture-init"

    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", init_data)
    result = asyncio.run(cave.probe_cave_public_entry(h.identity_id, h.url, now=h.now))
    assert_cancelled_without_health_backoff(h, result)
    h.start.assert_not_awaited()
    h.save.assert_called_once()


def test_explicit_cancelled_loader_result_does_not_become_entry_failure(probe_harness, monkeypatch):
    h = probe_harness
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", AsyncMock(return_value={
        "ok": False, "status": "cancelled", "error": "operation invalidated",
    }))
    result = asyncio.run(cave.probe_cave_public_entry(h.identity_id, h.url, now=h.now))
    assert_cancelled_without_health_backoff(h, result)


def test_task_cancellation_releases_only_the_owned_canary_and_propagates(probe_harness, monkeypatch):
    h = probe_harness

    async def cancelled_init(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", cancelled_init)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cave.probe_cave_public_entry(h.identity_id, h.url, now=h.now))
    assert state_module.get_miniapp_auto_config() == {**h.config, "cave_public_entry_token_canary_at": 0}
    h.start.assert_not_awaited()


def test_probe_cannot_use_a_canary_claim_owned_by_another_tick(probe_harness, monkeypatch):
    h = probe_harness
    replacement = {**h.config, "cave_public_entry_token_canary_at": h.now + 1}
    state_module.set_miniapp_auto_config(replacement)
    loader = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    result = asyncio.run(cave.probe_cave_public_entry(h.identity_id, h.url, now=h.now))
    assert not result["ok"]
    assert result.get("extra", {}).get("status") == "cancelled"
    assert state_module.get_miniapp_auto_config() == replacement
    loader.assert_not_awaited()
    h.save.assert_not_called()


def test_task_cancellation_cannot_release_a_replacement_canary(probe_harness, monkeypatch):
    h = probe_harness
    replacement = {**h.config, "cave_public_entry_token_canary_at": h.now + 1}

    async def cancelled_init(*_args, **_kwargs):
        state_module.set_miniapp_auto_config(replacement)
        raise asyncio.CancelledError

    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", cancelled_init)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cave.probe_cave_public_entry(h.identity_id, h.url, now=h.now))
    assert state_module.get_miniapp_auto_config() == replacement
    h.save.assert_not_called()


@pytest.mark.parametrize("changed", ["claim", "entry_urls"])
@pytest.mark.parametrize("response_ok", [False, True])
def test_old_probe_result_cannot_overwrite_new_claim_or_entry_generation(probe_harness, monkeypatch, changed, response_ok):
    h = probe_harness
    replacement = {**h.config, "cave_public_entry_token_canary_at": h.now + 1}
    if changed == "entry_urls":
        fresh = "https://t.me/hantianzun99_bot?startapp=df_FRESH38"
        replacement.update({
            "cave_public_entry_url": fresh,
            "cave_public_entry_urls": [fresh, h.url],
            "cave_public_entry_token_blocked_signature": cave.cave_public_entry_urls_signature([fresh, h.url]),
            "cave_public_entry_token_canary_at": h.now,
        })

    async def load(*_args, **_kwargs):
        state_module.set_miniapp_auto_config(replacement)
        return {"ok": response_ok, "error": "dwelling_token_expired" if not response_ok else ""}

    monkeypatch.setattr(cave, "_load_cave_public_identity_session", load)
    result = asyncio.run(cave.probe_cave_public_entry(h.identity_id, h.url, now=h.now))
    assert not result["ok"]
    assert result.get("extra", {}).get("status") == "cancelled"
    assert state_module.get_miniapp_auto_config() == replacement
    h.save.assert_not_called()


@pytest.mark.parametrize("response_ok", [False, True])
def test_rebound_owner_cannot_publish_old_probe_result(probe_harness, monkeypatch, response_ok):
    h = probe_harness

    async def load(*_args, **_kwargs):
        state_module.set_identity_account(h.identity_id, 7402)
        return {"ok": response_ok, "error": "dwelling_token_expired" if not response_ok else ""}

    monkeypatch.setattr(cave, "_load_cave_public_identity_session", load)
    result = asyncio.run(cave.probe_cave_public_entry(h.identity_id, h.url, now=h.now))
    assert_cancelled_without_health_backoff(h, result)


@pytest.mark.parametrize("outcome", ["ok", "token_expired", "upstream_failed"])
def test_current_probe_keeps_real_server_health_transitions(probe_harness, outcome):
    h = probe_harness
    if outcome != "ok":
        h.start.return_value = {
            "ok": False,
            "error": "dwelling_token_expired" if outcome == "token_expired" else "HTTP 502 returned non JSON",
        }
    result = asyncio.run(cave.probe_cave_public_entry(h.identity_id, h.url, now=h.now))
    config = state_module.get_miniapp_auto_config()
    assert result["ok"] is (outcome == "ok")
    assert config["cave_public_entry_token_canary_at"] == 0
    if outcome == "ok":
        assert config["cave_public_entry_token_failure_count"] == 0
        assert not cave.get_cave_public_entry_gate(now=h.now + 1)["blocked"]
    else:
        delay = cave.CAVE_PUBLIC_ENTRY_RETRY_MAX_SEC if outcome == "token_expired" else cave.CAVE_PUBLIC_ENTRY_RETRY_BASE_SEC
        assert config["cave_public_entry_token_retry_at"] == h.now + delay
        assert config["cave_public_entry_token_failure_count"] == (4 if outcome == "token_expired" else 3)
    h.start.assert_awaited_once()
    h.save.assert_called_once()


def test_channel_freeze_and_tianzun_maintenance_still_allow_canary_http(probe_harness):
    h = probe_harness
    state_module.set_identity_enabled(h.identity_id, False)
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [h.identity_id]})
    state_module.set_global_enabled(False)
    state_module.set_global_pause_source("tianzun_maintenance")
    result = asyncio.run(cave.probe_cave_public_entry(h.identity_id, h.url, now=h.now))
    assert result["ok"]
    h.start.assert_awaited_once()


def test_scheduler_moves_to_next_available_identity_after_cancelled_canary(probe_harness, monkeypatch):
    h = probe_harness
    next_id = h.identity_id + 1
    state_module.set_identity_account(next_id, 7401)
    state_module.set_miniapp_auto_config({**h.config, "cave_public_entry_token_canary_at": 0})
    identities = []

    async def init_data(identity_id, **_kwargs):
        identities.append(identity_id)
        if identity_id == h.identity_id:
            state_module.set_identity_enabled(identity_id, False)
        return "fixture-init"

    h.start.return_value = {"ok": True, "data": {"overview": {"player_id": next_id}, "raw": {}}}
    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", init_data)
    monkeypatch.setattr(ui, "maybe_send_cave_public_fate_cards_daily_summary", AsyncMock(return_value=False))
    monkeypatch.setattr(ui, "_cave_public_shared_hold", Mock(return_value={}))
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", state_module.get_miniapp_auto_config)
    monkeypatch.setattr(ui, "get_miniapp_auto_config_snapshot", Mock(return_value={}))
    monkeypatch.setattr(ui, "_run_tree_miniapp_daily_scheduler", AsyncMock())
    monkeypatch.setattr(ui, "_run_cave_public_background_scheduler", AsyncMock())

    async def run():
        cancelled = await ui.run_miniapp_daily_scheduler(h.now)
        assert not cancelled["started"]
        assert cancelled["reason"] == "entry_canary_cancelled"
        assert identities == [h.identity_id]
        assert state_module.get_miniapp_auto_config()["cave_public_entry_token_retry_at"] == h.config["cave_public_entry_token_retry_at"]
        assert state_module.get_miniapp_auto_config()["cave_public_entry_token_failure_count"] == 3
        completed = await ui.run_miniapp_daily_scheduler(h.now + 1)
        assert completed["started"] and completed["ok"]
        assert completed["identity_id"] == next_id

    asyncio.run(run())
    assert identities == [h.identity_id, next_id]
    h.start.assert_awaited_once()
    ui._run_tree_miniapp_daily_scheduler.assert_not_awaited()
    ui._run_cave_public_background_scheduler.assert_not_awaited()
