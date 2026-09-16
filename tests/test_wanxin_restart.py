import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from model import persistence, state as state_module
from model.features import wanxin
from test_wanxin_lifecycle import CHAT, HELPER, NOW, OWNER, deliver, receipt, schedule, visit_event
from test_wanxin_lifecycle import env as env
from test_wanxin_liveness import persistent_env as persistent_env


class AbandonedSend(BaseException):
    pass


def abandon(env, action="visit"):
    observed = env.owner["wanxin_observation"]
    if action in {"accept", "identify"}:
        observed["commission"].update(id=5, published_at=NOW - 100, owner_username="owner")
    if action == "identify":
        observed["commission"].update(accepted=True, accepted_at=NOW - 50, helper_username="helper")
    actor = HELPER if action in {"accept", "identify"} else OWNER
    command = wanxin.WANXIN_ACTION_COMMANDS[action]
    command += {"publish": " 1", "accept": " 5", "identify": " @owner"}.get(action, "")

    async def stop(_command, **kwargs):
        pending = env.owner["wanxin_observation"]["pending"]
        assert pending["status"] == "sending"
        assert pending["op_id"] == kwargs["op_id"]
        raise AbandonedSend

    env.monkeypatch.setattr(wanxin, "send_game_command", stop)
    with state_module.use_identity(OWNER), pytest.raises(AbandonedSend):
        asyncio.run(wanxin._send_nonfinancial_action(observed, action, command, NOW, send_as_id=actor))
    return copy.deepcopy(env.owner["wanxin_observation"]["pending"])


@pytest.mark.parametrize("action", ["visit", "publish", "accept", "identify", "moon_seal"])
def test_abandoned_send_is_held_while_an_independent_action_can_proceed(env, action):
    original = abandon(env, action)
    observed = env.owner["wanxin_observation"]
    observed["auto_config"].update(
        publish_enabled=action in wanxin.WANXIN_COMMISSION_ACTIONS,
        moon_seal_enabled=action == "moon_seal", moon_join_enabled=False,
    )
    observed.update(moon_awakened=action == "moon_seal", next_visit_time=NOW + 1000, next_protect_time=NOW + 100)
    sender = AsyncMock(return_value=receipt(200, NOW + 100))
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    env.monkeypatch.setattr(wanxin.time, "time", lambda: NOW + 100)
    asyncio.run(schedule(NOW + 100))
    sender.assert_awaited_once()
    assert sender.await_args.args[0] == wanxin.CMD_WANXIN_PROTECT
    observed = env.owner["wanxin_observation"]
    assert observed["pending"]["action"] == "protect"
    assert observed["unresolved_actions"][action] == dict(original, status="unknown")
    assert observed["commission"]["id"] == original["commission_id"]


def test_restored_sending_is_not_changed_by_normalization_alone(env):
    original = abandon(env)
    value = json.loads(json.dumps(env.owner["wanxin_observation"]))
    normalized = wanxin.normalize_wanxin_observation(value)
    assert normalized["pending"] == value["pending"] == original
    assert not normalized["unresolved_actions"]


@pytest.mark.parametrize("pause", ["module", "identity", "global"])
def test_paused_cleanup_can_hold_restored_send_without_enabling_or_sending(env, pause):
    original = abandon(env)
    if pause == "module":
        env.owner["wanxin_enabled"] = False
    elif pause == "identity":
        state_module.update_send_as_profile(OWNER, enabled=False)
    else:
        state_module._meta_state["global_enabled"] = False
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(wanxin.run_wanxin_global_cleanup_scheduler(NOW + 100))
    sender.assert_not_awaited()
    observed = env.owner["wanxin_observation"]
    assert not observed["pending"]
    assert observed["unresolved_actions"]["visit"] == dict(original, status="unknown")
    if pause == "module":
        assert not env.owner["wanxin_enabled"]
    elif pause == "identity":
        assert not state_module.get_identity_enabled(OWNER)
    else:
        assert not state_module.get_global_enabled()


@pytest.mark.parametrize("change", ["missing_account", "wrong_owner", "missing_op", "no_start", "actor_rebind", "owner_rebind"])
def test_unowned_or_malformed_sending_is_not_migrated_to_a_usable_slot(env, change):
    abandon(env, "identify")
    pending = env.owner["wanxin_observation"]["pending"]
    if change == "missing_account":
        pending["account_id"] = 0
    elif change == "wrong_owner":
        pending["owner_id"] = HELPER
    elif change == "missing_op":
        pending["op_id"] = ""
    elif change == "no_start":
        pending["started_at"] = 0
    elif change == "actor_rebind":
        state_module.set_identity_account(HELPER, OWNER)
    else:
        state_module.set_identity_account(OWNER, HELPER)
    original = copy.deepcopy(pending)
    env.owner["wanxin_observation"]["next_protect_time"] = NOW + 100
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule(NOW + 100))
    sender.assert_not_awaited()
    observed = env.owner["wanxin_observation"]
    assert observed["pending"]["op_id"] == original["op_id"]
    assert not observed["unresolved_actions"]


def test_abandoned_send_keeps_shared_pending_and_resolves_only_its_native_result(env):
    original = abandon(env)
    env.owner["pending_tasks"][(CHAT, 100)] = {
        "cmd": wanxin.CMD_WANXIN_VISIT, "family": "wanxin_visit", "account_id": OWNER,
        "op_id": original["op_id"], "chat_id": CHAT, "sent_at": NOW,
    }
    shared = copy.deepcopy(env.owner["pending_tasks"])
    env.owner["wanxin_observation"]["auto_config"].update(protect_enabled=False, deduce_enabled=False)
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    for now in (NOW + 100, NOW + 2000, NOW + 90000):
        env.monkeypatch.setattr(wanxin.time, "time", lambda: now)
        asyncio.run(schedule(now))
    sender.assert_not_awaited()
    observed = env.owner["wanxin_observation"]
    assert observed["unresolved_actions"]["visit"] == dict(original, status="unknown")
    assert env.owner["pending_tasks"] == shared
    assert observed["next_visit_time"] == NOW - 1
    assert asyncio.run(deliver(visit_event(at=NOW + 50), NOW + 90001))
    assert not env.owner["pending_tasks"]
    assert not env.owner["wanxin_observation"]["unresolved_actions"]


def test_sqlite_save_failure_cannot_release_or_dispatch_past_restored_sending(persistent_env):
    env = persistent_env
    original = abandon(env)
    env.owner["wanxin_observation"]["next_protect_time"] = NOW + 100
    assert persistence.save_state()
    before = copy.deepcopy(env.owner["wanxin_observation"])
    sender = AsyncMock(return_value=receipt(200, NOW + 100))
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    env.monkeypatch.setattr(wanxin.time, "time", lambda: NOW + 100)
    conn = persistence.get_db_conn()
    conn.execute(f"CREATE TEMP TRIGGER fail_wanxin_restart BEFORE INSERT ON identity_runtime_state "
                 f"WHEN NEW.send_as_id = {OWNER} BEGIN SELECT RAISE(ABORT, 'wanxin failure'); END")
    try:
        asyncio.run(schedule(NOW + 100))
        sender.assert_not_awaited()
        assert env.owner["wanxin_observation"] == before
    finally:
        conn.execute("DROP TRIGGER fail_wanxin_restart")
    asyncio.run(schedule(NOW + 100))
    sender.assert_awaited_once()
    assert env.owner["wanxin_observation"]["unresolved_actions"]["visit"] == dict(original, status="unknown")


def test_sqlite_reload_preserves_original_sending_and_then_held_operation(persistent_env):
    env = persistent_env
    original = abandon(env)
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.owner = state_module.get_identity_state(OWNER)
    assert env.owner["wanxin_observation"]["pending"] == original
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(wanxin.run_wanxin_global_cleanup_scheduler(NOW + 100))
    sender.assert_not_awaited()
    assert not env.owner["wanxin_observation"]["pending"]
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.owner = state_module.get_identity_state(OWNER)
    assert env.owner["wanxin_observation"]["unresolved_actions"]["visit"] == dict(original, status="unknown")


@pytest.mark.parametrize("outcome", ["sent", "unsent", "unknown", "cancel", "error", "abandon"])
def test_process_marker_is_released_on_every_sender_exit(env, outcome):
    env.monkeypatch.setattr(wanxin, "_WANXIN_INFLIGHT", {})
    if outcome == "unknown":
        env.monkeypatch.setattr(wanxin, "classify_game_send_block", lambda *_args: {"status": "unknown"})

    async def send(_command, **options):
        assert wanxin._WANXIN_INFLIGHT[OWNER, "visit"] == options["op_id"]
        if outcome == "cancel":
            raise asyncio.CancelledError
        if outcome == "error":
            raise RuntimeError("sender failed")
        if outcome == "abandon":
            raise AbandonedSend
        return receipt() if outcome == "sent" else None

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    if outcome in {"cancel", "abandon"}:
        with pytest.raises(asyncio.CancelledError if outcome == "cancel" else AbandonedSend):
            asyncio.run(schedule())
    else:
        asyncio.run(schedule())
    assert not wanxin._WANXIN_INFLIGHT
    pending = env.owner["wanxin_observation"]["pending"]
    if outcome == "unsent":
        assert not pending
    else:
        assert pending["status"] == {"sent": "sent", "abandon": "sending"}.get(outcome, "unknown")


def test_old_sender_finally_does_not_release_a_replacement_marker(env):
    env.monkeypatch.setattr(wanxin, "_WANXIN_INFLIGHT", {})

    async def send(_command, **_options):
        wanxin._WANXIN_INFLIGHT[OWNER, "visit"] = "replacement"
        return receipt()

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    asyncio.run(schedule())
    assert wanxin._WANXIN_INFLIGHT == {(OWNER, "visit"): "replacement"}


def test_live_marker_only_blocks_the_original_owner(env):
    other_id = OWNER + 2
    state_module.ensure_identity_registered(other_id)
    state_module.set_identity_account(other_id, other_id)
    state_module.update_send_as_profile(other_id, username="other_owner", enabled=True)
    other = state_module.get_identity_state(other_id)
    other["wanxin_enabled"] = True

    async def send(_command, **_options):
        original = copy.deepcopy(env.owner["wanxin_observation"])
        other["wanxin_observation"] = copy.deepcopy(original)
        other["wanxin_observation"]["pending"].update(
            owner_id=other_id, owner_account_id=other_id, send_as_id=other_id, account_id=other_id,
        )
        with state_module.use_identity(other_id):
            await wanxin._cleanup_wanxin_pending_only(NOW + 100)
        assert not other["wanxin_observation"]["pending"]
        assert other["wanxin_observation"]["unresolved_actions"]["visit"]["status"] == "unknown"
        assert env.owner["wanxin_observation"] == original
        await wanxin.run_wanxin_global_cleanup_scheduler(NOW + 200)
        assert env.owner["wanxin_observation"]["pending"]["status"] == "sending"
        assert not env.owner["wanxin_observation"]["unresolved_actions"]
        return receipt()

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    asyncio.run(schedule())
    assert not wanxin._WANXIN_INFLIGHT
