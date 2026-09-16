import asyncio
import json
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import fishing_miniapp as fishing
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppRequestBudget, MiniAppRequestPolicy, miniapp_retry_after_sec


TOKEN = "fish_FIXTURE105"
INIT = "query_id=fixture&hash=FIXTURE105_SECRET"
URL = "https://t.me/fanrenxiuxian_bot?startapp=" + TOKEN


def adapter_with_limit(count=32):
    return replace(fishing.build_fishing_miniapp_adapter(), request_policy=MiniAppRequestPolicy(
        min_interval_sec=0, max_requests_per_run=count,
    ))


def challenge(*, v2=False):
    data = {"challengeId": "fixture105", "minDurationMs": 20, "maxDurationMs": 70000}
    if v2:
        data.update(targetLow=35, targetHigh=65, fishPower=1.2, fishSeed=12345)
    return {"ok": True, "session": {"phase": "bite"}, "challenge": data}


def settled():
    return {"ok": True, "ready": True, "result": {
        "score": 94, "expGain": 4,
        "details": {"fish": {"name": "fixture-fish", "grade": "a"}},
    }}


def shop():
    return {"ok": True, "shop": {
        "ponds": [{"key": "pond", "unlocked": True}],
        "baits": [{"key": "bait", "itemId": "bait-item", "count": 10, "unlocked": True}],
    }}


def response(endpoint):
    return {
        "start": challenge(), "shop": shop(),
        "next": {"ok": True, "token": "fish_NEXT105"},
        "finish": {"ok": True, "result": {"score": 94}},
        "result": settled(),
    }[endpoint]


def assert_one_settlement(result):
    assert result["data"].get("settled_count") == 1, result
    assert result["data"].get("expGain") == 4, result
    assert [item["fish"] for item in result["data"].get("catches", [])] == ["fixture-fish"]


@pytest.mark.parametrize("limit", [2, 3, 4, 5, 7])
def test_one_request_budget_covers_rounds_and_next_shop(limit):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(limit),
        max_rounds=3, pond_choice="pond", bait_choice="bait", sleeper=lambda _delay: None,
    )
    assert len(calls) == limit, calls
    assert result["error"] == "request_budget_exhausted"
    assert any(event.get("error_type") == "request_budget" for event in result["events"])
    if limit >= 3:
        assert_one_settlement(result)
    else:
        assert result["data"]["settled_count"] == 0


def test_cancelled_worker_drains_inflight_settlement_and_never_starts_next_round(monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    original = fishing.run_fishing_miniapp_loop_lab_flow

    def worker(**kwargs):
        try:
            return original(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(fishing, "run_fishing_miniapp_loop_lab_flow", worker)

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "result":
            entered.set()
            assert release.wait(3)
        return response(endpoint)

    async def run():
        task = asyncio.create_task(fishing.run_fishing_miniapp_production_flow(
            991050001, token=TOKEN, webview_url=URL, init_data=INIT,
            transport=transport, adapter=adapter_with_limit(), max_rounds=2,
            sleeper=lambda _delay: None,
        ))
        outcome = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            retained = not task.done()
            task.cancel()
            await asyncio.sleep(0.02)
            retained_twice = not task.done()
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError as exc:
                outcome = exc
            assert await asyncio.to_thread(finished.wait, 2)
        assert retained and retained_twice, "caller released exclusion before HTTP returned"
        assert isinstance(outcome, MiniAppFlowCancelled)
        assert_one_settlement(outcome.result)
        assert calls == ["start", "finish", "result"]

    asyncio.run(run())


def test_later_wait_exception_preserves_an_earlier_confirmed_round():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return response(endpoint)

    def fail_rest(_delay):
        raise RuntimeError("fixture wait failed")

    result = asyncio.run(fishing.run_fishing_miniapp_production_flow(
        991050001, token=TOKEN, webview_url=URL, init_data=INIT,
        transport=transport, adapter=adapter_with_limit(), max_rounds=2, sleeper=fail_rest,
    ))
    assert_one_settlement(result)
    assert result["error"] == "fixture wait failed"
    assert calls == ["start", "finish", "result", "next"]


def test_chain_retains_round_http_failure_and_retry_after_evidence():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            return SimpleNamespace(status_code=429, headers={"Retry-After": "41"},
                                   json=lambda: {"error": "rate limit"})
        return response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(), max_rounds=2,
    )
    assert calls == ["start", "finish"]
    events = [event for event in result["events"] if event.get("step") == "finish"]
    assert len(events) == 1, result
    assert events[0]["status_code"] == 429
    assert events[0]["retry_after_sec"] == 41
    assert miniapp_retry_after_sec(result) == 41
    assert result["data"]["settled_count"] == 0


@pytest.mark.parametrize("boundary", ["start", "shop", "next", "finish", "result"])
def test_guard_invalidated_by_http_response_stops_later_dispatch(boundary):
    allowed = True
    calls = []

    def transport(request):
        nonlocal allowed
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == boundary:
            allowed = False
        if endpoint == "start" and len(calls) == 1 and boundary in {"shop", "next"}:
            return {"ok": True, "session": {"phase": "lobby"}}
        return response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=2, operation_check=lambda: allowed, sleeper=lambda _delay: None,
    )
    assert calls[-1] == boundary
    assert result["status"] == "cancelled"
    if boundary == "result":
        assert_one_settlement(result)
    else:
        assert result["data"]["settled_count"] == 0
        assert not result["data"]["catches"]


@pytest.mark.parametrize("boundary", ["bite", "proof", "result", "rest"])
def test_guard_invalidated_during_each_gameplay_wait_stops_later_dispatch(boundary):
    allowed = True
    calls, sleeps = [], []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            if boundary == "bite":
                return {"ok": True, "session": {"phase": "waiting", "biteAt": 1000, "serverNow": 0}}
            return challenge(v2=boundary == "proof")
        if endpoint == "result" and boundary == "result":
            return {"ok": True, "ready": False}
        return response(endpoint)

    def sleep(delay):
        nonlocal allowed
        sleeps.append(delay)
        allowed = False

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=2, operation_check=lambda: allowed, sleeper=sleep,
    )
    assert len(sleeps) == 1
    expected = {
        "bite": ["start"], "proof": ["start"], "result": ["start", "finish", "result"],
        "rest": ["start", "finish", "result", "next"],
    }
    assert calls == expected[boundary]
    assert result["status"] == "cancelled"
    if boundary == "rest":
        assert_one_settlement(result)
    else:
        assert result["data"]["settled_count"] == 0


@pytest.mark.parametrize("boundary", ["slot", "entity", "input", "webview"])
def test_webview_authorization_rechecks_each_await(monkeypatch, boundary):
    allowed = True

    async def step(name, value):
        nonlocal allowed
        if boundary == name:
            allowed = False
        return value

    @asynccontextmanager
    async def slot(**_kwargs):
        await step("slot", None)
        yield

    client = AsyncMock(side_effect=lambda _request: None)

    async def webview(_request):
        return await step("webview", SimpleNamespace(url="https://asc.aiopenai.app/#tgWebAppData=fixture-init"))

    async def entity(_name):
        return await step("entity", "fixture-bot")

    async def input_entity(_bot):
        return await step("input", "fixture-input")

    client.side_effect = webview
    client.get_entity = AsyncMock(side_effect=entity)
    client.get_input_entity = AsyncMock(side_effect=input_entity)
    monkeypatch.setattr(fishing, "account_rpc_slot", slot)
    monkeypatch.setattr(fishing, "_get_identity_client_with_account", Mock(return_value=(7105, client)))
    transport = Mock()
    result = asyncio.run(fishing.run_fishing_miniapp_production_flow(
        991050001, token=TOKEN, webview_url=URL, transport=transport,
        adapter=adapter_with_limit(), operation_check=lambda: allowed,
    ))
    assert result["status"] == "cancelled", result
    transport.assert_not_called()
    assert client.get_entity.await_count == int(boundary != "slot")
    assert client.get_input_entity.await_count == int(boundary in {"input", "webview"})
    assert client.await_count == int(boundary == "webview")


def test_successful_chain_keeps_budget_evidence_but_no_protocol_credentials():
    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=lambda request: response(request["safe_summary"]["endpoint"]),
        adapter=adapter_with_limit(), max_rounds=2, sleeper=lambda _delay: None,
    )
    assert result["ok"]
    assert result["data"]["settled_count"] == 2
    assert result["data"]["expGain"] == 8
    serialized = json.dumps(result)
    assert TOKEN not in serialized
    assert "fish_NEXT105" not in serialized
    assert "FIXTURE105_SECRET" not in serialized
    assert len([event for event in result["events"] if event.get("step") == "finish"]) == 2


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 5, 6, 7])
def test_single_round_lobby_and_result_polling_use_the_same_budget(limit):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start" and len(calls) == 1:
            return {"ok": True, "session": {"phase": "lobby"}}
        if endpoint == "result":
            return {"ok": True, "ready": False}
        return response(endpoint)

    result = fishing.run_fishing_miniapp_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(limit),
        sleeper=lambda _delay: None,
    )
    assert len(calls) == limit
    assert not result["ok"]
    assert result["error"] == "request_budget_exhausted"
    assert result["events"][-1]["error_type"] == "request_budget"
    assert result["data"].get("phase") == ("finish_submitted" if limit >= 5 else None)


def test_budget_wait_is_real_and_guard_is_rechecked_after_it():
    allowed = True
    calls, sleeps = [], []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return response(endpoint)

    def sleep(delay):
        nonlocal allowed
        sleeps.append(delay)
        allowed = False

    budget = MiniAppRequestBudget(clock=lambda: 0, sleeper=sleep)
    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, request_budget=budget,
        operation_check=lambda: allowed,
    )
    assert calls == ["start"]
    assert sleeps == [1]
    assert result["status"] == "cancelled"
    assert result["data"]["settled_count"] == 0


def test_default_budget_spaces_all_requests_and_bounds_a_whole_chain():
    clock = [0.0]
    calls, sleeps = [], []

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    budget = MiniAppRequestBudget(clock=lambda: clock[0], sleeper=sleep)

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "next":
            return {"ok": True, "token": f"fish_BUDGET105_{calls.count('next')}"}
        return response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, request_budget=budget,
        max_rounds=99, sleeper=sleep, rest_range_sec=(0, 0),
    )
    assert len(calls) == 32 == budget.request_count
    assert [delay for delay in sleeps if delay] == [1] * 31
    assert result["error"] == "request_budget_exhausted"
    assert result["data"]["settled_count"] == 8


@pytest.mark.parametrize("value", [False, None, 1, {}, "true"])
def test_non_true_guard_cannot_authorize_production(value, monkeypatch):
    auth = AsyncMock()
    transport = Mock()
    monkeypatch.setattr(fishing, "request_fishing_miniapp_init_data", auth)
    result = asyncio.run(fishing.run_fishing_miniapp_production_flow(
        991050001, token=TOKEN, webview_url=URL, transport=transport,
        operation_check=lambda: value,
    ))
    assert result["status"] == "cancelled"
    auth.assert_not_awaited()
    transport.assert_not_called()


@pytest.mark.parametrize("boundary_index", range(8))
def test_cancellation_at_every_lobby_round_and_next_http_boundary(boundary_index):
    entered, release = threading.Event(), threading.Event()
    calls = []
    sequence = ["start", "shop", "next", "start", "finish", "result", "shop", "next"]

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if len(calls) - 1 == boundary_index:
            entered.set()
            assert release.wait(3)
        if len(calls) == 1:
            return {"ok": True, "session": {"phase": "lobby"}}
        return response(endpoint)

    async def run():
        task = asyncio.create_task(fishing.run_fishing_miniapp_production_flow(
            991050001, token=TOKEN, webview_url=URL, init_data=INIT, max_rounds=2,
            pond_choice="pond", bait_choice="bait", transport=transport,
            adapter=adapter_with_limit(), sleeper=lambda _delay: None,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.01)
            retained = not task.done()
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as raised:
                await task
        assert retained
        assert calls == sequence[:boundary_index + 1]
        result = raised.value.result
        assert result["status"] == "cancelled"
        if boundary_index >= 5:
            assert_one_settlement(result)
        else:
            assert result["data"]["settled_count"] == 0
            assert result["data"]["catches"] == []

    asyncio.run(run())


def test_cancel_during_default_long_wait_drains_without_waiting_out_the_bite(monkeypatch):
    entered = threading.Event()
    original_wait = fishing._wait_fishing

    def wait(delay, sleeper, operation_check):
        entered.set()
        return original_wait(delay, sleeper, operation_check)

    monkeypatch.setattr(fishing, "_wait_fishing", wait)
    transport = Mock(return_value={"ok": True, "session": {"phase": "waiting", "biteAt": 70000, "serverNow": 0}})

    async def run():
        task = asyncio.create_task(fishing.run_fishing_miniapp_production_flow(
            991050001, token=TOKEN, webview_url=URL, init_data=INIT, transport=transport,
            adapter=adapter_with_limit(),
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
        finally:
            task.cancel()
            with pytest.raises(MiniAppFlowCancelled) as raised:
                await asyncio.wait_for(task, 2)
        assert raised.value.result["status"] == "cancelled"
        assert transport.call_count == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["no_rod", "daily_limit", "transport", "not_ready", "malformed"])
def test_second_round_failure_preserves_only_the_first_settlement(failure):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if len(calls) > 4:
            if failure in {"no_rod", "daily_limit"}:
                return {"ok": False, "error": failure}
            if endpoint == "result":
                if failure == "transport":
                    raise TimeoutError("fixture timeout")
                if failure == "malformed":
                    return {"ok": True, "ready": "true", "result": settled()["result"]}
                return {"ok": True, "ready": False, "result": settled()["result"]}
        return response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=2, result_poll_limit=2, sleeper=lambda _delay: None,
    )
    assert_one_settlement(result)
    assert calls.count("next") == 1
    assert calls.count("finish") == (1 if failure in {"no_rod", "daily_limit"} else 2)


def test_confirmed_count_survives_a_later_catch_decoder_error(monkeypatch):
    def decode(_data):
        raise ValueError("fixture catch schema changed")

    monkeypatch.setattr(fishing, "extract_fishing_miniapp_catches", decode)
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return response(endpoint)

    result = fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(), max_rounds=2,
    )
    assert result["data"]["settled_count"] == 1
    assert result["data"]["expGain"] == 4
    assert result["data"]["rounds"][0]["status"] == "settled"
    assert result["data"]["catches"] == []
    assert any(event.get("step") == "result" and event.get("ok") for event in result["events"])
    assert calls == ["start", "finish", "result"]
    assert result["error"] == "fixture catch schema changed"
