import asyncio
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import control, persistence
from model import state as state_module
from model.features import explore_rift, tianxing


NOW = 1780000000.0
IDENTITY = 990570001
ACCOUNT = 7551
CHAT = -100570001
BOT = 880570001
ROOT = 5701
RESULT = 5702
ROUTE = "\u63a2\u7d22"
FINAL = "\u3010\u906d\u9047\u98ce\u66b4\u3011\n\u4fee\u4e3a\u5012\u9000\u4e86 300 \u70b9\uff01"
START = "\u4f60\u8fd0\u8f6c\u5168\u8eab\u6cd5\u529b\uff0c\u6495\u5f00\u4e00\u9053\u6f06\u9ed1\u7684\u7a7a\u95f4\u88c2\u7f1d"
PANEL = "\u3010\u5929\u673a\u76d8\u3011\n\u5f53\u524d\u63a8\u547d: \u65e0\n\u5f53\u524d\u6539\u547d: \u65e0"


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_bot_ids([BOT])
    state_module.set_game_group_route_config({"primary_group_id": CHAT})
    state_module.update_send_as_profile(IDENTITY, realm="\u5143\u5a74\u521d\u671f", xiuwei_current=1000)
    identity = state_module.get_identity_state(IDENTITY)
    identity.update(explore_rift_enabled=True, tianxing_enabled=False, next_explore_rift_time=NOW - 1)
    send = AsyncMock(return_value=SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW))
    audit = AsyncMock()
    monkeypatch.setattr(explore_rift, "send_game_command", send)
    monkeypatch.setattr(explore_rift, "send_audit_log", audit)
    monkeypatch.setattr(explore_rift, "save_state", Mock(return_value=True))
    monkeypatch.setattr(explore_rift, "console_log", Mock())
    monkeypatch.setattr(explore_rift, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(explore_rift, "_prepare_explore_rift_tianxing_route", AsyncMock(return_value=True))
    monkeypatch.setattr(explore_rift, "_tianxing_explore_change_ready", lambda now: True)
    monkeypatch.setattr(explore_rift.random, "uniform", lambda *_args: 0)
    monkeypatch.setattr(explore_rift.time, "time", lambda: NOW)
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "unknown.db"))
    with state_module.use_identity(IDENTITY):
        yield SimpleNamespace(identity=identity, send=send, audit=audit, path=tmp_path)
    explore_rift._EXPLORE_RIFT_LOCKS.clear()
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def pending(env, *, result=False):
    env.identity["my_msg_ids"][(CHAT, ROOT)] = NOW - 120
    env.identity["explore_rift_reply_to_msg_id"] = 0 if result else ROOT
    env.identity["explore_rift_pending_result_msg_id"] = RESULT if result else 0
    env.identity["explore_rift_reply_due_at"] = NOW - 1


def unknown(env, *, known=True):
    if known:
        pending(env)
    explore_rift._mark_explore_rift_send_unknown(NOW - 600)


def snapshot():
    return state_module.state.get("tianxing_observation", {}).get("explore_rift_unknown_snapshot")


def reply_context(**updates):
    return {
        "send_as_id": IDENTITY, "chat_id": CHAT, "root_msg_id": ROOT,
        "reply_to_msg_id": ROOT, "msg_id": RESULT, "family": "explore_rift",
        "server_event_at": NOW - 30, "processed_at": NOW, "event_type": "edit", **updates,
    }


def deliver(context=None):
    return explore_rift.handle_explore_rift_reply(
        FINAL, NOW, SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=explore_rift.CMD_EXPLORE_RIFT),
        matched_family="explore_rift", result_msg_id=RESULT, reply_context=context or reply_context(),
    )


def write_log(env, entries):
    day = datetime.fromtimestamp(NOW, explore_rift.TZ_LOCAL).date().isoformat()
    (env.path / f"{day}.log").write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


def log_row(text, msg_id, at, *, root=0, sender=IDENTITY, kind="message"):
    return {
        "ts": datetime.fromtimestamp(at, explore_rift.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "server_event_at": at, "event_type": kind, "text": text,
        "message_id": msg_id, "chat_id": CHAT, "sender_id": sender, "reply_to_msg_id": root,
    }


@pytest.mark.parametrize("reload", [False, True])
def test_known_timeout_never_reissues_the_command(env, reload):
    pending(env)
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    if reload:
        assert persistence.save_state()
        assert persistence.load_state()
    for at in (NOW + explore_rift.RETRY_MAX_SEC + 1, NOW + 86400):
        asyncio.run(explore_rift.run_explore_rift_scheduler(at))
    env.send.assert_not_awaited()
    assert state_module.state["explore_rift_reply_to_msg_id"] == ROOT
    assert snapshot()
    assert env.audit.await_count <= 1


@pytest.mark.parametrize("reload", [False, True])
def test_no_id_unknown_is_not_just_a_one_tick_pause(env, reload):
    unknown(env, known=False)
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    if reload:
        assert persistence.save_state()
        assert persistence.load_state()
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW + 86400))
    env.send.assert_not_awaited()
    assert snapshot()


@pytest.mark.parametrize("prediction,change", [(ROUTE, ROUTE), ("", ROUTE), ("", ""), (ROUTE, "")])
def test_cached_panel_does_not_prove_rift_outcome(env, prediction, change):
    unknown(env)
    observed = env.identity["tianxing_observation"]
    observed.update(last_action="\u5929\u673a\u76d8", last_observed_at=NOW,
                    current_prediction=prediction, current_change=change,
                    current_prediction_until=NOW + 3600, current_change_until=NOW + 7200)
    observed["explore_rift_unknown_snapshot"].update(
        prediction=ROUTE, change=ROUTE, panel_msg_id=ROOT + 10, panel_sent_at=NOW - 5,
    )
    before = copy.deepcopy(observed)
    assert explore_rift._reconcile_unknown_rift_from_panel(NOW) not in {"confirmed", "not_sent"}
    assert snapshot()
    assert env.identity["tianxing_observation"] == before


@pytest.mark.parametrize("change", [{"panel_sent_at": "bad"}, {"panel_sent_at": float("nan")}, {"panel_msg_id": []}])
def test_malformed_panel_snapshot_never_crashes_or_releases_unknown(env, change):
    unknown(env)
    env.identity["tianxing_observation"].update(last_action="\u5929\u673a\u76d8", last_observed_at=NOW)
    snapshot().update(change)
    assert explore_rift._reconcile_unknown_rift_from_panel(NOW) not in {"confirmed", "not_sent"}
    assert snapshot()


def test_stale_start_notice_does_not_clear_result_wait(env):
    pending(env, result=True)
    sent_at = NOW - explore_rift.EXPLORE_RIFT_PENDING_RESULT_STALE_SEC - 10
    write_log(env, [
        log_row(explore_rift.CMD_EXPLORE_RIFT, ROOT, sent_at - 1),
        log_row(START, RESULT, sent_at, root=ROOT, sender=BOT),
    ])
    assert asyncio.run(explore_rift._recover_pending_explore_rift_result_from_message_log(NOW)) == "pending"
    assert env.identity["explore_rift_pending_result_msg_id"] == RESULT
    assert not env.identity.get("explore_rift_last_result_key")
    env.send.assert_not_awaited()


def test_waiting_result_log_reads_obey_the_wait_deadline(env, monkeypatch):
    pending(env, result=True)
    reads = Mock(return_value=None)
    monkeypatch.setattr(explore_rift, "_find_logged_explore_rift_result_message", reads)
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW + 1))
    assert reads.call_count == 1


@pytest.mark.parametrize("disabled", [False, True])
def test_owned_late_result_releases_unknown_even_after_module_disable(env, disabled):
    unknown(env)
    env.identity["explore_rift_enabled"] = not disabled
    assert asyncio.run(deliver())
    assert not snapshot()
    assert env.identity["explore_rift_reply_to_msg_id"] == 0
    assert not env.identity["explore_rift_manual_required"]
    assert env.identity["next_explore_rift_time"] == NOW - 30 + explore_rift.EXPLORE_RIFT_CD


@pytest.mark.parametrize("context", [
    {"chat_id": CHAT - 1}, {"root_msg_id": ROOT + 99}, {"send_as_id": IDENTITY + 1},
    {"server_event_at": 0}, {"server_event_at": NOW + 30},
])
def test_unowned_reply_cannot_release_unknown_or_change_cooldown(env, context):
    unknown(env)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(reply_context(**context)))
    assert env.identity == before


def test_tianxing_sees_no_id_unknown_as_an_unresolved_downstream(env):
    unknown(env, known=False)
    assert tianxing._tianxing_route_has_pending_downstream(ROUTE)


def test_tianxing_keeps_legacy_unknown_blocked_before_rift_scheduler_runs(env):
    env.identity["explore_rift_last_result"] = "\u53d1\u9001\u72b6\u6001\u672a\u77e5\u4e14\u672a\u635e\u5230\u53cd\u9988\uff0c\u5df2\u6682\u505c\u672c\u8f6e"
    assert not snapshot()
    assert tianxing._tianxing_route_has_pending_downstream(ROUTE)


def test_unknown_command_capture_uses_only_bounded_log_reads(env, monkeypatch):
    env.identity["explore_rift_reply_to_msg_id"] = ROOT
    write_log(env, [log_row(explore_rift.CMD_EXPLORE_RIFT, ROOT, NOW - 120)])
    read = Mock(wraps=explore_rift._read_log_tail_lines)
    monkeypatch.setattr(explore_rift, "_read_log_tail_lines", read)
    explore_rift._mark_explore_rift_send_unknown(NOW)
    assert snapshot()["command_chat_id"] == CHAT
    assert snapshot()["command_started_at"] == NOW - 120
    assert read.called
    assert all(call.kwargs["max_bytes"] <= 512 * 1024 for call in read.call_args_list)


@pytest.mark.parametrize("kind", ["command", "result", "unknown"])
def test_ui_toggle_and_initial_check_preserve_inflight_evidence(env, kind):
    if kind == "unknown":
        unknown(env)
    else:
        pending(env, result=kind == "result")
    expected = (env.identity["explore_rift_reply_to_msg_id"], env.identity["explore_rift_pending_result_msg_id"], copy.deepcopy(snapshot()))
    control._manual_disable_explore_rift_module_state()
    assert not env.identity["explore_rift_enabled"]
    control._manual_enable_explore_rift_module_state(NOW)
    explore_rift.schedule_explore_rift_initial_check(NOW)
    assert (env.identity["explore_rift_reply_to_msg_id"], env.identity["explore_rift_pending_result_msg_id"], snapshot()) == expected


def test_panel_query_is_sent_only_once(env):
    unknown(env)
    env.identity["tianxing_enabled"] = True
    assert asyncio.run(explore_rift._request_unknown_rift_panel(NOW))
    asyncio.run(explore_rift._request_unknown_rift_panel(NOW + 60))
    assert env.send.await_count == 1


def test_cancelled_panel_query_is_not_repeated_after_reload(env, monkeypatch):
    unknown(env)
    env.identity["tianxing_enabled"] = True
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    env.send.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(explore_rift._request_unknown_rift_panel(NOW))
    assert persistence.load_state()
    assert snapshot()["panel_status"] == "sending"
    env.send.side_effect = None
    asyncio.run(explore_rift._request_unknown_rift_panel(NOW + 60))
    assert env.send.await_count == 1


def test_definitely_unsent_query_retries_only_after_backoff(env, monkeypatch):
    unknown(env)
    env.identity["tianxing_enabled"] = True
    env.send.return_value = None
    monkeypatch.setattr(explore_rift, "classify_game_send_block", lambda *_: {"status": "unsent", "code": "global_disabled"})
    assert not asyncio.run(explore_rift._request_unknown_rift_panel(NOW))
    assert snapshot()["panel_status"] == "unsent"
    assert not asyncio.run(explore_rift._request_unknown_rift_panel(NOW + 1))
    assert not asyncio.run(explore_rift._request_unknown_rift_panel(NOW + explore_rift.RETRY_MAX_SEC))
    assert env.send.await_count == 2
    assert all(call.args == (explore_rift.CMD_TIANXING_PANEL,) for call in env.send.await_args_list)
    assert env.identity["explore_rift_reply_to_msg_id"] == ROOT


def test_query_cannot_send_until_its_pending_snapshot_is_saved(env, monkeypatch):
    unknown(env)
    env.identity["tianxing_enabled"] = True
    monkeypatch.setattr(explore_rift, "save_state", Mock(return_value=False))
    assert not asyncio.run(explore_rift._request_unknown_rift_panel(NOW))
    env.send.assert_not_awaited()
    assert snapshot()["panel_status"] == "unsent"
    assert snapshot()["panel_next_time"] > NOW
    assert env.identity["explore_rift_reply_to_msg_id"] == ROOT


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "new_operation"])
def test_query_and_parent_do_not_mutate_invalidated_work(env, change):
    unknown(env)
    env.identity["tianxing_enabled"] = True
    expected = {}

    async def invalidate(*args, **kwargs):
        assert kwargs["operation_check"]()
        if change in {"removed", "replaced"}:
            state_module.remove_identity(IDENTITY)
            target = IDENTITY if change == "replaced" else IDENTITY + 1
            state_module.set_identity_account(target, ACCOUNT)
            replacement = state_module.get_identity_state(target)
            replacement.update(copy.deepcopy(env.identity))
            expected["state"] = copy.deepcopy(replacement)
            expected["target"] = target
        else:
            if change == "rebound":
                state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
            else:
                snapshot()["op_id"] = "new-operation"
            expected["target"] = IDENTITY
            expected["state"] = copy.deepcopy(env.identity)
        assert not kwargs["operation_check"]()
        return SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW)

    env.send.side_effect = invalidate
    asyncio.run(explore_rift.run_explore_rift_scheduler(NOW))
    assert state_module.get_identity_state(expected["target"]) == expected["state"]
    env.audit.assert_not_awaited()


@pytest.mark.parametrize("change", [{"account_id": ACCOUNT + 1}, {"identity_id": IDENTITY + 1}])
def test_foreign_unknown_snapshot_cannot_query_or_accept_results(env, change):
    unknown(env)
    snapshot().update(change)
    env.identity["tianxing_enabled"] = True
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver())
    assert not asyncio.run(explore_rift._request_unknown_rift_panel(NOW))
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("receipt_source", ["pending", "log"])
def test_unknown_query_adopts_only_its_late_operation_receipt(env, receipt_source):
    unknown(env)
    snapshot().update(panel_status="sending", panel_started_at=NOW - 60, panel_op_id="query-one")
    outgoing = log_row(explore_rift.CMD_TIANXING_PANEL, ROOT + 10, NOW - 50, kind="sent")
    outgoing.update(op_id="query-one", account_id=ACCOUNT, source_module="\u63a2\u5bfb\u88c2\u7f1d")
    if receipt_source == "pending":
        env.identity["pending_tasks"][(CHAT, ROOT + 10)] = {
            "cmd": explore_rift.CMD_TIANXING_PANEL, "op_id": "query-one", "chat_id": CHAT,
            "source_module": "\u63a2\u5bfb\u88c2\u7f1d", "account_id": ACCOUNT,
            "sent_at": NOW - 50, "send_started_at": NOW - 60,
        }
        entries = []
    else:
        entries = [outgoing]
    entries.append(log_row(PANEL, RESULT + 10, NOW - 40, root=ROOT + 10, sender=BOT))
    write_log(env, entries)
    assert explore_rift._recover_unknown_rift_panel_from_message_log(NOW)
    assert snapshot()["panel_msg_id"] == ROOT + 10
    assert snapshot()["panel_evidence"]["complete"]
    assert explore_rift._reconcile_unknown_rift_from_panel(NOW) == "unresolved_hold"
    assert snapshot() and env.identity["explore_rift_reply_to_msg_id"] == ROOT
    before = copy.deepcopy(env.identity)
    assert not explore_rift._recover_unknown_rift_panel_from_message_log(NOW + 1)
    assert not explore_rift._reconcile_unknown_rift_from_panel(NOW + 1)
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("problem", ["op_id", "account_id", "before_query"])
def test_query_does_not_adopt_unowned_or_prequery_receipt(env, problem):
    unknown(env)
    snapshot().update(panel_status="sending", panel_started_at=NOW - 60, panel_op_id="query-one")
    pending_receipt = {
        "cmd": explore_rift.CMD_TIANXING_PANEL, "op_id": "query-one", "chat_id": CHAT,
        "source_module": "\u63a2\u5bfb\u88c2\u7f1d", "account_id": ACCOUNT,
        "sent_at": NOW - 50, "send_started_at": NOW - 60,
    }
    if problem == "op_id":
        pending_receipt["op_id"] = "other-query"
    elif problem == "account_id":
        pending_receipt["account_id"] = ACCOUNT + 1
    else:
        pending_receipt.update(sent_at=NOW - 100, send_started_at=NOW - 120)
    env.identity["pending_tasks"][(CHAT, ROOT + 10)] = pending_receipt
    write_log(env, [log_row(PANEL, RESULT + 10, NOW - 40, root=ROOT + 10, sender=BOT)])
    before = copy.deepcopy(env.identity)
    assert not explore_rift._recover_unknown_rift_panel_from_message_log(NOW)
    assert env.identity == before


def test_stale_start_then_final_edit_closes_unknown_after_reload(env):
    pending(env, result=True)
    start_at = NOW - explore_rift.EXPLORE_RIFT_PENDING_RESULT_STALE_SEC - 10
    entries = [
        log_row(explore_rift.CMD_EXPLORE_RIFT, ROOT, start_at - 1),
        log_row(START, RESULT, start_at, root=ROOT, sender=BOT),
    ]
    write_log(env, entries)
    assert asyncio.run(explore_rift._recover_pending_explore_rift_result_from_message_log(NOW)) == "pending"
    assert persistence.save_state()
    assert persistence.load_state()
    final_at = NOW + explore_rift.RETRY_MAX_SEC
    entries.append(log_row(FINAL, RESULT, final_at, root=ROOT, sender=BOT, kind="edit"))
    write_log(env, entries)
    asyncio.run(explore_rift.run_explore_rift_scheduler(final_at + 1))
    assert not snapshot()
    assert not state_module.state["explore_rift_pending_result_msg_id"]
    assert state_module.state["next_explore_rift_time"] == final_at + explore_rift.EXPLORE_RIFT_CD
    env.send.assert_not_awaited()


def test_no_id_start_adopts_original_anchor_for_later_final(env):
    unknown(env, known=False)
    write_log(env, [log_row(explore_rift.CMD_EXPLORE_RIFT, ROOT, NOW - 120)])
    assert asyncio.run(explore_rift.handle_explore_rift_reply(
        START, NOW, matched_family="explore_rift", result_msg_id=RESULT, reply_context=reply_context(),
    ))
    assert snapshot()["command_msg_id"] == ROOT
    assert snapshot()["command_chat_id"] == CHAT
    write_log(env, [])
    final_at = NOW + 86400
    assert asyncio.run(explore_rift.handle_explore_rift_reply(
        FINAL, final_at, matched_family="explore_rift", result_msg_id=RESULT,
        reply_context=reply_context(server_event_at=final_at, processed_at=final_at),
    ))
    assert not snapshot()
    assert not env.identity["explore_rift_pending_result_msg_id"]


def test_reply_older_than_original_dispatch_cannot_close_unknown(env):
    unknown(env)
    snapshot()["command_started_at"] = NOW - 120
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(reply_context(server_event_at=NOW - 240)))
    assert env.identity == before


@pytest.mark.parametrize("path", ["routed", "log"])
@pytest.mark.parametrize("kind", ["message", "edit"])
@pytest.mark.parametrize("result", ["final", "cooldown"])
def test_native_late_reply_closes_disabled_unknown_once(env, monkeypatch, path, kind, result):
    from model import app, app_message_log, app_runtime, runtime

    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    runtime._finalize_game_command_sent(
        explore_rift.CMD_EXPLORE_RIFT, msg_id=ROOT, sent_at=NOW - 100, send_started_at=NOW - 120,
        send_as_id=IDENTITY, game_group_id=CHAT, topic_id=0, max_retry=0, append_sent_log=False,
    )
    unknown(env)
    env.identity["explore_rift_enabled"] = False
    text = FINAL if result == "final" else "\u7a7a\u95f4\u88c2\u7f1d\u5c1a\u672a\u7a33\u5b9a\uff0c\u8bf7\u7b49\u5f85 1\u5c0f\u65f6\u3002"
    event_at = NOW - (30 if kind == "edit" else 60)
    event = SimpleNamespace(
        id=RESULT, chat_id=CHAT, sender_id=BOT, raw_text=text,
        date=datetime.fromtimestamp(NOW - 60, timezone.utc),
        edit_date=datetime.fromtimestamp(NOW - 30, timezone.utc) if kind == "edit" else None,
        reply_to=SimpleNamespace(reply_to_msg_id=ROOT),
    )
    reply = SimpleNamespace(id=ROOT, raw_text=explore_rift.CMD_EXPLORE_RIFT, chat_id=CHAT)
    ctx = reply_context(event_type=kind, server_event_at=event_at)
    if path == "routed":
        assert asyncio.run(app._handle_routed_reply_event(event, text, NOW, reply, ctx, event_kind=kind))
    else:
        _, entry = app_message_log._build_message_log_payload(event, event_type=kind)
        entry["ts_epoch"] = NOW
        task = env.identity["pending_tasks"][(CHAT, ROOT)]
        assert asyncio.run(app._replay_pending_log_replies(IDENTITY, ROOT, task, [entry], NOW))
    assert not snapshot()
    assert not env.identity["explore_rift_reply_to_msg_id"]
    delay = explore_rift.EXPLORE_RIFT_CD if result == "final" else 3600 + explore_rift.CD_BUFFER_SEC
    assert env.identity["next_explore_rift_time"] == event_at + delay
    assert not env.identity["explore_rift_enabled"]
    completed = copy.deepcopy(env.identity)
    if path == "routed":
        asyncio.run(app._handle_routed_reply_event(event, text, NOW + 1, reply, ctx, event_kind=kind))
    else:
        asyncio.run(app._replay_pending_log_replies(IDENTITY, ROOT, task, [entry], NOW + 1))
    assert env.identity == completed
    assert env.audit.await_count == 1
    env.send.assert_not_awaited()


def test_result_arriving_during_query_cannot_be_overwritten_by_query_receipt(env):
    unknown(env)
    env.identity["tianxing_enabled"] = True

    async def send_and_deliver(*args, **kwargs):
        assert await deliver()
        return SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW)

    env.send.side_effect = send_and_deliver
    asyncio.run(explore_rift._request_unknown_rift_panel(NOW))
    assert not snapshot()
    assert env.identity["explore_rift_last_result"] != "\u53d1\u9001\u72b6\u6001\u672a\u77e5\uff0c\u7b49\u5f85\u5929\u673a\u76d8\u6d88\u8d39\u6821\u51c6"


def test_query_validates_operation_after_await_and_before_transport(env):
    unknown(env)
    env.identity["tianxing_enabled"] = True

    async def disable(*args, **kwargs):
        assert kwargs["operation_check"]()
        env.identity["explore_rift_enabled"] = False
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = disable
    asyncio.run(explore_rift._request_unknown_rift_panel(NOW))
    asyncio.run(explore_rift._request_unknown_rift_panel(NOW + 60))
    assert env.send.await_count == 1
