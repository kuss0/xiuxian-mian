import asyncio
import copy
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_runtime as cave
from model.features import fate_cards_miniapp as api
from model.features.miniapp_common import MiniAppFlowCancelled


NOW = 1_700_000_500.0
DAY = "2026-09-11"
ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE999"
FATE_ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=fate_FIXTURE999"


def native(*, drawn=True, reading=True, choice="accept", progress=0, status="active", day=DAY):
    record = {
        "challengeDate": day,
        "createdAt": f"{day}T00:01:00Z",
        "questionKey": "cultivation",
        "choiceKey": choice,
        "cards": [{"key": "cause"}, {"key": "present"}, {"key": "outcome"}],
        "aiReading": {"overview": "fixture"} if reading else {},
        "questKey": "cultivation_gain" if choice else "",
        "questStatus": status if choice else "",
        "quest": {
            "key": "cultivation_gain", "choiceKey": choice,
            "metric": "cultivation_gain", "progress": progress, "target": 30,
            "status": status, "canSettle": progress >= 30 and status == "active",
            "startedAt": f"{day}T00:02:00Z", "expiresAt": f"{day}T23:59:59Z",
        } if choice else {},
    } if drawn else None
    return {
        "ok": True, "challengeDate": day, "hasDrawn": drawn, "traceBalance": 20,
        "questions": [{"key": "cultivation"}],
        "choices": [{"key": "accept"}, {"key": "hide"}], "record": record,
    }


def action_payload(**kwargs):
    return {"ok": True, "record": native(**kwargs)["record"]}


@pytest.mark.parametrize("value", [{}, {"ok": True}, {"data": {"status": "ok"}}, {"ok": False, **{k: v for k, v in native().items() if k != "ok"}}])
def test_unrelated_or_rejected_payload_does_not_become_fate_state(value):
    assert not api.parse_fate_cards_state(value)


@pytest.mark.parametrize("change", ["day", "has_drawn", "record", "cards", "reading", "quest_counter", "quest_flag", "quest_day"])
def test_incomplete_or_contradictory_state_has_no_mutation_authority(change):
    raw = native(progress=30)
    if change == "day":
        raw["challengeDate"] = "2026-02-30"
    elif change == "has_drawn":
        raw["hasDrawn"] = False
    elif change == "record":
        raw["record"] = None
    elif change == "cards":
        raw["record"]["cards"] = []
    elif change == "reading":
        del raw["record"]["aiReading"]
    elif change == "quest_counter":
        raw["record"]["quest"]["target"] = "garbage"
    elif change == "quest_flag":
        raw["record"]["quest"]["canSettle"] = "false"
    else:
        raw["record"]["challengeDate"] = "2026-09-10"
    assert api.parse_fate_cards_state(raw).get("state_verified") is not True


def test_native_action_contract_does_not_require_unsupported_player_id():
    parsed = api.parse_fate_cards_state(action_payload())
    assert parsed["state_verified"] is True
    assert parsed["record_key"]
    assert parsed["quest"]["key"] == "cultivation_gain"
    assert "player_id" not in parsed


def test_invalid_start_exposes_only_safe_contract_reason():
    raw = native()
    raw["record"]["quest"]["progress"] = "token=SECRET"
    result = api.run_fate_cards_start_probe(
        token="fate_SECRET", init_data="query_id=SECRET",
        transport=lambda _request: (200, raw), sleeper=lambda _seconds: None,
    )
    assert result["ok"] is False
    assert result["data"] == {"contract_error": "quest_counter_invalid"}
    assert "SECRET" not in str(result)


@pytest.mark.parametrize("reason,expected", [
    ("quest_counter_invalid", "fate_read_failed:quest_counter_invalid"),
    ("token=SECRET", "fate_read_failed"),
])
def test_failed_start_reason_survives_public_wrapper_without_secrets(env, reason, expected):
    env.probe.return_value = {"ok": False, "data": {"contract_error": reason}}
    result = asyncio.run(cave.run_cave_public_fate_cards(1001, ENTRY, now=NOW))
    assert not result["ok"]
    assert expected in result["message"]
    assert "SECRET" not in str(result)
    env.action.assert_not_awaited()


@pytest.mark.parametrize("body", [{"ok": True}, "<html>temporary error</html>"])
def test_missing_action_contract_preserves_dispatched_unknown(body):
    calls = []

    def transport(request):
        calls.append(request)
        return 200, body

    result = api.run_fate_cards_action(
        "settle", token="fate_FIXTURE999", init_data="fixture_init", transport=transport,
    )
    assert len(calls) == 1
    assert result["action_dispatched"] is True
    assert result["outcome_unknown"] is True


def test_operation_invalidation_prevents_fate_http():
    transport = Mock()
    result = api.run_fate_cards_action(
        "settle", token="fate_FIXTURE999", init_data="fixture_init", transport=transport,
        operation_check=lambda: False,
    )
    transport.assert_not_called()
    assert result["action_dispatched"] is False
    assert not result["outcome_unknown"]


@pytest.mark.parametrize("stage", ["slot", "entity", "input", "webview"])
def test_auth_rechecks_operation_at_each_await(monkeypatch, stage):
    current = True
    visited = []

    def change(at):
        nonlocal current
        visited.append(at)
        if stage == at:
            current = False

    @asynccontextmanager
    async def slot(**_kwargs):
        change("slot")
        yield

    async def entity(*_args):
        change("entity")
        return object()

    async def input_entity(*_args):
        change("input")
        return object()

    async def webview(*_args):
        change("webview")
        return SimpleNamespace(url="https://asc.aiopenai.app/#tgWebAppData=fixture_init")

    client = AsyncMock(side_effect=webview)
    client.get_entity = AsyncMock(side_effect=entity)
    client.get_input_entity = AsyncMock(side_effect=input_entity)
    monkeypatch.setattr(api, "_get_identity_client_with_account", lambda _id: (11, client))
    monkeypatch.setattr(api, "account_rpc_slot", slot)
    with pytest.raises(Exception, match="operation_invalidated"):
        asyncio.run(api.request_fate_cards_miniapp_init_data(
            1001, token="fate_FIXTURE999", webview_url=FATE_ENTRY,
            operation_check=lambda: current,
        ))
    assert visited == ["slot", "entity", "input", "webview"][:["slot", "entity", "input", "webview"].index(stage) + 1]


def test_cancelled_fate_worker_is_drained_and_completion_is_returned():
    entered = threading.Event()
    release = threading.Event()

    def transport(_request):
        entered.set()
        assert release.wait(3)
        return 200, {**action_payload(progress=30, status="settled"), "reward": {"tianjiTrace": 2}}

    async def scenario():
        task = asyncio.create_task(api.run_fate_cards_action_production(
            1001, "settle", token="fate_FIXTURE999", webview_url=FATE_ENTRY,
            init_data="fixture_init", transport=transport,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(MiniAppFlowCancelled) as caught:
            await task
        result = caught.value.result
        assert result["ok"]
        assert result["action_dispatched"] is True
        assert result["data"]["reward"] == {"天机残痕": 2}

    asyncio.run(scenario())


@pytest.fixture
def env(monkeypatch):
    metadata = copy.deepcopy(state_module._meta_state)
    locks = dict(cave._PUBLIC_ENTRY_LOCKS)
    state_module._meta_state.update({
        "identity_ids": [], "identity_states": {}, "send_as_profiles": {},
        "identity_account_map": {}, "miniapp_state_records": {}, "channel_send_as_health": {},
        "miniapp_auto_config": {},
    })
    cave._PUBLIC_ENTRY_LOCKS.clear()
    state_module.ensure_identity_registered(1001)
    state_module.set_identity_account(1001, 11)
    state_module.set_global_enabled(True)
    owner = state_module.get_identity_state(1001)
    session = AsyncMock(return_value={
        "ok": True, "init_data": "fixture_dwelling", "player_id": 1001,
        "result": {"ok": True, "data": {"raw": {
            "ok": True, "account": {"playerId": 1001},
            "externalApps": [{"key": "fate_cards", "url": FATE_ENTRY, "available": True}],
            "dwelling": {"meditation": {"canSettle": False, "projectedGain": 0}},
        }, "overview": {"meditation": {"can_settle": False, "projected_gain": 0}}}},
    })
    probe = AsyncMock(return_value={"ok": True, "data": {"state": api.parse_fate_cards_state(native(progress=30))}})
    action = AsyncMock(return_value={
        "ok": True, "status": "settle", "action_dispatched": True,
        "data": {"state": api.parse_fate_cards_state(native(progress=30, status="settled")), "reward": {"天机残痕": 2}},
    })
    auth = AsyncMock(return_value="fixture_fate")
    save = Mock(return_value=True)
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", session)
    monkeypatch.setattr(cave, "_capture_store", lambda _now: None)
    monkeypatch.setattr(cave, "request_fate_cards_miniapp_init_data", auth)
    monkeypatch.setattr(cave, "run_fate_cards_start_probe_production", probe)
    monkeypatch.setattr(cave, "run_fate_cards_action_production", action)
    monkeypatch.setattr(cave, "send_audit_log", AsyncMock())
    monkeypatch.setattr(cave, "console_log", Mock())
    monkeypatch.setattr(cave, "save_state", save)
    monkeypatch.setattr("model.miniapp_state.save_state", save)
    try:
        yield SimpleNamespace(owner=owner, session=session, probe=probe, action=action, auth=auth, save=save)
    finally:
        cave._PUBLIC_ENTRY_LOCKS.clear()
        cave._PUBLIC_ENTRY_LOCKS.update(locks)
        state_module._meta_state.clear()
        state_module._meta_state.update(metadata)


def run():
    return asyncio.run(cave.run_cave_public_fate_cards(1001, ENTRY, now=NOW))


@pytest.mark.parametrize("change", ["replace", "rebind", "disable", "pause", "record", "deep_schedule"])
def test_entry_change_cannot_dispatch_or_write_fate(env, change):
    after = {}

    async def changed(*_args, **_kwargs):
        if change == "replace":
            state_module._meta_state["identity_states"][1001] = copy.deepcopy(env.owner)
        elif change == "rebind":
            state_module.set_identity_account(1001, 22)
        elif change == "disable":
            state_module.set_identity_enabled(1001, False)
        elif change == "pause":
            state_module.set_global_enabled(False)
        elif change == "deep_schedule":
            env.owner["next_deep_retreat_time"] = NOW + 9999
        else:
            state_module.set_miniapp_state_records({"1001:fate_cards": {"state": {"new": True}}})
        after.update(copy.deepcopy(state_module._meta_state))
        return env.session.return_value

    env.session.side_effect = changed
    result = run()
    assert not result["ok"]
    env.auth.assert_not_awaited()
    env.action.assert_not_awaited()
    assert state_module._meta_state == after


def test_settlement_completion_survives_failed_reconciliation(env):
    env.probe.side_effect = [env.probe.return_value, {"ok": False, "error": "read timeout"}]
    result = run()
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert row["status"] == "settled"
    assert row["gains"] == {"天机残痕": 2}
    assert result["extra"]["daily_exhausted"]


def test_cancelled_settlement_is_recorded_before_parent_reraises(env):
    env.action.side_effect = MiniAppFlowCancelled(env.action.return_value)
    with pytest.raises(MiniAppFlowCancelled):
        run()
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert row["status"] == "settled"
    assert row["gains"] == {"天机残痕": 2}
    assert env.probe.await_count == 1


@pytest.mark.parametrize("change", ["disable", "pause", "config_off"])
def test_inflight_settlement_is_adopted_after_disable_without_more_requests(env, change):
    async def completed(*_args, **_kwargs):
        if change == "disable":
            state_module.set_identity_enabled(1001, False)
        elif change == "pause":
            state_module.set_global_enabled(False)
        else:
            state_module.set_miniapp_auto_config({"cave_public_fate_cards_enabled": False})
        return env.action.return_value

    env.action.side_effect = completed
    assert run()["extra"]["daily_exhausted"]
    assert env.probe.await_count == 1
    assert state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]["gains"] == {"天机残痕": 2}
    if change == "disable":
        assert not state_module.get_identity_enabled(1001)
    elif change == "pause":
        assert not state_module.get_global_enabled()
    else:
        assert not state_module.get_miniapp_auto_config()["cave_public_fate_cards_enabled"]


@pytest.mark.parametrize("stage", ["auth", "probe", "action"])
@pytest.mark.parametrize("change", ["delete", "replace", "rebind", "record"])
def test_each_fate_await_respects_completion_owner(env, stage, change):
    after = {}

    async def changed(*_args, **_kwargs):
        if change == "delete":
            state_module.remove_identity(1001)
        elif change == "replace":
            state_module._meta_state["identity_states"][1001] = copy.deepcopy(env.owner)
        elif change == "rebind":
            state_module.set_identity_account(1001, 22)
        else:
            state_module.set_miniapp_state_records({"1001:fate_cards": {"state": {"new": True}}})
        after.update(copy.deepcopy(state_module._meta_state))
        return getattr(env, stage).return_value

    getattr(env, stage).side_effect = changed
    assert not run()["ok"]
    assert state_module._meta_state == after
    if stage != "action":
        env.action.assert_not_awaited()


@pytest.mark.parametrize("field", ["day", "record", "quest", "progress"])
def test_reconciliation_cannot_confirm_a_different_or_regressed_business(env, field):
    env.action.return_value = {
        "ok": False, "action_dispatched": True, "outcome_unknown": True, "error": "timeout",
    }
    initial = api.parse_fate_cards_state(native(progress=30))
    after = api.parse_fate_cards_state(native(progress=30, status="settled"))
    if field == "day":
        after["challenge_date"] = "2026-09-12"
    elif field == "record":
        after["record_key"] = "different"
    elif field == "quest":
        after["quest"]["started_at"] = "2026-09-11T02:02:00Z"
    else:
        after["quest"]["progress"] = 0
    env.probe.side_effect = [{"ok": True, "data": {"state": initial}}, {"ok": True, "data": {"state": after}}]
    assert not run()["ok"]
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert row["pending"]["action"] == "settle"
    assert row.get("gains", {}) == {}
    assert row["challenge_date"] == DAY


@pytest.mark.parametrize("summary_pressure", [False, True])
def test_unknown_draw_survives_codec_failed_reads_and_identical_panel_without_replay(env, summary_pressure):
    from model import miniapp_state, persistence

    undrawn = {"ok": True, "data": {"state": api.parse_fate_cards_state(native(drawn=False))}}
    env.probe.return_value = undrawn
    env.action.return_value = {"ok": False, "action_dispatched": True, "outcome_unknown": True, "error": "timeout"}
    assert not run()["ok"]
    if summary_pressure:
        for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 1):
            miniapp_state.record_miniapp_state(1001, f"diagnostic_{number}", {"marker": number}, now=NOW + number + 1, persist=False)
    _get, encode, restore = persistence._META_STATE_CODEC["miniapp_state_records"]
    encoded = encode(state_module.get_miniapp_state_records())
    state_module.set_miniapp_state_records({})
    cave._PUBLIC_ENTRY_LOCKS.clear()
    restore(encoded)
    env.action.reset_mock()
    env.probe.return_value = {"ok": False, "error": "read timeout"}
    assert not run()["ok"]
    env.probe.return_value = undrawn
    assert not run()["ok"]
    env.action.assert_not_awaited()
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert row["pending"]["action"] == "draw"
    assert row["pending"]["before"]["has_drawn"] is False
    assert row["pending"]["before"]["state_verified"] is True


def test_authoritative_settled_panel_resolves_unknown_without_replay_or_guessed_reward(env):
    env.action.return_value = {"ok": False, "action_dispatched": True, "outcome_unknown": True, "error": "timeout"}
    assert not run()["ok"]
    env.action.reset_mock()
    env.probe.return_value = {"ok": True, "data": {"state": api.parse_fate_cards_state(native(progress=30, status="settled"))}}
    result = run()
    assert result["extra"]["daily_exhausted"]
    env.action.assert_not_awaited()
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert not row.get("pending")
    assert not row.get("gains")


def test_confirmed_reward_not_counted_again_on_next_run_or_old_receipt(env):
    settled = env.action.return_value["data"]["state"]
    env.probe.side_effect = [env.probe.return_value, {"ok": True, "data": {"state": settled}}]
    assert run()["ok"]
    env.probe.side_effect = None
    env.probe.return_value = {"ok": True, "data": {"state": settled}}
    assert run()["ok"]
    env.action.assert_awaited_once()
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert row["gains"] == {"天机残痕": 2}
    assert len(row["receipts"]) == 1


def test_explicit_rejection_does_not_create_an_unknown_outcome_hold(env):
    env.action.return_value = {
        "ok": False, "action_dispatched": True, "outcome_unknown": False, "error": "rejected",
    }
    assert not run()["ok"]
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert not row.get("pending")
    assert not row["status"].endswith("_unknown")


def meditation_ready(env):
    env.probe.return_value = {"ok": True, "data": {"state": api.parse_fate_cards_state(native())}}
    session = env.session.return_value
    session["result"]["data"]["overview"]["meditation"] = {"can_settle": True, "projected_gain": 18}
    session["result"]["data"]["raw"]["dwelling"]["meditation"] = {
        "canSettle": True, "projectedGain": 18, "lastUpdateMs": 123000,
    }


def meditation_result():
    return {"ok": True, "action_dispatched": True, "outcome_unknown": False, "data": {
        "ok": True, "account": {"playerId": 1001},
        "actionResult": {"ok": True, "cultivationGain": 18},
    }}


@pytest.mark.parametrize("cancelled", [False, True])
def test_meditation_completion_survives_refresh_failure_or_cancellation(env, monkeypatch, cancelled):
    meditation_ready(env)
    result = meditation_result()
    meditate = AsyncMock(return_value=result)
    if cancelled:
        meditate.side_effect = MiniAppFlowCancelled(result)
    else:
        env.session.side_effect = [env.session.return_value, {"ok": False, "error": "timeout"}]
    monkeypatch.setattr(cave, "run_cave_meditation_settle_production_flow", meditate)
    if cancelled:
        with pytest.raises(MiniAppFlowCancelled):
            run()
        assert env.session.await_count == 1
    else:
        assert not run()["ok"]
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert row["gains"] == {"修为": 18}
    assert not row.get("pending")
    env.action.assert_not_awaited()


@pytest.mark.parametrize("summary_pressure", [False, True])
def test_meditation_unknown_is_not_replayed_on_another_run(env, monkeypatch, summary_pressure):
    from model import miniapp_state, persistence

    meditation_ready(env)
    meditate = AsyncMock(return_value={"ok": False, "action_dispatched": True, "outcome_unknown": True})
    monkeypatch.setattr(cave, "run_cave_meditation_settle_production_flow", meditate)
    assert not run()["ok"]
    if summary_pressure:
        for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 1):
            miniapp_state.record_miniapp_state(1001, f"diagnostic_{number}", {"marker": number}, now=NOW + number + 1, persist=False)
    _get, encode, restore = persistence._META_STATE_CODEC["miniapp_state_records"]
    encoded = encode(state_module.get_miniapp_state_records())
    state_module.set_miniapp_state_records({})
    cave._PUBLIC_ENTRY_LOCKS.clear()
    restore(encoded)
    assert not run()["ok"]
    meditate.assert_awaited_once()
    assert state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]["pending"]["action"] == "meditation"


@pytest.mark.parametrize("status", ["active", "settled", "expired"])
def test_completed_same_quest_can_skip_unknown_prerequisite_without_inventing_its_reward(env, monkeypatch, status):
    meditation_ready(env)
    meditate = AsyncMock(return_value={"ok": False, "action_dispatched": True, "outcome_unknown": True})
    monkeypatch.setattr(cave, "run_cave_meditation_settle_production_flow", meditate)
    assert not run()["ok"]
    ready = {"ok": True, "data": {"state": api.parse_fate_cards_state(native(progress=30, status=status))}}
    settled = {"ok": True, "data": {"state": api.parse_fate_cards_state(native(progress=30, status="settled"))}}
    env.probe.side_effect = [ready, settled]
    result = run()
    assert result["ok"]
    meditate.assert_awaited_once()
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert not row.get("pending")
    assert row["unconfirmed_prerequisite"]["action"] == "meditation"
    assert row["unconfirmed_prerequisite"]["outcome_unknown"] is True
    if status == "active":
        env.action.assert_awaited_once()
        assert env.action.await_args.args[1] == "settle"
        assert row["gains"] == {"天机残痕": 2}
    else:
        env.action.assert_not_awaited()
        assert not row.get("gains")


@pytest.mark.parametrize("changed", ["day", "record", "quest", "progress"])
def test_unmatched_or_incomplete_quest_cannot_release_unknown_prerequisite(env, monkeypatch, changed):
    meditation_ready(env)
    meditate = AsyncMock(return_value={"ok": False, "action_dispatched": True, "outcome_unknown": True})
    monkeypatch.setattr(cave, "run_cave_meditation_settle_production_flow", meditate)
    assert not run()["ok"]
    observed = api.parse_fate_cards_state(native(progress=30))
    if changed == "day":
        observed["challenge_date"] = "2026-09-12"
    elif changed == "record":
        observed["record_key"] = "different"
    elif changed == "quest":
        observed["quest"]["started_at"] = "2026-09-11T02:02:00Z"
    else:
        observed["quest"]["progress"] = 29
        observed["quest"]["can_settle"] = False
    env.probe.return_value = {"ok": True, "data": {"state": observed}}
    assert not run()["ok"]
    meditate.assert_awaited_once()
    env.action.assert_not_awaited()
    assert state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]["pending"]["action"] == "meditation"


@pytest.mark.parametrize("change", ["wrong_player", "missing_action", "action_rejected", "bad_gain", "profile_gain"])
def test_quiet_room_requires_a_matching_native_action_receipt(env, monkeypatch, change):
    meditation_ready(env)
    result = meditation_result()
    if change == "wrong_player":
        result["data"]["account"]["playerId"] = 1002
    elif change == "missing_action":
        del result["data"]["actionResult"]
    elif change == "action_rejected":
        result["data"]["actionResult"]["ok"] = False
    elif change == "bad_gain":
        result["data"]["actionResult"]["cultivationGain"] = "invalid"
    else:
        result["data"]["account"]["profile"] = {"cultivationGain": 900000}
    monkeypatch.setattr(cave, "run_cave_meditation_settle_production_flow", AsyncMock(return_value=result))
    env.session.side_effect = [env.session.return_value, {"ok": False, "error": "timeout"}]
    assert not run()["ok"]
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert row.get("gains", {}) == ({"修为": 18} if change == "profile_gain" else {})
    env.action.assert_not_awaited()


@pytest.mark.parametrize("status", ["settled", "expired"])
def test_terminal_quest_does_not_start_interpretation_or_retreat(env, status):
    env.probe.return_value = {"ok": True, "data": {"state": api.parse_fate_cards_state(native(reading=False, status=status))}}
    assert run()["extra"]["daily_exhausted"]
    env.action.assert_not_awaited()


def test_new_run_cannot_regress_a_verified_same_day_snapshot(env):
    settled = env.action.return_value["data"]["state"]
    env.probe.side_effect = [env.probe.return_value, {"ok": True, "data": {"state": settled}}]
    assert run()["ok"]
    previous = copy.deepcopy(state_module.get_miniapp_state_records()["1001:fate_cards"])
    env.probe.side_effect = None
    env.probe.return_value = {"ok": True, "data": {"state": api.parse_fate_cards_state(native(drawn=False))}}
    env.action.reset_mock()
    assert not run()["ok"]
    env.action.assert_not_awaited()
    assert state_module.get_miniapp_state_records()["1001:fate_cards"] == previous


def test_reward_from_another_record_is_not_adopted_by_reconciliation(env):
    initial = env.probe.return_value
    settled = api.parse_fate_cards_state(native(progress=30, status="settled"))
    wrong = api.parse_fate_cards_state(native(progress=30, status="settled", day="2026-09-12"))
    env.action.return_value["data"]["state"] = wrong
    env.probe.side_effect = [initial, {"ok": True, "data": {"state": settled}}]
    assert not run()["ok"]
    row = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert not row.get("gains")
    assert row["pending"]["action"] == "settle"


def test_an_action_without_bound_reward_evidence_does_not_invent_a_gain(env):
    settled = api.parse_fate_cards_state(native(progress=30, status="settled"))
    env.probe.side_effect = [env.probe.return_value, {"ok": True, "data": {"state": settled}}]
    env.action.return_value["data"].pop("state")
    env.action.return_value["outcome_unknown"] = True
    assert run()["ok"]
    assert not state_module.get_miniapp_state_records()["1001:fate_cards"]["state"].get("gains")


def test_native_channel_chain_collects_each_action_gain_once(env, monkeypatch):
    from functools import partial
    from model.features import cave_treasure_miniapp as dwelling_api

    player = -1_000_000_001_001
    state_module.ensure_identity_registered(11)
    primary = copy.deepcopy(state_module.get_identity_state(11))
    state_module.set_identity_enabled(1001, False)
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [1001]})
    raw = native(drawn=False)
    calls = []
    meditation_ready(env)
    session = env.session.return_value
    session["player_id"] = player
    session["result"]["data"]["raw"]["account"]["playerId"] = player

    def transport(request):
        nonlocal raw
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return 200, copy.deepcopy(raw)
        if endpoint == "draw":
            assert request["payload"]["questionKey"] == "cultivation"
            raw = native(reading=False, choice="")
            return 200, {"ok": True, "record": copy.deepcopy(raw["record"]), "reward": {"tianjiTrace": 1}}
        if endpoint == "interpret":
            raw = native(choice="")
        elif endpoint == "choose":
            assert request["payload"]["choiceKey"] == "accept"
            raw = native()
        elif endpoint == "settle":
            assert raw["record"]["quest"]["canSettle"]
            raw = native(progress=30, status="settled")
            return 200, {"ok": True, "record": copy.deepcopy(raw["record"]), "reward": {"tianjiTrace": 2}}
        else:
            raise AssertionError(endpoint)
        return 200, {"ok": True, "record": copy.deepcopy(raw["record"])}

    def meditation_transport(request):
        nonlocal raw
        assert request["payload"]["playerId"] == player
        calls.append("meditation")
        raw = native(progress=30)
        session["result"]["data"]["raw"]["dwelling"]["meditation"]["canSettle"] = False
        session["result"]["data"]["overview"]["meditation"]["can_settle"] = False
        result = meditation_result()["data"]
        result["account"]["playerId"] = player
        return 200, result

    monkeypatch.setattr(cave, "run_fate_cards_start_probe_production", partial(api.run_fate_cards_start_probe_production, transport=transport, sleeper=lambda _s: None))
    monkeypatch.setattr(cave, "run_fate_cards_action_production", partial(api.run_fate_cards_action_production, transport=transport, sleeper=lambda _s: None))
    monkeypatch.setattr(cave, "run_cave_meditation_settle_production_flow", partial(
        dwelling_api.run_cave_meditation_settle_production_flow, transport=meditation_transport, sleeper=lambda _s: None,
    ))
    result = run()
    assert result["ok"], result
    assert calls == ["start", "draw", "start", "interpret", "start", "choose", "start", "meditation", "start", "settle", "start"]
    assert result["extra"]["gains"] == {"修为": 18, "天机残痕": 3}
    assert state_module.get_identity_state(11) == primary
    assert not state_module.get_identity_enabled(1001)


@pytest.mark.parametrize("change", ["selected", "account", "missing", "selector_echo"])
def test_unverified_dwelling_session_cannot_launch_fate(env, change):
    session = env.session.return_value
    raw = session["result"]["data"]["raw"]
    if change == "selected":
        session["player_id"] = 1002
    elif change == "account":
        raw["account"]["playerId"] = 1002
    elif change == "missing":
        del raw["account"]
    else:
        raw["identity"] = {"selectedPlayerId": 1002}
    assert not run()["ok"]
    env.auth.assert_not_awaited()
    env.action.assert_not_awaited()


def test_explicit_http_200_business_rejection_is_not_an_unknown_mutation():
    result = api.run_fate_cards_action(
        "settle", token="fate_FIXTURE999", init_data="fixture_init",
        transport=lambda _request: (200, {"ok": False, "error": "quest_not_complete"}),
    )
    assert not result["ok"]
    assert result["action_dispatched"]
    assert not result["outcome_unknown"]


def test_post_dispatch_parser_exception_does_not_become_safe_to_replay(monkeypatch):
    transport = Mock(return_value=(200, action_payload(progress=30, status="settled")))
    monkeypatch.setattr(api, "parse_fate_cards_state", Mock(side_effect=RuntimeError("parser failure")))
    result = asyncio.run(api.run_fate_cards_action_production(
        1001, "settle", token="fate_FIXTURE999", webview_url=FATE_ENTRY,
        init_data="fixture_init", transport=transport,
    ))
    transport.assert_called_once()
    assert not result["ok"]
    assert result["outcome_unknown"]
    assert result.get("action_dispatched") is not False


@pytest.mark.parametrize("control", ["cave_public_fate_cards_enabled", "cave_public_fate_cards_choice_key", "cave_public_deep_status_enabled"])
def test_live_control_changes_stop_the_chain_before_mutation(env, control):
    async def auth(*_args, **_kwargs):
        state_module.set_miniapp_auto_config({control: "hide" if control.endswith("choice_key") else False})
        return "fixture_fate"

    env.auth.side_effect = auth
    assert not run()["ok"]
    env.action.assert_not_awaited()
    env.probe.assert_not_awaited()


@pytest.mark.parametrize("balance,expected", [(None, 20), (0, 0), (27, 27)])
def test_action_partial_balance_does_not_reset_last_observation_to_zero(env, balance, expected):
    body = action_payload(progress=30, status="settled")
    if balance is not None:
        body["reward"] = {"balance": balance, "tianjiTrace": 2}
    env.action.return_value["data"]["state"] = api.parse_fate_cards_state(body)
    env.probe.side_effect = [env.probe.return_value, {"ok": False, "error": "timeout"}]
    assert run()["ok"]
    saved = state_module.get_miniapp_state_records()["1001:fate_cards"]["state"]
    assert saved["trace_balance"] == expected
    assert saved["snapshot"]["trace_balance_known"] is (balance is not None)
