import asyncio
import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import miniapp_state, persistence, state as state_module, ui
from model.features import cave_treasure_runtime as cave, tree_runtime as runtime
from model.features import tree_miniapp as tree
from model.features import tree_operations
from model.features.miniapp_common import MiniAppFlowCancelled
from test_tree_worker_lifecycle import adapter, scripted_transport


IDENTITY, ACCOUNT, NOW = 991310101, 7131, 1_700_000_000.0
URL = "https://t.me/fanrenxiuxian_bot?startapp=tree_FIXTURE131"
PUBLIC = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE131"
DAY = "2023-11-15"
REAL_LOADER = cave._load_cave_public_identity_session


@pytest.fixture
def h(monkeypatch):
    monkeypatch.setattr(state_module, "_meta_state", deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_global_enabled(True)
    state_module.update_send_as_profile(IDENTITY, enabled=True, username="tree_fixture", sect_name="\u843d\u4e91\u5b97")
    monkeypatch.setattr(runtime, "_MANUAL_AUTH", {})
    monkeypatch.setattr(runtime, "_COORDINATOR", {"phase": "idle", "op_id": "", "identity_id": 0})
    monkeypatch.setattr(runtime, "_GLOBAL_RUN_LOCK", None)
    monkeypatch.setattr(tree_operations, "_ACTIVE", {})
    monkeypatch.setattr(tree_operations, "save_state", Mock(return_value=True))
    monkeypatch.setattr(runtime, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(cave, "_capture_store", Mock(return_value=None))
    monkeypatch.setattr(runtime, "_tree_miniapp_capture_store", Mock(return_value=None))
    monkeypatch.setattr(miniapp_state, "save_state", Mock(return_value=True))
    monkeypatch.setattr(cave, "save_state", Mock(return_value=True))
    monkeypatch.setattr(ui, "save_state", Mock(return_value=True))
    monkeypatch.setattr(ui, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", state_module.get_miniapp_auto_config)
    monkeypatch.setattr(ui, "get_tree_miniapp_score_config", lambda _id: {"jump": {}, "fly": {}})
    monkeypatch.setattr(ui, "_cave_public_background_state", {
        "running": False, "circuit_open_until": 0, "circuit_reason": "", "next_run_at": 0,
    })
    config = {"tree_daily_enabled_identity_ids": [IDENTITY], "cave_public_entry_urls": [PUBLIC]}
    state_module.set_miniapp_auto_config(config)
    result = {"ok": True, "status": "completed", "data": {
        "phase": "completed", "quotas": {}, "runs": [{"mode": "jump", "score": 75}],
        "rewards": {"items": {"fixture_material": 1}, "gains": {}},
    }}
    flow = AsyncMock(return_value=result)
    audit, capture = AsyncMock(return_value=True), Mock()
    monkeypatch.setattr(runtime, "run_tree_miniapp_daily_production_flow", flow)
    monkeypatch.setattr(runtime, "send_audit_log", audit)
    monkeypatch.setattr(ui, "send_audit_log", audit)
    monkeypatch.setattr(runtime, "_record_tree_business_capture", capture)
    session = {"ok": True, "player_id": IDENTITY, "init_data": "fixture-init", "result": {
        "ok": True, "data": {"raw": {"account": {"externalApps": {"groups": [{"apps": [{
            "key": "tree", "title": "\u843d\u4e91\u7075\u6811", "available": True, "action": "tree",
        }]}]}}}},
    }}
    loader = AsyncMock(return_value=session)
    external = AsyncMock(return_value={"ok": True, "data": {"url": URL}})
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_external_action_production_flow", external)
    return SimpleNamespace(flow=flow, audit=audit, capture=capture, result=result, session=session,
                           loader=loader, external=external, config=config, monkeypatch=monkeypatch)


def record():
    return state_module.get_miniapp_state_records().get(f"{IDENTITY}:tree", {}).get("state", {})


def replace_control(kind):
    if kind == "removed":
        state_module.remove_identity(IDENTITY)
    elif kind == "replaced":
        state_module._meta_state["identity_states"][IDENTITY] = deepcopy(state_module.get_identity_state(IDENTITY))
    elif kind == "rebound":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    elif kind == "disabled":
        state_module.set_identity_enabled(IDENTITY, False)
    elif kind == "paused":
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("manual")
    elif kind == "record":
        miniapp_state.record_miniapp_state(IDENTITY, "tree", {"fixture_replacement": True}, now=NOW + 1)
    elif kind == "coordinator":
        runtime._COORDINATOR.update(phase="running", op_id="fixture-new-op", identity_id=IDENTITY + 1)
    elif kind == "authorization":
        runtime.authorize_tree_miniapp_manual_run(IDENTITY, now=NOW, op_id="fixture-new-auth")
    else:
        raise AssertionError(kind)


async def invoke(kind, **kwargs):
    if kind == "direct":
        return await runtime.run_tree_miniapp_daily_direct(
            IDENTITY, token="tree_FIXTURE131", webview_url=URL, init_data="fixture-init",
            day_key=DAY, op_id="fixture131-op", now=NOW, **kwargs,
        )
    if kind == "public":
        return await cave.run_cave_public_tree(IDENTITY, PUBLIC, day_key=DAY, op_id="fixture131-op", now=NOW, **kwargs)
    assert kind == "command"
    prepared = runtime.prepare_tree_miniapp_daily_run(IDENTITY, enabled=True, day_key=DAY, now=NOW)
    assert prepared["ok"]
    runtime.finalize_tree_miniapp_daily_command(prepared["op_id"], 131, now=NOW)
    button = SimpleNamespace(button=SimpleNamespace(text="tree", url=URL))
    event = SimpleNamespace(id=132, message=SimpleNamespace(buttons=[[button]]))
    with state_module.use_identity(IDENTITY):
        return await runtime.handle_tree_miniapp_entry(event, "@tree_fixture", NOW, reply_to=131)


@pytest.mark.parametrize("kind", ["direct", "command", "public"])
@pytest.mark.parametrize("diagnostic", ["capture", "audit"])
def test_diagnostic_failure_cannot_erase_saved_result(h, kind, diagnostic):
    getattr(h, diagnostic).side_effect = OSError("fixture131 diagnostic unavailable")
    result = asyncio.run(invoke(kind))
    assert result
    assert record()["runs"] == h.result["data"]["runs"]
    assert record()["rewards"]["items"] == h.result["data"]["rewards"]["items"]
    assert record()["rewards"].get("gains", {}) == h.result["data"]["rewards"]["gains"]
    assert runtime._COORDINATOR["phase"] == "completed"


@pytest.mark.parametrize("kind", ["direct", "command", "public"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "record", "coordinator"])
def test_late_result_cannot_publish_over_replacement_owner_or_operation(h, kind, change):
    captured = {}

    async def flow(*_args, **kwargs):
        replace_control(change)
        captured["record"] = deepcopy(record())
        captured["coordinator"] = deepcopy(runtime._COORDINATOR)
        guard = kwargs.get("operation_check")
        assert callable(guard) and not guard()
        return h.result

    h.flow.side_effect = flow
    asyncio.run(invoke(kind))
    assert record() == captured["record"]
    if change == "coordinator":
        assert runtime._COORDINATOR == captured["coordinator"]
    h.capture.assert_not_called()


@pytest.mark.parametrize("kind", ["direct", "command", "public"])
@pytest.mark.parametrize("change", ["disabled", "paused"])
def test_returned_facts_survive_control_disable_without_permitting_more_work(h, kind, change):
    async def flow(*_args, **kwargs):
        replace_control(change)
        assert not kwargs["operation_check"]()
        return h.result

    h.flow.side_effect = flow
    asyncio.run(invoke(kind))
    assert record()["runs"] == h.result["data"]["runs"]
    h.flow.assert_awaited_once()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "disabled", "paused", "record", "coordinator", "authorization"])
def test_command_rechecks_after_start_notification(h, change):
    async def audit(*_args, **_kwargs):
        replace_control(change)
        return True

    h.audit.side_effect = audit
    assert asyncio.run(invoke("command"))
    h.flow.assert_not_awaited()
    if change == "authorization":
        assert runtime._MANUAL_AUTH[IDENTITY]["op_id"] == "fixture-new-auth"


@pytest.mark.parametrize("kind", ["direct", "public"])
def test_explicit_invalid_operation_never_loads_entry_or_runs_worker(h, kind):
    result = asyncio.run(invoke(kind, operation_check=lambda: False))
    assert not result["ok"]
    h.loader.assert_not_awaited()
    h.flow.assert_not_awaited()


def test_direct_path_obeys_manual_global_pause(h):
    replace_control("paused")
    assert not asyncio.run(invoke("direct"))["ok"]
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("kind", ["direct", "command", "public"])
@pytest.mark.parametrize("flag", ["outcome_unknown", "open_run"])
def test_unresolved_mutation_never_becomes_retry_pending(h, kind, flag):
    h.result.update(ok=False, status="result_unknown" if flag == "outcome_unknown" else "cancelled",
                    retry_after_sec=91, **{flag: True})
    h.result["data"]["phase"] = "unknown" if flag == "outcome_unknown" else "blocked"
    asyncio.run(invoke(kind))
    assert runtime._COORDINATOR["phase"] != "retry_pending"
    assert record()[flag] is True
    assert record()["retry_at"] == 0
    assert record()["retry_after_sec"] == 91


@pytest.mark.parametrize("kind", ["direct", "command", "public"])
def test_cancelled_worker_retains_result_before_releasing_exclusion(h, kind):
    h.flow.side_effect = MiniAppFlowCancelled(h.result)
    with pytest.raises(MiniAppFlowCancelled) as error:
        asyncio.run(invoke(kind))
    assert record()["runs"] == h.result["data"]["runs"]
    assert error.value.result
    assert not runtime._global_run_lock().locked()


@pytest.mark.parametrize("boundary", ["session", "external"])
@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "disabled", "paused", "record"])
def test_public_chain_stops_at_invalidated_await_boundary(h, boundary, change):
    async def invalidate(*_args, **_kwargs):
        replace_control(change)
        return h.session if boundary == "session" else {"ok": True, "data": {"url": URL}}

    (h.loader if boundary == "session" else h.external).side_effect = invalidate
    assert not asyncio.run(invoke("public"))["ok"]
    h.flow.assert_not_awaited()
    if boundary == "session":
        h.external.assert_not_awaited()


def test_public_entry_read_shares_command_global_exclusion(h):
    async def scenario():
        async with runtime._global_run_lock():
            return await invoke("public")

    assert not asyncio.run(scenario())["ok"]
    h.loader.assert_not_awaited()


@pytest.mark.parametrize("flag", ["outcome_unknown", "open_run"])
@pytest.mark.parametrize("phase", ["blocked", "retry_pending"])
def test_scheduler_does_not_erase_unresolved_run_on_next_day(h, flag, phase):
    miniapp_state.record_miniapp_state(IDENTITY, "tree", {
        "kind": "daily", "day_key": "old-day", "phase": phase, flag: True, "retry_at": NOW - 1,
    }, now=NOW - 86400)
    queued = []
    h.monkeypatch.setattr(ui, "_fire_and_forget", lambda coro: (queued.append(coro), coro.close()))
    result = asyncio.run(ui._run_tree_miniapp_daily_scheduler(NOW, h.config))
    assert not result["started"]
    assert queued == []
    assert record()[flag]


def test_scheduler_reserves_before_public_worker_starts(h):
    queued = []
    h.monkeypatch.setattr(ui, "_fire_and_forget", queued.append)

    async def scenario():
        first = await ui._run_tree_miniapp_daily_scheduler(NOW, h.config)
        second = await ui._run_tree_miniapp_daily_scheduler(NOW, h.config)
        assert first["started"] and not second["started"]
        other = await invoke("public")
        assert not other["ok"]
        h.loader.assert_not_awaited()

    try:
        asyncio.run(scenario())
    finally:
        for coro in queued:
            coro.close()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "disabled", "paused", "config", "entry", "record"])
def test_queued_worker_cannot_use_stale_controls(h, change):
    queued = []
    h.monkeypatch.setattr(ui, "_fire_and_forget", queued.append)

    async def scenario():
        assert (await ui._run_tree_miniapp_daily_scheduler(NOW, h.config))["started"]
        if change in {"config", "entry"}:
            state_module.set_miniapp_auto_config({**h.config, **(
                {"tree_daily_enabled_identity_ids": []} if change == "config" else {"cave_public_entry_urls": []}
            )})
        else:
            replace_control(change)
        await queued.pop()
        h.loader.assert_not_awaited()
        h.flow.assert_not_awaited()

    try:
        asyncio.run(scenario())
    finally:
        for coro in queued:
            coro.close()


def test_prepared_single_game_summary_never_claims_settlement(h):
    summary = runtime._format_tree_summary({"ok": True, "status": "prepared", "open_run": True,
                                            "data": {"mode": "jump", "proof_summary": {"score": 75}}})
    assert "\u5df2\u7ed3\u7b97" not in summary


@pytest.fixture
def native(h):
    h.calls = []
    h.transport = scripted_transport(h.calls)

    def proof(mode, _run, **_kwargs):
        score = 75 if mode == "jump" else 17
        return ({"durationMs": 1, "clientScore": score, "charges": [0.5]},
                {"mode": mode, "score": score, "targetScore": score, "durationMs": 1})

    async def flow(*args, **kwargs):
        return await tree.run_tree_miniapp_daily_production_flow(
            *args, **kwargs, transport=h.transport, adapter=adapter(), sleeper=lambda _delay: None,
        )

    h.flow.side_effect = flow
    h.monkeypatch.setattr(tree, "build_tree_game_proof", proof)
    h.monkeypatch.setattr(tree, "request_tree_miniapp_init_data", AsyncMock(return_value="fixture-init"))
    h.monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", AsyncMock(return_value="fixture-init"))
    initial = deepcopy(h.session["result"])
    initial["data"]["overview"] = {"player_id": IDENTITY}
    h.start = AsyncMock(return_value=initial)
    h.monkeypatch.setattr(cave, "run_cave_dwelling_start_production_flow", h.start)
    h.monkeypatch.setattr(cave, "run_cave_dwelling_snapshot_production_flow", AsyncMock(return_value=initial))
    h.monkeypatch.setattr(cave, "_load_cave_public_identity_session", REAL_LOADER)
    h.monkeypatch.setattr(ui, "_cave_public_ui_run_lock", asyncio.Lock())
    h.monkeypatch.setattr(ui, "_cave_public_shared_hold", Mock(return_value={}))
    return h


async def schedule_native(h):
    tasks = []

    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    h.monkeypatch.setattr(ui, "_fire_and_forget", schedule)
    assert (await ui._run_tree_miniapp_daily_scheduler(NOW, state_module.get_miniapp_auto_config()))["started"]
    return tasks[0]


@pytest.mark.parametrize("kind", ["direct", "command", "public", "scheduled"])
@pytest.mark.parametrize("stage", ["run_start", "run_submit"])
def test_native_caller_holds_exclusion_until_cancelled_http_drains(native, kind, stage):
    h = native
    entered, release = threading.Event(), threading.Event()
    original = h.transport

    def transport(request):
        value = original(request)
        if request["safe_summary"]["endpoint"] == stage:
            entered.set()
            assert release.wait(3)
        return value

    h.transport = transport

    async def scenario():
        task = await schedule_native(h) if kind == "scheduled" else asyncio.create_task(invoke(kind))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            held = not task.done() and runtime._global_run_lock().locked()
            if kind in {"public", "scheduled"}:
                held = held and cave._public_entry_lock(IDENTITY).locked()
        finally:
            release.set()
            with pytest.raises(MiniAppFlowCancelled):
                await task
        assert held
        assert not runtime._global_run_lock().locked()

    asyncio.run(scenario())
    if stage == "run_submit":
        assert len(record()["runs"]) == 1
        assert record()["rewards"]["items"] == {"fixture_material": 1}
    else:
        assert record()["open_run"]
    expected = [("start", ""), ("run_start", "jump"), ("run_submit", "jump")]
    assert h.calls == expected[:2 if stage == "run_start" else 3]


def test_native_scheduler_finishes_both_modes_and_does_not_repeat_today(native):
    h = native

    async def scenario():
        task = await schedule_native(h)
        result = await task
        assert result["ok"], result
        assert not (await ui._run_tree_miniapp_daily_scheduler(NOW + 1, h.config))["started"]

    asyncio.run(scenario())
    assert record()["phase"] == "completed"
    assert [run["score"] for run in record()["runs"]] == [75, 17]
    assert record()["rewards"]["items"] == {"fixture_material": 2}
    assert len(h.calls) == 5


@pytest.mark.parametrize("kind", ["direct", "command", "public", "scheduled"])
@pytest.mark.parametrize("change", ["missing_score", "wrong_player"])
def test_native_callers_keep_unconfirmed_receipt_without_new_round(native, kind, change):
    h = native
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "run_submit":
            if change == "missing_score":
                response.pop("score")
            else:
                response["player_id"] = IDENTITY + 1
        return response

    h.transport = transport

    async def scenario():
        if kind == "scheduled":
            await (await schedule_native(h))
        else:
            await invoke(kind)
        assert not (await ui._run_tree_miniapp_daily_scheduler(NOW + 86400, h.config))["started"]

    asyncio.run(scenario())
    assert record()["phase"] == "unknown"
    assert not record()["completed_today"]
    assert record()["open_run"] and record()["outcome_unknown"]
    assert not record().get("runs")
    assert h.calls == [("start", ""), ("run_start", "jump"), ("run_submit", "jump")]
    if change == "missing_score":
        assert record()["rewards"]["items"] == {"fixture_material": 1}
        assert len(record()["partial_receipts"]) == 1
    else:
        assert not record().get("rewards", {}).get("items")


@pytest.fixture
def tree_db(native, monkeypatch, tmp_path):
    for name, value in {
        "DB_FILE": str(tmp_path / "tree.db"), "_db_conn": None, "_db_initialized": False,
        "_schema_columns_ensured_key": None, "_schema_columns_ensured_version": None,
        "_persistence_snapshot_db_key": "", "_persisted_meta_snapshot": {}, "_persisted_identity_snapshots": {},
        "_state_dirty": False, "_last_flush_time": 0, "_last_save_failed_at": 0, "_last_save_error": "",
    }.items():
        monkeypatch.setattr(persistence, name, value)
    monkeypatch.setattr(persistence, "_try_write_live_guard_backup", Mock(return_value=False))
    monkeypatch.setattr(miniapp_state, "save_state", persistence.save_state)
    monkeypatch.setattr(tree_operations, "save_state", persistence.save_state)
    try:
        yield native
    finally:
        if persistence._db_conn is not None:
            persistence._db_conn.close()


def test_saved_partial_receipt_survives_sqlite_reload_without_new_entry(tree_db):
    h = tree_db
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "run_submit":
            response.pop("score")
        return response

    h.transport = transport
    asyncio.run(invoke("public"))
    before = deepcopy(record())
    assert before["phase"] == "unknown" and before["outcome_unknown"]
    assert before["rewards"]["items"] == {"fixture_material": 1}
    state_module.set_miniapp_state_records({})
    assert persistence.load_state()
    assert record() == before
    runtime._COORDINATOR.clear()
    runtime._COORDINATOR.update(phase="idle")
    loader_calls = h.loader.await_count
    assert not asyncio.run(ui._run_tree_miniapp_daily_scheduler(NOW + 86400, h.config))["started"]
    assert not asyncio.run(invoke("public"))["ok"]
    assert h.loader.await_count == loader_calls
    assert record() == before
    assert len(h.calls) == 3


def test_native_scheduler_keeps_unknown_game_result_without_fallback_or_retry(native):
    h = native
    urls = [PUBLIC, "https://t.me/hantianzun99_bot?startapp=df_FIXTURE131_OTHER"]
    state_module.set_miniapp_auto_config({**h.config, "cave_public_entry_urls": urls})
    original = h.transport

    def transport(request):
        value = original(request)
        if request["safe_summary"]["endpoint"] == "run_submit":
            return SimpleNamespace(status_code=503, headers={"Retry-After": "91"},
                                   json=lambda: {"ok": False, "error": "dwelling_token_expired"})
        return value

    h.transport = transport

    async def scenario():
        task = await schedule_native(h)
        result = await task
        assert not result["ok"]
        assert result["extra"]["result"]["outcome_unknown"]

    asyncio.run(scenario())
    h.start.assert_awaited_once()
    assert len(h.calls) == 3
    assert record()["outcome_unknown"] and record()["retry_at"] == 0
    assert not ui._cave_public_background_state["circuit_open_until"]


def test_native_scheduler_can_use_second_entry_only_after_first_read_failed(native):
    h = native
    urls = [PUBLIC, "https://t.me/hantianzun99_bot?startapp=df_FIXTURE131_OTHER"]
    state_module.set_miniapp_auto_config({**h.config, "cave_public_entry_urls": urls})
    h.start.side_effect = [
        {"ok": False, "status": "failed", "error": "dwelling_token_expired"}, h.start.return_value,
    ]

    async def scenario():
        task = await schedule_native(h)
        result = await task
        assert result["ok"], result

    asyncio.run(scenario())
    assert h.start.await_count == 2
    assert len(h.calls) == 5


def test_queued_task_cancelled_before_start_releases_reservation_without_http(h):
    async def scenario():
        task = await schedule_native(h)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert runtime._COORDINATOR["phase"] not in {"queued", "running"}
    h.loader.assert_not_awaited()
    assert record()["phase"] == "blocked" and not record()["outcome_unknown"]


@pytest.mark.parametrize("flag", ["outcome_unknown", "open_run"])
def test_direct_reentry_cannot_bypass_unresolved_record(h, flag):
    miniapp_state.record_miniapp_state(IDENTITY, "tree", {flag: True, "kind": "manual"}, now=NOW - 86400)
    assert not asyncio.run(invoke("direct"))["ok"]
    h.flow.assert_not_awaited()
    assert record()[flag]


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
def test_manual_authorization_is_bound_to_original_owner(h, change):
    runtime.authorize_tree_miniapp_manual_run(IDENTITY, now=NOW)
    replace_control(change)
    assert runtime._manual_auth(IDENTITY, NOW + 1) == {}


@pytest.mark.parametrize("action", ["finalize", "cancel"])
def test_late_command_callback_cannot_replace_new_coordinator(h, action):
    auth = runtime.prepare_tree_miniapp_daily_run(IDENTITY, enabled=True, now=NOW)
    replace_control("coordinator")
    before = deepcopy(runtime._COORDINATOR)
    if action == "finalize":
        assert not runtime.finalize_tree_miniapp_daily_command(auth["op_id"], 131, now=NOW)
    else:
        assert runtime.cancel_tree_miniapp_daily_run(auth["op_id"], now=NOW)
    assert runtime._COORDINATOR == before


def test_new_authorization_with_same_op_id_survives_lock_acquisition(h):
    class Lock:
        def locked(self):
            return False

        async def __aenter__(self):
            previous = runtime._MANUAL_AUTH[IDENTITY]
            runtime.authorize_tree_miniapp_manual_run(IDENTITY, now=NOW, op_id=previous["op_id"])
            self.replacement = runtime._MANUAL_AUTH[IDENTITY]

        async def __aexit__(self, *_args):
            return False

    lock = Lock()
    h.monkeypatch.setattr(runtime, "_GLOBAL_RUN_LOCK", lock)
    assert asyncio.run(invoke("command"))
    assert runtime._MANUAL_AUTH[IDENTITY] is lock.replacement
    h.flow.assert_not_awaited()


def test_same_scalar_coordinator_replacement_still_invalidates_older_operation(h):
    async def flow(*_args, **kwargs):
        snapshot = runtime.get_tree_miniapp_coordinator_snapshot()
        runtime._set_coordinator("running", auth=snapshot, now=NOW)
        assert not kwargs["operation_check"]()
        return h.result

    h.flow.side_effect = flow
    asyncio.run(invoke("direct"))
    assert record() == {}
    h.capture.assert_not_called()


def test_coordinator_snapshot_does_not_alias_retained_result(h):
    asyncio.run(invoke("direct"))
    snapshot = runtime.get_tree_miniapp_coordinator_snapshot()
    snapshot["result"]["data"]["runs"].clear()
    assert runtime._COORDINATOR["result"]["data"]["runs"]


def test_save_failure_after_memory_commit_cannot_repeat_daily_work(native):
    h = native
    def save():
        if state_module.get_identity_state(IDENTITY)["tree_operation"].get("published"):
            raise OSError("fixture publication failed")
        return True

    tree_operations.save_state.side_effect = save
    result = asyncio.run(invoke("direct"))
    assert not result["ok"] and result["status"] == "persistence_pending"
    journal = state_module.get_identity_state(IDENTITY)["tree_operation"]
    assert len(journal["checkpoint"]["result"]["data"]["runs"]) == 2
    assert not journal["published"]
    assert not asyncio.run(ui._run_tree_miniapp_daily_scheduler(NOW + 1, h.config))["started"]
    assert len(h.calls) == 5


@pytest.mark.parametrize("kind", ["direct", "command", "public"])
def test_summary_formatting_failure_preserves_returned_result(h, kind):
    h.monkeypatch.setattr(runtime, "_format_tree_summary", Mock(side_effect=ValueError("fixture summary failed")))
    result = asyncio.run(invoke(kind))
    assert result
    if kind != "command":
        assert result["ok"]
    assert record()["runs"] == h.result["data"]["runs"]


@pytest.mark.parametrize("kind", ["direct", "command", "public"])
def test_notification_cancellation_cannot_return_result_for_replaced_owner(h, kind):
    async def audit(message, **_kwargs):
        if "\u7ed3\u679c" in message:
            replace_control("replaced")
            raise asyncio.CancelledError
        return True

    h.audit.side_effect = audit
    with pytest.raises(MiniAppFlowCancelled) as error:
        asyncio.run(invoke(kind))
    assert error.value.result is None


def test_standalone_public_entry_read_failure_keeps_server_retry_after(h):
    h.loader.return_value = {"ok": False, "error": "fixture read limited", "result": {
        "ok": False, "status": "rate_limited", "retry_after_sec": 91,
    }}
    assert not asyncio.run(invoke("public"))["ok"]
    assert record()["retry_after_sec"] == 91
    assert record()["phase"] == "retry_pending"
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("kind", ["direct", "command", "public", "scheduled"])
@pytest.mark.parametrize("availability", ["maintenance", "channel_closed"])
def test_miniapp_paths_keep_maintenance_and_channel_availability_independent(native, kind, availability):
    h = native
    if availability == "maintenance":
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("tianzun_maintenance")
    else:
        state_module.set_identity_enabled(IDENTITY, False)
        state_module.set_channel_send_as_health({
            "status": "closed", "restore_identity_ids": [IDENTITY], "frozen_identity_ids": [IDENTITY],
        })

    async def scenario():
        if kind == "scheduled":
            task = await schedule_native(h)
            result = await task
        else:
            result = await invoke(kind)
        assert result
        if kind != "command":
            assert result["ok"], result

    asyncio.run(scenario())
    assert record()["phase"] == "completed"
    assert len(h.calls) == 5


@pytest.mark.parametrize("kind", ["direct", "public"])
@pytest.mark.parametrize("op_id", ["old-operation", "fixture131-op"])
def test_interrupted_running_record_cannot_be_reopened_by_direct_entry(h, kind, op_id):
    miniapp_state.record_miniapp_state(IDENTITY, "tree", {
        "kind": "daily", "phase": "running", "day_key": "old-day", "op_id": op_id,
    }, now=NOW - 86400)
    before = deepcopy(record())
    assert not asyncio.run(invoke(kind))["ok"]
    assert record() == before
    h.loader.assert_not_awaited()
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("kind", ["direct", "command", "public", "scheduled"])
@pytest.mark.parametrize("stage", ["initial", "after_submit"])
def test_native_callers_do_not_turn_unknown_quota_into_completion(native, kind, stage):
    h = native
    original = h.transport

    def transport(request):
        response = original(request)
        if "council" in response and (stage == "initial" or ("run_submit", "jump") in h.calls):
            response["council"]["daily"]["jump" if stage == "initial" else "fly"]["used"] = None
        return response

    h.transport = transport

    async def scenario():
        if kind == "scheduled":
            await (await schedule_native(h))
        else:
            await invoke(kind)

    asyncio.run(scenario())
    assert not record().get("completed_today")
    assert record()["status"] == "quota_unknown"
    assert record()["phase"] == "blocked"
    assert not any(mode == "fly" for _, mode in h.calls)
    if stage == "initial":
        assert h.calls == [("start", "")]
        assert not record().get("runs")
    else:
        assert h.calls == [("start", ""), ("run_start", "jump"), ("run_submit", "jump"), ("start", "")]
        assert len(record()["runs"]) == 1
        assert record()["rewards"]["items"] == {"fixture_material": 1}


@pytest.mark.parametrize("kind", ["direct", "command", "public", "scheduled"])
def test_native_callers_reconcile_missing_sibling_quota(native, kind):
    h = native
    original = h.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == "run_submit":
            response["council"]["daily"].pop("fly")
        return response

    h.transport = transport

    async def scenario():
        if kind == "scheduled":
            await (await schedule_native(h))
        else:
            await invoke(kind)

    asyncio.run(scenario())
    assert record()["completed_today"]
    assert record()["phase"] == "completed"
    assert h.calls.count(("start", "")) == 3
    assert [mode for endpoint, mode in h.calls if endpoint == "run_start"] == ["jump", "fly"]
    assert record()["rewards"]["items"] == {"fixture_material": 2}
