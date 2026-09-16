import asyncio
import hashlib
import json
import threading
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from model.features import fishing_miniapp as fishing
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppRequestAborted
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_worker_lifecycle import INIT, TOKEN, URL, adapter_with_limit, response


fishing_env = lifecycle.fishing_env


def run(transport, *, polls=3, rounds=1, limit=32, sleeper=None, operation_check=None, error_limit=None):
    adapter = adapter_with_limit(limit)
    if error_limit is not None:
        adapter = replace(adapter, request_policy=replace(adapter.request_policy, max_consecutive_failures=error_limit))
    return fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter,
        max_rounds=rounds, result_poll_limit=polls,
        sleeper=sleeper or (lambda _delay: None), operation_check=operation_check,
    )


@pytest.mark.parametrize("failure", ["exception", "server", "non_json"])
def test_lost_finish_response_uses_same_token_result_without_resubmitting(failure):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append((endpoint, request["payload"]["token"]))
        if endpoint == "finish":
            if failure == "exception":
                raise TimeoutError("fixture reply lost")
            if failure == "server":
                return 503, {"ok": False, "error": "upstream timeout"}
            return 502, "upstream HTML"
        return response(endpoint)

    result = run(transport)
    assert result["data"]["settled_count"] == 1
    assert result["data"]["catches"][0]["fish"] == "fixture-fish"
    assert calls == [("start", TOKEN), ("finish", TOKEN), ("result", TOKEN)]
    assert result["action_dispatched"] is True
    assert result["outcome_unknown"] is False
    assert not result["unresolved_action"]
    assert any(event["step"] == "finish" and event["ok"] is False for event in result["events"])


@pytest.mark.parametrize("failure", ["exception", "server", "non_json", "not_ready"])
def test_bounded_result_reads_recover_transient_fault_without_replaying_finish(failure):
    calls, sleeps = [], []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "result" and calls.count("result") == 1:
            if failure == "exception":
                raise TimeoutError("fixture result timeout")
            if failure == "server":
                return 503, {"ok": False, "error": "fixture result error"}
            if failure == "non_json":
                return 502, "fixture proxy error"
            return {"ok": True, "ready": False}
        return response(endpoint)

    result = run(transport, sleeper=sleeps.append)
    assert result["data"]["settled_count"] == 1
    assert calls == ["start", "finish", "result", "result"]
    assert sleeps and min(sleeps) >= fishing.FISHING_MINIAPP_RESULT_POLL_DELAY_SEC
    assert result["outcome_unknown"] is False


@pytest.mark.parametrize("endpoint", ["finish", "result"])
@pytest.mark.parametrize("code", [401, 403, 429])
def test_auth_access_and_rate_limit_failures_do_not_trigger_inline_probes(endpoint, code):
    calls, sleeps = [], []

    def transport(request):
        step = request["safe_summary"]["endpoint"]
        calls.append(step)
        if step == endpoint:
            return code, {"ok": False, "error": "fixture limit"}, {"Retry-After": "90"}
        return response(step)

    result = run(transport, sleeper=sleeps.append)
    assert calls[-1] == endpoint
    assert calls.count(endpoint) == 1
    assert not sleeps
    assert result["outcome_unknown"] is True
    assert result["data"]["settled_count"] == 0
    if code == 429:
        assert any(event.get("retry_after_sec") == 90 for event in result["events"])


def test_explicit_finish_rejection_is_not_retried_or_read_as_success():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            return {"ok": False, "error": "invalid_fishing_proof"}
        return response(endpoint)

    result = run(transport)
    assert calls == ["start", "finish"]
    assert result["data"]["settled_count"] == 0
    assert result["action_dispatched"] is True
    assert result["outcome_unknown"] is True


@pytest.mark.parametrize("boundary", ["start", "finish", "next"])
def test_entered_transport_cancellation_is_not_reported_as_unsent(boundary):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == boundary:
            raise MiniAppRequestAborted("fixture transport uncertain")
        return response(endpoint)

    result = run(transport, rounds=2)
    assert calls[-1] == boundary
    assert result["action_dispatched"] is True
    assert result["outcome_unknown"] is True
    assert result["unresolved_action"] in {boundary, "start"}
    assert result["data"]["settled_count"] == int(boundary == "next")


@pytest.mark.parametrize("boundary", ["start", "finish", "result", "next"])
def test_unknown_effects_keep_the_exact_pending_round_key(boundary):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == boundary:
            raise TimeoutError("fixture uncertain action")
        return response(endpoint)

    result = run(transport, rounds=2, polls=0 if boundary == "finish" else 1)
    assert result["action_dispatched"] is True
    assert result["outcome_unknown"] is True
    assert result["unresolved_round_key"] == hashlib.sha256(TOKEN.encode()).hexdigest()
    assert TOKEN not in json.dumps(result)
    assert "FIXTURE105_SECRET" not in json.dumps(result)
    assert result["data"]["settled_count"] == int(boundary == "next")


def test_operation_rejection_before_transport_has_no_game_effect():
    calls = []
    result = run(lambda request: calls.append(request), operation_check=lambda: False)
    assert calls == []
    assert result["action_dispatched"] is False
    assert result["outcome_unknown"] is False
    assert not result["unresolved_action"]


@pytest.mark.parametrize("limit", [1, 2])
def test_budget_exhaustion_retains_an_already_active_round(limit):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return response(endpoint)

    result = run(transport, limit=limit)
    assert len(calls) == limit
    assert result["outcome_unknown"] is True
    assert result["unresolved_action"] == ("start" if limit == 1 else "finish")
    assert result["data"]["settled_count"] == 0


def test_partial_success_plus_next_timeout_keeps_both_fact_and_uncertainty():
    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint == "next":
            raise TimeoutError("fixture next reply lost")
        return response(endpoint)

    result = run(transport, rounds=2)
    assert result["data"]["settled_count"] == 1
    assert result["data"]["expGain"] == 4
    assert result["data"]["catches"][0]["fish"] == "fixture-fish"
    assert result["outcome_unknown"] is True
    assert result["unresolved_action"] == "next"


def test_definite_next_rejection_does_not_reopen_the_settled_round():
    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint == "next":
            return {"ok": False, "error": "fishing_bait_missing"}
        return response(endpoint)

    result = run(transport, rounds=2)
    assert result["data"]["settled_count"] == 1
    assert result["action_dispatched"] is True
    assert result["outcome_unknown"] is False
    assert not result["unresolved_action"]


def test_webview_failure_reports_no_game_dispatch(monkeypatch):
    monkeypatch.setattr(fishing, "request_fishing_miniapp_init_data", AsyncMock(side_effect=RuntimeError("fixture auth failed")))
    result = asyncio.run(fishing.run_fishing_miniapp_production_flow(991100001, token=TOKEN, webview_url=URL))
    assert result["action_dispatched"] is False
    assert result["outcome_unknown"] is False


def test_poll_limit_and_shared_budget_bound_read_recovery():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint in {"finish", "result"}:
            raise TimeoutError("fixture remains unavailable")
        return response(endpoint)

    result = run(transport, polls=7, limit=4, error_limit=10)
    assert calls == ["start", "finish", "result", "result"]
    assert result["outcome_unknown"] is True
    assert result["data"]["settled_count"] == 0


def test_default_consecutive_error_budget_is_not_relaxed_for_recovery():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint in {"finish", "result"}:
            raise TimeoutError("fixture remains unavailable")
        return response(endpoint)

    result = run(transport, polls=7, limit=32)
    assert calls == ["start", "finish", "result"]
    assert result["status"] == "request_budget"
    assert result["outcome_unknown"] is True


@pytest.mark.parametrize("boundary", ["finish", "result", "next"])
def test_cancellation_drains_http_and_preserves_current_round_outcome(boundary):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == boundary:
            entered.set()
            assert release.wait(3)
        return response(endpoint)

    async def scenario():
        task = asyncio.create_task(fishing.run_fishing_miniapp_production_flow(
            991100001, token=TOKEN, webview_url=URL, init_data=INIT, max_rounds=2,
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
        assert calls[-1] == boundary
        assert result["data"]["settled_count"] == int(boundary in {"result", "next"})
        assert result["outcome_unknown"] is (boundary != "result")
        if boundary == "next":
            assert result["unresolved_action"] == "next"
            assert result["unresolved_round_key"] == hashlib.sha256(b"fish_NEXT105").hexdigest()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["finish", "result"])
def test_retry_after_is_respected_before_result_recovery(boundary):
    calls, sleeps = [], []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == boundary and calls.count(endpoint) == 1:
            return 503, {"ok": False, "error": "fixture transient"}, {"Retry-After": "4"}
        return response(endpoint)

    result = run(transport, sleeper=sleeps.append)
    assert result["data"]["settled_count"] == 1
    assert sleeps == [4]
    assert calls.count("finish") == 1


@pytest.mark.parametrize("boundary", ["finish", "result"])
def test_long_server_backoff_returns_unknown_without_inline_wait(boundary):
    calls, sleeps = [], []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == boundary:
            return 503, {"ok": False, "error": "fixture transient"}, {"Retry-After": "1000"}
        return response(endpoint)

    result = run(transport, sleeper=sleeps.append)
    assert calls[-1] == boundary and calls.count(boundary) == 1
    assert result["outcome_unknown"] is True
    assert not sleeps


@pytest.mark.parametrize("boundary", ["finish", "result"])
def test_invalidated_recovery_wait_never_starts_the_next_read(boundary):
    calls = []
    allowed = True

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == boundary:
            raise TimeoutError("fixture transient")
        return response(endpoint)

    def sleep(_delay):
        nonlocal allowed
        allowed = False

    result = run(transport, sleeper=sleep, operation_check=lambda: allowed)
    assert calls[-1] == boundary and calls.count(boundary) == 1
    assert result["status"] == "cancelled"
    assert result["outcome_unknown"] is True


@pytest.mark.parametrize("failure", ["shop", "no_bait", "next_rejected", "next_timeout"])
def test_lobby_reports_only_unresolved_mutations(failure):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return {"ok": True, "session": {"phase": "lobby"}}
        if endpoint == "shop":
            if failure == "shop":
                raise TimeoutError("fixture read-only shop failed")
            result = response(endpoint)
            if failure == "no_bait":
                result["shop"]["baits"] = []
            return result
        if failure == "next_rejected":
            return {"ok": False, "error": "fishing_bait_missing"}
        raise TimeoutError("fixture uncertain next")

    result = run(transport)
    assert result["data"]["settled_count"] == 0
    assert result["outcome_unknown"] is (failure == "next_timeout")
    assert not result["unresolved_action"] or result["unresolved_action"] == "next"


@pytest.mark.parametrize("code", [200, 400])
def test_explicit_initial_rejection_is_not_an_unknown_action(code):
    result = run(lambda _request: (code, {"ok": False, "error": "no_rod"}))
    assert result["outcome_unknown"] is False
    assert result["action_dispatched"] is True
    assert result["status"] == "no_rod"


@pytest.mark.parametrize("caller", ["public", "message"])
def test_actual_caller_accounts_recovered_finish_once(fishing_env, caller):
    h = fishing_env
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            raise TimeoutError("fixture finish response lost")
        return response(endpoint)

    async def worker(identity_id, **kwargs):
        kwargs.update(init_data=INIT, transport=transport, adapter=adapter_with_limit(),
                      max_rounds=1, sleeper=lambda _delay: None)
        return await fishing.run_fishing_miniapp_production_flow(identity_id, **kwargs)

    h.flow.side_effect = worker
    asyncio.run(lifecycle.run_caller(h, caller))
    assert calls == ["start", "finish", "result"]
    assert h.identity["fishing_daily_count"] == 1
    assert h.identity["fishing_result_pending"] == {}
    h.items.assert_called_once_with(h.identity_id, {"fixture-fish": 1}, persist=False)
