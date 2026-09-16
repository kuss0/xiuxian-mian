import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model.features import tree_miniapp as worker
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppRequestAborted, MiniAppRequestPolicy


IDENTITY = 991310001
TOKEN = "tree_FIXTURE131_SECRET"
INIT = "query_id=fixture131&hash=FIXTURE131_HASH"
URL = f"https://t.me/fanrenxiuxian_bot?startapp={TOKEN}"


def adapter(limit=32):
    return replace(worker.build_tree_miniapp_adapter(), request_policy=MiniAppRequestPolicy(
        min_interval_sec=0, max_requests_per_run=limit,
    ))


def panel(used=None, *, limits=None):
    used = used or {"jump": 0, "fly": 0}
    limits = limits or {"jump": 1, "fly": 1}
    return {"ok": True, "tree": {"maturity": 1}, "council": {"daily": {
        mode: {"used": used[mode], "limit": limits[mode], "remaining": limits[mode] - used[mode]}
        for mode in ("jump", "fly")
    }}}


def scripted_transport(calls, *, limits=None):
    used = {"jump": 0, "fly": 0}

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        mode = request["payload"].get("mode", "")
        calls.append((endpoint, mode))
        if endpoint == "start":
            return panel(used, limits=limits)
        if endpoint == "run_start":
            return {"ok": True, "run": {
                "mode": mode, "runToken": f"private-run131-{mode}-{used[mode]}",
                "seed": f"fixture131-{mode}-{used[mode]}", "runNo": used[mode] + 1,
            }}
        assert endpoint == "run_submit"
        used[mode] += 1
        return {**panel(used, limits=limits), "score": 75 if mode == "jump" else 17,
                "rewards": [{"name": "fixture_material", "qty": 1}]}

    return transport


@pytest.fixture
def h(monkeypatch):
    calls = []

    def proof(mode, _run, **_kwargs):
        score = 75 if mode == "jump" else 17
        return ({"durationMs": 100, "clientScore": score, "charges": [0.5]},
                {"mode": mode, "score": score, "targetScore": score, "durationMs": 100})

    monkeypatch.setattr(worker, "build_tree_game_proof", proof)
    auth = AsyncMock(return_value=INIT)
    monkeypatch.setattr(worker, "request_tree_miniapp_init_data", auth)
    return SimpleNamespace(calls=calls, transport=scripted_transport(calls), auth=auth)


def lab(h, kind, **kwargs):
    params = dict(token=TOKEN, init_data=INIT, transport=h.transport, adapter=adapter(), sleeper=lambda _delay: None)
    if kind == "game":
        params.update(mode="jump", submit=True)
    return getattr(worker, f"run_tree_miniapp_{kind}_lab_flow")(**dict(params, **kwargs))


async def production(h, kind, **kwargs):
    params = dict(token=TOKEN, webview_url=URL, transport=h.transport, adapter=adapter(), sleeper=lambda _delay: None)
    if kind == "game":
        params.update(mode="jump", submit=True)
    if kind == "daily":
        params["init_data"] = INIT
    return await getattr(worker, f"run_tree_miniapp_{kind}_production_flow")(IDENTITY, **dict(params, **kwargs))


@pytest.mark.parametrize("kind", ["start", "game", "daily"])
def test_one_budget_bounds_all_worker_requests(h, kind):
    result = lab(h, kind, adapter=adapter(1))
    assert h.calls == [("start", "")]
    assert result["request_budget"]["request_count"] == 1
    assert not result["outcome_unknown"]
    assert result["ok"] is (kind == "start")


@pytest.mark.parametrize("kind", ["start", "game", "daily"])
def test_invalidated_operation_never_authenticates_or_sends(h, kind):
    result = asyncio.run(production(h, kind, operation_check=lambda: False))
    assert not result["ok"]
    assert h.calls == []
    h.auth.assert_not_awaited()


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("stage", ["start", "run_start", "run_submit"])
def test_control_change_stops_next_request_and_retains_returned_submission(h, kind, stage):
    allowed = True
    original = h.transport

    def transport(request):
        nonlocal allowed
        value = original(request)
        if request["safe_summary"]["endpoint"] == stage:
            allowed = False
        return value

    h.transport = transport
    result = asyncio.run(production(h, kind, operation_check=lambda: allowed))
    expected = [("start", ""), ("run_start", "jump"), ("run_submit", "jump")]
    assert h.calls == expected[:{"start": 1, "run_start": 2, "run_submit": 3}[stage]]
    if stage == "run_submit":
        assert result["data"]["rewards"]["items"] == {"fixture_material": 1}
    else:
        assert not result["ok"]


@pytest.mark.parametrize("kind,stage", [
    ("start", "start"), ("game", "run_start"), ("game", "run_submit"),
    ("daily", "start"), ("daily", "run_start"), ("daily", "run_submit"),
])
def test_cancelled_thread_drains_before_return_and_preserves_received_facts(h, monkeypatch, kind, stage):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_transport = h.transport
    original_flow = getattr(worker, f"run_tree_miniapp_{kind}_lab_flow")

    def transport(request):
        value = original_transport(request)
        if request["safe_summary"]["endpoint"] == stage and not entered.is_set():
            entered.set()
            assert release.wait(3)
        return value

    def flow(**kwargs):
        try:
            return original_flow(**kwargs)
        finally:
            finished.set()

    h.transport = transport
    monkeypatch.setattr(worker, f"run_tree_miniapp_{kind}_lab_flow", flow)

    async def scenario():
        task = asyncio.create_task(production(h, kind))
        cancellation = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            drained = not task.done()
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError as exc:
                cancellation = exc
            assert await asyncio.to_thread(finished.wait, 2)
        assert drained
        assert isinstance(cancellation, MiniAppFlowCancelled)
        result = cancellation.result
        assert isinstance(result, dict)
        if stage == "run_submit":
            assert result["data"]["rewards"]["items"] == {"fixture_material": 1}
        if stage == "run_start":
            assert result["open_run"] and not result["outcome_unknown"]

    asyncio.run(scenario())
    expected = [("start", ""), ("run_start", "jump"), ("run_submit", "jump")]
    assert h.calls == expected[:{"start": 1, "run_start": 2, "run_submit": 3}[stage]]


@pytest.mark.parametrize("stage", ["next_profile", "next_parse", "next_sleep"])
def test_daily_later_exception_cannot_erase_a_confirmed_submission(h, monkeypatch, stage):
    original = h.transport

    def transport(request):
        value = original(request)
        if stage == "next_parse" and request["safe_summary"]["endpoint"] == "run_submit":
            value["tree"]["maturity"] = "not-a-number"
        return value

    h.transport = transport
    if stage == "next_profile":
        normalize = worker.normalize_tree_score_profile

        def profile(mode, value=None):
            if h.calls.count(("run_submit", "jump")):
                raise ValueError("fixture131 later profile")
            return normalize(mode, value)

        monkeypatch.setattr(worker, "normalize_tree_score_profile", profile)

    def sleeper(_delay):
        if stage == "next_sleep" and h.calls.count(("run_submit", "jump")):
            raise ValueError("fixture131 later wait")

    result = asyncio.run(production(h, "daily", sleeper=sleeper))
    assert not result["ok"]
    assert len(result["data"]["runs"]) == 1
    assert result["data"]["rewards"]["items"] == {"fixture_material": 1}


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("stage", ["run_start", "run_submit"])
@pytest.mark.parametrize("status", [408, 429, 503, 0])
def test_uncertain_mutations_keep_their_classification_and_server_wait(h, kind, stage, status):
    original = h.transport

    def transport(request):
        value = original(request)
        if request["safe_summary"]["endpoint"] != stage:
            return value
        if not status:
            raise OSError("fixture131 uncertain transport")
        return SimpleNamespace(status_code=status, headers={"Retry-After": "91"},
                               json=lambda: {"ok": False, "error": "fixture131 uncertain response"})

    h.transport = transport
    result = lab(h, kind)
    assert not result["ok"] and result["outcome_unknown"]
    assert result["status"] == "result_unknown"
    assert h.calls.count((stage, "jump")) == 1
    assert not any(mode == "fly" for _, mode in h.calls)
    if status:
        assert result["retry_after_sec"] == 91


@pytest.mark.parametrize("stage", ["slot", "entity", "input", "webview"])
def test_webview_auth_checks_operation_at_each_await(monkeypatch, stage):
    allowed = True
    calls = []

    def reached(name):
        nonlocal allowed
        calls.append(name)
        if stage == name:
            allowed = False

    class Client:
        async def get_entity(self, _bot):
            reached("entity")
            return object()

        async def get_input_entity(self, _bot):
            reached("input")
            return object()

        async def __call__(self, _request):
            reached("webview")
            return SimpleNamespace(url="https://asc.aiopenai.app/#tgWebAppData=query_id%3Dx%26hash%3Dfixture")

    @asynccontextmanager
    async def slot(**_kwargs):
        reached("slot")
        yield

    monkeypatch.setattr(worker, "_get_identity_client_with_account", lambda _id: (1, Client()))
    monkeypatch.setattr(worker, "account_rpc_slot", slot)
    monkeypatch.setattr(worker.functions.messages, "RequestMainWebViewRequest", lambda **kwargs: kwargs)
    with pytest.raises(MiniAppRequestAborted):
        asyncio.run(worker.request_tree_miniapp_init_data(
            IDENTITY, token=TOKEN, webview_url=URL, operation_check=lambda: allowed,
        ))
    assert calls == ["slot", "entity", "input", "webview"][:["slot", "entity", "input", "webview"].index(stage) + 1]


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("stage", ["proof", "wait"])
def test_invalidated_local_work_preserves_allocated_run_without_submitting(h, monkeypatch, kind, stage):
    allowed = True
    original = worker.build_tree_game_proof

    def proof(*args, **kwargs):
        nonlocal allowed
        value = original(*args, **kwargs)
        if stage == "proof":
            allowed = False
        return value

    def sleeper(_delay):
        nonlocal allowed
        allowed = False

    monkeypatch.setattr(worker, "build_tree_game_proof", proof)
    result = lab(h, kind, operation_check=lambda: allowed, sleeper=sleeper)
    assert h.calls == [("start", ""), ("run_start", "jump")]
    assert not result["ok"] and result["open_run"] and not result["outcome_unknown"]
    assert "private-run131" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["game", "daily"])
@pytest.mark.parametrize("stage", ["run_start", "run_submit"])
def test_explicit_business_rejection_is_not_an_unknown_mutation(h, kind, stage):
    original = h.transport

    def transport(request):
        if request["safe_summary"]["endpoint"] == stage:
            h.calls.append((stage, request["payload"].get("mode", "")))
            return 403, {"ok": False, "error": "turnstile_failed"}
        return original(request)

    h.transport = transport
    result = lab(h, kind)
    assert not result["ok"] and not result["outcome_unknown"]
    assert result["status"] == "verification_required"
    assert result["open_run"] is (stage == "run_submit")


@pytest.mark.parametrize("response", [(302, {"ok": False}), (200, {}), (200, "html fixture"), (425, {"ok": False})])
def test_non_authoritative_mutation_response_is_not_treated_as_rejection(h, response):
    original = h.transport

    def transport(request):
        value = original(request)
        return response if request["safe_summary"]["endpoint"] == "run_start" else value

    h.transport = transport
    result = lab(h, "daily")
    assert result["outcome_unknown"] and result["status"] == "result_unknown"
    assert h.calls == [("start", ""), ("run_start", "jump")]


def test_single_submit_never_replaces_missing_server_score_with_local_score(h):
    original = h.transport

    def transport(request):
        value = original(request)
        if request["safe_summary"]["endpoint"] == "run_submit":
            value.pop("score")
        return value

    h.transport = transport
    result = lab(h, "game")
    assert result["status"] == "score_unknown" and not result["ok"]
    assert result["data"]["submit"]["score"] != 75
    assert result["data"]["rewards"]["items"] == {"fixture_material": 1}


@pytest.mark.parametrize("kind", ["game", "daily"])
def test_budget_sleep_failure_is_unsent_not_unknown(h, kind):
    def sleeper(_delay):
        raise ValueError("fixture131 budget wait")

    slow_adapter = replace(adapter(), request_policy=MiniAppRequestPolicy(min_interval_sec=3600))
    result = lab(h, kind, adapter=slow_adapter, sleeper=sleeper)
    assert not result["ok"] and not result["outcome_unknown"] and not result["open_run"]
    assert h.calls == [("start", "")]


@pytest.mark.parametrize("kind", ["start", "game", "daily"])
def test_successful_flow_metadata_and_secrets_are_consistent(h, kind):
    result = asyncio.run(production(h, kind))
    assert result["ok"]
    assert result["request_budget"]["request_count"] == len(h.calls)
    assert not result["open_run"] and not result["outcome_unknown"]
    serialized = json.dumps(result)
    for secret in (TOKEN, INIT, "private-run131", "FIXTURE131_HASH"):
        assert secret not in serialized
