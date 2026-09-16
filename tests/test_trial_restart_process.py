import multiprocessing
import os
from pathlib import Path
import signal

import pytest


def _process(directory, mode, boundary, output):
    import asyncio
    from contextlib import ExitStack
    from copy import deepcopy
    from dataclasses import replace
    import threading
    from unittest.mock import patch

    root = Path(directory)
    os.environ.update({
        "XIUXIAN_TESTING": "1", "XIUXIAN_ALLOW_LIVE_TEST_DB": "0",
        "XIUXIAN_DATA_DIR": str(root), "XIUXIAN_STATE_DIR": str(root / "state"),
        "XIUXIAN_SESSION_DIR": str(root / "session"), "XIUXIAN_MESSAGES_DIR": str(root / "messages"),
        "XIUXIAN_DB_FILE": str(root / "state" / "trial.db"), "API_ID": "12345", "API_HASH": "0" * 32,
        "ADMIN_ID": "1", "LOG_GROUP_ID": "0", "LOG_SEND_MODE": "account", "TG_PROXY_TYPE": "",
    })
    from model import persistence, state as state_module
    from model.features import trial_operations as operations
    from model.features import trial_miniapp as worker
    from model.features import trial_runtime as runtime
    from model.features.miniapp_common import MiniAppIdentityOwner
    from model.webapp_core import MiniAppRequestPolicy

    identity_id, account_id, now = 991240001, 7124, 1_800_000_000.0
    state_module._meta_state.clear()
    state_module._meta_state.update(deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    if mode == "first":
        state_module.set_identity_account(identity_id, account_id)
        assert persistence.save_state()
    else:
        assert persistence.load_state()
    identity = state_module.get_identity_state(identity_id)
    calls = []

    def pause():
        output.send({"record": deepcopy(identity[operations.STATE_KEY]), "calls": list(calls)})
        threading.Event().wait()

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if boundary == endpoint + "_dispatched":
            pause()
        if endpoint == "next":
            return {"ok": True, "token": "trial_RESTART124_SECOND"}
        if endpoint == "finish":
            return {"ok": True, "result": {"traceGain": 3}}
        return {"ok": True, "challenge": {
            "challengeId": "restart124", "mode": "tianjiMeridianV1", "sequence": ["p"],
            "points": [{"id": "p", "x": 10, "y": 20}], "minDurationMs": 20, "maxDurationMs": 1000,
        }}

    async def scenario():
        if mode != "first":
            original = deepcopy(identity[operations.STATE_KEY])
            assert operations.valid_record(original)
            first = operations.recover_local(identity_id)
            after = deepcopy(identity[operations.STATE_KEY])
            second = operations.recover_local(identity_id)
            output.send({"original": original, "first": first, "second": second,
                         "record": after, "pending": operations.pending(identity),
                         "authorized": runtime.authorize_trial_miniapp_manual_run(identity_id), "calls": calls})
            return
        writer = operations.CheckpointWriter(MiniAppIdentityOwner.capture(identity_id), player_id=identity_id)

        def checkpoint(record):
            saved = writer(record)
            action = (record["pending"] or {}).get("action")
            if saved and (
                boundary == "intent" and record["sequence"] == 1
                or boundary == "challenge" and record["phase"] == "response" and action == "challenge"
                or boundary == "settled" and record["phase"] == "settled"
                or boundary == "next_received" and record["phase"] == "response" and action == "entry"
            ):
                pause()
            return saved

        await worker.run_trial_miniapp_production_flow(
            identity_id, token="trial_RESTART124_FIRST", webview_url="", init_data="fixture-init",
            player_id=identity_id, max_rounds=2, transport=transport, sleeper=lambda _delay: None,
            checkpoint=checkpoint,
            adapter=replace(worker.build_trial_miniapp_adapter(), request_policy=MiniAppRequestPolicy(min_interval_sec=0)),
        )
        raise AssertionError("interruption boundary not reached")

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(persistence, "_try_write_live_guard_backup", lambda *_args, **_kwargs: False))
            stack.enter_context(patch.object(operations.time, "time", lambda: now if mode == "first" else now + 100))
            asyncio.run(scenario())
    except BaseException as exc:
        output.send({"error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        output.close()
        if persistence._db_conn is not None:
            persistence._db_conn.close()


@pytest.mark.parametrize("boundary,action,count,unknown", [
    ("intent", "start", 0, True), ("start_dispatched", "start", 0, True),
    ("challenge", "challenge", 0, False), ("finish_dispatched", "finish", 0, True),
    ("settled", "", 1, False), ("next_dispatched", "next", 1, True),
    ("next_received", "entry", 1, False),
])
def test_process_kill_retains_exact_trial_stage_and_never_replays_http(tmp_path, boundary, action, count, unknown):
    context = multiprocessing.get_context("spawn")

    def launch(mode):
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_process, args=(str(tmp_path), mode, boundary, send))
        process.start()
        send.close()
        return process, receive

    process, pipe = launch("first")
    try:
        assert pipe.poll(20), "first process did not reach its checkpoint"
        interrupted = pipe.recv()
        assert "error" not in interrupted, interrupted
        os.kill(process.pid, signal.SIGKILL)
        process.join(10)
        assert process.exitcode == -signal.SIGKILL
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        pipe.close()
    process, pipe = launch("reload")
    try:
        assert pipe.poll(20), "reload process did not finish recovery"
        restored = pipe.recv()
        process.join(10)
        assert process.exitcode == 0, restored
        assert "error" not in restored, restored
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        pipe.close()
    assert restored["original"] == interrupted["record"]
    checkpoint = restored["original"]["checkpoint"]
    assert checkpoint["pending"].get("action", "") == action
    assert len(checkpoint["round_receipts"]) == count
    assert checkpoint["outcome_unknown"] is unknown
    assert restored["calls"] == []
    if action:
        assert restored["pending"] and restored["authorized"] == 0
        assert restored["record"] == restored["original"]
        assert restored["first"]["status"] == restored["second"]["status"] == "operation_pending"
    else:
        assert not restored["pending"] and restored["authorized"] > 0
        assert restored["first"]["data"]["settled_count"] == count
        assert restored["second"] is None
