"""Probe no-message-ID crash safety using isolated processes and a fake RPC."""

import argparse
import json
import multiprocessing
import os
from pathlib import Path
import tempfile


def _worker(state_dir, phase, command_name, output, crash_point, tracked, advance_seconds):
    import asyncio
    from contextlib import ExitStack
    import copy
    import sys
    import threading
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    root = Path(state_dir)
    os.environ.update({
        "XIUXIAN_TESTING": "1",
        "XIUXIAN_ALLOW_LIVE_TEST_DB": "0",
        "XIUXIAN_DATA_DIR": str(root),
        "XIUXIAN_STATE_DIR": str(root / "state"),
        "XIUXIAN_SESSION_DIR": str(root / "session"),
        "XIUXIAN_MESSAGES_DIR": str(root / "messages"),
        "XIUXIAN_DB_FILE": str(root / "state" / "state.db"),
        "CHAOGU_UI_BASE_URL": "http://127.0.0.1:3030",
        "API_ID": "12345",
        "API_HASH": "0" * 32,
        "ADMIN_ID": "1",
        "LOG_GROUP_ID": "0",
        "LOG_SEND_MODE": "account",
        "TG_PROXY_TYPE": "",
    })
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from model import persistence, runtime, state as state_module

    identity_id = 990191
    command = {"checkin": runtime.CMD_CHECKIN, "rift": runtime.CMD_EXPLORE_RIFT}[command_name]
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    if phase == "first":
        state_module.ensure_identity_registered(identity_id)
        state_module.set_identity_account(identity_id, 7101)
        if not persistence.save_state():
            raise RuntimeError("initial isolated save failed")
    else:
        persistence.load_state()
        if not state_module.has_identity(identity_id):
            raise RuntimeError("isolated identity was not reloaded")

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def is_connected(self):
            return True

        async def is_user_authorized(self):
            return True

        async def get_input_entity(self, value):
            return SimpleNamespace(id=value)

        async def __call__(self, _request):
            self.calls += 1
            if phase == "first" and crash_point == "before_id":
                report_boundary(self.calls, 0)
                await asyncio.Future()
            return SimpleNamespace(id=910001)

    def report_boundary(calls, message_id):
        saved = persistence.save_state()
        identity = state_module.get_identity_state(identity_id)
        output.send({
            "phase": phase, "transport_calls": calls, "saved": saved,
            "message_id": message_id, "pending_count": len(identity["pending_tasks"]),
            "guard_attempts": [row.get("attempt", 0) for row in identity["action_guard_sessions"].values()],
        })

    async def run():
        client = FakeClient()
        with ExitStack() as stack:
            if phase == "reload" and advance_seconds:
                real_time = time.time
                stack.enter_context(patch.object(runtime.time, "time", lambda: real_time() + advance_seconds))
            if phase == "first" and crash_point == "after_id":
                finalize = runtime._finalize_game_send_receipt

                def intercept_receipt(*args, **kwargs):
                    message = finalize(*args, **kwargs)
                    report_boundary(client.calls, int(getattr(message, "id", 0) or 0))
                    # Stop between transport registration and the business caller.
                    # Only the parent process can kill this isolated worker.
                    threading.Event().wait()
                    return message

                stack.enter_context(patch.object(runtime, "_finalize_game_send_receipt", intercept_receipt))
            for name, value in (
                ("get_registered_client", client), ("is_account_offline", False),
                ("get_game_group_id", 123456), ("get_game_group_ids", [123456]),
                ("get_game_topic_id", 0), ("get_game_group_topic_id", 0),
                ("get_global_enabled", True), ("_get_send_gap_range", (0.0, 0.0)),
                ("_module_send_gap_min_sec", 0.0), ("is_identity_weak", False),
            ):
                stack.enter_context(patch.object(runtime, name, return_value=value))
            stack.enter_context(patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0))
            stack.enter_context(patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)))
            stack.enter_context(patch.object(runtime, "send_audit_log", new=AsyncMock()))
            stack.enter_context(patch.object(runtime, "_append_sent_message_log"))
            stack.enter_context(patch.object(runtime, "_notify_game_command_sent_observers"))
            message = await runtime.send_game_command(command, send_as_id=identity_id, track=tracked, max_retry=0)
            output.send({
                "phase": phase, "transport_calls": client.calls,
                "message_id": int(getattr(message, "id", 0) or 0),
                "block_code": runtime.get_last_game_send_block(identity_id, command).get("code", ""),
            })

    try:
        asyncio.run(run())
    finally:
        output.close()


def probe(command_name="rift", *, crash_point="before_id", tracked=True, advance_seconds=0):
    if command_name not in ("checkin", "rift") or crash_point not in ("before_id", "after_id"):
        raise ValueError("unsupported isolated probe case")
    if type(tracked) is not bool or type(advance_seconds) not in (int, float) or not 0 <= advance_seconds <= 31 * 86400:
        raise ValueError("invalid isolated probe options")
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="xiuxian-send-crash-") as state_dir:
        observations = []
        for phase in ("first", "reload"):
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(target=_worker, args=(
                state_dir, phase, command_name, sender, crash_point, tracked, advance_seconds,
            ))
            process.start()
            sender.close()
            try:
                if not receiver.poll(20):
                    raise TimeoutError(f"{phase} worker did not reach the probe boundary")
                observations.append(receiver.recv())
                if phase == "first":
                    process.kill()
                process.join(5)
                if process.is_alive():
                    raise TimeoutError(f"{phase} worker did not stop")
                expected_exit = -9 if phase == "first" else 0
                if process.exitcode != expected_exit:
                    raise RuntimeError(f"{phase} worker exited with {process.exitcode}")
            finally:
                if process.is_alive():
                    process.kill()
                    process.join(5)
                receiver.close()
        if not observations[0]["saved"]:
            raise RuntimeError("crash probe did not persist its initial state")
        return {
            "command": command_name, "crash_point": crash_point, "tracked": tracked,
            "advance_seconds": advance_seconds, "observations": observations,
            "safe": observations[1]["transport_calls"] == 0,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", choices=("checkin", "rift"), default="rift")
    parser.add_argument("--crash-point", choices=("before_id", "after_id"), default="before_id")
    parser.add_argument("--untracked", action="store_true")
    parser.add_argument("--advance-seconds", type=float, default=0)
    parser.add_argument("--assert-safe", action="store_true")
    args = parser.parse_args()
    result = probe(
        args.command, crash_point=args.crash_point, tracked=not args.untracked,
        advance_seconds=args.advance_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(1 if args.assert_safe and not result["safe"] else 0)
