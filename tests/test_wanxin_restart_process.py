import multiprocessing
import os
from pathlib import Path
import signal

import pytest


def _worker(directory, phase, action, output):
    import asyncio
    from contextlib import ExitStack
    import copy
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    root = Path(directory)
    os.environ.update({
        "XIUXIAN_TESTING": "1", "XIUXIAN_ALLOW_LIVE_TEST_DB": "0",
        "XIUXIAN_DATA_DIR": str(root), "XIUXIAN_STATE_DIR": str(root / "state"),
        "XIUXIAN_SESSION_DIR": str(root / "session"), "XIUXIAN_MESSAGES_DIR": str(root / "messages"),
        "XIUXIAN_DB_FILE": str(root / "state" / "wanxin.db"),
        "API_ID": "12345", "API_HASH": "0" * 32, "ADMIN_ID": "1",
        "LOG_GROUP_ID": "0", "LOG_SEND_MODE": "account", "TG_PROXY_TYPE": "",
    })
    from model import persistence, state as state_module
    from model.features import wanxin

    identity, account, chat, bot = 990299, 7101, -100990299, 990399
    now = 1_800_000_000.0
    tick = now if phase == "first" else now + 100
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    if phase == "first":
        state_module._meta_state["global_enabled"] = True
        state_module.set_game_group_id(chat)
        state_module.set_game_bot_ids([bot])
        state_module.ensure_identity_registered(identity)
        state_module.set_identity_account(identity, account)
        state_module.update_send_as_profile(identity, username="restart_owner", enabled=True)
        state_module.ensure_identity_registered(identity + 1)
        state_module.set_identity_account(identity + 1, account + 1)
        state_module.update_send_as_profile(
            identity + 1, username="restart_helper", enabled=True, sect_name="\u9634\u7f57\u5b97",
        )
        owner = state_module.get_identity_state(identity)
        owner["wanxin_enabled"] = True
        owner["wanxin_observation"] = wanxin.normalize_wanxin_observation({
            "auto_next_time": now - 1, "next_visit_time": now - 1,
            "next_protect_time": now + 100, "next_deduce_time": now + 3600,
            "assist": {"send_as_id": identity + 1},
            "auto_config": {"publish_enabled": action == "publish", "visit_enabled": action == "visit",
                            "moon_greet_enabled": False, "deduce_enabled": False},
        })
    else:
        assert persistence.load_state()
        assert state_module.has_identity(identity)
        owner = state_module.get_identity_state(identity)
        assert owner["wanxin_observation"]["pending"]["status"] == "sending"
    original = copy.deepcopy(owner["wanxin_observation"]["pending"])
    calls = []

    async def send(command, **options):
        calls.append(command)
        if phase == "first":
            pending = owner["wanxin_observation"]["pending"]
            assert pending["op_id"] == options["op_id"]
            assert pending["status"] == "sending" and pending["msg_id"] == 0
            assert options["operation_check"]()
            assert persistence.save_state()
            output.send({"pending": copy.deepcopy(pending), "calls": list(calls)})
            await asyncio.Future()
        return SimpleNamespace(id=1000, chat_id=chat, sent_at=tick, send_started_at=tick)

    async def run():
        nonlocal tick
        with state_module.use_identity(identity):
            await wanxin.run_wanxin_scheduler(tick)
            if phase == "first":
                raise AssertionError("first worker did not stop inside the fake send")
            held = copy.deepcopy(owner["wanxin_observation"]["unresolved_actions"])
            assert held[action] == dict(original, status="unknown")
            assert owner["wanxin_observation"]["pending"]["action"] == "protect"
            tick = now + 90000
            await wanxin.run_wanxin_scheduler(tick)
            output.send({"pending": original, "held": held, "calls": list(calls),
                         "final_held": copy.deepcopy(owner["wanxin_observation"]["unresolved_actions"])})

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(wanxin, "send_game_command", send))
            stack.enter_context(patch.object(wanxin, "send_audit_log", AsyncMock()))
            stack.enter_context(patch.object(wanxin, "get_phaseful_summary_risk_reason", lambda _now: ""))
            stack.enter_context(patch.object(wanxin, "_iter_message_log_entries_between", lambda *_args: iter(())))
            stack.enter_context(patch.object(wanxin.time, "time", lambda: tick))
            stack.enter_context(patch.object(persistence, "_write_live_guard_backup", lambda *_args, **_kwargs: None))
            asyncio.run(run())
    except BaseException as exc:
        output.send({"error": f"{type(exc).__name__}: {exc}", "phase": phase})
        raise
    finally:
        output.close()
        if persistence._db_conn is not None:
            persistence._db_conn.close()


@pytest.mark.parametrize("action", ["visit", "publish"])
def test_killed_sender_reloads_intent_without_repeating_or_blocking_independent_work(tmp_path, action):
    context = multiprocessing.get_context("spawn")
    results = []
    for phase in ("first", "reload"):
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_worker, args=(str(tmp_path), phase, action, sender))
        process.start()
        sender.close()
        try:
            assert receiver.poll(15), f"{phase} worker did not reach the isolated boundary"
            report = receiver.recv()
            assert "error" not in report, report
            results.append(report)
            if phase == "first":
                process.kill()
            process.join(5)
            assert not process.is_alive()
            assert process.exitcode == (-signal.SIGKILL if phase == "first" else 0)
        finally:
            if process.is_alive():
                process.kill()
                process.join(5)
            receiver.close()
    first, restored = results
    assert restored["pending"] == first["pending"]
    assert len(first["calls"]) == 1
    assert restored["calls"] == [".\u62a4\u6301\u795e\u9b42"]
    assert restored["held"][action] == dict(first["pending"], status="unknown")
    assert restored["final_held"][action] == restored["held"][action]
