import multiprocessing
import os
from pathlib import Path
import signal

import pytest


def _process(directory, mode, boundary, output):
    import asyncio
    import copy
    from dataclasses import replace
    from unittest.mock import AsyncMock, patch
    from contextlib import ExitStack
    import threading

    root = Path(directory)
    os.environ.update({
        "XIUXIAN_TESTING": "1", "XIUXIAN_ALLOW_LIVE_TEST_DB": "0",
        "XIUXIAN_DATA_DIR": str(root), "XIUXIAN_STATE_DIR": str(root / "state"),
        "XIUXIAN_SESSION_DIR": str(root / "session"), "XIUXIAN_MESSAGES_DIR": str(root / "messages"),
        "XIUXIAN_DB_FILE": str(root / "state" / "fishing.db"),
        "API_ID": "12345", "API_HASH": "0" * 32, "ADMIN_ID": "1",
        "LOG_GROUP_ID": "0", "LOG_SEND_MODE": "account", "TG_PROXY_TYPE": "",
    })
    from model import persistence, state as state_module
    from model.features import fishing_runtime as fishing
    from model.features import fishing_miniapp as worker
    from model.features import fishing_operations as operations
    from model.timing import get_day_key
    from model.webapp_core import MiniAppRequestPolicy

    identity_id, account_id, now = 991120001, 7112, 1_800_000_000.0
    tick = now if mode == "first" else now + 100
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    if mode == "first":
        state_module.set_identity_account(identity_id, account_id)
        identity = state_module.get_identity_state(identity_id)
        identity.update(fishing_enabled=True, fishing_daily_day=get_day_key(now), fishing_daily_count=0,
                        fishing_daily_limit=10, fishing_auto_open_fish_enabled=False, next_fishing_time=now - 1)
    else:
        assert persistence.load_state()
        identity = state_module.get_identity_state(identity_id)
    calls = []

    def pause():
        output.send({"record": copy.deepcopy(identity[operations.STATE_KEY]), "calls": list(calls)})
        threading.Event().wait()

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "next":
            if boundary == "next_dispatched":
                pause()
            return {"ok": True, "token": "fish_RESTART112_SECOND"}
        if endpoint == "start":
            return {"ok": True, "session": {"phase": "bite"}, "challenge": {
                "challengeId": "fixture112", "minDurationMs": 20, "maxDurationMs": 70000,
            }}
        if endpoint == "finish":
            return {"ok": True}
        if endpoint == "result":
            return {"ok": True, "ready": True, "result": {"fish": "fixture-fish", "expGain": 4}}
        raise AssertionError("unexpected transport")

    async def run():
        if mode != "first":
            original = copy.deepcopy(identity[operations.STATE_KEY])
            assert original == {} or operations.valid_record(original)
            with state_module.use_identity(identity_id):
                fishing.schedule_fishing_initial_check(tick, persist=True)
                assert identity[operations.STATE_KEY] == original
                await fishing.run_fishing_scheduler(tick)
                after = copy.deepcopy(identity)
                await fishing.run_fishing_scheduler(tick)
            output.send({"original": original, "count": identity["fishing_daily_count"],
                         "count_after_first": after["fishing_daily_count"],
                         "record": identity[operations.STATE_KEY],
                         "inventory": state_module.get_storage_bag_records(), "calls": calls})
            return
        assert persistence.save_state()
        writer = operations.CheckpointWriter(fishing.FishingMiniAppOperation.capture(identity_id))

        def checkpoint(record):
            saved = writer(record)
            if saved and ((boundary == "intent" and record["sequence"] == 1)
                          or (boundary == "settled" and record["phase"] == "settled")
                          or (boundary == "next_ack" and record["phase"] == "response"
                              and record["unresolved_action"] == "next")):
                pause()
            return saved

        result = await worker.run_fishing_miniapp_production_flow(
            identity_id, token="fish_RESTART112_FIRST", webview_url="", init_data="fixture-only-init",
            max_rounds=2, transport=transport, sleeper=lambda _delay: None, checkpoint=checkpoint,
            adapter=replace(worker.build_fishing_miniapp_adapter(), request_policy=MiniAppRequestPolicy(min_interval_sec=0)),
        )
        assert writer.finish(result)
        if boundary == "pre_accounting":
            pause()
        assert boundary == "during_accounting"
        with patch.object(fishing, "save_state", pause), state_module.use_identity(identity_id):
            fishing._apply_fishing_miniapp_result(result, now)
        raise AssertionError("worker did not reach interruption boundary")

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(fishing.time, "time", lambda: tick))
            stack.enter_context(patch.object(fishing, "send_audit_log", AsyncMock()))
            stack.enter_context(patch.object(fishing, "_run_fishing_valuable_drop_reminders", AsyncMock(return_value=False)))
            stack.enter_context(patch.object(fishing, "_run_pending_fishing_transfer", AsyncMock(return_value=False)))
            stack.enter_context(patch.object(fishing, "_send_fishing_daily_completion_summary", AsyncMock(return_value=False)))
            stack.enter_context(patch.object(persistence, "_try_write_live_guard_backup", lambda *_args, **_kwargs: False))
            asyncio.run(run())
    except BaseException as exc:
        output.send({"error": f"{type(exc).__name__}: {exc}", "mode": mode, "boundary": boundary})
        raise
    finally:
        output.close()
        if persistence._db_conn is not None:
            persistence._db_conn.close()


@pytest.mark.parametrize("boundary,count,unknown,known", [
    ("intent", 0, True, True), ("settled", 1, False, False),
    ("next_dispatched", 1, True, False), ("next_ack", 1, True, True),
    ("pre_accounting", 2, False, False), ("during_accounting", 2, False, False),
])
def test_process_death_retains_exact_round_ownership_and_counts_once(tmp_path, boundary, count, unknown, known):
    context = multiprocessing.get_context("spawn")
    reports = []
    for mode in ("first", "reload", "reload"):
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_process, args=(str(tmp_path), mode, boundary, sender))
        process.start()
        sender.close()
        try:
            assert receiver.poll(20), f"{mode}/{boundary} failed to reach boundary"
            report = receiver.recv()
            assert "error" not in report, report
            reports.append(report)
            if mode == "first":
                process.kill()
            process.join(5)
            assert not process.is_alive()
            assert process.exitcode == (-signal.SIGKILL if mode == "first" else 0)
        finally:
            if process.is_alive():
                process.kill()
                process.join(5)
            receiver.close()
    before, after, twice = reports
    assert after["count"] == after["count_after_first"] == twice["count"] == count
    if unknown:
        assert after["record"]["checkpoint"]["outcome_unknown"]
        assert after["record"]["checkpoint"]["unresolved_round_known"] is known
        assert after["record"]["operation_id"] == before["record"]["operation_id"]
    else:
        assert after["record"] == twice["record"] == {}
    assert after["calls"] == twice["calls"] == []
    if count:
        assert after["inventory"]["991120001"]["items"]["fixture-fish"] == count
        assert twice["inventory"] == after["inventory"]
