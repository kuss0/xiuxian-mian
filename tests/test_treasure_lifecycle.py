import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import random
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model import ui
from model.features import cave_treasure_miniapp as worker
from model.features import cave_treasure_runtime as runtime
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppCaptureStore, MiniAppRequestPolicy


IDENTITY = 991250001
TOKEN = "df_FIXTURE125_SECRET"
INIT = "query_id=fixture&hash=FIXTURE125_HASH"
URL = f"https://t.me/fanrenxiuxian_bot?startapp={TOKEN}"


def panel(used=0, limit=2):
    return {"ok": True, "account": {"playerId": IDENTITY}, "dwelling": {
        "hunt": {"used": used, "limit": limit, "remaining": limit - used, "actionPoints": 1},
    }}


def round_state(index=1, *, revealed=False):
    return {"sessionId": f"fixture125-{index}", "status": "active", "size": 1,
            "ap": 0 if revealed else 1, "maxAp": 1, "foundMain": revealed,
            "cells": [{"index": 0, "revealed": revealed}]}


def receipt(index=1):
    return {"sessionId": f"fixture125-{index}", "foundMain": True,
            "loot": [{"name": "fixture_item", "quantity": 1}]}


def scripted_transport(calls, *, limit=2):
    rounds = 0

    def transport(request):
        nonlocal rounds
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return panel(limit=limit)
        if endpoint == "hunt":
            rounds += 1
            return {**panel(rounds, limit), "huntRun": round_state(rounds)}
        if endpoint == "hunt_reveal":
            return {"ok": True, "huntRun": round_state(rounds, revealed=True)}
        if endpoint == "hunt_settle":
            return {**panel(rounds, limit), "huntResult": receipt(rounds)}
        raise AssertionError(endpoint)

    return transport


def adapter(limit=32):
    return replace(worker.build_cave_treasure_miniapp_adapter(), request_policy=MiniAppRequestPolicy(
        min_interval_sec=0, max_requests_per_run=limit,
    ))


def run(transport, **kwargs):
    return worker.run_cave_treasure_miniapp_lab_flow(**dict({
        "token": TOKEN, "init_data": INIT, "transport": transport,
        "rng": random.Random(125), "sleeper": lambda _delay: None,
        "max_steps": 9, "adapter": adapter(), "player_id": IDENTITY,
    }, **kwargs))


def test_start_and_all_mutations_share_one_budget():
    calls = []
    result = run(scripted_transport(calls), adapter=adapter(2))
    assert calls == ["start", "hunt"]
    assert result["error"] == "request_budget_exhausted"
    assert not result.get("outcome_unknown")


def test_selected_player_is_carried_by_every_mutation():
    calls, players = [], []
    base = scripted_transport(calls)

    def transport(request):
        players.append(request["payload"].get("playerId"))
        return base(request)

    player_id = -1_000_000_000_000 - IDENTITY
    run(transport, player_id=player_id)
    assert len(players) > 1 and set(players) == {player_id}


def test_success_on_last_step_retains_the_post_settlement_state():
    calls = []
    result = run(scripted_transport(calls, limit=1), max_steps=3)
    assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
    assert result["status"] == "daily_limit"
    assert result["data"]["state"]["games_used"] == 1
    assert not result["data"]["state"]["in_round"]
    assert result["data"]["settled_count"] == 1


def test_missing_settlement_receipt_never_counts_as_a_round():
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        return {"ok": True} if request["safe_summary"]["endpoint"] == "hunt_settle" else value

    result = run(transport)
    assert result["data"]["settled_count"] == 0
    assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
    assert result["outcome_unknown"]


def test_later_planner_exception_does_not_erase_a_settlement(monkeypatch):
    calls = []
    choose = worker.choose_cave_treasure_action

    def fail_later(state, **kwargs):
        if calls.count("hunt_settle"):
            raise ValueError("fixture later planner error")
        return choose(state, **kwargs)

    monkeypatch.setattr(worker, "choose_cave_treasure_action", fail_later)
    result = asyncio.run(worker.run_cave_treasure_miniapp_production_flow(
        IDENTITY, token=TOKEN, init_data=INIT, webview_url=URL, player_id=IDENTITY,
        transport=scripted_transport(calls), adapter=adapter(), sleeper=lambda _delay: None,
    ))
    assert result["data"]["settled_count"] == 1
    assert result["data"]["results"] == [receipt()]
    assert result["error"] == "fixture later planner error"


def test_cancelled_worker_is_drained_and_keeps_returned_settlement(monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    base = scripted_transport(calls)
    original = worker.run_cave_treasure_miniapp_lab_flow

    def monitored(**kwargs):
        try:
            return original(**kwargs)
        finally:
            finished.set()

    def transport(request):
        if request["safe_summary"]["endpoint"] == "hunt_settle":
            entered.set()
            assert release.wait(3)
        return base(request)

    monkeypatch.setattr(worker, "run_cave_treasure_miniapp_lab_flow", monitored)

    async def scenario():
        task = asyncio.create_task(worker.run_cave_treasure_miniapp_production_flow(
            IDENTITY, token=TOKEN, init_data=INIT, webview_url=URL, player_id=IDENTITY,
            transport=transport, adapter=adapter(), sleeper=lambda _delay: None,
        ))
        outcome = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            drained = not task.done()
            task.cancel()
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError as exc:
                outcome = exc
            assert await asyncio.to_thread(finished.wait, 2)
        assert drained
        assert isinstance(outcome, MiniAppFlowCancelled)
        assert outcome.result["data"]["results"] == [receipt()]
        assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]

    asyncio.run(scenario())


class ReturnedTreasureResultWriter:
    """Caller/projection tests replace the worker; native checkpoint tests do not."""

    def __init__(self, projection, **_kwargs):
        self.owner = projection.owner
        self.sequence, self.closed, self.cancelled = 0, False, False

    def is_current(self):
        return not self.closed and self.owner.is_current()

    def __call__(self, _frame):
        return True

    def finish(self, _result):
        self.closed = True
        return True


@pytest.fixture
def h(monkeypatch):
    saved = deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, IDENTITY)
    state_module.set_global_enabled(True)
    state_module.update_send_as_profile(IDENTITY, username="treasure_owner", enabled=True)
    for key in ("_PUBLIC_ENTRY_LOCKS", "_RUN_LOCKS", "_MANUAL_AUTH_UNTIL"):
        monkeypatch.setattr(runtime, key, {})
    audit, capture_entry = AsyncMock(return_value=True), AsyncMock(return_value=False)
    session = {"ok": True, "init_data": INIT, "player_id": IDENTITY,
               "result": {"ok": True, "data": {"raw": panel()}}}
    loader = AsyncMock(return_value=session)
    result = {"ok": True, "status": "daily_limit", "data": {
        "state": {"games_used": 1, "games_limit": 1}, "settled_count": 1, "results": [receipt()],
    }}
    flow = AsyncMock(return_value=result)
    records = [Mock(return_value={"changed": True, "record_key": "fixture125"}) for _ in range(3)]
    monkeypatch.setattr(runtime, "send_audit_log", audit)
    monkeypatch.setattr(runtime, "capture_cave_public_entry_event", capture_entry)
    monkeypatch.setattr(runtime, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(runtime, "run_cave_treasure_miniapp_production_flow", flow)
    monkeypatch.setattr(runtime, "_capture_store", Mock(return_value=None))
    record_names = ("_record_cave_treasure_business_capture", "_record_cave_treasure_inventory_delta",
                    "_record_cave_treasure_miniapp_state")
    originals = {name: getattr(runtime, name) for name in record_names}
    records[1:] = [Mock(wraps=originals[name]) for name in record_names[1:]]
    monkeypatch.setattr(runtime.treasure_results, "save_state", Mock(return_value=True))
    monkeypatch.setattr(runtime.treasure_operations, "CheckpointWriter", ReturnedTreasureResultWriter)
    for name, mock in zip(record_names, records):
        monkeypatch.setattr(runtime, name, mock)
    yield SimpleNamespace(owner=state_module.get_identity_state(IDENTITY), session=session, result=result,
                          loader=loader, flow=flow, audit=audit, capture_entry=capture_entry, records=records,
                          now=1_800_000_000.0, originals=originals)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


async def call(h, caller):
    if caller == "public":
        return await runtime.run_cave_public_treasure(IDENTITY, URL, now=h.now)
    runtime.authorize_cave_treasure_miniapp_manual_run(IDENTITY, now=h.now)
    event = SimpleNamespace(id=125, message=SimpleNamespace(buttons=[[
        SimpleNamespace(button=SimpleNamespace(text="\u8fdb\u5165\u6d1e\u5e9c", url=URL)),
    ]]))
    message = "\u6d1e\u5e9c"
    assert worker.extract_cave_treasure_miniapp_launch(event, message_text=message)
    with state_module.use_identity(IDENTITY):
        return await runtime.handle_cave_treasure_miniapp_entry(event, message, h.now, result_msg_id=125)


def test_public_and_command_entries_share_the_game_lock(h):
    async def scenario():
        async with runtime._run_lock(IDENTITY):
            result = await runtime.run_cave_public_treasure(IDENTITY, URL, now=h.now)
            assert result["extra"]["status"] == "busy"

    asyncio.run(scenario())
    h.loader.assert_not_awaited()
    h.flow.assert_not_awaited()


def test_public_admission_is_rechecked_after_loading_entry(h):
    async def pause(*_args, **_kwargs):
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")
        return h.session

    h.loader.side_effect = pause
    result = asyncio.run(call(h, "public"))
    assert not result["ok"]
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("caller", ["public", "command"])
def test_result_cannot_be_published_to_a_replaced_owner(h, caller):
    async def replaced(*_args, **_kwargs):
        state_module._meta_state["identity_states"][IDENTITY] = deepcopy(h.owner)
        return h.result

    h.flow.side_effect = replaced
    asyncio.run(call(h, caller))
    h.flow.assert_awaited_once()
    for record in h.records:
        record.assert_not_called()


@pytest.mark.parametrize("caller", ["public", "command"])
def test_unknown_hold_is_not_success_or_daily_exhaustion(h, monkeypatch, caller):
    monkeypatch.setattr(runtime, "_cave_treasure_unknown_hold", lambda _identity, _now: True)
    result = asyncio.run(call(h, caller))
    h.loader.assert_not_awaited()
    h.flow.assert_not_awaited()
    if caller == "public":
        assert not result["ok"] and not result["extra"].get("daily_exhausted")
        assert result["extra"]["outcome_unknown"]


def test_partial_result_keeps_confirmed_inventory_and_reported_materials():
    result = {"ok": False, "status": "result_unknown", "error": "fixture later timeout", "data": {
        "results": [receipt()], "settled_count": 1,
    }}
    assert runtime._cave_treasure_inventory_items(result) == {"fixture_item": 1}
    summary = runtime._format_cave_treasure_summary(result)
    assert "fixture_item" in summary and "fixture later timeout" in summary


def test_successful_multiround_flow_keeps_every_settlement_and_counter():
    calls = []
    result = run(scripted_transport(calls, limit=2))
    assert result["ok"] and result["status"] == "daily_limit"
    assert calls == ["start"] + ["hunt", "hunt_reveal", "hunt_settle"] * 2
    assert result["data"]["results"] == [receipt(1), receipt(2)]
    assert result["data"]["settled_count"] == 2
    assert result["request_budget"]["request_count"] == 7
    assert result["data"]["state"]["games_used"] == 2
    assert not result["data"]["state"]["in_round"]
    assert not result["outcome_unknown"]


@pytest.mark.parametrize("endpoint", ["start", "hunt", "hunt_reveal", "hunt_settle"])
def test_admission_change_after_response_stops_next_dispatch(endpoint):
    calls, allowed = [], [True]
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] == endpoint:
            allowed[0] = False
        return value

    result = run(transport, operation_check=lambda: allowed[0])
    assert result["status"] == "cancelled"
    assert calls[-1] == endpoint
    assert not result["outcome_unknown"]
    assert result["data"]["settled_count"] == (1 if endpoint == "hunt_settle" else 0)


@pytest.mark.parametrize("allowed", [False, None])
def test_invalid_admission_never_dispatches(allowed):
    calls = []
    result = run(scripted_transport(calls), operation_check=lambda: allowed)
    assert not calls and result["status"] == "cancelled"
    assert not result["outcome_unknown"]


def test_admission_exception_never_dispatches():
    calls = []

    def fail():
        raise RuntimeError("fixture admission failure")

    result = run(scripted_transport(calls), operation_check=fail)
    assert not calls and result["error"] == "operation_check_failed"


def test_admission_is_rechecked_after_budget_sleep():
    calls, allowed, sleeps = [], [True], []
    policy = replace(adapter(), request_policy=MiniAppRequestPolicy(min_interval_sec=60))

    def sleep(delay):
        sleeps.append(delay)
        allowed[0] = False

    result = run(scripted_transport(calls), adapter=policy, sleeper=sleep, operation_check=lambda: allowed[0])
    assert sleeps and calls == ["start"]
    assert result["status"] == "cancelled" and not result["outcome_unknown"]


def test_budget_failure_after_first_round_keeps_settlement():
    calls = []
    result = run(scripted_transport(calls), adapter=adapter(4))
    assert result["error"] == "request_budget_exhausted"
    assert calls == ["start", "hunt", "hunt_reveal", "hunt_settle"]
    assert result["data"]["results"] == [receipt()]
    assert not result["outcome_unknown"]


@pytest.mark.parametrize("endpoint", ["hunt", "hunt_reveal", "hunt_settle"])
@pytest.mark.parametrize("failure", ["timeout", "http_503", "http_403", "rejected"])
def test_later_mutation_failure_never_replays_or_drops_previous_round(endpoint, failure):
    calls = []
    base = scripted_transport(calls)
    attempts = []

    def transport(request):
        current = request["safe_summary"]["endpoint"]
        attempts.append(current)
        if calls.count("hunt_settle") == 1 and current == endpoint:
            if failure == "timeout":
                raise TimeoutError("fixture timeout")
            if failure == "rejected":
                return {"ok": False, "error": "fixture rejection"}
            return int(failure.removeprefix("http_")), {"ok": False, "error": "fixture HTTP failure"}
        return base(request)

    result = run(transport)
    expected = ["start", "hunt", "hunt_reveal", "hunt_settle"]
    expected.extend(["hunt", "hunt_reveal", "hunt_settle"][:["hunt", "hunt_reveal", "hunt_settle"].index(endpoint) + 1])
    assert attempts == expected
    assert not result["ok"]
    assert result["data"]["results"] == [receipt()]
    assert result["outcome_unknown"] is (failure in {"timeout", "http_503"})


@pytest.mark.parametrize("status", [408, 425, 429, 503])
def test_unknown_http_outcome_preserves_wait_hint_without_retry(status):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "hunt":
            calls.append("hunt")
            return SimpleNamespace(status_code=status, headers={"Retry-After": "125"},
                                   json=lambda: {"ok": False, "error": "fixture wait"})
        return base(request)

    result = run(transport)
    assert calls == ["start", "hunt"]
    assert result["outcome_unknown"] and result["retry_after_sec"] == 125


@pytest.mark.parametrize("endpoint", ["hunt", "hunt_reveal", "hunt_settle"])
@pytest.mark.parametrize("missing", [None, {}, [], "settled"])
def test_empty_or_wrong_shaped_action_receipt_is_unknown(endpoint, missing):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        value = base(request)
        if request["safe_summary"]["endpoint"] == endpoint:
            return {"ok": True, "huntResult" if endpoint == "hunt_settle" else "huntRun": missing}
        return value

    result = run(transport)
    assert calls[-1] == endpoint
    assert result["outcome_unknown"]
    assert not result["data"]["results"]


def test_nested_settlement_receipt_is_retained():
    calls = []
    base = scripted_transport(calls, limit=1)

    def transport(request):
        value = base(request)
        return {"ok": True, "data": value} if request["safe_summary"]["endpoint"] == "hunt_settle" else value

    result = run(transport)
    assert result["ok"] and result["data"]["results"] == [receipt()]


def test_post_settlement_parser_exception_keeps_receipt(monkeypatch):
    calls = []
    parse = worker.parse_cave_treasure_state

    def fail_after_settle(value):
        if calls.count("hunt_settle"):
            raise ValueError("fixture parser failure")
        return parse(value)

    monkeypatch.setattr(worker, "parse_cave_treasure_state", fail_after_settle)
    result = run(scripted_transport(calls))
    assert result["data"]["results"] == [receipt()]
    assert result["error"] == "fixture parser failure"
    assert not result["outcome_unknown"]


@pytest.mark.parametrize("caller", ["public", "command"])
def test_native_callers_keep_controls_and_pass_selected_player(h, caller):
    result = asyncio.run(call(h, caller))
    assert result
    h.loader.assert_awaited_once()
    h.flow.assert_awaited_once()
    assert h.flow.await_args.kwargs["player_id"] == IDENTITY
    assert h.flow.await_args.kwargs["operation_check"]() is False
    assert h.loader.await_args.kwargs["operation_check"]() is True
    for record in h.records:
        record.assert_called_once()


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("player", [None, True, IDENTITY + 1])
def test_missing_or_mismatched_selected_player_blocks_native_worker(h, caller, player):
    h.session["player_id"] = player
    asyncio.run(call(h, caller))
    h.flow.assert_not_awaited()
    for record in h.records:
        record.assert_not_called()


def change_owner(h, change):
    if change == "replace":
        state_module._meta_state["identity_states"][IDENTITY] = deepcopy(h.owner)
    elif change == "remove":
        state_module.remove_identity(IDENTITY)
    elif change == "rebind":
        state_module.set_identity_account(IDENTITY, IDENTITY + 1)
    elif change == "disable":
        state_module.set_identity_enabled(IDENTITY, False)
    elif change == "pause":
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")
    else:
        raise AssertionError(change)


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("change", ["replace", "remove", "rebind", "disable", "pause"])
def test_native_admission_is_rechecked_after_session_loading(h, caller, change):
    async def load(*_args, **_kwargs):
        change_owner(h, change)
        return h.session

    h.loader.side_effect = load
    asyncio.run(call(h, caller))
    h.flow.assert_not_awaited()
    for record in h.records:
        record.assert_not_called()
    if change == "remove":
        assert not state_module.has_identity(IDENTITY)


@pytest.mark.parametrize("change", ["replace", "remove", "rebind", "disable", "pause"])
def test_command_rechecks_owner_after_entry_capture(h, change):
    async def capture(*_args, **_kwargs):
        change_owner(h, change)
        return False

    h.capture_entry.side_effect = capture
    asyncio.run(call(h, "command"))
    h.loader.assert_not_awaited()
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("change", ["disable", "pause"])
def test_returned_result_is_adopted_when_dispatch_is_disabled(h, caller, change):
    async def flow(*_args, **_kwargs):
        change_owner(h, change)
        return h.result

    h.flow.side_effect = flow
    result = asyncio.run(call(h, caller))
    for record in h.records:
        record.assert_called_once()
    if caller == "public":
        assert result["ok"] and result["extra"]["operation_cancelled"]
        assert result["extra"]["settled_count"] == 1


@pytest.mark.parametrize("caller", ["public", "command"])
def test_native_callers_allow_maintenance_http(h, caller):
    state_module.set_global_enabled(False)
    state_module.set_global_pause_source("tianzun_maintenance")
    asyncio.run(call(h, caller))
    h.flow.assert_awaited_once()


def test_public_observation_invalidates_native_chain(h):
    allowed = [True]

    async def load(*_args, **_kwargs):
        allowed[0] = False
        return h.session

    h.loader.side_effect = load
    with runtime.observe_cave_public_entry(IDENTITY, URL, operation_check=lambda: allowed[0]):
        result = asyncio.run(call(h, "public"))
    assert not result["ok"]
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("caller", ["public", "command"])
@pytest.mark.parametrize("endpoint", ["hunt", "hunt_reveal", "hunt_settle"])
def test_native_cancellation_drains_worker_before_releasing_both_locks(h, caller, endpoint):
    entered, release = threading.Event(), threading.Event()
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == endpoint:
            entered.set()
            assert release.wait(3)
        return base(request)

    async def flow(*args, **kwargs):
        return await worker.run_cave_treasure_miniapp_production_flow(
            *args, **kwargs, transport=transport, adapter=adapter(), sleeper=lambda _delay: None,
        )

    h.flow.side_effect = flow

    async def scenario():
        task = asyncio.create_task(call(h, caller))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            assert runtime._public_entry_lock(IDENTITY).locked()
            assert runtime._run_lock(IDENTITY).locked()
            busy = await runtime.run_cave_public_treasure(IDENTITY, URL, now=h.now)
            assert busy["extra"]["status"] == "busy"
            if caller == "public":
                assert await call(h, "command") is True
                assert h.loader.await_count == h.flow.await_count == 1
            task.cancel()
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as cancelled:
                await task
        assert cancelled.value.result["extra"]["settled_count"] == (1 if endpoint == "hunt_settle" else 0)
        assert not runtime._public_entry_lock(IDENTITY).locked()
        assert not runtime._run_lock(IDENTITY).locked()
        assert calls[-1] == endpoint

    asyncio.run(scenario())
    for record in h.records:
        record.assert_called_once()


@pytest.mark.parametrize("caller", ["public", "command"])
def test_notification_failure_preserves_adopted_result(h, caller):
    h.audit.side_effect = RuntimeError("fixture notification failure")
    result = asyncio.run(call(h, caller))
    h.flow.assert_awaited_once()
    for record in h.records:
        record.assert_called_once()
    if caller == "public":
        assert result["ok"] and result["extra"]["rewards"] == {"fixture_item": 1}


@pytest.mark.parametrize("caller", ["public", "command"])
def test_notification_cancellation_carries_already_adopted_result(h, caller):
    async def audit(message, **_kwargs):
        if "fixture_item" in message:
            raise asyncio.CancelledError

    h.audit.side_effect = audit
    with pytest.raises(MiniAppFlowCancelled) as cancelled:
        asyncio.run(call(h, caller))
    assert cancelled.value.result["extra"]["rewards"] == {"fixture_item": 1}
    for record in h.records:
        record.assert_called_once()


def test_account_shared_game_lock_excludes_channel_and_physical_account(h):
    sibling = IDENTITY + 1
    state_module.set_identity_account(sibling, IDENTITY)
    assert runtime._run_lock(sibling) is runtime._run_lock(IDENTITY)
    assert runtime._run_lock(IDENTITY + 2) is not runtime._run_lock(IDENTITY)


def test_unknown_hold_survives_day_change_and_applies_to_shared_account(h):
    sibling = IDENTITY + 1
    state_module.set_identity_account(sibling, IDENTITY)
    h.originals["_record_cave_treasure_miniapp_state"](IDENTITY, {
        "ok": False, "status": "result_unknown", "outcome_unknown": True,
    }, now=h.now)
    assert runtime._cave_treasure_unknown_hold(IDENTITY, h.now + 86400)
    assert runtime._cave_treasure_unknown_hold(sibling, h.now + 86400)
    assert not runtime._cave_treasure_unknown_hold(IDENTITY + 2, h.now + 86400)
    assert not ui._cave_public_background_action_due("treasure", IDENTITY, h.now + 86400)
    assert not ui._cave_public_background_action_due("treasure", sibling, h.now + 86400)


def test_later_daily_summary_cannot_clear_unresolved_treasure_action(h):
    record = h.originals["_record_cave_treasure_miniapp_state"]
    record(IDENTITY, {"ok": False, "status": "result_unknown", "outcome_unknown": True}, now=h.now)
    before = deepcopy(state_module.get_miniapp_state_records())
    result = record(IDENTITY, h.result, now=h.now + 86400)
    assert not result["changed"]
    assert state_module.get_miniapp_state_records() == before


def test_in_progress_last_round_is_still_due_for_settlement(h, monkeypatch):
    monkeypatch.setattr(ui, "_cave_public_background_daily_done", set())
    state_module.set_miniapp_state_records({f"{IDENTITY}:cave_treasure": {
        "updated_at": h.now, "state": {"games_used": 1, "games_limit": 1, "in_round": True},
    }})
    assert ui._cave_public_background_action_due("treasure", IDENTITY, h.now + 1)


def test_partial_consumers_ignore_unsettled_rewards_and_keep_confirmed_receipt(h):
    result = {"ok": False, "status": "result_unknown", "error": "fixture timeout", "data": {
        "results": [receipt()], "settled_count": 1, "raw": {"rewards": [{"name": "unconfirmed", "quantity": 99}]},
    }}
    assert runtime._cave_treasure_inventory_items(result) == {"fixture_item": 1}
    assert "unconfirmed" not in runtime._format_cave_treasure_summary(result)
    capture = MiniAppCaptureStore()
    h.originals["_record_cave_treasure_business_capture"](capture, result, source="fixture125", now=h.now)
    serialized = json.dumps(capture.records)
    assert "fixture_item" in serialized and "unconfirmed" not in serialized
    inventory = h.originals["_record_cave_treasure_inventory_delta"](IDENTITY, result, now=h.now)
    assert inventory["record"]["items"] == {"fixture_item": 1}


def test_transport_diagnostics_remain_secret_safe():
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "hunt":
            raise TimeoutError(f"token={TOKEN} hash=FIXTURE125_HASH Authorization: Bearer fixture-secret")
        return base(request)

    serialized = json.dumps(run(transport))
    assert TOKEN not in serialized and "FIXTURE125_HASH" not in serialized and "fixture-secret" not in serialized


def test_production_pool_receives_cancellable_operation_guard(monkeypatch):
    calls = []
    pool = Mock(return_value=scripted_transport(calls, limit=1))
    monkeypatch.setattr(worker, "build_pooled_miniapp_transport", pool)
    result = asyncio.run(worker.run_cave_treasure_miniapp_production_flow(
        IDENTITY, token=TOKEN, init_data=INIT, webview_url=URL, player_id=IDENTITY,
        adapter=adapter(), sleeper=lambda _delay: None,
    ))
    assert result["ok"] and result["data"]["settled_count"] == 1
    assert callable(pool.call_args.kwargs["operation_check"])


def test_auth_boundary_rechecks_admission_before_opening_worker(monkeypatch):
    allowed, calls = [True], []

    async def auth(*_args, **kwargs):
        assert kwargs["operation_check"]() is True
        allowed[0] = False
        return INIT

    monkeypatch.setattr(worker, "request_cave_treasure_miniapp_init_data", auth)
    result = asyncio.run(worker.run_cave_treasure_miniapp_production_flow(
        IDENTITY, token=TOKEN, webview_url=URL, player_id=IDENTITY,
        transport=scripted_transport(calls), adapter=adapter(), sleeper=lambda _delay: None,
        operation_check=lambda: allowed[0],
    ))
    assert result["status"] == "cancelled" and not calls


@pytest.mark.parametrize("player", [True, False, 0, -1, float(IDENTITY), [], IDENTITY + 1])
def test_production_rejects_invalid_selection_before_auth_or_transport(monkeypatch, player):
    auth, transport = AsyncMock(return_value=INIT), Mock()
    monkeypatch.setattr(worker, "request_cave_treasure_miniapp_init_data", auth)
    result = asyncio.run(worker.run_cave_treasure_miniapp_production_flow(
        IDENTITY, token=TOKEN, webview_url=URL, player_id=player, transport=transport,
    ))
    assert not result["ok"] and result["error"].startswith("cave_action_player_")
    auth.assert_not_awaited()
    transport.assert_not_called()
