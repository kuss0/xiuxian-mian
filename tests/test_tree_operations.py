import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model import persistence, state as state_module, ui
from model.features import tree_operations as ops, tree_runtime as runtime, tree_miniapp as worker
from model.features.miniapp_common import MiniAppIdentityOwner
import test_tree_caller_lifecycle as callers


h = callers.h
native = callers.native
tree_db = callers.tree_db
IDENTITY, ACCOUNT, NOW = callers.IDENTITY, callers.ACCOUNT, callers.NOW


def journal():
    return state_module.get_identity_state(IDENTITY).get(ops.STATE_KEY, {})


def test_each_actual_mutation_has_its_owned_saved_intent(native):
    original = native.transport
    intents = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint != "start":
            record = deepcopy(journal())
            assert ops.valid_record(record)
            checkpoint = record["checkpoint"]
            assert checkpoint["phase"] == "intent" and checkpoint["pending"]["action"] == endpoint
            assert checkpoint["pending"]["mode"] == request["payload"]["mode"]
            intents.append(record)
        return original(request)

    native.transport = transport
    result = asyncio.run(callers.invoke("direct"))
    assert result["ok"], result
    assert len(intents) == 4 and journal()["published"]
    assert ops._ACTIVE == {}
    assert ops.admission_allowed(MiniAppIdentityOwner.capture(IDENTITY))
    encoded = json.dumps(journal())
    assert "private-run131" not in encoded and "fixture-init" not in encoded and "tree_FIXTURE131" not in encoded


@pytest.mark.parametrize("failure", [False, OSError("fixture disk full")])
def test_failed_intent_save_prevents_game_mutation(native, monkeypatch, failure):
    save = Mock(return_value=False) if failure is False else Mock(side_effect=failure)
    monkeypatch.setattr(ops, "save_state", save)
    result = asyncio.run(callers.invoke("direct"))
    assert not result["ok"]
    assert native.calls == [("start", "")]
    assert journal() == {} and ops._ACTIVE == {}


def test_final_publication_failure_recovers_locally_once(tree_db, monkeypatch):
    original_save = persistence.save_state

    def save():
        return False if journal().get("published") else original_save()

    monkeypatch.setattr(ops, "save_state", save)
    result = asyncio.run(callers.invoke("public"))
    assert not result["ok"]
    assert ops.valid_record(journal()) and not journal()["published"]
    assert not callers.record()
    assert persistence.load_state()
    monkeypatch.setattr(ops, "save_state", original_save)
    assert runtime.recover_tree_miniapp_local(IDENTITY)["status"] == "recovered"
    assert runtime.recover_tree_miniapp_local(IDENTITY) is None
    assert callers.record()["phase"] == "completed"
    assert callers.record()["rewards"]["items"] == {"fixture_material": 2}
    assert journal()["published"]
    assert not asyncio.run(ui._run_tree_miniapp_daily_scheduler(NOW + 1, tree_db.config))["started"]
    assert len(tree_db.calls) == 5


@pytest.mark.parametrize("phase", ["response", "settled", "complete"])
def test_post_dispatch_save_failure_retains_evidence_and_blocks_more_play(native, monkeypatch, phase):
    failing = [False]

    def save():
        if journal().get("checkpoint", {}).get("phase") == phase:
            failing[0] = True
        return not failing[0]

    monkeypatch.setattr(ops, "save_state", save)
    result = asyncio.run(callers.invoke("direct"))
    assert not result["ok"] and journal()["pending_save"]
    before_calls = list(native.calls)
    assert not asyncio.run(callers.invoke("public"))["ok"]
    assert native.calls == before_calls
    monkeypatch.setattr(ops, "save_state", lambda: True)
    assert runtime.recover_tree_miniapp_local(IDENTITY)["status"] == "recovered"
    assert runtime.recover_tree_miniapp_local(IDENTITY) is None
    assert native.calls == before_calls
    if phase == "response":
        assert journal()["checkpoint"]["pending"]["action"] == "allocated"
        assert callers.record()["open_run"]
    else:
        assert callers.record()["rewards"]["items"]["fixture_material"] >= 1


@pytest.mark.parametrize("mutation", ["identity", "account", "record"])
def test_local_recovery_never_overwrites_another_owner_or_newer_record(native, monkeypatch, mutation):
    original = ops.CheckpointWriter.publish
    monkeypatch.setattr(ops.CheckpointWriter, "publish", lambda *_args: False)
    asyncio.run(callers.invoke("direct"))
    monkeypatch.setattr(ops.CheckpointWriter, "publish", original)
    before_journal = deepcopy(journal())
    if mutation == "identity":
        state_module.remove_identity(IDENTITY)
        state_module.set_identity_account(IDENTITY, ACCOUNT)
    elif mutation == "account":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    else:
        state_module.set_miniapp_state_records({f"{IDENTITY}:tree": {"replacement": True}})
    before = deepcopy(state_module.get_miniapp_state_records())
    assert runtime.recover_tree_miniapp_local(IDENTITY) is None
    assert state_module.get_miniapp_state_records() == before
    if mutation != "identity":
        assert journal() == before_journal


def test_active_checkpoint_cannot_be_projected_by_scheduler(native):
    original = native.transport

    def transport(request):
        assert runtime.recover_tree_miniapp_local(IDENTITY) is None
        return original(request)

    native.transport = transport
    assert asyncio.run(callers.invoke("direct"))["ok"]


@pytest.mark.parametrize("bad", [None, [], {"invalid": True}])
def test_malformed_legacy_journal_is_not_treated_as_no_work(native, bad):
    state_module.get_identity_state(IDENTITY)[ops.STATE_KEY] = bad
    assert not asyncio.run(callers.invoke("public"))["ok"]
    assert native.calls == [] and native.loader.await_count == 0


def test_journal_codec_preserves_invalid_markers_and_rejects_oversize():
    for value in (None, [], {"x": float("nan")}, {"x": "x" * (256 * 1024)}):
        encoded = persistence._serialize_db_value(ops.STATE_KEY, value)
        assert persistence._deserialize_db_value(ops.STATE_KEY, encoded) == {"invalid": True}


def test_checkpoint_cannot_clear_unresolved_intent_without_a_receipt(native, monkeypatch):
    captured = []
    original = ops.CheckpointWriter.__call__

    def checkpoint(writer, value):
        result = original(writer, value)
        if result and value["sequence"] == 1:
            captured.append((writer, deepcopy(value)))
            bad = deepcopy(value)
            bad.update(sequence=2, phase="complete", pending={})
            bad["result"].update(ok=True, status="completed", open_run=False, outcome_unknown=False)
            assert not writer(bad)
        return result

    monkeypatch.setattr(ops.CheckpointWriter, "__call__", checkpoint)
    assert asyncio.run(callers.invoke("direct"))["ok"]
    assert len(captured) == 1


@pytest.mark.parametrize("endpoint", ["run_start", "run_submit"])
def test_unknown_transport_retains_exact_pending_and_blocks_manual_reentry(native, endpoint):
    original = native.transport

    def transport(request):
        response = original(request)
        if request["safe_summary"]["endpoint"] == endpoint:
            raise TimeoutError("fixture response lost")
        return response

    native.transport = transport
    result = asyncio.run(callers.invoke("direct"))
    assert result["outcome_unknown"] and not result["ok"]
    assert journal()["checkpoint"]["pending"]["action"] == endpoint
    assert journal()["published"]
    assert not runtime.prepare_tree_miniapp_daily_run(IDENTITY, enabled=True, now=NOW)["ok"]
    assert not asyncio.run(callers.invoke("public"))["ok"]
    assert len(native.calls) == (2 if endpoint == "run_start" else 3)


def test_unsent_budget_failure_resolves_intent_without_unknown_outcome(native, monkeypatch):
    async def flow(*args, **kwargs):
        return await worker.run_tree_miniapp_daily_production_flow(
            *args, **kwargs, transport=native.transport, adapter=callers.adapter(1), sleeper=lambda _delay: None,
        )

    monkeypatch.setattr(runtime, "run_tree_miniapp_daily_production_flow", flow)
    result = asyncio.run(callers.invoke("direct"))
    assert not result["ok"] and not result["outcome_unknown"]
    assert journal()["checkpoint"]["resolution"]["kind"] == "not_sent"
    assert not journal()["checkpoint"]["pending"]
    assert ops.admission_allowed(MiniAppIdentityOwner.capture(IDENTITY))
    assert native.calls == [("start", "")]


def test_manual_single_game_uses_same_durable_publication(native, monkeypatch):
    async def flow(*args, **kwargs):
        return await worker.run_tree_miniapp_game_production_flow(
            *args, **kwargs, transport=native.transport, adapter=callers.adapter(), sleeper=lambda _delay: None,
        )

    monkeypatch.setattr(runtime, "run_tree_miniapp_game_production_flow", flow)
    runtime.authorize_tree_miniapp_manual_run(IDENTITY, now=NOW, mode="jump", submit=True)
    event = SimpleNamespace(id=134, message=SimpleNamespace(buttons=[[
        SimpleNamespace(button=SimpleNamespace(text="tree", url=callers.URL)),
    ]]))

    async def scenario():
        with state_module.use_identity(IDENTITY):
            assert await runtime.handle_tree_miniapp_entry(event, "@tree_fixture", NOW)

    asyncio.run(scenario())
    assert ops.valid_record(journal()) and journal()["published"]
    assert journal()["checkpoint"]["result"]["data"]["submit"]["score"] == 75
    assert callers.record()["submit"]["confirmed"]
    assert callers.record()["rewards"]["items"] == {"fixture_material": 1}
    assert len(native.calls) == 3


def test_held_identity_does_not_starve_next_tree_role(native, monkeypatch):
    state_module.get_identity_state(IDENTITY)[ops.STATE_KEY] = {"invalid": True}
    other = IDENTITY + 1
    state_module.set_identity_account(other, ACCOUNT)
    state_module.update_send_as_profile(other, enabled=True, sect_name="\u843d\u4e91\u5b97")
    config = {**native.config, "tree_daily_enabled_identity_ids": [IDENTITY, other]}
    state_module.set_miniapp_auto_config(config)
    queued = []
    monkeypatch.setattr(ui, "_fire_and_forget", lambda coro: queued.append(coro))
    try:
        result = asyncio.run(ui._run_tree_miniapp_daily_scheduler(NOW, config))
        assert result["started"] and result["identity_id"] == other
    finally:
        for coro in queued:
            coro.close()
    assert native.calls == []
