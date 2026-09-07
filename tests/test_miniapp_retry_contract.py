import asyncio
from dataclasses import replace

import pytest

from model import webapp_core
from model.features import (
    cave_treasure_miniapp,
    fate_cards_miniapp,
    fishing_miniapp,
    stargazer_miniapp,
    tree_miniapp,
    trial_miniapp,
    world_boss_miniapp,
)


@pytest.mark.parametrize("endpoint", ["start", "finish", "next", "result"])
@pytest.mark.parametrize("failure", ["timeout", "http_503", "http_429", "invalid_json"])
def test_unclassified_request_is_not_replayed_even_with_retry_delays(endpoint, failure):
    request = fishing_miniapp.build_fishing_miniapp_request(endpoint, token="fish_TEST", init_data="init")
    calls = []
    sleeps = []

    def transport(_request):
        calls.append(endpoint)
        if failure == "timeout":
            raise TimeoutError("response lost after dispatch")
        if failure == "invalid_json":
            return 200, "upstream reply truncated"
        return int(failure.removeprefix("http_")), {"ok": False, "error": "temporary failure"}

    result = webapp_core.execute_miniapp_http_request(
        request, transport, backoff_sec=(0.1, 0.2), sleeper=sleeps.append,
    )
    assert not result.ok
    assert result.attempts == 1
    assert calls == [endpoint]
    assert sleeps == []


@pytest.mark.parametrize("run", [
    fishing_miniapp.run_fishing_miniapp_lab_flow,
    trial_miniapp.run_trial_miniapp_lab_flow,
    cave_treasure_miniapp.run_cave_treasure_miniapp_lab_flow,
    stargazer_miniapp.run_stargazer_miniapp_lab_flow,
    tree_miniapp.run_tree_miniapp_start_lab_flow,
    fate_cards_miniapp.run_fate_cards_start_probe,
])
def test_real_start_flows_do_not_blindly_replay_a_lost_response(run):
    calls = []
    sleeps = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        raise TimeoutError("outcome unknown after dispatch")

    result = run(token="unit_TEST", init_data="hash=test", transport=transport, sleeper=sleeps.append)
    assert not result["ok"]
    assert calls == ["start"]
    assert sleeps == []


def _test_adapter():
    return webapp_core.MiniAppAdapter(
        game_key="test", label="test", api_base_url="https://miniapp.invalid",
        allowed_api_hosts=("miniapp.invalid",), allowed_api_paths=("/api/",),
        endpoints={"read": "/api/read", "mutate": "/api/mutate", "finish": "/api/finish"},
        request_policy={"min_interval_sec": 0},
    )


@pytest.mark.parametrize("retry_safe", [False, None, "false", "true", 1])
def test_retry_permission_requires_literal_true(retry_safe):
    calls = []

    def transport(_request):
        calls.append(1)
        raise TimeoutError("outcome unknown")

    result = webapp_core.execute_miniapp_http_request(
        webapp_core.build_miniapp_http_request(_test_adapter(), "mutate"),
        transport, retry_safe=retry_safe, sleeper=lambda _delay: pytest.fail("unexpected retry"),
    )
    assert not result.ok
    assert len(calls) == 1


def test_explicit_read_retry_retains_budget_backoff_and_capture_accounting():
    calls = []
    sleeps = []
    captures = []
    budget = webapp_core.MiniAppRequestBudget({"min_interval_sec": 0, "max_attempts_per_request": 2})

    def transport(_request):
        calls.append(1)
        if len(calls) == 1:
            return {"status_code": 429, "json": {"ok": False}, "headers": {"Retry-After": "7"}}
        return 200, {"ok": True, "value": 42}

    result = webapp_core.execute_miniapp_http_request(
        webapp_core.build_miniapp_http_request(_test_adapter(), "read"),
        transport, retry_safe=True, backoff_sec=(0.1, 0.2), sleeper=sleeps.append,
        request_budget=budget, capture_sink=captures,
    )
    assert result.ok
    assert result.data["value"] == 42
    assert result.attempts == budget.request_count == len(calls) == len(captures) == 2
    assert sleeps == [7.0]


def test_read_retries_do_not_bypass_the_global_limiter(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    limiter_calls = []
    transport_calls = []
    monkeypatch.setattr(
        webapp_core._GLOBAL_MINIAPP_RATE_LIMITER, "acquire",
        lambda **_kwargs: limiter_calls.append(1),
    )

    def transport(_request):
        transport_calls.append(1)
        return (503, {"ok": False}) if len(transport_calls) == 1 else (200, {"ok": True})

    result = webapp_core.execute_miniapp_http_request(
        webapp_core.build_miniapp_http_request(_test_adapter(), "read"),
        transport, retry_safe=True, sleeper=lambda _delay: None,
    )
    assert result.ok
    assert len(limiter_calls) == len(transport_calls) == 2


def test_unknown_generic_flow_stops_after_single_mutation_attempt():
    plan = webapp_core.MiniAppFlowPlan(
        adapter_key="test", label="test", steps=(
            webapp_core.MiniAppFlowStep(key="mutate", endpoint="mutate"),
            webapp_core.MiniAppFlowStep(key="finish", endpoint="finish"),
        ),
    )
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        raise TimeoutError("applied but reply lost")

    result = webapp_core.run_miniapp_flow_plan(
        plan, _test_adapter(), transport=transport, sleeper=lambda _delay: None,
    )
    assert not result.ok
    assert calls == ["mutate"]
    assert "mutate" not in result.context


def test_generic_flow_uses_one_budget_for_reads_retries_and_mutations():
    adapter = _test_adapter()
    adapter.request_policy = replace(adapter.request_policy, max_requests_per_run=3)
    plan = webapp_core.MiniAppFlowPlan(
        adapter_key="test", label="test", steps=(
            webapp_core.MiniAppFlowStep(key="read", endpoint="read", retry_safe=True),
            webapp_core.MiniAppFlowStep(key="mutate", endpoint="mutate"),
            webapp_core.MiniAppFlowStep(key="finish", endpoint="finish"),
        ),
    )
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        if len(calls) == 1:
            return 503, {"ok": False}
        return 200, {"ok": True, "value": 42}

    result = webapp_core.run_miniapp_flow_plan(
        plan, adapter, transport=transport, backoff_sec=(0.1,), sleeper=lambda _delay: None,
    )
    assert not result.ok
    assert result.error == "request_budget_exhausted"
    assert calls == ["read", "read", "mutate"]
    assert result.context["read"]["value"] == result.context["mutate"]["value"] == 42
    assert "finish" not in result.context
    assert plan.steps[0].safe_summary()["retry_safe"] is True
    assert plan.steps[1].safe_summary()["retry_safe"] is False


@pytest.mark.parametrize("value", ["false", "true", 1, None])
def test_flow_step_rejects_ambiguous_retry_permission(value):
    with pytest.raises(ValueError, match="retry_safe must be a boolean"):
        webapp_core.MiniAppFlowStep(key="mutate", endpoint="mutate", retry_safe=value)


def test_flow_budget_enforces_inter_request_delay_and_skips_local_steps():
    now = [0.0]
    sleeps = []
    calls = []

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    budget = webapp_core.MiniAppRequestBudget(
        {"max_requests_per_run": 2, "min_interval_sec": 0.5},
        clock=lambda: now[0], sleeper=sleep,
    )
    plan = webapp_core.MiniAppFlowPlan(
        adapter_key="test", label="test", steps=(
            webapp_core.MiniAppFlowStep(key="local", method="LOCAL"),
            webapp_core.MiniAppFlowStep(key="read", endpoint="read"),
            webapp_core.MiniAppFlowStep(key="mutate", endpoint="mutate"),
        ),
    )

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return 200, {"ok": True}

    result = webapp_core.run_miniapp_flow_plan(
        plan, _test_adapter(), transport=transport, request_budget=budget,
    )
    assert result.ok
    assert calls == ["read", "mutate"]
    assert budget.request_count == 2
    assert sleeps == [0.5]


def test_flow_budget_stops_retries_after_consecutive_failures():
    adapter = _test_adapter()
    adapter.request_policy = replace(
        adapter.request_policy, max_attempts_per_request=4, max_consecutive_failures=2,
    )
    calls = []
    plan = webapp_core.MiniAppFlowPlan(
        adapter_key="test", label="test",
        steps=(webapp_core.MiniAppFlowStep(key="read", endpoint="read", retry_safe=True),),
    )

    def transport(_request):
        calls.append(1)
        return 503, {"ok": False}

    result = webapp_core.run_miniapp_flow_plan(
        plan, adapter, transport=transport, sleeper=lambda _delay: None,
    )
    assert not result.ok
    assert result.error == "consecutive_failure_limit"
    assert len(calls) == 2


def test_flow_prepare_mode_does_not_consume_a_budget():
    budget = webapp_core.MiniAppRequestBudget({"max_requests_per_run": 1})
    assert budget.acquire()[0]
    plan = webapp_core.MiniAppFlowPlan(
        adapter_key="test", label="test",
        steps=(webapp_core.MiniAppFlowStep(key="mutate", endpoint="mutate"),),
    )
    result = webapp_core.run_miniapp_flow_plan(plan, _test_adapter(), request_budget=budget)
    assert result.ok
    assert budget.request_count == 1


def test_explicit_retry_does_not_swallow_cancellation():
    def transport(_request):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        webapp_core.execute_miniapp_http_request(
            webapp_core.build_miniapp_http_request(_test_adapter(), "read"),
            transport, retry_safe=True, sleeper=lambda _delay: pytest.fail("cancelled request retried"),
        )


def test_boss_join_reconciliation_keeps_bounded_read_only_recovery():
    calls = []
    sleeps = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        if len(calls) == 1:
            raise TimeoutError("state read unavailable")
        return 200, {"ok": True, "joined": True, "sessionToken": "qyz_SESSION"}

    result = world_boss_miniapp.reconcile_world_boss_join_state_lab(
        token="qyz_TEST", init_data="hash=test", player_id=77,
        transport=transport, sleeper=sleeps.append,
    )
    assert result.joined
    assert calls == ["state", "state"]
    assert len(sleeps) == 1
