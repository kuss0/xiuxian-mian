import asyncio
import inspect
import threading
from contextvars import ContextVar
from unittest.mock import Mock, patch

import pytest

from model import webapp_core as core
from model.features import miniapp_common


def request():
    return {"method": "POST", "url": "https://miniapp.invalid/api/action", "payload": {}}


@pytest.mark.parametrize("value", [False, None, 0, 1, "true", "false"])
def test_non_boolean_operation_approval_stops_without_retry(value):
    transport = Mock()
    budget = core.MiniAppRequestBudget({"min_interval_sec": 0})
    result = core.execute_miniapp_http_request(
        request(), transport, operation_check=lambda: value,
        retry_safe=True, backoff_sec=(0, 0), request_budget=budget,
    )
    assert not result.ok
    assert result.error_type == "operation_cancelled"
    assert not result.retryable
    assert result.attempts == budget.request_count == 0
    transport.assert_not_called()


def test_throwing_or_async_operation_check_fails_closed():
    def failed():
        raise RuntimeError("fixture failure")

    async def async_check():
        return True

    pending = async_check()
    for operation_check in (failed, lambda: pending):
        transport = Mock()
        result = core.execute_miniapp_http_request(request(), transport, operation_check=operation_check)
        assert result.error_type == "operation_cancelled"
        assert result.attempts == 0
        transport.assert_not_called()
    assert inspect.getcoroutinestate(pending) == inspect.CORO_CLOSED


def test_budget_wait_revalidates_before_transport():
    active = True

    def sleep(_delay):
        nonlocal active
        active = False

    budget = core.MiniAppRequestBudget({"min_interval_sec": 1}, clock=lambda: 0, sleeper=sleep)
    assert budget.acquire()[0]
    transport = Mock()
    result = core.execute_miniapp_http_request(
        request(), transport, operation_check=lambda: active, request_budget=budget,
    )
    assert result.error_type == "operation_cancelled"
    assert result.attempts == 0
    transport.assert_not_called()


def test_global_limiter_wait_revalidates_before_transport(monkeypatch):
    active = True

    def acquire(**_kwargs):
        nonlocal active
        active = False

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(core._GLOBAL_MINIAPP_RATE_LIMITER, "acquire", acquire)
    transport = Mock()
    result = core.execute_miniapp_http_request(request(), transport, operation_check=lambda: active)
    assert result.error_type == "operation_cancelled"
    assert result.attempts == 0
    transport.assert_not_called()


def test_budget_preparation_error_is_not_an_unknown_send():
    budget = core.MiniAppRequestBudget()
    transport = Mock()
    with patch.object(budget, "acquire", side_effect=RuntimeError("fixture budget failed")):
        result = core.execute_miniapp_http_request(request(), transport, request_budget=budget, retry_safe=True)
    assert result.error_type == "preparation"
    assert result.attempts == 0
    assert not result.retryable
    transport.assert_not_called()


def test_cancelled_retry_delay_preserves_the_previous_http_evidence():
    transport = Mock(return_value=(503, {"ok": False, "error": "upstream_failed"}))

    def cancelled(_delay):
        raise core.MiniAppRequestAborted("operation_invalidated")

    result = core.execute_miniapp_http_request(
        request(), transport, retry_safe=True, sleeper=cancelled,
    )
    assert result.status_code == 503
    assert result.attempts == 1
    assert result.error != "operation_invalidated"
    transport.assert_called_once()


def test_cancellation_while_waiting_for_pool_does_not_wait_for_another_request():
    async def run():
        pool = miniapp_common._MiniAppSessionPool()
        loop = asyncio.get_running_loop()
        queued = asyncio.Event()
        session = Mock()
        session.request.return_value = (200, {"ok": True})
        with patch.object(miniapp_common, "_MINIAPP_SESSION_POOL", pool), \
                patch.object(miniapp_common.requests, "Session", return_value=session):
            def flow(operation):
                def check():
                    with pool._lock:
                        slot = pool._request_slots.get(("tower", 36))
                        if slot is not None and slot.users > 1:
                            loop.call_soon_threadsafe(queued.set)
                    return operation.check()

                transport = miniapp_common.build_pooled_miniapp_transport(
                    adapter_key="tower", identity_id=36, proxies={}, operation_check=check,
                )
                return core.execute_miniapp_http_request(
                    request(), transport, operation_check=operation.check, sleeper=operation.sleep,
                )

            task = None
            try:
                with pool.lease("tower", 36, {}, owner=miniapp_common.MiniAppIdentityOwner.capture(36)):
                    slot = pool._request_slots[("tower", 36)]
                    task = asyncio.create_task(miniapp_common.run_miniapp_blocking_flow(flow))
                    await asyncio.wait_for(queued.wait(), 1)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 1)
                    session.request.assert_not_called()
                    assert slot.lock.locked()
                    assert slot.users == 1
                assert not pool._request_slots
            finally:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                pool.close()

    asyncio.run(run())


def test_cancellation_interrupts_a_global_priority_wait(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    limiter = core.MiniAppGlobalRateLimiter()
    limiter.begin_priority("fixture")
    monkeypatch.setattr(core, "_GLOBAL_MINIAPP_RATE_LIMITER", limiter)

    async def run():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        transport = Mock()
        real_acquire = limiter.acquire

        def acquire(**kwargs):
            loop.call_soon_threadsafe(started.set)
            return real_acquire(**kwargs)

        def flow(operation):
            return core.execute_miniapp_http_request(
                request(), transport, operation_check=operation.check, sleeper=operation.sleep,
            )

        with patch.object(limiter, "acquire", side_effect=acquire):
            task = asyncio.create_task(miniapp_common.run_miniapp_blocking_flow(flow))
            try:
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
                transport.assert_not_called()
            finally:
                limiter.end_priority("fixture")
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_thread_draining_preserves_context_and_propagates_repeated_cancellation():
    owner = ContextVar("miniapp_fixture_owner", default=0)
    entered, release = threading.Event(), threading.Event()
    result = {"ok": True, "data": {"confirmed": True}}

    async def run():
        owner.set(36)

        def flow(_operation):
            assert owner.get() == 36
            entered.set()
            assert release.wait(2)
            return result

        task = asyncio.create_task(miniapp_common.run_miniapp_blocking_flow(flow))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(miniapp_common.MiniAppFlowCancelled) as caught:
                await task
        assert caught.value.result is result
        assert task.cancelled()

    asyncio.run(run())
