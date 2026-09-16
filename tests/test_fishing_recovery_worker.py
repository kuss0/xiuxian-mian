import asyncio
import hashlib
import json
import threading
from unittest.mock import AsyncMock

import pytest

from model.features import fishing_miniapp as fishing
from model.features.miniapp_common import MiniAppFlowCancelled
from test_fishing_worker_lifecycle import INIT, TOKEN, URL, adapter_with_limit, response


KEY = hashlib.sha256(TOKEN.encode()).hexdigest()


def run(transport, **kwargs):
    options = dict(token=TOKEN, init_data=INIT, expected_round_key=KEY,
                   pending_action="finish", pending_round_known=True, settled_round_keys=(),
                   transport=transport, adapter=adapter_with_limit(), sleeper=lambda _delay: None,
                   result_poll_limit=3)
    options.update(kwargs)
    return fishing.run_fishing_miniapp_recovery_lab_flow(**options)


@pytest.mark.parametrize("action", ["start", "finish", "next"])
def test_recovery_reads_only_the_bound_round(action):
    calls = []

    def transport(request):
        calls.append((request["safe_summary"]["endpoint"], request["payload"]["token"]))
        return response("result")

    result = run(transport, pending_action=action)
    assert calls == [("result", TOKEN)]
    assert result["ok"] is True
    assert result["status"] == "settled"
    assert result["action_dispatched"] is False
    assert result["outcome_unknown"] is False
    assert result["settled_round_keys"] == [KEY]
    assert result["data"]["details"]["fish"]["name"] == "fixture-fish"
    assert TOKEN not in json.dumps(result)
    assert "FIXTURE105_SECRET" not in json.dumps(result)


@pytest.mark.parametrize("change", [
    {"token": "fish_UNRELATED112"},
    {"token": "", "expected_round_key": hashlib.sha256(b"").hexdigest()},
    {"expected_round_key": ""},
    {"expected_round_key": "fish_SECRET112"},
    {"pending_action": "next", "pending_round_known": False},
    {"pending_action": "next", "pending_round_known": "true"},
    {"pending_action": "finish", "pending_round_known": False},
    {"pending_action": "unknown"},
    {"settled_round_keys": [KEY]},
    {"settled_round_keys": ["invalid"]},
    {"settled_round_keys": "invalid"},
    {"settled_round_keys": [hashlib.sha256(b"prior").hexdigest()] * 2},
])
def test_unbound_or_already_confirmed_round_never_reaches_http(change):
    calls = []
    result = run(lambda request: calls.append(request), **change)
    assert calls == []
    assert result["ok"] is False
    assert result["status"] == "recovery_blocked"
    assert result["action_dispatched"] is False
    assert result["outcome_unknown"] is True
    assert "fish_SECRET112" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["not_ready", "timeout", "server", "invalid_ready", "conflicting_ready"])
def test_read_retry_retains_pending_round_and_never_finishes_it_twice(failure):
    calls, sleeps = [], []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        if len(calls) == 1:
            if failure == "timeout":
                raise TimeoutError("fixture read timeout")
            if failure == "server":
                return 503, {"ok": False, "error": "fixture transient"}, {"Retry-After": "4"}
            if failure == "invalid_ready":
                return {"ok": True, "ready": "true", "result": {"expGain": 400}}
            if failure == "conflicting_ready":
                return {"ok": True, "ready": True, "result": {"ready": False, "expGain": 400}}
            return {"ok": True, "ready": False}
        return response("result")

    result = run(transport, sleeper=sleeps.append)
    invalid = failure in {"invalid_ready", "conflicting_ready"}
    assert calls == (["result"] if invalid else ["result", "result"])
    assert result["outcome_unknown"] is invalid
    assert result["ok"] is (not invalid)
    if failure == "server":
        assert sleeps == [4]


@pytest.mark.parametrize("code", [401, 403, 429, 503])
def test_access_and_long_backoff_never_trigger_reentry(code):
    calls, sleeps = [], []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return code, {"ok": False, "error": "fixture blocked"}, {"Retry-After": "1000"}

    result = run(transport, sleeper=sleeps.append)
    assert calls == ["result"]
    assert sleeps == []
    assert result["ok"] is False
    assert result["outcome_unknown"] is True
    assert result["unresolved_round_key"] == KEY


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_read_polling_is_bounded(limit):
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return {"ok": True, "ready": False}

    result = run(transport, result_poll_limit=limit)
    assert calls == ["result"] * limit
    assert result["outcome_unknown"] is True


def test_operation_invalidation_before_read_does_not_erase_pending():
    calls = []
    result = run(lambda request: calls.append(request), operation_check=lambda: False)
    assert calls == []
    assert result["status"] == "cancelled"
    assert result["outcome_unknown"] is True
    assert result["unresolved_round_key"] == KEY


def test_result_recovery_uses_the_existing_request_budget():
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return {"ok": True, "ready": False}

    result = run(transport, adapter=adapter_with_limit(1))
    assert calls == ["result"]
    assert result["status"] == "request_budget"
    assert result["outcome_unknown"] is True


@pytest.mark.parametrize("acknowledged", [False, True])
def test_normal_worker_separates_next_request_key_from_acknowledged_round(acknowledged):
    allowed = True

    def transport(request):
        nonlocal allowed
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint == "next":
            if not acknowledged:
                raise TimeoutError("fixture next response lost")
            allowed = False
            return {"ok": True, "token": "fish_NEW112"}
        return response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        sleeper=lambda _delay: None, max_rounds=2, operation_check=lambda: allowed,
    )
    assert result["data"]["settled_count"] == 1
    assert result["settled_round_keys"] == [KEY]
    assert result["outcome_unknown"] is True
    assert result["unresolved_round_known"] is acknowledged
    assert result["unresolved_round_key"] == (hashlib.sha256(b"fish_NEW112").hexdigest() if acknowledged else KEY)


@pytest.mark.parametrize("payload", [
    {"token": True}, {"token": {"value": "fish_NEW112"}}, {"token": ""},
    {"token": "fish_NEW112", "nextToken": "fish_OTHER112"},
    {"token": None, "next": {"token": "fish_NEW112"}},
    {"token": "fish_NEW112", "nextToken": None},
    {"token": "fish_NEW112", "nextToken": ""},
])
def test_invalid_next_token_cannot_establish_new_round_identity(payload):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return dict(payload, ok=True) if endpoint == "next" else response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        sleeper=lambda _delay: None, max_rounds=2,
    )
    assert calls == ["start", "finish", "result", "next"]
    assert result["data"]["settled_count"] == 1
    assert result["outcome_unknown"] is True
    assert result["unresolved_round_known"] is False
    assert result["unresolved_round_key"] == KEY


def test_repeated_round_token_cannot_repeat_gameplay_or_income():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return {"ok": True, "token": TOKEN} if endpoint == "next" else response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        sleeper=lambda _delay: None, max_rounds=3,
    )
    assert calls == ["start", "finish", "result", "next"]
    assert result["data"]["settled_count"] == 1
    assert result["settled_round_keys"] == [KEY]
    assert result["outcome_unknown"] is True


def test_identical_catches_in_distinct_rounds_still_accumulate():
    next_count = 0

    def transport(request):
        nonlocal next_count
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint == "next":
            next_count += 1
            return {"ok": True, "token": f"fish_NEW112_{next_count}"}
        return response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        sleeper=lambda _delay: None, max_rounds=3,
    )
    assert result["data"]["settled_count"] == 3
    assert len(set(result["settled_round_keys"])) == 3
    assert len(result["data"]["catches"]) == 3
    assert result["data"]["expGain"] == 12


def test_invalid_recovery_is_rejected_before_authorization(monkeypatch):
    authorize = AsyncMock(return_value=INIT)
    monkeypatch.setattr(fishing, "request_fishing_miniapp_init_data", authorize)
    result = asyncio.run(fishing.run_fishing_miniapp_recovery_production_flow(
        991120001, token=TOKEN, webview_url=URL, expected_round_key=KEY,
        pending_action="next", pending_round_known=False,
    ))
    authorize.assert_not_awaited()
    assert result["status"] == "recovery_blocked"
    assert result["outcome_unknown"] is True


def test_authorization_failure_keeps_prior_unknown_operation(monkeypatch):
    monkeypatch.setattr(fishing, "request_fishing_miniapp_init_data", AsyncMock(side_effect=TimeoutError("fixture auth failed")))
    result = asyncio.run(fishing.run_fishing_miniapp_recovery_production_flow(
        991120001, token=TOKEN, webview_url=URL, expected_round_key=KEY,
    ))
    assert result["ok"] is False
    assert result["action_dispatched"] is False
    assert result["outcome_unknown"] is True
    assert result["unresolved_round_key"] == KEY


def test_authorization_cancellation_keeps_prior_unknown_operation(monkeypatch):
    monkeypatch.setattr(fishing, "request_fishing_miniapp_init_data", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(MiniAppFlowCancelled) as raised:
        asyncio.run(fishing.run_fishing_miniapp_recovery_production_flow(
            991120001, token=TOKEN, webview_url=URL, expected_round_key=KEY,
        ))
    assert raised.value.result["outcome_unknown"] is True
    assert raised.value.result["unresolved_round_key"] == KEY


@pytest.mark.parametrize("ready", [False, True])
def test_cancellation_drains_bound_read_without_losing_its_outcome(ready):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        entered.set()
        assert release.wait(3)
        return response("result") if ready else {"ok": True, "ready": False}

    async def scenario():
        task = asyncio.create_task(fishing.run_fishing_miniapp_recovery_production_flow(
            991120001, token=TOKEN, webview_url=URL, init_data=INIT, expected_round_key=KEY,
            transport=transport, adapter=adapter_with_limit(), sleeper=lambda _delay: None,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            task.cancel()
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as raised:
                await task
        result = raised.value.result
        assert calls == ["result"]
        assert result["outcome_unknown"] is (not ready)
        assert result["ok"] is ready

    asyncio.run(scenario())
