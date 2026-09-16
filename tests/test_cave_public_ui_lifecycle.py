import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model import ui
from model.features import cave_treasure_runtime as cave, tianti


@pytest.fixture
def public_ui(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    identity_id = 990390001
    state_module.set_identity_account(identity_id, 7401)
    now = 1_700_000_000.0
    urls = [
        "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE391",
        "https://t.me/hantianzun99_bot?startapp=df_FIXTURE392",
    ]
    config = {"cave_public_entry_url": urls[0], "cave_public_entry_urls": urls}
    state_module.set_miniapp_auto_config(config)
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", state_module.get_miniapp_auto_config)
    monkeypatch.setattr(ui, "_cave_public_ui_run_lock", asyncio.Lock())
    monkeypatch.setattr(ui, "_cave_public_shared_hold", Mock(return_value={}))
    monkeypatch.setattr(ui, "_cave_public_background_state", {
        "running": False, "circuit_open_until": 0, "circuit_reason": "", "next_run_at": 0,
    })
    monkeypatch.setattr(ui.time, "time", lambda: now)
    monkeypatch.setattr(ui, "console_log", Mock())
    monkeypatch.setattr(ui, "maybe_send_cave_public_fate_cards_daily_summary", AsyncMock())
    save = Mock()
    monkeypatch.setattr(cave, "save_state", save)
    monkeypatch.setattr(ui, "save_state", save)
    monkeypatch.setattr(cave, "_capture_store", Mock(return_value=None))
    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", AsyncMock(return_value="fixture-init"))
    start = AsyncMock(return_value={
        "ok": True, "data": {"overview": {"player_id": identity_id}, "raw": {}},
    })
    monkeypatch.setattr(cave, "run_cave_dwelling_start_production_flow", start)
    runner = AsyncMock(return_value={"ok": True, "message": "fixture complete", "extra": {}})
    monkeypatch.setattr(ui, "run_cave_public_trial", runner)
    yield SimpleNamespace(identity_id=identity_id, now=now, urls=urls, config=config, save=save, start=start, runner=runner)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def block_entry(h):
    config = {
        **h.config,
        "cave_public_entry_token_blocked_signature": cave.cave_public_entry_urls_signature(h.urls),
        "cave_public_entry_token_blocked_at": h.now - 7200,
        "cave_public_entry_token_retry_at": h.now - 1,
        "cave_public_entry_token_canary_at": 0,
        "cave_public_entry_token_failure_count": 3,
        "cave_public_entry_token_blocked_reason": "dwelling_token_expired",
    }
    state_module.set_miniapp_auto_config(config)
    return config


async def read_entry(h, identity_id, url):
    token, webview_url, error = cave._parse_public_cave_entry_url(url)
    assert not error
    return await cave._load_cave_public_identity_session(
        identity_id, token, webview_url, now=h.now, capture_source="fixture-public-ui",
    )


@pytest.mark.parametrize("result", [
    {"ok": False, "message": "operation invalidated", "extra": {"status": "cancelled"}},
    {"ok": False, "message": "module disabled", "extra": {}},
    {"ok": True, "message": "request skipped", "extra": {"skipped": True}},
    {"ok": True, "message": "unverified success", "extra": {}},
])
def test_local_outcome_without_entry_read_cannot_restore_shared_health(public_ui, result):
    h = public_ui
    config = block_entry(h)
    ui._cave_public_background_state.update(circuit_open_until=h.now + 600, circuit_reason="old upstream failure")
    background = copy.deepcopy(ui._cave_public_background_state)
    h.runner.return_value = result
    asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert state_module.get_miniapp_auto_config() == config
    assert ui._cave_public_background_state == background
    h.runner.assert_awaited_once()
    h.start.assert_not_awaited()


def test_busy_entry_does_not_claim_a_canary(public_ui):
    h = public_ui
    config = block_entry(h)

    async def run():
        async with ui._cave_public_ui_run_lock:
            ok, _message, _extra = await ui.ui_run_cave_public_entry(h.identity_id, "trial", "")
            assert not ok

    asyncio.run(run())
    assert state_module.get_miniapp_auto_config() == config
    h.save.assert_not_called()
    h.runner.assert_not_awaited()


def test_native_tianti_session_limit_reaches_ui_without_entry_fallback(public_ui, monkeypatch):
    h = public_ui
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(tianti, "_TIANTI_RUN_LOCKS", {})
    monkeypatch.setattr(cave, "send_audit_log", AsyncMock())
    h.start.return_value = {
        "ok": False, "status": "failed", "error": "fixture-limit",
        "events": [{"status_code": 429, "retry_after_sec": 50000, "shared_rate_limit": True}],
    }
    ok, _message, extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "tianti_status", ""))
    assert not ok
    assert extra["shared_rate_limit"]
    assert extra["shared_retry_after_sec"] == 50000
    assert extra["shared_retry_at"] == h.now + 50000
    assert len(extra["entry_attempts"]) == 1
    h.start.assert_awaited_once()
    assert not ui._cave_public_background_state["circuit_open_until"]


def test_invalid_action_does_not_claim_a_canary(public_ui):
    h = public_ui
    config = block_entry(h)
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "not-a-game", ""))
    assert not ok
    assert state_module.get_miniapp_auto_config() == config
    h.save.assert_not_called()


@pytest.mark.parametrize("error", [asyncio.CancelledError, RuntimeError])
def test_interrupted_action_releases_its_claim_without_health_reclassification(public_ui, error):
    h = public_ui
    config = block_entry(h)
    h.runner.side_effect = error("fixture interruption")
    with pytest.raises(error):
        asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert state_module.get_miniapp_auto_config() == config
    assert not ui._cave_public_ui_run_lock.locked()
    h.runner.assert_awaited_once()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "disabled", "paused"])
def test_invalidated_owner_cannot_fall_back_to_another_entry(public_ui, change):
    h = public_ui

    async def action(_identity_id, _url):
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
        return {"ok": False, "message": "initial_start_failed: dwelling_token_expired", "extra": {}}

    h.runner.side_effect = action
    ok, _message, extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert not ok
    h.runner.assert_awaited_once()
    assert state_module.get_miniapp_auto_config() == h.config
    assert extra.get("status") == "cancelled"


@pytest.mark.parametrize("change", ["entry_urls", "claim"])
@pytest.mark.parametrize("outcome", ["success", "token_expired", "upstream_failed"])
def test_old_result_preserves_replacement_entry_health_and_does_not_fan_out(public_ui, change, outcome):
    h = public_ui
    config = block_entry(h)
    replacement = {**config, "cave_public_entry_token_canary_at": h.now + 1}
    if change == "entry_urls":
        urls = ["https://t.me/hantianzun100_bot?startapp=df_FRESH39", *h.urls]
        replacement.update({
            "cave_public_entry_url": urls[0], "cave_public_entry_urls": urls,
            "cave_public_entry_token_blocked_signature": cave.cave_public_entry_urls_signature(urls),
        })

    async def action(identity_id, url):
        if outcome == "success":
            assert (await read_entry(h, identity_id, url))["ok"]
        state_module.set_miniapp_auto_config(replacement)
        return {
            "ok": outcome == "success",
            "message": {
                "success": "completed",
                "token_expired": "initial_start_failed: dwelling_token_expired",
                "upstream_failed": "initial_start_failed: HTTP 502",
            }[outcome],
            "extra": {"rewards": {"fixture-material": 2}} if outcome == "success" else {},
        }

    h.runner.side_effect = action
    ok, _message, extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert ok is (outcome == "success")
    assert state_module.get_miniapp_auto_config() == replacement
    assert ui._cave_public_background_state["circuit_open_until"] == 0
    assert extra.get("entry_context_stale") is True
    h.runner.assert_awaited_once()
    if outcome == "success":
        assert extra["rewards"] == {"fixture-material": 2}


def test_real_entry_read_restores_health_even_when_business_result_is_negative(public_ui):
    h = public_ui
    block_entry(h)
    ui._cave_public_background_state.update(circuit_open_until=h.now + 600, circuit_reason="old upstream failure")

    async def action(identity_id, url):
        assert (await read_entry(h, identity_id, url))["ok"]
        return {"ok": False, "message": "insufficient cultivation", "extra": {}}

    h.runner.side_effect = action
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert not ok
    assert not cave.get_cave_public_entry_gate(now=h.now + 1)["blocked"]
    assert ui._cave_public_background_state["circuit_open_until"] == 0
    h.start.assert_awaited_once()
    h.runner.assert_awaited_once()


def test_verified_entry_does_not_replay_a_downstream_failure_at_another_url(public_ui):
    h = public_ui

    async def action(identity_id, url):
        assert (await read_entry(h, identity_id, url))["ok"]
        return {"ok": False, "message": "WebView external action failed", "extra": {}}

    h.runner.side_effect = action
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert not ok
    h.runner.assert_awaited_once()


def test_success_from_old_request_does_not_close_a_newer_upstream_circuit(public_ui):
    h = public_ui

    async def action(identity_id, url):
        assert (await read_entry(h, identity_id, url))["ok"]
        ui._cave_public_background_state.update(circuit_open_until=h.now + 900, circuit_reason="new upstream failure")
        return {"ok": True, "message": "complete", "extra": {}}

    h.runner.side_effect = action
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert ok
    assert ui._cave_public_background_state["circuit_open_until"] == h.now + 900
    assert ui._cave_public_background_state["circuit_reason"] == "new upstream failure"


def test_local_skip_after_expired_candidate_is_not_evidence_that_all_urls_expired(public_ui):
    h = public_ui
    h.runner.side_effect = [
        {"ok": False, "message": "initial_start_failed: dwelling_token_expired", "extra": {}},
        {"ok": True, "message": "\u6700\u5c0f\u95f4\u9694", "extra": {"skipped": True}},
    ]
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert not ok
    assert state_module.get_miniapp_auto_config() == h.config
    assert ui._cave_public_background_state["circuit_open_until"] == 0
    assert h.runner.await_count == 2


def test_expired_manual_url_cannot_block_different_configured_entries(public_ui):
    h = public_ui
    h.runner.return_value = {"ok": False, "message": "initial_start_failed: dwelling_token_expired", "extra": {}}
    old_url = "https://t.me/fanrenxiuxian_bot?startapp=df_OTHER39"
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", old_url))
    assert not ok
    assert state_module.get_miniapp_auto_config() == h.config
    assert ui._cave_public_background_state["circuit_open_until"] == 0


def test_all_configured_candidates_expired_still_sets_real_token_backoff(public_ui):
    h = public_ui
    h.runner.return_value = {"ok": False, "message": "initial_start_failed: dwelling_token_expired", "extra": {}}
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    config = state_module.get_miniapp_auto_config()
    assert not ok
    assert h.runner.await_count == 2
    assert config["cave_public_entry_token_blocked_signature"] == cave.cave_public_entry_urls_signature(h.urls)
    assert config["cave_public_entry_token_retry_at"] == h.now + cave.CAVE_PUBLIC_ENTRY_RETRY_BASE_SEC


def test_shared_rate_limit_still_stops_candidate_fanout(public_ui, monkeypatch):
    h = public_ui
    remember = Mock(return_value=h.now + 90)
    monkeypatch.setattr(ui, "_remember_cave_public_shared_limit", remember)
    h.runner.return_value = {
        "ok": False, "message": "external_action_rate_limited",
        "extra": {"shared_rate_limit": True, "shared_retry_after_sec": 90},
    }
    ok, _message, extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert not ok
    assert extra["shared_retry_at"] == h.now + 90
    remember.assert_called_once()
    h.runner.assert_awaited_once()


def test_old_manual_result_cannot_clear_new_failure_with_the_same_claim(public_ui):
    h = public_ui
    config = block_entry(h)
    replacement = {
        **config, "cave_public_entry_token_blocked_at": h.now + 1,
        "cave_public_entry_token_retry_at": h.now + 86400,
        "cave_public_entry_token_failure_count": 4,
    }

    async def action(identity_id, url):
        assert (await read_entry(h, identity_id, url))["ok"]
        state_module.set_miniapp_auto_config(replacement)
        return {"ok": True, "message": "complete", "extra": {}}

    h.runner.side_effect = action
    ok, _message, extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", h.urls[0]))
    assert ok
    assert extra.get("entry_context_stale") is True
    assert state_module.get_miniapp_auto_config() == replacement


def test_genuine_canary_http_failure_keeps_shared_retry_backoff(public_ui):
    h = public_ui
    block_entry(h)
    h.start.return_value = {"ok": False, "error": "HTTP 502 returned non JSON"}

    async def action(identity_id, url):
        session = await read_entry(h, identity_id, url)
        return {"ok": False, "message": f"initial_start_failed: {session['error']}", "extra": {}}

    h.runner.side_effect = action
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    config = state_module.get_miniapp_auto_config()
    assert not ok
    assert config["cave_public_entry_token_retry_at"] == h.now + cave.CAVE_PUBLIC_ENTRY_RETRY_BASE_SEC
    assert config["cave_public_entry_token_canary_at"] == 0
    assert config["cave_public_entry_token_failure_count"] == 3
    assert ui._cave_public_background_state["circuit_open_until"] > h.now
    h.runner.assert_awaited_once()


def test_http_failure_after_verified_entry_is_not_shared_upstream_recovery(public_ui):
    h = public_ui

    async def action(identity_id, url):
        assert (await read_entry(h, identity_id, url))["ok"]
        return {"ok": False, "message": "WebView external action failed: HTTP 502", "extra": {}}

    h.runner.side_effect = action
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert not ok
    assert ui._cave_public_background_state["circuit_open_until"] > h.now
    h.runner.assert_awaited_once()


@pytest.mark.parametrize("location", ["top_level", "extra"])
def test_explicit_local_cancellation_with_entry_error_wording_never_falls_back(public_ui, location):
    h = public_ui
    h.runner.return_value = {"ok": False, "message": "initial_start_failed: WebView cancelled", "extra": {}}
    target = h.runner.return_value if location == "top_level" else h.runner.return_value["extra"]
    target["status"] = "cancelled"
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert not ok
    h.runner.assert_awaited_once()
    assert state_module.get_miniapp_auto_config() == h.config


@pytest.mark.parametrize("change", ["owner", "entry", "pause", "rate_limit"])
def test_lock_acquisition_rechecks_context_before_claim_or_dispatch(public_ui, monkeypatch, change):
    h = public_ui
    config = block_entry(h)

    class ChangingLock:
        def locked(self):
            return False

        async def __aenter__(self):
            if change == "owner":
                state_module.set_identity_account(h.identity_id, 7402)
            elif change == "entry":
                state_module.set_miniapp_auto_config({**config, "cave_public_entry_urls": [h.urls[1]]})
            elif change == "pause":
                state_module.set_global_enabled(False)
                state_module.set_global_pause_source("manual")
            else:
                monkeypatch.setattr(ui, "_cave_public_shared_hold", Mock(return_value={"shared_rate_limit": True}))

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(ui, "_cave_public_ui_run_lock", ChangingLock())
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert not ok
    assert not state_module.get_miniapp_auto_config()["cave_public_entry_token_canary_at"]
    h.save.assert_not_called()
    h.runner.assert_not_awaited()


@pytest.mark.parametrize("change", ["rebind", "entry_urls", "claim"])
def test_loader_checks_ui_context_after_init_data_before_http(public_ui, change):
    h = public_ui

    async def get_init(*_args, **_kwargs):
        if change == "rebind":
            state_module.set_identity_account(h.identity_id, 7402)
        elif change == "entry_urls":
            state_module.set_miniapp_auto_config({**h.config, "cave_public_entry_urls": [h.urls[1]]})
        else:
            state_module.set_miniapp_auto_config({**h.config, "cave_public_entry_token_canary_at": h.now + 1})
        return "fixture-init"

    async def action(identity_id, url):
        session = await read_entry(h, identity_id, url)
        return {"ok": session["ok"], "message": session.get("error"), "extra": {}}

    cave.request_cave_treasure_miniapp_init_data.side_effect = get_init
    h.runner.side_effect = action
    ok, _message, extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert not ok
    assert extra.get("status") == "cancelled" or extra.get("entry_context_stale") is True
    h.start.assert_not_awaited()
    h.runner.assert_awaited_once()
    assert not state_module.get_miniapp_auto_config().get("cave_public_entry_token_blocked_signature")


def test_frozen_channel_and_maintenance_still_allow_public_entry(public_ui):
    h = public_ui
    state_module.set_identity_enabled(h.identity_id, False)
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [h.identity_id]})
    state_module.set_global_enabled(False)
    state_module.set_global_pause_source("tianzun_maintenance")

    async def action(identity_id, url):
        assert (await read_entry(h, identity_id, url))["ok"]
        return {"ok": True, "message": "complete", "extra": {}}

    h.runner.side_effect = action
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert ok
    h.start.assert_awaited_once()
    h.runner.assert_awaited_once()


def test_failed_manual_subset_is_not_evidence_all_configured_entries_expired(public_ui):
    h = public_ui
    h.runner.return_value = {"ok": False, "message": "initial_start_failed: dwelling_token_expired", "extra": {}}
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", h.urls[0]))
    assert not ok
    assert state_module.get_miniapp_auto_config() == h.config
    assert ui._cave_public_background_state["circuit_open_until"] == 0
    h.runner.assert_awaited_once()


def test_success_from_unconfigured_manual_url_cannot_clear_configured_health(public_ui):
    h = public_ui
    config = block_entry(h)
    ui._cave_public_background_state.update(circuit_open_until=h.now + 600, circuit_reason="configured entry failure")

    async def action(identity_id, url):
        assert (await read_entry(h, identity_id, url))["ok"]
        return {"ok": True, "message": "complete", "extra": {}}

    h.runner.side_effect = action
    ok, _message, _extra = asyncio.run(ui.ui_run_cave_public_entry(
        h.identity_id, "trial", "https://t.me/fanrenxiuxian_bot?startapp=df_OTHER39",
    ))
    assert ok
    assert state_module.get_miniapp_auto_config() == config
    assert ui._cave_public_background_state["circuit_open_until"] == h.now + 600


def test_daily_report_fault_keeps_confirmed_business_outcome(public_ui, monkeypatch, caplog):
    h = public_ui

    async def action(identity_id, url, **_kwargs):
        assert (await read_entry(h, identity_id, url))["ok"]
        return {"ok": True, "message": "claimed", "extra": {"rewards": {"fixture-material": 2}}}

    run = AsyncMock(side_effect=action)
    monkeypatch.setattr(ui, "run_cave_public_fate_cards", run)
    ui.maybe_send_cave_public_fate_cards_daily_summary.side_effect = RuntimeError("SECRET-NOTIFICATION-DETAIL")
    ok, message, extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "fate_cards", ""))
    assert ok
    assert message == "claimed"
    assert extra["rewards"] == {"fixture-material": 2}
    assert "RuntimeError" in caplog.text
    assert "SECRET-NOTIFICATION-DETAIL" not in caplog.text
    run.assert_awaited_once()


@pytest.mark.parametrize("change", ["disabled", "paused"])
def test_confirmed_outcome_for_same_owner_survives_post_dispatch_stop(public_ui, change):
    h = public_ui
    config = block_entry(h)

    async def action(identity_id, url):
        assert (await read_entry(h, identity_id, url))["ok"]
        if change == "disabled":
            state_module.set_identity_enabled(identity_id, False)
        else:
            state_module.set_global_enabled(False)
            state_module.set_global_pause_source("manual")
        return {"ok": True, "message": "claimed", "extra": {"rewards": {"fixture-material": 2}}}

    h.runner.side_effect = action
    ok, message, extra = asyncio.run(ui.ui_run_cave_public_entry(h.identity_id, "trial", ""))
    assert ok
    assert message == "claimed"
    assert extra["rewards"] == {"fixture-material": 2}
    assert extra.get("operation_cancelled") is True
    assert state_module.get_miniapp_auto_config() == config
    h.runner.assert_awaited_once()


def test_ui_action_surface_does_not_expand_batch_automation(public_ui):
    h = public_ui
    manual_only = ["inventory", "storage_bag", "tower", "tree", "deep_start", "deep_force", "beast_status"]
    assert all(ui._cave_public_entry_runner(h.identity_id, action) is not None for action in manual_only)
    assert ui._normalize_cave_public_batch_actions({"actions": manual_only}) == []
