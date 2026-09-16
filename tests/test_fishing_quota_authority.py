import asyncio
import copy
import threading
from unittest.mock import Mock

import pytest

from model import persistence, state as state_module
from model.features import fishing_miniapp as worker
from model.features import fishing_runtime as fishing
from model.features.miniapp_common import MiniAppFlowCancelled
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_worker_lifecycle import INIT, TOKEN, adapter_with_limit, response


fishing_env = lifecycle.fishing_env
fishing_db = lifecycle.fishing_db
QUOTA = {"limit": 30, "used": 6, "remaining": 24}


@pytest.mark.parametrize("wrapper", [
    "last_daily", "last_result", "session", "shop", "history", "rounds",
    "previous_result", "daily_history", "fishingProof",
])
def test_diagnostic_quota_cannot_calibrate(wrapper):
    progress = fishing._extract_miniapp_daily_progress({wrapper: {"daily": QUOTA}})
    assert not progress.get("limit")


@pytest.mark.parametrize("data", [
    {"limit": 30, "used": 6, "remaining": 24},
    {"quota": 30, "count": 6},
    {"total": 30, "left": 24, "remaining": 24},
])
def test_generic_root_counts_are_not_daily_quota(data):
    assert not fishing._extract_miniapp_daily_progress(data).get("limit")


@pytest.mark.parametrize("empty", [None, {}, [], "", {"used": 6}, {"limit": 30}])
def test_explicit_current_quota_masks_nested_fallback(empty):
    data = {"daily": empty, "result": {"daily": QUOTA}, "last_daily": QUOTA}
    assert not fishing._extract_miniapp_daily_progress(data).get("limit")


@pytest.mark.parametrize("quota", [
    {"limit": 30, "dailyLimit": 40, "used": 6},
    {"limit": 30, "used": 6, "dailyUsed": 7},
    {"limit": 30, "remaining": 24, "dailyRemaining": 23},
    {"limit": 30, "used": 6, "remaining": 23},
    {"limit": 30, "used": 31},
    {"limit": 30, "remaining": 31},
    {"limit": 0, "used": 0},
    {"limit": 100, "used": 6},
])
def test_invalid_or_contradictory_quota_has_no_authority(quota):
    assert not fishing._extract_miniapp_daily_progress({"daily": quota}).get("limit")


@pytest.mark.parametrize("field", ["limit", "used", "remaining"])
@pytest.mark.parametrize("value", [None, True, "6", -1, 1.5, float("inf"), float("nan")])
def test_invalid_alias_cannot_be_replaced_by_a_valid_alias(field, value):
    quota = dict(QUOTA)
    quota["daily" + field.title()] = value
    assert not fishing._extract_miniapp_daily_progress({"daily": quota}).get("limit")


@pytest.mark.parametrize("data", [
    {"daily": QUOTA},
    {"result": {"daily": QUOTA}},
    {"dailyLimit": 30, "dailyUsed": 6, "dailyRemaining": 24},
    {"limit": 30, "dailyUsed": 6},
    {"daily": {"limit": 30, "used": 6}},
    {"daily": {"limit": 30, "remaining": 24}},
    {"daily": {"limit": 30.0, "used": 6.0, "remaining": 24.0}},
    {"daily": QUOTA, "dailyQuota": dict(QUOTA)},
    {"daily": dict(QUOTA, dailyLimit=30, dailyUsed=6)},
])
def test_current_consistent_quota_is_normalized(data):
    assert fishing._extract_miniapp_daily_progress(data) == QUOTA


@pytest.mark.parametrize("other", [None, {}, {"limit": 30, "used": 7}])
def test_same_scope_quota_aliases_must_agree(other):
    assert not fishing._extract_miniapp_daily_progress({"daily": QUOTA, "dailyQuota": other}).get("limit")


@pytest.mark.parametrize("quota", [
    {"limit": 30, "used": 0, "remaining": 30},
    {"limit": 30, "used": 30, "remaining": 0},
    {"limit": 99, "used": 99, "remaining": 0},
])
def test_zero_and_supported_upper_bound_are_valid(quota):
    assert fishing._extract_miniapp_daily_progress({"daily": quota}) == quota


@pytest.mark.parametrize("data", [
    {"dailyLimit": None, "result": {"daily": QUOTA}},
    {"dailyLimit": 30, "result": {"daily": QUOTA}},
    {"result": {"daily": QUOTA}, "details": {"daily": {"limit": 30, "used": 7}}},
])
def test_partial_or_conflicting_scopes_do_not_mix(data):
    assert not fishing._extract_miniapp_daily_progress(data).get("limit")


@pytest.mark.parametrize("ok,status", [(False, "failed"), (True, "not_ready"), (False, "request_budget")])
def test_unconfirmed_result_cannot_change_daily_limit(fishing_env, ok, status):
    h = fishing_env
    h.identity["fishing_daily_count"] = 4
    result = {"ok": ok, "status": status, "data": {"settled_count": 0, "daily": QUOTA}}
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(result, h.now)
    assert h.identity["fishing_daily_count"] == 4
    assert h.identity["fishing_daily_limit"] == 10
    h.items.assert_not_called()


@pytest.mark.parametrize("reported", [0, 4, 7])
def test_stale_quota_cannot_erase_known_rounds_or_reduce_limit(fishing_env, reported):
    h = fishing_env
    h.identity["fishing_daily_count"] = 7
    h.result["data"]["daily"] = {"limit": 8, "used": reported}
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    assert h.identity["fishing_daily_count"] == 8
    assert h.identity["fishing_daily_limit"] == 10
    h.items.assert_called_once()


@pytest.mark.parametrize("caller", ["public", "message"])
def test_actual_worker_and_caller_keep_outer_quota(fishing_env, caller):
    h = fishing_env
    h.identity["fishing_daily_count"] = 4
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        result = response(endpoint)
        if endpoint == "result":
            result["daily"] = dict(QUOTA)
        return result

    async def flow(identity_id, **kwargs):
        kwargs.update(init_data=INIT, transport=transport, adapter=adapter_with_limit(),
                      max_rounds=1, sleeper=lambda _delay: None)
        return await worker.run_fishing_miniapp_production_flow(identity_id, **kwargs)

    h.flow.side_effect = flow
    asyncio.run(lifecycle.run_caller(h, caller))
    assert calls == ["start", "finish", "result"]
    assert h.identity["fishing_daily_count"] == 6
    assert h.identity["fishing_daily_limit"] == 30
    h.items.assert_called_once()


@pytest.mark.parametrize("outer", [QUOTA, {}, None])
@pytest.mark.parametrize("inner", [
    {"daily": {"limit": 30, "used": 9}},
    {"dailyLimit": 30, "dailyUsed": 9},
    {"dailyQuota": {"limit": 30, "used": 9}},
])
def test_worker_canonical_quota_does_not_compete_with_discarded_nested_aliases(outer, inner):
    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        result = response(endpoint)
        if endpoint == "result":
            result["daily"] = outer
            result["result"].update(inner)
        return result

    result = worker.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        sleeper=lambda _delay: None,
    )
    assert result["data"]["settled_count"] == 1
    assert result["data"]["daily"] == (QUOTA if outer else {})
    assert fishing._extract_miniapp_daily_progress(result["data"]) == (QUOTA if outer else {})


@pytest.mark.parametrize("caller", ["public", "message"])
def test_cancelled_caller_keeps_confirmed_quota_and_catch(fishing_env, caller):
    h = fishing_env
    h.identity["fishing_daily_count"] = 4
    entered, release = threading.Event(), threading.Event()
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        result = response(endpoint)
        if endpoint == "result":
            entered.set()
            assert release.wait(3)
            result["daily"] = dict(QUOTA)
        return result

    async def flow(identity_id, **kwargs):
        kwargs.update(init_data=INIT, transport=transport, adapter=adapter_with_limit(),
                      max_rounds=1, sleeper=lambda _delay: None)
        return await worker.run_fishing_miniapp_production_flow(identity_id, **kwargs)

    async def scenario():
        h.flow.side_effect = flow
        task = asyncio.create_task(lifecycle.run_caller(h, caller))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled):
                await task

    asyncio.run(scenario())
    assert calls == ["start", "finish", "result"]
    assert h.identity["fishing_daily_count"] == 6
    assert h.identity["fishing_daily_limit"] == 30
    h.items.assert_called_once()
    h.daily.assert_not_awaited()


@pytest.mark.parametrize("last", ["missing", "newer", "failed"])
def test_chain_quota_belongs_to_the_latest_confirmed_round(last):
    rounds = 0

    def transport(request):
        nonlocal rounds
        endpoint = request["safe_summary"]["endpoint"]
        result = response(endpoint)
        if endpoint == "result":
            rounds += 1
            if rounds == 2 and last == "failed":
                return {"ok": False, "error": "fixture failure", "daily": {"used": 30, "limit": 30}}
            if rounds == 1 or last == "newer":
                result["result"]["daily"] = {"used": 5 + rounds, "limit": 30}
        return result

    result = worker.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=2, sleeper=lambda _delay: None,
    )
    progress = result["data"].get("daily")
    assert progress == ({} if last == "missing" else {
        "used": 7 if last == "newer" else 6, "limit": 30, "remaining": 23 if last == "newer" else 24,
    })
    assert result["data"]["settled_count"] == (1 if last == "failed" else 2)


def test_history_only_quota_preserves_real_catch_without_calibration(fishing_env):
    h = fishing_env
    h.result["data"]["last_daily"] = {"limit": 99, "used": 99}
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    assert h.identity["fishing_daily_count"] == 1
    assert h.identity["fishing_daily_limit"] == 10
    assert h.identity["next_fishing_time"] < h.now + 60
    h.items.assert_called_once()


def test_daily_limit_status_does_not_adopt_a_contradictory_open_quota(fishing_env):
    h = fishing_env
    result = {"ok": False, "status": "daily_limit", "data": {"settled_count": 0, "daily": QUOTA}}
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(result, h.now)
    assert h.identity["fishing_daily_limit"] == 10
    assert h.identity["fishing_daily_count"] == 10
    h.items.assert_not_called()


def test_quota_projection_retains_atomic_local_recovery(fishing_db, monkeypatch):
    h = fishing_db
    h.identity["fishing_daily_count"] = 4
    h.result["data"]["daily"] = dict(QUOTA)
    assert persistence.save_state() is not False
    before = copy.deepcopy(h.identity)
    with monkeypatch.context() as patch:
        patch.setattr(fishing, "save_state", Mock(return_value=False))
        with state_module.use_identity(h.identity_id), pytest.raises(fishing.FishingMiniAppCommitError):
            fishing._apply_fishing_miniapp_result(h.result, h.now)
    assert h.identity["fishing_daily_count"] == before["fishing_daily_count"]
    assert h.identity["fishing_daily_limit"] == before["fishing_daily_limit"]
    assert h.identity["fishing_result_pending"]
    assert persistence.save_state() is not False
    persistence.load_state()
    outcome = fishing.recover_fishing_result_pending(h.identity_id)
    assert outcome["ok"]
    identity = state_module.get_identity_state(h.identity_id)
    assert identity["fishing_daily_count"] == 6
    assert identity["fishing_daily_limit"] == 30
    assert fishing.recover_fishing_result_pending(h.identity_id) is None
    assert identity["fishing_result_pending"] == {}
