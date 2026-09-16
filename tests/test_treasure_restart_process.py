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
    from types import SimpleNamespace
    from unittest.mock import patch

    root = Path(directory)
    os.environ.update({
        "XIUXIAN_TESTING": "1", "XIUXIAN_ALLOW_LIVE_TEST_DB": "0",
        "XIUXIAN_DATA_DIR": str(root), "XIUXIAN_STATE_DIR": str(root / "state"),
        "XIUXIAN_SESSION_DIR": str(root / "session"), "XIUXIAN_MESSAGES_DIR": str(root / "messages"),
        "XIUXIAN_DB_FILE": str(root / "state" / "treasure.db"), "API_ID": "12345", "API_HASH": "0" * 32,
        "ADMIN_ID": "1", "LOG_GROUP_ID": "0", "LOG_SEND_MODE": "account", "TG_PROXY_TYPE": "",
    })
    from model import persistence, state as state_module
    from model.features import cave_treasure_miniapp as worker
    from model.features import cave_treasure_runtime as runtime
    from model.features import treasure_operations as operations
    from model.features import treasure_results as results
    from model.features.miniapp_common import MiniAppIdentityOwner
    from model.webapp_core import MiniAppRequestPolicy

    identity_id, account_id, now = 991280001, 7128, 1_800_000_000.0
    state_module._meta_state.clear()
    state_module._meta_state.update(deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    if mode == "first":
        state_module.set_identity_account(identity_id, account_id)
        assert persistence.save_state()
    else:
        assert persistence.load_state()
    identity, calls = state_module.get_identity_state(identity_id), []
    round_index = 0
    continuation, resuming = boundary.startswith("resume_"), False

    def active_boundary():
        return boundary.removeprefix("resume_") if resuming else boundary

    def pause():
        output.send({"record": deepcopy(identity[operations.STATE_KEY]), "calls": list(calls)})
        threading.Event().wait()

    def panel(used):
        return {"ok": True, "account": {"playerId": identity_id},
                "dwelling": {"hunt": {"used": used, "limit": 2, "remaining": 2 - used, "actionPoints": 1}}}

    def transport(request):
        nonlocal round_index
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if active_boundary() == endpoint + "_dispatched" or (
            boundary == "later_enter_dispatched" and endpoint == "hunt" and round_index == 1
        ):
            pause()
        if continuation and not resuming and round_index == 2 and endpoint == "hunt_reveal":
            raise OSError("fixture continuation interrupted reveal")
        if endpoint == "start":
            if not resuming:
                return panel(0)
            return {**panel(2), "huntRun": {
                "sessionId": "private-session128-2", "status": "active", "size": 1,
                "ap": 0, "maxAp": 1, "foundMain": True, "cells": [{"index": 0, "revealed": True}],
            }}
        if endpoint == "hunt":
            round_index += 1
        if endpoint == "hunt_settle":
            return {**panel(round_index), "huntResult": {
                "sessionId": f"private-session128-{round_index}", "settled": True,
                "loot": [{"name": "fixture_item", "quantity": 1}],
            }}
        revealed = endpoint == "hunt_reveal"
        return {**panel(round_index), "huntRun": {
            "sessionId": f"private-session128-{round_index}", "status": "active", "size": 1,
            "ap": 0 if revealed else 1, "maxAp": 1, "foundMain": revealed,
            "cells": [{"index": 0, "revealed": revealed}],
        }}

    async def scenario():
        nonlocal resuming
        if mode != "first":
            original = deepcopy(identity[operations.STATE_KEY])
            assert operations.valid_record(original)
            first = runtime.recover_cave_treasure_result(identity_id)
            second = runtime.recover_cave_treasure_result(identity_id)
            inventory = {key: deepcopy(value) for key, value in state_module.get_inventory_delta_records().items() if key != "_meta"}
            output.send({"original": original, "first": first, "second": second,
                         "record": deepcopy(identity[operations.STATE_KEY]), "inventory": inventory,
                         "reason": operations.hold_reason(identity_id), "calls": calls})
            return
        projection = results.ResultProjection.capture(MiniAppIdentityOwner.capture(identity_id))
        writer = operations.CheckpointWriter(projection, player_id=identity_id)

        def checkpoint(frame):
            saved = writer(frame)
            pending = frame["pending"].get("action", "")
            state = frame["state"]
            current_boundary = active_boundary()
            if saved and (
                current_boundary == "snapshot" and frame["sequence"] == 1
                or current_boundary == "enter_intent" and frame["phase"] == "intent" and pending == "enter"
                or current_boundary == "search_intent" and frame["phase"] == "intent" and pending == "search"
                or current_boundary == "active" and frame["phase"] == "response" and state.get("in_round") and not state.get("treasure_found")
                or current_boundary == "revealed" and frame["phase"] == "response" and state.get("treasure_found")
                or current_boundary == "settled" and frame["phase"] == "settled"
            ):
                pause()
            return saved

        while True:
            result = await worker.run_cave_treasure_miniapp_production_flow(
                identity_id, token="df_FIXTURE128_SECRET", webview_url="", init_data="fixture-init",
                player_id=identity_id, transport=transport, sleeper=lambda _delay: None, checkpoint=checkpoint,
                operation_check=writer.is_current, resume_record=writer.resume_record,
                adapter=replace(worker.build_cave_treasure_miniapp_adapter(), request_policy=MiniAppRequestPolicy(min_interval_sec=0)),
            )
            assert writer.finish(result), result
            if not continuation or resuming:
                break
            runtime._commit_cave_treasure_result(
                projection, operations.projected_result(writer.current), now=now, operation_record=writer.current,
            )
            assert operations.resume_allowed(identity_id)
            resuming = True
            projection = results.ResultProjection.capture(MiniAppIdentityOwner.capture(identity_id), resume=True)
            writer = operations.CheckpointWriter(projection, player_id=identity_id, resume=True)
        if active_boundary() == "finished":
            pause()
        assert active_boundary() in ("commit_before_db", "commit_after_db")

        def commit_save():
            if active_boundary() == "commit_before_db":
                pause()
            saved = persistence.save_state()
            if saved and active_boundary() == "commit_after_db":
                pause()
            return saved

        with patch.object(results, "save_state", commit_save):
            runtime._commit_cave_treasure_result(
                projection, operations.projected_result(writer.current), now=now, operation_record=writer.current,
            )
        raise AssertionError("interruption boundary not reached")

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(persistence, "_try_write_live_guard_backup", lambda *_args, **_kwargs: False))
            stack.enter_context(patch.object(operations, "time", SimpleNamespace(time=lambda: now if mode == "first" else now + 100)))
            asyncio.run(scenario())
    except BaseException as exc:
        output.send({"error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        output.close()
        if persistence._db_conn is not None:
            persistence._db_conn.close()


@pytest.mark.parametrize("boundary,action,count,active", [
    ("snapshot", "", 0, False), ("enter_intent", "enter", 0, False),
    ("hunt_dispatched", "enter", 0, False), ("active", "", 0, True),
    ("search_intent", "search", 0, True), ("hunt_reveal_dispatched", "search", 0, True),
    ("revealed", "", 0, True), ("hunt_settle_dispatched", "settle", 0, True),
    ("settled", "", 1, False), ("later_enter_dispatched", "enter", 1, False),
    ("finished", "", 2, False), ("commit_before_db", "", 2, False), ("commit_after_db", "", 2, False),
])
def test_forced_stop_reloads_exact_stage_and_accounts_at_most_once_without_http(tmp_path, boundary, action, count, active):
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
        assert process.exitcode == 0, restored
        assert "error" not in restored, restored
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        pipe.close()
    assert restored["original"] == interrupted["record"] == restored["record"]
    checkpoint = restored["record"]["checkpoint"]
    assert checkpoint["pending"].get("action", "") == action
    assert len(checkpoint["receipts"]) == count
    assert checkpoint["state"].get("in_round", False) is active
    assert restored["calls"] == []
    assert "df_FIXTURE128_SECRET" not in str(restored)
    assert "private-session128" not in str(restored)
    inventory = list(restored["inventory"].values())
    assert len(inventory) == (1 if count else 0)
    if count:
        assert inventory[0]["items"] == {"fixture_item": count}
    if action or active:
        assert restored["reason"] == ("outcome_unknown_hold" if action else "original_round_required")
        assert restored["second"]["extra"]["persistence_only"]
    else:
        assert restored["reason"] == "" and restored["second"] is None
    if boundary == "commit_after_db":
        assert restored["first"] is None
    else:
        assert restored["first"]["extra"]["persistence_saved"]
