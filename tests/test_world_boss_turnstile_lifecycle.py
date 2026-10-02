import asyncio
import copy
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model import state
from model.features import world_boss_miniapp_runtime as runtime


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(state, "_meta_state", copy.deepcopy(state.GLOBAL_STATE_DEFAULTS))
    state.ensure_identity_registered(77)
    state.set_identity_account(77, 88)
    state.set_identity_enabled(77, True)
    state.set_global_enabled(True)
    return 77


def test_guard_preserves_channel_http_and_maintenance_exception(identity):
    guard = runtime._WorldBossRuntimeStopGuard(threading.Event(), [identity])
    assert not guard.is_set()
    state.set_identity_enabled(identity, False)
    assert guard.is_set()
    state.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [identity]})
    assert not guard.is_set()
    state.set_global_enabled(False)
    state.set_global_pause_source("tianzun_maintenance")
    assert not guard.is_set()
    state.set_global_pause_source("ui")
    assert guard.is_set()


@pytest.mark.parametrize("change", ["remove", "replace", "remap"])
def test_guard_rejects_stale_identity_owner(identity, change):
    guard = runtime._WorldBossRuntimeStopGuard(threading.Event(), [identity])
    if change == "remap":
        state.set_identity_account(identity, 99)
    else:
        state.remove_identity(identity)
        if change == "replace":
            state.ensure_identity_registered(identity)
            state.set_identity_account(identity, 88)
    assert guard.is_set()


def test_guard_observes_ui_opt_out_and_shared_stop(identity):
    allowed = [True]
    stopped = threading.Event()
    guard = runtime._WorldBossRuntimeStopGuard(stopped, [identity], lambda: allowed[0])
    assert not guard.is_set()
    allowed[0] = False
    assert guard.is_set()
    allowed[0] = True
    stopped.set()
    assert guard.is_set()


@pytest.mark.parametrize("cancel", [False, True])
def test_inflight_browser_wait_cannot_send_after_opt_out_or_cancel(identity, cancel):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    allowed = [True]
    sent = []
    event = SimpleNamespace(buttons=[[SimpleNamespace(
        text="enter", url="https://t.me/hantianzun22_bot?startapp=qyz_test123",
    )]])

    async def init_data_provider(*args):
        return "offline-init"

    def join(**kwargs):
        return SimpleNamespace(
            joined=True, status="joined", session_token="offline-session", account_id=88,
            safe_summary=lambda: {"joined": True, "status": "joined"},
        )

    def battle(receipt, **kwargs):
        entered.set()
        try:
            assert release.wait(3)
            kwargs["transport"]({"url": "https://example.invalid/begin"})
            return {"ok": True, "status": "settled"}
        finally:
            finished.set()

    async def scenario():
        task = asyncio.create_task(runtime.run_world_boss_miniapp_event(
            [identity], event, init_data_provider=init_data_provider,
            transport=lambda request: sent.append(request), operation_check=lambda: allowed[0],
        ))
        try:
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            if cancel:
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done()
            else:
                allowed[0] = False
            release.set()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            else:
                result = await asyncio.wait_for(task, 3)
                assert not result["ok"]
            assert finished.is_set()
            assert not sent
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with (
        patch.object(runtime, "join_world_boss_miniapp_lab", side_effect=join),
        patch.object(runtime, "run_world_boss_joined_battle_lab_flow", side_effect=battle),
    ):
        asyncio.run(scenario())
