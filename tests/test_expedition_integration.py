"""Mocked ownership, transport, lifecycle and route acceptance; no live requests."""
import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model import app_expeditions, app_xuangu, state, ui
from model.features import cave_treasure_runtime as cave, changfeng_trial as trial, storage_bag as bag


@pytest.fixture(autouse=True)
def isolated_meta():
    before = copy.deepcopy(state._meta_state)
    state.set_expedition_run_state({})
    state.set_expedition_config({})
    state.set_hunt_run_state({})
    bag._clear_storage_bag_transfer_state()
    bag._clear_storage_bag_transfer_batch_state()
    bag._storage_bag_recent_listing_sends.clear()
    yield
    state._meta_state.clear()
    state._meta_state.update(before)
    bag._clear_storage_bag_transfer_state()
    bag._clear_storage_bag_transfer_batch_state()


@pytest.fixture
def trial_env(monkeypatch):
    requests = []
    lock = asyncio.Lock()
    monkeypatch.setattr(trial.MiniAppIdentityOwner, "capture", lambda _: SimpleNamespace(account_id=10, is_current=lambda: True))
    monkeypatch.setattr(trial, "is_cave_public_identity_available", lambda _: True)
    monkeypatch.setattr(trial, "get_global_enabled", lambda: True)
    monkeypatch.setattr(trial, "get_activity_block_reason", lambda *a, **k: "")
    monkeypatch.setattr(trial, "_entry_url", lambda: "fixture-entry")
    monkeypatch.setattr(trial, "_save", lambda: None)
    monkeypatch.setattr(cave, "_public_entry_lock", lambda _: lock)
    monkeypatch.setattr(cave, "_parse_public_cave_entry_url", lambda _: ("fixture-token", "fixture-url", ""))
    monkeypatch.setattr(cave, "_public_entry_allowed", lambda: True)
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", AsyncMock(return_value={
        "ok": True, "player_id": "fixture-player", "init_data": "fixture-init",
    }))
    monkeypatch.setattr(trial, "build_pooled_miniapp_transport", lambda **_: object())
    monkeypatch.setattr(trial, "send_audit_log", AsyncMock())

    async def run_flow(flow, *, operation_check):
        assert operation_check()
        return flow(SimpleNamespace(check=operation_check, sleep=lambda _: None))

    monkeypatch.setattr(trial, "run_miniapp_blocking_flow", run_flow)
    # Advance the request clock without adding real delays to the mocked run.
    ticks = iter(range(1000, 5000, 2))
    monkeypatch.setattr(trial.time, "monotonic", lambda: float(next(ticks)))

    def execute(request, _transport, **kwargs):
        assert kwargs["operation_check"]()
        payload = request["payload"]
        assert payload["playerId"] == "fixture-player"
        assert payload["initData"] == "fixture-init"
        assert payload["token"] == "fixture-token"
        requests.append(copy.deepcopy(payload))
        action = payload["action"]
        if action == "status":
            data = {"status": {"tier": {"next": 1}, "carryAvailable": 0, "used": 1}}
        elif action == "start":
            data = {"run": {"id": "fixture-run", "rewarded": True}}
        else:
            data = {"grants": [{"name": "灵石", "quantity": 2}], "result": {"grade": "乙"}}
        return SimpleNamespace(ok=True, data={"ok": True, "trial": data})

    monkeypatch.setattr(trial, "execute_miniapp_http_request", execute)
    return requests


def test_trial_adapter_full_flow_and_daily_consumption(trial_env):
    ok, message = asyncio.run(trial.run_changfeng_once(1, manual=True))
    assert ok and "灵石×8" in message
    assert [r["action"] for r in trial_env] == ["status", "start"] + ["checkpoint"] * 4
    assert trial_env[1]["carry"] == 0
    record = state.get_expedition_run_state()["changfeng"]["1"]
    assert record["phase"] == "done" and record["daily_used"] == 1
    assert len(record["receipts"]) == 4
    assert "fixture-init" not in repr(record) and "fixture-token" not in repr(record)
    assert not asyncio.run(trial.run_changfeng_once(1, manual=True))[0]
    assert len(trial_env) == 6


def test_trial_held_status_only_never_starts_or_clears_uncertainty(trial_env):
    old = {"phase": "held", "run_id": "uncertain", "daily_used": 1}
    state.set_expedition_run_state({"changfeng": {"1": old}})
    assert asyncio.run(trial.run_changfeng_once(1, manual=True, status_only=True))[0]
    assert [r["action"] for r in trial_env] == ["status"]
    assert old["phase"] == "held" and old["run_id"] == "uncertain"
    assert old["observed_status"]["carryAvailable"] == 0


def test_trial_session_failure_before_start_does_not_hold_a_nonexistent_run(trial_env, monkeypatch):
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", AsyncMock(return_value={"ok": False}))
    assert not asyncio.run(trial.run_changfeng_once(1, manual=True))[0]
    assert not trial_env
    assert state.get_expedition_run_state()["changfeng"]["1"]["phase"] == "idle"


def test_same_grade_score_mismatch_stops_before_another_checkpoint(trial_env, monkeypatch):
    execute = trial.execute_miniapp_http_request

    def mismatch(request, transport, **kwargs):
        result = execute(request, transport, **kwargs)
        if request["payload"]["action"] == "checkpoint":
            score = trial.report_score(request["payload"]["report"])
            result.data["trial"]["result"] = {"grade": "乙", "score": score - 1}
        return result

    monkeypatch.setattr(trial, "execute_miniapp_http_request", mismatch)
    assert not asyncio.run(trial.run_changfeng_once(1, manual=True))[0]
    record = state.get_expedition_run_state()["changfeng"]["1"]
    assert record["phase"] == "held" and record["daily_used"] == 1
    assert len(record["receipts"]) == 1
    assert [request["action"] for request in trial_env] == ["status", "start", "checkpoint"]


def test_trial_transport_unknown_retains_record_without_retry(trial_env, monkeypatch):
    execute = trial.execute_miniapp_http_request

    def fail(request, transport, **kwargs):
        if request["payload"]["action"] == "start":
            trial_env.append(copy.deepcopy(request["payload"]))
            raise TimeoutError()
        return execute(request, transport, **kwargs)

    monkeypatch.setattr(trial, "execute_miniapp_http_request", fail)
    assert not asyncio.run(trial.run_changfeng_once(1, manual=True))[0]
    assert [r["action"] for r in trial_env] == ["status", "start"]
    assert state.get_expedition_run_state()["changfeng"]["1"]["phase"] == "held"
    assert not asyncio.run(trial.run_changfeng_once(1, manual=True))[0]
    assert len(trial_env) == 2


def test_trial_mismatch_preserves_consumed_receipt_before_stopping(trial_env, monkeypatch):
    execute = trial.execute_miniapp_http_request

    def mismatch(request, transport, **kwargs):
        result = execute(request, transport, **kwargs)
        if request["payload"]["action"] == "checkpoint":
            result.data["trial"]["result"]["grade"] = "丙"
        return result

    monkeypatch.setattr(trial, "execute_miniapp_http_request", mismatch)
    assert not asyncio.run(trial.run_changfeng_once(1, manual=True))[0]
    record = state.get_expedition_run_state()["changfeng"]["1"]
    assert record["daily_used"] == 1
    assert record["receipts"][0]["receipt"]["grants"] == [{"name": "灵石", "quantity": 2}]
    assert record["receipts"][0]["receipt"]["result"]["grade"] == "丙"
    assert len(trial_env) == 3


def test_manual_rejection_is_reported(monkeypatch):
    monkeypatch.setattr(trial, "run_changfeng_once", AsyncMock(return_value=(False, "身份不可用")))
    audit = AsyncMock()
    monkeypatch.setattr(app_expeditions, "send_audit_log", audit)
    asyncio.run(app_expeditions._manual_trial(1, "longwind"))
    assert "身份不可用" in audit.call_args.args[0]


def test_manual_confirmation_requires_matching_held_record_and_keeps_receipts(monkeypatch):
    record = {"phase": "held", "operation_id": "start-fixture", "daily_used": 0, "receipts": [{"wave": 0}]}
    state.set_expedition_run_state({"changfeng": {"1": record}})
    monkeypatch.setattr(trial, "_save", lambda: None)
    assert not trial.confirm_changfeng_finished(1, "wrong")[0]
    assert trial.confirm_changfeng_finished(1, "start-fixture")[0]
    assert record["phase"] == "done" and record["daily_used"] == 1
    assert record["receipts"] == [{"wave": 0}]
    assert not trial.confirm_changfeng_finished(1, "start-fixture")[0]


def test_event_exception_does_not_break_sibling_or_listener(monkeypatch):
    state.set_hunt_run_state({"boat": {}})
    state.set_expedition_config({"xuangu": {"enabled": True}})
    monkeypatch.setattr(app_expeditions, "handle_hunt_game_event", AsyncMock(side_effect=ValueError("fixture")))
    sibling = AsyncMock()
    monkeypatch.setattr(app_expeditions, "handle_xuangu_game_event", sibling)

    async def scenario():
        await app_expeditions.handle_expedition_game_event(SimpleNamespace())
        await asyncio.gather(*app_expeditions._EVENT_TASKS)

    asyncio.run(scenario())
    sibling.assert_awaited_once()


def test_blocked_expedition_does_not_starve_daily_siblings_or_overlap(monkeypatch):
    calls = []
    async def scenario():
        release = asyncio.Event()
        async def stuck(now):
            calls.append("stuck")
            await release.wait()
        async def daily(now):
            calls.append("daily")
        monkeypatch.setattr(app_expeditions, "_SCHEDULER_TASKS", {})
        monkeypatch.setattr(app_expeditions, "_SCHEDULER_STARTED", {})
        monkeypatch.setattr(app_expeditions, "run_hunt_scheduler", stuck)
        monkeypatch.setattr(app_expeditions, "run_zhengmo_scheduler", daily)
        for obj, name in [(app_expeditions, "run_xuangu_scheduler"), (app_expeditions, "run_weekly_scheduler"),
                          (trial, "run_changfeng_scheduler"), (app_expeditions.app_spirit_beast, "scheduler"),
                          (app_expeditions.paired_activities, "scheduler"), (app_expeditions.avatar_soul, "supervisor")]:
            async def noop(now):
                pass
            # Distinct module/name keys, like the real adapters.
            noop.__name__ = name
            noop.__module__ = str(id(obj))
            monkeypatch.setattr(obj, name, noop)
        await app_expeditions._run_tick(100)
        assert calls == ["stuck", "daily"]
        await app_expeditions._run_tick(105)
        assert calls == ["stuck", "daily", "daily"]
        snapshot = app_expeditions.get_expedition_scheduler_snapshot()
        assert any(row["running"] and any("stuck:" in line for line in row["awaiting"])
                   for row in snapshot["modules"].values())
        release.set()
        await asyncio.gather(*app_expeditions._SCHEDULER_TASKS.values())
    asyncio.run(scenario())


def test_xuangu_adapter_rejects_wrong_topic(monkeypatch):
    monkeypatch.setattr(app_xuangu, "_enabled", lambda *_: True)
    monkeypatch.setattr(app_xuangu, "hunt_target", lambda _: (-100, 300))
    monkeypatch.setattr(app_xuangu, "get_game_bot_ids", lambda: [99])
    handler = AsyncMock()
    monkeypatch.setattr(app_xuangu.xuangu, "handle_xuangu_game_event", handler)
    event = SimpleNamespace(chat_id=-100, sender_id=99, reply_to=SimpleNamespace(reply_to_top_id=301))
    assert not asyncio.run(app_xuangu.handle_xuangu_game_event(event))
    handler.assert_not_called()


def test_xuangu_headerless_reply_requires_one_owned_command(monkeypatch):
    monkeypatch.setattr(app_xuangu, "_enabled", lambda *_: True)
    monkeypatch.setattr(app_xuangu, "hunt_target", lambda _: (-100, 300))
    monkeypatch.setattr(app_xuangu, "get_game_bot_ids", lambda: [99])
    rows = [{"game_chat_id": -100, "game_topic_id": 300, "command_attempts": {"action": {"msg_id": 500}}}]
    monkeypatch.setattr(app_xuangu, "_context", lambda: SimpleNamespace(flows=SimpleNamespace(active=lambda _: rows)))
    handler = AsyncMock(return_value=True)
    monkeypatch.setattr(app_xuangu.xuangu, "handle_xuangu_game_event", handler)

    async def scenario():
        event = SimpleNamespace(chat_id=-100, sender_id=99, reply_to_msg_id=499, reply_to=None)
        assert not await app_xuangu.handle_xuangu_game_event(event)
        event.reply_to_msg_id = 500
        assert await app_xuangu.handle_xuangu_game_event(event)
        rows.append(copy.deepcopy(rows[0]))
        assert not await app_xuangu.handle_xuangu_game_event(event)

    asyncio.run(scenario())
    assert handler.await_count == 1


def test_storage_explicit_route_persists_across_buy_and_retries(monkeypatch):
    for identity in (1, 2):
        state.ensure_identity_registered(identity)
    monkeypatch.setattr(bag, "get_game_group_id", lambda: -100)
    monkeypatch.setattr(bag, "get_game_group_ids", lambda: [-100, -200])
    monkeypatch.setattr(bag, "get_game_group_topic_id", lambda chat: 300 if chat == -200 else 100)
    send = AsyncMock(return_value=SimpleNamespace(id=500))
    monkeypatch.setattr(bag, "send_game_command", send)

    async def scenario():
        ok, _, _ = await bag.start_storage_bag_transfer_task(1, 2, [{"item_name": "灵石", "quantity": 100}], "黄芽丹", group_role="secondary")
        assert ok and send.call_args.kwargs["target_chat_id"] == -200
        assert send.call_args.kwargs["operation_check"]()
        wrong = {"chat_id": -100, "topic_id": 300, "reply_to_msg_id": 500}
        assert not await bag.handle_storage_bag_transfer_reply("上架成功", time.time(), reply_context=wrong)
        wrong["chat_id"], wrong["topic_id"] = -200, 301
        assert not await bag.handle_storage_bag_transfer_reply("上架成功", time.time(), reply_context=wrong)
        await bag._send_storage_bag_transfer_command(".购买 fixture", identity_id=1, msg_id_key="buy_msg_id", wait_step="waiting_buy_reply", family="storage_bag_buy", retry=True)
        assert send.call_args.kwargs["target_chat_id"] == -200
        check = send.call_args.kwargs["operation_check"]
        bag._clear_storage_bag_transfer_state()
        assert not check()

    asyncio.run(scenario())


def test_storage_preflight_uses_whole_order_and_only_fresh_complete_snapshots():
    state.set_storage_bag_records({"1": {"updated_at": time.time(), "inventory_complete": True, "items": {"灵石": 100}},
                                   "2": {"updated_at": time.time(), "inventory_complete": True, "items": {"黄芽丹": 20}}})
    assert not bag.storage_transfer_inventory_error(1, 2, [{"item_name": "灵石", "quantity": 100}], "黄芽丹", 20)
    assert bag.storage_transfer_inventory_error(1, 2, [{"item_name": "灵石", "quantity": 101}])
    state.get_storage_bag_records()["1"]["updated_at"] -= 121
    assert not bag.storage_transfer_inventory_error(1, 2, [{"item_name": "灵石", "quantity": 101}])
    assert not bag.storage_transfer_inventory_error(3, 2, [{"item_name": "灵石", "quantity": 101}])


def test_expedition_ui_route_requires_auth_and_accepts_valid_configuration(monkeypatch):
    writes = []
    monkeypatch.setattr(ui, "_write_json_unauthorized", lambda *_: writes.append("unauthorized"))
    monkeypatch.setattr(ui, "_write_json_result", lambda *a, **k: writes.append(a[1:3]))
    monkeypatch.setattr(app_expeditions, "save_state", lambda: True)

    async def scenario():
        for session in (None, {"sender_id": 1}):
            assert await ui._serve_ui_json_action_route(None, method="POST", path="/api/expedition-config",
                                                      payload={"hunt_teams": []}, session=session, auth_headers=[])

    asyncio.run(scenario())
    assert writes[0] == "unauthorized" and writes[1][0] is True
