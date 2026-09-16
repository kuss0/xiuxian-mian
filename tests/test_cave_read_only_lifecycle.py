import asyncio
import copy
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_miniapp as api
from model.features import cave_treasure_runtime as cave
from model.features import concubine, tianti, yinluo
from model.features.miniapp_common import MiniAppFlowCancelled
from model.real_message_replay import get_real_message_text
from model.webapp_core import MiniAppHttpResult
from tests import test_concubine_external_events as external_tests


ID = 1001
ACCOUNT = 11
PLAYER = -1_000_000_001_001
NOW = 1_700_000_500.0
ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE999"
TIANTI = (
    "\u3010\u51cc\u9704\u4e91\u9636\u3011\n\u5f53\u524d\u8fdb\u5ea6\uff1a3 / 12 \u9636\n"
    "\u5df2\u5b8c\u6210\u5468\u5929\uff1a1 \u8f6e\n\u7f61\u98ce\u6dec\u4f53\uff1a2 / 12 \u5c42\n"
    "\u767b\u9636\u51b7\u5374\uff1a\u53ef\u7acb\u5373\u767b\u9636\n"
    "\u95ee\u5fc3\u72b6\u6001\uff1a\u4eca\u65e5\u5c1a\u672a\u95ee\u5fc3"
)
YINLUO = (
    "\u3010fixture\u7684\u9634\u7f57\u5e61\u3011\n\u672c\u547d\u9b54\u5175\uff1a\u4e4c\u9f99\u5e61\n"
    "\u5e61\u4f53\u7b49\u9636\uff1a\u7384\u9636\n\u715e\u6c14\u6c60\uff1a120 / 500 (24%)\n"
    "- 1\u53f7\u69fd\uff1a[\u7a7a\u95f2]"
)
CONCUBINE = (
    "\u4f60\u7684\u9053\u5fc3\u4f8d\u59be\uff1a\u3010\u5357\u5bab\u5a49\u3011\uff08\u72b6\u6001\uff1a\u968f\u884c\u4e2d\uff09\n"
    "\u60c5\u7f18\u503c\uff1a184\n"
    "\u5165\u68a6\u5bfb\u56fe\u51b7\u5374\uff1a2\u5c0f\u65f6\n"
    "\u5929\u673a\u4ee3\u535c\u51b7\u5374\uff1a3\u5c0f\u65f6\n"
    "\u5171\u5386\u5fc3\u52ab\u51b7\u5374\uff1a4\u5c0f\u65f6"
)
KINDS = ("tianti", "tianti_generic", "yinluo", "concubine")
COMMANDS = {
    "tianti": tianti.CMD_TIANTI_STATUS,
    "tianti_generic": tianti.CMD_TIANTI_STATUS,
    "yinluo": ".\u6211\u7684\u9634\u7f57\u5e61",
    "concubine": ".\u6211\u7684\u4f8d\u59be",
}
PANELS = {"tianti": TIANTI, "tianti_generic": TIANTI, "yinluo": YINLUO, "concubine": CONCUBINE}


def payload(message):
    return {"account": {"playerId": PLAYER}, "actionResult": {"ok": True, "completed": True, "rawMessage": message}}


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(ID, ACCOUNT)
    identity = state_module.get_identity_state(ID)
    identity.update(tianti_enabled=False, concubine_phase="idle")
    identity["yinluo_observation"] = yinluo.normalize_yinluo_observation({"sha_current": 300, "sha_max": 500})
    session = {"ok": True, "init_data": "fixture-init", "player_id": PLAYER, "result": {
        "ok": True, "data": {"raw": {"account": {"playerId": PLAYER}}},
    }}
    loader = AsyncMock(return_value=session)
    flow = AsyncMock(return_value={"ok": True, "status": "ok", "data": payload(TIANTI)})
    audit, save = AsyncMock(), Mock()
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_tianjige_command_production_flow", flow)
    monkeypatch.setattr(cave, "_capture_store", lambda _now: None)
    monkeypatch.setattr(cave, "send_audit_log", audit)
    monkeypatch.setattr(cave, "console_log", Mock())
    monkeypatch.setattr(tianti, "_TIANTI_RUN_LOCKS", {})
    monkeypatch.setattr(tianti.random, "randint", lambda *_args: 0)
    for module in (tianti, yinluo, concubine):
        monkeypatch.setattr(module, "save_state", save)
        monkeypatch.setattr(module, "send_game_command", AsyncMock())
    monkeypatch.setattr(concubine, "send_audit_log", AsyncMock())
    try:
        yield SimpleNamespace(identity=identity, session=loader, flow=flow, audit=audit, save=save)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(saved)


async def read(kind="tianti"):
    if kind == "tianti":
        return await cave.run_cave_public_tianti_status(ID, ENTRY, now=NOW)
    return await cave.run_cave_public_tianjige_read_only(ID, ENTRY, COMMANDS[kind], now=NOW)


async def observe_partner_change(at):
    state_module.set_game_group_id(external_tests.CHAT)
    state_module.set_game_bot_ids([external_tests.BOT])
    state_module.update_send_as_profile(ID, username="cave_owner")
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_affinity_event(
            external_tests.MOON.replace("query_owner", "cave_owner"), at,
            SimpleNamespace(id=external_tests.ROOT, chat_id=external_tests.CHAT,
                            sender_id=external_tests.BOT, server_event_at=at))


@pytest.mark.parametrize("stage", ["session", "result"])
def test_inflight_miniapp_read_cannot_reconcile_a_newer_external_observation(env, stage):
    env.flow.return_value["data"] = payload(CONCUBINE)
    selected = env.session if stage == "session" else env.flow

    async def completed(*_args, **kwargs):
        assert kwargs["operation_check"]()
        with state_module.use_identity(ID):
            assert await observe_partner_change(NOW + 1)
        assert not kwargs["operation_check"]()
        return selected.return_value

    selected.side_effect = completed
    response = asyncio.run(read("concubine"))
    assert not response["ok"]
    assert response["extra"]["status"] == "cancelled"
    assert env.identity[external_tests.FIELD]["status"] == "pending"
    assert env.identity["concubine_last_snapshot_at"] == 0


def test_new_miniapp_read_atomically_reconciles_saved_external_observation(env):
    env.flow.return_value["data"] = payload(CONCUBINE)
    with state_module.use_identity(ID):
        assert asyncio.run(observe_partner_change(NOW - 1))
    assert asyncio.run(read("concubine"))["ok"]
    assert env.identity[external_tests.FIELD]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 184
    assert env.identity["concubine_name"] == "\u5357\u5bab\u5a49"
    concubine.send_game_command.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_owned_panels_update_their_own_module_without_group_sends(env, kind):
    env.flow.return_value["data"] = payload(PANELS[kind])
    response = asyncio.run(read(kind))
    assert response["ok"]
    if kind.startswith("tianti"):
        assert env.identity["tianti_progress_current"] == 3
        assert not env.identity["tianti_enabled"]
    elif kind == "yinluo":
        assert env.identity["yinluo_observation"]["sha_current"] == 120
    else:
        assert env.identity["concubine_affinity"] == 184
    for module in (tianti, yinluo, concubine):
        module.send_game_command.assert_not_awaited()
    env.flow.assert_awaited_once()


@pytest.mark.parametrize("stage", ["session", "result"])
@pytest.mark.parametrize("change", [
    "delete", "replace", "rebind", "disable", "pause", "module_toggle",
    "new_climb", "new_wenxin", "new_gangfeng", "new_status",
])
def test_tianti_rechecks_owner_controls_and_every_business_clock(env, stage, change):
    after = {}

    async def completed(*_args, **_kwargs):
        if change == "delete":
            state_module.remove_identity(ID)
        elif change == "replace":
            state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
        elif change == "rebind":
            state_module.set_identity_account(ID, 22)
        elif change == "disable":
            state_module.set_identity_enabled(ID, False)
        elif change == "pause":
            state_module.set_global_enabled(False)
        elif change == "module_toggle":
            env.identity["tianti_enabled"] = True
        else:
            env.identity[f"next_tianti_{change.removeprefix('new_')}_time"] = NOW + 12345
        after.update(copy.deepcopy(state_module._meta_state))
        return env.session.return_value if stage == "session" else env.flow.return_value

    (env.session if stage == "session" else env.flow).side_effect = completed
    response = asyncio.run(read())
    assert not response["ok"]
    assert response["extra"]["status"] == "cancelled"
    assert state_module._meta_state == after
    env.save.assert_not_called()
    assert env.flow.await_count == (stage == "result")


@pytest.mark.parametrize("kind", KINDS)
def test_parent_entry_invalidation_is_passed_to_both_read_flows(env, kind):
    env.flow.return_value["data"] = payload(PANELS[kind])
    allowed = True

    async def loaded(*_args, **kwargs):
        nonlocal allowed
        assert kwargs["operation_check"]()
        allowed = False
        return env.session.return_value

    env.session.side_effect = loaded
    before = copy.deepcopy(state_module._meta_state)
    with cave.observe_cave_public_entry(ID, ENTRY, operation_check=lambda: allowed):
        response = asyncio.run(read(kind))
    assert not response["ok"]
    assert state_module._meta_state == before
    env.flow.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stage", ["session", "result"])
def test_read_failure_retains_retry_after(env, kind, stage):
    rejected = {
        "ok": False, "status": "failed", "error": "fixture-limit",
        "events": [{"status_code": 429, "retry_after_sec": 50000, "shared_rate_limit": True}],
    }
    (env.session if stage == "session" else env.flow).return_value = rejected
    before = copy.deepcopy(state_module._meta_state)
    response = asyncio.run(read(kind))
    assert not response["ok"]
    assert response["extra"]["retry_after_sec"] == 50000
    assert response["extra"]["shared_rate_limit"]
    assert state_module._meta_state == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("failure", ["exception", "cancel"])
def test_notification_cannot_discard_saved_read_result(env, kind, failure):
    env.flow.return_value["data"] = payload(PANELS[kind])
    env.audit.side_effect = RuntimeError("audit unavailable") if failure == "exception" else asyncio.CancelledError()
    if failure == "cancel":
        with pytest.raises(MiniAppFlowCancelled) as raised:
            asyncio.run(read(kind))
        response = raised.value.result
    else:
        response = asyncio.run(read(kind))
    assert response["ok"]
    env.save.assert_called()


@pytest.mark.parametrize("kind", KINDS)
def test_cancelled_http_read_carries_workflow_failure_not_unapplied_success(env, monkeypatch, kind):
    entered, release = threading.Event(), threading.Event()
    before = copy.deepcopy(state_module._meta_state)

    def execute(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return MiniAppHttpResult(ok=True, data=payload(PANELS[kind]), attempts=1, status_code=200)

    monkeypatch.setattr(api, "execute_miniapp_http_request", execute)
    monkeypatch.setattr(cave, "run_cave_tianjige_command_production_flow", api.run_cave_tianjige_command_production_flow)

    async def scenario():
        task = asyncio.create_task(read(kind))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0.01)
            assert cave._public_entry_lock(ID).locked()
            assert not task.done()
            release.set()
            with pytest.raises(MiniAppFlowCancelled) as raised:
                await task
            assert not raised.value.result["ok"]
            assert raised.value.result["extra"]["status"] == "cancelled"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert not cave._public_entry_lock(ID).locked()

    asyncio.run(scenario())
    assert state_module._meta_state == before
    env.save.assert_not_called()


@pytest.mark.parametrize("kind,clock", [
    ("tianti", "tianti_last_status_seen_at"),
    ("concubine", "concubine_last_snapshot_at"),
])
def test_older_read_cannot_regress_an_existing_newer_observation(env, kind, clock):
    env.identity[clock] = NOW + 1
    env.flow.return_value["data"] = payload(PANELS[kind])
    before = copy.deepcopy(env.identity)
    response = asyncio.run(read(kind))
    assert not response["ok"]
    assert env.identity == before
    env.save.assert_not_called()


@pytest.mark.parametrize("command", [tianti.CMD_TIANTI_CLIMB, tianti.CMD_TIANTI_WENXIN, tianti.CMD_TIANTI_GANGFENG])
def test_status_read_does_not_rearm_a_pending_mutation(env, command):
    env.identity["pending_tasks"] = {(-10011, 55): {"cmd": command, "time": NOW - 10}}
    before = copy.deepcopy(env.identity)
    response = asyncio.run(read())
    assert not response["ok"]
    assert env.identity == before
    env.session.assert_not_awaited()


def test_status_read_does_not_calibrate_during_reserved_climb(env):
    env.identity["tianti_commands"] = {"climb": {
        "op_id": "read-guard-test", "identity_id": ID,
        "account_id": state_module.get_identity_account(ID), "command": tianti.CMD_TIANTI_CLIMB,
        "chat_id": -10011, "msg_id": 0, "started_at": NOW, "status": "sending",
        "rank_choice": state_module.get_tianti_rank_choice(ID),
    }}
    before = copy.deepcopy(env.identity)
    response = asyncio.run(read())
    assert not response["ok"]
    assert env.identity == before
    env.session.assert_not_awaited()


@pytest.mark.parametrize("message", [
    TIANTI.replace("3 / 12", "13 / 12"),
    TIANTI.replace("3 / 12", "3 / 0"),
    TIANTI.replace("2 / 12", "20 / 12"),
    TIANTI + "\n\u5f53\u524d\u8fdb\u5ea6\uff1a4 / 12 \u9636",
    TIANTI + "\n\u767b\u9636\u51b7\u5374\uff1a2\u5c0f\u65f6",
    TIANTI.replace("\u53ef\u7acb\u5373\u767b\u9636", "\u672a\u5f00\u653e"),
    TIANTI.replace("\u53ef\u7acb\u5373\u767b\u9636", "\u4e0d\u53ef\u7528"),
    TIANTI.replace("\u53ef\u7acb\u5373\u767b\u9636", "\u7b49\u5f85GM\u8c03\u6574"),
    TIANTI + "\n.\u5f15\u4e5d\u5929\u7f61\u98ce\uff1a\u4e0d\u53ef\u7528",
    TIANTI.replace("\u4eca\u65e5\u5c1a\u672a\u95ee\u5fc3", "\u4eca\u65e5\u5df2\u95ee\u5fc3\uff1b\u4eca\u65e5\u5c1a\u672a\u95ee\u5fc3"),
])
def test_ambiguous_or_invalid_panel_never_publishes_ready_state(env, message):
    env.identity.update(next_tianti_climb_time=NOW + 3600, next_tianti_wenxin_time=NOW + 86400)
    env.flow.return_value["data"] = payload(message)
    before = copy.deepcopy(env.identity)
    response = asyncio.run(read())
    assert not response["ok"]
    assert env.identity == before
    env.save.assert_not_called()


MUTATIONS = {
    "tianti": tianti.CMD_TIANTI_WENXIN,
    "tianti_generic": tianti.CMD_TIANTI_GANGFENG,
    "yinluo": ".\u5316\u529f\u4e3a\u715e 10000",
    "concubine": concubine.CMD_CONCUBINE_DAILY_GREET,
}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stage", ["session", "result"])
def test_pending_mutation_appearing_during_read_invalidates_the_snapshot(env, kind, stage):
    env.flow.return_value["data"] = payload(PANELS[kind])
    after = {}

    async def completed(*_args, **_kwargs):
        env.identity["pending_tasks"] = {(-10011, 70): {"cmd": MUTATIONS[kind], "time": NOW}}
        after.update(copy.deepcopy(env.identity))
        return env.session.return_value if stage == "session" else env.flow.return_value

    (env.session if stage == "session" else env.flow).side_effect = completed
    response = asyncio.run(read(kind))
    assert not response["ok"]
    assert response["extra"]["status"] == "cancelled"
    assert env.identity == after
    env.save.assert_not_called()
    env.audit.assert_not_awaited()
    assert env.flow.await_count == (stage == "result")


@pytest.mark.parametrize("kind", KINDS)
def test_unrelated_pending_does_not_disable_status_reads(env, kind):
    pending = {(-10011, 71): {"cmd": ".\u70b9\u536f", "family": "checkin", "time": NOW - 10}}
    env.identity["pending_tasks"] = copy.deepcopy(pending)
    env.flow.return_value["data"] = payload(PANELS[kind])
    assert asyncio.run(read(kind))["ok"]
    assert env.identity["pending_tasks"] == pending


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("pending", [None, [], {1: "invalid"}])
def test_invalid_pending_state_never_authorizes_a_read(env, kind, pending):
    env.identity["pending_tasks"] = pending
    before = copy.deepcopy(env.identity)
    response = asyncio.run(read(kind))
    assert not response["ok"]
    assert response["extra"]["reason"] == "invalid_pending"
    assert env.identity == before
    env.session.assert_not_awaited()
    env.save.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stage", ["session", "result"])
def test_cancelled_reads_preserve_limit_metadata_but_not_raw_success(env, kind, stage):
    env.flow.return_value["data"] = payload(PANELS[kind])
    cancelled_result = {
        "ok": True, "status": "ok", "data": payload(PANELS[kind]),
        "events": [{"status_code": 429, "retry_after_sec": 50000, "shared_rate_limit": True}],
    }
    (env.session if stage == "session" else env.flow).side_effect = MiniAppFlowCancelled(cancelled_result)
    before = copy.deepcopy(env.identity)
    with pytest.raises(MiniAppFlowCancelled) as raised:
        asyncio.run(read(kind))
    response = raised.value.result
    assert not response["ok"]
    assert response["extra"]["status"] == "cancelled"
    assert response["extra"]["retry_after_sec"] == 50000
    assert response["extra"]["shared_rate_limit"]
    assert "data" not in response
    assert env.identity == before
    env.audit.assert_not_awaited()
    env.save.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("failure", ["exception", "cancel"])
def test_failed_read_retains_limit_even_if_its_notification_fails(env, kind, failure):
    env.flow.return_value = {
        "ok": False, "status": "failed", "error": "fixture-limit",
        "events": [{"status_code": 429, "retry_after_sec": 50000, "shared_rate_limit": True}],
    }
    env.audit.side_effect = RuntimeError("audit unavailable") if failure == "exception" else asyncio.CancelledError()
    if failure == "cancel":
        with pytest.raises(MiniAppFlowCancelled) as raised:
            asyncio.run(read(kind))
        response = raised.value.result
    else:
        response = asyncio.run(read(kind))
    assert not response["ok"]
    assert response["extra"]["shared_retry_after_sec"] == 50000
    env.save.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
def test_successful_sync_is_not_written_again_after_notification_await(env, kind):
    env.flow.return_value["data"] = payload(PANELS[kind])
    after = {}

    async def audit(*_args, **_kwargs):
        replacement = copy.deepcopy(env.identity)
        replacement["tianti_progress_current"] = 11
        replacement["concubine_affinity"] = 999
        state_module._meta_state["identity_states"][ID] = replacement
        after.update(copy.deepcopy(state_module._meta_state))

    env.audit.side_effect = audit
    assert asyncio.run(read(kind))["ok"]
    assert state_module._meta_state == after
    env.save.assert_called_once()


@pytest.mark.parametrize("kind", ["tianti", "tianti_generic"])
@pytest.mark.parametrize("field", [
    "\u5f53\u524d\u8fdb\u5ea6", "\u5df2\u5b8c\u6210\u5468\u5929",
    "\u7f61\u98ce\u6dec\u4f53", "\u767b\u9636\u51b7\u5374", "\u95ee\u5fc3\u72b6\u6001",
])
def test_partial_tianti_panel_cannot_certify_cached_fields_or_reset_timers(env, kind, field):
    env.identity.update(
        tianti_enabled=True, tianti_progress_current=10,
        next_tianti_climb_time=NOW + 3600, next_tianti_wenxin_time=NOW + 86400,
        next_tianti_gangfeng_time=NOW + 43200, next_tianti_status_time=NOW + 600,
        tianti_last_status_seen_at=NOW - 3600, tianti_last_error="preserve-error",
    )
    env.flow.return_value["data"] = payload("\n".join(line for line in TIANTI.splitlines() if not line.startswith(field)))
    before = copy.deepcopy(env.identity)
    response = asyncio.run(read(kind))
    assert not response["ok"]
    assert response["extra"]["reason"] == "incomplete_panel"
    assert env.identity == before
    env.save.assert_not_called()


@pytest.mark.parametrize("wait", [
    "-1\u5c0f\u65f6", "0\u79d2", "1.5\u5c0f\u65f6", "1\u5c0f\u65f6 2\u5c0f\u65f6",
    "1\u5206\u949f 1\u5c0f\u65f6", "\u53ef\u7528\uff1b2\u5c0f\u65f6",
    "\u6682\u4e0d\u53ef\u7528", "\u51b7\u5374\u672a\u77e5", "1\u5c0f\u65f6\u62162\u5c0f\u65f6",
])
def test_ambiguous_tianti_countdown_keeps_previous_schedule(env, wait):
    env.identity["next_tianti_climb_time"] = NOW + 3600
    env.flow.return_value["data"] = payload(TIANTI.replace("\u53ef\u7acb\u5373\u767b\u9636", wait))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(read())["ok"]
    assert env.identity == before
    env.save.assert_not_called()


@pytest.mark.parametrize("extra", [
    "\n\u5f53\u524d\u8fdb\u5ea6\uff1a3 / 12 \u9636",
    "\n\u5df2\u5b8c\u6210\u5468\u5929\uff1a1 \u8f6e",
    "\n\u7f61\u98ce\u6dec\u4f53\uff1a2 / 12 \u5c42",
    "\n\u95ee\u5fc3\u72b6\u6001\uff1a\u4eca\u65e5\u5c1a\u672a\u95ee\u5fc3",
    "\n\u3010\u51cc\u9704\u4e91\u9636\u3011",
])
def test_duplicate_tianti_fields_are_rejected_even_when_the_values_agree(env, extra):
    env.flow.return_value["data"] = payload(TIANTI + extra)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(read())["ok"]
    assert env.identity == before


def test_native_recorded_tianti_panel_is_still_supported(env):
    message = get_real_message_text(
        Path(__file__).parent / "fixtures" / "real_message_samples.json", "tianti.status.panel",
    )
    env.flow.return_value["data"] = payload(message)
    response = asyncio.run(read())
    assert response["ok"]
    assert env.identity["tianti_progress_current"] == 10
    assert env.identity["next_tianti_climb_time"] == NOW + 37 * 60 + 43


@pytest.mark.parametrize("cooldown,delay", [
    ("\u53ef\u7528", 0), ("\u53ef\u7acb\u5373\u767b\u9636", 0),
    ("1\u5c0f\u65f62\u5206\u949f3\u79d2", 3723), ("2 \u65f6 3 \u5206", 7380),
    ("\u5269\u4f59\uff1a1\u5206\u949f", 60), ("\u8bf7\u5728 1\u5206\u949f \u540e\u518d\u8bd5\u3002", 60),
])
def test_explicit_tianti_readiness_and_countdown_use_only_reported_values(env, cooldown, delay):
    env.flow.return_value["data"] = payload(TIANTI.replace("\u53ef\u7acb\u5373\u767b\u9636", cooldown))
    assert asyncio.run(read())["ok"]
    assert env.identity["next_tianti_climb_time"] == NOW + delay


def test_locked_gangfeng_does_not_become_ready(env):
    env.identity["next_tianti_gangfeng_time"] = NOW + 43200
    env.flow.return_value["data"] = payload(TIANTI + "\n.\u5f15\u4e5d\u5929\u7f61\u98ce\uff1a\u672a\u89e3\u9501")
    assert asyncio.run(read())["ok"]
    assert env.identity["next_tianti_gangfeng_time"] == NOW + 43200


@pytest.mark.parametrize("kind,clock", [
    ("tianti", "tianti_last_status_seen_at"), ("concubine", "concubine_last_snapshot_at"),
])
def test_direct_status_bridge_rejects_older_panels_without_wrapper(env, kind, clock):
    env.identity[clock] = NOW + 1
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        response = (
            tianti.sync_tianti_miniapp_status(TIANTI, now=NOW) if kind == "tianti"
            else concubine.sync_concubine_miniapp_status(CONCUBINE, NOW)
        )
    assert not response["handled"]
    assert response["reason"] == "stale_observation"
    assert env.identity == before
    env.save.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("cancel", [False, True])
def test_native_session_envelope_preserves_shared_limit(env, kind, cancel):
    rejected = {
        "ok": False, "error": "fixture-limit",
        "result": {
            "ok": False, "status": "failed",
            "events": [{"status_code": 429, "retry_after_sec": 50000, "shared_rate_limit": True}],
        },
    }
    if cancel:
        env.session.side_effect = MiniAppFlowCancelled(rejected)
        with pytest.raises(MiniAppFlowCancelled) as raised:
            asyncio.run(read(kind))
        response = raised.value.result
    else:
        env.session.return_value = rejected
        response = asyncio.run(read(kind))
    assert not response["ok"]
    assert response["extra"]["retry_after_sec"] == 50000
    assert response["extra"]["shared_rate_limit"]
    assert response["extra"]["shared_retry_after_sec"] == 50000
    env.flow.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_local_pending_is_reported_as_busy_not_a_remote_failure(env, kind):
    env.identity["pending_tasks"] = {(-10011, 72): {"cmd": MUTATIONS[kind], "time": NOW}}
    response = asyncio.run(read(kind))
    assert not response["ok"]
    assert response["extra"]["status"] == "busy"
    env.session.assert_not_awaited()
    env.audit.assert_not_awaited()


@pytest.mark.parametrize("key", [
    "concubine_heart_prompt_msg_id", "concubine_heart_msg_id", "concubine_dream_msg_id",
    "concubine_voyage_msg_id", "concubine_gift_msg_id", "concubine_tianji_msg_id",
])
def test_idle_phase_does_not_erase_an_unresolved_concubine_anchor(env, key):
    env.identity[key] = 85
    env.flow.return_value["data"] = payload(CONCUBINE)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(read("concubine"))["ok"]
    assert env.identity == before
    env.session.assert_not_awaited()
    env.save.assert_not_called()


@pytest.mark.parametrize("envelope", ["events", "result", "extra"])
def test_result_metadata_is_cycle_safe_and_keeps_nested_limit(envelope):
    limited = {"shared_rate_limit": True, "retry_after_sec": 50000}
    root = {envelope: [limited], "result": {}}
    root["result"]["result"] = root
    if envelope == "result":
        root["result"]["events"] = [limited]
    extra = cave._miniapp_result_extra({"status": "cancelled"}, root)
    assert extra == {
        "status": "cancelled", "retry_after_sec": 50000, "shared_rate_limit": True,
        "shared_retry_after_sec": 50000,
    }


@pytest.mark.parametrize("value", [False, "false", "true", 1, None])
def test_only_typed_shared_limit_metadata_is_trusted(value):
    extra = cave._miniapp_result_extra({}, {
        "events": [{"shared_rate_limit": value}],
        "data": {"shared_rate_limit": True, "retry_after_sec": 50000},
    })
    assert extra == {}


@pytest.mark.parametrize("kind", KINDS)
def test_read_lock_contention_is_local_busy(env, kind):
    async def run():
        async with cave._public_entry_lock(ID):
            response = await read(kind)
            assert not response["ok"]
            assert response["extra"]["status"] == "busy"
    asyncio.run(run())
    env.session.assert_not_awaited()
    env.audit.assert_not_awaited()
