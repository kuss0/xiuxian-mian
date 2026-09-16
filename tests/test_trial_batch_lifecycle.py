import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import trial_runtime as runtime


NOW = 1_700_000_000.0
IDENTITY = 991230001


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    for key in ("_MANUAL_AUTH_UNTIL", "_BATCH_RUNS", "_BATCH_BY_IDENTITY", "_RUN_LOCKS"):
        monkeypatch.setattr(runtime, key, {})
    monkeypatch.setattr(runtime, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(runtime, "track_background_task", Mock())
    monkeypatch.setattr(runtime, "get_identity_display_name", lambda identity: f"fixture-{identity}")
    monkeypatch.setattr(runtime, "send_audit_log", AsyncMock(return_value=True))


def create_batch(identities=None, *, now=NOW):
    async def create():
        return runtime.start_trial_miniapp_batch_run(identities or [IDENTITY], now=now)

    return asyncio.run(create())


def settled_result(status="settled"):
    return {"ok": True, "status": status, "data": {
        "settled_count": 1, "results": [{"traceGain": 3, "rewards": [{"name": "fixture", "qty": 2}]}],
    }}


@pytest.mark.parametrize("identities,delta", [([IDENTITY], 0), ([IDENTITY + 1], 0), ([IDENTITY], 0.1)])
def test_same_second_batches_cannot_replace_prior_records(identities, delta):
    first_id = create_batch()
    first = runtime._BATCH_RUNS[first_id]
    runtime._record_trial_batch_result(first_id, IDENTITY, settled_result())
    second_id = create_batch(identities, now=NOW + delta)
    assert second_id != first_id
    assert runtime._BATCH_RUNS[first_id] is first
    assert first["results"][IDENTITY] == settled_result()
    assert runtime._BATCH_RUNS[second_id]["results"] == {}


@pytest.mark.parametrize("status", ["settled", "partial", "cancelled", "next_unavailable"])
def test_late_send_failure_cannot_replace_returned_business_result(status):
    batch_id = create_batch()
    result = settled_result(status)
    assert runtime._record_trial_batch_result(batch_id, IDENTITY, result)
    runtime.note_trial_batch_send_result(batch_id, IDENTITY, ok=False, error="fixture late transport failure")
    assert runtime._BATCH_RUNS[batch_id]["results"][IDENTITY] == result
    assert asyncio.run(runtime.finalize_trial_batch_run(batch_id))
    assert "\u5929\u673a\u6b8b\u75d5+3" in runtime.send_audit_log.await_args.args[0]


@pytest.mark.parametrize("writer", ["send", "result"])
@pytest.mark.parametrize("identity,finalized", [(IDENTITY + 1, False), (IDENTITY, True)])
def test_nonmember_or_finalized_batch_rejects_writes(writer, identity, finalized):
    batch_id = create_batch()
    batch = runtime._BATCH_RUNS[batch_id]
    batch["finalized"] = finalized
    before = deepcopy(batch)
    if writer == "send":
        runtime.note_trial_batch_send_result(batch_id, identity, ok=False, error="fixture")
    else:
        assert not runtime._record_trial_batch_result(batch_id, identity, settled_result())
    assert batch == before


def test_recorded_result_does_not_alias_later_caller_mutations():
    batch_id = create_batch()
    result = settled_result()
    assert runtime._record_trial_batch_result(batch_id, IDENTITY, result)
    result["data"]["results"][0]["traceGain"] = 99
    result["data"]["results"][0]["rewards"][0]["qty"] = 99
    assert runtime._BATCH_RUNS[batch_id]["results"][IDENTITY] == settled_result()


def test_real_result_can_supersede_an_earlier_send_failure():
    batch_id = create_batch()
    runtime.note_trial_batch_send_result(batch_id, IDENTITY, ok=False, error="fixture unknown send")
    assert runtime._BATCH_RUNS[batch_id]["results"][IDENTITY]["status"] == "send_failed"
    assert runtime._record_trial_batch_result(batch_id, IDENTITY, settled_result())
    assert runtime._BATCH_RUNS[batch_id]["results"][IDENTITY] == settled_result()


@pytest.mark.parametrize("kind", ["missing", "foreign", "finalized"])
def test_invalid_batch_authorization_cannot_replace_existing_authorization(kind):
    expires = runtime.authorize_trial_miniapp_manual_run(IDENTITY, ttl_sec=120)
    if kind == "missing":
        batch_id = "missing-fixture"
    else:
        batch_id = create_batch([IDENTITY + 1] if kind == "foreign" else [IDENTITY])
        runtime._BATCH_RUNS[batch_id]["finalized"] = kind == "finalized"
    assert runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=batch_id) == 0
    assert runtime._MANUAL_AUTH_UNTIL[IDENTITY] == expires
    assert IDENTITY not in runtime._BATCH_BY_IDENTITY


def test_standalone_authorization_does_not_inherit_prior_batch():
    batch_id = create_batch()
    runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=batch_id)
    expires = runtime.authorize_trial_miniapp_manual_run(IDENTITY, ttl_sec=120)
    assert expires == NOW + 120
    assert IDENTITY not in runtime._BATCH_BY_IDENTITY
    assert batch_id in runtime._BATCH_RUNS


def test_expired_authorization_clears_its_stale_batch_link():
    batch_id = create_batch()
    runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=batch_id, ttl_sec=60)
    assert not runtime._has_manual_auth(IDENTITY, NOW + 61)
    assert IDENTITY not in runtime._MANUAL_AUTH_UNTIL
    assert IDENTITY not in runtime._BATCH_BY_IDENTITY
    assert batch_id in runtime._BATCH_RUNS


@pytest.mark.parametrize("kind", ["removed", "finalized", "foreign"])
def test_stale_batch_link_cannot_authorize_a_new_entry(kind):
    batch_id = create_batch()
    runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=batch_id)
    if kind == "removed":
        runtime._BATCH_RUNS.pop(batch_id)
    elif kind == "finalized":
        runtime._BATCH_RUNS[batch_id]["finalized"] = True
    else:
        runtime._BATCH_RUNS[batch_id]["identity_ids"] = [IDENTITY + 1]
    assert not runtime._has_manual_auth(IDENTITY, NOW + 1)
    assert IDENTITY not in runtime._MANUAL_AUTH_UNTIL
    assert IDENTITY not in runtime._BATCH_BY_IDENTITY


def test_old_batch_revocation_cannot_clear_new_batch_authorization():
    old_id = create_batch()
    new_id = create_batch(now=NOW + 2)
    expires = runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=new_id)
    assert not runtime.revoke_trial_miniapp_manual_run(IDENTITY, batch_id=old_id)
    assert runtime._MANUAL_AUTH_UNTIL[IDENTITY] == expires
    assert runtime._BATCH_BY_IDENTITY[IDENTITY] == new_id
    assert runtime.revoke_trial_miniapp_manual_run(IDENTITY, batch_id=new_id)
    assert IDENTITY not in runtime._MANUAL_AUTH_UNTIL
    assert IDENTITY not in runtime._BATCH_BY_IDENTITY


def test_finalization_revokes_unconsumed_batch_authorization():
    batch_id = create_batch()
    runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=batch_id, ttl_sec=7200)
    runtime._record_trial_batch_result(batch_id, IDENTITY, {"ok": False, "status": "send_failed", "data": {}})
    assert asyncio.run(runtime.finalize_trial_batch_run(batch_id, reason="timeout"))
    assert not runtime._has_manual_auth(IDENTITY, NOW + 1)
    assert IDENTITY not in runtime._MANUAL_AUTH_UNTIL
    assert IDENTITY not in runtime._BATCH_BY_IDENTITY


@pytest.mark.parametrize("delivery", [False, None, "true", "exception"])
def test_failed_report_keeps_batch_and_its_authorization(delivery):
    batch_id = create_batch()
    batch = runtime._BATCH_RUNS[batch_id]
    expires = runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=batch_id)
    runtime._record_trial_batch_result(batch_id, IDENTITY, settled_result())
    if delivery == "exception":
        runtime.send_audit_log.side_effect = OSError("fixture unavailable")
    else:
        runtime.send_audit_log.return_value = delivery
    assert not asyncio.run(runtime.finalize_trial_batch_run(batch_id))
    assert runtime._BATCH_RUNS[batch_id] is batch
    assert batch["results"][IDENTITY] == settled_result()
    assert runtime._MANUAL_AUTH_UNTIL[IDENTITY] == expires
    assert runtime._BATCH_BY_IDENTITY[IDENTITY] == batch_id


@pytest.mark.parametrize("replacement", ["standalone", "batch"])
def test_inflight_old_report_cannot_revoke_new_authorization(replacement):
    old_id = create_batch()
    new_id = create_batch(now=NOW + 2) if replacement == "batch" else ""
    runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=old_id)
    runtime._record_trial_batch_result(old_id, IDENTITY, settled_result())

    async def deliver(*_args, **_kwargs):
        runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=new_id, ttl_sec=120)
        return True

    runtime.send_audit_log.side_effect = deliver
    assert asyncio.run(runtime.finalize_trial_batch_run(old_id))
    assert old_id not in runtime._BATCH_RUNS
    assert runtime._MANUAL_AUTH_UNTIL[IDENTITY] == NOW + 120
    assert runtime._BATCH_BY_IDENTITY.get(IDENTITY, "") == new_id


def test_ui_batch_does_not_dispatch_without_valid_authorization(monkeypatch):
    from model import ui

    send = AsyncMock()
    monkeypatch.setattr(ui, "send_game_command", send)
    monkeypatch.setattr(ui, "send_audit_log", AsyncMock(return_value=True))
    monkeypatch.setattr(ui, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    asyncio.run(ui._run_trial_miniapp_batch("missing-fixture", [IDENTITY]))
    send.assert_not_awaited()
    assert IDENTITY not in runtime._MANUAL_AUTH_UNTIL


def test_ui_late_failed_send_cannot_revoke_new_batch_authorization(monkeypatch):
    from model import ui

    old_id = create_batch()
    new_id = create_batch(now=NOW + 2)

    async def send(*_args, **_kwargs):
        runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=new_id, ttl_sec=120)
        return None

    monkeypatch.setattr(ui, "send_game_command", AsyncMock(side_effect=send))
    monkeypatch.setattr(ui, "send_audit_log", AsyncMock(return_value=True))
    monkeypatch.setattr(ui, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    asyncio.run(ui._run_trial_miniapp_batch(old_id, [IDENTITY]))
    ui.send_game_command.assert_awaited_once()
    assert runtime._MANUAL_AUTH_UNTIL[IDENTITY] == NOW + 120
    assert runtime._BATCH_BY_IDENTITY[IDENTITY] == new_id


@pytest.mark.parametrize("sent", [False, True])
def test_ui_batch_sends_members_only_and_keeps_existing_no_retry_policy(monkeypatch, sent):
    from model import ui

    batch_id = create_batch()
    send = AsyncMock(return_value=SimpleNamespace(id=77) if sent else None)
    monkeypatch.setattr(ui, "send_game_command", send)
    monkeypatch.setattr(ui, "send_audit_log", AsyncMock(return_value=True))
    monkeypatch.setattr(ui, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    asyncio.run(ui._run_trial_miniapp_batch(batch_id, [IDENTITY, IDENTITY + 1]))
    send.assert_awaited_once()
    assert send.await_args.kwargs["send_as_id"] == IDENTITY
    assert send.await_args.kwargs["max_retry"] == 0
    assert IDENTITY + 1 not in runtime._MANUAL_AUTH_UNTIL
    if sent:
        assert runtime._BATCH_RUNS[batch_id]["send"][IDENTITY]["msg_id"] == 77
        assert runtime._has_manual_auth(IDENTITY, NOW + 1)
    else:
        assert batch_id not in runtime._BATCH_RUNS
        assert IDENTITY not in runtime._MANUAL_AUTH_UNTIL


@pytest.mark.parametrize("sent", [False, True])
def test_timeout_report_keeps_missing_result_for_later_owned_receipt(sent):
    batch_id = create_batch()
    batch = runtime._BATCH_RUNS[batch_id]
    runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=batch_id, ttl_sec=7200)
    if sent:
        runtime.note_trial_batch_send_result(batch_id, IDENTITY, ok=True, msg_id=77)
    assert not asyncio.run(runtime.finalize_trial_batch_run(batch_id, reason="timeout"))
    assert runtime._BATCH_RUNS[batch_id] is batch
    assert not batch["finalized"]
    assert IDENTITY not in runtime._MANUAL_AUTH_UNTIL
    assert IDENTITY not in runtime._BATCH_BY_IDENTITY
    assert runtime.authorize_trial_miniapp_manual_run(IDENTITY, batch_id=batch_id) == 0
    assert runtime._record_trial_batch_result(batch_id, IDENTITY, settled_result())
    assert asyncio.run(runtime.finalize_trial_batch_run(batch_id))
    assert batch_id not in runtime._BATCH_RUNS
    assert runtime.send_audit_log.await_count == 2
    assert "\u5929\u673a\u6b8b\u75d5+3" in runtime.send_audit_log.await_args.args[0]


def test_identical_incomplete_report_is_not_resent_by_concurrent_finalizers():
    batch_id = create_batch()

    async def run():
        async def deliver(*_args, **_kwargs):
            await asyncio.sleep(0)
            return True

        runtime.send_audit_log.side_effect = deliver
        first, second = await asyncio.gather(
            runtime.finalize_trial_batch_run(batch_id, reason="timeout"),
            runtime.finalize_trial_batch_run(batch_id, reason="timeout"),
        )
        assert first is False and second is False

    asyncio.run(run())
    runtime.send_audit_log.assert_awaited_once()
    assert batch_id in runtime._BATCH_RUNS


def test_partial_timeout_report_retains_both_early_and_late_materials():
    batch_id = create_batch([IDENTITY, IDENTITY + 1])
    runtime._record_trial_batch_result(batch_id, IDENTITY, settled_result())
    runtime.note_trial_batch_send_result(batch_id, IDENTITY + 1, ok=True, msg_id=77)
    assert not asyncio.run(runtime.finalize_trial_batch_run(batch_id, reason="timeout"))
    assert runtime._record_trial_batch_result(batch_id, IDENTITY + 1, settled_result())
    assert asyncio.run(runtime.finalize_trial_batch_run(batch_id))
    assert "\u5929\u673a\u6b8b\u75d5+6" in runtime.send_audit_log.await_args.args[0]
    assert "fixturex4" in runtime.send_audit_log.await_args.args[0]
