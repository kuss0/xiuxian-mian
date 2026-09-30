import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from model.features import cave_treasure_runtime as cave
from model.features import fishing_runtime as fishing
from model.features import fishing_dwelling_runtime as native
from model.features.miniapp_common import MiniAppFlowCancelled
from model import ui
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_dwelling_journal import Store, start


fishing_env = lifecycle.fishing_env


def configure(h):
    h.identity["fishing_pond"] = "青溪浅滩"
    h.identity["fishing_bait"] = "凡饵"
    raw = h.session["result"]["data"]["raw"]
    raw["account"]["commandCenter"] = {"entries": [{"key": "fishing", "status": "integrated"}]}
    raw["characterModel"] = {"selectedId": "ngw"}


def test_native_requires_explicit_canary_and_keeps_both_existing_locks(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)

    async def flow(identity_id, **kwargs):
        assert cave._public_entry_lock(identity_id).locked()
        assert fishing._fishing_send_lock(identity_id).locked()
        assert kwargs["player_id"] == h.session["player_id"]
        assert kwargs["site_id"] == "west-shore" and kwargs["model_id"] == "ngw"
        assert kwargs["bait_choice"] == "凡饵"
        assert kwargs["operation_check"]()
        return {"ok": True, "committed": True, "status": "settled", "data": {"catches": {"fish": 2}}}

    worker = AsyncMock(side_effect=flow)
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_canary=True))
    assert result["ok"] and result["extra"]["native"]
    h.flow.assert_not_awaited()
    h.external.assert_not_awaited()
    assert worker.await_count == 1
    assert not cave._public_entry_lock(h.identity_id).locked()


def test_normal_public_action_does_not_enable_native_before_acceptance(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    worker = AsyncMock()
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    worker.assert_not_awaited()
    assert h.flow.await_count == 1


@pytest.mark.parametrize("blocked", ["model", "directory", "disabled", "pond"])
def test_canary_missing_prerequisites_never_falls_back_to_legacy(fishing_env, monkeypatch, blocked):
    h = fishing_env
    configure(h)
    raw = h.session["result"]["data"]["raw"]
    if blocked == "model":
        raw.pop("characterModel")
    elif blocked == "directory":
        raw["account"].pop("commandCenter")
    elif blocked == "disabled":
        h.identity["fishing_enabled"] = False
    else:
        h.identity["fishing_pond"] = "unknown"
    worker = AsyncMock()
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_canary=True))
    assert not result["ok"]
    worker.assert_not_awaited()
    h.flow.assert_not_awaited()
    h.external.assert_not_awaited()


def test_native_unknown_never_calls_legacy_worker_or_marks_daily_done(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    store = Store()
    start(store.open(owner=(h.identity_id, 7106, h.session["player_id"])))
    h.identity[native.STATE_KEY] = deepcopy(store.record)
    worker = AsyncMock(return_value={"ok": False, "status": "operation_pending", "outcome_unknown": True})
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert not result["ok"] and result["extra"]["outcome_unknown"]
    assert worker.await_count == 1
    h.flow.assert_not_awaited()
    h.external.assert_not_awaited()
    h.daily.assert_not_awaited()
    assert h.identity[native.STATE_KEY] == store.record


def test_corrupt_native_record_keeps_legacy_recovery_blocked(fishing_env):
    h = fishing_env
    h.identity[native.STATE_KEY] = {"phase": "accounted"}
    assert native.pending(h.identity)
    result = fishing.recover_fishing_result_pending(h.identity_id)
    assert result["extra"]["status"] == "native_operation_pending"


def test_canary_cancelled_result_does_not_emit_a_second_summary(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    worker = AsyncMock(side_effect=MiniAppFlowCancelled({"ok": True, "committed": True,
                                                       "data": {"catches": {"fish": 1}}}))
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    with pytest.raises(MiniAppFlowCancelled) as caught:
        asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_canary=True))
    assert caught.value.result["extra"]["committed"]
    h.audit.assert_not_awaited()
    h.daily.assert_not_awaited()


def test_manual_canary_alias_is_not_in_scheduled_action_list(fishing_env):
    assert ui._cave_public_entry_runner(fishing_env.identity_id, "fishing_native_canary") is not None
    assert "fishing_native_canary" not in ui._cave_public_actions_from_config()
