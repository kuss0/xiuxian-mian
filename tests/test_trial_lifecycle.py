import asyncio
import copy
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_runtime as cave
from model.features import trial_miniapp as trial
from model.features import trial_runtime as runtime
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppRequestAborted, MiniAppRequestBudget, MiniAppRequestPolicy


IDENTITY, ACCOUNT = 991210001, 7121
TOKEN = "trial_FIXTURE121"
INIT = "query_id=fixture&hash=FIXTURE121_SECRET"
URL = "https://t.me/fanrenxiuxian_bot?startapp=" + TOKEN


def adapter_with_limit(count=32):
    return replace(trial.build_trial_miniapp_adapter(), request_policy=MiniAppRequestPolicy(
        min_interval_sec=0, max_requests_per_run=count,
    ))


def challenge(index=1):
    return {
        "challengeId": f"fixture121-{index}", "mode": "tianjiMeridianV1", "sequence": ["p1"],
        "points": [{"id": "p1", "x": 12, "y": 34}], "minDurationMs": 20, "maxDurationMs": 1000,
    }


def response(endpoint, index=1):
    if endpoint in {"start", "next"}:
        return {"ok": True, "challenge": challenge(index)}
    return {"ok": True, "result": {"traceGain": 3}, "dailyProgress": {"remaining": 9}}


def transport_recording(calls):
    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return response(endpoint, 1 + calls.count("next"))
    return transport


def assert_one_settlement(result):
    assert result["data"]["settled_count"] == 1, result
    assert result["data"]["results"] == [{"traceGain": 3}], result


def test_trial_shares_budget_across_finish_and_next():
    calls = []
    result = trial.run_trial_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport_recording(calls),
        adapter=adapter_with_limit(3), max_rounds=3, sleeper=lambda _delay: None,
    )
    assert calls == ["start", "finish", "next"]
    assert result["error"] == "request_budget_exhausted"
    assert_one_settlement(result)


def test_last_requested_round_does_not_open_an_unused_next_round():
    calls = []
    result = trial.run_trial_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport_recording(calls),
        adapter=adapter_with_limit(), max_rounds=2, sleeper=lambda _delay: None,
    )
    assert calls == ["start", "finish", "next", "finish"]
    assert result["data"]["settled_count"] == 2


def test_later_wait_exception_preserves_trial_settlements():
    calls, waits = [], []

    def sleep(delay):
        waits.append(delay)
        if len(waits) == 2:
            raise RuntimeError("fixture later wait failed")

    result = asyncio.run(trial.run_trial_miniapp_production_flow(
        IDENTITY, token=TOKEN, webview_url=URL, init_data=INIT, max_rounds=3,
        transport=transport_recording(calls), adapter=adapter_with_limit(), sleeper=sleep,
    ))
    assert_one_settlement(result)
    assert result["error"] == "fixture later wait failed"
    assert calls == ["start", "finish", "next"]


def test_cancelled_trial_drains_finish_before_releasing_caller(monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    original = trial.run_trial_miniapp_loop_lab_flow

    def worker(**kwargs):
        try:
            return original(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(trial, "run_trial_miniapp_loop_lab_flow", worker)

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            entered.set()
            assert release.wait(3)
        return response(endpoint, 1 + calls.count("next"))

    async def run():
        task = asyncio.create_task(trial.run_trial_miniapp_production_flow(
            IDENTITY, token=TOKEN, webview_url=URL, init_data=INIT, max_rounds=3,
            transport=transport, adapter=adapter_with_limit(), sleeper=lambda _delay: None,
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
        assert retained and retained_twice, "caller released exclusion while HTTP was still active"
        assert isinstance(outcome, MiniAppFlowCancelled)
        assert_one_settlement(outcome.result)
        assert calls == ["start", "finish"]

    asyncio.run(run())


@pytest.fixture
def h(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_global_enabled(True)
    state_module.update_send_as_profile(IDENTITY, enabled=True, username="trial_owner")
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(runtime, "_RUN_LOCKS", {})
    monkeypatch.setattr(runtime, "_MANUAL_AUTH_UNTIL", {})
    monkeypatch.setattr(runtime, "_BATCH_RUNS", {})
    monkeypatch.setattr(runtime, "_BATCH_BY_IDENTITY", {})
    monkeypatch.setattr(runtime.trial_operations, "save_state", Mock(return_value=True))
    monkeypatch.setattr(cave, "_capture_store", Mock(return_value=None))
    monkeypatch.setattr(cave, "_trial_miniapp_capture_store", Mock(return_value=None))
    monkeypatch.setattr(runtime, "_trial_miniapp_capture_store", Mock(return_value=None))
    audit = AsyncMock(return_value=True)
    monkeypatch.setattr(cave, "send_audit_log", audit)
    monkeypatch.setattr(runtime, "send_audit_log", audit)
    capture = Mock()
    monkeypatch.setattr(cave, "_record_trial_business_capture", capture)
    monkeypatch.setattr(runtime, "_record_trial_business_capture", capture)
    session = {"ok": True, "init_data": INIT, "player_id": IDENTITY, "result": {
        "ok": True, "data": {"raw": {"account": {"externalApps": {"groups": [{"apps": [{
            "key": "trial", "title": "\u5929\u673a\u8bd5\u70bc", "available": True, "action": "trial",
        }]}]}}}},
    }}
    loader = AsyncMock(return_value=session)
    external = AsyncMock(return_value={"ok": True, "data": {"url": URL}})
    result = {"ok": True, "status": "settled", "data": {"settled_count": 1, "results": [{"traceGain": 3}]}}
    flow = AsyncMock(return_value=result)
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_external_action_production_flow", external)
    monkeypatch.setattr(cave, "run_trial_miniapp_production_flow", flow)
    monkeypatch.setattr(runtime, "run_trial_miniapp_production_flow", flow)
    yield SimpleNamespace(
        owner=state_module.get_identity_state(IDENTITY), loader=loader, external=external, session=session,
        result=result, flow=flow, audit=audit, capture=capture, now=1700000000,
        public_url="https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE121",
    )
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


@pytest.mark.parametrize("boundary", ["session", "external"])
def test_public_trial_pause_during_entry_does_not_reach_game(h, boundary):
    async def invalidate(*_args, **_kwargs):
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")
        return h.session if boundary == "session" else {"ok": True, "data": {"url": URL}}

    (h.loader if boundary == "session" else h.external).side_effect = invalidate
    result = asyncio.run(cave.run_cave_public_trial(IDENTITY, h.public_url, now=h.now))
    h.flow.assert_not_awaited()
    h.capture.assert_not_called()
    assert not result["ok"]


def test_public_and_command_trial_share_exclusion(h):
    async def run():
        async with runtime._run_lock(IDENTITY):
            return await cave.run_cave_public_trial(IDENTITY, h.public_url, now=h.now)

    result = asyncio.run(run())
    h.loader.assert_not_awaited()
    h.flow.assert_not_awaited()
    assert not result["ok"]


def test_public_trial_does_not_publish_a_replaced_owners_result(h):
    async def invalidate(*_args, **_kwargs):
        state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(h.owner)
        return h.result

    h.flow.side_effect = invalidate
    result = asyncio.run(cave.run_cave_public_trial(IDENTITY, h.public_url, now=h.now))
    assert not result["ok"]
    h.capture.assert_not_called()


def test_trial_batch_partial_still_reports_confirmed_gains(h):
    runtime._BATCH_RUNS["batch"] = {
        "batch_id": "batch", "identity_ids": [IDENTITY], "send": {}, "finalized": False,
        "results": {IDENTITY: {"ok": True, "status": "partial", "error": "next unavailable", "data": {
            "settled_count": 1, "results": [{"traceGain": 3}],
        }}},
    }
    assert asyncio.run(runtime.finalize_trial_batch_run("batch"))
    text = h.audit.await_args.args[0]
    assert "\u5929\u673a\u6b8b\u75d5+3" in text
    assert "0/1" in text


def run_worker(calls, *, max_rounds=3, transport=None, **kwargs):
    return trial.run_trial_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport or transport_recording(calls),
        adapter=adapter_with_limit(), max_rounds=max_rounds,
        sleeper=lambda _delay: None, **kwargs,
    )


@pytest.mark.parametrize("embedded", [False, True])
def test_default_budget_remains_bounded_with_many_rounds(embedded):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        result = response(endpoint, len(calls))
        if embedded and endpoint == "finish":
            result["nextChallenge"] = challenge(len(calls) + 1)
        return result

    result = run_worker(calls, max_rounds=99, transport=transport)
    assert len(calls) == 32
    assert result["error"] == "request_budget_exhausted"
    assert result["data"]["settled_count"] == calls.count("finish")


@pytest.mark.parametrize("boundary", ["start", "finish", "next", "restart"])
def test_worker_rechecks_guard_at_each_next_step(boundary):
    calls = []
    allowed = True

    def transport(request):
        nonlocal allowed
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        step = "restart" if endpoint == "start" and len(calls) > 1 else endpoint
        if step == boundary:
            allowed = False
        if endpoint == "next":
            return {"ok": True, "token": "trial_NEXT121"}
        return response(endpoint, len(calls))

    result = run_worker(calls, transport=transport, operation_check=lambda: allowed)
    expected = ["start", "finish", "next", "start"][:["start", "finish", "next", "restart"].index(boundary) + 1]
    assert calls == expected
    assert result["status"] == "cancelled"
    assert result["data"]["settled_count"] == (0 if boundary == "start" else 1)


@pytest.mark.parametrize("boundary", ["proof", "proof_wait", "budget_wait"])
def test_guard_invalidation_during_local_work_prevents_finish(monkeypatch, boundary):
    calls = []
    allowed = True

    def invalidate(*_args, **_kwargs):
        nonlocal allowed
        allowed = False

    if boundary == "proof":
        original = trial.build_trial_proof

        def build(*args, **kwargs):
            proof = original(*args, **kwargs)
            invalidate()
            return proof

        monkeypatch.setattr(trial, "build_trial_proof", build)
    budget = MiniAppRequestBudget(
        MiniAppRequestPolicy(min_interval_sec=1), clock=lambda: 0,
        sleeper=invalidate if boundary == "budget_wait" else lambda _delay: None,
    )
    result = trial.run_trial_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport_recording(calls), adapter=adapter_with_limit(),
        sleeper=invalidate if boundary == "proof_wait" else lambda _delay: None,
        operation_check=lambda: allowed, request_budget=budget,
    )
    assert calls == ["start"]
    assert result["status"] == "cancelled"


@pytest.mark.parametrize("failure", ["exception", "rate_limit", "unavailable"])
def test_later_finish_failure_keeps_prior_rewards_and_transport_evidence(failure):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish" and calls.count("finish") == 2:
            if failure == "exception":
                raise OSError("fixture reset")
            return (429 if failure == "rate_limit" else 503), {"ok": False, "error": "fixture busy"}
        return response(endpoint, len(calls))

    result = run_worker(calls, transport=transport)
    assert calls == ["start", "finish", "next", "finish"]
    assert_one_settlement(result)
    assert result["status"] == "partial"
    event = [event for event in result["events"] if event["step"] == "finish"][-1]
    assert event["error_type"] == "transient"
    assert event["status_code"] == {"exception": 0, "rate_limit": 429, "unavailable": 503}[failure]
    assert event["attempts"] == 1


@pytest.mark.parametrize("boundary", ["slot", "entity", "input", "webview"])
def test_trial_webview_rechecks_guard_after_each_rpc(monkeypatch, boundary):
    allowed = True
    calls = []

    def visit(step):
        nonlocal allowed
        calls.append(step)
        if step == boundary:
            allowed = False

    @asynccontextmanager
    async def slot(**_kwargs):
        visit("slot")
        yield

    async def entity(*_args):
        visit("entity")
        return object()

    async def input_entity(*_args):
        visit("input")
        return object()

    async def webview(*_args):
        visit("webview")
        return SimpleNamespace(url="https://asc.aiopenai.app/?tgWebAppData=query_id%3DFIXTURE121")

    client = AsyncMock(side_effect=webview)
    client.get_entity.side_effect = entity
    client.get_input_entity.side_effect = input_entity
    monkeypatch.setattr(trial, "account_rpc_slot", slot)
    monkeypatch.setattr(trial, "_get_identity_client_with_account", lambda _id: (ACCOUNT, client))
    monkeypatch.setattr(trial.functions.messages, "RequestMainWebViewRequest", Mock(return_value=object()))
    with pytest.raises(MiniAppRequestAborted):
        asyncio.run(trial.request_trial_miniapp_init_data(
            IDENTITY, token=TOKEN, webview_url=URL, operation_check=lambda: allowed,
        ))
    steps = ["slot", "entity", "input", "webview"]
    assert calls == steps[:steps.index(boundary) + 1]


def test_cancelled_authorization_never_starts_trial_worker(monkeypatch):
    entered = asyncio.Event()
    transport = Mock()

    async def authorize(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(trial, "request_trial_miniapp_init_data", authorize)

    async def run():
        task = asyncio.create_task(trial.run_trial_miniapp_production_flow(
            IDENTITY, token=TOKEN, webview_url=URL, transport=transport,
        ))
        await entered.wait()
        task.cancel()
        with pytest.raises(MiniAppFlowCancelled) as error:
            await task
        assert error.value.result["status"] == "cancelled"

    asyncio.run(run())
    transport.assert_not_called()


def invalidate_owner(h, change):
    if change == "removed":
        state_module.remove_identity(IDENTITY)
    elif change == "replaced":
        state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(h.owner)
    elif change == "rebound":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    elif change == "disabled":
        state_module.set_identity_enabled(IDENTITY, False)
    elif change == "paused":
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")
    else:
        raise AssertionError(change)


async def call_trial(h, caller, *, batch_id=""):
    if caller == "public":
        return await cave.run_cave_public_trial(IDENTITY, h.public_url, now=h.now)
    runtime.authorize_trial_miniapp_manual_run(IDENTITY, now=h.now, batch_id=batch_id)
    event = SimpleNamespace(id=121, message=SimpleNamespace(buttons=[[
        SimpleNamespace(button=SimpleNamespace(text="trial", url=URL)),
    ]]))
    with state_module.use_identity(IDENTITY):
        return await runtime.handle_trial_miniapp_entry(event, "trial", h.now, result_msg_id=121)


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "disabled", "paused"])
@pytest.mark.parametrize("boundary", ["session", "external"])
def test_public_trial_revalidates_owner_during_entry(h, change, boundary):
    async def invalidate(*_args, **kwargs):
        assert kwargs["operation_check"]() is True
        invalidate_owner(h, change)
        assert kwargs["operation_check"]() is False
        return h.session if boundary == "session" else {"ok": True, "data": {"url": URL}}

    (h.loader if boundary == "session" else h.external).side_effect = invalidate
    result = asyncio.run(call_trial(h, "public"))
    assert not result["ok"]
    h.flow.assert_not_awaited()
    h.capture.assert_not_called()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "disabled", "paused"])
def test_command_trial_revalidates_after_start_notification(h, change):
    async def invalidate(*_args, **_kwargs):
        invalidate_owner(h, change)
        return True

    h.audit.side_effect = invalidate
    assert asyncio.run(call_trial(h, "command"))
    h.flow.assert_not_awaited()
    h.capture.assert_not_called()


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "disabled", "paused"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_settlement_ownership_is_distinct_from_dispatch_permission(h, caller, change, cancelled):
    async def flow(*_args, **kwargs):
        assert kwargs["operation_check"]() is True
        invalidate_owner(h, change)
        assert kwargs["operation_check"]() is False
        if cancelled:
            raise MiniAppFlowCancelled(h.result)
        return h.result

    h.flow.side_effect = flow
    owns_result = change in {"disabled", "paused"}
    if cancelled:
        with pytest.raises(MiniAppFlowCancelled) as error:
            asyncio.run(call_trial(h, caller))
        assert bool(error.value.result) == owns_result
    else:
        result = asyncio.run(call_trial(h, caller))
        if caller == "public":
            assert bool(result["ok"]) == owns_result
            if owns_result:
                assert result["extra"]["gains"] == {"\u5929\u673a\u6b8b\u75d5": 3}
                assert result["extra"]["operation_cancelled"]
    assert h.capture.call_count == int(owns_result)


def test_public_observation_guard_survives_entry_await(h):
    allowed = True

    async def invalidate(*_args, **_kwargs):
        nonlocal allowed
        allowed = False
        return h.session

    h.loader.side_effect = invalidate
    with cave.observe_cave_public_entry(IDENTITY, h.public_url, operation_check=lambda: allowed):
        result = asyncio.run(call_trial(h, "public"))
    assert not result["ok"]
    h.external.assert_not_awaited()
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("caller", ["public", "command"])
def test_tianzun_maintenance_does_not_block_trial_http(h, caller):
    state_module.set_global_enabled(False)
    state_module.set_global_pause_source("tianzun_maintenance")
    result = asyncio.run(call_trial(h, caller))
    assert result
    h.flow.assert_awaited_once()


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("outcome", ["settled", "unknown"])
def test_caller_keeps_shared_lock_until_native_finish_is_drained(h, monkeypatch, caller, outcome):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            entered.set()
            assert release.wait(3)
            if outcome == "unknown":
                raise OSError("fixture finish response lost")
        return response(endpoint)

    async def flow(identity_id, **kwargs):
        kwargs.update(init_data=INIT, adapter=adapter_with_limit(), transport=transport, sleeper=lambda _delay: None)
        return await trial.run_trial_miniapp_production_flow(identity_id, **kwargs)

    monkeypatch.setattr(cave, "run_trial_miniapp_production_flow", flow)
    monkeypatch.setattr(runtime, "run_trial_miniapp_production_flow", flow)

    async def run():
        task = asyncio.create_task(call_trial(h, caller))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert runtime._run_lock(IDENTITY).locked()
            if caller == "public":
                assert cave._public_entry_lock(IDENTITY).locked()
            competing = await cave.run_cave_public_trial(IDENTITY, h.public_url, now=h.now)
            assert competing["extra"]["status"] == "busy"
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled):
                await task
        assert not runtime._run_lock(IDENTITY).locked()
        assert not cave._public_entry_lock(IDENTITY).locked()

    asyncio.run(run())
    assert calls == ["start", "finish"]
    assert h.capture.call_count == int(outcome == "settled")
    record = h.owner[runtime.trial_operations.STATE_KEY]
    assert runtime.trial_operations.valid_record(record)
    assert record["checkpoint"]["phase"] == "complete"
    assert record["checkpoint"]["outcome_unknown"] is (outcome == "unknown")
    assert len(record["checkpoint"]["round_receipts"]) == int(outcome == "settled")


@pytest.mark.parametrize("caller", ["public", "command"])
def test_final_notification_cancellation_retains_settlement(h, caller):
    async def audit(text, **_kwargs):
        if "\u63a5\u7ba1\u5165\u53e3" not in text:
            raise asyncio.CancelledError()
        return True

    h.audit.side_effect = audit
    with pytest.raises(MiniAppFlowCancelled) as error:
        asyncio.run(call_trial(h, caller))
    assert error.value.result
    h.capture.assert_called_once()


def batch_fixture(h):
    batch = {
        "batch_id": "batch", "identity_ids": [IDENTITY], "send": {}, "finalized": False,
        "results": {IDENTITY: copy.deepcopy(h.result)},
    }
    runtime._BATCH_RUNS["batch"] = batch
    runtime._BATCH_BY_IDENTITY[IDENTITY] = "batch"
    return batch


@pytest.mark.parametrize("status,ok,completed", [
    ("settled", True, True), ("daily_limit", False, True), ("settled", "yes", False),
    ("partial", True, False), ("next_unavailable", True, False), ("cancelled", True, False),
    ("failed", True, False), ("unknown", True, False),
])
def test_completion_requires_a_terminal_status(status, ok, completed):
    assert runtime._trial_result_completed_ok({"status": status, "ok": ok}) is completed


@pytest.mark.parametrize("failure", [False, None, "true", "exception"])
def test_failed_batch_delivery_keeps_results_for_an_explicit_retry(h, failure):
    batch = batch_fixture(h)
    if failure == "exception":
        h.audit.side_effect = OSError("fixture notification failure")
    else:
        h.audit.return_value = failure
    assert not asyncio.run(runtime.finalize_trial_batch_run("batch"))
    assert runtime._BATCH_RUNS["batch"] is batch
    assert not batch["finalized"]
    assert runtime._BATCH_BY_IDENTITY[IDENTITY] == "batch"
    h.audit.side_effect = None
    h.audit.return_value = True
    assert asyncio.run(runtime.finalize_trial_batch_run("batch"))
    assert "batch" not in runtime._BATCH_RUNS


def test_concurrent_batch_finalizers_only_send_one_identical_report(h):
    batch_fixture(h)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def deliver(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return True

        h.audit.side_effect = deliver
        first = asyncio.create_task(runtime.finalize_trial_batch_run("batch"))
        await entered.wait()
        second = asyncio.create_task(runtime.finalize_trial_batch_run("batch"))
        await asyncio.sleep(0)
        release.set()
        assert await first
        assert not await second

    asyncio.run(run())
    h.audit.assert_awaited_once()


def test_late_batch_result_is_not_deleted_by_inflight_report(h):
    batch = batch_fixture(h)
    batch["results"].clear()

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def deliver(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return True

        h.audit.side_effect = deliver
        first = asyncio.create_task(runtime.finalize_trial_batch_run("batch", reason="timeout"))
        await entered.wait()
        runtime._record_trial_batch_result("batch", IDENTITY, h.result)
        second = asyncio.create_task(runtime.finalize_trial_batch_run("batch"))
        await asyncio.sleep(0)
        release.set()
        assert not await first
        assert await second

    asyncio.run(run())
    assert h.audit.await_count == 2
    assert "\u5929\u673a\u6b8b\u75d5+3" in h.audit.await_args.args[0]
    assert "batch" not in runtime._BATCH_RUNS


def test_replaced_batch_survives_old_notification(h):
    batch_fixture(h)
    replacement = {}

    async def deliver(*_args, **_kwargs):
        replacement.update(batch_fixture(h))
        runtime._BATCH_RUNS["batch"] = replacement
        return True

    h.audit.side_effect = deliver
    assert not asyncio.run(runtime.finalize_trial_batch_run("batch"))
    assert runtime._BATCH_RUNS["batch"] is replacement
    assert not replacement["finalized"]


def test_cancelled_batch_delivery_does_not_mark_finalized(h):
    batch = batch_fixture(h)
    h.audit.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.finalize_trial_batch_run("batch"))
    assert runtime._BATCH_RUNS["batch"] is batch
    assert not batch["finalized"]


@pytest.mark.parametrize("caller", ["public", "command"])
def test_cancelled_failed_finish_retains_transport_evidence_without_rewards(h, caller):
    outcome = {"ok": False, "status": "failed", "error": "fixture reset", "data": {
        "settled_count": 0, "results": [],
    }, "events": [{"step": "finish", "status_code": 0, "error_type": "transient", "attempts": 1}]}

    async def flow(*_args, **_kwargs):
        invalidate_owner(h, "paused")
        raise MiniAppFlowCancelled(outcome)

    h.flow.side_effect = flow
    with pytest.raises(MiniAppFlowCancelled) as error:
        asyncio.run(call_trial(h, caller))
    result = error.value.result
    evidence = result["extra"] if caller == "public" else result
    assert evidence["events"] == outcome["events"]
    assert evidence["error"] == "fixture reset"
    assert not result["ok"]
    h.capture.assert_not_called()


def test_partial_summary_names_both_settled_gains_and_failure(h):
    result = copy.deepcopy(h.result)
    result.update(status="partial", error="fixture next failed")
    summary = runtime._format_trial_summary(result)
    assert "\u5929\u673a\u6b8b\u75d5+3" in summary
    assert "fixture next failed" in summary


@pytest.mark.parametrize("boundary", ["start", "proof_wait", "next"])
def test_cancelled_production_stops_after_other_inflight_boundaries(boundary):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def block():
        entered.set()
        assert release.wait(3)

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == boundary:
            block()
        return response(endpoint)

    def sleeper(_delay):
        if boundary == "proof_wait":
            block()

    async def run():
        task = asyncio.create_task(trial.run_trial_miniapp_production_flow(
            IDENTITY, token=TOKEN, webview_url=URL, init_data=INIT, max_rounds=3,
            transport=transport, adapter=adapter_with_limit(), sleeper=sleeper,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as error:
                await task
        assert error.value.result["data"]["settled_count"] == (1 if boundary == "next" else 0)

    asyncio.run(run())
    assert calls == (["start", "finish", "next"] if boundary == "next" else ["start"])


def test_cancelled_command_result_is_stored_in_its_original_batch(h):
    batch = batch_fixture(h)
    batch["results"].clear()

    async def flow(*_args, **_kwargs):
        raise MiniAppFlowCancelled(h.result)

    h.flow.side_effect = flow
    with pytest.raises(MiniAppFlowCancelled):
        asyncio.run(call_trial(h, "command", batch_id="batch"))
    assert batch["results"][IDENTITY] == h.result
    assert not batch["finalized"]
    h.capture.assert_called_once()
    h.audit.assert_not_awaited()


def test_replaced_batch_does_not_inherit_old_command_result(h):
    original = batch_fixture(h)
    original["results"].clear()
    replacement = {}

    async def flow(*_args, **_kwargs):
        replacement.update(batch_fixture(h))
        replacement["results"].clear()
        runtime._BATCH_RUNS["batch"] = replacement
        return h.result

    h.flow.side_effect = flow
    assert asyncio.run(call_trial(h, "command", batch_id="batch"))
    assert original["results"] == {}
    assert replacement["results"] == {}
    h.capture.assert_called_once()
    h.audit.assert_not_awaited()
