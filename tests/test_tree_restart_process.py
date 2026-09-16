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
        "XIUXIAN_TESTING": "1", "XIUXIAN_ALLOW_LIVE_TEST_DB": "0", "XIUXIAN_DATA_DIR": str(root),
        "XIUXIAN_STATE_DIR": str(root / "state"), "XIUXIAN_SESSION_DIR": str(root / "session"),
        "XIUXIAN_MESSAGES_DIR": str(root / "messages"), "XIUXIAN_DB_FILE": str(root / "state" / "tree.db"),
        "API_ID": "12345", "API_HASH": "0" * 32, "ADMIN_ID": "1", "LOG_GROUP_ID": "0",
        "LOG_SEND_MODE": "account", "TG_PROXY_TYPE": "",
    })
    from model import persistence, state as state_module
    from model.features import tree_miniapp as worker, tree_runtime as runtime, tree_operations as ops
    from model.features.miniapp_common import MiniAppIdentityOwner
    from model.webapp_core import MiniAppRequestPolicy

    identity_id = 991340001
    state_module._meta_state.clear()
    state_module._meta_state.update(deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    if mode == "first":
        state_module.set_identity_account(identity_id, 7134)
        assert persistence.save_state()
    else:
        assert persistence.load_state()
    identity = state_module.get_identity_state(identity_id)
    calls, used = [], {"jump": 0, "fly": 0}

    def pause():
        output.send({"record": deepcopy(identity[ops.STATE_KEY]), "calls": list(calls)})
        threading.Event().wait()

    def panel():
        return {"ok": True, "council": {"daily": {
            key: {"used": value, "limit": 1, "remaining": 1 - value} for key, value in used.items()
        }}}

    def transport(request):
        endpoint, game_mode = request["safe_summary"]["endpoint"], request["payload"].get("mode", "")
        calls.append((endpoint, game_mode))
        if boundary == endpoint + "_dispatched":
            pause()
        if endpoint == "start":
            return panel()
        if endpoint == "run_start":
            return {"ok": True, "run": {"mode": game_mode, "seed": "fixture-seed",
                                        "runToken": "private-run-" + game_mode}}
        assert endpoint == "run_submit"
        used[game_mode] += 1
        return {**panel(), "score": 75 if game_mode == "jump" else 17, "rewards": [{"name": "material", "qty": 1}]}

    def proof(game_mode, _run, **_kwargs):
        score = 75 if game_mode == "jump" else 17
        return {"clientScore": score, "durationMs": 1}, {"score": score, "targetScore": score}

    async def scenario():
        owner = MiniAppIdentityOwner.capture(identity_id)
        if mode != "first":
            original = deepcopy(identity[ops.STATE_KEY])
            assert ops.valid_record(original)
            first = runtime.recover_tree_miniapp_local(identity_id)
            second = runtime.recover_tree_miniapp_local(identity_id)
            output.send({"original": original, "first": first, "second": second, "calls": calls,
                         "record": deepcopy(identity[ops.STATE_KEY]), "allowed": ops.admission_allowed(owner),
                         "view": state_module.get_miniapp_state_records().get(f"{identity_id}:tree", {}).get("state", {})})
            return
        writer = ops.CheckpointWriter(owner, {"kind": "daily", "day_key": "2026-09-15"}, operation_check=owner.is_current)

        def checkpoint(record):
            saved = writer(record)
            if saved and (
                boundary == "intent" and record["sequence"] == 1
                or boundary == "allocated" and record["pending"].get("action") == "allocated"
                or boundary == "settled" and record["phase"] == "settled"
                or boundary == "second_intent" and record["phase"] == "intent" and record["pending"].get("mode") == "fly"
                or boundary == "complete" and record["phase"] == "complete"
            ):
                pause()
            return saved

        result = await worker.run_tree_miniapp_daily_production_flow(
            identity_id, token="tree_RESTART134", webview_url="", init_data="fixture-init", transport=transport,
            sleeper=lambda _delay: None, checkpoint_sink=checkpoint,
            adapter=replace(worker.build_tree_miniapp_adapter(), request_policy=MiniAppRequestPolicy(min_interval_sec=0)),
        )
        raise AssertionError(f"interruption not reached: {result.get('status')}")

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(persistence, "_try_write_live_guard_backup", lambda *_a, **_kw: False))
            stack.enter_context(patch.object(worker, "build_tree_game_proof", proof))
            asyncio.run(scenario())
    except BaseException as exc:
        output.send({"error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        output.close()
        if persistence._db_conn is not None:
            persistence._db_conn.close()


@pytest.mark.parametrize("boundary,action,count", [
    ("intent", "run_start", 0), ("run_start_dispatched", "run_start", 0),
    ("allocated", "allocated", 0), ("run_submit_dispatched", "run_submit", 0),
    ("settled", "", 1), ("second_intent", "run_start", 1), ("complete", "", 2),
])
def test_real_process_kill_preserves_tree_intent_and_receipts_without_replay(tmp_path, boundary, action, count):
    context = multiprocessing.get_context("spawn")

    def launch(mode):
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_process, args=(str(tmp_path), mode, boundary, send))
        process.start()
        send.close()
        return process, receive

    process, pipe = launch("first")
    try:
        assert pipe.poll(20), "first process did not reach checkpoint"
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
        assert pipe.poll(20), "reload process did not finish"
        restored = pipe.recv()
        process.join(10)
        assert process.exitcode == 0 and "error" not in restored, restored
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        pipe.close()
    assert restored["original"] == interrupted["record"]
    checkpoint = restored["original"]["checkpoint"]
    assert checkpoint["pending"].get("action", "") == action
    assert len(checkpoint["result"]["data"].get("runs", [])) == count
    assert restored["calls"] == []
    assert restored["first"]["status"] == "recovered" and restored["second"] is None
    assert restored["record"]["published"]
    assert restored["allowed"] is (not action)
    if count:
        assert restored["view"]["rewards"]["items"] == {"material": count}
    assert restored["view"]["completed_today"] is (boundary == "complete")
