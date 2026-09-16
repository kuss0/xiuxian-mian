import asyncio
import copy
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_miniapp as api
from model.features import cave_treasure_runtime as cave
from model.features import deep_retreat
from model.features.miniapp_common import MiniAppFlowCancelled
from model.webapp_core import MiniAppHttpResult


NOW = 1_700_000_500.0
ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE999"
PLAYER_ID = -1_000_000_001_001
START_TEXT = "你已进入深度闭关状态，神魂将自行吐纳 **8** 小时。"
SUMMARY_TEXT = "【深度闭关总结】\n本次结算时长: 8.0 小时\n神魂吐纳次数: 32 周天"
LOAD_SESSION = cave._load_cave_public_identity_session


def payload(*, text=START_TEXT, deep=None):
    result = {
        "ok": True,
        "account": {"playerId": PLAYER_ID},
        "actionResult": {"ok": True, "rawMessage": text},
    }
    if deep is not None:
        result["dwelling"] = {"meditation": {"deepSeclusion": deep}}
    return result


@pytest.fixture
def env(monkeypatch):
    metadata = copy.deepcopy(state_module._meta_state)
    locks = dict(cave._PUBLIC_ENTRY_LOCKS)
    state_module._meta_state.update({
        "identity_ids": [], "identity_states": {}, "send_as_profiles": {},
        "identity_account_map": {}, "miniapp_state_records": {}, "channel_send_as_health": {},
    })
    cave._PUBLIC_ENTRY_LOCKS.clear()
    state_module.ensure_identity_registered(1001)
    state_module.set_identity_account(1001, 11)
    state_module.set_global_enabled(True)
    identity = state_module.get_identity_state(1001)
    identity.update(deep_retreat_enabled=True, deep_retreat_phase="idle", next_deep_retreat_time=NOW - 1)
    session = AsyncMock(return_value={
        "ok": True, "init_data": "fixture_init", "player_id": PLAYER_ID,
        "result": {"ok": True, "data": {"raw": payload(deep={
            "active": False, "completed": False, "canStart": True,
            "canSettle": False, "canForceExit": False,
        })}},
    })
    flow = AsyncMock(return_value={"ok": True, "status": "start", "action_dispatched": True, "data": payload()})
    audit = AsyncMock()
    save = Mock(return_value=True)
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", session)
    monkeypatch.setattr(cave, "run_cave_deep_seclusion_action_production_flow", flow)
    monkeypatch.setattr(cave, "_capture_store", lambda _now: None)
    monkeypatch.setattr(cave, "send_audit_log", audit)
    monkeypatch.setattr(cave, "save_state", save)
    monkeypatch.setattr(deep_retreat, "send_audit_log", AsyncMock())
    monkeypatch.setattr(deep_retreat, "save_state", save)
    monkeypatch.setattr(deep_retreat, "console_log", Mock())
    monkeypatch.setattr("model.features._phaseful.save_state", save)
    try:
        yield SimpleNamespace(identity=identity, session=session, flow=flow, audit=audit, save=save)
    finally:
        cave._PUBLIC_ENTRY_LOCKS.clear()
        cave._PUBLIC_ENTRY_LOCKS.update(locks)
        state_module._meta_state.clear()
        state_module._meta_state.update(metadata)


def run(action="start"):
    return asyncio.run(cave.run_cave_public_deep_retreat_action(1001, ENTRY, action, now=NOW))


@pytest.mark.parametrize("change", ["delete", "replace", "rebind", "disable", "module_off", "pause", "new_phase", "new_record"])
def test_entry_await_cannot_send_after_owner_control_or_schedule_changed(env, change):
    after = {}

    async def loaded(*_args, **_kwargs):
        if change == "delete":
            state_module.remove_identity(1001)
        elif change == "replace":
            state_module._meta_state["identity_states"][1001] = copy.deepcopy(env.identity)
        elif change == "rebind":
            state_module.set_identity_account(1001, 22)
        elif change == "disable":
            state_module.set_identity_enabled(1001, False)
        elif change == "module_off":
            env.identity["deep_retreat_enabled"] = False
        elif change == "pause":
            state_module.set_global_enabled(False)
        elif change == "new_phase":
            env.identity.update(deep_retreat_phase="running", next_deep_retreat_time=NOW + 12345)
        else:
            state_module.set_miniapp_state_records({"1001:cave_deep_retreat": {"updated_at": NOW + 1, "state": {"new": True}}})
        after.update(copy.deepcopy(state_module._meta_state))
        return env.session.return_value

    env.session.side_effect = loaded
    result = run()
    assert not result["ok"]
    env.flow.assert_not_awaited()
    env.audit.assert_not_awaited()
    env.save.assert_not_called()
    assert state_module._meta_state == after


@pytest.mark.parametrize("change", ["delete", "replace", "rebind", "new_phase", "new_record"])
def test_action_reply_does_not_write_through_changed_owner_or_newer_result(env, change):
    after = {}

    async def completed(*_args, **_kwargs):
        if change == "delete":
            state_module.remove_identity(1001)
        elif change == "replace":
            state_module._meta_state["identity_states"][1001] = copy.deepcopy(env.identity)
        elif change == "rebind":
            state_module.set_identity_account(1001, 22)
        elif change == "new_phase":
            env.identity.update(deep_retreat_phase="running", next_deep_retreat_time=NOW + 12345)
        else:
            state_module.set_miniapp_state_records({"1001:cave_deep_retreat": {"updated_at": NOW + 1, "state": {"new": True}}})
        after.update(copy.deepcopy(state_module._meta_state))
        return env.flow.return_value

    env.flow.side_effect = completed
    result = run()
    assert not result["ok"]
    assert state_module._meta_state == after
    env.save.assert_not_called()
    env.audit.assert_not_awaited()


def test_confirmed_action_result_is_retained_after_module_is_disabled(env):
    async def completed(*_args, **_kwargs):
        env.identity["deep_retreat_enabled"] = False
        return env.flow.return_value

    env.flow.side_effect = completed
    result = run()
    assert result["ok"]
    assert not env.identity["deep_retreat_enabled"]
    assert env.identity["deep_retreat_phase"] == "running"
    assert env.identity["next_deep_retreat_time"] == pytest.approx(NOW + 8 * 3600 + deep_retreat.CD_BUFFER_SEC, abs=1)
    env.flow.assert_awaited_once()


def test_late_or_failed_log_does_not_replace_confirmed_business_result(env):
    env.audit.side_effect = RuntimeError("logging unavailable")
    result = run()
    assert result["ok"]
    assert env.identity["deep_retreat_phase"] == "running"
    assert state_module.get_miniapp_state_records()["1001:cave_deep_retreat"]["state"]["ok"]


@pytest.mark.parametrize("action,text", [
    ("start", "你已进入深度闭关状态，神魂将自行吐纳。"),
    ("status", "你并未处于深度闭关之中。"),
    ("settle", SUMMARY_TEXT),
])
def test_miniapp_reducer_does_not_call_group_probe_delete_or_summary_replay(env, monkeypatch, action, text):
    env.identity.update(deep_retreat_phase="running", next_deep_retreat_time=NOW - 1, last_deep_retreat_summary_msg_id=99)
    probe = AsyncMock()
    delete = AsyncMock()
    replay = Mock()
    close = Mock()
    monkeypatch.setattr(deep_retreat, "schedule_deep_retreat_status_probe", probe)
    monkeypatch.setattr(deep_retreat, "delete_deep_retreat_summary_trigger_msg", delete)
    monkeypatch.setattr("model.features._phaseful.delete_summary_trigger_msg", delete)
    monkeypatch.setattr("model.features._phaseful._schedule_summary_consumed_command_replay", replay)
    monkeypatch.setattr(deep_retreat, "_clear_deep_retreat_remote_block_after_summary", close)
    asyncio.run(cave.sync_cave_deep_seclusion_action_result(1001, action, payload(text=text), now=NOW))
    probe.assert_not_awaited()
    delete.assert_not_awaited()
    replay.assert_not_called()
    close.assert_not_called()


@pytest.mark.parametrize("kind", ["deep", "meditation"])
def test_action_flow_rechecks_operation_after_auth(env, monkeypatch, kind):
    allowed = True

    async def auth(*_args, **_kwargs):
        nonlocal allowed
        allowed = False
        return "fixture_init"

    transport = Mock(return_value=(200, payload()))
    monkeypatch.setattr(api, "request_cave_treasure_miniapp_init_data", AsyncMock(side_effect=auth))
    flow = api.run_cave_deep_seclusion_action_production_flow if kind == "deep" else api.run_cave_meditation_settle_production_flow
    result = asyncio.run(flow(
        1001, token="df_FIXTURE999", webview_url=ENTRY, player_id=PLAYER_ID,
        operation_check=lambda: allowed, transport=transport,
        **({"action": "start"} if kind == "deep" else {}),
    ))
    assert not result["ok"]
    assert not result.get("action_dispatched")
    transport.assert_not_called()


def test_cancellation_joins_http_and_keeps_confirmed_action_before_releasing_lock(env, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def execute(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return MiniAppHttpResult(ok=True, data=payload(), attempts=1, status_code=200)

    monkeypatch.setattr(api, "execute_miniapp_http_request", execute)
    monkeypatch.setattr(cave, "run_cave_deep_seclusion_action_production_flow", api.run_cave_deep_seclusion_action_production_flow)

    async def scenario():
        task = asyncio.create_task(cave.run_cave_public_deep_retreat_action(1001, ENTRY, "start", now=NOW))
        try:
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(0.01)
            assert cave._public_entry_lock(1001).locked()
            assert not task.done()
            task.cancel()
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as error:
                await task
            assert error.value.result["ok"]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert not cave._public_entry_lock(1001).locked()

    asyncio.run(scenario())
    assert env.identity["deep_retreat_phase"] == "running"
    assert state_module.get_miniapp_state_records()["1001:cave_deep_retreat"]["state"]["ok"]


@pytest.mark.parametrize("action,permission", [("start", "canStart"), ("force", "canForceExit"), ("settle", "canSettle")])
@pytest.mark.parametrize("permission_value", [None, False, "true", 1])
def test_all_mutations_require_explicit_dashboard_permission(env, action, permission, permission_value):
    panel = {"active": False, "completed": False, "canStart": False, "canSettle": False, "canForceExit": False}
    if permission_value is None:
        panel.pop(permission)
    else:
        panel[permission] = permission_value
    env.session.return_value["result"]["data"]["raw"] = payload(text="", deep=panel)
    result = run(action)
    env.flow.assert_not_awaited()
    assert result["extra"]["action_skipped"]


@pytest.mark.parametrize("action", ["start", "force", "settle"])
def test_matching_selector_does_not_authorize_a_wrong_dashboard(env, action):
    env.session.return_value["result"]["data"]["raw"] = {
        **payload(text="", deep={"canStart": True, "canForceExit": True, "canSettle": True}),
        "account": {"playerId": 11},
    }
    before = copy.deepcopy(env.identity)
    result = run(action)
    assert not result["ok"]
    assert env.identity == before
    env.flow.assert_not_awaited()


def test_unrelated_active_or_text_does_not_become_deep_retreat_evidence(env):
    raw = {"account": {"playerId": PLAYER_ID}, "smallWorld": {
        "active": True, "remainingSeconds": 36000, "statusText": "活动中",
    }, "history": {"message": START_TEXT}}
    before = copy.deepcopy(env.identity)
    result = asyncio.run(cave.sync_cave_deep_seclusion_action_result(1001, "status", raw, now=NOW))
    assert not result["handled"]
    assert env.identity == before


@pytest.mark.parametrize("field", ["remainingSeconds", "endMs"])
@pytest.mark.parametrize("value", [True, -1, 1.5, "invalid", "-1", "nan", pytest.param("9" * 5000, id="oversized")])
def test_bad_deep_counters_are_unknown_instead_of_zero(field, value):
    snapshot = cave.extract_cave_deep_seclusion_state(payload(text="", deep={field: value}))
    assert snapshot["remaining_seconds" if field == "remainingSeconds" else "end_ms"] is None


def test_no_deep_evidence_can_consume_or_rewrite_tianxing(env):
    env.identity.update(tianxing_enabled=True, tianxing_observation={"prediction": "exploration"}, tianxing_timeline_state={"next": "duel"})
    before = {key: copy.deepcopy(value) for key, value in env.identity.items() if key.startswith("tianxing_")}
    assert run()["ok"]
    assert {key: value for key, value in env.identity.items() if key.startswith("tianxing_")} == before


def test_unknown_mutation_is_retained_without_group_fallback_or_replay(env):
    env.flow.return_value = {"ok": False, "status": "failed", "error": "timeout", "action_dispatched": True, "outcome_unknown": True}
    result = run()
    assert not result["ok"]
    assert result["extra"]["outcome_unknown"]
    assert env.identity["deep_retreat_phase"] == "launching"
    assert env.identity["next_deep_retreat_time"] >= NOW + cave.CAVE_DEEP_STATUS_RECHECK_SEC
    with state_module.use_identity(1001):
        assert not deep_retreat._cave_public_deep_legacy_fallback_ready(NOW + 1)
    env.session.return_value["result"]["data"]["raw"] = payload(text="", deep={})
    env.flow.reset_mock()
    result = run()
    env.flow.assert_not_awaited()
    assert result["extra"]["outcome_unknown"]
    assert state_module.get_miniapp_state_records()["1001:cave_deep_retreat"]["state"]["outcome_unknown"]


def test_failed_status_does_not_erase_an_earlier_unknown_mutation(env):
    state_module.set_miniapp_state_records({"1001:cave_deep_retreat": {"updated_at": NOW - 100, "state": {
        "ok": False, "action": "start", "action_dispatched": True,
        "outcome_unknown": True, "unknown_action": "start", "unknown_since": NOW - 100,
    }}})
    env.session.return_value = {"ok": False, "error": "offline"}
    result = run("status")
    assert not result["ok"]
    stored = state_module.get_miniapp_state_records()["1001:cave_deep_retreat"]["state"]
    assert stored["outcome_unknown"]
    assert stored["unknown_action"] == "start"
    assert stored["unknown_since"] == NOW - 100
    with state_module.use_identity(1001):
        assert not deep_retreat._cave_public_deep_legacy_fallback_ready(NOW + 1)


def test_identity_bound_status_resolves_prior_unknown_without_another_start(env):
    state_module.set_miniapp_state_records({"1001:cave_deep_retreat": {"updated_at": NOW - 100, "state": {
        "ok": False, "action": "start", "action_dispatched": True, "outcome_unknown": True,
    }}})
    env.identity["deep_retreat_phase"] = "launching"
    env.flow.return_value = {"ok": True, "status": "status", "action_dispatched": True, "data": payload(
        text="", deep={"active": True, "completed": False, "canSettle": False, "remainingSeconds": 1200},
    )}
    result = run("status")
    assert result["ok"]
    assert env.identity["deep_retreat_phase"] == "running"
    assert not state_module.get_miniapp_state_records()["1001:cave_deep_retreat"]["state"]["outcome_unknown"]
    assert env.flow.await_args.kwargs["action"] == "status"


@pytest.mark.parametrize("action, panel", [
    ("start", {"active": False, "completed": False, "canStart": True}),
    ("settle", {"active": True, "completed": True, "canStart": False, "canSettle": True}),
    ("force", {"active": True, "completed": False, "canStart": False, "canForceExit": True, "remainingSeconds": 1200}),
])
def test_unchanged_panel_cannot_release_or_repeat_an_unknown_deep_action(env, action, panel):
    env.session.return_value["result"]["data"]["raw"] = payload(text="", deep=panel)
    env.flow.return_value = {"ok": False, "action_dispatched": True, "outcome_unknown": True, "error": "timeout"}
    assert not run(action)["ok"]
    for _ in range(2):
        assert run(action)["extra"]["outcome_unknown"]
    env.flow.assert_awaited_once()
    stored = state_module.get_miniapp_state_records()["1001:cave_deep_retreat"]["state"]
    assert stored["unknown_action"] == action
    assert env.identity["deep_retreat_phase"] == "launching"


@pytest.mark.parametrize("action, panel", [
    ("start", {"active": True, "completed": False, "canStart": False, "remainingSeconds": 1200}),
    ("start", {"active": True, "completed": True, "canStart": False, "canSettle": True}),
    ("settle", {"active": False, "completed": False, "canStart": True, "canSettle": False}),
    ("force", {"active": False, "completed": False, "canStart": True, "canSettle": False}),
])
def test_native_deep_postcondition_resolves_only_the_matching_unknown_action(env, action, panel):
    state_module.set_miniapp_state_records({"1001:cave_deep_retreat": {"updated_at": NOW - 100, "state": {
        "ok": False, "action": action, "outcome_unknown": True, "unknown_action": action,
        "unknown_since": NOW - 100,
    }}})
    env.flow.return_value = {"ok": True, "action_dispatched": True, "data": payload(text="", deep=panel)}
    result = run("status")
    assert result["ok"]
    assert not result["extra"]["outcome_unknown"]
    env.flow.assert_awaited_once()
    assert env.flow.await_args.kwargs["action"] == "status"


@pytest.mark.parametrize("kind", ["legacy_action", "rejected_observation", "ambiguous_idle"])
def test_unverified_postcondition_does_not_close_a_deep_unknown(env, kind):
    action = "legacy" if kind == "legacy_action" else "force"
    state_module.set_miniapp_state_records({"1001:cave_deep_retreat": {"updated_at": NOW - 100, "state": {
        "ok": False, "outcome_unknown": True, "unknown_action": action,
    }}})
    observed = payload(text="", deep={"active": False, "completed": False, "canStart": True})
    if kind == "rejected_observation":
        observed["actionResult"]["ok"] = False
    elif kind == "ambiguous_idle":
        del observed["dwelling"]["meditation"]["deepSeclusion"]["active"]
    env.flow.return_value = {"ok": True, "action_dispatched": True, "data": observed}
    result = run("status")
    assert not result["ok"]
    assert result["extra"]["outcome_unknown"]
    assert env.identity["deep_retreat_phase"] == "launching"


@pytest.mark.parametrize("panel", [
    {"active": True, "canStart": True},
    {"active": False, "remainingSeconds": 900},
    {"completed": True, "remainingSeconds": 900},
    {"canSettle": True, "remainingSeconds": 900},
])
def test_conflicting_deep_snapshot_cannot_change_business_state(env, panel):
    before = copy.deepcopy(env.identity)
    result = asyncio.run(cave.sync_cave_deep_seclusion_action_result(1001, "status", payload(text="", deep=panel), now=NOW))
    assert not result["handled"]
    assert env.identity == before


def test_verified_conflict_records_only_safe_diagnostic_fields(env):
    raw = payload(text="fixture-private-message", deep={"active": True, "canStart": True})
    raw["initData"] = "fixture-private-auth"
    raw["inventory"] = {"private": "fixture-private-bag"}
    env.flow.return_value = {"ok": True, "status": "status", "action_dispatched": True, "data": raw}
    assert not run("status")["ok"]
    record = state_module.get_miniapp_state_records()["1001:cave_deep_retreat"]["state"]
    assert record["identity_verified"]
    assert record["snapshot"]["conflicting"]
    assert record["snapshot"]["active"] is True
    assert "fixture-private" not in repr(record)


def test_rejected_start_text_does_not_count_as_a_success(env):
    raw = payload()
    raw["actionResult"]["ok"] = False
    before = copy.deepcopy(env.identity)
    result = asyncio.run(cave.sync_cave_deep_seclusion_action_result(1001, "start", raw, now=NOW))
    assert not result["handled"]
    assert env.identity == before


@pytest.mark.parametrize("action", ["settle", "force"])
def test_successful_exit_can_reconcile_a_verified_idle_snapshot(env, action):
    raw = payload(text="", deep={"active": False, "canStart": True, "canSettle": False})
    result = asyncio.run(cave.sync_cave_deep_seclusion_action_result(1001, action, raw, now=NOW))
    assert result["handled"]
    assert env.identity["deep_retreat_phase"] == "post_summary_wait"


@pytest.mark.parametrize("summary_pressure", [False, True])
def test_unknown_receipt_survives_persistence_codec_and_cannot_be_replayed(env, summary_pressure):
    from model import miniapp_state, persistence

    env.flow.return_value = {"ok": False, "status": "failed", "error": "timeout", "action_dispatched": True, "outcome_unknown": True}
    assert not run()["ok"]
    if summary_pressure:
        for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 1):
            miniapp_state.record_miniapp_state(1001, f"diagnostic_{number}", {"marker": number}, now=NOW + number + 1, persist=False)
    _get, encode, restore = persistence._META_STATE_CODEC["miniapp_state_records"]
    saved = encode(state_module.get_miniapp_state_records())
    state_module.set_miniapp_state_records({})
    cave._PUBLIC_ENTRY_LOCKS.clear()
    env.identity["deep_retreat_phase"] = "idle"
    restore(saved)
    env.session.return_value["result"]["data"]["raw"] = payload(text="", deep={})
    env.flow.reset_mock()
    result = run()
    assert result["extra"]["outcome_unknown"]
    env.flow.assert_not_awaited()


def test_other_summaries_cannot_discard_a_current_inflight_retreat_result(env):
    from model import miniapp_state

    miniapp_state.record_miniapp_state(1001, "cave_deep_retreat", {"ok": False, "action_dispatched": False}, now=NOW - 10, persist=False)

    async def completed(*_args, **_kwargs):
        for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 1):
            miniapp_state.record_miniapp_state(1001, f"diagnostic_{number}", {"marker": number}, now=NOW + number + 1, persist=False)
        return env.flow.return_value

    env.flow.side_effect = completed
    assert run()["ok"]
    env.flow.assert_awaited_once()
    assert env.identity["deep_retreat_phase"] == "running"


def test_real_entry_selection_and_action_reconcile_only_the_channel(env, monkeypatch):
    state_module.ensure_identity_registered(11)
    primary = state_module.get_identity_state(11)
    primary.update(deep_retreat_phase="running", next_deep_retreat_time=NOW + 800)
    before = copy.deepcopy(primary)
    state_module.set_identity_enabled(1001, False)
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [1001]})
    calls = []
    end_ms = int((NOW + 28800) * 1000)

    def transport(request):
        body = request["payload"]
        selected = body.get("playerId")
        step = request["url"].rsplit("/", 1)[-1]
        calls.append((step, selected, body.get("action")))
        if step == "deep-seclusion":
            assert selected == PLAYER_ID
            return 200, payload(deep={"active": True, "canStart": False, "canSettle": False, "endMs": end_ms})
        if selected is None:
            return 200, {
                "ok": True, "account": {"playerId": 11},
                "identity": {"selectedPlayerId": 11, "choices": [{"playerId": 11}, {"playerId": PLAYER_ID}]},
            }
        assert selected == PLAYER_ID
        return 200, {
            "ok": True, "account": {"playerId": PLAYER_ID},
            "identity": {"selectedPlayerId": PLAYER_ID},
            "dwelling": {"meditation": {"deepSeclusion": {
                "active": False, "completed": False, "canStart": True, "canSettle": False,
            }}},
        }

    monkeypatch.setattr(cave, "_load_cave_public_identity_session", LOAD_SESSION)
    monkeypatch.setattr(cave, "request_cave_treasure_miniapp_init_data", AsyncMock(return_value="fixture_init"))
    monkeypatch.setattr(cave, "run_cave_deep_seclusion_action_production_flow", api.run_cave_deep_seclusion_action_production_flow)
    monkeypatch.setattr(api, "_flow_transport", lambda *_args, **_kwargs: transport)
    result = run()
    assert result["ok"], result
    assert calls == [("start", None, None), ("start", PLAYER_ID, None), ("deep-seclusion", PLAYER_ID, "start")]
    assert state_module.get_identity_state(11) == before
    assert env.identity["deep_retreat_phase"] == "running"
    assert env.identity["next_deep_retreat_time"] == end_ms / 1000 + deep_retreat.CD_BUFFER_SEC


@pytest.mark.parametrize("attempt", [
    {"ok": False},
    {"ok": False, "action_dispatched": True},
    {"ok": False, "action_dispatched": False, "outcome_unknown": True},
    {"ok": True, "action_dispatched": False},
])
def test_uncertain_or_successful_http_work_cannot_authorize_legacy_fallback(env, attempt):
    state_module.set_miniapp_state_records({"1001:cave_deep_retreat": {"updated_at": NOW, "state": attempt}})
    with state_module.use_identity(1001):
        assert not deep_retreat._cave_public_deep_legacy_fallback_ready(NOW + 1)


def test_confirmed_pre_dispatch_failure_keeps_the_configured_legacy_fallback(env):
    state_module.set_miniapp_state_records({"1001:cave_deep_retreat": {"updated_at": NOW, "state": {
        "ok": False, "action_dispatched": False, "outcome_unknown": False,
    }}})
    with state_module.use_identity(1001):
        assert deep_retreat._cave_public_deep_legacy_fallback_ready(NOW + 1)


@pytest.mark.parametrize("body", ["<html>interrupted</html>", {}, {"message": "unknown response shape"}])
@pytest.mark.parametrize("kind", ["deep", "meditation"])
def test_http_200_without_an_action_contract_is_unknown_not_safe_to_replay(env, body, kind):
    flow = api.run_cave_deep_seclusion_action_production_flow if kind == "deep" else api.run_cave_meditation_settle_production_flow
    transport = Mock(return_value=(200, body))
    result = asyncio.run(flow(
        1001, token="df_FIXTURE999", webview_url=ENTRY, player_id=PLAYER_ID,
        init_data="fixture_init", transport=transport,
        **({"action": "start"} if kind == "deep" else {}),
    ))
    assert not result["ok"]
    assert result["action_dispatched"]
    assert result["outcome_unknown"]
    transport.assert_called_once()


@pytest.mark.parametrize("panel", [
    {"active": False, "canStart": False, "statusText": "你并未处于深度闭关之中。"},
    {"active": True, "completed": True, "canSettle": False},
])
def test_explicit_dashboard_refusal_cannot_schedule_a_rapid_mutation_loop(env, panel):
    before = copy.deepcopy(env.identity)
    result = asyncio.run(cave.sync_cave_deep_seclusion_action_result(1001, "status", payload(text="", deep=panel), now=NOW))
    assert not result["handled"]
    assert env.identity == before


def test_missing_start_permission_rechecks_status_instead_of_thirty_second_start_loop(env):
    env.session.return_value["result"]["data"]["raw"] = payload(text="", deep={
        "active": False, "statusText": "你并未处于深度闭关之中。",
    })
    result = run()
    assert result["extra"]["action_skipped"]
    assert env.identity["deep_retreat_phase"] == "launching"
    assert env.identity["next_deep_retreat_time"] == NOW + cave.CAVE_DEEP_STATUS_RECHECK_SEC
    env.flow.assert_not_awaited()


def test_cancellation_without_a_worker_result_cannot_claim_no_dispatch(env):
    env.flow.side_effect = MiniAppFlowCancelled()
    with pytest.raises(MiniAppFlowCancelled) as error:
        run()
    assert error.value.result["extra"]["outcome_unknown"]
    stored = state_module.get_miniapp_state_records()["1001:cave_deep_retreat"]["state"]
    assert stored["outcome_unknown"]
    assert stored.get("action_dispatched") is not False
    with state_module.use_identity(1001):
        assert not deep_retreat._cave_public_deep_legacy_fallback_ready(NOW + 1)
