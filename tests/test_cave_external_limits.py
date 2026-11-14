import asyncio
import copy
from unittest.mock import AsyncMock, Mock

import pytest

from model import cave_external_limits as limits, miniapp_state, state as states
from model.features import cave_treasure_miniapp as cave, cave_treasure_runtime as runtime
from model.webapp_core import MiniAppHttpResult


@pytest.fixture
def accounts(monkeypatch):
    saved = copy.deepcopy(states._meta_state)
    states._meta_state.clear()
    states._meta_state.update(copy.deepcopy(states.GLOBAL_STATE_DEFAULTS))
    for identity, account in ((2001, 2001), (2002, 2001), (3001, 3001)):
        states.set_identity_account(identity, account)
    save = Mock(return_value=True)
    monkeypatch.setattr(limits, "save_state", save)
    yield save
    states._meta_state.clear()
    states._meta_state.update(saved)


def test_shared_account_waits_but_other_accounts_can_launch(accounts):
    assert limits.reserve_request(2001, 1000) == 0
    assert limits.reserve_request(2002, 1010) == 90
    assert limits.reserve_request(3001, 1010) == 0
    assert limits.reserve_request(2002, 1100) == 0
    assert accounts.call_count == 3


@pytest.mark.parametrize(("window", "limit"), [(600, 6), (3600, 36)])
def test_native_rate_limit_samples_respect_both_windows(accounts, window, limit):
    accepted = []
    for now in range(1000, 4660):
        if limits.reserve_request(2001 + now % 2, now) == 0:
            accepted.append(now)
    assert accepted
    assert all(sum(t - window < other <= t for other in accepted) <= limit for t in accepted)


def test_restart_snapshot_and_normal_record_pruning_keep_deadline(accounts):
    assert limits.reserve_request(2002, 1000) == 0
    persisted = copy.deepcopy(states.get_miniapp_state_records())
    states.set_miniapp_state_records({})
    states.set_miniapp_state_records(persisted)
    miniapp_state.record_miniapp_state(2001, "fixture", {"value": 1}, now=1010, persist=False)
    assert limits.reserve_request(2001, 1010) == 90


def test_save_failure_cannot_admit_a_request(accounts):
    accounts.return_value = False
    assert limits.reserve_request(2001, 1000) == 100
    assert limits.reserve_request(2002, 1001) == 99


def test_production_flow_defers_without_http_and_preserves_wait(accounts, monkeypatch):
    monkeypatch.setattr(cave.time, "time", lambda: 1000.0)
    http = Mock(return_value=MiniAppHttpResult(ok=True, status_code=200, data={"ok": True}))
    monkeypatch.setattr(cave, "execute_miniapp_http_request", http)
    monkeypatch.setattr(cave, "_flow_transport", lambda *_args, **_kwargs: Mock())
    options = dict(token="df_FIXTURE42", webview_url="https://asc.aiopenai.app/cave",
                   action="fate_cards", init_data="fixture", player_id=2001)
    first = asyncio.run(cave.run_cave_external_action_production_flow(2001, **options))
    second = asyncio.run(cave.run_cave_external_action_production_flow(2002, **options))
    assert first["ok"]
    assert not second["ok"] and second["status"] == "local_rate_limit"
    assert runtime._miniapp_result_extra({}, second) == {"retry_after_sec": 100, "expected_wait": True}
    assert http.call_count == 1


@pytest.mark.parametrize(("action", "key", "runner_name", "downstream_name"), [
    ("trial", "tianji_trial", "run_cave_public_trial", "run_trial_miniapp_production_flow"),
    ("fishing", "fishing", "run_cave_public_fishing", "run_fishing_miniapp_production_flow"),
])
def test_external_wait_keeps_dynamic_games_quiet(accounts, monkeypatch, action, key, runner_name, downstream_name):
    monkeypatch.setattr(runtime, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(runtime, "_public_entry_allowed", lambda: True)
    monkeypatch.setattr(runtime, "_load_cave_public_identity_session", AsyncMock(return_value={
        "ok": True, "init_data": "fixture", "player_id": 2001,
        "result": {"ok": True, "data": {"raw": {"account": {"externalApps": {"groups": [{
            "apps": [{"key": key, "action": action, "available": True}],
        }]}}}}},
    }))
    external = AsyncMock(return_value={
        "ok": False, "status": "local_rate_limit", "retry_after_sec": 75,
        "error": "洞府外部入口等待同账号请求间隔",
    })
    monkeypatch.setattr(runtime, "run_cave_external_action_production_flow", external)
    downstream, audit = AsyncMock(), AsyncMock()
    monkeypatch.setattr(runtime, downstream_name, downstream)
    monkeypatch.setattr(runtime, "send_audit_log", audit)
    result = asyncio.run(getattr(runtime, runner_name)(
        2001, "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE42", now=1000,
    ))
    assert not result["ok"]
    assert result["extra"] == {"expected_wait": True, "retry_after_sec": 75}
    external.assert_awaited_once()
    downstream.assert_not_awaited()
    audit.assert_not_awaited()
